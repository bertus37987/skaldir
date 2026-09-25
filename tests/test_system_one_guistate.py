"""JEV-15 — GUIState contract: structured, versioned states from current perception.

ROT-FIRST evidence, captured before the module existed:

    $ .venv/bin/python -c "import godsv.system_one_guistate"
    ModuleNotFoundError: No module named 'godsv.system_one_guistate'

    $ .venv/bin/python -m pytest tests/test_completion_precheck.py \
        tests/test_system_one_guistate.py -p no:randomly
    E   ImportError: cannot import name 'guistate_action_candidates' from 'godsv.computer_use'
        (/home/willy/projects/gods-voice/src/godsv/computer_use.py)
    !!! Interrupted: 2 errors during collection !!!
    2 errors in 0.12s

What this file pins (ABNAHME §52/§53/§54/§93/JEV-15, behaviour — never source text):

* a GUIState is built from ONE current perception (a channel + its text), carries
  a schema version, a content fingerprint and a revision chain (``previous`` →
  ``revision``) — never from a remembered/plausible state;
* it is honest about what it could NOT perceive: every canonical §52 field is
  reported, and an unperceived field carries ``value: null`` and its name in
  ``unperceived`` — no plausible default, and a value on an unperceived field is
  a contract error;
* a GUIState never widens the pre-approved action set: candidate actions are
  validated against the pre-approved set (and an unknown candidate is refused),
  ``allowed_kinds`` may only shrink along the revision chain, and the
  Computer-Use binding hands back the UNCHANGED ``allowed_kinds``;
* ground truth stays the real app effect: ``ground_truth`` is pinned to
  ``app_effect``, ``certifies_effect`` is always False, and ``effect_check``
  reads the *post-action* perception — an observed change is still not a
  certificate;
* with the §98 flags off the binding returns ``None`` and the existing
  Computer-Use path runs unchanged (§50: allowed kinds stay state-based
  authority).

Evidence class: contract (schema/parser proof) — plus a test-double evaluation
for the advisory binding (mock). NO live GUI session exists here: the perception
text in these tests is fabricated input, not an observed desktop, and no test
claims a real app effect.

ROT-FIRST evidence for the §10.2 extension (full field list + contract view),
captured before it existed:

    $ .venv/bin/python -m pytest tests/test_system_one_guistate.py -p no:randomly
    E   ImportError: cannot import name 'GUI_STATE_CONTRACT_FIELDS' from
        'godsv.system_one_guistate'
        (/home/willy/projects/gods-voice/src/godsv/system_one_guistate.py)
    !!! Interrupted: 1 error during collection !!!
    1 error in 0.09s
"""

from __future__ import annotations

import pytest

from skaldir.computer_use import guistate_action_candidates, system_one_guistate_gate
from skaldir.system_one import (
    ACCESS_DISABLED,
    PROVIDER_DISABLED,
    EvaluationContext,
    SystemOneState,
    build_evaluation,
)
from skaldir.system_one_flags import SystemOneFlags
from skaldir.system_one_guistate import (
    CONTRACT_FIELD_PROVENANCE,
    GROUND_TRUTH_APP_EFFECT,
    GUI_STATE_CONTRACT_FIELDS,
    GUI_STATE_FIELDS,
    GUISTATE_SCHEMA_VERSION,
    GUIState,
    GuiStateContractError,
    PerceivedField,
    build_gui_state,
    contract_field_completeness,
    effect_check,
    parse_perception_text,
)
from skaldir.system_one_questions import compose_questions

# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #

#: A §52-shaped perception, exactly as one channel would report it. This is
#: fabricated input for the parser — not an observed desktop.
FULL_PERCEPTION = "\n".join(
    [
        "application: Blender",
        "app_id: org.blender.blender",
        "app_version: 4.1.7",
        "window_id: 0x3e00007",
        "window_title: scene.blend - Blender",
        "focused: true",
        "document: scene.blend",
        "blocking_dialog: none",
        "anchor: Render",
        "anchor: Output Properties",
        "previous_action: invoke_render",
        "expected_transition: render_window_opened",
        "observed_change: render_window_opened",
        "user_takeover_state: none",
        "captured_at: 1737048000.25",
    ]
)

APPROVED = ("invoke_render", "reobserve", "escalate")


