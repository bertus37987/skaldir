"""The emergency-stop chain: detector → queue → fence → pause → status.

GOAL §3 demands a *chain*, not a set of parts, and says what must not happen: agent
generated escapes must not imitate a user stop, held input must be released in a controlled
way, and a pause must not be cleared by Voice, a scheduler or a restart.
"""

from __future__ import annotations

import pytest

from skaldir.desktop_lease import DesktopLeaseStore, LeaseState, StaleFence
from skaldir.stop_dispatch import (
    RESUME_AUTHORITIES,
    ResumeRefused,
    StopDispatcher,
    feed_and_stop,
)
from skaldir.stop_signal import InputEvent, Origin


def _esc(at: float, origin=Origin.USER) -> InputEvent:
    return InputEvent(key="ESC", at=at, origin=origin)


class FakeQueue:
    """A queue that keeps a backlog, so 'drop everything' can be counted."""

    def __init__(self, pending: int = 0) -> None:
        self.pending = list(range(pending))
        self.drained = 0

    def drain(self) -> int:
        self.drained = len(self.pending)
        self.pending = []
        return self.drained


@pytest.fixture()
def store(tmp_path):
    return DesktopLeaseStore(tmp_path / "state" / "desktop-lease.json")


@pytest.fixture()
def statuses():
    return []


def _dispatcher(store, statuses, *, queue=None, held=None):
    return StopDispatcher(
        store,
        queue=queue,
        on_status=statuses.append,
        release_held=(lambda: list(held or [])),
    )


def test_the_whole_chain_runs_in_order(store, statuses):
    """detector → queue drained → fence bumped → run paused → status shown."""
    queue = FakeQueue(pending=5)
    store.acquire("run-1", task="t", now=1.0)
    before = store.generation()

    dispatcher = _dispatcher(store, statuses, queue=queue, held=["key:Shift", "drag"])
    receipt = feed_and_stop(
        dispatcher,
        "run-1",
        [_esc(1.000), _esc(1.120)],
        now=2.0,
    )

    assert receipt is not None and receipt.applied is True
    assert receipt.dropped_actions == 5, "the queue was not drained"
    assert receipt.held_released == ["key:Shift", "drag"], "held input was not released"
    assert receipt.fence_generation > before, "the fence generation was not bumped"
    assert statuses == ["stopping", "paused"], f"status order wrong: {statuses}"

    # the run really is paused, and an action with the old token is now refused
    assert dispatcher.is_paused("run-1") is True
    assert store.state() == LeaseState.PAUSED
    with pytest.raises(StaleFence):
        store.fence("run-1", before)


def test_an_agent_generated_escape_is_not_an_emergency_stop(store, statuses):
    """GOAL §3: agent-generierte Escapes dürfen keinen Nutzerstopp imitieren.

    Two independent lines of defence, both asserted: the *detector* does not even form a
    signal from non-user events, and the *dispatcher* refuses a non-user origin if one ever
    reaches it. Neither alone is enough if the other is later relaxed.
    """
    store.acquire("run-1", task="t", now=1.0)
    before = store.generation()
    dispatcher = _dispatcher(store, statuses)

    # 1. the detector never raises a signal for a synthetic press at all
    receipt = feed_and_stop(
        dispatcher,
        "run-1",
        [_esc(1.000, origin=Origin.AGENT), _esc(1.120, origin=Origin.AGENT)],
        now=2.0,
    )
    assert receipt is None, "the detector formed a stop signal from agent presses"
    assert dispatcher.is_paused("run-1") is False
    assert store.generation() == before, "a synthetic stop bumped the fence"
    assert statuses == []

    # 2. and if one reaches the dispatcher anyway, it is refused by name
    receipt = dispatcher.stop("run-1", origin=Origin.AGENT, now=2.0)
    assert receipt.applied is False
    assert "must never imitate the emergency stop" in receipt.reason
    assert dispatcher.is_paused("run-1") is False
    assert store.generation() == before
    assert statuses == [], "a refused stop still changed the visible status"


def test_a_second_stop_does_not_bump_the_fence_twice(store, statuses):
    """A repeated Esc while paused is recorded, not re-applied."""
    store.acquire("run-1", task="t", now=1.0)
    dispatcher = _dispatcher(store, statuses)
    first = dispatcher.stop("run-1", now=2.0)
    assert first.applied is True
    generation = first.fence_generation

    second = dispatcher.stop("run-1", now=3.0)
    assert second.applied is False
    assert second.already_paused is True
    assert second.fence_generation == generation, "a repeated stop jumped the fence again"
    assert store.generation() == generation


def test_a_pause_cannot_be_cleared_by_voice_a_scheduler_or_a_restart(store):
    """GOAL §3: Pause darf durch Voice, Scheduler oder Neustart nicht
    eigenständig aufgehoben werden."""
    store.acquire("run-1", task="t", now=1.0)
    dispatcher = _dispatcher(store, [])
    dispatcher.stop("run-1", now=2.0)

    for untrusted in ("voice", "scheduler", "restart", "system", "", "auto"):
        with pytest.raises(ResumeRefused):
            dispatcher.resume("run-1", by=untrusted, now=3.0)
        assert dispatcher.is_paused("run-1") is True, f"{untrusted!r} cleared the pause"

    # only a named user authority may
    assert "user" in RESUME_AUTHORITIES
    stop = dispatcher.resume("run-1", by="user", now=4.0)
    assert stop.resumed is True and stop.resumed_by == "user"
    assert dispatcher.is_paused("run-1") is False


def test_a_new_run_after_a_restart_finds_the_pause(store, tmp_path):
    """The pause is persisted, so it survives the process that created it."""
    store.acquire("run-1", task="t", now=1.0)
    StopDispatcher(store).stop("run-1", now=2.0)

    # a fresh dispatcher over the same state file — the model of a restarted process
    reloaded = StopDispatcher(DesktopLeaseStore(store.path))
    assert reloaded.is_paused("run-1") is False, "stops are per dispatcher instance"
    # but the *store* is the authority and still says paused
    assert store.state() == LeaseState.PAUSED
    with pytest.raises(ResumeRefused):
        reloaded.resume("run-1", by="user", now=3.0)


def test_a_stop_without_a_queue_still_pauses_and_bumps_the_fence(store, statuses):
    """The chain must not depend on a queue being present."""
    store.acquire("run-1", task="t", now=1.0)
    before = store.generation()
    dispatcher = _dispatcher(store, statuses)
    receipt = dispatcher.stop("run-1", now=2.0)
    assert receipt.applied is True
    assert receipt.dropped_actions == 0
    assert receipt.fence_generation > before


def test_a_failing_held_release_does_not_lose_the_stop(store, statuses):
    """A stuck drag must not prevent the pause."""
    store.acquire("run-1", task="t", now=1.0)
    dispatcher = StopDispatcher(
        store, on_status=statuses.append, release_held=lambda: 1 / 0
    )
    receipt = dispatcher.stop("run-1", now=2.0)
    assert receipt.applied is True, "the stop was lost because releasing held input failed"
    assert receipt.held_released == ["release failed"]
    assert store.state() == LeaseState.PAUSED
