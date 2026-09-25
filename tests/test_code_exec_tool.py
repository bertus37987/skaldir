"""The typed ``run_code`` model adapter — CORE-06 (audit F02).

The audit measured that the Python runner works and is offered by no model toolset. These
tests cover the adapter itself: that it delegates, that it is context-bound, and that a
raised exception is returned as data rather than as a crash.

The acceptance asks for real error handling ("echte Fehlerbehandlung"), so the failure
shape is asserted field by field — not just a boolean.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skaldir.code_exec_tool import CODE_TOOL_NAME, CODE_INPUT_SCHEMA, run_code_tool


@pytest.fixture()
def tool(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return run_code_tool(workspace=ws, runtime=tmp_path / "runtime", run_id="run-1")


# --------------------------------------------------------------------------- #
# it runs, and it reports the value
# --------------------------------------------------------------------------- #

def test_a_calculation_returns_the_value_and_the_output(tool):
    result = tool.handler(source="print('code-ok'); result = sum([2,3,5])")
    assert result["ok"] is True, result
    assert result["value"] == 10
    assert result["stdout"] == "code-ok\n"
    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert result["duration_ms"] >= 0


def test_the_tool_is_named_and_declares_itself_mutating(tool):
    """It writes to the workspace, so it must not run in parallel with siblings."""
    assert tool.name == CODE_TOOL_NAME
    assert tool.mutating is True
    assert tool.parallel_safe is False
    assert tool.source_name == "skaldir.code_exec", "the adapter must not hide that it delegates"


# --------------------------------------------------------------------------- #
# real error handling
# --------------------------------------------------------------------------- #

def test_a_raised_exception_is_data_with_a_name_and_a_traceback(tool):
    """A crash is not a failure of the tool — it is what the code did."""
    result = tool.handler(source='raise ValueError("kaputt")')
    assert result["ok"] is False
    assert result["error"] == "ValueError"
    assert result["error_message"] == "kaputt"
    assert result["traceback"], "an exception without a traceback is not diagnosable"
    assert "ValueError" in result["traceback"]
    assert result["status"] == "error"
    assert result["exit_code"] == 1


def test_a_syntax_error_is_reported_as_a_named_failure(tool):
    """A compile failure never reaches the result frame — the runner says so plainly.

    Measured contract, not assumed: a syntax error is ``status=no_result`` with
    ``exit_code=1``, and the reason names what is missing. It must not read as success, and
    it must not be confused with a raised exception.
    """
    result = tool.handler(source="def broken(:")
    assert result["ok"] is False
    assert result["status"] == "no_result"
    assert result["exit_code"] == 1
    assert "without reporting a result" in result["reason"]
    assert result.get("error") != "ValueError"


def test_a_raised_exception_and_a_dead_process_are_different_results(tool):
    """Three different endings must stay three different endings."""
    raised = tool.handler(source='raise ValueError("x")')
    exited = tool.handler(source="import os; os._exit(7)")
    assert raised["status"] == "error" and raised["error"] == "ValueError"
    assert exited["status"] == "no_result"
    assert exited["exit_code"] == 7, "a killed process keeps its real exit code"
    assert raised["status"] != exited["status"]


def test_a_timeout_is_distinguishable_from_a_crash(tool):
    """The acceptance requires timeout ≠ failure ≠ 'no tests' to be different results."""
    result = tool.handler(source="import time; time.sleep(9)", timeout_s=1)
    assert result["ok"] is False
    assert result["status"] == "timeout"
    assert result.get("timed_out_after_s"), "a timeout must say how long it ran"
    assert result["error"] != "ValueError"


def test_a_non_zero_exit_never_reads_as_success(tool):
    """The verification path must read the exit, not the transport's ok.

    ``sys.exit`` inside the snippet is caught by the runner and reported as an exception
    named ``SystemExit`` with the code as its message; the runner's own process exit stays
    1. What must never happen is ``ok=true`` for code that did not complete.
    """
    result = tool.handler(source="import sys; sys.exit(7); result = 1")
    assert result["ok"] is False
    assert result["error"] == "SystemExit"
    assert result["error_message"] == "7"
    assert result["status"] == "error"


# --------------------------------------------------------------------------- #
# context binding (this is the F03 direction at the tool layer)
# --------------------------------------------------------------------------- #

def test_the_tool_is_bound_to_its_own_workspace(tmp_path):
    a_ws, b_ws = tmp_path / "a", tmp_path / "b"
    a_ws.mkdir(); b_ws.mkdir()
    (a_ws / "data.txt").write_text("IN-A", encoding="utf-8")
    (b_ws / "data.txt").write_text("IN-B", encoding="utf-8")

    a = run_code_tool(workspace=a_ws, runtime=tmp_path / "ra", run_id="run-a")
    b = run_code_tool(workspace=b_ws, runtime=tmp_path / "rb", run_id="run-b")

    ra = a.handler(source="result = open('data.txt').read()")
    rb = b.handler(source="result = open('data.txt').read()")
    assert ra["value"] == "IN-A", f"run-a read the wrong workspace: {ra['value']!r}"
    assert rb["value"] == "IN-B", f"run-b read the wrong workspace: {rb['value']!r}"
    assert ra["workspace"] == str(a_ws)
    assert rb["workspace"] == str(b_ws)
    assert ra["run_id"] == "run-a" and rb["run_id"] == "run-b"


def test_the_result_carries_the_workspace_digest_as_evidence(tool):
    """Proof that the run happened in the workspace it claims."""
    result = tool.handler(source="result = 1")
    assert result["workspace_digest_before"]
    assert result["workspace_digest_after"]


# --------------------------------------------------------------------------- #
# honesty and shape
# --------------------------------------------------------------------------- #

def test_the_tool_does_not_pretend_to_sandbox(tool):
    result = tool.handler(source="result = 1")
    assert "NOT an OS sandbox" in result["isolation"]
    assert "not automatically" in result["isolation"], (
        "the result must say effects outside the tool API are not individually approved"
    )


def test_an_unsupported_language_is_refused_by_name(tool):
    """Only Python here; other runtimes go through the terminal tool."""
    result = tool.handler(source="x = 1", language="node")
    assert result["ok"] is False
    assert result["error"] == "unsupported_language"
    assert "terminal tool" in result["reason"]


def test_empty_source_is_a_usage_error(tool):
    result = tool.handler(source="   ")
    assert result["ok"] is False
    assert result["error"] == "usage"


def test_the_schema_asks_for_source_and_explains_the_budgets(tool):
    props = CODE_INPUT_SCHEMA["properties"]
    assert CODE_INPUT_SCHEMA["required"] == ["source"]
    assert props["timeout_s"]["maximum"] == 120, "the runner caps at 120s — the schema must agree"
    assert "other runtimes go through the terminal tool" in props["language"]["description"]
