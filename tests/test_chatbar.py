"""The two-bar chat bar: the frame, the palette, the facts, the honest verb table.

What these tests hold down:

* the frame is **four lines, exactly ``width`` cells each, and it never wraps** — at 40, 80
  and 200 columns, with a long line and a full palette;
* plain mode is a real path: no escape sequence, ASCII only, and the same ``skaldir > ``
  prompt the shell already prints for a pipe;
* the frame is re-drawn when its content changed or the terminal resized — and a redraw
  that changes nothing prints nothing;
* the keys behave: ``/`` opens the palette filtered as you type, Tab completes, Enter runs,
  Esc dismisses, Ctrl-C is the shell's ``KeyboardInterrupt`` (not a swallowed key);
* every number in the hint row is measured or it says ``n/a`` with its reason — no
  plausible-looking zero;
* **every standard slash verb is either real (it resolves against the real parser) or
  reported as planned** — a verb this build does not carry is refused, never faked.
"""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from skaldir import chatbar, cli, cli_contract, complete, repl, slash

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

#: The standard vocabulary the owner asked for, and what it must resolve to.
STANDARD = (
    "/goal",
    "/plan",
    "/status",
    "/model",
    "/provider",
    "/verify",
    "/runs",
    "/tools",
    "/context",
    "/diff",
    "/undo",
    "/init",
    "/compact",
    "/checkpoint",
    "/approve",
    "/help",
    "/quit",
)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GODSV_HOME", str(tmp_path))
    monkeypatch.setenv("SKALDIR_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    return tmp_path


@pytest.fixture(scope="module")
def tree():
    return complete.build_tree(cli.build_parser())


def _bar(out: io.StringIO | None = None, **kwargs) -> tuple[chatbar.ChatBar, io.StringIO]:
    """A bar wired to a StringIO (so its bytes can be read) but with a real fd number.

    ``fd=0`` is what makes ``active`` true without opening a terminal: the drawing code is
    then exercised byte for byte, and no test needs a PTY to check the frame's escapes.
    """
    stream = out if out is not None else io.StringIO()
    kwargs.setdefault("fd", 0)
    kwargs.setdefault("width", 80)
    kwargs.setdefault("height", 24)
    kwargs.setdefault("unicode", False)
    kwargs.setdefault("model", lambda: chatbar.Fact("test-model", "test", ""))
    kwargs.setdefault("goal", lambda: chatbar.Fact("none", "test", "no goal is recorded"))
    return chatbar.ChatBar(stream, **kwargs), stream


# --------------------------------------------------------------------------- #
# the look: two full-width bars around the typing line
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("width", [40, 60, 80, 120, 200])
def test_the_frame_is_four_lines_of_exactly_the_width(width):
    lines, cursor = chatbar.frame_lines(
        width,
        text="a question that is long enough to be clipped by any reasonable terminal "
             "width, twice over",
        hint_left="model a-very-long-model-name · ctx n/a · tok/s n/a · 00:00",
        hint_right="goal: none · /help",
        unicode=True,
    )
    assert len(lines) == chatbar.FRAME_LINES
    assert cursor == 1
    for line in lines:
        assert len(line) == width, (width, len(line), line)
    # the two bars are the frame's edges: the same glyphs, the same length
    assert lines[0] == lines[2]


@pytest.mark.parametrize("width", [40, 80, 200])
def test_the_palette_sits_above_the_bars_and_never_widens_the_frame(width):
    rows = chatbar.palette_rows(
        slash.entries(None), width, unicode=False, selected=0, max_rows=6
    )
    lines, cursor = chatbar.frame_lines(
        width, text="/go", rows=rows, hint_left="facts", hint_right="goal: none · /help",
        unicode=False,
    )
    assert cursor == len(rows) + 1  # the input line index: palette rows, then the bars
    assert len(lines) == len(rows) + chatbar.FRAME_LINES
    for line in lines:
        assert len(line) == width
    assert rows and rows[0].startswith("  >")  # the selected row is pointed at


def test_the_bars_are_theme_glyphs() -> None:
    rich = chatbar.bar_line(20, unicode=True, margin=2)
    plain = chatbar.bar_line(20, unicode=False, margin=2)
    assert rich == "  " + chatbar.BAR_RICH * 18
    assert plain == "  " + "-" * 18
    assert len(rich) == len(plain) == 20


def test_the_hint_row_is_right_aligned_and_keeps_the_goal_and_help():
    for width in (40, 80):
        row = chatbar.hint_line(
            width, "model x · ctx n/a · tok/s n/a · 00:00", "goal: none · /help", unicode=True
        )
        assert len(row) == width
        assert row.rstrip().endswith("goal: none · /help")
    # a narrow terminal drops the left facts, never the right-hand side
    row = chatbar.hint_line(40, "x" * 200, "goal: g · /help", unicode=False)
    assert row.rstrip().endswith("goal: g - /help"), row  # plain mode: ASCII only


def test_plain_mode_is_ascii_only_with_no_escape_sequence():
    lines, _ = chatbar.frame_lines(
        80,
        text="how do I add tests?",
        rows=chatbar.palette_rows(slash.entries(None), 80, unicode=False),
        hint_left="model n/a · ctx n/a · tok/s n/a · 00:00",
        hint_right="goal: none · /help",
        unicode=False,
    )
    for line in lines:
        assert "\x1b" not in line
        assert line.isascii(), line
    assert chatbar.plain_prompt() == "skaldir > "
    assert chatbar.plain_prompt().isascii()


def test_the_frames_own_prompt_is_what_the_shell_would_have_printed(runtime):
    """The plain prompt is the shell's own prompt — the degradation is not a second look."""
    out, err = io.StringIO(), io.StringIO()
    options = repl.ShellOptions(probe=False, no_color=True)
    session = repl.Session(runtime=runtime, runner=lambda argv: 0, out=out, err=err)
    shell = repl.Shell(options, session=session, out=out, err=err, stdin=io.StringIO(""))
    assert shell.interactive is False
    assert shell.prompt() == chatbar.plain_prompt()


def test_attaching_the_frame_re_points_the_sessions_streams(runtime):
    """A command's own output must land above the frame — and it does not use ``shell.out``.

    ``Session.execute`` reads ``session.out``/``.err``, wraps them in a ``Tee`` and redirects
    ``sys.stdout``/``sys.stderr`` onto that; a frame that only replaced the shell's two
    attributes sat *beside* the streamed answer instead of under it (measured in the pty
    capture, and this is the test that would have caught it).
    """
    out, err = io.StringIO(), io.StringIO()
    session = repl.Session(runtime=runtime, runner=lambda argv: 0, out=out, err=err)
    shell = repl.Shell(repl.ShellOptions(probe=False), session=session, out=out, err=err,
                       stdin=io.StringIO(""))
    bar, _ = _bar(out)
    front = chatbar.Front(shell, bar)
    chatbar._attach(shell, front)
    assert isinstance(shell.out, chatbar.FrameStream)
    assert isinstance(session.out, chatbar.FrameStream)
    assert session.out is shell.out and session.err is shell.err
    bar.draw(force=True)
    before = len(out.getvalue())
    session.out.write("the command's own output\n")
    segment = out.getvalue()[before:]
    assert "the command's own output" in segment
    assert segment.rindex(bar.frame.lines[0]) > segment.rindex("the command's own output")
    # and attaching twice never builds a second frame
    chatbar._attach(shell, front)
    assert shell.out is session.out


def test_read_plain_echoes_without_an_escape():
    sink = io.StringIO()
    line = chatbar.read_plain(io.StringIO("tools list\n"), out=sink)
    assert line == "tools list"
    assert sink.getvalue() == "skaldir > tools list\n"
    assert "\x1b" not in sink.getvalue()
    assert sink.getvalue().isascii()
    assert chatbar.read_plain(io.StringIO(""), out=sink) is None


# --------------------------------------------------------------------------- #
# the redraw policy: when it changed, or when the terminal moved
# --------------------------------------------------------------------------- #


def test_the_frame_is_drawn_once_and_a_redraw_without_change_prints_nothing():
    bar, out = _bar()
    assert bar.draw(force=True) is True
    first = out.getvalue()
    # the frame's two bars are identical, so each of them is on the screen exactly once
    assert bar.frame.lines[0] == bar.frame.lines[2]
    assert first.count(bar.frame.lines[0]) == 2
    assert bar.draw() is False  # nothing changed: nothing printed
    assert out.getvalue() == first


def test_typing_redraws_the_frame_and_shows_the_bars_again():
    bar, out = _bar()
    bar.draw(force=True)
    before = len(out.getvalue())
    for char in "hi":
        bar.handle(char)
        bar.draw()
    text = out.getvalue()[before:]
    assert "hi" in text
    # the erase comes first, then the whole frame is written again — bars included
    assert text.count(bar.frame.lines[0]) >= 1
    assert len(bar.frame.lines) == chatbar.FRAME_LINES


def test_a_resize_redraws_at_the_new_width_and_counts_visual_rows():
    bar, out = _bar(width=80)
    bar.draw(force=True)
    assert bar.frame.visual_rows(80) == chatbar.FRAME_LINES
    before = len(out.getvalue())
    assert bar.on_resize(width=40, height=24) is True
    text = out.getvalue()[before:]
    assert "\x1b[J" in text  # the old frame was taken back
    assert bar.width == 40
    for line in bar.frame.lines:
        assert len(line) == 40
    # a line that is 100 cells wide occupies two rows at 60 columns: a resize must erase
    # what the terminal really re-wrapped, or the frame's tail stays on the screen
    bar.frame.lines = ["x" * 100]
    assert bar.frame.visual_rows(60) == 2


def test_sigwinch_marks_a_repaint_and_the_loop_honours_it():
    bar, out = _bar(width=80)
    bar.draw(force=True)
    bar.note_resize()
    assert bar._resized is True
    before = len(out.getvalue())
    assert bar.on_resize(width=100) is True
    assert bar._resized is False
    assert len(out.getvalue()) > before


# --------------------------------------------------------------------------- #
# the keys: `/` opens the palette, Tab completes, Enter runs, Esc dismisses
# --------------------------------------------------------------------------- #


def _palette_bar(tree, **kwargs) -> tuple[chatbar.ChatBar, io.StringIO]:
    # the frame draws PALETTE_ROWS rows in practice; the tests ask for the whole table
    # (``rows=0`` = no cap) so a filter is checked against every verb, not the first page
    return _bar(palette=lambda: slash.entries(tree), rows=0, **kwargs)


def test_typing_slash_opens_the_palette_and_it_filters_as_you_type(tree):
    bar, _ = _palette_bar(tree)
    assert bar.open is False
    bar.handle("/")
    bar.refresh()
    assert bar.open is True
    everything = [chatbar._name_of(row) for row in bar.entries]
    assert "/goal" in everything and "/plan" in everything and "/status" in everything
    for char in "go":
        bar.handle(char)
    names = [chatbar._name_of(row) for row in bar.entries]
    assert names == ["/goal"]
    # a space ends the verb: the palette closes while the arguments are typed
    bar.handle(" ")
    assert bar.refresh() == []
    assert bar.open is False


def test_tab_completes_and_enter_runs_what_is_typed(tree):
    bar, _ = _palette_bar(tree)
    for char in "/stat":
        bar.handle(char)
    assert bar.handle("tab").kind == "redraw"
    assert bar.text == "/status "
    action = bar.handle("enter")
    assert action.kind == "submit"
    assert action.text == "/status "


def test_esc_dismisses_the_palette_and_keeps_the_line(tree):
    bar, _ = _palette_bar(tree)
    for char in "/go":
        bar.handle(char)
    assert bar.open is True
    assert bar.handle("esc").kind == "redraw"
    assert bar.open is False
    assert bar.text == "/go"  # Esc closed the palette, it did not eat the typing


def test_tab_after_a_trailing_space_reaches_the_value_slot():
    """``/history `` + Tab: the space is an argument boundary, not a stray character.

    Measured in the pty (SLASH-BENCHMARK's proof): the composer decided where a line goes
    on ``self.text.strip()``, so the buffer the engine saw was ``/history`` — a bare verb —
    and the value slot never opened at all. Any non-empty partial worked, which is exactly
    why the defect looked like "the value slot is broken".
    """
    asked: list[str] = []

    def engine(text: str) -> str:
        asked.append(text)
        return "/history s-a2391f" if text == "/history " else ""

    bar, _ = _bar(complete_line=engine)
    bar.text = "/history "
    bar.cursor = len(bar.text)
    assert bar.complete() is True
    assert asked == ["/history "], "the engine sees the line as typed, trailing space and all"
    assert bar.text == "/history s-a2391f"


def test_tab_after_a_trailing_space_opens_the_slot_for_every_value_slot_verb():
    """Every verb that declares a value delegates a trailing-space Tab to the engine.

    One rule, no per-verb list in the frame: the bar cannot complete a session id, a model
    id, a checkpoint id or a path, so a line that carries an argument — including the empty
    argument a trailing space announces — goes to ``shell.completer``.
    """
    verbs = sorted({row.slash for row in slash.SLASH_COMMANDS if getattr(row, "values", "")})
    assert {"history", "resume", "model"} <= {verb.lstrip("/") for verb in verbs}
    asked: list[str] = []
    bar, _ = _bar(complete_line=lambda text: asked.append(text) or "")
    for verb in verbs:
        asked.clear()
        bar.text = f"{verb} "
        bar.cursor = len(bar.text)
        bar.complete()
        assert asked == [f"{verb} "], verb


def test_a_bare_verb_still_completes_from_the_palette(tree):
    """The bar's own verb completion is not lost: a bare ``/stat`` never needs the engine."""
    asked: list[str] = []
    bar, _ = _palette_bar(tree, complete_line=lambda text: asked.append(text) or "")
    for char in "/stat":
        bar.handle(char)
    assert bar.handle("tab").kind == "redraw"
    assert bar.text == "/status "
    assert asked == [], "a bare verb is the palette's, not the engine's"


# --------------------------------------------------------------------------- #
# the value slot: a slot with *several* candidates is drawn, not swallowed
# --------------------------------------------------------------------------- #


def _sessions(count: int = 13) -> list[complete.Suggestion]:
    """A value slot as the real engine fills it: ids first, then the verb's own flags."""
    rows = [
        complete.Suggestion(f"s-{index:012x}", f"session {index}, 4 messages", "argument")
        for index in range(count - 4)
    ]
    rows += [
        complete.Suggestion("--runtime", "the runtime dir", "option"),
        complete.Suggestion("--json", "one envelope", "option"),
        complete.Suggestion("--format", "the output format", "option"),
        complete.Suggestion("--quiet", "less talk", "option"),
    ]
    return rows


def test_a_value_slot_with_candidates_draws_them_above_the_bars():
    """``/history `` + Tab: 13 candidates, no common prefix to insert — the frame draws them.

    Measured defect (a real 80x24 pty, a session store with ids): the engine answered with a
    list, ``insert`` was empty because no candidate shares a prefix, and the frame swallowed
    the keystroke whole — ``/history `` stayed ``/history `` and nothing was drawn. The 13
    rows the engine *did* offer are the answer; they belong on the screen.
    """
    candidates = _sessions(13)
    bar, out = _bar(
        complete_line=lambda text: "",
        complete_menu=lambda text: candidates,
    )
    bar.text = "/history "
    bar.cursor = len(bar.text)
    assert bar.complete() is True
    assert bar.text == "/history ", "nothing was inserted — there is nothing to insert"
    assert len(bar.menu) == 13

    lines, cursor = bar.compose()
    rows = bar.menu_rows()
    # bounded: VALUE_ROWS rows plus the honest remainder, never the whole 13
    assert len(rows) == chatbar.VALUE_ROWS + 1
    assert rows[-1].rstrip().endswith("5 more"), rows[-1]
    assert "s-000000000000" in rows[0]
    assert "value" in rows[0], "a slot-filling candidate is marked as a value"
    # the marks come from the engine's own vocabulary: an option is a flag, a slot value is
    # a value — one word each, so a 40-row menu can be scanned
    assert "flag" in chatbar.value_rows(_sessions(13)[-1:], 80, unicode=False)[0]
    # the layout is untouched: menu rows above, then the two bars around the input line
    assert len(lines) == len(rows) + chatbar.FRAME_LINES
    assert cursor == len(rows) + 1
    assert lines[cursor] == chatbar.input_line(bar.width, "/history ", unicode=False)
    assert lines[cursor - 1] == lines[cursor + 1], "the two bars still frame the line"
    for line in lines:
        assert len(line) == bar.width, (len(line), line)


def test_the_value_menu_is_ascii_and_escape_free_in_plain_mode():
    """The same rows in plain mode: no escape byte, ASCII only (a log stays a log)."""
    bar, _ = _bar(
        unicode=False,
        complete_line=lambda text: "",
        complete_menu=lambda text: _sessions(13),
    )
    bar.text = "/history "
    bar.complete()
    rows = bar.menu_rows()
    assert rows and all(row.isascii() for row in rows)
    assert all("\x1b" not in row for row in rows)
    assert rows[-1].rstrip().endswith("... 5 more"), rows[-1]


def test_a_value_menu_never_pushes_the_frame_past_eight_rows():
    """A 40-candidate slot (``/exec ``) stays a page: the two bars may not leave the screen."""
    bar, _ = _bar(
        complete_line=lambda text: "",
        complete_menu=lambda text: _sessions(40),
    )
    bar.text = "/exec "
    bar.complete()
    rows = bar.menu_rows()
    assert len(rows) == chatbar.VALUE_ROWS + 1
    assert rows[-1].rstrip().endswith("32 more"), rows[-1]
    assert len(bar.compose()[0]) <= 24 - 1, "the page must still fit a 24-line terminal"


def test_a_stale_value_menu_disappears_with_the_verb_that_offered_it():
    """The menu belongs to a line: one keystroke that changes it makes the list stale."""
    bar, _ = _bar(
        complete_line=lambda text: "",
        complete_menu=lambda text: _sessions(13),
    )
    bar.text = "/history "
    bar.complete()
    assert bar.menu_rows()
    bar.handle("x")  # /history x — the slot is no longer the one the engine answered for
    assert bar.menu_rows() == []
    assert "x" not in [row for row in bar.menu_rows()]
    bar.handle("esc")  # Esc drops it for real, so a redraw cannot resurrect it
    assert bar.menu == []


def test_a_flag_only_slot_still_shows_the_insert_it_always_did():
    """The four verbs that complete their own flag visibly are untouched.

    ``/retry ``, ``/undo ``, ``/compact `` and ``/approve `` have one common prefix (``--``),
    so the line hook inserts it and the menu hook is never consulted — the behaviour that
    already worked is not paid for by the new one.
    """
    asked: list[str] = []
    bar, _ = _bar(
        complete_line=lambda text: "/retry --" if text == "/retry " else "",
        complete_menu=lambda text: asked.append(text) or _sessions(13),
    )
    bar.text = "/retry "
    bar.cursor = len(bar.text)
    assert bar.complete() is True
    assert bar.text == "/retry --"
    assert asked == [], "an insert that worked must not pay for a second engine call"
    assert bar.menu == []
    assert bar.menu_rows() == []


def test_a_value_slot_with_nothing_to_offer_draws_nothing(tree):
    """No candidates (an empty store) is not a menu of nothing — it is no menu."""
    bar, _ = _bar(complete_line=lambda text: "", complete_menu=lambda text: [])
    bar.text = "/resume "
    bar.cursor = len(bar.text)
    assert bar.complete() is False
    assert bar.menu_rows() == []
    lines, _ = bar.compose()
    assert len(lines) == chatbar.FRAME_LINES

    # and a bar with no menu hook at all is byte-identical to the old behaviour
    bar2, _ = _bar(complete_line=lambda text: "")
    bar2.text = "/resume "
    assert bar2.complete() is False
    assert bar2.menu_rows() == []


# --------------------------------------------------------------------------- #
# below MIN_WIDTH: the frame stands down instead of wrapping
# --------------------------------------------------------------------------- #


def test_the_terminal_width_is_never_clamped_up():
    """The clamp that made `width < MIN_WIDTH` dead code is gone (measured on a real pty).

    ``term_width`` used to answer ``max(MIN_WIDTH, columns)``: a 30-column terminal reported
    40, so ``front_for``'s check never fired and the frame painted 40-cell rows into a
    30-column screen — every bar wrapped, the band split mid-word, the hint row ended in
    ``dismiss…``.
    """
    if termios_unavailable():  # pragma: no cover — Windows
        pytest.skip("no pty on this platform")
    import fcntl
    import pty
    import struct
    import termios as _termios

    master, slave = pty.openpty()
    try:
        fcntl.ioctl(slave, _termios.TIOCSWINSZ, struct.pack("HHHH", 24, 30, 0, 0))
        assert chatbar.term_width(slave) == 30, "the real size, not the frame's minimum"
        assert chatbar.term_width(slave) < chatbar.MIN_WIDTH
        fcntl.ioctl(master, _termios.TIOCSWINSZ, struct.pack("HHHH", 24, 120, 0, 0))
        assert chatbar.term_width(slave) == 120
    finally:
        os.close(slave)
        os.close(master)


def test_a_frame_that_narrows_below_the_minimum_stands_down_and_stops_painting():
    """No torn frame, no wrapped rows: the frame leaves, one honest line replaces it."""
    bar, out = _bar(width=80)
    bar.draw(force=True)
    assert bar.active is True and bar.stood_down is False
    before = len(out.getvalue())
    assert bar.on_resize(width=30, height=24) is False, "the frame did not come back"
    text = out.getvalue()[before:]
    assert "\x1b[J" in text, "the rows it drew were erased, not left behind"
    assert "narrower than 40 columns" in text
    assert "──────────────────────────" not in text, "no 30-cell bar was painted"
    assert chatbar.bar_line(30, unicode=False) not in text, "no 30-cell bar was painted"
    assert bar.width == 30
    # inert: nothing may paint into a terminal the frame no longer fits
    assert bar.active is False and bar.stood_down is True
    assert bar.draw(force=True) is False
    assert bar.restore() is False
    assert bar.frame.drawn == 0 and bar.frame.lines == []
    assert bar.on_resize(width=30) is False
    # sticky: widening again does not resurrect a frame the session has already left
    assert bar.on_resize(width=120) is False
    assert bar.stood_down is True


def test_a_stood_down_front_hands_the_turn_back_to_the_shells_plain_prompt(runtime):
    """``front_for`` answers ``None`` after the stand-down, so the shell's `input()` takes over.

    This is the whole degradation: the session keeps running, on the prompt every pipe
    already gets (``skaldir > ``), and no frame row can wrap because no frame row is drawn.
    """
    out, err = io.StringIO(), io.StringIO()
    session = repl.Session(runtime=runtime, runner=lambda argv: 0, out=out, err=err)
    shell = repl.Shell(repl.ShellOptions(probe=False), session=session, out=out, err=err,
                       stdin=io.StringIO(""))
    bar, _ = _bar(out)
    front = chatbar.Front(shell, bar)
    chatbar._attach(shell, front)
    assert chatbar.front_for(shell) is front, "an attached frame is handed back"
    bar.draw(force=True)
    bar.stand_down(note="")
    assert chatbar.front_for(shell) is None, "a stood-down frame is not a front any more"
    # the streams stay routed through the frame's own stream, which now passes through
    # (the frame is not active), so a command's output is not lost
    shell.out.write("still visible\n")
    assert "still visible" in out.getvalue()


def test_a_stood_down_frame_is_not_asked_to_clear_the_screen(runtime):
    """``/clear`` says the honest thing when the frame already left the screen."""
    out, err = io.StringIO(), io.StringIO()
    session = repl.Session(runtime=runtime, runner=lambda argv: 0, out=out, err=err)
    shell = repl.Shell(repl.ShellOptions(probe=False), session=session, out=out, err=err,
                       stdin=io.StringIO(""))
    bar, _ = _bar(out)
    front = chatbar.Front(shell, bar)
    chatbar._attach(shell, front)
    bar.stand_down(note="")
    before = len(out.getvalue())
    assert chatbar.handle_clear(shell, "/clear") is False
    assert "narrower than 40 columns" in out.getvalue()[before:]
    assert "\x1b[2J" not in out.getvalue()[before:], "nothing was wiped"


def test_ctrl_c_is_the_shells_interrupt_and_ctrl_d_is_the_exit(tree):
    bar, _ = _palette_bar(tree)
    for char in "/go":
        bar.handle(char)
    assert bar.handle("ctrl-c").kind == "interrupt"
    assert bar.text == "/go"  # the line is the shell's to clear, not ours
    assert bar.handle("ctrl-d").kind == "none"  # text on the line: no EOF
    bar.text = ""
    assert bar.handle("ctrl-d").kind == "eof"


def test_the_editing_keys_edit_the_line():
    bar, _ = _bar()
    for char in "abc":
        bar.handle(char)
    assert bar.handle("backspace").kind == "redraw"
    assert bar.text == "ab"
    bar.handle("left")
    assert bar.cursor == 1
    bar.handle("delete")
    assert bar.text == "a"
    bar.text, bar.cursor = "one two", 7
    bar.handle("ctrl-w")
    assert bar.text == "one "
    bar.handle("ctrl-u")
    assert bar.text == ""


def test_up_and_down_walk_the_palette_then_the_history(tree):
    bar, _ = _palette_bar(tree, )
    bar._history = lambda: ["/status", "tools list"]
    bar.handle("/")
    bar.refresh()
    first = bar.selected
    bar.handle("down")
    assert bar.selected != first
    bar.handle("esc")
    bar.text = ""
    bar.refresh()
    bar.handle("up")
    assert bar.text == "tools list"  # newest first, straight from the session history


def test_the_arrow_keys_come_from_the_real_escape_sequences():
    assert chatbar._SEQUENCES["A"] == "up"
    assert chatbar._SEQUENCES["B"] == "down"
    assert chatbar._CONTROL["\x03"] == "ctrl-c"
    assert chatbar._CONTROL["\x7f"] == "backspace"
    assert chatbar._CONTROL["\x1b"] == "esc"
    assert chatbar.read_key.__doc__  # the contract is documented where it is implemented


# --------------------------------------------------------------------------- #
# the facts: measured, labelled or n/a with its reason
# --------------------------------------------------------------------------- #


def test_the_clock_and_the_speed_are_measured_over_a_named_window():
    turn = chatbar.Turn(started=100.0)
    turn.observe("a" * 400, now=101.0)
    turn.observe("b" * 400, now=103.0)
    turn.finished_at = 103.0  # end_turn freezes the measured duration
    assert turn.clock() == "00:03"
    speed = turn.speed()
    assert speed.source == "estimate"
    assert speed.text.startswith("~")  # an estimate says so
    assert speed.known


def test_a_token_count_the_run_reports_wins_over_the_estimate():
    turn = chatbar.Turn(started=0.0)
    turn.observe("tokens: 40\n", now=0.0)
    turn.observe("done\n", now=2.0)
    speed = turn.speed()
    assert speed.text == "20 tok/s"
    assert speed.source == "provider"


def test_a_speed_without_a_window_is_unknown_with_its_reason():
    turn = chatbar.Turn(started=0.0)
    turn.observe("answer", now=0.0)
    fact = turn.speed()
    assert fact.known is False
    assert fact.render() == "tok/s n/a"  # the label says which number is missing
    assert "window" in fact.reason


def test_the_context_reading_is_only_shown_when_the_run_reported_one():
    turn = chatbar.Turn(started=0.0)
    assert turn.context().render() == "ctx n/a"
    assert "no context reading" in turn.context().reason
    turn.observe("ctx 12% · 3 steps\n", now=1.0)
    assert turn.context().text == "ctx 12%"
    assert turn.context().source == "provider"


def test_the_hint_row_shows_the_model_the_context_the_speed_and_the_clock():
    bar, _ = _bar(goal=lambda: chatbar.Fact("final1", "runs_store", ""))
    bar.begin_turn()
    bar.turn.observe("x" * 400, now=bar.turn.started + 1.0)
    bar.turn.observe("y" * 400, now=bar.turn.started + 3.0)
    left, right = bar.facts()
    assert left.startswith("model test-model")
    assert "ctx n/a" in left  # the run reported none: stated, not zeroed
    assert "tok/s" in left  # speed is measured over the stream window
    # the clock is live (was pinned to the last-chunk time and froze)
    assert bar.turn.elapsed >= 0.0
    assert right == "goal: final1 - /help"  # plain mode (this bar is unicode=False)


def test_a_missing_goal_says_so_instead_of_inventing_one():
    bar, _ = _bar(goal=lambda: chatbar.fact_unknown("no goal is recorded"))
    _left, right = bar.facts()
    # plain mode (unicode=False in this bar): the dot is a plain "-", and the missing fact
    # names itself instead of rendering a bare "n/a"
    assert right == "goal: n/a - /help"


def test_the_model_probe_reads_the_real_config_and_never_raises(runtime):
    fact = chatbar.probe_model()
    assert isinstance(fact, chatbar.Fact)
    assert fact.known is False  # this runtime has no provider configured
    assert fact.render() == "n/a"
    assert fact.reason  # and it says why


def test_a_probe_that_raises_does_not_break_the_frame():
    def boom() -> chatbar.Fact:
        raise RuntimeError("no config")

    bar, _ = _bar(model=boom, goal=boom)
    left, right = bar.facts()
    assert "model n/a" in left and "goal: n/a" in right


def test_model_and_goal_probes_use_the_real_sources(runtime):
    """``probe_goal`` asks the shell's own probe — it never derives a goal itself."""
    out, err = io.StringIO(), io.StringIO()
    session = repl.Session(runtime=runtime, runner=lambda argv: 0, out=out, err=err)
    shell = repl.Shell(repl.ShellOptions(probe=False), session=session, out=out, err=err,
                       stdin=io.StringIO(""))
    fact = chatbar.probe_goal(shell)
    assert isinstance(fact, chatbar.Fact)
    assert fact.known is False and fact.reason  # no run store in this runtime


# --------------------------------------------------------------------------- #
# streaming into the same zone: the frame stays the last thing on the screen
# --------------------------------------------------------------------------- #


def test_a_streamed_answer_lands_above_the_frame_which_is_redrawn_underneath():
    bar, out = _bar()
    bar.draw(force=True)
    before = len(out.getvalue())
    bar.begin_turn()
    bar.stream_chunk("the answer starts here\nsecond line\n")
    text = out.getvalue()[before:]
    assert "the answer starts here" in text
    assert "second line" in text
    # after every chunk the frame is written again, below the text it just streamed
    assert text.rindex(bar.frame.lines[-1]) > text.rindex("second line")
    assert text.rindex(bar.frame.lines[0]) > text.rindex("second line")
    assert len(bar.frame.lines) == chatbar.FRAME_LINES


def test_a_chunk_without_a_newline_keeps_the_line_open_and_rewrites_it():
    bar, out = _bar()
    bar.draw(force=True)
    before = len(out.getvalue())
    bar.stream_chunk("half a sentence")
    assert bar.frame.partial is True
    before = len(out.getvalue())
    bar.stream_chunk(" finished.\n")
    text = out.getvalue()[before:]
    assert " finished." in text  # the chunk continued the open line
    assert "half a sentencehalf" not in out.getvalue()  # and did not duplicate it
    assert bar.frame.partial is False


def test_one_erase_per_chunk_and_the_frame_starts_on_a_line_of_its_own():
    """The two ways a frame tears itself: erasing twice, and painting into the open line.

    Measured before the fix: ``stream_chunk`` erased the frame, wrote the text, and then
    the repaint erased *again* — from a cursor that had already moved, so the second erase
    ate the last two lines of the text it was supposed to sit under.
    """
    bar, out = _bar()
    bar.draw(force=True)
    before = len(out.getvalue())
    bar.stream_chunk("an answer that has not ended yet")
    text = out.getvalue()[before:]
    assert text.count("\x1b[J") == 1, "one write, one erase"
    assert text.index("\x1b[J") < text.index("an answer"), "the erase comes first"
    # the open line is left alone: the frame begins on the next line (CR LF, then the bar)
    assert "\r\n" + bar.frame.lines[0] in text
    assert "an answer that has not ended yet" + bar.frame.lines[0] not in text


def test_a_flush_is_not_a_line_break(runtime):
    """A streamer flushes after every chunk; a flush that closed the line broke the frame.

    Measured in the pty capture: ``FrameStream.flush`` called ``close_line()``, which wrote a
    newline *without* repainting. From then on the cursor was no longer parked in the input
    line, so every following chunk wrote at the old position and the frame walked down the
    screen leaving copies of itself behind.
    """
    bar, out = _bar()
    bar.draw(force=True)
    stream = chatbar.FrameStream(out, bar)
    stream.write("half a line")
    stream.flush()
    assert bar.frame.partial is True, "the stream is still on that line"
    assert bar.frame.on_screen is True, "and the frame is still parked"
    before = len(out.getvalue())
    stream.write(" and the rest.\n")
    segment = out.getvalue()[before:]
    assert "\x1b[2F\x1b[J" in segment  # the erase still counts the open line
    assert " and the rest." in segment
    assert bar.frame.partial is False
    assert bar.frame.on_screen is True


def test_closing_a_line_puts_the_frame_back_below_it():
    bar, out = _bar()
    bar.draw(force=True)
    bar.stream_chunk("a line that ends without a newline")
    assert bar.frame.partial is True
    before = len(out.getvalue())
    bar.close_line()
    segment = out.getvalue()[before:]
    assert segment.startswith("\r\n")  # the newline that finishes the open line
    assert bar.frame.lines[0] in segment  # and the frame, drawn again under it
    assert bar.frame.partial is False and bar.frame.on_screen is True
    # a second close is a no-op (no stray newline, no second frame)
    before = len(out.getvalue())
    bar.close_line()
    assert out.getvalue()[before:] == ""


def test_a_wrapped_open_line_is_erased_by_the_row(runtime):
    """A line longer than the terminal is re-wrapped; the erase counts rows, not lines.

    Measured in the pty capture: the open line "sibling work is running…" (three rows at 80
    columns) was counted as one row, so the erase started below its first row and the next
    chunk rewrote the line while the old copy stayed visible — the sentence printed three
    times down the screen.
    """
    bar, out = _bar(width=40)
    bar.draw(force=True)
    # 90 cells of text, no newline: three rows at 40 columns
    bar.stream_chunk("x" * 90)
    assert bar.frame.open_rows == 3
    assert bar.frame.partial is True
    before = len(out.getvalue())
    bar.stream_chunk(" more\n")
    segment = out.getvalue()[before:]
    assert segment.startswith("\x1b[4F\x1b[J")  # cursor_index 1 + 3 wrapped rows
    assert bar.frame.open_rows == 0


def test_a_single_row_open_line_is_erased_by_one_row(runtime):
    bar, out = _bar(width=80)
    bar.draw(force=True)
    bar.stream_chunk("a short unfinished line")
    assert bar.frame.open_rows == 1
    before = len(out.getvalue())
    bar.stream_chunk(" and more\n")
    assert out.getvalue()[before:].startswith("\x1b[2F\x1b[J")


def test_five_write_above_calls_keep_all_five_lines():
    """The pin the model-picker round asked for: ``write_above()`` loses nothing, ever.

    Measured in that round's pty capture: five ``bar.write_above(...)`` calls in a row lost
    the earlier lines — the report kept only the first ~20 characters of its first line.
    The bar owes every caller this: each call takes the frame back, writes its line and
    paints the frame again under it, so any number of lines stack up in order.
    """
    lines = ["first line of the block", "second line", "third", "fourth", "fifth line"]
    bar, out = _bar()
    bar.draw(force=True)
    before = len(out.getvalue())
    for line in lines:
        bar.write_above(line)
    text = out.getvalue()[before:]
    for line in lines:
        assert text.count(line) == 1, (line, text)
    assert [text.index(line) for line in lines] == sorted(text.index(line) for line in lines)
    # the frame is painted again under *every* line (once per call), so a reader that only
    # looks at the tail of the screen still sees a whole frame; the frame has two bars and
    # both carry the same rule
    assert text.count(bar.frame.lines[0]) == 2 * len(lines)
    assert len(bar.frame.lines) == chatbar.FRAME_LINES


def test_the_erase_counts_the_rows_a_wrapped_frame_row_really_takes():
    """A frame row wider than the terminal wraps — the erase has to count both rows.

    This is the mechanism behind the loss above, and it is a real one: a caller that hands
    the frame rows built for another width (the in-frame picker builds its list at the
    bar's width, so a resize between the two does exactly this) makes every frame row
    occupy two screen rows. Counting one per line started the erase *inside* the frame, and
    the rows it cleared were the ones just written above it.
    """
    bar, out = _bar(width=40)
    bar.draw(force=True)
    wide = [chatbar.bar_line(80, unicode=False) for _ in range(4)]
    bar._paint(wide, 3)  # the picker's shape: four rows, the input line at index 2
    before = len(out.getvalue())
    bar.write_above("text above the frame")
    segment = out.getvalue()[before:]
    # three rows above the cursor, each two rows at 40 columns: up 6, where a line count
    # said up 3 — six rows back is the top of the frame, three rows back is inside it
    assert segment.startswith("\x1b[6F\x1b[J"), segment[:20]
    assert segment.count("\x1b[J") == 1, "one write, one erase"


def test_the_erase_is_unchanged_for_a_frame_that_fits():
    """The arithmetic above is the same number when no row wraps (nothing else moved)."""
    bar, out = _bar(width=80)
    bar.draw(force=True)
    assert bar.frame.rows_above(80) == bar.frame.cursor_index
    before = len(out.getvalue())
    bar.write_above("a line")
    # the four-line frame parks the cursor one line below its top bar
    assert out.getvalue()[before:].startswith("\x1b[1F\x1b[J")


def test_a_narrower_terminal_is_adopted_before_the_next_write():
    """A geometry that never fit (or moved) is re-read before a write above the frame.

    The bar used to learn a new size only from ``SIGWINCH`` *plus* a keystroke, so a resize
    while an answer streamed left it painting rows the terminal re-wrapped — the frame in
    the wrong place and the text above it eaten. One ``ioctl`` before each write fixes the
    whole class; the deterministic tests above use a ``StringIO``, where the frame's own
    width stays the authority (``_owns_terminal`` is false).
    """
    if termios_unavailable():  # pragma: no cover — Windows
        pytest.skip("no pty on this platform")
    import fcntl
    import pty
    import struct
    import termios as _termios

    master, slave = pty.openpty()
    out = None
    try:
        fcntl.ioctl(slave, _termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        # the frame writes to the very fd it measures (that is what makes the bar live)
        out = os.fdopen(slave, "w", buffering=1, encoding="utf-8", newline="")
        bar = chatbar.ChatBar(
            out, fd=slave, width=80, height=24, unicode=False,
            model=lambda: chatbar.Fact("test-model", "test", ""),
            goal=lambda: chatbar.Fact("none", "test", "no goal"),
        )
        assert bar._owns_terminal() is True
        bar.draw(force=True)
        bar.write_above("before the resize")
        assert bar.width == 80
        fcntl.ioctl(master, _termios.TIOCSWINSZ, struct.pack("HHHH", 24, 60, 0, 0))
        bar.write_above("after the resize")
        assert bar.width == 60, "the frame adopted what the terminal really has"
        assert bar.frame.lines[0] == chatbar.bar_line(60, unicode=False)
        assert all(len(line) == 60 for line in bar.frame.lines)
    finally:
        if out is not None:
            out.close()  # closes the slave fd as well
        os.close(master)


def test_the_frames_own_text_is_not_counted_as_wide(runtime):
    """The frame's rows and the open line's rows are counted the same way (one cell = one)."""
    assert chatbar._visible_rows("\x1b[31mabc\x1b[0m", 80) == 1
    assert chatbar._visible("\x1b[31mabc\x1b[0m") == "abc"
    assert chatbar._visible_rows("", 80) == 0
    assert chatbar._visible_rows("x" * 80, 80) == 1
    assert chatbar._visible_rows("x" * 81, 80) == 2


def test_the_frame_is_still_there_after_a_full_turn():
    bar, out = _bar()
    bar.draw(force=True)
    bar.begin_turn()
    bar.stream_chunk("streaming…\n")
    turn = bar.end_turn()
    assert turn is not None
    assert bar.turn is None
    bar.draw()
    lines = bar.frame.lines
    assert len(lines) == chatbar.FRAME_LINES
    assert lines[0] == lines[2] != ""
    assert "tok/s n/a" in lines[-1]  # the turn is over: no live number is claimed


def test_the_palette_shows_the_most_used_verbs_first(tree):
    """Typing ``/`` opens on the verbs a session reaches for, not on the alphabet."""
    bar, _ = _palette_bar(tree)
    bar.handle("/")
    bar.refresh()
    names = [chatbar._name_of(row) for row in bar.entries]
    assert names[:5] == ["/help", "/goal", "/status", "/plan", "/verify"]
    # the frame itself draws one page and names the rest honestly (never silently)
    bar.max_rows = chatbar.PALETTE_ROWS
    bar.refresh()
    rows = chatbar.palette_rows(
        bar.entries, 80, unicode=False, max_rows=bar.max_rows,
        hidden=bar.matches - len(bar.entries),
    )
    assert len(rows) == chatbar.PALETTE_ROWS + 1  # the page plus "... n more"
    assert "more" in rows[-1]
    assert str(bar.matches - len(bar.entries)) in rows[-1]


def test_the_frame_stream_is_a_usable_stdout():
    bar, out = _bar()
    bar.draw(force=True)
    stream = chatbar.FrameStream(out, bar)
    assert stream.writable() is True
    assert stream.isatty() is False  # the StringIO is not a terminal
    assert stream.write("hello\n") == 6
    assert stream.encoding
    stream.flush()
    assert "hello" in out.getvalue()


# --------------------------------------------------------------------------- #
# the frame takes a shell over only when it really can
# --------------------------------------------------------------------------- #


def test_front_for_refuses_a_stream_without_a_terminal(runtime):
    out, err = io.StringIO(), io.StringIO()
    session = repl.Session(runtime=runtime, runner=lambda argv: 0, out=out, err=err)
    shell = repl.Shell(repl.ShellOptions(probe=False, interactive=True), session=session,
                       out=out, err=err, stdin=io.StringIO(""))
    assert shell.interactive is True
    assert chatbar.front_for(shell) is None  # a StringIO is not a terminal


def test_front_for_is_off_when_the_environment_says_so(runtime, monkeypatch):
    monkeypatch.setenv("SKALDIR_CHATBAR", "0")
    out, err = io.StringIO(), io.StringIO()
    session = repl.Session(runtime=runtime, runner=lambda argv: 0, out=out, err=err)
    shell = repl.Shell(repl.ShellOptions(probe=False, interactive=True), session=session,
                       out=out, err=err, stdin=io.StringIO(""))
    assert chatbar.front_for(shell) is None
    assert chatbar._bar_enabled() is False


# --------------------------------------------------------------------------- #
# plain language goes to the agent loop
# --------------------------------------------------------------------------- #


def test_prose_is_a_question_and_a_command_is_a_command(tree):
    known = chatbar.root_names(tree)
    assert "tools" in known and "goal" in known
    assert chatbar.looks_like_prose("how do I add tests to this project?", known=known)
    assert chatbar.looks_like_prose("refactor the parser", known=known)
    assert chatbar.looks_like_prose("really?", known=known)
    assert not chatbar.looks_like_prose("tools list", known=known)
    assert not chatbar.looks_like_prose("selftest", known=known)
    assert not chatbar.looks_like_prose("", known=known)


@pytest.mark.parametrize("text", ["hi", "hello", "weiter", "yes", "👍", "こんにちは"])
def test_single_word_chat_input_reaches_agent_not_command_parser(runtime, text):
    out, err = io.StringIO(), io.StringIO()
    seen = []
    session = repl.Session(runtime=runtime, runner=lambda argv: (seen.append(tuple(argv)), 0)[1],
                           out=out, err=err)
    shell = repl.Shell(repl.ShellOptions(probe=False, quiet=True), session=session,
                       out=out, err=err, stdin=io.StringIO(""))
    bar, _ = _bar(out)
    shell._chatbar_front = chatbar.Front(shell, bar)
    shell.dispatch(text)
    assert seen and seen[0][:3] == ("agent", "run", text)
    assert "invalid choice" not in err.getvalue()
    assert "--chat-output" in seen[0]
    assert "done agent run" not in out.getvalue()
    assert "exit 0 (OK)" not in out.getvalue()


@pytest.mark.parametrize("text", ["quit", "exit", "help", "history", "history 3", "/help"])
def test_chat_routing_preserves_explicit_shell_commands(tree, text):
    assert not chatbar.looks_like_prose(text, known=chatbar.root_names(tree))


def test_a_plain_language_line_is_routed_to_the_agent_loop(runtime):
    out, err = io.StringIO(), io.StringIO()
    seen: list[tuple[str, ...]] = []

    def runner(argv):
        seen.append(tuple(argv))
        return 0

    session = repl.Session(runtime=runtime, runner=runner, out=out, err=err)
    shell = repl.Shell(repl.ShellOptions(probe=False), session=session, out=out, err=err,
                       stdin=io.StringIO(""))
    bar, _ = _bar(out)  # the frame writes into the shell's own stream
    front = chatbar.Front(shell, bar)
    shell._chatbar_front = front
    routed = chatbar.route_plain(shell, "how do I add tests to this project?")
    assert routed is not None  # handled: it did not fall through to `skaldir <line>`
    assert seen and seen[0][:2] == ("agent", "run")
    assert seen[0][2] == "how do I add tests to this project?"
    assert "working" in out.getvalue()  # the visible line the owner asked for


def test_a_question_ending_in_a_question_mark_is_answered_not_overlaid(runtime, tree):
    """`how do I add tests?` is a question, not the `?` overlay.

    Measured in the pty capture: the trailing-`?` shortcut ran *before* the routing hook, so
    a prose question opened the `?` overlay ("no command 'project' in this build"). A line
    that starts with a real command still gets the overlay.
    """
    out, err = io.StringIO(), io.StringIO()
    seen: list[tuple[str, ...]] = []
    session = repl.Session(runtime=runtime, runner=lambda argv: (seen.append(tuple(argv)), 0)[1],
                           out=out, err=err)
    shell = repl.Shell(repl.ShellOptions(probe=False, quiet=True), session=session,
                       out=out, err=err, stdin=io.StringIO(""))
    bar, _ = _bar(out)
    shell._chatbar_front = chatbar.Front(shell, bar)
    shell.dispatch("how do I add tests to this project?")
    assert seen and seen[0][:2] == ("agent", "run")
    assert "overlay" not in out.getvalue()

    # a command with the same suffix keeps the overlay it always had
    out.seek(0)
    out.truncate(0)
    shell.dispatch("tools list?")
    assert "overlay" in out.getvalue()
    assert len(seen) == 1  # nothing was executed for the overlay line


def test_without_a_frame_a_plain_line_keeps_its_old_path(runtime):
    out, err = io.StringIO(), io.StringIO()
    session = repl.Session(runtime=runtime, runner=lambda argv: 0, out=out, err=err)
    shell = repl.Shell(repl.ShellOptions(probe=False), session=session, out=out, err=err,
                       stdin=io.StringIO(""))
    assert chatbar.route_plain(shell, "how do I add tests?") is None


# --------------------------------------------------------------------------- #
# the verb table: real, built-in or planned — never faked
# --------------------------------------------------------------------------- #


def test_every_standard_verb_exists(tree):
    names = {item.slash for item in slash.SLASH_COMMANDS}
    missing = [name for name in STANDARD if name not in names]
    assert missing == [], f"these standard verbs are not in the palette: {missing}"


def test_every_real_verb_resolves_against_the_real_parser(tree):
    known = set(complete.known_paths(tree))
    rows = chatbar.mapping_rows(tree)
    for row in rows:
        assert row["kind"] in {"real", "builtin", "planned", "unwired"}, row
        if row["kind"] == "real":
            assert row["path"] in known, (
                f"{row['slash']} claims {row['path']}, which is not in the parser")
            assert row["command"].startswith("skaldir ")
        if row["kind"] == "planned":
            assert row["path"] == "" and row["command"] == ""
            assert row["planned_reason"], row  # a planned verb names its reason
            assert row["nearest"], row  # and the real commands that cover the ground
        if row["slash"] in STANDARD:
            assert row["kind"] in {"real", "builtin", "planned"}


def test_the_required_verbs_are_mapped_the_way_the_docs_say(tree):
    rows = {row["slash"]: row for row in chatbar.mapping_rows(tree)}
    expected = {
        "/goal": "skaldir goal show",
        "/status": "skaldir selftest",
        # MOVED (2026-09-19): `/model` used to map onto `provider tiers`; the palette row is
        # now the picker's own (`model_picker.SLASH_SNIPPET`, pasted into slash.py by the
        # slash block), so the verb it really runs is `skaldir model`. That is the row the
        # framed `/model` answers with, and `skaldir chat verbs` reports the same thing.
        "/model": "skaldir model",
        "/provider": "skaldir provider list",
        "/verify": "skaldir verify status",
        "/runs": "skaldir runs status",
        "/tools": "skaldir tools list",
        "/context": "skaldir context usage --text",
        "/undo": "skaldir edit undo",
        "/compact": "skaldir context compact --text",
        "/checkpoint": "skaldir runs checkpoint",
        "/approve": "skaldir approval allow",
    }
    for verb, command in expected.items():
        assert rows[verb]["kind"] == "real", verb
        assert rows[verb]["command"] == command, verb
    # the three the shell answers itself, and the one verb that is not built yet
    for verb, kind in (("/help", "builtin"), ("/quit", "builtin"), ("/diff", "builtin"),
                       ("/init", "builtin"), ("/plan", "planned")):
        assert rows[verb]["kind"] == kind, verb


def test_the_verb_report_counts_agree_with_its_rows(tree):
    report = chatbar.verb_report(tree)
    counts = report["counts"]
    assert counts["total"] == len(report["verbs"])
    assert (counts["real"] + counts["builtin"] + counts["planned"] + counts["unwired"]
            == counts["total"])
    # every planned verb is named, and `/plan` is still the one the surface grew around.
    # `/review` left the planned set when it became a real verb (`voice check`).
    assert report["planned"][0] == "/plan"
    for verb in ("/plan", "/rename", "/newtask", "/debug"):
        assert verb in report["planned"], verb
    assert "/review" not in report["planned"]
    assert any(row["slash"] == "/review" and row["kind"] == "real" for row in report["verbs"])
    assert str(counts["planned"]) in chatbar.format_verbs(report)


def test_a_planned_verb_is_refused_by_the_shell_and_nothing_runs(runtime):
    out, err = io.StringIO(), io.StringIO()
    seen: list[tuple[str, ...]] = []

    def runner(argv):
        seen.append(tuple(argv))
        return 0

    session = repl.Session(runtime=runtime, runner=runner, out=out, err=err)
    shell = repl.Shell(repl.ShellOptions(probe=False, quiet=True), session=session,
                       out=out, err=err, stdin=io.StringIO("/plan\n/quit\n"))
    shell.run()
    text = out.getvalue()
    assert "planned" in text
    assert "no such" in text  # the shell says the verb is not there
    assert "skaldir completion begin" in text  # and names the real commands instead
    assert seen == []  # nothing was executed for it


def test_a_verb_that_needs_an_argument_says_so_instead_of_running(runtime):
    """``/context`` maps onto a real command whose bare form prints nothing: say so.

    Measured: ``skaldir context usage`` (no ``--transcript``, no ``--run-id``) exits 1 with
    an empty stdout *and* an empty stderr, so a user typing ``/context`` would see a bare
    ``exit 1 (USAGE)`` and no reason. The shell refuses with the real forms instead, and the
    mapping still names the real command (``skaldir context usage --text``).
    """
    out, err = io.StringIO(), io.StringIO()
    seen: list[tuple[str, ...]] = []

    def runner(argv):
        seen.append(tuple(argv))
        return 0

    session = repl.Session(runtime=runtime, runner=runner, out=out, err=err)
    shell = repl.Shell(repl.ShellOptions(probe=False, quiet=True), session=session,
                       out=out, err=err, stdin=io.StringIO("/context\n/quit\n"))
    shell.run()
    text = out.getvalue()
    assert "needs --transcript" in text
    assert "skaldir context usage --transcript <a message file or a run transcript>" in text
    assert seen == []  # the empty form is never run


def test_a_verb_with_its_argument_still_runs(runtime):
    """With the argument in hand the same verb runs the real command (flags included)."""
    out, err = io.StringIO(), io.StringIO()
    seen: list[tuple[str, ...]] = []

    def runner(argv):
        seen.append(tuple(argv))
        return 0

    session = repl.Session(runtime=runtime, runner=runner, out=out, err=err)
    shell = repl.Shell(repl.ShellOptions(probe=False, quiet=True), session=session,
                       out=out, err=err,
                       stdin=io.StringIO("/context --transcript /tmp/x.jsonl\n/quit\n"))
    shell.run()
    assert seen == [("context", "usage", "--text", "--transcript", "/tmp/x.jsonl")]


def test_a_planned_verb_never_enters_the_palette_as_ready(tree):
    entry = next(item for item in slash.entries(tree) if item.command.name == "plan")
    assert entry.state == slash.STATE_PLANNED
    assert entry.runnable is False
    assert entry.mark() == "planned"
    assert entry.detail  # the why is carried, not dropped


# --------------------------------------------------------------------------- #
# the CLI surface: chat / chat verbs / chat preview
# --------------------------------------------------------------------------- #


def _subparsers() -> argparse._SubParsersAction:
    parser = cli.build_parser()
    return [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)][0]


def test_register_cli_adds_the_chat_group_and_is_idempotent():
    sub = _subparsers()
    if "chat" not in sub.choices:
        chatbar.register_cli(sub)
    parser = chatbar.register_cli(sub)  # a second call must not raise
    assert parser is sub.choices["chat"]
    assert "chat" in cli_contract.COMMANDS
    spec = cli_contract.spec_for("chat")
    assert spec.layer
    assert spec.mutates is True
    assert spec.evidence


def _parser_with_chat(original):
    """``build_parser()`` plus the owner's two-line snippet — what cli.py will look like."""

    def build():
        parser = original()
        sub = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)][0]
        if "chat" not in sub.choices:
            chatbar.register_cli(sub)
            cli_contract.annotate(parser)
        return parser

    return build


