"""The checked-in logo art (``assets/cli-logo.txt``) and the generator that writes it.

The asset is a *derived* file: it must be reproducible from the source PNG byte for
byte, it must stay ASCII (it is the plain-mode banner), and it must not be blank. The
generator itself is exercised through the public ``regenerate()`` API — no Pillow, no
test fixtures beyond the repo's own copy of the logo.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from skaldir import logo
from skaldir import theme as T

REPO_ROOT = Path(__file__).resolve().parents[1]
ART_FILE = REPO_ROOT / "assets" / logo.ART_FILENAME
PLAIN = T.detect(io.StringIO(), env={})


@pytest.fixture(scope="module")
def art_text() -> str:
    if not ART_FILE.is_file():
        pytest.skip(f"the art asset is missing: {ART_FILE}")
    return ART_FILE.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# the checked-in asset                                                         #
# --------------------------------------------------------------------------- #

def test_the_asset_is_checked_in_with_its_source(art_text):
    assert art_text.startswith("# Skaldir CLI art")
    assert "# source:" in art_text
    assert "generator: python -m skaldir.logo regenerate" in art_text
    assert "[grid banner " in art_text and "[grid mark " in art_text
    assert "[caption:full]" in art_text and "[caption:short]" in art_text


def test_the_asset_is_ascii_and_small(art_text):
    assert art_text.isascii()
    assert len(art_text) < 40_000, "the art is a text asset, not a picture"
    assert ART_FILE.stat().st_size < 40_000


def test_the_grids_have_the_documented_sizes():
    art = logo.load_art(ART_FILE)
    banner = art.grid("banner")
    mark = art.grid("mark")
    assert (banner.columns, banner.rows) == (logo.BANNER_COLUMNS, 25)
    assert (mark.columns, mark.rows) == (logo.MARK_COLUMNS, 9)
    assert mark.columns < banner.columns and mark.rows < banner.rows
    assert art.caption("full") == ["S K A L D I R"]
    assert art.caption("short") == ["SKALDIR"]


def test_every_cell_is_a_hex_digit_within_range():
    art = logo.load_art(ART_FILE)
    for name, grid in art.grids.items():
        for row in grid.cells:
            assert len(row) == grid.columns, name
            assert all(0 <= value <= 15 for value in row), name


def test_the_banner_carries_ink_and_the_mark_is_solid():
    art = logo.load_art(ART_FILE)
    banner = art.grid("banner")
    mark = art.grid("mark")
    cells = banner.columns * banner.rows
    assert banner.ink > cells * 2, "the banner is blank — the generator or the PNG is wrong"
    assert mark.ink > mark.columns * mark.rows // 4
    levels = {value for grid in (mark,) for row in grid.cells for value in row}
    assert levels != {0, 15}, (
        "the compact mark keeps the shade ramp: posterizing it turned the emblem into a "
        "blob at 24 columns (measured 2026-09-18)"
    )
    assert max(levels) == 15, "the mark still carries solid ink"
    assert len(levels) >= 4, "a posterized mark loses the shape: the ramp is what reads"


def test_the_rows_keep_the_logo_aspect_ratio(art_text):
    """rows = columns * ink_height / ink_width / 2 — the only aspect correction."""
    art = logo.load_art(ART_FILE)
    banner = art.grid("banner")
    ratio_line = next(line for line in art_text.splitlines() if " ink box " in line)
    box = ratio_line.split("ink box ", 1)[1]
    (x0, y0), (x1, y1) = (part.split(",") for part in box.replace("-", " ").split())
    ink_width, ink_height = int(x1) - int(x0), int(y1) - int(y0)
    assert banner.rows == round(banner.columns * ink_height / ink_width / 2)
    assert 0.45 <= banner.rows / banner.columns <= 0.7   # a character cell is ~2:1 tall


def test_the_art_renders_ascii_only_without_colour_and_greys_with_it():
    plain = logo.banner(theme=PLAIN)
    assert plain.isascii() and "\x1b" not in plain
    rich = logo.banner(theme=T.detect(io.StringIO(), env={"GODSV_COLOR": "1",
                                                          "TERM": "xterm-256color"}))
    assert "\x1b[38;5;" in rich
    assert logo.render_grid(logo.load_art(ART_FILE).grid("banner"), theme=PLAIN)
    assert len(plain.splitlines()) == logo.load_art(ART_FILE).grid("banner").rows


# --------------------------------------------------------------------------- #
# the generator                                                                #
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not logo.REPO_PNG.is_file(), reason="the repo copy of the logo is missing")
def test_regenerating_reproduces_the_checked_in_art(monkeypatch, tmp_path):
    """The asset is derived: same PNG in, byte-identical file out."""
    monkeypatch.setattr(logo, "_CACHE", {})
    out = tmp_path / logo.ART_FILENAME
    result = logo.regenerate(png=logo.REPO_PNG, out=out)
    assert Path(result["out"]) == out
    assert result["source_size"] == [1254, 1254]
    # the ink box is measured, not hardcoded: the drawing sits inside the square
    left, top, right, bottom = result["ink_box"]
    assert 0 < left < right <= 1254 and 0 < top < bottom <= 1254
    assert out.read_text(encoding="utf-8") == ART_FILE.read_text(encoding="utf-8")


@pytest.mark.skipif(not logo.REPO_PNG.is_file(), reason="the repo copy of the logo is missing")
def test_the_repo_copy_is_the_owners_file():
    """The generator reads the repo copy; it must still be the owner's design file."""
    if not logo.OWNER_PNG.is_file():
        pytest.skip("the owner's design file is not on this machine")
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()  # noqa: E731
    assert digest(logo.REPO_PNG) == digest(logo.OWNER_PNG)


