"""The core toolset *as a registered capability*: registry, health, CLI wiring.

What is proven here is the difference between a library and a capability:

* ``tools-core register`` publishes one real ``tools.db`` row per tool (kind
  ``python``, status ``approved``, the limits in its tags), writes the scope
  manifest the runtime's scope filter reads, and writes the descriptor holding
  the full typed contract;
* the existing registry surfaces see them — ``tools list --json`` really counts
  them, ``registry status`` resolves the same runtime;
* a call goes through the registry gate (an unregistered runtime refuses) and
  through the in-process bridge convention the MCP surface and the agent loop use;
* ``tools-core smoke`` runs every declared smoke case for real and records the
  observation in the ``capability_health`` ledger, so the tools are ``HEALTHY``
  and auto-routable afterwards — and the health reader reports a *removed* tool
  as unverified again;
* the CLI group installs its own contract entries, so ``cli.py`` needs the 3-line
  snippet and nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skaldir import cli, cli_contract
from skaldir.tools_core import cli as core_cli
from skaldir.tools_core import contract as C
from skaldir.tools_core import registry


@pytest.fixture()
def runtime(tmp_path):
    path = tmp_path / "rt"
    path.mkdir()
    return path


@pytest.fixture()
def workspace(tmp_path):
    path = tmp_path / "ws"
    path.mkdir()
    return path


@pytest.fixture()
def registered(runtime):
    report = registry.register(runtime, probe_network=False)
    assert report["ok"] is True
    return report


# ------------------------------------------------------------------ registry


def test_register_publishes_one_approved_row_per_tool(runtime, registered):
    from skaldir.tools import STATUS_APPROVED, ToolStore

    store = ToolStore(runtime / "tools.db")
    try:
        specs = store.all()
    finally:
        store.close()
    assert sorted(spec.name for spec in specs) == sorted(registry.names())
    assert registered["count"] == 9
    for spec in specs:
        assert spec.kind == registry.REGISTRY_KIND == "python"
        assert spec.status == STATUS_APPROVED
        assert spec.module == registry.BRIDGE_MODULE
        assert spec.entrypoint == spec.name
        assert spec.provenance == "core:tools_core"
        assert "core" in spec.tags
        contract = registry.contract_for(spec.name)
        assert f"timeout:{contract.timeout_s}s" in spec.tags
        assert f"truncate:{contract.truncation.max_bytes}b" in spec.tags
        assert spec.sample_input == contract.smoke["input"]
        assert spec.test_command[-3:] == ["--name", spec.name, "--json"]
        assert spec.rationale and spec.scan_summary


def test_register_writes_the_scope_manifest_and_the_full_descriptor(runtime, registered):
    for row in registered["registered"]:
        manifest = Path(row["manifest"])
        descriptor = Path(row["descriptor"])
        assert manifest.is_file() and descriptor.is_file()
        text = manifest.read_text(encoding="utf-8")
        assert "scope: global" in text
        assert "status: approved" in text
        payload = json.loads(descriptor.read_text(encoding="utf-8"))
        assert payload["name"] == row["name"]
        assert payload["kind"] == "core"
        assert payload["permissions"]["filesystem"]["mode"]
        assert payload["truncation"]["max_bytes"] > 0
        assert payload["bridge"]["module"] == registry.BRIDGE_MODULE
        assert payload["bridge"]["entrypoint"] == row["name"]
        assert sorted(payload["input_schema"]) == [
            "additionalProperties",
            "properties",
            "required",
            "type",
        ]


def test_register_is_idempotent_and_does_not_bump_the_registry_revision(runtime):
    from skaldir.tools import ToolStore

    first = registry.register(runtime, probe_network=False)
    store = ToolStore(runtime / "tools.db")
    try:
        revision = store.registry_revision()
    finally:
        store.close()
    second = registry.register(runtime, probe_network=False)
    assert {row["action"] for row in second["registered"]} == {"unchanged"}
    store = ToolStore(runtime / "tools.db")
    try:
        assert store.registry_revision() == revision
        assert store.count() == 9
    finally:
        store.close()
    assert first["count"] == second["count"] == 9


def test_a_foreign_row_with_a_core_name_is_a_conflict(runtime):
    from skaldir.tools import ToolSpec, ToolStore

    store = ToolStore(runtime / "tools.db")
    try:
        store.upsert(ToolSpec(name="terminal", title="someone else's terminal", kind="composition"))
    finally:
        store.close()
    report = registry.register(runtime, probe_network=False)
    assert [item["name"] for item in report["conflicts"]] == ["terminal"]
    assert report["ok"] is False
    assert "terminal" not in [row["name"] for row in report["registered"]]


def test_the_registered_entrypoints_really_resolve(runtime, registered):
    import importlib

    bridge = importlib.import_module(registry.BRIDGE_MODULE)
    for name in registry.names():
        assert callable(getattr(bridge, name))


def test_unregister_takes_the_toolset_back_out_and_retires_health(runtime, registered):
    manifest = runtime / "generated_tools" / "terminal" / "manifest.yaml"
    descriptor = runtime / "tools_core" / "terminal.json"
    assert manifest.is_file() and descriptor.is_file()
    report = registry.unregister(runtime)
    assert report["count"] == 9
    assert not manifest.exists() and not descriptor.exists()
    assert not (runtime / "generated_tools" / "terminal").exists()
    from skaldir import capability_health

    assert capability_health.file_state(runtime, "terminal")["state"] == "RETIRED"


# -------------------------------------------------- the ordinary registry surface


def test_tools_list_really_shows_the_core_toolset(runtime, registered, capsys, monkeypatch):
    monkeypatch.setenv("GODSV_HOME", str(runtime))
    assert cli.main(["tools", "list", "--runtime", str(runtime), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    data = document["data"]
    assert data["count"] == 9
    assert sorted(row["name"] for row in data["tools"]) == sorted(registry.names())
    assert {row["kind"] for row in data["tools"]} == {"python"}
    assert {row["status"] for row in data["tools"]} == {"approved"}
    assert {row["scope"] for row in data["tools"]} == {"global"}
    assert data["tools_db"] == str(runtime / "tools.db")


def test_registry_status_resolves_the_same_runtime(runtime, registered, capsys, monkeypatch):
    monkeypatch.setenv("GODSV_HOME", str(runtime))
    assert cli.main(["registry", "status", "--runtime", str(runtime), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    payload = document["data"]
    assert payload["tool_specs"] == 9
    assert sorted(payload["tool_names"]) == sorted(registry.names())
    assert {entry["scope"] for entry in payload["generated_tools"]} == {"global"}


def test_the_mcp_export_sees_the_core_tools(runtime, registered):
    """The existing python-tool export path picks them up — no edit anywhere else."""
    from skaldir.mcpserver import approved_python_specs
    from skaldir.tools import ToolStore

    store = ToolStore(runtime / "tools.db")
    try:
        specs = approved_python_specs(store, runtime=runtime, project_id="")
        assert sorted(spec.name for spec in specs) == sorted(registry.names())
        # and the wrapper the MCP surface hands to a client really calls the tool
        from skaldir.mcpserver import _wrapper_for

        wrapper = _wrapper_for(store.get("list_dir"), base_dir=runtime)
    finally:
        store.close()
    output = wrapper({"path": "."})
    assert output["tool"] == "list_dir"
    assert output["ok"] is True


# ------------------------------------------------------------------- call gate


def test_a_call_refuses_an_unregistered_runtime(runtime, workspace):
    result = registry.call(
        "terminal", {"command": "true"}, runtime=runtime, root=workspace, db=runtime / "tools.db"
    )
    assert result.ok is False and result.error_kind == "not_found"
    assert result.exit_code == C.EXIT_NOT_FOUND
    assert "not registered" in result.error


def test_the_bridge_is_the_loop_and_mcp_call_convention(runtime, workspace, monkeypatch):
    """The registered entrypoint runs the real tool with the runtime's defaults."""
    monkeypatch.setenv("SKALDIR_HOME", str(runtime))
    monkeypatch.setenv("SKALDIR_TOOL_WORKSPACE", str(workspace))
    registry.register(runtime, probe_network=False)
    from skaldir.tools_core import bridge

    written = bridge.write_file("from-bridge.txt", "hello\n")
    assert written["ok"] is True and written["output"]["verified"] is True
    read = bridge.read_file("from-bridge.txt")
    assert read["output"]["content"] == "1|hello"
    searched = bridge.search_files("hello")
    assert searched["output"]["count"] == 1
    listed = bridge.list_dir(".")
    assert listed["output"]["count"] == 1
    patched = bridge.patch("from-bridge.txt", "hello", "goodbye")
    assert patched["output"]["applied"] is True
    ran = bridge.terminal("printf bridge-ok")
    assert ran["output"]["stdout"] == "bridge-ok"
    assert bridge.web_fetch("file:///etc/passwd")["error_kind"] == "blocked"
    assert bridge.web_search("x", backend="nope")["error_kind"] == "usage"


