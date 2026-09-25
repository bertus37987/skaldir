"""Strict completion supervisor tests (Goal §1–§44).

Invariants under test:
* INCOMPLETE never ends a run; only the listed terminal states do.
* The main agent cannot self-certify — only gates over real facts/evidence decide.
* Blocking requirements cannot be silently dropped.
* VERIFIED_COMPLETE requires independent evidence.
* Repeating the same rejection escalates to a strategy change, never to "just mark it done".
"""

import subprocess

import pytest

from skaldir.completion import (
    BLOCKED_USER,
    COMPLETION_REQUESTED,
    INCOMPLETE,
    TERMINAL_STATES,
    USER_CANCELLED,
    VERIFIED_COMPLETE,
    WORKING,
    CompletionRequest,
    CompletionStore,
    DodItem,
    GateRunner,
    RepoFacts,
    RunEvidence,
    fingerprint_for,
)


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def repo(tmp_path):
    """A real git repo with one committed source file, then one change."""
    root = tmp_path / "repo"
    (root / "src" / "skaldir").mkdir(parents=True)
    (root / "src" / "skaldir" / "existing.py").write_text("def f():\n    return 1\n")
    (root / "README.md").write_text("demo\n")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    # one real change the supervisor must see
    target = root / "src" / "skaldir" / "existing.py"
    target.write_text("def f():\n    return 2\n")
    return root


def _evidence(name="pytest", ok=True, ts=None):
    return RunEvidence(name=name, cmd=f"{name} -q", ok=ok, detail="421 passed", ts=ts or 9e12)


def _request(**overrides):
    base = {
        "user_goal": "fix the thing",
        "claimed_completed": ["the thing is fixed"],
        "test_results": "421 passed",
    }
    base.update(overrides)
    return CompletionRequest(**base)


class TestStateMachine:
    def test_begin_goal_starts_working(self, tmp_path):
        store = CompletionStore(tmp_path / "rt")
        record = store.begin_goal("s1", "goal", [DodItem(id="d1", text="x", verification="pytest")])
        assert record["state"] == WORKING
        assert store.state()["goal"] == "goal"

    def test_terminal_states_are_terminal(self, tmp_path):
        store = CompletionStore(tmp_path / "rt")
        store.begin_goal("s1", "goal", [])
        store.transition(VERIFIED_COMPLETE)
        with pytest.raises(ValueError):
            store.transition(COMPLETION_REQUESTED)
        # a new goal resets
        store.begin_goal("s2", "next", [])
        assert store.state()["state"] == WORKING

    def test_completion_requested_is_not_terminal(self):
        assert COMPLETION_REQUESTED not in TERMINAL_STATES
        assert INCOMPLETE not in TERMINAL_STATES
        assert {BLOCKED_USER, USER_CANCELLED, VERIFIED_COMPLETE} <= set(TERMINAL_STATES)

    def test_unknown_state_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            CompletionStore(tmp_path / "rt").transition("DONE_MAYBE")


