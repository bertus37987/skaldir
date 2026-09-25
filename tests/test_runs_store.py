"""PERSIST — the durable goal record, operation ledger and budget ledger.

MVP/10 §2 (the record), §2.3 (the sqlite side), §2.4 (the write rules) and §3
R2/R3 (the resume classification). The store is the single writer of
``runs.db``: one file, one transaction per change, an additive migration, and a
criterion that can only flip from evidence.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from skaldir import budget as budget_module
from skaldir import run_supervisor as rs
from skaldir import runs_store as R
from skaldir.events import EventStore

SESSION = "s1"


@pytest.fixture
def store(tmp_path):
    handle = R.RunsStore(tmp_path / "runs.db")
    yield handle
    handle.close()


def _goal(store: R.RunsStore, text: str = "ship the persist layer", **kw):
    kw.setdefault("session_id", SESSION)
    kw.setdefault("criteria", ["cli works", "durability"])
    return store.start_goal(text, **kw)


def _silent(pid):
    """``alive`` stand-in: nothing is alive (a dead holder)."""
    return False


# --------------------------------------------------------------------------- #
# schema, migration, one writer                                                 #
# --------------------------------------------------------------------------- #


def test_the_schema_has_every_table_mvp10_names(store, tmp_path):
    with sqlite3.connect(store.path) as db:
        names = {
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {
        "goals",
        "criteria",
        "criterion_evidence",
        "operations",
        "checkpoints",
        "resume_cursor",
        "budget_ledger",
        "budget_state",
        "budget_reservations",
        "runs_store_meta",
    } <= names
    assert store.schema_version() == R.SCHEMA_VERSION
    assert (tmp_path / "runs.db").is_file()


def test_the_operation_queue_is_in_the_run_supervisors_own_file(store):
    """MVP/10 §2.1: ``operations`` sits next to runs/continuations/run_actions."""
    with sqlite3.connect(store.path) as db:
        names = {
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"runs", "continuations", "run_actions", "run_audit"} <= names
    supervisor = rs.RunStore(store.path)
    try:
        snapshot = rs.describe(supervisor)
    finally:
        supervisor.close()
    assert snapshot["ok"] is True and snapshot["runs"] == []
    assert store.runs_snapshot()["available"] is True


def test_an_older_table_is_extended_additively(tmp_path):
    """A pre-existing runs.db gains what it lacks; nothing is rewritten."""
    path = tmp_path / "runs.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE goals (goal_id TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'WORKING')"
        )
        db.execute(
            "INSERT INTO goals(goal_id, state) VALUES ('old:g1', 'WORKING')"
        )
    handle = R.RunsStore(path)
    try:
        with sqlite3.connect(path) as db:
            columns = {str(row[1]) for row in db.execute("PRAGMA table_info(goals)")}
        # an existing table is extended, never rebuilt: only the additive
        # columns this module declares are added (the CREATE TABLE DDL is a no-op
        # for a table that already exists)
        assert {"history_ref", "blocked_reason", "wake_source"} <= columns
        assert handle.schema_version() == R.SCHEMA_VERSION
        assert handle.goal("old:g1") is not None  # the old row is still readable
    finally:
        handle.close()


def test_the_writer_lease_is_a_compare_and_swap(tmp_path):
    first = R.RunsStore(tmp_path / "runs.db", owner="pid=111:boot=b")
    second = R.RunsStore(tmp_path / "runs.db", owner="pid=222:boot=b")
    try:
        assert first.acquire_writer(alive=_silent)["action"] == "acquired"
        assert first.acquire_writer(alive=_silent)["action"] == "heartbeat"

        refused = second.acquire_writer(alive=lambda pid: True)
        assert refused["acquired"] is False
        assert refused["action"] == "refused"
        assert "one writer only" in refused["reason"]

        taken = second.acquire_writer(alive=_silent)
        assert taken["acquired"] is True and taken["action"] == "takeover"
        assert second.writer()["holder"] == "pid=222:boot=b"

        disabled = first.acquire_writer(alive=_silent, takeover=False)
        assert disabled["acquired"] is False
        assert "takeover is disabled" in disabled["reason"]
    finally:
        first.close()
        second.close()


def test_only_the_holder_releases_the_writer_lease(tmp_path):
    owner = R.RunsStore(tmp_path / "runs.db", owner="pid=1:boot=b")
    other = R.RunsStore(tmp_path / "runs.db", owner="pid=2:boot=b")
    try:
        owner.acquire_writer(alive=_silent)
        assert other.release_writer() is False
        assert other.writer()["held"] is True
        assert owner.release_writer() is True
        assert owner.writer()["held"] is False
    finally:
        owner.close()
        other.close()


def test_a_released_lease_is_acquirable_again_and_again(tmp_path):
    """F1: a release writes ``""`` — the next acquire must not mistake it for
    "no row yet" and lose the compare-and-swap.

    Releasing used to blank the row, so ``not previous`` sent the next acquire
    down the ``ON CONFLICT DO NOTHING`` INSERT: rowcount 0, reported as ``lost``,
    and **every run after the first became a silent no-op** (exit 6, the chat
    waiting forever). Two runs in a row is the case the product really hits.
    """
    store = R.RunsStore(tmp_path / "runs.db")
    try:
        for run_number in (1, 2, 3):
            owner = f"pid={100 + run_number}:boot=run{run_number}"
            started = store.acquire_writer(owner=owner, alive=_silent)
            assert started["acquired"] is True, (run_number, started)
            assert started["action"] == "acquired", (run_number, started)
            assert store.writer()["holder"] == owner
            # the run did its work and gave the store back
            assert store.release_writer(owner=owner) is True
            assert store.writer()["held"] is False
        # ...and the CAS is still a CAS: a live holder is never overwritten
        store.acquire_writer(owner="pid=1:boot=live", alive=_silent)
        rival = store.acquire_writer(owner="pid=2:boot=rival", alive=lambda pid: True)
        assert rival["acquired"] is False and rival["action"] == "refused"
        assert "one writer only" in rival["reason"]
    finally:
        store.close()


# --------------------------------------------------------------------------- #
# the goal record                                                               #
# --------------------------------------------------------------------------- #


def test_a_new_goal_gets_an_id_revision_criteria_and_a_checkpoint(store):
    goal = _goal(store)
    assert goal.goal_id.startswith(f"{SESSION}:g")
    assert goal.revision == 1 and goal.state == "WORKING"
    criteria = store.criteria(goal.goal_id)
    assert [item.criterion_id for item in criteria] == ["c1", "c2"]
    assert [item.status for item in criteria] == ["open", "open"]
    assert store.open_criteria(goal.goal_id) == ["c1", "c2"]
    checkpoint = store.latest_checkpoint(goal.goal_id)
    assert checkpoint is not None and checkpoint["seq"] == 1
    assert store.cursor(goal.goal_id)["last_checkpoint_id"] == checkpoint["checkpoint_id"]


def test_a_decision_never_bumps_the_revision(store):
    goal = _goal(store)
    store.add_decision(goal.goal_id, kind="non_goal", text="no UI work", reason="CLI first")
    assert store.require_goal(goal.goal_id).revision == 1
    assert store.require_goal(goal.goal_id).decisions[0]["kind"] == "non_goal"


def test_a_criteria_edit_bumps_the_revision_and_ages_the_evidence(store):
    goal = _goal(store)
    store.record_criterion_outcome(
        goal.goal_id, "c1", outcome="verified", evidence_refs=["test:tests/test_x.py::t"],
        check_id="pytest", exit_code=0,
    )
    assert store.criterion(goal.goal_id, "c1").status == "verified"
    store.add_criterion(goal.goal_id, "a third criterion", reason="owner added it")
    updated = store.require_goal(goal.goal_id)
    assert updated.revision == 2
    assert updated.revision_log[-1]["new"] == 2
    demoted = store.demote_stale_criteria(goal.goal_id)
    assert [item["criterion_id"] for item in demoted] == ["c1"]
    assert store.criterion(goal.goal_id, "c1").status == "open"
    # demoted by mismatch, never deleted (MVP/10 §2.4 rule 4)
    assert store.evidence(goal.goal_id, "c1")


def test_a_terminal_goal_is_frozen(store):
    goal = _goal(store)
    store.set_goal_state(goal.goal_id, "VERIFIED_COMPLETE")
    assert store.require_goal(goal.goal_id).terminal is True
    with pytest.raises(R.RunsError):
        store.set_goal_state(goal.goal_id, "WORKING")


def test_an_unknown_state_is_reported_as_corrupt_and_never_resumed(store):
    goal = _goal(store)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE goals SET state = 'BOGUS' WHERE goal_id = ?", (goal.goal_id,))
    record = store.require_goal(goal.goal_id)
    assert record.corrupt is True and "BOGUS" in record.corrupt_reason
    payload = store.resume(goal.goal_id)
    assert payload["corrupt"] is True
    assert payload["resumable"] is False
    assert payload["wrote"]["lease"] in ("heartbeat", "takeover")
    assert store.require_goal(goal.goal_id).corrupt is True


# --------------------------------------------------------------------------- #
# criteria: evidence only                                                       #
# --------------------------------------------------------------------------- #


def test_a_criterion_outcome_without_evidence_is_refused(store):
    goal = _goal(store)
    with pytest.raises(R.EvidenceRequired):
        store.record_criterion_outcome(goal.goal_id, "c1", outcome="verified", evidence_refs=[])
    assert store.criterion(goal.goal_id, "c1").status == "open"


def test_a_criterion_outcome_must_be_verified_or_failed(store):
    """There is no API for \"set this criterion verified\" — only an outcome + refs."""
    goal = _goal(store)
    with pytest.raises(R.RunsError):
        store.record_criterion_outcome(
            goal.goal_id, "c1", outcome="open", evidence_refs=["test:x"]
        )
    assert store.criterion(goal.goal_id, "c1").status == "open"


