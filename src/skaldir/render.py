"""Text helpers for the Skaldir CLI: bars, rules, tables, key/value blocks, steps.

Everything here returns a string and never prints — the command decides where it goes.
Every helper takes a :class:`skaldir.theme.Theme` (default: the process theme) and is
written so that **a plain theme produces pure ASCII with no escape sequences at all**:
the same call renders box-drawing glyphs on a TTY and ``---`` in a log file.

The vocabulary is small on purpose (see ``docs/CLI-STYLE.md``):

* ``bar`` — the section separator: ``─── COMMANDS ─────────────────────────``. One per
  multi-section output, full width, never wrapped, the only ALL-CAPS element.
* ``end_rule`` — the light rule that closes a section before a footer/summary line.
* ``status_line`` / ``step_line`` — one state per line, *mark + two spaces + text*.
* ``table`` — aligned columns, no silent wrapping; ``key_values`` for one record,
  ``progress`` for a bar, ``bullets`` for a short list.
* ``legend`` — the glyph legend, for the reference surfaces only (never on a command's
  normal output: it would be noise there).
* ``heading`` — a title (level 1) or a bar with a label (level 2), ``box`` when a note
  must not be missed, ``spacer`` for the single blank line between sections.

Hierarchy is three signals, and nothing else: the bar (a section), the two-space mark
rhythm (a line), and a four-space indent (detail under that line, drawn dim).
"""

from __future__ import annotations

import re
import shutil
import textwrap
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from . import theme as theme_mod
from .theme import Theme

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

#: width of the column separator (``" │ "`` / ``" | "``)
GAP = 3
MIN_COLUMN = 3
DEFAULT_WIDTH = 80
DEFAULT_HEIGHT = 24
MIN_WIDTH = 40
MAX_WIDTH = 240
MIN_HEIGHT = 8
MAX_HEIGHT = 500

#: the bar's lead-in (``─── LABEL ``) — three strokes, then one space, then the label
BAR_LEAD = 3

#: the two weights a rule comes in: the section bar and the light ending rule
HEAVY = "heavy"
LIGHT = "light"

#: how far a detail line is indented under the status line it belongs to
DETAIL_INDENT = 4

#: the legend's order: the four statuses every command uses
LEGEND_STATUSES = ("ok", "fail", "blocked", "warn")


def _theme(theme: Theme | None) -> Theme:
    return theme if theme is not None else theme_mod.get()


def terminal_width(default: int = DEFAULT_WIDTH) -> int:
    """The usable width: ``$SKALDIR_WIDTH``/``$COLUMNS``, else the real terminal size.

    A pipe with nothing declared is **80 columns** — the width the CLI is written for,
    so a redirect and an 80-column terminal render the same body.
    """
    detected = theme_mod.get().width
    if not detected:
        try:
            detected = int(shutil.get_terminal_size((default, DEFAULT_HEIGHT)).columns)
        except Exception:  # noqa: BLE001 — a console without a size is a pipe
            detected = default
    return max(MIN_WIDTH, min(MAX_WIDTH, int(detected or default)))


def terminal_height(default: int = DEFAULT_HEIGHT) -> int:
    """The usable height: ``$SKALDIR_HEIGHT``/``$LINES``, else the real terminal size.

    Used for the one depth decision the CLI makes by itself: whether the startup art gets
    its full band or the compact mark (see :func:`skaldir.logo.splash`).
    """
    detected = theme_mod.get().height
    if not detected:
        try:
            detected = int(shutil.get_terminal_size((DEFAULT_WIDTH, default)).lines)
        except Exception:  # noqa: BLE001
            detected = default
    return max(MIN_HEIGHT, min(MAX_HEIGHT, int(detected or default)))


#: The characters a *plain* stream never gets: the CLI's own typography is spelled in
#: ASCII there (an em dash a probe reason borrowed from somewhere becomes ``-``). Only
#: the known set is folded — an unknown glyph (a CJK path, say) passes through unchanged,
#: because mangling somebody's data would be worse than one wide character in a log.
ASCII_FOLD = str.maketrans({
    "\u00a0": " ", "\u00ad": "", "\u2010": "-", "\u2011": "-", "\u2012": "-",
    "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-", "\u00b7": "-",
    "\u2022": "*", "\u2027": "-", "\u2039": "<", "\u203a": ">", "\u2026": "...",
    "\u2190": "<-", "\u2192": "->", "\u2194": "<->", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2713": "+", "\u2717": "x", "\u25a0": "#",
    "\u25aa": "*", "\u2500": "-", "\u2502": "|", "\u250c": "+", "\u2510": "+",
    "\u2514": "+", "\u2518": "+", "\u251c": "+", "\u2524": "+", "\u253c": "+",
    "\u2588": "#", "\u2591": "-", "\u2592": "=",
})


