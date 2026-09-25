"""JEV §9 — CompletionLedger, hard checks, semantic preflight, shortcuts, gate.

ROT-FIRST evidence, captured before this delta existed (the pristine
pre-delta modules from `git show HEAD:src/godsv/...` on the path, so the
failures below are the real ones, not a reconstruction):

    $ .venv/bin/python -m pytest -o pythonpath=$HOME/jev_red/src \
        tests/test_jev_completion_ledger.py -p no:randomly
    E   ImportError: cannot import name 'COMPLETION_SANITY_IDS' from
        'godsv.completion' (/home/willy/jev_red/src/godsv/completion.py)
    !!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
    1 error in 0.10s

What this file pins (§9.1–§9.8):

* §9.2 — the per-criterion ledger fields (16) and the global fields (7) exist,
  and ``mandatory_criterion_count`` is aggregated in code;
* §9.3 — the nine hard checks run *before* any System-One call, and a missing
  structure refuses even when every model answer says "supported";
* §9.4 — the semantic preflight pack with the eight Noul questions and the
  optional Choice ``evidence_fit``;
* §9.5 — the shortcut detectors (own success marker, unchecked file, unit test
  as live use, mock as real provider, old green run, foreign workspace,
  self-certification, PASS without an outcome contract);
* §9.6 — hierarchical completion: Jev judges leaf/criterion level, parents are
  aggregated in code, and no "is the whole project done" question exists;
* §9.7 — the second sanity pass over the 9-state summary with the 7 questions;
* §9.8 — the gate stays deterministic: a preflight-only source cannot complete
  and a preflight can never certify.
"""

from __future__ import annotations

import asyncio

import pytest

from skaldir.completion import (
    BLOCKER,
    COMPLETION_SANITY_IDS,
    COMPLETION_SEMANTIC_IDS,
    HARD_CHECKS,
    HARD_REASONS,
    PRECHECK_CERTIFIES,
    PRECHECK_ONLY_SOURCE,
    SHORTCUT_CODES,
    CompletionLedger,
    CompletionPreflight,
    CompletionRequest,
    EvidenceFit,
    GateRunner,
    LedgerCriterion,
    RunEvidence,
    aggregate_criteria,
    build_ledger_summary,
    completion_preflight,
    detect_shortcuts,
    hard_checks,
)
from skaldir.system_one import PROVIDER_MODE_REPLAY_FIXTURE, EvaluationContext
from skaldir.system_one_questions import default_question_registry

# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #

CRIT = "c1"


def _run(**overrides) -> RunEvidence:
    base = {
        "name": "pytest",
        "cmd": "pytest tests/test_x.py",
        "ok": True,
        "status": "passed",
        "check_id": "pytest",
        "source": "runner",
        "run_id": "run-1",
        "revision": 1,
        "workspace": "ws-1",
        "session": "s1",
        "suite": True,
        "ts": 1000.0,
        "evidence_type": "unit_test",
        "platform": "linux",
    }
    base.update(overrides)
    return RunEvidence(**base)


def _criterion(**overrides) -> LedgerCriterion:
    base = {
        "criterion_id": CRIT,
        "goal_revision": 1,
        "required": True,
        "requirement_summary": "the feature works",
        "implementation_status": "implemented",
        "evidence_refs": ("run-1",),
        "evidence_types": ("unit_test",),
        "evidence_revision": 1,
        "evidence_workspace_fingerprint": "ws-1",
        "independent_required": True,
        "independent_present": True,
        "live_required": False,
        "live_present": False,
        "last_relevant_change": 900.0,
        "contradicting_evidence_refs": (),
        "blockers": (),
    }
    base.update(overrides)
    return LedgerCriterion(**base)


def _runs(*runs: RunEvidence) -> dict:
    return {run.run_id: run for run in runs}


class _ScriptedEvaluator:
    """A replay-fidelity provider double: scripted answers, no network."""

    provider = "replay_fixture"
    provider_model = "scripted"
    provider_access_mode = "fixture"

    def __init__(self, answers=None) -> None:
        self.answers = dict(answers or {})
        self.calls = 0

    async def evaluate(self, *, state, questions, context):
        from skaldir.system_one import build_evaluation, parse_answer

        self.calls += 1
        answers = {}
        for question in questions.questions:
            payload = self.answers.get(question.id)
            if payload is None:
                continue
            answers[question.id] = parse_answer(
                question_id=question.id, kind=question.kind, payload=payload
            )
        return build_evaluation(
            context=context,
            questions=questions,
            state=state,
            provider=self.provider,
            provider_model=self.provider_model,
            access_mode=self.provider_access_mode,
            answers=answers,
        )