def test_evidence_from_an_older_revision_is_refused_and_nothing_is_written(store):
    goal = _goal(store)
    store.add_criterion(goal.goal_id, "another", reason="bump")
    assert store.require_goal(goal.goal_id).revision == 2
    with pytest.raises(R.StaleEvidence):
        store.record_criterion_outcome(
            goal.goal_id, "c1", outcome="verified", evidence_refs=["test:tests/test_x.py::t"],
            revision=1, check_id="pytest", exit_code=0,
        )
    assert store.criterion(goal.goal_id, "c1").status == "open"
    assert store.evidence(goal.goal_id, "c1") == []


def test_evidence_from_another_workspace_is_refused(store):
    goal = _goal(store, workspace_fingerprint="aaaa")
    with pytest.raises(R.StaleEvidence):
        store.record_criterion_outcome(
            goal.goal_id, "c1", outcome="verified", evidence_refs=["test:x"],
            workspace_fingerprint="bbbb", check_id="pytest", exit_code=0,
        )
    assert store.criterion(goal.goal_id, "c1").status == "open"


def test_evidence_refs_are_typed_and_kept(store):
    goal = _goal(store)
    store.record_criterion_outcome(
        goal.goal_id, "c1",
        outcome="verified",
        evidence_refs=["test:tests/test_runs_store.py::t", "receipt:/tmp/r.json", "events:s1:12"],
        check_id="pytest", cmd="pytest -q", exit_code=0, artifact="report.xml",
    )
    row = store.criterion(goal.goal_id, "c1")
    assert row.status == "verified"
    assert row.verified_by == "pytest" and row.verified_at > 0
    assert row.evidence_revision == store.require_goal(goal.goal_id).revision
    assert [item["kind"] for item in row.evidence_refs] == ["test", "receipt", "events"]
    assert row.fresh(store.require_goal(goal.goal_id)) is True


