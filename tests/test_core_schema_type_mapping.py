"""BLOCK-M3-01 — the model-facing schema must match the real argument annotations.

Defect (scrutiny m3-harness, VAL-HARNESS-005 unmet): ``core_capabilities._bound_tool``
derived the JSON schema from raw ``inspect.signature`` annotations.
``tools_core/bridge.py`` declares ``from __future__ import annotations`` (PEP 563),
so every annotation arrives as a STRING and the ``ann in (int, "int")`` comparisons
never matched — every non-str argument fell through to the ``"string"`` default.

Measured consequences at the defect's HEAD:

* ``terminal.timeout_s`` / ``terminal.max_output_bytes`` published as ``"string"``
  although the registry contract (``tools_core/terminal.py``) declares ``integer``;
* ``terminal.env`` and ``web_fetch.headers`` published as ``"string"`` although the
  contracts declare ``object``;
* through the real AgentLoop the correct call ``terminal(timeout_s=30)`` was refused
  by ``_argument_problem`` and then trip-refused (``repeated_invalid_arguments``),
  while the schema-literal string form passed the gate and failed in the registry
  ("expected integer, got str") — no callable form existed for these four arguments;
* ``tool_search`` published the CORRECT contract, so the two discovery surfaces
  disagreed and the obedient model was exactly the one refused.

Pinned here, failing-first (measured before the fix):

* every bridge-bound core tool's declared property type equals the wrapper's REAL
  annotation type (resolved with ``typing.get_type_hints``, PEP 563/604 aware), and
  a correctly typed call passes ``_argument_problem``;
* the wrong string form is refused (no weakening, no narrowed schema);
* the model-facing schema and the registry contracts agree;
* a real AgentLoop run executes ``terminal(timeout_s=30)``.
"""

from __future__ import annotations

import inspect
import sys
import typing
from pathlib import Path
from typing import Any

import pytest

from skaldir.agent_loop import (
    STATUS_ANSWERED,
    AgentLoop,
    ToolSet,
    _argument_problem,
    read_transcript,
)
from skaldir.core_capabilities import _BRIDGE_WRAPPERS, _bound_tool, core_capability_tools
from skaldir.tools_core import bridge as core_bridge
from tests.test_llm_provider import FAKE_KEY, Step, StubProvider

_UNION_ORIGINS = {typing.Union}
_UNION_ORIGINS.add(getattr(sys.modules["types"], "UnionType", None))


def _json_kind(ann: Any) -> str:
    """The JSON-schema type for one REAL annotation — independent of the fix.

    Union members are stripped (``int | None`` is an integer argument; ``None``
    only means "omittable"), then the base type decides. This mirrors the registry
    contracts, not the implementation under test.
    """
    origin = typing.get_origin(ann)
    if origin in _UNION_ORIGINS:
        members = [a for a in typing.get_args(ann) if a is not type(None)]
        if members:
            ann = members[0]
            origin = typing.get_origin(ann)
    if ann is bool:  # before int: bool is an int subclass
        return "boolean"
    if ann is int:
        return "integer"
    if ann is float:
        return "number"
    if ann is str:
        return "string"
    if ann is dict or origin is dict:
        return "object"
    if ann in (list, tuple) or origin in (list, tuple, set, frozenset):
        return "array"
    return "string"


#: a correctly typed sample value per declared JSON type (enum-aware)
_SAMPLES: dict[str, Any] = {
    "string": "probe",
    "integer": 30,
    "number": 1.5,
    "boolean": True,
    "object": {"K": "V"},
    "array": ["a"],
}


def _sample(spec: dict[str, Any]) -> Any:
    if isinstance(spec.get("enum"), (list, tuple)) and spec["enum"]:
        return spec["enum"][0]
    return _SAMPLES[spec.get("type", "string")]


# --------------------------------------------------------------------------- #
# (a) declared type == real annotation type, per bridge wrapper                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("capability", sorted(_BRIDGE_WRAPPERS))
def test_declared_property_type_equals_the_real_annotation_type(capability: str):
    """The published schema is derived from the wrapper's resolved annotations."""
    wrapper = getattr(core_bridge, _BRIDGE_WRAPPERS[capability])
    tool = _bound_tool(capability, wrapper, Path("/tmp"), None, run_id="")
    hints = typing.get_type_hints(wrapper)
    for pname, param in inspect.signature(wrapper).parameters.items():
        if pname in ("self", "root", "runtime", "run_id"):
            continue
        expected = _json_kind(hints.get(pname, param.annotation))
        declared = tool.input_schema["properties"][pname]["type"]
        assert declared == expected, (
            f"{capability}.{pname}: schema says {declared!r}, "
            f"the wrapper's real annotation is {expected!r}"
        )


