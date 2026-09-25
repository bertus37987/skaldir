"""RUNTIME DELEGATION — the model-callable ``spawn_subagents`` tool of ``skaldir run``.

The subagent harness has always existed (``skaldir agent spawn``) and the role ceilings have
always been enforced (``subagent_roles``), but a **run** could not delegate: ``goal_run.turn_tools``
offered the model no way to ask for a child, and ``SubagentRun`` dispatched through an empty
handler map. So a long run could not reach the capability the rest of the runtime is built
around (audit §6b of the final-acceptance wave).

This module is that seam, and nothing else:

* :func:`child_handlers` — the handlers a delegated child's tools **really run**. Reads are the
  loop's own workspace-fenced implementations (:func:`skaldir.agent_loop.builtin_tools`) plus
  ``search_files``/``glob``; writes go through the product's **core toolset**
  (:mod:`skaldir.tools_core` — the typed contracts behind ``skaldir tools-core``: workspace-scoped
  paths, verified writes) and through the atomic transaction engine
  (:mod:`skaldir.edit_tx`, behind ``skaldir edit apply``); the test tool runs the workspace's
  **declared check** through the runtime's one judge (:func:`skaldir.verify_service.
  run_declared_criterion`) in the ``bwrap`` sandbox, fail-closed. A tool a role does not hold
  gets **no** handler at all, and a granted tool with no handler is reported ``declared`` —
  never silently "executed".
* :func:`spawn_family` — one delegation, as a real :class:`skaldir.subagents.SubagentRun` with
  real threads, real prompts and its record on disk.
* :func:`spawn_subagents_tool` — the :class:`skaldir.agent_loop.AgentTool` the run offers the
  model, whose handler is the real thing above.

Three hard limits, stated here because a tool the model drives must not be able to widen them:

* the **role** a delegation may ask for is one of :data:`GRANTABLE_ROLES`. ``main`` is the root's
  own surface and ``unroled`` is the historical, ceiling-free child; asking for either is a named
  refusal and spawns nothing (``delegation.role_not_grantable``);
* **what a role may really do is the role's own table** (:data:`GRANTABLE_ROLES` × its
  :mod:`skaldir.subagent_roles` policy): ``explore`` reads, ``verifier`` reads **and runs the
  declared check** but may not change a project file, ``worker`` reads, writes and runs the
  declared check. The dispatched call is refused by :class:`skaldir.subagent_roles.
  RoleToolDispatcher` first, so a handler outside the role's ceiling is unreachable even if it
  were registered (there is a test that the two tables agree, role by role);
* **the fence is the workspace, for reads and for writes alike**: a read/search/glob handler
  never follows a symlink and re-checks each file through its own descriptor, so no byte of a
  file outside the workspace is returned — not through ``..``, not through an absolute path, not
  through a link planted inside (the reproduced defect this module fixes was ``search_files``
  reading a symlinked outside file that ``read_file`` refused). A read-only child's declared
  check is bound ``read-only`` where the shared gate can build that bind, and the bind it really
  got is **reported** (``workspace_bind`` / ``read_only_enforced``) with the paths the sentinel
  saw change — a tool name in a role table is never the proof of a restriction;
* the family is **bounded**: at most :data:`MAX_DELEGATED_CHILDREN` children and
  ``max_depth = 1``, so a delegated child cannot nest further (its role holds no spawn right
  either — two independent ceilings, both refused by name).

Nothing here invents a result: a child that failed is reported with its own error and failure
class, a provider that refuses is a child outcome, and the run's own directory is the evidence.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent_loop import AgentTool, _relative, _workspace_path, builtin_tools
from .subagent_roles import (
    NAMED_ROLES,
    ROLE_EXPLORE,
    ROLE_VERIFIER,
    ROLE_WORKER,
    TEST_TOOLS,
    WRITE_TOOLS,
    policy_for,
    resolve_role,
    tools_for,
)
from .subagents import SubagentRun, briefs_from_tasks

#: The tool name the model sees. One constant, so a test can assert on the real surface.
SPAWN_TOOL_NAME = "spawn_subagents"

#: The roles a *tool call* may ask for. ``main`` (the root's surface) and ``unroled`` (no ceiling
#: at all) are deliberately absent: a model may narrow its own rights, never widen them. The
#: owner's named research/production roles are grantable too — each carries its own ceiling,
#: so asking for one is a narrowing, never a widening.
GRANTABLE_ROLES: tuple[str, ...] = tuple(
    dict.fromkeys((ROLE_EXPLORE, ROLE_WORKER, ROLE_VERIFIER) + NAMED_ROLES)
)

#: A delegation is a bounded sub-run, not a second runtime: few children, shallow, cheap.
MAX_DELEGATED_CHILDREN = 3
CHILD_MAX_TURNS = 4
CHILD_BUDGET_TOKENS = 12_000
CHILD_TIMEOUT_S = 120.0
DELEGATION_MAX_DEPTH = 1

#: The declared check a child may run, and how long it may take. Clamped, because the model
#: chooses this number and a delegated test run must not be able to hold a child open.
CHILD_TEST_TIMEOUT_S = 120.0
CHILD_TEST_MAX_TIMEOUT_S = 600.0
SUITE_CHECK_ID = "pytest"

#: The workspace-fence class: a search/list/glob a child drives may not hand back one byte of a
#: file that lives outside the workspace, whatever spelling of the path got it there — an
#: absolute path, a ``..`` walk, or a **symlink** (file or directory) planted inside the
#: workspace. The walk below therefore never follows a link, and each file it does open is
#: reopened with ``O_NOFOLLOW`` and re-checked against the fence through its own file
#: descriptor, so a link swapped in between the walk and the read is still refused (TOCTOU).
FENCE_MAX_ENTRIES = 20_000
#: How many refusals/skips a result carries (a tree full of links must not turn into a payload).
FENCE_MAX_SKIPPED = 20


def _subagents() -> Any:
    """The child-provider resolver's module, imported lazily (import cost stays flat)."""
    from . import subagents

    return subagents

#: The read-only guarantee a read-only child's declared check needs, and the names the gate must
#: expose for it. The property belongs to the one place that builds the sandbox argv
#: (:func:`skaldir.gates._run`): ``bwrap`` applies the *last* bind of a path, so only the argv
#: builder can put ``--ro-bind <workspace>`` after its ``--bind <workspace>`` — ``extra_binds``
#: skips the workdir by design and :class:`skaldir.execution.RunPolicy` has no bind knob, so a
#: caller cannot express it from the outside. This module therefore *asks* the gate (see
#: :func:`_gate_read_only_support`) instead of assuming, and reports the answer per run.
GATE_READ_ONLY_PARAM = "read_only_workspace"
GATE_READ_ONLY_WORKDIR_PARAM = "read_only_workdir"
READ_ONLY_BIND = "read-only"
READ_WRITE_BIND = "read-write"

