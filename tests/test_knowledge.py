"""Knowledge store: INFO-01..04 — Markdown units, search, versioned reads, freshness."""
from __future__ import annotations

import pytest

from skaldir.knowledge import (
    KINDS,
    LINE_TOO_LARGE,
    STALE_REFERENCE,
    KnowledgeEntry,
    KnowledgeStore,
)


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(runtime=tmp_path)


def _entry(**kw):
    defaults = dict(
        id="test-entry",
        title="Test Entry",
        kind="fact",
        scope="global",
        status="candidate",
        body="Line one.\nLine two.\nLine three.",
    )
    defaults.update(kw)
    return KnowledgeEntry(**defaults)


class TestINFO01Store:
    """Small Markdown units with metadata."""

    def test_upsert_and_get_roundtrip(self, store):
        entry = store.upsert(_entry())
        fetched = store.get("test-entry")
        assert fetched is not None
        assert fetched.id == "test-entry"
        assert fetched.title == "Test Entry"
        assert fetched.kind == "fact"
        assert fetched.revision == entry.revision
        assert fetched.body == "Line one.\nLine two.\nLine three."

    def test_revision_is_sha256_of_body(self, store):
        entry = store.upsert(_entry())
        import hashlib

        expected = hashlib.sha256(b"Line one.\nLine two.\nLine three.").hexdigest()
        assert entry.revision == expected

    def test_frontmatter_roundtrip(self, store):
        store.upsert(
            _entry(
                sources=["evidence-1", "evidence-2"],
                applies_to={"app": "firefox", "version": "1.0"},
                valid_until="2099-01-01T00:00:00Z",
                verified_at="2026-09-24T00:00:00Z",
            )
        )
        fetched = store.get("test-entry")
        assert fetched.sources == ["evidence-1", "evidence-2"]
        assert fetched.applies_to == {"app": "firefox", "version": "1.0"}
        assert fetched.valid_until == "2099-01-01T00:00:00Z"
        assert fetched.verified_at == "2026-09-24T00:00:00Z"

    def test_all_kinds_are_valid(self, store):
        for kind in KINDS:
            entry = store.upsert(_entry(id=f"e-{kind}", kind=kind))
            assert entry.kind == kind

    def test_get_missing_returns_none(self, store):
        assert store.get("nope") is None


class TestINFO02Search:
    """Bounded FTS5 search with scope/kind/status filters."""

    def test_search_finds_by_content(self, store):
        store.upsert(_entry(id="a", body="PDF export loses fonts on Linux"))
        store.upsert(_entry(id="b", body="Table columns misalign after import"))
        result = store.search("export fonts")
        ids = [m["id"] for m in result["matches"]]
        assert "a" in ids
        assert "b" not in ids

    def test_search_returns_snippet_and_lines(self, store):
        store.upsert(_entry(id="a", body="Alpha.\nBeta.\nGamma export fonts here.\nDelta."))
        result = store.search("export fonts")
        assert result["matches"]
        m = result["matches"][0]
        assert m["id"] == "a"
        assert m["start_line"] >= 1
        assert m["end_line"] >= m["start_line"]
        assert m["revision"]
        assert m["snippet"]

    def test_search_scope_filter(self, store):
        store.upsert(_entry(id="a", scope="project:x", body="shared keyword"))
        store.upsert(_entry(id="b", scope="project:y", body="shared keyword"))
        result = store.search("shared keyword", scope="project:x")
        ids = [m["id"] for m in result["matches"]]
        assert "a" in ids
        assert "b" not in ids

    def test_search_kind_filter(self, store):
        store.upsert(_entry(id="a", kind="procedure", body="how to export"))
        store.upsert(_entry(id="b", kind="decision", body="how to export"))
        result = store.search("export", kinds=["procedure"])
        ids = [m["id"] for m in result["matches"]]
        assert "a" in ids
        assert "b" not in ids

    def test_search_respects_limit(self, store):
        for i in range(10):
            store.upsert(_entry(id=f"e{i}", body=f"common keyword {i}"))
        result = store.search("common keyword", limit=3)
        assert len(result["matches"]) <= 3

    def test_search_empty_is_valid(self, store):
        result = store.search("nonexistent term xyzzy")
        assert result["matches"] == []
        assert result["truncated"] is False


class TestINFO03VersionedReads:
    """Version-bound line reads with STALE_REFERENCE on mismatch."""

    def test_read_all_lines(self, store):
        store.upsert(_entry(body="L1\nL2\nL3"))
        result = store.read_lines("test-entry")
        assert result["total_lines"] == 3
        assert len(result["lines"]) == 3
        assert result["lines"][0] == {"number": 1, "text": "L1"}
        assert result["lines"][2] == {"number": 3, "text": "L3"}
        assert result["truncated"] is False

    def test_read_line_range(self, store):
        store.upsert(_entry(body="L1\nL2\nL3\nL4\nL5"))
        result = store.read_lines("test-entry", start_line=2, end_line=4)
        assert result["returned_start_line"] == 2
        assert result["returned_end_line"] == 4
        assert len(result["lines"]) == 3

    def test_stale_reference_on_revision_mismatch(self, store):
        entry = store.upsert(_entry(body="L1\nL2"))
        result = store.read_lines("test-entry", expected_revision="wrong-revision")
        assert result["error"] == STALE_REFERENCE
        assert result["revision"] == entry.revision

    def test_correct_revision_passes(self, store):
        entry = store.upsert(_entry(body="L1\nL2"))
        result = store.read_lines("test-entry", expected_revision=entry.revision)
        assert "error" not in result
        assert result["revision"] == entry.revision

    def test_invalid_range_beyond_eof(self, store):
        store.upsert(_entry(body="L1\nL2"))
        result = store.read_lines("test-entry", start_line=10)
        assert result["error"] == "INVALID_RANGE"

    def test_empty_entry(self, store):
        store.upsert(_entry(body=""))
        result = store.read_lines("test-entry")
        assert result["total_lines"] == 0
        assert result["lines"] == []

    def test_truncated_when_budget_exceeded(self, store):
        body = "\n".join(f"Line {i} with some content here" for i in range(100))
        store.upsert(_entry(body=body))
        result = store.read_lines("test-entry", max_output_tokens=10)
        assert result["truncated"] is True
        assert result["next_start_line"] is not None

    def test_line_too_large(self, store):
        huge_line = "x" * 50_000
        store.upsert(_entry(body="short\n" + huge_line))
        result = store.read_lines("test-entry", max_output_tokens=10)
        assert result["error"] == LINE_TOO_LARGE


class TestINFO04Freshness:
    """Freshness: stale entries are flagged, not silently trusted."""

    def test_no_valid_until_is_not_stale(self, store):
        store.upsert(_entry(valid_until=""))
        entry = store.get("test-entry")
        assert entry.is_stale is False

    def test_future_valid_until_is_not_stale(self, store):
        store.upsert(_entry(valid_until="2099-01-01T00:00:00Z"))
        entry = store.get("test-entry")
        assert entry.is_stale is False

    def test_past_valid_until_is_stale(self, store):
        store.upsert(_entry(valid_until="2020-01-01T00:00:00Z"))
        entry = store.get("test-entry")
        assert entry.is_stale is True

    def test_search_flags_stale(self, store):
        store.upsert(_entry(id="a", body="export fonts", valid_until="2020-01-01T00:00:00Z"))
        result = store.search("export fonts")
        assert result["matches"][0]["is_stale"] is True
