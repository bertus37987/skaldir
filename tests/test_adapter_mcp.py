"""MCP surface adapter: handshake, tool mapping, typed capability, real call, refusal.

The server is the local stdlib fixture (``tests/fixtures/mcp_test_server.py``),
started as a child process — no network, no container, no owner account. The
interop test additionally drives a REAL fastmcp server so the client is proven
against an implementation we did not write.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from skaldir import adapter_mcp as A

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mcp_test_server.py"
FASTMCP_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mcp_fastmcp_server.py"

#: the tool names the happy path maps
TYPED_TOOLS = {
    "add_numbers",
    "echo_text",
    "server_now",
    "always_errors",
    "union_mode",
    "nested_typed",
}
#: the tools whose schema cannot be validated (each → one refusal reason)
REFUSED_TOOLS = {
    "opaque_blob",
    "freeform_config",
    "missing_required",
    "no_schema",
    "oneof_hetero",
    "recursive_node",
    "deep_freeform",
}
#: the fixture's published tool count (kept in one place: five tests assert it)
FIXTURE_TOOLS_COUNT = 13


def _spec(*extra: str, name: str = "fixture") -> A.MCPServerSpec:
    return A.MCPServerSpec.from_strings(
        command=f"{sys.executable} {FIXTURE} stdio", args=list(extra), name=name, timeout=20.0
    )


@pytest.fixture
def report() -> dict:
    result = A.probe_server(_spec())
    assert result["ok"], result.get("reason")
    return result


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    return tmp_path / "runtime"


# --------------------------------------------------------------------------- #
# (a) handshake + (b) tool list                                                 #
# --------------------------------------------------------------------------- #


def test_stdin_handshake_completes_and_negotiates_protocol(report):
    assert report["ok"] is True
    assert report["failure_class"] == ""
    assert report["transport"] == "stdio"
    assert report["protocol_version"] == "2025-06-18"
    assert report["protocol_supported"] is True
    assert report["server_info"]["name"] == "godsv-mcp-fixture"
    assert report["server_capabilities"]["tools"] == {"listChanged": False}
    assert report["duration_ms"] >= 0


def test_handshake_transcript_is_the_raw_message_sequence(report):
    """The proof of the handshake: the messages, in order, as they went on the wire."""
    methods = [
        str((entry["message"] or {}).get("method") or entry["kind"])
        for entry in report["transcript"]
    ]
    assert methods == [
        "transport",
        "initialize",
        "response",
        "notifications/initialized",
        "tools/list",
        "response",
        "transport",
    ]
    initialize = report["transcript"][1]
    assert initialize["direction"] == "->"
    params = initialize["message"]["params"]
    assert params["protocolVersion"] == "2025-06-18"
    assert params["clientInfo"]["name"] == "skaldir-godsv"
    notification = report["transcript"][3]
    assert notification["message"] == {"jsonrpc": "2.0", "method": "notifications/initialized"}
    response = report["transcript"][2]
    assert response["direction"] == "<-"
    assert response["message"]["result"]["serverInfo"]["name"] == "godsv-mcp-fixture"
    assert response["message"]["id"] == 1


def test_tool_list_carries_every_tool_with_its_schemas(report):
    assert report["tools_count"] == FIXTURE_TOOLS_COUNT
    names = {tool["name"] for tool in report["tools"]}
    assert names == TYPED_TOOLS | REFUSED_TOOLS
    add = next(tool for tool in report["tools"] if tool["name"] == "add_numbers")
    assert add["inputSchema"]["required"] == ["a", "b"]
    assert add["inputSchema"]["properties"]["a"] == {
        "type": "integer",
        "description": "first addend",
    }
    assert add["outputSchema"]["properties"] == {"sum": {"type": "integer"}}
    assert add["description"] == "Return the sum of two integers."


def test_the_list_is_paginated_through(report):
    """A server that pages must not lose tools silently."""
    paged = A.probe_server(_spec("--page-size", "4"))
    assert paged["ok"]
    assert paged["tools_count"] == report["tools_count"] == FIXTURE_TOOLS_COUNT
    assert {tool["name"] for tool in paged["tools"]} == {tool["name"] for tool in report["tools"]}
    cursors = [
        entry["message"]["params"]
        for entry in paged["transcript"]
        if str((entry["message"] or {}).get("method") or "") == "tools/list"
    ]
    assert len(cursors) >= 3, cursors


def test_unknown_protocol_is_refused_unless_allowed():
    refused = A.probe_server(_spec("--protocol", "1999-01-01"))
    assert refused["ok"] is False
    assert refused["failure_class"] == "protocol_unsupported"
    assert "1999-01-01" in refused["reason"]
    allowed = A.probe_server(_spec("--protocol", "1999-01-01"), allow_unknown_protocol=True)
    assert allowed["ok"] is True
    assert allowed["protocol_supported"] is False


def test_a_server_without_tools_is_a_reason_not_a_silent_success():
    result = A.handshake(_spec("--omit-tools"), require_tools=True)
    assert result["ok"] is False
    assert result["failure_class"] == "no_tools"


def test_a_missing_server_command_is_a_structured_refusal():
    spec = A.MCPServerSpec(name="nope", transport="stdio", command=["/nonexistent/mcp-server"])
    result = A.handshake(spec)
    assert result["ok"] is False
    assert result["failure_class"] == "handshake_failed"
    assert "could not be started" in result["reason"]
    assert A.handshake(A.MCPServerSpec(name="empty")).get("failure_class") == "command_required"
    assert A.handshake(A.MCPServerSpec(name="empty", transport="http")).get("failure_class") == (
        "url_required"
    )
    assert (
        A.handshake(A.MCPServerSpec(name="x", transport="carrier-pigeon")).get("failure_class")
        == "transport_unsupported"
    )


def test_the_handshake_can_be_proven_from_the_server_side_too(tmp_path):
    """An independent transcript, written by the SERVER, matches the client's order."""
    log = tmp_path / "server.log"
    spec = _spec("--log", str(log))
    assert A.probe_server(spec)["ok"]
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    received = [row for row in rows if row["direction"] == "<-"]
    seen = [str((row["message"] or {}).get("method") or "") for row in received]
    assert seen == ["initialize", "notifications/initialized", "tools/list"]
    answered = [row for row in rows if row["direction"] == "->"]
    assert [row["message"]["id"] for row in answered] == [1, 2]


