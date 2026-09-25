"""Durable goal record, operation ledger and the resume protocol (MVP/10).

This is the PERSIST layer's store: ``<runtime>/runs.db`` — the file the run
supervisor already declares (`run_supervisor.run_store_path`) and that nothing
had ever written. Where MVP/10 §2.1 splits the artifacts across
``goals/<id>.json`` + ``budget.db`` + ``runs.db``, this module keeps **one
file**, because the single-writer rule and the single transaction that must
span "ledger row + state row" are only enforceable cheaply when they share one
connection:

* ``goals`` / ``criteria`` / ``criterion_evidence`` — the durable goal record:
  ``goal_id`` (immutable identity), ``revision``, state, criteria with **typed
  evidence refs**, decisions (which never bump the revision), the owner lease.
* ``operations`` — one row per attempt: idempotency key (**UNIQUE** — the
  double-effect fence), state, side-effect disposition, lease, ``result_ref``.
  It lives next to ``runs``/``continuations``/``run_actions``, which this module
  creates through :mod:`skaldir.run_supervisor` so the existing run store and the
  operation queue are the *same* file.
* ``checkpoints`` (+ ``<runtime>/checkpoints/<goal_id>/<seq>.json``) — an index
  row plus a self-sufficient payload file, chained by sha256.
* ``budget_ledger`` / ``budget_state`` / ``budget_reservations`` — the budget
  ledger that **loads on start**. ``budget.BudgetAccount`` kept spent/reserved in
  instance fields and had no load path (MVP/10 gap 5): after a restart the
  account started at zero, i.e. an unbounded budget. :meth:`BudgetLedger.restore_into`
  replays the durable ledger through the real account, so ``spent``/``reserved``
  after a restart equal the values before it (proved by a real SIGKILL test).
* ``resume_cursor`` — where the goal stood: last committed event seq, last
  checkpoint, open criteria, open operations, next action.

Write mechanics (reused, not invented — MVP/10 §2.4): read-modify-write under a
re-entrant in-process lock, one ``BEGIN IMMEDIATE`` transaction per change, WAL +
``synchronous=FULL`` so a committed row survives ``kill -9``, an atomic
temp+``fsync``+``os.replace`` write for every file this module creates, and an
additive ``ALTER TABLE`` migration plus a ``schema_version`` fence in
``runs_store_meta``.

Single writer: :meth:`RunsStore.acquire_writer` holds a compare-and-swap lease on
the store (``pid=<pid>:boot=<boot_id>``) so a second process cannot become a
second writer while the holder is alive. Per goal, :meth:`RunsStore.resume`
re-attaches to a live owner (heartbeat only, never a second worker) or takes over
through a CAS when the lease is expired or the holder pid is dead.

Resume protocol (R0-R3 implemented here; R4 is the idempotency contract):

* R1 detect — non-terminal goals, operations ``pending``/``in_flight``/``unknown``,
  run-supervisor invariant violations, orphaned host turn leases, uncommitted
  events (cursor < ``MAX(events.seq)``), stale criterion evidence.
* R2 classify — ``IDLE`` (nothing was in flight), ``REPEAT`` (registered
  idempotent), ``RESOLVED`` (``result_ref`` exists), ``UNKNOWN`` (**no retry** —
  parked with ``operator:side_effect_verified``).
* R3 ownership — re-attach or CAS takeover; a parked ``unknown`` operation
  supersedes any open continuation of its run first, so a claim left behind by a
  dead owner cannot execute behind the operator's back.

Nothing here may mark a criterion ``verified`` without an evidence ref: the
status is derived from evidence, never set by a parameter
(``record_criterion_outcome`` refuses an empty ref list, a stale
``evidence_revision`` and a foreign workspace fingerprint — MVP/10 R6).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import budget as budget_module
from . import run_supervisor as rs
from .registry import default_runtime_dir

# --- goal states: the existing completion vocabulary, one source of truth ----- #

try:  # the design names completion.STATES (completion.py:133-145)
    from .completion import STATES as GOAL_STATES  # type: ignore[attr-defined]
    from .completion import TERMINAL_STATES as TERMINAL_GOAL_STATES
except Exception:  # noqa: BLE001 — keep the module importable standalone
    GOAL_STATES = (
        "WORKING",
        "COMPLETION_REQUESTED",
        "VERIFYING",
        "INCOMPLETE",
        "REDIRECT",
        "VERIFIED_COMPLETE",
        "BLOCKED_USER",
        "BLOCKED_EXTERNAL",
        "FAILED_UNRECOVERABLE",
        "USER_CANCELLED",
        "SHIPPED_WITH_USER_OVERRIDE",
    )
    TERMINAL_GOAL_STATES = (
        "VERIFIED_COMPLETE",
        "BLOCKED_USER",
        "BLOCKED_EXTERNAL",
        "FAILED_UNRECOVERABLE",
        "USER_CANCELLED",
        "SHIPPED_WITH_USER_OVERRIDE",
    )

WORKING = "WORKING"

# --- criterion status -------------------------------------------------------- #

STATUS_OPEN = "open"
STATUS_VERIFIED = "verified"
STATUS_FAILED = "failed"
CRITERION_STATUSES = (STATUS_OPEN, STATUS_VERIFIED, STATUS_FAILED)

# --- operation state / side-effect disposition -------------------------------- #

OP_PENDING = "pending"
OP_IN_FLIGHT = "in_flight"
OP_DONE = "done"
OP_FAILED = "failed"
OP_UNKNOWN = "unknown"  # parked: may have taken effect and cannot be proven

OP_STATES = (OP_PENDING, OP_IN_FLIGHT, OP_DONE, OP_FAILED, OP_UNKNOWN)
OPEN_OP_STATES = (OP_PENDING, OP_IN_FLIGHT, OP_UNKNOWN)

SIDE_EFFECT_NONE = "none"
SIDE_EFFECT_KNOWN = "known"
SIDE_EFFECT_UNKNOWN = "unknown"
SIDE_EFFECT_STATES = (SIDE_EFFECT_NONE, SIDE_EFFECT_KNOWN, SIDE_EFFECT_UNKNOWN)

DISPOSITION_IDLE = rs.DISPOSITION_IDLE
DISPOSITION_REPEAT = rs.DISPOSITION_REPEAT
DISPOSITION_RESOLVED = rs.DISPOSITION_RESOLVED
DISPOSITION_UNKNOWN = rs.DISPOSITION_UNKNOWN
DISPOSITIONS = (DISPOSITION_IDLE, DISPOSITION_REPEAT, DISPOSITION_RESOLVED, DISPOSITION_UNKNOWN)

OPERATOR_WAKE_SOURCE = rs.OPERATOR_WAKE_SOURCE  # "operator:side_effect_verified"

# --- ownership ---------------------------------------------------------------- #

DEFAULT_LEASE_TTL = rs.DEFAULT_LEASE_TTL  # 120 s
SCHEMA_VERSION = 1

_EVIDENCE_PREFIX_KINDS = (
    ("events:", "events"),
    ("run:", "run"),
    ("receipt:", "receipt"),
    ("test:", "test"),
    ("file:", "file"),
    ("cmd:", "cmd"),
    ("op:", "operation"),
)

_META_WRITER_KEY = "writer"
#: The meta key that says whether this store accepts operator messages at all. Absent/true =
#: accepted (the default); `false` is what makes `queue-disabled` an honest refusal instead of a
#: dropped message.
_META_MESSAGES_KEY = "messages_enabled"
_TX_DEPTH = threading.local()


class RunsError(RuntimeError):
    """Base class for this module's refusals."""


class WriterConflict(RunsError):
    """Another live process owns the store's single-writer lease."""


class IdempotencyConflict(RunsError):
    """An operation with this idempotency key already exists."""


class StaleEvidence(RunsError):
    """Evidence does not belong to this revision / workspace — never certify it."""


class EvidenceRequired(RunsError):
    """A criterion outcome without an evidence ref is a claim, not proof."""


class UnknownGoal(RunsError):
    """No goal record matches the selector."""


class CorruptStore(RunsError):
    """``runs.db`` is not a readable sqlite database — never silently recreated."""


# --- operator messages: /steer and /queue -------------------------------------- #
#
# The chat is the surface of a running goal, so "type here what the run should do" has to be a
# *durable* act, not a keystroke into a buffer: the message is written to this store before the
# chat answers, drained at a turn boundary by the loop that really runs the goal, and marked with
# the turn that consumed it. The alternative (an in-memory hand-off) dies with the chat process
# and cannot be inspected afterwards — and "no silent drops" is a product rule.

MESSAGE_STEER = "steer"
MESSAGE_QUEUE = "queue"
MESSAGE_KINDS = (MESSAGE_STEER, MESSAGE_QUEUE)

MESSAGE_PENDING = "pending"
MESSAGE_CONSUMED = "consumed"
MESSAGE_REFUSED = "refused"
MESSAGE_STATES = (MESSAGE_PENDING, MESSAGE_CONSUMED, MESSAGE_REFUSED)

#: How many messages of one run may wait at once. A bound, not a policy: past it the store
#: refuses by name (`queue-full`) instead of growing without limit behind a run that is stuck.
MESSAGE_CAP = 64

REFUSAL_NO_RUN = "no-run"
REFUSAL_RUN_FINISHED = "run-finished"
REFUSAL_EMPTY = "empty-text"
REFUSAL_QUEUE_FULL = "queue-full"
REFUSAL_QUEUE_DISABLED = "queue-disabled"
REFUSAL_UNKNOWN_KIND = "unknown-kind"


class MessageRefused(RunsError):
    """An operator message this store will not take — with the reason it says no.

    The reason is a *name* (``no-run``, ``run-finished``, ``empty-text``, ``queue-full``,
    ``queue-disabled``, ``unknown-kind``) so a caller can answer honestly instead of guessing,
    and so the refusal is greppable in a log.
    """

    def __init__(self, reason: str, message: str = "", *, details: dict | None = None) -> None:
        super().__init__(message or reason)
        self.reason = str(reason)
        self.details = dict(details or {})

    @property
    def code(self) -> str:
        return self.reason


@dataclass
class Message:
    """One operator message: the text, who sent it, and when a turn consumed it."""

    message_id: int = 0
    goal_id: str = ""
    kind: str = MESSAGE_STEER
    origin: str = ""
    text: str = ""
    created: float = 0.0
    state: str = MESSAGE_PENDING
    consumed_at: float = 0.0
    consumed_turn: int = -1
    turn_name: str = ""
    refused_reason: str = ""

    @property
    def consumed(self) -> bool:
        return self.state == MESSAGE_CONSUMED

    @property
    def urgent(self) -> bool:
        return self.kind == MESSAGE_STEER

    def to_dict(self) -> dict:
        data = dict(self.__dict__)
        data["consumed"] = self.consumed
        return data


def _message_from_row(row: sqlite3.Row) -> Message:
    return Message(
        message_id=int(row["id"]),
        goal_id=str(row["goal_id"]),
        kind=str(row["kind"]),
        origin=str(row["origin"]),
        text=str(row["text"]),
        created=float(row["created"]),
        state=str(row["state"]),
        consumed_at=float(row["consumed_at"]),
        consumed_turn=int(row["consumed_turn"]),
        turn_name=str(row["turn_name"]),
        refused_reason=str(row["refused_reason"]),
    )


# --------------------------------------------------------------------------- #
# small helpers                                                                #
# --------------------------------------------------------------------------- #


