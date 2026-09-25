"""One selector for the whole product: a list you move in, filter and pick from.

The owner asked for a session picker that is arrow-navigable and for **the same widget
everywhere**. This module is that widget. It is deliberately terminal-free: it holds the items,
the filter, the cursor and the visible window, and turns one key into one decision. Two runners
use it:

* :meth:`skaldir.chatbar.ChatBar.pick` — inside the two-bar frame, driven by the real key reader
  (arrows, ``j``/``k``, PgUp/PgDn, live filtering, Enter, Esc, Ctrl-C);
* :func:`select` — the product's one call: a block (title, ≤ 10 rows + ``… N more``, one status
  row) drawn on ``stderr`` and redrawn in place, driven by :func:`read_key`; the terminal is in
  :func:`raw_mode` (``ISIG`` cleared, so ``^C`` cancels the *question*) and restored in a
  ``finally``. ``skaldir setup``, ``skaldir model`` and every other list go through it;
* :func:`plain_menu` — the numbered list a pipe, a redirect, ``TERM=dumb`` or ``NO_COLOR`` gets:
  the rows are printed and the user types a number. :func:`select` falls back to it by itself
  when no block can be drawn or driven.

Public API (stable; other rounds may reuse it)::

    Item(value, title, detail="", meta="")
    Picker(items, *, title="", page=PAGE, unicode=True, width=0, query="")
    Picker.visible() -> list[Item]      # after the filter
    Picker.rows()    -> list[str]       # <= page rows, plus an honest "… N more" row
    Picker.handle(key) -> "none" | "redraw" | "select" | "cancel"
    Picker.selected  -> Item | None     # set by "select"
    Picker.cancelled -> ("esc" | "ctrl-c" | "empty") | None
    Result(spec)      # kind: "select" | "cancel" | "none"
    select(title, options, *, default=0, allow_text=False, out=None, env=None,
           keys=None, size=None) -> str | None
    read_key(fd, *, timeout=0.25, escape_timeout=0.05) -> str | None
    raw_mode(termios_module, fd, saved) -> None
    plain_menu(picker, write=print, read=input, page=PAGE) -> Result
    from_sessions(payload) / from_dicts(rows, key, ...) -> list[Item]

Keys (the framed runner maps them; the picker only names them): ``up``/``down`` move, ``j``/``k``
move **while the filter is empty** (once you type, ``j``/``k`` are letters like any other),
``pageup``/``pagedown`` page, ``home``/``end`` jump, a printable character filters live (with an
empty filter the first letter therefore jumps the selection to the first match — one rule, not
two), ``backspace``/``ctrl-u`` edit the filter, ``enter`` selects, ``esc``/``ctrl-c`` cancel and
the session survives.
"""

from __future__ import annotations

import os
import select as _select
import shutil
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: rows drawn at once before the widget says how many are left
PAGE = 10

#: the marks (each has an ASCII twin: colour is never the information)
MARK_RICH = "❯"
MARK_PLAIN = ">"

#: cancels, by name (so a caller can say *why* nothing was picked)
CANCEL_ESC = "esc"
CANCEL_CTRL_C = "ctrl-c"
CANCEL_EMPTY = "empty"
#: the plain list ran out of answers: it typed a number outside ``1..N`` more times than the
#: runner allows. Distinct from ``empty`` on purpose — ``empty`` means "there was nothing to
#: pick", and the two must not answer the same value (``""`` vs ``None``).
CANCEL_EXHAUSTED = "exhausted"

SELECT = "select"
CANCEL = "cancel"
NONE = "none"


@dataclass(frozen=True)
class Item:
    """One row: the value the caller gets back, and what a user needs to choose it."""

    value: str
    title: str = ""
    detail: str = ""
    meta: str = ""

    def label(self) -> str:
        """``title`` if it has one, else the value (a row is never blank)."""
        return self.title or self.value


