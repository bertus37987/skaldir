"""Hard resume tests (spec §21): no voice research pause may deadlock a session.

Scenarios:
  * voice process killed      → watchdog abandon releases the main agent
  * research hangs            → lease expiry releases the main agent
  * full runtime restart      → stale pause is recovered, session continues
  * multiple resume events    → exactly one resume, ever (concurrent)
Plus the intervention cost gate (spec §1): a pause is only spent when the
expected cost of continuing exceeds the expected cost of the intervention.
"""

import threading
import time

from skaldir.monitor import SEV_WARN, Signal
from skaldir.pause import (
    STATUS_ABANDONED,
    STATUS_DELIVERED,
    STATUS_EXPIRED,
    STATUS_READY,
    STATUS_RESEARCHING,
    PauseStore,
    VoicePause,
)
from skaldir.voice import EST_PAUSE_COST, EST_TOKENS_PER_STEP, assess_intervention


class HangingRunner:
    """Research that never finishes (simulates a killed/hung voice)."""

    def run(self, *, session_id, base_step, trigger_kind, trigger_fp, problem):
        time.sleep(60)
        return {"status": "ok"}


class SlowRunner:
    def run(self, *, session_id, base_step, trigger_kind, trigger_fp, problem):
        time.sleep(0.15)
        return {"status": "ok", "summary": "spät"}


def _sig(kind="loop", fp="fp1", severity=SEV_WARN):
    return Signal(kind=kind, severity=severity, fingerprint=fp, message=f"{kind} detected")


def _make_pause(tmp_path, runner, **kwargs):
    store = PauseStore(tmp_path / "pauses.db")
    events: list[tuple[str, dict]] = []
    pause = VoicePause(
        store,
        "s1",
        research_runner=runner,
        emit=lambda kind, payload: events.append((kind, payload)),
        **kwargs,
    )
    return pause, store, events