def test_an_unknown_tool_is_not_found_and_lists_the_known_ones(runtime):
    result = registry.call("nope", {}, runtime=runtime)
    assert result.exit_code == C.EXIT_NOT_FOUND
    assert result.data["known"] == list(registry.names())


# ---------------------------------------------------------------------- smoke


def test_smoke_runs_every_local_case_and_records_health(runtime, registered):
    report = registry.smoke(runtime)
    local = [row for row in report["tools"] if not row["network"]]
    assert len(local) == 6
    assert all(row["ok"] is True for row in local)
    assert report["skipped"] == ["web_fetch", "web_search", "docker.ensure"]
    assert report["ok"] is True
    for row in local:
        assert row["health_state"] == "HEALTHY"
        assert row["checks"] and all(check["ok"] for check in row["checks"])
        assert len(row["result_sha256"]) == 64


def test_smoke_names_a_failing_check_instead_of_hiding_it(runtime, registered, monkeypatch):
    """A check that cannot pass must fail the smoke case, not the whole tool."""
    broken = registry.contract_for("list_dir")
    table = dict(registry.contracts())
    table["list_dir"] = C.ToolContract(
        **{**broken.__dict__, "smoke": {"input": {"path": "."}, "expect": {"min_count": 99}}}
    )
    monkeypatch.setattr(registry, "contracts", lambda: table)
    report = registry.smoke(runtime, name="list_dir")
    assert report["failed"] == 1
    assert report["ok"] is False
    assert report["tools"][0]["health_state"] in {"BROKEN", "DEGRADED", "QUARANTINED"}
    failing = [check for check in report["tools"][0]["checks"] if not check["ok"]]
    assert failing and failing[0]["check"] == "min_count"


