"""Tests for cu_platform — multi-distro adapters, detection, key normalisation."""

from __future__ import annotations

from skaldir.cu_platform import (
    OMARCHY_HOTKEYS,
    GnomeAdapter,
    HyprlandAdapter,
    KdeAdapter,
    MacAdapter,
    PlatformInfo,
    WindowsAdapter,
    X11Adapter,
    detect_platform,
    get_adapter,
    normalize_key,
)

# --------------------------------------------------------------------------- #
# Detection                                                                    #
# --------------------------------------------------------------------------- #

def test_detect_platform_returns_info():
    info = detect_platform()
    assert info.os in ("linux", "macos", "windows")
    assert info.compositor  # never empty
    assert isinstance(info.tools, dict)
    assert "xdotool" in info.tools


def test_detect_platform_omarchy_marker(tmp_path, monkeypatch):
    monkeypatch.setattr("skaldir.cu_platform._OMARCHY_MARKERS", (str(tmp_path / "omarchy"),))
    (tmp_path / "omarchy").mkdir()
    info = detect_platform()
    assert info.distro == "omarchy"
    assert info.is_omarchy
    assert info.compositor == "hyprland"


def test_platform_info_is_wayland():
    info = PlatformInfo(os="linux", distro="omarchy", compositor="hyprland")
    assert info.is_wayland
    info2 = PlatformInfo(os="linux", distro="fedora", compositor="x11")
    assert not info2.is_wayland


# --------------------------------------------------------------------------- #
# Adapter factory                                                              #
# --------------------------------------------------------------------------- #

def test_get_adapter_hyprland():
    info = PlatformInfo(os="linux", distro="omarchy", compositor="hyprland")
    assert isinstance(get_adapter(info), HyprlandAdapter)


def test_get_adapter_gnome():
    info = PlatformInfo(os="linux", distro="ubuntu", compositor="gnome")
    assert isinstance(get_adapter(info), GnomeAdapter)


def test_get_adapter_kde():
    info = PlatformInfo(os="linux", distro="fedora", compositor="kde")
    assert isinstance(get_adapter(info), KdeAdapter)


def test_get_adapter_macos():
    info = PlatformInfo(os="macos", distro="macos", compositor="aqua")
    assert isinstance(get_adapter(info), MacAdapter)


def test_get_adapter_windows():
    info = PlatformInfo(os="windows", distro="windows", compositor="win32")
    assert isinstance(get_adapter(info), WindowsAdapter)


def test_get_adapter_x11_fallback():
    info = PlatformInfo(os="linux", distro="linux", compositor="unknown")
    assert isinstance(get_adapter(info), X11Adapter)


# --------------------------------------------------------------------------- #
# Key normalisation                                                            #
# --------------------------------------------------------------------------- #

def test_normalize_key_xdotool():
    assert normalize_key("ctrl+l", "xdotool") == ["ctrl", "l"]
    assert normalize_key("super+space", "xdotool") == ["super", "space"]
    assert normalize_key("return", "xdotool") == ["Return"]
    assert normalize_key("enter", "xdotool") == ["Return"]


def test_normalize_key_wtype():
    assert normalize_key("super+space", "wtype") == ["super", "space"]
    assert normalize_key("ctrl+l", "wtype") == ["ctrl", "l"]
    assert normalize_key("return", "wtype") == ["Return"]


def test_normalize_key_multi_modifier():
    assert normalize_key("ctrl+alt+t", "xdotool") == ["ctrl", "alt", "t"]
    assert normalize_key("super+shift+1", "wtype") == ["super", "shift", "1"]


# --------------------------------------------------------------------------- #
# Omarchy hotkeys                                                              #
# --------------------------------------------------------------------------- #

def test_omarchy_hotkeys_core_entries():
    assert OMARCHY_HOTKEYS["menu"] == "Super+Space"
    assert OMARCHY_HOTKEYS["terminal"] == "Super+Return"
    assert OMARCHY_HOTKEYS["browser"] == "Super+Shift+Return"
    assert OMARCHY_HOTKEYS["close_window"] == "Super+W"
    assert OMARCHY_HOTKEYS["screenshot"] == "Print Screen"


def test_omarchy_hotkeys_count():
    # measured from manual/07-hotkeys.md — 50 entries at capture time
    assert len(OMARCHY_HOTKEYS) >= 40


def test_hyprland_adapter_hotkeys():
    info = PlatformInfo(os="linux", distro="omarchy", compositor="hyprland")
    adapter = get_adapter(info)
    assert adapter.hotkeys()["menu"] == "Super+Space"


# --------------------------------------------------------------------------- #
# Adapter methods (no crash without tools)                                     #
# --------------------------------------------------------------------------- #

def test_gnome_adapter_list_windows_no_crash():
    info = PlatformInfo(os="linux", distro="fedora", compositor="gnome",
                        tools={"xdotool": False, "gdbus": False})
    adapter = GnomeAdapter(info)
    assert adapter.list_windows() == []


def test_hyprland_adapter_list_windows_no_hyprctl():
    info = PlatformInfo(os="linux", distro="omarchy", compositor="hyprland",
                        tools={"hyprctl": False, "wtype": False, "ydotool": False})
    adapter = HyprlandAdapter(info)
    assert adapter.list_windows() == []
    assert adapter.get_active_window() is None


def test_mac_adapter_methods_exist():
    info = PlatformInfo(os="macos", distro="macos", compositor="aqua",
                        tools={"osascript": False, "cliclick": False})
    adapter = MacAdapter(info)
    assert isinstance(adapter.list_windows(), list)
    assert adapter.hotkeys()["copy"] == "Cmd+C"


def test_windows_adapter_methods_exist():
    info = PlatformInfo(os="windows", distro="windows", compositor="win32",
                        tools={"powershell": False})
    adapter = WindowsAdapter(info)
    assert isinstance(adapter.list_windows(), list)
    assert adapter.hotkeys()["lock"] == "Win+L"