def ascii_fold(text: str) -> str:
    """Spell the CLI's typography in ASCII (``—`` → ``-``, ``…`` → ``...``, ``→`` → ``->``).

    Applied automatically by :func:`truncate` and :func:`wrap` on a plain theme, so text
    a command *borrowed* (a probe's reason, a stored detail) cannot break the ASCII
    promise of a redirect. Unknown characters are left alone on purpose.
    """
    return str(text).translate(ASCII_FOLD)


def _fold(text: str, theme: Theme) -> str:
    return ascii_fold(text) if theme.plain else text


def strip_ansi(text: str) -> str:
    """Drop every SGR sequence (used for width maths and for the plain-mode tests)."""
    return ANSI_RE.sub("", text)


def width(text: str) -> int:
    """The display width of ``text``, ignoring escapes and counting wide glyphs as 2."""
    total = 0
    for char in strip_ansi(text):
        if unicodedata.combining(char):
            continue
        total += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return total


def truncate(text: str, limit: int, *, theme: Theme | None = None) -> str:
    """Cut ``text`` to ``limit`` visible columns, marking the cut with ``…``/``...``.

    The result never exceeds ``limit`` — for a limit narrower than the ellipsis itself
    the ellipsis is cut short rather than the budget being overrun. On a plain theme the
    text is folded to ASCII first (see :func:`ascii_fold`).
    """
    current = _theme(theme)
    if limit <= 0:
        return ""
    text = _fold(text, current)
    if width(text) <= limit:
        return text
    cut = current.glyph("ellipsis")
    if limit < len(cut):
        return cut[:limit]
    keep = limit - len(cut)
    out = ""
    used = 0
    for char in strip_ansi(text):
        size = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if used + size > keep:
            break
        out += char
        used += size
    return out + cut


def fit(text: str, limit: int, *, align: str = "left", theme: Theme | None = None) -> str:
    """Pad or truncate ``text`` to exactly ``limit`` visible columns."""
    current = _theme(theme)
    text = truncate(text, limit, theme=current)
    padding = " " * max(0, limit - width(text))
    if align == "right":
        return padding + text
    if align == "center":
        left = len(padding) // 2
        return " " * left + text + " " * (len(padding) - left)
    return text + padding


def wrap(text: str, columns: int | None = None, *, cont: int = 0,
         theme: Theme | None = None) -> str:
    """Wrap ``text`` to at most ``columns`` visible columns (default: the terminal).

    A real line break, not an ellipsis — a long *reason* must stay readable at 80
    columns, so prose wraps while tables and bars truncate. ``cont`` indents every
    continuation line, which is how a wrapped :func:`status_line` hangs under its mark.
    Words are never broken in the middle.
    """
    limit = terminal_width() if columns is None else max(1, int(columns))
    pad = " " * max(0, int(cont))
    out: list[str] = []
    for line in _fold(text, _theme(theme)).splitlines() or [""]:
        wrapped = textwrap.wrap(
            line.expandtabs(), width=limit, break_long_words=False,
            break_on_hyphens=False, drop_whitespace=True,
        ) or [""]
        out.extend(wrapped)
    return ("\n" + pad).join(out)


def rule(columns: int | None = None, *, label: str = "", weight: str = HEAVY,
         theme: Theme | None = None) -> str:
    """A horizontal rule — the CLI's separator.

    ``weight="heavy"`` (the default) draws ``──────`` and, with a label,
    ``─── COMMANDS ─────────────────────``: the label sits after a three-stroke lead-in,
    the rest of the requested width is stroke. ``weight="light"`` draws the dotted rule
    that closes a section (``········`` / ``.........``), never an edge.

    The result is **exactly** ``columns`` visible columns wide (default: the terminal) and
    never wraps: a label too long for the width is truncated with the theme's ellipsis.
    """
    current = _theme(theme)
    columns = terminal_width() if columns is None else max(1, int(columns))
    if weight == LIGHT:
        return current.style(current.glyph("dot") * columns, "rule")
    char = current.glyph("h")
    if not label or columns < BAR_LEAD + 4:
        # no room for a label (or none asked for): the plain rule is still exact
        return current.style(char * columns, "rule")
    lead = char * BAR_LEAD
    shown = truncate(label, columns - BAR_LEAD - 3, theme=current)
    tail = columns - BAR_LEAD - 2 - width(shown)      # >= 1 by construction
    return (
        f"{current.style(lead, 'rule')} {current.style(shown, 'heading')} "
        f"{current.style(char * tail, 'rule')}"
    )


