"""Voice budget/dedup/severity + injection formatting."""

from skaldir.inject import ADVISORY_FOOTER, InjectionQueue, format_hint, sanitize_injection
from skaldir.monitor import SEV_HIGH, SEV_INFO, SEV_WARN, Signal
from skaldir.voice import Voice


def sig(kind="loop", severity=SEV_WARN, fingerprint="fp1", message="msg"):
    return Signal(kind=kind, severity=severity, fingerprint=fingerprint, message=message)


class TestVoice:
    def test_budget_is_enforced(self):
        voice = Voice(budget=1, min_gap=0)
        assert voice.consider([sig()], 1) is not None
        assert voice.consider([sig(fingerprint="fp2")], 2) is None

    def test_never_repeats_fingerprint(self):
        voice = Voice(budget=5, min_gap=0)
        assert voice.consider([sig()], 1) is not None
        assert voice.consider([sig()], 2) is None

    def test_min_gap_delays(self):
        voice = Voice(budget=5, min_gap=3)
        assert voice.consider([sig()], 1) is not None
        assert voice.consider([sig(fingerprint="fp2")], 3) is None
        assert voice.consider([sig(fingerprint="fp3")], 4) is not None

    def test_highest_severity_wins(self):
        voice = Voice(min_gap=0)
        low = sig(kind="slop_tbd_comment", severity=SEV_INFO, fingerprint="low")
        high = sig(kind="slop_bare_except", severity=SEV_HIGH, fingerprint="high")
        hint = voice.consider([low, high], 1)
        assert hint is not None and hint.kind == "slop_bare_except"

    def test_hint_fields(self):
        voice = Voice(budget=5, min_gap=0)
        hint = voice.consider([sig(kind="error_burst", severity=SEV_HIGH, fingerprint="abc123")], 7)
        assert hint is not None
        assert hint.pos == 1 and hint.total == 5
        assert hint.confidence == 0.75
        assert "abc123" not in hint.text


class TestInject:
    def test_format_contains_header_and_footer(self):
        voice = Voice(min_gap=0)
        hint = voice.consider([sig(kind="loop", fingerprint="abcdef123456")], 1)
        assert hint is not None
        formatted = format_hint(hint)
        assert formatted.startswith("[voice id=1 sig=abcdef12 typ=loop conf=0.70 pos=1/5]")
        assert ADVISORY_FOOTER in formatted

    def test_evidence_rendered(self):
        voice = Voice(min_gap=0)
        hint = voice.consider(
            [sig(kind="error_burst", fingerprint="ef0000000000")],
            1,
            evidence=["Incident #1234 (Test grün)", "PyTorch-Doku 2.9"],
        )
        assert hint is not None
        formatted = format_hint(hint)
        assert "Evidence:" in formatted
        assert "- Incident #1234 (Test grün)" in formatted

    def test_sanitize_strips_control_and_caps_words(self):
        dirty = "a\u200bb\x07 " + " ".join(f"w{i}" for i in range(300))
        clean = sanitize_injection(dirty, max_words=10)
        assert "\u200b" not in clean and "\x07" not in clean
        assert clean.endswith("[…]")
        assert len(clean.split()) <= 12

    def test_queue_push_drain(self):
        queue = InjectionQueue()
        voice = Voice(min_gap=0)
        hint = voice.consider([sig()], 1)
        assert hint is not None
        queue.push(hint)
        assert queue.pending_count() == 1
        assert len(queue.drain()) == 1
        assert queue.pending_count() == 0