# --------------------------------------------------------------------------- #
# (c) mapping: tool → typed capability, or a refusal with a reason              #
# --------------------------------------------------------------------------- #


def test_add_numbers_maps_onto_a_fully_typed_capability(report):
    mapped = A.map_surface(report, tools=["add_numbers"])
    assert mapped["ok"] and len(mapped["mapped"]) == 1
    adapter = mapped["mapped"][0]
    assert adapter["name"] == "mcp_add_numbers"
    assert adapter["kind"] == "mcp"
    assert adapter["tool"] == "add_numbers"
    assert adapter["operation"]["inputs"] == {
        "a": {"type": "integer", "required": True, "path": "a", "description": "first addend"},
        "b": {"type": "integer", "required": True, "path": "b", "description": "second addend"},
    }
    assert adapter["operation"]["outputs"] == {
        "sum": {"type": "integer", "from": "structuredContent", "path": "sum"}
    }
    assert adapter["operation"]["output_mode"] == "structuredContent"
    assert adapter["server"]["protocol_version"] == "2025-06-18"
    assert adapter["server"]["tools_listed"] == FIXTURE_TOOLS_COUNT
    assert adapter["permissions"]["filesystem"]["access"].startswith("none")


def test_a_tool_without_an_output_schema_falls_back_to_text(report):
    mapped = A.map_surface(report, tools=["always_errors"])
    adapter = mapped["mapped"][0]
    assert adapter["operation"]["output_mode"] == "content-text"
    assert adapter["operation"]["outputs"] == {
        "result": {"type": "string", "from": "content", "path": "text"}
    }


def test_a_zero_argument_tool_maps_without_inputs(report):
    mapped = A.map_surface(report, tools=["server_now"])
    adapter = mapped["mapped"][0]
    assert adapter["operation"]["inputs"] == {}
    assert adapter["operation"]["outputs"] == {
        "iso": {"type": "string", "from": "structuredContent", "path": "iso"}
    }


def test_every_untypable_tool_is_refused_with_its_own_reason(report):
    mapped = A.map_surface(report)
    assert {item["tool"] for item in mapped["mapped"]} == TYPED_TOOLS
    refused = {item["tool"]: item["reason"] for item in mapped["refused"]}
    assert set(refused) == REFUSED_TOOLS
    assert "unknown type 'binary-blob'" in refused["opaque_blob"]
    assert "free-form object carries no typed properties" in refused["freeform_config"]
    assert "required input(s) ['ghost']" in refused["missing_required"]
    assert "no inputSchema" in refused["no_schema"]
    # the constructs the mapper now RESOLVES are no longer refusals at all
    assert "union type" not in "; ".join(refused.values())
    assert "'$ref' cannot be reduced to one typed value" not in "; ".join(refused.values())
    assert "does not resolve inside" not in "; ".join(refused.values())
    # what still cannot be typed is refused by NAME, not by shrug
    assert "'oneOf' offers 2 alternatives of different types" in refused["oneof_hetero"]
    assert "'integer'" in refused["oneof_hetero"] and "'string'" in refused["oneof_hetero"]
    assert "'$ref' '#/$defs/Node' is recursive" in refused["recursive_node"]
    assert "'rows[].payload'" in refused["deep_freeform"]
    # every reason names the property it disagrees with
    assert "'blob'" in refused["opaque_blob"]
    assert "'config'" in refused["freeform_config"]
    assert "'value'" in refused["oneof_hetero"]
    assert "'head.child<anyOf[0]>'" in refused["recursive_node"]


def test_mapping_a_refused_tool_raises_with_the_reason(report):
    tool = next(item for item in report["tools"] if item["name"] == "oneof_hetero")
    with pytest.raises(A.MCPRefusal) as excinfo:
        A.map_tool_to_capability(tool, server={"name": "fixture"})
    assert "oneof_hetero" in str(excinfo.value)
    assert "different types" in str(excinfo.value)


