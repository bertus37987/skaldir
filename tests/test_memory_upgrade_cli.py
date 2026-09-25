"""``skaldir memory …`` — the CLI group (MVP/31).

Pinned here:

1. ``register_cli`` wires the group *and* its ``cli_contract.COMMANDS`` entries —
   a leaf with no contract entry fails the product's invariant test, so the
   group must not be able to ship half-wired;
2. every verb runs in ``--json`` mode as one envelope, and the human view moves
   to stderr;
3. exit codes are honest: 1 usage, 2 unresolved contradiction, 5 unknown id;
4. nothing is written silently — a dry run leaves the database byte-identical;
5. ``memory recall --measure`` reports the measured precision and exits 2 when it
   drops below the declared floor.
"""

import json

import pytest

from skaldir import cli_contract
from skaldir import memory_upgrade_cli as mcli


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    """Never touch the developer's real runtime; every default path stays here."""
    home = tmp_path / "home"
    (home / "godsv").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("GODSV_HOME", str(home / "godsv"))
    monkeypatch.delenv("SKALDIR_HOME", raising=False)
    monkeypatch.delenv("SKALDIR_FORMAT", raising=False)
    return home


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "typed.db")


def _parser():
    """A stand-in for ``skaldir``: same wiring, minus the 28 other command groups."""
    parser = cli_contract.ContractArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command", required=False)
    mcli.register_cli(sub)
    cli_contract.annotate(parser)
    return parser


def _run(argv, capsys):
    """Run one ``memory`` invocation → (exit code, envelope|None, stdout, stderr)."""
    parser = _parser()
    args = parser.parse_args(["memory", *argv])
    code = cli_contract.run_command(args, args.func)
    captured = capsys.readouterr()
    stream = captured.out.strip()
    document = json.loads(stream) if stream.startswith("{") else None
    return int(code), document, captured.out, captured.err


# -- wiring ------------------------------------------------------------------ #


def test_register_cli_wires_every_verb_into_the_contract():
    parser = _parser()
    paths = {path for path, _sub in cli_contract.leaf_commands(parser)}
    assert {path for path in paths if path.startswith("memory.")} == {
        "memory.remember",
        "memory.recall",
        "memory.consolidate",
        "memory.explain",
        "memory.forget",
        "memory.scope",
    }
    inventory = cli_contract.dump(parser)
    assert inventory["unregistered"] == [], (
        "a leaf without a contract entry: " + ", ".join(inventory["unregistered"])
    )


def test_register_cli_is_idempotent():
    parser = cli_contract.ContractArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    first = mcli.register_cli(sub)
    second = mcli.register_cli(sub)
    assert first is second
    assert len([path for path, _ in cli_contract.leaf_commands(parser)]) == 6


def test_the_contract_declares_which_verbs_write():
    for command, mutates in (
        ("memory.remember", True),
        ("memory.forget", True),
        ("memory.consolidate", True),
        ("memory.recall", False),
        ("memory.explain", False),
        ("memory.scope", False),
    ):
        spec = cli_contract.spec_for(command)
        assert spec.mutates is mutates, command
        assert spec.layer == "PERSIST"
        assert spec.evidence, command
        assert spec.exit_codes, command


# -- remember / recall ------------------------------------------------------- #


def test_remember_and_recall_round_trip_in_json(db, capsys):
    code, document, _out, _err = _run(
        ["remember", "--db", db, "deploy.port: prod listens on 8080", "--source", "owner",
         "--confidence", "0.9", "--scope", "project", "--scope-id", "prod", "--json"],
        capsys,
    )
    assert code == 0
    memory = document["data"]["memory"]
    assert document["data"]["written"] is True
    assert memory["kind"] == "fact" and memory["subject"] == "deploy port"
    assert document["data"]["journal_seq"] == 1

    code, document, _out, _err = _run(
        ["recall", "which port does prod listen on", "--db", db, "--json"], capsys
    )
    assert code == 0
    hits = document["data"]["hits"]
    assert hits and hits[0]["id"] == memory["id"]
    assert hits[0]["score_parts"]["scope_match"] == 1.0
    assert "relevance" in hits[0]["explanation"]


