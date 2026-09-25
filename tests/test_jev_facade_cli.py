"""The ``skaldir jev …`` surface: the CLI envelope contract of MVP/03 §7.1.

This file proves the contract *without* editing ``src/godsv/cli.py`` (another
agent owns that file): it takes the real product parser, retrieves its
``subparsers`` action, calls ``register_cli`` on it — the exact 3-line snippet the
CLI owner has to add — and then runs the ``jev`` verbs through argparse. If the
snippet is wrong, this fails.

It also pins the envelope itself: exactly one JSON object on stdout, exit codes
0/2/3/4, the three honest ``--provider`` choices (never the fixture), and the
``selftest`` rule that exits ``1`` when a rule loses its tests.
"""

from __future__ import annotations

import argparse
import io
import json

import pytest

from skaldir.jev import cli as jev_cli
from skaldir.jev.cli import (
    EXIT_NEEDS_CREDENTIALS,
    EXIT_NO_ADVISORY,
    EXIT_OK,
    EXIT_USAGE,
    EXIT_VIOLATION,
)
from skaldir.jev.selftest import SELFTEST_CASES

# --------------------------------------------------------------------------- #
# the wiring contract                                                           #
# --------------------------------------------------------------------------- #


def _subparsers(parser: argparse.ArgumentParser):
    return [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ][0]


def _wired_product_parser() -> argparse.ArgumentParser:
    """The real ``skaldir`` parser with the Jev group added, as the patch does.

    The CLI owner's patch is now in ``cli.py``: ``register_cli(sub)`` runs inside
    ``build_parser()``, so the real parser already carries the group. Registering a
    second time is an argparse conflict ("conflicting subparser: jev"), so the helper
    wires only when the group is missing — which is also what keeps this test honest
    for a build whose ``cli.py`` does not have the snippet yet.
    """
    from skaldir.cli import build_parser

    parser = build_parser()
    sub = _subparsers(parser)
    if "jev" not in sub.choices:
        jev_cli.register_cli(sub)
    return parser


def test_the_cli_wires_the_group_itself():
    """The hand-over: ``build_parser()`` carries the ``jev`` group without any help."""
    from skaldir.cli import build_parser

    parser = build_parser()
    assert "jev" in _subparsers(parser).choices
    assert parser.parse_args(["jev", "kinds", "--json"]).func is jev_cli.cmd_kinds


def test_register_cli_adds_the_group_to_a_subparsers_handle():
    parser = argparse.ArgumentParser(prog="x")
    sub = parser.add_subparsers(dest="command")
    returned = jev_cli.register_cli(sub)
    assert returned is not None
    assert parser.parse_args(["jev", "kinds"]).func is jev_cli.cmd_kinds
    for verb in ("kinds", "packs", "selftest", "decide", "history"):
        assert parser.parse_args(["jev", verb]).func is not None


def test_the_three_line_snippet_works_against_the_real_product_parser():
    """``register_cli(_subparsers(build_parser()))`` — the snippet, executed."""
    parser = _wired_product_parser()
    args = parser.parse_args(["jev", "selftest", "--json"])
    assert args.func is jev_cli.cmd_selftest
    assert args.json is True

    decide_args = parser.parse_args(
        ["jev", "decide", "--kind", "voice_triage", "--provider", "disabled", "--dry-run"]
    )
    assert decide_args.kind == "voice_triage"
    assert decide_args.dry_run is True


def test_the_product_parser_still_parses_its_own_commands():
    """The group is additive: an existing command keeps working after wiring."""
    parser = _wired_product_parser()
    assert parser.parse_args(["metrics"]).func is not None
    assert parser.parse_args([]).command is None


def test_only_the_honest_providers_are_selectable():
    # ``settings`` is not a fourth *provider*: it is "the provider this installation is
    # configured with", resolved by the runtime, and it is the default because a decision
    # layer that answers `provider_disabled` on a configured host is a door that only
    # looks open (audit 12 §3, measured: exit 3, `provider_disabled`).
    assert jev_cli.PROVIDER_CHOICES == (
        "settings",
        "disabled",
        "llm_system_one_adapter",
        "typesafe_jev",
    )
    assert jev_cli.PROVIDER_SETTINGS == "settings"
    assert "replay_fixture" not in jev_cli.PROVIDER_CHOICES
    assert "fixture" not in jev_cli.PROVIDER_CHOICES


# --------------------------------------------------------------------------- #
# stdout / exit codes                                                           #
# --------------------------------------------------------------------------- #


def _run(argv, stdin: str | None = None, monkeypatch=None) -> tuple[int, str]:
    if stdin is not None and monkeypatch is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return jev_cli.main(argv), ""


def test_kinds_json_is_one_object(capsys):
    code = jev_cli.main(["kinds", "--json"])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    payload = json.loads(out)
    # six of MVP/03 §5 plus ``semantic_verification`` (MVP/93)
    assert payload["count"] == 7
    assert {row["kind"] for row in payload["kinds"]} == set(SELFTEST_CASES)


