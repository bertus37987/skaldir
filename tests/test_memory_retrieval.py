"""Evidence retrieval across incident/note memory (incl. untrusted rendering)."""

import json

from skaldir.incidents import Incident, IncidentStore
from skaldir.llm import ScriptedLLM
from skaldir.notes import NoteStore, ResearchNote
from skaldir.prompts import PromptRegistry
from skaldir.retrieval import Evidence, find_evidence, judge_same_problem


class TestFindEvidence:
    def test_exact_signature_hit(self, tmp_path):
        incidents = IncidentStore(tmp_path / "memory.db")
        incidents.upsert(
            Incident(signature="fpX", title="Import-Fix", status="active", confidence=0.9)
        )
        evidence = find_evidence(incidents, None, signature="fpX")
        assert len(evidence) == 1
        assert evidence[0].ref == "incident#1"
        assert "Import-Fix" in evidence[0].text
        assert evidence[0].untrusted is False

    def test_text_query_hits_incidents_and_notes(self, tmp_path):
        incidents = IncidentStore(tmp_path / "memory.db")
        notes = NoteStore(tmp_path / "memory.db")
        incidents.upsert(Incident(signature="fp1", title="Timeout im Loader", status="active"))
        notes.add(ResearchNote(topic="Loader Timeout", claim="Erhöhe das Timeout-Limit", trust=0.7))
        evidence = find_evidence(incidents, notes, query="Timeout Loader", limit=5)
        refs = [item.ref for item in evidence]
        assert any(ref.startswith("incident#") for ref in refs)
        assert any(ref.startswith("note#") for ref in refs)

    def test_note_evidence_is_untrusted_and_spotlighted(self, tmp_path):
        notes = NoteStore(tmp_path / "memory.db")
        notes.add(ResearchNote(topic="Aus Web", claim="Behauptung aus dem Netz"))
        evidence = find_evidence(None, notes, query="Behauptung")
        assert evidence and evidence[0].untrusted is True
        assert evidence[0].render().startswith("<<<UNTRUSTED")

    def test_dedupe_across_exact_and_text(self, tmp_path):
        incidents = IncidentStore(tmp_path / "memory.db")
        incidents.upsert(Incident(signature="fp1", title="Loader Timeout", status="active"))
        evidence = find_evidence(
            incidents, None, signature="fp1", query="Loader Timeout", limit=3
        )
        assert [item.ref for item in evidence] == ["incident#1"]

    def test_empty_memory_returns_nothing(self, tmp_path):
        incidents = IncidentStore(tmp_path / "memory.db")
        assert find_evidence(incidents, None, signature="x", query="y") == []

    def test_archived_incident_not_surfaced(self, tmp_path):
        incidents = IncidentStore(tmp_path / "memory.db")
        incidents.upsert(Incident(signature="fpA", title="Altes Problem", status="archived"))
        assert find_evidence(incidents, None, signature="fpA") == []
        assert find_evidence(incidents, None, query="Altes Problem") == []


class TestJudgeSameProblem:
    def test_returns_confirmed_ref(self):
        candidates = [
            Evidence(ref="incident#1", text="Loader Timeout im DataLoader"),
            Evidence(ref="incident#2", text="CUDA OOM"),
        ]
        payload = {
            "same": True,
            "incident_id": "incident#1",
            "reason": "gleicher Loader",
            "confidence": 0.8,
        }
        client = ScriptedLLM(responses={"same_problem_judge": json.dumps(payload)})
        result = judge_same_problem(
            "Timeout beim DataLoader", candidates, client=client, registry=PromptRegistry()
        )
        assert result == "incident#1"

    def test_no_client_returns_none(self):
        candidates = [Evidence(ref="incident#1", text="x")]
        assert judge_same_problem("y", candidates) is None

    def test_unknown_ref_returns_none(self):
        candidates = [Evidence(ref="incident#1", text="x")]
        payload = {"same": True, "incident_id": "incident#999"}
        client = ScriptedLLM(responses={"same_problem_judge": json.dumps(payload)})
        result = judge_same_problem("y", candidates, client=client, registry=PromptRegistry())
        assert result is None

    def test_not_same_returns_none(self):
        candidates = [Evidence(ref="incident#1", text="x")]
        payload = {"same": False, "incident_id": "incident#1", "reason": "anders"}
        client = ScriptedLLM(responses={"same_problem_judge": json.dumps(payload)})
        result = judge_same_problem("y", candidates, client=client, registry=PromptRegistry())
        assert result is None