def test_recall_keeps_the_human_view_out_of_the_envelope(db, capsys):
    _run(["remember", "--db", db, "deploy.port: prod listens on 8080", "--source", "owner"], capsys)
    code, document, out, err = _run(
        ["recall", "which port does prod listen on", "--db", db, "--json"], capsys
    )
    assert code == 0 and document is not None
    assert out.strip().startswith("{") and out.count("{") >= 1
    assert "hit(s) of" in err, "the human view belongs on stderr in --json mode"


def test_remember_dry_run_writes_nothing(db, capsys):
    code, document, _out, _err = _run(
        ["remember", "--db", db, "a: b", "--dry-run", "--json"], capsys
    )
    assert code == 0
    assert document["data"]["written"] is False
    assert document["data"]["journal_seq"] is None
    code, document, _out, _err = _run(["scope", "--db", db, "--json"], capsys)
    assert code == 5, "a dry run left no database to report on"


def test_remember_reports_the_subject_convention_or_its_absence(db, capsys):
    _code, _document, out, _err = _run(["remember", "--db", db, "plain prose"], capsys)
    assert "no subject" in out


def test_remember_refuses_a_bad_confidence_with_exit_1(db, capsys):
    code, document, _out, _err = _run(
        ["remember", "--db", db, "a: b", "--confidence", "2", "--json"], capsys
    )
    assert code == 1
    assert document["error"]["code"] == "usage"


def test_a_scope_without_an_id_is_a_usage_error(db, capsys):
    code, document, _out, _err = _run(
        ["remember", "--db", db, "a: b", "--scope", "project", "--json"], capsys
    )
    assert code == 1 and "scope id" in document["error"]["message"]


def test_recall_refuses_an_empty_query(db, capsys):
    code, document, _out, _err = _run(["recall", "--db", db, "--json"], capsys)
    assert code == 1 and document["error"]["code"] == "usage"


def test_recall_on_a_missing_db_is_not_found(capsys, db):
    code, document, _out, _err = _run(["recall", "a", "--db", db, "--json"], capsys)
    assert code == 5 and document["error"]["code"] == "not_found"


def test_recall_with_jev_emits_the_facade_shape(db, capsys):
    _run(["remember", "--db", db, "deploy.port: prod listens on 8080", "--source", "owner"], capsys)
    code, document, _out, _err = _run(
        ["recall", "port", "--db", db, "--jev", "--json"], capsys
    )
    assert code == 0
    request = document["data"]["jev_request"]
    assert request["kind"] == "memory_injection"
    assert request["candidates"][0]["protected"] is True
    from skaldir.jev.contracts import JevMemoryInjectionRequest

    # the façade accepts the candidates unchanged (identity/revision fields come
    # from the caller, as they do for every Jev request)
    JevMemoryInjectionRequest(
        run_id="run-1",
        goal_id="goal-1",
        kind="memory_injection",
        goal_text=request["goal_text"],
        candidates=request["candidates"],
        budget=request["budget"],
    )


# -- consolidate ------------------------------------------------------------- #


def test_consolidate_is_a_dry_run_by_default(db, capsys):
    _run(["remember", "--db", db, "port: use 8080", "--source", "owner"], capsys)
    _run(["remember", "--db", db, "port: use 9090"], capsys)
    code, document, _out, err = _run(["consolidate", "--db", db, "--json"], capsys)
    assert code == 0
    assert document["data"]["dry_run"] is True
    assert document["data"]["superseded"] == 1
    assert document["data"]["after"] == document["data"]["before"]
    code2, doc2, _o, _e = _run(["scope", "--db", db, "--json"], capsys)
    assert code2 == 0 and doc2["data"]["status_counts"] == {"active": 2}
    assert "dry run" in err, "the human view belongs on stderr in --json mode"


def test_consolidate_apply_writes_and_reports(db, capsys):
    _run(["remember", "--db", db, "port: use 8080", "--source", "owner"], capsys)
    _run(["remember", "--db", db, "port: use 9090"], capsys)
    code, document, _out, _err = _run(["consolidate", "--db", db, "--apply", "--json"], capsys)
    assert code == 0
    assert document["data"]["dry_run"] is False
    assert document["data"]["after"]["superseded"] == 1
    assert document["data"]["rule"].startswith("owner-authored wins")


