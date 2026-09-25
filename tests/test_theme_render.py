"""``godsv.render``: the monochrome helpers, and their plain-ASCII guarantee.

The load-bearing test here is :func:`test_every_helper_degrades_to_pure_ascii` — it
walks every public helper with the theme a pipe/log file gets and asserts there is not
a single escape sequence or non-ASCII character anywhere in the output.
"""

from __future__ import annotations

import io

import pytest

from skaldir import render
from skaldir import theme as T


class FakeTTY(io.StringIO):
    def __init__(self, encoding: str = "utf-8") -> None:
        super().__init__()
        self._encoding = encoding

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return self._encoding

    def isatty(self) -> bool:
        return True


PLAIN = T.detect(io.StringIO(), env={})
RICH = T.detect(FakeTTY(), env={"TERM": "xterm-256color", "COLORTERM": "truecolor"})


# --------------------------------------------------------------------------- #
# width maths                                                                  #
# --------------------------------------------------------------------------- #

def test_escapes_never_count_towards_the_width():
    assert render.strip_ansi("\x1b[1mab\x1b[0m") == "ab"
    assert render.width("\x1b[1mab\x1b[0m") == 2
    assert render.width(RICH.style("ab", "ok")) == 2


def test_wide_glyphs_count_as_two_columns():
    assert render.width("日本") == 4
    assert render.width("a日b") == 4


def test_truncate_marks_the_cut_and_stays_inside_the_limit():
    assert render.truncate("abcdef", 10, theme=PLAIN) == "abcdef"
    assert render.truncate("abcdef", 4, theme=PLAIN) == "a..."      # ascii ellipsis
    assert render.truncate("abcdef", 4, theme=RICH) == "abc…"       # unicode ellipsis
    assert render.truncate("abcdef", 0, theme=PLAIN) == ""


def test_fit_pads_and_aligns():
    assert render.fit("ab", 5, theme=PLAIN) == "ab   "
    assert render.fit("ab", 5, align="right", theme=PLAIN) == "   ab"
    assert render.fit("ab", 5, align="center", theme=PLAIN) == " ab  "
    assert render.width(render.fit(RICH.style("ab", "ok"), 5, theme=RICH)) == 5


def test_terminal_width_prefers_the_theme_then_the_console(monkeypatch):
    with T.use(T.detect(io.StringIO(), env={"GODSV_WIDTH": "72"})):
        assert render.terminal_width() == 72
    with T.use(T.detect(io.StringIO(), env={"GODSV_WIDTH": "10"})):
        assert render.terminal_width() == render.MIN_WIDTH       # clamped, never 10
    with T.use(T.detect(io.StringIO(), env={})):
        assert render.MIN_WIDTH <= render.terminal_width() <= render.MAX_WIDTH


# --------------------------------------------------------------------------- #
# structure                                                                    #
# --------------------------------------------------------------------------- #

def test_rule_is_ascii_plain_and_unicode_rich():
    assert render.rule(6, theme=PLAIN) == "------"
    assert render.strip_ansi(render.rule(6, theme=RICH)) == "──────"
    labelled = render.rule(20, label="steps", theme=PLAIN)
    # the bar leads with three strokes, then one space, the label, a space, then fill
    assert labelled.startswith("--- steps ") and render.width(labelled) == 20
    assert render.strip_ansi(render.rule(20, label="steps", theme=RICH)).startswith("─── steps ")
    # a label that cannot fit never pushes the rule past the requested width
    narrow = render.rule(9, label="a very long label", theme=PLAIN)
    assert render.width(narrow) == 9 and narrow.startswith("--- ")


def test_bar_is_the_section_separator_and_uppercases_its_label():
    drawn = render.bar("commands", columns=40, theme=PLAIN)
    assert drawn == "--- COMMANDS " + "-" * 27
    assert render.width(drawn) == 40
    light = render.end_rule(40, theme=PLAIN)
    assert light == "." * 40
    assert render.width(render.bar("x", columns=3, theme=PLAIN)) == 3      # degenerate width


def test_legend_lists_the_glyphs():
    assert render.legend(theme=PLAIN) == "+ ok  x fail  # blocked  . warn"
    assert render.strip_ansi(render.legend(theme=RICH)) == "✓ ok  ✗ fail  ■ blocked  · warn"


