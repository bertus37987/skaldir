"""``skaldir runs …`` / ``skaldir goal …`` — the PERSIST CLI surface.

Every verb goes through ``cli_contract``'s envelope: one JSON document on stdout,
the shared exit-code set, and no success without evidence. The wiring snippet the
CLI owner has to paste is checked here too (leaf command paths + a second
``register_cli`` call being refused), because "a new command cannot ship without
a contract" only holds if the paths are the ones ``COMMANDS`` names.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pytest

from skaldir import budget as budget_module
from skaldir import cli_contract, runs_cli
from skaldir import runs_store as R
from skaldir.events import EventStore
from skaldir.incidents import IncidentStore
from skaldir.notes import NoteStore
from skaldir.session_store import SessionStore

#: The dotted paths ``runs_cli.register_cli(sub)`` produces — each one needs a
#: ``cli_contract.COMMANDS`` entry (see the hand-over snippet).
LEAF_PATHS = (
    "goal.criteria",
    "goal.show",
    "runs.checkpoint",
    "runs.list",
    "runs.resume",
    "runs.start",
    "runs.status",
)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("GODSV_FORMAT", raising=False)
    # SKALDIR_HOME outranks GODSV_HOME in registry.runtime_dir, so a leaked one (a
    # parallel wave's shell) would move the store out of this tmp_path: pin the premise.
    monkeypatch.delenv("SKALDIR_HOME", raising=False)
    home = tmp_path / "hermes-home"
    (home / "godsv").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("GODSV_HOME", str(home / "godsv"))
    return home / "godsv"


def _run(capsys, *argv: str) -> tuple[int, dict]:
    code = runs_cli.main([str(item) for item in argv])
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    return code, document


def _start(capsys, *, session: str = "cli", goal: str = "ship the persist layer"):
    code, document = _run(
        capsys,
        "runs",
        "start",
        "--goal",
        goal,
        "--session",
        session,
        "--criterion",
        "runs status reports the store",
        "--criterion",
        "the budget survives a restart",
        "--json",
    )
    assert code == 0, document
    return document["data"]


# --------------------------------------------------------------------------- #
# the surface itself                                                            #
# --------------------------------------------------------------------------- #


def test_register_cli_adds_the_two_groups_with_the_seven_leaf_paths():
    parser = runs_cli.build_parser()
    paths = sorted(path for path, _sub in cli_contract.leaf_commands(parser))
    assert paths == list(LEAF_PATHS)
    inventory = cli_contract.dump(parser)
    assert sorted(item["command"] for item in inventory["commands"]) == list(LEAF_PATHS)
    assert all(item["has_json_flag"] is True for item in inventory["commands"])
    assert inventory["leaf_commands"] == len(LEAF_PATHS)
    # the group IS wired now (cli.build_parser() calls runs_cli.register_cli), and that wiring
    # installs the six COMMANDS entries from inside register_cli — so nothing is unregistered.
    assert inventory["unregistered"] == []
    parsed = parser.parse_args(["runs", "status", "--json"])
    assert parsed.command_path == "runs.status"
    parsed = parser.parse_args(["goal", "criteria", "--json"])
    assert parsed.command_path == "goal.criteria"


# --------------------------------------------------------------------------- #
# runs list — the run ids the hints used to be unable to name                 #
# --------------------------------------------------------------------------- #


def _write_run(runs_dir: Path, run_id: str, task: str, *, status: str = "answered",
               stamp: int = 0) -> Path:
    """One hand-written transcript in exactly the shape ``read_transcript`` reads."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{run_id}.jsonl"
    rows = [
        {"seq": 1, "kind": "run.started", "at": "2026-09-22T10:00:00+00:00",
         "run_id": run_id, "task": task},
        {"seq": 2, "kind": "run.finished", "status": status, "reason": "",
         "steps": 1, "answer": "done"},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    # list_runs orders by mtime; two writes in the same millisecond must not race.
    import os

    os.utime(path, (stamp or path.stat().st_atime, stamp or path.stat().st_atime))
    return path


def test_runs_list_names_the_run_ids_with_the_shared_envelope(runtime, capsys):
    """``skaldir runs list --json`` — audit 63/V7: the verb the CLI reference promised."""
    state = runtime / "agent"
    _write_run(state / "runs", "run-20260922T100000Z-aaaaaa", "ship the release notes",
               stamp=1_700_000_000)
    _write_run(state / "runs", "run-20260922T110000Z-bbbbbb", "fix the failing check",
               stamp=1_700_000_100)

    code, document = _run(capsys, "runs", "list", "--state-dir", str(state), "--json")
    assert code == 0, document
    assert document["command"] == "runs.list"
    data = document["data"]
    assert data["count"] == 2
    assert [row["run_id"] for row in data["runs"]] == [
        "run-20260922T110000Z-bbbbbb",
        "run-20260922T100000Z-aaaaaa",
    ], "newest first, and the ids are the ones a caller can pass around"
    assert {row["status"] for row in data["runs"]} == {"answered"}
    assert all(row["task"] for row in data["runs"])
    assert data["runs_dir"].endswith("runs")
    assert data["exists"] is True
    # no success without evidence: the directory itself plus the reader
    refs = {item["ref"] for item in document["evidence"]}
    assert str(state / "runs") in refs
    assert any("agent_loop.py" in ref for ref in refs)


def test_runs_list_on_a_runtime_without_runs_is_an_empty_list_not_an_error(
    runtime, capsys
):
    """An empty run list is data with exit 0 — the same rule as every other list verb."""
    code, document = _run(capsys, "runs", "list", "--state-dir",
                          str(runtime / "agent"), "--json")
    assert code == 0, document
    assert document["data"]["count"] == 0
    assert document["data"]["runs"] == []
    assert document["data"]["exists"] is False


def test_the_product_parser_and_the_contract_know_runs_list(runtime, capsys):
    """The verb ships wired: parser leaf, contract entry, JSON flag (dump invariant)."""
    from skaldir import cli

    parser = cli.build_parser()
    inventory = cli_contract.dump(parser)
    dumped = {item["command"] for item in inventory["commands"]}
    assert "runs.list" in dumped
    assert inventory["unregistered"] == []
    parsed = parser.parse_args(["runs", "list", "--json"])
    assert parsed.command_path == "runs.list"
    assert parsed.json is True


def test_a_second_register_cli_call_is_refused_by_argparse():
    """The wiring pitfall: registering the group twice raises, it never doubles it."""
    parser = argparse.ArgumentParser(prog="probe")
    sub = parser.add_subparsers(dest="command")
    runs_cli.register_cli(sub)
    with pytest.raises(argparse.ArgumentError):
        runs_cli.register_cli(sub)


def test_the_hand_over_snippet_works_in_the_real_product_parser(runtime, capsys):
    """The two lines handed to the CLI owner, exercised against ``cli.build_parser()``.

    ``cli.py`` does not register the group yet, so this test *is* the snippet: it
    registers into the product parser's own subparsers handle (the real snippet
    sits right after ``sub = parser.add_subparsers(...)``, which is before
    ``cli_contract.annotate(parser)`` runs — the same order is reproduced here)
    and then drives the verbs through ``cli_contract.run_command``, exactly like
    ``cli.main`` does. Once the group is wired for real, the same assertions run
    against the wired parser and nothing is registered twice.
    """
    from skaldir import cli

    parser = cli.build_parser()
    sub = [action for action in parser._actions  # noqa: SLF001
           if isinstance(action, argparse._SubParsersAction)][0]
    wired = "runs" in sub.choices
    if not wired:
        runs_cli.register_cli(sub)  # the pasted snippet
        cli_contract.annotate(parser)  # ends build_parser() in cli.py

    def drive(*argv):
        args = parser.parse_args([str(item) for item in argv])
        code = cli_contract.run_command(args, args.func)
        captured = capsys.readouterr()
        return code, json.loads(captured.out)

    code, document = drive(
        "runs", "start", "--goal", "wired", "--session", "wired",
        "--criterion", "the snippet works", "--json",
    )
    assert code == 0 and document["command"] == "runs.start"
    assert document["data"]["goal_id"].startswith("wired:g")
    started = document["data"]["goal_id"]

    code, document = drive("runs", "status", "--json")
    assert code == 0 and document["command"] == "runs.status"
    assert document["data"]["goals"][0]["goal_id"] == started

    code, document = drive("goal", "criteria", "--json")
    assert code == 0 and document["command"] == "goal.criteria"
    assert document["data"]["open_criteria"] == ["c1"]


def test_the_six_commands_pass_the_dump_invariant_once_their_contracts_exist(monkeypatch):
    """The hand-over literal: these six ``COMMANDS`` entries make the dump clean.

    ``tests/test_cli_contract_dump.py`` fails while a leaf has no contract entry,
    so the snippet handed to the CLI owner includes the entries below. They are
    built here as the same objects the owner will paste, which keeps the literal
    in the hand-over honest instead of a claim.
    """
    def _cev(ref: str) -> tuple[dict, ...]:
        """The evidence tuple one COMMANDS entry declares (same literal, one place)."""
        return ({"ref": ref, "kind": "code", "note": ""},)

    entries = {
        "runs.status": cli_contract.CommandSpec(
            layer="PERSIST", evidence=_cev("src/skaldir/runs_store.py"),
            description="durable store: goals, operations, budget, run invariant",
            exit_codes=(0, 5, 7),
            data_schema={"type": "object",
                         "properties": {"store": {"type": "string"},
                                        "counts": {"type": "object"},
                                        "goals": {"type": "array"},
                                        "operations": {"type": "object"},
                                        "budget": {"type": "object"},
                                        "writer": {"type": "object"}},
                         "required": ["store", "counts", "goals", "operations"]},
            notes="read-only; exit 5 when runs.db does not exist yet",
        ),
        "runs.start": cli_contract.CommandSpec(
            layer="PERSIST",
            evidence=_cev("src/skaldir/runs_store.py::RunsStore.start_goal"),
            description="create the goal record + criteria + budget rows + first checkpoint",
            exit_codes=(0, 1, 3, 6, 7), mutates=True,
            data_schema={"type": "object",
                         "properties": {"goal_id": {"type": "string"},
                                        "revision": {"type": "integer"},
                                        "criteria": {"type": "array"},
                                        "checkpoint": {"type": "object"},
                                        "budget": {"type": "object"}},
                         "required": ["goal_id", "revision", "criteria", "checkpoint"]},
            notes="exit 6 when the session already has an open goal; exit 3 when another live "
                  "process owns the store's single-writer lease",
        ),
        "runs.resume": cli_contract.CommandSpec(
            layer="PERSIST",
            evidence=_cev("src/skaldir/runs_store.py::RunsStore.resume"),
            description="R0-R3: ownership, in-flight classification, cursor",
            exit_codes=(0, 2, 3, 5, 7), mutates=True,
            data_schema={"type": "object",
                         "properties": {"goal_id": {"type": "string"},
                                        "revision": {"type": "integer"},
                                        "state": {"type": "string"},
                                        "open_criteria": {"type": "array"},
                                        "parked_operations": {"type": "array"},
                                        "cursor": {"type": "object"},
                                        "budget": {"type": "object"},
                                        "owners": {"type": "object"},
                                        "wrote": {"type": "object"},
                                        "authoritative": {"type": "boolean"}},
                         "required": ["goal_id", "revision", "state", "cursor", "wrote"]},
            notes="exit 3 while a parked operation waits for operator:side_effect_verified; "
                  "exit 2 when the record cannot be classified",
        ),
        "runs.checkpoint": cli_contract.CommandSpec(
            layer="PERSIST",
            evidence=_cev("src/skaldir/runs_store.py::RunsStore.write_checkpoint"),
            description="write the self-sufficient snapshot + advance the resume cursor",
            exit_codes=(0, 5, 7), mutates=True,
            data_schema={"type": "object",
                         "properties": {"checkpoint_id": {"type": "string"},
                                        "seq": {"type": "integer"},
                                        "path": {"type": "string"},
                                        "sha256": {"type": "string"},
                                        "size_bytes": {"type": "integer"},
                                        "cursor": {"type": "object"}},
                         "required": ["checkpoint_id", "seq", "path", "sha256"]},
            notes="the payload file is the state; the index/cursor rows follow it",
        ),
        "goal.show": cli_contract.CommandSpec(
            layer="PERSIST", evidence=_cev("src/skaldir/runs_store.py::RunsStore.goal"),
            description="the durable goal record (revision, criteria with typed evidence refs)",
            exit_codes=(0, 5, 7),
            data_schema={"type": "object",
                         "properties": {"goal_id": {"type": "string"},
                                        "revision": {"type": "integer"},
                                        "state": {"type": "string"},
                                        "criteria": {"type": "array"},
                                        "operations": {"type": "array"},
                                        "cursor": {"type": "object"},
                                        "budget": {"type": "object"}},
                         "required": ["goal_id", "revision", "state", "criteria"]},
            notes="read-only; --all lists every record",
        ),
        "goal.criteria": cli_contract.CommandSpec(
            layer="PERSIST",
            evidence=_cev("src/skaldir/runs_store.py::RunsStore.record_criterion_outcome"),
            description="criteria table, or record one outcome from evidence",
            exit_codes=(0, 1, 2, 5, 7), mutates=True,
            data_schema={"type": "object",
                         "properties": {"goal_id": {"type": "string"},
                                        "revision": {"type": "integer"},
                                        "criteria": {"type": "array"},
                                        "open_criteria": {"type": "array"},
                                        "outcome": {"type": ["object", "null"]}},
                         "required": ["goal_id", "criteria", "open_criteria"]},
            notes="read-only unless --verify: a status is never a parameter, --evidence-ref is "
                  "mandatory and a stale revision / foreign workspace is refused (exit 2)",
        ),
    }
    for command, spec in entries.items():
        monkeypatch.setitem(cli_contract.COMMANDS, command, spec)

    inventory = cli_contract.dump(runs_cli.build_parser())
    assert inventory["unregistered"] == []
    assert sorted(item["command"] for item in inventory["commands"]) == list(LEAF_PATHS)
    for item in inventory["commands"]:
        assert item["layer"] == "PERSIST"
        assert item["default_evidence"], item["command"]
        assert item["args"], item["command"]
        assert 0 in item["exit_codes"]


def test_a_corrupt_store_is_exit_2_and_never_read_as_empty(runtime, capsys):
    """A damaged file is reported (exit 2) and its bytes are left alone."""
    (runtime / "runs.db").write_bytes(b"this is not a sqlite database\n" * 8)
    code, document = _run(capsys, "runs", "status", "--json")
    assert code == cli_contract.EXIT_VERIFICATION_FAILED
    assert document["error"]["code"] == "verification_failed"
    assert document["data"]["corrupt"] is True
    assert "not a readable" in document["data"]["blocked_reason"]
    assert (runtime / "runs.db").read_bytes().startswith(b"this is not")

    code, document = _run(capsys, "goal", "show", "--json")
    assert code == cli_contract.EXIT_VERIFICATION_FAILED
    code, document = _run(capsys, "runs", "resume", "--json")
    assert code == cli_contract.EXIT_VERIFICATION_FAILED


def test_status_without_a_store_is_not_found_and_says_so(runtime, capsys):
    code, document = _run(capsys, "runs", "status", "--json")
    assert code == cli_contract.EXIT_NOT_FOUND
    assert document["error"]["code"] == "not_found"
    assert document["data"]["exists"] is False
    assert "no goal has ever been started" in document["data"]["blocked_reason"]
    # the wired group's COMMANDS entry leads the envelope (run_command starts from
    # spec.evidence); the store path is the handler's own evidence and follows it.
    assert any(item["ref"] == str(runtime / "runs.db") for item in document["evidence"])
    assert document["next_actions"][0]["command"].startswith("skaldir runs start")


# --------------------------------------------------------------------------- #
# start / show / criteria / checkpoint                                          #
# --------------------------------------------------------------------------- #


def test_start_creates_the_record_the_store_the_budget_and_a_checkpoint(runtime, capsys):
    data = _start(capsys)
    assert data["goal_id"].startswith("cli:g") and data["revision"] == 1
    assert (runtime / "runs.db").is_file()
    assert [item["criterion_id"] for item in data["criteria"]] == ["c1", "c2"]
    assert set(data["budget"]) == set(budget_module.DEFAULT_LIMITS)
    assert data["checkpoint"]["seq"] == 1
    assert (runtime / "checkpoints" / data["goal_id"] / "1.json").is_file()


def test_starting_a_second_goal_for_the_same_session_is_a_conflict(runtime, capsys):
    first = _start(capsys)
    code, document = _run(capsys, "runs", "start", "--goal", "second", "--session", "cli", "--json")
    assert code == cli_contract.EXIT_CONFLICT
    assert document["error"]["code"] == "conflict"
    assert first["goal_id"] in document["error"]["message"]
    # a different session is a different goal — no interference
    second = _start(capsys, session="other")
    assert second["goal_id"] != first["goal_id"]


def test_goal_show_returns_the_record_and_its_typed_criteria(runtime, capsys):
    data = _start(capsys)
    code, document = _run(capsys, "goal", "show", "--json")
    assert code == 0
    record = document["data"]
    assert record["goal_id"] == data["goal_id"]
    assert record["state"] == "WORKING" and record["terminal"] is False
    assert [item["text"] for item in record["criteria"]] == [
        "runs status reports the store",
        "the budget survives a restart",
    ]
    assert record["cursor"]["open_criteria"] == ["c1", "c2"]
    assert record["checkpoint"]["seq"] == 1
    # the declared code ref leads the wired envelope; the record's own runs.db evidence follows
    assert any(item["ref"].endswith("runs.db") for item in document["evidence"])


def test_goal_show_with_an_unknown_id_is_not_found(runtime, capsys):
    _start(capsys)
    code, document = _run(capsys, "goal", "show", "--goal-id", "nope:g1", "--json")
    assert code == cli_contract.EXIT_NOT_FOUND
    assert document["error"]["code"] == "not_found"


def test_goal_criteria_lists_and_can_verify_one_from_evidence(runtime, capsys):
    _start(capsys)
    code, document = _run(capsys, "goal", "criteria", "--json")
    assert code == 0 and document["data"]["open_criteria"] == ["c1", "c2"]

    code, document = _run(
        capsys, "goal", "criteria", "--verify", "c1",
        "--evidence-ref", "test:tests/test_runs_store_cli.py::t",
        "--check-id", "pytest", "--exit-code", "0", "--json",
    )
    assert code == 0, document
    assert document["data"]["outcome"]["status"] == "verified"
    # the declared entry refs lead (run_command starts from spec.evidence); the handler's own
    # pair — the store, then the evidence the criterion was verified from — keeps its order.
    assert [item["ref"] for item in document["evidence"]][-2:] == [
        str(runtime / "runs.db"),
        "test:tests/test_runs_store_cli.py::t",
    ]
    code, document = _run(capsys, "goal", "criteria", "--json")
    assert document["data"]["open_criteria"] == ["c2"]
    assert document["data"]["criteria"][0]["status"] == "verified"
    assert document["data"]["criteria"][0]["evidence_refs"][0]["kind"] == "test"


def test_goal_criteria_refuses_a_status_without_evidence(runtime, capsys):
    _start(capsys)
    code, document = _run(capsys, "goal", "criteria", "--verify", "c1", "--json")
    assert code == cli_contract.EXIT_USAGE
    assert document["error"]["code"] == "usage"
    assert "evidence" in document["error"]["message"]
    code, document = _run(capsys, "goal", "criteria", "--json")
    assert document["data"]["criteria"][0]["status"] == "open"


def test_goal_criteria_refuses_evidence_from_a_stale_revision(runtime, capsys):
    _start(capsys)
    code, document = _run(
        capsys, "goal", "criteria", "--verify", "c1",
        "--evidence-ref", "test:tests/x.py::t", "--check-id", "pytest",
        "--revision", "0", "--json",
    )
    assert code == cli_contract.EXIT_VERIFICATION_FAILED
    assert document["error"]["code"] == "verification_failed"
    assert "invalidates earlier evidence" in document["error"]["message"]
    code, document = _run(capsys, "goal", "criteria", "--json")
    assert document["data"]["criteria"][0]["status"] == "open"


def test_a_failed_check_is_a_verification_failure(runtime, capsys):
    _start(capsys)
    code, document = _run(
        capsys, "goal", "criteria", "--verify", "c2",
        "--evidence-ref", "test:tests/x.py::t", "--check-id", "pytest",
        "--exit-code", "1", "--json",
    )
    assert code == cli_contract.EXIT_VERIFICATION_FAILED
    assert document["data"]["outcome"]["status"] == "failed"


def test_runs_checkpoint_writes_the_snapshot_and_the_cursor(runtime, capsys):
    data = _start(capsys)
    code, document = _run(
        capsys, "runs", "checkpoint", "--next-action", "write the durability test",
        "--last-event-seq", "41", "--json",
    )
    assert code == 0, document
    checkpoint = document["data"]
    assert checkpoint["seq"] == 2 and checkpoint["size_bytes"] <= 4096
    assert checkpoint["prev"] == data["checkpoint"]["sha256"]
    assert checkpoint["cursor"]["last_event_seq"] == 41
    assert checkpoint["cursor"]["next_action"] == "write the durability test"
    # the declared code ref leads; the snapshot file itself is the handler's own evidence
    assert any(item["ref"] == checkpoint["path"] for item in document["evidence"])
    assert R.sha256_file(checkpoint["path"]) == checkpoint["sha256"]


def test_runs_checkpoint_on_an_unknown_goal_is_not_found(runtime, capsys):
    _start(capsys)
    code, document = _run(capsys, "runs", "checkpoint", "--goal-id", "nope:g1", "--json")
    assert code == cli_contract.EXIT_NOT_FOUND


# --------------------------------------------------------------------------- #
# resume                                                                        #
# --------------------------------------------------------------------------- #


def test_resume_takes_ownership_and_hands_back_the_cursor(runtime, capsys):
    data = _start(capsys)
    code, document = _run(capsys, "runs", "resume", "--json")
    assert code == 0, document
    payload = document["data"]
    assert payload["goal_id"] == data["goal_id"]
    assert payload["revision"] == 1 and payload["state"] == "WORKING"
    assert payload["authoritative"] is True and payload["resumable"] is True
    assert payload["owners"]["owns"] is True
    assert payload["next_action"]
    assert payload["checkpoint"]["sha256_ok"] is True
    assert payload["detected"]["operations"]["open"] == 0
    # the declared code ref leads the wired envelope; the resume path's own file evidence is
    # the runs.db it read (plus the checkpoint it verified).
    assert any(
        item["kind"] == "file" and item["ref"].endswith("runs.db")
        for item in document["evidence"]
    )


def test_resume_of_an_unknown_goal_is_not_found(runtime, capsys):
    _start(capsys)
    code, document = _run(capsys, "runs", "resume", "--goal-id", "nope:g1", "--json")
    assert code == cli_contract.EXIT_NOT_FOUND


def test_resume_blocks_honestly_while_an_operation_is_parked(runtime, capsys):
    data = _start(capsys)
    with R.open_store(runtime) as store:
        operation, _ = store.register_operation(
            data["goal_id"], kind="send_email", target="owner@example", payload={"s": 1}
        )
        store.begin_operation(operation.op_id)

    code, document = _run(capsys, "runs", "resume", "--json")
    assert code == cli_contract.EXIT_BLOCKED
    assert document["error"]["code"] == "blocked"
    assert "operator" in document["warnings"][-1] or "parked" in json.dumps(document["warnings"])
    assert document["data"]["resumable"] is False
    assert document["data"]["parked_operations"][0]["op_id"] == operation.op_id
    assert document["data"]["parked_operations"][0]["wake_source"] == R.OPERATOR_WAKE_SOURCE

    # a second resume reports the same parked state and does not retry it
    code, document = _run(capsys, "runs", "resume", "--json")
    assert code == cli_contract.EXIT_BLOCKED
    with R.open_store(runtime) as store:
        row = store.operation(operation.op_id)
    assert row is not None
    assert (row.state, row.attempt) == ("unknown", 1)


def test_resume_restores_the_budget_from_the_durable_ledger(runtime, capsys):
    data = _start(capsys)
    with R.open_store(runtime) as store:
        ledger = store.ledger(data["goal_id"])
        reservation = ledger.reserve("main_agent", 5_000)
        ledger.spend("main_agent", 1_200, reservation=reservation["reservation_id"], note="turn 1")
    code, document = _run(capsys, "runs", "resume", "--restore-budget", "--json")
    assert code == 0, document
    restore = document["data"]["budget_restored"]
    assert restore["restored"] is True and restore["applied"] == 2
    assert restore["spent"]["main_agent"] == 1_200
    assert restore["reserved"]["main_agent"] == 3_800
    assert document["data"]["budget"]["main_agent"]["remaining"] == 100_000 - 1_200 - 3_800


def test_resume_reports_a_corrupt_record_instead_of_resuming_it(runtime, capsys):
    data = _start(capsys)
    with sqlite3.connect(runtime / "runs.db") as db:
        db.execute("UPDATE goals SET state = 'BOGUS' WHERE goal_id = ?", (data["goal_id"],))
    code, document = _run(capsys, "runs", "resume", "--json")
    assert code == cli_contract.EXIT_VERIFICATION_FAILED
    assert document["data"]["corrupt"] is True
    assert "BOGUS" in document["data"]["corrupt_reason"]


def test_runs_status_reports_the_store_the_invariant_and_the_budget(runtime, capsys):
    data = _start(capsys)
    with R.open_store(runtime) as store:
        ledger = store.ledger(data["goal_id"])
        ledger.reserve("workers", 1_000)
    code, document = _run(capsys, "runs", "status", "--json")
    assert code == 0
    payload = document["data"]
    assert payload["counts"]["goals"] == 1 and payload["counts"]["open_goals"] == 1
    assert payload["runs"]["available"] is True and payload["runs"]["ok"] is True
    assert payload["budget"][data["goal_id"]]["workers"]["reserved"] == 1_000
    assert payload["goals"][0]["goal_id"] == data["goal_id"]
    assert payload["writer"]["held"] is True


# --------------------------------------------------------------------------- #
# the observable outcome: the PERSIST selftest probe stops reporting "no store" #
# --------------------------------------------------------------------------- #


def test_the_persist_selftest_probes_go_green_once_a_goal_exists(runtime, capsys):
    """``skaldir selftest --json`` reported PERSIST blocked: no run store.

    The layer is green only when **every** durable store it probes exists. The session
    history (``sessions.db``, PERSIST's fourth probe, registered after this test was
    written) is one of them, so the setup creates all four — and the verdict is the one
    the probes compute (``_probe_persist`` no longer returns a constant ``False``), not the
    generic placeholder.
    """
    before = cli_contract.run_selftest(runtime, ["PERSIST"])
    assert before["ok"] is False
    assert "no run store" in before["layers"][0]["blocked_reason"]

    events = EventStore(runtime / "events.db")
    try:
        events.append("cli", 0, "thought", {"text": "hello"})
    finally:
        events.close()
    incidents = IncidentStore(runtime / "memory.db")
    notes = NoteStore(runtime / "memory.db")
    incidents.close()
    notes.close()
    sessions = SessionStore(runtime / "sessions.db")
    try:
        created = sessions.create_session(title="cli")
    finally:
        sessions.close()
    assert created.session_id
    _start(capsys)

    after = cli_contract.run_selftest(runtime, ["PERSIST"])
    layer = after["layers"][0]
    probes = {probe["name"]: probe for probe in layer["probes"]}
    assert sorted(probes) == ["events", "memory", "run_store", "session_store"]
    assert [name for name, probe in probes.items() if probe["ok"] is not True] == [], probes
    assert "run(s)" in probes["run_store"]["detail"]
    assert probes["run_store"]["evidence_ref"].endswith("runs.db")
    assert layer["status"] == "reachable"
    # the layer verdict is the probes' own: every durable store exists, so PERSIST is reached
    assert layer["ok"] is True and after["ok"] is True
    assert layer["blocked_reason"] is None
