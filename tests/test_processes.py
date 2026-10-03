"""The supervised process path — Paket 3 (audit F04).

The assignment's own example is the dev server: "Ein Devserver darf die Agentenrunde nicht
bis zum Timeout blockieren; der Agent muss ihn starten, auf Bereitschaft prüfen, nutzen und
seine eigene Instanz beenden können."

And two lines it draws explicitly:

* output is bounded **during capture** (``communicate`` + later truncation is not a RAM
  limit), so a loud child cannot grow memory without bound;
* process state is **not** task success, and the result says so.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from skaldir.processes import (
    ProcessManager,
    ProcessState,
    StreamBuffer,
)


@pytest.fixture()
def manager(tmp_path):
    return ProcessManager(runtime=tmp_path / "runtime", stream_limit_bytes=4096)


# --------------------------------------------------------------------------- #
# the dev-server shape
# --------------------------------------------------------------------------- #

def test_spawning_returns_immediately_with_a_stable_process_id(manager):
    """The agent round must not block until the child finishes."""
    started = time.monotonic()
    result = manager.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
    elapsed = time.monotonic() - started
    assert elapsed < 5.0, f"spawn blocked for {elapsed:.1f}s — the round would stall"
    assert result.process_id.startswith("proc-")
    assert result.pid > 0
    assert result.state == ProcessState.RUNNING
    manager.cancel(result.process_id, reason="test")


def test_early_output_is_readable_before_the_process_ends(manager):
    """Start, stream early output, then use it — without waiting for exit."""
    result = manager.spawn(
        [sys.executable, "-c",
         "import time\nprint('READY', flush=True)\ntime.sleep(30)"],
    )
    seen = ""
    for _ in range(100):
        seen += manager.poll(result.process_id)["stdout"]
        if "READY" in seen:
            break
        time.sleep(0.05)
    assert "READY" in seen, "the readiness line never arrived while the process ran"
    manager.cancel(result.process_id, reason="test")


def test_the_agent_can_stop_its_own_instance(manager):
    result = manager.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
    outcome = manager.cancel(result.process_id, reason="done with it")
    assert outcome["state"] == ProcessState.KILLED
    assert outcome["group_signalled"] is True, (
        "the whole process group must be killed, not just the shell pid"
    )
    poll = manager.poll(result.process_id)
    assert poll["running"] is False


# --------------------------------------------------------------------------- #
# the two lines the assignment draws
# --------------------------------------------------------------------------- #

def test_output_is_bounded_during_capture_not_afterwards(manager, tmp_path):
    """A loud child must not grow memory without bound — the tail spills to disk."""
    result = manager.spawn(
        [sys.executable, "-c", "print('x' * 400000)"],
    )
    manager.wait(result.process_id, timeout_s=30)
    meta = manager.poll(result.process_id)["stdout_meta"]
    assert meta["bytes_total"] > meta["bytes_in_window"], (
        "the whole output was held in memory — that is a truncation, not a limit"
    )
    assert meta["truncated"] is True
    assert meta["bytes_in_window"] <= 4096 + 4096, "the memory window must stay bounded"
    assert meta["spill"], "the overflow must be spilled somewhere"


def test_the_in_memory_window_stays_bounded_no_matter_the_output():
    buf = StreamBuffer(limit_bytes=1024)
    for _ in range(200):
        buf.feed(b"y" * 4096)
    snap = buf.snapshot()
    assert snap["bytes_in_window"] <= 1024
    assert snap["bytes_total"] == 200 * 4096, "the total still counts everything"
    assert snap["truncated"] is True


def test_process_state_is_not_task_success(manager):
    """`running`/`exited`/`killed` describe the process, not the task."""
    result = manager.spawn([sys.executable, "-c", "import sys; sys.exit(3)"])
    manager.wait(result.process_id, timeout_s=30)
    poll = manager.poll(result.process_id)
    assert poll["state"] == ProcessState.EXITED
    assert poll["exit_code"] == 3
    assert poll["task_success"] is None, (
        "the process manager must not decide whether the task succeeded"
    )
    assert "not task success" in poll["note"]


def test_a_failing_command_exit_is_still_visible_as_a_process_exit(manager):
    """"Exit 7 must not read as bestanden" — the exit is reported as data."""
    result = manager.spawn([sys.executable, "-c", "import sys; sys.exit(7)"])
    manager.wait(result.process_id, timeout_s=30)
    assert manager.poll(result.process_id)["exit_code"] == 7


# --------------------------------------------------------------------------- #
# control
# --------------------------------------------------------------------------- #

def test_a_timeout_kills_the_group_and_says_so(manager):
    result = manager.spawn([sys.executable, "-c", "import time; time.sleep(30)"],
                           timeout_s=1)
    for _ in range(120):
        if manager.poll(result.process_id)["state"] != ProcessState.RUNNING:
            break
        time.sleep(0.05)
    poll = manager.poll(result.process_id)
    assert poll["state"] in (ProcessState.TIMEOUT, ProcessState.KILLED)
    assert poll["running"] is False


def test_later_stdin_reaches_an_interactive_program(manager):
    """A program that needs input after it started must be able to get it."""
    result = manager.spawn(
        [sys.executable, "-c",
         "import sys\nline = sys.stdin.readline()\nprint('GOT:' + line.strip(), flush=True)"],
        stdin_data="hello\n",
    )
    out = ""
    for _ in range(100):
        out += manager.poll(result.process_id)["stdout"]
        if "GOT:hello" in out:
            break
        time.sleep(0.05)
    assert "GOT:hello" in out, f"the input never reached the child: {out!r}"


def test_write_stdin_works_for_a_program_that_waits(manager):
    result = manager.spawn(
        [sys.executable, "-c",
         "import sys\nline = sys.stdin.readline()\nprint('GOT:' + line.strip(), flush=True)"],
    )
    proc = manager.get(result.process_id)
    assert proc is not None
    time.sleep(0.2)
    assert proc.write_stdin("later\n") is True
    out = ""
    for _ in range(100):
        out += manager.poll(result.process_id)["stdout"]
        if "GOT:later" in out:
            break
        time.sleep(0.05)
    assert "GOT:later" in out


def test_an_unknown_process_id_is_reported_not_a_crash(manager):
    assert manager.poll("nope")["state"] == ProcessState.FAILED
    assert "unknown process id" in manager.poll("nope")["error"]
    assert manager.cancel("nope")["state"] == ProcessState.FAILED


def test_cleanup_stops_only_its_own_processes(manager, tmp_path):
    """A cancel/timeout cleans up its own, and leaves other people's alone."""
    mine = manager.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
    other = ProcessManager(runtime=tmp_path / "other")
    theirs = other.spawn([sys.executable, "-c", "import time; time.sleep(30)"])

    stopped = manager.cleanup()
    assert stopped, "the manager stopped nothing"
    assert manager.poll(mine.process_id)["running"] is False
    assert other.poll(theirs.process_id)["running"] is True, (
        "cleanup stopped a process it does not own"
    )
    other.cleanup()


