"""Platform adapters for computer use — one interface, every desktop.

Why this module exists: ``xdotool`` alone cannot drive a Wayland-native compositor
(Hyprland, GNOME Wayland, KWin Wayland). Measured on the owner's machine: a Zen window
running Wayland-native was invisible to ``xdotool search`` so the end_state never matched
and the agent re-launched the browser 3×.  Omarchy (DHH's Arch + Hyprland + Quickshell)
adds ``hyprctl`` IPC and an ``omarchy`` CLI that are strictly better than xdotool for
window control.  macOS needs ``osascript``/``cliclick``; Windows needs UI Automation.

The contract: every adapter exposes the same seven verbs — list windows, focus, close,
send key, send text, pointer, screenshot, launch — so ``cu_host_session`` can drive
**any** app (Blender, Docker Desktop, DaVinci Resolve, a browser) without app-specific
built-ins.  Built-ins are shortcuts on top of these primitives, never a requirement.
"""

from __future__ import annotations

import json
import os
import platform as _platform_mod
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "WindowInfo",
    "PlatformInfo",
    "PlatformAdapter",
    "detect_platform",
    "get_adapter",
    "install_omarchy_skills",
    "OMARCHY_HOTKEYS",
    "normalize_key",
    "resolve_app_command",
]


# --------------------------------------------------------------------------- #
# Data                                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class WindowInfo:
    """One top-level window, regardless of toolkit or display server."""

    id: str
    title: str
    app_class: str = ""
    pid: int = 0
    workspace: str = ""
    floating: bool = False
    focused: bool = False
    xwayland: bool = False
    source: str = ""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0


@dataclass
class PlatformInfo:
    """What this host actually is."""

    os: str = "linux"  # linux | macos | windows
    distro: str = ""  # omarchy | ubuntu | fedora | rhel | arch | debian | ...
    compositor: str = ""  # hyprland | gnome | kde | x11 | aqua | dwl | ...
    display: str = ""
    tools: dict[str, bool] = field(default_factory=dict)

    @property
    def is_omarchy(self) -> bool:
        return self.distro == "omarchy"

    @property
    def is_wayland(self) -> bool:
        return (
            self.compositor in ("hyprland", "sway", "gnome", "kde", "dwl")
            and self.os == "linux"
        )


# --------------------------------------------------------------------------- #
# Detection                                                                    #
# --------------------------------------------------------------------------- #

_OMARCHY_MARKERS = (
    "/etc/omarchy-release",
    os.path.expanduser("~/.config/omarchy"),
    os.path.expanduser("~/.local/share/omarchy"),
    "/usr/bin/omarchy",
    "/usr/local/bin/omarchy",
    "/usr/bin/omarchy-version",
    "/usr/local/bin/omarchy-version",
)


def _detect_distro() -> str:
    """Identify the Linux distribution (or macOS / Windows)."""
    if _platform_mod.system() == "Darwin":
        return "macos"
    if _platform_mod.system() == "Windows":
        return "windows"
    # Omarchy first: it is Arch-based so /etc/os-release says "arch"
    omarchy_env = (os.environ.get("OMARCHY_PATH") or "").strip()
    if omarchy_env and Path(omarchy_env).exists():
        return "omarchy"
    for marker in _OMARCHY_MARKERS:
        if Path(marker).exists():
            return "omarchy"
    os_release = Path("/etc/os-release")
    if os_release.exists():
        text = os_release.read_text(encoding="utf-8", errors="replace").lower()
        for name in (
            "omarchy", "ubuntu", "debian", "fedora", "rhel", "centos", "rocky", "almalinux",
            "arch", "manjaro", "opensuse", "nixos", "gentoo", "void",
        ):
            if re.search(rf'^id=["\']?{re.escape(name)}["\']?\s*$', text, re.MULTILINE):
                return name if name not in ("centos", "rocky", "almalinux") else "rhel"
        for name in ("ubuntu", "debian", "fedora", "rhel", "arch"):
            if f"id_like={name}" in text or f'id_like="{name}' in text:
                return name
    return "linux"


def _detect_compositor(distro: str) -> str:
    """Identify the display server / compositor."""
    if distro == "macos":
        return "aqua"
    if distro == "windows":
        return "win32"
    if distro == "omarchy":
        return "hyprland"
    # Hyprland / Sway / other wlroots
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if os.environ.get("SWAYSOCK"):
        return "sway"
    if os.environ.get("WLR_BACKENDS"):
        return "dwl"
    # GNOME / KDE via Wayland or X11
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    if "hyprland" in desktop:
        return "hyprland"
    if "sway" in desktop:
        return "sway"
    if "gnome" in desktop or os.environ.get("GNOME_DESKTOP_SESSION_ID"):
        return "gnome"
    if "kde" in desktop or "plasma" in desktop or os.environ.get("KDE_FULL_SESSION"):
        return "kde"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def detect_platform() -> PlatformInfo:
    """Detect OS, distro, compositor and which control tools are installed."""
    distro = _detect_distro()
    compositor = _detect_compositor(distro)
    tools = {
        name: bool(shutil.which(name))
        for name in (
            "hyprctl", "wtype", "ydotool", "ydotoold", "wlrctl",
            "xdotool", "wmctrl", "xprop", "xwd", "import",
            "scrot", "maim", "gnome-screenshot",
            "grim", "slurp", "hyprshot", "wl-copy", "wl-paste",
            "gdbus", "qdbus", "kdotool",
            "osascript", "cliclick",
            "omarchy", "omarchy-cmd-screenshot",
        )
    }
    return PlatformInfo(
        os=("macos" if distro == "macos" else "windows" if distro == "windows" else "linux"),
        distro=distro,
        compositor=compositor,
        display=str(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or ""),
        tools=tools,
    )


# --------------------------------------------------------------------------- #
# Key normalisation                                                            #
# --------------------------------------------------------------------------- #

#: logical name → per-tool tokens.  ``xdotool`` / ``wtype`` / ``ydotool`` each spell
#: modifier keys differently; the agent always sends a *logical* name (``ctrl+l``).
_KEY_MAP_XDOTOOL: dict[str, str] = {
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "super": "super", "meta": "super", "win": "super",
    "shift": "shift",
    "return": "Return", "enter": "Return", "tab": "Tab", "escape": "Escape", "esc": "Escape",
    "space": "space", "backspace": "BackSpace", "delete": "Delete", "del": "Delete",
    "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4", "f5": "F5",
    "f6": "F6", "f7": "F7", "f8": "F8", "f9": "F9", "f10": "F10",
    "f11": "F11", "f12": "F12",
}

_KEY_MAP_WTYPE: dict[str, str] = {
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "super": "super", "meta": "super", "win": "super",
    "shift": "shift",
    "return": "Return", "enter": "Return", "tab": "Tab", "escape": "Escape", "esc": "Escape",
    "space": "space", "backspace": "BackSpace", "delete": "Delete", "del": "Delete",
    "home": "Home", "end": "End", "pageup": "Page_Up", "pagedown": "Page_Down",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4", "f5": "F5",
    "f6": "F6", "f7": "F7", "f8": "F8", "f9": "F9", "f10": "F10",
    "f11": "F11", "f12": "F12",
}