class TestGates:
    def test_missing_evidence_blocks(self, repo):
        runner = GateRunner(repo_root=repo)
        result = runner.verify(_request(), [])
        assert result.status == INCOMPLETE
        assert any("evidence" in failure for failure in result.blocking_failures)
        assert "Continue working now" in result.redirect_md()

    def test_code_change_without_test_run_blocks(self, repo):
        runner = GateRunner(repo_root=repo)
        result = runner.verify(_request(), [_evidence(name="ruff")])
        assert result.status == INCOMPLETE
        assert any("test" in failure for failure in result.blocking_failures)

    def test_failed_test_run_blocks(self, repo):
        result = GateRunner(repo_root=repo).verify(_request(), [_evidence(ok=False)])
        assert result.status == INCOMPLETE
        assert any("failed" in failure for failure in result.blocking_failures)

    def test_fake_completion_markers_detected(self, repo):
        target = repo / "src" / "skaldir" / "existing.py"
        target.write_text("def f():\n    return 2  # TODO: finish this\n")
        _git(repo, "add", "-A")
        result = GateRunner(repo_root=repo).verify(_request(), [_evidence()])
        assert result.status == INCOMPLETE
        assert any("TODO" in failure for failure in result.major_failures)

    def test_placeholder_marker_is_blocker(self, repo):
        target = repo / "src" / "skaldir" / "existing.py"
        target.write_text('def f():\n    return {"placeholder": True}\n')
        _git(repo, "add", "-A")
        result = GateRunner(repo_root=repo).verify(_request(), [_evidence()])
        assert result.status == INCOMPLETE
        assert any("placeholder" in failure for failure in result.blocking_failures)

    def test_orphan_module_blocks(self, repo):
        orphan = repo / "src" / "skaldir" / "orphan_module.py"
        orphan.write_text("VALUE = 1\n")
        _git(repo, "add", "-A")
        result = GateRunner(repo_root=repo).verify(_request(), [_evidence()])
        assert result.status == INCOMPLETE
        assert any("orphan_module" in failure for failure in result.blocking_failures)

    def test_secret_material_blocks(self, repo):
        target = repo / "src" / "skaldir" / "existing.py"
        target.write_text('TOKEN = "sk-ABCDEF1234567890abcdef"\n')
        _git(repo, "add", "-A")
        result = GateRunner(repo_root=repo).verify(_request(), [_evidence()])
        assert result.status == INCOMPLETE
        assert any("secret" in failure for failure in result.blocking_failures)

    def test_cleanup_findings_are_minor_only(self, repo):
        target = repo / "src" / "skaldir" / "existing.py"
        target.write_text("def f():\n    print('dbg')\n    return 2\n")
        _git(repo, "add", "-A")
        result = GateRunner(repo_root=repo).verify(_request(), [_evidence()])
        assert result.status == VERIFIED_COMPLETE
        assert any("debug print" in finding for finding in result.minor_findings)


class TestDefinitionOfDone:
    def test_blocking_dod_cannot_be_dropped(self, repo):
        runner = GateRunner(
            repo_root=repo,
            dod=[
                DodItem(
                    id="d1", text="UI verified in real app",
                    blocking=True, verification="ui_e2e",
                )
            ],
        )
        result = runner.verify(_request(), [_evidence()])
        assert result.status == INCOMPLETE
        assert any("definition-of-done" in failure for failure in result.blocking_failures)

    def test_blocking_dod_verified_by_its_check(self, repo):
        runner = GateRunner(
            repo_root=repo,
            dod=[DodItem(id="d1", text="suite green", blocking=True, verification="pytest")],
        )
        result = runner.verify(_request(), [_evidence(name="pytest")])
        assert result.status == VERIFIED_COMPLETE
        assert result.verified_requirements

    def test_nonblocking_dod_does_not_block(self, repo):
        runner = GateRunner(
            repo_root=repo,
            dod=[
                DodItem(
                    id="d9", text="nice-to-have polish",
                    blocking=False, verification="ui_e2e",
                )
            ],
        )
        result = runner.verify(_request(), [_evidence()])
        assert result.status == VERIFIED_COMPLETE
        assert any("nice-to-have" in finding for finding in result.minor_findings)


