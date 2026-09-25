"""JEV-17/JEV-16 — the full Computer-Use question pack (§10.3) and the visual
subtool contract (§11), both living in ``godsv.system_one_subtools``.

ROT-FIRST evidence, captured before the module existed:

    $ .venv/bin/python -c "import godsv.system_one_subtools"
    ModuleNotFoundError: No module named 'godsv.system_one_subtools'

    $ .venv/bin/python -m pytest tests/test_jev_subtools.py -p no:randomly
    ERROR tests/test_jev_subtools.py - ModuleNotFoundError:
        No module named 'godsv.system_one_subtools'
    !!! Interrupted: 1 error during collection !!!

What this file pins (behaviour, never source text):

* §10.3: the Computer-Use pack carries the **full** contract — 6 pre-action,
  5 post-action and 5 recovery questions plus one candidate-`Choice` whose
  options are exactly ``candidate_a, candidate_b, keyboard_shortcut, reobserve,
  escalate, abort`` and which may **only** offer options that are already
  allowed: a narrowed per-step pack offers the allowed subset, and a provider
  answer naming an option that was not allowed is refused (never downgraded to
  a click);
* the pack is registered through the repo's existing :class:`QuestionRegistry`
  (no second registry) and keeps the one legacy id the Voice route policy reads
  (``computer_use.recovery_needed``), so replacing v1 cannot silently strip it;
* §11: a :class:`VisualSubtool` reports all 16 contract fields, with the 15
  goal-named fields plus §55's third optional ``system_one_recovery_questions``;
* §11: an inapplicable app version or locale is refused (no guessing), and
  **blind macro replay is refused** — a stored sequence may not be replayed when
  its start conditions/anchors are not currently observed (§50/§56);
* the six Jev checks (start condition valid, anchor reached, expected transition
  happened, procedure still applicable, new observation needed, System-2
  takeover) drive one decision: proceed / reobserve / escalate — never a
  proceeding decision when an anchor is missing.

Evidence class: contract + mock (test doubles for the decision inputs). These tests do not
need a GUI session: the observed anchors are passed in as data, so nothing here is a real
app effect (``skaldir act check --json`` reports what this host can really drive).
"""

from __future__ import annotations

import pytest

from skaldir.system_one_questions import Question, QuestionPack, QuestionRegistry
from skaldir.system_one_subtools import (
    CANDIDATE_CHOICE_OPTIONS,
    COMPUTER_USE_PACK_ID,
    COMPUTER_USE_PACK_VERSION,
    LEGACY_COMPUTER_USE_QUESTION_IDS,
    POST_ACTION_QUESTIONS,
    PRE_ACTION_QUESTIONS,
    RECOVERY_QUESTIONS,
    VISUAL_SUBTOOL_FIELDS,
    BlindReplayRefused,
    CandidateChoiceViolation,
    SubtoolContractError,
    SubtoolNotRegistered,
    SubtoolRegistry,
    SubtoolRevoked,
    VisualSubtool,
    allowed_candidate_options,
    check_applicability,
    check_blind_replay,
    compose_computer_use_questions,
    computer_use_pack,
    evaluate_subtool_step,
    register_computer_use_pack,
    validate_candidate_choice,
)

#: The invented macro of §11: a stored, ordered sequence with anchors. Not
#: observed anywhere — this is declared data, not a live desktop reading.
RENDER_MACRO = VisualSubtool(
    capability_id="blender.render_scene",
    version="v1",
    supported_app_versions=("4.1.*", "4.2.0"),
    supported_locales=("en_US", "de_DE"),
    start_conditions=("app_running", "document_open"),
    anchors=("Render", "Output Properties"),
    allowed_actions=("invoke_render", "confirm_render", "reobserve", "escalate_system2"),
    expected_transitions=("render_window_opened", "render_finished"),
    completion_conditions=("output_file_present",),
    failure_conditions=("blocking_dialog_present", "anchor_missing"),
    reobserve_policy=("anchor_mismatch", "transition_mismatch", "action_failed"),
    system2_escalation_policy=("app_version_outside_subtool_applicability", "new_unknown_ui"),
    verification_contract="each attempt needs an expectation; the effect is read from perception",
    system_one_prechecks=PRE_ACTION_QUESTIONS,
    system_one_postchecks=POST_ACTION_QUESTIONS,
    system_one_recovery_questions=RECOVERY_QUESTIONS,
)


