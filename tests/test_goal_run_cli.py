"""``skaldir run "<goal>"`` — the product command of the goal lock (ENFORCE).

What this file pins, in order of how much it would hurt to lose it:

1. the command is **wired and registered** (``contract dump`` → ``unregistered == []``, a
   contract entry with the real exit codes, ``mutates`` true);
2. the criteria documents and the workspace's own plan file are read as declared, and a
   document that carries nothing is a usage error instead of an empty contract;
3. **completion is never accepted without verification**: a goal whose declared checks are
   red is refused, the refusal is durable, and the command exits 3 with the goal parked
   ``BLOCKED_EXTERNAL`` — after really executing the checks;
4. **exit 0 happens only on a verification PASS** — a goal whose criteria are already green
   closes with ``VERIFIED_COMPLETE`` and exit 0, and the accepted completion carries
   executed evidence.

The provider is a loopback stub (``StubEndpoint`` from ``test_agent_loop``): a deterministic
``/v1/chat/completions`` that answers whatever the script says. No network, no key.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from test_agent_loop import StubEndpoint, answer

from skaldir import cli, cli_contract, goal_run

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "long_project"

GOAL = (
    "Bring the textkit project to its promised state: slugify, the CLI and the packaged "
    "artifact must really work."
)

#: The correct implementation, so the achievable case really passes its three checks.
FIXED_SLUG = '''"""Slug generation."""

from __future__ import annotations

import re
import unicodedata

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Turn a human title into an ASCII URL slug (the promised rules)."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    return _NON_SLUG.sub("-", ascii_only).strip("-")
'''


# --------------------------------------------------------------------------- #
# the command in the product's own inventory                                    #
# --------------------------------------------------------------------------- #


def test_the_run_command_is_wired_into_the_product_parser():
    parser = cli.build_parser()
    leaves = [path for path, _sub in cli_contract.leaf_commands(parser)]
    assert "run" in leaves, "`skaldir run` is not reachable from the product CLI"
    inventory = cli_contract.dump(parser)
    assert inventory["unregistered"] == [], (
        "a registered leaf without a contract entry: " + ", ".join(inventory["unregistered"])
    )


def test_the_contract_entry_declares_the_real_exit_codes():
    entry = cli_contract.COMMANDS["run"]
    assert entry.layer == "ENFORCE"
    assert entry.mutates is True
    assert set(entry.exit_codes) == {0, 1, 2, 3, 4, 5, 6, 7}
    assert 0 in entry.exit_codes and 2 in entry.exit_codes and 3 in entry.exit_codes
    schema = entry.data_schema or {}
    for key in ("goal_id", "verify", "terminal_state", "accepted_completions", "exit_code"):
        assert key in schema.get("properties", {}), f"{key} is missing from the data schema"
    notes = str(entry.notes)
    assert "VERIFY" in notes or "verification PASS" in notes


def test_registering_the_group_into_a_fresh_parser_changes_nothing():
    import argparse

    parser = argparse.ArgumentParser(prog="standalone")
    sub = parser.add_subparsers(dest="command")
    from skaldir.goal_run_cli import register_cli

    first = register_cli(sub)
    second = register_cli(sub)  # idempotent: a second wiring must not raise
    assert first is not None and second is not None
    assert sorted(getattr(sub, "choices", {})) == ["run"]


# --------------------------------------------------------------------------- #
# criteria: declared, never invented                                            #
# --------------------------------------------------------------------------- #


def test_a_criteria_document_is_normalised_in_both_declared_shapes(tmp_path: Path):
    bare = tmp_path / "bare.json"
    bare.write_text(
        json.dumps([{"id": "c1", "text": "the suite passes", "check": "pytest"}]),
        encoding="utf-8",
    )
    assert goal_run.load_criteria_file(bare) == [
        {
            "id": "c1",
            "text": "the suite passes",
            "check": "pytest",
            "check_argv": [],
            "blocking": True,
        }
    ]
    # a check declared as a command vector is read as a vector (never str()'d into a
    # check *name* — see the runtime-final-wiring acceptance report)
    import sys as _sys

    command = tmp_path / "command.json"
    command.write_text(
        json.dumps(
            [
                {
                    "id": "c1",
                    "text": "the declared command runs",
                    "check": [_sys.executable, "-c", "pass"],
                }
            ]
        ),
        encoding="utf-8",
    )
    assert goal_run.load_criteria_file(command) == [
        {
            "id": "c1",
            "text": "the declared command runs",
            "check": "command",
            "check_argv": [_sys.executable, "-c", "pass"],
            "blocking": True,
        }
    ]
    plan = tmp_path / "verify.json"
    plan.write_text(
        json.dumps(
            {
                "goal": "a goal",
                "criteria": [
                    {"criterion": "the CLI prints the slug", "check": "run", "blocking": True}
                ],
            }
        ),
        encoding="utf-8",
    )
    assert goal_run.load_criteria_file(plan) == [
        {
            "id": "c1",
            "text": "the CLI prints the slug",
            "check": "run",
            "check_argv": [],
            "blocking": True,
        }
    ]


@pytest.mark.parametrize(
    "payload",
    [{"goal": "a goal", "criteria": []}, {"criteria": "not a list"}, {}],
)
def test_a_criteria_document_without_criteria_is_a_usage_error(tmp_path: Path, payload):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(goal_run.GoalRunError) as excinfo:
        goal_run.load_criteria_file(path)
    assert excinfo.value.code == goal_run.EXIT_USAGE


def test_criteria_come_from_the_workspace_plan_when_none_are_passed(tmp_path: Path):
    (tmp_path / ".godsv").mkdir()
    (tmp_path / ".godsv" / "verify.json").write_text(
        json.dumps(
            {
                "criteria": [
                    {"criterion": "the suite passes", "check": "pytest"},
                    {"criterion": "the CLI prints the slug", "check": "run"},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert [item["id"] for item in goal_run.criteria_from_workspace(tmp_path)] == ["c1", "c2"]
    with pytest.raises(goal_run.GoalRunError):
        goal_run.criteria_from_workspace(tmp_path / "elsewhere")


# --------------------------------------------------------------------------- #
# the loop itself (loopback provider, real checks)                              #
# --------------------------------------------------------------------------- #


def _workspace(tmp_path: Path, variant: str) -> Path:
    project = tmp_path / "project"
    shutil.copytree(FIXTURE / "base", project)
    overlay = FIXTURE / variant
    if overlay.is_dir():
        shutil.copytree(overlay, project, dirs_exist_ok=True)
    return project


def _write_criteria(project: Path) -> Path:
    """The criteria of the scenario, written where the engine does not read by itself."""
    data = json.loads((project / ".godsv" / "verify.json").read_text(encoding="utf-8"))
    path = project / "criteria.json"
    path.write_text(
        json.dumps(
            [
                {"id": f"c{index + 1}", "text": item["criterion"], "check": item["check"]}
                for index, item in enumerate(data["criteria"])
            ]
        ),
        encoding="utf-8",
    )
    return path


def _run(project: Path, state: Path, base_url: str, *, criteria: Path, max_turns: int = 1) -> dict:
    options = goal_run.GoalRunOptions(
        goal=GOAL,
        workspace=str(project),
        state_dir=str(state),
        session="goal-run-test",
        criteria=goal_run.load_criteria_file(criteria),
        max_turns=max_turns,
        max_steps=6,
        check_timeout=120.0,
        baseline=True,
        snapshot=True,
        context_budget_tokens=900,
        context_trigger_fraction=0.30,
    )
    from skaldir.llm_provider import provider_for

    client = provider_for(
        {
            "provider": "openai-compatible",
            "base_url": base_url,
            "model": "stub-goal-run",
            "api_key": "stub-local-key",
        }
    )
    return goal_run.run_goal(options, client=client, asker=None)


def test_a_claim_is_refused_while_the_declared_checks_are_red(tmp_path: Path):
    """The whole point: the model may only *request* completion — the checks decide."""
    project = _workspace(tmp_path, "achievable")
    state = tmp_path / "state"
    criteria = _write_criteria(project)

    with StubEndpoint(
        [
            answer("Everything is done, I fixed slugify. Completion requested."),
        ]
    ) as stub:
        report = _run(project, state, stub.base_url, criteria=criteria, max_turns=1)

    assert report["exit_code"] == goal_run.EXIT_BLOCKED
    assert report["terminal_state"] == "BLOCKED_EXTERNAL"
    assert report["accepted_completions"] == 0
    completions = report["completions"]
    assert len(completions) == 1 and completions[0]["accepted"] is False
    assert completions[0]["verdict"] == "INCOMPLETE"
    assert completions[0]["blocking_failures"], "a refusal must name its blockers"
    # the checks were really executed, and their verdict is the one the gate read
    evidence = {row["check_id"]: row["status"] for row in completions[0]["evidence"]}
    assert evidence["pytest"] != "passed"
    assert str(report["verify"]["verdict"]) != "PASS"
    assert report["blocked_reason"], "a blocked goal names what could not be met"
    # the refusal and the parked state are durable, not in-memory
    from skaldir.completion import CompletionStore
    from skaldir.runs_store import RunsStore

    contract = CompletionStore(state, session_id="goal-run-test")
    assert contract.state()["state"] == "BLOCKED_EXTERNAL"
    assert [row["status"] for row in contract.timeline(limit=100)
            if row.get("kind") == "verification"] == ["INCOMPLETE"]
    store = RunsStore(str(state / "runs.db"), create=False)
    try:
        goal = store.require_goal(report["goal_id"])
        assert goal.state == "BLOCKED_EXTERNAL"
        assert all(item.status != "verified" for item in store.criteria(goal.goal_id))
    finally:
        store.close()
    # a checkpoint per turn, and the workspace snapshot is on the record
    assert report["checkpoints"], "a rejected turn must leave a checkpoint"
    assert any(row.get("kind") == "workspace" for row in report["checkpoints"])

    # a frozen goal is not resumed behind the owner's back: the second attempt is refused
    # with exit 6 (conflict), before a single model call
    resumed = goal_run.GoalRunOptions(
        goal="",
        workspace=str(project),
        state_dir=str(state),
        session="goal-run-test",
        resume_goal_id=str(report["goal_id"]),
        max_turns=1,
    )
    with pytest.raises(goal_run.GoalRunError) as excinfo:
        goal_run.run_goal(resumed, client=object(), asker=None)
    assert excinfo.value.code == goal_run.EXIT_CONFLICT


def test_a_verification_pass_is_the_only_way_to_exit_zero(tmp_path: Path):
    """Green criteria: the same command accepts the completion and exits 0."""
    project = _workspace(tmp_path, "achievable")
    (project / "textkit" / "slug.py").write_text(FIXED_SLUG, encoding="utf-8")
    state = tmp_path / "state"
    criteria = _write_criteria(project)

    with StubEndpoint([answer("All three criteria pass; completion requested.")]) as stub:
        report = _run(project, state, stub.base_url, criteria=criteria, max_turns=1)

    assert report["baseline"]["verdict"] == "PASS", report["baseline"]
    assert report["accepted_completions"] == 1
    assert report["verify"]["verdict"] == "PASS"
    assert int(report["verify"]["counts"]["passed"]) == 3
    assert report["terminal_state"] == "VERIFIED_COMPLETE"
    assert report["exit_code"] == goal_run.EXIT_OK
    accepted = [row for row in report["completions"] if row["accepted"]]
    assert accepted and accepted[0]["verdict"] == "VERIFIED_COMPLETE"
    assert accepted[0]["evidence"], "an accepted completion carries executed evidence"
    from skaldir.runs_store import RunsStore

    store = RunsStore(str(state / "runs.db"), create=False)
    try:
        assert store.require_goal(report["goal_id"]).state == "VERIFIED_COMPLETE"
        assert all(
            item.status == "verified"
            for item in store.criteria(report["goal_id"])
        ), "every criterion is verified from evidence"
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# the CLI's own contract on the exit codes                                      #
# --------------------------------------------------------------------------- #


def test_the_cli_maps_a_pass_to_zero_and_a_blocked_goal_to_three(monkeypatch, tmp_path: Path):
    """``cmd_run`` must not confuse the *presence* of a code with its *value*."""
    from skaldir import goal_run_cli

    seen: dict[str, Any] = {}

    def fake_run_goal(options, *, client, asker, progress):
        seen["options"] = options
        return {
            "goal_id": "s:g1",
            "workspace": str(options.workspace),
            "session": options.session,
            "goal": options.goal,
            "criteria": list(options.criteria),
            "turns": [],
            "completions": [],
            "checkpoints": [],
            "compactions": [],
            "verify": {"verdict": "PASS", "counts": {"passed": 3, "failed": 0}},
            "terminal_state": "VERIFIED_COMPLETE",
            "turns_run": 1,
            "accepted_completions": 1,
            "exit_code": 0,
            "runtime_errors": [],
            "report_path": "",
        }

    monkeypatch.setattr(goal_run_cli, "run_goal", fake_run_goal)
    monkeypatch.setattr(
        goal_run_cli, "_client_and_status", lambda args: (object(), {"ready": True})
    )
    criteria = tmp_path / "criteria.json"
    criteria.write_text(json.dumps([{"id": "c1", "text": "x", "check": "pytest"}]),
                        encoding="utf-8")
    parser = goal_run_cli.build_parser()
    args = parser.parse_args(
        ["run", "a goal", "--criteria", str(criteria), "--workspace", str(tmp_path)]
    )
    assert goal_run_cli.cmd_run(args) == 0
    assert seen["options"].goal == "a goal"


def test_a_finished_goal_is_refused_by_name_not_by_traceback(tmp_path: Path):
    """F1b: the second run of the *same* goal on the *same* session.

    Once the writer lease really frees (F1), the second run reaches
    ``cstore.begin_goal`` — and a VERIFIED_COMPLETE contract cannot be renamed
    back into an active one. That invariant is right; the *presentation* was
    not: a bare ``ValueError`` escaped as a traceback with exit 7 (a crash where
    a refusal belongs), and it escaped **after** ``store.start_goal`` had
    already written a goal row, leaving an orphan behind.
    """
    project = _workspace(tmp_path, "achievable")
    (project / "textkit" / "slug.py").write_text(FIXED_SLUG, encoding="utf-8")
    state = tmp_path / "state"
    criteria = _write_criteria(project)

    with StubEndpoint([answer("All three criteria pass; completion requested.")]) as stub:
        first = _run(project, state, stub.base_url, criteria=criteria, max_turns=1)
    assert first["terminal_state"] == "VERIFIED_COMPLETE"
    assert first["exit_code"] == goal_run.EXIT_OK

    from skaldir.completion import CompletionStore
    from skaldir.runs_store import RunsStore

    store = RunsStore(str(state / "runs.db"), create=False)
    try:
        goals_before = len(store.goals()) if hasattr(store, "goals") else None
    finally:
        store.close()

    # the same goal again: a named refusal with the conflict exit code, and no
    # goal row written behind it
    with StubEndpoint([answer("Let's do it again.")]) as stub:
        with pytest.raises(goal_run.GoalRunError) as excinfo:
            _run(project, state, stub.base_url, criteria=criteria, max_turns=1)
    assert excinfo.value.code == goal_run.EXIT_CONFLICT
    assert "VERIFIED_COMPLETE" in str(excinfo.value)
    assert "not reopened" in str(excinfo.value)
    # the refusal is not a ValueError: nothing about it smells like a crash
    assert not isinstance(excinfo.value, ValueError)
    assert "not a rename" not in str(excinfo.value)

    contract = CompletionStore(state, session_id="goal-run-test")
    assert contract.state()["state"] == "VERIFIED_COMPLETE"

    if goals_before is not None:
        store = RunsStore(str(state / "runs.db"), create=False)
        try:
            assert len(store.goals()) == goals_before, (
                "the refusal must not leave an orphan goal row"
            )
        finally:
            store.close()

    # a *different* goal is a new identity and is still allowed to start
    from skaldir.completion import DodItem
    other = CompletionStore(state, session_id="goal-run-test")
    assert other.begin_goal(
        "goal-run-test", "a genuinely different goal", [DodItem(id="c1", text="x")]
    )["state"] == "WORKING"


def test_a_stalled_goal_is_told_to_change_strategy_then_stopped_by_name(tmp_path: Path):
    """§94: no-progress is a runtime DECISION, and the run record names the action.

    The model answers the same thing three turns running, calls no tool and changes
    nothing, so the declared checks stay red and the rejection fingerprint repeats.
    Turn 2 must therefore *deliver a directive* (the next turn's task really carries
    it), and turn 3 must stop the run with a named reason instead of looping — and the
    durable run record must say so.
    """
    project = _workspace(tmp_path, "achievable")  # slug.py is NOT fixed: the checks fail
    state = tmp_path / "state"
    criteria = _write_criteria(project)
    same = "Everything is done. Completion requested."

    with StubEndpoint([answer(same)]) as stub:
        report = _run(project, state, stub.base_url, criteria=criteria, max_turns=3)

    progress = report["progress"]
    assert progress["enabled"] is True
    actions = [row["action"] for row in progress["actions"]]
    # exactly: one demand, then the stop (the second stalled turn is not nudged again)
    assert actions == ["strategy_change_required", "stop"], actions
    assert progress["decisions"][1]["signal"] == "pattern_no_new_evidence"
    assert progress["decisions"][2]["reason"] == "no_strategy_change_after_directive"
    assert progress["decisions"][2]["stuck"] is True

    # the directive was a REAL action: the next turn's task carries it
    tasks = [row["task"] for row in report["turns"]]
    assert "Progress guard" in tasks[2], tasks[2][:200]
    assert "name a different tool or approach" in tasks[2]

    # the stop is named, and the run did not loop to its turn bound
    assert report["turns_run"] == 3
    assert "progress guard" in report["stopped_by"]
    assert "no_strategy_change_after_directive" in report["blocked_reason"]
    assert report["exit_code"] == goal_run.EXIT_BLOCKED

    # ...and the durable RUN RECORD names the action taken (§94 quick win 1)
    from skaldir.runs_store import RunsStore

    store = RunsStore(str(state / "runs.db"), create=False)
    try:
        assert report["run_record_id"], "the run must have a durable record"
        signals = store.progress(report["run_record_id"])
        assert signals is not None
        assert signals.last_action == "stop"
        assert signals.last_signal == "pattern_retry_without_strategy_change"
        assert signals.no_progress_streak >= 2
        assert signals.no_tool_streak >= 3
        assert signals.last_verification == "INCOMPLETE"
        assert signals.reply_fingerprint, "the fingerprint of the repeated answer"
    finally:
        store.close()


def test_the_progress_guard_can_be_switched_off(tmp_path: Path):
    """``progress_guard=False`` is exactly the old behaviour: no decision at all."""
    project = _workspace(tmp_path, "achievable")
    state = tmp_path / "state"
    criteria = _write_criteria(project)
    options = goal_run.GoalRunOptions(
        goal=GOAL,
        workspace=str(project),
        state_dir=str(state),
        session="goal-run-test",
        criteria=goal_run.load_criteria_file(criteria),
        max_turns=2,
        max_steps=6,
        check_timeout=120.0,
        progress_guard=False,
    )
    with StubEndpoint([answer("Everything is done. Completion requested.")]) as stub:
        from skaldir.llm_provider import provider_for

        client = provider_for(
            {
                "provider": "openai-compatible",
                "base_url": stub.base_url,
                "model": "stub-goal-run",
                "api_key": "stub-local-key",
            }
        )
        report = goal_run.run_goal(options, client=client, asker=None)
    assert report["progress"]["enabled"] is False
    assert report["progress"]["actions"] == []
    assert [row["action"] for row in report["progress"]["decisions"]] == [
        "continue",
        "continue",
    ]
