"""Evidenz-Bindung (Audit P1, 13.09.): Runner-Evidenz zertifiziert nur die
Revision und den Workspace, unter denen sie erzeugt wurde. Der belegte Fall:
alte pytest-Evidenz + neue Goal-Revision 2 => VERIFIED_COMPLETE."""

from skaldir.completion import (
    CompletionRequest,
    CompletionStore,
    DodItem,
    GateRunner,
    RunEvidence,
    execute_check,
)


def _ev(rev=0, ws="", status="passed"):
    return RunEvidence(
        name="pytest", check_id="pytest", source="runner", status=status,
        ok=(status == "passed"), cmd="python -m pytest -q", revision=rev, workspace=ws,
    )


def _runner(**kw):
    dod = [DodItem(id="c1", text="tests green", blocking=True, verification="pytest")]
    return GateRunner(repo_root="/nonexistent-gv-audit-repo", dod=dod, **kw)


def test_same_revision_evidence_certifies():
    assert _runner(revision=1).gate_dod(CompletionRequest(user_goal="g"), [_ev(rev=1)]) == []


def test_stale_evidence_does_not_certify_new_revision():
    findings = _runner(revision=2).gate_dod(CompletionRequest(user_goal="g"), [_ev(rev=1)])
    assert findings and findings[0].requirement == "c1"


def test_unbound_evidence_does_not_certify_a_revisioned_contract():
    """Der Audit-Fall: Evidence-Revision 0 gegen Goal-Revision 2."""
    findings = _runner(revision=2).gate_dod(CompletionRequest(user_goal="g"), [_ev(rev=0)])
    assert findings


def test_other_workspace_does_not_certify():
    assert _runner(revision=1, workspace="/a").gate_dod(
        CompletionRequest(user_goal="g"), [_ev(rev=1, ws="/b")]
    )
    assert _runner(revision=1, workspace="/a").gate_dod(
        CompletionRequest(user_goal="g"), [_ev(rev=1, ws="/a")]
    ) == []


def test_verify_inherits_revision_from_store(tmp_path):
    store = CompletionStore(tmp_path / "rt", session_id="eb1")
    dod = [DodItem(id="c1", text="t", blocking=True, verification="pytest")]
    store.begin_goal(session_id="eb1", goal="g", dod=dod)
    rev = int(store.state().get("revision") or 0)
    assert rev >= 1, "contract revision must be tracked"
    dod2 = [DodItem(id="c1", text="t", blocking=True, verification="pytest")]
    runner = GateRunner(repo_root=tmp_path, dod=dod2)
    res = runner.verify(
        CompletionRequest(user_goal="g", claimed_completed=["done"]),
        [_ev(rev=rev + 5)],
        store=store,
    )
    # the store records c1 as failed: the bound gate judged it, old evidence did not certify
    assert store.state()["dod"][0]["status"] == "failed", "old evidence must not certify via store"
    assert res.status != "verified_complete"


def test_execute_check_binds_revision_and_workspace(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    ev = execute_check("pytest", tmp_path, timeout=120, revision=7, workspace=str(tmp_path))
    assert ev is not None and ev.revision == 7 and ev.workspace == str(tmp_path)