@pytest.mark.parametrize(
    "tool, expected",
    [
        (
            {"name": "x", "inputSchema": {"type": "array", "items": {"type": "string"}}},
            "not an object schema",
        ),
        (
            {"name": "x", "inputSchema": {"type": "object", "properties": {"a": {"$ref": "#/x"}}}},
            "$ref",
        ),
        (
            {"name": "x", "inputSchema": {"type": "object", "properties": {"a": {}}}},
            "no declared type",
        ),
        (
            {
                "name": "x",
                "inputSchema": {"type": "object", "properties": {"a": {"type": "array"}}},
            },
            "array without an item schema",
        ),
        (
            {"name": "x", "inputSchema": {"type": "object", "properties": {}, "required": "a"}},
            "'required' is not a list",
        ),
        ({"name": "", "inputSchema": {"type": "object"}}, "no name"),
    ],
)
def test_schema_edge_cases_are_refused_with_a_reason(tool, expected):
    usable, reason = A.validate_tool_schema(tool)
    assert usable is False
    assert expected in reason


@pytest.mark.parametrize(
    "property_schema, expected",
    [
        (
            {"oneOf": [{"type": "string"}, {"type": "integer"}]},
            "'oneOf' offers 2 alternatives of different types",
        ),
        (
            {
                "anyOf": [
                    {"type": "object", "properties": {"a": {"type": "string"}}},
                    {"type": "object", "properties": {"b": {"type": "string"}}},
                ]
            },
            "structural alternatives of type 'object'",
        ),
        (
            {
                "anyOf": [
                    {"type": "array", "items": {"type": "string"}},
                    {"type": "array", "items": {"type": "integer"}},
                ]
            },
            "structural alternatives of type 'array'",
        ),
        ({"type": ["string", "integer"]}, "union type"),
        ({"not": {"type": "string"}}, "'not' cannot be reduced to one typed value"),
        ({"if": {"type": "string"}, "then": {"type": "string"}}, "'if' cannot be reduced"),
        ({"$ref": "#/$defs/Absent"}, "does not resolve inside this tool's schema"),
        ({"$ref": "https://example.com/other.json"}, "does not resolve"),
        ({"type": "array", "prefixItems": [{"type": "string"}]}, "prefixItems"),
        ({"type": "array", "items": [{"type": "string"}]}, "prefixItems"),
        ({"type": "object"}, "free-form object"),
        ({"type": "object", "additionalProperties": True}, "free-form object"),
        ({"enum": [1, "two"]}, "'enum'/'const' values of different types"),
        # refinements narrow a value whose type is known: they are carried, not refused
        ({"type": "string", "pattern": "^a", "format": "uuid"}, None),
        ({"type": "integer", "minimum": 0, "maximum": 9}, None),
        ({"anyOf": [{"type": "string", "enum": ["a", "b"]}, {"type": "null"}]}, None),
        ({"const": "fixed"}, None),
        # a MAP declares its VALUES, so it is typed: keys are open, values are not
        ({"type": "object", "additionalProperties": {"type": "string"}}, None),
    ],
)
def test_the_constructs_that_cannot_be_typed_are_named(property_schema, expected):
    """A refusal names the property AND the construct; a resolvable one is not refused."""
    tool = {"name": "x", "inputSchema": {"type": "object", "properties": {"p": property_schema}}}
    usable, reason = A.validate_tool_schema(tool)
    if expected is None:
        assert usable is True, reason
    else:
        assert usable is False
        assert expected in reason
        assert "'p" in reason


def test_a_typed_additional_properties_object_is_a_map_not_a_free_form_bag():
    """``additionalProperties: {"type": "string"}`` is a MAP — a typed, mappable shape.

    The free-form policy is about objects whose VALUES are undeclared (``{"type":
    "object"}``, ``additionalProperties: true``) — those stay refused. A declared value
    type makes the shape finite and checkable: the keys are open, every value is a
    string. ``tools_core``'s ``terminal.env`` and ``web_fetch.headers`` are exactly
    this declaration, and before this they were the two tools of nine that refused.
    """
    tool = {
        "name": "stringmap",
        "inputSchema": {
            "type": "object",
            "properties": {"env": {"type": "object", "additionalProperties": {"type": "string"}}},
            "required": [],
        },
    }
    usable, reason = A.validate_tool_schema(tool)
    assert usable is True, reason

    adapter = A.map_tool_to_capability(tool, server={"name": "x"})
    env = adapter["operation"]["inputs"]["env"]
    assert env["type"] == "object"
    assert env["values"] == {"type": "string", "path": "env[<key>]"}
    assert env["fields"] == {}, "a map declares no fixed keys"
    assert [leaf["path"] for leaf in adapter["operation"]["typed_paths"]] == ["env[<key>]"]

    # the keys are open, and every value must be the declared type
    assert A.validate_payload(adapter, {"env": {"A": "b", "B": "c"}})["env"] == {
        "A": "b",
        "B": "c",
    }
    with pytest.raises(A.MCPRefusal) as excinfo:
        A.validate_payload(adapter, {"env": {"A": 3}})
    assert "input 'env'.A" in str(excinfo.value) and "is not a string" in str(excinfo.value)


