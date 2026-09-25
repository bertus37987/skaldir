"""ART group — ``skaldir art preview|json``: the surface and the hand-over to ``cli.py``.

Two things are proved here:

1. **The group wires without touching a file this module does not own.** ``register_cli`` is
   idempotent and lands the group on the *real* ``build_parser()`` subparsers handle, and —
   with :data:`skaldir.art.CONTRACTS_SNIPPET` installed — the tree's own invariant holds:
   ``cli_contract.dump(parser)["unregistered"] == []``. No parser leaf ships without a
   contract.
2. **The snippet is the hand-over, literally.** It is parsed as Python and must name exactly
   the two command paths the group owns, with the layer/evidence/schema a contract entry
   needs — so pasting it into ``cli_contract.py`` cannot drift from the parser.

The global ``COMMANDS`` mutation is restored after every test (the same fixture
``tests/test_agent_loop_cli.py`` uses): until ``WIRING_SNIPPET`` is pasted into ``cli.py`` the
group is not in the shipped parser, and a stray entry would be a claim without a surface.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import pytest

from skaldir import art, cli, cli_contract


@pytest.fixture(autouse=True)
def contracts_snapshot():
    """Restore ``cli_contract.COMMANDS`` after each test (see the module docstring)."""
    before = dict(cli_contract.COMMANDS)
    try:
        yield
    finally:
        cli_contract.COMMANDS.clear()
        cli_contract.COMMANDS.update(before)


def _subparsers(parser: argparse.ArgumentParser) -> argparse.Action:
    for action in parser._actions:  # noqa: SLF001 — argparse has no public API for this
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return action
    raise AssertionError("the parser has no subparsers action")


def test_the_standalone_parser_carries_both_verbs():
    parser = art.build_parser()
    preview = parser.parse_args(["preview", "--ascii"])
    assert preview.func is art.cmd_preview and preview.ascii is True
    listing = parser.parse_args(["json"])
    assert listing.func is art.cmd_json
    with pytest.raises(SystemExit):
        parser.parse_args(["nope"])


def test_register_cli_is_idempotent_and_wires_the_group():
    sub = _subparsers(cli.build_parser())
    first = art.register_cli(sub)
    second = art.register_cli(sub)          # a second wiring must not raise
    assert first is second
    assert art.GROUP in sub.choices


def test_the_real_parser_and_the_contract_agree_after_wiring():
    """The hand-over proof: wiring the group leaves no leaf without a contract."""
    parser = cli.build_parser()
    art.register_cli(_subparsers(parser))
    art.install_contract_entries()
    inventory = cli_contract.dump(parser)
    assert inventory["unregistered"] == []
    by_path = {item["command"]: item for item in inventory["commands"]}
    for path in ("art.preview", "art.json"):
        entry = by_path[path]
        assert entry["registered"] is True
        assert entry["layer"] == "COMPOSE"
        assert entry["default_evidence"], f"{path} declares no evidence"
        assert entry["data_schema"]["type"] == "object"
        assert 0 in entry["exit_codes"] and 1 in entry["exit_codes"]


def test_the_snippet_is_valid_python_and_names_the_same_paths():
    namespace: dict[str, Any] = {
        "_C": lambda layer, evidence, description, **kw: {
            "layer": layer, "evidence": evidence, "description": description, **kw},
        "_ev": lambda ref, kind="code", note="": {"ref": ref, "kind": kind, "note": note},
    }
    text = "COMMANDS = {\n" + art.CONTRACTS_SNIPPET + "}\n"
    exec(compile(text, "<hand-over>", "exec"), namespace)  # noqa: S102 — data, not code
    pasted = namespace["COMMANDS"]
    assert sorted(pasted) == ["art.json", "art.preview"]
    for entry in pasted.values():
        assert entry["layer"] == "COMPOSE"
        assert entry["evidence"] and entry["data_schema"]["type"] == "object"
    assert "register_cli" in art.WIRING_SNIPPET
    assert "src/skaldir/cli.py" in art.WIRING_SNIPPET


def test_install_is_idempotent_and_never_overwrites():
    """A second install adds nothing; an entry that already exists is left alone."""
    art.install_contract_entries()
    before = dict(cli_contract.COMMANDS)
    assert art.install_contract_entries() == []
    assert cli_contract.COMMANDS == before


def test_the_group_prints_the_preview_in_ascii_when_asked(capsys):
    assert art.main(["preview", "--ascii"]) == 0
    printed = capsys.readouterr().out
    assert printed.isascii() and "\x1b" not in printed
    for name in art.NAMES:
        assert name in printed
    assert "BLOCKED" in printed and "UNKNOWN" in printed


def test_the_group_dumps_the_vocabulary_as_json(capsys):
    assert art.main(["json", "--ascii"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert len(document["activities"]) == len(art.NAMES)
    assert {row["activity"] for row in document["activities"]} == set(art.NAMES)
    assert document["frame_ms"] == art.FRAME_INTERVAL_MS
    for row in document["activities"]:
        assert row["purpose"] and row["when"]
        assert row["frames"] or row["ascii_frames"]
        assert row["line_width"] <= art.LINE_BUDGET


def test_an_unknown_verb_is_a_usage_exit_not_a_traceback():
    assert art.main(["frobnicate"]) == art.EXIT_USAGE