#: The workspace write sentinel: what a read-only child's declared check *really* did to the
#: workspace. Bounded, because a check must not pay an unbounded hashing bill.
SENTINEL_MAX_FILES = 4_000
SENTINEL_MAX_BYTES = 32 * 1024 * 1024
SENTINEL_HASH_MAX_BYTES = 1 << 20

#: The model pays for every character a child hands back, so the answer is quoted, not dumped.
ANSWER_CHARS = 2000
DETAIL_CHARS = 400
#: One tool result as it travels back to the model and into the run's record.
TOOL_RESULT_CHARS = 1200

#: Where delegated runs live under the run's state directory (grouped, never mixed with the
#: runtime's own turn state).
DELEGATION_DIRNAME = "delegated"

#: What a delegation may really do, per role — derived from the role table so a display cannot
#: drift from what the dispatcher enforces.
MODE_READ_ONLY = "read-only"
MODE_READ_TESTS = "read-only+tests"
MODE_WRITE_TESTS = "read-write+tests"

#: The tool names a worker's write handlers answer to, and the names the test handler answers to.
WRITE_HANDLERS: tuple[str, ...] = ("write_file", "write_text", "patch", "edit_apply")
TEST_HANDLERS: tuple[str, ...] = ("run_tests", "test_run")


class DelegationRefused(RuntimeError):
    """A delegation the runtime refuses **before** anything is created.

    Carries a stable machine code (``delegation.<reason>``) so a caller can report the refusal
    instead of a generic error, and so no half-built run directory is ever left behind.
    """

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = str(code)


def role_mode(role: str) -> str:
    """What a role may *really* do through this seam — read from its own policy table.

    A role that writes is ``read-write+tests``, one that may only run the declared check is
    ``read-only+tests``, and everything else is ``read-only``. The legacy ceiling-free child
    (``role=""``) reports ``read-only``: this seam never *widens* a child, it only executes the
    rights the role table already granted.
    """
    policy = policy_for(role, allow_unroled=True)
    if policy is None:
        return MODE_READ_ONLY
    if policy.tools & WRITE_TOOLS:
        return MODE_WRITE_TESTS
    if policy.tools & TEST_TOOLS:
        return MODE_READ_TESTS
    return MODE_READ_ONLY


def _grantable_role(requested: str | None, *, kind: str = "") -> str:
    """The role a delegation really gets — or a named refusal.

    ``""``/``None`` mean "the caller did not say": the canonical role for ``kind`` (``worker``,
    or ``explore``/``verifier``). Anything outside :data:`GRANTABLE_ROLES` is refused by name,
    including the two names that exist in the role table but must never come from a tool call.
    """
    text = str(requested or "").strip().lower()
    try:
        role = resolve_role(text or None, kind=kind)
    except ValueError as exc:
        # An unknown name is the same statement as a wider one: this delegation may not have it.
        raise DelegationRefused(
            f"{exc}; a tool call may ask for {' or '.join(GRANTABLE_ROLES)}",
            code="delegation.role_not_grantable",
        ) from exc
    if role not in GRANTABLE_ROLES:
        raise DelegationRefused(
            (
                f"role {requested or text!r} cannot be granted to a delegated child — a tool "
                f"call may ask for {' or '.join(GRANTABLE_ROLES)}"
            ),
            code="delegation.role_not_grantable",
        )
    return role


def clamp_test_timeout(value: float = 0.0) -> float:
    """The declared check's wall clock: the model may ask for less, never for more."""
    wanted = float(value or 0.0)
    if wanted <= 0:
        return float(CHILD_TEST_TIMEOUT_S)
    return float(min(wanted, CHILD_TEST_MAX_TIMEOUT_S))


def declared_test_argv(
    workspace: str | Path, *, check_id: str = SUITE_CHECK_ID
) -> tuple[list[str], str]:
    """The test command this workspace declares — else the runtime's own suite runner.

    The declaration is read by the product's **one** reader
    (:func:`skaldir.verify_service.declared_command`: ``.godsv/checks.json`` or the check's
    ``SKALDIR_CHECK_*_CMD`` variable), so a child runs what the run's own gates would run.
    With no declaration the same fallback the completion runner uses applies — the project's
    own interpreter running its own suite. Nothing is guessed: an unreadable or empty
    declaration falls back *and says so*, and the returned source names where the command came
    from.
    """
    from .verify_service import declared_command

    base = Path(workspace)
    command, source = declared_command(base, str(check_id))
    if command:
        try:
            import shlex

            argv = [part for part in shlex.split(command) if part]
        except ValueError:  # pragma: no cover - an unbalanced quote in the declaration
            argv = []
        if argv:
            return argv, f"declared: {source}"
    venv_python = base / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else sys.executable
    return (
        [python, "-m", "pytest", "-q"],
        (
            "no declared check in the workspace — the project's own suite runner "
            f"({python} -m pytest -q)"
        ),
    )


def _gate_read_only_support() -> tuple[bool, str]:
    """Can the shared gate run a declared check with the workspace bound **read-only**?

    A read-only child's ceiling is "read the declared check's verdict", and the user's
    requirement for that child is *read-only plus tests*: the workspace must not be writable
    while its check runs. ``bwrap`` applies the *last* bind of a path, so the read-only bind has
    to be built in the argv by the one function that builds it
    (:func:`skaldir.gates._run`, which today always emits ``--bind <workdir>``). Nothing outside
    that function can add it: ``extra_binds`` skips a path equal to the workdir by design, and
    :class:`skaldir.execution.RunPolicy` carries no bind knob.

    So this function **asks the gate** rather than assuming it, and every test result reports the
    answer (``read_only_enforced`` / ``workspace_bind``) — a tool name being allowed is not a
    write restriction. The exact snippet that makes this return ``True`` (three signatures; the
    parameter reaches the argv builder through the judge):

    .. code-block:: python

        # skaldir/gates.py — _run()
        def _run(cmd: list[str], *, workdir: Path, timeout: float,
                 extra_env: dict[str, str] | None = None,
                 extra_binds: list[str] | None = None,
                 allow_network: bool = False, stdin: bytes | None = None,
                 read_only_workdir: bool = False) -> tuple[int, str, bool]:
            ...
            full = [
                bwrap,
                ...
                ("--ro-bind" if read_only_workdir else "--bind"),
                str(workdir), str(workdir),
                "--", *cmd,
            ]

        # skaldir/gates.py — run_declared_check()
        def run_declared_check(argv: list[str], *, workdir: Path, timeout: float,
                               extra_env: dict[str, str] | None = None,
                               read_only_workspace: bool = False) -> tuple[int, str, bool]:
            ...
            code, output, sandboxed = _run(list(argv), workdir=workdir, timeout=timeout,
                                           extra_env=extra_env,
                                           read_only_workdir=read_only_workspace)

        # skaldir/verify_service.py — the one judge, and _run_declared beneath it
        def _run_declared(argv, *, workspace, timeout, label, read_only_workspace=False):
            code, output, sandboxed = run_declared_check(
                ..., read_only_workspace=read_only_workspace
            )

        def run_declared_criterion(criterion, *, workspace, timeout, label="",
                                   read_only_workspace: bool = False):
            status, exit_code, output = _run_declared(argv, workspace=root, timeout=timeout,
                                                      label=...,
                                                      read_only_workspace=read_only_workspace)

    Measured before that snippet lands: the declared check can write the workspace
    (``--bind``), and a workspace bound ``--ro-bind`` still runs ``python -m pytest -q`` to a
    clean exit 0 (pytest degrades its cache to a warning) while a check that writes gets
    ``EROFS`` — so the fix is viable and it is prevention, not reporting.
    """
    import inspect

    from . import gates, verify_service

    wanted = (
        ("skaldir.verify_service.run_declared_criterion",
         verify_service.run_declared_criterion, GATE_READ_ONLY_PARAM),
        ("skaldir.gates.run_declared_check", gates.run_declared_check, GATE_READ_ONLY_PARAM),
        ("skaldir.gates._run", gates._run, GATE_READ_ONLY_WORKDIR_PARAM),
    )
    missing: list[str] = []
    for name, target, parameter in wanted:
        try:
            parameters = inspect.signature(target).parameters
        except (TypeError, ValueError):  # pragma: no cover - a builtin/C-level callable
            missing.append(f"{name} (not introspectable)")
            continue
        if parameter not in parameters:
            missing.append(f"{name} (no {parameter}=)")
    if missing:
        return (
            False,
            "the shared gate binds the workspace read-write: "
            + ", ".join(missing)
            + " — a declared check run through it can change the workspace",
        )
    return (
        True,
        "the shared gate can bind the workspace read-only (--ro-bind) — a read-only child's "
        "declared check cannot change the workspace",
    )


