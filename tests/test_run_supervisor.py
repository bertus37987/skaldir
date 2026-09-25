"""Run supervisor — invariant, leases, orphan repair, recovery, watchdog.

Everything runs against an isolated store under ``tmp_path`` (never the live
``~/.hermes/godsv/runs.db``) and against fake liveness/paths, so no test kills,
starts or touches the running God's Voice instance.
"""

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from skaldir import run_supervisor as rs

NOW = 1_000_000.0
DEAD = lambda _pid: False  # noqa: E731 — a dead holder, the live case
ALIVE = lambda _pid: True  # noqa: E731


@pytest.fixture
def store(tmp_path):
    st = rs.RunStore(tmp_path / "runs.db")
    yield st
    st.close()


def expired_lease_run(store, *, run_id="run-1", session="s1", pending_action="", **extra):
    """The live finding: a lease whose holder PID is long dead."""
    return store.create_run(
        session,
        run_id=run_id,
        state=rs.ACTIVE,
        next_owner="worker",
        pending_action=pending_action,
        lease_owner="pid=88437:turn=s1:d8f0b1de:platform=desktop",
        lease_expires_at=NOW - 300.0,
        heartbeat_at=NOW - 600.0,
        last_progress_at=NOW - 600.0,
        now=NOW,
        **extra,
    )


# --- (2) the persistent run record -------------------------------------------


def test_run_record_has_exactly_the_required_fields(store):
    assert rs.field_names() == (
        "run_id",
        "session_id",
        "goal_revision",
        "state",
        "next_owner",
        "pending_action",
        "continuation_generation",
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "wait_reason",
        "wake_source",
        "retry_count",
        "recovery_count",
        "last_progress_at",
        "last_committed_event",
        # §94 PROGRESS-GUARD: the durable per-turn progress signals, and the action the
        # runtime took about them. Additive — a resumed run can now tell a slow run from
        # a stuck one, which is the whole point of persisting them.
        "turn",
        "no_progress_streak",
        "no_tool_streak",
        "last_verification",
        "reply_fingerprint",
        "progress_action",
        "progress_signal",
        "tokens_used",
        "token_budget",
    )
    # the persisted table carries exactly those columns (plus nothing that would
    # duplicate state elsewhere)
    from skaldir.run_supervisor import RunStore

    con = sqlite3.connect(store.path)
    columns = [row[1] for row in con.execute("PRAGMA table_info(runs)")]
    con.close()
    assert columns == list(rs.field_names())
    assert RunStore is not None


def test_record_survives_a_new_process_handle(tmp_path):
    first = rs.RunStore(tmp_path / "runs.db")
    first.create_run("s1", run_id="run-1", state=rs.ACTIVE, goal_revision=3)
    first.close()
    second = rs.RunStore(tmp_path / "runs.db")
    run = second.get("run-1")
    assert run is not None and run.goal_revision == 3 and run.session_id == "s1"
    second.close()


def test_migration_adds_missing_columns_and_keeps_legacy_rows(tmp_path):
    """An old table without the new columns must not break the store."""
    path = tmp_path / "runs.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, session_id TEXT, state TEXT,"
        " lease_owner TEXT, lease_expires_at REAL)"
    )
    con.execute(
        "INSERT INTO runs(run_id, session_id, state, lease_owner, lease_expires_at)"
        " VALUES ('legacy-1', 's-old', 'WORKING', 'pid=1:turn=x', 10.0)"
    )
    con.commit()
    con.close()

    store = rs.RunStore(path)  # runs the additive migration
    run = store.get("legacy-1")
    assert run is not None
    # legacy data is preserved ...
    assert run.session_id == "s-old"
    assert run.lease_owner == "pid=1:turn=x"
    # ... missing columns fall back to defaults, never to a crash
    assert run.goal_revision == 0 and run.continuation_generation == 0
    assert run.recovery_count == 0 and run.last_committed_event == 0
    # a state GV does not know becomes an explicit operator wait, not a repair
    assert run.state == rs.LEGACY_ORPHAN
    assert run.wake_source == "operator:classify"
    check = rs.check_run(store, "legacy-1", now=NOW, alive=DEAD)
    assert check["ok"] is True  # explicit wait with a wake source
    store.close()


def test_legacy_record_participates_without_being_rewritten(store):
    """The migration and the reader must not invent work for old records."""
    con = sqlite3.connect(store.path)
    con.execute(
        "INSERT INTO runs(run_id, session_id, state, lease_owner, lease_expires_at)"
        " VALUES ('legacy-2', 's-old', 'SOME_OLD_STATE', 'pid=99:x', 0)"
    )
    con.commit()
    con.close()

    decision = rs.repair_run(store, "legacy-2", now=NOW, alive=DEAD)
    assert decision["action"] == rs.WAITING_ON_WAKE_SOURCE
    assert store.get("legacy-2").state == rs.LEGACY_ORPHAN


# --- (1) the invariant -------------------------------------------------------


def test_invariant_string_and_disjuncts():
    assert "NON_TERMINAL_RUN" in rs.INVARIANT
    assert rs.DISJUNCTS == (
        rs.ACTIVE_WORKER_LEASE,
        rs.QUEUED_CONTINUATION,
        rs.EXPLICIT_WAIT_WITH_WAKE_SOURCE,
    )


