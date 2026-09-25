"""Release features in the plugin adapter: guard channels, pause delivery,
skill consumption, contradiction, file-edit mirroring."""

import time

import godsv_plugin
import pytest

from skaldir.research import ResearchResult
from skaldir.skills import STATUS_DEMOTED, STATUS_PROMOTED, SkillCandidate


class FakeCtx:
    def __init__(self) -> None:
        self.hooks: dict = {}

    def register_hook(self, name, fn):
        self.hooks[name] = fn


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("GODSV_MODE", "whisper")
    monkeypatch.delenv("GODSV_LLM", raising=False)
    monkeypatch.delenv("GODSV_GUARD_FORCE", raising=False)
    monkeypatch.delenv("GODSV_PAUSE", raising=False)
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


def _session(ctx, session_id="r1"):
    ctx.hooks["on_session_start"](session_id=session_id)
    return godsv_plugin._SESSIONS[session_id]


def _edit(store, path="src/app.py"):
    """Simulate a write through the plugin (file_edit mirroring path)."""
    return path


class TestGuardChannels:
    def test_pre_verify_nudges_unverified_completion(self, ctx, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)  # deterministic scope: gates see no foreign repo
        session = _session(ctx)
        ctx.hooks["post_tool_call"](
            tool_name="write_file",
            args={"path": "src/app.py", "content": "def f():\n    return 1\n"},
            result="ok",
            session_id="r1",
            status="success",
        )
        verdict = ctx.hooks["pre_verify"](
            session_id="r1", attempt=0, final_response="Ich bin fertig, alles implementiert."
        )
        assert isinstance(verdict, dict) and verdict["action"] == "continue"
        assert "Continue working now" in verdict["message"]
        kinds = [event.kind for event in session.store.iter_session("r1")]
        assert "completion_verdict" in kinds and "completion_rejected" in kinds
        # the strict loop keeps rejecting: later attempts continue, never end the run
        second = ctx.hooks["pre_verify"](
            session_id="r1", attempt=1, final_response="Ich bin fertig, alles implementiert."
        )
        assert isinstance(second, dict) and second["action"] == "continue"

    def test_pre_verify_silent_when_verified(self, ctx, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        session = _session(ctx)
        ctx.hooks["post_tool_call"](
            tool_name="write_file",
            args={"path": "src/app.py", "content": "pass\n"},
            result="ok",
            session_id="r1",
            status="success",
        )
        ctx.hooks["post_tool_call"](
            tool_name="terminal",
            args={"command": "pytest -q"},
            result="3 passed",
            session_id="r1",
            status="success",
        )
        assert (
            ctx.hooks["pre_verify"](
                session_id="r1",
                attempt=0,
                final_response="All tests are green and everything works.",
            )
            is None
        )
        # the strict gate verified against executed evidence — independently
        assert session._completion_store().state()["state"] == "VERIFIED_COMPLETE"

    def test_transform_llm_output_adds_caveat_footer(self, ctx):
        _session(ctx)
        ctx.hooks["post_tool_call"](
            tool_name="write_file",
            args={"path": "src/app.py", "content": "x = 1\n"},
            result="ok",
            session_id="r1",
            status="success",
        )
        out = ctx.hooks["transform_llm_output"](
            response_text="Alles ist fertig und funktioniert.", session_id="r1"
        )
        assert isinstance(out, str)
        assert out.startswith("Alles ist fertig")
        assert "[voice·guard]" in out

    def test_transform_llm_output_untouched_when_ok(self, ctx):
        _session(ctx)
        out = ctx.hooks["transform_llm_output"](
            response_text="Ich habe nachgesehen und berichte dir gleich.", session_id="r1"
        )
        assert out is None

    def test_guard_force_nudges_once(self, ctx, monkeypatch):
        monkeypatch.setenv("GODSV_GUARD_FORCE", "1")
        _session(ctx)
        verdict = ctx.hooks["pre_verify"](
            session_id="r1", attempt=0, final_response="Keine Aussage."
        )
        assert isinstance(verdict, dict) and verdict["action"] == "continue"
        assert "Guard channel test" in verdict["message"]

    def test_shadow_mode_records_but_never_steers(self, ctx, monkeypatch, tmp_path):
        monkeypatch.setenv("GODSV_MODE", "shadow")
        monkeypatch.chdir(tmp_path)
        session = _session(ctx)
        ctx.hooks["post_tool_call"](
            tool_name="write_file",
            args={"path": "src/app.py", "content": "x = 1\n"},
            result="ok",
            session_id="r1",
            status="success",
        )
        text = "All tests are green and everything works."
        assert (
            ctx.hooks["pre_verify"](session_id="r1", attempt=0, final_response=text) is None
        )
        assert (
            ctx.hooks["transform_llm_output"](response_text=text, session_id="r1") is None
        )
        kinds = [event.kind for event in session.store.iter_session("r1")]
        assert "completion_verdict" in kinds  # observed + audited, but no nudges
        assert "guard_nudge" not in kinds and "guard_footer" not in kinds
        assert "completion_rejected" not in kinds


class TestFileEditMirroring:
    def test_write_file_becomes_file_edit(self, ctx):
        session = _session(ctx)
        ctx.hooks["post_tool_call"](
            tool_name="write_file",
            args={"path": "src/slop.py", "content": "x = 1  # TODO: fix\n"},
            result="ok",
            session_id="r1",
            status="success",
        )
        edits = [e for e in session.store.iter_session("r1") if e.kind == "file_edit"]
        assert edits and edits[0].payload["path"] == "src/slop.py"
        hints = session.pending
        assert any("placeholder" in hint for hint in hints)

    def test_patch_maps_new_string(self, ctx):
        session = _session(ctx)
        ctx.hooks["post_tool_call"](
            tool_name="patch",
            args={"path": "src/app.py", "old_string": "a", "new_string": "assert True\n"},
            result="ok",
            session_id="r1",
            status="success",
        )
        edits = [e for e in session.store.iter_session("r1") if e.kind == "file_edit"]
        assert edits and "assert True" in edits[0].payload["added"]


class TestPauseInPlugin:
    class _Runner:
        def run(self, *, session_id, base_step, trigger_kind, trigger_fp, problem):
            return ResearchResult(
                research_id="research-x",
                session_id=session_id,
                base_step=base_step,
                trigger_kind=trigger_kind,
                trigger_fp=trigger_fp,
                problem=problem,
                direction="Ansatz wechseln",
                avoid="Wiederholung",
                uncertainty="keine",
                status="ok",
            )

    def _install_fast_research(self, session):
        assert session._pause is not None
        session._pause.research = self._Runner()

    def test_loop_triggers_pause_and_blocks_once(self, ctx, monkeypatch):
        monkeypatch.setenv("GODSV_PAUSE_WAIT", "1")
        session = _session(ctx)
        self._install_fast_research(session)
        calls = {
            "tool_name": "terminal",
            "args": {"command": "pytest -q"},
            "result": "boom",
            "status": "error",
            "error_type": "ImportError",
            "error_message": "cannot import name 'cv_split'",
        }
        # 3 identical failing calls → loop signal; a 4th pass pushes count ≥2
        for _ in range(6):
            ctx.hooks["post_tool_call"](**{**calls, "session_id": "r1"})
        assert session._pause is not None
        lease = session._pause.store.current("r1")
        assert lease is not None, "pause should have been requested"
        # research is instant → ready; the next tool call gets blocked once
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            lease = session._pause.store.get(lease["id"])
            if lease and lease["status"] == "ready":
                break
            time.sleep(0.02)
        directive = ctx.hooks["pre_tool_call"](tool_name="terminal", args={}, session_id="r1")
        assert isinstance(directive, dict) and directive["action"] == "block"
        assert "voice·pause" in directive["message"]
        assert ctx.hooks["pre_tool_call"](tool_name="terminal", args={}, session_id="r1") is None
        kinds = [event.kind for event in session.store.iter_session("r1")]
        assert "pause_delivered" in kinds

    def test_contradiction_triggers_pause(self, ctx, monkeypatch):
        monkeypatch.setenv("GODSV_PAUSE_WAIT", "1")
        session = _session(ctx)
        self._install_fast_research(session)
        calls = {
            "tool_name": "terminal",
            "args": {"command": "python app.py"},
            "result": "boom",
            "status": "error",
            "error_type": "ImportError",
            "error_message": "cannot import name 'cv_split'",
        }
        ctx.hooks["post_tool_call"](**{**calls, "session_id": "r1"})  # error known
        ctx.hooks["post_llm_call"](assistant_response="Alles läuft jetzt rund.", session_id="r1")
        ctx.hooks["post_tool_call"](**{**calls, "session_id": "r1"})  # same error again
        kinds = [event.kind for event in session.store.iter_session("r1")]
        assert "claim" in kinds and "contradiction" in kinds
        assert "pause_requested" in kinds


class TestSkillConsumption:
    def _seed_skill(self, session, name="workflow-a-b", status=STATUS_PROMOTED):
        session.skills.upsert(
            SkillCandidate(
                name=name,
                title=f"Workflow {name}",
                tool_chain=["read_file", "patch"],
                occurrences=5,
                sessions=["s1", "s2"],
                status=status,
            )
        )

    def test_promoted_skill_is_surfaced_once(self, ctx):
        session = _session(ctx)
        self._seed_skill(session)
        ctx.hooks["pre_tool_call"](tool_name="read_file", args={}, session_id="r1")
        ctx.hooks["pre_tool_call"](tool_name="patch", args={}, session_id="r1")
        kinds = [event.kind for event in session.store.iter_session("r1")]
        assert "skill_consumed" in kinds
        assert any("Proven procedure available" in hint for hint in session.pending)
        # once per session, not on every call
        before = len([k for k in kinds if k == "skill_consumed"])
        ctx.hooks["pre_tool_call"](tool_name="read_file", args={}, session_id="r1")
        ctx.hooks["pre_tool_call"](tool_name="patch", args={}, session_id="r1")
        kinds2 = [event.kind for event in session.store.iter_session("r1")]
        assert len([k for k in kinds2 if k == "skill_consumed"]) == before

    def test_demoted_skill_is_never_surfaced(self, ctx):
        session = _session(ctx)
        self._seed_skill(session, status=STATUS_DEMOTED)
        ctx.hooks["pre_tool_call"](tool_name="read_file", args={}, session_id="r1")
        ctx.hooks["pre_tool_call"](tool_name="patch", args={}, session_id="r1")
        kinds = [event.kind for event in session.store.iter_session("r1")]
        assert "skill_consumed" not in kinds
        assert session.pending == []
