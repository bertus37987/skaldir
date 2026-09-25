"""``skaldir coding …`` — the CLI surface: leaves, contract entries, exit codes, envelope.

The group is driven through its own standalone parser **and** through a real ``subparsers``
handle, so what is measured is the verb, its exit code and its JSON — not a handler called
directly.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from skaldir import cli_contract
from skaldir.coding_features_cli import build_parser, coding_contract_entries, main, register_cli

PY = sys.executable

LEAVES = (
    "events run",
    "events show",
    "plan begin",
    "plan probe",
    "plan propose",
    "plan show",
    "plan check",
    "plan close",
    "testfix",
    "complete",
    "expand",
    "turn begin",
    "turn record",
    "turn end",
    "turn list",
    "turn undo",
    "status",
    "summary",
    "init",
    "model show",
    "model set",
    "model clear",
)

FAILING_TEST = "def test_x():\n    assert 1 == 2\n"


@pytest.fixture(autouse=True)
def _restore_contract_commands():
    """Registering this group must not leak into another test file's ``COMMANDS`` view."""
    before = dict(cli_contract.COMMANDS)
    yield
    cli_contract.COMMANDS.clear()
    cli_contract.COMMANDS.update(before)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    (root / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tests" / "test_mod.py").write_text(
        "def test_x():\n    assert 1 == 1\n", encoding="utf-8"
    )
    return root


@pytest.fixture
def state(tmp_path: Path) -> Path:
    directory = tmp_path / "state"
    directory.mkdir()
    return directory


