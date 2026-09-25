"""§1.3/§1.4 host wiring — the plugin's model boundary drives the live surface.

These tests drive the REAL plugin hooks with a fake host context, a real
registry (tmp ``HERMES_HOME``) and the real builder pipeline: no model call, no
network, no host roundtrip (that separation is the point — see
``docs/TOOLS-V2-HOST-WIRING.md`` §7/§8).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import godsv_plugin
import pytest

from skaldir.capability import project_id_for
from skaldir.toolbuilder import ToolBuilder, ToolBuildRequest

CAPABILITIES = {
    "tool_a": "Inspect a blender project and screenshot the viewport",
    "tool_b": "Convert money between currencies via provider rates",
    "tool_c": "Extract the plain text of a document for reading",
    "tool_d": "Rasterize a font specimen sheet at a fixed size",
}


class _Handle:
    def __init__(self, ctx, name):
        self._ctx = ctx
        self._name = name

    def dispose(self):
        self._ctx.disposed.append(self._name)
        self._ctx.tools.pop(self._name, None)


class FakeCtx:
    """Minimal host context: hooks plus the plugin tool registration surface."""

    def __init__(self, *, accept: bool = True):
        self.hooks: dict = {}
        self.tools: dict = {}
        self.disposed: list = []
        self.accept = accept

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        if not self.accept:
            return None
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, **kwargs}
        return _Handle(self, name)


@pytest.fixture
def live_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("GODSV_MODE", "shadow")
    monkeypatch.setenv("GODSV_MCP_AUTOLINK", "0")
    monkeypatch.setattr(godsv_plugin, "LIVE_SYNC_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(godsv_plugin, "_LINK_ATTEMPTED", False)
    godsv_plugin._SESSIONS.clear()
    godsv_plugin._LIVE_SURFACES.clear()
    ctx = FakeCtx()
    godsv_plugin.register(ctx)
    yield ctx, tmp_path / "godsv"
    for session in list(godsv_plugin._SESSIONS.values()):
        try:
            session.end()
        except Exception:
            pass
    godsv_plugin._SESSIONS.clear()


def _build(runtime: Path, name: str, project_id: str) -> dict:
    return ToolBuilder(runtime).build(
        ToolBuildRequest(
            capability=CAPABILITIES[name],
            name=name,
            project_id=project_id,
            source_code=(
                "def main(project_path: str = '') -> dict:\n"
                "    return {'project': project_path, 'tool': 'live'}\n"
            ),
            module=name,
            entrypoint="main",
            sample_input={"project_path": "/tmp/x.blend"},
        )
    )


def _live_names(ctx) -> set:
    """Registered names of the live surface — the pinned report tool is not one."""
    return {name for name in ctx.tools if name != godsv_plugin.LIVE_REPORT_TOOL}


def _session_project_id(ctx, session_id: str = "s1") -> str:
    """The project id the plugin itself resolves — build against the same one."""
    ctx.hooks["on_session_start"](session_id=session_id)
    session = godsv_plugin._SESSIONS[session_id]
    resolved = godsv_plugin._live_surface(session).project_id()
    return resolved or project_id_for(Path.cwd())


def test_the_boundary_registers_a_verified_capability_as_a_native_tool(live_env):
    ctx, runtime = live_env
    project_id = _session_project_id(ctx)
    build = _build(runtime, "tool_a", project_id)
    assert build["ok"] is True, build

    ctx.hooks["pre_llm_call"](session_id="s1", user_message="go")

    assert "tool_a" in ctx.tools
    entry = ctx.tools["tool_a"]
    assert entry["toolset"] == godsv_plugin.LIVE_TOOLSET
    assert entry["schema"]["name"] == "tool_a"
    # the handler is the real call path: a JSON string back, through the wrapper
    payload = json.loads(entry["handler"]({"project_path": "/tmp/live.blend"}))
    assert payload["project"] == "/tmp/live.blend"
    assert payload["tool"] == "live"


def test_a_capability_built_after_the_last_boundary_appears_at_the_next_one(live_env):
    ctx, runtime = live_env
    project_id = _session_project_id(ctx)
    ctx.hooks["pre_llm_call"](session_id="s1", user_message="first")
    assert _live_names(ctx) == set()

    build = _build(runtime, "tool_b", project_id)
    assert build["ok"] is True, build
    ctx.hooks["pre_llm_call"](session_id="s1", user_message="second")

    assert "tool_b" in ctx.tools, godsv_plugin._SESSIONS["s1"].live.last_report


def test_the_surface_keeps_the_newest_three_and_deregisters_the_oldest(live_env):
    ctx, runtime = live_env
    project_id = _session_project_id(ctx)
    for name in ("tool_a", "tool_b", "tool_c"):
        assert _build(runtime, name, project_id)["ok"] is True
    ctx.hooks["pre_llm_call"](session_id="s1", user_message="one")
    assert sorted(_live_names(ctx)) == ["tool_a", "tool_b", "tool_c"]

    assert _build(runtime, "tool_d", project_id)["ok"] is True
    ctx.hooks["pre_llm_call"](session_id="s1", user_message="two")

    assert sorted(_live_names(ctx)) == ["tool_b", "tool_c", "tool_d"]
    assert ctx.disposed == ["tool_a"]  # the oldest left the hot set


def test_the_boundary_survives_a_broken_registry(live_env, monkeypatch):
    ctx, runtime = live_env
    _session_project_id(ctx)

    def boom(self, **kwargs):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr("godsv.liveinsert.CapabilityIndex.sync_registry", boom)
    result = ctx.hooks["pre_llm_call"](session_id="s1", user_message="go")
    assert result is None or isinstance(result, dict)  # never raises
    assert _live_names(ctx) == set()


def test_a_refused_registration_is_not_tracked_as_live(live_env):
    ctx, runtime = live_env
    ctx.accept = False
    project_id = _session_project_id(ctx)
    assert _build(runtime, "tool_a", project_id)["ok"] is True
    ctx.hooks["pre_llm_call"](session_id="s1", user_message="go")
    assert _live_names(ctx) == set()
    assert godsv_plugin._SESSIONS["s1"].live.registered == {}


def test_autolink_runs_once_and_only_when_the_entry_is_not_matching(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("GODSV_MODE", "shadow")
    monkeypatch.delenv("GODSV_MCP_AUTOLINK", raising=False)
    monkeypatch.setattr(godsv_plugin, "_LINK_ATTEMPTED", False)
    calls: list = []

    class _Report(dict):
        pass

    monkeypatch.setattr(
        "godsv.mcp_link.link_state",
        lambda **kwargs: {"matches": False, "reason": "not linked"},
    )
    monkeypatch.setattr(
        "godsv.mcp_link.ensure_linked",
        lambda **kwargs: calls.append(kwargs) or _Report(action="added", reason=""),
    )
    godsv_plugin._SESSIONS.clear()
    ctx = FakeCtx()
    godsv_plugin.register(ctx)
    try:
        ctx.hooks["on_session_start"](session_id="s2")
        ctx.hooks["on_session_start"](session_id="s2")
        deadline = time.monotonic() + 5.0
        while not calls and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(calls) == 1  # once per process, and only when not matching
    finally:
        godsv_plugin._SESSIONS.clear()
        monkeypatch.setattr(godsv_plugin, "_LINK_ATTEMPTED", False)


def test_autolink_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("GODSV_MODE", "shadow")
    monkeypatch.setenv("GODSV_MCP_AUTOLINK", "0")
    monkeypatch.setattr(godsv_plugin, "_LINK_ATTEMPTED", False)
    called: list = []
    monkeypatch.setattr("godsv.mcp_link.ensure_linked", lambda **kwargs: called.append(kwargs))
    godsv_plugin._SESSIONS.clear()
    ctx = FakeCtx()
    godsv_plugin.register(ctx)
    try:
        ctx.hooks["on_session_start"](session_id="s3")
        time.sleep(0.2)
        assert called == []
        assert godsv_plugin._LINK_ATTEMPTED is False  # not even claimed
    finally:
        godsv_plugin._SESSIONS.clear()


def test_register_pins_the_live_toolset_with_one_stable_tool(live_env):
    """The toolset key must exist at agent-build time (live-found 14.09.)."""
    ctx, _runtime = live_env
    assert godsv_plugin.LIVE_REPORT_TOOL in ctx.tools
    entry = ctx.tools[godsv_plugin.LIVE_REPORT_TOOL]
    assert entry["toolset"] == godsv_plugin.LIVE_TOOLSET
    payload = json.loads(entry["handler"]({}))
    assert payload["toolset"] == godsv_plugin.LIVE_TOOLSET
    assert payload["sessions"] == {}


def test_the_report_tool_renders_the_current_surface(live_env):
    ctx, runtime = live_env
    project_id = _session_project_id(ctx)
    assert _build(runtime, "tool_a", project_id)["ok"] is True
    ctx.hooks["pre_llm_call"](session_id="s1", user_message="go")

    payload = json.loads(ctx.tools[godsv_plugin.LIVE_REPORT_TOOL]["handler"]({}))
    surface = payload["sessions"]["s1"]
    assert surface["live"] == ["tool_a"]
    assert surface["hot"] == ["tool_a"]
    assert surface["boundary"] == "pre_llm_call"


def test_the_surface_survives_a_recreated_session_object(live_env):
    """Live-found 14.09.: the session object can be dropped between two turns."""
    ctx, runtime = live_env
    project_id = _session_project_id(ctx)
    assert _build(runtime, "tool_a", project_id)["ok"] is True
    ctx.hooks["pre_llm_call"](session_id="s1", user_message="one")
    assert sorted(_live_names(ctx)) == ["tool_a"]

    godsv_plugin._SESSIONS.clear()  # the host drops the session object
    assert _build(runtime, "tool_b", project_id)["ok"] is True
    ctx.hooks["pre_llm_call"](session_id="s1", user_message="two")

    # the new capability is added; the earlier one is NOT re-inserted/duplicated
    assert sorted(_live_names(ctx)) == ["tool_a", "tool_b"]
    assert ctx.disposed == []