def test_a_failed_check_records_the_failure_and_keeps_the_criterion_open(store):
    goal = _goal(store)
    store.record_criterion_outcome(
        goal.goal_id, "c2", outcome="failed", evidence_refs=["test:tests/test_x.py::t"],
        check_id="pytest", exit_code=1,
    )
    row = store.criterion(goal.goal_id, "c2")
    assert row.status == "failed" and row.verified_at == 0
    assert row.last_failure  # the reason travels with the record


# --------------------------------------------------------------------------- #
# operations: the double-effect fence                                           #
# --------------------------------------------------------------------------- #


def test_the_idempotency_key_is_a_unique_fence(store):
    goal = _goal(store)
    first, created = store.register_operation(
        goal.goal_id, kind="deploy", target="prod", payload={"rev": 1}
    )
    assert created is True and first.state == "pending"
    expected = R.idempotency_key(goal.goal_id, goal.revision, "deploy", "prod", {"rev": 1})
    assert first.idempotency_key == expected

    again, created_again = store.register_operation(
        goal.goal_id, kind="deploy", target="prod", payload={"rev": 1}
    )
    assert created_again is False and again.op_id == first.op_id and again.duplicate is True
    assert len(store.operations(goal.goal_id)) == 1

    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(store.path) as db:
            db.execute(
                "INSERT INTO operations(op_id, goal_id, idempotency_key)"
                " VALUES ('op-x', ?, ?)",
                (goal.goal_id, first.idempotency_key),
            )