def _fd_real_path(fd: int) -> Path | None:
    """The real path a descriptor holds — ``None`` when the platform cannot say.

    Linux exposes it through ``/proc/self/fd``; the read is re-checked against the fence through
    *this* path, not through the name the walk saw, so a symlink swapped in after the walk is
    refused too. On a platform without ``/proc`` the walk's own fence (no link followed, parent
    resolved) is what stands, and the caller's ``O_NOFOLLOW`` still refuses a swapped link.
    """
    try:
        raw = os.readlink(f"/proc/self/fd/{int(fd)}")
    except (OSError, ValueError):
        return None
    if raw.endswith(" (deleted)"):  # pragma: no cover - a file unlinked between open and stat
        raw = raw[: -len(" (deleted)")]
    return Path(raw)


def _fenced_relative(path: Path, root: Path) -> str | None:
    """``path`` as a workspace-relative posix string, or ``None`` when it is not inside.

    Never returns an absolute path from outside the workspace: the loop's own ``_relative``
    falls back to ``str(path)``, which would hand a caller the name (and therefore the
    existence) of an outside file. This one refuses instead.
    """
    try:
        resolved_root = root.resolve()
        parent = path.parent.resolve()
    except OSError:
        return None
    if parent != resolved_root and resolved_root not in parent.parents:
        return None
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def fenced_files(
    start: Path, root: Path, *, max_entries: int = FENCE_MAX_ENTRIES
) -> tuple[list[Path], list[dict[str, str]], bool]:
    """Every **regular file** under ``start``, walking inside the workspace only.

    ``(files, skipped, truncated)``. A symlink — to a file or to a directory — is never read and
    never walked: it is the one entry whose content can live outside the fence. That is the
    reproduced defect this function closes: with ``target.rglob("*")`` a child's ``search_files``
    read ``ws/filelink.txt`` (a link to ``outside/secret.txt``) and returned the outside file's
    matching line, even though ``read_file`` on the same name was refused. The walk is also
    deterministic (sorted) and bounded, so a hostile tree cannot hang a child.
    """
    files: list[Path] = []
    skipped: list[dict[str, str]] = []
    truncated = False
    pending = [start]
    seen_entries = 0
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as listing:
                entries = sorted(listing, key=lambda item: item.name)
        except OSError:
            continue
        for entry in entries:
            seen_entries += 1
            if seen_entries > max_entries:
                truncated = True
                break
            path = Path(entry.path)
            rel = _fenced_relative(path, root)
            if rel is None:  # pragma: no cover - the walk never leaves the fence
                skipped.append({"path": entry.name, "reason": "leaves the workspace"})
                continue
            try:
                if entry.is_symlink():
                    skipped.append({"path": rel, "reason": "symlink (not followed)"})
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            files.append(path)
        if truncated:
            break
    files.sort(key=lambda item: item.as_posix())
    return files, skipped, truncated


