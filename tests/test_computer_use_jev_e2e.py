"""§30 E2E-J3 — Computer Use: correct GUIState → allowed candidate → action → new
observation → postcondition success, **and** its counter-case (wrong window /
stale anchor → reobserve or escalation, no blind click).

ROT-FIRST evidence. The path below did not exist as one runnable flow; captured
against the committed tree (`HEAD`) in a detached worktree with only this file
copied in, because it exercises modules that are all uncommitted additions:

    $ git worktree add --detach $HOME/jev-red-head HEAD
    $ cp tests/test_computer_use_jev_e2e.py $HOME/jev-red-head/tests/
    $ cd $HOME/jev-red-head && PYTHONPATH=src \\
        /home/willy/projects/gods-voice/.venv/bin/python -m pytest \\
        tests/test_computer_use_jev_e2e.py -p no:randomly
    E   ModuleNotFoundError: No module named 'godsv.system_one_subtools'
    !!! Interrupted: 1 error during collection !!!
    1 error in 0.09s

What this file pins (behaviour, never source text):

* the full E2E-J3 chain runs through the **existing** components — the real
  Computer-Use executor and its dry-run runner, the real policy ``allowed_kinds``,
  the real ``CUPolicy``/``DryRunRunner``, the real GUIState contract, the real
  question pack and the real internal evaluation contract. No second
  orchestrator is introduced anywhere;
* the candidate that is executed is one that was **already allowed**; the gate
  hands back the unchanged ``allowed_kinds`` and only pre-approved candidates;
* the counter-case executes **zero** actions: a stale anchor / wrong window
  reobserves or escalates, and a blind replay of the stored macro is refused;
* with no live provider access (the honest state here) the chain degrades to the
  System-2 rung and still executes nothing;
* ground truth stays the real app effect: every GUIState records
  ``certifies_effect: False`` and the postcondition check reads the post-action
  perception.

Evidence class: integration across real components with test doubles where an
external system would be needed (LLM adapter for the provider, a scripted
perception for the desktop). NOT live: no TypeSafe call, no real desktop — these
tests script the perception instead of driving one (``skaldir act check --json``
reports what this host can really drive).
"""

from __future__ import annotations

import json

import pytest

from skaldir.computer_use import (
    Action,
    ActionResult,
    Attempt,
    CUExecutor,
    CUPolicy,
    DryRunRunner,
    EscalationSignals,
    PolicyViolation,
    Verification,
    guistate_action_candidates,
    system_one_guistate_gate,
    system_two_escalation_decision,
)
from skaldir.events import EventStore
from skaldir.system_one import (
    ACCESS_LLM_ADAPTER,
    PROVIDER_LLM_ADAPTER,
    EvaluationContext,
    SystemOneState,
)
from skaldir.system_one_adapters import (
    LLMSystemOneAdapter,
    SystemOneFallbackChain,
    TypeSafeJevEvaluator,
)
from skaldir.system_one_flags import SystemOneFlags
from skaldir.system_one_guistate import build_gui_state, effect_check
from skaldir.system_one_subtools import (
    CAUSE_ANCHOR_MISSING,
    BlindReplayRefused,
    CandidateChoiceViolation,
    VisualSubtool,
    allowed_candidate_options,
    assert_no_blind_replay,
    check_blind_replay,
    compose_computer_use_questions,
    evaluate_subtool_step,
    validate_candidate_choice,
)

#: What the accessibility tree reports *before* the step. Fabricated input for a
#: scripted perception — not an observed desktop.
PRE_PERCEPTION = "\n".join(
    [
        "app_id: org.blender.blender",
        "application: Blender",
        "app_version: 4.1.7",
        "window_id: 0x3e00007",
        "window_title: scene.blend - Blender",
        "focused: true",
        "document: scene.blend",
        "blocking_dialog: none",
        "anchor: Render",
        "anchor: Output Properties",
        "expected_transition: render_window_opened",
    ]
)

