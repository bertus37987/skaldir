#!/usr/bin/env python3
"""Streaming-responsiveness harness: Enter → first byte, Enter → last byte, renders/s, CPU.

Four measurements, all of them raw numbers instead of adjectives:

``pty``    a **real** interactive shell on a real pseudo-terminal set to 80x24, fed one
           command that streams a long answer, with the terminal's render bound on and off.
           Reported per arm: p50/p95 of Enter→first byte, Enter→last byte, render updates
           per second *inside the streaming window*, and the child's own CPU time.
``pipe``   the same input through the same shell with stdout as a pipe — the control that
           shows the bound is a terminal concern (a pipe keeps every byte immediately).
``settle`` in-process: the coalescer's own counters against an injectable clock, so the
           bound and the "first chunk renders immediately" rule are checkable to the
           millisecond without a terminal.
``table``  render the last results file as the Markdown table the report uses.

Method, stated because a number without one is not evidence:

* **Enter → first byte** is measured from the moment the newline of the command is written
  to the pty master to the moment the first *answer* byte comes back out of it. The answer
  is a marker-only file, so "an answer byte" is unambiguous — the echoed command line and
  the prompt can never be mistaken for output.
* **Enter → last byte** ends when the last of the N expected markers has been read.
* **Renders** are counted on the **master side**: one non-empty ``os.read`` per render. This
  is independent of the in-process counter (which the payload also reports) — if the two
  disagree, the kernel merged or split a write and the payload says so.
* **CPU** is the child's ``utime + stime`` from ``/proc/<pid>/stat``, sampled by the reader
  at the two boundary moments. It includes the whole process, so it is an upper bound on
  the streaming cost — never a flattering subset.
* Everything runs on **one machine, one core count, one load** (stated in the output);
  p50/p95 over ``--runs`` repetitions; a single median on a busy box is noise.

Usage::

    .venv/bin/python tools/gv_stream_perf.py pty --runs 5 --chars 24000
    .venv/bin/python tools/gv_stream_perf.py pty --runs 5 --hz 30 --fixture probe
    .venv/bin/python tools/gv_stream_perf.py pipe --runs 3
    .venv/bin/python tools/gv_stream_perf.py table
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import pty
import select
import statistics
import struct
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
SCRIPT = REPO / ".venv" / "bin" / "skaldir"

#: the answer's unit: a marker-only file makes "an answer byte" unambiguous on the pty
MARKER = "SKALDIR-STREAM-MARK"
MARKER_LINE = MARKER + "\n"

#: the replay command's own completion line — emitted once, after the answer's last byte
DONE_MARK = "replayed "

ROWS, COLS = 24, 80

#: how long to wait for the shell to reach its prompt / to finish a command
READY_TIMEOUT_S = 30.0
COMMAND_TIMEOUT_S = 90.0

#: the shell is "ready" when it has printed its banner/prompt and then gone quiet for this
#: long — a text needle would break the moment --json suppresses the banner
QUIESCE_S = 0.4

#: with ``--json`` the shell prints no banner and no prompt at all, so "it went quiet" is
#: the wrong signal there: the startup budget below is the wait instead. It only has to
#: cover reaching the read loop (`--no-probe` keeps the startup work small).
STARTUP_SETTLE_S = 1.5

#: where the package under test lives; ``--src`` replaces it with a wired scratch tree
SRC_OVERRIDE: str = ""

#: a shell that answered the command with a CLI error cannot finish the answer, and
#: waiting the whole timeout for markers that will never come wastes a run
ERROR_MARKERS = (
    "error: argument command",
    "Traceback (most recent call last)",
    "invalid choice",
)

EXIT_OK = 0
EXIT_BLOCKED = 3
EXIT_INTERNAL = 7


# --------------------------------------------------------------------------- #
# the pinned environment: no sibling's PYTHONPATH, no sandboxed runtime
# --------------------------------------------------------------------------- #


def child_env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    for name in (
        "HERMES_HOME", "GODSV_HOME", "SKALDIR_HOME",
        "GODSV_LLM_KEY", "GODSV_LLM_BASE_URL", "GODSV_LLM_MODEL", "GODSV_LLM_STREAM",
        "SKALDIR_LLM_KEY", "SKALDIR_LLM_BASE_URL", "SKALDIR_LLM_MODEL",
        "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
    ):
        env.pop(name, None)
    # a leaked PYTHONPATH silently measures another checkout — pin ours (or the
    # wired scratch tree the hand-over snippet was applied to)
    env["PYTHONPATH"] = SRC_OVERRIDE or str(SRC)
    env["TMPDIR"] = "/tmp"
    env["NO_COLOR"] = "1"  # keep the byte count about the answer, not about SGR runs
    env["TERM"] = "xterm"
    env.update(extra)
    return env


def child_argv(*extra: str) -> list[str]:
    """The real entry point: the installed console script, or ``-m skaldir``."""
    if SRC_OVERRIDE:
        # inside a scratch tree the installed script would still import the repo's
        # editable install, so run the package the PYTHONPATH actually points at
        return [sys.executable, "-m", "skaldir", "shell", *extra]
    if SCRIPT.is_file():
        return [str(SCRIPT), "shell", *extra]
    return [sys.executable, "-m", "skaldir", "shell", *extra]


# --------------------------------------------------------------------------- #
# the fixture: an answer made only of markers
# --------------------------------------------------------------------------- #


def write_fixture(path: Path, repetitions: int) -> int:
    """Write ``repetitions`` marker lines; returns the byte count (== the answer size)."""
    text = MARKER_LINE * repetitions
    path.write_text(text, encoding="utf-8")
    return len(text)


# --------------------------------------------------------------------------- #
# a pty session with a timestamping reader
# --------------------------------------------------------------------------- #


class PtySession:
    """One real pty, an 80x24 window, a reader thread that timestamps every read."""

    def __init__(self, argv: list[str], *, cwd: Path, rows: int = ROWS, cols: int = COLS,
                 env: dict[str, str] | None = None) -> None:
        self.argv = argv
        self.rows, self.cols = rows, cols
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, 0, 0))
        # the window the child will see, read back from the slave — not assumed
        try:
            self.winsize = struct.unpack(
                "HHHH",
                fcntl.ioctl(slave, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0)),
            )
        except OSError:
            self.winsize = (rows, cols, 0, 0)
        self.proc = subprocess.Popen(
            argv,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=str(cwd),
            env=env or child_env(),
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave)
        self.reads: list[tuple[float, int]] = []
        self.chunks: list[tuple[float, str]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._pump, name="pty-reader", daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([self.master], [], [], 0.05)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            try:
                data = os.read(self.master, 65536)
            except OSError:
                return
            if not data:
                return
            moment = time.monotonic()
            text = data.decode("utf-8", "replace")
            self.reads.append((moment, len(data)))
            self.chunks.append((moment, text))

    # -- the two facts the measurement needs ---------------------------------- #

    def cpu_ms(self) -> float | None:
        """The child's own CPU time (utime + stime), in milliseconds."""
        try:
            stat = Path(f"/proc/{self.proc.pid}/stat").read_text(encoding="utf-8")
        except OSError:
            return None
        fields = stat.rsplit(")", 1)[-1].split()
        try:
            utime, stime = int(fields[11]), int(fields[12])
        except (IndexError, ValueError):
            return None
        return 1000.0 * (utime + stime) / os.sysconf("SC_CLK_TCK")

    def text_since(self, index: int) -> str:
        return "".join(text for _moment, text in self.chunks[index:])

    def wait_for(self, needle: str, *, timeout: float = READY_TIMEOUT_S,
                 start: int = 0) -> int:
        """Wait until ``needle`` appears after read ``start``; returns the read index."""
        deadline = time.monotonic() + timeout
        seen = ""
        while time.monotonic() < deadline:
            seen = "".join(text for _m, text in self.chunks[start:])
            if needle in seen:
                return len(self.chunks)
            if self.proc.poll() is not None and self.chunks:
                return len(self.chunks)
            time.sleep(0.01)
        raise TimeoutError(f"never saw {needle!r} (got {seen[-200:]!r})")

    def wait_ready(self, *, timeout: float = READY_TIMEOUT_S,
                   quiesce: float = QUIESCE_S,
                   startup_settle: float = STARTUP_SETTLE_S) -> int:
        """Wait until the shell is at its read loop.

        Two shapes, because the shell has two: with a banner it prints and then goes quiet
        for ``quiesce``; with ``--json`` it prints nothing at all, so the only honest wait
        is a startup budget. A text needle is avoided either way — the banner's wording is
        not a contract and ``--json`` removes it.
        """
        deadline = time.monotonic() + timeout
        last_count = 0
        last_change = time.monotonic()
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise TimeoutError(
                    f"shell exited before it was ready (rc={self.proc.returncode}, "
                    f"{''.join(t for _m, t in self.chunks)[-200:]!r})"
                )
            if len(self.chunks) != last_count:
                last_count = len(self.chunks)
                last_change = time.monotonic()
                continue
            quiet_for = time.monotonic() - last_change
            if last_count > 0 and quiet_for >= quiesce:
                return last_count
            if last_count == 0 and quiet_for >= startup_settle:
                return last_count
            time.sleep(0.01)
        raise TimeoutError(
            f"shell never became ready ({len(self.chunks)} reads, "
            f"{''.join(t for _m, t in self.chunks)[-200:]!r})"
        )

    def write(self, text: str) -> float:
        """Write to the pty; returns the moment the bytes were handed to the kernel."""
        os.write(self.master, text.encode("utf-8"))
        return time.monotonic()

    def close(self) -> None:
        self._stop.set()
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except OSError:
            pass
        try:
            os.close(self.master)
        except OSError:
            pass
        self._thread.join(timeout=1.0)


