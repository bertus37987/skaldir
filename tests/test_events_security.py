"""Event store + security primitives (secret scan, spotlight, thread safety)."""

import threading

import pytest

from skaldir.events import EventStore, SecretLeakError
from skaldir.secretscan import redact, scan
from skaldir.spotlight import sanitize, spotlight

# A fake, pattern-shaped key — not a real secret.
FAKE_OPENAI = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"


class TestEventStore:
    def test_append_and_readback(self, tmp_path):
        store = EventStore(tmp_path / "events.db")
        store.append("s1", 1, "thought", {"text": "hallo"}, ts=100.0)
        store.append(
            "s1", 2, "tool_call", {"name": "terminal", "args": {"command": "ls"}}, ts=101.0
        )
        store.append("s2", 1, "thought", {"text": "andere session"}, ts=102.0)
        events = store.iter_session("s1")
        assert [e.seq for e in events] == [1, 2]
        assert events[1].payload["name"] == "terminal"
        assert store.count() == 3
        stream = store.stream(after_seq=2)
        assert [e.session_id for e in stream] == ["s2"]

    def test_unknown_kind_rejected(self, tmp_path):
        store = EventStore(tmp_path / "events.db")
        with pytest.raises(ValueError, match="kind"):
            store.append("s1", 1, "bogus", {})

    def test_oversized_payload_rejected(self, tmp_path):
        store = EventStore(tmp_path / "events.db")
        with pytest.raises(ValueError, match="too large"):
            store.append("s1", 1, "thought", {"text": "x" * 200_000})

    def test_secret_payload_is_rejected(self, tmp_path):
        store = EventStore(tmp_path / "events.db")
        with pytest.raises(SecretLeakError):
            store.append("s1", 1, "tool_result", {"stdout": f"key={FAKE_OPENAI}"})

    def test_append_redacted_masks_and_marks(self, tmp_path):
        store = EventStore(tmp_path / "events.db")
        event = store.append_redacted("s1", 1, "tool_result", {"stdout": f"key={FAKE_OPENAI}"})
        assert "[REDACTED:openai]" in event.payload["stdout"]
        assert event.payload["_godsv_redacted"] == 1


class TestThreadSafety:
    """Hermes invokes plugin hooks on bounded worker threads (regression)."""

    def test_append_from_another_thread_works(self, tmp_path):
        store = EventStore(tmp_path / "events.db")
        store.append("main", 1, "thought", {"text": "main thread"}, ts=1.0)
        errors: list = []

        def worker():
            try:
                store.append("t2", 1, "thought", {"text": "aus Thread"}, ts=2.0)
            except Exception as exc:  # pragma: no cover - regression guard
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        assert errors == []
        assert store.iter_session("t2")[0].payload["text"] == "aus Thread"

    def test_concurrent_appends_are_serialized(self, tmp_path):
        store = EventStore(tmp_path / "events.db")

        def worker(n: int):
            for i in range(10):
                store.append(f"s{n}", i, "thought", {"n": n, "i": i})

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert store.count() == 40
        seqs = [event.seq for event in store.stream()]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 40


class TestSecretScan:
    def test_finds_common_patterns(self):
        text = f"{FAKE_OPENAI} ghp_{'b' * 36} AKIA{'A' * 16}"
        kinds = {f.kind for f in scan(text)}
        assert {"openai", "github_pat", "aws_access_key"} <= kinds

    def test_generic_key_requires_context(self):
        assert scan("the token was short") == []
        findings = scan("token: " + "z" * 24)
        assert any(f.kind == "generic_key" for f in findings)

    def test_redact_removes_value_keeps_marker(self):
        redacted, findings = redact(f"prefix {FAKE_OPENAI} suffix")
        assert findings
        assert FAKE_OPENAI not in redacted
        assert "[REDACTED:openai]" in redacted
        assert redacted.startswith("prefix") and redacted.endswith("suffix")

    def test_preview_never_leaks_full_value(self):
        findings = [f for f in scan(FAKE_OPENAI) if f.kind == "openai"]
        assert len(findings) == 1
        assert FAKE_OPENAI not in findings[0].preview


class TestSpotlight:
    def test_wraps_with_markers(self):
        wrapped = spotlight("hello", source="web", retrieved_at="2026-09-11")
        assert wrapped.startswith("<<<UNTRUSTED source=web retrieved=2026-09-11>>>")
        assert wrapped.endswith("<<<END UNTRUSTED>>>")
        assert "hello" in wrapped

    def test_strips_zero_width_and_control_chars(self):
        assert sanitize("a\u200bb\x07c\td\ne") == "abc\td\ne"

    def test_truncates(self):
        out = sanitize("x" * 50, max_len=10)
        assert out.startswith("x" * 10)
        assert "truncated" in out
