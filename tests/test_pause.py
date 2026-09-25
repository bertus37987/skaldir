"""Voice pause — leases, watchdog, exactly-once resume, crash recovery."""

import time

from skaldir.monitor import Signal
from skaldir.pause import (
    KIND_USER,
    STATUS_ABANDONED,
    STATUS_DELIVERED,
    STATUS_EXPIRED,
    STATUS_READY,
    PauseStore,
    VoicePause,
)
from skaldir.research import ResearchResult


class FakeRunner:
    """Instant research double (no LLM, no repo scan)."""

    def __init__(self, *, delay: float = 0.0, fail: bool = False) -> None:
        self.delay = delay
        self.fail = fail
        self.calls: list[dict] = []

    def run(self, *, session_id, base_step, trigger_kind, trigger_fp, problem):
        self.calls.append({"trigger": trigger_kind, "fp": trigger_fp, "base_step": base_step})
        if self.delay:
            time.sleep(self.delay)
        return ResearchResult(
            research_id="research-test",
            session_id=session_id,
            base_step=base_step,
            trigger_kind=trigger_kind,
            trigger_fp=trigger_fp,
            problem=problem,
            evidence=["step 3: pytest failed"],
            direction="Anderen Ansatz wählen",
            avoid="Wiederholung",
            uncertainty="keine",
            status="failed" if self.fail else "ok",
        )


def _sig(kind="loop", fp="fp1"):
    return Signal(kind=kind, severity=2, fingerprint=fp, message=f"{kind} detected")


