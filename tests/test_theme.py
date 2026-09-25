"""The CLI theme: monochrome by decision, plain by default.

``detect()`` is the one place that answers "may I emit ANSI here?" — so these tests
pin the precedence (``--no-color`` → ``NO_COLOR`` → force → TTY), the grey ramp per
colour depth, the status marks and the plain-mode guarantee: no escapes at all.
"""

from __future__ import annotations

import io

import pytest

from skaldir import theme as T


class FakeTTY(io.StringIO):
    """A stream that claims to be a terminal (the only TTY that is safe in a test)."""

    def __init__(self, encoding: str = "utf-8") -> None:
        super().__init__()
        self._encoding = encoding

    @property
    def encoding(self) -> str:  # type: ignore[override] — StringIO's is read-only
        return self._encoding

    def isatty(self) -> bool:
        return True


def pipe() -> io.StringIO:
    """A stream that is not a terminal (the default for pytest's capsys too)."""
    return io.StringIO()


# --------------------------------------------------------------------------- #
# detection                                                                    #
# --------------------------------------------------------------------------- #

def test_a_pipe_is_plain_and_ascii():
    theme = T.detect(pipe(), env={})
    assert (theme.mode, theme.depth, theme.glyphs) == ("plain", "none", "ascii")
    assert theme.color is False
    assert theme.unicode is False
    assert theme.source == "not-a-tty"
    assert "\x1b" not in theme.style("done", "ok")
    assert theme.mark("ok") == "+"


def test_a_tty_is_rich_with_the_grey_ramp():
    theme = T.detect(FakeTTY(), env={"TERM": "xterm-256color"})
    assert (theme.mode, theme.depth, theme.glyphs) == ("rich", "256", "unicode")
    assert theme.source == "tty"
    assert theme.style("done", "ok") == "\x1b[38;5;231;1mdone\x1b[0m"
    assert theme.mark("ok") == "✓"


def test_no_color_flag_wins_over_a_tty():
    theme = T.detect(FakeTTY(), no_color=True, env={"TERM": "xterm-256color"})
    assert theme.mode == "plain" and theme.depth == "none" and theme.glyphs == "ascii"
    assert theme.source == "no-color"


def test_no_color_environment_variable_is_honoured():
    theme = T.detect(FakeTTY(), env={"NO_COLOR": "1", "TERM": "xterm-256color"})
    assert theme.mode == "plain"
    assert theme.style("x", "fail") == "x"


def test_an_empty_no_color_is_not_a_request():
    """The NO_COLOR convention: only a non-empty value counts."""
    theme = T.detect(FakeTTY(), env={"NO_COLOR": "", "TERM": "xterm-256color"})
    assert theme.mode == "rich"


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_force_color_wins_over_a_pipe(value):
    theme = T.detect(pipe(), env={"GODSV_COLOR": value, "TERM": "xterm-256color"})
    assert theme.mode == "rich" and theme.source == "forced"


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_godsv_color_zero_switches_a_tty_off(value):
    theme = T.detect(FakeTTY(), env={"GODSV_COLOR": value, "TERM": "xterm-256color"})
    assert theme.mode == "plain"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"TERM": "xterm-256color", "COLORTERM": "truecolor"}, "truecolor"),
        ({"TERM": "xterm-256color", "COLORTERM": "24bit"}, "truecolor"),
        ({"TERM": "xterm-256color"}, "256"),
        ({"TERM": "screen-256color"}, "256"),
        ({"TERM": "dumb"}, "basic"),
        ({}, "basic"),
    ],
)
def test_depth_detection(env, expected):
    assert T.detect_depth(env) == expected


@pytest.mark.parametrize("encoding", ["ascii", "latin-1"])
def test_a_stream_that_cannot_encode_the_glyphs_gets_ascii(encoding):
    theme = T.detect(FakeTTY(encoding=encoding), env={"TERM": "xterm-256color"})
    assert theme.mode == "rich"          # the escapes are still fine
    assert theme.glyphs == "ascii"       # …but ✓ would be mojibake
    assert theme.mark("fail") == "x"


def test_godsv_unicode_can_be_forced_even_in_plain_mode():
    theme = T.detect(pipe(), env={"GODSV_UNICODE": "1"})
    assert theme.mode == "plain" and theme.glyphs == "unicode"
    assert theme.mark("ok") == "✓"
    assert "\x1b" not in theme.mark_line("ok", "done")


def test_godsv_unicode_off_forces_ascii_glyphs_on_a_tty():
    theme = T.detect(FakeTTY(), env={"TERM": "xterm-256color", "GODSV_UNICODE": "0"})
    assert theme.mode == "rich" and theme.glyphs == "ascii"


