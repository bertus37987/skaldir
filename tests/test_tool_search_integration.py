"""Integration: tool_search finds a tool and it can actually be called.

P0 gap: tool_search returned tool names but the model couldn't call them.
This test pins the full path: discover → schema → dispatch → result.

Repaired 2026-09-25 after the transfer: three of these tests were written against an
older contract and had gone silently stale (or outright broken) before the migration —
measured at the initial transfer commit, `tests/test_tool_search_integration.py` failed
4 of 5. They are repaired against the *real* signatures, not deleted:

* ``tool_search`` answers with a ``results`` list (plus ``build_candidates``), not a
  ``matches`` key. Two tests iterated ``result.get("matches", [])`` — a loop over
  nothing, which passes without asserting a single thing.
* ``ToolContract`` takes ``title``/``module``/``permission``/``truncation``/``timeout_s``;
  the old ``permissions=[...]`` keyword raised ``TypeError``.
* ``tools_core.registry.register`` registers the **whole core toolset** for a runtime
  (``register(runtime, *, db=…)``); it does not take one contract and an implementation.
"""
from __future__ import annotations

import pytest

from skaldir.agent_loop import tool_set_for
from skaldir.agent_toolset import build_agent_toolset, tool_search_tool


def _registered_runtime(tmp_path):
    """A runtime with the core toolset actually registered, so search has something to find."""
    from skaldir.tools_core.registry import register

    runtime = tmp_path / "runtime"
    result = register(str(runtime))
    assert isinstance(result, dict)
    return runtime


def test_tool_search_result_shape_has_needed_fields(tmp_path):
    """Every discovered row needs at least id/name and a description.

    Measured 2026-09-25 on a freshly registered runtime: ``tool_search`` separates two
    surfaces — ``results`` (verified, selectable capabilities) and ``build_candidates``
    (found, but "no passing validation evidence — build candidate only"). A freshly
    registered core toolset lands in the second one, so this asserts the shape of whatever
    the search actually returned instead of demanding one particular surface be populated.
    """
    runtime = _registered_runtime(tmp_path)
    search = tool_search_tool(runtime=str(runtime))
    result = search.handler(need="file", limit=5)
    assert "results" in result and "build_candidates" in result, sorted(result)
    rows = list(result.get("results", [])) + list(result.get("build_candidates", []))
    assert rows, f"a populated registry must name something: {sorted(result)}"
    for match in rows:
        assert "id" in match or "tool_id" in match or "name" in match, sorted(match)
        # a description is what lets the model decide to call it at all
        assert "effect" in match or "description" in match, sorted(match)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "OPEN PRODUCT GAP (the P0 this file was written for): tool_search names "
        "capabilities but carries no `input_schema`, so the model is told a tool exists and "
        "is not told how to call it. Measured 2026-09-25 on a freshly registered core "
        "toolset: build_candidates[i] = {name, capability_id, version, scope, surface, "
        "state, selectable, executable, build_candidate, score, reasons, limits, unknown, "
        "components, preconditions, validation, effect} — no input_schema, no output_schema. "
        "strict=True: the day someone adds the schema this test FAILS and must be upgraded "
        "to assert it positively."
    ),
)
def test_tool_search_gives_the_model_a_call_contract(tmp_path):
    """tool_search must return input_schema so the model knows how to call the tool."""
    runtime = _registered_runtime(tmp_path)
    search = tool_search_tool(runtime=str(runtime))
    result = search.handler(need="read a file", limit=3)
    rows = list(result.get("results", [])) + list(result.get("build_candidates", []))
    assert rows, "nothing found"
    assert all("input_schema" in row for row in rows), [
        sorted(row) for row in rows
    ]


def test_registry_tools_are_callable_as_agent_tools(tmp_path):
    """Registry tools must appear as AgentTool with a callable handler."""
    runtime = _registered_runtime(tmp_path)

    tools = tool_set_for(workspace=str(tmp_path), kind="both", runtime=str(runtime))
    names = [t.name for t in tools.tools]
    # the core toolset is registered, so it must show up alongside the builtin ones
    assert len(tools.tools) > 0
    assert any(name not in {"tool_search", "knowledge_search", "knowledge_read"}
               for name in names), names
    for t in tools.tools:
        assert callable(t.handler), t.name
        assert t.name, "every agent tool carries a name the model can call"


def test_agent_toolset_contains_knowledge_and_search_tools(tmp_path):
    """build_agent_toolset must expose tool_search, knowledge_search, knowledge_read."""
    toolset = build_agent_toolset(workspace=str(tmp_path), kind="both", runtime=str(tmp_path))
    names = {t.name for t in toolset.tools}
    assert "tool_search" in names
    assert "knowledge_search" in names
    assert "knowledge_read" in names


def test_knowledge_search_tool_is_callable(tmp_path):
    """knowledge_search must return a result dict when called."""
    toolset = build_agent_toolset(workspace=str(tmp_path), kind="both", runtime=str(tmp_path))
    ks = next(t for t in toolset.tools if t.name == "knowledge_search")
    result = ks.handler(query="test", limit=3)
    assert isinstance(result, dict)
    assert "matches" in result


def test_the_registry_toolset_needs_no_caller_supplied_store(tmp_path):
    """`store=None` is a usable default, not a trap (it used to raise).

    Regression guard for the repair above: ``build_agent_toolset(..., kind="both")`` and
    ``tool_set_for(..., kind="both")`` both accept the documented ``store=None`` default
    and resolve ``<runtime>/tools.db`` themselves.
    """
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    # `build_agent_toolset` is the assembly that adds the discovery/knowledge trio on top
    # of the selection; `tool_set_for` is the narrower loop surface (builtin + registry
    # only). Both must accept store=None without raising.
    assembled = build_agent_toolset(workspace=str(tmp_path), kind="both",
                                    runtime=str(runtime))
    assert {t.name for t in assembled.tools} >= {"tool_search", "knowledge_search",
                                                 "knowledge_read"}

    loop_set = tool_set_for(workspace=str(tmp_path), kind="both", runtime=str(runtime))
    assert loop_set.tools, "kind='both' must yield tools without a caller-supplied store"
    assert all(callable(t.handler) for t in loop_set.tools)