def test_a_different_payload_is_a_different_operation(store):
    goal = _goal(store)
    one, _ = store.register_operation(
        goal.goal_id, kind="deploy", target="prod", payload={"rev": 1}
    )
    two, _ = store.register_operation(
        goal.goal_id, kind="deploy", target="prod", payload={"rev": 2}
    )
    assert one.op_id != two.op_id
    assert len(store.operations(goal.goal_id)) == 2


def test_the_lifecycle_pending_in_flight_done_and_a_receipt(store):
    goal = _goal(store)
    operation, _ = store.register_operation(goal.goal_id, kind="publish", target="reg", payload={})
    assert store.operation(operation.op_id).state == "pending"
    assert store.begin_operation(operation.op_id) is True
    assert store.operation(operation.op_id).state == "in_flight"
    assert store.operation(operation.op_id).attempt == 1
    result = store.complete_operation(operation.op_id, result_ref="receipt:deploy", note="ok")
    assert result["state"] == "done" and result["side_effect_state"] == "known"
    assert Path(result["receipt"]).is_file()
    receipt = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
    assert receipt["op_id"] == operation.op_id and receipt["idempotency_key"]


def test_classification_idle_repeat_resolved_unknown(store, tmp_path):
    goal = _goal(store)
    idle, _ = store.register_operation(goal.goal_id, kind="draft", target="readme", payload={})
    repeatable, _ = store.register_operation(
        goal.goal_id, kind="publish", target="reg", payload={}, idempotent=True
    )
    store.begin_operation(repeatable.op_id)
    resolved, _ = store.register_operation(goal.goal_id, kind="deploy", target="prod", payload={})
    store.begin_operation(resolved.op_id)
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("done\n", encoding="utf-8")
    store.complete_operation(resolved.op_id, result_ref=str(artifact))
    unknown, _ = store.register_operation(goal.goal_id, kind="send_email", target="a@b", payload={})
    store.begin_operation(unknown.op_id)

    assert store.classify_operation(store.operation(idle.op_id))[0] == R.DISPOSITION_IDLE
    assert store.classify_operation(store.operation(repeatable.op_id))[0] == R.DISPOSITION_REPEAT
    assert store.classify_operation(store.operation(resolved.op_id))[0] == R.DISPOSITION_RESOLVED
    assert store.classify_operation(store.operation(unknown.op_id))[0] == R.DISPOSITION_UNKNOWN


def test_resume_parks_an_unprovable_operation_and_never_retries_it(store):
    goal = _goal(store)
    operation, _ = store.register_operation(
        goal.goal_id, kind="send_email", target="owner@example", payload={"subject": "x"}
    )
    store.begin_operation(operation.op_id)

    first = store.resume(goal.goal_id)
    parked = store.operation(operation.op_id)
    assert parked.state == "unknown" and parked.side_effect_state == "unknown"
    assert parked.wake_source == R.OPERATOR_WAKE_SOURCE
    assert parked.attempt == 1  # the attempt is recorded, not repeated
    assert first["resumable"] is False
    assert first["parked_operations"][0]["op_id"] == operation.op_id

    store.resume(goal.goal_id)
    again = store.operation(operation.op_id)
    assert again.attempt == 1, "a parked operation must never be retried automatically"
    assert again.state == "unknown"
    assert again.disposition == R.DISPOSITION_UNKNOWN


def test_resume_resolves_an_operation_whose_result_provably_exists(store, tmp_path):
    goal = _goal(store)
    operation, _ = store.register_operation(goal.goal_id, kind="deploy", target="prod", payload={})
    store.begin_operation(operation.op_id)
    artifact = tmp_path / "deployed.txt"
    artifact.write_text("landed\n", encoding="utf-8")
    with store.transaction() as db:  # the effect landed, the commit did not
        db.execute(
            "UPDATE operations SET result_ref = ? WHERE op_id = ?", (str(artifact), operation.op_id)
        )
    store.resume(goal.goal_id)
    row = store.operation(operation.op_id)
    assert row.state == "done" and row.side_effect_state == "known"


