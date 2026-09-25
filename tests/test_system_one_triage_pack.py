"""JEV-12 — Voice-Triage-Pack against productive counter-examples (§47/§90).

ROT-FIRST evidence, captured before the implementation existed. First the route
vocabulary and the pack module were missing (the import order decides which one
fails first):

    $ .venv/bin/python -m pytest tests/test_system_one_triage_pack.py -p no:randomly
    tests/test_system_one_triage_pack.py:35: in <module>
        from skaldir.system_one_policy import (
    E   ImportError: cannot import name 'ROUTE_ESCALATE_SYSTEM_TWO' from
        'godsv.system_one_policy'                                 (1 error, 0.10s)

and, with that extension in place, the pack module itself:

    $ mv src/godsv/system_one_shadow.py src/godsv/_shadow_red_state.py.bak && \
      .venv/bin/python -m pytest tests/test_system_one_triage_pack.py -p no:randomly
    tests/test_system_one_triage_pack.py:41: in <module>
        from skaldir.system_one_shadow import (
    E   ModuleNotFoundError: No module named 'godsv.system_one_shadow'  (1 error, 0.07s)
    $ mv src/godsv/_shadow_red_state.py.bak src/godsv/system_one_shadow.py

Both failures were observed as written here; the module was restored immediately
afterwards (the red state was reproduced by moving the file aside, not by
editing the test).

What this file pins:

* §47/§90/JEV-12: the voice triage pack is evaluated across the six required
  dimensions — Loop, **Fortschritt** (progress), Drift, Completion, Research,
  Recovery — and every dimension is exercised by at least one *productive
  counter-example*: a real production trigger shape in which the correct answer
  is "keep working" (productive debugging, necessary supporting work, current
  completion evidence, sufficient local evidence, a safe replay, real progress);
* the trigger shapes are the ones the runtime really emits
  (`pause.TRIGGER_RULES` + the monitor's signal kinds), not only the happy path
  of the eight mapped triggers;
* each evaluation runs the *real* composition (`compose_voice_triage`), the
  *real* answer contract (`parse_answer`), the *real* deterministic Voice and
  the *real* shadow harness — only the provider is a fixture (evidence: mock);
* the pack's readings are checked against the productive outcome, disagreements
  with today's deterministic path are measured and kept, and the dimension
  coverage gaps of real triggers are reported instead of hushed up;
* §98/JEV-20: the whole evaluation sits behind the shadow flag — with the flags
  off nothing runs and the existing path stays byte-identical.
"""

from __future__ import annotations

import pytest

from skaldir.system_one import SystemOneContractError
from skaldir.system_one_flags import SystemOneFlags
from skaldir.system_one_policy import (
    ROUTE_ESCALATE_SYSTEM_TWO,
    ROUTE_LADDER,
    ROUTE_OBSERVE,
    propose_route,
)
from skaldir.system_one_shadow import (
    HARD_INTERVENTION_ROUTES,
    INTERVENING_ROUTES,
    NON_INTERVENING_ROUTES,
    OUTCOME_INTERVENTION,
    OUTCOME_NO_INTERVENTION,
    PRODUCTION_CASES,
    PRODUCTION_TRIGGER_KINDS,
    REQUIRED_GROUPS_BY_TRIGGER,
    TriageCase,
    evaluate_case,
    evaluate_pack,
    evaluate_pack_if_enabled,
    outcome_aligned,
    pack_evaluation_enabled,
    render_pack_report,
    trigger_coverage,
)

#: The six dimensions the contract text of JEV-12 names, in contract order.
REQUIRED_DIMENSIONS = ("loop", "progress", "drift", "completion", "research", "recovery")

#: Real trigger kinds whose production shape needs a §47 dimension that the
#: existing ``TRIGGER_GROUPS`` does not compose (reported, not silently fixed).
EXPECTED_COVERAGE_GAPS = {
    "hallucinated_api": ("research",),
    "outage_misdiagnosis": ("research",),
    "dependency_change": ("research",),
    "pattern_dependency_churn": ("research",),
}