def test_smoke_network_cases_are_skipped_unless_asked_for(runtime, registered):
    report = registry.smoke(runtime, name="web_search")
    assert report["tools"][0]["ok"] is None
    assert "network smoke was not requested" in report["tools"][0]["skipped"]
    assert report["ok"] is False  # nothing was measured — not a pass


def test_smoke_of_an_unknown_tool_is_reported(runtime, registered):
    report = registry.smoke(runtime, name="nope")
    assert report["unknown"] == ["nope"]
    assert report["count"] == 0


# --------------------------------------------------------------------- health


def test_health_reader_reports_the_ledger(runtime, registered):
    registry.smoke(runtime)
    view = registry.health(runtime)
    assert view["count"] == 9
    assert view["ledger_available"] is True
    states = {row["name"]: row["state"] for row in view["tools"]}
    assert states["terminal"] == "HEALTHY"
    assert states["web_search"] == "CANDIDATE"  # skipped: never verified
    assert view["healthy"][:0] == [] or set(view["healthy"]) <= set(registry.names())
    assert set(view["unverified"]) | set(view["healthy"]) == set(registry.names())
    # the runtime's own health reader agrees
    from skaldir import capability_health

    assert capability_health.file_state(runtime, "terminal")["auto_routable"] is True
    assert capability_health.selectable(runtime, "terminal")[0] is True


def test_health_never_claims_a_tool_is_healthy_after_it_was_retired(runtime, registered):
    from skaldir import capability_health

    registry.smoke(runtime, name="terminal")
    assert capability_health.file_state(runtime, "terminal")["state"] == "HEALTHY"
    capability_health.retire(runtime, "terminal", reason="test")
    view = registry.health(runtime, names_=("terminal",))
    assert view["tools"][0]["state"] == "RETIRED"
    assert view["tools"][0]["auto_routable"] is False


# ------------------------------------------------------------- the CLI surface


def test_the_cli_group_installs_its_own_contract_entries():
    """The 3-line snippet is enough: the entries come with register_cli()."""
    saved = dict(cli_contract.COMMANDS)
    try:
        parser = cli_contract.ContractArgumentParser(prog="skaldir")
        sub = parser.add_subparsers(dest="command")
        core_cli.register_cli(sub)
        paths = [path for path, _sub in cli_contract.leaf_commands(parser)]
        assert paths == [
            "tools-core.list",
            "tools-core.show",
            "tools-core.register",
            "tools-core.call",
            "tools-core.smoke",
            "tools-core.health",
            "tools-core.policy",
            "tools-core.unregister",
        ]
        cli_contract.annotate(parser)
        inventory = cli_contract.dump(parser)
        assert inventory["unregistered"] == []
        for item in inventory["commands"]:
            assert item["layer"] == "EXPAND"
            assert item["registered"] is True
            assert item["data_schema"]["type"] == "object"
            assert item["has_json_flag"] is True
            assert item["default_evidence"] or 0 not in item["exit_codes"]
        entry = cli_contract.spec_for("tools-core.call")
        assert entry.mutates is True
        assert 8 in entry.exit_codes and 3 in entry.exit_codes and 5 in entry.exit_codes
        assert "exit 8" in entry.notes and "3" in entry.notes
    finally:
        cli_contract.COMMANDS.clear()
        cli_contract.COMMANDS.update(saved)