# --------------------------------------------------------------------------- #
# one measured run
# --------------------------------------------------------------------------- #


def run_pty_once(*, hz: float | None, repetitions: int, source: Path, rate: float,
                 chunk_bytes: int, quiet: bool = True,
                 json_summary: bool = False) -> dict:
    """One shell session, one streamed answer, everything timestamped.

    ``hz is None`` measures the unpaced arm (every chunk its own write); a number measures
    the paced arm. The two arms differ in exactly one flag.

    ``json_summary`` adds ``--json`` to the shell so its closing envelope carries the
    in-process render counters (a second, independent method for the same quantity). It is
    off by default because ``--json`` also turns the footer and the activity line off — the
    default arm is the experience the user actually gets.
    """
    expect = MARKER_LINE * repetitions
    argv = child_argv("--no-probe", "--no-color")
    if json_summary:
        argv += ["--json"]
    # 0 is an explicit "never pace" — without the flag an interactive terminal is paced by
    # default, and the control arm would silently be a second paced arm (measured, then fixed)
    argv += ["--stream-hz", f"{hz:g}" if hz is not None else "0"]
    session = PtySession(argv, cwd=REPO)
    result: dict = {"hz": hz, "ok": False}
    try:
        session.wait_ready()
        base = len(session.chunks)
        command = (
            f"stream replay --source {source} --chunk-bytes {chunk_bytes} "
            f"--rate {rate:g} --limit {len(expect)}\n"
        )
        cpu_before = session.cpu_ms()
        enter_at = session.write(command)
        wanted = repetitions
        deadline = time.monotonic() + COMMAND_TIMEOUT_S
        first_at: float | None = None
        last_at: float | None = None
        markers = 0
        cursor = base
        # a marker can be split across two reads (a 6-byte chunk never carries 19 chars of
        # it, and the pty hands each small write back separately) — so count on a rolling
        # window that keeps the last len(MARKER)-1 characters, not on single reads
        carry = ""
        while time.monotonic() < deadline:
            if cursor < len(session.chunks):
                moment, text = session.chunks[cursor]
                cursor += 1
                window = carry + text
                found = window.count(MARKER)
                carry = window[-(len(MARKER) - 1):] if len(MARKER) > 1 else ""
                if found:
                    if first_at is None:
                        first_at = moment
                    markers += found
                # The END is the replay's own completion line, not the last marker: a
                # marker can straddle a read (or be broken by anything else that writes the
                # same terminal), so counting it would make "Enter -> last byte" depend on
                # how the bytes happened to be cut. The completion line is emitted once,
                # after the answer's last byte, and both arms use the same rule.
                if DONE_MARK in window:
                    last_at = moment
                    break
                if any(marker in text for marker in ERROR_MARKERS):
                    # a CLI error means no answer will ever arrive: stop now instead of
                    # burning the whole timeout waiting for markers that cannot come
                    result["command_error"] = text.strip()[-300:]
                    break
            elif session.proc.poll() is not None:
                break
            else:
                time.sleep(0.001)
        cpu_after = session.cpu_ms()
        # one non-empty read == one write that reached the pty master. A read may carry
        # several renders the kernel merged (more likely in the unpaced arm, where writes
        # are 2.5 ms apart) — the in-process counter below is the exact one.
        window_reads = [
            moment for moment, _text in session.chunks[base:]
            if first_at is not None and last_at is not None and first_at <= moment <= last_at
        ]
        shell_totals = _quit_and_read_summary(session) if json_summary else {}
        answer = "".join(text for _m, text in session.chunks[base:])
        result.update(
            {
                "ok": first_at is not None and last_at is not None,
                "answer_intact": markers == wanted,
                "markers": markers,
                "markers_expected": wanted,
                "enter_to_first_ms": None if first_at is None
                else int((first_at - enter_at) * 1000),
                "enter_to_last_ms": None if last_at is None
                else int((last_at - enter_at) * 1000),
                "markers_in_transcript": answer.count(MARKER),
                "renders": len(window_reads),
                "renders_per_s": None if not first_at or not last_at or last_at <= first_at
                else round(len(window_reads) / (last_at - first_at), 1),
                "cpu_ms": None if cpu_before is None or cpu_after is None
                else int(cpu_after - cpu_before),
                "cpu_method": "child /proc/<pid>/stat utime+stime delta, sampled by the "
                "reader at Enter and at the last marker",
                "pty_bytes": sum(size for _m, size in session.reads),
                "window": f"{session.winsize[0]}x{session.winsize[1]}",
                "answer_bytes": len(answer.encode("utf-8", "replace")),
                "command": command.strip(),
                **shell_totals,
            }
        )
        master = result.get("renders")
        shell = result.get("shell_renders")
        if isinstance(master, int) and isinstance(shell, int) and shell:
            result["render_count_agreement"] = (
                "exact" if master == shell else f"master {master} vs shell {shell} "
                f"({round(100 * master / shell)}% of writes were separate reads)"
            )
        if not quiet:
            result["transcript_tail"] = answer[-800:]
    except TimeoutError as exc:
        result["error"] = str(exc)
    finally:
        session.close()
    return result


