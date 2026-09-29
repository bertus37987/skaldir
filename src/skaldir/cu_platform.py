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
        return self.compositor in ("hyprland", "gnome", "kde", "dwl") and self.os == "linux"


# --------------------------------------------------------------------------- #
# Detection                                                                    #
# --------------------------------------------------------------------------- #

_OMARCHY_MARKERS = (
    "/etc/omarchy-release",
    os.path.expanduser("~/.config/omarchy"),
    "/usr/bin/omarchy",
    "/usr/local/bin/omarchy",
)


def _detect_distro() -> str:
    """Identify the Linux distribution (or macOS / Windows)."""
    if _platform_mod.system() == "Darwin":
        return "macos"
    if _platform_mod.system() == "Windows":
        return "windows"
    # Omarchy first: it is Arch-based so /etc/os-release says "arch"
    for marker in _OMARCHY_MARKERS:
        if Path(marker).exists():
            return "omarchy"
    os_release = Path("/etc/os-release")
    if os_release.exists():
        text = os_release.read_text(encoding="utf-8", errors="replace").lower()
        for name in ("omarchy", "ubuntu", "fedora", "rhel", "centos", "rocky", "almalinux",
                     "arch", "manjaro", "debian", "opensuse", "nixos", "gentoo", "void"):
            if f"id={name}" in text or f"id=\"{name}\"" in text:
                return name if name not in ("centos", "rocky", "almalinux") else "rhel"
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
            "hyprctl", "wtype", "ydotool", "wlrctl",
            "xdotool", "xprop", "xwd", "import", "grim", "slurp",
            "gdbus", "qdbus", "kdotool",
            "osascript", "cliclick",
            "omarchy",
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


# --------------------------------------------------------------------------- #
# Adapter base                                                                 #
# --------------------------------------------------------------------------- #


def _run(argv: Sequence[str], *, timeout: float = 8.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(  # nosec B603 — fixed argv, no shell
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


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
    """Omarchy / Hyprland: ``hyprctl`` IPC + ``wtype`` / ``ydotool`` input + ``omarchy`` CLI.

    ``hyprctl clients -j`` lists **all** windows including Wayland-native ones — the
    exact gap that made xdotool-driven observe() report ``(no windows)`` while Zen
    was plainly on screen (measured 2026-09-23).
    """

    def list_windows(self) -> list[WindowInfo]:
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
            ))
        return windows

    def get_active_window(self) -> WindowInfo | None:
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
        )

    def focus_window(self, wid: str) -> bool:
        code, _ = _run(["hyprctl", "dispatch", "focuswindow", f"address:{wid}"])
        return code == 0

    def close_window(self, wid: str) -> bool:
        code, _ = _run(["hyprctl", "dispatch", "closewindow", f"address:{wid}"])
        return code == 0

    def send_key(self, key: str, window: str = "") -> bool:
        if window:
            self.focus_window(window)
        if self.info.tools.get("wtype"):
            code, _ = _run(_wtype_key_argv(key))
            return code == 0
        if self.info.tools.get("ydotool"):
            tokens = normalize_key(key, "xdotool")
            argv = ["ydotool", "key"]
            for t in tokens[:-1]:
                argv.append(t.lower() + ":1")
            if tokens:
                argv.append(tokens[-1])
            for t in tokens[:-1]:
                argv.append(t.lower() + ":0")
            code, _ = _run(argv)
            return code == 0
        # last resort: XWayland via xdotool (only works for xwayland windows)
        code, _ = _run(_xdotool_key_argv(key))
        return code == 0

    def send_text(self, text: str, window: str = "") -> bool:
        if window:
            self.focus_window(window)
        if self.info.tools.get("wtype"):
            code, _ = _run(["wtype", "--", text])
            return code == 0
        if self.info.tools.get("ydotool"):
            code, _ = _run(["ydotool", "type", "--", text])
            return code == 0
        code, _ = _run(["xdotool", "type", "--delay", "12", "--", text])
        return code == 0

    def mouse_move(self, x: int, y: int) -> bool:
        if self.info.tools.get("ydotool"):
            code, _ = _run(["ydotool", "mousemove", "--absolute", str(x), str(y)])
            return code == 0
        code, _ = _run(["xdotool", "mousemove", "--sync", str(x), str(y)])
        return code == 0

    def mouse_click(self, x: int, y: int, button: int = 1) -> bool:
        self.mouse_move(x, y)
        if self.info.tools.get("ydotool"):
            code, _ = _run(["ydotool", "click", str(button)])
            return code == 0
        code, _ = _run(["xdotool", "click", str(button)])
        return code == 0

    def mouse_drag(
        self, x0: int, y0: int, x1: int, y1: int, button: int = 1
    ) -> bool:
        self.mouse_move(x0, y0)
        if self.info.tools.get("ydotool"):
            _run(["ydotool", "mousedown", str(button)])
            self.mouse_move(x1, y1)
            _run(["ydotool", "mouseup", str(button)])
            return True
        _run(["xdotool", "mousedown", str(button)])
        self.mouse_move(x1, y1)
        _run(["xdotool", "mouseup", str(button)])
        return True

    def screenshot(self, path: str | Path) -> bool:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Omarchy's own capture is the blessed path; grim is the wlroots fallback
        if self.info.tools.get("omarchy"):
            code, _ = _run(["omarchy", "capture", "screenshot", "fullscreen", "save"])
            if code == 0:
                return True
        if self.info.tools.get("grim"):
            code, _ = _run(["grim", str(target)])
            return code == 0
        if self.info.tools.get("import"):
            code, _ = _run(["import", "-window", "root", str(target)])
            return code == 0
        if self.info.tools.get("xwd"):
            code, _ = _run(["xwd", "-root", "-silent", "-out", str(target)])
            return code == 0
        return False

    def launch_app(self, command: str | list[str]) -> bool:
        if isinstance(command, str):
            argv = ["hyprctl", "dispatch", "exec", command]
        else:
            argv = ["hyprctl", "dispatch", "exec", " ".join(command)]
        code, _ = _run(argv)
        return code == 0

    def hotkeys(self) -> dict[str, str]:
        return dict(OMARCHY_HOTKEYS)


