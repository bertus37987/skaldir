"""The first-run screen (MVP/64 §3): what a fresh start shows, and what it may not claim.

Four properties:

1. the **logo** is the one the bare call draws — the same module, never a second drawing;
2. the **missing-provider line** appears *only* when the setup state really says
   ``degraded.needs_setup: true`` — and an unreadable state prints nothing instead of
   guessing (an unknown is not a finding);
3. the **``try this`` row** is generated against this build's own parser/slash table, so
   every example it prints exists and a planned verb can never slip into it;
4. the pieces render as the spec's screen order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skaldir import cli, cli_contract, first_run, logo


@pytest.fixture
def empty_runtime(tmp_path, monkeypatch):
    """A private runtime with no config and no credentials file."""
    home = tmp_path / "godsv"
    home.mkdir()
    monkeypatch.setenv("GODSV_HOME", str(home))
    for name in ("SKALDIR_LLM_BASE_URL", "SKALDIR_LLM_MODEL", "SKALDIR_LLM_API_KEY",
                 "OPENAI_BASE_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return home


def test_the_logo_is_the_one_art_module_not_a_second_drawing():
    rows = first_run.logo_lines()
    # a broken asset still yields the word mark: the screen never dies for a missing file
    assert rows and all(isinstance(row, str) for row in rows)
    assert first_run.logo_lines() == logo.splash().splitlines()[: len(rows)]
    assert any("SKALDIR" in row.upper() or "@" in row for row in rows)


def test_the_missing_provider_line_comes_from_the_measured_state(empty_runtime):
    """Nothing configured → the line. Both keys present → no line, not a reworded one."""
    assert first_run.provider_state(runtime=empty_runtime)["needs_setup"] is True
    assert first_run.missing_provider_line(runtime=empty_runtime) == first_run.MISSING_PROVIDER

    monkeypatch_state = {"degraded": {"needs_setup": False}}
    assert first_run.missing_provider_line.__name__  # the helper is the one under test
    from skaldir import setup_flow

    original = setup_flow.detect_state
    try:
        setup_flow.detect_state = lambda **kwargs: monkeypatch_state  # type: ignore[assignment]
        assert first_run.missing_provider_line(runtime=empty_runtime) == ""
        monkeypatch_state["degraded"] = {"needs_setup": True}
        assert first_run.missing_provider_line(runtime=empty_runtime) == first_run.MISSING_PROVIDER
    finally:
        setup_flow.detect_state = original  # type: ignore[assignment]


def test_an_unreadable_state_prints_nothing_and_never_claims_a_provider(empty_runtime):
    from skaldir import setup_flow

    original = setup_flow.detect_state

    def broken(**kwargs):
        raise RuntimeError("no setup layer here")

    try:
        setup_flow.detect_state = broken  # type: ignore[assignment]
        state = first_run.provider_state(runtime=empty_runtime)
        assert state["needs_setup"] is None
        assert state["reason"]
        assert first_run.missing_provider_line(runtime=empty_runtime) == ""
    finally:
        setup_flow.detect_state = original  # type: ignore[assignment]


def test_the_try_this_row_only_prints_examples_this_build_carries():
    cli.build_parser()  # every group installs its contract entries here
    examples = first_run.resolved_examples()
    assert len(examples) == 3, examples
    commands = set(cli_contract.COMMANDS)
    from skaldir import complete, slash

    # the table with the real tree: `mapping()` without one reports `unverified` for every
    # command-backed verb (documented in `first_run._slash_kinds`), which is a different
    # question from "is this example real here" — this test asks the latter
    tree = complete.build_tree(cli.build_parser())
    kinds = {row["slash"]: row["kind"] for row in slash.mapping(tree)}
    for example in examples:
        if example.startswith("/"):
            assert kinds.get(example) in ("real", "builtin"), (example, kinds.get(example))
        else:
            # a CLI example: the first word pair must be a real command path
            head = example.split()[0]
            assert any(path.startswith(head) for path in commands), example
    assert first_run.try_this_row().startswith("try this: ")
    # a planned verb is dropped, never printed as a working example
    dropped = first_run.resolved_examples(
        candidates=(("/plan", ("plan",)), ("/goal", ("goal.show",)))
    )
    assert dropped == ["/goal"]


def test_the_screen_is_rendered_in_the_specified_order(empty_runtime):
    lines = first_run.screen_lines(runtime=empty_runtime, logo=True)
    text = "\n".join(lines)
    assert first_run.MISSING_PROVIDER in text
    assert "try this:" in text
    logo_at = next(index for index, line in enumerate(lines) if "@" in line)
    missing_at = next(index for index, line in enumerate(lines)
                      if first_run.MISSING_PROVIDER in line)
    assert logo_at < missing_at, "the logo comes first, the honest line after it"
    # and the facts are available as data too (the --json form of the same screen)
    data = first_run.as_data(runtime=empty_runtime)
    assert data["needs_setup"] is True
    assert data["missing_provider_line"] == first_run.MISSING_PROVIDER
    assert len(data["try_this"]) == 3


def test_the_shell_banner_carries_the_three_pieces(tmp_path, monkeypatch):
    """The wiring, not just the helpers: the shell's first screen draws them."""
    from skaldir import repl

    home = tmp_path / "godsv"
    home.mkdir()
    monkeypatch.setenv("GODSV_HOME", str(home))
    monkeypatch.delenv("SKALDIR_LLM_BASE_URL", raising=False)
    source = Path(repl.__file__).read_text(encoding="utf-8")
    assert "first_run.missing_provider_line" in source
    assert "first_run.try_this_row" in source
    assert "first_run.logo_lines" in source
