"""§34 — voice quality from the durable pause history (no new store)."""

import sqlite3

from skaldir.events import EventStore
from skaldir.metrics import compute_metrics, voice_quality, voice_quality_from_db


def _pause(session, fp, status, base, reason=""):
    return {
        "session_id": session,
        "trigger_fp": fp,
        "status": status,
        "base_step": base,
        "released_reason": reason,
        "trigger_kind": "loop",
    }


class TestVoiceQuality:
    def test_delivered_recovery_vs_recurrence(self):
        pauses = [
            _pause("s1", "fp-a", "delivered", 10),
            _pause("s1", "fp-a", "delivered", 30),  # same fp later → the firs one recurred
            _pause("s1", "fp-b", "delivered", 40),  # sticks
            _pause("s1", "fp-c", "abandoned", 60, "Investigation: no intervention needed — normal"),
        ]
        q = voice_quality(pauses)
        assert q["interventions"] == 4
        assert q["delivered"] == 3
        assert q["no_action"] == 1
        assert q["recurrence_after_intervention"] == 1
        assert q["recovery_success"] == 2
        assert q["recovery_success_rate"] == round(2 / 3, 3)
        assert q["no_action_rate"] == 0.25
        assert q["steps_before_first_intervention"] == {"s1": 10}

    def test_empty_history_is_all_zero(self):
        q = voice_quality([])
        assert q["interventions"] == 0
        assert q["recovery_success_rate"] is None
        assert q["no_action_rate"] is None

    def test_from_db_reads_the_real_pauses_table(self, tmp_path):
        db = tmp_path / "pauses.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE pauses (session_id TEXT, trigger_kind TEXT, trigger_fp TEXT,"
            " status TEXT, released_reason TEXT, base_step INTEGER, created REAL)"
        )
        conn.execute("INSERT INTO pauses VALUES ('s1','loop','fp-a','delivered','',10,1.0)")
        conn.execute("INSERT INTO pauses VALUES ('s1','loop','fp-a','delivered','',30,2.0)")
        conn.commit()
        conn.close()
        q = voice_quality_from_db(db)
        assert q["delivered"] == 2
        assert q["recurrence_after_intervention"] == 1

    def test_compute_metrics_carries_the_field(self, tmp_path):
        db = tmp_path / "pauses.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE pauses (session_id TEXT, trigger_kind TEXT, trigger_fp TEXT,"
            " status TEXT, released_reason TEXT, base_step INTEGER, created REAL)"
        )
        conn.commit()
        conn.close()
        store = EventStore(tmp_path / "events.db")
        metrics = compute_metrics(store, pause_db=db)
        store.close()
        assert metrics.voice_quality is not None
        assert metrics.voice_quality["interventions"] == 0
