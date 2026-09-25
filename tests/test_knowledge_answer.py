"""MEM-03's third sentence: answer a short question from *selected* documents, with origins.

    "Zusätzlich kann eine kurze Frage auf ausgewählte Dokumente beantwortet werden:
     Antwort mit Originalreferenzen, kein erneutes Einlesen der ganzen Datenbank."

MEM-01/02/04 are already held by `tests/test_knowledge.py` — the store, the metadata, the
SHA revision and `STALE_REFERENCE`. What had no home was this one: a bounded question
over named entries whose every claim can be traced back to a line.

Two of these tests are the point of the requirement rather than of the implementation:
the answer must not re-read the whole database, and it must not invent.
"""

from __future__ import annotations

import pytest

from skaldir.knowledge import KnowledgeEntry, KnowledgeStore


@pytest.fixture()
def store(tmp_path):
    s = KnowledgeStore(runtime=tmp_path / "runtime")
    s.upsert(KnowledgeEntry(
        id="e1",
        title="Desktop lease rules",
        kind="rule",
        scope="global",
        body=(
            "The desktop lease is held by one run at a time.\n"
            "Ownership change invalidates old input.\n"
            "The fence is checked at the input adapter.\n"
        ),
    ))
    s.upsert(KnowledgeEntry(
        id="e2",
        title="Timezone choice",
        kind="decision",
        scope="project",
        body=(
            "The scheduler uses Europe/Berlin.\n"
            "A skipped local time still fires exactly once.\n"
        ),
    ))
    return s


def test_a_question_is_answered_with_original_references(store):
    """Every claim has to be traceable to a line, not just to a document."""
    result = store.answer("which timezone does the scheduler use?", ["e2"])
    assert result["answered"] is True, result
    refs = result["references"]
    assert refs, "an answered question must carry references"
    for ref in refs:
        assert ref["reference"].startswith("e2:"), ref["reference"]
        assert "@" in ref["reference"], "a reference must name the revision"
        assert ref["line_start"] >= 1 and ref["line_end"] >= ref["line_start"]
        assert ref["text"], "a reference without the line it points at is not checkable"
        assert ref["revision"], "and without the revision it could be a different document"


def test_the_reference_names_the_exact_lines(store):
    result = store.answer("Europe/Berlin", ["e2"])
    assert result["references"], result
    ref = result["references"][0]
    assert ref["line_start"] == 1, "the first line is 1-based and inclusive (MEM-04)"
    assert ref["entry_id"] == "e2"
    assert ref["revision"] == store.get("e2").revision


def test_only_the_named_entries_are_read(store):
    """'kein erneutes Einlesen der ganzen Datenbank' — this is the point of the rule."""
    result = store.answer("desktop lease", ["e1"])
    assert result["queried_only"] == ["e1"], "the answer must say exactly what it read"
    assert result["read_entries"] <= ["e1"], (
        f"the answer read beyond the named entries: {result['read_entries']}"
    )
    assert all(ref["entry_id"] == "e1" for ref in result["references"])


def test_refusing_to_name_entries_is_refused_not_silently_full_scan(store):
    """A whole-database read is exactly what MEM-03 forbids."""
    result = store.answer("anything at all", [])
    assert result["error"] == "usage"
    assert "entry_ids" in result["message"]
    assert "whole database" in result["message"] or "ganzen Datenbank" in result["message"]


def test_a_question_the_documents_do_not_answer_is_not_answered_from_nothing(store):
    """An invented answer would be worse than no answer."""
    result = store.answer("what is the airspeed velocity of an unladen swallow", ["e1", "e2"])
    assert result["answered"] is False
    assert result["answer"] == ""
    assert result["references"] == []
    assert "nothing is invented" in result["note"]


def test_an_unknown_entry_is_reported_rather_than_skipped_in_silence(store):
    result = store.answer("desktop lease", ["e1", "does-not-exist"])
    assert result["missing_entries"] == ["does-not-exist"], (
        "a document that could not be read has to be visible in the answer"
    )
    assert result["answered"] is True


def test_the_answer_is_labelled_as_extractive_not_as_prose(store):
    """A reader must know these are quotes, not a summary written over them."""
    result = store.answer("scheduler timezone", ["e2"])
    assert result["kind"] == "extractive"
    assert "not generated prose" in result["note"]


def test_an_empty_question_is_a_usage_error_not_an_empty_answer(store):
    result = store.answer("   ", ["e1"])
    assert result["error"] == "usage"


def test_the_same_entry_may_be_named_twice_without_double_reading(store):
    result = store.answer("desktop lease", ["e1", "e1"])
    assert result["queried_only"] == ["e1"], "the read set is de-duplicated"