def bar(label: str, *, columns: int | None = None, theme: Theme | None = None) -> str:
    """The **section separator**: ``─── COMMANDS ────────────────────``.

    One per multi-section output, above the section it opens. The label is upper-cased —
    a bar is the only ALL-CAPS text in the CLI (the logo caption aside) — and the rule is
    always exactly the screen width, so two bars in one output line up.
    """
    return rule(columns, label=str(label).strip().upper(), weight=HEAVY, theme=theme)


def end_rule(columns: int | None = None, *, theme: Theme | None = None) -> str:
    """The **light rule** that closes a section: ``···············`` / ``...........``.

    Used before a summary or footer line, so the line reads as the end of the section
    instead of as one more row of it. Not used as a substitute for ``spacer()``.
    """
    return rule(columns, weight=LIGHT, theme=theme)


def spacer(count: int = 1) -> str:
    """``count`` blank lines (no trailing newline of its own)."""
    return "\n" * max(0, int(count))


def legend(*, theme: Theme | None = None,
           statuses: Sequence[str] = LEGEND_STATUSES) -> str:
    """The glyph legend: ``✓ ok  ✗ fail  ■ blocked  · warn``.

    For the reference surfaces (``style preview`` / ``style palette``) and for a command
    that introduces a new mark — never on the normal output, where it would be noise.
    """
    current = _theme(theme)
    return "  ".join(f"{current.style(current.mark(status), status)} {status}"
                     for status in statuses)


def heading(text: str, *, level: int = 1, theme: Theme | None = None,
            columns: int | None = None) -> str:
    """A section heading: level 1 = title + full rule, level 2 = the labelled bar."""
    current = _theme(theme)
    if level <= 1:
        return f"{current.style(text, 'heading')}\n{rule(columns, theme=current)}"
    return bar(text, columns=columns, theme=current)


def status_line(status: str, text: str, *, theme: Theme | None = None, indent: int = 0,
                muted: bool = False, columns: int | None = None) -> str:
    """``"✓  text"`` — one line in the status vocabulary (ok/fail/blocked/warn/…).

    The mark is followed by **two spaces**; ``indent`` shifts the whole line (details sit
    at :data:`DETAIL_INDENT` under the line they belong to) and ``muted`` draws the text
    dim, which is the CLI's only depth cue beyond indentation. With ``columns`` a long
    text wraps and hangs two columns in, so a 200-character blocked reason still fits an
    80-column screen.
    """
    current = _theme(theme)
    pad = " " * max(0, int(indent))
    drawn = current.style(text, "muted") if muted else text
    if columns is not None:
        room = max(MIN_COLUMN, int(columns) - len(pad) - 3)
        drawn = wrap(drawn, room, cont=3, theme=current).replace("\n", f"\n{pad}")
    return f"{pad}{current.mark_line(status, drawn)}"


def step_line(index: int, total: int, text: str, *, status: str = "running",
              theme: Theme | None = None) -> str:
    """``"  2/5  ✓ build"`` — a step with a counter, a mark and its text."""
    current = _theme(theme)
    counter = f"{int(index)}/{int(total)}"
    return f"  {counter:<6} {current.mark_line(status, text)}"


def progress(fraction: float, *, columns: int = 24, label: str = "",
             status: str = "ok", theme: Theme | None = None) -> str:
    """``"[████████░░░░]"  62%`` — a monochrome bar (``#``/``-`` in plain mode)."""
    current = _theme(theme)
    columns = max(4, int(columns))
    try:
        value = float(fraction)
    except (TypeError, ValueError):
        value = 0.0
    value = 0.0 if value < 0 else (1.0 if value > 1 else value)
    filled = int(round(value * columns))
    bar = current.glyph("bar_full") * filled + current.glyph("bar_empty") * (columns - filled)
    percent = f"{int(round(value * 100)):>3}%"
    prefix = f"{label} " if label else ""
    return f"{prefix}{current.style('[' + bar + ']', status)} {percent}"


