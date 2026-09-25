"""The durable operator channel: ``run_messages`` in ``runs.db`` (`/steer` and `/queue`).

One table, one transaction per change, and a drain protocol that lives in **one** place so
"a steer is urgent, a queued message is one per turn" cannot drift between callers. What this
file pins:

* a message is a **row** — post it, close the process, open the store again, and it is still
  there, in the order it arrived;
* every refusal has a **name** (``no-run``, ``run-finished``, ``empty-text``, ``queue-full``,
  ``queue-disabled``, ``unknown-kind``) — a message is never dropped in silence;
* the **drain rule**: all pending steers plus exactly one queued message per turn boundary,
  each stamped with the turn that consumed it;
* ``messages_summary`` counts what is pending, what was consumed and when.

Nothing here needs a model or a run: the store is the contract, and the run only reads it.
"""

from __future__ import annotations

import pytest

from skaldir import runs_store as R

SESSION = "s-chat"


@pytest.fixture
def store(tmp_path):
    handle = R.RunsStore(tmp_path / "runs.db")
    yield handle
    handle.close()


def _goal(store: R.RunsStore, text: str = "make the tree clean", session: str = SESSION):
    return store.start_goal(text, session_id=session, criteria=["cli works"])


# --------------------------------------------------------------------------- #
# post: durable, ordered, or refused by name                                    #
# --------------------------------------------------------------------------- #


def test_a_message_is_a_row_that_survives_the_process(store, tmp_path):
    goal = _goal(store)
    first = store.post_message(goal.goal_id, "work the failing check next", origin="chat:s1")
    second = store.post_message(goal.goal_id, "then write the test", kind="queue",
                                origin="chat:s1")
    assert first.message_id < second.message_id, "ordering is the autoincrement id"
    assert first.kind == "steer" and second.kind == "queue"
    assert first.state == "pending" and first.consumed_turn == -1
    assert first.origin == "chat:s1"
    store.close()

    again = R.RunsStore(tmp_path / "runs.db")
    try:
        rows = again.messages(goal.goal_id)
        assert [row.text for row in rows] == ["work the failing check next",
                                              "then write the test"]
        assert [row.message_id for row in rows] == [first.message_id, second.message_id]
    finally:
        again.close()


@pytest.mark.parametrize(
    ("kind", "text", "goal_id", "reason"),
    [
        ("steer", "hello", "", R.REFUSAL_NO_RUN),
        ("steer", "hello", "g-nope", R.REFUSAL_NO_RUN),
        ("steer", "   ", "goal", R.REFUSAL_EMPTY),
        ("shout", "hello", "goal", R.REFUSAL_UNKNOWN_KIND),
    ],
)
def test_a_refusal_has_a_name(store, kind, text, goal_id, reason):
    goal = _goal(store)
    target = goal.goal_id if goal_id == "goal" else goal_id
    with pytest.raises(R.MessageRefused) as caught:
        store.post_message(target, text, kind=kind)
    assert caught.value.reason == reason
    assert store.messages(goal.goal_id) == [], "a refused message is not a pending row"


def test_a_finished_run_takes_no_messages(store):
    goal = _goal(store)
    store.set_goal_state(goal.goal_id, "VERIFIED_COMPLETE")
    with pytest.raises(R.MessageRefused) as caught:
        store.post_message(goal.goal_id, "one more thing")
    assert caught.value.reason == R.REFUSAL_RUN_FINISHED
    assert caught.value.details.get("state") == "VERIFIED_COMPLETE"


def test_the_queue_cap_is_a_named_refusal_not_a_silent_drop(store):
    goal = _goal(store)
    store.post_message(goal.goal_id, "first", kind="queue", cap=1)
    with pytest.raises(R.MessageRefused) as caught:
        store.post_message(goal.goal_id, "second", kind="queue", cap=1)
    assert caught.value.reason == R.REFUSAL_QUEUE_FULL
    assert caught.value.details == {"goal_id": goal.goal_id, "pending": 1, "cap": 1}
    assert [row.text for row in store.messages(goal.goal_id)] == ["first"]


def test_a_disabled_channel_says_so(store):
    goal = _goal(store)
    store.set_messages_enabled(False)
    assert store.messages_enabled() is False
    with pytest.raises(R.MessageRefused) as caught:
        store.post_message(goal.goal_id, "hello")
    assert caught.value.reason == R.REFUSAL_QUEUE_DISABLED
    store.set_messages_enabled(True)
    assert store.post_message(goal.goal_id, "hello").state == "pending"


