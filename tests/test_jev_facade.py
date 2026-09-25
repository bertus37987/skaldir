"""The Jev façade: contracts, packs, decide() paths, and the substrate it reuses.

What this file pins (MVP/03 §4–§5, §7.5):

* the seven kinds, their ``Literal`` vocabulary, their request/answer models and
  their pack refs agree — and every pack resolves in the registry;
* every kind is reachable end to end with the offline fixture evaluator, and its
  happy path, its borderline path and its refuse/blocked path behave as the
  design's examples say;
* the questions are atomic (one dimension, ends in ``?``, ≤ 160 chars) and every
  concrete per-candidate id comes from a registered template — never from the
  caller;
* the facade is the *only* edge to the substrate's provider service, and no Jev
  module reaches a transport or the live adapter;
* a malformed answer is refused, a missing answer is a contract error, and a
  provider problem is a ``blocked`` decision — never a fabricated answer.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from skaldir.jev import (
    DECISION_KINDS,
    JEV_ANSWERS,
    JEV_CONTRACTS,
    JEV_REQUESTS,
    decide,
    decide_async,
    request_for,
    selftest,
)
from skaldir.jev import contracts as C
from skaldir.jev import packs as P
from skaldir.jev.fixture import (
    FixtureJevEvaluator,
    SilentFixtureEvaluator,
    blocked_evaluator,
)
from skaldir.jev.kinds import FLAG_FOR_KIND, PACK_FOR_KIND, JevKind, pack_ref_for
from skaldir.jev.rules import FORBIDDEN_EXACT, JevRuleViolation, assert_protected_memories_kept
from skaldir.jev.selftest import SELFTEST_CASES, case_request
from skaldir.system_one import SystemOneContractError
from skaldir.system_one_flags import SystemOneFlags

SRC = Path(__file__).resolve().parents[1] / "src"
JEV_DIR = SRC / "skaldir" / "jev"


# --------------------------------------------------------------------------- #
# payload helpers                                                               #
# --------------------------------------------------------------------------- #


def _noul(value: float) -> dict:
    return {"value": value}


def _choice(chosen: str, options, confidence: float = 0.9) -> dict:
    others = [str(option) for option in options if str(option) != chosen]
    weights = {option: 0.01 for option in others}
    weights[chosen] = 1.0 - sum(weights.values())
    return {"choice": chosen, "probabilities": weights, "confidence": confidence}


def _score(value: float, legend=(0.0, 1.0, 2.0, 3.0), confidence: float = 0.9) -> dict:
    keys = [f"{item:g}" for item in legend]
    chosen = f"{value:g}"
    weights = {key: 0.01 for key in keys if key != chosen}
    weights[chosen] = 1.0 - sum(weights.values())
    return {
        "score": value,
        "legend": list(legend),
        "probabilities": weights,
        "confidence": confidence,
    }


def _request(kind: str, **overrides):
    request = case_request(kind)
    return request.model_copy(update=overrides) if overrides else request


def _run(kind: str, answers=None, **overrides):
    request = _request(kind, **overrides)
    evaluator = FixtureJevEvaluator(answers or {})
    return decide(request, evaluator=evaluator), evaluator


# --------------------------------------------------------------------------- #
# the vocabulary and the registry                                               #
# --------------------------------------------------------------------------- #


def test_the_seven_kinds_are_one_vocabulary():
    from typing import get_args

    assert get_args(JevKind) == DECISION_KINDS
    # Six of MVP/03 §5, plus ``semantic_verification`` (MVP/93 §3).
    assert len(DECISION_KINDS) == 7
    assert DECISION_KINDS[-1] == "semantic_verification"
    for kind in DECISION_KINDS:
        assert kind in JEV_REQUESTS and kind in JEV_ANSWERS
        assert kind in FLAG_FOR_KIND and kind in PACK_FOR_KIND
        assert request_for(kind, SELFTEST_CASES[kind]).kind == kind


def test_every_kind_resolves_its_pack_in_the_registry():
    registry = P.default_jev_registry()
    for kind in DECISION_KINDS:
        pack = registry.pack(pack_ref_for(kind))
        assert pack.ref == pack_ref_for(kind)
        assert pack.purpose and pack.fallback_policy and pack.calibration_dataset_version
        assert pack.applicable_states
        assert pack.risk_class in {"R0", "R1", "R2", "R3", "R4"}
    # the six new packs are Jev's; voice_triage is the substrate's, reused
    assert {pack.ref for pack in P.JEV_PACKS} == {
        "memory_injection_v1",
        "routing_v1",
        "capability_ranking_v1",
        "compaction_v1",
        "adversarial_test_v1",
        "semantic_verification_v1",
    }
    assert "voice_triage" not in {pack.id for pack in P.JEV_PACKS}


def test_voice_triage_pack_is_reused_not_forked():
    """§27: the substrate's own pack object and composition, not a copy."""
    from skaldir.system_one_questions import (
        compose_voice_triage as substrate_compose,
    )
    from skaldir.system_one_questions import (
        default_question_registry,
    )

    substrate_registry = default_question_registry()
    substrate = substrate_registry.pack("voice_triage_v1")
    ours = request_for("voice_triage", SELFTEST_CASES["voice_triage"])
    composed = P.compose_voice_triage(ours)
    assert composed.id == "voice_triage" and composed.version == "v1"
    # the same narrowing the substrate's own composer performs for this trigger
    assert composed.ids == substrate_compose("loop", registry=substrate_registry).ids
    assert set(composed.ids) <= set(substrate.ids())
    assert P.default_jev_registry().pack("voice_triage_v1") is substrate


