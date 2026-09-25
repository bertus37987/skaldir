"""The gui step runner against a REAL isolated display: a declared flow, verified end to end.

Evidence class: real execution. This file starts the isolated container display
(``localhost/godsv-cu-x11``: Xvfb on ``:90`` + xdotool, inside rootless podman), runs a
declared GUI step **through** ``godsv.step_runners.run_gui_step`` and reads the app's OWN
state afterwards (the xterm writes every input byte it receives to a file). It is skipped
when podman or the image is missing, so a CI without the image simply does not run it — it
never fakes a pass. The in-process/synthetic cases live in ``tests/test_step_runners.py``.

The gui runner is not re-tested for what ``tests/test_act_service_container.py`` already
proves (that the actions really move the display); what is proven here is that the *step*
kind resolves a real bridge, delegates a declared flow, maps the ACT record honestly
(``live``/``isolation``/per-action verdicts) and carries the end-state check.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest

from skaldir import act_service as A
from skaldir import step_runners as R
from skaldir.computer_use import ContainerRunner, _run_cmd

GUI_IMAGE = ContainerRunner.DEFAULT_IMAGE
PODMAN = shutil.which("podman")

TITLE = f"godsv-steprunners-{os.getpid()}"
INBOX = f"/tmp/godsv-steprunners-inbox-{os.getpid()}.txt"


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
    runner = ContainerRunner(gui=True, name=f"godsv-steprunners-{int(time.time()) % 1000000}")
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
    """A real app on the isolated display that reports every input byte it receives."""
    _sh(runner, f"pkill -f '{title}' || true; rm -f {INBOX}")
    script = (
        f"rm -f {INBOX}; "
        f"xterm -T {title} -e sh -c 'stty -icanon -echo min 1 time 0; "
        f"printf \"\\033[?1000h\"; dd bs=1 count=400 of={INBOX} 2>/dev/null' & "
        f"sleep 3; xdotool search --name {title} getwindowname %@"
    )
    code, output = _sh(runner, script)
    assert code == 0 and title in output, f"the app did not start: {output[:200]}"
    return title


def _declared_flow(title: str, *, end_state: str | None = None) -> list[dict]:
    """The declared flow: window list → focus + type → click → scroll → read the app state."""
    focus_type = (
        f"wid=$(xdotool search --name {title} | head -1); "
        "xdotool windowfocus --sync $wid; sleep 0.5; "
        "xdotool type --delay 40 -- STEPRUNNER-MARKER-7; sleep 2; "
        f"cat {INBOX}"
    )
    read_effect = (
        f"bytes=$(wc -c < {INBOX}); echo REPORTS=$bytes; "
        "test $bytes -ge 30 && echo MOUSE_EVENTS_OK"
    )
    return [
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
            "verify": {"expect_stdout_contains": "STEPRUNNER-MARKER-7"},
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
    ]


def _gui_step(target: str = "inline", *, step_id: str = "drive_display") -> SimpleNamespace:
    return SimpleNamespace(
        id=step_id, kind="gui", target=target, timeout_s=120.0, note="", outputs=[]
    )


def _subtool_spec(title: str) -> dict:
    return {
        "name": "probe_real_step_runner",
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


def test_a_declared_inline_gui_step_verifies_on_the_real_display(display, tmp_path):
    title = _launch(display)
    outcome = R.run_gui_step(
        _gui_step(),
        {
            "actions": _declared_flow(title),
            "end_state": {
                "channel": "window_title",
                "description": "the app window is on the display after the flow",
                "expect_observe_contains": title,
            },
            "goal": "drive the isolated display: window, focus, type, click, scroll",
            "state": tmp_path,
        },
        runtime=tmp_path,
        act_runner=display,
    )
    assert outcome.ok is True, outcome.detail
    assert outcome.status == "ok"
    assert outcome.exit_code == R.EXIT_OK
    payload = outcome.outputs
    assert payload["status"] == "verified" and payload["verified"] is True
    assert payload["live"] is True and payload["scope"] == "real"
    assert payload["isolation"] == "container"
    assert payload["executed"] == 5
    assert [row["status"] for row in payload["actions"]] == ["verified"] * 5
    assert payload["end_state"]["ok"] is True
    assert title in payload["observation"]
    assert payload["failure_class"] == ""
    # the run reached the same ACT audit trail every other declared run uses
    record = tmp_path / "cu_runs.jsonl"
    assert record.is_file(), "the ACT run record was written where the panel view reads it"
    last = json.loads(record.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["status"] == "verified" and last["source"] == "act"
    # the same payload in the shape the COMPOSE layer consumes
    ok, outputs, _raw, detail, missing = R.as_step_tuple(outcome)
    assert ok is True and missing == "" and outputs == payload
    assert "action(s) verified" in detail


def test_a_step_whose_end_state_is_absent_is_not_verified_on_the_real_display(display, tmp_path):
    title = _launch(display, title=f"{TITLE}-counter")
    outcome = R.run_gui_step(
        _gui_step("inline", step_id="absent_end_state"),
        {
            "actions": _declared_flow(title),
            "end_state": {
                "channel": "window_title",
                "description": "an end state that is not on the display",
                "expect_observe_contains": "NOT-ON-THIS-DISPLAY",
            },
            "state": tmp_path,
        },
        runtime=tmp_path,
        act_runner=display,
    )
    assert outcome.ok is False and outcome.blocked is False
    assert outcome.failure_class == R.FAILURE_VERIFICATION
    assert outcome.exit_code == R.EXIT_VERIFICATION_FAILED
    assert outcome.outputs["end_state"]["missing"] == ["NOT-ON-THIS-DISPLAY"]
    assert [row["status"] for row in outcome.outputs["actions"]] == ["verified"] * 5


def test_a_stored_subtool_target_runs_through_the_same_step_runner(display, tmp_path):
    title = _launch(display, title=f"{TITLE}-subtool")
    A.save_subtool(_subtool_spec(title), state=tmp_path)
    outcome = R.run_gui_step(
        _gui_step("probe_real_step_runner", step_id="saved_flow"),
        {"state": tmp_path},
        runtime=tmp_path,
        act_runner=display,
    )
    assert outcome.ok is True, outcome.detail
    assert outcome.outputs["subtool"] == "probe_real_step_runner"
    assert outcome.outputs["executed"] == 1
    assert outcome.outputs["end_state"]["ok"] is True
    assert outcome.outputs["isolation"] == "container"
    assert outcome.outputs["live"] is True


def test_the_runner_resolves_the_bridge_itself_and_blocks_when_it_cannot(tmp_path, monkeypatch):
    """No injected runner, a host whose bridges are gone: exit 3, the gap named."""
    monkeypatch.setattr(
        A,
        "resolve_runner",
        lambda kind="auto", **kwargs: (
            None,
            {
                "requested": kind,
                "bridge": "",
                "resolved": False,
                "isolation": "",
                "real": False,
                "fixture": False,
                "scope": "real",
                "reason": "no real bridge is available on this host: the image is missing",
            },
        ),
    )
    outcome = R.run_gui_step(
        _gui_step(),
        {
            "actions": _declared_flow(TITLE),
            "end_state": {"expect_observe_contains": TITLE},
            "state": tmp_path,
        },
        runtime=tmp_path,
    )
    assert outcome.blocked is True
    assert outcome.missing_kind == "gui"
    assert outcome.exit_code == R.EXIT_BLOCKED
    assert outcome.outputs["missing_capability"]
    assert "no real bridge" in outcome.detail
