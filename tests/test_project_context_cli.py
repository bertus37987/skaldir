"""``skaldir context …`` — the CLI surface, its envelope and its contract entries.

The group is wired by ``register_cli(subparsers)``; these tests drive it through its own
standalone parser and through a real ``subparsers`` handle, so what is measured is the verb, its
exit code and its output — not a handler called directly.

Two rules the verbs are held to:

* an **empty** context exits 5 (not 0) unless ``--allow-empty`` says the caller accepts one, and
  the JSON payload is emitted *before* that refusal, so a machine caller still sees the empty
  block, its fingerprint and the warning;
* nothing about the resolution stays invisible — a refused symlink, an overridden rule and a
  dropped entry each appear in ``context explain``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os

import pytest
from test_project_context import make_workspace, write  # noqa: E402 — shared fixture workspace

from skaldir import cli_contract
from skaldir import project_context as P
from skaldir import project_context_cli as C
from skaldir.project_context_cli import register_cli


@pytest.fixture
def workspace(tmp_path):
    """The same proof workspace the core tests use: root + one nested level, cwd two deep."""
    _root, workdir = make_workspace(tmp_path)
    return workdir


@pytest.fixture(autouse=True)
def _restore_contract_commands():
    """Registering this group must not leak into another test file's ``COMMANDS`` view."""
    before = dict(cli_contract.COMMANDS)
    yield
    cli_contract.COMMANDS.clear()
    cli_contract.COMMANDS.update(before)


@pytest.fixture
def subparsers():
    """A subparsers handle like the product CLI's ``build_parser()`` passes in."""
    parser = argparse.ArgumentParser(prog="skaldir")
    return parser.add_subparsers(dest="command")


def leaves(group) -> dict:
    """``{verb: parser}`` of one group's leaf commands."""
    action = next(
        item for item in group._actions if isinstance(item, argparse._SubParsersAction)
    )
    return dict(action.choices)


def run(argv, capsys) -> tuple[int, str, str]:
    """Run the standalone entry point; returns ``(exit_code, stdout, stderr)``."""
    code = C.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --------------------------------------------------------------------------- #
# wiring
# --------------------------------------------------------------------------- #


class TestWiring:
    def test_register_cli_adds_the_context_group_with_its_three_verbs(self, subparsers):
        group = register_cli(subparsers)
        assert group.prog.endswith("context")
        assert sorted(leaves(group)) == ["explain", "fingerprint", "show"]

    def test_every_leaf_carries_its_dotted_command_path_and_the_json_flag(self, subparsers):
        group = register_cli(subparsers)
        for verb, parser in leaves(group).items():
            assert parser.get_default("command_path") == f"context.{verb}"
            parsed = parser.parse_args(["--json"])
            assert parsed.command_path == f"context.{verb}"
            assert parsed.json is True
            assert "--dir" in {option for action in parser._actions for option in
                               action.option_strings}

    def test_register_cli_is_idempotent(self, subparsers):
        first = register_cli(subparsers)
        second = register_cli(subparsers)  # argparse would raise on a double add
        assert first is second

    def test_register_cli_accepts_a_parser_that_owns_a_subparsers_action(self):
        parser = argparse.ArgumentParser(prog="skaldir")
        sub = parser.add_subparsers(dest="command")
        group = register_cli(sub)
        assert "show" in leaves(group)

    def test_the_standalone_parser_has_the_same_verbs(self):
        parser = C.build_parser()
        action = next(
            item for item in parser._actions if isinstance(item, argparse._SubParsersAction)
        )
        assert sorted(action.choices) == ["explain", "fingerprint", "show"]


class TestContractEntries:
    def test_the_entries_are_installed_once_and_are_valid(self):
        for path in ("context.show", "context.explain", "context.fingerprint"):
            cli_contract.COMMANDS.pop(path, None)
        added = C.register_contract_entries()
        assert set(added) >= {"context.show", "context.explain", "context.fingerprint"}
        for path in ("context.show", "context.explain", "context.fingerprint"):
            spec = cli_contract.COMMANDS[path]
            assert spec.layer in cli_contract.LAYERS
            assert spec.data_schema and spec.data_schema.get("type") == "object"
            assert spec.exit_codes and all(code in range(9) for code in spec.exit_codes)
            assert spec.evidence
            assert spec.mutates is False  # every verb here is read-only
        assert C.register_contract_entries() == []  # idempotent

    def test_register_cli_installs_the_entries(self, subparsers):
        for path in ("context.show", "context.explain", "context.fingerprint"):
            cli_contract.COMMANDS.pop(path, None)
        register_cli(subparsers)
        assert "context.show" in cli_contract.COMMANDS
        assert cli_contract.registered("context.explain") is True


