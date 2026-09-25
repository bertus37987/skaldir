"""Token-level streaming into the terminal: coalesced, incremental, measurable.

The gap this module closes is the one the owner feels first. ``client.chat()`` used to
return a *whole* answer, so the first visible character of an ``agent run`` appeared when
the **model had finished** — 1.3-3.7 s against a stub, minutes against a real provider.
The provider client has always been able to stream (``stream: true``, the SSE reader in
:meth:`skaldir.llm_provider.OpenAICompatibleLLM._read_chat_stream`); what was missing is
the **sink**: every delta was folded into the final result and thrown away on the way to
the screen. This module is that sink, and it is deliberately the last one in the chain —
the loop hands it text, it decides what the terminal needs to see and when.

Design transferred from **Cline** (Apache-2.0 — credited in ``THIRD_PARTY_NOTICES.md``),
studied read-only at ``sdk/packages/core/src/extensions/tools/executors/bash.ts``
(``createCommandProgressEmitter``) and ``apps/cli/src/tui`` (``use-agent-events``):

* **one update per interval** — Cline coalesces a command's output into a single UI update
  per ``COMMAND_PROGRESS_FLUSH_INTERVAL_MS`` (48 ms); here the bound is
  :data:`DEFAULT_INTERVAL_MS` (60 ms) and it is enforced by the clock, because the sink is
  called from a synchronous handler with no event loop to run a timer on;
* **the first delta renders immediately** — Cline's timer also delays the first update by
  up to one interval. On a terminal the first byte *is* the felt latency, so a delay there
  would give away the whole point of the change;
* **never re-render what did not change** — Cline's reconciler returns untouched entries as
  the same object so the renderer skips them. A terminal has no reconciler, so the rule is
  stricter: this renderer only ever **appends** new text. The single line it rewrites in
  place is its own activity footer, and it clears exactly that line before every append.

Honesty rules, enforced in code rather than left to a caller:

* **The token count is labelled.** No tokenizer is bundled, so ``tokens_estimate`` is
  ``chars // 4`` with :data:`TOKEN_METHOD` naming the method; the *exact*, measured number
  is ``deltas`` (SSE content chunks). Both are reported, and the footer shows both.
* **The elapsed time is measured**, from ``arm()`` (the moment before the request goes
  out) to the render, with an injected clock — never a guess.
* **A sink that raises never changes a run.** Every write is wrapped; failures are counted
  in ``write_errors`` and reported.
* **A pipe gets no escapes and no per-chunk noise.** With ``tty=False`` (or ``plain=True``)
  the deltas are accumulated and written **once**, at the end, byte-identical to what the
  non-streaming path would have printed; only the measured numbers are kept.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import render as render_mod

__all__ = [
    "DEFAULT_INTERVAL_MS",
    "MIN_INTERVAL_MS",
    "TOKEN_METHOD",
    "CLEAR_LINE",
    "DEFAULT_HEAD_LINES",
    "DEFAULT_TAIL_FLUSH_MS",
    "DEFAULT_TAIL_FLUSH_CHARS",
    "LONG_OUTPUT_HINT_TAIL",
    "MARKDOWN_TOKENS",
    "MarkdownStream",
    "StreamRenderer",
    "enabled_for",
    "safe_prefix",
    "render_inline",
    "code_line",
    "render_code_block",
    "bounded_output",
    "terminal_columns",
]

#: One terminal update per this many milliseconds. Cline's own bound is 48 ms
#: (``COMMAND_PROGRESS_FLUSH_INTERVAL_MS``); 60 ms is the same shape on a terminal, where
#: ~16 updates a second already read as continuous text (``MVP/53-cline-perf.md`` §3).
DEFAULT_INTERVAL_MS = 60

#: Below this, pacing stops being a bound and starts being a throttle.
MIN_INTERVAL_MS = 16

#: What the token number is. There is no tokenizer in the dependency set, so the number is
#: an estimate and says so wherever it is shown.
TOKEN_METHOD = "chars/4"

#: The one escape this module emits: return to column 0 and erase the current line. Used
#: **only** on the activity footer, and only on a stream that is a terminal.
CLEAR_LINE = "\r\x1b[K"


def enabled_for(
    stream: Any = None,
    *,
    json_mode: bool = False,
    quiet: bool = False,
    requested: bool | None = None,
) -> bool:
    """Whether the answer should stream into this stream.

    ``requested`` is the explicit wish (``--stream`` / ``--no-stream``): ``True`` forces
    it even on a pipe (useful in a test and for a pager), ``False`` refuses it even on a
    terminal. With no explicit wish the decision is the honest default: a **terminal and
    only a terminal**, never in ``--json`` (stdout carries exactly one envelope) and never
    in ``--quiet``.
    """
    if json_mode:
        return False
    if requested is not None:
        return bool(requested)
    if quiet:
        return False
    target = sys.stdout if stream is None else stream
    try:
        return bool(target.isatty())
    except Exception:  # noqa: BLE001 — a stream without isatty is not a terminal
        return False


@dataclass
class StreamMetrics:
    """What the render path measured. Every number names its own source."""

    #: monotonic seconds spent between :meth:`StreamRenderer.arm` and the first delta.
    first_content_ms: float | None = None
    #: monotonic seconds between ``arm()`` and the first *write* to the terminal.
    first_update_ms: float | None = None
    #: content chunks accepted (exact — one per SSE content delta the provider sent).
    deltas: int = 0
    #: characters accepted.
    chars: int = 0
    #: writes to the terminal carrying content (the bound is ``interval_ms``).
    updates: int = 0
    #: activity-footer repaints (never counted as content updates).
    footer_updates: int = 0
    #: bytes handed to the stream, escapes and newlines included (measured).
    bytes_written: int = 0
    #: deltas that arrived inside the interval and were folded into the next update.
    coalesced: int = 0
    #: write failures swallowed (a closed terminal must not break a run).
    write_errors: int = 0
    #: process CPU time (user+sys) spent inside the render path.
    cpu_ms: int = 0
    #: longest gap between two deltas, in ms — the silence a user actually sat through.
    longest_gap_ms: float | None = None
    #: whether the terminal path was used at all (False = accumulate-and-print-once).
    paced: bool = False
    interval_ms: int = DEFAULT_INTERVAL_MS
    tty: bool = False
    elapsed_ms: int = 0
    #: rows an *answer* line committed to the screen (a ``\n``-terminated line arrived).
    lines_written: int = 0
    #: answer rows that were visible and then **changed** — extended after the eye had
    #: already read them, or taken back and written again. The goal is 0: an answer row is
    #: written once and never revisited.
    lines_changed: int = 0
    #: of :attr:`lines_changed`, the harsh kind: a row that was erased and written again
    #: rather than merely grown (a redraw is visible even on a slow terminal).
    lines_redrawn: int = 0
    #: rewrites of the renderer's own chrome (the activity footer, the status band). Chrome
    #: is a row the user understands as live, and it is reported apart from the answer so
    #: "the answer never flickers" cannot be confused with "something flickered".
    chrome_rewrites: int = 0
    #: rows the chrome committed (the status band's own row).
    chrome_rows: int = 0
    #: the status band written once at the end of the turn (0 or 1).
    status_band_updates: int = 0
    #: whether the screen model ran — the flicker numbers are only real when it did.
    screen_measured: bool = False

    @property
    def updates_per_s(self) -> float:
        elapsed = self.elapsed_ms / 1000.0
        return round(self.updates / elapsed, 1) if elapsed > 0 else 0.0

    @property
    def flicker_ratio(self) -> float:
        """Changed answer rows per written answer row. 0.0 is the goal."""
        if not self.lines_written:
            return 0.0
        return round(self.lines_changed / self.lines_written, 3)

    @property
    def deltas_per_update(self) -> float:
        return round(self.deltas / self.updates, 1) if self.updates else 0.0

    @property
    def tokens_estimate(self) -> int | None:
        """``chars//4`` — ``None`` when nothing (or too little) streamed, never a fake 0."""
        if self.chars < 4:
            return None
        return self.chars // 4

    @property
    def seconds_to_first_content(self) -> float | None:
        if self.first_content_ms is None:
            return None
        return round(self.first_content_ms / 1000.0, 3)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe facts, with the token number's method and source named."""
        out: dict[str, Any] = {
            "streamed": self.deltas > 0,
            "paced": bool(self.paced),
            "tty": bool(self.tty),
            "interval_ms": int(self.interval_ms),
            "deltas": int(self.deltas),
            "chars": int(self.chars),
            "updates": int(self.updates),
            "footer_updates": int(self.footer_updates),
            "updates_per_s": self.updates_per_s,
            "deltas_per_update": self.deltas_per_update,
            "bytes_written": int(self.bytes_written),
            "coalesced": int(self.coalesced),
            "write_errors": int(self.write_errors),
            "cpu_ms": int(self.cpu_ms),
            "elapsed_ms": int(self.elapsed_ms),
            "first_content_ms": self.first_content_ms,
            "first_update_ms": self.first_update_ms,
            "longest_gap_ms": self.longest_gap_ms,
            "tokens_estimate": self.tokens_estimate,
            "token_method": TOKEN_METHOD,
            "token_source": "estimate",
            "lines_written": int(self.lines_written),
            "lines_changed": int(self.lines_changed),
            "lines_redrawn": int(self.lines_redrawn),
            "flicker_ratio": self.flicker_ratio,
            "chrome_rewrites": int(self.chrome_rewrites),
            "chrome_rows": int(self.chrome_rows),
            "status_band_updates": int(self.status_band_updates),
            "screen_measured": bool(self.screen_measured),
        }
        if self.deltas <= 0:
            out["reason"] = "no content delta reached the renderer"
        return out


