"""``skaldir run`` on a terminal: the run hands the screen to the chat, with its own goal id.

The owner's rule for the redesigned surface is that ``skaldir run "<goal>"`` *sets the goal and
opens the chat attached to it* — the run is the thing that owns the terminal, and the chat is its
surface. What that has to mean, measurably:

* the chat is opened with the **durable** goal id the engine published (the id of a real row in
  ``runs.db``), the run's own state dir and the run's progress feed — never with a guessed id,
  never unattached while a goal exists;
* when the chat closes, the run is asked to stop **at its next turn boundary** (the control
  object is shared with the engine, so the stop is visible to it);
* the process still returns the *run's* exit code and the run's own report, so closing the chat
  cannot turn a failed run into a success.

These tests stub ``run_goal`` and ``launch_attached_chat`` — the two seams — and assert the
hand-over itself: no model, no credential and no terminal is involved.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from skaldir import chatbar, cli, cli_contract, goal_run_cli

EXIT_BLOCKED = int(getattr(cli_contract, "EXIT_BLOCKED", 3))


def _args(**overrides: object) -> argparse.Namespace:
    """A real ``run`` namespace, parsed by the product's own parser, plus test overrides."""
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "run",
            "make the checks pass",
            "--workspace",
            str(overrides.pop("workspace", ".")),
            "--state",
            str(overrides.pop("state", ".")),
            "--no-color",
        ]
    )
    # the test owns the clock: the hand-over must not wait for a real wall-clock budget
    args.wall_clock_s = 1.0
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _report_for(goal_id: str, *, exit_code: int, verdict: str) -> dict:
    return {
        "goal_id": goal_id,
        "goal": "make the checks pass",
        "workspace": ".",
        "session": "goal-run",
        "terminal_state": "VERIFIED_COMPLETE" if exit_code == 0 else "BLOCKED",
        "exit_code": exit_code,
        "turns_run": 2,
        "completions": [],
        "accepted_completions": 0,
        "compactions": [],
        "criteria": [],
        "verify": {"verdict": verdict, "counts": {}},
    }


def _seams(monkeypatch, *, publish: str, report: dict, chat_code: int = 0,
           raises: Exception | None = None) -> dict:
    """Stub the two seams of the hand-over and record what each one was told."""
    seen: dict = {}

    def fake_run_goal(options, *, client, asker, progress, on_goal, control):  # noqa: ANN001
        seen["options"] = options
        seen["control"] = control
        seen["asker"] = asker
        if publish:
            on_goal(publish)          # the engine's own announcement, once the goal is durable
            progress("turn", {"turn": 1, "goal_id": publish})
        if raises is not None:
            raise raises
        return dict(report)

    def fake_launch(**kwargs):  # noqa: ANN003
        seen["launch"] = kwargs
        return chat_code

    monkeypatch.setattr(goal_run_cli, "run_goal", fake_run_goal)
    monkeypatch.setattr(
        goal_run_cli,
        "_client_and_status",
        lambda args: (object(), {"ready": True, "provider": "stub"}),
    )
    monkeypatch.setattr(chatbar, "launch_attached_chat", fake_launch)
    return seen


def _options(tmp_path: Path) -> object:
    """The run's own options object, built by the product from a parsed namespace."""
    return goal_run_cli._options_from(_args(workspace=tmp_path, state=tmp_path), Path(tmp_path))


def test_the_chat_gets_the_goal_id_the_run_published(tmp_path, monkeypatch):
    seen = _seams(
        monkeypatch,
        publish="g-1a2b3c4d",
        report=_report_for("g-1a2b3c4d", exit_code=0, verdict="PASS"),
    )
    code = goal_run_cli._cmd_run_with_chat(
        _args(workspace=tmp_path, state=tmp_path), _options(tmp_path), Path(tmp_path)
    )

    assert code == 0
    launch = seen["launch"]
    # the chat is the surface *of that run*: the durable handle, the run's runtime, its feed
    assert launch["goal_id"] == "g-1a2b3c4d"
    assert launch["runtime"] == str(tmp_path)
    assert launch["feed"] is not None
    assert launch["no_color"] is True
    # …and the engine was given the very control object the chat's close reaches
    assert seen["control"].stop_requested is True
    assert seen["control"].stop_reason == "the chat was closed"


def test_closing_the_chat_does_not_turn_a_blocked_run_into_a_success(tmp_path, monkeypatch):
    seen = _seams(
        monkeypatch,
        publish="g-9f8e7d6c",
        report=_report_for("g-9f8e7d6c", exit_code=2, verdict="FAIL"),
        chat_code=0,
    )
    code = goal_run_cli._cmd_run_with_chat(
        _args(workspace=tmp_path, state=tmp_path), _options(tmp_path), Path(tmp_path)
    )

    # the run's own verdict decides the exit code; the chat closing cleanly (0) does not
    assert code != 0
    assert seen["launch"]["goal_id"] == "g-9f8e7d6c"


def test_a_run_that_never_records_a_goal_opens_the_chat_unattached(tmp_path, monkeypatch):
    """A run that announced no handle still gets a chat — it just is not attached to a goal."""
    seen = _seams(
        monkeypatch,
        publish="",                       # the engine never announced a handle
        report=_report_for("", exit_code=0, verdict="PASS"),
        chat_code=EXIT_BLOCKED,
    )
    code = goal_run_cli._cmd_run_with_chat(
        _args(workspace=tmp_path, state=tmp_path), _options(tmp_path), Path(tmp_path)
    )

    # the chat still opens (the human is not left staring at a run that never started) and is
    # told plainly that there is no goal; the code is the *run's* report, not the chat's
    assert seen["launch"]["goal_id"] == ""
    assert code == 0


def test_a_run_that_refuses_before_publishing_returns_its_own_code(tmp_path, monkeypatch):
    """The refusal path: no report at all, so the refusal's exit code is the one returned."""
    refusal = goal_run_cli.GoalRunError(
        "this goal is already VERIFIED_COMPLETE", code=EXIT_BLOCKED
    )
    seen = _seams(
        monkeypatch,
        publish="",
        report=_report_for("", exit_code=0, verdict="PASS"),
        chat_code=0,
        raises=refusal,
    )
    code = goal_run_cli._cmd_run_with_chat(
        _args(workspace=tmp_path, state=tmp_path), _options(tmp_path), Path(tmp_path)
    )

    assert code == EXIT_BLOCKED
    assert seen["launch"]["goal_id"] == ""
    assert seen["control"].stop_requested is True
