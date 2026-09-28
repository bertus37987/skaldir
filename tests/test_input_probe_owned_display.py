"""Der Probe auf einem EIGENEN X-Display: Xvfb + eigener Fenstermanager, echte Zustellung.

Was hier bewiesen wird — die ganze Kette, mit echten Prozessen und einem echten X-Server:

1. der Probe **erzeugt sein eigenes Fenster** (frischer Prozess, eigene PID, eigener Fenster-Id),
2. **aktiviert** es und bestätigt die Bindung (Server-Fokus, ``WM_STATE``, ``_NET_WM_PID``),
3. sendet einen Zufalls-Marker als Text,
4. der **eigene Empfänger** des Fensters meldet diesen Marker zurück, und
5. der Probe räumt **genau** seinen eigenen Fensterprozess und seine eigenen Temp-Dateien weg.

Was hier NICHT bewiesen wird: die Zustellung auf dem echten Host-Desktop des Nutzers
(GNOME/Wayland, Xwayland ``:0``). Dafür gibt es die Live-Abnahme (VAL-INPUT-007); ein
Xvfb-Ergebnis ist ein Fixture-Ergebnis und wird hier auch so benannt.

Der Display ist privat: Er wird nur gestartet, wenn die Nummer wirklich frei war, und nur die
beiden Prozesse, die dieser Test selbst gestartet hat, werden wieder beendet.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from skaldir import input_backend as IB
from skaldir import probe_target as PT
from skaldir.input_backend import ProbeState

DISPLAY_NUMBERS = range(91, 131)


def _display_taken(number: int) -> bool:
    lock = Path(f"/tmp/.X{number}-lock")
    socket = Path(f"/tmp/.X11-unix/X{number}")
    return lock.exists() or socket.exists()


@pytest.fixture(scope="module")
def own_display():
    """A private Xvfb with the lightest available window manager — ours, not the user's."""
    tool = [name for name in ("Xvfb", "xdotool", "xprop", "openbox")
            if subprocess.run(["which", name], capture_output=True).returncode != 0]
    if tool:
        pytest.skip(f"the owned-display probe needs {', '.join(tool)} on this host")

    number = next((n for n in DISPLAY_NUMBERS if not _display_taken(n)), None)
    if number is None:
        pytest.skip("no free X display number between 91 and 130")
    display = f":{number}"
    env = {**os.environ, "DISPLAY": display}

    xvfb = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", "800x600x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        for _ in range(60):
            if _display_taken(number):
                break
            if xvfb.poll() is not None:
                pytest.skip(f"Xvfb on {display} exited immediately")
            time.sleep(0.1)
        else:
            pytest.skip(f"Xvfb on {display} never came up")

        # A window manager, because `xdotool windowactivate --sync` is a _NET_ACTIVE_WINDOW
        # request: without one, activation does nothing and the probe would (rightly) refuse.
        window_manager = subprocess.Popen(
            ["openbox"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, env=env, start_new_session=True,
        )
        time.sleep(1.5)
        yield display, env
        if window_manager.poll() is None:
            window_manager.terminate()
            try:
                window_manager.wait(timeout=5)
            except subprocess.TimeoutExpired:
                window_manager.kill()
                window_manager.wait(timeout=5)
    finally:
        if xvfb.poll() is None:
            xvfb.terminate()
            try:
                xvfb.wait(timeout=5)
            except subprocess.TimeoutExpired:
                xvfb.kill()
                xvfb.wait(timeout=5)


def _real_send(argv, timeout, given_env):
    def _send(inner_argv, inner_timeout, env=None):
        # the display the probe was created on travels with the send — never the caller's
        return PT.run_command([str(part) for part in inner_argv], inner_timeout,
                              env=env or given_env)
    del argv, timeout
    return _send


def test_the_probe_window_delivers_its_own_marker(own_display, monkeypatch):
    """The whole chain on a real X server: own window → confirmed binding → marker → delivered."""
    display, env = own_display
    monkeypatch.setattr(IB, "backend_available", lambda name: (True, "present on this host"))
    marker = IB.new_marker()

    found = IB.probe(
        "xdotool",
        timeout=8.0,
        marker=marker,
        display=display,
        send=_real_send(None, None, env),
        focus=None,
    )

    assert found.state is ProbeState.INPUT_OBSERVED_AT_TARGET, found.detail
    assert found.delivers is True
    assert found.sent is True
    assert found.window["pid"], "the probe recorded its own receiver pid"
    assert found.window["wid"], "the probe recorded its own window id"
    assert found.window["confirmed"] is True, found.window.get("reason")
    assert "".join(item["char"] for item in found.records) == marker, (
        "the correlated records are the marker, keystroke by keystroke"
    )
    # the binding the probe claims to have confirmed, check by check
    checks = found.window["checks"]
    assert checks["server_focus"] == found.window["wid"]
    assert checks["net_wm_pid"] == found.window["pid"]
    assert checks["title_is_ours"] is True
    assert checks["wm_state"] in ("Normal", "(keiner)")


def test_the_probe_leaves_only_its_own_resources_behind(own_display, monkeypatch):
    """After the probe: its own window is gone and its temp directory is gone with it."""
    display, own_env = own_display
    monkeypatch.setattr(IB, "backend_available", lambda name: (True, "present on this host"))
    seen: dict = {}

    def _remember(argv, timeout, env=None):
        # `env` is the display the probe's own window lives on — it travels with the send
        seen.setdefault("display", dict(env or {}).get("DISPLAY", ""))
        return PT.run_command([str(part) for part in argv], timeout, env=env or own_env)

    marker = IB.new_marker()
    found = IB.probe("xdotool", timeout=8.0, marker=marker, display=display,
                     send=_remember, spawn=None)
    assert found.delivers is True
    assert seen.get("display") == display, seen
    wid = int(found.window["wid"])
    pid = int(found.window["pid"])
    log = Path(found.window["log"])

    code, _output = PT.run_command(["xdotool", "getwindowname", str(wid)], 10.0, env=own_env)
    assert code != 0, "the probe's own window is gone — its receiver stopped"
    assert not Path(f"/proc/{pid}").exists(), "the probe's own receiver process is gone"
    assert not log.exists(), "the probe's own log file is removed with its own directory"
    assert not log.parent.exists(), "the probe's own temp directory is removed"


def test_the_receiver_window_is_real_and_mapped(own_display):
    """The receiver really maps a window on this display, with its own pid published."""
    display, env = own_display
    window = PT.spawn_probe_window(display=display, timeout=10.0)
    try:
        assert window.binding.confirmed is True, window.binding.reason
        assert window.binding.map_state == "IsViewable", window.binding.map_state
        assert window.binding.checks["net_wm_pid"] == window.binding.pid
        code, output = PT.run_command(
            ["xdotool", "getwindowname", str(window.binding.wid)], 10.0, env=env
        )
        assert code == 0
        assert str(window.binding.run) in output, "the window carries this probe's own nonce"
    finally:
        report = window.destroy()
    assert report["terminated"] is True
    assert report["window_gone"] is True