class StreamRenderer:
    """The sink a streaming model answer is written to, one bounded update at a time.

    Wiring (what the loop does)::

        renderer = StreamRenderer(interval_ms=60)
        renderer.arm()                     # just before the request goes out
        loop.run(task, on_delta=renderer.delta)
        facts = renderer.finish()          # flush the tail, clear the footer, measure

    ``delta`` is the *only* callback the model path needs, and it is called for **every**
    content chunk — the renderer decides what reaches the screen. It never raises.
    """

    def __init__(
        self,
        stream: Any = None,
        *,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        tty: bool | None = None,
        theme: Any = None,
        clock: Callable[[], float] = time.monotonic,
        cpu_clock: Callable[[], float] = time.process_time,
        indent: int = 2,
        footer: bool = True,
        columns: int | None = None,
        label: str = "streaming",
    ) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.interval_ms = max(MIN_INTERVAL_MS, int(interval_ms)) if interval_ms else 0
        self.theme = theme
        self.indent = max(0, int(indent))
        self.footer = bool(footer)
        self.label = str(label)
        self.columns = columns
        self._clock = clock
        self._cpu_clock = cpu_clock
        if tty is None:
            try:
                tty = bool(self.stream.isatty())
            except Exception:  # noqa: BLE001
                tty = False
        self.tty = bool(tty)
        self.plain = not self.tty
        #: everything the model sent, whether or not it has been written yet — the caller
        #: uses it to decide whether reprinting the final answer would duplicate it
        self.text = ""
        self.metrics = StreamMetrics(interval_ms=int(self.interval_ms or 0), tty=self.tty)
        self._pending: list[str] = []
        self._armed_at: float | None = None
        self._last_delta_at: float | None = None
        self._last_update_at: float | None = None
        self._footer_visible = False
        #: whether the last content byte written was a newline — i.e. whether the cursor is
        #: at column 0 and another writer (the progress line, the result block) may write
        #: without landing inside the answer
        self._ends_line = True
        self._cpu_at_arm: float | None = None
        self._lock = threading.RLock()
        self._closed = False
        #: the screen model (see :class:`_Screen`) — ``None`` means the flicker numbers are
        #: not measured and are reported as 0 with ``screen_measured=False``
        self._screen: _Screen | None = None

    # -- the clock ---------------------------------------------------------- #

    def arm(self, *, label: str = "") -> None:
        """Start measuring: call this **immediately before** the request is sent.

        Resets the counters so a multi-step run measures one model call, not the session.
        """
        with self._lock:
            self._armed_at = self._clock()
            self._cpu_at_arm = self._cpu_clock()
            self._last_delta_at = None
            self._last_update_at = None
            self._pending.clear()
            self.text = ""
            self._closed = False
            if label:
                self.label = str(label)
            self.metrics = StreamMetrics(interval_ms=int(self.interval_ms or 0), tty=self.tty)
            self._footer_visible = False
            if self._screen is not None:
                self._screen.reset()

    def _elapsed_ms(self) -> float:
        if self._armed_at is None:
            return 0.0
        return max(0.0, (self._clock() - self._armed_at) * 1000.0)

    # -- the sink ----------------------------------------------------------- #

    def delta(self, text: str) -> None:
        """One content chunk from the model. Never raises, never blocks on the clock.

        The first chunk is written at once (the felt latency); later chunks are written at
        most once per :attr:`interval_ms`. Everything is accumulated either way, so the
        caller can hand back the whole answer.
        """
        if not text:
            return
        try:
            with self._lock:
                if self._closed:
                    return
                now = self._clock()
                self.text += text
                self.metrics.deltas += 1
                self.metrics.chars += len(text)
                if self.metrics.first_content_ms is None:
                    self.metrics.first_content_ms = round(self._elapsed_ms(), 3)
                if self._last_delta_at is not None:
                    gap = (now - self._last_delta_at) * 1000.0
                    if gap > (self.metrics.longest_gap_ms or 0.0):
                        self.metrics.longest_gap_ms = round(gap, 1)
                self._last_delta_at = now
                self._pending.append(text)
                if self.plain:
                    return  # a pipe is written once, in finish()
                due = (
                    self._last_update_at is None
                    or not self.interval_ms
                    or (now - self._last_update_at) * 1000.0 >= self.interval_ms
                )
                if due:
                    self._update_locked(now)
                else:
                    self.metrics.coalesced += 1
        except Exception:  # noqa: BLE001 — the sink must never break a run
            self.metrics.write_errors += 1

    def close_call(self) -> None:
        """One model call ended: write the tail and leave the row clean (a TTY only).

        Two things happen here, and the second one was measured the hard way. The pending
        tail is written **now** — not at the end of the run — so the terminal has the whole
        answer before any other line is printed. Then, on a terminal, an answer that did not
        end with a newline gets one: the progress line ("answer 2276 ms · …") is written by
        a *second* writer on the same pty, and without this it landed **inside** the answer's
        last line (measured on the pty: 63 of 64 answer lines arrived intact). A pipe gets no
        such byte — there is no second writer to protect and its output must stay exactly
        what the model produced.

        Honest limit: this closes the row for the writers *we* own. A tool that prints a
        partial line of its own has the same exposure and is not covered here.
        """
        with self._lock:
            if self._closed:
                return
            self.update()
            if self.tty and self.metrics.chars and not self._ends_line:
                if self._write("\n") is not None:
                    self._ends_line = True

    def update(self) -> None:
        """Force one update now (a turn boundary, a caller that wants the tail early)."""
        with self._lock:
            if self._pending and not self.plain:
                self._update_locked(self._clock())

    # -- writing ------------------------------------------------------------ #

    def _take_pending(self) -> str:
        """Everything that is ready to be written **right now**.

        The base renderer has no opinion: every chunk that arrived is ready, and the write
        is exactly what the model sent. :class:`MarkdownStream` overrides this to hand back
        only what it can prove is renderable — see its docstring for why a terminal and a
        pipe must not share the answer here.
        """
        text = "".join(self._pending)
        self._pending.clear()
        return text

    def _update_locked(self, now: float) -> None:
        """Append the pending text; clear the footer first if it is on the screen."""
        text = self._take_pending()
        if not text:
            return
        prefix = CLEAR_LINE if self._footer_visible else ""
        if prefix:
            self._footer_visible = False
        written = self._write(prefix + text)
        if written is None:
            return
        self._ends_line = text.endswith("\n")
        self.metrics.updates += 1
        self.metrics.elapsed_ms = int(self._elapsed_ms())
        if self.metrics.first_update_ms is None:
            self.metrics.first_update_ms = round(self._elapsed_ms(), 3)
        self._last_update_at = now
        self.metrics.cpu_ms = self._cpu_ms()
        if self.footer and text.endswith("\n"):
            self._draw_footer_locked()

    def _draw_footer_locked(self) -> None:
        """One line, rewritten in place, from measured facts — the streaming activity line.

        Drawn only when the content just written ended a line: the footer owns a row of its
        own, so it can never land inside a line the model was in the middle of
        (``MVP/53-cline-perf.md`` §6 measured exactly that defect).
        """
        line = self.activity_line()
        if not line:
            return
        written = self._write(CLEAR_LINE + line, chrome=True)
        if written is None:
            return
        self._footer_visible = True
        self.metrics.footer_updates += 1
        self.metrics.cpu_ms = self._cpu_ms()

    def _write(self, text: str, *, chrome: bool = False) -> int | None:
        """One write, measured. Returns the byte count, or ``None`` when it failed.

        ``chrome=True`` marks the renderer's own furniture (the activity footer, the status
        band) so the screen model can report its rewrites apart from the answer's. The
        distinction is the whole honesty of the flicker number: a live footer *is* a row
        that changes, and pretending otherwise would make the answer look better than it is.
        """
        try:
            self.stream.write(text)
            self.stream.flush()
        except Exception:  # noqa: BLE001 — a closed terminal is not a command failure
            self.metrics.write_errors += 1
            return None
        if self._screen is not None:
            self._screen.feed(text, chrome=chrome)
            self.metrics.lines_written = self._screen.rows_written
            self.metrics.lines_changed = self._screen.rows_changed
            self.metrics.lines_redrawn = self._screen.rows_redrawn
            self.metrics.chrome_rewrites = self._screen.chrome_rewrites
            self.metrics.chrome_rows = self._screen.chrome_rows
            self.metrics.screen_measured = True
        size = len(text.encode("utf-8", "replace"))
        self.metrics.bytes_written += size
        return size

    def _cpu_ms(self) -> int:
        if self._cpu_at_arm is None:
            return self.metrics.cpu_ms
        try:
            return int(max(0.0, (self._cpu_clock() - self._cpu_at_arm) * 1000.0))
        except Exception:  # noqa: BLE001
            return self.metrics.cpu_ms

    # -- the activity line -------------------------------------------------- #

    def activity_line(self) -> str:
        """``streaming · 2.4 s · 128 deltas · ~32 tok (est., chars/4)`` — measured only.

        The elapsed time comes from the armed clock, the delta count from what really
        arrived, and the token number is labelled an estimate with its method. A fact that
        has not been measured is omitted rather than printed as a placeholder.
        """
        facts: dict[str, Any] = {}
        elapsed = self.metrics.elapsed_ms / 1000.0
        if elapsed > 0:
            facts["s"] = f"{elapsed:.1f}"
        if self.metrics.deltas:
            facts["deltas"] = self.metrics.deltas
        tokens = self.metrics.tokens_estimate
        if tokens:
            facts["tok"] = f"~{tokens} (est. {TOKEN_METHOD})"
        if self.metrics.updates:
            facts["updates"] = self.metrics.updates
        theme = self.theme
        if theme is None:
            try:
                from . import theme as theme_mod

                theme = theme_mod.get()
            except Exception:  # noqa: BLE001
                theme = None
        line = render_mod.activity_line(
            self.label, facts, theme=theme, columns=self.columns, indent=self.indent
        )
        return line

    # -- closing ------------------------------------------------------------ #

    def finish(self) -> dict[str, Any]:
        """Flush the tail, drop the footer and return the measured facts.

        On a pipe this is where the whole answer is written — once, exactly as the
        non-streaming path would have printed it, and with no escape sequence anywhere.
        """
        with self._lock:
            if self._closed:
                return self.metrics.as_dict()
            if self.plain:
                text = self._take_pending()
                if text:
                    if self._write(text) is not None:
                        self.metrics.updates += 1
                        self.metrics.first_update_ms = self.metrics.first_update_ms or 0.0
            else:
                self.update()
                if self._footer_visible:
                    self._write(CLEAR_LINE, chrome=True)
                    self._footer_visible = False
                self.close_call()
            self.metrics.elapsed_ms = int(self._elapsed_ms())
            self.metrics.cpu_ms = self._cpu_ms()
            self.metrics.paced = bool(self.tty)
            self._closed = True
            return self.metrics.as_dict()

    def facts_line(self) -> str:
        """The one end-of-stream line: first content, duration, updates, volume.

        Deliberately *not* the answer: the answer is already on the screen above it. Every
        number here is measured (see :class:`StreamMetrics`), and a number that was not
        measured is omitted.
        """
        m = self.metrics
        bits: list[str] = []
        if m.first_content_ms is not None:
            bits.append(f"first token at {m.first_content_ms / 1000.0:.2f} s")
        if m.elapsed_ms:
            bits.append(f"{m.elapsed_ms / 1000.0:.2f} s total")
        if m.deltas:
            bits.append(f"{m.deltas} deltas")
        tokens = m.tokens_estimate
        if tokens:
            bits.append(f"~{tokens} tok ({TOKEN_METHOD})")
        if m.updates:
            bits.append(f"{m.updates} updates ({m.updates_per_s}/s)")
        if m.chars:
            bits.append(f"{m.chars} chars")
        if m.bytes_written:
            bits.append(f"{m.bytes_written} B written")
        if m.write_errors:
            bits.append(f"{m.write_errors} write error(s)")
        if not bits:
            return ""
        mark = render_mod.status_line(
            "ok", "streamed", theme=self.theme, indent=self.indent, muted=True
        )
        return f"{mark} {' · '.join(bits)}"

    def as_dict(self) -> dict[str, Any]:
        return self.metrics.as_dict()

    def end_turn(self, *, status: bool = True) -> dict[str, Any]:
        """Close the turn: the tail, a fresh line, and **one** status band.

        The band is written into the row the activity footer was using — the footer is
        cleared first, so the final numbers land where the eye already is: no extra row, no
        redraw of the answer above them, one write. ``status_band_updates`` reports how
        many times that happened; it is 1, or 0 when there was nothing to say.
        """
        facts = self.finish()
        line = self.facts_line()
        if status and self.tty and self.metrics.chars and line:
            if self._write(CLEAR_LINE + line + "\n", chrome=True) is not None:
                self.metrics.status_band_updates += 1
                facts = self.metrics.as_dict()
        return facts


