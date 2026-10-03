"""Strict completion supervisor (Goal §1–§34): the loop that never ends on "I think I'm done".

The main agent may only *request* completion; this module decides. State machine
(§2):

    WORKING → COMPLETION_REQUESTED → VERIFYING
      ├── VERIFIED_COMPLETE        (all blocking requirements independently verified)
      ├── INCOMPLETE               (NOT a terminal state → redirect → resume)
      ├── BLOCKED_USER / BLOCKED_EXTERNAL / FAILED_UNRECOVERABLE   (terminal, evidence-backed)
      └── USER_CANCELLED / SHIPPED_WITH_USER_OVERRIDE              (terminal, explicit)

System One (Jev) reaches this module **only** through
:class:`skaldir.system_one_service.SystemOneService`, the one facade (§33):
``Completion -> SystemOneService -> SystemOneEvaluator``. This module never
imports a provider and never talks to an endpoint directly (§3), and no
preflight reading here can certify a completion — the deterministic gate does
(§9.8/JEV-15).

Design rules honoured here:
* Claims from the main agent are **claims** until evidence exists (§13/§17).
* Gates run against real repository facts, real run evidence and the pact's
  required verification (§14–§27) — never against the agent's summary alone.
* Repeating the same rejection is not allowed: failures carry a fingerprint and
  repeat offenders escalate to a strategy change (§31/§32).
* No new nice-to-haves: only blocking criteria can block (§33/§34).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any

from .env import child_env, getenv
from .system_one import SystemOneContractError

# The §9.4/§9.7 question ids live in the versioned registry, never as literal
# strings spread through this module (§39/§5).
from .system_one_questions import (
    COMPLETION_PREFLIGHT_SEMANTIC_IDS as COMPLETION_SEMANTIC_IDS,
)
from .system_one_questions import COMPLETION_SANITY_IDS

_log = logging.getLogger("skaldir.completion")

# One lock per contract file: the panel, the hook and the supervisor may all
# write the same session's contract from different threads.
_WRITE_LOCKS: dict[str, threading.RLock] = {}
_WRITE_LOCKS_GUARD = threading.Lock()


def _path_lock(path: str | Path) -> threading.RLock:
    """The (re-entrant) lock that serializes writers of one contract file."""
    key = str(Path(path).resolve())
    with _WRITE_LOCKS_GUARD:
        lock = _WRITE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _WRITE_LOCKS[key] = lock
        return lock


def _serialized(method):
    """Hold the contract's write lock across the WHOLE read-modify-write.

    Writing atomically is not enough: two writers that each read the record,
    mutate their own copy and write it back still lose one update. The panel,
    the plugin hook and the supervisor all mutate the same session's contract,
    so every mutator takes the file lock for its full body (re-entrant —
    ``transition`` may nest inside ``add_criteria``).
    """

    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with _path_lock(self.state_path):
            return method(self, *args, **kwargs)

    return wrapper


def _atomic_write_text(path: str | Path, text: str) -> None:
    """Write ``text`` atomically: temp file in the same dir, then ``os.replace``.

    A crash or interrupt mid-write must never expose a half-written contract:
    ``Path.write_text`` truncated the file in place, and a truncated JSON
    document read back as a clean empty WORKING state — goal, revision and DoD
    silently gone (Block 1 / STATE-integrity). ``os.replace`` is atomic within
    a filesystem, so a reader sees either the old or the new complete file.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        # never leave a stray temp file behind on the failure path
        try:
            os.unlink(tmp_name)
        except OSError:  # pragma: no cover — already gone
            pass
        raise

WORKING = "WORKING"
COMPLETION_REQUESTED = "COMPLETION_REQUESTED"
VERIFYING = "VERIFYING"
INCOMPLETE = "INCOMPLETE"
REDIRECT = "REDIRECT"
VERIFIED_COMPLETE = "VERIFIED_COMPLETE"
BLOCKED_USER = "BLOCKED_USER"
BLOCKED_EXTERNAL = "BLOCKED_EXTERNAL"
FAILED_UNRECOVERABLE = "FAILED_UNRECOVERABLE"
USER_CANCELLED = "USER_CANCELLED"
SHIPPED_WITH_USER_OVERRIDE = "SHIPPED_WITH_USER_OVERRIDE"

STATES = (
    WORKING,
    COMPLETION_REQUESTED,
    VERIFYING,
    INCOMPLETE,
    REDIRECT,
    VERIFIED_COMPLETE,
    BLOCKED_USER,
    BLOCKED_EXTERNAL,
    FAILED_UNRECOVERABLE,
    USER_CANCELLED,
    SHIPPED_WITH_USER_OVERRIDE,
)

TERMINAL_STATES = (
    VERIFIED_COMPLETE,
    BLOCKED_USER,
    BLOCKED_EXTERNAL,
    FAILED_UNRECOVERABLE,
    USER_CANCELLED,
    SHIPPED_WITH_USER_OVERRIDE,
)

BLOCKER = "BLOCKER"
MAJOR = "MAJOR"
MINOR = "MINOR"
INFO = "INFO"

#: Status for an honest non-answer: a check that is declared but cannot run in
#: this environment. It is never a pass and never a silent failure — the
#: runner's own reason travels with it (Block 3).
NOT_CERTIFIABLE = "not-certifiable"

_SEVERITY_ORDER = {BLOCKER: 0, MAJOR: 1, MINOR: 2, INFO: 3}


@dataclass
class DodItem:
    """One definition-of-done criterion (§6). ``blocking`` decides the loop."""

    id: str
    text: str
    blocking: bool = True
    verification: str = ""  # e.g. "pytest", "ui_e2e", "build", "persistence_restart"
    status: str = "open"  # open | verified | failed
    #: A criterion may declare its check as a **command vector** instead of a known check id
    #: (``"check": ["python", "spec/check_api.py"]``). The vector travels with the item, because
    #: the runner needs the real argv — without it the gate had a criterion it could name but
    #: never certify, and the goal could only close through the independent verify pass.
    check_argv: tuple[str, ...] = ()


@dataclass
class CompletionRequest:
    """§13 — everything the main agent claims, all of it unverified."""

    user_goal: str = ""
    claimed_completed: list[str] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)
    implementation_summary: str = ""
    files_changed: list[str] = field(default_factory=list)
    tests_run: list[str] = field(default_factory=list)
    test_results: str = ""
    build_results: str = ""
    runtime_checks: list[str] = field(default_factory=list)
    ui_checks: list[str] = field(default_factory=list)
    persistence_checks: list[str] = field(default_factory=list)
    regression_checks: list[str] = field(default_factory=list)
    security_checks: list[str] = field(default_factory=list)
    known_limitations: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    attempt_id: str = ""
    created: float = 0.0


@dataclass
class RunEvidence:
    """One *actually executed* check (command, exit code, output snippet).

    ``status`` is the explicit verdict — ``passed`` | ``failed`` | ``unknown``.
    Only an explicit success signal (exit code 0, or a host-supplied ok/status)
    may set ``passed``; errors, aborts and missing signals stay ``failed`` or
    ``unknown`` and never count as proof. ``source`` records provenance:
    ``runner`` (executed by the verification runner itself, with the real
    command and exit code) vs ``events`` (derived from tool events — useful as
    context, never certifying a specific requirement). ``check_id`` names the
    canonical check this result belongs to; ``tool_call_id`` ties it to the
    exact tool invocation it pairs with.
    """

    name: str
    cmd: str = ""
    ok: bool = False
    detail: str = ""
    ts: float = 0.0
    status: str = ""  # passed|failed|unknown|not-certifiable — empty falls back to `ok`
    check_id: str = ""  # canonical check (defaults to name)
    source: str = "runner"  # runner|events
    tool_call_id: str = ""
    revision: int = 0  # goal revision the evidence was produced under
    workspace: str = ""  # product state (repo root) it was produced in
    session: str = ""  # session id the evidence was produced for (binding, Block 3)
    suite: bool = False  # the check really invoked the project's test runner
    # -- §9 ledger binding: what the completion preflight reads -------------
    run_id: str = ""  # the id a ledger criterion references (evidence_ref)
    evidence_type: str = ""  # unit_test|mock|live_use|file_exists|claim_text|…
    platform: str = ""  # the platform the run really executed on
    outcome_contract: str = ""  # what the run promised to prove (empty = none)
    artifact: str = ""  # path (relative to the workspace) the run produced
    # -- machine-readable facts (F05): the tool surface reports these, not prose --
    exit_code: int | None = None  # the child's own exit, when a command really ran
    runner: str = ""  # which project runner executed the suite: pytest|npm|make|script

    @property
    def passed(self) -> bool:
        """Explicit success only: status==passed, else legacy ok=True.

        ``not-certifiable`` is never a pass — a declared-but-unavailable
        environment is an honest non-answer, not a green check (Block 3).
        """
        if self.status:
            return self.status == "passed"
        return bool(self.ok)

    def fresh(self, after: float) -> bool:
        return self.ts >= after


@dataclass
class Finding:
    gate: str
    severity: str
    detail: str
    requirement: str = ""
    evidence: str = ""
    #: Machine-readable reason code (§91/JEV-13). Empty for prose-only findings
    #: — legacy gates keep their exact behaviour and carry no code.
    code: str = ""


@dataclass
class VerificationResult:
    """§28 — the structured verdict."""

    status: str
    attempt_id: str = ""
    verified_requirements: list[str] = field(default_factory=list)
    blocking_failures: list[str] = field(default_factory=list)
    major_failures: list[str] = field(default_factory=list)
    minor_findings: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    fingerprint: str = ""
    repeat_count: int = 1
    escalate: bool = False
    created: float = 0.0
    #: Machine-readable refusal reasons of this verdict (§91/JEV-13). Empty for
    #: every uncoded finding, so existing callers see no new behaviour.
    refusal_codes: list[str] = field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    def redirect_md(self) -> str:
        """Compact redirect for the main agent — evidence-based, no essays."""
        if self.status == VERIFIED_COMPLETE:
            return (
                "godsv·completion: VERIFIED_COMPLETE — all blocking requirements "
                "independently verified."
            )
        head = f"godsv·completion: {self.status}"
        if self.escalate:
            head += " (repeated failure — change strategy, do not repeat the same fix)"
        lines = [head]
        for failure in self.blocking_failures[:5]:
            lines.append(f"- BLOCKER: {failure}")
        for failure in self.major_failures[:3]:
            lines.append(f"- MAJOR: {failure}")
        if self.next_actions:
            lines.append("Next actions:")
            for action in self.next_actions[:5]:
                lines.append(f"  {action}")
        lines.append(
            "Do not claim completion again before these are fixed and evidenced. "
            "Continue working now."
        )
        # Teach the main agent the honest-stop convention exactly when it
        # matters: prose alone never ends the loop — a status line does (B5).
        lines.append(
            "If — and only if — this is a genuine blocker that needs the owner's "
            "decision (access, credentials, a product choice), end with a status "
            "line `BLOCKED_USER: <reason>`; prose alone does not stop the loop."
        )
        # Keep the user posted on the way: the live block renders from this
        # paragraph (transform_llm_output does not reach the desktop surface).
        lines.append(
            'When you resume, keep the user posted — add ::godsv-live{stage="working"} '
            "as its own paragraph in the next report."
        )
        return "\n".join(lines)


def fingerprint_for(failures: list[str]) -> str:
    """Stable identity of a rejection (§31): same blockers → same fingerprint."""
    blob = "\n".join(sorted(failure.strip() for failure in failures if failure.strip()))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def plan_verification(text: str, tool_names: Iterable[str] = ()) -> str:
    """Derive the canonical check for a criterion text — '' means plan missing.

    The derivation is the *suggestion* side of the evidence rule (P1): it names
    which runner-executed check could certify the criterion. An empty result is
    honest — ``gate_dod`` reports it as a missing verification plan instead of
    letting the item float between "any check" and eternal blockage.

    ``tool_names`` are the names of registered (validated) testing tools: a
    criterion that names one of them in its text is wired to that tool's check
    id, so its runner-executed result can certify the item (live-found 13.09.:
    previously such criteria fell back to an uncertifiable plan).
    """
    text = str(text or "")
    from .guard import criterion_categories

    categories = criterion_categories(text)
    if "tests" in categories:
        return "pytest"
    if "ui" in categories:
        return "ui_e2e"
    if "api" in categories:
        return "api"
    lowered = text.lower()
    if "restart" in lowered or "persist" in lowered:
        return "persistence_restart"
    for name in tool_names:
        candidate = str(name or "").strip()
        if candidate and re.search(rf"\b{re.escape(candidate)}\b", text):
            return candidate
    return ""


def _registered_check_names(runtime: str | Path) -> list[str]:
    """Registered testing-tool names for the runtime — lazy, failure-tolerant."""
    try:
        from .capability import register_testing_tools

        return register_testing_tools(runtime)
    except Exception:
        return []


class CriterionConflict(ValueError):
    """Stale ``expected_revision``: the caller edited from an outdated view."""

    def __init__(self, current_revision: int) -> None:
        super().__init__(f"revision conflict: current revision is {current_revision}")
        self.current_revision = int(current_revision)