# --------------------------------------------------------------------------- #
# JEV-12 §90 — the pack is evaluated over all six dimensions                    #
# --------------------------------------------------------------------------- #


def test_corpus_covers_the_six_dimensions_of_the_contract():
    dimensions = {case.dimension for case in PRODUCTION_CASES}
    assert set(REQUIRED_DIMENSIONS) <= dimensions


def test_every_dimension_has_a_productive_counter_example():
    """JEV-12: evaluated *with* productive counter-examples, per dimension."""
    for dimension in REQUIRED_DIMENSIONS:
        counter_examples = [
            case
            for case in PRODUCTION_CASES
            if case.dimension == dimension and case.counter_example
        ]
        assert counter_examples, f"{dimension} has no productive counter-example"


def test_corpus_uses_real_production_trigger_shapes():
    for case in PRODUCTION_CASES:
        assert case.trigger_kind in PRODUCTION_TRIGGER_KINDS
        assert case.shape
        assert case.answers, f"{case.case_id} carries no reading"
        assert case.expected_outcome in (OUTCOME_NO_INTERVENTION, OUTCOME_INTERVENTION)


def test_pack_readings_match_the_pinned_route_and_rule():
    report = evaluate_pack()
    assert report.mismatches == ()
    assert report.ok is True


def test_pack_readings_align_with_the_productive_outcome():
    report = evaluate_pack()
    assert report.outcome_mismatches == ()
    assert report.counter_examples >= len(REQUIRED_DIMENSIONS)


def test_counter_examples_never_pause_or_escalate():
    """A productive counter-example may ask for a clarification — never a pause
    or an escalation (that is the over-reaction the counter-examples exist for)."""
    checked = 0
    for result in evaluate_pack().results:
        if not result.counter_example:
            continue
        checked += 1
        assert result.proposed_route not in HARD_INTERVENTION_ROUTES, result.case_id
        if result.expected_outcome == OUTCOME_NO_INTERVENTION:
            assert result.proposed_route in NON_INTERVENING_ROUTES, result.case_id
    assert checked >= len(REQUIRED_DIMENSIONS)


def test_productive_debugging_is_left_alone():
    """§33/S05: a healthy debugging trajectory must not be read as a loop."""
    result = _result("loop_productive_debugging")
    assert result.proposed_route == ROUTE_OBSERVE
    assert result.expected_outcome == OUTCOME_NO_INTERVENTION
    assert result.outcome_aligned is True
    assert result.counter_example is True


def test_real_loop_is_paused_and_investigated():
    result = _result("loop_same_strategy_no_new_information")
    assert result.proposed_route == "pause_and_investigate"
    assert result.proposed_rule == "loop_no_progress_without_new_information"
    assert result.expected_outcome == OUTCOME_INTERVENTION


def test_progress_alone_prevents_the_pause():
    """Fortschritt: real progress without strategy change is not a loop."""
    result = _result("progress_real_but_slow")
    assert result.proposed_route == ROUTE_OBSERVE
    assert result.counter_example is True


def test_necessary_supporting_work_is_not_drift():
    result = _result("drift_necessary_supporting_work")
    assert result.proposed_route == ROUTE_OBSERVE
    assert result.counter_example is True


def test_scope_expansion_contradicting_a_non_goal_goes_to_the_user():
    result = _result("drift_non_goal_conflict")
    assert result.proposed_route == "request_user_input"
    assert result.proposed_rule == "drift_declared_non_goal_conflict"


def test_completion_claim_without_current_evidence_asks_for_verification():
    result = _result("completion_stub_fake_done")
    assert result.proposed_route == "request_verification"
    assert result.proposed_rule == "completion_claim_without_current_evidence"


def test_current_completion_evidence_is_not_nagged():
    result = _result("completion_claim_with_current_evidence")
    assert result.proposed_route == ROUTE_OBSERVE
    assert result.counter_example is True


def test_version_sensitive_missing_fact_is_investigated():
    result = _result("research_version_sensitive_fact_missing")
    assert result.proposed_route == "investigate"
    assert result.proposed_rule == "research_missing_version_sensitive_fact"


