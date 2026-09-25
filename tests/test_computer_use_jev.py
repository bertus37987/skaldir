"""JEV-18/§12 — the 13 System-2 escalation triggers as a checkable list plus one
decision function (``godsv.computer_use``).

ROT-FIRST evidence, captured against the committed tree (`HEAD`), because the
implementation was appended to the existing module: a detached worktree of
`HEAD` with only this test file copied in.

    $ git worktree add --detach $HOME/jev-red-head HEAD
    $ cp tests/test_computer_use_jev.py $HOME/jev-red-head/tests/
    $ cd $HOME/jev-red-head && PYTHONPATH=src \
        /home/willy/projects/gods-voice/.venv/bin/python -m pytest \
        tests/test_computer_use_jev.py -p no:randomly
    E   ImportError: cannot import name 'CONTRADICTION_READING' from 'godsv.computer_use'
        (/home/willy/jev-red-head/src/godsv/computer_use.py)
    !!! Interrupted: 1 error during collection !!!
    1 error in 0.25s

What this file pins (behaviour, never source text):

* the goal's §12 list is **data**: exactly 13 triggers, each with the goal's
  wording, each raisable by a concrete signal;
* an unknown signal is not a pass and a missing calibration is not a default:
  a check that cannot be measured escalates and is listed in ``unmeasured`` —
  there is no global threshold and no invented confidence (§13);
* the decision is advisory: it never authorizes an action, never carries a
  coordinate or a route, and never proposes a second policy ladder (§50/§34).

Evidence class: contract + mock (a real internal evaluation built from the
Computer-Use pack with test-double answers). No provider call happened, so
``live_jev`` is irrelevant here and nothing is a live reading.
"""

from __future__ import annotations

import json

from skaldir.computer_use import (
    CONTRADICTION_READING,
    ESCALATION_TRIGGER_REASONS,
    ESCALATION_TRIGGERS,
    EscalationSignals,
    contradicting_answers,
    system_two_escalation_decision,
)
from skaldir.system_one import (
    ACCESS_LLM_ADAPTER,
    PROVIDER_LLM_ADAPTER,
    EvaluationContext,
    SystemOneState,
    build_evaluation,
    parse_answer,
)
from skaldir.system_one_subtools import computer_use_pack


class FrozenProfile:
    """A *test* threshold profile: two frozen calibrations, invented here."""

    def __init__(self, *, profile_id="test-profile", floor=0.7, band=(0.35, 0.65)):
        self.profile_id = profile_id
        self.frozen = True
        self.choice_threshold = floor
        self.score_threshold = floor
        self.noul_band = band


def _evaluation(values: dict[str, float] | None = None, *, confidence: float = 0.95):
    """A real internal evaluation over the full Computer-Use pack (doubles)."""
    values = values or {}
    pack = computer_use_pack()
    answers = {}
    for question in pack.questions:
        short = question.id.split(".")[-1]
        value = float(values.get(short, 0.1))
        if question.kind == "noul":
            answers[question.id] = parse_answer(
                question_id=question.id, kind="noul", payload={"value": value}
            )
            continue
        options = tuple(question.options)
        share = round(1.0 / len(options), 6)
        probabilities = {option: share for option in options}
        probabilities[options[-1]] = round(1.0 - share * (len(options) - 1), 6)
        answers[question.id] = parse_answer(
            question_id=question.id,
            kind="choice",
            payload={
                "choice": options[0],
                "probabilities": probabilities,
                "confidence": confidence,
            },
        )
    return build_evaluation(
        context=EvaluationContext(run_id="run-1", goal_id="goal-1", goal_revision=1),
        questions=pack,
        state=SystemOneState(payload={"task": "render_scene"}),
        provider=PROVIDER_LLM_ADAPTER,
        provider_model="system-one-test-double",
        access_mode=ACCESS_LLM_ADAPTER,
        answers=answers,
    )


def _healthy_signals(**overrides) -> EscalationSignals:
    base = {
        "state_sufficient": True,
        "candidate_action_available": True,
        "app_version_applicable": True,
        "noul_value": 0.95,
    }
    base.update(overrides)
    return EscalationSignals(**base)


# --------------------------------------------------------------------------- #
# the list itself                                                              #
# --------------------------------------------------------------------------- #


def test_the_goal_lists_exactly_thirteen_triggers():
    assert len(ESCALATION_TRIGGERS) == 13
    assert len(set(ESCALATION_TRIGGERS)) == 13
    assert set(ESCALATION_TRIGGER_REASONS) == set(ESCALATION_TRIGGERS)
    assert all(ESCALATION_TRIGGER_REASONS[trigger] for trigger in ESCALATION_TRIGGERS)


