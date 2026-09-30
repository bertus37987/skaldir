"""The two-bar chat bar: a framed input line, a slash-first surface, honest facts.

The input zone is framed by **two full-width horizontal bars** — a light rule above and
below the typing area — so the chat bar reads as its own zone, the way Cline's and
Hermes' composers do::

    ──────────────────────────────────────────────────────────────────
    ❯ how do I add tests to this project?
    ──────────────────────────────────────────────────────────────────
    model deepseek-chat · ctx n/a · ~14 tok/s · 00:07    goal: none · /help

Three rules hold this together, and each one is enforced by a test:

* **the bars are the theme's, not an escape soup.** The bar glyph is ``─`` on a
  terminal and ``-`` in plain mode (``theme.py``'s contract: plain output is ASCII and
  carries no escape sequence at all); every frame line is exactly ``width`` cells wide,
  so **the frame never wraps**, whatever the text is.
* **the frame is redrawn only when it has to be** — when the terminal resized
  (``SIGWINCH``) or when the frame's own content changed (a keystroke, new facts, a
  streamed line). A keystroke that changes nothing prints nothing.
* **plain mode is a real path, not a fallback that prints leftovers.** A pipe, a
  redirect, ``NO_COLOR`` or ``--no-color`` gets :func:`plain_prompt` (``"skaldir > "``)
  and no frame at all — zero escapes, ASCII only — which is what
  :meth:`repl.Shell.prompt` already produces for a pipe.
* **a terminal narrower than :data:`MIN_WIDTH` is never framed.** The frame does not shrink
  below 40 columns (the bars would be mostly bar), and it must not pretend it can: below
  that, ``front_for`` answers ``None`` and the shell keeps its own plain prompt. A terminal
  that narrows *under* a live frame (the width used to be clamped **up** to 40, so a
  30-column terminal got 40-cell rows and every row wrapped into two) stands the frame down:
  it is erased at the geometry it was drawn in, one honest line replaces it, and
  :class:`FrameStoodDown` hands the turn to the shell's plain path
  (:meth:`ChatBar.stand_down`).

The numbers in the hint row are the ones the owner asked for (context %, tokens/second,
elapsed) and each one names its source: a count that came out of a command's own output
is ``provider``, a computed one is labelled ``~`` (estimate), and anything that cannot be
measured prints ``n/a`` **with the reason** — never a plausible-looking ``0``.

What the frame does *not* do is decide anything: the palette comes from
:mod:`skaldir.slash`, every command runs through the real CLI, and a verb this build does
not carry is reported as ``planned`` and refused (:data:`skaldir.slash.STATE_PLANNED`).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import select
import shutil
import signal
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # the product contract (optional: this module also runs standalone)
    from . import cli_contract as _CONTRACT
except Exception:  # pragma: no cover — a build without the contract
    _CONTRACT = None

try:
    from . import env as _env
except Exception:  # pragma: no cover
    _env = None

try:
    from . import slash as _slash
except Exception:  # pragma: no cover — the palette is optional, the frame is not
    _slash = None

try:  # the Computer-Use first-use gate (owner requirement): the ChatBar renders it
    from . import cut as _cut
except Exception:  # pragma: no cover — a build without computer_use keeps the refusal
    _cut = None


def cut_first_use_decision(shell: Any, *, interactive: bool | None = None,
                           goal: str = "", target: str = "") -> dict[str, Any] | None:
    """The live-desktop first-use gate, through the ChatBar's own ask/say seam.

    ``skaldir.cut`` decides; this wrapper is the seam the chat owns (and the test
    surface). ``None`` when the gate module itself is unavailable — the refusal
    then stays what it already was, never a silent allow.
    """
    if _cut is None:
        return None
    return _cut.first_use_gate(
        shell, goal=goal, target=target, interactive=interactive
    )


try:  # POSIX only: a terminal is what this module needs, and Windows has none of this
    import termios
    import tty
except Exception:  # pragma: no cover
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]


__all__ = [
    "BAR_PLAIN",
    "BAR_RICH",
    "BAR_TOKEN",
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "FRAME_LINES",
    "HINT_HELP",
    "MARGIN",
    "MIN_WIDTH",
    "PALETTE_ROWS",
    "PLAIN_PROMPT",
    "SCREEN_LINES",
    "VALUE_ROWS",
    "Action",
    "ChatBar",
    "Fact",
    "Frame",
    "FrameStoodDown",
    "FrameStream",
    "Front",
    "Turn",
    "ValueRow",
    "add_chat_parser",
    "bar_line",
    "contract_entries",
    "fact_unknown",
    "format_verbs",
    "frame_lines",
    "front_for",
    "front_of",
    "handle_clear",
    "hint_line",
    "input_line",
    "install_contract_entries",
    "looks_like_prose",
    "main",
    "mapping_rows",
    "palette_rows",
    "plain_prompt",
    "probe_goal",
    "probe_model",
    "read_key",
    "read_plain",
    "register_cli",
    "root_names",
    "route_plain",
    "truncate_plain",
    "unicode_ok",
    "value_rows",
    "verb_report",
]

#: The lines a **screen-mode** frame owns before any palette row or logo row: the band, the
#: top bar, the input line, the bottom bar and the hint row. ``FRAME_LINES`` (4) stays the
#: core frame's own height, so every caller that does not ask for a screen is unchanged.
SCREEN_LINES = 5

#: The verb the shell routes to :meth:`ChatBar.clear_screen` — the screen wipe and the logo
#: coming back. Deliberately *not* added to ``slash.SLASH_COMMANDS``: that table belongs to
#: the palette block, and this hook has to work before the palette is consulted.
CLEAR_VERBS = ("/clear", "/cls")

#: The clock ticker only repaints after this much transcript silence (it must not
#: erase a line that is being written).
TICKER_QUIET_S = 1.0
BADGE_ROWS = 3

# --------------------------------------------------------------------------- #
# the look: glyphs, margins, widths
# --------------------------------------------------------------------------- #

#: The bar itself. ``─`` on a terminal, ``-`` in plain mode (ASCII only, no escapes).
BAR_RICH = "─"
BAR_PLAIN = "-"
#: The input mark — the same shape ``repl`` uses for its prompt.
MARK_RICH = "❯"
MARK_PLAIN = ">"
#: What a pipe gets instead of a frame: the plain prompt.
PLAIN_PROMPT = "skaldir > "
#: The frame's left inset, so the bars read as a frame and not as a page rule.
MARGIN = 2
#: The lines the frame itself draws (bar, input, bar, hint) — palette rows are extra.
FRAME_LINES = 4
#: How many palette rows the frame draws above the bars before it names the rest. A frame
#: of 4 lines plus 12 rows still fits an 80x24 terminal, and the verbs are ordered by how
#: often a session reaches for them (see ``slash.SLASH_COMMANDS``).
PALETTE_ROWS = 12
#: The fewest cells a *clipped* band goal may have: ``goal: `` plus the ellipsis and one
#: character of the label. Below this the fact would be the word ``goal`` and nothing else —
#: a shorter band is honest, a band that says "goal:" and no goal is not (see
#: :meth:`ChatBar.band_text`).
MIN_BAND_GOAL_CELLS = 8
#: How many rows a **value-slot menu** draws before it names the rest. The palette's 12 rows
#: are for verbs (a list a session scans); a value slot is a lookup — session ids, checkpoint
#: ids, model ids, ``@paths`` — and 8 rows plus an honest ``… n more`` keeps the frame inside
#: a 24-line terminal even when the slot carries 40 candidates (measured: ``/exec `` → 40).
VALUE_ROWS = 8
#: A terminal narrower than this is not framed; the frame would be mostly bar. Never a
#: clamp: :func:`term_width` reports what the terminal really has, so this is a *decision*
#: only — the caller stands the frame down instead of painting 40 cells into 30 columns.
MIN_WIDTH = 40
DEFAULT_WIDTH = 80
DEFAULT_HEIGHT = 24
#: The one keystroke that pays for itself, on the right of the hint row.
HINT_HELP = "/help"
#: The theme token the two bars are painted with — one place, so the frame's look is one
#: edit. ``accent`` is the paper/bright grey with weight, which is what makes the bars read
#: as *the* frame on a monochrome terminal.
BAR_TOKEN = "accent"
#: How often the frame may repaint while output streams. The *text* is never delayed (it
#: is written immediately); only the frame's repaint is throttled, so a fast stream cannot
#: turn one line of answer into five lines of screen traffic.
MIN_REDRAW_S = 0.033
#: A speed needs a window this long before it is a measurement rather than a division.
MIN_WINDOW_S = 0.05
#: How long a fact survives before the frame probes for it again. The hint row is redrawn
#: per keystroke and per chunk; reading the provider config on each one would be a file
#: read per frame, and a two-second-old model name is not a lie — it is a cached reading.
PROBE_TTL_S = 2.0
_ELLIPSIS_RICH = "…"
_ELLIPSIS_PLAIN = "..."
#: The dot between facts (``·``; plain mode gets ``|`` inside a sentence, ``-`` in a join).
DOT_RICH = "·"
DOT_PLAIN = "-"

#: The palette is cached for the life of the bar: its marks come from the one selftest
#: probe the session already ran, so re-deriving 20 rows per keystroke buys nothing.
_PALETTE_CACHE: dict[int, list[Any]] = {}


def unicode_ok(stream: Any) -> bool:
    """Can this stream carry ``─``/``❯``? (a latin-1 log cannot)."""
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        "─❯".encode(encoding, errors="strict")
    except Exception:  # noqa: BLE001 — unknown encoding: assume the safe ASCII set
        return False
    return True


def plain_prompt() -> str:
    """The prompt a pipe, a redirect or a log gets — no frame, no escapes, ASCII."""
    return PLAIN_PROMPT


def term_width(fd: int | None = None, default: int = DEFAULT_WIDTH) -> int:
    """The real terminal width, from the fd when there is one. Clamped *down*, never up.

    It used to return ``max(MIN_WIDTH, columns)``, which made ``front_for``'s
    ``width < MIN_WIDTH`` check dead code: a 30-column terminal reported 40, so the frame was
    painted 40 cells wide and every bar wrapped onto a second row (measured in a real 30x24
    pty: one bar became ``────…────`` + ``──────``, the band split mid-word, the hint row
    ended in ``dismiss…``). A size below :data:`MIN_WIDTH` is a real answer, and the caller
    decides what to do with it.
    """
    if fd is not None:
        try:
            return max(1, int(os.get_terminal_size(fd).columns))
        except Exception:  # noqa: BLE001 — not a terminal / no size: fall through
            pass
    try:
        return max(1, int(shutil.get_terminal_size().columns))
    except Exception:  # noqa: BLE001
        return default


def term_height(fd: int | None = None, default: int = DEFAULT_HEIGHT) -> int:
    """The real terminal height (never below one frame)."""
    if fd is not None:
        try:
            return max(FRAME_LINES, int(os.get_terminal_size(fd).lines))
        except Exception:  # noqa: BLE001
            pass
    try:
        return max(FRAME_LINES, int(shutil.get_terminal_size().lines))
    except Exception:  # noqa: BLE001
        return default


# --------------------------------------------------------------------------- #
# layout: pure functions, so the frame can be asserted without a terminal
# --------------------------------------------------------------------------- #


def bar_line(width: int, *, unicode: bool = True, margin: int = MARGIN) -> str:
    """One full-width bar: exactly ``width`` cells, never one more."""
    glyph = BAR_RICH if unicode else BAR_PLAIN
    body = max(0, int(width) - max(0, margin))
    return " " * max(0, margin) + glyph * body


def truncate_plain(text: str, limit: int, *, unicode: bool = True) -> str:
    """Clip one line to ``limit`` cells (``...`` in plain mode, ``…`` on a terminal)."""
    text = " ".join(str(text or "").replace("\n", " ").split())
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    ellipsis = _ELLIPSIS_RICH if unicode else _ELLIPSIS_PLAIN
    if limit <= len(ellipsis):
        return text[:limit]
    return text[: limit - len(ellipsis)].rstrip() + ellipsis


def input_line(width: int, text: str = "", *, unicode: bool = True,
               margin: int = MARGIN) -> str:
    """``  ❯ typed text`` — clipped and padded to the width, so the frame never wraps.

    Padding is part of the contract, not decoration: every frame line is exactly ``width``
    cells, which is what lets the erase/redraw arithmetic be a line count.
    """
    mark = MARK_RICH if unicode else MARK_PLAIN
    head = " " * max(0, margin) + mark + " "
    room = max(0, int(width) - len(head))
    return (head + truncate_plain(text, room, unicode=unicode)).ljust(max(0, int(width)))


def hint_line(width: int, left: str = "", right: str = "", *, margin: int = MARGIN,
              unicode: bool = True) -> str:
    """The row under the bars: facts on the left, the goal and ``/help`` on the right.

    Exactly ``width`` cells. The right-hand side (what the owner asked for: the durable
    goal and one hint) is kept in full; the left-hand facts give way first, because a
    dropped fact is cheaper than a lie about the goal.
    """
    right_part = str(right or "")
    room = max(0, int(width) - max(0, margin) - len(right_part) - 2)
    left_part = str(left or "")
    if not unicode:
        left_part, right_part = _fold_ascii(left_part), _fold_ascii(right_part)
        room = max(0, int(width) - max(0, margin) - len(right_part) - 2)
    if len(left_part) > room:
        left_part = truncate_plain(left_part, room, unicode=unicode) if room else ""
    gap = max(1, int(width) - max(0, margin) - len(left_part) - len(right_part))
    row = " " * max(0, margin) + left_part + " " * gap + right_part
    return row[: int(width)].ljust(max(0, int(width)))


def _fold_ascii(text: Any) -> str:
    """Plain mode is ASCII-only (``theme.py``'s contract) — the frame's own text too."""
    value = str(text or "")
    if _slash is not None:
        try:
            return _slash.ascii_safe(value)
        except Exception:  # noqa: BLE001 — an older slash module: fall through
            pass
    return value.encode("ascii", errors="replace").decode("ascii")


def _visible(text: str) -> str:
    """The text with SGR/CSI sequences removed: what the terminal actually shows."""
    out: list[str] = []
    index = 0
    size = len(text)
    while index < size:
        char = text[index]
        if char == "\x1b" and index + 1 < size:
            nxt = text[index + 1]
            if nxt == "[":  # CSI: parameters, then one final byte in @-~
                index += 2
                while index < size and not ("@" <= text[index] <= "~"):
                    index += 1
                index += 1
                continue
            if nxt == "]":  # OSC: until BEL or ST
                index += 2
                while index < size and text[index] not in ("\x07", "\x1b"):
                    index += 1
                index += 2 if index + 1 < size and text[index] == "\x1b" else 1
                continue
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _visible_rows(text: str, width: int) -> int:
    """How many terminal rows ``text`` takes at ``width`` cells (the terminal re-wraps).

    The erase is issued from the frame's own parking spot, so an unfinished line above the
    frame has to be *measured* in rows: a 200-cell line is three rows at 80 columns, and
    counting it as one left its tail on screen while the next chunk rewrote it. Wide East
    Asian glyphs are counted as one cell each — the frame's own lines are built the same way
    (``truncate_plain`` counts characters), so the two agree.
    """
    body = _visible(str(text or ""))
    if not body:
        return 0
    cells = max(1, int(width))
    return max(1, -(-len(body) // cells))


def _name_of(entry: Any) -> str:
    command = getattr(entry, "command", None)
    if command is not None:
        return str(getattr(command, "slash", "") or getattr(command, "name", ""))
    return str(getattr(entry, "slash", entry))


def _purpose_of(entry: Any) -> str:
    command = getattr(entry, "command", None)
    if command is not None:
        return str(getattr(command, "title", "") or "")
    return str(getattr(entry, "description", "") or "")


def _mark_of(entry: Any) -> str:
    mark = getattr(entry, "mark", None)
    if callable(mark):
        try:
            return str(mark())
        except Exception:  # noqa: BLE001 — a row that cannot describe itself says so
            return "?"
    return str(getattr(entry, "state", "") or getattr(entry, "kind", "") or "")


def palette_rows(entries: Sequence[Any], width: int, *, unicode: bool = True,
                 selected: int = -1, max_rows: int = PALETTE_ROWS, hidden: int = 0,
                 margin: int = MARGIN, name_width: int | None = None) -> list[str]:
    """The palette above the bars: one aligned row per verb, an honest state at the end.

    The rows come from :mod:`skaldir.slash` — this function only draws them, so the
    palette and the frame can never disagree about what a verb maps onto. ``hidden`` is the
    number of matches the page does not show: it is passed in (rather than guessed from the
    list) so "… n more" is the real remainder.

    ``name_width`` overrides the verb column; the **value** menu passes its own, because a
    session or checkpoint id is longer than a verb and glued text is not a row. Left out, the
    column is sized from the verbs actually on this page — measured at 80x24: a fixed 14-wide
    cap drew ``/memory-retrieval-check`` *onto* its purpose (``memory-searchsearch the memory
    store``, no gap at all). The state column is the invariant, not the cap; ``width`` still
    bounds the column so a row can never wrap the frame.
    """
    rows = list(entries)
    shown = rows if max_rows <= 0 else rows[:max_rows]
    out: list[str] = []
    if name_width is None:
        longest = max((len(_name_of(item)) for item in shown), default=8)
        widest_mark = max((len(_mark_of(item)) for item in shown), default=0)
        column = max(8, min(longest + 2, max(8, int(width) - widest_mark - 24)))
    else:
        column = max(4, int(name_width))
    pointer_glyph = MARK_RICH if unicode else MARK_PLAIN
    for index, entry in enumerate(shown):
        name = _name_of(entry)
        mark = _mark_of(entry)
        purpose = _purpose_of(entry)
        if not unicode:
            name, mark, purpose = _fold_ascii(name), _fold_ascii(mark), _fold_ascii(purpose)
        pointer = pointer_glyph if index == selected else " "
        head = " " * max(0, margin) + pointer + " " + f"{name:<{column}}"
        room = max(0, int(width) - len(head) - len(mark) - 1)
        clipped = truncate_plain(purpose, room, unicode=unicode)
        out.append(f"{head}{clipped:<{room}} {mark}"[: int(width)].ljust(max(0, int(width))))
    hidden = max(0, int(hidden) + (len(rows) - len(shown) if max_rows > 0 else 0))
    if hidden > 0:
        more = f"... {hidden} more" if not unicode else f"… {hidden} more"
        out.append((" " * max(0, margin) + more)[: int(width)])
    return out


@dataclass(frozen=True)
class ValueRow:
    """One candidate of a value slot, in the palette's own vocabulary.

    The frame draws menu rows with :func:`palette_rows`, which reads ``slash`` /
    ``description`` / ``state`` off whatever it is handed. A completion candidate is not a
    slash verb, so it is adapted here — one place, so the menu and the palette cannot drift
    into two row formats (``/resume <Tab>`` and ``/re<Tab>`` are one surface).
    """

    slash: str
    description: str = ""
    state: str = ""


def value_rows(candidates: Sequence[Any], width: int, *, unicode: bool = True,
               max_rows: int = VALUE_ROWS, margin: int = MARGIN) -> list[str]:
    """The **value slot** menu: what the completion engine offered after a verb, drawn.

    Measured defect (a real 80x24 pty, a session store with two ids): ``/history `` + Tab
    inserted nothing and drew nothing. The engine had 14 candidates — two session ids and the
    verb's flags — their longest common prefix was empty, so ``insert`` was empty, and the
    frame had no path for "the engine answered with a list". The user saw a keystroke that
    did nothing at all. The same happened for ``/export `` (13), ``/restore `` (13) and
    ``/new `` (13), while a flag-only verb (``/retry ``, ``/undo ``) *did* show its ``--``,
    because there the common prefix is non-empty.

    Rows are the palette's rows (name + clipped purpose + state mark), bounded to
    ``max_rows`` with the real remainder named — a 40-candidate slot (``/exec ``) must not
    push the two bars off a 24-line terminal.
    """
    adapted = [
        ValueRow(
            slash=str(getattr(item, "text", item) or ""),
            description=str(getattr(item, "description", "") or ""),
            state=_candidate_state(item),
        )
        for item in candidates
    ]
    column = min(30, max(10, max((len(row.slash) for row in adapted), default=10) + 2))
    return palette_rows(adapted, width, unicode=unicode, selected=-1, max_rows=max_rows,
                        margin=margin, name_width=column)


def _candidate_state(item: Any) -> str:
    """The right-hand mark of a candidate: what kind of thing it is, in one word.

    The engine's own vocabulary (``complete.Suggestion.kind``), collapsed to the two words a
    value slot actually needs: ``flag`` for an option (``--json``), ``value`` for something
    that fills the slot (a declared choice, an id, a path).
    """
    kind = str(getattr(item, "kind", "") or "")
    if kind == "option":
        return "flag"
    if kind in ("choice", "argument"):
        return "value"
    if not bool(getattr(item, "available", True)):
        return "unwired"
    return kind


def frame_lines(width: int, *, text: str = "", rows: Sequence[str] = (),
                hint_left: str = "", hint_right: str = "", unicode: bool = True,
                margin: int = MARGIN) -> tuple[list[str], int]:
    """The frame as lines, plus the index of the input line inside them.

    ``rows`` are the palette rows (already drawn by :func:`palette_rows`); they sit
    **above** the top bar, so the typing area never moves while the palette opens and
    closes. Every returned line is exactly ``width`` cells: the caller can put them on a
    terminal without checking anything. With ``unicode=False`` the text is folded to ASCII
    as well as the glyphs, so a plain frame is plain all the way down.
    """
    lines: list[str] = [str(row)[: int(width)].ljust(max(0, int(width))) for row in rows]
    cursor_index = len(lines) + 1
    if not unicode:
        text = _fold_ascii(text)
    lines.append(bar_line(width, unicode=unicode, margin=margin))
    lines.append(input_line(width, text, unicode=unicode, margin=margin))
    lines.append(bar_line(width, unicode=unicode, margin=margin))
    lines.append(hint_line(width, hint_left, hint_right, margin=margin, unicode=unicode))
    return lines, cursor_index


# --------------------------------------------------------------------------- #
# the facts: every number with its source, or an honest n/a
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Fact:
    """One bottom-bar fact: what to show, who measured it, and why it may be missing.

    ``label`` is what an unknown reading renders as (``ctx n/a``), so a bare ``n/a`` never
    appears in the hint row without saying which number is missing.
    """

    text: str = ""
    source: str = ""
    reason: str = ""
    label: str = ""

    @property
    def known(self) -> bool:
        return bool(self.text) and not self.reason

    def render(self) -> str:
        if self.known:
            return self.text
        return f"{self.label} n/a".strip()


def fact_unknown(reason: str, *, label: str = "") -> Fact:
    """A fact nobody could measure — it renders ``n/a`` and keeps the reason."""
    return Fact("", "", reason, label)


#: A token count in a command's own output (the loop and ``usage`` blocks speak these).
_TOKENS_RE = re.compile(
    r"\b(?:total_tokens|completion_tokens|prompt_tokens|tokens?)\s*[=:]\s*(\d{1,9})\b",
    re.IGNORECASE,
)
#: A context reading in a command's own output (``ctx 12%``, ``context: 12 %``).
_CTX_RE = re.compile(r"\b(?:ctx|context)\b[^0-9%\n]{0,24}?(\d{1,3}(?:\.\d)?)\s*%", re.IGNORECASE)
#: A usage block's prompt/total token count (``prompt_tokens: 8123``).
_USAGE_CTX_RE = re.compile(
    r"\b(?:prompt_tokens|prompt_tokens_count|context_tokens)\b[\"'\s:=]+(\d{1,9})",
    re.IGNORECASE,
)


@dataclass
class Turn:
    """The turn in flight: the clock, and the numbers that can really be measured.

    ``chars`` is a byte count of the answer as it arrives; ``tokens``/``ctx_percent`` are
    only ever set when the command's own output carried them (that is the ``provider``
    source). A speed computed from ``chars`` is an estimate and is rendered with ``~``.
    """

    started: float = field(default_factory=time.monotonic)
    text: str = ""
    chars: int = 0
    chunks: int = 0
    tokens: int | None = None
    ctx_percent: float | None = None
    #: context *size* when the provider reported usage (prompt_tokens) but no percent
    ctx_tokens: int | None = None
    first_at: float | None = None
    last_at: float | None = None
    #: set by ``end_turn``: the measured duration freezes here. While ``None`` the
    #: clock is live (``time.monotonic() - started``) so a silent tool cannot freeze it.
    finished_at: float | None = None

    @property
    def elapsed(self) -> float:
        """Seconds since the turn started — live while running, frozen when finished."""
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(0.0, end - self.started)

    @property
    def window(self) -> float:
        """The seconds the counted work really took (0 when nothing has arrived)."""
        if self.first_at is None or self.last_at is None:
            return 0.0
        return max(0.0, self.last_at - self.first_at)

    def observe(self, text: str, *, now: float | None = None) -> None:
        """Fold one streamed chunk in: byte count, and any number the chunk reports."""
        chunk = str(text or "")
        if not chunk:
            return
        stamp = time.monotonic() if now is None else float(now)
        if self.first_at is None:
            self.first_at = stamp
        self.last_at = stamp
        self.chunks += 1
        self.chars += len(chunk)
        self.text += chunk
        if len(self.text) > 200_000:  # never hold a whole run for a hint row
            self.text = self.text[-200_000:]
        found = _TOKENS_RE.findall(self.text)
        if found:
            self.tokens = max(int(item) for item in found)
        reading = _CTX_RE.search(self.text)
        if reading:
            try:
                self.ctx_percent = float(reading.group(1))
            except ValueError:  # pragma: no cover — the regex matched digits
                self.ctx_percent = None
        usage = _USAGE_CTX_RE.search(self.text)
        if usage and self.ctx_tokens is None:
            try:
                self.ctx_tokens = int(usage.group(1))
            except ValueError:  # pragma: no cover
                self.ctx_tokens = None

    def note_usage(
        self, *, prompt_tokens: int | None = None, total_tokens: int | None = None
    ) -> None:
        """Fold a provider ``usage`` block into the band (ctx size when no % is known)."""
        if prompt_tokens:
            self.ctx_tokens = max(int(self.ctx_tokens or 0), int(prompt_tokens))
        if total_tokens:
            self.tokens = max(int(self.tokens or 0), int(total_tokens))

    def speed(self) -> Fact:
        """Tokens/second over the window the counted work really took."""
        window = self.window
        if not self.chars:
            return fact_unknown("nothing has streamed yet", label="tok/s")
        if window < MIN_WINDOW_S:
            return fact_unknown("the answered window is too short to measure", label="tok/s")
        if self.tokens:
            return Fact(f"{self.tokens / window:.0f} tok/s", "provider", "")
        return Fact(f"~{(self.chars / 4) / window:.0f} tok/s", "estimate", "")

    def context(self) -> Fact:
        """The context reading the run itself reported (never a local guess)."""
        if self.ctx_percent is not None:
            return Fact(f"ctx {self.ctx_percent:.0f}%", "provider", "")
        if self.ctx_tokens:
            from .telemetry import format_tokens_short

            return Fact(
                f"ctx ~{format_tokens_short(self.ctx_tokens)} tok", "provider", ""
            )
        return fact_unknown("the run reported no context reading", label="ctx")

    def clock(self) -> str:
        """``MM:SS`` since the turn started (the one number that is always measured)."""
        seconds = int(self.elapsed)
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def cost_tokens(self) -> tuple[int | None, bool]:
        """``(tokens, estimated)`` — the run's own count when it reported one, else ``chars/4``.

        ``chars/4`` is a *documented* estimate (the same one ``stream_render`` uses), so the
        caller can mark the cost with ``~``; :func:`skaldir.chat_screen.cost_text` never marks
        a provider-reported count.
        """
        if self.tokens:
            return int(self.tokens), False
        if self.chars:
            return max(1, int(self.chars) // 4), True
        return None, False


def probe_model() -> Fact:
    """The model this runtime would use, from the same two sources the loop reads.

    Precedence mirrors the provider layer: ``SKALDIR_LLM_MODEL`` (the environment the run
    actually uses) first, then the configured store's default provider. ``provider
    default``/``provider tiers`` are the verbs that *set* the stored one; this probe only
    reads, so the hint row and the shell's own status line cannot disagree.
    """
    from_env = ""
    if _env is not None:
        from_env = str(_env.first_env(("SKALDIR_LLM_MODEL", "OPENAI_MODEL")) or "")
        if from_env:
            return Fact(from_env, f"env {_env.source('LLM_MODEL') or 'SKALDIR_LLM_MODEL'}", "")
    try:
        from . import provider_config as pc
    except Exception as exc:  # noqa: BLE001
        return fact_unknown(f"provider layer unavailable ({type(exc).__name__})")
    try:
        report = pc.store_report(path=str(pc.config_path()))
    except Exception as exc:  # noqa: BLE001 — a refused/secret/corrupt config
        return fact_unknown(f"provider config unreadable ({type(exc).__name__})")
    active = report.get("active") or {}
    model = str(active.get("model") or "")
    default = str(report.get("default") or "")
    if model:
        return Fact(model, "provider_config", "")
    if not default:
        return fact_unknown("no provider is configured")
    return fact_unknown(f"{default} declares no model")


def probe_goal(shell: Any) -> Fact:
    """The durable goal, from the shell's own probe (never re-derived here)."""
    try:
        value = shell.session.probes.goal()
    except Exception as exc:  # noqa: BLE001
        return fact_unknown(f"goal probe unavailable ({type(exc).__name__})")
    label = str(getattr(value, "label", "") or "")
    if not label or label in {"none", "unknown"}:
        return fact_unknown(str(getattr(value, "detail", "") or "no goal is recorded"))
    return Fact(label, str(getattr(value, "source", "") or "runs_store"), "")


