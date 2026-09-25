"""§5-rest: strategy novelty — the same strategy retried against the same error.

§6 asks to track STRATEGY_NOVELTY. The hard, checkable part: an error that comes
back after the SAME tool strategy (same name + args signature within 2 steps)
is a blind retry; the same error after a DIFFERENT strategy is normal debugging
and must not be flagged. strategy_repeat carries a pause trigger (2).
"""

import time
from types import SimpleNamespace

from skaldir.events import EventStore
from skaldir.monitor import Monitor
from skaldir.pause import PauseStore, VoicePause

FAIL = "AssertionError: expected 1, found 2"


class _OkRunner:
    def run(self, **kwargs):
        return SimpleNamespace(
            status="ok",
            intervention_needed=True,
            no_intervention_reason="",
            research_id="r-strategy",
            duration_ms=1,
            redirect_md=lambda: "## Direction\ntry a different approach",
        )


def _call(events, sid, step, name, args):
    return events.append(sid, step, "tool_call", {"name": name, "args": args})


def _result(events, sid, step, error):
    return events.append(sid, step, "tool_result", {"error": error, "exit_code": 1})


class TestStrategyNovelty:
    def test_same_strategy_against_the_same_error_is_flagged(self, tmp_path):
        events = EventStore(tmp_path / "e.db")
        monitor = Monitor()
        monitor.ingest(_call(events, "s1", 5, "shell", {"command": "pytest -q"}))
        first = monitor.ingest(_result(events, "s1", 6, FAIL))
        assert "strategy_repeat" not in [s.kind for s in first]
        monitor.ingest(_call(events, "s1", 9, "shell", {"command": "pytest -q"}))
        second = monitor.ingest(_result(events, "s1", 10, FAIL))
        kinds = [s.kind for s in second]
        assert "strategy_repeat" in kinds
        signal = next(s for s in second if s.kind == "strategy_repeat")
        assert signal.refs["strategy"] == "shell"
        events.close()

    def test_new_strategy_after_the_same_error_is_not_flagged(self, tmp_path):
        """Same error, different tool strategy = normal debugging, not a blind retry."""
        events = EventStore(tmp_path / "e.db")
        monitor = Monitor()
        monitor.ingest(_call(events, "s1", 5, "shell", {"command": "pytest -q"}))
        monitor.ingest(_result(events, "s1", 6, FAIL))
        monitor.ingest(_call(events, "s1", 9, "shell", {"command": "pytest -q -x tests/test_x.py"}))
        second = monitor.ingest(_result(events, "s1", 10, FAIL))
        assert "strategy_repeat" not in [s.kind for s in second]
        events.close()

    def test_error_without_prior_tool_call_is_safe(self, tmp_path):
        events = EventStore(tmp_path / "e.db")
        monitor = Monitor()
        for step in (3, 8):
            signals = monitor.ingest(_result(events, "s1", step, FAIL))
            assert "strategy_repeat" not in [s.kind for s in signals]
        events.close()

    def test_trigger_pauses_on_repeated_strategy(self, tmp_path):
        """Three failures with the SAME strategy -> two strategy_repeat signals ->
        the pause threshold (2) is met."""
        events = EventStore(tmp_path / "e.db")
        store = PauseStore(tmp_path / "p.db")
        pause = VoicePause(store, "s1", research_runner=_OkRunner(), cooldown_seconds=0.0)
        monitor = Monitor()
        signals = []
        for step in (5, 9, 13):
            monitor.ingest(_call(events, "s1", step, "shell", {"command": "pytest -q"}))
            signals.extend(monitor.ingest(_result(events, "s1", step + 1, FAIL)))
        kinds = [s.kind for s in signals]
        assert kinds.count("strategy_repeat") == 2
        pid = pause.consider(step=16, signals=signals, problem="same strategy retried")
        assert pid is not None
        for _ in range(40):
            lease = store.get(pid)
            if lease and lease["status"] in ("ready", "abandoned", "stale"):
                break
            time.sleep(0.05)
        events.close()
        store.close()
