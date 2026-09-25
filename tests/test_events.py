"""Event-Register: emittierte Kinds müssen registriert sein (Audit-Fix T1/T2)."""

from skaldir.events import KINDS, EventStore


def test_pause_no_action_is_a_registered_event_kind(tmp_path):
    assert "pause_no_action" in KINDS
    store = EventStore(tmp_path / "events.db")
    try:
        store.append(
            session_id="s1",
            step=1,
            kind="pause_no_action",
            payload={"pause": "p1", "reason": "no intervention needed"},
        )
        kinds = [event.kind for event in store.iter_session("s1")]
        assert "pause_no_action" in kinds
    finally:
        store.close()


def test_treasury_selftest_is_a_registered_event_kind():
    assert "treasury_selftest" in KINDS


def test_live_emitters_kinds_are_registered(tmp_path):
    """pause.py emits voice_intervention_assessment and run_supervisor emits run_sweep
    through EventStore; an unregistered kind raises ValueError (seen live)."""
    from skaldir.events import EventStore

    store = EventStore(tmp_path / "events.db")
    store.append(
        session_id="s1", step=1, kind="voice_intervention_assessment", payload={"intervene": False}
    )
    store.append(session_id="s1", step=2, kind="run_sweep", payload={"checked": 0})
    kinds = [e.kind for e in store.stream()]
    assert "voice_intervention_assessment" in kinds
    assert "run_sweep" in kinds
