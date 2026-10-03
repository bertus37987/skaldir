"""A GUI child on the live desktop must start XWayland, not Wayland-native.

Two facts measured on this host, kept separate because only one of them is proven:

* A GUI app started from the **shell** environment inherits ``XDG_SESSION_TYPE=wayland`` and
  no ``GDK_BACKEND``. It then becomes a Wayland client, holds no X11 socket, and no X tool
  can see or drive it (measured: ``soffice.bin`` alive, file locked, 0 X11 sockets, no
  window).
* A GUI app started with the runner's own ``child_env()`` **does** become visible: with
  ``zenity --info`` the window maps within one second on the live desktop.

The integration gap those two facts expose: ``_child_env()`` built the right selectors all
along, but only for the runner's own children. An agent that started the application itself
got the shell's environment. ``child_env()``/``launch()`` close that.

**Not proven, and therefore never claimed:** that this alone makes LibreOffice Calc show a
window. Calc stayed windowless on the live session even with this environment.

**Explicitly forbidden (owner, 2026-09-26):** denying a child the D-Bus session bus or the
accessibility bus to suppress a consent dialog, and closing windows by title or process
name. A consent may be *necessary* for the chosen Wayland path; it is the user's to give.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from skaldir.cu_host_session import HostSessionRunner


@pytest.fixture()
def runner():
    return HostSessionRunner(target="soffice", attach_to_session=True,
                             allow_live_desktop=True, display=":0", timeout=5.0)


# --------------------------------------------------------------------------- #
# the environment a live child gets
# --------------------------------------------------------------------------- #

def test_the_child_env_forces_xwayland(runner):
    """This is the part that makes an app visible to X tools — it stays."""
    env = runner.child_env()
    assert env["GDK_BACKEND"] == "x11"
    assert env["QT_QPA_PLATFORM"] == "xcb"
    assert env["XDG_SESSION_TYPE"] == "x11"
    assert env["MOZ_ENABLE_WAYLAND"] == "0"
    assert env["ELECTRON_OZONE_PLATFORM_HINT"] == "x11"


def test_the_child_env_drops_the_wayland_socket(runner, monkeypatch):
    """The compositor socket must not survive: with it a GTK app is Wayland-native."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    env = runner.child_env()
    assert "WAYLAND_DISPLAY" not in env, env.get("WAYLAND_DISPLAY")
    assert env["DISPLAY"] == ":0"


def test_the_child_env_keeps_the_owners_home(runner):
    """A foreign HOME would give the app a stranger's LibreOffice profile."""
    env = runner.child_env()
    assert env["HOME"], "no HOME: the app would start with an empty profile"


def test_the_child_env_keeps_the_session_bus(runner, monkeypatch):
    """Denying the bus was tried and reverted.

    Reason: an app may legitimately need D-Bus, and a consent dialog may be a *necessary*
    consent that must stay transparent. The runner therefore does not strip the bus.
    """
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    assert runner.child_env().get("DBUS_SESSION_BUS_ADDRESS") == "unix:path=/run/user/1000/bus"


def test_the_child_env_keeps_the_accessibility_bus(runner, monkeypatch):
    """Accessibility is how a screen reader and several apps talk to the session."""
    monkeypatch.setenv("AT_SPI_BUS_ADDRESS", "unix:path=/run/user/1000/at-spi/bus")
    assert runner.child_env().get("AT_SPI_BUS_ADDRESS")


def test_an_owned_display_pins_its_own_display():
    r = HostSessionRunner(target="soffice", display=":95", window_size="1200x800x24")
    assert r.child_env()["DISPLAY"] == ":95"


