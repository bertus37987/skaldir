"""Skaldir CLI theme — black, white and a grey ramp. Never a rainbow.

The product's visual language is monochrome: one ink, one paper, a ramp of greys
between them, plus typographic weight (bold/dim/reverse) for meaning. Colour is a
*choice the terminal makes*, not a property of the message — so every helper in
:mod:`skaldir.render`, :mod:`skaldir.logo` and every command that prints them asks this
module first.

Two axes, decided by :func:`detect`:

``mode``
    ``"rich"``  — ANSI grey ramp + typographic weight, unicode glyphs (✓ ✗ ■ ·, ─ │ ┌ ┐).
    ``"plain"`` — byte-stable ASCII: no escape sequences at all, ASCII glyphs
    (``+`` ``x`` ``#`` ``.``, ``-`` ``|`` ``+``). This is what a pipe, a log file,
    a pager, a diff or a ``NO_COLOR`` / ``--no-color`` run gets — plain output is
    also ASCII on purpose, so a redirect never depends on the reader's font.

``depth``
    ``"truecolor"`` (``38;2;l;l;l``) · ``"256"`` (``38;5;N``) · ``"basic"`` (weight only)
    · ``"none"`` (plain mode). The grey ramp is the only colour the CLI ever emits:
    ``Theme.grey(level)`` renders a level 0-255 as an SGR foreground.

``size``
    ``width``/``height`` in columns and rows (0 = "ask the console"). They decide how
    wide a bar is and whether the startup art gets its full band or the compact mark —
    the *layout* honours them, the *content* never changes with them per character, so a
    pipe with the same declared size produces the same bytes as a terminal.

Precedence, highest first: an explicit ``mode``/``depth``/``glyphs`` argument (tests,
``SKALDIR_*`` opt-ins) → ``--no-color`` → ``NO_COLOR`` → ``SKALDIR_COLOR``/``FORCE_COLOR``
→ ``stdout.isatty()`` → ``basic`` when the terminfo says nothing better.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any, TextIO

from .env import expand, getenv

MODE_RICH = "rich"
MODE_PLAIN = "plain"
MODES = (MODE_RICH, MODE_PLAIN)

DEPTH_TRUECOLOR = "truecolor"
DEPTH_256 = "256"
DEPTH_BASIC = "basic"
DEPTH_NONE = "none"
DEPTHS = (DEPTH_TRUECOLOR, DEPTH_256, DEPTH_BASIC, DEPTH_NONE)

GLYPHS_UNICODE = "unicode"
GLYPHS_ASCII = "ascii"

ESC = "\x1b["
RESET = "\x1b[0m"

#: The grey ramp. Names are for humans; the number is a 0-255 intensity, rendered as
#: a grey foreground in ``truecolor``/``256`` and as typographic weight in ``basic``.
GREYS: dict[str, int] = {
    "paper": 255,
    "bright": 235,
    "silver": 205,
    "stone": 168,
    "slate": 128,
    "ash": 96,
    "smoke": 66,
    "coal": 38,
    "void": 0,
}

#: Tokens → the grey they are drawn in (``None`` = the terminal's own foreground).
TOKEN_GREY: dict[str, str | None] = {
    "default": None,
    "value": None,
    "strong": None,
    "heading": "paper",
    "accent": "paper",
    "ok": "paper",
    "fail": None,
    "blocked": "smoke",
    "warn": "silver",
    "muted": None,
    "key": "silver",
    "rule": "smoke",
    "art": "paper",
    "art_shadow": "smoke",
}

#: Tokens → typographic weight. This is what survives ``depth="basic"``.
TOKEN_ATTRS: dict[str, tuple[str, ...]] = {
    "default": (),
    "value": (),
    "strong": ("1",),
    "heading": ("1",),
    "accent": ("1",),
    "ok": ("1",),
    "fail": ("7",),  # reverse video: the one monochrome way to shout
    "blocked": ("2",),
    "warn": (),
    "muted": ("2",),
    "key": ("2",),
    "rule": ("2",),
    "art": (),
    "art_shadow": ("2",),
}

TOKENS = tuple(TOKEN_GREY)

#: Status vocabulary. ``unicode`` is the product's set, ``ascii`` the plain fallback.
MARKS: dict[str, dict[str, str]] = {
    "ok": {"unicode": "✓", "ascii": "+"},
    "fail": {"unicode": "✗", "ascii": "x"},
    "blocked": {"unicode": "■", "ascii": "#"},
    "warn": {"unicode": "·", "ascii": "."},
    "running": {"unicode": "›", "ascii": ">"},
    "skip": {"unicode": "·", "ascii": "-"},
    "info": {"unicode": "·", "ascii": "*"},
}

#: Box drawing + a few punctuation glyphs, unicode vs ASCII.
GLYPHS: dict[str, dict[str, str]] = {
    "h": {"unicode": "─", "ascii": "-"},
    "v": {"unicode": "│", "ascii": "|"},
    "tl": {"unicode": "┌", "ascii": "+"},
    "tr": {"unicode": "┐", "ascii": "+"},
    "bl": {"unicode": "└", "ascii": "+"},
    "br": {"unicode": "┘", "ascii": "+"},
    "lt": {"unicode": "├", "ascii": "+"},
    "rt": {"unicode": "┤", "ascii": "+"},
    "cross": {"unicode": "┼", "ascii": "+"},
    "bullet": {"unicode": "•", "ascii": "-"},
    "sep": {"unicode": "·", "ascii": "-"},
    "dot": {"unicode": "·", "ascii": "."},  # the light rule: section ends, never an edge
    "ellipsis": {"unicode": "…", "ascii": "..."},
    "arrow": {"unicode": "→", "ascii": "->"},
    "bar_full": {"unicode": "█", "ascii": "#"},
    "bar_empty": {"unicode": "░", "ascii": "-"},
    "bar_head": {"unicode": "▓", "ascii": "="},
}

#: The shade ramp the logo art is drawn with (dark → dense). Pure ASCII on purpose:
#: the same characters render in ``rich`` and ``plain`` mode, only the grey differs.
SHADES = " .:-=+*#%@"


def _env_flag(value: str | None) -> bool:
    """``1/true/yes/on`` (and any other non-empty value) is on; ``0/false/no/off/""`` off."""
    if value is None or value == "":
        return False
    return value.strip().lower() not in {"0", "false", "no", "off", "none"}


def _isatty(stream: Any) -> bool:
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001 — a console without isatty() is not a terminal
        return False


def _encodable(stream: Any, sample: str = "✓✗■·─│┌┐█░") -> bool:
    """Can this stream encode the unicode glyph set? (A latin-1 log cannot.)"""
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        sample.encode(encoding, errors="strict")
    except Exception:  # noqa: BLE001 — unknown encoding: assume the safe ASCII set
        return False
    return True


def detect_depth(env: Mapping[str, str] | None = None) -> str:
    """How many grey levels this terminal can show (never a colour, only greys)."""
    env = os.environ if env is None else env
    term = str(env.get("TERM", "") or "")
    colorterm = str(env.get("COLORTERM", "") or "").lower()
    if colorterm in {"truecolor", "24bit"}:
        return DEPTH_TRUECOLOR
    if "256" in term or "truecolor" in term:
        return DEPTH_256
    if term in {"", "dumb"} and not colorterm:
        return DEPTH_BASIC
    return DEPTH_256 if term else DEPTH_BASIC


@dataclass(frozen=True)
class Theme:
    """The decided look for one output stream (immutable; :func:`use` swaps it)."""

    mode: str = MODE_PLAIN
    depth: str = DEPTH_NONE
    glyphs: str = GLYPHS_ASCII
    source: str = "default"
    width: int = 0
    height: int = 0

    # -- the two questions every other module asks ------------------------------- #
    @property
    def color(self) -> bool:
        """ANSI escapes allowed on this stream?"""
        return self.mode == MODE_RICH

    @property
    def unicode(self) -> bool:
        """Unicode glyphs (✓ ✗ ■ · ─ │) allowed on this stream?"""
        return self.glyphs == GLYPHS_UNICODE

    @property
    def plain(self) -> bool:
        return self.mode == MODE_PLAIN

    # -- primitives -------------------------------------------------------------- #
    def sgr(self, *codes: str) -> str:
        """One SGR sequence for these codes — empty when colour is off."""
        if not self.color:
            return ""
        parts = [str(code) for code in codes if str(code)]
        return f"{ESC}{';'.join(parts)}m" if parts else ""

    def reset(self) -> str:
        return RESET if self.color else ""

    def grey(self, level: int | str) -> str:
        """A grey 0-255 as a foreground SGR (a name from :data:`GREYS` also works)."""
        if isinstance(level, str):
            level = GREYS.get(level, 200)
        level = max(0, min(255, int(level)))
        if self.depth == DEPTH_TRUECOLOR:
            return f"38;2;{level};{level};{level}"
        if self.depth == DEPTH_256:
            return f"38;5;{_grey_256(level)}"
        if self.depth == DEPTH_BASIC:
            return "1" if level >= 160 else ("2" if level <= 90 else "")
        return ""

    def style(self, text: str, token: str = "default") -> str:
        """Draw ``text`` in a semantic token (see :data:`TOKENS`)."""
        if not self.color or token in {"default", "value"}:
            return text
        codes: list[str] = []
        grey = TOKEN_GREY.get(token)
        if grey is not None:
            code = self.grey(grey)
            if code:
                codes.append(code)
        codes.extend(TOKEN_ATTRS.get(token, ()))
        if not codes:
            return text
        return f"{self.sgr(*codes)}{text}{self.reset()}"

    def mark(self, status: str = "ok") -> str:
        """The glyph for a status (✓ / ✗ / ■ / · — or ``+`` / ``x`` / ``#`` / ``.``)."""
        table = MARKS.get(status) or MARKS["info"]
        return table[GLYPHS_UNICODE if self.unicode else GLYPHS_ASCII]

    def glyph(self, name: str) -> str:
        """A box-drawing or punctuation glyph (``h``, ``tl``, ``bullet``, ``ellipsis`` …)."""
        table = GLYPHS.get(name)
        if table is None:
            return name
        return table[GLYPHS_UNICODE if self.unicode else GLYPHS_ASCII]

    def mark_line(self, status: str, text: str) -> str:
        """``"✓  text"`` — the mark, **two spaces**, the text.

        The two-space rhythm is what makes a column of marks scannable next to a
        single-space indent; it is the same in every command and in both modes.
        """
        token = status if status in TOKENS else "default"
        return f"{self.style(self.mark(status), token)}  {text}"

    # -- convenience ------------------------------------------------------------- #
    def replace(self, **changes: Any) -> Theme:
        return replace(self, **changes)

    def capabilities(self) -> dict[str, Any]:
        """What this theme decided — for ``skaldir style palette --json``."""
        return {
            "mode": self.mode,
            "depth": self.depth,
            "glyphs": self.glyphs,
            "color": self.color,
            "unicode": self.unicode,
            "source": self.source,
            "width": self.width,
            "height": self.height,
            "grey_token": {token: TOKEN_GREY[token] for token in TOKENS},
            "grey_levels": dict(GREYS),
            "marks": {status: self.mark(status) for status in MARKS},
            "legend": "  ".join(
                f"{self.mark(status)} {status}" for status in ("ok", "fail", "blocked", "warn")
            ),
        }


def _grey_256(level: int) -> int:
    """Snap a 0-255 grey to the xterm 256-colour grey ramp (232-255) or the cube."""
    if level < 8:
        return 16
    if level > 238:
        return 231
    return 232 + round((level - 8) / 10)


def detect(
    stream: TextIO | Any | None = None,
    *,
    no_color: bool = False,
    env: Mapping[str, str] | None = None,
    mode: str | None = None,
    depth: str | None = None,
    glyphs: str | None = None,
) -> Theme:
    """Decide the theme for a stream.

    ``no_color`` is the CLI's ``--no-color``; ``NO_COLOR`` is honoured the same way
    (present and non-empty). A stream that is not a TTY, or one whose encoding
    cannot carry the glyphs, degrades on its own. ``mode``/``depth``/``glyphs``
    are explicit overrides — the escape hatch for tests and for ``SKALDIR_*``.
    """
    env = os.environ if env is None else env
    stream = sys.stdout if stream is None else stream
    tty = _isatty(stream)

    no_color_env = str(env.get("NO_COLOR", "") or "") != ""
    force_env = _env_flag(getenv("COLOR", environ=env)) or _env_flag(env.get("FORCE_COLOR"))
    force_off_env = str(getenv("COLOR", "", environ=env) or "").strip().lower() in {
        "0", "false", "no", "off",
    }

    if mode is None:
        if no_color or no_color_env or force_off_env:
            mode = MODE_PLAIN
            source = "no-color"
        elif force_env:
            mode = MODE_RICH
            source = "forced"
        elif not tty:
            mode = MODE_PLAIN
            source = "not-a-tty"
        else:
            mode = MODE_RICH
            source = "tty"
    else:
        mode = MODE_RICH if mode == MODE_RICH else MODE_PLAIN
        source = "explicit"

    if depth is None:
        depth = detect_depth(env) if mode == MODE_RICH else DEPTH_NONE
    if mode == MODE_PLAIN:
        depth = DEPTH_NONE

    if glyphs is None:
        override = getenv("UNICODE", environ=env)
        if override is not None and str(override) != "":
            glyphs = GLYPHS_UNICODE if _env_flag(override) else GLYPHS_ASCII
        elif mode == MODE_RICH and _encodable(stream):
            glyphs = GLYPHS_UNICODE
        else:
            glyphs = GLYPHS_ASCII

    width = 0
    for key in (*expand(("SKALDIR_WIDTH",)), "COLUMNS"):
        try:
            value = int(str(env.get(key, "") or "0"))
        except ValueError:
            value = 0
        if value > 0:
            width = value
            break

    height = 0
    for key in (*expand(("SKALDIR_HEIGHT",)), "LINES"):
        try:
            value = int(str(env.get(key, "") or "0"))
        except ValueError:
            value = 0
        if value > 0:
            height = value
            break

    return Theme(mode=mode, depth=depth, glyphs=glyphs, source=source, width=width, height=height)


# --------------------------------------------------------------------------- #
# the process-wide theme                                                       #
# --------------------------------------------------------------------------- #

_CURRENT: Theme = Theme()


def get() -> Theme:
    """The theme for the current process (plain until :func:`detect` says otherwise)."""
    return _CURRENT


def set(theme: Theme) -> Theme:  # noqa: A001 — the module owns this name on purpose
    """Install a process-wide theme; returns the previous one."""
    global _CURRENT
    previous = _CURRENT
    _CURRENT = theme
    return previous


@contextmanager
def use(theme: Theme) -> Iterator[Theme]:
    """``with theme.use(t):`` — process-wide for the block, restored afterwards."""
    previous = set(theme)
    try:
        yield theme
    finally:
        set(previous)


def activate(stream: TextIO | Any | None = None, *, no_color: bool = False,
             env: Mapping[str, str] | None = None, **overrides: Any) -> Theme:
    """Detect for a stream and install the result (what a command handler calls)."""
    theme = detect(stream, no_color=no_color, env=env, **overrides)
    set(theme)
    return theme


def for_args(args: Any) -> Theme:
    """Install the theme one parsed command asks for (its ``--no-color``, then the stream).

    The one line a handler needs before it prints anything: every helper in
    :mod:`skaldir.render` and :mod:`skaldir.logo` then defaults to this theme.
    """
    return activate(no_color=bool(getattr(args, "no_color", False)))


__all__ = [
    "DEPTHS",
    "DEPTH_256",
    "DEPTH_BASIC",
    "DEPTH_NONE",
    "DEPTH_TRUECOLOR",
    "GLYPHS",
    "GLYPHS_ASCII",
    "GLYPHS_UNICODE",
    "GREYS",
    "MARKS",
    "MODES",
    "MODE_PLAIN",
    "MODE_RICH",
    "SHADES",
    "TOKENS",
    "TOKEN_ATTRS",
    "TOKEN_GREY",
    "Theme",
    "activate",
    "detect",
    "detect_depth",
    "for_args",
    "get",
    "set",
    "use",
    "register_cli",
]


# --------------------------------------------------------------------------- #
# the CLI group: `skaldir style …`                                            #
# --------------------------------------------------------------------------- #

def _product_contract() -> Any:
    """The product CLI's envelope module, when this build has one."""
    try:
        from . import cli_contract
    except Exception:  # noqa: BLE001 — an older build without the module
        return None
    return cli_contract


_CONTRACT = _product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))


