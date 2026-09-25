"""Filesystem / environment / truncation policy shared by the core tools.

Three things every hand of the agent needs, in one place so they cannot drift:

**Path containment.** A tool only ever addresses paths inside the *declared
root* (``ToolContext.root``). The root comes from the caller (CLI
``--workspace``, ``SKALDIR_TOOL_WORKSPACE``, else the current directory) — never
from a tool argument — and every candidate path is resolved (symlinks followed)
before it is compared against the root, so ``../``, an absolute path and a
symlink that points outside all end in the same refusal. The read side may also
address the spill directory (see below), because that is where an oversized
result went.

**Environment policy.** A child process gets an explicit allowlist plus the
runtime-home pass-through; everything that looks like a credential is dropped
(the names are reported, the values never are), and ``PYTHONPATH`` is dropped on
purpose — a leaked ``PYTHONPATH`` silently makes a child import another tree
(this repository has been bitten by exactly that).

**Truncation with a spill.** Output above the contract's limit is cut
head+tail with a marker that says how much was removed, and the *full* output is
written to ``<runtime>/tool_output/`` (itself capped), so nothing is silently
lost and nothing floods a context.

**Isolation.** ``terminal`` runs in a private process group (killed as a group on
timeout) by default, and can be escalated to a real filesystem/network sandbox
through ``bubblewrap`` when the binary is present — with the probe result
reported, never assumed.
"""

from __future__ import annotations

import fnmatch
import json
import os
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from ..env import child_env as _child_env
from .contract import TruncationRule

#: Directories that are never written by ``write_file`` / ``patch``: version
#: control and credential stores. This is an *accident guard*, not an isolation
#: boundary — the boundary is the declared root (or bubblewrap).
WRITE_DENY_DIRS: tuple[str, ...] = (".git", ".ssh", ".aws", ".gnupg", ".config/gcloud")

#: File-name globs that are never written (matched against the relative path and
#: the basename).
WRITE_DENY_GLOBS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.p12",
    "*.pfx",
    "*.ppk",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    "credentials",
    "credentials.*",
)

#: Directory names a *search* / *listing* skips by default (bounded traversal).
SKIP_DIRS: tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".idea",
    ".gradle",
    "dist",
    "build",
    ".next",
)

#: Environment names a child process may inherit (allowlist, not denylist).
ENV_ALLOW: tuple[str, ...] = (
    "PATH",
    "HOME",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TZ",
    "TMPDIR",
    "TEMP",
    "TMP",
    "USER",
    "LOGNAME",
    "SHELL",
    "PWD",
    "COLUMNS",
    "LINES",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    # the runtime homes: a child ``skaldir``/``godsvoice`` must talk to the same
    # runtime this tool call belongs to, never a different one by accident
    "SKALDIR_HOME",
    "GODSV_HOME",
    "HERMES_HOME",
)

#: Names matching this are dropped even if they were listed above — a credential
#: never reaches a child just because it was on a list.
ENV_DENY_PATTERN = (
    r"(?i)(key|token|secret|passwd|password|credential|auth|cookie|session"
    r"|bearer|private|_pat$|gh_|_ssh)"
)

#: Dropped explicitly (documented traps, not credentials).
ENV_DROP: tuple[str, ...] = ("PYTHONPATH", "PYTHONSTARTUP", "NODE_OPTIONS")

_ISOLATION_CACHE: dict[str, dict[str, Any]] = {}


# --------------------------------------------------------------------- paths


def resolve_root(value: str | Path | None) -> Path:
    """The sandbox root: explicit value, else the current directory."""
    if value is None or str(value).strip() == "":
        return Path.cwd().resolve()
    return Path(str(value)).expanduser().resolve()


def _inside(child: Path, base: Path) -> bool:
    try:
        child.relative_to(base)
    except ValueError:
        return False
    return True


def candidate_path(
    root: str | Path,
    value: Any,
    *,
    allow_roots: Sequence[str | Path] = (),
) -> tuple[Path | None, str]:
    """Resolve a tool argument to an absolute path inside the root.

    Returns ``(path, "")`` on success or ``(None, reason)`` when the path is
    empty, asks for ``~`` or resolves outside every allowed root. Symlinks are
    followed by the resolution, so one that points outside is refused too.
    """
    text = str(value or "").strip()
    if not text:
        return None, "empty path"
    if text.startswith("~"):
        return None, (
            "a '~' path is refused: the tool only addresses its declared root "
            "(pass a path relative to the workspace instead)"
        )
    root_path = Path(root)
    raw = Path(text)
    resolved = raw if raw.is_absolute() else root_path / raw
    try:
        resolved = resolved.resolve()
    except OSError as exc:  # pragma: no cover — permission/loop edge cases
        return None, f"cannot resolve {text}: {exc}"
    bases = [Path(root_path).resolve(), *[Path(item).resolve() for item in allow_roots]]
    if any(_inside(resolved, base) for base in bases):
        return resolved, ""
    allowed = ", ".join(str(item) for item in bases)
    return None, f"path escapes the declared root: {resolved} is not under {allowed}"