def test_packs_json_lists_the_registry_including_the_reused_pack(capsys):
    code = jev_cli.main(["packs", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    refs = [row["ref"] for row in payload["packs"]]
    assert "voice_triage_v1" in refs
    assert "routing_v1" in refs


def test_selftest_json_exit_zero_when_every_rule_is_pinned(capsys):
    code = jev_cli.main(["selftest", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert payload["ok"] is True
    assert payload["untested_rules"] == []
    assert all(row["reachable"] for row in payload["kinds"])


def test_selftest_exits_one_when_a_rule_loses_its_tests(capsys, monkeypatch):
    """The auditable claim: a rule without a test makes the CLI fail."""
    from skaldir.jev import rules

    monkeypatch.setattr(
        rules,
        "missing_rule_tests",
        lambda root=None: {"R1_no_goal_pass": ("tests/test_jev_facade_rules.py::test_x",)},
    )
    code = jev_cli.main(["selftest", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["untested_rules"] == ["R1_no_goal_pass"]
    assert payload["exit_code"] == 1


def test_selftest_text_mode_names_the_untested_rule(capsys, monkeypatch):
    from skaldir.jev import rules

    monkeypatch.setattr(
        rules,
        "missing_rule_tests",
        lambda root=None: {"R3_no_security_exception": ("tests/x.py::test_y",)},
    )
    code = jev_cli.main(["selftest", "--text"])
    captured = capsys.readouterr()
    assert code == 1
    assert "R3_no_security_exception" in captured.out
    assert "UNTESTED" in captured.out
    assert "untested rule" in captured.err


def test_decide_without_a_request_is_a_usage_error(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code = jev_cli.main(["decide", "--kind", "voice_triage", "--dry-run"])
    assert code == EXIT_USAGE
    assert "empty" in capsys.readouterr().err


def test_decide_rejects_a_kind_it_does_not_know(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"run_id": "r", "goal_id": "g"})))
    code = jev_cli.main(["decide", "--kind", "telepathy", "--dry-run"])
    assert code == EXIT_USAGE


def test_decide_reports_a_contract_violation_with_exit_four(capsys, monkeypatch):
    payload = {**SELFTEST_CASES["voice_triage"], "surprise": True}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    code = jev_cli.main(["decide", "--kind", "voice_triage", "--dry-run"])
    assert code == EXIT_VIOLATION
    assert "not a valid voice_triage request" in capsys.readouterr().err


def test_decide_refuses_a_kind_mismatch_with_exit_four(capsys, monkeypatch):
    payload = {**SELFTEST_CASES["tool_routing"], "kind": "voice_triage"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    code = jev_cli.main(["decide", "--kind", "tool_routing", "--dry-run"])
    assert code == EXIT_VIOLATION
    assert "contradicts" in capsys.readouterr().err


def test_decide_with_a_disabled_provider_prints_the_blocked_decision(capsys, monkeypatch):
    """``--provider disabled`` is still selectable, and still honest: no fabricated answer.

    The *default* is no longer ``disabled`` (it is ``settings`` — the runtime's own flags,
    see ``tests/test_jev_always_armed.py``): the switch keeps its meaning, it just is not
    what an unconfigured verb does to a configured installation.
    """
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(SELFTEST_CASES["tool_routing"])))
    code = jev_cli.main(
        ["decide", "--kind", "tool_routing", "--dry-run", "--provider", "disabled"]
    )
    out = capsys.readouterr().out
    assert code == EXIT_NO_ADVISORY
    document = json.loads(out)
    assert document["status"] == "blocked"
    assert document["provider_mode"] == "disabled"
    assert document["authority"] == "advisory"
    assert document["refusal_reason"] == "provider_disabled"
    assert document["fallback"]


def test_decide_with_the_live_provider_without_a_credential_is_blocked(capsys, monkeypatch):
    """No authorized credential ⇒ 'blocked' + the needs-credentials code, never a live answer."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(SELFTEST_CASES["voice_triage"])))
    code = jev_cli.main(
        ["decide", "--kind", "voice_triage", "--provider", "typesafe_jev", "--dry-run"]
    )
    document = json.loads(capsys.readouterr().out)
    assert code == EXIT_NEEDS_CREDENTIALS
    assert document["status"] == "blocked"
    assert document["live_jev"] is False
    # the substrate's own refusal is named, not hidden behind a contract error
    assert document["refusal_reason"].startswith("provider_contract_refusal")
    assert document["fallback"]


def test_decide_writes_out_and_the_ledger_then_history_reads_it(capsys, monkeypatch, tmp_path):
    out_file = tmp_path / "decision.json"
    state = tmp_path / "state"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(SELFTEST_CASES["voice_triage"])))
    code = jev_cli.main(
        [
            "decide",
            "--kind",
            "voice_triage",
            "--out",
            str(out_file),
            "--state",
            str(state),
        ]
    )
    document = json.loads(capsys.readouterr().out)
    assert code == EXIT_NO_ADVISORY  # provider disabled by default
    written = json.loads(out_file.read_text(encoding="utf-8"))
    assert written["decision_id"] == document["decision_id"]
    ledger = state / "jev_decisions.jsonl"
    assert ledger.is_file()

    code = jev_cli.main(["history", "--json", "--state", str(state)])
    history = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert history["count"] == 1
    assert history["decisions"][0]["kind"] == "voice_triage"
    assert history["decisions"][0]["status"] == "blocked"


def test_dry_run_does_not_touch_the_ledger(capsys, monkeypatch, tmp_path):
    state = tmp_path / "state"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(SELFTEST_CASES["voice_triage"])))
    jev_cli.main(
        ["decide", "--kind", "voice_triage", "--dry-run", "--state", str(state)]
    )
    capsys.readouterr()
    assert not (state / "jev_decisions.jsonl").exists()


def test_decide_text_mode_is_not_json(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(SELFTEST_CASES["memory_injection"])))
    code = jev_cli.main(["decide", "--kind", "memory_injection", "--text", "--dry-run"])
    out = capsys.readouterr().out
    assert code == EXIT_NO_ADVISORY
    assert out.startswith("memory_injection")
    assert "{" not in out


def test_history_on_an_empty_state_is_an_empty_list(capsys, tmp_path):
    code = jev_cli.main(["history", "--json", "--state", str(tmp_path / "nope")])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert payload["count"] == 0


def test_history_can_filter_by_run(capsys, monkeypatch, tmp_path):
    state = tmp_path / "state"
    for run_id in ("run-a", "run-b"):
        payload = {**SELFTEST_CASES["voice_triage"], "run_id": run_id}
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        jev_cli.main(["decide", "--kind", "voice_triage", "--state", str(state)])
        capsys.readouterr()
    code = jev_cli.main(["history", "--json", "--run", "run-a", "--state", str(state)])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert payload["count"] == 1
    assert payload["decisions"][0]["run_id"] == "run-a"


def test_decide_reads_a_request_from_a_file_and_transcript_alias(capsys, monkeypatch, tmp_path):
    request_file = tmp_path / "state.json"
    request_file.write_text(json.dumps(SELFTEST_CASES["context_compaction"]), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # nothing on stdin
    for flag in ("--request", "--transcript"):
        code = jev_cli.main(
            ["decide", "--kind", "context_compaction", flag, str(request_file), "--dry-run"]
        )
        document = json.loads(capsys.readouterr().out)
        assert code == EXIT_NO_ADVISORY
        assert document["kind"] == "context_compaction"


def test_decide_apply_is_refused_with_the_owner_named(capsys, monkeypatch):
    """Applying belongs to the compaction port; Jev says so instead of pretending."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(SELFTEST_CASES["context_compaction"])))
    code = jev_cli.main(["decide", "--kind", "context_compaction", "--apply", "--dry-run"])
    captured = capsys.readouterr()
    assert code == EXIT_USAGE
    assert captured.out == ""
    assert "jevcomp" in captured.err


def test_the_wired_group_satisfies_the_dump_invariant_or_says_what_is_missing():
    """The CLI's own invariant: every parser leaf needs a ``cli_contract.COMMANDS`` entry.

    ``tests/test_cli_contract_dump.py`` fails when a leaf is missing from
    ``cli_contract.COMMANDS`` ("a new command cannot ship without a contract"). The
    ``jev`` leaves therefore need five entries in that table — which lives in the
    CLI owner's ``cli_contract.py``, so this test reports the dependency instead of
    editing a file this package does not own. It passes as soon as they are added.
    """
    from skaldir.jev import cli as jev_cli_module

    contract = jev_cli_module._CONTRACT
    if contract is None:
        pytest.skip("this build has no cli_contract module: the group stands alone")
    missing = [
        f"jev.{verb}"
        for verb in ("kinds", "packs", "selftest", "decide", "history")
        if f"jev.{verb}" not in contract.COMMANDS
    ]
    if missing:
        pytest.skip(
            "awaiting the CLI owner's cli_contract.COMMANDS entries: " + ", ".join(missing)
        )
    for name in missing or ["jev.kinds"]:
        spec = contract.COMMANDS.get(name)
        if spec is not None:
            assert spec.evidence, f"{name} must declare default evidence"


def test_main_without_a_verb_prints_help(capsys):
    code = jev_cli.main([])
    assert code == EXIT_USAGE
    assert "jev" in capsys.readouterr().out


def test_argparse_usage_error_returns_two(capsys):
    code = jev_cli.main(["decide", "--kind", "not-a-kind"])
    assert code == EXIT_USAGE
    assert "invalid choice" in capsys.readouterr().err


def test_help_exits_zero(capsys):
    assert jev_cli.main(["--help"]) == EXIT_OK
    assert "jev" in capsys.readouterr().out