def _share_common_flags(parser: Any) -> None:
    if _CONTRACT is None or not hasattr(_CONTRACT, "add_common_flags"):
        return
    try:
        _CONTRACT.add_common_flags(parser)
    except Exception:  # noqa: BLE001 — a flag helper must never break the group
        return


def _theme_for(args: argparse.Namespace) -> Theme:
    """The theme for this invocation: the shared flags decide, the environment backs it."""
    return for_args(args)


def _report(data: dict, evidence: list[dict] | None = None) -> None:
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(data)
        for item in evidence or []:
            _CONTRACT.add_evidence(
                item.get("ref", ""), item.get("kind", "code"), note=item.get("note", "")
            )
    except Exception:  # noqa: BLE001 — reporting must never break the command
        return


def cmd_preview(args: argparse.Namespace) -> int:
    """Print every helper of the visual language — the reference implementation."""
    from . import logo, render

    theme = _theme_for(args)
    print(render.heading("Skaldir CLI visual language", theme=theme))
    print(render.spacer())
    print(render.bar("sections", theme=theme))
    print("  a bar opens a section; its label is the only ALL-CAPS text in the CLI")
    print(render.end_rule(theme=theme))
    print("  a light rule closes one (before a summary or a footer line, never randomly)")
    print(render.spacer())
    print(render.bar("status lines", theme=theme))
    for status in ("ok", "fail", "blocked", "warn", "running", "skip"):
        print(render.status_line(status, f"{status} line", theme=theme))
    print(render.status_line(
        "ok", "a detail sits four columns in and reads dim", indent=render.DETAIL_INDENT,
        muted=True, theme=theme))
    print(render.spacer())
    print(render.bar("key/value", theme=theme))
    size = f"{render.terminal_width()}x{render.terminal_height()}"
    print(render.key_values(
        [("theme", theme.mode), ("depth", theme.depth), ("glyphs", theme.glyphs),
         ("source", theme.source), ("size", size)],
        theme=theme))
    print(render.spacer())
    print(render.bar("table", theme=theme))
    print(render.table(
        ["layer", "verb", "state"],
        [["EXPAND", "expand", "ok"], ["ACT", "act", "ok"],
         ["VERIFY", "verify", "blocked"], ["COMPOSE", "compose", "warn"]],
        theme=theme))
    print(render.spacer())
    print(render.bar("steps", theme=theme))
    print(render.step_line(1, 3, "collect", theme=theme))
    print(render.step_line(2, 3, "build", status="ok", theme=theme))
    print(render.step_line(3, 3, "verify", status="warn", theme=theme))
    print(render.spacer())
    print(render.progress(0.62, label="installing", theme=theme))
    print(render.spacer())
    print(render.bar("box", theme=theme))
    print(render.box([
        "The CLI is monochrome: one ink, one paper, a grey ramp.",
        "Colour is a property of the terminal, never of the message.",
    ], title="note", theme=theme))
    print(render.spacer())
    print(render.bar("legend", theme=theme))
    print("  " + render.legend(theme=theme))
    print(render.spacer())
    print(logo.splash(theme=theme))
    _report(
        {"capabilities": theme.capabilities(), "commands": ["style.preview"]},
        evidence=[{"ref": "src/skaldir/theme.py", "kind": "code",
                   "note": "the theme decides colour/glyph mode per stream"},
                  {"ref": "src/skaldir/render.py", "kind": "code",
                   "note": "every helper drawn above"},
                  {"ref": "src/skaldir/logo.py", "kind": "code",
                   "note": "the banner and the compact mark"}],
    )
    return EXIT_OK


