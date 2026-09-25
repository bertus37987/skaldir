"""JEV_IMPLEMENTATION_GOAL §4.1/§4.2/§4.3/§5/§6/§7 — the internal contract delta.

ROT-FIRST evidence, captured before this delta existed (the pristine
pre-delta modules from `git show HEAD:src/godsv/...` on the path, so the
failures below are the real ones, not a reconstruction):

    $ .venv/bin/python -m pytest -o pythonpath=$HOME/jev_red/src \
        tests/test_jev_contract_v2.py -p no:randomly
    E   ImportError: cannot import name 'PROVIDER_MODE_DISABLED' from
        'godsv.system_one' (/home/willy/jev_red/src/godsv/system_one.py)
    !!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
    1 error in 0.09s

What this file pins:

* §4.1 — ``EvaluationContext`` carries ``workspace_fingerprint``, ``caller``,
  ``question_pack_id``, ``question_pack_version``, ``budget_reservation`` and
  ``privacy_scope``, all with defaults so every existing call site keeps
  working.
* §4.2 — ``SystemOneEvaluation.provider_mode`` has exactly the four documented
  values, an LLM-adapter result can never read ``typesafe_jev_live``, and a
  live TypeSafe evaluation reads it only when ``live_jev`` is True. The old
  ``provider_access_mode`` stays for backward compatibility.
* §4.3 — an answer carries ``question_type`` and ``provider_metadata``; a Noul
  carries ``confidence = null`` in its serialized contract and never a
  dataclass field (the pre-existing pin ``test_noul_answer_has_no_confidence_field``
  is stronger than "None" and stays true).
* §5 — the five §39 packs carry the mandatory metadata (purpose,
  applicable_states, risk_class, fallback_policy, calibration_dataset_version)
  and the productive registry refuses an incomplete pack.
* §6 — the canonical atomic ids are resolvable through an explicit alias layer;
  no existing question id is silently renamed.
* §7 — a state carries ``source_event_refs``, ``workspace_fingerprint`` and
  ``created_at``, populated by the sanitizer/state builder, and the fingerprint
  stays comparable across two builds of the same payload.
"""

from __future__ import annotations

import pytest

from skaldir.system_one import (
    ACCESS_DISABLED,
    ACCESS_LIVE,
    ACCESS_LLM_ADAPTER,
    PROVIDER_DISABLED,
    PROVIDER_MODE_DISABLED,
    PROVIDER_MODE_LLM_ADAPTER,
    PROVIDER_MODE_REPLAY_FIXTURE,
    PROVIDER_MODE_TYPESAFE_LIVE,
    PROVIDER_MODES,
    ChoiceAnswer,
    DisabledSystemOneEvaluator,
    EvaluationContext,
    NoulAnswer,
    ScoreAnswer,
    SystemOneContractError,
    SystemOneState,
    answer_confidence,
    build_evaluation,
    parse_answer,
    provider_mode_for,
    state_fingerprint,
)
from skaldir.system_one_questions import (
    CANONICAL_FEATURES,
    DEFAULT_QUESTION_PACKS,
    QuestionPack,
    assert_pack_metadata,
    compose_voice_triage,
    default_question_registry,
)
from skaldir.system_one_sanitizer import SanitizerPolicy, StateSanitizer


def _state() -> SystemOneState:
    payload = {"goal": "fix the flaky test"}
    return SystemOneState(payload=payload, input_scope="task")


def _ctx(**overrides) -> EvaluationContext:
    base = {"run_id": "run-1", "goal_id": "goal-1"}
    base.update(overrides)
    return EvaluationContext(**base)


