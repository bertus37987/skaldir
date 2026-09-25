"""Coalesced progress rendering: the rate limiter, the adaptive policy, the queue.

The port from Codex's ``frame_rate_limiter`` / ``chunking`` / ``commit_tick`` has
three properties worth pinning, and each of them is a way the naive version
loses data or blocks a producer:

* the **first** emit is never clamped (a stream must show its first line at
  once), every later one at most once per interval;
* the policy escalates to catch-up on depth *or* age and returns to smooth with
  hysteresis — never flapping;
* nothing is silently lost: a bounded queue reports what it dropped.
"""

from __future__ import annotations

import time

from skaldir.perf_progress import (
    CATCH_UP_BATCH,
    MIN_FRAME_INTERVAL,
    AdaptiveChunkingPolicy,
    CatchUp,
    FrameRateLimiter,
    ProgressCoalescer,
    QueueSnapshot,
    Smooth,
)


class RecordingStream:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, text: str) -> int:
        self.writes.append(text)
        return len(text)

    def flush(self) -> None:
        return None


def test_the_first_emit_is_never_clamped():
    limiter = FrameRateLimiter()
    assert limiter.clamp_deadline(100.0) == 100.0


def test_later_emits_are_clamped_to_the_minimum_interval():
    limiter = FrameRateLimiter()
    limiter.mark_emitted(100.0)
    assert limiter.clamp_deadline(100.001) == 100.0 + MIN_FRAME_INTERVAL
    assert limiter.clamp_deadline(100.5) == 100.5  # a later request is not moved back


def test_min_interval_is_the_codex_120fps_constant():
    assert MIN_FRAME_INTERVAL == 0.008333334


def test_smooth_by_default_and_escalates_on_depth():
    policy = AdaptiveChunkingPolicy(catch_up_depth=5)
    assert policy.decide(QueueSnapshot(1, 0.0), 0.0).mode == Smooth
    decision = policy.decide(QueueSnapshot(5, 0.0), 0.0)
    assert decision.mode == CatchUp
    assert decision.batch == CATCH_UP_BATCH
    assert "depth" in decision.reason


def test_escalates_on_age_too():
    policy = AdaptiveChunkingPolicy(catch_up_age_s=0.2)
    assert policy.decide(QueueSnapshot(1, 0.25), 0.0).mode == CatchUp


def test_catch_up_drains_a_severe_backlog_in_one_tick():
    policy = AdaptiveChunkingPolicy(catch_up_batch=4)
    decision = policy.decide(QueueSnapshot(100, 0.0), 0.0)
    assert decision.mode == CatchUp
    assert decision.batch == 100, "a severe backlog is drained whole"


def test_it_returns_to_smooth_with_hysteresis_not_immediately():
    policy = AdaptiveChunkingPolicy()  # depth 12, batch 12, hold 0.5 s
    policy.decide(QueueSnapshot(50, 0.0), 0.0)  # → catch-up
    assert policy.mode == CatchUp
    assert policy.decide(QueueSnapshot(0, 0.0), 1.0).mode == Smooth  # backlog gone

    held = policy.decide(QueueSnapshot(20, 0.0), 1.1)
    assert held.mode == Smooth, "inside the re-entry hold a mild burst must not flap"
    assert "hold" in held.reason

    severe = policy.decide(QueueSnapshot(200, 0.0), 1.2)
    assert severe.mode == CatchUp, "a severe backlog still escalates during the hold"


def test_tick_withholds_inside_the_interval_then_commits():
    # real monotonic stamps: the queue age is measured against the same clock the
    # producer stamped with, so the test drives the production semantics
    base = time.monotonic()
    coalescer = ProgressCoalescer(min_interval=10.0)
    for index in range(5):
        coalescer.push(f"line {index}")
    assert len(coalescer.tick(now=base)) == 1, "smooth mode commits one line per tick"
    assert coalescer.tick(now=base + 1.0) == [], "inside the interval nothing is emitted"
    # …and a queue that has been waiting past the interval is an *aged* queue, so
    # the policy escalates to catch-up rather than trickling one line per tick
    aged = coalescer.tick(now=base + 100.0)
    assert len(aged) == 4, "an aged backlog is drained in one catch-up tick"


def test_a_deep_queue_switches_the_tick_to_catch_up():
    coalescer = ProgressCoalescer(min_interval=0.0)
    for index in range(50):
        coalescer.push(f"line {index}")
    committed = coalescer.tick(now=time.monotonic())
    assert len(committed) == 50, "the backlog is drained, not trickled"


def test_nothing_is_lost_at_the_end_of_a_run():
    stream = RecordingStream()
    coalescer = ProgressCoalescer(stream=stream, min_interval=0.0)
    for index in range(25):
        coalescer.push(f"line {index}")
    coalescer.finish()
    text = "".join(stream.writes)
    assert all(f"line {index}" in text for index in range(25))
    assert coalescer.stats["queued"] == 0


def test_a_batch_costs_exactly_one_write():
    stream = RecordingStream()
    coalescer = ProgressCoalescer(stream=stream, min_interval=0.0)
    for index in range(10):
        coalescer.push(f"line {index}")
    coalescer.finish()
    assert len(stream.writes) == 1, "coalescing is measured in writes"


def test_the_queue_is_bounded_and_reports_what_it_dropped():
    coalescer = ProgressCoalescer(stream=None, max_queued=4, min_interval=0.0)
    for index in range(10):
        coalescer.push(f"line {index}")
    assert coalescer.stats["queued"] == 4
    assert coalescer.stats["dropped"] == 6
    drained = coalescer.drain(now=time.monotonic())
    assert any("dropped" in line for line in drained), "a silent drop would be a lie"
    assert coalescer.stats["dropped"] == 0


def test_empty_pushes_are_ignored_and_a_tick_costs_nothing():
    coalescer = ProgressCoalescer(min_interval=0.0)
    coalescer.push("")
    assert coalescer.tick(now=time.monotonic()) == []
    assert coalescer.snapshot().queued_lines == 0


def test_emit_many_pushes_then_commits_what_one_tick_allows():
    stream = RecordingStream()
    coalescer = ProgressCoalescer(stream=stream, min_interval=0.0)
    committed = coalescer.emit_many(["a", "b", "c"])
    assert committed == ["a"], "a fresh queue is in smooth mode: one line per tick"
    assert "a\n" == "".join(stream.writes)
    coalescer.finish()
    assert "a\nb\nc\n" == "".join(stream.writes), "the rest is not lost"
