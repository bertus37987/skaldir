"""``skaldir mcp …`` — the MCP-surface CLI: verbs, exit codes, wiring snippet.

The runner is the same programmatic entry point the product CLI uses
(``main(argv)`` returns the exit code and prints one JSON document on stdout),
so the exit codes are asserted, not described.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

from skaldir import adapter_mcp as A
from skaldir import adapter_mcp_cli as C

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mcp_test_server.py"

VERBS = ("probe", "map", "test", "register", "call")


def server_flags(*extra: str) -> list[str]:
    """The server flags as they must be written AFTER the verb name."""
    return ["--command", f"{sys.executable} {FIXTURE} stdio", "--name", "fixture", *extra]


def run(argv: list[str], capsys) -> tuple[int, dict, str]:
    """``(exit_code, stdout json, the JSON document as raw text)``."""
    code = C.main(argv)
    captured = capsys.readouterr()
    text = captured.out.strip()
    assert text, "the CLI must always print exactly one document on stdout"
    return code, json.loads(text), text


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    return tmp_path / "runtime"


def _stage(runtime: Path, capsys, tool: str = "add_numbers") -> dict:
    code = C.main(["map", *server_flags("--tool", tool), "--runtime", str(runtime)])
    assert code == 0
    return json.loads(capsys.readouterr().out)


# --------------------------------------------------------------------------- #
# probe                                                                         #
# --------------------------------------------------------------------------- #


def test_probe_prints_the_handshake_and_the_tool_list(capsys):
    code, payload, _text = run(["probe", *server_flags()], capsys)
    assert code == 0
    assert payload["ok"] is True
    assert payload["transport"] == "stdio"
    assert payload["protocol_version"] == "2025-06-18"
    assert payload["server_info"]["name"] == "godsv-mcp-fixture"
    assert payload["tools_count"] == 13
    assert {item["name"] for item in payload["tools"]} >= {"add_numbers", "echo_text"}
    assert payload["refused_schema"] == [
        "opaque_blob",
        "freeform_config",
        "missing_required",
        "no_schema",
        "oneof_hetero",
        "recursive_node",
        "deep_freeform",
    ]
    assert payload["warnings"]
    steps = [entry["method"] for entry in payload["transcript"]]
    assert steps == [
        "transport",
        "initialize",
        "response",
        "notifications/initialized",
        "tools/list",
        "response",
        "transport",
    ]


def test_probe_transcript_flag_prints_the_raw_messages(capsys):
    code, payload, _text = run(["probe", *server_flags("--transcript")], capsys)
    assert code == 0
    initialize = payload["transcript"][1]["message"]
    assert initialize["method"] == "initialize"
    assert initialize["params"]["clientInfo"]["name"] == "skaldir-godsv"
    assert payload["transcript"][2]["message"]["result"]["serverInfo"]["name"] == (
        "godsv-mcp-fixture"
    )


def test_probe_exits_3_when_the_server_cannot_be_started(capsys):
    code, payload, _text = run(["probe", "--command", "/nonexistent/mcp-server"], capsys)
    assert code == C.EXIT_BLOCKED == 3
    assert payload["ok"] is False
    assert payload["failure_class"] == "handshake_failed"
    assert "could not be started" in payload["reason"]


def test_probe_exits_3_for_an_unknown_negotiated_protocol(capsys):
    code, payload, _text = run(
        ["probe", "--command", f"{sys.executable} {FIXTURE} stdio --protocol 1999-01-01"], capsys
    )
    assert code == 3
    assert payload["failure_class"] == "protocol_unsupported"


# --------------------------------------------------------------------------- #
# map                                                                           #
# --------------------------------------------------------------------------- #


def test_map_stages_only_the_tools_with_a_typable_schema(runtime, capsys):
    code, payload, _text = run(["map", *server_flags(), "--runtime", str(runtime)], capsys)
    assert code == 0
    assert {item["name"] for item in payload["mapped"]} == {
        "mcp_add_numbers",
        "mcp_echo_text",
        "mcp_server_now",
        "mcp_always_errors",
        "mcp_union_mode",
        "mcp_nested_typed",
    }
    refused = {item["tool"]: item["reason"] for item in payload["refused"]}
    assert set(refused) == {
        "opaque_blob",
        "freeform_config",
        "missing_required",
        "no_schema",
        "oneof_hetero",
        "recursive_node",
        "deep_freeform",
    }
    assert "unknown type 'binary-blob'" in refused["opaque_blob"]
    assert "'oneOf' offers 2 alternatives of different types" in refused["oneof_hetero"]
    assert payload["runtime"] == str(runtime)
    assert all(item["ok"] for item in payload["staged"])
    assert (runtime / "generated_tools" / "mcp_add_numbers" / "adapter.json").is_file()
    assert len(payload["warnings"]) == 7


def test_map_refuses_one_untypable_tool_with_exit_3(runtime, capsys):
    code, payload, _text = run(
        ["map", *server_flags("--tool", "opaque_blob"), "--runtime", str(runtime)], capsys
    )
    assert code == C.EXIT_BLOCKED
    assert payload["ok"] is False
    assert payload["mapped"] == []
    assert payload["refused"] == [
        {
            "tool": "opaque_blob",
            "reason": "tool 'opaque_blob' was refused: 'blob': unknown type 'binary-blob'",
        }
    ]
    assert payload["staged"] == []
    assert not (runtime / "generated_tools" / "mcp_opaque_blob").exists()


def test_map_no_stage_writes_nothing(runtime, capsys):
    code, payload, _text = run(
        ["map", *server_flags("--no-stage"), "--runtime", str(runtime)], capsys
    )
    assert code == 0
    assert payload["mapped"]
    assert payload["staged"] == []
    assert not (runtime / "generated_tools").exists()


def test_map_names_a_tool_the_server_does_not_offer(runtime, capsys):
    code, payload, _text = run(
        ["map", *server_flags("--tool", "ghost"), "--runtime", str(runtime)], capsys
    )
    assert code == C.EXIT_BLOCKED
    assert payload["mapped"] == []
    assert payload["refused"] == [{"tool": "ghost", "reason": "the server did not list this tool"}]


def test_map_without_a_handshake_takes_no_action(capsys):
    code, payload, _text = run(["map", "--command", "/nonexistent/mcp-server"], capsys)
    assert code == 3
    assert payload["mapped"] == [] and payload["staged"] == []
    assert payload["failure_class"] == "handshake_failed"


# --------------------------------------------------------------------------- #
# test → register → call                                                        #
# --------------------------------------------------------------------------- #


def test_test_register_call_round_trip(runtime, capsys):
    _stage(runtime, capsys)

    code, payload, _text = run(
        [
            "test",
            "mcp_add_numbers",
            "--input",
            '{"a": 2, "b": 3}',
            "--expect",
            '{"sum": 5}',
            "--runtime",
            str(runtime),
        ],
        capsys,
    )
    assert code == 0
    assert payload["ok"] is True
    assert payload["observed"] == {"sum": 5}
    assert payload["match"] is True
    assert payload["transport"] == "stdio"
    assert payload["oracle"].startswith("none — a real MCP call")
    assert Path(payload["evidence_path"]).is_file()

    code, payload, _text = run(
        [
            "register",
            "mcp_add_numbers",
            "--runtime",
            str(runtime),
            "--db",
            str(runtime / "tools.db"),
        ],
        capsys,
    )
    assert code == 0
    assert payload["registered"] is True
    assert payload["version"] == "0.1.0"
    assert payload["kind"] == "mcp"
    assert payload["health_state"] == "HEALTHY"
    assert payload["publish"]["created"] is True
    assert payload["warnings"]

    code, payload, _text = run(
        ["call", "mcp_add_numbers", "--input", '{"a": 40, "b": 2}', "--runtime", str(runtime)],
        capsys,
    )
    assert code == 0
    assert payload["ok"] is True
    assert payload["output"] == {"sum": 42}
    assert payload["via"] == "adapter"
    assert payload["raw"]["structuredContent"] == {"sum": 42}


def test_call_via_the_generated_wrapper_script(runtime, capsys):
    _stage(runtime, capsys)
    code, payload, _text = run(
        [
            "call",
            "mcp_add_numbers",
            "--input",
            '{"a": 1, "b": 2}',
            "--via",
            "wrapper",
            "--runtime",
            str(runtime),
        ],
        capsys,
    )
    assert code == 0
    assert payload["via"] == "wrapper"
    assert payload["output"] == {"sum": 3}


def test_test_exits_2_when_the_expectation_does_not_hold(runtime, capsys):
    _stage(runtime, capsys)
    code, payload, _text = run(
        [
            "test",
            "mcp_add_numbers",
            "--input",
            '{"a": 2, "b": 3}',
            "--expect",
            '{"sum": 999}',
            "--runtime",
            str(runtime),
        ],
        capsys,
    )
    assert code == C.EXIT_VERIFICATION_FAILED == 2
    assert payload["ok"] is False
    assert payload["failure_class"] == "expectation_mismatch"
    assert payload["observed"] == {"sum": 5}
    assert "expected 999" in payload["difference"]


def test_test_exits_3_when_the_payload_violates_the_declared_types(runtime, capsys):
    _stage(runtime, capsys)
    code, payload, _text = run(
        ["test", "mcp_add_numbers", "--input", '{"a": "two", "b": 3}', "--runtime", str(runtime)],
        capsys,
    )
    assert code == C.EXIT_BLOCKED
    assert payload["failure_class"] == "invalid_input"
    assert "input 'a' is not an integer" in payload["reason"]


def test_test_exits_5_for_a_capability_that_was_never_staged(tmp_path, capsys):
    code, payload, _text = run(["test", "mcp_ghost", "--runtime", str(tmp_path)], capsys)
    assert code == C.EXIT_NOT_FOUND == 5
    assert payload["failure_class"] == "not_found"


def test_register_exits_2_without_evidence(runtime, capsys):
    _stage(runtime, capsys)
    code, payload, _text = run(
        [
            "register",
            "mcp_add_numbers",
            "--runtime",
            str(runtime),
            "--db",
            str(runtime / "tools.db"),
        ],
        capsys,
    )
    assert code == C.EXIT_VERIFICATION_FAILED == 2
    assert payload["registered"] is False
    assert payload["failure_class"] == "unverified"
    assert "no test evidence" in payload["reason"]


def test_a_bad_json_argument_is_a_usage_error(runtime, capsys):
    _stage(runtime, capsys)
    code, payload, _text = run(
        ["test", "mcp_add_numbers", "--input", "{not json", "--runtime", str(runtime)], capsys
    )
    assert code == C.EXIT_USAGE == 1
    assert payload["failure_class"] == "usage"
    assert "not JSON" in payload["reason"]
    code, payload, _text = run(
        ["test", "mcp_add_numbers", "--input", "[1, 2]", "--runtime", str(runtime)], capsys
    )
    assert code == 1
    assert "must be a JSON object" in payload["reason"]


# --------------------------------------------------------------------------- #
# the parser contract and the cli.py wiring snippet                             #
# --------------------------------------------------------------------------- #


def test_the_standalone_parser_has_exactly_the_five_mcp_verbs():
    parser = C.build_parser()
    subs = [action for action in parser._actions if isinstance(action, argparse._SubParsersAction)][
        0
    ]
    assert set(subs.choices) == set(VERBS)
    for verb in VERBS:
        assert getattr(subs.choices[verb].get_default("func"), "__name__", "").startswith("cmd_")
        assert subs.choices[verb].get_default("command_path") == f"mcp.{verb}"


def _mcp_group_subparsers(parser: argparse.ArgumentParser):
    """The real ``mcp`` subparsers action of ``cli.build_parser()`` (the snippet's target)."""
    top = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)][0]
    assert "mcp" in top.choices, sorted(top.choices)
    return next(a for a in top.choices["mcp"]._actions if isinstance(a, argparse._SubParsersAction))


