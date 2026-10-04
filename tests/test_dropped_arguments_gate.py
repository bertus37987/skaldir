"""BLOCK-M3-03 — unknown core-tool arguments are REFUSED, never silently dropped.

Defect (scrutiny m3 round 2, measured independently; pre-existing since the
transfer commit 0b09a8a): ``tools_core.files`` advertises ``expected_revision``
in ``read_file``'s registry contract (``descriptor('read_file')``), but
``tools_core.bridge.read_file``'s SIGNATURE omits it — so the model-facing
bound schema lacked the parameter while ``_bound_tool``'s handler filtered
unknown kwargs (``{k: v for k, v in args.items() if k in sig.parameters}``).
Bound core tools did not set ``additionalProperties: False``, so
``_argument_problem`` ACCEPTED the argument and the handler then silently
DROPPED it while reporting ``ok=True``:

* a model asking for a revision-guarded read got an UNGUARDED read with no
  indication (full content, no ``STALE_REFERENCE``), and
* ANY typo'd/unknown argument on ANY bound core tool was silently swallowed.

Pinned here, failing-first (measured at the defect's HEAD through the real
loop; only the LLM provider is stubbed):

* every bridge-bound core tool's declared property SET equals its registry
  contract's property set (and the required sets match), so an advertised
  contract argument is ALWAYS expressible;
* every model-facing tool bound this way declares ``additionalProperties:
  False``, so an unknown argument is refused at the gate (named
  ``invalid_arguments``) instead of dropped with ``ok=True``;
* ``read_file(expected_revision=<wrong sha>)`` through the real loop is a
  NAMED ``STALE_REFERENCE`` refusal with NO content — never an unguarded
  read — while the matching sha reads normally;
* one unknown argument passes through the real loop and is REFUSED
  (``invalid_arguments``, ``executed=False``); the run itself survives.
"""

from __future__ import annotations

import json
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
from skaldir.core_capabilities import (
    _BRIDGE_WRAPPERS,
    _bound_tool,
    core_capability_tools,
)
from skaldir.tools_core import bridge as core_bridge
from skaldir.tools_core.registry import descriptor
from tests.test_agent_loop import answer, tool_step
from tests.test_llm_provider import FAKE_KEY, Step, StubProvider


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


def _result_for(stub: StubProvider, call_id: str) -> dict[str, Any]:
    """The parsed ``role: tool`` payload fed back for one call id."""
    for request in stub.requests:
        for message in reversed(request["body"].get("messages") or []):
            if (message.get("role") == "tool"
                    and str(message.get("tool_call_id")) == call_id):
                return json.loads(str(message.get("content") or ""))
    raise AssertionError(f"no tool result for call {call_id!r}")


# --------------------------------------------------------------------------- #
# (1) bound schema property set == registry contract property set              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", sorted(_BRIDGE_WRAPPERS))
def test_bound_property_set_equals_the_registry_contract(name: str):
    """A contract argument the wrapper's signature omits is a silent contract drift."""
    wrapper = getattr(core_bridge, _BRIDGE_WRAPPERS[name])
    tool = _bound_tool(name, wrapper, Path("/tmp"), None, run_id="")
    contract = descriptor(name)
    assert contract, f"no registry contract published for {name!r}"
    declared = set(tool.input_schema["properties"])
    advertised = set(contract["input_schema"]["properties"])
    assert declared == advertised, (
        f"{name}: the bound model-facing schema offers {sorted(declared)}, "
        f"the registry contract advertises {sorted(advertised)}"
    )
    bound_required = set(tool.input_schema["required"])
    contract_required = set(contract["input_schema"].get("required") or [])
    assert bound_required == contract_required, (
        f"{name}: bound required {sorted(bound_required)} vs "
        f"contract required {sorted(contract_required)}"
    )


def test_read_file_schema_advertises_expected_revision():
    """The guard parameter reached the model surface (the meadured defect's headline)."""
    tool = _bound_tool("read_file", core_bridge.read_file, Path("/tmp"), None, run_id="")
    assert "expected_revision" in tool.input_schema["properties"], tool.input_schema


@pytest.mark.parametrize("name", sorted(_BRIDGE_WRAPPERS))
def test_bound_core_tools_declare_additional_properties_false(name: str):
    """Without it the gate accepts an unknown argument, and the handler drops it."""
    wrapper = getattr(core_bridge, _BRIDGE_WRAPPERS[name])
    tool = _bound_tool(name, wrapper, Path("/tmp"), None, run_id="")
    assert tool.input_schema.get("additionalProperties") is False, tool.input_schema


def test_every_core_capability_tool_declares_additional_properties_false(tmp_path: Path):
    """Also the hand-built core tools (run_code/process/browser) are closed schemas."""
    for tool in core_capability_tools(workspace=tmp_path):
        schema = tool.input_schema or {}
        assert schema.get("additionalProperties") is False, (
            f"{getattr(tool, 'name', '?')!r} accepts unknown arguments silently: {schema}"
        )