def test_expired_lease_without_queue_and_wait_violates_the_invariant(store):
    """The live-found case, exactly: expired lease, dead holder, no queue/wait."""
    expired_lease_run(store)

    check = rs.check_run(store, "run-1", now=NOW, alive=DEAD)

    assert check["ok"] is False
    assert check["missing"] == list(rs.DISJUNCTS)
    kinds = {violation["kind"] for violation in check["violations"]}
    assert rs.EXPIRED_LEASE in kinds
    assert rs.NO_QUEUE_NO_WAIT in kinds


def test_live_lease_with_heartbeat_satisfies_the_invariant(store):
    store.create_run(
        "s1",
        run_id="run-live",
        state=rs.ACTIVE,
        lease_owner="pid=1234:turn=s1",
        lease_expires_at=NOW + 60,
        heartbeat_at=NOW,
        now=NOW,
    )
    check = rs.check_run(store, "run-live", now=NOW, alive=ALIVE)
    assert check["ok"] is True
    assert check["evidence"] == [rs.ACTIVE_WORKER_LEASE]


def test_expired_lease_with_live_process_is_still_a_violation(store):
    """Liveness alone is not a lease: an expired lease is no ownership."""
    store.create_run(
        "s1",
        run_id="run-2",
        state=rs.ACTIVE,
        lease_owner="pid=1234:turn=s1",
        lease_expires_at=NOW - 1,
        heartbeat_at=NOW - 5,
        now=NOW,
    )
    check = rs.check_run(store, "run-2", now=NOW, alive=ALIVE)
    assert check["ok"] is False
    assert {v["kind"] for v in check["violations"]} == {
        rs.EXPIRED_LEASE,
        rs.NO_QUEUE_NO_WAIT,
    }


def test_dead_holder_with_unexpired_lease_is_an_orphan(store):
    store.create_run(
        "s1",
        run_id="run-3",
        state=rs.ACTIVE,
        lease_owner="pid=88437:turn=s1",
        lease_expires_at=NOW + 600,
        heartbeat_at=NOW,
        now=NOW,
    )
    check = rs.check_run(store, "run-3", now=NOW, alive=DEAD)
    assert check["ok"] is False
    assert rs.ORPHANED_LEASE in {v["kind"] for v in check["violations"]}


def test_stale_heartbeat_on_a_live_lease_is_a_warning_not_a_violation(store):
    """Stale heartbeat: worth reporting, but ownership is intact — no recovery."""
    store.create_run(
        "s1",
        run_id="run-4",
        state=rs.ACTIVE,
        lease_owner="pid=1234:turn=s1",
        lease_expires_at=NOW + 600,
        heartbeat_at=NOW - 500,
        now=NOW,
    )
    check = rs.check_run(store, "run-4", now=NOW, alive=ALIVE)
    assert check["ok"] is True  # a valid lease still holds the invariant
    assert [warning["kind"] for warning in check["warnings"]] == [rs.STALE_HEARTBEAT]
    report = rs.check_invariants(store, now=NOW, alive=ALIVE)
    assert report["ok"] is True
    assert [warning["run_id"] for warning in report["warnings"]] == ["run-4"]


def test_wait_with_wake_source_satisfies_the_invariant(store):
    store.create_run("s1", run_id="run-wait", state=rs.WAITING, now=NOW)
    assert store.wait("run-wait", reason="user reads the report", wake_source="user:continue",
                      now=NOW)
    check = rs.check_run(store, "run-wait", now=NOW, alive=DEAD)
    assert check["ok"] is True
    assert check["evidence"] == [rs.EXPLICIT_WAIT_WITH_WAKE_SOURCE]


def test_a_wait_without_a_wake_source_is_not_an_explicit_wait(store):
    """The invariant demands EXPLICIT_WAIT_*WITH_WAKE_SOURCE* — half is not enough."""
    store.create_run("s1", run_id="run-half", state=rs.WAITING, now=NOW)
    check = rs.check_run(store, "run-half", now=NOW, alive=DEAD)
    assert check["ok"] is False
    assert rs.NO_QUEUE_NO_WAIT in {v["kind"] for v in check["violations"]}


def test_queued_continuation_satisfies_the_invariant(store):
    store.create_run("s1", run_id="run-q", state=rs.QUEUED, now=NOW)
    store.enqueue_continuation("run-q", generation=1, reason="initial", now=NOW)
    store.update("run-q", continuation_generation=1, now=NOW)
    check = rs.check_run(store, "run-q", now=NOW, alive=DEAD)
    assert check["ok"] is True
    assert check["evidence"] == [rs.QUEUED_CONTINUATION]


def test_terminal_run_needs_no_disjunct(store):
    store.create_run("s1", run_id="run-done", state=rs.COMPLETE, now=NOW)
    check = rs.check_run(store, "run-done", now=NOW, alive=DEAD)
    assert check["ok"] is True
    assert check["non_terminal"] is False
    assert rs.check_invariants(store, now=NOW, alive=DEAD)["ok"] is True


def test_check_invariants_only_looks_at_non_terminal_runs(store):
    expired_lease_run(store)
    store.create_run("s2", run_id="run-done", state=rs.FAILED, now=NOW)
    report = rs.check_invariants(store, now=NOW, alive=DEAD)
    assert report["checked"] == 1  # only the non-terminal one
    assert [v["run_id"] for v in report["violations"]] == ["run-1"]
    assert report["ok"] is False


