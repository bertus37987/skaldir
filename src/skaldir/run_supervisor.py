"""Persistent run supervisor — execution responsibility that survives a crash.

Invariant (the whole point of this module):

    NON_TERMINAL_RUN => ACTIVE_WORKER_LEASE
                        OR QUEUED_CONTINUATION
                        OR EXPLICIT_WAIT_WITH_WAKE_SOURCE

A non-terminal run carrying none of those three is **not** a quietly sleeping
run — it is a lifecycle error: nobody owns the work, no queue entry exists, and
nobody waits for a wake source. :func:`sweep` finds those runs and moves each of
them into exactly one defined state, without inventing work and without touching
state that another owner owns:

* terminal runs are untouchable (``TERMINAL_UNTOUCHED``),
* user pause, safety block, approval and budget stop are policy-owned and are
  **never** overwritten by recovery (``POLICY_OWNED_UNTOUCHED``) — not even when
  they technically violate the invariant,
* an orphaned or expired lease without queue and without wait is either re-queued
  (one new continuation generation, exactly once) or, when an action may be in
  flight whose idempotency is unproven and whose result cannot be verified,
  parked in ``UNKNOWN_SIDE_EFFECT_STATE`` with an operator wake source.

Exactly-once continuation: the queue keys every row on ``(run_id, generation)``
(UNIQUE) and every state change is a single conditional UPDATE
(compare-and-swap on ``state`` / ``continuation_generation``). A crash between
"redirect delivered" and "continue executed" therefore yields exactly one
resumption, and a duplicated replay cannot count twice.

Persistence: one SQLite file per runtime (same pattern as ``pauses.db`` /
``events.db`` in the plugin runtime dir), WAL, ``RLock``, ``CREATE TABLE IF NOT
EXISTS`` plus an additive ``ALTER TABLE`` migration, so pre-existing rows and
older schemas keep working.

See ``docs/RUN-LIFECYCLE.md`` for the implemented-versus-open split.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

# --- the invariant -----------------------------------------------------------

INVARIANT = (
    "NON_TERMINAL_RUN => ACTIVE_WORKER_LEASE OR QUEUED_CONTINUATION "
    "OR EXPLICIT_WAIT_WITH_WAKE_SOURCE"
)

ACTIVE_WORKER_LEASE = "ACTIVE_WORKER_LEASE"
QUEUED_CONTINUATION = "QUEUED_CONTINUATION"
EXPLICIT_WAIT_WITH_WAKE_SOURCE = "EXPLICIT_WAIT_WITH_WAKE_SOURCE"
DISJUNCTS = (ACTIVE_WORKER_LEASE, QUEUED_CONTINUATION, EXPLICIT_WAIT_WITH_WAKE_SOURCE)

# --- run states --------------------------------------------------------------

QUEUED = "QUEUED"
ACTIVE = "ACTIVE"
CONTINUING = "CONTINUING"
WAITING = "WAITING"
UNKNOWN_SIDE_EFFECT_STATE = "UNKNOWN_SIDE_EFFECT_STATE"
LEGACY_ORPHAN = "LEGACY_ORPHAN"
COMPLETE = "COMPLETE"
FAILED = "FAILED"
CANCELLED = "CANCELLED"
ABANDONED = "ABANDONED"
USER_PAUSED = "USER_PAUSED"
SAFETY_BLOCKED = "SAFETY_BLOCKED"
APPROVAL_PENDING = "APPROVAL_PENDING"
BUDGET_STOPPED = "BUDGET_STOPPED"

TERMINAL_RUN_STATES = frozenset({COMPLETE, FAILED, CANCELLED, ABANDONED})

# state -> the wake source that alone may lift it. Recovery never lifts these.
POLICY_OWNED_WAKE_SOURCES: dict[str, str] = {
    USER_PAUSED: "user:resume",
    SAFETY_BLOCKED: "safety:clearance",
    APPROVAL_PENDING: "approval:decision",
    BUDGET_STOPPED: "budget:top_up",
}
PROTECTED_STATES = frozenset(POLICY_OWNED_WAKE_SOURCES)
OPERATOR_WAKE_SOURCE = "operator:side_effect_verified"

RUN_STATES = (
    QUEUED,
    ACTIVE,
    CONTINUING,
    WAITING,
    UNKNOWN_SIDE_EFFECT_STATE,
    LEGACY_ORPHAN,
    USER_PAUSED,
    SAFETY_BLOCKED,
    APPROVAL_PENDING,
    BUDGET_STOPPED,
    COMPLETE,
    FAILED,
    CANCELLED,
    ABANDONED,
)

# --- continuation status -----------------------------------------------------

CONTINUATION_QUEUED = "queued"
CONTINUATION_CLAIMED = "claimed"
CONTINUATION_COMMITTED = "committed"
CONTINUATION_SUPERSEDED = "superseded"

# --- violations / repair actions ---------------------------------------------

ORPHANED_LEASE = "ORPHANED_LEASE"
EXPIRED_LEASE = "EXPIRED_LEASE"
STALE_HEARTBEAT = "STALE_HEARTBEAT"
ORPHANED_CONTINUATION = "ORPHANED_CONTINUATION"
NO_QUEUE_NO_WAIT = "NO_QUEUE_NO_WAIT"

TERMINAL_UNTOUCHED = "TERMINAL_UNTOUCHED"
POLICY_OWNED_UNTOUCHED = "POLICY_OWNED_UNTOUCHED"
HEALTHY = "HEALTHY"
WAITING_ON_WAKE_SOURCE = "WAITING_ON_WAKE_SOURCE"
REQUEUED = "REQUEUED"
PARKED_UNKNOWN_SIDE_EFFECT = "PARKED_UNKNOWN_SIDE_EFFECT"
SUPERSEDED_BY_OTHER = "SUPERSEDED_BY_OTHER"
UNKNOWN_RUN = "UNKNOWN_RUN"

# --- side-effect rule --------------------------------------------------------

DISPOSITION_IDLE = "IDLE"  # nothing in flight -> repeating is safe
DISPOSITION_REPEAT = "REPEAT"  # idempotent == true
DISPOSITION_RESOLVED = "RESOLVED"  # result existence verified
DISPOSITION_UNKNOWN = "UNKNOWN"  # -> UNKNOWN_SIDE_EFFECT_STATE

DEFAULT_LEASE_TTL = 120.0
DEFAULT_HEARTBEAT_STALE = 90.0
WATCHDOG_INTERVAL = 2.5

HOST_LEASES_DB = "state.db"
_PID_RE = re.compile(r"pid=(\d+)")

RUN_FIELDS = (
    "run_id",
    "session_id",
    "goal_revision",
    "state",
    "next_owner",
    "pending_action",
    "continuation_generation",
    "lease_owner",
    "lease_expires_at",
    "heartbeat_at",
    "wait_reason",
    "wake_source",
    "retry_count",
    "recovery_count",
    "last_progress_at",
    "last_committed_event",
    # --- §94 PROGRESS-GUARD: the durable per-turn progress signals + the action taken --
    # Before these, a resumed run knew *time* had passed (``last_progress_at``) but not
    # that the answer had stopped changing — it could not tell a slow run from a stuck
    # one. These fields are the difference, and they are why the stall survives a crash.
    "turn",
    "no_progress_streak",
    "no_tool_streak",
    "last_verification",
    "reply_fingerprint",
    #: the action the progress guard last demanded (``continue`` / ``strategy_change_required``
    #: / ``stop``) and the named signal behind it — so a resumed run is not re-nudged with a
    #: directive it was already given.
    "progress_action",
    "progress_signal",
    #: the budget state at that turn: tokens spent, and the declared budget (0 = unbounded)
    "tokens_used",
    "token_budget",
)

_RUN_DEFAULTS: dict[str, object] = {
    "session_id": "",
    "goal_revision": 0,
    "state": QUEUED,
    "next_owner": "worker",
    "pending_action": "",
    "continuation_generation": 0,
    "lease_owner": "",
    "lease_expires_at": 0.0,
    "heartbeat_at": 0.0,
    "wait_reason": "",
    "wake_source": "",
    "retry_count": 0,
    "recovery_count": 0,
    "last_progress_at": 0.0,
    "last_committed_event": 0,
    "turn": 0,
    "no_progress_streak": 0,
    "no_tool_streak": 0,
    "last_verification": "",
    "reply_fingerprint": "",
    "progress_action": "",
    "progress_signal": "",
    "tokens_used": 0,
    "token_budget": 0,
}

_INT_FIELDS = frozenset(
    {
        "goal_revision",
        "continuation_generation",
        "retry_count",
        "recovery_count",
        "last_committed_event",
        "turn",
        "no_progress_streak",
        "no_tool_streak",
        "tokens_used",
        "token_budget",
    }
)
_FLOAT_FIELDS = frozenset({"lease_expires_at", "heartbeat_at", "last_progress_at"})


def pid_alive(pid: int | None) -> bool:
    """Signal-0 probe: ESRCH means dead, EPERM means alive-but-foreign."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def owner_pid(lease_owner: str) -> int | None:
    """``pid=88437:turn=…`` -> 88437 (the host turn-lease holder format)."""
    match = _PID_RE.search(str(lease_owner or ""))
    return int(match.group(1)) if match else None


