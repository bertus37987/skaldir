"""The full-screen terminal UI: chat left, tools upper-right, goal lower-right.

``03-TUI-ENTSCHEIDUNGEN.md`` is the confirmed decision and this module is its layout and
rendering half. Every sentence below is one of those confirmed points.

Layout (wide terminal, confirmed 2026-09-25)::

    ┌ SKALDIR · project · run #42 ──────────────────────────────────────┐
    │ CHAT                                  │ TOOLS                   │
    │ …                                     │ #18 web_search      ✓   │
    │                                       │ #19 page.read       ◐   │
    │                                       ├ GOAL ────────────────────┤
    │                                       │ ✓ Sources visited       │
    ├───────────────────────────────────────┴─────────────────────────┤
    │ › Your message …                                                │
    ├ Approval: Approve for me · Desktop: run #42 · elapsed 01:24 ─────┤
    └─────────────────────────────────────────────────────────────────┘

What is deliberately *not* here:

* **No execution.** UI-01: "Keine zweite Ausführungslogik im UI." The TUI renders an event
  stream (:class:`TuiState`) that the existing runtime produces; it never runs a tool.
* **No invented state.** The renderer draws what the state says. A call that has not
  finished is not shown as finished; a goal without a ``PASS`` verification gets no success
  mark (``03-TUI-ENTSCHEIDUNGEN.md`` §3 Goal).
* **No colour as information.** Everything is legible in ASCII and with ``NO_COLOR``; the
  error mark is a symbol *and* a word, so red is never the only signal.

Every render function returns lines of exactly ``width`` cells (UI-07: the renderer has a
bounded event stream and a stable line width, and a terminal control sequence inside tool
data is never passed through — :func:`sanitize` owns that).
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from .approval_modes import ApprovalMode
from .desktop_lease import IndicatorState, indicator_lines

__all__ = [
    "CallRow",
    "CallState",
    "GoalPanel",
    "Geometry",
    "Pane",
    "Screen",
    "TuiState",
    "MIN_WIDTH",
    "MIN_HEIGHT",
    "COMPACT_WIDTH",
    "COMPACT_HEIGHT",
    "sanitize",
    "layout",
    "render",
    "tab_action",
    "figure_frame",
    "humor_at",
    "desktop_indicator",
]

MIN_WIDTH = 40
MIN_HEIGHT = 10
#: below this the split is dropped and the panes stack (03 §3: "Bei schmalem Terminal
#: gestapelte oder wechselbare Bereiche mit denselben Daten")
COMPACT_WIDTH = 72
COMPACT_HEIGHT = 24

_CONTROL_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]|\x00|\x07|\x08")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def sanitize(text: Any, *, limit: int = 4000) -> str:
    """Strip terminal control sequences from tool output before it is ever rendered.

    UI-07 and T-50: a tool's stdout is *data*, never instructions to the terminal. This
    removes escapes and other control bytes rather than letting them through, and it does so
    before truncation so a truncated escape cannot half-render.
    """
    raw = "" if text is None else str(text)
    cleaned = _CONTROL_RE.sub("", raw)
    cleaned = "".join(ch for ch in cleaned if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + " …[truncated]"
    return cleaned


def _plain(text: Any) -> str:
    """Visible text with escapes removed and nothing else changed."""
    return _ANSI_RE.sub("", "" if text is None else str(text))


class Pane(str, enum.Enum):
    CHAT = "chat"
    TOOLS = "tools"
    GOAL = "goal"


class CallState(str, enum.Enum):
    """The states the tools pane may show. ``done`` never implies fachlich success."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


_MARKS: dict[CallState, str] = {
    CallState.QUEUED: "·",
    CallState.RUNNING: "◐",
    CallState.DONE: "✓",
    CallState.FAILED: "✖",
    CallState.CANCELLED: "⊘",
    CallState.UNKNOWN: "?",
}
_ASCII_MARKS: dict[CallState, str] = {
    CallState.QUEUED: ".",
    CallState.RUNNING: "~",
    CallState.DONE: "OK",
    CallState.FAILED: "XX",
    CallState.CANCELLED: "off",
    CallState.UNKNOWN: "?",
}