@dataclass
class Result:
    """What a picker run answered: the selected item, or the reason there is none."""

    kind: str = NONE
    item: Item | None = None
    index: int = -1
    query: str = ""
    why: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == SELECT and self.item is not None

    @property
    def value(self) -> str:
        return self.item.value if self.item is not None else ""


@dataclass
class Picker:
    """The state machine: items, filter, cursor, window. No I/O, no clock, no terminal."""

    items: list[Item] = field(default_factory=list)
    title: str = ""
    page: int = PAGE
    unicode: bool = True
    width: int = 0
    query: str = ""
    index: int = 0
    top: int = 0
    selected: Item | None = None
    cancelled: str | None = None
    #: the last key the widget did something with (for a test or a debug line)
    last_key: str = ""

    # -- the filter --------------------------------------------------------- #

    def visible(self) -> list[Item]:
        """The items the filter admits, in the order they were given."""
        needle = self.query.strip().lower()
        if not needle:
            return list(self.items)
        terms = [term for term in needle.split() if term]
        out: list[Item] = []
        for item in self.items:
            haystack = " ".join(
                (item.value, item.title, item.detail, item.meta)
            ).lower()
            if all(term in haystack for term in terms):
                out.append(item)
        return out

    # -- the window --------------------------------------------------------- #

    def window(self) -> tuple[list[Item], int]:
        """``(rows to draw, how many matches are hidden below them)``."""
        items = self.visible()
        size = max(1, int(self.page))
        if not items:
            return [], 0
        self.index = max(0, min(self.index, len(items) - 1))
        if self.index < self.top:
            self.top = self.index
        elif self.index >= self.top + size:
            self.top = self.index - size + 1
        self.top = max(0, min(self.top, max(0, len(items) - size)))
        rows = items[self.top:self.top + size]
        hidden = max(0, len(items) - (self.top + len(rows)))
        return rows, hidden

    def rows(self) -> list[str]:
        """The rows as text: ``❯`` on the cursor's row, the count of the rest under them."""
        items = self.visible()
        if not items:
            return [f"  no match for {self.query!r}" if self.query else "  (nothing to pick)"]
        window, hidden = self.window()
        mark = MARK_RICH if self.unicode else MARK_PLAIN
        blank = " " * len(mark)
        lines: list[str] = []
        for offset, item in enumerate(window):
            position = self.top + offset
            head = f"{mark if position == self.index else blank} {item.label()}"
            tail = " · ".join(part for part in (item.detail, item.meta) if part)
            lines.append(f"{head}  {tail}".rstrip() if tail else head)
        if hidden:
            lines.append(f"{blank} … {hidden} more")
        elif self.top:
            lines.append(f"{blank} … {self.top} above")
        return [self._clip(line) for line in lines]

    def rows_horizontal(self) -> list[str]:
        """One row of options (the approve bar): ``←``/``→`` moves, ``Enter`` selects.

        Used by the live-desktop first-use gate. The mark is never the only signal —
        each option is always spelled out. ``hint``/``rows`` stay the vertical form;
        a caller that wants the bar swaps in this method.
        """
        items = self.visible()
        if not items:
            return ["  (nothing to approve)"]
        parts: list[str] = []
        for at, item in enumerate(items):
            label = item.label()
            if at == self.index:
                parts.append(f"❯ {label} ❮" if self.unicode else f"[{label}]")
            else:
                parts.append(f"  {label}  ")
        line = "  " + " · ".join(parts)
        hint = "  ←/→ move · Enter approve · Esc cancel"
        return [self._clip(line), self._clip(hint)]

    def header(self) -> str:
        """One line above the rows: what is being picked and what the filter is doing."""
        total = len(self.items)
        shown = len(self.visible())
        bits = [f"{self.title or 'select'} ({total})"]
        if self.query:
            bits.append(f'filter "{self.query}" → {shown}')
        elif shown != total:
            bits.append(f"{shown} match(es)")
        return " · ".join(bits)

    def hint(self) -> str:
        """The keys the widget really binds (the frame's hint row carries this)."""
        return ("↑/↓←/→ move · j/k move (empty filter) · PgUp/PgDn page · type to filter · "
                "Enter select · Esc cancel")

    # -- the key ------------------------------------------------------------ #

    def handle(self, key: str) -> str:
        """One key → ``"none"``, ``"redraw"``, ``"select"`` or ``"cancel"``."""
        self.last_key = str(key)
        if key == "enter":
            items = self.visible()
            if not items:
                return NONE
            self.selected = items[min(self.index, len(items) - 1)]
            return SELECT
        if key in ("esc", "ctrl-c"):
            self.cancelled = CANCEL_CTRL_C if key == "ctrl-c" else CANCEL_ESC
            return CANCEL
        if key in ("up", "down"):
            return self._move(-1 if key == "up" else 1)
        if key in ("j", "k") and not self.query:
            # j/k move only while no filter is typed: once the filter has text, j and k are
            # letters like any other (one rule, stated, instead of a key that changes meaning
            # silently)
            return self._move(-1 if key == "k" else 1)
        if key in ("pageup", "pagedown"):
            step = max(1, self.page)
            return self._move(-step if key == "pageup" else step, by_page=True)
        if key == "home":
            return self._move(-len(self.items) - 1, by_page=True)
        if key == "end":
            return self._move(len(self.items) + 1, by_page=True)
        if key == "left":
            # ←/→ walk the options (the approve bar is horizontal) and **wrap** —
            # three choices, a bar that dead-ends at the edge is worse than ↑/↓'s clamp
            return self._move(-1, wrap=True)
        if key == "right":
            return self._move(1, wrap=True)
        if key in ("backspace",):
            if self.query:
                self.query = self.query[:-1]
            return self._refilter()
        if key == "ctrl-u":
            self.query = ""
            return self._refilter()
        if len(key) == 1 and key.isprintable() and key != " ":
            self.query += key
            return self._refilter()
        if key == " ":
            self.query += " "
            return self._refilter()
        return NONE

    def _move(self, step: int, *, by_page: bool = False, wrap: bool = False) -> str:
        """Move the cursor; a **page jump** scrolls the window by the same amount.

        A single step keeps the window where it is until the cursor would leave it (the
        cursor then sits on the window's edge — the smooth-scroll rule). A jump of a whole
        page scrolls the window with the pointer instead, because ``PgDn`` that moves the
        pointer ten rows while the window follows by one shows the user almost nothing new.
        ``wrap=True`` (the approve bar's ``←``/``→``) turns past the last row back to the
        first instead of stopping.
        """
        items = self.visible()
        if not items:
            return NONE
        if wrap and not by_page and len(items) > 1:
            self.index = (self.index + step) % len(items)
            return "redraw"
        self.index = max(0, min(self.index + step, len(items) - 1))
        if by_page:
            size = max(1, int(self.page))
            self.top = max(0, min(self.top + step, max(0, len(items) - size)))
        return "redraw"

    def _refilter(self) -> str:
        """A filter edit: the cursor goes to the first match (that is the first-letter jump)."""
        self.query = self.query.strip()
        self.index = 0
        self.top = 0
        return "redraw"

    def result(self) -> Result:
        """The outcome as a :class:`Result` (``none`` while nothing was decided)."""
        if self.selected is not None:
            position = self.top + max(0, self.index - self.top)
            return Result(kind=SELECT, item=self.selected, index=position, query=self.query)
        if self.cancelled:
            return Result(kind=CANCEL, why=self.cancelled, query=self.query)
        return Result(kind=NONE, query=self.query)

    def _clip(self, line: str) -> str:
        limit = int(self.width or 0)
        if limit <= 0 or len(line) <= limit:
            return line
        if limit <= 1:
            return line[:limit]
        return line[: limit - 1] + ("…" if self.unicode else "~")