#: What the same channel reports *after* the action.
POST_PERCEPTION = "\n".join(
    [
        "app_id: org.blender.blender",
        "application: Blender",
        "app_version: 4.1.7",
        "window_id: 0x3e00009",
        "window_title: Rendering - Blender",
        "focused: true",
        "document: scene.blend",
        "blocking_dialog: none",
        "anchor: Render",
        "anchor: Output Properties",
        "previous_action: invoke_render",
        "observed_change: render_window_opened",
    ]
)

#: The counter-case: a different window, its anchors gone.
WRONG_WINDOW_PERCEPTION = "\n".join(
    [
        "app_id: org.blender.blender",
        "application: Blender",
        "app_version: 4.1.7",
        "window_id: 0x3e00f11",
        "window_title: Preferences - Blender",
        "focused: false",
    ]
)

#: The already-allowed *candidate option ids* of §10.3. Jev may only choose among
#: these; the runtime maps a chosen candidate onto the pre-approved action.
PRE_APPROVED = ("candidate_a", "reobserve", "escalate")

#: candidate option → the concrete pre-approved action the runtime would run.
CANDIDATE_ACTIONS = {"candidate_a": "invoke_render"}

STEP_MACRO = VisualSubtool(
    capability_id="blender.render_scene",
    version="v1",
    supported_app_versions=("4.1.*",),
    supported_locales=("en_US",),
    start_conditions=("app_running", "document_open"),
    anchors=("Render", "Output Properties"),
    allowed_actions=("invoke_render",),
    expected_transitions=("render_window_opened",),
    completion_conditions=("output_file_present",),
    failure_conditions=("anchor_missing", "blocking_dialog_present"),
    reobserve_policy=("anchor_mismatch", "transition_mismatch"),
    system2_escalation_policy=("insufficient_state",),
    verification_contract=(
        "every attempt carries an expectation; the effect is read from perception"
    ),
    system_one_prechecks=("state_sufficient_for_action", "stored_subtool_anchor_valid"),
    system_one_postchecks=("previous_action_succeeded", "expected_transition_occurred"),
)

CU_FLAGS = SystemOneFlags(
    enabled=True,
    provider=PROVIDER_LLM_ADAPTER,
    shadow_mode=False,
    computer_use=True,
)

ANSWER_VALUES = {
    "correct_application": 0.97,
    "correct_window": 0.94,
    "correct_document": 0.92,
    "blocking_dialog_present": 0.03,
    "state_sufficient_for_action": 0.93,
    "stored_subtool_anchor_valid": 0.95,
    "previous_action_succeeded": 0.05,
    "expected_transition_occurred": 0.05,
    "unexpected_transition_occurred": 0.05,
    "wrong_target_changed": 0.02,
    "reobserve_required": 0.05,
    "retry_safe": 0.05,
    "alternate_candidate_needed": 0.05,
    "state_recoverable": 0.05,
    "system2_needed": 0.02,
    "user_takeover_detected": 0.02,
    "recovery_needed": 0.02,
}


class ScriptedLLM:
    """Deterministic LLM double for the readiness adapter (never a live provider)."""

    def __init__(self, answers: dict) -> None:
        self.answers = answers
        self.prompts: list[str] = []

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        self.prompts.append(prompt)
        return json.dumps({"answers": self.answers})


class FrozenProfile:
    """A test threshold profile: frozen floor + Noul band (invented for the test)."""

    profile_id = "e2e-test-profile"
    frozen = True
    choice_threshold = 0.7
    score_threshold = 0.7
    noul_band = (0.35, 0.65)