def cmd_logo(args: argparse.Namespace) -> int:
    """Print the logo art: the startup banner, the compact mark or the ASCII fallback."""
    from . import logo

    theme = _theme_for(args)
    if getattr(args, "ascii", False):
        theme = theme.replace(mode=MODE_PLAIN, depth=DEPTH_NONE, glyphs=GLYPHS_ASCII)
        set(theme)
    which = str(getattr(args, "which", "") or "banner")
    text = {
        "banner": lambda: logo.banner(theme=theme),
        "mark": lambda: logo.mark(theme=theme),
        "splash": lambda: logo.splash(theme=theme),
    }.get(which, lambda: logo.banner(theme=theme))()
    if not text:
        # plain on purpose: the message may be the only thing a log file keeps
        message = (
            f"no CLI art found - looked for {logo.ART_FILENAME} in "
            + ", ".join(str(path) for path in logo.art_candidates())
        )
        print(message)
        if _CONTRACT is not None and hasattr(_CONTRACT, "not_found"):
            return int(_CONTRACT.not_found(
                message, hint="run `python -m skaldir.logo regenerate` from the repo root",
                details={"candidates": [str(p) for p in logo.art_candidates()]}))
        return EXIT_INTERNAL
    print(text)
    art = logo.load_art()
    _report(
        {"art": which, "path": str(art.path), "grids": sorted(art.grids),
         "source": art.source, "capabilities": theme.capabilities()},
        evidence=[{"ref": str(art.path), "kind": "file",
                   "note": f"{art.source} — the logo PNG converted once, checked in"}],
    )
    return EXIT_OK