#: Well-known application binary aliases across Linux distributions
#: (e.g. Fedora, Omarchy Hyprland, Debian/Ubuntu)
APP_ALIASES: dict[str, list[str]] = {
    "google-chrome": [
        "google-chrome", "google-chrome-stable", "chromium-browser",
        "chromium", "brave-browser", "zen-browser", "firefox",
    ],
    "chrome": [
        "google-chrome", "google-chrome-stable", "chromium-browser",
        "chromium", "brave-browser", "zen-browser", "firefox",
    ],
    "chromium": [
        "chromium-browser", "chromium", "google-chrome", "brave-browser", "firefox",
    ],
    "brave": ["brave-browser", "brave"],
    "zen": ["zen-browser", "zen"],
    "firefox": ["firefox", "firefox-esr"],
    "browser": [
        "zen-browser", "firefox", "chromium-browser", "brave-browser", "google-chrome",
    ],
    "gnome-terminal": [
        "ptyxis", "gnome-terminal", "foot", "kitty", "alacritty", "konsole", "xterm",
    ],
    "terminal": [
        "ptyxis", "gnome-terminal", "foot", "kitty", "alacritty", "konsole", "xterm",
    ],
    "gedit": ["gnome-text-editor", "gedit", "kate", "xed", "mousepad"],
    "text-editor": ["gnome-text-editor", "gedit", "kate", "xed", "mousepad"],
    "calc": ["libreoffice", "localc", "gnumeric"],
    "libreoffice-calc": ["libreoffice", "localc"],
    "writer": ["libreoffice", "lowriter"],
    "libreoffice-writer": ["libreoffice", "lowriter"],
    "files": ["nautilus", "nemo", "thunar", "dolphin"],
    "nautilus": ["nautilus", "nemo", "thunar", "dolphin"],
}


def resolve_app_command(argv: Sequence[str]) -> list[str]:
    """Resolve application names to installed binaries across Linux distros.

    E.g. on Fedora 'google-chrome' -> 'chromium-browser' / 'zen-browser',
    'gnome-terminal' -> 'ptyxis', 'calc' -> ['libreoffice', '--calc'].
    """
    if not argv:
        return []
    parts = [str(p) for p in argv]
    cmd = parts[0]
    if shutil.which(cmd):
        return parts
    if cmd.startswith(("http://", "https://")):
        return ["xdg-open", *parts]

    candidates = APP_ALIASES.get(cmd.lower(), [])
    for candidate in candidates:
        if shutil.which(candidate):
            new_parts = [candidate, *parts[1:]]
            if (
                cmd.lower() in ("calc", "libreoffice-calc")
                and candidate == "libreoffice"
                and "--calc" not in new_parts
            ):
                new_parts.insert(1, "--calc")
            elif (
                cmd.lower() in ("writer", "libreoffice-writer")
                and candidate == "libreoffice"
                and "--writer" not in new_parts
            ):
                new_parts.insert(1, "--writer")
            return new_parts
    return parts


def normalize_key(key: str, tool: str = "xdotool") -> list[str]:
    """Turn ``ctrl+l`` into the token list the target tool expects.

    Returns a list like ``["ctrl", "l"]`` for xdotool or ``["-M", "ctrl", "-k", "l", "-m", "ctrl"]``
    is the *caller's* job — this returns the bare tokens.
    """
    mapping = _KEY_MAP_WTYPE if tool == "wtype" else _KEY_MAP_XDOTOOL
    parts = re.split(r"[+]", (key or "").strip())
    return [mapping.get(p.lower(), p) for p in parts if p]


def _xdotool_key_argv(key: str) -> list[str]:
    tokens = normalize_key(key, "xdotool")
    return ["xdotool", "key", "--clearmodifiers", "+".join(tokens)]


def _wtype_key_argv(key: str) -> list[str]:
    tokens = normalize_key(key, "wtype")
    argv = ["wtype"]
    mods = [
        t for t in tokens[:-1]
        if t.lower() in ("ctrl", "control", "alt", "super", "meta", "shift")
    ]
    final = tokens[-1] if tokens else ""
    for mod in mods:
        argv += ["-M", mod.lower().replace("control", "ctrl").replace("meta", "super")]
    if final:
        argv += ["-k", final]
    for mod in reversed(mods):
        argv += ["-m", mod.lower().replace("control", "ctrl").replace("meta", "super")]
    return argv


def _ydotool_key_argv(key: str) -> list[str]:
    """Build ``ydotool key <evdev_code>:<press> ...`` argv using numeric evdev keycodes."""
    try:
        from .evdev_keys import key_event_argv

        return ["ydotool", "key", *key_event_argv(key)]
    except Exception:  # noqa: BLE001
        tokens = normalize_key(key, "xdotool")
        argv = ["ydotool", "key"]
        for t in tokens[:-1]:
            argv.append(t.lower() + ":1")
        if tokens:
            argv.append(tokens[-1])
        for t in reversed(tokens[:-1]):
            argv.append(t.lower() + ":0")
        return argv


def _ydotool_button_hex(button: int, mode: str = "click") -> str:
    """Convert 1-based X11 mouse button (1=left, 2=middle, 3=right) to ydotool hex mask.

    ``ydotool click --help``: 0x00=LEFT, 0x01=RIGHT, 0x02=MIDDLE; 0x40=down, 0x80=up, 0xC0=click.
    """
    btn_idx = {1: 0x00, 2: 0x02, 3: 0x01}.get(int(button or 1), 0x00)
    mask = {"click": 0xC0, "down": 0x40, "up": 0x80}.get(mode, 0xC0)
    return f"0x{mask | btn_idx:02X}"


def _hyprctl_window_selector(wid: str) -> str:
    """Normalize a window identifier into a Hyprland selector (``address:0x...``, ``pid:...``)."""
    raw = (wid or "").strip()
    if not raw:
        return "activewindow"
    if raw.startswith(("address:", "pid:", "class:", "title:", "initialclass:", "initialtitle:")):
        return raw
    if raw.startswith("0x") or all(c in "0123456789abcdefABCDEF" for c in raw):
        addr = raw if raw.startswith("0x") else f"0x{raw}"
        return f"address:{addr}"
    return f"title:{raw}"


def _hyprctl_sendshortcut_argv(key: str, window: str = "") -> list[str]:
    """Build ``hyprctl dispatch sendshortcut "<mods>, <key>, <target>"`` argv."""
    tokens = normalize_key(key, "wtype")
    mod_map = {
        "ctrl": "CTRL", "control": "CTRL",
        "alt": "ALT",
        "super": "SUPER", "meta": "SUPER", "win": "SUPER",
        "shift": "SHIFT",
    }
    mods: list[str] = []
    final = ""
    for t in tokens:
        low = t.lower()
        if low in mod_map:
            mods.append(mod_map[low])
        else:
            final = t
    target = _hyprctl_window_selector(window) if window else "activewindow"
    mod_str = " ".join(mods)
    return ["hyprctl", "dispatch", "sendshortcut", f"{mod_str}, {final}, {target}"]


# --------------------------------------------------------------------------- #
# Adapter base                                                                 #
# --------------------------------------------------------------------------- #


