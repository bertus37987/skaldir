"""VERIFY layer — the ``skaldir verify`` command group (own module, no ``cli.py`` edit).

Pinned here:

1. STRUCTURE — ``register_cli(subparsers)`` adds exactly the six leaf paths
   ``verify.plan`` / ``verify.run`` / ``verify.status`` / ``verify.evidence`` /
   ``verify.flow`` / ``verify.flows``, each with ``--json`` (the invariant
   ``tests/test_cli_contract_dump.py`` enforces once the group is wired), and the group
   installs the six matching ``cli_contract.COMMANDS`` entries itself.
2. EXIT CODES — ``0`` plan certifies / run PASS · ``2`` run FAIL · ``3`` blocked
   (UNKNOWN or nothing to verify) · ``5`` an unknown ``--run-id`` · ``1`` usage.
3. ENVELOPE — driven through ``cli_contract.run_command`` stdout carries exactly one JSON
   document, ``data`` is the payload and ``evidence`` is never empty (otherwise the
   envelope downgrades the success to exit 7).
4. NO FAKE PASS — the CLI cannot return 0 for a criterion whose check did not execute.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from skaldir import cli_contract, verify_cli

LEAF_PATHS = (
    "verify.evidence",
    "verify.flow",
    "verify.flows",
    "verify.plan",
    "verify.run",
    "verify.status",
)


# -- helpers ---------------------------------------------------------------- #


def _workspace(tmp_path: Path, criteria: list, *, goal: str = "cli goal") -> Path:
    root = tmp_path / "ws"
    (root / ".godsv").mkdir(parents=True, exist_ok=True)
    (root / ".godsv" / "verify.json").write_text(
        json.dumps({"goal": goal, "criteria": criteria}), encoding="utf-8"
    )
    return root


def _passing_ws(tmp_path: Path) -> Path:
    root = _workspace(tmp_path, [
        {"criterion": "the artifact exists", "paths": ["artifact.bin"], "min_bytes": 3},
        {"criterion": "the command answers",
         "argv": [sys.executable, "-c", "print('PASS cli')"], "require_pass_line": True},
    ])
    (root / "artifact.bin").write_bytes(b"built")
    return root


def _invoke(capsys, argv: list[str]) -> tuple[int, dict]:
    """Run the standalone group and read its one JSON document from stdout."""
    code = verify_cli.main(argv)
    captured = capsys.readouterr()
    text = captured.out.strip()
    payload = json.loads(text) if text else {}
    return code, payload


def _wired_parser(sub=None) -> argparse.ArgumentParser:
    """A parser shaped like ``cli.py``: the group registered, then annotated."""
    root = argparse.ArgumentParser(prog="skaldir")
    sub = root.add_subparsers(dest="command", required=False)
    verify_cli.register_cli(sub)
    cli_contract.annotate(root)
    return root


def _envelope(capsys, parser: argparse.ArgumentParser, argv: list[str]) -> tuple[int, dict]:
    args = parser.parse_args(argv)
    code = cli_contract.run_command(args, args.func)
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    return code, document


# -- 1. structure ------------------------------------------------------------ #


class TestStructure:
    def test_register_cli_adds_exactly_the_six_leaf_paths(self):
        parser = _wired_parser()
        paths = sorted(path for path, _sub in cli_contract.leaf_commands(parser))
        assert paths == list(LEAF_PATHS)
        groups = [action.choices for action in parser._actions
                  if isinstance(action, argparse._SubParsersAction)]
        assert any("verify" in choices for choices in groups)

    def test_every_verb_takes_json(self):
        parser = _wired_parser()
        for path, sub in cli_contract.leaf_commands(parser):
            options = {option for action in sub._actions for option in action.option_strings}
            assert "--json" in options, f"{path} has no --json flag"

    def test_the_group_reports_the_six_verbs_in_its_help(self):
        parser = _wired_parser()
        group = [
            action.choices["verify"] for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ][0]
        verbs = [
            name for action in group._actions if isinstance(action, argparse._SubParsersAction)
            for name in action.choices
        ]
        assert sorted(verbs) == ["evidence", "flow", "flows", "plan", "run", "status"]

    def test_a_missing_verb_is_a_usage_error(self):
        assert verify_cli.main([]) == verify_cli.EXIT_USAGE
        assert verify_cli.main(["nope"]) == verify_cli.EXIT_USAGE

    def test_the_exit_codes_are_the_contracts_own(self):
        assert verify_cli.EXIT_OK == cli_contract.EXIT_OK == 0
        assert verify_cli.EXIT_USAGE == cli_contract.EXIT_USAGE == 1
        assert verify_cli.EXIT_VERIFICATION_FAILED == cli_contract.EXIT_VERIFICATION_FAILED == 2
        assert verify_cli.EXIT_BLOCKED == cli_contract.EXIT_BLOCKED == 3
        assert verify_cli.EXIT_NOT_FOUND == cli_contract.EXIT_NOT_FOUND == 5

    def test_both_entry_points_expose_the_same_verbs(self):
        standalone = verify_cli.build_parser()
        paths = sorted(path for path, _sub in cli_contract.leaf_commands(standalone))
        assert paths == ["evidence", "flow", "flows", "plan", "run", "status"]


# -- 2/3. plan and run ------------------------------------------------------ #


class TestPlanCommand:
    def test_a_certifiable_plan_exits_zero(self, tmp_path, capsys):
        root = _passing_ws(tmp_path)
        code, payload = _invoke(capsys, ["plan", "--workspace", str(root), "--json"])
        assert code == verify_cli.EXIT_OK
        assert payload["plan"]["certifiable"] is True
        assert payload["criteria_count"] == 2
        assert payload["plan"]["goal"] == "cli goal"
        assert payload["pass_requirement"] == verify_cli.V.PASS_REQUIREMENT

    def test_a_plan_that_cannot_certify_anything_is_blocked_with_its_reason(
        self, tmp_path, capsys, monkeypatch
    ):
        root = _workspace(tmp_path, [{"criterion": "the ui flow works", "check": "ui_e2e"}])
        for name in verify_cli.V.DECLARED_CHECK_ENV.values():
            monkeypatch.delenv(name, raising=False)
        code, payload = _invoke(capsys, ["plan", "--workspace", str(root), "--json"])
        assert code == verify_cli.EXIT_BLOCKED
        assert payload["plan"]["certifiable"] is False
        assert payload["plan"]["uncertifiable"] == ["the ui flow works"]

    def test_no_criteria_at_all_is_blocked(self, tmp_path, capsys):
        root = tmp_path / "bare"
        root.mkdir()
        code, payload = _invoke(capsys, ["plan", "--workspace", str(root), "--json"])
        assert code == verify_cli.EXIT_BLOCKED
        assert payload["criteria_count"] == 0
        assert payload["verdict"] == verify_cli.V.VERDICT_UNKNOWN
        assert payload["certifiable"] is False
        assert "no criteria" in payload["verdict_reason"]

    def test_a_missing_workspace_is_blocked_not_planned(self, tmp_path, capsys):
        absent = tmp_path / "gone"
        code, payload = _invoke(
            capsys, ["plan", "--workspace", str(absent), "--criterion",
                     "the test suite passes", "--json"]
        )
        assert code == verify_cli.EXIT_BLOCKED
        assert payload["certifiable"] is False
        assert "does not exist" in payload["verdict_reason"]
        assert absent.exists() is False

    def test_a_criterion_can_be_passed_inline(self, tmp_path, capsys):
        code, payload = _invoke(
            capsys,
            ["plan", "--workspace", str(tmp_path), "--criterion", "the test suite passes",
             "--json"],
        )
        assert code == verify_cli.EXIT_OK
        assert payload["source"] == "argument"
        assert payload["plan"]["checks"] == ["pytest"]

    def test_the_plan_comes_from_the_contract_when_asked(self, tmp_path, capsys, monkeypatch):
        from skaldir.completion import CompletionStore, DodItem

        runtime = tmp_path / "runtime"
        runtime.mkdir()
        CompletionStore(runtime).begin_goal(
            "s1", "contract goal",
            [DodItem(id="c1", text="the test suite passes", verification="pytest")],
        )
        code, payload = _invoke(
            capsys,
            ["plan", "--workspace", str(tmp_path), "--from-contract", "--runtime",
             str(runtime), "--session", "s1", "--json"],
        )
        assert code == verify_cli.EXIT_OK
        assert payload["source"] == "contract"
        assert payload["plan"]["goal"] == "contract goal"
        assert payload["plan"]["criteria"][0]["criterion_id"] == "c1"


class TestRunCommand:
    def test_a_passing_run_exits_zero_and_records_the_evidence(self, tmp_path, capsys):
        root = _passing_ws(tmp_path)
        state = tmp_path / "state"
        code, payload = _invoke(
            capsys, ["run", "--workspace", str(root), "--state", str(state), "--json"]
        )
        assert code == verify_cli.EXIT_OK
        assert payload["verdict"] == verify_cli.V.VERDICT_PASS
        assert payload["counts"] == {"criteria": 2, "passed": 2, "failed": 0, "unknown": 0,
                                     "blocking": 2}
        assert payload["recorded"] is True
        assert (state / "runs" / f"{payload['run_id']}.json").is_file()
        assert (state / "evidence.jsonl").is_file()
        for result in payload["results"]:
            assert result["evidence_sha256"] and result["started_at"] <= result["finished_at"]

    def test_a_real_failure_exits_two(self, tmp_path, capsys):
        root = _workspace(tmp_path, [{"criterion": "the artifact exists", "paths": ["gone.bin"]}])
        code, payload = _invoke(
            capsys, ["run", "--workspace", str(root), "--state", str(tmp_path / "s"), "--json"]
        )
        assert code == verify_cli.EXIT_VERIFICATION_FAILED == 2
        assert payload["verdict"] == verify_cli.V.VERDICT_FAIL
        assert payload["counts"]["failed"] == 1

    def test_a_check_that_cannot_run_exits_three_not_zero(self, tmp_path, capsys, monkeypatch):
        root = _workspace(tmp_path, [{"criterion": "the ui flow works", "check": "ui_e2e"}])
        for name in verify_cli.V.DECLARED_CHECK_ENV.values():
            monkeypatch.delenv(name, raising=False)
        code, payload = _invoke(
            capsys, ["run", "--workspace", str(root), "--state", str(tmp_path / "s"), "--json"]
        )
        assert code == verify_cli.EXIT_BLOCKED == 3
        assert payload["verdict"] == verify_cli.V.VERDICT_UNKNOWN != verify_cli.V.VERDICT_PASS
        assert payload["counts"]["passed"] == 0

    def test_a_failing_check_beside_an_unknown_one_is_a_failure(self, tmp_path, capsys,
                                                                monkeypatch):
        root = _workspace(tmp_path, [
            {"criterion": "the artifact exists", "paths": ["gone.bin"]},
            {"criterion": "the ui flow works", "check": "ui_e2e"},
        ])
        for name in verify_cli.V.DECLARED_CHECK_ENV.values():
            monkeypatch.delenv(name, raising=False)
        code, payload = _invoke(
            capsys, ["run", "--workspace", str(root), "--state", str(tmp_path / "s"), "--json"]
        )
        assert code == 2 and payload["verdict"] == verify_cli.V.VERDICT_FAIL

    def test_a_dry_run_records_nothing(self, tmp_path, capsys):
        root = _passing_ws(tmp_path)
        state = tmp_path / "state"
        code, payload = _invoke(
            capsys,
            ["run", "--workspace", str(root), "--state", str(state), "--dry-run", "--json"],
        )
        assert code == verify_cli.EXIT_OK
        assert payload["recorded"] is False
        assert not state.exists()
        assert payload["verdict"] == verify_cli.V.VERDICT_PASS

    def test_no_criteria_is_blocked_not_a_silent_success(self, tmp_path, capsys):
        root = tmp_path / "bare"
        root.mkdir()
        code, payload = _invoke(
            capsys, ["run", "--workspace", str(root), "--state", str(tmp_path / "s"), "--json"]
        )
        assert code == verify_cli.EXIT_BLOCKED
        assert payload["verdict"] == verify_cli.V.VERDICT_UNKNOWN
        assert not (tmp_path / "s").exists()

    def test_the_timeout_flag_reaches_the_check(self, tmp_path, capsys):
        root = _workspace(tmp_path, [
            {"criterion": "the slow command finishes",
             "argv": [sys.executable, "-c", "import time; time.sleep(30); print('PASS')"],
             "require_pass_line": True},
        ])
        code, payload = _invoke(
            capsys,
            ["run", "--workspace", str(root), "--state", str(tmp_path / "s"),
             "--timeout", "2", "--json"],
        )
        assert code == 2
        assert "timeout" in payload["results"][0]["reason"].lower()


# -- status and evidence ---------------------------------------------------- #


class TestStatusCommand:
    def test_status_of_an_empty_store_is_honest(self, tmp_path, capsys):
        code, payload = _invoke(capsys, ["status", "--state", str(tmp_path / "s"), "--json"])
        assert code == verify_cli.EXIT_OK
        assert payload["found"] is False and payload["runs_count"] == 0
        assert payload["latest"] is None

    def test_status_reports_the_latest_run_and_its_freshness(self, tmp_path, capsys):
        root = _passing_ws(tmp_path)
        state = tmp_path / "state"
        _invoke(capsys, ["run", "--workspace", str(root), "--state", str(state), "--json"])
        code, payload = _invoke(capsys, ["status", "--state", str(state), "--json"])
        assert code == verify_cli.EXIT_OK
        assert payload["found"] is True
        assert payload["verdict"] == verify_cli.V.VERDICT_PASS
        assert payload["runs_count"] == 1
        assert payload["freshness"]["fresh"] is None  # no git repository here
        assert payload["citable"] is False
        assert payload["results"]

    def test_an_unknown_run_id_is_not_found(self, tmp_path, capsys):
        code, payload = _invoke(
            capsys, ["status", "--state", str(tmp_path / "s"), "--run-id", "vr-nope", "--json"]
        )
        assert code == verify_cli.EXIT_NOT_FOUND == 5
        assert payload["found"] is False and payload["run_id"] == "vr-nope"

    def test_a_specific_run_id_is_looked_up(self, tmp_path, capsys):
        root = _passing_ws(tmp_path)
        state = tmp_path / "state"
        _code, run = _invoke(
            capsys, ["run", "--workspace", str(root), "--state", str(state), "--json"]
        )
        code, payload = _invoke(
            capsys, ["status", "--state", str(state), "--run-id", run["run_id"], "--json"]
        )
        assert code == verify_cli.EXIT_OK and payload["run_id"] == run["run_id"]


class TestEvidenceCommand:
    def test_evidence_lists_the_recorded_rows_with_hashes(self, tmp_path, capsys):
        root = _passing_ws(tmp_path)
        state = tmp_path / "state"
        _invoke(capsys, ["run", "--workspace", str(root), "--state", str(state), "--json"])
        code, payload = _invoke(capsys, ["evidence", "--state", str(state), "--json"])
        assert code == verify_cli.EXIT_OK
        assert payload["count"] == 2
        assert payload["ledger_exists"] is True
        for row in payload["evidence"]:
            assert row["evidence_sha256"] and row["output_sha256"]
            assert row["status"] == verify_cli.V.STATUS_PASSED

    def test_evidence_filters_by_run_and_criterion(self, tmp_path, capsys):
        root = _passing_ws(tmp_path)
        state = tmp_path / "state"
        _code, run = _invoke(
            capsys, ["run", "--workspace", str(root), "--state", str(state), "--json"]
        )
        _code, one = _invoke(
            capsys, ["evidence", "--state", str(state), "--run-id", run["run_id"],
                     "--criterion", "artifact", "--json"]
        )
        assert one["count"] == 1
        assert one["evidence"][0]["criterion"] == "the artifact exists"
        assert one["evidence"][0]["artifacts"][0]["path"] == "artifact.bin"

    def test_evidence_of_an_unknown_run_is_not_found(self, tmp_path, capsys):
        code, payload = _invoke(
            capsys, ["evidence", "--state", str(tmp_path / "s"), "--run-id", "vr-nope", "--json"]
        )
        assert code == verify_cli.EXIT_NOT_FOUND
        assert payload["count"] == 0 and payload["found"] is False

    def test_evidence_of_an_empty_store_is_an_empty_list_not_an_error(self, tmp_path, capsys):
        code, payload = _invoke(capsys, ["evidence", "--state", str(tmp_path / "s"), "--json"])
        assert code == verify_cli.EXIT_OK
        assert payload["evidence"] == [] and payload["count"] == 0
        assert payload["ledger_exists"] is False


# -- 3. the shared envelope ------------------------------------------------- #


class TestEnvelope:
    def test_each_verb_emits_exactly_one_envelope_document(self, tmp_path, capsys):
        root = _passing_ws(tmp_path)
        state = tmp_path / "state"
        parser = _wired_parser()
        for argv, command in (
            (["verify", "plan", "--workspace", str(root), "--json"], "verify.plan"),
            (["verify", "run", "--workspace", str(root), "--state", str(state), "--json"],
             "verify.run"),
            (["verify", "status", "--state", str(state), "--json"], "verify.status"),
            (["verify", "evidence", "--state", str(state), "--json"], "verify.evidence"),
        ):
            code, document = _envelope(capsys, parser, argv)
            assert code == 0, document
            assert document["schema_version"] == cli_contract.SCHEMA_VERSION
            assert document["command"] == command
            assert document["ok"] is True
            assert document["exit_code"] == 0
            assert document["data"], f"{command} returned no data"
            assert document["evidence"], (
                f"{command} reported success without evidence — the envelope downgrades that "
                "to exit 7"
            )
            assert document["error"] is None

    def test_the_envelope_carries_the_verdict_and_the_results(self, tmp_path, capsys):
        root = _passing_ws(tmp_path)
        _code, document = _envelope(
            capsys, _wired_parser(),
            ["verify", "run", "--workspace", str(root), "--state", str(tmp_path / "s"), "--json"],
        )
        assert document["data"]["verdict"] == "PASS"
        assert len(document["data"]["results"]) == 2
        kinds = {item["kind"] for item in document["evidence"]}
        assert kinds & {"file", "run", "verification"}

    def test_a_verification_failure_becomes_exit_two_with_its_code(self, tmp_path, capsys):
        root = _workspace(tmp_path, [{"criterion": "the artifact exists", "paths": ["gone.bin"]}])
        code, document = _envelope(
            capsys, _wired_parser(),
            ["verify", "run", "--workspace", str(root), "--state", str(tmp_path / "s"), "--json"],
        )
        assert code == 2 and document["exit_code"] == 2
        assert document["error"]["code"] == "verification_failed"
        assert document["ok"] is False

    def test_a_blocked_plan_becomes_exit_three_with_its_code(self, tmp_path, capsys):
        root = _workspace(tmp_path, [{"criterion": "the ui flow works", "check": "ui_e2e"}])
        code, document = _envelope(
            capsys, _wired_parser(), ["verify", "plan", "--workspace", str(root), "--json"]
        )
        assert code == 3 and document["error"]["code"] == "blocked"
        assert document["data"]["plan"]["certifiable"] is False

    def test_unknown_is_never_a_pass_through_the_envelope(self, tmp_path, capsys, monkeypatch):
        root = _workspace(tmp_path, [{"criterion": "the ui flow works", "check": "ui_e2e"}])
        for name in verify_cli.V.DECLARED_CHECK_ENV.values():
            monkeypatch.delenv(name, raising=False)
        code, document = _envelope(
            capsys, _wired_parser(),
            ["verify", "run", "--workspace", str(root), "--state", str(tmp_path / "s"), "--json"],
        )
        assert code == 3 and document["ok"] is False
        assert document["data"]["verdict"] == "UNKNOWN"
        assert document["error"]["code"] == "blocked"
        assert document["error"]["details"]["unknown"] == ["the ui flow works"]

    def test_format_json_also_takes_the_envelope_path(self, tmp_path, capsys):
        root = _passing_ws(tmp_path)
        args = _wired_parser().parse_args(
            ["verify", "plan", "--workspace", str(root), "--format", "json"]
        )
        code = cli_contract.run_command(args, args.func)
        document = json.loads(capsys.readouterr().out)
        assert code == 0 and document["command"] == "verify.plan"

    def test_the_human_view_moves_to_stderr_and_text_mode_prints_a_table(self, tmp_path, capsys):
        root = _passing_ws(tmp_path)
        args = _wired_parser().parse_args(
            ["verify", "plan", "--workspace", str(root), "--json", "--text"]
        )
        code = cli_contract.run_command(args, args.func)
        captured = capsys.readouterr()
        document = json.loads(captured.out)
        assert code == 0 and document["command"] == "verify.plan"
        assert "criterion" in captured.err and "artifact" in captured.err

    def test_not_found_run_id_in_envelope_mode(self, tmp_path, capsys):
        code, document = _envelope(
            capsys, _wired_parser(),
            ["verify", "status", "--state", str(tmp_path / "s"), "--run-id", "vr-nope", "--json"],
        )
        assert code == 5 and document["error"]["code"] == "not_found"
        assert document["evidence"], "even a not-found answer names what it searched"