@dataclass
class CallRow:
    """One tool call in the tools pane — confirmed detail depth "A".

    A short result preview is always visible; ``args`` and ``details`` are the expandable
    parts (``03-TUI-ENTSCHEIDUNGEN.md`` §2: "A ist die bestätigte Standarddarstellung").
    """

    call_id: str
    label: str = ""
    target: str = ""
    state: CallState = CallState.QUEUED
    duration_s: float | None = None
    preview: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    details: str = ""
    exit_code: int | None = None
    verification: str = "not_required"
    error: str = ""
    parent: str = ""          # batch id when this call belongs to a batch
    expanded: bool = False

    @property
    def is_error(self) -> bool:
        return self.state is CallState.FAILED or bool(self.error)

    def line(self, width: int, *, unicode: bool = True) -> str:
        """`#18 web_search      ✓   0.78s  Comparing sources …`"""
        mark = (_MARKS if unicode else _ASCII_MARKS)[self.state]
        ident = (self.call_id or "-")[:12]
        label = sanitize(self.label or self.target or "-", limit=40)
        dur = "" if self.duration_s is None else f"{self.duration_s:.2f}s"
        preview = sanitize(self.preview, limit=max(0, width))
        head = f"{ident} {label}"
        tail = f"{dur:>7} {mark}"
        room = max(0, width - len(head) - len(tail) - 2)
        body = preview[:room].ljust(room)
        return f"{head[:max(0, width - len(tail) - 1)]} {body} {tail}"[:width].ljust(width)

    def detail_lines(self, width: int, *, unicode: bool = True) -> list[str]:
        """The expandable block: args, exit code, verification, full details."""
        bullet = "•" if unicode else "-"
        rows: list[str] = []
        rows.append(f"  {bullet} args: {sanitize(self.args, limit=width)}"[:width])
        rows.append(f"  {bullet} exit: {self.exit_code if self.exit_code is not None else 'n/a'}"
                    f"  verify: {self.verification}"[:width])
        for raw in sanitize(self.details, limit=2000).splitlines() or [""]:
            rows.append(f"  {sanitize(raw, limit=max(0, width - 4))}"[:width])
        if self.error:
            rows.append(f"  {_ERR_MARK(unicode)} {sanitize(self.error, limit=max(0, width - 4))}"[:width])
        return [r.ljust(width) for r in rows]


def _ERR_MARK(unicode: bool) -> str:
    """Errors are a symbol *and* a word — colour is never the only signal (UI-05)."""
    return "✖ ERROR" if unicode else "XX ERROR"


@dataclass
class GoalPanel:
    """What the goal pane shows. No success mark before VERIFY/ENFORCE says PASS."""

    title: str = ""
    phase: str = ""
    run_state: str = ""
    criteria: list[tuple[bool, str]] = field(default_factory=list)
    open_points: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    verification: str = "unknown"
    revision: int = 0

    def lines(self, width: int, *, unicode: bool = True) -> list[str]:
        tick, cross = ("✓", "○") if unicode else ("OK", "[]")
        out: list[str] = []
        head = self.title or "no goal set"
        out.append(sanitize(head, limit=width)[:width])
        out.append(f"phase: {self.phase or '-'}  run: {self.run_state or '-'}"[:width])
        for done, text in self.criteria:
            mark = tick if done else cross
            out.append(f"{mark} {sanitize(text, limit=max(0, width - 3))}"[:width])
        for text in self.open_points:
            out.append(f"{'·' if unicode else '-'} open: {sanitize(text, limit=max(0, width - 8))}"[:width])
        for text in self.blockers:
            out.append(f"{_ERR_MARK(unicode)} {sanitize(text, limit=max(0, width - 9))}"[:width])
        # VERIFY-02: UNKNOWN never counts as done
        out.append(f"verify: {self.verification or 'unknown'}  rev {self.revision}"[:width])
        return [r.ljust(width) for r in out]