def _wait_terminal_or_ready(store, pause_id, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lease = store.get(pause_id)
        if lease is not None and lease["status"] in (STATUS_READY, STATUS_ABANDONED):
            return lease
        time.sleep(0.02)
    return store.get(pause_id)


def _pause(tmp_path, runner=None, **kwargs):
    store = PauseStore(tmp_path / "pauses.db")
    events: list[tuple[str, dict]] = []
    pause = VoicePause(
        store,
        "s1",
        research_runner=runner or FakeRunner(),
        emit=lambda kind, payload: events.append((kind, payload)),
        **kwargs,
    )
    return pause, store, events


def _request(pause, *, step=4, problem="x"):
    """First signal counts, second triggers — returns the lease id."""
    pause.consider(step=step - 2, signals=[_sig()], problem=problem)
    lease_id = pause.consider(step=step, signals=[_sig()], problem=problem)
    assert lease_id is not None, "expected the pause to trigger"
    return lease_id


class TestPauseStore:
    def test_exactly_once_delivery(self, tmp_path):
        store = PauseStore(tmp_path / "p.db")
        lease = store.create(
            session_id="s", base_step=1, trigger_kind="loop", trigger_fp="fp", problem="p"
        )
        store.set_status(lease["id"], STATUS_READY, from_statuses=["requested"])
        assert store.mark_delivered(lease["id"]) is True
        assert store.mark_delivered(lease["id"]) is False  # only once, ever
        assert store.get(lease["id"])["status"] == STATUS_DELIVERED
        store.close()

    def test_expiry_only_touches_voice(self, tmp_path):
        store = PauseStore(tmp_path / "p.db")
        voice = store.create(
            session_id="s", base_step=1, trigger_kind="loop", trigger_fp="f1",
            problem="p", ttl=-1,
        )
        user = store.create(
            session_id="s", kind=KIND_USER, base_step=1, trigger_kind="user", trigger_fp="u",
            problem="Nutzerpause", ttl=-1,
        )
        expired = store.expire_stale()
        assert [lease["id"] for lease in expired] == [voice["id"]]
        assert store.get(user["id"])["status"] != STATUS_EXPIRED
        store.close()

    def test_heartbeat_staleness_abandons_research(self, tmp_path):
        store = PauseStore(tmp_path / "p.db")
        lease = store.create(
            session_id="s", base_step=1, trigger_kind="loop", trigger_fp="f", problem="p"
        )
        store.set_status(lease["id"], "researching", from_statuses=["requested"])
        dead = store.abandon_dead_research(now=time.time() + 120, stale_after=30)
        assert [item["id"] for item in dead] == [lease["id"]]
        assert store.get(lease["id"])["status"] == STATUS_ABANDONED
        store.close()


class TestTriggerPolicy:
    def test_threshold_and_cooldown(self, tmp_path):
        pause, store, _events = _pause(tmp_path)
        assert pause.consider(step=2, signals=[_sig()], problem="x") is None  # 1st signal
        lease_id = pause.consider(step=4, signals=[_sig()], problem="x")  # 2nd signal
        assert lease_id is not None
        # an active lease blocks further requests (no competing interventions)
        assert pause.consider(step=6, signals=[_sig(fp="fp2")], problem="y") is None
        pause.release("test")
        # cooldown for the same fingerprint after a pause
        pause._counts["loop:fp1"] = 2
        assert pause.consider(step=8, signals=[_sig()], problem="x") is None
        store.close()

    def test_contradiction_triggers_immediately(self, tmp_path):
        pause, store, _events = _pause(tmp_path)
        contradiction = {"claim": "läuft", "fingerprint": "abc", "claim_step": 3, "error_step": 5}
        lease_id = pause.consider(step=5, contradiction=contradiction, problem="widerspruch")
        assert lease_id is not None
        store.close()


class TestDelivery:
    def test_redirect_delivered_exactly_once(self, tmp_path):
        pause, store, events = _pause(tmp_path)
        lease_id = _request(pause)
        _wait_terminal_or_ready(store, lease_id)
        first = pause.deliverable(step=5, wait=1.0)
        assert first is not None and "voice·pause" in first["redirect"]
        assert pause.deliverable(step=6, wait=0.0) is None
        kinds = [kind for kind, _payload in events]
        assert "pause_requested" in kinds
        assert "research_started" in kinds
        assert "research_done" in kinds
        assert "pause_delivered" in kinds
        store.close()

    def test_stale_result_discarded(self, tmp_path):
        pause, store, events = _pause(tmp_path)
        pause.consider(step=1, signals=[_sig()], problem="x")
        lease_id = pause.consider(step=3, signals=[_sig()], problem="x")
        assert lease_id is not None
        _wait_terminal_or_ready(store, lease_id)
        assert pause.deliverable(step=40, wait=0.5) is None  # 37 steps later → stale
        assert "pause_stale" in [kind for kind, _ in events]
        assert store.get(lease_id)["status"] != STATUS_DELIVERED
        store.close()

    def test_failed_research_abandons_but_emits(self, tmp_path):
        pause, store, events = _pause(tmp_path, runner=FakeRunner(fail=True))
        lease_id = _request(pause)
        _wait_terminal_or_ready(store, lease_id)
        assert store.get(lease_id)["status"] == STATUS_ABANDONED
        assert "research_failed" in [kind for kind, _ in events]
        assert pause.deliverable(step=5, wait=0.2) is None
        store.close()

    def test_expired_lease_recovered_on_read(self, tmp_path):
        pause, store, events = _pause(tmp_path, runner=FakeRunner(delay=1.0))
        lease_id = _request(pause)
        # simulate a dead process: lease long past its TTL, still 'researching'
        store._db.execute("UPDATE pauses SET expires = 1.0 WHERE id = ?", (lease_id,))
        store._db.commit()
        assert pause.deliverable(step=5, wait=0.2) is None
        assert "pause_expired" in [kind for kind, _ in events]
        store.close()

    def test_crash_of_research_thread_releases(self, tmp_path):
        class Exploding:
            def run(self, **kwargs):
                raise RuntimeError("boom")

        pause, store, events = _pause(tmp_path, runner=Exploding())
        lease_id = _request(pause)
        _wait_terminal_or_ready(store, lease_id)
        assert store.get(lease_id)["status"] == STATUS_ABANDONED
        assert "research_failed" in [kind for kind, _ in events]
        store.close()
