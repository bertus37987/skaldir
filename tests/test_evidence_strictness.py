"""Regression tests for the P1 evidence-strictness fixes (independent review 2026-09-12).

Every test here failed on the pre-fix baseline (`hardening-baseline`) and guards one of the
reproduced findings:
- `echo pytest` (exit 0) must never certify a requirement it never tested;
- tool results must pair with their own call — a later `pwd` never erases a failed `pytest`;
- a tool result with an `error` and no exit code is `failed`, never an implicit pass;
- untracked new modules are part of the task diff and cannot slip past the test gate.
"""

import subprocess
from types import SimpleNamespace

import pytest

from skaldir.completion import (
    CompletionRequest,
    DodItem,
    GateRunner,
    RunEvidence,
    execute_check,
    run_evidence_from_events,
)

REQUEST = CompletionRequest(claimed_completed=["the requested feature works"])


def event(step, kind, payload):
    return SimpleNamespace(step=step, kind=kind, payload=payload)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
        )

    git("init", "-q")
    (root / "README.md").write_text("fixture\n")
    git("add", ".")
    git("-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "fixture")
    return root


class TestEvidenceStrictness:
    def test_echo_pytest_never_certifies_a_missing_feature(self, repo):
        events = [
            event(1, "tool_call", {"name": "terminal", "args": {"command": "echo pytest"}}),
            event(2, "tool_result", {"tool": "terminal", "exit_code": 0, "stdout": "pytest"}),
        ]
        evidence = run_evidence_from_events(events)
        assert evidence and evidence[0].source == "events"
        assert evidence[0].status == "passed"  # the command did succeed — as a command
        verdict = GateRunner(
            repo_root=repo,
            dod=[DodItem("login", "Login actually authenticates", verification="pytest")],
        ).verify(REQUEST, evidence)
        assert verdict.status != "VERIFIED_COMPLETE"
        assert any("runner-executed" in failure for failure in verdict.blocking_failures)

    def test_failed_pytest_stays_blocking_despite_later_pwd(self, repo):
        events = [
            event(1, "tool_call", {"name": "terminal", "args": {"command": "pytest -q"}}),
            event(2, "tool_result", {"tool": "terminal", "exit_code": 1}),
            event(3, "tool_call", {"name": "terminal", "args": {"command": "pwd"}}),
            event(4, "tool_result", {"tool": "terminal", "exit_code": 0}),
        ]
        evidence = run_evidence_from_events(events)
        assert [item.status for item in evidence] == ["failed", "passed"]
        verdict = GateRunner(repo_root=repo).verify(REQUEST, evidence)
        assert verdict.status != "VERIFIED_COMPLETE"
        assert any("test run failed" in failure for failure in verdict.blocking_failures)

    def test_error_without_exit_code_is_not_a_pass(self, repo):
        evidence = run_evidence_from_events(
            [
                event(1, "tool_call", {"name": "terminal", "args": {"command": "pytest -q"}}),
                event(2, "tool_result", {"tool": "terminal", "error": "process never started"}),
            ]
        )
        assert evidence and evidence[0].status == "failed"
        assert evidence[0].ok is False

    def test_non_command_results_are_not_evidence(self, repo):
        # a write_file result is not an executed check — no evidence may exist
        events = [
            event(1, "tool_call", {"name": "write_file", "args": {"path": "a.py", "content": "x"}}),
            event(2, "tool_result", {"tool": "write_file", "status": "success", "result": "ok"}),
        ]
        assert run_evidence_from_events(events) == []

    def test_parallel_same_name_calls_pair_by_tool_call_id(self, repo):
        events = [
            event(
                1,
                "tool_call",
                {"name": "terminal", "tool_call_id": "c1", "args": {"command": "pytest -q"}},
            ),
            event(
                1,
                "tool_call",
                {"name": "terminal", "tool_call_id": "c2", "args": {"command": "pwd"}},
            ),
            event(2, "tool_result", {"tool": "terminal", "tool_call_id": "c2", "exit_code": 0}),
            event(2, "tool_result", {"tool": "terminal", "tool_call_id": "c1", "exit_code": 1}),
        ]
        evidence = run_evidence_from_events(events)
        by_id = {item.tool_call_id: item for item in evidence}
        assert set(by_id) == {"c1", "c2"}
        assert by_id["c1"].status == "failed" and "pytest" in by_id["c1"].cmd
        assert by_id["c2"].status == "passed" and "pwd" in by_id["c2"].cmd

    def test_untracked_new_module_is_test_gated(self, repo):
        (repo / "src").mkdir()
        (repo / "src" / "broken.py").write_text("raise RuntimeError('not implemented')\n")
        verdict = GateRunner(repo_root=repo).verify(
            REQUEST, [RunEvidence(name="run", ok=True, cmd="pwd")]
        )
        assert verdict.status != "VERIFIED_COMPLETE"
        assert any("no test run is evidenced" in failure for failure in verdict.blocking_failures)

    def test_newer_green_run_supersedes_older_failure(self, repo):
        events = [
            event(1, "tool_call", {"name": "terminal", "args": {"command": "pytest -q"}}),
            event(2, "tool_result", {"tool": "terminal", "exit_code": 1}),
            event(3, "tool_call", {"name": "terminal", "args": {"command": "pytest -q"}}),
            event(4, "tool_result", {"tool": "terminal", "exit_code": 0}),
        ]
        verdict = GateRunner(repo_root=repo).verify(REQUEST, run_evidence_from_events(events))
        assert not any("test run failed" in failure for failure in verdict.blocking_failures)


class TestRunnerChecks:
    def test_runner_executes_real_pytest_and_passes(self, tmp_path):
        (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n")
        result = execute_check("pytest", tmp_path, timeout=120)
        assert result is not None
        assert result.source == "runner" and result.status == "passed"
        assert "pytest" in result.cmd

    def test_runner_does_not_certify_an_empty_repo(self, tmp_path):
        # pytest exits 5 ("no tests ran") — an empty run is not a green run
        result = execute_check("pytest", tmp_path, timeout=120)
        assert result is not None and result.status == "failed"

    def test_unknown_check_has_no_runner(self, tmp_path):
        assert execute_check("ui_e2e_that_does_not_exist", tmp_path) is None

def test_empty_contract_without_any_check_is_not_verified(repo):
    """P1: no blocking criteria and no executed check = nothing verified.

    Regression for the hollow-green case: the panel used to mint
    VERIFIED_COMPLETE from an empty goal with zero evidence.
    """
    from skaldir.completion import INCOMPLETE, CompletionRequest

    runner = GateRunner(repo_root=repo, dod=[])
    result = runner.verify(
        CompletionRequest(
            user_goal="",
            claimed_completed=["everything works"],
            attempt_id="empty-contract",
        ),
        [],
    )
    assert result.status == INCOMPLETE
    assert any("nothing was verified" in b for b in result.blocking_failures)


def test_empty_contract_with_green_evidence_stays_verified(repo):
    """The same empty contract WITH a passed runner check keeps verifying
    evidence-backed claims — the rule must not create a false negative."""
    from skaldir.completion import VERIFIED_COMPLETE, CompletionRequest, RunEvidence

    green = RunEvidence(
        name="pytest",
        check_id="pytest",
        source="runner",
        status="passed",
        ok=True,
        cmd="python -m pytest -q",
        detail="1 passed",
        ts=0.0,
    )
    result = GateRunner(repo_root=repo, dod=[]).verify(
        CompletionRequest(
            user_goal="", claimed_completed=["the suite passes"], attempt_id="x"
        ),
        [green],
    )
    assert result.status == VERIFIED_COMPLETE


def test_dod_registered_tool_certified_only_by_runner(tmp_path):
    """A criterion wired to a registered tool is certified by runner evidence only."""
    gate = GateRunner(
        repo_root=tmp_path,
        dod=[DodItem(id="c1", text="check_readme passes", verification="check_readme")],
    )
    request = CompletionRequest(user_goal="goal")
    # runner evidence with the tool's check id certifies the item
    findings = gate.gate_dod(
        request,
        [
            RunEvidence(
                name="check_readme", cmd="check_readme repo", status="passed", source="runner"
            )
        ],
    )
    assert findings == []
    # the same result derived from tool events must NOT certify (P1 strictness)
    findings2 = gate.gate_dod(
        request,
        [RunEvidence(name="check_readme", status="passed", source="events")],
    )
    assert any("runner-executed" in f.detail for f in findings2)
