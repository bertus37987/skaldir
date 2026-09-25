"""The visible host corner indicator (DESK-03) — a real window, or an honest refusal.

``03-TUI-ENTSCHEIDUNGEN.md`` §6 is explicit about what this is:

* a **small status bar in one corner**, three short lines::

      Skaldir controls the desktop
      Task: Edit video
      Esc, Esc to pause

* visible **while control is held**, "auch ohne fokussierte TUI" — so a terminal footer is
  not enough;
* it must not cover the target application's important UI, must not steal focus, and must
  fit the measured backend;
* the states are exactly **controls / waits / paused**, and "controls" only with a valid
  lease.

What is deliberately missing from this module: any claim it cannot back. Creating a window
needs a display server, and there are three honest outcomes — ``visible``, ``unsupported``
and ``unavailable`` — never a silent no-op that the caller reports as "indicator shown".

Backend note (measured 25.09.2026, this host): GNOME on **Wayland** with XWayland. The
window here is an X11 override-redirect window, which appears on the desktop through
XWayland and is positioned/sticky/above via EWMH hints. A *native* Wayland layer-shell
surface would be the better integration and is **not** what this is; :func:`status` says
``x11-xwayland`` so nobody mistakes it for one.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .desktop_lease import IndicatorState, LeaseState, indicator_lines

__all__ = [
    "BOX_MARGIN_PX",
    "DEFAULT_WIDTH_CELLS",
    "FontMetrics",
    "HostIndicator",
    "IndicatorGeometry",
    "IndicatorStatus",
    "LINE_HEIGHT_PX",
    "layout_geometry",
    "render_box",
    "status",
]

#: Three short lines (DESK-03), one column of text.
DEFAULT_WIDTH_CELLS = 34
LINE_HEIGHT_PX = 18
BOX_MARGIN_PX = 12
_GLYPH_WIDTH_PX = 8


@dataclass(frozen=True)
class FontMetrics:
    """Enough of a font to size the box; never a guess about the real font."""

    glyph_width_px: int = _GLYPH_WIDTH_PX
    line_height_px: int = LINE_HEIGHT_PX

    def text_width(self, cells: int) -> int:
        return max(1, int(cells)) * int(self.glyph_width_px)


@dataclass(frozen=True)
class IndicatorGeometry:
    """Where the box goes and how big it is.

    Corner placement is the confirmed detail ("kleine Statusleiste in einer Ecke"); the
    concrete corner is an implementation choice and is reported, so a user who wants the
    other one can ask for it rather than discovering it.
    """

    screen_w: int
    screen_h: int
    x: int
    y: int
    w: int
    h: int
    corner: str = "bottom-right"

    def to_dict(self) -> dict[str, Any]:
        return {"screen": [self.screen_w, self.screen_h], "x": self.x, "y": self.y,
                "w": self.w, "h": self.h, "corner": self.corner}


def layout_geometry(screen_w: int, screen_h: int, *, lines: int = 3,
                    width_cells: int = DEFAULT_WIDTH_CELLS, corner: str = "bottom-right",
                    margin: int = BOX_MARGIN_PX,
                    metrics: FontMetrics | None = None) -> IndicatorGeometry:
    """Place a ``lines``-line box in a corner without ever leaving the screen.

    The box is clamped to the screen: on a small display it shrinks rather than drifting
    off-screen, and it never covers the whole screen (DESK-03: "Zielanwendung und wichtige
    UI-Elemente möglichst nicht verdecken").
    """
    m = metrics or FontMetrics()
    w = max(1, min(int(screen_w) - 2 * int(margin), m.text_width(width_cells)))
    h = max(1, min(int(screen_h) - 2 * int(margin), int(lines) * m.line_height_px + 8))
    if corner.startswith("top"):
        y = int(margin)
    else:
        y = max(0, int(screen_h) - int(margin) - h)
    if corner.endswith("left"):
        x = int(margin)
    else:
        x = max(0, int(screen_w) - int(margin) - w)
    return IndicatorGeometry(int(screen_w), int(screen_h), x, y, w, h, corner)


def render_box(info: IndicatorState, *, width_cells: int = DEFAULT_WIDTH_CELLS,
               unicode: bool = True) -> list[str]:
    """The confirmed three **content** lines, exactly ``width_cells`` each.

    ``03-TUI-ENTSCHEIDUNGEN.md`` §6 shows exactly three lines (state, task, stop hint).
    :func:`skaldir.desktop_lease.indicator_lines` additionally draws a horizontal rule
    between them; that is *decoration*, not content, and this window paints its own rule
    (see ``HostIndicator._draw``). So the shared renderer is reused for the wording and the
    separator is filtered out here — which keeps the three surfaces (TUI, CLI, host window)
    saying the same words while letting each draw its own furniture.
    """
    lines = indicator_lines(info, width=width_cells, unicode=unicode)
    bar_chars = set("─-_=~")
    return [line for line in lines
            if line.strip() and not set(line.strip()) <= bar_chars]


@dataclass
class IndicatorStatus:
    """What actually happened when the indicator was asked to appear."""

    state: str = "unavailable"
    reason: str = ""
    backend: str = ""
    geometry: dict[str, Any] | None = None
    lines: list[str] = field(default_factory=list)
    since: float = 0.0

    @property
    def visible(self) -> bool:
        return self.state == "visible"

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "reason": self.reason, "backend": self.backend,
                "visible": self.visible, "geometry": self.geometry, "lines": list(self.lines),
                "since": self.since}


class HostIndicator:
    """The corner window. Create it, update it, take it away — or say why not.

    The window is **override-redirect**: the window manager is not asked to decorate or
    position it, which is what keeps it from stealing focus or becoming a taskbar entry.
    EWMH hints (``_NET_WM_STATE_ABOVE``, ``_NET_WM_STATE_STICKY``,
    ``_NET_WM_WINDOW_TYPE_DOCK``) are set best-effort; a compositor that ignores them
    leaves a plain small window, which is still a correct, visible indicator.
    """

    def __init__(self, *, corner: str = "bottom-right",
                 width_cells: int = DEFAULT_WIDTH_CELLS, unicode: bool = True,
                 margin: int = BOX_MARGIN_PX) -> None:
        self.corner = corner
        self.width_cells = int(width_cells)
        self.unicode = bool(unicode)
        self.margin = int(margin)
        self._display: Any = None
        self._window: Any = None
        self._gc: Any = None
        self._font: Any = None
        self._shown = IndicatorStatus()
        self._lines: list[str] = []

    # -- status -------------------------------------------------------------

    def status(self) -> IndicatorStatus:
        """What is true right now — never an optimistic guess."""
        return self._shown

    def _refuse(self, reason: str, *, backend: str = "") -> IndicatorStatus:
        self._shown = IndicatorStatus(state="unavailable", reason=reason, backend=backend,
                                      lines=list(self._lines), since=time.time())
        return self._shown

    # -- lifecycle ----------------------------------------------------------

    def show(self, info: IndicatorState) -> IndicatorStatus:
        """Draw (or redraw) the indicator for ``info``.

        Returns the status, and the caller must read it: ``visible`` is the only state in
        which anything is on screen.
        """
        self._lines = render_box(info, width_cells=self.width_cells, unicode=self.unicode)
        display_name = os.environ.get("DISPLAY", "")
        if not display_name:
            return self._refuse(
                "no DISPLAY in this environment — a window cannot be created", backend="none")
        try:
            from Xlib import X, display as _xdisplay
        except Exception as exc:  # noqa: BLE001 — an optional extra, refused structurally
            return self._refuse(
                f"python-xlib is not importable: {type(exc).__name__}: {exc}",
                backend="x11-xwayland")
        try:
            if self._display is None:
                self._display = _xdisplay.Display(display_name)
            screen = self._display.screen()
            geom = layout_geometry(
                screen.width_in_pixels, screen.height_in_pixels,
                lines=len(self._lines), width_cells=self.width_cells,
                corner=self.corner, margin=self.margin,
            )
            if self._window is None:
                self._window = screen.root.create_window(
                    geom.x, geom.y, geom.w, geom.h, 0, screen.root_depth,
                    X.InputOutput, X.CopyFromParent,
                    override_redirect=1,
                    background_pixel=self._pixel(screen, "#111111"),
                    event_mask=X.ExposureMask,
                )
                self._set_hints(geom)
                self._window.map()
            else:
                self._window.configure(x=geom.x, y=geom.y, width=geom.w, height=geom.h)
            self._draw(geom)
            self._display.sync()
            self._shown = IndicatorStatus(
                state="visible", reason="", backend="x11-xwayland",
                geometry=geom.to_dict(), lines=list(self._lines), since=time.time(),
            )
            return self._shown
        except Exception as exc:  # noqa: BLE001 — a display failure must not crash a run
            return self._refuse(
                f"could not create the indicator window: {type(exc).__name__}: {exc}",
                backend="x11-xwayland")

    def update(self, info: IndicatorState) -> IndicatorStatus:
        """Redraw with new text; creates the window when there is none yet."""
        return self.show(info)

    def hide(self) -> IndicatorStatus:
        """Take the window away. The status afterwards is honest, not 'idle on screen'."""
        try:
            if self._window is not None:
                self._window.unmap()
                self._display.sync()
        except Exception:  # noqa: BLE001 — hiding must never raise
            pass
        self._shown = IndicatorStatus(state="hidden", reason="released by the owner",
                                      backend=self._shown.backend, since=time.time())
        return self._shown

    def close(self) -> None:
        """Release the connection entirely."""
        try:
            if self._window is not None:
                self._window.destroy()
            if self._display is not None:
                self._display.close()
        except Exception:  # noqa: BLE001 — teardown must never raise
            pass
        self._window = None
        self._display = None
        self._shown = IndicatorStatus(state="unavailable", reason="closed",
                                      since=time.time())

    # -- drawing ------------------------------------------------------------

    @staticmethod
    def _pixel(screen: Any, hex_color: str) -> int:
        try:
            cmap = screen.default_colormap
            return cmap.alloc_named_color(hex_color).pixel
        except Exception:  # noqa: BLE001 — fall back to black
            return 0

    def _set_hints(self, geom: Any) -> None:
        """Best-effort EWMH: above, sticky, dock-like, and never focused."""
        try:
            from Xlib import Xatom

            state = self._display.intern_atom("_NET_WM_STATE")
            atoms = [self._display.intern_atom(n) for n in
                     ("_NET_WM_STATE_ABOVE", "_NET_WM_STATE_STICKY")]
            self._window.change_property(state, Xatom.ATOM, 32, atoms)
            self._window.change_property(
                self._display.intern_atom("_NET_WM_WINDOW_TYPE"),
                Xatom.ATOM, 32,
                [self._display.intern_atom("_NET_WM_WINDOW_TYPE_DOCK")])
            label = b"Skaldir desktop indicator"
            # Both names, on purpose: `_NET_WM_NAME` is what EWMH-aware tools read, and
            # `WM_NAME` is what `get_wm_name()` and older window walkers read. Setting only
            # the first made the window invisible to exactly the check that was supposed to
            # prove it was there (measured 25.09.2026).
            self._window.change_property(
                self._display.intern_atom("_NET_WM_NAME"),
                self._display.intern_atom("UTF8_STRING"), 8, list(label))
            try:
                self._window.set_wm_name("Skaldir desktop indicator")
            except Exception:  # noqa: BLE001 — best effort alongside _NET_WM_NAME
                pass
        except Exception:  # noqa: BLE001 — hints are best-effort, the window still shows
            pass

    def _draw(self, geom: Any) -> None:
        if self._gc is None:
            self._gc = self._window.create_gc(
                foreground=self._pixel(self._display.screen(), "#e8e8e8"),
                background=self._pixel(self._display.screen(), "#111111"),
                graphics_exposures=0,
            )
        try:
            if self._font is None:
                self._font = self._display.open_font("fixed")
                self._gc.change(font=self._font)
        except Exception:  # noqa: BLE001 — a server-side default font is fine
            pass
        self._window.clear_area(0, 0, geom.w, geom.h)
        line_h = FontMetrics().line_height_px
        for index, line in enumerate(self._lines):
            try:
                self._window.draw_text(self._gc, 6, 14 + index * line_h,
                                       line.encode("ascii", "replace"))
            except Exception:  # noqa: BLE001 — one unreadable line must not kill the rest
                continue
        # a thin rule so the box reads as a bar, not as stray text (DESK-03)
        try:
            self._window.poly_fill(self._gc, [(0, 0), (geom.w, 0), (geom.w, 2), (0, 2)])
        except Exception:  # noqa: BLE001
            pass


def status() -> dict[str, Any]:
    """The backend this module would use here — measured, not assumed."""
    display_name = os.environ.get("DISPLAY", "")
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    try:
        import Xlib  # noqa: F401
        xlib = True
    except Exception:  # noqa: BLE001
        xlib = False
    if not display_name:
        state, reason = "unavailable", "no DISPLAY in this environment"
    elif not xlib:
        state, reason = "unavailable", "python-xlib is not installed"
    else:
        state, reason = "available", ""
    return {
        "state": state,
        "reason": reason,
        "backend": "x11-xwayland",
        "native_wayland_layer_shell": False,
        "note": (
            "this indicator is an X11 window shown through XWayland; it is NOT a native "
            "Wayland layer-shell surface. On a Wayland-only session without XWayland it "
            "reports unavailable rather than pretending to be visible."
        ),
        "display": display_name,
        "wayland_display": bool(wayland),
    }