def fenced_read(path: Path, root: Path, *, max_chars: int = 0) -> tuple[str | None, str]:
    """Read one file **through the fence**, or refuse. ``(text, "")`` / ``(None, why)``.

    The descriptor is opened with ``O_NOFOLLOW`` (a link cannot be swapped in for the last
    component) and the descriptor's *own* real path is re-checked against the workspace before a
    single byte is decoded — the walk's check and this one are the same fence at two moments,
    which is what closes the window between them.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        return None, f"could not open it: {exc}"
    real = _fd_real_path(fd)  # never raises: it answers None when the platform cannot say
    if real is not None and _fenced_relative(real, root) is None:
        os.close(fd)
        return None, "its descriptor resolves outside the workspace"
    try:
        handle = os.fdopen(fd, "r", encoding="utf-8", errors="replace")
    except OSError as exc:  # pragma: no cover - open succeeded, so this is a real host problem
        os.close(fd)
        return None, f"could not read it: {exc}"
    with handle:  # `handle` owns the descriptor from here on
        try:
            return (handle.read(max_chars) if max_chars > 0 else handle.read()), ""
        except OSError as exc:  # pragma: no cover - a read error is a real error
            return None, f"could not read it: {exc}"


def _pattern_segments(raw: str) -> tuple[str, ...]:
    return tuple(part for part in str(raw or "").split("/") if part not in ("", "."))


def _segments_match(relative: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    """Glob semantics on split paths: ``**`` crosses separators, ``*``/``?``/``[…]`` do not."""
    if not pattern:
        return not relative
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_segments_match(relative[index:], rest) for index in range(len(relative) + 1))
    if not relative:
        return False
    return bool(fnmatch.fnmatchcase(relative[0], head)) and _segments_match(relative[1:], rest)


def fenced_glob(pattern: str, root: Path, *, limit: int = 100) -> dict[str, Any]:
    """Match ``pattern`` against the workspace **through the fence** — files only, no links.

    The old implementation called ``root.glob(raw)`` and filtered the *results*: an absolute or
    ``..`` pattern was refused, but a pattern that reached a symlink still produced a candidate
    whose ``_relative`` fell back to an absolute outside path, and whether a symlinked directory
    was descended into depended on the interpreter's glob policy. This one enumerates only
    workspace files (:func:`fenced_files`) and matches their relative paths itself, so the
    fence does not depend on the interpreter, and a hidden entry stays hidden unless the pattern
    names it (pathlib's own rule).
    """
    raw = str(pattern or "").strip()
    if not raw:
        return {"error": "pattern is empty"}
    expanded = Path(raw).expanduser()
    if expanded.is_absolute() or raw.startswith(("/", "~")) or ".." in Path(raw).parts:
        return {"error": f"pattern leaves the workspace: {raw!r}"}
    wanted = max(1, int(limit or 100))
    segments = _pattern_segments(raw)
    allow_hidden = any(part.startswith(".") for part in segments)
    try:
        files, skipped, truncated = fenced_files(root, root)
    except OSError as exc:  # pragma: no cover - the walk swallows its own OSErrors
        return {"error": f"cannot match {raw!r}: {exc}"}
    matches: list[str] = []
    for candidate in files:
        rel = _fenced_relative(candidate, root)
        if rel is None:  # pragma: no cover - fenced_files only yields inside paths
            continue
        parts = tuple(rel.split("/"))
        if not allow_hidden and any(part.startswith(".") for part in parts):
            continue
        if _segments_match(parts, segments):
            matches.append(rel)
            if len(matches) >= wanted:
                break
    return {
        "pattern": raw,
        "matches": matches,
        "count": len(matches),
        "skipped": skipped[:FENCE_MAX_SKIPPED],
        "walk_truncated": truncated,
    }


@dataclass
class WorkspaceSnapshot:
    """One measurement of the workspace: what each file was, so a change can be named."""

    files: dict[str, tuple[int, int, str]]
    truncated: bool = False
    hashed_bytes: int = 0


def workspace_manifest(
    root: str | Path,
    *,
    max_files: int = SENTINEL_MAX_FILES,
    max_bytes: int = SENTINEL_MAX_BYTES,
) -> WorkspaceSnapshot:
    """Measure the workspace (size, mtime, and a digest when it is cheap) — the sentinel's side.

    Bounded on purpose: the sentinel exists to *name* what a read-only child's declared check
    changed, and it may not cost more than the check itself. When a workspace is larger than the
    cap the snapshot says ``truncated`` and the caller reports that the measurement is partial.
    """
    import hashlib

    base = Path(root)
    snapshot = WorkspaceSnapshot(files={})
    try:
        files, _skipped, truncated = fenced_files(base, base)
    except OSError:  # pragma: no cover - the walk swallows its own OSErrors
        return WorkspaceSnapshot(files={}, truncated=True)
    snapshot.truncated = truncated
    for candidate in files:
        if len(snapshot.files) >= max_files:
            snapshot.truncated = True
            break
        rel = _fenced_relative(candidate, base)
        if rel is None:  # pragma: no cover - fenced_files only yields inside paths
            continue
        try:
            stat = candidate.stat()
        except OSError:
            continue
        digest = ""
        if stat.st_size <= SENTINEL_HASH_MAX_BYTES and snapshot.hashed_bytes < max_bytes:
            text, problem = fenced_read(candidate, base)
            if problem == "" and text is not None:
                digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
                snapshot.hashed_bytes += len(text)
        snapshot.files[rel] = (int(stat.st_size), int(stat.st_mtime_ns), digest)
    return snapshot


def workspace_changes(before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> list[dict[str, str]]:
    """What the check did to the workspace — ``created`` / ``modified`` / ``deleted``, by name."""
    changes: list[dict[str, str]] = []
    for rel, was in before.files.items():
        now = after.files.get(rel)
        if now is None:
            changes.append({"path": rel, "change": "deleted"})
        elif was != now and (
            was[2] != now[2] or (not was[2] and (was[0], was[1]) != (now[0], now[1]))
        ):
            changes.append({"path": rel, "change": "modified"})
    for rel in after.files:
        if rel not in before.files:
            changes.append({"path": rel, "change": "created"})
    changes.sort(key=lambda item: (item["path"], item["change"]))
    return changes


#: What bwrap/the kernel says when a write was refused because the bind is read-only.
_READ_ONLY_REFUSAL = re.compile(
    r"read-only file system|Read-only file system|EROFS|errno 30\b|Errno 30\b"
)


def read_only_write_refusal(output: str) -> bool:
    """Is this output the *fence* refusing a write, rather than the project's suite failing?"""
    return bool(_READ_ONLY_REFUSAL.search(str(output or "")))


def child_handlers(
    workspace: str | Path,
    *,
    role: str = "",
    state_dir: str | Path | None = None,
    runtime: str | Path | None = None,
    test_timeout_s: float = CHILD_TEST_TIMEOUT_S,
    max_chars: int = 4000,
    max_matches: int = 50,
) -> dict[str, Callable[..., Any]]:
    """The real handlers a delegated child's tools run through — **for its role**.

    Reads (every role with the read tools): ``read_text``/``list_dir``/``sha256_file`` are the
    loop's own handlers (:func:`skaldir.agent_loop.builtin_tools`) — one implementation for a
    solo agent and a delegated one — ``read_file``/``write_text``-style spellings are the same
    operations under the product's names, and ``search_files``/``glob`` are implemented here
    over the same workspace fence, so a pattern that walks out of the workspace is an error the
    child can read, not a leak.

    Writes (``worker`` only, because only that role's tool list holds a write tool):
    ``write_file``/``write_text`` and ``patch`` go through the product's core toolset
    (:func:`skaldir.tools_core.registry.call` with ``root=workspace`` — typed contract,
    workspace-scoped path resolution, a re-read sha256 so the write is *verified*), and
    ``edit_apply`` through the atomic transaction engine (:mod:`skaldir.edit_tx`), the same
    engine behind ``skaldir edit apply``. No raw filesystem write exists here.

    The declared check (``worker`` and ``verifier``): ``run_tests``/``test_run`` resolve the
    workspace's declared test command and execute it through
    :func:`skaldir.verify_service.run_declared_criterion` — the ``bwrap`` sandbox, fail-closed
    when it is unavailable, never a raw unsandboxed subprocess.

    Deliberately absent: ``terminal``/``exec``/``docker.ensure`` have **no** handler. A delegated
    child gets the declared check, not a free shell; a granted tool with no handler is reported
    ``declared`` (never silently "executed"), and a tool the role does not hold is refused by the
    dispatcher before a handler is ever looked up.

    Without a role (``role=""``, the historical ceiling-free child) the map is **read-only**:
    this seam executes the rights a role table *granted*, and "nobody said which role" is not a
    grant. Nothing is widened by omission.
    """
    root = Path(workspace).expanduser()
    own = {tool.name: tool.handler for tool in builtin_tools(root)}
    policy = policy_for(role, allow_unroled=True)
    # The role table is the ceiling; a handler outside it would be unreachable anyway, so it is
    # simply not built (a test asserts `set(child_handlers(ws, role=r)) <= set(tools_for(r))`).
    # No role ⇒ no grant ⇒ no write/test handler (an unroled *dispatcher* would allow the call,
    # so this map is the only thing standing between it and the workspace).
    allowed: set[str] = set() if policy is None else set(policy.tools)
    # A read-only role that may still run the declared check (``verifier``): its test run must not
    # be able to change the workspace, and whether it could is *measured and reported* per call —
    # a tool name being in the role's table is not a write restriction.
    read_only_tests = bool(allowed & set(TEST_HANDLERS)) and not bool(allowed & set(WRITE_HANDLERS))
    write_state = (
        (Path(state_dir).expanduser() / DELEGATION_DIRNAME / "edit_tx")
        if state_dir
        else (root / ".godsv" / DELEGATION_DIRNAME / "edit_tx")
    )

    def search_files(pattern: str, path: str = ".", limit: int = 0) -> dict[str, Any]:
        """Grep the workspace **through the fence** — no link is followed, nothing outside is read.

        The walk (:func:`fenced_files`) refuses symlinks and the read (:func:`fenced_read`) is
        re-checked on the descriptor, so ``search_files`` cannot return a line of a file that
        lives outside the workspace — not through a symlinked file, not through a symlinked
        directory, not through an absolute or ``..`` ``path``. What it refused is *named* in the
        result (``refused``), so the child reads a fence, not an empty answer.
        """
        target, problem = _workspace_path(path, root)
        if target is None:
            return {"error": problem or "no usable path"}
        try:
            regex = re.compile(str(pattern or ""))
        except re.error as exc:
            return {"error": f"not a valid regular expression: {exc}"}
        wanted = max(1, int(limit or max_matches))
        hits: list[dict[str, Any]] = []
        refused: list[dict[str, str]] = []
        truncated = False
        walk_truncated = False
        if target.is_dir():
            candidates, refused, walk_truncated = fenced_files(target, root)
        elif target.is_file() and not target.is_symlink():
            candidates = [target]
        else:
            candidates = []
        for candidate in candidates:
            rel = _fenced_relative(candidate, root)
            if rel is None:  # pragma: no cover - fenced_files only yields inside paths
                continue
            text, why = fenced_read(candidate, root)
            if text is None:
                refused.append({"path": rel, "reason": why})
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if not regex.search(line):
                    continue
                if len(hits) >= wanted:
                    truncated = True
                    break
                hits.append({"path": rel, "line": number, "text": line.strip()[:200]})
                break  # one hit per file: the model gets coverage, not a dump
            if truncated:
                break
        return {
            "pattern": str(pattern or ""),
            "path": _relative(target, root) or ".",
            "matches": hits,
            "count": len(hits),
            "truncated": truncated,
            "refused": refused[:FENCE_MAX_SKIPPED],
            "walk_truncated": walk_truncated,
        }

    def glob(pattern: str, limit: int = 100) -> dict[str, Any]:
        """Match a workspace-relative glob, through the fence (see :func:`fenced_glob`)."""
        return fenced_glob(pattern, root, limit=limit)

    def read_text(path: str, max_bytes: int = 0) -> dict[str, Any]:
        """The loop's own fenced read (``_workspace_path`` resolves, so a link out is refused)."""
        return own["read_text"](
            path=path, max_bytes=int(max_bytes) if int(max_bytes or 0) > 0 else max_chars * 5
        )

    def list_dir(path: str = ".", limit: int = 200) -> dict[str, Any]:
        """The loop's own listing, with a link **named as a link**.

        The content fence refuses to read through a symlink, so it must not hand back the
        target's size either (the old listing reported ``child.stat().st_size``, which follows the
        link: existence and size of a file outside the workspace). An entry whose resolved target
        leaves the workspace is reported as a link with no size and ``target_outside: true``.
        """
        result = own["list_dir"](path=path, limit=limit)
        if not isinstance(result, dict) or result.get("error"):
            return result
        target, _problem = _workspace_path(path, root)
        if target is None:
            return result
        for entry in result.get("entries") or []:
            candidate = target / str(entry.get("name") or "")
            try:
                if not candidate.is_symlink():
                    continue
                entry["type"] = "link"
                entry["size"] = None
                entry["target_outside"] = _fenced_relative(candidate.resolve(), root) is None
            except OSError:
                continue
        return result

    handlers: dict[str, Callable[..., Any]] = {
        "read_file": read_text,
        "read_text": read_text,
        "read": read_text,
        "list_dir": list_dir,
        "sha256_file": own["sha256_file"],
        "search_files": search_files,
        "grep": search_files,
        "glob": glob,
    }

    # ---- the core toolset: the product's own write seam (workspace-scoped, verified) ---- #
    def core(tool: str, **arguments: Any) -> dict[str, Any]:
        """One call into the core toolset — the seam every other surface uses too."""
        from .tools_core import registry as core_registry

        return core_registry.call(
            tool, arguments, root=root, runtime=runtime
        ).to_dict()

    def write_file(
        path: str,
        content: str,
        mode: str = "overwrite",
        make_parents: bool = True,
    ) -> dict[str, Any]:
        """Write inside the run's workspace, through the core ``write_file`` contract."""
        return core(
            "write_file",
            path=path,
            content=content,
            mode=str(mode or "overwrite"),
            make_parents=bool(make_parents),
        )

    def write_text(path: str, text: str, create_dirs: bool = False) -> dict[str, Any]:
        """The loop's own spelling of ``write_file`` (same core contract underneath)."""
        return write_file(path=path, content=text, mode="overwrite", make_parents=bool(create_dirs))

    def patch(
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        fuzzy: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Replace a target string in one file, through the core ``patch`` contract."""
        return core(
            "patch",
            path=path,
            old_string=old_string,
            new_string=new_string,
            replace_all=bool(replace_all),
            fuzzy=bool(fuzzy),
            dry_run=bool(dry_run),
        )

    def edit_apply(
        reason: str,
        files: list[dict[str, Any]],
        verification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply one all-or-nothing edit transaction (multi-file, atomic, syntax-verified)."""
        from .edit_tx import parse_spec, run_transaction

        spec, problem = parse_spec(
            {
                "reason": reason,
                "files": files,
                "verification": verification or {"kind": "syntax"},
            }
        )
        if spec is None:
            return {"ok": False, "error": f"the transaction document was refused: {problem}"}
        record = run_transaction(spec, root, state_dir=write_state)
        return {
            "ok": bool(record.get("applied")) and bool(record.get("verified")),
            "tx_id": record.get("tx_id"),
            "verdict": record.get("verdict"),
            "applied": record.get("applied"),
            "verified": record.get("verified"),
            "applied_paths": [
                item.get("path") for item in record.get("files", []) if item.get("ok")
            ],
            "refused": [
                {"path": item.get("path"), "reason": item.get("reason")}
                for item in record.get("files", [])
                if not item.get("ok")
            ],
        }

    if allowed & set(WRITE_HANDLERS):
        handlers.update(
            {
                "write_file": write_file,
                "write_text": write_text,
                "patch": patch,
                "edit_apply": edit_apply,
            }
        )

    # ---- the declared check: sandboxed, fail-closed, the runtime's own judge ------------ #
    def run_tests(timeout_s: float = 0.0) -> dict[str, Any]:
        """Run the workspace's declared check in the sandbox and return its real verdict.

        The check itself runs through the runtime's **one judge**
        (:func:`skaldir.verify_service.run_declared_criterion`) — never a command this seam
        invents. A **read-only** role (``verifier``: reads and runs the declared check, writes
        nothing) additionally gets the workspace bound ``read-only`` **when the shared gate can
        build that bind** (:func:`_gate_read_only_support`: ``bwrap`` takes the last bind of a
        path, so only :func:`skaldir.gates._run` can put ``--ro-bind <workspace>`` after its
        ``--bind``).

        The result never has to be taken on trust:

        * ``workspace_bind`` — what the run really got (``read-only`` / ``read-write``);
        * ``read_only_enforced`` — the gate's own answer, ``False`` while the shared gate binds
          the workspace read-write (today), so nothing can claim a read-only verifier that is not;
        * ``workspace_changes`` — the write sentinel (:func:`workspace_manifest`), so the paths a
          read-only child's check touched are *named* even when the bind was read-write;
        * ``read_only_blocked_write`` — set when the check failed *because* the fence refused its
          write (``EROFS``), which is a fence, not a red suite.
        """
        from .verify_service import KIND_COMMAND, Criterion, run_declared_criterion

        argv, source = declared_test_argv(root, check_id=SUITE_CHECK_ID)
        if not argv:
            return {
                "ok": False,
                "status": "not-certifiable",
                "source": source,
                "detail": source,
            }
        limit = clamp_test_timeout(float(timeout_s or test_timeout_s))
        criterion = Criterion(
            text="the project's own declared check",
            criterion_id=SUITE_CHECK_ID,
            check_id=SUITE_CHECK_ID,
            kind=KIND_COMMAND,
            argv=tuple(argv),
            declared_command=" ".join(argv),
        )
        supported, support_detail = (
            _gate_read_only_support() if read_only_tests else (False, "this role may write")
        )
        enforced = bool(read_only_tests and supported)
        snapshot = workspace_manifest(root) if read_only_tests else None
        options = {"read_only_workspace": True} if enforced else {}
        try:
            status, exit_code, output, reason, cmd = run_declared_criterion(
                criterion, workspace=root, timeout=limit, label=SUITE_CHECK_ID, **options
            )
        except TypeError as exc:
            # The gate advertised a bind it does not accept: never a silent downgrade to
            # read-write, and never a claim of read-only. Name it as not-certifiable.
            return {
                "ok": False,
                "status": "not-certifiable",
                "cmd": " ".join(argv),
                "source": source,
                "timeout_s": limit,
                "read_only_requested": True,
                "read_only_enforced": False,
                "workspace_bind": READ_WRITE_BIND,
                "workspace_measured": False,
                "workspace_changes": [],
                "workspace_changed": None,
                "independent": None,
                "reason": (
                    "the gate advertised a read-only workspace bind but refused the argument "
                    f"({type(exc).__name__}: {exc}) — the declared check was not run"
                ),
            }
        changes = workspace_changes(snapshot, workspace_manifest(root)) if snapshot else []
        result: dict[str, Any] = {
            "ok": status == "passed",
            "status": status,
            "exit": exit_code,
            "cmd": cmd,
            "source": source,
            "timeout_s": limit,
            "detail": str(output or "")[:TOOL_RESULT_CHARS],
            "reason": str(reason or "")[:DETAIL_CHARS],
            "read_only_requested": bool(read_only_tests),
            "read_only_enforced": enforced,
            "workspace_bind": READ_ONLY_BIND if enforced else READ_WRITE_BIND,
            "workspace_measured": bool(snapshot is not None),
            "workspace_changes": changes[:FENCE_MAX_SKIPPED],
            "workspace_changes_count": len(changes),
            "workspace_changed": bool(changes) if snapshot is not None else None,
            "independent": (not changes) if snapshot is not None else None,
            "gate": {"read_only_capable": bool(supported), "detail": support_detail},
        }
        if read_only_tests and not enforced:
            result["read_only_detail"] = (
                "the shared gate binds the workspace read-write, so this declared check *could* "
                "change the workspace; the write sentinel measured it"
                + (
                    f": {len(changes)} path(s) changed — a read-only child's check is not a "
                    "read-only verification while a file changed"
                    if changes
                    else ": no path changed on this run"
                )
            )
        elif enforced:
            result["read_only_detail"] = (
                "the workspace is bound read-only for this check (--ro-bind): the check cannot "
                "change a project file"
            )
            if changes:  # pragma: no cover - the gate's bind and the measurement disagree
                result["read_only_detail"] += (
                    f" — WARNING: {len(changes)} path(s) changed anyway; the gate's bind and the "
                    "sentinel disagree (a gate bug, reported rather than hidden)"
                )
        if enforced and status != "passed" and read_only_write_refusal(output or ""):
            result["read_only_blocked_write"] = True
            result["reason"] = (
                "the declared check was refused a write by the read-only workspace bind "
                f"(EROFS), so this is a fence, not a red suite: {str(reason or '')[:DETAIL_CHARS]}"
            )[:DETAIL_CHARS]
        else:
            result["read_only_blocked_write"] = False
        return result

    if allowed & set(TEST_HANDLERS):
        handlers.update({"run_tests": run_tests, "test_run": run_tests})

    return handlers


@dataclass
class DelegatedFamily:
    """One delegation: the real run, its record, and the payload the model reads."""

    run: SubagentRun
    record: Any
    role: str
    handlers: dict[str, Callable[..., Any]] = field(default_factory=dict)
    started: float = field(default_factory=time.time)

    @property
    def mode(self) -> str:
        """What this family may really do — read from the role table, not from the payload."""
        return role_mode(self.role)

    def _child_row(self, child: dict[str, Any]) -> dict[str, Any]:
        calls = list(child.get("tool_calls") or [])
        return {
            "child_id": child.get("child_id"),
            "task": child.get("task"),
            "role": child.get("role") or None,
            "status": child.get("status"),
            "delivered": bool(child.get("ok")),
            "turns": int(child.get("turns") or 0),
            "tokens": int(child.get("spent_tokens") or 0),
            "answer": str(child.get("answer") or "")[:ANSWER_CHARS],
            "error": str(child.get("error") or "")[:DETAIL_CHARS],
            "stop_reason": str(child.get("stop_reason") or "")[:DETAIL_CHARS],
            "failure_class": child.get("failure_class"),
            "tools_offered": list(child.get("tools") or []),
            # What the child *did*, not what it said it did: every executed tool, every
            # refusal, and the tool that has no handler here.
            "tool_calls": [
                {
                    "tool": call.get("tool"),
                    "ok": call.get("ok"),
                    "executed": call.get("executed"),
                    "mode": call.get("mode"),
                    "code": call.get("code") or "",
                    "error": str(call.get("error") or "")[:DETAIL_CHARS],
                }
                for call in calls
            ],
            "tools_executed": sum(1 for call in calls if call.get("executed")),
            "tool_refusals": list(child.get("tool_refusals") or []),
        }

    def payload(self) -> dict[str, Any]:
        """What the model gets back — bounded, structured, and honest about the limits."""
        record = self.record.as_dict()
        children = [self._child_row(child) for child in (record.get("children") or [])]
        handlers = sorted(self.handlers)
        mode = self.mode
        may_write = bool(set(handlers) & set(WRITE_HANDLERS))
        may_test = bool(set(handlers) & set(TEST_HANDLERS))
        # A read-only role's declared check: the bind it will really get, asked from the gate
        # rather than assumed from the tool's name (see ``_gate_read_only_support``).
        if may_test and not may_write:
            tests_read_only, tests_detail = _gate_read_only_support()
            tests_bind = READ_ONLY_BIND if tests_read_only else READ_WRITE_BIND
        else:
            tests_read_only, tests_detail = False, "this role may write — its check runs read-write"
            tests_bind = READ_WRITE_BIND
        return {
            "schema": "skaldir.runtime_delegation.v1",
            "run_id": record.get("run_id"),
            "run_dir": str(self.run.run_dir),
            "role": self.role,
            "mode": mode,
            "depth": record.get("depth"),
            "max_depth": record.get("max_depth"),
            "total": int(record.get("children_total") or 0),
            "delivered": int(record.get("children_delivered") or 0),
            "tokens": int(record.get("spent_tokens") or 0),
            "failure_classes": record.get("failure_classes") or {},
            "nesting": record.get("nesting") or {"spawned": [], "refused": []},
            "children": children,
            "elapsed_ms": round((time.time() - self.started) * 1000.0, 3),
            "limits": {
                "role_ceiling": list(tools_for(self.role)),
                # Which of the role's tools this seam can *execute* and which are only
                # *declared* (no handler): the model is told the difference instead of being
                # left to discover it from a call that reports ``mode: declared``.
                "implemented_tools": handlers,
                "declared_tools": sorted(set(tools_for(self.role)) - set(handlers)),
                "may_spawn": False,
                "max_depth": DELEGATION_MAX_DEPTH,
                "max_children": MAX_DELEGATED_CHILDREN,
                "may_write": may_write,
                "may_test": may_test,
                "tests_workspace": tests_bind,
                "read_only_gate": {"capable": bool(tests_read_only), "detail": tests_detail},
                "workspace_fence": (
                    "the read/search/glob handlers never follow a symlink and re-check every "
                    "file through its own descriptor: no byte of a file outside the workspace "
                    "is returned, whatever path or pattern reaches it"
                ),
                "write_scope": str(Path(self.run.workspace).expanduser()),
                "mode": mode,
            },
            "notes": [
                "these children ran as a real subagent family (their run directory is on disk "
                "and their transcripts are readable)",
                (
                    "writes go through the product's own core toolset and the atomic edit "
                    "transaction engine, scoped to the run's workspace; the declared check runs "
                    "in the sandbox (fail-closed), and no child gets a free shell"
                ),
                (
                    "what a child may do is its role's own table: explore reads, verifier reads "
                    "and runs the declared check, worker also writes. A call outside the role's "
                    "ceiling is refused with a named code and never executed."
                ),
                "a delegated child may not spawn children (max_depth 1 from this seam, and its "
                "role holds no spawn right) — delegation depth is bounded at the root",
                (
                    "the read/search/glob handlers never follow a symlink and re-check every file "
                    "through its own descriptor: no byte of a file outside the workspace comes "
                    "back. A read-only child's declared check reports the bind it really got "
                    f"(limits.tests_workspace = {tests_bind}) and the paths its sentinel saw change"
                ),
            ],
        }


def spawn_family(
    *,
    tasks: Sequence[str],
    provider: Any,
    workspace: str | Path,
    state_dir: str | Path,
    session: str = "",
    role: str | None = None,
    kind: str = "",
    max_turns: int = CHILD_MAX_TURNS,
    budget_tokens: int = CHILD_BUDGET_TOKENS,
    timeout_s: float = CHILD_TIMEOUT_S,
    test_timeout_s: float = CHILD_TEST_TIMEOUT_S,
    parallel: int = 0,
    run_id: str = "",
    runtime: str | Path | None = None,
    provider_summary: dict[str, Any] | None = None,
    model_override: str = "",
    requested_provider: str = "",
) -> DelegatedFamily:
    """Run one delegated family for real and return it (the run, its record, its handlers).

    The provider resolution follows the MAS-0048 rule, **in order**:

    1. ``requested_provider`` (the model's own ``provider`` tool argument, or a CLI
       ``--provider`` namespaced to the spawn surface) wins when it names a real catalog
       provider — the children get a client built for *that* endpoint and model;
    2. otherwise ``settings.yaml`` ``subagent.provider`` (optionally ``subagent.model``) —
       the settings-level default. **The root's own provider is never a silent default on
       top of either**: when the settings name one, the root client is kept for the parent
       turn but the children take the settings-backed client and the record says so;
    3. no request and no settings key ⇒ today's fallback, unchanged: the run's own client
       (or a ``delegation.no_provider`` refusal when none is attached).

    Every resolution carries its *source* in the run record's ``provider`` block
    (``explicit provider request (…)`` / ``settings.yaml subagent.provider`` / the run's
    own note), so the endpoint a child really called is an argued fact, not an inherited
    silence. Raises :class:`DelegationRefused` — before any directory or thread exists —
    for a role a tool call may not ask for, an empty list of tasks, more children than
    :data:`MAX_DELEGATED_CHILDREN`, or a provider/model the catalog does not carry
    (``delegation.unknown_provider`` / ``delegation.unknown_model``).
    """
    wanted = [str(task).strip() for task in tasks or [] if str(task).strip()]
    if not wanted:
        raise DelegationRefused(
            "no task given: a delegation needs at least one task",
            code="delegation.no_tasks",
        )
    if len(wanted) > MAX_DELEGATED_CHILDREN:
        raise DelegationRefused(
            f"{len(wanted)} tasks asked for but a delegation runs at most "
            f"{MAX_DELEGATED_CHILDREN} children at once — split the work across turns",
            code="delegation.too_many_tasks",
        )
    child_provider = provider
    child_summary = (
        dict(provider_summary)
        if provider_summary
        else {
            "mode": "the run's own provider client",
            "note": (
                "delegated children call the same client the run already owns; no second "
                "provider is opened and the parent turn's retry gate is not shared"
            ),
        }
    )
    granted = _grantable_role(role, kind=kind)
    settings_provider = ""
    role_provider = ""
    if not requested_provider:
        from .subagents import read_subagent_role_overrides, subagent_provider_rule

        settings_provider = subagent_provider_rule(runtime)
        role_provider = str(
            (read_subagent_role_overrides(runtime).get(granted) or {}).get("provider")
            or ""
        ).strip()
    if not (requested_provider or settings_provider or role_provider):
        if provider is None:
            # No request, no settings key, no attached client: the pre MAS-0048 rule stands.
            raise DelegationRefused(
                "no provider client is attached to this run's tool surface, so a delegated "
                "child could not be called (no subagent.provider settings key either)",
                code="delegation.no_provider",
            )
    else:
        # An explicit request always resolves; with no client attached the settings
        # default is the one honest chance a delegation has to run at all.
        try:
            child_provider, child_default = _subagents().resolve_subagent_provider(
                requested_provider,
                model=model_override,
                role=granted,
                runtime=runtime,
            )
            if child_default.get("provider"):
                child_summary = {
                    "mode": child_default["provider"],
                    "provider": child_default["provider"],
                    "endpoint": child_default.get("endpoint", ""),
                    "model": child_default.get("model", ""),
                    "source": child_default.get("source", ""),
                    "note": (
                        "the children's client was resolved per MAS-0048: the run's own "
                        "client, if any, is not silently inherited"
                    ),
                }
        except _subagents().SubagentError as exc:
            text = str(exc)
            if "unknown model" in text:
                raise DelegationRefused(
                    text, code="delegation.unknown_model"
                ) from exc
            if "has no credential" in text:
                raise DelegationRefused(
                    text, code="delegation.provider_not_configured"
                ) from exc
            raise DelegationRefused(
                text, code="delegation.unknown_provider"
            ) from exc
        if child_provider is None:
            raise DelegationRefused(
                "no provider client is attached to this run's tool surface, so a "
                "delegated child could not be called",
                code="delegation.no_provider",
            )
    family_id = str(run_id or f"delegated-{uuid.uuid4().hex[:8]}")
    handlers = child_handlers(
        workspace,
        role=granted,
        state_dir=state_dir,
        runtime=runtime,
        test_timeout_s=test_timeout_s,
    )
    briefs = briefs_from_tasks(
        wanted,
        max_turns=int(max_turns),
        budget_tokens=int(budget_tokens),
        timeout_s=float(timeout_s),
        depth=1,
        role=granted,
        kind=kind,
    )
    root = Path(state_dir).expanduser() / DELEGATION_DIRNAME
    run = SubagentRun(
        provider=child_provider,
        briefs=briefs,
        state_dir=root,
        run_id=family_id,
        parallel=int(parallel),
        context_chars=0,
        tool_handlers=handlers,
        workspace=workspace,
        provider_summary=child_summary,
        max_depth=DELEGATION_MAX_DEPTH,
        depth=0,
    )
    record = run.run()
    return DelegatedFamily(
        run=run,
        record=record,
        role=granted,
        handlers=handlers,
        started=time.time(),
    )


def spawn_subagents_tool(
    *,
    workspace: str | Path,
    state_dir: str | Path,
    session: str = "",
    provider: Any,
    runtime: str | Path | None = None,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> AgentTool:
    """The ``spawn_subagents`` tool ``skaldir run`` offers the model.

    Its handler runs a real family (:func:`spawn_family`) and hands back a bounded payload; a
    refused delegation comes back as a **named** refusal (``ok: false``, ``error.code``), never
    as invented child output. ``on_result`` is the run's own bookkeeping hook (the bus row that
    makes "the model delegated this" visible in the turn's record).

    The honest provider rule (MAS-0048): the children's provider is *this call's*
    ``provider`` argument when given, else ``settings.yaml`` ``subagent.provider``
    when that key exists, else — and only else — the run's own client. The root
    provider is never silently inherited on top of a configured default, and every
    resolution is reported with the source of the endpoint it mapped to.
    """
    workspace = Path(workspace)
    run_provider = provider

    def spawn_subagents(
        tasks: list[str],
        role: str = "",
        max_turns: int = 0,
        provider: str = "",
        model: str = "",
    ) -> dict[str, Any]:
        try:
            from .subagent_roles import ROLE_WORKER, ROLE_BUDGETS

            resolved_turns = int(max_turns)
            if resolved_turns < 1:
                # 0 / absent = the role's own budget ceiling (never a zero-turn child)
                budget = ROLE_BUDGETS.get(str(role or "").strip() or ROLE_WORKER)
                resolved_turns = int(budget.max_turns) if budget else 1
            family = spawn_family(
                tasks=list(tasks or []),
                provider=run_provider,
                workspace=workspace,
                state_dir=state_dir,
                session=session,
                role=role,
                max_turns=min(resolved_turns, CHILD_MAX_TURNS),
                runtime=runtime,
                model_override=str(model or "").strip(),
                requested_provider=str(provider or "").strip(),
            )
        except DelegationRefused as exc:
            return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
        payload = family.payload()
        if on_result is not None:  # pragma: no cover - a broken hook must not fail a tool
            try:
                on_result(payload)
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "data": payload}

    return AgentTool(
        name=SPAWN_TOOL_NAME,
        description=(
            "Delegate independent sub-tasks to child agents that run in parallel and report "
            "back. Each child gets a role ceiling (explore = read-only research; worker = "
            "bounded production work that may write files inside the workspace and run the "
            "project's declared test check; verifier = an independent check that reads and runs "
            "that declared check with the workspace bound read-only where the sandbox can, and "
            "writes nothing). Children cannot delegate further, get no free shell, and cost real "
            "cost real tokens, so delegate work that is genuinely independent. Children "
            "default to the subagent.provider settings entry (or this run's own provider "
            "when unset); a call may name a provider/model the catalog carries instead."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        f"one task per child, at most {MAX_DELEGATED_CHILDREN} — each is a "
                        "self-contained assignment a child can do alone"
                    ),
                },
                "role": {
                    "type": "string",
                    "description": (
                        "the children's role ceiling: explore (read-only research), worker "
                        "(the default: reads, writes and runs the declared check), or verifier "
                        "(an independent check: reads and runs the declared check, writes "
                        "nothing). 'main' and 'unroled' are refused."
                    ),
                    "default": "",
                },
                "max_turns": {
                    "type": "integer",
                    "description": (
                        f"per-child turn cap (default {CHILD_MAX_TURNS}; the role's own envelope "
                        "may narrow it further)"
                    ),
                    "default": 0,
                },
                "provider": {
                    "type": "string",
                    "description": (
                        "optional provider id for this delegation (a name the provider "
                        "catalog carries, e.g. 'deepseek'); a child endpoint is refused by "
                        "name when the catalog does not know it. Without it the "
                        "subagent.provider settings key decides, never this run's own provider"
                    ),
                    "default": "",
                },
                "model": {
                    "type": "string",
                    "description": (
                        "optional model id the children call through (must be one the named "
                        "provider's catalog lists; the first documented model is the default)"
                    ),
                    "default": "",
                },
            },
            "required": ["tasks"],
            "additionalProperties": False,
        },
        handler=spawn_subagents,
        parallel_safe=False,
        mutating=False,
        source="goal_run",
    )
