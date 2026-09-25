"""False-loop regression (§32) — signals, not verdicts.

Proven live (13.09.): with no stderr the error text was 'exit code 1 (no
output)', so EVERY unrelated exit-1 failure shared one fingerprint — which
was also seeded as a known-incident fingerprint. Eight unrelated failures
across 50 steps got collapsed into 'known error repeating' and paused the
agent. These tests pin the fixed semantics:

- coarse exit-only errors: never a repeat, never an incident match;
- two DIFFERENT failures near in time: no loop, no known-repeat (the exact
  AssertionError-then-SyntaxError scene from the report);
- same signature after real progress: recurrence signal, not a verdict;
- genuine repetition without progress: still escalates;
- signal-only kinds have no pause trigger.
"""


from skaldir.events import EventStore
from skaldir.monitor import (
    Monitor,
    error_fingerprint,
    is_coarse_error_text,
)
from skaldir.pause import PauseStore, VoicePause

ASSERTION = (
    "Traceback (most recent call last):\n"
    '  File "tools/patch.py", line 20, in sub\n    assert n == 1\n'
    "AssertionError: expected 1, found 2"
)
SYNTAX = (
    "Traceback (most recent call last):\n"
    '  File "src/skaldir/cli.py", line 786\n'
    '    f"{pending[\'snippet\']}" ({...}) — a claim is "\n'
    "SyntaxError: invalid character '—' (U+2014)"
)


def _monitor(tmp_path, **kw):
    events = EventStore(tmp_path / "events.db")
    return Monitor(**kw), events


def _tool_result(events, step, payload):
    return events.append("s1", step, "tool_result", payload)


class TestCoarseErrors:
    def test_coarse_text_detection_and_fallback(self):
        assert is_coarse_error_text("exit code 1 (no output)")
        assert is_coarse_error_text("tool_error: exit 1")
        assert not is_coarse_error_text(ASSERTION)

    def test_exit_only_errors_never_match_even_a_seeded_fingerprint(self, tmp_path):
        monitor, events = _monitor(
            tmp_path,
            known_error_fingerprints={error_fingerprint("exit code 1 (no output)")},
        )
        kinds = []
        for step in (2, 8):
            event = _tool_result(events, step, {"exit_code": 1, "error": "tool_error: exit 1"})
            kinds.extend(s.kind for s in monitor.ingest(event))
        assert "known_incident_repeat" not in kinds
        assert "error_burst" not in kinds
        assert "error_without_details" in kinds


class TestUnrelatedFailuresNearInTime:
    def test_assertion_then_syntax_error_is_not_a_loop(self, tmp_path):
        """The exact scene from the report — unrelated failures get a signal,
        not a loop/incident verdict."""
        monitor, events = _monitor(
            tmp_path,
            known_error_fingerprints={error_fingerprint("exit code 1 (no output)")},
        )
        first = _tool_result(events, 10, {"error": ASSERTION, "exit_code": 1})
        second = _tool_result(events, 16, {"error": SYNTAX, "exit_code": 1})
        kinds = [s.kind for s in monitor.ingest(first)] + [s.kind for s in monitor.ingest(second)]
        assert "known_incident_repeat" not in kinds
        assert "error_burst" not in kinds
        assert "loop" not in kinds

    def test_three_distinct_failures_emit_cluster_signal_not_a_verdict(self, tmp_path):
        monitor, events = _monitor(tmp_path)
        kinds = []
        tricky = "NameError: name 'foo' is not defined"
        for step, text in ((5, ASSERTION), (7, SYNTAX), (9, tricky)):
            event = _tool_result(events, step, {"error": text})
            kinds.extend(signal.kind for signal in monitor.ingest(event))
        assert "error_cluster" in kinds
        assert "known_incident_repeat" not in kinds
        assert "error_burst" not in kinds


class TestGenuineRepeat:
    def test_same_signature_without_progress_still_escalates(self, tmp_path):
        monitor, events = _monitor(tmp_path)
        first = _tool_result(events, 10, {"error": ASSERTION})
        second = _tool_result(events, 12, {"error": ASSERTION})
        kinds = [s.kind for s in monitor.ingest(first)] + [s.kind for s in monitor.ingest(second)]
        assert "error_burst" in kinds

    def test_known_incident_repeat_needs_known_fp_and_no_progress(self, tmp_path):
        monitor, events = _monitor(
            tmp_path, known_error_fingerprints={error_fingerprint(ASSERTION)}
        )
        first = _tool_result(events, 10, {"error": ASSERTION})
        second = _tool_result(events, 12, {"error": ASSERTION})
        kinds = [s.kind for s in monitor.ingest(first)] + [s.kind for s in monitor.ingest(second)]
        assert "known_incident_repeat" in kinds

    def test_recurrence_after_real_progress_is_a_signal_not_a_verdict(self, tmp_path):
        monitor, events = _monitor(
            tmp_path, known_error_fingerprints={error_fingerprint(ASSERTION)}
        )
        first = _tool_result(events, 10, {"error": ASSERTION})
        monitor.ingest(first)
        ok = _tool_result(events, 11, {"result": "ok", "exit_code": 0})  # real progress
        monitor.ingest(ok)
        again = _tool_result(events, 14, {"error": ASSERTION})
        kinds = [s.kind for s in monitor.ingest(again)]
        assert "error_recurrence_after_progress" in kinds
        assert "known_incident_repeat" not in kinds


class TestSignalOnlyKindsDoNotPause:
    def test_cluster_and_coarse_signals_have_no_pause_trigger(self, tmp_path):
        from skaldir.monitor import SEV_WARN, Signal

        store = PauseStore(tmp_path / "pauses.db")
        pause = VoicePause(store, "s1", research_runner=None, cooldown_seconds=0.0)
        for kind in ("error_cluster", "error_without_details", "error_recurrence_after_progress"):
            sig = Signal(kind=kind, severity=SEV_WARN, fingerprint=f"x:{kind}", message="m")
            assert pause.consider(step=10, signals=[sig], problem="p") is None, kind