def test_a_command_that_cannot_start_is_a_named_failure(manager):
    with pytest.raises((OSError, ValueError)):
        manager.spawn(["/definitely/not/here"])


# --------------------------------------------------------------------------- #
# no orphans (VAL-HARNESS-004): the whole group dies, verified against the OS
# --------------------------------------------------------------------------- #

def _record_pgid(manager, marker_name: str, tmp_path, args: str) -> str:
    result = manager.spawn(
        ["/bin/sh", "-c", f"echo $$ > {marker_name}; {args}"],
        cwd=str(tmp_path),
    )
    marker = tmp_path / marker_name
    for _ in range(100):
        if marker.exists() and marker.read_text().strip():
            break
        time.sleep(0.05)
    pgid = marker.read_text().strip()
    assert pgid, "the child never recorded its process-group id"
    return result.process_id, int(pgid)


def _group_is_empty(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return False  # something is still alive in the group
    except ProcessLookupError:
        return True


def _group_members(pgid: int) -> str:
    return (
        subprocess.run(  # noqa: S603 — read-only ps for evidence
            ["/bin/sh", "-c", f"ps -eo pid=,pgid=,cmd= | awk -v g={pgid} '$2 == g'"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    )


def test_cancel_leaves_no_orphaned_children_of_the_spawned_worker(manager, tmp_path):
    """The classic orphan shape: a shell that started a server child of its own.

    After cancel the OS view must be empty for the spawned group — verified with ps,
    scoped to the group id this test spawned, never by scanning for a command name
    (a foreign process with the same name must not flip the verdict).
    """
    process_id, pgid = _record_pgid(
        manager, "cancel-pgid-marker", tmp_path, "sleep 30 & sleep 31; wait"
    )
    outcome = manager.cancel(process_id, reason="test")
    assert outcome["state"] == ProcessState.KILLED
    assert outcome["group_signalled"] is True

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if _group_is_empty(pgid):
            break
        time.sleep(0.1)
    remaining = _group_members(pgid)
    assert _group_is_empty(pgid), (
        f"orphaned children survived the cancel (ps shows, pgid {pgid}):\n{remaining}"
    )


def test_a_timeout_leaves_no_orphaned_children_of_the_spawned_worker(manager, tmp_path):
    """Same guarantee for the supervisor's own timeout path."""
    result = manager.spawn(
        ["/bin/sh", "-c", "echo $$ > timeout2-pgid-marker; sleep 30 & sleep 31; wait"],
        cwd=str(tmp_path),
        timeout_s=0.5,
    )
    marker = tmp_path / "timeout2-pgid-marker"
    for _ in range(100):
        if marker.exists() and marker.read_text().strip():
            break
        time.sleep(0.05)
    pgid = int(marker.read_text().strip())
    for _ in range(120):
        if manager.poll(result.process_id)["state"] != ProcessState.RUNNING:
            break
        time.sleep(0.05)
    poll = manager.poll(result.process_id)
    assert poll["state"] in (ProcessState.TIMEOUT, ProcessState.KILLED)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if _group_is_empty(pgid):
            break
        time.sleep(0.1)
    remaining = _group_members(pgid)
    assert _group_is_empty(pgid), (
        f"orphaned children survived the timeout (ps shows, pgid {pgid}):\n{remaining}"
    )


# --------------------------------------------------------------------------- #
# the PTY option (audit F04): a program that refuses a pipe gets a real tty
# --------------------------------------------------------------------------- #

def test_pty_true_gives_the_child_a_real_tty_on_stdin(manager):
    """A forked child cannot be handed a tty by dup2 in the parent — measured: that no-op
    left stdin a pipe and programs checking isatty (cropping, progress bars) noticed. With
    pty=True the slave end must be the child's fd 0 from exec onwards."""
    result = manager.spawn(
        [sys.executable, "-c",
         "import sys\n"
         "print('TTY:', sys.stdin.isatty(), flush=True)\n"
         "line = sys.stdin.readline()\n"
         "print('GOT:' + line.strip(), flush=True)\n"
         "input()\n"
         "print('GOT-LATER', flush=True)\n"],
        pty=True,
    )
    out = ""
    for _ in range(100):
        out += manager.poll(result.process_id)["stdout"]
        if "TTY:" in out:
            break
        time.sleep(0.05)
    assert "TTY: True" in out, f"stdin was not a terminal for the child: {out!r}"
    managed = manager.get(result.process_id)
    assert managed is not None
    time.sleep(0.2)
    assert managed.write_stdin("first-input\n") is True, "stdin must reach the tty"
    out_more = ""
    for _ in range(100):
        out_more += manager.poll(result.process_id)["stdout"]
        if "GOT:" in out_more:
            break
        time.sleep(0.05)
    assert "GOT:first-input" in out_more, f"no input ever arrived over the tty: {out_more!r}"
    assert managed is not None
    time.sleep(0.2)
    assert managed.write_stdin("later-input\n") is True, "later stdin must reach the tty"
    out2 = ""
    for _ in range(100):
        out2 += manager.poll(result.process_id)["stdout"]
        if "GOT-LATER" in out2:
            break
        time.sleep(0.05)
    assert "GOT-LATER" in out2, f"the tty-fed stdin did not round-trip: {out2!r}"
    manager.cancel(result.process_id, reason="test")


def test_without_pty_stdin_is_honestly_a_pipe(manager):
    """The default keeps pipe semantics — pty=False must not claim a terminal."""
    result = manager.spawn(
        [sys.executable, "-c",
         "import sys, time\nprint('TTY:', sys.stdin.isatty(), flush=True)\n"
         "input()\nprint('GOT', flush=True)\n"],
    )
    out = ""
    for _ in range(100):
        out += manager.poll(result.process_id)["stdout"]
        if "TTY:" in out:
            break
        time.sleep(0.05)
    assert "TTY: False" in out, f"a pipe is not a tty — the child must see that: {out!r}"
    manager.cancel(result.process_id, reason="test")
