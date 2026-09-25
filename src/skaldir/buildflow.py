"""Persistent, verifiable build flow — master prompt §4a/§4b (Welle 2, Block B7).

The builder used to be a straight line of stages with a JSONL trail. This module
adds the *persistent state machine* around it: every build has an identity
(build_id), the triggering session/goal revision, project context, the
normalized need, its source, budget, owner, timestamps and a durable event
history — and it moves through the master-prompt phases with **enforced**
transitions:

``need → search → decide → inspect → contract → adapter → verify → validate →
publish → bind → observe``

with the separate exits ``failed`` / ``blocked`` / ``cancelled`` /
``superseded``. The existing ``ToolBuilder.STAGES`` names are kept (they are
what the panel/queue renders and what ``events.KINDS`` pins); the mapping
stage → phase is :data:`STAGE_PHASE` and is test-enforced over the real
``toolbuilder.STAGES``.

Guarantees implemented here:

* **Idempotency** — a build key over (normalized need, project, relevant
  versions); two identical requests admit to ONE build, the second request is a
  subscriber.
* **Leases** — a worker owns a build for a bounded time and renews it; an
  expired lease can be reclaimed by recovery (a crashed/killed worker never
  blocks the build forever).
* **Crash recovery** — transitions are persisted *before* the external action of
  their phase; recovery reconciles a half-open phase against reality (the
  registry is the truth for ``publish``) and never invents a success.
* **Cancellation / supersession** — a cancelled build cannot publish; a
  subscriber whose goal revision moved cannot insert its result into a changed
  task (subscription version check).
* **Bounded repair** — at most ``DEFAULT_REPAIR_LIMIT`` (3, configurable)
  repair attempts inside one shared time/cost budget; infrastructure failures
  are repaired *as infrastructure* and never by rewriting the adapter; the same
  defect without new evidence ends the loop with a concrete blocker.
* **Acceptance before the adapter** — acceptance criteria are derived from the
  user need (and refused when they are only "exit 0"/"HTTP 200"/"container
  running"); builder cases and holdout cases are strictly separated, and a
  holdout opened for diagnosis counts as a *known regression test* — the next
  independent acceptance needs fresh cases.
* **Evidence package** — normalized need, search decision, inspected sources,
  manifest, code hashes, test commands, results, holdout provenance, limits,
  runtime probe; secrets stay handles (``secret://…``) and are redacted.

The store is a small SQLite database in the runtime directory
(``<runtime>/buildflow.db``), additive to the existing registry DB
(``tools.db``) — the publication outbox lives *there* because it must share the
registry transaction (§4d, see :mod:`skaldir.tools`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# phases
# --------------------------------------------------------------------------- #

PHASES: tuple[str, ...] = (
    "need",
    "search",
    "decide",
    "inspect",
    "contract",
    "adapter",
    "verify",
    "validate",
    "publish",
    "bind",
    "observe",
)

#: The separate exits (§4a). A terminal outcome is final — no further phase.
OUTCOMES: tuple[str, ...] = ("failed", "blocked", "cancelled", "superseded")
TERMINAL_OUTCOMES = frozenset(OUTCOMES)

#: Existing builder stages (`toolbuilder.STAGES`) → master-prompt phase.
#: Test-enforced: every entry of the real ``STAGES`` must appear here.
STAGE_PHASE: dict[str, str] = {
    "requested": "need",
    "researching": "search",
    "compose_check": "decide",
    "dependency_inspection": "inspect",
    "manifest": "contract",
    "generating": "adapter",
    "gates": "verify",
    "register": "publish",
    "ready": "bind",
}

#: Record-only stages that carry their own phase (not part of ``STAGES``).
EXTRA_STAGE_PHASE: dict[str, str] = {
    "drafted": "decide",
    "validate": "validate",
    "observe": "observe",
}

#: Forward order plus the deliberate repair edges (adapter re-entry only via a
#: recorded repair attempt — see :meth:`BuildFlowStore.transition`).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "": frozenset({"need"}),
    "need": frozenset({"search"}),
    "search": frozenset({"decide"}),
    # reuse/composition is not a failure — it SUPERSEDES this build.
    "decide": frozenset({"inspect", "superseded"}),
    "inspect": frozenset({"contract"}),
    "contract": frozenset({"adapter"}),
    "adapter": frozenset({"verify"}),
    # gates failed → one bounded repair round back to the adapter (or validate).
    "verify": frozenset({"validate", "adapter"}),
    "validate": frozenset({"publish", "adapter"}),
    "publish": frozenset({"bind"}),
    "bind": frozenset({"observe"}),
    "observe": frozenset(),
}

DEFAULT_REPAIR_LIMIT = 3
DEFAULT_LEASE_SECONDS = 120.0
DEFAULT_BUILD_BUDGET_SECONDS = 900.0

#: Acceptance criteria that prove nothing (they are a smoke check, not the
#: user's need) — refused before the adapter is ever built (§4b).
BANNED_ACCEPTANCE = (
    "exit 0",
    "exit code 0",
    "http 200",
    "http 2xx",
    "status 200",
    "response 200",
    "container running",
    "container running",
    "container is running",
    "no error",
    "returns ok",
    "command succeeds",
)

#: Evidence package fields (§4b). Secrets are referenced, never embedded.
EVIDENCE_FIELDS: tuple[str, ...] = (
    "need",
    "search_decision",
    "sources",
    "manifest",
    "code_hashes",
    "image_hash",
    "test_commands",
    "results",
    "holdout_provenance",
    "limits",
    "runtime_probe",
    "secrets",
)

#: Fields that may legitimately stay empty: a local tool has no image hash (the
#: limitation is then named in ``limits``), a build may need no credential.
EVIDENCE_OPTIONAL: tuple[str, ...] = ("image_hash", "secrets")

_INFRA_HINTS = (
    "sandbox",
    "bwrap",
    "no space left",
    "disk full",
    "permission denied",
    "connection refused",
    "network is unreachable",
    "dns",
    "docker",
    "podman",
    "timeout",
    "oom",
    "out of memory",
    "read-only file system",
)


class InvalidTransition(ValueError):
    """A phase transition that the state machine does not allow."""


class WrongLayerError(ValueError):
    """A repair that would rewrite the adapter for an infrastructure failure."""


class BuildCancelled(RuntimeError):
    """Raised when the build was cancelled while a worker was running it."""


class LeaseLost(RuntimeError):
    """Raised when a worker no longer owns the build's lease."""