class TestHardResume:
    def test_voice_process_killed_watchdog_releases_main_agent(self, tmp_path):
        """Voice paused, research hangs (process effectively dead) → abandon.”

        The watchdog (abandon_dead_research) must release the lease so the main
        agent is never blocked by a dead voice; a new pause stays possible.
        """
        pause, store, events = _make_pause(tmp_path, HangingRunner())
        pause.consider(step=2, signals=[_sig()], problem="x")
        lease_id = pause.consider(step=4, signals=[_sig()], problem="x")
        assert lease_id is not None
        # kill = no more heartbeats from the voice research
        abandoned = store.abandon_dead_research(now=time.time() + 120, stale_after=30)
        lease_now = store.get(lease_id)
        assert lease_now is not None
        if lease_now["status"] == STATUS_READY:
            # research finished before the kill; deliver once, then it is terminal
            assert store.mark_delivered(lease_id) is True
            assert store.current("s1") is None
            return
        assert [item["id"] for item in abandoned] == [lease_id]
        assert lease_now["status"] == STATUS_ABANDONED
        # main agent is free again — no deadlock
        assert store.current("s1") is None
        # a fresh pause (new fingerprint — the cooldown for an old one is by design)
        pause.consider(step=10, signals=[_sig(fp="fp2")], problem="y")
        second = pause.consider(step=12, signals=[_sig(fp="fp2")], problem="y")
        assert second is not None and second != lease_id
        store.close()

    def test_research_hangs_lease_expires_and_releases(self, tmp_path):
        store = PauseStore(tmp_path / "p.db")
        lease = store.create(
            session_id="s1", base_step=1, trigger_kind="loop", trigger_fp="f", problem="p",
            ttl=0.2,
        )
        store.set_status(lease["id"], STATUS_RESEARCHING, from_statuses=["requested"])
        time.sleep(0.3)
        expired = store.expire_stale()
        assert lease["id"] in [item["id"] for item in expired]
        assert store.get(lease["id"])["status"] == STATUS_EXPIRED
        # expired voice pauses never block the session
        assert store.current("s1") is None
        store.close()

    def test_full_runtime_restart_recovers_stale_pause(self, tmp_path):
        """A new process (fresh PauseStore on the same DB) recovers stale leases."""
        path = tmp_path / "p.db"
        store = PauseStore(path)
        lease = store.create(
            session_id="s1", base_step=1, trigger_kind="loop", trigger_fp="f", problem="p",
        )
        store.set_status(lease["id"], STATUS_RESEARCHING, from_statuses=["requested"])
        store.close()  # ← runtime dies here (no graceful shutdown)

        reopened = PauseStore(path)  # ← runtime restarts
        recovered = reopened.abandon_dead_research(now=time.time() + 120, stale_after=30)
        assert [item["id"] for item in recovered] == [lease["id"]]
        assert reopened.get(lease["id"])["status"] == STATUS_ABANDONED
        # and expiry on restart also covers the ttl path
        fresh = reopened.create(
            session_id="s1", base_step=5, trigger_kind="loop", trigger_fp="f2", problem="p",
            ttl=-1,
        )
        expired = reopened.expire_stale()
        assert fresh["id"] in [item["id"] for item in expired]
        assert reopened.current("s1") is None
        reopened.close()

    def test_multiple_resume_events_resume_exactly_once(self, tmp_path):
        store = PauseStore(tmp_path / "p.db")
        lease = store.create(
            session_id="s", base_step=1, trigger_kind="loop", trigger_fp="fp", problem="p"
        )
        store.set_status(lease["id"], STATUS_READY, from_statuses=["requested"])
        results: list[bool] = []
        lock = threading.Lock()

        def deliver():
            ok = store.mark_delivered(lease["id"])
            with lock:
                results.append(ok)

        threads = [threading.Thread(target=deliver) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert results.count(True) == 1, f"exactly one resume, got {results}"
        assert store.get(lease["id"])["status"] == STATUS_DELIVERED
        store.close()


class TestInterventionCostGate:
    def test_assessment_math_and_measured_estimated_split(self):
        small = assess_intervention(trigger_kind="loop", severity=SEV_WARN, occurrences=1)
        assert small.intervene is False  # one warn occurrence < pause cost
        assert small.measured == {"occurrences": 1, "severity": SEV_WARN, "steps_in_window": 0}
        assert small.estimated["continuation_tokens"] == EST_TOKENS_PER_STEP
        assert small.estimated["intervention_tokens"] == EST_PAUSE_COST
        big = assess_intervention(trigger_kind="loop", severity=SEV_WARN, occurrences=2)
        assert big.intervene is True  # 2 × 2200 > 3500
        capped = assess_intervention(trigger_kind="loop", severity=3, occurrences=50)
        assert capped.measured["occurrences"] == 50  # measured stays true
        assert capped.continuation_tokens == 2200 * 2.2 * 6  # estimate capped at 6

    def test_cost_gate_blocks_a_low_severity_pause(self, tmp_path):
        """A trigger with severity 1 does not buy a pause (event proves the math)."""
        pause, store, events = _make_pause(tmp_path, SlowRunner())
        low = _sig(severity=1)
        pause.consider(step=2, signals=[low], problem="x")
        lease_id = pause.consider(step=4, signals=[low], problem="x")
        assert lease_id is None  # threshold reached, but the cost gate said no
        assessments = [
            payload for kind, payload in events if kind == "voice_intervention_assessment"
        ]
        assert assessments and assessments[-1]["intervene"] is False
        assert assessments[-1]["measured"]["occurrences"] == 2
        assert assessments[-1]["estimated"]["intervention_tokens"] == EST_PAUSE_COST
        assert store.current("s1") is None
        store.close()

    def test_pause_records_assessment_when_it_intervenes(self, tmp_path):
        pause, store, events = _make_pause(tmp_path, SlowRunner())
        pause.consider(step=2, signals=[_sig()], problem="x")
        lease_id = pause.consider(step=4, signals=[_sig()], problem="x")
        assert lease_id is not None
        assessments = [
            payload for kind, payload in events if kind == "voice_intervention_assessment"
        ]
        assert assessments and assessments[-1]["intervene"] is True
        store.close()


class TestNewMonitorSignals:
    def test_hallucinated_api_detected(self, tmp_path):
        from skaldir.monitor import Monitor, classify_hallucination

        attr_error = "AttributeError: 'Vault' object has no attribute 'rotate'"
        assert classify_hallucination(attr_error) == "attr"
        assert classify_hallucination("ModuleNotFoundError: No module named 'skywalk'") == "import"
        kwarg_error = "call_llm() got an unexpected keyword argument 'fast'"
        assert classify_hallucination(kwarg_error) == "signature"
        assert classify_hallucination("connection refused") is None

        monitor = Monitor(error_repeat=5)
        from skaldir.events import Event

        first = monitor.ingest(
            Event(seq=1, ts=0.0, session_id="s", step=3, kind="tool_result",
                  payload={"error": attr_error})
        )
        assert "hallucinated_api" not in {signal.kind for signal in first}
        second = monitor.ingest(
            Event(seq=2, ts=0.0, session_id="s", step=4, kind="tool_result",
                  payload={"error": attr_error})
        )
        kinds = {signal.kind for signal in second}
        assert "hallucinated_api" in kinds
        assert next(s for s in second if s.kind == "hallucinated_api").severity == 3

    def test_known_incident_repeat_is_high_severity(self):
        from skaldir.events import Event
        from skaldir.monitor import Monitor, error_fingerprint

        text = "TypeError: x is not iterable"
        fp = error_fingerprint(text)
        monitor = Monitor(error_repeat=2, known_error_fingerprints={fp})
        event = Event(seq=1, ts=0.0, session_id="s", step=5, kind="tool_result",
                      payload={"error": text})
        monitor.ingest(event)
        signals = monitor.ingest(
            Event(seq=2, ts=0.0, session_id="s", step=6, kind="tool_result",
                  payload={"error": text})
        )
        kinds = {signal.kind: signal for signal in signals}
        assert "known_incident_repeat" in kinds
        assert kinds["known_incident_repeat"].refs["known_incident"] is True

    def test_diff_churn_after_many_edit_lines_without_progress(self):
        from skaldir.events import Event
        from skaldir.monitor import Monitor

        monitor = Monitor(churn_lines=100)
        big_add = "\n".join(f"line {i}" for i in range(60))
        signals_a = monitor.ingest(
            Event(seq=1, ts=0.0, session_id="s", step=2, kind="file_edit",
                  payload={"path": "a.py", "added": big_add})
        )
        signals_b = monitor.ingest(
            Event(seq=2, ts=0.0, session_id="s", step=3, kind="file_edit",
                  payload={"path": "b.py", "added": big_add})
        )
        kinds = {signal.kind for signal in signals_a + signals_b}
        assert "diff_churn" in kinds
        # a successful tool result resets the churn window
        monitor.ingest(
            Event(seq=3, ts=0.0, session_id="s", step=4, kind="tool_result", payload={"ok": True})
        )
        signals_c = monitor.ingest(
            Event(seq=4, ts=0.0, session_id="s", step=5, kind="file_edit",
                  payload={"path": "c.py", "added": big_add})
        )
        assert "diff_churn" not in {signal.kind for signal in signals_c}


class TestIncidentIntegration:
    def test_incident_signatures_feed_the_monitor(self, tmp_path):
        """Cross-session: a known incident signature escalates to known_incident_repeat."""
        from skaldir.events import Event
        from skaldir.incidents import Incident, IncidentStore
        from skaldir.monitor import Monitor, error_fingerprint

        text = "ValueError: parse mode 'strict' unsupported"
        fp = error_fingerprint(text)
        store = IncidentStore(tmp_path / "memory.db")
        store.upsert(
            Incident(signature=fp, title="Parser-Fehler", kind="error", root_cause="strict mode")
        )
        assert fp in store.signatures()

        monitor = Monitor(error_repeat=2, known_error_fingerprints=store.signatures())
        payload = {"error": text}
        monitor.ingest(
            Event(seq=1, ts=0.0, session_id="s", step=1, kind="tool_result", payload=payload)
        )
        signals = monitor.ingest(
            Event(seq=2, ts=0.0, session_id="s", step=2, kind="tool_result", payload=payload)
        )
        kinds = {signal.kind for signal in signals}
        assert "known_incident_repeat" in kinds
        store.close()


class TestTriagePromptCategories:
    def test_prompt_carries_the_new_categories(self):
        from pathlib import Path

        text = (
            Path(__file__).resolve().parent.parent / "prompts" / "voice_triage.v1.md"
        ).read_text(encoding="utf-8")
        for category in (
            "scope_creep", "goal_drift", "overengineering", "unnecessary_dependency",
            "unverified_assumption", "hallucinated_api", "ignored_evidence",
            "constraint_forgetting", "wrong_tool", "diff_growth",
        ):
            assert category in text, f"missing triage category: {category}"
        assert "Precision over recall" in text