# --------------------------------------------------------------------------- #
# context show
# --------------------------------------------------------------------------- #


class TestShow:
    def test_text_mode_prints_the_block_verbatim_and_the_provenance_on_stderr(
        self, workspace, capsys
    ):
        code, out, err = run(["show", "--dir", str(workspace), "--format", "text"], capsys)
        context = P.build(workspace)
        assert code == 0
        assert out == context.block  # byte for byte, the paste is the block
        assert context.fingerprint in err
        assert "level 1" in err and "nested/AGENTS.md" in err
        assert "wins over AGENTS.md" in err  # the override is announced, not hidden

    def test_json_mode_is_one_document_with_the_block_and_the_evidence(self, workspace, capsys):
        code, out, _err = run(
            ["show", "--dir", str(workspace), "--max-chars", "8000", "--json"], capsys
        )
        payload = json.loads(out)
        context = P.build(workspace)
        assert code == 0
        assert payload["block"] == context.block
        assert payload["fingerprint"] == context.fingerprint
        assert payload["fingerprint"] == payload["evidence"]["fingerprint"]
        assert payload["empty"] is False
        assert [row["path"] for row in payload["sources"]] == [
            "nested/AGENTS.md",
            "SKALDIR.md",
            "SOUL.md",
            "AGENTS.md",
        ]
        assert payload["max_chars"] == 8000

    def test_a_capped_run_reports_the_truncation(self, workspace, capsys):
        code, out, _err = run(["show", "--dir", str(workspace), "--max-chars", "150"], capsys)
        payload = json.loads(out)
        assert code == 0
        assert payload["truncated"] is True
        assert payload["clipped_sections"] == ["rules"]
        assert "role" in payload["omitted_sections"]
        assert "…[" in payload["block"]

    def test_nothing_found_exits_5_but_still_emits_the_payload(self, tmp_path, capsys):
        empty = tmp_path / "nothing"
        empty.mkdir()
        code, out, err = run(["show", "--dir", str(empty), "--json"], capsys)
        payload = json.loads(out)
        assert code == 5
        assert payload["empty"] is True and payload["sources"] == []
        assert "no project context file found" in payload["warnings"][0]
        assert "no project context file found" in err

    def test_allow_empty_turns_the_finding_into_an_accepted_empty_block(self, tmp_path, capsys):
        empty = tmp_path / "nothing"
        empty.mkdir()
        code, out, _err = run(
            ["show", "--dir", str(empty), "--allow-empty", "--format", "text"], capsys
        )
        assert code == 0
        assert out.startswith("<!-- project context")
        assert out.count("\n") == 1  # the header and nothing else


# --------------------------------------------------------------------------- #
# context explain
# --------------------------------------------------------------------------- #