def run(argv: list[str]) -> tuple[int, dict, str]:
    """Run the group's CLI; return ``(exit_code, json_on_stdout_or_empty, stderr)``."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    text = out.getvalue().strip()
    payload = json.loads(text) if text.startswith("{") else {}
    return code, payload, err.getvalue()


def every_child(target: object, prefix: str = "") -> list[str]:
    """Walk a real parser tree and name every leaf (so "wired" is measured, not assumed)."""
    action = (
        target
        if isinstance(target, argparse._SubParsersAction)
        else next(
            (item for item in getattr(target, "_actions", [])
             if isinstance(item, argparse._SubParsersAction)),
            None,
        )
    )
    names: list[str] = []
    if action is None:
        return names
    for name, child in action.choices.items():
        deeper = f"{prefix} {name}".strip()
        if any(isinstance(item, argparse._SubParsersAction) for item in child._actions):
            names.extend(every_child(child, deeper))
        else:
            names.append(deeper)
    return names


# --------------------------------------------------------------------------- #
# the shape of the group
# --------------------------------------------------------------------------- #


def test_every_documented_verb_exists_in_the_standalone_parser() -> None:
    assert set(every_child(build_parser())) == set(LEAVES)


def test_register_cli_installs_every_contract_entry_and_is_idempotent() -> None:
    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    register_cli(sub)
    installed = sorted(path for path in cli_contract.COMMANDS if path.startswith("coding."))
    expected = sorted("coding." + leaf.replace(" ", ".") for leaf in LEAVES)
    assert installed == expected

    names = every_child(parser)
    assert set(names) == {"coding " + leaf for leaf in LEAVES}
    register_cli(sub)  # a second wiring must add nothing and must not raise
    assert set(names) == {"coding " + leaf for leaf in LEAVES}


def test_register_cli_accepts_a_parser_that_already_has_the_group() -> None:
    """A sibling that created an ``coding`` group first must get our verbs attach to it."""
    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("coding")
    parent = register_cli(sub)
    assert isinstance(parent, argparse.ArgumentParser)
    verbs = next(a for a in parent._actions if isinstance(a, argparse._SubParsersAction))
    assert "events" in verbs.choices


def test_every_contract_entry_has_a_layer_a_schema_and_evidence() -> None:
    for path, spec in coding_contract_entries().items():
        assert spec.layer in ("EXPAND", "ACT", "COMPOSE", "ROUTE", "SUPERVISE", "ENFORCE",
                             "VERIFY", "PERSIST"), path
        assert spec.data_schema["type"] == "object", path
        assert spec.evidence, f"{path} has no default evidence ref"
        assert path.startswith("coding.")


# --------------------------------------------------------------------------- #
# the event stream
# --------------------------------------------------------------------------- #


def test_events_run_emits_a_stream_and_flags_work_after_the_separator(
    workspace: Path, state: Path
) -> None:
    """Measured trap: argparse puts everything after ``--`` into the REMAINDER, so ``--json``
    used to end up in the child command. The flags are hoisted back and the stream still runs
    the declared argv."""
    stream_path = state / "events.jsonl"
    code, payload, _ = run([
        "events", "run", "--state", str(state), "--workspace", str(workspace),
        "--jsonl", str(stream_path), "--", "bash", "-c",
        "for i in 1 2 3; do echo step $i; done", "--json",
    ])
    assert code == 0, payload
    assert payload["status"] == "completed"
    assert payload["command"] == ["bash", "-c", "for i in 1 2 3; do echo step $i; done"]
    assert payload["lines"] == 3
    assert payload["invariants"]["ok"] is True
    assert payload["stream"] == str(stream_path)

    lines = stream_path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert [event["type"] for event in events][0] == "run.started"
    assert [event["type"] for event in events][-1] == "run.completed"
    assert sum(event["type"] == "item.updated" for event in events) == 3


def test_events_run_reports_a_failing_command_as_exit_2_and_keeps_its_code(
    workspace: Path, state: Path
) -> None:
    code, payload, _ = run([
        "events", "run", "--state", str(state), "--workspace", str(workspace), "--json",
        "--", "bash", "-c", "echo nope; exit 3",
    ])
    assert code == 2
    assert payload["status"] == "failed"
    assert payload["child_exit_code"] == 3


def test_plan_guard_declines_a_write_inside_plan_mode(workspace: Path, state: Path) -> None:
    assert run(["plan", "begin", "add a flag", "--state", str(state),
                "--workspace", str(workspace), "--json"])[0] == 0
    code, payload, _ = run([
        "events", "run", "--state", str(state), "--workspace", str(workspace), "--plan-guard",
        "--json", "--", "rm", "-rf", str(workspace / "src"),
    ])
    assert code == 3
    assert payload["status"] == "declined"
    assert payload["child_exit_code"] is None
    assert (workspace / "src").exists(), "a declined action must not reach the disk"


def test_events_run_without_a_command_is_a_usage_error(state: Path) -> None:
    code, payload, _ = run(["events", "run", "--state", str(state), "--json"])
    assert code == 1
    assert payload["reason"] == "no command given"


def test_events_show_validates_and_a_missing_file_is_not_found(
    workspace: Path, state: Path
) -> None:
    stream_path = state / "s.jsonl"
    run(["events", "run", "--state", str(state), "--workspace", str(workspace),
         "--jsonl", str(stream_path), "--json", "--", "true"])
    code, payload, _ = run(["events", "show", str(stream_path), "--json"])
    assert code == 0 and payload["ok"] is True and payload["items_completed"] == 1
    missing, _, _ = run(["events", "show", str(state / "nope.jsonl"), "--json"])
    assert missing == 5


# --------------------------------------------------------------------------- #
# plan mode
# --------------------------------------------------------------------------- #


def test_plan_lifecycle_through_the_cli(workspace: Path, state: Path) -> None:
    code, payload, _ = run(["plan", "begin", "add --dry-run", "--state", str(state),
                            "--workspace", str(workspace), "--json"])
    assert code == 0 and payload["status"] == "planning"
    conflict, payload, _ = run(["plan", "begin", "another", "--state", str(state),
                                "--workspace", str(workspace), "--json"])
    assert conflict == 6 and payload["plan"]["task"] == "add --dry-run"

    probe, payload, _ = run(["plan", "probe", "--state", str(state),
                             "--workspace", str(workspace), "--json"])
    assert probe == 0
    assert "src/mod.py" in [entry["path"] for entry in payload["probe"]["files"]]

    proposal, payload, _ = run([
        "plan", "propose", "--state", str(state), "--workspace", str(workspace),
        "--change", "edit src/mod.py", "--question", "which flag name?", "--json",
    ])
    assert proposal == 0
    assert payload["decision_complete"] is False
    assert "NOT decision complete" in payload["plan"]

    allowed, payload, _ = run(["plan", "check", "--state", str(state), "--workspace",
                               str(workspace), "--json", "--", "git", "status"])
    assert allowed == 0 and payload["classification"] == "read_only"
    refused, payload, _ = run(["plan", "check", "--state", str(state), "--workspace",
                               str(workspace), "--json", "--", "git", "commit", "-m", "x"])
    assert refused == 3 and payload["status"] == "declined"

    closed, payload, _ = run(["plan", "close", "--state", str(state), "--apply", "--json"])
    assert closed == 0 and payload["status"] == "applied"
    after, payload, _ = run(["plan", "check", "--state", str(state), "--workspace",
                             str(workspace), "--json", "--", "git", "commit", "-m", "x"])
    assert after == 0 and payload["mode"] == "default"


def test_plan_close_needs_a_decision_and_show_says_when_nothing_is_open(state: Path) -> None:
    code, payload, _ = run(["plan", "show", "--state", str(state), "--json"])
    assert code == 5 and payload["status"] == "no_plan"
    run(["plan", "begin", "x", "--state", str(state), "--json"])
    code, payload, _ = run(["plan", "close", "--state", str(state), "--json"])
    assert code == 1 and "pass --apply or --drop" in payload["reason"]


# --------------------------------------------------------------------------- #
# the test-fix loop
# --------------------------------------------------------------------------- #


def _pytest_cmd(workspace: Path) -> list[str]:
    return ["testfix", "--state", "STATE", "--workspace", str(workspace), "--json", "--",
            PY, "-m", "pytest", "tests", "-q", "-o", "addopts="]


def test_testfix_without_a_fixer_reports_the_roster_and_exits_2(
    workspace: Path, state: Path
) -> None:
    (workspace / "tests" / "test_mod.py").write_text(FAILING_TEST, encoding="utf-8")
    argv = [item if item != "STATE" else str(state) for item in _pytest_cmd(workspace)]
    code, payload, _ = run(argv)
    assert code == 2
    assert payload["status"] == "no_fixer"
    assert [item["name"] for item in payload["failures"]] == ["test_x"]
    assert payload["rounds_used"] == 0


def test_testfix_with_a_fixer_command_converges(
    workspace: Path, state: Path, tmp_path: Path
) -> None:
    (workspace / "tests" / "test_mod.py").write_text(FAILING_TEST, encoding="utf-8")
    fixer = tmp_path / "fix.py"
    fixer.write_text(
        "import json, pathlib\n"
        "pathlib.Path('tests/test_mod.py').write_text('def test_x():\\n    assert 1 == 1\\n')\n"
        "print(json.dumps({'outcome': 'fixed', 'changed_paths': ['tests/test_mod.py']}))\n",
        encoding="utf-8",
    )
    argv = [item if item != "STATE" else str(state) for item in _pytest_cmd(workspace)]
    argv = argv[:1] + ["--fix-cmd", f"{PY} {fixer}"] + argv[1:]
    code, payload, _ = run(argv)
    assert code == 0, payload
    assert payload["status"] == "passed"
    assert payload["rounds_used"] == 1
    assert payload["first_run"]["failures"] == 1


def test_testfix_that_is_already_green_says_so(workspace: Path, state: Path) -> None:
    argv = [item if item != "STATE" else str(state) for item in _pytest_cmd(workspace)]
    code, payload, _ = run(argv)
    assert code == 0
    assert payload["status"] == "already_green"
    assert payload["rounds_used"] == 0


# --------------------------------------------------------------------------- #
# mentions
# --------------------------------------------------------------------------- #


def test_complete_returns_scored_paths_and_an_inline_list(workspace: Path) -> None:
    code, payload, _ = run(["complete", "@mod", "--workspace", str(workspace), "--inline",
                            "--json"])
    assert code == 0
    assert [item["path"] for item in payload["matches"]] == [
        "src/mod.py",
        "tests/test_mod.py",
    ]
    assert payload["inline"][0] == "src/mod.py"
    assert payload["matches"][0]["indices"]

    empty, payload, _ = run(["complete", "zzzz", "--workspace", str(workspace), "--json"])
    assert empty == 0 and payload["matches"] == [] and payload["total_matches"] == 0


def test_expand_lists_refusals_as_warnings(workspace: Path) -> None:
    code, payload, err = run(["expand", "see @src/mod.py and @../secret", "--workspace",
                              str(workspace), "--json"])
    assert code == 0
    assert [item["path"] for item in payload["mentions"]] == ["src/mod.py"]
    assert payload["refused"] and payload["refused"][0]["mention"] == "../secret"


# --------------------------------------------------------------------------- #
# turn journal + undo
# --------------------------------------------------------------------------- #


def test_the_turn_journal_across_four_invocations(workspace: Path, state: Path) -> None:
    """A shell has no memory between commands: begin, record, end and undo are four runs."""
    target = workspace / "data.txt"
    target.write_text("original\n", encoding="utf-8")
    space = ["--state", str(state), "--workspace", str(workspace), "--json"]

    assert run(["turn", "begin", "--label", "edit", *space])[0] == 0
    assert run(["turn", "record", "data.txt", *space])[0] == 0
    assert run(["turn", "record", "new.txt", "--new", *space])[0] == 0
    target.write_text("changed\n", encoding="utf-8")
    (workspace / "new.txt").write_text("created\n", encoding="utf-8")
    end_code, payload, _ = run(["turn", "end", *space])
    assert end_code == 0 and payload["turn_id"]

    listing, payload, _ = run(["turn", "list", *space])
    assert listing == 0 and payload["count"] == 1
    assert len(payload["turns"][0]["files"]) == 2

    undo, payload, _ = run(["turn", "undo", *space])
    assert undo == 0 and payload["status"] == "undone"
    assert target.read_text(encoding="utf-8") == "original\n"
    assert not (workspace / "new.txt").exists()
    assert payload["transcript"]["reverted"] is False

    again, payload, _ = run(["turn", "undo", *space])
    assert again == 5 and payload["status"] == "nothing_to_undo"


def test_recording_without_an_open_turn_is_not_found(workspace: Path, state: Path) -> None:
    code, payload, _ = run(["turn", "record", "data.txt", "--state", str(state),
                            "--workspace", str(workspace), "--json"])
    assert code == 5 and payload["status"] == "no_open_turn"


def test_undo_refuses_when_a_file_moved_since_the_turn(workspace: Path, state: Path) -> None:
    target = workspace / "data.txt"
    target.write_text("original\n", encoding="utf-8")
    space = ["--state", str(state), "--workspace", str(workspace), "--json"]
    run(["turn", "begin", *space])
    run(["turn", "record", "data.txt", *space])
    target.write_text("changed\n", encoding="utf-8")
    run(["turn", "end", *space])
    target.write_text("moved on\n", encoding="utf-8")
    code, payload, _ = run(["turn", "undo", *space])
    assert code == 6
    assert payload["status"] == "refused"


# --------------------------------------------------------------------------- #
# status / summary / init / model
# --------------------------------------------------------------------------- #


def test_status_reports_an_unknown_session_as_not_found(state: Path, workspace: Path) -> None:
    """An unknown session is exit 5 with the reason — never a silent zero that reads as
    "this session is empty"."""
    code, payload, err = run([
        "status", "--state", str(state), "--workspace", str(workspace), "--session", "nope",
        "--runtime", str(state / "rt"), "--json",
    ])
    assert code == 5
    assert payload["status"] == "session_not_found"
    assert payload["messages"]["available"] is False
    assert "not in" in payload["messages"]["reason"]


def test_status_without_a_session_is_a_clean_zero(state: Path, workspace: Path) -> None:
    code, payload, _ = run([
        "status", "--state", str(state), "--workspace", str(workspace),
        "--runtime", str(state / "rt"), "--json",
    ])
    assert code == 0
    assert payload["session_found"] is None
    assert payload["messages"]["total"] == 0


def test_summary_writes_the_handoff_markdown(state: Path, workspace: Path) -> None:
    code, payload, _ = run([
        "summary", "--state", str(state), "--workspace", str(workspace), "--write",
        "--runtime", str(state / "rt"), "--json",
    ])
    assert code == 0
    written = Path(payload["written"])
    assert written.exists()
    text = written.read_text(encoding="utf-8")
    assert "## What remains to be done" in text
    assert payload["markdown"] == text


def test_init_writes_and_then_refuses_in_the_cli(workspace: Path) -> None:
    code, payload, _ = run(["init", "--workspace", str(workspace), "--name", "demo", "--json"])
    assert code == 0 and payload["status"] == "written"
    assert "## Commands" in (workspace / "AGENTS.md").read_text(encoding="utf-8")
    again, payload, _ = run(["init", "--workspace", str(workspace), "--json"])
    assert again == 6 and payload["status"] == "exists"
    assert payload["sha256"]
    forced, payload, _ = run(["init", "--workspace", str(workspace), "--force", "--json"])
    assert forced == 0 and payload["replaced_sha256"]


def test_model_switching_mid_session(state: Path, tmp_path: Path) -> None:
    space = ["--state", str(state), "--session", "s1", "--json"]
    assert run(["model", "set", "--model", "deepseek-x", "--effort", "high", *space])[0] == 0
    code, payload, _ = run(["model", "show", *space])
    assert code == 0
    assert payload["effective"]["model"] == "deepseek-x"
    assert payload["effective"]["reasoning_effort"] == "high"
    assert payload["effective"]["source"] == "session-override"
    bad, payload, _ = run(["model", "set", "--effort", "extreme", *space])
    assert bad == 1 and "low" in payload["reason"]
    cleared, payload, _ = run(["model", "clear", *space])
    assert cleared == 0 and payload["cleared"] is True
    after, payload, _ = run(["model", "show", *space])
    assert after == 0 and payload["effective"]["source"] == "unset"