def _answer_payload(questions, *, overrides: dict[str, float] | None = None) -> dict:
    values = dict(ANSWER_VALUES)
    values.update(overrides or {})
    payload: dict[str, dict] = {}
    for question in questions.questions:
        short = question.id.split(".")[-1]
        if question.kind == "noul":
            payload[question.id] = {"value": values.get(short, 0.05)}
            continue
        options = tuple(question.options)
        share = round(1.0 / len(options), 6)
        probabilities = {option: share for option in options}
        probabilities[options[-1]] = round(1.0 - share * (len(options) - 1), 6)
        payload[question.id] = {
            "choice": options[0],
            "probabilities": probabilities,
            "confidence": 0.93,
        }
    return payload


def _evaluate(questions, *, overrides: dict[str, float] | None = None):
    llm = ScriptedLLM(_answer_payload(questions, overrides=overrides))
    adapter = LLMSystemOneAdapter(llm, model="system-one-double")
    import asyncio

    return asyncio.run(
        adapter.evaluate(
            state=SystemOneState(payload={"task": "render_scene"}, input_scope="task"),
            questions=questions,
            context=EvaluationContext(
                run_id="run-e2e", goal_id="goal-e2e", goal_revision=1, event_cursor=7
            ),
        )
    )


def _state(perception: str, runner, *, previous=None, candidates=PRE_APPROVED):
    return build_gui_state(
        perception_text=perception,
        channel="accessibility_tree",
        goal_step="render_scene",
        previous=previous,
        action_candidates=candidates,
        pre_approved_candidates=PRE_APPROVED,
        allowed_kinds=runner.policy.allowed_kinds,
    )


class _Runner:
    """The real ``DryRunRunner`` plus the real policy it runs under."""

    def __init__(self, observations: list[str]) -> None:
        self.runner = DryRunRunner(
            observations=observations,
            behaviors={"invoke_render": ActionResult(ok=True, stdout="render_started")},
        )
        self.policy = CUPolicy(allowed_kinds=("mouse", "key", "dom_click"), max_actions=5)


# --------------------------------------------------------------------------- #
# E2E-J3 — the happy path                                                      #
# --------------------------------------------------------------------------- #


