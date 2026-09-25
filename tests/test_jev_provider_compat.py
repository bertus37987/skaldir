"""JEV-07/JEV-08 (§15/§17) — TypeSafe request contract, the seven provider error
codes, retries inside the shared deadline and the fallback chain.

ROT-FIRST evidence, captured before this existed:

    $ .venv/bin/python -m pytest tests/test_jev_provider_compat.py -p no:randomly
    E   ImportError: cannot import name 'PROVIDER_ERROR_CODES' from
        'godsv.system_one_adapters'
        (/home/willy/projects/gods-voice/src/godsv/system_one_adapters.py)
    !!! Interrupted: 1 error during collection !!!
    1 error in 0.10s

What this file pins (behaviour, never source text):

* §15: the request to ``POST https://api.typesafe.ai/v1/systemone`` carries
  **exactly** the fields the goal documents (``model``, ``state``,
  ``questions``) — no invented field — with ``model=jev-latest``, the ``Bearer``
  scheme and ``application/json``; ``state`` may be a string, an object or an
  array; Noul, Choice and Score questions may be mixed in **one** call;
* §15: with no authorized credential the adapter stays
  ``blocked_external_access`` — the request shape is never a substitute for
  access, and nothing here is a live call;
* §17: the seven error codes are first-class names and every one of them is
  reachable from a concrete failure (401/422/429/529/timeout/transport/malformed);
* §17: retries stay inside the shared deadline, SDK retries are counted, and
  401/422 are never retried;
* §17: the fallback chain (authorized LLM System-One fallback → System-2 Voice
  fallback → degraded observe/blocked) is expressible and its rungs are
  distinguishable — an LLM fallback is never labelled live Jev.

Evidence class: contract + mock (transport and LLM doubles). NO live TypeSafe
call happened anywhere in this file: every provider is a transport or LLM
**double**. Where a double transport makes the adapter label a result
``live_jev=True``, that only proves the adapter's labelling contract — it is not
evidence of a live TypeSafe call, and the blocked path stays the honest state
(Jev is Early Access).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import pytest

from skaldir.system_one import (
    ACCESS_BLOCKED,
    PROVIDER_TYPESAFE,
    EvaluationContext,
    SystemOneBlockedExternal,
    SystemOneContractError,
    SystemOneState,
    SystemOneTimeout,
)
from skaldir.system_one_adapters import (
    DOCUMENTED_QUESTION_FIELDS,
    DOCUMENTED_REQUEST_FIELDS,
    FALLBACK_RUNG_DEGRADED,
    FALLBACK_RUNG_LLM,
    FALLBACK_RUNG_PRIMARY,
    FALLBACK_RUNG_SYSTEM2_VOICE,
    PROVIDER_ERROR_CODES,
    PROVIDER_MODES,
    TYPESAFE_AUTH_SCHEME,
    TYPESAFE_CONTENT_TYPE,
    TYPESAFE_ENDPOINT,
    TYPESAFE_MODEL,
    LLMSystemOneAdapter,
    ProviderErrorRecord,
    SystemOneFallbackChain,
    TypeSafeJevEvaluator,
    build_typesafe_request,
    classify_error,
    error_code_for_status,
    provider_mode_for,
    run_sync,
)
from skaldir.system_one_questions import Question, QuestionSet

# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


class ScriptedLLM:
    """A deterministic LLM double (never a live provider)."""

    def __init__(self, response: str = "", error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls = 0

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response


class StatusError(RuntimeError):
    """An SDK-shaped failure carrying an HTTP status."""

    def __init__(self, status: int | None, message: str = "", *, sdk_retries: int = 0) -> None:
        super().__init__(message or f"status={status}")
        self.status = status
        self.sdk_retries = sdk_retries


def _mixed_questions() -> QuestionSet:
    """Noul + Choice + Score in one set — §15 allows mixing them in one call."""
    return QuestionSet(
        id="mixed_contract_test",
        version="v1",
        owner="test",
        questions=(
            Question(
                id="test.noul.question",
                version="v1",
                owner="test",
                kind="noul",
                text="Is this one atomic question?",
                group="g",
            ),
            Question(
                id="test.choice.question",
                version="v1",
                owner="test",
                kind="choice",
                text="Which documented option applies?",
                group="g",
                options=("a", "b"),
            ),
            Question(
                id="test.score.question",
                version="v1",
                owner="test",
                kind="score",
                text="How strong is the signal?",
                group="g",
                legend=(0.0, 1.0, 2.0),
            ),
        ),
    )


def _answers_for(questions: QuestionSet, *, value: float = 0.5) -> dict:
    answers: dict[str, dict] = {}
    for question in questions.questions:
        if question.kind == "noul":
            answers[question.id] = {"value": value}
        elif question.kind == "choice":
            options = tuple(question.options)
            share = round(1.0 / len(options), 6)
            probabilities = {option: share for option in options}
            probabilities[options[-1]] = round(1.0 - share * (len(options) - 1), 6)
            answers[question.id] = {
                "choice": options[0],
                "probabilities": probabilities,
                "confidence": 0.9,
            }
        else:
            buckets = [str(item) for item in question.legend]
            share = round(1.0 / len(buckets), 6)
            probabilities = {bucket: share for bucket in buckets}
            probabilities[buckets[-1]] = round(1.0 - share * (len(buckets) - 1), 6)
            answers[question.id] = {
                "score": question.legend[0],
                "legend": list(question.legend),
                "probabilities": probabilities,
                "confidence": 0.9,
            }
    return answers


def _state() -> SystemOneState:
    return SystemOneState(payload={"task": "render_scene"}, input_scope="task")


def _context(**overrides) -> EvaluationContext:
    base = {"run_id": "run-1", "goal_id": "goal-1", "event_cursor": 3}
    base.update(overrides)
    return EvaluationContext(**base)


def _run(coro):
    return asyncio.run(coro)


async def _no_sleep(delay: float) -> None:
    """Tests never sit out a real backoff — the deadline logic is what is pinned."""
    return None


# --------------------------------------------------------------------------- #
# §15 — the documented request contract                                        #
# --------------------------------------------------------------------------- #


def test_the_documented_endpoint_and_scheme_are_pinned():
    assert TYPESAFE_ENDPOINT == "https://api.typesafe.ai/v1/systemone"
    assert TYPESAFE_MODEL == "jev-latest"
    assert TYPESAFE_AUTH_SCHEME == "Bearer"
    assert TYPESAFE_CONTENT_TYPE == "application/json"


def test_the_request_carries_only_the_documented_fields():
    """The request body is exactly ``{model, state, questions}`` — and ``questions`` is a
    **map** keyed by the question id, each entry the documented ``Question``
    (``{type, instructions, criteria}``). This is the fetched reference's shape
    (https://docs.typesafe.ai/api); it used to be a *list* of the registry's internal dicts
    (report 14 §9 / Q-1), which a live call would have refused with 422.
    """
    request = build_typesafe_request(_state(), _mixed_questions())
    assert set(request) == {"model", "state", "questions"}
    assert request["model"] == TYPESAFE_MODEL
    questions = request["questions"]
    assert isinstance(questions, dict), "the documented `questions` is map<string, Question>"
    assert list(questions) == [
        "test.noul.question",
        "test.choice.question",
        "test.score.question",
    ]
    # every entry carries the documented fields and nothing of the registry's own metadata
    assert [item["type"] for item in questions.values()] == ["noul", "choice", "score"]
    for entry in questions.values():
        assert set(entry) <= {"type", "instructions", "criteria"}
        assert entry["instructions"], "`instructions` is the documented required question text"
    assert set(questions["test.noul.question"]) == {"type", "instructions"}  # criteria optional
    assert questions["test.choice.question"]["criteria"] == {"a": None, "b": None}
    assert questions["test.score.question"]["criteria"] == ["0.0", "1.0", "2.0"]
    assert request["state"]["input_scope"] == "task"


def test_the_documented_example_request_is_reproducible_field_for_field():
    """The reference's own example (verbatim, fetched 2026-09-19) validates against ours.

    The example is a *fixture*, not invented data: it is what ``POST /v1/systemone`` is
    documented to accept, so a request of ours that fails the same strict reading would fail
    the provider too — and this is provable without a credential.
    """
    documented_example = {
        "state": "Help! My payouts have been failing for 3 days.",
        "model": "jev-latest",
        "questions": {
            "is_urgent": {
                "type": "noul",
                "instructions": "Does this convey urgency?",
                "criteria": {"true": "Explicitly time-sensitive", "false": "No urgency expressed"},
            },
            "department": {
                "type": "choice",
                "instructions": "Which team should handle this?",
                "criteria": {"billing": "Payments", "technical": None, "sales": "Pricing"},
            },
            "frustration": {
                "type": "score",
                "instructions": "How frustrated is the customer?",
                "criteria": ["Calm", "Frustrated", "Very angry"],
            },
        },
    }

    def documented_violations(body: dict) -> list[str]:
        """The reference's request rules, applied to any body (a strict, local reader)."""
        problems: list[str] = []
        unknown = set(body) - set(DOCUMENTED_REQUEST_FIELDS)
        if unknown:
            problems.append(f"unknown top-level field(s): {sorted(unknown)}")
        if not isinstance(body.get("state"), (str, dict, list)):
            problems.append("state must be string | object | array")
        if not isinstance(body.get("model"), str):
            problems.append("model must be a string")
        questions = body.get("questions")
        if not isinstance(questions, dict):
            problems.append("questions must be map<string, Question>, not "
                            f"{type(questions).__name__}")
            return problems
        for key, entry in questions.items():
            if not isinstance(entry, dict):
                problems.append(f"{key}: a Question must be an object")
                continue
            if set(entry) - set(DOCUMENTED_QUESTION_FIELDS):
                problems.append(f"{key}: unknown Question field(s): "
                                f"{sorted(set(entry) - set(DOCUMENTED_QUESTION_FIELDS))}")
            kind = entry.get("type")
            if kind not in ("noul", "choice", "score"):
                problems.append(f"{key}: type must be noul | choice | score")
            if not isinstance(entry.get("instructions"), str) or not entry["instructions"]:
                problems.append(f"{key}: instructions is required")
            criteria = entry.get("criteria")
            if kind == "noul" and criteria is not None and not isinstance(criteria, dict):
                problems.append(f"{key}: noul criteria is a map (or absent)")
            if kind == "choice":
                if not isinstance(criteria, dict) or len(criteria) < 2:
                    problems.append(f"{key}: choice criteria is a required map of options")
                elif not all(value is None or isinstance(value, str)
                             for value in criteria.values()):
                    problems.append(f"{key}: choice criteria values are string | null")
            if kind == "score":
                if not isinstance(criteria, list) or len(criteria) < 2:
                    problems.append(f"{key}: score criteria is a required array of >= 2 levels")
        return problems

    assert documented_violations(documented_example) == []  # the fixture is the real shape
    # ours must pass the very same reader, in every documented state shape
    for shape in ("object", "string", "array"):
        ours = build_typesafe_request(_state(), _mixed_questions(), state_shape=shape)
        assert documented_violations(ours) == [], (shape, documented_violations(ours))
    # and the one thing this reader exists to catch is the defect the report named
    broken = build_typesafe_request(_state(), _mixed_questions())
    broken["questions"] = list(broken["questions"].values())
    assert "questions must be map<string, Question>, not list" in documented_violations(broken)


def test_state_may_be_a_string_an_object_or_an_array():
    """The goal says ``state`` may be string | object | array — all three work."""
    as_object = build_typesafe_request(_state(), _mixed_questions(), state_shape="object")
    assert isinstance(as_object["state"], dict)
    as_string = build_typesafe_request(_state(), _mixed_questions(), state_shape="string")
    assert isinstance(as_string["state"], str)
    assert "render_scene" in as_string["state"]
    as_array = build_typesafe_request(_state(), _mixed_questions(), state_shape="array")
    assert isinstance(as_array["state"], list)
    for shape in ("object", "string", "array"):
        request = build_typesafe_request(_state(), _mixed_questions(), state_shape=shape)
        assert set(request) == {"model", "state", "questions"}
    with pytest.raises(SystemOneContractError):
        build_typesafe_request(_state(), _mixed_questions(), state_shape="protobuf")


def test_a_test_double_transport_receives_the_documented_request():
    seen: dict = {}

    async def transport(request: Mapping[str, Any]) -> dict:
        seen.update(request)
        questions = _mixed_questions()
        return {
            "answers": _answers_for(questions),
            "usage": {"input_tokens": 7},
            "sdk_retries": 0,
        }

    evaluation = _run(
        TypeSafeJevEvaluator(
            credential_resolver=lambda context: {"handle": "secret://typesafe/systemone"},
            transport=transport,
        ).evaluate(state=_state(), questions=_mixed_questions(), context=_context())
    )
    assert set(seen) == {"model", "state", "questions"}
    assert seen["model"] == TYPESAFE_MODEL
    assert evaluation.live_jev is True, "an injected transport is the live shape, labelled live"
    assert evaluation.provider == PROVIDER_TYPESAFE
    assert evaluation.usage.get("input_tokens") == 7


def test_no_credential_keeps_the_adapter_blocked_with_the_documented_shape():
    """The request shape is not access: without a credential the state stays blocked."""
    with pytest.raises(SystemOneBlockedExternal) as excinfo:
        _run(
            TypeSafeJevEvaluator().evaluate(
                state=_state(), questions=_mixed_questions(), context=_context()
            )
        )
    assert excinfo.value.blocker["state"] == ACCESS_BLOCKED
    assert excinfo.value.blocker["live_jev"] is False


# --------------------------------------------------------------------------- #
# §17 — the seven error codes                                                  #
# --------------------------------------------------------------------------- #


def test_the_seven_error_codes_are_first_class_names():
    assert PROVIDER_ERROR_CODES == (
        "connection_invalid",
        "request_contract_error",
        "provider_rate_limited",
        "provider_overloaded",
        "provider_timeout",
        "provider_unavailable",
        "adapter_error",
    )
    assert error_code_for_status(401) == "connection_invalid"
    assert error_code_for_status(422) == "request_contract_error"
    assert error_code_for_status(429) == "provider_rate_limited"
    assert error_code_for_status(529) == "provider_overloaded"
    assert error_code_for_status(None) is None
    assert error_code_for_status(500) is None


def test_every_code_is_reachable_from_a_concrete_failure():
    cases = {
        "connection_invalid": StatusError(401),
        "request_contract_error": StatusError(422),
        "provider_rate_limited": StatusError(429),
        "provider_overloaded": StatusError(529),
        "provider_timeout": TimeoutError("deadline exceeded"),
        "provider_unavailable": ConnectionError("connection refused"),
        "adapter_error": json.JSONDecodeError("malformed", "{", 0),
    }
    assert set(cases) == set(PROVIDER_ERROR_CODES)
    for expected, error in cases.items():
        assert classify_error(error) == expected, expected


def test_the_error_record_says_whether_a_retry_is_allowed():
    limited = ProviderErrorRecord.from_exception(
        StatusError(429, "slow down"), attempts=2, sdk_retries=1, remaining_deadline=5.0
    )
    assert limited.code == "provider_rate_limited"
    assert limited.retryable is True
    assert limited.as_dict()["attempts"] == 2
    assert limited.as_dict()["sdk_retries"] == 1
    auth = ProviderErrorRecord.from_exception(StatusError(401), remaining_deadline=5.0)
    assert auth.code == "connection_invalid"
    assert auth.retryable is False
    contract = ProviderErrorRecord.from_exception(StatusError(422), remaining_deadline=5.0)
    assert contract.retryable is False


# --------------------------------------------------------------------------- #
# §17 — retries inside the shared deadline                                     #
# --------------------------------------------------------------------------- #


def test_retries_stay_inside_the_shared_deadline():
    """429 twice, then success — three attempts, all within the deadline."""
    calls: list[float] = []
    questions = _mixed_questions()

    async def flaky(request: Mapping[str, Any]) -> dict:
        calls.append(1.0)
        if len(calls) < 3:
            raise StatusError(429, "rate limited")
        return {"answers": _answers_for(questions), "sdk_retries": 0}

    import time as _time

    evaluator = TypeSafeJevEvaluator(
        credential_resolver=lambda context: {"handle": "secret://typesafe/systemone"},
        transport=flaky,
        max_attempts=3,
        sleep=_no_sleep,
    )
    evaluation = run_sync(
        evaluator,
        state=_state(),
        questions=questions,
        context=_context(deadline=_time.time() + 10.0),
    )
    assert len(calls) == 3
    assert evaluation.attempts == 3


def test_an_exhausted_deadline_stops_the_retry_instead_of_burning_it():
    calls: list[int] = []

    async def always_limited(request: Mapping[str, Any]) -> dict:
        calls.append(1)
        raise StatusError(429, "rate limited")

    import time as _time

    evaluator = TypeSafeJevEvaluator(
        credential_resolver=lambda context: {"handle": "secret://typesafe/systemone"},
        transport=always_limited,
        max_attempts=3,
        sleep=_no_sleep,
    )
    context = _context(deadline=_time.time() - 1.0)  # the shared deadline has passed
    with pytest.raises(Exception) as excinfo:
        run_sync(evaluator, state=_state(), questions=_mixed_questions(), context=context)
    assert len(calls) == 1, "no retry outside the shared deadline (§67)"
    assert getattr(excinfo.value, "code", "") == "provider_rate_limited"


def test_a_401_is_never_retried_and_sdk_retries_are_counted():
    calls: list[int] = []

    async def unauthorized(request: Mapping[str, Any]) -> dict:
        calls.append(1)
        raise StatusError(401, "bad credential", sdk_retries=2)

    evaluator = TypeSafeJevEvaluator(
        credential_resolver=lambda context: {"handle": "secret://typesafe/systemone"},
        transport=unauthorized,
        max_attempts=3,
    )
    with pytest.raises(Exception) as excinfo:
        run_sync(evaluator, state=_state(), questions=_mixed_questions(), context=_context())
    assert len(calls) == 1, "401 is never retried (§43)"
    assert getattr(excinfo.value, "code", "") == "connection_invalid"
    assert evaluator.attempts >= 3, "one client attempt + two SDK-internal retries"


def test_a_provider_timeout_is_classified_and_retried_inside_the_deadline():
    calls: list[int] = []

    async def times_out(request: Mapping[str, Any]) -> dict:
        calls.append(1)
        raise SystemOneTimeout("deadline exceeded")

    import time as _time

    evaluator = TypeSafeJevEvaluator(
        credential_resolver=lambda context: {"handle": "secret://typesafe/systemone"},
        transport=times_out,
        max_attempts=2,
        sleep=_no_sleep,
    )
    with pytest.raises(Exception) as excinfo:
        run_sync(
            evaluator,
            state=_state(),
            questions=_mixed_questions(),
            context=_context(deadline=_time.time() + 10.0),
        )
    assert len(calls) == 2, "a timeout is retryable inside the shared deadline"
    assert getattr(excinfo.value, "code", "") == "provider_timeout"


# --------------------------------------------------------------------------- #
# §17 — the fallback chain                                                     #
# --------------------------------------------------------------------------- #


def test_provider_modes_are_the_four_contract_values():
    assert PROVIDER_MODES == (
        "typesafe_jev_live",
        "llm_system_one_adapter",
        "replay_fixture",
        "disabled",
    )
    assert provider_mode_for(PROVIDER_TYPESAFE, live_jev=True) == "typesafe_jev_live"
    assert provider_mode_for("llm_system_one_adapter") == "llm_system_one_adapter"
    assert provider_mode_for("disabled") == "disabled"
    assert provider_mode_for("replay_fixture") == "replay_fixture"
    assert provider_mode_for(PROVIDER_TYPESAFE, live_jev=True, replay=True) == "replay_fixture"
    with pytest.raises(SystemOneContractError):
        # a typesafe evaluation that is not live is a wiring bug, never a mode
        provider_mode_for(PROVIDER_TYPESAFE, live_jev=False)


def test_the_chain_walks_jev_to_the_llm_fallback_without_ever_claiming_live_jev():
    questions = _mixed_questions()
    llm = ScriptedLLM(json.dumps({"answers": _answers_for(questions)}))
    chain = SystemOneFallbackChain(
        primary=TypeSafeJevEvaluator(),  # no credential → blocked
        llm_fallback=LLMSystemOneAdapter(llm, model="fallback-model"),
        llm_fallback_authorized=True,
    )
    outcome = _run(
        chain.evaluate(state=_state(), questions=questions, context=_context())
    )
    assert outcome.rung == FALLBACK_RUNG_LLM
    assert outcome.evaluation is not None
    assert outcome.evaluation.live_jev is False
    assert outcome.evaluation.provider == "llm_system_one_adapter"
    assert "blocked_external_access" in outcome.error_codes, (
        "the blocked primary rung is named as blocked, not as an invented answer"
    )
    assert outcome.degraded is False


def test_an_unauthorized_llm_fallback_is_skipped_for_system_2():
    questions = _mixed_questions()
    llm = ScriptedLLM(json.dumps({"answers": _answers_for(questions)}))
    voice_calls: list[str] = []

    def voice_fallback(*, reason: str):
        voice_calls.append(reason)
        return {"route": "escalate_system_two", "reason": reason}

    chain = SystemOneFallbackChain(
        primary=TypeSafeJevEvaluator(),
        llm_fallback=LLMSystemOneAdapter(llm, model="fallback-model"),
        voice_fallback=voice_fallback,
        llm_fallback_authorized=False,
    )
    outcome = _run(chain.evaluate(state=_state(), questions=questions, context=_context()))
    assert outcome.rung == FALLBACK_RUNG_SYSTEM2_VOICE
    assert llm.calls == 0, "an unauthorized LLM fallback must not be called at all"
    assert voice_calls, "the System-2 Voice fallback was reached"
    assert outcome.evaluation is None, "System-2 decides; no System-One answer is invented"


def test_a_failed_primary_and_fallback_degrade_to_observe_and_blocked():
    async def broken(request: Mapping[str, Any]) -> dict:
        raise ConnectionError("connection refused")

    chain = SystemOneFallbackChain(
        primary=TypeSafeJevEvaluator(
            credential_resolver=lambda context: {"handle": "secret://typesafe/systemone"},
            transport=broken,
        ),
        llm_fallback=LLMSystemOneAdapter(ScriptedLLM(error=RuntimeError("llm down"))),
        llm_fallback_authorized=True,
    )
    outcome = _run(chain.evaluate(state=_state(), questions=_mixed_questions(), context=_context()))
    assert outcome.rung == FALLBACK_RUNG_DEGRADED
    assert outcome.degraded is True
    assert outcome.evaluation is None
    assert "provider_unavailable" in outcome.error_codes
    assert outcome.authorizes_action is False


def test_a_healthy_primary_rung_is_used_directly():
    questions = _mixed_questions()

    async def transport(request: Mapping[str, Any]) -> dict:
        return {"answers": _answers_for(questions), "sdk_retries": 0}

    chain = SystemOneFallbackChain(
        primary=TypeSafeJevEvaluator(
            credential_resolver=lambda context: {"handle": "secret://typesafe/systemone"},
            transport=transport,
        ),
        llm_fallback=LLMSystemOneAdapter(ScriptedLLM("{}")),
        llm_fallback_authorized=True,
    )
    outcome = _run(chain.evaluate(state=_state(), questions=questions, context=_context()))
    assert outcome.rung == FALLBACK_RUNG_PRIMARY
    assert outcome.error_codes == ()
    assert outcome.evaluation.live_jev is True


def test_the_adapters_fill_the_contract_provider_mode_integration():
    """INTEGRATION pin (no new behaviour): the §4.2 field the contract module
    derives/validates is really filled by both adapters and by the chain."""
    questions = _mixed_questions()
    llm = ScriptedLLM(json.dumps({"answers": _answers_for(questions)}))
    adapter_evaluation = _run(
        LLMSystemOneAdapter(llm, model="m").evaluate(
            state=_state(), questions=questions, context=_context()
        )
    )
    assert getattr(adapter_evaluation, "provider_mode", "") == "llm_system_one_adapter"

    async def transport(request: Mapping[str, Any]) -> dict:
        return {"answers": _answers_for(questions), "sdk_retries": 0}

    live_evaluation = _run(
        TypeSafeJevEvaluator(
            credential_resolver=lambda context: {"handle": "secret://typesafe/systemone"},
            transport=transport,
        ).evaluate(state=_state(), questions=questions, context=_context())
    )
    assert getattr(live_evaluation, "provider_mode", "") == "typesafe_jev_live"

    chain = SystemOneFallbackChain(
        primary=TypeSafeJevEvaluator(),
        llm_fallback=LLMSystemOneAdapter(
            ScriptedLLM(json.dumps({"answers": _answers_for(questions)}))
        ),
        llm_fallback_authorized=True,
    )
    outcome = _run(chain.evaluate(state=_state(), questions=questions, context=_context()))
    assert outcome.evaluation.provider_mode == "llm_system_one_adapter"
    assert outcome.evaluation.live_jev is False


def test_the_chain_marks_the_fallback_instead_of_hiding_it():
    """A fallback result names the failed primary rung — no silent substitution."""
    questions = _mixed_questions()
    llm = ScriptedLLM(json.dumps({"answers": _answers_for(questions)}))
    chain = SystemOneFallbackChain(
        primary=TypeSafeJevEvaluator(),
        llm_fallback=LLMSystemOneAdapter(llm, model="fallback-model"),
        llm_fallback_authorized=True,
    )
    outcome = _run(chain.evaluate(state=_state(), questions=questions, context=_context()))
    record = outcome.as_dict()
    assert record["rung"] == FALLBACK_RUNG_LLM
    assert record["primary_failed"] is True
    assert record["evaluation"]["live_jev"] is False