# --- (1) repair --------------------------------------------------------------


def test_repair_requeues_the_live_orphan_case(store):
    expired_lease_run(store)

    decision = rs.repair_run(store, "run-1", now=NOW, alive=DEAD)

    assert decision["action"] == rs.REQUEUED
    assert decision["state_after"] == rs.QUEUED
    assert decision["generation"] == 1
    run = store.get("run-1")
    assert run.state == rs.QUEUED
    assert run.lease_owner == "" and run.lease_expires_at == 0.0
    assert run.recovery_count == 1
    assert run.continuation_generation == 1
    # repair restored the invariant
    assert rs.check_invariants(store, now=NOW, alive=DEAD)["ok"] is True
    assert [c["status"] for c in store.continuations("run-1")] == [rs.CONTINUATION_QUEUED]


def test_repair_is_idempotent_and_leaves_healthy_runs_alone(store):
    expired_lease_run(store)
    assert rs.repair_run(store, "run-1", now=NOW, alive=DEAD)["action"] == rs.REQUEUED
    second = rs.repair_run(store, "run-1", now=NOW + 1, alive=DEAD)
    assert second["action"] == rs.HEALTHY
    assert store.get("run-1").recovery_count == 1  # not bumped twice
    assert len(store.continuations("run-1")) == 1


def test_repair_never_touches_terminal_runs(store):
    store.create_run("s1", run_id="run-done", state=rs.COMPLETE, now=NOW)
    decision = rs.repair_run(store, "run-done", now=NOW, alive=DEAD)
    assert decision["action"] == rs.TERMINAL_UNTOUCHED
    assert store.get("run-done").state == rs.COMPLETE


def test_repair_reports_unknown_runs(store):
    assert rs.repair_run(store, "nope", now=NOW)["action"] == rs.UNKNOWN_RUN


def test_repair_only_recovers_orphans_not_live_runs(store):
    """A healthy run with a live lease keeps its owner through a sweep."""
    store.create_run(
        "s1",
        run_id="run-live",
        state=rs.ACTIVE,
        lease_owner="pid=1234:turn=s1",
        lease_expires_at=NOW + 600,
        heartbeat_at=NOW,
        now=NOW,
    )
    receipt = rs.sweep(store, now=NOW, alive=ALIVE, claim=False)
    assert receipt["violations_before"] == 0
    assert receipt["repairs"] == []
    assert store.get("run-live").lease_owner == "pid=1234:turn=s1"


def test_sweep_repairs_orphans_and_claims_the_continuation(store):
    expired_lease_run(store)

    receipt = rs.sweep(store, owner="watchdog-test", now=NOW, alive=DEAD)

    assert receipt["violations_before"] == 1
    assert receipt["violations_after"] == 0
    assert receipt["ok"] is True
    assert receipt["repairs"][0]["action"] == rs.REQUEUED
    assert len(receipt["claimed"]) == 1
    assert receipt["claimed"][0]["generation"] == 1
    run = store.get("run-1")
    assert run.state == rs.CONTINUING
    assert run.lease_owner == "watchdog-test"


# --- (3) exactly-once continuation -------------------------------------------


def test_enqueue_is_unique_per_run_and_generation(store):
    store.create_run("s1", run_id="run-1", state=rs.ACTIVE, now=NOW)
    first_id, first_new = store.enqueue_continuation("run-1", generation=1, now=NOW)
    second_id, second_new = store.enqueue_continuation("run-1", generation=1, now=NOW)
    assert first_new is True and second_new is False
    assert first_id == second_id
    assert len(store.continuations("run-1")) == 1


def test_crash_between_redirect_and_continue_produces_exactly_one_resume(tmp_path):
    """Store-level crash simulation: repair, drop the handle, restart, recover.

    The crash lands *after* the redirect was recorded and the queue row written,
    but before the continue ran. The restart must produce exactly ONE
    continuation — a second worker (or a replay) must not count again.
    """
    path = tmp_path / "runs.db"
    store = rs.RunStore(path)
    expired_lease_run(store, pending_action="continue-after-redirect")
    store.register_action("continue-after-redirect", idempotent=True)

    # --- crash point: redirect delivered, continuation enqueued -------------
    first_repair = rs.repair_run(store, "run-1", now=NOW, alive=DEAD)
    assert first_repair["action"] == rs.REQUEUED
    assert len(store.continuations("run-1")) == 1
    store.close()  # <- process dies here, before the continue executed

    # --- restart: reconstruct from the store --------------------------------
    restarted = rs.RunStore(path)
    resumed_run = restarted.get("run-1")
    assert resumed_run is not None
    generation = resumed_run.continuation_generation
    assert generation == 1

    claimed = rs.recover_pending(restarted, owner="pid=4321:turn=restart", now=NOW + 5)
    assert len(claimed) == 1

    # the crash-recovery watchdog runs again: it must NOT re-queue the same work
    # (the restarted worker is genuinely alive, so its lease is valid)
    receipt = rs.sweep(
        restarted,
        owner="pid=4321:turn=restart",
        now=NOW + 6,
        alive=lambda pid: pid == 4321,
    )
    assert receipt["violations_before"] == 0
    assert len(receipt["claimed"]) == 0
    assert restarted.continuations("run-1")[-1]["generation"] == generation
    assert len(restarted.continuations("run-1")) == 1
    assert restarted.get("run-1").recovery_count == 1  # exactly one recovery

    assert restarted.commit_continuation(
        "run-1", generation=generation, last_committed_event=7, now=NOW + 7
    )
    restarted.close()