def cmd_palette(args: argparse.Namespace) -> int:
    """Print the grey ramp, the tokens and the status marks — the literal palette."""
    from . import render

    theme = _theme_for(args)
    print(render.heading("Palette", theme=theme))
    print(render.key_values(
        [("mode", theme.mode), ("depth", theme.depth), ("glyphs", theme.glyphs),
         ("legend", render.legend(theme=theme))],
        theme=theme))
    print(render.spacer())
    print(render.bar("grey ramp", theme=theme))
    rows = [[name, str(GREYS[name]), theme.style("████", name)] for name in GREYS]
    print(render.table(["grey", "level", "sample"], rows, theme=theme))
    print(render.spacer())
    print(render.bar("tokens", theme=theme))
    print(render.table(
        ["token", "grey", "weight"],
        [[token, str(TOKEN_GREY[token]), " ".join(TOKEN_ATTRS[token]) or "-"]
         for token in TOKENS],
        theme=theme))
    print(render.spacer())
    print(render.bar("marks", theme=theme))
    print(render.table(
        ["status", "unicode", "ascii"],
        [[status, MARKS[status]["unicode"], MARKS[status]["ascii"]] for status in MARKS],
        theme=theme))
    print(render.spacer())
    print(render.bar("glyphs", theme=theme))
    print(render.table(
        ["name", "unicode", "ascii"],
        [[name, GLYPHS[name]["unicode"], GLYPHS[name]["ascii"]] for name in GLYPHS],
        theme=theme))
    print(render.end_rule(theme=theme))
    sep = theme.glyph("sep")
    print(f"  {len(GREYS)} grey(s) {sep} {len(TOKENS)} token(s) {sep} "
          f"{len(MARKS)} mark(s) {sep} {len(GLYPHS)} glyph(s)")
    _report(
        {"capabilities": theme.capabilities(), "shades": SHADES},
        evidence=[{"ref": "src/skaldir/theme.py", "kind": "code",
                   "note": "GREYS / TOKENS / MARKS are the single source of the look"}],
    )
    return EXIT_OK


