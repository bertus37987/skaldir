"""Window-level capture through the X server: python-xlib ``GetImage``.

Why this module exists (measured 2026-09-30, feature ``cu-screenshot-fallback``):
every compositor-level screenshot tool is broken on this host — ImageMagick's
``import`` (7.1.2-13) rejects every invocation with "missing an image filename",
``xwd`` is not installed, ``gnome-screenshot`` falls back to X11 and records nothing,
and ``ffmpeg x11grab`` sees only the empty Xwayland root (on Wayland the root does
not contain composited client content). That left the product's own evidence path,
``HostSessionRunner.screenshot()``, without a working backend. Reading a window's
pixels through the X server is the honest fallback: it proves the window is mapped
AND what it draws. The proven reference this module follows is
``logs/input-live/capture_window.py`` (input-live, 2026-09-30).

Scope guard: this module captures exactly the window it is told to capture — one
integer id (parsed before any X call, never a title pattern and never "whatever is
focused") or the root window of an owned display. Closing or touching windows is
not this module's business; the guard test
``test_no_run_closes_a_window_by_title_or_process`` stays authoritative for that.
"""

from __future__ import annotations

import re
import sys

from .computer_use import ScreenImage

__all__ = ["XlibCaptureError", "capture_window_image", "xlib_available"]

#: A window id is ONE integer: decimal (``xdotool`` prints decimal) or ``0x``-hex.
_WINDOW_ID_RE = re.compile(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)")


class XlibCaptureError(RuntimeError):
    """A window capture through the X server failed; the message names the reason."""


def xlib_available() -> bool:
    """Can the X-server capture be attempted at all (python-xlib importable)?"""
    try:
        import Xlib.display  # noqa: F401 — the import itself is the measurement
    except Exception:  # noqa: BLE001 — an import failure is exactly the answer "no"
        return False
    return True