def _supporting_answers(**overrides) -> dict:
    answers = {
        "completion_preflight.does_evidence_directly_support_requirement": {"value": 0.95},
        "completion_preflight.is_evidence_only_indirect": {"value": 0.05},
        "completion_preflight.is_live_requirement_supported_only_by_mock": {"value": 0.05},
        "completion_preflight.does_evidence_cover_current_behavior": {"value": 0.95},
        "completion_preflight.is_scope_of_proof_too_narrow": {"value": 0.05},
        "completion_preflight.does_contradicting_evidence_materially_challenge_pass": {
            "value": 0.05
        },
        "completion_preflight.is_independent_verification_still_needed": {"value": 0.05},
        "completion_preflight.is_criterion_semantically_unresolved": {"value": 0.05},
        "completion_preflight.evidence_fit": {
            "choice": "direct",
            "probabilities": {
                "direct": 0.9,
                "partial": 0.05,
                "irrelevant": 0.025,
                "contradictory": 0.015,
                "uncertain": 0.01,
            },
            "confidence": 0.9,
        },
    }
    answers.update(overrides)
    return answers


def _service(evaluator=None):
    from skaldir.system_one_service import SystemOneService

    return SystemOneService(evaluator=evaluator or _ScriptedEvaluator(_supporting_answers()))


# --------------------------------------------------------------------------- #
# §9.2 — the ledger                                                            #
# --------------------------------------------------------------------------- #


def test_the_per_criterion_ledger_carries_the_documented_fields():
    expected = {
        "criterion_id",
        "goal_revision",
        "required",
        "requirement_summary",
        "implementation_status",
        "evidence_refs",
        "evidence_types",
        "evidence_revision",
        "evidence_workspace_fingerprint",
        "independent_required",
        "independent_present",
        "live_required",
        "live_present",
        "last_relevant_change",
        "contradicting_evidence_refs",
        "blockers",
    }
    assert set(LedgerCriterion.__dataclass_fields__) == expected


def test_the_global_ledger_carries_the_documented_fields():
    expected = {
        "criteria",
        "unknown_side_effects",
        "open_blockers",
        "goal_revision",
        "workspace_fingerprint",
        "pending_external_operations",
        "unresolved_findings",
    }
    fields = set(CompletionLedger.__dataclass_fields__)
    assert expected <= fields
    # mandatory_criterion_count is aggregated in code, never supplied by a model
    ledger = CompletionLedger(
        criteria=(_criterion(), _criterion(criterion_id="c2", required=False))
    )
    # a code aggregation, never a model answer
    assert ledger.mandatory_criterion_count == 1
    assert ledger.as_dict()["mandatory_criterion_count"] == 1


def test_the_nine_hard_checks_of_9_3_are_named():
    assert set(HARD_CHECKS) == {
        "evidence_ref_exists",
        "run_exists",
        "criterion_id_exists",
        "goal_revision_matches",
        "workspace_fingerprint_matches",
        "artifact_exists",
        "runner_result_parsable",
        "required_platform_present",
        "evidence_newer_than_code_change",
    }
    assert len(set(HARD_REASONS.values())) == len(HARD_REASONS)


def test_a_complete_criterion_passes_every_hard_check():
    report = hard_checks(
        _criterion(),
        runs=_runs(_run()),
        workspace_root=".",
        platform="linux",
        known_criterion_ids={CRIT},
    )
    assert report.refusals == ()
    assert report.ok is True


