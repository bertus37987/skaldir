"""Subagent briefs — worker-assignment contracts (design 04 §5).

In-memory only (no second lease/store system): briefs reuse the supervisor's
established owner/epoch fencing idea, validated and coordinated here.
"""

import threading

import pytest

from skaldir import subagent_briefs as sb


def make_kwargs(**over):
    kwargs = dict(
        assignment_id="a-1",
        goal_id="g-1",
        base_revision="base-abc",
        allowed_write_paths=["src/module_a.py"],
        read_context=["pact:g-1", "notes:3"],
        owner="worker-1",
    )
    kwargs.update(over)
    return kwargs


def create(coordinator=None, **over):
    coord = coordinator or sb.BriefCoordinator()
    kwargs = make_kwargs(**over)
    return coord.create_brief(
        assignment_id=kwargs["assignment_id"],
        goal_id=kwargs["goal_id"],
        base_revision=kwargs["base_revision"],
        allowed_write_paths=kwargs["allowed_write_paths"],
        read_context=kwargs["read_context"],
        owner=kwargs["owner"],
        deadline_monotonic=over.pop("deadline_monotonic", 60.0),
        acceptance=over.pop("acceptance", ["tests green"]),
        worktree=over.pop("worktree", "/tmp/wt-a-1"),
        worker_kind=over.pop("worker_kind", "code"),
    )


# --- (1) validation ----------------------------------------------------------


def test_brief_requires_scope():
    with pytest.raises(ValueError):
        sb.SubagentBrief(**make_kwargs(allowed_write_paths=[]))


def test_brief_requires_deadline():
    with pytest.raises(ValueError):
        sb.SubagentBrief(**make_kwargs(deadline_monotonic=None))


def test_brief_requires_acceptance():
    with pytest.raises(ValueError):
        sb.SubagentBrief(**make_kwargs(acceptance=[]))


def test_brief_requires_owner_and_ids():
    with pytest.raises(ValueError):
        sb.SubagentBrief(**make_kwargs(owner=""))
    with pytest.raises(ValueError):
        sb.SubagentBrief(**make_kwargs(goal_id=""))


def test_brief_status_lifecycle():
    brief = create()
    for status in ("running", "collecting", "verifying"):
        brief.advance(status)
        assert brief.status == status
    with pytest.raises(ValueError):
        brief.advance("created")  # lifecycle is forward-only


# --- (2) worktree requirement -------------------------------------------------


def test_code_worker_requires_worktree_at_creation():
    coord = sb.BriefCoordinator()
    with pytest.raises(ValueError):
        create(coord, worktree=None)
    # a non-code worker needs no worktree
    brief = create(coord, worktree=None, worker_kind="research")
    assert brief.worktree is None


# --- (3) owner_epoch fencing ---------------------------------------------------


def test_stale_epoch_result_rejected_after_takeover():
    coord = sb.BriefCoordinator()
    brief = create(coord)
    assert brief.owner_epoch == 1
    brief.advance("running")
    # takeover bumps the epoch monotonically
    new = coord.takeover(brief.assignment_id, new_owner="worker-2")
    assert new.owner_epoch == 2
    stale = coord.report_result(
        brief.assignment_id,
        owner="worker-1",
        owner_epoch=1,  # original worker, pre-takeover token
        delivered=True,
        outputs=["diff text"],
    )
    assert not stale.accepted
    assert stale.reason == "stale_epoch"
    fresh = coord.report_result(
        brief.assignment_id,
        owner="worker-2",
        owner_epoch=2,
        delivered=True,
        outputs=["diff text"],
    )
    assert fresh.accepted


def test_wrong_owner_result_rejected():
    coord = sb.BriefCoordinator()
    brief = create(coord)
    decision = coord.report_result(
        brief.assignment_id, owner="someone-else", owner_epoch=1, delivered=True
    )
    assert not decision.accepted
    assert decision.reason == "unknown_owner"


# --- (4) timeout classification ------------------------------------------------


def test_timeout_with_verifiable_output_is_partial():
    coord = sb.BriefCoordinator()
    brief = create(coord)
    brief.advance("running")
    decision = coord.on_timeout(
        brief.assignment_id,
        owner=brief.owner,
        owner_epoch=brief.owner_epoch,
        outputs=["patch: fix 1"],
    )
    assert decision.outcome == "timed_out_partial"
    assert brief.status == "timed_out_partial"