# --------------------------------------------------------------------------- #
# GNOME (X11 + Wayland)                                                        #
# --------------------------------------------------------------------------- #


class GnomeAdapter(PlatformAdapter):
    """Ubuntu / Fedora / RHEL GNOME: xdotool (X11) + gdbus Introspect (Wayland windows)
    + ydotool/wtype (Wayland input) + xdg-open (launch).

    The XWayland pin (``MOZ_ENABLE_WAYLAND=0`` etc. in ``_child_env``) still applies:
    forcing app children to XWayland keeps xdotool in the loop for key/text/pointer.
    """

    def list_windows(self) -> list[WindowInfo]:
        windows: list[WindowInfo] = []
        if self.info.tools.get("xdotool"):
            code, output = _run(["xdotool", "search", "--name", ".*"])
            if code == 0:
                active_code, active_out = _run(["xdotool", "getactivewindow"])
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
                    windows.append(WindowInfo(
                        id=wid,
                        title=title.strip(),
                        app_class=cls,
                        focused=(wid == active_id),
                        source="xdotool",
                    ))
        # Wayland-native windows via GNOME Shell Introspect
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
        code, _ = _run(["xdotool", "windowfocus", "--sync", wid])
        return code == 0

    def close_window(self, wid: str) -> bool:
        if not wid:
            return False
        code, _ = _run(["xdotool", "windowclose", wid])
        return code == 0

    def send_key(self, key: str, window: str = "") -> bool:
        if window:
            self.focus_window(window)
        if self.info.is_wayland and self.info.tools.get("wtype"):
            code, _ = _run(_wtype_key_argv(key))
            return code == 0
        if self.info.is_wayland and self.info.tools.get("ydotool"):
            tokens = normalize_key(key, "xdotool")
            argv = ["ydotool", "key"]
            for t in tokens[:-1]:
                argv.append(t.lower() + ":1")
            if tokens:
                argv.append(tokens[-1])
            for t in tokens[:-1]:
                argv.append(t.lower() + ":0")
            code, _ = _run(argv)
            return code == 0
        code, _ = _run(_xdotool_key_argv(key))
        return code == 0

    def send_text(self, text: str, window: str = "") -> bool:
        if window:
            self.focus_window(window)
        if self.info.is_wayland and self.info.tools.get("wtype"):
            code, _ = _run(["wtype", "--", text])
            return code == 0
        if self.info.is_wayland and self.info.tools.get("ydotool"):
            code, _ = _run(["ydotool", "type", "--", text])
            return code == 0
        code, _ = _run(["xdotool", "type", "--delay", "12", "--", text])
        return code == 0

    def mouse_move(self, x: int, y: int) -> bool:
        if self.info.is_wayland and self.info.tools.get("ydotool"):
            code, _ = _run(["ydotool", "mousemove", "--absolute", str(x), str(y)])
            return code == 0
        code, _ = _run(["xdotool", "mousemove", "--sync", str(x), str(y)])
        return code == 0

    def mouse_click(self, x: int, y: int, button: int = 1) -> bool:
        self.mouse_move(x, y)
        if self.info.is_wayland and self.info.tools.get("ydotool"):
            code, _ = _run(["ydotool", "click", str(button)])
            return code == 0
        code, _ = _run(["xdotool", "click", str(button)])
        return code == 0

    def mouse_drag(
        self, x0: int, y0: int, x1: int, y1: int, button: int = 1
    ) -> bool:
        self.mouse_move(x0, y0)
        tool = "ydotool" if (self.info.is_wayland and self.info.tools.get("ydotool")) else "xdotool"
        _run([tool, "mousedown", str(button)])
        self.mouse_move(x1, y1)
        _run([tool, "mouseup", str(button)])
        return True

    def screenshot(self, path: str | Path) -> bool:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.info.tools.get("import"):
            code, _ = _run(["import", "-window", "root", str(target)])
            return code == 0
        if self.info.tools.get("grim"):
            code, _ = _run(["grim", str(target)])
            return code == 0
        if self.info.tools.get("xwd"):
            code, _ = _run(["xwd", "-root", "-silent", "-out", str(target)])
            return code == 0
        return False

    def launch_app(self, command: str | list[str]) -> bool:
        if isinstance(command, list):
            command = " ".join(command)
        if command.startswith(("http://", "https://", "file://")):
            code, _ = _run(["xdg-open", command])
        else:
            import shlex
            code, _ = _run(["sh", "-c", f"{shlex.quote(command)} >/dev/null 2>&1 &"])
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


def _gdbus_window_titles() -> list[str]:
    """Wayland-native window titles via org.gnome.Shell.Introspect.GetWindows."""
    code, output = _run([
        "gdbus", "call", "--session",
        "--dest", "org.gnome.Shell.Introspect",
        "--object-path", "/org/gnome/Shell/Introspect",
        "--method", "org.gnome.Shell.Introspect.GetWindows",
    ])
    if code != 0:
        return []
    titles: list[str] = []
    for match in re.finditer(r"'title'\s*:\s*<'([^']*)'>", output):
        title = match.group(1).strip()
        if title:
            titles.append(title)
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
