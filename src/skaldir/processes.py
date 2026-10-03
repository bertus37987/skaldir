"""Supervised long-running processes — Paket 3 (audit F04).

``tools_core/terminal.py`` is a synchronous command: it starts the child in its own process
group, kills the group on timeout and returns when the child is done. That is right for
fast commands and the assignment says to keep it.

What it cannot do is run a dev server. A server that must not block the agent round until
its timeout needs a **supervised** path: a stable process id, incremental output, a poll,
a timeout, a cancel, and cleanup of its own process group. The assignment also draws two
lines that are easy to cross:

* **Bounded output during capture.** ``communicate()`` plus a later truncation is not a RAM
  limit — the whole output is already in memory by then. Here each stream has a bounded
  in-memory window and the rest is **spilled to a file**, so a runaway process cannot grow
  the agent's memory without bound.
* **Process state is not task success.** ``running``/``exited``/``killed`` describe the
  process. Whether the *task* succeeded is a separate question, and this module reports the
  two separately instead of folding one into the other.

Existing infrastructure is reused rather than duplicated: the child goes into its own
process group and cancel kills the **group**, exactly as ``terminal._run`` and
``api_server`` already do.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_KILL_GRACE_S = 1.0    # SIGTERM ->
_REAP_GRACE_S = 3.0    # after a SIGKILL, wait for the child before calling it reaped

__all__ = [
    "ManagedProcess",
    "ProcessState",
    "ProcessManager",
    "SpawnResult",
    "StreamBuffer",
]


class ProcessState:
    RUNNING = "running"
    EXITED = "exited"
    KILLED = "killed"
    TIMEOUT = "timeout"
    FAILED = "failed"          # could not be started at all


class StreamBuffer:
    """A bounded in-memory window plus a spill file.

    This is the difference between a cap and a hope: once the window is full, the tail goes
    to disk and the memory does not grow again. ``total`` still counts everything, so a
    caller can tell "nothing more happened" from "a lot happened and you only see the head".
    """

    def __init__(self, limit_bytes: int = 64_000, spill_to: Path | None = None) -> None:
        self.limit = max(1024, int(limit_bytes))
        self.spill_path = spill_to
        self._chunks: list[bytes] = []
        self._size = 0
        self.total = 0
        self.truncated = False
        self._lock = threading.Lock()

    def feed(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            self.total += len(data)
            room = self.limit - self._size
            if room > 0:
                keep = data[:room]
                self._chunks.append(keep)
                self._size += len(keep)
                if len(keep) < len(data):
                    self.truncated = True
            else:
                self.truncated = True
            if self.truncated and self.spill_path is not None:
                self._spill(data)

    def _spill(self, data: bytes) -> None:
        try:
            if self.spill_path is not None:
                self.spill_path.parent.mkdir(parents=True, exist_ok=True)
                with self.spill_path.open("ab") as handle:
                    handle.write(data)
        except OSError:
            pass                       # a full disk must not kill the process manager

    def text(self, *, limit: int = 0) -> str:
        with self._lock:
            blob = b"".join(self._chunks)
        if limit and len(blob) > limit:
            blob = blob[:limit]
        return blob.decode("utf-8", "replace")

    def drain(self) -> str:
        """Everything new since the last drain — the incremental read."""
        with self._lock:
            blob = b"".join(self._chunks)
            self._chunks = []
            self._size = 0
        return blob.decode("utf-8", "replace")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "bytes_in_window": self._size,
                "bytes_total": self.total,
                "truncated": self.truncated,
                "spill": str(self.spill_path) if self.spill_path else "",
            }


@dataclass
class SpawnResult:
    process_id: str
    pid: int
    argv: list[str]
    cwd: str
    state: str
    started_at: float


@dataclass
class ManagedProcess:
    """One supervised child."""

    process_id: str
    argv: list[str]
    cwd: str
    proc: subprocess.Popen
    out: StreamBuffer
    err: StreamBuffer
    started_at: float
    timeout_s: float = 0.0
    deadline: float = 0.0
    state: str = ProcessState.RUNNING
    exit_code: int | None = None
    killed_signal: int | None = None
    finished_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _threads: list[threading.Thread] = field(default_factory=list)

    # -- reading -----------------------------------------------------------

    def poll(self) -> dict[str, Any]:
        """Where the process is, and what is new. Process state ≠ task success."""
        with self._lock:
            code = self.proc.poll()
            if code is not None and self.state == ProcessState.RUNNING:
                self.state = ProcessState.EXITED
                self.exit_code = code
                self.finished_at = time.time()
            state = self.state
            exit_code = self.exit_code
        return {
            "process_id": self.process_id,
            "pid": self.proc.pid,
            "state": state,
            "exit_code": exit_code,
            "running": state == ProcessState.RUNNING,
            "elapsed_s": round((self.finished_at or time.time()) - self.started_at, 3),
            "stdout": self.out.drain(),
            "stderr": self.err.drain(),
            "stdout_meta": self.out.snapshot(),
            "stderr_meta": self.err.snapshot(),
            # deliberately separate: a running process is not a successful task
            "task_success": None,
            "note": ("process state is not task success — the caller decides whether the "
                     "task is done"),
        }

    def read(self, *, stream: str = "stdout", limit: int = 8000) -> str:
        buf = self.out if stream == "stdout" else self.err
        return buf.text(limit=limit)

    # -- control -----------------------------------------------------------

    def write_stdin(self, data: str) -> bool:
        """Later stdin — for interactive programs that need it after they started."""
        if self.proc.stdin is None:
            return False
        try:
            self.proc.stdin.write(data.encode("utf-8", "replace"))
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            return False

    def cancel(self, *, reason: str = "") -> dict[str, Any]:
        """Stop it, clean up its own process group, and REAP the corpse.

        The group is killed, not just the pid: a shell that started children would
        otherwise leave them running after the agent thinks it stopped them. And the
        killed child is waited for afterwards — a process that is only signalled stays
        a ``<defunct>`` zombie in ``ps`` for as long as this parent lives, which is an
        orphan bookkeeping defect of its own (measured 2026-10-03: the ``sh`` group
        leader sat unreaped after every cancel).
        """
        with self._lock:
            if self.state != ProcessState.RUNNING:
                return {"process_id": self.process_id, "state": self.state,
                        "already_finished": True}
            self.state = ProcessState.KILLED
            self.finished_at = time.time()
        killed = _kill_group(self.proc.pid)
        reaped = True
        try:
            self.proc.wait(timeout=_REAP_GRACE_S)
        except subprocess.TimeoutExpired:
            reaped = False  # SIGKILL ignored is exotic; report it instead of lying silent
        self._release_stdin()
        return {
            "process_id": self.process_id,
            "state": ProcessState.KILLED,
            "reason": reason,
            "group_signalled": killed,
            "reaped": reaped,
            "pid": self.proc.pid,
        }

    def _release_stdin(self) -> None:
        """Close the parent's stdin side after the kill so a blocked reader sees EOF."""
        stream = self.proc.stdin
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass

    def wait(self, timeout_s: float = 30.0) -> dict[str, Any]:
        try:
            self.proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return {"process_id": self.process_id, "state": self.state, "waited_out": True}
        return self.poll()

    def to_dict(self) -> dict[str, Any]:
        return {
            "process_id": self.process_id,
            "pid": self.proc.pid,
            "argv": self.argv,
            "cwd": self.cwd,
            "state": self.state,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "timeout_s": self.timeout_s,
        }