class TestLoop:
    def test_incomplete_returns_control_and_escalates_on_repeat(self, repo, tmp_path):
        store = CompletionStore(tmp_path / "rt")
        store.begin_goal("s1", "goal", [])
        runner = GateRunner(repo_root=repo)

        first = runner.verify(_request(), [], store=store)
        assert first.status == INCOMPLETE
        assert first.escalate is False
        assert first.next_actions and "Continue working now" in first.redirect_md()

        second = runner.verify(_request(), [], store=store)
        assert second.status == INCOMPLETE
        assert second.escalate is True, "same rejection twice must change strategy"
        assert any("STOP repeating" in action for action in second.next_actions)

    def test_fingerprint_is_stable_across_runs(self):
        assert fingerprint_for(["a", "b"]) == fingerprint_for(["b", "a"])
        assert fingerprint_for(["a"]) != fingerprint_for(["a", "b"])

    def test_metrics_track_loop(self, repo, tmp_path):
        store = CompletionStore(tmp_path / "rt")
        store.begin_goal("s1", "goal", [])
        runner = GateRunner(repo_root=repo)
        runner.verify(_request(), [], store=store)  # rejection
        runner.verify(_request(), [_evidence()], store=store)  # verified
        metrics = store.metrics()
        assert metrics["completion_requests"] == 2
        assert metrics["completion_rejections"] == 1
        assert metrics["verified_completions"] == 1
        assert metrics["false_completion_prevented"] == 1
        assert metrics["requirements_verified"] >= 1

    def test_verified_complete_is_terminal(self, repo, tmp_path):
        store = CompletionStore(tmp_path / "rt")
        store.begin_goal("s1", "goal", [])
        result = GateRunner(repo_root=repo).verify(_request(), [_evidence()], store=store)
        assert result.status == VERIFIED_COMPLETE
        assert result.terminal
        assert store.state()["state"] == VERIFIED_COMPLETE

    def test_timeline_records_the_loop(self, repo, tmp_path):
        store = CompletionStore(tmp_path / "rt")
        store.begin_goal("s1", "goal", [])
        runner = GateRunner(repo_root=repo)
        runner.verify(_request(), [], store=store)
        runner.verify(_request(), [_evidence()], store=store)
        kinds = [row["kind"] for row in store.timeline()]
        assert "completion_request" in kinds and "verification" in kinds


class TestRepoFacts:
    def test_collect_sees_changes(self, repo):
        facts = RepoFacts.collect(repo)
        assert "src/skaldir/existing.py" in facts.changed_files

    def test_collect_empty_on_missing_git(self, tmp_path):
        facts = RepoFacts.collect(tmp_path / "nope")
        assert facts.changed_files == []


class TestAddCriteria:
    """`@goal` chat mentions: criteria accumulate on the active goal."""
    def test_appends_blocking_criteria_with_continuing_ids(self, tmp_path):
        store = CompletionStore(tmp_path / "rt")
        store.begin_goal(
            "s1", "ship it", [DodItem(id="c1", text="first", blocking=True)]
        )
        record, added = store.add_criteria(["second", "third"])
        assert added == ["second", "third"]
        dod = record["dod"]
        assert [item["id"] for item in dod] == ["c1", "c2", "c3"]
        assert all(item["blocking"] is True for item in dod)
        assert record["goal"] == "ship it" and record["session_id"] == "s1"

    def test_deduplicates_case_insensitively(self, tmp_path):
        store = CompletionStore(tmp_path / "rt")
        store.begin_goal("s1", "goal", [DodItem(id="c1", text="Survive Restart")])
        record, added = store.add_criteria(["survive restart", "new one"])
        assert added == ["new one"]
        assert len(record["dod"]) == 2

    def test_terminal_goal_refuses_more_criteria(self, tmp_path):
        store = CompletionStore(tmp_path / "rt")
        store.begin_goal("s1", "goal", [])
        store.transition(VERIFIED_COMPLETE)
        with pytest.raises(ValueError):
            store.add_criteria(["too late"])


class TestGoalEdit:
    """Inline goal edit (chat strip) — goal text changes, everything else stays."""

    def test_begin_goal_records_started_timestamp(self, tmp_path):
        store = CompletionStore(tmp_path / "rt")
        record = store.begin_goal("s1", "ship it", [])
        assert isinstance(record.get("started"), float) and record["started"] > 0

    def test_set_goal_keeps_criteria_and_session(self, tmp_path):
        store = CompletionStore(tmp_path / "rt")
        store.begin_goal("s1", "old goal", [DodItem(id="c1", text="a")])
        store.add_criteria(["b"])
        record = store.set_goal("  new goal  ")
        assert record["goal"] == "new goal"
        assert [item["text"] for item in record["dod"]] == ["a", "b"]
        assert record["session_id"] == "s1"

    def test_set_goal_refuses_terminal_and_empty(self, tmp_path):
        store = CompletionStore(tmp_path / "rt")
        store.begin_goal("s1", "goal", [])
        with pytest.raises(ValueError):
            store.set_goal("   ")
        store.transition(VERIFIED_COMPLETE)
        with pytest.raises(ValueError):
            store.set_goal("later")

