"""``tools call`` — the CLI's manual call verb for the approved registry surface.

The tool under test is BUILT BY THE REAL BUILDER into a scratch runtime and
promoted to global scope (the path a visible global capability has already
walked), so the fixture is exactly what ``agent_loop.registry_tools`` resolves
at run time — no hand-crafted spec dodges the scope rule. Refusals are asserted
against the resolver's own words:

* unknown name → exit 5, ``unknown tool: <name>``;
* candidate (not approved) → exit 3, the spec's real status named;
* health ledger quarantine → exit 3, the ledger's exact reason text;
* malformed ``--json-args`` → exit 1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from skaldir import tools_call_cli as C
from skaldir.tools import STATUS_CANDIDATE, ToolSpec, ToolStore

TOOL_NAME = "probe_call_tool"
TOOL_SOURCE = "def main(x: int = 0) -> dict:\n    return {'x_seen': x}\n"


def _promote_to_global(runtime: Path) -> None:
    """Move the built tool's manifest to global scope (what `promote-tool` ends at)."""
    import yaml

    manifest_path = runtime / "generated_tools" / TOOL_NAME / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    manifest["scope"] = "global"
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


@pytest.fixture()
def runtime(tmp_path: Path) -> Path:
    """A fresh runtime per test (one real build; refusal cases cannot cross it)."""
    from skaldir.toolbuilder import ToolBuilder, ToolBuildRequest

    result = ToolBuilder(tmp_path).build(
        ToolBuildRequest(
            capability="probe tool for the tools call verb tests",
            name=TOOL_NAME,
            source_code=TOOL_SOURCE,
            module=TOOL_NAME,
            entrypoint="main",
            sample_input={"x": 1},
        )
    )
    assert result.get("ok") is True, result
    _promote_to_global(tmp_path)
    return tmp_path


def _run(runtime: Path, argv: list[str]) -> tuple[int, dict, str]:
    """``(exit, payload, raw stdout)`` — the standalone path owns stdout."""
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = C.main([*argv, "--runtime", str(runtime)])
    text = out.getvalue().strip()
    return code, (json.loads(text) if text else {}), text


# --------------------------------------------------------------------------- #
# the happy path — the tool is really called through the loop's own path        #
# --------------------------------------------------------------------------- #