# --------------------------------------------------------------------------- #
# markdown-lite: what a streaming answer looks like while it is still arriving
# --------------------------------------------------------------------------- #

#: how many body rows of a huge tool return are printed before the head is cut.
DEFAULT_HEAD_LINES = 12

#: how a cut head ends. The tail names the commands that show the rest, so the line is
#: actionable rather than apologetic — and the count is the caller's measurement, never
#: a rounded-away guess.
LONG_OUTPUT_HINT_TAIL = "skaldir /diff or /export"

#: The whole vocabulary, spelled once. A caller can state it; a test can pin it; a doc can
#: be checked against it. Anything not on this list is printed as the character it is.
MARKDOWN_TOKENS = ("**bold**", "`code`", "- item", "1. item", "# heading", "``` fence")

_CODE = "`"
_BOLD = "**"
_MARKERS = (_CODE, _BOLD)

#: A fence line: three or more backticks (or tildes), an optional language, nothing else.
_FENCE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<marker>`{3,}|~{3,})[ \t]*(?P<lang>[^\s`~]*)[ \t]*$"
)
_HEADING_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<hashes>#{1,6})[ \t]+(?P<body>.*?)[ \t]*$")
_BULLET_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<marker>[-*+])[ \t]+(?P<body>.*)$")
_ORDERED_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<number>\d{1,3})[.)][ \t]+(?P<body>.*)$")
_QUOTE_RE = re.compile(r"^(?P<indent>[ \t]*)[>][ \t]?(?P<body>.*)$")

