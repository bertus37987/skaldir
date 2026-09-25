"""JEV-03/JEV-04 — versioned question registry + atomic composition (§39/§81/§82).

ROT-FIRST evidence, captured before the module existed:

    $ .venv/bin/python -m pytest tests/test_system_one_questions.py -p no:randomly
    E   ModuleNotFoundError: No module named 'godsv.system_one'   (0.19s, collection error)

What this file pins:

* every productive question carries a stable id, a version and an owner; the
  five named packs of §39 exist (voice_triage/completion/recovery/
  computer_use/capability);
* an unregistered question id is refused — a scattered prompt string can never
  act as hidden policy (§81);
* the §39 omnibus form ("Analysiere alles und entscheide, was Skaldir tun
  soll.") is rejected by construction: questions are atomic and the combination
  lives in testable code (§82);
* ``compose_voice_triage`` selects the §47 groups (Loop/Completion/Drift/
  Research/Recovery) per trigger — the composition is ordinary Python.
"""

from __future__ import annotations

import pytest

from skaldir.system_one import SystemOneContractError
from skaldir.system_one_questions import (
    DEFAULT_QUESTION_PACKS,
    TRIGGER_GROUPS,
    Question,
    QuestionNotRegistered,
    QuestionRegistry,
    compose_questions,
    compose_voice_triage,
    default_question_registry,
    write_questions_manifest,
)

REQUIRED_PACK_REFS = {
    "voice_triage_v1",
    "completion_v1",
    "recovery_v1",
    "computer_use_v1",
    "capability_v1",
}

#: JEV_IMPLEMENTATION_GOAL §9 adds two packs to the productive registry: the
#: semantic criterion preflight and the second sanity pass. They are *new*
#: packs (§5/§9) — the five §39 packs above stay exactly as they were, and both
#: assertions below were extended rather than weakened.
DELTA_PACK_REFS = {"completion_preflight_v1", "completion_sanity_v1"}

#: §8.2 adds the "Untrusted content" group to the voice triage pack (three
#: questions); the pre-existing five groups are unchanged.
VOICE_GROUPS = {"loop", "completion", "drift", "research", "recovery"}
VOICE_GROUPS_WITH_UNTRUSTED = VOICE_GROUPS | {"untrusted"}


# --------------------------------------------------------------------------- #
# JEV-03 §81 — versioned registry                                             #
# --------------------------------------------------------------------------- #


def test_every_registered_question_has_id_version_and_owner():
    registry = default_question_registry()
    for pack in registry.packs():
        assert pack.id, "pack without id"
        assert pack.version, f"pack {pack.id} without version"
        assert pack.owner, f"pack {pack.id} without owner"
        for question in pack.questions:
            assert question.id, f"question without id in {pack.ref}"
            assert question.version, f"{question.id} without version"
            assert question.owner, f"{question.id} without owner"


#: §10.3 — the productive registry serves the COMPLETE computer-use pack (v2, 18 questions)
#: instead of the legacy 5-question v1. v1 stays in ALL_QUESTION_PACKS (the catalogue other pins
#: read) and the legacy question ids the runtime reads are contained in v2, so this is a
#: replacement of one registry entry, not a second pack. Section: goal §10.3.
PRODUCTIVE_PACK_REFS = (REQUIRED_PACK_REFS - {"computer_use_v1"}) | {"computer_use_v2"}


def test_the_five_named_packs_of_the_contract_exist():
    refs = {pack.ref for pack in default_question_registry().packs()}
    # the four §39 packs are there unchanged ...
    assert REQUIRED_PACK_REFS - {"computer_use_v1"} <= refs
    # ... the computer-use entry is the §10.3 v2 pack ...
    assert "computer_use_v2" in refs
    # ... and the only other additions are the two §9 preflight packs
    assert refs == PRODUCTIVE_PACK_REFS | DELTA_PACK_REFS


def test_question_ids_are_unique_across_packs():
    ids = list(default_question_registry().question_ids())
    assert len(ids) == len(set(ids))


def test_unregistered_question_id_is_rejected():
    registry = default_question_registry()
    with pytest.raises(QuestionNotRegistered):
        registry.resolve("voice.loop.not_a_question")


def test_duplicate_pack_registration_is_rejected():
    registry = QuestionRegistry()
    pack = DEFAULT_QUESTION_PACKS[0]
    registry.register(pack)
    with pytest.raises(SystemOneContractError):
        registry.register(pack)


