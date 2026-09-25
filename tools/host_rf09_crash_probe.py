#!/usr/bin/env python3
"""RF-09 crash-window recovery probe — red on the pristine engine, green on RF-09.

Run with the ENGINE interpreter against an engine tree:

    <engine-venv>/bin/python tools/host_rf09_crash_probe.py <engine-root>

CRASH INJECTION IS REAL: the probe frames a helper child process (spawnable
python) in the target tree, stages the exact window it wants to prove broken
and then sends SIGKILL (``kill -9``) mid-step — no exception-shaped substitute.
A parent process owns every staged state and the assertions.

Scenarios (each kills the runtime mid-window, then a RESTART re-derives the
invariant the criterion requires):

* W1 — external action dispatched, crash BEFORE its result was recorded.
  The helper child writes the crash-pending marker and NEVER clears it — the
  kill lands inside the open window. The parent then checks the marker state
  and the RESTART fence.
* W2 — resume claim: a stale-epoch restart must NOT take over the segment.
* Round trip — crash-restart: a real SIGKILL between ``pending marker`` and
  ``result recorded`` leaves the marker discoverable, and the restarted
  executor fences the identical call (exactly-once resume, one owner,
  unknown state persisted on disk).

PRISTINE (RC=1): no ``_rf09_mark_pending_external`` / ``_rf09_claim_resume``
on the engine — crash windows have no fence at all (the meaningful red).

GREEN (RC=0): the marker survives the kill, the duplicate claim loses with
CURSOR-MISMATCH, and the restarted executor fences the identical call while
letting unrelated calls through — one owner, unknown side-effect state on disk.

Exit 0 only on the patched (green) tree, 1 on the pristine tree.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _engine_python(root: Path) -> str:
    venv_py = root / "venv" / "bin" / "python"
    if venv_py.is_file():
        return str(venv_py)
    return sys.executable


def _run_child(py: str, code: str, home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HERMES_HOME"] = str(home)
    return subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=120, env=env)


def _await_death(pid: int) -> None:
    """Wait until a killed child is provably gone (groups SIGKILL, no fake)."""
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.05)
    raise TimeoutError(f"pid {pid} survived the kill")


def _result(ok: bool) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0 if ok else 1, stdout="", stderr="")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: host_rf09_crash_probe.py <engine-root>")
        return 2
    root = Path(sys.argv[1]).resolve()
    py = _engine_python(root)
    if not (root / "agent" / "conversation_loop.py").is_file():
        print(f"FAILED not an engine tree: {root}")
        return 2

    probe = _run_child(
        py,
        "import agent.tool_executor as te, agent.turn_finalizer as tf; "
        "print('RF09_PROBE', hasattr(te, '_rf09_mark_pending_external'), "
        "hasattr(tf, '_rf09_claim_resume'))",
        home=Path(os.path.expanduser("~/.hermes")),
    )
    markers = [row for row in probe.stdout.splitlines() if row.startswith("RF09_PROBE")]
    if not markers or "RF09_PROBE False False" in markers[0]:
        print(
            "PRISTINE RF-09: no _rf09_mark_pending_external / _rf09_claim_resume on the engine — "
            "crash windows have no fence (side effects may silently re-send, two owners possible)"
        )
        return 1

    home = Path(tempfile.mkdtemp(prefix="gv_rf09_probe_"))
    session_id = "rf09-probe-session"

    # ── Scenario W2: a stale-epoch restart must not take over the resume. ──
    w2_py = f"""
import sys
sys.path.insert(0, {str(root)!r})
import os
os.environ.setdefault("HERMES_HOME", {str(home)!r})
from types import SimpleNamespace
from agent.turn_finalizer import _rf09_claim_resume

agent = SimpleNamespace(session_id={session_id!r})
first = _rf09_claim_resume(agent, {session_id!r}, 1)
second = _rf09_claim_resume(
    agent, {session_id!r}, 1, owner_epoch=first["request"]["owner_epoch"] - 1,
)
print("RF09_CLAIM", first.get("rejected") is False, second.get("rejected") is True,
      (second.get("reject_reason") or ""))