#: Narrowest body a wrapped code row may be given before wrapping stops helping.
MIN_CODE_COLUMNS = 20


def _resolve_theme(theme: Any) -> Any:
    """The caller's theme, else the process theme, else ``None`` — never a crash."""
    if theme is not None:
        return theme
    try:
        from . import theme as theme_mod

        return theme_mod.get()
    except Exception:  # noqa: BLE001 — a render without a theme is still a render
        return None


def _styled(theme: Any, text: str, token: str) -> str:
    if theme is None or not text:
        return text
    try:
        return theme.style(text, token)
    except Exception:  # noqa: BLE001
        return text


def _glyph(theme: Any, name: str, fallback: str) -> str:
    if theme is None:
        return fallback
    try:
        return theme.glyph(name)
    except Exception:  # noqa: BLE001
        return fallback


def terminal_columns(columns: int | None = None, theme: Any = None) -> int:
    """The width to render to: the caller's, else the theme's, else the real terminal."""
    if columns:
        return max(MIN_CODE_COLUMNS, int(columns))
    width = int(getattr(theme, "width", 0) or 0)
    if width:
        return max(MIN_CODE_COLUMNS, width)
    try:
        return max(MIN_CODE_COLUMNS, render_mod.terminal_width())
    except Exception:  # noqa: BLE001 — no terminal to ask is not a failure
        return render_mod.DEFAULT_WIDTH


