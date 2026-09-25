"""``skaldir code …`` — the CLI surface, its envelope, its contract entries, its exit codes.

The group is wired by ``register_cli(subparsers)``; these tests drive it through its own
standalone parser *and* through a real ``subparsers`` handle, so what is measured is the
verb, its exit code and its output — not a handler called directly.

Four rules the verbs are held to:

* **every leaf has a contract entry** (``cli_contract.COMMANDS``), installed by the group's
  own ``register_cli`` so wiring the group cannot forget it;
* **no success without evidence** — every verb publishes evidence with its payload;
* **a blocked answer exits 3** and names what is missing (the diagnostics and symbols verbs
  are the two that can honestly be blocked);
* **a search that finds nothing exits 0** — an empty result is data, not a failure.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from contextlib import redirect_stdout

import pytest

from skaldir import cli_contract
from skaldir import codectx as C
from skaldir import codectx_cli as CLI
from skaldir.codectx_cli import register_cli

VERBS = ("map", "search", "symbols", "diagnostics", "changed", "blame", "pack", "tools", "cache")


@pytest.fixture
def workspace(tmp_path):
    from test_codectx import make_workspace  # the same proof workspace the module tests use

    return make_workspace(tmp_path / "ws")


@pytest.fixture
def state(tmp_path):
    directory = tmp_path / "state"
    directory.mkdir()
    return directory


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


@pytest.fixture
def env(monkeypatch, workspace, state):
    monkeypatch.setenv("SKALDIR_CODECTX_STATE", str(state))
    monkeypatch.chdir(workspace)
    return workspace


def leaves(group) -> dict:
    """``{verb: parser}`` of one group's leaf commands."""
    action = next(item for item in group._actions if isinstance(item, argparse._SubParsersAction))
    return dict(action.choices)


