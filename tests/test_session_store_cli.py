"""MVP/40 — ``skaldir session …``: the CLI surface, its exit codes and its durability.

The store's invariants are tested in ``test_session_store.py``. What is tested here is
the *product* path: one envelope per command, the documented exit codes (``6`` for a
same-role turn, ``2`` for a history that no longer matches, ``5`` for a session that was
never stored), the contract entries ``register_cli`` installs, and the one claim that
needs a real process to be believable — **a committed message survives ``kill -9``**.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from skaldir import cli, cli_contract, session_cli
from skaldir.session_store import SessionStore

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

CONVERSATION: tuple[tuple[str, str], ...] = (
    ("user", "We need a durable session history with message ids."),
    ("assistant", "One append-only table plus a per-session sequence covers it."),
    ("user", "What about two user turns in a row?"),
    ("assistant", "Refused by the alternation check and by a trigger."),
    ("user", "And after a crash?"),
    ("assistant", "Resume re-reads the history and compares its sha256."),
)


def _run(capsys, argv: list[str]) -> tuple[int, dict]:
    """Run the group's CLI and return ``(exit_code, envelope)`` from stdout.

    The group's own parser carries the ``session`` node, exactly like the product CLI
    does (``python -m skaldir.session_cli session list …``, the same shape as
    ``python -m skaldir.runs_cli runs status``), so the dotted contract paths match.
    """
    code = session_cli.main(["session", *argv])
    out = capsys.readouterr().out
    try:
        document = json.loads(out)
    except json.JSONDecodeError as exc:  # pragma: no cover — a broken envelope
        raise AssertionError(f"stdout was not one JSON document: {out[:400]!r}") from exc
    return code, document


def _seed(path: Path, *sessions: tuple[str, tuple[tuple[str, str], ...]]) -> list[str]:
    """Write sessions straight through the store (the CLI verbs are tested separately)."""
    store = SessionStore(path)
    ids: list[str] = []
    try:
        for title, messages in sessions or (("conversation", CONVERSATION),):
            record = store.create_session(title)
            for role, text in messages:
                store.append(record.session_id, role, text)
            ids.append(record.session_id)
    finally:
        store.close()
    return ids


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never let a test touch the owner's runtime directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("SKALDIR_HOME", str(home / "skaldir"))
    monkeypatch.delenv("GODSV_HOME", raising=False)
    monkeypatch.delenv("SKALDIR_FORMAT", raising=False)
    monkeypatch.delenv("GODSV_FORMAT", raising=False)


# --------------------------------------------------------------------------- #
# the contract the group installs for itself                                   #
# --------------------------------------------------------------------------- #


def test_register_cli_installs_every_session_contract_entry():
    """The group owns its ``COMMANDS`` rows, so wiring it cannot forget them."""
    import argparse

    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    assert session_cli.register_cli(sub) is not None
    leaves = [path for path, _parser in cli_contract.leaf_commands(parser)]
    assert leaves and all(path.startswith("session.") for path in leaves)
    assert set(leaves) == set(session_cli.CONTRACT_ENTRIES)
    for path in leaves:
        spec = cli_contract.COMMANDS.get(path)
        assert spec is not None, f"{path} has no contract entry"
        assert spec.layer == "PERSIST"
        assert spec.evidence, f"{path} declares no evidence"
        assert spec.data_schema and spec.data_schema.get("type")


def test_installing_the_entries_twice_changes_nothing():
    """``install_contract_entries`` is additive and idempotent (a second wiring is a no-op)."""
    import argparse

    from skaldir import cli_shared

    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    session_cli.register_cli(sub)
    before = dict(cli_contract.COMMANDS)
    assert cli_shared.install_contract_entries(session_cli.CONTRACT_ENTRIES, cli_contract) == []
    assert cli_contract.COMMANDS == before


def test_registering_the_group_in_choices_makes_it_reachable_once():
    """A second ``add_parser("session")`` is argparse's own refusal, not a silent double."""
    import argparse

    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    session_cli.register_cli(sub)
    with pytest.raises(argparse.ArgumentError):
        session_cli.register_cli(sub)
    assert list(sub.choices) == ["session"]


def test_every_session_leaf_takes_json_and_declares_its_exit_codes():
    parser = session_cli.build_parser()
    inventory = cli_contract.dump(parser)
    assert inventory["unregistered"] == []
    for item in inventory["commands"]:
        assert item["has_json_flag"] is True
        assert item["layer"] == "PERSIST"
        assert 0 in item["exit_codes"] and item["default_evidence"]