class CompletionStore:
    """Append-only history + current state, persisted next to the runtime."""

    def __init__(self, runtime: str | Path, session_id: str = "") -> None:
        """Completion store scoped to ONE task session (P1/C fix).

        With a session id the record lives under ``<runtime>/completion/<id>.json``
        — two parallel sessions never read or write each other's contract.
        Without one the legacy global paths stay for CLI compatibility; the
        panel and the plugin hook always pass the real session.
        """
        self.runtime = Path(runtime)
        self.session_id = str(session_id or "")
        if self.session_id:
            safe = re.sub(r"[^A-Za-z0-9._-]+", "-", self.session_id)[:80] or "session"
            base = self.runtime / "completion"
            if safe.startswith("prodtest-"):
                # Test sessions live in their own namespace: production
                # reads (sessions()/latest()/panel default) never surface
                # them, so E2E runs cannot pollute real chats. Explicit
                # access by id still resolves, and payloads mark test: true.
                base = base / "_tests"
            self.state_path = base / f"{safe}.json"
            self.history_path = base / f"{safe}.jsonl"
        else:
            self.state_path = self.runtime / "completion_state.json"
            self.history_path = self.runtime / "completion_history.jsonl"

    # -- state machine ------------------------------------------------------

    def _legacy_record(self) -> dict | None:
        path = self.runtime / "completion_state.json"
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return record if isinstance(record, dict) else None

    def _migrate_legacy(self) -> None:
        """Adopt the legacy global record only when it belongs to THIS session.

        A record written for another session is never silently adopted — that
        was exactly the cross-session leak the review reproduced.
        """
        if not self.session_id or self.state_path.exists():
            return
        legacy = self._legacy_record()
        if not legacy or str(legacy.get("session_id") or "") != self.session_id:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(self.state_path, json.dumps(legacy, ensure_ascii=False))

    def _empty_state(self, *, corrupt: bool = False, reason: str = "") -> dict:
        """The usable baseline for a session without a readable contract.

        ``corrupt`` distinguishes "never started" from "the file is there but
        unreadable" — the panel and the gates must be able to tell those apart
        (Block 1: a torn contract used to read as a fresh empty WORKING run).
        """
        record: dict = {
            "state": WORKING,
            "session_id": self.session_id,
            "goal": "",
            "updated": 0.0,
        }
        if corrupt:
            record["corrupt"] = True
            record["corrupt_reason"] = reason
        return record

    def _quarantine_corrupt(self, reason: str) -> None:
        """Keep the damaged bytes — never overwrite a contract we cannot parse."""
        try:
            stamp = f"{self.state_path.name}.corrupt.{int(time.time() * 1000)}"
            target = self.state_path.parent / stamp
            target.write_bytes(self.state_path.read_bytes())
            _log.warning(
                "completion contract unreadable (%s) — session=%s; damaged file "
                "preserved at %s",
                reason,
                self.session_id or "<global>",
                target.name,
            )
        except OSError as exc:  # pragma: no cover - best effort preservation
            _log.warning(
                "completion contract unreadable (%s) — session=%s; quarantine failed: %s",
                reason,
                self.session_id or "<global>",
                exc,
            )

    def state(self) -> dict:
        if self.session_id:
            self._migrate_legacy()
        if not self.state_path.exists():
            return self._empty_state()
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            self._quarantine_corrupt(reason)
            return self._empty_state(corrupt=True, reason=reason)
        if not isinstance(loaded, dict):
            reason = f"contract is {type(loaded).__name__}, not an object"
            self._quarantine_corrupt(reason)
            return self._empty_state(corrupt=True, reason=reason)
        return loaded

    @_serialized
    def transition(self, state: str, **extra) -> dict:
        if state not in STATES:
            raise ValueError(f"unknown completion state: {state}")
        record = self.state()
        previous = record.get("state")
        if previous in TERMINAL_STATES and state != WORKING:
            raise ValueError(
                f"state {previous} is terminal — only a new goal ({WORKING}) may continue"
            )
        if previous == VERIFIED_COMPLETE and state == WORKING:
            # Block 3 terminal invariant: a verified contract must not be
            # silently re-frozen into WORKING (nor dissolved) — only a NEW goal
            # (begin_goal, which resets revision to 1) may reopen it. Without
            # this, `transition(WORKING, goal=...)` reopened a finished run
            # with its old revision, so VERIFIED_COMPLETE looked actionable
            # again and stale evidence re-certified it.
            if "goal" not in extra:
                raise ValueError(
                    f"state {VERIFIED_COMPLETE} is terminal — begin a new goal "
                    "(a new goal identity) to continue"
                )
            revision = extra.get("revision")
            goal = str(extra.get("goal") or "").strip()
            previous_goal = str(record.get("goal") or "").strip()
            if revision is None:
                raise ValueError(
                    f"state {VERIFIED_COMPLETE} is terminal — reopening needs a new "
                    "goal with an explicit revision (begin_goal does this)"
                )
            if int(revision) <= 0 or (goal == previous_goal):
                raise ValueError(
                    f"state {VERIFIED_COMPLETE} is terminal — a new goal needs a new "
                    "identity (goal text and revision), not a rename"
                )
        # Merge over the previous record: goal/DoD/session must survive every
        # transition (a rejection that dropped the goal emptied the strip, the
        # cockpit and the gate context at once).
        record = {**record, "state": state, "updated": time.time(), **extra}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with _path_lock(self.state_path):
            _atomic_write_text(self.state_path, json.dumps(record, ensure_ascii=False))
        self._append({"kind": "state", "state": state, **extra})
        return record

    @_serialized
    def begin_goal(
        self,
        session_id: str,
        goal: str,
        dod: list[DodItem],
        task_base: str = "",
        non_goals: list[str] | None = None,
        constraints: list[str] | None = None,
    ) -> dict:
        """Begin a fresh contract: revision 1, task base recorded for diffs."""
        if self.session_id and session_id and session_id != self.session_id:
            raise ValueError("session mismatch: this store is scoped to another session")
        extra: dict = {}
        if non_goals:
            extra["non_goals"] = [
                {"text": str(item).strip(), "at": time.time()}
                for item in non_goals
                if str(item).strip()
            ]
        if constraints:
            extra["constraints"] = [
                {"text": str(item).strip(), "at": time.time()}
                for item in constraints
                if str(item).strip()
            ]
        return self.transition(
            WORKING,
            session_id=session_id or self.session_id,
            goal=goal,
            started=time.time(),
            revision=1,
            task_base=str(task_base or ""),
            dod=[asdict(item) for item in dod],
            **extra,
        )

    @_serialized
    def record_trajectory(self, kind: str, text: str, reason: str = "") -> dict:
        """Record trajectory state on the contract (ledger revival, P0-1).

        Decisions, assumptions and non-goals live WITH the contract — outside
        the LLM context — so the watcher and the per-turn context block can
        hold the agent to them. These entries do NOT bump the revision: the
        contract terms are unchanged (revision invalidation is reserved for
        goal/DoD edits). Terminal contracts refuse edits.
        """
        field = {
            "decision": "decisions",
            "assumption": "assumptions",
            "non_goal": "non_goals",
        }.get(str(kind or "").strip().lower())
        if not field:
            raise ValueError("kind must be decision|assumption|non_goal")
        clean = str(text or "").strip()
        if not clean:
            raise ValueError("text required")
        record = self.state()
        if record.get("state") in TERMINAL_STATES:
            raise ValueError(
                f"state {record['state']} is terminal — begin a new goal to edit"
            )
        entries = list(record.get(field) or [])
        entry: dict = {"text": clean, "at": time.time()}
        if str(reason or "").strip():
            entry["reason"] = str(reason).strip()
        entries.append(entry)
        payload = {
            **record,
            "state": record.get("state") or WORKING,
            "updated": time.time(),
            field: entries,
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with _path_lock(self.state_path):
            _atomic_write_text(self.state_path, json.dumps(payload, ensure_ascii=False))
        self._append(
            {"kind": "trajectory", "field": field, "text": clean, "reason": str(reason or "")}
        )
        return payload

    @_serialized
    def set_goal(self, goal: str) -> dict:
        """Replace the goal text of the active contract (inline chat edit).

        A contract edit is a revision: ``revision`` bumps and every DoD item
        drops back to ``open`` — evidence gathered against the old wording
        loses its validity (review: "contract revisions … affected old
        evidence loses its validity"). A terminal goal refuses.
        """
        record = self.state()
        if record.get("state") in TERMINAL_STATES:
            raise ValueError(
                f"state {record['state']} is terminal — begin a new goal to edit"
            )
        clean = str(goal).strip()
        if not clean:
            raise ValueError("goal required")
        dod = [{**dict(item), "status": "open"} for item in (record.get("dod") or [])]
        revision = int(record.get("revision") or 0) + 1
        payload = {
            k: v
            for k, v in record.items()
            if k not in ("state", "updated", "goal", "dod", "revision")
        }
        return self.transition(
            record.get("state") or WORKING,
            goal=clean,
            dod=dod,
            revision=revision,
            **payload,
        )

    @_serialized
    def add_criteria(
        self,
        criteria: list[str],
        *,
        expected_revision: int | None = None,
        request_id: str | None = None,
    ) -> tuple[dict, list[str]]:
        """Append blocking acceptance criteria to the active goal (`@goal`).

        Criteria accumulate on the running goal — deduplicated, ids continue the
        c<n> sequence, everything else on the record is preserved. Each new
        criterion gets a derived verification plan (``plan_verification``); an
        empty plan is explicit and reported by ``gate_dod`` as missing — never a
        silent free pass (P1/A6). The edit bumps the revision.

        Same contract as update/remove (audit P2, 13.09.): a stale
        ``expected_revision`` raises :class:`CriterionConflict` instead of
        silently applying the request to a newer revision, and a repeated
        ``request_id`` replays idempotently instead of appending twice.
        """
        record = self.state()
        if request_id and request_id in (record.get("applied_requests") or []):
            return {**record, "replayed": True}, []
        if record.get("state") in TERMINAL_STATES:
            raise ValueError(
                f"state {record['state']} is terminal — begin a new goal to add criteria"
            )
        current = int(record.get("revision") or 0)
        if expected_revision is not None and current != int(expected_revision):
            raise CriterionConflict(current)
        dod = [dict(item) for item in (record.get("dod") or [])]
        seen = {str(item.get("text") or "").strip().lower() for item in dod}
        ids = [
            int(str(item.get("id") or "")[1:])
            for item in dod
            if str(item.get("id") or "").startswith("c") and str(item.get("id"))[1:].isdigit()
        ]
        next_id = max(ids, default=0)
        added: list[str] = []
        for text in criteria:
            clean = str(text).strip()
            if not clean or clean.lower() in seen:
                continue
            next_id += 1
            dod.append(
                {
                    "id": f"c{next_id}",
                    "text": clean,
                    "blocking": True,
                    "verification": plan_verification(
                        clean, tool_names=_registered_check_names(self.runtime)
                    ),
                    "status": "open",
                }
            )
            added.append(clean)
            seen.add(clean.lower())
        if not added:
            return record, []
        revision = current + 1
        payload = {
            k: v
            for k, v in record.items()
            if k not in ("state", "updated", "dod", "revision")
        }
        if request_id:
            seen = [str(r) for r in (record.get("applied_requests") or [])]
            payload["applied_requests"] = (seen + [request_id])[-50:]
        return (
            self.transition(
                record.get("state") or WORKING,
                dod=dod,
                revision=revision,
                **payload,
            ),
            added,
        )

    @_serialized
    def record_dod_outcomes(self, result, findings=None) -> None:
        """Persist per-criterion outcomes after a verification run (T3 fix).

        A criterion named by a blocking/major finding is ``failed``; every
        other criterion is ``verified`` — the gates just proved it. The
        outcome carries the Finding objects (which hold ``requirement``);
        the string lists on the result are for human display only.
        """
        record = self.state()
        if record.get("state") in TERMINAL_STATES:
            return
        named = {
            f.requirement
            for f in (findings or [])
            if getattr(f, "requirement", "")
        }
        blocking = {
            f.requirement
            for f in (findings or [])
            if getattr(f, "severity", "") in (BLOCKER, MAJOR)
            and getattr(f, "requirement", "")
        }
        dod = record.get("dod")
        if not isinstance(dod, list):
            return
        changed = False
        for item in dod:
            if not isinstance(item, dict):
                continue
            if item.get("id") in blocking:
                new = "failed"
            elif item.get("id") in named:
                new = "open"  # MINOR/INFO: not certified as-is
            else:
                new = "verified"
            if item.get("status") != new:
                item["status"] = new
                changed = True
        if changed:
            record["updated"] = time.time()
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with _path_lock(self.state_path):
                _atomic_write_text(
                    self.state_path, json.dumps(record, indent=2, ensure_ascii=False)
                )

    @_serialized
    def _criterion_edit(
        self,
        cid: str,
        mutate,
        *,
        expected_revision: int | None,
        request_id: str | None,
    ) -> dict:
        """Shared path for update/remove: terminal lock, revision fence, idempotency."""
        record = self.state()
        if request_id and request_id in (record.get("applied_requests") or []):
            return {**record, "replayed": True}
        if record.get("state") in TERMINAL_STATES:
            raise ValueError(
                f"state {record['state']} is terminal — criteria are frozen; "
                "start a new goal revision"
            )
        current = int(record.get("revision") or 0)
        if expected_revision is not None and current != int(expected_revision):
            raise CriterionConflict(current)
        dod = [dict(item) for item in (record.get("dod") or [])]
        target = next((item for item in dod if str(item.get("id")) == str(cid)), None)
        if target is None:
            raise ValueError(f"unknown criterion id: {cid}")
        mutate(target, dod)
        extra: dict = {}
        if request_id:
            seen = [str(r) for r in (record.get("applied_requests") or [])]
            extra["applied_requests"] = (seen + [request_id])[-50:]
        return self.transition(
            record.get("state") or WORKING,
            dod=dod,
            revision=current + 1,
            **extra,
        )

    def update_criterion(
        self,
        cid: str,
        *,
        text: str | None = None,
        blocking: bool | None = None,
        expected_revision: int | None = None,
        request_id: str | None = None,
    ) -> dict:
        """Edit one criterion in place (plan §5: revidierbarer Einzelkriterien-Editor).

        Only ``text``/``blocking`` are user-editable — ``status`` belongs to
        evidence, and there is deliberately no parameter that could flip it.
        """
        if text is None and blocking is None:
            raise ValueError("update needs text and/or blocking")

        def mutate(item: dict, _dod: list) -> None:
            if text is not None:
                clean = str(text).strip()
                if not clean:
                    raise ValueError("criterion text must not be empty")
                item["text"] = clean
                item["verification"] = plan_verification(
                    clean, tool_names=_registered_check_names(self.runtime)
                )
            if blocking is not None:
                item["blocking"] = bool(blocking)

        return self._criterion_edit(
            cid, mutate, expected_revision=expected_revision, request_id=request_id
        )

    def remove_criterion(
        self,
        cid: str,
        *,
        expected_revision: int | None = None,
        request_id: str | None = None,
    ) -> dict:
        """Drop one criterion (its evidence stays in the event store, untouched)."""

        def mutate(_item: dict, dod: list) -> None:
            dod[:] = [i for i in dod if str(i.get("id")) != str(cid)]

        return self._criterion_edit(
            cid, mutate, expected_revision=expected_revision, request_id=request_id
        )

    def move_criterion(
        self,
        cid: str,
        *,
        before: str | None = None,
        after: str | None = None,
        expected_revision: int | None = None,
        request_id: str | None = None,
    ) -> dict:
        """Reorder one criterion (plan §5: sortierbare Punkte).

        The ledger's own list IS the plan's order — a move is a list operation on
        ``dod`` and nothing else; no second ordering field, no UI-side copy. The
        target position is named by a NEIGHBOUR (``before``/``after``) rather than
        an index, so two clients that both moved something cannot silently
        disagree about what "slot 3" means. Like every other criterion edit it
        carries the revision fence and the idempotent ``request_id``, and it bumps
        the revision.
        """

        if bool(before) == bool(after):
            raise ValueError("move needs exactly one of before/after")
        anchor = str(before or after)

        def mutate(_item: dict, dod: list) -> None:
            ids = {str(i.get("id")) for i in dod}
            if anchor not in ids:
                raise ValueError(f"unknown anchor criterion id: {anchor}")
            moving = next(i for i in dod if str(i.get("id")) == str(cid))
            rest = [i for i in dod if str(i.get("id")) != str(cid)]
            index = next(n for n, i in enumerate(rest) if str(i.get("id")) == anchor)
            rest.insert(index if before else index + 1, moving)
            dod[:] = rest

        return self._criterion_edit(
            cid, mutate, expected_revision=expected_revision, request_id=request_id
        )

    @classmethod
    def sessions(cls, runtime: str | Path) -> list[dict]:
        """Every session contract on disk, newest first (panel overview)."""
        base = Path(runtime) / "completion"
        out: list[dict] = []
        if not base.exists():
            return out
        for path in sorted(base.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(record, dict):
                continue
            out.append(
                {
                    "session_id": str(record.get("session_id") or path.stem),
                    "state": record.get("state") or WORKING,
                    "goal": str(record.get("goal") or ""),
                    "updated": float(record.get("updated") or 0),
                    "revision": int(record.get("revision") or 0),
                }
            )
        return sorted(out, key=lambda row: row["updated"], reverse=True)

    @classmethod
    def latest(cls, runtime: str | Path) -> CompletionStore:
        """The store of the most recently updated contract (panel default).

        This is an explicit, named policy — not a hidden "last session" grab
        for verification context. The hook and the gates always use their own
        session-scoped store.
        """
        newest = cls.sessions(runtime)
        if newest:
            return cls(runtime, session_id=newest[0]["session_id"])
        return cls(runtime)

    # -- history / timeline -------------------------------------------------

    def _append(self, entry: dict) -> None:
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def record_request(self, request: CompletionRequest) -> None:
        self._append({"kind": "completion_request", **asdict(request)})

    def record_precheck(self, precheck) -> None:
        """Append one advisory precheck reading to the timeline (§91/JEV-13).

        Recorded *evidence of a reading*, never a certificate: the entry carries
        ``certifies: false``. A precheck that is not a
        :class:`CompletionPrecheck` (any mapping-shaped reading, e.g. the
        policy-layer one) is recorded as it is.
        """
        payload = precheck.as_dict() if hasattr(precheck, "as_dict") else dict(precheck)
        self._append({"kind": "precheck", **payload})

    def record_result(self, result: VerificationResult) -> None:
        self._append({"kind": "verification", **asdict(result)})

    def timeline(self, *, limit: int = 40) -> list[dict]:
        if not self.history_path.exists():
            return []
        rows: list[dict] = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[-limit:]

    def rejection_attempts(self, fingerprint: str) -> int:
        """How often this exact rejection already happened (§31)."""
        return sum(
            1
            for row in self.timeline(limit=500)
            if row.get("kind") == "verification"
            and row.get("status") == INCOMPLETE
            and row.get("fingerprint") == fingerprint
        )

    # -- metrics (§39) ------------------------------------------------------

    def metrics(self) -> dict:
        rows = self.timeline(limit=1000)
        requests = [r for r in rows if r.get("kind") == "completion_request"]
        rejections = [
            r
            for r in rows
            if r.get("kind") == "verification" and r.get("status") == INCOMPLETE
        ]
        verified = [
            r
            for r in rows
            if r.get("kind") == "verification" and r.get("status") == VERIFIED_COMPLETE
        ]
        return {
            "completion_requests": len(requests),
            "completion_rejections": len(rejections),
            "verified_completions": len(verified),
            "false_completion_prevented": len(rejections),
            "attempts_until_verified": (len(rejections) + len(verified)) if verified else None,
            "requirements_verified": sum(
                len(r.get("verified_requirements") or []) for r in verified
            ),
        }


# -- gates ------------------------------------------------------------------

_CHANGED_TODO = re.compile(r"(?i)\b(todo|fixme|xxx)\b")
_PLACEHOLDER = re.compile(
    r"(?i)\b(placeholder|fake(?:d)?\s+(?:data|response|result)|hardcoded\s+demo|dummy\s+data)\b"
)
_SKIPPED_TEST = re.compile(r"pytest\.mark\.(skip|xfail)|@unittest\.skip|it\.skip|test\.skip\(")
_DEBUG_PRINT = re.compile(r"^\s*print\(", re.MULTILINE)
_HARDCODED_TMP = re.compile(r"['\"]/tmp/[A-Za-z0-9_./-]+['\"]")


@dataclass
class RepoFacts:
    """Real repository state at verification time (§14)."""

    root: Path
    changed_files: list[str] = field(default_factory=list)
    diff_text: str = ""
    new_files: list[str] = field(default_factory=list)
    unreadable: bool = False

    @classmethod
    def collect(cls, root: str | Path, base: str = "") -> RepoFacts:
        """Snapshot the task diff.

        Against ``base`` (the goal's recorded task base) when given — this sees
        changes that were already committed during the task. Untracked files
        always count as changed: a new file that ``git diff HEAD`` cannot show
        is exactly the kind a completion gate must not miss.
        """
        import subprocess

        root = Path(root)
        facts = cls(root=root)
        try:
            diff_ref = base or "HEAD"
            diff = subprocess.run(  # noqa: S603,S607 — fixed argv
                ["git", "-C", str(root), "diff", "--name-only", diff_ref],
                capture_output=True,
                text=True,
                timeout=30,
            )
            facts.changed_files = [line for line in diff.stdout.splitlines() if line.strip()]
            full = subprocess.run(  # noqa: S603,S607 — fixed argv
                ["git", "-C", str(root), "diff", diff_ref],
                capture_output=True,
                text=True,
                timeout=30,
            )
            facts.diff_text = full.stdout
            if base:
                # committed task changes are invisible to `diff HEAD` — take
                # their text from the range diff too (fake-completion/security
                # gates scan diff_text)
                head_full = subprocess.run(  # noqa: S603,S607 — fixed argv
                    ["git", "-C", str(root), "diff", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                facts.diff_text += head_full.stdout
            status = subprocess.run(  # noqa: S603,S607 — fixed argv
                ["git", "-C", str(root), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            staged_new = [
                line[3:].strip()
                for line in status.stdout.splitlines()
                if line.startswith(("A ", "AM"))
            ]
            # `status` collapses untracked directories to `?? src/` — the file
            # list needs `ls-files --others`, or whole new modules stay invisible
            others = subprocess.run(  # noqa: S603,S607 — fixed argv
                ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            untracked = [line.strip() for line in others.stdout.splitlines() if line.strip()]
            facts.new_files = sorted(set(staged_new) | set(untracked))
            # untracked files are real task changes — never invisible to gates
            for path in facts.new_files:
                if path and path not in facts.changed_files:
                    facts.changed_files.append(path)
        except Exception:
            # a broken repo query is an unverified state, not an empty success
            facts.diff_text = ""
            facts.changed_files = []
            facts.new_files = []
            facts.root = root
            facts.unreadable = True
        return facts


# -- §91/JEV-13 completion precheck ------------------------------------------- #
#
# ABNAHME Teil XV: "Jev kann fehlende/stale Completion-Evidence signalisieren,
# aber niemals allein Completion zertifizieren."
#
# The gate runner below stays the SOLE authority for certification. This
# precheck only *signals* that completion evidence is missing or stale: it can
# neither certify, nor un-block, nor downgrade a verdict. A completion whose
# only support is a precheck is refused by ``GateRunner.gate_precheck`` with the
# machine-readable reason ``completion-precheck-only-source``, and a precheck
# that claims to certify is refused outright.

PRECHECK_MISSING_EVIDENCE = "completion-precheck-missing-evidence"
PRECHECK_STALE_EVIDENCE = "completion-precheck-stale-evidence"
PRECHECK_ONLY_SOURCE = "completion-precheck-only-source"
PRECHECK_CERTIFIES = "completion-precheck-certifies"

#: Advisory signals read from the completion contract itself (structural).
SIGNAL_MISSING_EVIDENCE = "completion_evidence_missing"
SIGNAL_STALE_EVIDENCE = "completion_evidence_stale"
SIGNAL_FRESH_EVIDENCE = "completion_evidence_fresh"
SIGNAL_NOT_CERTIFIABLE = "completion_evidence_not_certifiable"
SIGNAL_AGENT_CLAIMS = "agent_claims_completion"
SIGNAL_NO_CLAIM = "no_completion_claim"
SIGNAL_NO_ADVISORY = "no_advisory_signal"

#: Advisory signals read from a System-One evaluation — hedged on purpose, a
#: model reading is never a fact ("may_be").
SIGNAL_MODEL_EVIDENCE_MISSING = "completion_evidence_may_be_missing"
SIGNAL_MODEL_EVIDENCE_STALE = "completion_evidence_may_be_stale"
SIGNAL_INDEPENDENT = "independent_verification_required"


@dataclass(frozen=True)
class CompletionPrecheck:
    """Advisory reading of completion evidence (§91/JEV-13) — never a verdict.

    ``certifies`` is ``False`` by construction and ``certifies_completion()``
    always returns ``False``: the only authority that may certify a completion
    is the gate runner over runner-executed evidence.
    """

    signals: tuple[str, ...] = ()
    #: Machine-readable reason codes derived from the contract/evidence binding.
    reasons: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()
    advisory_only: bool = True
    certifies: bool = False
    provider: str = ""
    live_jev: bool = False
    revision: int = 0
    fingerprint: str = ""

    def certifies_completion(self) -> bool:
        """Always ``False`` — a precheck never certifies completion (§91)."""
        return False

    def as_dict(self) -> dict:
        return {
            "signals": list(self.signals),
            "reasons": list(self.reasons),
            "missing": list(self.missing),
            "stale": list(self.stale),
            "advisory_only": bool(self.advisory_only),
            "certifies": bool(self.certifies),
            "certifies_completion": self.certifies_completion(),
            "provider": self.provider,
            "live_jev": bool(self.live_jev),
            "revision": int(self.revision),
            "fingerprint": self.fingerprint,
        }


def _precheck_noul(evaluation, question_id: str) -> float | None:
    """A Noul reading of an evaluation, or ``None`` — never a made-up value."""
    getter = getattr(evaluation, "noul", None)
    if not callable(getter):
        return None
    try:
        value: Any = getter(question_id)
    except Exception:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def completion_precheck(
    *,
    request: CompletionRequest | None = None,
    evidence: Iterable[RunEvidence] = (),
    revision: int = 0,
    workspace: str = "",
    session: str = "",
    flags=None,
    evaluation=None,
    live=None,
) -> CompletionPrecheck | None:
    """Signal missing/stale completion evidence — never a completion verdict.

    ``None`` means "no advisory, keep your existing completion path": with §98
    flags passed and the ``system_one.completion_precheck`` flag (or the master
    flag) off, nothing is computed at all. A caller that asks for the reading
    without flags gets it without any System-One rollout — the structural
    evidence reading needs no provider and no model.

    The reading is binding-aware: evidence from another revision, workspace or
    session counts as *stale*, never as proof, and a declared-but-unavailable
    check is reported as not-certifiable instead of green. A stale System-One
    evaluation contributes nothing (§92/JEV-14): its signals are inert, while
    the structural reading stands on its own.
    """
    if flags is not None and (
        not getattr(flags, "enabled", False)
        or not getattr(flags, "completion_precheck", False)
    ):
        return None
    runs = [run for run in (evidence or ()) if run is not None]
    claims = [str(claim) for claim in (getattr(request, "claimed_completed", []) or [])]
    wanted_revision = int(revision or 0)
    wanted_workspace = str(workspace or "").strip()
    wanted_session = str(session or "").strip()
    fresh: list[RunEvidence] = []
    stale: list[str] = []
    for run in runs:
        label = str(run.cmd or run.name)
        if wanted_revision and int(getattr(run, "revision", 0) or 0) != wanted_revision:
            stale.append(label)
        elif wanted_workspace and str(run.workspace or "").strip() != wanted_workspace:
            stale.append(label)
        elif wanted_session and str(run.session or "").strip() != wanted_session:
            stale.append(label)
        else:
            fresh.append(run)
    strong = [run for run in fresh if run.source == "runner" and run.passed]
    unavailable = [run for run in runs if str(run.status or "") == NOT_CERTIFIABLE]
    signals: list[str] = []
    missing: list[str] = []
    if claims:
        signals.append(SIGNAL_AGENT_CLAIMS)
    else:
        signals.append(SIGNAL_NO_CLAIM)
    if claims and not strong:
        # Stale evidence is reported as stale (it exists, it expired) — only a
        # claim without any bound evidence at all counts as missing.
        if fresh or not stale:
            missing.extend(claims)
    if stale:
        signals.append(SIGNAL_STALE_EVIDENCE)
    if missing:
        signals.append(SIGNAL_MISSING_EVIDENCE)
    if unavailable:
        signals.append(SIGNAL_NOT_CERTIFIABLE)
    if claims and strong and not stale:
        signals.append(SIGNAL_FRESH_EVIDENCE)

    provider = ""
    live_jev = False
    if evaluation is not None:
        stale_answer = False
        if live is not None:
            try:
                from .system_one_policy import is_stale

                stale_answer = bool(is_stale(evaluation, live=live))
            except Exception:
                stale_answer = True  # an unreadable staleness check is inert
        if not stale_answer:
            provider = str(getattr(evaluation, "provider", "") or "")
            live_jev = bool(getattr(evaluation, "live_jev", False))
            current = _precheck_noul(evaluation, "completion.evidence_current")
            if current is not None and current < 0.5:
                signals.append(SIGNAL_MODEL_EVIDENCE_MISSING)
            matches = _precheck_noul(evaluation, "completion.evidence_matches_revision")
            if matches is not None and matches < 0.5:
                signals.append(SIGNAL_MODEL_EVIDENCE_STALE)
            independent = _precheck_noul(evaluation, "completion.independent_verification_required")
            if independent is not None and independent >= 0.5:
                signals.append(SIGNAL_INDEPENDENT)

    reasons: list[str] = []
    if missing:
        reasons.append(PRECHECK_MISSING_EVIDENCE)
    if stale:
        reasons.append(PRECHECK_STALE_EVIDENCE)
    if not signals:
        signals.append(SIGNAL_NO_ADVISORY)
    unique_signals = tuple(dict.fromkeys(signals))
    return CompletionPrecheck(
        signals=unique_signals,
        reasons=tuple(reasons),
        missing=tuple(missing[:20]),
        stale=tuple(stale[:20]),
        certifies=False,  # System One never certifies completion (§91/JEV-13)
        provider=provider,
        live_jev=live_jev,
        revision=wanted_revision,
        fingerprint=fingerprint_for(list(reasons) + list(unique_signals)),
    )


# --------------------------------------------------------------------------- #
# §9 — Jev Goal Completion Preflight                                           #
# --------------------------------------------------------------------------- #
#
# §9.1 pipeline, in this exact order:
#
#     Completion Claim -> CompletionLedger -> Hard Structural Checks
#       -> Jev Criterion Preflight -> Targeted Verifier / System-2
#       -> Independent Evidence Review -> Deterministic CompletionGate -> completed
#
# Rules this section carries:
#
# * the **hard checks are deterministic and come first** — a criterion whose
#   structure is missing can never pass, and no model answer may overwrite that
#   (§9.3). With every criterion structurally refused, no provider is called at
#   all.
# * the semantic questions are **atomic and criterion-level** (§9.4/§9.6):
#   Jev never answers "is the whole project done?", the parents are aggregated
#   here, in plain Python (:func:`aggregate_criteria`).
# * the preflight is **advisory** (§9.8): only the runtime code sets completion,
#   only a runner-executed check certifies, and a preflight that tries to
#   certify is refused by :meth:`GateRunner.gate_precheck`.
# * honest gaps are reported as gaps: an unread question is not a "no", and a
#   provider that never answered leaves ``evaluated=False`` instead of a
#   fabricated reading.

#: §9.3 — the nine deterministic checks, by name.
HARD_CHECKS: tuple[str, ...] = (
    "evidence_ref_exists",
    "run_exists",
    "criterion_id_exists",
    "goal_revision_matches",
    "workspace_fingerprint_matches",
    "artifact_exists",
    "runner_result_parsable",
    "required_platform_present",
    "evidence_newer_than_code_change",
)

#: The machine-readable refusal code each hard check produces.
HARD_REASONS: dict[str, str] = {
    "evidence_ref_exists": "evidence-ref-missing",
    "run_exists": "run-missing",
    "criterion_id_exists": "criterion-id-missing",
    "goal_revision_matches": "goal-revision-mismatch",
    "workspace_fingerprint_matches": "workspace-fingerprint-mismatch",
    "artifact_exists": "artifact-missing",
    "runner_result_parsable": "runner-result-unparsable",
    "required_platform_present": "required-platform-missing",
    "evidence_newer_than_code_change": "evidence-older-than-relevant-change",
}

#: The verdicts a runner result may carry. Anything else is unparsable — an
#: unknown status is not a pass and not a failure, it is unusable evidence.
RUN_STATUSES: tuple[str, ...] = ("passed", "failed", "unknown", NOT_CERTIFIABLE)

#: §9.5 — the shortcut codes. Each one is a *detected self-certification*.
SHORTCUT_OWN_SUCCESS_MARKER = "own-success-marker"
SHORTCUT_FILE_EXISTS_UNCHECKED = "file-exists-content-unchecked"
SHORTCUT_UNIT_TEST_AS_LIVE = "unit-test-as-live-use"
SHORTCUT_MOCK_AS_LIVE = "mock-provider-as-real-provider"
SHORTCUT_OLD_GREEN_RUN = "old-green-run-on-old-revision"
SHORTCUT_FOREIGN_WORKSPACE = "evidence-from-another-workspace"
SHORTCUT_SELF_CERTIFICATION = "criterion-supported-only-by-its-own-completion-text"
SHORTCUT_PASS_WITHOUT_CONTRACT = "pass-marker-without-outcome-contract"

SHORTCUT_CODES: tuple[str, ...] = (
    SHORTCUT_OWN_SUCCESS_MARKER,
    SHORTCUT_FILE_EXISTS_UNCHECKED,
    SHORTCUT_UNIT_TEST_AS_LIVE,
    SHORTCUT_MOCK_AS_LIVE,
    SHORTCUT_OLD_GREEN_RUN,
    SHORTCUT_FOREIGN_WORKSPACE,
    SHORTCUT_SELF_CERTIFICATION,
    SHORTCUT_PASS_WITHOUT_CONTRACT,
)

#: Shortcuts that refuse a criterion outright (the evidence cannot certify it).
HARD_SHORTCUTS: tuple[str, ...] = (
    SHORTCUT_OWN_SUCCESS_MARKER,
    SHORTCUT_FILE_EXISTS_UNCHECKED,
    SHORTCUT_UNIT_TEST_AS_LIVE,
    SHORTCUT_MOCK_AS_LIVE,
    SHORTCUT_OLD_GREEN_RUN,
    SHORTCUT_FOREIGN_WORKSPACE,
    SHORTCUT_SELF_CERTIFICATION,
    SHORTCUT_PASS_WITHOUT_CONTRACT,
)

#: Evidence types that describe a *claim* rather than an executed check.
CLAIM_EVIDENCE_TYPES: tuple[str, ...] = ("claim_text", "completion_text", "self_report")
MOCK_EVIDENCE_TYPES: tuple[str, ...] = ("mock", "fake", "stub", "simulated")
FILE_ONLY_EVIDENCE_TYPES: tuple[str, ...] = ("file_exists", "artifact", "presence")


class EvidenceFit:
    """§9.4's optional Choice answer: how well the evidence fits (§9.4)."""

    OPTIONS: tuple[str, ...] = (
        "direct",
        "partial",
        "irrelevant",
        "contradictory",
        "uncertain",
    )
    #: Fits that escalate a criterion rather than passing it.
    ESCALATING: tuple[str, ...] = ("irrelevant", "contradictory", "uncertain")


@dataclass
class LedgerCriterion:
    """§9.2 — one criterion of the completion ledger, fully evidence-bound.

    Every field is either measured (revisions, fingerprints, timestamps,
    booleans) or an explicit declaration by the caller. Nothing here is a model
    reading: the model only ever *reads* this structure.
    """

    criterion_id: str
    goal_revision: int = 0
    required: bool = True
    requirement_summary: str = ""
    implementation_status: str = "unknown"  # open|implemented|failed|unknown
    evidence_refs: tuple[str, ...] = ()
    evidence_types: tuple[str, ...] = ()
    evidence_revision: int = 0
    evidence_workspace_fingerprint: str = ""
    independent_required: bool = False
    independent_present: bool = False
    live_required: bool = False
    live_present: bool = False
    last_relevant_change: float = 0.0
    contradicting_evidence_refs: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(str(ref) for ref in self.evidence_refs))
        object.__setattr__(
            self, "evidence_types", tuple(str(kind) for kind in self.evidence_types)
        )
        object.__setattr__(
            self,
            "contradicting_evidence_refs",
            tuple(str(ref) for ref in self.contradicting_evidence_refs),
        )
        object.__setattr__(self, "blockers", tuple(str(item) for item in self.blockers))

    def as_dict(self) -> dict:
        return {
            "criterion_id": self.criterion_id,
            "goal_revision": int(self.goal_revision),
            "required": bool(self.required),
            "requirement_summary": self.requirement_summary,
            "implementation_status": self.implementation_status,
            "evidence_refs": list(self.evidence_refs),
            "evidence_types": list(self.evidence_types),
            "evidence_revision": int(self.evidence_revision),
            "evidence_workspace_fingerprint": self.evidence_workspace_fingerprint,
            "independent_required": bool(self.independent_required),
            "independent_present": bool(self.independent_present),
            "live_required": bool(self.live_required),
            "live_present": bool(self.live_present),
            "last_relevant_change": float(self.last_relevant_change),
            "contradicting_evidence_refs": list(self.contradicting_evidence_refs),
            "blockers": list(self.blockers),
        }


@dataclass
class CompletionLedger:
    """§9.2 — the per-criterion ledger plus its global fields.

    ``mandatory_criterion_count`` is *derived in code* from the criteria, never
    supplied by a model (a count a model could set is a count a model could
    lower).
    """

    criteria: tuple[LedgerCriterion, ...] = ()
    unknown_side_effects: tuple[str, ...] = ()
    open_blockers: tuple[str, ...] = ()
    goal_revision: int = 0
    workspace_fingerprint: str = ""
    pending_external_operations: tuple[str, ...] = ()
    unresolved_findings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "criteria", tuple(self.criteria or ()))
        for name in (
            "unknown_side_effects",
            "open_blockers",
            "pending_external_operations",
            "unresolved_findings",
        ):
            object.__setattr__(
                self, name, tuple(str(item) for item in (getattr(self, name) or ()))
            )

    @property
    def mandatory_criterion_count(self) -> int:
        """How many criteria are *blocking* — a code aggregation, not a claim."""
        return sum(1 for criterion in self.criteria if criterion.required)

    def mandatory(self) -> tuple[LedgerCriterion, ...]:
        return tuple(criterion for criterion in self.criteria if criterion.required)

    def criterion(self, criterion_id: str) -> LedgerCriterion | None:
        for criterion in self.criteria:
            if criterion.criterion_id == criterion_id:
                return criterion
        return None

    def as_dict(self) -> dict:
        return {
            "criteria": [criterion.as_dict() for criterion in self.criteria],
            "mandatory_criterion_count": self.mandatory_criterion_count,
            "open_blockers": list(self.open_blockers),
            "unknown_side_effects": list(self.unknown_side_effects),
            "goal_revision": int(self.goal_revision),
            "workspace_fingerprint": self.workspace_fingerprint,
            "pending_external_operations": list(self.pending_external_operations),
            "unresolved_findings": list(self.unresolved_findings),
        }


@dataclass(frozen=True)
class HardCheckReport:
    """The deterministic verdict of one criterion's structural checks (§9.3)."""

    criterion_id: str
    refusals: tuple[str, ...] = ()
    checks: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True only when every applicable check passed."""
        return not self.refusals

    def as_dict(self) -> dict:
        return {
            "criterion_id": self.criterion_id,
            "ok": self.ok,
            "refusals": list(self.refusals),
            "checks": dict(self.checks),
            "details": dict(self.details),
        }


def hard_checks(
    criterion: LedgerCriterion,
    *,
    runs: Mapping[str, RunEvidence],
    workspace_root: str | Path = ".",
    platform: str = "",
    known_criterion_ids: set[str] | None = None,
    live_goal_revision: int | None = None,
    live_workspace_fingerprint: str | None = None,
) -> HardCheckReport:
    """Run the nine §9.3 checks for one criterion — before any Jev call.

    Missing structure **refuses**: the report's ``refusals`` are the reason
    codes, and no later model reading may clear them.
    """
    runs = dict(runs or {})
    refusals: list[str] = []
    checks: dict[str, bool] = {}
    details: dict = {}
    refs = tuple(str(ref) for ref in criterion.evidence_refs)

    def mark(name: str, ok: bool, detail: str = "") -> None:
        checks[name] = ok
        if not ok:
            refusals.append(HARD_REASONS[name])
            if detail:
                details[name] = detail

    mark(
        "criterion_id_exists",
        bool(str(criterion.criterion_id).strip())
        and (known_criterion_ids is None or criterion.criterion_id in set(known_criterion_ids)),
        f"unknown criterion id: {criterion.criterion_id!r}",
    )
    mark(
        "evidence_ref_exists",
        bool(refs) or not criterion.required,
        "the criterion declares no evidence reference",
    )
    missing_runs = [ref for ref in refs if ref not in runs]
    mark("run_exists", not missing_runs, f"no run/evidence for ref(s): {missing_runs}")
    # "goal_revision matches?" covers both sides of the binding: the criterion
    # was written against the live revision, *and* its evidence was produced
    # against it. A green run from an earlier revision is structurally stale
    # (§9.3/§9.5), whatever the model later says.
    revision_matches = live_goal_revision is None or (
        int(criterion.goal_revision) == int(live_goal_revision)
        and (
            not int(criterion.evidence_revision)
            or int(criterion.evidence_revision) == int(live_goal_revision)
        )
    )
    mark(
        "goal_revision_matches",
        revision_matches,
        (
            f"criterion revision {criterion.goal_revision} / evidence revision "
            f"{criterion.evidence_revision} != live {live_goal_revision}"
        ),
    )
    mark(
        "workspace_fingerprint_matches",
        live_workspace_fingerprint is None
        or str(criterion.evidence_workspace_fingerprint) == str(live_workspace_fingerprint),
        (
            f"evidence workspace {criterion.evidence_workspace_fingerprint!r} != "
            f"live {live_workspace_fingerprint!r}"
        ),
    )

    used = [runs[ref] for ref in refs if ref in runs]
    unparsable = [
        str(run.run_id or run.name)
        for run in used
        if str(run.status or "") and str(run.status) not in RUN_STATUSES
    ]
    mark("runner_result_parsable", not unparsable, f"unparsable result(s): {unparsable}")

    root = Path(workspace_root)
    missing_artifacts: list[str] = []
    for run in used:
        artifact = str(run.artifact or "").strip()
        if artifact and not (root / artifact).exists():
            missing_artifacts.append(artifact)
    mark("artifact_exists", not missing_artifacts, f"missing artifact(s): {missing_artifacts}")

    wrong_platform: list[str] = []
    if criterion.live_required and platform:
        for run in used:
            declared = str(run.platform or "").strip()
            if declared != str(platform):
                wrong_platform.append(str(run.run_id or run.name) or declared or "<unstamped>")
    mark(
        "required_platform_present",
        not wrong_platform,
        f"live evidence without the required platform {platform!r}: {wrong_platform}",
    )

    stale = [
        str(run.run_id or run.name)
        for run in used
        if float(run.ts or 0.0) < float(criterion.last_relevant_change or 0.0)
    ]
    mark(
        "evidence_newer_than_code_change",
        not stale,
        f"evidence older than the last relevant change: {stale}",
    )
    return HardCheckReport(
        criterion_id=criterion.criterion_id,
        refusals=tuple(dict.fromkeys(refusals)),
        checks=checks,
        details=details,
    )


def detect_shortcuts(
    criterion: LedgerCriterion,
    *,
    runs: Mapping[str, RunEvidence],
    claim_text: str = "",
    completion_text: str = "",
) -> tuple[str, ...]:
    """§9.5 — detect the self-certification shortcuts of one criterion.

    Deterministic on purpose: each shortcut is a *shape* the ledger reveals
    (a mock carried as a live proof, a green run on an old revision, a
    criterion whose only support is its own completion text), not a judgement.
    """
    runs = dict(runs or {})
    used = [runs[ref] for ref in criterion.evidence_refs if ref in runs]
    found: list[str] = []
    evidence_types = {
        str(kind) for kind in criterion.evidence_types
    } | {str(run.evidence_type) for run in used if str(run.evidence_type)}

    if evidence_types & set(CLAIM_EVIDENCE_TYPES):
        found.append(SHORTCUT_OWN_SUCCESS_MARKER)
        claim = str(claim_text or "").strip()
        completion = str(completion_text or "").strip()
        if claim and claim == completion and used and all(
            str(run.evidence_type) in CLAIM_EVIDENCE_TYPES for run in used
        ):
            found.append(SHORTCUT_SELF_CERTIFICATION)

    if evidence_types & set(FILE_ONLY_EVIDENCE_TYPES) or any(
        str(run.evidence_type) in FILE_ONLY_EVIDENCE_TYPES and not str(run.cmd or "").strip()
        for run in used
    ):
        found.append(SHORTCUT_FILE_EXISTS_UNCHECKED)

    if criterion.live_required and not criterion.live_present:
        if evidence_types & set(MOCK_EVIDENCE_TYPES) or any(
            run.source != "runner" for run in used
        ):
            found.append(SHORTCUT_MOCK_AS_LIVE)
        if "unit_test" in evidence_types:
            found.append(SHORTCUT_UNIT_TEST_AS_LIVE)

    for run in used:
        if int(run.revision or 0) and int(run.revision) != int(criterion.goal_revision):
            found.append(SHORTCUT_OLD_GREEN_RUN)
            break
    for run in used:
        declared = str(run.workspace or "").strip()
        if declared and str(criterion.evidence_workspace_fingerprint or "").strip():
            if declared != str(criterion.evidence_workspace_fingerprint):
                found.append(SHORTCUT_FOREIGN_WORKSPACE)
                break
    for run in used:
        if run.source == "events" and run.passed and not str(run.outcome_contract or "").strip():
            found.append(SHORTCUT_PASS_WITHOUT_CONTRACT)
            break
    return tuple(dict.fromkeys(found))


def aggregate_criteria(leaves) -> str:
    """§9.6 — aggregate leaf verdicts into a parent status, in code.

    ``refuse`` dominates ``escalate``, which dominates ``open``; a parent is
    only ``pass`` when every leaf passed. An empty parent is ``unknown``.
    """
    decisions = [str(item.get("decision", "")) for item in leaves]
    if not decisions:
        return "unknown"
    if "refuse" in decisions:
        return "refuse"
    if "escalate" in decisions:
        return "escalate"
    if any(decision not in ("pass",) for decision in decisions):
        return "open"
    return "pass"


def build_ledger_summary(
    criteria,
    *,
    verdicts,
    unknown_side_effects=(),
    unresolved_findings=(),
    changed_after_verification=(),
) -> dict:
    """§9.7 — the aggregated 9-state summary the second sanity pass reads.

    Built from the ledger and the verdicts, never asked of a model: Jev may
    *read* this summary, it may not compute it.
    """
    by_id = {str(item.get("criterion_id")): str(item.get("decision")) for item in verdicts}
    summary: dict[str, list[str]] = {
        "verified": [],
        "failed": [],
        "unknown": [],
        "stale": [],
        "mock_only": [],
        "blocked": [],
        "unknown_side_effects": [str(item) for item in unknown_side_effects],
        "unresolved_p0_p1": [str(item) for item in unresolved_findings],
        "changed_after_verification": [str(item) for item in changed_after_verification],
    }
    for criterion in criteria:
        decision = by_id.get(str(criterion.criterion_id), "")
        if decision == "pass":
            summary["verified"].append(criterion.criterion_id)
        elif decision == "refuse":
            summary["failed"].append(criterion.criterion_id)
        elif decision == "escalate":
            summary["blocked"].append(criterion.criterion_id)
        else:
            summary["unknown"].append(criterion.criterion_id)
    return summary


@dataclass(frozen=True)
class CriterionVerdict:
    """The advisory verdict of one criterion: decision + the reasons it rests on."""

    criterion_id: str
    decision: str  # pass | refuse | escalate | unknown
    reasons: tuple[str, ...] = ()
    hard_refusals: tuple[str, ...] = ()
    shortcut_codes: tuple[str, ...] = ()
    evidence_fit: str = ""
    readings: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.decision == "pass"

    def as_dict(self) -> dict:
        return {
            "criterion_id": self.criterion_id,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "hard_refusals": list(self.hard_refusals),
            "shortcut_codes": list(self.shortcut_codes),
            "evidence_fit": self.evidence_fit,
            "readings": dict(self.readings),
        }


@dataclass(frozen=True)
class CompletionPreflight:
    """§9.1 — the preflight reading of a completion claim. Never a certificate.

    ``certifies`` is ``False`` by construction and
    :meth:`certifies_completion` always returns ``False``: the deterministic
    gate over runner-executed evidence is the only authority (§9.8).
    ``evaluated`` states honestly whether a provider reading was obtained at
    all — a preflight that only ran the structural checks says so.
    """

    ledger: CompletionLedger
    verdicts: tuple[CriterionVerdict, ...] = ()
    signals: tuple[str, ...] = ()
    #: Machine-readable reasons for the gate's message (hard refusals,
    #: shortcut codes, escalation reasons).
    reasons: tuple[str, ...] = ()
    shortcut_codes: tuple[str, ...] = ()
    hierarchy: str = "unknown"
    sanity: dict = field(default_factory=dict)
    provider: str = ""
    provider_mode: str = ""
    live_jev: bool = False
    advisory_only: bool = True
    certifies: bool = False
    revision: int = 0
    workspace_fingerprint: str = ""
    evaluated: bool = False
    fingerprint: str = ""

    def certifies_completion(self) -> bool:
        """Always ``False`` — a preflight never certifies completion (§9.8)."""
        return False

    def as_dict(self) -> dict:
        return {
            "verdicts": [verdict.as_dict() for verdict in self.verdicts],
            "signals": list(self.signals),
            "reasons": list(self.reasons),
            "shortcut_codes": list(self.shortcut_codes),
            "hierarchy": self.hierarchy,
            "sanity": dict(self.sanity),
            "provider": self.provider,
            "provider_mode": self.provider_mode,
            "live_jev": bool(self.live_jev),
            "advisory_only": bool(self.advisory_only),
            "certifies": bool(self.certifies),
            "certifies_completion": self.certifies_completion(),
            "revision": int(self.revision),
            "workspace_fingerprint": self.workspace_fingerprint,
            "evaluated": bool(self.evaluated),
            "fingerprint": self.fingerprint,
            "ledger": self.ledger.as_dict(),
        }


#: §9.4 — the Noul reading that only has meaning for a *live* requirement.
LIVE_MOCK_READING = "completion_preflight.is_live_requirement_supported_only_by_mock"

#: The Noul readings that mark a criterion as needing a closer look.
ESCALATING_READINGS: tuple[str, ...] = (
    "completion_preflight.does_contradicting_evidence_materially_challenge_pass",
    "completion_preflight.is_criterion_semantically_unresolved",
    LIVE_MOCK_READING,
    "completion_preflight.is_evidence_only_indirect",
    "completion_preflight.is_scope_of_proof_too_narrow",
)
NEGATIVE_READINGS: tuple[str, ...] = (
    "completion_preflight.does_evidence_directly_support_requirement",
    "completion_preflight.does_evidence_cover_current_behavior",
)


def assert_preflight_readings_registered() -> None:
    """Every reading this module interprets must exist in the §9.4 pack.

    A reading that is not in the registered pack would be silently ``None``
    forever — this guard turns that drift into a loud contract error.
    """
    known = set(COMPLETION_SEMANTIC_IDS)
    unknown = sorted(set(ESCALATING_READINGS + NEGATIVE_READINGS) - known)
    if unknown:
        raise SystemOneContractError(
            f"preflight reads question(s) that are not in the §9.4 pack: {unknown}"
        )


def _preflight_noul(evaluation, question_id: str) -> float | None:
    """One Noul reading of the preflight evaluation, or ``None`` if unasked."""
    if evaluation is None:
        return None
    answer = evaluation.answer(question_id)
    if answer is None:
        return None
    value = getattr(answer, "value", None)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _preflight_choice(evaluation, question_id: str) -> str:
    answer = evaluation.answer(question_id) if evaluation is not None else None
    choice = getattr(answer, "choice", None)
    return str(choice) if choice is not None else ""


def _preflight_state(*, ledger: CompletionLedger, criteria, runs) -> dict:
    """The minimal, sanitizer-safe state the semantic questions are asked over.

    Uses the allow-listed ``completion`` key (see
    :data:`skaldir.system_one_sanitizer.DEFAULT_ALLOWED_KEYS`) so the payload
    survives the sanitizer unchanged.
    """
    return {
        "step": 0,
        "completion": {
            "goal_revision": int(ledger.goal_revision),
            "workspace_fingerprint": ledger.workspace_fingerprint,
            "mandatory_criterion_count": ledger.mandatory_criterion_count,
            "criteria": [
                {
                    "criterion_id": criterion.criterion_id,
                    "required": bool(criterion.required),
                    "requirement_summary": criterion.requirement_summary,
                    "implementation_status": criterion.implementation_status,
                    "evidence_types": list(criterion.evidence_types),
                    "evidence_revision": int(criterion.evidence_revision),
                    "live_required": bool(criterion.live_required),
                    "live_present": bool(criterion.live_present),
                    "independent_required": bool(criterion.independent_required),
                    "independent_present": bool(criterion.independent_present),
                    "has_run": any(ref in runs for ref in criterion.evidence_refs),
                    "contradicting_evidence_refs": list(
                        criterion.contradicting_evidence_refs
                    ),
                }
                for criterion in criteria
            ],
        },
    }


def _verdict_for(
    criterion: LedgerCriterion,
    *,
    report: HardCheckReport,
    shortcuts: tuple[str, ...],
    evaluation,
) -> CriterionVerdict:
    """Combine the deterministic checks with (never over) the model reading."""
    hard_shortcuts = tuple(code for code in shortcuts if code in HARD_SHORTCUTS)
    if not report.ok:
        # §9.3: missing structure refuses — whatever the model answered
        return CriterionVerdict(
            criterion_id=criterion.criterion_id,
            decision="refuse",
            reasons=tuple(report.refusals),
            hard_refusals=tuple(report.refusals),
            shortcut_codes=shortcuts,
        )
    if hard_shortcuts:
        return CriterionVerdict(
            criterion_id=criterion.criterion_id,
            decision="refuse",
            reasons=hard_shortcuts,
            shortcut_codes=shortcuts,
        )

    applicable = tuple(
        question_id
        for question_id in ESCALATING_READINGS
        # "is a live requirement only mock-supported?" is only material when
        # the criterion really has a live requirement — otherwise it is a
        # reading about a requirement that does not exist.
        if question_id != LIVE_MOCK_READING or criterion.live_required
    )
    readings = {
        question_id: _preflight_noul(evaluation, question_id)
        for question_id in applicable + NEGATIVE_READINGS
    }
    evidence_fit = _preflight_choice(evaluation, "completion_preflight.evidence_fit")
    reasons: list[str] = []
    for question_id in applicable:
        value = readings.get(question_id)
        if value is not None and value >= 0.5:
            reasons.append(question_id)
    for question_id in NEGATIVE_READINGS:
        value = readings.get(question_id)
        if value is not None and value < 0.5:
            reasons.append(question_id)
    if evidence_fit in EvidenceFit.ESCALATING:
        reasons.append(f"evidence_fit:{evidence_fit}")
    if criterion.live_required and not criterion.live_present:
        reasons.append("live-requirement-unsupported")
    if criterion.independent_required and not criterion.independent_present:
        reasons.append("independent-verification-missing")
    if criterion.blockers:
        reasons.append("criterion-blockers-present")
    if criterion.contradicting_evidence_refs:
        reasons.append("contradicting-evidence-present")

    unique = tuple(dict.fromkeys(reasons))
    if unique:
        return CriterionVerdict(
            criterion_id=criterion.criterion_id,
            decision="escalate",
            reasons=unique,
            shortcut_codes=shortcuts,
            evidence_fit=evidence_fit,
            readings=readings,
        )
    return CriterionVerdict(
        criterion_id=criterion.criterion_id,
        decision="pass",
        reasons=(),
        shortcut_codes=shortcuts,
        evidence_fit=evidence_fit,
        readings=readings,
    )


async def completion_preflight(
    *,
    ledger: CompletionLedger,
    runs: Mapping[str, RunEvidence] | None = None,
    goal_revision: int | None = None,
    workspace_fingerprint: str = "",
    platform: str = "",
    workspace_root: str | Path = ".",
    service=None,
    context=None,
    live_goal_revision: int | None = None,
    live_workspace_fingerprint: str | None = None,
    known_criterion_ids: set[str] | None = None,
    claim_text: str = "",
    completion_text: str = "",
    flags=None,
) -> CompletionPreflight:
    """§9.1 — run the hard checks, then the semantic preflight, then sanity.

    Order is the contract: the structural checks run first and alone; only the
    criteria that survived them are asked about. An escalation is recorded as a
    ``system_one.escalated`` *request* (§24) — never an action, and never a
    completion.
    """
    from .system_one import EvaluationContext, SystemOneState

    assert_preflight_readings_registered()
    started = time.time()
    runs = dict(runs or {})
    revision = int(goal_revision if goal_revision is not None else ledger.goal_revision)
    workspace = str(workspace_fingerprint or ledger.workspace_fingerprint or "")
    ids = set(known_criterion_ids) if known_criterion_ids is not None else {
        criterion.criterion_id for criterion in ledger.criteria
    }

    gate_open = flags is None or (
        bool(getattr(flags, "enabled", False))
        and bool(getattr(flags, "goal_completion", False))
    )

    reports: dict[str, HardCheckReport] = {}
    for criterion in ledger.criteria:
        reports[criterion.criterion_id] = hard_checks(
            criterion,
            runs=runs,
            workspace_root=workspace_root,
            platform=platform,
            known_criterion_ids=ids,
            live_goal_revision=live_goal_revision
            if live_goal_revision is not None
            else revision,
            live_workspace_fingerprint=live_workspace_fingerprint
            if live_workspace_fingerprint is not None
            else (workspace or None),
        )

    signals: list[str] = []
    if any(not report.ok for report in reports.values()):
        signals.append("hard_check_refused")

    open_criteria = [
        criterion for criterion in ledger.criteria if reports[criterion.criterion_id].ok
    ]
    evaluation = None
    evaluated = False
    if not gate_open:
        signals.append("goal_completion_disabled")
    elif open_criteria and service is not None and bool(getattr(service, "enabled", True)):
        ctx = context
        if ctx is None:
            ctx = EvaluationContext(
                run_id="",
                goal_id="",
                goal_revision=revision,
                caller="completion",
                workspace_fingerprint=workspace,
                question_pack_id="completion_preflight",
            )
        ctx.question_pack_id = "completion_preflight"
        state = SystemOneState(
            payload=_preflight_state(ledger=ledger, criteria=open_criteria, runs=runs),
            workspace_fingerprint=workspace,
        )
        from .system_one_questions import compose_questions

        evaluation = await service.evaluate_snapshot(
            snapshot=state.payload,
            questions=compose_questions("completion_preflight"),
            context=ctx,
        )
        evaluated = evaluation is not None

    verdicts: list[CriterionVerdict] = []
    for criterion in ledger.criteria:
        report = reports[criterion.criterion_id]
        shortcuts = detect_shortcuts(
            criterion,
            runs=runs,
            claim_text=claim_text,
            completion_text=completion_text,
        )
        verdicts.append(
            _verdict_for(
                criterion, report=report, shortcuts=shortcuts, evaluation=evaluation
            )
        )

    hierarchy = aggregate_criteria([verdict.as_dict() for verdict in verdicts])
    shortcut_codes = tuple(
        dict.fromkeys(code for verdict in verdicts for code in verdict.shortcut_codes)
    )

    sanity: dict = {}
    sanity_evaluation = None
    if hierarchy == "pass" and service is not None and bool(getattr(service, "enabled", True)):
        summary = build_ledger_summary(
            ledger.criteria,
            verdicts=[verdict.as_dict() for verdict in verdicts],
            unknown_side_effects=ledger.unknown_side_effects,
            unresolved_findings=ledger.unresolved_findings,
        )
        sanity_state = SystemOneState(
            payload={"step": 0, "completion": {"summary": summary}},
            workspace_fingerprint=workspace,
        )
        ctx = context
        if ctx is None:
            ctx = EvaluationContext(
                run_id="",
                goal_id="",
                goal_revision=revision,
                caller="completion",
                workspace_fingerprint=workspace,
                question_pack_id="completion_sanity",
            )
        ctx.question_pack_id = "completion_sanity"
        from .system_one_questions import compose_questions

        sanity_evaluation = await service.evaluate_snapshot(
            snapshot=sanity_state.payload,
            questions=compose_questions("completion_sanity"),
            context=ctx,
        )
        for question_id in COMPLETION_SANITY_IDS:
            value = _preflight_noul(sanity_evaluation, question_id)
            if value is not None:
                sanity[question_id.rsplit(".", 1)[-1]] = value
        if any(value >= 0.5 for value in sanity.values()):
            signals.append("sanity_pass_escalated")
        elif sanity_evaluation is not None:
            signals.append("sanity_pass_clean")
        else:
            signals.append("sanity_pass_not_run")

    reasons = tuple(
        dict.fromkeys(
            list(shortcut_codes)
            + [
                reason
                for verdict in verdicts
                for reason in verdict.reasons
                if not verdict.hard_refusals or reason in verdict.hard_refusals
            ]
        )
    )
    reading = evaluation if evaluation is not None else sanity_evaluation
    provider = str(getattr(reading, "provider", "") or "")
    provider_mode = str(getattr(reading, "provider_mode", "") or "")
    live_jev = bool(getattr(reading, "live_jev", False))
    preflight = CompletionPreflight(
        ledger=ledger,
        verdicts=tuple(verdicts),
        signals=tuple(dict.fromkeys(signals)),
        reasons=reasons,
        shortcut_codes=shortcut_codes,
        hierarchy=hierarchy,
        sanity=sanity,
        provider=provider,
        provider_mode=provider_mode,
        live_jev=live_jev,
        advisory_only=True,
        certifies=False,
        revision=revision,
        workspace_fingerprint=workspace,
        evaluated=evaluated,
        fingerprint=fingerprint_for(list(reasons) + list(shortcut_codes) + [hierarchy]),
    )

    escalated = [verdict for verdict in verdicts if verdict.decision == "escalate"]
    if escalated and service is not None:
        service.emit(
            "system_one.escalated",
            {
                "criterion_ids": [verdict.criterion_id for verdict in escalated],
                "route": "request_verification",
                "request_only": True,
                "revision": revision,
                "workspace_fingerprint": workspace,
            },
        )
    if service is not None:
        metrics = getattr(service, "metrics", None)
        if metrics is not None:
            metrics.record_completion_run(
                criteria_checked=len(ledger.criteria),
                criteria_flagged=sum(1 for v in verdicts if v.decision == "refuse"),
                criteria_escalated=len(escalated),
                total_latency_ms=(time.time() - started) * 1000.0,
                source="completion_preflight",
            )
    return preflight


class GateRunner:
    """§14–§27: run the gates against facts + evidence + the pact's DoD."""

    def __init__(
        self,
        *,
        repo_root: str | Path,
        dod: list[DodItem] | None = None,
        task_base: str = "",
        revision: int = 0,
        workspace: str = "",
        session: str = "",
    ) -> None:
        self.repo_root = Path(repo_root)
        self.dod = dod or []
        self.task_base = str(task_base or "")
        # Evidence binding (audit P1, 13.09.): runner evidence only certifies
        # the revision/workspace it was produced under. 0/"" = no binding
        # context (legacy callers keep the old behaviour).
        self.revision = int(revision or 0)
        self.workspace = str(workspace or "")
        # Block 3: the same fence for the session — runner evidence produced for
        # another session (or with no session stamp at all) never certifies.
        self.session = str(session or "")

    # -- individual gates ---------------------------------------------------

    def gate_evidence(
        self, request: CompletionRequest, evidence: list[RunEvidence]
    ) -> list[Finding]:
        """§17: every claimed-completed item needs a check that actually ran."""
        findings: list[Finding] = []
        if request.claimed_completed and not evidence:
            findings.append(
                Finding(
                    gate="evidence",
                    severity=BLOCKER,
                    detail="completion claimed but no executed check was provided as evidence",
                    requirement=request.claimed_completed[0],
                )
            )
        if request.claimed_completed and evidence and not any(run.passed for run in evidence):
            findings.append(
                Finding(
                    gate="evidence",
                    severity=BLOCKER,
                    detail="only failing checks were provided as evidence",
                    requirement=request.claimed_completed[0],
                )
            )
        return findings

    def gate_tests(
        self,
        request: CompletionRequest,
        evidence: list[RunEvidence],
        facts: RepoFacts,
    ) -> list[Finding]:
        """§19: tests written but not executed = INCOMPLETE.

        Block 3 (deadlock fix): a suite run counts as evidence by what it
        *executed*, not only by the check id a contract happened to declare.
        The old rule demanded a runner-source result with the literal id
        ``pytest`` while the runner only executed DoD-declared checks — a
        contract without a pytest criterion could never clear the gate
        (replay-verified). Now a runner-executed result that really invoked a
        test runner (``suite``) also satisfies the gate; a green in-session
        `pytest` *event* still does not (``source == "events"``), and identity
        stays bound: a check whose command never touches a test runner is not
        a suite run.
        """
        findings: list[Finding] = []
        tests = [e for e in evidence if (e.check_id or e.name) in ("pytest", "tests", "test")]
        runner_tests = [e for e in tests if e.source == "runner"]
        suite_runs = [
            e
            for e in evidence
            if e.source == "runner" and (e.suite or bool(_SUITE_HINT.search(e.cmd or "")))
        ]
        code_suffixes = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx")
        code_changed = any(f.endswith(code_suffixes) for f in facts.changed_files)
        if code_changed and not runner_tests and not suite_runs:
            findings.append(
                Finding(
                    gate="tests",
                    severity=BLOCKER,
                    detail="code changed but no test run is evidenced — run the suite now",
                )
            )
        # A failed test run stays relevant until a *later, matching* green run
        # supersedes it (P1): failures are read from every source (conservative),
        # a pass only settles the failure it chronologically follows.
        for check in ("pytest", "tests", "test"):
            runs = sorted(
                [e for e in tests if (e.check_id or e.name) == check], key=lambda e: e.ts
            )
            if not runs:
                continue
            latest_fail = max((e.ts for e in runs if not e.passed), default=None)
            if latest_fail is None:
                continue
            if any(e.passed and e.ts > latest_fail for e in runs):
                continue
            if any(e.passed and e.ts > latest_fail for e in suite_runs):
                continue
            newest = max(runs, key=lambda e: e.ts)
            findings.append(
                Finding(
                    gate="tests",
                    severity=BLOCKER,
                    detail=f"test run failed: {newest.cmd or newest.name}",
                    evidence=newest.detail[:200],
                )
            )
        # Block 3: a runner-executed suite run that really ran the project's
        # tests and failed stays blocking, even when its check id is not
        # `pytest` (the gate is about the executed command, not the label).
        for run in suite_runs:
            if run.passed or (run.check_id or run.name) in ("pytest", "tests", "test"):
                continue
            if not any(e.ts > run.ts for e in suite_runs if e.passed):
                findings.append(
                    Finding(
                        gate="tests",
                        severity=BLOCKER,
                        detail=f"test run failed: {run.cmd or run.name}",
                        evidence=run.detail[:200],
                    )
                )
        return findings

    def gate_build(
        self,
        request: CompletionRequest,
        evidence: list[RunEvidence],
        facts: RepoFacts,
    ) -> list[Finding]:
        """§18: a build, when relevant, must actually pass."""
        findings: list[Finding] = []
        build_relevant = any(
            f.endswith(("package.json", "pyproject.toml", "electron-builder.yml"))
            or "desktop/" in f
            for f in facts.changed_files
        )
        builds = [e for e in evidence if e.name in ("build", "app_build")]
        if build_relevant and not builds:
            findings.append(
                Finding(
                    gate="build",
                    severity=MAJOR,
                    detail="build-relevant files changed but no build run is evidenced",
                )
            )
        for run in builds:
            if not run.passed:
                findings.append(
                    Finding(
                        gate="build",
                        severity=BLOCKER,
                        detail=f"build failed: {run.cmd or run.name}",
                        evidence=run.detail[:200],
                    )
                )
        return findings

    def gate_integration(self, request: CompletionRequest, facts: RepoFacts) -> list[Finding]:
        """§16: new modules must actually be imported somewhere (no orphans)."""
        findings: list[Finding] = []
        for path in facts.new_files:
            if not (path.startswith("src/skaldir/") and path.endswith(".py")):
                continue
            module = Path(path).stem
            if module in ("__init__", "__main__"):
                continue
            try:
                import subprocess

                grep = subprocess.run(  # noqa: S603,S607 — fixed argv
                    ["git", "-C", str(self.repo_root), "grep", "-l", module, "--", "src", "tests"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                hits = [line for line in grep.stdout.splitlines() if not line.endswith(path)]
            except Exception:
                hits = [path]
            if not hits:
                findings.append(
                    Finding(
                        gate="integration",
                        severity=BLOCKER,
                        detail=f"new module '{path}' is imported/used nowhere — dead component",
                    )
                )
        return findings

    def gate_fake_completion(self, facts: RepoFacts) -> list[Finding]:
        """§24: TODO/FIXME/placeholder/skip/silent-except in changed lines."""
        findings: list[Finding] = []
        current_file = ""
        for line in facts.diff_text.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:]
                continue
            if not line.startswith("+") or line.startswith("+++"):
                continue
            added = line[1:]
            if _CHANGED_TODO.search(added):
                findings.append(
                    Finding(
                        gate="fake_completion",
                        severity=MAJOR,
                        detail=f"TODO/FIXME in changed line ({current_file})",
                        evidence=added.strip()[:120],
                    )
                )
            elif _PLACEHOLDER.search(added):
                findings.append(
                    Finding(
                        gate="fake_completion",
                        severity=BLOCKER,
                        detail=f"placeholder/fake-data marker in changed line ({current_file})",
                        evidence=added.strip()[:120],
                    )
                )
            elif _SKIPPED_TEST.search(added):
                findings.append(
                    Finding(
                        gate="fake_completion",
                        severity=MAJOR,
                        detail=f"skipped test added ({current_file})",
                        evidence=added.strip()[:120],
                    )
                )
        return findings

    def gate_security(self, facts: RepoFacts) -> list[Finding]:
        """§26: secret material must not enter the diff.

        Fail-**closed** (Block 1 / audit SEC-07): the scanner tripping is a
        finding of its own — the old ``except Exception: pass`` turned a
        broken secret scan into "no findings", so a secret diff passed the
        gate silently. Two cases are distinguished, because they must not be
        conflated:

        * the scan **failed** (exception, missing module) → BLOCKER: an
          unverified diff is not a clean diff;
        * the scan **ran clean with no match** → no finding: a clean repo
          must never be blocked just because a pattern did not match.

        Overlapping matches are already deduplicated by
        :func:`skaldir.secretscan.scan`, so one secret yields one finding (the
        two-BLOCKER duplication for a single ``api_key = "sk-…"``).
        """
        findings: list[Finding] = []
        try:
            from .secretscan import redact

            _cleaned, scan_findings = redact(facts.diff_text)
        except Exception as exc:  # scanner failed → the diff is UNVERIFIED
            findings.append(
                Finding(
                    gate="security",
                    severity=BLOCKER,
                    detail=(
                        "secret scan failed — the diff is unverified, "
                        "not clean (fail-closed)"
                    ),
                    evidence=f"{type(exc).__name__}: {exc}"[:120],
                )
            )
            return findings
        for found in scan_findings[:5]:
            findings.append(
                Finding(
                    gate="security",
                    severity=BLOCKER,
                    detail="possible secret material in the diff",
                    evidence=str(getattr(found, "kind", found))[:120],
                )
            )
        return findings

    def gate_cleanup(self, facts: RepoFacts) -> list[Finding]:
        """§27: debug prints / hardcoded tmp paths in changed src lines."""
        findings: list[Finding] = []
        current_file = ""
        for line in facts.diff_text.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:]
                continue
            if not line.startswith("+") or line.startswith("+++"):
                continue
            added = line[1:]
            if not current_file.startswith("src/"):
                continue
            if _DEBUG_PRINT.match(added):
                findings.append(
                    Finding(
                        gate="cleanup",
                        severity=MINOR,
                        detail=f"debug print left in {current_file}",
                        evidence=added.strip()[:120],
                    )
                )
            if _HARDCODED_TMP.search(added):
                findings.append(
                    Finding(
                        gate="cleanup",
                        severity=MINOR,
                        detail=f"hardcoded temp path in {current_file}",
                        evidence=added.strip()[:120],
                    )
                )
        return findings

    def _evidence_bound(self, e: RunEvidence) -> bool:
        """Old/foreign evidence must not certify a revision, workspace or session.

        Block 3 closes the bypass the audit reproduced: with a binding context
        set, a **blank** field on the evidence (``revision=0``, empty
        ``workspace``/``session``) no longer slips through just because only one
        side was populated — a runner that stamps nothing certifies nothing.
        Without any binding context (0/""/None) legacy callers keep the old
        behaviour, so unit fixtures are unaffected.
        """
        wanted_workspace = (self.workspace or "").strip()
        evidence_workspace = (e.workspace or "").strip()
        if self.revision and int(e.revision or 0) != self.revision:
            return False
        if wanted_workspace:
            if not evidence_workspace or evidence_workspace != wanted_workspace:
                return False
        wanted_session = (self.session or "").strip()
        evidence_session = (e.session or "").strip()
        if wanted_session:
            if not evidence_session or evidence_session != wanted_session:
                return False
        return True

    def gate_dod(
        self,
        request: CompletionRequest,
        evidence: list[RunEvidence],
    ) -> list[Finding]:
        """The pact's definition of done: every *blocking* item needs evidence.

        Strictness (P1 fix): a specific requirement is only certified by
        runner-executed evidence (``source == "runner"``, real command + exit
        code). Tool-event evidence is suggestive context; keyword matches in a
        command must never certify. Items without a verification plan are
        reported as such instead of silently passing or blocking namelessly.
        """
        findings: list[Finding] = []
        for item in self.dod:
            # Status is bookkeeping for the UI; certification always needs
            # fresh evidence in THIS run (a persisted 'verified' must never
            # skip verification — audit finding 13.09.).
            wanted = (item.verification or "").strip()
            severity = BLOCKER if item.blocking else MINOR
            if not wanted:
                findings.append(
                    Finding(
                        gate="dod",
                        severity=severity,
                        detail=(
                            "no verification plan for: "
                            f"{item.text} — derive a concrete check (it cannot be certified as-is)"
                        ),
                        requirement=item.id,
                        evidence="expected verification: missing",
                    )
                )
                continue
            candidates = [e for e in evidence if (e.check_id or e.name) == wanted]
            strong = [e for e in candidates if e.source == "runner" and self._evidence_bound(e)]
            failing = [e for e in strong if not e.passed]
            if strong and not failing:
                continue
            if candidates and not strong:
                stale = [e for e in candidates if e.source == "runner"]
                # Block 3: a declared-but-unavailable environment is not a bug
                # in the check — say so, with the runner's own reason, so the
                # blocker is actionable instead of reading like a hang.
                unavailable = [
                    e for e in candidates if (e.status or "") == NOT_CERTIFIABLE
                ]
                if unavailable:
                    reason = (
                        unavailable[0].detail or "no runnable environment"
                    )
                    why = f"not certifiable here — {reason}"
                elif stale:
                    why = (
                        f"runner evidence for '{wanted}' is from a different revision/"
                        "workspace — re-run the check in THIS contract revision"
                    )
                else:
                    why = (
                        f"only tool-event evidence for '{wanted}' — needs a "
                        "runner-executed check"
                    )
                findings.append(
                    Finding(
                        gate="dod",
                        severity=severity,
                        detail=f"definition-of-done not verified: {item.text} ({why})",
                        requirement=item.id,
                        evidence=f"expected verification: {wanted}",
                    )
                )
                continue
            findings.append(
                Finding(
                    gate="dod",
                    severity=severity,
                    detail=f"definition-of-done not verified: {item.text}",
                    requirement=item.id,
                    evidence=f"expected verification: {wanted}",
                )
            )
        return findings

    # -- the verification ---------------------------------------------------

    def gate_precheck(
        self,
        request: CompletionRequest,
        evidence: list[RunEvidence],
        precheck,
    ) -> list[Finding]:
        """§91/JEV-13: a precheck may signal, it never certifies.

        Three outcomes, in this order:

        * a precheck that claims ``certifies=True`` is refused — the gate, not
          the precheck, decides;
        * without a *runner-executed, bound, passing* check the precheck cannot
          carry the completion: BLOCKER with the machine-readable reason
          ``completion-precheck-only-source``;
        * beside real certifying evidence the reading stays advisory: recorded
          as INFO/MINOR findings, never blocking and never downgrading.
        """
        findings: list[Finding] = []
        if precheck is None:
            return findings
        for signal in tuple(getattr(precheck, "signals", ()) or ()):
            findings.append(
                Finding(
                    gate="precheck",
                    severity=INFO,
                    detail=f"completion precheck signal: {signal}",
                )
            )
        if bool(getattr(precheck, "certifies", False)):
            findings.append(
                Finding(
                    gate="precheck",
                    severity=BLOCKER,
                    code=PRECHECK_CERTIFIES,
                    detail=(
                        "completion refused: a precheck must never certify completion — "
                        "only a runner-executed check may (§91/JEV-13)"
                    ),
                )
            )
            return findings
        certifying = [
            run
            for run in evidence
            if run.source == "runner" and run.passed and self._evidence_bound(run)
        ]
        if certifying:
            reasons = tuple(getattr(precheck, "reasons", ()) or ())
            if reasons:
                findings.append(
                    Finding(
                        gate="precheck",
                        severity=MINOR,
                        detail=(
                            "completion precheck noted "
                            + ", ".join(reasons)
                            + " — the runner-executed evidence above certifies this run"
                        ),
                    )
                )
            return findings
        reasons = ", ".join(tuple(getattr(precheck, "reasons", ()) or ())) or "no reason given"
        findings.append(
            Finding(
                gate="precheck",
                severity=BLOCKER,
                code=PRECHECK_ONLY_SOURCE,
                detail=(
                    "completion refused: the precheck is the only source — it signals "
                    f"missing/stale evidence ({reasons}) but never certifies; a "
                    "runner-executed check must certify (§91/JEV-13)"
                ),
            )
        )
        return findings

    def verify(
        self,
        request: CompletionRequest,
        evidence: list[RunEvidence],
        *,
        store: CompletionStore | None = None,
        precheck=None,
    ) -> VerificationResult:
        facts = RepoFacts.collect(self.repo_root, base=self.task_base)
        # Block 3: the session-scoped store is the authority for WHO is being
        # verified. Without an explicit binding the store's session is adopted,
        # and runner evidence that carries no session stamp at all is adopted
        # into it — evidence stamped with ANOTHER session is never accepted
        # (`_evidence_bound`). Producers that already stamp (``execute_check``)
        # keep their own value; nothing is overwritten.
        if store is not None and not self.session:
            try:
                self.session = str(getattr(store, "session_id", "") or "")
            except Exception:  # a broken store must not silently widen the fence
                self.session = ""
        if self.session:
            for run in evidence:
                if run.source == "runner" and not (run.session or "").strip():
                    run.session = self.session
        if store is not None and not self.revision:
            try:
                self.revision = int(store.state().get("revision") or 0)
            except Exception:
                self.revision = 0
        findings: list[Finding] = []
        findings += self.gate_precheck(request, evidence, precheck)
        findings += self.gate_evidence(request, evidence)
        findings += self.gate_tests(request, evidence, facts)
        findings += self.gate_build(request, evidence, facts)
        findings += self.gate_integration(request, facts)
        findings += self.gate_fake_completion(facts)
        findings += self.gate_security(facts)
        findings += self.gate_cleanup(facts)
        findings += self.gate_dod(request, evidence)

        # P1: no certificate without a check. When the contract carries no
        # blocking criteria AND no executed check passed, nothing was verified
        # — that must never read as VERIFIED_COMPLETE (the hollow-green case
        # the panel could mint from an empty goal with zero evidence).
        if not any(item.blocking for item in self.dod) and not any(
            run.ok for run in evidence
        ):
            findings.append(
                Finding(
                    gate="evidence",
                    severity=BLOCKER,
                    detail=(
                        "nothing was verified — the goal has no blocking "
                        "acceptance criteria and no executed check passed; "
                        "define done and run a check before claiming completion"
                    ),
                )
            )

        findings.sort(key=lambda f: _SEVERITY_ORDER.get(f.severity, 9))
        blockers = [f for f in findings if f.severity == BLOCKER]
        majors = [f for f in findings if f.severity == MAJOR]
        minors = [f for f in findings if f.severity in (MINOR, INFO)]

        status = VERIFIED_COMPLETE if not blockers and not majors else INCOMPLETE
        result = VerificationResult(
            status=status,
            attempt_id=request.attempt_id or f"att-{uuid.uuid4().hex[:8]}",
            verified_requirements=list(request.claimed_completed)
            if status == VERIFIED_COMPLETE
            else [],
            blocking_failures=[f"{f.gate}: {f.detail}" for f in blockers],
            major_failures=[f"{f.gate}: {f.detail}" for f in majors],
            minor_findings=[f"{f.gate}: {f.detail}" for f in minors],
            evidence=[
                f"{run.name}: {'ok' if run.ok else 'failed'} ({run.cmd or run.detail[:60]})"
                for run in evidence
            ],
            next_actions=_next_actions(blockers, majors),
            fingerprint=fingerprint_for([f"{f.gate}: {f.detail}" for f in blockers + majors]),
            created=time.time(),
            # §91/JEV-13: the machine-readable reasons of this verdict. Only
            # coded findings contribute — legacy rejections stay code-free.
            refusal_codes=sorted({f.code for f in blockers + majors if f.code}),
        )

        if store is not None:
            if precheck is not None:
                store.record_precheck(precheck)
            store.record_request(request)
            repeat = (
                store.rejection_attempts(result.fingerprint)
                if result.status == INCOMPLETE
                else 0
            )
            result.repeat_count = repeat + 1
            result.escalate = result.status == INCOMPLETE and repeat >= 1
            if result.escalate:
                result.next_actions = [
                    "STOP repeating the same fix. Investigate the root cause first "
                    "(pause → research → different strategy)."
                ] + result.next_actions
            store.record_result(result)
            store.record_dod_outcomes(result, findings)
            store.transition(
                result.status if result.status in TERMINAL_STATES else INCOMPLETE,
                attempt_id=result.attempt_id,
                fingerprint=result.fingerprint,
            )
        return result


def _next_actions(blockers: list[Finding], majors: list[Finding]) -> list[str]:
    actions: list[str] = []
    gates = {finding.gate for finding in blockers + majors}
    if "tests" in gates:
        actions.append("Run the full test suite now and attach the result as evidence.")
    if "build" in gates:
        actions.append("Run the real build and attach its output.")
    if "integration" in gates:
        actions.append("Wire the new component into the real flow (import/use it).")
    if "fake_completion" in gates:
        actions.append("Remove placeholder/skip markers or implement the missing part.")
    if "security" in gates:
        actions.append("Remove the secret material and re-scan before claiming completion.")
    if "dod" in gates:
        actions.append("Verify each blocking definition-of-done item with its required check.")
    if "evidence" in gates:
        actions.append("Provide at least one executed check per claimed requirement.")
    if not actions:
        actions.append("Fix the reported blockers and request completion again with evidence.")
    return actions


def _result_status(payload: dict) -> str:
    """Explicit verdict of one tool result — errors are never a pass (P1 fix).

    Precedence: an ``error`` beats everything (a conflict is not a success);
    then the exit code; then the host-supplied status/ok. Missing or
    unreadable signals stay ``unknown``.
    """
    error = payload.get("error")
    if error not in (None, ""):
        return "failed"
    exit_code = payload.get("exit_code")
    if exit_code not in (None, ""):
        try:
            return "passed" if int(exit_code) == 0 else "failed"
        except (TypeError, ValueError):
            return "failed"
    hint = str(payload.get("status") or "").strip().lower()
    if hint in ("ok", "passed", "success", "succeeded", "completed", "done"):
        return "passed"
    if hint in ("error", "failed", "failure", "cancelled", "canceled", "aborted", "timeout"):
        return "failed"
    ok_field = payload.get("ok")
    if ok_field is True:
        return "passed"
    if ok_field is False:
        return "failed"
    return "unknown"


def run_evidence_from_events(events) -> list[RunEvidence]:
    """Derive RunEvidence from checks that were *actually executed* (§17).

    Strictness (P1 fixes):
    - results pair with their call through ``tool_call_id`` (fallback: the next
      unmatched call of the same tool, each call consumed once) — results are
      never misattributed to a different invocation;
    - an explicit verdict is required: a missing exit code/status is
      ``unknown`` and an ``error`` is ``failed`` — never an implicit pass;
    - the category is a *suggestion* derived from the command (``check_id``);
      the evidence stays ``source="events"`` and cannot certify a specific
      requirement — only runner-executed evidence can.

    Freshness rule: only results *after* the last file edit count — a green run
    from before the change proves nothing about the current code.
    """
    import re as _re

    from .guard import API_HINT, TEST_HINT, VISUAL_HINT

    build_hint = _re.compile(
        r"(?i)\b(build-app|npm run build|electron-builder|docker build|uv build)\b"
    )

    def suggest_check(args, name: str) -> str:
        """Suggested check id for a call — only *command executions* qualify.

        A write/read of a file is not a check: without a real command there is
        no execution to evidence (the pre-fix heuristic required command/argv
        too — dropping this let plain tool calls certify completions).
        """
        text = ""
        if isinstance(args, dict):
            if not any(key in args for key in ("command", "argv", "cmd")):
                return ""
            text = " ".join(str(args.get(k) or "") for k in ("command", "argv", "cmd"))
        elif isinstance(args, str):
            stripped = args.strip()
            if not stripped:
                return ""
            parsed = None
            if stripped.startswith("{"):
                try:
                    parsed = json.loads(stripped)
                except Exception:
                    parsed = None
            if isinstance(parsed, dict):
                if not any(key in parsed for key in ("command", "argv", "cmd")):
                    return ""
                text = " ".join(str(parsed.get(k) or "") for k in ("command", "argv", "cmd"))
            else:
                return ""  # not recognisably a command execution
        else:
            return ""
        if not text.strip():
            return ""
        if build_hint.search(text):
            return "build"
        if TEST_HINT.search(text):
            return "pytest"
        if VISUAL_HINT.search(text):
            return "ui_e2e"
        if API_HINT.search(text):
            return "api"
        return "run"

    events = list(events)
    calls: dict[str, dict] = {}
    queue: dict[str, list[str]] = {}
    last_edit = 0
    seq = 0
    for event in events:
        payload = getattr(event, "payload", None) or {}
        if event.kind == "file_edit":
            last_edit = max(last_edit, int(event.step or 0))
            continue
        if event.kind != "tool_call":
            continue
        seq += 1
        name = str(payload.get("name") or "")
        call_id = str(
            payload.get("tool_call_id") or payload.get("call_id") or payload.get("id") or ""
        ) or f"{name}#{seq}"
        args = payload.get("args")
        cmd_text = str(args or "")
        if isinstance(args, dict):
            cmd_text = str(args.get("command") or args.get("argv") or args.get("cmd") or args)
        check = suggest_check(args, name)
        if not check:
            continue  # not a command execution — nothing to evidence
        calls[call_id] = {
            "id": call_id,
            "name": name,
            "check": check,
            "step": int(event.step or 0),
            "cmd": cmd_text.strip()[:160],
        }
        queue.setdefault(name, []).append(call_id)

    evidence: list[RunEvidence] = []
    for event in events:
        if event.kind != "tool_result":
            continue
        step = int(event.step or 0)
        if step <= last_edit:
            continue
        payload = getattr(event, "payload", None) or {}
        name = str(payload.get("tool") or payload.get("name") or "")
        call_id = str(
            payload.get("tool_call_id") or payload.get("call_id") or payload.get("id") or ""
        )
        call = calls.get(call_id) if call_id else None
        if call is None:
            pending = queue.get(name) or []
            call_id = pending.pop(0) if pending else ""
            call = calls.get(call_id) if call_id else None
        if call is None:
            continue  # no matching command call — nothing to evidence
        status = _result_status(payload)
        check = str((call or {}).get("check") or "run")
        cmd = str((call or {}).get("cmd") or f"{name} @ step {step}")
        evidence.append(
            RunEvidence(
                name=check,
                cmd=cmd,
                ok=(status == "passed"),
                status=status,
                check_id=check,
                source="events",
                tool_call_id=call_id or "",
                detail=str(payload.get("stderr") or payload.get("error") or "")[:160],
                ts=float(step),
            )
        )
    return evidence


# -- the verification runner ---------------------------------------------------
#
# Only runner-executed evidence may certify a requirement (P1). A check runs
# through a fixed, known command; anything without a runner stays honestly
# unfulfilled instead of being approximated by tool events.
#
# Block 3 closes the runner gap: the canonical check ids ``run``/``ui_e2e``/
# ``api``/``build``/``persistence_restart`` now have real runners. A runner
# executes a *declared* command (workspace-local, sandboxed) and applies one
# contract to every source: exit 0 AND an explicit ``PASS`` verdict line —
# anything else fails closed. A declared-but-unavailable environment (no
# sandbox, no declaration, missing executable, no restart cycle) is reported as
# ``not-certifiable`` with its reason; it never passes.



_NAMED_CHECKS: dict[str, str] = {
    "run": "SKALDIR_CHECK_RUN_CMD",
    "ui_e2e": "SKALDIR_CHECK_UI_CMD",
    "api": "SKALDIR_CHECK_API_CMD",
    "build": "SKALDIR_CHECK_BUILD_CMD",
    "persistence_restart": "SKALDIR_CHECK_RESTART_CMD",
}

#: Canonical check ids that always have a runner (Block 3).
CANONICAL_CHECKS = ("pytest", "run", "ui_e2e", "api", "build", "persistence_restart")

#: A command that really invokes the project's test runner — the evidence
#: ``gate_tests`` accepts when the contract never named a pytest criterion.
#: The lookahead matters: a path like ``/tmp/pytest-of-willy/x.py`` is not a
#: suite run, only the runner token itself counts.
_SUITE_HINT = re.compile(
    r"(?i)(?:^|[\s/&|;])(?:pytest|py\.test|unittest|tox|nox|vitest|jest|mocha"
    r"|node\s+--test|npm\s+(?:run\s+)?test|pnpm\s+test|yarn\s+test|go\s+test"
    r"|cargo\s+test|mvn\s+test|gradle\s+test)(?![\w-])"
)


#: Code file suffixes the test gate reacts to (§19).
_CODE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx")


def workspace_has_code_changes(repo_root: str | Path) -> bool:
    """Whether the workspace carries changed/untracked code (Block 3).

    A broken repository query counts as "changed": an unverifiable tree must
    lead to a suite run, never to a silent pass.
    """
    try:
        facts = RepoFacts.collect(repo_root)
    except Exception:
        return True
    if facts.unreadable:
        return True
    return any(path.endswith(_CODE_SUFFIXES) for path in facts.changed_files)


def check_verdict(check_id: str, exit_code: int | None, output: str) -> tuple[str, str]:
    """The one result contract for every runner (Block 3).

    A pass requires **both**: exit code 0 AND an explicit ``PASS`` verdict line.
    An explicit ``FAIL``/``ERROR`` line wins over the exit code — ``FAIL: check
    did not PASS`` at exit 0 is not a pass. Anything ambiguous fails closed.
    Returns ``(status, reason)`` with ``status`` in ``passed``/``failed``.
    """
    text = str(output or "")
    upper = text.upper()
    verdict_fail = re.search(r"^\s*(?:FAIL|FAILED|ERROR)\b", upper, re.M) is not None
    verdict_pass = re.search(r"^\s*PASS\b", upper, re.M) is not None
    if exit_code is None:
        return "failed", "no exit code — the check did not complete"
    if int(exit_code) < 0:
        return "failed", f"runner could not execute the check (exit {int(exit_code)})"
    if int(exit_code) != 0:
        return "failed", f"exit {int(exit_code)}"
    if verdict_fail:
        return "failed", "explicit FAIL/ERROR verdict line"
    if verdict_pass:
        return "passed", ""
    return "failed", "no explicit PASS verdict line (fail-closed)"


def _split_command(text: str) -> list[str]:
    try:
        return shlex.split(str(text or "").strip())
    except ValueError:
        return []


def _declared_spec(repo_root: Path, check_id: str) -> dict:
    """The declaration for one check: ``.godsv/checks.json`` (workspace-local).

    Shape: ``{"<check_id>": {"argv": [...], "timeout": 300, ...}}``. An
    unreadable or malformed file is treated as "nothing declared" — the check
    is then honestly not-certifiable, never silently green.
    """
    path = Path(repo_root) / ".godsv" / "checks.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    spec = data.get(str(check_id))
    return dict(spec) if isinstance(spec, dict) else {}


def _declared_env(check_id: str) -> str:
    return str(getenv(_NAMED_CHECKS.get(str(check_id), ""), "") or "").strip()


def _executable_missing(argv: list[str]) -> str:
    """Why ``argv[0]`` cannot run here — '' when it looks runnable."""
    if not argv:
        return "empty command"
    head = argv[0]
    if os.sep in head:
        candidate = Path(head)
        if not candidate.exists():
            return f"declared executable missing: {head}"
        if not os.access(candidate, os.X_OK):
            return f"declared executable is not executable: {head}"
        return ""
    if shutil.which(head) is None:
        return f"declared executable missing from PATH: {head}"
    return ""


def _not_certifiable(check_id: str, reason: str, *, revision: int, workspace: str,
                     session: str, cmd: str = "") -> RunEvidence:
    import time as _time

    return RunEvidence(
        name=str(check_id),
        check_id=str(check_id),
        source="runner",
        status=NOT_CERTIFIABLE,
        ok=False,
        cmd=cmd or f"<check {check_id}>",
        detail=(f"{check_id} is declared but not available here — {reason}")[:300],
        ts=_time.time(),
        revision=int(revision or 0),
        workspace=str(workspace or ""),
        session=str(session or ""),
    )


def _run_check_command(
    argv: list[str],
    *,
    workspace: str,
    timeout: float,
    label: str,
    revision: int,
    session: str,
) -> tuple[str, int | None, str, bool]:
    """Execute a declared command sandboxed. Returns (status, exit, output, passed).

    Two failure kinds are kept apart on purpose: a check that could not be
    executed at all (no sandbox — fail-closed) is ``not-certifiable``, while a
    check that *ran* and timed out or failed is a real ``failed`` result.
    """
    from .gates import NO_SANDBOX_EXIT, run_declared_check

    cwd = Path(workspace or ".").resolve()
    code, output, sandboxed = run_declared_check(
        argv,
        workdir=cwd,
        timeout=timeout,
        extra_env=child_env({"CHECK_ID": label, "WORKSPACE": str(cwd)}),
    )
    if code == NO_SANDBOX_EXIT and not sandboxed:
        return NOT_CERTIFIABLE, None, output, False
    status, reason = check_verdict(label, code, output)
    if status != "passed":
        output = f"{reason}: {output}" if reason else output
    return status, code, output, status == "passed"


def _declared_check_evidence(
    check_id: str,
    repo_root: Path,
    *,
    timeout: int,
    revision: int,
    workspace: str,
    session: str,
) -> RunEvidence:
    """The runner for ``run``/``ui_e2e``/``api``/``build`` (Block 3)."""
    import time as _time

    check_id = str(check_id)
    spec = _declared_spec(repo_root, check_id)
    text = str(spec.get("argv") and " ".join(str(a) for a in spec["argv"]) or "")
    if not text:
        text = _declared_env(check_id)
    if not text:
        return _not_certifiable(
            check_id,
            f"no declaration — set {_NAMED_CHECKS.get(check_id, 'the command')} or "
            f"add it to .godsv/checks.json",
            revision=revision,
            workspace=workspace,
            session=session,
        )
    argv = _split_command(text)
    if not argv:
        return _not_certifiable(
            check_id, f"declaration is not a valid command: {text!r}", revision=revision,
            workspace=workspace, session=session,
        )
    missing = _executable_missing(argv)
    if missing:
        return _not_certifiable(
            check_id, missing, revision=revision, workspace=workspace, session=session,
            cmd=text,
        )
    effective = float(spec.get("timeout") or timeout)
    status, code, output, _passed = _run_check_command(
        argv,
        workspace=workspace or str(repo_root),
        timeout=effective,
        label=check_id,
        revision=revision,
        session=session,
    )
    detail = (
        f"exit {code}: {output}" if code is not None else output
    )[:300]
    return RunEvidence(
        name=check_id,
        check_id=check_id,
        source="runner",
        status=status,
        ok=(status == "passed"),
        cmd=" ".join(argv)[:200],
        detail=detail,
        ts=_time.time(),
        revision=int(revision or 0),
        workspace=str(workspace or ""),
        session=str(session or ""),
        # a declared check that really invokes a test runner counts as a suite
        # run for gate_tests (identity is read from the executed command)
        suite=bool(_SUITE_HINT.search(text)),
    )


def _persistence_cycle_evidence(
    repo_root: Path,
    *,
    timeout: int,
    revision: int,
    workspace: str,
    session: str,
) -> RunEvidence:
    """The runner for ``persistence_restart``: a real write→restart→verify cycle.

    Two-phase protocol, all three parts must be declared:

    * ``SKALDIR_CHECK_RESTART_MARK_CMD`` — produce the state to survive (run first);
    * ``SKALDIR_CHECK_RESTART_ASSERT``   — workspace-relative path of that state;
    * ``SKALDIR_CHECK_RESTART_CMD``      — the restart itself.

    The check passes only when the restart command reports ``PASS`` **and** the
    state file is observably different afterwards — the restart really happened
    and the state really survived. Without the declaration the check is
    honestly ``not-certifiable``; it never passes on a missing environment.
    """
    import hashlib as _hashlib
    import time as _time

    check_id = "persistence_restart"
    specs = {
        key: _declared_spec(repo_root, key)
        for key in ("persistence_restart", "persistence_restart_mark")
    }
    mark_text = str(
        (specs["persistence_restart_mark"].get("argv") and "")
        or getenv("CHECK_RESTART_MARK_CMD", "")
    ).strip()
    restart_text = str(
        (specs["persistence_restart"].get("argv") and "")
        or getenv("CHECK_RESTART_CMD", "")
    ).strip()
    assert_rel = str(
        specs["persistence_restart"].get("assert")
        or getenv("CHECK_RESTART_ASSERT", "")
    ).strip()
    if not (mark_text and restart_text and assert_rel):
        return _not_certifiable(
            check_id,
            "no restart cycle declared — SKALDIR_CHECK_RESTART_MARK_CMD, "
            "SKALDIR_CHECK_RESTART_ASSERT and SKALDIR_CHECK_RESTART_CMD must all be set",
            revision=revision,
            workspace=workspace,
            session=session,
        )
    cwd = Path(workspace or repo_root).resolve()
    assertions = Path(assert_rel)
    assert_path = assertions if assertions.is_absolute() else cwd / assertions
    mark_argv = _split_command(mark_text)
    restart_argv = _split_command(restart_text)
    for label, argv in (("mark", mark_argv), ("restart", restart_argv)):
        missing = _executable_missing(argv)
        if missing:
            return _not_certifiable(
                check_id, f"{label} step: {missing}", revision=revision,
                workspace=workspace, session=session, cmd=restart_text,
            )
    effective = float(specs["persistence_restart"].get("timeout") or timeout)
    mark_status, _code, mark_out, _p = _run_check_command(
        mark_argv, workspace=str(cwd), timeout=effective, label=f"{check_id}_mark",
        revision=revision, session=session,
    )
    if mark_status != "passed":
        return _not_certifiable(
            check_id,
            f"mark step did not run ({mark_status}): {mark_out}"[:200],
            revision=revision,
            workspace=workspace,
            session=session,
            cmd=" ".join(mark_argv)[:200],
        )
    if not assert_path.exists():
        return _not_certifiable(
            check_id,
            f"state file not produced by the mark step: {assert_rel}",
            revision=revision,
            workspace=workspace,
            session=session,
            cmd=" ".join(mark_argv)[:200],
        )
    before = _hashlib.sha256(assert_path.read_bytes()).hexdigest()
    status, code, output, _passed = _run_check_command(
        restart_argv, workspace=str(cwd), timeout=effective, label=check_id,
        revision=revision, session=session,
    )
    if status == NOT_CERTIFIABLE:
        return _not_certifiable(
            check_id, output or "restart step could not run", revision=revision,
            workspace=workspace, session=session, cmd=" ".join(restart_argv)[:200],
        )
    detail = f"exit {code}: {output}" if code is not None else output
    if status == "passed":
        after = (
            _hashlib.sha256(assert_path.read_bytes()).hexdigest()
            if assert_path.exists()
            else ""
        )
        if after == before:
            status = "failed"
            detail = (
                "restart reported PASS but the state file did not change — "
                f"no restart evidence for {assert_rel}"
            )
        else:
            detail = f"restart cycle verified for {assert_rel}: {output}"
    return RunEvidence(
        name=check_id,
        check_id=check_id,
        source="runner",
        status=status,
        ok=(status == "passed"),
        cmd=" ".join(restart_argv)[:200],
        detail=detail[:300],
        ts=_time.time(),
        revision=int(revision or 0),
        workspace=str(workspace or ""),
        session=str(session or ""),
    )


_CUSTOM_RUNNERS: dict[str, object] = {}


def register_check(check_id: str, runner) -> None:
    """Register a check runner: callable(repo_root: Path) -> RunEvidence."""
    _CUSTOM_RUNNERS[str(check_id)] = runner


def _run_command(argv: list[str], cwd, timeout: int) -> tuple[int | None, str]:
    """Run one fixed local command. Returns (exit_code|None, output tail)."""
    import subprocess

    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-600:].strip()
        return proc.returncode, tail
    except subprocess.TimeoutExpired:
        return None, "check timed out"
    except OSError as exc:
        return None, f"spawn failed: {exc}"


def _detect_test_runner(repo_root: Path) -> tuple[str, list[str]] | None:
    """The project's OWN runner, read from its files — never invented (audit F05).

    Priority order (explicit script beats inferred config; both are only names for
    an *executed* command):

    1. ``package.json`` with a non-empty ``scripts.test`` → ``npm test`` (a Node
       project must not be told "your pytest suite failed" any more).
    2. pytest markers — a ``tests``/``test`` directory, ``conftest.py``,
       ``pytest.ini``, ``tox.ini``, or a ``pyproject.toml`` that names pytest →
       ``<python> -m pytest -q`` (project ``.venv`` when present).
    3. a ``Makefile`` with a real ``test:`` target → ``make test``.
    4. ``scripts/run_tests.sh`` → run by the shell.

    ``None`` means nothing declares a runner here — an honest ``no_test_runner``
    state, not a fabricated green or a fabricated failure.
    """
    import sys

    package_json = repo_root / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            script = str(((data.get("scripts") or {}).get("test")) or "").strip()
            if script:
                return "npm", ["npm", "test"]
        except Exception:  # noqa: BLE001 — a broken manifest must not invent a runner
            pass

    pytest_named_in_pyproject = False
    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists():
        try:
            pytest_named_in_pyproject = "pytest" in pyproject.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            pytest_named_in_pyproject = False
    # pytest's own default discovery collects test_*.py / *_test.py at the root and
    # one level below — scattered test files ARE a pytest project (measured: the
    # gate's dirty-repo fixture writes `test_ok.py` at the root and expects a run).
    has_scattered_test_file = (
        bool(list(repo_root.glob("test_*.py")) or list(repo_root.glob("*_test.py")))
        or bool(list(repo_root.glob("*/test_*.py")) or list(repo_root.glob("*/*_test.py")))
    )
    has_pytest_marker = (
        (repo_root / "tests").is_dir()
        or (repo_root / "test").is_dir()
        or (repo_root / "conftest.py").exists()
        or (repo_root / "pytest.ini").exists()
        or (repo_root / "tox.ini").exists()
        or pytest_named_in_pyproject
        or has_scattered_test_file
    )
    if has_pytest_marker:
        venv_python = repo_root / ".venv" / "bin" / "python"
        python = str(venv_python) if venv_python.exists() else sys.executable
        return "pytest", [python, "-m", "pytest", "-q"]

    makefile = repo_root / "Makefile"
    if makefile.exists():
        try:
            for line in makefile.read_text(encoding="utf-8", errors="replace").splitlines():
                if re.match(r"^test\s*:(?!=)", line.strip()):
                    return "make", ["make", "test"]
        except OSError:
            pass

    run_tests_sh = repo_root / "scripts" / "run_tests.sh"
    if run_tests_sh.exists():
        return "script", ["/bin/sh", str(run_tests_sh)]

    return None


#: Runner outputs that mean "the runner worked and found zero tests": pytest ``exit 5``
#: ("collected 0 items"), jest ("No tests found") and pytest's legacy summary line.
#: This is its OWN state — an empty test project is never "failed tests" (audit F05).
_NO_TESTS_RE = re.compile(
    r"no tests (?:found|collected|ran)|no test files? found|collected 0 items",
    re.IGNORECASE,
)


_SUITE_PROBES = (
    "package.json with a scripts.test entry",
    "pytest markers (tests/ or test/ directory, conftest.py, pytest.ini, tox.ini, "
    "pyproject.toml naming pytest)",
    "a Makefile test target",
    "scripts/run_tests.sh",
)


def _suite_runner_evidence(
    check_id: str, repo_root: Path, *, timeout: int, revision: int, workspace: str,
    session: str,
) -> RunEvidence:
    """Execute the project's detected runner and classify the real ending.

    Four endings stay four endings: ``passed`` (exit 0), ``failed`` (the runner ran
    and failed, or never finished / could not start), ``no_tests_found`` (the runner
    ran fine and collected nothing — pytest's exit 5 or the runner saying so in its
    own words) and ``no_test_runner`` (nothing here declares how tests run). A
    timeout is a *failed* suite whose detail says why — never a pass.
    """
    import time as _time

    runner = _detect_test_runner(repo_root)
    if runner is None:
        return RunEvidence(
            name=str(check_id),
            check_id=str(check_id),
            source="runner",
            status="no_test_runner",
            ok=False,
            cmd="<none detected>",
            detail=(
                "no test runner is declared here; looked for: " + "; ".join(_SUITE_PROBES)
            ),
            ts=_time.time(),
            revision=int(revision or 0),
            workspace=str(workspace or ""),
            session=str(session or ""),
            suite=False,
        )
    runner_name, command = runner
    code, output = _run_command(command, repo_root, timeout)
    if code is None:
        status = "failed"
        detail = output if output else "the suite could not be run here"
    elif code == 0:
        status = "passed"
        detail = f"exit 0: {output}" if output else "exit 0"
    elif runner_name == "pytest" and code == 5:
        status = "no_tests_found"
        detail = f"exit 5 — nothing collected: {output}" if output else "exit 5 — nothing collected"
    elif _NO_TESTS_RE.search(output or ""):
        status = "no_tests_found"
        detail = f"exit {code} — the runner reports no tests: {output}" if output else (
            f"exit {code} — the runner reports no tests"
        )
    else:
        status = "failed"
        detail = f"exit {code}: {output}" if code is not None else output
    return RunEvidence(
        name=str(check_id),
        check_id=str(check_id),
        source="runner",
        status=status,
        ok=(status == "passed"),
        cmd=" ".join(command),
        detail=detail[:300],
        exit_code=code,
        runner=runner_name,
        ts=_time.time(),
        revision=int(revision or 0),
        workspace=str(workspace or ""),
        session=str(session or ""),
        suite=True,
    )


def _pytest_runner_evidence(
    check_id: str, repo_root: Path, *, timeout: int, revision: int, workspace: str,
    session: str,
) -> RunEvidence:
    """Run the project's own suite with the project's own interpreter.

    Kept for direct callers of the pytest runner — the check route goes through
    :func:`_project_test_evidence`, which detects the project's own runner first
    (audit F05: the goal run offered ``pytest`` even to a Node project).
    """
    import sys
    import time as _time

    venv_python = repo_root / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else sys.executable
    command = [python, "-m", "pytest", "-q"]
    code, output = _run_command(command, repo_root, timeout)
    status = "unknown" if code is None else ("passed" if code == 0 else "failed")
    if code is None:
        status = "failed"  # a suite that never finished is not a green suite
    detail = f"exit {code}: {output}" if code is not None else output
    return RunEvidence(
        name=str(check_id),
        check_id=str(check_id),
        source="runner",
        status=status,
        ok=(status == "passed"),
        cmd=" ".join(command),
        detail=detail[:300],
        exit_code=code,
        runner="pytest",
        ts=_time.time(),
        revision=int(revision or 0),
        workspace=str(workspace or ""),
        session=str(session or ""),
        suite=True,
    )


def _declared_argv_evidence(
    check_id: str,
    argv: Sequence[str],
    repo_root: Path,
    *,
    timeout: float,
    revision: int,
    workspace: str,
    session: str,
) -> RunEvidence:
    """Run one criterion's **declared command vector** — the completion gate's own producer.

    Both gates judge the vector through the same
    :func:`skaldir.verify_service.run_declared_criterion`, so the model's completion claim and
    the independent verification pass cannot disagree about what the command did (audit §6c:
    the completion gate had no runner for a free argv at all, so a claim resting on one was
    refused — safely, as ``UNKNOWN`` — and never certifiable). A command that could not even be
    executed here stays ``not-certifiable``; a command that ran and failed is a real ``failed``.
    """
    import time as _time

    from .verify_service import KIND_COMMAND, Criterion, run_declared_criterion

    check_id = str(check_id)
    cwd = str(workspace or repo_root)
    criterion = Criterion(
        text=check_id,
        criterion_id=check_id,
        check_id=check_id,
        kind=KIND_COMMAND,
        argv=tuple(str(part) for part in argv),
        declared_command=" ".join(str(part) for part in argv),
    )
    status, exit_code, output, reason, cmd = run_declared_criterion(
        criterion, workspace=cwd, timeout=float(timeout)
    )
    mapped = {"passed": "passed", "failed": "failed"}.get(status, NOT_CERTIFIABLE)
    detail = (
        f"exit {exit_code}: {reason}" if exit_code is not None else (reason or output)
    )
    return RunEvidence(
        name=check_id,
        check_id=check_id,
        source="runner",
        status=mapped,
        ok=(mapped == "passed"),
        cmd=str(cmd or " ".join(criterion.argv))[:200],
        detail=str(detail)[:300],
        exit_code=exit_code,
        ts=_time.time(),
        revision=int(revision or 0),
        workspace=str(workspace or ""),
        session=str(session or ""),
    )


def execute_check(
    check_id: str,
    repo_root,
    *,
    argv: Sequence[str] | None = None,
    timeout: int = 600,
    revision: int = 0,
    workspace: str = "",
    session: str = "",
) -> RunEvidence | None:
    """Execute one check for real — or return None when no runner exists.

    Every canonical check id has a runner now (Block 3): ``pytest``/``tests``/
    ``test`` run the project's suite, ``run``/``ui_e2e``/``api``/``build`` run
    the workspace-declared command, ``persistence_restart`` runs the declared
    restart cycle. A **command vector** (``argv`` — how a criterion declares its
    own check) is executed and judged like any other declared command, and a
    ``command``/``command:<id>`` id with no vector is honestly
    ``not-certifiable`` rather than a nameless ``None``. Checks without a
    declaration return ``not-certifiable`` evidence instead of a silent ``None``;
    only genuinely unknown ids return ``None``. All evidence carries the contract
    binding (revision, workspace, session).
    """
    import time as _time

    repo_root = Path(repo_root)
    check_id = str(check_id)
    vector = [str(part) for part in (argv or []) if str(part).strip()]
    if vector:
        return _declared_argv_evidence(
            check_id,
            vector,
            repo_root,
            timeout=float(timeout),
            revision=revision,
            workspace=workspace,
            session=session,
        )
    if check_id in ("command", "argv") or check_id.startswith("command:"):
        return _not_certifiable(
            check_id,
            "no command vector was declared for this criterion — a 'command' check "
            "must carry its argv",
            revision=revision,
            workspace=workspace,
            session=session,
        )
    custom = _CUSTOM_RUNNERS.get(check_id)
    if custom is not None:
        try:
            result = custom(repo_root)  # type: ignore[operator]
        except Exception as exc:  # a broken runner is an unverified state
            return RunEvidence(
                name=check_id,
                check_id=check_id,
                source="runner",
                status="failed",
                cmd=f"<custom runner {check_id}>",
                detail=f"runner error: {exc}"[:200],
                ts=_time.time(),
                revision=int(revision or 0),
                workspace=str(workspace or ""),
                session=str(session or ""),
            )
        if result is not None:
            # bind custom-runner evidence to the current contract
            if not getattr(result, "revision", 0):
                result.revision = int(revision or 0)
            if not getattr(result, "workspace", ""):
                result.workspace = str(workspace or "")
            if not getattr(result, "session", ""):
                result.session = str(session or "")
        return result
    if check_id in ("pytest", "tests", "test"):
        # F05: the project's OWN runner — npm for a Node project, pytest for a pytest
        # project, make/script where declared, and an honest state when nothing runs.
        return _suite_runner_evidence(
            check_id, repo_root, timeout=timeout, revision=revision,
            workspace=workspace, session=session,
        )
    if check_id == "persistence_restart":
        return _persistence_cycle_evidence(
            repo_root, timeout=timeout, revision=revision, workspace=workspace,
            session=session,
        )
    if check_id in _NAMED_CHECKS:
        return _declared_check_evidence(
            check_id, repo_root, timeout=timeout, revision=revision,
            workspace=workspace, session=session,
        )
    return None


def run_requirement_checks(
    dod,
    repo_root,
    *,
    timeout: int = 600,
    revision: int = 0,
    workspace: str = "",
    session: str = "",
) -> list[RunEvidence]:
    """Execute every executable check a definition-of-done asks for.

    Accepts DodItem objects or plain dicts. A criterion may declare its check as a **command
    vector** (``check_argv`` on the item): the vector is executed for *that* criterion, so two
    criteria with two different commands are two checks and neither collapses into the other
    (audit §6c). Items without a runner (unknown check ids) are skipped here — ``gate_dod``
    reports them; each distinct check runs at most once. ``session`` binds the produced evidence
    to the contract's session so a foreign store can never certify it.
    """
    wanted: list[tuple[str, tuple[str, ...]]] = []
    for item in dod or []:
        if isinstance(item, dict):
            value = item.get("verification")
            argv = item.get("check_argv") or item.get("argv") or ()
        else:
            value = getattr(item, "verification", "")
            argv = getattr(item, "check_argv", ()) or ()
        value = str(value or "").strip()
        vector = tuple(str(part) for part in argv if str(part).strip())
        if value and all(value != seen for seen, _ in wanted):
            wanted.append((value, vector))
    # Block 3 (deadlock fix, producer side): ``gate_tests`` demands a
    # runner-executed suite run whenever the workspace carries changed code.
    # If the contract declares no suite check, the run is added here — the gate
    # keeps its semantics, and the producer now actually produces what the gate
    # requires instead of leaving the contract permanently unclosable.
    if not any(str(check).lower() in ("pytest", "tests", "test") for check, _ in wanted):
        if workspace_has_code_changes(repo_root):
            wanted.append(("pytest", ()))
    results: list[RunEvidence] = []
    for check_id, vector in wanted:
        result = execute_check(
            check_id,
            repo_root,
            argv=vector,
            timeout=timeout,
            revision=revision,
            workspace=workspace,
            session=session,
        )
        if result is not None:
            results.append(result)
    return results