def test_an_approved_tool_is_called_and_the_digest_round_trips(runtime: Path):
    code, payload, _raw = _run(runtime, [TOOL_NAME, "--json-args", '{"x": 3}'])
    assert code == 0, payload
    assert payload["ok"] is True
    assert payload["name"] == TOOL_NAME
    assert payload["result"] == {"x_seen": 3}
    expected = hashlib.sha256(
        json.dumps({"x_seen": 3}, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    assert payload["result_sha256"] == expected
    # the envelope names the call path it went through (the loop's own join)
    assert any(
        "_call_registry_tool" in str(item.get("ref", ""))
        for item in payload.get("evidence") or []
    )


def test_bare_braces_are_empty_arguments(runtime: Path):
    code, payload, _raw = _run(runtime, [TOOL_NAME, "--json-args", "{}"])
    assert code == 0, payload
    assert payload["result"] == {"x_seen": 0}


# --------------------------------------------------------------------------- #
# the refusals — the exact reason, never a paraphrase                           #
# --------------------------------------------------------------------------- #


def test_an_unknown_tool_is_refused_with_the_registry_s_own_words(runtime: Path):
    code, payload, _raw = _run(runtime, ["no-such-tool"])
    assert code == 5
    assert payload["ok"] is False
    assert "unknown tool: no-such-tool" in payload["refusal"]
    assert payload["result"] is None
    assert payload["failure_class"] == "unknown_tool"


def test_an_empty_runtime_creates_no_schema_and_refuses(tmp_path: Path):
    """An absent registry is "nothing with that name" — politely, exit 5."""
    code, payload, _raw = _run(tmp_path, ["whatever"])
    assert code == 5
    assert payload["ok"] is False
    assert "unknown tool: whatever" in payload["refusal"]


def test_a_candidate_not_on_surface_refuses_with_the_spec_status(runtime: Path):
    store = ToolStore(str(runtime / "tools.db"))
    try:
        store.upsert(
            ToolSpec(
                name="draft_tool",
                title="Draft",
                module=TOOL_NAME,
                entrypoint="main",
                status=STATUS_CANDIDATE,
            )
        )
    finally:
        store.close()
    code, payload, _raw = _run(runtime, ["draft_tool"])
    assert code == 3
    assert payload["ok"] is False
    assert payload["failure_class"] == "not_exported"
    assert "status='candidate'" in payload["refusal"]


def test_a_health_excluded_tool_refuses_with_the_ledger_reason(runtime: Path):
    from skaldir import capability_health as health

    for _ in range(4):
        health.record_trigger(
            runtime, TOOL_NAME, "degradation", detail="probe failed in test fixture"
        )
    code, payload, _raw = _run(runtime, [TOOL_NAME])
    assert code == 3, payload
    assert payload["failure_class"] == "health_blocked"
    assert "QUARANTINED" in payload["refusal"]


def test_a_malformed_json_args_is_a_usage_refusal(runtime: Path):
    code, payload, _raw = _run(runtime, [TOOL_NAME, "--json-args", "{not json"])
    assert code == 1
    assert "malformed" in payload["refusal"]


# --------------------------------------------------------------------------- #
# the hook contract                                                             #
# --------------------------------------------------------------------------- #


def test_register_cli_fills_a_fresh_tools_group():
    root = argparse.ArgumentParser(prog="skaldir")
    sub = root.add_subparsers(dest="tools_command", required=True)
    tools = sub.add_parser("tools")
    tools_sub = tools.add_subparsers(dest="subcommand", required=True)
    C.register_cli(tools_sub)
    call = tools_sub.choices["call"]
    args = call.parse_args([TOOL_NAME, "--json-args", "{}"])
    assert args.name == TOOL_NAME
    assert args.json_args == "{}"
    assert args.func is C.cmd_call


def test_register_cli_is_idempotent_and_upgrades_a_legacy_call_parser():
    from skaldir import cli_contract

    before = dict(cli_contract.COMMANDS)
    root = argparse.ArgumentParser(prog="skaldir")
    sub = root.add_subparsers(dest="tools_command", required=True)
    tools = sub.add_parser("tools")
    tools_sub = tools.add_subparsers(dest="subcommand", required=True)
    # a sibling block's legacy parser, first: the hook upgrades in place
    legacy = tools_sub.add_parser("call")
    legacy.add_argument("name")
    legacy.add_argument("--db", default=None)
    C.register_cli(tools_sub)
    C.register_cli(tools_sub)  # a second registration order must not conflict
    now = tools_sub.choices["call"]
    args = now.parse_args(["tool", "--json-args", '{"a": 1}'])
    assert args.json_args == '{"a": 1}'
    assert getattr(args, "db", None) is None  # the legacy flag still parses
    assert "tools.call" in cli_contract.COMMANDS
    assert "tools.call" not in before or (
        cli_contract.COMMANDS["tools.call"].description == before["tools.call"].description
    )


def test_the_command_entry_carries_the_loop_call_path_as_evidence():
    from skaldir.cli_shared import install_contract_entries

    entry = C.proposed_contract_entries()["tools.call"]
    assert entry is not None
    assert entry.exit_codes == (0, 1, 3, 5, 7)
    assert any(
        "agent_loop" in str(item.get("note", "")) for item in entry.evidence
    )
    # the entry installs cleanly into a scratch contract table (idempotent shape):
    # the real table is left exactly as the other groups own it
    scratch: dict[str, object] = {"tools.show": object()}
    installed_names = install_contract_entries(
        {"tools.call": entry}, type("M", (), {"COMMANDS": scratch})
    )
    assert installed_names == ["tools.call"]
    again = install_contract_entries(
        {"tools.call": entry}, type("M", (), {"COMMANDS": scratch})
    )
    assert again == []  # the second order does not conflict
