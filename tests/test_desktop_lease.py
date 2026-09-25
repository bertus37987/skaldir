"""DESK-01..DESK-04: exclusive desktop ownership, fencing and the corner indicator.

The point of these tests is DESK-02: an action that arrives after an ownership change is
**dropped**, not executed. That is what the fencing generation is for, and every one of these
asserts the drop rather than trusting a boolean.
"""

from __future__ import annotations

import pytest

from skaldir.desktop_lease import (
    DesktopLease,
    DesktopLeaseStore,
    IndicatorState,
    LeaseRefused,
    LeaseState,
    StaleFence,
    indicator_lines,
)


@pytest.fixture()
def store(tmp_path):
    return DesktopLeaseStore(tmp_path / "state" / "desktop-lease.json")


# --------------------------------------------------------------------------- #
# DESK-01: one owner, a token/generation, and a visible queue
# --------------------------------------------------------------------------- #


def test_one_run_holds_the_lease_and_the_next_waits_visibly(store):
    first = store.acquire("run-1", task="Edit video", now=10.0)
    assert first.run_id == "run-1"
    assert first.task == "Edit video"
    assert first.state == LeaseState.CONTROLLING
    assert first.generation == 1

    with pytest.raises(LeaseRefused) as excinfo:
        store.acquire("run-2", task="Research", now=11.0)
    message = str(excinfo.value)
    assert "run-1" in message            # the holder is named
    assert "queued as waiter" in message  # and the queue is visible

    assert store.waiters() == ["run-2"]
    assert store.current().run_id == "run-1"


def test_the_fencing_token_names_run_and_generation(store):
    lease = store.acquire("run-7", now=1.0)
    assert lease.owner_token == "run-7:1"
    assert lease.generation == 1
    assert store.generation() == 1


def test_a_second_acquire_by_the_same_run_is_not_a_conflict(store):
    store.acquire("run-1", now=1.0)
    again = store.acquire("run-1", now=2.0)
    assert again.run_id == "run-1"


def test_a_deliberate_owner_change_is_possible_and_bumps_the_generation(store):
    store.acquire("run-1", now=1.0)
    taken = store.acquire("run-2", task="Takeover", now=2.0, force=True)
    assert taken.generation == 2
    assert taken.run_id == "run-2"
    assert store.waiters() == []


# --------------------------------------------------------------------------- #
# DESK-02: an ownership change invalidates old input
# --------------------------------------------------------------------------- #


def test_stale_input_is_dropped_at_the_adapter(store):
    lease = store.acquire("run-1", now=1.0)
    store.fence("run-1", lease.generation)  # the happy path does not raise

    store.request_stop(reason="double-escape", now=2.0)
    with pytest.raises(StaleFence) as excinfo:
        store.fence("run-1", lease.generation)
    assert "action dropped, not executed" in str(excinfo.value)
    assert "generation 1" in str(excinfo.value)
    assert "generation 2" in str(excinfo.value)


def test_the_generation_is_never_reused_after_a_release(store):
    first = store.acquire("run-1", now=1.0)
    store.release("run-1", now=2.0)
    second = store.acquire("run-2", now=3.0)
    assert second.generation == first.generation + 1
    with pytest.raises(StaleFence):
        store.fence("run-1", first.generation)


def test_an_action_from_a_previous_owner_is_refused_after_a_takeover(store):
    old = store.acquire("run-1", now=1.0)
    store.acquire("run-2", now=2.0, force=True)
    with pytest.raises(StaleFence):
        store.fence("run-1", old.generation)


def test_the_fence_rejects_any_wrong_generation_not_just_an_old_one(store):
    lease = store.acquire("run-1", now=1.0)
    for wrong in (0, 2, 99, lease.generation + 1):
        with pytest.raises(StaleFence):
            store.fence("run-1", wrong)


def test_a_late_action_after_a_crash_recovery_is_also_refused(store):
    lease = store.acquire("run-1", now=1.0)
    # the process died and a new one took over
    store.acquire("run-1", now=5.0)
    with pytest.raises(StaleFence):
        store.fence("run-1", lease.generation)


# --------------------------------------------------------------------------- #
# DESK-03: the indicator reports the truth, and shows the stop hint
# --------------------------------------------------------------------------- #


def test_the_indicator_shows_the_task_and_the_stop_hint(store):
    store.acquire("run-1", task="Edit video", now=1.0)
    info = store.indicator()
    assert info.state == LeaseState.CONTROLLING
    assert info.task == "Edit video"
    assert info.run_id == "run-1"
    lines = indicator_lines(info, width=34)
    assert lines[0].rstrip() == "Skaldir controls the desktop"
    assert lines[2].rstrip() == "Task: Edit video"
    assert lines[3].rstrip() == "Esc, Esc to pause"
    assert all(len(line) == 34 for line in lines)


def test_controlling_is_never_shown_without_a_lease():
    idle = indicator_lines(IndicatorState(), width=30)
    assert "controls the desktop" not in idle[0]
    assert "idle" in idle[0]