def safe_prefix(text: str) -> int:
    """The longest leading prefix of ``text`` whose inline markup is already balanced.

    Markdown's inline tokens are *pairs*. While a model is still emitting one the text can
    end inside it — and half a pair is worse than nothing, because a terminal cannot take
    those characters back. This returns the index up to which everything is decided; a
    caller holds the rest until the partner arrives.

    A ``**`` that never closes is not an error here, it is simply *undecided*, which is why
    this **holds** rather than reformats: the same text may well turn out to be a complete
    bold span one delta later, and the answer must not have to be rewritten when it does.
    """
    index = 0
    size = len(text)
    while index < size:
        char = text[index]
        if char == _CODE:
            close = text.find(_CODE, index + 1)
            if close == -1:
                return index
            index = close + 1
            continue
        if text.startswith(_BOLD, index):
            close = text.find(_BOLD, index + len(_BOLD))
            if close == -1:
                return index
            index = close + len(_BOLD)
            continue
        index += 1
    return size


def render_inline(text: str, *, theme: Any = None, strong: str = "strong",
                  code: str = "key") -> str:
    """``**bold**`` and ``\u0060code\u0060`` drawn with the theme; everything else literal.

    A marker with no partner is printed as the character it is — the same rule
    :func:`safe_prefix` uses to decide what may be printed at all, so the two can never
    disagree about whether a chunk is finished. A lone ``*`` is always literal: it is far
    more often arithmetic (``a * b``) or a glob than a request for emphasis, and a renderer
    that guesses wrong there corrupts text the user is about to copy.
    """
    current = _resolve_theme(theme)
    body = str(text or "")
    out: list[str] = []
    index = 0
    size = len(body)
    while index < size:
        char = body[index]
        if char == _CODE:
            close = body.find(_CODE, index + 1)
            if close == -1:
                out.append(body[index:])
                break
            out.append(_styled(current, body[index + 1:close], code))
            index = close + 1
            continue
        if char == "*" and body.startswith(_BOLD, index):
            close = body.find(_BOLD, index + len(_BOLD))
            if close == -1:
                out.append(body[index:])
                break
            out.append(_styled(current, body[index + len(_BOLD):close], strong))
            index = close + len(_BOLD)
            continue
        nxt = size
        for marker in _MARKERS:
            found = body.find(marker, index + 1)
            if found != -1:
                nxt = min(nxt, found)
        out.append(body[index:nxt])
        index = nxt
    return "".join(out)