# --------------------------------------------------------------------------- #
# §10.3 — the full Computer-Use question pack                                   #
# --------------------------------------------------------------------------- #


def test_pack_carries_the_full_goal_contract():
    pack = computer_use_pack()
    assert pack.id == COMPUTER_USE_PACK_ID
    assert pack.version == COMPUTER_USE_PACK_VERSION
    ids = set(pack.ids())
    for group in (PRE_ACTION_QUESTIONS, POST_ACTION_QUESTIONS, RECOVERY_QUESTIONS):
        for name in group:
            assert any(question.endswith(f".{name}") for question in ids), name
    assert len(PRE_ACTION_QUESTIONS) == 6
    assert len(POST_ACTION_QUESTIONS) == 5
    assert len(RECOVERY_QUESTIONS) == 5
    assert len(set(pack.ids())) == len(pack.questions), "no duplicate question id"


def test_pack_questions_stay_atomic():
    for question in computer_use_pack().questions:
        assert question.text.endswith("?")
        assert question.text.count("?") == 1
        assert len(question.text) <= 160


def test_pack_keeps_the_legacy_id_the_voice_route_policy_reads():
    """Replacing the v1 pack must not silently strip Worker A's route input."""
    assert LEGACY_COMPUTER_USE_QUESTION_IDS == ("computer_use.recovery_needed",)
    ids = set(computer_use_pack().ids())
    for legacy in LEGACY_COMPUTER_USE_QUESTION_IDS:
        assert legacy in ids


def test_pack_registers_into_the_existing_question_registry():
    registry = QuestionRegistry()
    pack = register_computer_use_pack(registry)
    assert registry.pack("computer_use") is pack
    with pytest.raises(SubtoolContractError):
        register_computer_use_pack(registry)  # a second registration is refused


def test_candidate_choice_offers_exactly_the_documented_options():
    assert CANDIDATE_CHOICE_OPTIONS == (
        "candidate_a",
        "candidate_b",
        "keyboard_shortcut",
        "reobserve",
        "escalate",
        "abort",
    )
    choice = _choice_question(computer_use_pack())
    assert tuple(choice.options) == CANDIDATE_CHOICE_OPTIONS


def test_a_narrowed_pack_only_offers_already_allowed_options():
    """The rule, in code: a Choice may contain only already-allowed options."""
    allowed = ("candidate_a", "reobserve", "abort")
    narrowed = compose_computer_use_questions(allowed_candidates=allowed)
    choice = _choice_question(_as_pack(narrowed))
    assert tuple(choice.options) == allowed
    assert set(choice.options) <= set(CANDIDATE_CHOICE_OPTIONS)
    assert "keyboard_shortcut" not in choice.options


def test_allowed_candidate_options_never_adds_one():
    assert allowed_candidate_options(("candidate_b",)) == ("candidate_b",)
    assert allowed_candidate_options(("candidate_a", "reobserve")) == (
        "candidate_a",
        "reobserve",
    )
    assert allowed_candidate_options(()) == ()
    # an option that is not part of the documented set is never offered
    assert allowed_candidate_options(("click_at_1200_800",)) == ()


def test_a_choice_outside_the_allowed_set_is_refused():
    allowed = ("candidate_a", "reobserve")
    assert validate_candidate_choice("candidate_a", allowed_candidates=allowed) == "candidate_a"
    with pytest.raises(CandidateChoiceViolation):
        validate_candidate_choice("candidate_b", allowed_candidates=allowed)
    with pytest.raises(CandidateChoiceViolation):
        validate_candidate_choice("click_at_1200_800", allowed_candidates=allowed)


