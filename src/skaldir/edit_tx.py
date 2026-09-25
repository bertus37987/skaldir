"""``edit`` — edit transactions: a batch of file edits applied atomically.

Why a transaction, and not just a better ``patch`` call
------------------------------------------------------
``tools_core.patch_tool`` edits **one** file and verifies nothing after the write.
That is the right primitive, and this layer builds on it instead of re-implementing
it: the matching ladder (``exact`` → ``trailing-whitespace`` →
``indentation-flexible`` → ``similar``), the ambiguity refusal, the declared-root
containment, the binary/size refusals and the deny-globs all come from
``tools_core.sandbox`` / ``tools_core.patch_tool``. What a single call cannot give,
and what is added here:

* **all-or-nothing over many files** — every edit is resolved and *staged* first; the
  workspace is published only once every file is ready, and any failure restores the
  pre-images (a temp file + ``os.replace``, never a truncate-write);
* **a dry-run** that shows the per-file diff **and the exact failure reason**
  (``not_found`` / ``ambiguous_match`` / ``would_change_other_occurrences`` /
  ``cascade_edit``) — and exits with the same code ``apply`` would exit with;
* **a verification step after apply** — a declared command, a set of test targets, or
  a syntax/parse check — whose failure rolls the whole transaction back;
* **a snapshot per transaction** and a **byte-identical undo** (sha256 proven);
* **hard caps** on what a transaction may read, stage, patch and keep.

The refusals a caller meets most often are deliberately *not* best-effort: a needle
found twice is a refusal, not a coin flip; a needle whose *looser* tolerance form
appears elsewhere is a refusal too (``would_change_other_occurrences``), because that
is how an edit meant for one function silently lands in its twin.

Reference patterns (studied, not copied)
----------------------------------------
* **Claude Code** — search-and-replace as the editing primitive, ``old_string`` unique
  unless ``replace_all`` is set, a mandatory read before an edit ("file modified since
  read" is a hard error), and cascade protection so a later edit does not silently
  match text an earlier edit just inserted. Here: the uniqueness refusal,
  :func:`run_transaction` comparing the ``before_sha256`` it read against the tree at
  publish time (``changed_since_plan``), and :data:`REASON_CASCADE`.
* **Cline** — checkpoints as a *shadow snapshot* kept outside the user's VCS history,
  with "restore files" separate from "restore the conversation". Here: the
  per-transaction snapshot directory under the runtime state dir (the project's own
  ``.git`` is never touched) and ``edit undo`` restoring exactly the touched files.
* **OpenCode** — ``edit`` with a unique ``oldString`` (or ``replaceAll``), a refusal
  for paths outside the project, and diagnostics run *after* the write. Here: the
  declared-root containment, and the post-apply verification step.
* **This repository** — ``tools_core.patch_tool`` / ``tools_core.sandbox`` (single-file
  fuzzy patch, path containment, binary/cap policy) and ``verify_service``'s split
  between "executed", "passed" and "certifiable", which this layer keeps: a
  transaction is never reported ``verified`` when no check executed.

Caps (also in ``data.caps`` and ``MVP/36-edit-transactions.md``)
---------------------------------------------------------------
===========================  ==================  =================================
cap                          default             what it bounds
===========================  ==================  =================================
``max_file_bytes``           2 MiB               one file read *and* patched
``max_tx_bytes``             8 MiB               sum of the pre-images (snapshot)
``max_files``                64                  files in one transaction
``max_edits``                256                 edits in one transaction
``max_needle_chars``         200 000             one ``old``/``new`` string
``max_diff_bytes``           200 000             the diff in the *payload*
``verify_timeout_s``         120                 the declared verification command
``keep_transactions``        32                  snapshots kept (see :func:`prune`)
===========================  ==================  =================================

Every cap is overridable through the environment helper
(``SKALDIR_EDIT_MAX_FILE_BYTES`` …, the legacy ``GODSV_*`` spelling still accepted),
never by a flag that only the process which forgot to set it knows about.
``patch_tool`` additionally refuses a file above its own 5 MiB limit: both gates stay
in place.

The state directory holds the snapshots and the records::

    <state>/                       default <runtime>/edit
      20260918-142233-123456-a1b2c3.json  one transaction record (the ``edit show`` payload)
      20260918-142233-123456-a1b2c3/
        pre/000-mod.py             the pre-image bytes (the undo source)
        stage/000-mod.py           the working copy during apply
        diff.patch                 the full aggregate diff (payload copies are capped)

Honest limits (repeated in every record's ``limits``)
-----------------------------------------------------
* Atomic across files only in the *published* sense: files are replaced one after the
  other with ``os.replace``; a crash between two replacements leaves a partly updated
  tree, and the record names the pre-images that restore it.
* A rollback restores the files the transaction touched — not side effects of a
  verification command that ran (and perhaps wrote files) before failing.
* ``syntax`` proves the changed files *parse*; it proves nothing about behaviour. It is
  the default only because it always executes; a declared check is always better.
* No shell: a verification command is an argv, or a shlex-split string whose first
  token is not a shell. ``sh -c "…"`` is refused.
* The verification command runs with the workspace as cwd and the credential-scrubbed
  environment from ``sandbox.child_environment`` (``PYTHONPATH`` dropped on purpose).
  It is *declared*, never assembled by this layer.
* No lock: two concurrent transactions over one file are not serialized; the
  changed-since-plan check catches the loser.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import secrets
import shlex
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import env

try:  # the sibling EXPAND toolset: the matching ladder and the path policy live there
    from .tools_core import patch_tool as _patch_tool
    from .tools_core import sandbox as _sandbox
except Exception:  # noqa: BLE001 — a build without tools_core still imports this module
    _patch_tool = None
    _sandbox = None

SCHEMA_VERSION = "1.0"

#: Directory name under the runtime state dir that holds records + snapshots.
RECORD_DIRNAME = "edit"

#: Suffix of the temporary file a publish goes through (same directory as the target,
#: so ``os.replace`` is a rename inside one filesystem).
TMP_SUFFIX = ".skaldir-edit-tmp"

# --------------------------------------------------------------------------- #
# caps                                                                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EditCaps:
    """The caps one transaction will not cross. Frozen: a cap is not a suggestion."""

    max_file_bytes: int = 2 * 1024 * 1024
    max_tx_bytes: int = 8 * 1024 * 1024
    max_files: int = 64
    max_edits: int = 256
    max_needle_chars: int = 200_000
    max_diff_bytes: int = 200_000
    verify_timeout_s: float = 120.0
    keep_transactions: int = 32

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> EditCaps:
        """Defaults, each replaced by its environment override when one is set."""
        values: dict[str, Any] = {}
        for name, row in _CAP_ENV.items():
            raw = env.getenv(row[0], "", environ=environ)
            if raw in (None, ""):
                continue
            try:
                values[name] = row[1](str(raw).strip())
            except ValueError:
                continue  # a malformed override is ignored, never silently zeroed
        return cls(**values)

    def as_dict(self) -> dict[str, dict[str, Any]]:
        """One row per cap: value, unit, the environment name that overrides it."""
        return {
            name: {
                "value": getattr(self, name),
                "unit": row[2],
                "env": env.primary_name(row[0]),
                "what": row[3],
            }
            for name, row in _CAP_ENV.items()
        }


#: field -> (env token, caster, unit, what it bounds). The env *token* is unprefixed
#: on purpose: ``skaldir.env`` owns the two prefixes (see its module note).
_CAP_ENV: dict[str, tuple[str, Any, str, str]] = {
    "max_file_bytes": ("EDIT_MAX_FILE_BYTES", int, "bytes", "one file read and patched"),
    "max_tx_bytes": ("EDIT_MAX_TX_BYTES", int, "bytes", "sum of the pre-images (snapshot)"),
    "max_files": ("EDIT_MAX_FILES", int, "count", "files in one transaction"),
    "max_edits": ("EDIT_MAX_EDITS", int, "count", "edits in one transaction"),
    "max_needle_chars": ("EDIT_MAX_NEEDLE_CHARS", int, "characters", "one old/new string"),
    "max_diff_bytes": ("EDIT_MAX_DIFF_BYTES", int, "bytes", "the diff inside the payload"),
    "verify_timeout_s": ("EDIT_VERIFY_TIMEOUT_S", float, "seconds", "the verification command"),
    "keep_transactions": ("EDIT_KEEP_TRANSACTIONS", int, "count", "snapshots kept (see prune)"),
}

# --------------------------------------------------------------------------- #
# vocabulary                                                                    #
# --------------------------------------------------------------------------- #

#: The file could not be found / is not a regular file.
REASON_NOT_FOUND = "not_found"
#: ≥2 regions match at the accepting tolerance — a refusal, never a coin flip.
REASON_AMBIGUOUS = "ambiguous_match"
#: The edit would change occurrences the caller did not name (``detail.cause`` is
#: ``near_duplicate_regions`` or ``occurrence_count_mismatch``).
REASON_OTHER_OCCURRENCES = "would_change_other_occurrences"
#: A later edit in the same transaction matches text an earlier edit inserted.
REASON_CASCADE = "cascade_edit"
#: ``old`` == ``new`` — nothing to do (usage, not damage).
REASON_UNCHANGED = "unchanged"
#: The file moved between the plan (dry-run) and the publish.
REASON_CHANGED_SINCE_PLAN = "changed_since_plan"
#: Policy refusals (never damage): the workspace, the path, the shape, or a cap.
REASON_BLOCKED = "blocked"
#: The pre-image is gone, so an exact undo is impossible.
REASON_SNAPSHOT_MISSING = "snapshot_missing"
#: ``tools_core`` is not in this build — the matching ladder has no home.
REASON_DEPENDENCY_MISSING = "dependency_missing"
#: The transaction was applied but nothing may be undone (never applied, already undone).
REASON_NOTHING_APPLIED = "nothing_applied"
REASON_ALREADY_UNDONE = "already_undone"
#: A touched file changed after the apply, so restoring it would lose work.
REASON_MODIFIED_SINCE_APPLY = "modified_since_apply"

#: reasons that mean "the transaction would not do what was asked" (exit 6)
CONFLICT_REASONS = frozenset(
    {
        REASON_AMBIGUOUS,
        REASON_OTHER_OCCURRENCES,
        REASON_CASCADE,
        REASON_CHANGED_SINCE_PLAN,
        REASON_NOTHING_APPLIED,
        REASON_ALREADY_UNDONE,
        REASON_MODIFIED_SINCE_APPLY,
    }
)
#: reasons that mean "the target does not exist" (exit 5)
MISSING_REASONS = frozenset({REASON_NOT_FOUND})
#: reasons that mean "the call itself was wrong" (exit 1)
USAGE_REASONS = frozenset({REASON_UNCHANGED})

VERDICT_APPLICABLE = "applicable"
VERDICT_NOT_APPLICABLE = "not_applicable"
VERDICT_APPLIED = "applied"
VERDICT_BLOCKED = "blocked"
VERDICT_ROLLED_BACK = "rolled_back"
VERDICT_APPLIED_UNVERIFIED = "applied_unverified"
VERDICT_UNDONE = "undone"

VERDICTS = (
    VERDICT_APPLICABLE,
    VERDICT_NOT_APPLICABLE,
    VERDICT_APPLIED,
    VERDICT_BLOCKED,
    VERDICT_ROLLED_BACK,
    VERDICT_APPLIED_UNVERIFIED,
    VERDICT_UNDONE,
)

#: The one list of limits every record carries (kept next to the code that has them).
LIMITS: tuple[str, ...] = (
    "atomic across files only in the published sense: each file is os.replace'd one "
    "after another, so a crash mid-publish can leave a partly updated tree (the "
    "record's pre-images still restore it)",
    "a rollback restores the touched files only — not files a verification command "
    "wrote before it failed",
    "the 'syntax' check proves the changed files parse; it proves nothing about "
    "behaviour",
    "a verification command is an argv (no shell): 'sh -c …' is refused",
    "the verification command runs with the workspace as cwd and a credential-scrubbed "
    "environment (PYTHONPATH dropped)",
    "no lock: two concurrent transactions over the same file are not serialized (the "
    "changed-since-plan check catches the loser)",
)


# --------------------------------------------------------------------------- #
# small helpers                                                                 #
# --------------------------------------------------------------------------- #


def _now_iso(stamp: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(stamp if stamp else time.time()))


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def new_tx_id(stamp: float | None = None) -> str:
    """``YYYYmmdd-HHMMSS-uuuuuu-<6 hex>`` — sortable to the microsecond, unique, no lock.

    The microsecond field is not decoration: ``list_transactions`` and ``resolve_tx_id``
    order records by id, and two transactions in the same second must still order the way
    they happened.
    """
    moment = stamp if stamp else time.time()
    micro = int(round((moment - int(moment)) * 1_000_000)) % 1_000_000
    return (
        time.strftime("%Y%m%d-%H%M%S", time.localtime(moment))
        + f"-{micro:06d}-"
        + secrets.token_hex(3)
    )


def _issue(reason: str, message: str, **detail: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"reason": reason, "message": message}
    clean = {key: value for key, value in detail.items() if value not in (None, "", [], {})}
    if clean:
        record["detail"] = clean
    return record


def _matching_gap() -> dict[str, Any] | None:
    """The one refusal that means "this build has no matching ladder"."""
    if _patch_tool is None or _sandbox is None:
        return _issue(
            REASON_DEPENDENCY_MISSING,
            "skaldir.tools_core (patch_tool/sandbox) is not importable in this build — "
            "the matching ladder and the path policy live there",
        )
    return None


def _label(workspace: Path, path: Path) -> str:
    """The path as the caller spelled it: relative to the workspace when inside."""
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return str(path)


def _resolve(workspace: Path, value: str) -> tuple[Path | None, dict[str, Any] | None, bool]:
    """Resolve ``value`` inside ``workspace``; returns ``(path, issue, via_symlink)``.

    The containment rule is ``tools_core.sandbox``'s (symlinks followed, ``..``
    resolved, ``~`` refused) — one implementation, not a second copy of it. A path that
    only resolves inside *because* a symlink points there is allowed and reported
    (``via_symlink``); a symlink that escapes the root is refused by the resolver.
    """
    assert _sandbox is not None  # guarded by _matching_gap()
    path, problem = _sandbox.candidate_path(workspace, value)
    if path is None:
        return None, _issue(REASON_BLOCKED, problem or "path refused", path=str(value)), False
    raw = Path(str(value))
    joined = raw if raw.is_absolute() else workspace / raw
    try:
        via_symlink = Path(os.path.realpath(joined)) != Path(joined)
    except OSError:  # pragma: no cover — a symlink loop is already refused above
        via_symlink = False
    return path, None, bool(via_symlink)


def _read_target(
    path: Path, workspace: Path, caps: EditCaps
) -> tuple[bytes | None, dict[str, Any] | None]:
    """Read one file under the policy: exists, regular, inside a deny glob, text, capped."""
    assert _sandbox is not None  # guarded by _matching_gap()
    label = _label(workspace, path)
    if not path.exists():
        return None, _issue(REASON_NOT_FOUND, f"file does not exist: {label}")
    if not path.is_file():
        return None, _issue(REASON_BLOCKED, f"not a regular file: {label}")
    denied = _sandbox.write_denied(workspace, path)
    if denied:
        return None, _issue(
            REASON_BLOCKED,
            f"editing {label} is refused by the deny rule {denied!r}",
            deny_glob=denied,
        )
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, _issue(REASON_BLOCKED, f"cannot stat {label}: {exc}")
    if size > caps.max_file_bytes:
        return None, _issue(
            REASON_BLOCKED,
            f"{label} is {size} bytes — above the {caps.max_file_bytes}-byte file cap "
            f"({env.primary_name('EDIT_MAX_FILE_BYTES')} raises it)",
            size_bytes=size,
            cap=caps.max_file_bytes,
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, _issue(REASON_BLOCKED, f"cannot read {label}: {exc}")
    if _sandbox.is_probably_binary(raw):
        return None, _issue(REASON_BLOCKED, f"{label} is a binary file (NUL in the first 8 KiB)")
    return raw, None


def _truncate(text: str, cap: int) -> tuple[str, bool]:
    """Cut ``text`` to ``cap`` bytes with a marker that says how much was removed."""
    raw = text.encode("utf-8")
    if len(raw) <= cap:
        return text, False
    marker = f"\n[edit diff truncated: kept {cap} of {len(raw)} bytes; full diff in the record]\n"
    return raw[:cap].decode("utf-8", "replace") + marker, True


def _atomic_write(target: Path, data: bytes, mode: int | None = None) -> None:
    """Publish ``data`` at ``target``: temp in the same directory, fsync, rename.

    Same-directory temp + ``os.replace`` is what makes a publish a rename (and never a
    truncate-write), and the fallback covers the state dir being on another filesystem.
    """
    directory = target.parent
    handle = None
    tmp = directory / f".{target.name}.{os.getpid()}{TMP_SUFFIX}-{secrets.token_hex(3)}"
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        handle = os.fdopen(fd, "wb")
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        if mode is not None:
            try:
                os.chmod(tmp, mode)
            except OSError:  # pragma: no cover — a filesystem without modes
                pass
        os.replace(tmp, target)
    finally:
        if handle is not None:  # pragma: no cover — the error path already opened files
            handle.close()
        if tmp.exists():  # a failed publish must not leave its temp file behind
            tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# the transaction document                                                      #
# --------------------------------------------------------------------------- #


def parse_spec(document: Any) -> tuple[dict[str, Any] | None, str]:
    """Validate one transaction document; returns ``(spec, "")`` or ``(None, why)``.

    Shape (every key but ``files`` is optional)::

        {"reason": "why",
         "strict": true,
         "verification": {"kind": "syntax"}
                       | {"kind": "command", "argv": ["python", "-m", "pytest", "-q"]}
                       | {"kind": "command", "command": "python -m pytest -q"}
                       | {"kind": "tests", "targets": ["tests/test_x.py::test_y"]}
                       | {"kind": "none"},
         "files": [{"path": "src/a.py",
                    "edits": [{"old": "…", "new": "…",
                               "expect_occurrences": 1, "replace_all": false,
                               "fuzzy": true, "fuzzy_min_ratio": 0.8}]}]}
    """
    if not isinstance(document, dict):
        return None, "a transaction document must be a JSON object"
    files = document.get("files")
    if not isinstance(files, list) or not files:
        return None, "a transaction needs a non-empty 'files' list"
    parsed: list[dict[str, Any]] = []
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            return None, f"files[{index}] must be an object"
        path = str(entry.get("path") or "").strip()
        if not path:
            return None, f"files[{index}] has no 'path'"
        edits = entry.get("edits")
        if not isinstance(edits, list) or not edits:
            return None, f"files[{index}] ({path}) needs a non-empty 'edits' list"
        parsed_edits: list[dict[str, Any]] = []
        for position, edit in enumerate(edits):
            parsed_edit, problem = _parse_edit(edit, index, position)
            if problem:
                return None, problem
            assert parsed_edit is not None
            parsed_edits.append(parsed_edit)
        parsed.append({"path": path, "edits": parsed_edits})
    verification, problem = normalize_verification(document.get("verification"))
    if problem:
        return None, problem
    return (
        {
            "reason": str(document.get("reason") or ""),
            "strict": bool(document.get("strict", True)),
            "files": parsed,
            "verification": verification,
        },
        "",
    )


def _parse_edit(edit: Any, index: int, position: int) -> tuple[dict[str, Any] | None, str]:
    """One ``old``/``new`` entry, validated (per-edit rules, not the whole document)."""
    where = f"files[{index}].edits[{position}]"
    if not isinstance(edit, dict):
        return None, f"{where} must be an object"
    old = edit.get("old")
    new = edit.get("new")
    if not isinstance(old, str) or not isinstance(new, str):
        return None, f"{where} needs string 'old' and 'new'"
    if old == "":
        return None, f"{where} has an empty 'old'"
    replace_all = bool(edit.get("replace_all", False))
    expect_declared = edit.get("expect_occurrences") is not None
    expect = edit.get("expect_occurrences", 1)
    if not isinstance(expect, int) or isinstance(expect, bool) or expect < 1:
        return None, f"{where}.expect_occurrences must be an integer >= 1"
    if int(expect) > 1 and not replace_all:
        return None, (
            f"{where}.expect_occurrences is {expect} but replace_all is false — declare "
            "replace_all: true to change more than one occurrence on purpose"
        )
    try:
        ratio = float(edit.get("fuzzy_min_ratio", 0.8))
    except (TypeError, ValueError):
        return None, f"{where}.fuzzy_min_ratio is not a number"
    return (
        {
            "old": old,
            "new": new,
            "expect_occurrences": int(expect),
            "expect_declared": bool(expect_declared),
            "replace_all": replace_all,
            "fuzzy": bool(edit.get("fuzzy", True)),
            "fuzzy_min_ratio": ratio,
        },
        "",
    )


#: Kinds of verification a caller may declare. ``syntax`` is the default because it
#: always executes; ``none`` is an explicit, loudly reported opt-out.
VERIFY_KINDS = ("syntax", "command", "tests", "none")

#: A verification "command" is an argv. These are refused: a shell string is not one.
SHELL_TOKENS = frozenset({"sh", "bash", "zsh", "ksh", "dash", "fish", "csh", "tcsh"})

#: Suffix -> the parse used by the ``syntax`` check (the *file's* own parser).
SYNTAX_SUFFIXES: dict[str, str] = {
    ".py": "python-compile",
    ".pyi": "python-compile",
    ".json": "json-parse",
    ".toml": "toml-parse",
    ".yaml": "yaml-parse",
    ".yml": "yaml-parse",
}


def normalize_verification(declared: Any) -> tuple[dict[str, Any], str]:
    """Validate the caller's verification declaration (shape only — no I/O)."""
    if declared is None or declared == "":
        return (
            {"kind": "syntax", "declared": False, "note": "no check declared: parsing only"},
            "",
        )
    if isinstance(declared, str):
        declared = {"kind": declared}
    if not isinstance(declared, dict):
        return {}, "verification must be an object, a kind name, or absent"
    kind = str(declared.get("kind") or "").strip().lower()
    if kind not in VERIFY_KINDS:
        return {}, f"verification.kind must be one of {', '.join(VERIFY_KINDS)} (got {kind!r})"
    timeout = declared.get("timeout_s")
    try:
        timeout_value = float(timeout) if timeout not in (None, "") else 0.0
    except (TypeError, ValueError):
        return {}, "verification.timeout_s is not a number"
    if timeout_value < 0:
        return {}, "verification.timeout_s must not be negative"
    expect = declared.get("expect_exit", 0)
    if not isinstance(expect, int) or isinstance(expect, bool):
        return {}, "verification.expect_exit must be an integer"
    if kind in ("syntax", "none"):
        return {"kind": kind, "declared": True}, ""
    if kind == "command":
        argv = declared.get("argv")
        text = str(declared.get("command") or "").strip()
        if argv is not None:
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
                return {}, "verification.argv must be a non-empty list of strings"
            tokens = [str(item) for item in argv]
        elif text:
            try:
                tokens = shlex.split(text)
            except ValueError as exc:
                return {}, f"verification.command does not split: {exc}"
        else:
            return {}, "verification kind 'command' needs 'argv' or 'command'"
        if not tokens:
            return {}, "verification command is empty"
        if Path(tokens[0]).name in SHELL_TOKENS:
            return {}, (
                "a shell verification command is refused (no 'sh -c'): declare the argv "
                "of the program instead, e.g. {'kind': 'command', 'argv': "
                "['python', '-m', 'pytest', '-q']}"
            )
        return (
            {
                "kind": kind,
                "declared": True,
                "argv": tokens,
                "timeout_s": timeout_value,
                "expect_exit": int(expect),
            },
            "",
        )
    targets = declared.get("targets")
    if isinstance(targets, str):
        targets = [targets]
    if not isinstance(targets, list) or not targets or not all(isinstance(x, str) for x in targets):
        return {}, "verification kind 'tests' needs a non-empty 'targets' list of paths"
    argv = [sys.executable or "python", "-m", "pytest", "-q", "-o", "addopts=", *targets]
    return (
        {
            "kind": kind,
            "declared": True,
            "argv": argv,
            "targets": [str(x) for x in targets],
            "timeout_s": timeout_value,
            "expect_exit": int(expect),
        },
        "",
    )


