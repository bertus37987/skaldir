"""Real-world verification: "can a user actually use this?" — not "does the code exist".

This is the VERIFY layer's service half (skill ``gods-voice``, MVP/12 "the blocked list
IS the work list": *no command checks the real workspace; ``eval`` is in-process only*).
It answers one question per goal criterion by **executing something against the real
workspace** and recording what happened:

* **plan** — derive, from the goal's criteria, which real check would certify each one
  (the project's own suite, a declared build/command/end-to-end flow, or a declared
  artifact to inspect). A criterion with no certifiable check is reported as such; it
  is never silently dropped.
* **run** — execute those checks for real: start the declared command / build / flow in
  the workspace, capture the exit code and output, inspect the artifacts it produced
  (existence, size, sha256), and record one evidence record per criterion with
  timestamps and hashes.
* **verdict** — ``PASS`` / ``FAIL`` / ``UNKNOWN``. Only a run in which **every blocking
  criterion has an executed check that passed** may be ``PASS``. A check that could not
  be executed at all is ``UNKNOWN``, and ``UNKNOWN`` is never promoted to ``PASS``
  (:func:`verdict_for` is the only place a verdict is computed).

Everything the runner executes comes from a declaration the workspace (or the operator)
supplies — never from a heuristic guess:

* ``pytest`` / ``tests`` / ``test`` → the project's own suite through
  :func:`skaldir.completion.execute_check` (the project interpreter, ``.venv/bin/python``).
* ``run`` / ``build`` / ``ui_e2e`` / ``api`` / ``persistence_restart`` → the canonical
  runner in :mod:`skaldir.completion`, i.e. ``.godsv/checks.json`` or ``SKALDIR_CHECK_*_CMD``,
  executed **sandboxed** through :func:`skaldir.gates.run_declared_check`; without a
  declaration the check is honestly ``not-certifiable`` → ``UNKNOWN``.
* an explicit ``argv`` in the plan file → executed sandboxed as well, then judged against
  the declared expectations (``expect_exit``, ``expect_stdout``, ``require_pass_line``).
* an ``artifact`` criterion → the path is really inspected (exists? size? sha256?).

The plan file is ``<workspace>/.godsv/verify.json``::

    {
      "goal": "ship the widget",
      "criteria": [
        {"criterion": "the test suite passes", "check": "pytest"},
        {"criterion": "the app builds", "check": "build"},
        {"criterion": "the CLI reports its version",
         "argv": ["skaldir", "--version"], "expect_stdout": "skaldir"},
        {"criterion": "the bundle exists", "check": "artifact",
         "paths": ["dist/app.asar"], "min_bytes": 1024},
        {"criterion": "the UI round-trip works in English", "check": "ui_e2e"}
      ]
    }

Evidence is persisted under ``<state dir>/verify`` (``runs/<run_id>.json`` plus one line
per criterion in ``evidence.jsonl``) so a restart resumes instead of re-deriving, and so
a later reader can re-hash what was claimed: every evidence record carries its own
``evidence_sha256``, the run carries ``plan_sha256`` and the ``workspace_digest`` it was
produced under. :func:`run_freshness` re-computes that digest so a ``PASS`` from a
workspace that has changed since is marked stale instead of being quoted as proof.

No dependency outside the standard library and this package.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .env import child_env, getenv
from .subagent_briefs import (
    DEFAULT_MAX_BRIEF_CHARS,
    EvidenceBrief,
    EvidenceBriefError,
    build_evidence_brief,
)
from .subagent_briefs import (
    DEFAULT_TAIL_LINES as DEFAULT_BRIEF_TAIL_LINES,
)
from .subagent_briefs import (
    brief_tokens as _brief_tokens,
)
from .subagent_briefs import (
    transcript_tokens as _transcript_tokens,
)
from .subagent_roles import ROLE_BUDGETS, ROLE_VERIFIER

# --------------------------------------------------------------------------- #
# vocabulary                                                                    #
# --------------------------------------------------------------------------- #

#: The only three verdicts this layer knows. ``UNKNOWN`` is a first-class outcome.
VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_UNKNOWN = "UNKNOWN"

#: Per-criterion outcome (the run's verdict is derived from these, never the other way).
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"

#: How a criterion is really checked.
KIND_SUITE = "suite"  # the project's own test runner
KIND_COMMAND = "command"  # a declared build/command/end-to-end flow
KIND_ARTIFACT = "artifact"  # an artifact in the workspace, inspected for real

CHECK_KINDS = (KIND_SUITE, KIND_COMMAND, KIND_ARTIFACT)

#: Canonical check ids that only ever run from a declaration (kept in sync with
#: ``skaldir.completion.CANONICAL_CHECKS`` and its ``_NAMED_CHECKS`` env names).
DECLARED_CHECK_ENV: dict[str, str] = {
    "run": "SKALDIR_CHECK_RUN_CMD",
    "ui_e2e": "SKALDIR_CHECK_UI_CMD",
    "api": "SKALDIR_CHECK_API_CMD",
    "build": "SKALDIR_CHECK_BUILD_CMD",
    "persistence_restart": "SKALDIR_CHECK_RESTART_CMD",
}

#: Check ids that run the project's own suite.
SUITE_CHECKS = ("pytest", "tests", "test")

#: Last-resort derivation: a criterion whose own wording names a canonical check that
#: the workspace **really declares** (or that always has a runner, like the suite).
#: Used only when ``plan_verification`` derives nothing (it has no branch for
#: build/run wording), and for a declared check only with a declaration in hand — so
#: the plan never points at a check that cannot run here. Matching is by word prefix
#: (``\bbuild`` matches "builds"/"building"), which is what makes the hints useful.
TEXT_CHECK_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pytest", ("test suite", "tests", "pytest", "unittest", "test run")),
    ("build", ("build", "compil", "packag", "bundl", "bundle")),
    ("ui_e2e", ("ui", "browser", "desktop", "window", "click", "screen", "gui")),
    ("api", ("api", "endpoint", "http", "rest")),
    ("persistence_restart", ("restart", "persist", "reload", "surviv")),
    ("run", ("run", "launch", "start", "execut")),
)

#: Where a workspace declares its checks and (optionally) its whole verification plan.
CHECKS_FILENAME = Path(".godsv") / "checks.json"
PLAN_FILENAME = Path(".godsv") / "verify.json"

DEFAULT_TIMEOUT = 600
MAX_OUTPUT_TAIL = 1500
MAX_HASH_BYTES = 64 * 1024 * 1024
MAX_TREE_ENTRIES = 5000

PASS_REQUIREMENT = (
    "PASS requires every blocking criterion to have an executed check with status "
    "'passed'; an unexecuted/not-certifiable check is UNKNOWN and never a PASS"
)

_EXIT_RE = re.compile(r"exit\s+(-?\d+)\s*:", re.I)


def now_iso() -> str:
    """Local ISO-8601 timestamp with milliseconds (the envelope's own format)."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _wall_to_iso(seconds: float) -> str:
    return datetime.fromtimestamp(float(seconds)).astimezone().isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------- #
# hashing helpers — every claim carries the hash it was produced from           #
# --------------------------------------------------------------------------- #


def sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def canonical_json(payload: Any) -> str:
    """Byte-stable JSON for hashing (sorted keys, no whitespace)."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str,
                      separators=(",", ":"))


def sha256_file(path: str | Path, *, limit: int = MAX_HASH_BYTES) -> tuple[str, str]:
    """``(sha256, scope)`` of a file — ``scope`` says honestly what was hashed."""
    digest = hashlib.sha256()
    read = 0
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                read += len(chunk)
                if read >= int(limit):
                    return digest.hexdigest(), f"first {int(limit)} bytes"
    except OSError as exc:
        return "", f"unreadable: {type(exc).__name__}: {exc}"
    return digest.hexdigest(), "whole file"


def _tree_facts(root: Path) -> tuple[int, int]:
    """Bounded ``(entries, bytes)`` walk of a directory."""
    entries = 0
    total = 0
    for base, _dirs, files in os.walk(root):
        for name in files:
            entries += 1
            try:
                total += (Path(base) / name).stat().st_size
            except OSError:
                continue
            if entries >= MAX_TREE_ENTRIES:
                return entries, total
    return entries, total


def artifact_record(path: str | Path, *, root: str | Path | None = None) -> dict:
    """Inspect one declared artifact for real: existence, kind, size, sha256, mtime."""
    raw = Path(path)
    base = Path(root) if root else None
    candidate = raw if raw.is_absolute() else (base / raw if base else raw)
    shown = str(raw)
    record: dict[str, Any] = {"path": shown, "resolved": str(candidate), "exists": False}
    try:
        stat = candidate.stat()
    except OSError as exc:
        record["reason"] = f"{type(exc).__name__}: {exc}"
        return record
    record["exists"] = True
    record["kind"] = "dir" if candidate.is_dir() else "file"
    record["size_bytes"] = int(stat.st_size)
    record["mtime"] = _wall_to_iso(stat.st_mtime)
    if candidate.is_file():
        digest, scope = sha256_file(candidate)
        record["sha256"] = digest
        record["sha256_scope"] = scope
    else:
        entries, total = _tree_facts(candidate)
        record["entries"] = int(entries)
        record["bytes_recursive"] = int(total)
    return record


#: Bounds for the workspace digest: enough to see real edits, small enough to stay cheap.
DIGEST_MAX_FILES = 200
DIGEST_MAX_FILE_BYTES = 8 * 1024 * 1024


def workspace_digest(workspace: str | Path) -> dict:
    """The digest a run's evidence is bound to: HEAD + the working tree's content.

    Path *sets* are not enough — editing an already-dirty or untracked file leaves
    ``git status`` unchanged, and that edit is exactly what must invalidate a PASS.
    So every changed/untracked file contributes its size, mtime and (bounded) content
    hash. A workspace that is not a repository has *no* digest — reported as
    ``digest: null`` with a reason, never as an empty-but-equal digest.
    """
    root = Path(workspace)
    out: dict[str, Any] = {"path": str(root), "exists": root.is_dir(), "digest": None,
                           "reason": "", "git": False}
    if not root.is_dir():
        out["reason"] = "workspace does not exist"
        return out
    try:
        head = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
        status = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        out["reason"] = f"{type(exc).__name__}: {exc}"
        return out
    if head.returncode != 0 or status.returncode != 0:
        out["reason"] = "not a git repository — no workspace digest"
        return out
    out["git"] = True
    out["head"] = head.stdout.strip()
    changed = _changed_paths(status.stdout)
    out["dirty_files"] = len(changed)
    entries: list[dict] = []
    for relative in changed:
        entry = _digest_entry(root, relative)
        entries.append(entry)
        if len(entries) >= DIGEST_MAX_FILES:
            out["truncated_at"] = DIGEST_MAX_FILES
            break
    out["hashed_files"] = len([item for item in entries if item.get("sha256")])
    out["digest"] = sha256_text(canonical_json({
        "head": out["head"],
        "status": status.stdout,
        "entries": entries,
    }))
    return out


def _changed_paths(porcelain: str) -> list[str]:
    """The paths ``git status --porcelain`` names (renames reduced to their new path)."""
    paths: list[str] = []
    for line in str(porcelain or "").splitlines():
        text = line[3:].strip() if len(line) > 3 else ""
        if " -> " in text:
            text = text.split(" -> ", 1)[1].strip()
        text = text.strip('"')
        if text and text not in paths:
            paths.append(text)
    return paths


def _digest_entry(root: Path, relative: str) -> dict:
    """Size, mtime and (bounded) content hash of one changed path."""
    candidate = root / relative
    entry: dict[str, Any] = {"path": relative}
    try:
        stat = candidate.stat()
    except OSError:
        entry["missing"] = True
        return entry
    entry["size_bytes"] = int(stat.st_size)
    entry["mtime_ns"] = int(stat.st_mtime_ns)
    if candidate.is_file():
        digest, scope = sha256_file(candidate, limit=DIGEST_MAX_FILE_BYTES)
        entry["sha256"] = digest
        entry["sha256_scope"] = scope
    return entry


def run_freshness(record: dict, workspace: str | Path | None = None) -> dict:
    """Is a recorded run still about the workspace it was produced in?

    The basis is the **post-run** digest when the run recorded one: the checks
    themselves may write into the workspace (a build writes its output, pytest writes
    caches), and *that* write is not a change that invalidates the run. Anything that
    changed after the run ended does.
    """
    pre = (record or {}).get("workspace_digest") or {}
    post = (record or {}).get("workspace_digest_after") or {}
    basis = post if post.get("digest") else pre
    recorded = str(basis.get("digest") or "")
    root = workspace or (record or {}).get("workspace") or ""
    current = workspace_digest(root) if root else {"digest": None, "reason": "no workspace"}
    where = "post-run" if basis is post and post.get("digest") else "pre-run"
    if not recorded:
        detail = str(pre.get("reason") or "the run carries no workspace digest")
        return {"fresh": None, "citable": False, "basis": where,
                "reason": f"{detail} — freshness unknown"}
    if not current.get("digest"):
        unavailable = current.get("reason") or "unknown"
        return {"fresh": None, "citable": False, "basis": where,
                "reason": f"workspace digest unavailable now ({unavailable})"}
    if recorded == current["digest"]:
        return {"fresh": True, "citable": True, "basis": where, "reason": "",
                "digest": current["digest"]}
    return {"fresh": False, "citable": False, "basis": where, "digest": current["digest"],
            "reason": f"the workspace changed since this run (digest compared {where}) — "
                      "its PASS is not citable"}


# --------------------------------------------------------------------------- #
# the plan                                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class Criterion:
    """One goal criterion and the real check that is supposed to certify it."""

    text: str
    criterion_id: str = ""
    check_id: str = ""
    kind: str = ""
    source: str = ""  # declared | contract | argument | derived
    blocking: bool = True
    argv: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    expect_exit: int = 0
    expect_stdout: str = ""
    require_pass_line: bool = False
    min_bytes: int = 0
    declared_command: str = ""
    reason: str = ""

    @property
    def planned(self) -> bool:
        return bool(self.kind) and (bool(self.check_id) or bool(self.argv))

    @property
    def certifiable(self) -> bool:
        """Can this criterion be checked *here and now* — runner/declaration in hand?"""
        if not self.planned:
            return False
        if self.kind == KIND_SUITE:
            return True
        if self.kind == KIND_ARTIFACT:
            return bool(self.paths)
        if self.kind == KIND_COMMAND:
            return bool(self.argv or self.declared_command)
        return False

    def as_dict(self) -> dict:
        return {
            "criterion": self.text,
            "criterion_id": self.criterion_id,
            "check_id": self.check_id,
            "kind": self.kind,
            "source": self.source,
            "blocking": bool(self.blocking),
            "argv": list(self.argv),
            "paths": list(self.paths),
            "expect_exit": int(self.expect_exit),
            "expect_stdout": self.expect_stdout,
            "require_pass_line": bool(self.require_pass_line),
            "min_bytes": int(self.min_bytes),
            "declared_command": self.declared_command,
            "planned": self.planned,
            "certifiable": self.certifiable,
            "reason": self.reason,
        }


@dataclass
class Plan:
    """What would be verified, and whether every criterion can be verified at all."""

    goal: str = ""
    workspace: str = ""
    revision: int = 0
    criteria: list[Criterion] = field(default_factory=list)
    source: str = ""
    plan_file: str = ""
    plan_file_reason: str = ""
    workspace_exists: bool = True
    digest: dict = field(default_factory=dict)
    created_at: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def unplannable(self) -> list[Criterion]:
        return [item for item in self.criteria if not item.planned]

    @property
    def uncertifiable(self) -> list[Criterion]:
        return [item for item in self.criteria if not item.certifiable]

    @property
    def plannable(self) -> bool:
        return bool(self.criteria) and not self.unplannable

    @property
    def certifiable(self) -> bool:
        """Would this plan be able to certify the goal today?

        A plan over a workspace that does not exist can never certify anything —
        ``workspace_exists`` is part of the answer, not a footnote.
        """
        return bool(self.criteria) and self.workspace_exists and not self.uncertifiable

    def as_dict(self) -> dict:
        return {
            "goal": self.goal,
            "workspace": self.workspace,
            "revision": int(self.revision),
            "source": self.source,
            "plan_file": self.plan_file,
            "plan_file_reason": self.plan_file_reason,
            "workspace_exists": bool(self.workspace_exists),
            "workspace_digest": self.digest,
            "created_at": self.created_at or now_iso(),
            "criteria": [item.as_dict() for item in self.criteria],
            "checks": sorted({item.check_id for item in self.criteria if item.check_id}),
            "criteria_count": len(self.criteria),
            "unplannable": [item.text for item in self.unplannable],
            "uncertifiable": [item.text for item in self.uncertifiable],
            "blocking_count": len([item for item in self.criteria if item.blocking]),
            "plannable": self.plannable,
            "certifiable": self.certifiable,
            "pass_requirement": PASS_REQUIREMENT,
            "warnings": list(self.warnings),
        }


def load_plan_file(workspace: str | Path = ".", plan_file: str | Path = "") -> tuple[dict, str]:
    """Read the workspace's verification plan (``.godsv/verify.json``).

    Returns ``(data, reason)``; an absent or unreadable file yields ``({}, reason)`` —
    the caller then falls back to derived checks instead of pretending a plan existed.
    """
    path = Path(plan_file) if plan_file else Path(workspace) / PLAN_FILENAME
    if not path.is_file():
        return {}, f"no plan file at {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"plan file unreadable ({type(exc).__name__}: {exc})"
    if not isinstance(data, dict):
        return {}, f"plan file is {type(data).__name__}, not an object"
    return data, ""


def declared_command(workspace: str | Path, check_id: str) -> tuple[str, str]:
    """How this workspace declares ``check_id`` — ``(command, source)`` or ``("", reason)``.

    Two declaration sources, exactly as the completion runner reads them:
    ``<workspace>/.godsv/checks.json`` and the check's ``SKALDIR_CHECK_*_CMD`` environment
    variable. Nothing is invented: no declaration means the check cannot be certified.
    """
    check_id = str(check_id)
    base = Path(workspace)
    if check_id in DECLARED_CHECK_ENV:
        env = str(getenv(DECLARED_CHECK_ENV[check_id], "") or "").strip()
        if env:
            return env, f"${DECLARED_CHECK_ENV[check_id]}"
    candidates = [check_id, "run"] if check_id in SUITE_CHECKS else [check_id]
    for candidate in candidates:
        path = base / CHECKS_FILENAME
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        spec = data.get(candidate)
        if isinstance(spec, dict) and spec.get("argv"):
            argv = [str(item) for item in spec["argv"]]
            return " ".join(argv), f"{CHECKS_FILENAME}:{candidate}"
    return "", f"no declaration for check '{check_id}' (add it to {CHECKS_FILENAME} or set " \
               f"{DECLARED_CHECK_ENV.get(check_id, 'the SKALDIR_CHECK_*_CMD variable')})"


def _as_argv(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            import shlex

            return tuple(shlex.split(value))
        except ValueError:
            return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item).strip())
    return ()


def _kind_for(check_id: str) -> str:
    if check_id in SUITE_CHECKS:
        return KIND_SUITE
    return KIND_COMMAND


def _check_declaration(value: Any) -> tuple[str, tuple[str, ...]]:
    """Read a declared check: ``(check_id, argv)``.

    A declaration is either a **check id** (``"pytest"``, ``"api"``, a registered tool
    name) or an explicit **command vector** (``["python", "spec/check_api.py"]``). The
    vector form is what a criteria document naturally carries — and the earlier reader
    ran ``str()`` over it, which turned the vector into the *name*
    ``"['/…/python', 'spec/check_api.py']"``: a check no registry knows, with an empty
    argv, so every criterion it declared came back ``UNKNOWN`` forever while the record
    looked like a normal plan. A vector is read as a vector, or it is refused; it is
    never stringified into a name.
    """
    if value is None or value == "":
        return "", ()
    if isinstance(value, (list, tuple)):
        argv = tuple(str(item) for item in value if str(item).strip())
        if not argv:
            return "", ()
        return "command", argv
    return str(value).strip(), ()


def _criterion_from_contract(item: Any) -> dict:
    """Normalise a completion-contract DoD item (``DodItem`` or dict)."""
    if isinstance(item, dict):
        check, argv = _check_declaration(
            item.get("verification")
            or item.get("check")
            or item.get("check_id")
            or item.get("check_argv")
        )
        declared_argv = _as_argv(item.get("argv") or item.get("check_argv"))
        return {
            "criterion": str(item.get("text") or item.get("criterion") or ""),
            "criterion_id": str(item.get("id") or item.get("criterion_id") or ""),
            "blocking": bool(item.get("blocking", True)),
            "check": check,
            "argv": declared_argv or argv,
            "source": "contract",
        }
    check, argv = _check_declaration(getattr(item, "verification", "") or "")
    return {
        "criterion": str(getattr(item, "text", "") or ""),
        "criterion_id": str(getattr(item, "id", "") or ""),
        "blocking": bool(getattr(item, "blocking", True)),
        "check": check,
        "argv": argv,
        "source": "contract",
    }


def _as_paths(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item).strip())
    return ()


def _criterion_from_plan_file(item: Any) -> dict:
    """Normalise one ``criteria[]`` entry of ``.godsv/verify.json``."""
    if not isinstance(item, dict):
        return {"criterion": str(item), "source": "plan-file"}
    return {
        "criterion": str(item.get("criterion") or item.get("text") or ""),
        "criterion_id": str(item.get("id") or item.get("criterion_id") or ""),
        "blocking": bool(item.get("blocking", True)),
        "check": str(item.get("check") or ""),
        "argv": _as_argv(item.get("argv")),
        "paths": _as_paths(item.get("paths")),
        "expect_exit": int(item.get("expect_exit", 0) or 0),
        "expect_stdout": str(item.get("expect_stdout") or ""),
        "require_pass_line": bool(item.get("require_pass_line", False)),
        "min_bytes": int(item.get("min_bytes", 0) or 0),
        "source": "plan-file",
    }


def _find_plan_entry(entries: Sequence[dict], *, text: str, criterion_id: str) -> dict | None:
    """Match a criterion against the plan file by id, then by text."""
    for entry in entries:
        entry_id = str(entry.get("criterion_id") or "")
        if criterion_id and entry_id and entry_id == criterion_id:
            return entry
    for entry in entries:
        if entry.get("criterion") and str(entry["criterion"]).strip() == text.strip():
            return entry
    for entry in entries:
        if text.strip() and str(entry.get("criterion", "")).strip().lower() == text.strip().lower():
            return entry
    return None


def build_plan(
    criteria: Iterable[Any] = (),
    *,
    goal: str = "",
    workspace: str | Path = ".",
    plan_file: str | Path = "",
    revision: int = 0,
) -> Plan:
    """Derive the real check for every goal criterion.

    ``criteria`` may hold plain strings, completion-contract DoD items or plan-file
    entries; when it is empty the workspace's own plan file supplies them. For each
    criterion the check comes from, in order: the plan file's own declaration, the
    contract item's ``verification`` id, or :func:`skaldir.completion.plan_verification`
    over the criterion text. A criterion without a check stays in the plan as
    *unplannable* — visible, blocking the plan, never dropped.
    """
    root = Path(workspace)
    data, plan_reason = load_plan_file(root, plan_file)
    file_entries = [
        _criterion_from_plan_file(item) for item in (data.get("criteria") or [])
        if isinstance(item, (dict, str))
    ]
    supplied = list(criteria)
    source = "argument"
    if not supplied and file_entries:
        supplied = [entry["criterion"] for entry in file_entries if entry.get("criterion")]
        source = "plan-file"
        # keep the declarations attached: re-resolve each supplied text against the file
    elif not supplied:
        source = "none"

    items: list[dict] = []
    for raw in supplied:
        if isinstance(raw, str):
            items.append({"criterion": raw, "source": source if source != "none" else "argument"})
        elif isinstance(raw, dict) and ("text" in raw or "id" in raw):
            items.append(_criterion_from_contract(raw))
        elif isinstance(raw, dict):
            items.append(_criterion_from_plan_file(raw))
        else:
            entry = _criterion_from_contract(raw)
            entry["source"] = "contract" if source == "none" else source
            items.append(entry)

    plan = Plan(
        goal=str(goal or data.get("goal") or ""),
        workspace=str(root),
        revision=int(revision or 0),
        source=source,
        plan_file=str(Path(plan_file) if plan_file else root / PLAN_FILENAME),
        plan_file_reason=plan_reason,
        workspace_exists=root.is_dir(),
        digest=workspace_digest(root),
        created_at=now_iso(),
    )
    if not root.is_dir():
        plan.warnings.append(f"workspace does not exist: {root}")
    if plan_reason:
        plan.warnings.append(plan_reason)

    tool_names = _testing_tool_names(root)
    for item in items:
        text = str(item.get("criterion") or "").strip()
        if not text:
            continue
        file_entry = _find_plan_entry(
            file_entries, text=text, criterion_id=str(item.get("criterion_id") or "")
        )
        # only empty values are dropped: `blocking: false` and `expect_exit: 0` are
        # real declarations and must survive the merge
        overrides = {
            key: value for key, value in item.items()
            if value != "" and value != () and value != []
        }
        merged = {**file_entry, **overrides} if file_entry else dict(overrides)
        criterion = _criterion_from_merged(merged, text=text, workspace=root, tools=tool_names)
        plan.criteria.append(criterion)

    if not plan.criteria:
        plan.warnings.append(
            "no criterion is planned — pass --criterion/--goal, declare .godsv/verify.json, "
            "or record a goal with `skaldir completion begin`"
        )
    return plan


def _criterion_from_merged(merged: dict, *, text: str, workspace: Path, tools: Sequence[str]):
    """One criterion, with the check resolved from declaration → contract → derivation."""
    check_id = str(merged.get("check") or "").strip()
    argv = tuple(merged.get("argv") or ())
    paths = tuple(str(path) for path in (merged.get("paths") or ()))
    source = str(merged.get("source") or "derived")
    reason = ""
    kind = ""

    if check_id in ("artifact", "file", "files") or (paths and not argv and not check_id):
        kind = KIND_ARTIFACT
        check_id = "artifact"
        reason = (
            f"inspect {len(paths)} declared artifact(s) in the workspace"
            if paths
            else "artifact criterion without a declared path"
        )
    elif check_id in ("command", "argv") or (argv and not check_id):
        kind = KIND_COMMAND
        check_id = check_id or "command"
        reason = "explicit argv declared"
    elif check_id in SUITE_CHECKS:
        kind = KIND_SUITE
        reason = f"declared check '{check_id}' (the project's own runner)"
    elif check_id:
        kind = _kind_for(check_id)
        reason = f"declared check '{check_id}'"
    else:
        from .completion import plan_verification

        derived = str(plan_verification(text, tool_names=tools) or "")
        hint = _declared_hint(text, workspace) if not derived else ""
        if derived:
            check_id = derived
            kind = _kind_for(derived)
            reason = f"derived from the criterion text (plan_verification -> '{derived}')"
        elif hint:
            check_id = hint
            kind = _kind_for(hint)
            reason = (
                f"the criterion names '{hint}' and the workspace declares that check "
                f"(text hint, declaration in hand)"
            )
        else:
            reason = "no check is derivable from this criterion text"

    declared = ""
    if kind == KIND_COMMAND and argv:
        declared = " ".join(argv)
    elif kind == KIND_COMMAND and check_id:
        declared, why = declared_command(workspace, check_id)
        if not declared:
            reason = f"{reason}; {why}"

    return Criterion(
        text=text,
        criterion_id=str(merged.get("criterion_id") or ""),
        check_id=check_id,
        kind=kind,
        source=source,
        blocking=bool(merged.get("blocking", True)),
        argv=argv,
        paths=paths,
        expect_exit=int(merged.get("expect_exit", 0) or 0),
        expect_stdout=str(merged.get("expect_stdout") or ""),
        require_pass_line=bool(merged.get("require_pass_line", False)),
        min_bytes=int(merged.get("min_bytes", 0) or 0),
        declared_command=declared,
        reason=reason,
    )


def _declared_hint(text: str, workspace: Path) -> str:
    """A canonical check the criterion's own wording names **and** that can run here.

    The suite always has a runner; every other hint is accepted only with a
    declaration in hand, so a plan never points at a check that could not run here.
    False positives cost one real, harmless check run; they can never produce a pass
    the check did not earn.
    """
    lowered = str(text or "").lower()
    for check_id, words in TEXT_CHECK_HINTS:
        if not any(re.search(rf"\b{re.escape(word)}", lowered) for word in words):
            continue
        if check_id in SUITE_CHECKS:
            return check_id
        command, _why = declared_command(workspace, check_id)
        if command:
            return check_id
    return ""


def _testing_tool_names(workspace: Path) -> list[str]:
    """Registered testing-tool names for this workspace — lazy, failure-tolerant."""
    try:
        from .completion import _registered_check_names

        return list(_registered_check_names(workspace))
    except Exception:  # noqa: BLE001 — a plan must survive a broken registry
        return []


def contract_criteria(runtime: str | Path, session_id: str = "") -> tuple[str, list[dict]]:
    """The recorded goal and its DoD items from the completion contract.

    Returns ``("", [])`` when no contract is readable — the caller reports that as
    blocked instead of inventing criteria.
    """
    try:
        from .completion import CompletionStore

        state = CompletionStore(runtime, session_id=str(session_id or "")).state()
    except Exception:  # noqa: BLE001 — an unreadable contract is a block, not a crash
        return "", []
    if not isinstance(state, dict):
        return "", []
    if str(state.get("corrupt") or "").lower() in ("true", "1"):
        return "", []
    dod = [_criterion_from_contract(item) for item in (state.get("dod") or [])]
    dod = [item for item in dod if item.get("criterion")]
    return str(state.get("goal") or ""), dod


# --------------------------------------------------------------------------- #
# the run                                                                       #
# --------------------------------------------------------------------------- #


def _tail(text: str, limit: int = MAX_OUTPUT_TAIL) -> str:
    text = str(text or "")
    return text if len(text) <= limit else "…" + text[-limit:]


def _exit_from_detail(detail: str) -> int | None:
    match = _EXIT_RE.search(str(detail or ""))
    return int(match.group(1)) if match else None


def _run_declared(argv: Sequence[str], *, workspace: Path, timeout: float,
                  label: str, read_only_workspace: bool = False) -> tuple[str, int | None, str]:
    """Execute a declared command sandboxed; ``(status, exit_code, output)``."""
    from .gates import NO_SANDBOX_EXIT, TIMEOUT_EXIT, run_declared_check

    workdir = workspace if workspace.is_dir() else Path(".")
    code, output, sandboxed = run_declared_check(
        list(argv),
        workdir=workdir,
        timeout=float(timeout),
        extra_env=child_env({"CHECK_ID": str(label), "WORKSPACE": str(workdir)}),
        read_only_workspace=read_only_workspace,
    )
    if code == NO_SANDBOX_EXIT and not sandboxed:
        return STATUS_UNKNOWN, None, str(output)
    if code == TIMEOUT_EXIT:
        return STATUS_FAILED, None, f"timeout after {float(timeout):.0f}s: {output}"
    return "ran", int(code), str(output)


def _expectation_verdict(criterion: Criterion, exit_code: int | None,
                         output: str) -> tuple[str, str]:
    """Judge a really executed command against the declared expectations."""
    if exit_code is None:
        return STATUS_FAILED, "no exit code — the check did not complete"
    if criterion.require_pass_line:
        from .completion import check_verdict

        status, why = check_verdict(criterion.check_id or "run", exit_code, output)
        return (STATUS_PASSED if status == "passed" else STATUS_FAILED), \
            why or "explicit PASS verdict line"
    if int(exit_code) != int(criterion.expect_exit):
        return STATUS_FAILED, f"exit {int(exit_code)}, expected {int(criterion.expect_exit)}"
    if criterion.expect_stdout and criterion.expect_stdout not in str(output):
        return STATUS_FAILED, f"expected output {criterion.expect_stdout!r} not found"
    if not criterion.expect_stdout:
        return STATUS_PASSED, "exit code matched (no expect_stdout declared)"
    return STATUS_PASSED, ""


def _artifact_verdict(criterion: Criterion, records: Sequence[dict]) -> tuple[str, str]:
    missing = [item["path"] for item in records if not item.get("exists")]
    if missing:
        return STATUS_FAILED, "artifact missing: " + ", ".join(missing)
    if criterion.min_bytes:
        small = [
            item["path"] for item in records
            if int(item.get("size_bytes") or 0) < int(criterion.min_bytes)
        ]
        if small:
            return STATUS_FAILED, (
                f"artifact smaller than the declared min_bytes={criterion.min_bytes}: "
                + ", ".join(small)
            )
    return STATUS_PASSED, "every declared artifact is present"


def _suite_result(criterion: Criterion, *, workspace: Path, timeout: float, revision: int,
                  session: str) -> tuple[str, int | None, str, str]:
    """The project's own suite, really executed (``(status, exit, output, cmd)``)."""
    from .completion import execute_check

    evidence = execute_check(
        criterion.check_id or "pytest",
        workspace,
        timeout=int(timeout),
        revision=int(revision),
        workspace=str(workspace),
        session=str(session),
    )
    if evidence is None:
        return STATUS_UNKNOWN, None, (
            f"no runner for check '{criterion.check_id}' — this cannot be verified here"
        ), ""
    status = str(getattr(evidence, "status", "") or "")
    mapped = {
        "passed": STATUS_PASSED,
        "failed": STATUS_FAILED,
        "not-certifiable": STATUS_UNKNOWN,
        "unknown": STATUS_UNKNOWN,
    }.get(status, STATUS_UNKNOWN)
    detail = str(getattr(evidence, "detail", "") or "")
    return mapped, _exit_from_detail(detail), detail, str(getattr(evidence, "cmd", "") or "")


def _result_record(
    criterion: Criterion,
    *,
    status: str,
    reason: str,
    cmd: str,
    exit_code: int | None,
    output: str,
    artifacts: Sequence[dict],
    workspace: str,
    revision: int,
    session: str,
    started: float,
    finished: float,
) -> dict:
    record: dict[str, Any] = {
        "criterion": criterion.text,
        "criterion_id": criterion.criterion_id,
        "check_id": criterion.check_id,
        "kind": criterion.kind,
        "planned": criterion.planned,
        "blocking": bool(criterion.blocking),
        "status": status,
        "reason": reason,
        "cmd": cmd,
        "exit_code": exit_code,
        "started_at": _wall_to_iso(started),
        "finished_at": _wall_to_iso(finished),
        "duration_ms": int(max(0.0, finished - started) * 1000),
        "output_tail": _tail(output),
        "output_sha256": sha256_text(output),
        "artifacts": [dict(item) for item in artifacts],
        "source": "runner",
        "workspace": workspace,
        "revision": int(revision),
        "session": session,
    }
    record["evidence_sha256"] = sha256_text(canonical_json(record))
    return record


def run_declared_criterion(
    criterion: Criterion,
    *,
    workspace: str | Path,
    timeout: float,
    label: str = "",
    read_only_workspace: bool = False,
) -> tuple[str, int | None, str, str, str]:
    """Execute one **declared-command criterion** and judge it — ``(status, exit, output,
    reason, cmd)`` with ``status`` in ``passed``/``failed``/``unknown``.

    The one judge both gates read: :func:`check_criterion` (the independent verify pass) and
    the completion gate's ``execute_check`` call it, so the model's completion claim and the
    verification pass can never disagree about what the criterion's own command did. They did
    disagree before: the verify pass ran the vector while the completion gate had no runner
    for it at all, so every argv criterion was un-certifiable on the completion side and the
    goal could only close through the independent pass.
    """
    root = Path(workspace)
    argv = tuple(criterion.argv)
    cmd = criterion.declared_command
    if not argv and cmd:
        from .completion import _split_command

        argv = tuple(_split_command(cmd))
    if not argv:
        return (
            STATUS_UNKNOWN,
            None,
            "",
            criterion.reason or f"check '{criterion.check_id}' has no declaration here",
            cmd,
        )
    cmd = cmd or " ".join(argv)
    status, exit_code, output = _run_declared(
        argv, workspace=root, timeout=timeout, label=label or criterion.check_id or "command",
        read_only_workspace=read_only_workspace,
    )
    if status != "ran":
        return (
            status,
            exit_code,
            output,
            output or "the declared command could not be executed here",
            cmd,
        )
    verdict, reason = _expectation_verdict(criterion, exit_code, output)
    return verdict, exit_code, output, reason, cmd


def check_criterion(criterion: Criterion, *, workspace: str | Path,
                    timeout: float = DEFAULT_TIMEOUT, revision: int = 0,
                    session: str = "") -> dict:
    """Execute one criterion's check against the real workspace. Read-only."""
    root = Path(workspace)
    started = time.time()
    cmd = ""
    exit_code: int | None = None
    output = ""
    artifacts: list[dict] = []
    status = STATUS_UNKNOWN
    reason = ""

    if not root.is_dir():
        status, reason = STATUS_UNKNOWN, f"workspace does not exist: {root}"
    elif not criterion.planned:
        reason = criterion.reason or (
            "no check is declared or derivable for this criterion — it cannot be verified"
        )
    elif criterion.kind == KIND_ARTIFACT:
        artifacts = [artifact_record(path, root=root) for path in criterion.paths]
        status, reason = _artifact_verdict(criterion, artifacts)
        cmd = "inspect " + " ".join(str(path) for path in criterion.paths)
    elif criterion.kind == KIND_SUITE:
        status, exit_code, output, cmd = _suite_result(
            criterion, workspace=root, timeout=timeout, revision=revision, session=session
        )
        reason = reason or _reason_from_output(status, output, criterion)
    elif criterion.kind == KIND_COMMAND:
        # One judge for both gates: the completion gate's runner calls the same function, so
        # the model's claim and this independent pass cannot disagree (see
        # :func:`run_declared_criterion`).
        status, exit_code, output, reason, cmd = run_declared_criterion(
            criterion, workspace=root, timeout=timeout
        )
    else:
        reason = f"unknown check kind '{criterion.kind}'"

    return _result_record(
        criterion,
        status=status,
        reason=reason,
        cmd=cmd,
        exit_code=exit_code,
        output=output,
        artifacts=artifacts,
        workspace=str(root),
        revision=int(revision),
        session=str(session),
        started=started,
        finished=time.time(),
    )


def _reason_from_output(status: str, output: str, criterion: Criterion) -> str:
    if status == STATUS_PASSED:
        return f"check '{criterion.check_id}' passed"
    return _tail(output, 300) or f"check '{criterion.check_id}' did not pass"


def verdict_for(results: Sequence[dict]) -> tuple[str, str]:
    """The verdict — the only place one is computed. ``UNKNOWN`` is never a PASS.

    * any criterion ``failed`` → ``FAIL``
    * else any **blocking** criterion not ``passed`` → ``UNKNOWN``
    * else every blocking criterion ``passed`` → ``PASS``
    * no result at all → ``UNKNOWN`` (nothing was verified)
    """
    if not results:
        return VERDICT_UNKNOWN, "no criterion was verified — nothing to certify"
    failed = [item for item in results if item.get("status") == STATUS_FAILED]
    if failed:
        names = ", ".join(str(item.get("criterion")) for item in failed[:3])
        return VERDICT_FAIL, f"{len(failed)} criterion/criteria failed: {names}"
    unresolved = [
        item for item in results
        if item.get("blocking", True) and item.get("status") != STATUS_PASSED
    ]
    if unresolved:
        names = ", ".join(str(item.get("criterion")) for item in unresolved[:3])
        return VERDICT_UNKNOWN, (
            f"{len(unresolved)} blocking criterion/criteria could not be verified: {names}"
        )
    non_blocking_unknown = [
        item for item in results
        if not item.get("blocking", True) and item.get("status") != STATUS_PASSED
    ]
    total = len(results)
    if non_blocking_unknown:
        return VERDICT_PASS, (
            f"all {total} blocking criterion/criteria verified; "
            f"{len(non_blocking_unknown)} non-blocking criterion/criteria remain unknown"
        )
    return VERDICT_PASS, f"all {total} criterion/criteria verified against the real workspace"


def run_plan(plan: Plan, *, timeout: float = DEFAULT_TIMEOUT, session: str = "",
             state_dir: str | Path | None = None, record: bool = True) -> dict:
    """Execute every planned check for real and record the evidence.

    Returns the run record (``verdict``, one record per criterion, hashes, timestamps).
    Nothing here may turn an unexecuted check into a pass: the verdict comes from
    :func:`verdict_for` over the executed results only.
    """
    started = time.time()
    workspace = Path(plan.workspace)
    results: list[dict] = []
    plan_dict = plan.as_dict()
    run_id = f"vr-{time.strftime('%Y%m%dT%H%M%S')}-{sha256_text(canonical_json(plan_dict))[:8]}"

    for criterion in plan.criteria:
        results.append(
            check_criterion(
                criterion,
                workspace=workspace,
                timeout=timeout,
                revision=plan.revision,
                session=session,
            )
        )

    verdict, verdict_reason = verdict_for(results)
    finished = time.time()
    after = workspace_digest(workspace)
    pre_digest = str(plan.digest.get("digest") or "")
    run: dict[str, Any] = {
        "run_id": run_id,
        "goal": plan.goal,
        "workspace": str(workspace),
        "revision": int(plan.revision),
        "session": str(session),
        "started_at": _wall_to_iso(started),
        "finished_at": _wall_to_iso(finished),
        "duration_ms": int((finished - started) * 1000),
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "results": results,
        "counts": {
            "criteria": len(results),
            "passed": len([item for item in results if item["status"] == STATUS_PASSED]),
            "failed": len([item for item in results if item["status"] == STATUS_FAILED]),
            "unknown": len([item for item in results if item["status"] == STATUS_UNKNOWN]),
            "blocking": len([item for item in results if item.get("blocking", True)]),
        },
        "plan": plan_dict,
        "plan_sha256": sha256_text(canonical_json(plan_dict)),
        "workspace_digest": plan.digest,
        "workspace_digest_after": after,
        "workspace_changed_during_run": bool(
            pre_digest and after.get("digest") and pre_digest != after["digest"]
        ),
        "pass_requirement": PASS_REQUIREMENT,
        # the invariant, spelled out for a reader of the record
        "fake_pass_guard": (
            "verdict is computed from executed results only; an unexecuted, not-certifiable "
            "or unplannable check is UNKNOWN and can never yield PASS"
        ),
        "evidence": [item["evidence_sha256"] for item in results],
        "state_dir": "",
    }
    if verdict == VERDICT_UNKNOWN:
        run["unknown_reason"] = verdict_reason
    if verdict == VERDICT_FAIL:
        run["failure_reason"] = verdict_reason

    if record and state_dir:
        store = VerifyStore(state_dir)
        run["state_dir"] = str(store.dir)
        store.save_run(run)
    return run


# --------------------------------------------------------------------------- #
# the evidence store                                                            #
# --------------------------------------------------------------------------- #


class VerifyStore:
    """Append-only verification records under one state directory.

    ``<state>/runs/<run_id>.json`` holds the complete run (plan + results + hashes);
    ``<state>/evidence.jsonl`` holds one line per criterion record, so a reader can
    follow a single criterion across runs without loading whole runs.
    """

    def __init__(self, state_dir: str | Path) -> None:
        self.dir = Path(state_dir)
        self.runs_dir = self.dir / "runs"
        self.evidence_path = self.dir / "evidence.jsonl"

    # -- writes -------------------------------------------------------------

    def save_run(self, run: dict) -> Path:
        """Persist the run record and append its criterion evidence. Returns the path."""
        run_id = str(run.get("run_id") or "")
        if not run_id:
            raise ValueError("a run record needs a run_id")
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        path = self.runs_dir / f"{run_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        self.append_evidence(run)
        return path

    def append_evidence(self, run: dict) -> int:
        """Append one evidence line per criterion result. Returns the number written."""
        self.dir.mkdir(parents=True, exist_ok=True)
        written = 0
        with open(self.evidence_path, "a", encoding="utf-8") as handle:
            for result in run.get("results") or []:
                line = {
                    "run_id": str(run.get("run_id") or ""),
                    "criterion": result.get("criterion"),
                    "criterion_id": result.get("criterion_id"),
                    "check_id": result.get("check_id"),
                    "kind": result.get("kind"),
                    "status": result.get("status"),
                    "blocking": result.get("blocking"),
                    "cmd": result.get("cmd"),
                    "exit_code": result.get("exit_code"),
                    "started_at": result.get("started_at"),
                    "finished_at": result.get("finished_at"),
                    "duration_ms": result.get("duration_ms"),
                    "output_sha256": result.get("output_sha256"),
                    "artifacts": result.get("artifacts") or [],
                    "workspace": run.get("workspace"),
                    "verdict": run.get("verdict"),
                    "evidence_sha256": result.get("evidence_sha256"),
                }
                handle.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
                written += 1
        return written

    # -- reads --------------------------------------------------------------

    def run_paths(self) -> list[Path]:
        if not self.runs_dir.is_dir():
            return []
        return sorted(self.runs_dir.glob("*.json"), key=lambda item: item.name, reverse=True)

    def runs(self, limit: int = 20) -> list[dict]:
        """Run **summaries**, newest first (the full records stay on disk)."""
        summaries: list[dict] = []
        for path in self.run_paths()[: max(1, int(limit))]:
            record = self.load(path)
            if not record:
                continue
            summaries.append(self.summary(record))
        return summaries

    def latest(self) -> dict | None:
        for path in self.run_paths():
            record = self.load(path)
            if record:
                return record
        return None

    def get(self, run_id: str) -> dict | None:
        path = self.runs_dir / f"{run_id}.json"
        return self.load(path)

    @staticmethod
    def load(path: str | Path) -> dict | None:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def summary(record: dict) -> dict:
        return {
            "run_id": record.get("run_id"),
            "goal": record.get("goal"),
            "workspace": record.get("workspace"),
            "verdict": record.get("verdict"),
            "verdict_reason": record.get("verdict_reason"),
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
            "duration_ms": record.get("duration_ms"),
            "counts": record.get("counts") or {},
            "plan_sha256": record.get("plan_sha256"),
        }

    def evidence(self, *, run_id: str = "", criterion: str = "", limit: int = 50) -> list[dict]:
        """Recorded criterion evidence, newest last, optionally filtered."""
        if not self.evidence_path.is_file():
            return []
        rows: list[dict] = []
        try:
            text = self.evidence_path.read_text(encoding="utf-8")
        except OSError:
            return []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if run_id and str(row.get("run_id") or "") != str(run_id):
                continue
            if criterion:
                haystack = " ".join([
                    str(row.get("criterion") or ""),
                    str(row.get("criterion_id") or ""),
                    str(row.get("check_id") or ""),
                    str(row.get("cmd") or ""),
                    " ".join(
                        str(item.get("path") or "") for item in (row.get("artifacts") or [])
                    ),
                ]).lower()
                if str(criterion).strip().lower() not in haystack:
                    continue
            rows.append(row)
        if limit and len(rows) > int(limit):
            return rows[-int(limit):]
        return rows


# --------------------------------------------------------------------------- #
# the semantic gate — deterministic VERIFY → Jev → promotion (MVP/93 §3)          #
# --------------------------------------------------------------------------- #

#: The gate's own schema id.
SEMANTIC_GATE_SCHEMA = "skaldir.verify.semantic_gate.v1"

#: The gate states a caller branches on.
GATE_SUPPORTED = "supported"
GATE_REFUSED = "refused"
GATE_UNDERSPECIFIED = "underspecified"
GATE_NO_ADVISORY = "no_advisory"
GATE_SKIPPED = "skipped"

#: Promotion outcomes. ``allowed`` is the only one a completion may act on.
PROMOTION_ALLOWED = "allowed"
PROMOTION_BLOCKED = "blocked"
PROMOTION_UNREAD = "unread"
PROMOTION_SKIPPED = "skipped"

GATE_STATES = (
    GATE_SUPPORTED,
    GATE_REFUSED,
    GATE_UNDERSPECIFIED,
    GATE_NO_ADVISORY,
    GATE_SKIPPED,
)


def changed_file_facts(workspace: str | Path, *, limit: int = DIGEST_MAX_FILES) -> list[dict]:
    """The changed files of a workspace with a real diffstat and content digest.

    Two git calls (``status --porcelain``, ``diff --numstat HEAD``) and one
    bounded hash per file. A path git cannot diff (an untracked file) still gets
    its digest and an honest ``0/0`` diffstat: "new file, unknown line delta" is
    information, and a missing entry would not be.
    """
    root = Path(workspace)
    facts: list[dict] = []
    if not root.is_dir():
        return facts
    try:
        status = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, timeout=30,
        )
        diff = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["git", "-C", str(root), "diff", "--numstat", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return facts
    if status.returncode != 0:
        return facts
    stats: dict[str, tuple[int, int]] = {}
    for line in diff.stdout.splitlines() if diff.returncode == 0 else []:
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, removed, path = parts
        stats[path.strip()] = (
            int(added) if added.isdigit() else 0,
            int(removed) if removed.isdigit() else 0,
        )
    for path in _changed_paths(status.stdout)[: max(1, int(limit))]:
        added, removed = stats.get(path, (0, 0))
        digest = ""
        try:
            digest, _ = sha256_file(root / path)
        except OSError:
            digest = ""
        facts.append(
            {
                "path": path,
                "added": added,
                "removed": removed,
                "digest": (digest or "")[:16],
            }
        )
    return facts


def semantic_brief_from_run(
    run: dict,
    *,
    claim: str,
    workspace: str | Path | None = None,
    changed_files: Sequence[dict] | None = None,
    transcript: Sequence[str] | None = None,
    tail_lines: int = DEFAULT_BRIEF_TAIL_LINES,
    max_chars: int = DEFAULT_MAX_BRIEF_CHARS,
) -> tuple[EvidenceBrief, list[str]]:
    """Build the Evidence Brief for a finished deterministic run.

    Everything is read from what the run already recorded — the plan's goal and
    digests, the executed commands with their exit codes, the evidence hashes and
    each criterion's output tail. Nothing is re-run and nothing is invented; the
    second return value is the full transcript lines the brief *replaces*, so the
    token effect can be measured on the same run.
    """
    plan = run.get("plan") or {}
    objective = str(run.get("goal") or plan.get("goal") or "")
    results = list(run.get("results") or [])
    commands: list[dict] = []
    refs: list[str] = []
    for result in results:
        cmd = str(result.get("cmd") or "").strip()
        if cmd:
            commands.append(
                {
                    "cmd": cmd,
                    "exit_code": result.get("exit_code"),
                    "duration_ms": int(result.get("duration_ms") or 0),
                }
            )
        if result.get("evidence_sha256"):
            refs.append(str(result["evidence_sha256"]))
        if result.get("output_sha256"):
            refs.append(str(result["output_sha256"]))
    for key in ("run_id", "plan_sha256"):
        if run.get(key):
            refs.append(f"{key}:{run[key]}")
    for artifact in [a for result in results for a in (result.get("artifacts") or [])]:
        if artifact.get("sha256"):
            refs.append(f"{artifact.get('path')}:{artifact['sha256']}")

    if changed_files is None:
        target = workspace if workspace is not None else run.get("workspace")
        changed_files = changed_file_facts(target) if target else []

    if transcript is None:
        lines: list[str] = []
        for result in results:
            tail = str(result.get("output_tail") or "").strip()
            lines.append(
                f"criterion {result.get('criterion')!r}: {result.get('status')} "
                f"(exit {result.get('exit_code')})"
            )
            if tail:
                lines.extend(tail.splitlines())
        transcript = lines

    brief = build_evidence_brief(
        objective=objective,
        claim=claim,
        objective_digest=str(run.get("plan_sha256") or ""),
        files=changed_files,
        commands=commands,
        evidence_refs=refs,
        transcript=transcript,
        baseline_revision=str((run.get("workspace_digest") or {}).get("head") or ""),
        tail_lines=tail_lines,
        max_chars=max_chars,
    )
    return brief, list(transcript)


def semantic_gate(
    run: dict,
    *,
    claim: str = "",
    workspace: str | Path | None = None,
    changed_files: Sequence[dict] | None = None,
    transcript: Sequence[str] | None = None,
    evaluator: Any | None = None,
    thresholds: Any | None = None,
    dispatcher: Any | None = None,
    goal_id: str = "",
    goal_revision: int = 0,
    tail_lines: int = DEFAULT_BRIEF_TAIL_LINES,
    max_chars: int = DEFAULT_MAX_BRIEF_CHARS,
) -> dict:
    """The semantic gate: the deterministic verdict, then a read of the claim.

    The chain is exactly ``deterministic VERIFY → Jev semantic verifier →
    promotion``, and the invariants are absolute:

    * **the deterministic verdict is never replaced** — ``verdict_after_gate``
      always equals ``run["verdict"]``; a non-PASS run is not even sent to the
      gate (``state = "skipped"``), because there is nothing to promote;
    * **the gate can only block a promotion, never grant one** — ``promotion``
      is ``allowed`` only when the verifier read the claim as *supported*;
    * **an unavailable gate is not agreement and not a refusal** —
      ``no_advisory`` leaves the deterministic verdict standing and says so.

    ``dispatcher``, when given, must be a **read-only verifier**
    (:mod:`skaldir.subagent_roles`): passing a writer role is refused by name,
    because a verifier that can edit is not a verifier.
    """
    verdict = str(run.get("verdict") or "")
    gate: dict[str, Any] = {
        "schema": SEMANTIC_GATE_SCHEMA,
        "gate": "semantic_verification",
        "deterministic_verdict": verdict,
        "verdict_after_gate": verdict,
        "replaces_deterministic_checks": False,
        "state": GATE_SKIPPED,
        "promotion": PROMOTION_SKIPPED,
        "promotion_reason": "",
        "blocking": False,
        "authority": "advisory",
        "certifies": False,
        "decision_id": "",
        "status": "",
        "provider_mode": "",
        "support": "",
        "reasons": [],
        "evidence_gaps": [],
        "unverifiable": [],
        "checks_run": [],
        "budget": {},
        "read_only": True,
        "brief": {},
        "token_effect": {},
    }

    # -- the read-only ceiling, before anything else -------------------------
    if dispatcher is not None:
        role = str(getattr(dispatcher, "role", ""))
        policy = getattr(dispatcher, "policy", None)
        if role != ROLE_VERIFIER:
            gate["state"] = GATE_NO_ADVISORY
            gate["promotion"] = PROMOTION_UNREAD
            gate["promotion_reason"] = (
                f"refused: the semantic gate needs a {ROLE_VERIFIER!r} dispatcher, "
                f"got {role!r} — a gate that can write is not a verifier"
            )
            gate["refusal_code"] = "role_ceiling.tool_not_granted"
            return gate
        if policy is not None and not bool(policy.read_only):
            gate["state"] = GATE_NO_ADVISORY
            gate["promotion"] = PROMOTION_UNREAD
            gate["promotion_reason"] = "refused: the verifier role is not read-only"
            gate["refusal_code"] = "role_ceiling.tool_not_granted"
            return gate

    budget = ROLE_BUDGETS[ROLE_VERIFIER]
    gate["budget"] = {
        **budget.as_dict(),
        "max_reads": int(budget.max_tool_calls),
        "role": ROLE_VERIFIER,
        "source": "skaldir.subagent_roles.ROLE_BUDGETS['verifier']",
    }

    if verdict != VERDICT_PASS:
        gate["promotion_reason"] = (
            f"the deterministic verdict is {verdict or 'absent'}, not {VERDICT_PASS} — "
            "the semantic gate adds nothing to a run that did not pass, and it never "
            "turns one into a pass"
        )
        return gate

    if not str(claim or "").strip():
        gate["state"] = GATE_NO_ADVISORY
        gate["promotion"] = PROMOTION_UNREAD
        gate["promotion_reason"] = (
            "no completion claim was supplied — the deterministic PASS stands and "
            "nobody read it against the brief"
        )
        return gate

    try:
        brief, full_transcript = semantic_brief_from_run(
            run,
            claim=claim,
            workspace=workspace,
            changed_files=changed_files,
            transcript=transcript,
            tail_lines=tail_lines,
            max_chars=max_chars,
        )
    except EvidenceBriefError as exc:
        gate["state"] = GATE_NO_ADVISORY
        gate["promotion"] = PROMOTION_UNREAD
        gate["promotion_reason"] = f"the evidence brief could not be built: {exc}"
        return gate

    gate["brief"] = {
        "schema": brief.schema,
        "chars": brief.chars,
        "files": len(brief.files),
        "commands": len(brief.commands),
        "evidence_refs": len(brief.evidence_refs),
        "tail_lines": len(brief.transcript_tail),
        "transcript_lines_total": brief.transcript_lines_total,
        "truncated": bool(brief.truncated),
    }
    gate["token_effect"] = token_effect(brief, full_transcript)

    state, payload = _read_brief_with_jev(
        brief,
        run=run,
        goal_id=goal_id or str(run.get("run_id") or ""),
        goal_revision=int(goal_revision),
        evaluator=evaluator,
        thresholds=thresholds,
        budget=budget,
    )
    gate.update(payload)
    gate["state"] = state
    if state == GATE_SUPPORTED:
        gate["promotion"] = PROMOTION_ALLOWED
        gate["promotion_reason"] = (
            "the verifier read the claim as supported by the brief; the deterministic "
            f"{VERDICT_PASS} verdict stands unchanged"
        )
    elif state in (GATE_REFUSED, GATE_UNDERSPECIFIED):
        gate["promotion"] = PROMOTION_BLOCKED
        gate["blocking"] = True
        gate["promotion_reason"] = (
            "the semantic gate refused the promotion: "
            + ("; ".join(gate.get("reasons") or []) or "no reason given")
        )
    else:
        gate["promotion"] = PROMOTION_UNREAD
        gate["promotion_reason"] = (
            "no semantic advisory was obtained; the deterministic verdict stands alone "
            "and is not to be read as having been checked semantically"
        )
    return gate


def _read_brief_with_jev(
    brief: EvidenceBrief,
    *,
    run: dict,
    goal_id: str,
    goal_revision: int,
    evaluator: Any | None,
    thresholds: Any | None,
    budget: Any,
) -> tuple[str, dict]:
    """Ask the Jev semantic verifier one question about one brief."""
    from .jev import decide

    state = {
        "run_id": str(run.get("run_id") or ""),
        "goal_id": goal_id,
        "goal_revision": int(goal_revision),
        "risk_class": "R1",
        "objective": brief.objective,
        "objective_digest": brief.objective_digest,
        "claim": brief.claim,
        "criteria_already_met": [
            str(result.get("criterion"))
            for result in (run.get("results") or [])
            if str(result.get("status")) == STATUS_PASSED and result.get("blocking", True)
        ],
        "files": [item.as_dict() for item in brief.files],
        "commands": [item.as_dict() for item in brief.commands],
        "evidence_refs": list(brief.evidence_refs),
        "transcript_tail": list(brief.transcript_tail),
        "brief_truncated": bool(brief.truncated),
        "baseline_revision": brief.baseline_revision,
        "budget": {
            "max_tokens": int(budget.max_tokens),
            "max_turns": int(budget.max_turns),
            "max_reads": int(budget.max_tool_calls),
        },
    }
    kwargs: dict[str, Any] = {}
    if evaluator is not None:
        kwargs["evaluator"] = evaluator
    if thresholds is not None:
        kwargs["thresholds"] = thresholds
    try:
        decision = decide("semantic_verification", state, **kwargs)
    except Exception as exc:  # noqa: BLE001 — a gate failure is a state, not a crash
        return GATE_NO_ADVISORY, {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}"[:400],
            "reasons": [f"the semantic verifier could not be reached: {type(exc).__name__}"],
        }

    answer = decision.answer
    payload = {
        "decision_id": str(decision.decision_id),
        "status": str(decision.status),
        "provider_mode": str(decision.provider_mode),
        "authority": str(decision.authority),
        "certifies": bool(decision.certifies),
        "live_jev": bool(decision.live_jev),
        "confidence": float(decision.confidence),
        "question_pack": f"{decision.question_pack_id}:{decision.question_pack_version}",
        "support": str(getattr(answer, "support", "")),
        "reasons": list(getattr(answer, "reasons", ()) or ()),
        "evidence_gaps": list(getattr(answer, "evidence_gaps", ()) or ()),
        "unverifiable": list(getattr(answer, "unverifiable", ()) or ()),
        "checks_run": list(getattr(answer, "checks_run", ()) or ()),
        "provider_choice": str(getattr(answer, "provider_choice", "")),
        "refusal_reason": str(decision.refusal_reason),
    }
    if decision.status != "ok":
        return GATE_NO_ADVISORY, payload
    if payload["support"] == "supported":
        return GATE_SUPPORTED, payload
    if payload["support"] == "unsupported":
        return GATE_REFUSED, payload
    return GATE_UNDERSPECIFIED, payload


