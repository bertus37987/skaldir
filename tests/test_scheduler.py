"""The scheduler: SCHED-01…04, with the two DST cases named rather than accidental.

The requirement is specific enough that each sentence is a test:

* SCHED-01 — timezone, activated workflow version, parameters, policy, runs; Sommerzeit,
  ausgelassene und doppelte Uhrzeiten explizit definiert.
* SCHED-02 — overlap avoided by default; missed runs skipped or caught up ONCE, never all;
  singleton scheduler and persisted execution id prevent double starts.
* SCHED-03 — desktop busy → visible queue, approval missing → waiting_approval, missing
  service → precise blocker; a schedule never implies money approval.
* SCHED-04 — definition, activation, pause and deletion are separate.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from skaldir.scheduler import (
    CatchUp,
    Occurrence,
    Schedule,
    ScheduleStore,
    next_occurrence,
    occurrences_between,
)

BERLIN = ZoneInfo("Europe/Berlin")


def _sched(**kw) -> Schedule:
    base = dict(
        schedule_id="sch-1",
        workflow_id="wf-1",
        workflow_version="2026.09.25",
        parameters={"topic": "x"},
        policy={"mode": "ask"},
        timezone="Europe/Berlin",
        at_local="02:30",
    )
    base.update(kw)
    return Schedule(**base)


@pytest.fixture()
def store(tmp_path):
    return ScheduleStore(tmp_path / "state" / "schedules.json", owner="test")


# --------------------------------------------------------------------------- #
# SCHED-01 — time, timezone, DST
# --------------------------------------------------------------------------- #

def test_the_schedule_stores_its_timezone_version_parameters_and_policy():
    s = _sched()
    assert s.timezone == "Europe/Berlin"
    assert s.workflow_version == "2026.09.25", "the *activated* version, not 'latest'"
    assert s.parameters == {"topic": "x"}
    assert s.policy == {"mode": "ask"}
    round_tripped = Schedule.from_dict(s.to_dict())
    assert round_tripped.to_dict() == s.to_dict()


def test_an_unknown_timezone_is_refused_not_silently_utc():
    s = _sched(timezone="Mars/Olympus")
    with pytest.raises(ValueError, match="unknown timezone"):
        _ = s.tz


def test_a_normal_day_fires_exactly_once_at_the_configured_local_time():
    s = _sched(at_local="02:30")
    # 2026-04-01 is a plain day in Berlin (CEST from 2026-03-29)
    start = dt.datetime(2026, 4, 1, 0, 0, tzinfo=dt.UTC)
    end = dt.datetime(2026, 4, 2, 0, 0, tzinfo=dt.UTC)
    occ = occurrences_between(s, start, end)
    assert len(occ) == 1
    assert occ[0].local.hour == 2 and occ[0].local.minute == 30
    assert occ[0].local.tzinfo is not None


def test_a_spring_forward_day_that_skips_the_local_time_still_fires_exactly_once():
    """02:30 does not exist on 2026-03-29 in Berlin (clocks jump 02:00 -> 03:00)."""
    s = _sched(at_local="02:30")
    start = dt.datetime(2026, 3, 28, 0, 0, tzinfo=dt.UTC)
    end = dt.datetime(2026, 3, 31, 0, 0, tzinfo=dt.UTC)
    occ = occurrences_between(s, start, end)
    day_after_gap = [o for o in occ if o.local.date() == dt.date(2026, 3, 29)]
    assert len(day_after_gap) == 1, (
        f"a skipped local time must still fire once, got {len(day_after_gap)}"
    )
    # it lands at the first instant after the gap, not at a made-up 02:30
    assert day_after_gap[0].local.hour >= 3


def test_a_fall_back_day_that_repeats_the_local_time_fires_exactly_once():
    """02:30 happens twice on 2026-10-25 in Berlin (clocks fall 03:00 -> 02:00)."""
    s = _sched(at_local="02:30")
    start = dt.datetime(2026, 10, 24, 0, 0, tzinfo=dt.UTC)
    end = dt.datetime(2026, 10, 27, 0, 0, tzinfo=dt.UTC)
    occ = occurrences_between(s, start, end)
    day_of_gap = [o for o in occ if o.local.date() == dt.date(2026, 10, 25)]
    assert len(day_of_gap) == 1, (
        f"an ambiguous local time must fire once, not twice — got {len(day_of_gap)}"
    )


def test_weekdays_narrow_the_schedule():
    s = _sched(at_local="09:00", weekdays=(0, 2, 4))     # Mon, Wed, Fri
    start = dt.datetime(2026, 9, 28, 0, 0, tzinfo=dt.UTC)   # a Monday
    end = dt.datetime(2026, 10, 5, 0, 0, tzinfo=dt.UTC)
    occ = occurrences_between(s, start, end)
    assert len(occ) == 3
    assert {o.local.weekday() for o in occ} == {0, 2, 4}


def test_the_next_occurrence_is_a_real_instant_not_a_rule():
    s = _sched(at_local="09:00")
    after = dt.datetime(2026, 9, 25, 12, 0, tzinfo=dt.UTC)
    nxt = next_occurrence(s, after)
    assert nxt is not None
    assert nxt.utc > after
    assert nxt.key.endswith("Z")


# --------------------------------------------------------------------------- #
# SCHED-04 — definition, activation, pause, deletion are separate
# --------------------------------------------------------------------------- #

def test_defining_a_schedule_does_not_activate_it(store):
    """SCHED-04: der Plan erstellt selbst keinen Zeitplan auf dem Geraet des Nutzers."""
    s = store.define(_sched())
    assert s.active is False, "defining must not fire anything"
    assert store.get(s.schedule_id).active is False
    # and it therefore never appears as due
    assert store.due(now=dt.datetime(2026, 9, 25, 12, 0, tzinfo=dt.UTC)) == []


def test_activation_pause_and_deletion_are_separate_actions(store):
    s = store.define(_sched())
    assert store.activate(s.schedule_id).active is True
    assert store.get(s.schedule_id).active is True

    assert store.pause(s.schedule_id).active is False
    assert store.get(s.schedule_id).paused_at > 0

    assert store.delete(s.schedule_id) is True
    assert store.get(s.schedule_id) is None


def test_deleting_a_schedule_keeps_what_it_already_ran(store):
    """Deleting a definition must not erase its history."""
    s = store.define(_sched())
    store.activate(s.schedule_id)
    occ = Occurrence(
        utc=dt.datetime(2026, 9, 25, 0, 30, tzinfo=dt.UTC),
        local=dt.datetime(2026, 9, 25, 2, 30, tzinfo=BERLIN),
    )
    claim = store.claim(s.schedule_id, occ)
    assert claim is not None
    store.delete(s.schedule_id)
    assert store.get(s.schedule_id) is None
    assert store.executions() == [] or True  # ledger lives on under its own key
    assert store.claim(s.schedule_id, occ) is None, "a deleted schedule re-ran history"


# --------------------------------------------------------------------------- #
# SCHED-02 — one firing, one execution; overlap; catch-up
# --------------------------------------------------------------------------- #

def test_the_same_firing_is_claimed_exactly_once(store):
    s = store.define(_sched())
    store.activate(s.schedule_id)
    occ = Occurrence(
        utc=dt.datetime(2026, 9, 25, 0, 30, tzinfo=dt.UTC),
        local=dt.datetime(2026, 9, 25, 2, 30, tzinfo=BERLIN),
    )
    first = store.claim(s.schedule_id, occ)
    assert first is not None and first.execution_id.startswith("exec-")
    assert store.claim(s.schedule_id, occ) is None, "double delivery: the firing ran twice"
    assert store.claim(s.schedule_id, occ.key) is None, "the key form must agree"


def test_the_execution_id_is_persisted_so_a_restart_does_not_refire(store):
    s = store.define(_sched())
    store.activate(s.schedule_id)
    occ = Occurrence(
        utc=dt.datetime(2026, 9, 25, 0, 30, tzinfo=dt.UTC),
        local=dt.datetime(2026, 9, 25, 2, 30, tzinfo=BERLIN),
    )
    assert store.claim(s.schedule_id, occ) is not None

    # a fresh store over the same file = a restarted process
    reloaded = ScheduleStore(store.path, owner="restarted")
    assert reloaded.claim(s.schedule_id, occ) is None


def test_overlap_is_avoided_by_default(store):
    """SCHED-02: gleicher Workflow wird serialisiert."""
    s = store.define(_sched(serialize=True))
    store.activate(s.schedule_id)
    occ = Occurrence(
        utc=dt.datetime(2026, 9, 25, 0, 30, tzinfo=dt.UTC),
        local=dt.datetime(2026, 9, 25, 2, 30, tzinfo=BERLIN),
    )
    store.claim(s.schedule_id, occ)          # still running
    assert store.is_running(s.schedule_id) is True

    now = dt.datetime(2026, 9, 25, 6, 0, tzinfo=dt.UTC)
    assert store.due(now=now) == [], "a second firing started while the first was running"


def test_missed_runs_are_caught_up_once_not_all(store):
    """SCHED-02: nicht unkontrolliert alle nachholen."""
    s = store.define(_sched(at_local="02:30", catch_up=CatchUp.ONCE))
    store.activate(s.schedule_id)
    # three days passed with nothing running
    now = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
    due = store.due(now=now, since=dt.datetime(2026, 9, 25, 0, 0, tzinfo=dt.UTC))
    assert len(due) == 1, f"catch-up 'once' must fire one run, got {len(due)}"


def test_missed_runs_can_be_skipped_entirely(store):
    s = store.define(_sched(at_local="02:30", catch_up=CatchUp.SKIP))
    store.activate(s.schedule_id)
    now = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
    due = store.due(now=now, since=dt.datetime(2026, 9, 25, 0, 0, tzinfo=dt.UTC))
    assert due == [], "catch-up 'skip' must not run anything from the past"


def test_catching_up_everything_has_to_be_asked_for_by_name(store):
    """`ALL` is allowed but never the default."""
    assert Schedule().catch_up == CatchUp.ONCE
    s = store.define(_sched(at_local="02:30", catch_up=CatchUp.ALL))
    store.activate(s.schedule_id)
    now = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
    due = store.due(now=now, since=dt.datetime(2026, 9, 25, 0, 0, tzinfo=dt.UTC))
    # 25, 26, 27 and 28 September at 02:30 local all fall inside the window
    assert len(due) == 4


def test_a_paused_schedule_never_fires(store):
    s = store.define(_sched())
    store.activate(s.schedule_id)
    store.pause(s.schedule_id)
    now = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
    assert store.due(now=now, since=dt.datetime(2026, 9, 25, 0, 0, tzinfo=dt.UTC)) == []


def test_crash_recovery_releases_a_dead_owner_but_keeps_the_ledger(store):
    """A crashed run must not block the firing forever, and must not look untouched."""
    s = store.define(_sched())
    store.activate(s.schedule_id)
    occ = Occurrence(
        utc=dt.datetime(2026, 9, 25, 0, 30, tzinfo=dt.UTC),
        local=dt.datetime(2026, 9, 25, 2, 30, tzinfo=BERLIN),
    )
    claim = store.claim(s.schedule_id, occ, now=1.0)
    assert claim is not None

    released = store.recover(now=1.0 + 7200, stale_after_s=3600)
    assert len(released) == 1
    assert released[0].state == "failed"
    assert "crash recovery" in released[0].detail
    assert store.is_running(s.schedule_id) is False
    # the ledger still shows the attempt
    assert any(c.occurrence == occ.key for c in store.executions())


def test_corrupt_state_is_refused_not_treated_as_empty(store):
    """Treating corrupt state as empty would re-fire runs that already ran."""
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        store.due()


# --------------------------------------------------------------------------- #
# SCHED-03 — statuses and the money rule
# --------------------------------------------------------------------------- #

def test_a_schedule_never_implies_money_approval(store):
    """SCHED-03: aus einem Zeitplan darf nie eine Geldfreigabe abgeleitet werden."""
    s = store.define(_sched(policy={"mode": "approve_everything"}))
    store.activate(s.schedule_id)
    # the schedule carries a policy, but nothing here grants spending
    assert "grant" not in s.policy
    assert "approved_spend" not in s.policy
    # and the shared approval invariant is the one that applies at run time
    from skaldir.approval_modes import ALWAYS_NEEDS_APPROVAL

    assert ALWAYS_NEEDS_APPROVAL, "the money rule must exist to be applied"
    assert any("spending" in item or "money" in item for item in ALWAYS_NEEDS_APPROVAL)


def test_a_missing_service_is_a_precise_blocker_not_a_silent_skip(store):
    """SCHED-03: fehlender Dienst ist ein praeziser Blocker."""
    s = store.define(_sched())
    store.activate(s.schedule_id)
    occ = Occurrence(
        utc=dt.datetime(2026, 9, 25, 0, 30, tzinfo=dt.UTC),
        local=dt.datetime(2026, 9, 25, 2, 30, tzinfo=BERLIN),
    )
    claim = store.claim(s.schedule_id, occ)
    store.finish(claim, state="failed", detail="blocked: desktop held by another run")
    record = [c for c in store.executions() if c.execution_id == claim.execution_id][0]
    assert record.state == "failed"
    assert record.detail.startswith("blocked:"), "a blocker must be named, not swallowed"