def _state(**overrides) -> GUIState:
    base = {
        "perception_text": FULL_PERCEPTION,
        "channel": "accessibility_tree",
        "goal_step": "render_scene",
        "action_candidates": ("invoke_render",),
        "pre_approved_candidates": APPROVED,
        "allowed_kinds": ("mouse", "key"),
    }
    base.update(overrides)
    return build_gui_state(**base)


class _FakeFlags:
    """Duck-typed flags: only the attributes the binding reads (§98 names)."""

    def __init__(self, **overrides) -> None:
        self.enabled = True
        self.provider = "llm_system_one_adapter"
        self.shadow_mode = False
        self.voice_triage = False
        self.completion_precheck = False
        self.computer_use = True
        for key, value in overrides.items():
            setattr(self, key, value)


def _evaluation():
    """A real evaluation record over the computer-use pack (no hand-made stub)."""
    return build_evaluation(
        context=EvaluationContext(run_id="r", goal_id="g", goal_revision=1),
        questions=compose_questions("computer_use"),
        state=SystemOneState(payload={"task": "render_scene"}, input_scope="task"),
        provider=PROVIDER_DISABLED,
        provider_model="disabled",
        access_mode=ACCESS_DISABLED,
    )


# --------------------------------------------------------------------------- #
# structured + versioned, from current perception                              #
# --------------------------------------------------------------------------- #


def test_gui_state_is_structured_versioned_and_content_addressed():
    state = _state()
    assert state.schema_version == GUISTATE_SCHEMA_VERSION
    assert state.revision == 1
    assert state.previous == ""
    payload = state.as_dict()
    assert payload["fields"]["application"]["value"] == "Blender"
    assert payload["fields"]["window_title"]["value"] == "scene.blend - Blender"
    assert payload["fields"]["focused"]["value"] is True
    assert payload["fields"]["blocking_dialog"]["value"] is None
    assert payload["fields"]["visible_anchors"]["value"] == ["Render", "Output Properties"]
    assert payload["fields"]["observed_changes"]["value"] == ["render_window_opened"]
    assert payload["fields"]["previous_action"]["value"] == "invoke_render"
    assert payload["schema_version"] == GUISTATE_SCHEMA_VERSION
    assert state.fingerprint and len(state.fingerprint) == 64
    assert state.gui_state_id.startswith("gui-")
    # the same perception parsed twice yields the same content fingerprint
    assert _state().fingerprint == state.fingerprint
    assert _state().gui_state_id == state.gui_state_id


def test_gui_state_keeps_the_verbatim_perception_it_was_built_from():
    state = _state()
    assert state.channel == "accessibility_tree"
    assert state.perception["accessibility_tree"].startswith("application: Blender")


def test_revision_chain_links_to_the_previous_state():
    first = _state()
    second = _state(
        perception_text=FULL_PERCEPTION.replace("blocking_dialog: none", "blocking_dialog: Save?"),
        previous=first,
    )
    assert second.revision == 2
    assert second.previous == first.gui_state_id
    assert second.fingerprint != first.fingerprint
    assert second.as_dict()["previous"] == first.gui_state_id


def test_perception_text_without_values_marks_fields_unperceived():
    fields = parse_perception_text(
        "application: Blender\nwindow_title: scene.blend - Blender\n", channel="window_metadata"
    )
    assert set(fields) == set(GUI_STATE_FIELDS)
    assert fields["application"].perceived is True
    assert fields["focused"].perceived is False
    assert fields["focused"].value is None


# --------------------------------------------------------------------------- #
# honest about what was NOT perceived                                          #
# --------------------------------------------------------------------------- #


def test_unperceived_fields_are_explicit_and_never_defaulted():
    state = build_gui_state(
        perception_text="application: Blender\nwindow_title: scene.blend - Blender\n",
        channel="window_metadata",
        goal_step="",
    )
    payload = state.as_dict()
    assert payload["fields"]["focused"] == {"perceived": False, "value": None, "channel": ""}
    assert "focused" in payload["unperceived"]
    assert "document" in payload["unperceived"]
    assert "goal_step" in payload["unperceived"], "an unknown step is reported, not guessed"
    # an unfocused-value must never be read as the plausible default 'no dialog'
    assert payload["fields"]["blocking_dialog"] == {
        "perceived": False,
        "value": None,
        "channel": "",
    }


