"""The interactive Skaldir shell — Hermes' command breadth, Claude Code's comfort.

``skaldir shell`` (and ``skaldir`` with no arguments, when the CLI owner wires the
bare call) drops into one long-lived session that keeps its state, shows a compact
prompt with the real runtime/goal/provider, streams each command's output as it is
produced, lets Ctrl-C cancel the running command without killing the session, and
remembers what was typed (history + ``--resume``).

Everything it can do is something the CLI can already do:

* a bare line is the ``skaldir`` argv it looks like (``tools list`` → ``skaldir
  tools list``); the real parser parses it, the real handler runs it, the real exit
  code and envelope come back;
* a ``/`` line is a :mod:`skaldir.slash` alias for a real command group;
* ``?`` shows the inline overlay built from the **real** parser metadata
  (:func:`skaldir.complete.help_overlay`) plus the honest "what can I do from here"
  line derived from the **real** ``selftest`` report (:func:`skaldir.slash.hint_line`).

Nothing here re-implements a command, invents a capability or fabricates an exit code:
a group that is not wired into this build is refused with the reason and the closest
real command, and a layer the selftest reports unreachable is shown as blocked.

Keys: Tab completes (commands, options, option values, the ``/`` palette); ``?`` opens
the overlay; Esc dismisses the palette; Ctrl-C cancels the running command (the
session stays); Ctrl-D or ``/quit`` leaves.

The look is one quiet line per fact, never a box:

``<status>``
    one dim line above the prompt, the 2-3 most relevant facts only
    (``workspace · goal:… · provider:…``), re-printed between commands **only when
    it changed**, clipped to 80 columns by dropping the least relevant fact.
``skaldir ❯``
    the prompt itself: one line, always the same shape.
``⏺ <what runs>`` / ``  ⎿ <what it did>``
    the message rhythm of a turn: one action line, then the command's own output,
    then one result line. One blank line between turns, no boxes, no rules.

Glyphs come from the theme (``skaldir.theme``) and degrade to ASCII on a pipe, a log,
``NO_COLOR`` or ``--no-color`` — colour is never the information.

Standalone: ``python -m skaldir.repl`` runs the shell without the product CLI wiring.

Integration (the only change ``cli.py`` needs — 3 lines)
--------------------------------------------------------

In ``cli.build_parser()``, directly after ``_register_jev_cli(sub)`` (so it runs before
the final ``cli_contract.annotate(parser)`` — ``annotate`` is what puts ``command_path``
on the leaf and adds ``--json/--format/--quiet/--no-color``)::

    from .repl import register_cli as _register_repl_cli   # +
    _register_repl_cli(sub)                                # +

Optional, for ``skaldir`` with **no** arguments (the bare call): replace the
``_print_welcome(...)`` line in ``main()`` with a launcher that starts the shell only
when a human is at the terminal (a pipe, a script or ``--json`` keeps today's
behaviour)::

    from .repl import launch_from_bare_call
    return launch_from_bare_call(no_color=bool(getattr(args, "no_color", False)))

``register_cli`` is idempotent and registers the ``shell`` contract entry itself, so
``tests/test_cli_contract_dump.py`` stays green without an edit to ``cli_contract.py``.
"""

from __future__ import annotations

import argparse
import contextlib
import inspect
import io
import json
import os
import re
import shutil
import sys
import textwrap
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

try:  # the product contract (envelope, exit codes, selftest)
    from . import cli_contract as _CONTRACT
except Exception:  # pragma: no cover — standalone builds
    _CONTRACT = None

from . import complete as _complete
from . import render as _render
from . import slash as _slash
from .streaming import DEFAULT_HZ as _STREAM_HZ
from .streaming import Coalescer as _Coalescer
from .streaming import LockedStream as _LockedStream
from .streaming import same_terminal as _same_terminal

try:  # the two-bar chat bar (CHATBAR) — optional: the shell runs without it
    from . import chatbar as _chatbar
except Exception:  # pragma: no cover — a build without the frame
    _chatbar = None

try:  # the model picker's in-frame hook (MODEL-PICKER) — optional like the frame
    from . import model_picker as _model_picker
except Exception:  # pragma: no cover — a build without the picker keeps the old mapping
    _model_picker = None

#: the session was interrupted by the user — never a CLI exit code, a signal one.
EXIT_CANCELLED = 130

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))

PROMPT_NAME = "skaldir"
PROMPT_MARK = "❯"

#: The status line above the prompt is capped here ("truncates gracefully at 80 columns").
STATUS_WIDTH = 80

#: Glyphs ``theme.py`` does not carry (it owns the box/punctuation set). Every pair is
#: ``(unicode, ascii)``: a pipe, a log, ``NO_COLOR`` or ``--no-color`` gets the ASCII form.
_OWN_GLYPHS: dict[str, tuple[str, str]] = {
    "prompt": ("❯", ">"),
    "action": ("⏺", "*"),
    "result": ("⎿", "\\"),
    "tab": ("⇥", "<TAB>"),
    "dash": ("—", "-"),
    # the shell's own separator: `·` on a terminal, `|` in plain mode — a plain `-`
    # between two facts reads like a minus sign (the theme's generic sep is overridden
    # here on purpose, so the status line and the result line read the same way)
    "sep": ("·", "|"),
    # the streaming activity indicator's lead glyph and the approval prompt's bullet
    "activity": ("✳", "*"),
    "ask": ("▶", ">"),
}

#: Only used when there is no theme module at all (a standalone or older build).
_FALLBACK_GLYPHS: dict[str, str] = {"sep": "·", "ellipsis": "…", "arrow": "→"}

#: How many menu rows the palette/completion draws (a palette is scanned, not read).
MENU_ROWS = 12

#: The streaming activity indicator. It redraws in place every :data:`ACTIVITY_INTERVAL_S`
#: while a command runs, and a command shorter than :data:`ACTIVITY_MIN_MS` never gets one
#: (a clock on a 40 ms command is noise, not evidence).
ACTIVITY_INTERVAL_S = 0.5

#: How long the command must be silent before the indicator draws over the screen. During
#: output the answer is its own feedback; a spinner that keeps redrawing while text pours
#: past it splits the answer's lines (measured, see ``Activity.quiet_s``).
ACTIVITY_QUIET_S = 1.0
ACTIVITY_MIN_MS = 300

#: The approval prompt's answers, as typed (one letter — Codex walks a list, we take a
#: key, because our prompt sits on the same line as the command it is about).
APPROVAL_ANSWERS: dict[str, str] = {
    "a": "once",
    "s": "session",
    "r": "rule",
    "d": "deny",
    "q": "abort",
}

#: The body size units the activity indicator and the footer use (decimal, like every
#: other size this CLI prints).
_SIZE_UNITS: tuple[tuple[int, str], ...] = (
    (1_000_000_000, "GB"),
    (1_000_000, "MB"),
    (1_000, "kB"),
)

#: The runtime subdirectory the edit transactions live in (``skaldir edit``'s default
#: state dir) — the footer and ``/diff`` read it, neither writes it.
EDIT_STATE_DIR = "edit"

#: One SGR sequence (used to bracket a painted prompt for readline).
_SGR = re.compile("\x1b\\[[0-9;]*[A-Za-z]")

#: The warm-session probe: capped so a prompt never stalls, cached so a prompt is cheap.
WARM_PROBE_TIMEOUT_S = 0.15
WARM_PROBE_TTL_S = 5.0

_HISTORY_DIR = "shell"
_HISTORY_FILE = "history.jsonl"


# --------------------------------------------------------------------------- #
# colour: the theme module when it is there, plain text when it is not
# --------------------------------------------------------------------------- #

_ANSI_ROLES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
    # semantic tokens (what the shell and slash.py actually ask for) — monochrome on
    # purpose: weight and reverse video carry the meaning, never a colour
    "strong": "1",
    "muted": "2",
    "ok": "32",
    "fail": "31",
    "warn": "33",
    "blocked": "33",
    "key": "2",
    "value": "",
}

#: names a theme module may use for the same role (tried in order); the first group is
#: the vocabulary of the real ``skaldir.theme`` module (its semantic tokens)
_THEME_TOKENS = {
    "bold": ("strong", "heading"),
    "dim": ("muted", "key"),
    "red": ("fail",),
    "green": ("ok",),
    "yellow": ("warn", "blocked"),
    "blue": ("accent",),
    "magenta": ("accent",),
    "cyan": ("accent",),
    "white": ("default",),
}

_ROLE_ALIASES = {
    "bold": ("bold", "strong", "title"),
    "dim": ("dim", "muted", "faint", "subtle"),
    "red": ("red", "error", "danger"),
    "green": ("green", "ok", "success"),
    "yellow": ("yellow", "warn", "warning", "caution"),
    "blue": ("blue", "info"),
    "magenta": ("magenta", "accent"),
    "cyan": ("cyan", "info", "highlight"),
}


def load_theme() -> Any:
    """Import ``skaldir.theme`` if this build has one (it is written in parallel).

    Never a hard dependency: without the module the painter falls back to its own ANSI
    codes, and with no colour at all it degrades to plain text.
    """
    try:
        from . import theme  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 — module absent or broken
        return None
    return theme


class Painter:
    """``paint(text, role)`` — theme-first, ANSI second, plain text last.

    The theme module has two possible shapes and both are supported, because it is
    written in parallel with this file:

    * an object API (``detect(stream, …) -> Theme`` with ``Theme.style(text, token)``
      — the real one) — preferred, since it also decides glyphs and colour depth;
    * a function API (``paint(text, role)`` / ``style(text, role)`` / per-role
      functions) as a fallback.

    Anything missing or raising degrades to this module's own ANSI codes, and with
    ``color=False`` to plain text. ``used_theme`` records which one answered.
    """

    def __init__(
        self,
        *,
        color: bool = True,
        stream: Any = None,
        no_color: bool = False,
        module: Any = None,
    ) -> None:
        self.color = bool(color)
        self.stream = stream if stream is not None else sys.stdout
        self.module = module if module is not None else load_theme()
        self.used_theme = False
        self.theme = self._detect_theme()

    def _detect_theme(self) -> Any:
        """Ask the theme module for its decided theme on this stream (or ``None``).

        The painter's own colour decision is passed *as the mode*: ``--color`` on a pipe
        must really paint, and a plain painter must not be handed a rich theme (the theme
        still decides the depth and the glyph set — it just does not overrule the decision
        that was already made for this stream).
        """
        module = self.module
        detect = getattr(module, "detect", None)
        if not callable(detect):
            return None
        try:
            theme = detect(
                self.stream,
                no_color=bool(os.environ.get("NO_COLOR")),
                mode="rich" if self.color else "plain",
            )
        except Exception:  # noqa: BLE001 — an incompatible signature
            return None
        return theme if hasattr(theme, "style") else None

    def _from_object(self, text: str, role: str) -> str | None:
        theme = self.theme
        if theme is None:
            return None
        tokens = _THEME_TOKENS.get(role, (role,)) if role else ("default",)
        for token in tokens:
            try:
                painted = theme.style(text, token)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(painted, str):
                self.used_theme = True
                return painted
        return None

    def _from_functions(self, text: str, role: str) -> str | None:
        module = self.module
        if module is None:
            return None
        for name in ("paint", "style", "colorize", "colour", "ansi"):
            function = getattr(module, name, None)
            if callable(function):
                try:
                    painted = function(text, role)
                except Exception:  # noqa: BLE001 — incompatible signature
                    continue
                if isinstance(painted, str) and painted:
                    self.used_theme = True
                    return painted
        for name in _ROLE_ALIASES.get(role, (role,)):
            function = getattr(module, name, None)
            if callable(function):
                try:
                    painted = function(text)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(painted, str) and painted:
                    self.used_theme = True
                    return painted
        return None

    def __call__(self, text: str, role: str = "") -> str:
        text = str(text)
        if not self.color:
            return text
        themed = self._from_object(text, role)
        if themed is None:
            themed = self._from_functions(text, role)
        if themed is not None:
            return themed
        code = _ANSI_ROLES.get(role)
        return f"\033[{code}m{text}\033[0m" if code else text


def color_enabled(stream: Any = None, *, forced: bool = False, no_color: bool = False) -> bool:
    """Colour only on a TTY, unless forced — and never when NO_COLOR is set.

    When ``skaldir.theme`` exists its own decision wins (it also honours ``SKALDIR_COLOR``
    and ``FORCE_COLOR``); without it the plain TTY rule applies.
    """
    if no_color or os.environ.get("NO_COLOR"):
        return False
    module = load_theme()
    detect = getattr(module, "detect", None)
    if callable(detect):
        try:
            theme = detect(
                stream if stream is not None else sys.stdout,
                no_color=False,
                mode="rich" if forced else None,
            )
            return bool(getattr(theme, "color", False))
        except Exception:  # noqa: BLE001 — fall through to the plain rule
            pass
    if forced:
        return True
    return bool(getattr(stream or sys.stdout, "isatty", lambda: False)())


# --------------------------------------------------------------------------- #
# streaming: every command's output is tee'd (live + recorded)
# --------------------------------------------------------------------------- #


class Tee(io.TextIOBase):
    """Write through to the terminal *and* keep a copy for the session record.

    ``first_write_at`` is when the first byte reached the terminal — the shell reports it,
    so "the output streams" is a measured number and not a claim. ``bytes_streamed`` and
    ``lines_streamed`` are measured here too: they are what the streaming activity
    indicator and the footer report, so neither has to guess.

    ``activity`` (optional) is cleared before every write so the command's output never
    lands on top of the live indicator line.

    ``coalesce`` (optional, a :class:`skaldir.streaming.Coalescer`) paces the *terminal*:
    every chunk is still recorded verbatim in ``sink`` and counted here, but at most one
    ``write`` per interval reaches ``primary``. The 200-chunks-a-second stream a model
    produces becomes 30 writes a second, and the bytes are unchanged — see
    ``MVP/53-cline-perf.md`` for the measurement and the reason this is a terminal-only
    concern (a pipe, a redirect and a transcript all keep every byte immediately).
    """

    def __init__(self, primary: Any, sink: io.StringIO, activity: Any = None,
                 coalesce: Any = None) -> None:
        self._primary = primary
        self._sink = sink
        self.first_write_at: float | None = None
        self.bytes_streamed = 0
        self.lines_streamed = 0
        self.activity = activity
        self.coalesce = coalesce

    def write(self, text: str) -> int:  # type: ignore[override]
        text = str(text)
        if text and self.first_write_at is None:
            self.first_write_at = time.monotonic()
        self.bytes_streamed += len(text.encode("utf-8", "replace"))
        self.lines_streamed += text.count("\n")
        try:
            self._sink.write(text)
        except Exception:  # noqa: BLE001 — the record must never break the command
            pass
        stream = self.coalesce
        if stream is not None:
            # the activity line is a terminal concern; the coalescer owns the primary
            try:
                return int(stream.write(text))
            except Exception:  # noqa: BLE001 — a closed terminal is not the command's fault
                return len(text)
        activity = self.activity
        if activity is not None:
            activity.clear()
        try:
            written = self._primary.write(text)
        except Exception:  # noqa: BLE001 — a closed terminal is not the command's fault
            return len(text)
        try:
            self._primary.flush()
        except Exception:  # noqa: BLE001
            pass
        return written if isinstance(written, int) else len(text)

    def metrics(self) -> Any:
        """The coalescer's measured numbers, or ``None`` when this Tee is not paced."""
        if self.coalesce is None:
            return None
        return getattr(self.coalesce, "metrics", None)

    def finish(self) -> None:
        """Render the tail (the bound never holds back the last bytes of a command).

        ``drain`` rather than ``flush``: the coalescer's ``flush`` honours the render bound
        (a producer calls it after every chunk), so only ``drain`` is allowed to say "and
        now everything that is left".
        """
        drain = getattr(self.coalesce, "drain", None)
        if callable(drain):
            try:
                drain()
            except Exception:  # noqa: BLE001 — a closed terminal is not a crash
                pass
        for stream in (self._sink, self._primary):
            flush = getattr(stream, "flush", None)
            if not callable(flush):
                continue
            try:
                flush()
            except Exception:  # noqa: BLE001 — a closed terminal is not a crash
                pass

    def flush(self) -> None:
        for stream in (self.coalesce, self._sink, self._primary):
            if stream is None:
                continue
            try:
                stream.flush()
            except Exception:  # noqa: BLE001
                pass

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return bool(getattr(self._primary, "isatty", lambda: False)())

    def fileno(self) -> int:  # pragma: no cover — only when something really needs it
        return self._primary.fileno()

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return str(getattr(self._primary, "encoding", "utf-8"))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._primary, name)


# --------------------------------------------------------------------------- #
# the activity indicator (the streaming clock) and its number formats
# --------------------------------------------------------------------------- #


def format_elapsed(seconds: float) -> str:
    """Elapsed seconds in the compact form the indicator and the footer both use.

    ``0.5s`` → ``12.4s`` under a minute (the tenths are what make a tick visible), then
    ``2m 03s``, then ``1h 02m 03s``. One function, so the clock cannot read two ways.
    """
    value = max(0.0, float(seconds or 0.0))
    if value < 60:
        return f"{value:.1f}s"
    whole = int(value)
    if whole < 3600:
        return f"{whole // 60}m {whole % 60:02d}s"
    return f"{whole // 3600}h {(whole % 3600) // 60:02d}m {whole % 60:02d}s"


def format_size(count: int) -> str:
    """A byte count in decimal units (``1340`` → ``1.3 kB``), never a fake precision."""
    value = max(0, int(count or 0))
    for scale, unit in _SIZE_UNITS:
        if value >= scale:
            return f"{value / scale:.1f} {unit}"
    return f"{value} B"


def format_count(value: int) -> str:
    """A token/line count short enough for one line (``1240`` → ``1.2K``)."""
    number = max(0, int(value or 0))
    if number < 1_000:
        return str(number)
    if number < 1_000_000:
        return f"{number / 1_000:.1f}K"
    return f"{number / 1_000_000:.1f}M"


# --------------------------------------------------------------------------- #
# the frame's tool rows (`MVP/66-chat-ux-plan.md` §7, `MVP/67-activity-art.md`)
# --------------------------------------------------------------------------- #

#: The commands whose run really *is* a call to the model: nothing else runs until the model
#: answers, so the frame's live row says `thinking` — the honest word for that wait.
MODEL_COMMANDS: tuple[tuple[str, ...], ...] = (("agent", "run"), ("goal", "run"))


def tool_label(argv: Sequence[str]) -> str:
    """The name a command's tool row carries: its two words, or the single verb.

    ``agent run "say hi"`` → ``agent run``; ``tools list`` → ``tools list``; ``selftest
    --json`` → ``selftest``. Only the command's own words are used — no path is invented and
    no flag is part of a name.
    """
    words = [str(item) for item in argv if not str(item).startswith("-")]
    return " ".join(words[:2] if len(words) > 1 else words[:1]) or PROMPT_NAME


def is_model_command(argv: Sequence[str]) -> bool:
    """Is this run a call to the model? (Two root commands are; everything else is local.)"""
    head = tuple(str(item) for item in argv[:2])
    return head in MODEL_COMMANDS


def parallel_children(argv: Sequence[str]) -> int | None:
    """The child count a fan-out was asked for (``agent spawn --parallel 3``), else ``None``.

    ``None`` means the line is not a fan-out at all, which is a different answer from a
    fan-out whose count was not readable (that one draws no count row rather than a guessed
    number).
    """
    items = [str(item) for item in argv]
    if items[:2] != ["agent", "spawn"]:
        return None
    for index, item in enumerate(items):
        value = ""
        if item == "--parallel" and index + 1 < len(items):
            value = items[index + 1]
        elif item.startswith("--parallel="):
            value = item.split("=", 1)[1]
        else:
            continue
        try:
            return max(0, int(value))
        except ValueError:
            return None
    return None