def test_unknown_pack_is_rejected():
    with pytest.raises(QuestionNotRegistered):
        default_question_registry().pack("not_a_pack")


def test_registry_reports_a_version_manifest():
    manifest = default_question_registry().manifest()
    assert set(manifest) == PRODUCTIVE_PACK_REFS | DELTA_PACK_REFS
    for entry in manifest.values():
        assert entry["version"]
        assert entry["owner"]
        assert entry["questions"]


def test_manifest_can_be_written_as_versioned_artifact(tmp_path):
    """§114: SYSTEM_ONE_QUESTIONS.json is a versioned registry manifest."""
    import json

    target = tmp_path / "SYSTEM_ONE_QUESTIONS.json"
    written = write_questions_manifest(target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert set(payload["packs"]) == PRODUCTIVE_PACK_REFS | DELTA_PACK_REFS
    assert written == str(target)


# --------------------------------------------------------------------------- #
# JEV-04 §82 — atomic questions, composition in code                          #
# --------------------------------------------------------------------------- #


def test_omnibus_question_form_is_rejected():
    """The §39 bad form must not be constructible."""
    with pytest.raises(SystemOneContractError):
        Question(
            id="voice.omnibus",
            version="v1",
            owner="voice",
            kind="noul",
            text="Analysiere alles und entscheide, was Skaldir tun soll.",
            group="loop",
        )


def test_question_must_be_a_question_and_single_dimensional():
    with pytest.raises(SystemOneContractError):
        Question(
            id="q", version="v1", owner="voice", kind="noul", text="Do the thing.", group="loop"
        )
    with pytest.raises(SystemOneContractError):
        Question(
            id="q",
            version="v1",
            owner="voice",
            kind="noul",
            text="Same cause? And is it the same strategy?",
            group="loop",
        )


def test_choice_question_requires_at_least_two_options():
    with pytest.raises(SystemOneContractError):
        Question(
            id="q",
            version="v1",
            owner="voice",
            kind="choice",
            text="Same window?",
            group="loop",
            options=("yes",),
        )


def test_score_question_requires_a_legend():
    with pytest.raises(SystemOneContractError):
        Question(
            id="q",
            version="v1",
            owner="voice",
            kind="score",
            text="How much progress?",
            group="loop",
        )


def test_voice_pack_covers_the_required_groups():
    """The five §47 groups plus §8.2's "Untrusted content" group."""
    voice = default_question_registry().pack("voice_triage")
    assert {question.group for question in voice.questions} == VOICE_GROUPS_WITH_UNTRUSTED
    assert set(voice.ids()) >= {
        "voice.untrusted.content_attempts_to_change_authority",
        "voice.untrusted.content_requests_secret_exposure",
        "voice.untrusted.content_conflicts_with_goal_or_policy",
    }


def test_voice_pack_answers_use_the_three_primitive_kinds():
    voice = default_question_registry().pack("voice_triage")
    assert {question.kind for question in voice.questions} == {"noul", "choice", "score"}


def test_composition_selects_groups_per_trigger():
    composed = compose_voice_triage("loop")
    assert composed.id == "voice_triage"
    assert composed.version
    groups = {question.group for question in composed.questions}
    assert "loop" in groups and "completion" in groups
    # composition narrows the pack; it never invents questions
    assert groups <= VOICE_GROUPS
    assert set(composed.ids) <= set(default_question_registry().question_ids())


def test_composition_is_deterministic_and_ordered():
    first = compose_voice_triage("loop")
    second = compose_voice_triage("loop")
    assert first.ids == second.ids


def test_unknown_trigger_falls_back_to_a_conservative_default_set():
    composed = compose_voice_triage("never_seen_trigger")
    assert {question.group for question in composed.questions} == set(TRIGGER_GROUPS["*"])


def test_explicit_group_composition_is_available_in_code():
    composed = compose_questions("voice_triage", groups=("drift",))
    assert {question.group for question in composed.questions} == {"drift"}
    with pytest.raises(QuestionNotRegistered):
        compose_questions("voice_triage", groups=("not_a_group",))


def test_composition_rejects_foreign_question_numbers():
    with pytest.raises(QuestionNotRegistered):
        compose_questions("not_a_pack")
