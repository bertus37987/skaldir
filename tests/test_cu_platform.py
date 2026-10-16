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


# --------------------------------------------------------------------------- #
# Target Platforms: Omarchy Hyprland, Debian/Ubuntu, Fedora Linux (Host)      #
# --------------------------------------------------------------------------- #

def test_detect_distro_debian_ubuntu_fedora_omarchy(tmp_path, monkeypatch):
    import skaldir.cu_platform as cp

    monkeypatch.setattr(cp, "_OMARCHY_MARKERS", ())
    monkeypatch.delenv("OMARCHY_PATH", raising=False)

    # 1. Omarchy via OMARCHY_PATH
    om_dir = tmp_path / "omarchy-share"
    om_dir.mkdir()
    monkeypatch.setenv("OMARCHY_PATH", str(om_dir))
    assert cp._detect_distro() == "omarchy"
    monkeypatch.delenv("OMARCHY_PATH", raising=False)

    # 2. Ubuntu / Debian / Fedora via /etc/os-release
    os_rel = tmp_path / "os-release"
    real_path = cp.Path

    def _fake_path(p):
        if str(p) == "/etc/os-release":
            return os_rel
        return real_path(p)

    monkeypatch.setattr(cp, "Path", _fake_path)

    os_rel.write_text('NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\n', encoding="utf-8")
    assert cp._detect_distro() == "ubuntu"

    os_rel.write_text('NAME="Debian GNU/Linux"\nID=debian\n', encoding="utf-8")
    assert cp._detect_distro() == "debian"

    os_rel.write_text('NAME="Fedora Linux"\nID=fedora\n', encoding="utf-8")
    assert cp._detect_distro() == "fedora"

    os_rel.write_text(
        'NAME="Linux Mint"\nID=linuxmint\nID_LIKE="ubuntu debian"\n', encoding="utf-8"
    )
    assert cp._detect_distro() == "ubuntu"


def test_hyprland_adapter_full_omarchy_workflow(monkeypatch, tmp_path):
    """Verify Omarchy Hyprland window geometry, movecursor, sendshortcut, wlrctl, and grim -s 1."""
    import json

    import skaldir.cu_platform as cp

    calls: list[list[str]] = []
    clients_json = json.dumps([
        {
            "address": "0x55aabbcc",
            "mapped": True,
            "hidden": False,
            "at": [120, 80],
            "size": [1280, 720],
            "workspace": {"id": 1, "name": "1"},
            "floating": False,
            "class": "org.mozilla.zen",
            "title": "Zen Browser",
            "pid": 4321,
            "xwayland": False,
        }
    ])
    active_json = json.dumps({
        "address": "0x55aabbcc",
        "at": [120, 80],
        "size": [1280, 720],
        "workspace": {"id": 1, "name": "1"},
        "floating": False,
        "class": "org.mozilla.zen",
        "title": "Zen Browser",
        "pid": 4321,
        "xwayland": False,
    })

    def _fake_run(argv, *, timeout=8.0):
        cmd = list(argv)
        calls.append(cmd)
        if cmd == ["hyprctl", "clients", "-j"]:
            return 0, clients_json
        if cmd == ["hyprctl", "activewindow", "-j"]:
            return 0, active_json
        if cmd[:3] == ["grim", "-s", "1"]:
            out_file = tmp_path / "shot.png"
            out_file.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            return 0, ""
        return 0, "ok"

    monkeypatch.setattr(cp, "_run", _fake_run)
    info = PlatformInfo(
        os="linux",
        distro="omarchy",
        compositor="hyprland",
        display="wayland-1",
        tools={
            "hyprctl": True,
            "wtype": False,
            "wlrctl": True,
            "ydotool": True,
            "grim": True,
            "omarchy": True,
        },
    )
    adapter = HyprlandAdapter(info)

    # 1. list_windows + geometry
    wins = adapter.list_windows()
    assert len(wins) == 1
    assert wins[0].id == "0x55aabbcc"
    assert wins[0].title == "Zen Browser"
    assert (wins[0].x, wins[0].y, wins[0].width, wins[0].height) == (120, 80, 1280, 720)
    assert wins[0].focused is True

    # 2. get_active_window
    active = adapter.get_active_window()
    assert active is not None
    assert active.id == "0x55aabbcc"
    assert (active.x, active.y, active.width, active.height) == (120, 80, 1280, 720)

    # 3. focus_window & close_window with address normalization
    assert adapter.focus_window("0x55aabbcc") is True
    assert ["hyprctl", "dispatch", "focuswindow", "address:0x55aabbcc"] in calls

    # 4. send_key falls back to hyprctl dispatch sendshortcut when wtype is absent
    assert adapter.send_key("ctrl+l", window="0x55aabbcc") is True
    assert ["hyprctl", "dispatch", "sendshortcut", "CTRL, l, address:0x55aabbcc"] in calls

    # 5. mouse_move uses hyprctl dispatch movecursor, mouse_click uses wlrctl pointer click
    assert adapter.mouse_click(400, 300, button=1) is True
    assert ["hyprctl", "dispatch", "movecursor", "400", "300"] in calls
    assert ["wlrctl", "pointer", "click", "left"] in calls

    # 6. screenshot uses grim -s 1 (logical 1:1 coordinate scaling)
    shot_path = tmp_path / "shot.png"
    assert adapter.screenshot(shot_path) is True
    assert ["grim", "-s", "1", str(shot_path)] in calls


