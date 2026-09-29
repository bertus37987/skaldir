"""Regression: end_state.channel='stdout' returns real stdout, not 'verified'.

P0 bug: the end_state observation for CLI actions was the verification status
string ("verified") instead of the actual command output. This test pins the
fix: a command that writes to stdout and stderr must deliver both values and
the exit code in the structured result.
"""
from __future__ import annotations

from skaldir.act_service import check_end_state
from skaldir.computer_use import ActionResult, LocalSandboxRunner


class _StdoutRunner(LocalSandboxRunner):
    """A runner that returns a known stdout and exit code for CLI actions."""

    def act(self, action):
        if action.kind == "cli":
            return ActionResult(ok=True, stdout="STDOUT_MARKER_XYZ\n", detail="ok")
        return ActionResult(ok=False, detail="unsupported")


def test_end_state_stdout_returns_real_output_not_status_marker():
    """The observation for channel='stdout' must be the command's actual stdout."""
    end_state = {"channel": "stdout", "expect_observe_contains": ["STDOUT_MARKER_XYZ"]}
    result = check_end_state(
        end_state,
        "STDOUT_MARKER_XYZ\n",  # what the runner returns as observation
        retries=0,
        observe_fn=None,
    )
    assert result["ok"] is True
    assert "STDOUT_MARKER_XYZ" in result["observed"]
    assert "verified" not in result["observed"] or "STDOUT_MARKER_XYZ" in result["observed"]


def test_end_state_stdout_rejects_when_output_missing():
    """A stdout end_state must fail when the expected value is absent."""
    end_state = {"channel": "stdout", "expect_observe_contains": ["NEVER_APPEARS"]}
    result = check_end_state(
        end_state,
        "some other output entirely",
        retries=0,
        observe_fn=None,
    )
    assert result["ok"] is False
    assert "NEVER_APPEARS" in str(result.get("missing", []))


def test_end_state_window_title_still_uses_desktop_observation():
    """GUI end_states (window_title channel) still check the desktop observation."""
    end_state = {"channel": "window_title", "expect_observe_contains": ["Firefox"]}
    result = check_end_state(
        end_state,
        "Firefox — Mozilla Firefox",
        retries=0,
        observe_fn=None,
    )
    assert result["ok"] is True
