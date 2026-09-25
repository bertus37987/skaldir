"""``godsv.logo``: the art loader, the renderer and the plain-ASCII contract.

The checked-in asset is exercised in ``test_logo_art.py``; this file pins the module's
behaviour — including the promise that a missing or malformed art file degrades to an
empty string instead of exploding inside the CLI's startup path.
"""

from __future__ import annotations

import io

import pytest

from skaldir import logo
from skaldir import theme as T


class FakeTTY(io.StringIO):
    def __init__(self, encoding: str = "utf-8") -> None:
        super().__init__()
        self._encoding = encoding

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return self._encoding

    def isatty(self) -> bool:
        return True


PLAIN = T.detect(io.StringIO(), env={})
RICH = T.detect(FakeTTY(), env={"TERM": "xterm-256color"})

SAMPLE = """# sample art
# source: a test fixture
[grid banner 4x4]
0123
4567
89ab
cdef
[end]
[caption:full]
S K A L D I R
[end]
[caption:short]
SKALDIR
[end]
"""


@pytest.fixture
def sample_art(tmp_path, monkeypatch):
    path = tmp_path / "cli-logo.txt"
    path.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setenv("GODSV_CLI_ART", str(path))
    monkeypatch.setattr(logo, "_CACHE", {})
    return path


# --------------------------------------------------------------------------- #
# loading                                                                      #
# --------------------------------------------------------------------------- #

def test_the_checked_in_art_is_found_from_the_repo():
    found = logo.art_path()
    assert found is not None and found.name == logo.ART_FILENAME
    art = logo.load_art()
    assert "banner" in art.grids and "mark" in art.grids
    assert art.source, "the art file must record where it came from"


def test_the_environment_can_point_at_another_art_file(sample_art):
    art = logo.load_art()
    assert art.path == sample_art
    assert art.grid("banner").cells[0] == (0, 1, 2, 3)
    assert art.caption("full") == ["S K A L D I R"]


def test_load_art_is_cached(sample_art):
    first = logo.load_art()
    second = logo.load_art()
    assert first is second


def test_a_missing_art_file_yields_empty_art_and_no_crash(monkeypatch):
    monkeypatch.setattr(logo, "art_candidates", lambda: [])
    assert logo.art_path() is None
    with pytest.raises(logo.LogoArtError):
        logo.load_art()
    assert logo.banner(theme=PLAIN) == ""
    assert logo.mark(theme=PLAIN) == ""
    assert logo.header("verify", theme=PLAIN) == "SKALDIR - verify"


def test_a_broken_art_file_degrades_to_empty_art(monkeypatch, tmp_path):
    path = tmp_path / "cli-logo.txt"
    path.write_text("[grid banner 4x4]\n0000\n[end]\n", encoding="utf-8")
    monkeypatch.setenv("GODSV_CLI_ART", str(path))
    monkeypatch.setattr(logo, "_CACHE", {})
    assert logo.banner(theme=PLAIN) == ""
    assert logo.splash(theme=PLAIN, version="1.0").strip().startswith("SKALDIR")


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("[grid banner 4x4]\n0000\n1111\n[end]\n", "rows"),
        ("[grid banner 4x4]\n0000\n1111\n2222\nzzzz\n[end]\n", "hex"),
        ("[grid banner 4x4]\n0000\n", "unterminated"),
        ("[grid banner four]\n0000\n[end]\n", "header"),
        ("no grids here\n", "no grids"),
    ],
)
def test_malformed_art_is_refused_with_a_reason(text, reason):
    with pytest.raises(logo.LogoArtError):
        logo.parse_art(text)


def test_grid_levels_invalid_hex_fail_loudly():
    with pytest.raises(logo.LogoArtError):
        logo.parse_art("[grid banner 4x1]\n00q0\n[end]\n")


# --------------------------------------------------------------------------- #
# rendering                                                                    #
# --------------------------------------------------------------------------- #