def test_a_second_claim_of_the_same_continuation_is_refused(store):
    store.create_run("s1", run_id="run-1", state=rs.QUEUED, now=NOW)
    store.enqueue_continuation("run-1", generation=1, now=NOW)
    store.update("run-1", continuation_generation=1, now=NOW)

    first = store.claim_continuation("run-1", owner="worker-a", now=NOW)
    second = store.claim_continuation("run-1", owner="worker-b", now=NOW)

    assert first is not None and second is None
    assert store.get("run-1").next_owner == "worker-a"


def test_duplicate_execution_does_not_count(store):
    """The core promise: a repeated commit is a no-op, not a second execution."""
    store.create_run("s1", run_id="run-1", state=rs.QUEUED, now=NOW)
    store.enqueue_continuation("run-1", generation=1, now=NOW)
    store.update("run-1", continuation_generation=1, now=NOW)
    store.claim_continuation("run-1", owner="worker-a", now=NOW)

    assert store.commit_continuation(
        "run-1", generation=1, last_committed_event=5, now=NOW + 1
    ) is True
    assert store.commit_continuation(
        "run-1", generation=1, last_committed_event=99, now=NOW + 2
    ) is False

    run = store.get("run-1")
    assert run.last_committed_event == 5  # the replay did not move it
    assert run.state == rs.ACTIVE
    assert len(store.audit("run-1", limit=100)) > 0
    committed = [
        c for c in store.continuations("run-1") if c["status"] == rs.CONTINUATION_COMMITTED
    ]
    assert len(committed) == 1


def test_last_committed_event_never_moves_backwards(store):
    store.create_run("s1", run_id="run-1", state=rs.ACTIVE, now=NOW)
    store.update("run-1", last_committed_event=20, now=NOW)
    store.enqueue_continuation("run-1", generation=1, now=NOW)
    store.update("run-1", continuation_generation=1, now=NOW)
    store.claim_continuation("run-1", owner="w", now=NOW)
    store.commit_continuation("run-1", generation=1, last_committed_event=3, now=NOW + 1)
    assert store.get("run-1").last_committed_event == 20


def test_claimed_continuation_of_a_dead_owner_is_requeued_at_the_same_generation(store):
    """Exactly-once: recovery revives the SAME generation, never a second one.

    A new generation here would be a second logical continuation — the double
    execution the crash window must not produce.
    """
    store.create_run("s1", run_id="run-1", state=rs.QUEUED, now=NOW)
    store.enqueue_continuation("run-1", generation=1, now=NOW)
    store.update("run-1", continuation_generation=1, now=NOW)
    store.claim_continuation("run-1", owner="pid=88437:turn=s1", now=NOW)

    # the run is orphaned again while generation 1 is still claimed
    assert store.update(
        "run-1", state=rs.ACTIVE, lease_owner="pid=88437:x", lease_expires_at=NOW - 1, now=NOW + 10
    )
    decision = rs.repair_run(store, "run-1", now=NOW + 10, alive=DEAD)

    assert decision["action"] == rs.REQUEUED
    assert decision["generation"] == 1  # same generation, not a new one
    rows = store.continuations("run-1")
    assert len(rows) == 1
    assert rows[0]["generation"] == 1 and rows[0]["status"] == rs.CONTINUATION_QUEUED
    assert rows[0]["claimed_by"] == ""
    claimed = rs.recover_pending(store, owner="w2", now=NOW + 11)
    assert [c["generation"] for c in claimed] == [1]


def test_a_new_generation_is_used_when_no_continuation_exists_yet(store):
    expired_lease_run(store)
    decision = rs.repair_run(store, "run-1", now=NOW, alive=DEAD)
    assert decision["generation"] == 1
    assert [c["generation"] for c in store.continuations("run-1")] == [1]


def test_recover_pending_respects_the_limit(store):
    for index in range(3):
        store.create_run("s", run_id=f"run-{index}", state=rs.QUEUED, now=NOW)
        store.enqueue_continuation(f"run-{index}", generation=1, now=NOW)
        store.update(f"run-{index}", continuation_generation=1, now=NOW)
    claimed = rs.recover_pending(store, owner="w", limit=2, now=NOW)
    assert len(claimed) == 2


def test_orphaned_claimed_continuation_is_detected(store):
    store.create_run(
        "s1",
        run_id="run-1",
        state=rs.ACTIVE,
        lease_owner="pid=88437:turn=s1",
        lease_expires_at=NOW + 600,
        heartbeat_at=NOW,
        now=NOW,
    )
    store.enqueue_continuation("run-1", generation=1, now=NOW)
    store.update("run-1", continuation_generation=1, now=NOW)
    store.claim_continuation("run-1", owner="pid=88437:turn=s1", now=NOW)
    # claimant dies while the run still looks alive
    check = rs.check_run(store, "run-1", now=NOW, alive=DEAD)
    kinds = {v["kind"] for v in check["violations"]}
    assert rs.ORPHANED_LEASE in kinds
    assert rs.ORPHANED_CONTINUATION in kinds


