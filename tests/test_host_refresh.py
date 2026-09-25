"""Host-Overlay `patch_tool_refresh.py`: Anker-Sicherheit + echte Refresh-Semantik.

Das Overlay darf den geteilten Hermes-Checkout nie halb patchen: es ist
idempotent, bricht bei fehlendem Anker laut ab und schreibt nichts. Der
Verhaltenstest führt die GEPATCHTE Funktion mit Stubs aus und pinnt beide
Richtungen: Registry bewegt => Refresh, unverändert ohne MCP => kein Refresh.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PATCH = REPO / "host-overlay" / "patch_tool_refresh.py"
HOST_TURN_CONTEXT = Path.home() / ".hermes" / "hermes-agent" / "agent" / "turn_context.py"

HEADER = '''"""Fixture stand-in for agent/turn_context.py (host overlay test)."""

from __future__ import annotations

import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

'''


def _load_patch_module():
    spec = importlib.util.spec_from_file_location("godsv_patch_tool_refresh", PATCH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def patch_module():
    return _load_patch_module()


@pytest.fixture
def host_tree(tmp_path, patch_module):
    """A minimal turn_context.py carrying the real anchor verbatim."""
    tree = tmp_path / "host"
    target = tree / "agent" / "turn_context.py"
    target.parent.mkdir(parents=True)
    target.write_text(HEADER + patch_module.OLD + "\n", encoding="utf-8")
    return tree, target


def test_the_anchor_matches_and_the_result_parses(patch_module, host_tree):
    tree, target = host_tree
    assert patch_module.patch(tree) == 0
    text = target.read_text(encoding="utf-8")
    assert patch_module.MARKER in text
    assert patch_module.OLD not in text
    ast.parse(text)  # a broken replacement literal would fail here, not at build time
    assert "_gv_trace_tool_refresh" in text


def test_patching_twice_changes_nothing(patch_module, host_tree):
    tree, target = host_tree
    assert patch_module.patch(tree) == 0
    after_first = target.read_text(encoding="utf-8")
    assert patch_module.patch(tree) == 0
    assert target.read_text(encoding="utf-8") == after_first


def test_check_mode_never_writes(patch_module, host_tree):
    tree, target = host_tree
    before = target.read_text(encoding="utf-8")
    assert patch_module.patch(tree, check=True) == 0
    assert target.read_text(encoding="utf-8") == before
    assert not (target.with_suffix(".py.gv-backup")).exists()


def test_a_missing_anchor_fails_loudly_and_writes_nothing(patch_module, tmp_path):
    tree = tmp_path / "host"
    target = tree / "agent" / "turn_context.py"
    target.parent.mkdir(parents=True)
    target.write_text("# some other Hermes version\n", encoding="utf-8")
    assert patch_module.patch(tree) == 1
    assert target.read_text(encoding="utf-8") == "# some other Hermes version\n"


def _load_patched(patch_module, host_tree) -> types.ModuleType:
    tree, target = host_tree
    assert patch_module.patch(tree) == 0
    module = types.ModuleType("gv_fixture_turn_context")
    exec(compile(target.read_text(encoding="utf-8"), str(target), "exec"), module.__dict__)
    return module


class _Agent:
    def __init__(self, generation: int, mcp_present: bool = False):
        self._tool_snapshot_generation = generation
        self.session_id = "s-refresh"
        self.tools: list = []


@pytest.fixture
def stub_registry(monkeypatch):
    """tools.registry / tools.mcp_tool_agent / tools.mcp_tool stubs for the exec'd code."""
    calls: list = []
    registry = types.SimpleNamespace(_generation=10)
    registry_mod = types.ModuleType("tools.registry")
    registry_mod.registry = registry
    agent_mod = types.ModuleType("tools.mcp_tool_agent")
    agent_mod.refresh_agent_mcp_tools = lambda agent, **kwargs: (
        calls.append(kwargs) or {"added_tool"}
    )
    discovery_mod = types.ModuleType("tools.mcp_tool_discovery")
    discovery_mod.has_registered_mcp_tools = lambda: True
    monkeypatch.setitem(sys.modules, "tools", types.ModuleType("tools"))
    monkeypatch.setitem(sys.modules, "tools.registry", registry_mod)
    monkeypatch.setitem(sys.modules, "tools.mcp_tool_agent", agent_mod)
    monkeypatch.setitem(sys.modules, "tools.mcp_tool_discovery", discovery_mod)
    monkeypatch.delitem(sys.modules, "tools.mcp_tool", raising=False)
    return calls, registry


def test_a_moved_registry_generation_triggers_the_refresh(
    patch_module, host_tree, stub_registry, monkeypatch, tmp_path
):
    module = _load_patched(patch_module, host_tree)
    calls, registry = stub_registry
    trace = tmp_path / "turn.trace"
    monkeypatch.setenv("HERMES_TURN_TRACE", str(trace))

    agent = _Agent(generation=registry._generation - 1)  # registry moved since the snapshot
    module._refresh_mcp_tools_between_turns(agent)

    assert len(calls) == 1
    assert calls[0]["preserve_prefix"] is True
    assert "gv_tool_refresh" in trace.read_text(encoding="utf-8")


def test_an_unchanged_registry_without_mcp_stays_quiet(
    patch_module, host_tree, stub_registry
):
    module = _load_patched(patch_module, host_tree)
    calls, registry = stub_registry
    agent = _Agent(generation=registry._generation)  # nothing moved, no MCP imported
    module._refresh_mcp_tools_between_turns(agent)
    assert calls == []


def test_the_original_mcp_path_still_refreshes(
    patch_module, host_tree, stub_registry, monkeypatch
):
    module = _load_patched(patch_module, host_tree)
    calls, registry = stub_registry
    monkeypatch.setitem(sys.modules, "tools.mcp_tool", types.ModuleType("tools.mcp_tool"))
    agent = _Agent(generation=registry._generation)
    module._refresh_mcp_tools_between_turns(agent)
    assert len(calls) == 1


def test_skip_mcp_refresh_still_opts_out(patch_module, host_tree, stub_registry):
    module = _load_patched(patch_module, host_tree)
    calls, registry = stub_registry
    agent = _Agent(generation=registry._generation - 1)
    agent._skip_mcp_refresh = True
    module._refresh_mcp_tools_between_turns(agent)
    assert calls == []


@pytest.mark.skipif(not HOST_TURN_CONTEXT.is_file(), reason="host checkout not present")
def test_the_anchor_resolves_on_the_real_host_checkout(patch_module):
    """--check must succeed on the real file: anchor matches OR marker already applied."""
    target = HOST_TURN_CONTEXT.parent.parent
    assert patch_module.patch(target, check=True) == 0