def _wrap_code(text: str, width: int) -> list[str]:
    """Split a code row into visual rows of at most ``width`` cells, losing nothing.

    Joining the pieces gives the line back exactly, so a wrapped code block is still
    copy-pasteable — the only property a code block really has to keep. Within that rule the
    break prefers a space: a split identifier is lossless but unreadable, and a code block is
    read far more often than it is copied.
    """
    if width <= 0 or len(text) <= width:
        return [text]
    rows: list[str] = []
    rest = text
    while len(rest) > width:
        cut = rest.rfind(" ", 0, width)
        if cut < max(1, width // 2):
            cut = width          # no usable break: cut at the edge rather than leave a stub
        else:
            cut += 1             # keep the space at the end of the row it belongs to
        rows.append(rest[:cut])
        rest = rest[cut:]
    rows.append(rest)
    return rows


def code_line(body: str, *, index: int = 0, line_numbers: bool = False, theme: Any = None,
              columns: int | None = None, tty: bool = True, rule: bool = True,
              indent: int = 0) -> str:
    """One line of a fenced code block, as it appears on the screen.

    On a **terminal**: the subtle left rule (``\u2502`` / ``|``), the number column when it was
    asked for, and a hard wrap that repeats the rule on every visual row so the block's left
    edge is never broken.

    On a **pipe**: the body comes back byte-identical — no rule, no number, no wrap, no
    escape, because a code block that is bound for a paste must not carry a character the
    model did not write.
    """
    current = _resolve_theme(theme)
    pad = " " * max(0, int(indent))
    raw = str(body)
    if not tty or not rule:
        return pad + raw
    bar = _glyph(current, "v", "|")
    number = f"{max(1, int(index)):>3} " if line_numbers else ""
    head = f"{bar} {number}"
    rows = _wrap_code(raw, terminal_columns(columns, current) - len(pad) - len(head))
    blanks = " " * len(number)
    out: list[str] = []
    for position, row in enumerate(rows):
        lead = f"{bar} {number if position == 0 else blanks}"
        out.append(f"{pad}{_styled(current, lead, 'rule')}{row}")
    return "\n".join(out)


def render_code_block(text: str, *, theme: Any = None, columns: int | None = None,
                      line_numbers: bool = False, tty: bool = True, rule: bool = True,
                      indent: int = 0) -> str:
    """A whole fenced block: the code and nothing else (the fence lines are consumed)."""
    lines = str(text or "").split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # a trailing newline terminates the block; it is not an empty row
    out = [
        code_line(line, index=number, line_numbers=line_numbers, theme=theme,
                  columns=columns, tty=tty, rule=rule, indent=indent)
        for number, line in enumerate(lines, start=1)
    ]
    return "\n".join(out) + "\n" if out else ""


def bounded_output(text: str, *, max_lines: int = DEFAULT_HEAD_LINES,
                   hint: str = LONG_OUTPUT_HINT_TAIL, theme: Any = None,
                   indent: int = 2) -> str:
    """The head of a huge tool return, then one row saying exactly how much was left out.

    ``max_lines`` counts the body rows that are shown. Nothing is dropped silently: the last
    row carries the measured count of hidden rows and the command that shows them
    (``\u2026 +812 lines (skaldir /diff or /export to see it)``), because a bounded print that
    does not say it is bounded reads as the whole answer.
    """
    current = _resolve_theme(theme)
    lines = str(text or "").split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    pad = " " * max(0, int(indent))
    limit = max(0, int(max_lines))
    if limit <= 0 or len(lines) <= limit:
        return "\n".join(f"{pad}{line}" for line in lines) + ("\n" if lines else "")
    marker = _glyph(current, "ellipsis", "...")
    note = f"{marker} +{len(lines) - limit} lines"
    if hint:
        note += f" ({hint} to see it)"
    body = [f"{pad}{line}" for line in lines[:limit]]
    body.append(_styled(current, f"{pad}{note}", "muted"))
    return "\n".join(body) + "\n"


class _Screen:
    """A model of what the terminal *shows*, so flicker is measured instead of claimed.

    It understands exactly the four things this module writes — text, ``\\n``, ``\\r`` and the
    erase-line sequence — and nothing else, and that is the point: a renderer that grew a
    cursor move would make these numbers wrong *loudly* instead of quietly.

    Two kinds of change are counted, in one place: a row **grown** after it was already
    visible (two writes landed on it), and a row **taken back** and written again. Both are
    a row the eye read twice. Rows carrying the renderer's own furniture (``chrome``) are
    counted separately, so "the answer never flickers" can never be confused with "nothing
    flickered".
    """

    _TOKENS = re.compile(r"\x1b\[K|\r|\n")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.rows: list[str] = []
        self.current = ""
        self.rows_written = 0
        self.rows_changed = 0
        self.rows_redrawn = 0
        self.chrome_rewrites = 0
        self.chrome_rows = 0
        self._current_chrome = False
        self._appends = 0  # writes that landed on the open row beyond the one that opened it

    def _open(self, piece: str, chrome: bool) -> None:
        if not self.current:
            self._current_chrome = chrome
        else:
            self._appends += 1  # the row was already on screen; this write changes it
        self.current += piece

    def _commit(self) -> None:
        changed = self._appends > 0
        if self._current_chrome:
            self.chrome_rows += 1
            if changed:
                self.chrome_rewrites += 1
        else:
            self.rows_written += 1
            if changed:
                self.rows_changed += 1
        self.rows.append(self.current)
        self.current = ""
        self._appends = 0

    def _take_back(self, chrome: bool) -> None:
        # ``\r`` and ``\x1b[K`` only ever appear inside a CLEAR_LINE here: the row is being
        # taken back before it is written again, which is the harsh kind of change
        if self.current:
            if self._current_chrome:
                self.chrome_rewrites += 1
            else:
                self.rows_changed += 1
                self.rows_redrawn += 1
        self.current = ""
        self._appends = 0
        # what follows the erase belongs to *this* write, and carries its flag
        self._current_chrome = chrome

    def feed(self, text: str, *, chrome: bool = False) -> None:
        pos = 0
        for match in self._TOKENS.finditer(text):
            piece = text[pos:match.start()]
            if piece:
                self._open(piece, chrome)
            if match.group(0) == "\n":
                self._commit()
            else:
                self._take_back(chrome)
            pos = match.end()
        piece = text[pos:]
        if piece:
            self._open(piece, chrome)


#: A line that has been open this long without a newline may be shown anyway, so that a model
#: stuck inside one enormous line cannot look like a hang. **0 — the default — means never**:
#: the incomplete line is buffered whole, which is the only setting that makes
#: ``flicker_ratio`` exactly 0 *by construction*, because a row cannot change after it was
#: written if it is only ever written once. A caller who would rather trade a rewritten row
#: for sub-line liveness sets it; 250 is the value the pty harness measures the cost of
#: (``tools/gv_stream_look.py`` §3–4, ``MVP/69-streaming-look.md``).
DEFAULT_TAIL_FLUSH_MS = 0

#: ...and only when this much of it is already provably safe to print.
DEFAULT_TAIL_FLUSH_CHARS = 40


class MarkdownStream(StreamRenderer):
    """The streaming sink with the answer rendered as light markdown, one line at a time.

    Three guarantees, each paid for by a measurement rather than an opinion:

    * **a line is written once.** Text is rendered when its line *completes* — the unfinished
      tail is buffered, not printed — and rendering only ever **appends**: no byte that
      reached the screen is ever revisited. Each visible answer row therefore changes exactly
      once, which is what ``flicker_ratio`` reports and what the pty capture in
      ``tools/gv_stream_look.py`` measures from the bytes.
    * **half a marker is never printed.** The unfinished tail is held at the last point where
      the inline markup is balanced (:func:`safe_prefix`), so a chunk that ends inside
      ``**bold**`` or a code span prints the safe part and waits for the partner;
    * **a terminal and a pipe see different things on purpose.** A terminal gets the rendered
      look — bold, inline code, lists, headings, a ruled code block. A pipe gets the model's
      own bytes, once, with no rule, no number and no escape, because a code block bound for
      a paste has to survive the trip.
    """

    def __init__(self, stream: Any = None, *, interval_ms: int = DEFAULT_INTERVAL_MS,
                 tty: bool | None = None, theme: Any = None,
                 clock: Callable[[], float] = time.monotonic,
                 cpu_clock: Callable[[], float] = time.process_time, indent: int = 2,
                 footer: bool = True, columns: int | None = None, label: str = "streaming",
                 line_numbers: bool = False, rule: bool = True,
                 tail_flush_ms: float = DEFAULT_TAIL_FLUSH_MS,
                 tail_flush_chars: int = DEFAULT_TAIL_FLUSH_CHARS,
                 incremental: bool = False) -> None:
        super().__init__(stream, interval_ms=interval_ms, tty=tty, theme=theme, clock=clock,
                         cpu_clock=cpu_clock, indent=indent, footer=footer, columns=columns,
                         label=label)
        self.line_numbers = bool(line_numbers)
        self.rule = bool(rule)
        self.tail_flush_ms = float(tail_flush_ms)
        self.tail_flush_chars = int(tail_flush_chars)
        self.incremental = bool(incremental)
        self._screen = _Screen()
        self._reset_markdown()

    # -- the markdown state -------------------------------------------------- #

    def _reset_markdown(self) -> None:
        self._src_done = 0           # source characters already rendered or consumed
        self._line_src_emitted = 0   # of the open line, how much has reached the screen
        self._line_started_at = 0.0  # when the open line first waited (for the tail flush)
        self._final = False          # a turn boundary: the open line is a finished line
        self._in_fence = False
        self._fence_marker = ""
        self._fence_lang = ""
        self._code_index = 0

    def arm(self, *, label: str = "") -> None:
        super().arm(label=label)
        self._reset_markdown()

    def _theme(self) -> Any:
        return _resolve_theme(self.theme)

    def _columns(self) -> int:
        return terminal_columns(self.columns, self.theme)

    # -- rendering ----------------------------------------------------------- #

    def _take_pending(self) -> str:
        """Only what is provably renderable: complete lines, plus a line in progress.

        With ``incremental=False`` (the default) this holds to the historical contract: a
        line reaches the screen only when it *ends*, so each answer row is written once and
        ``flicker_ratio`` is 0 by construction. With ``incremental=True`` the unfinished line
        is also released — up to :func:`safe_prefix`, never past a half-written marker and
        never inside a fence, where the rule has to start the row.
        """
        if self.plain:
            text = "".join(self._pending)
            self._pending.clear()
            return text
        source = self.text
        out: list[str] = []
        while True:
            newline = source.find("\n", self._src_done)
            if newline == -1:
                out.append(self._line_in_progress(source[self._src_done:]))
                break
            rendered = self._render_complete_line(source[self._src_done:newline])
            if rendered is not None:
                out.append(rendered)
                out.append("\n")
            self._src_done = newline + 1
            self._line_src_emitted = 0
            self._line_started_at = 0.0
        tail = source[self._src_done:]
        if tail:
            if self._final:
                self._src_done = len(source)
                rendered = self._render_complete_line(tail)
                if rendered is not None:
                    out.append(rendered)
                self._line_src_emitted = 0
            elif not self.incremental:
                out.append(self._tail_ready(tail))
        self._pending.clear()
        return "".join(out)

    def _line_in_progress(self, tail: str) -> str:
        """The safe part of a line that has not ended yet (``incremental`` mode only).

        Three things are held, and each one is a rule rather than a judgement call:

        * everything after the last balanced marker (:func:`safe_prefix`), so half of a
          ``**bold**`` or a code span never reaches the screen;
        * a line that could still turn out to be a fence, because two backticks are either
          the start of a fence or the start of a code span and the two render differently;
        * any line while inside a fence — a code row's rule has to start it and its wrap is
          computed against the whole row, so a code line keeps its line buffering.

        Only the *continuation* is rendered inline: the first segment of the line is what
        decides the block form and carries the indent, so a later chunk cannot repeat either.
        """
        if not self.incremental or not tail or self._in_fence or _could_be_fence(tail):
            return ""
        safe = safe_prefix(tail)
        start = self._line_src_emitted
        if safe <= start:
            return ""
        segment = tail[start:safe]
        self._line_src_emitted = safe
        if not segment:
            return ""
        if start == 0:
            # the first thing rendered on this line: it chooses the block form (indent,
            # bullet, heading) and only it may draw those characters
            return self._render_block_line(segment)
        return render_inline(segment, theme=self.theme)

    def _tail_ready(self, tail: str) -> str:
        """The safe prefix of a line that has not ended yet — usually nothing at all.

        Holding is the default, not an accident: a row written now and extended a moment
        later is a row the eye saw twice. It is released early in exactly one case — a line
        that has been open longer than :attr:`tail_flush_ms` with at least
        :attr:`tail_flush_chars` of it provably renderable, which is a model stuck inside one
        huge line rather than a model writing an answer.
        """
        safe = safe_prefix(tail)
        self._note_open_line()
        if safe <= self._line_src_emitted:
            return ""
        if self.tail_flush_ms <= 0 or self.tail_flush_chars <= 0:
            return ""
        if (safe - self._line_src_emitted) < self.tail_flush_chars:
            return ""
        if not self._line_waited_long_enough() or _could_be_fence(tail):
            return ""
        if self._in_fence:
            # A code line is wrapped against its own length, and the rule has to start the
            # row: half a code line would put the rule in the middle of the text and split
            # the wrap in two. A fence is held whole, however long it takes.
            return ""
        segment = tail[self._line_src_emitted:safe]
        self._line_src_emitted = safe
        self._line_started_at = 0.0
        return self._render_block_line(segment)

    def _note_open_line(self) -> None:
        if not self._line_started_at:
            self._line_started_at = self._clock()

    def _line_waited_long_enough(self) -> bool:
        if not self._line_started_at:
            return False
        return (self._clock() - self._line_started_at) * 1000.0 >= self.tail_flush_ms

    def _render_complete_line(self, line: str) -> str | None:
        """One finished line, in the block form its first characters chose.

        ``None`` means the line produced no row at all — a fence is scaffolding, and the
        block's shape is carried by the rule on its content rows. Returning ``None`` rather
        than "" is what keeps a fence from costing two blank rows.
        """
        if self._in_fence:
            if self._is_fence_close(line):
                self._in_fence = False
                self._fence_marker = ""
                self._fence_lang = ""
                return None
            self._code_index += 1
            return self._code_line(line[self._line_src_emitted:])
        if self._line_src_emitted:
            # part of the line is already on screen (a stall was flushed): only the rest is
            # left to render, and it is inline by definition — the block form was decided
            return render_inline(line[self._line_src_emitted:], theme=self.theme)
        match = _FENCE_RE.match(line)
        if match:
            self._in_fence = True
            self._fence_marker = match.group("marker")
            self._fence_lang = match.group("lang")
            self._code_index = 0
            return None  # the fence line itself is scaffolding: only the rule is drawn
        return self._render_block_line(line)

    def _render_block_line(self, line: str) -> str:
        theme = self._theme()
        pad = " " * self.indent
        if not line.strip():
            return ""
        match = _HEADING_RE.match(line)
        if match:
            return f"{pad}{match.group('indent')}{_styled(theme, match.group('body'), 'heading')}"
        match = _BULLET_RE.match(line)
        if match:
            mark = _glyph(theme, "bullet", "-")
            body = render_inline(match.group("body"), theme=theme)
            return f"{pad}{match.group('indent')}{_styled(theme, mark, 'muted')} {body}"
        match = _ORDERED_RE.match(line)
        if match:
            number = f"{match.group('number')}."
            body = render_inline(match.group("body"), theme=theme)
            return f"{pad}{match.group('indent')}{_styled(theme, number, 'muted')} {body}"
        match = _QUOTE_RE.match(line)
        if match:
            bar = _glyph(theme, "v", "|")
            body = render_inline(match.group("body"), theme=theme)
            return f"{pad}{match.group('indent')}{_styled(theme, bar, 'rule')} {body}"
        return pad + render_inline(line, theme=theme)

    def _code_line(self, body: str) -> str:
        pad = " " * self.indent
        theme = self._theme()
        if not self.rule:
            return pad + body
        bar = _glyph(theme, "v", "|")
        number = f"{max(1, self._code_index):>3} " if self.line_numbers else ""
        head = f"{bar} {number}"
        rows = _wrap_code(body, self._columns() - len(pad) - len(head))
        blanks = " " * len(number)
        out: list[str] = []
        for position, row in enumerate(rows):
            # the rule is repeated on every visual row so the block's left edge is never
            # broken; the *number* is not, because a wrapped row is still one line and
            # printing its number again would claim a line that does not exist
            lead = f"{bar} {number if position == 0 else blanks}"
            out.append(f"{pad}{_styled(theme, lead, 'rule')}{row}")
        return "\n".join(out)

    def _is_fence_close(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        char = self._fence_marker[:1] or "`"
        return set(stripped) == {char} and len(stripped) >= len(self._fence_marker)

    # -- the turn boundary --------------------------------------------------- #

    def _release_tail(self) -> None:
        """Make the open line finishable *now*, so a turn boundary never leaves it held."""
        if self.text[self._src_done:]:
            self._pending.append("")

    def close_call(self) -> None:
        """A model call ended: write the held tail, then leave the row clean."""
        with self._lock:
            if self._closed:
                return
            self._final = True
            try:
                self._release_tail()
                self.update()
            finally:
                self._final = False
        super().close_call()

    def finish(self) -> dict[str, Any]:
        """Flush the tail, drop the footer, return the measured facts — tail first."""
        with self._lock:
            if self._closed:
                return self.metrics.as_dict()
            self._final = True
            try:
                self._release_tail()
                self.update()
            finally:
                self._final = False
        return super().finish()


def _could_be_fence(text: str) -> bool:
    """Could this unfinished line still turn into a fence? Then it must not be printed yet.

    ``\u0060\u0060`` is the whole reason: it is either the start of a fence or the start of a code
    span, and the two render differently. Three characters decide it, so three characters is
    the whole cost of never guessing.
    """
    stripped = text.lstrip()
    return bool(stripped) and stripped[0] in "`~"