def test_resume_does_not_double_dispatch_a_repeatable_operation(store):
    goal = _goal(store)
    operation, _ = store.register_operation(
        goal.goal_id, kind="publish", target="reg", payload={}, idempotent=True
    )
    store.begin_operation(operation.op_id)
    payload = store.resume(goal.goal_id)
    row = store.operation(operation.op_id)
    assert row.state == "pending" and row.attempt == 1, "the caller repeats with the same key"
    assert payload["wrote"]["repeats"] == [operation.op_id]
    assert len(store.operations(goal.goal_id)) == 1


def test_parking_supersedes_the_open_continuation_of_the_run(store):
    goal = _goal(store)
    supervisor = rs.RunStore(store.path)
    try:
        run = supervisor.create_run(SESSION, run_id="run-1", state=rs.ACTIVE)
        generation, created = supervisor.enqueue_continuation(run.run_id, reason="orphan")
        assert created is True
        operation, _ = store.register_operation(
            goal.goal_id, kind="http_write", target="api", payload={}, run_id=run.run_id
        )
        store.begin_operation(operation.op_id)
        store.resume(goal.goal_id)
        continuation = supervisor.continuation_for(run.run_id, generation)
    finally:
        supervisor.close()
    assert continuation is not None
    assert continuation["status"] == rs.CONTINUATION_SUPERSEDED, (
        "a claim left behind by a dead owner must not execute behind the operator"
    )


def test_an_operator_can_confirm_a_parked_effect(store):
    goal = _goal(store)
    operation, _ = store.register_operation(goal.goal_id, kind="email", target="owner", payload={})
    store.begin_operation(operation.op_id)
    store.park_operation(operation.op_id, reason="crash in the window")
    with pytest.raises(R.RunsError):
        store.resolve_operation(operation.op_id, result_ref="receipt:x")
    resolved = store.resolve_operation(
        operation.op_id, result_ref="receipt:x", operator="owner", note="the mail went out"
    )
    assert resolved["state"] == "done"
    row = store.operation(operation.op_id)
    assert row.state == "done" and row.wake_source == "" and row.side_effect_state == "known"


# --------------------------------------------------------------------------- #
# checkpoints                                                                   #
# --------------------------------------------------------------------------- #


def test_a_checkpoint_is_self_sufficient_and_chained(store):
    goal = _goal(store)
    first = store.latest_checkpoint(goal.goal_id)
    second = store.write_checkpoint(
        goal.goal_id, next_action="write the durability test", last_event_seq=17
    )
    assert second["seq"] == 2 and second["prev"] == first["sha256"]
    assert second["size_bytes"] <= 4096 and second["oversize"] is False
    path = Path(second["path"])
    assert path.is_file() and R.sha256_file(path) == second["sha256"]

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["goal_id"] == goal.goal_id
    assert document["cursor"]["next_action"] == "write the durability test"
    assert document["cursor"]["open_criteria"] == ["c1", "c2"]
    assert set(document["budget"]) == set(budget_module.DEFAULT_LIMITS)
    assert document["state"] == "WORKING" and document["revision"] == 1

    cursor = store.cursor(goal.goal_id)
    assert cursor["last_checkpoint_id"] == second["checkpoint_id"]
    assert cursor["last_event_seq"] == 17


def test_a_tampered_checkpoint_payload_is_detected(store):
    goal = _goal(store)
    checkpoint = store.latest_checkpoint(goal.goal_id)
    Path(checkpoint["path"]).write_text('{"tampered": true}\n', encoding="utf-8")
    read = store.checkpoint_payload(checkpoint)
    assert read["readable"] is True and read["sha256_ok"] is False
    payload = store.resume(goal.goal_id)
    assert payload["checkpoint"]["sha256_ok"] is False
    assert any("sha256" in text for text in payload["warnings"])