class Activity:
    """The streaming activity indicator: one line, rewritten in place, never invented.

    While a command runs, a daemon thread redraws a single line on the terminal —
    ``✳ running skaldir agent run (2.5s · 1.4 kB · 12 lines)`` — and the numbers come from
    the :class:`Tee` that carries the command's own output, so they are measurements, not
    estimates. ``tokens`` is only ever printed when the caller *knows* it (the probe in
    :meth:`note_tokens`), and is omitted otherwise instead of being guessed.

    Only a real terminal gets a drawn indicator: with ``enabled=False`` (a pipe, a log,
    ``--no-footer``, JSON mode) the object still measures, and writes nothing.
    """

    def __init__(
        self,
        stream: Any,
        *,
        enabled: bool = False,
        interval: float = ACTIVITY_INTERVAL_S,
        paint: Callable[[str, str], str] | None = None,
        glyphs: Callable[[str], str] | None = None,
        width: Callable[[], int] | None = None,
    ) -> None:
        self.stream = stream
        self.enabled = bool(enabled)
        self.interval = max(0.05, float(interval or ACTIVITY_INTERVAL_S))
        self._paint = paint or (lambda text, _role="": text)
        self._glyph = glyphs or (lambda name: name)
        self._width = width or (lambda: STATUS_WIDTH)
        self.label = ""
        self.started_at = 0.0
        self.bytes = 0
        self.lines = 0
        self.tokens: int | None = None
        self.drawn = 0
        self._line_open = False
        self._stop = threading.Event()
        #: the terminal's one writer lock. The indicator and the command's output share it,
        #: so a redraw can never land inside a byte of somebody else's line — measured
        #: without it: 4 of 240 streamed lines lost their first byte (MVP/53-cline-perf.md).
        self.lock = threading.RLock()
        self._lock = self.lock
        self._thread: threading.Thread | None = None
        #: the Tees carrying the command's output: with them attached the indicator shows
        #: what has *really* streamed by the moment it redraws (live), not a final total
        self._tees: list[Any] = []
        self._last_volume: tuple[int, int] = (0, 0)
        #: The indicator only draws when the command has been **silent** for this long.
        #: Measured: drawing while output flows splits the answer's own lines — with the
        #: indicator on, 4 of 240 streamed lines no longer read as one line in the byte
        #: stream (the bytes are all there; the indicator's "\r\x1b[K" lands inside them).
        #: During output the answer *is* the feedback, so silence is where the indicator
        #: belongs — and a spinner over a busy screen is decoration, not information.
        self.quiet_s = ACTIVITY_QUIET_S
        self._last_output_at = 0.0
        self.suppressed = 0

    # -- measurement ------------------------------------------------------
    def attach(self, *tees: Any) -> None:
        """Follow these Tees' counters, so each repaint reports the live volume."""
        self._tees = [tee for tee in tees if tee is not None]

    def live_volume(self) -> tuple[int, int]:
        """``(bytes, lines)`` streamed so far — from the attached Tees when there are any."""
        if self._tees:
            return (
                sum(int(getattr(tee, "bytes_streamed", 0) or 0) for tee in self._tees),
                sum(int(getattr(tee, "lines_streamed", 0) or 0) for tee in self._tees),
            )
        return self.bytes, self.lines

    def note_output(self, *, bytes_streamed: int, lines_streamed: int) -> None:
        """A Tee telling the indicator what it streamed in total."""
        self.bytes = max(0, int(bytes_streamed or 0))
        self.lines = max(0, int(lines_streamed or 0))

    def note_tokens(self, tokens: int | None) -> None:
        """Tokens so far — only when they are *known* (``None`` prints nothing)."""
        self.tokens = None if tokens is None else max(0, int(tokens))

    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        return max(0.0, time.monotonic() - self.started_at)

    @property
    def text(self) -> str:
        """The indicator's one line (built from measurements only).

        The *label* is what gives when the line is too narrow — the elapsed time and the
        volume are the facts, and they keep their place at the end of the line (the same
        rule Codex's indicator follows: the clock never disappears behind a long title).
        """
        streamed, lines = self.live_volume()
        facts = [format_elapsed(self.elapsed())]
        if streamed:
            facts.append(format_size(streamed))
        if lines:
            facts.append(f"{lines} line" + ("" if lines == 1 else "s"))
        if self.tokens is not None:
            facts.append(f"tokens:{format_count(self.tokens)}")
        width = max(20, int(self._width()))
        glyph = self._glyph("activity")
        fact_text = f"({' · '.join(facts)})"
        room = max(8, width - len(glyph) - len(fact_text) - 2)
        label = _slash.short_text(self.label, room, ellipsis=self._glyph("ellipsis"))
        return f"{glyph} {label} {fact_text}".rstrip()

    # -- drawing ----------------------------------------------------------
    def clear(self) -> None:
        """Erase the indicator line (called before any real output reaches the terminal)."""
        if not self.enabled or not self._line_open:
            return
        with self.lock:
            try:
                self.stream.write("\r\x1b[K")
                self.stream.flush()
            except Exception:  # noqa: BLE001 — a closed terminal is not a crash
                pass
            self._line_open = False

    def repaint(self) -> None:
        """Draw the current line, replacing whatever was there."""
        if not self.enabled:
            return
        with self.lock:
            try:
                self.stream.write("\r\x1b[K" + self._paint(self.text, "dim"))
                self.stream.flush()
            except Exception:  # noqa: BLE001
                return
            self._line_open = True
            self.drawn += 1

    def start(self, label: str) -> None:
        """Begin ticking for ``label`` (a no-op indicator is still a real measurement)."""
        self.label = str(label or "").strip()
        self.started_at = time.monotonic()
        self.drawn = 0
        self._stop = threading.Event()
        if not self.enabled:
            return
        self.repaint()
        worker = threading.Thread(target=self._tick, name="skaldir-activity", daemon=True)
        self._thread = worker
        worker.start()

    def should_draw(self, volume: tuple[int, int], now: float | None = None) -> bool:
        """Whether the indicator may draw, given what has really streamed.

        Pure on purpose: the rule is the thing to test, not the thread that obeys it.
        ``False`` while output flows (the indicator would split it), ``True`` once the
        command has been quiet for :attr:`quiet_s` — the state a spinner is actually for.
        """
        moment = time.monotonic() if now is None else float(now)
        if volume != self._last_volume:
            self._last_volume = volume
            self._last_output_at = moment
            return False
        return (moment - self._last_output_at) >= self.quiet_s

    def _tick(self) -> None:
        self._last_volume = self.live_volume()
        self._last_output_at = time.monotonic()
        while not self._stop.wait(self.interval):
            with self._lock:
                if self.should_draw(self.live_volume()):
                    self.repaint()
                else:
                    # the command's own output owns the line: get out of its way
                    self.suppressed += 1
                    self.clear()

    def stop(self) -> None:
        """Stop ticking and leave the terminal clean (the last line is erased)."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=self.interval + 0.2)
            self._thread = None
        self.clear()
        self.started_at = 0.0


# --------------------------------------------------------------------------- #
# session status probes (read-only, mtime-cached, never invented)
# --------------------------------------------------------------------------- #


def _short_path(path: Path) -> str:
    text = str(path)
    home = str(Path.home())
    return text.replace(home, "~", 1) if text.startswith(home) else text


def _unquote(text: str) -> str:
    """One message as the user meant it: a wrapping pair of quotes is punctuation, not content.

    ``/steer "do it differently"`` must store ``do it differently``: the quotes are how the line
    was written at a prompt, not part of the instruction the run is handed. Only a *matching*
    pair is removed, and the inner text is left byte-for-byte alone.
    """
    body = str(text or "").strip()
    for mark in ('"', "'"):
        if len(body) >= 2 and body.startswith(mark) and body.endswith(mark):
            return body[1:-1].strip()
    return body


def _status_label(label: str) -> str:
    """The short form of a probe label for the status line (the banner keeps the long one).

    Only one normalisation, and only when the meaning is unchanged: ``none configured``
    reads as ``none`` on a line that already says what the fact is about.
    """
    text = " ".join(str(label or "").split())
    return text.replace("none configured", "none")


def clip_facts(
    facts: Sequence[str],
    width: int,
    *,
    sep: str = " · ",
    ellipsis: str = "…",
) -> str:
    """Join the status facts, dropping the least relevant until the line fits ``width``.

    ``facts`` is ordered most relevant first; the tail is what goes when the terminal is
    narrow (a provider is less important than the workspace). One fact that is still too
    long is clipped with an ellipsis; no facts at all gives ``""`` — a caller prints
    nothing rather than a bare separator.
    """
    kept = [" ".join(str(item).split()) for item in facts]
    kept = [item for item in kept if item]
    if not kept:
        return ""
    width = max(1, int(width))
    while kept:
        line = sep.join(kept)
        if len(line) <= width:
            return line
        if len(kept) == 1:
            if width <= len(ellipsis):
                return kept[0][:width]
            return kept[0][: width - len(ellipsis)].rstrip() + ellipsis
        kept.pop()
    return ""


@dataclass
class ProbeValue:
    """One probe's answer: a short label, a detail line and its source."""

    label: str
    detail: str = ""
    source: str = ""
    ok: bool = True


class Probes:
    """The runtime / provider / goal facts the prompt and the HUD show."""

    def __init__(self, runtime: Path, *, attach: str = "", session_id: str = "") -> None:
        self.runtime = Path(runtime)
        #: the run this session is attached to (a goal id / run id) — ``""`` means unattached.
        #: The goal fact is scoped by this: a session paints *its own* goal, never whichever
        #: goal happens to be open in the store (see :meth:`goal`).
        self.attach = str(attach or "").strip()
        #: the session id a goal of *this* session would carry (``session_id`` of the goal row).
        self.session_id = str(session_id or "").strip()
        self._cache: dict[str, tuple[float, ProbeValue]] = {}
        self._warm_cache: tuple[float, ProbeValue] | None = None

    def _stat_key(self, *paths: Path) -> float:
        newest = 0.0
        for path in paths:
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                newest = max(newest, 0.0)
        return newest

    def runtime_dir(self) -> ProbeValue:
        return ProbeValue(
            _short_path(self.runtime), str(self.runtime), "registry.default_runtime_dir"
        )

    def provider(self) -> ProbeValue:
        """What the LLM provider layer says — read from the real config, never guessed."""
        try:
            from . import provider_config as pc
        except Exception as exc:  # noqa: BLE001 — no provider layer in this build
            return ProbeValue("unknown", f"provider layer unavailable ({type(exc).__name__})",
                              "provider_config", ok=False)
        try:
            path = Path(pc.config_path())
        except Exception as exc:  # noqa: BLE001
            return ProbeValue("unknown", f"provider config path unresolved ({type(exc).__name__})",
                              "provider_config", ok=False)
        key = self._stat_key(path)
        cached = self._cache.get("provider")
        if cached and cached[0] == key:
            return cached[1]
        value: ProbeValue
        if not path.exists():
            value = ProbeValue("not configured", f"no config at {_short_path(path)}",
                               str(path), ok=False)
        else:
            try:
                store = pc.load_store(path)
                default = str(getattr(store, "default", "") or "")
                names = sorted(getattr(store, "providers", {}) or {})
                if default and default in names:
                    entry = store.providers[default]
                    kind = str(getattr(entry, "kind", "") or "?")
                    # ``ProviderEntry.ready`` is a *method*: reading the attribute used to
                    # hand back a bound method, so the flag was permanently " (not ready)".
                    # The real signal is ``missing()`` — and its first line is the reason,
                    # which is what makes the status line worth reading.
                    missing: list[str] = []
                    checker = getattr(entry, "missing", None)
                    if callable(checker):
                        try:
                            raw = checker()
                        except Exception:  # noqa: BLE001 — a check that cannot run is no verdict
                            raw = []
                        missing = (
                            [str(item) for item in raw] if isinstance(raw, (list, tuple)) else []
                        )
                    flag = " (not ready)" if missing else ""
                    detail = f"{default} · {kind}"
                    if missing:
                        detail = f"{detail} · {missing[0]}"
                    value = ProbeValue(f"{default}{flag}", detail, str(path), ok=True)
                elif names:
                    value = ProbeValue(
                        f"none default ({len(names)} configured)",
                        "no default provider is set — `skaldir provider default NAME`",
                        str(path),
                        ok=False,
                    )
                else:
                    value = ProbeValue("not configured", f"{_short_path(path)} has no providers",
                                       str(path), ok=False)
            except Exception as exc:  # noqa: BLE001 — refused/secret/corrupt config
                value = ProbeValue("unknown", f"{type(exc).__name__}: {exc}", str(path), ok=False)
        self._cache["provider"] = (key, value)
        return value

    def attached_goal(self) -> str:
        """The goal id this session is attached to (``""`` when it is not attached)."""
        return self.attach

    def goal(self) -> ProbeValue:
        """This session's goal — the *attached* run's, or ``none``. Never a foreign one.

        Measured defect (2026-09-19): the chat used to report *any* non-terminal goal in the
        runtime's ``runs.db``, so a fresh chat painted ``final1-i WORKING`` — the abandoned goal
        of another session (lease owner ``pid=3571473``, dead; lease expired 2026-09-18 20:22).
        The store is shared by every process that ever ran against the runtime, so "a goal is
        open" is not the same fact as "this session has a goal". The rule is therefore:

        * attached (``--attach <goal-id>``, or the hand-over from ``skaldir run``): that record,
          or an honest ``none`` when the id is unknown/terminal in this store;
        * otherwise: the goal whose ``session_id`` **is this session's**, or ``none``.

        Other sessions' open goals are *counted* in the detail so the fact is not lost, but they
        are never adopted as this session's goal.
        """
        try:
            from . import runs_store as rs
        except Exception as exc:  # noqa: BLE001 — no PERSIST layer in this build
            return ProbeValue("unknown", f"run store unavailable ({type(exc).__name__})",
                              "runs_store", ok=False)
        try:
            path = Path(rs.run_store_path(self.runtime))
        except Exception as exc:  # noqa: BLE001
            return ProbeValue("unknown", f"run store path unresolved ({type(exc).__name__})",
                              "runs_store", ok=False)
        key = self._stat_key(path)
        cache_key = f"goal:{self.attach}:{self.session_id}"
        cached = self._cache.get(cache_key)
        if cached and cached[0] == key:
            return cached[1]
        value: ProbeValue
        if not path.exists():
            value = ProbeValue("none", f"no run store at {_short_path(path)}", str(path), ok=False)
        else:
            try:
                store = rs.RunsStore(path)
                try:
                    value = self._goal_value(store)
                finally:
                    store.close()
            except Exception as exc:  # noqa: BLE001 — locked/corrupt/unmigrated store
                value = ProbeValue("unknown", f"{type(exc).__name__}: {exc}", str(path), ok=False)
        self._cache[cache_key] = (key, value)
        return value

    def _goal_value(self, store: Any) -> ProbeValue:
        """The scoped goal fact (see :meth:`goal`): one store, one answer, no adoption."""
        path = str(getattr(store, "path", "") or "")
        others = [item for item in store.goals(include_terminal=False)]
        if self.attach:
            record = store.goal(self.attach)
            if record is None:
                return ProbeValue(
                    "none",
                    f"the attached run {self.attach[:20]} is not in {_short_path(Path(path))}",
                    path,
                    ok=False,
                )
            return ProbeValue(
                f"{record.goal_id[:8]} {record.state}",
                f"attached run · revision {record.revision}"
                + (f" · {record.goal_text[:48]}" if record.goal_text else ""),
                path,
                ok=not record.terminal,
            )
        record = store.goal_for_session(self.session_id) if self.session_id else None
        if record is None:
            foreign = len([item for item in others if item.goal_id != self.attach])
            detail = "no run is attached to this session"
            if foreign:
                detail += (f" · {foreign} open goal(s) belong to other sessions "
                           "(not this chat's)")
            return ProbeValue("none", detail, path, ok=False)
        return ProbeValue(
            f"{record.goal_id[:8]} {record.state}",
            f"this session's goal · revision {record.revision}",
            path,
            ok=not record.terminal,
        )

    def warm(self) -> ProbeValue:
        """Is a warm session answering? Capped and cached — the prompt never waits on it.

        The probe is a real ``warm_session.status_of(...)`` with a short timeout, and its
        answer is reused for :data:`WARM_PROBE_TTL_S` seconds, so a status line costs
        nothing per keystroke. ``ok`` means a session really answered; the status line
        only shows the fact when it does (a session that is off is not news).
        """
        now = time.monotonic()
        cached = self._warm_cache
        if cached is not None and (now - cached[0]) < WARM_PROBE_TTL_S:
            return cached[1]
        value = self._probe_warm()
        self._warm_cache = (now, value)
        return value

    @staticmethod
    def _probe_warm() -> ProbeValue:
        try:
            from . import warm_session as ws
        except Exception as exc:  # noqa: BLE001 — no warm layer in this build
            return ProbeValue("unknown", f"warm layer unavailable ({type(exc).__name__})",
                              "warm_session", ok=False)
        try:
            status = ws.status_of(timeout=WARM_PROBE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — a probe that cannot run is reported as such
            return ProbeValue("unknown", f"{type(exc).__name__}: {exc}", "warm_session",
                              ok=False)
        running = bool(getattr(status, "running", False))
        note = str(getattr(status, "note", "") or getattr(status, "state", "") or "")
        pid = int(getattr(status, "pid", 0) or 0)
        detail = note + (f" (pid {pid})" if pid else "")
        return ProbeValue("on" if running else "off", detail.strip(), "warm_session",
                          ok=running)


# --------------------------------------------------------------------------- #
# history (persisted, so ``--resume`` restores real state)
# --------------------------------------------------------------------------- #


@dataclass
class HistoryEntry:
    ts: str
    line: str
    argv: list[str] = field(default_factory=list)
    exit_code: int = EXIT_OK
    cancelled: bool = False
    duration_ms: int = 0
    source: str = "command"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "line": self.line,
            "argv": list(self.argv),
            "exit_code": int(self.exit_code),
            "cancelled": bool(self.cancelled),
            "duration_ms": int(self.duration_ms),
            "source": self.source,
        }


class History:
    """Append-only JSONL history under ``<runtime>/shell/history.jsonl``."""

    def __init__(self, runtime: Path, name: str = _HISTORY_FILE) -> None:
        self.path = Path(runtime) / _HISTORY_DIR / name
        self.entries: list[HistoryEntry] = []

    def load(self) -> list[HistoryEntry]:
        self.entries = []
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return self.entries
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            self.entries.append(
                HistoryEntry(
                    ts=str(record.get("ts", "")),
                    line=str(record.get("line", "")),
                    argv=[str(item) for item in record.get("argv", []) or []],
                    exit_code=int(record.get("exit_code", EXIT_OK) or 0),
                    cancelled=bool(record.get("cancelled")),
                    duration_ms=int(record.get("duration_ms", 0) or 0),
                    source=str(record.get("source", "command")),
                )
            )
        return self.entries

    def append(self, entry: HistoryEntry) -> None:
        self.entries.append(entry)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.as_dict(), ensure_ascii=False) + "\n")
        except OSError:
            pass  # a read-only runtime is not a reason to lose the session

    def tail(self, limit: int = 10) -> list[HistoryEntry]:
        return self.entries[-limit:] if limit else list(self.entries)


# --------------------------------------------------------------------------- #
# one command's run
# --------------------------------------------------------------------------- #


@contextlib.contextmanager
def _no_frame_marker() -> Any:
    """The no-op marker for a build without ``skaldir.chatbar``."""
    yield


@dataclass
class RunResult:
    """What one command really did."""

    argv: tuple[str, ...]
    exit_code: int = EXIT_OK
    cancelled: bool = False
    duration_ms: int = 0
    #: milliseconds from "go" to the command's first byte reaching the terminal (-1 = none)
    first_byte_ms: int = -1
    output: str = ""
    unexpected: str = ""
    #: measured by the Tee that carried the output (0 when the command printed nothing)
    bytes_streamed: int = 0
    lines_streamed: int = 0
    #: the token count the command's own envelope reported, when it reported one
    tokens_reported: int | None = None
    #: the paced-render measurements (all zero when this session runs unpaced)
    coalesced: bool = False
    renders: int = 0
    renders_per_s: float = 0.0
    chunks_per_render: float = 0.0
    #: milliseconds from "go" to the last byte reaching the terminal (-1 = none)
    last_byte_ms: int = -1
    #: this process's CPU (user+system) spent inside the command — the syscall-saving lever
    cpu_ms: int = 0

    @property
    def ok(self) -> bool:
        return not self.cancelled and self.exit_code == EXIT_OK

    def verdict(self) -> str:
        if self.cancelled:
            return "cancelled"
        name = ""
        if _CONTRACT is not None:
            try:
                name = _CONTRACT.EXIT_NAMES.get(self.exit_code, "")
            except Exception:  # noqa: BLE001
                name = ""
        return f"exit {self.exit_code}" + (f" ({name})" if name else "")

    def footer(self, sep: str = " · ", *, streamed_above_ms: int = 200) -> str:
        """The one quiet result line: verdict, wall time, and — only for a slow command —
        when the first output arrived (that is the streaming evidence, not decoration).

        The measured volume of what streamed is appended when there was any: a fact a
        reader can check against the output above it, never an estimate.
        """
        parts = [self.verdict(), f"{self.duration_ms} ms"]
        if self.first_byte_ms >= 0 and self.duration_ms >= streamed_above_ms:
            parts.insert(1, f"first output at {self.first_byte_ms} ms")
        if self.bytes_streamed:
            volume = format_size(self.bytes_streamed)
            parts.append(f"{volume} streamed")
        if self.tokens_reported is not None:
            parts.append(f"tokens:{format_count(self.tokens_reported)} (reported)")
        return sep.join(parts)


