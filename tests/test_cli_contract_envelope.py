"""P1a — the CLI envelope contract: one shape, stable exit codes, pure JSON.

These are invariant tests, not change detectors: they assert the *rules* the contract
states (MVP/02-cli-contract.md §4), so they keep holding when commands are added.

* every leaf command takes ``--json`` and, with it, writes exactly one JSON envelope to
  stdout (human text moves to stderr — this is the ``tools request`` mixed-stream bug);
* every success envelope carries evidence (a success without evidence is downgraded);
* ``error.code`` always agrees with ``exit_code``;
* argparse usage errors exit 1 (the contract's USAGE), never argparse's 2.
"""

from __future__ import annotations

import json

import pytest

from skaldir import cli, cli_contract

ENVELOPE_FIELDS = {
    "schema_version",
    "ok",
    "command",
    "godsvoice_version",
    "started_at",
    "duration_ms",
    "exit_code",
    "data",
    "error",
    "evidence",
    "warnings",
    "next_actions",
}

#: read-only commands, safe to run in a throwaway runtime
SAMPLES = [
    ["contract", "list"],
    ["contract", "show", "tools.show"],
    ["contract", "dump"],
    ["selftest", "--layer", "ENFORCE"],
    ["completion", "status"],
    ["completion", "history", "--limit", "5"],
    ["tools", "list"],
    ["tools", "show", "nope"],
    ["registry", "status"],
    ["workflows", "list"],
    ["workflows", "show", "open_app_snapshot"],
    ["workflows", "run", "nope"],
    ["cu", "check"],
    ["cu", "demo"],
    ["skills", "list"],
    ["metrics"],
    ["memory-stats"],
    ["memory-search", "timeout"],
    ["tools-hub"],
    ["spend", "allowance"],
    ["eval"],
    ["scan", "pyproject.toml"],
    ["vault", "list"],
]


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """A throwaway runtime so no test touches the installation's state."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("GODSV_HOME", str(home / "godsv"))
    monkeypatch.delenv("GODSV_VAULT_KEY", raising=False)
    monkeypatch.delenv("GODSV_FORMAT", raising=False)
    return home / "godsv"


def _run(capsys, argv: list[str]) -> tuple[int, dict, str]:
    code = cli.main(argv)
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    return code, document, captured.err


def _expected_command(argv: list[str]) -> str:
    """The deepest registered leaf path whose tokens prefix the command line."""
    tokens = [item for item in argv if not item.startswith("-")]
    paths = sorted(
        (path for path, _ in cli_contract.leaf_commands(cli.build_parser())),
        key=lambda path: -len(path.split(".")),
    )
    for path in paths:
        parts = path.split(".")
        if tokens[: len(parts)] == parts:
            return path
    return tokens[0] if tokens else ""


@pytest.mark.parametrize("argv", SAMPLES, ids=[" ".join(item) for item in SAMPLES])
def test_json_stdout_is_exactly_one_envelope(runtime, capsys, argv):
    code, document, _err = _run(capsys, [*argv, "--json"])
    assert set(document) >= ENVELOPE_FIELDS, sorted(document)
    assert document["command"] == _expected_command(argv)
    assert document["exit_code"] == code
    assert document["schema_version"] == cli_contract.SCHEMA_VERSION
    assert document["godsvoice_version"] == cli.__version__
    assert document["ok"] is (code == 0)


@pytest.mark.parametrize("argv", SAMPLES, ids=[" ".join(item) for item in SAMPLES])
def test_error_code_matches_the_exit_code(runtime, capsys, argv):
    code, document, _err = _run(capsys, [*argv, "--json"])
    if code == 0:
        assert document["error"] is None
        return
    assert document["error"] is not None
    assert document["error"]["code"] == cli_contract.EXIT_ERROR_CODES[code]
    assert document["error"]["code"] in cli_contract.ERROR_CODES


@pytest.mark.parametrize("argv", SAMPLES, ids=[" ".join(item) for item in SAMPLES])
def test_success_without_evidence_is_not_possible(runtime, capsys, argv):
    code, document, _err = _run(capsys, [*argv, "--json"])
    if document["ok"]:
        assert document["evidence"], f"{argv} reported success without evidence"
        for item in document["evidence"]:
            assert set(item) == {"ref", "kind", "note"}
            assert item["kind"] in cli_contract.EVIDENCE_KINDS


def test_build_envelope_downgrades_success_without_evidence():
    """The rule is enforced by the builder, not by convention."""
    envelope = cli_contract.build_envelope(
        command="contract.list", exit_code=cli_contract.EXIT_OK, data={"commands": []}
    )
    assert envelope["ok"] is False
    assert envelope["exit_code"] == cli_contract.EXIT_INTERNAL
    assert envelope["error"]["code"] == "internal"
    assert "without evidence" in envelope["error"]["message"]


def test_human_output_stays_the_default(runtime, capsys):
    assert cli.main(["completion", "status"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("state:")
    assert not out.lstrip().startswith("{")


def test_the_mixed_stream_bug_is_gone(runtime, capsys):
    """``tools request --json`` used to print 3 human lines plus a JSON line on stdout."""
    code, document, err = _run(
        capsys,
        ["tools", "request", "--capability", "probe", "--reason", "test", "--json"],
    )
    assert code == 0
    assert document["command"] == "tools.request"
    assert document["data"]["request_id"].startswith("cap-")
    assert "capability request cap-" in err  # the human view moved to stderr


def test_usage_errors_exit_1_not_argparse_2(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["metrics", "--not-a-flag"])
    assert excinfo.value.code == cli_contract.EXIT_USAGE


def test_usage_error_with_json_still_writes_an_envelope(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["metrics", "--json", "--not-a-flag"])
    assert excinfo.value.code == cli_contract.EXIT_USAGE
    document = json.loads(capsys.readouterr().out)
    assert document["exit_code"] == cli_contract.EXIT_USAGE
    assert document["error"]["code"] == "usage"
    assert document["ok"] is False


def test_unknown_command_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["does-not-exist"])
    assert excinfo.value.code == cli_contract.EXIT_USAGE


def test_bare_json_call_describes_the_cli(runtime, capsys):
    code, document, _err = _run(capsys, ["--json"])
    assert code == 0
    assert document["command"] == "skaldir"
    assert document["data"]["leaf_commands"] == len(
        cli_contract.dump(cli.build_parser())["commands"]
    )
    assert "skaldir selftest --json" in [item["command"] for item in document["next_actions"]]


def test_godsv_format_env_switches_to_the_envelope(runtime, monkeypatch, capsys):
    monkeypatch.setenv("GODSV_FORMAT", "json")
    assert cli.main(["completion", "status"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["command"] == "completion.status"


def test_quiet_moves_the_human_view_away(runtime, capsys):
    assert cli.main(["completion", "status", "--json", "--quiet"]) == 0
    captured = capsys.readouterr()
    json.loads(captured.out)
    assert captured.err == ""


def test_handler_exceptions_become_internal_exit_codes(runtime, capsys):
    import argparse

    def boom(_args):
        raise RuntimeError("handler exploded")

    args = argparse.Namespace(command_path="cu.check", json=True, quiet=False)
    code = cli_contract.run_command(args, boom, json_mode=True)
    assert code == cli_contract.EXIT_INTERNAL
    document = json.loads(capsys.readouterr().out)
    assert document["error"]["code"] == "internal"
    assert "RuntimeError" in document["error"]["message"]


def test_exit_code_set_is_complete_and_named():
    assert [
        cli_contract.EXIT_OK,
        cli_contract.EXIT_USAGE,
        cli_contract.EXIT_VERIFICATION_FAILED,
        cli_contract.EXIT_BLOCKED,
        cli_contract.EXIT_NEEDS_CREDENTIALS,
        cli_contract.EXIT_NOT_FOUND,
        cli_contract.EXIT_CONFLICT,
        cli_contract.EXIT_INTERNAL,
        cli_contract.EXIT_UNKNOWN,
    ] == list(range(9))
    assert set(cli_contract.EXIT_NAMES) == set(range(9))
    assert cli_contract.EXIT_ERROR_CODES[cli_contract.EXIT_UNKNOWN] == "unknown"
    # the same set is importable from the CLI module (no bare numbers at call sites)
    assert cli.EXIT_BLOCKED == 3 and cli.EXIT_UNKNOWN == 8
