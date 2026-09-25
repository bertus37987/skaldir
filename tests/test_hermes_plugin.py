"""Hermes plugin adapter tests — modes, delivery, secret redaction, LLM synthesis.

The adapter package is importable via pytest's ``pythonpath`` config
(``integrations/hermes`` in pyproject.toml).
"""

import json
import threading
import types

import godsv_plugin
import pytest

FAKE_OPENAI = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"

TOOL_CALL = {
    "tool_name": "terminal",
    "args": {"command": "pytest -q"},
    "result": "boom",
    "status": "error",
    "error_type": "ImportError",
    "error_message": "cannot import name 'cv_split'",
}

ERROR_TEXT = "ImportError: cannot import name 'cv_split'"


class FakeCtx:
    """Minimal stand-in for the Hermes plugin context."""

    def __init__(self) -> None:
        self.hooks: dict = {}

    def register_hook(self, name, fn):
        self.hooks[name] = fn


class FakeLLM:
    """Stand-in for ``ctx.llm`` (host-owned side-LLM)."""

    def __init__(self, text: str = "", error: bool = False) -> None:
        self.text = text
        self.error = error
        self.calls: list[dict] = []

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self.error:
            raise RuntimeError("host denied")
        return types.SimpleNamespace(text=self.text)


class GatedLLM(FakeLLM):
    """FakeLLM whose completion blocks until the test opens the gate."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.gate = threading.Event()

    def complete(self, messages, **kwargs):
        self.gate.wait(timeout=5)
        return super().complete(messages, **kwargs)


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("GODSV_MODE", raising=False)
    monkeypatch.delenv("GODSV_LLM", raising=False)
    monkeypatch.delenv("GODSV_LLM_TIMEOUT", raising=False)
    monkeypatch.delenv("GODSV_LLM_GRACE", raising=False)
    godsv_plugin._SESSIONS.clear()
    fake = FakeCtx()
    godsv_plugin.register(fake)
    yield fake
    for session in godsv_plugin._SESSIONS.values():
        try:
            session.end()
        except Exception:
            pass
    godsv_plugin._SESSIONS.clear()


def _fail_tool_call(ctx, session_id, times=1):
    for _ in range(times):
        ctx.hooks["post_tool_call"](**{**TOOL_CALL, "session_id": session_id})


def _seed_incident(session) -> None:
    from skaldir.incidents import Incident
    from skaldir.monitor import error_fingerprint

    session.incidents.upsert(
        Incident(
            signature=error_fingerprint(ERROR_TEXT),
            title="cv_split Import-Fix",
            root_cause="Funktion wurde umbenannt",
            resolution_steps=["Import auf split_cv umgestellt"],
            verification="pytest -q grün",
            status="active",
            confidence=0.9,
        )
    )


def _synth_payload(hint_md="**LLM-Hinweis:** cv_split wurde umbenannt — Import prüfen."):
    return json.dumps(
        {
            "hint_md": hint_md,
            "confidence": 0.95,
            "evidence_ids": ["incident:1"],
            "verify": "pytest -q",
        }
    )


def test_register_registers_expected_hooks(ctx):
    assert set(ctx.hooks) == {
        "on_session_start",
        "post_tool_call",
        "post_llm_call",
        "on_session_end",
        "transform_tool_result",
        "pre_tool_call",
        "pre_verify",
        "transform_llm_output",
        "on_interim_message",
        "pre_llm_call",
    }


def test_shadow_mode_mirrors_events_and_logs_hint(ctx, tmp_path):
    ctx.hooks["on_session_start"](session_id="s1")
    _fail_tool_call(ctx, "s1", times=3)
    session = godsv_plugin._SESSIONS["s1"]
    kinds = [event.kind for event in session.store.iter_session("s1")]
    assert kinds.count("tool_call") == 3
    assert kinds.count("tool_result") == 3
    inbox = (tmp_path / "godsv" / "inbox.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(inbox) >= 1
    assert "verify it yourself" in inbox[-1]
    # shadow mode must not modify results
    out = ctx.hooks["transform_tool_result"](tool_name="terminal", result="boom", session_id="s1")
    assert out is None


def test_whisper_mode_delivers_on_triggering_result(ctx, monkeypatch):
    ctx.hooks["on_session_start"](session_id="w1")
    monkeypatch.setenv("GODSV_MODE", "whisper")
    _fail_tool_call(ctx, "w1", times=2)
    out = ctx.hooks["transform_tool_result"](
        tool_name="terminal", result="boom2", session_id="w1"
    )
    assert isinstance(out, str)
    assert out.startswith("boom2")
    assert "[voice " in out
    assert "verify it yourself" in out
    # delivered exactly once
    assert ctx.hooks["transform_tool_result"](result="later", session_id="w1") is None
    session = godsv_plugin._SESSIONS["w1"]
    kinds = [event.kind for event in session.store.iter_session("w1")]
    assert "hint_delivered" in kinds


def test_whisper_non_string_result_defers_delivery(ctx, monkeypatch):
    ctx.hooks["on_session_start"](session_id="w2")
    monkeypatch.setenv("GODSV_MODE", "whisper")
    _fail_tool_call(ctx, "w2", times=2)
    session = godsv_plugin._SESSIONS["w2"]
    assert session.pending  # a hint is queued
    assert ctx.hooks["transform_tool_result"](result=None, session_id="w2") is None
    assert session.pending  # not lost
    out = ctx.hooks["transform_tool_result"](result="ok", session_id="w2")
    assert isinstance(out, str) and out.startswith("ok")


def test_off_mode_records_nothing(ctx, monkeypatch, tmp_path):
    monkeypatch.setenv("GODSV_MODE", "off")
    ctx.hooks["on_session_start"](session_id="off1")
    _fail_tool_call(ctx, "off1", times=2)
    assert godsv_plugin._SESSIONS == {}
    assert not (tmp_path / "godsv").exists()


def test_secret_result_is_redacted_not_crashed(ctx):
    ctx.hooks["on_session_start"](session_id="s2")
    ctx.hooks["post_tool_call"](
        tool_name="terminal",
        args={"command": "env"},
        result=f"OPENAI_KEY={FAKE_OPENAI}",
        session_id="s2",
        status="success",
    )
    session = godsv_plugin._SESSIONS["s2"]
    results = [e for e in session.store.iter_session("s2") if e.kind == "tool_result"]
    assert results and results[0].payload.get("_godsv_redacted") == 1
    assert FAKE_OPENAI not in str(results[0].payload)


def test_session_end_clears_state(ctx):
    ctx.hooks["on_session_start"](session_id="s3")
    assert "s3" in godsv_plugin._SESSIONS
    ctx.hooks["on_session_end"](session_id="s3")
    assert "s3" not in godsv_plugin._SESSIONS


def test_hint_gets_evidence_from_incident_memory(ctx):
    ctx.hooks["on_session_start"](session_id="m1")
    session = godsv_plugin._SESSIONS["m1"]
    _seed_incident(session)
    _fail_tool_call(ctx, "m1", times=2)
    hints = session.pending
    assert hints, "expected a hint to be queued"
    assert any("cv_split Import-Fix" in hint for hint in hints)
    assert any("Evidence:" in hint for hint in hints)


def test_llm_disabled_by_default(ctx):
    """ctx.llm present but GODSV_LLM unset → template hints, zero calls."""
    ctx.llm = FakeLLM(text='{"hint_md": "should not be used", "confidence": 1.0}')
    ctx.hooks["on_session_start"](session_id="l0")
    session = godsv_plugin._SESSIONS["l0"]
    _seed_incident(session)
    _fail_tool_call(ctx, "l0", times=2)
    assert ctx.llm.calls == []
    assert any("Recurring error" in hint for hint in session.pending)


def test_llm_synthesis_is_held_then_upgraded(ctx, monkeypatch):
    """The hook must not wait for the model: hint is held, then upgraded."""
    ctx.llm = GatedLLM(text=_synth_payload())
    monkeypatch.setenv("GODSV_LLM", "1")
    ctx.hooks["on_session_start"](session_id="l1")
    session = godsv_plugin._SESSIONS["l1"]
    _seed_incident(session)
    _fail_tool_call(ctx, "l1", times=2)
    # hook already returned; nothing delivered yet, hint held for synthesis
    assert session.pending == []
    assert len(session._held) == 1
    ctx.llm.gate.set()
    session.wait_synthesis(timeout=5)
    hint = session.take_pending()[0]
    assert "LLM-Hinweis" in hint
    assert "Verification step: pytest -q" in hint
    assert "Evidence:" in hint  # evidence block still rendered by the formatter
    assert len(ctx.llm.calls) == 1
    assert ctx.llm.calls[0]["purpose"] == "godsv:voice_synthesis"
    assert "Compose at most ONE hint" in str(ctx.llm.calls[0]["messages"])


def test_llm_failure_keeps_template_hint(ctx, monkeypatch):
    ctx.llm = FakeLLM(error=True)
    monkeypatch.setenv("GODSV_LLM", "1")
    ctx.hooks["on_session_start"](session_id="l2")
    session = godsv_plugin._SESSIONS["l2"]
    _seed_incident(session)
    _fail_tool_call(ctx, "l2", times=2)
    session.wait_synthesis(timeout=5)
    hints = session.take_pending()
    assert any("Recurring error" in hint for hint in hints)
    assert len(ctx.llm.calls) == 1  # attempted, then fell back


def test_synthesis_grace_promotes_template(ctx, monkeypatch):
    """With GODSV_LLM_GRACE=0 the held hint is delivered as template at once."""
    monkeypatch.setenv("GODSV_LLM_GRACE", "0")
    ctx.llm = GatedLLM(text=_synth_payload())
    monkeypatch.setenv("GODSV_LLM", "1")
    ctx.hooks["on_session_start"](session_id="l3")
    session = godsv_plugin._SESSIONS["l3"]
    _seed_incident(session)
    _fail_tool_call(ctx, "l3", times=2)
    hint = session.take_pending()[0]
    assert "Recurring error" in hint  # template — model never answered in time
    assert "LLM-Hinweis" not in hint
    ctx.llm.gate.set()
    session.wait_synthesis(timeout=5)


class RoutingLLM(FakeLLM):
    """FakeLLM that replies based on the prompt text (substring match)."""

    def __init__(self, routes: dict) -> None:
        super().__init__()
        self.routes = routes

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        text = str(messages[-1].get("content", ""))
        for marker, reply in self.routes.items():
            if marker in text:
                return types.SimpleNamespace(text=reply)
        return types.SimpleNamespace(text="")


def _seed_fuzzy_incident(session) -> None:
    """Incident matching by text (FTS) but not by exact signature."""
    from skaldir.incidents import Incident

    session.incidents.upsert(
        Incident(
            signature="deadbeef0000",
            title="cv_split Import-Fix (anderer Lauf)",
            root_cause="Funktion wurde umbenannt",
            resolution_steps=["Import auf split_cv umgestellt"],
            verification="pytest -q grün",
            status="active",
            confidence=0.8,
        )
    )


def _loop_tool_call(ctx, session_id, times=3):
    for _ in range(times):
        ctx.hooks["post_tool_call"](
            tool_name="terminal",
            args={"command": "pytest -q"},
            result="ok",
            session_id=session_id,
            status="ok",
        )


TRIAGE_NOT_STUCK = json.dumps(
    {"stuck": False, "category": "none", "confidence": 0.9, "search_queries": []}
)
TRIAGE_STUCK = json.dumps(
    {"stuck": True, "category": "error_loop", "confidence": 0.8, "search_queries": ["x"]}
)


def test_triage_suppresses_loop_hint_when_not_stuck(ctx, monkeypatch):
    ctx.llm = RoutingLLM({"triage stage": TRIAGE_NOT_STUCK})
    monkeypatch.setenv("GODSV_LLM", "1")
    monkeypatch.setenv("GODSV_TRIAGE", "1")
    ctx.hooks["on_session_start"](session_id="t1")
    _loop_tool_call(ctx, "t1", times=3)
    session = godsv_plugin._SESSIONS["t1"]
    session.wait_synthesis(timeout=5)
    kinds = [event.kind for event in session.store.iter_session("t1")]
    assert "hint_triage" in kinds and "hint_suppressed" in kinds
    session.flush()  # promotion at the delivery opportunity / session end
    assert session.pending == []  # suppressed hints are never delivered
    inbox = (session.state_dir / "inbox.jsonl").read_text(encoding="utf-8")
    assert '"suppressed": true' in inbox
    assert len(ctx.llm.calls) == 1  # triage ran; no synthesis call (no evidence)


def test_triage_keeps_hint_when_stuck(ctx, monkeypatch):
    ctx.llm = RoutingLLM({"triage stage": TRIAGE_STUCK})
    monkeypatch.setenv("GODSV_LLM", "1")
    monkeypatch.setenv("GODSV_TRIAGE", "1")
    ctx.hooks["on_session_start"](session_id="t2")
    _loop_tool_call(ctx, "t2", times=3)
    session = godsv_plugin._SESSIONS["t2"]
    session.wait_synthesis(timeout=5)
    hints = session.take_pending()
    assert hints and "Probable loop" in hints[0]
    kinds = [event.kind for event in session.store.iter_session("t2")]
    assert "hint_triage" in kinds and "hint_suppressed" not in kinds


def test_triage_off_by_default(ctx, monkeypatch):
    ctx.llm = RoutingLLM({"triage stage": TRIAGE_NOT_STUCK})
    monkeypatch.setenv("GODSV_LLM", "1")
    ctx.hooks["on_session_start"](session_id="t3")
    _loop_tool_call(ctx, "t3", times=3)
    session = godsv_plugin._SESSIONS["t3"]
    assert session.pending  # delivered straight away, no triage call
    assert ctx.llm.calls == []


def test_judge_drops_fuzzy_evidence_when_not_same(ctx, monkeypatch):
    ctx.llm = RoutingLLM(
        {
            "root-cause class": json.dumps(
                {"same": False, "incident_id": None, "reason": "anderes Problem", "confidence": 0.9}
            )
        }
    )
    monkeypatch.setenv("GODSV_LLM", "1")
    ctx.hooks["on_session_start"](session_id="t4")
    session = godsv_plugin._SESSIONS["t4"]
    _seed_fuzzy_incident(session)
    _fail_tool_call(ctx, "t4", times=2)
    session.wait_synthesis(timeout=5)
    hints = session.take_pending()
    assert hints
    assert "cv_split Import-Fix" not in hints[0]  # fuzzy match dropped by the judge
    assert "Evidence:" not in hints[0]
    kinds = [event.kind for event in session.store.iter_session("t4")]
    assert "evidence_judged" in kinds


def test_judge_keeps_confirmed_fuzzy_evidence(ctx, monkeypatch):
    ctx.llm = RoutingLLM(
        {
            "root-cause class": json.dumps(
                {
                    "same": True,
                    "incident_id": "incident#1",
                    "reason": "gleiche Ursache",
                    "confidence": 0.9,
                }
            ),
            "Compose at most ONE hint": json.dumps(
                {
                    "hint_md": "**LLM-Hinweis:** bekannte Ursache.",
                    "confidence": 0.9,
                    "evidence_ids": ["incident#1"],
                }
            ),
        }
    )
    monkeypatch.setenv("GODSV_LLM", "1")
    ctx.hooks["on_session_start"](session_id="t5")
    session = godsv_plugin._SESSIONS["t5"]
    _seed_fuzzy_incident(session)
    _fail_tool_call(ctx, "t5", times=2)
    session.wait_synthesis(timeout=5)
    hints = session.take_pending()
    assert hints
    assert "cv_split Import-Fix" in hints[0]  # confirmed → stays as evidence
    assert "Evidence:" in hints[0]


# -- strict completion supervisor (Goal: reject → redirect → continue) -------


def _claim_text():
    return "All tests are green and the feature works."


def _seed_edit_and_test_run(session, *, ok=True):
    session._record("file_edit", {"path": "src/app.py", "added": "x = 1\n"})
    session._record("tool_call", {"name": "terminal", "args": {"command": "pytest -q"}})
    session._record(
        "tool_result",
        {"tool": "terminal", "ok": ok, "exit_code": 0 if ok else 1, "stderr": "" if ok else "boom"},
    )


def test_strict_supervisor_rejects_even_on_later_attempts(ctx, monkeypatch, tmp_path):
    """The old one-shot nudge is gone: a rejected claim keeps the run going."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GODSV_MODE", "whisper")
    ctx.hooks["on_session_start"](session_id="sc1")
    session = godsv_plugin._SESSIONS["sc1"]
    session._record("file_edit", {"path": "src/app.py", "added": "x = 1\n"})  # code changed…
    out = ctx.hooks["pre_verify"](session_id="sc1", attempt=1, final_response=_claim_text())
    assert isinstance(out, dict) and out.get("action") == "continue", out
    assert "Continue working now" in out["message"]
    kinds = [event.kind for event in session.store.iter_session("sc1")]
    assert "completion_verdict" in kinds and "completion_rejected" in kinds


