"""Coalesced progress rendering (CODEX-PERF block).

A streaming run produces events far faster than a human can read them (one
per streamed token, per tool transition, per transcript line). Writing each one
separately costs one ``write`` syscall and one terminal flush per event, and on
a slow terminal it makes the *producer* wait — the classic way a TUI stops being
responsive while the model is streaming.

Codex solves this with two small pure helpers (`codex-rs/tui/src/`):

* ``frame_rate_limiter.rs`` — clamps a requested draw deadline forward so no more
  than one frame per ``MIN_FRAME_INTERVAL`` (120 FPS) is emitted, even when
  widgets ask constantly;
* ``frame_requester.rs`` — an actor that **coalesces many requests into a single
  notification** on the event loop, so N callers asking for a redraw cost one
  draw;
* ``streaming/chunking.rs`` + ``streaming/commit_tick.rs`` — an *adaptive* policy
  on top: in ``Smooth`` mode it drains a small batch per tick, and when the queue
  is deep or its head is old it switches to ``CatchUp`` (bigger, faster-paced
  batches, with hysteresis so it does not flap) — the UI stays smooth *and* never
  falls behind the stream.

This module is the Python port of exactly that, with no UI dependency: a queue
plus a drain plan, so the writer is the only thing that touches the terminal.

    coalescer = ProgressCoalescer()
    coalescer.push("step 1 ...")
    coalescer.push("step 1 ... done")
    for line in coalescer.tick():      # at most one batch per MIN_INTERVAL
        stream.write(line + "\\n")

``emit_many()`` is the whole-path helper (push + drain + write) used by a
producer that cannot schedule ticks itself, e.g. a stream reader callback.
"""

from __future__ import annotations

import sys
import time
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "MIN_FRAME_INTERVAL",
    "AdaptiveChunkingPolicy",
    "ChunkingDecision",
    "FrameRateLimiter",
    "ProgressCoalescer",
    "QueueSnapshot",
    "Smooth",
    "CatchUp",
    "SMOOTH_BATCH",
    "CATCH_UP_BATCH",
]

#: Ported verbatim from Codex ``frame_rate_limiter.rs``: a 120 FPS floor
#: (≈8.33 ms). Below this a terminal cannot show the difference and the writes
#: are pure cost.
MIN_FRAME_INTERVAL = 0.008333334

#: Codex's mode names, kept so the port is recognisable.
Smooth = "smooth"
CatchUp = "catch-up"

#: Lines committed per tick in each mode (Codex drains one batch per tick).
SMOOTH_BATCH = 1
CATCH_UP_BATCH = 12

#: Queue depth / head age at which the policy escalates (Codex: depth or age).
CATCH_UP_DEPTH = 12
CATCH_UP_AGE_S = 0.25
#: …and the hysteresis that keeps it from flapping back immediately.
EXIT_DEPTH = 2
EXIT_AGE_S = 0.05
REENTRY_HOLD_S = 0.5


@dataclass(frozen=True)
class QueueSnapshot:
    """What the policy decides on: how much is waiting and how old it is."""

    queued_lines: int
    oldest_age_s: float

    def to_dict(self) -> dict[str, Any]:
        return {"queued_lines": self.queued_lines, "oldest_age_s": round(self.oldest_age_s, 4)}


@dataclass(frozen=True)
class ChunkingDecision:
    """The drain plan for one tick: which mode, and how many lines to commit."""

    mode: str
    batch: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "batch": self.batch, "reason": self.reason}


class FrameRateLimiter:
    """Clamp a requested emit deadline forward; never emit faster than 120 FPS.

    A direct port of Codex's helper, including the ``None`` first-emit case
    (the very first emit is never clamped — a stream must be able to show its
    first line immediately).
    """

    def __init__(self, min_interval: float = MIN_FRAME_INTERVAL) -> None:
        self.min_interval = float(min_interval)
        self._last: float | None = None

    def clamp_deadline(self, requested: float) -> float:
        if self._last is None:
            return requested
        return max(requested, self._last + self.min_interval)

    def mark_emitted(self, emitted_at: float) -> None:
        self._last = emitted_at

    @property
    def last_emitted_at(self) -> float | None:
        return self._last

    def reset(self) -> None:
        self._last = None