@dataclass
class Captured:
    """A command run with its output kept out of the terminal (a probe, not a turn).

    Used by the two gates: ``approval check --dry-run`` and ``edit dry-run`` print the
    product's own JSON envelope, and the shell reads that envelope instead of asking the
    engine (or the edit layer) a second time in its own words.
    """

    argv: tuple[str, ...]
    exit_code: int = EXIT_OK
    output: str = ""
    envelope: dict[str, Any] | None = None

    @property
    def data(self) -> dict[str, Any]:
        data = (self.envelope or {}).get("data")
        return data if isinstance(data, dict) else {}


def envelope_of(text: str) -> dict[str, Any] | None:
    """The last product envelope in a command's output (one JSON object per command).

    The CLI prints exactly one envelope per call, but a command may also print human text
    first (``edit show --list`` does), so every line is tried and the last object that
    carries the envelope's own keys wins. ``None`` means "no envelope here" — never a
    guess from a partial line.
    """
    found: dict[str, Any] | None = None
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and "schema_version" in record and "ok" in record:
            found = record
    return found


def default_runner(argv: Sequence[str]) -> int:
    """Run one command through the product CLI (the one entry point, no shortcut)."""
    from .cli import main as cli_main

    return int(cli_main(list(argv)))


# --------------------------------------------------------------------------- #
# the two gates in front of the runner: the approval ladder, the diff preview
# --------------------------------------------------------------------------- #

#: The invocation context flags a gated command may carry, and the CLI spelling of each.
#: Passed through to every real command the gate runs, so the judgement and the run agree
#: about which runtime, profile, workspace and session they are talking about.
CONTEXT_FLAGS: tuple[tuple[str, str], ...] = (
    ("runtime", "--runtime"),
    ("profile", "--profile"),
    ("workspace", "--workspace"),
    ("session", "--session"),
)

#: The dotted command paths this shell gates. ``approval run`` is the enforcement path
#: (judge, then run inside the profile); ``edit apply`` is the path that writes files.
APPROVAL_PATHS: tuple[str, ...] = ("approval.run",)
EDIT_APPLY_PATHS: tuple[str, ...] = ("edit.apply",)

_PARSER_CACHE: Any = None


def real_parser() -> Any:
    """The product parser, built once (the shell already builds it for the palette)."""
    global _PARSER_CACHE
    if _PARSER_CACHE is None:
        from .cli import build_parser

        _PARSER_CACHE = build_parser()
    return _PARSER_CACHE


def parse_invocation(argv: Sequence[str]) -> Any | None:
    """Parse ``argv`` with the **real** parser; ``None`` when it refuses the line.

    A line the parser rejects is not gated at all: the real command runs and prints its
    own usage error, which is the answer the user should get.
    """
    if any(str(item) in ("-h", "--help") for item in argv):
        return None  # --help is its own answer; a gate must not swallow it
    try:
        return real_parser().parse_args([str(item) for item in argv])
    except SystemExit:
        return None
    except Exception:  # noqa: BLE001 — an unparsable line is the real run's business
        return None


@dataclass(frozen=True)
class GatedCommand:
    """One invocation the shell must judge (or preview) before it lets it run.

    Everything here comes from the real parser's own answer: ``path`` is the leaf's
    ``command_path``, ``tokens`` is the ``REMAINDER`` the handler will join, ``context`` is
    the flags the invocation named. Nothing is re-derived with a second tokenizer.
    """

    path: str
    tokens: tuple[str, ...]
    argv: tuple[str, ...]
    context: tuple[tuple[str, str], ...] = ()
    dry_run: bool = False
    session: str = ""

    @property
    def command(self) -> str:
        """The command string the ladder judges (one space-joined token list)."""
        return " ".join(self.tokens)

    def flags(self, *, omit: Sequence[str] = ()) -> list[str]:
        """The context flags as argv (``["--runtime", "/tmp/x", …]``), minus ``omit``."""
        out: list[str] = []
        skip = set(omit)
        for _name, flag in CONTEXT_FLAGS:
            if flag in skip:
                continue
            value = self.context_value(flag)
            if value:
                out += [flag, value]
        return out

    def context_value(self, flag: str) -> str:
        for name, spelled in CONTEXT_FLAGS:
            if spelled == flag:
                return dict(self.context).get(name, "")
        return ""


def gated_command(argv: Sequence[str], paths: Sequence[str]) -> GatedCommand | None:
    """The command a gated invocation would run — or ``None`` when it is not gated.

    ``paths`` are dotted ``command_path`` values (``approval.run``): the parser decides
    what the line means, so ``/approval run …`` and ``approval run …`` gate identically.
    """
    args = parse_invocation(argv)
    if args is None:
        return None
    if str(getattr(args, "command_path", "") or "") not in set(paths):
        return None
    raw = getattr(args, "command", None)
    tokens = tuple(str(item) for item in raw) if isinstance(raw, (list, tuple)) else ()
    context = tuple(
        (name, str(getattr(args, name, "") or ""))
        for name, _flag in CONTEXT_FLAGS
        if str(getattr(args, name, "") or "")
    )
    return GatedCommand(
        path=str(getattr(args, "command_path", "")),
        tokens=tokens,
        argv=tuple(str(item) for item in argv),
        context=context,
        dry_run=bool(getattr(args, "dry_run", False)),
        session=str(getattr(args, "session", "") or ""),
    )


def with_flag(argv: Sequence[str], flag: str, value: str) -> tuple[str, ...]:
    """Insert ``flag value`` right after the command's verb (before the REMAINDER).

    ``approval run`` reads its command with ``argparse.REMAINDER``, so a flag after the
    first command token would be handed to the command itself. The verb is therefore
    ``argv[1]`` and the flags go between it and the command — measured, not assumed.
    """
    items = [str(item) for item in argv]
    if len(items) < 2:
        return tuple(items)
    if flag in items:
        return tuple(items)  # the caller already named it; their value wins
    return tuple([items[0], items[1], flag, str(value), *items[2:]])


#: Keys a command's own envelope uses for a token count (any of them, at any depth).
_TOKEN_KEYS: tuple[str, ...] = ("total_tokens", "tokens")

#: How deep :func:`tokens_reported_in` looks inside an envelope before it gives up.
_TOKEN_SCAN_DEPTH = 4