def bullets(items: Iterable[str], *, theme: Theme | None = None, indent: int = 2) -> str:
    """``"  • one"`` lines (``"  - one"`` when unicode is off)."""
    current = _theme(theme)
    pad = " " * max(0, int(indent))
    bullet = current.glyph("bullet")
    return "\n".join(f"{pad}{bullet} {item}" for item in items)


def box(lines: Iterable[str], *, title: str = "", theme: Theme | None = None,
        columns: int | None = None, pad: int = 1) -> str:
    """A monochrome box around ``lines`` (``┌─ title ─┐`` / ``+-- title --+``)."""
    current = _theme(theme)
    body = [str(line) for line in lines]
    widths = [width(line) for line in body] + ([width(title) + 4] if title else [])
    inner = max(1, (max(widths) if widths else 1) + 2 * pad)
    if columns is not None:
        # `columns` is the *total* width the caller wants: the box is exactly that
        # wide and long lines are truncated instead of silently overflowing.
        inner = max(3, int(columns) - 2)
    h = current.glyph("h")
    if title:
        label = f" {current.style(title, 'muted')} "
        top_body = h * 2 + label
        top = (current.glyph("tl") + top_body
               + h * max(0, inner - width(top_body)) + current.glyph("tr"))
    else:
        top = current.glyph("tl") + h * inner + current.glyph("tr")
    bottom = current.glyph("bl") + h * inner + current.glyph("br")
    mid = " " * pad
    rows = [current.glyph("v") + mid + fit(line, inner - 2 * pad, theme=current) + mid
            + current.glyph("v") for line in body]
    return "\n".join([current.style(top, "rule"), *rows, current.style(bottom, "rule")])


def key_values(pairs: Iterable[tuple[str, Any]], *, theme: Theme | None = None,
               title: str = "", gap: int = 2) -> str:
    """``key   value`` lines with the keys aligned in one column."""
    current = _theme(theme)
    items = [(str(key), str(value)) for key, value in pairs]
    if not items:
        return ""
    key_width = max(width(key) for key, _ in items)
    lines = [f"{current.style(fit(key, key_width, theme=current), 'key')}"
             f"{' ' * max(1, int(gap))}{value}" for key, value in items]
    if title:
        lines.insert(0, current.style(title, "heading"))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# diffs: the compact preview a change is decided on
# --------------------------------------------------------------------------- #

#: The diff gutter. The sign IS the information — the CLI is monochrome, so a changed
#: line is marked by `+`/`-` and never by a colour.
DIFF_ADD = "+"
DIFF_DEL = "-"
DIFF_CONTEXT = " "
DIFF_MARKER = "\\ No newline at end of file"

#: How many diff lines a compact preview draws before it says how many are left. A
#: preview is scanned, not read: the full record is one command away.
DIFF_PREVIEW_LINES = 24

#: The per-file header prefix of a unified diff (``--- a/x`` / ``+++ b/x``): the file is
#: already named by the block's own header line, so these two rows are dropped.
_DIFF_FILE_HEADERS = ("--- ", "+++ ")


def diff_stat(text: str) -> tuple[int, int]:
    """``(added, removed)`` counted from the diff's own lines — no second source.

    Header rows (``+++ b/x`` / ``--- a/x``) are not changes and are not counted. The
    numbers come from the text that will be applied, so a stat line can never disagree
    with the preview under it.
    """
    added = removed = 0
    for line in str(text or "").splitlines():
        if line.startswith(_DIFF_FILE_HEADERS) or line.startswith("@@ "):
            continue
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def diff_lines(text: str) -> list[str]:
    """A unified diff's body: the hunk lines, without the per-file header rows.

    ``@@`` hunk headers are kept (they are what makes a partial preview honest about
    *where* in the file the change lands); ``---``/``+++`` rows are dropped.
    """
    out: list[str] = []
    for line in str(text or "").splitlines():
        if any(line.startswith(prefix) for prefix in _DIFF_FILE_HEADERS):
            continue
        out.append(line)
    return out


