"""``skaldir host …`` — the group's verbs, envelope and exit codes, proven.

The runner is the same programmatic entry point the product CLI uses
(``cli_contract.run_command``), so the envelope and the exit codes are asserted,
not described. The backend is the stub from ``test_host_bridge``; ``register_cli``
is exercised exactly as ``cli.build_parser()`` calls it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from skaldir import cli_contract
from skaldir import host_bridge as HB
from skaldir import host_cli as C

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_host_bridge import FAKE_TABLE, StubBackend, _write_fake_hermes  # noqa: E402

VERBS = ("status", "sessions", "skills", "cron", "tools", "doctor")


@pytest.fixture
def stub():
    backend = StubBackend()
    try:
        yield backend
    finally:
        backend.close()


@pytest.fixture(autouse=True)
def hermetic(tmp_path, monkeypatch):
    """Same hermeticity as the bridge suite: no live host, no real ``hermes``."""
    empty_proc = tmp_path / "proc"
    empty_proc.mkdir(exist_ok=True)
    monkeypatch.setattr(HB, "_proc_root", lambda: empty_proc)
    monkeypatch.delenv(HB.ENV_URL, raising=False)
    monkeypatch.delenv(HB.ENV_TOKEN, raising=False)
    fake = _write_fake_hermes(tmp_path)
    monkeypatch.setattr(HB, "hermes_binary", lambda explicit=None: explicit or fake)
    return fake


# --------------------------------------------------------------------------- #
# the wiring the product CLI uses                                               #
# --------------------------------------------------------------------------- #
def _build_parser():
    """A real product-shaped parser: subparsers handle + register_cli + annotate."""
    parser = cli_contract.ContractArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    C.register_cli(sub)
    cli_contract.annotate(parser)
    return parser


def _run(argv: list[str], capsys) -> tuple[int, dict, str]:
    """``(exit_code, envelope, stderr)`` — stdout must carry exactly one document."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    code = cli_contract.run_command(args, getattr(args, "func", None))
    captured = capsys.readouterr()
    text = captured.out.strip()
    assert text, "the CLI must always print exactly one JSON document on stdout"
    return code, json.loads(text), captured.err


def test_register_cli_is_idempotent_and_returns_the_existing_group():
    parser = cli_contract.ContractArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    first = C.register_cli(sub)
    second = C.register_cli(sub)  # a second call must not raise "conflicting subparser"
    assert first is second
    assert sorted(sub.choices) == ["host"]


def test_every_host_leaf_has_a_contract_entry():
    inventory = cli_contract.dump(_build_parser())
    leaves = [
        item["command"] for item in inventory["commands"]
        if item["command"].startswith("host.")
    ]
    assert sorted(leaves) == [
        "host.cron",
        "host.doctor",
        "host.memory.search",
        "host.sessions",
        "host.skills",
        "host.status",
        "host.tools",
    ]
    assert inventory["unregistered"] == []
    for item in inventory["commands"]:
        if not item["command"].startswith("host."):
            continue
        assert item["registered"] is True
        assert item["layer"] == "kernel"
        assert item["mutates"] is False, "the host bridge exposes no write verb"
        assert item["has_json_flag"] is True
        assert item["data_schema"]["type"] == "object"
        assert item["args"]
        assert sorted(item["exit_codes"]) == [0, 1, 3, 4, 7]


def test_the_contract_declares_no_write_verb_and_names_the_surfaces():
    entries = C.contract_entries()
    assert set(entries) == {
        "host.status",
        "host.sessions",
        "host.memory.search",
        "host.skills",
        "host.cron",
        "host.tools",
        "host.doctor",
    }
    for entry in entries.values():
        assert entry.mutates is False
        assert entry.evidence, "every command's answer names the code it was read from"


# --------------------------------------------------------------------------- #
# the happy paths (against the stub backend)                                     #
# --------------------------------------------------------------------------- #
def test_status_json_is_one_envelope_with_the_live_backend(stub, capsys):
    code, envelope, _err = _run(
        ["host", "status", "--url", stub.url, "--token", "stub-loopback-token", "--json"], capsys
    )
    assert code == 0
    assert envelope["ok"] is True
    assert envelope["command"] == "host.status"
    assert envelope["exit_code"] == 0
    assert envelope["data"]["running"] is True
    assert envelope["data"]["backend"]["url"] == stub.url
    assert envelope["error"] is None
    assert envelope["evidence"], "a run records which backend answered"


def test_sessions_json_names_the_request_and_the_profile(stub, capsys):
    code, envelope, _err = _run(
        ["host", "sessions", "--url", stub.url, "--token", "stub-loopback-token",
         "--limit", "2", "--json"], capsys
    )
    assert code == 0
    assert envelope["data"]["profile"] == "default"
    assert envelope["data"]["request"].startswith("GET /api/sessions?")
    assert envelope["data"]["sessions"]
    assert envelope["data"]["fragile"] is False


def test_memory_search_json_reports_the_index_it_used(stub, capsys):
    code, envelope, _err = _run(
        ["host", "memory", "search", "penpot", "--url", stub.url,
         "--token", "stub-loopback-token", "--json"],
        capsys,
    )
    assert code == 0
    assert envelope["data"]["query"] == "penpot"
    assert envelope["data"]["results"][0]["snippet"] == "found penpot"


