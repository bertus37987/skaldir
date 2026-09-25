"""The host path and the live-desktop opt-in: explicit, target-scoped, default-off (GAP-K3).

Evidence class: unit (the gate) plus **real execution** on an owned display (an Xvfb this
test starts and stops itself).

Why this file exists: `act check` used to answer `live_desktop_allowed: false` with no
switch anywhere — a hard-coded value in two places, so a user could not tell "missing
feature" from "deliberate refusal", and no host runner existed at all. This wave adds both,
under three rules that are pinned here:

1. **The default does not move.** No opt-in, no host session, no live desktop; the policy
   still refuses a runner whose isolation is ``none``.
2. **The opt-in is target-scoped.** ``allow_live_desktop`` without a named target is not an
   opt-in: the runner refuses it, and the policy only lets a live runner through when the
   target it declares matches the one the policy names.
3. **Nothing here ever aims at "the desktop".** Keys go through ``windowfocus --sync <id>``,
   a pointer action must land inside the target window's geometry, a capture is
   ``xwd -id``/``import -window <id>``, and a title that matches two windows is refused
   instead of guessed.

The test host is a GNOME Wayland session. Everything below runs on a display this test owns
(``:91``+), never on ``:0``: the user's own desktop is not touched by this suite.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 — fixed argv lists, no shell
import sys
import time
from pathlib import Path

import pytest

from skaldir import act_service as A
from skaldir.computer_use import (
    Action,
    ActionResult,
    Attempt,
    CUExecutor,
    CUPolicy,
    Observation,
    PolicyViolation,
    ScreenCapture,
    Verification,
    png_size,
)
from skaldir.cu_host_session import (
    ENV_ALLOW,
    ENV_TARGET,
    REGISTRY_ENV,
    SETTINGS_KEY,
    TARGET_KEY,
    HostSessionRunner,
    TargetScopeRefused,
    host_session_availability,
    live_desktop_optin,
    live_pointer_argv_is_bound,
    live_window_argv_is_bound,
    reap_orphaned_displays,
)

HAVE_XVFB = bool(shutil.which("Xvfb") and shutil.which("xdotool"))
needs_display = pytest.mark.skipif(not HAVE_XVFB, reason="Xvfb/xdotool are not installed")


# --------------------------------------------------------------------------- #
# the gate: default off, target required                                        #
# --------------------------------------------------------------------------- #


def test_the_opt_in_is_off_and_says_how_to_turn_it_on(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(ENV_ALLOW, raising=False)
    monkeypatch.delenv(ENV_TARGET, raising=False)
    # only this runtime: never inherit the host's settings.yaml
    (tmp_path / "settings.yaml").write_text("computer_use:\n  allow_live_desktop: false\n")
    state = live_desktop_optin(tmp_path)
    assert state["allowed"] is False
    assert state["opted_in"] is False
    # the reason names the exact switch — never a bare false
    assert "live-setup" in state["reason"] or ENV_ALLOW in state["reason"]
    assert "live" in state["reason"].lower()
    assert ENV_ALLOW in state["reason"]


def test_an_opt_in_without_a_target_is_full_desktop(monkeypatch, tmp_path) -> None:
    """Owner's rule: live-desktop darf alles steuern — no target = the whole desktop."""
    monkeypatch.setenv(ENV_ALLOW, "1")
    monkeypatch.delenv(ENV_TARGET, raising=False)
    (tmp_path / "settings.yaml").write_text("computer_use:\n  allow_live_desktop: false\n")
    state = live_desktop_optin(tmp_path)
    assert state["opted_in"] is True
    assert state["allowed"] is True
    assert state["full_desktop"] is True


def test_opt_in_with_a_target_is_allowed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(ENV_ALLOW, "1")
    monkeypatch.setenv(ENV_TARGET, "some-window-title")
    state = live_desktop_optin(tmp_path)
    assert state["allowed"] is True
    # Owner rule: any opt-in is the FULL desktop — the name is a focus hint only
    assert state["full_desktop"] is True
    assert ENV_ALLOW in state["source"]


def test_attaching_to_the_session_needs_both_switches(monkeypatch) -> None:
    with pytest.raises(TargetScopeRefused) as no_optin:
        HostSessionRunner(attach_to_session=True, display=":0", target="x")
    assert ENV_ALLOW in str(no_optin.value)
    with pytest.raises(TargetScopeRefused) as no_target:
        HostSessionRunner(
            attach_to_session=True, display=":0", target="", allow_live_desktop=True
        )
    assert "target" in str(no_target.value)