# --------------------------------------------------------------------------- #
# the matching half: the sibling's ladder + the two checks it cannot do          #
# --------------------------------------------------------------------------- #


def near_duplicate_regions(text: str, old: str, *, indent: bool = True) -> list[int]:
    """Line numbers of the regions that match ``old`` at a looser tolerance.

    ``tools_core.patch_tool`` matches at one tolerance and refuses ≥2 hits *there*.
    This is the other direction: the literal needle is unique, but its
    trailing-whitespace/indentation-normalised form also appears elsewhere — so a later
    edit (or a re-apply after a reformat) aimed at the same text could land in the
    twin. Strict transactions refuse and report the lines.
    """
    needle = [line.rstrip() for line in old.splitlines()]
    if indent:
        needle = [line.lstrip() for line in needle]
    if not needle:
        return []
    span = len(needle)
    lines = text.splitlines(keepends=True)
    hits: list[int] = []
    for start in range(0, len(lines) - span + 1):
        block = [line.rstrip() for line in lines[start : start + span]]
        if indent:
            block = [line.lstrip() for line in block]
        if block == needle:
            hits.append(start + 1)
    return hits


def _occurrence_lines(text: str, needle: str) -> list[int]:
    """The line numbers of every literal occurrence of ``needle`` (all of them)."""
    lines: list[int] = []
    start = text.find(needle)
    while start != -1:
        lines.append(1 + text.count("\n", 0, start))
        start = text.find(needle, start + len(needle))
    return lines