def test_the_event_cursor_reports_uncommitted_events(store):
    goal = _goal(store)
    events = EventStore(store.events_db())
    try:
        for step in range(3):
            events.append(SESSION, step, "tool_call", {"step": step})
    finally:
        events.close()
    assert store.max_event_seq() == 3
    store.write_checkpoint(goal.goal_id, last_event_seq=1)
    detected = store.detect_incomplete(goal.goal_id)
    assert detected["events"] == {"max_seq": 3, "cursor": 1, "uncommitted": 2}


# --------------------------------------------------------------------------- #
# ownership                                                                     #
# --------------------------------------------------------------------------- #


def test_resume_reattaches_to_a_live_owner_and_writes_nothing(store):
    goal = _goal(store, owner="pid=999:boot=b", now=time.time() + 60)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "UPDATE goals SET lease_owner = 'pid=999:boot=b', lease_expires_at = ?"
            " WHERE goal_id = ?",
            (time.time() + 600, goal.goal_id),
        )
    before = store.require_goal(goal.goal_id)
    payload = store.resume(goal.goal_id, owner="pid=222:boot=b", alive=lambda pid: True)
    assert payload["owners"]["action"] == "re-attached"
    assert payload["owners"]["owns"] is False
    after = store.require_goal(goal.goal_id)
    assert after.lease_owner == before.lease_owner
    assert after.heartbeat_at == before.heartbeat_at
    assert payload["wrote"]["lease"] == "re-attached"


def test_resume_takes_over_a_dead_owner_through_the_cas(store):
    goal = _goal(store)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "UPDATE goals SET lease_owner = 'pid=999:boot=b', lease_expires_at = ?"
            " WHERE goal_id = ?",
            (time.time() + 600, goal.goal_id),
        )
    payload = store.resume(goal.goal_id, owner="pid=222:boot=b", alive=_silent)
    assert payload["owners"]["action"] == "takeover"
    assert payload["owners"]["owns"] is True
    assert store.require_goal(goal.goal_id).lease_owner == "pid=222:boot=b"


def test_resume_without_takeover_reports_instead_of_acting(store):
    goal = _goal(store)
    payload = store.resume(goal.goal_id, owner="pid=222:boot=b", alive=_silent, takeover=False)
    assert payload["owners"]["action"] == "refused"
    assert payload["owners"]["owns"] is False


# --------------------------------------------------------------------------- #
# the budget ledger (MVP/10 gap 5: it had no load path)                         #
# --------------------------------------------------------------------------- #


def test_budget_reserve_spend_release_keeps_the_invariant(store):
    goal = _goal(store, limits={"main_agent": 10_000, "workers": 1_000})
    ledger = store.ledger(goal.goal_id)
    first = ledger.reserve("main_agent", 4_000)
    ledger.spend("main_agent", 1_000, reservation=first["reservation_id"], note="turn 1")
    second = ledger.reserve("main_agent", 2_000)
    state = ledger.state()["main_agent"]
    assert (state["spent"], state["reserved"], state["remaining"]) == (1_000, 5_000, 4_000)
    assert state["spent"] + state["reserved"] <= state["limit"]
    assert ledger.release(second["reservation_id"]) is True
    assert ledger.state()["main_agent"]["reserved"] == 3_000
    assert ledger.release("res-nope") is False


def test_budget_refuses_over_commitment_and_reservation_less_spend(store):
    goal = _goal(store, limits={"main_agent": 1_000})
    ledger = store.ledger(goal.goal_id)
    with pytest.raises(budget_module.BudgetExceeded):
        ledger.reserve("main_agent", 2_000)
    with pytest.raises(budget_module.BudgetExceeded):
        ledger.spend("main_agent", 500)  # over the small-spend floor, no reservation
    with pytest.raises(budget_module.BudgetExceeded):
        ledger.spend("main_agent", 10, reservation="res-unknown")
    reservation = ledger.reserve("main_agent", 100)
    with pytest.raises(budget_module.BudgetExceeded):
        ledger.spend("main_agent", 200, reservation=reservation["reservation_id"])
    assert ledger.state()["main_agent"]["spent"] == 0


