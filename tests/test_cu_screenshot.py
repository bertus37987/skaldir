"""The screenshot capability: XWD → PNG, decoded by the application itself (GAP-K2).

Evidence class: unit (synthetic XWD) plus **real execution** (the container image's own
``xwd``) in the one test that needs the image.

Why this file exists: the container GUI image ships ``xwd`` and no converter, no ImageMagick
and no Pillow, and the audit's finding was that "pixel evidence is not producible in-container
today" — while ``verify flow`` promises exactly that evidence. The fix is app-level: the XWD
dump travels out as base64 and :func:`skaldir.computer_use.decode_xwd` turns it into RGB rows
with the standard library, so no dependency is added on either side of the container boundary.

The synthetic cases pin the decoder against the *format*, not against one lucky capture:
both byte orders, four bit depths, and the refusals (a non-ZPixmap dump is named as
unsupported instead of being rendered as a wrong picture).
"""

from __future__ import annotations

import hashlib
import shutil
import struct
import subprocess
import time
import zlib

import pytest

from skaldir.computer_use import (
    SCREENSHOT_MIN_BYTES,
    ContainerRunner,
    ScreenshotError,
    decode_xwd,
    encode_png,
    safe_label,
    xwd_to_png,
)

PODMAN = shutil.which("podman")
GUI_IMAGE = ContainerRunner.DEFAULT_IMAGE


def _image_present(image: str) -> bool:
    if not PODMAN:
        return False
    return (
        subprocess.run(  # nosec B603 — fixed argv list, no shell  # noqa: S603
            [PODMAN, "image", "exists", image], capture_output=True
        ).returncode
        == 0
    )


# --------------------------------------------------------------------------- #
# synthetic XWD dumps — the decoder is pinned against the format                #
# --------------------------------------------------------------------------- #