def _cascade_conflict(inserted: list[str], old: str) -> str | None:
    """Does ``old`` match text a previous edit of the same file inserted?

    Claude Code's cascade protection: in a multi-step edit, a later step must not
    silently match what an earlier step just wrote.
    """
    for index, text in enumerate(inserted):
        if text and old in text:
            return f"the needle matches text inserted by edit #{index + 1} of this file"
    return None


# --------------------------------------------------------------------------- #
# staging one file                                                              #
# --------------------------------------------------------------------------- #


def _file_record(path_value: str) -> dict[str, Any]:
    """A file result with every field a caller may read, in one shape."""
    return {
        "path": path_value,
        "abs_path": "",
        "ok": False,
        "reason": "",
        "message": "",
        "detail": {},
        "via_symlink": False,
        "strategy": "",
        "score": 1.0,
        "matches": 0,
        "occurrence_lines": [],
        "near_duplicate_lines": [],
        "before_sha256": "",
        "after_sha256": "",
        "bytes_before": 0,
        "bytes_after": 0,
        "mode": 0,
        "edits": [],
        "diff": "",
        "diff_truncated": False,
    }


def _stage_file(
    entry: dict[str, Any],
    workspace: Path,
    stage_root: Path,
    index: int,
    caps: EditCaps,
    *,
    strict: bool,
    context_lines: int,
) -> tuple[dict[str, Any], bytes | None, bytes | None]:
    """Resolve, check and patch one file **on a staged copy**.

    Returns ``(record, original_bytes, patched_bytes)`` — the bytes are ``None`` when the
    file was refused. Nothing outside ``stage_root`` is written, which is why the same
    code path serves ``dry-run`` and ``apply``.
    """
    record = _file_record(entry["path"])
    gap = _matching_gap()
    if gap is not None:
        record.update({"reason": gap["reason"], "message": gap["message"]})
        return record, None, None
    target, issue, via_symlink = _resolve(workspace, entry["path"])
    record["via_symlink"] = via_symlink
    if target is None:
        assert issue is not None
        record.update(
            {"reason": issue["reason"], "message": issue["message"],
             "detail": issue.get("detail", {})}
        )
        return record, None, None
    record["abs_path"] = str(target)
    raw, issue = _read_target(target, workspace, caps)
    if raw is None:
        assert issue is not None
        record.update(
            {"reason": issue["reason"], "message": issue["message"],
             "detail": issue.get("detail", {})}
        )
        return record, None, None
    for edit in entry["edits"]:
        for key in ("old", "new"):
            if len(edit[key]) > caps.max_needle_chars:
                record.update(
                    {
                        "reason": REASON_BLOCKED,
                        "message": (
                            f"the '{key}' string is {len(edit[key])} characters — above the "
                            f"{caps.max_needle_chars}-character needle cap"
                        ),
                        "detail": {"cap": caps.max_needle_chars, "chars": len(edit[key])},
                    }
                )
                return record, None, None

    mode = target.stat().st_mode & 0o7777
    stage_name = f"{index:03d}-{target.name}"
    stage_target = stage_root / stage_name
    stage_target.write_bytes(raw)
    record.update(
        {
            "mode": mode,
            "stage_file": stage_name,
            "before_sha256": _sha256(raw),
            "bytes_before": len(raw),
        }
    )

    inserted: list[str] = []
    for position, edit in enumerate(entry["edits"]):
        current = stage_target.read_bytes()
        if edit["old"] == edit["new"]:
            record.update(
                {
                    "reason": REASON_UNCHANGED,
                    "message": (
                        f"{entry['path']}: edit #{position + 1} has identical old/new "
                        "(a no-op patch)"
                    ),
                }
            )
            return record, raw, None
        if strict and position > 0:
            cascade = _cascade_conflict(inserted, edit["old"])
            if cascade:
                record.update(
                    {
                        "reason": REASON_CASCADE,
                        "message": f"{entry['path']}: {cascade}",
                        "detail": {"edit_index": position + 1},
                    }
                )
                return record, raw, None
        text = current.decode("utf-8", "replace")
        literal = text.count(edit["old"])
        if literal > 1 and not edit["replace_all"]:
            # ≥2 matches at the accepting (literal) tolerance: the refusal the caller must
            # see is ambiguity, and it names both lines. It takes precedence over the
            # near-duplicate check below, which is about a *unique* needle's twin.
            record.update(
                {
                    "reason": REASON_AMBIGUOUS,
                    "message": (
                        f"{entry['path']}: the needle occurs {literal} times in the file; "
                        "lengthen 'old' until it is unique, or declare replace_all with the "
                        "expect_occurrences you mean"
                    ),
                    "detail": {
                        "edit_index": position + 1,
                        "matches": literal,
                        "occurrence_lines": _occurrence_lines(text, edit["old"]),
                    },
                }
            )
            return record, raw, None
        if literal <= 1 and not edit["replace_all"]:
            hits = near_duplicate_regions(text, edit["old"])
            record["near_duplicate_lines"] = hits
            if strict and len(hits) > 1:
                record.update(
                    {
                        "reason": REASON_OTHER_OCCURRENCES,
                        "message": (
                            f"{entry['path']}: the needle is unique literally but "
                            f"{len(hits)} regions match it once whitespace/indentation is "
                            "normalised — a later edit or a reformat could change an "
                            "occurrence you did not name"
                        ),
                        "detail": {
                            "cause": "near_duplicate_regions",
                            "occurrence_lines": hits,
                            "hint": "lengthen 'old', or pass strict=false / "
                            "--allow-near-duplicates to accept the literal-only match",
                        },
                    }
                )
                return record, raw, None
        patch = _patch_tool
        assert patch is not None  # guarded by _matching_gap()
        result = patch.patch_text(
            stage_root,
            stage_name,
            edit["old"],
            edit["new"],
            replace_all=edit["replace_all"],
            fuzzy=edit["fuzzy"],
            fuzzy_min_ratio=edit["fuzzy_min_ratio"],
            dry_run=False,
            context_lines=context_lines,
        )
        if not result.ok:
            record.update(_refusal_from_patch(result, entry["path"], edit, position, position))
            return record, raw, None
        data = result.data
        matches = int(data.get("matches") or 0)
        if edit["replace_all"]:
            problem = None
            if not edit["expect_declared"]:
                problem = (
                    "replace_all is declared without expect_occurrences: the edit would "
                    f"change an unstated number of occurrences ({matches} found)"
                )
            elif matches != edit["expect_occurrences"]:
                problem = (
                    f"the needle occurs {matches} time(s) but the transaction declared "
                    f"expect_occurrences: {edit['expect_occurrences']} — applying it would "
                    "change occurrences the caller did not name"
                )
            if problem:
                record.update(
                    {
                        "reason": REASON_OTHER_OCCURRENCES,
                        "message": f"{entry['path']}: {problem}",
                        "detail": {
                            "cause": "occurrence_count_mismatch",
                            "matches": matches,
                            "expected": edit["expect_occurrences"],
                            "occurrence_lines": data.get("occurrence_lines") or [],
                        },
                    }
                )
                return record, raw, None
        record["strategy"] = str(data.get("strategy") or "")
        record["score"] = float(data.get("score") or 1.0)
        record["matches"] = matches
        record["occurrence_lines"] = list(data.get("occurrence_lines") or [])
        record["edits"].append(
            {
                "edit_index": position + 1,
                "strategy": record["strategy"],
                "score": record["score"],
                "matches": matches,
                "occurrence_lines": record["occurrence_lines"],
                "reindent": str(data.get("reindent") or ""),
            }
        )
        inserted.append(edit["new"])

    patched = stage_target.read_bytes()
    record.update(
        {
            "ok": True,
            "after_sha256": _sha256(patched),
            "bytes_after": len(patched),
            "diff": _unified_diff(raw, patched, entry["path"], context_lines),
        }
    )
    return record, raw, patched


