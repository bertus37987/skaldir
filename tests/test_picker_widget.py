"""The shared widget: ``skaldir.picker.select`` — the one call every list goes through.

What this pins, all of it without a terminal:

* the **contract**: ``select(title, options, *, default, allow_text, out, env, keys, size)``,
  the exact shape ``model_picker.shared_picker()`` insists on before it hands a question over;
* the **answers**: the chosen *value*, ``""`` for "Enter on an empty filter", ``None`` for
  "left without choosing" (``Esc`` and ``Ctrl-C`` alike);
* the **keys**: arrows and ``j``/``k`` (only while the filter is empty), paging, filtering with
  the pointer on the first match, ``Backspace``/``Ctrl-U``;
* the **block**: at most 10 rows + an honest ``… N more``, every line exactly ``columns - 1``
  cells, a height that never changes between paints;
* the **fallback**: the same rows as a numbered list when no block can be drawn.

The real-terminal behaviour — the raw-mode flag, the bytes on the wire — is proved at the
bottom with a real pty, because that is where the defect this round fixed actually lived.
"""

from __future__ import annotations

import fcntl
import inspect
import io
import os
import re
import select
import struct
import sys
import termios
import threading
import time
import tty
from typing import Any

import pytest

from skaldir import model_picker as mp
from skaldir import picker as pk

OPTIONS: list[tuple[str, str]] = [
    ("1 alpha", "alpha"),
    ("2 bravo", "bravo"),
    ("3 charlie", "charlie"),
    ("4 delta", "delta"),
    ("5 echo", "echo"),
]

#: The block's own cursor-up: ``ESC [ <depth> A``. It starts every paint but the first, and
#: it is what makes a repaint distinguishable from a new block in the byte stream.
_FRAME = re.compile(r"\x1b\[[0-9]+A")