# --------------------------------------------------------------------------- #
# the plain runner: a numbered list, for a pipe / a dumb terminal               #
# --------------------------------------------------------------------------- #


def plain_lines(picker: Picker, rendered: Sequence[str] | None = None) -> list[str]:
    """The numbered list a non-terminal gets (the same rows, numbered, no marks)."""
    items = picker.visible()
    rows = list(rendered) if rendered is not None else picker.rows()
    out = [f"  {picker.header()}"]
    for position, row in enumerate(rows, start=1):
        body = row.lstrip(MARK_PLAIN + MARK_RICH + " ").strip()
        out.append(f"  {position:>2}. {body}")
    if not items:
        out = [f"  {picker.header()}"]
    return out


def plain_menu(
    picker: Picker,
    *,
    write: Callable[[str], Any] = print,
    read: Callable[[str], str] = input,
    prompt: str = "pick a number (Enter cancels): ",
    attempts: int = 3,
) -> Result:
    """The fallback: print the numbered rows and read the number.

    A pipe, a redirect, ``TERM=dumb`` or ``NO_COLOR`` has no arrow keys, and a picker that cannot
    be driven is worse than a list. Enter (nothing typed), a non-number or ``q`` cancels — and a
    number outside the list says so instead of picking a neighbour. Running out of attempts is
    its own reason (``CANCEL_EXHAUSTED``): it answered ``None``, never the empty-string answer
    that means "there was nothing to pick".
    """
    items = picker.visible()
    for line in plain_lines(picker):
        write(line)
    if not items:
        return Result(kind=CANCEL, why=CANCEL_EMPTY, query=picker.query)
    for _ in range(max(1, int(attempts))):
        try:
            answer = str(read(prompt)).strip()
        except (EOFError, KeyboardInterrupt):
            return Result(kind=CANCEL, why=CANCEL_CTRL_C, query=picker.query)
        if not answer or answer.lower() in ("q", "quit", "esc"):
            return Result(kind=CANCEL, why=CANCEL_ESC, query=picker.query)
        if answer.isdigit():
            position = int(answer)
            if 1 <= position <= len(items):
                picker.index = position - 1
                picker.selected = items[position - 1]
                return picker.result()
        write(f"  {answer!r} is not one of 1..{len(items)}")
    return Result(kind=CANCEL, why=CANCEL_EXHAUSTED, query=picker.query)