def boot_id() -> str:
    """The kernel boot id, so a recycled PID cannot be mistaken for the same owner."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:  # pragma: no cover — non-Linux
        return "boot-unknown"


def owner_string(pid: int | None = None, boot: str | None = None) -> str:
    """``pid=<pid>:boot=<boot_id>`` — liveness is derivable from the string."""
    return f"pid={int(os.getpid() if pid is None else pid)}:boot={boot or boot_id()}"


def pid_alive(pid: int | None) -> bool:
    return rs.pid_alive(pid)


def owner_pid(owner: str) -> int | None:
    return rs.owner_pid(owner)


def payload_hash(payload: object) -> str:
    """Stable hash of an operation payload (``sha256`` of canonical JSON)."""
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def idempotency_key(
    goal_id: str, revision: int, kind: str, target: str = "", payload: object = ""
) -> str:
    """``sha256(goal_id|revision|op_kind|target|payload_hash)`` (MVP/10 R4.1)."""
    digest = payload if isinstance(payload, str) and _is_hex64(payload) else payload_hash(payload)
    material = f"{goal_id}|{int(revision)}|{kind}|{target}|{digest}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _is_hex64(text: str) -> bool:
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text.lower())


def evidence_kind(ref: str) -> str:
    """Typed evidence ref: the prefix names the store the row lives in."""
    text = str(ref)
    for prefix, kind in _EVIDENCE_PREFIX_KINDS:
        if text.startswith(prefix):
            return kind
    return "other"


def runtime_dir_for(store_path: str | os.PathLike) -> Path:
    """The runtime dir a store file lives in (checkpoints/receipts sit next to it)."""
    return Path(store_path).expanduser().parent


def run_store_path(runtime: str | os.PathLike | None = None) -> Path:
    """``<runtime>/runs.db`` — the same path the run supervisor declares."""
    if runtime is None:
        return rs.run_store_path()
    return rs.run_store_path(str(Path(runtime).expanduser()))


def _now(value: float | None) -> float:
    return time.time() if value is None else float(value)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _loads(text: str, default: object) -> object:
    try:
        loaded = json.loads(text or "")
    except (json.JSONDecodeError, TypeError):
        return default
    return loaded


def _load_list(text: object) -> list:
    """JSON text → list; anything else (absent, torn, wrong shape) → ``[]``."""
    loaded = _loads(str(text or ""), [])
    return list(loaded) if isinstance(loaded, (list, tuple)) else []


def _load_dict(text: object) -> dict:
    loaded = _loads(str(text or ""), {})
    return dict(loaded) if isinstance(loaded, Mapping) else {}


def atomic_write_text(path: str | os.PathLike, text: str) -> None:
    """Temp file in the same dir + ``fsync`` + ``os.replace`` (completion.py:93-119)."""
    try:  # reuse the proven implementation rather than writing a second one
        from .completion import _atomic_write_text as _impl

        return _impl(str(path), text)
    except Exception:  # noqa: BLE001 — standalone/older build: same discipline, inline
        pass
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def sha256_file(path: str | os.PathLike) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# records                                                                       #
# --------------------------------------------------------------------------- #


@dataclass
class Goal:
    """The durable goal record (MVP/10 §2.2), one row in ``goals``."""

    goal_id: str
    revision: int = 1
    session_id: str = ""
    state: str = WORKING
    goal_text: str = ""
    task_base: str = ""
    workspace_fingerprint: str = ""
    created: float = 0.0
    updated: float = 0.0
    revision_log: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    last_failure: str = ""
    lease_owner: str = ""
    lease_expires_at: float = 0.0
    heartbeat_at: float = 0.0
    blocked_reason: str = ""
    wake_source: str = ""
    schema_version: int = SCHEMA_VERSION
    history_ref: str = ""
    corrupt: bool = False
    corrupt_reason: str = ""

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_GOAL_STATES

    def to_dict(self) -> dict:
        data = dict(self.__dict__)
        data["terminal"] = self.terminal
        return data


@dataclass
class Criterion:
    """One acceptance criterion with its typed evidence refs."""

    criterion_id: str
    text: str = ""
    blocking: bool = True
    verification_plan: str = ""
    status: str = STATUS_OPEN
    evidence_revision: int = 0
    evidence_workspace_fingerprint: str = ""
    verified_at: float = 0.0
    verified_by: str = ""
    last_failure: str = ""
    evidence_refs: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    def fresh(self, goal: Goal) -> bool:
        """Is the evidence still about THIS revision and workspace (MVP/10 R6)?"""
        if self.status != STATUS_VERIFIED:
            return True
        if int(self.evidence_revision) != int(goal.revision):
            return False
        current = str(goal.workspace_fingerprint or "")
        if current and str(self.evidence_workspace_fingerprint or "") != current:
            return False
        return bool(self.evidence_refs)


@dataclass
class Operation:
    """One operation attempt: who owns it, whether repeating it is safe."""

    op_id: str
    goal_id: str
    idempotency_key: str = ""
    kind: str = ""
    target: str = ""
    payload_hash: str = ""
    state: str = OP_PENDING
    side_effect_state: str = SIDE_EFFECT_NONE
    disposition: str = ""
    attempt: int = 0
    idempotent: bool = False
    lease_owner: str = ""
    lease_expires_at: float = 0.0
    heartbeat_at: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    result_ref: str = ""
    last_error: str = ""
    wake_source: str = ""
    revision: int = 0
    run_id: str = ""
    created: float = 0.0
    duplicate: bool = False

    @property
    def parked(self) -> bool:
        return self.state == OP_UNKNOWN

    def to_dict(self) -> dict:
        data = dict(self.__dict__)
        data["parked"] = self.parked
        return data


# --------------------------------------------------------------------------- #
# the store                                                                     #
# --------------------------------------------------------------------------- #

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS runs_store_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS goals (
        goal_id               TEXT PRIMARY KEY,
        schema_version        INTEGER NOT NULL DEFAULT 1,
        revision              INTEGER NOT NULL DEFAULT 1,
        session_id            TEXT NOT NULL DEFAULT '',
        state                 TEXT NOT NULL DEFAULT 'WORKING',
        goal_text             TEXT NOT NULL DEFAULT '',
        task_base             TEXT NOT NULL DEFAULT '',
        workspace_fingerprint TEXT NOT NULL DEFAULT '',
        created               REAL NOT NULL DEFAULT 0,
        updated               REAL NOT NULL DEFAULT 0,
        revision_log          TEXT NOT NULL DEFAULT '[]',
        decisions             TEXT NOT NULL DEFAULT '[]',
        last_failure          TEXT NOT NULL DEFAULT '',
        lease_owner           TEXT NOT NULL DEFAULT '',
        lease_expires_at      REAL NOT NULL DEFAULT 0,
        heartbeat_at          REAL NOT NULL DEFAULT 0,
        blocked_reason        TEXT NOT NULL DEFAULT '',
        wake_source           TEXT NOT NULL DEFAULT '',
        history_ref           TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS criteria (
        goal_id                        TEXT NOT NULL,
        criterion_id                   TEXT NOT NULL,
        position                       INTEGER NOT NULL DEFAULT 0,
        text                           TEXT NOT NULL DEFAULT '',
        blocking                       INTEGER NOT NULL DEFAULT 1,
        verification_plan              TEXT NOT NULL DEFAULT '',
        status                         TEXT NOT NULL DEFAULT 'open',
        evidence_revision              INTEGER NOT NULL DEFAULT 0,
        evidence_workspace_fingerprint TEXT NOT NULL DEFAULT '',
        verified_at                    REAL NOT NULL DEFAULT 0,
        verified_by                    TEXT NOT NULL DEFAULT '',
        last_failure                   TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (goal_id, criterion_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS criterion_evidence (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        goal_id      TEXT NOT NULL,
        criterion_id TEXT NOT NULL,
        ref          TEXT NOT NULL,
        kind         TEXT NOT NULL DEFAULT '',
        at           REAL NOT NULL DEFAULT 0,
        revision     INTEGER NOT NULL DEFAULT 0,
        workspace    TEXT NOT NULL DEFAULT '',
        check_id     TEXT NOT NULL DEFAULT '',
        cmd          TEXT NOT NULL DEFAULT '',
        exit_code    INTEGER NOT NULL DEFAULT -1,
        outcome      TEXT NOT NULL DEFAULT '',
        artifact     TEXT NOT NULL DEFAULT '',
        run_id       TEXT NOT NULL DEFAULT '',
        UNIQUE(goal_id, criterion_id, ref)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS operations (
        op_id             TEXT PRIMARY KEY,
        goal_id           TEXT NOT NULL,
        idempotency_key   TEXT NOT NULL UNIQUE,
        kind              TEXT NOT NULL DEFAULT '',
        target            TEXT NOT NULL DEFAULT '',
        payload_hash      TEXT NOT NULL DEFAULT '',
        revision          INTEGER NOT NULL DEFAULT 0,
        state             TEXT NOT NULL DEFAULT 'pending',
        side_effect_state TEXT NOT NULL DEFAULT 'none',
        disposition       TEXT NOT NULL DEFAULT '',
        idempotent        INTEGER NOT NULL DEFAULT 0,
        attempt           INTEGER NOT NULL DEFAULT 0,
        lease_owner       TEXT NOT NULL DEFAULT '',
        lease_expires_at  REAL NOT NULL DEFAULT 0,
        heartbeat_at      REAL NOT NULL DEFAULT 0,
        started_at        REAL NOT NULL DEFAULT 0,
        finished_at       REAL NOT NULL DEFAULT 0,
        result_ref        TEXT NOT NULL DEFAULT '',
        last_error        TEXT NOT NULL DEFAULT '',
        wake_source       TEXT NOT NULL DEFAULT '',
        run_id            TEXT NOT NULL DEFAULT '',
        created           REAL NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS checkpoints (
        checkpoint_id  TEXT PRIMARY KEY,
        goal_id        TEXT NOT NULL,
        seq            INTEGER NOT NULL,
        at             REAL NOT NULL DEFAULT 0,
        revision       INTEGER NOT NULL DEFAULT 0,
        last_event_seq INTEGER NOT NULL DEFAULT 0,
        path           TEXT NOT NULL DEFAULT '',
        sha256         TEXT NOT NULL DEFAULT '',
        prev           TEXT NOT NULL DEFAULT '',
        size_bytes     INTEGER NOT NULL DEFAULT 0,
        next_action    TEXT NOT NULL DEFAULT '',
        open_criteria  TEXT NOT NULL DEFAULT '[]',
        open_operations TEXT NOT NULL DEFAULT '[]',
        UNIQUE(goal_id, seq)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS resume_cursor (
        goal_id            TEXT PRIMARY KEY,
        last_event_seq     INTEGER NOT NULL DEFAULT 0,
        last_checkpoint_id TEXT NOT NULL DEFAULT '',
        open_criteria      TEXT NOT NULL DEFAULT '[]',
        open_operations    TEXT NOT NULL DEFAULT '[]',
        next_action        TEXT NOT NULL DEFAULT '',
        updated            REAL NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS budget_ledger (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        goal_id        TEXT NOT NULL,
        ts             REAL NOT NULL,
        role           TEXT NOT NULL,
        action         TEXT NOT NULL,
        amount         INTEGER NOT NULL,
        reservation_id TEXT NOT NULL DEFAULT '',
        note           TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS budget_state (
        goal_id          TEXT NOT NULL,
        role             TEXT NOT NULL,
        limit_tokens     INTEGER NOT NULL,
        spent            INTEGER NOT NULL DEFAULT 0,
        reserved         INTEGER NOT NULL DEFAULT 0,
        unreserved_spent INTEGER NOT NULL DEFAULT 0,
        updated          REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (goal_id, role)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS budget_reservations (
        goal_id        TEXT NOT NULL,
        reservation_id TEXT NOT NULL,
        role           TEXT NOT NULL,
        amount         INTEGER NOT NULL,
        created        REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (goal_id, reservation_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS operations_state ON operations(state)",
    "CREATE INDEX IF NOT EXISTS operations_goal ON operations(goal_id, state)",
    "CREATE INDEX IF NOT EXISTS criteria_goal ON criteria(goal_id, position)",
    "CREATE INDEX IF NOT EXISTS budget_ledger_goal ON budget_ledger(goal_id, id)",
    "CREATE INDEX IF NOT EXISTS checkpoints_goal ON checkpoints(goal_id, seq)",
    """
    CREATE TABLE IF NOT EXISTS run_messages (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        goal_id        TEXT NOT NULL,
        kind           TEXT NOT NULL DEFAULT 'steer',
        origin         TEXT NOT NULL DEFAULT '',
        text           TEXT NOT NULL DEFAULT '',
        created        REAL NOT NULL DEFAULT 0,
        state          TEXT NOT NULL DEFAULT 'pending',
        consumed_at    REAL NOT NULL DEFAULT 0,
        consumed_turn  INTEGER NOT NULL DEFAULT -1,
        turn_name      TEXT NOT NULL DEFAULT '',
        refused_reason TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS run_messages_pending ON run_messages(goal_id, kind, state, id)",
)

#: additive columns other builds/waves may add to a table that already exists
_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "operations": {
        "disposition": "TEXT NOT NULL DEFAULT ''",
        "run_id": "TEXT NOT NULL DEFAULT ''",
        "revision": "INTEGER NOT NULL DEFAULT 0",
        "idempotent": "INTEGER NOT NULL DEFAULT 0",
    },
    "goals": {
        "history_ref": "TEXT NOT NULL DEFAULT ''",
        "blocked_reason": "TEXT NOT NULL DEFAULT ''",
        "wake_source": "TEXT NOT NULL DEFAULT ''",
    },
    "criterion_evidence": {"run_id": "TEXT NOT NULL DEFAULT ''"},
    "checkpoints": {
        "revision": "INTEGER NOT NULL DEFAULT 0",
        "size_bytes": "INTEGER NOT NULL DEFAULT 0",
    },
}