def test_a_ref_schema_is_resolved_into_nested_typed_paths(report):
    """$defs/$ref, anyOf[T,null], ["T","null"], arrays and nested objects are TYPED."""
    adapter = A.map_surface(report, tools=["nested_typed"])["mapped"][0]
    inputs = adapter["operation"]["inputs"]
    assert inputs["mode"] == {
        "type": "string",
        "required": False,
        "path": "mode",
        "nullable": True,
        "enum": ["fast", "slow"],
        "default": None,
    }
    assert inputs["path"] == {
        "type": "array",
        "required": True,
        "path": "path",
        "items": {
            "type": "object",
            "path": "path[]",
            "fields": {
                "x": {"type": "integer", "required": True, "path": "path[].x"},
                "y": {"type": "integer", "required": True, "path": "path[].y"},
            },
        },
    }
    assert inputs["origin"] == {
        "type": "object",
        "required": False,
        "path": "origin",
        "nullable": True,
        "default": None,
        "fields": {
            "x": {"type": "integer", "required": True, "path": "origin.x"},
            "y": {"type": "integer", "required": True, "path": "origin.y"},
        },
    }
    assert inputs["label"] == {
        "type": "string", "required": False, "path": "label", "nullable": True,
    }
    assert inputs["retries"] == {
        "type": "integer", "required": False, "path": "retries",
        "constraints": {"minimum": 0}, "default": 3,
    }
    assert inputs["settings"] == {
        "type": "object", "required": False, "path": "settings", "additional": False,
        "fields": {
            "verbose": {
                "type": "boolean", "required": False, "path": "settings.verbose",
                "default": False,
            }
        },
    }
    # every leaf typed path — nested and array element paths included
    paths = {
        (leaf["path"], leaf["type"], leaf["required"])
        for leaf in adapter["operation"]["typed_paths"]
    }
    for expected in [
        ("mode", "string", False),
        ("path[].x", "integer", True),
        ("path[].y", "integer", True),
        ("origin.x", "integer", True),
        ("label", "string", False),
        ("retries", "integer", False),
        ("settings.verbose", "boolean", False),
    ]:
        assert expected in paths, expected
    # …and the resolved output shape, on the other side of the same tool
    assert adapter["operation"]["outputs"]["points"] == {
        "type": "array",
        "from": "structuredContent",
        "path": "points",
        "items": {
            "type": "object",
            "path": "points[]",
            "fields": {
                "x": {"type": "integer", "required": True, "path": "points[].x"},
                "y": {"type": "integer", "required": True, "path": "points[].y"},
            },
        },
    }
    assert adapter["operation"]["outputs"]["mode"] == {
        "type": "string",
        "from": "structuredContent",
        "path": "mode",
        "nullable": True,
        "default": None,
    }
    assert adapter["operation"]["output_mode"] == "structuredContent"


def test_a_nullable_union_is_one_typed_value_not_a_union(report):
    """``["string","null"]`` and ``anyOf[T,null]`` are a nullable T — not a union."""
    adapter = A.map_surface(report, tools=["union_mode"])["mapped"][0]
    assert adapter["operation"]["inputs"]["mode"] == {
        "type": "string", "required": False, "path": "mode", "nullable": True,
    }


def test_all_of_is_merged_into_one_shape_not_dropped():
    tool = {
        "name": "x",
        "inputSchema": {
            "type": "object",
            "$defs": {
                "Base": {
                    "type": "object",
                    "properties": {"a": {"type": "string"}},
                    "required": ["a"],
                }
            },
            "properties": {
                "p": {
                    "allOf": [
                        {"$ref": "#/$defs/Base"},
                        {"type": "object", "properties": {"b": {"type": "integer"}}},
                    ]
                }
            },
        },
    }
    usable, reason = A.validate_tool_schema(tool)
    assert usable is True, reason
    adapter = A.map_tool_to_capability(tool, server={"name": "s"})
    assert adapter["operation"]["inputs"]["p"]["fields"] == {
        "a": {"type": "string", "required": True, "path": "p.a"},
        "b": {"type": "integer", "required": False, "path": "p.b"},
    }


def test_all_of_that_disagrees_about_the_type_is_refused():
    tool = {
        "name": "x",
        "inputSchema": {
            "type": "object",
            "properties": {"p": {"allOf": [{"type": "string"}, {"type": "integer"}]}},
        },
    }
    usable, reason = A.validate_tool_schema(tool)
    assert usable is False
    assert "conflicting types" in reason


def test_a_zero_field_object_is_a_shape_not_a_free_form_object():
    """``additionalProperties: false`` with no properties is finite: it maps."""
    tool = {
        "name": "x",
        "inputSchema": {
            "type": "object",
            "properties": {"empty": {"type": "object", "additionalProperties": False}},
        },
    }
    usable, reason = A.validate_tool_schema(tool)
    assert usable is True, reason
    adapter = A.map_tool_to_capability(tool, server={"name": "s"})
    assert adapter["operation"]["inputs"]["empty"]["fields"] == {}
    assert adapter["operation"]["inputs"]["empty"]["additional"] is False