def _refusal_from_patch(
    result: Any, path: str, edit: dict[str, Any], position: int, _unused: int
) -> dict[str, Any]:
    """Translate a ``patch_tool`` refusal into this layer's reason vocabulary."""
    data = result.data if isinstance(result.data, dict) else {}
    kind = str(getattr(result, "error_kind", "") or "")
    message = str(getattr(result, "error", "") or "the patch was refused")
    matches = int(data.get("matches") or 0)
    if kind == "not_found":
        return {
            "reason": REASON_NOT_FOUND,
            "message": f"{path}: {message}",
            "detail": {"edit_index": position + 1, "tried": "exact, trailing-whitespace, "
                       "indentation-flexible" + (", similar" if edit["fuzzy"] else "")},
        }
    if kind == "conflict":
        candidates = data.get("occurrence_lines") or [
            item.get("line") for item in (data.get("candidates") or []) if isinstance(item, dict)
        ]
        return {
            "reason": REASON_AMBIGUOUS,
            "message": f"{path}: {message}",
            "detail": {
                "edit_index": position + 1,
                "matches": matches,
                "occurrence_lines": [item for item in candidates if item is not None],
                "hint": "lengthen 'old' until it is unique in the file",
            },
        }
    if kind == "usage":
        return {
            "reason": REASON_UNCHANGED,
            "message": f"{path}: {message}",
            "detail": {"edit_index": position + 1},
        }
    return {
        "reason": REASON_BLOCKED,
        "message": f"{path}: {message}",
        "detail": {"edit_index": position + 1, "error_kind": kind},
    }


