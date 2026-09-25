"""§38/GS-04 wired: the live monitor only escalates on checked signatures.

`IncidentStore.intervention_signatures()` is the applicability-checked set
(status + `valid_until` + confidence). `signatures()` lists *everything* ever
recorded. Feeding the latter into `Monitor(known_error_fingerprints=...)` lets
an archived, expired or never-reviewed entry raise a STRONG intervention
(`known_incident_repeat`, pause trigger threshold 1) — exactly what the
retrieval rule forbids.

These tests pin the runtime path, not just the store API: the GodsvSession the
Hermes plugin builds must hand the checked set to its monitor.
"""

import godsv_plugin
import pytest

from skaldir.events import Event
from skaldir.incidents import Incident, IncidentStore
from skaldir.monitor import SEV_HIGH, Monitor, error_fingerprint

STRONG_TEXT = "ValueError: parse mode 'strict' unsupported"
STALE_TEXT = "RuntimeError: trainer dataset shard missing"
NEVER_TEXT = "KeyError: 'session_token' while restoring checkpoint"
ARCHIVED_TEXT = "ImportError: no module named 'legacy_trainer'"


class _FakeCtx:
    def __init__(self) -> None:
        self.hooks: dict = {}

    def register_hook(self, name, fn):
        self.hooks[name] = fn


def _seed(state_dir):
    """One actively reviewed incident plus every stale shape (Block 7)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    store = IncidentStore(state_dir / "memory.db")
    checked = error_fingerprint(STRONG_TEXT)
    stale = [
        # expired: was right once, its validity window has passed
        Incident(
            signature=error_fingerprint(STALE_TEXT),
            title="Dataset-Shard",
            kind="error",
            status="active",
            confidence=0.9,
            valid_until=1.0,
        ),
        # never reviewed: a proposal nobody promoted
        Incident(
            signature=error_fingerprint(NEVER_TEXT),
            title="Checkpoint-Restore",
            kind="error",
            status="candidate",
            confidence=0.9,
        ),
        # withdrawn
        Incident(
            signature=error_fingerprint(ARCHIVED_TEXT),
            title="Legacy import",
            kind="error",
            status="archived",
            confidence=0.9,
        ),
        # the one a human actually reviewed and kept
        Incident(
            signature=checked,
            title="Parser-Fehler",
            kind="error",
            status="active",
            confidence=0.9,
        ),
    ]
    for incident in stale:
        store.upsert(incident)
    store.close()
    return checked


def _repeat_twice(monitor, text=STALE_TEXT, start=1):
    """Two identical failures, no progress between them → the repeat path."""
    payload = {"error": text}
    monitor.ingest(
        Event(seq=start, ts=0.0, session_id="s", step=start, kind="tool_result", payload=payload)
    )
    return monitor.ingest(
        Event(
            seq=start + 1,
            ts=0.0,
            session_id="s",
            step=start + 1,
            kind="tool_result",
            payload=payload,
        )
    )


class TestStaleIncidentNeverEscalates:
    """The checked set excludes every unreviewed/expired/archived entry."""

    def test_only_the_reviewed_incident_is_intervention_eligible(self, tmp_path):
        checked = _seed(tmp_path)
        store = IncidentStore(tmp_path / "memory.db")
        try:
            eligible = store.intervention_signatures()
            assert checked in eligible
            # the broad API is untouched — it keeps listing everything
            assert store.signatures() == eligible | {
                error_fingerprint(STALE_TEXT),
                error_fingerprint(NEVER_TEXT),
                error_fingerprint(ARCHIVED_TEXT),
            }
        finally:
            store.close()

    def test_a_stale_fingerprint_does_not_raise_the_strong_incident_signal(self, tmp_path):
        """(b) in the task: expired/archived/unreviewed ⇒ no strong escalation."""
        _seed(tmp_path)
        store = IncidentStore(tmp_path / "memory.db")
        try:
            monitor = Monitor(
                error_repeat=2, known_error_fingerprints=store.intervention_signatures()
            )
        finally:
            store.close()

        kinds = {signal.kind for signal in _repeat_twice(monitor)}
        assert "known_incident_repeat" not in kinds
        # the repeat itself was seen — it was classified weak, not ignored
        assert "error_burst" in kinds
        # and the checked entry still escalates: the gate is not an off-switch
        kinds_checked = {signal.kind for signal in _repeat_twice(monitor, STRONG_TEXT)}
        assert "known_incident_repeat" in kinds_checked

    def test_a_stale_match_cannot_escalate_on_the_first_sighting(self):
        """'strong' is concrete: the incident trigger needs 1, the burst needs 2."""
        from skaldir.pause import TRIGGER_RULES

        assert TRIGGER_RULES["known_incident_repeat"] == 1
        assert TRIGGER_RULES["error_burst"] == 2


class TestLiveMonitorIsWiredToTheCheckedSet:
    """The runtime path: GodsvSession must feed the monitor the gated set."""

    @pytest.fixture
    def ctx(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("GODSV_MODE", "whisper")
        monkeypatch.delenv("GODSV_GUARD_FORCE", raising=False)
        monkeypatch.delenv("GODSV_PAUSE", raising=False)
        godsv_plugin._SESSIONS.clear()
        fake = _FakeCtx()
        godsv_plugin.register(fake)
        yield fake
        godsv_plugin._SESSIONS.clear()

    def test_the_session_monitor_holds_only_intervention_eligible_fps(
        self, ctx, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / "godsv"
        state_dir.mkdir(parents=True, exist_ok=True)
        store = IncidentStore(state_dir / "memory.db")
        checked = error_fingerprint(STRONG_TEXT)
        store.upsert(
            Incident(
                signature=checked,
                title="Parser-Fehler",
                kind="error",
                status="active",
                confidence=0.9,
            )
        )
        # expired, unreviewed and withdrawn shapes of the same class
        store.upsert(
            Incident(
                signature=error_fingerprint(STALE_TEXT),
                title="Dataset-Shard",
                kind="error",
                status="active",
                confidence=0.9,
                valid_until=1.0,
            )
        )
        store.upsert(
            Incident(
                signature=error_fingerprint(NEVER_TEXT),
                title="Checkpoint-Restore",
                kind="error",
                status="candidate",
                confidence=0.9,
            )
        )
        store.close()

        ctx.hooks["on_session_start"](session_id="w1")
        session = godsv_plugin._SESSIONS["w1"]
        wired = session.monitor.known_error_fingerprints
        assert checked in wired
        # the stale shapes never reach the live monitor's strong-incident rule
        assert error_fingerprint(STALE_TEXT) not in wired
        assert error_fingerprint(NEVER_TEXT) not in wired

    def test_the_live_session_does_not_escalate_a_stale_fingerprint(
        self, ctx, monkeypatch, tmp_path
    ):
        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / "godsv"
        state_dir.mkdir(parents=True, exist_ok=True)
        store = IncidentStore(state_dir / "memory.db")
        store.upsert(
            Incident(
                signature=error_fingerprint(STALE_TEXT),
                title="Dataset-Shard",
                kind="error",
                status="active",
                confidence=0.9,
                valid_until=1.0,  # long expired
            )
        )
        store.close()

        ctx.hooks["on_session_start"](session_id="w2")
        monitor = godsv_plugin._SESSIONS["w2"].monitor
        kinds = {signal.kind for signal in _repeat_twice(monitor)}
        assert "known_incident_repeat" not in kinds
        high = [s for s in _repeat_twice(monitor, start=3) if s.severity >= SEV_HIGH]
        assert "known_incident_repeat" not in {s.kind for s in high}