def _run(argv: Sequence[str], *, timeout: float = 8.0) -> tuple[int, str]:
    cmd = list(argv)
    env: dict[str, str] | None = None
    if cmd and cmd[0] == "ydotool" and not os.environ.get("YDOTOOL_SOCKET"):
        try:
            from .input_backend import MISSION_SOCKET, active_ydotool_socket, ensure_ydotoold

            ensure_ydotoold()
            sock = active_ydotool_socket() or (
                MISSION_SOCKET if Path(MISSION_SOCKET).exists() else ""
            )
            if sock:
                env = {**os.environ, "YDOTOOL_SOCKET": sock}
        except Exception:  # noqa: BLE001
            env = None
    try:
        proc = subprocess.run(  # nosec B603 — fixed argv, no shell
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _xlib_root_screenshot(target: Path, window_id: str = "") -> bool:
    """Capture root or window via python-xlib ``capture_window_image`` + ``encode_png``.

    On pure X11 / Xvfb, capturing ``root`` (``window_id=""``) succeeds directly.
    On Wayland + Xwayland (e.g. Fedora GNOME Wayland ``:0``), the Xwayland root surface
    has no backing pixmap (``BadMatch``), so we fall back to the largest viewable
    Xwayland window on the display.
    """
    display = str(os.environ.get("DISPLAY") or "").strip()
    if not display:
        return False
    try:
        from .computer_use import encode_png
        from .xlib_capture import XlibCaptureError, capture_window_image
    except Exception:  # noqa: BLE001
        return False

    img = None
    try:
        img = capture_window_image(display, window_id)
    except XlibCaptureError:
        if not window_id:
            try:
                from Xlib import X
                from Xlib import display as xdisplay

                conn = xdisplay.Display(display)
                try:
                    candidates: list[tuple[int, int]] = []
                    stack = list(conn.screen().root.query_tree().children)
                    while stack:
                        w = stack.pop()
                        try:
                            attrs = w.get_attributes()
                            geom = w.get_geometry()
                            if (
                                attrs.map_state == X.IsViewable
                                and getattr(attrs, "win_class", X.InputOutput) == X.InputOutput
                                and geom.width > 0
                                and geom.height > 0
                            ):
                                candidates.append((geom.width * geom.height, w.id))
                            children = w.query_tree().children
                            if children:
                                stack.extend(children)
                        except Exception:  # noqa: BLE001
                            continue
                finally:
                    conn.close()
                for _area, cand_wid in sorted(candidates, reverse=True):
                    try:
                        img = capture_window_image(display, str(cand_wid))
                        break
                    except XlibCaptureError:
                        continue
            except Exception:  # noqa: BLE001
                img = None
    except Exception:  # noqa: BLE001
        return False

    if img is None:
        return False
    try:
        png = encode_png(img)
        if not png:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(png)
        return target.is_file() and target.stat().st_size > 0
    except Exception:  # noqa: BLE001
        return False


class PlatformAdapter(ABC):
    """The seven verbs every desktop must support for computer use."""

    def __init__(self, info: PlatformInfo) -> None:
        self.info = info

    # -- windows -------------------------------------------------------------- #

    @abstractmethod
    def list_windows(self) -> list[WindowInfo]: ...

    @abstractmethod
    def get_active_window(self) -> WindowInfo | None: ...

    @abstractmethod
    def focus_window(self, wid: str) -> bool: ...

    @abstractmethod
    def close_window(self, wid: str) -> bool: ...

    # -- input ---------------------------------------------------------------- #

    @abstractmethod
    def send_key(self, key: str, window: str = "") -> bool: ...

    @abstractmethod
    def send_text(self, text: str, window: str = "") -> bool: ...

    @abstractmethod
    def mouse_move(self, x: int, y: int) -> bool: ...

    @abstractmethod
    def mouse_click(self, x: int, y: int, button: int = 1) -> bool: ...

    @abstractmethod
    def mouse_drag(
        self, x0: int, y0: int, x1: int, y1: int, button: int = 1
    ) -> bool: ...

    # -- capture / launch ----------------------------------------------------- #

    @abstractmethod
    def screenshot(self, path: str | Path) -> bool: ...

    @abstractmethod
    def launch_app(self, command: str | list[str]) -> bool: ...

    def hotkeys(self) -> dict[str, str]:
        """Platform / desktop-specific keyboard shortcuts for the computer-use skill."""
        return {}


# --------------------------------------------------------------------------- #
# Omarchy / Hyprland                                                           #
# --------------------------------------------------------------------------- #

#: Omarchy hotkeys from ``manual/07-hotkeys.md`` — the computer-use skill teaches the
#: agent these so it can drive the desktop like a user, not just send raw X11 keys.
OMARCHY_HOTKEYS: dict[str, str] = {
    "menu": "Super+Space",
    "apps_menu": "Super+Alt+Space",
    "system_menu": "Super+Escape",
    "terminal": "Super+Return",
    "tmux_terminal": "Super+Alt+Return",
    "agent_manager": "Super+Ctrl+Return",
    "browser": "Super+Shift+Return",
    "browser_private": "Super+Shift+Alt+B",
    "file_manager": "Super+Shift+F",
    "editor": "Super+Shift+N",
    "close_window": "Super+W",
    "close_all_windows": "Ctrl+Alt+Delete",
    "fullscreen": "Super+F",
    "toggle_floating": "Super+T",
    "workspace_1": "Super+1",
    "workspace_2": "Super+2",
    "workspace_3": "Super+3",
    "workspace_4": "Super+4",
    "move_to_workspace_1": "Super+Shift+1",
    "next_workspace": "Super+Tab",
    "prev_workspace": "Super+Shift+Tab",
    "focus_left": "Super+Left",
    "focus_right": "Super+Right",
    "focus_up": "Super+Up",
    "focus_down": "Super+Down",
    "swap_left": "Super+Shift+Left",
    "swap_right": "Super+Shift+Right",
    "copy": "Super+C",
    "cut": "Super+X",
    "paste": "Super+V",
    "clipboard_manager": "Super+Ctrl+V",
    "screenshot": "Print Screen",
    "screen_record": "Alt+Print Screen",
    "capture_menu": "Super+Ctrl+C",
    "theme_picker": "Super+Ctrl+Shift+Space",
    "calculator": "Super+Ctrl+Q",
    "emoji_picker": "Super+Ctrl+E",
    "audio_panel": "Super+Ctrl+A",
    "bluetooth_panel": "Super+Ctrl+B",
    "wifi_panel": "Super+Ctrl+W",
    "display_panel": "Super+Ctrl+D",
    "power_panel": "Super+Ctrl+P",
    "lock": "Super+Ctrl+L",
    "scratchpad": "Super+S",
    "zoom_in": "Super+Ctrl+Z",
    "zoom_out": "Super+Ctrl+Alt+Z",
    "docker": "Super+Shift+D",
    "music": "Super+Shift+M",
    "email": "Super+Shift+E",
    "calendar": "Super+Shift+C",
}


class HyprlandAdapter(PlatformAdapter):
    """Omarchy / Hyprland: ``hyprctl`` IPC + ``wtype``/``wlrctl``/``ydotool`` + ``grim``.

    ``hyprctl clients -j`` lists **all** windows including Wayland-native ones — the
    exact gap that made xdotool-driven observe() report ``(no windows)`` while Zen
    was plainly on screen (measured 2026-09-23).
    """

    def list_windows(self) -> list[WindowInfo]:
        if not self.info.tools.get("hyprctl", True):
            return []
        code, output = _run(["hyprctl", "clients", "-j"])
        if code != 0 or not output:
            return []
        try:
            clients = json.loads(output)
        except json.JSONDecodeError:
            return []
        active_id = ""
        acode, aout = _run(["hyprctl", "activewindow", "-j"])
        if acode == 0 and aout:
            try:
                active_id = str(json.loads(aout).get("address") or "")
            except json.JSONDecodeError:
                pass
        windows: list[WindowInfo] = []
        for c in clients:
            if not isinstance(c, dict):
                continue
            if not c.get("mapped") or c.get("hidden"):
                continue
            addr = str(c.get("address") or "")
            ws = c.get("workspace") or {}
            at = c.get("at") if isinstance(c.get("at"), (list, tuple)) else [0, 0]
            size = c.get("size") if isinstance(c.get("size"), (list, tuple)) else [0, 0]
            wx = int(at[0]) if len(at) >= 1 and isinstance(at[0], (int, float)) else 0
            wy = int(at[1]) if len(at) >= 2 and isinstance(at[1], (int, float)) else 0
            ww = int(size[0]) if len(size) >= 1 and isinstance(size[0], (int, float)) else 0
            wh = int(size[1]) if len(size) >= 2 and isinstance(size[1], (int, float)) else 0
            windows.append(WindowInfo(
                id=addr,
                title=str(c.get("title") or ""),
                app_class=str(c.get("class") or ""),
                pid=int(c.get("pid") or 0),
                workspace=str(ws.get("name") or ws.get("id") or ""),
                floating=bool(c.get("floating")),
                focused=(addr == active_id),
                xwayland=bool(c.get("xwayland")),
                source="hyprctl",
                x=wx,
                y=wy,
                width=ww,
                height=wh,
            ))
        return windows

    def get_active_window(self) -> WindowInfo | None:
        if not self.info.tools.get("hyprctl", True):
            return None
        code, output = _run(["hyprctl", "activewindow", "-j"])
        if code != 0 or not output:
            return None
        try:
            c = json.loads(output)
        except json.JSONDecodeError:
            return None
        if not isinstance(c, dict) or not c.get("address"):
            return None
        ws = c.get("workspace") or {}
        at = c.get("at") if isinstance(c.get("at"), (list, tuple)) else [0, 0]
        size = c.get("size") if isinstance(c.get("size"), (list, tuple)) else [0, 0]
        wx = int(at[0]) if len(at) >= 1 and isinstance(at[0], (int, float)) else 0
        wy = int(at[1]) if len(at) >= 2 and isinstance(at[1], (int, float)) else 0
        ww = int(size[0]) if len(size) >= 1 and isinstance(size[0], (int, float)) else 0
        wh = int(size[1]) if len(size) >= 2 and isinstance(size[1], (int, float)) else 0
        return WindowInfo(
            id=str(c["address"]),
            title=str(c.get("title") or ""),
            app_class=str(c.get("class") or ""),
            pid=int(c.get("pid") or 0),
            workspace=str(ws.get("name") or ws.get("id") or ""),
            floating=bool(c.get("floating")),
            focused=True,
            xwayland=bool(c.get("xwayland")),
            source="hyprctl",
            x=wx,
            y=wy,
            width=ww,
            height=wh,
        )

    def focus_window(self, wid: str) -> bool:
        if not wid:
            return False
        selector = _hyprctl_window_selector(wid)
        code, _ = _run(["hyprctl", "dispatch", "focuswindow", selector])
        return code == 0

    def close_window(self, wid: str) -> bool:
        if not wid:
            return False
        selector = _hyprctl_window_selector(wid)
        code, _ = _run(["hyprctl", "dispatch", "closewindow", selector])
        return code == 0

    def send_key(self, key: str, window: str = "") -> bool:
        if window:
            self.focus_window(window)
        if self.info.tools.get("wtype"):
            code, _ = _run(_wtype_key_argv(key))
            if code == 0:
                return True
        if self.info.tools.get("hyprctl"):
            code, _ = _run(_hyprctl_sendshortcut_argv(key, window))
            if code == 0:
                return True
        if self.info.tools.get("ydotool"):
            code, _ = _run(_ydotool_key_argv(key))
            if code == 0:
                return True
        # last resort: XWayland via xdotool (only works for xwayland windows)
        code, _ = _run(_xdotool_key_argv(key))
        return code == 0

    def send_text(self, text: str, window: str = "") -> bool:
        if window:
            self.focus_window(window)
        if self.info.tools.get("wtype"):
            code, _ = _run(["wtype", "--", text])
            if code == 0:
                return True
        if self.info.tools.get("ydotool"):
            code, _ = _run(["ydotool", "type", "--key-delay", "20", "--", text])
            if code == 0:
                return True
        if self.info.tools.get("wl-copy") and self.info.tools.get("hyprctl"):
            c1, _ = _run(["wl-copy", "--", text])
            if c1 == 0:
                c2, _ = _run(_hyprctl_sendshortcut_argv("ctrl+v", window))
                if c2 == 0:
                    return True
        code, _ = _run(["xdotool", "type", "--delay", "12", "--", text])
        return code == 0

    def mouse_move(self, x: int, y: int) -> bool:
        if self.info.tools.get("hyprctl"):
            code, _ = _run(["hyprctl", "dispatch", "movecursor", str(x), str(y)])
            if code == 0:
                return True
        if self.info.tools.get("ydotool"):
            code, _ = _run(["ydotool", "mousemove", "-a", "-x", str(x), "-y", str(y)])
            if code == 0:
                return True
        code, _ = _run(["xdotool", "mousemove", "--sync", str(x), str(y)])
        return code == 0

    def mouse_click(self, x: int, y: int, button: int = 1) -> bool:
        self.mouse_move(x, y)
        if self.info.tools.get("wlrctl"):
            btn_name = {1: "left", 2: "middle", 3: "right"}.get(button, "left")
            code, _ = _run(["wlrctl", "pointer", "click", btn_name])
            if code == 0:
                return True
        if self.info.tools.get("ydotool"):
            code, _ = _run(["ydotool", "click", _ydotool_button_hex(button, "click")])
            if code == 0:
                return True
        code, _ = _run(["xdotool", "click", str(button)])
        return code == 0

    def mouse_drag(
        self, x0: int, y0: int, x1: int, y1: int, button: int = 1
    ) -> bool:
        self.mouse_move(x0, y0)
        if self.info.tools.get("ydotool"):
            c_down, _ = _run(["ydotool", "click", _ydotool_button_hex(button, "down")])
            self.mouse_move(x1, y1)
            c_up, _ = _run(["ydotool", "click", _ydotool_button_hex(button, "up")])
            if c_down == 0 and c_up == 0:
                return True
        _run(["xdotool", "mousedown", str(button)])
        self.mouse_move(x1, y1)
        code, _ = _run(["xdotool", "mouseup", str(button)])
        return code == 0

    def screenshot(self, path: str | Path) -> bool:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # grim -s 1 captures at logical 1x scale so pixels align 1:1 with hyprctl coordinates
        if self.info.tools.get("grim"):
            code, _ = _run(["grim", "-s", "1", str(target)])
            if code == 0 and target.is_file() and target.stat().st_size > 0:
                return True
            code, _ = _run(["grim", str(target)])
            if code == 0 and target.is_file() and target.stat().st_size > 0:
                return True
        if self.info.tools.get("hyprshot"):
            code, _ = _run([
                "hyprshot", "-m", "output", "-s",
                "-o", str(target.parent), "-f", target.name,
            ])
            if code == 0 and target.is_file() and target.stat().st_size > 0:
                return True
        if self.info.tools.get("omarchy"):
            code, _ = _run(["omarchy", "capture", "screenshot", "fullscreen", "save"])
            if code == 0:
                return True
        if _xlib_root_screenshot(target):
            return True
        if self.info.tools.get("scrot"):
            code, _ = _run(["scrot", "--overwrite", str(target)])
            if code == 0 and target.is_file():
                return True
        if self.info.tools.get("maim"):
            code, _ = _run(["maim", str(target)])
            if code == 0 and target.is_file():
                return True
        if self.info.tools.get("import"):
            code, _ = _run(["import", "-window", "root", str(target)])
            if code == 0 and target.is_file():
                return True
        if self.info.tools.get("xwd"):
            code, _ = _run(["xwd", "-root", "-silent", "-out", str(target)])
            return code == 0
        return False

    def launch_app(self, command: str | list[str]) -> bool:
        if isinstance(command, str):
            import shlex
            try:
                cmd_parts = shlex.split(command)
            except Exception:
                cmd_parts = command.split()
        else:
            cmd_parts = list(command)
        resolved = resolve_app_command(cmd_parts)
        argv = ["hyprctl", "dispatch", "exec", " ".join(resolved)]
        code, _ = _run(argv)
        return code == 0

    def hotkeys(self) -> dict[str, str]:
        return dict(OMARCHY_HOTKEYS)


# --------------------------------------------------------------------------- #
# GNOME / Debian / Ubuntu / Fedora (X11 + Wayland)                             #
# --------------------------------------------------------------------------- #


class GnomeAdapter(PlatformAdapter):
    """Ubuntu / Debian / Fedora / RHEL GNOME: xdotool/wmctrl (X11/XWayland) + gdbus Introspect
    (Wayland windows) + ydotool/wtype (Wayland input) + xlib_capture/scrot/maim/grim (capture).

    The XWayland pin (``MOZ_ENABLE_WAYLAND=0`` etc. in ``_child_env``) still applies:
    forcing app children to XWayland keeps xdotool in the loop for key/text/pointer.
    """

    def list_windows(self) -> list[WindowInfo]:
        windows: list[WindowInfo] = []
        if self.info.tools.get("xdotool"):
            code, output = _run(["xdotool", "search", "--name", ".*"])
            if code == 0:
                _, active_out = _run(["xdotool", "getactivewindow"])
                active_id = active_out.strip().splitlines()[0] if active_out else ""
                for line in output.splitlines():
                    wid = line.strip()
                    if not wid:
                        continue
                    _, title = _run(["xdotool", "getwindowname", wid])
                    cls = ""
                    if self.info.tools.get("xprop"):
                        _, cls_out = _run(["xprop", "-id", wid, "WM_CLASS"])
                        m = re.search(r'=\s*"([^"]*)"', cls_out)
                        if m:
                            cls = m.group(1)
                    wx, wy, ww, wh = 0, 0, 0, 0
                    g_code, g_out = _run(["xdotool", "getwindowgeometry", "--shell", wid])
                    if g_code == 0 and g_out:
                        g_vals: dict[str, int] = {}
                        for g_line in g_out.splitlines():
                            if "=" in g_line:
                                k, _, v = g_line.partition("=")
                                if v.strip().lstrip("-").isdigit():
                                    g_vals[k.strip()] = int(v.strip())
                        wx = g_vals.get("X", 0)
                        wy = g_vals.get("Y", 0)
                        ww = g_vals.get("WIDTH", 0)
                        wh = g_vals.get("HEIGHT", 0)
                    windows.append(WindowInfo(
                        id=wid,
                        title=title.strip(),
                        app_class=cls,
                        focused=(wid == active_id),
                        source="xdotool",
                        x=wx,
                        y=wy,
                        width=ww,
                        height=wh,
                    ))
        elif self.info.tools.get("wmctrl"):
            code, output = _run(["wmctrl", "-lGpx"])
            if code == 0 and output:
                for line in output.splitlines():
                    parts = line.split(None, 9)
                    if len(parts) < 9:
                        continue
                    if len(parts) == 10:
                        wid, ws, pid_s, gx, gy, gw, gh, wm_cls, _host, title = parts
                    else:
                        wid, ws, pid_s, gx, gy, gw, gh, wm_cls, title = parts
                    windows.append(WindowInfo(
                        id=wid,
                        title=title.strip(),
                        app_class=wm_cls,
                        pid=int(pid_s) if pid_s.isdigit() else 0,
                        workspace=ws,
                        source="wmctrl",
                        x=int(gx) if gx.lstrip("-").isdigit() else 0,
                        y=int(gy) if gy.lstrip("-").isdigit() else 0,
                        width=int(gw) if gw.isdigit() else 0,
                        height=int(gh) if gh.isdigit() else 0,
                    ))
        # Wayland-native windows via GNOME Shell Introspect
        if self.info.tools.get("gdbus", True):
            for title in _gdbus_window_titles():
                if any(w.title == title for w in windows):
                    continue
                windows.append(WindowInfo(
                    id="", title=title, source="gnome-introspect",
                ))
        return windows

    def get_active_window(self) -> WindowInfo | None:
        if self.info.tools.get("xdotool"):
            code, output = _run(["xdotool", "getactivewindow"])
            if code == 0 and output.strip():
                wid = output.strip().splitlines()[0]
                _, title = _run(["xdotool", "getwindowname", wid])
                return WindowInfo(id=wid, title=title.strip(), focused=True, source="xdotool")
        return None

    def focus_window(self, wid: str) -> bool:
        if not wid:
            return False
        if self.info.tools.get("xdotool"):
            code, _ = _run(["xdotool", "windowactivate", "--sync", wid])
            if code == 0:
                return True
            code, _ = _run(["xdotool", "windowfocus", "--sync", wid])
            if code == 0:
                return True
        if self.info.tools.get("wmctrl"):
            code, _ = _run(["wmctrl", "-ia", wid])
            if code == 0:
                return True
        return False

    def close_window(self, wid: str) -> bool:
        if not wid:
            return False
        if self.info.tools.get("xdotool"):
            code, _ = _run(["xdotool", "windowclose", wid])
            if code == 0:
                return True
        if self.info.tools.get("wmctrl"):
            code, _ = _run(["wmctrl", "-ic", wid])
            if code == 0:
                return True
        return False

    def send_key(self, key: str, window: str = "") -> bool:
        if window:
            self.focus_window(window)
        if self.info.is_wayland and self.info.tools.get("wtype"):
            code, _ = _run(_wtype_key_argv(key))
            if code == 0:
                return True
        if self.info.is_wayland and self.info.tools.get("ydotool"):
            code, _ = _run(_ydotool_key_argv(key))
            if code == 0:
                return True
        code, _ = _run(_xdotool_key_argv(key))
        return code == 0

    def send_text(self, text: str, window: str = "") -> bool:
        if window:
            self.focus_window(window)
        if self.info.is_wayland and self.info.tools.get("wtype"):
            code, _ = _run(["wtype", "--", text])
            if code == 0:
                return True
        if self.info.is_wayland and self.info.tools.get("ydotool"):
            try:
                from .text_keys import text_event_argv

                km_code, km_out = _run(["xmodmap", "-pke"])
                pairs = text_event_argv(text, keymap=km_out if km_code == 0 else "")
                code, _ = _run(["ydotool", "key", *pairs])
                if code == 0:
                    return True
            except Exception:  # noqa: BLE001
                pass
        code, _ = _run(["xdotool", "type", "--delay", "12", "--", text])
        return code == 0

    def mouse_move(self, x: int, y: int) -> bool:
        if self.info.is_wayland and self.info.tools.get("ydotool"):
            code, _ = _run(["ydotool", "mousemove", "-a", "-x", str(x), "-y", str(y)])
            if code == 0:
                return True
        code, _ = _run(["xdotool", "mousemove", "--sync", str(x), str(y)])
        return code == 0

    def mouse_click(self, x: int, y: int, button: int = 1) -> bool:
        self.mouse_move(x, y)
        if self.info.is_wayland and self.info.tools.get("ydotool"):
            code, _ = _run(["ydotool", "click", _ydotool_button_hex(button, "click")])
            if code == 0:
                return True
        code, _ = _run(["xdotool", "click", str(button)])
        return code == 0

    def mouse_drag(
        self, x0: int, y0: int, x1: int, y1: int, button: int = 1
    ) -> bool:
        self.mouse_move(x0, y0)
        if self.info.is_wayland and self.info.tools.get("ydotool"):
            c_down, _ = _run(["ydotool", "click", _ydotool_button_hex(button, "down")])
            self.mouse_move(x1, y1)
            c_up, _ = _run(["ydotool", "click", _ydotool_button_hex(button, "up")])
            if c_down == 0 and c_up == 0:
                return True
        _run(["xdotool", "mousedown", str(button)])
        self.mouse_move(x1, y1)
        code, _ = _run(["xdotool", "mouseup", str(button)])
        return code == 0

    def screenshot(self, path: str | Path) -> bool:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # 1. python-xlib root capture (works out-of-the-box on Fedora, Debian, Ubuntu X11/XWayland)
        if _xlib_root_screenshot(target):
            return True
        # 2. Wayland / Debian / Ubuntu / Fedora CLI tools
        if self.info.tools.get("grim"):
            code, _ = _run(["grim", str(target)])
            if code == 0 and target.is_file() and target.stat().st_size > 0:
                return True
        if self.info.tools.get("scrot"):
            code, _ = _run(["scrot", "--overwrite", str(target)])
            if code == 0 and target.is_file() and target.stat().st_size > 0:
                return True
        if self.info.tools.get("maim"):
            code, _ = _run(["maim", str(target)])
            if code == 0 and target.is_file() and target.stat().st_size > 0:
                return True
        if self.info.tools.get("gnome-screenshot"):
            code, _ = _run(["gnome-screenshot", "-f", str(target)])
            if code == 0 and target.is_file() and target.stat().st_size > 0:
                return True
        if self.info.tools.get("import"):
            code, _ = _run(["import", "-window", "root", str(target)])
            if code == 0 and target.is_file() and target.stat().st_size > 0:
                return True
        if self.info.tools.get("xwd"):
            code, _ = _run(["xwd", "-root", "-silent", "-out", str(target)])
            return code == 0
        return False

    def launch_app(self, command: str | list[str]) -> bool:
        if isinstance(command, str):
            import shlex
            try:
                argv = shlex.split(command)
            except Exception:
                argv = command.split()
        else:
            argv = list(command)
        if argv and argv[0].startswith(("http://", "https://", "file://")):
            argv = ["xdg-open", *argv]
        else:
            argv = resolve_app_command(argv)
        code, _ = _run(["sh", "-c", '"$@" >/dev/null 2>&1 &', "sh", *argv])
        return code == 0

    def hotkeys(self) -> dict[str, str]:
        return {
            "menu": "Super",
            "terminal": "Ctrl+Alt+T",
            "screenshot": "Print Screen",
            "window_menu": "Alt+Space",
            "close_window": "Alt+F4",
            "switch_windows": "Alt+Tab",
            "workspace_left": "Super+Page Up",
            "workspace_right": "Super+Page Down",
            "lock": "Super+L",
            "file_manager": "Super+E",
            "browser": "Super+B",
        }


def _decompress_mozlz4(data: bytes) -> bytes:
    """Decompress Mozilla's custom ``mozLz40\\0`` raw LZ4 block format in pure Python."""
    import struct

    if len(data) < 12 or data[:8] != b"mozLz40\x00":
        return b""
    expected_len = struct.unpack("<I", data[8:12])[0]
    src = data[12:]
    out = bytearray()
    i = 0
    n = len(src)
    while i < n:
        token = src[i]
        i += 1
        lit_len = token >> 4
        if lit_len == 15:
            while i < n:
                b = src[i]
                i += 1
                lit_len += b
                if b != 255:
                    break
        out.extend(src[i : i + lit_len])
        i += lit_len
        if i >= n:
            break
        if i + 1 >= n:
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset <= 0 or offset > len(out):
            break
        match_len = (token & 0x0F) + 4
        if (token & 0x0F) == 15:
            while i < n:
                b = src[i]
                i += 1
                match_len += b
                if b != 255:
                    break
        start = len(out) - offset
        for j in range(match_len):
            out.append(out[start + j])
    return bytes(out[:expected_len])


def _wayland_browser_session_titles() -> list[str]:
    """Fallback for GNOME 45+ Wayland where ``GetWindows`` is restricted:
    read active tab titles + URLs from running Wayland-native Zen / Firefox sessionstore."""
    import glob
    import json
    import time

    titles: list[str] = []
    # Check if Zen or Firefox is running
    code_zen, _ = _run(["pgrep", "-f", "zen-bin|zen-browser"])
    code_ff, _ = _run(["pgrep", "-f", "firefox"])
    if code_zen != 0 and code_ff != 0:
        return []
    browser_label = "Zen Browser" if code_zen == 0 else "Mozilla Firefox"
    patterns = (
        "~/.zen/*/sessionstore-backups/recovery.jsonlz4",
        "~/.mozilla/firefox/*/sessionstore-backups/recovery.jsonlz4",
        "~/.var/app/io.github.zen_browser.zen/.zen/*/sessionstore-backups/recovery.jsonlz4",
        "~/.var/app/org.mozilla.firefox/.mozilla/firefox/*/sessionstore-backups/recovery.jsonlz4",
    )
    now = time.time()
    for pat in patterns:
        for path_str in glob.glob(str(Path(pat).expanduser())):
            try:
                p = Path(path_str)
                if now - p.stat().st_mtime > 86400:
                    continue
                raw = _decompress_mozlz4(p.read_bytes())
                if not raw:
                    continue
                data = json.loads(raw.decode("utf-8", errors="replace"))
                for win in data.get("windows") or []:
                    tabs = win.get("tabs") or []
                    selected_idx = int(win.get("selected", len(tabs)) or len(tabs)) - 1
                    ordered_tabs = []
                    if 0 <= selected_idx < len(tabs):
                        ordered_tabs.append(tabs[selected_idx])
                    for idx, t in enumerate(tabs):
                        if idx != selected_idx:
                            ordered_tabs.append(t)
                    for tab in ordered_tabs:
                        entries = tab.get("entries") or []
                        if not entries:
                            continue
                        raw_idx = int(tab.get("index", len(entries))) - 1
                        e_idx = min(max(0, raw_idx), len(entries) - 1)
                        last = entries[e_idx]
                        t_title = str(last.get("title") or "").strip()
                        t_url = str(last.get("url") or "").strip()
                        if t_title and t_title not in ("New Tab", "about:blank", "None"):
                            titles.append(f"{t_title} — {browser_label}")
                        elif t_url and t_url != "about:blank":
                            titles.append(f"{t_url} — {browser_label}")
            except Exception:  # noqa: BLE001
                continue
    if not titles and (code_zen == 0 or code_ff == 0):
        titles.append(browser_label)
    return titles


def _gdbus_window_titles() -> list[str]:
    """Wayland-native window titles via org.gnome.Shell.Introspect.GetWindows, with
    Wayland browser sessionstore fallback when GNOME 45+ restricts GetWindows."""
    code, output = _run([
        "gdbus", "call", "--session",
        "--dest", "org.gnome.Shell.Introspect",
        "--object-path", "/org/gnome/Shell/Introspect",
        "--method", "org.gnome.Shell.Introspect.GetWindows",
    ])
    titles: list[str] = []
    if code == 0 and output:
        for match in re.finditer(r"'title'\s*:\s*<'([^']*)'>", output):
            title = match.group(1).strip()
            if title:
                titles.append(title)
    if not titles:
        titles.extend(_wayland_browser_session_titles())
    return titles


# --------------------------------------------------------------------------- #
# KDE                                                                          #
# --------------------------------------------------------------------------- #


class KdeAdapter(GnomeAdapter):
    """KDE Plasma — same X11 tools, plus ``kdotool`` when present."""

    def list_windows(self) -> list[WindowInfo]:
        if self.info.tools.get("kdotool"):
            code, output = _run(["kdotool", "search", "--name", ".*"])
            if code == 0:
                windows = []
                for line in output.splitlines():
                    wid = line.strip()
                    if not wid:
                        continue
                    _, title = _run(["kdotool", "getwindowname", wid])
                    windows.append(WindowInfo(id=wid, title=title.strip(), source="kdotool"))
                if windows:
                    return windows
        return super().list_windows()

    def hotkeys(self) -> dict[str, str]:
        return {
            "menu": "Alt+F1",
            "terminal": "Ctrl+Alt+T",
            "screenshot": "Print Screen",
            "close_window": "Alt+F4",
            "switch_windows": "Alt+Tab",
            "lock": "Ctrl+Alt+L",
            "file_manager": "Ctrl+Alt+D",
        }


# --------------------------------------------------------------------------- #
# macOS                                                                        #
# --------------------------------------------------------------------------- #


class MacAdapter(PlatformAdapter):
    """macOS: ``osascript`` (AppleScript / System Events) + ``cliclick`` + ``screencapture``."""

    def list_windows(self) -> list[WindowInfo]:
        script = (
            'tell application "System Events" to get name of every process '
            'whose background only is false'
        )
        code, output = _run(["osascript", "-e", script])
        if code != 0:
            return []
        windows: list[WindowInfo] = []
        for i, app_name in enumerate(p.strip() for p in output.split(",")):
            if not app_name:
                continue
            title_script = (
                f'tell application "System Events" to try to get name of front window of '
                f'process "{app_name}" on error ""'
            )
            _, title = _run(["osascript", "-e", title_script])
            windows.append(WindowInfo(
                id=str(i), title=title.strip() or app_name,
                app_class=app_name, source="osascript",
            ))
        return windows

    def get_active_window(self) -> WindowInfo | None:
        script = (
            'tell application "System Events" to get name of first application process '
            'whose frontmost is true'
        )
        code, output = _run(["osascript", "-e", script])
        if code != 0 or not output.strip():
            return None
        app_name = output.strip()
        title_script = (
            f'tell application "System Events" to try to get name of front window of '
            f'process "{app_name}" on error ""'
        )
        _, title = _run(["osascript", "-e", title_script])
        return WindowInfo(id=app_name, title=title.strip() or app_name,
                          app_class=app_name, focused=True, source="osascript")

    def focus_window(self, wid: str) -> bool:
        script = f'tell application "{wid}" to activate'
        code, _ = _run(["osascript", "-e", script])
        return code == 0

    def close_window(self, wid: str) -> bool:
        script = (
            f'tell application "System Events" to tell process "{wid}" '
            'to keystroke "w" using command down'
        )
        code, _ = _run(["osascript", "-e", script])
        return code == 0

    def send_key(self, key: str, window: str = "") -> bool:
        if window:
            self.focus_window(window)
        tokens = normalize_key(key, "osascript")
        # map to AppleScript modifier names
        mod_map = {"ctrl": "control", "alt": "option", "super": "command", "shift": "shift"}
        mods = []
        chars = []
        for t in tokens:
            low = t.lower()
            if low in mod_map:
                mods.append(mod_map[low])
            else:
                chars.append(t)
        char = chars[0] if chars else ""
        if not char:
            return False
        using = ""
        if mods:
            using = " using {" + ", ".join(f"{m} down" for m in mods) + "}"
        script = f'tell application "System Events" to keystroke "{char}"{using}'
        code, _ = _run(["osascript", "-e", script])
        return code == 0

    def send_text(self, text: str, window: str = "") -> bool:
        if window:
            self.focus_window(window)
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "System Events" to keystroke "{escaped}"'
        code, _ = _run(["osascript", "-e", script])
        return code == 0

    def mouse_move(self, x: int, y: int) -> bool:
        code, _ = _run(["cliclick", f"m:{x},{y}"])
        return code == 0

    def mouse_click(self, x: int, y: int, button: int = 1) -> bool:
        cmd = f"c:{x},{y}" if button == 1 else f"rc:{x},{y}"
        code, _ = _run(["cliclick", cmd])
        return code == 0

    def mouse_drag(
        self, x0: int, y0: int, x1: int, y1: int, button: int = 1
    ) -> bool:
        code, _ = _run(["cliclick", f"dd:{x0},{y0}", f"du:{x1},{y1}"])
        return code == 0

    def screenshot(self, path: str | Path) -> bool:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        code, _ = _run(["screencapture", "-x", str(target)])
        return code == 0

    def launch_app(self, command: str | list[str]) -> bool:
        if isinstance(command, list):
            command = " ".join(command)
        if command.startswith(("http://", "https://", "file://")):
            code, _ = _run(["open", command])
        else:
            code, _ = _run(["open", "-a", command])
        return code == 0

    def hotkeys(self) -> dict[str, str]:
        return {
            "menu": "Cmd+Space",
            "terminal": "Cmd+Space",  # Spotlight → Terminal
            "screenshot": "Cmd+Shift+3",
            "screenshot_region": "Cmd+Shift+4",
            "close_window": "Cmd+W",
            "switch_windows": "Cmd+Tab",
            "lock": "Ctrl+Cmd+Q",
            "file_manager": "Cmd+Shift+H",
            "browser": "Cmd+Space",  # Spotlight → browser
            "copy": "Cmd+C",
            "cut": "Cmd+X",
            "paste": "Cmd+V",
            "undo": "Cmd+Z",
        }


# --------------------------------------------------------------------------- #
# Windows                                                                      #
# --------------------------------------------------------------------------- #


class WindowsAdapter(PlatformAdapter):
    """Windows: PowerShell UI Automation + ``SendKeys`` + ``System.Drawing`` screenshot.

    Kept deliberately thin — the full UIA tree walk lands when a Windows owner needs it.
    """

    def list_windows(self) -> list[WindowInfo]:
        ps = (
            'Add-Type -AssemblyName System.Windows.Forms; '
            '[System.Windows.Forms.Screen]::AllScreens | Out-Null; '
            'Get-Process | Where-Object {$_.MainWindowTitle} | '
            'Select-Object Id,MainWindowTitle,ProcessName | ConvertTo-Json'
        )
        code, output = _run(["powershell", "-NoProfile", "-Command", ps])
        if code != 0 or not output:
            return []
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict):
            data = [data]
        return [
            WindowInfo(
                id=str(d.get("Id") or ""),
                title=str(d.get("MainWindowTitle") or ""),
                app_class=str(d.get("ProcessName") or ""),
                pid=int(d.get("Id") or 0),
                source="powershell",
            )
            for d in data if isinstance(d, dict)
        ]

    def get_active_window(self) -> WindowInfo | None:
        ps = (
            'Add-Type -Name W -Namespace U -MemberDefinition '
            "'[DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow();'; "
            "[U.W]::GetForegroundWindow().ToString()"
        )
        code, output = _run(["powershell", "-NoProfile", "-Command", ps])
        if code != 0:
            return None
        wid = output.strip()
        return WindowInfo(id=wid, title="", focused=True, source="powershell") if wid else None

    def focus_window(self, wid: str) -> bool:
        ps = (
            f"$p = Get-Process -Id {wid} -ErrorAction SilentlyContinue; "
            "if ($p) { "
            "  Add-Type -Name W -Namespace U -MemberDefinition "
            "'[DllImport(\"user32.dll\")] public static extern "
            "bool SetForegroundWindow(IntPtr h);'; "
            "  [U.W]::SetForegroundWindow($p.MainWindowHandle) "
            "}"
        )
        code, _ = _run(["powershell", "-NoProfile", "-Command", ps])
        return code == 0

    def close_window(self, wid: str) -> bool:
        code, _ = _run(["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {wid}"])
        return code == 0

    def send_key(self, key: str, window: str = "") -> bool:
        if window:
            self.focus_window(window)
        # PowerShell SendKeys: ^ = ctrl, % = alt, + = shift
        sk = key.lower().replace("ctrl+", "^").replace("alt+", "%").replace("shift+", "+")
        ps = (
            f'Add-Type -AssemblyName System.Windows.Forms; '
            f'[System.Windows.Forms.SendKeys]::SendWait("{sk}")'
        )
        code, _ = _run(["powershell", "-NoProfile", "-Command", ps])
        return code == 0

    def send_text(self, text: str, window: str = "") -> bool:
        if window:
            self.focus_window(window)
        escaped = text.replace('"', '`"')
        ps = (
            f'Add-Type -AssemblyName System.Windows.Forms; '
            f'[System.Windows.Forms.SendKeys]::SendWait("{escaped}")'
        )
        code, _ = _run(["powershell", "-NoProfile", "-Command", ps])
        return code == 0

    def mouse_move(self, x: int, y: int) -> bool:
        ps = (
            'Add-Type -AssemblyName System.Windows.Forms; '
            f'[System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point({x},{y})'
        )
        code, _ = _run(["powershell", "-NoProfile", "-Command", ps])
        return code == 0

    def mouse_click(self, x: int, y: int, button: int = 1) -> bool:
        self.mouse_move(x, y)
        down = 2 if button == 1 else 8
        up = 4 if button == 1 else 16
        ps = (
            "Add-Type -Name M -Namespace U -MemberDefinition "
            "'[DllImport(\"user32.dll\")] public static extern void mouse_event("
            "uint f, int x, int y, uint d, IntPtr e);'; "
            f"[U.M]::mouse_event({down},0,0,0,[IntPtr]::Zero); "
            f"[U.M]::mouse_event({up},0,0,0,[IntPtr]::Zero)"
        )
        code, _ = _run(["powershell", "-NoProfile", "-Command", ps])
        return code == 0

    def mouse_drag(
        self, x0: int, y0: int, x1: int, y1: int, button: int = 1
    ) -> bool:
        self.mouse_move(x0, y0)
        self.mouse_click(x0, y0, button)  # press+release is the best SendKeys gives us
        self.mouse_move(x1, y1)
        self.mouse_click(x1, y1, button)
        return True

    def screenshot(self, path: str | Path) -> bool:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        ps = (
            'Add-Type -AssemblyName System.Windows.Forms,System.Drawing; '
            '$b = New-Object System.Drawing.Bitmap('
            '[System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Width, '
            '[System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Height); '
            '$g = [System.Drawing.Graphics]::FromImage($b); '
            '$g.CopyFromScreen(0,0,0,0,$b.Size); '
            f'$b.Save("{target}"); $g.Dispose(); $b.Dispose()'
        )
        code, _ = _run(["powershell", "-NoProfile", "-Command", ps])
        return code == 0

    def launch_app(self, command: str | list[str]) -> bool:
        if isinstance(command, list):
            command = " ".join(command)
        code, _ = _run(["cmd", "/c", "start", "", command])
        return code == 0

    def hotkeys(self) -> dict[str, str]:
        return {
            "menu": "Win",
            "terminal": "Win+X",
            "screenshot": "Win+Shift+S",
            "close_window": "Alt+F4",
            "switch_windows": "Alt+Tab",
            "lock": "Win+L",
            "file_manager": "Win+E",
            "browser": "Win",  # Start → browser
            "copy": "Ctrl+C",
            "cut": "Ctrl+X",
            "paste": "Ctrl+V",
            "undo": "Ctrl+Z",
        }