def hermes_home() -> Path:
    from .env import host_getenv  # local: this module stays importable on its own

    return Path(host_getenv("HERMES_HOME") or (Path.home() / ".hermes"))


def runtime_dir(home: str | Path | None = None) -> Path:
    """The runtime dir the plugin stores its state in (``$HERMES_HOME/skaldir``)."""
    if home is not None:
        return Path(home)
    try:
        from .registry import default_runtime_dir

        return Path(default_runtime_dir())
    except Exception:  # noqa: BLE001 — standalone use has no registry
        return hermes_home() / "skaldir"


def run_store_path(state_dir: str | Path | None = None) -> Path:
    return Path(state_dir or runtime_dir()) / "runs.db"


def field_names() -> tuple[str, ...]:
    """The exact run-record field list (test-enforced contract)."""
    return RUN_FIELDS


# --- run record --------------------------------------------------------------


@dataclass
class Run:
    """The persistent run record — exactly the 16 required fields, no more."""

    run_id: str
    session_id: str = ""
    goal_revision: int = 0
    state: str = QUEUED
    next_owner: str = "worker"
    pending_action: str = ""
    continuation_generation: int = 0
    lease_owner: str = ""
    lease_expires_at: float = 0.0
    heartbeat_at: float = 0.0
    wait_reason: str = ""
    wake_source: str = ""
    retry_count: int = 0
    recovery_count: int = 0
    last_progress_at: float = 0.0
    last_committed_event: int = 0
    # §94 PROGRESS-GUARD — the durable per-turn progress signals (see RUN_FIELDS)
    turn: int = 0
    no_progress_streak: int = 0
    no_tool_streak: int = 0
    last_verification: str = ""
    reply_fingerprint: str = ""
    progress_action: str = ""
    progress_signal: str = ""
    tokens_used: int = 0
    token_budget: int = 0

    FIELDS = RUN_FIELDS

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_RUN_STATES

    @property
    def non_terminal(self) -> bool:
        return not self.terminal

    @property
    def protected(self) -> bool:
        """Policy-owned: recovery must never overwrite this state."""
        return self.state in PROTECTED_STATES

    def to_dict(self) -> dict:
        return asdict(self)


def run_from_record(record: dict) -> Run:
    """Tolerant reader: missing or legacy columns fall back to defaults."""
    values: dict = {}
    for field in RUN_FIELDS:
        raw = record.get(field)
        if field in _INT_FIELDS:
            values[field] = int(raw) if raw not in (None, "") else 0
        elif field in _FLOAT_FIELDS:
            values[field] = float(raw) if raw not in (None, "") else 0.0
        else:
            values[field] = "" if raw is None else str(raw)
    return Run(**values)


# --- the store ---------------------------------------------------------------