def _find_token_count(value: Any, depth: int = 0) -> int | None:
    if depth > _TOKEN_SCAN_DEPTH:
        return None
    if isinstance(value, dict):
        for key in _TOKEN_KEYS:
            found = value.get(key)
            if isinstance(found, bool):
                continue
            if isinstance(found, int):
                return found
            if isinstance(found, float):
                return int(found)
        for item in value.values():
            found = _find_token_count(item, depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_token_count(item, depth + 1)
            if found is not None:
                return found
    return None


def tokens_reported_in(text: str) -> int | None:
    """The token count a command's own envelope reported — ``None`` when it reported none.

    The one honest source of a live token fact this build has: Skaldir commands report
    usage in their payload (``agent run``, ``codectx …``), and the footer shows that number
    labelled ``(reported)``. Nothing estimates a token count here.
    """
    record = envelope_of(text)
    if record is None:
        return None
    return _find_token_count({"data": record.get("data")})


#: The words the approval prompt accepts, mapped onto the ladder's vocabulary. Codex walks
#: a list of sentences; a line-based shell takes a key — but a person may still type the
#: word, so both are accepted and anything unknown is *not* an answer.
APPROVAL_WORDS: dict[str, str] = {
    "a": "once", "allow": "once", "yes": "once", "y": "once", "once": "once",
    "s": "session", "session": "session",
    "r": "rule", "rule": "rule", "always": "rule", "policy": "rule",
    "d": "deny", "deny": "deny", "no": "deny", "n": "deny",
    "q": "abort", "abort": "abort", "quit": "abort",
}


def approval_answer(text: str) -> str | None:
    """Map a typed answer onto ``once`` / ``session`` / ``rule`` / ``deny`` / ``abort``.

    An empty answer is a denial (silence is not consent, the ladder's own rule); anything
    the vocabulary does not know returns ``None`` so the caller can ask again instead of
    guessing what the user meant.
    """
    word = str(text or "").strip().lower()
    if not word:
        return "deny"
    return APPROVAL_WORDS.get(word.split()[0])


# --------------------------------------------------------------------------- #
# /init: the project guide, generated from what the repository really says
# --------------------------------------------------------------------------- #

#: Files ``/init`` reads. Only a file that exists contributes a line — the generator says
#: what it saw, and says nothing about what it did not.
_INIT_READS: tuple[str, ...] = ("pyproject.toml", "package.json", "Makefile", "README.md",
                               "setup.cfg", "tox.ini", "pytest.ini")


def _read_text(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _toml_section(text: str, header: str) -> dict[str, str]:
    """The simple ``key = "value"`` pairs of one TOML section (no dependencies).

    Enough for the few facts the guide states (project name, requires-python, a console
    script, the pytest config); anything more structured is left to a real parser and
    therefore *not* stated here.
    """
    out: dict[str, str] = {}
    current = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line.strip("[]").strip()
            continue
        if current != header or "=" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _git_branch(root: Path) -> str:
    """The current branch, read from ``.git/HEAD`` (no subprocess, no guessing)."""
    head = _read_text(root / ".git" / "HEAD", 4096).strip()
    if head.startswith("ref: refs/heads/"):
        return head[len("ref: refs/heads/"):].strip()
    return ""


def _repo_layout(root: Path, limit: int = 8) -> tuple[list[str], list[str]]:
    """The top-level directories and the source/test directories that really exist."""
    directories: list[str] = []
    try:
        entries = sorted(iter(root.iterdir()), key=lambda item: item.name)
    except OSError:
        return [], []
    for entry in entries:
        name = entry.name
        if name.startswith(".") or name in {"node_modules", "venv"}:
            continue
        if entry.is_dir() and len(directories) < limit:
            directories.append(name + "/")
    known = [name for name in ("src", "tests", "test", "docs", "scripts", "assets", "tools")
             if (root / name).is_dir()]
    return directories, known


def _test_command(root: Path) -> str:
    """The test command this repository's own files declare (never an invented one)."""
    if (root / "pyproject.toml").is_file() or (root / "pytest.ini").is_file() \
            or (root / "setup.cfg").is_file() or (root / "tox.ini").is_file():
        if (root / ".venv" / "bin" / "python").exists():
            return ".venv/bin/python -m pytest tests -q -o addopts=\"\""
        return "python -m pytest tests -q"
    package = _read_text(root / "package.json", 40_000)
    if package and '"test"' in package:
        return "npm test"
    if (root / "Makefile").is_file():
        return "make test"
    return ""


def agents_md_for(root: Path) -> str:
    """The ``AGENTS.md`` body for one repository — every line read, none invented.

    Codex's ``/init`` sends a fixed prompt to the model and lets it write the file; this
    generator produces the same *kind* of document deterministically from the repository
    itself (layout, declared commands, the test runner, the git branch), so the result is
    reviewable line by line and works with no provider configured. Anything the repository
    does not state is simply absent, with one line saying so.
    """
    root = Path(root)
    pyproject = _read_text(root / "pyproject.toml")
    project = _toml_section(pyproject, "project")
    poetry = _toml_section(pyproject, "tool.poetry")
    name = project.get("name") or poetry.get("name") or root.name
    requires = project.get("requires-python") or poetry.get("python") or ""
    description = project.get("description") or ""
    directories, known = _repo_layout(root)
    branch = _git_branch(root)
    test_command = _test_command(root)
    lines: list[str] = [f"# {name} — repository guidelines", ""]
    if description:
        lines += ["## What this repository is", "", description, ""]
    lines += ["## Layout", ""]
    if directories:
        lines.append("Top level: " + ", ".join(f"`{item}`" for item in directories))
    if known:
        lines.append("")
        for item in known:
            hint = {
                "src": "the importable package(s) live here",
                "tests": "the tests live here; they mirror the package's module names",
                "docs": "the documentation lives here",
                "scripts": "operational scripts live here",
                "tools": "developer tooling lives here",
                "assets": "static assets live here",
            }.get(item, "")
            lines.append(f"- `{item}/` — {hint}" if hint else f"- `{item}/`")
    lines.append("")
    lines += ["## Running the work", ""]
    if test_command:
        lines += ["```", test_command + "   # the test suite", "```", ""]
    lines += ["The commands above are the ones this repository's own files declare; run them "
              "from the repository root.", ""]
    lines += ["## Conventions", ""]
    if requires:
        lines += [f"- Python `{requires}` is what the project declares.",]
    if branch:
        lines += [f"- The checked-out branch is `{branch}` (from `.git/HEAD`)."]
    lines += ["- Keep the diff small and reviewable: one intent per change.",
              "- Run the test suite before calling a change done; a claim without the "
              "output is not evidence.",
              "- Do not overwrite or delete files this guide does not mention without "
              "asking first.",
              ""]
    lines += ["## Notes for agents", ""]
    readable = len([1 for f in _INIT_READS if (root / f).is_file()])
    lines += [f"- This file was generated by `skaldir /init` from {readable} "
              f"file(s) it could read in this repository; edit it freely — it is a normal "
              f"tracked file.",
              "- If a command above does not work, fix this file in the same change: a "
              "wrong command here is worse than no command.",
              ""]
    return "\n".join(lines)


class Session:
    """The long-lived state: runtime, tree, probes, history and the command runner."""

    def __init__(
        self,
        *,
        runtime: str | os.PathLike[str] | None = None,
        runner: Callable[[Sequence[str]], int] | None = None,
        history: History | None = None,
        out: Any = None,
        err: Any = None,
        attach: str = "",
        session_id: str = "",
    ) -> None:
        self.runtime = Path(runtime) if runtime else _default_runtime()
        self.runner = runner or default_runner
        self.history = history or History(self.runtime)
        self.out = out if out is not None else sys.stdout
        self.err = err if err is not None else sys.stderr
        self.session_id = str(session_id or "")
        # The probes are scoped to *this* session: `attach` is the run the session is the surface
        # of, `session_id` is what one of its own goals would carry. Without the two, the goal
        # fact would adopt whatever goal happens to be open in the shared runtime store.
        self.probes = Probes(self.runtime, attach=attach, session_id=self.session_id)
        self._tree: Any = None
        self.tree_error = ""
        self.report: dict | None = None
        self.started_at = _now_iso()
        self.exit_codes: list[int] = []
        #: the swallowed Ctrl-C of the last command (a caller may want to re-raise/say so)
        self.last_interrupt: KeyboardInterrupt | None = None
        #: the streaming clock (set by the shell; ``None`` means no indicator at all)
        self.activity: Activity | None = None
        #: render bound for the terminal, in renders/second (0 = every chunk goes straight
        #: out, the pre-coalescing behaviour). The shell sets it; a Session used directly
        #: (a test, a script) stays unpaced unless it asks.
        self.stream_hz: float = 0.0
        #: session-level streaming totals (a measurement of the whole session, not one run)
        self.renders = 0
        self.streamed_chunks = 0
        self.stream_cpu_ms = 0

    def note_tokens(self, tokens: int | None) -> None:
        """Tokens so far, when they are *known* — the one hook a token source plugs into.

        A caller that really counts tokens (a streaming model client) can call this while
        a command runs; nothing here ever invents a number, and with no caller the
        indicator simply does not show a token fact.
        """
        if self.activity is not None:
            self.activity.note_tokens(tokens)

    def capture(self, argv: Sequence[str]) -> Captured:
        """Run one command with its output **kept out of the terminal** and read its JSON.

        The gates need a verdict (``approval check --dry-run``) or a plan (``edit
        dry-run``) before the real command runs. Both are real CLI calls through the same
        runner — the parser parses, the handler runs, the envelope comes back — but the
        output is captured instead of tee'd, so a probe never looks like a turn.
        """
        argv = tuple(str(item) for item in argv)
        buffer = io.StringIO()
        code = EXIT_OK
        try:
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                code = int(self.runner(argv))
        except SystemExit as exc:  # argparse refused the probe: report its code
            code = int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
        except KeyboardInterrupt:
            code = EXIT_CANCELLED
        except Exception as exc:  # noqa: BLE001 — a probe that crashes is reported, not hidden
            buffer.write(f"{type(exc).__name__}: {exc}\n")
            code = EXIT_INTERNAL
        text = buffer.getvalue()
        return Captured(argv=argv, exit_code=code, output=text, envelope=envelope_of(text))

    # -- the real parser, lazily (a build without cli.py can still render probes) --
    @property
    def tree(self) -> Any:
        if self._tree is None:
            try:
                from .cli import build_parser

                self._tree = _complete.build_tree(build_parser())
                self.tree_error = ""
            except Exception as exc:  # noqa: BLE001 — no CLI to complete against
                self.tree_error = f"{type(exc).__name__}: {exc}"
                self._tree = _complete.Node(path="", name="skaldir")
        return self._tree

    def probe_layers(self) -> dict | None:
        """The real ``selftest`` report (read-only) — the only source of "ready" here."""
        if self.report is not None:
            return self.report
        if _CONTRACT is None:
            return None
        try:
            self.report = _CONTRACT.run_selftest(self.runtime)
        except Exception:  # noqa: BLE001 — a probe that cannot run is reported as such
            self.report = None
        return self.report

    def reachable(self) -> dict[str, bool] | None:
        report = self.report
        if report is None:
            return None
        return {
            str(record.get("layer", "")).upper(): bool(record.get("ok"))
            for record in report.get("layers", [])
        }

    def execute(self, argv: Sequence[str]) -> RunResult:
        """Run one argv through the real CLI, streaming its output and never dying.

        Streaming is real, not buffered: the command's writes go straight to the terminal
        through :class:`Tee` (which also records them), and the result carries the measured
        time to the first byte plus the measured volume that streamed. A Ctrl-C anywhere
        inside the command cancels *the command* (``cancelled`` + :data:`EXIT_CANCELLED`);
        the session object stays usable.

        While it runs, the session's :class:`Activity` ticks: it is cleared before every
        write so the command's own output owns the line, and erased when the command ends.

        With ``stream_hz`` set, the terminal is **paced**: every chunk is still recorded and
        counted here, but the coalescer renders at most ``stream_hz`` writes a second. The
        activity line is cleared by the coalescer just before a render, so the two still
        never interleave — and the tail is always flushed, so the bound can never swallow
        the last bytes of a command.
        """
        argv = tuple(str(item) for item in argv)
        sink = io.StringIO()
        started = time.monotonic()
        cpu_at_start = time.process_time()
        code = EXIT_OK
        cancelled = False
        unexpected = ""
        last_interrupt: KeyboardInterrupt | None = None
        primary_out = self.out
        primary_err = self.err
        activity = self.activity
        hz = float(getattr(self, "stream_hz", 0.0) or 0.0)
        paced = hz > 0

        def before_render(_metrics: Any) -> None:
            # the indicator and the stream share one line: the stream wins
            if activity is not None:
                activity.clear()

        # one terminal, one writer at a time: the activity thread and the command's output
        # share the indicator's lock, or the two garble each other's lines (measured)
        terminal_lock = getattr(activity, "lock", None)
        if terminal_lock is not None:
            primary_out = _LockedStream(primary_out, terminal_lock)
            primary_err = _LockedStream(primary_err, terminal_lock)
        # One terminal also has one write *order*. When stdout and stderr address the same
        # tty they share one coalescer, so the program's own ordering survives the bound —
        # two coalescers let stderr's last line overtake stdout's paced tail (measured).
        shared = _same_terminal(primary_out, primary_err)
        if paced and shared:
            one = _Coalescer(primary_out, hz=hz, before_render=before_render)
            tee_out = Tee(primary_out, sink, activity=activity, coalesce=one)
            tee_err = Tee(primary_err, sink, activity=activity, coalesce=one)
        else:
            tee_out = Tee(primary_out, sink, activity=activity,
                          coalesce=_Coalescer(primary_out, hz=hz, before_render=before_render)
                          if paced else None)
            tee_err = Tee(primary_err, sink, activity=activity,
                          coalesce=_Coalescer(primary_err, hz=hz, before_render=before_render)
                          if paced else None)
        label = f"running skaldir {' '.join(argv)}"
        if activity is not None:
            activity.attach(tee_out, tee_err)
            activity.start(label)
        try:
            with contextlib.redirect_stdout(tee_out), contextlib.redirect_stderr(tee_err):
                code = int(self.runner(argv))
        except KeyboardInterrupt as exc:
            cancelled = True
            code = EXIT_CANCELLED
            last_interrupt = exc
        except SystemExit as exc:  # argparse --help / a handler that exits
            code = int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
        except Exception as exc:  # noqa: BLE001 — a crashed command is reported, not hidden
            unexpected = f"{type(exc).__name__}: {exc}"
            code = EXIT_INTERNAL
        # the tail always goes out: the render bound is a pacing rule, never a truncation
        tee_out.finish()
        tee_err.finish()
        duration = int((time.monotonic() - started) * 1000)
        cpu_ms = max(0, int((time.process_time() - cpu_at_start) * 1000))
        stamps = [t for t in (tee_out.first_write_at, tee_err.first_write_at) if t]
        unstamped = -1 if not stamps else max(0, int((min(stamps) - started) * 1000))
        metrics: list[Any] = []
        for candidate in (tee_out.metrics(), tee_err.metrics()):
            if candidate is not None and not any(candidate is seen for seen in metrics):
                metrics.append(candidate)
        renders = sum(int(m.renders) for m in metrics)
        firsts = [int(m.first_render_ms) for m in metrics if int(m.first_render_ms) >= 0]
        lasts = [int(m.last_render_ms) for m in metrics if int(m.last_render_ms) >= 0]
        chunks = sum(int(m.chunks_in) for m in metrics)
        # with the terminal paced, "the first byte" is the first *render*, not the first
        # chunk handed to us — reporting the latter would flatter the number
        first_ms = min(firsts) if firsts else unstamped
        last_ms = max(lasts) if lasts else -1
        output = sink.getvalue()
        if activity is not None:
            activity.note_output(
                bytes_streamed=tee_out.bytes_streamed + tee_err.bytes_streamed,
                lines_streamed=tee_out.lines_streamed + tee_err.lines_streamed,
            )
            activity.stop()
        result = RunResult(
            argv=argv,
            exit_code=code,
            cancelled=cancelled,
            duration_ms=duration,
            first_byte_ms=first_ms,
            output=output,
            unexpected=unexpected,
            bytes_streamed=tee_out.bytes_streamed + tee_err.bytes_streamed,
            lines_streamed=tee_out.lines_streamed + tee_err.lines_streamed,
            tokens_reported=tokens_reported_in(output),
            coalesced=bool(paced and metrics),
            renders=renders,
            renders_per_s=round(renders / (duration / 1000.0), 1) if duration > 0 else 0.0,
            chunks_per_render=round(chunks / renders, 1) if renders else 0.0,
            last_byte_ms=last_ms,
            cpu_ms=cpu_ms,
        )
        self.last_interrupt = last_interrupt
        self.exit_codes.append(code)
        self.renders = getattr(self, "renders", 0) + renders
        self.streamed_chunks = getattr(self, "streamed_chunks", 0) + chunks
        self.stream_cpu_ms = getattr(self, "stream_cpu_ms", 0) + cpu_ms
        return result


def _default_runtime() -> Path:
    try:
        from .registry import default_runtime_dir

        return Path(default_runtime_dir())
    except Exception:  # noqa: BLE001
        from .env import host_getenv  # local: the fallback path imports as little as it can

        hermes = host_getenv("HERMES_HOME") or (Path.home() / ".hermes")
        try:
            from .registry import runtime_dir_for

            return runtime_dir_for(hermes)
        except Exception:  # noqa: BLE001 — standalone use has no registry
            return Path(hermes) / "skaldir"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# the shell
# --------------------------------------------------------------------------- #


@dataclass
class ShellOptions:
    runtime: str | None = None
    resume: bool = False
    echo: bool | None = None
    transcript: str | None = None
    limit: int = 0
    color: bool = False
    no_color: bool = False
    probe: bool = True
    interactive: bool | None = None
    quiet: bool = False
    #: the two gates (on by default; a JSON/quiet run turns both off — a prompt has no
    #: place in a machine-readable stream)
    approval: bool = True
    preview: bool = True
    #: the footer under a turn (the session facts line) and the streaming indicator
    footer: bool = True
    activity: bool = True
    #: how often the activity clock redraws, in hertz (``None`` = the 0.5 s default). A
    #: documented knob, not a look: at 8 Hz even a half-second command shows its ticks
    activity_hz: float | None = None
    #: the terminal's render bound in renders/second: ``None`` decides it at run time
    #: (on for an interactive terminal, off for a pipe), ``0`` never paces, ``>0`` is it.
    stream_hz: float | None = None
    #: the session id grants are filed under ("allow for this session" persists per id)
    session_id: str = ""
    #: the attached run's progress feed: an object with ``pending() -> list[str]`` (and, when it
    #: is a live run, ``stop(reason)``/``done``). The shell writes its lines from its own thread.
    feed: Any = None
    #: the run this session is the surface of (a goal id from ``skaldir run``'s hand-over, or
    #: ``--attach <goal-id>``). ``""`` means unattached: the goal fact then says `none` instead of
    #: adopting a goal another session left open in the shared runtime store.
    attach: str = ""


def _tolerant_stdin() -> Any:
    """``sys.stdin`` with invalid UTF-8 replaced instead of raised (R-2).

    A piped byte that is not valid UTF-8 (``printf 'hello\\xff\\xfe\\x81 world\\n'``)
    used to crash ``skaldir chat`` mid-``readline()`` with a UnicodeDecodeError
    traceback (rc 7). The wrapper decodes with ``errors="replace"`` so the byte
    becomes U+FFFD in the frame and the session keeps going.
    """
    stdin = sys.stdin
    try:
        if getattr(stdin, "isatty", lambda: False)():  # a terminal already decodes safely
            return stdin
        encoding = getattr(stdin, "encoding", None) or "utf-8"
        return io.TextIOWrapper(
            stdin.buffer, encoding=encoding, errors="replace", newline=None
        )
    except (AttributeError, OSError):
        return stdin


class Shell:
    """The read-eval-print loop over the real CLI."""

    def __init__(
        self,
        options: ShellOptions | None = None,
        *,
        session: Session | None = None,
        runner: Callable[[Sequence[str]], int] | None = None,
        out: Any = None,
        err: Any = None,
        stdin: Any = None,
    ) -> None:
        self.options = options or ShellOptions()
        self.stdin = stdin if stdin is not None else _tolerant_stdin()
        self.out = out if out is not None else sys.stdout
        self.err = err if err is not None else sys.stderr
        probe_session_id = str(self.options.session_id or f"shell-{os.getpid()}")
        self.session = session or Session(
            runtime=self.options.runtime, runner=runner, out=self.out, err=self.err,
            attach=str(getattr(self.options, "attach", "") or ""),
            session_id=probe_session_id,
        )
        interactive = self.options.interactive
        if interactive is None:
            interactive = bool(getattr(self.stdin, "isatty", lambda: False)()) and bool(
                getattr(self.out, "isatty", lambda: False)()
            )
        self.interactive = bool(interactive)
        # the live-desktop approve bar opens in *this* shell (cut.install_gate)
        try:
            from . import cut as _cut

            _cut.install_gate(self)
        except Exception:  # noqa: BLE001 — a build without CU keeps the refusal
            pass
        if self.options.echo is None:
            # a scripted session (stdin is a pipe) echoes prompt + line so the
            # transcript reads like the session really did
            self.echo = not self.interactive
        else:
            self.echo = bool(self.options.echo)
        self.paint = Painter(
            color=color_enabled(
                self.out,
                forced=bool(self.options.color),
                no_color=bool(self.options.no_color),
            ),
            stream=self.out,
            no_color=bool(self.options.no_color),
        )
        self.completer = _complete.LineCompleter(
            self.session.tree,
            slash=self._slash_suggestions,
        )
        self.theme = self._detect_theme()
        self.menu_state = _slash.PaletteMenu()
        self.history = self.session.history
        self.transcript: list[str] = []
        self.commands = 0
        self.cancelled = 0
        self.failures = 0
        self.last_exit: int | None = None
        #: the last command's own result (the footer reads what it reported)
        self.last_result: RunResult | None = None
        self.lines_written = 0
        self._quit_requested = False
        #: the status line last drawn — the line is re-printed between commands only when
        #: it really changed (a repeated identical line is noise, not status)
        self._status_shown = ""
        #: the footer's material identity last drawn (same rule: only a footer with news
        #: gets a line — a counter ticking is not news)
        self._footer_shown = ""
        #: did the turn that just ended print anything? (one blank line between turns)
        self._turn_printed = False
        #: is readline driving this session? (a painted prompt needs its markers)
        self.readline_ready = False
        #: the session id `approval allow --scope session` files grants under, so "for this
        #: session" survives into the next command (a fresh shell is a fresh id)
        self.session_id = str(self.options.session_id or f"shell-{os.getpid()}")
        # …and the goal fact is scoped to *this* session: the run it is attached to, plus the id
        # one of its own goals would carry. Without this the facts would adopt whatever goal the
        # shared runtime store happens to hold (the measured `final1-i WORKING` leak).
        probes = getattr(self.session, "probes", None)
        if probes is not None:
            probes.attach = str(getattr(self.options, "attach", "") or "")
            probes.session_id = str(self.session_id or "")
        #: the attached run's progress feed (an object with ``pending()``), polled between
        #: keystrokes and written by *this* thread only — the frame has exactly one writer.
        self.feed = getattr(self.options, "feed", None)
        #: one-shot grants the last "allow once" created, removed when its run ended
        self._oneshot_grants: list[str] = []
        #: the number of approval prompts answered in this session (a measured count)
        self.approvals = 0
        #: the last edit transaction the footer saw (tx_id, verdict, files) — a read-only probe
        self._edit_state: tuple[float, tuple[dict[str, Any], ...]] | None = None
        #: the streaming clock. Only a real terminal gets the ticking line; the measurement
        #: (bytes/lines) happens either way.
        self.session.activity = None
        if self.options.activity:
            hz = self.options.activity_hz
            interval = ACTIVITY_INTERVAL_S if not hz else max(0.02, 1.0 / max(0.1, float(hz)))
            self.session.activity = Activity(
                self.out,
                enabled=bool(self.interactive),
                interval=interval,
                paint=self.paint,
                glyphs=self.glyph,
                width=self.line_width,
            )
        #: the render bound. On by default **only for a real terminal**: a pipe, a
        #: redirect, a transcript and a JSON consumer want every byte as it is produced,
        #: and pacing them would buy nothing (nobody reads a log at 30 Hz). ``None`` from
        #: the caller means "decide it here"; ``0`` means "never pace"; a positive number
        #: is the explicit bound.
        declared = getattr(self.options, "stream_hz", None)
        if declared is None:
            self.session.stream_hz = _STREAM_HZ if self.interactive else 0.0
        else:
            self.session.stream_hz = max(0.0, float(declared))

    # -- the decided look ------------------------------------------------ #
    def _detect_theme(self) -> Any:
        """The theme object for this stream, when the theme module is there (never required)."""
        module = load_theme()
        detect = getattr(module, "detect", None)
        if not callable(detect):
            return None
        try:
            decided = detect(
                self.out,
                no_color=bool(self.options.no_color),
                mode="rich" if self.paint.color else "plain",
            )
        except Exception:  # noqa: BLE001 — an incompatible signature: fall back below
            return None
        return decided if hasattr(decided, "unicode") else None

    @property
    def unicode(self) -> bool:
        """Unicode glyphs allowed here? (the theme decides; without one, colour implies it)"""
        if self.theme is not None:
            return bool(getattr(self.theme, "unicode", False))
        return bool(self.paint.color)

    def glyph(self, name: str) -> str:
        """One glyph of the message rhythm, ASCII when the stream is ASCII (a pipe, a log)."""
        pair = _OWN_GLYPHS.get(name)
        if pair is not None:
            return pair[0] if self.unicode else pair[1]
        theme = self.theme
        if theme is not None and hasattr(theme, "glyph"):
            try:
                return str(theme.glyph(name))
            except Exception:  # noqa: BLE001 — an unknown name in an older theme
                pass
        if name in _FALLBACK_GLYPHS:
            return _FALLBACK_GLYPHS[name] if self.unicode else {
                "sep": "-", "ellipsis": "...", "arrow": "->",
            }.get(name, name)
        return name

    def mark(self, status: str) -> str:
        """A status mark (✓ / +) — the theme's when it is there, ASCII otherwise."""
        theme = self.theme
        if theme is not None and hasattr(theme, "mark"):
            try:
                return str(theme.mark(status))
            except Exception:  # noqa: BLE001
                pass
        table = {"ok": ("✓", "+"), "fail": ("✗", "x"), "blocked": ("■", "#"),
                 "warn": ("·", ".")}
        pair = table.get(status, ("·", "."))
        return pair[0] if self.unicode else pair[1]

    def line_width(self) -> int:
        """The line budget: 80 columns, less when the terminal is narrower."""
        theme = self.theme
        columns = int(getattr(theme, "width", 0) or 0)
        if not columns:
            try:
                columns = int(shutil.get_terminal_size().columns)
            except Exception:  # noqa: BLE001 — a stream without a size is 80 wide
                columns = 0
        return max(40, min(STATUS_WIDTH, columns or STATUS_WIDTH))

    # -- output ---------------------------------------------------------- #
    def _plain(self, text: str) -> str:
        """Plain mode is ASCII-only — the shell's own lines go through here, not the
        command's output (a command prints what it prints; rewriting it would be a lie)."""
        return str(text) if self.unicode else _slash.ascii_safe(text)

    def say(self, text: str = "") -> None:
        """Print one shell line (terminal + transcript record)."""
        text = self._plain(text)
        self.transcript.append(text)
        if text:
            self._turn_printed = True
        try:
            print(text, file=self.out)
        except Exception:  # noqa: BLE001 — a closed terminal must not crash the loop
            pass

    def warn(self, text: str) -> None:
        text = self._plain(text)
        self.transcript.append(text)
        try:
            print(text, file=self.err)
        except Exception:  # noqa: BLE001
            pass

    def write_transcript(self) -> str:
        if not self.options.transcript:
            return ""
        path = Path(self.options.transcript)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(self.transcript) + "\n", encoding="utf-8")
        except OSError as exc:
            self.warn(f"could not write the transcript to {path}: {type(exc).__name__}: {exc}")
            return ""
        return str(path)

    # -- state display --------------------------------------------------- #
    def status_bits(self) -> list[str]:
        """The runtime / goal / provider labels (long forms — `status_facts` is the
        short line the prompt uses; this stays for callers and tests that want the
        probe values as they are)."""
        runtime = self.session.probes.runtime_dir()
        provider = self.session.probes.provider()
        goal = self.session.probes.goal()
        return [runtime.label, goal.label, provider.label]

    def workspace_label(self) -> str:
        """Where the session is — short (``~/projects/gods-voice``), never a full path."""
        try:
            here = _short_path(Path.cwd())
        except Exception:  # noqa: BLE001 — a deleted cwd is not a crash
            here = ""
        return here or "workspace:?"

    def status_facts(self) -> list[str]:
        """The 2-3 facts the status line carries, most relevant first.

        Workspace, the durable goal, the provider — plus the warm session, and only while
        one is really answering (``warm:off`` is not news). Everything comes from a real
        probe; nothing here is guessed, and the order is the order facts are dropped in
        when the line has to give.
        """
        probes = self.session.probes
        facts = [
            self.workspace_label(),
            f"goal:{_status_label(probes.goal().label)}",
            f"provider:{_status_label(probes.provider().label)}",
        ]
        warm = probes.warm()
        if warm.ok:
            facts.append(f"warm:{_status_label(warm.label)}")
        return facts

    def status_line(self) -> str:
        """The one dim line above the prompt (``""`` when there is nothing honest to say)."""
        line = clip_facts(
            self.status_facts(),
            self.line_width(),
            sep=f" {self.glyph('sep')} ",
            ellipsis=self.glyph("ellipsis"),
        )
        return self.paint(line, "dim") if line else ""

    def show_status(self, *, force: bool = False) -> bool:
        """Draw the status line — and redraw it between commands only when it changed.

        Returns whether a line was printed. A redraw per keystroke would be noise; a stale
        line after the goal moved would be a lie, so the comparison is on the rendered text.
        """
        line = self.status_line()
        if not line or (not force and line == self._status_shown):
            return False
        self._status_shown = line
        self.say(line)
        return True

    def prompt(self) -> str:
        """One line, always the same shape: ``skaldir ❯ ``.

        The state lives on the dim line above it (see :meth:`status_line`); a prompt that
        grows with the session is exactly the noise this shell avoids.
        """
        return f"{self.paint(PROMPT_NAME, 'bold')} {self.paint(self.glyph('prompt'), 'dim')} "

    def prompt_for_readline(self, prompt: str) -> str:
        """Bracket every SGR run in the prompt with readline's ignore markers.

        Measured on a real PTY: readline **deletes** bare escape sequences from a prompt
        (it computes the line width itself), so a painted prompt otherwise reaches the
        terminal as a literal ``[1mskaldir[0m``. ``\\001``/``\\002`` are readline's
        RL_PROMPT_START_IGNORE / RL_PROMPT_END_IGNORE; a plain (unpainted) prompt is
        returned unchanged, so nothing leaks into a pipe or a non-readline input().
        """
        if not self.readline_ready or "\x1b" not in prompt:
            return prompt
        return _SGR.sub(lambda match: f"\001{match.group(0)}\002", prompt)

    # -- the approval prompt --------------------------------------------- #
    # Codex's review overlay, in this shell's vocabulary: the exact command, the deciding
    # rule, the three answers as words a person can read. The *decision* is never taken
    # here — ``approval.py`` judged it; the shell only collects the answer and records it
    # through the real ``approval allow`` / ``approval deny`` commands.
    def ask(self, prompt_text: str, *, default: str = "") -> str:
        """One answer from the user. A non-interactive session never blocks (``default``)."""
        if not self.interactive:
            return default
        try:
            return str(input(self.prompt_for_readline(prompt_text))).strip()
        except (EOFError, KeyboardInterrupt):
            return default

    def gated_argv(self, request: GatedCommand) -> tuple[str, ...]:
        """The invocation the gate runs: the caller's, plus **this shell's session id**.

        Without this, ``--session`` would fall back to the CLI's own default (``default``)
        and "allow for this session" would cover nothing: the grant is filed under the
        shell's session id, so every gated call has to name it. A ``--session`` the caller
        wrote is kept (their choice wins, and the grant is filed under it instead).
        """
        if request.session:
            return request.argv
        return with_flag(request.argv, "--session", self.session_id)

    def judge_approval(self, request: GatedCommand) -> dict[str, Any] | None:
        """The ladder's verdict, asked of the real ``approval check --dry-run --json``.

        Captured, never streamed: the check is a probe, not a turn, and its envelope is
        the same object ``approval check`` prints. ``None`` means the check itself could
        not answer — then the real command runs and its own verdict is the record.
        """
        session_id = request.session or self.session_id
        argv = ["approval", "check", "--dry-run", "--json",
                *request.flags(omit=("--session",)), "--session", session_id,
                *request.tokens]
        captured = self.session.capture(argv)
        return captured.data or None

    def _row(self, key: str, value: str, key_width: int, *, wrap_value: bool = False) -> str:
        """One ``key  value`` row of the approval block, clipped or wrapped to the width.

        The reason is worth wrapping (it is the fact that decides the answer); every other
        value is clipped, because an approval prompt is scanned before it is read.
        """
        room = max(20, self.line_width() - key_width - 4)
        if wrap_value and len(value) > room:
            chunks = textwrap.wrap(value, room, break_long_words=False,
                                   break_on_hyphens=False) or [value[:room]]
            pad = " " * (key_width + 2)
            head = f"  {self.paint(f'{key:<{key_width}}', 'dim')}{chunks[0]}"
            return "\n".join([head, *[f"{pad}{chunk}" for chunk in chunks[1:]]])
        shown = _slash.short_text(value, room, ellipsis=self.glyph("ellipsis"))
        return f"  {self.paint(f'{key:<{key_width}}', 'dim')}{shown}"

    def approval_block(
        self, request: GatedCommand, verdict: dict[str, Any], *, headless: bool = False
    ) -> str:
        """The approval prompt: what runs, why, on whose authority, and the answers.

        Facts in the order a reader needs them (command → decision/reason → rule →
        profile), then the answers — ``[a]`` once, ``[s]`` for this session, ``[r]`` make
        it policy, ``[d]`` deny with a reason, Enter denies. A ``forbid`` shows the same
        block without an answer row: a forbidden command has nothing to answer.
        """
        sep = f" {self.glyph('sep')} "
        decision = str(verdict.get("decision") or "?")
        profile = verdict.get("profile") if isinstance(verdict.get("profile"), dict) else {}
        rows: list[tuple[str, str]] = [
            ("command", request.command or "(empty)"),
            ("because", f"{decision} {self.glyph('dash')} {verdict.get('reason') or ''}".strip()),
            ("rule", (f"{verdict.get('rule_id') or '(none)'} "
                      f"{verdict.get('rule_text') or ''}").strip()),
            ("profile", f"{profile.get('name') or '?'} {sep} sha256 "
                        f"{str(profile.get('sha256') or '')[:12]}"),
        ]
        key_width = max(len(key) for key, _ in rows) + 2
        lines = [
            f"{self.paint(self.glyph('action'), 'dim')} "
            f"{self.paint('approval needed', 'bold')}{sep}"
            f"{self.paint(request.path, 'dim')}"
        ]
        for key, value in rows:
            lines.append(self._row(key, value, key_width, wrap_value=(key == "because")))
        if decision == "forbid":
            lines.append(self.paint(
                "  nothing to answer — this policy forbids it; the command reports it "
                "blocked (exit 3) and does not run", "warn"))
            return "\n".join(lines)
        if headless:
            lines.append(self.paint(
                "  headless — no interactive user, so the prompt cannot be answered: the "
                "command returns blocked (exit 3) and nothing is auto-allowed", "warn"))
            for line in verdict.get("would_allow") or []:
                lines.append(self.paint(f"    {line}", "dim"))
            return "\n".join(lines)
        answers = (
            f"{self.paint('[a]', 'bold')} allow once{sep}"
            f"{self.paint('[s]', 'bold')} allow for this session{sep}"
            f"{self.paint('[r]', 'bold')} allow and make it policy{sep}"
            f"{self.paint('[d]', 'bold')} deny with a reason{sep}"
            f"{self.paint('[Enter]', 'bold')} deny"
        )
        # the key is padded *before* it is painted: a painted string carries escape
        # sequences, and format-width counts them (measured in the PTY capture)
        key_label = f"{'answers':<{key_width}}"
        lines.append(f"  {self.paint(key_label, 'dim')}{answers}")
        return "\n".join(lines)

    def approval_answer_prompt(self) -> str:
        return (f"{self.paint(self.glyph('ask'), 'dim')} "
                f"{self.paint('answer [a/s/r/d]', 'bold')} › ")

    def reason_prompt(self) -> str:
        return (f"{self.paint(self.glyph('ask'), 'dim')} "
                f"{self.paint('reason (the agent is told this; Enter = none)', 'bold')} › ")

    def record_allow(
        self, request: GatedCommand, *, scope: str, session_id: str, rule: bool = False
    ) -> dict[str, Any]:
        """Record a *yes* through the real ``approval allow`` (one real command, real record).

        ``--json`` is asked for explicitly: the shell prints its own line and needs the
        envelope (the evidence ref it shows), not the command's human text.
        """
        argv = ["approval", "allow", "--json", *request.flags(omit=("--session",))]
        if rule:
            argv += ["--rule"]
        else:
            argv += ["--scope", scope]
        argv += ["--session", session_id, *request.tokens]
        captured = self.session.capture(argv)
        return {"exit_code": captured.exit_code, "data": captured.data,
                "output": captured.output, "envelope": captured.envelope}

    def record_deny(
        self, request: GatedCommand, *, reason: str, abort: bool = False
    ) -> dict[str, Any]:
        """Record a *no* through the real ``approval deny`` — with the reason, never a dead end."""
        argv = ["approval", "deny", "--json", *request.flags(omit=("--session",))]
        if reason:
            argv += ["--reason", reason]
        if abort:
            argv += ["--abort"]
        argv += [*request.tokens]
        captured = self.session.capture(argv)
        return {"exit_code": captured.exit_code, "data": captured.data,
                "output": captured.output, "envelope": captured.envelope}

    def ask_approval(
        self, request: GatedCommand, verdict: dict[str, Any]
    ) -> tuple[str, ...] | None:
        """Ask, record the answer, and return the argv that may now run (``None`` = nothing).

        ``allow once`` is a **one-shot session grant**: ``approval.py`` deliberately never
        persists a turn grant, so a grant that must survive exactly one command is created
        for a session id of its own and removed by :meth:`settle_oneshot` the moment that
        command ends. That is why the same command asks again the second time.
        """
        self.say(self.approval_block(request, verdict))
        answer = ""
        for _attempt in range(2):
            answer = self.ask(self.approval_answer_prompt())
            if approval_answer(answer) is not None:
                break
            self.say(self.paint(
                f"  {answer!r} is not one of the answers — a, s, r, d, or Enter to deny", "warn"))
        kind = approval_answer(answer) or "deny"
        if kind == "abort":
            record = self.record_deny(request, reason="the user aborted the turn", abort=True)
            self.approvals += 1
            self.say(self.result_line(
                f"denied (abort) {self.glyph('dash')} nothing ran {self.glyph('sep')} "
                f"recorded {self._evidence_ref(record)}"))
            return None
        if kind == "deny":
            reason = self.ask(self.reason_prompt())
            record = self.record_deny(request, reason=reason)
            self.approvals += 1
            told = _slash.short_text(reason or 'the user declined without a reason', 60,
                                      ellipsis=self.glyph('ellipsis'))
            self.say(self.result_line(
                f"denied {self.glyph('dash')} nothing ran {self.glyph('sep')} "
                f"the agent is told: {told}"
            ))
            self.say(self.paint(f"  recorded {self._evidence_ref(record)}", "dim"))
            return None
        if kind == "rule":
            session_id = request.session or self.session_id
            record = self.record_allow(
                request, scope="turn", session_id=session_id, rule=True
            )
            self.approvals += 1
            if int(record["exit_code"]) != 0:
                refusal = _slash.short_text(self._refusal_text(record), 70,
                                            ellipsis=self.glyph('ellipsis'))
                self.say(self.paint(
                    f"  the rule was refused ({record['exit_code']}): {refusal}",
                    "warn"))
                return None
            self.say(self.result_line(
                f"allowed and made policy {self.glyph('sep')} {self._evidence_ref(record)}"))
            return with_flag(request.argv, "--session", session_id)
        if kind == "session":
            session_id = request.session or self.session_id
            record = self.record_allow(request, scope="session", session_id=session_id)
            self.approvals += 1
            if int(record["exit_code"]) != 0:
                refusal = _slash.short_text(self._refusal_text(record), 70,
                                            ellipsis=self.glyph('ellipsis'))
                self.say(self.paint(
                    f"  the grant was refused ({record['exit_code']}): {refusal}",
                    "warn"))
                return None
            self.say(self.result_line(
                f"allowed for this session {self.glyph('sep')} session {session_id} "
                f"{self.glyph('sep')} {self._evidence_ref(record)}"))
            return with_flag(request.argv, "--session", session_id)
        # allow once
        session_id = f"{request.session or self.session_id}-once-{uuid4().hex[:8]}"
        record = self.record_allow(request, scope="session", session_id=session_id)
        self.approvals += 1
        if int(record["exit_code"]) != 0:
            refusal = _slash.short_text(self._refusal_text(record), 70,
                                        ellipsis=self.glyph('ellipsis'))
            self.say(self.paint(
                f"  the approval was refused ({record['exit_code']}): {refusal}",
                "warn"))
            return None
        self._oneshot_grants.append(session_id)
        self.say(self.result_line(
            f"allowed once {self.glyph('sep')} one-shot grant {session_id} "
            f"{self.glyph('sep')} {self._evidence_ref(record)}"))
        return with_flag(request.argv, "--session", session_id)

    @staticmethod
    def _evidence_ref(record: dict[str, Any]) -> str:
        """The ledger reference of a recorded answer (from the command's own envelope)."""
        data = record.get("data") or {}
        ref = str(data.get("evidence_ref") or "")
        if not ref:
            rule = data.get("persisted_rule")
            if isinstance(rule, dict):
                ref = str(rule.get("evidence_ref") or "")
        if not ref:
            envelope = record.get("envelope") or {}
            for entry in envelope.get("evidence") or []:
                if isinstance(entry, dict) and str(entry.get("ref") or "").endswith(".jsonl"):
                    ref = str(entry["ref"])
                    break
        return ref or f"exit {record.get('exit_code')}"

    @staticmethod
    def _refusal_text(record: dict[str, Any]) -> str:
        """The refusal a real command printed (its own envelope's error, else its output)."""
        envelope = record.get("data")
        if isinstance(envelope, dict) and envelope:
            return str(envelope.get("message") or envelope.get("reason") or "")
        lines = [line for line in str(record.get("output") or "").splitlines() if line.strip()]
        return lines[-1] if lines else "no reason given"

    def settle_oneshot(self) -> None:
        """Remove the one-shot grants of the last answer — that is what "once" means.

        The *record* of the answer stays in the approval ledger (it is evidence); only the
        grant file the shell created for a single command is deleted. Without this, "allow
        once" would quietly become "allow for this session".
        """
        if not self._oneshot_grants:
            return
        try:
            from . import approval as _approval
        except Exception:  # noqa: BLE001 — no approval layer: nothing was created either
            self._oneshot_grants = []
            return
        for session_id in self._oneshot_grants:
            try:
                path = _approval.grants_path(self.session.runtime, session_id)
                path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001 — a grant that cannot be removed is reported
                self.warn(self.paint(
                    f"  the one-shot grant {session_id} could not be removed — it is still "
                    f"in {_approval.grants_dir(self.session.runtime)}", "warn"))
        self._oneshot_grants = []

    def approval_gate(self, argv: tuple[str, ...]) -> tuple[str, ...] | None:
        """Judge a gated invocation, prompt when the ladder asks, and return what may run."""
        request = gated_command(argv, APPROVAL_PATHS)
        if request is None or request.dry_run or not request.tokens:
            return argv
        if not self.options.approval:
            return argv
        verdict = self.judge_approval(request)
        if not verdict:
            return argv  # the check could not answer: the real command's verdict counts
        decision = str(verdict.get("decision") or "")
        if decision == "allow":
            return self.gated_argv(request)
        if decision == "forbid" or not self.interactive:
            self.say(self.approval_block(
                request, verdict, headless=(decision != "forbid")))
            return self.gated_argv(request)
        return self.ask_approval(request, verdict)

    # -- the diff preview ------------------------------------------------ #
    def theme_scope(self) -> Any:
        """Render inside the shell's own theme (a command may have changed the global one)."""
        module = load_theme()
        use = getattr(module, "use", None) if module is not None else None
        if self.theme is not None and callable(use):
            try:
                return use(self.theme)
            except Exception:  # noqa: BLE001 — an incompatible theme module
                return contextlib.nullcontext()
        return contextlib.nullcontext()

    def preview_block(self, record: dict[str, Any]) -> str:
        """The compact diff preview of a planned transaction (record → text)."""
        sep = f" {self.glyph('sep')} "
        files = [item for item in (record.get("files") or []) if isinstance(item, dict)]
        added = removed = 0
        for item in files:
            plus, minus = _render.diff_stat(str(item.get("diff") or ""))
            added += plus
            removed += minus
        head = (
            f"{self.paint(self.glyph('action'), 'dim')} "
            f"{self.paint('diff preview', 'bold')}{sep}"
            f"{self.paint('dry run — nothing is written yet', 'dim')}"
        )
        facts = (
            f"files {len(files)}{sep}+{added} -{removed}{sep}"
            f"verdict {record.get('verdict') or '?'}"
            + (f"{sep}check {record.get('verification', {}).get('kind')}"
               if (record.get("verification") or {}).get("kind") else "")
        )
        lines = [head, f"  {self.paint(facts, 'dim')}"]
        with self.theme_scope():
            body, hidden = _render.diff_preview(
                files, theme=self.theme, columns=self.line_width(),
                limit=_render.DIFF_PREVIEW_LINES,
            )
        if body:
            lines.append(body)
        if hidden:
            lines.append(self.paint(
                f"  {self.glyph('ellipsis')} {hidden} more changed line(s) — "
                f"`skaldir edit show --json` has the whole diff", "dim"))
        if record.get("diff_truncated"):
            lines.append(self.paint(
                "  note: the payload's diff hit the diff cap; the full aggregate is in the "
                "record's diff.patch", "warn"))
        return "\n".join(lines)

    def diff_gate(self, argv: tuple[str, ...]) -> tuple[str, ...] | None:
        """Preview an ``edit apply`` with the real dry run, ask, then let it (not) apply."""
        request = gated_command(argv, EDIT_APPLY_PATHS)
        if request is None or not self.options.preview:
            return argv
        dry = ["edit", "dry-run", *request.argv[2:]]
        if "--json" not in dry:
            dry.append("--json")
        captured = self.session.capture(dry)
        record = captured.data
        if not record:
            return argv  # the dry run could not plan: the real apply speaks for itself
        self.say(self.preview_block(record))
        verdict = str(record.get("verdict") or "")
        if verdict != "applicable":
            self.say(self.result_line(
                f"the dry run refuses it ({verdict}) {self.glyph('dash')} running the real "
                f"command so its own answer and exit code count"))
            return argv
        if not self.interactive:
            return argv  # a scripted run is not asked; the dry run above is its preview
        answer = self.ask(
            f"{self.paint(self.glyph('ask'), 'dim')} "
            f"{self.paint('apply this transaction? [y/N]', 'bold')} › "
        )
        if answer.strip().lower() in ("y", "yes"):
            return argv
        self.say(self.result_line(
            f"not applied {self.glyph('dash')} nothing was written; `skaldir edit dry-run` "
            f"shows the same diff"))
        return None

    def prepare(self, argv: tuple[str, ...]) -> tuple[str, ...] | None:
        """The two gates in front of the runner: the approval ladder, then the diff preview.

        Returns the argv to run — the same one when nothing was gated, a rewritten one when
        an answer was recorded — or ``None`` when nothing may run at all.
        """
        decided = self.approval_gate(argv)
        if decided is None:
            return None
        argv = decided
        return self.diff_gate(argv)

    # -- the footer ------------------------------------------------------- #
    def edit_state(self) -> tuple[dict[str, Any], ...]:
        """The edit records in this runtime, newest first — read-only, mtime-cached.

        Read from the edit layer's own state directory through
        :func:`skaldir.edit_tx.list_transactions` (the same function ``skaldir edit show
        --list`` prints from), **not** by running a command: a footer must not cost the
        runner a call, and a probe must never look like a turn. The cache key is the state
        directory's mtime, so a transaction written since the last look invalidates it.
        """
        state = Path(self.session.runtime) / EDIT_STATE_DIR
        try:
            key = state.stat().st_mtime
        except OSError:
            key = 0.0
        cached = self._edit_state
        if cached is not None and cached[0] == key:
            return cached[1]
        rows: tuple[dict[str, Any], ...] = ()
        if state.is_dir():
            try:
                from . import edit_tx as _edit_tx

                found = _edit_tx.list_transactions(state)
                rows = tuple(item for item in (found or []) if isinstance(item, dict))
            except Exception:  # noqa: BLE001 — no edit layer in this build: no records to show
                rows = ()
        self._edit_state = (key, rows)
        return rows

    def footer_material(self) -> list[str]:
        """The facts that make the footer **reprint** — everything except the counters.

        The counters are part of the line (a footer without them says less than it could)
        but not part of its identity: a footer that reappears because a counter ticked is
        noise, while one that appears because a transaction, an approval or a reported
        token count changed is news. Codex redraws its footer every frame because it owns
        the frame; a line-based shell only has one line to spend, so it spends it on news.
        """
        facts: list[str] = []
        records = self.edit_state()
        if records:
            newest = records[0]
            tx_id = str(newest.get("tx_id") or "")
            verdict = str(newest.get("verdict") or "")
            # a transaction id is long (a timestamp + a suffix): the line shows its time
            # part with an ellipsis, and `/diff` (which resolves "last") prints the whole
            # id in its header — a truncated id is never presented as the id
            shown = tx_id if len(tx_id) <= 16 else tx_id[:15] + self.glyph("ellipsis")
            fact = f"last edit {shown}".strip()
            # the verdict is stated only when it is *not* a plain success: a rolled-back
            # transaction is the one thing this line must never flatten into "an edit
            # happened"
            if verdict and verdict not in ("applied", "applied_unverified"):
                fact += f" ({verdict})"
            facts.append(fact)
            facts.append("/diff shows it")
        elif not (Path.cwd() / "AGENTS.md").is_file():
            facts.append("/init writes AGENTS.md")
        if self.approvals:
            facts.append(f"{self.approvals} approval" + ("" if self.approvals == 1 else "s"))
        result = self.last_result
        if result is not None and result.tokens_reported is not None:
            facts.append(f"tokens:{format_count(result.tokens_reported)} (reported)")
        return facts

    def footer_facts(self) -> list[str]:
        """The footer's whole line: what changed first, then the counters.

        The order is the drop order on a narrow line (:func:`clip_facts` gives up the tail
        first), and a counter is the least actionable fact on the line.
        """
        counts = f"{self.commands} command" + ("" if self.commands == 1 else "s")
        counters = [counts, f"{self.failures} failed"]
        if self.cancelled:
            counters.append(f"{self.cancelled} cancelled")
        return self.footer_material() + counters

    def footer_line(self) -> str:
        """One dim line of session facts (``""`` when nothing honest to say)."""
        line = clip_facts(self.footer_facts(), self.line_width(),
                          sep=f" {self.glyph('sep')} ", ellipsis=self.glyph("ellipsis"))
        return self.paint(line, "dim") if line else ""

    def footer_key(self) -> str:
        """The material identity of the footer (what :meth:`show_footer` compares)."""
        return clip_facts(self.footer_material(), self.line_width(),
                          sep=f" {self.glyph('sep')} ", ellipsis=self.glyph("ellipsis"))

    def show_footer(self, *, force: bool = False) -> bool:
        """Draw the footer — and re-draw it only when it has news (see :meth:`footer_material`)."""
        if not self.options.footer:
            return False
        key = self.footer_key()
        if not key:
            return False
        if not force and key == self._footer_shown:
            return False
        line = self.footer_line()
        if not line:
            return False
        self._footer_shown = key
        self.say(line)
        return True

    # -- /init and /diff (the shell's own words, always over real files) -- #
    def cmd_init(self, tokens: Sequence[str] = ()) -> int:
        """``/init`` — write a project ``AGENTS.md`` from this repository, never over one.

        Codex's ``/init`` asks the model for the file; ours *reads the repository* and
        states only what it can see (layout, commands, test runner, conventions) — the same
        outcome without a model in the loop, and every line traceable to a file that was
        read. ``--force`` is the only way to overwrite an existing file.
        """
        args = [str(item) for item in tokens if str(item)]
        cwd = Path.cwd()
        target = cwd / "AGENTS.md"
        forced = "--force" in args
        if target.exists() and not forced:
            self.say(self.paint(
                f"  {_short_path(target)} already exists — /init does not overwrite it "
                f"(pass --force to replace it)", "warn"))
            return EXIT_BLOCKED
        try:
            body = agents_md_for(cwd)
            target.write_text(body, encoding="utf-8")
        except OSError as exc:
            self.say(self.paint(f"  could not write {_short_path(target)}: "
                                f"{type(exc).__name__}: {exc}", "fail"))
            return EXIT_INTERNAL
        size = len(body.encode("utf-8"))
        self.say(f"{self.paint('wrote', 'bold')} {_short_path(target)} "
                 f"{self.glyph('sep')} {format_size(size)} {self.glyph('sep')} "
                 f"{body.count(chr(10)) + 1} lines")
        for section in [line[2:].strip() for line in body.splitlines()
                        if line.startswith("## ")]:
            self.say(self.paint(f"  {section}", "dim"))
        self.say(self.paint(
            "  every line above was read from this repository (layout, commands, tests, "
            "history) — review it and edit it like any other file", "dim"))
        return EXIT_OK

    def cmd_diff(self, tokens: Sequence[str] = ()) -> int:
        """``/diff`` — the last edit transaction's diff, rendered compactly.

        ``/diff`` (or ``/diff last``) shows the newest transaction in this runtime's edit
        state; ``/diff <tx_id>`` shows that one; ``/diff --list`` lists them. The record
        comes from the real ``edit show`` — the shell renders it, it does not re-derive it.
        """
        args = [str(item) for item in tokens if str(item)]
        state = Path(self.session.runtime) / EDIT_STATE_DIR
        if "--list" in args or "-l" in args:
            records = self.edit_state()
            if not records:
                self.say(self.paint(f"  no edit transaction in {_short_path(state)}", "dim"))
                return EXIT_OK
            self.say(self.paint(
                f"  {len(records)} edit record(s) in {_short_path(state)}", "dim"))
            for item in records:
                files = item.get("files") or []
                self.say(f"  {item.get('tx_id')}  {str(item.get('verdict')):<18} "
                         f"{len(files)} file(s)  {item.get('created_at')}")
            return EXIT_OK
        wanted = next((item for item in args if not item.startswith("-")), "last")
        captured = self.session.capture(
            # `edit show` takes the transaction id as its positional argument (measured:
            # `--tx` is the option of apply/dry-run, not of show)
            ["edit", "show", wanted, "--json", "--state", str(state)]
        )
        record = captured.data if isinstance(captured.data, dict) else {}
        if record.get("reason") == "not_found" or not record.get("tx_id"):
            # `edit show` says it in its own words (exit 5, reason "not_found"): show that
            self.say(self.paint(
                f"  {str(record.get('message') or f'no transaction {wanted!r} in {state}')} "
                f"— `skaldir edit apply --tx …` writes one, `/diff --list` lists them", "warn"))
            return EXIT_NOT_FOUND
        if captured.exit_code != EXIT_OK:
            self.say(self.paint(
                f"  {str(record.get('message') or 'the edit record could not be read')}", "warn"))
            return int(captured.exit_code)
        sep = f" {self.glyph('sep')} "
        files = [item for item in (record.get("files") or []) if isinstance(item, dict)]
        added = removed = 0
        for item in files:
            plus, minus = _render.diff_stat(str(item.get("diff") or ""))
            added += plus
            removed += minus
        self.say(
            f"{self.paint(self.glyph('action'), 'dim')} "
            f"{self.paint('edit diff', 'bold')}{sep}"
            f"{self.paint(str(record.get('tx_id')), 'dim')}"
        )
        facts = [f"verdict {record.get('verdict')}",
                 _short_path(Path(str(record.get('workspace') or '.'))),
                 f"files {len(files)}", f"+{added} -{removed}"]
        if record.get("created_at"):            # only facts that are known are printed
            facts.append(str(record["created_at"]))
        self.say(self.paint("  " + sep.join(facts), "dim"))
        with self.theme_scope():
            body, hidden = _render.diff_preview(
                files, theme=self.theme, columns=self.line_width(),
                limit=_render.DIFF_PREVIEW_LINES,
            )
        if body:
            self.say(body)
        if hidden:
            self.say(self.paint(
                f"  {self.glyph('ellipsis')} {hidden} more changed line(s) — "
                f"`skaldir edit show {record.get('tx_id')} --json` has the whole record", "dim"))
        if record.get("in_tree"):
            self.say(self.paint(
                f"  `skaldir edit undo {record.get('tx_id')}` restores exactly these files",
                "dim"))
        return EXIT_OK

    def run_builtin(self, invocation: Any) -> bool:
        """A slash command the shell answers itself: help, quit-ish, /init, /diff, /keys, /clear."""
        name = invocation.command.name
        handlers = {
            "init": lambda extra: self.cmd_init(extra),
            "diff": lambda extra: self.cmd_diff(extra),
            # CHATBAR's own hook (chatbar.handle_clear) already knows what a wipe means in
            # each mode: a frame up = wipe + logo, a pipe = an honest "nothing to clear".
            # It had no caller; `/clear` (a slash verb since this round) is that caller.
            "clear": lambda extra: self.cmd_clear(invocation),
            "keys": lambda extra: self.cmd_keys(),
            # SURFACE-RUN-CHAT: the two delivery verbs. Both write to the *durable* message
            # table of the run this session is the surface of; the running loop drains them at
            # its next turn boundary and stamps the turn that consumed them.
            "steer": lambda extra: self.cmd_steer(extra),
            "queue": lambda extra: self.cmd_queue(extra),
        }
        if name in handlers:
            code = handlers[name](invocation.extra)
            self.commands += 1
            self.last_exit = code
            if code != EXIT_OK:                     # a refused built-in is a failed turn
                self.failures += 1
            self.record(
                invocation.raw or invocation.command.slash,
                (invocation.command.slash, *invocation.extra),
                RunResult(argv=(invocation.command.slash, *invocation.extra), exit_code=code),
            )
            return False
        self.handle_builtin("/help")
        return bool(self._quit_requested)

    def cmd_keys(self) -> int:
        """``/keys`` — the bindings that are really installed, plus the shell's own keys."""
        self.say(_slash.keys_text(paint=self.paint, width=self.line_width()))
        return EXIT_OK

    # -- the picker's own verbs --------------------------------------------- #
    #: ``/verb`` → the shell method that answers its **bare** form. With an argument (or a flag)
    #: the line is not this hook's: it goes to the mapped command exactly as before.
    PICKER_VERBS: tuple[tuple[str, str], ...] = (
        ("sessions", "cmd_sessions"),
        ("resume", "cmd_resume"),
    )

    def handle_picker_verb(self, text: str) -> bool | None:
        """Open the picker for a *bare* picker verb; ``None`` means "not my line".

        ``/sessions`` and ``/resume`` with no argument are browse questions — the widget answers
        them. ``/sessions --limit 5``, ``/resume s-1234`` and ``/sessions --all`` are the real
        commands with real flags, and this hook refuses them so the palette's mapping runs them
        unchanged (one behaviour per line, no second implementation of the command).
        """
        words = [part for part in str(text or "").strip().split() if part]
        if len(words) != 1:
            return None
        head = words[0].lower()
        if not head.startswith(_slash.SLASH):
            return None
        invocation = _slash.resolve(head, self.session.tree)
        if invocation is None or invocation.builtin:
            return None
        for name, method in self.PICKER_VERBS:
            if invocation.command.name != name:
                continue
            handler = getattr(self, method, None)
            if handler is None:
                return None
            code = int(handler(()))
            self.commands += 1
            self.last_exit = code
            if code != EXIT_OK:
                self.failures += 1
            self.record(
                invocation.raw or invocation.command.slash,
                (invocation.command.slash,),
                RunResult(argv=(invocation.command.slash,), exit_code=code),
            )
            return False
        return None

    # -- the picker: one selector for the whole product ---------------------- #
    def pick(self, items: Sequence[Any], *, title: str = "", page: int = 0) -> Any:
        """One selection out of ``items`` — the frame's picker, or a numbered list.

        On a terminal the picker runs *inside* the two-bar frame (arrows, j/k, PgUp/PgDn, live
        filtering, Enter, Esc); a pipe, a redirect, ``TERM=dumb`` or ``NO_COLOR`` gets the numbered
        fallback where the number is typed. The widget itself is :mod:`skaldir.picker` — the same
        one for every list in the chat.
        """
        from . import picker as _picker

        chooser = _picker.Picker(
            list(items),
            title=str(title),
            page=int(page or _picker.PAGE),
            unicode=bool(self.unicode),
            width=int(self.line_width()),
        )
        front = self._front()
        if front is not None:
            return front.pick(chooser)
        return _picker.plain_menu(
            chooser,
            write=self.say,
            read=self._read_choice,
            prompt="pick a number (Enter cancels): ",
        )

    def _read_choice(self, prompt_text: str) -> str:
        """One typed answer for the numbered picker — a terminal *or* a scripted stdin.

        ``Shell.ask`` returns its default when stdin is a pipe, which is right for a prompt nobody
        is there to answer and wrong for a scripted session that really does answer: a pipe feeds
        the picker its number, and an exhausted pipe cancels instead of blocking.
        """
        if self.interactive:
            try:
                return str(input(self.prompt_for_readline(prompt_text))).strip()
            except (EOFError, KeyboardInterrupt):
                return ""
        self.say(prompt_text)
        try:
            raw = self.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            return ""
        if raw == "":
            return ""
        answer = raw.strip()
        if self.echo:
            self.say(answer)
        return answer

    def session_items(self) -> tuple[list[Any], str]:
        """``([Item, …], why)`` — the real session store, read through the real command."""
        from . import picker as _picker

        captured = self.session.capture(["session", "list", "--json"])
        envelope = captured.envelope or {}
        data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        rows = list((data or {}).get("sessions") or [])
        if not rows:
            if captured.exit_code != EXIT_OK:
                return [], (f"the session store could not be read (exit {captured.exit_code}) — "
                            "`skaldir session list` says why")
            return [], ""
        now = time.time()
        items = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            identifier = str(row.get("session_id") or "")
            if not identifier:
                continue
            title = str(row.get("title") or "") or "(untitled)"
            count = int(row.get("message_count") or 0)
            age = _picker.age_text(now - float(row.get("updated") or 0.0))
            items.append(_picker.Item(
                value=identifier,
                title=title,
                detail=f"{count} message(s)",
                meta=age,
            ))
        return items, ""

    def cmd_sessions(self, extra: Sequence[str] = ()) -> int:
        """``/sessions`` — browse the session store in the picker (select shows the session)."""
        args = [str(item) for item in extra]
        if args:  # `/sessions --limit 5` is the real command, flags and all
            return self.run_command(" ".join(("session", "list", *args)))
        items, why = self.session_items()
        if why:
            self.say(self.paint(f"  {why}", "warn"))
            return EXIT_BLOCKED
        if not items:
            self.say(self.paint("  no sessions are recorded in this runtime yet "
                                "(`/new` starts one)", "dim"))
            return EXIT_OK
        result = self.pick(items, title="sessions")
        if not result.ok:
            self.say(self.paint(f"  nothing selected ({result.why or 'cancelled'})", "dim"))
            return EXIT_OK
        return self.run_command(f"session show {result.value}")

    def cmd_resume(self, extra: Sequence[str] = ()) -> int:
        """``/resume`` — pick a session to resume; ``/resume <id>`` is the direct form."""
        args = [str(item) for item in extra]
        if args:
            return self.run_command(" ".join(("session", "resume", "--session", *args)))
        items, why = self.session_items()
        if why:
            self.say(self.paint(f"  {why}", "warn"))
            return EXIT_BLOCKED
        if not items:
            self.say(self.paint("  no sessions are recorded in this runtime yet", "dim"))
            return EXIT_OK
        result = self.pick(items, title="resume which session")
        if not result.ok:
            self.say(self.paint(f"  nothing selected ({result.why or 'cancelled'})", "dim"))
            return EXIT_OK
        return self.run_command(f"session resume --session {result.value}")

    # -- /steer and /queue: the durable channel into a running goal ---------- #
    def run_message_target(self) -> tuple[str, str]:
        """``(goal_id, why)`` — the run this session may message.

        The target is the run this session is *attached* to (``/steer`` in the chat that
        ``skaldir run`` opened), else this session's own goal. Nothing else: a goal another
        session left open is never messaged behind its owner's back, and an empty answer is
        reported with the reason instead of a guess.
        """
        try:
            from . import runs_store as rs
        except Exception as exc:  # noqa: BLE001
            return "", f"the run store is unavailable ({type(exc).__name__})"
        probes = self.session.probes
        attached = str(getattr(probes, "attach", "") or "")
        try:
            path = rs.run_store_path(self.session.runtime)
            store = rs.RunsStore(path)
        except Exception as exc:  # noqa: BLE001
            return "", f"no run store to write to ({type(exc).__name__}: {exc})"
        try:
            if attached:
                return (attached, "") if store.goal(attached) else (
                    "", f"the attached run {attached[:24]} is not in {path}"
                )
            record = store.goal_for_session(str(self.session_id or ""))
            if record is None:
                return "", ("no run is attached to this session — start one with "
                            '`skaldir run "<goal>"`, or open the chat with --attach <goal-id>')
            return record.goal_id, ""
        finally:
            store.close()

    def deliver_message(self, kind: str, text: str) -> int:
        """``/steer``/``/queue``: record the message durably, or refuse **by name**.

        The refusal vocabulary is the store's (`no-run`, `run-finished`, `empty-text`,
        `queue-full`, `queue-disabled`, `unknown-kind`); the chat prints the reason and the
        refused attempt is written down, so nothing is dropped silently.
        """
        body = str(text or "").strip()
        if not body:
            self.say(self.paint(
                f"  /{kind} needs a message — e.g. /{kind} \"work the failing check next\"",
                "warn"))
            return EXIT_USAGE
        goal_id, why = self.run_message_target()
        try:
            from . import runs_store as rs
        except Exception as exc:  # noqa: BLE001
            self.say(self.paint(f"  the run store is unavailable ({type(exc).__name__})", "warn"))
            return EXIT_BLOCKED
        origin = f"chat:{self.session_id}"
        store = None
        try:
            store = rs.RunsStore(rs.run_store_path(self.session.runtime))
            message = store.post_message(goal_id, body, kind=kind, origin=origin)
            if why:
                # an unattached session is a refusal by name, never a silent write
                store.record_refusal(goal_id, body, kind=kind, origin=origin,
                                     reason=rs.REFUSAL_NO_RUN)
                self.say(self.paint(f"  refused (no-run): {why}", "warn"))
                return EXIT_BLOCKED
            facts = [
                f"{kind} #{message.message_id}",
                f"run {str(message.goal_id)[:20]}",
                "picked up at the next turn boundary" if kind == "steer"
                else "drained in order, one per turn",
                f"origin {origin}",
            ]
            sep = f" {self.glyph('sep')} "
            self.say(f"  {self.paint('delivered', 'ok')}{sep}" + sep.join(
                self.paint(item, "dim") for item in facts
            ))
            return EXIT_OK
        except rs.MessageRefused as exc:
            if store is not None:
                try:
                    store.record_refusal(goal_id, body, kind=kind, origin=origin,
                                         reason=exc.reason)
                except Exception:  # noqa: BLE001 — the log half must not hide the refusal
                    pass
            self.say(self.paint(f"  refused ({exc.reason}): {exc}", "warn"))
            if why:
                self.say(self.paint(f"  {why}", "dim"))
            return EXIT_USAGE if exc.reason == rs.REFUSAL_EMPTY else EXIT_BLOCKED
        except Exception as exc:  # noqa: BLE001 — a broken store is reported, not swallowed
            self.say(self.paint(
                f"  the message was not delivered ({type(exc).__name__}: {exc})", "warn"))
            return EXIT_INTERNAL
        finally:
            if store is not None:
                store.close()

    def cmd_steer(self, extra: Sequence[str] = ()) -> int:
        """``/steer <text>`` — the urgent message: consumed at the next turn boundary."""
        return self.deliver_message("steer", _unquote(" ".join(str(item) for item in extra)))

    def cmd_queue(self, extra: Sequence[str] = ()) -> int:
        """``/queue <text>`` — appended to the run's queue, drained in order."""
        return self.deliver_message("queue", _unquote(" ".join(str(item) for item in extra)))

    # -- the attached run's progress feed ------------------------------------ #
    def pump_feed(self) -> int:
        """Write what an attached run has produced since the last poll.

        Exactly one thread writes to the terminal: this one. The run hands its progress lines to
        a feed object (a :class:`queue.Queue`), and they are written here — above the frame when
        one is up, as plain shell lines otherwise.
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
        front = self._front()
        if front is not None:
            for line in lines:
                front.bar.write_above(str(line))
            return len(lines)
        for line in lines:
            self.say(str(line))
        return len(lines)

    def _say_slash_help(self, target: Any) -> bool:
        """Print one slash verb's own help + its live state (the ``/help`` answer)."""
        self.say(_slash.help_text(target, self.session.tree))
        entry = next(
            (item for item in self.palette() if item.command.name == target.name), None
        )
        if entry is not None:
            self.say(self.paint(f"  state: {entry.mark()}", "dim"))
        return False

    def _is_real_command(self, name: str) -> bool:
        """Is ``name`` a command of this build (so ``<name> --help`` means something)?"""
        word = str(name or "").lstrip(_slash.SLASH).strip()
        if not word:
            return False
        try:
            return word in set(_complete.known_paths(self.session.tree))
        except Exception:  # noqa: BLE001 — no tree: treat it as a command and let it answer
            return True

    def cmd_clear(self, invocation: Any) -> int:
        """``/clear`` — routed to the chatbar hook (wipes with a frame, honest without one)."""
        if _chatbar is not None:
            try:
                handled = _chatbar.handle_clear(self, invocation.raw or invocation.command.slash)
            except Exception as exc:  # noqa: BLE001 — a broken wipe must not end the shell
                self.warn(f"[shell] clear: {type(exc).__name__}: {exc}")
                return EXIT_INTERNAL
            if handled is not None:
                return EXIT_OK
        self.say(self.paint(
            "  /clear wipes the screen and redraws the logo — that needs the framed chat "
            "(`skaldir chat`); nothing was cleared here", "dim"))
        return EXIT_OK

    # -- the message rhythm ---------------------------------------------- #
    def action_line(self, argv: Sequence[str] = (), text: str = "") -> str:
        """``⏺ skaldir tools list`` — what is about to run, before it runs it."""
        shown = f"skaldir {' '.join(str(item) for item in argv)}".strip() if argv else text
        return f"{self.paint(self.glyph('action'), 'dim')} {self.paint(shown, 'bold')}"

    def result_line(self, text: str) -> str:
        """``  ⎿ exit 0 (OK) · 12 ms`` — one quiet line for what the action did."""
        return f"  {self.paint(self.glyph('result'), 'dim')} {self.paint(text, 'dim')}"

    def turn_open(self) -> None:
        """One blank line between turns (never a box, never a rule), then the chrome.

        The chrome above the prompt is the status line **and** the footer: the 2-3
        context facts (workspace · goal · provider) and the session facts that matter
        (counts, the last edit transaction ``/diff`` would show, tokens a command
        reported). Both are drawn only when they changed — a chrome line that reprints
        itself every turn is noise, which is the one thing this shell refuses to be.
        """
        if self._turn_printed:
            self.say("")
            self._turn_printed = False
        self.show_status()
        self.show_footer()

    def banner(self) -> None:
        version = ""
        try:
            from . import __version__

            version = f" v{__version__}"
        except Exception:  # noqa: BLE001
            version = ""
        self.say("")
        # Minimalist first screen: logo, one identity line, one `try` row, one key row.
        from . import first_run

        for line in first_run.logo_lines(theme=getattr(self, "theme", None), version=version):
            self.say(line)
        runtime = self.session.probes.runtime_dir()
        self.say(
            f"  {self.paint('Skaldir', 'bold')}{version} "
            f"{self.glyph('dash')} interactive shell "
            f"{self.glyph('dash')} {runtime.label}"
        )
        missing = first_run.missing_provider_line(runtime=self.session.probes.runtime)
        if missing:
            self.say(f"  {self.paint(missing, 'warn')}")
        row = first_run.try_this_row(tree=self.session.tree, sep=f" {self.glyph('sep')} ",
                                     paint=self.paint)
        if row:
            self.say(f"  {row}")
        # probe state (honest: "selftest: 8 layer(s)" or "capability probe did not run")
        report = self.session.probe_layers() if self.options.probe else None
        hint = _slash.hint_line(self.session.tree, report, paint=self.paint,
                                width=self.line_width(), tree_error=self.session.tree_error)
        for chunk in hint.splitlines():
            if chunk.strip():
                self.say("  " + chunk)
        keys = (
            "/help" + f" {self.glyph('sep')} Tab completes"
            + f" {self.glyph('sep')} Esc dismisses"
            + f" {self.glyph('sep')} Ctrl-C cancels"
            + f" {self.glyph('sep')} /quit"
        )
        self.say(f"  {self.paint(keys, 'dim')}")
        if self.session.tree_error:
            self.say(self.paint(
                f"  the CLI parser did not load ({self.session.tree_error}) — the palette "
                "cannot be checked against it", "warn"))
        self.say("")
        # the banner is not a turn: the first prompt gets no leading blank line
        self._turn_printed = False

    def resume_line(self) -> None:
        entries = self.history.load()
        if not entries:
            self.say(self.paint("  resume: no earlier session recorded here", "dim"))
            return
        last = entries[-1]
        known = history_counts(entries)
        self.say(
            self.paint(
                f"  resumed: {len(entries)} earlier command(s) · "
                f"{known['cancelled']} cancelled · last: {last.line!r} "
                f"({last.exit_code if not last.cancelled else 'cancelled'})",
                "dim",
            )
        )

    # -- the overlay ----------------------------------------------------- #
    def overlay(self, tokens: Sequence[str] = ()) -> None:
        """The inline ``?`` help overlay — real parser facts, never a copy."""
        tokens = [token for token in tokens if token]
        width = self.line_width()
        if not tokens:
            self.say(self.paint("? help", "bold"))
            runtime_probe = self.session.probes.runtime_dir()
            provider = self.session.probes.provider()
            goal = self.session.probes.goal()
            warm = self.session.probes.warm()
            rows = [
                ("runtime", runtime_probe.label, runtime_probe.detail or runtime_probe.source),
                ("goal", goal.label, goal.detail or goal.source),
                ("provider", provider.label, provider.detail or provider.source),
                ("warm", warm.label, warm.detail or warm.source),
            ]
            key_width = max(len(key) for key, _label, _detail in rows) + 2
            detail_width = max(20, self.line_width() - key_width - 18)
            for key, label, detail in rows:
                shown = _slash.short_text(detail, detail_width,
                                          ellipsis=self.glyph("ellipsis"))
                self.say(f"  {self.paint(f'{key:<{key_width}}', 'dim')}{label} "
                         f"{self.glyph('dash')} {shown}")
            reachable = self.session.reachable()
            if reachable is None:
                self.say(self.paint("  layers: the selftest probe has not run (--no-probe?)",
                                    "warn"))
            else:
                good = [name for name, ok in reachable.items() if ok]
                bad = [name for name, ok in reachable.items() if not ok]
                self.say(f"  {self.paint('layers', 'dim')}   reachable: "
                         f"{', '.join(good) or '(none)'}")
                if bad:
                    self.say(self.paint("           not reachable: " + ", ".join(bad), "warn"))
            self.say("")
            self.say(_slash.render(
                self.palette(),
                paint=self.paint,
                width=width,
                title="slash commands",
                ellipsis=self.glyph("ellipsis"),
            ))
            self.say(self.paint("  ? <command> shows that command's own parser help "
                                "(e.g. `tools list ?`) · `help` lists the built-ins", "dim"))
            return
        node_path = _complete.path_for(self.session.tree, tokens)
        self.say(self.paint("? overlay", "bold"))
        self.say(_complete.help_overlay(self.session.tree, tokens, width=width))
        if not node_path:
            self.say(self.paint("  (this build does not know that command — nothing is "
                                "claimed about it)", "warn"))

    # -- one line -------------------------------------------------------- #
    def read_line(self) -> str | None:
        # CHATBAR hook (additive): on a real terminal the two-bar frame owns the input
        # line; `front_for` answers None for a pipe, a redirect, a StringIO or --json, and
        # then everything below is exactly the path it always was.
        front = _chatbar.front_for(self) if _chatbar is not None else None
        if front is not None:
            return front.read_line(self)
        prompt = self.prompt()
        if self.interactive:
            try:
                line = input(self.prompt_for_readline(prompt))
            except EOFError:
                return None
            try:  # feed every line to readline's own history
                import readline

                if line.strip():
                    readline.add_history(line)
            except Exception:  # noqa: BLE001
                pass
            return line
        try:
            raw = self.stdin.readline()
        except KeyboardInterrupt:
            raise
        if raw == "":
            return None
        line = raw.rstrip("\n")
        if self.echo:
            # a literal TAB is shown as one visible glyph so the transcript shows the
            # keystroke (ASCII "<TAB>" on a pipe — plain mode is ASCII-only)
            self.say(prompt + line.replace("\t", self.glyph("tab")))
        return line

    def handle_tabs(self, line: str) -> str:
        """A literal TAB in a scripted line means "press Tab" — complete and show the menu."""
        while "\t" in line:
            head, _, tail = line.partition("\t")
            completion = _complete.complete_line(
                self.session.tree, head, len(head), slash=self._slash_suggestions
            )
            if completion.menu_lines():
                self.menu(completion)
            if not completion.insert:
                if not completion.menu_lines():
                    self.say(self.paint(f"  (no completion for {head!r})", "dim"))
                break
            head = completion.completed_line()
            line = head + tail
            if not tail:
                break
        return line

    def palette(self) -> list[_slash.PaletteEntry]:
        """The palette, marked from the real selftest report (or left neutral)."""
        _ = self.session.tree  # force the lazy load so tree_error is known
        return _slash.entries(
            self.session.tree,
            reachable=self.session.reachable(),
            report=self.session.report,
            tree_error=self.session.tree_error,
        )

    def _slash_suggestions(self, query: str) -> list[_complete.Suggestion]:
        return _slash.suggestions(
            query,
            self.session.tree,
            reachable=self.session.reachable(),
            report=self.session.report,
            tree_error=self.session.tree_error,
        )

    def menu(self, completion: _complete.Completion) -> None:
        """The completion dropdown (the scripted stand-in for readline's list).

        A ``/`` palette is drawn by the slash palette (three aligned columns, an honest
        state per row); anything else gets the completion engine's aligned candidate list.
        Both are quiet: no box, no duplicated state word, and at most :data:`MENU_ROWS`
        rows plus an honest ``… n more``.
        """
        width = self.line_width()
        header = (f"  {len(completion.suggestions)} candidate(s)"
                  f"{' · insert ' + repr(completion.insert) if completion.insert else ''}"
                  f"{' · ' + completion.reason if completion.reason else ''}")
        self.say(self.paint(header, "dim"))
        if self._is_slash_menu(completion):
            self.open_palette(completion.partial or "/")
            return
        rows = _complete.menu_lines(
            completion.suggestions, limit=MENU_ROWS, width=width,
            ellipsis=self.glyph("ellipsis"),
        )
        for row in rows:
            self.say("    " + row)
        hidden = len(completion.suggestions) - min(MENU_ROWS, len(completion.suggestions))
        if hidden > 0:
            self.say(self.paint(f"    {self.glyph('ellipsis')} {hidden} more", "dim"))

    def _is_slash_menu(self, completion: _complete.Completion) -> bool:
        """Is this the ``/`` palette? (asked of the line, not of the caller's intentions)"""
        return str(completion.partial or "").startswith(_slash.SLASH)

    def open_palette(self, query: str = "/", *, note: str = "") -> bool:
        """Show the palette, filtered by what was typed — one renderer, whoever asked.

        This is what ``/`` + Enter, a filtered ``/je`` + Enter and the scripted Tab path
        all call, so the palette looks the same however it was reached. ``False`` means
        nothing matched (the caller says so in its own words).
        """
        rows = self.menu_state.update(query, self.palette(), limit=MENU_ROWS)
        if not rows:
            return False
        if note:
            self.say(self.paint("  " + note, "warn"))
        for row in self.menu_state.render(
            paint=self.paint, width=self.line_width(), ellipsis=self.glyph("ellipsis"),
            title=f"slash commands {self.glyph('dash')} {self.menu_state.query or '/'}",
        ).splitlines():
            self.say("  " + row)
        return True

    def dismiss_menu(self) -> bool:
        """ESC: close the palette without running anything (one honest line, nothing else)."""
        if not self.menu_state.dismiss():
            return False
        self.say(self.result_line("palette dismissed"))
        return True

    def _readline_menu(self, substitution: str, matches: list[str], longest: int) -> None:
        """readline's own menu display hook — we draw OUR palette, not readline's columns.

        readline calls this when it would list the matches; drawing here keeps one look for
        ``/`` + Tab whether the line was typed at a real terminal or scripted.
        """
        suggestions = list(getattr(self.completer, "menu", []) or [])
        if not suggestions:
            return
        rows = _complete.menu_lines(suggestions, limit=MENU_ROWS, width=self.line_width(),
                                    ellipsis=self.glyph("ellipsis"))
        for row in rows:
            self.say("    " + row)

    def handle_builtin(self, line: str) -> bool:
        """The shell's own words (no slash). Returns True when the line was handled."""
        word = line.split()[0].lower() if line.split() else ""
        if word in ("quit", "exit"):
            self._quit_requested = True
            return True
        if word == "history":
            limit = 10
            parts = line.split()
            if len(parts) > 1:
                try:
                    limit = max(1, int(parts[1]))
                except ValueError:
                    limit = 10
            self.history_table(limit)
            return True
        if word in ("help", "/help"):
            self.say(_slash.render(
                self.palette(),
                paint=self.paint,
                title="slash commands — everything else is a plain skaldir command",
            ))
            self.say("")
            self.say(self.paint("  built-ins: help · history [n] · ? [command] · quit", "dim"))
            self.say(self.paint("  any line without a leading / runs as `skaldir <line>`", "dim"))
            return True
        return False

    def history_table(self, limit: int = 10) -> None:
        entries = self.history.tail(limit)
        if not entries:
            self.say(self.paint("  no commands recorded yet", "dim"))
            return
        self.say(self.paint(f"  last {len(entries)} command(s) {self.glyph('dash')} "
                            f"{self.history.path}", "dim"))
        for entry in entries:
            if entry.cancelled:
                mark, verdict = self.mark("warn"), "cancelled"
            elif entry.exit_code == 0:
                mark, verdict = self.mark("ok"), "ok"
            else:
                mark, verdict = self.mark("fail"), f"exit {entry.exit_code}"
            when = entry.ts[11:19] if len(entry.ts) >= 19 else entry.ts
            self.say(f"  {mark} {when} {entry.line}  {self.paint(verdict, 'dim')}".rstrip())

    # -- the loop -------------------------------------------------------- #
    def run(self) -> dict[str, Any]:
        """Read lines, execute them, keep the session alive; returns the summary."""
        self._quit_requested = False
        if not self.options.quiet:
            self.banner()
            if self.options.resume:
                self.resume_line()
        if self.interactive:
            self.readline_ready = _complete.install_readline(
                self.completer,
                history_file=self._readline_history(),
                display=self._readline_menu,
            )
        while True:
            if self.options.limit and self.commands >= self.options.limit:
                self.say(self.paint(f"  limit of {self.options.limit} command(s) reached", "dim"))
                break
            # an attached run's progress is written before the prompt is drawn, so its lines are
            # never lost behind the frame and the sync is the shell's own thread
            self.pump_feed()
            # one blank line between turns, then the (only-if-changed) status line
            self.turn_open()
            try:
                line = self.read_line()
            except KeyboardInterrupt:
                self.menu_state.dismiss()
                self.warn(self.paint(
                    f"^C input cleared {self.glyph('dash')} nothing was running; "
                    "the session is still here", "warn"))
                continue
            if line is None:
                if not self.options.quiet and not self.interactive:
                    self.say(self.paint("  (end of input)", "dim"))
                break
            try:
                stop = self.dispatch(line)
            except KeyboardInterrupt:  # a Ctrl-C outside a running command
                self.warn(self.paint(
                    f"^C nothing was running {self.glyph('dash')} the session "
                    "is still here", "warn"))
                continue
            except Exception as exc:  # noqa: BLE001 — the shell survives anything
                self.warn(f"[shell] {type(exc).__name__}: {exc}")
                continue
            if stop or self._quit_requested:
                break
        return self.summary()

    def dispatch(self, line: str) -> bool:
        """Handle one input line. Returns True when the session should stop."""
        if "\t" in line:
            line = self.handle_tabs(line)
        text = line.strip()
        if "\x1b" in text:
            # ESC: dismiss the palette and run nothing. Measured: Python's readline keeps
            # ESC as the meta prefix (it cannot be bound), so this is the shell's own key
            # handling for any line that carries the byte — a scripted session, a paste,
            # a terminal whose readline is absent.
            self.dismiss_menu()
            return False
        if not text:
            return False
        if text.startswith("?"):
            self.overlay(_split_tokens(text[1:]))
            return False
        if text.startswith("/"):
            # slash lines first: `/help tools list` is not the same as the bare `/help`
            return self.run_slash(text)
        # CHATBAR hook (additive): while the frame owns the prompt, a plain-language line
        # goes to the agent loop — *before* the trailing-`?` shortcut below, or a question
        # like "how do I add tests?" would open the `?` overlay instead of being answered.
        # A line that starts with a real command is not prose: `route_plain` declines it and
        # it falls through to the paths below (the `?` overlay, the built-ins, the CLI).
        routed = _chatbar.route_plain(self, text) if _chatbar is not None else None
        if routed is not None:
            return routed
        if text.endswith("?"):
            self.overlay(_split_tokens(text[:-1]))
            return False
        if self.handle_builtin(text):
            # a built-in is handled here; only `quit`/`exit` asks the session to stop
            return bool(self._quit_requested)
        return self.run_command(text)

    def _is_computer_use_task(self, invocation: Any, text: str) -> bool:
        """``/computer-use <free-text>`` → the agent run that needs the approve bar.

        Any free-text task (not a sibling verb like ``run``/``subtool``) counts:
        the owner said "open firefox on my live desktop" and expects the slider.
        """
        argv = tuple(str(item) for item in (getattr(invocation, "argv", ()) or ()))
        mode = str(getattr(invocation, "mode", "") or "")
        head = str(text or "").strip().lstrip("/").split()[0] if str(text or "").strip() else ""
        if head not in ("computer-use", "cu"):
            return False
        if mode == "verb":
            return False  # `/computer-use run …` / `subtool …` are the real act verbs
        # bare `/computer-use` (no free text) is `act check`, not a task
        rest = str(text or "").strip().lstrip("/").split(None, 1)
        if len(rest) < 2 or not rest[1].strip():
            return False
        return argv[:2] == ("agent", "run") or mode == "task" or bool(rest[1].strip())

    def run_slash(self, text: str) -> bool:
        invocation = _slash.resolve(text, self.session.tree)
        # /computer-use with a free-text task: open the live-desktop approve bar
        # *before* the agent loop, so the owner sees the slider instead of a refusal.
        if invocation is not None and self._is_computer_use_task(invocation, text):
            try:
                from . import cut as _cut

                front = self._front()
                if front is not None:
                    _cut.install_gate(front)  # Front.pick draws the ←/→ bar
                else:
                    _cut.install_gate(self)
                goal = str(text.split(" ", 1)[-1] if " " in text else "a computer-use action")
                decision = _cut.maybe_approve_live(goal=goal.lstrip("/").split(" ", 1)[-1])
                if (
                    decision and decision.get("prompted")
                    and not decision.get("granted_now") and self.interactive
                ):

                    self.say(self.paint(
                        f"  live desktop: {decision.get('reason', 'refused')}", "warn"))
                    return False
            except Exception:  # noqa: BLE001 — the gate never blocks the verb
                pass
        if _model_picker is not None:
            # MODEL-PICKER's own hook, consulted before the verb table: `/model` opens the
            # provider+model picker *inside the frame* (the same engine as `skaldir model`),
            # a bare `/provider` shows the same list, and `/setup` is pointed at the picker
            # when a mandatory key is missing. `None` means "not my line" — a pipe, a
            # `--json` run or any other slash verb keeps the old path untouched.
            handled = _model_picker.handle_slash(self, text)
            if handled is not None:
                return bool(handled)
        # the picker's own hook, for the same reason: `/sessions` with no argument is a *browse*
        # (the widget), while `/sessions --limit 5` is the real command with its flags — and a
        # hook keeps the mapping (`session list`) intact instead of replacing it with a builtin
        handled = self.handle_picker_verb(text)
        if handled is not None:
            return bool(handled)
        if invocation is None:
            name = text[1:].split()[0] if len(text) > 1 else ""
            if not name:
                # a bare `/` (or `/` + Enter, on a terminal whose readline cannot draw our
                # menu): show the palette — filtered by nothing
                self.open_palette("/")
                return False
            if not self.open_palette(text, note=f"no exact match for "
                                                f"{text.split()[0]!r} — closest rows:"):
                close = _slash.close_names(name)
                self.say(self.paint(f"  no slash command {text.split()[0]!r}"
                                    + (f" {self.glyph('dash')} closest: {', '.join(close)}"
                                       if close else ""), "warn"))
                # The bare route is the one thing that makes a verb-less root usable at all,
                # and nothing said so at the moment it mattered: a refused `/mcp` names the
                # real command in one line instead of leaving the user with a suggestion list.
                rest = text[1:].strip()
                if self._is_real_command(name):
                    self.say(self.paint(
                        f"  `{rest}` (no slash) runs it: {PROMPT_NAME} {rest}", "dim"))
            return False
        if invocation.is_quit:
            return True
        if invocation.command.name == "help" and invocation.extra:
            first = str(invocation.extra[0])
            target = _slash.command_for(first)
            if first.startswith(_slash.SLASH):
                # `/help /tools` describes the slash command itself
                if target is None:
                    close = _slash.close_names(first)
                    hint = (f" {self.glyph('dash')} closest: {', '.join(close)}"
                            if close else "")
                    self.say(self.paint(f"  no slash command {first!r}{hint}", "warn"))
                    return False
                return self._say_slash_help(target)
            # `/help resume` where `resume` is a slash verb and NOT a command of this build:
            # the CLI has no `resume`, so running `resume --help` would only print a usage
            # error. A name that is both (`/help tools`) stays the command's own --help.
            if target is not None and not self._is_real_command(first):
                return self._say_slash_help(target)
            # `/help tools list` runs the command's own `--help` (the real parser text)
            return self.run_command(" ".join(invocation.extra) + " --help")
        if invocation.builtin:
            # a built-in the shell answers itself: help, /init, /diff (order matters: a
            # `/help <something>` line was already handled above)
            return self.run_builtin(invocation)
        entry = next(
            (
                item
                for item in self.palette()
                if item.command.name == invocation.command.name
            ),
            None,
        )
        if entry is not None and entry.command.needs and not invocation.extra:
            # CHATBAR: the verb is real, but its bare form does nothing in this build —
            # `context usage`/`context compact` exit 1 and print nothing at all when they
            # have no transcript. Refusing with the real forms beats an empty result.
            self.say(self.paint(
                f"  {invocation.command.slash} runs `{invocation.command.spelling}`, and that "
                f"needs {entry.command.needs}.", "warn"))
            for form in entry.command.needs_forms:
                self.say(self.paint(f"  {form}", "dim"))
            return False
        if entry is not None and entry.state == _slash.STATE_PLANNED:
            # CHATBAR: a planned verb has no command behind it — the shell says why and
            # names the real commands that cover the ground, and runs nothing.
            self.say(self.paint(
                f"  {invocation.command.slash} is {entry.mark()} — this build has no such "
                "verb, so nothing was run.", "warn"))
            reason = entry.detail or entry.reason
            if reason:
                self.say(self.paint(f"  why: {reason}", "dim"))
            if invocation.command.nearest:
                self.say(self.paint(
                    "  instead: "
                    + ", ".join(f"`{PROMPT_NAME} {item}`"
                                for item in invocation.command.nearest), "dim"))
            return False
        if entry is not None and not entry.runnable:
            self.say(self.paint(
                f"  {invocation.command.slash} is {entry.mark()} — it would run "
                f"`{invocation.command.spelling}`, which this build cannot do.", "warn"))
            if entry.state == _slash.STATE_UNWIRED:
                close = _complete.fuzzy_filter(
                    invocation.command.name, _complete.known_paths(self.session.tree), limit=4
                )
                if close:
                    self.say(self.paint("  closest wired command: " + ", ".join(close), "dim"))
            return False
        if entry is not None and entry.state == _slash.STATE_UNVERIFIED:
            self.say(self.paint(
                f"  note: {entry.mark()} — running "
                f"`{invocation.command.spelling}` anyway; the parser's own answer counts",
                "warn"))
        if entry is not None and entry.state == _slash.STATE_BLOCKED:
            self.say(self.paint(
                f"  note: the selftest flags {entry.layer} ({entry.reason}) — "
                f"running `{invocation.command.spelling}` anyway, its own verdict counts"
                + (f"\n        why: {entry.detail}" if entry.detail else ""), "warn"))
        # the action line in run_command is the honest "this is what runs" line; a second
        # arrow line here would say the same thing twice
        return self.run_command(
            " ".join(invocation.argv), argv=invocation.argv, source_line=text
        )

    # -- the frame's own rows for a running command ------------------------ #

    def _front(self) -> Any:
        """The frame attached to this shell, or ``None`` (a pipe, ``--json``, a test)."""
        return _chatbar.front_of(self) if _chatbar is not None else None

    def front_begin(self, argv: Sequence[str], front: Any = None) -> None:
        """Show the command that is starting in the frame's live row.

        A model call says ``thinking`` (nothing else runs until it answers); every other
        command says ``tool``, which is what it is — one tool of the product, running now.
        The row's own detail is the command, so a reader can see *what* is running without
        the frame ever claiming a step the product did not take.
        """
        frame = front if front is not None else self._front()
        if frame is None:
            return
        children = parallel_children(argv)
        if children is not None:
            # a fan-out is worth its own row: the count is the fact the user asked for
            frame.subagents(children, state="running")
        state = "thinking" if is_model_command(argv) else "tool"
        frame.activity(state, tool_label(argv))

    def front_end(self, argv: Sequence[str], result: RunResult, front: Any = None) -> None:
        """Stamp the finished command as a transcript row: the tool's mark + its measured ms.

        The vocabulary draws the first row (``art.line``: the mark, the name, the measured
        duration, the fail mark when it failed), the badge under it carries the exit code and
        the streamed volume. Every number is this process's own measurement.
        """
        frame = front if front is not None else self._front()
        if frame is None:
            return
        children = parallel_children(argv)
        if children is not None:
            frame.subagents(children, state="done" if result.ok else "incomplete")
        frame.tool_done(
            tool_label(argv),
            detail=" ".join(str(item) for item in argv[1:]) or tool_label(argv),
            status="failed" if not result.ok else "ok",
            exit_code=int(result.exit_code),
            duration_ms=int(result.duration_ms),
            streamed=int(result.bytes_streamed) or None,
        )

    def context_state_dir(self, argv: Sequence[str]) -> tuple[str, ...]:
        """``/context --run-id <id>`` gets the state dir the chat's own runs live in.

        The run ids a chat prints come from ``<runtime>/agent/runs/<id>.jsonl``; ``context
        usage``/``context compact`` resolve a run id against ``--state-dir``, whose default is
        the current directory — so the id the chat itself had just shown was unresolvable
        *from the chat* (measured: exit 5, `no run '<id>' at ./runs/<id>.jsonl`). One path
        resolution, in the one place that knows the runtime: the shell.
        """
        items = tuple(str(item) for item in argv)
        if not items or items[0] != "context":
            return items
        if "--run-id" not in items or "--state-dir" in items:
            return items
        runtime = str(getattr(self.session, "runtime", "") or "")
        if not runtime:
            return items
        # `agent_loop_cli._state_dir`: the group's runs live one level under the runtime
        return (*items, "--state-dir", str(Path(runtime) / "agent"))

    def run_command(self, text: str, argv: Sequence[str] | None = None,
                    source_line: str | None = None) -> bool:
        """One turn: the action line, the command's own (streamed) output, one result line.

        Before anything runs, the two gates get their say: the approval ladder (a gated
        command is judged, the prompt is shown, the answer is recorded) and the diff
        preview (an ``edit apply`` is planned first and shown as a compact diff). A gate
        that answers "nothing may run" ends the turn here — with its own reason on screen.
        """
        argv = tuple(argv) if argv is not None else tuple(_slash.shlex_split(text))
        if not argv:
            return False
        decided = self.prepare(argv)
        if decided is None:
            self.commands += 1
            self.last_exit = EXIT_BLOCKED
            self.record(source_line if source_line is not None else text, argv,
                        RunResult(argv=argv, exit_code=EXIT_BLOCKED))
            return False
        argv = decided
        argv = self.context_state_dir(argv)
        front = self._front()
        chat_turn = tuple(argv[:2]) == ("agent", "run") and "--chat-output" in argv
        if front is not None:
            if chat_turn:
                # a chat turn owns the band's clock/metrics: begin/end around the run
                front.begin_turn("thinking")
            else:
                self.front_begin(argv, front)
            try:
                import os as _os

                _os._skaldir_chat_front = (lambda f=front: f)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        if chat_turn:
            self.say(f"  You: {source_line if source_line is not None else text}")
        else:
            self.say(self.action_line(argv))
        # A frame is up: tell any command that would open its own terminal surface that this
        # one is taken (`skaldir run` opens the attached chat on a tty). Without it the
        # nested frame draws over this one and `run_command` never returns, so the live row
        # outlives the run and no result row is ever stamped.
        marker = (
            _chatbar.frame_owns_terminal(front is not None)
            if _chatbar is not None and hasattr(_chatbar, "frame_owns_terminal")
            else _no_frame_marker()
        )
        with marker:
            result = self.session.execute(argv)
        self.last_result = result
        self.commands += 1
        self.last_exit = result.exit_code
        if front is not None:
            if chat_turn:
                # fold the run's usage into the band (ctx size / tok/s stay pinned)
                try:
                    usage = dict(getattr(result, "data", {}) or {}).get("usage") or {}
                    if not usage:
                        import re as _re

                        text = str(getattr(result, "output", "") or "")
                        found = _re.search(
                            r'"prompt_tokens"\s*:\s*(\d+)', text
                        ) or _re.search(r"prompt_tokens[\"'\s:=]+(\d+)", text)
                        total = _re.search(
                            r'"total_tokens"\s*:\s*(\d+)', text
                        ) or _re.search(r"total_tokens[\"'\s:=]+(\d+)", text)
                        if found:
                            usage = {
                                "prompt_tokens": int(found.group(1)),
                                "total_tokens": int(total.group(1)) if total else 0,
                            }
                    bar = getattr(front, "bar", front)
                    turn = getattr(bar, "turn", None)
                    if usage:
                        prompt_tokens = usage.get("prompt_tokens")
                        total_tokens = usage.get("total_tokens") or usage.get("completion_tokens")
                        if turn is not None:
                            turn.note_usage(prompt_tokens=prompt_tokens, total_tokens=total_tokens)
                        elif hasattr(bar, "_last_ctx"):
                            from .telemetry import format_tokens_short

                            if prompt_tokens:
                                if prompt_tokens:
                                    bar._last_ctx = (
                                        f"ctx ~{format_tokens_short(int(prompt_tokens))} tok"
                                    )

                except Exception:  # noqa: BLE001
                    pass
                front.end_turn()
            else:
                self.front_end(argv, result, front)
        if result.cancelled:
            self.cancelled += 1
            self.warn(self.paint(
                f"^C cancelled `{PROMPT_NAME} {' '.join(argv)}` after {result.duration_ms} ms"
                f" {self.glyph('dash')} the session is still here", "warn"))
        elif result.exit_code != EXIT_OK:
            self.failures += 1
        if result.unexpected:
            self.warn(f"[shell] the command raised {result.unexpected}")
        elif not result.cancelled and (not chat_turn or result.exit_code != EXIT_OK):
            self.say(self.result_line(result.footer(f" {self.glyph('sep')} ")))
        self.record(source_line if source_line is not None else text, argv, result)
        self.settle_oneshot()
        return False

    def record(self, line: str, argv: Sequence[str], result: RunResult) -> None:
        self.history.append(
            HistoryEntry(
                ts=_now_iso(),
                line=line,
                argv=list(argv),
                exit_code=result.exit_code,
                cancelled=result.cancelled,
                duration_ms=result.duration_ms,
                source="slash" if line.strip().startswith("/") else "command",
            )
        )
        if result.output:
            for chunk in result.output.rstrip("\n").splitlines():
                self.transcript.append(chunk)

    def _readline_history(self) -> Path | None:
        try:
            return self.history.path
        except Exception:  # noqa: BLE001
            return None

    def summary(self) -> dict[str, Any]:
        wrote = self.write_transcript()
        report = self.session.report
        return {
            "runtime": str(self.session.runtime),
            "interactive": self.interactive,
            "commands": self.commands,
            "failures": self.failures,
            "cancelled": self.cancelled,
            "last_exit_code": self.last_exit,
            "history_file": str(self.history.path),
            "transcript": wrote or None,
            "started_at": self.session.started_at,
            "layers_probed": None if report is None else len(report.get("layers", [])),
            "layers_not_reachable": None if report is None else report.get("failed"),
            # measured streaming totals for the whole session (0 when nothing streamed,
            # and ``stream_hz: 0`` when this session never paced — never a fabricated rate)
            "stream_hz": float(getattr(self.session, "stream_hz", 0.0) or 0.0),
            "renders": int(getattr(self.session, "renders", 0) or 0),
            "chunks": int(getattr(self.session, "streamed_chunks", 0) or 0),
            "stream_cpu_ms": int(getattr(self.session, "stream_cpu_ms", 0) or 0),
            # the interaction gates: how many approval prompts were answered, and the last
            # edit transaction this session saw (the one ``/diff`` shows)
            "approvals": int(self.approvals),
            "approval_session": self.session_id,
            "last_edit": (self.edit_state() or ({},))[0].get("tx_id", "") or None,
        }


def _split_tokens(text: str) -> list[str]:
    return [token for token in str(text).split() if token]


def history_counts(entries: Iterable[HistoryEntry]) -> dict[str, int]:
    material = list(entries)
    return {
        "commands": len(material),
        "cancelled": sum(1 for entry in material if entry.cancelled),
        "failed": sum(1 for entry in material if not entry.cancelled and entry.exit_code != 0),
    }


# --------------------------------------------------------------------------- #
# CLI wiring — ``register_cli(subparsers)`` is the whole contract
# --------------------------------------------------------------------------- #

_GROUP_EVIDENCE: tuple[dict, ...] = (
    {
        "ref": "src/skaldir/repl.py:1",
        "kind": "code",
        "note": "the shell runs every command through skaldir.cli.main — the one entry point",
    },
    {
        "ref": "src/skaldir/slash.py:1",
        "kind": "code",
        "note": "each slash command names the real command group it runs",
    },
)


def contract_entries() -> dict[str, Any]:
    """The ``cli_contract.COMMANDS`` entry for ``shell`` (registered at CLI build time)."""
    spec = getattr(_CONTRACT, "CommandSpec", None)
    if spec is None:
        return {}
    return {
        "shell": spec(
            "kernel",
            (
                {
                    "ref": "src/skaldir/repl.py:1",
                    "kind": "code",
                    "note": "Shell.run() — the session loop over the real CLI",
                },
                {
                    "ref": "src/skaldir/slash.py:1",
                    "kind": "code",
                    "note": "the slash palette maps onto existing command groups",
                },
            ),
            "interactive shell: session state, slash palette, completion, streaming",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_INTERNAL),
            mutates=True,
            data_schema={
                "type": "object",
                "properties": {
                    "runtime": {"type": "string"},
                    "interactive": {"type": "boolean"},
                    "commands": {"type": "integer"},
                    "failures": {"type": "integer"},
                    "cancelled": {"type": "integer"},
                    "last_exit_code": {"type": ["integer", "null"]},
                    "history_file": {"type": "string"},
                    "transcript": {"type": ["string", "null"]},
                    "started_at": {"type": "string"},
                    "layers_probed": {"type": ["integer", "null"]},
                    "layers_not_reachable": {"type": ["integer", "null"]},
                    "stream_hz": {
                        "type": "number",
                        "description": "the terminal's render bound in renders/second "
                        "(0 = this session was not paced, e.g. a pipe)",
                    },
                    "renders": {
                        "type": "integer",
                        "description": "writes that reached the terminal while streaming",
                    },
                    "chunks": {
                        "type": "integer",
                        "description": "chunks accepted from the commands' output",
                    },
                    "stream_cpu_ms": {
                        "type": "integer",
                        "description": "process CPU (user+system) spent inside commands, ms",
                    },
                    "approvals": {
                        "type": "integer",
                        "description": "approval prompts this session answered (allow/deny)",
                    },
                    "approval_session": {
                        "type": "string",
                        "description": "the session id grants are filed under",
                    },
                    "last_edit": {
                        "type": ["string", "null"],
                        "description": "the newest edit transaction in this runtime "
                        "(`/diff` shows it), or null when there is none",
                    },
                },
                "required": ["runtime", "commands", "history_file"],
            },
            notes="the shell executes whatever is typed through the product CLI: a bare "
            "line becomes `skaldir <line>`, a `/…` line is a real command group alias. "
            "A command the build does not wire is refused with its reason (never a "
            "guessed verb). Ctrl-C cancels the running command (the record marks it "
            "cancelled; the session continues). Commands are appended to "
            "<runtime>/shell/history.jsonl; --resume restores that state. Two gates run "
            "first: `approval run` is judged by the approval ladder and its answer is "
            "recorded through `approval allow`/`deny` (allow-once is a one-shot session "
            "grant; a non-interactive session never prompts and keeps the CLI's exit 3), "
            "and `edit apply` is previewed with a real `edit dry-run` before it applies. "
            "`/init` writes a project AGENTS.md; `/diff` renders the last transaction",
        )
    }


