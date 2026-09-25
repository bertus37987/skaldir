"""AGENT wave — ``skaldir agent run|transcript`` (the group's own CLI contract).

Two things are proved here:

1. **The commands work.** ``agent run`` drives the loop against the local stub
   endpoint and answers with the product envelope; a bound exits 3 with
   ``data.answer`` null; ``agent transcript <run-id>`` reads the run back.
2. **The hand-over is complete.** ``register_cli`` + ``install_contract_entries``
   added to the *real* ``build_parser()`` leave no command without a
   ``cli_contract.COMMANDS`` entry (``unregistered == []``) — the invariant
   ``tests/test_cli_contract_dump.py`` enforces for the whole tree. The literal
   snippet for ``cli_contract.py`` is parsed as Python and must name the same
   paths.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from test_agent_loop import StubEndpoint, answer, tool_step

from skaldir import agent_loop_cli, cli, cli_contract


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "hello.txt").write_text("hello world\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    directory = tmp_path / "runtime"
    directory.mkdir()
    return directory


@pytest.fixture(autouse=True)
def contracts_snapshot():
    """The group installs its ``COMMANDS`` entries globally — restore them every test.

    Without the restore, a later test that compares ``cli_contract.COMMANDS`` with
    the real parser would see entries for a group that is not wired into ``cli.py``
    yet (the integration pass adds the three lines).
    """
    before = dict(cli_contract.COMMANDS)
    try:
        yield
    finally:
        cli_contract.COMMANDS.clear()
        cli_contract.COMMANDS.update(before)


def _run(capsys, argv: list[str]) -> tuple[int, dict[str, Any], str]:
    code = agent_loop_cli.main(argv)
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if captured.out.strip() else {}), captured.err


def _subparsers(parser: argparse.ArgumentParser) -> argparse.Action:
    for action in parser._actions:  # noqa: SLF001 — argparse has no public API for this
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return action
    raise AssertionError("the parser has no subparsers action")


# --------------------------------------------------------------------------- #
# the group's own surface                                                      #
# --------------------------------------------------------------------------- #


def test_the_standalone_parser_carries_both_verbs():
    parser = agent_loop_cli.build_parser()
    run = parser.parse_args(["run", "do it", "--max-steps", "3", "--tools", "both"])
    assert run.func is agent_loop_cli.cmd_run
    assert run.task == "do it"
    assert run.max_steps == 3
    assert run.tools == "both"
    transcript = parser.parse_args(["transcript", "run-1"])
    assert transcript.func is agent_loop_cli.cmd_transcript
    assert transcript.run_id == "run-1"
    with pytest.raises(SystemExit):
        parser.parse_args(["nope"])


def test_register_cli_is_idempotent_and_wires_the_group(contracts_snapshot):
    parser = cli.build_parser()
    sub = _subparsers(parser)
    first = agent_loop_cli.register_cli(sub)
    second = agent_loop_cli.register_cli(sub)  # a second wiring must not raise
    assert first is second
    assert "agent" in sub.choices


def test_the_real_parser_and_the_contract_agree_after_wiring(contracts_snapshot):
    """The hand-over proof: wiring the group leaves no leaf without a contract."""
    parser = cli.build_parser()
    agent_loop_cli.register_cli(_subparsers(parser))
    leaves = [path for path, _sub in cli_contract.leaf_commands(parser)]
    assert "agent.run" in leaves and "agent.transcript" in leaves
    inventory = cli_contract.dump(parser)
    assert inventory["unregistered"] == []
    by_path = {item["command"]: item for item in inventory["commands"]}
    for path in ("agent.run", "agent.transcript"):
        item = by_path[path]
        assert item["registered"] is True
        assert item["layer"] == "ENFORCE"
        assert item["has_json_flag"] is True
        assert item["data_schema"]["type"] == "object"
        assert item["default_evidence"], f"{path} declares no evidence"
        assert 0 in item["exit_codes"] and 1 in item["exit_codes"]
    assert by_path["agent.run"]["mutates"] is True


def test_the_snippet_for_cli_contract_is_valid_python_and_names_the_same_paths():
    namespace: dict[str, Any] = {
        "_C": lambda layer, evidence, description, **kw: {
            "layer": layer,
            "evidence": evidence,
            "description": description,
            **kw,
        },
        "_ev": lambda ref, kind="code", note="": {"ref": ref, "kind": kind, "note": note},
    }
    text = "COMMANDS = {\n" + agent_loop_cli.CONTRACTS_SNIPPET + "}\n"
    exec(compile(text, "<hand-over>", "exec"), namespace)  # noqa: S102 — data, not code
    pasted = namespace["COMMANDS"]
    assert sorted(pasted) == ["agent.run", "agent.transcript"]
    for entry in pasted.values():
        assert entry["layer"] == "ENFORCE"
        assert entry["evidence"] and entry["data_schema"]["type"] == "object"
    assert "register_cli" in agent_loop_cli.WIRING_SNIPPET
    assert "src/skaldir/cli.py" in agent_loop_cli.WIRING_SNIPPET


# --------------------------------------------------------------------------- #
# `agent run` against the stub endpoint                                        #
# --------------------------------------------------------------------------- #


def test_agent_run_answers_with_the_product_envelope(workspace: Path, runtime: Path, capsys):
    steps = [
        tool_step(("call_1", "read_text", {"path": "hello.txt"})),
        answer("hello.txt holds 'hello world'"),
    ]
    argv = [
        "run",
        "what does hello.txt say?",
        "--json",
        "--workspace",
        str(workspace),
        "--runtime",
        str(runtime),
    ]
    with StubEndpoint(steps) as stub:
        code, document, _err = _run(
            capsys, [*argv, "--base-url", stub.base_url, "--model", "stub-model"]
        )
        raw_bodies = stub.raw_bodies

    assert code == 0
    assert document["ok"] is True
    assert document["command"] == "agent.run"
    data = document["data"]
    assert data["status"] == "answered"
    assert data["answer"] == "hello.txt holds 'hello world'"
    assert data["steps"] == 2
    assert data["tool_calls"] == 1
    assert data["executed_calls"] == 1
    assert data["exit_code"] == 0
    assert data["tools"] == ["read_text", "list_dir", "sha256_file", "write_text"]
    assert data["provider"]["model"] == "stub-model"
    assert Path(data["transcript_path"]).is_file()
    assert document["evidence"], "a success without evidence is a contract violation"
    assert any(item["ref"] == data["transcript_path"] for item in document["evidence"])
    assert "read_text" in raw_bodies[0]  # the tools array was on the wire


def test_a_bound_exits_3_and_the_envelope_reports_no_answer(
    workspace: Path, runtime: Path, capsys
):
    steps = [tool_step(("c", "list_dir", {}), text="working on it")]
    argv = [
        "run",
        "never finish",
        "--json",
        "--max-steps",
        "1",
        "--workspace",
        str(workspace),
        "--runtime",
        str(runtime),
    ]
    with StubEndpoint(steps) as stub:
        code, document, _err = _run(
            capsys, [*argv, "--base-url", stub.base_url, "--model", "stub-model"]
        )
    assert code == 3
    assert document["ok"] is False
    assert document["exit_code"] == 3
    assert document["error"]["code"] == "blocked"
    assert document["error"]["details"]["reason"] == "max_steps"
    assert document["data"]["status"] == "incomplete"
    assert document["data"]["answer"] is None
    assert document["data"]["last_text"] == "working on it"
    assert document["data"]["exit_code"] == 3
    assert document["next_actions"], "a blocked run names what to do next"
    assert "step limit" in document["error"]["message"]
    assert document["next_actions"][0]["command"].startswith("skaldir agent transcript ")
    assert document["evidence"]


def test_agent_transcript_reads_the_run_back(workspace: Path, runtime: Path, capsys):
    steps = [
        tool_step(("call_1", "read_text", {"path": "hello.txt"})),
        answer("done"),
    ]
    with StubEndpoint(steps) as stub:
        code, document, _err = _run(
            capsys,
            [
                "run",
                "read it",
                "--json",
                "--run-id",
                "run-cli-0001",
                "--workspace",
                str(workspace),
                "--runtime",
                str(runtime),
                "--base-url",
                stub.base_url,
                "--model",
                "stub-model",
            ],
        )
    assert code == 0
    run_id = document["data"]["run_id"]
    assert run_id == "run-cli-0001"

    code, document, _err = _run(
        capsys, ["transcript", run_id, "--json", "--runtime", str(runtime)]
    )
    assert code == 0
    data = document["data"]
    assert data["run_id"] == run_id
    assert data["status"] == "answered"
    assert data["tool_call_count"] == 1
    assert data["tool_calls"][0]["name"] == "read_text"
    assert data["tool_calls"][0]["duration_ms"] >= 0
    assert data["requests"][0]["body"]["tools"][0]["function"]["name"] == "read_text"
    assert data["events"][0]["kind"] == "run.started"
    refs = [item["ref"] for item in document["evidence"]]
    assert any(ref.endswith(f"{run_id}.jsonl") for ref in refs), refs
    assert "src/skaldir/agent_loop.py" in refs


def test_an_unknown_run_id_exits_5(runtime: Path, capsys):
    code, document, _err = _run(
        capsys, ["transcript", "run-does-not-exist", "--json", "--runtime", str(runtime)]
    )
    assert code == 5
    assert document["error"]["code"] == "not_found"
    assert "run-does-not-exist" in document["error"]["message"]


def test_a_run_id_that_is_not_a_file_name_is_a_usage_error(
    workspace: Path, runtime: Path, capsys
):
    with StubEndpoint([answer("x")]) as stub:
        code, document, _err = _run(
            capsys,
            [
                "run",
                "task",
                "--json",
                "--run-id",
                "../escape",
                "--workspace",
                str(workspace),
                "--runtime",
                str(runtime),
                "--base-url",
                stub.base_url,
                "--model",
                "stub-model",
            ],
        )
    assert code == 1
    assert document["error"]["code"] == "usage"
    assert "run id" in document["error"]["message"]


def test_an_unpriced_cost_budget_is_a_usage_error(workspace: Path, runtime: Path, capsys):
    code, document, _err = _run(
        capsys,
        [
            "run",
            "task",
            "--json",
            "--cost-budget-usd",
            "0.5",
            "--workspace",
            str(workspace),
            "--runtime",
            str(runtime),
        ],
    )
    assert code == 1
    assert document["error"]["code"] == "usage"
    assert "price" in document["error"]["hint"]


def test_no_provider_configured_exits_4(workspace: Path, runtime: Path, capsys, monkeypatch):
    for name in (
        "SKALDIR_LLM_BASE_URL",
        "SKALDIR_LLM_API_BASE",
        "OPENAI_BASE_URL",
        "SKALDIR_LLM_MODEL",
        "OPENAI_MODEL",
        "GODSV_LLM_BASE_URL",
        "GODSV_LLM_API_BASE",
        "GODSV_LLM_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(runtime / "absent-config.yaml"))
    code, document, err = _run(
        capsys, ["run", "task", "--json", "--workspace", str(workspace), "--runtime",
                 str(runtime)]
    )
    assert code == 4
    assert document["error"]["code"] == "needs_credentials"
    assert "SKALDIR_LLM_BASE_URL" in (document["error"]["hint"] + document["error"]["message"])


def test_the_registry_tool_set_without_a_db_is_blocked(workspace: Path, runtime: Path, capsys):
    with StubEndpoint([answer("x")]) as stub:
        code, document, _err = _run(
            capsys,
            [
                "run",
                "task",
                "--json",
                "--tools",
                "registry",
                "--workspace",
                str(workspace),
                "--runtime",
                str(runtime),
                "--base-url",
                stub.base_url,
                "--model",
                "stub-model",
            ],
        )
    assert code == 3
    assert document["error"]["code"] == "blocked"
    assert document["error"]["details"]["reason"] == "no_registry"
    assert document["next_actions"][0]["command"] == "skaldir tools list"
    assert len(stub.requests) == 0  # no request without a tool surface


def test_a_human_run_prints_the_answer_and_writes_the_transcript(
    workspace: Path, runtime: Path, capsys
):
    with StubEndpoint([answer("plain text answer")]) as stub:
        code = agent_loop_cli.main(
            [
                "run",
                "say hi",
                "--workspace",
                str(workspace),
                "--runtime",
                str(runtime),
                "--base-url",
                stub.base_url,
                "--model",
                "stub-model",
            ]
        )
    captured = capsys.readouterr()
    assert code == 0
    assert "plain text answer" in captured.out
    assert "transcript:" in captured.out
    runs = sorted((runtime / "agent" / "runs").glob("*.jsonl"))
    assert len(runs) == 1


# --------------------------------------------------------------------------- #
# the `agent` group name is shared with the subagent harness                    #
# --------------------------------------------------------------------------- #


def test_the_group_merges_into_a_sibling_agent_group(contracts_snapshot):
    """Two blocks may own the same group name: the verbs must coexist, not collide."""
    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command", required=True)
    sibling = sub.add_parser("agent", help="a sibling block's group")
    sibling_sub = sibling.add_subparsers(dest="agent_command", required=True)
    sibling_sub.add_parser("spawn", help="the sibling's own verb")

    group = agent_loop_cli.register_cli(sub)  # must extend, not re-register
    assert group is sibling
    assert set(sibling_sub.choices) == {"spawn", "run", "transcript"}

    agent_loop_cli.register_cli(sub)  # a third wiring still must not raise
    assert set(sibling_sub.choices) == {"spawn", "run", "transcript"}


def test_the_loop_and_the_subagent_harness_share_the_real_agent_group(contracts_snapshot):
    """The integration step, measured: both groups wired into the REAL parser."""
    subagents_cli = pytest.importorskip("skaldir.subagents_cli")
    parser = cli.build_parser()
    sub = _subparsers(parser)
    agent_loop_cli.register_cli(sub)
    subagents_cli.register_cli(sub)  # the sibling wires itself into the same group
    leaves = [path for path, _sub in cli_contract.leaf_commands(parser)]
    for path in ("agent.run", "agent.transcript"):
        assert path in leaves, (path, leaves)
    sibling_verbs = [path for path in leaves if path.startswith("agent.")]
    assert len(sibling_verbs) >= 3, sibling_verbs  # theirs are there too
    inventory = cli_contract.dump(parser)
    assert inventory["unregistered"] == []
