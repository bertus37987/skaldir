"""Constraint proof: no plugin hook may ever raise on broken input.

A voice/guard/pause failure must never take the agent down — Hermes calls
these hooks inline with whatever it has; our contract is silent tolerance.
"""

import godsv_plugin
import pytest


class FakeCtx:
    def __init__(self) -> None:
        self.hooks: dict = {}

    def register_hook(self, name, fn):
        self.hooks[name] = fn


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("GODSV_MODE", "whisper")
    godsv_plugin._SESSIONS.clear()
    fake = FakeCtx()
    godsv_plugin.register(fake)
    yield fake
    for session in list(godsv_plugin._SESSIONS.values()):
        try:
            session.end()
        except Exception:
            pass
    godsv_plugin._SESSIONS.clear()


class TestHookRobustness:
    def test_ten_hooks_registered(self, ctx):
        assert len(ctx.hooks) == 10

    def test_every_hook_survives_garbage(self, ctx):
        ctx.hooks["on_session_start"](session_id="g1")
        # none of these may raise — and their return is harmless on garbage
        ctx.hooks["pre_llm_call"](session_id="g1")
        ctx.hooks["on_interim_message"](text=None, session_id="g1")
        ctx.hooks["post_llm_call"](assistant_response=None, session_id="g1")
        assert ctx.hooks["transform_llm_output"](response_text=None, session_id="g1") in (None, "")
        ctx.hooks["post_tool_call"](
            tool_name=None, args=None, result=None, session_id="g1", status=None
        )
        result_obj = ctx.hooks["transform_tool_result"]
        assert result_obj(tool_name=None, result=None, session_id="g1") is None
        directive = ctx.hooks["pre_tool_call"](tool_name=None, args=None, session_id="g1")
        assert directive is None or isinstance(directive, dict)
        verdict = ctx.hooks["pre_verify"](session_id="g1", attempt=None, final_response=None)
        assert verdict is None or isinstance(verdict, dict)
        ctx.hooks["on_session_end"](session_id="g1")

    def test_hooks_do_not_raise_without_session(self, ctx):
        # hooks fired before any session start must be no-ops, never errors
        ctx.hooks["post_tool_call"](tool_name="terminal", args={}, result="x", session_id="ghost")
        ctx.hooks["post_llm_call"](assistant_response="hi", session_id="ghost")
        assert ctx.hooks["transform_llm_output"](response_text="hi", session_id="ghost") is None
        transform = ctx.hooks["transform_tool_result"]
        assert transform(tool_name="t", result="r", session_id="ghost") is None
        assert ctx.hooks["pre_tool_call"](tool_name="t", args={}, session_id="ghost") is None
        assert ctx.hooks["pre_verify"](session_id="ghost", final_response="done") is None
        ctx.hooks["on_session_end"](session_id="ghost")  # unknown session: no-op

    def test_off_mode_is_a_kill_switch(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("GODSV_MODE", "off")
        godsv_plugin._SESSIONS.clear()
        fake = FakeCtx()
        godsv_plugin.register(fake)
        fake.hooks["on_session_start"](session_id="off1")
        assert "off1" not in godsv_plugin._SESSIONS
        assert fake.hooks["pre_tool_call"](tool_name="t", args={}, session_id="off1") is None
        assert fake.hooks["transform_llm_output"](response_text="x", session_id="off1") is None
