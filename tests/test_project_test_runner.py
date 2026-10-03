"""F05 closing regression: the project's OWN runner, not a pytest hard-wire.

The audit measured the gap exactly: ``goal_run.turn_tools.run_tests`` called
``execute_check("pytest", ...)`` unconditionally, with an empty input schema, and
reported ``exit=None`` forever (``RunEvidence`` carried no exit code at all). A Node
project was therefore told "your pytest suite failed", an empty test project read as
"a failure", and the model never saw the real exit.

The contract this file pins:

* **Detection is a measurement, not a guess.** The runner comes from the project's own
  files (package.json test script, pytest config/test root, Makefile, run_tests.sh) in
  a documented priority order — nobody invents a command.
* **Four different endings stay four different endings:** passed / failed /
  ``no_tests_found`` / ``no_test_runner`` (plus a timeout detail). "Empty test project"
  is never "failed tests" and never a pass.
* **The goal-run tool surface executes the real runner through the real assembly path**
  (``goal_run.turn_tools``) and reports the runner, the real exit code and an explicit
  ``ok`` — no status string the gate could mistake for a pass (``RunEvidence.passed``
  only accepts ``passed``).
* **A declared command vector wins.** With ``argv`` the tool runs exactly that vector,
  like every declared check.

Everything here runs real subprocesses on real fixture workspaces (``tmp_path``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from skaldir import goal_run
from skaldir.completion import execute_check

# --------------------------------------------------------------------------- #
# fixture projects
# --------------------------------------------------------------------------- #

def pyproject_project(tmp_path: Path) -> tuple[Path, Path]:
    """A pytest project whose one test really runs."""
    root = tmp_path / "pytest-project"
    tests = root / "tests"
    tests.mkdir(parents=True)
    (tests / "test_ok.py").write_text("def test_ok():\n    assert 2 + 2 == 4\n")
    return root, tests


def npm_project(tmp_path: Path, *, script: str) -> Path:
    """A Node project whose ``test`` script is real (npm run execs it offline)."""
    root = tmp_path / f"npm-project-{script[:12].replace(' ', '-')}"
    root.mkdir()
    (root / "package.json").write_text(
        json.dumps({"name": "fixture", "private": True, "scripts": {"test": script}}),
        encoding="utf-8",
    )
    return root


def _toolset(root: Path, state: Path) -> tuple[list[Any], dict[str, Any]]:
    bus: dict[str, Any] = {}
    tools = goal_run.turn_tools(
        root, state, "sess-runner", bus,
        goal_run.GoalRunOptions(goal="runner probe", workspace=str(root), state_dir=str(state)),
    )
    return tools, bus


def _tool(tools: list[Any], name: str) -> Any:
    for tool in tools:
        if tool.name == name:
            return tool
    raise AssertionError(f"{name} is not offered by goal turn_tools: "
                          f"{sorted(t.name for t in tools)}")


# --------------------------------------------------------------------------- #
# detection and the distinct states
# --------------------------------------------------------------------------- #

def test_a_pytest_project_runs_green_with_a_real_exit(root_pytest) -> None:
    root, _tests = root_pytest
    evidence = execute_check("tests", root, timeout=600, workspace=str(root), session="s1")
    assert evidence is not None
    assert evidence.status == "passed"
    assert evidence.passed is True
    assert evidence.exit_code == 0, "the real exit code must travel with the evidence"
    assert "pytest" in str(evidence.cmd)


def test_a_pytest_project_with_zero_tests_is_no_tests_not_failed(tmp_path: Path) -> None:
    root = tmp_path / "empty-pytest-project"
    (root / "tests").mkdir(parents=True)  # the marker says pytest, the dir holds no tests
    evidence = execute_check("tests", root, timeout=600, workspace=str(root), session="s1")
    assert evidence is not None
    assert evidence.status == "no_tests_found", (
        f"'empty runnable project' must read as no-tests, got {evidence.status!r}"
    )
    assert evidence.passed is False
    assert evidence.exit_code == 5, "the runner's own 'nothing collected' code is data"


def test_an_npm_project_runs_npm_test_not_pytest(tmp_path: Path) -> None:
    root = npm_project(tmp_path, script="node -e \"console.log('NPM-RAN')\"")
    evidence = execute_check("tests", root, timeout=600, workspace=str(root), session="s1")
    assert evidence is not None
    assert evidence.status == "passed"
    assert evidence.passed is True
    assert "pytest" not in str(evidence.cmd), "the runner was invented, not detected"
    assert str(evidence.cmd).startswith("npm"), f"the detected runner is npm, got {evidence.cmd!r}"
    assert "NPM-RAN" in str(evidence.detail), "the npm script's own output must be reported"


def test_an_npm_project_with_no_tests_found_is_that_state(tmp_path: Path) -> None:
    root = npm_project(tmp_path, script="node -e \"console.log('No tests found');"
                                        "process.exit(1)\"")
    evidence = execute_check("tests", root, timeout=600, workspace=str(root), session="s1")
    assert evidence is not None
    assert evidence.status == "no_tests_found", (
        f"jest's 'No tests found' ending must stay distinguishable, got {evidence.status!r}"
    )
    assert evidence.passed is False


def test_a_project_without_any_runner_reports_no_test_runner(tmp_path: Path) -> None:
    evidence = execute_check("tests", tmp_path, timeout=60, workspace=str(tmp_path),
                             session="s1")
    assert evidence is not None
    assert evidence.status == "no_test_runner"
    assert evidence.passed is False
    assert "looked" in str(evidence.detail).lower() or "probe" in str(evidence.detail).lower(), (
        "the honest non-answer must name what was checked, not a bare shrug"
    )


def test_a_declared_command_vector_wins_over_the_detected_runner(tmp_path: Path) -> None:
    root, _tests = pyproject_project(tmp_path)
    evidence = execute_check(
        "tests", root, argv=[sys.executable, "-c", "print('declared ran'); raise SystemExit(0)"],
        timeout=60, workspace=str(root), session="s1",
    )
    assert evidence is not None
    assert evidence.status == "passed"
    assert evidence.exit_code == 0
    assert "declared ran" in str(evidence.cmd), "the vector runs as declared, not detected"


# --------------------------------------------------------------------------- #
# the goal-run tool surface — the real assembly path
# --------------------------------------------------------------------------- #

def test_the_goal_run_tool_runs_a_pytest_project_for_real(tmp_path: Path) -> None:
    root, _tests = pyproject_project(tmp_path)
    tools, _bus = _toolset(root, tmp_path / "state")
    tool = _tool(tools, "run_tests")
    payload = tool.handler()
    assert payload["status"] == "passed", payload
    assert payload["ok"] is True
    assert payload["exit"] == 0, f"the real exit must be reported, got {payload['exit']!r}"
    assert payload["runner"], "the payload must name the runner it detected"


def test_the_goal_run_tool_runs_an_npm_project_for_real(tmp_path: Path) -> None:
    root = npm_project(tmp_path, script="node -e \"console.log('NPM-GREEN')\"")
    tools, _bus = _toolset(root, tmp_path / "state")
    tool = _tool(tools, "run_tests")
    payload = tool.handler()
    assert payload["status"] == "passed", payload
    assert payload["ok"] is True
    assert payload["runner"] == "npm", f"the npm runner was not detected: {payload!r}"
    assert "NPM-GREEN" in str(payload["detail"])


def test_the_goal_run_tool_reports_no_tests_for_an_empty_pytest_project(
    root_pytest: tuple[Path, Path] | None, tmp_path: Path,
) -> None:
    root = tmp_path / "empty"
    (root / "tests").mkdir(parents=True)
    tools, _bus = _toolset(root, tmp_path / "state")
    payload = _tool(tools, "run_tests").handler()
    assert payload["status"] == "no_tests_found", payload
    assert payload["ok"] is False
    assert payload["exit"] == 5


def test_the_goal_run_tool_refuses_an_empty_project_with_no_test_runner(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    tools, _bus = _toolset(root, tmp_path / "state")
    payload = _tool(tools, "run_tests").handler()
    assert payload["status"] == "no_test_runner", payload
    assert payload["ok"] is False


def test_the_goal_run_tool_accepts_a_declared_command_vector(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    tools, _bus = _toolset(root, tmp_path / "state")
    payload = _tool(tools, "run_tests").handler(
        argv=[sys.executable, "-c", "print('vector ran')"],
    )
    assert payload["status"] == "passed", payload
    assert payload["ok"] is True
    assert payload["exit"] == 0
    assert "vector ran" in str(payload["cmd"]), "the declared vector itself is reported"


def test_the_goal_run_tools_schema_offers_the_vector(tmp_path: Path) -> None:
    """The audit called the schema empty — an argv vector must be offered and validated."""
    root = tmp_path / "plain"
    root.mkdir()
    tools, _bus = _toolset(root, tmp_path / "state")
    tool = _tool(tools, "run_tests")
    properties = tool.input_schema.get("properties", {})
    assert "argv" in properties, f"run_tests still has an empty schema: {tool.input_schema!r}"


# --------------------------------------------------------------------------- #
# the suite evidence's exit is machine-readable, not only in detail prose
# --------------------------------------------------------------------------- #

def test_verify_service_reads_the_real_exit_from_the_evidence(root_pytest) -> None:
    from skaldir.verify_service import _suite_result

    root, _tests = root_pytest
    from skaldir.verify_service import Criterion

    criterion = Criterion(text="the suite passes", criterion_id="c1", check_id="tests")
    status, exit_code, _detail, _cmd = _suite_result(
        criterion, workspace=root, timeout=600.0, revision=0, session="s1"
    )
    assert status == "passed"
    assert exit_code == 0, (
        f"the exit must come from the evidence, not only from detail: {exit_code!r}"
    )


@pytest.fixture()
def root_pytest(tmp_path: Path) -> tuple[Path, Path]:
    return pyproject_project(tmp_path)