def test_budget_state_survives_a_restart(tmp_path):
    goal_id = None
    with R.RunsStore(tmp_path / "runs.db") as store:
        goal = _goal(store, limits={"main_agent": 20_000})
        goal_id = goal.goal_id
        ledger = store.ledger(goal.goal_id)
        reservation = ledger.reserve("main_agent", 5_000)
        ledger.spend("main_agent", 1_200, reservation=reservation["reservation_id"])
    with R.RunsStore(tmp_path / "runs.db") as reopened:
        state = reopened.budget_state(goal_id)["main_agent"]
        assert state["spent"] == 1_200 and state["reserved"] == 3_800
        assert state["remaining"] == 20_000 - 1_200 - 3_800


def test_the_budget_load_path_replays_the_ledger_into_a_real_account(tmp_path):
    """The gap MVP/10 §1.3 names: after a restart the account started at zero."""
    with R.RunsStore(tmp_path / "runs.db") as store:
        goal = _goal(store, limits={"main_agent": 20_000})
        ledger = store.ledger(goal.goal_id)
        first = ledger.reserve("main_agent", 5_000)
        ledger.spend("main_agent", 1_200, reservation=first["reservation_id"], note="turn 1")
        second = ledger.reserve("main_agent", 8_000)
        ledger.spend("main_agent", 3_000, reservation=second["reservation_id"], note="turn 2")
    with R.RunsStore(tmp_path / "runs.db") as reopened:
        account = budget_module.BudgetAccount(
            goal_id=goal.goal_id, limits={"main_agent": 20_000}
        )
        assert account.spent("main_agent") == 0  # the in-memory account starts empty
        report = reopened.restore_budget_account(account, goal.goal_id)
        assert report["restored"] is True, report
        assert report["matches"]["main_agent"] is True
        assert account.spent("main_agent") == 4_200
        assert account.reserved("main_agent") == 8_800
        assert account.remaining("main_agent") == 20_000 - 4_200 - 8_800


def test_a_replay_the_account_refuses_is_reported_not_pretended(tmp_path):
    with R.RunsStore(tmp_path / "runs.db") as store:
        goal = _goal(store, limits={"main_agent": 20_000})
        ledger = store.ledger(goal.goal_id)
        ledger.reserve("main_agent", 5_000)
        account = budget_module.BudgetAccount(goal_id=goal.goal_id, limits={"main_agent": 1_000})
        report = store.restore_budget_account(account, goal.goal_id)
    assert report["restored"] is False
    assert "BudgetExceeded" in report["reason"]


# --------------------------------------------------------------------------- #
# status / detect                                                               #
# --------------------------------------------------------------------------- #


def test_status_reports_counts_parked_operations_and_the_writer(store):
    goal = _goal(store)
    operation, _ = store.register_operation(goal.goal_id, kind="email", target="x", payload={})
    store.begin_operation(operation.op_id)
    store.resume(goal.goal_id)
    snapshot = store.status()
    assert snapshot["store"] == store.path and snapshot["exists"] is True
    assert snapshot["counts"]["goals"] == 1 and snapshot["counts"]["open_goals"] == 1
    assert snapshot["counts"]["parked_operations"] == 1
    assert snapshot["operations"]["parked"][0]["op_id"] == operation.op_id
    assert snapshot["writer"]["holder"]
    assert snapshot["goals"][0]["goal_id"] == goal.goal_id


def test_detect_reports_stale_evidence_and_the_run_invariant(store):
    goal = _goal(store)
    store.record_criterion_outcome(
        goal.goal_id, "c1", outcome="verified", evidence_refs=["test:tests/test_x.py::t"],
        check_id="pytest", exit_code=0,
    )
    store.add_criterion(goal.goal_id, "extra", reason="bump the revision")
    detected = store.detect_incomplete(goal.goal_id)
    assert detected["stale_evidence"][0]["criterion_id"] == "c1"
    assert detected["resumable"] is True
    assert detected["runs"]["available"] is True


def test_adopting_host_leases_needs_no_host_db(store):
    goal = _goal(store)
    detected = store.detect_incomplete(goal.goal_id, adopt_host=True)
    assert detected["host_leases_adopted"] == []


def test_goal_states_match_the_completion_vocabulary():
    """One vocabulary, two readers: the mirror may not drift from completion."""
    from skaldir.completion import STATES, TERMINAL_STATES

    assert tuple(R.GOAL_STATES) == tuple(STATES)
    assert tuple(R.TERMINAL_GOAL_STATES) == tuple(TERMINAL_STATES)
