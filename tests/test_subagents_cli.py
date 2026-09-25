"""``skaldir agent …`` — the subagent harness CLI surface and its contract entries.

These tests drive the **real** envelope (``cli_contract.run_command``) and, for the
hand-over, the **real** ``cli.build_parser()`` tree: the five leaf paths, their
``COMMANDS`` entries, the exit codes (0 all delivered / 2 partial / 3 no provider /
1 usage / 5 unknown run / 8 wait deadline), and that in ``--json`` mode stdout
carries exactly one document while the streamed events go to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path

import pytest

from skaldir import cli, cli_contract, subagents_cli
from skaldir import subagents as S

LEAF_PATHS = (
    "agent.cancel",
    "agent.collect",
    "agent.list",
    "agent.retry",
    "agent.roles",
    "agent.show",
    "agent.spawn",
)

_PROVIDER_ENV = (
    "SKALDIR_LLM_BASE_URL",
    "GODSV_LLM_BASE_URL",
    "SKALDIR_LLM_API_BASE",
    "GODSV_LLM_API_BASE",
    "OPENAI_BASE_URL",
    "SKALDIR_LLM_MODEL",
    "GODSV_LLM_MODEL",
    "OPENAI_MODEL",
    "SKALDIR_LLM_API_KEY",
    "GODSV_LLM_API_KEY",
    "SKALDIR_LLM_KEY",
    "GODSV_LLM_KEY",
    "OPENAI_API_KEY",
    "SKALDIR_LLM_PROVIDER",
    "GODSV_LLM_PROVIDER",
    "SKALDIR_LLM_CONFIG",
    "GODSV_LLM_CONFIG",
)


@pytest.fixture(autouse=True)
def _restore_contract_commands(monkeypatch):
    """``register_cli`` installs entries into the real ``COMMANDS`` — undo that."""
    monkeypatch.setattr(cli_contract, "COMMANDS", dict(cli_contract.COMMANDS))


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.delenv("SKALDIR_SUBAGENTS_DIR", raising=False)
    monkeypatch.delenv("GODSV_SUBAGENTS_DIR", raising=False)
    monkeypatch.setenv("GODSV_HOME", str(tmp_path / "runtime"))
    return tmp_path / "state"


def _run(capsys, *argv: str) -> tuple[int, dict, str]:
    code = subagents_cli.main([str(item) for item in argv])
    captured = capsys.readouterr()
    document = json.loads(captured.out) if captured.out.strip() else {}
    return code, document, captured.err


def _spawn_args(state: Path, *tasks: str, run_id: str = "t1", **extra: str) -> list[str]:
    argv = ["agent", "spawn", "--provider", "stub", "--state-dir", str(state), "--run-id", run_id]
    for task in tasks:
        argv += ["--task", task]
    for flag, value in extra.items():
        argv += [f"--{flag.replace('_', '-')}", str(value)]
    return argv


# --------------------------------------------------------------------------- #
# the parser, the entries, the hand-over
# --------------------------------------------------------------------------- #


def test_the_group_registers_the_five_leaf_paths_with_their_entries():
    parser = subagents_cli.build_parser()
    leaves = sorted(path for path, _sub in cli_contract.leaf_commands(parser))
    assert leaves == list(LEAF_PATHS)
    assert sorted(subagents_cli.CONTRACT_ENTRIES) == leaves
    inventory = cli_contract.dump(parser)
    assert inventory["unregistered"] == []
    for item in inventory["commands"]:
        assert item["layer"] in cli_contract.LAYERS
        assert item["data_schema"]["type"] == "object"
        assert item["default_evidence"], f"{item['command']} declares no evidence source"
        assert item["has_json_flag"] is True
        assert 0 in item["exit_codes"]


def test_register_cli_is_idempotent_and_additive():
    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    before = dict(cli_contract.COMMANDS)
    first = subagents_cli.register_cli(sub)
    assert first is sub.choices["agent"]
    # The five entries are there — and they are *ours*, not another block's spec.
    for path in LEAF_PATHS:
        assert path in cli_contract.COMMANDS
        assert cli_contract.COMMANDS[path] is subagents_cli.CONTRACT_ENTRIES[path]
    added = [path for path in cli_contract.COMMANDS if path not in before]
    assert set(added) <= set(LEAF_PATHS)
    for path, entry in before.items():  # an existing entry is never overwritten
        assert cli_contract.COMMANDS[path] is entry
    snapshot = dict(cli_contract.COMMANDS)
    assert subagents_cli.register_cli(sub) is first  # a second call changes nothing
    assert cli_contract.COMMANDS == snapshot


def test_the_hand_over_snippet_lands_on_the_real_cli_parser():
    """The two lines the CLI owner pastes: no duplicate group, no unregistered leaf."""
    parser = cli.build_parser()
    sub = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    if "agent" not in sub.choices:
        subagents_cli.register_cli(sub)
    leaves = {path for path, _sub in cli_contract.leaf_commands(parser)}
    assert set(LEAF_PATHS) <= leaves
    inventory = cli_contract.dump(parser)
    assert [path for path in inventory["unregistered"] if path.startswith("agent.")] == []
    subagents_cli.register_cli(sub)  # idempotent: no argparse "conflicting subparser"
    assert len(sub.choices) == len(set(sub.choices))


def test_the_documented_snippets_name_every_path():
    contract_lines = subagents_cli.contract_snippet()
    for path in LEAF_PATHS:
        assert f'"{path}"' in contract_lines
    assert "_register_agent_cli(sub)" in subagents_cli.WIRING_SNIPPET
    assert "from .subagents_cli import register_cli" in subagents_cli.WIRING_SNIPPET


def test_it_merges_into_an_agent_group_another_block_already_registered(capsys, state):
    """The ENFORCE agent loop owns an ``agent`` group too — merge, never collide."""
    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    foreign = sub.add_parser("agent", help="the agent loop (ENFORCE)")
    foreign_sub = foreign.add_subparsers(dest="agent_command")
    foreign_sub.add_parser("run", help="run the agent loop")
    foreign_sub.add_parser("transcript", help="read a transcript back")

    returned = subagents_cli.register_cli(sub)
    assert returned is foreign, "a second `agent` group would be an argparse conflict"
    assert set(foreign_sub.choices) == {"run", "transcript", "spawn", "list", "show",
                                         "collect", "cancel", "retry", "roles"}
    assert subagents_cli.register_cli(sub) is foreign  # idempotent
    assert len(foreign_sub.choices) == 9

    # Our handler is really wired on the merged group, under the dotted path.
    cli_contract.annotate(parser)
    args = parser.parse_args(["agent", "spawn", "--provider", "stub", "--task", "x",
                              "--state-dir", str(state)])
    assert args.command_path == "agent.spawn"
    assert args.func is subagents_cli.cmd_agent_spawn


def test_the_state_dir_follows_the_runtime_then_the_env_override(tmp_path, monkeypatch):
    from skaldir.registry import default_runtime_dir

    assert subagents_cli._state_dir(argparse.Namespace(runtime="")) == (
        default_runtime_dir() / "subagents"
    )
    assert subagents_cli._state_dir(argparse.Namespace(runtime=str(tmp_path / "rt"))) == (
        tmp_path / "rt" / "subagents"
    )
    monkeypatch.setenv("SKALDIR_SUBAGENTS_DIR", str(tmp_path / "custom"))
    assert subagents_cli._state_dir(argparse.Namespace(runtime="")) == tmp_path / "custom"
    assert subagents_cli._state_dir(
        argparse.Namespace(runtime="", state_dir=str(tmp_path / "explicit"))
    ) == tmp_path / "explicit"


# --------------------------------------------------------------------------- #
# spawn
# --------------------------------------------------------------------------- #


def test_spawn_reports_one_result_per_child_and_exits_two_on_a_partial_run(
    capsys, state, tmp_path, monkeypatch
):
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    code, document, _stderr = _run(
        capsys,
        *_spawn_args(
            state,
            "a [[turns:1]]",
            "b [[fail]]",
            "c [[turns:2]]",
            run_id="cli-partial",
            parallel="3",
        ),
        "--json",
    )
    assert code == 2
    assert document["command"] == "agent.spawn"
    assert document["ok"] is False
    assert document["exit_code"] == 2
    assert document["error"]["code"] == "verification_failed"
    assert document["error"]["message"].startswith("1 of 3 child(ren) did not deliver")

    data = document["data"]
    assert data["run_id"] == "cli-partial"
    assert data["children_total"] == 3
    assert data["children_delivered"] == 2
    assert data["children_not_delivered"] == ["c2"]
    assert [child["ok"] for child in data["children"]] == [True, False, True]
    assert data["children"][1]["error"].startswith("RuntimeError: stub provider")
    assert data["children"][0]["context"]["session_id"] == "cli-partial:c1"
    assert data["children"][2]["turns"] == 2
    assert data["provider"]["mode"] == "stub"
    assert data["timing"]["spawn_samples"] == 3
    assert data["timing"]["spawn_p95_ms"] >= data["timing"]["spawn_p50_ms"]
    assert data["timing"]["first_result_ms"] <= data["timing"]["last_result_ms"]
    assert Path(data["run_dir"], "run.json").is_file()
    assert Path(data["run_dir"], "events.jsonl").is_file()
    assert any("no model was called" in item for item in document["warnings"])
    assert document["evidence"]
    assert document["data"]["failure_classes"] == {"provider_error": ["c2"]}
    assert document["data"]["cost"]["tokens"] > 0
    assert document["data"]["depth"] == 0 and document["data"]["max_depth"] == 1
    assert {item["command"].split()[2] for item in document["next_actions"]} == {
        "retry",
        "show",
    }
    assert document["next_actions"][0]["command"] == "skaldir agent retry cli-partial"


def test_spawn_exits_zero_and_streams_events_when_every_child_delivers(
    capsys, state
):
    code, document, stderr = _run(
        capsys,
        *_spawn_args(state, "one [[turns:1]]", "two [[turns:1]]", run_id="cli-green"),
        "--parallel",
        "2",
        "--stream",
        "--json",
    )
    assert code == 0
    assert document["ok"] is True
    assert document["data"]["children_delivered"] == 2
    assert document["data"]["partial"] is False
    # stdout held exactly one JSON document (json.loads would fail on a second one)
    assert document["command"] == "agent.spawn"
    # the stream went to stderr, as JSONL, and it is the run's real event order
    events = [json.loads(line) for line in stderr.splitlines() if line.startswith("{")]
    kinds = [event["kind"] for event in events]
    assert kinds[0] == "run_created"
    assert kinds[-1] == "run_finished"
    assert {"child_text", "child_finished"} <= set(kinds)
    assert all(event["t_ms"] >= 0 for event in events)
    assert {event["child_id"] for event in events if event["kind"] == "child_text"} == {
        "c1",
        "c2",
    }


def test_the_per_child_workdir_is_created_under_the_requested_parent(capsys, state, tmp_path):
    parent = tmp_path / "children"
    code, document, _stderr = _run(
        capsys,
        *_spawn_args(state, "a", "b", run_id="cli-workdir", workdir=str(parent)),
        "--json",
    )
    assert code == 0
    workdirs = {
        child["context"]["workdir"] for child in document["data"]["children"]
    }
    assert workdirs == {str(parent / "c1"), str(parent / "c2")}
    assert (parent / "c1" / ".subagent.json").is_file()


def test_a_task_file_supplies_the_tasks(capsys, state, tmp_path):
    task_file = tmp_path / "tasks.txt"
    task_file.write_text("# a comment\nalpha [[turns:1]]\n\nbeta [[turns:1]]\n")
    code, document, _stderr = _run(
        capsys, *_spawn_args(state, run_id="cli-file", task_file=str(task_file)), "--json"
    )
    assert code == 0
    assert [child["task"] for child in document["data"]["children"]] == [
        "alpha [[turns:1]]",
        "beta [[turns:1]]",
    ]


def test_the_per_child_budget_and_turn_cap_reach_the_result(capsys, state):
    code, document, _stderr = _run(
        capsys,
        *_spawn_args(
            state,
            "never finish [[infinite]]",
            run_id="cli-budget",
            max_turns="2",
            budget="100000",
        ),
        "--json",
    )
    assert code == 2
    child = document["data"]["children"][0]
    assert child["status"] == "max_turns_reached"
    assert child["failure_class"] == "max_turns"
    assert child["turns"] == 2
    assert child["max_turns"] == 2


def test_the_parallel_cap_is_visible_in_the_envelope(capsys, state):
    code, document, _stderr = _run(
        capsys,
        *_spawn_args(
            state, "a [[turns:1]]", "b [[turns:1]]", "c [[turns:1]]",
            run_id="cli-queue", parallel="2",
        ),
        "--json",
    )
    assert code == 0
    data = document["data"]
    assert data["queue_max"] == 2
    assert data["timing"]["parallel"] == 2
    assert data["timing"]["queue_max_concurrent"] == 2
    assert len(data["timing"]["queue_waited_children"]) == 1
    assert sum(1 for child in data["children"] if child["queued"]) == 1


def test_the_depth_cap_is_a_flag_and_past_the_ceiling_is_a_usage_error(capsys, state):
    code, refused, _stderr = _run(
        capsys,
        *_spawn_args(
            state, "work [[spawn:a grandchild]] [[turns:1]]", run_id="cli-depth-1",
            max_depth="1",
        ),
        "--json",
    )
    assert code == 0
    assert refused["data"]["max_depth"] == 1
    assert refused["data"]["nesting"]["spawned"] == []
    assert refused["data"]["nesting"]["refused"][0]["child_id"] == "c1"
    assert refused["data"]["children"][0]["context"]["may_spawn"] is False

    code, document, _stderr = _run(
        capsys,
        *_spawn_args(state, "a", run_id="cli-depth-bad", max_depth="9"),
        "--json",
    )
    assert code == 1
    assert document["error"]["code"] == "usage"
    assert "--max-depth" in document["error"]["message"]

    code, nested, _stderr = _run(
        capsys,
        *_spawn_args(
            state, "work [[spawn:a grandchild]] [[turns:1]]", run_id="cli-depth-2",
            max_depth="2",
        ),
        "--json",
    )
    assert code == 0
    # A CLI child really is role-bearing now (the default), and a ``worker`` may not delegate:
    # ``--max-depth 2`` alone cannot make a child spawn, and the record names which ceiling did
    # it. The role that *can* nest is asked for by name — see
    # ``test_the_cli_can_ask_for_a_role_and_the_role_really_nests``.
    refused_by_role = nested["data"]["nesting"]["refused"]
    assert refused_by_role and refused_by_role[0]["ceiling"] == "role"
    assert refused_by_role[0]["code"] == "role_ceiling.delegation_forbidden"
    assert nested["data"]["children"][0]["role"] == "worker"


def _help(capsys, *argv: str) -> str:
    """The CLI's own help text for one verb (``main`` returns 0 after argparse prints it)."""
    code = subagents_cli.main([str(item) for item in argv] + ["--help"])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    return captured.out + captured.err