class AdaptiveChunkingPolicy:
    """Smooth ↔ CatchUp with hysteresis and a re-entry hold (Codex ``chunking.rs``)."""

    def __init__(
        self,
        *,
        catch_up_depth: int = CATCH_UP_DEPTH,
        catch_up_age_s: float = CATCH_UP_AGE_S,
        smooth_batch: int = SMOOTH_BATCH,
        catch_up_batch: int = CATCH_UP_BATCH,
    ) -> None:
        self.catch_up_depth = int(catch_up_depth)
        self.catch_up_age_s = float(catch_up_age_s)
        self.smooth_batch = int(smooth_batch)
        self.catch_up_batch = int(catch_up_batch)
        self._mode = Smooth
        self._held_until = 0.0

    @property
    def mode(self) -> str:
        return self._mode

    def reset(self) -> None:
        self._mode = Smooth
        self._held_until = 0.0

    def decide(self, snapshot: QueueSnapshot, now: float) -> ChunkingDecision:
        if self._mode == Smooth:
            if now < self._held_until:
                # Re-entry hold (Codex `holds_reentry_after_catch_up_exit`): right
                # after a catch-up exit a mild burst must NOT flip the mode back —
                # that flapping is what makes a UI jitter. A *severe* backlog still
                # escalates immediately (Codex `severe_backlog_can_reenter_during_hold`).
                if self._severe(snapshot):
                    self._mode = CatchUp
                    return ChunkingDecision(CatchUp, self._batch_for(snapshot), "severe backlog")
                return ChunkingDecision(Smooth, self.smooth_batch, "re-entry hold")
            if snapshot.queued_lines >= self.catch_up_depth:
                self._mode = CatchUp
                return ChunkingDecision(
                    CatchUp, self._batch_for(snapshot), f"depth>={self.catch_up_depth}"
                )
            if snapshot.oldest_age_s >= self.catch_up_age_s:
                self._mode = CatchUp
                return ChunkingDecision(
                    CatchUp, self._batch_for(snapshot), f"age>={self.catch_up_age_s}s"
                )
            return ChunkingDecision(Smooth, self.smooth_batch, "smooth")
        # in catch-up: leave only when the backlog is genuinely gone
        if snapshot.queued_lines <= EXIT_DEPTH and snapshot.oldest_age_s <= EXIT_AGE_S:
            self._mode = Smooth
            self._held_until = now + REENTRY_HOLD_S
            return ChunkingDecision(Smooth, self.smooth_batch, "backlog drained")
        return ChunkingDecision(CatchUp, self._batch_for(snapshot), "catching up")

    def _severe(self, snapshot: QueueSnapshot) -> bool:
        return (
            snapshot.queued_lines > self.catch_up_batch * 4
            or snapshot.oldest_age_s > self.catch_up_age_s * 4
        )

    def _batch_for(self, snapshot: QueueSnapshot) -> int:
        """Drain the whole backlog when it is severe, else the catch-up batch."""
        if snapshot.queued_lines > self.catch_up_batch * 4:
            return snapshot.queued_lines
        return self.catch_up_batch


