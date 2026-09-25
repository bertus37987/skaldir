"""JEV-09/JEV-14 — error-deadline policy + staleness binding (§43/§87/§92).

ROT-FIRST evidence, captured before the module existed:

    $ .venv/bin/python -m pytest tests/test_system_one_policy.py -p no:randomly
    E   ImportError: cannot import name 'LLMProviderError' from 'godsv.llm'   (0.21s)
    ... and, one symbol later, the same for godsv.system_one_policy.

What this file pins:

* §43/JEV-09: 401 is never retried, 422 is a contract error, 429/529 retry only
  inside the shared remaining deadline with bounded backoff, SDK-internal
  retries are part of the total attempt count, and a provider error never
  disables runtime policy;
* §92/JEV-14: a late System-One answer evaluated against an older
  goal/policy/event/question-pack version is inert — it is refused and never
  drives a decision;
* §44/§89 (structure only): risk classes R0..R4 with R4 never authorizable by
  System One alone, thresholds frozen before holdout and Noul handled
  differently from Choice/Score;
* §45: the shadow harness records but never mutates.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from skaldir.llm import LLMProviderError
from skaldir.system_one import (
    ACCESS_DISABLED,
    PROVIDER_DISABLED,
    ChoiceAnswer,
    DisabledSystemOneEvaluator,
    EvaluationContext,
    NoulAnswer,
    ScoreAnswer,
    SystemOneBlockedExternal,
    SystemOneContractError,
    SystemOneError,
    SystemOneState,
    SystemOneTimeout,
    build_evaluation,
    state_fingerprint,
)
from skaldir.system_one_policy import (
    R0,
    R4,
    RISK_POLICY,
    AppliedDecision,
    ShadowHarness,
    SystemOneGate,
    ThresholdProfile,
    VersionStamp,
    authorize,
    backoff_delay,
    classify_status,
    evaluation_precheck_signals,
    is_stale,
    plan_retry,
    total_attempts,
)
from skaldir.system_one_questions import compose_questions, compose_voice_triage


def _run(coro):
    return asyncio.run(coro)


def _state() -> SystemOneState:
    payload = {"goal": "fix the flaky test", "signals": ["loop"]}
    return SystemOneState(
        payload=payload, input_scope="task", fingerprint=state_fingerprint(payload)
    )


def _ctx(**overrides) -> EvaluationContext:
    base = {"run_id": "r", "goal_id": "g"}
    base.update(overrides)
    return EvaluationContext(**base)


# --------------------------------------------------------------------------- #
# JEV-09 §87 — error and deadline policy                                       #
# --------------------------------------------------------------------------- #


def test_status_classification_matches_the_documented_cases():
    assert classify_status(401) == "auth"
    assert classify_status(422) == "contract"
    assert classify_status(429) == "rate_limit"
    assert classify_status(529) == "overloaded"
    assert classify_status(None) == "unknown"


def test_401_is_not_retried():
    decision = plan_retry(status=401, attempt=1, max_attempts=3, remaining_deadline=30.0)
    assert decision.retry is False
    assert decision.reason == "auth_not_retryable"


def test_422_is_treated_as_a_contract_error_and_not_retried():
    decision = plan_retry(status=422, attempt=1, max_attempts=3, remaining_deadline=30.0)
    assert decision.retry is False
    assert decision.reason == "contract_error"


def test_429_retries_only_inside_the_shared_remaining_deadline():
    inside = plan_retry(status=429, attempt=1, max_attempts=3, remaining_deadline=5.0)
    exhausted = plan_retry(status=429, attempt=1, max_attempts=3, remaining_deadline=0.0)
    assert inside.retry is True
    assert inside.delay <= 5.0
    assert exhausted.retry is False
    assert exhausted.reason == "deadline_exhausted"


def test_529_uses_the_same_bounded_backoff_budget():
    first = plan_retry(status=529, attempt=1, max_attempts=3, remaining_deadline=10.0)
    second = plan_retry(status=529, attempt=2, max_attempts=3, remaining_deadline=10.0)
    third = plan_retry(status=529, attempt=3, max_attempts=3, remaining_deadline=10.0)
    assert first.retry is True and second.retry is True
    assert first.delay <= second.delay
    assert second.delay <= 2.0  # the cap bounds every attempt
    assert third.retry is False  # max attempts reached
    assert third.reason == "max_attempts"


def test_backoff_is_bounded_by_the_cap():
    assert backoff_delay(1, base=0.25, cap=2.0) == 0.25
    assert backoff_delay(10, base=0.25, cap=2.0) == 2.0


def test_timeout_is_retryable_within_the_deadline():
    decision = plan_retry(
        status=None, attempt=1, max_attempts=3, remaining_deadline=10.0, timeout=True
    )
    assert decision.retry is True
    exhausted = plan_retry(
        status=None, attempt=1, max_attempts=3, remaining_deadline=0.0, timeout=True
    )
    assert exhausted.retry is False


def test_sdk_internal_retries_count_into_the_total_attempt_count():
    assert total_attempts(client_attempts=1, sdk_retries=2) == 3
    assert total_attempts(client_attempts=2, sdk_retries=1) == 3


def test_provider_error_reaches_the_caller_as_a_classified_status():
    error = LLMProviderError("rate limited", status=429, sdk_retries=1)
    assert error.status == 429
    assert error.sdk_retries == 1
    assert isinstance(error, Exception)


# -- gate: a provider error must never disable runtime policy ------------------ #


class _BoomEvaluator:
    provider = "boom"
    provider_model = "boom-1"

    async def evaluate(self, *, state, questions, context):
        raise LLMProviderError("provider exploded", status=529)


class _CountingDecide:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, evaluation):
        self.calls += 1
        return AppliedDecision(inert=False, route="voice_hint", reason="consumed")


def test_provider_error_never_disables_runtime_policy():
    gate = SystemOneGate(_BoomEvaluator(), flags=None)
    questions = compose_voice_triage("loop")

    result = _run(gate.advise(state=_state(), questions=questions, context=_ctx()))

    assert result is None  # degraded: no advisory, caller keeps its own path
    assert gate.enabled is True  # the flag was not flipped by the error
    assert gate.errors  # the error is reported, structured
    assert gate.errors[-1]["status"] == 529

    # a later healthy call still works: no sticky disable
    healthy = DisabledSystemOneEvaluator()
    gate_ok = SystemOneGate(healthy, flags=None)
    ok = _run(gate_ok.advise(state=_state(), questions=questions, context=_ctx()))
    assert ok is not None
    assert ok.provider_access_mode == ACCESS_DISABLED


def test_gate_reports_a_blocked_provider_as_a_structured_blocker():
    gate = SystemOneGate(_BlockedEvaluator(), flags=None)
    questions = compose_voice_triage("loop")
    result = _run(gate.advise(state=_state(), questions=questions, context=_ctx()))
    assert result is None
    assert gate.errors[-1]["state"] == "blocked_external_access"


class _BlockedEvaluator:
    provider = "typesafe"
    provider_model = "jev-latest"

    async def evaluate(self, *, state, questions, context):
        raise SystemOneBlockedExternal(
            {"state": "blocked_external_access", "reason": "no credential"}
        )


# --------------------------------------------------------------------------- #
# JEV-14 §92 — staleness / version binding                                     #
# --------------------------------------------------------------------------- #


def _stamp(**overrides) -> VersionStamp:
    base = {
        "goal_revision": 4,
        "policy_revision": 2,
        "event_cursor": 50,
        "question_pack_version": "v1",
    }
    base.update(overrides)
    return VersionStamp(**base)


def _late_evaluation(**overrides):
    """A real late-arriving evaluation: evaluated against a superseded stamp."""
    questions = compose_voice_triage("loop")
    context = EvaluationContext(
        run_id="run-late",
        goal_id="goal-1",
        goal_revision=3,
        policy_revision=2,
        event_cursor=41,
    )
    fields = {
        "answers": {
            "loop.same_root_cause": NoulAnswer(question_id="loop.same_root_cause", value=0.9)
        },
        "question_pack_version": "v1",
    }
    fields.update(overrides)
    return build_evaluation(
        context=context,
        questions=questions,
        state=_state(),
        provider=PROVIDER_DISABLED,
        provider_model="",
        access_mode=ACCESS_DISABLED,
        answers=fields["answers"],
        question_pack_version=fields["question_pack_version"],
    )


def test_late_answer_against_a_newer_goal_revision_is_stale():
    evaluation = _late_evaluation()
    assert is_stale(evaluation, live=_stamp(goal_revision=4)) is True


def test_late_answer_against_a_newer_event_cursor_is_stale():
    assert is_stale(_late_evaluation(), live=_stamp(event_cursor=51)) is True


def test_answer_against_a_different_question_pack_version_is_stale():
    assert is_stale(_late_evaluation(), live=_stamp(question_pack_version="v2")) is True


def test_answer_against_the_live_stamp_is_not_stale():
    assert is_stale(_late_evaluation(), live=_stamp(goal_revision=3, event_cursor=41)) is False


def test_stale_evaluation_never_drives_a_decision():
    evaluation = _late_evaluation()
    gate = SystemOneGate(_WorkingEvaluator(), flags=None)
    decide = _CountingDecide()

    decision = gate.consume(evaluation, live=_stamp(goal_revision=4), decide=decide)

    assert decision.inert is True
    assert decision.route is None
    assert "stale" in decision.reason
    assert decide.calls == 0  # the answer never reached the decision
    assert gate.stale[-1]["evaluation_id"] == evaluation.evaluation_id


def test_fresh_evaluation_is_consumed():
    evaluation = _late_evaluation()
    gate = SystemOneGate(_WorkingEvaluator(), flags=None)
    decide = _CountingDecide()
    decision = gate.consume(
        evaluation, live=_stamp(goal_revision=3, event_cursor=41), decide=decide
    )
    assert decision.inert is False
    assert decide.calls == 1


class _WorkingEvaluator:
    provider = "disabled"
    provider_model = ""

    async def evaluate(self, *, state, questions, context):
        return None


# --------------------------------------------------------------------------- #
# §44/§89 — risk classes and calibration structure (optional criterion)         #
# --------------------------------------------------------------------------- #


def test_risk_classes_cover_r0_to_r4():
    assert set(RISK_POLICY) == {"R0", "R1", "R2", "R3", "R4"}


def test_r4_is_never_authorizable_by_system_one_alone():
    profile = ThresholdProfile(
        profile_id="dev-1",
        frozen=True,
        per_risk={R0: 0.5, "R1": 0.6, "R2": 0.7, "R3": 0.8, "R4": 0.0},
        noul_threshold=0.5,
        choice_threshold=0.6,
        score_threshold=0.6,
    )
    answer = NoulAnswer(question_id="q", value=1.0)
    decision = authorize(R4, answer, profile=profile)
    assert decision.auto is False
    assert decision.require_system_two is True
    assert decision.reason == "R4_never_system_one_alone"


def test_thresholds_are_per_risk_and_frozen_before_holdout():
    profile = ThresholdProfile(
        profile_id="dev-1",
        frozen=True,
        per_risk={R0: 0.4, "R1": 0.5, "R2": 0.7, "R3": 0.85, "R4": 0.0},
        noul_threshold=0.5,
        choice_threshold=0.6,
        score_threshold=0.6,
    )
    assert profile.frozen is True
    with pytest.raises(SystemOneContractError):
        profile.with_threshold(R0, 0.1)


def test_noul_is_handled_differently_from_choice_and_score():
    profile = ThresholdProfile(
        profile_id="dev-1",
        frozen=True,
        per_risk={R0: 0.4, "R1": 0.5, "R2": 0.7, "R3": 0.85, "R4": 0.0},
        noul_threshold=0.9,
        choice_threshold=0.1,
        score_threshold=0.1,
    )
    noul = NoulAnswer(question_id="q", value=0.6)
    choice = ChoiceAnswer(question_id="q", choice="yes", probabilities={"yes": 1.0}, confidence=0.9)
    score = ScoreAnswer(
        question_id="q",
        score=2.0,
        legend=(0.0, 2.0),
        probabilities={"0.0": 0.1, "2.0": 0.9},
        confidence=0.9,
    )
    # the Noul has no confidence: only its value is compared to the Noul threshold
    assert authorize(R0, noul, profile=profile).auto is False
    assert authorize(R0, choice, profile=profile).auto is True
    assert authorize(R0, score, profile=profile).auto is True


# --------------------------------------------------------------------------- #
# §45 — shadow harness                                                          #
# --------------------------------------------------------------------------- #


def test_shadow_harness_records_without_mutating():
    questions = compose_voice_triage("loop")
    harness = ShadowHarness(DisabledSystemOneEvaluator())
    record = _run(
        harness.run(
            state=_state(),
            questions=questions,
            context=_ctx(),
            baseline_route="voice_hint",
        )
    )
    assert record["mode"] == "shadow"
    assert record["mutations"] == 0
    assert record["route"] is None
    assert record["evaluation"]["provider_access_mode"] == ACCESS_DISABLED
    assert "disagreement" in record


# --------------------------------------------------------------------------- #
# §91/JEV-13 — completion precheck degrades to the existing path               #
# --------------------------------------------------------------------------- #


class _FakeFlags:
    """Duck-typed flags: only the attributes the hook reads (real defaults off)."""

    def __init__(self, **overrides) -> None:
        self.enabled = True
        self.provider = "llm_system_one_adapter"
        self.shadow_mode = False
        self.voice_triage = True
        self.completion_precheck = True
        self.computer_use = False
        for key, value in overrides.items():
            setattr(self, key, value)


def _completion_evaluation(
    *, goal_revision: int = 4, event_cursor: int = 50, pack_version: str = "v1"
):
    """A real evaluation record over the completion pack (no hand-made stub)."""
    questions = compose_questions("completion")
    return build_evaluation(
        context=EvaluationContext(
            run_id="r",
            goal_id="g",
            goal_revision=goal_revision,
            policy_revision=2,
            event_cursor=event_cursor,
        ),
        questions=questions,
        state=_state(),
        provider=PROVIDER_DISABLED,
        provider_model="disabled",
        access_mode=ACCESS_DISABLED,
        answers={
            "completion.claims_done": ChoiceAnswer(
                question_id="completion.claims_done",
                choice="yes",
                probabilities={"yes": 0.8, "no": 0.2},
                confidence=0.8,
            ),
            "completion.evidence_current": NoulAnswer(
                question_id="completion.evidence_current", value=0.2
            ),
        },
        question_pack_version=pack_version,
    )


def test_evaluation_precheck_signals_off_keeps_the_existing_completion_path():
    flags = _FakeFlags(completion_precheck=False)
    result = evaluation_precheck_signals(
        flags=flags,
        evaluation=_completion_evaluation(),
        live=_stamp(),
        completion_state={"claims_complete": True},
    )
    assert result is None


def test_evaluation_precheck_signals_never_certifies_completion_alone():
    flags = _FakeFlags()
    result = evaluation_precheck_signals(
        flags=flags,
        evaluation=_completion_evaluation(),
        live=_stamp(),
        completion_state={"claims_complete": True},
    )
    assert result is not None
    assert result.certifies is False
    assert result.signals


def test_evaluation_precheck_signals_refuses_a_stale_evaluation():
    flags = _FakeFlags()
    result = evaluation_precheck_signals(
        flags=flags,
        evaluation=_completion_evaluation(),
        live=_stamp(goal_revision=5),
        completion_state={"claims_complete": True},
    )
    assert result is None


def test_deadline_helpers_use_monotonic_time_not_wall_clock():
    started = time.monotonic()
    decision = plan_retry(status=429, attempt=1, max_attempts=3, remaining_deadline=1.0)
    assert decision.delay < 1.0
    assert time.monotonic() - started < 1.0


def test_policy_errors_are_system_one_errors():
    assert issubclass(SystemOneTimeout, SystemOneError)
    assert issubclass(SystemOneBlockedExternal, SystemOneError)


# --------------------------------------------------------------------------- #
# §43/JEV-09 end to end through the adapter's retry loop                       #
# --------------------------------------------------------------------------- #


class _FlakyLLM:
    """Fails with a scripted sequence of provider errors, then succeeds."""

    def __init__(self, errors: list[Exception], response: str) -> None:
        self.errors = list(errors)
        self.response = response
        self.calls = 0

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.response


def test_adapter_does_not_retry_a_401():
    from skaldir.system_one import SystemOneAuthError
    from skaldir.system_one_adapters import LLMSystemOneAdapter

    questions = compose_voice_triage("loop")
    llm = _FlakyLLM([LLMProviderError("bad key", status=401)], "{}")
    adapter = LLMSystemOneAdapter(llm, model="m", sleep=lambda delay: None)
    with pytest.raises(SystemOneAuthError):
        _run(
            adapter.evaluate(
                state=_state(), questions=questions, context=_ctx()
            )
        )
    assert llm.calls == 1  # 401 is never retried


def test_adapter_retries_a_429_inside_the_shared_deadline_then_succeeds():
    import json

    from skaldir.system_one_adapters import LLMSystemOneAdapter

    questions = compose_voice_triage("loop")
    response = json.dumps(
        {
            "answers": {
                question.id: {"value": 0.4}
                for question in questions.questions
                if question.kind == "noul"
            }
        }
    )
    llm = _FlakyLLM(
        [LLMProviderError("rate limited", status=429, sdk_retries=1)],
        response,
    )
    adapter = LLMSystemOneAdapter(llm, model="m", max_attempts=3, sleep=lambda delay: None)
    context = EvaluationContext(
        run_id="r", goal_id="g", deadline=time.time() + 30.0
    )
    evaluation = _run(adapter.evaluate(state=_state(), questions=questions, context=context))
    assert llm.calls == 2  # one retryable failure + one success
    # the SDK-internal retry stays visible in the total attempt count
    assert evaluation.sdk_retries == 1
    assert evaluation.attempts == 3


def test_adapter_stops_retrying_when_the_shared_deadline_is_exhausted():
    from skaldir.system_one import SystemOneRateLimited
    from skaldir.system_one_adapters import LLMSystemOneAdapter

    questions = compose_voice_triage("loop")
    llm = _FlakyLLM([LLMProviderError("rate limited", status=429)], "{}")
    adapter = LLMSystemOneAdapter(llm, model="m", max_attempts=3, sleep=lambda delay: None)
    context = EvaluationContext(run_id="r", goal_id="g", deadline=time.time() - 1.0)
    with pytest.raises(SystemOneRateLimited):
        _run(adapter.evaluate(state=_state(), questions=questions, context=context))
    assert llm.calls == 1  # no retry outside the shared deadline