def test_consolidate_exits_2_while_a_contradiction_is_unresolved(db, capsys):
    """Exit 2 means: this needs an owner-authored decision, not another agent guess."""
    _run(["remember", "--db", db, "cache: prefer redis", "--source", "owner"], capsys)
    _run(["remember", "--db", db, "cache: avoid redis", "--source", "owner"], capsys)
    code, document, _out, err = _run(["consolidate", "--db", db, "--apply", "--json"], capsys)
    assert code == 2
    assert document["error"]["code"] == "verification_failed"
    assert document["data"]["unresolved_pairs"] == 1
    assert "owner-authored decision" in err


def test_consolidate_on_a_missing_db_is_not_found(db, capsys):
    code, document, _out, _err = _run(["consolidate", "--db", db, "--json"], capsys)
    assert code == 5 and document["error"]["code"] == "not_found"


# -- explain / forget -------------------------------------------------------- #


def test_explain_shows_provenance_decay_conflicts_and_journal(db, capsys):
    _code, first, _out, _err = _run(
        ["remember", "--db", db, "port: use 8080", "--source", "owner",
         "--confidence", "0.9", "--evidence-ref", "src/a.py", "--json"],
        capsys,
    )
    _run(["remember", "--db", db, "port: use 9090", "--confidence", "0.5"], capsys)
    memory_id = first["data"]["memory"]["id"]
    code, document, _out, _err = _run(
        ["explain", memory_id, "--db", db, "--query", "port", "--json"], capsys
    )
    assert code == 0
    data = document["data"]
    assert data["memory"]["source"] == "owner"
    assert data["evidence"]["evidence_weight"] > 0.5
    assert data["decay"]["half_life_days"] == 180.0
    assert data["conflicts"] and data["conflicts"][0]["reason"]
    assert [row["action"] for row in data["journal"]] == ["remember"]
    assert data["would_rank"][0]["id"] == memory_id


def test_explain_unknown_id_is_not_found(db, capsys):
    code, document, _out, _err = _run(["explain", "mem_nope123456", "--db", db, "--json"], capsys)
    assert code == 5 and document["error"]["code"] == "not_found"


def test_forget_is_soft_and_journaled(db, capsys):
    _code, first, _out, _err = _run(["remember", "--db", db, "a: b", "--json"], capsys)
    memory_id = first["data"]["memory"]["id"]
    code, document, _out, _err = _run(
        ["forget", memory_id, "--db", db, "--reason", "wrong", "--json"], capsys
    )
    assert code == 0 and document["data"]["written"] is True
    assert document["data"]["memory"]["status"] == "forgotten"
    code, document, _out, _err = _run(["explain", memory_id, "--db", db, "--json"], capsys)
    actions = [row["action"] for row in document["data"]["journal"]]
    assert actions == ["remember", "forget"]


def test_forget_hard_deletes_the_row(db, capsys):
    _code, first, _out, _err = _run(["remember", "--db", db, "a: b", "--json"], capsys)
    memory_id = first["data"]["memory"]["id"]
    code, document, _out, _err = _run(["forget", memory_id, "--db", db, "--hard", "--json"], capsys)
    assert code == 0 and document["data"]["hard"] is True
    code, document, _out, _err = _run(["explain", memory_id, "--db", db, "--json"], capsys)
    assert code == 5


def test_forget_dry_run_writes_nothing(db, capsys):
    _code, first, _out, _err = _run(["remember", "--db", db, "a: b", "--json"], capsys)
    memory_id = first["data"]["memory"]["id"]
    code, document, _out, _err = _run(
        ["forget", memory_id, "--db", db, "--dry-run", "--json"], capsys
    )
    assert code == 0 and document["data"]["written"] is False
    _code, document, _out, _err = _run(["explain", memory_id, "--db", db, "--json"], capsys)
    assert document["data"]["memory"]["status"] == "active"


