"""JEV §13 (threshold profiles) + §18 (shadow record completeness).

ROT-FIRST evidence, captured before this delta existed (the pristine
pre-delta modules from `git show HEAD:src/godsv/...` on the path, so the
failures below are the real ones, not a reconstruction):

    $ .venv/bin/python -m pytest -o pythonpath=$HOME/jev_red/src \
        tests/test_jev_policy_delta.py -p no:randomly
    E   ImportError: cannot import name 'REASON_UNCERTAIN_BAND' from
        'godsv.system_one_policy' (/home/willy/jev_red/src/godsv/system_one_policy.py)
    !!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
    1 error in 0.08s

What this file pins:

* §13 — a profile carries ``threshold_profile_id``, ``question_pack_version``,
  ``dataset_version`` and the four *measured* rates. The rates are ``None``
  until somebody measured them: no invented calibration number ever appears.
  Thresholds stay per action class (R0..R4), never global; an uncertainty band
  escalates instead of guessing; a frozen profile refuses edits, so
  freeze-before-holdout stays enforced.
* §18 — a shadow record carries ``trace_id``, ``current_system_result``,
  ``later_ground_truth`` and ``cost_if_known``, and keeps the §45 guard
  semantics (``mutations == 0``, ``route is None``).
"""

from __future__ import annotations

import asyncio

import pytest

from skaldir.system_one import (
    DisabledSystemOneEvaluator,
    EvaluationContext,
    NoulAnswer,
    SystemOneContractError,
    SystemOneState,
)
from skaldir.system_one_policy import (
    R0,
    R1,
    R3,
    R4,
    REASON_UNCERTAIN_BAND,
    RISK_POLICY,
    ShadowHarness,
    ThresholdProfile,
    authorize,
    calibrate_thresholds,
)
from skaldir.system_one_questions import compose_voice_triage


def _profile(**overrides) -> ThresholdProfile:
    base = {
        "profile_id": "dev-1",
        "frozen": False,
        "per_risk": {R0: 0.4, R1: 0.5, "R2": 0.7, R3: 0.85, R4: 0.0},
        "noul_threshold": 0.5,
        "choice_threshold": 0.6,
        "score_threshold": 0.6,
    }
    base.update(overrides)
    return ThresholdProfile(**base)


# --------------------------------------------------------------------------- #
# §13 — the profile carries its calibration identity                           #
# --------------------------------------------------------------------------- #


def test_profile_carries_the_documented_identity_and_measurement_fields():
    profile = _profile(
        question_pack_version="v1", dataset_version="dev-2026-09-16"
    )
    assert profile.threshold_profile_id == "dev-1"
    assert profile.question_pack_version == "v1"
    assert profile.dataset_version == "dev-2026-09-16"
    assert profile.measured_precision is None
    assert profile.measured_recall is None
    assert profile.false_positive_rate is None
    assert profile.false_negative_rate is None
    assert profile.measured is False


def test_measurements_are_only_what_was_measured():
    profile = _profile(
        measured_precision=0.91,
        measured_recall=0.83,
        false_positive_rate=0.09,
        false_negative_rate=0.17,
        dataset_version="dev-2026-09-16",
    )
    assert profile.measured_precision == pytest.approx(0.91)
    assert profile.measured_recall == pytest.approx(0.83)
    assert profile.false_positive_rate == pytest.approx(0.09)
    assert profile.false_negative_rate == pytest.approx(0.17)
    assert profile.measured is True
    # a half-measured profile is honestly incomplete, not silently complete
    assert _profile(measured_precision=0.9).measured is False


def test_calibrate_thresholds_reports_no_invented_measurements():
    profile = calibrate_thresholds(
        [{"risk_class": R1, "value": 0.7, "correct": True}],
        profile_id="dev-2",
        frozen=False,
    )
    assert profile.threshold_profile_id == "dev-2"
    assert profile.measured_precision is None
    assert profile.measured is False
    assert profile.dataset_version == ""


def test_thresholds_are_per_action_class_and_never_global():
    profile = _profile()
    assert set(profile.per_risk) == set(RISK_POLICY)
    assert profile.threshold_for(R0) != profile.threshold_for(R3)
    assert profile.threshold_for_action_class(R3) == profile.threshold_for(R3)


def test_a_profile_missing_an_action_class_is_refused():
    with pytest.raises(SystemOneContractError):
        _profile(per_risk={R0: 0.4, R1: 0.5})


# --------------------------------------------------------------------------- #
# §13 — uncertainty band escalates instead of guessing                         #
# --------------------------------------------------------------------------- #


def test_a_reading_inside_the_uncertainty_band_escalates_instead_of_guessing():
    profile = _profile(uncertainty_band={R1: (0.40, 0.60)})
    answer = NoulAnswer(question_id="q", value=0.50)
    decision = authorize(R1, answer, profile=profile)
    assert decision.escalate is True
    assert decision.auto is False
    assert decision.require_system_two is True
    assert decision.reason == REASON_UNCERTAIN_BAND


def test_a_reading_outside_the_band_is_authorized_as_before():
    profile = _profile(uncertainty_band={R1: (0.45, 0.55)})
    confident = NoulAnswer(question_id="q", value=0.99)
    decision = authorize(R1, confident, profile=profile)
    assert decision.escalate is False
    assert decision.auto is True


