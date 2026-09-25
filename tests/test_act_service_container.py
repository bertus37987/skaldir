"""ACT against a REAL isolated display: the declared loop, verified end to end.

Evidence class: real execution. This file starts the isolated container display
(``localhost/godsv-cu-x11``: Xvfb + xdotool inside rootless podman), runs a declared action
list against a real app in it, and reads the app's OWN state afterwards. It is skipped when
podman or the image is missing, so CI without the image simply does not run it — it never
fakes a pass.

Two measured traps are pinned here, because both make a "verified" claim a lie:

* the pointer must be moved with ``xdotool mousemove --sync`` before the button event,
  otherwise the click lands where the pointer *was* (``act`` builds that argv);
* the action's argv already carries one ``sh -c`` layer, so a second ``sh -c '…'`` wrapper
  re-quotes the DECSET sequence and the app never enables mouse reporting.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from skaldir import act_service as S
from skaldir.computer_use import ContainerRunner, _run_cmd

GUI_IMAGE = ContainerRunner.DEFAULT_IMAGE
PODMAN = shutil.which("podman")

TITLE = f"godsv-act-test-{os.getpid()}"
INBOX = f"/tmp/godsv-act-test-{os.getpid()}.txt"
UNTOUCHED = f"/tmp/godsv-act-test-untouched-{os.getpid()}.txt"


def _image_present(image: str) -> bool:
    if not PODMAN:
        return False
    return subprocess.run(  # nosec B603 — fixed argv list, no shell  # noqa: S603
        [PODMAN, "image", "exists", image], capture_output=True
    ).returncode == 0


pytestmark = pytest.mark.skipif(
    not _image_present(GUI_IMAGE), reason="the godsv-cu-x11 image is not available"
)


@pytest.fixture(scope="module")
def display():
    """One isolated display for the whole module; torn down afterwards."""
    runner = ContainerRunner(gui=True, name=f"godsv-act-test-{int(time.time()) % 1000000}")
    ok, detail = runner.start()
    if not ok:
        pytest.skip(f"the isolated display did not start: {detail}")
    try:
        yield runner
    finally:
        runner.close()


def _sh(runner: ContainerRunner, script: str, *, timeout: float = 120.0) -> tuple[int, str]:
    return _run_cmd(
        ["podman", "exec", "-e", f"DISPLAY={runner.display}", runner.name, "sh", "-c", script],
        timeout=timeout,
    )


def _launch(runner: ContainerRunner, title: str = TITLE) -> str:
    """Start a real app on the isolated display that reports the input it receives.

    The app enables X mouse reporting (DECSET 1000) and writes every input byte it gets to a
    file: that file is the app's own state, not our claim about it.
    """
    _sh(runner, f"pkill -f '{title}' || true; rm -f {INBOX} {UNTOUCHED}")
    script = (
        f"rm -f {INBOX}; "
        f"xterm -T {title} -e sh -c 'stty -icanon -echo min 1 time 0; "
        f'printf "\\033[?1000h"; dd bs=1 count=400 of={INBOX} 2>/dev/null\' & '
        f"sleep 3; xdotool search --name {title} getwindowname %@"
    )
    code, output = _sh(runner, script)
    assert code == 0 and title in output, f"the app did not start: {output[:200]}"
    return title


#: The declared loop: window list → focus + type → click → scroll → read the app's own state.
def _loop(title: str, *, end_state: str | None = None) -> dict:
    focus_type = (
        f"wid=$(xdotool search --name {title} | head -1); "
        "xdotool windowfocus --sync $wid; sleep 0.5; "
        "xdotool type --delay 40 -- ACT-MARKER-4711; sleep 2; "
        f"cat {INBOX}"
    )
    read_effect = (
        f"bytes=$(wc -c < {INBOX}); echo REPORTS=$bytes; "
        "test $bytes -ge 39 && echo MOUSE_EVENTS_OK"
    )
    return {
        "goal": "drive the isolated display: window, focus, type, click, scroll",
        "actions": [
            {
                "kind": "window",
                "channel": "dom",
                "payload": {"op": "list", "title": title},
                "description": "read the display's window list",
                "verify": {"expect_stdout_contains": title},
            },
            {
                "kind": "cli",
                "channel": "api",
                "argv": ["sh", "-c", focus_type],
                "description": "focus the app window, type a marker, read the app's own state",
                "verify": {"expect_stdout_contains": "ACT-MARKER-4711"},
            },
            {
                "kind": "mouse",
                "channel": "vision",
                "payload": {"x": 242, "y": 158, "click": True, "button": 1},
                "description": "click into the app window (act builds the --sync argv)",
                "verify": {"expect_observe_contains": title},
            },
            {
                "kind": "scroll",
                "channel": "vision",
                "payload": {"direction": "down", "amount": 2},
                "description": "wheel down twice (X buttons 4/5)",
                "verify": {"expect_observe_contains": title},
            },
            {
                "kind": "cli",
                "channel": "api",
                "argv": ["sh", "-c", read_effect],
                "description": "read the app's own report of the input it received",
                "verify": {"expect_stdout_contains": "MOUSE_EVENTS_OK"},
            },
        ],
        "end_state": {
            "channel": "window_title",
            "description": "the target app window is on the display",
            "expect_observe_contains": end_state or title,
        },
    }


def test_the_whole_declared_loop_verifies_against_the_real_display(display, tmp_path):
    title = _launch(display)
    request = _loop(title)
    record = S.run_action_list(
        request["actions"],
        runner=display,
        end_state=request["end_state"],
        goal=request["goal"],
        runtime=tmp_path,
        timeout=120.0,
    )
    assert record["status"] == "verified", record.get("reason")
    assert record["verified"] is True
    assert record["exit_code"] == S.EXIT_OK
    assert record["live"] is True
    assert record["runner"]["isolation"] == "container"
    assert [row["status"] for row in record["actions"]] == ["verified"] * 5
    # the kinds really ran inside the isolated display, through the core's own vocabulary
    assert "scroll" not in record["policy"]["allowed_kinds"]
    assert record["end_state"]["ok"] is True
    written = Path(record["record"])
    row = json.loads(written.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert row["status"] == "verified" and row["kind"] == "container" and row["source"] == "act"


def test_an_absent_end_state_is_never_verified_on_the_real_display(display, tmp_path):
    title = _launch(display, title=f"{TITLE}-counter")
    request = _loop(title, end_state="NOT-ON-THIS-DISPLAY")
    record = S.run_action_list(
        request["actions"],
        runner=display,
        end_state=request["end_state"],
        goal=request["goal"],
        runtime=tmp_path,
        timeout=120.0,
    )
    assert record["status"] == "verification_failed"
    assert record["verified"] is False
    assert record["exit_code"] == S.EXIT_VERIFICATION_FAILED
    # every action verified — it is the end state that is not there, and that is a failure
    assert [row["status"] for row in record["actions"]] == ["verified"] * 5
    assert record["end_state"]["missing"] == ["NOT-ON-THIS-DISPLAY"]
    assert "NOT-ON-THIS-DISPLAY" not in record["end_state"]["observed"]


def test_a_saved_subtool_runs_against_the_real_display(display, tmp_path):
    title = _launch(display, title=f"{TITLE}-subtool")
    spec = {
        "name": "probe_real_display",
        "version": "1.0",
        "description": "read the display's window list and leave the window in place",
        "applicability": {
            "app": "isolated-display",
            "app_versions": [">=1.0,<2"],
            "locales": ["en", "*"],
        },
        "preconditions": [
            {"id": "display_ready", "text": "READY", "source": "host", "fact": "gui_image"},
        ],
        "anchors": [{"id": "app_window", "text": title, "source": "window_title"}],
        "actions": [
            {
                "kind": "window",
                "channel": "dom",
                "payload": {"op": "list", "title": title},
                "description": "read the window list of the isolated display",
                "verify": {"expect_stdout_contains": title},
            }
        ],
        "end_state": {
            "channel": "window_title",
            "description": "the app window is still on the display",
            "expect_observe_contains": title,
        },
        "verification_contract": "the display's own window list shows the app",
    }
    S.save_subtool(spec, state=tmp_path)
    record = S.run_subtool(
        "probe_real_display", state=tmp_path, runner=display, runtime=tmp_path, timeout=120.0
    )
    assert record["status"] == "verified", record.get("reason")
    assert record["verified"] is True
    assert record["origin"] == "saved"
    assert record["applicability"]["applicable"] is True
    assert record["precondition_check"]["observed_anchors"] == (title,)
    assert record["end_state"]["ok"] is True


def test_a_subtool_whose_anchor_is_absent_executes_nothing_on_the_real_display(display, tmp_path):
    title = _launch(display, title=f"{TITLE}-absent")
    marker = f"/tmp/godsv-act-must-not-exist-{os.getpid()}"
    _sh(display, f"rm -f {marker}")
    spec = {
        "name": "probe_absent_anchor",
        "version": "1.0",
        "description": "a stored flow whose anchor is not on the display right now",
        "applicability": {
            "app": "isolated-display",
            "app_versions": [">=1.0,<2"],
            "locales": ["en", "*"],
        },
        "preconditions": [
            {"id": "display_ready", "text": "READY", "source": "host", "fact": "gui_image"},
        ],
        "anchors": [
            {
                "id": "missing_window",
                "text": "NOT-ON-THIS-DISPLAY",
                "source": "window_title",
            }
        ],
        "actions": [
            {
                "kind": "cli",
                "channel": "api",
                "argv": ["sh", "-c", f"touch {marker}; echo TOUCHED"],
                "description": "an action that must never run",
                "verify": {"expect_stdout_contains": "TOUCHED"},
            }
        ],
        "end_state": {
            "channel": "window_title",
            "description": "the app window is on the display",
            "expect_observe_contains": title,
        },
        "verification_contract": "the display's own window list shows the app",
    }
    S.save_subtool(spec, state=tmp_path)
    record = S.run_subtool(
        "probe_absent_anchor", state=tmp_path, runner=display, runtime=tmp_path, timeout=120.0
    )
    assert record["status"] == "reobserve"
    assert record["exit_code"] == S.EXIT_VERIFICATION_FAILED
    assert record["verified"] is False
    assert record["missing_anchors"] and "NOT-ON-THIS-DISPLAY" in record["missing_anchors"][0]
    assert "actions" not in record
    # the proof that nothing was executed on the real display
    code, output = _sh(display, f"test -e {marker} && echo EXISTS || echo ABSENT")
    assert output.strip() == "ABSENT", output