# --- (3) side-effect rule ----------------------------------------------------


def test_side_effect_disposition_matrix(store, tmp_path):
    store.register_action("idempotent-write", idempotent=True)
    store.register_action("unsafe-write", idempotent=False)
    result = tmp_path / "report.json"
    result.write_text("{}", encoding="utf-8")
    store.register_action("verified-write", result_path=str(result))

    assert rs.side_effect_disposition(store, "") == rs.DISPOSITION_IDLE
    assert rs.side_effect_disposition(store, "idempotent-write") == rs.DISPOSITION_REPEAT
    assert rs.side_effect_disposition(store, "verified-write") == rs.DISPOSITION_RESOLVED
    assert rs.side_effect_disposition(store, "unsafe-write") == rs.DISPOSITION_UNKNOWN
    assert rs.side_effect_disposition(store, "never-seen") == rs.DISPOSITION_UNKNOWN


def test_verified_result_through_the_store_counts_as_resolved(store):
    store.register_action("deploy", idempotent=False)
    assert rs.side_effect_disposition(
        store, "deploy", path_exists=lambda path: False
    ) == rs.DISPOSITION_UNKNOWN
    store.verify_result("deploy", exists=True, now=NOW)
    assert rs.side_effect_disposition(
        store, "deploy", path_exists=lambda path: False
    ) == rs.DISPOSITION_RESOLVED


def test_unknown_side_effect_is_parked_not_retried(store):
    """An orphan with an unproven, unverified action → UNKNOWN_SIDE_EFFECT_STATE."""
    expired_lease_run(store, pending_action="charge-the-customer")
    store.register_action("charge-the-customer", idempotent=False)

    decision = rs.repair_run(store, "run-1", now=NOW, alive=DEAD)

    assert decision["action"] == rs.PARKED_UNKNOWN_SIDE_EFFECT
    run = store.get("run-1")
    assert run.state == rs.UNKNOWN_SIDE_EFFECT_STATE
    assert run.next_owner == "operator"
    assert run.wake_source == rs.OPERATOR_WAKE_SOURCE
    assert "charge-the-customer" in run.wait_reason
    assert run.recovery_count == 1
    # nothing was queued: the operator decides, the supervisor does not retry
    assert store.continuations("run-1") == []
    actions = [row["action"] for row in store.audit("run-1")]
    assert "parked_unknown_side_effect" in actions
    assert "orphan_requeued" not in actions


def test_unregistered_action_is_never_repeated_automatically(store):
    expired_lease_run(store, pending_action="mystery-effect")
    decision = rs.repair_run(store, "run-1", now=NOW, alive=DEAD)
    assert decision["action"] == rs.PARKED_UNKNOWN_SIDE_EFFECT
    assert store.get("run-1").state == rs.UNKNOWN_SIDE_EFFECT_STATE


def test_idempotent_action_is_repeated_and_result_verified_action_closes(store, tmp_path):
    expired_lease_run(store, run_id="run-idem", session="s-idem", pending_action="reindex")
    store.register_action("reindex", idempotent=True)
    decision = rs.repair_run(store, "run-idem", now=NOW, alive=DEAD)
    assert decision["action"] == rs.REQUEUED
    row = store.continuations("run-idem")[0]
    assert row["idempotent"] == 1 and row["result_verified"] == 0

    expired_lease_run(store, run_id="run-res", session="s-res", pending_action="write-report")
    result = tmp_path / "report.json"
    result.write_text("{}", encoding="utf-8")
    store.register_action("write-report", result_path=str(result))
    decision = rs.repair_run(store, "run-res", now=NOW, alive=DEAD)
    assert decision["action"] == rs.REQUEUED
    row = store.continuations("run-res")[0]
    assert row["result_verified"] == 1
    # the resolved action is done — it must not be repeated after the resume
    assert store.get("run-res").pending_action == ""


def test_an_idle_orphan_without_any_action_is_requeued(store):
    expired_lease_run(store)  # no pending_action at all
    decision = rs.repair_run(store, "run-1", now=NOW, alive=DEAD)
    assert decision["action"] == rs.REQUEUED
    assert store.continuations("run-1")[0]["idempotent"] == 0


def test_parked_run_is_not_resumed_by_a_later_sweep(store):
    expired_lease_run(store, pending_action="charge-the-customer")
    store.register_action("charge-the-customer", idempotent=False)
    rs.sweep(store, owner="w", now=NOW, alive=DEAD)

    receipt = rs.sweep(store, owner="w", now=NOW + 100, alive=DEAD)

    assert receipt["claimed"] == []
    assert store.get("run-1").state == rs.UNKNOWN_SIDE_EFFECT_STATE
    assert store.continuations("run-1") == []


def test_parking_supersedes_an_open_claim_from_the_crashed_owner(store):
    """A claim left behind by the dead owner must not be executed after parking."""
    expired_lease_run(store, pending_action="charge-the-customer")
    store.register_action("charge-the-customer", idempotent=False)
    store.enqueue_continuation("run-1", generation=1, now=NOW)
    store.update("run-1", continuation_generation=1, now=NOW)
    store.claim_continuation("run-1", owner="pid=88437:turn=s1", now=NOW)

    decision = rs.repair_run(store, "run-1", now=NOW, alive=DEAD)

    assert decision["action"] == rs.PARKED_UNKNOWN_SIDE_EFFECT
    assert [c["status"] for c in store.continuations("run-1")] == [rs.CONTINUATION_SUPERSEDED]
    assert rs.recover_pending(store, owner="w", now=NOW + 1) == []