def test_register_cli_wires_the_verbs_into_the_real_mcp_group():
    """The hand-over snippet, proven against the parser ``cli.py`` really builds.

    ``cli.py`` now calls ``register_cli(mcp_sub)`` itself, and the contract is
    "call it once" (``_add_mcp_verbs`` uses ``sub.add_parser``, so a second call is
    an ``argparse`` conflict — pinned by the test right below). This test therefore
    wires the group **only when it is not wired yet**, so it proves the same two
    things in both worlds: the five verbs land on the real ``mcp`` group and the
    existing ``serve`` verb is untouched.
    """
    from skaldir import cli

    parser = cli.build_parser()
    mcp_sub = _mcp_group_subparsers(parser)
    before = set(mcp_sub.choices)
    assert "serve" in before, "the existing mcp group must keep its verb"

    if not set(VERBS) <= before:
        C.register_cli(mcp_sub)

    assert set(mcp_sub.choices) == before | set(VERBS)
    assert "serve" in mcp_sub.choices
    mapped = mcp_sub.choices["map"]
    assert mapped.get_default("command_path") == "mcp.map"
    assert "command" in {action.dest for action in mapped._actions}
    assert "tool" in {action.dest for action in mapped._actions}
    assert mapped.get_default("func") is C.cmd_map