def test_requests_round_trip_and_refuse_unknown_fields():
    for kind in DECISION_KINDS:
        request = case_request(kind)
        again = JEV_REQUESTS[kind](**request.model_dump(mode="json"))
        assert again == request
        with pytest.raises(ValidationError):
            JEV_REQUESTS[kind](**{**request.model_dump(mode="json"), "undeclared": 1})


def test_questions_are_atomic_and_short():
    from skaldir.system_one_questions import MAX_QUESTION_CHARS

    for kind in DECISION_KINDS:
        if kind == "voice_triage":
            continue  # the substrate's pack has its own tests
        for pack in P.JEV_PACKS:
            for question in pack.questions:
                assert question.text.endswith("?"), question.id
                assert question.text.count("?") == 1, question.id
                assert len(question.text) <= MAX_QUESTION_CHARS, question.id
                if question.kind == "choice":
                    assert len(question.options) >= 2, question.id
                if question.kind == "score":
                    assert question.legend, question.id
    # ... and the *instantiated* questions of every kind stay atomic too
    for kind in DECISION_KINDS:
        composed = P.compose_questions_for(case_request(kind))
        assert composed.questions, kind
        for question in composed.questions:
            assert len(question.text) <= MAX_QUESTION_CHARS, question.id
            assert question.text.count("?") == 1, question.id


def test_concrete_question_ids_come_from_registered_templates():
    cases = {
        "memory_injection": ["mem.incident#812.relevant_now", "mem.incident#812.helpfulness"],
        "capability_ranking": ["cap.rank.cap.http_fetch.fit", "cap.rank.best"],
        "context_compaction": ["compaction.call.t1.keep", "compaction.result.t2.keep_verbatim"],
    }
    for kind, expected in cases.items():
        composed = P.compose_questions_for(case_request(kind))
        for question_id in expected:
            assert question_id in composed.ids, (kind, question_id)
            assert P.template_id_of(question_id), (kind, question_id)


def test_an_over_long_candidate_id_is_refused_not_truncated():
    state = dict(SELFTEST_CASES["memory_injection"])
    state["candidates"] = [
        {**state["candidates"][0], "memory_id": "m" * 200},
    ]
    with pytest.raises(ValueError) as excinfo:
        P.compose_questions_for(C.request_for("memory_injection", state))
    assert "too long" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 1. memory_injection                                                           #
# --------------------------------------------------------------------------- #


def test_memory_injection_happy_path():
    """§5.1 happy example: the incident is injected, the unrelated notes dropped."""
    state = dict(SELFTEST_CASES["memory_injection"])
    state["candidates"] = [
        {**state["candidates"][0], "memory_id": "incident#812"},
        {**state["candidates"][0], "memory_id": "research#1", "source": "research"},
        {**state["candidates"][0], "memory_id": "research#2", "source": "research"},
    ]
    request = C.request_for("memory_injection", state)
    evaluator = FixtureJevEvaluator(
        {
            "mem.incident#812.relevant_now": _noul(0.86),
            "mem.research#1.relevant_now": _noul(0.1),
            "mem.research#2.relevant_now": _noul(0.05),
        }
    )
    decision = decide(request, evaluator=evaluator)
    assert decision.status == "ok"
    answer = decision.answer
    assert answer.chosen == "inject"
    assert answer.injected == ("incident#812",)
    assert answer.ordering == answer.injected
    reads = {verdict.memory_id: verdict for verdict in answer.verdicts}
    assert reads["incident#812"].read == "inject_now"
    assert reads["incident#812"].rank == 1
    assert reads["incident#812"].stance == "supports"
    assert reads["research#1"].read == "drop"
    assert reads["research#2"].read == "drop"
    assert decision.confidence > 0


