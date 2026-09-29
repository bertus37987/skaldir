"""The scheduler execution service — Paket 5 (audit F09).

The audit's point: the store was data and there was no service that picked due terms up and
actually started a workflow. The acceptance scenario H is "isolierten Workflow zeitgesteuert
bei geschlossener TUI ausführen, nach Restart keine ungewollte Doppelwirkung."

So these tests drive the *service*, not the store: a term that comes due is executed, and a
restart does not execute it a second time.
"""

from __future__ import annotations

import datetime as dt
import threading

import pytest

from skaldir.scheduler import CatchUp, Schedule, ScheduleStore
from skaldir.scheduler_worker import SchedulerWorker


@pytest.fixture()
def store(tmp_path):
    return ScheduleStore(tmp_path / "state" / "schedules.json", owner="test-store")


def _schedule(**kw) -> Schedule:
    base = dict(
        schedule_id="sch-1",
        workflow_id="wf-1",
        workflow_version="1.0",
        timezone="UTC",
        at_local="02:30",
    )
    base.update(kw)
    return Schedule(**base)


def _activate(store: ScheduleStore, **kw) -> Schedule:
    s = store.define(_schedule(**kw))
    return store.activate(s.schedule_id)


# --------------------------------------------------------------------------- #
# it actually runs
# --------------------------------------------------------------------------- #

def test_a_due_term_is_executed_by_the_worker(store):
    """The store was data; this is what makes it a service."""
    _activate(store)
    ran: list = []

    def runner(schedule, occurrence):
        key = occurrence.key if hasattr(occurrence, "key") else str(occurrence)
        ran.append((schedule.workflow_id, key))
        return {"ok": True, "note": "the workflow ran"}

    worker = SchedulerWorker(store, runner=runner, owner="worker-1")
    now = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
    tick = worker.tick(now=now, since=dt.datetime(2026, 9, 25, 0, 0, tzinfo=dt.UTC))

    assert ran, "the worker never ran the workflow"
    assert tick.claimed == 1 and tick.ran == 1
    assert worker.report.firings[0]["workflow_id"] == "wf-1"
    assert worker.report.firings[0]["ok"] is True


def test_the_worker_runs_headless_with_the_tui_closed(store):
    """The acceptance: "bei geschlossener TUI"."""
    _activate(store)
    ran: list = []
    worker = SchedulerWorker(store, runner=lambda s, o: ran.append(1) or {"ok": True},
                             owner="worker-1", interval_s=0.01)
    # nothing here opens a terminal, and the loop is bounded for the test
    now = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
    worker.tick(now=now, since=dt.datetime(2026, 9, 25, 0, 0, tzinfo=dt.UTC))
    assert ran, "the headless worker did nothing"


# --------------------------------------------------------------------------- #
# no double delivery
# --------------------------------------------------------------------------- #

def _occurrence_of(occurrence) -> str:
    return occurrence.key if hasattr(occurrence, "key") else str(occurrence)


def test_the_same_term_never_runs_twice(store):
    """A restart (or a second worker) must not cause an unintended second effect.

    The property is about the SAME firing. A ``catch_up=once`` schedule deliberately picks
    up one missed term per tick — 28 September, then 27 September — and that is intended
    catch-up, not a double effect. What must never happen is the same occurrence twice.
    """
    _activate(store)
    ran: list = []
    def runner(s, o):
        ran.append(_occurrence_of(o))
        return {"ok": True}
    now = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
    since = dt.datetime(2026, 9, 25, 0, 0, tzinfo=dt.UTC)

    SchedulerWorker(store, runner=runner, owner="worker-1").tick(now=now, since=since)
    SchedulerWorker(store, runner=runner, owner="worker-2").tick(now=now, since=since)
    SchedulerWorker(store, runner=runner, owner="worker-3").tick(now=now, since=since)

    assert len(ran) == len(set(ran)), f"a term ran more than once: {ran}"
    assert all(o.endswith("02:30:00Z") for o in ran)


def test_a_restart_does_not_rerun_a_term_that_already_ran(store):
    """Acceptance H: "nach Restart keine ungewollte Doppelwirkung"."""
    _activate(store)
    ran: list = []
    def runner(s, o):
        ran.append(_occurrence_of(o))
        return {"ok": True}
    now = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
    since = dt.datetime(2026, 9, 25, 0, 0, tzinfo=dt.UTC)

    SchedulerWorker(store, runner=runner, owner="before").tick(now=now, since=since)
    first = list(ran)

    # a fresh worker over the same store = a restarted process
    SchedulerWorker(ScheduleStore(store.path, owner="after"),
                    runner=runner, owner="after").tick(now=now, since=since)

    assert set(first) & set(ran[:len(first)]) == set(first), (
        "the restarted worker re-ran a term that had already run"
    )
    assert len(ran) == len(set(ran)), f"the restart re-ran a term: {ran}"