def test_e2e_j3_correct_state_to_verified_postcondition(tmp_path):
    runner = _Runner([POST_PERCEPTION])
    events = EventStore(tmp_path / "events.db")
    executor = CUExecutor(runner.policy, events=events, session_id="e2e-j3")

    # 1) the structured, versioned state of the CURRENT perception
    state = _state(PRE_PERCEPTION, runner)
    assert state.perceived("window_title") == "scene.blend - Blender"
    assert state.certifies_effect is False
    assert state.live_display is False, "no live desktop exists in this environment"

    # 2) the per-step question set: candidate options are the already-allowed ones
    questions = compose_computer_use_questions(allowed_candidates=PRE_APPROVED)
    choice = next(q for q in questions.questions if q.kind == "choice")
    assert set(choice.options) == set(allowed_candidate_options(PRE_APPROVED))

    # 3) the System-One reading (readiness adapter double: never live Jev)
    evaluation = _evaluate(questions)
    assert evaluation.provider == PROVIDER_LLM_ADAPTER
    assert evaluation.live_jev is False
    assert evaluation.provider_access_mode == ACCESS_LLM_ADAPTER
    assert evaluation.answers[choice.id].choice == "candidate_a"

    # 4) the advisory gate: unchanged kinds, only pre-approved candidates
    record = system_one_guistate_gate(
        flags=CU_FLAGS,
        evaluation=evaluation,
        guistate=state,
        allowed_kinds=runner.policy.allowed_kinds,
        pre_approved_candidates=("reobserve", "candidate_a"),
    )
    assert record is not None
    assert record["allowed_kinds"] == runner.policy.allowed_kinds
    assert record["mutates"] is False
    assert record["certifies_effect"] is False
    assert record["ground_truth"] == "app_effect"
    assert set(record["action_candidates"]) <= set(PRE_APPROVED)

    # 5) the provider's candidate must be one of the allowed ones (rule in code)
    chosen = validate_candidate_choice(
        evaluation.answers[choice.id].choice, allowed_candidates=record["action_candidates"]
    )
    assert chosen in PRE_APPROVED
    assert chosen in CANDIDATE_ACTIONS, "only a mapped candidate can become an action"
    with pytest.raises(CandidateChoiceViolation):
        # a choice that was never allowed is refused, not coerced
        validate_candidate_choice(
            "keyboard_shortcut", allowed_candidates=record["action_candidates"]
        )
    assert guistate_action_candidates(state, pre_approved=("escalate",)) == ("escalate",)

    # 6) no System-2 trigger fires for this healthy step
    decision = system_two_escalation_decision(
        profile=FrozenProfile(),
        evaluation=evaluation,
        signals=EscalationSignals(
            state_sufficient=True,
            candidate_action_available=True,
            app_version_applicable=True,
            noul_value=0.95,
        ),
        allowed_candidates=record["action_candidates"],
    )
    assert decision.escalate is False
    assert decision.authorizes_action is False

    # 7) the subtool step proceeds with a declared-allowed action
    step = evaluate_subtool_step(
        STEP_MACRO,
        app_version="4.1.7",
        locale="en_US",
        observed_start_conditions=("app_running", "document_open"),
        observed_anchors=("Render", "Output Properties"),
        observed_transitions=(),
        performed_actions=(),
    )
    assert step.proceed is True
    assert step.action == CANDIDATE_ACTIONS[chosen]
    assert step.action in STEP_MACRO.allowed_actions

    # 8) act through the REAL executor + dry-run runner
    attempt = Attempt(
        channel="dom",
        action=Action(
            kind="dom_click", description="invoke_render", payload={"selector": "#render"}
        ),
        verify=Verification(expect_stdout_contains="render_started", description="render started"),
    )
    result = executor.run("render_scene", [attempt], runner.runner)
    assert result.status == "verified", result.detail
    assert result.channel == "dom"
    assert len(runner.runner.actions) == 1
    assert runner.runner.actions[0].description == "invoke_render"

    # 9) observe again: a NEW state on the revision chain
    post = _state(POST_PERCEPTION, runner, previous=state)
    assert post.revision == 2
    assert post.previous == state.gui_state_id
    assert post.perceived("observed_changes") == ("render_window_opened",)
    assert post.contract_view()["fields"]["observation_fingerprint"]["value"] == post.fingerprint
    assert post.certifies_effect is False

    # 10) postcondition: the real app effect is read from the post-action perception
    effect = effect_check(state, expect_change="render_window_opened", post_text=POST_PERCEPTION)
    assert effect["effect_observed"] is True
    assert effect["certifies"] is False
    post_eval = _evaluate(
        questions,
        overrides={"previous_action_succeeded": 0.93, "expected_transition_occurred": 0.91},
    )
    assert post_eval.noul("computer_use.post.previous_action_succeeded") == pytest.approx(0.93)
    post_record = system_one_guistate_gate(
        flags=CU_FLAGS,
        evaluation=post_eval,
        guistate=post,
        allowed_kinds=runner.policy.allowed_kinds,
        pre_approved_candidates=PRE_APPROVED,
    )
    assert post_record["allowed_kinds"] == runner.policy.allowed_kinds
    assert post_record["certifies_effect"] is False

    # the audit trail is real: the whole step is in the event store
    kinds = [event.kind for event in events.iter_session("e2e-j3")]
    assert "cu_attempt" in kinds and "cu_action" in kinds and "cu_verified" in kinds
    events.close()


# --------------------------------------------------------------------------- #
# the counter-case — stale anchor / wrong window                               #
# --------------------------------------------------------------------------- #