def test_memory_injection_borderline_stale_note_is_deferred():
    """§5.1 borderline: a stale note is deferred, not destroyed."""
    state = dict(SELFTEST_CASES["memory_injection"])
    state["candidates"] = [
        {
            "memory_id": "note#44",
            "source": "note",
            "title": "old provider timeout workaround",
            "age_days": 400,
            "retrieval_score": 0.62,
        }
    ]
    request = C.request_for("memory_injection", state)
    evaluator = FixtureJevEvaluator(
        {"mem.note#44.stance": _choice("stale", ["supports", "contradicts", "stale", "neutral"])}
    )
    decision = decide(request, evaluator=evaluator)
    assert decision.answer.verdicts[0].read == "defer"
    assert decision.answer.chosen == "defer"
    assert decision.answer.injected == ()


def test_memory_protected_candidate_is_always_injected():
    """Owner-authored memory: injected verbatim, even over a budget of one."""
    state = dict(SELFTEST_CASES["memory_injection"])
    state["candidates"] = [
        {**state["candidates"][0], "memory_id": "note#owner", "protected": True},
        {**state["candidates"][1], "memory_id": "note#other"},
    ]
    state["budget"] = {"max_inject": 1, "max_tokens": 800}
    request = C.request_for("memory_injection", state)
    evaluator = FixtureJevEvaluator(
        {
            "mem.note#owner.relevant_now": _noul(0.1),
            "mem.note#other.relevant_now": _noul(0.95),
        }
    )
    decision = decide(request, evaluator=evaluator)
    assert "note#owner" in decision.answer.injected
    reads = {v.memory_id: v.read for v in decision.answer.verdicts}
    assert reads["note#owner"] == "inject_now"
    assert reads["note#other"] == "defer"  # the budget pushed it out, not the owner's

    # ... and a crafted answer that drops it is a hard-rule violation (R4)
    crafted = C.MemoryInjectionAnswer(
        chosen="none",
        verdicts=(
            C.MemoryVerdict(memory_id="note#owner", read="drop", relevance=0.1),
            C.MemoryVerdict(memory_id="note#other", read="drop", relevance=0.2),
        ),
    )
    with pytest.raises(JevRuleViolation) as excinfo:
        assert_protected_memories_kept(request, crafted)
    assert excinfo.value.rule == "R4_no_goal_override"


def test_memory_refuses_without_goal_binding():
    request = _request("memory_injection", goal_id="", goal_text="")
    decision = decide(request, evaluator=FixtureJevEvaluator())
    assert decision.status == "refused"
    assert decision.refusal_reason == "no_goal_binding"
    assert decision.answer.chosen == "none"
    assert "deterministic retrieval order" in decision.fallback


def test_memory_refuses_without_candidates():
    decision = decide(_request("memory_injection", candidates=()), evaluator=FixtureJevEvaluator())
    assert decision.status == "refused"
    assert decision.refusal_reason == "no_candidates"


def test_a_candidate_set_larger_than_one_batch_is_refused_not_truncated():
    """§5.1 batching: one call = one batch of ≤ 8; the caller splits the rest."""
    from skaldir.jev.packs import CANDIDATES_PER_REQUEST

    state = dict(SELFTEST_CASES["memory_injection"])
    template = state["candidates"][0]
    state["candidates"] = [
        {**template, "memory_id": f"m{i}"} for i in range(CANDIDATES_PER_REQUEST + 1)
    ]
    request = C.request_for("memory_injection", state)
    decision = decide(request, evaluator=FixtureJevEvaluator())
    assert decision.status == "refused"
    assert decision.refusal_reason == "too_many_candidates"
    assert CANDIDATES_PER_REQUEST == 8

    # exactly one batch is fine
    state["candidates"] = state["candidates"][:CANDIDATES_PER_REQUEST]
    full_batch = decide(C.request_for("memory_injection", state), evaluator=FixtureJevEvaluator())
    assert full_batch.status == "ok"


# --------------------------------------------------------------------------- #
# 2. tool_routing                                                               #
# --------------------------------------------------------------------------- #