# --------------------------------------------------------------------------- #
# the shared call: the one function the rest of the product hands a list to     #
# --------------------------------------------------------------------------- #
#
# ``skaldir setup``, ``skaldir model`` and every other list ask their question through
# :func:`select`. It is a thin adapter over :class:`Picker`: the widget owns the items, the
# filter and the cursor; this function owns the two things a state machine cannot own — the
# terminal (raw keys, one block redrawn in place) and the *plain* fallback a pipe gets.
#
# The signature is a contract, not a convenience: ``model_picker.shared_picker()`` looks for
# ``select`` with the keyword names ``title, options, default, allow_text, out`` and refuses a
# widget with another vocabulary, so nothing is ever called with the wrong meaning.

#: The keys this driver answers, by name (the bytes are decoded once, here).
_KEYS: dict[str, str] = {
    "\x03": "ctrl-c",
    "\x04": "ctrl-d",
    "\x08": "backspace",
    "\x0c": "ctrl-l",
    "\x15": "ctrl-u",
    "\r": "enter",
    "\n": "enter",
    "\x7f": "backspace",
}

#: What the letters after ``ESC [`` stand for, and the numbered ``ESC [ <n> ~`` keys.
_FINALS: dict[str, str] = {
    "A": "up", "B": "down", "C": "right", "D": "left", "F": "end", "H": "home",
}
_TILDES: dict[str, str] = {
    "1": "home", "2": "insert", "3": "delete", "4": "end", "5": "pageup", "6": "pagedown",
}

#: The lines the block keeps for itself besides the rows: the header and the status row. The
#: ``… N more`` row is part of the row budget, so the block's height is exactly
#: ``page + 3`` and never changes between paints.
CHROME_ROWS = 2