class _LiveishRunner:
    """A stand-in for the live runner: only its declared scope and isolation matter here."""

    isolation = "live-desktop"

    def __init__(self, scope: str) -> None:
        self.target_scope = scope

    @property
    def is_isolated(self) -> bool:
        return False


def _attempt(policy: CUPolicy, runner) -> str:
    executor = CUExecutor(policy)
    try:
        executor._check_policy([], runner)  # the gate, called directly
    except PolicyViolation as exc:
        return f"refused: {exc}"
    return "allowed"


def test_the_policy_refuses_a_live_runner_the_default_way() -> None:
    runner = _LiveishRunner("some-window")
    assert _attempt(CUPolicy(), runner).startswith("refused")
    # Owner rule: allow_live_desktop IS the full desktop — any window, any app
    optin = CUPolicy(allow_live_desktop=True, live_desktop_target="another-window")
    assert _attempt(optin, runner) == "allowed"
    matching = CUPolicy(allow_live_desktop=True, live_desktop_target="some-window")
    assert _attempt(matching, runner) == "allowed"


def test_a_dry_run_policy_still_refuses_everything_else() -> None:
    assert _attempt(CUPolicy(dry_run=True), _LiveishRunner("w")).startswith("refused")


# --------------------------------------------------------------------------- #
# the host-session runner on a display this test owns                           #
# --------------------------------------------------------------------------- #


@needs_display
def test_host_session_owns_its_display_and_cleans_it_up(tmp_path) -> None:
    """Real execution: an Xvfb this run starts, a real window, a real capture, then gone."""
    runner = HostSessionRunner(timeout=60.0)
    try:
        ok, detail = runner.start()
        assert ok, detail
        display = runner.display
        assert display and display != os.environ.get("DISPLAY", ":0")
        assert runner.is_isolated is True
        assert runner.isolation == "host-session"
        title = f"godsv-cu-host-{time.time_ns() % 100000}"
        shown = runner.act(
            Action(
                kind="cli",
                argv=[
                    "sh",
                    "-c",
                    f"(zenity --info --title={title} --text='host session' "
                    ">/dev/null 2>&1 &); sleep 3; echo LAUNCHED",
                ],
            )
        )
        assert shown.ok, shown.detail
        observation = runner.observe()
        assert title in observation.text, observation.text
        capture, why = runner.screenshot(tmp_path / "host.png", label=title)
        assert capture is not None, why
        assert isinstance(capture, ScreenCapture)
        assert capture.size_bytes > 0
        assert capture.scope == "root"
    finally:
        runner.close()
    record = runner.cleanup_record()
    assert record["closed"] is True
    assert record["still_present"] is False
    # the owned display is really gone: the X socket and the lock file are removed
    number = str(display).lstrip(":")
    assert not os.path.exists(f"/tmp/.X11-unix/X{number}")


@needs_display
def test_the_owned_display_never_uses_the_users_session(tmp_path) -> None:
    """The child environment is pinned to X so no window lands in the real session."""
    runner = HostSessionRunner(timeout=60.0)
    try:
        ok, _detail = runner.start()
        assert ok
        env = runner._child_env()
        assert env["DISPLAY"] == runner.display
        assert "WAYLAND_DISPLAY" not in env, "a GTK/Qt app would go to the real compositor"
        assert env["GDK_BACKEND"] == "x11"
        assert env["QT_QPA_PLATFORM"] == "xcb"
    finally:
        runner.close()


def test_host_session_availability_is_measured_not_assumed() -> None:
    report = host_session_availability()
    assert set(report) == {"available", "detail", "tools"}
    assert report["detail"], "an availability answer always carries its reason"


# --------------------------------------------------------------------------- #
# the report the CLI prints                                                     #
# --------------------------------------------------------------------------- #