def test_sufficient_local_evidence_stops_the_research():
    result = _result("research_sufficient_local_evidence")
    assert result.proposed_route == ROUTE_OBSERVE
    assert result.counter_example is True


def test_unsafe_replay_escalates_even_with_a_low_system_two_reading():
    """§48/§33: an unsafe replay is a hard stop, not a confidence question."""
    result = _result("recovery_side_effect_unsafe_replay")
    assert result.proposed_route == ROUTE_ESCALATE_SYSTEM_TWO
    assert result.proposed_rule == "recovery_possible_side_effect_unsafe_replay"
    assert result.expected_outcome == OUTCOME_INTERVENTION


def test_unclear_state_with_safe_replay_is_investigated_not_escalated():
    result = _result("recovery_state_unclear_replay_safe")
    assert result.proposed_route == "investigate"
    assert result.proposed_rule == "recovery_state_unclear_replay_safe"
    assert result.counter_example is True


def test_system_two_reading_escalates_and_keeps_the_existing_rule_name():
    """§48: the pre-existing escalation reading keeps its behaviour."""
    result = _result("recovery_system_two_needed")
    assert result.proposed_route == ROUTE_ESCALATE_SYSTEM_TWO
    assert result.proposed_rule == "recovery_system_two_needed"


# --------------------------------------------------------------------------- #
# JEV-12 — the measurement itself: disagreement, outcome, shadow invariants     #
# --------------------------------------------------------------------------- #


def test_disagreement_with_the_deterministic_path_is_measured_and_kept():
    report = evaluate_pack()
    assert report.disagreements > 0
    for result in report.results:
        assert result.baseline_route  # the deterministic reading is measured, not assumed
        assert result.record["baseline_route"] == result.baseline_route
        assert result.record["disagreement"] == result.disagreement
        assert result.disagreement == (result.baseline_route != result.proposed_route)


def test_pack_evaluation_records_the_productive_outcome_on_the_shadow_record():
    for result in evaluate_pack().results:
        assert result.record["outcome"] == result.expected_outcome
        assert result.record["outcome_at"] is not None
        assert result.record["mutations"] == 0
        assert result.record["route"] is None
        assert result.record["context_shadow"] is True  # the evaluation is labelled shadow (§49)
        assert result.record["evaluation"]["mode"] == "shadow"


def test_pack_evaluation_never_executes_a_route():
    report = evaluate_pack()
    assert report.records == len(PRODUCTION_CASES)
    assert all(result.record["route"] is None for result in report.results)
    assert report.executed_routes == 0


def test_pack_evaluation_reports_latency_and_usage_per_case():
    result = _result("loop_same_strategy_no_new_information")
    assert result.record["latency_ms"] >= 0
    assert result.record["usage"]  # the fixture reports what a provider would report
    assert result.record["evaluation"]["provider_access_mode"] == "fixture"
    assert result.record["evaluation"]["live_jev"] is False


def test_a_case_cannot_answer_a_question_the_pack_does_not_ask():
    """The fixture goes through the real contract — no smuggled answers."""
    case = TriageCase(
        case_id="smuggled",
        dimension="loop",
        trigger_kind="loop",
        shape="an answer to a drift question on a loop trigger",
        answers={"voice.drift.supports_goal": {"value": 0.9}},
        expected_route=ROUTE_OBSERVE,
        expected_rule="no_rule_matched",
        expected_outcome=OUTCOME_NO_INTERVENTION,
        counter_example=False,
        required_groups=("loop",),
    )
    with pytest.raises((SystemOneContractError, KeyError)):
        evaluate_case(case)


# --------------------------------------------------------------------------- #
# JEV-12 — coverage of the real trigger kinds, reported honestly                #
# --------------------------------------------------------------------------- #


def test_coverage_probe_reports_exactly_the_documented_gaps():
    gaps = {gap.trigger_kind: gap.missing for gap in trigger_coverage()}
    assert gaps == EXPECTED_COVERAGE_GAPS