def capture_window_image(display: str, window_id: str = "") -> ScreenImage:
    """Read one X window's pixels (``GetImage``) and decode them into RGB rows.

    ``window_id``: decimal or ``0x``-hex id; empty means the display's root window
    (meaningful on an owned Xvfb — on Wayland the root is the empty Xwayland
    surface, measured). Anything that is not one integer is refused before the X
    connection is opened: a hostile id is an argument error, never a capture.

    Raises :class:`XlibCaptureError` with the exact reason on every failure —
    the caller names the problem instead of inventing a capture.

    Byte order: implemented for little-endian hosts (measured hosts are x86);
    a big-endian server is refused rather than decoded into wrong colours.
    """
    if sys.byteorder != "little":
        raise XlibCaptureError(
            "the X-server capture is only implemented for little-endian hosts"
        )
    text = str(window_id or "").strip()
    if text and not _WINDOW_ID_RE.fullmatch(text):
        raise XlibCaptureError(
            f"not a window id: {text[:40]!r} — a capture is one integer window id, "
            "never a title pattern and never a shell fragment"
        )
    wid = None if not text else (
        int(text, 16) if text[:2].lower() == "0x" else int(text, 10)
    )
    try:
        from Xlib import X
        from Xlib import display as xdisplay
    except Exception as exc:  # noqa: BLE001 — a missing python-xlib is a named case
        raise XlibCaptureError(
            f"python-xlib is not importable: {type(exc).__name__}: {exc}"
        ) from exc
    try:
        conn = xdisplay.Display(display)
    except Exception as exc:  # noqa: BLE001 — a dead display is a named reason
        raise XlibCaptureError(
            f"the X display {display!r} did not answer: {type(exc).__name__}: {exc}"
        ) from exc
    try:
        if wid is None:
            window = conn.screen().root
        else:
            window = conn.create_resource_object("window", wid)
        geom = window.get_geometry()
        if not geom.width or not geom.height:
            raise XlibCaptureError(
                f"the capture target is empty ({geom.width}x{geom.height})"
            )
        raw = window.get_image(0, 0, geom.width, geom.height, X.ZPixmap, 0xffffffff)
        return _decode_zpixmap(raw.data, geom.width, geom.height, geom.depth, conn, window)
    except XlibCaptureError:
        raise
    except Exception as exc:  # noqa: BLE001 — a failed capture is named, never leaked
        raise XlibCaptureError(
            f"the capture of window {window_id or 'root'} on {display} failed: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — the capture's answer matters, not teardown
            pass


def _decode_zpixmap(
    data: bytes, width: int, height: int, depth: int, conn, window
) -> ScreenImage:
    """Raw ZPixmap bytes of one window → RGB rows (the layout of this host's servers).

    The channel order is *read out of the visual's masks*, not assumed: for the
    measured servers (Xvfb, Xwayland, TrueColor) the masks are red ``0xFF0000``,
    green ``0xFF00``, blue ``0xFF``, which little-endian memory stores byte-wise as
    B, G, R, pad. A layout the decoder cannot map byte-aligned falls through to the
    per-pixel path instead of producing a silently wrong-coloured image.
    """
    if width <= 0 or height <= 0:
        raise XlibCaptureError(f"the capture target is empty ({width}x{height})")
    size = len(data)
    if size == width * height * 4:
        bytes_pp = 4
    elif size == width * height * 3:
        bytes_pp = 3
    else:
        raise XlibCaptureError(
            f"the server returned {size} bytes for a {width}x{height} depth-{depth} "
            "window — no ZPixmap layout this decoder knows"
        )
    rmask, gmask, bmask = _visual_masks(conn, window)
    if rmask is None:  # masks unreadable: assume the standard TrueColor layout
        rmask, gmask, bmask = 0xFF0000, 0xFF00, 0xFF
    red = _channel_byte(rmask, bytes_pp)
    green = _channel_byte(gmask, bytes_pp)
    blue = _channel_byte(bmask, bytes_pp)
    out = bytearray(width * height * 3)
    if red is not None and green is not None and blue is not None:
        out[0::3] = data[red::bytes_pp]
        out[1::3] = data[green::bytes_pp]
        out[2::3] = data[blue::bytes_pp]
    else:  # a non byte-aligned mask (e.g. 5-6-5): the honest per-pixel path
        for y in range(height):
            row_base = y * width * bytes_pp
            row = bytearray()
            for x in range(width):
                base = row_base + x * bytes_pp
                pixel = int.from_bytes(data[base : base + bytes_pp], "little")
                row += bytes((
                    _scaled(pixel & rmask, rmask),
                    _scaled(pixel & gmask, gmask),
                    _scaled(pixel & bmask, bmask),
                ))
            out[row_base * 3 // bytes_pp : (row_base + width * bytes_pp) * 3 // bytes_pp] = row
    rows = tuple(bytes(out[y * width * 3 : (y + 1) * width * 3]) for y in range(height))
    return ScreenImage(width=width, height=height, rows=rows)


def _visual_masks(conn, window) -> tuple[int, int, int] | None:
    """The (red, green, blue) masks of the window's visual — or None if unknown."""
    try:
        visual_id = window.get_attributes().visual
        for depth in conn.screen().allowed_depths:
            for visual in depth.visuals:
                if visual.visual_id == visual_id:
                    return visual.red_mask, visual.green_mask, visual.blue_mask
    except Exception:  # noqa: BLE001 — without masks the standard layout is assumed
        return None
    return None


def _channel_byte(mask: int, bytes_pp: int) -> int | None:
    """The little-endian byte offset of an 8-bit-aligned channel mask — or None."""
    mask_width = mask.bit_length()
    if mask_width != 8:
        return None
    shift = 0
    value = mask
    while value and not (value & 1):
        value >>= 1
        shift += 1
    byte = shift // 8
    if shift % 8 or byte >= bytes_pp:
        return None
    return byte


def _scaled(value: int, mask: int) -> int:
    """Scale a masked channel to full 8-bit range (same rule as ``decode_xwd``)."""
    width = mask.bit_length()
    return value * 255 // ((1 << width) - 1) if width else 0