def test_the_cli_documents_its_role_flag_on_both_verbs(capsys):
    """``--role`` is a real flag on ``agent spawn`` and ``agent retry``, not a hidden knob."""
    for verb in ("spawn", "retry"):
        text = _help(capsys, "agent", verb)
        assert "--role" in text, f"agent {verb} does not document --role"
        assert "explore" in text and "unroled" in text, text


def test_the_cli_can_ask_for_a_role_and_the_role_really_nests(capsys, state):
    """**Live (stub provider, real threads, real nested run on disk).**

    ``--max-depth 2`` can only be exercised through the CLI when the child's role may delegate;
    before the flag existed the CLI could not ask for one, so the capability was unreachable
    from the product surface (audit §6a). ``main`` is the role that may spawn.
    """
    code, document, _stderr = _run(
        capsys,
        *_spawn_args(
            state,
            "work [[spawn:a grandchild]] [[turns:1]]",
            run_id="cli-role-main",
            max_depth="2",
            role="main",
        ),
        "--json",
    )
    assert code == 0, document
    data = document["data"]
    assert data["children"][0]["role"] == "main"
    assert data["nesting"]["refused"] == []
    spawned = data["nesting"]["spawned"]
    assert len(spawned) == 1, spawned
    assert spawned[0]["by"] == "c1" and spawned[0]["total"] == 1

    nested = json.loads((Path(spawned[0]["dir"]) / "run.json").read_text(encoding="utf-8"))
    assert nested["depth"] == 1 and nested["max_depth"] == 2
    assert nested["parent"] == {"run_id": "cli-role-main", "child_id": "c1"}
    # the grandchildren are role-bearing too, and they may not nest further
    assert [item["role"] for item in nested["children"]] == ["worker"]
    assert all(item["context"]["may_spawn"] is False for item in nested["children"])