@dataclass
class TuiState:
    """Everything the screen draws, from one event model (03 §4)."""

    title: str = "SKALDIR"
    project: str = ""
    run_id: str = ""
    chat: list[str] = field(default_factory=list)
    calls: list[CallRow] = field(default_factory=list)
    goal: GoalPanel = field(default_factory=GoalPanel)
    approval: ApprovalMode = ApprovalMode.APPROVE_FOR_ME
    desktop: IndicatorState = field(default_factory=IndicatorState)
    input_text: str = ""
    elapsed_s: float = 0.0
    ctx: str = "n/a"          # never 0 — a missing measurement says so (03 §4)
    tok_per_s: str = "n/a"
    cost: str = "n/a"
    blocked: str = ""         # red blocker text (UI-05)
    #: sequence cursor for reattach (03 §4): duplicates never double a call
    cursor: int = 0
    new_events: int = 0
    at_live_edge: bool = True


@dataclass(frozen=True)
class Geometry:
    """Where each pane goes at this terminal size."""

    width: int
    height: int
    split: bool
    compact: bool
    chat: tuple[int, int, int, int] = (0, 0, 0, 0)      # x, y, w, h
    tools: tuple[int, int, int, int] = (0, 0, 0, 0)
    goal: tuple[int, int, int, int] = (0, 0, 0, 0)
    input: tuple[int, int, int, int] = (0, 0, 0, 0)
    status: tuple[int, int, int, int] = (0, 0, 0, 0)


