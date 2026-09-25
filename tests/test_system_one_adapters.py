"""JEV-06/JEV-07 — LLM readiness adapter + blocked TypeSafe path (§84/§85).

ROT-FIRST evidence, captured before the module existed:

    $ .venv/bin/python -m pytest tests/test_system_one_adapters.py -p no:randomly
    E   ModuleNotFoundError: No module named 'godsv.system_one'   (0.19s, collection error)

What this file pins:

* the LLM-based System-One adapter is usable **today** (no TypeSafe access) and
  its record always shows ``provider='llm_system_one_adapter', live_jev=False``
  (§84) — an adapter result is never labelled live Jev;
* without authorized TypeSafe credentials the live adapter raises the
  structured ``blocked_external_access`` state instead of fabricating an answer
  (§85/JEV-07). Jev is Early Access: the honest state is ``blocked``, never
  ``pass``;
* SDK-internal retries count into the total attempt count (§43).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from skaldir.system_one import (
    ACCESS_BLOCKED,
    ACCESS_LLM_ADAPTER,
    PROVIDER_LLM_ADAPTER,
    PROVIDER_TYPESAFE,
    EvaluationContext,
    SystemOneBlockedExternal,
    SystemOneContractError,
    SystemOneEvaluator,
    SystemOneState,
    state_fingerprint,
)
from skaldir.system_one_adapters import LLMSystemOneAdapter, TypeSafeJevEvaluator
from skaldir.system_one_questions import compose_voice_triage


class ScriptedLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []
        self.tiers: list[str] = []

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        self.prompts.append(prompt)
        self.tiers.append(tier)
        return self.response


class FailingLLM:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        self.calls += 1
        raise self.error


def _run(coro):
    return asyncio.run(coro)


def _answers(qset, value: float = 0.5) -> dict:
    """Kind-correct provider payloads — the contract is per answer kind (§38)."""
    answers: dict[str, dict] = {}
    for question in qset.questions:
        if question.kind == "noul":
            answers[question.id] = {"value": value}
        elif question.kind == "choice":
            options = question.options
            share = round(1.0 / len(options), 6)
            probs = {option: share for option in options}
            probs[options[-1]] = round(1.0 - share * (len(options) - 1), 6)
            answers[question.id] = {
                "choice": options[0],
                "probabilities": probs,
                "confidence": value,
            }
        else:
            bins = [str(item) for item in question.legend]
            share = round(1.0 / len(bins), 6)
            probs = {bucket: share for bucket in bins}
            probs[bins[-1]] = round(1.0 - share * (len(bins) - 1), 6)
            answers[question.id] = {
                "score": question.legend[0],
                "legend": list(question.legend),
                "probabilities": probs,
                "confidence": value,
            }
    return answers


def _state() -> SystemOneState:
    payload = {"goal": "fix the flaky test", "signals": ["loop"]}
    return SystemOneState(
        payload=payload, input_scope="task", fingerprint=state_fingerprint(payload)
    )


def _context(**overrides) -> EvaluationContext:
    base = {"run_id": "run-7", "goal_id": "goal-7", "event_cursor": 9}
    base.update(overrides)
    return EvaluationContext(**base)


# --------------------------------------------------------------------------- #
# JEV-06 §84 — the readiness adapter usable today                              #
# --------------------------------------------------------------------------- #


def test_llm_adapter_evaluates_the_real_question_pack_today():
    questions = compose_voice_triage("loop")
    llm = ScriptedLLM(json.dumps({"answers": _answers(questions)}))
    adapter = LLMSystemOneAdapter(llm, model="gv-system-one-test")

    evaluation = _run(adapter.evaluate(state=_state(), questions=questions, context=_context()))

    assert evaluation.provider == PROVIDER_LLM_ADAPTER
    assert evaluation.provider_model == "gv-system-one-test"
    assert evaluation.provider_access_mode == ACCESS_LLM_ADAPTER
    assert set(evaluation.answers) == set(questions.ids)
    assert llm.prompts, "the adapter must build a prompt from the question pack"


def test_llm_adapter_prompt_contains_the_atomic_questions():
    questions = compose_voice_triage("loop")
    llm = ScriptedLLM(json.dumps({"answers": _answers(questions)}))
    adapter = LLMSystemOneAdapter(llm, model="m")
    _run(adapter.evaluate(state=_state(), questions=questions, context=_context()))
    prompt = llm.prompts[0]
    for question in questions.questions:
        assert question.text in prompt
        assert question.id in prompt


def test_adapter_result_is_never_labelled_live_jev():
    questions = compose_voice_triage("loop")
    llm = ScriptedLLM(json.dumps({"answers": _answers(questions)}))
    evaluation = _run(
        LLMSystemOneAdapter(llm, model="m").evaluate(
            state=_state(), questions=questions, context=_context()
        )
    )
    assert evaluation.live_jev is False
    evidence = evaluation.evidence()
    assert evidence["provider"] == "llm_system_one_adapter"
    assert evidence["live_jev"] is False
    assert evidence["provider_access_mode"] == ACCESS_LLM_ADAPTER


def test_adapter_rejects_a_malformed_response_as_contract_error():
    questions = compose_voice_triage("loop")
    llm = ScriptedLLM("not json at all")
    with pytest.raises(SystemOneContractError):
        _run(
            LLMSystemOneAdapter(llm, model="m").evaluate(
                state=_state(), questions=questions, context=_context()
            )
        )


def test_adapter_answers_only_the_registered_questions():
    """An answer for an unknown question id is a contract violation."""
    questions = compose_voice_triage("loop")
    payload = _answers(questions)
    payload["voice.invented.question"] = {"value": 0.5}
    llm = ScriptedLLM(json.dumps({"answers": payload}))
    with pytest.raises(SystemOneContractError):
        _run(
            LLMSystemOneAdapter(llm, model="m").evaluate(
                state=_state(), questions=questions, context=_context()
            )
        )


def test_llm_adapter_records_usage_from_the_llm_call():
    questions = compose_voice_triage("loop")
    llm = ScriptedLLM(json.dumps({"answers": _answers(questions)}))
    evaluation = _run(
        LLMSystemOneAdapter(llm, model="m").evaluate(
            state=_state(), questions=questions, context=_context()
        )
    )
    assert evaluation.usage.get("prompt_chars", 0) > 0
    assert evaluation.usage.get("response_chars", 0) > 0


# --------------------------------------------------------------------------- #
# JEV-07 §85 — blocked external access, never a fake answer                    #
# --------------------------------------------------------------------------- #


def test_typesafe_adapter_is_behind_the_shared_protocol():
    adapter = TypeSafeJevEvaluator()
    assert isinstance(adapter, SystemOneEvaluator)


def test_typesafe_without_credential_reports_blocked_external_access():
    """No authorized credential → structured blocker, never a fabricated answer."""
    questions = compose_voice_triage("loop")
    adapter = TypeSafeJevEvaluator(credential_resolver=None)

    with pytest.raises(SystemOneBlockedExternal) as excinfo:
        _run(adapter.evaluate(state=_state(), questions=questions, context=_context()))

    blocker = excinfo.value.blocker
    assert blocker["state"] == ACCESS_BLOCKED
    assert blocker["acceptance_state"] == ACCESS_BLOCKED
    assert blocker["provider"] == PROVIDER_TYPESAFE
    assert blocker["live_jev"] is False
    assert "credential" in blocker["reason"].lower()
    # a blocker is not an evaluation: no answers can be invented from it
    assert "answers" not in blocker


def test_typesafe_credential_resolver_returning_nothing_is_blocked():
    questions = compose_voice_triage("loop")
    adapter = TypeSafeJevEvaluator(credential_resolver=lambda context: None)
    with pytest.raises(SystemOneBlockedExternal):
        _run(adapter.evaluate(state=_state(), questions=questions, context=_context()))


def test_blocked_path_never_returns_a_pass_shaped_object():
    questions = compose_voice_triage("loop")
    adapter = TypeSafeJevEvaluator()
    try:
        _run(adapter.evaluate(state=_state(), questions=questions, context=_context()))
    except SystemOneBlockedExternal as exc:
        assert exc.blocker["state"] != "pass"
        # the honest state is a blocker; there is no evaluation to consume
        assert not hasattr(exc, "answers")
    else:  # pragma: no cover — would be a fabricated success
        pytest.fail("the TypeSafe adapter produced an answer without authorized access")


def test_typesafe_with_injected_transport_translates_a_live_shaped_response():
    """A test double proves the translation; it is *not* a live Jev call."""
    questions = compose_voice_triage("loop")

    async def transport(request: dict) -> dict:
        assert request["state"]["payload"]["goal"] == "fix the flaky test"
        # the documented shape: a MAP keyed by the question id, `{type, instructions, ...}`
        assert list(request["questions"]) == list(questions.ids)
        assert request["questions"][questions.ids[0]]["type"] == questions.questions[0].kind
        assert request["questions"][questions.ids[0]]["instructions"] == questions.questions[0].text
        return {"answers": _answers(questions), "usage": {"input_tokens": 12}, "sdk_retries": 0}

    adapter = TypeSafeJevEvaluator(
        credential_resolver=lambda context: {"handle": "treasury:typesafe/systemone"},
        transport=transport,
        model="jev-latest",
    )
    evaluation = _run(adapter.evaluate(state=_state(), questions=questions, context=_context()))
    assert evaluation.provider == PROVIDER_TYPESAFE
    assert evaluation.provider_model == "jev-latest"
    assert set(evaluation.answers) == set(questions.ids)


# --------------------------------------------------------------------------- #
# §43 — SDK-internal retries are visible in the total attempt count            #
# --------------------------------------------------------------------------- #


def test_sdk_internal_retries_are_counted_in_the_total_attempts():
    questions = compose_voice_triage("loop")

    async def transport(request: dict) -> dict:
        return {"answers": _answers(questions), "sdk_retries": 2, "usage": {}}

    adapter = TypeSafeJevEvaluator(
        credential_resolver=lambda context: {"handle": "h"},
        transport=transport,
    )
    evaluation = _run(adapter.evaluate(state=_state(), questions=questions, context=_context()))
    assert evaluation.attempts == 3  # one client attempt + two SDK-internal retries
    assert evaluation.evidence()["attempts"] == 3