def test_an_unperceived_field_carrying_a_value_is_refused():
    with pytest.raises(GuiStateContractError):
        PerceivedField(name="focused", value=True, perceived=False)
    with pytest.raises(GuiStateContractError):
        PerceivedField(name="focused", value=True, perceived=True)  # needs its channel


def test_a_gui_state_must_report_every_canonical_field():
    fields = parse_perception_text("application: Blender\n", channel="dom")
    del fields["focused"]
    with pytest.raises(GuiStateContractError):
        GUIState(gui_state_id="gui-test", fields=fields, channel="dom")


def test_an_unknown_perception_channel_is_refused():
    with pytest.raises(GuiStateContractError):
        build_gui_state(perception_text="", channel="telepathy")


def test_an_unknown_schema_version_is_refused():
    fields = parse_perception_text("application: Blender\n", channel="dom")
    with pytest.raises(GuiStateContractError):
        GUIState(
            gui_state_id="gui-test",
            fields=fields,
            channel="dom",
            schema_version="guistate/v99",
        )


# --------------------------------------------------------------------------- #
# never widening the pre-approved action set                                   #
# --------------------------------------------------------------------------- #


def test_a_candidate_outside_the_pre_approved_set_is_refused():
    with pytest.raises(GuiStateContractError):
        build_gui_state(
            perception_text=FULL_PERCEPTION,
            channel="dom",
            action_candidates=("delete_database",),
            pre_approved_candidates=("reobserve",),
        )
    allowed = _state(action_candidates=("reobserve",), pre_approved_candidates=APPROVED)
    assert allowed.action_candidates == ("reobserve",)


def test_allowed_kinds_never_widen_along_the_revision_chain():
    first = _state(allowed_kinds=("mouse", "key"))
    with pytest.raises(GuiStateContractError):
        _state(previous=first, allowed_kinds=("mouse", "key", "cli"))
    narrowed = _state(previous=first, allowed_kinds=("mouse",))
    assert narrowed.allowed_kinds == ("mouse",)


def test_guistate_binding_keeps_the_pre_approved_action_set():
    state = _state(action_candidates=("invoke_render", "reobserve"))
    record = system_one_guistate_gate(
        flags=_FakeFlags(),
        evaluation=_evaluation(),
        guistate=state,
        allowed_kinds=("mouse", "key"),
        pre_approved_candidates=("reobserve",),
    )
    assert record is not None
    assert record["allowed_kinds"] == ("mouse", "key"), "System One never widens kinds"
    assert record["action_candidates"] == ("reobserve",), "only pre-approved candidates survive"
    assert record["mutates"] is False
    assert record["live_jev"] is False
    assert record["gui_state_schema_version"] == GUISTATE_SCHEMA_VERSION
    assert record["gui_state_fingerprint"] == state.fingerprint
    assert record["gui_state_revision"] == state.revision
    assert "document" in record["gui_state_unperceived"] or record["gui_state_unperceived"] == ()


def test_guistate_binding_is_off_with_the_flags_off():
    state = _state()
    assert (
        system_one_guistate_gate(
            flags=SystemOneFlags.disabled(),
            evaluation=_evaluation(),
            guistate=state,
            allowed_kinds=("mouse",),
        )
        is None
    )
    assert (
        system_one_guistate_gate(
            flags=_FakeFlags(computer_use=False),
            evaluation=_evaluation(),
            guistate=state,
            allowed_kinds=("mouse",),
        )
        is None
    )
    assert (
        system_one_guistate_gate(
            flags=_FakeFlags(), evaluation=None, guistate=state, allowed_kinds=("mouse",)
        )
        is None
    )
    assert (
        system_one_guistate_gate(
            flags=_FakeFlags(),
            evaluation=_evaluation(),
            guistate=None,
            allowed_kinds=("mouse",),
        )
        is None
    )


def test_candidate_intersection_helper_never_adds_candidates():
    state = _state(action_candidates=("invoke_render", "reobserve"))
    assert guistate_action_candidates(state, pre_approved=("reobserve",)) == ("reobserve",)
    assert guistate_action_candidates(state, pre_approved=()) == ()