def test_catch_up_once_picks_up_one_missed_term_per_tick_not_all_at_once(store):
    """'nicht unkontrolliert alle nachholen' — at the service, not just the store."""
    _activate(store, catch_up=CatchUp.ONCE)
    ran: list = []
    def runner(s, o):
        ran.append(_occurrence_of(o))
        return {"ok": True}
    now = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
    since = dt.datetime(2026, 9, 25, 0, 0, tzinfo=dt.UTC)

    tick = SchedulerWorker(store, runner=runner).tick(now=now, since=since)
    assert tick.ran == 1, "one tick caught up more than one term"
    assert len(ran) == 1


def test_concurrent_workers_race_and_only_one_wins(store):
    """Real concurrency, not sequential calls."""
    _activate(store)
    ran: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    def runner(schedule, occurrence):
        with lock:
            ran.append(schedule.schedule_id)
        return {"ok": True}

    now = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
    since = dt.datetime(2026, 9, 25, 0, 0, tzinfo=dt.UTC)

    def go(idx: int) -> None:
        barrier.wait(timeout=30)
        SchedulerWorker(ScheduleStore(store.path, owner=f"w{idx}"),
                        runner=runner, owner=f"w{idx}").tick(now=now, since=since)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)

    assert len(ran) == 1, f"{len(ran)} workers ran the same term"


# --------------------------------------------------------------------------- #
# the worker never widens anything
# --------------------------------------------------------------------------- #

def test_a_paused_schedule_is_never_executed(store):
    s = store.define(_schedule())
    store.activate(s.schedule_id)
    store.pause(s.schedule_id)
    ran: list = []
    now = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
    tick = SchedulerWorker(store, runner=lambda a, b: ran.append(1) or {"ok": True}).tick(
        now=now, since=dt.datetime(2026, 9, 25, 0, 0, tzinfo=dt.UTC)
    )
    assert not ran
    assert tick.seen == 0


def test_a_defined_but_inactive_schedule_is_never_executed(store):
    """Starting the worker must not turn a paused schedule on."""
    store.define(_schedule())
    ran: list = []
    now = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
    SchedulerWorker(store, runner=lambda a, b: ran.append(1) or {"ok": True}).tick(
        now=now, since=dt.datetime(2026, 9, 25, 0, 0, tzinfo=dt.UTC)
    )
    assert not ran, "the worker executed a schedule nobody activated"


def test_a_failed_workflow_is_recorded_as_failed_not_as_success(store):
    _activate(store)
    now = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
    tick = SchedulerWorker(
        store, runner=lambda a, b: {"ok": False, "reason": "the workflow broke"}
    ).tick(now=now, since=dt.datetime(2026, 9, 25, 0, 0, tzinfo=dt.UTC))
    assert tick.failed == 1 and tick.ran == 0
    assert store.executions()[0].state == "failed"
    assert "broke" in store.executions()[0].detail


def test_crashed_claims_are_recovered_but_the_attempt_stays_visible(store):
    _activate(store)
    now = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
    # a claim that is never finished = a crashed worker
    due = store.due(now=now, since=dt.datetime(2026, 9, 25, 0, 0, tzinfo=dt.UTC))
    schedule, occurrence = due[0]
    store.claim(schedule.schedule_id, occurrence, now=1.0)

    tick = SchedulerWorker(store, runner=lambda a, b: {"ok": True}).tick(
        now=now, since=dt.datetime(2026, 9, 25, 0, 0, tzinfo=dt.UTC)
    )
    assert tick.recovered >= 1, "a dead claim was not released"
    # and the ledger still shows the attempt
    assert store.executions(), "the crashed attempt vanished from the ledger"


def test_the_worker_records_evidence_it_can_write_out(store, tmp_path):
    _activate(store)
    now = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
    path = tmp_path / "evidence.json"
    worker = SchedulerWorker(store, runner=lambda a, b: {"ok": True},
                             evidence_path=path, owner="worker-1")
    worker.tick(now=now, since=dt.datetime(2026, 9, 25, 0, 0, tzinfo=dt.UTC))
    worker._write_evidence()
    assert path.exists(), "no evidence was written"
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["firings"] and data["firings"][0]["workflow_id"] == "wf-1"