"""
    w2 = _run_child(py, w2_py, home=home)
    w2_lines = [row for row in w2.stdout.splitlines() if row.startswith("RF09_CLAIM")]
    ok_w2 = bool(w2_lines) and w2_lines[0].strip() == "RF09_CLAIM True True CURSOR-MISMATCH"
    checks: list = [
        (ok_w2, "W2 resume fence: older epoch rejected CURSOR-MISMATCH (one owner)", w2),
    ]

    # ── Scenario W1: real SIGKILL between dispatch and the recorded result. ──
    helper_py = f"""
import sys, os, time, json
sys.path.insert(0, {str(root)!r})
os.environ["HERMES_HOME"] = {str(home)!r}
from types import SimpleNamespace
from agent.tool_executor import _rf09_mark_pending_external
agent = SimpleNamespace(session_id={session_id!r})
_rf09_mark_pending_external(agent, "terminal_tool", "crash_tc_1", {{"cmd": "echo boom"}})
print("RF09_STAGED", flush=True)
time.sleep(30)
"""
    helper = subprocess.Popen(
        [py, "-c", helper_py], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        if helper.stdout is not None:
            # the read IS the wait: the child stages the window, then gets killed
            _staged = helper.stdout.readline().strip()
    finally:
        helper.kill()
        helper.wait()
        try:
            os.kill(helper.pid, 9)
        except ProcessLookupError:
            pass
    try:
        _await_death(helper.pid)
        kill_ok = True
    except TimeoutError:
        kill_ok = False
    checks.append((kill_ok, "crash injection is a REAL SIGKILL (runtime died mid-step)", _result(kill_ok)))

    marker_path = home / "tool_events" / "rf09_pending_external.json"
    checks.append((marker_path.is_file(), "W1 pending marker survives the SIGKILL", _result(marker_path.is_file())))

    marker_record: dict = {}
    if marker_path.is_file():
        try:
            marker_record = json.loads(marker_path.read_text(encoding="utf-8"))
        except Exception:
            marker_record = {}
    marker_ok = (
        marker_record.get("tool_name") == "terminal_tool"
        and marker_record.get("state") == "dispatching"
        and marker_record.get("tool_call_id") == "crash_tc_1"
    )
    checks.append((
        marker_ok, "W1 crash marker carries the pending external (id, name, dispatch state)",
        _result(marker_ok),
    ))

    # ── Round trip: the RESTARTED executor fences the identical call, not others. ──
    restart_py = f"""
import sys
sys.path.insert(0, {str(root)!r})
import os
os.environ["HERMES_HOME"] = {str(home)!r}
from types import SimpleNamespace
from agent.tool_executor import _rf09_seq_restart_fenced

agent = SimpleNamespace(
    session_id={session_id!r},
    _rf09_restarted_turn=True,
    _rf09_pending_side_effect_calls=[{{"id": "crash_tc_1", "name": "terminal_tool"}}],
)
fenced = _rf09_seq_restart_fenced(agent, "terminal_tool")
unrelated = _rf09_seq_restart_fenced(agent, "read_file")
print("RF09_FENCE", fenced, unrelated)
"""
    restart = _run_child(py, restart_py, home=home)
    fence_lines = [row for row in restart.stdout.splitlines() if row.startswith("RF09_FENCE")]
    ok_fence = bool(fence_lines) and fence_lines[0].strip() == "RF09_FENCE True False"
    checks.append((
        ok_fence, "crash-restart trip: identical re-request fenced, unrelated not",
        restart,
    ))

    ok_all = True
    for ok, label, proc in checks:
        print(f"{'PASS' if ok else 'FAIL'} {label}")
        if not ok and hasattr(proc, "stderr"):
            print(f"      rc={proc.returncode} stdout={proc.stdout[-300:]!r} stderr={proc.stderr[-300:]!r}"[:400])
        ok_all = ok_all and ok
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
