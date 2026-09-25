"""Block 1 / Defekt 4 — Contract-Store schreibt atomar (kein stiller WORKING).

Rot-first-Beleg (echter Lauf gegen den Vor-Zustand, NICHT per ``git stash``)::

    $ .venv/bin/python -m pytest tests/test_contract_store_atomic.py -q
    FFFF  (4 failed)
    - ``transition`` schrieb direkt per ``Path.write_text``: ein Abbruch mitten
      im Write hinterließ eine abgeschnittene Datei; ``state()`` las sie als
      leeren WORKING-Zustand (Goal/Revision still verloren).
    - eine korrupte Datei wurde still zu ``{"state": "WORKING", "goal": ""}``
      und die Originalbytes waren weg.
    - parallele Writer verwarfen sich gegenseitig (kein Lock).

Vertrag nach dem Fix:

* Schreiben geht über Temp-Datei + ``os.replace``: nach einem Abbruch ist die
  vorige Datei unverändert gültig (oder die neue vollständig) — nie halb.
* Korruption ist **sichtbar** (WARNING + ``corrupt``-Flag + Quarantäne-Datei)
  und die Originalbytes bleiben erhalten.
* Writer sind serialisiert; kein Update geht verloren.
"""

import json
import os
import shutil
import threading
from pathlib import Path

import pytest

from skaldir.completion import WORKING, CompletionStore, DodItem


def _begin(tmp: Path, sid: str = "s1", goal: str = "Ship the feature") -> CompletionStore:
    store = CompletionStore(tmp, session_id=sid)
    store.begin_goal(sid, goal, [DodItem("c1", goal, verification="pytest")])
    return store


class TestInterruptedWriteKeepsTheOldContract:
    def test_failed_replace_leaves_the_previous_state_intact(self, tmp_path, monkeypatch):
        """Abbruch an der kritischsten Stelle: os.replace schlägt fehl."""
        store = _begin(tmp_path, "s1", "Goal eins")
        before = store.state_path.read_text(encoding="utf-8")
        assert "Goal eins" in before

        real_replace = os.replace

        def _boom(src, dst):
            raise OSError("simulated crash during replace")

        monkeypatch.setattr(os, "replace", _boom)
        with pytest.raises(OSError):
            store.transition(WORKING, goal="Goal zwei", revision=2)
        monkeypatch.setattr(os, "replace", real_replace)

        # Die alte Datei ist vollständig und gültig — kein leerer WORKING-Zustand.
        after = store.state_path.read_text(encoding="utf-8")
        assert after == before
        record = store.state()
        assert record["goal"] == "Goal eins"
        assert record["revision"] == 1
        assert record.get("corrupt") is not True

    def test_no_temp_leftovers_after_a_failed_write(self, tmp_path, monkeypatch):
        store = _begin(tmp_path, "s1")

        def _boom(_src, _dst):
            raise OSError("simulated crash during replace")

        monkeypatch.setattr(os, "replace", _boom)
        with pytest.raises(OSError):
            store.transition(WORKING, note="x")
        leftovers = [
            p.name
            for p in store.state_path.parent.iterdir()
            if p.suffix == ".tmp" or p.name.startswith(".")
        ]
        assert leftovers == [], leftovers

    def test_a_successful_write_replaces_atomically(self, tmp_path, monkeypatch):
        """Der echte Pfad nutzt os.replace (Temp-Datei → Ziel)."""
        store = _begin(tmp_path, "s1")
        calls: list[tuple[str, str]] = []
        real_replace = os.replace

        def _record(src, dst):
            calls.append((str(src), str(dst)))
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", _record)
        store.transition(WORKING, goal="Goal zwei", revision=2)
        assert calls, "state write must go through os.replace"
        assert calls[-1][1] == str(store.state_path)
        assert store.state()["goal"] == "Goal zwei"