def write_denied(root: str | Path, path: Path) -> str:
    """The deny glob that forbids writing ``path``, or ``""`` when it is allowed.

    Matches any path *component* against WRITE_DENY_DIRS — measured: nested paths
    like ``project/.ssh/id_rsa`` and ``app/.git/config`` were allowed because the
    old check only compared prefixes from the root.
    """
    try:
        relative = path.resolve().relative_to(Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        relative = path.name
    parts = relative.split("/")
    # Check every path component against WRITE_DENY_DIRS (not just root prefixes)
    for index, part in enumerate(parts):
        if part in WRITE_DENY_DIRS:
            return "/".join(parts[: index + 1]) + "/**"
    # Also check multi-component deny dirs like ".config/gcloud"
    for deny_dir in WRITE_DENY_DIRS:
        if "/" in deny_dir and deny_dir in relative:
            return deny_dir + "/**"
    for pattern in WRITE_DENY_GLOBS:
        if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.name, pattern):
            return pattern
    return ""


def relative_label(root: str | Path, path: Path) -> str:
    """``path`` as the caller would address it (relative to the root when inside)."""
    try:
        return path.relative_to(Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def is_probably_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def walk_entries(
    root: Path,
    *,
    depth: int = 1,
    limit: int = 200,
    skip_dirs: Iterable[str] = SKIP_DIRS,
    keep: Callable[[Path], bool] | None = None,
) -> tuple[list[Path], bool]:
    """Bounded, deterministic (sorted) directory walk; returns ``(paths, cut)``.

    ``keep`` is the filter the *caller* would apply afterwards; passing it here makes
    the ``limit`` count only entries that survive it. Without ``keep`` the limit is a
    raw cap on the walk order (which is what made ``list_dir`` spend ``limit: 5`` on
    five hidden children and return nothing). Descending is unaffected: a directory is
    still queued when ``depth`` allows it and its name is not a skip dir, whether or
    not ``keep`` accepted it.
    """
    skip = set(skip_dirs)
    found: list[Path] = []
    cut = False
    queue: list[tuple[Path, int]] = [(root, 0)]
    while queue:
        current, level = queue.pop(0)
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name)
        except OSError:
            continue
        for child in children:
            if keep is None or keep(child):
                found.append(child)
                if len(found) >= limit:
                    return found, True
            if (
                child.is_dir()
                and not child.is_symlink()
                and child.name not in skip
                and level + 1 < depth
            ):
                queue.append((child, level + 1))
    return found, cut


# ------------------------------------------------------------------ children


def child_environment(
    root: str | Path, *, extra: dict[str, str] | None = None
) -> tuple[dict[str, str], dict[str, Any]]:
    """The environment for a child process, plus an auditable report.

    Values of dropped variables are never reported — only their names.
    """
    import re

    deny = re.compile(ENV_DENY_PATTERN)
    kept: dict[str, str] = {}
    dropped_credentials: list[str] = []
    dropped_unlisted: list[str] = []
    allowed = set(ENV_ALLOW)
    for name, value in os.environ.items():
        if name in ENV_DROP:
            continue
        if deny.search(name):
            # a credential-looking name is named as such, whether or not it was
            # on the allowlist — the report must not hide why it was dropped
            dropped_credentials.append(name)
            continue
        if name not in allowed:
            dropped_unlisted.append(name)
            continue
        kept[name] = value
    forced = _child_env({"TOOL_WORKSPACE": str(Path(root))})
    forced.update(dict(extra or {}))
    kept.update(forced)
    report = {
        "policy": "allowlist + credential scrub",
        "allow": sorted(allowed),
        "kept": sorted(name for name in kept if name not in forced),
        "dropped_credentials": sorted(dropped_credentials),
        "dropped_unlisted": sorted(dropped_unlisted)[:40],
        "dropped_by_design": sorted(ENV_DROP),
        "forced": {name: forced[name] for name in sorted(forced)},
    }
    return kept, report


def isolation_report(*, refresh: bool = False) -> dict[str, Any]:
    """Is the bubblewrap sandbox really usable here? Probe once, report honestly."""
    if not refresh and "bwrap" in _ISOLATION_CACHE:
        return dict(_ISOLATION_CACHE["bwrap"])
    binary = shutil.which("bwrap")
    report: dict[str, Any] = {
        "mode": "bubblewrap" if binary else "process",
        "binary": binary or "",
        "available": False,
        "probe": "",
        "error": "" if binary else "bwrap is not installed",
        "filesystem": "root read-only except the workspace, private /tmp",
        "network": "namespaced off in isolated mode (--unshare-net)",
    }
    if binary:
        probe = [binary, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "/bin/true"]
        report["probe"] = " ".join(shlex.quote(item) for item in probe)
        try:
            completed = subprocess.run(  # noqa: S603 — fixed argv, no shell
                probe, capture_output=True, timeout=10, check=False
            )
            report["available"] = completed.returncode == 0
            if completed.returncode != 0:
                report["error"] = completed.stderr.decode("utf-8", "replace").strip()[:300]
        except (OSError, subprocess.SubprocessError) as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
    _ISOLATION_CACHE["bwrap"] = dict(report)
    return report