def test_timeout_without_output_is_empty():
    coord = sb.BriefCoordinator()
    brief = create(coord)
    decision = coord.on_timeout(
        brief.assignment_id,
        owner=brief.owner,
        owner_epoch=brief.owner_epoch,
        outputs=[],
    )
    assert decision.outcome == "timed_out_empty"
    assert brief.status == "timed_out_empty"


# --- (5) parent abort propagation ------------------------------------------------


def test_parent_abort_cancels_outstanding_and_marks_unknown_external():
    coord = sb.BriefCoordinator()
    b1 = create(coord)
    b2 = create(coord, assignment_id="a-2")
    b3 = create(coord, assignment_id="a-3")
    b1.advance("running", external_may_have_started=True)
    b2.advance("collecting")
    decision = coord.abort_all(reason="parent aborted")
    assert decision.outcome == "aborted"
    assert decision.side_effects_unknown == ["a-1"]  # only b1 may have externals out
    assert b1.status == "aborted"
    assert b2.status == "aborted"
    assert b3.status == "aborted"  # never started, but still cancelled


def test_abort_result_reported_with_epoch():
    coord = sb.BriefCoordinator()
    coord.create_brief(
        **{
            "assignment_id": "a-9",
            "goal_id": "g-1",
            "base_revision": "b",
            "allowed_write_paths": ["x"],
            "read_context": ["c"],
            "owner": "w1",
            "deadline_monotonic": 60.0,
            "acceptance": ["done"],
            "worktree": "/tmp/wt",
        }
    )
    decision = coord.abort_all(reason="parent died")
    assert decision.outcome == "aborted"


def test_side_effect_state_tracked_not_lost():
    coord = sb.BriefCoordinator()
    brief = create(coord)
    brief.advance("running", external_may_have_started=True)
    assert brief.side_effect_state == "unknown"
    brief.advance("collecting")
    assert brief.side_effect_state == "unknown"


# --- (6) run_supervisor wire-in --------------------------------------------------


def test_run_supervisor_enqueue_accepts_brief_and_records_result(tmp_path):
    from skaldir import run_supervisor as rs

    store = rs.RunStore(tmp_path / "runs.db")
    try:
        store.create_run("s1", run_id="run-1", state=rs.ACTIVE)
        run_id = "run-1"
        coord = sb.BriefCoordinator()
        brief = create(coord, goal_id="g-1")
        assert store.enqueue_brief(run_id, brief)
        # already enqueued briefs are idempotently accepted, never duplicated
        assert store.enqueue_brief(run_id, brief)
        assert len(store.briefs(run_id)) == 1
        assert store.briefs(run_id)[0]["assignment_id"] == brief.assignment_id
        brief.advance("running")
        decision = coord.report_result(
            brief.assignment_id,
            owner=brief.owner,
            owner_epoch=brief.owner_epoch,
            delivered=True,
            outputs=["done"],
        )
        assert decision.accepted
        assert store.record_brief_result(run_id, brief, decision)
        rows = store.briefs(run_id)
        assert rows[-1]["status"] == brief.status
        audit_actions = [entry["action"] for entry in store.audit(run_id)]
        assert "subagent_brief_enqueued" in audit_actions
        assert "subagent_brief_result" in audit_actions
    finally:
        store.close()


def test_run_supervisor_rejects_invalid_brief(tmp_path):
    from skaldir import run_supervisor as rs

    store = rs.RunStore(tmp_path / "runs.db")
    try:
        store.create_run("s1", run_id="run-1", state=rs.ACTIVE)
        with pytest.raises(ValueError):
            sb.SubagentBrief(**make_kwargs(deadline_monotonic=None))
        coord = sb.BriefCoordinator()
        with pytest.raises(ValueError):
            create(coord, worktree=None)  # code worker without worktree
        # enqueue path: unknown run is rejected like other store calls
        brief = create(coord)  # coord-registered brief
        with pytest.raises(ValueError):
            store.enqueue_brief("no-such-run", brief)
    finally:
        store.close()


# --- (7) concurrency -------------------------------------------------------------


def test_concurrent_takeovers_get_monotonic_epochs():
    coord = sb.BriefCoordinator()
    brief = create(coord)
    epochs: list[int] = []
    lock = threading.Lock()

    def bump():
        taken = coord.takeover(brief.assignment_id, new_owner="w")
        with lock:
            epochs.append(taken.owner_epoch)

    threads = [threading.Thread(target=bump) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert epochs == sorted(epochs) and len(set(epochs)) == len(epochs)
    assert brief.owner_epoch >= 6  # started at 1