@pytest.mark.parametrize("argv", [["skills"], ["cron"], ["tools"]])
def test_the_flat_lists_exit_zero_through_the_envelope(stub, capsys, argv):
    code, envelope, _err = _run(
        ["host", *argv, "--url", stub.url, "--token", "stub-loopback-token", "--json"], capsys
    )
    assert code == 0
    assert envelope["data"]["count"] >= 1
    assert envelope["data"]["fragile"] is False


def test_doctor_json_probes_and_reports(stub, capsys):
    code, envelope, _err = _run(
        ["host", "doctor", "--url", stub.url, "--token", "stub-loopback-token", "--json"], capsys
    )
    assert code == 0
    assert envelope["data"]["blocked"] == {}
    probed = {entry["path"] for entry in envelope["data"]["routes"]}
    assert probed >= {"/api/health", "/api/sessions"}


def test_sessions_via_cli_is_fragile_and_warns(hermetic, capsys):
    code, envelope, _err = _run(["host", "sessions", "--via", "cli", "--json"], capsys)
    assert code == 0
    assert envelope["data"]["fragile"] is True
    assert envelope["data"]["transport"] == "cli-text"
    assert envelope["data"]["sessions"][0]["id"] == "20260101_120000_abcdef"
    assert any("FRAGILE" in warning for warning in envelope["warnings"])
    assert FAKE_TABLE.splitlines()[0].startswith("Title")


# --------------------------------------------------------------------------- #
# the honest failures                                                            #
# --------------------------------------------------------------------------- #
def test_blocked_is_exit_3_with_the_reason_and_no_fake_answer(capsys):
    code, envelope, _err = _run(["host", "status", "--url", "http://127.0.0.1:1", "--json"], capsys)
    assert code == 3
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "blocked"
    assert "no host backend answers" in envelope["error"]["message"]
    assert envelope["data"]["running"] is False
    assert envelope["data"]["installed"] is True


def test_a_feature_without_a_backend_is_blocked_not_empty(capsys):
    code, envelope, _err = _run(
        ["host", "sessions", "--url", "http://127.0.0.1:1", "--json"], capsys
    )
    assert code == 3
    assert envelope["error"]["code"] == "blocked"
    assert envelope["data"] is None, "a blocked read must not pretend it read an empty store"


def test_missing_host_is_exit_4_with_the_needs_setup_note(monkeypatch, capsys):
    monkeypatch.setattr(HB, "hermes_binary", lambda explicit=None: None)
    code, envelope, _err = _run(["host", "status", "--json"], capsys)
    assert code == 4
    assert envelope["error"]["code"] == "needs_credentials"  # the contract's name for 4
    assert "host installation" in envelope["error"]["details"]["needs"]
    assert "needs-setup" in envelope["error"]["details"]["contract_code_4"]


def test_missing_host_is_exit_4_for_doctor_too(monkeypatch, capsys):
    monkeypatch.setattr(HB, "hermes_binary", lambda explicit=None: None)
    code, envelope, _err = _run(["host", "doctor", "--json"], capsys)
    assert code == 4
    assert envelope["data"]["installed"] is False


def test_an_empty_memory_query_is_a_usage_error(capsys):
    code, envelope, _err = _run(["host", "memory", "search", "  ", "--json"], capsys)
    assert code == 1
    assert envelope["error"]["code"] == "usage"
    assert "query is required" in envelope["error"]["message"]


def test_a_zero_or_negative_limit_is_a_usage_error(stub, capsys):
    code, envelope, _err = _run(
        ["host", "sessions", "--limit", "0", "--url", stub.url, "--json"], capsys
    )
    assert code == 1
    assert ">= 1" in envelope["error"]["message"]


def test_the_cli_never_prints_human_text_into_the_json_document(stub, capsys):
    """In --json mode the human lines go to stderr; stdout stays one document."""
    code, envelope, err = _run(
        ["host", "skills", "--url", stub.url, "--token", "stub-loopback-token", "--json"], capsys
    )
    assert code == 0
    assert envelope["command"] == "host.skills"
    assert "skill(s) from" in err, "the human summary is on stderr, not in stdout"


# --------------------------------------------------------------------------- #
# the standalone runner and the parser shape                                     #
# --------------------------------------------------------------------------- #
def test_the_standalone_runner_accepts_the_same_verbs_without_the_host_word():
    parser = C.build_parser()
    for verb in VERBS:
        args = parser.parse_args([verb])
        assert getattr(args, "func", None) is not None, verb
    args = parser.parse_args(["memory", "search", "text"])
    assert args.func is C.cmd_memory_search


def test_every_leaf_takes_the_json_flag_and_the_host_flags():
    parser = C.build_parser()
    for argv in (["status"], ["sessions"], ["memory", "search", "x"], ["skills"], ["cron"],
                 ["tools"], ["doctor"]):
        args = parser.parse_args(
            [*argv, "--json", "--url", "http://x", "--token", "t", "--timeout", "1.5"]
        )
        assert args.json is True
        assert args.url == "http://x"
        assert args.token == "t"
        assert C._timeout(args) == 1.5
