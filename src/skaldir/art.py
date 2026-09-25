r"""The activity vocabulary — one monochrome glyph per thing the agent is doing.

The CLI already owns a *status* vocabulary (:data:`skaldir.theme.MARKS`: ``✓ ✗ ■ ·``) for a
finished thing. A turn also has *ongoing* things — the model is reasoning, a tool is running,
a child is working — and those need their own small, consistent set of pictures, or every
screen invents its own spinner. This module is that set and nothing else:

===========  ==========================  ==================================  ======
activity     mark (unicode / ascii)      shown when                          width
===========  ==========================  ==================================  ======
``thinking`` ``◇ ◈ ◆ ◈`` / ``. o O o``   the model is reasoning (4 frames)   1
``working``  ``▓░░░`` / ``=---``         work is in progress, extent unknown  4
``tool``     ``[▸_]`` / ``[>_]``         one named tool is running           4
``search``   ``⌕~`` / ``o~``             a web search runs                    2
``read``     ``▤`` / ``[=]``             a file is being read                 1 / 3
``write``    ``▥`` / ``[+]``             a file is being written              1 / 3
``edit``     ``±`` / ``+-``              a file is being changed              1 / 2
``subagents`` ``│││`` / ``\|\|\|`` *      children run in parallel             n
``verify``   ``✓`` / ``✗`` / ``?``        a check ran: pass, fail, UNKNOWN     1
``blocked``  ``■`` / ``#``               a gate refused (declined)            1
===========  ==========================  ==================================  ======

Six rules the vocabulary obeys, and the reason for each:

1. **Monochrome, never colour-as-information.** Marks come from the theme
   (:meth:`skaldir.theme.Theme.mark`, :meth:`skaldir.theme.Theme.glyph`) wherever the
   theme has one; the four pictograms it does not carry (``◇ ▤ ▥ ±`` and the two
   two-column marks) live in :data:`PICTOGRAMS` with an explicit ASCII twin, the same way
   :data:`skaldir.theme.GLYPHS` does. A pipe, a log file or ``--no-color`` therefore gets
   **pure ASCII with no escape sequence at all** — :func:`line` is asserted ``isascii()``
   on a plain theme for every activity.
2. **Never colour-as-information** also means: the *differences* are carried by shape
   (``▤`` vs ``▥`` vs ``±``) and by the mark staying in the same column, so a
   black-and-white reader, a colour-blind reader and a screen reader all get the same
   distinction.
3. **Calm, not a toy.** The only animated marks are ``thinking`` (a diamond breathing in
   and out over four 150 ms frames) and ``working`` (a head moving along a four-column
   track). Nothing redraws faster than :data:`FRAME_INTERVAL_MS` = 150 ms, no frame
   contains more than one character of change, and a *known* fraction is drawn as a real
   bar (``██░░ 62%``) instead of being guessed at. The reference feel is Codex's restrained
   activity line — see the credits at the bottom of this docstring.
4. **Measurements or nothing.** A duration is only printed when the caller measured one
   (:func:`ms_label`); a hit count only when the search reported one; a diff stat only when
   the edit record carries one. There is no placeholder for an unknown number — the fact is
   omitted, never invented. That is the same rule ``RunResult.footer`` follows.
5. **``UNKNOWN`` and ``BLOCKED`` are spelled out**, in capitals, because they are the two
   states a reader must never mistake for success — a mangled glyph or a font without ``■``
   still leaves the word. Every other label is lowercase (the CLI's bars stay the only
   ALL-CAPS *decoration*; these two keywords are safety tokens, not decoration).
6. **One line, 76 columns, no wrapping.** Every composed line fits
   :data:`LINE_BUDGET` = 76 columns, which leaves the chat's :data:`CHAT_INDENT` = 2 space
   indent inside an 80-column screen. A long path, query or reason is truncated with the
   theme's ellipsis (``…`` / ``...``); a bar is never allowed to wrap the line it belongs to.

The vocabulary is used through tiny functions — ``art.thinking()``, ``art.tool(name, ms)``,
``art.search(query, hits)``, … — each returning the drawn line. :func:`line` renders a
record (a dict from a run's own event stream) and :func:`from_tool_event` renders a real
``tool.call`` transcript event, which is the one call a chat row or a tool line needs::

    ▤ MVP/67-activity-art.md   ⏱ omitted — the read carried no duration
    [▸_] read_text  41 ms
    ? UNKNOWN  which provider answered

**Credits.** The *shape* of the vocabulary (mark + label + muted measurement on one line,
an animated lead glyph only while the model is thinking, a diff stat echoing ``+n −n``) is
adapted from two lines the owner pointed at as the reference feel: Codex CLI's activity
line ("the clock never disappears behind a long title") and Claude Code's tool rows. The
marks themselves are this project's: the status set (``✓ ✗ ■ ·``) is
:data:`skaldir.theme.MARKS` unchanged, the bars and the vertical are
:data:`skaldir.theme.GLYPHS`, and only the four pictograms above are new here. Nothing is
transcribed from another product's code.

**Where it is drawn.** ``skaldir art preview`` prints this module's whole surface;
``python -m skaldir.art`` is the same without the CLI group. The chat screen's thinking
line, its tool rows and its subagent rows are the consumers (``register_cli`` below is the
hand-over; :data:`WIRING_SNIPPET` is the line ``cli.py`` pastes).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any

from . import cli_shared, render
from . import theme as theme_mod
from .theme import DEPTH_NONE, GLYPHS_ASCII, GLYPHS_UNICODE, MODE_PLAIN, Theme

try:
    from . import toolcall as _toolcall_vocabulary
except Exception:  # pragma: no cover — a build without the policy module
    _toolcall_vocabulary = None  # type: ignore[assignment]

#: The module-level name the drift guard reads (audit 46 #2): this is *the* allow
#: set ``from_tool_event`` renders with. It is toolcall's exported vocabulary, or
#: the same five words inline when the policy module is unavailable.
_ALLOW_DECISION_TOKENS: frozenset[str] = (
    frozenset(getattr(_toolcall_vocabulary, "ALLOW_TOKENS_FOR_RENDER", ()))
    if _toolcall_vocabulary is not None
    else frozenset({"allow", "allowed", "executed", "ok", "run"})
)


GROUP = "art"

#: How often an animated mark advances. 150 ms is a breath, not a spin: the eye reads the
#: change without the line drawing attention to itself (the owner's word for the target is
#: "calm"). Nothing in this module ever ticks faster.
FRAME_INTERVAL_MS = 150

#: The indent the chat puts in front of every activity line (two spaces — the same rhythm
#: :meth:`skaldir.theme.Theme.mark_line` uses after a mark).
CHAT_INDENT = 2

#: The widest line this module will compose. 76 + :data:`CHAT_INDENT` = 78, so an activity
#: line still fits an 80-column terminal with the chat's own margin.
LINE_BUDGET = 76

#: The narrowest label that is still worth drawing (a label shorter than this is not a
#: label, and the marks' own budget must not be spent on one).
MIN_LABEL = 8

#: How many child bars a ``subagents`` mark draws before it stops drawing bars and lets the
#: number carry the count (the count is authoritative; the bars are the glance).
MAX_BARS = 8

#: How the safety keyword joins its label (one space — the mark keeps the two-space rhythm).
KEYWORD_GAP = " "

#: The two states a reader must never confuse with success: spelled out in capitals.
KEYWORD_BLOCKED = "BLOCKED"
KEYWORD_DECLINED = "DECLINED"
#: A call that **ran** and then failed (``ok=False`` with ``executed`` true) —
#: never the gate's word. DECLINED says the gate refused; that is a lie about an
#: executed call (audit 46 #1/#15).
KEYWORD_FAILED = "FAILED"
KEYWORD_UNKNOWN = "UNKNOWN"

#: ``+n −n`` uses the real minus sign in unicode mode (the diff gutter minus is a dash and
#: reads narrower next to a plus); the ASCII twin is ``-``. :data:`skaldir.render.ASCII_FOLD`
#: folds U+2212 the same way for text this module only borrowed.
MINUS_SIGN = "\u2212"

#: Pictograms the theme does not carry: ``name -> (unicode, ascii)``. Same convention as
#: :data:`skaldir.theme.GLYPHS` — the ASCII twin is a *different character*, not a mangled
#: one, so plain output is real ASCII rather than mojibake.
PICTOGRAMS: dict[str, tuple[str, str]] = {
    # a breathing dot/diamond: four frames, symmetric in and out
    "thinking": ("◇", "."),
    # a boxed terminal mark: the caret differs, the box is ASCII by construction so the
    # mark survives every font (and it is the only *bracketed* mark family — see below)
    "tool": ("[▸_]", "[>_]"),
    # magnifier + wave: the wave is a plain tilde in both modes, the lens differs
    "search": ("⌕~", "o~"),
    # one page (read), one page with ink (write), plus/minus (a change)
    "read": ("▤", "[=]"),
    "write": ("▥", "[+]"),
    "edit": ("±", "+-"),
}

#: The frame sets of the two animated marks. ``thinking`` breathes (the same three shapes
#: in and out); ``working`` is a head travelling along a four-column track — a motif that
#: says "yes, still going" without pretending to know how far along it is.
THINKING_FRAMES = ("◇", "◈", "◆", "◈")
THINKING_ASCII_FRAMES = (".", "o", "O", "o")
WORKING_FRAMES = ("▓░░░", "░▓░░", "░░▓░", "░░░▓")
WORKING_ASCII_FRAMES = ("=---", "-=--", "--=-", "---=")

#: The mark the ``subagents`` line repeats once per child (the theme's own vertical), and
#: the default label/state of that line.
SUBAGENT_BAR = ("│", "|")


@dataclass(frozen=True)
class Glyph:
    """One activity: what it means, when it is shown, and how it is drawn."""

    name: str
    purpose: str
    when: str
    unicode: str
    ascii: str
    token: str = "key"
    frames: tuple[str, ...] = ()
    ascii_frames: tuple[str, ...] = ()
    interval_ms: int = 0
    #: ``True`` for the boxed marks (``[▸_]`` ``[=]`` ``[+]``): the only bracketed family, so
    #: a reader can tell a *thing that was touched* from a state by the box alone.
    bracketed: bool = False

    def is_animated(self) -> bool:
        return bool(self.frames or self.ascii_frames)

    def frame_count(self) -> int:
        return len(self.frames or self.ascii_frames) or 1


def _indexed(*glyphs: Glyph) -> dict[str, Glyph]:
    """``name -> Glyph`` from an ordered construction (keeps the table readable)."""
    return {glyph.name: glyph for glyph in glyphs}


#: The vocabulary. Order is the order of a turn: reason → act → report.
VOCABULARY: dict[str, Glyph] = _indexed(
    Glyph(
        name="thinking",
        purpose="the model is reasoning (no output has arrived yet)",
        when="between the request and its first token; in the chat's status row",
        unicode=THINKING_FRAMES[0],
        ascii=THINKING_ASCII_FRAMES[0],
        token="muted",
        frames=THINKING_FRAMES,
        ascii_frames=THINKING_ASCII_FRAMES,
        interval_ms=FRAME_INTERVAL_MS,
    ),
    Glyph(
        name="working",
        purpose="work is in progress and the extent is not known",
        when="while a step runs without a measurable fraction",
        unicode=WORKING_FRAMES[0],
        ascii=WORKING_ASCII_FRAMES[0],
        token="muted",
        frames=WORKING_FRAMES,
        ascii_frames=WORKING_ASCII_FRAMES,
        interval_ms=FRAME_INTERVAL_MS,
    ),
    Glyph(
        name="tool",
        purpose="one named tool is running (or ran)",
        when="a tool row in the chat, a tool line in the shell",
        unicode=PICTOGRAMS["tool"][0],
        ascii=PICTOGRAMS["tool"][1],
        token="key",
        bracketed=True,
    ),
    Glyph(
        name="search",
        purpose="a web search runs",
        when="the web-search row; the query and — when reported — the hit count follow",
        unicode=PICTOGRAMS["search"][0],
        ascii=PICTOGRAMS["search"][1],
        token="key",
    ),
    Glyph(
        name="read",
        purpose="a file is being read",
        when="a read tool row (path, then its measured duration when there is one)",
        unicode=PICTOGRAMS["read"][0],
        ascii=PICTOGRAMS["read"][1],
        token="key",
        bracketed=True,
    ),
    Glyph(
        name="write",
        purpose="a file is being written or created",
        when="a write tool row (path, then its measured duration when there is one)",
        unicode=PICTOGRAMS["write"][0],
        ascii=PICTOGRAMS["write"][1],
        token="key",
        bracketed=True,
    ),
    Glyph(
        name="edit",
        purpose="a file is being changed; the line echoes the change as ``+n −n``",
        when="an edit/patch tool row",
        unicode=PICTOGRAMS["edit"][0],
        ascii=PICTOGRAMS["edit"][1],
        token="key",
    ),
    Glyph(
        name="subagents",
        purpose="children run in parallel — one bar per child",
        when="the chat's subagent row, while a fan-out is open",
        unicode=SUBAGENT_BAR[0] * 3,
        ascii=SUBAGENT_BAR[1] * 3,
        token="key",
    ),
    Glyph(
        name="verify",
        purpose="a check ran: passed (✓), failed (✗) or UNKNOWN (?)",
        when="after the check; the unknown form whenever the check could not decide",
        unicode="✓",
        ascii="+",
        token="ok",
    ),
    Glyph(
        name="blocked",
        purpose="a gate refused — blocked or declined, never a success",
        when="a refused action; the reason follows when the record carries one",
        unicode="■",
        ascii="#",
        token="blocked",
    ),
)

#: Mark names in the order the preview and the width table print them.
NAMES: tuple[str, ...] = tuple(VOCABULARY)

#: Statuses :func:`verify` accepts (``unknown`` is a first-class outcome, not an absence).
VERIFY_STATUSES: tuple[str, ...] = ("ok", "fail", "unknown")

#: ``VERIFY_STATUSES`` → the theme mark that draws them (``unknown`` has no theme mark: it
#: is the one state the status vocabulary could not express before this module).
VERIFY_MARKS: dict[str, str] = {"ok": "ok", "fail": "fail", "unknown": "?"}
VERIFY_KEYWORDS: dict[str, str] = {"ok": "verified", "fail": "failed", "unknown": KEYWORD_UNKNOWN}

#: Tool name → activity. The one place a name becomes a mark; a name that is not here gets
#: the generic :func:`tool` mark rather than a guess about what it touches. Names are
#: lowercased and matched exactly (``read_text``, not a substring of it), so a tool called
#: ``unread_text`` cannot inherit the read mark by accident.
ALIASES: dict[str, str] = {
    # -- reads ---------------------------------------------------------------- #
    "read": "read",
    "read_text": "read",
    "read_file": "read",
    "view_file": "read",
    "open_file": "read",
    "cat": "read",
    "head": "read",
    "tail": "read",
    "list_dir": "read",
    "ls": "read",
    "glob": "read",
    "grep": "read",
    "search_files": "read",
    # -- writes --------------------------------------------------------------- #
    "write": "write",
    "write_text": "write",
    "write_file": "write",
    "create_file": "write",
    "save_file": "write",
    # -- edits ---------------------------------------------------------------- #
    "edit": "edit",
    "edit_file": "edit",
    "apply_patch": "edit",
    "patch": "edit",
    "replace_in_file": "edit",
    "str_replace": "edit",
    # -- network -------------------------------------------------------------- #
    "web_search": "search",
    "search_web": "search",
    "http_search": "search",
    "browser_search": "search",
    # -- checks --------------------------------------------------------------- #
    "verify": "verify",
    "verify_run": "verify",
    "run_tests": "verify",
    "pytest": "verify",
    "lint": "verify",
    "check": "verify",
}

#: The record keys :func:`line` reads (any other key is ignored, never interpreted).
ACTIVITY_KEYS = ("activity", "kind", "glyph")
LABEL_KEYS = ("text", "label", "task")
PATH_KEYS = ("path", "file", "target")
MS_KEYS = ("ms", "duration_ms", "elapsed_ms")


def _theme(theme: Theme | None) -> Theme:
    return theme if theme is not None else theme_mod.get()


def _fold(text: Any, theme: Theme) -> str:
    """Borrowed text on a plain theme: fold the CLI's typography to ASCII (as ``render`` does)."""
    value = str(text if text is not None else "")
    return render.ascii_fold(value) if theme.plain else value


