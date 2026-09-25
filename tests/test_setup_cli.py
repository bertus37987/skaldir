"""``skaldir setup`` as a CLI verb: registration, the contract, the exit codes.

The flow itself is proven in :mod:`tests.test_setup_flow`; this file is about the
*surface*. It pins three things the product standard demands — the verb is a real
leaf of ``cli.build_parser()``, it carries a ``cli_contract.COMMANDS`` entry (so
``skaldir contract dump`` has no unregistered command), and its exit codes are the
product's own: ``0`` both keys answered, ``3`` a key is missing, ``4`` a remote
endpoint has no credential, ``2`` a key is configured but broken.

Endpoints here are real ``ThreadingHTTPServer`` sockets on ``127.0.0.1`` (the stub
in ``tests/test_setup_flow.py``); no real credential is needed or used.
"""

from __future__ import annotations

import argparse
import json

import pytest
from test_setup_flow import ENV_NAMES, FAKE_KEY, StubEndpoint, _keys

from skaldir import cli, cli_contract
from skaldir import setup_cli as scli


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch, tmp_path):
    """Neither the developer's shell exports nor the real ~/.hermes may leak in."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SKALDIR_HOME", str(tmp_path / "runtime"))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("GODSV_HOME", raising=False)


@pytest.fixture
def stub():
    server = StubEndpoint()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def stub_jevs():
    server = StubEndpoint(model="stub-jev-model")
    try:
        yield server
    finally:
        server.close()


def _leaf(parser: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
    for action in parser._actions:  # noqa: SLF001 — argparse exposes no public API
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return action.choices[name]
    raise AssertionError("no subparsers on this parser")


def _option_strings(parser: argparse.ArgumentParser) -> set[str]:
    out: set[str] = set()
    for action in parser._actions:
        out.update(action.option_strings)
    return out


# --------------------------------------------------------------------------- #
# registration + the contract                                                  #
# --------------------------------------------------------------------------- #


def test_the_group_registers_on_the_real_product_parser():
    """The hand-over snippet, exercised on the real ``cli.build_parser()``."""
    parser = cli.build_parser()
    setup = _leaf(parser, "setup")

    options = _option_strings(setup)
    for flag in (
        "--jev-base-url", "--jev-model", "--jev-api-key-env",
        "--llm-base-url", "--llm-model", "--llm-api-key-env", "--llm-catalog",
        "--config", "--runtime", "--check", "--state", "--catalog",
        "--json", "--format", "--timeout", "--yes", "--no-input",
    ):
        assert flag in options, f"`skaldir setup` is missing {flag}"
    # the handler is really attached (the leaf is not a stub)
    assert setup.get_default("func") is scli.cmd_setup


def test_the_contract_entry_exists_and_covers_the_verb():
    inventory = cli_contract.dump(cli.build_parser())

    assert "setup" in cli_contract.COMMANDS
    assert inventory["unregistered"] == [], (
        "these commands have no contract entry: " + ", ".join(inventory["unregistered"])
    )
    entry = next(item for item in inventory["commands"] if item["command"] == "setup")
    assert entry["registered"] is True
    assert entry["layer"] in set(cli_contract.LAYERS) | {"kernel"}
    assert entry["mutates"] is True
    assert entry["requires_credentials"] is True
    assert entry["has_json_flag"] is True
    assert set(entry["exit_codes"]) >= {0, 1, 2, 3, 4, 8}
    assert entry["data_schema"]["required"] == ["action", "ok", "mandatory_keys"]
    assert "api_key_env is a NAME" in entry["notes"]


def test_every_handed_over_path_is_registered():
    """``CONTRACT_PATHS`` is the claim; this is the check."""
    scli._assert_registered()  # noqa: SLF001 — the module's own invariant helper
    for path in scli.CONTRACT_PATHS:
        assert path in cli_contract.COMMANDS


def test_the_installer_is_idempotent():
    before = dict(cli_contract.COMMANDS)
    added = scli.install_contract()

    assert added == ()  # already present — the installer adds nothing twice
    assert set(cli_contract.COMMANDS) == set(before)


def test_the_handed_over_entry_matches_the_pasted_snippet():
    """The snippet in the module and the object it installs describe one command."""
    entry = scli.contract_entry()
    assert entry is not None
    assert entry.description == scli.DESCRIPTION
    assert entry.notes == scli.NOTES
    assert entry.data_schema == scli.DATA_SCHEMA
    assert entry.mutates is True and entry.requires_credentials is True
    # the snippet names the same paths the installer adds
    for path in scli.CONTRACT_PATHS:
        assert f'"{path}":' in scli.CONTRACTS_SNIPPET


def test_the_standalone_parser_has_the_same_arguments():
    """Every argument of the verb is the same in both spellings.

    The product leaf additionally carries the CLI's shared flags (``--quiet``,
    ``--no-color``), which are attached to every group by ``cli.py``; the verb's own
    arguments must be identical.
    """
    product = _option_strings(_leaf(cli.build_parser(), "setup"))
    standalone = _option_strings(scli.build_parser())

    assert standalone <= product
    assert product - standalone == {"--quiet", "--no-color"}


# --------------------------------------------------------------------------- #
# the exit codes, through the CLI                                              #
# --------------------------------------------------------------------------- #


def test_check_exits_three_before_setup_and_prints_the_state(tmp_path, capsys):
    code = scli.main(["--check", "--json", "--config", str(tmp_path / "config.yaml")])

    assert code == scli.EXIT_BLOCKED
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "check"
    assert payload["ok"] is False
    assert payload["roles"]["typesafe"]["present"] is False
    assert "run `skaldir setup`" in payload["roles"]["typesafe"]["problem"]


def test_state_exits_three_with_nothing_configured_and_names_both_keys(tmp_path, capsys):
    code = scli.main(["--state", "--json", "--runtime", str(tmp_path)])

    assert code == scli.EXIT_BLOCKED
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "state"
    assert payload["configured"] is False
    missing = payload["degraded"]["missing_keys"]
    assert len(missing) == 2
    assert any("TypeSafe" in item for item in missing)
    assert any("LLM" in item for item in missing)


def test_the_full_run_writes_verifies_and_then_check_exits_zero(
    tmp_path, stub, stub_jevs, capsys
):
    typesafe, llm = _keys(stub, stub_jevs)
    target = tmp_path / "config.yaml"

    code = scli.main(
        [
            "--jev-base-url", typesafe.base_url,
            "--jev-model", typesafe.model,
            "--jev-api-key-env", typesafe.api_key_env,
            "--llm-base-url", llm.base_url,
            "--llm-model", llm.model,
            "--llm-api-key-env", llm.api_key_env,
            "--config", str(target),
            "--yes", "--json",
        ]
    )

    assert code == scli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["wrote"] is True
    assert payload["verify"]["llm_completion"]["ok"] is True
    assert payload["verify"]["jev_decision"]["status"] == "ok"
    assert target.is_file()
    assert FAKE_KEY not in target.read_text()

    # and the check agrees, through the CLI, with no extra traffic to write
    check = scli.main(["--check", "--json", "--config", str(target)])
    assert check == scli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_a_literal_key_is_refused_with_exit_three_and_never_printed(
    tmp_path, capsys, stub, stub_jevs
):
    code = scli.main(
        [
            "--jev-base-url", stub_jevs.base_url,
            "--jev-model", "m",
            "--jev-api-key-env", "STUB_JEV_KEY",
            "--llm-base-url", stub.base_url,
            "--llm-model", "m",
            "--llm-api-key-env", FAKE_KEY,
            "--config", str(tmp_path / "config.yaml"),
            "--yes", "--json",
        ]
    )

    assert code == scli.EXIT_BLOCKED
    captured = capsys.readouterr()
    assert FAKE_KEY not in captured.out
    assert FAKE_KEY not in captured.err
    assert not (tmp_path / "config.yaml").exists()


def test_a_remote_endpoint_without_a_credential_exits_four(tmp_path, capsys, monkeypatch):
    """No request may even be attempted on this path."""
    def _boom(*_args, **_kwargs):
        raise AssertionError("no HTTP request may be attempted on this path")

    monkeypatch.setattr("httpx.Client.post", _boom)
    monkeypatch.setattr("httpx.Client.stream", _boom)

    code = scli.main(
        [
            "--jev-base-url", "https://api.example.com/v1",
            "--jev-model", "jev-latest",
            "--jev-api-key-env", "ABSENT_JEV_KEY",
            "--llm-base-url", "https://api.example.com/v1",
            "--llm-model", "gpt-4o-mini",
            "--llm-api-key-env", "ABSENT_LLM_KEY",
            "--config", str(tmp_path / "config.yaml"),
            "--yes", "--json",
        ]
    )

    assert code == scli.EXIT_NEEDS_CREDENTIALS
    payload = json.loads(capsys.readouterr().out)
    assert "ABSENT_JEV_KEY" in json.dumps(payload)
    assert not (tmp_path / "config.yaml").exists()


def test_headless_env_variables_drive_the_cli(monkeypatch, tmp_path, capsys, stub, stub_jevs):
    monkeypatch.setenv("SKALDIR_SETUP_JEV_BASE_URL", stub_jevs.base_url)
    monkeypatch.setenv("SKALDIR_SETUP_JEV_MODEL", "stub-jev-model")
    monkeypatch.setenv("SKALDIR_SETUP_JEV_API_KEY_ENV", "STUB_JEV_KEY")
    monkeypatch.setenv("SKALDIR_SETUP_LLM_BASE_URL", stub.base_url)
    monkeypatch.setenv("SKALDIR_SETUP_LLM_MODEL", "stub-model")
    monkeypatch.setenv("SKALDIR_SETUP_LLM_API_KEY_ENV", "STUB_LLM_KEY")
    monkeypatch.setenv("STUB_JEV_KEY", FAKE_KEY)
    monkeypatch.setenv("STUB_LLM_KEY", FAKE_KEY)
    target = tmp_path / "config.yaml"

    code = scli.main(["--yes", "--json", "--config", str(target)])

    assert code == scli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["roles"]["llm"]["model_echo"] == "stub-model"
    assert target.is_file()


def test_no_write_validates_and_stops(tmp_path, stub, stub_jevs, capsys):
    typesafe, llm = _keys(stub, stub_jevs)
    target = tmp_path / "config.yaml"

    code = scli.main(
        [
            "--jev-base-url", typesafe.base_url, "--jev-model", typesafe.model,
            "--jev-api-key-env", typesafe.api_key_env,
            "--llm-base-url", llm.base_url, "--llm-model", llm.model,
            "--llm-api-key-env", llm.api_key_env,
            "--config", str(target), "--yes", "--no-write", "--json",
        ]
    )

    assert code == scli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["wrote"] is False
    assert not target.exists()


# --------------------------------------------------------------------------- #
# the human view                                                               #
# --------------------------------------------------------------------------- #


def test_the_text_view_names_both_keys_and_never_prints_a_key(tmp_path, capsys):
    code = scli.main(["--format", "text", "--runtime", str(tmp_path), "--yes"])

    assert code == scli.EXIT_BLOCKED
    text = capsys.readouterr().out
    assert "TypeSafe / Jev provider" in text
    assert "LLM provider" in text
    assert "--jev-base-url" in text and "--llm-base-url" in text
    assert FAKE_KEY not in text


def test_the_text_view_shows_the_measured_facts_after_a_run(
    tmp_path, stub, stub_jevs, capsys
):
    typesafe, llm = _keys(stub, stub_jevs)
    code = scli.main(
        [
            "--jev-base-url", typesafe.base_url, "--jev-model", typesafe.model,
            "--jev-api-key-env", typesafe.api_key_env,
            "--llm-base-url", llm.base_url, "--llm-model", llm.model,
            "--llm-api-key-env", llm.api_key_env,
            "--config", str(tmp_path / "config.yaml"),
            "--format", "text", "--yes",
        ]
    )

    assert code == scli.EXIT_OK
    text = capsys.readouterr().out
    assert "model echo stub-model" in text
    assert "model echo stub-jev-model" in text
    assert "streaming yes" in text
    assert "structured native" in text
    assert "completion:   ok" in text
    assert "jev decision: ok" in text
    assert "secret scan:  clean" in text


def test_the_catalog_view_lists_the_pickable_providers(capsys):
    code = scli.main(["--catalog", "--json"])

    assert code == scli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "catalog"
    assert payload["count"] >= 4
    assert "deepseek" in payload["order"]
