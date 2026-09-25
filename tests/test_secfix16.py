"""GV2-SEC wave 16 regression tests (P0 SEC-17 + P1 SEC-01/05/06/12/13/14)."""
from __future__ import annotations

import pytest

from skaldir import budget as bud
from skaldir import subagent_briefs as sb
from skaldir.run_supervisor import RunStore


def create(coord, assignment_id="a-1", **kw):
    return coord.create_brief(
        assignment_id=assignment_id,
        goal_id="g",
        base_revision="abc",
        allowed_write_paths=["/t"],
        read_context=[],
        owner="w1",
        deadline_monotonic=10.0,
        acceptance=["ok"],
        worktree="/w",
        **kw,
    )


# ---------------------------------------------------------------- GV2-SEC-01


def test_on_timeout_without_fencing_credentials_rejected():
    coord = sb.BriefCoordinator()
    brief = create(coord)
    with pytest.raises(TypeError):
        coord.on_timeout(brief.assignment_id, outputs=["partial"])  # non-fenced call


def test_on_timeout_stale_epoch_rejected():
    coord = sb.BriefCoordinator()
    brief = create(coord)
    brief.advance("running")
    coord.takeover(brief.assignment_id, new_owner="w2")
    decision = coord.on_timeout(brief.assignment_id, owner="w2", owner_epoch=1, outputs=["x"])
    assert not decision.accepted
    assert decision.reason == "stale_epoch"
    assert brief.status == "running"


def test_on_timeout_unknown_owner_rejected():
    coord = sb.BriefCoordinator()
    brief = create(coord)
    decision = coord.on_timeout(brief.assignment_id, owner="someone-else", owner_epoch=1)
    assert not decision.accepted
    assert decision.reason == "unknown_owner"


def test_on_timeout_correct_fencing_accepted():
    coord = sb.BriefCoordinator()
    brief = create(coord)
    brief.advance("running")
    decision = coord.on_timeout(
        brief.assignment_id, owner="w1", owner_epoch=1, outputs=["patch stuff"]
    )
    assert decision.accepted
    assert decision.outcome == "timed_out_partial"


# ---------------------------------------------------------------- GV2-SEC-05


def test_many_floor_spends_blocked_at_unreserved_cap():
    # 5% of 150000 workers limit = 7500 lifetime reservation-less spend
    acc = bud.BudgetAccount(goal_id="g", small_spend_floor=200)
    blocked = 0
    for i in range(200):  # 200 * 199 = 39800 > 7500 -> blocked at the cap
        try:
            acc.spend("workers", 199)
        except bud.BudgetExceeded:
            blocked = i
            break
    else:  # pragma: no cover
        pytest.fail("200 floor spends were NOT blocked at the unreserved cap")
    assert blocked == 37  # 38 * 199 = 7562 > 7500; 37 * 199 = 7363 <= 7500
    assert acc.spent("workers") == 37 * 199


def test_unreserved_cap_fraction_configurable_and_validated():
    acc = bud.BudgetAccount(goal_id="g", unreserved_cap_fraction=0.5)
    acc.spend("tools", 199)
    assert acc.spent("tools") == 199
    with pytest.raises(bud.BudgetError):
        bud.BudgetAccount(goal_id="g", unreserved_cap_fraction=1.5)


# ---------------------------------------------------------------- GV2-SEC-06


def test_spend_on_unknown_reservation_rejected_not_floor_path():
    acc = bud.BudgetAccount(goal_id="g")
    with pytest.raises(bud.BudgetExceeded) as exc:
        acc.spend("workers", 190, reservation="res-fake-999")
    assert "unknown or exhausted reservation" in str(exc.value)
    assert acc.spent("workers") == 0


def test_re_spend_on_exhausted_reservation_rejected():
    acc = bud.BudgetAccount(goal_id="g")
    res = acc.reserve("tools", 100)
    acc.spend("tools", 100, reservation=res.reservation_id)
    with pytest.raises(bud.BudgetExceeded) as exc:
        acc.spend("tools", 5000, reservation=res.reservation_id)
    assert "exceeds the remaining hold" in str(exc.value)
    assert acc.spent("tools") == 100