@pytest.mark.parametrize(
    "criterion,runs,kwargs,reason",
    [
        (_criterion(evidence_refs=()), {"run-1": _run()}, {}, "evidence-ref-missing"),
        (_criterion(evidence_refs=("ghost",)), {"run-1": _run()}, {}, "run-missing"),
        (
            _criterion(),
            {"run-1": _run()},
            {"known_criterion_ids": {"c9"}},
            "criterion-id-missing",
        ),
        (
            _criterion(goal_revision=1),
            {"run-1": _run()},
            {"live_goal_revision": 3},
            "goal-revision-mismatch",
        ),
        (
            _criterion(evidence_workspace_fingerprint="ws-1"),
            {"run-1": _run()},
            {"live_workspace_fingerprint": "ws-2"},
            "workspace-fingerprint-mismatch",
        ),
        (
            _criterion(),
            {"run-1": _run(artifact="does/not/exist.py")},
            {},
            "artifact-missing",
        ),
        (
            _criterion(),
            {"run-1": _run(status="something_else")},
            {},
            "runner-result-unparsable",
        ),
        (
            _criterion(live_required=True, live_present=True),
            {"run-1": _run(platform="darwin")},
            {"platform": "linux"},
            "required-platform-missing",
        ),
        (
            _criterion(last_relevant_change=1100.0),
            {"run-1": _run(ts=1000.0)},
            {},
            "evidence-older-than-relevant-change",
        ),
    ],
)
def test_each_hard_check_refuses_the_matching_structural_defect(
    criterion, runs, kwargs, reason
):
    options = {
        "workspace_root": ".",
        "platform": "linux",
        "known_criterion_ids": {CRIT},
    }
    options.update(kwargs)
    report = hard_checks(criterion, runs=runs, **options)
    assert reason in report.refusals


def test_a_hard_refusal_can_never_be_overwritten_by_a_model_answer():
    """§9.3: a missing structure refuses, whatever Jev thinks."""
    ledger = CompletionLedger(criteria=(_criterion(evidence_refs=()),))
    preflight = asyncio.run(
        completion_preflight(
            ledger=ledger,
            runs={},
            goal_revision=1,
            workspace_fingerprint="ws-1",
            platform="linux",
            workspace_root=".",
            service=_service(_ScriptedEvaluator(_supporting_answers())),
            context=EvaluationContext(run_id="r", goal_id="g"),
        )
    )
    verdict = preflight.verdicts[0]
    assert verdict.decision == "refuse"
    assert "evidence-ref-missing" in verdict.hard_refusals
    assert preflight.certifies_completion() is False
    assert preflight.advisory_only is True
    assert "hard_check_refused" in preflight.signals


def test_a_hard_refusal_does_not_even_ask_the_provider_for_that_criterion():
    """Hard checks come first — no model call is needed to refuse structure."""
    from skaldir.completion import completion_preflight

    evaluator = _ScriptedEvaluator(_supporting_answers())
    ledger = CompletionLedger(criteria=(_criterion(evidence_refs=()),))
    asyncio.run(
        completion_preflight(
            ledger=ledger,
            runs={},
            goal_revision=1,
            workspace_fingerprint="ws-1",
            platform="linux",
            workspace_root=".",
            service=_service(evaluator),
            context=EvaluationContext(run_id="r", goal_id="g"),
        )
    )
    # §9.3: with every criterion structurally refused there is nothing to judge
    assert evaluator.calls == 0


# --------------------------------------------------------------------------- #
# §9.4 — the semantic preflight question pack                                  #
# --------------------------------------------------------------------------- #


def test_the_eight_semantic_questions_and_the_optional_choice_exist():
    registry = default_question_registry()
    pack = registry.pack("completion_preflight")
    ids = set(pack.ids())
    assert {
        "completion_preflight.does_evidence_directly_support_requirement",
        "completion_preflight.is_evidence_only_indirect",
        "completion_preflight.is_live_requirement_supported_only_by_mock",
        "completion_preflight.does_evidence_cover_current_behavior",
        "completion_preflight.is_scope_of_proof_too_narrow",
        "completion_preflight.does_contradicting_evidence_materially_challenge_pass",
        "completion_preflight.is_independent_verification_still_needed",
        "completion_preflight.is_criterion_semantically_unresolved",
    } <= ids
    assert set(COMPLETION_SEMANTIC_IDS) <= ids
    fit = registry.resolve("completion_preflight.evidence_fit")
    assert fit.kind == "choice"
    assert set(fit.options) == {"direct", "partial", "irrelevant", "contradictory", "uncertain"}
    assert EvidenceFit.OPTIONS == fit.options


def test_a_mock_only_live_requirement_is_flagged_not_accepted():
    """§9.4/E2E-J2: a live requirement carried by a unit test stays open."""
    ledger = CompletionLedger(
        criteria=(
            _criterion(
                live_required=True,
                live_present=False,
                evidence_types=("unit_test",),
            ),
        )
    )
    preflight = asyncio.run(
        completion_preflight(
            ledger=ledger,
            runs=_runs(_run(evidence_type="unit_test")),
            goal_revision=1,
            workspace_fingerprint="ws-1",
            platform="linux",
            workspace_root=".",
            service=_service(_ScriptedEvaluator(_supporting_answers())),
            context=EvaluationContext(run_id="r", goal_id="g"),
        )
    )
    verdict = preflight.verdicts[0]
    assert verdict.decision in ("refuse", "escalate")
    assert "unit-test-as-live-use" in preflight.shortcut_codes


