"""JEV-10 — Shadow Mode: parallel evaluation without mutation (§45/§88/§21).

ROT-FIRST evidence, captured before the extension existed (the first symbol the
file imports is the first one that had to exist):

    $ .venv/bin/python -m pytest tests/test_system_one_shadow.py -p no:randomly
    tests/test_system_one_shadow.py:47: in <module>
        from skaldir.system_one_policy import (
    E   ImportError: cannot import name 'ROUTE_ESCALATE_SYSTEM_TWO' from
        'godsv.system_one_policy'                                 (1 error, 0.10s)

and, one import later, ``ImportError: cannot import name 'triage_with_shadow'
from 'godsv.voice'``.

What this file pins:

* §45/JEV-10: the *existing* ``ShadowHarness`` runs an evaluator beside the
  deterministic path and stores disagreement, latency, usage and the observed
  outcome — while every record states ``mutations=0`` and ``route=None``;
* §45: the harness may not mutate the run. That is asserted twice — against the
  caller's own state probe and against the record itself
  (:func:`assert_shadow_advisory`) — instead of being asserted in a comment;
* §88/§21/E2E-13: the same Voice snapshot runs through both paths. The
  deterministic hint is byte-identical whether the shadow runs or not, a late
  shadow result is recorded as inert, and a provider failure degrades to the
  existing path (§43);
* §98/JEV-20: with the flags off (the default) ``triage_with_shadow`` returns
  exactly what today's ``triage_with_system_one`` returns — same hint, same
  path, same reason, and the same Voice budget side effects.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from skaldir.monitor import Signal
from skaldir.system_one import (
    ChoiceAnswer,
    DisabledSystemOneEvaluator,
    EvaluationContext,
    NoulAnswer,
    SystemOneContractError,
    SystemOneState,
    SystemOneTimeout,
    build_evaluation,
    parse_answer,
    state_fingerprint,
)
from skaldir.system_one_flags import SystemOneFlags
from skaldir.system_one_policy import (
    ROUTE_ESCALATE_SYSTEM_TWO,
    ROUTE_HINT,
    ROUTE_LADDER,
    ROUTE_OBSERVE,
    ShadowHarness,
    VersionStamp,
    assert_shadow_advisory,
    propose_route,
)
from skaldir.system_one_questions import compose_voice_triage
from skaldir.voice import (
    PATH_LEGACY,
    PATH_SHADOW,
    Voice,
    triage_with_shadow,
    triage_with_system_one,
)


def _run(coro):
    return asyncio.run(coro)


def _state(payload: dict | None = None) -> SystemOneState:
    body = {"goal": "fix the flaky test", "signals": ["loop"]}
    body.update(payload or {})
    return SystemOneState(payload=body, input_scope="task", fingerprint=state_fingerprint(body))


def _ctx(**overrides) -> EvaluationContext:
    base = {"run_id": "r-shadow", "goal_id": "g-1", "goal_revision": 4, "event_cursor": 50}
    base.update(overrides)
    return EvaluationContext(**base)


def _shadow_flags() -> SystemOneFlags:
    """The canary-1 configuration of §46 (shadow) — still all-off by default."""
    return (
        SystemOneFlags.disabled()
        .with_flag("enabled", True)
        .with_flag("provider", "llm_system_one_adapter")
        .with_flag("shadow_mode", True)
    )


def _questions():
    """The §47 composition of a strategy_repeat trigger: loop + recovery.

    ``compose_voice_triage`` decides this; the corpus and the shadow tests use
    the real composition, never a hand-made question list.
    """
    return compose_voice_triage("strategy_repeat")


class _ScriptedEvaluator:
    """Provider-shaped stub: real answers through the real answer contract.

    Only the transport is replaced — the payloads are translated by the same
    internal contract the live adapters use, so what the harness records is a
    real :class:`~godsv.system_one.SystemOneEvaluation` (evidence class: mock).
    """

    provider = "scripted_shadow"
    provider_model = "scripted-1"
    provider_access_mode = "fixture"

    def __init__(self, answers=None, *, usage=None, latency_s=0.25, mutate=None) -> None:
        self.answers = dict(answers or {})
        self.usage = dict(usage or {})
        self.latency_s = float(latency_s)
        self.mutate = mutate
        self.calls = 0

    async def evaluate(self, *, state, questions, context):
        self.calls += 1
        if self.mutate is not None:
            self.mutate()
        kinds = {question.id: question.kind for question in questions.questions}
        translated = {}
        for question_id, payload in self.answers.items():
            if question_id not in kinds:
                raise SystemOneContractError(
                    f"the shadow asked no question {question_id!r}"
                )
            translated[question_id] = parse_answer(
                question_id=question_id, kind=kinds[question_id], payload=payload
            )
        # fixed clocks: the recorded latency is a measured delta, not a guess
        started = 1000.0
        return build_evaluation(
            context=context,
            questions=questions,
            state=state,
            provider=self.provider,
            provider_model=self.provider_model,
            access_mode=self.provider_access_mode,
            answers=translated,
            usage=dict(self.usage),
            started_at=started,
            completed_at=started + self.latency_s,
            shadow=bool(context.shadow),
        )


class _RaisingEvaluator:
    provider = "scripted_shadow"
    provider_model = "scripted-1"

    async def evaluate(self, *, state, questions, context):
        raise SystemOneTimeout("shadow deadline exhausted")


def _escalating_answers() -> dict:
    return {
        "voice.recovery.side_effect_maybe_happened": {"value": 0.9},
        "voice.recovery.replay_safe": {"value": 0.1},
        "voice.recovery.system_two_needed": {"value": 0.8},
    }


# --------------------------------------------------------------------------- #
# JEV-10 §88 — the record carries disagreement, latency, usage, outcome         #
# --------------------------------------------------------------------------- #


def test_shadow_record_carries_disagreement_latency_usage_and_outcome():
    harness = ShadowHarness(
        _ScriptedEvaluator(
            _escalating_answers(),
            usage={"prompt_chars": 120, "response_chars": 40, "cost_usd": 0.0012},
        )
    )
    record = _run(
        harness.run(
            state=_state(),
            questions=_questions(),
            context=_ctx(),
            baseline_route=ROUTE_HINT,
            baseline_hint={"kind": "loop", "severity": 2},
        )
    )

    assert record["latency_ms"] == 250
    assert record["usage"]["prompt_chars"] == 120
    assert record["cost"] == 0.0012
    assert record["baseline_route"] == ROUTE_HINT
    assert record["baseline_hint"]["kind"] == "loop"
    assert record["proposed_route"] == ROUTE_ESCALATE_SYSTEM_TWO
    assert record["proposed_rule"] == "recovery_system_two_needed"
    assert record["disagreement"] is True
    # §49: nothing consumed the answer, so the consuming rule stays empty …
    assert record["policy_rule"] is None
    assert record["selected_route"] is None
    # … and the mode is recorded as shadow, never as active.
    assert record["evaluation"]["mode"] == "shadow"

    # the outcome of the real run is stored next to its shadow record
    harness.observe_outcome(
        evaluation_id=record["evaluation"]["evaluation_id"],
        outcome="no_intervention_needed",
        details={"source": "run_end"},
    )
    assert harness.records[-1]["outcome"] == "no_intervention_needed"
    assert harness.records[-1]["outcome_at"] is not None
    assert harness.outcomes[-1]["details"] == {"source": "run_end"}


def test_shadow_record_states_zero_mutations_and_no_route():
    harness = ShadowHarness(DisabledSystemOneEvaluator())
    record = _run(
        harness.run(
            state=_state(),
            questions=_questions(),
            context=_ctx(),
            baseline_route=ROUTE_HINT,
        )
    )

    assert record["mode"] == "shadow"
    assert record["mutations"] == 0
    assert record["route"] is None
    assert record["selected_route"] is None
    assert record["proposed_route"] == ROUTE_OBSERVE
    assert record["proposed_rule"] == "no_rule_matched"
    assert harness.mutations == 0
    assert harness.executed_routes == ()
    # a shadow record never carries an executable action (§45)
    assert "action" not in record
    assert "pause" not in record


def test_disagreement_is_measured_not_resolved():
    """Both readings survive; the record never overwrites the baseline (§21)."""
    harness = ShadowHarness(_ScriptedEvaluator(_escalating_answers()))
    record = _run(
        harness.run(
            state=_state(),
            questions=_questions(),
            context=_ctx(),
            baseline_route=ROUTE_HINT,
        )
    )
    assert record["baseline_route"] == ROUTE_HINT
    assert record["proposed_route"] == ROUTE_ESCALATE_SYSTEM_TWO
    assert record["disagreement"] is True
    assert harness.summary()["disagreements"] == 1
    assert harness.summary()["disagreement_rate"] == 1.0


def test_agreement_is_recorded_as_agreement():
    harness = ShadowHarness(DisabledSystemOneEvaluator())
    record = _run(
        harness.run(
            state=_state(),
            questions=_questions(),
            context=_ctx(),
            baseline_route=ROUTE_OBSERVE,
        )
    )
    assert record["disagreement"] is False
    assert harness.summary()["disagreements"] == 0
    assert harness.summary()["disagreement_rate"] == 0.0


def test_summary_reports_the_measured_numbers_not_estimates():
    harness = ShadowHarness(_ScriptedEvaluator(_escalating_answers(), latency_s=0.1))
    questions = _questions()
    for _ in range(2):
        _run(
            harness.run(
                state=_state(),
                questions=questions,
                context=_ctx(),
                baseline_route=ROUTE_HINT,
            )
        )
    summary = harness.summary()

    assert summary["records"] == 2
    assert summary["disagreements"] == 2
    assert summary["latency_ms_total"] == 200
    assert summary["latency_ms_max"] == 100
    assert summary["mutations"] == 0
    assert summary["executed_routes"] == 0
    assert summary["outcomes_observed"] == 0
    assert summary["violations"] == 0


def test_observe_outcome_refuses_an_unknown_evaluation():
    harness = ShadowHarness(DisabledSystemOneEvaluator())
    with pytest.raises(SystemOneContractError):
        harness.observe_outcome(evaluation_id="soe-nope-000", outcome="resolved")


# --------------------------------------------------------------------------- #
# JEV-10 §45 — no mutating rights, asserted instead of commented               #
# --------------------------------------------------------------------------- #


def test_shadow_never_mutates_the_run_state():
    """A real mutable run state is probed before and after the shadow run."""
    run_state = {"goal_revision": 4, "paused": False, "completion": "open", "tool_calls": []}
    probe = lambda: json.dumps(run_state, sort_keys=True)  # noqa: E731 — a probe, not policy
    harness = ShadowHarness(_ScriptedEvaluator(_escalating_answers()), state_probe=probe)
    before = probe()

    _run(
        harness.run(
            state=_state(),
            questions=_questions(),
            context=_ctx(),
            baseline_route=ROUTE_HINT,
        )
    )

    assert probe() == before
    assert run_state["tool_calls"] == []
    assert run_state["paused"] is False
    assert harness.violations == []


def test_shadow_refuses_a_mutating_evaluator():
    """A shadow that mutates is a contract violation — loud, not swallowed."""
    run_state = {"goal_revision": 4, "tool_calls": []}
    probe = lambda: json.dumps(run_state, sort_keys=True)  # noqa: E731
    harness = ShadowHarness(
        _ScriptedEvaluator(mutate=lambda: run_state["tool_calls"].append("rm -rf /")),
        state_probe=probe,
    )

    with pytest.raises(SystemOneContractError):
        _run(
            harness.run(
                state=_state(),
                questions=_questions(),
                context=_ctx(),
                baseline_route=ROUTE_HINT,
            )
        )

    assert harness.violations[-1]["state"] == "mutation_detected"
    assert harness.records == []  # a violating run produces no usable record


def test_advisory_guard_rejects_an_executable_or_mutating_record():
    with pytest.raises(SystemOneContractError):
        assert_shadow_advisory({"mode": "shadow", "mutations": 0, "route": "pause_and_investigate"})
    with pytest.raises(SystemOneContractError):
        assert_shadow_advisory({"mode": "shadow", "mutations": 1, "route": None})
    with pytest.raises(SystemOneContractError):
        assert_shadow_advisory(
            {"mode": "shadow", "mutations": 0, "route": None, "action": {"kind": "click"}}
        )
    honest = {"mode": "shadow", "mutations": 0, "route": None, "selected_route": None}
    assert assert_shadow_advisory(honest) == honest


# --------------------------------------------------------------------------- #
# JEV-10 — the proposed route (§32 ladder, §48 escalation)                     #
# --------------------------------------------------------------------------- #


def _evaluation(answers: dict, *, goal_revision: int = 4, event_cursor: int = 50):
    questions = _questions()
    kinds = {question.id: question.kind for question in questions.questions}
    translated = {
        question_id: parse_answer(
            question_id=question_id, kind=kinds[question_id], payload=payload
        )
        for question_id, payload in answers.items()
    }
    return build_evaluation(
        context=_ctx(goal_revision=goal_revision, event_cursor=event_cursor),
        questions=questions,
        state=_state(),
        provider="scripted_shadow",
        provider_model="scripted-1",
        access_mode="fixture",
        answers=translated,
    )


def test_proposed_routes_stay_on_the_section_32_ladder():
    assert ROUTE_LADDER == (
        "observe",
        "hint",
        "investigate",
        "pause_and_investigate",
        "request_verification",
        "request_user_input",
    )
    readings = [
        _evaluation({}),
        _evaluation(_escalating_answers()),
        _evaluation(
            {
                "voice.loop.same_strategy": {"value": 0.9},
                "voice.loop.new_information": {"value": 0.1},
                "voice.loop.measurable_progress": {
                    "score": 0.0,
                    "legend": [0.0, 1.0, 2.0, 3.0],
                    "probabilities": {"0.0": 1.0},
                    "confidence": 0.9,
                },
            }
        ),
    ]
    for evaluation in readings:
        route = propose_route(evaluation).route
        assert route in ROUTE_LADDER or route == ROUTE_ESCALATE_SYSTEM_TWO


def test_proposed_route_names_the_rule_it_read():
    evaluation = _evaluation(_escalating_answers())
    proposed = propose_route(evaluation)
    assert proposed.route == ROUTE_ESCALATE_SYSTEM_TWO
    assert proposed.rule == "recovery_system_two_needed"
    assert "voice.recovery.system_two_needed" in proposed.basis

    empty = propose_route(None)
    assert empty.route == ROUTE_OBSERVE
    assert empty.rule == "no_rule_matched"


# --------------------------------------------------------------------------- #
# JEV-10 §21/E2E-13 — same snapshot, two paths, one unchanged decision          #
# --------------------------------------------------------------------------- #


def test_triage_with_shadow_keeps_the_deterministic_hint_and_records_alongside():
    signals = [Signal(kind="loop", severity=2, fingerprint="fp-loop", message="same call again")]
    voice = Voice()
    harness = ShadowHarness(_ScriptedEvaluator(_escalating_answers()))
    outcome = _run(
        triage_with_shadow(
            voice=voice,
            signals=signals,
            step=5,
            evidence=["e1"],
            flags=_shadow_flags(),
            harness=harness,
            state=_state(),
            questions=_questions(),
            context=_ctx(),
        )
    )

    legacy_voice = Voice()
    legacy_hint = legacy_voice.consider(signals, 5, evidence=["e1"])

    assert outcome.hint == legacy_hint  # the deterministic decision is untouched
    assert outcome.path == PATH_SHADOW
    assert outcome.advisory["mode"] == "shadow"
    assert outcome.advisory["shadow_record"]["mutations"] == 0
    assert outcome.advisory["shadow_record"]["route"] is None
    assert outcome.advisory["shadow_record"]["baseline_route"] == ROUTE_HINT
    # the shadow ran on a shadow-labelled context (§49) …
    assert harness.records[-1]["context_shadow"] is True
    assert harness.last_evaluation.shadow is True
    assert harness.records[-1]["evaluation"]["mode"] == "shadow"
    # … and the deterministic path still spends its own budget exactly once
    assert voice.delivered == legacy_voice.delivered == 1


def test_late_shadow_result_is_inert_and_cannot_change_the_run():
    """§21/E2E-13: a late shadow answer is stored, flagged inert, and inert."""
    signals = [Signal(kind="loop", severity=2, fingerprint="fp-late", message="loop again")]
    harness = ShadowHarness(_ScriptedEvaluator(_escalating_answers()))
    outcome = _run(
        triage_with_shadow(
            voice=Voice(),
            signals=signals,
            step=9,
            flags=_shadow_flags(),
            harness=harness,
            state=_state(),
            questions=_questions(),
            context=_ctx(goal_revision=4, event_cursor=50),
            live=VersionStamp(goal_revision=6, event_cursor=90),
        )
    )

    record = harness.records[-1]
    assert record["stale"] is True
    assert record["inert"] is True
    assert record["route"] is None
    assert "goal_revision 4 < 6" in record["stale_reason"]
    # the run's own decision is the deterministic one, unchanged
    assert outcome.path == PATH_SHADOW
    assert outcome.hint == Voice().consider(signals, 9)


def test_shadow_provider_failure_degrades_to_the_existing_path():
    signals = [Signal(kind="loop", severity=2, fingerprint="fp-boom", message="same call again")]
    voice = Voice()
    outcome = _run(
        triage_with_shadow(
            voice=voice,
            signals=signals,
            step=3,
            flags=_shadow_flags(),
            harness=ShadowHarness(_RaisingEvaluator()),
            state=_state(),
            questions=_questions(),
            context=_ctx(),
        )
    )

    assert outcome.path == PATH_LEGACY
    assert outcome.reason == "shadow_error:SystemOneTimeout"
    assert outcome.hint == Voice().consider(signals, 3)


def test_triage_with_shadow_propagates_a_shadow_contract_violation():
    """A shadow that mutates must not be downgraded into a quiet legacy run."""
    run_state = {"tool_calls": []}
    signals = [Signal(kind="loop", severity=2, fingerprint="fp-bad", message="loop")]
    harness = ShadowHarness(
        _ScriptedEvaluator(mutate=lambda: run_state["tool_calls"].append("x")),
        state_probe=lambda: json.dumps(run_state, sort_keys=True),
    )
    with pytest.raises(SystemOneContractError):
        _run(
            triage_with_shadow(
                voice=Voice(),
                signals=signals,
                step=3,
                flags=_shadow_flags(),
                harness=harness,
                state=_state(),
                questions=_questions(),
                context=_ctx(),
            )
        )
    assert harness.violations  # the violation is visible, not swallowed


# --------------------------------------------------------------------------- #
# JEV-20 §98 — flags off: byte-identical to today's behaviour                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "flags",
    [
        None,
        SystemOneFlags.disabled(),
        SystemOneFlags.disabled().with_flag("enabled", True),
        SystemOneFlags.disabled().with_flag("enabled", True).with_flag("voice_triage", True),
    ],
)
def test_flags_off_is_byte_identical_to_the_existing_path(flags):
    signals = [
        Signal(kind="loop", severity=2, fingerprint="fp-a", message="first"),
        Signal(kind="error_burst", severity=3, fingerprint="fp-b", message="second"),
    ]
    shadow_voice, legacy_voice = Voice(), Voice()

    got = _run(
        triage_with_shadow(
            voice=shadow_voice,
            signals=signals,
            step=7,
            evidence=["e"],
            flags=flags,
            harness=ShadowHarness(_ScriptedEvaluator(_escalating_answers())),
            state=_state(),
            questions=_questions(),
            context=_ctx(),
        )
    )
    expected = triage_with_system_one(
        voice=legacy_voice, signals=signals, step=7, evidence=["e"], flags=flags, evaluation=None
    )

    assert got == expected  # same hint, same path, same reason, same advisory
    assert got.path == PATH_LEGACY
    # identical budget side effects: the shadow never spends a second hint —
    # the same object state as today's path, down to the dedup bookkeeping
    assert shadow_voice.delivered == legacy_voice.delivered == 1
    assert shadow_voice._seen == legacy_voice._seen  # noqa: SLF001 — identity of the budget state
    assert shadow_voice._last_step == legacy_voice._last_step  # noqa: SLF001
    assert got.hint is not None


def test_shadow_mode_off_keeps_the_legacy_path():
    signals = [Signal(kind="loop", severity=2, fingerprint="fp-c", message="loop")]
    voice = Voice()
    outcome = _run(
        triage_with_shadow(
            voice=voice,
            signals=signals,
            step=4,
            flags=SystemOneFlags.disabled().with_flag("enabled", True).with_flag(
                "provider", "llm_system_one_adapter"
            ),
            harness=ShadowHarness(_ScriptedEvaluator(_escalating_answers())),
            state=_state(),
            questions=_questions(),
            context=_ctx(),
        )
    )
    assert outcome.path == PATH_LEGACY
    assert outcome.reason == "shadow_mode_disabled"
    assert outcome.hint is not None


def test_no_hint_means_no_shadow_record():
    """Nothing to triage: no baseline, so no shadow comparison either."""
    voice = Voice()
    signals = [Signal(kind="loop", severity=2, fingerprint="fp-d", message="loop")]
    harness = ShadowHarness(_ScriptedEvaluator(_escalating_answers()))
    outcome = _run(
        triage_with_shadow(
            voice=voice,
            signals=signals,
            step=1,
            flags=_shadow_flags(),
            harness=harness,
            state=_state(),
            questions=_questions(),
            context=_ctx(),
        )
    )
    assert outcome.hint is not None  # the first consideration does deliver

    # the second one is inside the min-gap window → silence → no shadow record
    silent = _run(
        triage_with_shadow(
            voice=voice,
            signals=signals,
            step=1,
            flags=_shadow_flags(),
            harness=harness,
            state=_state(),
            questions=_questions(),
            context=_ctx(),
        )
    )
    assert silent.hint is None
    assert silent.path == PATH_LEGACY
    assert silent.reason == "no_hint"
    assert len(harness.records) == 1


def test_shadow_never_pretends_to_be_live():
    """An adapter-backed shadow run must still report live_jev=False (§41)."""
    harness = ShadowHarness(_ScriptedEvaluator(_escalating_answers()))
    record = _run(
        harness.run(
            state=_state(),
            questions=_questions(),
            context=_ctx(),
            baseline_route=ROUTE_HINT,
        )
    )
    assert record["evaluation"]["live_jev"] is False
    assert record["evaluation"]["provider_access_mode"] == "fixture"


def test_shadow_gate_accepts_a_choice_answer_without_inventing_confidence():
    """The Noul/Choice contract is unchanged by the shadow path (§38/JEV-02)."""
    answers = {
        "voice.loop.same_root_cause": {
            "choice": "same",
            "probabilities": {"same": 0.9, "different": 0.1},
            "confidence": 0.9,
        },
        "voice.loop.same_strategy": {"value": 0.9},
        "voice.loop.new_information": {"value": 0.05},
        "voice.loop.measurable_progress": {
            "score": 0.0,
            "legend": [0.0, 1.0, 2.0, 3.0],
            "probabilities": {"0.0": 1.0},
            "confidence": 0.9,
        },
    }
    harness = ShadowHarness(_ScriptedEvaluator(answers))
    record = _run(
        harness.run(
            state=_state(),
            questions=_questions(),
            context=_ctx(),
            baseline_route=ROUTE_HINT,
        )
    )
    assert record["proposed_route"] == "pause_and_investigate"
    assert record["proposed_rule"] == "loop_no_progress_without_new_information"
    assert record["evaluation"]["confidence"] == {
        "voice.loop.same_root_cause": 0.9,
        "voice.loop.measurable_progress": 0.9,
    }
    # Noul answers still carry no confidence field, in the record either
    assert "voice.loop.same_strategy" not in record["evaluation"]["confidence"]
    assert record["evaluation"]["probabilities"]["voice.loop.same_strategy"] == {"value": 0.9}
    assert isinstance(harness.last_evaluation.answer("voice.loop.same_root_cause"), ChoiceAnswer)
    assert isinstance(harness.last_evaluation.answer("voice.loop.same_strategy"), NoulAnswer)