def diff_preview(
    files: Iterable[Any],
    *,
    theme: Theme | None = None,
    columns: int | None = None,
    limit: int = DIFF_PREVIEW_LINES,
    indent: int = 2,
) -> tuple[str, int]:
    """A compact, monochrome diff preview of an edit record's files → ``(text, hidden)``.

    One block per file: ``M path  +12 -4`` then the hunk lines, marked by their gutter
    sign, clipped to ``columns`` visible columns and capped at ``limit`` lines **in
    total**. ``hidden`` is how many change lines did not fit — the caller prints the
    ``… n more`` line from it, so a preview can never pretend to be the whole diff.

    ``files`` is the edit record's file list (dicts with ``path``/``diff``/``ok``); a
    refused file shows its reason instead of a diff, because that is what the record
    says happened.
    """
    current = _theme(theme)
    width = terminal_width() if columns is None else max(MIN_WIDTH, int(columns))
    pad = " " * max(0, int(indent))
    out: list[str] = []
    drawn = 0
    hidden = 0
    for item in files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "?")
        text = str(item.get("diff") or "")
        added, removed = diff_stat(text)
        stat = f"+{added} -{removed}"
        status = "M"
        head = f"{status} {path}  {stat}"
        out.append(
            f"{pad}{current.style(truncate(head, width - len(pad), theme=current), 'strong')}"
        )
        if not item.get("ok", True):
            message = str(item.get("message") or item.get("reason") or "refused")
            wrapped = wrap(message, max(MIN_COLUMN, width - len(pad) - 4), theme=current)
            for chunk in wrapped.splitlines():
                out.append(
                    f"{pad}  "
                    f"{current.style(truncate(chunk, width - len(pad) - 2, theme=current), 'fail')}"
                )
            continue
        for line in diff_lines(text):
            if line.startswith("@@ "):
                if drawn >= limit:
                    hidden += 1
                    continue
            elif line.startswith(DIFF_ADD) or line.startswith(DIFF_DEL):
                if drawn >= limit:
                    hidden += 1
                    continue
                drawn += 1
            text_line = f"{pad}  {line}"
            out.append(truncate(text_line, width, theme=current))
    return "\n".join(out), hidden


def table(headers: Sequence[Any], rows: Iterable[Sequence[Any]], *,
          theme: Theme | None = None, max_width: int | None = None,
          aligns: Sequence[str] | None = None, title: str = "") -> str:
    """A monochrome table: padded columns, a header rule, no silent wrapping.

    Columns are shrunk (the widest first) until the table fits ``max_width``
    (default: the terminal). A cell whose text is exactly a status or token name
    is drawn in that token, so ``ok``/``blocked`` cells read at a glance.
    """
    current = _theme(theme)
    head = [str(cell) for cell in headers]
    body = [[str(cell) for cell in row] for row in rows]
    columns = len(head)
    if columns == 0:
        return ""
    for row in body:
        if len(row) > columns:
            columns = len(row)
    head += [""] * (columns - len(head))
    body = [row + [""] * (columns - len(row)) for row in body]

    widths = []
    for index in range(columns):
        candidates = [width(head[index])] + [width(row[index]) for row in body]
        widths.append(max(MIN_COLUMN, max(candidates) if candidates else MIN_COLUMN))

    limit = terminal_width() if max_width is None else max(MIN_WIDTH, int(max_width))
    while sum(widths) + GAP * (columns - 1) > limit:
        widest = max(range(columns), key=lambda i: widths[i])
        if widths[widest] <= MIN_COLUMN:
            break
        widths[widest] -= 1

    align_list = list(aligns or ["left"] * columns)
    align_list += ["left"] * (columns - len(align_list))

    def render_row(cells: list[str], *, is_head: bool) -> str:
        drawn = []
        for index, cell in enumerate(cells):
            token = cell if (cell in theme_mod.TOKENS or cell in theme_mod.MARKS) else None
            shown = current.style(cell, token) if token else cell
            if width(cell) > widths[index]:
                shown = truncate(cell, widths[index], theme=current)
            drawn.append(fit(shown, widths[index], align=align_list[index], theme=current))
        separator = f" {current.style(current.glyph('v'), 'rule')} "
        line = separator.join(drawn)
        return current.style(line, "strong") if is_head else line

    out = [render_row(head, is_head=True)]
    h = current.glyph("h")
    cross = current.glyph("cross")
    separator = h * widths[0] + "".join(h + cross + h + h * w for w in widths[1:])
    out.append(current.style(separator, "rule"))
    out.extend(render_row(row, is_head=False) for row in body)
    if title:
        out.insert(0, current.style(title, "heading"))
    return "\n".join(out)