class TestCorruptionIsVisibleAndPreserved:
    def test_corrupt_state_is_flagged_not_silently_empty(self, tmp_path):
        store = _begin(tmp_path, "s1", "Wichtiges Ziel")
        store.state_path.write_text('{"state": "WORKING", "goal": "Wicht', encoding="utf-8")

        record = store.state()
        # Weiterhin ein benutzbarer Grundzustand …
        assert record["state"] == WORKING
        # … aber NICHT stillschweigend als saubere neue Session.
        assert record.get("corrupt") is True, record
        assert record.get("corrupt_reason"), record

    def test_corrupt_bytes_are_preserved_in_a_quarantine_file(self, tmp_path):
        store = _begin(tmp_path, "s1", "Wichtiges Ziel")
        damaged = '{"state": "WORKING", "goal": "Wicht'
        store.state_path.write_text(damaged, encoding="utf-8")

        store.state()
        pattern = f"{store.state_path.stem}*.corrupt*"
        quarantined = list(store.state_path.parent.glob(pattern))
        assert quarantined, "corrupt contract must be quarantined, not overwritten"
        assert damaged in "".join(p.read_text(encoding="utf-8") for p in quarantined)

    def test_corruption_is_logged(self, tmp_path, caplog):
        store = _begin(tmp_path, "s1")
        store.state_path.write_text("{not json", encoding="utf-8")
        with caplog.at_level("WARNING", logger="godsv.completion"):
            store.state()
        assert any(
            "corrupt" in rec.getMessage().lower() or "unreadable" in rec.getMessage().lower()
            for rec in caplog.records
        ), caplog.text

    def test_valid_state_is_never_flagged(self, tmp_path):
        store = _begin(tmp_path, "s1", "Sauberes Ziel")
        record = store.state()
        assert record.get("corrupt") is not True
        assert record["goal"] == "Sauberes Ziel"


class TestWritersAreSerialized:
    def test_parallel_writers_never_lose_a_contract(self, tmp_path):
        store = _begin(tmp_path, "s1", "Ziel")
        errors: list = []

        def worker(n: int):
            try:
                CompletionStore(tmp_path, session_id="s1").add_criteria([f"crit-{n}"])
            except Exception as exc:  # pragma: no cover - regression guard
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == [], errors
        record = store.state()  # must parse — a torn file would raise/mark corrupt
        assert record.get("corrupt") is not True
        texts = {str(item["text"]) for item in record["dod"]}
        assert {f"crit-{n}" for n in range(12)} <= texts, sorted(texts)

    def test_parallel_transitions_leave_one_valid_record(self, tmp_path):
        _begin(tmp_path, "s1", "Ziel")
        store = CompletionStore(tmp_path, session_id="s1")

        def worker(n: int):
            CompletionStore(tmp_path, session_id="s1").record_trajectory(
                "decision", f"dec-{n}"
            )

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        record = json.loads(store.state_path.read_text(encoding="utf-8"))
        assert record["goal"] == "Ziel"
        assert len({d["text"] for d in record["decisions"]}) == 8


class TestAtomicWriterUnit:
    def test_atomic_writer_leaves_previous_bytes_on_failure(self, tmp_path, monkeypatch):
        from skaldir import completion

        target = tmp_path / "x.json"
        target.write_text("old", encoding="utf-8")
        monkeypatch.setattr(
            os, "replace", lambda *a: (_ for _ in ()).throw(OSError("boom"))
        )
        with pytest.raises(OSError):
            completion._atomic_write_text(target, "new")
        assert target.read_text(encoding="utf-8") == "old"

    def test_atomic_writer_writes_the_new_bytes(self, tmp_path):
        from skaldir import completion

        target = tmp_path / "y.json"
        completion._atomic_write_text(target, "payload")
        assert target.read_text(encoding="utf-8") == "payload"

    def test_no_partial_file_is_visible_while_writing(self, tmp_path):
        """Der Leser sieht immer eine vollständige Datei (Replace-Semantik)."""
        from skaldir import completion

        target = tmp_path / "z.json"
        completion._atomic_write_text(target, "vx")
        seen: list[str] = []

        def reader():
            for _ in range(200):
                if target.exists():
                    seen.append(target.read_text(encoding="utf-8"))

        threads = [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        for i in range(50):
            completion._atomic_write_text(target, "v" + "x" * (i % 7 + 1))
        for t in threads:
            t.join()
        assert seen
        assert all(
            s.startswith("v") and s[1:] == "x" * len(s[1:]) and len(s) >= 2 for s in seen
        ), seen[:5]


@pytest.fixture(autouse=True)
def _clean_quarantine(tmp_path):
    yield
    for path in tmp_path.rglob("*.corrupt*"):
        try:
            path.unlink()
        except OSError:  # pragma: no cover
            pass
    shutil.rmtree(tmp_path, ignore_errors=True)