def run(argv: list[str]) -> tuple[int, str]:
    """Drive the standalone CLI and capture stdout."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = CLI.main(argv)
    return code, buffer.getvalue()


# ------------------------------------------------------------------ registration


def test_register_cli_adds_every_verb_and_is_idempotent(subparsers):
    group = register_cli(subparsers)
    assert set(leaves(group)) == set(VERBS)
    again = register_cli(subparsers)
    assert again is group, "a second call must return the group, not raise a conflict"
    assert set(leaves(again)) == set(VERBS)


def test_register_cli_extends_a_code_group_another_block_created(subparsers):
    """The `code` group is shared with the H8 block (``code run``/``code explain``).

    Whichever registers first creates the group; the other must **attach** its verbs to the
    same action instead of returning early (which would drop them while still installing
    their contract entries — a lie in the contract dump).
    """
    other = subparsers.add_parser("code", help="the other block's group")
    other_verbs = other.add_subparsers(dest="code_command", required=True)
    other_verbs.add_parser("run", help="the other block's verb")

    group = register_cli(subparsers)
    assert group is other
    assert set(leaves(group)) == set(VERBS) | {"run"}
    assert set(CLI.proposed_contract_entries()) <= set(cli_contract.COMMANDS)


def test_register_cli_accepts_a_parser_with_a_subparsers_action():
    outer = argparse.ArgumentParser(prog="skaldir")
    outer.add_subparsers(dest="command")
    group = register_cli(outer)
    assert set(leaves(group)) == set(VERBS)


def test_every_leaf_gets_its_contract_entry_from_the_group_itself(subparsers):
    register_cli(subparsers)
    installed = CLI.proposed_contract_entries()
    assert set(installed) == {f"code.{verb}" for verb in VERBS}
    for path, entry in installed.items():
        # equal, not identical: proposed_contract_entries() builds the table on every call
        assert cli_contract.COMMANDS[path] == entry, "the group installs its own entries"
        contract = cli_contract.spec_for(path)
        assert contract.layer in set(cli_contract.LAYERS) | {"kernel"}
        assert contract.evidence, "a contract without evidence is a success without a claim"
        assert contract.exit_codes


def test_the_group_is_discoverable_by_the_contract_dump(subparsers):
    register_cli(subparsers)
    for verb in VERBS:
        assert cli_contract.registered(f"code.{verb}")


def test_the_help_lists_every_verb(capsys):
    code, out = run(["--help"])
    assert code == 0
    text = out + capsys.readouterr().out
    for verb in VERBS:
        assert verb in text


# ------------------------------------------------------------------- the verbs


def test_map_prints_the_text_and_exits_zero(env):
    code, out = run(["map", "--tokens", "400"])
    assert code == 0
    assert "repo map" in out
    assert "src/app/main.py" in out


def test_map_json_is_one_document_with_the_update_counters(env):
    code, out = run(["map", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["schema"] == C.SCHEMA
    assert payload["update"]["reused"] + payload["update"]["reopened"] > 0
    assert payload["map"]["tokens"] <= payload["map"]["budget_tokens"] + 40


def test_a_second_map_call_reuses_the_cache(env):
    run(["map", "--json"])
    code, out = run(["map", "--json"])
    assert code == 0
    assert json.loads(out)["update"]["reopened"] == 0


def test_search_hit_exits_zero_and_a_miss_does_too(env):
    hit, out = run(["search", "def render_widget", "--json"])
    assert hit == 0
    assert json.loads(out)["count"] >= 1

    miss, empty = run(["search", "def nope_never_here", "--json"])
    assert miss == 0, "an empty result is data, not a failure"
    payload = json.loads(empty)
    assert payload["count"] == 0
    assert payload["files_scanned"] > 0


def test_an_invalid_regex_exits_one(env):
    code, out = run(["search", "def (", "--json"])
    assert code == 1
    assert json.loads(out)["status"] == "usage"


def test_a_search_outside_the_workspace_exits_three(env):
    code, _out = run(["search", "def ", "--path", "../..", "--json"])
    assert code == 3


def test_symbols_uses_ast_for_python(env):
    code, out = run(["symbols", "src/app/main.py", "--json"])
    assert code == 0
    payload = json.loads(out)
    names = {item["name"] for row in payload["files"] for item in row["symbols"]}
    assert {"Runner", "entry_point"} <= names


def test_symbols_without_an_index_exits_three_and_names_the_tool(env, monkeypatch):
    monkeypatch.setattr(C, "_ctags_available", lambda: "")
    code, out = run(["symbols", "src/app/flow.ts", "--json"])
    assert code == 3
    payload = json.loads(out)
    assert payload["status"] == "blocked"
    assert payload["needed"]


def test_diagnostics_exit_zero_when_the_linter_answers(env):
    if not C.probe_linter("ruff")["available"]:
        pytest.skip("ruff is not installed here")
    code, out = run(["diagnostics", "src/app/main.py", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["files_checked"] == 1
    assert payload["linter"]["available"] is True


def test_diagnostics_exit_three_when_nothing_can_look(env, monkeypatch):
    monkeypatch.setattr(
        C,
        "probe_linter",
        lambda name="ruff": {
            "name": name,
            "available": False,
            "argv": [],
            "reason": "ruff is not reachable",
            "needed": ["ruff (pip install ruff)"],
        },
    )
    code, out = run(["diagnostics", "src/app/main.py", "--json"])
    assert code == 3
    payload = json.loads(out)
    assert payload["status"] == "blocked"
    assert payload["findings"] == []
    assert "ruff" in " ".join(payload["needed"])


def test_changed_and_blame_answer_from_git(env):
    code, out = run(["changed", "--json"])
    assert code == 0
    assert json.loads(out)["status"] == "ok"

    code, out = run(["blame", "src/app/main.py", "1", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["status"] == "ok"
    assert payload["text"] == "import os"


def test_blame_of_a_missing_file_exits_five(env):
    code, out = run(["blame", "src/app/nope.py", "1", "--json"])
    assert code == 5
    assert json.loads(out)["status"] == "not_found"


def test_pack_text_is_exactly_the_fingerprinted_bytes(env):
    code, text = run(["pack", "widget renderer", "--text"])
    assert code == 0
    code, out = run(["pack", "widget renderer", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["block"]["text"].strip() == text.strip()
    digest = hashlib.sha256(payload["block"]["text"].encode("utf-8")).hexdigest()
    assert digest == payload["fingerprint"]


def test_pack_is_byte_stable_across_calls(env):
    _code, first = run(["pack", "widget renderer", "--json"])
    _code, second = run(["pack", "widget renderer", "--json"])
    assert json.loads(first)["fingerprint"] == json.loads(second)["fingerprint"]


def test_tools_lists_the_declared_surface(env):
    code, out = run(["tools", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert {row["verb"] for row in payload["verbs"]} == set(C.VERB_NAMES)
    for row in payload["verbs"]:
        assert row["contract"]["timeout_s"] > 0
        assert row["contract"]["truncation"]["max_bytes"] > 0
        assert row["contract"]["permissions"]["network"]["mode"] == "none"


def test_cache_reports_and_clears_the_map_cache(env, state):
    run(["map", "--json"])
    cached = sorted(state.glob("map-*.json"))
    assert cached, "the map cache lands in SKALDIR_CODECTX_STATE"

    code, out = run(["cache", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["exists"] is True
    assert payload["entries"] > 0

    code, out = run(["cache", "--clear", "--json"])
    assert code == 0
    assert json.loads(out)["cleared"]
    assert not sorted(state.glob("map-*.json"))


def test_an_unknown_verb_is_a_usage_error(capsys):
    code = CLI.main(["nope"])
    assert code == 1, "argparse's own choice error is reported as the CLI's usage code"
    assert "invalid choice" in capsys.readouterr().err


def test_the_verbs_never_raise_for_a_request(env):
    for argv in (["map"], ["changed"], ["tools"], ["cache"], ["search", "def "]):
        code, _out = run([*argv, "--json"])
        assert isinstance(code, int)