def _unified_diff(before: bytes, after: bytes, path: str, context_lines: int) -> str:
    """A unified diff of one file, rendered here so it is never a truncated summary."""
    return "".join(
        difflib.unified_diff(
            before.decode("utf-8", "replace").splitlines(keepends=True),
            after.decode("utf-8", "replace").splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=max(0, int(context_lines)),
        )
    )


# --------------------------------------------------------------------------- #
# the transaction                                                               #
# --------------------------------------------------------------------------- #


def _spec_caps_refusal(spec: dict[str, Any], caps: EditCaps) -> dict[str, Any] | None:
    """The caps that apply to the *document* (checked before any I/O)."""
    files = len(spec["files"])
    if files > caps.max_files:
        return _issue(
            REASON_BLOCKED,
            f"the transaction names {files} files — above the {caps.max_files}-file cap",
            cap=caps.max_files,
            files=files,
        )
    edits = sum(len(entry["edits"]) for entry in spec["files"])
    if edits > caps.max_edits:
        return _issue(
            REASON_BLOCKED,
            f"the transaction carries {edits} edits — above the {caps.max_edits}-edit cap",
            cap=caps.max_edits,
            edits=edits,
        )
    return None


def _tx_bytes_refusal(
    spec: dict[str, Any], root: Path, caps: EditCaps
) -> dict[str, Any] | None:
    """The transaction's size cap, decided **before** any edit is resolved.

    A cap is policy, not a property of a needle: it must not depend on which file happens
    to contain an ambiguous match first. Paths this pre-pass cannot resolve are left to
    the per-file pass, which reports its own reason.
    """
    total = 0
    counted: list[str] = []
    for entry in spec["files"]:
        target, issue, _via = _resolve(root, entry["path"])
        if target is None or issue is not None or not target.is_file():
            continue
        try:
            total += target.stat().st_size
        except OSError:  # pragma: no cover — a race with a delete
            continue
        counted.append(entry["path"])
        if total > caps.max_tx_bytes:
            return _issue(
                REASON_BLOCKED,
                f"these files total {total} bytes — above the {caps.max_tx_bytes}-byte "
                f"transaction cap",
                cap=caps.max_tx_bytes,
                bytes=total,
                files=counted,
            )
    return None


