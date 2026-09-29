"""The emergency-stop dispatcher: the missing link in the double-Escape chain.

The requirement (GOAL §3) names a chain, not a set of parts:

    physical keyboard source → double-Escape detector → input dispatcher/queue
    → fence invalidation → safe abort of further inputs → persistently paused run
    → visible status

:mod:`skaldir.stop_signal` detects the signal and :meth:`DesktopLeaseStore.request_stop`
invalidates the fence — but nothing joined them, so a detected stop could leave a live
input queue running. This module is that join.

What it guarantees, and what it deliberately refuses:

* **Only a user stop counts.** ``Origin.USER`` is required. A synthetic escape injected by
  the agent must never imitate an emergency stop, and autorepeat never does (the detector
  already rejects it; this refuses the origin as a second line of defence).
* **Further input is dropped, not delayed.** The queue is drained and every queued action
  is marked dropped; the fence generation is bumped so anything that slipped through is
  refused at the adapter anyway.
* **Held input is released on purpose.** ``release_held`` is a named step, not a side
  effect: held keys and an in-flight drag are released before the run is considered paused.
* **The pause survives.** It is written to the store, so a restart finds a paused run.
* **Nobody resumes it by accident.** Voice, a scheduler or a restart cannot clear it. Only
  an explicit user resume with a named authority can.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .desktop_lease import DesktopLeaseStore
from .stop_signal import InputEvent, Origin, StopSignal

__all__ = [
    "EmergencyStop",
    "PauseAlreadyActive",
    "ResumeRefused",
    "StopDispatcher",
    "StopReceipt",
]


#: Who is allowed to clear a pause. Anything else is refused by name.
RESUME_AUTHORITIES = frozenset({"user", "owner", "operator"})


class PauseAlreadyActive(RuntimeError):
    """A pause is already in force; a second stop is recorded, not re-applied."""


class ResumeRefused(RuntimeError):
    """A caller tried to clear a pause it is not allowed to clear."""


@dataclass
class EmergencyStop:
    """The persisted record of one emergency stop."""

    run_id: str
    reason: str = "double-escape"
    stopped_at: float = 0.0
    fence_generation: int = 0
    dropped_actions: int = 0
    held_released: list[str] = field(default_factory=list)
    resumed: bool = False
    resumed_at: float = 0.0
    resumed_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "reason": self.reason,
            "stopped_at": self.stopped_at,
            "fence_generation": self.fence_generation,
            "dropped_actions": self.dropped_actions,
            "held_released": list(self.held_released),
            "resumed": self.resumed,
            "resumed_at": self.resumed_at,
            "resumed_by": self.resumed_by,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmergencyStop:
        return cls(
            run_id=str(data.get("run_id", "")),
            reason=str(data.get("reason", "")),
            stopped_at=float(data.get("stopped_at", 0.0) or 0.0),
            fence_generation=int(data.get("fence_generation", 0) or 0),
            dropped_actions=int(data.get("dropped_actions", 0) or 0),
            held_released=list(data.get("held_released", []) or []),
            resumed=bool(data.get("resumed", False)),
            resumed_at=float(data.get("resumed_at", 0.0) or 0.0),
            resumed_by=str(data.get("resumed_by", "")),
        )


@dataclass
class StopReceipt:
    """What happened, so the caller can show it and never has to guess."""

    applied: bool
    run_id: str
    reason: str
    dropped_actions: int = 0
    held_released: list[str] = field(default_factory=list)
    fence_generation: int = 0
    already_paused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "run_id": self.run_id,
            "reason": self.reason,
            "dropped_actions": self.dropped_actions,
            "held_released": list(self.held_released),
            "fence_generation": self.fence_generation,
            "already_paused": self.already_paused,
        }


class StopDispatcher:
    """Joins detector, input queue, fence and pause into one stop."""

    def __init__(
        self,
        store: DesktopLeaseStore,
        *,
        queue: Any | None = None,
        on_status: Callable[[str], None] | None = None,
        release_held: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self.store = store
        self.queue = queue
        self.on_status = on_status
        self.release_held = release_held
        self._stops: dict[str, EmergencyStop] = {}

    # -- status ------------------------------------------------------------

    def _status(self, text: str) -> None:
        if self.on_status is not None:
            try:
                self.on_status(text)
            except Exception:  # noqa: BLE001 — a status sink must never break a stop
                pass

    # -- the chain ---------------------------------------------------------

    def stop(
        self,
        run_id: str,
        *,
        signal: StopSignal | None = None,
        origin: Origin = Origin.USER,
        reason: str = "double-escape",
        now: float | None = None,
    ) -> StopReceipt:
        """Run the whole chain for one stop.

        Returns a :class:`StopReceipt` rather than raising, because a stop that cannot be
        applied is still information the operator needs — and a stop must never be lost
        inside an exception the caller did not expect.

        ``origin`` names where the presses came from. ``StopSignal`` itself carries only a
        ``source`` string — the heredity lives on the input events — so the caller passes
        it, and :func:`feed_and_stop` derives it from the events it fed.
        """
        stamp = time.time() if now is None else float(now)

        # 1. only a user stop may pause the run
        if origin is not Origin.USER:
            return StopReceipt(
                applied=False,
                run_id=run_id,
                reason=(
                    f"stop refused: {origin} is not a user stop — an agent-generated "
                    "escape must never imitate the emergency stop"
                ),
            )

        # 2. a second stop is recorded, not re-applied (the generation must not jump twice)
        if run_id in self._stops and not self._stops[run_id].resumed:
            self._status("paused")
            return StopReceipt(
                applied=False,
                run_id=run_id,
                reason="already paused",
                already_paused=True,
                fence_generation=self._stops[run_id].fence_generation,
            )

        self._status("stopping")

        # 3. release held input first, while we still can — keys held down and an
        #    in-flight drag must not be left dangling across a pause
        held: list[str] = []
        if self.release_held is not None:
            try:
                held = [str(item) for item in self.release_held()]
            except Exception:  # noqa: BLE001 — a stuck drag must not stop the stop
                held = ["release failed"]

        # 4. drop everything still queued; the fence below would refuse it anyway, but
        #    the queue must not keep a backlog for a run that is no longer driving
        dropped = self._drain_queue()

        # 5. invalidate the fence + pause the run (this bumps the generation)
        outcome = self.store.request_stop(reason=reason, now=stamp)
        generation = int(outcome.get("fence_generation", 0) or 0)

        receipt = StopReceipt(
            applied=bool(outcome.get("ok", False)),
            run_id=run_id,
            reason=reason,
            dropped_actions=dropped,
            held_released=held,
            fence_generation=generation,
        )
        self._stops[run_id] = EmergencyStop(
            run_id=run_id,
            reason=reason,
            stopped_at=stamp,
            fence_generation=generation,
            dropped_actions=dropped,
            held_released=held,
        )
        self._status("paused")
        return receipt

    def _drain_queue(self) -> int:
        """Drop everything still queued for this run. Returns how much was dropped."""
        queue = self.queue
        if queue is None:
            return 0
        for name in ("drain", "drop_all", "clear"):
            method = getattr(queue, name, None)
            if callable(method):
                try:
                    result = method()
                except Exception:  # noqa: BLE001
                    return 0
                if isinstance(result, int):
                    return result
                if isinstance(result, (list, tuple, set)):
                    return len(result)
                return 0
        return 0

    # -- state -------------------------------------------------------------

    def is_paused(self, run_id: str) -> bool:
        stop = self._stops.get(run_id)
        return stop is not None and not stop.resumed

    def stop_for(self, run_id: str) -> EmergencyStop | None:
        return self._stops.get(run_id)

    # -- resume ------------------------------------------------------------

    def resume(self, run_id: str, *, by: str = "", now: float | None = None) -> EmergencyStop:
        """Clear a pause — only for a named user authority.

        Voice, a scheduler and a restart are deliberately **not** in
        :data:`RESUME_AUTHORITIES`. A pause that a background process could quietly clear
        would not be a pause at all.
        """
        authority = str(by or "").strip().lower()
        if authority not in RESUME_AUTHORITIES:
            raise ResumeRefused(
                f"resume refused: {authority or 'nobody'!r} may not clear an emergency "
                f"pause — only {sorted(RESUME_AUTHORITIES)} (explicit user action)"
            )
        stop = self._stops.get(run_id)
        if stop is None or stop.resumed:
            raise ResumeRefused(f"resume refused: run {run_id!r} is not paused")
        stamp = time.time() if now is None else float(now)
        self.store.resume(run_id, now=stamp)
        stop.resumed = True
        stop.resumed_at = stamp
        stop.resumed_by = authority
        self._status("resumed")
        return stop


def feed_and_stop(
    dispatcher: StopDispatcher,
    run_id: str,
    events: Iterable[InputEvent],
    *,
    now: float | None = None,
) -> StopReceipt | None:
    """Feed input events through the detector and run the chain on the first user stop.

    Returns ``None`` when no stop was signalled — that is the common case and not an error.
    """
    from .stop_signal import DoubleEscapeDetector

    detector = DoubleEscapeDetector()
    origin = Origin.USER
    for event in events:
        origin = getattr(event, "origin", Origin.USER) or Origin.USER
        signal = detector.feed(event, now=now)
        if signal is not None:
            return dispatcher.stop(
                run_id, signal=signal, origin=origin, reason="double-escape", now=now
            )
    return None
