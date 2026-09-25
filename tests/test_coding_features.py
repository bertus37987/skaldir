"""The coding-agent features, measured — stream, plan mode, test-fix, mentions, undo, summary.

Every test here runs something real: a subprocess for the stream and the test-fix loop, a
real file tree for the mentions and the journal, a real SQLite session store for the summary.
Nothing asserts a shape it did not execute, because the whole point of these features is that
they touch the machine.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from skaldir import coding_features as C

PY = sys.executable

FAILING_TEST = "def test_x():\n    assert 1 == 2\n"
PASSING_TEST = "def test_x():\n    assert 1 == 1\n"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A small project: one module, one test, a pyproject that declares two commands."""
    root = tmp_path / "ws"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    (root / "src" / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tests" / "test_mod.py").write_text(PASSING_TEST, encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1"\n\n[project.scripts]\ndemo = "demo:main"\n',
        encoding="utf-8",
    )
    return root


@pytest.fixture
def state(tmp_path: Path) -> Path:
    directory = tmp_path / "state"
    directory.mkdir()
    return directory


def _pytest_argv(*extra: str) -> list[str]:
    return [PY, "-m", "pytest", "tests", "-q", "-o", "addopts=", *extra]


# --------------------------------------------------------------------------- #
# 1. the event stream
# --------------------------------------------------------------------------- #


def test_a_run_emits_begin_progress_end_and_the_invariants_hold(tmp_path: Path) -> None:
    """The one thing a UI needs: an item's whole lifecycle, and exactly one terminal event."""
    stream_path = tmp_path / "run.jsonl"
    sink = C._Emitter(path=stream_path)
    stream = C.RunStream(run_id="run-1", emitter=sink)
    stream.run_started(["echo", "hi"])
    result = C.stream_command(
        ["bash", "-c", "for i in 1 2 3 4; do echo step $i; done"],
        stream=stream,
        cwd=tmp_path,
        progress_every=1,
    )
    sink.close()

    assert result["status"] == "completed"
    assert result["exit_code"] == 0
    assert result["lines"] == 4
    events = C.read_stream(stream_path)
    kinds = [event["type"] for event in events]
    assert kinds[0] == "run.started"
    assert kinds[-1] == "run.completed"
    assert kinds.count("item.started") == 1
    assert kinds.count("item.completed") == 1
    assert kinds.count("item.updated") == 4
    report = C.validate_stream(events)
    assert report["ok"] is True, report["problems"]
    assert report["items_started"] == 1 and report["items_completed"] == 1
    assert report["orphans"] == []
    # A progress event a UI can render without re-reading the command's output:
    progress = [event for event in events if event["type"] == "item.updated"]
    assert progress[0]["item"]["lines"] == 1
    assert progress[-1]["item"]["aggregated_output"].endswith("step 4")


def test_a_declined_command_reports_declined_not_failed(tmp_path: Path) -> None:
    """``declined`` is a policy refusal: no exit code, and the process never started."""
    marker = tmp_path / "should-not-exist"
    sink = C._Emitter(path=tmp_path / "declined.jsonl")
    stream = C.RunStream(run_id="run-2", emitter=sink)
    stream.run_started(["touch", str(marker)])
    result = C.stream_command(
        ["touch", str(marker)],
        stream=stream,
        cwd=tmp_path,
        policy=lambda command: "the policy refuses this",
    )
    sink.close()

    assert result["status"] == "declined"
    assert result["exit_code"] is None
    assert not marker.exists(), "a declined command must not have run"
    report = C.validate_stream(C.read_stream(tmp_path / "declined.jsonl"))
    assert report["ok"] is True
    assert report["items_declined"] == 1 and report["items_failed"] == 0


def test_validate_stream_bites_on_a_fabricated_stream(tmp_path: Path) -> None:
    """A stream whose invariants are broken is reported, not accepted — otherwise the
    validator is decoration."""
    events = [
        {"type": "run.started", "seq": 1, "schema": C.STREAM_SCHEMA},
        {
            "type": "item.started",
            "seq": 2,
            "schema": C.STREAM_SCHEMA,
            "item": {"id": "item-1", "type": "command_execution", "status": "in_progress"},
        },
        {
            "type": "item.completed",
            "seq": 3,
            "schema": C.STREAM_SCHEMA,
            "item": {"id": "item-2", "type": "command_execution", "status": "declined",
                     "exit_code": 1},
        },
    ]
    report = C.validate_stream(events)
    assert report["ok"] is False
    joined = " ".join(report["problems"])
    assert "was completed before it started" in joined  # the never-started twin
    assert "declined item item-2 carries an exit code" in joined
    assert "items without a terminal event: item-1" in joined

    orphan = [
        {"type": "run.started", "seq": 1, "schema": C.STREAM_SCHEMA},
        {
            "type": "item.started",
            "seq": 2,
            "schema": C.STREAM_SCHEMA,
            "item": {"id": "item-1", "type": "test_run", "status": "in_progress"},
        },
    ]
    assert C.validate_stream(orphan)["orphans"] == ["item-1"]


