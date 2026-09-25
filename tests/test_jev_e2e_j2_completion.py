"""JEV §30 — E2E-J2 Completion, including the counter-case.

ROT-FIRST evidence, captured before this delta existed (the pristine
pre-delta modules from `git show HEAD:src/godsv/...` on the path, so the
failures below are the real ones, not a reconstruction):

    $ .venv/bin/python -m pytest -o pythonpath=$HOME/jev_red/src \
        tests/test_jev_e2e_j2_completion.py -p no:randomly
    E   ImportError: cannot import name 'CompletionLedger' from
        'godsv.completion' (/home/willy/jev_red/src/godsv/completion.py)
    !!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
    1 error in 0.07s

The two §30 E2E-J2 arms:

    Hermes says done -> CompletionLedger -> Jev finds mock-only evidence
      -> targeted verifier -> criterion remains open -> no "completed"

    all current evidence valid -> Jev preflight -> independent verifier
      -> deterministic gate -> completed

Evidence class: **integration** — the real ledger, the real hard checks, the
real preflight question packs, the real ``GateRunner`` and the real
``CompletionStore`` run together against a real (temporary) repository. The
provider is a scripted replay fixture, so ``live_jev`` stays ``False``: this is
not a live-Jev proof (§14/§36).

Not proven here: the real System-2 verifier leg (an LLM-driven independent
review) — the counter-case drives the deterministic gate with runner-executed
evidence instead.
"""

from __future__ import annotations

import asyncio
import subprocess

from skaldir.completion import (
    INCOMPLETE,
    PRECHECK_ONLY_SOURCE,
    VERIFIED_COMPLETE,
    CompletionLedger,
    CompletionRequest,
    CompletionStore,
    DodItem,
    GateRunner,
    LedgerCriterion,
    RunEvidence,
    completion_preflight,
)
from skaldir.system_one import EvaluationContext
from skaldir.system_one_service import SystemOneService


class FixtureEvaluator:
    provider = "replay_fixture"
    provider_model = "scripted"
    provider_access_mode = "fixture"

    def __init__(self, answers) -> None:
        self.answers = dict(answers)
        self.calls = 0

    async def evaluate(self, *, state, questions, context):
        from skaldir.system_one import build_evaluation, parse_answer

        self.calls += 1
        parsed = {}
        for question in questions.questions:
            shape = self.answers.get(question.id)
            if shape is None:
                continue
            parsed[question.id] = parse_answer(
                question_id=question.id, kind=question.kind, payload=shape
            )
        return build_evaluation(
            context=context,
            questions=questions,
            state=state,
            provider=self.provider,
            provider_model=self.provider_model,
            access_mode=self.provider_access_mode,
            answers=parsed,
        )


SUPPORTED = {
    "completion_preflight.does_evidence_directly_support_requirement": {"value": 0.95},
    "completion_preflight.is_evidence_only_indirect": {"value": 0.05},
    "completion_preflight.is_live_requirement_supported_only_by_mock": {"value": 0.5},
    "completion_preflight.does_evidence_cover_current_behavior": {"value": 0.9},
    "completion_preflight.is_scope_of_proof_too_narrow": {"value": 0.1},
    "completion_preflight.does_contradicting_evidence_materially_challenge_pass": {"value": 0.05},
    "completion_preflight.is_independent_verification_still_needed": {"value": 0.05},
    "completion_preflight.is_criterion_semantically_unresolved": {"value": 0.05},
    "completion_preflight.evidence_fit": {
        "choice": "partial",
        "probabilities": {
            "direct": 0.2,
            "partial": 0.7,
            "irrelevant": 0.03,
            "contradictory": 0.02,
            "uncertain": 0.05,
        },
        "confidence": 0.7,
    },
}