def bubblewrap_argv(root: Path, cwd: Path, command: str, *, unshare_net: bool = True) -> list[str]:
    """The argv that runs ``command`` inside bubblewrap, rooted at ``root``.

    ``command`` is the caller's own shell line and travels as **one argv element**:
    the shell parses that element as its script, and nothing else is spliced into
    it. The root and the cwd are arguments of *bubblewrap*, not shell text, so they
    are built as their own argv segment — the defect this guards against is the
    opposite shape (a runtime value inserted *into* a script, as the ACT layer once
    did with a window title).
    """
    binary = shutil.which("bwrap") or "bwrap"
    argv = [
        binary,
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--die-with-parent",
        "--bind",
        os.fspath(root),
        os.fspath(root),
    ]
    if unshare_net:
        argv.append("--unshare-net")
    # bubblewrap's own arguments: paths it mounts, never text a shell re-parses
    argv += ["--chdir", os.fspath(cwd)]
    # the command itself, as the single element the shell reads as its script
    argv += ["/bin/sh", "-c", command]
    return argv


def shlex_join(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(item)) for item in argv)


# ---------------------------------------------------------------- truncation


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def spill_path(runtime: str | Path | None, name: str) -> Path | None:
    if runtime is None:
        return None
    directory = Path(runtime) / "tool_output"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return directory / f"{name}-{stamp}-{os.getpid()}.txt"


def truncate_bytes(
    raw: bytes,
    rule: TruncationRule,
    *,
    label: str = "output",
    runtime: str | Path | None = None,
    spill_name: str = "output",
) -> dict[str, Any]:
    """Cut ``raw`` to the rule head+tail, spilling the full text when asked.

    The returned dict carries ``text`` (what the caller may put into its typed
    output) and the accounting: total/kept/removed bytes, the truncation marker
    and the spill path (empty when nothing was spilled).
    """
    total = len(raw)
    result: dict[str, Any] = {
        "label": label,
        "total_bytes": total,
        "returned_bytes": total,
        "truncated": False,
        "removed_bytes": 0,
        "kept_head_bytes": 0,
        "kept_tail_bytes": 0,
        "spill_path": "",
        "spill_bytes": 0,
        "spill_truncated": False,
        "marker": "",
        "text": _decode(raw),
    }
    if total <= rule.max_bytes:
        return result
    head = raw[: rule.head_bytes]
    tail = raw[-rule.tail_bytes :] if rule.tail_bytes else b""
    removed = total - len(head) - len(tail)
    spill = ""
    spill_bytes = 0
    spill_truncated = False
    if rule.spill and runtime is not None:
        target = spill_path(runtime, spill_name)
        if target is not None:
            payload = raw[: rule.spill_max_bytes]
            spill_truncated = len(payload) < total
            target.write_bytes(payload)
            spill = str(target)
            spill_bytes = len(payload)
    marker = (
        f"[TRUNCATED: {label} was {total} bytes; {len(head)} head + {len(tail)} tail "
        f"kept, {removed} removed"
        + (f"; full output: {spill}" if spill else "; full output not spilled")
        + "]"
    )
    result.update(
        {
            "returned_bytes": len(head) + len(tail),
            "truncated": True,
            "removed_bytes": removed,
            "kept_head_bytes": len(head),
            "kept_tail_bytes": len(tail),
            "spill_path": spill,
            "spill_bytes": spill_bytes,
            "spill_truncated": spill_truncated,
            "marker": marker,
            # the marker is always in the returned text: a reader must be able to
            # see that something was cut, and how much, in head-only and
            # head+tail strategies alike
            "text": _decode(head) + f"\n{marker}\n" + _decode(tail),
        }
    )
    return result


def enforce_truncation(
    value: str, rule: TruncationRule, *, label: str = "output", runtime: str | Path | None = None
) -> dict[str, Any]:
    return truncate_bytes(
        str(value).encode("utf-8"), rule, label=label, runtime=runtime, spill_name=label
    )


def truncation_report(rule: TruncationRule) -> dict[str, Any]:
    """The limit side of the rule, for a result payload that was not truncated."""
    return {
        "rule": rule.to_dict(),
    }


def json_preview(value: Any, limit: int = 400) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


__all__ = [
    "ENV_ALLOW",
    "ENV_DENY_PATTERN",
    "ENV_DROP",
    "SKIP_DIRS",
    "WRITE_DENY_DIRS",
    "WRITE_DENY_GLOBS",
    "bubblewrap_argv",
    "candidate_path",
    "child_environment",
    "enforce_truncation",
    "is_probably_binary",
    "isolation_report",
    "relative_label",
    "resolve_root",
    "shlex_join",
    "spill_path",
    "truncate_bytes",
    "walk_entries",
    "write_denied",
]