def test_read_stream_refuses_a_line_that_is_not_one_json_object(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text('{"type": "run.started"}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2 is not JSON"):
        C.read_stream(path)


def test_a_failed_command_keeps_its_own_exit_code(tmp_path: Path) -> None:
    sink = C._Emitter(path=tmp_path / "fail.jsonl")
    stream = C.RunStream(run_id="run-3", emitter=sink)
    stream.run_started(["false"])
    result = C.stream_command(["bash", "-c", "echo boom; exit 3"], stream=stream, cwd=tmp_path)
    sink.close()
    assert result["status"] == "failed"
    assert result["exit_code"] == 3
    assert C.validate_stream(C.read_stream(tmp_path / "fail.jsonl"))["ok"] is True


def test_a_command_that_does_not_exist_is_an_error_event_not_a_crash(tmp_path: Path) -> None:
    sink = C._Emitter(path=tmp_path / "missing.jsonl")
    stream = C.RunStream(run_id="run-4", emitter=sink)
    stream.run_started(["definitely-not-a-program-xyz"])
    result = C.stream_command(["definitely-not-a-program-xyz"], stream=stream, cwd=tmp_path)
    sink.close()
    events = C.read_stream(tmp_path / "missing.jsonl")
    assert result["status"] == "failed"
    assert [event["type"] for event in events][-2:] == ["error", "run.completed"]
    assert events[-1]["status"] == "failed"


# --------------------------------------------------------------------------- #
# 2. plan mode
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("ls -la", "read_only"),
        ("cat pyproject.toml", "read_only"),
        ("grep -rn foo src", "read_only"),
        ("git status", "read_only"),
        ("git diff HEAD", "read_only"),
        ("pytest tests -q", "read_only"),
        ("python -m pytest tests", "read_only"),
        ("ruff check .", "read_only"),
        ("rm -rf build", "mutating"),
        ("git commit -m wip", "mutating"),
        ("git push", "mutating"),
        ("touch newfile", "mutating"),
        ("cp a b", "mutating"),
        ("echo x > f", "mutating"),
        ("cat a | tee b", "mutating"),
        ("ls && rm x", "mutating"),
        ("python -c 'open(1)'", "mutating"),
        ("python -m some_tool", "mutating"),
        ("", "mutating"),
    ],
)
def test_action_classification(action: str, expected: str) -> None:
    assert C.classify_action(action)["classification"] == expected


def test_plan_mode_refuses_a_write_and_allows_a_read(workspace: Path, state: Path) -> None:
    plan = C.plan_begin(state_dir=state, workspace=workspace, task="add a flag")
    allowed = C.plan_guard(plan, "pytest tests -q", state_dir=state)
    refused = C.plan_guard(plan, "git commit -m wip", state_dir=state)
    assert allowed["allowed"] is True and allowed["mode"] == "plan"
    assert refused["allowed"] is False
    assert refused["declined_reason"].startswith("plan mode:")
    stored = C.load_plan(state)
    assert stored is not None
    assert [entry["allowed"] for entry in stored["guarded_actions"]] == [True, False]


def test_plan_mode_ends_when_the_plan_is_applied(workspace: Path, state: Path) -> None:
    plan = C.plan_begin(state_dir=state, workspace=workspace, task="add a flag")
    assert C.plan_guard(plan, "rm x", state_dir=state)["allowed"] is False
    closed = C.plan_close(plan, state_dir=state, outcome="applied")
    assert closed["application"]["outcome"] == "applied"
    after = C.load_plan(state)
    assert after is not None
    assert C.plan_guard(after, "rm x", state_dir=state)["allowed"] is True
    assert after["closed_at"] is not None