def test_a_role_asked_for_on_the_command_line_really_bounds_the_child(capsys, state):
    """**Live.** ``--role explore`` is a *read-only* ceiling, and an unknown role is a usage error.

    The ceiling is the role table's, not the CLI's opinion: the child's offered tools come from
    ``subagent_roles.tools_for``, and a name nobody grants is refused before any child exists.
    """
    code, document, _stderr = _run(
        capsys,
        *_spawn_args(state, "look around [[turns:1]]", run_id="cli-role-explore", role="explore"),
        "--json",
    )
    assert code == 0, document
    child = document["data"]["children"][0]
    assert child["role"] == "explore"
    offered = child["context"]["tools"]
    assert "read_file" in offered and "search_files" in offered
    assert "terminal" not in offered and "write_file" not in offered

    code, refused, _stderr = _run(
        capsys,
        *_spawn_args(state, "x", run_id="cli-role-bogus", role="archduke"),
        "--json",
    )
    assert code == 1
    assert refused["error"]["code"] == "usage"
    assert "archduke" in refused["error"]["message"]
    assert "explore" in str(refused["error"].get("hint") or "")
    # no run directory was created for a role nobody grants
    assert not (state / "cli-role-bogus").exists()


def test_a_retry_keeps_the_recorded_role_unless_the_cli_overrides_it(capsys, state):
    """A retry re-roles only when asked: ``--role`` overrides, silence preserves.

    A retry that dropped the recorded role would silently *re-role* the child — an ``explore``
    child (read-only on purpose) would come back a writer.
    """
    code, spawned, _stderr = _run(
        capsys,
        *_spawn_args(state, "look then fail [[fail]]", run_id="cli-role-retry-src", role="explore"),
        "--json",
    )
    assert code == 2
    assert spawned["data"]["children"][0]["role"] == "explore"

    code, kept, _stderr = _run(
        capsys,
        "agent", "retry", "cli-role-retry-src",
        "--provider", "stub", "--state-dir", str(state), "--json",
    )
    assert code == 2
    assert kept["data"]["retried_from"] == "cli-role-retry-src"
    assert kept["data"]["children"][0]["role"] == "explore"
    assert "terminal" not in kept["data"]["children"][0]["context"]["tools"]

    code, overridden, _stderr = _run(
        capsys,
        "agent", "retry", "cli-role-retry-src",
        "--provider", "stub", "--state-dir", str(state),
        "--new-run-id", "cli-role-retry-main", "--role", "worker", "--json",
    )
    assert code == 2
    assert overridden["data"]["children"][0]["role"] == "worker"
    assert "terminal" in overridden["data"]["children"][0]["context"]["tools"]