def test_tool_routing_happy_path():
    decision, evaluator = _run("tool_routing")
    assert decision.status == "ok"
    assert decision.answer.chosen_kind == "route"
    assert decision.answer.route.candidate_id == "browser.open_url"
    assert decision.answer.route.kind == "gui_subtool"
    assert decision.answer.requires_approval is False
    assert decision.answer.missing_inputs == ()
    assert evaluator.calls  # a provider call really happened


def test_tool_routing_below_threshold_widens_instead_of_guessing():
    """§5.2 borderline: two near-miss candidates ⇒ no_route, widen retrieval."""
    decision, _ = _run("tool_routing", {"routing.confident": _noul(0.3)})
    assert decision.answer.chosen_kind == "no_route"
    assert decision.answer.reason_code == "below_threshold_widen_retrieval"
    assert decision.answer.route is None


def test_tool_routing_computer_use_fallback_and_approval():
    state = dict(SELFTEST_CASES["tool_routing"])
    state["candidates"] = [
        {**state["candidates"][0], "permission_state": "needs_approval"},
    ]
    request = C.request_for("tool_routing", state)
    options = ["browser.open_url", "computer_use_fallback", "no_route"]
    fallback = decide(
        request,
        evaluator=FixtureJevEvaluator(
            {"routing.choice": _choice("computer_use_fallback", options)}
        ),
    )
    assert fallback.answer.chosen_kind == "computer_use_fallback"
    assert fallback.answer.route is None
    assert fallback.answer.reason_code == "computer_use_fallback"

    approved = decide(
        request,
        evaluator=FixtureJevEvaluator({"routing.choice": _choice("browser.open_url", options)}),
    )
    assert approved.answer.chosen_kind == "route"
    assert approved.answer.requires_approval is True
    assert approved.caution_level >= 1


def test_tool_routing_respects_the_callers_constraints():
    state = dict(SELFTEST_CASES["tool_routing"])
    state["constraints"] = {"allowed_kinds": ["native_tool"]}
    request = C.request_for("tool_routing", state)
    decision = decide(request, evaluator=FixtureJevEvaluator())
    assert decision.answer.chosen_kind == "no_route"
    assert decision.answer.reason_code == "kind_not_allowed"

    risky_state = dict(state)
    risky_state["constraints"] = {"allowed_kinds": ["gui_subtool"], "max_risk_class": "R0"}
    decision = decide(C.request_for("tool_routing", risky_state), evaluator=FixtureJevEvaluator())
    assert decision.answer.chosen_kind == "no_route"
    assert decision.answer.reason_code == "risk_not_acceptable"


# --------------------------------------------------------------------------- #
# 3. capability_ranking                                                         #
# --------------------------------------------------------------------------- #


def test_capability_ranking_happy_path():
    decision, _ = _run("capability_ranking")
    assert decision.status == "ok"
    assert decision.answer.best_fit == "cap.http_fetch"
    assert decision.answer.gap is False
    assert decision.answer.ranking[0].rank == 1
    assert decision.answer.ranking[0].fit == pytest.approx(1.0)
    assert "covers_need" in decision.answer.ranking[0].reasons


def test_capability_ranking_borderline_is_a_gap_not_a_guess():
    """§5.3 borderline: a near-miss out of scope ⇒ best_fit null + widen_query."""
    state = dict(SELFTEST_CASES["capability_ranking"])
    state["candidates"] = [
        {**state["candidates"][0], "candidate_id": "cap.a", "scope_match": False},
        {**state["candidates"][0], "candidate_id": "cap.b", "scope_match": False},
    ]
    request = C.request_for("capability_ranking", state)
    evaluator = FixtureJevEvaluator(
        {
            "cap.rank.cap.a.fit": _score(2.0),
            "cap.rank.cap.b.fit": _score(2.0),
            "cap.rank.cap.a.covers_need": _noul(0.9),
            "cap.rank.cap.b.covers_need": _noul(0.9),
            "cap.rank.gap": _noul(0.9),
        }
    )
    decision = decide(request, evaluator=evaluator)
    assert decision.answer.best_fit is None
    assert decision.answer.gap is True
    assert decision.answer.widen_query == "http fetch text"


def test_capability_ranking_refuses_an_empty_offer():
    decision = decide(
        _request("capability_ranking", candidates=()), evaluator=FixtureJevEvaluator()
    )
    assert decision.status == "refused"
    assert decision.refusal_reason == "no_candidates"
    assert decision.answer.gap is False  # an empty offer is not evidence of a gap
    assert decision.answer.best_fit is None


