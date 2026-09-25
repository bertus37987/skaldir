"""The Skaldir CLI logo as ASCII/ANSI art — banner, compact mark and the generator.

The owner's logo is a 1254x1254 opaque **black-on-white** PNG
(``/home/willy/Documents/Design_Docs/logos_icons/Skaldir_Logo.png``, read-only source).
This module turns it into text once and checks the result in as
``assets/cli-logo.txt`` — a grid of one hex digit per pixel (``0`` = paper, ``f`` = full
ink) — so no image dependency (and no Pillow) is needed at run time, and the CLI never
touches the PNG.

Three pieces of art are generated and shipped:

``banner``  the full startup band, 46 columns wide (25 lines)
``mark``    the compact mark, 16 columns wide (9 lines, shade ramp)
``caption`` the wordmark (``S K A L D I R`` / ``SKALDIR``), from the same art file

Both grids come from the *same* source and are drawn with the ASCII shade ramp, one pixel
row per terminal line, aspect corrected for the ~2:1 character cell; with the theme's
colour on, each run of equal ink gets a grey foreground, and with colour off the output is
exactly the same characters with no escape sequences at all, so a redirect stays readable
and diffable.

:func:`splash` and :func:`welcome` adapt the *art* to the terminal, never the words: a
tall terminal gets the full 46x25 band, an 80x24 one gets the compact mark (both are the
Skaldir banner, the second one simply fits), and a terminal without room for art still
gets the wordmark. Nothing else in the CLI draws the logo, and the legacy
``assets/logo.ansi`` (the pre-rebrand gold/red art) is never printed.

Regenerate the art (only needed when the logo changes)::

    python -m skaldir.logo regenerate          # writes assets/cli-logo.txt, no image deps
    python -m skaldir.logo show --ascii        # print what the CLI will show

The PNG reader below is deliberately tiny: 8-bit greyscale/RGB/palette/RGBA, all five
scanline filters, stdlib only (``zlib`` + ``struct``).
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import render
from . import theme as theme_mod
from .env import getenv
from .theme import DEPTH_NONE, GLYPHS_ASCII, MODE_PLAIN, SHADES, Theme

ART_FILENAME = "cli-logo.txt"

#: The owner's design file (read-only) and the byte-identical copy inside the repo.
OWNER_PNG = Path("/home/willy/Documents/Design_Docs/logos_icons/Skaldir_Logo.png")
REPO_PNG = Path(__file__).resolve().parent / "assets" / "skaldir-logo.png"

BANNER_COLUMNS = 46
MARK_COLUMNS = 16

#: From this many terminal rows on, the startup gets the full band instead of the mark.
FULL_SPLASH_ROWS = 34

#: From this many terminal rows on, the startup can afford both hint sections: the whole
#: screen (band + signature + two sections + footer) is 41 lines, so 42 is the first
#: height where the second section does not push the prompt off the screen.
FULL_HINT_ROWS = 42

#: The one-line product signature every header and footer can fall back to.
SUBTITLE = "self-learning agent runtime"

#: paper/ink decision when looking for the drawing's bounding box
INK_THRESHOLD = 200
INK_FLOOR = 0.06

#: the compact mark keeps the shade ramp: posterizing it turned the emblem into a blob
POSTERIZE_LEVEL: int | None = None


class LogoArtError(RuntimeError):
    """The checked-in art is missing or malformed (never fatal to the CLI)."""


@dataclass(frozen=True)
class Grid:
    """One piece of art: a matrix of ink levels 0-15, one entry per pixel."""

    name: str
    columns: int
    rows: int
    cells: tuple[tuple[int, ...], ...]

    def level(self, column: int, row: int) -> int:
        return self.cells[row][column]

    @property
    def ink(self) -> int:
        """Total ink — 0 means the grid is blank (a generator bug, not a logo)."""
        return sum(sum(row) for row in self.cells)

    def as_text(self) -> str:
        return "\n".join("".join(f"{value:x}" for value in row) for row in self.cells)


@dataclass(frozen=True)
class Art:
    """The parsed art file: the grids plus the owner-facing captions."""

    path: Path
    source: str
    grids: dict[str, Grid] = field(default_factory=dict)
    captions: dict[str, list[str]] = field(default_factory=dict)

    def grid(self, name: str) -> Grid:
        try:
            return self.grids[name]
        except KeyError as exc:
            raise LogoArtError(f"the art file has no [{name}] grid: {self.path}") from exc

    def caption(self, name: str, default: str = "") -> list[str]:
        return self.captions.get(name) or ([default] if default else [])


# --------------------------------------------------------------------------- #
# where the art lives                                                          #
# --------------------------------------------------------------------------- #

def art_candidates() -> list[Path]:
    """Every place the art may live, most specific first (``$SKALDIR_CLI_ART`` wins)."""
    candidates: list[Path] = []
    override = str(getenv("CLI_ART", "") or "")
    if override:
        candidates.append(Path(override).expanduser())
    here = Path(__file__).resolve().parent
    candidates.append(here / "assets" / ART_FILENAME)          # installed package
    candidates.append(here.parent.parent / "assets" / ART_FILENAME)  # repo root
    return candidates


def art_path() -> Path | None:
    """The first candidate that exists, or ``None``."""
    for candidate in art_candidates():
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


_CACHE: dict[str, Art] = {}


def load_art(path: Path | str | None = None) -> Art:
    """Parse the art file (cached). Raises :class:`LogoArtError` when it is unusable."""
    if path is None:
        found = art_path()
        if found is None:
            raise LogoArtError("no " + ART_FILENAME + " found next to the package or the repo")
        path = found
    path = Path(path)
    key = str(path)
    if key in _CACHE:
        return _CACHE[key]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LogoArtError(f"cannot read the art file {path}: {exc}") from exc
    art = parse_art(text, path=path)
    _CACHE[key] = art
    return art


def parse_art(text: str, *, path: Path | str = Path(ART_FILENAME)) -> Art:
    """Parse the grid/caption file format (see :func:`regenerate` for the writer)."""
    grids: dict[str, Grid] = {}
    captions: dict[str, list[str]] = {}
    source = ""
    name = ""
    header = ""
    body: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not name:
            if stripped.startswith("#") and "source:" in stripped:
                source = stripped.split("source:", 1)[1].strip()
            if stripped.startswith("[grid "):
                head = stripped[len("[grid "):].rstrip("]").strip()
                parts = head.split()
                if len(parts) != 2 or "x" not in parts[1]:
                    raise LogoArtError(f"malformed grid header: {stripped!r}")
                columns, rows = parts[1].split("x")
                name, header, body = parts[0], f"{columns}x{rows}", []
            elif stripped.startswith("[caption:"):
                name = stripped[len("[caption:"):].rstrip("]").strip()
                header, body = "", []
            continue
        if stripped == "[end]":
            if header:
                grids[name] = _grid_from_lines(name, header, body, path)
            else:
                captions[name] = body
            name, header, body = "", "", []
            continue
        body.append(line)
    if name:
        raise LogoArtError(f"unterminated section [{name}] in {path}")
    if not grids:
        raise LogoArtError(f"no grids in {path}")
    return Art(path=Path(path), source=source, grids=grids, captions=captions)


def _grid_from_lines(name: str, header: str, lines: Sequence[str], path: Path) -> Grid:
    columns, rows = (int(value) for value in header.split("x"))
    trimmed = [line for line in lines if line.strip()]
    if len(trimmed) != rows:
        raise LogoArtError(
            f"grid {name!r} in {path} says {rows} rows but carries {len(trimmed)}")
    cells: list[tuple[int, ...]] = []
    for line in trimmed:
        digits = [char for char in line if not char.isspace()]
        if len(digits) != columns:
            raise LogoArtError(
                f"grid {name!r} in {path} says {columns} columns but row has {len(digits)}")
        try:
            cells.append(tuple(int(char, 16) for char in digits))
        except ValueError as exc:
            raise LogoArtError(f"grid {name!r} in {path} has a non-hex digit: {exc}") from exc
    return Grid(name=name, columns=columns, rows=rows, cells=tuple(cells))


# --------------------------------------------------------------------------- #
# rendering                                                                    #
# --------------------------------------------------------------------------- #

def _theme(theme: Theme | None) -> Theme:
    return theme if theme is not None else theme_mod.get()


def render_grid(grid: Grid, *, theme: Theme | None = None, shades: str = SHADES) -> str:
    """Draw a grid: one ink pixel per character, grey foreground when colour is on.

    Identical characters in ``rich`` and ``plain`` mode; only the SGR runs differ, so
    the art degrades to pure ASCII instead of to something else.
    """
    current = _theme(theme)
    lines: list[str] = []
    for row in grid.cells:
        out = ""
        run_level = -1
        run = ""
        for value in row:
            value = 0 if value <= 0 else (15 if value > 15 else int(value))
            char = shades[min(len(shades) - 1, value * (len(shades) - 1) // 15)]
            if current.color and value != run_level:
                out += _styled_run(run, run_level, current)
                run, run_level = char, value
            elif current.color:
                run += char
            else:
                out += char
        if current.color:
            out += _styled_run(run, run_level, current)
        lines.append(out.rstrip())
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines)


def _styled_run(run: str, level: int, current: Theme) -> str:
    if not run:
        return ""
    if level <= 0:
        return run
    return current.sgr(current.grey(round(255 * level / 15))) + run + current.reset()


def _art_or_error(theme: Theme | None) -> Art | None:
    try:
        return load_art()
    except LogoArtError:
        return None


def banner(*, theme: Theme | None = None) -> str:
    """The startup band (46 columns x 25 lines), or an empty string when the art is missing."""
    art = _art_or_error(theme)
    if art is None:
        return ""
    return render_grid(art.grid("banner"), theme=_theme(theme))


def mark(*, theme: Theme | None = None) -> str:
    """The compact mark (24 columns x 13 lines, solid ink), or ``""`` when missing."""
    art = _art_or_error(theme)
    if art is None:
        return ""
    return render_grid(art.grid("mark"), theme=_theme(theme))


def art_for_screen(*, theme: Theme | None = None, columns: int | None = None,
                   rows: int | None = None) -> tuple[str, str]:
    """The piece of art this screen gets, and its name.

    The one depth decision the CLI makes on its own: the full band on a tall terminal,
    the compact mark on a short one, and nothing at all when the art is missing — the
    wordmark then carries the product by itself. Returns ``(art, name)``.
    """
    current = _theme(theme)
    height = render.terminal_height() if rows is None else int(rows)
    if columns is not None and int(columns) < BANNER_COLUMNS:
        return mark(theme=current), "mark"
    if height >= FULL_SPLASH_ROWS:
        drawn = banner(theme=current)
        if drawn:
            return drawn, "banner"
    return mark(theme=current), "mark"


def caption(name: str = "full", *, theme: Theme | None = None, default: str = "") -> str:
    """A caption line from the art file (``full`` / ``short``), styled for a heading."""
    art = _art_or_error(theme)
    if art is None:
        return default
    lines = art.caption(name, default)
    return lines[0] if lines else default


def _tail(theme: Theme, subtitle: str, version: str) -> str:
    """``self-learning agent runtime · v1.5.0`` in the muted token (`` - `` in ASCII)."""
    sep = f" {theme.glyph('sep')} "
    parts = [part for part in (subtitle, f"v{version}" if version else "") if part]
    return sep.join(parts)


def splash(*, theme: Theme | None = None, version: str = "",
           subtitle: str = SUBTITLE) -> str:
    """Banner + caption + subtitle: the whole startup signature in one string.

    The art is chosen for the screen (see :func:`art_for_screen`) and the words are the
    same either way, so the signature reads the same on an 80x24 terminal and in a
    40-row one.
    """
    current = _theme(theme)
    art, _name = art_for_screen(theme=current)
    parts: list[str] = []
    if art:
        parts.append(art)
        parts.append("")
        parts.append("  " + current.style(caption("full", theme=current, default="S K A L D I R"),
                                          "accent"))
    else:
        parts.append("  " + current.style("SKALDIR", "accent"))
    tail = _tail(current, subtitle, version)
    if tail:
        parts.append("  " + current.style(tail, "muted"))
    return "\n".join(parts).rstrip()


def welcome(*, theme: Theme | None = None, version: str = "", subtitle: str = SUBTITLE,
            runtime: str = "", directory: str = "",
            sections: Sequence[tuple[str, Sequence[tuple[str, str]]]] = (),
            footer: str = "", columns: int | None = None, rows: int | None = None) -> str:
    """The startup screen: the Skaldir banner, then the way in — nothing else.

    Compact single-line headers, bars as separators, one blank line between sections and a
    two-space rhythm under every mark. The layout adapts to the screen in exactly two
    ways — the art (band or mark) and how many hint sections fit — so an 80x24 terminal
    gets a screen that does not scroll its own prompt away, and a tall one gets everything.
    """
    current = _theme(theme)
    limit = render.terminal_width() if columns is None else max(1, int(columns))
    height = render.terminal_height() if rows is None else int(rows)

    out: list[str] = []
    art, _name = art_for_screen(theme=current, columns=columns, rows=height)
    if art:
        out.extend("  " + line for line in art.splitlines())
        out.append("")
    out.append("  " + current.style(caption("full", theme=current, default="S K A L D I R"),
                                    "accent"))
    tail = _tail(current, subtitle, version)
    if tail:
        out.append("  " + current.style(render.truncate(tail, max(1, limit - 2), theme=current),
                                        "muted"))
    if runtime or directory:
        where = f" {current.glyph('sep')} ".join(
            part for part in (f"runtime {runtime}" if runtime else "",
                              f"directory {directory}" if directory else "") if part)
        out.append("  " + current.style(render.truncate(where, max(1, limit - 2), theme=current),
                                        "muted"))

    chosen = list(sections)[:2 if height >= FULL_HINT_ROWS else 1]
    for title, items in chosen:
        out.append("")
        out.append(render.bar(title, columns=limit, theme=current))
        entries = [(str(command), str(description)) for command, description in items]
        pad = max((len(command) for command, _ in entries), default=0) + 2
        for command, description in entries:
            line = f"{command:<{pad}}{description}"
            out.append("  " + render.truncate(line, max(1, limit - 2), theme=current))
    if footer:
        out.append("")
        out.append("  " + current.style(render.truncate(footer, max(1, limit - 2), theme=current),
                                        "muted"))
    return "\n".join(out).rstrip()


def header(title: str = "", *, subtitle: str = "", theme: Theme | None = None) -> str:
    """One-line header: ``SKALDIR · verify — real-world verification``."""
    current = _theme(theme)
    short = caption("short", theme=current, default="SKALDIR")
    parts = [current.style(short, "accent")]
    if title:
        parts.append(current.style(title, "strong"))
    if subtitle:
        parts.append(current.style(subtitle, "muted"))
    return f" {current.glyph('sep')} ".join(parts)


__all__ = [
    "ART_FILENAME",
    "BANNER_COLUMNS",
    "FULL_HINT_ROWS",
    "FULL_SPLASH_ROWS",
    "MARK_COLUMNS",
    "OWNER_PNG",
    "REPO_PNG",
    "SUBTITLE",
    "Art",
    "Grid",
    "LogoArtError",
    "art_candidates",
    "art_for_screen",
    "art_path",
    "banner",
    "caption",
    "header",
    "load_art",
    "main",
    "mark",
    "parse_art",
    "read_png",
    "regenerate",
    "render_grid",
    "splash",
    "welcome",
]


# --------------------------------------------------------------------------- #
# the generator: PNG -> grids -> assets/cli-logo.txt                           #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PngImage:
    """A decoded 8-bit PNG (RGB or greyscale; alpha is respected as paper)."""

    width: int
    height: int
    rgb: bytes  # 3 bytes per pixel

    def ink(self, x: int, y: int) -> float:
        """Ink strength 0.0 (paper) … 1.0 (full ink) of one pixel."""
        offset = (y * self.width + x) * 3
        red, green, blue = self.rgb[offset], self.rgb[offset + 1], self.rgb[offset + 2]
        luminance = (red * 299 + green * 587 + blue * 114) // 1000
        return max(0.0, min(1.0, (255 - luminance) / 255.0))


def read_png(path: Path | str) -> PngImage:
    """Decode an 8-bit PNG with the standard library only (all five filters).

    16-bit files are refused with a clear message instead of being guessed at; the
    owner's logo is 8-bit RGB (colour type 2).
    """
    raw = Path(path).read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise LogoArtError(f"not a PNG file: {path}")
    position = 8
    palette: list[tuple[int, int, int]] = []
    chunks: list[bytes] = []
    width = height = depth = color_type = 0
    while position + 8 <= len(raw):
        length = struct.unpack(">I", raw[position:position + 4])[0]
        tag = raw[position + 4:position + 8]
        data = raw[position + 8:position + 8 + length]
        position += 12 + length
        if tag == b"IHDR":
            width, height, depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", data)
            if interlace:
                raise LogoArtError("interlaced PNGs are not supported")
        elif tag == b"PLTE":
            palette = [tuple(data[i:i + 3]) for i in range(0, len(data), 3)]  # type: ignore[misc]
        elif tag == b"IDAT":
            chunks.append(data)
        elif tag == b"IEND":
            break
    if depth != 8:
        raise LogoArtError(f"only 8-bit PNGs are supported, {path} is {depth}-bit")
    if color_type not in {0, 2, 3, 4, 6}:
        raise LogoArtError(f"unsupported PNG colour type {color_type} in {path}")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    stride = width * channels
    pixels = _unfilter(zlib.decompress(b"".join(chunks)), width, height, channels, stride)
    return PngImage(width, height, _to_rgb(pixels, width, height, channels, color_type, palette))


def _unfilter(data: bytes, width: int, height: int, channels: int, stride: int) -> bytes:
    out = bytearray()
    previous = bytearray(stride)
    position = 0
    for _ in range(height):
        if position >= len(data):
            raise LogoArtError("truncated PNG image data")
        method = data[position]
        position += 1
        line = bytearray(data[position:position + stride])
        position += stride
        if len(line) != stride:
            raise LogoArtError("truncated PNG scanline")
        if method == 1:
            for index in range(channels, stride):
                line[index] = (line[index] + line[index - channels]) & 0xFF
        elif method == 2:
            for index in range(stride):
                line[index] = (line[index] + previous[index]) & 0xFF
        elif method == 3:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        elif method == 4:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                up = previous[index]
                up_left = previous[index - channels] if index >= channels else 0
                estimate = left + up - up_left
                dist_left = abs(estimate - left)
                dist_up = abs(estimate - up)
                dist_up_left = abs(estimate - up_left)
                if dist_left <= dist_up and dist_left <= dist_up_left:
                    predictor = left
                elif dist_up <= dist_up_left:
                    predictor = up
                else:
                    predictor = up_left
                line[index] = (line[index] + predictor) & 0xFF
        elif method != 0:
            raise LogoArtError(f"unknown PNG filter type {method}")
        out += line
        previous = line
    return bytes(out)


def _to_rgb(pixels: bytes, width: int, height: int, channels: int, color_type: int,
            palette: Sequence[tuple[int, int, int]]) -> bytes:
    if color_type == 2:
        return pixels
    out = bytearray()
    for index in range(width * height):
        offset = index * channels
        if color_type == 0:
            grey = pixels[offset]
            out += bytes((grey, grey, grey))
        elif color_type == 4:
            grey = pixels[offset]
            alpha = pixels[offset + 1]
            paper = 255 - alpha
            value = (grey * alpha + paper * 255) // 255
            out += bytes((value, value, value))
        elif color_type == 6:
            red, green, blue, alpha = pixels[offset:offset + 4]
            paper = 255 - alpha
            out += bytes(((red * alpha + paper * 255) // 255,
                          (green * alpha + paper * 255) // 255,
                          (blue * alpha + paper * 255) // 255))
        else:  # colour type 3 — palette
            entry = palette[pixels[offset]] if palette else (0, 0, 0)
            out += bytes(entry)
    return bytes(out)


def ink_box(image: PngImage, threshold: int = INK_THRESHOLD) -> tuple[int, int, int, int]:
    """The drawing's bounding box as ``(x0, y0, x1, y1)`` — the surrounding paper is cut."""
    x0, y0 = image.width, image.height
    x1 = y1 = -1
    for y in range(image.height):
        for x in range(image.width):
            if image.ink(x, y) * 255 > 255 - threshold:
                x0 = min(x0, x)
                x1 = max(x1, x)
                y0 = min(y0, y)
                y1 = max(y1, y)
    if x1 < 0:
        raise LogoArtError("the source image is blank")
    return x0, y0, x1 + 1, y1 + 1