def test_collect_reports_the_failure_class_and_the_cost(capsys, state):
    code, _document, _stderr = _run(
        capsys, *_spawn_args(state, "a [[turns:1]]", "b [[fail]]", run_id="cli-classes"),
        "--json",
    )
    assert code == 2
    code, collected, _stderr = _run(
        capsys, "agent", "collect", "cli-classes", "--state-dir", str(state), "--json"
    )
    assert code == 2
    data = collected["data"]
    assert data["failure_classes"] == {"provider_error": ["c2"]}
    assert [item["failure_class"] for item in data["results"]] == [None, "provider_error"]
    assert data["results"][0]["cost"]["tokens"] > 0
    assert data["cost"]["currency"] is None
    assert data["queue_max"] == 2


# --------------------------------------------------------------------------- #
# list / show / collect / cancel
# --------------------------------------------------------------------------- #


def test_list_show_and_collect_read_the_run_back(capsys, state):
    code, spawned, _stderr = _run(
        capsys, *_spawn_args(state, "a [[turns:1]]", "b [[fail]]", run_id="cli-read"), "--json"
    )
    assert code == 2

    code, listing, _stderr = _run(capsys, "agent", "list", "--state-dir", str(state), "--json")
    assert code == 0
    assert [item["run_id"] for item in listing["data"]["runs"]] == ["cli-read"]
    assert listing["data"]["runs"][0]["children_delivered"] == 1

    code, shown, _stderr = _run(
        capsys, "agent", "show", "cli-read", "--state-dir", str(state), "--events", "100", "--json"
    )
    assert code == 0
    assert shown["data"]["run"]["run_id"] == "cli-read"
    kinds = [event["kind"] for event in shown["data"]["events"]]
    assert kinds[0] == "run_created"
    assert kinds[-1] == "run_finished"
    assert {"child_turn_started", "child_text", "child_finished"} <= set(kinds)

    code, tail, _stderr = _run(
        capsys, "agent", "show", "cli-read", "--state-dir", str(state), "--events", "4", "--json"
    )
    assert len(tail["data"]["events"]) == 4
    assert [event["kind"] for event in tail["data"]["events"]] == kinds[-4:]

    code, shown_child, _stderr = _run(
        capsys, "agent", "show", "cli-read", "--child", "c2", "--state-dir", str(state), "--json"
    )
    assert [child["child_id"] for child in shown_child["data"]["run"]["children"]] == ["c2"]

    code, collected, _stderr = _run(
        capsys, "agent", "collect", "cli-read", "--state-dir", str(state), "--json"
    )
    assert code == 2
    assert collected["data"]["delivered"] == 1
    assert collected["data"]["complete"] is True
    assert [item["child_id"] for item in collected["data"]["results"]] == ["c1", "c2"]
    assert collected["data"]["results"][0]["answer"]
    assert collected["error"]["code"] == "verification_failed"