def test_an_unknown_argument_is_refused_at_the_gate_not_dropped():
    """The gate names the offending argument and lists what is allowed."""
    tool = _bound_tool("terminal", core_bridge.terminal, Path("/tmp"), None, run_id="")
    problem = _argument_problem(tool.input_schema, {"command": "echo hi", "bogus_flag": True})
    assert problem, "unknown argument passed the gate although the schema is closed"
    assert "bogus_flag" in problem, problem


def test_a_read_file_revision_guard_argument_passes_the_gate():
    """The guard stays usable: the advertised form is accepted (never 'fixed' into refusal)."""
    tool = _bound_tool("read_file", core_bridge.read_file, Path("/tmp"), None, run_id="")
    args = {
        "path": "notes.txt",
        "expected_revision": "a" * 64,
    }
    assert _argument_problem(tool.input_schema, args) == ""


# --------------------------------------------------------------------------- #
# (2) the guard is ENFORCED through the real loop (named error, no unguarded read)
# --------------------------------------------------------------------------- #


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("guarded-content-marker", encoding="utf-8")
    return workspace


def test_stale_revision_read_refuses_by_name_through_the_real_loop(tmp_path: Path):
    """A wrong expected_revision must NOT yield content: STALE_REFERENCE, guarded."""
    workspace = _workspace(tmp_path)
    state = tmp_path / "runtime" / "agent"
    state.mkdir(parents=True)
    steps = [
        Step(body=tool_step(
            ("r1", "read_file",
             {"path": "notes.txt", "expected_revision": "0" * 64}),
        )),
        Step(body=answer("done")),
    ]
    with StubProvider(steps) as stub:
        loop = AgentLoop(
            _llm(stub),
            ToolSet.of(core_capability_tools(workspace=workspace)),
            state_dir=state,
            workspace=workspace,
        )
        result = loop.run("guarded read probe")

    assert result.status == STATUS_ANSWERED, result.message
    payload = _result_for(stub, "r1")
    output = payload.get("result", {}).get("output") or {}
    assert "STALE_REFERENCE" in str(output.get("error") or ""), output
    assert str(output.get("content") or "") == "", (
        "a revision-guarded read that failed verification must not return content"
    )
    assert payload.get("ok") is False or output.get("shape") == "stale", output


def test_matching_revision_read_returns_content_through_the_real_loop(tmp_path: Path):
    """The correct sha is not tripped: the read executes and returns content."""
    import hashlib

    workspace = _workspace(tmp_path)
    state = tmp_path / "runtime" / "agent"
    state.mkdir(parents=True)
    sha = hashlib.sha256((workspace / "notes.txt").read_bytes()).hexdigest()

    steps = [
        Step(body=tool_step((
            "r1", "read_file",
            {"path": "notes.txt", "expected_revision": sha},
        ))),
        Step(body=answer("done")),
    ]
    with StubProvider(steps) as stub:
        loop = AgentLoop(
            _llm(stub),
            ToolSet.of(core_capability_tools(workspace=workspace)),
            state_dir=state,
            workspace=workspace,
        )
        result = loop.run("guarded read, matching sha")

    assert result.status == STATUS_ANSWERED, result.message
    payload = _result_for(stub, "r1")
    output = payload.get("result", {}).get("output") or {}
    assert "guarded-content-marker" in str(output.get("content") or ""), output
    assert output.get("error") is None, output


# --------------------------------------------------------------------------- #
# (3) real-loop refusal proof for one unknown argument                         #
# --------------------------------------------------------------------------- #


def test_unknown_argument_through_the_real_loop_is_refused(tmp_path: Path):
    """terminal(command=..., bogus_flag=True): refused, never silently dropped ok=True."""
    workspace = _workspace(tmp_path)
    state = tmp_path / "runtime" / "agent"
    state.mkdir(parents=True)

    steps = [
        Step(body=tool_step((
            "u1", "terminal",
            {"command": "echo probe-unknown-arg", "bogus_flag": True},
        ))),
        Step(body=answer("refused")),
    ]
    with StubProvider(steps) as stub:
        loop = AgentLoop(
            _llm(stub),
            ToolSet.of(core_capability_tools(workspace=workspace)),
            state_dir=state,
            workspace=workspace,
        )
        result = loop.run("unknown argument refusal probe")

    assert result.status == STATUS_ANSWERED, result.message
    calls = read_transcript(result.transcript_path)["tool_calls"]
    terminal = next(c for c in calls if c["name"] == "terminal")
    assert terminal["decision"] in ("invalid_arguments", "repeated_invalid_arguments"), terminal
    assert terminal["executed"] is False, terminal
    assert terminal.get("ok") is not True, terminal
    error = str(terminal.get("error") or "")
    assert "bogus_flag" in error, error