# --------------------------------------------------------------------------- #
# 4. context_compaction                                                         #
# --------------------------------------------------------------------------- #


def test_compaction_happy_path_keeps_both_calls():
    decision, _ = _run("context_compaction")
    assert [call.action for call in decision.answer.calls] == ["keep", "keep"]
    assert decision.answer.stats.candidates == 2
    assert decision.answer.stats.results_dropped == 0


def test_compaction_precedence_pinned_result_dropped_call_dropped():
    """§2.4 (7) precedence: pinned → keep/pinned; keep_result → drop_result; else drop."""
    state = dict(SELFTEST_CASES["context_compaction"])
    state["protected_message_ids"] = [1]  # t1's message is the pact statement
    request = C.request_for("context_compaction", state)
    evaluator = FixtureJevEvaluator(
        {
            "compaction.call.t1.keep": _noul(0.1),
            "compaction.result.t1.keep_verbatim": _noul(0.1),
            "compaction.call.t2.keep": _noul(0.72),
            "compaction.result.t2.keep_verbatim": _noul(0.22),
        }
    )
    decision = decide(request, evaluator=evaluator)
    calls = {call.id: call for call in decision.answer.calls}
    assert calls["t1"].action == "keep" and calls["t1"].reason == "pinned"
    assert calls["t2"].action == "drop_result" and calls["t2"].reason == "result_dropped"
    assert calls["t2"].head_chars_kept == request.options.truncate_head_chars
    assert decision.answer.stats.results_dropped == 1
    assert 0 < decision.answer.stats.reduction_ratio < 1


def test_compaction_drops_a_call_only_when_both_readings_are_low():
    state = dict(SELFTEST_CASES["context_compaction"])
    state["options"] = {"preserve_recent_messages": 0}
    request = C.request_for("context_compaction", state)
    evaluator = FixtureJevEvaluator(
        {
            "compaction.call.t1.keep": _noul(0.31),
            "compaction.result.t1.keep_verbatim": _noul(0.12),
            "compaction.call.t2.keep": _noul(0.91),
            "compaction.result.t2.keep_verbatim": _noul(0.87),
        }
    )
    decision = decide(request, evaluator=evaluator)
    calls = {call.id: call for call in decision.answer.calls}
    assert calls["t1"].action == "drop_call" and calls["t1"].head_chars_kept == 0
    assert calls["t2"].action == "keep" and calls["t2"].reason == "kept"
    assert decision.answer.stats.calls_dropped == 1


def test_compaction_refuses_a_state_that_cannot_fit():
    state = dict(SELFTEST_CASES["context_compaction"])
    state["options"] = {"max_state_tokens": 10}
    state["state"] = {**state["state"], "context": "x" * 1000}
    decision = decide(C.request_for("context_compaction", state), evaluator=FixtureJevEvaluator())
    assert decision.status == "refused"
    assert decision.refusal_reason == "state_too_large"
    assert "summary path" in decision.fallback


def test_compaction_a_call_without_a_result_is_not_a_candidate():
    state = dict(SELFTEST_CASES["context_compaction"])
    history = list(state["state"]["history"])
    history.append(
        {
            "i": 9,
            "role": "assistant",
            "tool_calls": [{"id": "t0", "tool": "Read", "input": "src/z.ts"}],
        }
    )
    state["state"] = {**state["state"], "history": tuple(history)}
    request = C.request_for("context_compaction", state)
    assert "t0" not in P.compaction_call_ids(request)  # no result ⇒ not a candidate
    assert "t1" in P.compaction_call_ids(request)
    decision = decide(request, evaluator=FixtureJevEvaluator())
    assert [call.id for call in decision.answer.calls] == ["t1", "t2"]


# --------------------------------------------------------------------------- #
# 5. voice_triage                                                               #
# --------------------------------------------------------------------------- #


def test_voice_triage_happy_path_wakes_on_a_loop():
    decision, _ = _run("voice_triage")
    assert decision.status == "ok"
    assert decision.answer.chosen == "wake_voice"
    assert decision.answer.urgency == 3
    assert decision.answer.route_hint == "pause_and_investigate"
    assert decision.answer.reason_code == "loop_no_progress_without_new_information"
    assert decision.answer.escalate_system_two is False
    assert decision.caution_level == 3


def test_voice_triage_defers_when_the_agent_is_on_a_sensible_path():
    """§5.5 borderline: recorded, no interruption now."""
    evaluator = FixtureJevEvaluator(
        {
            "voice.loop.same_root_cause": _choice(
                "different", ["same", "different", "unknown"]
            ),
        }
    )
    decision = decide(
        _request("voice_triage", agent_on_sensible_path=True), evaluator=evaluator
    )
    assert decision.answer.chosen == "defer"
    assert decision.answer.reason_code == "defer_sensible_path"