def test_the_measured_consequences_are_fixed(tmp_path: Path):
    """terminal.timeout_s/max_output_bytes integer, terminal.env/web_fetch.headers object."""
    tools = {t.name: t for t in core_capability_tools(workspace=tmp_path)}
    term = tools["terminal"].input_schema["properties"]
    assert term["timeout_s"]["type"] == "integer", term["timeout_s"]
    assert term["max_output_bytes"]["type"] == "integer", term["max_output_bytes"]
    assert term["env"]["type"] == "object", term["env"]
    fetch = tools["web_fetch"].input_schema["properties"]
    assert fetch["headers"]["type"] == "object", fetch["headers"]
    assert fetch["timeout_s"]["type"] == "integer", fetch["timeout_s"]


# --------------------------------------------------------------------------- #
# (a') the gate accepts the real types and refuses the broken string form       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("capability", sorted(_BRIDGE_WRAPPERS))
def test_a_correctly_typed_call_passes_the_argument_gate(capability: str):
    """Every declared argument, typed as declared, is accepted by _argument_problem."""
    wrapper = getattr(core_bridge, _BRIDGE_WRAPPERS[capability])
    tool = _bound_tool(capability, wrapper, Path("/tmp"), None, run_id="")
    args = {name: _sample(spec) for name, spec in tool.input_schema["properties"].items()}
    problem = _argument_problem(tool.input_schema, args)
    assert problem == "", f"{capability}: correctly typed call refused: {problem}"


def test_the_broken_string_form_is_refused_not_weakened():
    """integer stays integer: the schema-literal string form must NOT pass the gate."""
    tool = _bound_tool("terminal", core_bridge.terminal, Path("/tmp"), None, run_id="")
    problem = _argument_problem(tool.input_schema, {"command": "echo hi", "timeout_s": "30"})
    assert problem, "the published integer type accepted a string argument"


# --------------------------------------------------------------------------- #
# the two discovery surfaces agree                                              #
# --------------------------------------------------------------------------- #


def test_model_facing_schema_agrees_with_the_registry_contracts():
    """tool_search publishes the registry contract; the loop surface must not disagree."""
    from skaldir.tools_core.terminal import INPUT_SCHEMA as TERMINAL_CONTRACT
    from skaldir.tools_core.web import FETCH_INPUT_SCHEMA as WEB_FETCH_CONTRACT

    for name, contract in (
        ("terminal", TERMINAL_CONTRACT),
        ("web_fetch", WEB_FETCH_CONTRACT),
    ):
        tool = _bound_tool(name, getattr(core_bridge, name), Path("/tmp"), None, run_id="")
        declared = tool.input_schema["properties"]
        for pname, spec in contract["properties"].items():
            if "type" not in spec:
                continue
            assert declared[pname]["type"] == spec["type"], (
                f"{name}.{pname}: loop surface says {declared[pname]['type']!r}, "
                f"the registry contract says {spec['type']!r}"
            )


# --------------------------------------------------------------------------- #
# (b) the real loop executes terminal(timeout_s=30)                             #
# --------------------------------------------------------------------------- #


def _config(stub: StubProvider):
    from skaldir.llm_provider import ProviderConfig

    return ProviderConfig(
        provider="openai-compatible",
        base_url=stub.base_url,
        model="stub-model",
        timeout_s=10.0,
        max_retries=0,
        api_key=FAKE_KEY,
    )


def _llm(stub: StubProvider):
    from skaldir.llm_provider import OpenAICompatibleLLM

    return OpenAICompatibleLLM(_config(stub))


def _result_for(body: dict[str, Any], call_id: str) -> dict[str, Any]:
    """The parsed ``role: tool`` payload fed back for one call id."""
    import json as _json

    for message in reversed(body.get("messages") or []):
        if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
            return _json.loads(str(message.get("content") or ""))
    raise AssertionError(f"no tool result for call {call_id!r}")


def test_terminal_timeout_s_integer_call_executes_through_the_real_loop(tmp_path: Path):
    """The correct form runs: executed, ok, and the child's own exit code 0."""
    from tests.test_agent_loop import answer, tool_step

    workspace = tmp_path / "ws"
    workspace.mkdir()
    state = tmp_path / "runtime" / "agent"
    state.mkdir(parents=True)
    steps = [
        tool_step(("t1", "terminal", {"command": "echo skaldir-probe", "timeout_s": 30})),
        answer("the command ran"),
    ]
    with StubProvider([Step(body=s) for s in steps]) as stub:
        loop = AgentLoop(
            _llm(stub),
            ToolSet.of(core_capability_tools(workspace=workspace)),
            state_dir=state,
            workspace=workspace,
        )
        result = loop.run("run one bounded command")

    assert result.status == STATUS_ANSWERED, result.message
    calls = read_transcript(result.transcript_path)["tool_calls"]
    terminal = next(c for c in calls if c["name"] == "terminal")
    assert terminal["decision"] not in ("invalid_arguments", "repeated_invalid_arguments"), (
        terminal["error"]
    )
    assert terminal["executed"] is True, terminal
    assert terminal["ok"] is True, terminal

    # the terminal envelope carries the command's own facts under result.output
    payload = _result_for(stub.requests[1]["body"], "t1")
    output = payload["result"]["output"]
    assert output["exit_code"] == 0, output
    assert "skaldir-probe" in output["stdout"], output