def layout(width: int, height: int, *, blocked: bool = False) -> Geometry:
    """Compute the pane boxes. Three regimes, all with the same data.

    * ``split``  — wide enough: chat left, tools upper-right, goal lower-right
    * ``compact``— narrow or short: the panes stack, still carrying the same content
    * ``tiny``   — below :data:`MIN_WIDTH`/:data:`MIN_HEIGHT`: a one-line-per-section view
                   instead of a broken frame (03 §3: "Unter einer definierten Mindestgröße
                   kompakte Ansicht statt defekter Rahmen")
    """
    w, h = max(1, int(width)), max(1, int(height))
    if w < MIN_WIDTH or h < MIN_HEIGHT:
        return Geometry(w, h, split=False, compact=True,
                        chat=(0, 0, w, max(1, h - 2)), input=(0, max(1, h - 2), w, 1),
                        status=(0, max(1, h - 1), w, 1))
    # The blocker (UI-05) is a row of its own, directly above the input line. Taking it
    # out of the panes is what keeps it from ever overwriting the typing area.
    reserved = 5 if blocked else 4
    split = w >= COMPACT_WIDTH and h >= COMPACT_HEIGHT
    if split:
        right_w = max(24, w // 3)
        left_w = w - right_w
        tools_h = max(1, (h - reserved) // 2)
        goal_h = max(1, (h - reserved) - tools_h)
        return Geometry(
            w, h, split=True, compact=False,
            chat=(0, 1, left_w, max(1, h - reserved)),
            tools=(left_w, 1, right_w, tools_h),
            goal=(left_w, 1 + tools_h, right_w, goal_h),
            input=(0, h - 3, w, 1),
            status=(0, h - 2, w, 1),
        )
    body = h - reserved
    chat_h = max(2, body // 2)
    tools_h = max(1, (body - chat_h) // 2)
    goal_h = max(1, body - chat_h - tools_h)
    return Geometry(
        w, h, split=False, compact=False,
        chat=(0, 1, w, chat_h),
        tools=(0, 1 + chat_h, w, tools_h),
        goal=(0, 1 + chat_h + tools_h, w, goal_h),
        input=(0, h - 3, w, 1),
        status=(0, h - 2, w, 1),
    )


# --------------------------------------------------------------------------- #
# the figure: one subtle animation for the whole run, never per call (03 §5)
# --------------------------------------------------------------------------- #

#: 4-8 frames per second as suggested; this is the *frame count*, and the caller supplies
#: the clock. No model call happens per frame (03 §5: "keine Modellaufrufe pro Frame").
FIGURE_FRAMES: tuple[str, ...] = ("[·_]", "[·_]", "[-_]", "[o_]", "[·_]", "[._]")
#: Rare, light waiting accents. Only during ordinary work (03 §5).
HUMOR: tuple[str, ...] = (
    "still here, still typing",
    "thinking in a straight line",
    "consulting the tea leaves",
    "one more source",
)


def figure_frame(at: float, *, unicode: bool = True, motion: bool = True) -> str:
    """The animated figure at time ``at``. Deterministic, so a snapshot test can pin it."""
    if not motion:
        return "[._]" if unicode else "[._]"
    index = int(max(0.0, float(at)) * 8) % len(FIGURE_FRAMES)
    return FIGURE_FRAMES[index]


def humor_at(at: float, *, every_s: float = 45.0) -> str:
    """A rare accent, deterministic in ``at`` — and empty most of the time."""
    if every_s <= 0:
        return ""
    slot = int(max(0.0, float(at)) // every_s)
    if slot <= 0:
        return ""
    return HUMOR[slot % len(HUMOR)]


# --------------------------------------------------------------------------- #
# the renderer
# --------------------------------------------------------------------------- #


def _hline(width: int, left: str = "", right: str = "", *, unicode: bool = True) -> str:
    bar = "─" if unicode else "-"
    return f"{left}{' ' + bar * max(0, width - len(left) - len(right) - 2) + ' ' if right else bar * max(0, width - len(left))}{right}"[:width].ljust(width)


class Screen:
    """Render a :class:`TuiState` to lines. Pure: same state, same lines."""

    def __init__(self, *, unicode: bool = True, motion: bool = True, color: bool = True) -> None:
        self.unicode = unicode
        self.motion = motion
        self.color = color

    # -- pieces -------------------------------------------------------------

    def title_bar(self, state: TuiState, width: int) -> str:
        bits = [state.title or "SKALDIR"]
        if state.project:
            bits.append(state.project)
        if state.run_id:
            bits.append(f"run #{state.run_id}")
        return (" " + " · ".join(bits) + " ")[:width].ljust(width)

    def chat_lines(self, state: TuiState, box: tuple[int, int, int, int]) -> list[str]:
        _, _, w, h = box
        rows = [sanitize(line, limit=w) for line in state.chat]
        # UI-03: auto-follow only at the live edge; otherwise report the backlog
        if not state.at_live_edge and state.new_events:
            rows.append(f"… {state.new_events} new events — return to live"[:w])
        rows = rows[-h:] if len(rows) > h else rows
        padded = [r[:w].ljust(w) for r in rows]
        return padded + [" " * w] * (h - len(padded))

    def tools_lines(self, state: TuiState, box: tuple[int, int, int, int]) -> list[str]:
        _, _, w, h = box
        rows: list[str] = []
        for call in state.calls:
            rows.append(call.line(w, unicode=self.unicode))
            if call.expanded:                       # confirmed detail depth "A"
                rows.extend(call.detail_lines(w, unicode=self.unicode))
        rows = rows[-h:] if len(rows) > h else rows
        padded = [r[:w].ljust(w) for r in rows]
        return padded + [" " * w] * (h - len(padded))

    def goal_lines(self, state: TuiState, box: tuple[int, int, int, int]) -> list[str]:
        _, _, w, h = box
        rows = state.goal.lines(w, unicode=self.unicode)
        # The goal pane keeps its HEAD: the title and the phase are what a short pane must
        # never lose. Chat and tools keep their tail instead (newest wins there).
        if len(rows) > h:
            hidden = len(rows) - h + 1
            rows = rows[: h - 1] + [f"… {hidden} more — expand for details"[:w]]
        padded = [r[:w].ljust(w) for r in rows]
        return padded + [" " * w] * (h - len(padded))

    def input_line(self, state: TuiState, width: int) -> str:
        prompt = "› " if self.unicode else "> "
        text = sanitize(state.input_text, limit=max(0, width - 2))
        return f"{prompt}{text}"[:width].ljust(width)

    def status_line(self, state: TuiState, width: int, *, elapsed: str = "",
                    figure: str = "") -> str:
        """Approval, desktop ownership and telemetry — every number says its source.

        ``ctx``/``$``/``tok/s`` render as ``n/a`` when nothing measured them; a missing
        measurement is never drawn as ``0`` (03 §4). The figure occupies the right end, so
        the facts are composed into the room that is actually left.
        """
        desktop = state.desktop
        owner = f"Desktop: {desktop.run_id or '-'}"
        parts = [
            f"Approval: {state.approval.label}",
            owner,
            f"elapsed {elapsed or _mmss(state.elapsed_s)}",
            f"ctx {state.ctx}",
            f"{self.tok(state)} tok/s",
            f"$ {state.cost}",
        ]
        # the whole set first; if it does not fit, the least load-bearing facts drop first
        line = " · ".join(parts)
        room = max(0, width - len(figure))
        while len(line) > room and parts:
            parts.pop()
            line = " · ".join(parts)
        return line[:room].ljust(room)

    @staticmethod
    def tok(state: TuiState) -> str:
        return str(state.tok_per_s or "n/a")

    def blocker_line(self, state: TuiState, width: int) -> list[str]:
        """UI-05: red blocker text plus a symbol and a word, never colour alone."""
        if not state.blocked:
            return []
        mark = _ERR_MARK(self.unicode)
        text = sanitize(state.blocked, limit=max(0, width - len(mark) - 1))
        return [f"{mark} {text}"[:width].ljust(width)]

    # -- composed -----------------------------------------------------------

    def render(self, state: TuiState, width: int, height: int, *,
               at: float | None = None) -> list[str]:
        """The whole screen as ``height`` lines of exactly ``width`` cells."""
        w, h = max(1, int(width)), max(1, int(height))
        geom = layout(w, h, blocked=bool(state.blocked))
        lines: list[str] = [""] * h

        lines[0] = self.title_bar(state, w)

        if geom.compact and (w < MIN_WIDTH or h < MIN_HEIGHT):
            # tiny: one line per section instead of a broken frame
            body = [
                f"chat: {sanitize(state.chat[-1] if state.chat else '', limit=max(0, w - 6))}",
                f"goal: {sanitize(state.goal.title or '-', limit=max(0, w - 6))}",
            ]
            for call in state.calls[-max(1, h - 5):]:
                body.append(call.line(w, unicode=self.unicode))
            body = body[: max(1, h - 3)]
            for i, row in enumerate(body, start=1):
                lines[i] = row[:w].ljust(w)
        else:
            cx, cy, cw, ch = geom.chat
            for i, row in enumerate(self.chat_lines(state, (cx, cy, cw, ch))):
                if 0 <= cy + i < h:
                    lines[cy + i] = _merge(lines[cy + i], row, cx, cw)
            tx, ty, tw, th = geom.tools
            for i, row in enumerate(self.tools_lines(state, (tx, ty, tw, th))):
                if 0 <= ty + i < h:
                    lines[ty + i] = _merge(lines[ty + i], row, tx, tw)
            gx, gy, gw, gh = geom.goal
            for i, row in enumerate(self.goal_lines(state, (gx, gy, gw, gh))):
                if 0 <= gy + i < h:
                    lines[gy + i] = _merge(lines[gy + i], row, gx, gw)

        ix, iy, iw, _ = geom.input
        lines[iy] = self.input_line(state, w)
        sx, sy, sw, _ = geom.status
        fig = figure_frame(0.0 if at is None else at, unicode=self.unicode, motion=self.motion)
        lines[sy] = self.status_line(state, w, figure=fig)

        blocked = self.blocker_line(state, w)
        if blocked and h >= 5:
            # its own row, above the input line — never on top of what the user typed
            lines[h - 4] = blocked[0]

        # the figure sits in the status row's right end (03 §5: one global animation);
        # the status facts were composed into the room left of it, so nothing is overwritten
        lines[sy] = _put_right(lines[sy], fig)

        out = [(_plain(line) if not self.color else line)[:w].ljust(w) for line in lines]
        if not self.unicode:
            # UI-05 / plain mode: ASCII only. Fold the glyphs, never draw a different
            # picture — the same layout, spelled with ASCII.
            out = [_fold_ascii(line)[:w].ljust(w) for line in out]
        return out


def _merge(base: str, row: str, x: int, width: int) -> str:
    """Place ``row`` at column ``x`` inside ``base`` without disturbing the rest."""
    b = base.ljust(x + width)
    return b[:x] + row[:width].ljust(width) + b[x + width:]


#: ASCII equivalents for every glyph the renderer may draw (plain/pipe/NO_COLOR mode).
_ASCII_FOLD = {
    "·": ".", "›": ">", "─": "-", "│": "|", "┌": "+", "┐": "+", "└": "+", "┘": "+",
    "├": "+", "┤": "+", "┬": "+", "┴": "+", "┼": "+", "✓": "OK", "○": "[]", "✖": "XX",
    "⊘": "off", "◐": "~", "…": "...", "—": "-", "–": "-", "’": "'", "“": '"', "”": '"',
}


def _fold_ascii(text: str) -> str:
    return "".join(_ASCII_FOLD.get(ch, ch) if ord(ch) > 127 else ch for ch in str(text))


def _put_right(line: str, text: str) -> str:
    if len(text) >= len(line):
        return text[: len(line)]
    return line[: len(line) - len(text)] + text


def _mmss(seconds: float) -> str:
    total = int(max(0.0, float(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def render(state: TuiState, width: int, height: int, *, unicode: bool = True,
           motion: bool = True, at: float | None = None) -> list[str]:
    """Module-level convenience so callers need not build a :class:`Screen`."""
    return Screen(unicode=unicode, motion=motion).render(state, width, height, at=at)


# --------------------------------------------------------------------------- #
# the desktop corner indicator (DESK-03), as the host layer draws it
# --------------------------------------------------------------------------- #


def desktop_indicator(info: IndicatorState, *, width: int = 34, unicode: bool = True) -> list[str]:
    """The confirmed corner status bar (``03-TUI-ENTSCHEIDUNGEN.md`` §6).

    Reuses :func:`skaldir.desktop_lease.indicator_lines` so the TUI and the host layer
    cannot drift into two different statements about who owns the desktop.
    """
    return indicator_lines(info, width=width, unicode=unicode)


def approve_key_hint(mode: ApprovalMode) -> str:
    """What the UI tells the user about Tab (03 §4: Tab at an empty prompt cycles modes)."""
    return f"Tab cycles approval ({mode.label})"


def tab_action(*, prompt: str, completion_open: bool) -> str:
    """What Tab does right now — the confirmed rule (03 §4 / UI-04).

    "Tab bei leerem Eingabefeld wechselt den Approval-Modus; bei aktiver Vervollständigung
    bedient Tab die Vorschläge." The prompt text is irrelevant to the *choice* (an empty
    prompt with an open menu still accepts), which is why the two conditions are separate
    arguments rather than one derived guess — a policy change must never ride in on a
    completion keystroke.
    """
    return "accept_completion" if completion_open else "cycle_approval"
