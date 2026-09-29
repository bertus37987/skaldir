"""Exclusive host-desktop control: the lease, its fencing token and the corner indicator.

``docs/masterplan/01-MASTERPLAN.md`` DESK-01..DESK-04. The host desktop is a *resource* with
one owner at a time, and the masterplan is explicit that this is not the same thing as the
run lease in :mod:`skaldir.run_supervisor` (which answers "which process owns this run").

* **DESK-01** — a lease names the owning run, carries a token/generation and the session it
  is valid for. Dependent sub-tools of the same run share it; other runs wait *visibly*.
  Unrelated container/web work keeps running.
* **DESK-02** — an ownership change **invalidates old input**. "Single owner" alone is not
  enough: the fencing token is checked at the actual input adapter, so a late action from
  before a pause or crash cannot move the mouse. :meth:`DesktopLease.fence` is that check.
* **DESK-03** — a visible host indicator while control is held, even without a focused TUI.
  It shows the responsible task and the emergency-stop hint. This is the small status
  mechanism the plan requires, not a general desktop application.
* **DESK-04** — a user may allow long exclusive control (video editing). Nothing here times
  the lease out after a few seconds; a lease is released on stop, on completion, or when the
  owner proves it can resume into a known state.

Why a separate store from ``run_supervisor.RunStore``: the guarded resource differs (physical
input vs. run ownership) and AUD-02 forbids a *competing goal/memory/approval* store, which
this is not. The two are joined by the ``run_id`` only.

The indicator is rendered as text on purpose. Anything that paints on the compositor has to
be chosen for the measured backend (DESK-03 says "an Fedora-Backend anpassen"); a
backend-neutral renderer that the host layer prints is what can be tested here without a
display, and :func:`indicator_lines` is exactly what the host layer feeds it.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # POSIX only; the in-process lock still applies elsewhere
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

__all__ = [
    "DesktopLease",
    "DesktopLeaseStore",
    "IndicatorState",
    "LeaseState",
    "LeaseRefused",
    "StaleFence",
    "indicator_lines",
]

#: One state file, one owner. Generation increments on every hand-over; that number is the
#: fencing token the input adapter compares.
STATE_FILE = "desktop-lease.json"


class LeaseState(str):
    """What the desktop is doing right now — DESK-03's three visible states plus waiting."""

    CONTROLLING = "controlling"
    WAITING = "waiting"
    PAUSED = "paused"
    FREE = "free"


class LeaseRefused(RuntimeError):
    """Another run holds the lease. The message names the holder so the queue is visible."""


class StaleFence(RuntimeError):
    """An input action arrived with a fencing token older than the current owner.

    This is DESK-02's whole point: the action is **dropped**, never executed, and the caller
    learns exactly which generation it had versus which one owns the desktop now.
    """


@dataclass(frozen=True)
class IndicatorState:
    """The facts the host corner indicator shows (DESK-03)."""

    state: str = LeaseState.FREE
    task: str = ""
    run_id: str = ""
    stop_hint: str = "Esc, Esc to pause"

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "task": self.task, "run_id": self.run_id,
                "stop_hint": self.stop_hint}


def indicator_lines(info: IndicatorState, *, width: int = 34, unicode: bool = True) -> list[str]:
    """Render the corner status bar.

    Three short lines, exactly ``width`` cells each, so a host layer can position them
    without measuring anything::

        Skaldir controls the desktop
        Task: Edit video
        Esc, Esc to pause

    ``controlling`` is only ever shown with a valid lease (DESK-03); ``waiting`` and
    ``paused`` are shown as themselves, never dressed up as control.
    """
    w = max(8, int(width))
    bar = "─" if unicode else "-"
    state = str(info.state or LeaseState.FREE)
    if state == LeaseState.CONTROLLING:
        head = "Skaldir controls the desktop"
    elif state == LeaseState.WAITING:
        head = "Skaldir is waiting for the desktop"
    elif state == LeaseState.PAUSED:
        head = "Skaldir desktop control paused"
    else:
        head = "Skaldir desktop control idle"
    task = f"Task: {info.task}" if info.task else "Task: -"
    hint = info.stop_hint or "Esc, Esc to pause"
    return [
        head[:w].ljust(w),
        bar * w if unicode or True else bar * w,
        task[:w].ljust(w),
        hint[:w].ljust(w),
    ]


@dataclass
class DesktopLease:
    """One live lease record."""

    run_id: str
    task: str = ""
    generation: int = 0
    owner_token: str = ""
    session: str = ""
    state: str = LeaseState.CONTROLLING
    acquired_at: float = 0.0
    heartbeat_at: float = 0.0
    released_at: float = 0.0
    stop_requested: bool = False
    waiters: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task": self.task,
            "generation": self.generation,
            "owner_token": self.owner_token,
            "session": self.session,
            "state": self.state,
            "acquired_at": self.acquired_at,
            "heartbeat_at": self.heartbeat_at,
            "released_at": self.released_at,
            "stop_requested": self.stop_requested,
            "waiters": list(self.waiters),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DesktopLease:
        return cls(
            run_id=str(data.get("run_id", "")),
            task=str(data.get("task", "")),
            generation=int(data.get("generation", 0) or 0),
            owner_token=str(data.get("owner_token", "")),
            session=str(data.get("session", "")),
            state=str(data.get("state", LeaseState.FREE)),
            acquired_at=float(data.get("acquired_at", 0.0) or 0.0),
            heartbeat_at=float(data.get("heartbeat_at", 0.0) or 0.0),
            released_at=float(data.get("released_at", 0.0) or 0.0),
            stop_requested=bool(data.get("stop_requested", False)),
            waiters=[str(w) for w in data.get("waiters", []) or []],
        )

    def indicator(self) -> IndicatorState:
        return IndicatorState(state=self.state, task=self.task, run_id=self.run_id)