def run_transaction(
    spec: dict[str, Any],
    workspace: str | Path,
    *,
    state_dir: str | Path,
    caps: EditCaps | None = None,
    dry_run: bool = False,
    tx_id: str = "",
    context_lines: int = 3,
    keep_on_failure: bool = False,
    allow_near_duplicates: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Plan (and, unless ``dry_run``, apply) one transaction. Never raises for a request.

    The returned record is the ``edit show`` payload: per-file results, the aggregate
    diff, the verification result, the rollback (when there was one), the snapshot and
    the caps that were in force.
    """
    started = time.time()
    caps = caps or EditCaps.from_env()
    root = Path(str(workspace)).expanduser()
    try:
        root = root.resolve()
    except OSError as exc:  # pragma: no cover — a broken mount
        return _blocked_record(str(workspace), caps, _issue(REASON_BLOCKED, f"workspace: {exc}"))
    if not root.is_dir():
        return _blocked_record(
            str(root), caps,
            _issue(REASON_BLOCKED, f"the workspace does not exist or is not a directory: {root}"),
        )
    refusal = _spec_caps_refusal(spec, caps)
    if refusal is not None:
        return _blocked_record(str(root), caps, refusal)
    gaps = _matching_gap()
    if gaps is not None:
        return _blocked_record(str(root), caps, gaps)
    oversized = _tx_bytes_refusal(spec, root, caps)
    if oversized is not None:
        record = _blocked_record(str(root), caps, oversized)
        record["caps_hit"] = ["max_tx_bytes"]
        return record

    tx_id = tx_id or new_tx_id(now)
    state_path = Path(str(state_dir)).expanduser()
    tx_root = state_path / tx_id
    stage_root = tx_root / "stage"
    pre_root = tx_root / "pre"
    stage_root.mkdir(parents=True, exist_ok=True)
    pre_root.mkdir(parents=True, exist_ok=True)

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tx_id": tx_id,
        "created_at": _now_iso(now),
        "workspace": str(root),
        "reason": spec.get("reason", ""),
        "mode": "dry_run" if dry_run else "apply",
        "dry_run": bool(dry_run),
        "strict": bool(spec.get("strict", True)) and not allow_near_duplicates,
        "verdict": VERDICT_APPLICABLE,
        "applied": False,
        "in_tree": False,
        "verified": None,
        "files": [],
        "diff": "",
        "diff_truncated": False,
        "verification": {},
        "rollback": None,
        "undo": None,
        "snapshot": {"dir": str(tx_root), "pre": [], "bytes": 0},
        "record_path": "",
        "caps": caps.as_dict(),
        "caps_hit": [],
        "limits": list(LIMITS),
        "duration_ms": 0,
        "verification_declared": spec.get("verification", {}),
    }

    total_bytes = 0
    for index, entry in enumerate(spec["files"]):
        file_record, original, patched = _stage_file(
            entry,
            root,
            stage_root,
            index,
            caps,
            strict=record["strict"],
            context_lines=context_lines,
        )
        if original is not None and patched is not None:
            total_bytes += len(original)
            if total_bytes > caps.max_tx_bytes:
                file_record.update(
                    {
                        "ok": False,
                        "reason": REASON_BLOCKED,
                        "message": (
                            f"the transaction's files total {total_bytes} bytes — above the "
                            f"{caps.max_tx_bytes}-byte transaction cap"
                        ),
                        "detail": {"cap": caps.max_tx_bytes, "bytes": total_bytes},
                    }
                )
                if "max_tx_bytes" not in record["caps_hit"]:
                    record["caps_hit"].append("max_tx_bytes")
                patched = None
        record["files"].append(file_record)
        if original is None or patched is None:
            # A refused file means the transaction is refused: nothing is published, so
            # the pre-images already staged are dropped at the end of this function.
            continue
        pre_path = pre_root / f"{index:03d}-{Path(file_record['abs_path']).name}"
        pre_path.write_bytes(original)
        record["snapshot"]["pre"].append(
            {
                "path": file_record["path"],
                "pre_file": str(pre_path),
                "before_sha256": file_record["before_sha256"],
                "after_sha256": file_record["after_sha256"],
                "mode": file_record["mode"],
                "bytes": len(original),
            }
        )

    record["snapshot"]["bytes"] = total_bytes
    record["files"] = [_payload_file(item, caps) for item in record["files"]]
    record["verdict"] = _verdict_for(record)
    aggregate = "".join(str(item.get("diff") or "") for item in record["files"])
    record["diff"], record["diff_truncated"] = _truncate(aggregate, caps.max_diff_bytes)
    record["verification"] = _planned_verification(spec.get("verification", {}))

    if record["verdict"] != VERDICT_APPLICABLE:
        # Refused before anything was published: no snapshot, no record — the dry-run is
        # the diagnostic and the tree is exactly as it was.
        _remove_tree(tx_root)
        _prune_empty_state(state_path)
        record["snapshot"] = {"dir": "", "pre": [], "bytes": total_bytes}
        record["duration_ms"] = int((time.time() - started) * 1000)
        return record

    if dry_run:
        _remove_tree(tx_root)  # a dry run leaves nothing behind, not even a snapshot
        _prune_empty_state(state_path)
        record["snapshot"] = {"dir": "", "pre": [], "bytes": total_bytes}
        record["duration_ms"] = int((time.time() - started) * 1000)
        return record

    record = _publish(record, root, stage_root, state_path, tx_id, caps, started)
    if record["verdict"] == VERDICT_APPLIED or record["verdict"] == VERDICT_APPLIED_UNVERIFIED:
        declared = spec.get("verification", {})
        verification = run_verification(declared, root, _applied_paths(record), caps=caps)
        record["verification"] = verification
        record["verified"] = verification.get("passed")
        if verification.get("executed") and not verification.get("passed"):
            if keep_on_failure:
                record["verdict"] = VERDICT_APPLIED_UNVERIFIED
                record["warnings"] = list(record.get("warnings", [])) + [
                    "the verification failed and --keep-on-failure was given: the change "
                    "is still in the tree and the transaction is NOT verified"
                ]
            else:
                record["rollback"] = _restore(record, root)
                record["verdict"] = VERDICT_ROLLED_BACK
                record["in_tree"] = False
        elif not verification.get("executed"):
            record["verdict"] = VERDICT_APPLIED_UNVERIFIED
        else:
            record["verdict"] = VERDICT_APPLIED
    _write_aggregate_diff(tx_root, aggregate)
    if record["snapshot"]["dir"]:
        record["record_path"] = str(_write_record(state_path, tx_id, record))
    prune(state_path, caps=caps)
    record["duration_ms"] = int((time.time() - started) * 1000)
    if record["record_path"]:
        _write_record(state_path, tx_id, record)
    return record


def _planned_verification(declared: dict[str, Any]) -> dict[str, Any]:
    """What a dry-run may honestly say about the check: it is declared, not run."""
    planned = {
        "kind": str(declared.get("kind") or "syntax"),
        "declared": bool(declared.get("declared", False)),
        "executed": False,
        "passed": None,
        "note": "declared; a dry-run executes nothing",
    }
    if declared.get("argv"):
        planned["argv"] = [str(item) for item in declared["argv"]]
        planned["argv_line"] = shlex.join(planned["argv"])
    return planned


def _payload_file(item: dict[str, Any], caps: EditCaps) -> dict[str, Any]:
    """The JSON-safe view of one file result (diff capped, nothing internal)."""
    view = {key: value for key, value in item.items() if not key.startswith("_")}
    diff, truncated = _truncate(str(view.get("diff") or ""), caps.max_diff_bytes)
    view["diff"], view["diff_truncated"] = diff, truncated
    return view


def _verdict_for(record: dict[str, Any]) -> str:
    """One verdict for the whole transaction, derived from the per-file results."""
    files = record["files"]
    if any(item.get("reason") == REASON_BLOCKED for item in files):
        return VERDICT_BLOCKED
    if any(item.get("reason") == REASON_DEPENDENCY_MISSING for item in files):
        return VERDICT_BLOCKED
    if all(item.get("ok") for item in files):
        return VERDICT_APPLICABLE
    return VERDICT_NOT_APPLICABLE


def _blocked_record(workspace: str, caps: EditCaps, issue: dict[str, Any]) -> dict[str, Any]:
    """A record for a transaction that never got as far as a file."""
    return {
        "schema_version": SCHEMA_VERSION,
        "tx_id": "",
        "created_at": _now_iso(),
        "workspace": workspace,
        "reason": "",
        "mode": "apply",
        "dry_run": False,
        "strict": True,
        "verdict": VERDICT_BLOCKED,
        "applied": False,
        "in_tree": False,
        "verified": None,
        "files": [],
        "diff": "",
        "diff_truncated": False,
        "verification": {},
        "rollback": None,
        "undo": None,
        "snapshot": {"dir": "", "pre": [], "bytes": 0},
        "record_path": "",
        "caps": caps.as_dict(),
        "caps_hit": [],
        "limits": list(LIMITS),
        "duration_ms": 0,
        "blocked": issue,
    }


def _applied_paths(record: dict[str, Any]) -> list[str]:
    return [str(item["path"]) for item in record["files"] if item.get("ok")]


def _publish(
    record: dict[str, Any],
    root: Path,
    stage_root: Path,
    state_path: Path,
    tx_id: str,
    caps: EditCaps,
    started: float,
) -> dict[str, Any]:
    """Publish the staged files, then run the declared verification.

    Every file is re-read and its sha256 compared with the one the plan saw: a tree that
    moved between the dry-run and the publish is a refusal (nothing is written), which is
    Claude Code's "file modified since read" rule with the hash as the witness.
    """
    written: list[dict[str, Any]] = []
    for item in record["files"]:
        if not item.get("ok"):
            continue
        target = Path(str(item["abs_path"]))
        label = str(item["path"])
        try:
            current = target.read_bytes()
        except OSError as exc:
            return _abort(record, root, written, f"{label} vanished before the publish: {exc}")
        if _sha256(current) != item["before_sha256"]:
            return _abort(
                record,
                root,
                written,
                f"{label} changed after the plan (sha256 {_sha256(current)[:12]} "
                f"≠ {item['before_sha256'][:12]}) — re-run the dry-run against the current tree",
                reason=REASON_CHANGED_SINCE_PLAN,
            )
        source = stage_root / str(item["stage_file"])
        data = source.read_bytes()
        try:
            _atomic_write(target, data, mode=int(item.get("mode") or 0) or None)
        except OSError as exc:
            return _abort(record, root, written, f"cannot publish {label}: {exc}")
        written.append(item)
        item["after_sha256"] = _sha256(target.read_bytes())
        item["published"] = True
    record["applied"] = True
    record["in_tree"] = True
    record["verdict"] = VERDICT_APPLIED
    record["duration_ms"] = int((time.time() - started) * 1000)
    return record


def _abort(
    record: dict[str, Any], root: Path, written: list[dict[str, Any]], message: str,
    reason: str = REASON_BLOCKED,
) -> dict[str, Any]:
    """Refuse a transaction mid-publish and put back everything already written."""
    record["rollback"] = _restore(record, root, only=[item["path"] for item in written])
    record["rollback"]["reason"] = reason
    record["rollback"]["message"] = message
    record["verdict"] = VERDICT_ROLLED_BACK if written else VERDICT_NOT_APPLICABLE
    record["applied"] = False
    record["in_tree"] = False
    for item in record["files"]:
        if item.get("ok") and not item.get("published"):
            item["ok"] = False
            item["reason"] = reason
            item["message"] = message
            item["detail"] = {"cause": "aborted_before_publish"}
    return record


def _restore(
    record: dict[str, Any], root: Path, *, only: list[str] | None = None
) -> dict[str, Any]:
    """Put the pre-images back (the rollback and the undo share this one implementation)."""
    restored: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    wanted = set(only) if only is not None else None
    for entry in record.get("snapshot", {}).get("pre", []):
        if wanted is not None and entry["path"] not in wanted:
            continue
        target = root / entry["path"]
        pre = Path(str(entry["pre_file"]))
        if not pre.is_file():
            failures.append({"path": entry["path"], "message": "pre-image is missing"})
            continue
        try:
            data = pre.read_bytes()
            before_restore = target.read_bytes() if target.exists() else b""
            _atomic_write(target, data, mode=int(entry.get("mode") or 0) or None)
        except OSError as exc:
            failures.append({"path": entry["path"], "message": str(exc)})
            continue
        after = target.read_bytes()
        restored.append(
            {
                "path": entry["path"],
                "restored_sha256": _sha256(after),
                "expected_sha256": entry["before_sha256"],
                "byte_identical": _sha256(after) == entry["before_sha256"],
                "sha256_before_restore": _sha256(before_restore),
            }
        )
    return {
        "at": _now_iso(),
        "restored": restored,
        "failed": failures,
        "complete": bool(restored) and not failures,
        "reason": "",
        "message": "",
    }


def _write_aggregate_diff(tx_root: Path, aggregate: str) -> None:
    if not tx_root.exists():
        return
    try:
        (tx_root / "diff.patch").write_text(aggregate, encoding="utf-8")
    except OSError:  # pragma: no cover — the snapshot is best-effort
        pass


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _prune_empty_state(state_path: Path) -> None:
    """Drop the state directory when this call left nothing in it (a dry run creates it)."""
    try:
        if state_path.is_dir() and not any(state_path.iterdir()):
            state_path.rmdir()
    except OSError:  # pragma: no cover — someone else is writing there right now
        return


def _record_path(state_path: Path, tx_id: str) -> Path:
    return state_path / f"{tx_id}.json"


def _write_record(state_path: Path, tx_id: str, record: dict[str, Any]) -> Path:
    """Persist the record next to its snapshot (one JSON document, atomic)."""
    target = _record_path(state_path, tx_id)
    state_path.mkdir(parents=True, exist_ok=True)
    _atomic_write(target, json.dumps(record, ensure_ascii=False, indent=2, default=str).encode())
    return target


# --------------------------------------------------------------------------- #
# the verification step                                                         #
# --------------------------------------------------------------------------- #


def run_verification(
    declared: dict[str, Any],
    workspace: str | Path,
    paths: list[str],
    *,
    caps: EditCaps | None = None,
) -> dict[str, Any]:
    """Run the *declared* check against the real tree, after the publish.

    ``executed`` says whether a check really ran (a suffix with no parser is reported as
    ``skipped``, never as a pass); ``passed`` is ``None`` when nothing executed. That
    split is ``verify_service``'s: an unexecuted check is not a passing one.
    """
    caps = caps or EditCaps.from_env()
    root = Path(str(workspace))
    kind = str(declared.get("kind") or "syntax")
    record: dict[str, Any] = {
        "kind": kind,
        "declared": bool(declared.get("declared", kind != "syntax")),
        "executed": False,
        "passed": None,
        "started_at": _now_iso(),
    }
    if kind == "none":
        record["note"] = "no verification was declared — the change is applied unverified"
        return record
    if kind == "syntax":
        checks: list[dict[str, Any]] = []
        for path in paths:
            checks.append(_syntax_check(root / path, path))
        record["checks"] = checks
        executed = [item for item in checks if item["checked"]]
        skipped = [item for item in checks if not item["checked"]]
        record["executed"] = bool(executed)
        record["passed"] = all(item["ok"] for item in executed) if executed else None
        record["skipped"] = [item["path"] for item in skipped]
        record["failures"] = [item for item in executed if not item["ok"]]
        if skipped:
            record["note"] = (
                f"{len(skipped)} changed file(s) have no declared parser "
                f"({', '.join(record['skipped'])}) — parse check skipped, not passed"
            )
        return record
    argv = [str(item) for item in declared.get("argv") or []]
    timeout = float(declared.get("timeout_s") or 0) or caps.verify_timeout_s
    expect = int(declared.get("expect_exit", 0))
    env_map = _child_env(root)
    started = time.time()
    record.update(
        {
            "kind": kind,
            "declared": True,
            "argv": argv,
            "argv_line": shlex.join(argv),
            "timeout_s": timeout,
            "expect_exit": expect,
            "cwd": str(root),
        }
    )
    if declared.get("targets"):
        record["targets"] = [str(item) for item in declared["targets"]]
    try:
        completed = subprocess.run(  # noqa: S603 — a declared argv, never a shell string
            argv,
            cwd=str(root),
            env=env_map,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        record.update(
            {
                "executed": False,
                "passed": None,
                "error": f"the declared command was not found: {exc}",
                "duration_ms": int((time.time() - started) * 1000),
            }
        )
        return record
    except subprocess.TimeoutExpired:
        record.update(
            {
                "executed": True,
                "passed": False,
                "timed_out": True,
                "error": f"the declared command exceeded its {timeout:g}s timeout",
                "duration_ms": int((time.time() - started) * 1000),
            }
        )
        return record
    record.update(
        {
            "executed": True,
            "exit_code": int(completed.returncode),
            "passed": int(completed.returncode) == expect,
            "stdout_tail": completed.stdout.decode("utf-8", "replace")[-4000:],
            "stderr_tail": completed.stderr.decode("utf-8", "replace")[-2000:],
            "duration_ms": int((time.time() - started) * 1000),
        }
    )
    return record


def _child_env(root: Path) -> dict[str, str]:
    """The credential-scrubbed environment for a verification command."""
    if _sandbox is None:  # pragma: no cover — guarded before any verification runs
        return {"PATH": os.environ.get("PATH", "")}
    environment, _report = _sandbox.child_environment(root)
    return environment


def _syntax_check(path: Path, label: str) -> dict[str, Any]:
    """Parse one changed file with its own parser (a real compile, not a heuristic)."""
    check: dict[str, Any] = {"path": label, "checked": False, "ok": False, "parser": ""}
    parser = SYNTAX_SUFFIXES.get(path.suffix.lower(), "")
    if not parser:
        check["note"] = f"no parser is declared for {path.suffix or 'this file type'}"
        return check
    check["parser"] = parser
    try:
        raw = path.read_bytes()
    except OSError as exc:
        check.update({"checked": True, "ok": False, "error": f"cannot read: {exc}"})
        return check
    text = raw.decode("utf-8", "replace")
    try:
        if parser == "python-compile":
            compile(text, str(path), "exec")
        elif parser == "json-parse":
            json.loads(text)
        elif parser == "toml-parse":
            tomllib.loads(text)
        else:
            import yaml  # a declared dependency of the app, imported only when asked

            yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 — the parse error *is* the result
        check.update(
            {"checked": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        )
        return check
    check.update({"checked": True, "ok": True})
    return check


# --------------------------------------------------------------------------- #
# records on disk: show / list / undo / prune                                   #
# --------------------------------------------------------------------------- #


def list_transactions(state_dir: str | Path) -> list[dict[str, Any]]:
    """Every transaction record, newest first (tx ids sort by time)."""
    state_path = Path(str(state_dir))
    if not state_path.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in sorted(state_path.glob("*.json"), reverse=True):
        record = _load_record_file(path)
        if record is not None:
            found.append(_record_summary(record))
    return found


def _load_record_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _record_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "tx_id": record.get("tx_id", ""),
        "created_at": record.get("created_at", ""),
        "verdict": record.get("verdict", ""),
        "workspace": record.get("workspace", ""),
        "reason": record.get("reason", ""),
        "files": [item.get("path") for item in record.get("files", [])],
        "applied": bool(record.get("applied")),
        "in_tree": bool(record.get("in_tree")),
        "undone": bool((record.get("undo") or {}).get("at")),
        "record_path": record.get("record_path", ""),
    }


def resolve_tx_id(state_dir: str | Path, value: str) -> str:
    """``"last"`` (or ``""``) means the newest record; anything else is taken literally."""
    text = str(value or "").strip()
    if text and text != "last":
        return text
    state_path = Path(str(state_dir))
    if not state_path.is_dir():
        return ""
    candidates = sorted(state_path.glob("*.json"), reverse=True)
    return candidates[0].stem if candidates else ""


def load_transaction(state_dir: str | Path, tx_id: str) -> dict[str, Any] | None:
    """One record by id, or ``None`` when this state dir has no such transaction."""
    if not tx_id:
        return None
    path = _record_path(Path(str(state_dir)), tx_id)
    if not path.is_file():
        return None
    record = _load_record_file(path)
    if record is None:
        return None
    record.setdefault("state_dir", str(Path(str(state_dir))))
    return record


def undo_transaction(
    tx_id: str,
    *,
    state_dir: str | Path,
    caps: EditCaps | None = None,
    force: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Restore every file a transaction touched, **byte-identically**.

    Refuses (exit 6) when a touched file changed after the apply, unless ``force`` is
    given: a restore that silently overwrote newer work would be worse than no undo.
    """
    caps = caps or EditCaps.from_env()
    state_path = Path(str(state_dir))
    resolved = resolve_tx_id(state_path, tx_id)
    record = load_transaction(state_path, resolved)
    if record is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "tx_id": str(tx_id),
            "verdict": VERDICT_NOT_APPLICABLE,
            "reason": REASON_NOT_FOUND,
            "message": f"no transaction {tx_id!r} in {state_path}",
            "error": _issue(REASON_NOT_FOUND, f"unknown transaction: {tx_id or '(none)'}"),
            "files": [],
            "undo": None,
            "caps": caps.as_dict(),
            "limits": list(LIMITS),
        }
    undo = {
        "at": _now_iso(now),
        "forced": bool(force),
        "restored": [],
        "failed": [],
        "complete": False,
    }
    if (record.get("undo") or {}).get("at") and not force:
        return _undo_refusal(
            record, caps, REASON_ALREADY_UNDONE,
            f"transaction {resolved} was already undone at "
            f"{(record.get('undo') or {}).get('at')} — pass --force to restore again",
        )
    if not record.get("in_tree"):
        return _undo_refusal(
            record, caps, REASON_NOTHING_APPLIED,
            f"transaction {resolved} has no changes in the tree "
            f"(verdict {record.get('verdict')}) — nothing to undo",
        )
    workspace = Path(str(record.get("workspace") or ""))
    if not workspace.is_dir():
        return _undo_refusal(
            record, caps, REASON_BLOCKED, f"the workspace {workspace} is gone"
        )
    drift: list[dict[str, Any]] = []
    for entry in record.get("snapshot", {}).get("pre", []):
        target = workspace / entry["path"]
        current = target.read_bytes() if target.is_file() else b""
        if _sha256(current) != entry.get("after_sha256"):
            drift.append(
                {
                    "path": entry["path"],
                    "current_sha256": _sha256(current),
                    "expected_sha256": entry.get("after_sha256", ""),
                    "exists": target.is_file(),
                }
            )
    if drift and not force:
        record["undo_check"] = drift
        return _undo_refusal(
            record, caps, REASON_MODIFIED_SINCE_APPLY,
            "these files changed after the apply; restore would discard that work "
            "(pass --force to restore anyway)",
            detail={"drift": drift},
        )
    restored = _restore(record, workspace)
    undo.update(
        {
            "restored": restored["restored"],
            "failed": restored["failed"],
            "complete": restored["complete"],
            "drift": drift,
        }
    )
    record["undo"] = undo
    record["verdict"] = VERDICT_UNDONE if restored["complete"] else VERDICT_ROLLED_BACK
    record["in_tree"] = not restored["complete"]
    _write_record(state_path, resolved, record)
    return record