def test_a_refused_tool_is_never_staged(report, runtime):
    """The refusal is not cosmetic: no registry name, no files for it."""
    mapped = A.map_surface(report)
    for adapter in mapped["mapped"]:
        assert A.stage_mcp_tool(adapter, runtime=runtime, report=report)["ok"]
    staged = {path.name for path in (runtime / A.MCP_TOOL_DIR).iterdir()}
    assert staged == {adapter["name"] for adapter in mapped["mapped"]}
    assert "mcp_opaque_blob" not in staged
    assert not A.mcp_tool_paths(runtime, "mcp_opaque_blob")["tool_dir"].exists()


def test_nothing_mappable_is_a_blocked_reason(report):
    mapped = A.map_surface(report, tools=["opaque_blob"])
    assert mapped["ok"] is False
    assert mapped["failure_class"] == "nothing_mappable"
    assert mapped["refused"][0]["tool"] == "opaque_blob"


def test_mapping_without_a_handshake_never_invents_an_adapter():
    mapped = A.map_surface({"ok": False, "failure_class": "handshake_failed", "reason": "down"})
    assert mapped["ok"] is False
    assert mapped["mapped"] == []
    assert mapped["failure_class"] == "handshake_failed"


def test_a_server_with_no_tools_maps_nothing_with_a_reason():
    mapped = A.map_surface(A.probe_server(_spec("--omit-tools")))
    assert mapped["ok"] is False
    assert mapped["mapped"] == []
    assert mapped["refused"] == []
    assert mapped["failure_class"] == "no_tools"


# --------------------------------------------------------------------------- #
# (d) a REAL call through the adapter                                           #
# --------------------------------------------------------------------------- #


def _staged(report, runtime, tool: str, **kwargs) -> dict:
    adapter = A.map_surface(report, tools=[tool], **kwargs)["mapped"][0]
    staged = A.stage_mcp_tool(adapter, runtime=runtime, report=report)
    assert staged["ok"], staged
    return staged


def test_a_real_call_returns_the_declared_outputs(report, runtime):
    staged = _staged(report, runtime, "add_numbers")
    adapter = json.loads(Path(staged["adapter_path"]).read_text(encoding="utf-8"))
    call = A.call_mapped_tool(adapter, {"a": 2, "b": 3})
    assert call["ok"] is True
    assert call["output"] == {"sum": 5}
    assert call["raw"]["structuredContent"] == {"sum": 5}
    assert call["tool"] == "add_numbers"
    methods = [
        str((entry["message"] or {}).get("method") or entry["kind"]) for entry in call["transcript"]
    ]
    assert methods == [
        "transport",
        "initialize",
        "response",
        "notifications/initialized",
        "tools/call",
        "response",
        "transport",
    ]


def test_test_mapped_tool_records_evidence_bound_to_the_adapter_bytes(report, runtime):
    staged = _staged(report, runtime, "add_numbers")
    evidence = A.test_mapped_tool(
        runtime, staged["name"], payload={"a": 20, "b": 22}, expect={"sum": 42}
    )
    assert evidence["ok"] is True
    assert evidence["observed"] == {"sum": 42}
    assert evidence["match"] is True
    assert evidence["expected"] == {"sum": 42}
    assert evidence["adapter_digest"] == staged["adapter_digest"]
    assert evidence["registered"] is False
    on_disk = json.loads(Path(evidence["evidence_path"]).read_text(encoding="utf-8"))
    assert on_disk["observed"] == {"sum": 42}
    assert on_disk["adapter_digest"] == staged["adapter_digest"]
    assert on_disk["oracle"].startswith("none —")


def test_a_text_result_is_validated_as_the_declared_string(report, runtime):
    staged = _staged(report, runtime, "echo_text")
    evidence = A.test_mapped_tool(
        runtime, staged["name"], payload={"text": "hello"}, expect={"echo": "hello"}
    )
    assert evidence["ok"] is True, evidence["reason"]
    assert evidence["observed"] == {"echo": "hello"}


def test_a_tool_error_is_a_failure_not_an_empty_success(report, runtime):
    staged = _staged(report, runtime, "always_errors")
    evidence = A.test_mapped_tool(runtime, staged["name"], payload={"reason": "boom"})
    assert evidence["ok"] is False
    assert evidence["failure_class"] == "output_mismatch"
    assert "reported an error" in evidence["reason"]
    assert "boom" in evidence["reason"]


def test_a_failing_call_never_matches_a_stale_expectation(report, runtime):
    staged = _staged(report, runtime, "add_numbers")
    evidence = A.test_mapped_tool(
        runtime, staged["name"], payload={"a": 1, "b": 1}, expect={"sum": 999}
    )
    assert evidence["ok"] is False
    assert evidence["failure_class"] == "expectation_mismatch"
    assert "expected 999, observed 2" in evidence["difference"]


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"a": "two", "b": 3}, "input 'a' is not an integer"),
        ({"a": 2}, "missing required input: b"),
        ({"a": 2, "b": 3, "c": 4}, "unknown input(s) for this adapter: c"),
        ({"a": True, "b": 3}, "input 'a' is not an integer"),
    ],
)
def test_a_payload_outside_the_declared_types_is_refused(report, runtime, payload, expected):
    staged = _staged(report, runtime, "add_numbers")
    adapter = json.loads(Path(staged["adapter_path"]).read_text(encoding="utf-8"))
    call = A.call_mapped_tool(adapter, payload)
    assert call["ok"] is False
    assert call["failure_class"] == "invalid_input"
    assert expected in call["reason"]


