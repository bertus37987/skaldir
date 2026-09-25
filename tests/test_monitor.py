"""Deterministic step monitor: loops, error bursts, slop, scope."""

from skaldir.events import Event
from skaldir.monitor import Monitor, error_fingerprint, scan_slop
from skaldir.pact import Pact


def make_event(step: int, kind: str, payload: dict, session: str = "s", seq: int = 0) -> Event:
    return Event(seq=seq, ts=0.0, session_id=session, step=step, kind=kind, payload=payload)


class TestLoopDetector:
    def test_fires_only_on_third_repeat(self):
        monitor = Monitor()
        call = {"name": "terminal", "args": {"command": "pytest -q"}}
        assert monitor.ingest(make_event(1, "tool_call", call)) == []
        assert monitor.ingest(make_event(2, "tool_call", call)) == []
        signals = monitor.ingest(make_event(3, "tool_call", call))
        assert [s.kind for s in signals] == ["loop"]
        assert signals[0].refs["count"] == 3

    def test_different_args_do_not_fire(self):
        monitor = Monitor()
        for step, command in enumerate(["ls", "ls -l", "ls -la"], start=1):
            payload = {"name": "terminal", "args": {"command": command}}
            assert monitor.ingest(make_event(step, "tool_call", payload)) == []

    def test_outside_window_does_not_fire(self):
        monitor = Monitor(loop_window=2)
        call = {"name": "x", "args": {}}
        monitor.ingest(make_event(1, "tool_call", call))
        monitor.ingest(make_event(2, "tool_call", call))
        monitor.ingest(make_event(10, "tool_call", call))
        assert monitor.ingest(make_event(11, "tool_call", call)) == []


class TestErrorBurst:
    def test_fires_on_second_identical_error(self):
        monitor = Monitor()
        result = {"exit_code": 1, "stderr": "ImportError: cannot import name 'cv_split'"}
        # first occurrence: quiet (a single API-shaped error can be legitimate)
        assert monitor.ingest(make_event(1, "tool_result", result)) == []
        signals = monitor.ingest(make_event(2, "tool_result", result))
        kinds = [s.kind for s in signals]
        # second occurrence: burst + the invented-API classifier both fire
        assert "error_burst" in kinds and "hallucinated_api" in kinds
        burst = next(s for s in signals if s.kind == "error_burst")
        assert burst.severity >= 3

    def test_success_does_not_fire(self):
        monitor = Monitor()
        ok = {"exit_code": 0, "stdout": "ok"}
        monitor.ingest(make_event(1, "tool_result", ok))
        assert monitor.ingest(make_event(2, "tool_result", ok)) == []

    def test_fingerprint_ignores_digits(self):
        assert error_fingerprint("error 42 at line 7") == error_fingerprint("error 13 at line 99")


class TestSlop:
    def test_finds_bare_except_and_todo(self):
        hits = scan_slop("try:\n    x = 1\nexcept:\n    pass\n# TODO fix")
        ids = {hit[0] for hit in hits}
        assert "bare_except" in ids
        assert "todo_placeholder" in ids

    def test_clean_code_has_no_hits(self):
        assert scan_slop("def add(a, b):\n    return a + b\n") == []

    def test_assert_true_is_high_severity(self):
        slop = scan_slop("def test_x():\n    assert True")
        hits = [hit for hit in slop if hit[0] == "assert_true"]
        assert hits and hits[0][1] == 3


class TestScopeGuard:
    def test_edit_outside_scope_fires(self):
        monitor = Monitor(pact=Pact.example())  # scope: src/**, suite/**
        edit = make_event(1, "file_edit", {"path": "lib/utils.py", "added": "x = 1"})
        signals = monitor.ingest(edit)
        assert [s.kind for s in signals] == ["scope_drift"]

    def test_edit_inside_scope_is_quiet(self):
        monitor = Monitor(pact=Pact.example())
        payload = {"path": "src/train.py", "added": "x = 1"}
        assert monitor.ingest(make_event(1, "file_edit", payload)) == []

    def test_no_pact_no_scope_signal(self):
        monitor = Monitor()
        payload = {"path": "lib/x.py", "added": "x = 1"}
        assert monitor.ingest(make_event(1, "file_edit", payload)) == []
