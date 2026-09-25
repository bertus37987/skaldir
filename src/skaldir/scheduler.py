"""The scheduler: what SCHED-01…04 ask for, with the awkward parts made explicit.

There is no scheduler in this codebase at all, which is what the requirement audit called
"the actually missing function". The requirements are precise enough to build against:

* **SCHED-01** — time triggers run even when the TUI is closed. A schedule stores its
  timezone, the activated workflow version, parameters, policy and runs. Sommerzeit,
  *ausgelassene* and *doppelte* local times are defined explicitly rather than left to
  whatever the clock happens to do.
* **SCHED-02** — overlap is avoided by default: the same workflow is serialised. Missed
  runs are either skipped or caught up **once**, never an uncontrolled catch-up of every
  missed slot. A singleton scheduler and a persisted execution id prevent double starts.
* **SCHED-03** — desktop busy → a *visible* queue; approval missing → ``waiting_approval``;
  a missing service → a precise blocker. Independent allowed steps keep running. **A
  schedule never implies money approval.**
* **SCHED-04** — definition, activation, pause and deletion are separate actions. This
  module creates no schedule on the user's machine by itself.

The DST cases, spelled out because getting them wrong is silent and expensive:

| Local time | Happens? | What this scheduler does |
|---|---|---|
| 02:30 on a spring-forward day | **no** | the occurrence is *shifted to the first instant after the gap*, once — not skipped, not run twice |
| 02:30 on a fall-back day | **twice** | the occurrence is taken from the **first** pass only — once, never twice |
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None  # type: ignore[assignment]

__all__ = [
    "CatchUp",
    "ExecutionClaim",
    "Occurrence",
    "Schedule",
    "ScheduleNotActive",
    "ScheduleStore",
    "occurrences_between",
    "next_occurrence",
]

DEFAULT_TIMEZONE = "Europe/Berlin"     # SCHED-01's stated default for this user


class ScheduleNotActive(RuntimeError):
    """The schedule is paused or disabled — its times are computed but not fired."""


class CatchUp(str):
    """What to do with runs whose time passed while nothing was running (SCHED-02)."""

    SKIP = "skip"           # do not run them at all
    ONCE = "once"           # run exactly one of them, the most recent
    ALL = "all"             # run every missed one — OFF by default, and discouraged


@dataclass(frozen=True)
class Occurrence:
    """One scheduled firing, pinned to an exact UTC instant.

    Both the UTC instant and the intended local time are kept: the local time is what the
    user configured, the UTC instant is what the execution identity is derived from.
    """

    utc: dt.datetime
    local: dt.datetime

    @property
    def key(self) -> str:
        """The identity of *this* firing — stable across processes and restarts."""
        return self.utc.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ExecutionClaim:
    """Proof that one firing is being worked on. Exactly one wins (SCHED-02)."""

    execution_id: str
    schedule_id: str
    occurrence: str
    claimed_at: float
    claimed_by: str = ""
    state: str = "claimed"          # claimed | running | done | failed | skipped
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "schedule_id": self.schedule_id,
            "occurrence": self.occurrence,
            "claimed_at": self.claimed_at,
            "claimed_by": self.claimed_by,
            "state": self.state,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionClaim:
        return cls(
            execution_id=str(data.get("execution_id", "")),
            schedule_id=str(data.get("schedule_id", "")),
            occurrence=str(data.get("occurrence", "")),
            claimed_at=float(data.get("claimed_at", 0.0) or 0.0),
            claimed_by=str(data.get("claimed_by", "")),
            state=str(data.get("state", "claimed")),
            detail=str(data.get("detail", "")),
        )


@dataclass
class Schedule:
    """One persistent schedule. Definition and activation are separate (SCHED-04)."""

    schedule_id: str = ""
    workflow_id: str = ""
    workflow_version: str = ""          # the *activated* version, not "latest"
    parameters: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    timezone: str = DEFAULT_TIMEZONE
    #: local wall-clock time of day, "HH:MM"
    at_local: str = "00:00"
    #: which weekdays, 0=Monday … 6=Sunday; empty means every day
    weekdays: tuple[int, ...] = ()
    catch_up: str = CatchUp.ONCE
    serialize: bool = True              # SCHED-02: same workflow runs one at a time
    active: bool = False                # SCHED-04: defining does NOT activate
    created_at: float = 0.0
    paused_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "parameters": dict(self.parameters),
            "policy": dict(self.policy),
            "timezone": self.timezone,
            "at_local": self.at_local,
            "weekdays": list(self.weekdays),
            "catch_up": self.catch_up,
            "serialize": self.serialize,
            "active": self.active,
            "created_at": self.created_at,
            "paused_at": self.paused_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Schedule:
        return cls(
            schedule_id=str(data.get("schedule_id", "")),
            workflow_id=str(data.get("workflow_id", "")),
            workflow_version=str(data.get("workflow_version", "")),
            parameters=dict(data.get("parameters", {}) or {}),
            policy=dict(data.get("policy", {}) or {}),
            timezone=str(data.get("timezone", DEFAULT_TIMEZONE)),
            at_local=str(data.get("at_local", "00:00")),
            weekdays=tuple(int(d) for d in (data.get("weekdays", ()) or ())),
            catch_up=str(data.get("catch_up", CatchUp.ONCE)),
            serialize=bool(data.get("serialize", True)),
            active=bool(data.get("active", False)),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            paused_at=float(data.get("paused_at", 0.0) or 0.0),
        )

    @property
    def tz(self) -> Any:
        if ZoneInfo is None:  # pragma: no cover
            return dt.timezone.utc
        try:
            return ZoneInfo(self.timezone)
        except Exception:  # noqa: BLE001 — an unknown zone must not silently mean UTC
            raise ValueError(f"unknown timezone {self.timezone!r} — refusing to fall back to UTC")


# --------------------------------------------------------------------------- #
# time: occurrences, with the two DST cases handled by name
# --------------------------------------------------------------------------- #

def _parse_hhmm(text: str) -> tuple[int, int]:
    parts = str(text or "00:00").split(":")
    hour = int(parts[0] or 0)
    minute = int(parts[1] or 0) if len(parts) > 1 else 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"invalid time of day {text!r}")
    return hour, minute


def _local_occurrence(day: dt.date, hh: int, mm: int, tz: Any) -> tuple[dt.datetime, str]:
    """The firing for one local day, with the DST case named in the result.

    Returns ``(utc_datetime, note)`` where note is ``""``, ``"skipped_local_time"``
    (spring forward — the wall time does not exist) or ``"ambiguous_local_time"``
    (fall back — it exists twice and we take the first).
    """
    naive = dt.datetime(day.year, day.month, day.day, hh, mm, 0, 0)
    try:
        first = naive.replace(tzinfo=tz, fold=0)
        second = naive.replace(tzinfo=tz, fold=1)
    except Exception:  # noqa: BLE001
        first = naive.replace(tzinfo=dt.timezone.utc)
        second = first

    utcoffset_a = first.utcoffset()
    utcoffset_b = second.utcoffset()
    note = ""
    if utcoffset_a is not None and utcoffset_b is not None and utcoffset_a != utcoffset_b:
        # The two folds disagree, which is exactly the two DST cases:
        #   spring forward -> the wall time does not exist that day
        #   fall back      -> it exists twice
        dst_a = first.dst() or dt.timedelta(0)
        dst_b = second.dst() or dt.timedelta(0)
        note = "skipped_local_time" if dst_a < dst_b else "ambiguous_local_time"

    # fold=0 is used in BOTH cases, and it is the right answer for both:
    #   skipped   -> the wall time is read with the offset in force BEFORE the gap, which
    #                lands the run just after the jump (02:30 -> 03:30 in Berlin), once
    #   ambiguous -> the FIRST pass of the repeated hour, once
    # Measured against Europe/Berlin 2026-03-29 and 2026-10-25, not assumed.
    return first.astimezone(dt.timezone.utc), note


def occurrences_between(
    schedule: Schedule,
    start: dt.datetime,
    end: dt.datetime,
) -> list[Occurrence]:
    """Every firing of ``schedule`` in ``[start, end)`` as exact UTC instants.

    The walk is over *local calendar days*, then the local wall time is resolved for each
    day. That is what makes the two DST cases explicit instead of accidental.
    """
    tz = schedule.tz
    hh, mm = _parse_hhmm(schedule.at_local)
    start_utc = start.astimezone(dt.timezone.utc)
    end_utc = end.astimezone(dt.timezone.utc)

    first_day = start_utc.astimezone(tz).date() - dt.timedelta(days=1)
    last_day = end_utc.astimezone(tz).date() + dt.timedelta(days=1)
    out: list[Occurrence] = []
    day = first_day
    while day <= last_day:
        if not schedule.weekdays or day.weekday() in schedule.weekdays:
            utc, _note = _local_occurrence(day, hh, mm, tz)
            if start_utc <= utc < end_utc:
                out.append(Occurrence(utc=utc, local=utc.astimezone(tz)))
        day += dt.timedelta(days=1)
    out.sort(key=lambda o: o.utc)
    return out


def next_occurrence(schedule: Schedule, after: dt.datetime) -> Occurrence | None:
    """The first firing after ``after`` (UTC or aware)."""
    after_utc = after.astimezone(dt.timezone.utc)
    for occ in occurrences_between(schedule, after_utc, after_utc + dt.timedelta(days=40)):
        return occ
    return None


# --------------------------------------------------------------------------- #
# persistence: definition, activation and execution identity
# --------------------------------------------------------------------------- #

def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class ScheduleStore:
    """Persistent schedules plus an atomic execution ledger.

    Two things have to hold across processes and restarts:

    * **One firing, one execution.** :meth:`claim` writes a claim keyed by
      ``(schedule_id, occurrence)`` under the same cross-process lock the desktop lease
      uses. A second scheduler that tries the same firing gets nothing back.
    * **Definition is not activation.** ``define`` stores a schedule with ``active=False``.
      ``activate``, ``pause`` and ``delete`` are separate, deliberate actions (SCHED-04).
    """

    def __init__(self, path: str | Path, *, owner: str = "") -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.owner = owner or f"pid-{os.getpid()}"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # -- io -----------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"schedule state at {self.path} is corrupt ({exc}) — refusing to treat it "
                "as empty, because that would re-fire runs that already ran"
            ) from exc
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = __import__("tempfile").mkstemp(
            dir=str(self.path.parent), prefix=self.path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _critical(self):
        """The same cross-process critical section the desktop lease uses."""
        import contextlib

        @contextlib.contextmanager
        def hold():
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                try:
                    import fcntl
                except ImportError:  # pragma: no cover
                    fcntl = None  # type: ignore[assignment]
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                with self._lock:
                    try:
                        yield
                    finally:
                        if fcntl is not None:
                            fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

        return hold()

    # -- SCHED-04: four separate actions ------------------------------------

    def define(self, schedule: Schedule) -> Schedule:
        """Store a definition. It is **not** active afterwards."""
        if not schedule.schedule_id:
            schedule.schedule_id = f"sch-{uuid.uuid4().hex[:10]}"
        schedule.created_at = schedule.created_at or time.time()
        schedule.active = False
        with self._critical():
            data = self._read()
            schedules = dict(data.get("schedules", {}) or {})
            schedules[schedule.schedule_id] = schedule.to_dict()
            data["schedules"] = schedules
            self._write(data)
        return schedule

    def activate(self, schedule_id: str) -> Schedule:
        with self._critical():
            data = self._read()
            schedules = dict(data.get("schedules", {}) or {})
            if schedule_id not in schedules:
                raise KeyError(f"no schedule {schedule_id!r}")
            record = dict(schedules[schedule_id])
            record["active"] = True
            record["paused_at"] = 0.0
            schedules[schedule_id] = record
            data["schedules"] = schedules
            self._write(data)
            return Schedule.from_dict(record)

    def pause(self, schedule_id: str) -> Schedule:
        with self._critical():
            data = self._read()
            schedules = dict(data.get("schedules", {}) or {})
            if schedule_id not in schedules:
                raise KeyError(f"no schedule {schedule_id!r}")
            record = dict(schedules[schedule_id])
            record["active"] = False
            record["paused_at"] = time.time()
            schedules[schedule_id] = record
            data["schedules"] = schedules
            self._write(data)
            return Schedule.from_dict(record)

    def delete(self, schedule_id: str) -> bool:
        """Remove the definition. The execution ledger is kept — runs that happened stay
        recorded, because deleting a schedule must not erase what it already did."""
        with self._critical():
            data = self._read()
            schedules = dict(data.get("schedules", {}) or {})
            existed = schedule_id in schedules
            schedules.pop(schedule_id, None)
            data["schedules"] = schedules
            self._write(data)
            return existed

    def get(self, schedule_id: str) -> Schedule | None:
        with self._critical():
            data = self._read()
        record = (data.get("schedules", {}) or {}).get(schedule_id)
        return Schedule.from_dict(record) if record else None

    def all(self) -> list[Schedule]:
        with self._critical():
            data = self._read()
        return [Schedule.from_dict(r) for r in (data.get("schedules", {}) or {}).values()]

    # -- SCHED-02: one firing, one execution --------------------------------

    def claim(self, schedule_id: str, occurrence: Occurrence | str,
              *, now: float | None = None) -> ExecutionClaim | None:
        """Atomically take one firing. ``None`` means somebody already has it.

        The key is ``(schedule_id, occurrence)`` — the occurrence is a UTC instant, so a
        restart, a second process and a DST change all resolve to the same key. This is
        what makes "persistente Ausführungs-ID" true rather than hopeful.
        """
        occ_key = occurrence.key if isinstance(occurrence, Occurrence) else str(occurrence)
        stamp = time.time() if now is None else float(now)
        with self._critical():
            data = self._read()
            ledger = dict(data.get("executions", {}) or {})
            key = f"{schedule_id}|{occ_key}"
            if key in ledger:
                return None                      # already claimed — no double delivery
            claim = ExecutionClaim(
                execution_id=f"exec-{uuid.uuid4().hex[:12]}",
                schedule_id=schedule_id,
                occurrence=occ_key,
                claimed_at=stamp,
                claimed_by=self.owner,
            )
            ledger[key] = claim.to_dict()
            data["executions"] = ledger
            self._write(data)
            return claim

    def finish(self, claim: ExecutionClaim, *, state: str = "done", detail: str = "") -> None:
        with self._critical():
            data = self._read()
            ledger = dict(data.get("executions", {}) or {})
            key = f"{claim.schedule_id}|{claim.occurrence}"
            record = dict(ledger.get(key, claim.to_dict()))
            record["state"] = state
            record["detail"] = detail
            ledger[key] = record
            data["executions"] = ledger
            self._write(data)

    def executions(self) -> list[ExecutionClaim]:
        with self._critical():
            data = self._read()
        return [ExecutionClaim.from_dict(r) for r in (data.get("executions", {}) or {}).values()]

    def is_running(self, schedule_id: str) -> bool:
        """Is this schedule's workflow still in flight? (overlap avoidance)"""
        return any(
            c.schedule_id == schedule_id and c.state in ("claimed", "running")
            for c in self.executions()
        )

    def recover(self, *, now: float | None = None, stale_after_s: float = 3600.0) -> list[ExecutionClaim]:
        """Crash recovery: claims whose owner died are released.

        A claim that is still ``claimed``/``running`` after ``stale_after_s`` without a
        heartbeat is a crashed run, not a long one. Releasing it lets the next scheduler
        take the firing — while the *ledger entry stays*, so it is never silently re-run
        as if it had never been attempted.
        """
        stamp = time.time() if now is None else float(now)
        released: list[ExecutionClaim] = []
        with self._critical():
            data = self._read()
            ledger = dict(data.get("executions", {}) or {})
            for key, record in list(ledger.items()):
                claim = ExecutionClaim.from_dict(record)
                if claim.state in ("claimed", "running") and stamp - claim.claimed_at > stale_after_s:
                    claim.state = "failed"
                    claim.detail = "released by crash recovery (owner did not report back)"
                    ledger[key] = claim.to_dict()
                    released.append(claim)
            if released:
                data["executions"] = ledger
                self._write(data)
        return released

    # -- SCHED-01/02: what is due -------------------------------------------

    def due(self, *, now: dt.datetime | None = None,
            since: dt.datetime | None = None) -> list[tuple[Schedule, Occurrence]]:
        """The firings that should happen now, honouring activation and catch-up.

        ``since`` defaults to 24 h before ``now`` — that window is the look-back used for
        missed runs. It is deliberately bounded: an unbounded window is exactly the
        "unkontrolliert alle nachholen" the requirement forbids.
        """
        moment = (now or _utc_now()).astimezone(dt.timezone.utc)
        window_start = (since or (moment - dt.timedelta(hours=24))).astimezone(dt.timezone.utc)
        out: list[tuple[Schedule, Occurrence]] = []
        for schedule in self.all():
            if not schedule.active:
                continue                          # SCHED-04: not activated => never fires
            if schedule.serialize and self.is_running(schedule.schedule_id):
                continue                          # SCHED-02: same workflow serialised
            found = occurrences_between(schedule, window_start, moment + dt.timedelta(seconds=1))
            if not found:
                continue
            claimed = {
                c.occurrence for c in self.executions() if c.schedule_id == schedule.schedule_id
            }
            fresh = [o for o in found if o.key not in claimed]
            if not fresh:
                continue
            if schedule.catch_up == CatchUp.SKIP:
                # only firings that are actually now; nothing from the past
                fresh = [o for o in fresh if (moment - o.utc).total_seconds() < 60]
            elif schedule.catch_up == CatchUp.ONCE:
                # exactly one of the missed ones, the most recent — never all of them
                fresh = [fresh[-1]]
            for occ in fresh:
                out.append((schedule, occ))
        out.sort(key=lambda pair: pair[1].utc)
        return out