def test_cancel_from_the_cli_stops_a_live_run(capsys, state):
    store = S.RunStore(state)
    run = S.SubagentRun(
        provider=S.StubProvider(latency_s=0.1),
        briefs=S.briefs_from_tasks(["a [[infinite]]"], max_turns=100),
        state_dir=state,
        run_id="cli-cancel",
        provider_summary={"mode": "stub"},
    )
    thread = threading.Thread(target=run.run, daemon=True)
    thread.start()
    _wait(lambda: (store.load("cli-cancel") or {}).get("status") == "running")

    code, document, _stderr = _run(
        capsys, "agent", "cancel", "cli-cancel", "--state-dir", str(state), "--reason", "cli proof",
        "--wait", "10", "--json",
    )
    assert code == 0
    assert document["data"]["action"] == "signalled"
    assert document["data"]["status"] == "cancelled"
    assert document["data"]["pid_alive"] is True
    thread.join(timeout=15)
    record = store.load("cli-cancel")
    assert record["status"] == "cancelled"
    assert record["cancel_reason"] == "cli proof"

    code, again, stderr = _run(
        capsys, "agent", "cancel", "cli-cancel", "--state-dir", str(state), "--json"
    )
    assert code == 0
    assert again["data"]["action"] == "noop"
    assert again["warnings"]