#: the shell's own summary line carries the in-process render counters (--json)
def _quit_and_read_summary(session: PtySession) -> dict:
    """Quit the session and pull the shell's own streaming totals out of its envelope.

    The master-side read count is an *independent* oracle, but the kernel may merge two
    writes into one read. The shell's summary counts renders where they happen, so the
    two together say whether the read count is the exact one — and if they disagree the
    payload says by how much instead of quietly quoting the flattering number.
    """
    try:
        session.write("/quit\n")
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and session.proc.poll() is None:
            time.sleep(0.02)
        text = session.text_since(0)
    except OSError:
        return {}
    totals: dict = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith('{"schema_version"'):
            continue
        try:
            payload = json.loads(stripped)
        except ValueError:
            continue
        data = payload.get("data") or {}
        if isinstance(data, dict) and "renders" in data:
            totals = {
                "shell_renders": data.get("renders"),
                "shell_chunks": data.get("chunks"),
                "shell_cpu_ms": data.get("stream_cpu_ms"),
                "shell_stream_hz": data.get("stream_hz"),
            }
    return totals


def run_pipe_once(*, hz: float | None, repetitions: int, source: Path, rate: float,
                  chunk_bytes: int, explicit: bool = True) -> dict:
    """The control: the same shell, the same command, stdout is a pipe (no pty).

    ``explicit=False`` passes **no** ``--stream-hz`` at all — that arm measures the default
    policy for a pipe, which is the claim: nothing is paced unless the caller says so.
    """
    expect = MARKER_LINE * repetitions
    argv = child_argv("--no-probe", "--no-color", "--no-activity")
    if explicit:
        argv += ["--stream-hz", f"{hz:g}" if hz is not None else "0"]
    command = (
        f"stream replay --source {source} --chunk-bytes {chunk_bytes} "
        f"--rate {rate:g} --limit {len(expect)}\n"
    )
    started = time.monotonic()
    first_at: float | None = None
    last_at: float | None = None
    markers = 0
    reads = 0
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=str(REPO), env=child_env(),
    )
    assert proc.stdout is not None and proc.stdin is not None
    try:
        proc.stdin.write(command.encode("utf-8"))
        proc.stdin.write(b"/quit\n")
        proc.stdin.flush()
        deadline = time.monotonic() + COMMAND_TIMEOUT_S
        carry = ""
        while time.monotonic() < deadline:
            ready, _, _ = select.select([proc.stdout], [], [], 0.05)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            data = os.read(proc.stdout.fileno(), 65536)
            if not data:
                break
            moment = time.monotonic()
            reads += 1
            text = data.decode("utf-8", "replace")
            window = carry + text
            found = window.count(MARKER)
            carry = window[-(len(MARKER) - 1):] if len(MARKER) > 1 else ""
            if found:
                if first_at is None:
                    first_at = moment
                markers += found
            if DONE_MARK in window:
                last_at = moment
                break
        proc.terminate()
        proc.wait(timeout=5)
    except (BrokenPipeError, OSError):
        pass
    return {
        "hz": hz,
        "ok": first_at is not None and last_at is not None,
        "answer_intact": markers == repetitions,
        "markers": markers,
        "markers_expected": repetitions,
        "enter_to_first_ms": None if first_at is None else int((first_at - started) * 1000),
        "enter_to_last_ms": None if last_at is None else int((last_at - started) * 1000),
        "reads": reads,
        "pipe": True,
        "cpu_method": "not measured for the pipe arm (the point of this arm is the "
        "read boundary, and the shell's startup dominates any CPU delta)",
    }


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #


def percentile(values: list[float], fraction: float) -> float:
    """Linear-interpolated percentile; the caller only passes a non-empty list."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return float(ordered[low] * (1 - weight) + ordered[high] * weight)


def aggregate(runs: list[dict], fields: tuple[str, ...]) -> dict:
    summary: dict = {"runs": len(runs), "ok": sum(1 for run in runs if run.get("ok"))}
    for field in fields:
        values = [run[field] for run in runs if isinstance(run.get(field), (int, float))]
        summary[field] = {
            "p50": None if not values else round(percentile(values, 0.5), 1),
            "p95": None if not values else round(percentile(values, 0.95), 1),
            "min": None if not values else min(values),
            "max": None if not values else max(values),
        }
    return summary


PTY_FIELDS = ("enter_to_first_ms", "enter_to_last_ms", "renders", "renders_per_s", "cpu_ms")


# --------------------------------------------------------------------------- #
# the in-process settle check (no terminal needed)
# --------------------------------------------------------------------------- #


def settle_check(*, chunks: int = 200, hz: float = 30.0,
                 chunk_interval_s: float = 1.0 / 300.0) -> dict:
    """Drive the coalescer against an injectable clock and check the bound to the ms.

    ``run_stream`` writes into a counter sink; the clock's instant is derived from how many
    chunks have been written, so the whole run is deterministic and finishes in microseconds
    while still exercising the interval arithmetic.
    """
    sys.path.insert(0, str(SRC))
    from skaldir import streaming as S  # noqa: PLC0415 — the repo under test

    class Counter:
        def __init__(self) -> None:
            self.writes: list[str] = []

        def write(self, text: str) -> int:
            self.writes.append(text)
            return len(text)

        def flush(self) -> None:
            return None

    sink = Counter()
    state = {"chunks": 0}

    def clock() -> float:
        return state["chunks"] * chunk_interval_s

    def pace(_index: int) -> None:
        return None

    stream = S.Coalescer(sink, hz=hz, clock=clock)
    for index in range(chunks):
        state["chunks"] = index + 1
        stream.write("x" * 6)
    stream.close()
    metrics = stream.metrics
    expected_bound = int(hz * (chunks * chunk_interval_s)) + 2
    return {
        "chunks": chunks,
        "hz": hz,
        "renders": metrics.renders,
        "bound": expected_bound,
        "within_bound": metrics.renders <= expected_bound,
        "first_write_immediate": bool(sink.writes) and sink.writes[0] == "x" * 6,
        "bytes": sum(len(item) for item in sink.writes),
        "expected_bytes": chunks * 6,
        "byte_identical": sum(len(item) for item in sink.writes) == chunks * 6,
        "chunks_per_render": round(chunks / metrics.renders, 1) if metrics.renders else None,
        "median_render_gap_chunks": _median_gap(sink.writes),
    }


def _median_gap(writes: list[str]) -> float | None:
    """How many chunks separate two renders, p50 — the suppression, in the user's units."""
    if not writes:
        return None
    gaps = [len(item) / 6 for item in writes]
    return round(statistics.median(gaps), 1)