@pytest.mark.skipif(not logo.REPO_PNG.is_file(), reason="the repo copy of the logo is missing")
def test_the_png_reader_decodes_the_logo():
    image = logo.read_png(logo.REPO_PNG)
    assert (image.width, image.height) == (1254, 1254)
    assert len(image.rgb) == 1254 * 1254 * 3
    assert image.ink(0, 0) < 0.2                      # the corner is paper
    center = image.ink(660, 620)
    assert center > 0.5                               # the glyph is ink
    left, top, right, bottom = logo.ink_box(image)
    assert (left, top) == (282, 213)
    assert 1030 <= right <= 1040 and 1020 <= bottom <= 1030


@pytest.mark.skipif(not logo.REPO_PNG.is_file(), reason="the repo copy of the logo is missing")
def test_grid_geometry_follows_the_ink_box():
    image = logo.read_png(logo.REPO_PNG)
    box = logo.ink_box(image)
    grid = logo.grid_from_image(image, name="probe", columns=40, box=box)
    assert grid.columns == 40
    assert 18 <= grid.rows <= 24, "40 columns of a 757x812 drawing is ~22 lines"
    assert grid.ink > 0


def test_the_generator_refuses_a_missing_source(tmp_path):
    with pytest.raises(logo.LogoArtError):
        logo.regenerate(png=tmp_path / "nope.png", out=tmp_path / "art.txt")


def test_the_generator_refuses_a_file_that_is_not_a_png(tmp_path):
    bogus = tmp_path / "logo.png"
    bogus.write_bytes(b"not a png at all")
    with pytest.raises(logo.LogoArtError):
        logo.read_png(bogus)


@pytest.mark.skipif(not logo.REPO_PNG.is_file(), reason="the repo copy of the logo is missing")
def test_the_generator_cli_writes_the_file_and_reports(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(logo, "_CACHE", {})
    out = tmp_path / "art.txt"
    assert logo.main(["regenerate", "--png", str(logo.REPO_PNG), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "grids:" in printed and "ink_box:" in printed
    assert out.is_file()

    assert logo.main(["info", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["found"] is True
    assert info["grids"]["banner"] == [logo.BANNER_COLUMNS, 25]


def test_the_generator_accepts_other_geometry(tmp_path):
    if not logo.REPO_PNG.is_file():
        pytest.skip("the repo copy of the logo is missing")
    out = tmp_path / "small.txt"
    result = logo.regenerate(png=logo.REPO_PNG, out=out, banner_columns=10, mark_columns=6)
    assert result["grids"]["banner"] == [10, 5] or result["grids"]["banner"][0] == 10
    assert out.read_text(encoding="utf-8").count("[grid ") == 2


def test_show_prints_the_art_and_reports_a_missing_one(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(logo, "_CACHE", {})
    assert logo.main(["show", "mark", "--ascii"]) == 0
    assert capsys.readouterr().out.strip()

    monkeypatch.setattr(logo, "art_candidates", lambda: [])
    assert logo.main(["show", "banner", "--ascii"]) == 5
