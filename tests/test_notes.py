"""Research notes: add/search, expiry, contradictions, prompt curation."""

import json

from skaldir.llm import ScriptedLLM
from skaldir.notes import NoteStore, ResearchNote, curate_finding
from skaldir.prompts import PromptRegistry


class TestNoteStore:
    def test_add_and_get(self, tmp_path):
        store = NoteStore(tmp_path / "memory.db")
        note = store.add(
            ResearchNote(
                topic="PyTorch",
                claim="torch.compile braucht >=2.0",
                source_type="docs",
                source_url="https://pytorch.org",
                retrieved_at="2026-09-11",
                trust=0.9,
            ),
            ts=5.0,
        )
        assert note.id is not None and note.created == 5.0
        assert store.get(note.id).claim.startswith("torch.compile")
        assert store.count() == 1

    def test_search_text(self, tmp_path):
        store = NoteStore(tmp_path / "memory.db")
        store.add(ResearchNote(topic="PyTorch compile", claim="braucht Torch 2.0 oder neuer"))
        store.add(ResearchNote(topic="FastAPI", claim="uvicorn worker model"))
        hits = store.search_text("torch compile", limit=5)
        assert hits and hits[0][0].topic == "PyTorch compile"

    def test_stale_detection(self):
        note = ResearchNote(topic="x", claim="y", valid_until="2026-01-01")
        assert note.is_stale("2026-09-11") is True
        assert ResearchNote(topic="x", claim="y").is_stale("2026-09-11") is False
        future = ResearchNote(topic="x", claim="y", valid_until="2027-01-01")
        assert future.is_stale("2026-09-11") is False

    def test_link_contradiction_sets_status(self, tmp_path):
        store = NoteStore(tmp_path / "memory.db")
        first = store.add(ResearchNote(topic="a", claim="b"))
        second = store.add(ResearchNote(topic="a", claim="nicht b"))
        assert store.link_contradiction(second.id, f"note#{first.id}") is True
        loaded = store.get(second.id)
        assert loaded.status == "contradicted"
        assert f"note#{first.id}" in loaded.contradicts

    def test_evidence_text_includes_source(self, tmp_path):
        store = NoteStore(tmp_path / "memory.db")
        note = store.add(
            ResearchNote(topic="T", claim="C", source_type="docs", source_url="https://x")
        )
        text = note.to_evidence_text()
        assert "T: C" in text and "https://x" in text


class TestCurateFinding:
    def test_curates_scripted_finding(self):
        payload = {
            "topic": "PyTorch compile",
            "claim": "Ab Torch 2.0 verfügbar",
            "source_type": "docs",
            "source_url": "https://pytorch.org/docs",
            "retrieved_at": "2026-09-11",
            "valid_until": "null",
            "trust": 0.9,
            "repo_scope": "pytorch",
        }
        client = ScriptedLLM(responses={"research_note": json.dumps(payload)})
        note = curate_finding(
            client, PromptRegistry(), raw_finding="...", source_meta={"url": "u", "type": "docs"}
        )
        assert note.topic == "PyTorch compile"
        assert note.trust == 0.9
        assert note.valid_until is None  # "null" string normalized to None

    def test_trust_is_clamped(self):
        payload = {"topic": "t", "claim": "c", "trust": 5.0}
        client = ScriptedLLM(responses={"research_note": json.dumps(payload)})
        note = curate_finding(client, PromptRegistry(), raw_finding="x", source_meta={})
        assert note.trust == 1.0