def test_over_tap_beyond_reservation_rejected_never_overshoots():
    acc = bud.BudgetAccount(goal_id="g")
    res = acc.reserve("workers", 100)
    with pytest.raises(bud.BudgetExceeded) as exc:
        acc.spend("workers", 299, reservation=res.reservation_id)
    assert "exceeds the remaining hold" in str(exc.value)
    assert acc.spent("workers") == 0
    assert acc.reserved("workers") == 100  # hold untouched by the rejected spend


# ---------------------------------------------------------------- GV2-SEC-12


def make_brief(aid="a1", owner="w1", epoch=1):
    return sb.SubagentBrief(
        assignment_id=aid, goal_id="g", base_revision="abc",
        allowed_write_paths=["/t"], read_context=[], owner=owner,
        deadline_monotonic=10.0, acceptance=["ok"], worktree="/w",
        owner_epoch=epoch,
    )


def test_record_brief_result_fabricated_epoch_rejected_and_audited(tmp_path):
    store = RunStore(tmp_path / "store.db")
    try:
        store.create_run("s1", run_id="r1", state="QUEUED")
        brief = make_brief()
        store.enqueue_brief("r1", brief)
        decoy = make_brief(owner="fakeworker", epoch=999)
        decoy.status = "delivered"
        decision = sb.BriefDecision(accepted=True, outcome="delivered")
        assert store.record_brief_result("r1", decoy, decision) is False
        row = [r for r in store.briefs("r1") if r["assignment_id"] == "a1"][-1]
        assert row["status"] == "created"  # NOT overwritten
        audits = store.audit("r1")
        assert any(a["action"] == "subagent_brief_result_rejected" for a in audits)
    finally:
        store.close()


def test_record_brief_result_unknown_assignment_rejected_and_audited(tmp_path):
    store = RunStore(tmp_path / "store.db")
    try:
        store.create_run("s1", run_id="r1", state="QUEUED")
        ghost = make_brief(aid="ghost")
        ghost.status = "delivered"
        decision = sb.BriefDecision(accepted=True, outcome="delivered")
        assert store.record_brief_result("r1", ghost, decision) is False
        audits = store.audit("r1")
        assert any(
            a["action"] == "subagent_brief_result_rejected" and "unknown_assignment" in a["detail"]
            for a in audits
        )
    finally:
        store.close()


def test_record_brief_result_legit_outcome_still_persisted(tmp_path):
    store = RunStore(tmp_path / "store.db")
    try:
        store.create_run("s1", run_id="r1", state="QUEUED")
        brief = make_brief()
        store.enqueue_brief("r1", brief)
        brief.status = "delivered"
        decision = sb.BriefDecision(accepted=True, outcome="delivered")
        assert store.record_brief_result("r1", brief, decision) is True
        row = [r for r in store.briefs("r1") if r["assignment_id"] == "a1"][-1]
        assert row["status"] == "delivered"
    finally:
        store.close()


def test_store_rejects_outcome_smuggling_against_true_lifecycle(tmp_path):
    """GV2-SEC-12b: the persisted record must not trust a caller's outcome when
    it contradicts the brief's own declared lifecycle state (fencing passed,
    but the caller lies about WHAT happened: coordinator said timed_out_empty,
    decision claims delivered)."""
    store = RunStore(tmp_path / "store.db")
    try:
        store.create_run("s1", run_id="r1", state="QUEUED")
        brief = make_brief()
        store.enqueue_brief("r1", brief)
        # reality: the brief ended timed_out_empty; caller claims delivered
        brief.status = "timed_out_empty"
        lie = sb.BriefDecision(accepted=True, outcome="delivered")
        assert store.record_brief_result("r1", brief, lie) is False
        audits = store.audit("r1")
        assert any(
            a["action"] == "subagent_brief_result_rejected"
            and "outcome_lie" in a["detail"]
            for a in audits
        )
        row = [r for r in store.briefs("r1") if r["assignment_id"] == "a1"][-1]
        assert row["status"] != "delivered"
        # legitimate matching decision still persists
        truth = sb.BriefDecision(accepted=True, outcome="timed_out_empty")
        assert store.record_brief_result("r1", brief, truth) is True
    finally:
        store.close()