def test_without_a_calibrated_band_no_number_is_invented():
    """No band measured → no escalation band, and no silent default band."""
    profile = _profile()
    assert profile.uncertainty_band == {}
    decision = authorize(R1, NoulAnswer(question_id="q", value=0.5), profile=profile)
    assert decision.escalate is False
    assert profile.band_for(R1) is None


def test_band_bounds_are_validated():
    with pytest.raises(SystemOneContractError):
        _profile(uncertainty_band={R1: (0.7, 0.3)})


# --------------------------------------------------------------------------- #
# §13 — freeze before holdout stays enforced                                   #
# --------------------------------------------------------------------------- #


def test_a_frozen_profile_refuses_threshold_band_and_measurement_edits():
    frozen = _profile(frozen=True)
    with pytest.raises(SystemOneContractError):
        frozen.with_threshold(R0, 0.1)
    with pytest.raises(SystemOneContractError):
        frozen.with_uncertainty_band(R1, (0.4, 0.6))
    with pytest.raises(SystemOneContractError):
        frozen.with_measurements(
            measured_precision=0.9,
            measured_recall=0.9,
            false_positive_rate=0.1,
            false_negative_rate=0.1,
        )


def test_an_unfrozen_profile_can_be_calibrated_and_then_frozen():
    profile = _profile().with_uncertainty_band(R1, (0.4, 0.6))
    assert profile.band_for(R1) == (0.4, 0.6)
    measured = profile.with_measurements(
        measured_precision=0.9,
        measured_recall=0.8,
        false_positive_rate=0.1,
        false_negative_rate=0.2,
        dataset_version="holdout-1",
    )
    assert measured.measured is True
    frozen = measured.frozen_profile()
    assert frozen.frozen is True
    with pytest.raises(SystemOneContractError):
        frozen.with_threshold(R1, 0.9)


# --------------------------------------------------------------------------- #
# §18 — shadow records                                                         #
# --------------------------------------------------------------------------- #


def _state() -> SystemOneState:
    return SystemOneState(payload={"goal": "g"})


def _run(coro):
    return asyncio.run(coro)


def test_a_shadow_record_carries_the_four_new_documented_fields():
    harness = ShadowHarness(DisabledSystemOneEvaluator())
    record = _run(
        harness.run(
            state=_state(),
            questions=compose_voice_triage("loop"),
            context=EvaluationContext(run_id="r", goal_id="g"),
            baseline_route="hint",
        )
    )
    assert record["trace_id"]
    assert isinstance(record["trace_id"], str)
    assert record["current_system_result"]["route"] == "hint"
    assert record["later_ground_truth"] == ""
    assert record["cost_if_known"] is None  # nothing was reported → nothing invented
    # §45 guard semantics survive the extension
    assert record["mutations"] == 0
    assert record["route"] is None
    assert record["selected_route"] is None


def test_trace_ids_are_unique_per_record():
    harness = ShadowHarness(DisabledSystemOneEvaluator())
    questions = compose_voice_triage("loop")
    first = _run(
        harness.run(
            state=_state(),
            questions=questions,
            context=EvaluationContext(run_id="r", goal_id="g"),
        )
    )
    second = _run(
        harness.run(
            state=_state(),
            questions=questions,
            context=EvaluationContext(run_id="r", goal_id="g"),
        )
    )
    assert first["trace_id"] != second["trace_id"]


def test_an_observed_outcome_becomes_the_later_ground_truth():
    harness = ShadowHarness(DisabledSystemOneEvaluator())
    record = _run(
        harness.run(
            state=_state(),
            questions=compose_voice_triage("loop"),
            context=EvaluationContext(run_id="r", goal_id="g"),
            baseline_route="hint",
        )
    )
    updated = harness.observe_outcome(
        evaluation_id=record["evaluation"]["evaluation_id"],
        outcome="escalated",
        details={"reason": "real loop"},
    )
    assert updated["later_ground_truth"] == "escalated"
    assert updated["outcome_details"]["reason"] == "real loop"


def test_a_reported_cost_is_carried_through_unchanged():
    class _Costed:
        provider = "llm_system_one_adapter"
        provider_model = "m"
        provider_access_mode = "llm_adapter"

        async def evaluate(self, *, state, questions, context):
            from skaldir.system_one import build_evaluation

            return build_evaluation(
                context=context,
                questions=questions,
                state=state,
                provider=self.provider,
                provider_model=self.provider_model,
                access_mode=self.provider_access_mode,
                usage={"cost_usd": 0.0042, "prompt_chars": 120},
            )

    harness = ShadowHarness(_Costed())
    record = _run(
        harness.run(
            state=_state(),
            questions=compose_voice_triage("loop"),
            context=EvaluationContext(run_id="r", goal_id="g"),
        )
    )
    assert record["cost_if_known"] == pytest.approx(0.0042)


def test_shadow_summary_reports_trace_count_not_an_estimate():
    harness = ShadowHarness(DisabledSystemOneEvaluator())
    _run(
        harness.run(
            state=_state(),
            questions=compose_voice_triage("loop"),
            context=EvaluationContext(run_id="r", goal_id="g"),
        )
    )
    summary = harness.summary()
    assert summary["records"] == 1
    assert summary["mutations"] == 0