# --------------------------------------------------------------------------- #
# verbs
# --------------------------------------------------------------------------- #


def _fixture(workdir: Path, repetitions: int) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / "stream-answer.txt"
    write_fixture(path, repetitions)
    return path


def cmd_pty(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir or "/tmp/gv-stream-perf")
    repetitions = max(1, args.chars // len(MARKER_LINE))
    source = _fixture(workdir, repetitions)
    answer_bytes = repetitions * len(MARKER_LINE)
    arms: dict[str, dict] = {}
    for label, hz in (("unpaced", None), ("paced", args.hz)):
        runs: list[dict] = []
        for index in range(args.runs):
            run = run_pty_once(hz=hz, repetitions=repetitions, source=source,
                               rate=args.rate, chunk_bytes=args.chunk_bytes,
                               quiet=index != 0,
                               json_summary=bool(args.json_summary))
            run["run"] = index + 1
            runs.append(run)
            print(f"  {label} run {index + 1}/{args.runs}: "
                  f"first={run.get('enter_to_first_ms')} ms "
                  f"last={run.get('enter_to_last_ms')} ms "
                  f"renders={run.get('renders')} cpu={run.get('cpu_ms')} ms"
                  f"{' ERROR: ' + str(run.get('error')) if run.get('error') else ''}",
                  flush=True)
        arms[label] = {"runs": runs, "summary": aggregate(runs, PTY_FIELDS)}
    payload = {
        "harness": "tools/gv_stream_perf.py pty",
        "window": f"{ROWS}x{COLS}",
        "answer_bytes": answer_bytes,
        "markers": repetitions,
        "source": str(source),
        "rate": args.rate,
        "chunk_bytes": args.chunk_bytes,
        "hz": args.hz,
        "arms": arms,
        "machine": {
            "cpus": os.cpu_count(),
            "python": sys.version.split()[0],
            "entry": str(SCRIPT) if SCRIPT.is_file() else f"{sys.executable} -m skaldir",
        },
        "method": {
            "enter_to_first": "pty master write of the command's newline → first read "
            "carrying an answer marker",
            "enter_to_last": "→ the read that completed the last expected marker",
            "renders": "counted on the MASTER side: one non-empty os.read per render "
            "(independent of the in-process counter)",
            "renders_per_s": "renders / (last - first), i.e. inside the streaming window",
            "cpu_ms": "child /proc/<pid>/stat utime+stime delta (whole process, upper bound)",
            "percentiles": "p50/p95 over --runs repetitions, linear interpolation",
        },
    }
    out = Path(args.out or (workdir / "pty.json"))
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"unpaced": arms["unpaced"]["summary"],
                      "paced": arms["paced"]["summary"]}, indent=2, sort_keys=True))
    if args.describe:
        for label in ("unpaced", "paced"):
            first = arms[label]["runs"][0]
            print(f"\n----- what the user saw ({label}) -----")
            print(str(first.get("transcript_tail", "(no transcript captured)")))
    print(f"results: {out}")
    short = [run for arm in arms.values() for run in arm["runs"]
             if not run.get("answer_intact")]
    if short:
        print(f"NOTE: {len(short)} run(s) counted fewer answer markers than written "
              f"(a marker straddled a read boundary) — the byte count, not the marker "
              f"count, is what the timing uses", file=sys.stderr)
    worst = [run for arm in arms.values() for run in arm["runs"] if not run.get("ok")]
    if worst:
        print(f"BLOCKED: {len(worst)} run(s) never completed the answer", file=sys.stderr)
        return EXIT_BLOCKED
    return EXIT_OK


