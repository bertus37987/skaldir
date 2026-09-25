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
    LeaseStateCorrupt,
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
# DESK-02 at the ADAPTER: a fence nobody calls is not a fence
# --------------------------------------------------------------------------- #


def test_the_input_adapter_refuses_an_action_with_a_stale_fence(store):
    """The check belongs where the pointer is moved, not in the policy layer.

    Regression for the gap the requirement audit found: ``DesktopLeaseStore.fence()``
    existed and was tested, but **no input adapter called it**, so DESK-02 held on paper
    only. ``HostSessionRunner.bind_input_fence`` is the wiring, and this test drives the
    real adapter method.
    """
    from skaldir.cu_host_session import Action, HostSessionRunner

    runner = HostSessionRunner(target="", attach_to_session=False, display=":0")
    lease = store.acquire("run-1", task="Edit video", now=1.0)
    seen: list[str] = []
    runner.bind_input_fence(lambda: seen.append("checked"))

    result = runner.act(Action(kind="mouse", payload={"x": 1, "y": 1}))
    assert seen == ["checked"], "the fence must run before the action"
    assert result.ok in (True, False)  # the point is the check, not the display


def test_a_stale_generation_is_dropped_at_the_adapter_not_executed(store):
    from skaldir.cu_host_session import Action, HostSessionRunner
    from skaldir.desktop_lease import StaleFence

    runner = HostSessionRunner(target="", attach_to_session=False, display=":0")
    lease = store.acquire("run-1", now=1.0)
    store.request_stop(reason="double-escape", now=2.0)  # bumps the fence

    def stale():
        store.fence("run-1", lease.generation)  # the token from BEFORE the stop

    runner.bind_input_fence(stale)
    result = runner.act(Action(kind="key", payload={"key": "a"}))
    assert result.ok is False
    assert "DESK-02" in result.detail
    assert "dropped" in result.detail or "generation" in result.detail
    assert isinstance(stale.__call__, object)  # the fence itself still raises
    try:
        stale()
    except StaleFence as exc:
        assert "not executed" in str(exc)
    else:
        raise AssertionError("the stale fence must raise StaleFence")


def test_an_unbound_runner_behaves_exactly_as_before(store):
    """No lease, no fence: the adapter must not grow a requirement of its own."""
    from skaldir.cu_host_session import HostSessionRunner

    runner = HostSessionRunner(target="", attach_to_session=False, display=":0")
    assert runner._input_fence is None
    runner.bind_input_fence(None)
    assert runner._input_fence is None


def test_the_fence_only_applies_to_input_actions_not_to_observing(store):
    from skaldir.cu_host_session import HostSessionRunner

    runner = HostSessionRunner(target="", attach_to_session=False, display=":0")
    calls: list[str] = []
    runner.bind_input_fence(lambda: calls.append("x"))
    # observe() never calls the fence — watching is not acting (DESK-02 concerns input)
    runner.observe()
    assert calls == []


# --------------------------------------------------------------------------- #
# DESK-01 at a PRODUCTION CALLER: the lease is taken and given back by the run
# --------------------------------------------------------------------------- #


def _run(store, session_id="act-run", **kw):
    from skaldir.act_service import run_action_list

    return run_action_list(
        [{"kind": "wait", "seconds": 0}],  # harmless, no display needed
        runner_kind="dry_run",
        session_id=session_id,
        desktop_lease=store,
        write_record=False,
        **kw,
    )


def test_a_desktop_run_takes_and_releases_the_lease(store):
    """DESK-01: `act_service.run_action_list` is the production caller.

    Regression for the gap the requirement audit found: `DesktopLeaseStore` existed and
    was tested, but **no production code called it**. It is now wired into the run that
    drives the host desktop, and the lease is released on the same teardown as the runner.
    """
    record = _run(store, session_id="run-1", lease_task="Edit video")
    assert record["desktop"]["acquired"] is True or record["desktop"].get("generation")
    assert record["desktop"]["owner_run"] == "run-1"
    assert record["desktop"]["released"] is True
    # and the desktop is actually free again afterwards
    assert store.state() == "free"


def test_a_held_desktop_is_a_named_blocker_not_a_silent_fallthrough(store):
    store.acquire("other-run", task="Research", now=1.0)
    record = _run(store, session_id="run-2")
    assert record["ok"] is False
    assert record["verdict"] == "BLOCKED"
    assert "DESK-01" in record["blocked_reason"]
    assert "other-run" in record["blocked_reason"]
    assert record["desktop"]["acquired"] is False
    # the other run still owns it — a refusal never steals
    assert store.current().run_id == "other-run"


def test_the_fence_is_bound_on_the_resolved_runner(store):
    """DESK-02 through the real path: the runner gets the fence, not just a method."""
    from skaldir.act_service import run_action_list

    seen: list = []

    class Spy:
        def bind_input_fence(self, fence):
            seen.append(fence)

        def cleanup_record(self):
            return {"container": "", "prefix": "", "closed": True, "still_present": False}

        def close(self):
            return None

    run_action_list([{"kind": "wait", "seconds": 0}], runner_kind="dry_run",
                    session_id="run-1", desktop_lease=store, write_record=False)
    # dry_run has no bind_input_fence, so nothing is bound and nothing pretends it was
    assert seen == []

    from skaldir.act_service import _bind_desktop_fence

    spy = Spy()
    assert _bind_desktop_fence(spy, store, "run-1", 1) is True
    assert len(seen) == 1 and callable(seen[0])
    # and the bound fence really checks the store
    seen[0]()
    store.request_stop(now=2.0)
    import pytest as _pytest

    with _pytest.raises(Exception):
        seen[0]()