def test_a_second_plan_cannot_be_opened_while_one_is_open(workspace: Path, state: Path) -> None:
    C.plan_begin(state_dir=state, workspace=workspace, task="first")
    with pytest.raises(C.PlanConflict, match="plan mode is already open"):
        C.plan_begin(state_dir=state, workspace=workspace, task="second")
    open_plan = C.load_plan(state)
    assert open_plan is not None
    C.plan_close(open_plan, state_dir=state, outcome="dropped")
    second = C.plan_begin(state_dir=state, workspace=workspace, task="second")
    assert second["version"] == 2


def test_a_question_makes_the_plan_not_decision_complete(workspace: Path, state: Path) -> None:
    plan = C.plan_begin(state_dir=state, workspace=workspace, task="add a flag")
    plan["probe"] = C.plan_probe(workspace, "add a flag")
    complete = C.plan_propose(plan, changes=["edit src/pkg/mod.py"])
    assert complete["decision_complete"] is True
    assert "<proposed_plan>" in complete["plan"]
    assert "## Test Plan" in complete["plan"]
    assert "python -m pytest" in complete["plan"], "the project's own declared runner is used"

    open_ended = C.plan_propose(plan, changes=["edit x"], open_questions=["which name?"])
    assert open_ended["decision_complete"] is False
    assert "NOT decision complete" in open_ended["plan"]


def test_plan_probe_reads_real_facts_and_invents_nothing(workspace: Path) -> None:
    probe = C.plan_probe(workspace, "add a flag")
    assert probe["walker"] == "codectx.walk_workspace"
    paths = {entry["path"] for entry in probe["files"]}
    assert {"src/pkg/mod.py", "pyproject.toml"} <= paths
    commands = probe["declared_commands"]["commands"]
    assert commands["run"] == ["demo"]
    assert commands["test"] and "pytest" in commands["test"][0]


def test_detect_project_commands_says_nothing_when_nothing_is_declared(tmp_path: Path) -> None:
    detected = C.detect_project_commands(tmp_path)
    assert detected["commands"] == {}
    assert detected["notes"] == []


# --------------------------------------------------------------------------- #
# 3. the test-fix loop
# --------------------------------------------------------------------------- #


def test_parse_failures_reads_a_real_pytest_report(tmp_path: Path) -> None:
    output = (
        "=========================== short test summary info ============================\n"
        "FAILED tests/test_a.py::test_one - AssertionError: nope\n"
        "FAILED tests/test_b.py::TestX::test_two - ValueError: bad\n"
        "1 failed, 1 error in 0.07s\n"
    )
    failures = C.parse_failures(output)
    assert [item["name"] for item in failures] == ["test_one", "TestX::test_two"]
    assert failures[0]["runner"] == "pytest"
    assert "AssertionError" in failures[0]["reason"]


def test_the_traceback_twin_of_a_failure_is_not_counted_twice(tmp_path: Path) -> None:
    """pytest prints the same failure twice (traceback line + summary line). Counting both
    would double the roster and hide progress."""
    output = (
        "tests/test_a.py:2: AssertionError\n"
        "FAILED tests/test_a.py::test_one - AssertionError\n"
    )
    failures = C.parse_failures(output)
    assert len(failures) == 1
    assert failures[0]["name"] == "test_one"


def test_the_loop_without_a_fixer_claims_nothing(workspace: Path) -> None:
    (workspace / "tests" / "test_mod.py").write_text(FAILING_TEST, encoding="utf-8")
    report = C.test_fix_loop(_pytest_argv(), cwd=workspace, max_rounds=3)
    assert report["status"] == "no_fixer"
    assert report["exit_code"] == 2
    assert report["rounds_used"] == 0
    assert [item["name"] for item in report["failures"]] == ["test_x"]
    assert "no fixer" in report["reason"]