def _within(seconds: float, fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run ``fn`` under a deadline: a widget that blocks **fails** the test, never hangs it.

    The suite is the place a terminal regression must be caught; a test that waits forever on
    a pty keeps the whole run from ever finishing and reports nothing. One thread plus a join
    timeout turns "it hangs" into a named failure.
    """
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 — the test re-raises what the call did
            box["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), (
        f"the picker did not return within {seconds}s — it blocked on input instead of "
        "answering"
    )
    if "error" in box:
        raise box["error"]
    return box.get("value")


def frames(sink: io.StringIO) -> list[list[str]]:
    """The lines of **every** paint, in paint order (one list per paint).

    The block redraws in place, so the byte stream legitimately repeats a row the terminal
    shows once: each paint starts with the block's own cursor-up (``ESC [ <depth> A``) and
    each line inside it starts with erase-to-end-of-line (``\\r ESC [ 2K``). Splitting on
    those two sequences is what separates the paints — a helper that ignores them counts
    every repaint as if it were a new block.
    """
    out: list[list[str]] = []
    for chunk in _FRAME.split(sink.getvalue())[1:]:
        parts = chunk.split("\r\x1b[2K")[1:]
        out.append([part.split("\r")[0] for part in parts])
    return out


def painted(sink: io.StringIO) -> list[str]:
    """The lines of the **last** paint — the block as the user is left seeing it."""
    drawn = frames(sink)
    return drawn[-1] if drawn else []



def ask(keys: list[str], **kwargs: Any) -> tuple[Any, io.StringIO]:
    sink: io.StringIO = kwargs.pop("out", None) or io.StringIO()
    answer = pk.select(
        kwargs.pop("title", "pick one"), kwargs.pop("options", OPTIONS),
        keys=keys, out=sink, size=kwargs.pop("size", (80, 24)), **kwargs
    )
    return answer, sink


# --------------------------------------------------------------------------- #
# the contract                                                                  #
# --------------------------------------------------------------------------- #


def test_the_signature_is_exactly_the_one_the_swap_waits_on():
    """``shared_picker`` refuses a widget whose keyword names are not these five."""
    params = set(inspect.signature(pk.select).parameters)
    assert set(mp.SHARED_PICKER_PARAMS) <= params
    assert {"title", "options", "default", "allow_text", "out"} == set(
        mp.SHARED_PICKER_PARAMS
    )


def test_the_real_widget_is_the_one_model_picker_finds():
    assert mp.shared_picker() is pk
    # ``__all__`` holds *names* — the test that asked for the function objects could never
    # pass, whatever the widget did
    assert "select" in pk.__all__ and "read_key" in pk.__all__
    assert all(isinstance(name, str) for name in pk.__all__)
    assert "CANCEL_EXHAUSTED" in pk.__all__


def test_the_options_are_shown_in_their_own_order_with_their_values_kept():
    answer, sink = ask(["down", "enter"])
    assert answer == "bravo"
    body = "\n".join(painted(sink))
    positions = [body.index(label) for label, _value in OPTIONS]
    assert positions == sorted(positions), "the widget never reorders the caller's rows"


def test_default_is_the_index_the_pointer_starts_on():
    assert pk.select("t", OPTIONS, keys=["enter"], out=io.StringIO()) == "alpha"
    assert pk.select("t", OPTIONS, default=3, keys=["enter"],
                     out=io.StringIO()) == "delta"
    # an index past the end is clamped, never a hidden "nothing is selected" state
    assert pk.select("t", OPTIONS, default=99, keys=["enter"],
                     out=io.StringIO()) == "echo"


# --------------------------------------------------------------------------- #
# the answers                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", ["esc", "ctrl-c", "ctrl-d"])
def test_a_dismissal_answers_none(key):
    """"left without choosing" is ``None`` for every key that means it."""
    answer, sink = ask([key])
    assert answer is None
    assert "cancelled: nothing was chosen" in "\n".join(painted(sink))


def test_enter_on_an_empty_filter_answers_the_empty_string():
    """``""`` is "nothing was chosen here" — never a value invented from a blank filter."""
    answer, sink = ask(["enter"], options=[], allow_text=True)
    assert answer == ""
    assert "Enter on an empty list" in "\n".join(painted(sink))


def test_a_typed_id_stands_for_itself_only_when_allow_text_is_on():
    typed = [("1 deepseek-chat", "deepseek-chat"), ("2 deepseek-reasoner", "deepseek-reasoner")]
    assert pk.select("m", typed, allow_text=True, keys=["z", "z", "enter"],
                     out=io.StringIO()) == "zz"
    # without allow_text the same keys leave the typed text unused: the filter matches nothing
    assert pk.select("m", typed, allow_text=False, keys=["z", "z", "enter"],
                     out=io.StringIO()) == ""


def test_a_script_that_ends_answers_nothing_and_says_so():
    answer, sink = ask([])
    assert answer is None
    assert "no answer: the script ended" in "\n".join(painted(sink))


# --------------------------------------------------------------------------- #
# the keys                                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (["down", "enter"], "bravo"),
        (["down", "down", "down", "enter"], "delta"),
        (["j", "enter"], "bravo"),
        (["k", "enter"], "alpha"),            # clamped at the top, never wrapped
        (["down", "up", "enter"], "alpha"),
    ],
)
def test_the_move_keys(keys, expected):
    assert pk.select("t", OPTIONS, keys=keys, out=io.StringIO()) == expected


def test_j_and_k_move_only_while_the_filter_is_empty():
    """Once you type, ``j`` and ``k`` are letters like any other — one rule, not two."""
    rows = [("1 kilo", "kilo"), ("2 jade", "jade"), ("3 lima", "lima")]
    assert pk.select("t", rows, default=2, keys=["k", "enter"], out=io.StringIO()) == "jade"
    assert pk.select("t", rows, keys=["k", "enter"], out=io.StringIO()) == "kilo"


def test_paging_and_home_end_jump_inside_the_list():
    """A page key moves the pointer a whole page (10 rows) and clamps at both ends.

    The step is ``PAGE``, which is what ``skaldir model``'s picker does too
    (``test_setup_selectors.test_the_page_keys_jump_a_page_and_clamp`` asserts the same
    landing): the pointer leaves row 1 and lands on row 11, the first row of the next page.
    """
    rows = [(f"{number} row{number:02d}", f"row{number:02d}") for number in range(1, 21)]
    assert pk.select("t", rows, keys=["pagedown", "enter"], out=io.StringIO()) == "row11"
    assert pk.select("t", rows, keys=["end", "enter"], out=io.StringIO()) == "row20"
    assert pk.select("t", rows, keys=["end", "home", "enter"], out=io.StringIO()) == "row01"
    assert pk.select("t", rows, keys=["pagedown", "pageup", "enter"],
                     out=io.StringIO()) == "row01"


def test_a_page_jump_takes_the_window_with_it():
    """The defect a capture shows: ``PgDn`` moved the pointer ten rows and the window one.

    At 80×24 the block draws ten rows; after one ``PgDn`` the rows on screen must be the
    *second* page, not the first page shifted by a line — otherwise a page key shows the
    user almost nothing new while the pointer silently jumps ahead.
    """
    rows = [(f"{number} row{number:02d}", f"row{number:02d}") for number in range(1, 21)]
    sink = io.StringIO()
    answer = pk.select("t", rows, keys=["pagedown", "enter"], out=sink, size=(80, 24))
    assert answer == "row11"
    drawn = "\n".join(painted(sink))
    assert "row11" in drawn and "row20" in drawn, "the second page is what is on screen"
    assert "row01" not in drawn, "the first page is gone, not shifted"
    assert "… 10 above" in drawn, "and the block says ten rows are above it"


def test_typing_narrows_and_the_pointer_lands_on_the_first_match():
    rows = [("1 deepseek", "deepseek"), ("2 openai", "openai"), ("3 deepinfra", "deepinfra")]
    answer, sink = ask(["o", "enter"], options=rows, default=2)
    assert answer == "openai", "the pointer followed the filter instead of staying put"
    assert "deepinfra" not in "\n".join(painted(sink))
    assert 'filter "o"' in "\n".join(painted(sink))


def test_backspace_and_ctrl_u_edit_the_filter():
    rows = [("1 deepseek", "deepseek"), ("2 openai", "openai")]
    assert pk.select("t", rows, keys=["o", "p", "backspace", "enter"],
                     out=io.StringIO()) == "openai"
    assert pk.select("t", rows, keys=["o", "ctrl-u", "d", "enter"],
                     out=io.StringIO()) == "deepseek"


# --------------------------------------------------------------------------- #
# the block                                                                     #
# --------------------------------------------------------------------------- #


def test_the_block_is_exactly_width_cells_and_a_fixed_height():
    sink = io.StringIO()
    rows = [(f"{number} row{number:02d}", f"row{number:02d}") for number in range(1, 21)]
    pk.select("the question", rows, out=sink, keys=["down", "esc"], size=(80, 24))
    drawn = frames(sink)
    assert drawn, "something was drawn"
    page = pk.PAGE
    # header + the row budget (page + the "… N more" row) + the status row, on EVERY paint
    for paint in drawn:
        assert len(paint) == page + 3, "the height never changes between paints"
        assert all(len(line) == 79 for line in paint), "columns - 1, so the terminal never wraps"
    # a filter that narrows the list does not change the height either: the block cannot walk down
    sink = io.StringIO()
    pk.select("the question", rows, out=sink, keys=["r", "o", "w", "0", "1", "esc"],
              size=(80, 24))
    narrowed = frames(sink)
    assert narrowed and all(len(paint) == page + 3 for paint in narrowed)


def test_at_most_ten_rows_and_an_honest_count_of_the_rest():
    sink = io.StringIO()
    rows = [(f"{number} provider{number:02d}", f"p{number:02d}") for number in range(1, 17)]
    pk.select("providers", rows, out=sink, keys=["enter"], size=(80, 24))
    text = "\n".join(painted(sink))
    assert "providers (16)" in text, "the header counts every row the caller gave"
    assert "… 6 more" in text
    # the header *row* carries the word twice by itself ("providers" + the count is not a row):
    # count the rows, not the substring — ten numbered rows are drawn, and no more
    for paint in frames(sink):
        assert len([line for line in paint if re.search(r"\d+ provider\d\d", line)]) == 10
        assert len(paint) == pk.PAGE + 3


def test_a_short_terminal_draws_a_shorter_block_but_never_overflows():
    sink = io.StringIO()
    rows = [(f"{number} row{number:02d}", f"row{number:02d}") for number in range(1, 21)]
    pk.select("q", rows, out=sink, keys=["enter"], size=(80, 8))
    drawn = frames(sink)
    assert drawn and all(len(paint) == 8 for paint in drawn)
    assert all(len(line) == 79 for paint in drawn for line in paint)
    # ten rows would need twelve; on eight lines the block draws what fits and says so
    assert any("more" in line for line in drawn[-1]), "the honest count survives the shrink"


def test_no_options_without_allow_text_is_not_a_question():
    sink = io.StringIO()
    assert pk.select("nothing", [], out=sink, keys=["enter"]) is None
    assert painted(sink) == []


# --------------------------------------------------------------------------- #
# the plain fallback (no block can be drawn here)                               #
# --------------------------------------------------------------------------- #


def _plain(monkeypatch, stdin: str, **kwargs: Any) -> tuple[Any, str]:
    sink = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    answer = pk.select("pick a provider", kwargs.pop("options", OPTIONS),
                       out=sink, env={"NO_COLOR": "1"}, **kwargs)
    return answer, sink.getvalue()


def test_the_fallback_prints_the_same_rows_numbered(monkeypatch):
    answer, body = _plain(monkeypatch, "2\n")
    assert answer == "bravo"
    for position, (label, _value) in enumerate(OPTIONS, start=1):
        assert f"{position:>2}. {label}" in body
    assert picker_header_in(body), "the header survived the fallback"


def picker_header_in(body: str) -> bool:
    return "pick a provider (5)" in body


def test_the_fallback_accepts_a_number_and_refuses_a_blank_line(monkeypatch):
    assert _plain(monkeypatch, "q\n")[0] is None
    assert _plain(monkeypatch, "\n")[0] is None
    # three numbers outside 1..5: the list ran out of answers. ``None`` ("left without
    # choosing") — *not* ``""``, which means "there was nothing to pick": a caller that
    # treats ``""`` as "the user cleared this value" must never be handed one here.
    assert _plain(monkeypatch, "9\n9\n9\n")[0] is None


def test_running_out_of_answers_is_its_own_reason():
    """The plain runner names why it stopped — an empty list and an exhausted list differ."""
    sink = io.StringIO()
    picker = pk.Picker([pk.Item(value="alpha", title="1 alpha")], title="t")
    outcome = pk.plain_menu(
        picker,
        write=lambda line: sink.write(str(line) + "\n"),
        read=lambda prompt: "7",
        attempts=2,
    )
    assert outcome.kind == pk.CANCEL
    assert outcome.why == pk.CANCEL_EXHAUSTED
    assert outcome.ok is False
    empty = pk.Picker([], title="t")
    assert pk.plain_menu(empty, write=lambda line: None, read=lambda prompt: "") \
        .why == pk.CANCEL_EMPTY


def test_the_fallback_on_an_empty_list_is_the_empty_answer(monkeypatch):
    answer, _body = _plain(monkeypatch, "\n", options=[], allow_text=True)
    assert answer == ""


def test_the_fallback_writes_no_screen_control_at_all(monkeypatch):
    _answer, body = _plain(monkeypatch, "1\n")
    assert "\x1b" not in body
    assert body.isascii()


# --------------------------------------------------------------------------- #
# the terminal flag: ``^C`` is a key, not a signal                              #
# --------------------------------------------------------------------------- #


def pty_available() -> bool:
    return hasattr(os, "openpty") and sys.platform != "win32"


def make_pty() -> tuple[int, int]:
    """A real pty, sized 80x24 on both sides — the geometry a block is drawn at."""
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    return master, slave


def test_raw_mode_clears_isig_so_a_ctrl_c_arrives_as_a_key():
    """The defect this round fixed: ``tty.setcbreak`` alone leaves ``ISIG`` on."""
    if not pty_available():  # pragma: no cover — Windows
        pytest.skip("no pty on this platform")
    master, slave = make_pty()
    try:
        saved = termios.tcgetattr(slave)
        tty.setcbreak(slave)
        assert termios.tcgetattr(slave)[3] & termios.ISIG, "the trap the fix closes"
        pk.raw_mode(termios, slave, saved)
        flags = termios.tcgetattr(slave)[3]
        assert not flags & termios.ISIG
        assert not flags & termios.ECHO and not flags & termios.ICANON
        termios.tcsetattr(slave, termios.TCSADRAIN, saved)
        assert termios.tcgetattr(slave)[3] & termios.ISIG, "and the flag comes back"
    finally:
        os.close(master)
        os.close(slave)


def test_the_key_reader_names_the_bytes_the_widget_switches_on():
    """A pipe is enough: the names are what ``Picker.handle`` switches on, not the bytes."""
    if not pty_available():  # pragma: no cover — Windows
        pytest.skip("no pty on this platform")
    read_fd, write_fd = os.pipe()
    try:
        for wire, wanted in (
            (b"\x1b[A", "up"),
            (b"\x1b[B", "down"),
            (b"\x1b[6~", "pagedown"),
            (b"\x1b[5~", "pageup"),
            (b"\x03", "ctrl-c"),
            (b"\x04", "ctrl-d"),
            (b"\r", "enter"),
            (b"\x7f", "backspace"),
            (b"q", "q"),
            (b"\x1b", "esc"),
        ):
            os.write(write_fd, wire)
            assert pk.read_key(read_fd, timeout=0.5) == wanted
    finally:
        os.close(read_fd)
        os.close(write_fd)


def drain(fd: int, *, timeout: float = 0.5) -> str:
    """Everything written to the pty's *slave* before the timeout, decoded.

    Read from the **master**: that is the direction the widget's own bytes travel (writing to
    a dup of the slave would block on an empty input queue, which is how a capture turns into
    a hang of its own).
    """
    body = b""
    while True:
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            break
        try:
            chunk = os.read(fd, 65536)
        except OSError:  # pragma: no cover — a closed pty is nothing left to read
            break
        if not chunk:
            break
        body += chunk
    return body.decode("utf-8", "replace")


def feed(master: int, wire: bytes, *, delay: float = 0.4) -> threading.Thread:
    """Type into the pty **after** the widget has taken the terminal.

    The delay is not decoration. ``raw_mode`` switches the terminal's attributes with
    ``TCSAFLUSH``, which throws away whatever was typed before the widget owned it: a byte
    written first is a byte the widget never sees, and the capture then waits forever for a
    key that was already discarded. (The numbered fallback does not switch attributes, so it
    reads a line written before the call — see the test below.)
    """

    def run() -> None:
        time.sleep(delay)
        try:
            os.write(master, wire)
        except OSError:  # pragma: no cover — the pty went away under us
            pass

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker


def test_the_block_is_drawn_on_a_real_terminal_and_ctrl_c_cancels_the_question():
    """The end-to-end shape of the fix, at 80x24: a real pty, real bytes, real restore.

    ``env`` pins ``TERM`` on purpose. Whether a block is drawn is the *environment's*
    decision (a missing or ``dumb`` ``TERM`` gets the numbered list), and a numbered list on
    a pty waits for a line that this test never writes — that was this test's own hang, not
    the widget's. With the environment pinned, the bytes below are the widget's.
    """
    if not pty_available():  # pragma: no cover — Windows
        pytest.skip("no pty on this platform")
    master, slave = make_pty()
    saved_stdin = sys.stdin
    try:
        stream = os.fdopen(os.dup(slave), "w")
        sys.stdin = os.fdopen(os.dup(slave), "r")
        feed(master, b"\x03")
        answer = _within(
            10.0, pk.select, "pick a provider", OPTIONS, out=stream, env={"TERM": "xterm"}
        )
        stream.flush()
        assert answer is None
        written = drain(master)
        assert "\x1b[" in written, "the block went to the pty, not a numbered list"
        assert "cancelled: nothing was chosen" in written
        # the widget restored the terminal it borrowed
        flags = termios.tcgetattr(slave)[3]
        assert flags & termios.ISIG and flags & termios.ICANON
    finally:
        sys.stdin = saved_stdin
        os.close(master)
        os.close(slave)


def test_a_dumb_terminal_on_a_pty_takes_the_numbered_list_and_still_answers():
    """``NO_COLOR`` on a real terminal: the same rows as lines, one line read, no hang.

    The fallback never switches the terminal's attributes, so a line written before the call
    is still there to be read — this is the path that used to block a capture forever when
    the environment (not the widget) decided the question could not be drawn.
    """
    if not pty_available():  # pragma: no cover — Windows
        pytest.skip("no pty on this platform")
    master, slave = make_pty()
    saved_stdin = sys.stdin
    try:
        stream = os.fdopen(os.dup(slave), "w")
        sys.stdin = os.fdopen(os.dup(slave), "r")
        os.write(master, b"2\n")
        answer = _within(
            10.0, pk.select, "pick a provider", OPTIONS, out=stream,
            env={"TERM": "xterm", "NO_COLOR": "1"},
        )
        stream.flush()
        assert answer == "bravo", "the numbered answer is the row's value"
        assert "\x1b" not in drain(master), "the fallback writes no screen control"
    finally:
        sys.stdin = saved_stdin
        os.close(master)
        os.close(slave)
