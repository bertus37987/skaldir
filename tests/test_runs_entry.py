"""Wave 4: the run supervisor is reachable from the product path.

Measured at HEAD: `run_supervisor.py` (RunStore, check/repair, sweep, recover_pending, describe,
RunWatchdog) had **no production caller** — only `subagent_briefs` was consumed *by* it, not the
other way round. `godsv_capabilities(action="runs", …)` is the caller now.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    home = tmp_path / "hermes-home"
    (home / "godsv").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    yield


def _entry(**params):
    import godsv_plugin

    return json.loads(godsv_plugin.godsv_capabilities({"action": "runs", **params}))


class TestTheRunsEntry:
    def test_status_is_read_only_and_reports_the_store_invariant(self):
        payload = _entry()
        assert payload["invariant"]
        assert payload["runs"] == []
        assert payload["checked"] == 0
        assert payload["violations"] == []
        assert payload["ok"] is True

    def test_sweep_runs_the_watchdog_pass_on_an_empty_store(self):
        # measured receipt shape: the pass ran, nothing needed repair, nothing was claimed
        payload = _entry(runs_action="sweep")
        assert payload["ok"] is True
        assert payload["checked"] == 0
        assert payload["violations_before"] == 0
        assert payload["violations_after"] == 0
        assert payload["repairs"] == []
        assert payload["claimed"] == []
        assert payload["owner"] == "skaldir-panel"
        assert payload["invariant"]

    def test_recover_claims_continuations_exactly_once(self):
        payload = _entry(runs_action="recover")
        assert payload["claimed"] == []
        assert payload["owner"] == "skaldir-panel"

    def test_an_unknown_action_is_blocked_honestly(self):
        payload = _entry(runs_action="teleport")
        assert payload["status"] == "blocked"
        assert "known: status, sweep, recover" in payload["reason"]

    def test_the_entry_never_writes_to_the_live_runtime(self):
        first = _entry()
        second = _entry()
        assert first["runs"] == second["runs"] == [], "status must not create runs"