def _shaded(levels) -> str:
    ramp = T.SHADES
    return "".join(ramp[level * (len(ramp) - 1) // 15] for level in levels)


def test_a_grid_renders_its_shade_ramp(sample_art):
    grid = logo.load_art().grid("banner")
    drawn = logo.render_grid(grid, theme=PLAIN)
    assert drawn.splitlines()[0] == _shaded((0, 1, 2, 3)).rstrip()
    assert drawn.splitlines()[-1] == _shaded((12, 13, 14, 15))


def test_blank_edges_are_trimmed(sample_art):
    grid = logo.Grid(name="t", columns=2, rows=3, cells=((0, 0), (0, 15), (0, 0)))
    drawn = logo.render_grid(grid, theme=PLAIN)
    assert drawn.strip() == "@" and drawn.startswith(" ")


def test_rich_and_plain_render_the_same_picture(sample_art):
    import re

    grid = logo.load_art().grid("banner")
    plain = logo.render_grid(grid, theme=PLAIN)
    rich = logo.render_grid(grid, theme=RICH)
    assert "\x1b" not in plain and plain.isascii()
    assert "\x1b" in rich
    assert plain.splitlines() == [
        re.sub(r"\x1b\[[0-9;]*m", "", line).rstrip() for line in rich.splitlines()
    ]


def test_the_real_banner_and_mark_render_without_colour():
    banner = logo.banner(theme=PLAIN)
    mark = logo.mark(theme=PLAIN)
    assert banner and mark
    assert banner.isascii() and "\x1b" not in banner
    assert mark.isascii() and "\x1b" not in mark
    assert banner.count("\n") > mark.count("\n")     # the mark is the compact one
    assert all(line.rstrip() == line for line in banner.splitlines())


def test_the_real_banner_and_mark_carry_greys_when_colour_is_on():
    banner = logo.banner(theme=RICH)
    assert "\x1b[38;5;" in banner or "\x1b[38;2;" in banner
    assert "█" not in banner            # the art is shaded with the ASCII ramp, not blocks


def test_header_and_splash_read_as_one_signature():
    assert logo.header("verify", subtitle="real-world verification", theme=PLAIN) == (
        "SKALDIR - verify - real-world verification"
    )
    assert logo.header("verify", theme=RICH) == (
        "\x1b[38;5;231;1mSKALDIR\x1b[0m · \x1b[1mverify\x1b[0m"
    )
    splash = logo.splash(theme=PLAIN, version="1.5.0", subtitle="self-learning agent runtime")
    assert "S K A L D I R" in splash
    assert splash.rstrip().endswith("self-learning agent runtime - v1.5.0")
    assert splash.isascii()
    rich_tail = logo.splash(theme=RICH, version="1.5.0").rstrip().splitlines()[-1]
    assert "\x1b[" in rich_tail and "self-learning agent runtime · v1.5.0" in rich_tail


def test_caption_falls_back_when_the_art_is_missing(monkeypatch):
    monkeypatch.setattr(logo, "art_candidates", lambda: [])
    assert logo.caption("short", theme=PLAIN, default="SKALDIR") == "SKALDIR"
    assert logo.caption("nope", theme=PLAIN, default="x") == "x"


def test_the_generator_preview_detects_the_stream(monkeypatch):
    """`python -m skaldir.logo show` is human-facing: it must style on a TTY and not on a pipe."""
    fake = FakeTTY()
    monkeypatch.setattr(logo.sys, "stdout", fake)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    assert logo.main(["show", "mark"]) == 0
    assert "\x1b[38;2;255;255;255m" in fake.getvalue()

    plain = FakeTTY()
    monkeypatch.setattr(logo.sys, "stdout", plain)
    assert logo.main(["show", "mark", "--no-color"]) == 0
    assert "\x1b" not in plain.getvalue() and plain.getvalue().isascii()


def test_grid_invariants():
    grid = logo.Grid(name="g", columns=2, rows=2, cells=((1, 2), (3, 4)))
    assert grid.level(1, 0) == 2
    assert grid.ink == 10
    assert grid.as_text() == "12\n34"