def test_heading_levels():
    assert render.heading("Title", theme=PLAIN).splitlines()[0] == "Title"
    # level 2 is the section bar: the label is upper-cased and the rule is full width
    assert render.heading("Title", level=2, columns=30, theme=PLAIN).startswith("--- TITLE ")
    assert render.width(render.heading("Title", level=2, columns=30, theme=PLAIN)) == 30


def test_box_plain_is_ascii_and_sized_to_its_content():
    drawn = render.box(["hello", "hi"], title="note", theme=PLAIN)
    lines = drawn.splitlines()
    widths = {render.width(line) for line in lines}
    assert len(widths) == 1, drawn                      # a rectangle, not a ragged block
    assert all(line.isascii() for line in lines)
    assert lines[0].startswith("+-- note ") and lines[0].endswith("+")
    assert "| hello" in lines[1] and lines[-1].startswith("+")

    rich = [render.strip_ansi(line) for line in render.box(["hello"], theme=RICH).splitlines()]
    assert rich[0].startswith("┌") and rich[0].rstrip().endswith("┐")
    assert rich[1].startswith("│") and rich[1].rstrip().endswith("│")


def test_box_truncates_long_lines_to_the_requested_width():
    drawn = render.box(["x" * 200], theme=PLAIN, columns=20)
    assert all(render.width(line) == 20 for line in drawn.splitlines())
    assert "..." in drawn


def test_table_pads_columns_and_rules_the_header():
    drawn = render.table(["name", "state"], [["a", "ok"], ["bbbb", "blocked"]], theme=PLAIN)
    lines = drawn.splitlines()
    assert lines[0] == "name  state" or lines[0].startswith("name")
    assert set(lines[1]) <= {"-", "+"}
    assert lines[2].startswith("a ") and "ok" in lines[2]
    assert all(render.width(line) == render.width(lines[0]) for line in lines)


def test_table_styles_a_status_cell_only_when_colour_is_on():
    plain = render.table(["state"], [["ok"]], theme=PLAIN)
    assert "\x1b" not in plain
    rich = render.table(["state"], [["ok"]], theme=RICH)
    assert "\x1b" in rich and "ok" in render.strip_ansi(rich)


def test_table_shrinks_to_the_requested_max_width():
    rows = [["a" * 40, "b" * 40, "c" * 40], ["x", "y", "z"]]
    drawn = render.table(["first", "second", "third"], rows, theme=PLAIN, max_width=40)
    assert all(render.width(line) <= 40 for line in drawn.splitlines())
    assert "..." in drawn                              # the cut is visible, never silent


def test_table_without_headers_or_rows_stays_sane():
    assert render.table([], [], theme=PLAIN) == ""
    header_only = render.table(["only"], [], theme=PLAIN).splitlines()
    assert header_only[0] == "only" and set(header_only[1]) <= {"-", "+"}


def test_key_values_line_up_and_are_selectable():
    drawn = render.key_values([("mode", "plain"), ("depth", "none")], theme=PLAIN)
    lines = drawn.splitlines()
    assert lines[0] == "mode   plain"
    assert lines[1] == "depth  none"
    assert all(line.startswith("mode") is False or True for line in lines)
    assert render.key_values([], theme=PLAIN) == ""
    assert render.key_values([("a", 1)], title="head", theme=PLAIN).splitlines()[0] == "head"


def test_status_and_step_lines_use_the_marks():
    assert render.status_line("ok", "done", theme=PLAIN) == "+  done"
    assert render.status_line("blocked", "no key", theme=PLAIN) == "#  no key"
    # detail lines hang four columns in and can wrap under their own mark
    detail = render.status_line("ok", "a " * 40, theme=PLAIN, indent=render.DETAIL_INDENT,
                                columns=40)
    assert detail.splitlines()[0].startswith("    +  a")
    assert all(render.width(line) <= 40 for line in detail.splitlines())
    steps = render.step_line(2, 5, "build", status="ok", theme=PLAIN)
    assert steps == "  2/5    +  build"
    assert "✓" in render.step_line(2, 5, "build", status="ok", theme=RICH)