def test_a_recorded_refusal_is_written_down_and_never_drained(store):
    goal = _goal(store)
    row = store.record_refusal(goal.goal_id, "hello", origin="chat:s1",
                               reason=R.REFUSAL_NO_RUN)
    assert row.state == "refused" and row.refused_reason == R.REFUSAL_NO_RUN
    assert store.pending_messages(goal.goal_id) == []
    assert store.consume_messages(goal.goal_id, turn_index=1, turn_name="t1-work") == []
    assert [item.state for item in store.messages(goal.goal_id)] == ["refused"]


# --------------------------------------------------------------------------- #
# drain: all steers, then exactly one queued message                            #
# --------------------------------------------------------------------------- #


def test_a_turn_boundary_takes_all_the_steers_and_one_queued_message(store):
    goal = _goal(store)
    store.post_message(goal.goal_id, "steer one")
    store.post_message(goal.goal_id, "queued one", kind="queue")
    store.post_message(goal.goal_id, "steer two")
    store.post_message(goal.goal_id, "queued two", kind="queue")

    taken = store.consume_messages(goal.goal_id, turn_index=2, turn_name="t2-work")
    # the *selection* is the rule: every steer, plus the oldest queued message — and the list
    # comes back in the store's own order (the autoincrement id), never a re-sorted one
    assert {(row.kind, row.text) for row in taken} == {
        ("steer", "steer one"),
        ("steer", "steer two"),
        ("queue", "queued one"),
    }, "every steer is urgent; exactly one queued message rides along, oldest first"
    assert [row.message_id for row in taken] == sorted(row.message_id for row in taken)
    assert all(row.state == "consumed" for row in taken)
    assert all(row.consumed_turn == 2 and row.turn_name == "t2-work" for row in taken)

    # the queue of one more takes the *next* boundary, and nothing is reordered or skipped
    again = store.consume_messages(goal.goal_id, turn_index=3, turn_name="t3-work")
    assert [(row.kind, row.text) for row in again] == [("queue", "queued two")]
    assert again[0].consumed_turn == 3
    assert store.consume_messages(goal.goal_id, turn_index=4, turn_name="t4-work") == []


def test_a_boundary_with_no_steers_still_takes_only_one_queued_message(store):
    goal = _goal(store)
    for index in range(3):
        store.post_message(goal.goal_id, f"q{index}", kind="queue")
    for turn in (1, 2, 3):
        taken = store.consume_messages(goal.goal_id, turn_index=turn,
                                       turn_name=f"t{turn}-work")
        assert len(taken) == 1, f"turn {turn} took exactly one"
    assert store.pending_messages(goal.goal_id) == []


def test_the_consumed_turn_is_durable(store, tmp_path):
    goal = _goal(store)
    store.post_message(goal.goal_id, "steer one")
    store.consume_messages(goal.goal_id, turn_index=1, turn_name="t1-work")
    store.close()
    again = R.RunsStore(tmp_path / "runs.db")
    try:
        row = again.messages(goal.goal_id, state=R.MESSAGE_CONSUMED)[0]
        assert (row.consumed_turn, row.turn_name) == (1, "t1-work")
        assert row.consumed_at is not None
    finally:
        again.close()


# --------------------------------------------------------------------------- #
# the summary: honest counts, never an estimate                                 #
# --------------------------------------------------------------------------- #


def test_the_summary_counts_what_is_pending_and_where_the_last_one_went(store):
    goal = _goal(store)
    store.post_message(goal.goal_id, "steer one")
    store.post_message(goal.goal_id, "queued one", kind="queue")
    store.post_message(goal.goal_id, "queued two", kind="queue")
    store.record_refusal(goal.goal_id, "nope", reason=R.REFUSAL_NO_RUN)
    store.consume_messages(goal.goal_id, turn_index=1, turn_name="t1-work")

    summary = store.messages_summary(goal.goal_id)
    assert summary["goal_id"] == goal.goal_id
    assert summary["enabled"] is True
    assert summary["counts"] == {
        "steer:consumed": 1,
        "queue:pending": 1,
        "queue:consumed": 1,
        "steer:refused": 1,
    }
    assert summary["pending_count"] == 1
    assert [row["text"] for row in summary["pending"]] == ["queued two"]
    assert summary["last_consumed"]["turn_name"] == "t1-work"
    assert summary["last_consumed"]["text"] == "queued one", (
        "within one turn the store reports the last row it took (highest id)"
    )


def test_an_empty_channel_summarises_as_empty(store):
    goal = _goal(store)
    summary = store.messages_summary(goal.goal_id)
    assert summary["counts"] == {} and summary["pending_count"] == 0
    assert summary["last_consumed"] is None and summary["enabled"] is True


# --------------------------------------------------------------------------- #
# for the record: the store accepts an older file without the table             #
# --------------------------------------------------------------------------- #


def test_the_channel_is_created_by_the_additive_ddl_path(store):
    import sqlite3

    with sqlite3.connect(store.path) as db:
        names = {str(row[0]) for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "run_messages" in names
