"""P1a — the pretending sweep: a command may not report success it did not earn.

Audited in MVP/02 §2.1: ``cu demo`` (an in-process fixture), ``eval`` (in-process
episodes), ``workflows list`` (contracts nothing could start) and ``completion begin``
(a goal record with no loop behind it). The rules below are what "not pretending" means
per command — each is an invariant, not a snapshot of today's wording.
"""

from __future__ import annotations

import json

import pytest

from skaldir import cli, cli_contract
from skaldir.workflows import workflow_runners


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("GODSV_HOME", str(home / "godsv"))
    monkeypatch.delenv("GODSV_FORMAT", raising=False)
    return home / "godsv"


def _run(capsys, argv: list[str]) -> tuple[int, dict]:
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


def test_completion_begin_reports_blocked_while_no_loop_exists(runtime, capsys):
    code, document = _run(
        capsys, ["completion", "begin", "--goal", "prove it", "--criterion", "tests pass", "--json"]
    )
    assert code == cli_contract.EXIT_BLOCKED
    assert document["error"]["code"] == "blocked"
    assert document["data"]["loop_wired"] is False
    assert document["data"]["goal"] == "prove it"
    assert document["data"]["state"] == "WORKING"
    assert document["next_actions"], "a blocked command must name the follow-up"
    assert "no execution loop" in document["error"]["message"]


def test_completion_begin_still_writes_the_goal_it_reports(runtime, capsys):
    _run(capsys, ["completion", "begin", "--goal", "recorded", "--criterion", "c1", "--json"])
    code, status = _run(capsys, ["completion", "status", "--json"])
    assert code == 0
    assert status["data"]["goal"] == "recorded"
    assert [item["text"] for item in status["data"]["criteria"]] == ["c1"]


def test_cu_demo_declares_itself_a_fixture(runtime, capsys):
    """Exit 0 stays (the legacy CLI contract), but the envelope may not imply a real action."""
    code, document = _run(capsys, ["cu", "demo", "--json"])
    assert code == 0
    data = document["data"]
    assert data["scope"] == "in-process fixture"
    assert data["live"] is False and data["desktop_touched"] is False
    assert document["warnings"], "a fixture must say so"
    assert any("not a desktop" in warning for warning in document["warnings"])
    assert document["next_actions"], "the honest follow-up must be offered"
    assert any("cu check" in item["command"] for item in document["next_actions"])


def test_eval_scopes_itself_as_in_process(runtime, capsys):
    code, document = _run(capsys, ["eval", "--json"])
    assert code == 0
    data = document["data"]
    assert data["scope"] == "in-process episodes"
    assert data["total"] == len(data["episodes"]) > 0
    assert data["passed"] >= 0
    assert any("not real-world verification" in warning for warning in document["warnings"])


def test_eval_failure_is_a_verification_failure_not_a_success(monkeypatch, runtime, capsys):
    from skaldir import cli as cli_module

    class _Result:
        def __init__(self, name, passed):
            self.name = name
            self.passed = passed
            self.signal_kinds = []
            self.hint_kinds = ["loop"]

        def render(self):
            return f"[{'PASS' if self.passed else 'FAIL'}] {self.name}"

    monkeypatch.setattr(cli_module, "run_eval", lambda: [_Result("ep", False)])
    code, document = _run(capsys, ["eval", "--json"])
    assert code == cli_contract.EXIT_VERIFICATION_FAILED
    assert document["error"]["code"] == "verification_failed"
    assert document["data"]["episodes"][0]["passed"] is False


def test_workflows_list_only_promises_what_can_be_started(runtime, capsys):
    code, document = _run(capsys, ["workflows", "list", "--json"])
    assert code == 0
    data = document["data"]
    assert data["count"] == len(data["workflows"]) > 0
    startable = {row["name"] for row in data["workflows"] if row["runnable"]}
    assert startable == set(data["runnable"])
    assert startable <= set(workflow_runners()), "a listed workflow must be startable"
    assert any("workflows run" in item["command"] for item in document["next_actions"])


def test_workflows_run_carries_the_workflows_own_verdict(runtime, capsys):
    """No substitute success: a real run on this host reports its own non-verified status."""
    code, document = _run(
        capsys, ["workflows", "run", "open_app_snapshot", "--param", "app=no-such-app", "--json"]
    )
    assert code == cli_contract.EXIT_VERIFICATION_FAILED
    assert document["data"]["verified"] is False
    assert document["data"]["reason"]
    assert document["error"]["code"] == "verification_failed"
    assert document["evidence"], "a run must point at its record"


def test_workflows_run_reports_a_missing_input_as_blocked(runtime, capsys):
    code, document = _run(capsys, ["workflows", "run", "open_app_snapshot", "--json"])
    assert code == cli_contract.EXIT_BLOCKED
    assert document["error"]["code"] == "blocked"
    assert "missing required input" in document["error"]["message"]
    assert document["next_actions"]


def test_workflows_run_rejects_a_malformed_param(runtime, capsys):
    code, document = _run(
        capsys, ["workflows", "run", "open_app_snapshot", "--param", "broken", "--json"]
    )
    assert code == cli_contract.EXIT_USAGE
    assert document["error"]["code"] == "usage"


def test_no_command_reports_success_without_evidence_in_the_dump(runtime):
    """A structural check on the whole surface, not on one command."""
    inventory = cli_contract.dump(cli.build_parser())
    offenders = [
        item["command"]
        for item in inventory["commands"]
        if 0 in item["exit_codes"] and not item["default_evidence"]
    ]
    assert offenders == []
