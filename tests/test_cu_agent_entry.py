"""Wave 7 — an agent-facing Computer-Use entry, with honest states.

Measured 17.09. (S10 = missing): the product could probe runners and run a *fixed* dry-run demo,
but nothing in a chat could bring a Computer-Use goal in, and the gap was silent.
``run_cu_request`` is that entry (same pipeline as the panel demo: plan with the caller's client,
ordered attempts API > DOM > Vision, isolated runner, act → observe → verify → audit), and the one
stable agent tool exposes it via ``action="computer_use"``.

How the required cases map:
* correct window / successful verified effect → the verified run below;
* wrong window / layout changed / action has no effect → a failing verification (pinned);
* policy refusal / permission denied → the executor's ``PolicyViolation`` (pinned);
* no client / no plan / no runner → blocked, nothing invented (pinned);
* focus lost / user takeover / subtool anchor invalid → GUIState + System-One advisory, not part of
  this entry yet (that is the Wave 3/7 GUIState gate) — reported as the open part, not claimed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import godsv_plugin
import pytest

from skaldir.computer_use import (
    ActionResult,
    CUPolicy,
    DryRunRunner,
    run_cu_request,
)
from skaldir.llm import ScriptedLLM
from skaldir.system_one_guistate import GUI_STATE_FIELDS, GUIState, PerceivedField

DRAFT = json.dumps(
    {
        "attempts": [
            {
                "channel": "api",
                "description": "deploy via API",
                "action": {"kind": "cli", "argv": ["deploy", "--api"]},
                "verify": {"expect_stdout_contains": "deployed"},
            }
        ]
    }
)


def _client() -> ScriptedLLM:
    return ScriptedLLM(responses={"cu_step_plan": DRAFT})


def _runner(behaviors=None) -> DryRunRunner:
    return DryRunRunner(
        behaviors=behaviors or {"deploy via API": ActionResult(ok=True, stdout="deployed ok")}
    )


class FakeHostLLM:
    """Hermes' host side-LLM returns an object with ``.text`` (see the plugin's ContextLLM)."""

    payload = DRAFT

    def complete(self, messages, **kwargs):  # noqa: ARG002 — host signature
        return SimpleNamespace(text=self.payload)


# a plan whose action really runs in the sandbox and really verifies (echo → stdout)
SANDBOX_DRAFT = json.dumps(
    {
        "attempts": [
            {
                "channel": "api",
                "description": "print the marker",
                "action": {"kind": "cli", "argv": ["/bin/echo", "deployed"]},
                "verify": {"expect_stdout_contains": "deployed"},
            }
        ]
    }
)


class FakeCtx:
    def __init__(self) -> None:
        self.hooks: dict = {}
        self.llm = FakeHostLLM()
        self.tools: dict = {}

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def register_tool(self, **kwargs):
        self.tools[kwargs.get("name")] = kwargs


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    godsv_plugin._SESSIONS.clear()
    yield
    for session in list(godsv_plugin._SESSIONS.values()):
        try:
            session.end()
        except Exception:  # noqa: BLE001 — teardown must not mask the result
            pass
    godsv_plugin._SESSIONS.clear()


class TestTheProductEntry:
    def test_a_planned_request_is_executed_and_verified(self, tmp_path):
        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner=_runner(),
        )
        assert outcome["status"] == "verified", outcome
        assert outcome["verified"] is True
        assert outcome["channel"] == "api"
        assert outcome["planned"] == 1 and outcome["actions"] == 1
        # the run is recorded like any other (the panel reads the same file)
        lines = (tmp_path / "cu_runs.jsonl").read_text(encoding="utf-8").strip().splitlines()
        record = json.loads(lines[-1])
        assert record["status"] == "verified" and record["kind"] == "dry-run"
        assert record["goal"] == "deploy the staging build via the API"

    def test_without_a_client_the_gap_is_reported_and_nothing_runs(self, tmp_path):
        outcome = run_cu_request(goal="close the dialog", runtime=tmp_path, client=None)
        assert outcome["status"] == "blocked"
        assert "model client" in outcome["reason"]
        assert outcome["verified"] is False
        record = json.loads((tmp_path / "cu_runs.jsonl").read_text(encoding="utf-8").strip())
        assert record["status"] == "blocked"

    def test_a_failing_verification_is_never_a_success(self, tmp_path):
        """Wrong window / layout / no effect all land here: the effect is absent."""
        runner = DryRunRunner(
            behaviors={"deploy via API": ActionResult(ok=True, stdout="nothing happened")}
        )
        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner=runner,
        )
        assert outcome["status"] != "verified", outcome
        assert outcome["verified"] is False

    def test_a_policy_refusal_is_blocked_not_crashed(self, tmp_path):
        """Permission denied: the policy forbids the runner, so nothing is executed."""
        from skaldir.computer_use import LocalSandboxRunner

        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner=LocalSandboxRunner(tmp_path),
            policy=CUPolicy(dry_run=True),
        )
        assert outcome["status"] == "blocked"
        assert outcome["reason"].startswith("policy:")

    def test_an_unknown_runner_is_blocked(self, tmp_path):
        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner_kind="does-not-exist",
        )
        assert outcome["status"] == "blocked"
        assert "no usable runner" in outcome["reason"]

    def test_the_runners_are_reported_with_every_outcome(self, tmp_path):
        outcome = run_cu_request(goal="x", runtime=tmp_path, client=None)
        assert outcome["runners"], outcome
        assert "dry-run" in outcome["runners"]


class TestTheAgentTool:
    def test_the_stable_tool_runs_a_computer_use_goal(self, monkeypatch):
        from skaldir.execution import sandbox_available

        monkeypatch.setenv("GODSV_LLM", "1")
        ctx = FakeCtx()
        ctx.llm.payload = SANDBOX_DRAFT
        godsv_plugin.register(ctx)
        assert godsv_plugin.LIVE_REPORT_TOOL in ctx.tools, "the stable tool must be registered"
        raw = godsv_plugin.godsv_capabilities({"action": "computer_use", "goal": "print a marker"})
        payload = json.loads(raw)
        if sandbox_available():
            # a real, isolated effect that the verification accepts
            assert payload["status"] == "verified", payload
            assert payload["runner"] == "sandbox"
        else:
            assert payload["status"] == "blocked" and "no usable runner" in payload["reason"]
        assert payload["verified"] is (payload["status"] == "verified")

    def test_the_tool_can_ask_for_a_postcondition_read(self, monkeypatch):
        from skaldir.execution import sandbox_available

        if not sandbox_available():
            pytest.skip("no sandbox — the run would be blocked, not verified")
        monkeypatch.setenv("GODSV_LLM", "1")
        ctx = FakeCtx()
        ctx.llm.payload = SANDBOX_DRAFT
        godsv_plugin.register(ctx)
        payload = json.loads(
            godsv_plugin.godsv_capabilities(
                {
                    "action": "computer_use",
                    "goal": "print a marker",
                    "expect_change": "a-change-that-never-appears",
                    "post_text": "observed: nothing new",
                }
            )
        )
        assert payload["status"] == "reobserve", payload
        assert payload["verified"] is False
        assert payload["postcondition"]["certifies"] is False

    def test_without_the_switch_the_tool_reports_the_gap(self, monkeypatch):
        monkeypatch.delenv("GODSV_LLM", raising=False)
        godsv_plugin.register(FakeCtx())
        payload = json.loads(
            godsv_plugin.godsv_capabilities({"action": "computer_use", "goal": "close the dialog"})
        )
        assert payload["status"] == "blocked"
        assert "model client" in payload["reason"]

    def test_the_report_path_is_unchanged(self):
        godsv_plugin.register(FakeCtx())
        payload = json.loads(godsv_plugin.godsv_capabilities({}))
        assert payload["toolset"] == godsv_plugin.LIVE_TOOLSET
        assert "sessions" in payload

    def test_a_missing_goal_is_refused(self):
        godsv_plugin.register(FakeCtx())
        payload = json.loads(godsv_plugin.godsv_capabilities({"action": "computer_use"}))
        assert payload["status"] == "blocked"
        assert "goal" in payload["reason"]

def _gui_state(
    observed: tuple[str, ...] = (),
    action_candidates: tuple[str, ...] = (),
    pre_approved_candidates: tuple[str, ...] | None = None,
) -> GUIState:
    """A §52-complete GUIState: every field reported, unperceived ones without a value."""

    fields = {
        name: PerceivedField(name=name, value=None, perceived=False) for name in GUI_STATE_FIELDS
    }
    if observed:
        fields["observed_changes"] = PerceivedField(
            name="observed_changes", value=observed, perceived=True, channel="window_metadata"
        )
    # §53: a GUIState can only propose candidates inside its own approved set — the contract raises
    # otherwise. What decides whether an action may run is the CALLER's set (passed to the gate).
    approved = (
        action_candidates if pre_approved_candidates is None else tuple(pre_approved_candidates)
    )
    return GUIState(
        gui_state_id="wave7-postcheck",
        fields=fields,
        action_candidates=action_candidates,
        pre_approved_candidates=approved,
        channel="window_metadata",
        live_display=False,
    )


class TestThePostcondition:
    """§54.2: a verified action whose real effect cannot be read is a ``reobserve`` — never success.

    This is the honest verdict for the cases the CU matrix lists separately: wrong window, focus
    lost, user takeover, action has no effect on the observed surface, layout changed.
    """

    def test_a_missing_expected_change_becomes_a_reobserve(self, tmp_path):
        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner=_runner(),
            guistate=_gui_state(),
            expect_change="render_window_opened",
            post_text="nothing changed on screen",
        )
        assert outcome["status"] == "reobserve", outcome
        assert outcome["verified"] is False
        assert outcome["postcondition"]["effect_observed"] is False
        assert outcome["postcondition"]["certifies"] is False
        assert "reobserve" in outcome["detail"]

    def test_an_observed_change_keeps_the_verified_verdict(self, tmp_path):
        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner=_runner(),
            guistate=_gui_state(("render_window_opened",)),
            expect_change="render_window_opened",
            post_text="window list: render_window_opened",
        )
        assert outcome["status"] == "verified", outcome
        assert outcome["verified"] is True
        assert outcome["postcondition"]["effect_observed"] is True
        assert outcome["postcondition"]["certifies"] is False
        assert outcome["postcondition"]["ground_truth"] == "app_effect"

    def test_without_a_guistate_the_previous_behaviour_stands(self, tmp_path):
        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner=_runner(),
        )
        assert outcome["status"] == "verified"
        assert "postcondition" not in outcome

    def test_a_failed_action_is_never_upgraded_by_the_postcondition(self, tmp_path):
        runner = DryRunRunner(
            behaviors={"deploy via API": ActionResult(ok=True, stdout="nothing happened")}
        )
        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner=runner,
            guistate=_gui_state(("render_window_opened",)),
            expect_change="render_window_opened",
            post_text="window list: render_window_opened",
        )
        assert outcome["status"] != "verified"
        assert outcome["verified"] is False


class TestTheSystemOneGate:
    """§53/§54.3 — the GUIState's candidates ∩ the caller's approved set: advisory, never widening.

    The gate (`system_one_guistate_gate`) and the filter (`guistate_action_candidates`) were
    measured as test-covered but **unwired**: no caller in the run path ever consulted them.
    """

    @staticmethod
    def _flags(tmp_path):
        from skaldir.system_one_flags import load_system_one_flags

        state_dir = tmp_path / "godsv"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "settings.yaml").write_text(
            "system_one:\n  enabled: true\n  provider: llm_system_one_adapter\n"
            "  computer_use: true\n",
            encoding="utf-8",
        )
        return load_system_one_flags(state_dir)

    @staticmethod
    def _evaluation(choice: str = ""):
        from skaldir.system_one_subtools import CANDIDATE_CHOICE_QUESTION_ID

        class _Answer:
            def __init__(self, choice):
                self.choice = choice

        class _Evaluation:
            provider = "llm_system_one_adapter"
            provider_model = "test-adapter"
            live_jev = False
            evaluation_id = "eval-wave3-gate"

            def __init__(self, choice):
                self.answers = {CANDIDATE_CHOICE_QUESTION_ID: _Answer(choice)} if choice else {}

        return _Evaluation(choice)

    def test_a_proposal_without_pre_approval_blocks_the_run(self, tmp_path):
        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner=_runner(),
            guistate=_gui_state(("render_window_opened",), action_candidates=("candidate_a",)),
            flags=self._flags(tmp_path),
            evaluation=self._evaluation(),
        )
        assert outcome["status"] == "blocked", outcome
        assert outcome["verified"] is False
        assert "pre-approved" in outcome["reason"]
        assert outcome["system_one"]["certifies_effect"] is False
        assert "actions" not in outcome
        records = [
            json.loads(line)
            for line in (tmp_path / "cu_runs.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert records[-1]["status"] == "blocked"

    def test_a_pre_approved_candidate_lets_the_run_proceed_with_the_advisory(self, tmp_path):
        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner=_runner(),
            guistate=_gui_state(("render_window_opened",), action_candidates=("candidate_a",)),
            flags=self._flags(tmp_path),
            evaluation=self._evaluation(),
            pre_approved_candidates=("candidate_a",),
        )
        assert outcome["status"] == "verified", outcome
        gate = outcome["system_one"]
        assert gate["action_candidates"] == ("candidate_a",)
        assert gate["mutates"] is False
        assert gate["certifies_effect"] is False
        assert gate["live_jev"] is False
        assert gate["allowed_kinds"], "the plan's kinds are reported unchanged"

    def test_the_gate_never_widens_the_allowed_set(self, tmp_path):
        # the state proposes candidate_a, the caller approves candidate_b: intersection empty,
        # so the caller cannot push an unproposed action through the gate
        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner=_runner(),
            guistate=_gui_state(("render_window_opened",), action_candidates=("candidate_a",)),
            flags=self._flags(tmp_path),
            evaluation=self._evaluation(),
            pre_approved_candidates=("candidate_b",),
        )
        assert outcome["status"] == "blocked", outcome
        assert outcome["system_one"]["action_candidates"] == ()

    def test_without_flags_there_is_no_advisory(self, tmp_path):
        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner=_runner(),
            guistate=_gui_state(("render_window_opened",), action_candidates=("candidate_a",)),
        )
        assert outcome["status"] == "verified"
        assert "system_one" not in outcome

    def test_the_post_check_uses_the_same_approved_set(self, tmp_path):
        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner=_runner(),
            guistate=_gui_state(("render_window_opened",), action_candidates=("candidate_a",)),
            flags=self._flags(tmp_path),
            evaluation=self._evaluation(),
            pre_approved_candidates=("candidate_a",),
            post_guistate=_gui_state(("render_window_opened",), action_candidates=("candidate_a",)),
        )
        assert outcome["status"] == "verified", outcome
        assert outcome["post_system_one"]["action_candidates"] == ("candidate_a",)
        assert outcome["post_system_one"]["certifies_effect"] is False


class TestTheEscalationHalf:
    """§10.3/§53 + §12 — the provider's choice is validated, and System 2 may take over instead.

    `validate_candidate_choice` and `system_two_escalation_decision` were measured as having no
    production caller either; both are the "refuse or escalate instead of act" half of the chain.
    """

    @staticmethod
    def _flags(tmp_path):
        return TestTheSystemOneGate._flags(tmp_path)

    def test_a_provider_choice_outside_the_allowed_set_is_refused(self, tmp_path):
        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner=_runner(),
            guistate=_gui_state(("render_window_opened",), action_candidates=("candidate_a",)),
            flags=self._flags(tmp_path),
            evaluation=TestTheSystemOneGate._evaluation(choice="candidate_zzz"),
            pre_approved_candidates=("candidate_a",),
        )
        assert outcome["status"] == "blocked", outcome
        assert "candidate choice refused" in outcome["reason"]
        assert "actions" not in outcome
        records = [
            json.loads(line)
            for line in (tmp_path / "cu_runs.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert records[-1]["status"] == "blocked"

    def test_an_allowed_provider_choice_keeps_the_run_and_stays_silent_about_system_two(
        self, tmp_path
    ):
        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner=_runner(),
            guistate=_gui_state(("render_window_opened",), action_candidates=("candidate_a",)),
            flags=self._flags(tmp_path),
            evaluation=TestTheSystemOneGate._evaluation(choice="candidate_a"),
            pre_approved_candidates=("candidate_a",),
        )
        assert outcome["status"] == "verified", outcome
        assert "system_two" not in outcome

    def test_an_unmeasured_reading_escalates_instead_of_acting(self, tmp_path):
        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner=_runner(),
            guistate=_gui_state(("render_window_opened",), action_candidates=("candidate_a",)),
            flags=self._flags(tmp_path),
            evaluation=TestTheSystemOneGate._evaluation(choice="candidate_a"),
            pre_approved_candidates=("candidate_a",),
            escalation_check=True,
        )
        assert outcome["status"] == "blocked", outcome
        decision = outcome["system_two"]
        assert decision["escalate"] is True
        assert decision["authorizes_action"] is False
        assert decision["advisory_only"] is True
        assert decision["unmeasured"] or decision["triggers"], decision
        assert "actions" not in outcome

    def test_a_missing_frozen_profile_escalates_even_with_every_signal_measured(self, tmp_path):
        from skaldir.computer_use import EscalationSignals

        signals = EscalationSignals(
            state_sufficient=True,
            candidate_action_available=True,
            app_version_applicable=True,
        )
        outcome = run_cu_request(
            goal="deploy the staging build via the API",
            runtime=tmp_path,
            client=_client(),
            runner=_runner(),
            guistate=_gui_state(("render_window_opened",), action_candidates=("candidate_a",)),
            flags=self._flags(tmp_path),
            evaluation=TestTheSystemOneGate._evaluation(choice="candidate_a"),
            pre_approved_candidates=("candidate_a",),
            escalation_signals=signals,
        )
        assert outcome["status"] == "blocked", outcome
        assert outcome["system_two"]["escalate"] is True
        assert outcome["system_two"]["profile_id"] == ""


def _subtool(*, versions=("1.0",), anchors=("render_button",), start=("app_running",)):
    from skaldir.system_one_subtools import REOBSERVE_TRIGGERS, VisualSubtool

    return VisualSubtool(
        capability_id="blender.render",
        version="1.0",
        supported_app_versions=versions,
        supported_locales=("en",),
        start_conditions=start,
        anchors=anchors,
        allowed_actions=("invoke_render",),
        expected_transitions=("render_started",),
        completion_conditions=("render_window_opened",),
        failure_conditions=("render_failed",),
        reobserve_policy=(REOBSERVE_TRIGGERS[0],),
        system2_escalation_policy=("insufficient_state",),
        verification_contract="the app's own state shows the render window",
    )


class TestTheVisualSubtoolGuard:
    """§11/§56 — the whole subtool layer had no production caller; the guard is what makes it live.

    `check_applicability`, `check_blind_replay`, `assert_no_blind_replay` and
        `evaluate_subtool_step`
    were measured as reachable only from their own module and the tests.
    """

    def test_an_unobserved_anchor_refuses_the_replay(self, tmp_path):
        outcome = run_cu_request(
            goal="render the scene",
            runtime=tmp_path,
            client=_client(),
            runner=_runner(),
            subtool=_subtool(),
            observed_start_conditions=("app_running",),
            observed_anchors=(),  # the anchor is NOT on screen right now
        )
        assert outcome["status"] == "reobserve", outcome
        assert outcome["verified"] is False
        assert outcome["missing_anchors"] == ["render_button"]
        assert "blind replay refused" in outcome["reason"]
        assert "actions" not in outcome, "nothing may be replayed blind"

    def test_an_unsupported_app_version_is_not_probably_fine(self, tmp_path):
        outcome = run_cu_request(
            goal="render the scene",
            runtime=tmp_path,
            client=_client(),
            runner=_runner(),
            subtool=_subtool(versions=("1.0",)),
            app_version="9.9",
            locale="en",
            observed_start_conditions=("app_running",),
            observed_anchors=("render_button",),
        )
        assert outcome["status"] != "verified", outcome
        assert outcome["subtool"]["applicability"]["applicable"] is False

    def test_an_observed_anchor_lets_the_step_run_and_the_decision_travels(self, tmp_path):
        outcome = run_cu_request(
            goal="render the scene",
            runtime=tmp_path,
            client=_client(),
            runner=_runner(),
            subtool=_subtool(),
            app_version="1.0",
            locale="en",
            observed_start_conditions=("app_running",),
            observed_anchors=("render_button",),
        )
        assert outcome["status"] == "verified", outcome
        assert outcome["subtool"]["proceed"] is True
        assert outcome["subtool"]["applicability"]["applicable"] is True

    def test_without_a_subtool_nothing_changes(self, tmp_path):
        outcome = run_cu_request(
            goal="render the scene", runtime=tmp_path, client=_client(), runner=_runner()
        )
        assert outcome["status"] == "verified"
        assert "subtool" not in outcome