def test_collect_wait_hits_its_deadline_on_a_live_run(capsys, state):
    store = S.RunStore(state)
    run = S.SubagentRun(
        provider=S.StubProvider(latency_s=0.2),
        briefs=S.briefs_from_tasks(["a [[infinite]]"], max_turns=100),
        state_dir=state,
        run_id="cli-live",
        provider_summary={"mode": "stub"},
    )
    thread = threading.Thread(target=run.run, daemon=True)
    thread.start()
    _wait(lambda: (store.load("cli-live") or {}).get("status") == "running")
    try:
        code, document, _stderr = _run(
            capsys,
            "agent",
            "collect",
            "cli-live",
            "--state-dir",
            str(state),
            "--wait",
            "--wait-timeout",
            "0.3",
            "--json",
        )
        assert code == 8
        assert document["data"]["complete"] is False
        assert document["error"]["code"] == "unknown"
        assert document["next_actions"]
    finally:
        store.request_cancel("cli-live", reason="test cleanup")
        thread.join(timeout=15)


def test_an_unknown_run_is_not_found(capsys, state):
    for argv in (
        ("agent", "show", "ghost"),
        ("agent", "collect", "ghost"),
        ("agent", "cancel", "ghost"),
    ):
        code, document, _stderr = _run(capsys, *argv, "--state-dir", str(state), "--json")
        assert code == 5, argv
        assert document["error"]["code"] == "not_found"
        assert "ghost" in document["error"]["message"]


# --------------------------------------------------------------------------- #
# retry + reading a child's own transcript off disk
# --------------------------------------------------------------------------- #