class TestExplain:
    def test_the_json_report_names_the_ladder_the_winner_and_the_loser(self, workspace, capsys):
        code, out, _err = run(["explain", "--dir", str(workspace), "--json"], capsys)
        report = json.loads(out)
        assert code == 0
        assert report["ladder"]["checked_in_each_directory"] == list(P.FILENAME_LADDER)
        assert report["budget"]["funding_order"][0] == "rules"
        assert report["budget"]["planned"]["rules"] > 0
        assert [row["level"] for row in report["levels"]] == [0, 1, 2]

        override = [row for row in report["overrides"] if row["id"] == "R1"][0]
        assert override["winner"] == "SKALDIR.md"
        assert override["losers"] == ["AGENTS.md"]
        assert override["losing_text"] == ["never push to main directly"]
        assert override["text"].startswith("never push to main directly;")

    def test_the_json_report_shows_every_source_and_every_refusal(self, tmp_path, capsys):
        root, workdir = make_workspace(tmp_path)
        outside = write(tmp_path / "outside.md", "## Rules\n- EVIL: not from here\n")
        os.symlink(outside, root / "CLAUDE.md")
        code, out, _err = run(["explain", "--dir", str(workdir), "--json"], capsys)
        report = json.loads(out)
        assert code == 0
        refused = {row["path"]: row for row in report["refused"]}
        assert refused["CLAUDE.md"]["status"] == P.STATUS_REFUSED_ESCAPE
        assert "outside the workspace" in refused["CLAUDE.md"]["reason"]
        assert "EVIL" not in report["block"]
        assert all(row["status"] == "used" for row in report["sources"])

    def test_text_mode_prints_the_resolution_as_a_report(self, workspace, capsys):
        code, out, _err = run(
            ["explain", "--dir", str(workspace), "--format", "text"], capsys
        )
        assert code == 0
        assert "ladder" in out and "SKALDIR.md > SOUL.md > AGENTS.md" in out
        assert "workspace" in out and "levels" in out
        assert "overrides" in out
        assert "SKALDIR.md wins over AGENTS.md" in out
        assert "never push to main directly" in out  # what the loser said
        assert "fingerprint" in out

    def test_an_empty_ladder_exits_5_with_the_report(self, tmp_path, capsys):
        empty = tmp_path / "nothing"
        empty.mkdir()
        code, out, err = run(["explain", "--dir", str(empty), "--json"], capsys)
        report = json.loads(out)
        assert code == 5
        assert report["sources"] == []
        assert "no project context file found" in err

    def test_a_prose_file_is_taken_whole_and_the_report_names_it(self, tmp_path, capsys):
        work = tmp_path / "prose"
        write(work / "CLAUDE.md", "# Vendor standard\n\n## Naming\n- tabs are wrong\n")
        code, out, _err = run(["explain", "--dir", str(work), "--json"], capsys)
        report = json.loads(out)
        assert code == 0
        assert report["fallback_files"][0].endswith("CLAUDE.md")
        assert "tabs are wrong" in report["block"]
        assert any("injected whole as one" in warning for warning in report["warnings"])


# --------------------------------------------------------------------------- #
# context fingerprint
# --------------------------------------------------------------------------- #


class TestFingerprint:
    def test_the_fingerprint_matches_the_module_and_the_files_on_disk(self, tmp_path, capsys):
        root, workdir = make_workspace(tmp_path)
        code, out, _err = run(["fingerprint", "--dir", str(workdir), "--json"], capsys)
        payload = json.loads(out)
        context = P.build(workdir)
        assert code == 0
        assert payload["fingerprint"] == context.fingerprint
        assert payload["fingerprint_short"] == context.fingerprint[:16]
        for row in payload["sources"]:
            on_disk = hashlib.sha256((root / row["path"]).read_bytes()).hexdigest()
            assert row["sha256"] == on_disk

    def test_it_is_stable_and_moves_with_the_content(self, tmp_path, capsys):
        root, workdir = make_workspace(tmp_path)
        _code, first, _err = run(["fingerprint", "--dir", str(workdir), "--json"], capsys)
        _code, again, _err = run(["fingerprint", "--dir", str(workdir), "--json"], capsys)
        assert json.loads(first)["fingerprint"] == json.loads(again)["fingerprint"]

        write(root / "SKALDIR.md", "## Rules\n- R1: something else entirely\n")
        _code, changed, _err = run(["fingerprint", "--dir", str(workdir), "--json"], capsys)
        assert json.loads(changed)["fingerprint"] != json.loads(first)["fingerprint"]

    def test_text_mode_lists_the_digests(self, workspace, capsys):
        code, out, _err = run(
            ["fingerprint", "--dir", str(workspace), "--format", "text"], capsys
        )
        assert code == 0
        assert out.startswith("fingerprint        ")
        assert "fingerprint_short" in out
        assert out.count("level ") == 4


# --------------------------------------------------------------------------- #
# usage and refusals
# --------------------------------------------------------------------------- #


class TestUsage:
    def test_a_directory_that_does_not_exist_is_not_found(self, tmp_path, capsys):
        code, _out, _err = run(["show", "--dir", str(tmp_path / "missing")], capsys)
        assert code == 5

    def test_a_missing_verb_is_usage(self, capsys):
        assert C.main([]) == 1

    def test_an_unknown_flag_is_usage(self, tmp_path, capsys):
        code, _out, err = run(["show", "--dir", str(tmp_path), "--nope"], capsys)
        assert code == 1
        assert "unrecognized arguments" in err

    @pytest.mark.parametrize("verb", ["show", "explain", "fingerprint"])
    def test_every_verb_takes_json(self, verb, workspace, capsys):
        code, out, _err = run([verb, "--dir", str(workspace), "--json"], capsys)
        assert code == 0
        assert json.loads(out)  # one parseable JSON document on stdout