def _field_of(target: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` off a dataclass or a mapping — the guard's shapes, read tolerantly."""
    if target is None:
        return default
    if isinstance(target, Mapping):
        return target.get(name, default)
    return getattr(target, name, default)


def _wire_of(target: Any) -> dict[str, Any]:
    """The mapping view of a signals object (``to_dict``/``to_wire`` or a mapping)."""
    if target is None:
        return {}
    if isinstance(target, Mapping):
        return dict(target)
    for method in ("to_dict", "to_wire"):
        writer = getattr(target, method, None)
        if callable(writer):
            try:
                wire = writer()
            except Exception:  # noqa: BLE001 — a broken writer is not a store failure
                continue
            if isinstance(wire, Mapping):
                return dict(wire)
    return {}


class RunsStore:
    """The single writer of ``runs.db``: goal record, operations, ledger, cursor."""

    def __init__(
        self,
        path: str | os.PathLike,
        *,
        owner: str = "",
        lease_ttl: float = DEFAULT_LEASE_TTL,
        create: bool = True,
    ) -> None:
        self.path = str(Path(path).expanduser())
        self.lease_ttl = float(lease_ttl)
        self.owner = owner or owner_string()
        self._lock = threading.RLock()
        self._supervisor: rs.RunStore | None = None
        if create:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            self.path, check_same_thread=False, timeout=30.0, isolation_level=None
        )
        self._db.row_factory = sqlite3.Row
        self._tx_depth = 0
        try:
            with self._lock:
                self._db.execute("PRAGMA journal_mode=WAL")
                # FULL: a committed row must survive kill -9, not only a clean exit.
                self._db.execute("PRAGMA synchronous=FULL")
                self._db.execute("PRAGMA busy_timeout=30000")
                for statement in _DDL:
                    self._db.execute(statement)
                self.migrate()
        except sqlite3.DatabaseError as exc:
            # a torn/foreign file is reported, never overwritten into a fresh empty
            # store (MVP/10 R1: the same rule the contract gets for torn JSON)
            with contextlib.suppress(Exception):
                self._db.close()
            raise CorruptStore(
                f"{self.path} is not a readable runs.db: {type(exc).__name__}: {exc}"
            ) from exc

    # -- lifecycle ---------------------------------------------------------- #

    def close(self) -> None:
        with self._lock:
            if self._supervisor is not None:
                with contextlib.suppress(Exception):
                    self._supervisor.close()
                self._supervisor = None
            with contextlib.suppress(Exception):
                self._db.close()

    def __enter__(self) -> RunsStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transactions ------------------------------------------------------- #

    @contextlib.contextmanager
    def transaction(self):
        """One ``BEGIN IMMEDIATE`` transaction; re-entrant (inner blocks join it)."""
        with self._lock:
            outermost = self._tx_depth == 0
            if outermost:
                self._db.execute("BEGIN IMMEDIATE")
            self._tx_depth += 1
            try:
                yield self._db
            except BaseException:
                self._tx_depth -= 1
                if self._tx_depth == 0:
                    with contextlib.suppress(sqlite3.Error):
                        self._db.execute("ROLLBACK")
                raise
            else:
                self._tx_depth -= 1
                if self._tx_depth == 0:
                    self._db.execute("COMMIT")

    # -- schema / migrations ------------------------------------------------ #

    def schema_version(self) -> int:
        value = self.meta("schema_version")
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    def migrate(self) -> int:
        """Additive migration: missing columns, then the version fence."""
        with self._lock:
            for table, columns in _ADDITIVE_COLUMNS.items():
                existing = {
                    str(row["name"]) for row in self._db.execute(f"PRAGMA table_info({table})")
                }
                for column, spec in columns.items():
                    if column in existing:
                        continue
                    # identifiers come from this module's constants, never from input
                    self._db.execute(  # nosec B608
                        f"ALTER TABLE {table} ADD COLUMN {column} {spec}"
                    )
            current = self.schema_version()
            if current < SCHEMA_VERSION:
                # v0 -> v1 is the initial layout; the fence exists so a future
                # build can refuse a store written by a newer one.
                self.set_meta("schema_version", str(SCHEMA_VERSION))
                self.set_meta("migrated_at", str(_now(None)))
            self._ensure_supervisor_tables_locked()
        return self.schema_version()

    def _ensure_supervisor_tables_locked(self) -> None:
        """Create ``runs``/``continuations``/``run_actions`` in this same file.

        MVP/10 §2.1 keeps the operation queue in the *existing* runs.db next to
        the run supervisor's tables; the supervisor owns their DDL, so we ask it
        to create them instead of duplicating the schema here.
        """
        try:
            supervisor = rs.RunStore(self.path)
        except Exception:  # noqa: BLE001 — the goal/operation tables stand alone
            return
        with contextlib.suppress(Exception):
            supervisor.close()

    # -- meta --------------------------------------------------------------- #

    def meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM runs_store_meta WHERE key = ?", (str(key),)
            ).fetchone()
        return str(row["value"]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO runs_store_meta(key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(key), str(value)),
            )

    # -- single writer ------------------------------------------------------ #

    def acquire_writer(
        self,
        *,
        owner: str = "",
        ttl: float | None = None,
        now: float | None = None,
        alive: Callable[[int | None], bool] = pid_alive,
        takeover: bool = True,
    ) -> dict:
        """Acquire/heartbeat the store's single-writer lease (CAS, idempotent).

        Returns the honest outcome: ``action`` is ``acquired``/``heartbeat``/
        ``takeover`` on success and ``refused``/``lost`` when another live process
        holds the lease (or won the compare-and-swap race).
        """
        moment = _now(now)
        ttl = self.lease_ttl if ttl is None else float(ttl)
        holder = str(owner or self.owner)
        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM runs_store_meta WHERE key = ?", (_META_WRITER_KEY,)
            ).fetchone()
            previous = str(row["value"]) if row else ""
            info = _loads(previous, {}) if previous else {}
            if not isinstance(info, dict):
                info = {}
            current_holder = str(info.get("holder") or "")
            expires_at = float(info.get("expires_at") or 0.0)
            same = bool(current_holder) and current_holder == holder
            held_pid = owner_pid(current_holder)
            live = bool(current_holder) and expires_at > moment and alive(held_pid)
            if previous and not same:
                if live:
                    # one writer only: a live holder is never overwritten
                    return {
                        "acquired": False,
                        "action": "refused",
                        "holder": current_holder,
                        "expires_at": expires_at,
                        "reason": (
                            f"the store is held by {current_holder} "
                            f"(pid alive, lease until {expires_at:.0f}) — one writer only"
                        ),
                        "owner": holder,
                        "path": self.path,
                    }
                if not takeover:
                    return {
                        "acquired": False,
                        "action": "refused",
                        "holder": current_holder,
                        "expires_at": expires_at,
                        "reason": (
                            f"the holder {current_holder} is gone but takeover is disabled "
                            "(read-only run)"
                        ),
                        "owner": holder,
                        "path": self.path,
                    }
            action = "heartbeat" if same else ("takeover" if previous else "acquired")
            payload = _json(
                {
                    "holder": holder,
                    "expires_at": moment + ttl,
                    "heartbeat_at": moment,
                    "acquired_at": float(info.get("acquired_at") or moment),
                }
            )
            # One upsert is the compare-and-swap: the row is created when absent
            # and rewritten only while it still holds the value just read.
            # The previous shape branched on `not previous`, so a row that
            # release_writer had blanked to "" looked like "no row yet": the
            # INSERT hit ON CONFLICT DO NOTHING, rowcount stayed 0, and the
            # outcome was reported as `lost`. Every run after the first then
            # became a silent no-op (exit 6, chat waiting forever).
            cursor = db.execute(
                "INSERT INTO runs_store_meta(key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value"
                " WHERE runs_store_meta.value = ?",
                (_META_WRITER_KEY, payload, previous),
            )
            if cursor.rowcount <= 0:
                fresh = _load_dict(
                    db.execute(
                        "SELECT value FROM runs_store_meta WHERE key = ?", (_META_WRITER_KEY,)
                    ).fetchone()["value"]
                )
                return {
                    "acquired": False,
                    "action": "lost",
                    "holder": str(fresh.get("holder") or "") or None,
                    "expires_at": float(fresh.get("expires_at") or 0.0),
                    "reason": "another process won the compare-and-swap for the writer lease",
                    "owner": holder,
                    "path": self.path,
                }
        return {
            "acquired": True,
            "action": action,
            "holder": holder,
            "expires_at": moment + ttl,
            "reason": "",
            "owner": holder,
            "path": self.path,
        }

    def heartbeat_writer(self, *, owner: str = "", ttl: float | None = None,
                         now: float | None = None) -> bool:
        outcome = self.acquire_writer(owner=owner, ttl=ttl, now=now)
        return bool(outcome["acquired"])

    def release_writer(self, *, owner: str = "") -> bool:
        holder = str(owner or self.owner)
        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM runs_store_meta WHERE key = ?", (_META_WRITER_KEY,)
            ).fetchone()
            if not row:
                return False
            info = _loads(str(row["value"]), {})
            if not isinstance(info, dict) or str(info.get("holder") or "") != holder:
                return False
            cursor = db.execute(
                "UPDATE runs_store_meta SET value = ? WHERE key = ? AND value = ?",
                ("", _META_WRITER_KEY, str(row["value"])),
            )
        return cursor.rowcount > 0

    def writer(self, *, now: float | None = None) -> dict:
        moment = _now(now)
        info = _loads(self.meta(_META_WRITER_KEY) or "{}", {})
        if not isinstance(info, dict):
            info = {}
        holder = str(info.get("holder") or "")
        expires_at = float(info.get("expires_at") or 0.0)
        return {
            "holder": holder,
            "owner_pid": owner_pid(holder),
            "pid_alive": pid_alive(owner_pid(holder)) if holder else False,
            "expires_at": expires_at,
            "expired": bool(holder) and expires_at <= moment,
            "heartbeat_at": float(info.get("heartbeat_at") or 0.0),
            "held": bool(holder),
        }

    def assert_writer(self, *, owner: str = "", now: float | None = None) -> None:
        """Refuse a write when another live process owns the store."""
        info = self.writer(now=now)
        if not info["held"]:
            return
        holder = str(info["holder"])
        if holder == str(owner or self.owner):
            return
        if info["pid_alive"] and not info["expired"]:
            raise WriterConflict(
                f"{self.path} is held by {holder} (pid alive, lease until "
                f"{info['expires_at']:.0f}) — one writer only"
            )

    # -- goal records ------------------------------------------------------- #

    def runtime_dir(self) -> Path:
        return Path(self.path).parent

    def start_goal(
        self,
        goal_text: str,
        *,
        session_id: str = "",
        criteria: Iterable[object] = (),
        task_base: str = "",
        limits: Mapping[str, int] | None = None,
        workspace_fingerprint: str = "",
        goal_id: str = "",
        owner: str = "",
        now: float | None = None,
    ) -> Goal:
        """Create the goal record, its criteria, its budget rows and a checkpoint."""
        moment = _now(now)
        text = str(goal_text or "").strip()
        if not text:
            raise RunsError("a goal needs a text")
        session = str(session_id or "cli")
        identifier = goal_id or f"{session}:g{uuid.uuid4().hex[:12]}"
        holder = str(owner or self.owner)
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO goals(goal_id, schema_version, revision, session_id, state,
                    goal_text, task_base, workspace_fingerprint, created, updated,
                    revision_log, decisions, history_ref, lease_owner, lease_expires_at,
                    heartbeat_at)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', ?, ?, ?, ?)
                """,
                (
                    identifier,
                    SCHEMA_VERSION,
                    session,
                    WORKING,
                    text,
                    str(task_base or ""),
                    str(workspace_fingerprint or ""),
                    moment,
                    moment,
                    str(self.runtime_dir() / "goals" / f"{identifier}.jsonl"),
                    holder,
                    moment + self.lease_ttl,
                    moment,
                ),
            )
            for position, item in enumerate(criteria):
                spec = _criterion_spec(item, position)
                db.execute(
                    """
                    INSERT INTO criteria(goal_id, criterion_id, position, text, blocking,
                        verification_plan, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'open')
                    """,
                    (
                        identifier,
                        spec["criterion_id"],
                        position,
                        spec["text"],
                        int(bool(spec["blocking"])),
                        spec["verification_plan"],
                    ),
                )
            for role, limit in _limit_items(limits):
                db.execute(
                    """
                    INSERT INTO budget_state(goal_id, role, limit_tokens, spent, reserved,
                        unreserved_spent, updated)
                    VALUES (?, ?, ?, 0, 0, 0, ?)
                    ON CONFLICT(goal_id, role) DO UPDATE SET
                        limit_tokens = excluded.limit_tokens, updated = excluded.updated
                    """,
                    (identifier, role, int(limit), moment),
                )
            db.execute(
                "INSERT INTO resume_cursor(goal_id, updated) VALUES (?, ?)"
                " ON CONFLICT(goal_id) DO NOTHING",
                (identifier, moment),
            )
        self.write_checkpoint(
            identifier,
            next_action="read the goal record, then start the first open criterion",
            now=moment,
        )
        goal = self.goal(identifier)
        if goal is None:  # pragma: no cover — defensive
            raise RunsError(f"goal {identifier} vanished after insert")
        return goal

    def add_criterion(
        self,
        goal_id: str,
        text: str,
        *,
        criterion_id: str = "",
        blocking: bool = True,
        verification_plan: str = "",
        reason: str = "",
        now: float | None = None,
    ) -> dict:
        """Add a criterion — a criteria edit, so the revision bumps and evidence ages."""
        moment = _now(now)
        goal = self.require_goal(goal_id)
        with self.transaction() as db:
            position = int(
                db.execute(
                    "SELECT COALESCE(MAX(position), -1) AS last FROM criteria WHERE goal_id = ?",
                    (goal.goal_id,),
                ).fetchone()["last"]
            ) + 1
            identifier = criterion_id or f"c{position + 1}"
            db.execute(
                """
                INSERT INTO criteria(goal_id, criterion_id, position, text, blocking,
                    verification_plan, status)
                VALUES (?, ?, ?, ?, ?, ?, 'open')
                ON CONFLICT(goal_id, criterion_id) DO UPDATE SET
                    text = excluded.text, blocking = excluded.blocking,
                    verification_plan = excluded.verification_plan
                """,
                (
                    goal.goal_id,
                    identifier,
                    position,
                    str(text),
                    int(bool(blocking)),
                    str(verification_plan),
                ),
            )
            self._bump_revision_locked(db, goal, reason=reason or "criterion added", now=moment)
        return {"goal_id": goal.goal_id, "criterion_id": identifier, "position": position}

    def set_goal_state(
        self,
        goal_id: str,
        state: str,
        *,
        blocked_reason: str = "",
        wake_source: str = "",
        now: float | None = None,
    ) -> Goal:
        if state not in GOAL_STATES:
            raise RunsError(f"unknown goal state: {state!r}")
        moment = _now(now)
        goal = self.require_goal(goal_id)
        if goal.terminal:
            raise RunsError(f"goal {goal.goal_id} is frozen in {goal.state}")
        with self.transaction() as db:
            db.execute(
                "UPDATE goals SET state = ?, blocked_reason = ?, wake_source = ?, updated = ?"
                " WHERE goal_id = ?",
                (state, str(blocked_reason), str(wake_source), moment, goal.goal_id),
            )
        updated = self.require_goal(goal.goal_id)
        self._append_goal_history(updated, "state", {"state": state, "reason": blocked_reason})
        return updated

    def add_decision(
        self,
        goal_id: str,
        *,
        kind: str,
        text: str,
        reason: str = "",
        evidence_refs: Iterable[str] = (),
        now: float | None = None,
    ) -> dict:
        """Record a decision/assumption/non-goal — never a revision bump (§2.2)."""
        if kind not in ("decision", "assumption", "non_goal"):
            raise RunsError(f"unknown decision kind: {kind!r}")
        moment = _now(now)
        goal = self.require_goal(goal_id)
        entry = {
            "id": f"d-{uuid.uuid4().hex[:10]}",
            "at": moment,
            "kind": str(kind),
            "text": str(text),
            "reason": str(reason),
            "superseded_by": "",
            "evidence_refs": [str(ref) for ref in evidence_refs],
        }
        decisions = list(goal.decisions)
        decisions.append(entry)
        with self.transaction() as db:
            db.execute(
                "UPDATE goals SET decisions = ?, updated = ? WHERE goal_id = ?",
                (_json(decisions), moment, goal.goal_id),
            )
        self._append_goal_history(goal, "decision", entry)
        return entry

    def record_criterion_outcome(
        self,
        goal_id: str,
        criterion_id: str,
        *,
        outcome: str,
        evidence_refs: Iterable[str],
        check_id: str = "",
        workspace: str = "",
        workspace_fingerprint: str | None = None,
        revision: int | None = None,
        cmd: str = "",
        exit_code: int = -1,
        artifact: str = "",
        run_id: str = "",
        now: float | None = None,
    ) -> dict:
        """Flip a criterion from *evidence* only — never from a status parameter.

        Refuses (with :class:`EvidenceRequired` / :class:`StaleEvidence`, writing
        nothing) when the refs are empty, when ``revision`` is not the record's
        revision, or when the workspace fingerprint does not match the goal
        (MVP/10 R6: a foreign-workspace green run cannot certify).
        """
        if outcome not in (STATUS_VERIFIED, STATUS_FAILED):
            raise RunsError(f"criterion outcome must be verified|failed, not {outcome!r}")
        refs = [str(ref) for ref in evidence_refs if str(ref).strip()]
        if not refs:
            raise EvidenceRequired(
                f"criterion {criterion_id} needs at least one evidence ref — a status "
                "without evidence is a claim"
            )
        moment = _now(now)
        goal = self.require_goal(goal_id)
        if revision is not None and int(revision) != int(goal.revision):
            raise StaleEvidence(
                f"evidence revision {int(revision)} != goal revision {goal.revision}: "
                "a change to the goal invalidates earlier evidence"
            )
        fingerprint = (
            str(workspace_fingerprint)
            if workspace_fingerprint is not None
            else str(goal.workspace_fingerprint or "")
        )
        if goal.workspace_fingerprint and fingerprint != goal.workspace_fingerprint:
            raise StaleEvidence(
                "evidence from another workspace cannot certify this goal "
                f"({fingerprint or '(none)'} != {goal.workspace_fingerprint})"
            )
        criterion = self.criterion(goal.goal_id, criterion_id)
        if criterion is None:
            raise UnknownGoal(f"no criterion {criterion_id} on {goal.goal_id}")
        with self.transaction() as db:
            for ref in refs:
                db.execute(
                    """
                    INSERT INTO criterion_evidence(goal_id, criterion_id, ref, kind, at,
                        revision, workspace, check_id, cmd, exit_code, outcome, artifact, run_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(goal_id, criterion_id, ref) DO NOTHING
                    """,
                    (
                        goal.goal_id,
                        criterion.criterion_id,
                        ref,
                        evidence_kind(ref),
                        moment,
                        int(goal.revision),
                        str(workspace or goal.task_base or ""),
                        str(check_id),
                        str(cmd),
                        int(exit_code),
                        str(outcome),
                        str(artifact),
                        str(run_id),
                    ),
                )
            db.execute(
                """
                UPDATE criteria SET status = ?, evidence_revision = ?,
                    evidence_workspace_fingerprint = ?, verified_at = ?, verified_by = ?,
                    last_failure = ?
                WHERE goal_id = ? AND criterion_id = ?
                """,
                (
                    outcome,
                    int(goal.revision),
                    fingerprint,
                    moment if outcome == STATUS_VERIFIED else 0.0,
                    str(check_id) if outcome == STATUS_VERIFIED else "",
                    "" if outcome == STATUS_VERIFIED else str(check_id or "check failed"),
                    goal.goal_id,
                    criterion.criterion_id,
                ),
            )
        updated = self.criterion(goal.goal_id, criterion.criterion_id)
        assert updated is not None  # the UPDATE above targeted an existing row
        self._append_goal_history(
            goal,
            "criterion",
            {"criterion_id": updated.criterion_id, "status": updated.status, "refs": refs},
        )
        return {
            "goal_id": goal.goal_id,
            "criterion_id": updated.criterion_id,
            "status": updated.status,
            "evidence_refs": refs,
            "revision": int(goal.revision),
            "check_id": str(check_id),
            "exit_code": int(exit_code),
            "applied": True,
        }

    def demote_stale_criteria(self, goal_id: str, *, now: float | None = None) -> list[dict]:
        """Re-check freshness; a stale ``verified`` goes back to ``open`` (R5.3)."""
        goal = self.require_goal(goal_id)
        demoted: list[dict] = []
        for criterion in self.criteria(goal.goal_id):
            if criterion.status != STATUS_VERIFIED or criterion.fresh(goal):
                continue
            reason = (
                f"evidence_revision {criterion.evidence_revision} != revision {goal.revision}"
                if int(criterion.evidence_revision) != int(goal.revision)
                else "evidence workspace fingerprint no longer matches"
            )
            with self.transaction() as db:
                db.execute(
                    "UPDATE criteria SET status = 'open', verified_at = 0,"
                    " verified_by = '', last_failure = ? WHERE goal_id = ? AND criterion_id = ?",
                    (reason, goal.goal_id, criterion.criterion_id),
                )
            demoted.append(
                {"criterion_id": criterion.criterion_id, "reason": reason, "status": STATUS_OPEN}
            )
        if demoted:
            self._append_goal_history(goal, "criteria_demoted", {"demoted": demoted})
        return demoted

    # -- goal reads --------------------------------------------------------- #

    def goal(self, goal_id: str) -> Goal | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM goals WHERE goal_id = ?", (str(goal_id),)
            ).fetchone()
        return _goal_from_row(row) if row else None

    def require_goal(self, goal_id: str) -> Goal:
        goal = self.goal(goal_id)
        if goal is None:
            raise UnknownGoal(f"no goal record {goal_id!r} in {self.path}")
        return goal

    def goals(self, *, include_terminal: bool = True) -> list[Goal]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM goals ORDER BY updated DESC, goal_id"
            ).fetchall()
        records = [_goal_from_row(row) for row in rows]
        if include_terminal:
            return records
        return [item for item in records if not item.terminal]

    def goal_for_session(self, session_id: str) -> Goal | None:
        """Latest non-terminal goal of the session, else the latest goal of it."""
        if not str(session_id or "").strip():
            return None
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM goals WHERE session_id = ? ORDER BY updated DESC, created DESC",
                (str(session_id),),
            ).fetchall()
        records = [_goal_from_row(row) for row in rows]
        for record in records:
            if not record.terminal:
                return record
        return records[0] if records else None

    def resolve_goal(
        self, *, goal_id: str = "", session_id: str = ""
    ) -> Goal | None:
        """Explicit id wins; then the session's goal; then the newest open goal."""
        if goal_id:
            return self.goal(goal_id)
        if session_id:
            return self.goal_for_session(session_id)
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM goals ORDER BY CASE WHEN state IN ('VERIFIED_COMPLETE',"
                " 'BLOCKED_USER', 'BLOCKED_EXTERNAL', 'FAILED_UNRECOVERABLE',"
                " 'USER_CANCELLED', 'SHIPPED_WITH_USER_OVERRIDE') THEN 1 ELSE 0 END,"
                " updated DESC LIMIT 1"
            ).fetchone()
        return _goal_from_row(row) if row else None

    # -- operator messages (`/steer`, `/queue`) ----------------------------- #

    def messages_enabled(self) -> bool:
        """Does this store take operator messages? (absent = yes, the default)"""
        value = self.meta(_META_MESSAGES_KEY)
        if value is None:
            return True
        return str(value).strip().lower() not in ("0", "false", "no", "off")

    def set_messages_enabled(self, enabled: bool) -> bool:
        """Turn the message channel on/off for this store (the ``queue-disabled`` refusal)."""
        self.set_meta(_META_MESSAGES_KEY, "true" if enabled else "false")
        return bool(enabled)

    def post_message(
        self,
        goal_id: str,
        text: str,
        *,
        kind: str = MESSAGE_STEER,
        origin: str = "",
        at: float | None = None,
        cap: int = MESSAGE_CAP,
    ) -> Message:
        """Record one operator message for ``goal_id`` — durable, ordered, or refused.

        Refusals are *named* (``MessageRefused.reason``): ``no-run`` for an empty/unknown goal id,
        ``run-finished`` for a goal that is already terminal, ``empty-text`` for whitespace,
        ``queue-full`` past :data:`MESSAGE_CAP` pending rows, ``queue-disabled`` when the store
        says so, ``unknown-kind`` for a kind outside :data:`MESSAGE_KINDS`. Nothing here ever
        drops a message quietly: the caller hears the reason or the row exists.
        """
        wanted = str(goal_id or "").strip()
        body = str(text or "").strip()
        category = str(kind or MESSAGE_STEER).strip().lower()
        if category not in MESSAGE_KINDS:
            raise MessageRefused(
                REFUSAL_UNKNOWN_KIND,
                f"{kind!r} is not a message kind; known: {', '.join(MESSAGE_KINDS)}",
                details={"kind": kind},
            )
        if not body:
            raise MessageRefused(REFUSAL_EMPTY, "an empty message steers nothing")
        if not wanted:
            raise MessageRefused(
                REFUSAL_NO_RUN, "no run is attached to this session", details={"goal_id": ""}
            )
        stamp = _now(at)
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM goals WHERE goal_id = ?", (wanted,)
            ).fetchone()
            if row is None:
                raise MessageRefused(
                    REFUSAL_NO_RUN,
                    f"no run {wanted!r} in {self.path}",
                    details={"goal_id": wanted},
                )
            goal = _goal_from_row(row)
            if goal.terminal:
                raise MessageRefused(
                    REFUSAL_RUN_FINISHED,
                    f"run {wanted} is {goal.state} — a finished run takes no messages",
                    details={"goal_id": wanted, "state": goal.state},
                )
            if not self.messages_enabled():
                raise MessageRefused(
                    REFUSAL_QUEUE_DISABLED,
                    f"this store does not take operator messages ({self.path})",
                    details={"goal_id": wanted},
                )
            waiting = db.execute(
                "SELECT COUNT(*) AS n FROM run_messages WHERE goal_id = ? AND state = ?",
                (wanted, MESSAGE_PENDING),
            ).fetchone()
            pending = int(waiting["n"] if waiting else 0)
            if pending >= int(cap):
                raise MessageRefused(
                    REFUSAL_QUEUE_FULL,
                    f"run {wanted} already has {pending} message(s) waiting (cap {int(cap)})",
                    details={"goal_id": wanted, "pending": pending, "cap": int(cap)},
                )
            cursor = db.execute(
                "INSERT INTO run_messages(goal_id, kind, origin, text, created, state)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (wanted, category, str(origin or self.owner), body, stamp, MESSAGE_PENDING),
            )
            message_id = int(cursor.lastrowid or 0)
            row = db.execute("SELECT * FROM run_messages WHERE id = ?", (message_id,)).fetchone()
        return _message_from_row(row)

    def record_refusal(
        self,
        goal_id: str,
        text: str,
        *,
        kind: str = MESSAGE_STEER,
        origin: str = "",
        reason: str,
        at: float | None = None,
    ) -> Message:
        """Write down a message this store refused, so the attempt is not invisible.

        The row is ``state=refused`` and carries ``refused_reason``; it is never drained by a run.
        (The refusal itself is still raised to the caller by :meth:`post_message` — this method is
        for the *log* half of "never silently drop".)
        """
        stamp = _now(at)
        wanted = str(goal_id or "").strip()
        with self.transaction() as db:
            cursor = db.execute(
                "INSERT INTO run_messages(goal_id, kind, origin, text, created, state,"
                " refused_reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    wanted,
                    str(kind or MESSAGE_STEER),
                    str(origin or self.owner),
                    str(text or ""),
                    stamp,
                    MESSAGE_REFUSED,
                    str(reason or ""),
                ),
            )
            message_id = int(cursor.lastrowid or 0)
            row = db.execute("SELECT * FROM run_messages WHERE id = ?", (message_id,)).fetchone()
        return _message_from_row(row)

    def messages(
        self,
        goal_id: str,
        *,
        state: str = "",
        kind: str = "",
        limit: int = 200,
    ) -> list[Message]:
        """This run's messages, oldest first (optionally filtered by state/kind)."""
        query = "SELECT * FROM run_messages WHERE goal_id = ?"
        params: list[object] = [str(goal_id)]
        if state:
            query += " AND state = ?"
            params.append(str(state))
        if kind:
            query += " AND kind = ?"
            params.append(str(kind))
        query += " ORDER BY id ASC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        return [_message_from_row(row) for row in rows]

    def pending_messages(self, goal_id: str, *, kind: str = "") -> list[Message]:
        """The messages waiting for a turn boundary, oldest first (FIFO is the id order)."""
        return self.messages(goal_id, state=MESSAGE_PENDING, kind=kind)

    def consume_messages(
        self,
        goal_id: str,
        *,
        turn_index: int,
        turn_name: str,
        at: float | None = None,
    ) -> list[Message]:
        """Take the messages for one turn boundary: **all** steers, then one queued message.

        This is the drain protocol in one place, so the ordering rule ("a steer is urgent, a
        queued message is one per turn") cannot drift between callers. Each consumed row is
        stamped with the turn that took it — the decision is durable, not a debug line.
        """
        stamp = _now(at)
        taken: list[Message] = []
        with self.transaction() as db:
            rows = db.execute(
                "SELECT * FROM run_messages WHERE goal_id = ? AND state = ? ORDER BY id ASC",
                (str(goal_id), MESSAGE_PENDING),
            ).fetchall()
            waiting = [_message_from_row(row) for row in rows]
            chosen = [item for item in waiting if item.kind == MESSAGE_STEER]
            queued = [item for item in waiting if item.kind == MESSAGE_QUEUE]
            if queued:
                chosen.append(queued[0])
            for item in chosen:
                db.execute(
                    "UPDATE run_messages SET state = ?, consumed_at = ?, consumed_turn = ?,"
                    " turn_name = ? WHERE id = ?",
                    (MESSAGE_CONSUMED, stamp, int(turn_index), str(turn_name), item.message_id),
                )
            if chosen:
                ids = [item.message_id for item in chosen]
                marks = ",".join("?" for _ in ids)
                rows = db.execute(
                    f"SELECT * FROM run_messages WHERE id IN ({marks}) ORDER BY id ASC", ids
                ).fetchall()
                taken = [_message_from_row(row) for row in rows]
        return taken

    def messages_summary(self, goal_id: str) -> dict:
        """Honest counts for a run's message channel (the ``runs status``-shaped view)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT kind, state, COUNT(*) AS n FROM run_messages WHERE goal_id = ?"
                " GROUP BY kind, state",
                (str(goal_id),),
            ).fetchall()
            last = self._db.execute(
                "SELECT * FROM run_messages WHERE goal_id = ? AND state = ?"
                " ORDER BY consumed_turn DESC, id DESC LIMIT 1",
                (str(goal_id), MESSAGE_CONSUMED),
            ).fetchone()
        counts = {f"{row['kind']}:{row['state']}": int(row["n"]) for row in rows}
        pending = self.pending_messages(goal_id)
        return {
            "goal_id": str(goal_id),
            "counts": counts,
            "pending": [item.to_dict() for item in pending],
            "pending_count": len(pending),
            "enabled": self.messages_enabled(),
            "last_consumed": _message_from_row(last).to_dict() if last else None,
        }

    def criteria(self, goal_id: str) -> list[Criterion]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM criteria WHERE goal_id = ? ORDER BY position, criterion_id",
                (str(goal_id),),
            ).fetchall()
        return [_criterion_from_row(row, self.evidence(goal_id, str(row["criterion_id"])))
                for row in rows]

    def criterion(self, goal_id: str, criterion_id: str) -> Criterion | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM criteria WHERE goal_id = ? AND criterion_id = ?",
                (str(goal_id), str(criterion_id)),
            ).fetchone()
        if row is None:
            return None
        return _criterion_from_row(row, self.evidence(goal_id, str(criterion_id)))

    def evidence(self, goal_id: str, criterion_id: str = "") -> list[dict]:
        query = "SELECT * FROM criterion_evidence WHERE goal_id = ?"
        params: list = [str(goal_id)]
        if criterion_id:
            query += " AND criterion_id = ?"
            params.append(str(criterion_id))
        query += " ORDER BY id"
        with self._lock:
            rows = self._db.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def open_criteria(self, goal_id: str) -> list[str]:
        return [
            item.criterion_id
            for item in self.criteria(goal_id)
            if item.status != STATUS_VERIFIED
        ]

    # -- operations --------------------------------------------------------- #

    def register_operation(
        self,
        goal_id: str,
        *,
        kind: str,
        target: str = "",
        payload: object = "",
        payload_hash_value: str = "",
        idempotency_key_value: str = "",
        idempotent: bool = False,
        run_id: str = "",
        revision: int | None = None,
        op_id: str = "",
        now: float | None = None,
    ) -> tuple[Operation, bool]:
        """Insert the intent row **before** dispatch; duplicate key ⇒ the old row.

        ``UNIQUE(idempotency_key)`` is the double-effect fence: a second attempt
        with the same key returns the recorded operation (``duplicate: True``) so
        the caller can return the recorded result instead of dispatching again.
        """
        moment = _now(now)
        goal = self.require_goal(goal_id)
        revision_value = int(goal.revision if revision is None else revision)
        digest = str(payload_hash_value) or payload_hash(payload)
        key = str(idempotency_key_value) or idempotency_key(
            goal.goal_id, revision_value, str(kind), str(target), digest
        )
        identifier = op_id or f"op-{uuid.uuid4().hex[:12]}"
        with self.transaction() as db:
            cursor = db.execute(
                """
                INSERT INTO operations(op_id, goal_id, idempotency_key, kind, target,
                    payload_hash, revision, state, side_effect_state, idempotent,
                    created, disposition, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (
                    identifier,
                    goal.goal_id,
                    key,
                    str(kind),
                    str(target),
                    digest,
                    revision_value,
                    OP_PENDING,
                    SIDE_EFFECT_NONE,
                    int(bool(idempotent)),
                    moment,
                    DISPOSITION_IDLE,
                    str(run_id),
                ),
            )
            created = cursor.rowcount > 0
            if not created:
                row = db.execute(
                    "SELECT * FROM operations WHERE idempotency_key = ?", (key,)
                ).fetchone()
                if row is None:  # pragma: no cover — UNIQUE conflict without a row
                    raise IdempotencyConflict(f"idempotency key {key} vanished")
                operation = _operation_from_row(row)
                operation.duplicate = True
                return operation, False
        self._mirror_action(identifier, idempotent=bool(idempotent), note=f"{kind}:{target}")
        return self.operation(identifier) or _empty_operation(identifier, goal.goal_id), True

    def operation(self, op_id: str) -> Operation | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM operations WHERE op_id = ?", (str(op_id),)
            ).fetchone()
        return _operation_from_row(row) if row else None

    def operations(
        self, goal_id: str = "", *, states: Iterable[str] | None = None, parked: bool = False
    ) -> list[Operation]:
        query = "SELECT * FROM operations WHERE 1 = 1"
        params: list = []
        if goal_id:
            query += " AND goal_id = ?"
            params.append(str(goal_id))
        if states is not None:
            wanted = [str(item) for item in states]
            if not wanted:
                return []
            query += f" AND state IN ({', '.join('?' for _ in wanted)})"
            params.extend(wanted)
        if parked:
            query += " AND (state = ? OR side_effect_state = ?)"
            params.extend([OP_UNKNOWN, SIDE_EFFECT_UNKNOWN])
        query += " ORDER BY created, op_id"
        with self._lock:
            rows = self._db.execute(query, tuple(params)).fetchall()
        return [_operation_from_row(row) for row in rows]

    def begin_operation(
        self,
        op_id: str,
        *,
        owner: str = "",
        ttl: float | None = None,
        now: float | None = None,
    ) -> bool:
        """``pending -> in_flight`` with the owner lease — the last step before the call."""
        moment = _now(now)
        ttl = self.lease_ttl if ttl is None else float(ttl)
        holder = str(owner or self.owner)
        with self.transaction() as db:
            cursor = db.execute(
                """
                UPDATE operations SET state = ?, attempt = attempt + 1, lease_owner = ?,
                    lease_expires_at = ?, heartbeat_at = ?, started_at = ?
                WHERE op_id = ? AND state = ?
                """,
                (
                    OP_IN_FLIGHT,
                    holder,
                    moment + ttl,
                    moment,
                    moment,
                    str(op_id),
                    OP_PENDING,
                ),
            )
        return cursor.rowcount > 0

    def complete_operation(
        self,
        op_id: str,
        *,
        result_ref: str,
        note: str = "",
        receipts_dir: str | os.PathLike | None = None,
        now: float | None = None,
    ) -> dict:
        """Commit ``done`` + the proof ref, and write the operation receipt.

        The receipt file is written **first** (atomic), then the row: ``RESOLVED``
        must be provable from disk even if the row were lost.
        """
        moment = _now(now)
        operation = self.operation(op_id)
        if operation is None:
            raise RunsError(f"no operation {op_id!r}")
        ref = str(result_ref)
        receipt = self.write_operation_receipt(
            operation,
            result_ref=ref,
            note=note,
            receipts_dir=receipts_dir,
            now=moment,
        )
        with self.transaction() as db:
            cursor = db.execute(
                """
                UPDATE operations SET state = ?, side_effect_state = ?, finished_at = ?,
                    result_ref = ?, last_error = '', lease_owner = '', lease_expires_at = 0
                WHERE op_id = ? AND state IN (?, ?, ?)
                """,
                (
                    OP_DONE,
                    SIDE_EFFECT_KNOWN,
                    moment,
                    ref,
                    operation.op_id,
                    OP_PENDING,
                    OP_IN_FLIGHT,
                    OP_UNKNOWN,
                ),
            )
        self._mirror_verified(operation.op_id)
        return {
            "op_id": operation.op_id,
            "state": OP_DONE,
            "side_effect_state": SIDE_EFFECT_KNOWN,
            "result_ref": ref,
            "receipt": str(receipt),
            "applied": cursor.rowcount > 0,
        }

    def requeue_operation(
        self, op_id: str, *, reason: str = "", now: float | None = None
    ) -> bool:
        """``in_flight|failed -> pending`` with the lease cleared, same attempt count.

        R2 ``REPEAT``: an idempotent operation whose owner died is safe to
        dispatch **again under the same idempotency key** — the key, not the
        state, is the double-effect fence. ``attempt`` is not reset: it counts
        dispatches, and the next :meth:`begin_operation` increments it.
        """
        with self.transaction() as db:
            cursor = db.execute(
                """
                UPDATE operations SET state = ?, lease_owner = '', lease_expires_at = 0,
                    last_error = ?
                WHERE op_id = ? AND state IN (?, ?)
                """,
                (
                    OP_PENDING,
                    str(reason),
                    str(op_id),
                    OP_IN_FLIGHT,
                    OP_FAILED,
                ),
            )
        return cursor.rowcount > 0

    def fail_operation(
        self, op_id: str, *, error: str, now: float | None = None
    ) -> bool:
        """A failure whose side effect provably did **not** land."""
        moment = _now(now)
        with self.transaction() as db:
            cursor = db.execute(
                """
                UPDATE operations SET state = ?, side_effect_state = ?, finished_at = ?,
                    last_error = ?, lease_owner = '', lease_expires_at = 0
                WHERE op_id = ? AND state IN (?, ?)
                """,
                (
                    OP_FAILED,
                    SIDE_EFFECT_NONE,
                    moment,
                    str(error),
                    str(op_id),
                    OP_PENDING,
                    OP_IN_FLIGHT,
                ),
            )
        return cursor.rowcount > 0

    def park_operation(
        self,
        op_id: str,
        *,
        reason: str,
        wake_source: str = OPERATOR_WAKE_SOURCE,
        now: float | None = None,
    ) -> dict:
        """Park an unprovable operation: state ``unknown``, **no automatic retry**.

        R3: a claim left behind by a dead owner must not execute behind the
        operator's back, so any open continuation of the operation's run is
        superseded first.
        """
        moment = _now(now)
        operation = self.operation(op_id)
        if operation is None:
            raise RunsError(f"no operation {op_id!r}")
        superseded = self._supersede_continuations(operation.run_id, reason=reason, now=moment)
        with self.transaction() as db:
            cursor = db.execute(
                """
                UPDATE operations SET state = ?, side_effect_state = ?, disposition = ?,
                    wake_source = ?, last_error = ?, lease_owner = '', lease_expires_at = 0
                WHERE op_id = ?
                """,
                (
                    OP_UNKNOWN,
                    SIDE_EFFECT_UNKNOWN,
                    DISPOSITION_UNKNOWN,
                    str(wake_source),
                    str(reason),
                    operation.op_id,
                ),
            )
        return {
            "op_id": operation.op_id,
            "state": OP_UNKNOWN,
            "side_effect_state": SIDE_EFFECT_UNKNOWN,
            "wake_source": str(wake_source),
            "superseded_continuations": superseded,
            "applied": cursor.rowcount > 0,
        }

    def resolve_operation(
        self,
        op_id: str,
        *,
        result_ref: str,
        operator: str = "",
        note: str = "",
        now: float | None = None,
    ) -> dict:
        """Operator confirmation that a parked effect **did** land (RESOLVED)."""
        moment = _now(now)
        operation = self.operation(op_id)
        if operation is None:
            raise RunsError(f"no operation {op_id!r}")
        if not str(operator).strip():
            raise RunsError("confirming a parked operation needs the operator's identity")
        receipt = self.write_operation_receipt(
            operation,
            result_ref=str(result_ref),
            note=f"operator {operator}: {note}".strip(),
            now=moment,
        )
        with self.transaction() as db:
            db.execute(
                """
                UPDATE operations SET state = ?, side_effect_state = ?, disposition = ?,
                    wake_source = '', finished_at = ?, result_ref = ?, last_error = ''
                WHERE op_id = ?
                """,
                (
                    OP_DONE,
                    SIDE_EFFECT_KNOWN,
                    DISPOSITION_RESOLVED,
                    moment,
                    str(result_ref),
                    operation.op_id,
                ),
            )
        return {
            "op_id": operation.op_id,
            "state": OP_DONE,
            "result_ref": str(result_ref),
            "receipt": str(receipt),
            "operator": str(operator),
        }

    def classify_operation(
        self,
        operation: Operation,
        *,
        path_exists: Callable[[str], bool] = os.path.exists,
    ) -> tuple[str, str]:
        """``(disposition, reason)`` for one operation (MVP/10 R2).

        ``UNKNOWN`` is the honest answer whenever the effect may have landed and
        cannot be proven: it parks, it never retries.
        """
        ref = str(operation.result_ref or "")
        if operation.state == OP_DONE:
            return DISPOSITION_RESOLVED, "state=done"
        if ref and path_exists(ref.split(":", 1)[-1] if ref.startswith("file:") else ref):
            return DISPOSITION_RESOLVED, f"result ref exists: {ref}"
        if operation.state == OP_FAILED and operation.side_effect_state == SIDE_EFFECT_NONE:
            return DISPOSITION_IDLE, "failed with a proven absent side effect"
        if operation.state == OP_PENDING and int(operation.attempt) == 0:
            return DISPOSITION_IDLE, "intent row only — nothing was in flight"
        if operation.state == OP_PENDING and operation.idempotent:
            return DISPOSITION_REPEAT, "registered idempotent — safe to repeat with the same key"
        info = self._action_info(operation.op_id)
        if info and info.get("verified_at"):
            return DISPOSITION_RESOLVED, "run_actions registry has a verified result"
        if info and info.get("idempotent") and operation.state in (OP_PENDING, OP_UNKNOWN):
            return DISPOSITION_REPEAT, "run_actions registry says idempotent"
        if operation.idempotent:
            return DISPOSITION_REPEAT, "idempotent=true — repeat with the same key"
        return (
            DISPOSITION_UNKNOWN,
            "an attempt was made and no result could be proven — no retry",
        )

    def write_operation_receipt(
        self,
        operation: Operation,
        *,
        result_ref: str,
        note: str = "",
        receipts_dir: str | os.PathLike | None = None,
        now: float | None = None,
    ) -> Path:
        moment = _now(now)
        base = (
            Path(receipts_dir)
            if receipts_dir
            else self.runtime_dir() / "receipts" / "operations"
        )
        target = base / f"{operation.op_id}.json"
        atomic_write_text(
            target,
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "op_id": operation.op_id,
                    "goal_id": operation.goal_id,
                    "kind": operation.kind,
                    "target": operation.target,
                    "idempotency_key": operation.idempotency_key,
                    "attempt": operation.attempt,
                    "state": OP_DONE,
                    "side_effect_state": SIDE_EFFECT_KNOWN,
                    "result_ref": str(result_ref),
                    "note": str(note),
                    "at": moment,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        return target

    # -- checkpoints / cursor ----------------------------------------------- #

    def write_checkpoint(
        self,
        goal_id: str,
        *,
        next_action: str = "",
        last_event_seq: int | None = None,
        payload: Mapping[str, object] | None = None,
        directory: str | os.PathLike | None = None,
        now: float | None = None,
    ) -> dict:
        """Write the self-sufficient snapshot file + its index row + the cursor."""
        moment = _now(now)
        goal = self.require_goal(goal_id)
        criteria = self.criteria(goal.goal_id)
        operations = self.operations(goal.goal_id)
        open_criteria = [item.criterion_id for item in criteria if item.status != STATUS_VERIFIED]
        open_operations = [item.op_id for item in operations if item.state in OPEN_OP_STATES]
        cursor = self.cursor(goal.goal_id)
        event_seq = (
            int(cursor["last_event_seq"]) if last_event_seq is None else int(last_event_seq)
        )
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM checkpoints WHERE goal_id = ? ORDER BY seq DESC LIMIT 1",
                (goal.goal_id,),
            ).fetchone()
        seq = int(row["seq"]) + 1 if row else 1
        prev = str(row["sha256"]) if row else ""
        checkpoint_id = f"cp-{goal.goal_id.split(':')[0]}-{seq}-{uuid.uuid4().hex[:6]}"
        document: dict = {
            "schema_version": SCHEMA_VERSION,
            "checkpoint_id": checkpoint_id,
            "goal_id": goal.goal_id,
            "goal": goal.goal_text,
            "seq": seq,
            "at": moment,
            "revision": int(goal.revision),
            "state": goal.state,
            "task_base": goal.task_base,
            "workspace_fingerprint": goal.workspace_fingerprint,
            "cursor": {
                "last_event_seq": event_seq,
                "open_criteria": open_criteria,
                "open_operations": open_operations,
                "next_action": str(next_action or cursor["next_action"] or ""),
            },
            "criteria": [
                {
                    "criterion_id": item.criterion_id,
                    "status": item.status,
                    "blocking": item.blocking,
                    "plan": item.verification_plan,
                }
                for item in criteria
            ],
            "operations": [
                {
                    "op_id": item.op_id,
                    "state": item.state,
                    "side_effect_state": item.side_effect_state,
                    "disposition": item.disposition,
                }
                for item in operations
            ],
            "budget": self.budget_state(goal.goal_id),
        }
        if payload:
            document["payload"] = dict(payload)
        text = (
            json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n"
        )
        base = Path(directory) if directory else self.runtime_dir() / "checkpoints" / goal.goal_id
        target = base / f"{seq}.json"
        atomic_write_text(target, text)
        digest = sha256_file(target)
        size = len(text.encode("utf-8"))
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO checkpoints(checkpoint_id, goal_id, seq, at, revision,
                    last_event_seq, path, sha256, prev, size_bytes, next_action,
                    open_criteria, open_operations)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_id,
                    goal.goal_id,
                    seq,
                    moment,
                    int(goal.revision),
                    event_seq,
                    str(target),
                    digest,
                    prev,
                    size,
                    str(next_action or ""),
                    _json(open_criteria),
                    _json(open_operations),
                ),
            )
            db.execute(
                """
                INSERT INTO resume_cursor(goal_id, last_event_seq, last_checkpoint_id,
                    open_criteria, open_operations, next_action, updated)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(goal_id) DO UPDATE SET
                    last_event_seq = excluded.last_event_seq,
                    last_checkpoint_id = excluded.last_checkpoint_id,
                    open_criteria = excluded.open_criteria,
                    open_operations = excluded.open_operations,
                    next_action = excluded.next_action,
                    updated = excluded.updated
                """,
                (
                    goal.goal_id,
                    event_seq,
                    checkpoint_id,
                    _json(open_criteria),
                    _json(open_operations),
                    str(next_action or ""),
                    moment,
                ),
            )
        return {
            "checkpoint_id": checkpoint_id,
            "goal_id": goal.goal_id,
            "seq": seq,
            "path": str(target),
            "sha256": digest,
            "prev": prev,
            "size_bytes": size,
            "oversize": size > 4096,
            "open_criteria": open_criteria,
            "open_operations": open_operations,
            "last_event_seq": event_seq,
            "at": moment,
        }

    def checkpoints(self, goal_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM checkpoints WHERE goal_id = ? ORDER BY seq", (str(goal_id),)
            ).fetchall()
        return [_checkpoint_row(row) for row in rows]

    def latest_checkpoint(self, goal_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM checkpoints WHERE goal_id = ? ORDER BY seq DESC LIMIT 1",
                (str(goal_id),),
            ).fetchone()
        return _checkpoint_row(row) if row else None

    def checkpoint_payload(self, checkpoint: Mapping[str, object]) -> dict:
        """Read a checkpoint file back and verify its sha256 (never trust the index)."""
        path = str(checkpoint.get("path") or "")
        result: dict = {"path": path, "readable": False, "sha256_ok": False, "document": None}
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            result["reason"] = f"{type(exc).__name__}: {exc}"
            return result
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        result["readable"] = True
        result["sha256_ok"] = bool(checkpoint.get("sha256")) and digest == str(
            checkpoint.get("sha256")
        )
        with contextlib.suppress(json.JSONDecodeError):
            result["document"] = json.loads(text)
        return result

    def cursor(self, goal_id: str) -> dict:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM resume_cursor WHERE goal_id = ?", (str(goal_id),)
            ).fetchone()
        if row is None:
            return {
                "goal_id": str(goal_id),
                "last_event_seq": 0,
                "last_checkpoint_id": "",
                "open_criteria": [],
                "open_operations": [],
                "next_action": "",
                "updated": 0.0,
            }
        return {
            "goal_id": str(row["goal_id"]),
            "last_event_seq": int(row["last_event_seq"]),
            "last_checkpoint_id": str(row["last_checkpoint_id"]),
            "open_criteria": _load_list(row["open_criteria"]),
            "open_operations": _load_list(row["open_operations"]),
            "next_action": str(row["next_action"]),
            "updated": float(row["updated"] or 0.0),
        }

    def set_cursor_event_seq(self, goal_id: str, seq: int, *, now: float | None = None) -> bool:
        moment = _now(now)
        with self.transaction() as db:
            cursor = db.execute(
                "UPDATE resume_cursor SET last_event_seq = ?, updated = ? WHERE goal_id = ?"
                " AND last_event_seq < ?",
                (int(seq), moment, str(goal_id), int(seq)),
            )
        return cursor.rowcount > 0

    # -- budget ------------------------------------------------------------- #

    def ledger(self, goal_id: str) -> BudgetLedger:
        return BudgetLedger(self, goal_id)

    def budget_state(self, goal_id: str, *, role: str = "") -> dict:
        query = "SELECT * FROM budget_state WHERE goal_id = ?"
        params: list = [str(goal_id)]
        if role:
            query += " AND role = ?"
            params.append(str(role))
        query += " ORDER BY role"
        with self._lock:
            rows = self._db.execute(query, tuple(params)).fetchall()
        return {
            str(row["role"]): {
                "role": str(row["role"]),
                "limit": int(row["limit_tokens"]),
                "spent": int(row["spent"]),
                "reserved": int(row["reserved"]),
                "unreserved_spent": int(row["unreserved_spent"]),
                "remaining": max(
                    0, int(row["limit_tokens"]) - int(row["spent"]) - int(row["reserved"])
                ),
                "updated": float(row["updated"] or 0.0),
            }
            for row in rows
        }

    def restore_budget_account(self, account: object, goal_id: str) -> dict:
        """The load path ``budget.py`` never had: replay the ledger into the account."""
        return self.ledger(goal_id).restore_into(account)

    # -- events / runs cross-check ------------------------------------------ #

    def events_db(self) -> Path:
        return self.runtime_dir() / "events.db"

    def max_event_seq(self, *, events_db: str | os.PathLike | None = None) -> int | None:
        """Highest committed event seq, or ``None`` when there is no events store."""
        path = Path(events_db) if events_db else self.events_db()
        if not Path(path).is_file():
            return None
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        except sqlite3.Error:  # pragma: no cover — unreadable store
            return None
        try:
            row = connection.execute("SELECT MAX(seq) FROM events").fetchone()
        except sqlite3.Error:
            return None
        finally:
            connection.close()
        return int(row[0]) if row and row[0] is not None else 0

    def record_progress(
        self,
        run_id: str,
        *,
        signals: Any = None,
        decision: Any = None,
        now: float | None = None,
    ) -> bool:
        """Persist a turn's progress signals (and the action taken) onto the run.

        §94 PROGRESS-GUARD: this is what makes a stall *durable*. A run that was told
        "change strategy, this is the same answer again" and then crashed must come
        back knowing it was told — otherwise the resumed run re-arms the identical
        nudge, spends the identical budget, and stalls in the identical place.

        Accepts a :class:`skaldir.progress_guard.ProgressSignals` and/or a
        :class:`skaldir.progress_guard.ProgressDecision`; reads tolerantly (a mapping
        works too) so the caller is never forced through one shape. Returns whether a
        row was really written — a terminal run refuses, and that is reported, not
        invented.
        """
        supervisor = self.supervisor()
        if supervisor is None:
            return False
        fields: dict[str, Any] = {}
        if decision is not None:
            signals = signals if signals is not None else _field_of(decision, "signals")
            action = _field_of(decision, "action")
            signal = _field_of(decision, "signal")
            if action:
                fields["progress_action"] = str(action)
            fields["progress_signal"] = str(signal or "")
        if signals is not None:
            wire = _wire_of(signals)
            mapping = {
                "turn": "turn",
                "no_progress_streak": "no_progress_streak",
                "no_tool_streak": "no_tool_streak",
                "last_verification": "last_verification",
                "reply_fingerprint": "reply_fingerprint",
                "tokens_used": "tokens_used",
                "token_budget": "token_budget",
            }
            for source, target in mapping.items():
                if source in wire:
                    fields[target] = wire[source]
            # the guard's own view of the last action, unless the decision named one
            if "progress_action" not in fields and wire.get("last_action"):
                fields["progress_action"] = str(wire["last_action"])
            if "progress_signal" not in fields and wire.get("last_signal"):
                fields["progress_signal"] = str(wire["last_signal"])
        if not fields:
            return False
        return bool(supervisor.update(run_id, now=now, **fields))

    def progress(self, run_id: str) -> Any:
        """Read the durable progress signals back (``None`` when the run is unknown)."""
        from .progress_guard import ProgressSignals

        supervisor = self.supervisor()
        if supervisor is None:
            return None
        run = supervisor.get(run_id)
        if run is None:
            return None
        record = run.to_dict()
        return ProgressSignals.from_dict(record)

    def supervisor(self) -> rs.RunStore | None:
        """The run store handle for this same file (invariants, continuations)."""
        if self._supervisor is None:
            with contextlib.suppress(Exception):
                self._supervisor = rs.RunStore(self.path)
        return self._supervisor

    def runs_snapshot(self) -> dict:
        supervisor = self.supervisor()
        if supervisor is None:
            return {"available": False, "runs": [], "checked": 0, "violations": [], "ok": None}
        try:
            snapshot = rs.describe(supervisor)
        except Exception as exc:  # noqa: BLE001 — a broken run table must not hide the goal
            return {
                "available": False,
                "runs": [],
                "checked": 0,
                "violations": [],
                "ok": None,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        return {
            "available": True,
            "invariant": snapshot["invariant"],
            "runs": snapshot["runs"],
            "checked": snapshot["checked"],
            "violations": snapshot["violations"],
            "protected": snapshot["protected"],
            "ok": snapshot["ok"],
        }

    # -- resume protocol ---------------------------------------------------- #

    def detect_incomplete(
        self,
        goal_id: str = "",
        *,
        now: float | None = None,
        alive: Callable[[int | None], bool] = pid_alive,
        adopt_host: bool = False,
    ) -> dict:
        """R1: the read-only pass over everything that is not finished."""
        moment = _now(now)
        goal = self.resolve_goal(goal_id=goal_id) if goal_id else None
        goals = [goal] if goal else self.goals(include_terminal=False)
        operations = self.operations(goal.goal_id) if goal else self.operations()
        open_ops = [item for item in operations if item.state in OPEN_OP_STATES]
        classification = []
        for item in open_ops:
            disposition, reason = self.classify_operation(item)
            classification.append(
                {
                    "op_id": item.op_id,
                    "kind": item.kind,
                    "target": item.target,
                    "state": item.state,
                    "side_effect_state": item.side_effect_state,
                    "idempotent": item.idempotent,
                    "attempt": item.attempt,
                    "lease_owner": item.lease_owner,
                    "disposition": disposition,
                    "reason": reason,
                }
            )
        parked = [item for item in operations if item.parked or item.state == OP_UNKNOWN]
        stale: list[dict] = []
        for record in goals:
            for criterion in self.criteria(record.goal_id):
                if criterion.status == STATUS_VERIFIED and not criterion.fresh(record):
                    stale.append(
                        {
                            "goal_id": record.goal_id,
                            "criterion_id": criterion.criterion_id,
                            "evidence_revision": criterion.evidence_revision,
                            "revision": record.revision,
                        }
                    )
        adopted: list[str] = []
        if adopt_host:
            supervisor = self.supervisor()
            if supervisor is not None:
                with contextlib.suppress(Exception):
                    adopted = [item.run_id for item in rs.adopt_host_leases(supervisor, now=moment)]
        max_seq = self.max_event_seq()
        cursor_seq = self.cursor(goal.goal_id)["last_event_seq"] if goal else 0
        delta = int(max_seq - cursor_seq) if max_seq is not None else 0
        return {
            "checked_at": moment,
            "goal_id": goal.goal_id if goal else "",
            "goal_state": goal.state if goal else "",
            "non_terminal_goals": [
                {"goal_id": item.goal_id, "state": item.state, "revision": item.revision}
                for item in goals
            ],
            "operations": {
                "open": len(open_ops),
                "by_state": _counts(item.state for item in operations),
                "classification": classification,
                "parked": [item.op_id for item in parked],
            },
            "runs": self.runs_snapshot(),
            "host_leases_adopted": adopted,
            "events": {"max_seq": max_seq, "cursor": cursor_seq, "uncommitted": delta},
            "stale_evidence": stale,
            "resumable": bool(goals) and not any(item.parked for item in open_ops),
            "blocked_reason": (
                "parked unknown operation(s) need operator confirmation: "
                + ", ".join(item.op_id for item in parked)
                if parked
                else ""
            ),
        }

    def resume(
        self,
        goal_id: str = "",
        *,
        session_id: str = "",
        owner: str = "",
        now: float | None = None,
        alive: Callable[[int | None], bool] = pid_alive,
        takeover: bool = True,
        adopt_host: bool = True,
        classify: bool = True,
        budget_account: object | None = None,
        path_exists: Callable[[str], bool] = os.path.exists,
    ) -> dict:
        """R0-R3: take ownership, classify the in-flight work, hand back the cursor.

        Returns the record to resume from *and* every write it performed, so the
        outcome can be audited instead of believed.
        """
        moment = _now(now)
        goal = self.resolve_goal(goal_id=goal_id, session_id=session_id)
        if goal is None:
            raise UnknownGoal(
                f"no goal record in {self.path}"
                + (f" for session {session_id!r}" if session_id else "")
            )
        holder = str(owner or self.owner)
        wrote: dict = {
            "lease": "",
            "resolved": [],
            "parked": [],
            "already_parked": [],
            "repeats": [],
            "idle": [],
            "demoted_criteria": [],
            "superseded_continuations": 0,
            "writer": {},
        }
        writer = self.acquire_writer(owner=holder, now=moment, alive=alive)
        wrote["writer"] = {key: writer[key] for key in ("acquired", "action", "holder", "reason")}

        # R0/R3 — ownership: re-attach to a live owner, CAS-takeover a dead one.
        owners = self._goal_ownership(
            goal,
            holder,
            moment,
            alive,
            takeover=takeover,
            allow_write=bool(writer["acquired"]),
        )
        wrote["lease"] = owners["action"]
        if goal.corrupt:  # a record we cannot classify is never resumed silently
            return {
                "goal_id": goal.goal_id,
                "corrupt": True,
                "corrupt_reason": goal.corrupt_reason,
                "resumable": False,
                "authoritative": bool(writer["acquired"]),
                "blocked_reason": goal.corrupt_reason,
                "wrote": wrote,
                "owners": owners,
            }
        if goal.terminal:
            return {
                "goal_id": goal.goal_id,
                "state": goal.state,
                "revision": goal.revision,
                "terminal": True,
                "resumable": False,
                "authoritative": bool(writer["acquired"]),
                "blocked_reason": f"the goal is frozen in {goal.state} — reopen with a new goal id",
                "wrote": wrote,
                "owners": owners,
            }

        if classify:
            # R2 — classify every in-flight operation; park what cannot be proven.
            for item in self.operations(goal.goal_id, states=OPEN_OP_STATES):
                if item.parked and item.wake_source:
                    # already waiting for its operator: re-parking would be a write
                    # without a change, and the wake source is the operator's alone
                    wrote["already_parked"].append(item.op_id)
                    continue
                disposition, reason = self.classify_operation(item, path_exists=path_exists)
                if owners["owns"]:
                    if disposition == DISPOSITION_RESOLVED:
                        outcome = self.complete_operation(
                            item.op_id,
                            result_ref=str(item.result_ref or f"op:{item.op_id}"),
                            note=f"resume classification RESOLVED: {reason}",
                            now=moment,
                        )
                        wrote["resolved"].append(outcome["op_id"])
                    elif disposition == DISPOSITION_UNKNOWN:
                        outcome = self.park_operation(
                            item.op_id, reason=reason, now=moment
                        )
                        wrote["parked"].append(outcome["op_id"])
                        wrote["superseded_continuations"] += int(
                            outcome["superseded_continuations"]
                        )
                    elif disposition == DISPOSITION_REPEAT:
                        if item.state == OP_IN_FLIGHT:
                            self.requeue_operation(
                                item.op_id, reason=f"resume classification REPEAT: {reason}",
                                now=moment,
                            )
                        wrote["repeats"].append(item.op_id)
                    else:
                        if item.state == OP_FAILED:
                            self.requeue_operation(
                                item.op_id, reason=f"resume classification IDLE: {reason}",
                                now=moment,
                            )
                        wrote["idle"].append(item.op_id)
                    self._record_disposition(item.op_id, disposition)
                else:
                    # a foreign live owner: classify read-only, change nothing
                    wrote["idle" if disposition == DISPOSITION_IDLE else
                          "repeats" if disposition == DISPOSITION_REPEAT else
                          "resolved" if disposition == DISPOSITION_RESOLVED else
                          "parked"].append(item.op_id)

        # R5.3 — a stale `verified` goes back to open; never re-certify silently.
        if owners["owns"]:
            wrote["demoted_criteria"] = self.demote_stale_criteria(goal.goal_id, now=moment)

        detected = (
            self.detect_incomplete(goal.goal_id, now=moment, alive=alive)
            if not owners["owns"]
            else self.detect_incomplete(
                goal.goal_id, now=moment, alive=alive, adopt_host=adopt_host
            )
        )
        if adopt_host and owners["owns"]:
            wrote["host_leases_adopted"] = detected["host_leases_adopted"]

        checkpoint = self.latest_checkpoint(goal.goal_id)
        checkpoint_read = self.checkpoint_payload(checkpoint) if checkpoint else None
        criteria = self.criteria(goal.goal_id)
        operations = self.operations(goal.goal_id)
        parked = [item for item in operations if item.parked]
        cursor = self.cursor(goal.goal_id)
        budget = self.budget_state(goal.goal_id)
        budget_restored = {"restored": False, "reason": "no in-memory account was supplied"}
        if budget_account is not None:
            budget_restored = self.restore_budget_account(budget_account, goal.goal_id)
        warnings: list[str] = []
        if checkpoint and checkpoint_read and not checkpoint_read.get("sha256_ok"):
            warnings.append(
                f"checkpoint {checkpoint['checkpoint_id']} does not match its recorded "
                f"sha256 — falling back to the record (never a silent partial resume)"
            )
        if detected["events"]["uncommitted"] > 0:
            warnings.append(
                f"{detected['events']['uncommitted']} event(s) after the cursor "
                f"{detected['events']['cursor']} must be replayed before new work"
            )
        blocked_reason = (
            "parked unknown operation(s) need operator confirmation: "
            + ", ".join(item.op_id for item in parked)
            if parked
            else ""
        )
        authoritative = bool(writer["acquired"]) or bool(owners["owns"])
        if not authoritative:
            warnings.append(
                "another live process owns this store's writer lease "
                f"({writer.get('holder')}): this run may read and classify, it must not continue"
            )
        return {
            "goal_id": goal.goal_id,
            "session_id": goal.session_id,
            "goal": goal.goal_text,
            "revision": int(goal.revision),
            "state": goal.state,
            "task_base": goal.task_base,
            "workspace_fingerprint": goal.workspace_fingerprint,
            "created": goal.created,
            "updated": goal.updated,
            "decisions": goal.decisions,
            "criteria": [item.to_dict() for item in criteria],
            "open_criteria": [item.criterion_id for item in criteria
                              if item.status != STATUS_VERIFIED],
            "operations": {
                "all": [item.to_dict() for item in operations],
                "open": [item.op_id for item in operations if item.state in OPEN_OP_STATES],
                "parked": [item.op_id for item in parked],
            },
            "parked_operations": [
                {
                    "op_id": item.op_id,
                    "kind": item.kind,
                    "target": item.target,
                    "side_effect_state": item.side_effect_state,
                    "wake_source": item.wake_source or OPERATOR_WAKE_SOURCE,
                    "last_error": item.last_error,
                }
                for item in parked
            ],
            "cursor": cursor,
            "checkpoint": (
                {
                    **{key: checkpoint[key] for key in
                       ("checkpoint_id", "seq", "path", "sha256", "size_bytes",
                        "last_event_seq", "at")},
                    "readable": bool(checkpoint_read and checkpoint_read["readable"]),
                    "sha256_ok": bool(checkpoint_read and checkpoint_read["sha256_ok"]),
                }
                if checkpoint
                else None
            ),
            "budget": budget,
            "budget_restored": budget_restored,
            "owners": owners,
            "detected": detected,
            "next_action": str(cursor["next_action"] or "") or "read the goal record first",
            "wrote": wrote,
            "warnings": warnings,
            "resumable": not parked,
            "authoritative": authoritative,
            "blocked_reason": blocked_reason,
        }

    # -- status ------------------------------------------------------------- #

    def status(self, *, now: float | None = None) -> dict:
        """Read-only snapshot for ``skaldir runs status``."""
        moment = _now(now)
        goals = self.goals()
        operations = self.operations()
        return {
            "store": self.path,
            "schema_version": self.schema_version(),
            "exists": Path(self.path).is_file(),
            "checked_at": moment,
            "writer": self.writer(now=moment),
            "counts": {
                "goals": len(goals),
                "open_goals": len([item for item in goals if not item.terminal]),
                "operations": len(operations),
                "parked_operations": len([item for item in operations if item.parked]),
                "checkpoints": sum(len(self.checkpoints(item.goal_id)) for item in goals),
            },
            "goals": [
                {
                    "goal_id": item.goal_id,
                    "revision": item.revision,
                    "state": item.state,
                    "session_id": item.session_id,
                    "goal": item.goal_text,
                    "updated": item.updated,
                    "open_criteria": self.open_criteria(item.goal_id),
                    "owner": item.lease_owner,
                    "corrupt": item.corrupt,
                }
                for item in goals
            ],
            "operations": {
                "by_state": _counts(item.state for item in operations),
                "by_disposition": _counts(
                    item.disposition for item in operations if item.disposition
                ),
                "parked": [
                    {
                        "op_id": item.op_id,
                        "goal_id": item.goal_id,
                        "kind": item.kind,
                        "target": item.target,
                        "wake_source": item.wake_source,
                        "last_error": item.last_error,
                    }
                    for item in operations
                    if item.parked
                ],
            },
            "runs": self.runs_snapshot(),
            "budget": {
                record.goal_id: self.budget_state(record.goal_id) for record in goals
            },
        }

    # -- internals ---------------------------------------------------------- #

    def _bump_revision_locked(
        self, db: sqlite3.Connection, goal: Goal, *, reason: str, now: float
    ) -> int:
        """A goal/criteria edit bumps the revision and ages every criterion's evidence."""
        revision = int(goal.revision) + 1
        log = list(goal.revision_log)
        log.append(
            {
                "revision": revision,
                "at": now,
                "old": int(goal.revision),
                "new": revision,
                "reason": str(reason),
            }
        )
        db.execute(
            "UPDATE goals SET revision = ?, revision_log = ?, updated = ? WHERE goal_id = ?",
            (revision, _json(log), now, goal.goal_id),
        )
        # evidence is invalidated by mismatch, never by deletion (MVP/10 §2.4)
        db.execute(
            "UPDATE criteria SET verified_at = 0, verified_by = '' WHERE goal_id = ?",
            (goal.goal_id,),
        )
        return revision

    def _goal_ownership(
        self,
        goal: Goal,
        holder: str,
        moment: float,
        alive: Callable[[int | None], bool],
        *,
        takeover: bool,
        allow_write: bool = True,
    ) -> dict:
        """Re-attach to a live owner or CAS-takeover a dead one (MVP/10 R3)."""
        current = str(goal.lease_owner or "")
        expires = float(goal.lease_expires_at or 0.0)
        live = bool(current) and expires > moment and alive(owner_pid(current))
        owners = {
            "goal_id": goal.goal_id,
            "holder": current,
            "holder_pid": owner_pid(current),
            "holder_alive": bool(current) and alive(owner_pid(current)),
            "lease_expires_at": expires,
            "expired": bool(current) and expires <= moment,
            "requester": holder,
            "action": "none",
            "owns": False,
        }
        if not allow_write:
            # the store's single-writer lease is held by a live foreign process:
            # this run may read and classify, it may not write (never a second writer)
            owners.update(action="refused-writer", owns=False)
            return owners
        if current == holder:
            with self.transaction() as db:
                db.execute(
                    "UPDATE goals SET lease_expires_at = ?, heartbeat_at = ?, updated = ?"
                    " WHERE goal_id = ?",
                    (moment + self.lease_ttl, moment, moment, goal.goal_id),
                )
            owners.update(action="heartbeat", owns=True, holder=holder)
            return owners
        if live:
            # a healthy foreign owner keeps ownership: heartbeat only, never a
            # second worker (MVP/10 R3)
            owners.update(action="re-attached" if takeover else "refused", owns=False)
            return owners
        if not takeover:
            owners.update(action="refused", owns=False)
            return owners
        with self.transaction() as db:
            cursor = db.execute(
                "UPDATE goals SET lease_owner = ?, lease_expires_at = ?, heartbeat_at = ?,"
                " updated = ? WHERE goal_id = ? AND lease_owner = ?",
                (holder, moment + self.lease_ttl, moment, moment, goal.goal_id, current),
            )
        if cursor.rowcount <= 0:
            fresh = self.goal(goal.goal_id)
            owners.update(
                action="takeover-lost",
                owns=False,
                holder=str(fresh.lease_owner) if fresh else current,
            )
            return owners
        owners.update(action="takeover", owns=True, holder=holder)
        self._append_goal_history(
            goal, "lease_takeover", {"previous": current, "holder": holder}
        )
        return owners

    def _record_disposition(self, op_id: str, disposition: str) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE operations SET disposition = ? WHERE op_id = ?",
                (str(disposition), str(op_id)),
            )

    def _supersede_continuations(
        self, run_id: str, *, reason: str, now: float
    ) -> int:
        """R3: a parked unknown supersedes the run's open continuations first."""
        if not str(run_id or "").strip():
            return 0
        supervisor = self.supervisor()
        if supervisor is None:
            return 0
        try:
            return int(
                supervisor.supersede_open_continuations(run_id, reason=reason, now=now) or 0
            )
        except Exception:  # noqa: BLE001 — a missing run row must not hide the park
            return 0

    def _append_goal_history(self, goal: Goal, kind: str, payload: Mapping[str, object]) -> None:
        """Append-only JSONL companion (the record stays the authority)."""
        path = Path(goal.history_ref or (self.runtime_dir() / "goals" / f"{goal.goal_id}.jsonl"))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "ts": _now(None),
                            "goal_id": goal.goal_id,
                            "revision": goal.revision,
                            "kind": str(kind),
                            **{str(key): value for key, value in payload.items()},
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                    + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            # evidence only: a full disk must not fail the mutation itself
            pass

    def _mirror_action(self, op_id: str, *, idempotent: bool, note: str = "") -> None:
        """Project the op into the existing ``run_actions`` registry (best effort)."""
        supervisor = self.supervisor()
        if supervisor is None:
            return
        with contextlib.suppress(Exception):
            supervisor.register_action(f"op:{op_id}", idempotent=idempotent, note=note)

    def _mirror_verified(self, op_id: str) -> None:
        supervisor = self.supervisor()
        if supervisor is None:
            return
        with contextlib.suppress(Exception):
            supervisor.verify_result(f"op:{op_id}", exists=True)

    def _action_info(self, op_id: str) -> dict | None:
        supervisor = self.supervisor()
        if supervisor is None:
            return None
        with contextlib.suppress(Exception):
            return supervisor.action_info(f"op:{op_id}")
        return None


# --------------------------------------------------------------------------- #
# budget ledger                                                                 #
# --------------------------------------------------------------------------- #


class BudgetLedger:
    """Durable ``spent``/``reserved``/``remaining`` per role — the load path.

    Every mutation writes its ledger row **and** the ``budget_state`` row inside
    one transaction (MVP/10 §2.3), so the record's budget block is a projection
    of durable state and a restart cannot reset the account. The refusal rules
    mirror :mod:`skaldir.budget` (over-tap needs a reservation; reservation-less
    spend has a floor and a cumulative cap) — same exception type, one place to
    read them.
    """

    def __init__(
        self,
        store: RunsStore,
        goal_id: str,
        *,
        limits: Mapping[str, int] | None = None,
        small_spend_floor: int = 200,
        unreserved_cap_fraction: float = budget_module.DEFAULT_UNRESERVED_FRACTION,
    ) -> None:
        self.store = store
        self.goal_id = str(goal_id)
        self._small_floor = int(small_spend_floor)
        self._cap_fraction = float(unreserved_cap_fraction)
        if limits:
            self.ensure_limits(limits)

    # -- setup -------------------------------------------------------------- #

    def ensure_limits(self, limits: Mapping[str, int]) -> dict:
        moment = _now(None)
        with self.store.transaction() as db:
            for role, limit in _limit_items(limits):
                db.execute(
                    """
                    INSERT INTO budget_state(goal_id, role, limit_tokens, spent, reserved,
                        unreserved_spent, updated)
                    VALUES (?, ?, ?, 0, 0, 0, ?)
                    ON CONFLICT(goal_id, role) DO UPDATE SET
                        limit_tokens = excluded.limit_tokens, updated = excluded.updated
                    """,
                    (self.goal_id, role, int(limit), moment),
                )
        return self.state()

    # -- mutations ---------------------------------------------------------- #

    def reserve(
        self, role: str, amount: int, *, reservation_id: str = "", note: str = ""
    ) -> dict:
        amount = int(amount)
        moment = _now(None)
        rid = str(reservation_id) or f"res-{uuid.uuid4().hex[:10]}-{role}"
        with self.store.transaction() as db:
            row = self._state_row(db, role)
            if int(row["reserved"]) + amount > int(row["limit_tokens"]) or int(
                row["spent"]
            ) + amount > int(row["limit_tokens"]):
                raise budget_module.BudgetExceeded(
                    f"role '{role}' budget exceeded while reserving: "
                    f"{int(row['reserved']) + amount} > {int(row['limit_tokens'])} tokens"
                )
            db.execute(
                "UPDATE budget_state SET reserved = reserved + ?, updated = ?"
                " WHERE goal_id = ? AND role = ?",
                (amount, moment, self.goal_id, role),
            )
            db.execute(
                "INSERT INTO budget_reservations(goal_id, reservation_id, role, amount, created)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(goal_id, reservation_id) DO UPDATE SET"
                " amount = amount + excluded.amount",
                (self.goal_id, rid, role, amount, moment),
            )
            self._ledger_row(db, role, "reserve", amount, rid, note, moment)
        return {"reservation_id": rid, "role": role, "amount": amount, "state": self.state()}

    def release(self, reservation_id: str, *, note: str = "") -> bool:
        return self._close(reservation_id, action="release", note=note)

    def rollback(self, reservation_id: str) -> bool:
        return self._close(reservation_id, action="rollback", note="unused reservation")

    def spend(
        self, role: str, amount: int, *, reservation: str = "", note: str = ""
    ) -> dict:
        amount = int(amount)
        moment = _now(None)
        rid = str(reservation)
        with self.store.transaction() as db:
            row = self._state_row(db, role)
            held: int | None = None
            if rid:
                held_row = db.execute(
                    "SELECT amount FROM budget_reservations"
                    " WHERE goal_id = ? AND reservation_id = ?",
                    (self.goal_id, rid),
                ).fetchone()
                if held_row is None:
                    raise budget_module.BudgetExceeded(
                        f"role '{role}': unknown reservation id {rid!r} — reserve first"
                    )
                held = int(held_row["amount"])
                if amount > held:
                    raise budget_module.BudgetExceeded(
                        f"role '{role}': spend of {amount} exceeds the remaining hold of "
                        f"{held} on reservation {rid!r} (reserve the difference first)"
                    )
                db.execute(
                    "UPDATE budget_state SET reserved = MAX(0, reserved - ?), updated = ?"
                    " WHERE goal_id = ? AND role = ?",
                    (amount, moment, self.goal_id, role),
                )
                db.execute(
                    "UPDATE budget_reservations SET amount = amount - ?"
                    " WHERE goal_id = ? AND reservation_id = ?",
                    (amount, self.goal_id, rid),
                )
            else:
                if amount > self._small_floor:
                    raise budget_module.BudgetExceeded(
                        f"role '{role}': spend of {amount} needs a reservation "
                        f"(floor for reservation-less spend: {self._small_floor})"
                    )
                cap = int(int(row["limit_tokens"]) * self._cap_fraction)
                if int(row["unreserved_spent"]) + amount > cap:
                    raise budget_module.BudgetExceeded(
                        f"role '{role}': cumulative reservation-less spend "
                        f"{int(row['unreserved_spent']) + amount} exceeds the unreserved cap "
                        f"{cap}; reserve capacity instead"
                    )
                if int(row["spent"]) + int(row["reserved"]) + amount > int(row["limit_tokens"]):
                    raise budget_module.BudgetExceeded(
                        f"role '{role}' budget exceeded: "
                        f"{int(row['spent']) + int(row['reserved']) + amount} > "
                        f"{int(row['limit_tokens'])} tokens"
                    )
                db.execute(
                    "UPDATE budget_state SET unreserved_spent = unreserved_spent + ?,"
                    " updated = ? WHERE goal_id = ? AND role = ?",
                    (amount, moment, self.goal_id, role),
                )
            db.execute(
                "UPDATE budget_state SET spent = spent + ? WHERE goal_id = ? AND role = ?",
                (amount, self.goal_id, role),
            )
            self._ledger_row(db, role, "spend", amount, rid, note, moment)
        return {"role": role, "amount": amount, "reservation": rid, "state": self.state()}

    # -- reads -------------------------------------------------------------- #

    def state(self) -> dict:
        return self.store.budget_state(self.goal_id)

    def remaining(self, role: str) -> int:
        return int(self.state().get(str(role), {}).get("remaining", 0))

    def spent(self, role: str) -> int:
        return int(self.state().get(str(role), {}).get("spent", 0))

    def reserved(self, role: str) -> int:
        return int(self.state().get(str(role), {}).get("reserved", 0))

    def entries(self, *, role: str = "", limit: int = 500) -> list[dict]:
        query = "SELECT * FROM budget_ledger WHERE goal_id = ?"
        params: list = [self.goal_id]
        if role:
            query += " AND role = ?"
            params.append(str(role))
        query += " ORDER BY id LIMIT ?"
        params.append(int(limit))
        with self.store._lock:  # noqa: SLF001 — same store, one connection
            rows = self.store._db.execute(query, tuple(params)).fetchall()  # noqa: SLF001
        return [dict(row) for row in rows]

    def reservations(self) -> dict[str, dict]:
        with self.store._lock:  # noqa: SLF001
            rows = self.store._db.execute(  # noqa: SLF001
                "SELECT * FROM budget_reservations WHERE goal_id = ?", (self.goal_id,)
            ).fetchall()
        return {
            str(row["reservation_id"]): {
                "role": str(row["role"]),
                "amount": int(row["amount"]),
                "created": float(row["created"] or 0.0),
            }
            for row in rows
        }

    # -- the missing load path ---------------------------------------------- #

    def restore_into(self, account: object) -> dict:
        """Replay the durable ledger into a live ``budget.BudgetAccount``.

        This is the load path MVP/10 gap 5 names: after a restart the in-memory
        account is empty, and 'empty' silently meant 'unbounded'. Replaying the
        recorded ``reserve``/``release``/``rollback``/``spend`` sequence through
        the account's own public API rebuilds ``spent``/``reserved`` exactly; a
        replay that the account refuses (e.g. a lowered limit) is reported as
        ``restored: false`` with the reason — never as a silent success.
        """
        entries = self.entries(limit=100000)
        applied = 0
        try:
            for entry in entries:
                action = str(entry["action"])
                role = str(entry["role"])
                amount = int(entry["amount"])
                rid = str(entry["reservation_id"])
                if action == "reserve":
                    account.reserve(role, amount, reservation_id=rid)  # type: ignore[attr-defined]
                elif action in ("release", "rollback"):
                    account.release_reservation(rid)  # type: ignore[attr-defined]
                elif action == "spend":
                    account.spend(  # type: ignore[attr-defined]
                        role, amount, reservation=rid, note=str(entry["note"])
                    )
                else:
                    return {
                        "restored": False,
                        "reason": f"unknown ledger action {action!r} at id {entry['id']}",
                        "applied": applied,
                    }
                applied += 1
        except Exception as exc:  # noqa: BLE001 — a refusal is the honest answer
            return {
                "restored": False,
                "reason": f"{type(exc).__name__}: {exc}",
                "applied": applied,
                "entries": len(entries),
            }
        durable = self.state()
        matches: dict[str, bool] = {}
        for role, row in durable.items():
            matches[role] = (
                int(account.spent(role)) == int(row["spent"])  # type: ignore[attr-defined]
                and int(account.reserved(role)) == int(row["reserved"])  # type: ignore[attr-defined]
            )
        return {
            "restored": all(matches.values()) if matches else True,
            "applied": applied,
            "entries": len(entries),
            "matches": matches,
            "spent": {role: int(account.spent(role)) for role in durable},  # type: ignore[attr-defined]
            "reserved": {role: int(account.reserved(role)) for role in durable},  # type: ignore[attr-defined]
            "durable": durable,
        }

    # -- internals ---------------------------------------------------------- #

    def _state_row(self, db: sqlite3.Connection, role: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM budget_state WHERE goal_id = ? AND role = ?",
            (self.goal_id, str(role)),
        ).fetchone()
        if row is None:
            raise budget_module.BudgetError(
                f"role {role!r} has no budget row on {self.goal_id} "
                f"(known: {sorted(self.state()) or 'none'})"
            )
        return row

    def _ledger_row(
        self,
        db: sqlite3.Connection,
        role: str,
        action: str,
        amount: int,
        reservation_id: str,
        note: str,
        moment: float,
    ) -> None:
        db.execute(
            "INSERT INTO budget_ledger(goal_id, ts, role, action, amount, reservation_id, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.goal_id, moment, str(role), str(action), int(amount), str(reservation_id),
             str(note)),
        )

    def _close(self, reservation_id: str, *, action: str, note: str) -> bool:
        moment = _now(None)
        rid = str(reservation_id)
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT * FROM budget_reservations WHERE goal_id = ? AND reservation_id = ?",
                (self.goal_id, rid),
            ).fetchone()
            if row is None:
                return False
            db.execute(
                "UPDATE budget_state SET reserved = MAX(0, reserved - ?), updated = ?"
                " WHERE goal_id = ? AND role = ?",
                (int(row["amount"]), moment, self.goal_id, str(row["role"])),
            )
            db.execute(
                "DELETE FROM budget_reservations WHERE goal_id = ? AND reservation_id = ?",
                (self.goal_id, rid),
            )
            self._ledger_row(
                db, str(row["role"]), action, int(row["amount"]), rid, note, moment
            )
        return True


# --------------------------------------------------------------------------- #
# row → record                                                                  #
# --------------------------------------------------------------------------- #


def _goal_from_row(row: sqlite3.Row | Mapping[str, object]) -> Goal:
    data = dict(row)
    state = str(data.get("state") or "")
    corrupt = state not in GOAL_STATES
    goal = Goal(
        goal_id=str(data.get("goal_id") or ""),
        revision=int(data.get("revision") or 0),
        session_id=str(data.get("session_id") or ""),
        state=WORKING if corrupt else state,
        goal_text=str(data.get("goal_text") or ""),
        task_base=str(data.get("task_base") or ""),
        workspace_fingerprint=str(data.get("workspace_fingerprint") or ""),
        created=float(data.get("created") or 0.0),
        updated=float(data.get("updated") or 0.0),
        revision_log=_load_list(data.get("revision_log")),
        decisions=_load_list(data.get("decisions")),
        last_failure=str(data.get("last_failure") or ""),
        lease_owner=str(data.get("lease_owner") or ""),
        lease_expires_at=float(data.get("lease_expires_at") or 0.0),
        heartbeat_at=float(data.get("heartbeat_at") or 0.0),
        blocked_reason=str(data.get("blocked_reason") or ""),
        wake_source=str(data.get("wake_source") or ""),
        schema_version=int(data.get("schema_version") or SCHEMA_VERSION),
        history_ref=str(data.get("history_ref") or ""),
        corrupt=corrupt,
        corrupt_reason=(
            f"goal {data.get('goal_id')} carries unknown state {state!r} — the record "
            "cannot be classified and is never resumed silently"
            if corrupt
            else ""
        ),
    )
    return goal


def _criterion_from_row(row: sqlite3.Row | Mapping[str, object], evidence: list[dict]) -> Criterion:
    data = dict(row)
    return Criterion(
        criterion_id=str(data.get("criterion_id") or ""),
        text=str(data.get("text") or ""),
        blocking=bool(data.get("blocking")),
        verification_plan=str(data.get("verification_plan") or ""),
        status=str(data.get("status") or STATUS_OPEN),
        evidence_revision=int(data.get("evidence_revision") or 0),
        evidence_workspace_fingerprint=str(data.get("evidence_workspace_fingerprint") or ""),
        verified_at=float(data.get("verified_at") or 0.0),
        verified_by=str(data.get("verified_by") or ""),
        last_failure=str(data.get("last_failure") or ""),
        evidence_refs=evidence,
    )


def _operation_from_row(row: sqlite3.Row | Mapping[str, object]) -> Operation:
    data = dict(row)
    return Operation(
        op_id=str(data.get("op_id") or ""),
        goal_id=str(data.get("goal_id") or ""),
        idempotency_key=str(data.get("idempotency_key") or ""),
        kind=str(data.get("kind") or ""),
        target=str(data.get("target") or ""),
        payload_hash=str(data.get("payload_hash") or ""),
        state=str(data.get("state") or OP_PENDING),
        side_effect_state=str(data.get("side_effect_state") or SIDE_EFFECT_NONE),
        disposition=str(data.get("disposition") or ""),
        attempt=int(data.get("attempt") or 0),
        idempotent=bool(data.get("idempotent")),
        lease_owner=str(data.get("lease_owner") or ""),
        lease_expires_at=float(data.get("lease_expires_at") or 0.0),
        heartbeat_at=float(data.get("heartbeat_at") or 0.0),
        started_at=float(data.get("started_at") or 0.0),
        finished_at=float(data.get("finished_at") or 0.0),
        result_ref=str(data.get("result_ref") or ""),
        last_error=str(data.get("last_error") or ""),
        wake_source=str(data.get("wake_source") or ""),
        revision=int(data.get("revision") or 0),
        run_id=str(data.get("run_id") or ""),
        created=float(data.get("created") or 0.0),
    )


def _empty_operation(op_id: str, goal_id: str) -> Operation:  # pragma: no cover
    return Operation(op_id=op_id, goal_id=goal_id)


def _checkpoint_row(row: sqlite3.Row | Mapping[str, object]) -> dict:
    data = dict(row)
    return {
        "checkpoint_id": str(data.get("checkpoint_id") or ""),
        "goal_id": str(data.get("goal_id") or ""),
        "seq": int(data.get("seq") or 0),
        "at": float(data.get("at") or 0.0),
        "revision": int(data.get("revision") or 0),
        "last_event_seq": int(data.get("last_event_seq") or 0),
        "path": str(data.get("path") or ""),
        "sha256": str(data.get("sha256") or ""),
        "prev": str(data.get("prev") or ""),
        "size_bytes": int(data.get("size_bytes") or 0),
        "next_action": str(data.get("next_action") or ""),
        "open_criteria": _load_list(data.get("open_criteria")),
        "open_operations": _load_list(data.get("open_operations")),
    }


def _counts(values: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        key = str(value or "")
        out[key] = out.get(key, 0) + 1
    return out


def _limit_items(limits: Mapping[str, int] | None) -> list[tuple[str, int]]:
    source = dict(budget_module.DEFAULT_LIMITS if limits is None else limits)
    return [(str(role), int(limit)) for role, limit in source.items()]


def _criterion_spec(item: object, position: int) -> dict:
    """Accept ``"text"`` or ``{text, criterion_id, blocking, verification_plan}``."""
    if isinstance(item, Mapping):
        text = str(item.get("text") or "").strip()
        identifier = str(item.get("criterion_id") or item.get("id") or f"c{position + 1}")
        return {
            "criterion_id": identifier,
            "text": text,
            "blocking": bool(item.get("blocking", True)),
            "verification_plan": str(item.get("verification_plan") or item.get("plan") or ""),
        }
    return {
        "criterion_id": f"c{position + 1}",
        "text": str(item).strip(),
        "blocking": True,
        "verification_plan": "",
    }


# --------------------------------------------------------------------------- #
# convenience                                                                   #
# --------------------------------------------------------------------------- #


def open_store(
    runtime: str | os.PathLike | None = None,
    *,
    owner: str = "",
    path: str | os.PathLike | None = None,
) -> RunsStore:
    """Open (creating if needed) ``<runtime>/runs.db``."""
    target = Path(path) if path else run_store_path(runtime or default_runtime_dir())
    return RunsStore(target, owner=owner)


__all__ = [
    "CRITERION_STATUSES",
    "BudgetLedger",
    "CorruptStore",
    "Criterion",
    "DISPOSITION_IDLE",
    "DISPOSITION_REPEAT",
    "DISPOSITION_RESOLVED",
    "DISPOSITION_UNKNOWN",
    "EvidenceRequired",
    "GOAL_STATES",
    "Goal",
    "IdempotencyConflict",
    "OPEN_OP_STATES",
    "OP_DONE",
    "OP_FAILED",
    "OP_IN_FLIGHT",
    "OP_PENDING",
    "OP_STATES",
    "OP_UNKNOWN",
    "OPERATOR_WAKE_SOURCE",
    "Operation",
    "RunsError",
    "RunsStore",
    "SCHEMA_VERSION",
    "SIDE_EFFECT_KNOWN",
    "SIDE_EFFECT_NONE",
    "SIDE_EFFECT_UNKNOWN",
    "STATUS_FAILED",
    "STATUS_OPEN",
    "STATUS_VERIFIED",
    "StaleEvidence",
    "TERMINAL_GOAL_STATES",
    "UnknownGoal",
    "WriterConflict",
    "atomic_write_text",
    "boot_id",
    "evidence_kind",
    "idempotency_key",
    "open_store",
    "owner_pid",
    "owner_string",
    "payload_hash",
    "pid_alive",
    "run_store_path",
]