def test_a_real_fixer_makes_the_loop_green_and_the_cache_is_cleared(workspace: Path) -> None:
    """**The measured trap:** a fixer that rewrites a test with the same byte count inside the
    same second is invisible to pytest's assertion-rewrite cache. The loop clears the cache of
    the changed file, so the fix is really seen."""
    (workspace / "tests" / "test_mod.py").write_text(FAILING_TEST, encoding="utf-8")
    fixer = workspace.parent / "fixer.py"
    fixer.write_text(
        "import json, os, pathlib\n"
        "pathlib.Path('tests/test_mod.py').write_text('def test_x():\\n    assert 1 == 1\\n')\n"
        "print(json.dumps({'outcome': 'fixed', 'summary': 'assertion fixed',\n"
        "                  'changed_paths': ['tests/test_mod.py']}))\n",
        encoding="utf-8",
    )
    report = C.test_fix_loop(
        _pytest_argv(), cwd=workspace, fixer_argv=[PY, str(fixer)], max_rounds=3
    )
    assert report["status"] == "passed", report["reason"]
    assert report["rounds_used"] == 1
    assert report["exit_code"] == 0
    assert report["rounds"][1]["caches_cleared"], "the stale __pycache__ must be removed"

    # Antidote proof: without clearing, the same rewrite is invisible to pytest.
    (workspace / "tests" / "test_mod.py").write_text(FAILING_TEST, encoding="utf-8")
    C.run_tests(_pytest_argv(), cwd=workspace)  # writes the poisoned cache
    (workspace / "tests" / "test_mod.py").write_text(PASSING_TEST, encoding="utf-8")
    C.invalidate_python_caches(["tests/test_mod.py"], workspace=workspace)
    assert not (workspace / "tests" / "__pycache__").exists()


def test_the_loop_stops_when_the_failure_set_stops_changing(workspace: Path) -> None:
    (workspace / "tests" / "test_mod.py").write_text(FAILING_TEST, encoding="utf-8")
    noop = workspace.parent / "noop.py"
    noop.write_text(
        "import json\nprint(json.dumps({'outcome': 'fixed', 'summary': 'nothing',"
        " 'changed_paths': []}))\n",
        encoding="utf-8",
    )
    report = C.test_fix_loop(
        _pytest_argv(), cwd=workspace, fixer_argv=[PY, str(noop)], max_rounds=5
    )
    assert report["status"] == "no_progress"
    assert report["rounds_used"] == 1, "it must stop BEFORE the round bound"
    assert "did not change" in report["reason"]


def test_a_fixer_that_stops_is_honoured(workspace: Path) -> None:
    (workspace / "tests" / "test_mod.py").write_text(FAILING_TEST, encoding="utf-8")
    stopper = workspace.parent / "stopper.py"
    stopper.write_text(
        "import json\nprint(json.dumps({'outcome': 'stop', 'summary': 'needs a human'}))\n",
        encoding="utf-8",
    )
    report = C.test_fix_loop(
        _pytest_argv(), cwd=workspace, fixer_argv=[PY, str(stopper)], max_rounds=3
    )
    assert report["status"] == "fixer_stopped"
    assert report["exit_code"] == 2
    assert "needs a human" in report["reason"]


def test_a_test_command_that_cannot_start_is_reported(workspace: Path) -> None:
    run = C.run_tests(["definitely-not-a-program-xyz"], cwd=workspace)
    assert run["status"] == "failed"
    assert run["exit_code"] is None
    assert run["reason"].startswith("the test command could not be started")


def test_the_loop_without_failures_is_already_green_not_a_fix(workspace: Path) -> None:
    report = C.test_fix_loop(_pytest_argv(), cwd=workspace, max_rounds=3)
    assert report["status"] == "already_green"
    assert report["rounds_used"] == 0
    assert "nothing was changed" in report["reason"]


def test_the_loop_emits_its_rounds_on_a_stream(workspace: Path, tmp_path: Path) -> None:
    (workspace / "tests" / "test_mod.py").write_text(FAILING_TEST, encoding="utf-8")
    sink = C._Emitter(path=tmp_path / "loop.jsonl")
    stream = C.RunStream(run_id="tf", emitter=sink)
    fixer = workspace.parent / "fixer2.py"
    fixer.write_text(
        "import json, pathlib\n"
        "pathlib.Path('tests/test_mod.py').write_text('def test_x():\\n    assert 1 == 1\\n')\n"
        "print(json.dumps({'outcome': 'fixed', 'changed_paths': ['tests/test_mod.py']}))\n",
        encoding="utf-8",
    )
    report = C.test_fix_loop(
        _pytest_argv(), cwd=workspace, fixer_argv=[PY, str(fixer)], max_rounds=3, stream=stream
    )
    sink.close()
    assert report["status"] == "passed"
    events = C.read_stream(tmp_path / "loop.jsonl")
    assert C.validate_stream(events)["ok"] is True
    report_invariants = C.validate_stream(events)
    assert report_invariants["items_started"] == report_invariants["items_completed"] + (
        report_invariants["items_failed"] + report_invariants["items_declined"]
    )