def test_the_gap_is_a_missing_question_dimension_not_a_silent_pass():
    report = evaluate_pack()
    assert report.coverage_gaps
    assert report.fully_covered is False
    assert report.ok is True  # every *asked* case still matched its reading
    for result in report.results:
        assert result.missing_groups == () or result.status == "coverage_gap"


def test_unknown_coverage_gap_of_a_needed_dimension_is_visible_per_case():
    result = _result("hallucinated_api_needs_research")
    assert result.missing_groups == ("research",)
    assert result.status == "coverage_gap"
    assert "research" not in result.composed_groups


def test_required_groups_table_only_names_real_trigger_kinds():
    assert set(REQUIRED_GROUPS_BY_TRIGGER) <= set(PRODUCTION_TRIGGER_KINDS)
    assert all(groups for groups in REQUIRED_GROUPS_BY_TRIGGER.values())


# --------------------------------------------------------------------------- #
# JEV-12 — the §32 ladder is the only vocabulary the pack may propose           #
# --------------------------------------------------------------------------- #


def test_every_proposed_route_is_on_the_section_32_ladder():
    for case in PRODUCTION_CASES:
        result = evaluate_case(case)
        assert result.proposed_route in ROUTE_LADDER or (
            result.proposed_route == ROUTE_ESCALATE_SYSTEM_TWO
        )
        assert propose_route(result.evaluation).route == result.proposed_route


def test_outcome_alignment_helper_is_not_a_tautology():
    assert outcome_aligned(route=ROUTE_OBSERVE, outcome=OUTCOME_NO_INTERVENTION) is True
    assert outcome_aligned(route=ROUTE_ESCALATE_SYSTEM_TWO, outcome=OUTCOME_INTERVENTION) is True
    assert (
        outcome_aligned(route=ROUTE_ESCALATE_SYSTEM_TWO, outcome=OUTCOME_NO_INTERVENTION) is False
    )
    assert outcome_aligned(route=ROUTE_OBSERVE, outcome=OUTCOME_INTERVENTION) is False
    assert INTERVENING_ROUTES & NON_INTERVENING_ROUTES == frozenset()


# --------------------------------------------------------------------------- #
# JEV-20 §98 — behind the flag; off is the existing behaviour                   #
# --------------------------------------------------------------------------- #


def test_pack_evaluation_is_bound_to_the_shadow_flag():
    assert pack_evaluation_enabled(None) is False
    assert pack_evaluation_enabled(SystemOneFlags.disabled()) is False
    assert (
        pack_evaluation_enabled(
            SystemOneFlags.disabled().with_flag("enabled", True).with_flag("voice_triage", True)
        )
        is False
    )
    assert pack_evaluation_enabled(
        SystemOneFlags.disabled()
        .with_flag("enabled", True)
        .with_flag("provider", "llm_system_one_adapter")
        .with_flag("shadow_mode", True)
    )
    assert (
        pack_evaluation_enabled(
            SystemOneFlags.disabled().with_flag("enabled", True).with_flag("shadow_mode", True)
        )
        is False
    )  # provider disabled → the safe fallback still wins


def test_flags_off_runs_nothing_and_returns_none():
    assert evaluate_pack_if_enabled(flags=None) is None
    assert evaluate_pack_if_enabled(flags=SystemOneFlags.disabled()) is None

    enabled = (
        SystemOneFlags.disabled()
        .with_flag("enabled", True)
        .with_flag("provider", "llm_system_one_adapter")
        .with_flag("shadow_mode", True)
    )
    report = evaluate_pack_if_enabled(flags=enabled)
    assert report is not None
    assert report.records == len(PRODUCTION_CASES)


def test_rendered_report_states_the_cases_the_gaps_and_the_zero_mutations():
    report = evaluate_pack()
    text = render_pack_report(report)
    for case in PRODUCTION_CASES:
        assert case.case_id in text
    assert "shadow" in text
    assert "mutations=0" in text
    assert "hallucinated_api" in text
    assert "research" in text


def _result(case_id: str):
    for case in PRODUCTION_CASES:
        if case.case_id == case_id:
            return evaluate_case(case)
    raise AssertionError(f"no such case: {case_id}")