def test_chat_verbs_through_the_product_cli(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("GODSV_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "build_parser", _parser_with_chat(cli.build_parser))
    code = cli.main(["chat", "verbs", "--json"])
    captured = capsys.readouterr()
    import json

    document = json.loads(captured.out.strip())
    assert document["command"] == "chat"
    assert document["ok"] is True and document["exit_code"] == code == 0
    assert document["evidence"]
    data = document["data"]
    assert data["counts"]["planned"] == len(data["planned"])
    # the planned verbs are the ones with no verb behind them, `/plan` among them.
    # `/review` is no longer one of them: it is a real verb now (it runs `voice check`).
    for verb in ("/plan", "/rename", "/newtask", "/debug"):
        assert verb in data["planned"], verb
    assert "/review" not in data["planned"]
    rows = {row["slash"]: row for row in data["verbs"]}
    assert rows["/review"]["kind"] == "real"
    assert rows["/goal"]["kind"] == "real"
    assert rows["/goal"]["command"] == "skaldir goal show"
    assert rows["/help"]["kind"] == "builtin"
    assert rows["/plan"]["kind"] == "planned"


def test_chat_preview_draws_the_frame_with_two_bars(capsys, monkeypatch, tmp_path):
    """Both looks on one screen: the frame, and the same frame in plain ASCII.

    ``--text`` flags appear in the mapping (``skaldir context usage --text``) because the
    prompt is a human surface: the *path* is what has to be real, the flags are the shell's
    own form. ``/context`` is the only entry that carries one.
    """
    monkeypatch.setenv("GODSV_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "build_parser", _parser_with_chat(cli.build_parser))
    code = cli.main(["chat", "preview", "--width", "60"])
    captured = capsys.readouterr()
    assert code == 0
    lines = [line for line in captured.out.splitlines() if line.startswith("  ")]
    bars = [line for line in lines if set(line.strip()) <= {"─", "-"} and line.strip()]
    assert len(bars) == 4, captured.out  # two bars per look, and both looks are printed
    assert "❯" in captured.out and "\n  > " in captured.out
    assert all(len(line) == 60 for line in bars), "every bar is exactly the width asked for"
    assert "skaldir > " in captured.out  # the plain path is printed too
    # the plain look is pure ASCII and carries no escape at all
    plain = captured.out.split("plain mode")[1]
    assert "\x1b" not in plain
    assert all(ord(char) < 128 for char in plain)


def test_chat_json_never_draws_a_frame(capsys, monkeypatch, tmp_path):
    """``--json`` owns stdout: one envelope and nothing else, so the frame stays off."""
    monkeypatch.setenv("GODSV_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "build_parser", _parser_with_chat(cli.build_parser))
    monkeypatch.setattr("sys.stdin", io.StringIO("/quit\n"))
    code = cli.main(["chat", "--json", "--no-probe", "--limit", "1"])
    captured = capsys.readouterr()
    import json

    document = json.loads(captured.out.strip())
    assert document["command"] == "chat"
    assert document["data"]["frame"]["drawn"] is False
    # the process exit code is the envelope's own code (0 = ok), not a second opinion
    assert document["exit_code"] == code == 0


# --------------------------------------------------------------------------- #
# the real terminal: a PTY, the frame, a resize, a Ctrl-C
# --------------------------------------------------------------------------- #


def _pty_child(tmp_path, width: int = 80, height: int = 24):
    """A real chat session on a real controlling terminal.

    ``script(1)`` is not installed here, so the terminal is built from the stdlib. The
    child is a **session leader whose controlling terminal is the pty** (``TIOCSCTTY``
    after ``setsid``): without that step the line discipline has no foreground process
    group and a ``\\x03`` byte would be swallowed instead of becoming a ``SIGINT`` — the
    difference between testing a Ctrl-C and testing nothing at all.
    """
    import fcntl
    import pty
    import struct
    import termios as _termios

    master, slave = pty.openpty()
    fcntl.ioctl(slave, _termios.TIOCSWINSZ, struct.pack("HHHH", height, width, 0, 0))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONUNBUFFERED"] = "1"
    env["TERM"] = "dumb"
    env["GODSV_HOME"] = str(tmp_path / "rt")
    env["SKALDIR_HOME"] = str(tmp_path / "rt")
    env["SKALDIR_CHATBAR"] = "1"
    program = (
        "import fcntl, runpy, sys, termios;"
        "fcntl.ioctl(0, getattr(termios, 'TIOCSCTTY', 0x540E), 0);"
        "sys.argv = ['skaldir.chatbar', 'shell', '--no-probe'];"
        "runpy.run_module('skaldir.chatbar', run_name='__main__')"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", program],
        cwd=str(REPO), env=env, stdin=slave, stdout=slave, stderr=slave,
        start_new_session=True, close_fds=True,
    )
    os.close(slave)
    return master, child


def _read_until(master: int, needle: bytes, deadline: float = 12.0) -> bytes:
    import select

    seen = b""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            ready, _, _ = select.select([master], [], [], 0.2)
            if not ready:
                continue
            chunk = os.read(master, 65536)
        except OSError:
            break
        if not chunk:
            break
        seen += chunk
        if needle in seen:
            break
    return seen


def test_a_real_pty_shows_the_two_bars_and_survives_a_ctrl_c(tmp_path):
    """The end-to-end proof: a real terminal, the frame, a resize, then a ``^C``.

    Skipped when the PTY machinery is not available; the ``MVP/62`` capture is the same
    journey with every step recorded.
    """
    if termios_unavailable():  # pragma: no cover — Windows
        pytest.skip("no pty on this platform")
    master, child = _pty_child(tmp_path)
    try:
        bar_bytes = chatbar.BAR_RICH.encode("utf-8")
        seen = _read_until(master, bar_bytes)
        assert bar_bytes in seen, seen[-400:]
        os.write(master, b"hello")
        time.sleep(0.3)
        seen += _read_until(master, b"hello", deadline=4.0)
        assert b"hello" in seen
        # a resize: the child gets SIGWINCH through the pty and redraws at the new width
        import fcntl
        import signal as _signal
        import struct
        import termios as _termios

        fcntl.ioctl(master, _termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        os.kill(child.pid, _signal.SIGWINCH)
        time.sleep(0.4)
        seen += _read_until(master, bar_bytes, deadline=3.0)
        assert seen.count(bar_bytes) >= 2  # the frame came back after the resize
        # Ctrl-C at the prompt: the shell clears the line and keeps its session
        os.write(master, b"\x03")
        seen += _read_until(master, b"cleared", deadline=6.0)
        assert b"cleared" in seen, seen[-600:]
        assert bar_bytes in seen  # the frame is still there after the cancel
    finally:
        try:
            os.write(master, b"/quit\r")
        except OSError:
            pass
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            child.kill()
        os.close(master)


def termios_unavailable() -> bool:
    """Is the terminal machinery this module needs missing? (Windows, or a stub)."""
    if chatbar.termios is None or chatbar.tty is None:
        return True
    return not hasattr(os, "openpty")