def grid_from_image(image: PngImage, *, name: str, columns: int,
                    box: tuple[int, int, int, int] | None = None,
                    posterize: int | None = None) -> Grid:
    """Box-filter the image into ``columns`` x rows ink levels (rows keep the aspect).

    One pixel row per terminal line: a character cell is about twice as tall as it is
    wide, so ``rows = columns * height / width / 2``. ``posterize`` (a level 1-15)
    snaps the result to solid ink at or above that level — used for the small mark,
    where half-tones read as noise and the silhouette is what identifies the logo.
    """
    if columns < 4:
        raise LogoArtError("the art needs at least 4 columns")
    x0, y0, x1, y1 = box or (0, 0, image.width, image.height)
    source_width, source_height = x1 - x0, y1 - y0
    if source_width <= 0 or source_height <= 0:
        raise LogoArtError("the ink box is empty")
    rows = max(2, int(round(columns * source_height / source_width / 2)))
    cells: list[tuple[int, ...]] = []
    for row in range(rows):
        line: list[int] = []
        for column in range(columns):
            left = x0 + source_width * column // columns
            right = max(left + 1, x0 + source_width * (column + 1) // columns)
            top = y0 + source_height * row // rows
            bottom = max(top + 1, y0 + source_height * (row + 1) // rows)
            total = 0.0
            count = 0
            step_x = max(1, (right - left) // 8)
            step_y = max(1, (bottom - top) // 8)
            for y in range(top, bottom, step_y):
                for x in range(left, right, step_x):
                    total += image.ink(x, y)
                    count += 1
            average = total / count if count else 0.0
            level = 0 if average < INK_FLOOR else max(1, min(15, round(average * 15)))
            if posterize is not None:
                level = 15 if level >= posterize else 0
            line.append(level)
        cells.append(tuple(line))
    return Grid(name=name, columns=columns, rows=rows, cells=tuple(cells))


def source_png(preferred: Path | str | None = None) -> Path:
    """The PNG to generate from: the explicit path, else the repo copy, else the owner's.

    An explicit path that does not exist is an error — never a silent substitution.
    """
    if preferred:
        candidate = Path(preferred)
        if candidate.is_file():
            return candidate
        raise LogoArtError(f"no logo PNG at {candidate}")
    for candidate in (REPO_PNG, OWNER_PNG):
        if candidate.is_file():
            return candidate
    raise LogoArtError(
        "no logo PNG found — looked for " + ", ".join(str(p) for p in (REPO_PNG, OWNER_PNG))
    )


def regenerate(png: Path | str | None = None, out: Path | str | None = None, *,
               banner_columns: int = BANNER_COLUMNS,
               mark_columns: int = MARK_COLUMNS) -> dict[str, Any]:
    """Convert the PNG into the checked-in art file; returns what happened."""
    source = source_png(png)
    target = Path(out) if out else default_art_path()
    image = read_png(source)
    box = ink_box(image)
    banner_grid = grid_from_image(image, name="banner", columns=banner_columns, box=box)
    mark_grid = grid_from_image(image, name="mark", columns=mark_columns, box=box,
                                posterize=POSTERIZE_LEVEL)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        art_text(image, source=source, box=box, grids=(banner_grid, mark_grid)),
        encoding="utf-8",
    )
    _CACHE.pop(str(target), None)
    return {
        "source": str(source),
        "out": str(target),
        "source_size": [image.width, image.height],
        "ink_box": list(box),
        "grids": {grid.name: [grid.columns, grid.rows] for grid in (banner_grid, mark_grid)},
        "bytes": target.stat().st_size,
    }


def default_art_path() -> Path:
    """Where the art is written: the repo-root ``assets/`` when there is one."""
    here = Path(__file__).resolve().parent
    repo_assets = here.parent.parent / "assets"
    if repo_assets.is_dir() or (here.parent.parent / "pyproject.toml").is_file():
        return repo_assets / ART_FILENAME
    return here / "assets" / ART_FILENAME


def art_text(image: PngImage, *, source: Path, box: tuple[int, int, int, int],
             grids: Sequence[Grid]) -> str:
    """The art file itself — self-describing, so the next reader trusts it."""
    x0, y0, x1, y1 = box
    lines = [
        "# Skaldir CLI art - the logo converted once, checked in. Do not edit by hand.",
        "# a grid carries one hex digit per pixel: 0 = paper, f = full ink",
        f"# source: {source}",
        f"# source: {image.width}x{image.height} px, ink box {x0},{y0}-{x1},{y1}",
        "# generator: python -m skaldir.logo regenerate  (stdlib only, no Pillow)",
        "# rendering: one pixel row per terminal line, aspect corrected for 2:1 cells;",
        "#            shades " + SHADES.strip() + " with a grey foreground when colour is on",
        "",
    ]
    for grid in grids:
        lines.append(f"[grid {grid.name} {grid.columns}x{grid.rows}]")
        lines.extend(
            "".join(f"{value:x}" for value in row) for row in grid.cells
        )
        lines.append("[end]")
        lines.append("")
    lines += [
        "[caption:full]",
        "S K A L D I R",
        "[end]",
        "",
        "[caption:short]",
        "SKALDIR",
        "[end]",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# `python -m skaldir.logo …`                                                     #
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    """The generator's own CLI (``regenerate`` / ``show`` / ``info``)."""
    parser = argparse.ArgumentParser(
        prog="python -m skaldir.logo",
        description="the Skaldir CLI logo art (generator + preview)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    regen = sub.add_parser("regenerate", help="PNG -> assets/cli-logo.txt")
    regen.add_argument("--png", default="", help="source PNG (default: the repo copy)")
    regen.add_argument("--out", default="", help="output file (default: assets/cli-logo.txt)")
    regen.add_argument("--banner-columns", type=int, default=BANNER_COLUMNS)
    regen.add_argument("--mark-columns", type=int, default=MARK_COLUMNS)

    show = sub.add_parser("show", help="print the art the CLI would show")
    show.add_argument("which", nargs="?", default="splash",
                      choices=("banner", "mark", "splash", "welcome", "header"))
    show.add_argument("--ascii", action="store_true", help="force the plain rendering")
    show.add_argument("--no-color", dest="no_color", action="store_true",
                      help="disable ANSI colour even on a TTY (NO_COLOR works too)")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=_cmd_show)

    info = sub.add_parser("info", help="where the art lives and what it carries")
    info.add_argument("--json", action="store_true")
    info.set_defaults(func=_cmd_info)
    return parser


def _plain_theme() -> Theme:
    return theme_mod.get().replace(mode=MODE_PLAIN, depth=DEPTH_NONE, glyphs=GLYPHS_ASCII)


def _cmd_show(args: argparse.Namespace) -> int:
    if args.ascii:
        theme_mod.set(_plain_theme())
    else:
        # the preview behaves like the CLI group: the stream decides, the flags win
        theme_mod.activate(no_color=bool(getattr(args, "no_color", False)))
    theme = theme_mod.get()
    which = args.which
    text = {
        "banner": lambda: banner(theme=theme),
        "mark": lambda: mark(theme=theme),
        "splash": lambda: splash(theme=theme, subtitle=SUBTITLE),
        "welcome": lambda: welcome(theme=theme, subtitle=SUBTITLE,
                                   footer="skaldir --help shows all commands"),
        "header": lambda: header("skaldir style logo", subtitle="the CLI art", theme=theme),
    }[which]()
    print(text)
    return 0 if text else 5


def _cmd_info(args: argparse.Namespace) -> int:
    import json

    path = art_path()
    payload: dict[str, Any] = {"art_file": str(path) if path else "", "found": bool(path)}
    if path:
        art = load_art(path)
        payload["source"] = art.source
        payload["grids"] = {name: [grid.columns, grid.rows] for name, grid in art.grids.items()}
        payload["captions"] = {name: lines for name, lines in art.captions.items()}
    print(json.dumps(payload, indent=2) if args.json else
          "\n".join(f"{key}: {value}" for key, value in payload.items()))
    return 0 if path else 5


def main(argv: list[str] | None = None) -> int:
    """``python -m skaldir.logo …`` — always exits with a number, never a traceback."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "regenerate":
        try:
            result = regenerate(args.png or None, args.out or None,
                                banner_columns=args.banner_columns,
                                mark_columns=args.mark_columns)
        except LogoArtError as exc:
            print(str(exc))
            return 3
        for key, value in result.items():
            print(f"{key}: {value}")
        return 0
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover — exercised through main()
    sys.exit(main())
