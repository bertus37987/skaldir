"""Night-shift consolidation: episode summary + incident proposals + CLI."""

import json

from skaldir.cli import main
from skaldir.demo import SCRIPT
from skaldir.events import EventStore
from skaldir.incidents import Incident, IncidentStore
from skaldir.llm import ScriptedLLM
from skaldir.nightshift import build_episode_summary, consolidate
from skaldir.prompts import PromptRegistry


def _make_session(tmp_path, session="demo-1"):
    store = EventStore(tmp_path / "events.db")
    for step, kind, payload in SCRIPT:
        store.append(session, step, kind, payload, ts=1000.0 + step)
    store.close()
    return tmp_path / "events.db"


class TestEpisodeSummary:
    def test_builds_from_demo_script(self, tmp_path):
        store = EventStore(_make_session(tmp_path))
        summary = build_episode_summary(store, "demo-1")
        store.close()
        assert summary.events == 8
        assert summary.kinds["tool_call"] == 3
        assert summary.top_error is not None
        assert "cv_split" in summary.top_error["error"]
        assert summary.top_error["count"] == 2
        assert "lib/utils.py" in summary.files_touched
        assert "cv_split" in summary.to_text()

    def test_missing_session_is_empty(self, tmp_path):
        store = EventStore(_make_session(tmp_path))
        summary = build_episode_summary(store, "nope")
        store.close()
        assert summary.events == 0
        assert summary.top_error is None


class TestConsolidate:
    def test_dry_run_makes_skeleton_proposal(self, tmp_path):
        store = EventStore(_make_session(tmp_path))
        summary = build_episode_summary(store, "demo-1")
        store.close()
        result = consolidate(summary)
        assert result.applied is False
        assert len(result.proposals) == 1
        proposal = result.proposals[0]
        assert "cv_split" in proposal.title
        assert proposal.status == "candidate"

    def test_apply_with_llm_fills_and_stores(self, tmp_path):
        store = EventStore(_make_session(tmp_path))
        summary = build_episode_summary(store, "demo-1")
        store.close()
        incidents = IncidentStore(tmp_path / "memory.db")
        payload = {
            "title": "cv_split Import korrigiert",
            "kind": "error",
            "root_cause": "Funktion umbenannt",
            "resolution_steps": ["Import umgestellt"],
            "verification": "pytest grün",
            "confidence": 0.7,
        }
        client = ScriptedLLM(responses={"incident_extract": json.dumps(payload)})
        result = consolidate(
            summary, client=client, registry=PromptRegistry(), incidents=incidents, apply=True
        )
        assert result.applied is True
        stored = incidents.get(result.proposals[0].signature)
        assert stored is not None
        assert stored.title == "cv_split Import korrigiert"
        assert stored.root_cause == "Funktion umbenannt"
        assert stored.status == "candidate"  # propose-first
        assert stored.confidence == 0.7

    def test_existing_incident_not_reproposed(self, tmp_path):
        store = EventStore(_make_session(tmp_path))
        summary = build_episode_summary(store, "demo-1")
        store.close()
        incidents = IncidentStore(tmp_path / "memory.db")
        incidents.upsert(Incident(signature=summary.top_error["fingerprint"], title="schon da"))
        result = consolidate(summary, incidents=incidents)
        assert result.proposals == []


class TestCli:
    def test_nightshift_dry_run(self, tmp_path, capsys):
        events = _make_session(tmp_path, session="cli-1")
        code = main(
            [
                "nightshift",
                "--events",
                str(events),
                "--memory",
                str(tmp_path / "mem.db"),
                "--session",
                "cli-1",
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "propose incident" in out
        assert "applied: False" in out

    def test_memory_stats_and_search(self, tmp_path, capsys):
        incidents = IncidentStore(tmp_path / "mem.db")
        incidents.upsert(Incident(signature="fp1", title="Loader Timeout", status="active"))
        incidents.close()
        assert main(["memory-stats", "--db", str(tmp_path / "mem.db")]) == 0
        assert "incident:active: 1" in capsys.readouterr().out
        assert main(["memory-search", "--db", str(tmp_path / "mem.db"), "Timeout"]) == 0
        assert "incident#1" in capsys.readouterr().out
