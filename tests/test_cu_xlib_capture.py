"""The host capture fallback: reading a window's pixels through the X server.

Evidence class: unit on an owned Xvfb (a display this test starts and stops itself)
plus one live proof on the real ``:0`` display — both with an own window.

Why this file exists (measured 2026-09-30, ``docs/audit/ABNAHME-EINGABE-LIVE.md``):
every compositor-level screenshot tool is broken on this host — ImageMagick's
``import`` (7.1.2-13) rejects every invocation with "missing an image filename",
``xwd`` is not installed, ``gnome-screenshot`` falls back to X11 and records nothing,
and ``ffmpeg x11grab`` sees only the empty Xwayland root. That left the product's own
evidence path, ``HostSessionRunner.screenshot()`` (xwd → import fallback chain),
unable to produce pixel evidence at all. The fallback reads the window's pixels
through the X server (python-xlib ``GetImage``), the proven pattern of
``logs/input-live/capture_window.py``: it proves the window is mapped AND what it
draws.

Marker discipline: every window below is one this test created, recorded by window
id, and destroyed by that id. A foreign window is never captured, focused, or
closed — the guard ``test_no_run_closes_a_window_by_title_or_process`` stays
authoritative for that rule.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess  # nosec B404 — fixed argv lists, no shell
import time
import zlib
from pathlib import Path

import pytest

from skaldir.computer_use import ScreenCapture, ScreenImage, encode_png, png_size
from skaldir.cu_host_session import HostSessionRunner
from skaldir.xlib_capture import XlibCaptureError, capture_window_image

HAVE_XVFB = bool(shutil.which("Xvfb") and shutil.which("xdotool"))
needs_display = pytest.mark.skipif(not HAVE_XVFB, reason="Xvfb/xdotool are not installed")
needs_live_display = pytest.mark.skipif(
    not os.environ.get("DISPLAY"), reason="no X display in this environment"
)

WIDTH, HEIGHT = 120, 80
BLUE_X, BLUE_Y = WIDTH // 2 - 10, HEIGHT // 2 - 10  # the blue marker square's origin


class _OwnWindow:
    """An X window this test created: a unique title, a red field, a blue marker.

    The window content is the marker: solid red everywhere except a 20x20 blue square
    in the middle. The window id is recorded; ``close()`` destroys exactly that id.
    """

    def __init__(self, display_str: str, title: str) -> None:
        from Xlib import X
        from Xlib import display as xdisplay

        self.conn = xdisplay.Display(display_str)
        screen = self.conn.screen()
        self.window = screen.root.create_window(
            20, 20, WIDTH, HEIGHT, 1, screen.root_depth,
            window_class=X.InputOutput, event_mask=X.ExposureMask,
        )
        self.window.set_wm_name(title)
        self.window.map()
        self.conn.sync()
        visual_id = self.window.get_attributes().visual
        self.visual = next(
            v for d in screen.allowed_depths for v in d.visuals if v.visual_id == visual_id
        )
        # Draw AFTER the window is mapped and the window manager has settled with it:
        # a reparenting WM (openbox on the owned Xvfb, mutter on :0) unmaps and remaps
        # the client window while managing it, and content drawn before that settle is
        # discarded (measured 2026-09-30: the marker read back black without this wait).
        for _ in range(40):
            if self.window.get_attributes().map_state == 2:
                break
            time.sleep(0.05)
        time.sleep(0.2)
        gc = self.window.create_gc()
        gc.change(foreground=self.visual.red_mask)
        self.window.fill_rectangle(gc, 0, 0, WIDTH, HEIGHT)
        gc.change(foreground=self.visual.blue_mask)
        self.window.fill_rectangle(gc, BLUE_X, BLUE_Y, 20, 20)
        self.conn.sync()

    @property
    def wid(self) -> str:
        return str(self.window.id)

    def close(self) -> None:
        try:
            self.window.destroy()
            self.conn.flush()
            self.conn.close()
        except Exception:  # noqa: BLE001 — teardown never masks the test's answer
            pass


def _png_pixel(path: Path, x: int, y: int) -> tuple[int, int, int]:
    """One RGB pixel of our own filter-0 PNG, decoded with the standard library."""
    data = path.read_bytes()
    width, _height = png_size(data)
    pos = 8
    idat = b""
    while pos < len(data):
        (length,) = struct.unpack_from(">I", data, pos)
        tag = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        if tag == b"IDAT":
            idat += payload
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 3
    row = y * (stride + 1)
    assert raw[row] == 0, "our encoder writes filter type 0 only"
    base = row + 1 + x * 3
    return raw[base], raw[base + 1], raw[base + 2]


def _is_red(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    return r > 200 and g < 60 and b < 60


def _is_blue(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    return b > 200 and r < 60 and g < 60


# --------------------------------------------------------------------------- #
# the module: one window's pixels through the X server                          #
# --------------------------------------------------------------------------- #


@needs_display
def test_capture_window_image_decodes_the_own_marker_window() -> None:
    """Direct module contract: real dimensions, real pixels, both markers found."""
    runner = HostSessionRunner(timeout=60.0)
    own = None
    try:
        ok, detail = runner.start()
        assert ok, detail
        own = _OwnWindow(runner.display, f"skaldir-shot-{time.time_ns() % 1000000}")
        image = capture_window_image(runner.display, own.wid)
        assert (image.width, image.height) == (WIDTH, HEIGHT)
        assert _is_red(tuple(image.rows[5][5 * 3 : 5 * 3 + 3]))
        assert _is_blue(tuple(image.rows[HEIGHT // 2][60 * 3 : 60 * 3 + 3]))
    finally:
        if own is not None:
            own.close()
        runner.close()


def test_a_hostile_window_id_is_refused_before_any_x_call() -> None:
    """``12; touch ignored`` is an argument error, never a capture or a shell input."""
    with pytest.raises(XlibCaptureError) as excinfo:
        capture_window_image(":0", "12; touch ignored")
    assert "12; touch ignored" in str(excinfo.value)
    assert "window id" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# the runner: the productive screenshot path uses the fallback                  #
# --------------------------------------------------------------------------- #


@needs_display
def test_runner_screenshot_captures_the_own_marker_window(tmp_path) -> None:
    """``runner.screenshot()`` returns a real PNG of the own window: dims + marker."""
    runner = HostSessionRunner(timeout=60.0)
    own = None
    try:
        ok, detail = runner.start()
        assert ok, detail
        own = _OwnWindow(runner.display, f"skaldir-shot-{time.time_ns() % 1000000}")
        target = tmp_path / "own-window.png"
        capture, why = runner.screenshot(target, window_id=own.wid, label="own-marker")
        assert capture is not None, why
        assert isinstance(capture, ScreenCapture)
        assert (capture.width, capture.height) == (WIDTH, HEIGHT)
        assert capture.scope == f"window {own.wid}"
        assert target.is_file()
        assert capture.size_bytes == target.stat().st_size
        assert _is_red(_png_pixel(target, 5, 5))
        assert _is_blue(_png_pixel(target, WIDTH // 2, HEIGHT // 2))
    finally:
        if own is not None:
            own.close()
        runner.close()


@needs_display
def test_a_window_id_that_is_not_there_is_named_not_invented(tmp_path) -> None:
    """A capture of a window that is not on the display fails with the reason."""
    runner = HostSessionRunner(timeout=60.0)
    try:
        ok, detail = runner.start()
        assert ok, detail
        target = tmp_path / "missing.png"
        capture, why = runner.screenshot(target, window_id="999999999", label="missing")
        assert capture is None
        assert "999999999" in why, why
        assert not target.exists(), "a failed capture never writes a stand-in file"
    finally:
        runner.close()


@needs_live_display
def test_live_proof_the_real_display_captures_the_own_window(tmp_path) -> None:
    """Live: an own window on the real ``:0`` display, captured by the live runner.

    The target is the window id, not the title: mutter gives the frame window the
    client's name too, so a title matches 2 windows here (measured) — and the runner
    rightly refuses a target that is not exactly one window.
    """
    own = _OwnWindow(os.environ["DISPLAY"], f"skaldir-live-shot-{time.time_ns() % 1000000}")
    try:
        runner = HostSessionRunner(
            attach_to_session=True,
            target=own.wid,
            allow_live_desktop=True,
            timeout=60.0,
        )
        target = tmp_path / "live-own-window.png"
        capture, why = runner.screenshot(target)
        assert capture is not None, why
        assert (capture.width, capture.height) == (WIDTH, HEIGHT)
        assert capture.scope == f"window {own.wid}"
        assert _is_red(_png_pixel(target, 5, 5))
        assert _is_blue(_png_pixel(target, WIDTH // 2, HEIGHT // 2))
    finally:
        own.close()


@needs_display
def test_the_tool_fallback_chain_survives_without_the_x_server_path(
    tmp_path, monkeypatch
) -> None:
    """python-xlib gone → the old chain (import) still produces the capture.

    The import call is the process boundary; it is stood in by a stub that writes a
    real PNG — the *converter* path under test, not the PNG encoding itself.
    """

    def _no_xlib(display, window_id=""):
        raise ImportError("python-xlib missing (fixture)")

    monkeypatch.setattr("skaldir.cu_host_session.capture_window_image", _no_xlib)
    stub_png = encode_png(ScreenImage(
        width=2, height=2, rows=(b"\xff\x00\x00\x00\xff\x00", b"\x00\x00\xff\xff\xff\xff")
    ))
    real_run = subprocess.run

    def fake_import(argv, **kwargs):
        if argv and Path(argv[0]).name == "import":
            Path(argv[-1]).write_bytes(stub_png)
            return subprocess.CompletedProcess(argv, 0, "", "")
        return real_run(argv, **kwargs)

    monkeypatch.setattr("skaldir.cu_host_session.subprocess.run", fake_import)
    runner = HostSessionRunner(timeout=60.0)
    try:
        ok, detail = runner.start()
        assert ok, detail
        capture, why = runner.screenshot(tmp_path / "import-fallback.png", label="fallback")
        assert capture is not None, why
        assert capture.converter == "host-import"
    finally:
        runner.close()
