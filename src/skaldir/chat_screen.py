"""The chat screen: the logo, the status band, the state art and the composed frame.

:mod:`skaldir.chatbar` owns the *frame* — two full-width bars around the typing line, the
palette, the keys, the erase/repaint arithmetic. This module owns what sits **around** that
frame, so the two can be read and tested apart:

::

      <logo mark, 2-4 rows>              <- once, at the top; scrolls away, never pinned
      model gpt-5-codex · ctx 34% · 132 tok/s · $0.0042 · 02:14 · goal: none
      <live row: the art of what is happening right now>
      ────────────────────────────────────────────────────────────
      ❯ type your message, / for commands
      ────────────────────────────────────────────────────────────
      /help · Tab completes · Esc dismisses · Ctrl-C cancels · /resume

Four rules hold this together, and each one has a test:

* **the band names its source for every number.** ``ctx`` is only ever a percentage the run
  itself printed; ``tok/s`` is a measurement when the run reported tokens and an estimate
  (``~``) when it did not; ``$`` comes from a **declared** price and is ``$ n/a`` — never a
  guess — when nobody declared one; ``MM:SS`` is the turn clock, which is always measured.
* **the price is declared, not assumed.** :data:`DECLARED_PRICES` ships **empty** on purpose:
  a price table that guesses is worse than a table that says ``n/a``. An operator declares
  prices either per model in ``prices.json`` in the runtime home or globally through
  ``SKALDIR_TELEMETRY_PRICE_PER_1K`` (the declaration :mod:`skaldir.telemetry` already reads).
* **every line is exactly ``width`` cells**, on a terminal and in plain mode alike. That is
  what lets the frame's erase arithmetic be a line count.
* **plain mode is a real path.** ``unicode=False`` folds the art to ASCII — a different
  *picture* is never drawn, the same picture is drawn with ASCII — and no function here emits
  a single escape byte.

The state art (:func:`state_art`, :func:`activity_row`, :func:`tool_badge`) is the owner's
"ASCII art for tool calls, web search, thinking, working": a three-row badge for the
transcript and a one-line live form for the row inside the frame. A finished tool call is
history and scrolls; a running one is state and updates in place.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import chatbar as _bar
from . import env as _env
from .chatbar import MARGIN, truncate_plain

__all__ = [
    "BADGE_MAX_WIDTH",
    "BAND_DROP_ORDER",
    "DECLARED_PRICES",
    "ENV_CONTEXT_BUDGET",
    "ENV_PRICE_PER_1K",
    "HINT_KEYS",
    "HINT_KEYS_PLAIN",
    "LOGO_INK_LEVEL",
    "LOGO_ROWS",
    "PRICES_FILENAME",
    "Reading",
    "SCREEN_CORE_LINES",
    "SPINNER_PLAIN",
    "SPINNER_RICH",
    "STATE_ART_ROWS",
    "STATE_IDS",
    "STATE_TABLE",
    "Screen",
    "activity_row",
    "band_parts",
    "compact_logo",
    "context_reading",
    "cost_text",
    "declared_price_path",
    "hint_row",
    "live_rows",
    "logo_block",
    "mark_size",
    "price_for",
    "price_table",
    "reading_from_text",
    "reading_of_line",
    "screen_lines",
    "spinner",
    "state_art",
    "state_glyph",
    "state_name",
    "status_band",
    "tool_badge",
]

# --------------------------------------------------------------------------- #
# the geometry                                                                #
# --------------------------------------------------------------------------- #

#: The lines a screen-mode frame always owns: band, top bar, input, bottom bar, hint.
SCREEN_CORE_LINES = 5
#: The compact logo's height. Four rows of half-block cells carry the mark's eight pixel
#: rows, which is the shortest rendition that still reads as the emblem and not as a blob.
LOGO_ROWS = 4
#: A state badge in the transcript: a top rule, one fact line, a bottom rule.
STATE_ART_ROWS = 3
#: The widest a badge grows before it is clipped (a badge is a stamp, not a paragraph).
BADGE_MAX_WIDTH = 72

#: The discoverability line under the frame. Every key here is one the shell really answers.
HINT_KEYS = (
    "/help · Tab completes · Esc dismisses · Ctrl-C cancels · /resume"
)
HINT_KEYS_PLAIN = (
    "/help - Tab completes - Esc dismisses - Ctrl-C cancels - /resume"
)

#: Parts the band drops when the terminal is too narrow, first to go first. The three the
#: standard names (context, speed, duration) are never dropped; they are clipped instead.
BAND_DROP_ORDER: tuple[str, ...] = ("goal", "model", "price")

#: Where an operator declares per-model prices (in the runtime home).
PRICES_FILENAME = "prices.json"

#: The declared price table. **Empty on purpose** — see :func:`price_for`. An operator adds
#: entries here in a site patch, or (better) declares them in ``prices.json`` so the numbers
#: live in configuration and not in the shipped source.
DECLARED_PRICES: dict[str, float] = {}


def _fold(text: str, *, unicode: bool) -> str:
    """Plain mode is ASCII all the way down — the screen's own text included."""
    if unicode:
        return str(text)
    return _bar._fold_ascii(text)