def test_a_contradictory_evidence_fit_escalates():
    answers = _supporting_answers(
        **{
            "completion_preflight.evidence_fit": {
                "choice": "contradictory",
                "probabilities": {
                    "direct": 0.02,
                    "partial": 0.03,
                    "irrelevant": 0.05,
                    "contradictory": 0.85,
                    "uncertain": 0.05,
                },
                "confidence": 0.85,
            }
        }
    )
    ledger = CompletionLedger(criteria=(_criterion(),))
    preflight = asyncio.run(
        completion_preflight(
            ledger=ledger,
            runs=_runs(_run()),
            goal_revision=1,
            workspace_fingerprint="ws-1",
            platform="linux",
            workspace_root=".",
            service=_service(_ScriptedEvaluator(answers)),
            context=EvaluationContext(run_id="r", goal_id="g"),
        )
    )
    assert preflight.verdicts[0].decision == "escalate"
    assert preflight.verdicts[0].evidence_fit == "contradictory"


# --------------------------------------------------------------------------- #
# §9.5 — shortcut detectors                                                    #
# --------------------------------------------------------------------------- #


def test_the_eight_shortcuts_of_9_5_are_named():
    assert set(SHORTCUT_CODES) == {
        "own-success-marker",
        "file-exists-content-unchecked",
        "unit-test-as-live-use",
        "mock-provider-as-real-provider",
        "old-green-run-on-old-revision",
        "evidence-from-another-workspace",
        "criterion-supported-only-by-its-own-completion-text",
        "pass-marker-without-outcome-contract",
    }


def test_an_old_green_run_on_an_old_revision_is_caught():
    found = detect_shortcuts(
        _criterion(goal_revision=2),
        runs=_runs(_run(revision=1)),
        claim_text="",
        completion_text="",
    )
    assert "old-green-run-on-old-revision" in found


def test_evidence_from_another_workspace_is_caught():
    found = detect_shortcuts(
        _criterion(evidence_workspace_fingerprint="ws-1"),
        runs=_runs(_run(workspace="ws-OTHER")),
        claim_text="",
        completion_text="",
    )
    assert "evidence-from-another-workspace" in found


def test_a_criterion_supported_only_by_its_own_completion_text_is_self_certification():
    from skaldir.completion import detect_shortcuts

    text = "the feature works because I implemented it"
    found = detect_shortcuts(
        _criterion(evidence_refs=("claim-1",)),
        runs={"claim-1": _run(source="events", cmd="", evidence_type="claim_text")},
        claim_text=text,
        completion_text=text,
    )
    assert "own-success-marker" in found or (
        "criterion-supported-only-by-its-own-completion-text" in found
    )


def test_a_file_that_exists_without_a_content_check_is_caught():
    """§9.5 "Datei existiert, Inhalt nicht geprüft" — presence is not proof."""
    found = detect_shortcuts(
        _criterion(evidence_types=("file_exists",)),
        runs=_runs(_run(source="events", cmd="", evidence_type="file_exists")),
        claim_text="",
        completion_text="",
    )
    assert "file-exists-content-unchecked" in found


def test_a_unit_test_carried_as_computer_use_is_caught():
    """§9.5 "Unit-Test als Live-Computer-Use"."""
    found = detect_shortcuts(
        _criterion(live_required=True, live_present=False, evidence_types=("unit_test",)),
        runs=_runs(_run(evidence_type="unit_test")),
        claim_text="",
        completion_text="",
    )
    assert "unit-test-as-live-use" in found


def test_a_mock_carried_as_a_real_provider_is_caught():
    """§9.5 "Mock Provider als echter Provider"."""
    found = detect_shortcuts(
        _criterion(live_required=True, live_present=False, evidence_types=("mock",)),
        runs=_runs(_run(evidence_type="mock", source="events")),
        claim_text="",
        completion_text="",
    )
    assert "mock-provider-as-real-provider" in found


