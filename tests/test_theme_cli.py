"""``skaldir style`` — the CLI group over ``theme``/``render``/``logo``.

The group is wired by the CLI owner (``register_cli(subparsers)``); these tests pin the
hook, the three verbs, the ``--no-color`` behaviour on a real TTY, and — most
importantly — that the handlers can produce an ``ok`` envelope **with evidence**
through the product contract (``build_envelope`` downgrades a success without evidence).
"""

from __future__ import annotations

import io
import json

import pytest

from skaldir import cli_contract, logo, theme


class FakeTTY(io.StringIO):
    def __init__(self, encoding: str = "utf-8") -> None:
        super().__init__()
        self._encoding = encoding

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return self._encoding

    def isatty(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _restore_theme():
    before = theme.get()
    yield
    theme.set(before)


def _leaves(parser):
    return [path for path, _sub in cli_contract.leaf_commands(parser)]


# --------------------------------------------------------------------------- #
# the hook                                                                     #
# --------------------------------------------------------------------------- #

def test_the_group_offers_the_three_verbs():
    assert sorted(_leaves(theme.build_parser())) == ["logo", "palette", "preview"]


def test_register_cli_adds_the_group_and_sets_the_command_paths():
    import argparse

    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command", required=True)
    returned = theme.register_cli(sub)
    assert returned is not None
    assert "style" in sub.choices
    args = parser.parse_args(["style", "logo", "mark", "--no-color"])
    assert args.command_path == "style.logo"
    assert args.func is theme.cmd_logo


def test_register_cli_is_idempotent():
    """Wiring twice must not raise "conflicting subparser: style" (sibling tests wire too)."""
    import argparse

    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command", required=True)
    first = theme.register_cli(sub)
    second = theme.register_cli(sub)
    assert first is second
    assert list(sub.choices) == ["style"]


def test_the_shared_flags_are_attached_once():
    """``add_common_flags`` must not double-add ``--json`` (argparse would refuse)."""
    parser = theme.build_parser()
    for path, leaf in cli_contract.walk_parsers(parser):
        if cli_contract._subparsers_action(leaf) is not None:
            continue
        options = [option for action in leaf._actions for option in action.option_strings]
        assert options.count("--json") == 1, path
        assert options.count("--no-color") == 1, path


# --------------------------------------------------------------------------- #
# the verbs                                                                    #
# --------------------------------------------------------------------------- #

def test_preview_prints_the_whole_visual_language(capsys):
    assert theme.main(["preview"]) == 0
    out = capsys.readouterr().out
    # every section is opened by a bar, and the label of a bar is upper-cased
    for expected in ("Skaldir CLI visual language", "SECTIONS", "STATUS LINES", "KEY/VALUE",
                     "TABLE", "STEPS", "BOX", "LEGEND", "S K A L D I R"):
        assert expected in out, expected
    assert "-- SECTIONS " in out and "-- BOX " in out
    assert "✓" not in out and "·" not in out, "the preview must stay ASCII on a pipe"
    assert "\x1b" not in out, "pytest captures to a pipe: the preview must be plain"


def test_palette_prints_the_ramp_tokens_and_marks(capsys):
    assert theme.main(["palette"]) == 0
    out = capsys.readouterr().out
    assert "Palette" in out and "grey" in out and "token" in out
    for token in ("ok", "fail", "blocked", "warn", "heading", "muted"):
        assert token in out
    assert "+" in out and "x" in out and "#" in out        # the ASCII status marks


def test_logo_prints_the_banner_by_default(capsys):
    assert theme.main(["logo"]) == 0
    out = capsys.readouterr().out
    assert "@" in out and out.isascii()
    assert len(out.splitlines()) >= logo.BANNER_COLUMNS // 2


def test_logo_mark_and_splash(capsys):
    assert theme.main(["logo", "mark"]) == 0
    mark = capsys.readouterr().out
    assert mark.strip() and mark.isascii()
    assert theme.main(["logo", "splash"]) == 0
    splash = capsys.readouterr().out
    assert "S K A L D I R" in splash
    assert theme.main(["logo", "header"]) == 1          # not a valid choice → usage


def test_no_color_wins_over_a_tty_stdout(monkeypatch, capsys):
    """The flag on a real-looking TTY: colour on by default, nothing left with the flag."""
    fake = FakeTTY()
    monkeypatch.setattr(theme.sys, "stdout", fake)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    theme.main(["preview"])
    colored = fake.getvalue()
    assert "\x1b[38;2;255;255;255;1mSkaldir CLI visual language\x1b[0m" in colored
    assert "✓" in colored and "─" in colored

    plain_fake = FakeTTY()
    monkeypatch.setattr(theme.sys, "stdout", plain_fake)
    theme.main(["preview", "--no-color"])
    plain = plain_fake.getvalue()
    assert "\x1b" not in plain and plain.isascii()
    assert "+ " in plain


def test_logo_ascii_flag_forces_the_plain_rendering_on_a_tty(monkeypatch):
    fake = FakeTTY()
    monkeypatch.setattr(theme.sys, "stdout", fake)
    monkeypatch.setenv("TERM", "xterm-256color")
    theme.main(["logo", "banner", "--ascii"])
    assert "\x1b" not in fake.getvalue()


# --------------------------------------------------------------------------- #
# the envelope                                                                 #
# --------------------------------------------------------------------------- #

def test_json_mode_emits_one_envelope_with_evidence(capsys):
    parser = theme.build_parser()
    args = parser.parse_args(["preview", "--json"])
    code = cli_contract.run_command(args, args.func)
    captured = capsys.readouterr()
    assert code == 0
    assert len([line for line in captured.out.splitlines() if line.strip()]) == 1, (
        "stdout carries exactly one JSON document"
    )
    envelope = json.loads(captured.out)
    assert envelope["ok"] is True and envelope["exit_code"] == 0
    assert envelope["command"] == "style.preview"
    assert envelope["evidence"], "no success without evidence"
    assert envelope["data"]["capabilities"]["mode"] == "plain"
    assert "Skaldir CLI visual language" in captured.err, "human text belongs on stderr"


def test_json_mode_reports_the_art_file_for_logo(capsys):
    parser = theme.build_parser()
    args = parser.parse_args(["logo", "mark", "--json"])
    assert cli_contract.run_command(args, args.func) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"]["art"] == "mark"
    assert envelope["data"]["path"].endswith(logo.ART_FILENAME)
    assert envelope["data"]["grids"] == ["banner", "mark"]
    assert any(item["kind"] == "file" for item in envelope["evidence"])


def test_palette_json_lists_the_shades(capsys):
    parser = theme.build_parser()
    args = parser.parse_args(["palette", "--json"])
    assert cli_contract.run_command(args, args.func) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"]["shades"] == theme.SHADES
    assert envelope["data"]["capabilities"]["grey_levels"]["paper"] == 255


def test_a_missing_art_file_is_a_not_found_exit(monkeypatch, capsys):
    monkeypatch.setattr(logo, "art_candidates", lambda: [])
    parser = theme.build_parser()
    args = parser.parse_args(["logo", "banner", "--json"])
    code = cli_contract.run_command(args, args.func)
    envelope = json.loads(capsys.readouterr().out)
    assert code == 5, "the art is missing: blocked-as-not-found, never a fake banner"
    assert envelope["ok"] is False
    assert envelope["exit_code"] == 5
    assert logo.ART_FILENAME in envelope["error"]["message"]
    assert envelope["error"]["message"].isascii(), "even an error stays ASCII in plain mode"