def test_forget_unknown_id_is_not_found(db, capsys):
    code, document, _out, _err = _run(["forget", "mem_nope123456", "--db", db, "--json"], capsys)
    assert code == 5 and document["error"]["code"] == "not_found"


# -- scope ------------------------------------------------------------------- #


def test_scope_reports_policy_and_journal_integrity(db, capsys):
    _run(["remember", "--db", db, "a: b", "--scope", "project", "--scope-id", "prod"], capsys)
    _run(["remember", "--db", db, "c: d", "--kind", "incident"], capsys)
    code, document, _out, _err = _run(["scope", "--db", db, "--json"], capsys)
    assert code == 0
    data = document["data"]
    assert data["total"] == 2
    assert data["journal_integrity"]["ok"] is True
    assert data["decay_policy"]["incident"]["ttl_days"] == 180.0
    assert data["embedding_provider"]["usable"] is True
    assert data["embedding_provider"]["semantic"] is False
    assert {item["scope"] for item in data["scopes"]} == {"global", "project"}


def test_scope_on_missing_db_is_not_found(db, capsys):
    code, document, _out, _err = _run(["scope", "--db", db, "--json"], capsys)
    assert code == 5 and document["error"]["code"] == "not_found"


def test_default_db_lives_in_the_runtime_dir(isolated_runtime, capsys):
    code, document, _out, _err = _run(["remember", "a: b", "--json"], capsys)
    assert code == 0
    assert document["data"]["db"] == str(isolated_runtime / "godsv" / "typed_memory.db")


# -- the measured check ------------------------------------------------------ #


def test_recall_measure_reports_the_numbers_and_passes_the_floor(db, capsys):
    code, document, _out, _err = _run(["recall", "--measure", "--db", db, "--json"], capsys)
    assert code == 0
    data = document["data"]
    assert data["synthetic"] is True
    measurement = data["measurement"]
    assert measurement["queries"] == 13
    assert measurement["top1"] == 12
    assert measurement["baseline_newest"]["top1"] < measurement["top1"]
    assert measurement["failures"], "the known miss is reported, not dropped"
    assert data["within_floor"] is True
    assert "precision@1" in _err
    assert data["honesty"], "the synthetic-numbers caveat ships with the numbers"


def test_measure_does_not_touch_the_callers_store(tmp_path, db, capsys):
    _run(["remember", "--db", db, "a: b"], capsys)
    before = (tmp_path / "typed.db").read_bytes()
    code, _document, _out, _err = _run(["recall", "--measure", "--db", db, "--json"], capsys)
    assert code == 0
    assert (tmp_path / "typed.db").read_bytes() == before


def test_measure_exits_2_below_the_floor(db, capsys, monkeypatch):
    monkeypatch.setattr(mcli, "MEASURED_FLOOR_TOP1", 0.99)
    code, document, _out, _err = _run(["recall", "--measure", "--db", db, "--json"], capsys)
    assert code == 2
    assert document["error"]["code"] == "verification_failed"


# -- standalone entry point -------------------------------------------------- #


def test_build_parser_runs_a_verb_without_the_product_cli(db, capsys):
    parser = mcli.build_parser()
    assert parser is not None
    args = parser.parse_args(["scope", "--db", db])
    assert int(args.func(args)) == 5  # no db yet → not found, the honest code
    remember = parser.parse_args(["remember", "a: b", "--db", db])
    assert int(remember.func(remember)) == 0


def test_main_without_arguments_is_usage(capsys):
    assert mcli.main([]) == 1


def test_usage_error_helper_survives_a_missing_contract(monkeypatch, capsys):
    """A group must keep working in a build that has no cli_contract module."""
    monkeypatch.setattr(mcli, "_CONTRACT", None)
    assert mcli._not_found("no typed memory db at /x") == 5
    assert mcli._usage("bad input") == 1
    assert mcli._verification_failed("needs a human") == 2
    assert mcli.contract_entries() == {}
    # and the refusals go through the shared helpers when the contract is there
    assert mcli._not_found("gone") == 5
    assert mcli._verification_failed("needs a human", details={"a": 1}) == 2