def _nested_adapter(report) -> dict:
    return A.map_surface(report, tools=["nested_typed"])["mapped"][0]


def test_declared_defaults_travel_and_a_null_default_means_absent(report):
    """A concrete default is sent; a declared ``default: null`` is left out."""
    adapter = _nested_adapter(report)
    assert A.validate_payload(adapter, {"path": []}) == {"path": [], "retries": 3}
    assert A.validate_payload(adapter, {"path": [], "mode": "fast"}) == {
        "path": [],
        "retries": 3,
        "mode": "fast",
    }
    assert A.validate_payload(adapter, {"path": [], "retries": 5}) == {"path": [], "retries": 5}
    # origin's declared default is null: the argument is left out, not sent as null
    assert "origin" not in A.validate_payload(adapter, {"path": []})


def test_a_nested_input_value_is_type_checked(report):
    """The resolved sub-shape is enforced, not decoration."""
    adapter = _nested_adapter(report)
    with pytest.raises(A.MCPRefusal) as excinfo:
        A.validate_payload(adapter, {"path": [{"x": "no", "y": 1}]})
    assert "input 'path'[0].x is not an integer" in str(excinfo.value)
    # a sub-field the value OMITS is not an error: only what is present is checked
    assert A.validate_payload(adapter, {"path": [], "origin": {"x": 1}})["origin"] == {"x": 1}
    with pytest.raises(A.MCPRefusal) as excinfo:
        A.validate_payload(adapter, {"path": [], "mode": "sideways"})
    assert "not one of ['fast', 'slow']" in str(excinfo.value)
    with pytest.raises(A.MCPRefusal) as excinfo:
        A.validate_payload(adapter, {"path": [], "settings": {"nope": True}})
    assert "undeclared key(s) ['nope']" in str(excinfo.value)
    with pytest.raises(A.MCPRefusal) as excinfo:
        A.validate_payload(adapter, {"path": [], "retries": None})
    assert "is null but the declared type is not nullable" in str(excinfo.value)
    # a null IS legal where the schema says ["string","null"]
    assert A.validate_payload(adapter, {"path": [], "label": None})["label"] is None


def test_the_declared_output_shape_is_checked_against_the_real_result(report):
    adapter = _nested_adapter(report)
    ok, _parsed, reason = A.read_tool_result(
        adapter,
        {"structuredContent": {"total": 1, "mode": None, "points": [{"x": "no", "y": 2}]}},
    )
    assert ok is False
    assert "output 'points'[0].x is not an integer" in reason


def test_a_ref_resolved_tool_is_called_live_with_its_nested_inputs(report, runtime):
    """One real call: nested object + array of objects in, resolved shape out."""
    adapter = _nested_adapter(report)
    staged = A.stage_mcp_tool(adapter, runtime=runtime, report=report)
    assert staged["ok"], staged
    evidence = A.test_mapped_tool(
        runtime,
        staged["name"],
        payload={"path": [{"x": 1, "y": 2}, {"x": 3, "y": 4}], "mode": "slow"},
        expect={
            "total": 2,
            "points": [{"x": 1, "y": 2}, {"x": 3, "y": 4}],
            "mode": "slow",
        },
    )
    assert evidence["ok"] is True, evidence.get("reason")
    assert evidence["observed"]["total"] == 2
    call = A.call_mapped_tool(adapter, {"path": [{"x": 5, "y": 6}]})
    assert call["ok"] is True, call.get("reason")
    assert call["output"]["points"] == [{"x": 5, "y": 6}]


def test_the_generated_module_and_wrapper_call_the_same_tool(report, runtime):
    staged = _staged(report, runtime, "add_numbers")
    module_path = Path(staged["module"])
    assert module_path.is_file()
    assert module_path.name == "mcp_add_numbers.py"
    adapter_path = Path(staged["adapter_path"])
    assert A.call_staged_adapter(adapter_path, {"a": 5, "b": 6}) == {"sum": 11}
    wrapper = Path(staged["wrapper"])
    proc = subprocess.run(  # noqa: S603 — our own generated wrapper
        [str(wrapper), json.dumps({"a": 7, "b": 8})], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"sum": 15}
    assert (runtime / "tool_usage.jsonl").is_file()


def test_call_staged_adapter_raises_instead_of_returning_an_empty_dict(report, runtime):
    staged = _staged(report, runtime, "add_numbers")
    with pytest.raises(A.MCPError):
        A.call_staged_adapter(Path(staged["adapter_path"]), {"a": "nope", "b": 1})


# --------------------------------------------------------------------------- #
# (e) registration through the EXISTING registry path                           #
# --------------------------------------------------------------------------- #


def _store(runtime: Path):
    from skaldir.tools import ToolStore

    return ToolStore(runtime / "tools.db")


def test_registration_without_evidence_is_refused(report, runtime):
    staged = _staged(report, runtime, "add_numbers")
    store = _store(runtime)
    try:
        result = A.register_mapped_tool(runtime, staged["name"], store)
    finally:
        store.close()
    assert result["ok"] is False
    assert result["failure_class"] == "unverified"
    assert "no test evidence" in result["reason"]