def test_an_empty_allowed_set_offers_nothing_to_choose():
    assert allowed_candidate_options(()) == ()
    with pytest.raises(CandidateChoiceViolation):
        validate_candidate_choice("abort", allowed_candidates=())


# --------------------------------------------------------------------------- #
# §11 — the subtool contract                                                   #
# --------------------------------------------------------------------------- #


def test_every_contract_field_is_reported():
    """The goal §11 list (15) plus §55's third optional = the 16-field contract."""
    assert len(VISUAL_SUBTOOL_FIELDS) == 16
    for name in (
        "capability_id",
        "version",
        "supported_app_versions",
        "supported_locales",
        "start_conditions",
        "anchors",
        "allowed_actions",
        "expected_transitions",
        "completion_conditions",
        "failure_conditions",
        "reobserve_policy",
        "system2_escalation_policy",
        "verification_contract",
        "system_one_prechecks",
        "system_one_postchecks",
    ):
        assert name in VISUAL_SUBTOOL_FIELDS
    assert "system_one_recovery_questions" in VISUAL_SUBTOOL_FIELDS
    payload = RENDER_MACRO.as_dict()
    missing = [name for name in VISUAL_SUBTOOL_FIELDS if name not in payload]
    assert missing == []
    # the pre/post check lists are real question names, not free text
    assert set(RENDER_MACRO.system_one_prechecks) == set(PRE_ACTION_QUESTIONS)
    assert set(RENDER_MACRO.system_one_postchecks) == set(POST_ACTION_QUESTIONS)


def test_a_subtool_without_its_core_fields_is_refused():
    with pytest.raises(SubtoolContractError):
        VisualSubtool(
            capability_id="",
            version="v1",
            supported_app_versions=("4.1",),
            supported_locales=("en_US",),
            start_conditions=("app_running",),
            anchors=("Render",),
            allowed_actions=("invoke_render",),
            expected_transitions=("render_window_opened",),
            completion_conditions=("output_file_present",),
            failure_conditions=(),
            reobserve_policy=("anchor_mismatch",),
            system2_escalation_policy=("new_unknown_ui",),
            verification_contract="expectation required",
        )
    with pytest.raises(SubtoolContractError):
        VisualSubtool(
            capability_id="x",
            version="v1",
            supported_app_versions=(),
            supported_locales=("en_US",),
            start_conditions=("app_running",),
            anchors=("Render",),
            allowed_actions=("invoke_render",),
            expected_transitions=("render_window_opened",),
            completion_conditions=("output_file_present",),
            failure_conditions=(),
            reobserve_policy=("anchor_mismatch",),
            system2_escalation_policy=("new_unknown_ui",),
            verification_contract="expectation required",
        )


def test_registry_resolves_versions_and_refuses_duplicates_and_revocation():
    registry = SubtoolRegistry()
    registry.register(RENDER_MACRO)
    assert registry.resolve("blender.render_scene") is RENDER_MACRO
    assert registry.resolve("blender.render_scene", version="v1") is RENDER_MACRO
    with pytest.raises(SubtoolContractError):
        registry.register(RENDER_MACRO)
    with pytest.raises(SubtoolNotRegistered):
        registry.resolve("blender.render_scene", version="v2")
    with pytest.raises(SubtoolRevoked):
        registry.revoke("blender.render_scene", version="v1")
        registry.resolve("blender.render_scene")


# --------------------------------------------------------------------------- #
# §11 — applicability (no guessing) and blind-replay refusal                    #
# --------------------------------------------------------------------------- #