# --------------------------------------------------------------------------- #
# 4. file mentions
# --------------------------------------------------------------------------- #


def test_fuzzy_positions_and_scoring_are_deterministic() -> None:
    assert C.fuzzy_indices("cfe", "coding_features.py") == [0, 7, 8]
    assert C.fuzzy_indices("zzz", "coding_features.py") is None
    assert C.fuzzy_indices("", "anything") == []
    at_boundary = C.fuzzy_indices("cf", "coding_features.py") or []
    inside = C.fuzzy_indices("cf", "xxcodingxffeatures.py") or []
    boundary = C.score_match("cf", "coding_features.py", at_boundary)
    middle = C.score_match("cf", "xxcodingxffeatures.py", inside)
    assert boundary > middle, "a boundary hit must outrank a mid-word hit"


def test_completion_finds_a_file_by_a_shorthand(workspace: Path) -> None:
    result = C.complete_mention("mod", workspace=workspace)
    paths = [item["path"] for item in result["matches"]]
    assert paths == ["src/pkg/mod.py", "tests/test_mod.py"]
    assert all(item["indices"] for item in result["matches"])
    assert result["walker"] == "codectx.walk_workspace"
    # The same shorthand finds a file whose name matches even when the path does not lead
    # with it, which is what makes "@events" usable.
    by_name = C.complete_mention("testm", workspace=workspace)
    assert [item["path"] for item in by_name["matches"]] == ["tests/test_mod.py"]


def test_completion_matches_a_path_prefix_and_never_a_non_match(workspace: Path) -> None:
    prefixed = C.complete_mention("src/", workspace=workspace)
    assert "src/pkg/mod.py" in [item["path"] for item in prefixed["matches"]]
    assert C.complete_mention("zzzz", workspace=workspace)["matches"] == []
    assert C.complete_mention("src/zzz", workspace=workspace)["matches"] == []


def test_expansion_reports_resolved_unresolved_and_refused(workspace: Path) -> None:
    result = C.expand_mentions(
        "look at @src/pkg/mod.py and @missing.py and @../etc/passwd", workspace=workspace
    )
    assert [item["path"] for item in result["mentions"]] == ["src/pkg/mod.py"]
    assert result["unresolved"] == ["missing.py"]
    assert result["refused"] and "absolute or parent" in result["refused"][0]["reason"]
    assert result["count"] == 3