def test_binding_uses_a_real_dry_run_observation_and_a_real_policy():
    """Integration: real ``DryRunRunner`` observation + real ``CUPolicy``.

    Uses the actual Computer-Use components (runner observation, policy kinds) —
    but no real desktop, so this is not evidence of a real app effect.
    """
    from skaldir.computer_use import CUPolicy, DryRunRunner

    observation = DryRunRunner(observations=[FULL_PERCEPTION]).observe()
    policy = CUPolicy(allowed_kinds=("cli", "dom_click"))
    state = build_gui_state(
        perception_text=observation.text,
        channel="accessibility_tree",
        goal_step="render_scene",
        action_candidates=("invoke_render", "reobserve"),
        pre_approved_candidates=APPROVED,
        allowed_kinds=policy.allowed_kinds,
    )
    assert state.perceived("application") == "Blender"
    assert state.perception["accessibility_tree"] == observation.text
    record = system_one_guistate_gate(
        flags=_FakeFlags(),
        evaluation=_evaluation(),
        guistate=state,
        allowed_kinds=policy.allowed_kinds,
        pre_approved_candidates=("reobserve",),
    )
    assert record is not None
    assert record["allowed_kinds"] == policy.allowed_kinds, "the policy stays authority"
    assert record["mutates"] is False
    assert record["action_candidates"] == ("reobserve",)
    assert record["ground_truth"] == GROUND_TRUTH_APP_EFFECT
    assert record["certifies_effect"] is False


# --------------------------------------------------------------------------- #
# ground truth stays the real app effect                                       #
# --------------------------------------------------------------------------- #


def test_ground_truth_is_pinned_to_the_real_app_effect():
    state = _state()
    assert state.ground_truth == GROUND_TRUTH_APP_EFFECT
    assert state.certifies_effect is False
    assert state.as_dict()["certifies_effect"] is False
    fields = parse_perception_text(FULL_PERCEPTION, channel="dom")
    with pytest.raises(GuiStateContractError):
        GUIState(gui_state_id="gui-test", fields=fields, channel="dom", ground_truth="gui_state")


def test_effect_check_reads_the_post_action_perception_and_never_certifies():
    state = _state()
    before_only = effect_check(
        state, expect_change="render_window_opened", post_text="application: Blender\n"
    )
    assert before_only["effect_observed"] is False
    assert before_only["certifies"] is False
    assert before_only["ground_truth"] == GROUND_TRUTH_APP_EFFECT
    observed = effect_check(
        state, expect_change="render_window_opened", post_text=FULL_PERCEPTION
    )
    assert observed["effect_observed"] is True
    assert observed["certifies"] is False, "a derivation never certifies the app effect"
    assert observed["gui_state_id"] == state.gui_state_id


def test_live_display_flag_requires_a_live_perception_channel():
    assert _state().live_display is False, "no live GUI session exists here"
    with pytest.raises(GuiStateContractError):
        build_gui_state(perception_text="", channel="filesystem", live_display=True)


# --------------------------------------------------------------------------- #
# §10.2 — the full field list, checked additively against guistate/v1           #
# --------------------------------------------------------------------------- #

#: The goal's §10.2 list, verbatim and in order. The contract must report all of
#: them; it does so without renaming the §52 fields the runtime already reads.
GOAL_10_2_FIELDS = (
    "app_id",
    "app_name",
    "app_version",
    "window_id",
    "window_title",
    "focused",
    "document",
    "dialog_state",
    "visible_semantic_controls",
    "current_goal_step",
    "candidate_actions",
    "previous_action",
    "expected_transition",
    "observed_transition",
    "user_takeover_state",
    "capture_timestamp",
    "observation_fingerprint",
)

#: A perception that reports the §10.2 fields a §52 channel did not have.
EXTENDED_PERCEPTION = "\n".join(
    [
        "app_id: org.blender.blender",
        "app_name: Blender",
        "app_version: 4.1.7",
        "window_id: 0x3e00007",
        "window_title: scene.blend - Blender",
        "focused: true",
        "expected_transition: render_window_opened",
        "captured_at: 1737048000.25",
    ]
)


def test_contract_fields_are_the_goal_list_in_order():
    assert GUI_STATE_CONTRACT_FIELDS == GOAL_10_2_FIELDS
    assert len(GUI_STATE_CONTRACT_FIELDS) == 17
    assert set(GUI_STATE_FIELDS) >= {
        "app_id",
        "app_version",
        "window_id",
        "expected_transition",
        "user_takeover_state",
        "capture_timestamp",
    }, "the new §10.2 fields are real canonical fields, not aliases in a docstring"