#: The narrowest and the shortest terminal a block is still drawn on.
MIN_COLUMNS = 40
MIN_LINES = 8

#: What the status row says when a question ends.
SELECTED_STATUS = "selected: {value}"
CANCELLED_STATUS = "cancelled: nothing was chosen"
EMPTY_STATUS = "nothing was chosen: Enter on an empty list"
EXHAUSTED_STATUS = "no answer: the script ended"


def _text_unicode(stream: Any) -> bool:
    """Is this stream a UTF-8 one? (the block falls back to ASCII marks when it is not)"""
    return "utf" in str(getattr(stream, "encoding", "") or "").lower()


def _fold(text: Any, unicode: bool) -> str:
    """The ASCII twin of a string, when the stream cannot carry the pretty one."""
    body = str(text)
    return body if unicode else body.encode("ascii", "replace").decode("ascii")


def _fit(text: Any, width: int, *, unicode: bool = True) -> str:
    """Clip one line to ``width`` cells, saying so — a line never wraps inside the block."""
    body = _fold(text, unicode)
    limit = max(0, int(width))
    if len(body) <= limit:
        return body
    if limit <= 1:
        return body[:limit]
    return body[: limit - 1] + ("…" if unicode else "~")


def _terminal_size(size: tuple[int, int] | None = None) -> tuple[int, int]:
    """``(columns, lines)`` for this terminal — 80×24 when nothing answers."""
    if size is not None:
        try:
            return max(1, int(size[0])), max(1, int(size[1]))
        except Exception:  # noqa: BLE001 — a size that is not two numbers is not a size
            return 80, 24
    try:
        measured = shutil.get_terminal_size((80, 24))
        return int(measured.columns), int(measured.lines)
    except Exception:  # noqa: BLE001 — no measurement is not a reason to fail a question
        return 80, 24


def _is_tty(stream: Any) -> bool:
    try:
        return bool(stream is not None and stream.isatty())
    except Exception:  # noqa: BLE001 — a stream that cannot answer is not a terminal
        return False


def _fd_of(stream: Any) -> int:
    """The descriptor behind a stream, or ``-1`` when it has none (a captured stream)."""
    try:
        return int(stream.fileno())
    except Exception:  # noqa: BLE001 — a stream without a descriptor cannot drive a block
        return -1


def _drivable(env: Mapping[str, str], out: Any, source: Any) -> bool:
    """Can a block be **drawn and driven** here? The same refusals ``selectable`` names.

    ``SKALDIR_PLAIN``, ``NO_COLOR``, an unset or ``dumb`` ``TERM``, a stream that is not a
    terminal, and a platform without ``termios``: each one means the plain numbered list is
    the honest answer instead of screen control nobody can drive.
    """
    if str(env.get("SKALDIR_PLAIN") or "").strip():
        return False
    if "NO_COLOR" in env:
        return False
    term = str(env.get("TERM") or "").strip()
    if not term or term == "dumb":
        return False
    if not _is_tty(out) or not _is_tty(source):
        return False
    return _termios()[0] is not None


def _termios() -> tuple[Any, Any]:
    """The terminal control modules, or ``(None, None)`` where there are none."""
    try:
        import termios as module
        import tty as tty_module
    except Exception:  # noqa: BLE001 — no termios means no block on this platform
        return None, None
    return module, tty_module


def _byte(fd: int, timeout: float) -> bytes:
    """At most one byte from ``fd``, or nothing when the timeout passes first."""
    try:
        ready, _, _ = _select.select([fd], [], [], timeout)
    except (OSError, ValueError):
        return b""
    if not ready:
        return b""
    try:
        return os.read(fd, 1)
    except OSError:
        return b""


def _character(fd: int, first: bytes, timeout: float) -> str:
    """One character, continuation bytes included (a ``ü`` is not half a character)."""
    body = first
    while body:
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            if len(body) >= 4:
                return body.decode("utf-8", "replace")
            more = _byte(fd, timeout)
            if not more:
                return body.decode("utf-8", "replace")
            body += more
    return ""