def test_e2e_j3_counter_case_stale_anchor_never_clicks(tmp_path):
    runner = _Runner([WRONG_WINDOW_PERCEPTION])
    events = EventStore(tmp_path / "events.db")
    executor = CUExecutor(runner.policy, events=events, session_id="e2e-j3-counter")

    stale = _state(WRONG_WINDOW_PERCEPTION, runner)
    assert stale.perceived("window_title") == "Preferences - Blender"
    assert stale.perceived("visible_anchors") is None

    # a blind replay of the stored macro is refused, with the missing anchor named
    guard = check_blind_replay(
        STEP_MACRO,
        observed_start_conditions=("app_running", "document_open"),
        observed_anchors=(),
    )
    assert guard.replay_allowed is False
    assert guard.missing_anchors == ("Render", "Output Properties")
    with pytest.raises(BlindReplayRefused):
        assert_no_blind_replay(guard)

    # the subtool step reobserves instead of proceeding
    step = evaluate_subtool_step(
        STEP_MACRO,
        app_version="4.1.7",
        locale="en_US",
        observed_start_conditions=("app_running", "document_open"),
        observed_anchors=(),
        observed_transitions=(),
        performed_actions=(),
    )
    assert step.proceed is False
    assert step.action == ""
    assert step.reobserve is True
    assert step.checks["anchor_reached"] is False
    assert CAUSE_ANCHOR_MISSING in step.reasons

    # a stale-anchor / wrong-window reading escalates rather than being trusted
    questions = compose_computer_use_questions(allowed_candidates=("reobserve", "escalate"))
    evaluation = _evaluate(
        questions,
        overrides={
            "correct_application": 0.2,
            "correct_window": 0.1,
            "stored_subtool_anchor_valid": 0.05,
            "reobserve_required": 0.9,
        },
    )
    gate = system_one_guistate_gate(
        flags=CU_FLAGS,
        evaluation=evaluation,
        guistate=stale,
        allowed_kinds=runner.policy.allowed_kinds,
        pre_approved_candidates=("reobserve", "escalate"),
    )
    assert gate["action_candidates"] == ("reobserve", "escalate")
    assert gate["allowed_kinds"] == runner.policy.allowed_kinds

    decision = system_two_escalation_decision(
        profile=FrozenProfile(),
        evaluation=evaluation,
        signals=EscalationSignals(
            state_sufficient=False,
            candidate_action_available=True,
            app_version_applicable=True,
            unknown_ui=True,
        ),
        allowed_candidates=("reobserve", "escalate"),
    )
    assert decision.escalate is True
    assert "insufficient_state" in decision.triggers
    assert decision.authorizes_action is False

    # nothing was clicked, and the executor was never even called
    assert runner.runner.actions == []
    with pytest.raises(PolicyViolation):
        executor.run(
            "render_scene",
            [
                Attempt(
                    channel="dom",
                    action=Action(kind="dom_click", description="invoke_render"),
                    verify=Verification(),  # no expectation → the executor refuses
                )
            ],
            runner.runner,
        )
    assert runner.runner.actions == []
    kinds = [event.kind for event in events.iter_session("e2e-j3-counter")]
    assert "cu_blocked" in kinds and "cu_action" not in kinds
    events.close()


def test_e2e_j3_counter_case_no_live_provider_escalates_without_acting():
    """No provider access (the honest state here) → System-2 rung, no action."""
    runner = _Runner([POST_PERCEPTION])
    chain = SystemOneFallbackChain(
        primary=TypeSafeJevEvaluator(),  # no credential → blocked_external_access
        llm_fallback=None,
        voice_fallback=lambda *, reason: {"route": "escalate_system_two", "reason": reason},
    )
    questions = compose_computer_use_questions(allowed_candidates=PRE_APPROVED)
    import asyncio

    outcome = asyncio.run(
        chain.evaluate(
            state=SystemOneState(payload={"task": "render_scene"}),
            questions=questions,
            context=EvaluationContext(run_id="run-e2e", goal_id="goal-e2e"),
        )
    )
    assert outcome.rung == "system2_voice_fallback"
    assert outcome.evaluation is None, "no System-One answer is invented"
    assert "blocked_external_access" in outcome.error_codes
    assert outcome.authorizes_action is False
    assert runner.runner.actions == []