def test_retry_re_runs_only_the_children_that_did_not_deliver(capsys, state):
    code, first, _stderr = _run(
        capsys, *_spawn_args(state, "fine [[turns:1]]", "boom [[fail]]", run_id="cli-retry-src"),
        "--json",
    )
    assert code == 2 and first["data"]["children_not_delivered"] == ["c2"]

    code, retried, _stderr = _run(
        capsys,
        "agent",
        "retry",
        "cli-retry-src",
        "--state-dir",
        str(state),
        "--provider",
        "stub",
        "--json",
    )
    assert code == 2, "the stub fails the same task again — a retry is not a fix by itself"
    data = retried["data"]
    assert data["run_id"] == "cli-retry-src-retry"
    assert data["retried_from"] == "cli-retry-src"
    assert data["children_total"] == 1
    assert data["children"][0]["child_id"] == "c2", "only the unfinished child was re-run"
    assert data["children"][0]["failure_class"] == "provider_error"
    assert data["source_run"]["retried_children"] == ["c2"]
    assert data["source_run"]["failure_classes"] == {"provider_error": ["c2"]}

    source_transcript = Path(data["source_run"]["transcripts"]["c2"])
    assert source_transcript.is_file()
    assert any(item["ref"] == str(source_transcript) for item in retried["evidence"])
    assert any("FIRST attempt" in item["note"] for item in retried["evidence"])

    # The source run is untouched — a retry is a new run, not a resume.
    store = S.RunStore(state)
    assert store.load("cli-retry-src")["children"][1]["status"] == "failed"
    assert store.list()[0]["run_id"] == "cli-retry-src-retry"
    assert store.list()[0]["retried_from"] == "cli-retry-src"

    # --child selects explicitly, whether or not that child delivered.
    code, one, _stderr = _run(
        capsys,
        "agent",
        "retry",
        "cli-retry-src",
        "--child",
        "c1",
        "--state-dir",
        str(state),
        "--provider",
        "stub",
        "--new-run-id",
        "cli-retry-c1",
        "--json",
    )
    assert code == 0
    assert one["data"]["children"][0]["child_id"] == "c1"
    assert one["data"]["children"][0]["status"] == "delivered"
    assert one["data"]["source_run"]["retried_children"] == ["c1"]


def test_retry_refuses_an_unknown_run_a_delivered_run_a_taken_id_and_a_bad_child(capsys, state):
    code, missing, _stderr = _run(
        capsys, "agent", "retry", "ghost", "--state-dir", str(state), "--json"
    )
    assert code == 5 and missing["error"]["code"] == "not_found"

    code, _green, _stderr = _run(
        capsys, *_spawn_args(state, "all good [[turns:1]]", run_id="cli-all-green"), "--json"
    )
    assert code == 0
    code, nothing, _stderr = _run(
        capsys, "agent", "retry", "cli-all-green", "--state-dir", str(state), "--json"
    )
    assert code == 1 and nothing["error"]["code"] == "usage"
    assert "no child left to retry" in nothing["error"]["message"]

    code, _partial, _stderr = _run(
        capsys, *_spawn_args(state, "fine [[turns:1]]", "boom [[fail]]", run_id="cli-retry-dup"),
        "--json",
    )
    assert code == 2
    argv = ("agent", "retry", "cli-retry-dup", "--state-dir", str(state), "--provider", "stub",
            "--json")
    code, _first_retry, _stderr = _run(capsys, *argv)
    assert code == 2
    code, duplicate, _stderr = _run(capsys, *argv)
    assert code == 1 and duplicate["error"]["code"] == "usage"
    assert "cannot start the retry" in duplicate["error"]["message"]

    code, bad_child, _stderr = _run(
        capsys, "agent", "retry", "cli-retry-dup", "--child", "c9", "--state-dir", str(state),
        "--provider", "stub", "--json",
    )
    assert code == 1 and bad_child["error"]["code"] == "usage"
    assert "no such child" in bad_child["error"]["message"]


