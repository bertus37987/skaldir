"""Incident memory: signature dedupe, FTS search, similarity, thread safety."""

import threading

from skaldir.embedding import HashingEmbeddings
from skaldir.incidents import Incident, IncidentStore


def _incident(sig="fp1", **kwargs):
    defaults = dict(
        signature=sig,
        title="Import-Fehler cv_split",
        root_cause="Modul umbenannt",
        resolution_steps=["Import auf split_cv umgestellt"],
        verification="pytest grün",
        status="active",
        confidence=0.8,
    )
    defaults.update(kwargs)
    return Incident(**defaults)


class TestUpsert:
    def test_insert_and_get(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        stored = store.upsert(_incident(), ts=100.0)
        assert stored.id is not None
        assert stored.first_seen == 100.0
        assert store.get("fp1").title == "Import-Fehler cv_split"
        assert store.count() == 1

    def test_repeat_sighting_bumps_occurrences(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_incident(), ts=100.0)
        again = store.upsert(_incident(title="Import-Fehler cv_split v2"), ts=200.0)
        assert again.occurrences == 2
        assert again.last_seen == 200.0
        assert again.title == "Import-Fehler cv_split v2"
        assert store.count() == 1

    def test_record_sighting_unknown_returns_false(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        assert store.record_sighting("nope") is False

    def test_promote(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_incident(status="candidate"))
        assert store.promote("fp1") is True
        assert store.get("fp1").status == "active"
        assert store.promote("nope") is False

    def test_evidence_text_includes_resolution(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        stored = store.upsert(_incident())
        text = stored.to_evidence_text()
        assert "split_cv" in text and "pytest grün" in text


class TestSearch:
    def test_text_search_finds_by_title(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_incident())
        store.upsert(
            _incident(sig="fp2", title="Timeout im Loader", root_cause="", resolution_steps=[])
        )
        hits = store.search_text("Import cv_split", limit=5)
        assert hits and hits[0][0].signature == "fp1"

    def test_special_characters_do_not_crash(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_incident())
        assert store.search_text('"quoted" (weird) *', limit=5) == []

    def test_archived_not_in_text_search(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(_incident(sig="fp1", status="archived"))
        assert store.search_text("Import cv_split", limit=5) == []

    def test_similarity_search_orders_by_cosine(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        provider = HashingEmbeddings()
        store.upsert(
            _incident(
                sig="fp1", embedding=provider.embed("ImportError cannot import name cv_split")
            )
        )
        store.upsert(
            _incident(
                sig="fp2",
                title="Netzwerk-Problem",
                embedding=provider.embed("Netzwerk Timeout beim Upload"),
            )
        )
        hits = store.search_similar(provider.embed("cannot import name cv_split"), limit=2)
        assert hits and hits[0][0].signature == "fp1"
        assert hits[0][1] > 0.3


class TestThreadSafety:
    def test_upsert_from_worker_thread(self, tmp_path):
        store = IncidentStore(tmp_path / "memory.db")
        errors: list = []

        def worker():
            try:
                store.upsert(_incident(sig="thread-fp"))
            except Exception as exc:  # pragma: no cover - regression guard
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        assert errors == []
        assert store.get("thread-fp") is not None