def test_the_product_cli_exposes_the_session_group():
    """The feature is wired into the product CLI, not only into its own module."""
    parser = cli.build_parser()
    paths = [path for path, _parser in cli_contract.leaf_commands(parser)]
    assert "session.resume" in paths and "session.search" in paths
    assert "session" in {path.split(".")[0] for path in paths}


# --------------------------------------------------------------------------- #
# the read verbs                                                               #
# --------------------------------------------------------------------------- #


def test_list_reports_title_count_activity_and_lineage(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    first, second = _seed(
        store_file,
        ("alpha", CONVERSATION),
        ("beta", (("user", "a second session"), ("assistant", "with two messages"))),
    )
    code, document = _run(capsys, ["list", "--store", str(store_file), "--json"])
    assert code == 0
    assert document["ok"] is True and document["command"] == "session.list"
    data = document["data"]
    assert data["count"] == 2
    titles = {item["session_id"]: item for item in data["sessions"]}
    assert titles[first]["message_count"] == 6
    assert titles[first]["title"] == "alpha"
    assert titles[first]["updated"] > 0
    assert titles[first]["lineage"]["depth"] == 0
    assert data["totals"]["messages"] == 8
    assert document["evidence"]


def test_show_returns_a_bounded_window_and_the_hash_verdict(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    (session,) = _seed(store_file)
    code, document = _run(
        capsys, ["show", "--session", session, "--limit", "2", "--store", str(store_file), "--json"]
    )
    assert code == 0
    data = document["data"]
    assert data["window"]["returned"] == 2
    assert data["window"]["omitted"] == 4
    assert data["window"]["complete"] is False
    assert data["history"]["matches"] is True
    assert data["history"]["stored_sha256"] == data["history"]["recomputed_sha256"]
    assert [item["role"] for item in data["messages"]] == ["user", "assistant"]
    assert data["messages"][0]["token_method"] == "chars/4"


def test_show_can_return_the_newest_window_and_omit_the_bodies(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    (session,) = _seed(store_file)
    code, document = _run(
        capsys,
        ["show", "--session", session, "--last", "--limit", "2", "--no-body",
         "--store", str(store_file), "--json"],
    )
    assert code == 0
    data = document["data"]
    assert [item["seq"] for item in data["messages"]] == [5, 6]
    assert all("content" not in item for item in data["messages"])
    assert all(item["body_included"] is False for item in data["messages"])


def test_show_returns_the_audit_trail_on_request(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    (session,) = _seed(store_file)
    code, document = _run(
        capsys, ["show", "--session", session, "--events", "--store", str(store_file), "--json"]
    )
    assert code == 0
    kinds = [event["kind"] for event in document["data"]["events"]]
    assert "session_created" in kinds and kinds.count("append") == 6


def test_verify_is_read_only_and_reports_every_session(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    _seed(store_file)
    code, document = _run(capsys, ["verify", "--store", str(store_file), "--json"])
    assert code == 0
    assert document["data"]["checked"] == 1
    assert document["data"]["all_match"] is True


def test_status_reports_counts_engine_and_writer(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    _seed(store_file)
    code, document = _run(capsys, ["status", "--store", str(store_file), "--json"])
    assert code == 0
    data = document["data"]
    assert data["counts"]["sessions"] == 1
    assert data["counts"]["messages"] == 6
    assert data["search"]["engine"] == "fts5"
    assert set(data["invariants"]) == {
        "role_alternation", "immutable_messages", "append_only", "monotonic_sequence"
    }


def test_policy_is_machine_readable(tmp_path, capsys):
    code, document = _run(capsys, ["policy", "--json"])
    assert code == 0
    data = document["data"]
    assert data["retention"]["default_mode"] == "archive"
    assert "delete" in data["retention"]["modes"]
    assert data["roles"] == ["system", "user", "assistant", "tool"]
    assert document["warnings"], "the policy should say that nothing is purged on a timer"


def test_search_finds_a_message_by_content_across_two_sessions(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    first, second = _seed(
        store_file,
        ("alpha", (("user", "the durable message store lives in one sqlite file"),
                   ("assistant", "it is append-only"))),
        ("beta", (("user", "does the message store search across sessions?"),
                  ("assistant", "yes, the message store is indexed with FTS5"))),
    )
    code, document = _run(capsys, ["search", "message store", "--store", str(store_file), "--json"])
    assert code == 0
    data = document["data"]
    assert data["engine"] == "fts5"
    assert set(data["sessions"]) == {first, second}
    assert data["returned"] == 3
    assert all(hit["snippet"] for hit in data["hits"])
    assert all(hit["session_id"] in {first, second} for hit in data["hits"])


def test_search_is_bounded_and_reports_the_bound(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    _seed(
        store_file,
        ("many", tuple(
            ("user" if index % 2 == 0 else "assistant", f"needle {index}") for index in range(6)
        )),
    )
    code, document = _run(
        capsys, ["search", "needle", "--limit", "2", "--store", str(store_file), "--json"]
    )
    assert code == 0
    assert document["data"]["returned"] == 2
    assert document["data"]["bounded"] is True
    assert document["warnings"]


def test_search_without_a_query_is_a_usage_error(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    _seed(store_file)
    code, document = _run(capsys, ["search", "   ", "--store", str(store_file), "--json"])
    assert code == cli_contract.EXIT_USAGE
    assert document["error"]["code"] == "usage"


def test_an_unknown_session_is_not_found(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    _seed(store_file)
    code, document = _run(
        capsys, ["show", "--session", "s-nope", "--store", str(store_file), "--json"]
    )
    assert code == cli_contract.EXIT_NOT_FOUND
    assert document["error"]["code"] == "not_found"
    assert "s-nope" in document["error"]["message"]


def test_an_absent_store_is_not_found_and_a_damaged_one_is_a_verification_failure(
    tmp_path, capsys
):
    missing = tmp_path / "nope" / "sessions.db"
    code, document = _run(capsys, ["list", "--store", str(missing), "--json"])
    assert code == cli_contract.EXIT_NOT_FOUND
    assert document["data"]["exists"] is False
    assert document["next_actions"]

    damaged = tmp_path / "damaged.db"
    damaged.write_bytes(b"not sqlite at all" * 32)
    code, document = _run(capsys, ["list", "--store", str(damaged), "--json"])
    assert code == cli_contract.EXIT_VERIFICATION_FAILED
    assert document["data"]["corrupt"] is True
    assert damaged.read_bytes() == b"not sqlite at all" * 32


# --------------------------------------------------------------------------- #
# the write verbs and their exit codes                                         #
# --------------------------------------------------------------------------- #


def test_new_then_append_builds_a_conversation_with_alternating_roles(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    code, document = _run(
        capsys, ["new", "--title", "fresh", "--store", str(store_file), "--json"]
    )
    assert code == 0
    session = document["data"]["created"]
    assert document["data"]["session"]["next_role"] == "user"
    payload: dict = {}
    for role, text in CONVERSATION:
        code, document = _run(
            capsys,
            ["append", "--session", session, "--role", role, "--content", text,
             "--store", str(store_file), "--json"],
        )
        assert code == 0, document
        payload = document["data"]
        assert payload["role"] == role
        assert payload["history_matches"] is True
    assert payload["seq"] == 6
    assert payload["next_role"] == "user"
    assert payload["token_method"] == "chars/4"


def test_two_user_turns_in_a_row_exit_6_with_a_clear_error(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    code, document = _run(capsys, ["new", "--store", str(store_file), "--json"])
    session = document["data"]["created"]
    code, document = _run(
        capsys,
        ["append", "--session", session, "--role", "user", "--content", "first",
         "--store", str(store_file), "--json"],
    )
    assert code == 0
    code, document = _run(
        capsys,
        ["append", "--session", session, "--role", "user", "--content", "second",
         "--store", str(store_file), "--json"],
    )
    assert code == cli_contract.EXIT_CONFLICT == 6
    error = document["error"]
    assert error["code"] == "conflict"
    assert "two user messages in a row" in error["message"]
    assert "message 1" in error["message"]
    assert error["hint"]
    # nothing was written, and the refusal is in the audit trail
    store = SessionStore(store_file)
    try:
        assert store.require_session(session).message_count == 1
        assert "refused_alternation" in [
            event["kind"] for event in store.events(session)
        ]
    finally:
        store.close()


def test_an_unknown_role_is_a_usage_error(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    code, document = _run(
        capsys,
        ["append", "--session", "s-anything", "--role", "human", "--content", "x",
         "--store", str(store_file), "--json"],
    )
    # argparse rejects it before the store is ever opened
    assert code == cli_contract.EXIT_USAGE


def test_resume_returns_the_history_and_the_next_command(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    (session,) = _seed(store_file)
    code, document = _run(
        capsys, ["resume", "--session", session, "--store", str(store_file), "--json"]
    )
    assert code == 0
    data = document["data"]
    assert [item["content"] for item in data["messages"]] == [text for _, text in CONVERSATION]
    assert data["history"]["matches"] is True
    assert data["next_role"] == "user"
    assert data["next_seq"] == 7
    assert session in data["continuation"]["command"]
    assert data["session"]["history_sha256"] == data["history"]["stored_sha256"]


def test_resume_without_a_session_picks_the_most_recent_one(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    _seed(store_file, ("older", CONVERSATION))
    time.sleep(0.01)
    _seed(store_file, ("newer", (("user", "the newest session"), ("assistant", "yes"))))
    code, document = _run(capsys, ["resume", "--store", str(store_file), "--json"])
    assert code == 0
    assert document["data"]["session"]["title"] == "newer"
    assert document["data"]["next_role"] == "user"


def test_export_writes_the_document_atomically_and_reports_its_hash(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    (session,) = _seed(store_file)
    target = tmp_path / "out.jsonl"
    code, document = _run(
        capsys,
        ["export", "--session", session, "--as", "jsonl", "--out", str(target),
         "--store", str(store_file), "--json"],
    )
    assert code == 0
    data = document["data"]
    assert data["written"] == str(target)
    assert data["messages"] == 6
    assert data["matches"] is True
    lines = [line for line in target.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 8
    assert str(target) in [item["ref"] for item in document["evidence"]]
    # the reported sha256 is the file's
    assert data["sha256"] == __import__("hashlib").sha256(target.read_bytes()).hexdigest()


def test_export_markdown_goes_to_stdout_when_no_out_is_given(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    (session,) = _seed(store_file)
    code = session_cli.main(
        ["session", "export", "--session", session, "--as", "markdown",
         "--store", str(store_file)]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.startswith("# conversation")
    assert "## 1. user" in captured.out


def test_fork_and_rotate_from_the_cli(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    (parent,) = _seed(store_file)
    code, document = _run(
        capsys, ["fork", "--session", parent, "--title", "branch",
                 "--store", str(store_file), "--json"]
    )
    assert code == 0
    child = document["data"]["child"]["session_id"]
    assert document["data"]["copied_messages"] == 6
    assert document["data"]["child"]["parent_session_id"] == parent
    assert document["data"]["lineage"]["chain"][-1]["session_id"] == parent

    code, document = _run(
        capsys, ["rotate", "--session", parent, "--keep-last", "2",
                 "--store", str(store_file), "--json"]
    )
    assert code == 0
    assert document["data"]["rotated"] is True
    assert document["data"]["parent"]["archived"] is True
    assert document["data"]["parent_messages_kept"] == 6
    successor = document["data"]["successor"]["session_id"]
    assert successor not in {parent, child}


def test_purge_is_a_dry_run_and_then_applies(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    (session,) = _seed(store_file)
    code, document = _run(
        capsys, ["purge", "--session", session, "--mode", "delete",
                 "--store", str(store_file), "--json"]
    )
    assert code == 0
    assert document["data"]["dry_run"] is True
    assert document["data"]["messages_removed"] == 6
    assert document["warnings"]
    assert document["next_actions"]
    store = SessionStore(store_file)
    try:
        assert store.session(session) is not None  # the dry run changed nothing
    finally:
        store.close()

    code, document = _run(
        capsys, ["purge", "--session", session, "--mode", "delete", "--apply",
                 "--reason", "test", "--store", str(store_file), "--json"]
    )
    assert code == 0
    assert document["data"]["dry_run"] is False
    assert document["data"]["session_removed"] is True
    assert document["data"]["tombstone"]["message_count"] == 6
    store = SessionStore(store_file)
    try:
        assert store.session(session) is None
        assert store.search("message store")["hits"] == []
    finally:
        store.close()


def test_archiving_makes_a_session_read_only(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    (session,) = _seed(store_file)
    code, _document = _run(
        capsys, ["archive", "--session", session, "--reason", "done",
                 "--store", str(store_file), "--json"]
    )
    assert code == 0
    code, document = _run(
        capsys,
        ["append", "--session", session, "--role", "user", "--content", "more",
         "--store", str(store_file), "--json"],
    )
    assert code == cli_contract.EXIT_CONFLICT
    assert "archived" in document["error"]["message"]
    code, document = _run(
        capsys, ["archive", "--session", session, "--unarchive",
                 "--store", str(store_file), "--json"]
    )
    assert code == 0 and document["data"]["archived"] is False


# --------------------------------------------------------------------------- #
# a history that changed under the store                                       #
# --------------------------------------------------------------------------- #


def test_show_and_resume_exit_2_when_the_history_no_longer_matches(tmp_path, capsys):
    store_file = tmp_path / "sessions.db"
    (session,) = _seed(store_file)
    connection = sqlite3.connect(str(store_file))
    try:
        connection.execute("DROP TRIGGER messages_no_update")  # bypass the first fence
        connection.execute("UPDATE messages SET content = 'tampered' WHERE seq = 1")
        connection.commit()
    finally:
        connection.close()
    for verb in ("show", "resume", "verify"):
        code, document = _run(
            capsys, [verb, "--session", session, "--store", str(store_file), "--json"]
        )
        assert code == cli_contract.EXIT_VERIFICATION_FAILED == 2, verb
        assert document["error"]["code"] == "verification_failed"


# --------------------------------------------------------------------------- #
# the durability claim: a committed message survives kill -9                   #
# --------------------------------------------------------------------------- #

_CHILD = textwrap.dedent(
    """
    import json, sys, time
    from skaldir.session_store import SessionStore

    store = SessionStore(sys.argv[1])
    session = store.create_session("survives a kill")
    for role, text in json.loads(sys.argv[2]):
        store.append(session.session_id, role, text)
    record = store.require_session(session.session_id)
    print(json.dumps({
        "session_id": session.session_id,
        "history_sha256": record.history_sha256,
        "message_count": record.message_count,
        "pid": __import__("os").getpid(),
    }), flush=True)
    # the process stays alive on purpose: the parent SIGKILLs it below, so the
    # store never gets a clean close()
    while True:
        time.sleep(0.05)
    """
)


def test_a_committed_history_survives_a_kill_9(tmp_path, capsys):
    """No clean shutdown: the child is SIGKILLed, then the history is resumed."""
    store_file = tmp_path / "sessions.db"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(store_file), json.dumps(CONVERSATION)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert child.stdout is not None
        line = child.stdout.readline()
        assert line, f"the writer child printed nothing: {child.stderr.read()!r}"
        reported = json.loads(line)
    finally:
        child.kill()  # SIGKILL: no close(), no flush, no WAL checkpoint
        child.wait(timeout=30)
    assert reported["message_count"] == len(CONVERSATION)
    assert child.returncode == -9

    # a fresh process (this one) reopens the file the killed writer left behind
    code, document = _run(
        capsys,
        ["resume", "--session", reported["session_id"], "--store", str(store_file), "--json"],
    )
    assert code == 0, document
    data = document["data"]
    assert data["session"]["history_sha256"] == reported["history_sha256"]
    assert data["history"]["matches"] is True
    assert data["history"]["recomputed_sha256"] == reported["history_sha256"]
    assert [item["content"] for item in data["messages"]] == [text for _, text in CONVERSATION]
    assert data["window"]["complete"] is True
    assert data["session"]["message_count"] == len(CONVERSATION)

    # ... and the conversation can be continued with the correct alternation
    code, document = _run(
        capsys,
        ["append", "--session", reported["session_id"], "--role", "user",
         "--content", "continuing after the crash", "--store", str(store_file), "--json"],
    )
    assert code == 0
    assert document["data"]["seq"] == len(CONVERSATION) + 1
    assert document["data"]["history_matches"] is True


def test_the_standalone_module_entry_point_runs_the_same_handlers(tmp_path):
    """``python -m skaldir.session_cli`` is how the proof script drives the group."""
    store_file = tmp_path / "sessions.db"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    new = subprocess.run(
        [sys.executable, "-m", "skaldir.session_cli", "session", "new", "--title",
         "module entry", "--store", str(store_file), "--json"],
        capture_output=True, text=True, env=env, check=False,
    )
    assert new.returncode == 0, new.stderr
    document = json.loads(new.stdout)
    assert document["command"] == "session.new"
    assert document["data"]["session"]["title"] == "module entry"
    listed = subprocess.run(
        [sys.executable, "-m", "skaldir.session_cli", "session", "list",
         "--store", str(store_file), "--json"],
        capture_output=True, text=True, env=env, check=False,
    )
    assert listed.returncode == 0
    assert json.loads(listed.stdout)["data"]["count"] == 1