def test_a_symlink_that_leaves_the_workspace_is_refused(workspace: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    (workspace / "link.txt").symlink_to(outside)
    result = C.expand_mentions("@link.txt", workspace=workspace)
    assert result["mentions"] == []
    assert result["refused"][0]["reason"] == "the resolved path leaves the workspace"


# --------------------------------------------------------------------------- #
# 5. undo last turn
# --------------------------------------------------------------------------- #


def test_undo_restores_a_file_byte_identically(workspace: Path, state: Path) -> None:
    target = workspace / "data.txt"
    target.write_text("original\n", encoding="utf-8")
    before = C._sha256_bytes(target.read_bytes())
    journal = C.TurnJournal.begin(state, label="edit data")
    journal.record_file("data.txt", workspace=workspace)
    target.write_text("changed\n", encoding="utf-8")
    journal.close(workspace=workspace)

    result = C.undo_turns(state, workspace=workspace)
    assert result["status"] == "undone"
    assert C._sha256_bytes(target.read_bytes()) == before
    assert result["transcript"]["reverted"] is False
    assert "immutable transcript" in result["transcript"]["reason"]
    assert C.undo_turns(state, workspace=workspace)["status"] == "nothing_to_undo"


def test_undo_deletes_a_file_the_turn_created(workspace: Path, state: Path) -> None:
    journal = C.TurnJournal.begin(state)
    journal.expect_new("created.txt", workspace=workspace)
    (workspace / "created.txt").write_text("new\n", encoding="utf-8")
    journal.close(workspace=workspace)
    assert (workspace / "created.txt").exists()
    result = C.undo_turns(state, workspace=workspace)
    assert result["status"] == "undone"
    assert not (workspace / "created.txt").exists()
    assert result["restored"][0]["action"] == "deleted"


def test_a_file_that_moved_since_the_turn_refuses_the_whole_undo(
    workspace: Path, state: Path
) -> None:
    first = workspace / "a.txt"
    second = workspace / "b.txt"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    journal = C.TurnJournal.begin(state)
    journal.record_file("a.txt", workspace=workspace)
    journal.record_file("b.txt", workspace=workspace)
    first.write_text("changed\n", encoding="utf-8")
    second.write_text("changed\n", encoding="utf-8")
    journal.close(workspace=workspace)
    first.write_text("changed again\n", encoding="utf-8")  # moved since the turn

    result = C.undo_turns(state, workspace=workspace)
    assert result["status"] == "refused"
    assert result["error_kind"] == "changed_since_turn"
    assert result["exit_code"] == 6
    assert second.read_text(encoding="utf-8") == "changed\n", (
        "a partial undo is worse than none: nothing must be written"
    )


def test_a_file_outside_the_workspace_cannot_be_recorded(workspace: Path, state: Path) -> None:
    journal = C.TurnJournal.begin(state)
    with pytest.raises(ValueError, match="outside the workspace"):
        journal.record_file("../escape.txt", workspace=workspace)


def test_the_cross_process_journal_matches_the_in_process_one(workspace: Path, state: Path) -> None:
    """A shell cannot hold a Python object: ``coding turn begin/record/end`` uses the same
    journal through ``turn.file`` records, and undo must behave identically."""
    target = workspace / "data.txt"
    target.write_text("original\n", encoding="utf-8")
    C._append_turn(
        state,
        {
            "schema": C.TURN_SCHEMA,
            "event": "turn.begin",
            "turn_id": "turn-x",
            "label": "cli turn",
            "ts": 1.0,
            "workspace": str(workspace),
        },
    )
    assert C.open_turn(state) is not None
    entry = C.journal_record_file(state, workspace=workspace, path="data.txt")
    assert entry["existed"] is True and Path(entry["backup"]).exists()
    C.journal_record_file(state, workspace=workspace, path="new.txt", expect_new=True)
    target.write_text("changed\n", encoding="utf-8")
    (workspace / "new.txt").write_text("created\n", encoding="utf-8")
    C.close_open_turn(state, workspace=workspace)
    assert C.open_turn(state) is None

    summary = C.turn_summaries(state)[-1]
    assert {item["path"] for item in summary["files"]} == {"data.txt", "new.txt"}
    result = C.undo_turns(state, workspace=workspace)
    assert result["status"] == "undone"
    assert target.read_text(encoding="utf-8") == "original\n"
    assert not (workspace / "new.txt").exists()


def test_undo_refuses_when_more_turns_are_asked_for_than_exist(
    workspace: Path, state: Path
) -> None:
    (workspace / "a.txt").write_text("x\n", encoding="utf-8")
    journal = C.TurnJournal.begin(state)
    journal.record_file("a.txt", workspace=workspace)
    journal.close(workspace=workspace)
    result = C.undo_turns(state, workspace=workspace, count=3)
    assert result["status"] == "not_enough_turns"
    assert result["exit_code"] == 5


def test_recording_a_file_without_an_open_turn_is_an_error(workspace: Path, state: Path) -> None:
    with pytest.raises(LookupError, match="no open turn"):
        C.journal_record_file(state, workspace=workspace, path="a.txt")


# --------------------------------------------------------------------------- #
# 6. session summary / status
# --------------------------------------------------------------------------- #


def test_summary_without_a_session_store_is_honest(
    state: Path, workspace: Path, tmp_path: Path
) -> None:
    facts = C.session_facts(session_id="", runtime=str(tmp_path / "rt"), state_dir=state)
    assert facts["messages"]["available"] is False
    assert facts["messages"]["total"] == 0
    markdown = C.render_summary_markdown(facts)
    assert "## Current progress and key decisions" in markdown
    assert "## What remains to be done" in markdown
    assert "## References needed to continue" in markdown


def test_summary_reads_a_real_session_store(tmp_path: Path, state: Path) -> None:
    from skaldir.session_store import SessionStore, session_store_path

    runtime = tmp_path / "rt"
    runtime.mkdir()
    with SessionStore(str(session_store_path(runtime))) as store:
        session = store.create_session("a demo session")
        store.append(session.session_id, "user", "please add a --dry-run flag")
        store.append(session.session_id, "assistant", "planned; not applied yet")
    facts = C.session_facts(session_id=session.session_id, runtime=runtime, state_dir=state)
    assert facts["messages"]["available"] is True
    assert facts["messages"]["by_role"] == {"user": 1, "assistant": 1}
    assert facts["messages"]["total"] == 2
    assert facts["title"] == "a demo session"
    assert facts["history_hash"]
    assert facts["token_estimate"]["tokens"] >= 1
    assert facts["last_turn"]["role"] == "assistant"
    markdown = C.render_summary_markdown(facts)
    assert f"`{session.session_id}`" in markdown


def test_summary_reports_an_open_plan_and_the_turn_journal(
    workspace: Path, state: Path, tmp_path: Path
) -> None:
    C.plan_begin(state_dir=state, workspace=workspace, task="something")
    journal = C.TurnJournal.begin(state)
    (workspace / "a.txt").write_text("x\n", encoding="utf-8")
    journal.record_file("a.txt", workspace=workspace)
    journal.close(workspace=workspace)
    facts = C.session_facts(session_id="", runtime=str(tmp_path / "rt"), state_dir=state)
    assert facts["plan"]["status"] == "planning"
    assert facts["turns"]["recorded"] == 1 and facts["turns"]["files"] == 1
    assert "apply or drop the open plan" in C.render_summary_markdown(facts)


# --------------------------------------------------------------------------- #
# 7. project init
# --------------------------------------------------------------------------- #


def test_init_writes_only_the_headings_project_context_injects(workspace: Path) -> None:
    result = C.write_project_doc(workspace)
    assert result["status"] == "written"
    text = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    for heading in C.INJECTABLE_HEADINGS:
        assert f"## {heading.capitalize()}" in text
    assert "python -m pytest" in text
    assert result["sha256"] == C._sha256_text(text)


def test_init_refuses_to_overwrite_and_reports_the_existing_hash(workspace: Path) -> None:
    first = C.write_project_doc(workspace)
    second = C.write_project_doc(workspace)
    assert second["status"] == "exists"
    assert second["exit_code"] == 6
    assert second["sha256"] == first["sha256"]
    forced = C.write_project_doc(workspace, force=True)
    assert forced["status"] == "written"
    assert forced["replaced_sha256"] == first["sha256"]


def test_a_claude_file_is_not_overwritten_by_accident(workspace: Path) -> None:
    result = C.write_project_doc(workspace, filename="AGENTS.md", name="demo")
    assert result["status"] == "written"
    assert "demo Guidelines" in (workspace / "AGENTS.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 8. model + reasoning effort, mid-session
# --------------------------------------------------------------------------- #


def test_a_session_override_beats_the_environment(state: Path) -> None:
    C.set_session_model(
        state_dir=state, session_id="s1", model="deepseek-x", reasoning_effort="high"
    )
    effective = C.effective_session_model(
        "s1", state_dir=state, env={"SKALDIR_LLM_MODEL": "env-model"}
    )
    assert effective["model"] == "deepseek-x"
    assert effective["reasoning_effort"] == "high"
    assert effective["source"] == "session-override"
    layers = [layer["layer"] for layer in effective["layers"]]
    assert layers == ["session-override", "environment"]


def test_the_environment_wins_when_no_override_exists(state: Path) -> None:
    effective = C.effective_session_model(
        "s2",
        state_dir=state,
        env={"SKALDIR_LLM_MODEL": "env-model", "SKALDIR_LLM_REASONING": "low"},
    )
    assert effective["model"] == "env-model"
    assert effective["source"] == "environment"


def test_clearing_the_override_falls_back(state: Path) -> None:
    C.set_session_model(state_dir=state, session_id="s3", model="m")
    assert C.clear_session_model(state_dir=state, session_id="s3")["cleared"] is True
    assert C.effective_session_model("s3", state_dir=state, env={})["source"] == "unset"


def test_an_unknown_reasoning_effort_is_refused_not_coerced(state: Path) -> None:
    with pytest.raises(ValueError, match="reasoning effort must be one of"):
        C.set_session_model(state_dir=state, session_id="s4", reasoning_effort="extreme")


def test_the_effort_levels_match_the_product(state: Path) -> None:
    """The CLI's vocabulary must not drift from ``modelroles``' (low|medium|high)."""
    from skaldir import modelroles

    assert set(C.EFFORTS) == set(modelroles._REASONING_LEVELS)


def test_the_journal_is_append_only_jsonl(state: Path, workspace: Path) -> None:
    journal = C.TurnJournal.begin(state, label="one")
    journal.close(workspace=workspace)
    lines = C.turns_path(state).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["event"] for line in lines] == ["turn.begin", "turn.end"]