# --- (3b) policy-owned holds are never overwritten ---------------------------


@pytest.mark.parametrize(
    ("state", "wake_source"),
    [
        (rs.USER_PAUSED, "user:resume"),
        (rs.SAFETY_BLOCKED, "safety:clearance"),
        (rs.APPROVAL_PENDING, "approval:decision"),
        (rs.BUDGET_STOPPED, "budget:top_up"),
    ],
)
def test_recovery_never_overwrites_a_policy_hold(store, state, wake_source):
    """User pause, safety block, approval and budget stop survive every path."""
    store.create_run("s1", run_id="run-held", state=rs.ACTIVE, now=NOW)
    # a stale lease AND a stale pending action: maximum temptation to "repair"
    store.update(
        "run-held",
        lease_owner="pid=88437:turn=s1",
        lease_expires_at=NOW - 600,
        heartbeat_at=NOW - 900,
        pending_action="unsafe-write",
        now=NOW,
    )
    assert store.hold("run-held", state, reason="owner stopped the run", now=NOW)

    decision = rs.repair_run(store, "run-held", now=NOW, alive=DEAD)
    receipt = rs.sweep(store, owner="w", now=NOW, alive=DEAD)
    check = rs.check_run(store, "run-held", now=NOW, alive=DEAD)

    assert decision["action"] == rs.POLICY_OWNED_UNTOUCHED
    # a hold IS an explicit wait, so the invariant already holds: the sweep sees
    # zero violations and therefore never even considers repairing it
    assert receipt["violations_before"] == 0
    assert receipt["repairs"] == []
    run = store.get("run-held")
    assert run.state == state and run.wake_source == wake_source
    assert run.recovery_count == 0
    assert store.continuations("run-held") == []
    # the hold is an explicit wait → the invariant is satisfied, not violated
    assert check["ok"] is True
    assert check["protected"] is True
    assert check["evidence"] == [rs.EXPLICIT_WAIT_WITH_WAKE_SOURCE]


def test_a_direct_update_to_a_held_run_is_refused_by_default(store):
    store.create_run("s1", run_id="run-held", state=rs.ACTIVE, now=NOW)
    store.hold("run-held", rs.SAFETY_BLOCKED, reason="unsafe command", now=NOW)
    assert store.update("run-held", state=rs.QUEUED, now=NOW + 1) is False
    assert store.get("run-held").state == rs.SAFETY_BLOCKED


def test_hold_wake_sources_are_fixed_by_the_policy(store):
    store.create_run("s1", run_id="run-held", state=rs.ACTIVE, now=NOW)
    store.hold("run-held", rs.USER_PAUSED, reason="paused", now=NOW)
    assert store.get("run-held").wake_source == "user:resume"
    with pytest.raises(ValueError):
        store.hold("run-held", rs.QUEUED, reason="nope", now=NOW)
    assert store.wake("run-held", source="operator:side_effect_verified", now=NOW) is False
    assert store.get("run-held").state == rs.USER_PAUSED
    assert store.wake("run-held", source="user:resume", now=NOW) is True
    assert store.get("run-held").state == rs.QUEUED
    assert store.get("run-held").wake_source == ""


def test_recovery_does_not_resume_a_run_the_user_paused(store):
    """End-to-end: an orphaned run that the user then paused stays paused."""
    expired_lease_run(store)
    store.hold("run-1", rs.USER_PAUSED, reason="user paused the task", now=NOW)
    receipt = rs.sweep(store, owner="w", now=NOW + 60, alive=DEAD)
    assert receipt["violations_before"] == 0
    assert store.get("run-1").state == rs.USER_PAUSED
    assert store.continuations("run-1") == []


def test_policy_hold_does_not_mask_a_healthy_run_elsewhere(store):
    store.create_run("s1", run_id="run-held", state=rs.ACTIVE, now=NOW)
    store.hold("run-held", rs.BUDGET_STOPPED, reason="budget exhausted", now=NOW)
    expired_lease_run(store, run_id="run-orphan", session="s2")
    receipt = rs.sweep(store, owner="w", now=NOW, alive=DEAD)
    assert [r["run_id"] for r in receipt["repairs"]] == ["run-orphan"]
    assert store.get("run-held").state == rs.BUDGET_STOPPED
    assert store.get("run-orphan").state == rs.CONTINUING


# --- host turn leases (the live orphan case) ---------------------------------