def test_strict_supervisor_verifies_with_real_evidence(ctx, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GODSV_MODE", "whisper")
    ctx.hooks["on_session_start"](session_id="sc2")
    session = godsv_plugin._SESSIONS["sc2"]
    _seed_edit_and_test_run(session, ok=True)
    out = ctx.hooks["pre_verify"](session_id="sc2", attempt=0, final_response=_claim_text())
    assert out is None, out
    assert session._completion_store().state()["state"] == "VERIFIED_COMPLETE"


def test_strict_supervisor_rejects_failed_test_run(ctx, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GODSV_MODE", "whisper")
    ctx.hooks["on_session_start"](session_id="sc3")
    session = godsv_plugin._SESSIONS["sc3"]
    _seed_edit_and_test_run(session, ok=False)
    out = ctx.hooks["pre_verify"](session_id="sc3", attempt=0, final_response=_claim_text())
    assert isinstance(out, dict) and out.get("action") == "continue"


def test_strict_supervisor_escalates_on_repeat(ctx, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GODSV_MODE", "whisper")
    ctx.hooks["on_session_start"](session_id="sc4")
    session = godsv_plugin._SESSIONS["sc4"]
    session._record("file_edit", {"path": "src/app.py", "added": "x = 1\n"})
    first = ctx.hooks["pre_verify"](session_id="sc4", attempt=0, final_response=_claim_text())
    second = ctx.hooks["pre_verify"](session_id="sc4", attempt=1, final_response=_claim_text())
    assert first and second and first["action"] == second["action"] == "continue"
    assert "STOP repeating" in second["message"]


def test_strict_supervisor_blocked_user_is_terminal(ctx, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GODSV_MODE", "whisper")
    ctx.hooks["on_session_start"](session_id="sc5")
    session = godsv_plugin._SESSIONS["sc5"]
    text = (
        "All tests are green.\n"
        "BLOCKED_USER: further steps need an owner decision before rollout."
    )
    out = ctx.hooks["pre_verify"](session_id="sc5", attempt=0, final_response=text)
    assert out is None  # honest terminal exit — the answer may stand
    assert session._completion_store().state()["state"] == "BLOCKED_USER"
    kinds = [event.kind for event in session.store.iter_session("sc5")]
    assert "completion_blocked" in kinds


def test_strict_supervisor_cap_is_honest_not_verified(ctx, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GODSV_MODE", "whisper")
    ctx.hooks["on_session_start"](session_id="sc6")
    session = godsv_plugin._SESSIONS["sc6"]
    session._STRICT_NUDGE_CAP = 2
    session._record("file_edit", {"path": "src/app.py", "added": "x = 1\n"})
    for _ in range(4):
        ctx.hooks["pre_verify"](session_id="sc6", attempt=0, final_response=_claim_text())
    kinds = [event.kind for event in session.store.iter_session("sc6")]
    assert "completion_cap_reached" in kinds
    # the cap never fakes completion
    assert session._completion_store().state()["state"] != "VERIFIED_COMPLETE"

def test_chat_goal_mention_begins_a_session_contract(ctx):
    """`@goal <text>` in a chat message lands on THIS session's contract (P1/A4)."""
    ctx.hooks["on_session_start"](session_id="g1")
    out = ctx.hooks["pre_llm_call"](
        session_id="g1", user_message="@goal Ship the bundled settings view"
    )
    session = godsv_plugin._SESSIONS["g1"]
    record = session._completion_store().state()
    assert record["goal"] == "Ship the bundled settings view"
    assert record["state"] == "WORKING"
    kinds = [event.kind for event in session.store.iter_session("g1")]
    assert "goal_set_from_chat" in kinds
    assert isinstance(out, dict) and "WORKING" in out["context"]


def test_pre_llm_call_injects_contract_context_for_the_agent(ctx):
    """Every turn with a live contract hands the main agent state + dod told."""
    ctx.hooks["on_session_start"](session_id="g2")
    session = godsv_plugin._SESSIONS["g2"]
    from skaldir.completion import DodItem

    session._completion_store().begin_goal(
        "g2",
        "Keep the pytest suite green",
        [DodItem(id="c1", text="pytest passes", blocking=True, verification="pytest")],
    )
    out = ctx.hooks["pre_llm_call"](session_id="g2", user_message="carry on")
    assert isinstance(out, dict)
    text = out["context"]
    assert "state=WORKING" in text
    assert "goal=Keep the pytest suite green" in text
    assert "0/1 blocking requirements verified" in text


def test_pre_llm_call_silent_without_contract(ctx):
    ctx.hooks["on_session_start"](session_id="g3")
    assert ctx.hooks["pre_llm_call"](session_id="g3", user_message="plain turn") is None

def test_live_block_appended_on_voice_activity(ctx):
    """Voice activity in the turn earns the live `::godsv-live` paragraph."""
    ctx.hooks["on_session_start"](session_id="lb1")
    session = godsv_plugin._SESSIONS["lb1"]
    ctx.hooks["pre_llm_call"](session_id="lb1", user_message="@goal Ship the live block")
    session._record("completion_rejected", {"attempt": 1, "status": "INCOMPLETE"})
    out = ctx.hooks["transform_llm_output"](response_text="done for now", session_id="lb1")
    assert out is not None
    assert "done for now" in out
    assert out.rstrip().endswith('::godsv-live{stage="working" sid="lb1"}')


def test_live_block_silent_without_contract_or_activity(ctx):
    ctx.hooks["on_session_start"](session_id="lb2")
    assert (
        ctx.hooks["transform_llm_output"](response_text="plain answer", session_id="lb2")
        is None
    )


def test_live_block_emits_once_per_stage(ctx):
    ctx.hooks["on_session_start"](session_id="lb3")
    session = godsv_plugin._SESSIONS["lb3"]
    ctx.hooks["pre_llm_call"](session_id="lb3", user_message="@goal Watch the stage")
    first = ctx.hooks["transform_llm_output"](response_text="a", session_id="lb3")
    assert first and "::godsv-live" in first
    # same stage, new turn without voice activity → quiet again
    session.begin_turn()
    assert (
        ctx.hooks["transform_llm_output"](response_text="b", session_id="lb3") is None
    )

def test_contract_context_teaches_the_live_block_directive(ctx):
    """The agent is told the exact paragraph that renders the live block."""
    ctx.hooks["on_session_start"](session_id="lb4")
    out = ctx.hooks["pre_llm_call"](session_id="lb4", user_message="@goal Teach the block")
    assert isinstance(out, dict) and "::godsv-live" in out["context"]

def test_contract_context_shows_trajectory_state(ctx):
    """P0-1: decisions/assumptions/non-goals ride the per-turn contract block."""
    ctx.hooks["on_session_start"](session_id="tr1")
    session = godsv_plugin._SESSIONS["tr1"]
    store = session._completion_store()
    store.begin_goal("tr1", "Trajectory goal", [], non_goals=["No redesign"])
    store.record_trajectory("decision", "Keep the existing store", reason="compact")
    out = ctx.hooks["pre_llm_call"](session_id="tr1", user_message="status please")
    assert isinstance(out, dict)
    context = out["context"]
    assert "godsv·decision: Keep the existing store" in context
    assert "godsv·non_goal: No redesign" in context
    assert "!decision:" in context  # the intake format is taught


def test_repo_root_hint_prefers_contract_task_base(tmp_path):
    """The contract's declared task_base beats the edited-files heuristic.

    Live-found 13.09.: probe scripts in /tmp made the hint pick /tmp and the
    guard ran testing tools against the wrong repo.
    """
    from skaldir.completion import CompletionStore

    session = godsv_plugin.GodsvSession.__new__(godsv_plugin.GodsvSession)
    session.session_id = "s1"
    session.state_dir = tmp_path
    repo = tmp_path / "proj"
    (repo / ".git").mkdir(parents=True)
    (repo / "README.md").write_text("x\n")
    CompletionStore(tmp_path, session_id="s1").begin_goal("s1", "goal", [], task_base=str(repo))
    assert session._repo_root_hint() == repo