# --------------------------------------------------------------------------- #
# identity helpers
# --------------------------------------------------------------------------- #

_NEED_NOISE = re.compile(r"[^a-z0-9äöüß]+")


def normalize_need(text: str) -> str:
    """Normalize a need for the lock/idempotency key (case, punctuation, order).

    Words are sorted so "convert money amounts" and "amounts convert money"
    share one key — a reformulated request is the same need, not a second build.
    """
    lowered = (text or "").lower()
    words = [word for word in _NEED_NOISE.split(lowered) if word]
    return " ".join(sorted(words))


def build_key(need: str, *, project: str = "", versions: list[str] | None = None) -> str:
    """Idempotency/lock key over (normalized need, project, relevant versions)."""
    version_blob = ",".join(sorted(str(item) for item in (versions or [])))
    material = f"{normalize_need(need)}|{project or ''}|{version_blob}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def defect_fingerprint(defect: str) -> str:
    """Stable fingerprint of a defect for the 'same error without evidence' rule."""
    return hashlib.sha256(normalize_need(defect).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #


@dataclass
class BuildRecord:
    """One persistent build: identity, context, phase and its history anchor."""

    build_id: str
    phase: str = "need"
    outcome: str = ""
    reason: str = ""
    build_key: str = ""
    tool_name: str = ""
    session_id: str = ""
    goal_revision: int = 0
    project_id: str = ""
    need: str = ""
    need_normalized: str = ""
    source: str = ""
    budget: dict = field(default_factory=dict)
    #: B6: requested container runtime ("" = local, "docker" = containerised);
    #: recorded with the build so recovery/registry can tell the two apart.
    runtime: str = ""
    owner: str = ""
    lease_until: float = 0.0
    created: float = 0.0
    updated: float = 0.0
    repairs: list[dict] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    bind_state: str = ""
    observed_at: float = 0.0
    completed_at: float = 0.0
    next_step: str = ""
    last_error: str = ""

    @property
    def terminal(self) -> bool:
        return self.phase in TERMINAL_OUTCOMES

    @property
    def status_text(self) -> str:
        """Human status — 'registered, binding pending' is its OWN state (§4a)."""
        if self.terminal:
            return f"{self.phase}: {self.reason}" if self.reason else self.phase
        if self.phase == "bind" and self.bind_state == "pending":
            return "published, binding pending"
        if self.phase == "observe" and not self.observed_at:
            return "published, not yet used"
        if self.observed_at:
            return "observed"
        return self.phase

    def to_dict(self) -> dict:
        payload = dict(self.__dict__)
        payload["terminal"] = self.terminal
        payload["status_text"] = self.status_text
        return payload


def _hash_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hashes_for(paths: list[str | Path]) -> dict[str, str]:
    """sha256 per existing path (missing paths are reported, never faked)."""
    out: dict[str, str] = {}
    for path in paths:
        candidate = Path(path)
        if candidate.is_file():
            out[str(candidate)] = _hash_file(candidate)
        else:
            out[str(candidate)] = "missing"
    return out


# --------------------------------------------------------------------------- #
# bounded repair (§4b)
# --------------------------------------------------------------------------- #


#: Which gates a repair re-triggers, per touched area (§4b): a change to rights,
#: image, input schema or dependencies may not slip past the gates it invalidates.
_CHANGE_GATES: dict[str, tuple[str, ...]] = {
    "rights": ("scan", "test"),
    "secrets": ("scan", "test"),
    "image": ("scan", "import", "test", "determinism"),
    "input_schema": ("import", "test", "determinism"),
    "dependencies": ("scan", "import", "test"),
    "adapter_code": ("scan", "import", "test", "determinism"),
}


def gates_to_rerun(changes: list[str]) -> list[str]:
    """The gates a repair must re-run, derived from what the repair touched."""
    wanted: list[str] = []
    unknown: list[str] = []
    for change in changes or []:
        key = str(change).strip().lower()
        if key in _CHANGE_GATES:
            for gate in _CHANGE_GATES[key]:
                if gate not in wanted:
                    wanted.append(gate)
        else:
            unknown.append(key)
    if unknown:  # an unnamed change is not allowed to skip a gate
        for gate in ("scan", "import", "test", "determinism"):
            if gate not in wanted:
                wanted.append(gate)
    return wanted


def classify_failure(detail: str) -> str:
    """Keyword classifier: ``"infrastructure"`` vs ``"adapter"`` (§4b).

    Deliberately simple and inspectable — a wrong classification is visible in
    the recorded repair attempt instead of hidden behind a heuristic. The
    rule it protects: an infrastructure failure is repaired *as infrastructure*
    and never by rewriting the adapter.
    """
    text = (detail or "").lower()
    return "infrastructure" if any(hint in text for hint in _INFRA_HINTS) else "adapter"


@dataclass
class RepairAttempt:
    attempt: int
    defect: str
    fingerprint: str
    change: str
    checks: list[str]
    kind: str  # adapter | infrastructure
    evidence_new: bool
    ts: float
    detail: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class RepairLoop:
    """At most ``limit`` repair attempts inside one shared budget (§4b).

    Rules (each test-enforced):

    1. the cap is ``limit`` (default 3, configurable) **and** the shared
       time/cost budget may end the loop earlier — both end as a COMPLETE
       error state (outcome, cause, last valid artifacts, next step);
    2. an infrastructure failure may not be repaired by rewriting the adapter
       (:class:`WrongLayerError`), it is a ``kind="infrastructure"`` attempt;
    3. the same defect fingerprint without new evidence ends the loop;
    4. every recorded attempt names defect, change and the affected checks.
    """

    def __init__(self, *, limit: int = DEFAULT_REPAIR_LIMIT) -> None:
        self.limit = int(limit)
        self.attempts: list[RepairAttempt] = []
        self._seen: dict[str, int] = {}

    # -- policy --------------------------------------------------------------
    def may_repair(
        self,
        *,
        defect: str,
        failure_kind: str = "adapter",
        evidence_new: bool = False,
        layer: str = "",
    ) -> tuple[bool, str]:
        """Decide whether another repair round is allowed; returns (ok, reason)."""
        target_layer = layer or failure_kind
        if failure_kind == "infrastructure" and target_layer == "adapter":
            return False, (
                "infrastructure failure — repair the infrastructure, not the adapter"
            )
        if failure_kind not in ("adapter", "infrastructure"):
            return False, f"unknown failure kind: {failure_kind}"
        if len(self.attempts) >= self.limit:
            return False, (
                f"repair limit reached ({len(self.attempts)}/{self.limit}) — "
                "no further attempt"
            )
        fingerprint = defect_fingerprint(defect)
        if fingerprint in self._seen and not evidence_new:
            return False, (
                "same defect without new evidence — loop ended "
                f"(defect {fingerprint}, attempt {self._seen[fingerprint]})"
            )
        return True, "repair allowed"

    def record(
        self,
        *,
        defect: str,
        change: str,
        checks: list[str],
        failure_kind: str = "adapter",
        kind: str = "adapter",
        evidence_new: bool = False,
        detail: str = "",
        ts: float | None = None,
    ) -> RepairAttempt:
        """Record one repair attempt (raises when policy refuses it)."""
        ok, reason = self.may_repair(
            defect=defect,
            failure_kind=failure_kind,
            evidence_new=evidence_new,
            layer=kind,
        )
        if not ok:
            if failure_kind == "infrastructure" and kind == "adapter":
                raise WrongLayerError(reason)
            raise InvalidTransition(reason)
        fingerprint = defect_fingerprint(defect)
        self._seen[fingerprint] = self._seen.get(fingerprint, 0) + 1
        attempt = RepairAttempt(
            attempt=len(self.attempts) + 1,
            defect=defect[:400],
            fingerprint=fingerprint,
            change=change[:400],
            checks=[str(item) for item in checks],
            kind=kind,
            evidence_new=bool(evidence_new),
            ts=time.time() if ts is None else float(ts),
            detail=detail[:400],
        )
        self.attempts.append(attempt)
        return attempt

    def state(self) -> dict:
        return {
            "limit": self.limit,
            "used": len(self.attempts),
            "remaining": max(0, self.limit - len(self.attempts)),
            "attempts": [item.to_dict() for item in self.attempts],
        }


# --------------------------------------------------------------------------- #
# acceptance + holdout (§4b)
# --------------------------------------------------------------------------- #


def acceptance_problems(criteria: list[str]) -> list[str]:
    """Refuse 'acceptance' that only proves the process ran (§4b).

    A criterion must exist and must be about the user's need — HTTP 200, exit 0
    and 'container running' are smoke checks, not acceptance.
    """
    problems: list[str] = []
    clean = [str(item).strip() for item in criteria or [] if str(item).strip()]
    if not clean:
        problems.append("no acceptance criteria — the need was never made concrete")
    for item in clean:
        lowered = item.lower()
        for banned in BANNED_ACCEPTANCE:
            if banned in lowered:
                problems.append(f"criterion proves nothing but the process: {item[:120]}")
                break
    return problems


def derive_acceptance_criteria(need: str, *, declared: list[str] | None = None) -> list[str]:
    """Acceptance criteria, generated BEFORE the adapter from the user need."""
    criteria = [str(item).strip() for item in (declared or []) if str(item).strip()]
    need_text = (need or "").strip()
    if need_text:
        criteria.append(
            f"the delivered capability fulfils the stated need: {need_text[:160]}"
        )
    return criteria


@dataclass
class AcceptanceSet:
    """Builder cases vs. withheld holdout cases — strictly separated (§4b).

    The repair process only ever sees :attr:`builder_cases`. A holdout that was
    opened for diagnosis is *burned*: it becomes a known regression test and no
    longer counts as an independent acceptance case — the next acceptance needs
    fresh cases (``fresh_cases_required``).
    """

    criteria: list[str] = field(default_factory=list)
    builder_cases: list[dict] = field(default_factory=list)
    holdout_cases: list[dict] = field(default_factory=list)
    burned: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.criteria = [str(item) for item in self.criteria]
        self.builder_cases = [self._case(item) for item in self.builder_cases]
        self.holdout_cases = [self._case(item) for item in self.holdout_cases]

    @staticmethod
    def _case(item: Any) -> dict:
        if isinstance(item, dict):
            case = dict(item)
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            case = {"target": str(item[0]), "expect_pass": bool(item[1])}
        else:
            case = {"target": str(item)}
        case.setdefault("id", str(case.get("target", "")))
        return case

    # -- holdout handling ----------------------------------------------------
    def open_for_diagnosis(self, case_id: str, *, reason: str = "") -> bool:
        """Open a holdout for diagnosis → it is burned and becomes known.

        Returns True when the returned case is now *known* (and therefore the
        next independent acceptance will need a fresh case).
        """
        self.burned[str(case_id)] = reason or "opened for diagnosis"
        return True

    def fresh_holdout(self) -> list[dict]:
        """Holdout cases that are still independent (never opened)."""
        return [case for case in self.holdout_cases if case["id"] not in self.burned]

    @property
    def fresh_cases_required(self) -> bool:
        return bool(self.burned)

    def known_regression_tests(self) -> list[dict]:
        """Burned holdouts — usable as regression tests, not as acceptance."""
        return [case for case in self.holdout_cases if case["id"] in self.burned]

    def repair_view(self) -> dict:
        """What the repair process may see: builder cases + criteria, NO holdout."""
        return {
            "criteria": list(self.criteria),
            "builder_cases": [dict(case) for case in self.builder_cases],
            "holdout_visible": False,
        }

    def state(self) -> dict:
        return {
            "criteria": list(self.criteria),
            "builder_cases": len(self.builder_cases),
            "holdout_cases": len(self.holdout_cases),
            "fresh_holdout_cases": len(self.fresh_holdout()),
            "burned": dict(self.burned),
            "known_regression_tests": [case["id"] for case in self.known_regression_tests()],
            "fresh_cases_required": self.fresh_cases_required,
        }


# --------------------------------------------------------------------------- #
# evidence package (§4b)
# --------------------------------------------------------------------------- #


def evidence_package(
    *,
    need: str = "",
    search_decision: Any = None,
    sources: list | None = None,
    manifest: str = "",
    code_hashes: dict | None = None,
    image_hash: str = "",
    test_commands: list | None = None,
    results: list | None = None,
    holdout_provenance: Any = None,
    limits: list | None = None,
    runtime_probe: Any = None,
    secrets: list | None = None,
    extra: dict | None = None,
) -> dict:
    """Build the evidence bundle of a published build; secrets stay handles.

    Every string field is scanned: a real secret VALUE would be redacted here
    (and its count recorded) — the package references credentials as
    ``secret://…`` handles only.
    """
    from .secretscan import redact

    package: dict = {
        "need": str(need or ""),
        "search_decision": search_decision if search_decision is not None else {},
        "sources": list(sources or []),
        "manifest": str(manifest or ""),
        "code_hashes": dict(code_hashes or {}),
        "image_hash": str(image_hash or ""),
        "test_commands": list(test_commands or []),
        "results": list(results or []),
        "holdout_provenance": holdout_provenance if holdout_provenance is not None else {},
        "limits": list(limits or []),
        "runtime_probe": runtime_probe if runtime_probe is not None else {},
        "secrets": [str(item) for item in (secrets or [])],
        "redactions": 0,
        "created": time.time(),
    }
    if extra:
        for key, value in extra.items():
            package.setdefault(str(key), value)
    blob = json.dumps(package, ensure_ascii=False, default=str)
    redacted_text, findings = redact(blob)
    package["redactions"] = len(findings)
    if findings:
        package = json.loads(redacted_text)
        package["redactions"] = len(findings)
    return package


def evidence_missing(package: dict) -> list[str]:
    """Fields the package still owes ([] = complete).

    ``image_hash`` and ``secrets`` are optional (:data:`EVIDENCE_OPTIONAL`): a
    local (non-container) tool has no image, and a build may need no credential
    — both cases belong in ``limits``, not in a fake value.
    """
    missing: list[str] = []
    for name in EVIDENCE_FIELDS:
        if name in EVIDENCE_OPTIONAL:
            continue
        value = package.get(name)
        if value in (None, "", [], {}):
            missing.append(name)
    return missing


# --------------------------------------------------------------------------- #
# persistence
# --------------------------------------------------------------------------- #

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS builds (
        build_id        TEXT PRIMARY KEY,
        build_key       TEXT NOT NULL DEFAULT '',
        tool_name       TEXT NOT NULL DEFAULT '',
        phase           TEXT NOT NULL,
        outcome         TEXT NOT NULL DEFAULT '',
        reason          TEXT NOT NULL DEFAULT '',
        session_id      TEXT NOT NULL DEFAULT '',
        goal_revision   INTEGER NOT NULL DEFAULT 0,
        project_id      TEXT NOT NULL DEFAULT '',
        need            TEXT NOT NULL DEFAULT '',
        need_normalized TEXT NOT NULL DEFAULT '',
        source          TEXT NOT NULL DEFAULT '',
        budget          TEXT NOT NULL DEFAULT '{}',
        runtime         TEXT NOT NULL DEFAULT '',
        owner           TEXT NOT NULL DEFAULT '',
        lease_until     REAL NOT NULL DEFAULT 0,
        created         REAL NOT NULL DEFAULT 0,
        updated         REAL NOT NULL DEFAULT 0,
        repairs         TEXT NOT NULL DEFAULT '[]',
        artifacts       TEXT NOT NULL DEFAULT '[]',
        evidence        TEXT NOT NULL DEFAULT '{}',
        bind_state      TEXT NOT NULL DEFAULT '',
        observed_at     REAL NOT NULL DEFAULT 0,
        completed_at    REAL NOT NULL DEFAULT 0,
        next_step       TEXT NOT NULL DEFAULT '',
        last_error      TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS build_flow_events (
        seq        INTEGER PRIMARY KEY AUTOINCREMENT,
        build_id   TEXT NOT NULL,
        ts         REAL NOT NULL,
        kind       TEXT NOT NULL,
        phase      TEXT NOT NULL DEFAULT '',
        from_phase TEXT NOT NULL DEFAULT '',
        detail     TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS build_subscriptions (
        build_id      TEXT NOT NULL,
        session_id    TEXT NOT NULL,
        goal_revision INTEGER NOT NULL DEFAULT 0,
        ts            REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (build_id, session_id)
    )
    """,
)

_RECORD_COLUMNS = (
    "build_id", "build_key", "tool_name", "phase", "outcome", "reason", "session_id",
    "goal_revision", "project_id", "need", "need_normalized", "source", "budget",
    "runtime", "owner", "lease_until", "created", "updated", "repairs", "artifacts",
    "evidence", "bind_state", "observed_at", "completed_at", "next_step", "last_error",
)


class BuildFlowStore:
    """SQLite-backed persistence of build state, history, subscriptions, leases."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            for statement in _SCHEMA:
                self._db.execute(statement)
            self._db.commit()

    # -- plumbing ------------------------------------------------------------
    def _event_locked(
        self,
        build_id: str,
        kind: str,
        *,
        phase: str = "",
        from_phase: str = "",
        detail: dict | None = None,
        ts: float | None = None,
    ) -> None:
        self._db.execute(
            "INSERT INTO build_flow_events(build_id, ts, kind, phase, from_phase, detail) "
            "VALUES (?,?,?,?,?,?)",
            (
                build_id,
                time.time() if ts is None else float(ts),
                kind,
                phase,
                from_phase,
                json.dumps(detail or {}, ensure_ascii=False, default=str),
            ),
        )

    @staticmethod
    def _row_to_record(row: tuple) -> BuildRecord:
        data = dict(zip(_RECORD_COLUMNS, row, strict=False))
        for key in ("budget", "repairs", "artifacts", "evidence"):
            try:
                data[key] = json.loads(data[key])
            except (TypeError, json.JSONDecodeError):
                data[key] = [] if key in ("repairs", "artifacts") else {}
        return BuildRecord(**data)

    def _get_locked(self, build_id: str) -> BuildRecord | None:
        row = self._db.execute(
            f"SELECT {', '.join(_RECORD_COLUMNS)} FROM builds WHERE build_id = ?",  # nosec B608
            (build_id,),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def get(self, build_id: str) -> BuildRecord | None:
        with self._lock:
            return self._get_locked(build_id)

    def active(self) -> list[BuildRecord]:
        """Non-terminal builds, oldest first (what recovery has to look at)."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {', '.join(_RECORD_COLUMNS)} FROM builds "  # nosec B608
                "WHERE phase NOT IN (?,?,?,?) ORDER BY created",
                OUTCOMES,
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def history(self, build_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT seq, ts, kind, phase, from_phase, detail FROM build_flow_events "
                "WHERE build_id = ? ORDER BY seq",
                (build_id,),
            ).fetchall()
        out: list[dict] = []
        for seq, ts, kind, phase, from_phase, detail in rows:
            try:
                payload = json.loads(detail)
            except json.JSONDecodeError:
                payload = {}
            out.append(
                {
                    "seq": seq,
                    "ts": ts,
                    "kind": kind,
                    "phase": phase,
                    "from_phase": from_phase,
                    "detail": payload,
                }
            )
        return out

    # -- admission: identity, idempotency, lock ------------------------------
    def admit(
        self,
        *,
        need: str,
        build_id: str = "",
        project: str = "",
        versions: list[str] | None = None,
        session_id: str = "",
        goal_revision: int = 0,
        source: str = "",
        budget: dict | None = None,
        runtime: str = "",
        owner: str = "",
        tool_name: str = "",
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        ts: float | None = None,
    ) -> tuple[BuildRecord, bool]:
        """Admit a build request; two identical requests admit to ONE build.

        Returns ``(record, created)``. ``created=False`` means an identical
        (normalized need, project, versions) build is already running — the
        caller subscribes instead of building a duplicate.
        """
        now = time.time() if ts is None else float(ts)
        key = build_key(need, project=project, versions=versions)
        with self._lock:
            # the lock covers the BUILDING phases only: once a build is
            # published (bind/observe) the capability exists, and an identical
            # request is a normal new request (reuse/search answers it).
            building = PHASES[: PHASES.index("publish") + 1]
            placeholders = ",".join("?" for _ in building)
            row = self._db.execute(
                f"SELECT {', '.join(_RECORD_COLUMNS)} FROM builds "  # nosec B608
                f"WHERE build_key = ? AND phase IN ({placeholders}) ORDER BY created LIMIT 1",
                (key, *building),
            ).fetchone()
            if row is not None:
                existing = self._row_to_record(row)
                self._subscribe_locked(existing.build_id, session_id, goal_revision, now)
                self._db.commit()
                return existing, False
            new_id = build_id or f"build-{os.urandom(5).hex()}"
            self._db.execute(
                "INSERT INTO builds(build_id, build_key, tool_name, phase, outcome, reason, "
                "session_id, goal_revision, project_id, need, need_normalized, source, budget, "
                "runtime, owner, lease_until, created, updated, repairs, artifacts, evidence, "
                "bind_state, observed_at, completed_at, next_step, last_error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    new_id, key, tool_name, "need", "", "", session_id,
                    int(goal_revision or 0), project, need, normalize_need(need), source,
                    json.dumps(budget or {}, ensure_ascii=False), runtime, owner,
                    now + float(lease_seconds), now, now, "[]", "[]", "{}", "", 0.0, 0.0,
                    "", "",
                ),
            )
            self._event_locked(
                new_id, "created", phase="need", from_phase="",
                detail={"need": need[:400]}, ts=now,
            )
            # the requesting session is a subscriber of its own build (its goal
            # revision is part of the subscription — §4a)
            self._subscribe_locked(new_id, session_id, goal_revision, now)
            self._db.commit()
            record = self._get_locked(new_id)
        assert record is not None
        return record, True

    # -- transitions ---------------------------------------------------------
    def transition(
        self,
        build_id: str,
        target: str,
        *,
        detail: dict | None = None,
        reason: str = "",
        repair_attempt: int | None = None,
        ts: float | None = None,
    ) -> BuildRecord:
        """Move a build to ``target``; raises :class:`InvalidTransition`.

        Rules: only registered phases/outcomes; terminal states are final;
        forward edges come from :data:`ALLOWED_TRANSITIONS`; a backwards edge
        (repair) requires the number of a recorded repair attempt.
        """
        now = time.time() if ts is None else float(ts)
        with self._lock:
            record = self._get_locked(build_id)
            if record is None:
                raise InvalidTransition(f"unknown build: {build_id}")
            if target not in PHASES and target not in OUTCOMES:
                raise InvalidTransition(f"unknown phase: {target}")
            if record.terminal:
                raise InvalidTransition(
                    f"build {build_id} is terminal ({record.phase}) — no further transition"
                )
            if target not in OUTCOMES:
                allowed = ALLOWED_TRANSITIONS.get(record.phase, frozenset())
                if target not in allowed:
                    raise InvalidTransition(
                        f"{record.phase} → {target} is not allowed "
                        f"(allowed: {sorted(allowed) or 'none'})"
                    )
                if PHASES.index(target) < PHASES.index(record.phase) and repair_attempt is None:
                    raise InvalidTransition(
                        f"{record.phase} → {target} is a repair edge and needs a "
                        "recorded repair attempt"
                    )
            if target in OUTCOMES:
                self._db.execute(
                    "UPDATE builds SET phase = ?, outcome = ?, reason = ?, updated = ?, "
                    "completed_at = ? WHERE build_id = ?",
                    (target, target, reason[:400], now, now, build_id),
                )
            else:
                self._db.execute(
                    "UPDATE builds SET phase = ?, reason = ?, updated = ? WHERE build_id = ?",
                    (target, reason[:400], now, build_id),
                )
            self._event_locked(
                build_id, "enter", phase=target, from_phase=record.phase,
                detail={**(detail or {}), "repair_attempt": repair_attempt}, ts=now,
            )
            self._db.commit()
            updated = self._get_locked(build_id)
        assert updated is not None
        return updated

    def enter(self, build_id: str, phase: str, *, detail: dict | None = None) -> BuildRecord:
        """Enter a phase BEFORE its external action (the recovery anchor)."""
        return self.transition(build_id, phase, detail=detail)

    def step_done(
        self, build_id: str, phase: str, *, detail: dict | None = None, ts: float | None = None
    ) -> None:
        """Record that the external action of ``phase`` completed (with results)."""
        with self._lock:
            self._event_locked(
                build_id, "done", phase=phase, from_phase=phase, detail=detail, ts=ts
            )
            self._db.execute(
                "UPDATE builds SET updated = ? WHERE build_id = ?",
                (time.time() if ts is None else float(ts), build_id),
            )
            self._db.commit()

    def note(self, build_id: str, detail: dict, *, kind: str = "note") -> None:
        with self._lock:
            self._event_locked(build_id, kind, detail=detail)
            self._db.commit()

    def attach(
        self,
        build_id: str,
        *,
        artifacts: list | None = None,
        evidence: dict | None = None,
    ) -> BuildRecord:
        """Attach the durable artifacts/evidence summary of a phase (e.g. publish)."""
        with self._lock:
            record = self._get_locked(build_id)
            if record is None:
                raise InvalidTransition(f"unknown build: {build_id}")
            merged_artifacts = list(record.artifacts)
            for item in artifacts or []:
                if str(item) not in merged_artifacts:
                    merged_artifacts.append(str(item))
            merged_evidence = dict(record.evidence or {})
            merged_evidence.update(evidence or {})
            self._db.execute(
                "UPDATE builds SET artifacts = ?, evidence = ?, updated = ? WHERE build_id = ?",
                (
                    json.dumps(merged_artifacts, ensure_ascii=False),
                    json.dumps(merged_evidence, ensure_ascii=False, default=str),
                    time.time(),
                    build_id,
                ),
            )
            self._event_locked(
                build_id, "attach", phase=record.phase,
                detail={"artifacts": merged_artifacts, "evidence": merged_evidence},
            )
            self._db.commit()
            updated = self._get_locked(build_id)
        assert updated is not None
        return updated

    def open_step(self, build_id: str) -> dict | None:
        """The phase that was entered but never recorded as done (crash marker)."""
        record = self.get(build_id)
        if record is None or record.terminal:
            return None
        events = self.history(build_id)
        entered = False
        for event in events:
            if event["kind"] == "enter" and event["phase"] == record.phase:
                entered = True
            elif event["kind"] == "done" and event["phase"] == record.phase:
                entered = False
        return {"build_id": build_id, "phase": record.phase} if entered else None

    # -- outcomes ------------------------------------------------------------
    def cancel(self, build_id: str, *, reason: str = "", by: str = "") -> BuildRecord:
        """Cancel a build: no publish follows, the reason is durable."""
        return self.transition(
            build_id, "cancelled", reason=reason or "cancelled", detail={"by": by}
        )

    def finish(
        self,
        build_id: str,
        outcome: str,
        *,
        reason: str = "",
        artifacts: list[str] | None = None,
        next_step: str = "",
        evidence: dict | None = None,
        error: str = "",
    ) -> BuildRecord:
        """Close a build with a COMPLETE error state (phase, cause, artifacts, next)."""
        if outcome not in OUTCOMES:
            raise InvalidTransition(f"not an outcome: {outcome}")
        with self._lock:
            self._db.execute(
                "UPDATE builds SET artifacts = ?, next_step = ?, last_error = ? "
                "WHERE build_id = ?",
                (
                    json.dumps(list(artifacts or []), ensure_ascii=False),
                    next_step[:400],
                    error[:400],
                    build_id,
                ),
            )
            self._db.commit()
        return self.transition(build_id, outcome, reason=reason)

    # -- subscriptions -------------------------------------------------------
    def _subscribe_locked(
        self, build_id: str, session_id: str, goal_revision: int, now: float
    ) -> None:
        if not session_id:
            return
        self._db.execute(
            "INSERT INTO build_subscriptions(build_id, session_id, goal_revision, ts) "
            "VALUES (?,?,?,?) ON CONFLICT(build_id, session_id) DO UPDATE SET "
            "goal_revision = excluded.goal_revision, ts = excluded.ts",
            (build_id, session_id, int(goal_revision or 0), now),
        )

    def subscribe(
        self, build_id: str, session_id: str, *, goal_revision: int = 0, ts: float | None = None
    ) -> None:
        """A second session subscribes to the SAME build with its own goal revision."""
        with self._lock:
            self._subscribe_locked(
                build_id, session_id, goal_revision, time.time() if ts is None else float(ts)
            )
            self._db.commit()

    def subscribers(self, build_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT session_id, goal_revision, ts FROM build_subscriptions "
                "WHERE build_id = ? ORDER BY session_id",
                (build_id,),
            ).fetchall()
        return [
            {"session_id": row[0], "goal_revision": row[1], "ts": row[2]} for row in rows
        ]

    def subscription_ok(
        self, build_id: str, session_id: str, goal_revision: int
    ) -> tuple[bool, str]:
        """Subscription version check: may THIS session still insert the result?

        False when the build was cancelled or the subscription's goal revision
        no longer matches — a late build must not slip into a changed task.
        """
        record = self.get(build_id)
        if record is None:
            return False, f"unknown build: {build_id}"
        if record.phase == "cancelled":
            return False, f"build was cancelled: {record.reason or 'no reason recorded'}"
        if record.terminal:
            return False, f"build is {record.phase}: {record.reason or 'closed'}"
        for entry in self.subscribers(build_id):
            if entry["session_id"] == session_id:
                if int(entry["goal_revision"]) != int(goal_revision or 0):
                    return False, (
                        f"goal revision changed for session {session_id}: "
                        f"build was subscribed at {entry['goal_revision']}, "
                        f"request carries {goal_revision}"
                    )
                return True, "subscription current"
        return True, "no subscription recorded for this session (single-request build)"

    # -- leases --------------------------------------------------------------
    def acquire(
        self,
        build_id: str,
        owner: str,
        *,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        ts: float | None = None,
    ) -> bool:
        """Take the worker lease; False while another live owner holds it."""
        now = time.time() if ts is None else float(ts)
        with self._lock:
            record = self._get_locked(build_id)
            if record is None:
                return False
            if record.owner and record.owner != owner and record.lease_until > now:
                return False
            self._db.execute(
                "UPDATE builds SET owner = ?, lease_until = ?, updated = ? WHERE build_id = ?",
                (owner, now + float(lease_seconds), now, build_id),
            )
            self._event_locked(
                build_id, "lease", phase=record.phase, detail={"owner": owner},
            )
            self._db.commit()
        return True

    def renew(
        self,
        build_id: str,
        owner: str,
        *,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        ts: float | None = None,
    ) -> bool:
        now = time.time() if ts is None else float(ts)
        with self._lock:
            record = self._get_locked(build_id)
            if record is None or record.owner != owner:
                return False
            if record.lease_until and record.lease_until < now:
                return False
            self._db.execute(
                "UPDATE builds SET lease_until = ?, updated = ? WHERE build_id = ?",
                (now + float(lease_seconds), now, build_id),
            )
            self._db.commit()
        return True

    def lease_ok(self, build_id: str, owner: str, *, ts: float | None = None) -> bool:
        now = time.time() if ts is None else float(ts)
        record = self.get(build_id)
        if record is None:
            return False
        if record.owner and record.owner != owner:
            return False
        return not (record.lease_until and record.lease_until < now)

    # -- bind / observe ------------------------------------------------------
    def mark_bind(self, build_id: str, state: str, *, detail: dict | None = None) -> BuildRecord:
        """Record session binding: ``"pending"`` is its OWN state (§4a)."""
        if state not in ("pending", "applied"):
            raise InvalidTransition(f"unknown bind state: {state}")
        with self._lock:
            self._db.execute(
                "UPDATE builds SET bind_state = ?, updated = ? WHERE build_id = ?",
                (state, time.time(), build_id),
            )
            self._event_locked(
                build_id, "bind", phase="bind", from_phase="bind",
                detail={**(detail or {}), "state": state},
            )
            self._db.commit()
            record = self._get_locked(build_id)
        assert record is not None
        return record

    def observe(self, build_id: str, *, detail: dict | None = None) -> BuildRecord:
        """First real call/result/health of the published capability (§4a)."""
        now = time.time()
        with self._lock:
            self._db.execute(
                "UPDATE builds SET observed_at = ?, completed_at = ?, updated = ? "
                "WHERE build_id = ?",
                (now, now, now, build_id),
            )
            self._event_locked(build_id, "observed", phase="observe", detail=detail, ts=now)
            self._db.commit()
        record = self.get(build_id)
        if record is None:
            raise InvalidTransition(f"unknown build: {build_id}")
        if record.phase == "bind":
            return self.transition(build_id, "observe", detail=detail or {})
        return record

    # -- recovery ------------------------------------------------------------
    def recover(
        self,
        *,
        registry: Callable[[str], bool] | None = None,
        now: float | None = None,
        name_of: Callable[[BuildRecord], str] | None = None,
    ) -> dict:
        """Reconcile persisted state with reality after a crash/restart.

        * an expired lease is reclaimed (reported, not silently extended);
        * a build whose phase was entered but never completed is an OPEN step:
          for ``publish`` the registry decides — tool present ⇒ the commit
          landed (advance to ``bind``), tool absent ⇒ ``blocked`` with the last
          valid artifacts and a concrete next step (never a silent success);
        * every other open step is reported for the caller (the caller knows its
          own artifacts).
        """
        moment = time.time() if now is None else float(now)
        report: dict = {"reclaimed": [], "open_steps": [], "reconciled": [], "blocked": []}
        for record in self.active():
            if record.lease_until and record.lease_until < moment:
                with self._lock:
                    self._db.execute(
                        "UPDATE builds SET owner = '', updated = ? WHERE build_id = ?",
                        (moment, record.build_id),
                    )
                    self._event_locked(
                        record.build_id, "lease_expired", phase=record.phase,
                        detail={"owner": record.owner, "expired": record.lease_until},
                        ts=moment,
                    )
                    self._db.commit()
                report["reclaimed"].append(
                    {"build_id": record.build_id, "owner": record.owner}
                )
            step = self.open_step(record.build_id)
            if step is None:
                continue
            report["open_steps"].append(step)
            tool_name = name_of(record) if name_of is not None else record.tool_name
            if step["phase"] == "publish" and registry is not None and tool_name:
                if registry(tool_name):
                    report["reconciled"].append(
                        {"build_id": record.build_id, "action": "publish had landed → bind"}
                    )
                    try:
                        self.transition(
                            record.build_id, "bind",
                            detail={"recovered": "publish commit found in registry"},
                        )
                    except InvalidTransition:
                        pass
                else:
                    reason = (
                        "publish did not land (crash between transition and commit) — "
                        f"tool '{tool_name}' is not in the registry"
                    )
                    report["blocked"].append({"build_id": record.build_id, "reason": reason})
                    try:
                        self.finish(
                            record.build_id,
                            "blocked",
                            reason=reason,
                            artifacts=list(record.artifacts),
                            next_step="re-run the publish step with the recorded manifest",
                            error=reason,
                        )
                    except InvalidTransition:
                        pass
        report["pending_publications"] = self.pending_publications()
        return report

    def pending_publications(self) -> list[dict]:
        """Placeholder seam — the real outbox lives in the registry DB (§4d).

        Kept so callers (and tests) can ask the flow store for its view without
        importing the registry; :meth:`ToolBuilder.recover` fills it from
        ``ToolStore.pending_outbox()``.
        """
        return []

    def close(self) -> None:
        with self._lock:
            self._db.close()


__all__ = [
    "ALLOWED_TRANSITIONS",
    "BANNED_ACCEPTANCE",
    "DEFAULT_BUILD_BUDGET_SECONDS",
    "DEFAULT_LEASE_SECONDS",
    "DEFAULT_REPAIR_LIMIT",
    "EVIDENCE_FIELDS",
    "AcceptanceSet",
    "BuildCancelled",
    "BuildFlowStore",
    "BuildRecord",
    "InvalidTransition",
    "LeaseLost",
    "OUTCOMES",
    "PHASES",
    "RepairAttempt",
    "RepairLoop",
    "STAGE_PHASE",
    "TERMINAL_OUTCOMES",
    "WrongLayerError",
    "acceptance_problems",
    "build_key",
    "classify_failure",
    "defect_fingerprint",
    "derive_acceptance_criteria",
    "evidence_missing",
    "evidence_package",
    "gates_to_rerun",
    "hashes_for",
    "normalize_need",
]