# --------------------------------------------------------------------------- #
# Generic X11 fallback                                                         #
# --------------------------------------------------------------------------- #


class X11Adapter(GnomeAdapter):
    """Bare X11 (no GNOME/KDE): xdotool only.  Compositor-agnostic fallback."""

    def hotkeys(self) -> dict[str, str]:
        return {
            "terminal": "Ctrl+Alt+T",
            "screenshot": "Print Screen",
            "close_window": "Alt+F4",
            "switch_windows": "Alt+Tab",
        }


# --------------------------------------------------------------------------- #
# Adapter factory                                                              #
# --------------------------------------------------------------------------- #

_ADAPTERS: dict[str, type[PlatformAdapter]] = {
    "hyprland": HyprlandAdapter,
    "sway": HyprlandAdapter,  # wlroots IPC differs but hyprctl is the closest; wtype works
    "gnome": GnomeAdapter,
    "kde": KdeAdapter,
    "aqua": MacAdapter,
    "win32": WindowsAdapter,
    "x11": X11Adapter,
}


def get_adapter(info: PlatformInfo | None = None) -> PlatformAdapter:
    """Return the adapter for this host (detects the platform when *info* is omitted)."""
    info = info or detect_platform()
    cls = _ADAPTERS.get(info.compositor, X11Adapter)
    return cls(info)