def _group_verb_sets(parser: argparse.ArgumentParser) -> dict:
    """``{group name: its verb names}`` for every top-level group of a parser."""
    top = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)][0]
    out: dict[str, set] = {}
    for name, group in top.choices.items():
        sub = next((a for a in group._actions if isinstance(a, argparse._SubParsersAction)), None)
        out[name] = set(sub.choices) if sub is not None else set()
    return out


def test_register_cli_does_not_touch_any_other_group():
    from skaldir import cli

    parser = cli.build_parser()
    before = _group_verb_sets(parser)
    if not set(VERBS) <= before["mcp"]:
        # ``cli.py`` already wires this group; the contract is "once" (see above).
        C.register_cli(_mcp_group_subparsers(parser))
    after = _group_verb_sets(parser)
    changed = {name for name in before if before[name] != after[name]}
    assert changed <= {"mcp"}, f"register_cli touched {sorted(changed - {'mcp'})}"
    assert after["mcp"] == before["mcp"] | set(VERBS)
    assert after["tools"] == before["tools"]


def test_a_second_register_cli_call_is_refused_by_argparse_never_silently_accepted():
    parser = argparse.ArgumentParser(prog="probe")
    sub = parser.add_subparsers(dest="mcp_command", required=True)
    C.register_cli(sub)
    assert set(sub.choices) == set(VERBS)
    with pytest.raises(argparse.ArgumentError):
        C.register_cli(sub)