def test_contract_view_reports_every_field_with_its_value_and_provenance():
    state = build_gui_state(
        perception_text=EXTENDED_PERCEPTION,
        channel="accessibility_tree",
        goal_step="render_scene",
        action_candidates=("invoke_render",),
        pre_approved_candidates=APPROVED,
    )
    view = state.contract_view()
    assert tuple(view["fields"]) == GOAL_10_2_FIELDS
    assert view["fields"]["app_id"] == {
        "perceived": True,
        "value": "org.blender.blender",
        "channel": "accessibility_tree",
        "provenance": "perception",
        "source_field": "app_id",
    }
    assert view["fields"]["app_name"]["value"] == "Blender"
    assert view["fields"]["app_name"]["source_field"] == "application"
    assert view["fields"]["capture_timestamp"]["value"] == "1737048000.25"
    assert view["fields"]["expected_transition"]["value"] == "render_window_opened"
    # not a perception: these come from the caller's context / the state itself
    assert view["fields"]["current_goal_step"]["provenance"] == "context"
    assert view["fields"]["current_goal_step"]["value"] == "render_scene"
    assert view["fields"]["candidate_actions"]["provenance"] == "context"
    assert view["fields"]["candidate_actions"]["value"] == ["invoke_render"]
    assert view["fields"]["observation_fingerprint"]["provenance"] == "derivation"
    assert view["fields"]["observation_fingerprint"]["value"] == state.fingerprint
    assert view["observation_fingerprint"] == state.fingerprint
    assert view["fingerprint"] == state.fingerprint
    assert view["observed_at"] == state.observed_at, "the state's own timestamp is kept apart"


def test_contract_view_never_defaults_an_unperceived_field():
    """What was not perceived carries no value — in the view as on the state."""
    state = build_gui_state(
        perception_text="app_id: org.blender.blender\nwindow_title: scene.blend - Blender\n",
        channel="window_metadata",
    )
    view = state.contract_view()
    for name in ("app_version", "window_id", "focused", "dialog_state"):
        entry = view["fields"][name]
        assert entry["perceived"] is False, name
        assert entry["value"] is None, name
        assert entry["channel"] == "", name
    assert "dialog_state" in view["unperceived"]
    assert "visible_semantic_controls" in view["unperceived"]
    assert "expected_transition" in view["unperceived"]
    assert view["fields"]["candidate_actions"]["provenance"] == "context"
    assert view["fields"]["current_goal_step"]["perceived"] is False


def test_contract_field_provenance_covers_all_fields_and_names_sources():
    assert set(CONTRACT_FIELD_PROVENANCE) == set(GOAL_10_2_FIELDS)
    assert all(
        provenance in ("perception", "context", "derivation")
        for provenance in CONTRACT_FIELD_PROVENANCE.values()
    )
    assert contract_field_completeness() == ()


def test_a_contract_field_without_a_provenance_is_a_contract_error():
    broken = dict(CONTRACT_FIELD_PROVENANCE)
    del broken["app_id"]
    assert contract_field_completeness(mapping=broken) == ("app_id",)


def test_new_fields_join_the_content_fingerprint():
    without = build_gui_state(perception_text="app_id: org.blender.blender\n", channel="dom")
    with_version = build_gui_state(
        perception_text="app_id: org.blender.blender\napp_version: 4.1.7\n", channel="dom"
    )
    assert with_version.fingerprint != without.fingerprint
    assert with_version.perceived("app_version") == "4.1.7"


def test_stale_anchor_and_transition_readings_stay_queryable():
    """The §10.2 fields the anchor check needs are readable — or explicitly None."""
    state = build_gui_state(
        perception_text="anchor: Render\nobserved_transition: render_window_opened\n",
        channel="accessibility_tree",
    )
    assert state.perceived("visible_anchors") == ("Render",)
    assert state.perceived("observed_changes") == ("render_window_opened",)
    view = state.contract_view()
    assert view["fields"]["visible_semantic_controls"]["value"] == ["Render"]
    assert view["fields"]["observed_transition"]["value"] == ["render_window_opened"]
    missing = build_gui_state(perception_text="app_id: x\n", channel="dom")
    assert missing.perceived("expected_transition") is None
    assert missing.contract_view()["fields"]["expected_transition"]["perceived"] is False