def test_registration_of_a_failed_test_is_refused(report, runtime):
    staged = _staged(report, runtime, "add_numbers")
    A.test_mapped_tool(runtime, staged["name"], payload={"a": 1, "b": 1}, expect={"sum": 999})
    store = _store(runtime)
    try:
        result = A.register_mapped_tool(runtime, staged["name"], store)
    finally:
        store.close()
    assert result["ok"] is False
    assert result["failure_class"] == "test_failed"


def test_an_adapter_edited_after_its_test_is_not_registered(report, runtime):
    staged = _staged(report, runtime, "add_numbers")
    assert A.test_mapped_tool(runtime, staged["name"], payload={"a": 2, "b": 3}, expect={"sum": 5})[
        "ok"
    ]
    adapter_path = Path(staged["adapter_path"])
    adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
    adapter["capability"] = "tampered after the test"
    adapter_path.write_text(json.dumps(adapter, indent=2, sort_keys=True), encoding="utf-8")
    store = _store(runtime)
    try:
        result = A.register_mapped_tool(runtime, staged["name"], store)
    finally:
        store.close()
    assert result["ok"] is False
    assert result["failure_class"] == "adapter_changed"


def test_registration_goes_through_toolspec_and_the_publication_outbox(report, runtime):
    staged = _staged(report, runtime, "add_numbers")
    assert A.test_mapped_tool(runtime, staged["name"], payload={"a": 2, "b": 3}, expect={"sum": 5})[
        "ok"
    ]
    store = _store(runtime)
    try:
        result = A.register_mapped_tool(runtime, staged["name"], store, requested_by="test")
        assert result["ok"] is True, result
        assert result["registered"] is True
        assert result["version"] == "0.1.0"
        assert result["kind"] == "mcp"
        assert result["health_state"] == "HEALTHY"
        assert result["rollback_id"]
        assert result["publish"]["created"] is True
        assert result["publish"]["version"] == "0.1.0"
        spec = store.get(staged["name"])
        assert spec is not None
        assert spec.kind == "mcp"
        assert spec.status == "approved"
        assert spec.module == "mcp_add_numbers"
        assert spec.entrypoint == "main"
        assert spec.sample_input == {"a": 2, "b": 3}
        assert "mcp" in spec.tags
    finally:
        store.close()


def test_a_registered_capability_carries_its_permissions_health_and_rollback(report, runtime):
    staged = _staged(report, runtime, "echo_text")
    assert A.test_mapped_tool(
        runtime, staged["name"], payload={"text": "hi"}, expect={"echo": "hi"}
    )["ok"]
    store = _store(runtime)
    try:
        result = A.register_mapped_tool(runtime, staged["name"], store)
    finally:
        store.close()
    manifest = A.mcp_manifest(
        json.loads(Path(staged["adapter_path"]).read_text(encoding="utf-8")),
        name=staged["name"],
        status="ready",
        module_name="mcp_echo_text",
        version="0.1.0",
    )
    assert result["permissions"]["network"]["allowed_domains"] == []
    assert manifest["runtime"] == "mcp"
    assert manifest["type"] == "mcp"
    assert manifest["inputs"] == ["text"]
    assert manifest["outputs"] == ["echo"]
    health = result["health"]
    assert health["state"] == "HEALTHY"
    snapshot = Path(A.mcp_tool_paths(runtime, staged["name"])["rollbacks"]) / (
        result["rollback_id"] + ".json"
    )
    assert snapshot.is_file()


def test_reregistering_bumps_the_patch_version(report, runtime):
    staged = _staged(report, runtime, "add_numbers")
    store = _store(runtime)
    try:
        for expected in ("0.1.0", "0.1.1"):
            assert A.test_mapped_tool(
                runtime, staged["name"], payload={"a": 1, "b": 2}, expect={"sum": 3}
            )["ok"]
            result = A.register_mapped_tool(runtime, staged["name"], store)
            assert result["version"] == expected
    finally:
        store.close()


def test_the_registry_entry_is_visible_to_the_spawning_tools_module(report, runtime):
    """The manifest the adapter writes must be readable by the existing readers."""
    from skaldir.capability import load_tool_entries, project_id_for

    staged = _staged(report, runtime, "add_numbers")
    assert A.test_mapped_tool(runtime, staged["name"], payload={"a": 2, "b": 3}, expect={"sum": 5})[
        "ok"
    ]
    store = _store(runtime)
    try:
        project = project_id_for(runtime)
        A.register_mapped_tool(runtime, staged["name"], store, project_id=project)
    finally:
        store.close()
    entries = load_tool_entries(runtime)
    mine = next(entry for entry in entries if entry["name"] == staged["name"])
    assert mine["scope"] == "project"
    assert mine["project_id"].startswith(("path:", "gitdir:", "remote:"))
    assert mine["runtime"] == "mcp"
    assert mine["version"] == 1000  # "0.1.0" → major*1e6 + minor*1e3 + patch