def test_every_shortcut_code_of_9_5_has_a_detector_that_can_fire():
    """All eight codes are reachable — none is a constant that never fires."""
    def crit(**overrides):
        base = {"evidence_refs": ("r1",)}
        base.update(overrides)
        return _criterion(**base)

    claim_run = _run(source="events", evidence_type="claim_text", cmd="")
    live_gap = {"live_required": True, "live_present": False}
    cases = [
        (crit(evidence_types=("claim_text",)), claim_run, "own-success-marker"),
        (
            crit(evidence_types=("file_exists",)),
            _run(source="events", cmd="", evidence_type="file_exists"),
            "file-exists-content-unchecked",
        ),
        (
            crit(evidence_types=("unit_test",), **live_gap),
            _run(evidence_type="unit_test"),
            "unit-test-as-live-use",
        ),
        (
            crit(evidence_types=("mock",), **live_gap),
            _run(evidence_type="mock", source="events"),
            "mock-provider-as-real-provider",
        ),
        (crit(goal_revision=2), _run(revision=1), "old-green-run-on-old-revision"),
        (crit(), _run(workspace="ws-OTHER"), "evidence-from-another-workspace"),
        (
            crit(evidence_types=("claim_text",)),
            claim_run,
            "criterion-supported-only-by-its-own-completion-text",
        ),
        (
            crit(),
            _run(source="events", status="passed", outcome_contract=""),
            "pass-marker-without-outcome-contract",
        ),
    ]
    for criterion, evidence_run, expected in cases:
        found = detect_shortcuts(
            criterion,
            runs={"r1": evidence_run},
            claim_text="same text",
            completion_text="same text",
        )
        assert expected in found, f"{expected} unreachable: {found}"


def test_a_pass_marker_without_an_outcome_contract_is_caught():
    found = detect_shortcuts(
        _criterion(),
        runs=_runs(
            _run(source="events", detail="PASS", status="passed", outcome_contract="")
        ),
        claim_text="",
        completion_text="",
    )
    assert "pass-marker-without-outcome-contract" in found


def test_a_real_runner_result_is_not_flagged_as_a_shortcut():
    found = detect_shortcuts(
        _criterion(),
        runs=_runs(_run()),
        claim_text="done",
        completion_text="done",
    )
    assert found == ()


# --------------------------------------------------------------------------- #
# §9.6 — hierarchical completion                                               #
# --------------------------------------------------------------------------- #


def test_parents_are_aggregated_in_code_and_never_asked_of_the_model():
    leaves = (
        {"criterion_id": "c1", "decision": "pass"},
        {"criterion_id": "c2", "decision": "pass"},
    )
    assert aggregate_criteria(leaves) == "pass"
    assert aggregate_criteria(leaves + ({"criterion_id": "c3", "decision": "open"},)) == "open"
    assert (
        aggregate_criteria(leaves + ({"criterion_id": "c3", "decision": "refuse"},)) == "refuse"
    )
    assert aggregate_criteria(()) == "unknown"


def test_no_question_asks_whether_the_whole_project_is_done():
    registry = default_question_registry()
    for pack in registry.packs():
        for question in pack.questions:
            lowered = question.text.lower()
            assert "whole project" not in lowered
            assert "gesamte projekt" not in lowered
            assert "project done" not in lowered
            assert lowered.count("?") == 1


def test_the_preflight_never_asks_a_criterion_less_question():
    for question_id in COMPLETION_SEMANTIC_IDS:
        assert question_id.startswith("completion_preflight.")


# --------------------------------------------------------------------------- #
# §9.7 — the second sanity pass                                                #
# --------------------------------------------------------------------------- #


def test_the_nine_state_summary_and_the_seven_sanity_questions_exist():
    summary = build_ledger_summary(
        (
            _criterion(criterion_id="c1"),
            _criterion(criterion_id="c2", implementation_status="failed"),
        ),
        verdicts=(
            {"criterion_id": "c1", "decision": "pass"},
            {"criterion_id": "c2", "decision": "refuse"},
        ),
        unknown_side_effects=("unknown-db-write",),
        unresolved_findings=("P0: data loss",),
    )
    assert set(summary) >= {
        "verified",
        "failed",
        "unknown",
        "stale",
        "mock_only",
        "blocked",
        "unknown_side_effects",
        "unresolved_p0_p1",
    }
    assert summary["verified"] == ["c1"]
    assert summary["failed"] == ["c2"]
    assert summary["unknown_side_effects"] == ["unknown-db-write"]
    assert summary["unresolved_p0_p1"] == ["P0: data loss"]

    registry = default_question_registry()
    pack = registry.pack("completion_sanity")
    assert set(pack.ids()) >= set(COMPLETION_SANITY_IDS)
    assert len(COMPLETION_SANITY_IDS) == 7


