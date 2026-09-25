"""Recurrence-kill-rate metrics (corpus-level, deterministic)."""

from skaldir.events import EventStore
from skaldir.incidents import Incident, IncidentStore
from skaldir.metrics import compute_metrics
from skaldir.skills import STATUS_PROMOTED, SkillCandidate, SkillStore


def _seed_events(tmp_path) -> EventStore:
    store = EventStore(tmp_path / "events.db")
    store.append("s1", 1, "tool_result", {"error": "ImportError: x"}, ts=1.0)
    store.append("s2", 1, "tool_result", {"error": "ImportError: x"}, ts=2.0)
    store.append("s2", 2, "tool_result", {"error": "Timeout: z"}, ts=3.0)
    store.append("s2", 3, "hint_delivered", {"hints": ["a"]}, ts=4.0)
    return store


class TestMetrics:
    def test_counts_and_kill_rate(self, tmp_path):
        events = _seed_events(tmp_path)
        incidents = IncidentStore(tmp_path / "memory.db")
        incidents.upsert(Incident(signature="fp1", title="A"))
        incidents.upsert(Incident(signature="fp2", title="B"))
        skills = SkillStore(tmp_path / "memory.db")
        skills.upsert(SkillCandidate(name="w", title="W"))
        skills.set_status("w", STATUS_PROMOTED)

        metrics = compute_metrics(events, incidents, skills)
        assert metrics.sessions == 2
        assert metrics.events == 4
        assert metrics.problem_classes == 2
        assert metrics.recurring_classes == 1
        assert metrics.recurrence_kill_rate == 0.5
        assert metrics.hints_delivered == 1
        assert metrics.incidents == 2
        assert metrics.skills_promoted == 1
        events.close()

    def test_empty_corpus_kill_rate_is_one(self, tmp_path):
        events = EventStore(tmp_path / "events.db")
        metrics = compute_metrics(events)
        assert metrics.problem_classes == 0
        assert metrics.recurrence_kill_rate == 1.0
        events.close()

    def test_json_roundtrip(self, tmp_path):
        import json

        events = _seed_events(tmp_path)
        payload = compute_metrics(events).to_json()
        parsed = json.loads(payload)
        assert parsed["recurrence_kill_rate"] == 0.5
        events.close()


class TestCli:
    def test_metrics_command(self, tmp_path, capsys):
        from skaldir.cli import main

        events = tmp_path / "events.db"
        store = _seed_events(tmp_path)
        store.close()
        code = main(
            ["metrics", "--events", str(events), "--memory", str(tmp_path / "memory.db")]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "recurrence_kill_rate: 0.5" in out
        assert "hints_delivered: 1" in out
