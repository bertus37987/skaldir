"""P0-1 — trajectory state on the contract (ledger revival).

Decisions, assumptions and non-goals must live WITH the contract, outside
the LLM context, so the per-turn context block and the watcher can hold the
agent to them. Entries do not bump the revision (the contract terms are
unchanged) and a terminal contract refuses edits.
"""

import pytest

from skaldir.completion import CompletionStore, DodItem


def _store(tmp_path, sid="s1"):
    store = CompletionStore(tmp_path, session_id=sid)
    store.begin_goal(
        sid,
        "Ship it",
        [DodItem("c1", "check")],
        non_goals=["No redesign"],
        constraints=["Python 3.12"],
    )
    return store


class TestTrajectoryRecord:
    def test_begin_goal_records_non_goals_and_constraints(self, tmp_path):
        state = _store(tmp_path).state()
        assert [entry["text"] for entry in state["non_goals"]] == ["No redesign"]
        assert [entry["text"] for entry in state["constraints"]] == ["Python 3.12"]

    def test_three_kinds_keep_revision_and_accumulate(self, tmp_path):
        store = _store(tmp_path)
        store.record_trajectory("decision", "Use the existing store", reason="no second system")
        store.record_trajectory("assumption", "The suite is representative")
        store.record_trajectory("non_goal", "No UI redesign")
        state = store.state()
        assert state["revision"] == 1  # terms unchanged — no revision bump
        assert state["decisions"][0]["text"] == "Use the existing store"
        assert state["decisions"][0]["reason"] == "no second system"
        assert state["assumptions"][0]["text"] == "The suite is representative"
        assert [entry["text"] for entry in state["non_goals"]] == ["No redesign", "No UI redesign"]

    def test_entries_land_in_the_timeline(self, tmp_path):
        store = _store(tmp_path)
        store.record_trajectory("decision", "X")
        assert "trajectory" in [row["kind"] for row in store.timeline(limit=20)]

    def test_terminal_contract_refuses_edits(self, tmp_path):
        store = _store(tmp_path)
        store.transition("VERIFIED_COMPLETE")
        with pytest.raises(ValueError):
            store.record_trajectory("decision", "too late")

    def test_unknown_kind_and_empty_text_are_refused(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(ValueError):
            store.record_trajectory("banana", "x")
        with pytest.raises(ValueError):
            store.record_trajectory("decision", "   ")

    def test_api_records_and_exposes_trajectory(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        import importlib.util
        from pathlib import Path as _P

        panel_path = (
            _P(__file__).resolve().parent.parent
            / "integrations" / "hermes" / "godsv_plugin" / "dashboard" / "plugin_api.py"
        )
        spec = importlib.util.spec_from_file_location("godsv_panel_api_traj", panel_path)
        panel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(panel)
        store = CompletionStore(tmp_path / "godsv", session_id="t1")
        store.begin_goal("t1", "Traj goal", [])
        out = panel.completion_trajectory_payload(
            {"session_id": "t1", "kind": "decision", "text": "keep it", "reason": "compact"}
        )
        assert out["session"] == "t1"
        assert out["trajectory"]["decisions"][0]["text"] == "keep it"
        assert out["trajectory"]["decisions"][0]["reason"] == "compact"