@dataclass
class ProgressCoalescer:
    """A bounded progress queue drained at most once per ``min_interval``.

    ``push`` is O(1) and never blocks on I/O — that is what keeps a producer
    (stream reader, tool worker) free. ``tick`` returns the lines to render;
    ``emit_many`` does push + tick + write for callers without their own loop.
    """

    stream: Any = None
    min_interval: float = MIN_FRAME_INTERVAL
    max_queued: int = 4096
    limiter: FrameRateLimiter = field(init=False)
    policy: AdaptiveChunkingPolicy = field(init=False)
    _queue: deque[str] = field(init=False)
    _dropped: int = 0
    _emitted: int = 0
    _writes: int = 0
    _last_drain: float | None = field(default=None, init=False)
    _first_queued_at: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.limiter = FrameRateLimiter(self.min_interval)
        self.policy = AdaptiveChunkingPolicy()
        self._queue = deque()

    # -- producer side ----------------------------------------------------- #

    def push(self, line: str) -> None:
        """Queue one progress line; oldest lines are dropped when over budget.

        Dropping is the last resort (the cap is a memory bound, not a policy):
        the adaptive policy switches to catch-up draining long before the queue
        is this deep. What was dropped is counted and surfaced as one visible
        marker line on the next drain — a silent drop would be a lie.
        """
        text = str(line)
        if not text:
            return
        if len(self._queue) >= self.max_queued:
            self._queue.popleft()
            self._dropped += 1
        if not self._queue:
            # the age of the *oldest waiting line* is measured from here, against
            # the tick's own stamp — never against a second reading of the clock
            self._first_queued_at = time.monotonic()
        self._queue.append(text)

    def oldest_age(self, now: float | None = None) -> float:
        """How long the oldest queued line has been waiting (0.0 when empty)."""
        if not self._queue or self._first_queued_at is None:
            return 0.0
        stamp = time.monotonic() if now is None else float(now)
        return max(0.0, stamp - self._first_queued_at)

    def snapshot(self, now: float | None = None) -> QueueSnapshot:
        return QueueSnapshot(len(self._queue), self.oldest_age(now))

    # -- consumer side ----------------------------------------------------- #

    def tick(self, *, now: float | None = None) -> list[str]:
        """The lines this tick may commit (empty when the rate limit withholds)."""
        stamp = time.monotonic() if now is None else float(now)
        if not self._queue:
            return []
        if self.limiter.last_emitted_at is not None:
            if self.limiter.clamp_deadline(stamp) > stamp:
                return []  # too soon — the coalescing that makes this cheap
        decision = self.policy.decide(self.snapshot(stamp), stamp)
        batch = max(1, int(decision.batch))
        out = [self._queue.popleft() for _ in range(min(batch, len(self._queue)))]
        if not self._queue:
            self._first_queued_at = None
        self.limiter.mark_emitted(stamp)
        self._last_drain = stamp
        self._emitted += len(out)
        return out

    def drain(self, *, now: float | None = None) -> list[str]:
        """Everything queued, ignoring the rate limit (end of a run / cancel)."""
        stamp = time.monotonic() if now is None else float(now)
        out = list(self._queue)
        self._queue.clear()
        self._first_queued_at = None
        if self._dropped:
            out.insert(0, f"...[{self._dropped} progress lines dropped - the consumer fell behind]")
            self._dropped = 0
        if out:
            self.limiter.mark_emitted(stamp)
            self._last_drain = stamp
            self._emitted += len(out)
        return out

    def emit_many(self, lines: Iterable[str]) -> list[str]:
        """push() every line, then commit what one tick allows, writing to ``stream``."""
        for line in lines:
            self.push(line)
        return self.write(self.tick())

    def write(self, lines: Sequence[str]) -> list[str]:
        committed = list(lines)
        if not committed or self.stream is None:
            return committed
        # ONE write for the whole batch: the syscall count is the point
        self.stream.write("".join(f"{line}\n" for line in committed))
        try:
            self.stream.flush()
        except Exception:  # noqa: BLE001 - a stream without flush is fine
            pass
        self._writes += 1
        return committed

    def finish(self) -> list[str]:
        """Drain and render the tail — nothing may be lost at the end of a run."""
        return self.write(self.drain())

    @property
    def stats(self) -> dict[str, int]:
        return {
            "queued": len(self._queue),
            "dropped": self._dropped,
            "emitted": self._emitted,
            "writes": self._writes,
        }


def default_stream() -> Any:
    """Progress goes to stderr — stdout stays a parseable envelope."""
    return sys.stderr