def make_host_db(path: Path, rows):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE session_turn_leases (conversation_id TEXT PRIMARY KEY, holder TEXT,"
        " acquired_at REAL, expires_at REAL)"
    )
    con.executemany("INSERT INTO session_turn_leases VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()


def test_host_leases_are_read_and_expiry_is_folded_in(tmp_path):
    db = tmp_path / "state.db"
    make_host_db(
        db,
        [
            ("s-expired", "pid=88437:turn=s-expired:d8f0b1de:platform=desktop",
             NOW - 1000, NOW - 800),
            ("s-open", "pid=999:turn=s-open:aa:platform=desktop", NOW - 10, NOW + 600),
        ],
    )
    leases = {lease["conversation_id"]: lease for lease in rs.host_leases(db_path=db, now=NOW)}
    assert leases["s-expired"]["expired"] is True
    assert leases["s-expired"]["pid"] == 88437
    assert leases["s-open"]["expired"] is False


def test_host_leases_survive_a_missing_or_broken_db(tmp_path):
    assert rs.host_leases(db_path=tmp_path / "nope.db", now=NOW) == []
    broken = tmp_path / "broken.db"
    broken.write_text("not a database", encoding="utf-8")
    assert rs.host_leases(db_path=broken, now=NOW) == []


def test_orphaned_host_leases_are_adopted_but_work_is_not_replayed(tmp_path, store):
    """The 2 live expired leases: adopted, then parked — never silently re-run."""
    db = tmp_path / "state.db"
    make_host_db(
        db,
        [
            ("s-expired", "pid=88437:turn=s-expired:d8f0b1de:platform=desktop",
             NOW - 1000, NOW - 800),
            ("s-open", "pid=999:turn=s-open:aa:platform=desktop", NOW - 10, NOW + 600),
        ],
    )
    # only the dead holder is an orphan; the live one belongs to the host
    alive = lambda pid: pid == 999  # noqa: E731

    adopted = rs.adopt_host_leases(store, db_path=db, now=NOW, alive=alive)

    assert [run.run_id for run in adopted] == ["host-s-expired"]
    assert store.by_session("s-open") is None  # a healthy turn is the host's own
    decision = rs.repair_run(store, "host-s-expired", now=NOW, alive=alive)
    assert decision["action"] == rs.PARKED_UNKNOWN_SIDE_EFFECT
    assert store.get("host-s-expired").state == rs.UNKNOWN_SIDE_EFFECT_STATE
    assert store.continuations("host-s-expired") == []


def test_a_live_host_lease_with_a_valid_holder_is_never_adopted(tmp_path, store):
    """A running turn must not be doubled into a run record (live PID case)."""
    db = tmp_path / "state.db"
    make_host_db(
        db,
        [("s-live", "pid=330649:turn=s-live:aa:platform=desktop", NOW - 5, NOW + 900)],
    )
    adopted = rs.adopt_host_leases(store, db_path=db, now=NOW, alive=lambda pid: True)
    assert adopted == []
    assert store.all_runs() == []


def test_host_lease_adoption_is_idempotent(tmp_path, store):
    db = tmp_path / "state.db"
    make_host_db(
        db,
        [("s-expired", "pid=88437:turn=s-expired:x", NOW - 1000, NOW - 800)],
    )
    first = rs.adopt_host_leases(store, db_path=db, now=NOW, alive=DEAD)
    second = rs.adopt_host_leases(store, db_path=db, now=NOW + 5, alive=DEAD)
    assert len(first) == 1 and second == []
    assert len([r for r in store.all_runs() if r.session_id == "s-expired"]) == 1


def test_bootstrap_opens_the_store_under_hermes_home(tmp_path, monkeypatch):
    # HERMES_HOME only decides the runtime when no stronger override is exported:
    # SKALDIR_HOME (then GODSV_HOME) wins over it in registry.runtime_dir, and a leaked one
    # from the surrounding shell silently moved the store out of this tmp_path.
    monkeypatch.delenv("SKALDIR_HOME", raising=False)
    monkeypatch.delenv("GODSV_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    db = tmp_path / "state.db"
    make_host_db(db, [("s-expired", "pid=88437:turn=s-expired:x", NOW - 100, NOW - 50)])
    store = rs.bootstrap(now=NOW, alive=DEAD)
    try:
        # The rename rule (src/skaldir/registry.py): a fresh HERMES_HOME gets
        # `<hermes>/skaldir`; only a pre-existing `<hermes>/godsv` is still used.
        assert Path(store.path) == tmp_path / "hermes" / "skaldir" / "runs.db"
        issues = rs.check_invariants(store, now=NOW, alive=DEAD)["violations"]
    finally:
        store.close()
    # an isolated HERMES_HOME means the live runs.db is never touched
    assert rs.host_leases(db_path=db, now=NOW)[0]["expired"] is True
    assert isinstance(issues, list)


# --- watchdog ----------------------------------------------------------------


def test_watchdog_pass_once_repairs_and_reports(store):
    expired_lease_run(store)
    seen: list[tuple[str, dict]] = []
    watchdog = rs.RunWatchdog(
        store, owner="wd", interval=0.01, emit=lambda kind, payload: seen.append((kind, payload))
    )

    result = watchdog.pass_once()

    assert result["violations_before"] == 1
    assert result["ok"] is True
    assert watchdog.passes == 1
    assert watchdog.errors == []
    assert seen and seen[0][0] == "run_sweep"
    assert store.get("run-1").state == rs.CONTINUING


def test_watchdog_records_errors_without_dying(store):
    def exploding(*args, **kwargs):
        raise RuntimeError("store unavailable")

    watchdog = rs.RunWatchdog(store, interval=0.01, sweep_fn=exploding)
    result = watchdog.pass_once()

    assert result["ok"] is False
    assert watchdog.passes == 0
    assert watchdog.errors == ["RuntimeError: store unavailable"]


def test_watchdog_thread_ticks_and_stops(store):
    """The thread is exercised on a quiet store: it must not kill itself."""
    store.create_run("s1", run_id="run-live", state=rs.ACTIVE, now=NOW)
    store.grant_lease("run-live", "pid=1:turn=s1", ttl=10_000, now=NOW)
    watchdog = rs.RunWatchdog(store, owner="wd", interval=0.01, claim=False)

    watchdog.start()
    deadline = time.time() + 5.0
    while watchdog.passes < 2 and time.time() < deadline:
        time.sleep(0.02)
    running_mid = watchdog.running
    watchdog.stop()
    watchdog.stop()  # idempotent

    assert running_mid is True
    assert watchdog.passes >= 2
    assert watchdog.errors == []


def test_watchdog_start_is_idempotent(store):
    watchdog = rs.RunWatchdog(store, interval=0.05, claim=False)
    watchdog.start()
    thread = watchdog._thread
    watchdog.start()
    assert watchdog._thread is thread
    watchdog.stop()


def test_two_watchdogs_do_not_double_recover(store):
    """Two supervisors racing on the same store: exactly one claims the work."""
    expired_lease_run(store)
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def run_sweep(owner: str) -> None:
        barrier.wait()
        results.append(rs.sweep(store, owner=owner, now=NOW, alive=DEAD))

    threads = [
        threading.Thread(target=run_sweep, args=(owner,)) for owner in ("wd-a", "wd-b")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    claimed = [row for result in results for row in result["claimed"]]
    assert len(claimed) == 1  # one continuation, one winner
    generations = {c["generation"] for c in store.continuations("run-1")}
    assert generations == {1}
    assert store.get("run-1").recovery_count == 1


# --- describe / status -------------------------------------------------------


def test_describe_is_read_only(store):
    expired_lease_run(store)
    before = store.get("run-1").state
    report = rs.describe(store, now=NOW)
    assert report["ok"] is False
    assert len(report["violations"]) == 1
    assert store.get("run-1").state == before  # describe must not repair
    assert report["runs"][0]["run_id"] == "run-1"


def test_owner_pid_parsing():
    assert rs.owner_pid("pid=88437:turn=s1:d8f0b1de:platform=desktop") == 88437
    assert rs.owner_pid("worker-a") is None
    assert rs.owner_pid("") is None


def test_lease_valid_needs_both_expiry_and_liveness(store):
    run = store.create_run(
        "s1",
        run_id="run-1",
        state=rs.ACTIVE,
        lease_owner="pid=1:x",
        lease_expires_at=NOW + 10,
        now=NOW,
    )
    assert rs.lease_valid(run, now=NOW, alive=ALIVE) is True
    assert rs.lease_valid(run, now=NOW, alive=DEAD) is False
    assert rs.lease_valid(run, now=NOW + 11, alive=ALIVE) is False


def test_update_refuses_unknown_fields_and_states(store):
    store.create_run("s1", run_id="run-1", state=rs.ACTIVE, now=NOW)
    with pytest.raises(ValueError):
        store.update("run-1", nope="x", now=NOW)
    with pytest.raises(ValueError):
        store.update("run-1", state="NOT_A_STATE", now=NOW)
    with pytest.raises(ValueError):
        store.create_run("s1", state="NOT_A_STATE", now=NOW)
    with pytest.raises(ValueError):
        store.create_run("s1", made_up="x", now=NOW)
    with pytest.raises(ValueError):
        store.wait("run-1", reason="", wake_source="user:continue", now=NOW)
    with pytest.raises(ValueError):
        store.wait("run-1", reason="waiting", wake_source="", now=NOW)


def test_heartbeat_only_from_the_lease_owner(store):
    store.create_run("s1", run_id="run-1", state=rs.ACTIVE, now=NOW)
    store.grant_lease("run-1", "pid=1:turn=s1", ttl=60, now=NOW)
    assert store.heartbeat("run-1", "pid=2:turn=s1", now=NOW + 1) is False
    assert store.heartbeat("run-1", "pid=1:turn=s1", now=NOW + 2) is True
    run = store.get("run-1")
    assert run.heartbeat_at == NOW + 2 and run.last_progress_at == NOW + 2


def test_release_lease_clears_ownership_only_once(store):
    store.create_run("s1", run_id="run-1", state=rs.ACTIVE, now=NOW)
    store.grant_lease("run-1", "pid=1:turn=s1", now=NOW)
    assert store.release_lease("run-1", reason="done", now=NOW + 1) is True
    assert store.release_lease("run-1", reason="again", now=NOW + 2) is False
    assert store.get("run-1").lease_owner == ""


def test_audit_trail_is_append_only(store):
    expired_lease_run(store)
    rs.repair_run(store, "run-1", now=NOW, alive=DEAD)
    actions = [entry["action"] for entry in store.audit("run-1")]
    assert "orphan_requeued" in actions
    assert "update" in actions
    assert len(store.audit("run-1")) >= 2


def test_dumps_is_stable_and_json_safe(store):
    payload = {"b": 1, "a": {"ts": NOW}}
    assert rs.dumps(payload) == '{"a": {"ts": 1000000.0}, "b": 1}'
    assert rs.dumps({"path": Path("/tmp/x")}) == '{"path": "/tmp/x"}'