def _undo_refusal(
    record: dict[str, Any], caps: EditCaps, reason: str, message: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The record shape an ``edit undo`` refusal returns (the tree is untouched)."""
    record["undo_error"] = _issue(reason, message, **(detail or {}))
    record.setdefault("caps", caps.as_dict())
    record.setdefault("limits", list(LIMITS))
    return record


def prune(state_dir: str | Path, *, caps: EditCaps | None = None) -> dict[str, Any]:
    """Keep the newest ``keep_transactions`` records **whose undo is still possible**.

    An applied transaction is never pruned (its pre-images are the only way back).
    Only records that can no longer be restored — undone, rolled back, never applied —
    are removed, oldest first. Returns what was removed, so a cap hit is never silent.
    """
    caps = caps or EditCaps.from_env()
    state_path = Path(str(state_dir))
    removed: list[str] = []
    if not state_path.is_dir():
        return {"removed": removed, "kept": 0, "cap": caps.keep_transactions}
    records = [item for item in state_path.glob("*.json")]
    records.sort(key=lambda path: path.stem, reverse=True)
    expendable = [
        path
        for path in records[int(caps.keep_transactions) :]
        if not (json.loads(path.read_text(encoding="utf-8")).get("in_tree"))
    ]
    for path in expendable:
        _remove_tree(state_path / path.stem)
        path.unlink(missing_ok=True)
        removed.append(path.stem)
    kept = [path for path in state_path.glob("*.json")]
    return {"removed": removed, "kept": len(kept), "cap": caps.keep_transactions}


def caps_report(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """The caps in force, with the environment name that overrides each one."""
    caps = EditCaps.from_env(environ)
    return {
        "caps": caps.as_dict(),
        "state_dirname": RECORD_DIRNAME,
        "verification_kinds": list(VERIFY_KINDS),
        "syntax_parsers": dict(SYNTAX_SUFFIXES),
        "refusals": {
            "conflict": sorted(CONFLICT_REASONS),
            "missing": sorted(MISSING_REASONS),
            "usage": sorted(USAGE_REASONS),
        },
        "limits": list(LIMITS),
    }


def exit_code_for(record: dict[str, Any]) -> int:
    """The one mapping from a transaction record to the CLI's exit code.

    ``dry-run`` and ``apply`` share it, so a dry run exits with the code the apply
    would exit with: 0 applied/applicable · 1 a no-op edit · 2 verification failed or a
    plan that is not applicable · 3 blocked (policy, cap, dependency) · 5 the target
    does not exist · 6 the transaction would change something the caller did not name.
    """
    undo_error = record.get("undo_error") or {}
    if undo_error:
        # A refused undo leaves the transaction's own verdict alone (it is still
        # applied): the refusal is what decides the exit code, not the verdict.
        return _reasons_code([str(undo_error.get("reason") or "")])
    verdict = str(record.get("verdict") or "")
    if verdict in (VERDICT_APPLICABLE, VERDICT_APPLIED, VERDICT_APPLIED_UNVERIFIED):
        return 0
    if verdict == VERDICT_UNDONE:
        return 0
    if verdict == VERDICT_BLOCKED:
        return 3
    if verdict == VERDICT_ROLLED_BACK:
        return 2 if (record.get("verification") or {}).get("executed") else 7
    reasons = [str(item.get("reason") or "") for item in record.get("files") or []]
    for key in ("error", "blocked"):
        issue = record.get(key) or {}
        reasons.append(str(issue.get("reason") or ""))
    return _reasons_code(reasons)


def _reasons_code(reasons: list[str]) -> int:
    """A refusal's class, by the vocabulary the reasons come from."""
    if any(
        reason in (REASON_BLOCKED, REASON_DEPENDENCY_MISSING, REASON_SNAPSHOT_MISSING)
        for reason in reasons
    ):
        return 3
    if any(reason in CONFLICT_REASONS for reason in reasons):
        return 6
    if any(reason in USAGE_REASONS for reason in reasons):
        return 1
    if any(reason in MISSING_REASONS for reason in reasons):
        return 5
    return 2


def render_text(record: dict[str, Any]) -> str:
    """The human view: one honest block per verb, never the JSON (the CLI's job)."""
    lines: list[str] = []
    tx_id = record.get("tx_id") or "(none)"
    lines.append(
        f"edit {tx_id} — {record.get('verdict')}"
        + (" (dry run: nothing was written)" if record.get("dry_run") else "")
    )
    if record.get("blocked"):
        lines.append(f"  blocked: {record['blocked'].get('message', '')}")
    if record.get("reason"):
        lines.append(f"  reason: {record['reason']}")
    for item in record.get("files") or []:
        mark = "ok" if item.get("ok") else "fail"
        lines.append(
            f"  {mark:>4} {item.get('path')} — "
            + (
                f"{item.get('strategy')} match, {item.get('matches')} occurrence(s), "
                f"{item.get('bytes_before')}→{item.get('bytes_after')} bytes"
                if item.get("ok")
                else f"{item.get('reason')}: {item.get('message')}"
            )
        )
    for failure in (record.get("verification") or {}).get("failures") or []:
        lines.append(f"  verify {failure.get('path')}: {failure.get('error')}")
    verification = record.get("verification") or {}
    if verification.get("kind"):
        lines.append(
            f"  verification: {verification.get('kind')} "
            f"executed={verification.get('executed')} passed={verification.get('passed')}"
        )
    for entry in (record.get("undo") or {}).get("restored") or []:
        lines.append(
            f"  restored {entry.get('path')} byte-identical={entry.get('byte_identical')}"
        )
    if record.get("undo_error"):
        lines.append(f"  undo refused: {record['undo_error'].get('message')}")
    if record.get("diff"):
        lines.append("  diff:")
        lines.extend(f"    {line}" for line in str(record["diff"]).splitlines())
    return "\n".join(lines)


__all__ = [
    "CONFLICT_REASONS",
    "LIMITS",
    "MISSING_REASONS",
    "RECORD_DIRNAME",
    "REASON_AMBIGUOUS",
    "REASON_BLOCKED",
    "REASON_CASCADE",
    "REASON_CHANGED_SINCE_PLAN",
    "REASON_DEPENDENCY_MISSING",
    "REASON_NOT_FOUND",
    "REASON_OTHER_OCCURRENCES",
    "REASON_UNCHANGED",
    "SCHEMA_VERSION",
    "SYNTAX_SUFFIXES",
    "USAGE_REASONS",
    "VERDICTS",
    "VERIFY_KINDS",
    "EditCaps",
    "caps_report",
    "exit_code_for",
    "list_transactions",
    "load_transaction",
    "near_duplicate_regions",
    "new_tx_id",
    "normalize_verification",
    "parse_spec",
    "prune",
    "render_text",
    "resolve_tx_id",
    "run_transaction",
    "run_verification",
    "undo_transaction",
]