def test_the_contract_entries_cover_every_new_leaf():
    """``cli_contract.COMMANDS`` is owned by the centre: hand the entries over.

    Until they are merged, every leaf must appear in ``EV_NEW_COMMANDS`` —
    otherwise a new command would ship without a contract.
    """
    joined = "\n".join(C.EV_NEW_COMMANDS)
    for verb in VERBS:
        assert f"mcp.{verb}" in joined, f"mcp.{verb} is missing from EV_NEW_COMMANDS"
        assert "EXPAND" in joined


def test_every_failure_class_maps_onto_a_known_exit_code():
    from skaldir import cli_contract

    known = {
        cli_contract.EXIT_OK,
        cli_contract.EXIT_USAGE,
        cli_contract.EXIT_VERIFICATION_FAILED,
        cli_contract.EXIT_BLOCKED,
        cli_contract.EXIT_NEEDS_CREDENTIALS,
        cli_contract.EXIT_NOT_FOUND,
        cli_contract.EXIT_CONFLICT,
        cli_contract.EXIT_INTERNAL,
        cli_contract.EXIT_UNKNOWN,
    }
    assert C.FAILURE_EXIT
    assert set(C.FAILURE_EXIT.values()) <= known
    # the rules this surface is about
    assert C.FAILURE_EXIT["test_failed"] == cli_contract.EXIT_VERIFICATION_FAILED
    assert C.FAILURE_EXIT["unverified"] == cli_contract.EXIT_VERIFICATION_FAILED
    assert C.FAILURE_EXIT["adapter_changed"] == cli_contract.EXIT_VERIFICATION_FAILED
    assert C.FAILURE_EXIT["nothing_mappable"] == cli_contract.EXIT_BLOCKED


def test_text_mode_prints_the_human_line_and_no_envelope(runtime, capsys):
    _stage(runtime, capsys)
    code = C.main(
        [
            "test",
            "mcp_add_numbers",
            "--input",
            '{"a": 2, "b": 2}',
            "--runtime",
            str(runtime),
            "--text",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.startswith("mcp_add_numbers: PASS")
    assert not captured.out.strip().startswith("{")


def test_the_cli_is_a_surface_and_does_not_reimplement_the_transport():
    """The transport lives in ``adapter_mcp``; the CLI must not grow a second one."""
    source = Path(C.__file__).read_text(encoding="utf-8")
    for forbidden in ("jsonrpc", "Mcp-Session-Id", "text/event-stream", "protocolVersion"):
        assert forbidden not in source, f"the CLI reimplements the transport: {forbidden}"
    assert A.MCP_KIND == "mcp"
