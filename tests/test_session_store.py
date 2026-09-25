"""MVP/40 — the durable message store and the four invariants it enforces.

Every test here is about a *promise* the store makes to a resumed conversation:

* a message id is unique and a per-session sequence is monotonic and gapless,
* two messages of the same role in a row are refused, in code **and** by the schema,
* a past message cannot be rewritten or deleted (append-only),
* a resumed history is the same history — and the comparison is by hash, not by trust.

The schema-level half is proved by writing to the file with a **plain sqlite3
connection**, i.e. the way a hand-written ``UPDATE`` would: those statements must abort.
A trigger that was never exercised is not a guard.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from skaldir.session_store import (
    PURGE_MODES,
    RETENTION_POLICY,
    ROLES,
    SCHEMA_VERSION,
    TOKEN_METHOD,
    CorruptSessionStore,
    DuplicateMessage,
    ImmutableMessageError,
    RoleAlternationError,
    SessionError,
    SessionStore,
    UnknownRole,
    UnknownSession,
    WriterConflict,
    canonical_message_line,
    content_hash,
    estimate_tokens,
    history_hash,
    new_message_id,
    session_store_path,
)

CONVERSATION: tuple[tuple[str, str], ...] = (
    ("user", "How does the durable session history work?"),
    ("assistant", "It is an append-only message log in one sqlite file."),
    ("user", "What stops two user turns in a row?"),
    ("assistant", "A code check plus a trigger on the messages table."),
    ("user", "Can I resume it after a restart?"),
    ("assistant", "Yes — resume returns the history and its sha256."),
)


@pytest.fixture
def store(tmp_path):
    instance = SessionStore(tmp_path / "sessions.db")
    yield instance
    instance.close()


def _raw(path) -> sqlite3.Connection:
    """A second, *plain* connection — the way an out-of-band write would arrive."""
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    return connection


def _fill(instance: SessionStore, session_id: str = "", **kwargs) -> str:
    target = str(session_id or "")
    if not target:
        target = instance.create_session("conversation", **kwargs).session_id
    for role, text in CONVERSATION:
        instance.append(target, role, text)
    return target


# --------------------------------------------------------------------------- #
# the vocabulary and the pure helpers                                          #
# --------------------------------------------------------------------------- #


def test_roles_are_the_four_the_store_accepts():
    assert ROLES == ("system", "user", "assistant", "tool")


def test_only_the_four_roles_are_accepted(store):
    session = store.create_session("roles")
    store.append(session.session_id, "user", "hello")
    for bad in ("human", "AI", "USER ", ""):
        with pytest.raises(UnknownRole):
            store.append(session.session_id, bad, "x")


def test_token_estimates_are_labelled_and_bounded():
    """A count is an estimate and says which method produced it (never a tokenizer)."""
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("a" * 8) == 2
    assert TOKEN_METHOD == "chars/4"


def test_message_ids_are_unique_by_construction():
    ids = {new_message_id("s-abc", seq) for seq in range(1, 400)}
    assert len(ids) == 399
    assert all(item.startswith("m-sabc-") for item in ids)


def test_history_hash_is_deterministic_and_content_sensitive():
    rows = [
        {"seq": 1, "message_id": "m1", "role": "user", "content": "a", "created": 1.0,
         "token_estimate": 1, "kind": "chat"},
        {"seq": 2, "message_id": "m2", "role": "assistant", "content": "b", "created": 2.0,
         "token_estimate": 1, "kind": "chat"},
    ]
    assert history_hash(rows) == history_hash([dict(row) for row in rows])
    assert history_hash(rows) == history_hash(rows)
    changed = [dict(rows[0]), dict(rows[1])]
    changed[1]["content"] = "c"
    assert history_hash(changed) != history_hash(rows)
    order_swapped = [dict(rows[1]), dict(rows[0])]
    assert history_hash(order_swapped) != history_hash(rows)
    assert canonical_message_line(rows[0]) == canonical_message_line(dict(rows[0]))


def test_content_hash_is_the_sha256_of_the_body():
    assert content_hash("hello") == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )
    assert content_hash("hello") != content_hash("hello ")


def test_the_store_path_sits_next_to_the_other_stores(tmp_path):
    assert session_store_path(tmp_path).name == "sessions.db"
    assert session_store_path(tmp_path).parent == tmp_path


# --------------------------------------------------------------------------- #
# ids, roles, sequence                                                         #
# --------------------------------------------------------------------------- #


def test_appended_messages_carry_ids_roles_times_and_estimates(store):
    session = _fill(store)
    messages = store.messages(session)
    assert [item.seq for item in messages] == [1, 2, 3, 4, 5, 6]
    assert [item.role for item in messages] == [role for role, _ in CONVERSATION]
    assert len({item.message_id for item in messages}) == 6
    assert all(item.created > 0 for item in messages)
    assert all(item.token_estimate >= 1 and item.token_method == TOKEN_METHOD for item in messages)
    assert all(item.content_sha256 == content_hash(item.content) for item in messages)
    # the chain is explicit: each message names the previous one
    assert [item.prev_message_id for item in messages[1:]] == [
        item.message_id for item in messages[:-1]
    ]


def test_message_ids_are_globally_unique_across_sessions(store):
    first = _fill(store)
    second = _fill(store)
    ids = {item.message_id for item in store.messages(first)}
    ids |= {item.message_id for item in store.messages(second)}
    assert len(ids) == 12


def test_the_session_head_tracks_the_last_message_and_the_token_total(store):
    session = _fill(store)
    record = store.require_session(session)
    messages = store.messages(session)
    assert record.message_count == len(messages)
    assert record.last_seq == messages[-1].seq
    assert record.head_message_id == messages[-1].message_id
    assert record.head_role == "assistant"
    assert record.token_total == sum(item.token_estimate for item in messages)
    assert record.next_role == "user"


def test_the_sequence_trigger_rejects_a_hole_or_a_regression(store, tmp_path):
    session = _fill(store)
    connection = _raw(tmp_path / "sessions.db")
    try:
        for seq in (9, 0, 3):
            with pytest.raises(sqlite3.DatabaseError) as failure:
                connection.execute(
                    "INSERT INTO messages(message_id, session_id, seq, role, content)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (f"hand-{seq}", session, seq, "user", "x"),
                )
            assert "previous maximum + 1" in str(failure.value)
    finally:
        connection.close()
    assert [item.seq for item in store.messages(session)] == [1, 2, 3, 4, 5, 6]


def test_a_reused_message_id_is_refused(store):
    session = _fill(store)
    first = store.messages(session)[0]
    with pytest.raises(DuplicateMessage):
        store.append(session, "user", "collision", message_id=first.message_id)


# --------------------------------------------------------------------------- #
# invariant 1 — strict role alternation                                        #
# --------------------------------------------------------------------------- #


def test_two_user_turns_in_a_row_are_refused_with_a_clear_error(store):
    session = store.create_session("two user turns")
    store.append(session.session_id, "user", "first")
    with pytest.raises(RoleAlternationError) as failure:
        store.append(session.session_id, "user", "second")
    message = str(failure.value)
    assert "two user messages in a row" in message
    assert "message 1" in message
    assert "assistant" in message  # the roles it may use instead are named
    assert store.require_session(session.session_id).message_count == 1


def test_a_refused_turn_writes_nothing_and_is_recorded_in_the_audit(store):
    session = store.create_session("refusal")
    store.append(session.session_id, "user", "one")
    with pytest.raises(RoleAlternationError):
        store.append(session.session_id, "user", "again")
    assert len(store.messages(session.session_id)) == 1
    kinds = [event["kind"] for event in store.events(session.session_id)]
    assert "refused_alternation" in kinds
    refusal = next(
        event for event in store.events(session.session_id)
        if event["kind"] == "refused_alternation"
    )
    assert refusal["role"] == "user"
    assert "two user messages in a row" in refusal["detail"]


def test_the_alternation_trigger_refuses_a_hand_written_same_role_row(store, tmp_path):
    session = _fill(store, session_id="")  # head is assistant
    connection = _raw(tmp_path / "sessions.db")
    try:
        with pytest.raises(sqlite3.DatabaseError) as failure:
            connection.execute(
                "INSERT INTO messages(message_id, session_id, seq, role, content)"
                " VALUES ('hand-written', ?, 7, 'assistant', 'smuggled')",
                (session,),
            )
        assert "roles must alternate" in str(failure.value)
    finally:
        connection.close()
    assert len(store.messages(session)) == 6


def test_assistant_tool_and_tool_assistant_alternate_correctly(store):
    session = store.create_session("tool turn")
    store.append(session.session_id, "user", "read the file")
    store.append(session.session_id, "assistant", "calling the reader")
    store.append(session.session_id, "tool", '{"ok": true}', kind="tool_result")
    # a second tool result in a row is the refusal: parallel results are one message
    with pytest.raises(RoleAlternationError) as failure:
        store.append(session.session_id, "tool", "a second result", kind="tool_result")
    assert "two tool messages in a row" in str(failure.value)
    store.append(session.session_id, "assistant", "the file says hello")
    assert [item.role for item in store.messages(session.session_id)] == [
        "user", "assistant", "tool", "assistant"
    ]


def test_a_system_message_opens_a_conversation_but_cannot_repeat(store):
    session = store.create_session("with system")
    store.append(session.session_id, "system", "you are Skaldir")
    store.append(session.session_id, "user", "hello")
    with pytest.raises(RoleAlternationError):
        store.append(session.session_id, "user", "hello again")


# --------------------------------------------------------------------------- #
# invariant 2/3 — immutability and append-only                                 #
# --------------------------------------------------------------------------- #


def test_a_past_message_cannot_be_updated_even_by_a_hand_written_statement(store, tmp_path):
    session = _fill(store)
    target = store.messages(session)[0]
    connection = _raw(tmp_path / "sessions.db")
    try:
        with pytest.raises(sqlite3.DatabaseError) as failure:
            connection.execute(
                "UPDATE messages SET content = 'rewritten' WHERE message_id = ?",
                (target.message_id,),
            )
        assert "append-only" in str(failure.value)
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("UPDATE messages SET role = 'user'")
    finally:
        connection.close()
    assert store.message(target.message_id).content == target.content


def test_a_message_cannot_be_deleted_without_the_purge_token(store, tmp_path):
    session = _fill(store)
    target = store.messages(session)[0]
    connection = _raw(tmp_path / "sessions.db")
    try:
        with pytest.raises(sqlite3.DatabaseError) as failure:
            connection.execute(
                "DELETE FROM messages WHERE message_id = ?", (target.message_id,)
            )
        assert "purge" in str(failure.value)
    finally:
        connection.close()
    assert len(store.messages(session)) == 6


def test_the_audit_trail_is_append_only(store, tmp_path):
    session = _fill(store)
    connection = _raw(tmp_path / "sessions.db")
    try:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("UPDATE message_events SET detail = 'edited'")
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("DELETE FROM message_events")
    finally:
        connection.close()
    assert store.events(session)


def test_a_session_identity_is_immutable(store, tmp_path):
    session = _fill(store)
    connection = _raw(tmp_path / "sessions.db")
    try:
        with pytest.raises(sqlite3.DatabaseError) as failure:
            connection.execute(
                "UPDATE sessions SET session_id = 'renamed' WHERE session_id = ?", (session,)
            )
        assert "immutable" in str(failure.value)
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("UPDATE sessions SET created = 0")
    finally:
        connection.close()
    assert store.session(session) is not None


# --------------------------------------------------------------------------- #
# invariant 4 — monotonic sequence + the history hash                          #
# --------------------------------------------------------------------------- #


def test_the_session_records_a_history_hash_over_its_messages(store):
    session = _fill(store)
    recorded = store.require_session(session).history_sha256
    assert recorded == history_hash([item.to_dict() for item in store.messages(session)])
    assert store.verify_history(session)["matches"] is True


def test_an_edited_body_is_caught_even_when_the_session_hash_is_refreshed(store, tmp_path):
    """The trigger is the first fence; the per-body hash is the second, independent one."""
    session = _fill(store)
    target = store.messages(session)[1]
    connection = _raw(tmp_path / "sessions.db")
    try:
        connection.execute("DROP TRIGGER messages_no_update")  # bypass the first fence
        connection.execute(
            "UPDATE messages SET content = 'tampered' WHERE message_id = ?",
            (target.message_id,),
        )
        rows = connection.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY seq", (session,)
        ).fetchall()
        forged = history_hash([dict(row) for row in rows])
        connection.execute(
            "UPDATE sessions SET history_sha256 = ? WHERE session_id = ?", (forged, session)
        )
        connection.commit()
    finally:
        connection.close()
    report = store.verify_history(session)
    assert report["body_mismatch_seqs"] == [target.seq]
    assert report["bodies_ok"] is False
    assert report["matches"] is False


def test_resume_returns_the_exact_history_and_the_next_legal_role(store):
    session = _fill(store)
    resumed = store.resume(session)
    assert resumed["resumed"] is True
    assert [item["content"] for item in resumed["messages"]] == [
        text for _, text in CONVERSATION
    ]
    assert resumed["history"]["matches"] is True
    assert resumed["history"]["stored_sha256"] == resumed["history"]["recomputed_sha256"]
    assert resumed["next_role"] == "user"
    assert resumed["window"]["complete"] is True
    assert resumed["continuation"]["role"] == "user"
    assert session in resumed["continuation"]["command"]


def test_resume_refuses_a_turn_the_alternation_forbids(store):
    session = store.create_session("resume")
    store.append(session.session_id, "user", "only a user turn so far")
    resumed = store.resume(session.session_id)
    assert resumed["next_role"] == "assistant"
    with pytest.raises(RoleAlternationError):
        store.append(session.session_id, "user", "still the user")


def test_a_bounded_resume_window_says_what_it_omitted(store):
    session = _fill(store)
    resumed = store.resume(session, limit=2)
    assert resumed["window"]["returned"] == 2
    assert resumed["window"]["omitted"] == 4
    assert resumed["window"]["complete"] is False
    assert resumed["history"]["matches"] is True  # the check covers the whole session
    assert [item["seq"] for item in resumed["messages"]] == [1, 2]


def test_the_tail_window_returns_the_newest_messages_in_order(store):
    session = _fill(store)
    tail = store.messages(session, limit=2, tail=True)
    assert [item.seq for item in tail] == [5, 6]


# --------------------------------------------------------------------------- #
# search and listing                                                           #
# --------------------------------------------------------------------------- #


def test_search_finds_a_message_by_content_across_two_sessions(store):
    first = store.create_session("alpha")
    store.append(first.session_id, "user", "the durable session history is a message log")
    store.append(first.session_id, "assistant", "yes, append-only")
    second = store.create_session("beta")
    store.append(second.session_id, "user", "does the history survive a restart?")
    store.append(second.session_id, "assistant", "the history does survive a restart")
    result = store.search("history")
    assert result["engine"] == "fts5"
    assert set(result["sessions"]) == {first.session_id, second.session_id}
    assert result["returned"] == 3  # alpha 1 hit, beta 2 hits (one body each carries it)
    assert all(hit["snippet"] for hit in result["hits"])
    assert all(set(hit) >= {"message_id", "session_id", "seq", "role", "snippet", "score"}
               for hit in result["hits"])


def test_search_is_bounded_and_says_so(store):
    session = store.create_session("many")
    for index in range(6):
        store.append(session.session_id, "user" if index % 2 == 0 else "assistant",
                     f"needle number {index}")
    result = store.search("needle", limit=3)
    assert result["returned"] == 3
    assert result["bounded"] is True
    assert result["limit"] == 3


def test_search_can_be_scoped_to_one_session_and_role(store):
    first = store.create_session("alpha")
    store.append(first.session_id, "user", "scoped needle")
    store.append(first.session_id, "assistant", "another needle")
    second = store.create_session("beta")
    store.append(second.session_id, "user", "scoped needle elsewhere")
    scoped = store.search("needle", session_id=first.session_id)
    assert {hit["session_id"] for hit in scoped["hits"]} == {first.session_id}
    by_role = store.search("needle", roles=["assistant"])
    assert [hit["role"] for hit in by_role["hits"]] == ["assistant"]


def test_search_with_no_words_finds_nothing_and_says_why(store):
    _fill(store)
    result = store.search("   ")
    assert result["hits"] == []
    assert result["returned"] == 0
    assert "empty query" in result["note"]


def test_the_fts_index_is_consistent_with_the_message_table(store):
    _fill(store)
    consistency = store.index_consistency()
    assert consistency["engine"] == "fts5"
    assert consistency["messages"] == consistency["indexed"] == 6
    assert consistency["consistent"] is True


def test_reindex_reports_what_it_repaired(store):
    session = _fill(store)
    store.reindex()
    assert store.index_consistency()["consistent"] is True
    assert len(store.search("history")["hits"]) >= 1
    assert store.verify_history(session)["matches"] is True


def test_listing_reports_title_count_activity_and_lineage(store):
    session = _fill(store)
    records = store.sessions()
    assert len(records) == 1
    item = records[0]
    assert item["session_id"] == session
    assert item["title"] == "conversation"
    assert item["message_count"] == 6
    assert item["updated"] >= item["created"] > 0
    assert item["lineage"]["depth"] == 0
    assert item["lineage"]["lineage_root"] == session
    assert item["lineage"]["forked"] is False
    assert item["parent_present"] is True
    assert item["lineage_chain"] == [session]


def test_archived_sessions_leave_the_default_listing(store):
    session = _fill(store)
    store.archive_session(session, reason="done")
    assert store.sessions() == []
    assert [item["session_id"] for item in store.sessions(include_archived=True)] == [session]
    with pytest.raises(SessionError):
        store.append(session, "user", "no more writes into an archived history")
    store.archive_session(session, archived=False)
    assert store.append(session, "user", "writable again").seq == 7


# --------------------------------------------------------------------------- #
# lineage: fork and rotate                                                     #
# --------------------------------------------------------------------------- #


def test_a_fork_records_the_parent_and_copies_the_prefix_with_provenance(store):
    parent = _fill(store)
    cut = store.messages(parent)[3]
    forked = store.fork(parent, at_message_id=cut.message_id, title="branch")
    child = forked["child"]["session_id"]
    assert forked["copied_messages"] == 4
    assert child != parent
    child_messages = store.messages(child)
    assert [item.role for item in child_messages] == [
        role for role, _ in CONVERSATION[:4]
    ]
    assert [item.content for item in child_messages] == [
        text for _, text in CONVERSATION[:4]
    ]
    # fresh ids, but every copy names the message it came from
    assert not ({item.message_id for item in child_messages}
                & {item.message_id for item in store.messages(parent)})
    assert [item.origin_message_id for item in child_messages] == [
        item.message_id for item in store.messages(parent)[:4]
    ]
    assert store.require_session(child).parent_session_id == parent
    assert store.require_session(child).depth == 1
    assert store.require_session(child).lineage_root == parent
    assert store.verify_history(child)["matches"] is True
    assert store.lineage(child)["root"] == parent
    assert store.lineage(child)["chain"][-1]["session_id"] == parent


def test_a_fork_continues_with_correct_alternation(store):
    parent = _fill(store)
    child = store.fork(parent, at_message_id=store.messages(parent)[3].message_id)["child"]
    record = store.require_session(child["session_id"])
    assert record.head_role == "assistant"  # the prefix ends at message 4
    assert record.next_role == "user"
    assert store.append(child["session_id"], "user", "a new direction").seq == 5
    with pytest.raises(RoleAlternationError):
        store.append(child["session_id"], "user", "again")


def test_a_fork_without_copying_is_only_a_link(store):
    parent = _fill(store)
    forked = store.fork(parent, copy_prefix=False, title="empty branch")
    assert forked["copied_messages"] == 0
    assert store.messages(forked["child"]["session_id"]) == []
    assert store.require_session(forked["child"]["session_id"]).parent_session_id == parent


def test_a_fork_point_must_be_a_message_of_that_session(store):
    parent = _fill(store)
    other = _fill(store)
    stranger = store.messages(other)[0]
    with pytest.raises(UnknownSession):
        store.fork(parent, at_message_id=stranger.message_id)


def test_rotate_moves_the_newest_messages_into_a_successor_and_archives_the_parent(store):
    parent = _fill(store)
    rotated = store.rotate(parent, keep_last=2)
    assert rotated["rotated"] is True
    assert rotated["copied_messages"] == 2
    assert rotated["parent_messages_kept"] == 6
    assert rotated["parent"]["archived"] is True
    successor = rotated["successor"]["session_id"]
    assert [item.content for item in store.messages(successor)] == [
        text for _, text in CONVERSATION[-2:]
    ]
    assert store.require_session(successor).parent_session_id == parent
    assert store.require_session(successor).depth == 1
    # nothing was lost: the parent still has every message and its hash still verifies
    assert len(store.messages(parent, limit=100)) == 6
    assert store.verify_history(parent)["matches"] is True
    assert rotated["parent_messages_kept"] == 6


def test_rotate_reports_when_it_would_shrink_nothing(store):
    parent = _fill(store)
    rotated = store.rotate(parent, keep_last=50)
    assert rotated["rotated"] is False
    assert "keep_last" in rotated["reason"]
    assert rotated["successor"] is None


# --------------------------------------------------------------------------- #
# purge / retention                                                            #
# --------------------------------------------------------------------------- #


def test_purge_is_a_dry_run_unless_asked(store):
    session = _fill(store)
    plan = store.purge(session, mode="delete")
    assert plan["dry_run"] is True
    assert plan["messages_removed"] == 6
    assert plan["messages_kept"] == 0
    assert store.session(session) is not None
    assert len(store.messages(session)) == 6
    assert store.tombstone(session) is None


def test_purge_delete_writes_a_tombstone_and_removes_the_bodies(store):
    session = _fill(store)
    result = store.purge(session, mode="delete", reason="owner asked", dry_run=False)
    assert result["session_removed"] is True
    assert store.session(session) is None
    assert store.messages(session) == []
    tombstone = store.tombstone(session)
    assert tombstone["message_count"] == 6
    assert tombstone["mode"] == "delete"
    assert tombstone["reason"] == "owner asked"
    assert tombstone["history_sha256"] == result["history_sha256_before"]
    assert store.search("history")["hits"] == []  # the index went with the bodies
    assert store.index_consistency()["consistent"] is True
    # the audit trail keeps the metadata (never the bodies) so the act is provable
    kinds = [event["kind"] for event in store.events(session)]
    assert "purged" in kinds


def test_a_purged_session_still_has_a_lineage_and_says_it_was_purged(store):
    parent = _fill(store)
    child = store.fork(parent, at_message_id=store.messages(parent)[1].message_id)["child"]
    store.purge(parent, mode="delete", dry_run=False, reason="retention")
    lineage = store.lineage(child["session_id"])
    assert lineage["purged_ancestors"] == [parent]
    assert lineage["chain"][-1]["present"] is False
    assert lineage["chain"][-1]["purged"] is True
    with pytest.raises(UnknownSession) as failure:
        store.require_session(parent)
    assert "was purged" in str(failure.value)
    assert "the tombstone remains" in str(failure.value)


def test_truncate_keeps_the_newest_messages_and_re_records_the_hash(store):
    session = _fill(store)
    result = store.purge(session, mode="truncate", keep_last=2, dry_run=False)
    assert result["messages_removed"] == 4
    assert result["remaining_messages"] == 2
    remaining = store.messages(session)
    assert [item.seq for item in remaining] == [5, 6]
    assert store.verify_history(session)["matches"] is True
    record = store.require_session(session)
    assert record.message_count == 2
    assert record.head_message_id == remaining[-1].message_id
    # the sequence keeps counting from the head, so the next append is seq 7
    assert store.append(session, "user", "after the truncation").seq == 7


def test_purging_one_session_cannot_take_another_session_with_it(store, tmp_path):
    keep_me = _fill(store)
    target = _fill(store)
    connection = _raw(tmp_path / "sessions.db")
    try:
        connection.execute(
            "INSERT INTO session_store_meta(key, value) VALUES ('purge_token', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (target,),
        )
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("DELETE FROM messages")  # would take both sessions
    finally:
        connection.close()
    assert len(store.messages(keep_me)) == 6
    assert len(store.messages(target)) == 6
    store.purge(target, mode="delete", dry_run=False)
    assert len(store.messages(keep_me)) == 6
    assert store.verify_history(keep_me)["matches"] is True


def test_archive_is_the_reversible_mode_and_keeps_everything(store):
    session = _fill(store)
    result = store.purge(session, mode="archive", dry_run=False, reason="cold storage")
    assert result["applied"] is False or result["dry_run"] is False
    assert store.require_session(session).archived is True
    assert len(store.messages(session)) == 6


def test_purge_plan_refuses_an_unknown_mode(store):
    session = _fill(store)
    with pytest.raises(SessionError):
        store.purge_plan(session, mode="shred")


# --------------------------------------------------------------------------- #
# durability, the writer lease, migrations, corruption                         #
# --------------------------------------------------------------------------- #


def test_a_reopened_store_returns_the_same_history(tmp_path):
    path = tmp_path / "sessions.db"
    first = SessionStore(path)
    session = _fill(first)
    recorded = first.require_session(session).history_sha256
    first.close()
    second = SessionStore(path)
    try:
        assert second.require_session(session).history_sha256 == recorded
        assert second.verify_history(session)["matches"] is True
        resumed = second.resume(session)
        assert [item["content"] for item in resumed["messages"]] == [
            text for _, text in CONVERSATION
        ]
        assert resumed["next_role"] == "user"
        assert second.append(session, "user", "continuing after the reopen").seq == 7
    finally:
        second.close()


def test_a_live_writer_lease_refuses_a_second_owner(store):
    import os

    from skaldir.runs_store import owner_string

    other = owner_string(pid=os.getpid())
    held = store.acquire_writer(owner=other)
    assert held["acquired"] is True
    session = store.create_session("leased")
    rival = SessionStore(store.path, owner="pid=1:boot=elsewhere")
    try:
        refused = rival.acquire_writer(owner="pid=1:boot=elsewhere")
        assert refused["acquired"] is False
        assert refused["action"] == "refused"
        assert other in refused["reason"]
        with pytest.raises(WriterConflict):
            rival.append(session.session_id, "user", "not my store")
    finally:
        rival.close()
    assert store.release_writer(owner=other) is True


def test_migrations_are_additive_and_idempotent(tmp_path):
    """An older file gains the newer columns instead of failing to open."""
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(str(path))
    connection.execute(
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',
            created REAL NOT NULL DEFAULT 0, updated REAL NOT NULL DEFAULT 0,
            parent_session_id TEXT NOT NULL DEFAULT '',
            fork_point_message_id TEXT NOT NULL DEFAULT '',
            lineage_root TEXT NOT NULL DEFAULT '',
            message_count INTEGER NOT NULL DEFAULT 0, last_seq INTEGER NOT NULL DEFAULT 0,
            head_message_id TEXT NOT NULL DEFAULT '', head_role TEXT NOT NULL DEFAULT '',
            schema_version INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    connection.execute(
        "INSERT INTO sessions(session_id, title, created, updated) VALUES ('old', 'legacy', 1, 1)"
    )
    connection.commit()
    connection.close()
    store = SessionStore(path)
    try:
        columns = {
            str(row["name"])
            for row in store._db.execute("PRAGMA table_info(sessions)")  # noqa: SLF001
        }
        assert {"archived", "archived_at", "history_sha256", "token_total", "depth", "labels"} <= (
            columns
        )
        assert store.schema_version() == SCHEMA_VERSION
        assert store.session("old") is not None
        assert store.migrate() == SCHEMA_VERSION  # idempotent
    finally:
        store.close()


def test_a_damaged_file_is_reported_and_never_overwritten(tmp_path):
    path = tmp_path / "sessions.db"
    path.write_bytes(b"this is not a sqlite database\n" * 8)
    before = path.read_bytes()
    with pytest.raises(CorruptSessionStore) as failure:
        SessionStore(path)
    assert "not a readable session store" in str(failure.value)
    assert path.read_bytes() == before


def test_an_unknown_session_is_reported_with_its_store(store):
    with pytest.raises(UnknownSession) as failure:
        store.require_session("s-nope")
    assert "s-nope" in str(failure.value)
    assert store.path in str(failure.value)


# --------------------------------------------------------------------------- #
# export                                                                       #
# --------------------------------------------------------------------------- #


def test_export_renders_every_documented_format(store):
    session = _fill(store)
    as_json = store.export(session, as_format="json")
    document = json.loads(as_json["document"])
    assert document["session"]["session_id"] == session
    assert len(document["messages"]) == 6
    assert document["history"]["matches"] is True
    assert as_json["matches"] is True

    as_jsonl = store.export(session, as_format="jsonl")
    lines = [line for line in as_jsonl["document"].splitlines() if line]
    assert len(lines) == 8  # header + 6 messages + the history record
    assert json.loads(lines[0])["record"] == "session"
    assert json.loads(lines[-1])["record"] == "history"

    as_markdown = store.export(session, as_format="markdown")
    assert as_markdown["document"].startswith("# conversation")
    assert "## 1. user" in as_markdown["document"]

    as_text = store.export(session, as_format="text")
    assert "[1] user:" in as_text["document"]


def test_export_reports_a_mismatch_instead_of_hiding_it(store, tmp_path):
    session = _fill(store)
    connection = _raw(tmp_path / "sessions.db")
    try:
        connection.execute("DROP TRIGGER messages_no_update")
        connection.execute("UPDATE messages SET content = 'tampered' WHERE seq = 1")
        connection.commit()
    finally:
        connection.close()
    exported = store.export(session, as_format="jsonl")
    assert exported["matches"] is False


def test_export_refuses_an_unknown_format(store):
    session = _fill(store)
    with pytest.raises(SessionError):
        store.export(session, as_format="pdf")


# --------------------------------------------------------------------------- #
# the policy is data, not prose                                                #
# --------------------------------------------------------------------------- #


def test_the_retention_policy_names_what_is_kept_and_what_may_be_purged():
    assert set(PURGE_MODES) == {"truncate", "delete"}
    assert RETENTION_POLICY["default_mode"] == "archive"
    assert set(RETENTION_POLICY["modes"]) == {"archive", "rotate", "truncate", "delete"}
    assert RETENTION_POLICY["modes"]["archive"]["destructive"] is False
    assert RETENTION_POLICY["modes"]["truncate"]["destructive"] is True
    assert RETENTION_POLICY["modes"]["truncate"]["requires"].startswith("--apply")
    assert any("no TTL" in text or "timer" in text for text in RETENTION_POLICY["notes"]
               + RETENTION_POLICY["kept"])
    assert RETENTION_POLICY["never_purged"]


def test_status_reports_the_counts_the_engine_and_the_invariants(store):
    session = _fill(store)
    store.fork(session, at_message_id=store.messages(session)[1].message_id)
    snapshot = store.status()
    assert snapshot["store"] == store.path
    assert snapshot["schema_version"] == SCHEMA_VERSION
    assert snapshot["counts"]["sessions"] == 2
    assert snapshot["counts"]["messages"] == 8
    assert snapshot["counts"]["events"] >= 9
    assert snapshot["search"]["consistent"] is True
    assert set(snapshot["invariants"]) == {
        "role_alternation", "immutable_messages", "append_only", "monotonic_sequence"
    }
    assert snapshot["retention"] is RETENTION_POLICY


def test_the_invariants_are_triggered_in_the_schema_not_only_in_python():
    """A code-only guard is not the claim: the DDL must carry all seven triggers."""
    from skaldir import session_store

    ddl = " ".join(session_store._DDL)  # noqa: SLF001 — the schema is the claim
    for trigger in (
        "messages_no_update",
        "messages_no_delete",
        "messages_seq_monotonic",
        "messages_roles_alternate",
        "sessions_identity_immutable",
        "message_events_no_update",
        "message_events_no_delete",
    ):
        assert trigger in ddl
    assert "RAISE(ABORT" in ddl
    assert ImmutableMessageError  # the Python-side name for the same rule