# --------------------------------------------------------------------------- #
# the logo: the compact mark, derived from the checked-in art                  #
# --------------------------------------------------------------------------- #

#: Level at or above which a sampled pixel counts as ink. The mark's grid carries the emblem
#: at 14-15 and its antialiased edge at 8-13; 7 keeps the silhouette and drops the fringe.
LOGO_INK_LEVEL = 7

_UNICODE_HALF = (" ", "▄", "▀", "█")
_ASCII_HALF = (" ", "_", "-", "#")


@lru_cache(maxsize=4)
def mark_size() -> tuple[int, int]:
    """``(columns, rows)`` of the checked-in mark, or ``(0, 0)`` when the art is missing.

    The mark's native size is what a *legible* compaction has to respect: the emblem is a
    small grid, and asking for more columns than it has does not add detail — it smears one
    source column across several cells and the picture stops reading as the mark. So the
    compact logo never upscales; it only ever samples the art that is really there.
    """
    try:
        from . import logo as _logo

        grid = _logo.load_art().grid("mark")
        return int(grid.columns), int(grid.rows)
    except Exception:  # noqa: BLE001 — a build without the art has no mark
        return 0, 0


@lru_cache(maxsize=8)
def _sample_mark(columns: int, pixel_rows: int) -> tuple[tuple[float, ...], ...]:
    """Box-filter the checked-in ``mark`` grid down to ``columns`` x ``pixel_rows``.

    The art file is the one source of the logo; nothing here re-reads the PNG and nothing
    here draws a *different* mark. A build without the art gets an empty tuple, and the
    screen simply has no logo — which is stated, not faked.
    """
    from . import logo as _logo

    try:
        grid = _logo.load_art().grid("mark")
    except Exception:  # noqa: BLE001 — a build without the art has no logo, never a fake one
        return ()
    columns = max(1, min(int(columns), int(grid.columns)))
    out: list[tuple[float, ...]] = []
    for row in range(pixel_rows):
        top = grid.rows * row // pixel_rows
        bottom = max(top + 1, grid.rows * (row + 1) // pixel_rows)
        cells: list[float] = []
        for column in range(columns):
            left = grid.columns * column // columns
            right = max(left + 1, grid.columns * (column + 1) // columns)
            total = 0
            count = 0
            for y in range(top, bottom):
                for x in range(left, right):
                    total += grid.level(x, y)
                    count += 1
            cells.append(total / count if count else 0.0)
        out.append(tuple(cells))
    return tuple(out)


@lru_cache(maxsize=8)
def compact_logo(*, unicode: bool = True, rows: int = LOGO_ROWS,
                 columns: int = 0) -> tuple[str, ...]:
    """The compact mark: ``rows`` lines of half-block cells, or ``()`` without art.

    One cell carries two pixel rows (``▀``/``▄``/``█``), so four text rows hold the emblem's
    eight pixel rows at the aspect a terminal cell really has. The ASCII rendering uses the
    same threshold and the same box filter — only the four glyphs differ, so the two pictures
    are the same shape.

    ``columns`` is a *request*, and the mark's own width caps it: upscaling a 16-column emblem
    to 24 cells duplicates source columns and the shape stops reading. Zero means "the art's
    own width". A build without the art returns ``()`` rather than a drawn stand-in.
    """
    rows = max(2, int(rows))
    pixel_rows = rows * 2
    native_columns, native_rows = mark_size()
    if not native_columns or not native_rows:
        return ()
    requested = int(columns) or native_columns
    columns = max(4, min(requested, native_columns))
    grid = _sample_mark(columns, pixel_rows)
    if not grid:
        return ()
    glyphs = _UNICODE_HALF if unicode else _ASCII_HALF
    lines: list[str] = []
    for index in range(0, pixel_rows, 2):
        top = grid[index]
        bottom = grid[index + 1] if index + 1 < len(grid) else (0.0,) * len(top)
        body = ""
        for upper, lower in zip(top, bottom, strict=True):
            bit = (2 if upper >= LOGO_INK_LEVEL else 0) | (1 if lower >= LOGO_INK_LEVEL else 0)
            body += glyphs[bit]
        lines.append(body.rstrip())
    while lines and not lines[-1]:
        lines.pop()
    return tuple(lines)


def logo_block(*, unicode: bool = True, rows: int = LOGO_ROWS,
               columns: int = 0) -> tuple[str, ...]:
    """The logo as the screen writes it: the mark, inset by the frame's own margin."""
    return tuple(" " * MARGIN + line for line in compact_logo(unicode=unicode, rows=rows,
                                                              columns=columns) if line)


# --------------------------------------------------------------------------- #
# the band's numbers, from the run's own report                                #
# --------------------------------------------------------------------------- #

#: ``ctx 34%`` / ``~ctx 34%`` / ``ctx <1%`` — the reading
#: :func:`skaldir.telemetry.footer_line` prints. The tilde goes **before** the label (it is
#: ``format_percent``'s mark, prefixed to the rendered value), and ``<1%`` is what
#: ``format_percent`` prints for a small non-zero reading.
#:
#: The guard is a negative look-behind rather than ``\b``: a word boundary sits *after* the
#: ``~`` (space→``~`` is not a boundary, ``~``→``c`` is), so ``\b(~?)ctx`` would match at the
#: ``c`` and quietly drop the mark that says the number is an estimate.
_FOOTER_CTX_RE = re.compile(r"(?<![0-9A-Za-z_])(~?)ctx\s+(<)?(\d{1,3}(?:\.\d)?)\s*%",
                            re.IGNORECASE)
#: ``132 tok/s`` / ``~1.2k tok/s`` — the same footer's speed, with the same tilde rule.
_FOOTER_SPEED_RE = re.compile(r"(?<![0-9A-Za-z_])(~?)([\d.]+)(k?)\s*tok(?:ens)?/s\b",
                              re.IGNORECASE)


@dataclass(frozen=True)
class Reading:
    """A number a run reported **about itself**, with the mark that says how it got it.

    Nothing here is computed locally: an empty string is the honest value for a fact no run
    reported, and it renders as ``n/a`` rather than as a zero.
    """

    context: str = ""
    speed: str = ""
    source: str = ""

    @property
    def known(self) -> bool:
        return bool(self.context or self.speed)


#: A declared context budget in tokens, when nobody declared a per-model one.
ENV_CONTEXT_BUDGET = "CONTEXT_BUDGET"


def reading_from_text(text: str, *, source: str = "run footer") -> Reading:
    """Fold the run's own telemetry footer out of its output — never a local estimate.

    :func:`skaldir.telemetry.footer_line` prints ``model · ctx 34% · 1.2k tok/s · 02:14``,
    and that line travels through the frame's stream like any other chunk. This reads the
    numbers back out of it, keeping the ``~`` the footer itself put there: an estimate stays
    marked as one, and a reading nobody printed stays empty.

    **The last footer-shaped line wins**, where footer-shaped means "carries a context
    reading *and* a speed on one line" — that is the product's own summary line, and taking
    it is what stops a band from quoting one turn's 1-millisecond window (``22.0k tok/s``)
    when the run also printed the whole-run figure. When no such line exists the two numbers
    are taken independently, newest first.
    """
    body = str(text or "")
    context = ""
    speed = ""
    for line in reversed(body.splitlines()):
        pair = reading_of_line(line)
        if pair.context and pair.speed:
            return Reading(context=pair.context, speed=pair.speed, source=source)
    found = list(_FOOTER_CTX_RE.finditer(body))
    if found:
        last = found[-1]
        context = f"ctx {last.group(1)}{last.group(2) or ''}{last.group(3)}%"
    found = list(_FOOTER_SPEED_RE.finditer(body))
    if found:
        last = found[-1]
        scale = "k" if last.group(3) else ""
        speed = f"{last.group(1)}{last.group(2)}{scale} tok/s"
    return Reading(context=context, speed=speed, source=source if (context or speed) else "")


def reading_of_line(line: str) -> Reading:
    """The reading on **one** line — both numbers, or whichever of the two it carries."""
    body = str(line or "")
    context = ""
    speed = ""
    found = list(_FOOTER_CTX_RE.finditer(body))
    if found:
        last = found[-1]
        context = f"ctx {last.group(1)}{last.group(2) or ''}{last.group(3)}%"
    found = list(_FOOTER_SPEED_RE.finditer(body))
    if found:
        last = found[-1]
        speed = f"{last.group(1)}{last.group(2)}{'k' if last.group(3) else ''} tok/s"
    return Reading(context=context, speed=speed, source="run footer" if (context or speed) else "")


def context_reading(events: Sequence[dict[str, Any]] | None = None, *,
                    path: Path | str | None = None,
                    budget: int | None = None) -> Reading:
    """The context reading from the **real telemetry record**, or an empty :class:`Reading`.

    This is the same object ``skaldir telemetry show`` renders: the last *answered* turn's
    prompt tokens against the declared budget (:func:`skaldir.telemetry.resolve_budget`), with
    the record's own ``used_source`` deciding whether the number is marked ``~`` (an estimate)
    or not (a provider count). No budget, no events or no measurable request ⇒ no reading —
    and the band then says ``ctx n/a`` with that as the reason, which is a complete answer.

    ``path`` is the transcript whose events are folded; ``events`` short-circuits the read
    for a caller that already holds them (a test, or the shell's own record).
    """
    try:
        from . import telemetry as _telemetry
    except Exception:  # noqa: BLE001 — a build without telemetry has no record to read
        return Reading(source="telemetry is not available in this build")
    if events is None:
        if not path:
            return Reading(source="no transcript was given, so no context was measured")
        try:
            events, _problems = _telemetry.read_events(path)
        except Exception as exc:  # noqa: BLE001 — an unreadable transcript is stated
            return Reading(source=f"the transcript could not be read: {type(exc).__name__}")
    try:
        resolved, _source = _telemetry.resolve_budget(budget)
        record = _telemetry.build_record_from_events(list(events), context_budget=resolved)
    except Exception as exc:  # noqa: BLE001 — a broken record is reported, not hidden
        return Reading(source=f"the telemetry record could not be built: {type(exc).__name__}")
    block = dict(getattr(record, "context", None) or {})
    percent = block.get("percent")
    if percent is None:
        return Reading(source=str(block.get("reason") or "the run reported no context reading"))
    mark = "~" if str(block.get("used_source") or "") == "estimate" else ""
    speed_block = dict(getattr(record, "tokens_per_second", None) or {})
    speed = ""
    if speed_block.get("value") is not None:
        speed = f"{'~' if speed_block.get('source') == 'estimate' else ''}" \
                f"{_speed_text(float(speed_block['value']))} tok/s"
    return Reading(
        context=f"ctx {mark}{float(percent):.0f}%",
        speed=speed,
        source=f"telemetry:{block.get('used_source') or 'unknown'}",
    )


def _speed_text(value: float) -> str:
    """``703`` shown as ``703`` and ``1320`` as ``1.3k`` — the footer's own short form."""
    if value >= 1000:
        return f"{value / 1000.0:.1f}k"
    return f"{value:.0f}"


# --------------------------------------------------------------------------- #
# the price: declared, or honestly absent                                      #
# --------------------------------------------------------------------------- #

#: The env declaration :mod:`skaldir.telemetry` already reads (``SKALDIR_`` prefixed, the
#: legacy ``GODSV_`` accepted). Reused rather than re-invented — one declaration, one reader.
ENV_PRICE_PER_1K = "TELEMETRY_PRICE_PER_1K"


def declared_price_path() -> Path:
    """Where the per-model table lives: ``prices.json`` in the runtime home.

    The home is ``$SKALDIR_HOME`` (``$GODSV_HOME`` accepted), else the registry's default
    (``~/.hermes/skaldir``, falling back to ``~/.hermes/godsv``). The resolution is done the
    same way everywhere else in the product so a declaration is never looked for in a second
    place — and it is *read* through :mod:`skaldir.env`, the one module that owns both
    prefixes and the host-owned names (``tests/test_env_leak_guard.py`` scans every other
    module for a direct ``os.environ`` read of a runtime spelling).
    """
    explicit = str(_env.first_env(("SKALDIR_HOME", "GODSV_HOME")) or "")
    if explicit:
        return Path(explicit).expanduser() / PRICES_FILENAME
    try:
        from .registry import default_runtime_dir

        return Path(default_runtime_dir()) / PRICES_FILENAME
    except Exception:  # noqa: BLE001 — standalone use still gets a deterministic path
        hermes = _env.host_getenv("HERMES_HOME") or str(Path.home() / ".hermes")
        return Path(hermes) / "skaldir" / PRICES_FILENAME


def price_table(path: Path | str | None = None) -> dict[str, float]:
    """The declared per-model table, or ``{}`` — a corrupt file is empty, never fatal.

    Two shapes are accepted, so a hand-written file is not forced through a schema::

        {"deepseek-chat": 0.00055}                              # model -> USD / 1k tokens
        {"source": "...", "usd_per_1k_tokens": {"<model>": …}}   # the documented shape

    A value that is not a positive number is dropped: a table entry of ``0`` would render a
    confident ``$0.0000``, which is exactly the plausible-looking zero this module refuses.
    """
    target = Path(path) if path is not None else declared_price_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — no file, a bad file, no permission: no declaration
        return {}
    if not isinstance(raw, dict):
        return {}
    block = raw.get("usd_per_1k_tokens")
    source = block if isinstance(block, dict) else raw
    out: dict[str, float] = {}
    for model, value in source.items():
        if str(model).startswith("_") or str(model) == "source":
            continue
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if price > 0:
            out[str(model)] = price
    return out


def price_for(model: str, *, path: Path | str | None = None,
              table: dict[str, float] | None = None) -> tuple[float | None, str]:
    """``(usd_per_1k, source)`` for this model — ``(None, reason)`` when nobody declared one.

    Precedence, most specific first: an entry for **this model** in the declared table, then
    the built-in :data:`DECLARED_PRICES`, then the owner's global
    ``SKALDIR_TELEMETRY_PRICE_PER_1K``. Nothing else is consulted: a price for
    ``gpt-5-codex`` is never used for ``deepseek-chat`` and an unknown model gets ``None``.
    """
    name = str(model or "").strip()
    if not name:
        return None, "no model is known, so no price can be declared for it"
    declared = dict(DECLARED_PRICES)
    declared.update(table if table is not None else price_table(path))
    if name in declared:
        return declared[name], "table:prices.json"
    try:
        from . import telemetry as _telemetry

        value, source = _telemetry.resolve_price()
    except Exception:  # noqa: BLE001 — a build without telemetry has no env declaration
        value, source = None, ""
    if value:
        return float(value), f"env:{source or ENV_PRICE_PER_1K}"
    return None, f"no price is declared for {name}"


def cost_text(tokens: int | None, model: str, *, path: Path | str | None = None,
              estimated: bool = False) -> str:
    """``$0.0042`` when the tokens and a declared price are both known, else ``$ n/a``.

    ``$ n/a`` is a *complete* answer here, not a gap: an unpriced run has no cost, and a cost
    computed from an assumed price would be a fabricated number in the most literal sense.
    """
    if not tokens or tokens <= 0:
        return "$ n/a"
    price, _source = price_for(model, path=path)
    if price is None:
        return "$ n/a"
    usd = float(tokens) * float(price) / 1000.0
    mark = "~" if estimated else ""
    if usd >= 1.0:
        body = f"{usd:.2f}"
    elif usd >= 0.01:
        body = f"{usd:.3f}"
    else:
        body = f"{usd:.4f}"
    return f"{mark}${body}"


# --------------------------------------------------------------------------- #
# the band: model · ctx · tok/s · $ · time · goal                              #
# --------------------------------------------------------------------------- #


def band_parts(*, model: str = "", context: str = "", speed: str = "", price: str = "",
               clock: str = "", goal: str = "") -> list[tuple[str, str]]:
    """The band's parts, in the order the owner asked for, each with its key.

    The key is what :func:`status_band` drops the part *by* (see :data:`BAND_DROP_ORDER`), so
    a narrow terminal loses whole facts and never half of one.
    """
    return [
        ("model", f"model {model}".strip()),
        ("ctx", context),
        ("speed", speed),
        ("price", price),
        ("clock", clock),
        ("goal", goal),
    ]


def status_band(width: int, *, model: str = "", context: str = "", speed: str = "",
                price: str = "", clock: str = "", goal: str = "", unicode: bool = True,
                margin: int = MARGIN, dot: str | None = None) -> str:
    """The numbers line above the frame, exactly ``width`` cells.

    Parts the terminal cannot fit are dropped in :data:`BAND_DROP_ORDER` — ``goal`` first,
    then ``model``, then ``price`` — so the three the standard names (context, speed,
    duration) survive longest. A band that still does not fit is clipped with the mode's
    ellipsis; it is never allowed to wrap.
    """
    width = max(1, int(width))
    sep = dot if dot is not None else (_bar.DOT_RICH if unicode else _bar.DOT_PLAIN)
    kept = [
        (key, text)
        for key, text in band_parts(model=model, context=context, speed=speed, price=price,
                                    clock=clock, goal=goal)
        if text
    ]

    def render(items: Sequence[tuple[str, str]]) -> str:
        return f" {sep} ".join(_fold(text, unicode=unicode) for _key, text in items)

    # drop whole facts (goal, then model, then price) while the band is too wide; the three
    # the standard names — context, speed, duration — are only ever clipped, never dropped
    for drop in BAND_DROP_ORDER:
        if len(" " * max(0, margin) + render(kept)) <= width:
            break
        kept = [(key, text) for key, text in kept if key != drop]
    body = " " * max(0, margin) + render(kept)
    if len(body) > width:
        body = " " * max(0, margin) + truncate_plain(
            render(kept), max(1, width - max(0, margin)), unicode=unicode
        )
    return body[:width].ljust(width)


def hint_row(width: int, *, unicode: bool = True, margin: int = MARGIN,
             extra: str = "") -> str:
    """The discoverability line under the frame — the keys, and nothing else.

    ``extra`` lets a session add one context-dependent key (``/resume`` after a crash) without
    a second layout: the keys give way to it, never the other way round.
    """
    text = HINT_KEYS if unicode else HINT_KEYS_PLAIN
    text = f"{text} · {extra}" if extra and unicode else (f"{text} - {extra}" if extra else text)
    return _bar.hint_line(width, _fold(text, unicode=unicode), "", margin=margin,
                          unicode=unicode)


# --------------------------------------------------------------------------- #
# the state art: thinking, working, a tool call, a web search, a write …        #
# --------------------------------------------------------------------------- #

#: ``state -> (unicode glyph, ascii glyph, word)``. One table, so the live row and the badge
#: can never disagree about what a state is called.
STATE_TABLE: dict[str, tuple[str, str, str]] = {
    "idle": ("·", "-", "idle"),
    "thinking": ("⠋", "*", "thinking"),
    "working": ("⚙", "*", "working"),
    "tool": ("⚙", ">", "tool"),
    "search": ("⌕", "?", "web search"),
    "read": ("▤", "o", "read"),
    "write": ("✎", "+", "write"),
    "patch": ("✎", "+", "patch"),
    "done": ("✔", "v", "done"),
    "failed": ("✖", "x", "failed"),
}
STATE_IDS: tuple[str, ...] = tuple(STATE_TABLE)

#: The spinner the ``thinking``/``working`` rows advance through. Braille on a terminal (the
#: cells are one column wide, so the row never shifts), ``|/-\`` in plain mode.
SPINNER_RICH = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPINNER_PLAIN = "|/-\\"

_ART_RULES = {
    True: ("╭", "─", "╰", "│", "·"),
    False: (",", "-", "`", "|", "-"),
}


def spinner(tick: int = 0, *, unicode: bool = True) -> str:
    """One frame of the spinner (``tick`` is the caller's clock — nothing is stored here)."""
    frames = SPINNER_RICH if unicode else SPINNER_PLAIN
    return frames[int(tick) % len(frames)]


def state_name(state: str) -> str:
    """The word for a state, from the one table (an unknown state names itself)."""
    key = str(state or "idle").strip().lower()
    entry = STATE_TABLE.get(key)
    return entry[2] if entry else (key or "idle")


def state_glyph(state: str, *, unicode: bool = True, tick: int = 0) -> str:
    """The mark for a state: the spinner where one is running, a fixed glyph otherwise."""
    key = str(state or "idle").strip().lower()
    entry = STATE_TABLE.get(key)
    if entry is None:
        return "?" if not unicode else "·"
    if not unicode:
        return entry[1]
    if key in ("thinking", "working"):
        return spinner(tick, unicode=True)
    return entry[0]


def activity_row(width: int, state: str = "idle", *, detail: str = "", tick: int = 0,
                 unicode: bool = True, margin: int = MARGIN, elapsed: float | None = None) -> str:
    """The **live row inside the frame**: ``⠋ thinking — reasoning over the context · 1.2s``.

    One line, rewritten in place by every repaint. This is why a long tool call costs one row
    of screen instead of a new row per tick: the row is part of the frame, and the frame is
    erased and drawn again, never appended to.
    """
    key = str(state or "idle").strip().lower()
    word = state_name(key)
    glyph = state_glyph(key, unicode=unicode, tick=tick)
    pieces = [f"{glyph} {word}".strip()]
    if detail:
        pieces.append(str(detail))
    if elapsed is not None and elapsed >= 0.05:
        pieces.append(f"{float(elapsed):.1f}s")
    join = " — " if unicode else " - "
    inner = join.join(pieces) if len(pieces) > 1 else pieces[0]
    head = " " * max(0, margin)
    room = max(1, int(width) - max(0, margin))
    return (head + truncate_plain(inner, room, unicode=unicode))[: int(width)].ljust(
        max(0, int(width))
    )


def state_art(state: str, *, detail: str = "", width: int | None = None, tick: int = 0,
              unicode: bool = True, margin: int = MARGIN) -> tuple[str, ...]:
    """A three-row badge for the transcript: a titled rule, one fact line, a closing rule.

    ::

        ╭─ thinking ───────────────────────────────
        │ ⠋ reasoning over the context · 1.2s
        ╰──────────────────────────────────────────

    The badge is a *stamp*: it is written once, above the frame, and then scrolls with the
    history. The running state lives on the live row (:func:`activity_row`); the two are
    deliberately different so a screen does not carry two moving displays at once.
    """
    top_glyph, rule, bottom_glyph, side, plain_dash = _ART_RULES[bool(unicode)]
    key = str(state or "idle").strip().lower()
    word = state_name(key)
    glyph = state_glyph(key, unicode=unicode, tick=tick)
    span = min(BADGE_MAX_WIDTH, max(24, int(width) if width else BADGE_MAX_WIDTH))
    inner = max(1, span - max(0, margin))
    title = f"{top_glyph}{rule} {word} "
    head = " " * max(0, margin)
    top = (head + title)[: span]
    top = top + rule * max(0, span - len(top))
    fact = f"{glyph} {detail}".strip() if detail else glyph
    body = head + side + " " + truncate_plain(fact, max(1, inner - 2), unicode=unicode)
    body = body[:span].ljust(span)
    foot = (head + bottom_glyph + rule * max(0, inner - 1))[:span]
    foot = foot.ljust(span)
    return (top, body, foot)


def tool_badge(name: str, *, detail: str = "", status: str = "ok", exit_code: int | None = None,
               duration_ms: int | None = None, streamed: int | None = None,
               width: int | None = None, unicode: bool = True,
               margin: int = MARGIN) -> tuple[str, ...]:
    """The transcript badge for **one finished tool call**.

    ::

        ╭─ tool terminal ──────────────────────────
        │ ⚙ ls -la · exit 0 · 128 B · 0.42 s
        ╰──────────────────────────────────────────

    Every number is one the caller measured; a number the caller did not have is simply not
    printed. The badge never invents a duration, a size or an exit code — an absent fact is
    an absent word.
    """
    tool = str(name or "tool").strip() or "tool"
    state = "failed" if (status == "failed" or (exit_code is not None and exit_code != 0)) \
        else "done"
    facts: list[str] = []
    if detail:
        facts.append(str(detail))
    if exit_code is not None:
        facts.append(f"exit {int(exit_code)}")
    if streamed is not None:
        facts.append(f"{int(streamed)} B")
    if duration_ms is not None:
        facts.append(f"{int(duration_ms) / 1000.0:.2f} s")
    top_glyph, rule, bottom_glyph, side, _dash = _ART_RULES[bool(unicode)]
    span = min(BADGE_MAX_WIDTH, max(24, int(width) if width else BADGE_MAX_WIDTH))
    inner = max(1, span - max(0, margin))
    head = " " * max(0, margin)
    title = f"{top_glyph}{rule} {state_name(state)} {tool} "
    top = (head + title)[: span]
    top = top + rule * max(0, span - len(top))
    sep = " · " if unicode else " - "
    glyph = state_glyph(state, unicode=unicode)
    body = head + side + " " + truncate_plain(
        f"{glyph} {sep.join(facts)}".strip(), max(1, inner - 2), unicode=unicode
    )
    body = body[:span].ljust(span)
    foot = (head + bottom_glyph + rule * max(0, inner - 1))[:span].ljust(span)
    return (top, body, foot)


@dataclass(frozen=True)
class Screen:
    """The screen's instructions: the extra rows, the band, the live row and the hint.

    A value object, so a caller (and a test) can compare *what the screen should be* without
    a terminal. :func:`screen_lines` turns it into lines; nothing here draws.
    """

    logo: tuple[str, ...] = ()
    band: str = ""
    live: str = ""
    hint: str = ""
    palette: tuple[str, ...] = ()


def live_rows(state: str = "idle", *, detail: str = "", tick: int = 0, unicode: bool = True,
              elapsed: float | None = None) -> tuple[str, ...]:
    """The transcript rows for a state: its badge, with an optional measured duration.

    The live row inside the frame is drawn by :func:`activity_row`; this helper exists so a
    caller that only holds a state name can render the transcript side without knowing the
    table. ``elapsed`` is appended to the badge's detail only when the caller measured it.
    """
    text = detail
    if elapsed is not None and elapsed >= 0.05:
        text = f"{detail} · {float(elapsed):.1f}s" if detail else f"{float(elapsed):.1f}s"
    return state_art(state, detail=text, tick=tick, unicode=unicode)


# --------------------------------------------------------------------------- #
# the screen as lines                                                          #
# --------------------------------------------------------------------------- #


def screen_lines(width: int, *, logo: Sequence[str] = (), band: str = "", live: str = "",
                 rows: Sequence[str] = (), text: str = "", hint: str = "",
                 hint_left: str = "", hint_right: str = "", unicode: bool = True,
                 margin: int = MARGIN) -> tuple[list[str], int]:
    """The whole screen as lines, plus the index of the input line inside them.

    Top to bottom: the logo block, the band, the live row, the palette rows, then the
    two-bar frame (bar / input / bar) and the hint row. Every line is exactly ``width``
    cells, so the caller can put them on a terminal without checking anything.

    ``logo`` is passed in rather than derived here so the caller decides when the logo is
    owed (:meth:`chatbar.ChatBar.paint_logo` draws it once, and again after ``/clear``) —
    the composition itself stays a pure function of what it is handed.
    """
    def fit(line: str) -> str:
        body = _fold(str(line), unicode=unicode)
        return body[: int(width)].ljust(max(0, int(width)))

    lines: list[str] = [fit(row) for row in logo]
    if band:
        lines.append(fit(band))
    if live:
        lines.append(fit(live))
    lines.extend(fit(row) for row in rows)
    cursor_index = len(lines) + 1  # the input line: after the rows, after the top bar
    frame_body, _cursor = _bar.frame_lines(
        width,
        text=text,
        rows=(),
        hint_left=hint_left if not hint else "",
        hint_right=hint_right if not hint else "",
        unicode=unicode,
        margin=margin,
    )
    if hint:
        # the discoverability row replaces the fact row: one row, one job
        frame_body[3] = fit(hint)
    lines.extend(frame_body)
    return lines, cursor_index