def live_line(text: str, *, previous: str = "", columns: int | None = None,
              theme: Theme | None = None, final: bool = False) -> str:
    """The smallest possible write that puts ``text`` on the *current* line.

    This is the incremental path: one line is rewritten in place, the screen is never
    redrawn. On a rich stream that is ``\\r`` + the new text padded to the full width, so a
    shorter value cannot leave the tail of the previous one behind; on a plain stream
    in-place rewriting does not exist, so a changed line is *appended* with its own newline
    (the honest degradation — a log cannot un-write a line).

    **An unchanged line returns ``""``**: a tick that carries no new fact must cost no
    write at all. That is what makes "an indicator that ticks from real data" measurable
    rather than decorative — ``streaming.Activity`` counts the suppressed ticks.
    """
    current = _theme(theme)
    if text == previous and not final:
        return ""
    if current.plain:
        return text + "\n"
    room = terminal_width() if columns is None else max(1, int(columns))
    body = fit(text, room, theme=current)
    return f"\r{body}" + ("\n" if final else "")


def activity_line(label: str, facts: Mapping[str, Any], *, frame: str = "",
                  stalled: bool = False, theme: Theme | None = None,
                  columns: int | None = None, indent: int = 0) -> str:
    """One activity line from **real** measurements: ``⣽ working · 1 240 chunks · 38 renders``.

    ``facts`` is an ordered mapping of what the caller actually measured; an empty or
    ``None`` value is skipped rather than printed as a placeholder. ``stalled`` replaces the
    label with ``stalled`` and drops the frame — an indicator that keeps spinning while
    nothing arrives is a lie, and this is the one place that decides it.
    """
    current = _theme(theme)
    pad = " " * max(0, int(indent))
    mark = frame or current.mark("blocked" if stalled else "running")
    head = str(label)
    if stalled:
        head = f"{label} (stalled)"
    bits = [f"{mark} {head}"]
    for key, value in facts.items():
        if value is None or value == "" or value == 0:
            continue
        if isinstance(value, float):
            shown = f"{value:.1f}"
        elif isinstance(value, int) and abs(value) >= 10_000:
            shown = f"{value:,}".replace(",", " ")
        else:
            shown = str(value)
        bits.append(f"{shown} {key}")
    # One separator, spaces on both sides, in **both** modes: the ASCII spelling of the
    # separator must not read as a suffix of the value it follows ("1240 chunks- 38
    # renders" was what an un-spaced join produced on a plain stream).
    joiner = f" {current.glyph('sep')} "
    joined = joiner.join(bit.strip() for bit in bits).strip()
    text = f"{pad}{current.style(joined, 'muted')}"
    if stalled:
        text = f"{pad}{mark} {current.style(head, 'blocked')} " + joiner.join(
            b.strip() for b in bits[1:]
        )
    return text if columns is None else wrap(text, columns, cont=len(pad) + 2, theme=current)


def batch_line(done: int, total: int, names: Iterable[str], *, mode: str = "parallel",
               theme: Theme | None = None, columns: int | None = None) -> str:
    """One line for a group of concurrently running tools: ``⌁ 2/3 parallel · read, search``.

    Cline's transcript collapses a wave of parallel calls into one updating line instead of
    one line per call; the same shape here, with the counter taken from what really
    finished. Never pluralises a guess: the counts are the caller's measured values.
    """
    current = _theme(theme)
    listed = ", ".join(str(name) for name in names)
    head = f"{current.mark('running')} {int(done)}/{int(total)} {mode}"
    text = f"{head} {current.glyph('sep')} {listed}" if listed else head
    if columns is not None:
        return wrap(text, columns, cont=2, theme=current)
    return text


__all__ = [
    "BAR_LEAD",
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "DETAIL_INDENT",
    "DIFF_ADD",
    "DIFF_CONTEXT",
    "DIFF_DEL",
    "DIFF_MARKER",
    "DIFF_PREVIEW_LINES",
    "GAP",
    "HEAVY",
    "LEGEND_STATUSES",
    "LIGHT",
    "ascii_fold",
    "activity_line",
    "bar",
    "batch_line",
    "box",
    "bullets",
    "diff_lines",
    "diff_preview",
    "diff_stat",
    "end_rule",
    "fit",
    "heading",
    "key_values",
    "legend",
    "live_line",
    "progress",
    "rule",
    "spacer",
    "status_line",
    "step_line",
    "strip_ansi",
    "table",
    "terminal_height",
    "terminal_width",
    "truncate",
    "width",
    "wrap",
]