def test_waiting_and_paused_are_shown_as_themselves():
    waiting = indicator_lines(IndicatorState(state=LeaseState.WAITING, task="Research"), width=30)
    assert "waiting" in waiting[0]
    assert "controls the desktop" not in waiting[0]

    paused = indicator_lines(IndicatorState(state=LeaseState.PAUSED, task="Edit video"), width=30)
    assert "paused" in paused[0]
    assert "controls the desktop" not in paused[0]


def test_the_indicator_folds_to_ascii_when_asked():
    lines = indicator_lines(IndicatorState(state=LeaseState.CONTROLLING, task="T"), width=20,
                            unicode=False)
    assert all(len(line) == 20 for line in lines)
    assert set(lines[1]) == {"-"}
    assert all(ord(ch) < 128 for line in lines for ch in line)


def test_a_long_task_is_clipped_to_the_indicator_width():
    info = IndicatorState(state=LeaseState.CONTROLLING, task="x" * 200)
    lines = indicator_lines(info, width=24)
    assert all(len(line) == 24 for line in lines)


def test_the_indicator_dict_is_machine_readable(store):
    store.acquire("run-1", task="T", now=1.0)
    payload = store.indicator().to_dict()
    assert payload == {"state": LeaseState.CONTROLLING, "task": "T", "run_id": "run-1",
                       "stop_hint": "Esc, Esc to pause"}


# --------------------------------------------------------------------------- #
# DESK-04: long exclusive control is allowed; stop and resume are explicit
# --------------------------------------------------------------------------- #


def test_a_lease_is_not_timed_out_after_a_few_seconds(store):
    store.acquire("run-1", task="Edit video", now=1.0)
    # nine hours later the same run still owns it (DESK-04: no blanket interruption)
    assert store.heartbeat("run-1", now=1.0 + 9 * 3600)
    assert store.current().run_id == "run-1"
    assert store.state() == LeaseState.CONTROLLING


def test_the_emergency_stop_pauses_the_run_and_keeps_its_identity(store):
    store.acquire("run-1", task="Edit video", now=1.0)
    result = store.request_stop(reason="double-escape", now=2.0)
    assert result["ok"] is True
    assert result["reason"] == "double-escape"
    assert result["run_id"] == "run-1"
    assert result["fence_generation"] == 2
    assert result["dropped_actions_belong_to"] == 1
    assert store.state() == LeaseState.PAUSED
    assert store.current().run_id == "run-1"  # the run is paused, not forgotten


def test_resume_is_an_explicit_action_and_bumps_the_fence(store):
    store.acquire("run-1", now=1.0)
    before = store.generation()
    store.request_stop(now=2.0)
    stopped = store.generation()
    assert stopped > before

    resumed = store.resume("run-1", now=3.0)
    assert resumed.state == LeaseState.CONTROLLING
    assert resumed.stop_requested is False
    assert resumed.generation == stopped + 1
    store.fence("run-1", resumed.generation)  # the new token is valid again


def test_resume_is_refused_for_a_run_that_does_not_own_the_desktop(store):
    store.acquire("run-1", now=1.0)
    with pytest.raises(LeaseRefused):
        store.resume("run-2", now=2.0)


def test_resume_without_a_stop_changes_nothing(store):
    lease = store.acquire("run-1", now=1.0)
    again = store.resume("run-1", now=2.0)
    assert again.generation == lease.generation  # no gratuitous fence bump


def test_stopping_without_a_lease_is_an_honest_no(store):
    assert store.request_stop(now=1.0) == {"ok": False, "reason": "no lease",
                                           "generation": 0}


def test_heartbeat_for_a_non_owner_is_refused(store):
    store.acquire("run-1", now=1.0)
    assert store.heartbeat("run-2", now=2.0) is False
    assert store.release("run-2", now=2.0) is False


# --------------------------------------------------------------------------- #
# durability: the record survives a restart and is written atomically
# --------------------------------------------------------------------------- #


def test_the_lease_survives_a_new_store_object_on_the_same_file(store, tmp_path):
    store.acquire("run-1", task="Edit video", now=1.0)
    reopened = DesktopLeaseStore(store.path)
    assert reopened.current() is not None
    assert reopened.current().run_id == "run-1"
    assert reopened.current().task == "Edit video"
    assert reopened.generation() == 1


def test_a_corrupt_state_file_is_treated_as_empty_not_as_a_crash(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ not json", encoding="utf-8")
    assert store.current() is None
    assert store.generation() == 0
    assert store.state() == LeaseState.FREE


def test_no_temporary_file_is_left_behind(store):
    store.acquire("run-1", now=1.0)
    store.request_stop(now=2.0)
    leftovers = list(store.path.parent.glob("*.tmp"))
    assert leftovers == []
    assert store.path.exists()


def test_lease_round_trips_through_dict():
    lease = DesktopLease(run_id="r", task="t", generation=3, state=LeaseState.CONTROLLING,
                         waiters=["a", "b"])
    back = DesktopLease.from_dict(lease.to_dict())
    assert back == lease