def test_the_registration_declares_that_the_mcp_export_does_not_cover_it(report, runtime):
    """An honest residual, surfaced as a warning instead of a silent claim."""
    staged = _staged(report, runtime, "add_numbers")
    assert A.test_mapped_tool(runtime, staged["name"], payload={"a": 1, "b": 1}, expect={"sum": 2})[
        "ok"
    ]
    store = _store(runtime)
    try:
        result = A.register_mapped_tool(runtime, staged["name"], store)
    finally:
        store.close()
    assert result["exportable_as_python_tool"] is False
    assert any("mcp serve" in text for text in result["warnings"])


# --------------------------------------------------------------------------- #
# interop: a REAL fastmcp server (not our fixture)                              #
# --------------------------------------------------------------------------- #


def _fastmcp_spec(*extra: str) -> A.MCPServerSpec:
    return A.MCPServerSpec.from_strings(
        command=f"{sys.executable} {FASTMCP_FIXTURE} stdio",
        args=list(extra),
        name="fastmcp",
        timeout=30.0,
    )


def test_the_client_speaks_to_a_real_fastmcp_server_and_maps_its_tools(runtime):
    pytest.importorskip(
        "fastmcp", reason="MCP interop needs the extra: uv pip install 'godsv[mcp]'"
    )
    report = A.probe_server(_fastmcp_spec())
    assert report["ok"] is True, report.get("reason")
    assert report["server_info"]["name"] == "godsv-fastmcp-fixture"
    assert {tool["name"] for tool in report["tools"]} == {"add", "shout", "total"}
    mapped = A.map_surface(report)
    assert mapped["refused"] == []
    total = next(item for item in mapped["mapped"] if item["tool"] == "total")
    assert total["operation"]["outputs"] == {
        "count": {"type": "integer", "from": "structuredContent", "path": "count"},
        "total": {"type": "integer", "from": "structuredContent", "path": "total"},
    }
    staged = A.stage_mcp_tool(total, runtime=runtime, report=report)
    assert staged["ok"]
    evidence = A.test_mapped_tool(
        runtime, staged["name"], payload={"values": [1, 2, 3, 4]}, expect={"total": 10, "count": 4}
    )
    assert evidence["ok"] is True, evidence["reason"]
    assert evidence["observed"] == {"total": 10, "count": 4}


# --------------------------------------------------------------------------- #
# the http transport (streamable HTTP, plain JSON and SSE)                      #
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _start_http(tmp_path: Path, *extra: str) -> tuple[subprocess.Popen, str]:
    port = _free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    proc = subprocess.Popen(  # noqa: S603 — our own fixture
        [sys.executable, str(FIXTURE), "http", "--port", str(port), *extra],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/mcp", timeout=1).close()  # noqa: S310
            return proc, url
        except Exception:  # noqa: BLE001 — the server needs a moment to bind
            time.sleep(0.05)
    proc.terminate()
    raise AssertionError("the http fixture never became reachable")


@pytest.mark.parametrize("sse", [False, True])
def test_streamable_http_handshake_mapping_and_call(tmp_path, sse):
    proc, url = _start_http(tmp_path, *(["--sse"] if sse else []))
    try:
        spec = A.MCPServerSpec.from_strings(url=url, name=f"http-{sse}", timeout=20.0)
        report = A.probe_server(spec)
        assert report["ok"] is True, report.get("reason")
        assert report["transport"] == "http"
        assert report["protocol_version"] == "2025-06-18"
        assert report["session_id"] == "fixture-session-1"
        assert report["tools_count"] == FIXTURE_TOOLS_COUNT
        mapped = A.map_surface(report, tools=["add_numbers"])
        call = A.call_mapped_tool(mapped["mapped"][0], {"a": 40, "b": 2})
        assert call["ok"] is True, call["reason"]
        assert call["output"] == {"sum": 42}
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_an_unreachable_http_endpoint_is_a_structured_refusal():
    spec = A.MCPServerSpec.from_strings(url="http://127.0.0.1:9/mcp", name="dead", timeout=2.0)
    result = A.handshake(spec)
    assert result["ok"] is False
    assert result["failure_class"] == "handshake_failed"
    assert "did not answer" in result["reason"]


# --------------------------------------------------------------------------- #
# payload/type helpers                                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value, declared, ok",
    [
        (True, "boolean", True),
        (1, "boolean", False),
        (5, "integer", True),
        (True, "integer", False),
        (5.5, "number", True),
        (5, "number", True),
        (True, "number", False),
        ("x", "string", True),
        (5, "string", False),
        ([1], "array", True),
        ({}, "array", False),
        ({}, "object", True),
        ([], "object", False),
    ],
)
def test_declared_output_types_are_checked(value, declared, ok):
    assert A._type_ok(value, declared)[0] is ok


def test_the_slug_never_collides_with_a_raw_tool_name():
    adapter = A.map_tool_to_capability(
        {"name": "Some Tool-Name!", "inputSchema": {"type": "object", "properties": {}}},
        server={"name": "s"},
    )
    assert adapter["name"] == "mcp_some_tool_name"
    assert adapter["tool"] == "Some Tool-Name!"
    named = A.map_tool_to_capability(
        {"name": "add", "inputSchema": {"type": "object", "properties": {}}},
        server={"name": "s"},
        name="explicit",
    )
    assert named["name"] == "explicit"