# --------------------------------------------------------------------------- #
# the frame on screen: draw, erase, redraw when it has to
# --------------------------------------------------------------------------- #


@dataclass
class Frame:
    """What is on the screen right now, and how to take it back without guessing.

    ``drawn`` counts the draws (0 = nothing of ours is on screen, so an erase would eat
    somebody else's line). ``cursor_index`` is the input line's position inside the frame,
    which is all the arithmetic a redraw needs — the cursor is always in the input line.
    """

    width: int = DEFAULT_WIDTH
    unicode: bool = True
    lines: list[str] = field(default_factory=list)
    cursor_index: int = 1
    drawn: int = 0
    #: a streamed line is open (its last chunk carried no newline yet)
    partial: bool = False
    #: is the frame on the screen *right now*, with the cursor parked in the input line?
    #: every erase is relative to that parking spot, so a frame that was already taken
    #: back (or pushed aside by an output write) must not be erased again — that is how a
    #: second erase eats the text it was supposed to sit under.
    on_screen: bool = False
    #: the position moved without the content changing (a repaint is still owed)
    moved: bool = False
    #: rows the unfinished streamed line *above* the frame occupies. A line longer than the
    #: terminal is re-wrapped onto more rows, and the erase is measured in rows — counting
    #: one row per line left the wrapped tail of the open line on screen, so the next chunk
    #: rewrote the line while its old copy stayed visible (measured in the pty capture:
    #: "sibling work is running…" printed three times).
    open_rows: int = 0
    last_draw_at: float = 0.0
    #: when the frame's content was last recomposed (the facts probe is not free)
    last_compose_at: float = 0.0

    def erase(self) -> str:
        """The sequence that removes the frame (and the open streamed line above it).

        The count is in **visual** rows, not logical lines: a frame row that is wider than
        the terminal is re-wrapped onto a second row, and counting one row per line made
        the erase start *inside* the frame — so the text written above it lost its head
        (measured on a real pty: five consecutive ``write_above()`` calls kept only the
        last lines when every frame row wrapped).
        """
        if self.drawn <= 0 or not self.on_screen:
            return ""
        up = self.rows_above(self.width) + max(0, self.open_rows)
        return f"\x1b[{max(1, up)}F\x1b[J"

    def rows_above(self, width: int | None = None) -> int:
        """How many screen rows sit between the top of the frame and the cursor.

        The cursor is parked in the input line, so the answer is the visual height of the
        frame's own lines above it (at most the bars, the band, the live row and the
        palette) — each one measured at ``width``, because the terminal wraps what does
        not fit. For a frame whose lines are exactly ``width`` cells this is the same
        number as ``cursor_index``; it differs only when a row really wraps.
        """
        cells = max(1, int(width or self.width))
        head = self.lines[: max(0, self.cursor_index)] or [""]
        return sum(max(1, -(-len(line) // cells)) for line in head)

    def repaint_due(self, *, now: float | None = None, content_changed: bool = False) -> bool:
        """Would a repaint at this moment be new information? (the content always wins)"""
        if content_changed:
            return True
        stamp = time.monotonic() if now is None else float(now)
        return stamp - self.last_draw_at >= MIN_REDRAW_S

    def take(self, lines: Sequence[str], cursor_index: int) -> bool:
        """Record a draw; ``True`` when the frame's *content* changed."""
        new = list(lines)
        changed = new != self.lines or int(cursor_index) != self.cursor_index
        self.lines = new
        self.cursor_index = int(cursor_index)
        self.drawn += 1
        self.moved = False
        self.last_draw_at = time.monotonic()
        return changed

    def visual_rows(self, width: int | None = None) -> int:
        """How many terminal rows the drawn frame occupies at ``width``.

        A terminal re-wraps its screen when it is resized, so the erase after a resize has
        to count *visual* rows, not logical lines: a line 100 cells wide is two rows at 60
        columns. Counting the wrong number leaves the frame's tail behind.
        """
        cells = max(1, int(width or self.width))
        return sum(max(1, -(-len(line) // cells)) for line in self.lines) or 1


# --------------------------------------------------------------------------- #
# writing above the frame (this is what keeps the frame intact while a reply streams)
# --------------------------------------------------------------------------- #


class FrameStream(io.TextIOBase):
    """A stdout/stderr stand-in that keeps the frame the last thing on the screen.

    Every write goes *above* the frame: the frame is taken back, the text is written, the
    frame is drawn again underneath (with its hint row updated). A chunk that does not end
    in a newline leaves the line open, and the next chunk rewrites that line instead of
    starting a new one — so a streamed answer reads exactly as it would on a plain
    terminal.
    """

    def __init__(self, primary: Any, bar: ChatBar) -> None:
        self.primary = primary
        self.bar = bar
        self.lines_written = 0
        self.bytes_written = 0

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return str(getattr(self.primary, "encoding", "") or "utf-8")

    def isatty(self) -> bool:
        try:
            return bool(self.primary.isatty())
        except Exception:  # noqa: BLE001
            return False

    def fileno(self) -> int:
        return int(self.primary.fileno())

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:  # pragma: no cover — some libraries probe for it
        return False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.primary, name)

    def write(self, text: str) -> int:  # type: ignore[override]
        payload = "" if text is None else str(text)
        if not payload:
            return 0
        self.bytes_written += len(payload)
        self.lines_written += payload.count("\n")
        if not self.bar.active:
            self.primary.write(payload)
            return len(payload)
        self.bar.stream_chunk(payload)
        return len(payload)

    def flush(self) -> None:
        # A flush is not a line break. ``close_line()`` writes a newline and repaints the
        # frame below it; calling that from every flush (a streamer flushes after each
        # chunk) un-parked the frame mid-stream — the following chunks then wrote at the
        # old cursor position and the frame walked down the screen, leaving a stack of
        # copies behind it (measured in the pty capture). A flush only flushes.
        try:
            self.primary.flush()
        except Exception:  # noqa: BLE001 — a closed terminal must not crash a command
            pass


# --------------------------------------------------------------------------- #
# the bar
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Action:
    """What a key did. The shell only cares about ``submit``, ``eof`` and ``interrupt``."""

    kind: str = "none"
    text: str = ""


class FrameStoodDown(Exception):
    """The frame left the screen mid-read (the terminal narrowed below ``MIN_WIDTH``).

    Raised out of :meth:`ChatBar.read_line` so the caller can hand the turn to the shell's
    own plain prompt instead of returning ``None`` — which the shell reads as EOF, and an EOF
    is not what a resize means.
    """


class ChatBar:
    """The framed input zone: the palette, the keys, the facts and the drawing.

    The bar owns no product logic. It draws what it is handed (palette rows, the
    ``model``/``goal`` facts, a :class:`Turn`), turns keystrokes into one of four answers
    (keep typing, submit this line, leave, interrupt), and writes everything else above
    itself through :class:`FrameStream`.
    """

    def __init__(
        self,
        out: Any = None,
        *,
        fd: int | None = None,
        width: int | None = None,
        height: int | None = None,
        unicode: bool | None = None,
        paint: Callable[[str, str], str] | None = None,
        palette: Callable[[], Sequence[Any]] | None = None,
        model: Callable[[], Fact] | None = None,
        goal: Callable[[], Fact] | None = None,
        history: Callable[[], Sequence[str]] | None = None,
        rows: int = PALETTE_ROWS,
        screen: bool = False,
        logo: Callable[[], Sequence[str]] | None = None,
        price: Callable[[str], str] | None = None,
        #: CHAT-3: the completion engine as a callable ``text -> completed text`` (``"``
        #: when it adds nothing). ``None`` keeps the bar's own verb-only Tab, so every
        #: caller that does not pass one is byte-identical.
        complete_line: Callable[[str], str] | None = None,
        #: the **value-slot menu** (``text -> candidates``): what the engine offered when
        #: ``complete_line`` had nothing to insert. Wired to the same engine, never consulted
        #: on a path that already inserted something.
        complete_menu: Callable[[str], Sequence[Any]] | None = None,
        #: an attached run's progress feed (an object with ``pending() -> list[str]``). The bar
        #: polls it between keystrokes and writes its lines above the frame — one thread writes
        #: the terminal, and it is this one. ``None`` = no feed, byte-identical to before.
        feed: Any = None,
    ) -> None:
        self.out = out if out is not None else sys.stdout
        self.fd = fd
        self.width = int(width or term_width(fd))
        self.height = int(height or term_height(fd))
        self.unicode = unicode_ok(self.out) if unicode is None else bool(unicode)
        self.paint = paint or (lambda text, _role="": text)
        #: does the painter really draw? (probed once: plain mode must not be guessed from
        #: the stream — a forced-colour run and a NO_COLOR run both end up here)
        try:
            self.plain = self.paint("x", BAR_TOKEN) == "x"
        except Exception:  # noqa: BLE001 — a painter that raises is a no-op painter
            self.plain = True
        self._palette = palette
        self._model = model if model is not None else probe_model
        self._goal = goal if goal is not None else probe_goal
        self._history = history
        #: CHAT-3: the completion engine (``None`` = the bar's own verb-only Tab)
        self._complete_line = complete_line
        #: the value-slot menu's engine hook (``None`` = no menu is drawn, the old behaviour)
        self._complete_menu = complete_menu
        #: **screen mode** (additive, default off): the frame carries the status band above
        #: the top bar and the live state row under it, and the hint row carries the
        #: discoverability line instead of the facts. Off is the layout every existing caller
        #: already gets, byte for byte.
        self.screen = bool(screen)
        self._logo = logo
        self._price = price
        #: ``(state, detail, tick)`` — what the live row inside the frame says right now.
        self.activity: tuple[str, str, int] = ("idle", "", 0)
        self._logo_painted = 0
        #: The most recent numbers a run reported **about itself** (:class:`chat_screen.Reading`),
        #: taken from the telemetry footer that streamed past. The band shows the live turn's
        #: own reading while one is in flight and this one the rest of the time — and shows
        #: ``n/a`` when neither exists, because a number nobody printed is not a number.
        self._reading: Any = None
        self._stream_tail = ""
        #: The elapsed time of the turn that just finished (``MM:SS``), so the band keeps the
        #: duration after the turn instead of dropping back to a zero nobody measured.
        self._last_clock = "00:00"
        #: a turn has run at least once (cold start vs. a finished turn)
        self._saw_turn = False
        self._last_write_at = 0.0
        #: one writer at a time: the clock ticker must not erase a line mid-write
        self._paint_lock = threading.RLock()
        self._clock_thread: threading.Thread | None = None
        self.max_rows = max(0, int(rows))
        self.frame = Frame(width=self.width, unicode=self.unicode)
        self.text = ""
        self.cursor = 0
        self.selected = 0
        self.open = False
        self.entries: list[Any] = []
        #: the **value slot**'s candidates, with the line they were computed for. The line is
        #: part of the state on purpose: any keystroke that changes ``self.text`` makes the
        #: menu stale, and a stale menu is worse than no menu (it would offer ids for a verb
        #: the user has since edited away).
        self.menu: list[Any] = []
        self._menu_text: str | None = None
        self.turn: Turn | None = None
        self.notes: list[str] = []
        self.applied: list[str] = []
        self._palette_rows: list[Any] | None = None
        self.matches = 0
        #: the attached run's progress feed (``None`` = nothing to poll)
        self.feed: Any = feed
        #: the live **picker** (``skaldir.picker.Picker``): while it is set, its rows are the
        #: frame's rows and its keys win. One widget, used by /sessions, /resume and the rest.
        self.picker: Any = None
        self._probe_cache: dict[int, tuple[float, Fact]] = {}
        self._resized = False
        self._open_line = ""
        self._history_at: int | None = None
        self._history_draft = ""
        self._previous_winch: Any = None
        #: the frame left the screen because the terminal went below :data:`MIN_WIDTH`; from
        #: then on it paints nothing and the shell's plain prompt owns the session.
        self._stood_down = False

    # -- state ------------------------------------------------------------- #
    @property
    def active(self) -> bool:
        """Is the frame driving a terminal right now?"""
        if self._stood_down:
            return False
        return self.fd is not None and termios is not None and tty is not None

    @property
    def stood_down(self) -> bool:
        """Did the frame leave the screen (a terminal narrower than :data:`MIN_WIDTH`)?"""
        return bool(self._stood_down)

    def _owns_terminal(self) -> bool:
        """Does this bar write to the very fd it would measure?

        A ``StringIO`` (a test), a wrapper or a caller that pinned a width has no terminal
        to re-read, and then ``self.width`` stays the authority — which is what keeps the
        frame deterministic for those callers.
        """
        if self.fd is None:
            return False
        try:
            return int(self.out.fileno()) == int(self.fd)
        except Exception:  # noqa: BLE001 — no fileno: not the terminal this fd names
            return False

    def terminal_width(self) -> int:
        """The width the terminal really has, or the frame's own when it cannot be read."""
        if self._owns_terminal():
            try:
                fd = int(self.fd)  # type: ignore[arg-type]
                return max(1, int(os.get_terminal_size(fd).columns))
            except Exception:  # noqa: BLE001 — a pty without a size: keep the frame's
                pass
        return self.width

    def too_narrow(self) -> bool:
        """Is the terminal narrower than a frame needs (read from the real fd)?"""
        if not self._owns_terminal():
            return False
        return self.terminal_width() < MIN_WIDTH

    def stand_down(self, *, width: int | None = None, note: str = "") -> bool:
        """Leave the screen cleanly: the terminal is narrower than the frame can be.

        The frame never paints below :data:`MIN_WIDTH` — the bars would be mostly bar — and
        it must not paint *wider* than the terminal either, which is what the old up-clamp
        did: a 30-column terminal was reported as 40 and every row wrapped onto the next row
        (measured: ``────…────`` + ``──────`` for one bar). So the frame goes away instead:
        the rows it drew are erased at the geometry the terminal has **now** (it re-wrapped
        them on the way, so the count is visual rows at ``width``), one honest line replaces
        it, and from then on this bar is inert (``active`` is false, ``draw`` is a no-op) so
        nothing can repaint into a terminal it no longer fits.

        ``True`` = the frame really left. A second call is a no-op, and it is **sticky**: once
        the frame is off, widening the terminal again does not bring it back (the session is
        already on the shell's plain prompt, and a frame that reappears under a half-typed
        line is a worse answer than a plain one until the next session).
        """
        if self._stood_down:
            return False
        self._stood_down = True
        if self.frame.drawn > 0 and self.frame.on_screen:
            cells = max(1, int(width or self.width))
            rows = self.frame.visual_rows(cells) + max(0, self.frame.open_rows)
            self._write_raw(f"\x1b[{max(1, rows)}F\x1b[J")
        self.frame.drawn = 0
        self.frame.lines = []
        self.frame.on_screen = False
        self.frame.moved = False
        self.frame.partial = False
        self.frame.open_rows = 0
        self._set_open_line("")
        if note:
            try:
                self._write_raw(note.rstrip("\n") + "\n")
            except Exception:  # noqa: BLE001 — a closed terminal must not raise here
                pass
        return True

    def note_narrow(self) -> str:
        """The one line a stood-down frame leaves behind (English, and it says the width)."""
        return (f"frame off: this terminal is narrower than {MIN_WIDTH} columns "
                f"(the plain prompt takes over; `skaldir chat --frame off` turns the frame "
                f"off for good)")

    def adopt_terminal_width(self) -> bool:
        """Re-read the geometry and repaint if it moved. ``True`` = a repaint was issued.

        The bar used to learn a new size only from ``SIGWINCH`` *plus* a keystroke: a
        resize while an answer streamed left the frame painting at the old width, the
        terminal re-wrapped every row, and the erase started inside the frame — which is
        how five ``write_above()`` calls lost the earlier lines. So the size is re-read
        before every write above the frame as well; the check is one ``ioctl`` and the
        repaint only happens when the size really changed.
        """
        if not self._owns_terminal():
            return False
        width = self.terminal_width()
        if width == self.width:
            return False
        self.on_resize(width=width)
        return True

    @property
    def query(self) -> str:
        """The palette filter: the first token while it is still a ``/`` word."""
        slash = _slash.SLASH if _slash is not None else "/"
        head = self.text.split(" ", 1)[0]
        if not head.startswith(slash) or " " in self.text:
            return ""
        return head[len(slash):]

    def reset(self) -> None:
        """A fresh prompt: no text, no palette, no value menu, no turn."""
        self.text = ""
        self.cursor = 0
        self.selected = 0
        self.open = False
        self.entries = []
        self.menu = []
        self._menu_text = None
        self.turn = None
        self._set_open_line("")
        self._history_at = None
        self._history_draft = ""

    def reload_palette(self) -> None:
        """Forget the palette cache (a resized/re-probed session can ask for a re-read)."""
        self._palette_rows = None

    # -- the palette ------------------------------------------------------- #
    def _all_rows(self) -> list[Any]:
        if self._palette_rows is None:
            try:
                self._palette_rows = list(self._palette() if self._palette is not None else [])
            except Exception:  # noqa: BLE001 — no palette is better than no frame
                self._palette_rows = []
        return self._palette_rows

    def refresh(self) -> list[Any]:
        """Recompute the palette rows for what has been typed (filtered as you type)."""
        slash = _slash.SLASH if _slash is not None else "/"
        head = self.text.split(" ", 1)[0]
        wanted = self.query
        if not head.startswith(slash) or " " in self.text or self._palette is None:
            self.entries = []
            self.open = False
            return []
        pool = self._all_rows()
        if wanted:
            lowered = wanted.lower()
            prefix = [
                row for row in pool if _name_of(row).lstrip(slash).lower().startswith(lowered)
            ]
            inside = [
                row
                for row in pool
                if row not in prefix and lowered in _name_of(row).lstrip(slash).lower()
            ]
            pool = prefix + inside
        self.matches = len(pool)
        limit = self.max_rows if self.max_rows > 0 else len(pool)
        self.entries = pool[: max(1, limit)]
        self.open = bool(self.entries)
        self.selected = min(self.selected, max(0, len(self.entries) - 1))
        return self.entries

    def complete(self) -> bool:
        """``Tab``: complete the line — the highlighted verb, or the engine (CHAT-3).

        The bar knows the verb list; it does **not** know a model id, a session id, a
        checkpoint id or an ``@path``. Those are the completion engine's (``shell.completer``
        → :mod:`skaldir.complete`), and measured they were unreachable in the framed chat
        while they worked in ``skaldir shell``: `/model --main <Tab>` and `@src/sl<Tab>`
        did nothing here. So: a line that already carries arguments, an ``@path`` or a plain
        command goes to the engine first; a bare verb keeps the palette's own completion.

        **A trailing space counts as an argument boundary.** The decision used to be made on
        ``self.text.strip()``, which turned ``"/history "`` back into ``"/history"`` — a bare
        verb — so the line never reached the engine and the value slot never opened
        (measured in the pty: ``/history <Tab>`` left the buffer at ``/history``). Every
        value-slot verb (``/history``, ``/resume``, ``/export``, ``/goal``, …) is Tab-completed
        after a space by the same engine the plain shell uses.

        **An engine that answers with a *list* is drawn, not swallowed.** When the slot holds
        several candidates (``/history `` had 14: two session ids and the verb's flags) their
        longest common prefix is empty, so there is nothing to insert — and the frame used to
        do *nothing at all* with the keystroke. That menu is now kept on the bar
        (:attr:`menu`) and :meth:`compose` draws it above the bars. A flag-only verb keeps its
        visible ``--`` insert, unchanged.
        """
        self.refresh()
        if self._complete_line is not None:
            slash_mark = _slash.SLASH if _slash is not None else "/"
            raw = str(self.text)
            head = raw.strip()
            delegated = (
                head.startswith("@")
                or any(char.isspace() for char in raw)
                or not head.startswith(slash_mark)
            )
            if delegated:
                line = ""
                try:
                    line = self._complete_line(self.text)
                except Exception:  # noqa: BLE001 — a broken engine must not eat Tab
                    line = ""
                if line and line != self.text:
                    self.text = line
                    self.cursor = len(line)
                    self.forget_menu()
                    self.refresh()
                    return True
                return self.open_menu()
        if not self.entries:
            return False
        name = _name_of(self.entries[self.selected])
        if not name:
            return False
        rest = self.text.split(" ", 1)[1] if " " in self.text else ""
        self.text = f"{name} {rest}".strip() + " "
        self.cursor = len(self.text)
        self.forget_menu()
        self.refresh()
        return True

    def forget_menu(self) -> None:
        """Drop the value-slot menu (the line it belonged to is about to change)."""
        self.menu = []
        self._menu_text = None

    def open_menu(self) -> bool:
        """Ask the engine for the value slot's candidates and keep them for the frame.

        ``True`` when there is a menu to draw (the keystroke did something visible);
        ``False`` keeps the old behaviour exactly — no hook, no candidates, nothing drawn.
        Only called on the path where the line hook inserted nothing, so the engine is asked
        once per Tab and never twice for an answer that already advanced the line.
        """
        self.forget_menu()
        if self._complete_menu is None:
            return False
        try:
            candidates = list(self._complete_menu(self.text) or [])
        except Exception:  # noqa: BLE001 — a broken engine must not eat Tab
            return False
        if not candidates:
            return False
        self.menu = candidates
        self._menu_text = str(self.text)
        self.applied.append(f"value menu: {len(candidates)} candidate(s)")
        return True

    def menu_rows(self, width: int | None = None) -> list[str]:
        """The value-slot menu as frame rows — empty when there is nothing (or it is stale)."""
        if not self.menu or self._menu_text != str(self.text):
            return []
        return value_rows(self.menu, int(width or self.width), unicode=self.unicode,
                          max_rows=VALUE_ROWS)

    # -- the picker: one selector for the whole product --------------------- #

    def picker_rows(self, width: int | None = None) -> list[str]:
        """The live picker's rows (empty when no picker is running)."""
        if self.picker is None:
            return []
        self.picker.width = int(width or self.width)
        self.picker.unicode = self.unicode
        return self.picker.rows()

    def picker_hint(self) -> str:
        """The keys the live picker binds (shown in the hint row while it runs)."""
        return self.picker.hint() if self.picker is not None else ""

    def open_picker(self, chooser: Any, *, draw: bool = True) -> bool:
        """Start a picker inside the frame: its rows replace the palette's, its keys win."""
        if chooser is None:
            return False
        self.picker = chooser
        self.forget_menu()
        self.open = False
        self.applied.append(f"picker {chooser.title or 'select'}")
        if draw:
            self.draw(force=True)
        return True

    def close_picker(self) -> Any:
        """End the picker and hand back its result (``None`` while it is still running)."""
        chooser = self.picker
        self.picker = None
        if chooser is None:
            return None
        return chooser.result()

    def pick(self, chooser: Any, *, timeout: float = 0.25) -> Any:
        """Run a picker inside the frame until it is decided (Enter / Esc / Ctrl-C).

        The session survives every outcome: Esc and Ctrl-C cancel the *picker*, not the chat (a
        Ctrl-C inside a picker is swallowed here on purpose, and the frame is repainted before the
        caller gets control back). Returns the picker's :class:`skaldir.picker.Result`.
        """
        if not self.active or termios is None or tty is None:
            return chooser.result()
        fd = int(self.fd)  # type: ignore[arg-type]
        self.install_signals()
        saved = termios.tcgetattr(fd)
        self.open_picker(chooser, draw=False)
        try:
            _raw_terminal(fd, saved)
            self.reset()
            self.paint_logo()
            self.draw(force=True)
            try:
                while True:
                    if self._resized:
                        self.on_resize()
                        if self._stood_down:
                            raise FrameStoodDown(self.note_narrow())
                        continue
                    self.pump_feed()
                    key = read_key(fd, timeout=timeout)
                    if key is None:
                        self.draw()
                        continue
                    decision = chooser.handle(key)
                    if decision in ("select", "cancel"):
                        break
                    self.draw(force=True)
            except KeyboardInterrupt:
                # ``_raw_terminal`` clears ``ISIG``, so a real ``^C`` arrives as the key
                # ``ctrl-c`` and ``chooser.handle`` cancels the pick. This guard is the other
                # road: a build without the shared widget runs plain cbreak, where ``^C`` *is*
                # a signal — inside a picker that means "cancel the pick", not "cancel the
                # session", and the shell's own ``^C`` handling stays in charge of the chat.
                chooser.cancelled = "ctrl-c"
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            self.restore_signals()
            result = self.close_picker()
            self.draw(force=True)
        return result

    def move(self, step: int) -> bool:
        """``Up``/``Down``: walk the palette (or the command history when it is closed)."""
        self.refresh()
        if self.entries:
            self.selected = (self.selected + step) % len(self.entries)
            return True
        return self.recall(step)

    def recall(self, step: int) -> bool:
        """Walk back through the session's own history (newest first)."""
        if self._history is None:
            return False
        try:
            rows = [str(item) for item in (self._history() or []) if str(item).strip()]
        except Exception:  # noqa: BLE001
            return False
        if not rows:
            return False
        newest_first = list(reversed(rows))
        if self._history_at is None:
            self._history_draft = self.text
            self._history_at = 0
        else:
            self._history_at = min(len(newest_first) - 1, max(0, self._history_at - step))
        self.text = newest_first[self._history_at]
        self.cursor = len(self.text)
        self.refresh()
        return True

    def dismiss(self) -> bool:
        """``Esc``: close the palette (and the value menu) without running anything."""
        dropped = bool(self.menu)
        self.forget_menu()
        if not self.open:
            return dropped
        self.open = False
        self.entries = []
        self.selected = 0
        self.applied.append("palette dismissed")
        return True

    # -- input ------------------------------------------------------------- #
    def handle(self, key: str) -> Action:
        """One key → the bar's state. Pure enough to test a whole session without a PTY."""
        if self.picker is not None:
            # a running picker owns the keyboard: a browse/select session is not a command line,
            # and a stray keystroke must not edit a line under it
            decision = self.picker.handle(key)
            if decision in ("select", "cancel"):
                return Action("picker-done", key)
            return Action("redraw") if decision == "redraw" else Action("none")
        if key == "enter":
            self.applied.append("submitted")
            return Action("submit", self.text)
        if key == "ctrl-c":
            return Action("interrupt")
        if key == "ctrl-d":
            return Action("eof") if not self.text else Action("none")
        if key == "ctrl-l":
            return Action("redraw")
        if key == "esc":
            return Action("redraw") if self.dismiss() else Action("none")
        if key == "tab":
            self.complete()
            return Action("redraw")
        if key in ("up", "down"):
            self.move(-1 if key == "up" else 1)
            return Action("redraw")
        if key == "backspace":
            if self.cursor > 0:
                self.text = self.text[: self.cursor - 1] + self.text[self.cursor:]
                self.cursor -= 1
                self.refresh()
            return Action("redraw")
        if key == "ctrl-u":
            self.text = self.text[self.cursor:]
            self.cursor = 0
            self.refresh()
            return Action("redraw")
        if key == "ctrl-w":
            head = self.text[: self.cursor].rstrip()
            keep = head.rpartition(" ")[0]
            rest = self.text[self.cursor:]
            self.text = f"{keep} {rest}" if keep else rest
            self.cursor = max(0, len(keep) + (1 if keep else 0))
            self.refresh()
            return Action("redraw")
        if key == "left":
            self.cursor = max(0, self.cursor - 1)
            return Action("redraw")
        if key == "right":
            self.cursor = min(len(self.text), self.cursor + 1)
            return Action("redraw")
        if key == "home":
            self.cursor = 0
            return Action("redraw")
        if key == "end":
            self.cursor = len(self.text)
            return Action("redraw")
        if key == "delete":
            self.text = self.text[: self.cursor] + self.text[self.cursor + 1:]
            self.refresh()
            return Action("redraw")
        if len(key) == 1 and key.isprintable():
            self.text = self.text[: self.cursor] + key + self.text[self.cursor:]
            self.cursor += 1
            self.refresh()
            return Action("redraw")
        return Action("none")

    # -- the facts --------------------------------------------------------- #
    def facts(self) -> tuple[str, str]:
        """``(left, right)`` for the hint row — every part measured, labelled or ``n/a``.

        Cold start (no turn, no footer) shows the model only. After a turn that
        reported nothing, ``n/a`` stays — a missing reading is a fact, not a blank.
        """
        parts = [f"model {self._safe(self._model).render()}"]
        turn = self.turn
        context, speed, clock = self._band_numbers()
        if turn is not None or context or speed or clock:
            parts.extend(p for p in (context, speed, clock) if p)
        left = self._join(parts)
        right = f"goal: {self._safe(self._goal).render()} {self._dot()} {HINT_HELP}"
        return left, right

    def _safe(self, probe: Callable[[], Fact]) -> Fact:
        """Call a probe at most once per :data:`PROBE_TTL_S` (a hint row is not a probe)."""
        key = id(probe)
        cached = self._probe_cache.get(key)
        now = time.monotonic()
        if cached is not None and now - cached[0] < PROBE_TTL_S:
            return cached[1]
        try:
            value = probe()
        except Exception as exc:  # noqa: BLE001 — a probe may never break the frame
            value = fact_unknown(f"probe failed ({type(exc).__name__})")
        if not isinstance(value, Fact):
            value = fact_unknown("probe returned no fact")
        self._probe_cache[key] = (now, value)
        return value

    def _dot(self) -> str:
        return DOT_RICH if self.unicode else DOT_PLAIN

    def _ellipsis(self) -> str:
        return _ELLIPSIS_RICH if self.unicode else _ELLIPSIS_PLAIN

    def _join(self, parts: Sequence[str]) -> str:
        return f" {self._dot()} ".join(str(part) for part in parts if str(part))

    # -- the screen: the band, the live row, the hint (additive; screen mode only) ------- #
    def _screen(self) -> Any:
        """The layout module, imported lazily so ``chatbar`` stays importable on its own."""
        from . import chat_screen

        return chat_screen

    def _art(self) -> Any:
        """The activity vocabulary (`skaldir.art`), imported lazily for the same reason.

        The frame is the caller of the vocabulary, never the other way round: ``art`` draws a
        line and knows nothing about the frame.
        """
        from . import art

        return art

    def _art_theme(self) -> Any:
        """The theme an activity line is drawn with — the frame's own mode, not the stream's.

        A frame in plain mode must not print a mark the frame itself would never print: ASCII
        glyphs and no escape sequence, whatever the active theme says. Otherwise the active
        theme is used as it stands.
        """
        from . import theme as theme_mod
        from .theme import GLYPHS_ASCII, GLYPHS_UNICODE, MODE_PLAIN

        base = theme_mod.get()
        if bool(base.unicode) == bool(self.unicode) and (self.unicode or base.plain):
            return base
        return theme_mod.Theme(
            mode=base.mode if self.unicode else MODE_PLAIN,
            depth=base.depth,
            glyphs=GLYPHS_UNICODE if self.unicode else GLYPHS_ASCII,
            source=base.source,
        )

    def activity_rows(self, record: Any, *, indent: int = MARGIN) -> list[str]:
        """One transcript row drawn by the vocabulary — ``art.line(record)``, nothing else.

        The record is the chat's own mapping (``{"name": name, "duration_ms": ms, …}``): the
        activity is either named by the caller or decided by the tool's name, and every number
        in the row is one the caller measured. A record the vocabulary draws nothing for yields
        no rows — a caller stamps nothing rather than a blank line.
        """
        try:
            drawn = self._art().line(record, theme=self._art_theme(), indent=indent)
        except Exception:  # noqa: BLE001 — a broken mark must not break the frame
            return []
        return [drawn] if drawn else []

    def _price_fact(self) -> str:
        """``$0.0042`` / ``~$0.0011`` / ``$ n/a`` — the declared price applied to this turn.

        The rule the owner set: a price table is *declared* (``prices.json`` in the runtime
        home, the built-in :data:`~skaldir.chat_screen.DECLARED_PRICES` table, or the
        ``SKALDIR_TELEMETRY_PRICE_PER_1K`` declaration). Nothing here guesses a price, so an
        undeclared model renders ``$ n/a`` and that is a complete answer.
        """
        turn = self.turn
        if turn is None:
            return "$ n/a"
        tokens, estimated = turn.cost_tokens()
        model = self._safe(self._model)
        if self._price is not None:
            try:
                return str(self._price(str(model.text or "")))
            except Exception:  # noqa: BLE001 — a price probe may never break the frame
                return "$ n/a"
        try:
            return str(self._screen().cost_text(tokens, str(model.text or ""),
                                               estimated=estimated))
        except Exception:  # noqa: BLE001
            return "$ n/a"

    def _note_reading(self, chunk: str) -> None:
        """Fold a telemetry footer out of a streamed chunk — the run's own numbers, kept.

        The frame sees every byte a command writes, so it also sees the footer
        :func:`skaldir.telemetry.footer_line` prints (``model · ctx 34% · 1.2k tok/s · 02:14``).
        Keeping the newest one is what lets the band carry a *measured* context reading
        instead of a guess: the number is the product's own, and its ``~`` mark is kept.
        """
        screen = self._screen()
        tail = (self._stream_tail + str(chunk or ""))[-4000:]
        self._stream_tail = tail
        try:
            reading = screen.reading_from_text(tail)
        except Exception:  # noqa: BLE001 — a band must never break a stream
            return
        if reading.known:
            self._reading = reading

    def band_text(self, width: int | None = None) -> str:
        """The status band: ``model · ctx · tok/s · $ · MM:SS · goal``, exactly ``width`` cells.

        Every number is one a source really produced: ``ctx``/``tok/s`` from the turn in
        flight or from the last telemetry footer a run printed, the clock from the turn's own
        stopwatch, ``$`` from a declared price (:meth:`_price_fact`). A number no source
        produced is ``n/a`` and says so — the band never fills a gap with a zero.

        **A known goal is never dropped from the band** (measured defect, audit 10 §7 O1: at
        80 columns with a real model name the band lost ``goal`` entirely — the layout gives
        the goal up *first*, so a chat that is the surface of a run stopped saying which run
        it was). The full label is still the header's fact; the band keeps the fact by giving
        up an **unmeasured price** first and, if the row is still full, by clipping the goal
        into the room that is left (the band's own escape hatch: a clipped fact carries the
        ellipsis). Only when there is no room at all does the old drop order stand.
        """
        width = int(width or self.width)
        context, speed, clock = self._band_numbers()
        model = self._safe(self._model).render()
        goal = self._safe(self._goal).render()
        # the owner's words: a session with no durable goal says ``none``, not a blank
        known = bool(goal) and not goal.endswith("n/a")
        goal_text = f"goal: {'none' if not known else goal}"
        screen = self._screen()
        price = self._price_fact()

        def render(goal_fact: str, price_fact: str) -> str:
            return screen.status_band(
                width, model=model, context=context, speed=speed, price=price_fact,
                clock=clock, goal=goal_fact, unicode=self.unicode,
            )

        band = render(goal_text, price if not known or price != "$ n/a" else "")
        if not known or goal_text in band:
            return band
        # an unmeasured price is worth less than the run this chat is the surface of
        band = render(goal_text, "")
        if goal_text in band:
            return band
        # the row is full: clip the goal into the room the other facts leave, never drop it
        bare = render("", "")
        room = width - len(bare.rstrip()) - len(f" {self._dot()} ")
        if room >= MIN_BAND_GOAL_CELLS:
            clipped = render(truncate_plain(goal_text, room, unicode=self.unicode), "")
            if "goal:" in clipped:
                return clipped
        return band

    def _band_numbers(self) -> tuple[str, str, str]:
        """``(context, speed, clock)`` for the band — newest source first.

        Precedence: the turn in flight (its reading is the newest), then the last telemetry
        footer a run printed, then the **last measured** values from the turn that just
        ended (the band stays pinned under the chat — owner: "soll angeheftet werden").
        """
        turn = self.turn
        reading = self._reading
        clock = self._last_clock if turn is None else turn.clock()
        if (
            turn is None and reading is None and not getattr(self, "_saw_turn", False)
            and clock in ("", "00:00")
        ):
            return "", "", ""
        if turn is not None:
            context = turn.context().render()
            speed = turn.speed().render()
            clock = turn.clock()
            self._last_ctx, self._last_speed = context, speed
        else:
            context = getattr(self, "_last_ctx", "") or "ctx n/a"
            speed = getattr(self, "_last_speed", "") or "tok/s n/a"
        if reading is not None:
            if reading.context and context == "ctx n/a":
                context = reading.context
            if reading.speed and speed == "tok/s n/a":
                speed = reading.speed
        return context, speed, clock

    def hint_text(self, width: int | None = None) -> str:
        """The discoverability row under the frame — the keys, and the verb that clears."""
        if self.picker is not None:
            return self.picker.hint()
        return self._screen().hint_row(int(width or self.width), unicode=self.unicode)

    def live_row(self, width: int | None = None) -> str:
        """The in-frame row for what is happening now (``thinking``, a running tool, …)."""
        state, detail, tick = self.activity
        if state == "idle":
            return ""
        from . import chat_screen

        elapsed = self.turn.elapsed if self.turn is not None else None
        return chat_screen.activity_row(int(width or self.width), state, detail=detail,
                                        tick=tick, unicode=self.unicode, elapsed=elapsed)

    def set_activity(self, state: str, detail: str = "", *, tick: int = 0,
                     draw: bool = True) -> bool:
        """Set the live row's state and repaint **in place** (one row, never a scroll).

        Returns whether the row changed. The frame's own redraw is the only write: a state
        that changes every tick costs one repaint of the same row, not a new line per tick —
        which is what keeps a long tool call from walking the screen.
        """
        new = (str(state or "idle"), str(detail or ""), int(tick))
        old = self.activity
        self.activity = new
        if self.turn is not None and self.turn.started and state != "idle":
            pass  # the clock is the turn's; nothing to record here
        if new == old:
            return False
        self.applied.append(f"activity {new[0]}")
        if draw and self.frame.drawn > 0:
            self.draw()
        return True

    def stamp(self, rows: Sequence[str], *, draw: bool = True) -> int:
        """Write an art badge (or any block) **above** the frame, into the transcript.

        A finished tool call is history: it scrolls. This is the counterpart of
        :meth:`set_activity`, which is state and stays.
        """
        count = 0
        for row in rows:
            self.write_above(str(row))
            count += 1
        if draw:
            self.draw()
        return count

    def note_tool(self, name: str, *, detail: str = "", status: str = "ok",
                  exit_code: int | None = None, duration_ms: int | None = None,
                  streamed: int | None = None, state: str = "tool") -> int:
        """Finish one tool call: its mark, then its badge, above the frame; live row idle.

        The first row is the vocabulary's (`art.line`): the mark the tool's own name deserves
        — a read, a write, an edit, a search, the generic tool mark otherwise — with the
        duration the caller *measured* and nothing invented. The badge under it stays what it
        was: the boxed row that also carries the exit code and the streamed byte count. A
        caller that names the activity explicitly (`state`) wins over the name table.
        """
        record: dict[str, Any] = {
            "name": name,
            "duration_ms": duration_ms,
            "ok": False if (status == "failed" or (exit_code not in (None, 0))) else None,
        }
        if detail:
            record["label"] = detail
        if state and state != "tool":
            record["activity"] = state
        rows = self.activity_rows(record)
        rows.extend(self._screen().tool_badge(
            name,
            detail=detail,
            status=status,
            exit_code=exit_code,
            duration_ms=duration_ms,
            streamed=streamed,
            width=self.width,
            unicode=self.unicode,
        ))
        self.set_activity("idle", draw=False)
        return self.stamp(rows)

    def note_subagents(self, count: Any, *, state: str = "running",
                       label: str = "subagents") -> int:
        """Stamp one vocabulary row for the children running in parallel.

        ``count`` is the number of children the caller *knows about* (its own argv, or the
        run's own envelope) — the vocabulary prints that number and one bar per child, up to
        its bar cap. Zero children are a real answer and draw the info mark.
        """
        rows = self.activity_rows(
            {"activity": "subagents", "count": count, "state": str(state or ""),
             "label": label or "subagents"}
        )
        return self.stamp(rows)

    # -- the logo and the screen wipe ---------------------------------------------------- #
    def logo_rows(self, *, rows: int = 0) -> list[str]:
        """The compact logo block, or ``[]`` — from the caller's producer, else the art."""
        if self._logo is not None:
            try:
                return [str(line) for line in (self._logo() or [])]
            except Exception:  # noqa: BLE001 — no logo is better than a broken frame
                return []
        try:
            return list(self._screen().logo_block(unicode=self.unicode,
                                                  rows=rows or 0 or 4,
                                                  columns=min(self.width - MARGIN, 24)))
        except Exception:  # noqa: BLE001
            return []

    def paint_logo(self, *, force: bool = False) -> bool:
        """Write the logo **once**, above the frame (and again after ``/clear``).

        The owner's rule: the logo sits at the top once and scrolls away with the history. It
        is an ordinary block written above the frame, so it is never part of a repaint — a
        keystroke, a resize or a streamed answer cannot bring it back.

        Screen mode only. The logo is the *screen's* top row (see
        :meth:`~skaldir.chat_screen.screen_lines`); the four-line frame every other caller
        asks for gains no rows from this round, and a caller that has its own first-run screen
        does not get a second logo stacked above its frame.
        """
        if not self.screen:
            return False
        if self._logo_painted and not force:
            return False
        block = self.logo_rows()
        if not block:
            return False
        # the whole block goes out in **one** write: a row at a time would erase and repaint
        # the frame once per row, which is four repaints for one logo and visible churn
        self.stream_chunk("\n".join(block) + "\n")
        self.close_line()
        self._logo_painted += 1
        self.applied.append("logo painted")
        return True

    def clear_screen(self, *, echo: str = "") -> bool:
        """Wipe the screen, put the logo back and draw the frame again — ``/clear``.

        The wipe is ``ESC[2J`` + home instead of a run of newlines: it is one write, it is
        bounded on any terminal, and it cannot leave half a screen behind. ``echo`` is an
        optional line written into the fresh transcript (the shell's own acknowledgement).
        """
        if not self.active:
            return False
        self.frame.on_screen = False
        self.frame.drawn = 0
        self._set_open_line("")
        self._write_raw("\x1b[2J\x1b[H")
        self.frame.lines = []
        self._logo_painted = 0
        if echo:
            self.stream_chunk(str(echo) + "\n")
            self.close_line()
        self.paint_logo(force=True)
        self.draw(force=True)
        return True

    # -- drawing ------------------------------------------------------------ #
    def compose(self) -> tuple[list[str], int]:
        """The frame as it should look now: palette rows, the two bars, the hint row.

        In **screen mode** the frame also carries the status band above the top bar and the
        live state row under it, and the hint row is the discoverability line (the facts moved
        up into the band). The core is still the two bars around the input line — the band and
        the live row are extra rows the frame composes, and both are exactly ``width`` cells
        like every other row, so the erase arithmetic stays a line count.
        """
        self.refresh()
        rows = (
            palette_rows(
                self.entries,
                self.width,
                unicode=self.unicode,
                selected=self.selected,
                max_rows=self.max_rows,
                hidden=max(0, self.matches - len(self.entries)),
            )
            if self.open
            else []
        )
        if not rows:
            # the value slot: the palette is closed (a space ended the verb) and the engine
            # answered with a list. Same rows, same bound, same place — above the top bar.
            rows = self.menu_rows()
        if not rows:
            # a running picker owns the row area (it is the one widget the whole product uses)
            rows = self.picker_rows()
        if self.screen:
            return self._screen().screen_lines(
                self.width,
                band=self.band_text(),
                live=self.live_row() if self.activity[0] != "idle" else "",
                rows=rows,
                text=self.text,
                hint=self.hint_text(),
                unicode=self.unicode,
            )
        left, right = self.facts()
        return frame_lines(
            self.width,
            text=self.text,
            rows=rows,
            hint_left=left,
            hint_right=right,
            unicode=self.unicode,
        )

    def draw(self, *, force: bool = False) -> bool:
        """Put the frame on the terminal. Returns whether anything was written.

        The rule the owner asked for: the frame is re-drawn when the terminal resized or
        when its content changed — a draw that would change nothing prints nothing.
        ``force`` is for the first draw, for a keystroke and for ``Ctrl-L``.

        When the frame was taken off the screen by an output write (``moved``) it is put
        back **as it was drawn**: same lines, same facts, no probe per chunk. The traffic
        that bounds is the shell's own coalescer (30 renders/second on a terminal); a second
        throttle here would leave the frame off the screen between chunks, which is exactly
        what "the frame must not break" forbids.

        A stood-down frame (the terminal went below :data:`MIN_WIDTH`) draws nothing: there is
        no geometry at which its rows would not wrap.
        """
        if self._stood_down:
            return False
        now = time.monotonic()
        lines, cursor_index = self.compose()
        self.frame.last_compose_at = now
        changed = lines != self.frame.lines or cursor_index != self.frame.cursor_index
        if self.frame.drawn > 0 and not changed and not force:
            return self.restore() if self.frame.moved else False
        self._paint(lines, cursor_index)
        return True

    def restore(self) -> bool:
        """Draw the frame again exactly as it was last drawn (after a write above it).

        This is what keeps the frame intact while a reply streams: every chunk erases it,
        and this puts it back — same lines, same facts — without a probe per chunk.
        """
        if self._stood_down or self.frame.drawn <= 0:
            return False
        self._paint(self.frame.lines, self.frame.cursor_index, remember=False)
        return True

    def _paint(self, lines: Sequence[str], cursor_index: int, *,
               remember: bool = True) -> None:
        """Write the frame's lines and park the cursor in the input line."""
        out = self.frame.erase()
        body = "\r\n".join(self._decorate(lines, cursor_index))
        if self.frame.partial:
            # an output line is open: the frame starts on a line of its own below it,
            # otherwise the first bar would be written *into* that line
            body = "\r\n" + body
        out += body
        out += self._cursor_move(2)
        self._write_raw(out)
        self.frame.width = self.width
        self.frame.unicode = self.unicode
        self.frame.on_screen = True
        if remember:
            self.frame.take(lines, cursor_index)
        else:
            self.frame.drawn += 1
            self.frame.moved = False
            self.frame.last_draw_at = time.monotonic()

    def _decorate(self, lines: Sequence[str], cursor_index: int) -> list[str]:
        """Paint the bars with the theme's bar token and the hint row dim.

        The *logical* lines stay unpainted — they are what the frame compares, counts and
        erases with. Only the bytes that go out carry SGR, and in plain mode
        (``paint`` is the identity) the two are the same string.

        In screen mode the band and the live row are dim as well: they are state read at a
        glance, not content to be read word by word.
        """
        if self.plain:
            return list(lines)
        dim_extra: set[int] = set()
        if self.screen:
            dim_extra.add(0)  # the status band
            if self.activity[0] != "idle":
                dim_extra.add(1)  # the live state row
        out: list[str] = []
        for index, line in enumerate(lines):
            if index in (cursor_index - 1, cursor_index + 1):
                out.append(self.paint(line, BAR_TOKEN))
            elif index in dim_extra or index == cursor_index + 2:
                out.append(self.paint(line, "dim"))
            else:
                out.append(line)
        return out

    def _cursor_move(self, below: int = 2) -> str:
        """Park the cursor in the input line at the edit column.

        The frame is always written in full, so the cursor always ends after the hint row:
        two lines below the input line.
        """
        mark = len(MARK_RICH if self.unicode else MARK_PLAIN)
        column = MARGIN + mark + 1 + self.cursor
        return f"\x1b[{max(1, below)}F\x1b[{max(1, column)}C"

    def clear(self) -> None:
        """Take the frame off the screen (on the way out, or before a real full-screen ask)."""
        if self.frame.drawn <= 0:
            return
        self._write_raw(self.frame.erase() + "\x1b[K")
        self.frame.drawn = 0
        self.frame.lines = []
        self.frame.on_screen = False

    def _write_raw(self, text: str) -> None:
        try:
            self.out.write(text)
            self.out.flush()
        except Exception:  # noqa: BLE001 — a closed terminal ends the frame, not the shell
            self.frame.drawn = 0

    # -- output above the frame -------------------------------------------- #
    def _set_open_line(self, tail: str) -> None:
        """Record the unfinished line above the frame (its *rows* are what the erase counts)."""
        self._open_line = tail
        self.frame.partial = bool(tail)
        self.frame.open_rows = _visible_rows(tail, self.width)

    def stream_chunk(self, text: str) -> None:
        """Write a chunk of a command's output above the frame, keeping the frame last."""
        chunk = str(text or "").replace("\r\n", "\n")
        if not chunk:
            return
        self._last_write_at = time.monotonic()
        # the terminal may have moved since the last paint (a resize during a stream, a
        # width that never fit): re-read it *before* the erase, because the erase counts
        # the rows the frame really occupies at the size the terminal really has
        self.adopt_terminal_width()
        if self.turn is not None:
            self.turn.observe(chunk)
        self._note_reading(chunk)
        with self._paint_lock:
            out = self.frame.erase()
            # the frame is off the screen from here on: the cursor is no longer parked in the
            # input line, so a second erase would be measured from the wrong place
            self.frame.on_screen = False
            body = (self._open_line + chunk) if self.frame.partial else chunk
            parts = body.split("\n")
            self._set_open_line(parts[-1])
            out += "\r\n".join(parts[:-1])
            if len(parts) > 1:
                out += "\r\n"
            out += self._open_line
            self._write_raw(out)
            self.frame.moved = True
            self.draw()

    def close_line(self) -> None:
        """Finish an open streamed line: write the newline **and** put the frame back.

        The frame must never be left unparked: a write that moves the cursor away from the
        input line is always followed by a repaint, which is the invariant the erase
        arithmetic depends on.
        """
        if not self.frame.partial:
            self._set_open_line("")
            return
        self.frame.on_screen = False
        self._set_open_line("")
        self._write_raw("\r\n")
        self.frame.moved = True
        self.draw()

    def write_above(self, text: str) -> None:
        """One shell line into the zone above the frame (``working…``, a notice).

        Safe to call **repeatedly**: each call erases the frame, writes its line and paints
        the frame again underneath, so five calls in a row keep five lines (pinned by
        ``test_five_write_above_calls_keep_all_five_lines``). What that needs from the bar
        is that the erase counts the rows the frame *really* occupies — a frame row wider
        than the terminal wraps, and counting it as one row is what used to eat the head of
        the lines written before it.
        """
        self.stream_chunk(str(text) + "\n")
        self.close_line()

    def pump_feed(self) -> int:
        """Write what an attached run has produced since the last poll; returns the count.

        The run itself never touches the terminal: it appends its progress lines to a feed
        object, and they are written here — from the thread that owns the frame. That is what
        keeps an attached run's output from tearing the frame apart, and it is polled in
        :meth:`read_line` between keystrokes so the lines appear while the user types.
        """
        feed = self.feed
        if feed is None:
            return 0
        pending = getattr(feed, "pending", None)
        if not callable(pending):
            self.feed = None
            return 0
        try:
            raw: Any = pending()
        except Exception:  # noqa: BLE001 — a dead feed is dropped, never a crash
            self.feed = None
            return 0
        if not raw:
            return 0
        lines = [str(item) for item in ([raw] if isinstance(raw, str) else list(raw))]
        for line in lines:
            self.write_above(line)
        return len(lines)

    # -- the turn ---------------------------------------------------------- #
    def begin_turn(self, label: str = "working") -> Turn:
        """A turn is starting: the clock runs and the state becomes visible.

        In **screen mode** the state goes onto the live row *inside* the frame and no
        transcript line is spent on it: the row rewrites in place, so a turn that takes a
        minute still costs one row of screen. Without a screen — the four-line frame, or a
        caller that only wants the clock — the old behaviour stands: one ``working…`` line
        written above the frame.

        A **clock ticker** repaints the band every second so ``MM:SS`` never freezes
        while the model or a tool is silent (measured: a 16 s tool left the band on
        ``00:02`` because nothing streamed and nothing drew).
        """
        self.turn = Turn()
        self._saw_turn = True
        self.applied.append("turn started")
        self._start_clock_ticker()
        if self.screen:
            self.set_activity(label or "working", draw=self.frame.drawn > 0)
        else:
            self.write_above(f"{label}{self._ellipsis()}")
        return self.turn

    def end_turn(self) -> Turn | None:
        """The turn is over; the caller gets the clock (``None`` = nothing was running).

        The elapsed time is kept for the band (the turn's duration is a measurement the
        session should not lose) and the live row goes back to ``idle``, which removes it
        from the frame — a finished turn leaves no moving display behind.
        """
        turn = self.turn
        self.turn = None
        self._stop_clock_ticker()
        if turn is not None:
            if turn.finished_at is None:
                turn.finished_at = time.monotonic()
            self._last_clock = turn.clock()
            # pin the last measured ctx/tok/s so the band stays honest after the turn
            self._last_ctx = turn.context().render()
            self._last_speed = turn.speed().render()
        if self.activity[0] != "idle":
            self.set_activity("idle", draw=self.frame.drawn > 0)
        return turn

    def _start_clock_ticker(self) -> None:
        """Repaint the band every 0.5 s **only while the chat is quiet**.

        A full-frame erase while a chunk is being written eats the transcript
        (measured: tool-call lines disappeared after a long wait). The ticker
        therefore waits for :data:`TICKER_QUIET_S` of silence before it paints.
        """

        def tick() -> None:
            while self.turn is not None:
                time.sleep(0.5)
                if self.turn is None:
                    return
                try:
                    if self.frame.drawn <= 0:
                        continue
                    # quiet: no transcript write for a moment → safe to erase/redraw
                    last = getattr(self, "_last_write_at", 0.0) or 0.0
                    if time.monotonic() - last < TICKER_QUIET_S:
                        continue
                    with self._paint_lock:
                        self.draw(force=True)
                except Exception:  # noqa: BLE001 — a ticker must never kill the turn
                    return

        self._stop_clock_ticker()
        self._clock_thread = threading.Thread(target=tick, name="skaldir-clock", daemon=True)
        self._clock_thread.start()

    def _stop_clock_ticker(self) -> None:
        thread = getattr(self, "_clock_thread", None)
        self._clock_thread = None
        if thread is not None and thread.is_alive():
            # daemon thread: it exits on the next loop when self.turn is None
            pass

    # -- resize ------------------------------------------------------------ #
    def note_resize(self, _signum: int | None = None, _frame: Any = None) -> None:
        """``SIGWINCH``: the terminal moved, so the frame owes a repaint at the new size."""
        self._resized = True

    def on_resize(self, *, width: int | None = None, height: int | None = None) -> bool:
        """Take the frame back at the old geometry and draw it again at the new one.

        A terminal that narrowed below :data:`MIN_WIDTH` is the one case where the frame does
        **not** come back: it stands down (see :meth:`stand_down`) instead of painting rows
        the terminal will wrap.
        """
        self._resized = False
        new_width = int(width or term_width(self.fd))
        if height:
            self.height = int(height)
        if new_width < MIN_WIDTH:
            left = self.stand_down(width=new_width, note=self.note_narrow())
            self.width = new_width
            if left:
                self.notes.append("the frame stood down: the terminal is too narrow to frame")
            return False
        if self.frame.drawn > 0:
            # a terminal re-wraps on resize: erase as many *visual* rows as the frame really
            # occupies at the new width, or the repaint leaves its own tail behind it. The
            # unfinished line above the frame re-wraps too, so it is re-measured here.
            self.frame.open_rows = _visible_rows(self._open_line, new_width)
            rows = self.frame.visual_rows(new_width) + max(0, self.frame.open_rows)
            self._write_raw(f"\x1b[{max(1, rows)}F\x1b[J")
            self.frame.drawn = 0
            self.frame.lines = []
            self.frame.on_screen = False
        self.width = new_width
        return self.draw(force=True)

    def install_signals(self) -> None:
        """Own ``SIGWINCH`` while the frame is up (and give it back on the way out)."""
        if not hasattr(signal, "SIGWINCH"):
            return
        try:
            self._previous_winch = signal.getsignal(signal.SIGWINCH)
            signal.signal(signal.SIGWINCH, self.note_resize)
        except Exception:  # noqa: BLE001 — a non-main thread cannot own signals
            self._previous_winch = None

    def restore_signals(self) -> None:
        """Give the previous ``SIGWINCH`` handler back."""
        if self._previous_winch is None or not hasattr(signal, "SIGWINCH"):
            return
        try:
            signal.signal(signal.SIGWINCH, self._previous_winch)
        except Exception:  # noqa: BLE001
            pass
        self._previous_winch = None

    # -- the key loop ------------------------------------------------------ #
    def read_line(self, *, timeout: float = 0.25) -> str | None:
        """One line from a real terminal, framed. ``None`` = the user asked to leave.

        The terminal is in cbreak mode, so ``Ctrl-C`` still reaches the process as
        ``SIGINT`` (Python raises ``KeyboardInterrupt`` out of the read): the shell's own
        ``^C`` handling stays in charge, and the ``finally`` below restores the terminal —
        that is what makes the frame survive a cancel instead of leaving it half-drawn.

        A terminal below :data:`MIN_WIDTH` is read plainly instead of being framed (and a
        terminal that narrows under a live frame raises :class:`FrameStoodDown`, so the caller
        hands the turn to the shell's own prompt rather than being told the session ended).
        """
        if not self.active:
            return read_plain(sys.stdin, echo=False)
        fd = int(self.fd)  # type: ignore[arg-type]
        if self.too_narrow():
            self.stand_down(note=self.note_narrow())
            return read_plain(sys.stdin, echo=False)
        self.install_signals()
        saved = termios.tcgetattr(fd)
        try:
            _cbreak_keep_input(fd)
            self.reset()
            # the logo goes up once, above the frame, *before* the first paint — so the first
            # screen is logo, band, live row, the two bars and the hint in one draw
            self.paint_logo()
            self.draw(force=True)
            while True:
                if self._resized:
                    self.on_resize()
                    if self._stood_down:
                        raise FrameStoodDown(self.note_narrow())
                    continue
                # the attached run's progress goes out between keystrokes: the frame stays the
                # last thing on the screen and the lines are written by this thread only
                self.pump_feed()
                key = read_key(fd, timeout=timeout)
                if key is None:
                    self.draw()
                    continue
                action = self.handle(key)
                if action.kind == "submit":
                    return action.text
                if action.kind == "eof":
                    return None
                if action.kind == "interrupt":
                    raise KeyboardInterrupt
                # a keystroke repaints immediately: the throttle is for the output stream,
                # never for the echo of what the user just typed
                self.draw(force=True)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            self.restore_signals()


# --------------------------------------------------------------------------- #
# keys
# --------------------------------------------------------------------------- #

_SEQUENCES: dict[str, str] = {
    "A": "up",
    "B": "down",
    "C": "right",
    "D": "left",
    "H": "home",
    "F": "end",
    "Z": "tab",
    "1~": "home",
    "2~": "insert",
    "3~": "delete",
    "4~": "end",
    "5~": "pageup",
    "6~": "pagedown",
}
_CONTROL: dict[str, str] = {
    "\x03": "ctrl-c",
    "\x04": "ctrl-d",
    "\x09": "tab",
    "\x0a": "enter",
    "\x0d": "enter",
    "\x0c": "ctrl-l",
    "\x15": "ctrl-u",
    "\x17": "ctrl-w",
    "\x7f": "backspace",
    "\x08": "backspace",
    "\x1b": "esc",
}


def _cbreak_keep_input(fd: int) -> None:
    """Character-at-a-time input **without throwing away what was already typed**.

    ``tty.setcbreak`` defaults to ``TCSAFLUSH``, which discards the terminal's *pending*
    input on every call. That is a real defect for a line editor, and this frame is a line
    editor: the prompt is painted (logo, band, frame — dozens of writes) and only then does
    the key loop call ``select()``. Everything a person typed while that paint was in flight
    was already in the input queue, and ``TCSAFLUSH`` deleted it — silently, character by
    character, and always the *first* characters of the line.

    Measured (real 80x24 PTY, synthetic CPU load so the paint window widens): with
    ``TCSAFLUSH`` the whole line ``scan -`` was discarded on a loaded host and the ``^C``
    that followed only ever cleared an empty prompt ("``^C input cleared``"); with
    ``TCSADRAIN`` the line arrives whole, the command starts and cancels, on every run.
    ``TCSADRAIN`` is the right rule here precisely because the restore is already
    ``TCSADRAIN``: a drained terminal is the contract on both sides of the prompt, and the
    only thing that is deliberately discarded is nothing — nothing was read to discard.

    ``ISIG`` stays **on** (that is cbreak's own rule, and it is what makes a real ``^C``
    arrive as ``SIGINT`` and cancel the *running command* rather than a keystroke), so this
    changes what the frame loses, never what the shell sees.
    """
    if tty is None:  # pragma: no cover — no terminal module: the caller already guarded
        return
    try:
        tty.setcbreak(fd, termios.TCSADRAIN)
    except (TypeError, ValueError, termios.error, OSError):  # pragma: no cover
        tty.setcbreak(fd)


def _raw_terminal(fd: int, saved: Any) -> None:
    """cbreak with ``ISIG`` cleared — the widget's own raw-mode rule (``picker.raw_mode``).

    ``tty.setcbreak`` leaves ``ISIG`` on, so ``^C`` becomes a ``SIGINT`` instead of the byte
    ``\\x03``. Inside a picker both readings must mean the *same* thing — "cancel this pick" —
    and the way the rest of the product reaches that is the shared widget's: clear ``ISIG``,
    decode ``\\x03`` as the key ``ctrl-c``, and let the picker answer it. A build without
    ``skaldir.picker`` falls back to plain cbreak and the ``KeyboardInterrupt`` guard in
    ``ChatBar.pick``, which reaches the same answer by the other road.
    """
    try:
        from . import picker as _picker

        _picker.raw_mode(termios, fd, saved)
    except Exception:  # noqa: BLE001 — no shared widget: plain cbreak, guarded by the caller
        if tty is not None:
            tty.setcbreak(fd)


def read_key(fd: int, *, timeout: float = 0.25, escape_timeout: float = 0.05) -> str | None:
    """One key from ``fd`` — a name for the special ones, the character itself otherwise.

    ``None`` means "nothing arrived within the timeout": the caller then checks the resize
    flag (and repaints if it has to), which is how a ``SIGWINCH`` is honoured even while
    nobody is typing.
    """
    first = _read_bytes(fd, timeout)
    if not first:
        return None
    text = _decode_char(fd, first, escape_timeout)
    if text == "\x1b":
        return _read_escape(fd, escape_timeout)
    if text in _CONTROL:
        return _CONTROL[text]
    if text.isprintable():
        return text
    return "" if text else ""


def _read_bytes(fd: int, timeout: float) -> bytes:
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return b""
    try:
        return os.read(fd, 1)
    except OSError:  # pragma: no cover — a terminal that went away
        return b""


def _decode_char(fd: int, first: bytes, timeout: float) -> str:
    """One character, continuation bytes included (a `ü` is not half a character)."""
    body = first
    while body:
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            if len(body) >= 4:
                return body.decode("utf-8", errors="replace")
            extra = _read_bytes(fd, timeout)
            if not extra:
                return body.decode("utf-8", errors="replace")
            body += extra
    return ""


def _read_escape(fd: int, timeout: float) -> str:
    """``ESC`` alone (a dismissal) or the sequence behind it (an arrow, ``Del``)."""
    follow = _read_bytes(fd, timeout)
    if follow[:1] != b"[":
        return "esc"
    body = b""
    while len(body) < 8:
        chunk = _read_bytes(fd, timeout)
        if not chunk:
            break
        body += chunk
        if 0x40 <= chunk[0] <= 0x7E:
            break
    return _SEQUENCES.get(body.decode("ascii", errors="replace"), "esc")


# --------------------------------------------------------------------------- #
# the plain path: a pipe, a redirect, a log
# --------------------------------------------------------------------------- #


def read_plain(stream: Any, *, echo: bool = True, out: Any = None) -> str | None:
    """One line with **no** frame: the ``skaldir > `` prompt, ASCII, no escapes.

    This is the documented degradation for a non-TTY (and it is what
    :class:`repl.Shell` already does for a pipe — the same prompt, the same echo); the
    frame is not drawn at all, so nothing of this module reaches a log.
    """
    try:
        raw = stream.readline()
    except (KeyboardInterrupt, EOFError):
        raise
    if raw in ("", None):
        return None
    line = str(raw).rstrip("\n")
    if echo:
        sink = out if out is not None else sys.stdout
        try:
            sink.write(plain_prompt() + line + "\n")
            sink.flush()
        except Exception:  # noqa: BLE001
            pass
    return line


# --------------------------------------------------------------------------- #
# the shell front end
# --------------------------------------------------------------------------- #


class Front:
    """What a :class:`repl.Shell` talks to: the framed line, the streams, the routing."""

    def __init__(self, shell: Any, bar: ChatBar) -> None:
        self.shell = shell
        self.bar = bar
        #: a plain-language line goes to the agent loop while the frame is up
        self.routing = True
        self.turns = 0
        #: the transcript of the last prose turn — the conversation the *next* prose turn
        #: continues (``agent run --previous <path>``). ``""`` = no turn yet, and then the
        #: line is an independent run exactly as it was before there was a conversation.
        self.history = ""

    def read_line(self, shell: Any = None) -> str | None:
        """The framed line; raises ``KeyboardInterrupt`` exactly like ``input()`` does.

        When the terminal narrows below :data:`MIN_WIDTH` while the frame is up, the frame
        stands down and ``bar.read_line`` raises :class:`FrameStoodDown` — the turn is then
        handed to the shell's own plain path (``front_for`` answers ``None`` from here on),
        because reporting ``None`` would tell the shell the input had ended.
        """
        shell = self.shell if shell is None else shell
        try:
            line = self.bar.read_line()
        except FrameStoodDown:
            return shell.read_line()
        if line is None:
            return None
        self.turns += 1
        return line

    def begin_turn(self, label: str = "working") -> Turn:
        """Start the turn clock and show the ``working…`` line."""
        return self.bar.begin_turn(label)

    def end_turn(self) -> Turn | None:
        return self.bar.end_turn()

    # -- the screen, for whoever drives the turn (additive) ---------------- #
    def activity(self, state: str, detail: str = "", *, tick: int = 0) -> bool:
        """Set the live row inside the frame (``thinking``, a running tool, a search)."""
        return self.bar.set_activity(state, detail, tick=tick)

    def tool_done(self, name: str, **facts: Any) -> int:
        """Stamp a finished tool call's art badge into the transcript."""
        return self.bar.note_tool(name, **facts)

    def subagents(self, count: Any, *, state: str = "running",
                  label: str = "subagents") -> int:
        """Stamp a row for the parallel children (the vocabulary's ``subagents`` mark)."""
        return self.bar.note_subagents(count, state=state, label=label)

    def clear(self, *, echo: str = "") -> bool:
        """Wipe the screen and put the logo back (the frame already handles the order)."""
        return self.bar.clear_screen(echo=echo)

    def pick(self, chooser: Any) -> Any:
        """Run a picker inside the frame (``skaldir.picker``) and hand its result back.

        A frame that stands down mid-pick (the terminal narrowed below the frame's minimum) ends
        the pick as a cancel: the caller then uses the numbered list, which is what a terminal
        that cannot draw arrows gets.
        """
        try:
            return self.bar.pick(chooser)
        except FrameStoodDown:
            return chooser.result()


def is_clear_verb(text: str) -> bool:
    """Is this line the screen-wipe verb? (``/clear`` / ``/cls``, no arguments)"""
    head = str(text or "").strip().split(" ", 1)[0].lower()
    return head in CLEAR_VERBS


def handle_clear(shell: Any, text: str) -> bool | None:
    """Route ``/clear`` — ``None`` means "not my line, keep the old path".

    The hook the shell calls before it consults the slash table, so the verb works even
    though this block does not own ``slash.SLASH_COMMANDS``. With a frame up: wipe, rewrite
    the logo, draw the frame. Without one (a pipe, ``--json``, ``SKALDIR_CHATBAR=0``) there is
    nothing to wipe and the shell says so instead of pretending it cleared something.
    """
    if not is_clear_verb(text):
        return None
    front = front_of(shell)
    if front is not None and front.bar.stood_down:
        note = ("no frame is drawn here (this terminal is narrower than "
                f"{MIN_WIDTH} columns, so the frame stood down), so /clear has nothing to "
                "clear")
        try:
            sink = getattr(shell, "out", None) or sys.stdout
            sink.write(note + "\n")
        except Exception:  # noqa: BLE001 — a closed stream must not end the session
            pass
        return False
    if front is None or not front.bar.active:
        note = ("no frame is drawn here (stdout is not a terminal), so /clear has nothing "
                "to clear")
        try:
            sink = getattr(shell, "out", None) or sys.stdout
            sink.write(note + "\n")
        except Exception:  # noqa: BLE001 — a closed stream must not end the session
            pass
        return False
    front.bar.clear_screen(echo="screen cleared — the logo is back")
    return False


def _std_stream_ok(stream: Any) -> bool:
    """Is this a real terminal file object (a real fd, and a tty)?"""
    if stream is None:
        return False
    try:
        if not bool(stream.isatty()):
            return False
    except Exception:  # noqa: BLE001
        return False
    try:
        os.fstat(int(stream.fileno()))
    except Exception:  # noqa: BLE001 — a StringIO, a wrapper, a redirected object
        return False
    return True


def _bar_enabled() -> bool:
    """``SKALDIR_CHATBAR=0`` (or ``GODSV_CHATBAR=0``) turns the frame off for good."""
    if _env is not None:
        return bool(_env.flag("CHATBAR", True))
    return True


def front_for(shell: Any, *, force: bool = False) -> Front | None:
    """The frame for this shell — or ``None``, and then the shell keeps its own path.

    ``None`` is the honest answer for a pipe, a redirect, a ``StringIO`` (a test), a
    ``--json`` run (whose stdout is captured) or ``SKALDIR_CHATBAR=0``: the frame needs a
    real terminal on both ends, because that is the only place it can be taken back. A
    shell that already has a front gets that same one back (never a second frame) — unless
    that frame stood down (the terminal went below :data:`MIN_WIDTH`), and then ``None`` is
    what keeps the session on the plain prompt instead of framing 40 columns into 30.

    ``MIN_WIDTH`` is a **decision**, never a clamp: :func:`term_width` reports the real size,
    so this check is reachable (it used to be dead code behind ``max(MIN_WIDTH, columns)``).
    """
    existing = front_of(shell)
    if existing is not None:
        return None if existing.bar.stood_down else existing
    if bool(getattr(shell, "chatbar_off", False)):
        return None
    if not _bar_enabled():
        return None
    if not bool(getattr(shell, "interactive", False)):
        return None
    stdin = getattr(shell, "stdin", None)
    out = getattr(shell, "out", None)
    if not _std_stream_ok(stdin) or not _std_stream_ok(out):
        return None
    if termios is None or tty is None:
        return None
    try:
        fd = int(out.fileno())
    except Exception:  # noqa: BLE001
        return None
    width = term_width(fd)
    if width < MIN_WIDTH:
        return None
    bar = ChatBar(
        out,
        fd=fd,
        width=width,
        height=term_height(fd),
        unicode=None,
        paint=getattr(shell, "paint", None) or (lambda text, _role="": text),
        palette=_palette_for(shell),
        model=probe_model,
        goal=lambda: probe_goal(shell),
        history=lambda: [entry.line for entry in getattr(shell.history, "entries", [])],
        # CHAT-3: the SAME completion engine the plain shell uses (`complete.LineCompleter`,
        # already wired on the shell). The bar's own Tab only knows how to complete a bare
        # verb; a flag value, a session/checkpoint id or an `@path` needs the engine.
        complete_line=lambda text: _engine_line(shell, text),
        # …and the same engine again for the case the line hook cannot express: a slot with
        # *several* candidates and no common prefix to insert. Only asked when the line hook
        # inserted nothing, so no Tab pays for it twice.
        complete_menu=lambda text: _engine_menu(shell, text),
        # the chat screen: the status band over the frame, the live state row inside it and
        # the discoverability hint under it. Every other caller keeps the four-line frame.
        screen=not bool(getattr(shell, "chatbar_plain_frame", False)),
        # an attached run's progress feed: the bar polls it between keystrokes and writes the
        # lines itself (the frame has exactly one writer, and it is this thread).
        feed=getattr(shell, "feed", None),
    )
    front = Front(shell, bar)
    _attach(shell, front)
    return front


def _engine_line(shell: Any, text: str) -> str:
    """One line through the shell's own completer; ``""`` when the engine adds nothing.

    The engine is :mod:`skaldir.complete` as the shell wired it (``shell.completer``), so
    the framed chat, the plain shell and the tests complete through **one** implementation —
    the palette's verb list is the only thing the bar adds on top, and it stays.
    """
    completer = getattr(shell, "completer", None)
    if completer is None:
        return ""
    try:
        completion = completer.compute(str(text))
        head = completion.completed_line()
    except Exception:  # noqa: BLE001 — a broken completion must never eat a keystroke
        return ""
    return head if head != text else ""


def _engine_menu(shell: Any, text: str) -> Sequence[Any]:
    """The engine's *candidate list* for the token under the cursor; ``()`` when there is none.

    ``_engine_line`` can only answer with text to insert — and a value slot with several
    candidates (``/history `` → two session ids *and* the verb's flags) has no common prefix,
    so there is nothing to insert and the keystroke used to be swallowed whole. This is the
    same ``completer.compute`` call, read for its suggestions instead of its insert.
    """
    completer = getattr(shell, "completer", None)
    if completer is None:
        return ()
    try:
        completion = completer.compute(str(text))
    except Exception:  # noqa: BLE001 — a broken completion must never eat a keystroke
        return ()
    return tuple(getattr(completion, "suggestions", ()) or ())


def _palette_for(shell: Any) -> Callable[[], Sequence[Any]]:
    if _slash is None:
        return lambda: ()

    def rows() -> Sequence[Any]:
        try:
            session = shell.session
            return _slash.entries(
                session.tree,
                reachable=session.reachable(),
                report=session.report,
                tree_error=session.tree_error,
            )
        except Exception:  # noqa: BLE001 — no palette is better than no frame
            return ()

    return rows


def _attach(shell: Any, front: Front) -> None:
    """Point the shell's streams and its activity indicator at the frame (once).

    Both the *shell* and its *session* are re-pointed, because the command's own output does
    not travel through ``shell.out``: ``Session.execute`` reads ``session.out``/``.err``, wraps
    them in a ``Tee`` and redirects ``sys.stdout``/``sys.stderr`` onto that. A frame that only
    replaced the shell's two attributes would therefore sit *beside* the streamed answer
    instead of surviving under it (measured in the pty capture: the answer streamed straight
    to the terminal while the frame stayed where it was).

    The streaming indicator steps aside as well: the frame's own hint row carries the clock
    and the speed now, and two live displays on one line is how a frame gets torn.
    """
    if getattr(shell, "_chatbar_front", None) is not None:
        return
    out = FrameStream(shell.out, front.bar)
    err = FrameStream(getattr(shell, "err", None) or shell.out, front.bar)
    shell.out = out
    shell.err = err
    session = getattr(shell, "session", None)
    if session is not None:
        # the command's output path: `Session.execute` reads *these* two attributes
        session.out = out
        session.err = err
        if getattr(session, "activity", None) is not None:
            session.activity = None
            front.bar.notes.append(
                "the streaming indicator stepped aside: the frame's hint row carries the clock"
            )
    shell._chatbar_front = front


def front_of(shell: Any) -> Front | None:
    """The front already attached to this shell, if any."""
    return getattr(shell, "_chatbar_front", None)


def root_names(tree: Any) -> set[str]:
    """The command names the real parser knows (the only thing prose may not look like)."""
    children = getattr(tree, "children", None)
    if not isinstance(children, dict):
        return set()
    return {str(name).lower() for name in children}


def looks_like_prose(text: str, *, known: set[str] | None = None) -> bool:
    """Route non-command text to the model, including a one-word greeting or reply.

    Word count and punctuation do not indicate intent: ``hi`` and ``yes`` are
    valid chat turns. Preserve explicit slash commands, known CLI roots and the
    shell's built-ins; every other nonempty line is prose.
    """
    words = str(text or "").split()
    if not words:
        return False
    first = words[0].lower()
    if first.startswith("/") or first in {"quit", "exit", "help", "history"}:
        return False
    return first not in (known or set())


def _transcript_from_envelope(shell: Any) -> str:
    """The transcript path the command itself reported, when it reported one."""
    try:
        from .repl import envelope_of
    except Exception:  # noqa: BLE001 — no product driver: nothing to read the envelope with
        return ""
    result = getattr(shell, "last_result", None)
    try:
        envelope = envelope_of(str(getattr(result, "output", "") or "")) or {}
    except Exception:  # noqa: BLE001 — an unparseable output is not a transcript
        return ""
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    for key in ("transcript_path", "transcript"):
        text = str((data or {}).get(key) or "")
        if text and Path(text).is_file():
            return text
    return ""


def _transcript_since(shell: Any, since: float) -> str:
    """The transcript a turn just wrote, found by time in the runtime's own run dir.

    The fallback for a turn whose command did not print an envelope (the chat drives the
    real CLI, and only ``--json`` prints one). It is a *measurement*: the newest
    ``<runtime>/agent/runs/*.jsonl`` whose mtime is not older than the turn's own start.
    Nothing here invents a handle — a turn that wrote no transcript leaves the handle empty
    and the next line is an independent run again.

    Measured defect (a real 100x30 pty, two prose turns, a stub endpoint): this looked in
    ``<runtime>/runs`` — ``agent_state_dir`` is the identity, and the *caller* is what appends
    the group directory (``agent_loop_cli._state_dir`` → ``<runtime>/agent``). The run dir the
    chat's own ``agent run`` writes was therefore never found, the handle stayed empty, and the
    second turn went out with ``roles=[system,user]`` and no trace of the first exchange. Both
    the group directory and the runtime itself are searched, newest first, and the *directory*
    is the only thing this fix widens.
    """
    try:
        from .agent_loop import runs_dir

        runtime = getattr(getattr(shell, "session", None), "runtime", "")
        if not runtime:
            return ""
        base = Path(str(runtime)).expanduser()
        # the group's own state dir first (`agent run` writes `<runtime>/agent/runs/`), then the
        # runtime itself — a caller that already passes the state dir must keep working
        files = []
        for directory in (runs_dir(base / "agent"), runs_dir(base)):
            try:
                files += list(directory.glob("*.jsonl"))
            except OSError:  # pragma: no cover — an unreadable dir is not a failure of the turn
                continue
        files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    except Exception:  # noqa: BLE001 — no run dir is not a failure of the turn
        return ""
    for path in files[:20]:
        try:
            if path.stat().st_mtime >= float(since) - 1.0:
                return str(path)
        except OSError:  # pragma: no cover — a file that vanished between glob and stat
            continue
    return ""


def _stamp_tool_rows(front: Any, path: str) -> int:
    """One art row per tool the model **really** called, from the run's own transcript.

    ``front_end`` stamps one row per *shell command* — ``[▸_] agent run`` — because that is
    what the shell ran. The tools the model called inside that run are a different fact, and
    the vocabulary to draw them already existed with no product call site
    (``art.from_tool_event``): a read draws ``[+] a.py 12 ms``, a shell call ``[▸_] shell
    41 ms``, and a refused call the blocked mark. This is that call site: the events come out
    of ``agent_loop.read_transcript`` — the run's own JSONL, never a re-render of the progress
    lines — so the name and the duration are the loop's measurements.
    """
    if not path or not Path(path).is_file():
        return 0
    try:
        from . import art
        from .agent_loop import read_transcript

        events = list(read_transcript(path).get("tool_calls") or [])
    except Exception:  # noqa: BLE001 — a transcript that cannot be read draws no row
        return 0
    rows: list[str] = []
    for event in events:
        try:
            drawn = str(art.from_tool_event(event) or "").rstrip()
        except Exception:  # noqa: BLE001 — one undrawable event does not lose the others
            drawn = ""
        if drawn.strip():
            rows.append(drawn)
    stamp = getattr(front.bar, "stamp", None)
    if rows and callable(stamp):
        stamp(rows)
    return len(rows)


def route_plain(shell: Any, text: str) -> bool | None:
    """Send a plain-language line to the agent loop, or ``None`` to keep the old path.

    ``None`` means "not mine": no frame is up (a pipe, a scripted session, a test) or the
    line is a command. When the frame *is* up, a question goes to ``skaldir agent run`` —
    the one verb that answers in prose — through the shell's own ``run_command``, so the
    gates, the streamed answer, the history and the footer are the ones every other
    command already gets.

    **The session is the conversation.** The previous prose turn's transcript is handed to
    the next one (``--previous``), so the messages accumulate instead of every line being an
    independent run with an empty ``previous_transcript``. The handle is the transcript
    path, which is also what ``/context --transcript`` and ``agent transcript`` take.
    """
    front = front_of(shell)
    if front is None or not front.routing:
        return None
    tree = getattr(getattr(shell, "session", None), "tree", None)
    if tree is None:
        return None
    if not looks_like_prose(text, known=root_names(tree)):
        return None
    argv: tuple[str, ...] = ("agent", "run", str(text), "--chat-output")
    if front.history:
        argv += ("--previous", str(front.history))
    started = time.time()
    front.begin_turn()
    try:
        stop = shell.run_command(str(text), argv=argv, source_line=str(text))
    finally:
        front.end_turn()
    front.history = _transcript_from_envelope(shell) or _transcript_since(shell, started)
    if front.history:
        _stamp_tool_rows(front, front.history)
    return bool(stop)


# --------------------------------------------------------------------------- #
# the verb table (what is real, what is planned)
# --------------------------------------------------------------------------- #


#: The marker a frame sets while it owns the terminal (``SKALDIR_FRAME=1``, one definition in
#: :mod:`skaldir.env`). A command that would open a *second* terminal surface reads it.
FRAME_ENV = _env.FRAME_ENV if _env is not None else "SKALDIR_FRAME"


@contextlib.contextmanager
def frame_owns_terminal(active: bool) -> Any:
    """Mark the terminal as **owned by a frame** for the duration of a command.

    A product command that would open a *second* surface — ``skaldir run "<goal>"`` opens the
    chat attached to the goal whenever both ends are a terminal — must not do it while a frame
    already drives this one: the nested frame draws over the outer one and the outer command
    never returns to its prompt (measured: the outer live row stayed on
    ``⚙ tool — run …`` for 110 s after the goal reached ``VERIFIED_COMPLETE``). With the
    marker set, such a command runs in the foreground and returns, so the outer frame stamps
    its own result row (``exit 0 (OK) · 110 ms · 1.1 kB streamed``) exactly like the pipe path.
    """
    if not active:
        yield
        return
    previous = os.environ.get(FRAME_ENV)
    os.environ[FRAME_ENV] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(FRAME_ENV, None)
        else:
            os.environ[FRAME_ENV] = previous


def mapping_rows(tree: Any = None, **kwargs: Any) -> list[dict[str, Any]]:
    """The slash verb table from :mod:`skaldir.slash` (the one source of that truth)."""
    if _slash is None:  # pragma: no cover — the palette ships with the product
        return []
    return _slash.mapping(tree, **kwargs)


def verb_report(tree: Any = None) -> dict[str, Any]:
    """The verb table *and* its counts, so a summary cannot disagree with its rows."""
    rows = mapping_rows(tree)
    planned = [row["slash"] for row in rows if row["kind"] == "planned"]
    builtin = [row["slash"] for row in rows if row["kind"] == "builtin"]
    real = [row["slash"] for row in rows if row["kind"] == "real"]
    unwired = [row["slash"] for row in rows if row["kind"] == "unwired"]
    return {
        "verbs": rows,
        "counts": {
            "total": len(rows),
            "real": len(real),
            "builtin": len(builtin),
            "planned": len(planned),
            "unwired": len(unwired),
        },
        "planned": planned,
        "builtin": builtin,
        "unwired": unwired,
    }


def format_verbs(report: dict[str, Any], *, unicode: bool = False) -> str:
    """The verb table as text: one row per verb, real vs planned stated plainly."""
    dash = "—" if unicode else "-"
    lines = [f"slash verbs {dash} what each one really runs", ""]
    width = max((len(row["slash"]) for row in report["verbs"]), default=8)
    for row in report["verbs"]:
        kind = row["kind"]
        target = row["command"] or "-"
        note = ""
        if kind == "planned":
            note = f"  (nearest: {', '.join(row['nearest']) or 'none'})"
        elif kind in ("unwired", "unverified"):
            note = f"  ({kind})"
        lines.append(f"  {row['slash']:<{width}}  {kind:<8} {target}{note}")
    counts = report["counts"]
    lines.append("")
    lines.append(
        f"  {counts['real']} real, {counts['builtin']} built-in, "
        f"{counts['planned']} planned, {counts['unwired']} not wired in this build"
    )
    if counts["planned"]:
        lines.append(f"  planned verbs are never faked: {', '.join(report['planned'])}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the CLI surface: `skaldir chat` (the framed shell), plus `chat verbs` / `preview`
# --------------------------------------------------------------------------- #

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))


def _set_data(payload: dict[str, Any]) -> None:
    setter = getattr(_CONTRACT, "set_data", None)
    if callable(setter):
        setter(payload)


def _add_evidence(entries: Sequence[tuple[str, str, str]]) -> None:
    adder = getattr(_CONTRACT, "add_evidence", None)
    if not callable(adder):
        return
    for ref, kind, note in entries:
        try:
            adder(ref, kind, note)
        except Exception:  # noqa: BLE001 — an older contract takes fewer arguments
            adder(ref, kind)


def _fail(code: int, message: str, *, hint: str = "") -> int:
    reporter = getattr(_CONTRACT, "usage_error", None) if code == EXIT_USAGE else None
    if callable(reporter):
        reporter(message, hint=hint)
        return code
    print(message, file=sys.stderr)
    if hint:
        print(hint, file=sys.stderr)
    return code


def cmd_chat_verbs(args: argparse.Namespace) -> int:
    """``skaldir chat verbs`` — the honest slash-verb mapping, as data or as text."""
    tree = _parser_tree()
    report = verb_report(tree)
    _set_data(report)
    _add_evidence(
        [
            ("src/skaldir/slash.py:1", "code",
             "SLASH_COMMANDS — every verb names a real argv"),
            ("src/skaldir/chatbar.py:1", "code",
             "the frame draws those rows, it does not invent them"),
        ]
    )
    if not bool(getattr(args, "json", False)):
        print(format_verbs(report, unicode=unicode_ok(sys.stdout)))
    return EXIT_OK


def cmd_chat_preview(args: argparse.Namespace) -> int:
    """``skaldir chat preview`` — the frame itself, drawn as text (both modes)."""
    width = int(getattr(args, "width", 0) or 0) or term_width(None)
    rich = unicode_ok(sys.stdout) and not bool(getattr(args, "no_color", False))
    bar = ChatBar(sys.stdout, width=width, unicode=rich, rows=PALETTE_ROWS)
    bar.text = "how do I add tests to this project?"
    bar.turn = Turn(started=time.monotonic() - 7.0, chars=0)
    hint_left, hint_right = bar.facts()
    lines, cursor = frame_lines(
        width,
        text=bar.text,
        hint_left=hint_left,
        hint_right=hint_right,
        unicode=bar.unicode,
    )
    plain, _ = frame_lines(
        width,
        text=bar.text,
        hint_left=hint_left,
        hint_right=hint_right,
        unicode=False,
    )
    payload = {
        "width": width,
        "lines": lines,
        "plain_lines": plain,
        "cursor_index": cursor,
        "plain_prompt": plain_prompt(),
    }
    _set_data(payload)
    _add_evidence([("src/skaldir/chatbar.py:1", "code", "frame_lines() — the layout under test")])
    if not bool(getattr(args, "json", False)):
        print(f"frame at {width} columns (cursor on line {cursor}):")
        for line in lines:
            print(line)
        print()
        print("plain mode (a pipe, a log, --no-color): the same frame, ASCII, no escapes:")
        for line in plain:
            print(line)
        print()
        print("what a pipe really gets (no frame is drawn at all):")
        print(plain_prompt() + bar.text)
    return EXIT_OK


def cmd_chat(args: argparse.Namespace) -> int:
    """``skaldir chat`` — the shell with the framed chat bar."""
    from . import repl

    options = repl.ShellOptions()
    options.runtime = str(getattr(args, "runtime", "") or "") or None
    options.limit = int(getattr(args, "limit", 0) or 0)
    options.probe = bool(getattr(args, "probe", True))
    options.color = bool(getattr(args, "color", False))
    options.no_color = bool(getattr(args, "no_color", False))
    options.transcript = str(getattr(args, "transcript", "") or "") or None
    options.resume = bool(getattr(args, "resume", False))
    options.session_id = str(getattr(args, "session", "") or "")
    # the run this chat is the surface of (the hand-over from `skaldir run`, or `--attach`)
    options.attach = str(getattr(args, "attach", "") or "")
    # …and its progress feed, if a run in this process is streaming into the chat
    options.feed = getattr(args, "feed", None)
    options.activity = not bool(getattr(args, "no_activity", False))
    frame = str(getattr(args, "frame_mode", "auto") or "auto")
    if bool(getattr(args, "json", False)) and frame != "off":
        frame = "off"  # one envelope on stdout: the frame would be screen traffic on it
    shell = repl.Shell(options)
    # The chat screen draws its own compact logo at the very top (logo.py's mark, 2-4 rows),
    # so the shell's text splash would put a *second* logo above it. On a terminal the splash
    # stands down and the screen's mark is the one logo; a pipe keeps the splash, because a
    # pipe has no screen for the frame to draw on. `quiet` is set here, after construction,
    # so it only means "no splash" — the echo and footer paths are untouched.
    if frame != "off" and _std_stream_ok(getattr(shell, "out", None)):
        shell.options.quiet = True
    if frame == "off":
        shell.chatbar_off = True
    elif frame == "on" and not _std_stream_ok(getattr(shell, "out", None)):
        _warn(
            "the frame needs a terminal: stdout is not a TTY, so the shell keeps the "
            "plain `skaldir > ` prompt"
        )
    summary = shell.run()
    front = front_of(shell)
    payload = dict(summary)
    payload["frame"] = {
        "drawn": bool(front is not None),
        "width": front.bar.width if front is not None else 0,
        "activity": "replaced by the frame's hint row" if front is not None else "",
        "notes": list(front.bar.notes) if front is not None else [],
    }
    _set_data(payload)
    _add_evidence(
        [
            ("src/skaldir/chatbar.py:1", "code", "ChatBar.read_line() — the framed input zone"),
            ("src/skaldir/slash.py:1", "code", "the palette the frame draws"),
            ("src/skaldir/repl.py:1", "code", "Shell.run() — the session loop the frame drives"),
        ]
    )
    return EXIT_OK if not summary.get("failures") else EXIT_OK


def cmd_chat_router(args: argparse.Namespace) -> int:
    """The one handler; ``verb`` picks what it does (no second parser to keep in sync)."""
    verb = str(getattr(args, "verb", "") or "shell")
    if verb == "verbs":
        return cmd_chat_verbs(args)
    if verb == "preview":
        return cmd_chat_preview(args)
    return cmd_chat(args)


def _parser_tree() -> Any:
    """The real parser tree (``None`` when this build cannot build it — said, not hidden)."""
    try:
        from .cli import build_parser
        from .complete import build_tree

        return build_tree(build_parser())
    except Exception:  # noqa: BLE001 — the table still reports, marked unverified
        return None


def _warn(text: str) -> None:
    warner = getattr(_CONTRACT, "warn", None)
    if callable(warner):
        try:
            warner(text)
            return
        except Exception:  # noqa: BLE001
            pass
    print(f"warning: {text}", file=sys.stderr)


def add_chat_parser(subparsers: Any) -> Any:
    """Add ``chat`` to a subparsers handle (idempotent, create-or-extend)."""
    existing = getattr(subparsers, "choices", {}).get("chat")
    if existing is not None:
        return existing
    parser = subparsers.add_parser(
        "chat",
        help="the chat bar: two bars around the input line, a slash palette, honest facts",
        description=(
            "The Skaldir chat bar. The input line is framed by two full-width bars; the "
            "palette opens on `/` and is filtered as you type (Tab completes, Enter runs, "
            "Esc dismisses); the row under the bars carries the model, the context, the "
            "speed and the clock on the left and the durable goal plus /help on the right. "
            "A line without a leading / is prose and goes to the agent loop. On a pipe the "
            "frame is not drawn at all: the prompt is `skaldir > ` and nothing escapes."
        ),
    )
    _add_chat_arguments(parser)
    parser.set_defaults(func=cmd_chat_router)
    return parser


def _add_chat_arguments(parser: Any) -> None:
    """The group's flags — one place, so the wired command and the standalone parser agree."""
    parser.add_argument(
        "verb",
        nargs="?",
        choices=("shell", "verbs", "preview"),
        default="shell",
        help="shell (default): run the framed session; verbs: the slash verb mapping; "
             "preview: draw the frame as text",
    )
    parser.add_argument("--frame", dest="frame_mode", choices=("auto", "on", "off"),
                        default="auto",
                        help="draw the two-bar frame: auto (a real terminal gets it), "
                             "on (ask for it), off (never)")
    parser.add_argument("--width", type=int, default=0,
                        help="columns for `preview` (default: the terminal's)")
    parser.add_argument("--runtime", default="", help="runtime dir override")
    parser.add_argument("--resume", action="store_true",
                        help="restore the previous session's state and print a summary")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after N lines (0 = no limit)")
    parser.add_argument("--transcript", default="",
                        help="write the raw session transcript to this path")
    parser.add_argument("--color", action="store_true",
                        help="force ANSI colour even when stdout is not a TTY")
    parser.add_argument("--no-color", dest="no_color", action="store_true",
                        help="never emit colour or unicode (the plain, ASCII look)")
    parser.add_argument("--no-probe", dest="probe", action="store_false",
                        help="skip the startup selftest probe")
    parser.add_argument("--no-activity", dest="no_activity", action="store_true",
                        help="do not let the shell draw its own indicator (the frame's "
                             "hint row carries the clock)")
    parser.add_argument("--session", default="",
                        help="the session id grants are filed under (default: shell-<pid>)")
    parser.add_argument("--attach", default="",
                        help="the goal id this chat is the surface of: the band shows *that* "
                             "run's goal, and /steer + /queue deliver to it")


def launch_from_bare_call(*, no_color: bool = False, runtime: str = "",
                          attach: str = "", limit: int = 0) -> int:
    """``skaldir`` with no arguments: exactly the chat ``skaldir chat`` runs.

    The chat owns the terminal (the mark, the status band, the two bars, the palette), so a bare
    call is that same session — one implementation, so the two doors cannot drift. A pipe, a
    redirect or a non-TTY never reaches here: ``repl.launch_from_bare_call`` keeps the welcome
    text for that case, because a chat that waits for input would hang an automated caller.
    """
    return cmd_chat(
        argparse.Namespace(
            verb="shell", frame_mode="auto", width=0, runtime=runtime, attach=attach,
            resume=False, limit=int(limit), transcript="", color=False, no_color=no_color,
            probe=True, no_activity=False, session="", json=False, quiet=False, feed=None,
        )
    )


def launch_attached_chat(*, goal_id: str, runtime: str = "", feed: Any = None,
                         session: str = "", no_color: bool = False, limit: int = 0) -> int:
    """The chat as the **surface of a running goal** — what ``skaldir run`` hands over to.

    ``goal_id`` is the run's durable handle (the id of a real row in ``runs.db``): the band shows
    *that* goal, ``/steer`` and ``/queue`` deliver to it, and ``feed`` is the run's progress, whose
    lines the chat's own thread writes above the frame. Nothing is invented here — an empty
    ``goal_id`` opens the same chat unattached, and the goal fact then says ``none``.
    """
    return cmd_chat(
        argparse.Namespace(
            verb="shell", frame_mode="auto", width=0, runtime=runtime, attach=str(goal_id or ""),
            resume=False, limit=int(limit), transcript="", color=False, no_color=no_color,
            probe=True, no_activity=False, session=str(session or ""), json=False, quiet=False,
            feed=feed,
        )
    )


def contract_entries() -> dict[str, Any]:
    """The ``cli_contract.COMMANDS`` entry for ``chat`` (installed at CLI build time)."""
    spec = getattr(_CONTRACT, "CommandSpec", None)
    if spec is None:
        return {}
    return {
        "chat": spec(
            "kernel",
            (
                {
                    "ref": "src/skaldir/chatbar.py:1",
                    "kind": "code",
                    "note": "ChatBar — the two-bar frame, the keys, the hint row",
                },
                {
                    "ref": "src/skaldir/slash.py:1",
                    "kind": "code",
                    "note": "the palette the frame draws, with planned verbs marked",
                },
            ),
            "the chat bar: a framed input line, a slash-first palette, the session loop",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_BLOCKED, EXIT_INTERNAL),
            mutates=True,
            data_schema={
                "type": "object",
                "properties": {
                    "runtime": {"type": "string", "description": "the runtime dir in use"},
                    "interactive": {"type": "boolean",
                                    "description": "was the session on a terminal"},
                    "commands": {"type": "integer", "description": "commands this session ran"},
                    "failures": {"type": "integer", "description": "commands that failed"},
                    "frame": {
                        "type": "object",
                        "description": "what the frame did",
                        "properties": {
                            "drawn": {"type": "boolean",
                                      "description": "was the two-bar frame driving the prompt"},
                            "width": {"type": "integer",
                                      "description": "the frame's width in cells (0 = no frame)"},
                            "activity": {"type": "string",
                                         "description": "what happened to the shell's indicator"},
                            "notes": {"type": "array",
                                      "description": "the frame's own notes about this session"},
                        },
                    },
                    "verbs": {"type": "array",
                              "description": "`chat verbs`: the slash verbs with their kind"},
                    "counts": {"type": "object",
                               "description": "`chat verbs`: how many are real/builtin/planned"},
                    "lines": {"type": "array",
                              "description": "`chat preview`: the frame as lines"},
                },
            },
        )
    }


def install_contract_entries() -> list[str]:
    """Add this group's ``COMMANDS`` entries (idempotent); returns the paths added."""
    entries = contract_entries()
    if not entries or _CONTRACT is None:
        return []
    added: list[str] = []
    commands = getattr(_CONTRACT, "COMMANDS", None)
    if commands is None:
        return []
    for path, entry in entries.items():
        if path not in commands:
            commands[path] = entry
            added.append(path)
    return added


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``.

    Idempotent, and it registers this group's ``cli_contract.COMMANDS`` entry so a wired
    ``chat`` never ships without a contract. Nothing here edits ``cli.py`` — the CLI owner
    adds two lines::

        from .chatbar import register_cli as _register_chat
        _register_chat(sub)

    exactly the way the other groups are wired.
    """
    existing = getattr(subparsers, "choices", {}).get("chat")
    if existing is not None:
        install_contract_entries()
        return existing
    parser = add_chat_parser(subparsers)
    install_contract_entries()
    return parser


def build_parser() -> argparse.ArgumentParser:
    """The group's own parser for the standalone entry point and the tests.

    ``python -m skaldir.chatbar [shell|verbs|preview] …`` runs the same arguments the
    wired-in ``skaldir chat`` takes — one flag list, so the two can never drift.
    """
    parser = argparse.ArgumentParser(prog="skaldir chat", description="The Skaldir chat bar")
    _add_chat_arguments(parser)
    parser.set_defaults(func=cmd_chat_router)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m skaldir.chatbar [shell|verbs|preview]`` — the group, standalone."""
    parser = build_parser()
    parsed = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    parsed.command_path = "chat"
    return int(parsed.func(parsed))


if __name__ == "__main__":  # pragma: no cover — the standalone entry point
    raise SystemExit(main())
