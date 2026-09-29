"""Computer Use core (CORE-PLAN §5): isolated runners, channel ordering, verified actions.

Hard rules (enforced in code, not by convention):

- **Isolation is mandatory.** Actions never run on the user's live desktop:
  ``CUPolicy.require_isolation`` rejects any runner whose isolation is
  ``"none"``. Provided runners: dry-run (tests/demo), sandbox (bwrap:
  network namespace off, root read-only) and container (rootless podman,
  network off, disposable).
- **Channel order: API > DOM > Vision.** Attempts are stably ordered by
  channel — cheapest and most verifiable first; a failed verification falls
  through to the next attempt.
- **Verification after every action.** An attempt without an expectation
  (stdout or observation) is refused. Every attempt, action, verification,
  failure and abort is recorded as an event (audit trail).
- **Bounded runs.** ``max_actions``/``max_fallbacks`` stop runaway loops.

GUI runs need a display; the container path uses the local image
``localhost/godsv-cu-x11`` (Xvfb + xdotool + xmessage) so the full
mouse/keyboard loop stays inside an isolated environment. **Seeing** the display is part
of that loop: the image ships ``xwd`` (the X11 screen-dump tool) and nothing else, so the
capture travels out as XWD and is decoded into a real PNG by
:func:`xwd_to_png` — pure stdlib, no ImageMagick and no Pillow, which means the screenshot
capability needs no new dependency on either side (GAP-K2).

The live desktop is still refused by policy. ``CUPolicy.require_isolation`` defaults to
``True`` and any runner whose ``isolation`` is ``"none"`` is rejected; a host-session
runner (an Xvfb **this run owns**, or — only with an explicit, target-scoped opt-in — a
named window of the user's own session) lives in :mod:`skaldir.cu_host_session`, and the
policy refuses it unless the caller both opts in and names a target.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import struct
import subprocess  # nosec B404 — fixed argv lists for podman/bwrap, no shell
import sys
import time
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from .events import EventStore
from .gates import _run as _sandbox_run  # same-package subprocess + bwrap helper
from .gates import _sandbox_available
from .llm import BudgetTracker, LLMClient
from .promptrun import run_prompt
from .prompts import PromptRegistry
from .system_one_guistate import GROUND_TRUTH_APP_EFFECT, GUISTATE_SCHEMA_VERSION

API = "api"
DOM = "dom"
VISION = "vision"
CHANNEL_ORDER = (API, DOM, VISION)

GUI_KINDS = frozenset({"mouse", "key", "type_text", "dom_click", "dom_fill", "drag"})
#: ``scroll``/``window`` stay *translated* kinds (they become mouse/cli) — the
#: policy vocabulary is the core's plus ``drag``/``launch`` (deep GUI + app start).
ALL_KINDS = frozenset({"cli", "api_call", "launch"}) | GUI_KINDS


class PolicyViolation(RuntimeError):
    """Raised when a policy would put the live system at risk."""


@dataclass
class Action:
    kind: str
    argv: list[str] = field(default_factory=list)
    payload: dict = field(default_factory=dict)
    description: str = ""


@dataclass
class Verification:
    expect_stdout_contains: str | None = None
    expect_observe_contains: str | None = None
    #: The declared expectation is "the runner executed this action" (result.ok).
    #: Used where stdout is empty (xdotool key/focus) and the real proof is the
    #: end-state check against the observation / app state — never a free pass.
    expect_ok: bool = False
    description: str = ""

    def is_empty(self) -> bool:
        return (
            not self.expect_stdout_contains
            and not self.expect_observe_contains
            and not self.expect_ok
        )


@dataclass
class Attempt:
    channel: str
    action: Action
    verify: Verification


@dataclass
class ActionResult:
    ok: bool
    stdout: str = ""
    detail: str = ""
    exit_code: int | None = None
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False


@dataclass
class Observation:
    text: str = ""
    detail: str = ""


def order_attempts(attempts: list[Attempt]) -> list[Attempt]:
    """Stable order by channel: API > DOM > Vision (unknown channels last)."""
    rank = {channel: index for index, channel in enumerate(CHANNEL_ORDER)}
    return sorted(attempts, key=lambda attempt: rank.get(attempt.channel, len(CHANNEL_ORDER)))


@dataclass
class CUPolicy:
    allowed_kinds: tuple[str, ...] = tuple(sorted(ALL_KINDS))
    max_actions: int = 20
    max_fallbacks: int = 3
    require_isolation: bool = True
    dry_run: bool = False
    #: The **explicit** live-desktop opt-in (GAP-K3). Default ``False``, and the default is
    #: the whole guarantee: nothing in this module reaches the user's session unless a caller
    #: says so here. It is not a bypass — a live-desktop runner is still refused unless it is
    #: *target-scoped* (``live_desktop_target``, a window/process the run itself owns), so the
    #: opt-in can never turn into "drive whatever is on screen".
    allow_live_desktop: bool = False
    #: The window id / application the opt-in is scoped to. Empty means unscoped, and an
    #: unscoped live-desktop run is refused.
    live_desktop_target: str = ""


@dataclass
class CURunResult:
    step: str
    status: str  # verified | aborted | timed_out | cancelled | outcome_unknown
    channel: str | None
    actions: int
    fallbacks: int
    detail: str = ""
    stdout: str = ""
    exit_code: int | None = None
    stderr: str = ""


@runtime_checkable
class Runner(Protocol):
    isolation: str

    @property
    def is_isolated(self) -> bool: ...

    def act(self, action: Action) -> ActionResult: ...

    def observe(self) -> Observation: ...

    def close(self) -> None: ...


class BaseRunner:
    isolation = "none"
    #: The scope a live-desktop opt-in has to match (empty for every isolated runner).
    target_scope = ""

    @property
    def is_isolated(self) -> bool:
        return self.isolation != "none"

    def act(self, action: Action) -> ActionResult:  # pragma: no cover — interface
        raise NotImplementedError

    def observe(self) -> Observation:  # pragma: no cover — interface
        raise NotImplementedError

    def screenshot(
        self, path: str | Path | None = None, *, window_id: str = "", label: str = ""
    ) -> tuple[ScreenCapture | None, str]:
        """Capture the display, or say precisely why this runner cannot (never a stub image)."""
        return None, (
            f"the {self.isolation or 'base'} runner drives no display: nothing to capture"
        )

    def cleanup_record(self) -> dict:
        """What this runner leaves behind after ``close()`` (overridden where it matters)."""
        return {"container": "", "prefix": "", "closed": True, "still_present": False}

    def close(self) -> None:
        pass


class DryRunRunner(BaseRunner):
    """Deterministic runner: scripted observations, no side effects at all."""

    isolation = "dry-run"

    def __init__(
        self,
        observations: list[str] | None = None,
        behaviors: dict[str, ActionResult] | None = None,
    ) -> None:
        self._observations = list(observations or [""])
        self._behaviors = behaviors or {}
        self.actions: list[Action] = []

    def act(self, action: Action) -> ActionResult:
        self.actions.append(action)
        if action.description in self._behaviors:
            return self._behaviors[action.description]
        if action.kind in self._behaviors:
            return self._behaviors[action.kind]
        return ActionResult(ok=True, stdout="dry-run")

    def observe(self) -> Observation:
        if len(self._observations) > 1:
            return Observation(text=self._observations.pop(0))
        return Observation(text=self._observations[0] if self._observations else "")


class LocalSandboxRunner(BaseRunner):
    """CLI actions inside bwrap (network namespace off, root read-only)."""

    isolation = "sandbox"

    def __init__(self, workdir: str | Path, *, timeout: float = 30.0) -> None:
        self.workdir = Path(workdir)
        self.timeout = timeout

    def act(self, action: Action) -> ActionResult:
        if action.kind not in ("cli", "api_call"):
            return ActionResult(
                ok=False, detail=f"Kind '{action.kind}' is not supported by the sandbox runner"
            )
        if not action.argv:
            return ActionResult(ok=False, detail="argv is missing")
        try:
            code, output, _sandboxed = _sandbox_run(
                action.argv, workdir=self.workdir, timeout=self.timeout
            )
            return ActionResult(
                ok=code == 0, stdout=output, detail=output[:200] or f"exit {code}",
                exit_code=code,
            )
        except Exception as exc:  # noqa: BLE001 — distinguish timeout from other failures
            timed_out = "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower()
            return ActionResult(
                ok=False,
                detail=f"{type(exc).__name__}: {exc}",
                timed_out=timed_out,
            )

    def observe(self) -> Observation:
        script = "import os; print('\\n'.join(sorted(os.listdir('.'))))"
        code, output, _sandboxed = _sandbox_run(
            [sys.executable, "-c", script], workdir=self.workdir, timeout=self.timeout
        )
        return Observation(text=output if code == 0 else "", detail=f"exit {code}")


def _run_cmd(cmd: list[str], *, timeout: float) -> tuple[int, str]:
    try:
        proc = subprocess.run(  # nosec B603 — fixed argv list, no shell
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _run_cmd_bytes(cmd: list[str], *, timeout: float) -> tuple[int, bytes, str]:
    """Same as :func:`_run_cmd` but keeps stdout as bytes (a screen dump is binary)."""
    try:
        proc = subprocess.run(  # nosec B603 — fixed argv list, no shell
            cmd, capture_output=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, b"", f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout or b"", (proc.stderr or b"").decode(
        "utf-8", "replace"
    ).strip()


# --------------------------------------------------------------------------- #
# the screen capture: XWD in the image → PNG on the host, pure stdlib           #
# --------------------------------------------------------------------------- #
#
# The container GUI image carries exactly one capture tool, ``xwd`` (measured in
# ``localhost/godsv-cu-x11:latest``: xwd yes, import/scrot/gnome-screenshot/python3 no).
# XWD is a plain, fully documented format, so the conversion needs no ImageMagick, no
# Pillow and no extra package — the screenshot capability is *app-level* (GAP-K2), which
# is what keeps a clean install working and the dependency list honest.

XWD_HEADER_BYTES = 100
XWD_FILE_VERSION = 7
XWD_ZPIXMAP = 2
#: Shown when a capture produced a file we would not call a screenshot.
SCREENSHOT_MIN_BYTES = 64


class ScreenshotError(RuntimeError):
    """A capture that could not be turned into a real image — never a placeholder."""


@dataclass(frozen=True)
class ScreenImage:
    """A decoded image: 8-bit RGB rows plus the size they were read at."""

    width: int
    height: int
    rows: tuple[bytes, ...]

    def as_dict(self) -> dict:
        return {"width": self.width, "height": self.height}


@dataclass(frozen=True)
class ScreenCapture:
    """One real screenshot on disk: where it is, what it shows, and its hash."""

    path: str
    sha256: str
    size_bytes: int
    width: int
    height: int
    #: What the capture was scoped to: ``root`` (the whole display) or a window id.
    scope: str = "root"
    #: The window title / process the shot is named after (evidence naming).
    label: str = ""
    #: How the bytes became a PNG: ``builtin-xwd`` (stdlib) or an external converter.
    converter: str = "builtin-xwd"

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "scope": self.scope,
            "label": self.label,
            "converter": self.converter,
        }


def _u32(data: bytes, offset: int, order: str) -> int:
    return struct.unpack_from(order + "I", data, offset)[0]


def _shift(mask: int) -> int:
    if not mask:
        return 0
    return (mask & -mask).bit_length() - 1


def _scaled(value: int, mask: int) -> int:
    if not mask:
        return 0
    field = (value & mask) >> _shift(mask)
    full = mask >> _shift(mask)
    return int(round(field * 255 / full)) if full else 0


def decode_xwd(data: bytes) -> ScreenImage:
    """Decode an XWD screen dump into RGB rows — or refuse it by name.

    Supports what ``xwd`` actually emits here: ``ZPixmap`` at 8/16/24/32 bits per pixel,
    both byte orders, using the header's own channel masks (never a guessed layout).
    Anything else (``XYBitmap``/``XYPixmap``) is refused with the format named instead of
    being rendered as a wrong picture.
    """
    if len(data) < XWD_HEADER_BYTES:
        raise ScreenshotError(
            f"the capture is {len(data)} bytes — shorter than an XWD header "
            f"({XWD_HEADER_BYTES} bytes), so it is not a screenshot"
        )
    header_size = _u32(data, 0, "<")
    version = _u32(data, 4, "<")
    # The header's own integers are written in the *file's* byte order, which the header
    # declares at offset 28 — so the order has to be established before the rest is read.
    # ``xwd`` on this host writes big-endian headers (`\x00\x00\x00\x6b`), the one field
    # mapping little-endian, so both are tried and the version decides (measured).
    for candidate in ("<", ">"):
        if _u32(data, 4, candidate) == XWD_FILE_VERSION:
            magic_order = candidate
            break
    else:
        raise ScreenshotError(
            f"unsupported XWD version {version} (expected {XWD_FILE_VERSION}); neither byte "
            "order decodes this header"
        )
    header_size = _u32(data, 0, magic_order)
    pixmap_format = _u32(data, 8, magic_order)
    _depth = _u32(data, 12, magic_order)  # read for completeness; decoding needs no depth
    width = _u32(data, 16, magic_order)
    height = _u32(data, 20, magic_order)
    byte_order = _u32(data, 28, magic_order)
    bits_per_pixel = _u32(data, 44, magic_order)
    bytes_per_line = _u32(data, 48, magic_order)
    red_mask = _u32(data, 56, magic_order)
    green_mask = _u32(data, 60, magic_order)
    blue_mask = _u32(data, 64, magic_order)
    ncolors = _u32(data, 76, magic_order)

    if pixmap_format != XWD_ZPIXMAP:
        raise ScreenshotError(
            f"unsupported XWD pixmap format {pixmap_format} "
            f"(only ZPixmap={XWD_ZPIXMAP} is decoded; XYBitmap/XYPixmap are refused)"
        )
    if width <= 0 or height <= 0:
        raise ScreenshotError(f"the capture reports a {width}x{height} image")
    if bits_per_pixel not in (8, 16, 24, 32):
        raise ScreenshotError(f"unsupported bits_per_pixel {bits_per_pixel}")
    if header_size < XWD_HEADER_BYTES or header_size > len(data):
        raise ScreenshotError(f"the capture declares an impossible header size {header_size}")

    order = "<" if byte_order == 0 else ">"
    # XWDColor is 12 bytes: pixel(4) red(2) green(2) blue(2) flags(1) pad(1)
    color_table_start = header_size
    pixels_start = color_table_start + ncolors * 12
    usable = bytes_per_line * height
    if pixels_start + usable > len(data):
        raise ScreenshotError(
            f"the capture is truncated: {len(data)} bytes is less than the "
            f"{pixels_start + usable} its header describes"
        )
    palette: list[tuple[int, int, int]] = []
    for index in range(ncolors):
        base = color_table_start + index * 12
        palette.append(
            (
                struct.unpack_from(order + "H", data, base + 4)[0] >> 8,
                struct.unpack_from(order + "H", data, base + 6)[0] >> 8,
                struct.unpack_from(order + "H", data, base + 8)[0] >> 8,
            )
        )

    rows: list[bytes] = []
    for y in range(height):
        line = data[pixels_start + y * bytes_per_line : pixels_start + (y + 1) * bytes_per_line]
        out = bytearray()
        for x in range(width):
            if bits_per_pixel == 32:
                offset = x * 4
                if offset + 4 > len(line):
                    raise ScreenshotError("the capture's pixel rows are inconsistent")
                value = struct.unpack_from(order + "I", line, offset)[0]
                out += bytes(
                    (_scaled(value, red_mask), _scaled(value, green_mask),
                     _scaled(value, blue_mask))
                )
            elif bits_per_pixel == 24:
                offset = x * 3
                if offset + 3 > len(line):
                    raise ScreenshotError("the capture's pixel rows are inconsistent")
                if order == "<":
                    blue, green, red = line[offset], line[offset + 1], line[offset + 2]
                else:
                    red, green, blue = line[offset], line[offset + 1], line[offset + 2]
                out += bytes((red, green, blue))
            elif bits_per_pixel == 16:
                offset = x * 2
                if offset + 2 > len(line):
                    raise ScreenshotError("the capture's pixel rows are inconsistent")
                value = struct.unpack_from(order + "H", line, offset)[0]
                if red_mask:
                    out += bytes(
                        (_scaled(value, red_mask), _scaled(value, green_mask),
                         _scaled(value, blue_mask))
                    )
                else:  # no masks declared: the packed 5-6-5 layout xwd writes
                    out += bytes(
                        (
                            ((value >> 11) & 0x1F) * 255 // 31,
                            ((value >> 5) & 0x3F) * 255 // 63,
                            (value & 0x1F) * 255 // 31,
                        )
                    )
            else:  # 8 bpp: an index into the file's own colour table
                if x >= len(line):
                    raise ScreenshotError("the capture's pixel rows are inconsistent")
                index = line[x]
                if index < len(palette):
                    out += bytes(palette[index])
                else:
                    out += bytes((index, index, index))
        rows.append(bytes(out))
    return ScreenImage(width=width, height=height, rows=tuple(rows))


def encode_png(image: ScreenImage) -> bytes:
    """Encode RGB rows as a PNG (8-bit truecolour, filter 0) — stdlib only."""
    raw = bytearray()
    for row in image.rows:
        raw.append(0)  # filter type 0 (None) per scanline
        raw += row

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", image.width, image.height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def xwd_to_png(data: bytes) -> tuple[bytes, ScreenImage]:
    """XWD bytes → (PNG bytes, decoded image). Raises :class:`ScreenshotError`."""
    image = decode_xwd(data)
    return encode_png(image), image


def png_size(data: bytes) -> tuple[int, int]:
    """The ``(width, height)`` an encoded PNG declares in its IHDR chunk.

    A capture taken by an external tool (ImageMagick's ``import``, report 18 F-capture) has
    no decoded :class:`ScreenImage`, so its dimensions were reported as ``0x0`` while the
    file on disk was a real image — a *zero where a measurement exists*. This reads the
    measurement out of the bytes instead of guessing: signature, IHDR length, both integers.
    """
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ScreenshotError("not a PNG with an IHDR chunk")
    width, height = struct.unpack(">II", data[16:24])
    if not width or not height:
        raise ScreenshotError(f"the PNG declares an empty canvas ({width}x{height})")
    return int(width), int(height)


def safe_label(text: str, *, fallback: str = "screen") -> str:
    """A window title → a filename fragment (evidence naming, never a path)."""
    # Only alphanumerics, "-" and "_" survive: a "." is dropped too, so a label can never
    # contain ".." and a title like "../../etc/passwd" degrades to a harmless slug.
    cleaned = "".join(
        char if (char.isalnum() or char in "-_") else "-" for char in str(text or "").strip()
    )
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return (cleaned[:60].strip("-") or fallback).lower()


#: Container names this module creates. Only names under this prefix may ever be reaped,
#: and only for the run that owns them — a GUI run must never touch someone else's
#: container (a build, a database, another agent's session).
CONTAINER_PREFIX = "godsv-cu-"


def own_container_names(prefix: str = CONTAINER_PREFIX) -> list[str]:
    """Names of containers that match ``prefix`` — the only ones a run may ever remove."""
    if not prefix.startswith(CONTAINER_PREFIX) or len(prefix) <= len(CONTAINER_PREFIX):
        raise ValueError(
            f"refusing to list containers for {prefix!r}: only '{CONTAINER_PREFIX}…' names "
            "belong to a Computer-Use run"
        )
    code, output, _err = _run_cmd_bytes(
        ["podman", "ps", "-a", "--format", "{{.Names}}"], timeout=60
    )
    if code != 0:
        return []
    lines = output.decode("utf-8", "replace").split()
    return sorted(name for name in lines if name.startswith(prefix))


def reap_own_containers(prefix: str) -> list[str]:
    """Force-remove the containers of **this** run only, and report what was removed.

    ``prefix`` must start with :data:`CONTAINER_PREFIX`; anything else is refused, so this
    can never be turned into a "clean up everything" call.
    """
    removed: list[str] = []
    for name in own_container_names(prefix):
        code, _output, _err = _run_cmd_bytes(["podman", "rm", "-f", name], timeout=90)
        if code == 0:
            removed.append(name)
    return removed


class ContainerRunner(BaseRunner):
    """Rootless-podman runner: actions and GUI input inside a disposable container.

    ``gui=True`` targets the local image ``localhost/godsv-cu-x11`` (Xvfb on
    ``:90``) so mouse/keyboard actions stay in an isolated display; network
    is off by default. The container is ``--rm`` and force-removed on close.
    """

    isolation = "container"
    DEFAULT_IMAGE = "localhost/godsv-cu-x11:latest"

    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        *,
        gui: bool = True,
        network: bool = False,
        display: str = ":90",
        timeout: float = 90.0,
        name: str | None = None,
    ) -> None:
        self.image = image
        self.gui = gui
        self.network = network
        self.display = display
        self.timeout = timeout
        self.name = name or f"{CONTAINER_PREFIX}{os.getpid()}-{int(time.time()) % 1000000}"
        self._started = False
        self._closed = False

    #: The exact-name prefix of containers this runner may remove. It is derived from the
    #: runner's own name and can never widen: ``ContainerRunner("x").reap()`` removes
    #: ``godsv-cu-…`` containers of this run only, never another container on the host.
    @property
    def container_prefix(self) -> str:
        return self.name

    def container_present(self) -> bool:
        """Is *this runner's* container still there? (the cleanup check, no guessing)."""
        code, output = _run_cmd(
            ["podman", "container", "exists", self.name], timeout=30
        )
        return code == 0 and "no such" not in output.lower()

    def available(self) -> tuple[bool, str]:
        if shutil.which("podman") is None:
            return False, "podman is not installed"
        code, output = _run_cmd(["podman", "image", "exists", self.image], timeout=30)
        if code != 0:
            return False, f"image missing: {self.image}"
        return True, "available"

    def start(self) -> tuple[bool, str]:
        ok, detail = self.available()
        if not ok:
            return False, detail
        if self._started:
            return True, "running"
        cmd = [
            "podman",
            "run",
            "-d",
            "--rm",
            "--name",
            self.name,
            "--security-opt",
            "no-new-privileges",
        ]
        if not self.network:
            cmd += ["--network", "none"]
        cmd += [self.image, "sleep", "3600"]
        code, output = _run_cmd(cmd, timeout=self.timeout)
        if code != 0:
            return False, f"start failed: {output[:200]}"
        if self.gui:
            # A literal script with the display as the first positional argument: the
            # shell re-parses the text after -c, so the display is passed, never
            # spliced into the script (`(Xvfb "$1" -screen …)`).
            code, output = _run_cmd(
                [
                    "podman",
                    "exec",
                    self.name,
                    "sh",
                    "-c",
                    '(Xvfb "$1" -screen 0 800x600x24 -nolisten tcp '
                    ">/dev/null 2>&1 &); sleep 2; echo XVFB_READY",
                    "sh",
                    self.display,
                ],
                timeout=self.timeout,
            )
            if code != 0 or "XVFB_READY" not in output:
                _run_cmd(["podman", "rm", "-f", self.name], timeout=30)
                return False, f"Xvfb start failed: {output[:200]}"
        self._started = True
        return True, "started"

    def _default_argv(self, action: Action) -> list[str] | None:
        if action.argv:
            return list(action.argv)
        if action.kind == "mouse":
            x, y = int(action.payload.get("x", 0)), int(action.payload.get("y", 0))
            argv = ["xdotool", "mousemove", str(x), str(y)]
            if action.payload.get("click"):
                argv.append("click")
                argv.append(str(action.payload.get("button", 1)))
            return argv
        if action.kind == "key":
            key_name = str(action.payload.get("key", ""))
            return ["xdotool", "key", key_name] if key_name else None
        if action.kind == "type_text":
            text = str(action.payload.get("text", ""))
            return ["xdotool", "type", "--", text] if text else None
        return None

    def act(self, action: Action) -> ActionResult:
        if action.kind in GUI_KINDS and not self.gui:
            return ActionResult(ok=False, detail="GUI kinds need gui=True (a display image)")
        ok, detail = self.start()
        if not ok:
            return ActionResult(ok=False, detail=detail)
        argv = self._default_argv(action)
        if not argv:
            return ActionResult(ok=False, detail=f"no argv for kind '{action.kind}'")
        cmd = ["podman", "exec"]
        if self.gui:
            cmd += ["-e", f"DISPLAY={self.display}"]
        cmd += [self.name] + argv
        code, output = _run_cmd(cmd, timeout=self.timeout)
        return ActionResult(ok=code == 0, stdout=output, detail=output[:200] or f"exit {code}")

    def observe(self) -> Observation:
        ok, detail = self.start()
        if not ok:
            return Observation(detail=detail)
        if self.gui:
            code, output = _run_cmd(
                [
                    "podman",
                    "exec",
                    "-e",
                    f"DISPLAY={self.display}",
                    self.name,
                    "xdotool",
                    "search",
                    "--name",
                    ".",
                    "getwindowname",
                    "%@",
                ],
                timeout=self.timeout,
            )
            text = output if output else "(no windows)"
            return Observation(text=text, detail=f"exit {code}")
        code, output = _run_cmd(["podman", "exec", self.name, "ls", "-la"], timeout=self.timeout)
        return Observation(text=output, detail=f"exit {code}")

    # -- the capture (GAP-K2): xwd inside the image, PNG on the host ------------- #
    #
    # The image ships exactly one capture tool — ``xwd`` — and no converter, so the dump
    # travels out over the same `podman exec` the actions use (base64 on stdout) and is
    # decoded by :func:`xwd_to_png` with the standard library. Nothing is invented if the
    # tool is missing: the caller gets ``(None, reason)`` and can say exactly what is
    # missing (which is what ``act check`` reports as ``observe.screenshot``).
    CAPTURE_TOOLS: tuple[str, ...] = ("xwd",)
    CAPTURE_ENCODER: tuple[str, ...] = ("base64",)

    def _probe_capture_tools(self) -> tuple[bool, str]:
        """Whether the image really carries the capture tools — asked, not assumed.

        The probe is one **literal** shell script and every tool name travels as a
        positional argument (``sh -c '<literal>' sh <tool>…``): the shell re-parses
        the script text, so a name is never spliced into it.
        """
        code, output = _run_cmd(
            [
                "podman",
                "exec",
                self.name,
                "sh",
                "-c",
                'for tool in "$@"; do command -v "$tool" >/dev/null || '
                '{ echo "MISSING-$tool"; exit 3; }; done; echo CAPTURE-TOOLS-OK',
                "sh",
                *self.CAPTURE_TOOLS,
                *self.CAPTURE_ENCODER,
            ],
            timeout=self.timeout,
        )
        if code == 0 and "CAPTURE-TOOLS-OK" in output:
            return True, "xwd is present in the image"
        missing = [tool for tool in self.CAPTURE_TOOLS + self.CAPTURE_ENCODER
                   if f"MISSING-{tool}" in output]
        return False, (
            "the image carries no usable capture tool: "
            + (", ".join(f"missing {tool}" for tool in missing) if missing
               else f"the probe failed ({output[:160] or f'exit {code}'})")
        )

    def screenshot_available(self) -> tuple[bool, str]:
        """Can this runner really capture the display? (a real check, not a note)"""
        ok, detail = self.start()
        if not ok:
            return False, detail
        return self._probe_capture_tools()

    def screenshot(
        self,
        path: str | Path | None = None,
        *,
        window_id: str = "",
        label: str = "",
    ) -> tuple[ScreenCapture | None, str]:
        """Capture the isolated display (or one window) as a **real** PNG.

        Returns ``(capture, reason)``; the reason is empty on success and names the exact
        missing piece otherwise. The bytes on disk are hashed — a screenshot count without
        a hash is not evidence.
        """
        ok, detail = self.start()
        if not ok:
            return None, detail
        if self.gui:
            # A literal pipeline with the selector and the display as positional
            # arguments (`xwd "$@" -silent | base64 -w0`): no caller value is ever
            # spliced into shell text, so nothing here can be re-parsed by the shell.
            selector = ["-id", str(window_id)] if window_id else ["-root"]
            argv = [
                "sh",
                "-c",
                'xwd "$@" -silent | base64 -w0',
                "sh",
                *selector,
                "-display",
                self.display,
            ]
        else:
            return None, "this runner drives no display (gui=False): there is nothing to capture"
        cmd = ["podman", "exec", "-e", f"DISPLAY={self.display}", self.name] + argv
        code, raw, err = _run_cmd_bytes(cmd, timeout=self.timeout)
        if code != 0:
            return None, f"the capture (xwd) failed: exit {code} {err[:160]}"
        payload = raw.strip()
        if not payload:
            return None, (
                "the capture returned no bytes — the image has no screenshot tool "
                f"(xwd) or the display {self.display} is not up"
            )
        try:
            xwd = base64.b64decode(payload, validate=False)
        except Exception as exc:  # noqa: BLE001 — a broken transfer is not a screenshot
            return None, f"the capture could not be decoded: {type(exc).__name__}: {exc}"
        try:
            png, image = xwd_to_png(xwd)
        except ScreenshotError as exc:
            return None, f"the capture is not a decodable screenshot: {exc}"
        if len(png) < SCREENSHOT_MIN_BYTES:
            return None, f"the converted capture is {len(png)} bytes — not a screenshot"
        name = safe_label(label, fallback="screen")
        target_path = (
            Path(path)
            if path is not None
            else Path.cwd() / "cu-screenshots" / f"{self.name}-{name}.png"
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(png)
        return (
            ScreenCapture(
                path=str(target_path),
                sha256=hashlib.sha256(png).hexdigest(),
                size_bytes=len(png),
                width=image.width,
                height=image.height,
                scope=f"window {window_id}" if window_id else "root",
                label=name,
            ),
            "",
        )

    def close(self) -> None:
        """Remove **this runner's** container and nothing else.

        Scoped to ``self.name`` by construction; ``container_present()`` afterwards is the
        check the callers report, so "the run cleaned up after itself" is a measurement and
        not a promise.
        """
        if shutil.which("podman") is None:
            self._started = False
            self._closed = True
            return
        if self._started or self.container_present():
            _run_cmd(["podman", "rm", "-f", self.name], timeout=60)
        self._started = False
        self._closed = True

    def reap(self) -> list[str]:
        """Remove any container of *this run's* name family (crash leftovers only)."""
        return reap_own_containers(self.container_prefix)

    def cleanup_record(self) -> dict:
        """What the teardown actually left behind — published with every GUI run."""
        return {
            "container": self.name,
            "prefix": self.container_prefix,
            "closed": bool(self._closed),
            "still_present": bool(self.container_present()) if shutil.which("podman") else False,
        }


class CUExecutor:
    """Runs a step through ordered attempts: act → observe → verify → audit."""

    def __init__(
        self,
        policy: CUPolicy | None = None,
        *,
        events: EventStore | None = None,
        session_id: str = "cu",
    ) -> None:
        self.policy = policy or CUPolicy()
        self.events = events
        self.session_id = session_id
        self._step = 0

    def _record(self, kind: str, payload: dict) -> None:
        if self.events is None:
            return
        self._step += 1
        self.events.append(self.session_id, self._step, kind, payload)

    def _check_policy(self, attempts: list[Attempt], runner: Runner) -> None:
        isolation = str(getattr(runner, "isolation", "none") or "none")
        if self.policy.require_isolation and not runner.is_isolated:
            # Owner rule (2026-09-23): "live-desktop darf alles steuern". An explicit
            # ``allow_live_desktop`` is the full desktop — the runner's own target_scope
            # is a focus hint, never a cage. The policy field alone can still never widen
            # an isolated runner into the live session (GAP-K3).
            # Owner rule: ANY opt-in = FULL desktop. ``live_desktop_target`` is a focus
            # hint only — it never restricts which window may be driven.
            if not (
                self.policy.allow_live_desktop
                and isolation in ("host-session", "live-desktop")
            ):
                raise PolicyViolation(
                    f"Runner is not isolated ('{isolation}') — the live desktop is forbidden"
                    " (opt in with CUPolicy.allow_live_desktop; that is the full desktop)"
                )
        if self.policy.dry_run and runner.isolation != "dry-run":
            raise PolicyViolation("dry_run policy: only the DryRunRunner is allowed")
        for attempt in attempts:
            if attempt.action.kind not in self.policy.allowed_kinds:
                self._record(
                    "cu_blocked",
                    {"kind": attempt.action.kind, "reason": "kind-not-allowed"},
                )
                raise PolicyViolation(f"action kind is not allowed: {attempt.action.kind}")
            if attempt.verify.is_empty():
                self._record(
                    "cu_blocked",
                    {"kind": attempt.action.kind, "reason": "no-verification"},
                )
                raise PolicyViolation("an attempt without verification is not allowed")

    def run(self, step: str, attempts: list[Attempt], runner: Runner) -> CURunResult:
        self._check_policy(attempts, runner)
        ordered = order_attempts(attempts)
        actions = 0
        fallbacks = 0
        last_detail = ""
        for attempt in ordered:
            if actions >= self.policy.max_actions:
                self._record("cu_aborted", {"step": step, "reason": "max_actions"})
                return CURunResult(step, "aborted", None, actions, fallbacks, "max_actions")
            self._record(
                "cu_attempt",
                {
                    "step": step,
                    "channel": attempt.channel,
                    "kind": attempt.action.kind,
                    "description": attempt.action.description[:200],
                },
            )
            result = runner.act(attempt.action)
            actions += 1
            self._record(
                "cu_action",
                {
                    "step": step,
                    "channel": attempt.channel,
                    "kind": attempt.action.kind,
                    "ok": result.ok,
                    "detail": result.detail[:200],
                },
            )
            ok, detail = _verify_attempt(attempt, result, runner)
            stdout_val = str(getattr(result, "stdout", "") or "")
            if ok:
                self._record(
                    "cu_verified",
                    {"step": step, "channel": attempt.channel, "detail": detail[:200]},
                )
                return CURunResult(
                    step, "verified", attempt.channel, actions, fallbacks, detail,
                    stdout=stdout_val,
                    exit_code=getattr(result, "exit_code", None),
                    stderr=str(getattr(result, "stderr", "") or ""),
                )

            fallbacks += 1
            self._record(
                "cu_failed", {"step": step, "channel": attempt.channel, "detail": detail[:200]}
            )
            last_detail = detail
            if fallbacks >= self.policy.max_fallbacks:
                break
        self._record(
            "cu_aborted",
            {"step": step, "reason": "all-attempts-failed", "detail": last_detail[:200]},
        )
        return CURunResult(
            step, "aborted", None, actions, fallbacks, last_detail or "all attempts failed",
        )


def _verify_attempt(attempt: Attempt, result: ActionResult, runner: Runner) -> tuple[bool, str]:
    if not result.ok:
        # An action the runner *refused* did not run, so no expectation about its effect can
        # hold. Measured defect (report 18, F2): a live-desktop pointer action refused by the
        # runner was recorded `verified` because only stdout/observation were inspected — a
        # run could publish "all 2 action(s) verified" for something that never executed.
        return False, f"the action did not run: {result.detail[:160]}"
    verify = attempt.verify
    if verify.expect_stdout_contains and verify.expect_stdout_contains not in result.stdout:
        return False, f"stdout without '{verify.expect_stdout_contains}': {result.detail[:120]}"
    if verify.expect_observe_contains:
        observation = runner.observe()
        if verify.expect_observe_contains not in observation.text:
            return False, f"observation without '{verify.expect_observe_contains}'"
    if verify.expect_ok and not result.ok:
        return False, f"expected ok, got: {result.detail[:160]}"
    return True, "verified"


def runner_availability() -> dict[str, str]:
    """Probe which isolation levels are usable on this host."""
    report: dict[str, str] = {"dry-run": "available"}
    report["sandbox (bwrap)"] = "available" if _sandbox_available() else "unavailable"
    if shutil.which("podman") is None:
        report["container (podman)"] = "podman is not installed"
        report["container GUI (godsv-cu-x11)"] = "podman is not installed"
    else:
        plain = ContainerRunner(gui=False, image="docker.io/library/alpine:latest")
        ok, detail = plain.available()
        report["container (podman)"] = detail if ok else f"unavailable ({detail})"
        gui = ContainerRunner()
        ok_gui, detail_gui = gui.available()
        report["container GUI (godsv-cu-x11)"] = (
            detail_gui if ok_gui else f"unavailable ({detail_gui})"
        )
    try:
        from .cu_host_session import host_session_availability

        report["host session (owned Xvfb)"] = host_session_availability()["detail"]
    except Exception as exc:  # noqa: BLE001 — a missing host runner is a reportable fact
        report["host session (owned Xvfb)"] = f"unavailable ({type(exc).__name__}: {exc})"
    return report


def plan_cu_attempts(
    client: LLMClient,
    registry: PromptRegistry,
    *,
    goal: str,
    observations: str = "",
    channels: tuple[str, ...] = CHANNEL_ORDER,
    budget: BudgetTracker | None = None,
) -> list[Attempt] | None:
    """LLM draft of ordered attempts for one step (None on any failure)."""
    try:
        result = run_prompt(
            client,
            registry,
            "cu_step_plan",
            {
                "goal": goal,
                "observations": observations or "(none)",
                "available_channels": ", ".join(channels),
            },
            budget=budget,
        )
    except Exception:
        return None
    data = result.parsed if isinstance(result.parsed, dict) else {}
    raw = data.get("attempts") if isinstance(data.get("attempts"), list) else []
    attempts: list[Attempt] = []
    for item in raw[:4]:
        if not isinstance(item, dict):
            continue
        channel = str(item.get("channel", API))
        if channel not in CHANNEL_ORDER:
            continue
        action_raw = item.get("action")
        action_data: dict = action_raw if isinstance(action_raw, dict) else {}
        verify_raw = item.get("verify")
        verify_data: dict = verify_raw if isinstance(verify_raw, dict) else {}
        action = Action(
            kind=str(action_data.get("kind", "cli")),
            argv=[str(part) for part in (action_data.get("argv") or [])],
            payload=dict(action_data.get("payload") or {}),
            description=str(item.get("description", ""))[:200],
        )
        verification = Verification(
            expect_stdout_contains=_opt_str(verify_data.get("expect_stdout_contains")),
            expect_observe_contains=_opt_str(verify_data.get("expect_observe_contains")),
            description=str(verify_data.get("description", ""))[:200],
        )
        attempts.append(Attempt(channel=channel, action=action, verify=verification))
    if not attempts:
        return None
    return order_attempts(attempts)


def _record_cu_run(path: str | Path, record: dict) -> None:
    """Append one CU run to ``cu_runs.jsonl`` (the same file the panel view reads)."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def run_cu_request(
    *,
    goal: str,
    runtime: str | Path,
    client: LLMClient | None = None,
    registry: PromptRegistry | None = None,
    observations: str = "",
    runner: Runner | None = None,
    runner_kind: str = "",
    policy: CUPolicy | None = None,
    budget: BudgetTracker | None = None,
    events: EventStore | None = None,
    session_id: str = "cu",
    run_id: str = "",
    guistate=None,
    expect_change: str = "",
    post_text: str = "",
    flags=None,
    evaluation=None,
    pre_approved_candidates=(),
    post_guistate=None,
    escalation_signals=None,
    escalation_check: bool = False,
    subtool=None,
    app_version: str = "",
    locale: str = "",
    observed_start_conditions=(),
    observed_anchors=(),
) -> dict:
    """One Computer-Use request through the existing pipeline — the agent-facing entry.

    Measured 17.09. (S10): the product could list runners and run a *fixed* dry-run demo, but there
    was no way for the chat/agent to bring a goal in; the gap was silent. This is that entry and it
    reuses the same pieces the panel demo uses — plan (``plan_cu_attempts``, caller's client),
    ordered attempts (API > DOM > Vision), isolated runner, act → observe → verify → audit.

    Honest states, never a fake success:
    * no model client (or an unusable draft) → ``blocked`` with the reason, nothing is run;
    * a policy that forbids the runner/action kind → ``blocked`` with the policy reason
      (the executor records ``cu_blocked``);
    * everything else → the executor's real verdict (``verified`` / ``aborted`` + detail).

    The caller decides how real it gets: ``runner_kind="dry-run"`` (default, deterministic),
    ``"sandbox"`` or ``"container-gui"`` for a real effect, plus the matching ``policy``.

    §54.2 postcondition: with a ``guistate`` and an ``expect_change`` the run reads the post-action
    perception through the existing ``effect_check``. A verified action whose expected change cannot
    be read is reported as ``reobserve`` — the honest verdict for "wrong window / focus lost / user
    takeover / action had no effect on the observed surface" — never as a success. The reading never
    certifies (``certifies`` stays false, ground truth is the app effect).
    """

    runtime_dir = Path(runtime)
    run_key = run_id or f"cu-{int(time.time())}"
    availability = runner_availability()
    if runner is None:
        runner = _runner_for_kind(runner_kind or "dry-run", runtime_dir, policy=policy)
    if runner is None:
        result = {
            "status": "blocked",
            "reason": f"no usable runner ({runner_kind or 'dry-run'})",
            "verified": False,
            "goal": goal,
            "runners": availability,
        }
        _record_cu_run(
            runtime_dir / "cu_runs.jsonl",
            {"ts": time.time(), "step": run_key, "status": "blocked", "goal": goal,
             "kind": runner_kind or "dry-run", "detail": result["reason"]},
        )
        return result
    attempts = None
    if client is not None:
        attempts = plan_cu_attempts(
            client,
            registry if registry is not None else PromptRegistry(),
            goal=goal,
            observations=observations,
            budget=budget,
        )
    if not attempts:
        result = {
            "status": "blocked",
            "reason": (
                "no plan — no model client is configured for a draft (the caller injects its own)"
                if client is None
                else "no plan — the draft did not yield a usable attempt list"
            ),
            "verified": False,
            "goal": goal,
            "runner": runner.isolation,
            "runners": availability,
        }
        _record_cu_run(
            runtime_dir / "cu_runs.jsonl",
            {"ts": time.time(), "step": run_key, "status": "blocked", "goal": goal,
             "kind": runner.isolation, "detail": result["reason"]},
        )
        return result
    kinds = tuple(dict.fromkeys(str(attempt.action.kind) for attempt in attempts))
    gate = system_one_guistate_gate(
        flags=flags,
        evaluation=evaluation,
        guistate=guistate,
        allowed_kinds=kinds,
        pre_approved_candidates=pre_approved_candidates,
    )
    if gate is not None:
        proposed = tuple(getattr(guistate, "action_candidates", ()) or ())
        if proposed and not gate["action_candidates"]:
            # §53/§54.3: the state proposes candidates, none of them is pre-approved — the caller's
            # approved set stays empty, so nothing may run. The advisory never adds a candidate.
            result = {
                "status": "blocked",
                "reason": (
                    f"GUIState proposes {len(proposed)} action candidate(s), none pre-approved — "
                    "no action is allowed to run (the approved set belongs to the caller)"
                ),
                "verified": False,
                "goal": goal,
                "runner": runner.isolation,
                "runners": availability,
                "system_one": gate,
            }
            _record_cu_run(
                runtime_dir / "cu_runs.jsonl",
                {"ts": time.time(), "step": run_key, "status": "blocked", "goal": goal,
                 "kind": runner.isolation, "detail": result["reason"]},
            )
            if events is not None:
                events.append(
                    session_id,
                    0,
                    "cu_blocked",
                    {"stage": "system_one_gate", "reason": result["reason"][:200]},
                )
            return result
    decision = None
    if gate is not None:
        chosen = _evaluation_candidate_choice(evaluation)
        if chosen:
            from .system_one_subtools import CandidateChoiceViolation, validate_candidate_choice

            try:
                validate_candidate_choice(chosen, allowed_candidates=gate["action_candidates"])
            except CandidateChoiceViolation as exc:
                result = {
                    "status": "blocked",
                    "reason": f"candidate choice refused: {exc}",
                    "verified": False,
                    "goal": goal,
                    "runner": runner.isolation,
                    "runners": availability,
                    "system_one": gate,
                }
                _record_cu_run(
                    runtime_dir / "cu_runs.jsonl",
                    {"ts": time.time(), "step": run_key, "status": "blocked", "goal": goal,
                     "kind": runner.isolation, "detail": result["reason"][:200]},
                )
                if events is not None:
                    events.append(
                        session_id,
                        0,
                        "cu_blocked",
                        {"stage": "candidate_choice", "reason": result["reason"][:200]},
                    )
                return result
        if escalation_check or escalation_signals is not None:
            decision = system_two_escalation_decision(
                profile=None,
                evaluation=evaluation,
                signals=escalation_signals,
                allowed_candidates=gate["action_candidates"],
                context=goal,
            )
            if decision.escalate:
                reasons = "; ".join(decision.reasons) or ", ".join(decision.triggers)
                result = {
                    "status": "blocked",
                    "reason": f"system two escalation: {reasons}"[:400],
                    "verified": False,
                    "goal": goal,
                    "runner": runner.isolation,
                    "runners": availability,
                    "system_one": gate,
                    "system_two": decision.as_dict(),
                }
                _record_cu_run(
                    runtime_dir / "cu_runs.jsonl",
                    {"ts": time.time(), "step": run_key, "status": "blocked", "goal": goal,
                     "kind": runner.isolation, "detail": result["reason"][:200]},
                )
                if events is not None:
                    events.append(
                        session_id,
                        0,
                        "cu_blocked",
                        {"stage": "system_two", "reason": result["reason"][:200]},
                    )
                return result
    subtool_decision = None
    if subtool is not None:
        from .system_one_subtools import (
            BlindReplayRefused,
            assert_no_blind_replay,
            check_blind_replay,
            evaluate_subtool_step,
        )

        guard = check_blind_replay(
            subtool,
            observed_start_conditions=observed_start_conditions,
            observed_anchors=observed_anchors,
        )
        try:
            assert_no_blind_replay(guard)
        except BlindReplayRefused as exc:
            # §11/§56: the anchor is not observed right now, so the stored sequence may not run —
            # reobserve instead of replaying blind ("subtool anchor invalid")
            return _subtool_stop(
                status="reobserve",
                reason=f"blind replay refused: {exc}",
                runtime_dir=runtime_dir,
                run_key=run_key,
                goal=goal,
                runner=runner,
                availability=availability,
                events=events,
                session_id=session_id,
                extra={
                    "missing_anchors": list(guard.missing_anchors),
                    "missing_start_conditions": list(guard.missing_start_conditions),
                    "subtool": guard.as_dict(),
                },
            )
        subtool_decision = evaluate_subtool_step(
            subtool,
            app_version=app_version,
            locale=locale,
            observed_start_conditions=observed_start_conditions,
            observed_anchors=observed_anchors,
        )
        if not subtool_decision.proceed:
            return _subtool_stop(
                status="reobserve" if subtool_decision.reobserve else "blocked",
                reason="subtool step: " + ("; ".join(subtool_decision.reasons) or "not allowed"),
                runtime_dir=runtime_dir,
                run_key=run_key,
                goal=goal,
                runner=runner,
                availability=availability,
                events=events,
                session_id=session_id,
                extra={"subtool": subtool_decision.as_dict()},
            )
    # DESK-01/DESK-02 — this entry drives the same input path as run_action_list but was
    # not going through it, so it skipped the desktop lease entirely. A caller that asked
    # for the live desktop could move the user's mouse with nobody holding the desktop.
    # Lazy import: act_service imports this module at import time.
    from .act_service import (
        _bind_desktop_fence,
        _default_desktop_store,
        _needs_desktop_lease,
    )
    from .desktop_lease import LeaseRefused

    desktop_store = None
    desktop_lease = None
    desktop_payload: dict = {}
    if _needs_desktop_lease(runner_kind=runner_kind, runner=runner):
        desktop_store = _default_desktop_store()
        try:
            desktop_lease = desktop_store.acquire(session_id, task=goal or session_id)
        except LeaseRefused as exc:
            result = {
                "status": "blocked",
                "verified": False,
                "goal": goal,
                "reason": f"DESK-01: the host desktop is not available to this run — {exc}",
                "runner": runner.isolation,
                "runners": availability,
            }
            _record_cu_run(
                runtime_dir / "cu_runs.jsonl",
                {"ts": time.time(), "step": run_key, "status": "blocked", "goal": goal,
                 "kind": runner.isolation, "detail": result["reason"]},
            )
            return result
        generation = int(getattr(desktop_lease, "generation", 0) or 0)
        if not _bind_desktop_fence(runner, desktop_store, session_id, generation):
            # the fence must reach the adapter; without it the actions below would run
            # uncontrolled even though the lease was taken
            desktop_store.release(session_id, reason="fence not bindable")
            result = {
                "status": "blocked",
                "verified": False,
                "goal": goal,
                "reason": "DESK-02: the desktop lease was taken but the input fence could "
                          "not be bound to the runner — refusing to act uncontrolled",
                "runner": runner.isolation,
                "runners": availability,
            }
            _record_cu_run(
                runtime_dir / "cu_runs.jsonl",
                {"ts": time.time(), "step": run_key, "status": "blocked", "goal": goal,
                 "kind": runner.isolation, "detail": result["reason"]},
            )
            return result
        desktop_payload = {
            "acquired": True, "owner_run": session_id,
            "fencing_token": getattr(desktop_lease, "owner_token", ""),
            "generation": generation,
        }
    try:
        run_result = CUExecutor(policy or CUPolicy(), events=events, session_id=session_id).run(
            run_key, attempts, runner
        )
    except PolicyViolation as exc:
        result = {
            "status": "blocked",
            "reason": f"policy: {exc}",
            "verified": False,
            "goal": goal,
            "runner": runner.isolation,
            "runners": availability,
            "desktop": desktop_payload,
        }
        _record_cu_run(
            runtime_dir / "cu_runs.jsonl",
            {"ts": time.time(), "step": run_key, "status": "blocked", "goal": goal,
             "kind": runner.isolation, "detail": result["reason"]},
        )
        return result
    finally:
        if desktop_store is not None and desktop_lease is not None:
            released = desktop_store.release(session_id, reason="run finished")
            desktop_payload["released"] = bool(released)
    post_gate = system_one_guistate_gate(
        flags=flags,
        evaluation=evaluation,
        guistate=post_guistate,
        allowed_kinds=tuple(gate["allowed_kinds"]) if gate is not None else kinds,
        pre_approved_candidates=pre_approved_candidates,
    )
    postcondition = None
    status = run_result.status
    if guistate is None and str(expect_change or "").strip():
        # the post-action perception comes from the RUNNER, not from the caller's word
        try:
            observation = runner.observe()
            guistate = _guistate_from_observation(observation)
            post_text = post_text or str(getattr(observation, "text", "") or "")
        except Exception:  # noqa: BLE001 — without a perception the reading stays unavailable
            guistate = None
    if guistate is not None and str(expect_change or "").strip():
        try:
            from .system_one_guistate import effect_check

            postcondition = effect_check(
                guistate, expect_change=expect_change, post_text=post_text
            )
            if status == "verified" and not postcondition.get("effect_observed"):
                # verified against its own expectation, but the real effect is not in
                # the post-action perception: reobserve, never pretend success
                status = "reobserve"
        except Exception as exc:  # noqa: BLE001 — a failed reading is reported, not fatal
            postcondition = {
                "effect_observed": False,
                "certifies": False,
                "error": f"{type(exc).__name__}: {exc}"[:160],
            }
            if status == "verified":
                status = "reobserve"
    record = {
        "ts": time.time(),
        "step": run_result.step,
        "status": status,
        "channel": run_result.channel,
        "actions": run_result.actions,
        "fallbacks": run_result.fallbacks,
        "detail": run_result.detail,
        "kind": runner.isolation,
        "goal": goal,
        "planned": len(attempts),
    }
    _record_cu_run(runtime_dir / "cu_runs.jsonl", record)
    payload = {
        "status": status,
        "verified": status == "verified",
        "channel": run_result.channel,
        "actions": run_result.actions,
        "fallbacks": run_result.fallbacks,
        "detail": (
            f"{run_result.detail} — expected change {expect_change!r} not in the post-action "
            "perception: reobserve"
            if status == "reobserve"
            else run_result.detail
        ),
        "goal": goal,
        "runner": runner.isolation,
        "runners": availability,
        "planned": len(attempts),
    }
    if postcondition is not None:
        payload["postcondition"] = postcondition
    if gate is not None:
        payload["system_one"] = gate
    if post_gate is not None:
        payload["post_system_one"] = post_gate
    if decision is not None:
        payload["system_two"] = decision.as_dict()
    if subtool_decision is not None:
        payload["subtool"] = subtool_decision.as_dict()
    return payload


def _guistate_from_observation(observation, *, channel: str = "window_metadata"):
    """Build the §52-complete GUIState from a runner observation (real perception, no invention).

    Every field of the contract is reported; the ones we did not look at carry no value. The state
    never certifies an effect (ground truth stays the app effect) and ``live_display`` stays false:
    a window-metadata channel is perception, not a live surface.
    """

    from .system_one_guistate import GUI_STATE_FIELDS, GUIState, PerceivedField

    text = str(getattr(observation, "text", "") or "")
    changes = tuple(line.strip() for line in text.splitlines() if line.strip())
    fields = {
        name: PerceivedField(name=name, value=None, perceived=False) for name in GUI_STATE_FIELDS
    }
    fields["observed_changes"] = PerceivedField(
        name="observed_changes",
        value=changes,
        perceived=True,
        channel=channel,
    )
    return GUIState(
        gui_state_id=f"cu-observe-{int(time.time())}",
        fields=fields,
        channel=channel,
        live_display=False,
    )


def _subtool_stop(
    *,
    status: str,
    reason: str,
    runtime_dir,
    run_key,
    goal,
    runner,
    availability,
    events,
    session_id,
    extra: dict | None = None,
) -> dict:
    """Stop before acting: the subtool contract says no — reported, recorded, never a workaround."""

    result = {
        "status": status,
        "reason": reason[:400],
        "verified": False,
        "goal": goal,
        "runner": runner.isolation,
        "runners": availability,
    }
    result.update(extra or {})
    _record_cu_run(
        runtime_dir / "cu_runs.jsonl",
        {"ts": time.time(), "step": run_key, "status": status, "goal": goal,
         "kind": runner.isolation, "detail": reason[:200]},
    )
    if events is not None:
        events.append(
            session_id,
            0,
            "cu_blocked",
            {"stage": "subtool", "status": status, "reason": reason[:200]},
        )
    return result


def _evaluation_candidate_choice(evaluation) -> str:
    """The candidate the provider's answer named, or '' when it named none.

    Read-only and defensive: an evaluation without answers, or without the candidate question, is
    simply "no choice" — never an invented one.
    """

    try:
        from .system_one_subtools import CANDIDATE_CHOICE_QUESTION_ID
    except Exception:  # noqa: BLE001 — without the module there is nothing to validate
        return ""
    answers = getattr(evaluation, "answers", None) or {}
    answer = answers.get(CANDIDATE_CHOICE_QUESTION_ID) if hasattr(answers, "get") else None
    choice = getattr(answer, "choice", "") or ""
    return str(choice)


def default_runner_kind() -> str:
    """The runner a request gets when the caller names none.

    The sandbox is the strongest isolation that can be *observed* (a real CLI action inside bwrap),
    so a plan can actually be verified against an effect; without bwrap the deterministic dry-run
    runner is the honest fallback instead of pretending the isolation exists.
    """

    return "sandbox" if _sandbox_available() else "dry-run"


def _runner_for_kind(
    kind: str, workdir: str | Path = ".", *, policy: CUPolicy | None = None
) -> Runner | None:
    """Map a runner name to its runner — unavailable isolation stays unavailable.

    ``host-session`` is the one name that can reach the host's own X server: it is built
    only when ``policy.allow_live_desktop`` is set and a target is named, and the runner
    itself refuses again (see :mod:`skaldir.cu_host_session`). Every other path keeps the
    default isolation rule untouched.
    """

    key = (kind or "").strip().lower()
    if key in ("", "dry-run", "dryrun", "dry"):
        return DryRunRunner()
    if key in ("sandbox", "bwrap", "local-sandbox"):
        return LocalSandboxRunner(workdir) if _sandbox_available() else None
    if key in ("container", "container-gui", "podman", "gui"):
        runner = ContainerRunner(gui=key in ("container-gui", "gui"))
        return runner if runner.available()[0] else None
    if key in ("host-session", "host", "live-desktop"):
        from .cu_host_session import HostSessionRunner, live_desktop_optin

        optin = live_desktop_optin()
        target = str(getattr(policy, "live_desktop_target", "") or "") or str(
            optin.get("target", "")
        )
        try:
            runner = HostSessionRunner(
                target=target,
                allow_live_desktop=bool(
                    getattr(policy, "allow_live_desktop", False) or optin.get("allowed")
                ),
                attach_to_session=key == "live-desktop",
            )
        except Exception:  # noqa: BLE001 — a refused host runner is simply unavailable
            return None
        return runner if runner.available()[0] else None
    return None


def _opt_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# --------------------------------------------------------------------------- #
# System-One action gate (ABNAHME Teil IX §53/§54, JEV-16/JEV-20 §98)          #
# --------------------------------------------------------------------------- #
#
# Computer Use stays state-based: the pre-approved action kinds and the
# verification of the real app effect keep the authority (§50). System One may
# only add an *advisory* reading before a step — never a wider action set, never
# an executed action.
#
# With the flags off (the default), an unknown provider, no evaluation, or the
# ``computer_use`` flag off, this returns ``None``: the executor's existing
# ``allowed_kinds`` policy path runs unchanged (§98).


def system_one_action_gate(*, flags, evaluation, allowed_kinds) -> dict | None:
    """Advisory System-One gate before a Computer-Use action, or ``None``.

    ``None`` means "no advisory" — the caller keeps its existing path. The
    returned record carries the *unchanged* ``allowed_kinds``: System One can
    never widen or choose the executable action set here.
    """
    if flags is None or not getattr(flags, "enabled", False):
        return None
    if getattr(flags, "provider", "disabled") == "disabled":
        return None
    if not getattr(flags, "computer_use", False):
        return None
    if evaluation is None:
        return None
    shadow = bool(getattr(flags, "shadow_mode", False))
    reading: dict[str, dict] = {}
    for question_id, answer in dict(getattr(evaluation, "answers", {})).items():
        reading[question_id] = {
            "kind": getattr(answer, "kind", ""),
            "value": getattr(answer, "value", None),
            "choice": getattr(answer, "choice", None),
            "confidence": getattr(answer, "confidence", None),
        }
    return {
        "mode": "shadow" if shadow else "advisory",
        "provider": getattr(evaluation, "provider", ""),
        "provider_model": getattr(evaluation, "provider_model", ""),
        "live_jev": bool(getattr(evaluation, "live_jev", False)),
        "evaluation_id": getattr(evaluation, "evaluation_id", ""),
        "allowed_kinds": tuple(allowed_kinds),
        "mutates": False,
        "reading": reading,
    }


# --------------------------------------------------------------------------- #
# §52/§93 (JEV-15) — GUIState binding (advisory; never widens the action set)   #
# --------------------------------------------------------------------------- #
#
# A GUIState is the structured, versioned reading of the CURRENT GUI (§52) that
# System One reads. Binding it to a Computer-Use step must change nothing about
# what may be executed: the state-based ``allowed_kinds`` and the pre-approved
# candidate actions stay the authority (§50/§53). A GUIState can only *narrow*
# the candidates, and ground truth stays the real app effect — the binding
# record therefore carries ``certifies_effect: False`` by construction.
#
# With the flags off (the default), an unknown provider, no evaluation, no
# GUIState, or the ``computer_use`` flag off, this returns ``None``: the
# executor's existing ``allowed_kinds`` path runs unchanged (§98).


def guistate_action_candidates(guistate, *, pre_approved) -> tuple[str, ...]:
    """A GUIState's proposed candidates ∩ the pre-approved set (§53/§54.3).

    Filtering only: a candidate that is not pre-approved is dropped and none is
    ever added. The pre-approved set belongs to the caller — this helper cannot
    enlarge it.
    """
    approved = tuple(str(item) for item in (pre_approved or ()))
    return tuple(
        str(item)
        for item in tuple(getattr(guistate, "action_candidates", ()) or ())
        if str(item) in approved
    )


def system_one_guistate_gate(
    *,
    flags,
    evaluation,
    guistate,
    allowed_kinds,
    pre_approved_candidates=(),
) -> dict | None:
    """Advisory GUIState binding for one Computer-Use step, or ``None``.

    ``None`` means "no advisory" — the caller keeps its existing path. The
    returned record carries the *unchanged* ``allowed_kinds`` and only the
    intersection of the GUIState's candidates with the pre-approved ones: System
    One can never widen or choose the executable action set here.
    """
    if flags is None or not getattr(flags, "enabled", False):
        return None
    if getattr(flags, "provider", "disabled") == "disabled":
        return None
    if not getattr(flags, "computer_use", False):
        return None
    if evaluation is None or guistate is None:
        return None
    shadow = bool(getattr(flags, "shadow_mode", False))
    return {
        "mode": "shadow" if shadow else "advisory",
        "provider": getattr(evaluation, "provider", ""),
        "provider_model": getattr(evaluation, "provider_model", ""),
        "live_jev": bool(getattr(evaluation, "live_jev", False)),
        "evaluation_id": getattr(evaluation, "evaluation_id", ""),
        "allowed_kinds": tuple(allowed_kinds),
        "mutates": False,
        "gui_state_id": str(getattr(guistate, "gui_state_id", "")),
        "gui_state_schema_version": str(
            getattr(guistate, "schema_version", GUISTATE_SCHEMA_VERSION)
        ),
        "gui_state_fingerprint": str(getattr(guistate, "fingerprint", "")),
        "gui_state_revision": int(getattr(guistate, "revision", 0) or 0),
        "gui_state_unperceived": tuple(getattr(guistate, "unperceived", ()) or ()),
        "action_candidates": guistate_action_candidates(
            guistate, pre_approved=pre_approved_candidates
        ),
        "ground_truth": str(getattr(guistate, "ground_truth", GROUND_TRUTH_APP_EFFECT)),
        "certifies_effect": False,
        "live_display": bool(getattr(guistate, "live_display", False)),
    }


# --------------------------------------------------------------------------- #
# §12 — the 13 System-2 escalation triggers + one decision function             #
# --------------------------------------------------------------------------- #
#
# The goal lists exactly 13 reasons to hand a Computer-Use / System-One step
# back to System 2. They are data here (a checkable list, not prose in a route
# rule): every trigger has an id, the goal's wording, and one check in
# :func:`system_two_escalation_decision`.
#
# Two rules make the decision honest rather than eager:
#
# * **An unknown signal is not a pass.** A check that could not be measured —
#   no frozen confidence floor, no calibrated Noul band, no state reading at
#   all — escalates and is listed in ``unmeasured``. No threshold is invented
#   (§13: "Keine globale Schwelle"), no confidence is guessed.
# * **Escalation is advisory.** The result says "System 2 should look at this";
#   it never authorizes an action, never widens ``allowed_kinds`` and never
#   marks anything complete (§50/§34).

ESCALATION_TRIGGERS: tuple[str, ...] = (
    "insufficient_state",
    "low_choice_or_score_confidence",
    "noul_inside_calibrated_uncertainty_band",
    "contradicting_answers",
    "new_unknown_ui",
    "missing_candidate_action",
    "complex_cause",
    "research_needed",
    "destructive_high_risk_action",
    "goal_interpretation",
    "policy_ambiguity",
    "user_preference_needed",
    "app_version_outside_subtool_applicability",
)

#: One line per trigger, machine-first (the goal's §12 list kept as checkable data — the
#: reasons are shown to the user, so they are English like everything else we surface).
ESCALATION_TRIGGER_REASONS: dict[str, str] = {
    "insufficient_state": "insufficient state",
    "low_choice_or_score_confidence": "low choice/score confidence",
    "noul_inside_calibrated_uncertainty_band": (
        "Noul inside the calibrated uncertainty band"
    ),
    "contradicting_answers": "contradicting answers",
    "new_unknown_ui": "new unknown UI",
    "missing_candidate_action": "missing candidate action",
    "complex_cause": "complex cause",
    "research_needed": "research needed",
    "destructive_high_risk_action": "destructive/high-risk action",
    "goal_interpretation": "goal interpretation",
    "policy_ambiguity": "policy ambiguity",
    "user_preference_needed": "user preference needed",
    "app_version_outside_subtool_applicability": (
        "app version outside subtool applicability"
    ),
}

#: Noul pairs whose joint high reading is a contradiction (never both true in a
#: coherent step). Reading them is what turns "the model is unsure" into
#: "the model contradicts itself".
CONTRADICTORY_QUESTION_PAIRS: tuple[tuple[str, str], ...] = (
    (
        "computer_use.post.expected_transition_occurred",
        "computer_use.post.unexpected_transition_occurred",
    ),
    ("computer_use.pre.correct_window", "computer_use.post.wrong_target_changed"),
    ("computer_use.post.previous_action_succeeded", "computer_use.post.wrong_target_changed"),
    ("computer_use.pre.correct_application", "computer_use.post.wrong_target_changed"),
)

#: Both sides of a pair count as "high" above this reading. A pair reading is
#: *compared*, never averaged into an invented score.
CONTRADICTION_READING = 0.5


@dataclass
class EscalationSignals:
    """The signals a caller has actually observed (``None`` = not measured).

    ``None`` is deliberate: it means "we do not know", and the decision
    escalates instead of treating an unknown as a pass.
    """

    state_sufficient: bool | None = None
    unknown_ui: bool = False
    candidate_action_available: bool | None = None
    complex_cause: bool = False
    research_needed: bool = False
    destructive_or_high_risk: bool = False
    goal_interpretation_needed: bool = False
    policy_ambiguous: bool = False
    user_preference_needed: bool = False
    app_version_applicable: bool | None = None
    noul_value: float | None = None
    noul_question_id: str = ""


@dataclass(frozen=True)
class EscalationDecision:
    """One System-2 escalation reading: escalate or not, and why (§12)."""

    escalate: bool
    triggers: tuple[str, ...] = ()
    checks: Mapping[str, bool | None] = field(default_factory=dict)
    unmeasured: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    profile_id: str = ""
    advisory_only: bool = True
    authorizes_action: bool = False

    def as_dict(self) -> dict:
        return {
            "escalate": self.escalate,
            "triggers": list(self.triggers),
            "checks": {key: value for key, value in self.checks.items()},
            "unmeasured": list(self.unmeasured),
            "reasons": list(self.reasons),
            "profile_id": self.profile_id,
            "advisory_only": self.advisory_only,
            "authorizes_action": self.authorizes_action,
        }


def _noul(evaluation, question_id: str) -> float | None:
    """Read one Noul value from an evaluation, or ``None`` (never invented)."""
    if evaluation is None:
        return None
    value = getattr(evaluation, "noul", None)
    if callable(value):
        try:
            found = value(question_id)
        except Exception:  # noqa: BLE001 — a provider-shaped double may differ
            found = None
        if found is not None:
            return _numeric(found)
    answers = dict(getattr(evaluation, "answers", {}) or {})
    answer = answers.get(question_id)
    plain = getattr(answer, "value", None)
    return _numeric(plain) if isinstance(plain, (int, float)) else None


def contradicting_answers(evaluation) -> tuple[tuple[str, str], ...]:
    """Declared Noul pairs that read high on both sides at once (§12)."""
    found: list[tuple[str, str]] = []
    for left, right in CONTRADICTORY_QUESTION_PAIRS:
        left_value = _noul(evaluation, left)
        right_value = _noul(evaluation, right)
        if left_value is None or right_value is None:
            continue
        if left_value >= CONTRADICTION_READING and right_value >= CONTRADICTION_READING:
            found.append((left, right))
    return tuple(found)


def _confidence_floor(profile) -> float | None:
    """The frozen confidence floor of a profile — never a built-in default."""
    if profile is None:
        return None
    for attribute in ("choice_threshold", "confidence_floor"):
        value = getattr(profile, attribute, None)
        if isinstance(value, (int, float)) and float(value) > 0.0:
            return float(value)
    return None


def _numeric(value: object) -> float:
    """A float from anything a provider-shaped double might carry."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _uncertainty_band(profile) -> tuple[float, float] | None:
    """The calibrated Noul uncertainty band of a frozen profile, if it has one."""
    if profile is None:
        return None
    band = getattr(profile, "noul_band", None)
    if band is None:
        return None
    try:
        low, high = (_numeric(band[0]), _numeric(band[1]))
    except (TypeError, ValueError, IndexError):
        return None
    if not 0.0 <= low <= high <= 1.0:
        return None
    return (low, high)


def _check_confidence(evaluation, profile) -> bool | None:
    floor = _confidence_floor(profile)
    if floor is None:
        return None
    readings: list[float] = []
    for question_id in getattr(evaluation, "answers", {}) or {}:
        if not question_id.startswith("computer_use."):
            continue
        confidence = None
        getter = getattr(evaluation, "confidence", None)
        if callable(getter):
            confidence = getter(question_id)
        if confidence is None:
            answer = dict(getattr(evaluation, "answers", {}) or {})[question_id]
            raw = getattr(answer, "confidence", None)
            confidence = float(raw) if isinstance(raw, (int, float)) else None
        if confidence is not None:
            readings.append(float(confidence))
    if not readings:
        return None
    return min(readings) >= floor


def system_two_escalation_decision(
    *,
    profile=None,
    evaluation=None,
    signals: EscalationSignals | None = None,
    allowed_candidates=(),
    context: str = "",
) -> EscalationDecision:
    """Check all 13 §12 triggers and decide whether System 2 must take over.

    The decision escalates when a trigger fires **or** when a check could not be
    measured at all — an unknown state, a missing frozen profile, an unreadable
    confidence. Guessing is exactly what this function must not do.
    """
    signals = signals or EscalationSignals()
    approved = tuple(str(item) for item in (allowed_candidates or ()))
    band = _uncertainty_band(profile)
    floor = _confidence_floor(profile)
    contradictions = contradicting_answers(evaluation)

    checks: dict[str, bool | None] = {
        # True = "measured and no reason to escalate" for this trigger.
        "insufficient_state": (
            signals.state_sufficient if signals.state_sufficient is not None else None
        ),
        "low_choice_or_score_confidence": _check_confidence(evaluation, profile),
        "noul_inside_calibrated_uncertainty_band": (
            None
            if band is None or signals.noul_value is None
            else not (band[0] <= _numeric(signals.noul_value) <= band[1])
        ),
        "contradicting_answers": None if evaluation is None else not contradictions,
        "new_unknown_ui": not signals.unknown_ui,
        "missing_candidate_action": (
            None
            if signals.candidate_action_available is None
            else bool(signals.candidate_action_available) and bool(approved)
        ),
        "complex_cause": not signals.complex_cause,
        "research_needed": not signals.research_needed,
        "destructive_high_risk_action": not signals.destructive_or_high_risk,
        "goal_interpretation": not signals.goal_interpretation_needed,
        "policy_ambiguity": not signals.policy_ambiguous,
        "user_preference_needed": not signals.user_preference_needed,
        "app_version_outside_subtool_applicability": signals.app_version_applicable,
    }

    triggers: list[str] = []
    unmeasured: list[str] = []
    reasons: list[str] = []
    for trigger in ESCALATION_TRIGGERS:
        state = checks[trigger]
        if state is True:
            continue
        triggers.append(trigger)
        if state is None:
            unmeasured.append(trigger)
            reasons.append(
                f"{trigger}: {ESCALATION_TRIGGER_REASONS[trigger]} — could not be measured, "
                "and an unmeasured signal is not a pass"
            )
        else:
            reasons.append(f"{trigger}: {ESCALATION_TRIGGER_REASONS[trigger]}")
    if evaluation is None:
        reasons.append(
            "no System-One evaluation is available — escalation is the honest reading, "
            "not a guessed continuation"
        )
    if plan_note := _profile_note(profile, floor, band):
        reasons.append(plan_note)

    return EscalationDecision(
        escalate=bool(triggers),
        triggers=tuple(triggers),
        checks=checks,
        unmeasured=tuple(unmeasured),
        reasons=tuple(reasons),
        profile_id=str(
            getattr(profile, "profile_id", "") or getattr(profile, "threshold_profile_id", "")
        ),
        advisory_only=True,
        authorizes_action=False,
    )


def _profile_note(profile, floor: float | None, band: tuple[float, float] | None) -> str:
    """Say out loud which calibration is missing — never substitute a default."""
    if profile is None:
        return (
            "no frozen threshold profile was supplied: no confidence floor and no Noul "
            "uncertainty band exist, so nothing is assumed (§13)"
        )
    missing: list[str] = []
    if floor is None:
        missing.append("confidence floor")
    if band is None:
        missing.append("noul uncertainty band")
    if not missing:
        return ""
    return (
        f"profile {getattr(profile, 'profile_id', '')!r} carries no calibrated "
        f"{' and '.join(missing)} — that part of the decision stays unmeasured"
    )


__all__ = [
    "CONTRADICTORY_QUESTION_PAIRS",
    "ESCALATION_TRIGGER_REASONS",
    "ESCALATION_TRIGGERS",
    "EscalationDecision",
    "EscalationSignals",
    "contradicting_answers",
    "system_two_escalation_decision",
]