def test_debian_ubuntu_wmctrl_and_scrot_fallbacks(monkeypatch, tmp_path):
    """Verify Debian/Ubuntu wmctrl window listing/focus/close and scrot screenshot fallback."""
    from pathlib import Path

    import skaldir.cu_platform as cp

    calls: list[list[str]] = []
    wmctrl_out = (
        "0x02400003  0 1234   10   20  800  600 Navigator.Firefox     host Mozilla Firefox\n"
    )

    def _fake_run(argv, *, timeout=8.0):
        cmd = list(argv)
        calls.append(cmd)
        if cmd == ["wmctrl", "-lGpx"]:
            return 0, wmctrl_out
        if cmd[:2] == ["scrot", "--overwrite"]:
            Path(cmd[2]).write_bytes(b"\x89PNG\r\n\x1a\nfake")
            return 0, ""
        return 0, ""

    monkeypatch.setattr(cp, "_run", _fake_run)
    monkeypatch.setattr(cp, "_xlib_root_screenshot", lambda target, window_id="": False)

    info = PlatformInfo(
        os="linux",
        distro="ubuntu",
        compositor="gnome",
        display=":0",
        tools={
            "xdotool": False,
            "wmctrl": True,
            "gdbus": False,
            "scrot": True,
            "grim": False,
        },
    )
    adapter = GnomeAdapter(info)
    wins = adapter.list_windows()
    assert len(wins) == 1
    assert wins[0].id == "0x02400003"
    assert wins[0].pid == 1234
    assert (wins[0].x, wins[0].y, wins[0].width, wins[0].height) == (10, 20, 800, 600)
    assert adapter.focus_window("0x02400003") is True
    assert ["wmctrl", "-ia", "0x02400003"] in calls
    assert adapter.close_window("0x02400003") is True
    assert ["wmctrl", "-ic", "0x02400003"] in calls

    shot = tmp_path / "ubuntu.png"
    assert adapter.screenshot(shot) is True
    assert ["scrot", "--overwrite", str(shot)] in calls


def test_fedora_gnome_wayland_ydotool_syntax(monkeypatch):
    """Verify Fedora GNOME Wayland ydotool mousemove -a -x -y and hex click codes."""
    import skaldir.cu_platform as cp

    calls: list[list[str]] = []

    def _fake_run(argv, *, timeout=8.0):
        calls.append(list(argv))
        return 0, ""

    monkeypatch.setattr(cp, "_run", _fake_run)
    info = PlatformInfo(
        os="linux",
        distro="fedora",
        compositor="gnome",
        display=":0",
        tools={"xdotool": True, "ydotool": True, "wtype": False},
    )
    adapter = GnomeAdapter(info)
    assert adapter.mouse_click(250, 180, button=1) is True
    assert ["ydotool", "mousemove", "-a", "-x", "250", "-y", "180"] in calls
    assert ["ydotool", "click", "0xC0"] in calls

    calls.clear()
    assert adapter.mouse_drag(10, 20, 100, 200, button=1) is True
    assert ["ydotool", "click", "0x40"] in calls
    assert ["ydotool", "mousemove", "-a", "-x", "100", "-y", "200"] in calls
    assert ["ydotool", "click", "0x80"] in calls