class RunStore:
    """SQLite-backed run records + continuation queue (thread-safe, WAL)."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._brief_epoch_fences: dict[tuple[str, str], int] = {}
        self._run_locks: dict[str, threading.RLock] = {}
        self._run_locks_guard = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id                 TEXT PRIMARY KEY,
                    session_id             TEXT NOT NULL DEFAULT '',
                    goal_revision          INTEGER NOT NULL DEFAULT 0,
                    state                  TEXT NOT NULL DEFAULT '',
                    next_owner             TEXT NOT NULL DEFAULT '',
                    pending_action         TEXT NOT NULL DEFAULT '',
                    continuation_generation INTEGER NOT NULL DEFAULT 0,
                    lease_owner            TEXT NOT NULL DEFAULT '',
                    lease_expires_at       REAL NOT NULL DEFAULT 0,
                    heartbeat_at           REAL NOT NULL DEFAULT 0,
                    wait_reason            TEXT NOT NULL DEFAULT '',
                    wake_source            TEXT NOT NULL DEFAULT '',
                    retry_count            INTEGER NOT NULL DEFAULT 0,
                    recovery_count         INTEGER NOT NULL DEFAULT 0,
                    last_progress_at       REAL NOT NULL DEFAULT 0,
                    last_committed_event   INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS continuations (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id          TEXT NOT NULL,
                    session_id      TEXT NOT NULL DEFAULT '',
                    generation      INTEGER NOT NULL,
                    status          TEXT NOT NULL,
                    reason          TEXT NOT NULL DEFAULT '',
                    redirect        TEXT NOT NULL DEFAULT '',
                    idempotent      INTEGER NOT NULL DEFAULT 0,
                    result_verified INTEGER NOT NULL DEFAULT 0,
                    created         REAL NOT NULL DEFAULT 0,
                    claimed_at      REAL NOT NULL DEFAULT 0,
                    claimed_by      TEXT NOT NULL DEFAULT '',
                    committed_at    REAL NOT NULL DEFAULT 0,
                    UNIQUE(run_id, generation)
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS run_actions (
                    action      TEXT PRIMARY KEY,
                    idempotent  INTEGER NOT NULL DEFAULT 0,
                    result_path TEXT NOT NULL DEFAULT '',
                    verified_at REAL NOT NULL DEFAULT 0,
                    note        TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS run_audit (
                    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts         REAL NOT NULL,
                    run_id     TEXT NOT NULL,
                    action     TEXT NOT NULL,
                    from_state TEXT NOT NULL DEFAULT '',
                    to_state   TEXT NOT NULL DEFAULT '',
                    detail     TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS subagent_briefs (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id         TEXT NOT NULL,
                    assignment_id  TEXT NOT NULL,
                    goal_id        TEXT NOT NULL DEFAULT '',
                    base_revision  TEXT NOT NULL DEFAULT '',
                    payload        TEXT NOT NULL DEFAULT '',
                    status         TEXT NOT NULL DEFAULT 'created',
                    recorded_at    REAL NOT NULL DEFAULT 0,
                    UNIQUE(run_id, assignment_id)
                )
                """
            )
            self._db.execute("CREATE INDEX IF NOT EXISTS runs_state ON runs(state)")
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS continuations_run ON continuations(run_id, status)"
            )
            self._migrate_locked()
            self._db.commit()

    # -- migration ----------------------------------------------------------
    def _migrate_locked(self) -> None:
        """Additive migration: a pre-existing ``runs`` table gains what it lacks.

        Old rows are never rewritten here — a record whose ``state`` is not a
        known run state is normalised on read into ``LEGACY_ORPHAN`` with an
        operator wake source (see :meth:`_normalize`), so nothing breaks and
        nothing is silently acted upon.
        """
        existing = {str(row["name"]) for row in self._db.execute("PRAGMA table_info(runs)")}
        for field in RUN_FIELDS:
            if field in existing:
                continue
            if field in _INT_FIELDS:
                column_type, default = "INTEGER", "0"
            elif field in _FLOAT_FIELDS:
                column_type, default = "REAL", "0"
            else:
                column_type, default = "TEXT", "''"
            # Identifier and default come from fixed constants above, never from
            # user input; no values are interpolated.
            self._db.execute(  # nosec B608
                f"ALTER TABLE runs ADD COLUMN {field} {column_type} NOT NULL DEFAULT {default}"
            )

    # -- writes -------------------------------------------------------------
    def create_run(
        self,
        session_id: str = "",
        *,
        run_id: str = "",
        state: str = QUEUED,
        now: float | None = None,
        **fields,
    ) -> Run:
        if state not in RUN_STATES:
            raise ValueError(f"unknown run state: {state!r}")
        moment = time.time() if now is None else float(now)
        unknown = set(fields) - set(RUN_FIELDS)
        if unknown:
            raise ValueError(f"unknown run fields: {sorted(unknown)}")
        run_id = run_id or f"run-{uuid.uuid4().hex[:10]}"
        record = {
            **_RUN_DEFAULTS,
            "run_id": run_id,
            "session_id": session_id,
            "state": state,
            "last_progress_at": moment,
            "heartbeat_at": moment,
        }
        record.update({key: value for key, value in fields.items() if key != "run_id"})
        placeholders = ", ".join("?" for _ in RUN_FIELDS)
        with self._lock:
            self._db.execute(
                f"INSERT INTO runs({', '.join(RUN_FIELDS)}) VALUES ({placeholders})"  # nosec B608
                " ON CONFLICT(run_id) DO NOTHING",
                tuple(record[field] for field in RUN_FIELDS),
            )
            self._db.commit()
        run = self.get(run_id)
        if run is None:  # pragma: no cover — defensive
            raise RuntimeError(f"run {run_id} vanished after insert")
        return run

    def update(
        self,
        run_id: str,
        *,
        expect: dict | None = None,
        allow_protected: bool = False,
        now: float | None = None,
        **fields,
    ) -> bool:
        """Conditional update — the only write path for run state.

        Refuses terminal runs, refuses policy-owned states unless the caller
        explicitly opts in (``allow_protected``: only the owner of that state may
        do that), and honours ``expect`` as a compare-and-swap so two concurrent
        recoveries cannot both act on the same observation.
        """
        moment = time.time() if now is None else float(now)
        unknown = set(fields) - set(RUN_FIELDS)
        if unknown:
            raise ValueError(f"unknown run fields: {sorted(unknown)}")
        if "state" in fields and fields["state"] not in RUN_STATES:
            raise ValueError(f"unknown run state: {fields['state']!r}")
        if not fields:
            return False
        current = self.get(run_id)
        if current is None or current.terminal:
            return False
        if current.protected and not allow_protected:
            return False
        snapshot = current.to_dict()
        for key, value in (expect or {}).items():
            if snapshot.get(key) != value:
                return False
        assignments = ", ".join(f"{field} = ?" for field in fields)
        with self._lock:
            cursor = self._db.execute(  # nosec B608
                f"UPDATE runs SET {assignments} WHERE run_id = ? AND state = ?",  # nosec B608
                (*fields.values(), run_id, current.state),
            )
            self._db.commit()
        if cursor.rowcount <= 0:
            return False
        self.audit_append(
            run_id,
            "update",
            from_state=current.state,
            to_state=str(fields.get("state") or current.state),
            detail=", ".join(f"{key}={fields[key]}" for key in sorted(fields)),
            now=moment,
        )
        return True

    def audit_append(
        self,
        run_id: str,
        action: str,
        *,
        from_state: str = "",
        to_state: str = "",
        detail: str = "",
        now: float | None = None,
    ) -> int:
        moment = time.time() if now is None else float(now)
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO run_audit(ts, run_id, action, from_state, to_state, detail)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (moment, run_id, action, from_state, to_state, str(detail)[:2000]),
            )
            self._db.commit()
        return int(cursor.lastrowid or 0)

    # -- leases -------------------------------------------------------------
    def grant_lease(
        self, run_id: str, owner: str, *, ttl: float = DEFAULT_LEASE_TTL, now: float | None = None
    ) -> bool:
        moment = time.time() if now is None else float(now)
        return self.update(
            run_id,
            lease_owner=str(owner),
            lease_expires_at=moment + float(ttl),
            heartbeat_at=moment,
            now=moment,
        )

    def heartbeat(
        self,
        run_id: str,
        owner: str,
        *,
        ttl: float = DEFAULT_LEASE_TTL,
        now: float | None = None,
    ) -> bool:
        moment = time.time() if now is None else float(now)
        run = self.get(run_id)
        if run is None or run.lease_owner != str(owner):
            return False
        return self.update(
            run_id,
            lease_expires_at=moment + float(ttl),
            heartbeat_at=moment,
            last_progress_at=moment,
            now=moment,
        )

    def release_lease(self, run_id: str, *, reason: str = "", now: float | None = None) -> bool:
        moment = time.time() if now is None else float(now)
        run = self.get(run_id)
        if run is None or not run.lease_owner:
            return False
        released = self.update(
            run_id,
            lease_owner="",
            lease_expires_at=0.0,
            expect={"lease_owner": run.lease_owner},
            now=moment,
        )
        if released:
            self.audit_append(run_id, "lease_released", detail=reason, now=moment)
        return released

    # -- waits / holds ------------------------------------------------------
    def wait(
        self,
        run_id: str,
        *,
        reason: str,
        wake_source: str,
        next_owner: str = "waker",
        now: float | None = None,
    ) -> bool:
        if not str(reason).strip() or not str(wake_source).strip():
            raise ValueError("an explicit wait needs both a reason and a wake source")
        moment = time.time() if now is None else float(now)
        return self.update(
            run_id,
            state=WAITING,
            next_owner=next_owner,
            wait_reason=str(reason),
            wake_source=str(wake_source),
            lease_owner="",
            lease_expires_at=0.0,
            now=moment,
        )

    def hold(
        self,
        run_id: str,
        state: str,
        *,
        reason: str,
        next_owner: str = "owner",
        now: float | None = None,
    ) -> bool:
        """Park a run under a policy owner (user/safety/approval/budget).

        The wake source is fixed by the policy itself — no caller can hand it a
        different one, and :meth:`update` refuses to touch these states
        afterwards without an explicit opt-in.
        """
        if state not in POLICY_OWNED_WAKE_SOURCES:
            raise ValueError(f"not a policy-owned state: {state!r}")
        moment = time.time() if now is None else float(now)
        return self.update(
            run_id,
            state=state,
            next_owner=next_owner,
            wait_reason=str(reason or state),
            wake_source=POLICY_OWNED_WAKE_SOURCES[state],
            lease_owner="",
            lease_expires_at=0.0,
            allow_protected=True,
            now=moment,
        )

    def wake(
        self,
        run_id: str,
        *,
        source: str,
        next_owner: str = "worker",
        now: float | None = None,
    ) -> bool:
        """Lift a hold/wait — only with the wake source that state requires."""
        run = self.get(run_id)
        if run is None or not run.wake_source or str(source) != run.wake_source:
            return False
        moment = time.time() if now is None else float(now)
        return self.update(
            run_id,
            state=QUEUED,
            next_owner=next_owner,
            wait_reason="",
            wake_source="",
            allow_protected=True,
            now=moment,
        )

    # -- continuation queue -------------------------------------------------
    def enqueue_continuation(
        self,
        run_id: str,
        *,
        generation: int | None = None,
        reason: str = "",
        redirect: str = "",
        idempotent: bool = False,
        result_verified: bool = False,
        session_id: str = "",
        now: float | None = None,
    ) -> tuple[int, bool]:
        """Enqueue ONE continuation for ``(run_id, generation)``.

        Returns ``(continuation_id, created)``. ``(run_id, generation)`` is
        UNIQUE and the insert is ``ON CONFLICT DO NOTHING``, so a repeated
        enqueue after a crash can never produce a second resumption.
        """
        moment = time.time() if now is None else float(now)
        run = self.get(run_id)
        if run is None:
            raise ValueError(f"unknown run: {run_id}")
        target = int(run.continuation_generation) + 1 if generation is None else int(generation)
        with self._lock:
            cursor = self._db.execute(
                """
                INSERT INTO continuations(run_id, session_id, generation, status, reason,
                    redirect, idempotent, result_verified, created)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, generation) DO NOTHING
                """,
                (
                    run_id,
                    session_id or run.session_id,
                    target,
                    CONTINUATION_QUEUED,
                    str(reason),
                    str(redirect),
                    int(bool(idempotent)),
                    int(bool(result_verified)),
                    moment,
                ),
            )
            self._db.commit()
            if cursor.rowcount > 0:
                return int(cursor.lastrowid or 0), True
            row = self._db.execute(
                "SELECT id FROM continuations WHERE run_id = ? AND generation = ?",
                (run_id, target),
            ).fetchone()
        return (int(row["id"]) if row else 0), False

    def continuation_for(self, run_id: str, generation: int) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM continuations WHERE run_id = ? AND generation = ?",
                (run_id, int(generation)),
            ).fetchone()
        return dict(row) if row else None

    def continuations(self, run_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM continuations WHERE run_id = ? ORDER BY generation", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def open_continuation(self, run_id: str) -> dict | None:
        """Latest continuation that is neither committed nor superseded/dropped."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM continuations WHERE run_id = ? AND status IN (?, ?)"
                " ORDER BY generation DESC LIMIT 1",
                (run_id, CONTINUATION_QUEUED, CONTINUATION_CLAIMED),
            ).fetchone()
        return dict(row) if row else None

    def claim_continuation(
        self,
        run_id: str,
        *,
        owner: str,
        ttl: float = DEFAULT_LEASE_TTL,
        generation: int | None = None,
        now: float | None = None,
    ) -> dict | None:
        """Claim exactly one queued continuation (``queued -> claimed``).

        The conditional UPDATE is the exactly-once gate: a second claimer — the
        same process, another process, or the same process after a restart —
        gets ``None``, because the row is no longer ``queued``.
        """
        moment = time.time() if now is None else float(now)
        run = self.get(run_id)
        if run is None or run.terminal or run.protected:
            return None
        target = int(run.continuation_generation if generation is None else generation)
        with self._lock:
            cursor = self._db.execute(
                "UPDATE continuations SET status = ?, claimed_at = ?, claimed_by = ?"
                " WHERE run_id = ? AND generation = ? AND status = ?",
                (
                    CONTINUATION_CLAIMED,
                    moment,
                    str(owner),
                    run_id,
                    target,
                    CONTINUATION_QUEUED,
                ),
            )
            self._db.commit()
        if cursor.rowcount <= 0:
            return None
        granted = self.update(
            run_id,
            state=CONTINUING,
            next_owner=str(owner),
            lease_owner=str(owner),
            lease_expires_at=moment + float(ttl),
            heartbeat_at=moment,
            expect={"continuation_generation": target},
            now=moment,
        )
        if not granted:
            # another writer moved the run between claim and grant -> undo
            self._requeue_row(run_id, target)
            return None
        self.audit_append(
            run_id,
            "continuation_claimed",
            from_state=run.state,
            to_state=CONTINUING,
            detail=f"generation={target} owner={owner}",
            now=moment,
        )
        return self.continuation_for(run_id, target)

    def commit_continuation(
        self,
        run_id: str,
        *,
        generation: int,
        last_committed_event: int = 0,
        owner: str = "",
        now: float | None = None,
    ) -> bool:
        """Commit one claimed continuation — exactly once per generation.

        The second attempt returns ``False`` and changes nothing (no event
        counter movement, no extra history): duplicate execution does not count.
        """
        moment = time.time() if now is None else float(now)
        with self._lock:
            cursor = self._db.execute(
                "UPDATE continuations SET status = ?, committed_at = ?"
                " WHERE run_id = ? AND generation = ? AND status IN (?, ?)",
                (
                    CONTINUATION_COMMITTED,
                    moment,
                    run_id,
                    int(generation),
                    CONTINUATION_QUEUED,
                    CONTINUATION_CLAIMED,
                ),
            )
            self._db.commit()
        if cursor.rowcount <= 0:
            return False
        run = self.get(run_id)
        if run is None:  # pragma: no cover — defensive
            return False
        # ``last_committed_event`` only ever moves forward.
        committed_event = max(int(run.last_committed_event), int(last_committed_event or 0))
        updated = self.update(
            run_id,
            state=ACTIVE,
            next_owner=str(owner or run.next_owner or "worker"),
            pending_action="",
            wait_reason="",
            wake_source="",
            continuation_generation=max(int(run.continuation_generation), int(generation)),
            last_committed_event=committed_event,
            last_progress_at=moment,
            heartbeat_at=moment,
            now=moment,
        )
        self.audit_append(
            run_id,
            "continuation_committed",
            from_state=run.state,
            to_state=ACTIVE,
            detail=f"generation={int(generation)} event={committed_event}",
            now=moment,
        )
        return bool(updated)

    def supersede_open_continuations(
        self,
        run_id: str,
        *,
        below: int | None = None,
        reason: str = "",
        now: float | None = None,
    ) -> int:
        """Mark open continuations superseded.

        ``below`` restricts the sweep to generations *older* than that number —
        recovery must never kill a continuation a concurrent recovery just
        created for the current generation (that would lose exactly-once
        resumption instead of protecting it).
        """
        moment = time.time() if now is None else float(now)
        query = "UPDATE continuations SET status = ? WHERE run_id = ? AND status IN (?, ?)"
        params: list = [CONTINUATION_SUPERSEDED, run_id, CONTINUATION_QUEUED,
                        CONTINUATION_CLAIMED]
        if below is not None:
            query += " AND generation < ?"
            params.append(int(below))
        with self._lock:
            cursor = self._db.execute(query, params)
            self._db.commit()
        if cursor.rowcount:
            self.audit_append(run_id, "continuations_superseded", detail=reason, now=moment)
        return int(cursor.rowcount or 0)

    def requeue_continuation(
        self,
        run_id: str,
        generation: int,
        *,
        idempotent: bool | None = None,
        result_verified: bool | None = None,
        reason: str = "",
        now: float | None = None,
    ) -> bool:
        """Re-queue an existing (queued or claimed) continuation — SAME generation.

        Recovery uses this when the previous owner died between claim and commit.
        Re-queuing the *same* generation is what keeps resumption exactly-once: a
        new generation here would be a second logical continuation, i.e. exactly
        the double execution the crash window must not produce. Terminal rows
        (committed/superseded/dropped) are never re-opened.
        """
        assignments = ["status = ?", "claimed_at = 0", "claimed_by = ''"]
        values: list = [CONTINUATION_QUEUED]
        if idempotent is not None:
            assignments.append("idempotent = ?")
            values.append(int(bool(idempotent)))
        if result_verified is not None:
            assignments.append("result_verified = ?")
            values.append(int(bool(result_verified)))
        if reason:
            assignments.append("reason = ?")
            values.append(str(reason))
        values.extend([run_id, int(generation)])
        with self._lock:
            cursor = self._db.execute(
                f"UPDATE continuations SET {', '.join(assignments)}"  # nosec B608
                " WHERE run_id = ? AND generation = ? AND status IN (?, ?)",
                (*values, CONTINUATION_QUEUED, CONTINUATION_CLAIMED),
            )
            self._db.commit()
        return cursor.rowcount > 0

    def _requeue_row(self, run_id: str, generation: int) -> bool:
        """Undo a claim that could not be granted a lease (internal rollback)."""
        with self._lock:
            cursor = self._db.execute(
                "UPDATE continuations SET status = ?, claimed_at = 0, claimed_by = ''"
                " WHERE run_id = ? AND generation = ? AND status = ?",
                (CONTINUATION_QUEUED, run_id, int(generation), CONTINUATION_CLAIMED),
            )
            self._db.commit()
        return cursor.rowcount > 0

    # -- side-effect registry ----------------------------------------------
    def register_action(
        self,
        action: str,
        *,
        idempotent: bool = False,
        result_path: str = "",
        note: str = "",
    ) -> dict:
        with self._lock:
            self._db.execute(
                """
                INSERT INTO run_actions(action, idempotent, result_path, note)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(action) DO UPDATE SET
                    idempotent = excluded.idempotent,
                    result_path = excluded.result_path,
                    note = excluded.note
                """,
                (str(action), int(bool(idempotent)), str(result_path), str(note)),
            )
            self._db.commit()
        return self.action_info(action) or {}

    def verify_result(self, action: str, *, exists: bool, now: float | None = None) -> bool:
        """Record the *verified* existence of the action's result (only True)."""
        if not exists:
            return False
        moment = time.time() if now is None else float(now)
        with self._lock:
            self._db.execute(
                "INSERT INTO run_actions(action, idempotent, result_path, verified_at, note)"
                " VALUES (?, 0, '', ?, 'result-exists')"
                " ON CONFLICT(action) DO UPDATE SET verified_at = excluded.verified_at",
                (str(action), moment),
            )
            self._db.commit()
        return True

    def action_info(self, action: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM run_actions WHERE action = ?", (str(action),)
            ).fetchone()
        return dict(row) if row else None

    # -- per-run serialisation ---------------------------------------------
    def run_lock(self, run_id: str) -> threading.RLock:
        """A lock bound to ONE run id.

        Recovery reads a run, decides, and writes it back across several
        statements. Two supervisors doing that concurrently would both observe
        the same stale record and both "repair" it (the live race: recovery_count
        moved to 2 while only one continuation existed). The whole decision is
        therefore taken under a per-run lock; different runs stay independent.
        """
        with self._run_locks_guard:
            lock = self._run_locks.get(run_id)
            if lock is None:
                lock = threading.RLock()
                self._run_locks[run_id] = lock
        return lock

    # -- reads --------------------------------------------------------------
    def get(self, run_id: str) -> Run | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return self._normalize(run_from_record(dict(row)))

    def by_session(self, session_id: str) -> Run | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM runs WHERE session_id = ? ORDER BY last_progress_at DESC LIMIT 1",
                (str(session_id),),
            ).fetchone()
        return self._normalize(run_from_record(dict(row))) if row else None

    def all_runs(self) -> list[Run]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM runs ORDER BY last_progress_at, run_id"
            ).fetchall()
        return [self._normalize(run_from_record(dict(row))) for row in rows]

    def non_terminal(self) -> list[Run]:
        return [run for run in self.all_runs() if run.non_terminal]

    def audit(self, run_id: str, *, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM run_audit WHERE run_id = ? ORDER BY seq LIMIT ?",
                (run_id, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- subagent briefs (VD-16) --------------------------------------------
    def enqueue_brief(self, run_id: str, brief: object, *, now: float | None = None) -> bool:
        """Register a subagent assignment brief for a run (idempotent).

        The brief must be a valid contract (validation happens inside it);
        ``(run_id, assignment_id)`` is UNIQUE so a re-enqueue never duplicates.
        """
        from .subagent_briefs import SubagentBrief

        if not isinstance(brief, SubagentBrief):
            raise ValueError(f"not a SubagentBrief: {type(brief).__name__}")
        moment = time.time() if now is None else float(now)
        if self.get(run_id) is None:
            raise ValueError(f"unknown run: {run_id}")
        payload = json.dumps(
            {
                "assignment_id": brief.assignment_id,
                "goal_id": brief.goal_id,
                "base_revision": brief.base_revision,
                "allowed_write_paths": brief.allowed_write_paths,
                "read_context": brief.read_context,
                "owner": brief.owner,
                "owner_epoch": brief.owner_epoch,
                "deadline_monotonic": brief.deadline_monotonic,
                "acceptance": brief.acceptance,
                "worktree": brief.worktree,
                "worker_kind": brief.worker_kind,
                "budget_reservation": brief.budget_reservation,
                "status": brief.status,
                "side_effect_state": brief.side_effect_state,
            },
            sort_keys=True,
        )
        with self._lock:
            cursor = self._db.execute(
                """
                INSERT INTO subagent_briefs(
                    run_id, assignment_id, goal_id, base_revision, payload,
                    status, recorded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, assignment_id) DO NOTHING
                """,
                (
                    run_id,
                    brief.assignment_id,
                    brief.goal_id,
                    brief.base_revision,
                    payload,
                    brief.status,
                    moment,
                ),
            )
            self._db.commit()
            created = cursor.rowcount > 0
        if created:
            self.audit_append(
                run_id,
                "subagent_brief_enqueued",
                detail=f"assignment={brief.assignment_id} owner={brief.owner}"
                f" epoch={brief.owner_epoch}",
                now=moment,
            )
        return True

    def briefs(self, run_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM subagent_briefs WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_brief_result(self, run_id: str, brief: object, decision: object) -> bool:
        """Persist the latest brief status after a fenced result/timeout.

        GV2-SEC-12: the decision's owner/epoch is checked against the PERSISTED
        brief record — a fabricated epoch/owner that never came from a real
        fencing check is rejected and audited as ``subagent_brief_result_rejected``.
        """
        from .subagent_briefs import BriefDecision, SubagentBrief

        if not isinstance(brief, SubagentBrief) or not isinstance(decision, BriefDecision):
            raise ValueError("record_brief_result needs (SubagentBrief, BriefDecision)")
        if not str(decision.reason or "").strip() and not decision.accepted:
            raise ValueError("record_brief_result: rejected decisions are not persisted")
        moment = time.time()
        with self._lock:
            row = self._db.execute(
                "SELECT payload FROM subagent_briefs WHERE run_id = ? AND assignment_id = ?",
                (run_id, brief.assignment_id),
            ).fetchone()
            if row is None:
                self.audit_append(
                    run_id,
                    "subagent_brief_result_rejected",
                    detail=(
                        f"assignment={brief.assignment_id} reason=unknown_assignment"
                        " (no persisted brief for this run)"
                    ),
                    now=moment,
                )
                return False
            try:
                persisted_payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError):
                persisted_payload = {}
            persisted_owner = persisted_payload.get("owner")
            persisted_epoch = persisted_payload.get("owner_epoch")
            if (
                brief.owner != persisted_owner
                or int(brief.owner_epoch) != int(persisted_epoch or 0)
            ):
                self.audit_append(
                    run_id,
                    "subagent_brief_result_rejected",
                    detail=(
                        f"assignment={brief.assignment_id} outcome={decision.outcome} "
                        f"accepted={decision.accepted} "
                        f"claimed_owner={brief.owner} claimed_epoch={brief.owner_epoch} "
                        f"persisted_owner={persisted_owner} persisted_epoch={persisted_epoch} "
                        "reason=fencing_mismatch (brief owner/epoch differ from the "
                        "persisted record — fabricated or stale caller object)"
                    ),
                    now=moment,
                )
                return False
            seen_epoch = self._brief_epoch_fences.get((run_id, brief.assignment_id), 0)
            if int(brief.owner_epoch) < seen_epoch:
                self.audit_append(
                    run_id,
                    "subagent_brief_result_rejected",
                    detail=(
                        f"assignment={brief.assignment_id} outcome={decision.outcome} "
                        f"accepted={decision.accepted} "
                        f"reason=monotonic_epoch_regression (epoch {brief.owner_epoch}"
                        f" < already-seen {seen_epoch})"
                    ),
                    now=moment,
                )
                return False
            self._brief_epoch_fences[(run_id, brief.assignment_id)] = int(brief.owner_epoch)
            # GV2-SEC-12b: a decision may never claim a BETTER outcome than the
            # brief's own declared lifecycle state. The store derives the truth
            # from `brief.status` (what the coordinator's fencing accepted) and
            # rejects outcome upgrades ("delivered" claimed from a timed-out or
            # aborted brief — the audit trail must not record the lie).
            _TRUTH_RANK = {
                "delivered": 3,
                "timed_out_partial": 2,
                "timed_out_empty": 1,
                "aborted": 0,
            }
            claimed = str(decision.outcome or "").strip()
            truth = str(brief.status or "").strip()
            if (
                claimed in _TRUTH_RANK
                and truth in _TRUTH_RANK
                and _TRUTH_RANK[claimed] > _TRUTH_RANK[truth]
            ):
                self.audit_append(
                    run_id,
                    "subagent_brief_result_rejected",
                    detail=(
                        f"assignment={brief.assignment_id} claimed_outcome={claimed}"
                        f" true_status={truth} reason=outcome_lie"
                        " (decision claims a better outcome than the brief's own state)"
                    ),
                    now=moment,
                )
                return False
            self._db.execute(
                "UPDATE subagent_briefs SET payload = ?, status = ?, recorded_at = ?"
                " WHERE run_id = ? AND assignment_id = ?",
                (
                    json.dumps(
                        {
                            "status": brief.status,
                            "owner": brief.owner,
                            "owner_epoch": brief.owner_epoch,
                            "outcome": decision.outcome,
                            "accepted": decision.accepted,
                            "side_effects_unknown": decision.side_effects_unknown,
                            "side_effect_state": brief.side_effect_state,
                        },
                        sort_keys=True,
                    ),
                    brief.status,
                    moment,
                )
                + (run_id, brief.assignment_id),
            )
            self._db.commit()
        self.audit_append(
            run_id,
            "subagent_brief_result",
            to_state=brief.status,
            detail=f"assignment={brief.assignment_id} outcome={decision.outcome}"
            f" accepted={decision.accepted}",
            now=moment,
        )
        return True

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _normalize(run: Run) -> Run:
        """Legacy/unknown state -> an explicit operator wait, never a silent fix.

        A row written by an older schema (or by hand) keeps its data; only an
        unknown state is materialised as ``LEGACY_ORPHAN`` with a wake source, so
        the invariant check reports an explicit wait instead of inventing work
        for a record it cannot classify.
        """
        if run.state not in RUN_STATES:
            run.state = LEGACY_ORPHAN
            run.next_owner = run.next_owner or "operator"
            run.wait_reason = run.wait_reason or "legacy run record without a known state"
            run.wake_source = run.wake_source or "operator:classify"
        return run


# --- evidence and violations -------------------------------------------------


def lease_valid(
    run: Run, *, now: float, alive: Callable[[int | None], bool] = pid_alive
) -> bool:
    """An unexpired lease whose owner process is still alive."""
    if not run.lease_owner or float(run.lease_expires_at) <= now:
        return False
    pid = owner_pid(run.lease_owner)
    if pid is None:
        return True  # named owner without an encoded pid — take its word
    return bool(alive(pid))


def run_evidence(
    store: RunStore,
    run: Run,
    *,
    now: float,
    alive: Callable[[int | None], bool] = pid_alive,
) -> list[str]:
    """Which of the three invariant disjuncts hold for this run."""
    evidence: list[str] = []
    live_lease = lease_valid(run, now=now, alive=alive)
    if live_lease:
        evidence.append(ACTIVE_WORKER_LEASE)
    open_row = store.open_continuation(run.run_id)
    if open_row is not None and int(open_row["generation"]) == int(run.continuation_generation):
        # A queued row is a promise somebody can pick up; a claimed row needs the
        # claimant's lease to still be valid, otherwise it is an orphan.
        if open_row["status"] == CONTINUATION_QUEUED or live_lease:
            evidence.append(QUEUED_CONTINUATION)
    if run.wait_reason and run.wake_source:
        evidence.append(EXPLICIT_WAIT_WITH_WAKE_SOURCE)
    return sorted(set(evidence))


def run_violations(
    store: RunStore,
    run: Run,
    *,
    now: float,
    alive: Callable[[int | None], bool] = pid_alive,
    heartbeat_stale: float = DEFAULT_HEARTBEAT_STALE,
) -> tuple[list[dict], list[dict]]:
    """(violations, warnings) for one run.

    A *violation* breaks the invariant: the lease is gone (expired) or its owner
    is gone (dead process). A *warning* is a signal that a still-valid lease is
    not progressing (stale heartbeat) — worth reporting to the watchdog, but not
    grounds for recovery, because ownership is intact.
    """
    violations: list[dict] = []
    warnings: list[dict] = []
    if run.terminal:
        return violations, warnings
    if run.lease_owner:
        pid = owner_pid(run.lease_owner)
        if float(run.lease_expires_at) <= now:
            violations.append(
                {
                    "kind": EXPIRED_LEASE,
                    "detail": f"lease of {run.lease_owner} expired "
                    f"{int(now - float(run.lease_expires_at))}s ago",
                }
            )
        elif pid is not None and not alive(pid):
            violations.append(
                {"kind": ORPHANED_LEASE, "detail": f"lease holder {run.lease_owner} is dead"}
            )
        elif run.heartbeat_at and (now - float(run.heartbeat_at)) > float(heartbeat_stale):
            warnings.append(
                {
                    "kind": STALE_HEARTBEAT,
                    "detail": f"last heartbeat {int(now - float(run.heartbeat_at))}s ago",
                }
            )
    open_row = store.open_continuation(run.run_id)
    if open_row is not None and open_row["status"] == CONTINUATION_CLAIMED:
        claimer = str(open_row.get("claimed_by") or "")
        pid = owner_pid(claimer)
        if pid is not None and not alive(pid):
            violations.append(
                {
                    "kind": ORPHANED_CONTINUATION,
                    "detail": f"generation {open_row['generation']} claimed by dead {claimer}",
                }
            )
    return violations, warnings


def check_run(
    store: RunStore,
    run_id: str,
    *,
    now: float | None = None,
    alive: Callable[[int | None], bool] = pid_alive,
    heartbeat_stale: float = DEFAULT_HEARTBEAT_STALE,
) -> dict:
    """Invariant verdict for one run."""
    moment = time.time() if now is None else float(now)
    run = store.get(run_id)
    if run is None:
        return {
            "run_id": run_id,
            "found": False,
            "state": "",
            "non_terminal": False,
            "protected": False,
            "evidence": [],
            "violations": [],
            "warnings": [],
            "missing": list(DISJUNCTS),
            "ok": False,
        }
    evidence = run_evidence(store, run, now=moment, alive=alive)
    violations, warnings = run_violations(
        store, run, now=moment, alive=alive, heartbeat_stale=heartbeat_stale
    )
    if not evidence:
        violations = [
            *violations,
            {
                "kind": NO_QUEUE_NO_WAIT,
                "detail": "no active worker lease, no queued continuation, no explicit wait",
            },
        ]
    return {
        "run_id": run.run_id,
        "found": True,
        "session_id": run.session_id,
        "state": run.state,
        "non_terminal": run.non_terminal,
        "protected": run.protected,
        "evidence": evidence,
        "violations": violations,
        "warnings": warnings,
        "missing": [] if evidence else list(DISJUNCTS),
        "ok": bool(evidence) or run.terminal,
    }


def check_invariants(
    store: RunStore,
    *,
    now: float | None = None,
    alive: Callable[[int | None], bool] = pid_alive,
    heartbeat_stale: float = DEFAULT_HEARTBEAT_STALE,
) -> dict:
    """Whole-store invariant check (non-terminal runs only)."""
    moment = time.time() if now is None else float(now)
    checks = [
        check_run(store, run.run_id, now=moment, alive=alive, heartbeat_stale=heartbeat_stale)
        for run in store.non_terminal()
    ]
    return {
        "invariant": INVARIANT,
        "checked": len(checks),
        "violations": [check for check in checks if not check["ok"]],
        "warnings": [
            {"run_id": check["run_id"], **warning}
            for check in checks
            for warning in check["warnings"]
        ],
        "protected": [check for check in checks if check["protected"]],
        "ok": all(check["ok"] for check in checks),
    }


# --- the side-effect rule ----------------------------------------------------


def side_effect_disposition(
    store: RunStore,
    action: str,
    *,
    path_exists: Callable[[str], bool] = os.path.exists,
) -> str:
    """May the recovery repeat this action?

    * no action recorded at all -> ``IDLE`` (nothing was in flight, safe),
    * action registered ``idempotent`` -> ``REPEAT``,
    * action's result verified to exist (recorded or on disk) -> ``RESOLVED``,
    * anything else -> ``UNKNOWN`` -> ``UNKNOWN_SIDE_EFFECT_STATE``, no retry.
    """
    if not str(action or "").strip():
        return DISPOSITION_IDLE
    info = store.action_info(action)
    if info is None:
        return DISPOSITION_UNKNOWN
    if info.get("verified_at"):
        return DISPOSITION_RESOLVED
    result_path = str(info.get("result_path") or "")
    if result_path and path_exists(result_path):
        return DISPOSITION_RESOLVED
    if info.get("idempotent"):
        return DISPOSITION_REPEAT
    return DISPOSITION_UNKNOWN


# --- check and repair --------------------------------------------------------


def repair_run(
    store: RunStore,
    run_id: str,
    *,
    now: float | None = None,
    alive: Callable[[int | None], bool] = pid_alive,
    path_exists: Callable[[str], bool] = os.path.exists,
    heartbeat_stale: float = DEFAULT_HEARTBEAT_STALE,
    reason: str = "orphaned work: expired or orphaned lease without queue and wait",
) -> dict:
    """Move one violating run into a defined state — or deliberately not.

    ``action`` is one of ``TERMINAL_UNTOUCHED``, ``POLICY_OWNED_UNTOUCHED``,
    ``HEALTHY``, ``WAITING_ON_WAKE_SOURCE``, ``REQUEUED``,
    ``PARKED_UNKNOWN_SIDE_EFFECT``, ``SUPERSEDED_BY_OTHER``, ``UNKNOWN_RUN``.
    """
    moment = time.time() if now is None else float(now)
    # The read-decide-write sequence must be atomic per run: without this lock a
    # second supervisor observes the same stale record and repairs it again.
    with store.run_lock(run_id):
        return _repair_run_locked(
            store,
            run_id,
            moment=moment,
            alive=alive,
            path_exists=path_exists,
            heartbeat_stale=heartbeat_stale,
            reason=reason,
        )


def _repair_run_locked(
    store: RunStore,
    run_id: str,
    *,
    moment: float,
    alive: Callable[[int | None], bool],
    path_exists: Callable[[str], bool],
    heartbeat_stale: float,
    reason: str,
) -> dict:
    check = check_run(store, run_id, now=moment, alive=alive, heartbeat_stale=heartbeat_stale)
    decision: dict = {
        "run_id": run_id,
        "action": UNKNOWN_RUN,
        "state_before": check["state"],
        "state_after": check["state"],
        "generation": 0,
        "continuation": 0,
        "reason": "",
    }
    if not check["found"]:
        decision["reason"] = "no such run"
        return decision
    run = store.get(run_id)
    if run is None:  # pragma: no cover — check_run just proved it exists
        decision["reason"] = "no such run"
        return decision
    if run.terminal:
        decision["action"] = TERMINAL_UNTOUCHED
        decision["reason"] = f"terminal state {run.state} is never rewritten"
        return decision
    if run.protected:
        decision["action"] = POLICY_OWNED_UNTOUCHED
        decision["reason"] = (
            f"{run.state} belongs to its owner (wake source {run.wake_source or 'missing'})"
        )
        return decision
    if check["ok"]:
        waiting_only = (
            EXPLICIT_WAIT_WITH_WAKE_SOURCE in check["evidence"]
            and ACTIVE_WORKER_LEASE not in check["evidence"]
            and QUEUED_CONTINUATION not in check["evidence"]
        )
        decision["action"] = WAITING_ON_WAKE_SOURCE if waiting_only else HEALTHY
        decision["reason"] = "invariant holds: " + ", ".join(check["evidence"])
        return decision

    disposition = side_effect_disposition(store, run.pending_action, path_exists=path_exists)
    if disposition == DISPOSITION_UNKNOWN:
        # No automatic retry: the in-flight action may already have taken effect.
        # Supersede any open continuation first so the parked run cannot be
        # resumed behind the operator's back.
        store.supersede_open_continuations(
            run_id, reason="parked: unknown side effect", now=moment
        )
        parked = store.update(
            run_id,
            state=UNKNOWN_SIDE_EFFECT_STATE,
            next_owner="operator",
            wait_reason=(
                f"action {run.pending_action!r} may have side effects and is not proven "
                "idempotent; result existence unverified"
            ),
            wake_source=OPERATOR_WAKE_SOURCE,
            lease_owner="",
            lease_expires_at=0.0,
            recovery_count=int(run.recovery_count) + 1,
            expect={
                "state": run.state,
                "continuation_generation": int(run.continuation_generation),
            },
            now=moment,
        )
        if parked:
            store.audit_append(
                run_id,
                "parked_unknown_side_effect",
                from_state=run.state,
                to_state=UNKNOWN_SIDE_EFFECT_STATE,
                detail=str(run.pending_action),
                now=moment,
            )
        decision.update(
            action=PARKED_UNKNOWN_SIDE_EFFECT if parked else SUPERSEDED_BY_OTHER,
            state_after=(
                UNKNOWN_SIDE_EFFECT_STATE if parked else (store.get(run_id) or run).state
            ),
            reason=(
                f"side effect {run.pending_action!r} unknown -> parked for the operator, "
                "no automatic retry"
                if parked
                else "another recovery moved the run first (compare-and-swap refused)"
            ),
        )
        return decision

    open_row = store.open_continuation(run_id)
    generation = int(run.continuation_generation)
    if open_row is not None and int(open_row["generation"]) == generation:
        # The previous owner died between claim and commit. Re-queue the SAME
        # generation: a new one here would be a second logical continuation —
        # exactly the double execution this crash window must not produce.
        if not store.requeue_continuation(
            run_id,
            generation,
            idempotent=disposition == DISPOSITION_REPEAT,
            result_verified=disposition == DISPOSITION_RESOLVED,
            reason=reason,
            now=moment,
        ):
            decision.update(
                action=SUPERSEDED_BY_OTHER,
                state_after=(store.get(run_id) or run).state,
                reason="another worker committed the continuation first",
            )
            return decision
        continuation_id, created = int(open_row["id"]), False
    else:
        if open_row is not None:
            # Only generations OLDER than ours are dead weight. A concurrent
            # recovery may have just created the next generation — sweeping that
            # away would lose the resumption this pass exists to guarantee.
            store.supersede_open_continuations(
                run_id, below=generation, reason="stale generation", now=moment
            )
        generation = generation + 1
        continuation_id, created = store.enqueue_continuation(
            run_id,
            generation=generation,
            reason=reason,
            idempotent=disposition == DISPOSITION_REPEAT,
            result_verified=disposition == DISPOSITION_RESOLVED,
            session_id=run.session_id,
            now=moment,
        )
    updated = store.update(
        run_id,
        state=QUEUED,
        next_owner="worker",
        pending_action="" if disposition == DISPOSITION_RESOLVED else run.pending_action,
        continuation_generation=generation,
        lease_owner="",
        lease_expires_at=0.0,
        wait_reason="",
        wake_source="",
        recovery_count=int(run.recovery_count) + 1,
        expect={"continuation_generation": int(run.continuation_generation)},
        now=moment,
    )
    if not updated:
        decision.update(
            action=SUPERSEDED_BY_OTHER,
            state_after=(store.get(run_id) or run).state,
            reason="another recovery enqueued first (compare-and-swap refused)",
        )
        return decision
    store.audit_append(
        run_id,
        "orphan_requeued",
        from_state=run.state,
        to_state=QUEUED,
        detail=f"generation={generation} disposition={disposition} new_row={created}",
        now=moment,
    )
    decision.update(
        action=REQUEUED,
        state_after=QUEUED,
        generation=generation,
        continuation=continuation_id,
        reason=f"{reason} (disposition {disposition})",
    )
    return decision



def recover_pending(
    store: RunStore,
    *,
    owner: str,
    ttl: float = DEFAULT_LEASE_TTL,
    limit: int = 10,
    now: float | None = None,
    alive: Callable[[int | None], bool] = pid_alive,
) -> list[dict]:
    """Claim at most ``limit`` queued continuations — exactly once each.

    This is the worker side of the queue: whatever this returns is work this
    process now owns. Everything else stays queued for somebody else.
    """
    moment = time.time() if now is None else float(now)
    claimed: list[dict] = []
    for run in store.non_terminal():
        if len(claimed) >= int(limit):
            break
        if run.protected:
            continue
        row = store.open_continuation(run.run_id)
        if row is None or row["status"] != CONTINUATION_QUEUED:
            continue
        if int(row["generation"]) != int(run.continuation_generation):
            continue
        # Serialise claim against a concurrent repair of the same run: otherwise a
        # second supervisor can re-queue between this check and the claim.
        with store.run_lock(run.run_id):
            claimed_row = store.claim_continuation(run.run_id, owner=owner, ttl=ttl, now=moment)
        if claimed_row is not None:
            claimed.append(claimed_row)
    return claimed


def sweep(
    store: RunStore,
    *,
    owner: str = "run-supervisor",
    ttl: float = DEFAULT_LEASE_TTL,
    claim: bool = True,
    limit: int = 10,
    now: float | None = None,
    alive: Callable[[int | None], bool] = pid_alive,
    path_exists: Callable[[str], bool] = os.path.exists,
    heartbeat_stale: float = DEFAULT_HEARTBEAT_STALE,
) -> dict:
    """One watchdog pass: check -> repair -> claim. Returns a receipt-shaped dict."""
    moment = time.time() if now is None else float(now)
    before = check_invariants(store, now=moment, alive=alive, heartbeat_stale=heartbeat_stale)
    repairs = [
        repair_run(
            store,
            check["run_id"],
            now=moment,
            alive=alive,
            path_exists=path_exists,
            heartbeat_stale=heartbeat_stale,
        )
        for check in before["violations"]
    ]
    claimed = (
        recover_pending(store, owner=owner, ttl=ttl, limit=limit, now=moment, alive=alive)
        if claim
        else []
    )
    after = check_invariants(store, now=moment, alive=alive, heartbeat_stale=heartbeat_stale)
    return {
        "ts": moment,
        "owner": owner,
        "invariant": INVARIANT,
        "checked": before["checked"],
        "violations_before": len(before["violations"]),
        "violations_after": len(after["violations"]),
        "protected": [
            {"run_id": check["run_id"], "state": check["state"]}
            for check in after["protected"]
        ],
        "repairs": repairs,
        "claimed": claimed,
        "ok": after["ok"],
    }


# --- host turn leases (the live orphan case) ---------------------------------

HOST_TURN_ACTION_PREFIX = "host-turn:"


def host_leases(*, db_path: str | Path | None = None, now: float | None = None) -> list[dict]:
    """Read ``session_turn_leases`` from the host ``state.db`` (read-only, tolerant)."""
    path = Path(db_path or (hermes_home() / HOST_LEASES_DB))
    moment = time.time() if now is None else float(now)
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return []
    try:
        rows = con.execute(
            "SELECT conversation_id, holder, acquired_at, expires_at FROM session_turn_leases"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    leases: list[dict] = []
    for conversation_id, holder, acquired_at, expires_at in rows:
        holder_text = str(holder or "")
        leases.append(
            {
                "conversation_id": str(conversation_id),
                "holder": holder_text,
                "pid": owner_pid(holder_text),
                "acquired_at": float(acquired_at or 0.0),
                "expires_at": float(expires_at or 0.0),
                "expired": float(expires_at or 0.0) <= moment,
            }
        )
    return leases


def adopt_host_leases(
    store: RunStore,
    *,
    db_path: str | Path | None = None,
    now: float | None = None,
    alive: Callable[[int | None], bool] = pid_alive,
) -> list[Run]:
    """Turn *orphaned* host turn leases into run records (idempotent).

    Only leases whose holder is dead or whose expiry has passed are adopted: a
    healthy host turn belongs to the host and must not be doubled here. The
    adopted record names the unknown work as ``host-turn:<session>`` — an action
    GV cannot prove idempotent — so the supervisor parks it in
    ``UNKNOWN_SIDE_EFFECT_STATE`` instead of silently re-running a turn.
    """
    moment = time.time() if now is None else float(now)
    adopted: list[Run] = []
    for lease in host_leases(db_path=db_path, now=moment):
        pid = lease["pid"]
        orphan = bool(lease["expired"]) or (pid is not None and not alive(pid))
        if not orphan:
            continue
        session_id = str(lease["conversation_id"])
        if store.by_session(session_id) is not None:
            continue
        adopted.append(
            store.create_run(
                session_id,
                run_id=f"host-{session_id}",
                state=ACTIVE,
                next_owner="worker",
                pending_action=f"{HOST_TURN_ACTION_PREFIX}{session_id}",
                lease_owner=str(lease["holder"]),
                lease_expires_at=float(lease["expires_at"]),
                heartbeat_at=float(lease["acquired_at"]),
                last_progress_at=float(lease["acquired_at"]),
                now=moment,
            )
        )
        store.audit_append(
            adopted[-1].run_id,
            "host_lease_adopted",
            from_state="",
            to_state=ACTIVE,
            detail=f"holder={lease['holder']} expired={lease['expired']}",
            now=moment,
        )
    return adopted


# --- watchdog ----------------------------------------------------------------


class RunWatchdog:
    """Threaded watchdog: every pass checks the invariant and repairs orphans.

    Modelled on ``pause.VoicePause._ensure_watchdog`` — its own thread, bounded
    interval, exceptions never kill the loop (they are counted and reported).
    ``emit`` is optional so the module carries no hard dependency on the event
    store; wiring passes its own emitter (see docs/RUN-LIFECYCLE.md, "open").
    """

    def __init__(
        self,
        store: RunStore,
        *,
        interval: float = WATCHDOG_INTERVAL,
        owner: str = "run-watchdog",
        ttl: float = DEFAULT_LEASE_TTL,
        claim: bool = True,
        emit: Callable[[str, dict], None] | None = None,
        sweep_fn: Callable[..., dict] = sweep,
        clock: Callable[[], float] = time.time,
        heartbeat_stale: float = DEFAULT_HEARTBEAT_STALE,
    ) -> None:
        self.store = store
        self.interval = float(interval)
        self.owner = str(owner)
        self.ttl = float(ttl)
        self.claim = bool(claim)
        self.emit = emit
        self.sweep_fn = sweep_fn
        self.clock = clock
        self.heartbeat_stale = float(heartbeat_stale)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.passes = 0
        self.errors: list[str] = []
        self.last: dict = {}

    def pass_once(self) -> dict:
        """One sweep — never raises: failures are recorded, not swallowed."""
        try:
            result = self.sweep_fn(
                self.store,
                owner=self.owner,
                ttl=self.ttl,
                claim=self.claim,
                now=self.clock(),
                heartbeat_stale=self.heartbeat_stale,
            )
        except Exception as exc:  # noqa: BLE001 — the watchdog must keep ticking
            self.errors.append(f"{type(exc).__name__}: {exc}")
            return {"error": self.errors[-1], "ok": False}
        self.passes += 1
        self.last = result
        if self.emit is not None and (result.get("violations_before") or result.get("claimed")):
            self.emit(
                "run_sweep",
                {
                    key: result[key]
                    for key in ("ts", "checked", "violations_before", "violations_after")
                },
            )
        return result

    def start(self) -> RunWatchdog:
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()

        def loop() -> None:
            while not self._stop.wait(self.interval):
                self.pass_once()

        self._thread = threading.Thread(target=loop, name="godsv-run-watchdog", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# --- convenience -------------------------------------------------------------


def open_store(state_dir: str | Path | None = None) -> RunStore:
    """Open (creating if needed) the run store in the runtime dir."""
    path = run_store_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    return RunStore(path)


def bootstrap(
    store: RunStore | None = None,
    *,
    state_dir: str | Path | None = None,
    now: float | None = None,
    alive: Callable[[int | None], bool] = pid_alive,
) -> RunStore:
    """Open the store and adopt any orphaned host turn lease that has no run yet."""
    store = store or open_store(state_dir)
    adopt_host_leases(store, now=now, alive=alive)
    return store


def describe(store: RunStore, *, now: float | None = None) -> dict:
    """Read-only status snapshot for the panel/CLI (no writes, no repairs)."""
    moment = time.time() if now is None else float(now)
    checks = check_invariants(store, now=moment)
    return {
        "invariant": INVARIANT,
        "runs": [run.to_dict() for run in store.all_runs()],
        "checked": checks["checked"],
        "violations": checks["violations"],
        "protected": [check["run_id"] for check in checks["protected"]],
        "ok": checks["ok"],
    }


def dumps(payload: object) -> str:
    """Stable JSON for receipts (non-serialisable values become strings)."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