def register_contract_entries() -> list[str]:
    """Add the ``shell`` contract entry (idempotent); returns the paths added."""
    entries = contract_entries()
    if not entries or _CONTRACT is None:
        return []
    added: list[str] = []
    for path, entry in entries.items():
        if path not in _CONTRACT.COMMANDS:
            _CONTRACT.COMMANDS[path] = entry  # type: ignore[union-attr]
            added.append(path)
    return added


def add_shell_parser(subparsers: Any) -> Any:
    """Add the ``shell`` command to a subparsers handle (idempotent)."""
    existing = getattr(subparsers, "choices", {}).get("shell")
    if existing is not None:
        return existing
    parser = subparsers.add_parser(
        "shell",
        help="interactive shell: slash palette, tab completion, streaming output",
        description="Interactive Skaldir shell (session state, / palette, completion).",
    )
    parser.add_argument("--runtime", default="", help="runtime dir override")
    parser.add_argument("--resume", action="store_true",
                        help="restore the previous session's state and print a summary")
    parser.add_argument("--transcript", default="",
                        help="write the raw session transcript to this path")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after N commands (0 = no limit; scripted safety)")
    parser.add_argument("--color", action="store_true",
                        help="force ANSI colour even when stdout is not a TTY")
    parser.add_argument("--no-color", dest="no_color", action="store_true",
                        help="never emit colour or unicode (the plain, ASCII look)")
    parser.add_argument("--no-probe", dest="probe", action="store_false",
                        help="skip the startup selftest probe (the hint line then says so)")
    parser.add_argument("--no-echo", dest="echo", action="store_false", default=None,
                        help="do not echo the prompt for lines read from a pipe")
    parser.add_argument("--no-footer", dest="footer", action="store_false",
                        help="do not print the session footer line after a turn")
    parser.add_argument("--no-activity", dest="activity", action="store_false",
                        help="do not draw the streaming activity indicator")
    parser.add_argument("--activity-hz", dest="activity_hz", type=float, default=None,
                        help="how often the activity clock redraws, in hertz "
                             "(default: every 0.5 s)")
    parser.add_argument("--stream-hz", dest="stream_hz", type=float, default=None,
                        metavar="HZ",
                        help="renders per second for a streamed answer on this terminal "
                             "(default: 30 on an interactive terminal, never when the "
                             "output is a pipe; 0 disables pacing)")
    parser.add_argument("--no-approval-prompt", dest="approval", action="store_false",
                        help="never ask for approval: a gated command keeps the CLI's "
                             "headless answer (exit 3)")
    parser.add_argument("--no-preview", dest="preview", action="store_false",
                        help="do not preview an edit transaction before it applies")
    parser.add_argument("--session", default="",
                        help="the session id grants are filed under (default: shell-<pid>)")
    parser.add_argument("--attach", default="",
                        help="the goal id this session is the surface of: the status line shows "
                             "*that* run's goal, and /steer + /queue deliver to it")
    parser.set_defaults(func=_cmd_shell)
    return parser


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``.

    Idempotent (a second call returns the existing group instead of letting argparse
    raise ``conflicting subparser``), and it registers this group's
    ``cli_contract.COMMANDS`` entry so a wired-in ``shell`` never ships without a
    contract. Nothing here edits ``cli.py``.
    """
    existing = getattr(subparsers, "choices", {}).get("shell")
    if existing is not None:
        register_contract_entries()
        _register_chat_alias(subparsers)
        return existing
    parser = add_shell_parser(subparsers)
    register_contract_entries()
    _register_chat_alias(subparsers)
    return parser


def _register_chat_alias(subparsers: Any) -> Any:
    """``skaldir chat`` rides this group's own registration hook.

    ``cli.py`` is owned elsewhere, so the alias does not wait on it: this module's
    ``register_cli`` already receives the subparsers object, and ``chatbar.add_chat_parser``
    is idempotent — a second registration (the bar's own engineering record, ``62-chatbar.md``
    under ``~/Documents/Hermes_Skaldir_Agent_Docs/MVP/``; the repo carries no ``MVP/`` tree)
    returns the existing parser instead of raising ``conflicting subparser``.
    """
    if _chatbar is None:
        return None
    try:
        return _chatbar.add_chat_parser(subparsers)
    except Exception:  # noqa: BLE001 — a shell without its alias still works
        return None


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with this group's verbs (tests / ``python -m skaldir.repl``)."""
    parser = argparse.ArgumentParser(prog="skaldir",
                                     description="Interactive Skaldir shell")
    sub = parser.add_subparsers(dest="command")
    add_shell_parser(sub)
    _register_chat_alias(sub)
    return parser