def test_the_production_path_really_binds_the_fence(store):
    """The wiring must reach `run_action_list`, not only the helper.

    Regression for a defect found while reviewing: `_bind_desktop_fence` existed and was
    tested, but `run_action_list` never passed `on_runner` down — so the fence was never
    bound in the real path and DESK-02 held on paper only, again. This test drives the
    production entry point and asserts the fence is actually attached.
    """
    from skaldir.act_service import run_action_list

    bound: list = []

    class Spy:
        # the minimum the Runner protocol needs, so the run reaches the bind point
        isolation = "dry-run"
        is_isolated = True
        target_scope = ""

        def bind_input_fence(self, fence):
            bound.append(fence)

        def act(self, action):
            from skaldir.cu_host_session import ActionResult

            return ActionResult(ok=True, stdout="OK", detail="dry run")

        def observe(self):
            from skaldir.cu_host_session import Observation

            return Observation(text="OK", detail="dry run")

        def cleanup_record(self):
            return {"container": "", "prefix": "", "closed": True, "still_present": False}

        def close(self):
            return None

    spy = Spy()
    record = run_action_list(
        # a *declared* action shape (kind/channel/verify) — the run must get past
        # validation before there is a runner to bind the fence to
        [{"kind": "cli", "channel": "vision", "argv": ["sh", "-c", "echo OK"],
          "verify": {"expect_stdout_contains": "OK"}}],
        runner_kind="dry_run",
        runner=spy,                 # the caller supplies the runner, so we can see the bind
        session_id="run-1",
        desktop_lease=store,
        write_record=False,
    )
    assert bound, ("run_action_list did not bind the input fence to the runner — "
                   "DESK-02 would not hold on the real input path")
    assert callable(bound[0])
    # and the bound fence really consults the lease
    lease = store.current()
    assert lease is not None
    bound[0]()                      # no raise while the lease is ours
    store.request_stop(reason="double-escape", now=2.0)
    import pytest as _pytest

    with _pytest.raises(Exception):
        bound[0]()
    assert record["desktop"]["released"] is True


def test_a_runner_without_a_fence_is_reported_not_silently_treated_as_fenced(store):
    from skaldir.act_service import _bind_desktop_fence

    class Plain:
        pass

    assert _bind_desktop_fence(Plain(), store, "run-1", 1) is False


def test_the_lease_is_released_even_when_the_run_fails(store):
    from skaldir.act_service import run_action_list

    run_action_list([{"kind": "launch", "argv": ["/bin/false"]}], runner_kind="dry_run",
                    session_id="run-1", desktop_lease=store, write_record=False)
    assert store.state() == "free"


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


def test_a_corrupt_state_file_is_refused_not_treated_as_free(store):
    """GOAL §2 — requirement change, and deliberately so.

    This test previously asserted that unreadable state is treated as an *empty* state.
    That is the behaviour the task calls out as wrong: if a damaged file reads as "free",
    a second run takes the desktop while the first may still hold the input path. A corrupt
    file is now an explicit error and recovery is a deliberate act.
    """
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(LeaseStateCorrupt):
        store.current()
    with pytest.raises(LeaseStateCorrupt):
        store.acquire("run-1", now=1.0)

    # recovery is explicit, not silent: the operator moves the damaged file aside
    damaged = store.path.with_suffix(".corrupt")
    store.path.rename(damaged)
    assert damaged.exists()
    assert store.current() is None
    assert store.acquire("run-1", now=1.0).run_id == "run-1"


def test_an_unbound_live_desktop_runner_refuses_to_act():
    """GOAL §2: kein optional ungebundener Hostpfad darf die Kontrolle umgehen.

    A runner that drives the *real* host desktop and simply never bound the fence must be
    refused at the adapter. Otherwise any caller that forgot to hold the desktop lease
    would move the user's mouse unattended.
    """
    from skaldir.cu_host_session import Action, HostSessionRunner

    runner = HostSessionRunner.__new__(HostSessionRunner)
    runner.display = ":0"
    runner._input_fence = None
    runner.attach_to_session = False
    runner.isolation = "live-desktop"          # the user's real session

    assert runner.requires_input_fence is True
    result = runner.act(Action(kind="mouse", payload={"x": 1, "y": 1}))
    assert result.ok is False
    assert "no DESK-02 fence is bound" in result.detail


def test_a_live_desktop_runner_with_a_fence_actually_checks_it(store):
    """The bound fence must be consulted, not merely stored (DESK-02 at the adapter)."""
    from skaldir.desktop_lease import StaleFence
    from skaldir.cu_host_session import Action, HostSessionRunner

    lease = store.acquire("run-1", now=1.0)

    runner = HostSessionRunner.__new__(HostSessionRunner)
    runner.display = ":0"
    runner.attach_to_session = False
    runner.isolation = "live-desktop"

    checks: list[int] = []

    # a fence carrying a *stale* generation: it must be consulted and must stop the act
    # *before* anything else happens — which is also why no runner internals are touched
    # here, they are not reached
    def stale_fence():
        checks.append(1)
        store.fence(lease.run_id, lease.generation - 1)

    runner.bind_input_fence(stale_fence)
    result = runner.act(Action(kind="mouse", payload={"x": 1, "y": 1}))
    assert result.ok is False
    assert "input refused at the adapter (DESK-02 fence)" in result.detail
    assert checks == [1], "the fence was not consulted"

    # and the current generation is accepted by the same fence check
    store.fence(lease.run_id, lease.generation)      # must not raise
    assert checks == [1]


def test_an_isolated_runner_does_not_need_the_host_lease():
    """Containers and isolated sessions are not the shared desktop."""
    from skaldir.cu_host_session import HostSessionRunner

    runner = HostSessionRunner.__new__(HostSessionRunner)
    runner.isolation = "host-session"          # an isolated session, not the shared desktop
    assert runner.requires_input_fence is False


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