def test_every_trigger_is_raisable_by_a_concrete_signal():
    """One signal set per trigger — the list is checkable, not prose."""
    cases: dict[str, tuple[dict, EscalationSignals]] = {
        "insufficient_state": ({}, EscalationSignals(state_sufficient=False)),
        "low_choice_or_score_confidence": (
            {},
            _healthy_signals(candidate_action_available=True),
        ),
        "noul_inside_calibrated_uncertainty_band": ({}, _healthy_signals(noul_value=0.5)),
        "contradicting_answers": (
            {"expected_transition_occurred": 0.9, "unexpected_transition_occurred": 0.9},
            _healthy_signals(),
        ),
        "new_unknown_ui": ({}, _healthy_signals(unknown_ui=True)),
        "missing_candidate_action": (
            {},
            _healthy_signals(candidate_action_available=False),
        ),
        "complex_cause": ({}, _healthy_signals(complex_cause=True)),
        "research_needed": ({}, _healthy_signals(research_needed=True)),
        "destructive_high_risk_action": (
            {},
            _healthy_signals(destructive_or_high_risk=True),
        ),
        "goal_interpretation": ({}, _healthy_signals(goal_interpretation_needed=True)),
        "policy_ambiguity": ({}, _healthy_signals(policy_ambiguous=True)),
        "user_preference_needed": ({}, _healthy_signals(user_preference_needed=True)),
        "app_version_outside_subtool_applicability": (
            {},
            _healthy_signals(app_version_applicable=False),
        ),
    }
    assert set(cases) == set(ESCALATION_TRIGGERS), "every trigger needs a case"
    for trigger, (values, signals) in cases.items():
        confidence = 0.2 if trigger == "low_choice_or_score_confidence" else 0.95
        decision = system_two_escalation_decision(
            profile=FrozenProfile(),
            evaluation=_evaluation(values, confidence=confidence),
            signals=signals,
            allowed_candidates=("candidate_a",),
        )
        assert decision.escalate is True, trigger
        assert trigger in decision.triggers, trigger


def test_a_fully_measured_healthy_step_does_not_escalate():
    decision = system_two_escalation_decision(
        profile=FrozenProfile(),
        evaluation=_evaluation({"reobserve_required": 0.05, "system2_needed": 0.02}),
        signals=_healthy_signals(),
        allowed_candidates=("candidate_a", "reobserve"),
    )
    assert decision.escalate is False
    assert decision.triggers == ()
    assert decision.unmeasured == ()
    assert decision.authorizes_action is False
    assert decision.advisory_only is True


# --------------------------------------------------------------------------- #
# an unknown signal is not a pass, a missing calibration is not a default       #
# --------------------------------------------------------------------------- #


def test_an_unmeasured_signal_escalates_and_is_named():
    decision = system_two_escalation_decision(
        profile=None, evaluation=None, signals=EscalationSignals(), allowed_candidates=()
    )
    assert decision.escalate is True
    for trigger in (
        "insufficient_state",
        "low_choice_or_score_confidence",
        "noul_inside_calibrated_uncertainty_band",
        "contradicting_answers",
        "missing_candidate_action",
        "app_version_outside_subtool_applicability",
    ):
        assert trigger in decision.triggers
        assert trigger in decision.unmeasured, "unmeasured checks are named, not guessed"
    joined = " ".join(decision.reasons)
    assert "no frozen threshold profile" in joined
    assert "no System-One evaluation is available" in joined


def test_a_profile_without_calibration_leaves_the_checks_unmeasured():
    """No global threshold: an uncalibrated profile escalates instead of guessing."""
    uncalibrated = FrozenProfile(floor=0.0, band=None)
    uncalibrated.noul_band = None
    decision = system_two_escalation_decision(
        profile=uncalibrated,
        evaluation=_evaluation(),
        signals=_healthy_signals(noul_value=0.5),
        allowed_candidates=("candidate_a",),
    )
    assert decision.escalate is True
    assert set(decision.unmeasured) >= {
        "low_choice_or_score_confidence",
        "noul_inside_calibrated_uncertainty_band",
    }
    assert decision.profile_id == "test-profile"


def test_the_noul_band_is_compared_not_averaged():
    """Inside the band escalates; outside does not — no score is invented."""
    inside = system_two_escalation_decision(
        profile=FrozenProfile(band=(0.4, 0.6)),
        evaluation=_evaluation(),
        signals=_healthy_signals(noul_value=0.5),
        allowed_candidates=("candidate_a",),
    )
    assert "noul_inside_calibrated_uncertainty_band" in inside.triggers
    outside = system_two_escalation_decision(
        profile=FrozenProfile(band=(0.4, 0.6)),
        evaluation=_evaluation(),
        signals=_healthy_signals(noul_value=0.9),
        allowed_candidates=("candidate_a",),
    )
    assert "noul_inside_calibrated_uncertainty_band" not in outside.triggers


def test_contradicting_answers_are_read_from_the_evaluation():
    coherent = _evaluation(
        {"expected_transition_occurred": 0.9, "unexpected_transition_occurred": 0.05}
    )
    assert contradicting_answers(coherent) == ()
    contradictory = _evaluation(
        {"expected_transition_occurred": 0.9, "unexpected_transition_occurred": 0.9}
    )
    pairs = contradicting_answers(contradictory)
    assert pairs, "both sides high is a contradiction, not an average"
    assert all(left in json.dumps(pairs) for left, _right in pairs)
    assert CONTRADICTION_READING >= 0.5


# --------------------------------------------------------------------------- #
# advisory only — never an action, never a route, never a second policy         #
# --------------------------------------------------------------------------- #


def test_the_decision_is_advisory_and_authorizes_nothing():
    decision = system_two_escalation_decision(
        profile=FrozenProfile(),
        evaluation=_evaluation(),
        signals=_healthy_signals(research_needed=True),
        allowed_candidates=("candidate_a",),
    )
    record = decision.as_dict()
    assert record["escalate"] is True
    assert record["authorizes_action"] is False
    assert record["advisory_only"] is True
    # no executable payload and no second routing ladder may appear here
    assert "route" not in record
    assert "action" not in record
    assert "kind" not in record
    assert "click" not in json.dumps(record)