def _cmd_shell(args: argparse.Namespace) -> int:
    """``skaldir shell`` — run the interactive session and report it honestly."""
    options = ShellOptions(
        runtime=str(getattr(args, "runtime", "") or "") or None,
        resume=bool(getattr(args, "resume", False)),
        echo=getattr(args, "echo", None),
        transcript=str(getattr(args, "transcript", "") or "") or None,
        limit=int(getattr(args, "limit", 0) or 0),
        color=bool(getattr(args, "color", False)),
        no_color=bool(getattr(args, "no_color", False)),
        probe=bool(getattr(args, "probe", True)),
        stream_hz=getattr(args, "stream_hz", None),
        footer=bool(getattr(args, "footer", True)),
        activity=bool(getattr(args, "activity", True)),
        activity_hz=getattr(args, "activity_hz", None),
        approval=bool(getattr(args, "approval", True)),
        preview=bool(getattr(args, "preview", True)),
        session_id=str(getattr(args, "session", "") or ""),
        attach=str(getattr(args, "attach", "") or ""),
    )
    json_mode = False
    if _CONTRACT is not None:
        try:
            json_mode = bool(_CONTRACT.json_requested(args))
        except Exception:  # noqa: BLE001
            json_mode = False
    options.quiet = bool(getattr(args, "quiet", False) or json_mode)
    if options.quiet:
        # a machine-readable stream has no room for a prompt or a decorative line
        options.footer = False
        options.activity = False
        options.approval = False
        options.preview = False
    shell = Shell(options)
    summary = shell.run()
    if _CONTRACT is not None:
        try:
            _CONTRACT.set_data(summary)
            _CONTRACT.add_evidence(
                str(summary["history_file"]), "file", "the session's command history"
            )
            _CONTRACT.add_evidence(
                f"src/skaldir/repl.py:{inspect.getsourcelines(Shell.run)[1]}",
                "code",
                "Shell.run() — the loop that produced this summary",
            )
            if summary.get("transcript"):
                _CONTRACT.add_evidence(
                    str(summary["transcript"]), "file", "the raw session transcript"
                )
        except Exception:  # noqa: BLE001 — the envelope must not break the verb
            pass
    if summary["commands"] and summary["failures"] == summary["commands"]:
        return EXIT_BLOCKED
    return EXIT_OK