def test_a_sanity_gap_escalates_the_preflight():
    answers = dict(_supporting_answers())
    answers.update(
        {
            "completion_sanity.any_mandatory_gap": {"value": 0.9},
            "completion_sanity.any_stale_verification": {"value": 0.1},
            "completion_sanity.any_mock_only_live_requirement": {"value": 0.1},
            "completion_sanity.any_unresolved_contradiction": {"value": 0.1},
            "completion_sanity.any_changed_artifact_after_verification": {"value": 0.1},
            "completion_sanity.any_unknown_external_side_effect": {"value": 0.1},
            "completion_sanity.any_circular_self_certification": {"value": 0.1},
        }
    )
    ledger = CompletionLedger(criteria=(_criterion(),))
    preflight = asyncio.run(
        completion_preflight(
            ledger=ledger,
            runs=_runs(_run()),
            goal_revision=1,
            workspace_fingerprint="ws-1",
            platform="linux",
            workspace_root=".",
            service=_service(_ScriptedEvaluator(answers)),
            context=EvaluationContext(run_id="r", goal_id="g"),
        )
    )
    assert "sanity_pass_escalated" in preflight.signals
    assert preflight.sanity["any_mandatory_gap"] >= 0.5
    assert preflight.certifies_completion() is False


# --------------------------------------------------------------------------- #
# §9.8 — the gate stays deterministic                                          #
# --------------------------------------------------------------------------- #


def test_a_preflight_is_never_a_certificate():
    ledger = CompletionLedger(criteria=(_criterion(),))
    preflight = asyncio.run(
        completion_preflight(
            ledger=ledger,
            runs=_runs(_run()),
            goal_revision=1,
            workspace_fingerprint="ws-1",
            platform="linux",
            workspace_root=".",
            service=_service(_ScriptedEvaluator(_supporting_answers())),
            context=EvaluationContext(run_id="r", goal_id="g"),
        )
    )
    assert isinstance(preflight, CompletionPreflight)
    assert preflight.certifies is False
    assert preflight.certifies_completion() is False
    assert preflight.as_dict()["certifies_completion"] is False


def test_a_preflight_only_source_cannot_complete(tmp_path):
    """§9.8/JEV-15: only runner-executed evidence, and only the gate, certify."""
    runner = GateRunner(
        repo_root=str(tmp_path), dod=[], task_base="", revision=1, workspace="ws-1", session="s1"
    )
    request = CompletionRequest(user_goal="g", claimed_completed=["the feature works"])
    runner.gate_precheck(request, [], _FakePrecheck())
    findings = runner.gate_precheck(request, [], _FakePrecheck())
    codes = {finding.code for finding in findings}
    assert PRECHECK_ONLY_SOURCE in codes
    assert any(finding.severity == BLOCKER for finding in findings)


class _FakePrecheck:
    """A preflight that tries to be a certificate — must be refused."""

    signals = ("completion_evidence_fresh",)
    reasons = ()
    certifies = False
    provider = "replay_fixture"
    live_jev = False

    def certifies_completion(self) -> bool:
        return False


def test_a_precheck_claiming_to_certify_is_refused(tmp_path):
    class _Certifying(_FakePrecheck):
        certifies = True
        certifies_completion = lambda self: True  # noqa: E731

    runner = GateRunner(repo_root=str(tmp_path), dod=[], revision=1)
    request = CompletionRequest(user_goal="g", claimed_completed=["x"])
    findings = runner.gate_precheck(request, [], _Certifying())
    codes = {finding.code for finding in findings if finding.code}
    assert PRECHECK_CERTIFIES in codes
    assert any(
        finding.severity == BLOCKER and finding.code == PRECHECK_CERTIFIES
        for finding in findings
    )


def test_hard_check_reasons_are_machine_readable_codes():
    for reason in HARD_REASONS.values():
        assert reason == reason.lower()
        assert " " not in reason


def test_the_preflight_carries_its_evidence_binding():
    ledger = CompletionLedger(criteria=(_criterion(),))
    preflight = asyncio.run(
        completion_preflight(
            ledger=ledger,
            runs=_runs(_run()),
            goal_revision=1,
            workspace_fingerprint="ws-1",
            platform="linux",
            workspace_root=".",
            service=_service(_ScriptedEvaluator(_supporting_answers())),
            context=EvaluationContext(run_id="r", goal_id="g"),
        )
    )
    assert preflight.revision == 1
    assert preflight.fingerprint
    assert preflight.provider_mode == PROVIDER_MODE_REPLAY_FIXTURE
    assert preflight.live_jev is False  # a fixture is not a live Jev proof