def test_redirect_md_teaches_the_blocked_user_convention():
    """The redirect carries the honest-stop convention to the main agent:
    a `BLOCKED_USER:` status line ends the loop, prose alone does not (B5)."""
    from skaldir.completion import INCOMPLETE, VERIFIED_COMPLETE, VerificationResult

    rejected = VerificationResult(
        status=INCOMPLETE,
        blocking_failures=["dod: definition-of-done not verified: x"],
    )
    text = rejected.redirect_md()
    assert "BLOCKED_USER:" in text
    assert "prose alone does not stop the loop" in text

    passed = VerificationResult(status=VERIFIED_COMPLETE)
    assert "BLOCKED_USER:" not in passed.redirect_md()

def test_redirect_md_keeps_the_user_posted_with_the_live_block():
    from skaldir.completion import INCOMPLETE, VerificationResult

    text = VerificationResult(status=INCOMPLETE, blocking_failures=["x"]).redirect_md()
    assert "::godsv-live" in text


# -- T3: DoD-Outcome-Lifecycle (Audit-Fix) ------------------------------------


def test_verify_records_dod_outcomes(tmp_path):
    from skaldir.completion import (
        INCOMPLETE,
        CompletionRequest,
        CompletionStore,
        DodItem,
        GateRunner,
        RunEvidence,
    )

    store = CompletionStore(tmp_path / "rt", session_id="t3")
    items = [
        DodItem(id="c1", text="tests pass", verification="pytest"),
        DodItem(id="c2", text="no plan", verification=""),
    ]
    store.begin_goal(session_id="t3", goal="outcomes", dod=items)
    evidence = [
        RunEvidence(
            name="pytest", cmd="pytest -q", ok=True, status="passed",
            check_id="pytest", source="runner", ts=9e12, revision=1,
        )
    ]
    result = GateRunner(repo_root=tmp_path / "repo", dod=items).verify(
        CompletionRequest(user_goal="outcomes", claimed_completed=["done"]),
        evidence,
        store=store,
    )
    assert result.status == INCOMPLETE
    statuses = {item["id"]: item["status"] for item in store.state()["dod"]}
    assert statuses["c1"] == "verified"
    assert statuses["c2"] == "failed"


def test_fully_verified_run_marks_all_verified(tmp_path):
    from skaldir.completion import (
        VERIFIED_COMPLETE,
        CompletionRequest,
        CompletionStore,
        DodItem,
        GateRunner,
        RunEvidence,
    )

    store = CompletionStore(tmp_path / "rt", session_id="t3b")
    items = [DodItem(id="c1", text="tests pass", verification="pytest")]
    store.begin_goal(session_id="t3b", goal="green", dod=items)
    evidence = [
        RunEvidence(
            name="pytest", cmd="pytest -q", ok=True, status="passed",
            check_id="pytest", source="runner", ts=9e12, revision=1,
        )
    ]
    result = GateRunner(repo_root=tmp_path / "repo", dod=items).verify(
        CompletionRequest(user_goal="green", claimed_completed=["done"]),
        evidence,
        store=store,
    )
    assert result.status == VERIFIED_COMPLETE, (
        result.blocking_failures, result.major_failures, result.minor_findings
    )
    assert store.state()["dod"][0]["status"] == "verified"