def launch_from_bare_call(no_color: bool = False, *, runtime: str = "", attach: str = "",
                          limit: int = 0) -> int:
    """``skaldir`` with no arguments: **the chat** for a human, the welcome otherwise.

    Called from the CLI's ``main()`` when no sub-command was given. A pipe, a redirection or a
    non-TTY keeps the existing welcome text — a chat that hangs an automated caller is worse than
    no chat. On a real terminal the bare call is the *same session* ``skaldir chat`` runs (one
    implementation, so the two doors cannot drift); ``skaldir shell`` stays as the frame-less
    door, and ``skaldir chat`` stays as a working alias.
    """
    from .cli import _print_welcome  # the existing welcome, unchanged

    stdin_tty = bool(getattr(sys.stdin, "isatty", lambda: False)())
    stdout_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    if not (stdin_tty and stdout_tty):
        _print_welcome(no_color=no_color)
        return EXIT_OK
    if _chatbar is not None and hasattr(_chatbar, "launch_from_bare_call"):
        return int(_chatbar.launch_from_bare_call(
            no_color=no_color, runtime=runtime, attach=attach, limit=int(limit or 0)
        ))
    return _cmd_shell(  # a build without the chat keeps the frame-less shell
        argparse.Namespace(
            runtime=runtime, resume=False, transcript="", limit=int(limit or 0), color=False,
            no_color=no_color, probe=True, echo=None, quiet=False, attach=attach,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m skaldir.repl`` — the shell without the product CLI wiring.

    ``python -m skaldir.repl`` with no argument is the shell itself; ``python -m
    skaldir.repl --runtime X --resume`` passes the flags through to it.
    """
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not raw or raw[0].startswith("-"):
        args = parser.parse_args(["shell", *raw])
    else:
        args = parser.parse_args(raw)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    return int(func(args))


__all__ = [
    "EXIT_CANCELLED",
    "History",
    "HistoryEntry",
    "Painter",
    "Probes",
    "RunResult",
    "Session",
    "Shell",
    "ShellOptions",
    "Tee",
    "add_shell_parser",
    "build_parser",
    "color_enabled",
    "contract_entries",
    "default_runner",
    "history_counts",
    "launch_from_bare_call",
    "load_theme",
    "main",
    "register_cli",
    "register_contract_entries",
]


if __name__ == "__main__":  # pragma: no cover — the standalone shell
    raise SystemExit(main())