def test_voice_triage_refuses_without_state():
    decision = decide(
        _request("voice_triage", signals={}, trajectory_window=()),
        evaluator=FixtureJevEvaluator(),
    )
    assert decision.status == "refused"
    assert decision.refusal_reason == "no_state"
    assert decision.answer.chosen == "defer"  # never 'stay_silent' on a refusal


def test_voice_triage_escalation_route_agrees_with_its_flag():
    """The escalate route and its boolean are one statement, not two."""
    state = dict(SELFTEST_CASES["voice_triage"])
    state["trigger_kind"] = "capability_gap"  # the trigger group that asks for recovery
    evaluator = FixtureJevEvaluator({"voice.recovery.system_two_needed": _noul(0.9)})
    decision = decide(C.request_for("voice_triage", state), evaluator=evaluator)
    assert decision.answer.route_hint == "escalate_system_two"
    assert decision.answer.escalate_system_two is True
    assert decision.answer.reason_code == "recovery_system_two_needed"


# --------------------------------------------------------------------------- #
# 6. adversarial_test_planning                                                  #
# --------------------------------------------------------------------------- #


def test_adversarial_plan_happy_path():
    decision, _ = _run("adversarial_test_planning")
    assert decision.status == "ok"
    assert decision.answer.chosen == "plan"
    assert decision.answer.batch_size == 2  # parallel_slots
    assert decision.answer.plan[0].surface_id == "ui_flow:settings-save"
    assert decision.answer.plan[0].operation == "click"
    assert decision.answer.plan[0].target_ref.startswith("ui_flow:settings-save")
    assert decision.answer.plan[0].success_oracle.kind == "observed_state"
    assert decision.answer.plan[0].oracle_source == "deterministic"
    assert decision.answer.plan[0].risk_class == "R2"


def test_adversarial_plan_narrows_when_the_batch_budget_is_exhausted():
    state = dict(SELFTEST_CASES["adversarial_test_planning"])
    state["budget"] = {**state["budget"], "max_cases": 2}
    state["previous_results"] = [
        {"case_id": "adv-001", "surface_id": "ui_flow:settings-save", "outcome": "pass"}
    ]
    narrowed = decide(
        C.request_for("adversarial_test_planning", state), evaluator=FixtureJevEvaluator()
    )
    assert narrowed.answer.chosen == "narrow"
    assert narrowed.answer.batch_size == 1
    assert narrowed.answer.stop_reason == "batch_budget"


def test_adversarial_plan_refuses_an_empty_surface_offer():
    decision = decide(
        _request("adversarial_test_planning", surfaces=()), evaluator=FixtureJevEvaluator()
    )
    assert decision.status == "refused"
    assert decision.refusal_reason == "no_surfaces"
    assert decision.answer.chosen == "refuse"
    assert decision.answer.plan == ()


def test_adversarial_plan_refuses_network_when_the_budget_forbids_it():
    state = dict(SELFTEST_CASES["adversarial_test_planning"])
    state["surfaces"] = [
        {
            "surface_id": "api_endpoint:/api/tools",
            "name": "tools API",
            "kind": "api_endpoint",
            "risk": "R1",
            "seed_operations": ["http_request"],
        }
    ]
    with_network_state = {**state, "budget": {**state["budget"], "allow_network": True}}
    with_network = C.request_for("adversarial_test_planning", with_network_state)
    assert decide(with_network, evaluator=FixtureJevEvaluator()).status == "ok"

    state["budget"] = {**state["budget"], "allow_network": False}
    decision = decide(
        C.request_for("adversarial_test_planning", state), evaluator=FixtureJevEvaluator()
    )
    assert decision.status == "refused"
    assert decision.refusal_reason == "budget_forbids_network"


def test_adversarial_plan_refuses_a_case_already_covered():
    evaluator = FixtureJevEvaluator({"adv.case.covered_already": _noul(0.9)})
    decision = decide(_request("adversarial_test_planning"), evaluator=evaluator)
    assert decision.answer.chosen == "refuse"
    assert decision.answer.stop_reason == "case_already_covered"