def test_an_inapplicable_app_version_or_locale_is_refused():
    ok = check_applicability(RENDER_MACRO, app_version="4.1.7", locale="en_US")
    assert ok.applicable is True
    exact = check_applicability(RENDER_MACRO, app_version="4.2.0", locale="de_DE")
    assert exact.applicable is True
    wrong_version = check_applicability(RENDER_MACRO, app_version="5.0.1", locale="en_US")
    assert wrong_version.applicable is False
    assert "4.1.*" in wrong_version.reason or "version" in wrong_version.reason.lower()
    wrong_locale = check_applicability(RENDER_MACRO, app_version="4.1.7", locale="fr_FR")
    assert wrong_locale.applicable is False
    assert "locale" in wrong_locale.reason.lower()
    unknown_version = check_applicability(RENDER_MACRO, app_version="", locale="en_US")
    assert unknown_version.applicable is False, "an unknown version is never assumed applicable"


def test_blind_macro_replay_is_refused_when_start_conditions_or_anchors_are_missing():
    """§56: 'Blindes Weiterklicken nach verlorenem Anchor ist FAIL'."""
    missing_anchor = check_blind_replay(
        RENDER_MACRO,
        observed_start_conditions=("app_running", "document_open"),
        observed_anchors=("Output Properties",),
    )
    assert missing_anchor.replay_allowed is False
    assert missing_anchor.missing_anchors == ("Render",)
    assert missing_anchor.missing_start_conditions == ()

    missing_condition = check_blind_replay(
        RENDER_MACRO,
        observed_start_conditions=("app_running",),
        observed_anchors=("Render", "Output Properties"),
    )
    assert missing_condition.replay_allowed is False
    assert missing_condition.missing_start_conditions == ("document_open",)

    ready = check_blind_replay(
        RENDER_MACRO,
        observed_start_conditions=("app_running", "document_open"),
        observed_anchors=("Render", "Output Properties"),
    )
    assert ready.replay_allowed is True
    assert ready.missing_anchors == () and ready.missing_start_conditions == ()

    with pytest.raises(BlindReplayRefused):
        _raise_blind_replay(missing_anchor)


def _raise_blind_replay(decision) -> None:
    from skaldir.system_one_subtools import assert_no_blind_replay

    assert_no_blind_replay(decision)


# --------------------------------------------------------------------------- #
# §11 — the six Jev checks and their decision                                   #
# --------------------------------------------------------------------------- #


def test_a_fully_observed_step_proceeds():
    decision = evaluate_subtool_step(
        RENDER_MACRO,
        app_version="4.1.7",
        locale="en_US",
        observed_start_conditions=("app_running", "document_open"),
        observed_anchors=("Render", "Output Properties"),
        observed_transitions=(),
        performed_actions=(),
    )
    assert decision.proceed is True
    assert decision.action == "invoke_render"
    assert decision.reobserve is False
    assert decision.escalate is False
    assert decision.checks["start_condition_valid"] is True
    assert decision.checks["anchor_reached"] is True
    assert decision.checks["procedure_still_applicable"] is True


def test_a_lost_anchor_reobserves_and_never_proceeds():
    decision = evaluate_subtool_step(
        RENDER_MACRO,
        app_version="4.1.7",
        locale="en_US",
        observed_start_conditions=("app_running", "document_open"),
        observed_anchors=(),  # anchor lost — §56 FAIL case
        observed_transitions=(),
        performed_actions=(),
    )
    assert decision.proceed is False
    assert decision.action == ""
    assert decision.reobserve is True
    assert decision.checks["anchor_reached"] is False
    assert decision.checks["new_observation_needed"] is True


def test_an_inapplicable_version_escalates_instead_of_acting():
    decision = evaluate_subtool_step(
        RENDER_MACRO,
        app_version="5.0.1",
        locale="en_US",
        observed_start_conditions=("app_running", "document_open"),
        observed_anchors=("Render", "Output Properties"),
        observed_transitions=(),
        performed_actions=(),
    )
    assert decision.proceed is False
    assert decision.escalate is True
    assert decision.checks["procedure_still_applicable"] is False
    assert "app_version_outside_subtool_applicability" in decision.reasons


