"""``skaldir security audit`` — the command's contract, its exit codes, its envelope.

The command is the deliverable: the audit only counts as shipped when it is
re-runnable, machine-readable and exits by what it found. These tests pin the four
outcomes the product contract defines — ``0`` clean, ``2`` a finding at or above
``--fail-on``, ``3`` blocked because nothing was scanned, ``1`` usage — plus the
envelope's shape (one JSON document on stdout, evidence non-empty, the class filter
honoured) and the fact that the human view never carries a secret.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skaldir import security_audit as A
from skaldir import security_audit_cli as C

REPO = Path(A.__file__).resolve().parents[2]


def envelope(capsys, argv: list[str]) -> tuple[int, dict]:
    """Run the standalone cluster and return ``(exit_code, envelope)``."""
    code = C.main(argv)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    return code, payload


# --------------------------------------------------------------------------- #
# the wiring contract                                                           #
# --------------------------------------------------------------------------- #


def test_register_cli_adds_audit_and_coverage_commands() -> None:
    import argparse

    root = argparse.ArgumentParser(prog="skaldir")
    sub = root.add_subparsers(dest="command")
    parser = C.register_cli(sub)
    paths = sorted(path for path, _ in _leaves(root, ""))
    assert parser is not None
    assert paths == ["security", "security.audit", "security.coverage"], paths


def _leaves(parser, prefix: str):
    import argparse

    for action in parser._actions:  # noqa: SLF001 — argparse has no public API here
        if not isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            continue
        if not action.choices:
            continue
        for name, child in action.choices.items():
            path = f"{prefix}.{name}" if prefix else name
            nested = list(_leaves(child, path))
            yield (path, child)
            yield from nested


def test_the_parser_keeps_the_contract_flags_on_every_verb() -> None:
    parser = C.build_parser()
    audit = parser.parse_args(["audit", "--json", "--path", "src", "--fail-on", "medium"])
    assert audit.func is C.cmd_audit
    assert audit.command_path == "security.audit"
    assert audit.json is True
    assert audit.path == ["src"]
    assert audit.fail_on == "medium"


def test_the_verb_is_registered_with_the_product_contract_module() -> None:
    """``cli_contract`` imports cleanly, so the envelope half is reachable."""
    from skaldir import cli_contract

    assert cli_contract.EXIT_VERIFICATION_FAILED == 2
    assert hasattr(cli_contract, "run_command")


def test_an_unknown_class_is_a_usage_error(capsys) -> None:
    code, payload = envelope(capsys, ["audit", "--json", "--class", "nonsense"])
    assert code == C.EXIT_USAGE
    assert payload["exit_code"] == 1
    assert "unknown class" in json.dumps(payload["error"])


def test_a_missing_root_is_blocked_not_clean(capsys, tmp_path: Path) -> None:
    """A clean verdict about nothing is the failure mode this exit code prevents."""
    code, payload = envelope(capsys, ["audit", "--json", "--path", str(tmp_path / "absent")])
    assert code == C.EXIT_BLOCKED
    assert payload["exit_code"] == 3
    assert "nothing" in json.dumps(payload["error"])


# --------------------------------------------------------------------------- #
# the audit behind the command                                                  #
# --------------------------------------------------------------------------- #


def test_audit_reports_the_classes_and_exits_zero_when_nothing_blocks(
    capsys, tmp_path: Path
) -> None:
    clean = tmp_path / "clean.py"
    clean.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    code, payload = envelope(
        capsys, ["audit", "--json", "--path", str(clean), "--no-live", "--fail-on", "high"]
    )
    assert code == C.EXIT_OK, payload.get("error")
    data = payload["data"]
    assert data["totals"]["findings"] == 0
    assert set(data["classes_audited"]) == set(A.CLASSES)
    assert data["classes_clean"] == list(A.CLASSES)
    assert payload["evidence"]


def test_a_finding_at_the_failure_level_exits_two(capsys, tmp_path: Path) -> None:
    dirty = tmp_path / "dirty.py"
    dirty.write_text("import os\nos.system('rm -rf ' + target)\n", encoding="utf-8")
    code, payload = envelope(
        capsys, ["audit", "--json", "--path", str(dirty), "--no-live", "--fail-on", "high"]
    )
    assert code == C.EXIT_VERIFICATION_FAILED
    assert payload["exit_code"] == 2
    data = payload["data"]
    assert data["blocking_at_fail_on"] == 1
    finding = data["findings"][0]
    assert finding["rule"] == "OS-SYSTEM"
    assert finding["class"] == "command_injection"
    assert finding["line"] == 2
    assert "subprocess.run" in finding["fix"]
    assert payload["next_actions"], "a blocking finding must offer the next command"


def test_the_class_filter_narrows_the_report(capsys, tmp_path: Path) -> None:
    mixed = tmp_path / "mixed.py"
    mixed.write_text(
        "import os, yaml\nos.chmod(path, 0o777)\nyaml.load(text)\n", encoding="utf-8"
    )
    _code, payload = envelope(
        capsys,
        ["audit", "--json", "--path", str(mixed), "--no-live", "--class", "permissions",
         "--fail-on", "none"],
    )
    classes = {item["class"] for item in payload["data"]["findings"]}
    assert classes == {"permissions"}
    assert payload["data"]["filter"]["classes"] == ["permissions"]


def test_min_severity_drops_the_quiet_findings(capsys, tmp_path: Path) -> None:
    quiet = tmp_path / "quiet.py"
    quiet.write_text("import traceback\nprint(traceback.format_exc())\n", encoding="utf-8")
    _code, low = envelope(capsys, ["audit", "--json", "--path", str(quiet), "--no-live",
                                   "--fail-on", "none"])
    assert [item["rule"] for item in low["data"]["findings"]] == ["TRACEBACK-TO-USER"]
    _code, high = envelope(capsys, ["audit", "--json", "--path", str(quiet), "--no-live",
                                    "--min-severity", "high", "--fail-on", "none"])
    assert high["data"]["findings"] == []
    assert any("filtered out" in warning for warning in high["warnings"])


def test_list_rules_ships_the_catalogue_without_scanning(capsys) -> None:
    code, payload = envelope(capsys, ["audit", "--json", "--list-rules"])
    assert code == C.EXIT_OK
    rules = payload["data"]["rules"]
    assert len(rules) == len(A.RULES)
    ids = {item["id"] for item in rules}
    assert {"SHELL-C-INTERPOLATION", "WRITE-THEN-CHMOD", "TOCTOU-CHECK-USE"} <= ids
    assert payload["evidence"]


def test_the_live_runtime_checks_report_the_machine_they_ran_on(
    capsys, tmp_path: Path
) -> None:
    runtime = tmp_path / "godsv"
    (runtime / "generated_tools").mkdir(parents=True)
    (runtime / "vault.json").write_text("{}", encoding="utf-8")
    (runtime / "vault.json").chmod(0o664)
    code, payload = envelope(
        capsys, ["audit", "--json", "--path", str(tmp_path / "absent"), "--runtime", str(runtime)]
    )
    # the scan root does not exist, so the run is blocked — never reported as clean
    assert code == C.EXIT_BLOCKED
    source = tmp_path / "src.py"
    source.write_text("x = 1\n", encoding="utf-8")
    _code, payload = envelope(
        capsys, ["audit", "--json", "--path", str(source), "--runtime", str(runtime),
                 "--fail-on", "none"]
    )
    data = payload["data"]
    assert data["environment"]["runtime_exists"] is True
    assert data["environment"]["runtime_mode"] == "0o700" or data["environment"]["runtime_mode"]
    assert any(item["rule"] == "RUNTIME-PERMISSIONS" for item in data["findings"])
    assert payload["evidence"]


def test_the_human_view_is_a_table_of_classes_and_findings(capsys, tmp_path: Path) -> None:
    dirty = tmp_path / "dirty.py"
    dirty.write_text("import os\nos.system('x')\n", encoding="utf-8")
    code = C.main(["audit", "--path", str(dirty), "--no-live", "--text", "--fail-on", "none"])
    captured = capsys.readouterr()
    assert code == C.EXIT_OK
    assert "by class:" in captured.err
    assert "command_injection" in captured.err
    assert "OS-SYSTEM" in captured.err


def test_the_report_never_prints_a_secret_value(capsys, tmp_path: Path) -> None:
    secret = "ghp_" + "d" * 36
    leaky = tmp_path / "leaky.py"
    leaky.write_text(f'TOKEN = "{secret}"\n', encoding="utf-8")
    code, payload = envelope(
        capsys, ["audit", "--json", "--path", str(leaky), "--no-live", "--fail-on", "none"]
    )
    assert code == C.EXIT_OK
    assert secret not in json.dumps(payload)
    assert any(item["rule"] == "SECRET-LITERAL" for item in payload["data"]["findings"])


def test_json_mode_puts_one_document_on_stdout_and_the_text_on_stderr(
    capsys, tmp_path: Path
) -> None:
    dirty = tmp_path / "dirty.py"
    dirty.write_text("import os\nos.system('x')\n", encoding="utf-8")
    C.main(["audit", "--json", "--path", str(dirty), "--no-live", "--fail-on", "none"])
    captured = capsys.readouterr()
    json.loads(captured.out)  # stdout is exactly one JSON document
    assert captured.out.count("\n") == 1
    assert "by class:" in captured.err


def test_no_live_says_the_runtime_checks_were_skipped(capsys, tmp_path: Path) -> None:
    source = tmp_path / "src.py"
    source.write_text("x = 1\n", encoding="utf-8")
    _code, payload = envelope(
        capsys, ["audit", "--json", "--path", str(source), "--no-live", "--fail-on", "none"]
    )
    assert any("live runtime checks" in warning for warning in payload["warnings"])
    assert payload["data"]["environment"] == {}


def test_the_repo_audit_matches_the_shipped_rules(capsys) -> None:
    """The command over this tree: the counts are the shipped reality, not a claim."""
    code, payload = envelope(
        capsys,
        ["audit", "--json", "--path", str(REPO / "src"), "--path", str(REPO / "integrations"),
         "--fail-on", "high"],
    )
    data = payload["data"]
    assert data["files_scanned"] > 100
    assert data["rules"] == len(A.RULES)
    assert code == C.EXIT_OK  # no finding at or above high remains in the tree
    assert data["totals"]["by_class"], "an audit with zero findings over 100+ files is suspicious"


@pytest.mark.parametrize("level", ["info", "low", "medium", "high", "critical", "none"])
def test_every_fail_on_level_is_accepted(capsys, level: str) -> None:
    code, payload = envelope(
        capsys, ["audit", "--json", "--path", str(REPO / "src"), "--no-live", "--fail-on", level]
    )
    assert payload["data"]["fail_on"] == level
    assert code in (C.EXIT_OK, C.EXIT_VERIFICATION_FAILED)