def _after_escape(fd: int, timeout: float) -> str:
    """``ESC`` alone (a dismissal) or the sequence behind it (an arrow, ``PageDown``)."""
    follow = _byte(fd, timeout)
    if follow[:1] != b"[":
        return "esc"
    body = b""
    while len(body) < 8:
        chunk = _byte(fd, timeout)
        if not chunk:
            break
        body += chunk
        if 0x40 <= chunk[0] <= 0x7E:
            break
    text = body.decode("ascii", "replace")
    if text.endswith("~"):
        return _TILDES.get(text[:-1], "esc")
    return _FINALS.get(text[-1:], "esc")


def read_key(fd: int, *, timeout: float = 0.25, escape_timeout: float = 0.05) -> str | None:
    """One key from ``fd``: a name for the special ones, the character itself otherwise.

    ``None`` means "nothing arrived before the timeout" — the caller then repaints and reads
    again, which is how an idle question stays quiet. The names are the ones :class:`Picker`
    switches on (``up``, ``pagedown``, ``ctrl-c``, …), so the bytes are decoded exactly once
    in the product.
    """
    first = _byte(fd, timeout)
    if not first:
        return None
    text = _character(fd, first, escape_timeout)
    if text == "\x1b":
        return _after_escape(fd, escape_timeout)
    if text in _KEYS:
        return _KEYS[text]
    return text if text.isprintable() else ""


def raw_mode(termios_mod: Any, fd: int, saved: Any) -> None:
    """Character-at-a-time input with ``ISIG`` **cleared**, so ``^C`` is a key, not a signal.

    ``tty.setcbreak`` leaves ``ISIG`` on: ``^C`` then becomes a ``SIGINT`` and ends the whole
    run where the user asked to cancel one question. Here the byte ``\\x03`` reaches the loop,
    which answers it exactly like ``Esc``, and the caller restores ``saved`` in a ``finally``.
    """
    mode = list(saved)
    mode[3] = int(mode[3]) & ~(termios_mod.ECHO | termios_mod.ICANON | termios_mod.ISIG)
    flags = list(mode[6])
    flags[termios_mod.VMIN] = 1
    flags[termios_mod.VTIME] = 0
    mode[6] = flags
    termios_mod.tcsetattr(fd, termios_mod.TCSAFLUSH, mode)


class _Block:
    """A fixed-height block of lines the widget owns and redraws **in place**.

    The space is reserved *before* the first paint, and that one write is the only write
    allowed to scroll the screen: it happens while the block is still empty. Every later
    paint lands strictly inside rows that already exist, so the arithmetic cannot be off by a
    line that scrolled away.
    """

    def __init__(self, sink: Any) -> None:
        self.sink = sink
        self.depth = 0

    def paint(self, lines: Sequence[str]) -> None:
        body = [str(line) for line in lines]
        try:
            if not self.depth:
                self.sink.write("\r\n" * len(body))
                self.depth = len(body)
            self.sink.write(f"\x1b[{self.depth}A")
            for index, line in enumerate(body):
                # erase the line, then draw it: a shorter row leaves no tail behind
                self.sink.write("\r\x1b[2K" + line)
                if index < len(body) - 1:
                    self.sink.write("\r\n")
            self.sink.write("\r\n")
            self.sink.flush()
        except Exception:  # noqa: BLE001 — a stream that went away ends the drawing
            pass


def _block(picker: Picker, *, width: int, page: int, status: str = "") -> list[str]:
    """The block for one paint: a header, the rows, the ``… N more`` row, a status row.

    Every line is exactly ``width`` cells and the height is fixed (``page + 3``), so the
    terminal never wraps and a filter that narrows the list cannot make the block walk down
    the screen. The count of hidden rows is the widget's own (``Picker.rows``), so the two
    can never disagree.
    """
    unicode_mode = bool(picker.unicode)
    limit = max(1, int(width))
    lines = [_fit(picker.header(), limit, unicode=unicode_mode)]
    rows = list(picker.rows())[: int(page) + 1]
    lines += rows + [" " * limit] * max(0, int(page) + 1 - len(rows))
    lines.append(_fit(status or picker.hint(), limit, unicode=unicode_mode))
    return [line.ljust(limit) for line in lines]