def test_persisted_verified_status_never_skips_fresh_verification(tmp_path):
    """NEU-hoch: a persisted 'verified' must not certify a later run by itself."""
    from skaldir.completion import (
        VERIFIED_COMPLETE,
        CompletionRequest,
        CompletionStore,
        DodItem,
        GateRunner,
        RunEvidence,
    )

    store = CompletionStore(tmp_path / "rt", session_id="t3c")
    items = [
        DodItem(id="c1", text="tests pass", verification="pytest"),
        DodItem(id="c2", text="build ok", verification="build"),
    ]
    store.begin_goal(session_id="t3c", goal="g", dod=items)

    # Lauf 1: nur c1 belegt -> c1 verified, c2 failed, Vertrag bleibt INCOMPLETE
    r1 = GateRunner(repo_root=tmp_path / "repo", dod=items).verify(
        CompletionRequest(user_goal="g", claimed_completed=["done"]),
        [RunEvidence(name="pytest", cmd="pytest -q", ok=True, status="passed",
                     check_id="pytest", source="runner", ts=9e12, revision=1)],
        store=store,
    )
    assert r1.status != VERIFIED_COMPLETE
    statuses = {i["id"]: i["status"] for i in store.state()["dod"]}
    assert statuses == {"c1": "verified", "c2": "failed"}

    # Lauf 2: nur c2 hat FRISCHE Evidenz — c1 muss erneut geprüft werden
    persisted = {i["id"]: i for i in store.state()["dod"]}
    items2 = [DodItem(**persisted["c1"]), DodItem(**persisted["c2"])]
    r2 = GateRunner(repo_root=tmp_path / "repo", dod=items2).verify(
        CompletionRequest(user_goal="g", claimed_completed=["done"]),
        [RunEvidence(name="build", cmd="make", ok=True, status="passed",
                     check_id="build", source="runner", ts=9e13, revision=1)],
        store=store,
    )
    assert r2.status != VERIFIED_COMPLETE, "stale verified status must not certify"
    statuses2 = {i["id"]: i["status"] for i in store.state()["dod"]}
    assert statuses2["c1"] == "failed", "c1 must be re-evaluated, not skipped"


def test_minor_finding_keeps_criterion_open_not_verified(tmp_path):
    """NEU-niedrig: MINOR findings must not mark a criterion 'verified'."""
    from skaldir.completion import (
        CompletionRequest,
        CompletionStore,
        DodItem,
        GateRunner,
        RunEvidence,
    )

    store = CompletionStore(tmp_path / "rt", session_id="t3d")
    items = [DodItem(id="c1", text="no plan", verification="", blocking=False)]
    store.begin_goal(session_id="t3d", goal="g", dod=items)
    GateRunner(repo_root=tmp_path / "repo", dod=items).verify(
        CompletionRequest(user_goal="g", claimed_completed=["done"]),
        [RunEvidence(name="pytest", cmd="pytest -q", ok=True, status="passed",
                     check_id="pytest", source="runner", ts=9e12)],
        store=store,
    )
    assert store.state()["dod"][0]["status"] == "open"


def test_add_respects_expected_revision_and_request_id(tmp_path):
    """Audit P2: add used to ignore the revision fence — a stale add silently
    wrote revision 3. Now add has the same fence/idempotency contract as
    update/remove."""
    from skaldir.completion import CompletionStore, CriterionConflict, DodItem

    store = CompletionStore(tmp_path / "rt", session_id="t3d")
    store.begin_goal(
        session_id="t3d", goal="g",
        dod=[DodItem(id="c1", text="first", blocking=True, verification="pytest")],
    )
    store.update_criterion("c1", text="first v2", expected_revision=1)  # rev 2

    import pytest

    with pytest.raises(CriterionConflict):
        store.add_criteria(["late"], expected_revision=1)  # stale view → conflict

    rec, added = store.add_criteria(["fresh"], expected_revision=2)
    assert added == ["fresh"] and int(rec.get("revision") or 0) == 3

    rec2, a2 = store.add_criteria(["same"], expected_revision=3, request_id="rq-1")
    assert a2 == ["same"]
    rec3, a3 = store.add_criteria(["dup"], expected_revision=3, request_id="rq-1")
    assert rec3.get("replayed") is True and a3 == []
    assert int(rec3.get("revision") or 0) == int(rec2.get("revision") or 0)