# --------------------------------------------------------------------------- #
# the marks                                                                    #
# --------------------------------------------------------------------------- #


def resolve(name: str) -> str:
    """The activity a name (or a tool name) belongs to; unknown names get ``tool``."""
    key = str(name or "").strip().lower()
    if key in VOCABULARY:
        return key
    return ALIASES.get(key, "tool")


def activity_for(tool_name: str) -> str:
    """Which activity a real tool name is drawn as (``read_text`` → ``read``)."""
    return resolve(tool_name)


def frames(name: str, *, theme: Theme | None = None) -> tuple[str, ...]:
    """Every frame of an activity's mark (a static mark has exactly one frame)."""
    glyph = VOCABULARY[resolve(name)]
    if _theme(theme).unicode:
        return glyph.frames or (glyph.unicode,)
    return glyph.ascii_frames or (glyph.ascii,)


def interval_ms(name: str) -> int:
    """How long one frame of this activity lasts (``0`` for a static mark)."""
    return int(VOCABULARY[resolve(name)].interval_ms)


def frame_count(name: str, *, theme: Theme | None = None) -> int:
    """How many frames this activity has (``1`` when it does not animate)."""
    return len(frames(name, theme=theme))


def frame_index(name: str, elapsed_ms: Any) -> int:
    """The frame that belongs to ``elapsed_ms`` — pure, so the timing is testable.

    ``elapsed_ms`` is measured from the moment the activity started; a static mark always
    answers ``0``. The arithmetic is integer on purpose: it is the timing, not a clock. A
    value that is not a number (``None``, a string) is treated as "no time has passed"
    rather than raising — a chat row may not have a clock yet.
    """
    interval = interval_ms(name) or FRAME_INTERVAL_MS
    if frame_count(name) <= 1:
        return 0
    try:
        value = int(elapsed_ms)
    except (TypeError, ValueError):
        value = 0
    return (max(0, value) // interval) % frame_count(name)


def frame(name: str, index: int = 0, *, theme: Theme | None = None) -> str:
    """One frame of an activity's mark (wrapping, so ``index`` may be any integer)."""
    drawn = frames(name, theme=theme)
    return drawn[int(index) % len(drawn)]


def mark(name: str, *, theme: Theme | None = None, frame: int = 0,
         progress: float | None = None) -> str:
    """The bare mark of an activity: ``◇`` / ``▓░░░`` / ``[▸_]`` / ``■`` …

    ``progress`` (a real fraction 0-1) replaces the ``working`` track with a measured bar
    (``██░░``) — the only case where a mark claims an extent, and it is never invented:
    the caller passes a fraction it measured or nothing at all.
    """
    current = _theme(theme)
    key = resolve(name)
    if key == "working" and progress is not None:
        try:
            value = min(1.0, max(0.0, float(progress)))
        except (TypeError, ValueError):
            value = 0.0
        return progress_track(value, theme=current)
    drawn = frames(key, theme=current)
    if len(drawn) == 1:
        return drawn[0]
    return drawn[int(frame) % len(drawn)]


def progress_track(fraction: float, *, width: int = 4, theme: Theme | None = None) -> str:
    """A measured bar, four columns by default: ``██░░`` / ``##--``.

    The theme's own bar glyphs, so a bar drawn here and a bar drawn by
    :func:`skaldir.render.progress` are the same characters.
    """
    current = _theme(theme)
    columns = max(1, int(width))
    filled = int(round(min(1.0, max(0.0, float(fraction))) * columns))
    return (current.glyph("bar_full") * filled
            + current.glyph("bar_empty") * (columns - filled))


def mark_width(name: str, *, theme: Theme | None = None) -> int:
    """The columns the mark of an activity consumes (its widest frame)."""
    return max(render.width(item) for item in frames(name, theme=theme))


# --------------------------------------------------------------------------- #
# measurements — printed only when they exist                                  #
# --------------------------------------------------------------------------- #


def ms_label(ms: Any) -> str:
    """A measured duration as ``412 ms``; an *unknown* one is ``""``.

    ``None``/``""``/a bool/an unparseable value mean "not measured", and the caller then prints
    no duration at all — the rule ``RunResult.footer`` follows with its token count. A measured
    zero is printed (``0 ms``), because zero is a measurement and a fast tool is a fact.
    """
    if ms is None or ms == "" or isinstance(ms, bool):
        return ""
    try:
        value = int(float(ms))
    except (TypeError, ValueError):
        return ""
    return f"{max(0, value)} ms"


def hits_label(hits: Any) -> str:
    """A reported hit count as ``12 hits`` / ``1 hit``; unknown is ``""``.

    A real zero is printed (``0 hits`` is a measurement and often the answer); ``None``
    means the search did not report a count and nothing is printed.
    """
    if hits is None or hits == "" or isinstance(hits, bool):
        return ""
    try:
        value = int(float(hits))
    except (TypeError, ValueError):
        return ""
    value = max(0, value)
    return f"{value} hit" + ("" if value == 1 else "s")


def diff_label(added: Any, removed: Any, *, theme: Theme | None = None) -> str:
    """``+12 −4`` (``+12 -4`` in ASCII) — only the counts that were reported.

    ``None`` on one side drops that side rather than printing ``+0``, because ``+0`` is a
    measurement and an absent count is not. ``None`` on both sides returns ``""``.
    """
    current = _theme(theme)
    plus, minus = _count(added), _count(removed)
    if plus is None and minus is None:
        return ""
    minus_sign = MINUS_SIGN if current.unicode else "-"
    parts = []
    if plus is not None:
        parts.append(f"+{plus}")
    if minus is not None:
        parts.append(f"{minus_sign}{minus}" if plus is not None else f"{minus_sign}{minus}")
    return " ".join(parts)


def _count(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# the lines                                                                    #
# --------------------------------------------------------------------------- #


def _compose(mark_text: str, *, theme: Theme, token: str = "key", label: str = "",
             keyword: str = "", suffix: str = "") -> str:
    """``mark  keyword label  suffix`` — one line, truncated to :data:`LINE_BUDGET`.

    The two-space rhythm after the mark is the same one
    :meth:`skaldir.theme.Theme.mark_line` uses, so an activity line and a status line line
    up column for column. The mark is drawn in ``token``, the safety ``keyword`` in the
    *strong* token (bold, not reverse: a keyword must never read as a failure), the label in
    plain ink and the measurement muted. The label is what gives when the line is narrow —
    the same rule the shell's activity indicator follows with its clock.
    """
    used = (render.width(mark_text) + 2 if mark_text else 0)
    if keyword:
        used += render.width(keyword) + len(KEYWORD_GAP) + (2 if mark_text else 0)
    if suffix:
        used += render.width(suffix) + 2
    room = max(MIN_LABEL, LINE_BUDGET - used)
    shown = render.truncate(_fold(label, theme), room, theme=theme) if label else ""
    head = theme.style(mark_text, token) if mark_text else ""
    body = theme.style(keyword, "strong") if keyword else ""
    if shown:
        body = f"{body}{KEYWORD_GAP if body else ''}{shown}"
    line = f"{head}  {body}".rstrip() if head else body
    if suffix:
        line += "  " + theme.style(suffix, "muted")
    return line


def thinking(label: str = "thinking", *, frame: int = 0, elapsed_ms: Any = None,
             theme: Theme | None = None) -> str:
    """``◇ thinking`` — the model is reasoning (frame 0-3, or the frame for ``elapsed_ms``)."""
    current = _theme(theme)
    index = frame_index("thinking", elapsed_ms) if elapsed_ms is not None else int(frame)
    glyph = VOCABULARY["thinking"]
    return _compose(mark("thinking", theme=current, frame=index), theme=current,
                    token=glyph.token, label=label or glyph.purpose)


def working(label: str = "working", *, frame: int = 0, elapsed_ms: Any = None,
            progress: Any = None, theme: Theme | None = None) -> str:
    """``▓░░░ working`` — work in progress; with a measured ``progress``, ``██░░ 62%``.

    The percentage is only ever printed next to a real fraction: an unknown extent gets the
    travelling head and no number at all.
    """
    current = _theme(theme)
    fraction = _fraction(progress)
    index = frame_index("working", elapsed_ms) if elapsed_ms is not None else int(frame)
    drawn = mark("working", theme=current, frame=index, progress=fraction)
    suffix = "" if fraction is None else f"{int(round(fraction * 100))}%"
    return _compose(drawn, theme=current, token=VOCABULARY["working"].token,
                    label=label or VOCABULARY["working"].purpose, suffix=suffix)


def tool(name: str, ms: Any = None, *, ok: bool | None = None,
         theme: Theme | None = None) -> str:
    """``[▸_] read_text  41 ms`` — one named tool with its measured duration.

    ``ok=False`` appends the theme's fail mark (a tool that ran and failed is a fact worth
    the one column); ``ok=None`` says nothing was reported and nothing is drawn.
    """
    current = _theme(theme)
    suffix = ms_label(ms)
    line = _compose(mark("tool", theme=current), theme=current, token=VOCABULARY["tool"].token,
                    label=name or "tool", suffix=suffix)
    if ok is False:
        return line + "  " + current.style(current.mark("fail"), "fail")
    return line


def search(query: str, hits: Any = None, *, theme: Theme | None = None) -> str:
    """``⌕~ "agent runtime"  12 hits`` — a web search, its query and its reported hits."""
    current = _theme(theme)
    quoted = f'"{query}"' if query else ""
    return _compose(mark("search", theme=current), theme=current,
                    token=VOCABULARY["search"].token, label=quoted,
                    suffix=hits_label(hits))


def read(path: str, *, ms: Any = None, theme: Theme | None = None) -> str:
    """``▤ src/skaldir/art.py  41 ms`` — a file read (the duration only when measured)."""
    current = _theme(theme)
    return _compose(mark("read", theme=current), theme=current, token=VOCABULARY["read"].token,
                    label=path or "?", suffix=ms_label(ms))


def write(path: str, *, ms: Any = None, theme: Theme | None = None) -> str:
    """``▥ src/skaldir/art.py  12 ms`` — a file written or created."""
    current = _theme(theme)
    return _compose(mark("write", theme=current), theme=current, token=VOCABULARY["write"].token,
                    label=path or "?", suffix=ms_label(ms))


def edit(path: str, added: Any = None, removed: Any = None, *, ms: Any = None,
         theme: Theme | None = None) -> str:
    """``± src/skaldir/art.py  +12 −4`` — a change, echoing the diff stat it was given.

    The stat comes from the edit record (never recounted here, so a line and the diff under
    it cannot disagree); the duration is appended only when the record carried one.
    """
    current = _theme(theme)
    stat = diff_label(added, removed, theme=current)
    suffix = "  ".join(part for part in (stat, ms_label(ms)) if part)
    return _compose(mark("edit", theme=current), theme=current, token=VOCABULARY["edit"].token,
                    label=path or "?", suffix=suffix)


def subagents(count: Any = 0, *, state: str = "running", label: str = "subagents",
              theme: Theme | None = None) -> str:
    """``│││  3 subagents running`` — parallel children, one bar per child (up to 8).

    The *number* is the fact and is always printed; the bars are the glance, so a 12-child
    fan-out is ``││││││││  12 subagents running`` and never a lie about how many are there.
    Zero children draw the theme's info dot (an empty mark would be invisible), and the noun
    is singularised for exactly one child.
    """
    current = _theme(theme)
    number = _count(count) or 0
    bar = SUBAGENT_BAR[0] if current.unicode else SUBAGENT_BAR[1]
    drawn = bar * min(number, MAX_BARS)
    noun = label or "subagents"
    if number == 1 and noun.endswith("s"):
        noun = noun[:-1]
    text = f"{number} {noun}"
    if state:
        text += f" {state}"
    mark_text = drawn or current.mark("info")
    return _compose(mark_text, theme=current, token=VOCABULARY["subagents"].token, label=text)


def verify(subject: str, *, status: str = "ok", reason: str = "",
           theme: Theme | None = None) -> str:
    """``✓ verified  tests pass`` / ``✗ failed  …`` / ``? UNKNOWN  …`` — a check's outcome.

    ``status`` is one of :data:`VERIFY_STATUSES`. ``unknown`` is a real outcome and is
    spelled :data:`KEYWORD_UNKNOWN` in capitals: a check that could not decide must be
    visibly different from one that passed.
    """
    current = _theme(theme)
    key = str(status or "").strip().lower()
    if key == "unknown":
        key = "unknown"
    elif key not in VERIFY_STATUSES:
        key = "unknown"
    drawn = VERIFY_MARKS[key]
    mark_text = current.mark(drawn) if drawn in {"ok", "fail", "blocked"} else drawn
    token = "ok" if key == "ok" else ("fail" if key == "fail" else "warn")
    text = "  ".join(part for part in (_fold(subject, current), _fold(reason, current)) if part)
    return _compose(mark_text, theme=current, token=token, keyword=VERIFY_KEYWORDS[key],
                    label=text or "not reported")


def unknown(subject: str, *, reason: str = "", theme: Theme | None = None) -> str:
    """``? UNKNOWN  which provider answered`` — the explicit not-known mark."""
    return verify(subject, status="unknown", reason=reason, theme=theme)


def blocked(reason: str = "", *, kind: str = "blocked", theme: Theme | None = None) -> str:
    """``■ BLOCKED  approval declined by the owner`` — a refusal, never a success.

    ``kind="declined"`` draws the same mark with :data:`KEYWORD_DECLINED`; both are
    gate outcomes. ``kind="failed"`` draws it with :data:`KEYWORD_FAILED` — a call
    that **ran** and then failed (audit 46 #1/#15): never the gate's word.
    """
    current = _theme(theme)
    kind_word = str(kind).strip().lower()
    keyword = {
        "declined": KEYWORD_DECLINED,
        "failed": KEYWORD_FAILED,
    }.get(kind_word, KEYWORD_BLOCKED)
    return _compose(current.mark("blocked"), theme=current, token=VOCABULARY["blocked"].token,
                    keyword=keyword, label=reason or "")


def declined(reason: str = "", *, theme: Theme | None = None) -> str:
    """``■ DECLINED  …`` — the owner (or a policy) said no."""
    return blocked(reason, kind="declined", theme=theme)


# --------------------------------------------------------------------------- #
# records: the seam the chat and the tool lines use                            #
# --------------------------------------------------------------------------- #


def _first(record: dict, keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return default


def line(record: Any, *, theme: Theme | None = None, indent: int = 0, frame: int = 0,
         elapsed_ms: Any = None) -> str:
    """Draw one activity record as a line — the one call a chat row needs.

    The record is a mapping (typically a run's own event or the chat's row state):
    ``{"activity": "read", "path": "a.py", "ms": 41}``. The activity is read from
    ``activity``/``kind``/``glyph``; without one, the record's ``name`` (a real tool name)
    decides through :func:`activity_for` — so a ``tool.call`` event from a transcript renders
    as the right mark with no mapping table at the call site. An unknown record draws the
    generic :func:`tool` line rather than guessing.
    """
    current = _theme(theme)
    if not isinstance(record, dict):
        return ""
    named = str(_first(record, ACTIVITY_KEYS, "") or "")
    tool_name = str(record.get("name") or record.get("tool") or "")
    key = resolve(named) if named else (activity_for(tool_name) if tool_name else "tool")
    ms = _first(record, MS_KEYS)
    ok = record.get("ok")
    ok_value = None if ok is None else bool(ok)
    label = _first(record, LABEL_KEYS, "")
    args = record.get("arguments") if isinstance(record.get("arguments"), dict) else {}
    path = _first(record, PATH_KEYS) or args.get("path") or args.get("file") or args.get("target")
    drawn = ""
    if key == "thinking":
        drawn = thinking(str(label or "thinking"), frame=frame, elapsed_ms=elapsed_ms,
                         theme=current)
    elif key == "working":
        drawn = working(str(label or "working"), frame=frame, elapsed_ms=elapsed_ms,
                        progress=record.get("progress"), theme=current)
    elif key == "search":
        query = _first(record, ("query", "q", "text")) or args.get("query") or label or ""
        drawn = search(str(query), _first(record, ("hits", "results", "count")), theme=current)
    elif key == "read":
        drawn = read(str(path or label or "?"), ms=ms, theme=current)
    elif key == "write":
        drawn = write(str(path or label or "?"), ms=ms, theme=current)
    elif key == "edit":
        drawn = edit(str(path or label or "?"), record.get("added"), record.get("removed"),
                     ms=ms, theme=current)
    elif key == "subagents":
        drawn = subagents(_first(record, ("count", "children", "n"), 0),
                          state=str(record.get("state", "running") or ""),
                          label=str(record.get("label", "subagents") or "subagents"),
                          theme=current)
    elif key == "verify":
        drawn = verify(str(label), status=str(record.get("status", "ok") or "ok"),
                       reason=str(record.get("reason", "") or ""), theme=current)
    elif key == "blocked":
        drawn = blocked(str(record.get("reason") or label or ""),
                        kind=str(record.get("kind") or "blocked") if named == "blocked"
                        else str(record.get("status") or "blocked"),
                        theme=current)
    else:
        drawn = tool(tool_name or str(label or named or "tool"), ms, ok=ok_value, theme=current)
    return " " * max(0, int(indent)) + drawn


def from_tool_event(event: Any, *, theme: Theme | None = None, indent: int = 0) -> str:
    """Draw a real ``tool.call`` transcript event (``name``/``arguments``/``duration_ms``).

    This is the tool-line seam: an event straight out of ``agent_loop.read_transcript()``
    renders as the mark its activity deserves, with the duration the loop *measured*. A
    refused call (``decision`` is not an allow/execute outcome, case-insensitively)
    draws the blocked mark instead of a tool mark, because that is what happened —
    a call classified ALLOW runs (``line`` marks :data:`ok` False on its own).
    """
    current = _theme(theme)
    if not isinstance(event, dict):
        return ""
    decision = str(event.get("decision") or "").strip()
    folded = decision.lower()
    # one vocabulary, one copy (audit 46 #2): the allow words for rendering are
    # toolcall's — art never keeps a hand-copied twin of that set.
    allow = {word.lower() for word in _ALLOW_DECISION_TOKENS}
    executed = bool(event.get("executed"))
    # keyed on *execution*, not on ``ok`` (audit 46 #1/#15): a call the loop
    # executed and that then failed draws FAILED — the gate did not refuse it.
    # DECLINED is reserved for non-executed refusals.
    if decision and (folded not in allow or event.get("ok") is False):
        note = f"{event.get('name', 'tool')} {current.glyph('sep')} {decision}"
        return " " * max(0, int(indent)) + blocked(
            _fold(note, current),
            kind="failed" if executed else "declined", theme=current)
    record = dict(event)
    record["activity"] = activity_for(str(event.get("name") or ""))
    return line(record, theme=current, indent=indent)


def transcript(records: Any, *, theme: Theme | None = None, indent: int = CHAT_INDENT,
               frame: int = 0, elapsed_ms: Any = None) -> str:
    """Several records as one block of activity lines (the chat's own rhythm)."""
    out: list[str] = []
    for record in records or ():
        if isinstance(record, dict) and record.get("frame") is not None:
            indexed = dict(record)
            drawn = line(indexed, theme=theme, indent=indent,
                         frame=int(record.get("frame", frame)),
                         elapsed_ms=record.get("elapsed_ms", elapsed_ms))
        else:
            drawn = line(record, theme=theme, indent=indent, frame=frame, elapsed_ms=elapsed_ms)
        if drawn.strip():
            out.append(drawn.rstrip())
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# browsing the vocabulary                                                      #
# --------------------------------------------------------------------------- #


def _glyphs(theme: Theme, unicode: bool) -> Theme:  # noqa: A002 — the axis is the name
    """The same theme with the glyph axis forced: the table states both twins at once."""
    return theme.replace(glyphs=GLYPHS_UNICODE if unicode else GLYPHS_ASCII)


def _fraction(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return None


def example(name: str, *, theme: Theme | None = None) -> str:
    """One realistic line per activity — the same strings the preview and the tests use."""
    return {
        "thinking": lambda: thinking(theme=theme),
        "working": lambda: working("building the index", theme=theme),
        "tool": lambda: tool("read_text", 41, theme=theme),
        "search": lambda: search("skaldir art vocabulary", 12, theme=theme),
        "read": lambda: read("src/skaldir/art.py", ms=3, theme=theme),
        "write": lambda: write("MVP/67-activity-art.md", ms=12, theme=theme),
        "edit": lambda: edit("src/skaldir/art.py", 12, 4, ms=9, theme=theme),
        "subagents": lambda: subagents(3, theme=theme),
        "verify": lambda: verify("80 columns hold", theme=theme),
        "blocked": lambda: blocked("approval declined by the owner", theme=theme),
    }[resolve(name)]()


def describe(*, theme: Theme | None = None) -> list[dict[str, Any]]:
    """The vocabulary as data (the width table, the timings, the ASCII twins)."""
    current = _theme(theme)
    rows: list[dict[str, Any]] = []
    for name in NAMES:
        glyph = VOCABULARY[name]
        drawn = example(name, theme=current)
        rows.append({
            "activity": name,
            "purpose": glyph.purpose,
            "when": glyph.when,
            "mark_unicode": mark(name, theme=_glyphs(current, True)),
            "mark_ascii": mark(name, theme=_glyphs(current, False)),
            "mark_width_unicode": mark_width(name, theme=_glyphs(current, True)),
            "mark_width_ascii": mark_width(name, theme=_glyphs(current, False)),
            "frames": list(frames(name, theme=_glyphs(current, True))),
            "ascii_frames": list(frames(name, theme=_glyphs(current, False))),
            "frame_count": frame_count(name),
            "interval_ms": interval_ms(name),
            "bracketed": glyph.bracketed,
            "example": drawn,
            "line_width": render.width(drawn),
            "indented_width": render.width(drawn) + CHAT_INDENT,
        })
    return rows


def widths(*, theme: Theme | None = None) -> list[tuple[str, int, int, int]]:
    """``(activity, mark width unicode, mark width ascii, composed line width)``."""
    return [(row["activity"], row["mark_width_unicode"], row["mark_width_ascii"],
             row["line_width"]) for row in describe(theme=theme)]


def table(*, theme: Theme | None = None, columns: int | None = None) -> str:
    """The width table as :func:`skaldir.render.table` rows (the reference surface).

    The mark column is drawn in *this* theme, so a plain render of the table is pure ASCII;
    the column widths still report both modes (``unicode/ascii``), because the width a glyph
    consumes is the fact the table exists to state.
    """
    current = _theme(theme)
    rows = [[row["activity"],
             mark(row["activity"], theme=current),
             f'{row["mark_width_unicode"]} / {row["mark_width_ascii"]}',
             f'{row["frame_count"]}',
             f'{row["interval_ms"]} ms' if row["interval_ms"] else "-",
             f'{row["line_width"]} (+{CHAT_INDENT})']
            for row in describe(theme=current)]
    return render.table(
        ["activity", "mark", "cols uni/ascii", "frames", "frame", "line"], rows,
        theme=current, max_width=columns)


def preview(*, theme: Theme | None = None, columns: int | None = None) -> str:
    """Every mark this module can draw, in the mode the theme asked for.

    The reference surface: one line per activity (mark, example, when it is shown), then the
    frame strips at their real timing, then the width table, then the safety legend. It is
    what ``skaldir art preview`` prints and what ``--ascii`` renders in plain ASCII.
    """
    current = _theme(theme)
    rows = describe(theme=current)
    limit = render.terminal_width() if columns is None else max(render.MIN_WIDTH, int(columns))

    def paragraph(text: str) -> list[str]:
        """Prose in the preview wraps to the screen (a reference surface must fit one)."""
        body = render.wrap(_fold(text, current), max(render.MIN_COLUMN, limit - 2),
                           cont=2, theme=current)
        return ["  " + line for line in body.split("\n")]

    out: list[str] = [render.heading(
        _fold("Activity art \u2014 one glyph per thing the agent does", current),
        theme=current, columns=columns)]
    out.append(render.key_values(
        [("mode", current.mode), ("glyphs", current.glyphs), ("frame", f"{FRAME_INTERVAL_MS} ms"),
         ("budget", f"{LINE_BUDGET} cols (+{CHAT_INDENT} indent)")], theme=current))
    out.append("")
    out.append(render.bar("vocabulary", theme=current, columns=columns))
    for row in rows:
        out.append(f"  {row['example']}")
        out.append(f"    {current.style(_fold(row['when'], current), 'muted')}")
    out.append("")
    out.append(render.bar("frames", theme=current, columns=columns))
    ascii_theme = current.replace(mode=MODE_PLAIN, depth=DEPTH_NONE, glyphs=GLYPHS_ASCII)
    for name in ("thinking", "working"):
        out.append(f"  {name}: " + " ".join(frame(name, index, theme=current)
                                            for index in range(frame_count(name)))
                   + f"    {frame_count(name)} frames x {interval_ms(name)} ms")
        out.append("    ascii:  " + " ".join(frame(name, index, theme=ascii_theme)
                                            for index in range(frame_count(name))))
    out.extend(paragraph(f"a frame advances only every {FRAME_INTERVAL_MS} ms; "
                         "nothing here spins"))
    out.append("")
    out.append(render.bar("widths", theme=current, columns=columns))
    out.append(table(theme=current, columns=columns))
    out.append("")
    out.append(render.bar("safety", theme=current, columns=columns))
    for name in ("verify", "blocked"):
        out.append(f"  {example(name, theme=current)}")
    out.append(f"  {unknown('which provider answered', theme=current)}")
    out.append(f"  {declined('approval refused by policy', theme=current)}")
    out.extend(paragraph(
        "UNKNOWN and BLOCKED/DECLINED are words on purpose: a font without the mark still "
        "says what happened"))
    out.append(render.end_rule(theme=current, columns=columns))
    out.extend(paragraph(
        f"{len(NAMES)} activities {current.glyph('sep')} {LINE_BUDGET} column budget "
        f"{current.glyph('sep')} {FRAME_INTERVAL_MS} ms per frame "
        f"{current.glyph('sep')} ascii fallback: every mark has an ASCII twin"))
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# the CLI group: `skaldir art …`                                               #
# --------------------------------------------------------------------------- #

def _product_contract() -> Any:
    """The product CLI's envelope module, when this build has one."""
    return cli_shared.product_contract()


_CONTRACT: Any = _product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))


def _theme_for(args: argparse.Namespace) -> Theme:
    """The theme this invocation asked for (``--ascii`` forces the plain ASCII rendering)."""
    if getattr(args, "ascii", False):
        return theme_mod.set(theme_mod.get().replace(
            mode=MODE_PLAIN, depth=DEPTH_NONE, glyphs=GLYPHS_ASCII))
    return theme_mod.for_args(args)


def _report(data: dict[str, Any], evidence: list[dict[str, str]] | None = None) -> None:
    """Fill the product envelope when this build has one (a preview must never break it)."""
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(data)
        for item in evidence or []:
            _CONTRACT.add_evidence(item.get("ref", ""), item.get("kind", "code"),
                                   note=item.get("note", ""))
    except Exception:  # noqa: BLE001 — reporting must never break the command
        return


def cmd_preview(args: argparse.Namespace) -> int:
    """Print every mark, its timing and the width table."""
    theme = _theme_for(args)
    print(preview(theme=theme))
    _report({"activities": [row["activity"] for row in describe(theme=theme)],
             "frame_ms": FRAME_INTERVAL_MS, "budget": LINE_BUDGET},
            evidence=[{"ref": "src/skaldir/art.py", "kind": "code",
                       "note": "the vocabulary and its ASCII fallback"}])
    return EXIT_OK


def cmd_json(args: argparse.Namespace) -> int:
    """The vocabulary as JSON (the width table and the timings, machine-readable)."""
    theme = _theme_for(args)
    print(json.dumps({"activities": describe(theme=theme), "frame_ms": FRAME_INTERVAL_MS,
                      "chat_indent": CHAT_INDENT, "line_budget": LINE_BUDGET,
                      "statuses": list(VERIFY_STATUSES)}, indent=2))
    return EXIT_OK


def _add_verbs(sub: Any) -> Any:
    preview_parser = sub.add_parser("preview", help="every activity mark, its timing, the widths")
    preview_parser.add_argument("--ascii", action="store_true",
                                help="force the plain ASCII rendering")
    preview_parser.add_argument("--json", action="store_true")
    preview_parser.set_defaults(func=cmd_preview, command_path="art.preview")

    json_parser = sub.add_parser("json", help="the vocabulary as data")
    json_parser.add_argument("--ascii", action="store_true")
    json_parser.add_argument("--json", action="store_true")
    json_parser.set_defaults(func=cmd_json, command_path="art.json")
    for child in (preview_parser, json_parser):
        cli_shared.share_common_flags(child)
    return sub


def register_cli(subparsers: Any) -> Any:
    """Wire the ``art`` group (idempotent: wiring twice returns the group that is there)."""
    choices = getattr(subparsers, "choices", None)
    if isinstance(choices, dict) and GROUP in choices:
        return choices[GROUP]
    parser = subparsers.add_parser(
        GROUP,
        help="the activity vocabulary (one mark per thing the agent does)",
        description=(
            "Monochrome pictograms for an *ongoing* action: thinking (a breathing diamond), "
            "working, a tool call with its measured duration, a web search, read/write/edit, "
            "parallel subagents, verify (with an explicit UNKNOWN) and blocked/declined. "
            "Every mark has a plain-ASCII twin, so a pipe or --no-color renders the same "
            "line in ASCII with no escape sequences."
        ),
    )
    _add_verbs(parser.add_subparsers(dest="art_command", required=True))
    return parser


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``python -m skaldir.art``)."""
    parser = argparse.ArgumentParser(prog=f"skaldir {GROUP}",
                                     description="the activity art vocabulary")
    _add_verbs(parser.add_subparsers(dest="command", required=True))
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir art …`` — returns the exit code, never raises for a request."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_OK if not exc.code else EXIT_USAGE
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    return int(func(args))


# --------------------------------------------------------------------------- #
# the hand-over to cli.py / cli_contract.py                                    #
# --------------------------------------------------------------------------- #

CONTRACTS_SNIPPET = '''
    # -- the activity vocabulary (COMPOSE: one mark per ongoing action) -------- #
    # `register_cli(sub)` is called in `build_parser()` right after the root
    # `add_subparsers(...)`; the group owns its two entries below.
    "art.preview": _C("COMPOSE", (_ev("src/skaldir/art.py",
                                      note="the vocabulary: marks, frames, widths, ascii twin"),
                                  _ev("tests/test_art.py", "test",
                                      note="the ASCII fallback and the width budget")),
                      "every activity mark, its frame timing and its width",
                      exit_codes=(0, 1, 7),
                      data_schema={"type": "object", "properties": {
                          "activities": {"type": "array"}, "frame_ms": {"type": "integer"},
                          "budget": {"type": "integer"}},
                          "required": ["activities", "frame_ms", "budget"]},
                      notes="the reference render; --ascii forces the plain ASCII mode"),
    "art.json": _C("COMPOSE", (_ev("src/skaldir/art.py",
                                   note="the same vocabulary as data"),),
                   "the activity vocabulary as JSON (marks, frames, widths)",
                   exit_codes=(0, 1, 7),
                   data_schema={"type": "object", "properties": {
                       "activities": {"type": "array"}, "frame_ms": {"type": "integer"},
                       "chat_indent": {"type": "integer"}, "line_budget": {"type": "integer"},
                       "statuses": {"type": "array"}},
                       "required": ["activities", "line_budget"]}),
'''

WIRING_SNIPPET = '''# --- src/skaldir/cli.py, inside build_parser(), right after
#     sub = parser.add_subparsers(dest="command", required=False)
from .art import register_cli as _register_art

_register_art(sub)
'''


def _ev(ref: str, kind: str = "code", note: str = "") -> dict[str, str]:
    """One evidence record (the same shape ``cli_contract`` carries)."""
    return {"ref": ref, "kind": kind, "note": note}


def _spec_pairs() -> dict[str, Any]:
    """``{command path: CommandSpec}`` built lazily (does not import the CLI itself)."""
    if _CONTRACT is None or not hasattr(_CONTRACT, "CommandSpec"):
        return {}
    C = _CONTRACT.CommandSpec
    spec = {
        f"{GROUP}.preview": C(
            "COMPOSE",
            (_ev("src/skaldir/art.py",
                 note="the vocabulary: marks, frames, widths, ascii twin"),
             _ev("tests/test_art.py", "test",
                 note="the ASCII fallback and the width budget")),
            "every activity mark, its frame timing and its width",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_INTERNAL),
            data_schema={"type": "object", "properties": {
                "activities": {"type": "array"}, "frame_ms": {"type": "integer"},
                "budget": {"type": "integer"}},
                "required": ["activities", "frame_ms", "budget"]},
            notes="the reference render; --ascii forces the plain ASCII mode",
        ),
        f"{GROUP}.json": C(
            "COMPOSE",
            (_ev("src/skaldir/art.py", note="the same vocabulary as data"),),
            "the activity vocabulary as JSON (marks, frames, widths)",
            exit_codes=(EXIT_OK, EXIT_USAGE, EXIT_INTERNAL),
            data_schema={"type": "object", "properties": {
                "activities": {"type": "array"}, "frame_ms": {"type": "integer"},
                "chat_indent": {"type": "integer"}, "line_budget": {"type": "integer"},
                "statuses": {"type": "array"}},
                "required": ["activities", "line_budget"]},
        ),
    }
    return spec


def install_contract_entries() -> list[str]:
    """Add this group's ``COMMANDS`` entries (idempotent, never overwrites).

    Not called at import time on purpose: until :data:`WIRING_SNIPPET` is pasted into
    ``cli.py`` the group is not in the parser, and an entry without a command would be a
    claim without a surface. ``tests/test_art_cli.py`` calls it inside the test that wires
    the group, so the hand-over is proven without touching a file this module does not own.
    """
    if _CONTRACT is None or not hasattr(_CONTRACT, "COMMANDS"):
        return []
    return list(cli_shared.install_contract_entries(_spec_pairs(), _CONTRACT))


__all__ = [
    "ACTIVITY_KEYS",
    "ALIASES",
    "CHAT_INDENT",
    "CONTRACTS_SNIPPET",
    "FRAME_INTERVAL_MS",
    "GROUP",
    "Glyph",
    "KEYWORD_BLOCKED",
    "KEYWORD_DECLINED",
    "KEYWORD_GAP",
    "KEYWORD_UNKNOWN",
    "LINE_BUDGET",
    "MAX_BARS",
    "MIN_LABEL",
    "MINUS_SIGN",
    "NAMES",
    "PICTOGRAMS",
    "SUBAGENT_BAR",
    "THINKING_ASCII_FRAMES",
    "THINKING_FRAMES",
    "VERIFY_KEYWORDS",
    "VERIFY_MARKS",
    "VERIFY_STATUSES",
    "VOCABULARY",
    "WIRING_SNIPPET",
    "WORKING_ASCII_FRAMES",
    "WORKING_FRAMES",
    "activity_for",
    "blocked",
    "build_parser",
    "declined",
    "describe",
    "diff_label",
    "edit",
    "example",
    "frame",
    "frame_count",
    "frame_index",
    "frames",
    "from_tool_event",
    "hits_label",
    "install_contract_entries",
    "interval_ms",
    "line",
    "main",
    "mark",
    "mark_width",
    "ms_label",
    "preview",
    "progress_track",
    "read",
    "register_cli",
    "resolve",
    "search",
    "subagents",
    "table",
    "thinking",
    "tool",
    "transcript",
    "unknown",
    "verify",
    "widths",
    "working",
    "write",
]


if __name__ == "__main__":  # pragma: no cover — exercised through main()
    raise SystemExit(main())
