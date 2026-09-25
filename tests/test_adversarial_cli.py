"""``skaldir adversarial …`` — the wiring, the envelope and the release gate, as a CLI.

What this file pins:

* the group offers three verbs, each with the product CLI's shared flags (``--json`` included)
  and each with a ``COMMANDS`` contract record that :func:`register_cli` installs itself — a
  group that is wired cannot be missing from the dump;
* the group can be wired into a real parser (the CLI owner's one line) without argparse
  conflicts, and installing the entries twice adds nothing;
* ``run`` maps the verdict onto the exit code: PASS 0, FAIL 2, UNKNOWN 3 — ``--block-release``
  is the release decision and refuses the ``--allow-unknown`` waiver (exit 1);
* a run without a target is *blocked* (exit 3), not an empty success, and the ``--json``
  envelope carries the verdict, the defects and the evidence behind them.

Two of these tests drive a real browser (the canary), so they run the suite end to end.
"""

from __future__ import annotations

import json

import pytest

from skaldir import adversarial_cli as adv_cli
from skaldir import cli_contract


@pytest.fixture
def contract_restore():
    """The group installs its ``COMMANDS`` records globally — put the table back afterwards."""
    before = dict(cli_contract.COMMANDS)
    try:
        yield
    finally:
        cli_contract.COMMANDS.clear()
        cli_contract.COMMANDS.update(before)


def test_the_group_offers_three_verbs_with_the_shared_flags(contract_restore):
    import argparse

    root = argparse.ArgumentParser(prog="skaldir")
    sub = root.add_subparsers(dest="command")
    adv_cli.register_cli(sub)
    leaves = {name: leaf for name, leaf in cli_contract.leaf_commands(root)}
    assert sorted(leaves) == ["adversarial.plan", "adversarial.run", "adversarial.selftest"]
    for path, leaf in leaves.items():
        assert "--json" in {flag for action in leaf._actions for flag in action.option_strings}, (
            f"{path} has no --json flag"
        )


def test_register_cli_wires_the_group_into_a_real_parser(contract_restore):
    import argparse

    root = argparse.ArgumentParser(prog="skaldir")
    sub = root.add_subparsers(dest="command")
    parser = adv_cli.register_cli(sub)
    assert parser is not None
    wired = [path for path, _ in cli_contract.leaf_commands(root)]
    assert sorted(wired) == ["adversarial.plan", "adversarial.run", "adversarial.selftest"]
    for path in wired:
        assert cli_contract.registered(path), f"{path} has no contract record"


def test_installing_the_contract_entries_twice_adds_nothing(contract_restore):
    # another test in the same session may already have imported the product CLI (which wires
    # this group and installs these entries), so start from "not installed" explicitly
    for path in list(adv_cli.contract_entries()):
        cli_contract.COMMANDS.pop(path, None)
    first = adv_cli.register_contract_entries()
    assert sorted(first) == ["adversarial.plan", "adversarial.run", "adversarial.selftest"]
    assert adv_cli.register_contract_entries() == []
    for path in first:
        spec = cli_contract.spec_for(path)
        assert spec.layer == "VERIFY"
        assert spec.evidence, f"{path} declares no evidence"
        assert spec.data_schema and spec.data_schema["type"] == "object"
        assert spec.exit_codes


def test_the_entry_declares_that_an_unknown_suite_blocks(contract_restore):
    """The record a GUI renders must not describe UNKNOWN as a success."""
    adv_cli.register_contract_entries()
    spec = cli_contract.spec_for("adversarial.run")
    assert 2 in spec.exit_codes and 3 in spec.exit_codes
    assert "UNKNOWN is never PASS" in spec.notes
    assert spec.mutates is True  # it writes the run report


def test_the_gate_refuses_an_unknown_waiver_before_any_target_starts(capsys):
    code = adv_cli.main(["run", "--block-release", "--allow-unknown", "--json"])
    assert code == 1
    assert "contradict" in capsys.readouterr().err


def test_a_run_without_a_target_is_blocked_not_empty(capsys):
    code = adv_cli.main(["run", "--json"])
    assert code == 3
    captured = capsys.readouterr()
    assert "no target was offered" in captured.err


def test_plan_lists_the_declared_cases():
    assert adv_cli.main(["plan", "--canary", "fixed", "--format", "text"]) == 0


def test_a_fixed_target_passes_and_writes_its_record(tmp_path):
    state = tmp_path / "adv"
    code = adv_cli.main(
        ["run", "--canary", "fixed", "--block-release", "--state", str(state), "--json"]
    )
    assert code == 0
    records = list(state.glob("*.json"))
    assert len(records) == 1
    written = json.loads(records[0].read_text(encoding="utf-8"))
    assert written["verdict"] == "PASS"
    assert written["release_gate"] is True  # the durable record says the gate was passed
    assert written["waived_unknown"] is False
    assert written["counts"]["pass"] == len(written["cases"])


def test_a_broken_target_blocks_the_release_with_exit_two(tmp_path):
    code = adv_cli.main(
        [
            "run",
            "--canary",
            "broken",
            "--block-release",
            "--state",
            str(tmp_path / "adv"),
            "--json",
        ]
    )
    assert code == 2
    written = json.loads(next((tmp_path / "adv").glob("*.json")).read_text(encoding="utf-8"))
    assert written["verdict"] == "FAIL"
    assert written["blocks_release"] is True
    assert written["defect_classes"]


def test_the_json_envelope_carries_the_verdict_and_its_evidence(tmp_path, capsys, contract_restore):
    """The one document a GUI or a pipeline reads, produced through the product envelope."""
    parser = adv_cli.build_parser()
    args = parser.parse_args(
        [
            "run",
            "--canary",
            "broken",
            "--block-release",
            "--state",
            str(tmp_path / "adv"),
            "--json",
        ]
    )
    args.command_path = "adversarial.run"
    code = cli_contract.run_command(args, args.func)
    assert code == 2
    envelope = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert envelope["command"] == "adversarial.run"
    assert envelope["ok"] is False
    assert envelope["exit_code"] == 2
    assert envelope["data"]["verdict"] == "FAIL"
    assert envelope["data"]["blocks_release"] is True
    assert envelope["error"]["code"] == "verification_failed"
    assert envelope["evidence"], "the envelope must carry the evidence behind the verdict"
    assert any(
        str(item["ref"]).startswith("adversarial-suite/") for item in envelope["evidence"]
    )


def test_selftest_proves_the_suite_bites():
    assert adv_cli.main(["selftest", "--slots", "5", "--timeout", "30", "--json"]) == 0


def test_an_unknown_canary_mode_is_a_usage_error():
    with pytest.raises(SystemExit):
        adv_cli.build_parser().parse_args(["run", "--canary", "sometimes"])
