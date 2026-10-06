"""P1a — ``skaldir contract``: the self-description the GUI renders from.

Invariant (MVP/02 §4.5): the dumped inventory and ``build_parser()`` must agree —
count and names. A new command cannot ship without a contract entry, and a GUI never
has to hardcode a command list again.
"""

from __future__ import annotations

import json

import pytest

from skaldir import cli, cli_contract


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("GODSV_HOME", str(home / "godsv"))
    monkeypatch.delenv("GODSV_FORMAT", raising=False)
    return home / "godsv"


def test_the_dump_covers_every_leaf_of_the_real_parser():
    parser = cli.build_parser()
    leaves = [path for path, _sub in cli_contract.leaf_commands(parser)]
    inventory = cli_contract.dump(parser)
    dumped = [item["command"] for item in inventory["commands"]]
    assert sorted(dumped) == sorted(leaves), (
        "the dump and build_parser() disagree — a command was added or renamed without "
        "its contract entry"
    )
    assert inventory["leaf_commands"] == len(leaves)
    assert inventory["parser_nodes"] == sum(1 for _ in cli_contract.walk_parsers(parser))


def test_every_command_has_a_contract_entry_with_a_layer_and_a_schema():
    inventory = cli_contract.dump(cli.build_parser())
    assert inventory["unregistered"] == [], (
        "these commands have no contract entry: " + ", ".join(inventory["unregistered"])
    )
    for item in inventory["commands"]:
        assert item["registered"] is True
        assert item["layer"] in set(cli_contract.LAYERS) | {"kernel"}
        assert isinstance(item["data_schema"], dict) and item["data_schema"]["type"]
        assert item["exit_codes"] and all(code in range(9) for code in item["exit_codes"])
        assert isinstance(item["default_evidence"], list)
        assert item["args"], f"{item['command']} has no args recorded"


def test_every_command_takes_json_and_the_dump_says_so():
    inventory = cli_contract.dump(cli.build_parser())
    for item in inventory["commands"]:
        assert item["has_json_flag"] is True, f"{item['command']} has no --json flag"


def test_the_args_in_the_dump_match_the_real_parser_arguments():
    parser = cli.build_parser()
    inventory = cli_contract.dump(parser)
    for path, sub in cli_contract.leaf_commands(parser):
        record = next(item for item in inventory["commands"] if item["command"] == path)
        expected = cli_contract.args_of(sub)
        assert [arg["name"] for arg in record["args"]] == [arg["name"] for arg in expected]
        assert [arg["type"] for arg in record["args"]] == [arg["type"] for arg in expected]


def test_every_successful_command_has_at_least_one_default_evidence_ref():
    inventory = cli_contract.dump(cli.build_parser())
    for item in inventory["commands"]:
        if 0 in item["exit_codes"]:
            assert item["default_evidence"], (
                f"{item['command']} may exit 0 but declares no evidence source"
            )


def test_contract_dump_json_is_one_envelope_with_the_inventory(runtime, capsys):
    code = cli.main(["contract", "dump", "--json"])
    assert code == 0
    document = json.loads(capsys.readouterr().out)
    assert document["command"] == "contract.dump"
    data = document["data"]
    assert data["leaf_commands"] == len(data["commands"]) > 0
    assert data["schema_version"] == cli_contract.SCHEMA_VERSION
    assert data["unregistered"] == []
    assert document["evidence"]


def test_contract_dump_can_write_a_file(runtime, capsys, tmp_path):
    target = tmp_path / "inventory.json"
    assert cli.main(["contract", "dump", "--out", str(target), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["leaf_commands"] == document["data"]["leaf_commands"]
    assert str(target) in [item["ref"] for item in document["evidence"]]


def test_contract_list_filters_by_layer_and_keeps_the_envelope(runtime, capsys):
    assert cli.main(["contract", "list", "--layer", "ENFORCE", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["data"]["count"] == len(document["data"]["commands"]) > 0
    assert {item["layer"] for item in document["data"]["commands"]} == {"ENFORCE"}


def test_contract_show_returns_the_single_record(runtime, capsys):
    assert cli.main(["contract", "show", "tools.show", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["data"]["command"] == "tools.show"
    assert document["data"]["layer"] == "EXPAND"
    assert document["data"]["data_schema"]


def test_contract_show_unknown_command_is_not_found(runtime, capsys):
    code = cli.main(["contract", "show", "nope.nope", "--json"])
    assert code == cli_contract.EXIT_NOT_FOUND
    document = json.loads(capsys.readouterr().out)
    assert document["error"]["code"] == "not_found"


def test_every_command_declares_evidence_as_ref_kind_note_objects():
    """VAL-UI-003: envelope evidence is a list of ``{ref, kind, note}`` objects.

    Measured defect (2026-10-06): 31 ``surface_cli.contract_entries()`` commands passed a
    bare string where ``CommandSpec.evidence`` wants dicts, so ``run_command``'s
    ``list(spec.evidence)`` char-split the JSON envelope's evidence into
    ``["s", "r", "c", ...]`` — and ``contract dump``'s ``default_evidence`` the same way.
    This invariant stays in the suite so a new command cannot ship with string evidence.
    """
    cli.build_parser()
    bad = {}
    for path, entry in cli_contract.COMMANDS.items():
        evidence = list(entry.evidence)
        broken = [
            item for item in evidence
            if not (
                isinstance(item, dict)
                and isinstance(item.get("ref"), str) and item["ref"]
                and isinstance(item.get("kind"), str)
                and isinstance(item.get("note"), str)
            )
        ]
        if broken:
            bad[path] = [repr(item) for item in broken]
    assert bad == {}, (
        "commands whose evidence items are not ref/kind/note objects (a bare string here "
        "char-splits into single characters in the --json envelope and in the dump): "
        + ", ".join(sorted(bad))
    )


def test_contract_dump_reports_object_evidence_for_every_command(runtime, capsys):
    """The dump self-description carries the same rule for a GUI consumer."""
    assert cli.main(["contract", "dump", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    bad = {
        item["command"]: item["default_evidence"]
        for item in document["data"]["commands"]
        if not all(
            isinstance(evi, dict) and isinstance(evi.get("ref"), str) and evi["ref"]
            and isinstance(evi.get("kind"), str)
            for evi in item["default_evidence"]
        )
    }
    assert bad == {}, "default_evidence with non-object items: " + ", ".join(sorted(bad))


def test_compat_mirrors_are_declared_not_hidden():
    """Two legacy commands mirror their payload at top level for pre-contract consumers."""
    mirrored = sorted(
        command for command, spec in cli_contract.COMMANDS.items() if spec.compat_top_level_keys
    )
    assert mirrored == ["registry.status", "tools.promote-global"]
    for command in mirrored:
        assert "compat_top_level_keys" in cli_contract.spec_for(command).as_dict()
        assert cli_contract.spec_for(command).notes
