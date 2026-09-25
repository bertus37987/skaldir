"""Wave 7: the §6 workflow contracts are startable, not just listable.

Measured at HEAD: only ``skaldir workflows list`` ever read ``workflow_contracts()``; the four
implementations existed but nothing in the product could start one. ``run_workflow`` is the
dispatch — the contract decides the required inputs, the existing function does the work, and the
function's own verdict is what the caller sees. ``unknown`` is never success.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skaldir import workflows
from skaldir.workflows import run_workflow, workflow_contracts


class TestTheDispatch:
    def test_an_unknown_workflow_is_blocked_and_names_the_known_ones(self, tmp_path):
        outcome = run_workflow("does_not_exist", runtime=tmp_path)
        assert outcome["status"] == "blocked"
        assert outcome["verified"] is False
        for name in ("open_app_snapshot", "verify_setting_persistence"):
            assert name in outcome["reason"]
        assert not (tmp_path / "workflow_runs").exists(), "a blocked start writes no evidence"

    def test_a_missing_required_input_is_blocked_with_the_contract_inputs(self, tmp_path):
        contract = next(c for c in workflow_contracts() if c["name"] == "open_app_snapshot")
        outcome = run_workflow("open_app_snapshot", runtime=tmp_path, params={})
        assert outcome["status"] == "blocked"
        assert "missing required input(s) ['app']" in outcome["reason"]
        assert str(contract["inputs"]) in outcome["reason"]

    def test_a_real_workflow_runs_and_reports_its_own_verdict(self, tmp_path):
        # the host has no display and no such app: the workflow's own verdict is a failure, and the
        # runner must carry it through unchanged — a substitute success is exactly what §6 forbids
        outcome = run_workflow(
            "open_app_snapshot", runtime=tmp_path, params={"app": "skaldir-nichtvorhanden"}
        )
        assert outcome["status"] != "verified"
        assert outcome["verified"] is False
        assert outcome["reason"], "a non-success carries a reason (failure_class/detail)"
        assert outcome["success_rule"] == "right window found + fresh screenshot"
        assert outcome["evidence"], "the run is persisted through the existing persist_run"
        record = json.loads(Path(outcome["evidence"]).read_text(encoding="utf-8"))
        assert record["status"] == outcome["status"]
        assert record["workflow"] == "open_app_snapshot"
        assert record["run_id"], "persist_run keys the record by run_id (measured)"


class TestTheVerdictMapping:
    """The runner must not upgrade or invent a verdict — measured per branch, with fakes."""

    @staticmethod
    def _with_runner(monkeypatch, value):
        monkeypatch.setattr(
            workflows, "workflow_runners", lambda: {"open_app_snapshot": lambda **kw: value}
        )

    def test_a_false_result_is_a_failure(self, monkeypatch, tmp_path):
        self._with_runner(monkeypatch, {"ok": False, "status": "failed"})
        outcome = run_workflow("open_app_snapshot", runtime=tmp_path, params={"app": "x"})
        assert outcome["status"] == "failed" and outcome["verified"] is False

    def test_an_empty_result_is_unknown_and_unknown_is_never_success(self, monkeypatch, tmp_path):
        self._with_runner(monkeypatch, {})
        outcome = run_workflow("open_app_snapshot", runtime=tmp_path, params={"app": "x"})
        assert outcome["status"] == "unknown"
        assert outcome["verified"] is False

    def test_a_true_result_is_verified(self, monkeypatch, tmp_path):
        self._with_runner(monkeypatch, {"ok": True, "status": "verified"})
        outcome = run_workflow("open_app_snapshot", runtime=tmp_path, params={"app": "x"})
        assert outcome["status"] == "verified" and outcome["verified"] is True

    def test_a_raising_workflow_is_reported_not_swallowed(self, monkeypatch, tmp_path):
        def boom(**kwargs):
            raise RuntimeError("kaputt")

        monkeypatch.setattr(workflows, "workflow_runners", lambda: {"open_app_snapshot": boom})
        outcome = run_workflow("open_app_snapshot", runtime=tmp_path, params={"app": "x"})
        assert outcome["status"] == "failed"
        assert "RuntimeError: kaputt" in outcome["reason"]
        assert outcome["result"]["failure_class"] == "RuntimeError"


def test_no_unregistered_event_kind_is_invented(tmp_path):
    """Measured: the store's vocabulary is closed, so a run reports through its own record.

    A `workflow_run` kind is refused by the store ("unknown event kind: workflow_run"); registering
    one is a product decision, not a wiring one — until then the record is the evidence.
    """

    from skaldir.events import EventStore

    events = EventStore(tmp_path / "events.db")
    try:
        outcome = run_workflow(
            "open_app_snapshot",
            runtime=tmp_path,
            params={"app": "skaldir-nichtvorhanden"},
        )
        kinds = [row.kind for row in events.iter_session("wf-test")]
    finally:
        events.close()
    assert outcome["evidence"]
    assert kinds == [], "no event is invented for a workflow run"


def test_the_contract_registry_still_lists_every_workflow():
    names = [item["name"] for item in workflow_contracts()]
    assert names == [
        "open_app_snapshot",
        "open_app_settings_snapshot",
        "verify_setting_persistence",
        "run_model_test",
    ]


@pytest.mark.parametrize("name", [c["name"] for c in workflow_contracts()])
def test_every_declared_workflow_has_a_runner(name):
    assert name in workflows.workflow_runners(), f"{name} is declared but not startable"


class TestTheAgentEntry:
    """The same surface the agent uses: `godsv_capabilities(action="workflow", …)`."""

    def test_the_entry_names_the_contract_inputs_when_one_is_missing(self):
        import godsv_plugin

        payload = json.loads(
            godsv_plugin.godsv_capabilities(
                {"action": "workflow", "workflow": "open_app_snapshot"}
            )
        )
        assert payload["status"] == "blocked"
        assert payload["verified"] is False
        assert "missing required input(s) ['app']" in payload["reason"]

    def test_the_entry_reports_an_unknown_workflow_honestly(self):
        import godsv_plugin

        payload = json.loads(
            godsv_plugin.godsv_capabilities({"action": "workflow", "workflow": "nope"})
        )
        assert payload["status"] == "blocked"
        assert "open_app_snapshot" in payload["reason"]

    def test_the_entry_runs_a_declared_workflow_and_carries_its_verdict(self):
        import godsv_plugin

        payload = json.loads(
            godsv_plugin.godsv_capabilities(
                {
                    "action": "workflow",
                    "workflow": "open_app_snapshot",
                    "params": {"app": "skaldir-nichtvorhanden"},
                }
            )
        )
        assert payload["status"] != "verified", payload
        assert payload["verified"] is False
        assert payload["reason"], "a non-success carries its failure_class/detail"
