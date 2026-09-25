"""JEV-01/JEV-02 — provider-neutral System-One contract (ABNAHME §37/§38/§79/§80).

ROT-FIRST evidence, captured before the module existed:

    $ .venv/bin/python -m pytest tests/test_system_one_contract.py -p no:randomly
    E   ModuleNotFoundError: No module named 'godsv.system_one'
    1 error in 0.04s

What this file pins (behaviour, never source text):

* ONE contract suite runs against ``DisabledSystemOneEvaluator``, the
  LLM adapter and the TypeSafe adapter (with an injected test transport): they
  are interchangeable behind ``SystemOneEvaluator`` — the Pass condition of
  §79. No Voice/Computer-Use module may ever see a provider-shaped object.
* ``NoulAnswer`` carries **no** confidence field, and a TypeSafe-shaped payload
  that adds ``confidence`` to a Noul is rejected instead of silently invented
  (§38/§80). Choice/Score keep their distribution + confidence.
* probabilities must sum to 1 within tolerance; a Noul value must lie in 0..1.

Honesty note: the ``typesafe_stub`` parameter is a **test double** (injected
transport + credential handle). It proves the adapter translates a live-shaped
response into the internal contract; it is not evidence of a real Jev call
(JEV-07 stays ``blocked_external_access`` until authorized access exists).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from skaldir.system_one import (
    ACCESS_DISABLED,
    ACCESS_LIVE,
    ACCESS_LLM_ADAPTER,
    PROVIDER_DISABLED,
    PROVIDER_LLM_ADAPTER,
    PROVIDER_TYPESAFE,
    ChoiceAnswer,
    DisabledSystemOneEvaluator,
    EvaluationContext,
    NoulAnswer,
    ScoreAnswer,
    SystemOneContractError,
    SystemOneEvaluator,
    SystemOneState,
    parse_answer,
    state_fingerprint,
    validate_probabilities,
)
from skaldir.system_one_adapters import LLMSystemOneAdapter, TypeSafeJevEvaluator
from skaldir.system_one_questions import compose_voice_triage

# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


class CapturingLLM:
    """Minimal LLMClient double that records the exact prompt it received."""

    def __init__(self, response: str = "") -> None:
        self.response = response
        self.prompts: list[str] = []
        self.calls = 0

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        return self.response


def _run(coro):
    return asyncio.run(coro)


def _uniform(options: tuple[str, ...]) -> dict[str, float]:
    """A probability distribution that sums to exactly 1.0 (float-safe)."""
    share = round(1.0 / len(options), 6)
    probs = {option: share for option in options}
    probs[options[-1]] = round(1.0 - share * (len(options) - 1), 6)
    return probs


def _answer_payload(qset) -> dict:
    """Provider-shaped answers for a question set — one entry per question."""
    answers: dict[str, dict] = {}
    for question in qset.questions:
        if question.kind == "noul":
            answers[question.id] = {"value": 0.62}
        elif question.kind == "choice":
            probs = _uniform(question.options)
            answers[question.id] = {
                "choice": question.options[0],
                "probabilities": probs,
                "confidence": 0.71,
            }
        else:
            bins = tuple(str(value) for value in question.legend)
            answers[question.id] = {
                "score": question.legend[0],
                "legend": list(question.legend),
                "probabilities": _uniform(bins),
                "confidence": 0.55,
            }
    return answers


def _llm_response(qset) -> str:
    return json.dumps({"answers": _answer_payload(qset)})


def _stub_transport(qset):
    """Injected TypeSafe transport: returns a live-shaped response, no network."""

    async def transport(request: dict) -> dict:
        assert request["model"] == "jev-latest"
        return {
            "answers": _answer_payload(qset),
            "usage": {"input_tokens": 128, "output_tokens": 42},
            "sdk_retries": 1,
        }

    return transport


def _context(**overrides) -> EvaluationContext:
    base = {
        "run_id": "run-1",
        "goal_id": "goal-1",
        "goal_revision": 3,
        "policy_revision": 2,
        "event_cursor": 41,
        "project_id": "godsv",
    }
    base.update(overrides)
    return EvaluationContext(**base)


def _state(payload: dict | None = None) -> SystemOneState:
    body = payload or {"goal": "fix the flaky test", "signals": ["loop"], "step": 41}
    return SystemOneState(
        payload=body,
        input_scope="task",
        fingerprint=state_fingerprint(body),
    )


# --------------------------------------------------------------------------- #
# JEV-01 §79 — interchangeable implementations against ONE contract suite      #
# --------------------------------------------------------------------------- #


def _disabled(qset):
    return DisabledSystemOneEvaluator()


def _llm(qset):
    return LLMSystemOneAdapter(
        CapturingLLM(_llm_response(qset)),
        model="gv-system-one-test",
    )


def _typesafe_stub(qset):
    return TypeSafeJevEvaluator(
        credential_resolver=lambda context: {"handle": "treasury:typesafe/systemone"},
        transport=_stub_transport(qset),
        model="jev-latest",
    )


IMPLEMENTATIONS = [
    pytest.param(_disabled, PROVIDER_DISABLED, ACCESS_DISABLED, 0, id="disabled"),
    pytest.param(_llm, PROVIDER_LLM_ADAPTER, ACCESS_LLM_ADAPTER, None, id="llm_adapter"),
    pytest.param(_typesafe_stub, PROVIDER_TYPESAFE, ACCESS_LIVE, None, id="typesafe_stub"),
]


@pytest.mark.parametrize("factory,provider,access_mode,expected_answers", IMPLEMENTATIONS)
def test_every_implementation_is_a_system_one_evaluator(
    factory, provider, access_mode, expected_answers
):
    evaluator = factory(compose_voice_triage("loop"))
    assert isinstance(evaluator, SystemOneEvaluator)


@pytest.mark.parametrize("factory,provider,access_mode,expected_answers", IMPLEMENTATIONS)
def test_shared_contract_suite_over_every_implementation(
    factory, provider, access_mode, expected_answers
):
    """The one contract each implementation must satisfy (§37/§38)."""
    questions = compose_voice_triage("loop")
    state = _state()
    context = _context()
    evaluator = factory(questions)

    evaluation = _run(evaluator.evaluate(state=state, questions=questions, context=context))

    # provider identity is explicit and never invented
    assert evaluation.provider == provider
    assert evaluation.provider_access_mode == access_mode
    assert evaluation.provider_model

    # §38 required fields, all present and bound to the request
    for field_name in (
        "evaluation_id",
        "run_id",
        "goal_id",
        "goal_revision",
        "policy_revision",
        "event_cursor",
        "provider",
        "provider_model",
        "provider_access_mode",
        "question_pack_id",
        "question_pack_version",
        "state_fingerprint",
        "input_scope",
        "started_at",
        "completed_at",
        "latency_ms",
        "answers",
        "usage",
        "raw_ref",
    ):
        assert hasattr(evaluation, field_name), f"missing §38 field: {field_name}"

    assert evaluation.evaluation_id
    assert evaluation.run_id == "run-1"
    assert evaluation.goal_id == "goal-1"
    assert evaluation.goal_revision == 3
    assert evaluation.policy_revision == 2
    assert evaluation.event_cursor == 41
    assert evaluation.question_pack_id == questions.id
    assert evaluation.question_pack_version == questions.version
    assert evaluation.state_fingerprint == state.fingerprint
    assert evaluation.input_scope == "task"
    assert evaluation.completed_at >= evaluation.started_at
    assert evaluation.latency_ms >= 0
    assert isinstance(evaluation.usage, dict)

    # answers stay inside the requested question ids — no invented questions
    assert set(evaluation.answers) <= set(questions.ids)
    if expected_answers is not None:
        assert len(evaluation.answers) == expected_answers
    for answer in evaluation.answers.values():
        assert isinstance(answer, (NoulAnswer, ChoiceAnswer, ScoreAnswer))
        # every probability distribution that exists is already valid
        probs = getattr(answer, "probabilities", None)
        if probs:
            validate_probabilities(probs)

    # §49 evidence is derivable and always labels the provider honestly
    evidence = evaluation.evidence()
    for key in (
        "question_pack_id",
        "question_pack_version",
        "state_fingerprint",
        "provider",
        "provider_model",
        "provider_access_mode",
        "live_jev",
        "latency_ms",
        "usage",
        "attempts",
    ):
        assert key in evidence, f"missing §49 evidence field: {key}"
    assert evidence["provider"] == provider
    assert evidence["live_jev"] is (access_mode == ACCESS_LIVE)


@pytest.mark.parametrize("factory,provider,access_mode,expected_answers", IMPLEMENTATIONS)
def test_disabled_and_llm_are_never_labelled_live_jev(
    factory, provider, access_mode, expected_answers
):
    """Only an authorized live transport may claim live Jev (JEV-06 §84)."""
    questions = compose_voice_triage("loop")
    evaluation = _run(
        factory(questions).evaluate(state=_state(), questions=questions, context=_context())
    )
    if access_mode == ACCESS_LIVE:
        # the test double injects a transport; it is explicitly NOT a live claim
        assert evaluation.provider_access_mode == ACCESS_LIVE
    else:
        assert evaluation.live_jev is False
        assert evaluation.evidence()["live_jev"] is False


def test_disabled_evaluator_returns_no_answers_and_disabled_access():
    """Deactivation must not fabricate answers — the caller keeps its own path."""
    questions = compose_voice_triage("loop")
    evaluation = _run(
        DisabledSystemOneEvaluator().evaluate(
            state=_state(), questions=questions, context=_context()
        )
    )
    assert evaluation.answers == {}
    assert evaluation.provider == PROVIDER_DISABLED
    assert evaluation.provider_access_mode == ACCESS_DISABLED
    assert evaluation.live_jev is False


# --------------------------------------------------------------------------- #
# JEV-02 §80 — internal answer contract                                        #
# --------------------------------------------------------------------------- #


def test_noul_answer_has_no_confidence_field():
    assert "confidence" not in NoulAnswer.__dataclass_fields__
    answer = NoulAnswer(question_id="loop.same_root_cause", value=0.7)
    assert not hasattr(answer, "confidence")


def test_noul_payload_carrying_confidence_is_rejected_not_invented():
    """A TypeSafe-shaped Noul with a confidence field violates the documented
    contract (§3: Noul is a probability 0..1 *without* a separate confidence)."""
    with pytest.raises(SystemOneContractError):
        parse_answer(
            question_id="loop.same_root_cause",
            kind="noul",
            payload={"value": 0.7, "confidence": 0.9},
        )


def test_noul_payload_with_unknown_extra_key_is_rejected():
    with pytest.raises(SystemOneContractError):
        parse_answer(question_id="q", kind="noul", payload={"value": 0.5, "note": "extra"})


def test_noul_value_outside_zero_to_one_is_rejected():
    with pytest.raises(SystemOneContractError):
        parse_answer(question_id="q", kind="noul", payload={"value": 1.4})
    with pytest.raises(SystemOneContractError):
        NoulAnswer(question_id="q", value=-0.1)


def test_choice_answer_keeps_distribution_and_confidence():
    payload = {
        "choice": "same",
        "probabilities": {"same": 0.8, "different": 0.2},
        "confidence": 0.9,
    }
    answer = parse_answer(question_id="q", kind="choice", payload=payload)
    assert isinstance(answer, ChoiceAnswer)
    assert answer.choice == "same"
    assert answer.probabilities == {"same": 0.8, "different": 0.2}
    assert answer.confidence == 0.9


def test_score_answer_keeps_legend_distribution_and_confidence():
    payload = {
        "score": 2.0,
        "legend": [0.0, 1.0, 2.0, 3.0],
        "probabilities": {"0.0": 0.1, "1.0": 0.2, "2.0": 0.6, "3.0": 0.1},
        "confidence": 0.8,
    }
    answer = parse_answer(question_id="q", kind="score", payload=payload)
    assert isinstance(answer, ScoreAnswer)
    assert answer.score == 2.0
    assert answer.legend == (0.0, 1.0, 2.0, 3.0)
    assert answer.confidence == 0.8


def test_probabilities_must_sum_to_one_within_tolerance():
    with pytest.raises(SystemOneContractError):
        ChoiceAnswer(
            question_id="q",
            choice="a",
            probabilities={"a": 0.5, "b": 0.4},
            confidence=0.9,
        )
    # inside the tolerance the same distribution is accepted
    ChoiceAnswer(question_id="q", choice="a", probabilities={"a": 0.5, "b": 0.5}, confidence=0.9)


def test_confidence_outside_zero_to_one_is_rejected():
    with pytest.raises(SystemOneContractError):
        ChoiceAnswer(question_id="q", choice="a", probabilities={"a": 1.0}, confidence=1.2)


def test_choice_not_present_in_probabilities_is_rejected():
    with pytest.raises(SystemOneContractError):
        ChoiceAnswer(
            question_id="q",
            choice="other",
            probabilities={"a": 0.5, "b": 0.5},
            confidence=0.5,
        )


def test_unknown_answer_kind_is_a_contract_error():
    with pytest.raises(SystemOneContractError):
        parse_answer(question_id="q", kind="ordinal", payload={"value": 1})


def test_typesafe_adapter_rejects_noul_with_confidence_instead_of_inventing_it():
    """The provider path must apply the same strict validation (§42)."""
    questions = compose_voice_triage("loop")

    async def bad_transport(request: dict) -> dict:
        return {"answers": {"loop.same_root_cause": {"value": 0.5, "confidence": 0.99}}}

    evaluator = TypeSafeJevEvaluator(
        credential_resolver=lambda context: {"handle": "treasury:typesafe/systemone"},
        transport=bad_transport,
    )
    with pytest.raises(SystemOneContractError):
        _run(evaluator.evaluate(state=_state(), questions=questions, context=_context()))