def _add_style_verbs(sub: Any) -> Any:
    """Add the three ``style`` verbs to a subparsers handle (one definition)."""
    preview = sub.add_parser("preview", help="every helper of the visual language")
    preview.add_argument("--json", action="store_true")
    preview.set_defaults(func=cmd_preview, command_path="style.preview")

    logo_parser = sub.add_parser("logo", help="the Skaldir CLI art (banner or mark)")
    logo_parser.add_argument("which", nargs="?", default="banner",
                             choices=("banner", "mark", "splash"),
                             help="which piece of art (default: banner)")
    logo_parser.add_argument("--ascii", action="store_true",
                             help="force the plain ASCII rendering")
    logo_parser.add_argument("--json", action="store_true")
    logo_parser.set_defaults(func=cmd_logo, command_path="style.logo")

    palette = sub.add_parser("palette", help="grey ramp, tokens and status marks")
    palette.add_argument("--json", action="store_true")
    palette.set_defaults(func=cmd_palette, command_path="style.palette")

    for child in (preview, logo_parser, palette):
        _share_common_flags(child)
    return sub


def build_style_parser(subparsers: Any) -> Any:
    """Add the ``style`` command group (preview / logo / palette) under ``skaldir``."""
    parser = subparsers.add_parser(
        "style",
        help="the CLI's visual language (preview / logo / palette)",
        description=(
            "Monochrome on purpose: black, white and a grey ramp, plus typographic "
            "weight. `style preview` prints every helper so a new command can copy "
            "the look; `style logo` prints the Skaldir art (banner or compact mark, "
            "ANSI or plain ASCII); `style palette` prints the grey levels, the tokens "
            "and the status marks. All three honour --no-color and NO_COLOR."
        ),
    )
    _add_style_verbs(parser.add_subparsers(dest="style_command", required=True))
    return parser


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``.

    Idempotent on purpose: the group is wired inside ``build_parser()`` *and* some
    tests wire it again on the parser's subparsers handle — argparse would raise
    "conflicting subparser: style". Wiring twice returns the group that is already
    there, so no sibling test has to know about this one.
    """
    choices = getattr(subparsers, "choices", None)
    if isinstance(choices, dict) and "style" in choices:
        return choices["style"]
    return build_style_parser(subparsers)


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(prog="skaldir style", description="CLI visual language")
    _add_style_verbs(parser.add_subparsers(dest="command", required=True))
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir style …`` — returns the exit code, never raises for a request."""
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