def _line_writer(sink: Any) -> Callable[[str], None]:
    """A ``write one line`` for a stream that never raises into the caller's face."""

    def write(text: str) -> None:
        try:
            sink.write(str(text) + "\n")
            sink.flush()
        except Exception:  # noqa: BLE001 — a closed stream ends the line, not the session
            pass

    return write


def _line_reader(sink: Any) -> Callable[[str], str]:
    """Read one line from stdin, with the prompt written to the stream the rows went to."""

    def read(prompt: str) -> str:
        try:
            sink.write(str(prompt))
            sink.flush()
            return str(sys.stdin.readline()).rstrip("\n")
        except Exception:  # noqa: BLE001 — a stdin that cannot be read is not an answer
            return ""

    return read


def _plain_select(picker: Picker, *, sink: Any) -> str | None:
    """The fallback :func:`select` answers with when no block can be drawn or driven.

    The same rows, printed as a numbered list, the answer read as one line — the contract's
    own two special answers are kept: ``""`` for Enter on an empty list, ``None`` for a
    dismissal.
    """
    outcome = plain_menu(picker, write=_line_writer(sink), read=_line_reader(sink))
    if outcome.ok:
        return outcome.value
    return "" if outcome.why == CANCEL_EMPTY else None


def select(
    title: str,
    options: Sequence[tuple[str, str]],
    *,
    default: int = 0,
    allow_text: bool = False,
    out: Any = None,
    env: Mapping[str, str] | None = None,
    keys: Iterable[str] | None = None,
    size: tuple[int, int] | None = None,
) -> str | None:
    """Ask one question with a selection list. The product's one selector.

    ``options`` is ``(the line to show, the value it stands for)`` in display order;
    ``default`` is the **index** of the row the pointer starts on; the answer is the chosen
    **value**, ``""`` for "Enter on an empty filter" and ``None`` for "left without choosing".
    ``out`` is the stream the block is drawn on (``stderr``: stdout is the one document).

    Keys: ``↑``/``↓`` (or ``k``/``j``) move, ``PgUp``/``PgDn`` page, ``Home``/``End`` jump, a
    printable character filters live and puts the pointer on the first match, ``Backspace`` /
    ``Ctrl-U`` edit the filter, ``Enter`` selects — with ``allow_text`` an unmatched filter
    stands for itself — and ``Esc``/``Ctrl-C`` leave **the question** without an answer. The
    terminal is in raw mode (``ISIG`` cleared, so ``^C`` is a key) and restored in a
    ``finally``, whatever happens.

    ``keys`` is a scripted key source and ``size`` a geometry: both exist so the widget is
    testable without a terminal. Where a block cannot be drawn or driven (a pipe, ``TERM=dumb``,
    ``NO_COLOR``, ``SKALDIR_PLAIN``, a platform without ``termios``) the same rows are printed
    as a numbered list and one line is read.
    """
    sink = sys.stderr if out is None else out
    env_map = dict(os.environ if env is None else env)
    pairs = [(str(label), str(value)) for label, value in options]
    if not pairs and not allow_text:
        return None
    scripted = None if keys is None else iter(list(keys))
    columns, lines = _terminal_size(size)
    unicode_mode = _text_unicode(sink)
    width = max(MIN_COLUMNS - 1, columns - 1)
    page = max(3, min(PAGE, max(MIN_LINES, lines) - CHROME_ROWS - 1))
    picker = Picker(
        [Item(value=value, title=label) for label, value in pairs],
        title=str(title or "select"),
        page=page,
        unicode=unicode_mode,
        width=width,
    )
    picker.index = max(0, min(int(default or 0), max(0, len(picker.items) - 1)))

    termios_mod: Any = None
    fd = -1
    if scripted is None:
        if not _drivable(env_map, sink, sys.stdin):
            return _plain_select(picker, sink=sink)
        termios_mod, _tty_mod = _termios()
        fd = _fd_of(sys.stdin)

    answer: str | None = None
    status = ""
    saved: Any = None
    try:
        if termios_mod is not None:
            saved = termios_mod.tcgetattr(fd)
            raw_mode(termios_mod, fd, saved)
        board = _Block(sink)
        painted: tuple[Any, ...] = ()
        while True:
            visible = picker.visible()
            state = (picker.query, picker.index, picker.top, len(visible), page, width)
            if state != painted:
                board.paint(_block(picker, width=width, page=page))
                painted = state
            key = next(scripted, None) if scripted is not None else read_key(fd)
            if key is None:
                if scripted is not None:
                    status = EXHAUSTED_STATUS
                    break
                continue
            if not key:
                continue
            if key == "enter":
                if picker.handle(key) == SELECT:
                    answer = picker.result().value
                elif allow_text and picker.query.strip():
                    # a typed id nobody listed stands for itself — never invented, never lost
                    answer = picker.query.strip()
                else:
                    answer = ""
                break
            if key == "ctrl-d":  # not a cancel `Picker` names: say the byte that did it
                picker.cancelled = "ctrl-d"
                answer = None
                break
            if key in ("esc", "ctrl-c") and picker.handle(key) == CANCEL:
                answer = None
                break
            picker.handle(key)  # move, page, filter edit, or a key with nothing to do
        status = (
            SELECTED_STATUS.format(value=answer) if answer
            else EMPTY_STATUS if answer is not None
            else CANCELLED_STATUS
        ) if not status else status
        board.paint(_block(picker, width=width, page=page, status=status))
    finally:
        if saved is not None and termios_mod is not None:
            try:
                termios_mod.tcsetattr(fd, termios_mod.TCSADRAIN, saved)
            except Exception:  # noqa: BLE001 — a terminal that cannot be restored is gone
                pass
    return answer