# --------------------------------------------------------------------------- #
# Omarchy skill installer                                                      #
# --------------------------------------------------------------------------- #

_OMARCHY_SKILL_BASE = "https://raw.githubusercontent.com/omacom/omarchy/quattro/agents/skills"
_OMARCHY_SKILLS = (
    "acceptance-tests.md",
    "command-metadata.md",
    "icon-font.md",
    "install-scripts.md",
    "migrations.md",
    "shell-dev.md",
    "visual-verification.md",
)


def install_omarchy_skills(
    runtime: str | Path | None = None,
    *,
    skill_names: Sequence[str] | None = None,
) -> dict:
    """Fetch Omarchy's ``agents/skills/*.md`` into the runtime's skill directory.

    Called automatically when ``detect_platform().is_omarchy`` so the agent gets
    Omarchy's own shell-dev / visual-verification / install-scripts knowledge
    without a built-in.  Returns ``{installed: [...], skipped: [...], failed: [...]}``.
    """
    import urllib.request

    base = Path(runtime or Path.home() / ".hermes" / "skaldir")
    skill_dir = base / "skills" / "omarchy"
    skill_dir.mkdir(parents=True, exist_ok=True)
    names = list(skill_names) if skill_names else list(_OMARCHY_SKILLS)
    installed: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    for name in names:
        target = skill_dir / name
        if target.exists():
            skipped.append(name)
            continue
        url = f"{_OMARCHY_SKILL_BASE}/{name}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 — fixed HTTPS URL
                data = resp.read()
            target.write_bytes(data)
            installed.append(name)
        except Exception as exc:  # noqa: BLE001 — a missing skill is a reportable fact
            failed.append(f"{name}: {type(exc).__name__}: {exc}")
    return {"installed": installed, "skipped": skipped, "failed": failed,
            "skill_dir": str(skill_dir)}
