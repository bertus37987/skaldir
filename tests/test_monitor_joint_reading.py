"""Audit 13.09.: strategy_repeat und error_recurrence_after_progress wurden
gleichzeitig emittiert, obwohl sie sich widersprechen. Gemeinsame semantische
Beurteilung: nach ECHTEM Fortschritt ist der wiederholte Testlauf keine
'unveränderte Strategie' — das präzise Signal ist die Rekurrenz nach
Fortschritt. Ohne Fortschritt bleibt die Wiederholungswarnung bestehen."""


from skaldir.events import EventStore
from skaldir.monitor import Monitor

FAIL = "AssertionError: expected 1, found 2"


def _call(events, sid, step, command):
    return events.append(sid, step, "tool_call", {"name": "shell", "args": {"command": command}})


def _fail(events, sid, step):
    return events.append(sid, step, "tool_result", {"error": FAIL, "exit_code": 1})


def _ok(events, sid, step):
    return events.append(sid, step, "tool_result", {"output": "green", "exit_code": 0})


def test_no_contradiction_after_real_progress(tmp_path):
    events = EventStore(tmp_path / "e.db")
    monitor = Monitor()
    for _step, fn in (
        (5, lambda: _call(events, "s1", 5, "pytest -q")),
        (6, lambda: _fail(events, "s1", 6)),
        (7, lambda: _ok(events, "s1", 7)),  # real progress: a green side check
        (9, lambda: _call(events, "s1", 9, "pytest -q")),
    ):
        monitor.ingest(fn())
    second = monitor.ingest(_fail(events, "s1", 10))
    kinds = [s.kind for s in second]
    assert "strategy_repeat" not in kinds, "same command after real progress is not a blind retry"
    assert "error_recurrence_after_progress" in kinds
    events.close()


def test_blind_retry_without_progress_still_flags(tmp_path):
    events = EventStore(tmp_path / "e.db")
    monitor = Monitor()
    for _step, fn in (
        (5, lambda: _call(events, "s1", 5, "pytest -q")),
        (6, lambda: _fail(events, "s1", 6)),
        (9, lambda: _call(events, "s1", 9, "pytest -q")),
    ):
        monitor.ingest(fn())
    second = monitor.ingest(_fail(events, "s1", 10))
    kinds = [s.kind for s in second]
    assert "strategy_repeat" in kinds, "unchanged strategy without progress stays flagged"
    events.close()


def test_repeat_first_time_still_silent(tmp_path):
    events = EventStore(tmp_path / "e.db")
    monitor = Monitor()
    monitor.ingest(_call(events, "s1", 5, "pytest -q"))
    first = monitor.ingest(_fail(events, "s1", 6))
    assert "strategy_repeat" not in [s.kind for s in first]
    events.close()