def token_effect(brief: EvidenceBrief, transcript: Sequence[Any]) -> dict:
    """The measured token difference: the brief **vs** the full transcript.

    Same estimator (``skaldir.llm.estimate_tokens``, chars/4) and the same
    payload shape on both sides, so the two numbers are comparable; ``saved`` is
    negative when the brief is the more expensive of the two, which happens for a
    very short run and is reported rather than hidden.
    """
    brief_tokens_value = _brief_tokens(brief)
    full_tokens = _transcript_tokens(transcript)
    saved = int(full_tokens) - int(brief_tokens_value)
    return {
        "estimator": "skaldir.llm.estimate_tokens (chars / 4)",
        "brief_tokens": int(brief_tokens_value),
        "transcript_tokens": int(full_tokens),
        "saved_tokens": saved,
        "ratio": (
            round(int(brief_tokens_value) / int(full_tokens), 4) if full_tokens else None
        ),
        "brief_chars": int(brief.chars),
        "transcript_lines": len(list(transcript)),
    }


__all__ = [
    "CHECK_KINDS",
    "CHECKS_FILENAME",
    "DECLARED_CHECK_ENV",
    "DEFAULT_TIMEOUT",
    "GATE_NO_ADVISORY",
    "GATE_REFUSED",
    "GATE_SKIPPED",
    "GATE_STATES",
    "GATE_SUPPORTED",
    "GATE_UNDERSPECIFIED",
    "KIND_ARTIFACT",
    "KIND_COMMAND",
    "KIND_SUITE",
    "PASS_REQUIREMENT",
    "PLAN_FILENAME",
    "PROMOTION_ALLOWED",
    "PROMOTION_BLOCKED",
    "PROMOTION_SKIPPED",
    "PROMOTION_UNREAD",
    "SEMANTIC_GATE_SCHEMA",
    "STATUS_FAILED",
    "STATUS_PASSED",
    "STATUS_UNKNOWN",
    "SUITE_CHECKS",
    "VERDICT_FAIL",
    "VERDICT_PASS",
    "VERDICT_UNKNOWN",
    "Criterion",
    "Plan",
    "VerifyStore",
    "artifact_record",
    "build_plan",
    "canonical_json",
    "changed_file_facts",
    "check_criterion",
    "contract_criteria",
    "declared_command",
    "load_plan_file",
    "now_iso",
    "run_freshness",
    "run_plan",
    "semantic_brief_from_run",
    "semantic_gate",
    "sha256_file",
    "sha256_text",
    "token_effect",
    "verdict_for",
    "workspace_digest",
]