def _kill_group(pid: int, sig: int = signal.SIGKILL) -> bool:
    """Signal the child's whole process group. Same approach as api_server/terminal."""
    try:
        os.killpg(os.getpgid(pid), sig)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, sig)
            return True
        except OSError:
            return False


class ProcessManager:
    """Spawns and supervises children. One instance per run keeps the registry its own."""

    def __init__(self, *, runtime: str | Path | None = None,
                 default_timeout_s: float = 3600.0,
                 stream_limit_bytes: int = 64_000,
                 on_event: Callable[[str, Mapping[str, Any]], None] | None = None) -> None:
        self.runtime = Path(runtime) if runtime else None
        self.default_timeout_s = float(default_timeout_s)
        self.stream_limit = int(stream_limit_bytes)
        self.on_event = on_event
        self._procs: dict[str, ManagedProcess] = {}
        self._lock = threading.Lock()

    def _emit(self, kind: str, payload: Mapping[str, Any]) -> None:
        if self.on_event is not None:
            try:
                self.on_event(kind, dict(payload))
            except Exception:  # noqa: BLE001 — an observer must not break a process
                pass

    # -- spawn -------------------------------------------------------------

    def spawn(
        self,
        argv: Iterable[str],
        *,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        stdin_data: str = "",
        timeout_s: float | None = None,
        pty: bool = False,
        process_id: str = "",
    ) -> SpawnResult:
        """Start a child in its own process group and return its stable id at once.

        The caller is *not* blocked until the child finishes — that is the whole point of
        this path. A dev server gets a ``process_id`` and the round continues.
        """
        argv = [str(a) for a in argv]
        if not argv:
            raise ValueError("spawn needs an argv")
        workdir = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
        workdir.mkdir(parents=True, exist_ok=True)

        pid = process_id or f"proc-{uuid.uuid4().hex[:10]}"
        out_spill = self._spill_path(pid, "stdout")
        err_spill = self._spill_path(pid, "stderr")
        out_buf = StreamBuffer(self.stream_limit, out_spill)
        err_buf = StreamBuffer(self.stream_limit, err_spill)

        timeout = self.default_timeout_s if timeout_s is None else float(timeout_s)
        # stdin is ALWAYS a pipe: a program that needs input *after* it started could not
        # be given any if the pipe had been closed at spawn time. With ``pty=True`` the
        # child's fd 0 is a real tty SLAVE from exec onwards — the master side becomes
        # this parent's stdin writer. (Measured 2026-10-03: doing ``os.dup2`` in the
        # parent *after* ``Popen`` changed only the parent's own fd table — the child
        # still read a pipe and ``sys.stdin.isatty()`` answered ``False``. The tty must
        # be handed over at exec time, not patched on afterwards.)
        stdin_arg: Any = subprocess.PIPE
        master_fd = -1
        if pty:
            try:
                import pty as _pty_mod

                master_fd, slave_fd = _pty_mod.openpty()
                stdin_arg = slave_fd  # the child's fd 0, dup'd by Popen at exec time
            except OSError as exc:
                self._emit("pty.unavailable", {"process_id": pid, "error": str(exc)})
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(workdir),
                env=dict(env) if env else None,
                stdin=stdin_arg,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,          # own process group -> own cleanup
                bufsize=0,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            if master_fd >= 0:
                os.close(master_fd)
            self._emit("spawn.failed", {"process_id": pid, "error": f"{type(exc).__name__}: {exc}"})
            raise

        if pty and isinstance(stdin_arg, int) and stdin_arg >= 0 and master_fd >= 0:
            # this parent stops holding the child's side; the writer side is a dup
            # of the master, exposed as ``proc.stdin`` so write_stdin() works unchanged
            try:
                os.close(stdin_arg)
                proc.stdin = os.fdopen(os.dup(master_fd), "wb", buffering=0)  # type: ignore[union-attr]
                os.close(master_fd)
                master_fd = -1
            except OSError:
                self._emit("pty.unavailable", {"process_id": pid})
        if master_fd >= 0:
            os.close(master_fd)

        managed = ManagedProcess(
            process_id=pid, argv=argv, cwd=str(workdir), proc=proc,
            out=out_buf, err=err_buf, started_at=time.time(),
            timeout_s=timeout, deadline=(time.time() + timeout if timeout > 0 else 0.0),
        )
        with self._lock:
            self._procs[pid] = managed

        if stdin_data and proc.stdin is not None:
            try:
                proc.stdin.write(stdin_data.encode("utf-8", "replace"))
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

        self._pump(managed)
        self._emit("spawn.ok", {"process_id": pid, "pid": proc.pid, "cwd": str(workdir)})
        return SpawnResult(
            process_id=pid, pid=proc.pid, argv=argv, cwd=str(workdir),
            state=managed.state, started_at=managed.started_at,
        )

    def _spill_path(self, pid: str, stream: str) -> Path | None:
        if self.runtime is None:
            return None
        return Path(self.runtime) / "processes" / pid / f"{stream}.spill"

    def _pump(self, managed: ManagedProcess) -> None:
        """Drain the pipes continuously so a loud child cannot fill a pipe and block."""
        def read_into(stream: Any, buf: StreamBuffer) -> None:
            try:
                while True:
                    chunk = stream.read(4096) if hasattr(stream, "read") else b""
                    if not chunk:
                        break
                    buf.feed(chunk)
            except (OSError, ValueError):
                pass
            finally:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass

        for stream, buf in ((managed.proc.stdout, managed.out), (managed.proc.stderr, managed.err)):
            if stream is None:
                continue
            t = threading.Thread(target=read_into, args=(stream, buf), daemon=True)
            t.start()
            managed._threads.append(t)

        def watchdog() -> None:
            deadline = managed.deadline
            while managed.state == ProcessState.RUNNING:
                if deadline and time.time() > deadline:
                    managed.cancel(reason="timeout")
                    with managed._lock:
                        managed.state = ProcessState.TIMEOUT
                    self._emit("process.timeout", {"process_id": managed.process_id})
                    return
                if managed.proc.poll() is not None:
                    with managed._lock:
                        if managed.state == ProcessState.RUNNING:
                            managed.state = ProcessState.EXITED
                            managed.exit_code = managed.proc.returncode
                            managed.finished_at = time.time()
                    self._emit("process.exited", {"process_id": managed.process_id,
                                                  "exit_code": managed.exit_code})
                    return
                time.sleep(0.05)

        if managed.deadline:
            t = threading.Thread(target=watchdog, daemon=True)
            t.start()
            managed._threads.append(t)

    # -- registry ----------------------------------------------------------

    def get(self, process_id: str) -> ManagedProcess | None:
        with self._lock:
            return self._procs.get(process_id)

    def poll(self, process_id: str) -> dict[str, Any]:
        proc = self.get(process_id)
        if proc is None:
            return {"process_id": process_id, "state": ProcessState.FAILED,
                    "error": "unknown process id"}
        return proc.poll()

    def cancel(self, process_id: str, *, reason: str = "") -> dict[str, Any]:
        proc = self.get(process_id)
        if proc is None:
            return {"process_id": process_id, "state": ProcessState.FAILED,
                    "error": "unknown process id"}
        return proc.cancel(reason=reason)

    def wait(self, process_id: str, timeout_s: float = 30.0) -> dict[str, Any]:
        """Wait for one child to finish, then report. Still not a task verdict."""
        proc = self.get(process_id)
        if proc is None:
            return {"process_id": process_id, "state": ProcessState.FAILED,
                    "error": "unknown process id"}
        return proc.wait(timeout_s=timeout_s)

    def write_stdin(self, process_id: str, data: str) -> bool:
        proc = self.get(process_id)
        return proc.write_stdin(data) if proc is not None else False

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [p.to_dict() for p in self._procs.values()]

    def cleanup(self, *, only_own: bool = True) -> list[dict[str, Any]]:
        """Stop what this manager started. Nothing else.

        ``only_own`` is the point: a cancel/timeout must clean up its own processes and
        leave other people's alone.
        """
        out: list[dict[str, Any]] = []
        with self._lock:
            own = list(self._procs.values())
        for proc in own:
            if proc.state == ProcessState.RUNNING:
                out.append(proc.cancel(reason="cleanup"))
        return out
