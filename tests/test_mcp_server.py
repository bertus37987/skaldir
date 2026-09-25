"""MCP server export: only approved python tools, in-memory fastmcp client."""

import asyncio

import pytest

from skaldir.mcpserver import _wrapper_for, approved_python_specs, build_server
from skaldir.tools import STATUS_APPROVED, ToolSpec, ToolStore

pytest.importorskip("fastmcp", reason="MCP-Tests brauchen das Extra: uv pip install 'godsv[mcp]'")

MODULE = "def echo(text: str = '') -> dict:\n    return {'echo': text}\n"


@pytest.fixture
def store(tmp_path, monkeypatch):
    module_path = tmp_path / "mcptool.py"
    module_path.write_text(MODULE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    handle = ToolStore(tmp_path / "tools.db")
    handle.upsert(
        ToolSpec(
            name="echo_tool",
            title="Echo",
            description="Echo text back",
            module="mcptool",
            entrypoint="echo",
            status=STATUS_APPROVED,
        )
    )
    yield handle
    handle.close()


async def _list_tool_names(server) -> set[str]:
    from fastmcp import Client

    async with Client(server) as client:
        tools = await client.list_tools()
    return {tool.name for tool in tools}


async def _call(server, name: str, arguments: dict):
    from fastmcp import Client

    async with Client(server) as client:
        return await client.call_tool(name, arguments)


def test_only_approved_python_specs_are_exposed(store):
    store.upsert(ToolSpec(name="draft_tool", title="Draft", module="mcptool", entrypoint="echo"))
    names = asyncio.run(_list_tool_names(build_server(store)))
    assert "echo_tool" in names
    assert "draft_tool" not in names


def test_build_server_registers_approved_tool(store):
    assert [spec.name for spec in approved_python_specs(store)] == ["echo_tool"]
    names = asyncio.run(_list_tool_names(build_server(store)))
    assert names == {"echo_tool"}


def test_call_tool_roundtrip(store):
    result = asyncio.run(
        _call(build_server(store), "echo_tool", {"arguments": {"text": "hallo"}})
    )
    assert "hallo" in str(result)
    # usage ledger written next to the registry (spec §15)
    import json as _json
    from pathlib import Path as _Path

    ledger = _Path(store.path).parent / "tool_usage.jsonl"
    assert ledger.exists(), "MCP-Aufruf muss im Usage-Ledger landen"
    entry = _json.loads(ledger.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["name"] == "echo_tool" and entry["ok"] is True
    assert entry.get("duration_ms") is not None


def test_wrapper_reports_import_errors_as_payload():
    spec = ToolSpec(name="x", title="X", module="module_that_does_not_exist_xyz", entrypoint="f")
    payload = _wrapper_for(spec)()
    assert isinstance(payload, dict) and "error" in payload


def test_builder_generated_tool_survives_mcp_e2e(tmp_path):
    """§24 MCP E2E over the builder: built → discovered → invoked → restart keeps it."""
    from skaldir.toolbuilder import ToolBuilder, ToolBuildRequest

    runtime = tmp_path / "runtime"
    result = ToolBuilder(runtime).build(
        ToolBuildRequest(
            capability="double a number over MCP",
            name="doubler",
            source_code='def main(n: int = 2) -> dict:\n    return {"doubled": n * 2}\n',
            module="doubler",
            entrypoint="main",
            sample_input={"n": 21},
        )
    )
    assert result["ok"] is True, result
    store = ToolStore(runtime / "tools.db")
    # discovery: the generated tool is exposed by the godsv-tools server
    names = asyncio.run(_list_tool_names(build_server(store)))
    assert "doubler" in names
    # invoke: valid result through the real in-memory MCP client
    called = asyncio.run(_call(build_server(store), "doubler", {"arguments": {"n": 5}}))
    assert "10" in str(called)
    # restart: a fresh server over the same registry still exposes it
    store.close()
    reopened = ToolStore(runtime / "tools.db")
    assert "doubler" in asyncio.run(_list_tool_names(build_server(reopened)))
    reopened.close()


# --------------------------------------------------------------------------- #
# the published schemas: what the server says about itself                      #
# --------------------------------------------------------------------------- #
#
# The measured bug these pin: every tool used to be registered as one untyped
# ``arguments`` bag, so Skaldir's own server was 0/9 mappable by the MCP adapter
# while another server on the same host was 15/18. The tools already declare
# their schemas (``tools_core`` contracts); the server now publishes them.


def _core_store(tmp_path, name: str = "list_dir", module: str = "skaldir.tools_core.bridge"):
    """A store holding one core tool, registered the way ``tools_core register`` does."""
    handle = ToolStore(tmp_path / "tools.db")
    handle.upsert(
        ToolSpec(
            name=name,
            title="Core tool",
            description="a declared core tool",
            module=module,
            entrypoint=name,
            status=STATUS_APPROVED,
        )
    )
    return handle


def test_a_declared_tool_publishes_its_own_typed_input_schema(tmp_path):
    from skaldir import mcpserver as ms

    store = _core_store(tmp_path)
    try:
        surface = ms.tool_surface(store.get("list_dir"))
    finally:
        store.close()
    schema = surface["input_schema"]
    assert surface["call_convention"] == "declared-properties"
    assert "arguments" not in (schema.get("properties") or {})
    assert sorted(schema["properties"])[:3] == ["depth", "glob", "limit"]
    # the declared types, bounds and enums travel verbatim (they are not invented here)
    assert schema["properties"]["depth"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 3,
        "description": "how many levels to walk (default 1)",
    }
    assert schema["properties"]["order"]["enum"] == ["discovery", "modified"]
    assert schema["additionalProperties"] is False
    assert "skaldir.tools_core" in surface["declared_by"]


def test_the_typed_signature_matches_the_published_properties(tmp_path):
    import inspect

    from skaldir import mcpserver as ms

    store = _core_store(tmp_path)
    try:
        surface = ms.tool_surface(store.get("list_dir"))
    finally:
        store.close()
    parameters = inspect.signature(surface["function"]).parameters
    assert sorted(parameters) == sorted(surface["input_schema"]["properties"])
    # nothing in this contract is required, so every parameter carries a default…
    assert all(item.default is None for item in parameters.values())
    assert parameters["depth"].annotation == (int | None)


def test_required_properties_come_first_and_have_no_default(monkeypatch):
    import inspect

    from skaldir import mcpserver as ms

    declared = {
        "input": {
            "type": "object",
            "properties": {"alpha": {"type": "string"}, "beta": {"type": "integer"}},
            "required": ["beta"],
            "additionalProperties": False,
        },
        "output": {},
        "source": "test",
        "descriptor": "",
    }
    monkeypatch.setattr(ms, "declared_schemas", lambda spec, base_dir=None: dict(declared))
    surface = ms.tool_surface(ToolSpec(name="fixture", title="F", module="m", entrypoint="f"))
    parameters = list(inspect.signature(surface["function"]).parameters.values())
    assert [item.name for item in parameters] == ["beta", "alpha"]
    assert parameters[0].default is inspect.Parameter.empty
    assert parameters[1].default is None


def test_the_result_envelope_is_published_and_the_payload_as_far_as_it_is_declared(tmp_path):
    from skaldir import mcpserver as ms

    store = _core_store(tmp_path)
    try:
        surface = ms.tool_surface(store.get("list_dir"))
    finally:
        store.close()
    schema = surface["output_schema"]
    assert sorted(schema["properties"]) == [
        "duration_ms",
        "error",
        "error_kind",
        "evidence",
        "exit_code",
        "ok",
        "output",
        "tool",
        "warnings",
    ]
    assert schema["properties"]["ok"]["type"] == "boolean"
    assert schema["properties"]["evidence"]["items"]["properties"]["ref"]["type"] == "string"
    # the payload is published as far as the contract states it: the typed fields are
    # there, the ones the contract leaves open (entries, skipped) are named, not faked
    payload = schema["properties"]["output"]
    assert payload["type"] == "object"
    assert payload["additionalProperties"] is True
    assert "entries" not in payload["properties"] and "count" in payload["properties"]
    assert "entries" in payload["description"] and "skipped" in payload["description"]


def test_a_tool_without_a_declared_schema_is_published_as_an_honest_free_form_bag(store):
    from skaldir import mcpserver as ms

    surface = ms.tool_surface(store.get("echo_tool"))
    assert surface["call_convention"] == "object-parameter"
    assert surface["input_schema"]["additionalProperties"] is True
    assert "properties" not in surface["input_schema"]
    assert "declares no typed input schema" in surface["input_schema"]["description"]
    # nothing is stated about the result of a tool that declares nothing
    assert surface["output_schema"] is None
    assert surface["notes"] == [surface["input_schema"]["description"]]


def test_a_property_name_that_cannot_be_a_parameter_keeps_the_object_convention(monkeypatch):
    """Never invent a call convention: fall back, and say so."""
    from skaldir import mcpserver as ms

    monkeypatch.setattr(
        ms,
        "declared_schemas",
        lambda spec, base_dir=None: {
            "input": {
                "type": "object",
                "properties": {"a.b": {"type": "string"}},
                "additionalProperties": False,
            },
            "output": {"type": "object", "properties": {"x": {"type": "string"}}},
            "source": "test",
            "descriptor": "",
        },
    )
    surface = ms.tool_surface(ToolSpec(name="dotted", title="D", module="m", entrypoint="f"))
    assert surface["call_convention"] == "object-parameter"
    assert any("one-object call convention" in note for note in surface["notes"])
    # the declared schema is still published verbatim — the fallback is about the
    # callable, never about what the tool declares
    assert list(surface["input_schema"]["properties"]) == ["a.b"]


def test_an_unexpected_result_is_reported_in_the_declared_envelope(tmp_path):
    """A tool that answers outside its declared shape must not break that shape."""
    from skaldir import mcpserver as ms

    store = _core_store(tmp_path, module="module_that_does_not_exist_xyz")
    try:
        surface = ms.tool_surface(store.get("list_dir"))
    finally:
        store.close()
    payload = surface["function"]()
    assert sorted(payload) == [
        "duration_ms",
        "error",
        "error_kind",
        "evidence",
        "exit_code",
        "ok",
        "output",
        "tool",
        "warnings",
    ]
    assert payload["ok"] is False and payload["error_kind"] == "internal"
    assert payload["exit_code"] == 7 and "import failed" in payload["error"]


def test_the_typed_inputs_reach_tools_list_and_the_call(tmp_path):
    """End to end through the real in-memory fastmcp client (no server process)."""
    import json as _json

    from fastmcp import Client

    store = _core_store(tmp_path)
    server = build_server(store)
    try:

        async def flow():
            async with Client(server) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                schema = tools["list_dir"].input_schema
                out = tools["list_dir"].output_schema
                called = await client.call_tool("list_dir", {"path": ".", "depth": 1, "limit": 3})
                return schema, out, called

        schema, out, called = asyncio.run(flow())
    finally:
        store.close()
    assert "arguments" not in schema["properties"] and "order" in schema["properties"]
    assert schema["properties"]["order"]["enum"] == ["discovery", "modified"]
    assert "output" in (out.get("properties") or {})
    envelope = called.structured_content
    assert envelope["tool"] == "list_dir" and envelope["ok"] is True
    assert envelope["exit_code"] == 0 and isinstance(envelope["output"]["count"], int)
    assert _json.dumps(envelope)