# --------------------------------------------------------------------------- #
# building items from real data                                                 #
# --------------------------------------------------------------------------- #


def from_dicts(
    rows: Iterable[dict[str, Any]],
    *,
    key: str,
    title_keys: Sequence[str] = ("title", "goal", "text"),
    detail_keys: Sequence[str] = (),
    meta_keys: Sequence[str] = (),
) -> list[Item]:
    """Items from a JSON list of objects (missing keys simply do not appear)."""
    out: list[Item] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = str(row.get(key) or "")
        if not value:
            continue
        title = next((str(row[name]) for name in title_keys if row.get(name)), "")
        detail = " · ".join(str(row[name]) for name in detail_keys if row.get(name))
        meta = " · ".join(str(row[name]) for name in meta_keys if row.get(name))
        out.append(Item(value=value, title=title, detail=detail, meta=meta))
    return out


def age_text(seconds: float | None) -> str:
    """A coarse, honest age (``3 s``, ``4 m``, ``2 h``, ``6 d``); nothing invents precision."""
    if seconds is None:
        return ""
    try:
        value = max(0.0, float(seconds))
    except (TypeError, ValueError):
        return ""
    if value < 60:
        return f"{int(value)} s ago"
    if value < 3600:
        return f"{int(value // 60)} m ago"
    if value < 86400:
        return f"{int(value // 3600)} h ago"
    return f"{int(value // 86400)} d ago"


__all__ = [
    "CANCEL",
    "CANCEL_CTRL_C",
    "CANCEL_EMPTY",
    "CANCEL_ESC",
    "CANCEL_EXHAUSTED",
    "Item",
    "NONE",
    "PAGE",
    "Picker",
    "Result",
    "SELECT",
    "age_text",
    "from_dicts",
    "plain_lines",
    "plain_menu",
    "read_key",
    "raw_mode",
    "select",
]