def test_show_transcript_reads_a_childs_transcript_from_disk_while_the_run_is_live(capsys, state):
    store = S.RunStore(state)
    run = S.SubagentRun(
        provider=S.StubProvider(latency_s=0.15),
        briefs=S.briefs_from_tasks(["live work [[infinite]]"], max_turns=20),
        state_dir=state,
        run_id="cli-live-transcript",
        provider_summary={"mode": "stub"},
    )
    thread = threading.Thread(target=run.run, daemon=True)
    thread.start()
    try:
        _wait(lambda: bool(store.transcript("cli-live-transcript", "c1")))
        code, document, _stderr = _run(
            capsys,
            "agent",
            "show",
            "cli-live-transcript",
            "--state-dir",
            str(state),
            "--transcript",
            "--json",
        )
        assert code == 0
        data = document["data"]
        assert data["status"] == "running" and data["complete"] is False
        turns = data["transcripts"]["c1"]
        assert turns["exists"] is True and turns["turns"] >= 1
        assert turns["lines"][0]["turn"] == 1
        assert turns["lines"][0]["session_id"] == "cli-live-transcript:c1"
        assert data["run"]["children"][0]["status"] in ("running", "queued")
        assert any(str(item["ref"]).endswith("transcript.jsonl") for item in document["evidence"])

        code, tail, _stderr = _run(
            capsys,
            "agent",
            "show",
            "cli-live-transcript",
            "--state-dir",
            str(state),
            "--transcript",
            "--transcript-tail",
            "1",
            "--json",
        )
        assert code == 0 and tail["data"]["transcripts"]["c1"]["turns"] == 1

        text_code = subagents_cli.main(
            ["agent", "show", "cli-live-transcript", "--state-dir", str(state), "--transcript"]
        )
        captured = capsys.readouterr()
        assert text_code == 0
        assert captured.out == ""  # no envelope without --json
        assert "transcript c1:" in captured.err
        assert "transcript.jsonl" in captured.err
        assert "t1 " in captured.err
    finally:
        store.request_cancel("cli-live-transcript", reason="test cleanup")
        thread.join(timeout=15)


# --------------------------------------------------------------------------- #
# refusals
# --------------------------------------------------------------------------- #


def test_spawn_without_a_provider_blocks_with_the_variables_to_export(
    capsys, state, tmp_path, monkeypatch
):
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
    code, document, _stderr = _run(
        capsys,
        "agent",
        "spawn",
        "--provider",
        "auto",
        "--task",
        "a",
        "--state-dir",
        str(state),
        "--run-id",
        "cli-blocked",
        "--json",
    )
    assert code == 3
    assert document["error"]["code"] == "blocked"
    assert "SKALDIR_LLM" in document["error"]["message"]
    assert document["next_actions"][0]["command"].startswith("skaldir agent spawn --provider stub")


def test_spawn_without_a_task_is_a_usage_error(capsys, state):
    code, document, _stderr = _run(
        capsys, "agent", "spawn", "--provider", "stub", "--state-dir", str(state), "--json"
    )
    assert code == 1
    assert document["error"]["code"] == "usage"
    assert "--task" in document["error"]["hint"]


def test_an_invalid_run_id_is_a_usage_error(capsys, state):
    code, document, _stderr = _run(
        capsys,
        *_spawn_args(state, "a", run_id="../../escape"),
        "--json",
    )
    assert code == 1
    assert document["error"]["code"] == "usage"
    assert "invalid run id" in document["error"]["message"]


def test_a_missing_task_file_is_a_usage_error(capsys, state, tmp_path):
    code, document, _stderr = _run(
        capsys,
        *_spawn_args(state, run_id="cli-notaskfile", task_file=str(tmp_path / "nope.txt")),
        "--json",
    )
    assert code == 1
    assert document["error"]["code"] == "usage"
    assert "cannot read --task-file" in document["error"]["message"]


def test_text_mode_writes_the_table_to_stderr(capsys, state):
    code = subagents_cli.main(
        _spawn_args(state, "a [[turns:1]]", "b [[turns:1]]", run_id="cli-text")
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""  # no envelope without --json
    assert "provider: stub" in captured.err
    assert "spawn latency" in captured.err
    assert "per-child overhead" in captured.err
    assert "queue   cap all" in captured.err
    assert "delivered" in captured.err


def test_the_stub_run_says_so_in_the_envelope(capsys, state):
    code, document, _stderr = _run(
        capsys, *_spawn_args(state, "a [[turns:1]]", run_id="cli-stub-note"), "--json"
    )
    assert code == 0
    assert document["data"]["provider"]["mode"] == "stub"
    assert any("stub" in note for note in document["data"]["notes"])
    assert any("no model was called" in warning for warning in document["warnings"])


def _wait(predicate, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition never became true")


def test_no_test_writes_outside_its_tmp_state_dir(state, tmp_path):
    """A guard for this file: the product runtime is never the CLI's default here."""
    assert os.environ.get("GODSV_HOME") == str(tmp_path / "runtime")
    assert str(state).startswith(str(tmp_path))
