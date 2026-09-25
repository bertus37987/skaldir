"""`--section-budget NAME=CHARS` must not silently no-op on an absent section.

When a caller pins a budget for a section that is not present in the build (e.g.
``memory`` with no memory.db in a fresh runtime), the override does nothing and
today nothing says so. The override becomes a warning naming the section, so a
budget that was never applied cannot read as "applied and small".
"""

from __future__ import annotations

import argparse
from pathlib import Path

from skaldir import prompt_builder_cli as pcli


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    pcli.build_prompt_parser(sub)
    return parser


def test_section_budget_on_an_absent_section_warns_it_was_not_applied(
    tmp_path: Path, capsys
) -> None:
    """A budget for a section the build does not carry is reported, not ignored."""
    runtime = tmp_path / "rt"
    runtime.mkdir()
    parser = _parser()
    args = parser.parse_args(
        [
            "prompt",
            "build",
            "--runtime",
            str(runtime),
            "--project",
            str(tmp_path),  # no AGENTS.md/SOUL.md here either
            "--section-budget",
            "memory=100",
        ]
    )
    code = args.func(args)
    captured = capsys.readouterr()
    assert "memory" in captured.err, captured.err
    assert "section-budget" in captured.err or "not applied" in captured.err
    # a usage error is the wrong answer: the build ran, only the budget was moot
    assert code != 1


def test_section_budget_on_a_present_section_stays_silent(tmp_path: Path, capsys) -> None:
    """A budget for a section that IS there must not claim it was ignored."""
    runtime = tmp_path / "rt"
    runtime.mkdir()
    parser = _parser()
    args = parser.parse_args(
        [
            "prompt",
            "build",
            "--runtime",
            str(runtime),
            "--section-budget",
            "identity=100",
        ]
    )
    args.func(args)
    captured = capsys.readouterr()
    assert "identity" not in captured.err or "not applied" not in captured.err