def test_a_missing_expected_transition_reobserves_or_escalates():
    reobserve = evaluate_subtool_step(
        RENDER_MACRO,
        app_version="4.1.7",
        locale="en_US",
        observed_start_conditions=("app_running", "document_open"),
        observed_anchors=("Render", "Output Properties"),
        observed_transitions=(),
        performed_actions=("invoke_render",),
    )
    assert reobserve.proceed is False
    assert reobserve.checks["expected_transition_happened"] is False
    assert reobserve.reobserve is True
    assert reobserve.action == ""

    no_reobserve_policy = _macro_without_reobserve()
    escalate = evaluate_subtool_step(
        no_reobserve_policy,
        app_version="4.1.7",
        locale="en_US",
        observed_start_conditions=("app_running", "document_open"),
        observed_anchors=("Render", "Output Properties"),
        observed_transitions=(),
        performed_actions=("invoke_render",),
    )
    assert escalate.proceed is False
    assert escalate.escalate is True
    assert escalate.checks["system2_takeover"] is True
    assert "expected_transition_missing" in escalate.reasons
    # the subtool's own declared §12 trigger is surfaced, not invented
    assert "insufficient_state" in escalate.reasons


def test_an_observed_transition_proceeds_to_the_next_action():
    decision = evaluate_subtool_step(
        RENDER_MACRO,
        app_version="4.1.7",
        locale="en_US",
        observed_start_conditions=("app_running", "document_open"),
        observed_anchors=("Render", "Output Properties"),
        observed_transitions=("render_window_opened",),
        performed_actions=("invoke_render",),
    )
    assert decision.proceed is True
    assert decision.action == "confirm_render"


def test_a_system2_request_wins_over_proceeding():
    decision = evaluate_subtool_step(
        RENDER_MACRO,
        app_version="4.1.7",
        locale="en_US",
        observed_start_conditions=("app_running", "document_open"),
        observed_anchors=("Render", "Output Properties"),
        observed_transitions=("render_window_opened",),
        performed_actions=("invoke_render",),
        system2_requested=True,
    )
    assert decision.proceed is False
    assert decision.escalate is True
    assert decision.checks["system2_takeover"] is True


def test_completion_conditions_end_the_subtool():
    decision = evaluate_subtool_step(
        RENDER_MACRO,
        app_version="4.1.7",
        locale="en_US",
        observed_start_conditions=("app_running", "document_open"),
        observed_anchors=("Render", "Output Properties"),
        observed_transitions=("render_finished",),
        performed_actions=("invoke_render", "confirm_render", "reobserve", "escalate_system2"),
        observed_completion_conditions=("output_file_present",),
    )
    assert decision.complete is True
    assert decision.proceed is False
    assert decision.action == ""


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


def _choice_question(pack: QuestionPack) -> Question:
    choices = [question for question in pack.questions if question.kind == "choice"]
    assert choices, "the Computer-Use pack needs one candidate Choice question"
    return choices[0]


def _as_pack(question_set) -> QuestionPack:
    """A composed set as a pack, so the same choice assertion applies."""
    return QuestionPack(
        id=question_set.id,
        version=question_set.version,
        owner=question_set.owner,
        questions=tuple(question_set.questions),
    )


def _macro_without_reobserve() -> VisualSubtool:
    return VisualSubtool(
        capability_id=RENDER_MACRO.capability_id,
        version="v2",
        supported_app_versions=RENDER_MACRO.supported_app_versions,
        supported_locales=RENDER_MACRO.supported_locales,
        start_conditions=RENDER_MACRO.start_conditions,
        anchors=RENDER_MACRO.anchors,
        allowed_actions=RENDER_MACRO.allowed_actions,
        expected_transitions=RENDER_MACRO.expected_transitions,
        completion_conditions=RENDER_MACRO.completion_conditions,
        failure_conditions=RENDER_MACRO.failure_conditions,
        reobserve_policy=(),  # no reobserve allowed → escalate instead
        system2_escalation_policy=("insufficient_state",),
        verification_contract=RENDER_MACRO.verification_contract,
    )