def test_the_child_env_points_ydotool_at_the_probed_daemon(runner, monkeypatch, tmp_path):
    """The ydotool CLI child reaches the daemon the runner probed — not its own guess.

    Measured (survey 2026-10-03, feature input-type-charset-fix): ydotool 1.0.4 connects to
    ``$YDOTOOL_SOCKET`` or to ``/run/user/1000/.ydotool_socket``; neither matches the daemon
    this mission runs (``/tmp/skaldir-ydotoold/ydotoold.sock``), so a productive text send
    from a child env without the pin failed with rc 2 *before a single character moved*: "failed
    to connect socket `/run/user/1000/.ydotool_socket'". The child env therefore names the
    daemon the probe measured, and leaves an explicit ``YDOTOOL_SOCKET`` untouched.
    """
    from skaldir import input_backend as IB

    socket = tmp_path / "mission.sock"
    socket.touch()
    monkeypatch.delenv("YDOTOOL_SOCKET", raising=False)
    monkeypatch.setattr(IB, "MISSION_SOCKET", str(socket))
    monkeypatch.setattr(IB, "DEFAULT_SOCKET", str(tmp_path / "no-default.sock"))
    env = runner.child_env()
    assert env["YDOTOOL_SOCKET"] == str(socket), env.get("YDOTOOL_SOCKET")

    # an explicit env pin wins — the caller's choice, never overwritten
    monkeypatch.setenv("YDOTOOL_SOCKET", "/run/user/1000/.ydotool_socket")
    env = runner.child_env()
    assert env["YDOTOOL_SOCKET"] == "/run/user/1000/.ydotool_socket"

    # neither known socket exists → no invented path
    monkeypatch.delenv("YDOTOOL_SOCKET", raising=False)
    monkeypatch.setattr(IB, "MISSION_SOCKET", str(tmp_path / "gone.sock"))
    env = runner.child_env()
    assert "YDOTOOL_SOCKET" not in env


# --------------------------------------------------------------------------- #
# the consent rule the owner set
# --------------------------------------------------------------------------- #

def test_no_run_closes_a_window_by_title_or_process():
    """Permanently forbidden: closing a window on the strength of its title or process.

    This is the rule the owner set after the removed dialog-suppression work ("keine
    automatische Bestätigung oder pauschale Fenster-Schließroutine anhand von
    Titel/Prozessnamen"). If a close path reappears, this test fails by design.
    """
    path = Path(__file__).parent.parent / "src" / "skaldir" / "cu_host_session.py"
    body = path.read_text()
    assert "windowclose" not in body, (
        "a window-close path by title/process is forbidden — consent is the user's to give"
    )
    assert "_dismiss_remote_consent" not in body, (
        "the title-based consent suppression must stay removed"
    )


# --------------------------------------------------------------------------- #
# launch()
# --------------------------------------------------------------------------- #

def test_launch_uses_the_child_env_not_the_shell_one(runner, monkeypatch):
    """The measured gap: a plain Popen inherits `XDG_SESSION_TYPE=wayland`."""
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.delenv("GDK_BACKEND", raising=False)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    proc = runner.launch(["soffice", "--calc", "x.ods"])
    assert proc.env["GDK_BACKEND"] == "x11"
    assert proc.env["XDG_SESSION_TYPE"] == "x11"
    assert "WAYLAND_DISPLAY" not in proc.env
    assert proc.argv == ["soffice", "--calc", "x.ods"]


def test_launch_keeps_the_session_bus_if_there_is_one(runner, monkeypatch):
    """launch() must not become a covert way of re-introducing the stripped bus."""
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    proc = runner.launch(["zenity", "--info"])
    assert proc.env.get("DBUS_SESSION_BUS_ADDRESS")


def test_launch_detaches_the_child(runner, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    proc = runner.launch(["soffice"])
    assert proc.kwargs.get("start_new_session") is True, (
        "the app must not die with the run that started it"
    )


def test_launch_accepts_a_real_command_path(runner, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    proc = runner.launch(["/usr/bin/soffice", "--calc", "/tmp/a b.ods"])
    assert proc.argv[0] == "/usr/bin/soffice"
    # a path with a space stays one argument
    assert proc.argv[-1] == "/tmp/a b.ods"


class _FakePopen:
    """Records what the runner actually asked for."""

    def __init__(self, argv, **kwargs):
        self.argv = list(argv)
        self.env = dict(kwargs.get("env") or {})
        self.kwargs = kwargs