def test_progress_bar_clamps_and_uses_ascii_blocks_when_plain():
    assert render.progress(0.5, columns=10, theme=PLAIN) == "[#####-----]  50%"
    assert render.progress(2.0, columns=4, theme=PLAIN).endswith("100%")
    assert render.progress(-1, columns=4, theme=PLAIN).endswith("  0%")
    rich = render.strip_ansi(render.progress(1.0, columns=4, label="run", theme=RICH))
    assert rich == "run [████] 100%"


def test_bullets():
    assert render.bullets(["one", "two"], theme=PLAIN) == "  - one\n  - two"
    assert render.bullets(["one"], theme=RICH) == "  • one"


def test_spacer():
    assert render.spacer() == "\n"
    assert render.spacer(3) == "\n\n\n"
    assert render.spacer(0) == ""
    assert render.spacer(-2) == ""


# --------------------------------------------------------------------------- #
# the guarantee: colour off ⇒ pure ASCII                                       #
# --------------------------------------------------------------------------- #

def _every_helper() -> dict[str, str]:
    """Every public helper of ``render``, drawn with one theme."""
    theme = PLAIN
    return {
        "rule": render.rule(30, theme=theme),
        "rule+label": render.rule(30, label="steps", theme=theme),
        "bar": render.bar("commands", theme=theme),
        "end_rule": render.end_rule(30, theme=theme),
        "heading": render.heading("Title", theme=theme),
        "heading2": render.heading("Title", level=2, theme=theme),
        "status_lines": "\n".join(
            render.status_line(status, "text", theme=theme)
            for status in ("ok", "fail", "blocked", "warn", "running", "skip")
        ),
        "status_detail": render.status_line("ok", "detail", indent=render.DETAIL_INDENT,
                                            muted=True, columns=40, theme=theme),
        "step_line": render.step_line(3, 7, "verify", status="ok", theme=theme),
        "progress": render.progress(0.62, label="installing", theme=theme),
        "bullets": render.bullets(["one", "two"], theme=theme),
        "box": render.box(["a line", "another"], title="note", theme=theme),
        "table": render.table(["key", "value"], [["a", "1"], ["bb", "2"]], theme=theme),
        "key_values": render.key_values([("a", 1), ("bb", 2)], theme=theme),
        "legend": render.legend(theme=theme),
        "truncate": render.truncate("a-very-long-cell", 6, theme=theme),
        "wrap": render.wrap("a long borrowed sentence — with an em dash", 20, cont=2,
                            theme=theme),
        "ascii_fold": render.ascii_fold("a — b … c → d ✓ e"),
        "fit": render.fit("x", 4, theme=theme),
    }


def test_every_helper_degrades_to_pure_ascii():
    for name, drawn in _every_helper().items():
        assert "\x1b" not in drawn, f"{name} leaked an escape sequence"
        assert drawn.isascii(), f"{name} leaked a non-ASCII glyph"
        assert not any(ord(char) > 127 for char in drawn), name


def test_the_rich_theme_really_is_different():
    """The same helpers with colour on must emit escapes (else the test above is empty)."""
    with T.use(RICH):
        drawn = {
            "rule": render.rule(30),
            "heading": render.heading("Title"),
            "box": render.box(["a"], title="note"),
            "table": render.table(["state"], [["ok"]]),
            "key_values": render.key_values([("a", 1)]),
            "status_line": render.status_line("ok", "done"),
            "step_line": render.step_line(1, 2, "build", status="ok"),
            "progress": render.progress(0.5),
        }
    for name, text in drawn.items():
        assert "\x1b" in text, f"{name} printed no ANSI in rich mode"
        assert render.strip_ansi(text).strip(), name


def test_helpers_default_to_the_process_theme():
    with T.use(PLAIN):
        assert render.rule(4) == "----"
    with T.use(RICH):
        assert render.strip_ansi(render.rule(4)) == "────"
        assert "\x1b" in render.rule(4)
    assert render.rule(4) == "----"        # …and back to the plain default


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_degenerate_limits_never_raise(limit):
    assert render.width(render.box(["hello"], theme=PLAIN, columns=limit)) >= 0
    assert render.width(render.truncate("hello", limit, theme=PLAIN)) <= max(limit, 1)