def _git_repo(path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)  # noqa: S603,S607
    (path / "src").mkdir(exist_ok=True)
    (path / "src" / "feature.py").write_text("def feature():\n    return 1\n", encoding="utf-8")
    subprocess.run(  # noqa: S603,S607
        ["git", "-C", str(path), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(  # noqa: S603,S607
        ["git", "-C", str(path), "-c", "user.name=T", "-c", "user.email=t@example.invalid",
         "commit", "-qm", "init"],
        check=True,
        capture_output=True,
    )


def test_j2_mock_only_evidence_keeps_the_criterion_open(tmp_path):
    """Arm 1: Jev finds mock-only evidence → the criterion stays open."""
    _git_repo(tmp_path)
    ledger = CompletionLedger(
        criteria=(
            LedgerCriterion(
                criterion_id="c1",
                goal_revision=1,
                required=True,
                requirement_summary="the feature works in the real app",
                implementation_status="implemented",
                evidence_refs=("run-mock",),
                evidence_types=("mock",),
                evidence_revision=1,
                evidence_workspace_fingerprint="ws-1",
                independent_required=True,
                independent_present=False,
                live_required=True,
                live_present=False,
                last_relevant_change=0.0,
            ),
        )
    )
    mock_run = RunEvidence(
        name="fake_api_test",
        cmd="pytest tests/test_api_fake.py",
        ok=True,
        status="passed",
        check_id="pytest",
        source="runner",
        run_id="run-mock",
        revision=1,
        workspace="ws-1",
        session="s1",
        suite=True,
        evidence_type="mock",
        platform="linux",
    )
    service = SystemOneService(evaluator=FixtureEvaluator(SUPPORTED))
    preflight = asyncio.run(
        completion_preflight(
            ledger=ledger,
            runs={mock_run.run_id: mock_run},
            goal_revision=1,
            workspace_fingerprint="ws-1",
            platform="linux",
            workspace_root=str(tmp_path),
            service=service,
            context=EvaluationContext(run_id="run-1", goal_id="goal-1", caller="completion"),
        )
    )
    verdict = preflight.verdicts[0]
    assert verdict.decision in ("refuse", "escalate")
    assert "mock-provider-as-real-provider" in preflight.shortcut_codes or (
        "live-requirement-unsupported" in verdict.reasons
    )
    assert preflight.certifies_completion() is False

    # the targeted verifier runs, finds no certifying evidence, and the
    # deterministic gate stays INCOMPLETE
    store = CompletionStore(str(tmp_path / "state"), session_id="s1")
    store.begin_goal("s1", "the feature works in the real app", [DodItem(id="c1", text="works")])
    runner = GateRunner(
        repo_root=str(tmp_path),
        dod=[DodItem(id="c1", text="the feature works in the real app", verification="pytest")],
        revision=1,
        workspace="ws-1",
        session="s1",
    )
    result = runner.verify(
        CompletionRequest(user_goal="g", claimed_completed=["the feature works in the real app"]),
        [],
        store=store,
        precheck=preflight,
    )
    assert result.status == INCOMPLETE
    assert PRECHECK_ONLY_SOURCE in result.refusal_codes
    assert store.state()["state"] != VERIFIED_COMPLETE


def test_j2_counter_case_all_current_evidence_completes(tmp_path):
    """Counter-case: verified criteria → preflight → gate → VERIFIED_COMPLETE."""
    _git_repo(tmp_path)
    run = RunEvidence(
        name="pytest",
        cmd="pytest tests/test_x.py",
        ok=True,
        status="passed",
        check_id="pytest",
        source="runner",
        run_id="run-1",
        revision=1,
        workspace="ws-1",
        session="s1",
        suite=True,
        evidence_type="unit_test",
        platform="linux",
    )
    ledger = CompletionLedger(
        criteria=(
            LedgerCriterion(
                criterion_id="c1",
                goal_revision=1,
                required=True,
                requirement_summary="the suite passes",
                implementation_status="implemented",
                evidence_refs=("run-1",),
                evidence_types=("unit_test",),
                evidence_revision=1,
                evidence_workspace_fingerprint="ws-1",
                independent_required=True,
                independent_present=True,
                live_required=False,
                live_present=False,
                last_relevant_change=0.0,
            ),
        )
    )
    service = SystemOneService(evaluator=FixtureEvaluator(SUPPORTED))
    preflight = asyncio.run(
        completion_preflight(
            ledger=ledger,
            runs={run.run_id: run},
            goal_revision=1,
            workspace_fingerprint="ws-1",
            platform="linux",
            workspace_root=str(tmp_path),
            service=service,
            context=EvaluationContext(run_id="run-1", goal_id="goal-1", caller="completion"),
        )
    )
    assert preflight.verdicts[0].decision == "pass"
    assert preflight.hierarchy == "pass"
    assert preflight.certifies_completion() is False  # still never a certificate

    # the run was produced for session s1 (its own stamp) — the gate keeps that
    # binding, so the contract must be that session's
    store = CompletionStore(str(tmp_path / "state"), session_id="s1")
    store.begin_goal("s1", "the suite passes", [DodItem(id="c1", text="the suite passes")])
    runner = GateRunner(
        repo_root=str(tmp_path),
        dod=[DodItem(id="c1", text="the suite passes", verification="pytest")],
        revision=1,
        workspace="ws-1",
        session="s1",
    )
    result = runner.verify(
        CompletionRequest(user_goal="g", claimed_completed=["the suite passes"]),
        [run],
        store=store,
        precheck=preflight,
    )
    assert result.status == VERIFIED_COMPLETE
    assert store.state()["state"] == VERIFIED_COMPLETE


def test_j2_a_green_run_on_an_old_revision_is_not_a_completion(tmp_path):
    """Counter-counter-case: stale evidence must not complete."""
    _git_repo(tmp_path)
    stale = RunEvidence(
        name="pytest",
        cmd="pytest tests/test_x.py",
        ok=True,
        status="passed",
        check_id="pytest",
        source="runner",
        run_id="run-old",
        revision=1,
        workspace="ws-1",
        session="s3",
        suite=True,
        evidence_type="unit_test",
        platform="linux",
    )
    ledger = CompletionLedger(
        criteria=(
            LedgerCriterion(
                criterion_id="c1",
                goal_revision=2,  # the criterion moved on; the run did not
                required=True,
                requirement_summary="the suite passes",
                evidence_refs=("run-old",),
                evidence_revision=1,
                evidence_workspace_fingerprint="ws-1",
                last_relevant_change=0.0,
            ),
        )
    )
    service = SystemOneService(evaluator=FixtureEvaluator(SUPPORTED))
    preflight = asyncio.run(
        completion_preflight(
            ledger=ledger,
            runs={stale.run_id: stale},
            goal_revision=2,
            workspace_fingerprint="ws-1",
            platform="linux",
            workspace_root=str(tmp_path),
            service=service,
            context=EvaluationContext(run_id="run-1", goal_id="goal-1"),
        )
    )
    assert preflight.verdicts[0].decision == "refuse"
    assert "goal-revision-mismatch" in preflight.verdicts[0].hard_refusals


def test_j2_the_preflight_never_needs_a_provider_to_refuse_structure(tmp_path):
    """§9.3: a broken ledger refuses without any model call at all."""
    _git_repo(tmp_path)
    evaluator = FixtureEvaluator(SUPPORTED)
    service = SystemOneService(evaluator=evaluator)
    ledger = CompletionLedger(
        criteria=(LedgerCriterion(criterion_id="c1", goal_revision=1, evidence_refs=()),)
    )
    asyncio.run(
        completion_preflight(
            ledger=ledger,
            runs={},
            goal_revision=1,
            workspace_fingerprint="ws-1",
            platform="linux",
            workspace_root=str(tmp_path),
            service=service,
            context=EvaluationContext(run_id="run-1", goal_id="goal-1"),
        )
    )
    assert evaluator.calls == 0


def test_j2_live_jev_is_false_even_when_the_fixture_really_ran(tmp_path):
    """A scripted fixture is `replay_fixture` — never a live Jev proof (§14)."""
    _git_repo(tmp_path)
    run = RunEvidence(
        name="pytest",
        cmd="pytest tests/test_x.py",
        ok=True,
        status="passed",
        check_id="pytest",
        source="runner",
        run_id="run-1",
        revision=1,
        workspace="ws-1",
        session="s1",
        suite=True,
        evidence_type="unit_test",
        platform="linux",
    )
    service = SystemOneService(evaluator=FixtureEvaluator(SUPPORTED))
    ledger = CompletionLedger(
        criteria=(
            LedgerCriterion(
                criterion_id="c1",
                goal_revision=1,
                required=True,
                requirement_summary="the suite passes",
                evidence_refs=("run-1",),
                evidence_types=("unit_test",),
                evidence_revision=1,
                evidence_workspace_fingerprint="ws-1",
            ),
        )
    )
    preflight = asyncio.run(
        completion_preflight(
            ledger=ledger,
            runs={run.run_id: run},
            goal_revision=1,
            workspace_fingerprint="ws-1",
            platform="linux",
            workspace_root=str(tmp_path),
            service=service,
            context=EvaluationContext(run_id="run-1", goal_id="goal-1"),
        )
    )
    assert preflight.evaluated is True
    assert preflight.provider_mode == "replay_fixture"
    assert preflight.live_jev is False
    assert preflight.as_dict()["certifies_completion"] is False


def test_j2_no_provider_runs_when_the_structure_already_refuses(tmp_path):
    """§9.3: no model call happens for a criterion that cannot pass structurally."""
    _git_repo(tmp_path)
    evaluator = FixtureEvaluator(SUPPORTED)
    service = SystemOneService(evaluator=evaluator)
    ledger = CompletionLedger(
        criteria=(LedgerCriterion(criterion_id="c1", goal_revision=1, evidence_refs=()),)
    )
    preflight = asyncio.run(
        completion_preflight(
            ledger=ledger,
            runs={},
            goal_revision=1,
            workspace_fingerprint="ws-1",
            platform="linux",
            workspace_root=str(tmp_path),
            service=service,
            context=EvaluationContext(run_id="run-1", goal_id="goal-1"),
        )
    )
    assert evaluator.calls == 0
    assert preflight.evaluated is False
    # no provider ran, so there is no provider_mode to report — and none invented
    assert preflight.provider_mode == ""
    assert preflight.live_jev is False