def test_a_plan_can_never_target_something_unoffered():
    """Structural: target_ref must resolve to an offered surface (no selectors)."""
    request = _request("adversarial_test_planning")
    crafted = C.AdversarialPlanAnswer(
        plan=(
            C.TestCasePlan(
                case_id="adv-001",
                surface_id="ui_flow:settings-save",
                intent_class="persistence",
                operation="click",
                target_ref="//button[@id='save']",
            ),
        ),
        chosen="plan",
        batch_size=1,
    )
    from skaldir.jev.rules import assert_targets_resolve_to_offered_surfaces

    with pytest.raises(JevRuleViolation):
        assert_targets_resolve_to_offered_surfaces(request, crafted)

    scripted = C.AdversarialPlanAnswer(
        plan=(
            C.TestCasePlan(
                case_id="adv-002",
                surface_id="ui_flow:settings-save",
                intent_class="injection",
                operation="click",
                target_ref="ui_flow:settings-save#save-button",
                success_oracle=C.Oracle(kind="observed_state", expect="document.title == 'x'"),
            ),
        ),
        chosen="plan",
        batch_size=1,
    )
    with pytest.raises(JevRuleViolation):
        assert_targets_resolve_to_offered_surfaces(request, scripted)


# --------------------------------------------------------------------------- #
# blocked paths, contract errors, honesty                                       #
# --------------------------------------------------------------------------- #


def test_disabled_provider_is_blocked_with_the_callers_fallback():
    flags = SystemOneFlags(enabled=True, provider="disabled")
    decision = decide(case_request("memory_injection"), flags=flags)
    assert decision.status == "blocked"
    assert decision.refusal_reason == "provider_disabled"
    assert decision.provider_mode == "disabled"
    assert decision.fallback  # the caller's own path is named


def test_master_flag_off_is_blocked_and_says_so():
    decision = decide(case_request("tool_routing"), flags=SystemOneFlags.disabled())
    assert decision.status == "blocked"
    assert decision.refusal_reason == "system_one_disabled"


def test_no_service_at_all_is_blocked_not_guessed():
    decision = decide(case_request("capability_ranking"))
    assert decision.status == "blocked"
    assert decision.refusal_reason == "no_service"
    assert decision.provider_mode == "disabled"


def test_a_blocked_credential_becomes_a_blocked_decision():
    decision = decide(case_request("voice_triage"), evaluator=blocked_evaluator())
    assert decision.status == "blocked"
    assert "credential" in decision.refusal_reason
    assert decision.answer.chosen == "defer"


def test_missing_answer_is_a_contract_error():
    with pytest.raises(SystemOneContractError) as excinfo:
        decide(case_request("voice_triage"), evaluator=SilentFixtureEvaluator())
    assert "contract error" in str(excinfo.value)


def test_a_choice_outside_the_offered_options_is_a_contract_error():
    evaluator = FixtureJevEvaluator({"routing.choice": _choice("ghost.tool", ["ghost.tool"])})
    with pytest.raises(SystemOneContractError) as excinfo:
        decide(case_request("tool_routing"), evaluator=evaluator)
    assert "not one of the offered options" in str(excinfo.value)


def test_a_non_argmax_choice_is_a_contract_error():
    payload = {
        "choice": "browser.open_url",
        "probabilities": {"browser.open_url": 0.4, "computer_use_fallback": 0.6},
        "confidence": 0.9,
    }
    with pytest.raises(SystemOneContractError) as excinfo:
        decide(
            case_request("tool_routing"),
            evaluator=FixtureJevEvaluator({"routing.choice": payload}),
        )
    assert "argmax" in str(excinfo.value)


def test_a_score_outside_the_legend_is_a_contract_error():
    payload = {
        "score": 2.5,
        "legend": [0.0, 1.0, 2.0, 3.0],
        "probabilities": {"0": 0.1, "1": 0.1, "2": 0.1, "3": 0.7},
        "confidence": 0.9,
    }
    with pytest.raises(SystemOneContractError) as excinfo:
        decide(
            case_request("capability_ranking"),
            evaluator=FixtureJevEvaluator({"cap.rank.cap.http_fetch.fit": payload}),
        )
    assert "legend" in str(excinfo.value)


def test_a_broken_provider_payload_degrades_to_blocked():
    """A distribution that does not sum to 1 never becomes a decision (§42)."""
    payload = {
        "choice": "browser.open_url",
        "probabilities": {"browser.open_url": 0.4, "computer_use_fallback": 0.4},
        "confidence": 0.9,
    }
    decision = decide(
        case_request("tool_routing"), evaluator=FixtureJevEvaluator({"routing.choice": payload})
    )
    assert decision.status == "blocked"
    assert decision.refusal_reason  # the substrate's reason, not an invented answer