def test_an_explicit_mode_outranks_the_environment():
    theme = T.detect(pipe(), env={"NO_COLOR": "1"}, mode="rich", depth="truecolor")
    assert theme.mode == "rich" and theme.source == "explicit"
    assert theme.depth == "truecolor"


def test_width_can_be_pinned_by_the_environment():
    assert T.detect(pipe(), env={"GODSV_WIDTH": "120"}).width == 120
    assert T.detect(pipe(), env={"COLUMNS": "88"}).width == 88
    assert T.detect(pipe(), env={"GODSV_WIDTH": "0"}).width == 0


# --------------------------------------------------------------------------- #
# tokens, marks, glyphs                                                        #
# --------------------------------------------------------------------------- #

def test_the_status_marks_are_the_documented_set():
    rich = T.detect(FakeTTY(), env={"TERM": "xterm-256color"})
    plain = T.detect(pipe(), env={})
    assert [rich.mark(name) for name in ("ok", "fail", "blocked", "warn")] == ["✓", "✗", "■", "·"]
    assert [plain.mark(name) for name in ("ok", "fail", "blocked", "warn")] == ["+", "x", "#", "."]
    assert plain.mark("no-such-status") in {"·", "*", "+"}  # never a crash


def test_every_glyph_has_an_ascii_fallback():
    rich = T.detect(FakeTTY(), env={"TERM": "xterm-256color"})
    plain = T.detect(pipe(), env={})
    for name in T.GLYPHS:
        assert plain.glyph(name).isascii(), name
        assert rich.glyph(name) in T.GLYPHS[name]["unicode"]
    assert plain.glyph("unknown-glyph") == "unknown-glyph"


def test_plain_mode_styles_nothing_for_any_token():
    plain = T.detect(pipe(), env={})
    for token in T.TOKENS:
        assert plain.style("text", token) == "text", token


def test_unknown_tokens_are_inert():
    rich = T.detect(FakeTTY(), env={"TERM": "xterm-256color"})
    assert rich.style("text", "rainbow") == "text"


def test_the_ramp_is_greys_only():
    """Monochrome is a rule, not a taste: every SGR the theme emits is grey or weight."""
    theme = T.detect(FakeTTY(), env={"TERM": "xterm-256color"})
    codes = [theme.grey(level) for level in (0, 90, 128, 200, 255)]
    assert codes == ["38;5;16", "38;5;240", "38;5;244", "38;5;251", "38;5;231"]
    truecolor = theme.replace(depth="truecolor")
    assert truecolor.grey(128) == "38;2;128;128;128"
    basic = theme.replace(depth="basic")
    assert (basic.grey(255), basic.grey(60), basic.grey(120)) == ("1", "2", "")


def test_grey_names_are_accepted():
    theme = T.detect(FakeTTY(), env={"TERM": "xterm-256color"})
    assert theme.grey("paper") == theme.grey(T.GREYS["paper"])
    assert theme.grey("no-such-name") == theme.grey(200)
    assert theme.grey(9999) == theme.grey(255)


def test_mark_line_carries_the_status_token_and_the_text():
    rich = T.detect(FakeTTY(), env={"TERM": "xterm-256color"})
    assert rich.mark_line("fail", "boom") == "\x1b[7m✗\x1b[0m  boom"
    plain = T.detect(pipe(), env={})
    assert plain.mark_line("fail", "boom") == "x  boom"
    # the two-space rhythm is the same in both modes: the mark never changes width


def test_capabilities_are_reportable():
    theme = T.detect(FakeTTY(), env={"TERM": "xterm-256color"})
    payload = theme.capabilities()
    assert payload["mode"] == "rich" and payload["color"] is True
    assert set(payload["marks"]) >= {"ok", "fail", "blocked", "warn"}
    assert payload["grey_token"]["ok"] == "paper"


# --------------------------------------------------------------------------- #
# the process-wide theme                                                       #
# --------------------------------------------------------------------------- #

def test_use_restores_the_previous_theme():
    before = T.get()
    rich = T.detect(FakeTTY(), env={"TERM": "xterm-256color"})
    with T.use(rich) as current:
        assert current is rich and T.get() is rich
    assert T.get() is before


def test_activate_installs_the_detected_theme():
    before = T.get()
    try:
        activated = T.activate(pipe(), env={})
        assert activated.mode == "plain" and T.get() is activated
    finally:
        T.set(before)


def test_the_default_theme_is_plain_so_an_imported_module_cannot_leak_escapes():
    assert T.Theme().mode == "plain"
    assert T.Theme().style("x", "fail") == "x"
