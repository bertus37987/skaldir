"""P1-7 — measured (session DB) vs estimated token accounting."""

import sqlite3
from pathlib import Path

from skaldir.events import EventStore
from skaldir.metrics import compute_metrics, measured_session_usage


def _make_db(tmp_path) -> Path:
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, input_tokens INTEGER,"
        " output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER,"
        " reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL)"
    )
    conn.execute(
        "INSERT INTO sessions VALUES ('s-measured', 1200, 800, 300, 0, 42, 0.0123, NULL)"
    )
    conn.commit()
    conn.close()
    return db


class TestMeasuredUsage:
    def test_measured_session_usage_reads_real_counters(self, tmp_path):
        db = _make_db(tmp_path)
        usage = measured_session_usage(db, "s-measured")
        assert usage["input_tokens"] == 1200
        assert usage["output_tokens"] == 800
        assert usage["reasoning_tokens"] == 42
        assert usage["estimated_cost_usd"] == 0.0123
        assert "actual_cost_usd" not in usage  # NULL is omitted, never guessed at

    def test_missing_db_or_session_is_none_never_guessed(self, tmp_path):
        assert measured_session_usage(tmp_path / "nope.db", "x") is None
        db = _make_db(tmp_path)
        assert measured_session_usage(db, "other-session") is None

    def test_metrics_separate_measured_from_estimated(self, tmp_path):
        db = _make_db(tmp_path)
        events = EventStore(tmp_path / "events.db")
        metrics = compute_metrics(events, session_db=db, session_id="s-measured")
        events.close()
        assert metrics.main_agent_tokens_measured["input_tokens"] == 1200
        assert metrics.research_tokens_est == 0  # estimates stay their own field
        plain_store = EventStore(tmp_path / "events2.db")
        plain = compute_metrics(plain_store)
        plain_store.close()
        assert plain.main_agent_tokens_measured is None  # not queried is not zero

    def test_metrics_dict_exposes_the_new_field(self, tmp_path):
        db = _make_db(tmp_path)
        events = EventStore(tmp_path / "events.db")
        data = compute_metrics(events, session_db=db, session_id="s-measured").to_dict()
        events.close()
        assert data["main_agent_tokens_measured"]["input_tokens"] == 1200