def build_xwd(
    pixels: list[list[tuple[int, int, int]]],
    *,
    order: str = ">",
    bits_per_pixel: int = 32,
    pixmap_format: int = 2,
    ncolors: int = 0,
) -> bytes:
    """Build a real XWD file for the given pixel matrix (little- or big-endian header)."""
    height = len(pixels)
    width = len(pixels[0]) if height else 0
    bpl = width * (bits_per_pixel // 8)
    masks = {32: (0x00FF0000, 0x0000FF00, 0x000000FF), 16: (0xF800, 0x07E0, 0x001F)}.get(
        bits_per_pixel, (0, 0, 0)
    )
    header_size = 100 + len(b"probe-window") + 1
    header = struct.pack(
        order + "25I",
        header_size,
        7,
        pixmap_format,
        24,
        width,
        height,
        0,
        0 if order == "<" else 1,
        32,
        0,
        32,
        bits_per_pixel,
        bpl,
        4,
        masks[0],
        masks[1],
        masks[2],
        8,
        0,
        ncolors,
        width,
        height,
        0,
        0,
        0,
    )
    body = b"probe-window\x00"
    body += b"\x00" * (header_size - 100 - len(body))
    table = b""
    for index in range(ncolors):
        table += struct.pack(order + "IHHHBB", index, index << 8, 0, 0, 0, 0)
    data = b""
    for row in pixels:
        for red, green, blue in row:
            if bits_per_pixel == 32:
                value = (red << 16) | (green << 8) | blue
                data += struct.pack(order + "I", value)
            elif bits_per_pixel == 24:
                data += (
                    bytes((blue, green, red)) if order == "<" else bytes((red, green, blue))
                )
            elif bits_per_pixel == 16:
                data += struct.pack(
                    order + "H", ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
                )
            else:
                data += bytes((red,))
    return header + body + table + data


PIXELS = [
    [(255, 0, 0), (0, 255, 0)],
    [(0, 0, 255), (255, 255, 255)],
]


@pytest.mark.parametrize("order", ["<", ">"])
def test_decode_xwd_reads_both_byte_orders(order: str) -> None:
    """The header's integers follow the file's byte order — measured, not assumed.

    ``xwd`` on this host writes big-endian headers; a decoder that only ever tried
    little-endian answered "unsupported XWD version 117440512" for a perfectly good capture
    (observed live before this test existed).
    """
    image = decode_xwd(build_xwd(PIXELS, order=order))
    assert (image.width, image.height) == (2, 2)
    assert image.rows[0] == bytes((255, 0, 0, 0, 255, 0))
    assert image.rows[1] == bytes((0, 0, 255, 255, 255, 255))


@pytest.mark.parametrize("bits", [32, 24, 16])
def test_decode_xwd_handles_the_real_bit_depths(bits: int) -> None:
    image = decode_xwd(build_xwd(PIXELS, bits_per_pixel=bits))
    assert (image.width, image.height) == (2, 2)
    # 16-bit packs to 5/6/5, so the channel values are the quantised ones.
    assert image.rows[0][:3] == pytest.approx(bytes((255, 0, 0)), abs=8)


def test_decode_xwd_uses_a_colour_table_for_8bpp() -> None:
    data = build_xwd(PIXELS, bits_per_pixel=8, ncolors=2)
    # rewrite the pixel bytes to the two table indices
    header_size = struct.unpack_from(">I", data, 0)[0]
    body = data[:header_size] + data[header_size : header_size + 2 * 12]
    body += bytes((0, 1, 1, 0))
    image = decode_xwd(body)
    assert (image.width, image.height) == (2, 2)


def test_decode_xwd_refuses_a_format_it_cannot_render() -> None:
    with pytest.raises(ScreenshotError) as excinfo:
        decode_xwd(build_xwd(PIXELS, pixmap_format=0))
    assert "unsupported XWD pixmap format" in str(excinfo.value)


def test_decode_xwd_refuses_a_truncated_dump_and_a_short_buffer() -> None:
    with pytest.raises(ScreenshotError) as short:
        decode_xwd(b"\x00" * 8)
    assert "shorter than an XWD header" in str(short.value)
    full = build_xwd(PIXELS)
    with pytest.raises(ScreenshotError) as cut:
        decode_xwd(full[: len(full) - 4])
    assert "truncated" in str(cut.value)


def test_encode_png_is_a_real_png_with_a_stable_hash() -> None:
    image = decode_xwd(build_xwd(PIXELS))
    png = encode_png(image)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack_from(">II", png, 16)
    assert (width, height) == (2, 2)
    # the payload must be the zlib stream of (filter byte + RGB row) per scanline
    raw = b"\x00" + image.rows[0] + b"\x00" + image.rows[1]
    assert zlib.decompress(png[41:]) == raw
    assert hashlib.sha256(png).hexdigest() == hashlib.sha256(encode_png(image)).hexdigest()


def test_xwd_to_png_round_trip() -> None:
    png, image = xwd_to_png(build_xwd(PIXELS))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert (image.width, image.height) == (2, 2)


def test_safe_label_is_a_filename_fragment_never_a_path() -> None:
    assert safe_label("CU-CHAIN ../../etc/passwd") == "cu-chain-etc-passwd"
    assert safe_label("") == "screen"
    assert safe_label("x" * 200) == "x" * 60


# --------------------------------------------------------------------------- #
# the real capture: the image's own xwd, decoded by the application             #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not _image_present(GUI_IMAGE), reason="the godsv-cu-x11 image is missing")
def test_container_runner_captures_a_real_png_named_after_the_window(tmp_path) -> None:
    """Real execution: a window on the isolated display, captured and hashed.

    This is the acceptance the audit asked for — a non-empty PNG with a sha256 from inside
    the image — plus the evidence naming rule: the file is named after the window the run
    observed, so a reviewer can tell what was on screen from the name alone.
    """
    from skaldir.computer_use import Action

    runner = ContainerRunner(gui=True, name=f"godsv-cu-shot-{int(time.time()) % 1000000}")
    try:
        ok, detail = runner.start()
        assert ok, detail
        available, why = runner.screenshot_available()
        assert available, why
        title = f"godsv-cu-shot-{time.time_ns() % 100000}"
        result = runner.act(
            Action(
                kind="cli",
                argv=[
                    "sh",
                    "-c",
                    f"(xmessage -name {title} 'shot' >/dev/null 2>&1 &); sleep 2; echo SHOWN",
                ],
            )
        )
        assert result.ok, result.detail
        target = tmp_path / f"{title}.png"
        capture, reason = runner.screenshot(target, label=title)
        assert capture is not None, reason
        assert capture.width == 800 and capture.height == 600
        assert capture.size_bytes >= SCREENSHOT_MIN_BYTES
        assert capture.converter == "builtin-xwd"
        assert capture.label == safe_label(title)
        assert target.is_file()
        assert hashlib.sha256(target.read_bytes()).hexdigest() == capture.sha256
    finally:
        runner.close()
    assert runner.cleanup_record()["still_present"] is False


@pytest.mark.skipif(not _image_present(GUI_IMAGE), reason="the godsv-cu-x11 image is missing")
def test_capture_of_a_window_id_is_scoped_to_that_window(tmp_path) -> None:
    """A window-scoped capture is smaller than the whole 800x600 display."""
    from skaldir.computer_use import Action

    runner = ContainerRunner(gui=True, name=f"godsv-cu-shot2-{int(time.time()) % 1000000}")
    try:
        ok, _detail = runner.start()
        assert ok
        title = f"godsv-cu-win-{time.time_ns() % 100000}"
        shown = runner.act(
            Action(
                kind="cli",
                argv=["sh", "-c", f"(xmessage -name {title} 'scoped' >/dev/null 2>&1 &); sleep 2"],
            )
        )
        assert shown.ok, shown.detail
        found = runner.act(
            Action(kind="cli", argv=["xdotool", "search", "--name", title])
        )
        # `xdotool search` answers a decimal window id on this host (measured: 2097179),
        # which is what the runner's own window parser has to accept.
        window_id = found.stdout.strip().splitlines()[-1]
        assert window_id.isdigit() or window_id.startswith("0x")
        capture, reason = runner.screenshot(
            tmp_path / "window.png", window_id=window_id, label=title
        )
        assert capture is not None, reason
        assert capture.scope == f"window {window_id}"
        assert capture.width < 800
    finally:
        runner.close()


def test_a_runner_without_a_display_refuses_a_capture() -> None:
    """The base runner answers honestly instead of writing a stand-in image."""
    from skaldir.computer_use import DryRunRunner

    capture, reason = DryRunRunner().screenshot()
    assert capture is None
    assert "no display" in reason or "drives no display" in reason