def cmd_pipe(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir or "/tmp/gv-stream-perf")
    repetitions = max(1, args.chars // len(MARKER_LINE))
    source = _fixture(workdir, repetitions)
    arms: dict[str, dict] = {}
    plan = (
        ("default (no flag)", None, False),
        ("forced off (--stream-hz 0)", None, True),
        (f"forced on (--stream-hz {args.hz:g})", args.hz, True),
    )
    for label, hz, explicit in plan:
        runs = [
            run_pipe_once(hz=hz, repetitions=repetitions, source=source,
                          rate=args.rate, chunk_bytes=args.chunk_bytes, explicit=explicit)
            for _ in range(args.runs)
        ]
        arms[label] = {
            "runs": runs,
            "summary": aggregate(
                runs, ("enter_to_first_ms", "enter_to_last_ms", "reads"),
            ),
        }
        print(f"  {label}: reads p50 "
              f"{arms[label]['summary']['reads']['p50']} "
              f"first p50 {arms[label]['summary']['enter_to_first_ms']['p50']} ms",
              flush=True)
    payload = {
        "harness": "tools/gv_stream_perf.py pipe",
        "note": "stdout is a pipe: the shell must not pace it unless asked",
        "claim": "reads == chunks in the default and forced-off arms (nothing coalesced); "
        "reads << chunks in the forced-on arm (the bound applies when requested)",
        "arms": arms,
    }
    out = Path(args.out or (workdir / "pipe.json"))
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    print(f"results: {out}")
    return EXIT_OK


def cmd_settle(args: argparse.Namespace) -> int:
    report = settle_check(chunks=args.chunks, hz=args.hz)
    print(json.dumps(report, indent=2, sort_keys=True))
    ok = report["within_bound"] and report["byte_identical"] and report["first_write_immediate"]
    print("SETTLE: " + ("ok" if ok else "FAILED"), file=sys.stderr)
    return EXIT_OK if ok else EXIT_BLOCKED


def cmd_table(args: argparse.Namespace) -> int:
    path = Path(args.results or "/tmp/gv-stream-perf/pty.json")
    if not path.is_file():
        print(f"no results at {path} — run `pty` first", file=sys.stderr)
        return EXIT_BLOCKED
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(f"| metric | unpaced | paced ({payload.get('hz')} Hz) |")
    print("|---|---:|---:|")
    rows = (
        ("Enter → first byte p50 (ms)", "enter_to_first_ms", "p50"),
        ("Enter → first byte p95 (ms)", "enter_to_first_ms", "p95"),
        ("Enter → last byte p50 (ms)", "enter_to_last_ms", "p50"),
        ("Enter → last byte p95 (ms)", "enter_to_last_ms", "p95"),
        ("renders p50", "renders", "p50"),
        ("renders/s p50", "renders_per_s", "p50"),
        ("render CPU p50 (ms)", "cpu_ms", "p50"),
        ("render CPU p95 (ms)", "cpu_ms", "p95"),
    )
    for label, field, stat in rows:
        left = payload["arms"]["unpaced"]["summary"].get(field, {}).get(stat)
        right = payload["arms"]["paced"]["summary"].get(field, {}).get(stat)
        print(f"| {label} | {left} | {right} |")
    print()
    print(f"window {payload['window']} · answer {payload['answer_bytes']} B "
          f"· {payload['markers']} markers · chunk {payload['chunk_bytes']} B "
          f"· rate {payload['rate']}/s · {payload['machine']['cpus']} CPUs")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gv_stream_perf",
        description="streaming responsiveness: Enter→first byte, renders/s, CPU",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--runs", type=int, default=3, help="repetitions (p50/p95)")
        target.add_argument("--chars", type=int, default=24000,
                            help="answer size in bytes")
        target.add_argument("--hz", type=float, default=30.0,
                            help="the paced arm's render bound, renders/second")
        target.add_argument("--rate", type=float, default=400.0,
                            help="chunks/second the fixture writes at")
        target.add_argument("--chunk-bytes", type=int, default=6,
                            help="bytes per chunk (a token is 3-12 bytes)")
        target.add_argument("--workdir", default="/tmp/gv-stream-perf",
                            help="where the fixture and the results live")
        target.add_argument("--out", default="", help="results file")

    for name, func, help_text in (
        ("pty", cmd_pty, "a real interactive shell on a real 80x24 pty"),
        ("pipe", cmd_pipe, "the same shell with stdout as a pipe (the control)"),
    ):
        target = sub.add_parser(name, help=help_text)
        common(target)
        target.add_argument("--src", default="",
                            help="the package under test (a wired scratch tree); "
                                 "default: this repo's src/")
        target.set_defaults(func=func)

    sub.choices["pty"].add_argument(
        "--json-summary", action="store_true",
        help="run the shell with --json too, so its own envelope cross-checks the "
             "render count (turns the footer and the activity line off)")
    sub.choices["pty"].add_argument(
        "--describe", action="store_true",
        help="print the last arm's transcript tail (what the user saw)")

    settle = sub.add_parser("settle", help="the in-process bound check (no terminal)")
    settle.add_argument("--chunks", type=int, default=200, help="chunks to push")
    settle.add_argument("--hz", type=float, default=30.0, help="the render bound")
    settle.set_defaults(func=cmd_settle)

    table = sub.add_parser("table", help="render a results file as Markdown")
    table.add_argument("--results", default="/tmp/gv-stream-perf/pty.json",
                       help="the results file to render")
    table.set_defaults(func=cmd_table)
    return parser


def main(argv: list[str] | None = None) -> int:
    global SRC_OVERRIDE
    parser = build_parser()
    args = parser.parse_args(argv)
    declared = str(getattr(args, "src", "") or "")
    if declared:
        SRC_OVERRIDE = str(Path(declared).resolve())
        if not (Path(SRC_OVERRIDE) / "skaldir" / "streaming.py").is_file():
            print(f"--src {declared} does not carry skaldir/streaming.py", file=sys.stderr)
            return EXIT_BLOCKED
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
