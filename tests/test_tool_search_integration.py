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

from skaldir.agent_loop import AgentLoopError, tool_set_for
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


def test_tool_search_gives_the_model_a_call_contract(tmp_path):
    """tool_search must return input_schema so the model knows how to call the tool.

    This is the P0 the file was written for: "tool_search returned tool names but the
    model couldn't call them." It was still true on 2026-09-25 — the rows carried
    ``name``/``effect``/``limits`` and no argument shape at all, so a discovery hit was
    not actionable.

    Fixed by carrying the typed contract through: ``capability_mining._typed_contract``
    reads the descriptor the manifest points at (``contract: <path>``) and
    ``discovery._public_row`` puts ``input_schema``/``output_schema`` on every row.
    Nothing is invented — a descriptor without a schema omits the key.
    """
    runtime = _registered_runtime(tmp_path)
    search = tool_search_tool(runtime=str(runtime))
    result = search.handler(need="read a file", limit=3)
    rows = list(result.get("results", [])) + list(result.get("build_candidates", []))
    assert rows, "nothing found"

    for row in rows:
        assert "input_schema" in row, sorted(row)
        schema = row["input_schema"]
        assert isinstance(schema, dict), row["name"]
        # a call contract names its arguments; `{}` would be no contract at all
        assert schema.get("properties") or schema.get("required"), (row["name"], schema)

    # the point of the contract is that a call can be *made*: read_file's arguments are
    # the ones its descriptor declares, not a shape derived from its name
    read_row = next(r for r in rows if r["name"] == "read_file")
    declared = set(read_row["input_schema"].get("properties", {}))
    assert {"path", "limit", "offset"} <= declared, sorted(declared)


def test_the_call_contract_is_absent_rather_than_invented_when_none_is_declared(tmp_path):
    """No descriptor, no schema key — never a fabricated argument shape."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    from skaldir.capability_mining import contract_metadata

    payload = contract_metadata("bare_tool", {"name": "bare_tool", "effect": "does a thing"})
    assert "input_schema" not in payload
    assert "output_schema" not in payload

    # and the search row follows the same rule
    from skaldir.discovery import _public_row

    row = _public_row({"name": "bare_tool", "meta": payload, "state": "build_candidate"},
                      executable=False)
    assert "input_schema" not in row
    assert "output_schema" not in row
    assert row["effect"] == "does a thing"


def test_registry_tools_are_callable_as_agent_tools(tmp_path):
    """Registry tools must appear as AgentTool with a callable handler."""
    runtime = _registered_runtime(tmp_path)

    # `tool_set_for` is the loop surface and refuses to invent a store (see
    # test_the_loop_surface_still_refuses_to_invent_a_store) — hand it the runtime's own.
    from skaldir.tools import ToolStore

    tools = tool_set_for(workspace=str(tmp_path), kind="both", runtime=str(runtime),
                         store=ToolStore(str(runtime / "tools.db")))
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


def test_build_agent_toolset_resolves_the_store_it_was_not_given(tmp_path):
    """`build_agent_toolset(store=None)` resolves `<runtime>/tools.db` — it used to raise.

    Regression guard for the repair: the signature advertises ``store: ToolStore | None =
    None`` and two tests here called it exactly like that, so the default had to become
    usable. ``skaldir.agent_toolset._store_for`` already knew how; the two tool-set
    builders simply did not ask it.
    """
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    assembled = build_agent_toolset(workspace=str(tmp_path), kind="both",
                                    runtime=str(runtime))
    assert {t.name for t in assembled.tools} >= {"tool_search", "knowledge_search",
                                                 "knowledge_read"}
    assert all(callable(t.handler) for t in assembled.tools)


def test_the_loop_surface_still_refuses_to_invent_a_store(tmp_path):
    """The opposite contract, on purpose: `tool_set_for` is the loop surface.

    ``tests/test_agent_loop.py::test_the_registry_tool_set_mixes_builtin_and_registered_tools``
    pins ``tool_set_for(kind="registry", store=None)`` as an ``AgentLoopError`` — a caller
    bug must fail loudly there rather than quietly opening a stray ``tools.db``. The two
    surfaces deliberately differ; this test stops the next person from "unifying" them.
    """
    with pytest.raises(AgentLoopError):
        tool_set_for(workspace=str(tmp_path), kind="registry", store=None)
    with pytest.raises(AgentLoopError):
        tool_set_for(workspace=str(tmp_path), kind="both", store=None)

    # and the documented happy path still works
    from skaldir.tools import ToolStore

    store = ToolStore(str(tmp_path / "tools.db"))
    loop_set = tool_set_for(workspace=str(tmp_path), kind="both", store=store)
    assert loop_set.tools and all(callable(t.handler) for t in loop_set.tools)
