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

import sys
import time

import pytest

from skaldir.processes import (
    ManagedProcess,
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