class LeaseStateCorrupt(RuntimeError):
    """The persisted lease state could not be read.

    This is deliberately **not** the same as "free". Treating a corrupt file as an empty
    one would hand the desktop to a second run while the first still believes it owns the
    input path — exactly the double-control DESK-01 exists to prevent.
    """


class DesktopLeaseStore:
    """Persistence + the atomic hand-over for the desktop lease.

    Two things have to be true at once, and a thread lock alone cannot provide either:

    * **Atomic ownership across processes and across store instances.** ``acquire``,
      ``release`` and ``request_stop`` are read-modify-write cycles; two Skaldir processes
      running them concurrently would both see "free" and both take the desktop. So every
      cycle runs under a real :func:`fcntl.flock` on a sidecar lock file, *and* under the
      in-process lock (``flock`` is per file-descriptor, so two store objects in one
      process still need the thread lock to share one descriptor).

    * **A corrupt file is never mistaken for a free desktop.** See
      :class:`LeaseStateCorrupt`.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._lock = threading.RLock()
        self._depth = threading.local()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- the cross-process critical section ---------------------------------

    @contextmanager
    def _critical(self) -> Iterator[None]:
        """Hold the desktop state exclusively across processes *and* threads.

        The sidecar ``.lock`` file is never the state itself, so it can stay behind after
        a crash without leaving a stale owner. ``flock`` is released by the kernel when
        the descriptor closes, which is what makes a crashed run give the lock back.
        """
        depth = getattr(self._depth, "value", 0)
        if depth:
            # Re-entrant on purpose. ``flock`` is per open-file-description, so a nested
            # call opens a *second* descriptor and blocks against the outer one forever.
            # A method that reads the generation while already holding the section would
            # therefore deadlock — that exact bug was measured, so nesting is supported
            # instead of being banned by comment.
            self._depth.value = depth + 1
            try:
                yield
            finally:
                self._depth.value = depth
            return

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            # the *thread* lock here, not _critical(): flock is per descriptor, so two
            # store objects in one process would otherwise deadlock against themselves
            with self._lock:
                self._depth.value = 1
                try:
                    yield
                finally:
                    self._depth.value = 0
                    if fcntl is not None:
                        fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    # -- io -----------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LeaseStateCorrupt(
                f"desktop lease state at {self.path} is not valid JSON ({exc}) — "
                "refusing to treat it as 'free', because that would hand the desktop to a "
                "second run while the first may still hold it. Restore or move the file."
            ) from exc
        if not isinstance(data, dict):
            raise LeaseStateCorrupt(
                f"desktop lease state at {self.path} is {type(data).__name__}, not an "
                "object — refusing to treat it as 'free'."
            )
        return data

    def _write(self, data: dict[str, Any]) -> None:
        """Atomic replace from a *private* temporary file.

        The temp name must be unique per writer. A fixed ``state.json.tmp`` means two
        writers move each other's half-written file away and one ``os.replace`` fails with
        ``FileNotFoundError`` — measured in the concurrency test, not guessed.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=self.path.name + ".", suffix=".tmp"
        )

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # -- queries ------------------------------------------------------------

    def current(self) -> DesktopLease | None:
        with self._critical():
            data = self._read()
        lease = data.get("lease")
        return DesktopLease.from_dict(lease) if isinstance(lease, dict) else None

    def generation(self) -> int:
        with self._critical():
            return int(self._read().get("generation", 0) or 0)

    def state(self) -> str:
        lease = self.current()
        return lease.state if lease else LeaseState.FREE

    # -- lifecycle ----------------------------------------------------------

    def acquire(self, run_id: str, *, task: str = "", session: str = "",
                now: float | None = None, force: bool = False) -> DesktopLease:
        """Take the desktop. Raises :class:`LeaseRefused` while somebody else owns it.

        ``force`` is the *deliberate* owner change (a user hands control to another run). It
        bumps the generation — which is what makes DESK-02 hold: the previous owner's token
        is stale the moment this returns.
        """
        stamp = time.time() if now is None else float(now)
        with self._critical():
            data = self._read()
            existing = (
                DesktopLease.from_dict(data["lease"])
                if isinstance(data.get("lease"), dict) else None
            )
            # A released lease keeps its record for the audit trail but owns nothing: the
            # desktop is free again and the next run may take it without a forced takeover.
            if existing is not None and existing.state == LeaseState.FREE:
                existing = None
            if existing and existing.run_id and existing.run_id != run_id and not force:
                if run_id not in existing.waiters:
                    existing.waiters.append(run_id)
                data["lease"] = existing.to_dict()
                self._write(data)
                raise LeaseRefused(
                    f"desktop is controlled by run {existing.run_id!r} "
                    f"(generation {existing.generation}); {run_id!r} is queued as waiter "
                    f"{len(existing.waiters)}"
                )
            generation = int(data.get("generation", 0) or 0) + 1
            lease = DesktopLease(
                run_id=run_id, task=task, generation=generation,
                owner_token=f"{run_id}:{generation}", session=session,
                state=LeaseState.CONTROLLING, acquired_at=stamp, heartbeat_at=stamp,
                waiters=[w for w in (existing.waiters if existing else []) if w != run_id],
            )
            data["generation"] = generation
            data["lease"] = lease.to_dict()
            self._write(data)
            return lease

    def heartbeat(self, run_id: str, *, now: float | None = None) -> bool:
        stamp = time.time() if now is None else float(now)
        with self._critical():
            data = self._read()
            lease = (
                DesktopLease.from_dict(data["lease"])
                if isinstance(data.get("lease"), dict) else None
            )
            if lease is None or lease.run_id != run_id:
                return False
            lease.heartbeat_at = stamp
            data["lease"] = lease.to_dict()
            self._write(data)
            return True

    def release(self, run_id: str, *, reason: str = "", now: float | None = None) -> bool:
        """Give the desktop back. The generation is NOT reused — that is the fence."""
        stamp = time.time() if now is None else float(now)
        with self._critical():
            data = self._read()
            lease = (
                DesktopLease.from_dict(data["lease"])
                if isinstance(data.get("lease"), dict) else None
            )
            if lease is None or lease.run_id != run_id:
                return False
            lease.state = LeaseState.FREE
            lease.released_at = stamp
            lease.stop_requested = False
            if reason:
                lease.task = lease.task
            data["lease"] = lease.to_dict()
            self._write(data)
            return True

    def request_stop(
        self, *, reason: str = "double-escape", now: float | None = None
    ) -> dict[str, Any]:

        """DESK-02/STOP-02: an emergency stop.

        Marks the run stopped, moves it to ``paused`` and — crucially — **bumps the
        generation**, so every input action already queued with the old token is refused at
        the adapter. Actions that already executed are not undone (STOP-02 says so).
        """
        stamp = time.time() if now is None else float(now)
        with self._critical():
            data = self._read()
            lease = (
                DesktopLease.from_dict(data["lease"])
                if isinstance(data.get("lease"), dict) else None
            )
            if lease is None:
                return {
                    "ok": False,
                    "reason": "no lease",
                    "generation": int(data.get("generation", 0) or 0),
                }
            lease.stop_requested = True
            lease.state = LeaseState.PAUSED
            lease.heartbeat_at = stamp
            generation = int(data.get("generation", 0) or 0) + 1
            data["generation"] = generation
            data["lease"] = lease.to_dict()
            self._write(data)
            return {
                "ok": True,
                "reason": reason,
                "run_id": lease.run_id,
                "fence_generation": generation,
                "dropped_actions_belong_to": generation - 1,
                "stopped_at": stamp,
            }

    def resume(self, run_id: str, *, now: float | None = None) -> DesktopLease:
        """Explicit user resume after a stop (RUN-03). Always bumps the generation."""
        stamp = time.time() if now is None else float(now)
        with self._critical():
            data = self._read()
            lease = (
                DesktopLease.from_dict(data["lease"])
                if isinstance(data.get("lease"), dict) else None
            )
            if lease is None or lease.run_id != run_id:
                raise LeaseRefused(f"run {run_id!r} does not hold the desktop lease")
            if not lease.stop_requested:
                return lease
            generation = int(data.get("generation", 0) or 0) + 1
            lease.stop_requested = False
            lease.state = LeaseState.CONTROLLING
            lease.generation = generation
            lease.owner_token = f"{run_id}:{generation}"
            lease.heartbeat_at = stamp
            lease.acquired_at = stamp
            data["generation"] = generation
            data["lease"] = lease.to_dict()
            self._write(data)
            return lease

    def fence(self, run_id: str, generation: int) -> None:
        """The DESK-02 check an input adapter performs before it touches the mouse.

        Raises :class:`StaleFence` and drops the action when the token is not the current
        owner's. Callers must not catch this and continue — the action is dead.
        """
        with self._critical():
            # read the generation directly: calling self.generation() here would take the
            # flock a second time on a second descriptor and deadlock
            current = int(self._read().get("generation", 0) or 0)
        if int(generation) != current:
            raise StaleFence(
                f"input action carries fencing generation {int(generation)} but the desktop "
                f"is at generation {current}: action dropped, not executed"
            )

    def waiters(self) -> list[str]:
        lease = self.current()
        return list(lease.waiters) if lease else []

    def indicator(self) -> IndicatorState:
        lease = self.current()
        return lease.indicator() if lease else IndicatorState()