def test_act_check_reports_the_opt_in_and_the_host_session(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(ENV_ALLOW, raising=False)
    monkeypatch.delenv(ENV_TARGET, raising=False)
    report = A.capability_report()
    assert report["live_desktop_allowed"] is False
    assert report["live_desktop"] is False
    assert report["verdict"] in ("isolated-only", "blocked")
    ids = [record["id"] for record in report["bridges"]]
    assert "host-session" in ids
    assert report["live_desktop_optin"]["reason"]
    names = report["capability_names"]
    assert "observe.screenshot" in names
    shot = next(
        item for item in report["capabilities"] if item["name"] == "observe.screenshot"
    )
    # The capture is real now: it is available whenever the image is, and its driver is xwd.
    assert "xwd" in shot["driver"]
    assert "import" not in shot["missing"] if shot.get("missing") else True

    monkeypatch.setenv(ENV_ALLOW, "1")
    monkeypatch.setenv(ENV_TARGET, "a-named-window")
    allowed = A.capability_report()
    assert allowed["live_desktop_allowed"] is True
    assert allowed["verdict"] == "live-desktop-opted-in"
    live = next(item for item in allowed["bridges"] if item["id"] == "live-desktop")
    assert live["available"] is True
    # full desktop: the name is a hint, the capability is the whole session
    assert live["opt_in_target"] in ("*", "a-named-window")


def test_the_runner_resolution_refuses_a_live_desktop_without_the_opt_in(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv(ENV_ALLOW, raising=False)
    monkeypatch.delenv(ENV_TARGET, raising=False)
    runner, record = A.resolve_runner("live-desktop", timeout=30.0)
    assert runner is None
    assert record["resolved"] is False
    assert "opt-in" in record["reason"]


def test_the_settings_opt_in_alone_is_enough_for_the_policy(monkeypatch, tmp_path) -> None:
    """Wave A: `settings.yaml` opt-in must not die at PolicyViolation after resolve.

    ``resolve_runner`` already built the live runner from the settings opt-in; the
    executor refused it because only ``--allow-live-desktop`` used to sync the policy.
    """
    monkeypatch.delenv(ENV_ALLOW, raising=False)
    monkeypatch.delenv(ENV_TARGET, raising=False)

    class FakeLive:
        is_isolated = False
        isolation = "live-desktop"
        target_scope = "Firefox"

    synced = A._sync_live_policy(
        A.default_policy(),
        FakeLive(),
        allow_live_desktop=False,
        live_target="",
    )
    # Without an opt-in the policy stays closed.
    assert synced.allow_live_desktop is False

    monkeypatch.setenv(ENV_ALLOW, "1")
    monkeypatch.setenv(ENV_TARGET, "Firefox")
    synced = A._sync_live_policy(
        A.default_policy(),
        FakeLive(),
        allow_live_desktop=False,
        live_target="",
    )
    assert synced.allow_live_desktop is True
    assert synced.live_desktop_target == "Firefox"


def test_an_isolated_runner_is_never_widened_by_the_policy_sync() -> None:
    class FakeOwned:
        is_isolated = True
        isolation = "host-session"
        target_scope = ":91"

    policy = A._sync_live_policy(A.default_policy(), FakeOwned(), allow_live_desktop=True)
    assert policy.allow_live_desktop is False


def test_the_host_session_runner_resolves_without_any_opt_in(tmp_path) -> None:
    """The owned-display path needs no opt-in: it touches nothing of the user's."""
    if not HAVE_XVFB:
        pytest.skip("Xvfb/xdotool are not installed")
    runner, record = A.resolve_runner("host-session", timeout=30.0)
    assert runner is not None, record["reason"]
    assert record["bridge"] == "host-session"
    assert record["real"] is True
    assert runner.target_scope == ""
    runner.close()


# --------------------------------------------------------------------------- #
# report 18 — the boundary blockers and the lifecycle defects (F1/F2/F4-F8)     #
# --------------------------------------------------------------------------- #
#
# These pin the class, not the instance:
#
# * F1: a live-session action can never carry a command. The scope rule is *structural*
#   (xdotool, the resolved window id at the window-operand position) instead of the old
#   substring test over the joined argv — which accepted any program that mentioned the
#   target in a comment and any command that drove a *different* window.
# * F2: an action the runner refused is never recorded `verified` (evidence integrity).
# * F4/F5/F6: a display another process owns is never claimed, an owned display is
#   reaped by an owner-scoped recovery (never a blind kill), and `close()` is bounded and
#   leaves no zombie.
# * F7: a live-mode capture cannot be aimed at a window that is not the target.
# * F8: the geometry-checked pointer path is reachable from the product path again.
# * the host capture reports the width/height the PNG really declares (report 16 §9.2).
#
# Every display used here is started by the test itself on a number that was free, and
# stopped again; :0/:1 (the owner's session) is never touched.


def _free_display_number(start: int = 191, limit: int = 40) -> str:
    """A display number nothing owns right now — what the test may claim itself."""
    for number in range(start, start + limit):
        if not Path(f"/tmp/.X{number}-lock").exists() and not Path(
            f"/tmp/.X11-unix/X{number}"
        ).exists():
            return f":{number}"
    pytest.skip("no free X display number in the test range")


def _start_own_xvfb(display: str) -> subprocess.Popen:
    """Start an Xvfb the test owns (and will stop), on a number it just verified free."""
    number = display.lstrip(":")
    proc = subprocess.Popen(  # nosec B603 — fixed argv list, no shell  # noqa: S603
        ["Xvfb", display, "-screen", "0", "1024x768x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(100):
        if Path(f"/tmp/.X11-unix/X{number}").exists():
            return proc
        time.sleep(0.1)
    _stop_own_xvfb(proc)
    pytest.skip(f"Xvfb {display} did not come up")


def _stop_own_xvfb(proc: subprocess.Popen, display: str = "") -> None:
    """Stop **this test's** Xvfb and remove its files once the pid is really gone."""
    try:
        if proc.poll() is None:
            proc.terminate()
        proc.wait(timeout=10)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
    if display:
        try:
            os.kill(proc.pid, 0)
        except OSError:
            number = display.lstrip(":")
            for name in (f"/tmp/.X11-unix/X{number}", f"/tmp/.X{number}-lock"):
                try:
                    Path(name).unlink()
                except OSError:
                    pass


_WINDOWS_APP = '''
import sys
import tkinter as tk

target, victim, marker = sys.argv[1], sys.argv[2], sys.argv[3]
root = tk.Tk()
root.title("cu-root-container")


def fired(*_args):
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write("the target window's own click handler fired\\n")


win_a = tk.Toplevel(root)
win_a.title(target)
win_a.geometry("300x200+20+20")
tk.Label(win_a, text="target window").pack(padx=10, pady=6)
tk.Button(win_a, text="CLICK-ME", command=fired).pack()
win_a.bind("<Button-1>", fired)
win_b = tk.Toplevel(root)
win_b.title(victim)
win_b.geometry("300x200+400+300")
tk.Label(win_b, text="victim window").pack(padx=40, pady=40)
root.after(120_000, root.destroy)
root.mainloop()
'''


def _env(display: str) -> dict:
    return {**os.environ, "DISPLAY": display, "WAYLAND_DISPLAY": ""}


def _xdo(display: str, *argv: str) -> str:
    proc = subprocess.run(  # nosec B603 — fixed argv list, no shell  # noqa: S603
        ["xdotool", *argv], capture_output=True, text=True, env=_env(display), timeout=20
    )
    return (proc.stdout or "").strip()


def _window_geometry(display: str, wid: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in _xdo(display, "getwindowgeometry", "--shell", wid).splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            if value.strip().lstrip("-").isdigit():
                values[key.strip()] = int(value.strip())
    return values


def _start_windows(tmp_path: Path, display: str) -> tuple[subprocess.Popen, Path]:
    """Two real windows on a display this test owns; returns the app and its click marker."""
    script = tmp_path / "windows_app.py"
    script.write_text(_WINDOWS_APP, encoding="utf-8")
    marker = tmp_path / "clicked.txt"
    app = subprocess.Popen(  # nosec B603 — fixed argv list, no shell  # noqa: S603
        [sys.executable, str(script), "cu-target-probe", "cu-victim-probe", str(marker)],
        env=_env(display),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(150):
        target = _xdo(display, "search", "--name", "cu-target-probe")
        victim = _xdo(display, "search", "--name", "cu-victim-probe")
        if target.strip() and victim.strip():
            return app, marker
        time.sleep(0.1)
    _stop_app(app)
    pytest.skip("the probe windows did not appear on the test display")


def _stop_app(app: subprocess.Popen) -> None:
    try:
        if app.poll() is None:
            app.terminate()
        app.wait(timeout=10)
    except (OSError, subprocess.SubprocessError):
        try:
            app.kill()
            app.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass


def _measure_clickable_point(
    display: str, target_id: str, geometry: dict[str, int]
) -> tuple[int, int]:
    """The point of the target window the server really puts under the pointer.

    Tk nests its toplevels in a wrapper window, so the rectangle ``getwindowgeometry``
    reports (the one the runner validates the payload against) is not always the rectangle
    the pointer sees. The test *measures* the intersection with ``getmouselocation`` instead
    of assuming it — and the payload it then hands the runner is still inside the rectangle
    the runner validates.
    """
    left, top = geometry["X"], geometry["Y"]
    width, height = geometry["WIDTH"], geometry["HEIGHT"]

    def over(x: int, y: int) -> bool:
        _xdo(display, "mousemove", "--sync", str(x), str(y))
        shell = _xdo(display, "getmouselocation", "--shell")
        return any(token == f"WINDOW={target_id}" for token in shell.split())

    mid_y = top + height // 2
    xs = [x for x in range(left + 2, left + width - 2, 4) if over(x, mid_y)]
    if not xs:
        pytest.skip("the target window is not reachable by the pointer on this host")
    mid_x = (min(xs) + max(xs)) // 2
    ys = [y for y in range(top + 2, top + height - 2, 4) if over(mid_x, y)]
    if not ys:
        pytest.skip("the target window is not reachable by the pointer on this host")
    return mid_x, (min(ys) + max(ys)) // 2


def test_the_live_scope_rule_is_structural_not_textual() -> None:
    """F1: the target must be the *window operand*, not a substring of the argument list."""
    wid = "2097180"
    victim = "2097175"
    # the shapes the runner builds are bound
    assert live_window_argv_is_bound(["xdotool", "windowfocus", "--sync", wid], wid)
    assert live_window_argv_is_bound(["xdotool", "windowfocus", "--sync", wid, "key", "a"], wid)
    assert live_window_argv_is_bound(["xdotool", "getwindowname", wid], wid)
    assert live_window_argv_is_bound(["xdotool", "getwindowgeometry", "--shell", wid], wid)
    # every argument list the audit used to escape the target is refused
    hostile = [
        ["sh", "-c", f"id -u > /tmp/pwned; echo RAN # {wid}"],
        ["/bin/echo", wid],
        ["sh", "-c", f"xdotool windowfocus --sync {victim}; xdotool getwindowfocus # {wid}"],
        ["xdotool", "getwindowname", victim],
        ["xdotool", "windowfocus", "--sync", victim],
        ["xdotool", "search", "--name", wid],
        ["xdotool", "windowfocus", "--sync", wid, "key", "a; rm -rf /"],
        ["xdotool", "getwindowname", f"{wid}\n{victim}"],
    ]
    for argv in hostile:
        assert not live_window_argv_is_bound(argv, wid), argv
    # the operand must be the *resolved id*: the title (which the runner resolves first, and
    # which a window may share with a second window) is never a scope token
    assert not live_window_argv_is_bound(["xdotool", "getwindowname", "cu-target-probe"], wid)
    assert not live_window_argv_is_bound(["xdotool", "getwindowname", f"{wid}"], "cu-target-probe")


def test_the_live_pointer_rule_requires_the_targets_own_geometry() -> None:
    """F8: a pointer action is bound to a point inside the target, and nothing else."""
    geometry = {"X": 20, "Y": 20, "WIDTH": 300, "HEIGHT": 200}
    assert live_pointer_argv_is_bound(
        ["xdotool", "mousemove", "--sync", "133", "51", "click", "1"], geometry
    )
    assert live_pointer_argv_is_bound(["xdotool", "mousemove", "--sync", "133", "51"], geometry)
    assert live_pointer_argv_is_bound(
        ["xdotool", "mousemove", "--sync", "133", "51", "click", "--repeat", "3", "5"], geometry
    )
    outside = [
        ["xdotool", "mousemove", "--sync", "133", "700", "click", "1"],  # below the window
        ["xdotool", "mousemove", "--sync", "10", "51", "click", "1"],  # left of the window
        ["xdotool", "mousemove", "--sync", "133", "51", "xdotool", "click", "1"],
        ["xdotool", "mousemove", "--sync", "133", "51", "click", "1", "extra"],
        ["sh", "-c", "xdotool mousemove --sync 133 51 click 1"],
    ]
    for argv in outside:
        assert not live_pointer_argv_is_bound(argv, geometry), argv


def test_a_refused_action_is_never_recorded_verified() -> None:
    """F2: the runner refused it, so nothing about its effect may be 'verified'."""

    class _Refusing:
        isolation = "live-desktop"
        target_scope = "cu-target-probe"

        @property
        def is_isolated(self) -> bool:
            return False

        def act(self, _action):  # noqa: ANN001, ANN202
            return ActionResult(ok=False, detail="the runner refused this action")

        def observe(self):  # noqa: ANN202
            # The observation would satisfy the declaration: the old bug reported a success.
            return Observation(text="cu-target-probe", detail="exit 0")

        def cleanup_record(self) -> dict:
            return {"container": "", "prefix": "", "closed": True, "still_present": False}

        def close(self) -> None:
            return None

    policy = CUPolicy(allow_live_desktop=True, live_desktop_target="cu-target-probe")
    attempt = Attempt(
        channel="api",
        action=Action(kind="mouse", payload={"x": 1, "y": 1, "click": True}),
        verify=Verification(expect_observe_contains="cu-target-probe"),
    )
    outcome = CUExecutor(policy).run("step", [attempt], _Refusing())
    assert outcome.status == "aborted", outcome.status
    assert "did not run" in outcome.detail

    record = A.run_action_list(
        [
            {
                "kind": "mouse",
                "channel": "api",
                "payload": {"x": 1, "y": 1, "click": True},
                "verify": {"expect_observe_contains": "cu-target-probe"},
            }
        ],
        runner=_Refusing(),
        allow_live_desktop=True,
        live_target="cu-target-probe",
        end_state={"channel": "window_title", "expect_observe_contains": "cu-target-probe"},
        runtime=None,
        write_record=False,
    )
    assert record["status"] == "verification_failed"
    assert record["verified"] is False
    assert record["exit_code"] == A.EXIT_VERIFICATION_FAILED
    assert record["actions"][0]["status"] != "verified"


@needs_display
def test_a_live_action_may_not_carry_its_own_argv(monkeypatch) -> None:
    """F1: no caller argv runs in live mode — for any kind, including the safe defaults."""
    monkeypatch.setenv("DISPLAY", ":1")  # never the owner's session: this target is an id
    runner = HostSessionRunner(
        attach_to_session=True, allow_live_desktop=True, target="2097180", display=":1"
    )
    cases = [
        Action(kind="cli", argv=["/bin/echo", "unscoped"]),
        Action(kind="cli", argv=["sh", "-c", "id -u > /tmp/pwned # 2097180"]),
        Action(kind="window", argv=["sh", "-c", "xdotool windowfocus --sync 2097175 # 2097180"]),
        Action(kind="key", payload={"key": "a"}, argv=["sh", "-c", "echo OVERRIDDEN"]),
        Action(kind="type_text", payload={"text": "hi"}, argv=["sh", "-c", "echo OVERRIDDEN"]),
    ]
    for action in cases:
        result = runner.act(action)
        assert result.ok is False, action
        assert "argv" in result.detail or "cannot execute" in result.detail, result.detail
    assert not Path("/tmp/pwned").exists()


@needs_display
def test_a_display_another_process_owns_is_never_claimed(monkeypatch) -> None:
    """F4: a socket that was there before ``Popen`` is not ownership."""
    display = _free_display_number()
    owner = _start_own_xvfb(display)
    monkeypatch.setenv("DISPLAY", display)
    try:
        assert Path(f"/tmp/.X11-unix/X{display.lstrip(':')}").exists()
        runner = HostSessionRunner(display=display, timeout=20.0)
        ok, detail = runner.start()
        assert ok is False, detail
        assert "already in use" in detail
        assert runner._owned is None
        # and the run can not drive that display either
        refused = runner.act(Action(kind="cli", argv=["xdotool", "getdisplaygeometry"]))
        assert refused.ok is False
        assert runner.cleanup_record()["still_present"] is False
        assert owner.poll() is None, "the test's own Xvfb must not be touched by the runner"
        assert Path(f"/tmp/.X11-unix/X{display.lstrip(':')}").exists()
    finally:
        _stop_own_xvfb(owner, display)


@needs_display
def test_close_is_bounded_and_leaves_no_zombie() -> None:
    """F6: the child is waited for — no fixed 5 s teardown, no unreaped zombie."""
    runner = HostSessionRunner(timeout=20.0)
    ok, detail = runner.start()
    assert ok, detail
    pid = runner._owned.pid
    display = runner.display
    started = time.time()
    runner.close()
    seconds = time.time() - started
    assert seconds < 3.0, f"close() took {seconds:.1f}s"
    state = Path(f"/proc/{pid}/stat")
    if state.is_file():
        assert state.read_text().split()[2] != "Z", "the Xvfb child was left as a zombie"
    assert not Path(f"/tmp/.X11-unix/X{display.lstrip(':')}").exists()
    assert runner.cleanup_record()["still_present"] is False


@needs_display
def test_the_orphan_reaper_is_owner_scoped(monkeypatch, tmp_path) -> None:
    """F5: a crashed run's display is reclaimed; nobody else's process is ever signalled."""
    registry = tmp_path / "registry"
    monkeypatch.setenv(REGISTRY_ENV, str(registry))
    child_code = (
        "import os,sys;"
        "from skaldir.cu_host_session import HostSessionRunner;"
        "r=HostSessionRunner();ok,d=r.start();print('PID',r._owned.pid,flush=True);"
        "print('DISPLAY',r.display,flush=True);os._exit(0)"
    )
    child = subprocess.run(  # nosec B603 — fixed argv list, no shell  # noqa: S603
        [sys.executable, "-c", child_code],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    leaked_pid, leaked_display = 0, ""
    for token in child.stdout.split():
        if token.isdigit():
            leaked_pid = int(token)
        if token.startswith(":") and len(token) <= 4:
            leaked_display = token
    assert leaked_pid and leaked_display, child.stdout
    number = leaked_display.lstrip(":")
    assert Path(f"/tmp/.X11-unix/X{number}").exists(), "the orphan should still be there"

    # a run that is still alive owns its display: never touched
    kept = registry / "display-198.json"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(
        json.dumps({"display": ":198", "pid": os.getpid(), "owner_pid": os.getpid()}),
        encoding="utf-8",
    )
    # an entry whose pid is not an Xvfb (pid reuse, or someone else's process): never killed
    bystander = subprocess.Popen(["sleep", "60"])  # nosec B603,B607 — a test bystander
    foreign = registry / "display-197.json"
    foreign.write_text(
        json.dumps({"display": ":197", "pid": bystander.pid, "owner_pid": 999_999}),
        encoding="utf-8",
    )
    try:
        report = reap_orphaned_displays()
        assert [row["display"] for row in report["reaped"]] == [leaked_display]
        assert report["kept"] and report["kept"][0]["display"] == ":198"
        assert report["foreign"] and report["foreign"][0]["display"] == ":197"
        assert not Path(f"/tmp/.X11-unix/X{number}").exists()
        assert not Path(f"/tmp/.X{number}-lock").exists()
        try:
            os.kill(leaked_pid, 0)
            still = True
        except OSError:
            still = False
        assert still is False, "the orphaned Xvfb must be gone"
        assert bystander.poll() is None, "the bystander process was signalled"
        assert kept.is_file(), "a live run's registry entry must be left alone"
    finally:
        bystander.terminate()
        try:
            bystander.wait(timeout=5)
        except subprocess.SubprocessError:
            bystander.kill()
        if Path(f"/tmp/.X{number}-lock").exists():
            Path(f"/tmp/.X{number}-lock").unlink()
        if Path(f"/tmp/.X11-unix/X{number}").exists():
            Path(f"/tmp/.X11-unix/X{number}").unlink()


@needs_display
def test_the_live_pointer_path_clicks_inside_the_target_and_refuses_outside(
    monkeypatch, tmp_path
) -> None:
    """F8 + F1: the pointer action runs (built from payload+geometry) and cannot leave it."""
    display = _free_display_number()
    server = _start_own_xvfb(display)
    app: subprocess.Popen | None = None
    marker = tmp_path / "clicked.txt"
    monkeypatch.setenv("DISPLAY", display)
    try:
        app, marker = _start_windows(tmp_path, display)
        target = _xdo(display, "search", "--name", "cu-target-probe").splitlines()[0].strip()
        victim = _xdo(display, "search", "--name", "cu-victim-probe").splitlines()[0].strip()
        assert target and victim
        geometry = _window_geometry(display, target)
        assert geometry.get("WIDTH") == 300 and geometry.get("HEIGHT") == 200
        runner = HostSessionRunner(
            attach_to_session=True,
            allow_live_desktop=True,
            target="cu-target-probe",
            display=display,
            timeout=20.0,
        )
        try:
            assert runner.target_window()[0] == target
            point = _measure_clickable_point(display, target, geometry)
            payload = {"x": point[0] - geometry["X"], "y": point[1] - geometry["Y"], "click": True}
            assert 0 <= payload["x"] < geometry["WIDTH"]
            assert 0 <= payload["y"] < geometry["HEIGHT"]
            if marker.exists():
                marker.unlink()
            # even with the argv the ACT layer attaches, the runner builds its own command
            core = A.to_core_action({"kind": "mouse", "channel": "api", "payload": dict(payload)})
            assert core.argv, "the ACT layer attaches an argv for a pointer action"
            result = runner.act(core)
            assert result.ok, result.detail
            time.sleep(0.6)
            assert marker.exists(), "the click did not reach the target window"
            location = _xdo(display, "getmouselocation", "--shell")
            assert f"X={point[0]}" in location and f"Y={point[1]}" in location, location

            # a point outside the window is refused instead of being driven
            outside = runner.act(
                Action(kind="mouse", payload={"x": geometry["WIDTH"] + 40, "y": 10, "click": True})
            )
            assert outside.ok is False
            assert "outside the target window" in outside.detail

            # a key action still works, through windowfocus on the target
            assert runner.act(Action(kind="key", payload={"key": "a"})).ok is True
            # a key payload that is not a keystroke name is refused
            assert runner.act(Action(kind="key", payload={"key": "a; rm -rf /"})).ok is False
        finally:
            runner.close()
    finally:
        if app is not None:
            _stop_app(app)
        _stop_own_xvfb(server, display)


@needs_display
def test_a_live_capture_cannot_be_aimed_at_another_window(monkeypatch, tmp_path) -> None:
    """F7: a caller-supplied window id never widens a live-mode capture."""
    display = _free_display_number()
    server = _start_own_xvfb(display)
    app: subprocess.Popen | None = None
    monkeypatch.setenv("DISPLAY", display)
    runner = HostSessionRunner(
        attach_to_session=True,
        allow_live_desktop=True,
        target="cu-target-probe",
        display=display,
        timeout=20.0,
    )
    try:
        app, _marker = _start_windows(tmp_path, display)
        target = _xdo(display, "search", "--name", "cu-target-probe").splitlines()[0].strip()
        victim = _xdo(display, "search", "--name", "cu-victim-probe").splitlines()[0].strip()
        shot_path = tmp_path / "victim.png"
        capture, why = runner.screenshot(shot_path, window_id=victim, label="victim")
        assert capture is None
        assert "may only be the target window" in why
        assert not shot_path.exists(), "the refused capture must not leave a file"
        # the target itself is still captured, with the size the PNG really declares
        capture, why = runner.screenshot(tmp_path / "target.png", label="target")
        assert capture is not None, why
        assert capture.scope == f"window {target}"
        assert capture.width > 0 and capture.height > 0, capture.as_dict()
        declared = png_size((tmp_path / "target.png").read_bytes())
        assert (capture.width, capture.height) == declared
    finally:
        runner.close()
        if app is not None:
            _stop_app(app)
        _stop_own_xvfb(server, display)


@needs_display
def test_the_host_capture_reports_the_width_and_height_the_png_declares(tmp_path) -> None:
    """Report 16 §9.2: the import path used to answer `0x0` for a real image."""
    runner = HostSessionRunner(timeout=60.0)
    try:
        ok, detail = runner.start()
        assert ok, detail
        title = f"godsv-cu-size-{time.time_ns() % 100000}"
        launched = runner.act(
            Action(
                kind="cli",
                argv=[
                    "sh",
                    "-c",
                    f"(zenity --info --title={title} --text='size' >/dev/null 2>&1 &); "
                    "sleep 3; echo LAUNCHED",
                ],
            )
        )
        assert launched.ok, launched.detail
        capture, why = runner.screenshot(tmp_path / "host.png", label=title)
        assert capture is not None, why
        assert capture.width > 0 and capture.height > 0, capture.as_dict()
        assert (capture.width, capture.height) == png_size((tmp_path / "host.png").read_bytes())
    finally:
        runner.close()