def test_every_decision_is_advisory_and_labelled_replay_fixture():
    for kind in DECISION_KINDS:
        decision = decide(case_request(kind), evaluator=FixtureJevEvaluator())
        assert decision.authority == "advisory"
        assert decision.certifies is False
        assert decision.grants == ()
        assert decision.provider_mode == "replay_fixture"
        assert decision.live_jev is False
        document = decision.model_dump(mode="json")
        forbidden = set(document) & FORBIDDEN_EXACT
        assert forbidden <= {"grants", "certifies"}, kind


def test_decide_accepts_a_kind_and_a_state_mapping():
    decision = decide(
        "voice_triage", SELFTEST_CASES["voice_triage"], evaluator=FixtureJevEvaluator()
    )
    assert decision.kind == "voice_triage"

    with pytest.raises(SystemOneContractError):
        decide(case_request("voice_triage"), SELFTEST_CASES["voice_triage"])


def test_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        request_for("nope", {})
    with pytest.raises(KeyError):
        P.composer_for("nope")


def test_decide_refuses_to_run_inside_a_running_event_loop():
    async def scenario():
        return decide(case_request("voice_triage"), evaluator=FixtureJevEvaluator())

    with pytest.raises(SystemOneContractError) as excinfo:
        asyncio.run(scenario())
    assert "decide_async" in str(excinfo.value)


def test_decide_async_is_the_runtime_entry_point():
    async def scenario():
        return await decide_async(case_request("voice_triage"), evaluator=FixtureJevEvaluator())

    decision = asyncio.run(scenario())
    assert decision.status == "ok"


# --------------------------------------------------------------------------- #
# structure: the facade stays a facade                                          #
# --------------------------------------------------------------------------- #

JEV_MODULES = sorted(path.name for path in JEV_DIR.glob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            if node.level:
                found.add(f"skaldir.{node.module}")
    return found


def test_only_the_decide_module_talks_to_the_service_facade():
    """§4.1: decide.py is the one module that touches SystemOneService."""
    touching = {
        path.name
        for path in JEV_DIR.glob("*.py")
        if "system_one_service" in " ".join(_imports(path))
    }
    assert touching == {"decide.py"}, sorted(touching)


def test_no_jev_module_reaches_a_transport_or_the_live_adapter():
    forbidden = {
        "httpx",
        "requests",
        "aiohttp",
        "urllib.request",
        "http.client",
        "skaldir.system_one_adapters",
        "skaldir.llm",
    }
    for path in JEV_DIR.glob("*.py"):
        offenders = _imports(path) & forbidden
        assert not offenders, f"{path.name} imports {sorted(offenders)}"
        text = path.read_text(encoding="utf-8")
        for needle in ("api.typesafe.ai", "/v1/systemone", "Bearer ", "jev-latest"):
            assert needle not in text, f"{path.name} carries vendor detail {needle!r}"


def test_the_contract_scan_report_is_part_of_the_package():
    scan = C.CONTRACT_SCAN
    assert scan["model_count"] == len(JEV_CONTRACTS) >= 30
    assert scan["field_count"] >= 100
    assert set(scan["answer_models"]) >= {model.__name__ for model in JEV_ANSWERS.values()}
    assert "permission_state" in scan["input_only_fields"]
    assert scan["constant_fields"]["JevDecision"] == {"grants": (), "certifies": False}


# --------------------------------------------------------------------------- #
# the self-audit                                                                #
# --------------------------------------------------------------------------- #


def test_selftest_proves_every_kind_is_reachable():
    report = selftest()
    assert report["ok"] is True
    assert report["exit_code"] == 0
    assert report["reachable"] is True and report["blocked_path_ok"] is True
    assert [row["kind"] for row in report["kinds"]] == list(DECISION_KINDS)
    for row in report["kinds"]:
        assert row["reachable"], row
        assert row["status"] == "ok"
        assert row["provider_mode"] == "replay_fixture"
        assert row["answer_model"] == JEV_ANSWERS[row["kind"]].__name__
        assert row["blocked_path"]["status"] == "blocked"
    assert [rule["rule"] for rule in report["rules"]] == [
        "R1_no_goal_pass",
        "R2_no_permission_grant",
        "R3_no_security_exception",
        "R4_no_goal_override",
    ]
    assert all(rule["test_present"] for rule in report["rules"])
    assert report["untested_rules"] == []
    assert "voice_triage_v1" in [pack["ref"] for pack in report["packs"]]