def test_register_cli_is_idempotent_on_the_same_subparsers():
    saved = dict(cli_contract.COMMANDS)
    try:
        parser = cli_contract.ContractArgumentParser(prog="skaldir")
        sub = parser.add_subparsers(dest="command")
        first = core_cli.register_cli(sub)
        second = core_cli.register_cli(sub)
        assert first is second
        assert len([name for name in sub.choices if name == "tools-core"]) == 1
    finally:
        cli_contract.COMMANDS.clear()
        cli_contract.COMMANDS.update(saved)


def test_standalone_cli_drives_the_verbs_without_the_product_cli(runtime, tmp_path, capsys):
    """``python -m skaldir.tools_core …`` — the group before cli.py is wired."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert (
        core_cli.main(["register", "--runtime", str(runtime), "--no-network-probe", "--json"]) == 0
    )
    document = json.loads(capsys.readouterr().out)
    assert document["count"] == 9

    assert core_cli.main(["list", "--runtime", str(runtime), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 9

    assert core_cli.main(["show", "terminal", "--runtime", str(runtime), "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["input_schema"]["required"] == ["command"]
    assert shown["permissions"]["subprocess"]["allowed"] is True

    code = core_cli.main(
        [
            "call",
            "terminal",
            "--input",
            json.dumps({"command": "printf cli-ok"}),
            "--runtime",
            str(runtime),
            "--workspace",
            str(workspace),
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["output"]["stdout"] == "cli-ok"


def test_standalone_cli_maps_the_failures_onto_exit_codes(runtime, tmp_path, capsys):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    core_cli.main(["register", "--runtime", str(runtime), "--no-network-probe", "--json"])
    capsys.readouterr()

    # unknown tool → 5
    assert core_cli.main(["show", "nope", "--runtime", str(runtime), "--json"]) == 5
    capsys.readouterr()
    # broken JSON input → 1
    assert (
        core_cli.main(["call", "terminal", "--input", "{oops", "--runtime", str(runtime), "--json"])
        == 1
    )
    capsys.readouterr()
    # a refused path → 3
    assert (
        core_cli.main(
            [
                "call",
                "read_file",
                "--input",
                json.dumps({"path": "/etc/passwd"}),
                "--runtime",
                str(runtime),
                "--workspace",
                str(workspace),
                "--json",
            ]
        )
        == 3
    )
    capsys.readouterr()
    # a command that exits non-zero is a successful observation (exit 0) …
    code = core_cli.main(
        [
            "call",
            "terminal",
            "--input",
            json.dumps({"command": "exit 4"}),
            "--runtime",
            str(runtime),
            "--workspace",
            str(workspace),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["output"]["exit_code"] == 4
    # … unless the caller asks for the child's status explicitly
    code = core_cli.main(
        [
            "call",
            "terminal",
            "--input",
            json.dumps({"command": "exit 4"}),
            "--fail-on-nonzero",
            "--runtime",
            str(runtime),
            "--workspace",
            str(workspace),
            "--json",
        ]
    )
    assert code == 4


def test_standalone_smoke_and_health_report_honestly(runtime, capsys):
    core_cli.main(["register", "--runtime", str(runtime), "--no-network-probe", "--json"])
    capsys.readouterr()
    assert core_cli.main(["smoke", "--runtime", str(runtime), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["passed"] == 6 and report["failed"] == 0
    assert report["skipped"] == ["web_fetch", "web_search", "docker.ensure"]
    assert core_cli.main(["health", "--runtime", str(runtime), "--json"]) == 0
    health = json.loads(capsys.readouterr().out)
    assert health["counts"].get("HEALTHY") == 6
    # an unknown --name is a not-found, not an empty success
    assert core_cli.main(["smoke", "--name", "nope", "--runtime", str(runtime), "--json"]) == 5
    capsys.readouterr()


def test_the_committed_implementation_refuses_a_missing_script_argument(runtime, capsys):
    """argparse refuses before any tool runs (usage, not a crash)."""
    assert core_cli.main(["call", "--runtime", str(runtime)]) == 1
    capsys.readouterr()