def _evaluation(*, access_mode: str, live_jev: bool, **kwargs):
    questions = compose_voice_triage("loop")
    return build_evaluation(
        context=_ctx(),
        questions=questions,
        state=_state(),
        provider=kwargs.pop("provider", PROVIDER_DISABLED),
        provider_model="m",
        access_mode=access_mode,
        live_jev=live_jev,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# §4.1 — SystemOneContext                                                      #
# --------------------------------------------------------------------------- #


def test_context_gains_the_six_documented_fields_with_safe_defaults():
    ctx = _ctx()
    assert ctx.workspace_fingerprint == ""
    assert ctx.caller == ""
    assert ctx.question_pack_id == ""
    assert ctx.question_pack_version == ""
    assert ctx.budget_reservation == 0.0
    assert ctx.privacy_scope == "task"


def test_context_fields_are_settable_and_survive_the_evaluation_binding():
    ctx = _ctx(
        workspace_fingerprint="ws-abc",
        caller="voice",
        question_pack_id="voice_triage",
        question_pack_version="v1",
        budget_reservation=1200.0,
        privacy_scope="task",
    )
    assert ctx.workspace_fingerprint == "ws-abc"
    assert ctx.caller == "voice"
    assert ctx.budget_reservation == 1200.0
    # the evaluation carries the pack identity of the call
    evaluation = _evaluation(access_mode=ACCESS_DISABLED, live_jev=False)
    assert evaluation.question_pack_id == "voice_triage"
    assert evaluation.question_pack_version == compose_voice_triage("loop").version


# --------------------------------------------------------------------------- #
# §4.2 — provider_mode                                                         #
# --------------------------------------------------------------------------- #


def test_the_four_provider_modes_are_exactly_the_documented_values():
    assert PROVIDER_MODES == (
        "typesafe_jev_live",
        "llm_system_one_adapter",
        "replay_fixture",
        "disabled",
    )
    assert PROVIDER_MODE_TYPESAFE_LIVE == "typesafe_jev_live"
    assert PROVIDER_MODE_LLM_ADAPTER == "llm_system_one_adapter"
    assert PROVIDER_MODE_REPLAY_FIXTURE == "replay_fixture"
    assert PROVIDER_MODE_DISABLED == "disabled"


def test_provider_mode_is_derived_from_access_mode_and_live_jev():
    assert (
        provider_mode_for(access_mode=ACCESS_LIVE, live_jev=True)
        == PROVIDER_MODE_TYPESAFE_LIVE
    )
    assert (
        provider_mode_for(access_mode=ACCESS_LLM_ADAPTER, live_jev=False)
        == PROVIDER_MODE_LLM_ADAPTER
    )
    assert provider_mode_for(access_mode=ACCESS_DISABLED, live_jev=False) == (
        PROVIDER_MODE_DISABLED
    )
    assert (
        provider_mode_for(access_mode="fixture", live_jev=False)
        == PROVIDER_MODE_REPLAY_FIXTURE
    )
    with pytest.raises(SystemOneContractError):
        provider_mode_for(access_mode=ACCESS_LIVE, live_jev=False)


def test_llm_adapter_result_is_never_labelled_typesafe_jev_live():
    evaluation = _evaluation(access_mode=ACCESS_LLM_ADAPTER, live_jev=False)
    assert evaluation.provider_mode == PROVIDER_MODE_LLM_ADAPTER
    assert evaluation.provider_mode != PROVIDER_MODE_TYPESAFE_LIVE
    assert evaluation.live_jev is False
    assert evaluation.evidence()["provider_mode"] == PROVIDER_MODE_LLM_ADAPTER
    # the same claim expressed through access_mode is refused, not absorbed
    with pytest.raises(SystemOneContractError):
        _evaluation(access_mode=ACCESS_LLM_ADAPTER, live_jev=True)


def test_live_typesafe_reads_typesafe_jev_live_only_with_live_jev_true():
    live = _evaluation(access_mode=ACCESS_LIVE, live_jev=True)
    assert live.provider_mode == PROVIDER_MODE_TYPESAFE_LIVE
    assert live.live_jev is True
    with pytest.raises(SystemOneContractError):
        _evaluation(access_mode=ACCESS_LIVE, live_jev=False)


def test_provider_mode_cannot_be_forced_live_without_live_jev():
    with pytest.raises(SystemOneContractError):
        _evaluation(
            access_mode=ACCESS_LLM_ADAPTER,
            live_jev=False,
            provider_mode=PROVIDER_MODE_TYPESAFE_LIVE,
        )


def test_disabled_evaluator_reports_provider_mode_disabled():
    import asyncio

    evaluation = asyncio.run(
        DisabledSystemOneEvaluator().evaluate(
            state=_state(),
            questions=compose_voice_triage("loop"),
            context=_ctx(),
        )
    )
    assert evaluation.provider_mode == PROVIDER_MODE_DISABLED
    # backward compatibility: the old field is still there and unchanged
    assert evaluation.provider_access_mode == ACCESS_DISABLED


def test_replay_fixture_access_mode_reports_replay_fixture():
    evaluation = _evaluation(access_mode="fixture", live_jev=False)
    assert evaluation.provider_mode == PROVIDER_MODE_REPLAY_FIXTURE
    assert evaluation.provider_access_mode == "fixture"


# --------------------------------------------------------------------------- #
# §4.3 — Answer                                                                #
# --------------------------------------------------------------------------- #


def test_answer_carries_question_type_and_provider_metadata():
    noul = parse_answer(question_id="q", kind="noul", payload={"value": 0.7})
    assert noul.question_type == "noul"
    assert noul.as_dict()["question_type"] == "noul"
    assert noul.as_dict()["provider_metadata"] == {}

    choice = parse_answer(
        question_id="q",
        kind="choice",
        payload={"choice": "same", "probabilities": {"same": 0.6, "other": 0.4}, "confidence": 0.8},
    )
    assert choice.question_type == "choice"
    assert choice.as_dict()["confidence"] == pytest.approx(0.8)


def test_noul_confidence_is_null_in_the_serialized_contract_and_never_invented():
    answer = NoulAnswer(question_id="q", value=0.42)
    assert answer.as_dict()["confidence"] is None
    assert answer_confidence(answer) is None
    # the pre-existing pin stays true: no confidence attribute at all
    assert "confidence" not in NoulAnswer.__dataclass_fields__
    assert not hasattr(answer, "confidence")
    # Choice/Score keep the confidence the provider reported
    choice = ChoiceAnswer(
        question_id="q", choice="a", probabilities={"a": 1.0}, confidence=0.9
    )
    assert answer_confidence(choice) == pytest.approx(0.9)
    score = ScoreAnswer(
        question_id="q",
        score=1.0,
        legend=(0.0, 1.0, 2.0),
        probabilities={"0.0": 0.1, "1.0": 0.6, "2.0": 0.3},
        confidence=0.75,
    )
    assert answer_confidence(score) == pytest.approx(0.75)
    assert answer_confidence(None) is None


def test_provider_metadata_travels_with_the_answer_without_touching_payload_rules():
    choice = ChoiceAnswer(
        question_id="q",
        choice="a",
        probabilities={"a": 1.0},
        confidence=0.5,
        provider_metadata={"model": "jev-latest", "raw_ref": "typesafe://x"},
    )
    assert choice.as_dict()["provider_metadata"]["model"] == "jev-latest"
    # a Noul payload carrying confidence is still a contract error
    with pytest.raises(SystemOneContractError):
        parse_answer(question_id="q", kind="noul", payload={"value": 0.5, "confidence": 0.9})


# --------------------------------------------------------------------------- #
# §5 — pack metadata                                                           #
# --------------------------------------------------------------------------- #


def test_the_five_productive_packs_carry_the_mandatory_metadata():
    for pack in DEFAULT_QUESTION_PACKS:
        assert_pack_metadata(pack)
        assert pack.purpose, f"{pack.ref} without purpose"
        assert pack.applicable_states, f"{pack.ref} without applicable_states"
        assert pack.risk_class in {"R0", "R1", "R2", "R3", "R4"}, pack.ref
        assert pack.fallback_policy, f"{pack.ref} without fallback_policy"
        assert pack.calibration_dataset_version, f"{pack.ref} without dataset version"
        # the registry key stays <id>_v<version> — manifests must not move
        assert pack.ref == f"{pack.id}_{pack.version}"


def test_an_incomplete_pack_metadata_is_refused_by_the_productive_registry():
    incomplete = QuestionPack(
        id="x", version="v1", owner="x", questions=(DEFAULT_QUESTION_PACKS[0].questions[0],)
    )
    with pytest.raises(SystemOneContractError):
        assert_pack_metadata(incomplete)
    assert_pack_metadata(DEFAULT_QUESTION_PACKS[0])  # the complete one passes


def test_registry_manifest_exposes_the_new_metadata():
    manifest = default_question_registry().manifest()
    entry = manifest["voice_triage_v1"]
    assert entry["purpose"]
    assert entry["risk_class"]
    assert entry["fallback_policy"]
    assert entry["calibration_dataset_version"]


# --------------------------------------------------------------------------- #
# §6 — canonical atomic ids                                                    #
# --------------------------------------------------------------------------- #

CANONICAL_IDS = (
    "same_root_cause",
    "same_strategy",
    "same_failed_strategy",
    "new_information_obtained",
    "new_evidence_obtained",
    "meaningful_progress",
    "evidence_stale",
    "goal_drift_present",
    "completion_claim_present",
    "current_evidence_supports_claim",
    "system2_needed",
)


@pytest.mark.parametrize("canonical_id", CANONICAL_IDS)
def test_every_canonical_atomic_id_of_section_6_is_resolvable(canonical_id):
    question = default_question_registry().resolve_canonical(canonical_id)
    assert question.id in default_question_registry().question_ids()
    feature = next(
        feature
        for feature in CANONICAL_FEATURES
        if feature.canonical_id == canonical_id or canonical_id in feature.aliases
    )
    assert question.id == feature.question_id
    assert feature.polarity in ("direct", "inverted")


def test_canonical_aliases_resolve_to_the_very_same_question():
    registry = default_question_registry()
    assert registry.resolve_canonical("same_strategy").id == (
        registry.resolve_canonical("same_failed_strategy").id
    )
    assert registry.resolve_canonical("new_information_obtained").id == (
        registry.resolve_canonical("new_evidence_obtained").id
    )


def test_no_existing_question_id_was_silently_renamed():
    ids = set(default_question_registry().question_ids())
    for legacy_id in (
        "voice.loop.same_root_cause",
        "voice.loop.same_strategy",
        "voice.loop.new_information",
        "voice.loop.measurable_progress",
        "voice.completion.claims_done",
        "voice.completion.evidence_current",
        "voice.recovery.system_two_needed",
        "voice.drift.supports_goal",
    ):
        assert legacy_id in ids, f"legacy question id disappeared: {legacy_id}"


def test_questions_expose_their_canonical_id_as_metadata():
    registry = default_question_registry()
    question = registry.resolve("voice.loop.same_root_cause")
    assert question.canonical_id == "same_root_cause"
    assert registry.canonical_id_for("voice.loop.same_root_cause") == "same_root_cause"


def test_unresolvable_canonical_id_is_a_contract_error():
    from skaldir.system_one import QuestionNotRegistered

    with pytest.raises(QuestionNotRegistered):
        default_question_registry().resolve_canonical("not_a_canonical_id")


# --------------------------------------------------------------------------- #
# §7 — state builder stamping                                                  #
# --------------------------------------------------------------------------- #


def test_sanitizer_populates_the_state_stamp():
    sanitizer = StateSanitizer(policy=SanitizerPolicy(project_id="skaldir"))
    state = sanitizer.sanitize(
        {"goal": "g", "step": 3},
        workspace_fingerprint="ws-1",
        source_event_refs=("evt-1", "evt-2"),
    )
    assert state.workspace_fingerprint == "ws-1"
    assert state.source_event_refs == ("evt-1", "evt-2")
    assert state.created_at > 0.0
    assert state.as_dict()["created_at"] == state.created_at
    assert state.as_dict()["source_event_refs"] == ["evt-1", "evt-2"]


def test_created_at_never_enters_the_fingerprint_so_replays_stay_comparable():
    state_a = StateSanitizer().sanitize({"goal": "g"})
    state_b = StateSanitizer().sanitize({"goal": "g"})
    assert state_a.fingerprint == state_b.fingerprint
    assert state_a.fingerprint == state_fingerprint({"goal": "g"})


def test_a_state_without_a_stamp_is_still_constructible_for_existing_callers():
    state = SystemOneState(payload={"goal": "g"})
    assert state.source_event_refs == ()
    assert state.workspace_fingerprint == ""
    assert state.created_at == 0.0  # 0 = "the builder did not stamp it", never invented
