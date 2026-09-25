#!/usr/bin/env python3
"""Enter → first *content* byte: the felt latency of ``agent run``, measured in a real PTY.

Why a PTY and a stub, not a stopwatch: a stopwatch measures the command, a PTY measures
what the *terminal* received and when — including the shell's own chrome, the progress
lines and every escape. The provider is a local stub with a **fixed** per-chunk delay and
a fixed time-to-first-token, so the two arms differ in exactly one flag and nothing else.

The two arms are the whole point of this harness:

``before``   ``--no-stream`` — the client returns a *whole* answer, so the first content
             byte a user sees is the *completed* answer (TTFT + every chunk's delay)
``after``    streaming (the default on a terminal) — the provider's deltas are handed to
             the renderer as they arrive, so the first content byte is the answer's first
             token, plus the renderer's own 60 ms budget

Measured per run, all of it from outside the process unless stated:

* ``first_chrome_ms`` — first byte of *anything* (the CLI's own action/progress line);
* ``first_content_ms`` — first occurrence of the answer's first byte, a **BEL** (0x07) that
  the stub injects once. Nothing else our CLI writes can contain it, so the marker is
  unambiguous even when progress lines and content interleave on one pty;
* ``completion_ms`` — the last content byte;
* ``reads`` / ``reads_per_s`` — the kernel-side count of non-empty ``os.read`` calls on the
  pty master (one per write that reached the terminal). Independent of our own counters;
* ``cpu_ms`` — the child's user+sys CPU (``os.wait4``), not the parent's;
* ``bytes`` — bytes read off the master;
* ``updates_per_s`` / ``first_content_ms`` **also** parsed out of the run's own facts line,
  so the in-process numbers can be checked against the kernel-side ones.

Usage::

    .venv/bin/python tools/gv_stream_ttfb.py pty --runs 5 --py <repo>/.venv/bin/skaldir
    .venv/bin/python tools/gv_stream_ttfb.py pipe --runs 5
    .venv/bin/python tools/gv_stream_ttfb.py table     # the results, as Markdown
"""

from __future__ import annotations

import argparse
import json
import os
import pty
import re
import select
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

#: The answer's first byte. A BEL cannot be produced by any of our render paths, so its
#: position in the capture *is* the first content byte — even with progress lines and the
#: answer interleaved on the same terminal.
CONTENT_MARKER = b"\x07"

#: The answer's visible body. Used to find the *last* content byte (the first one is the
#: marker above); nothing else our CLI writes spells this token.
CONTENT_TOKEN = b"SGMARK"

#: 80x24, as declared. The child must see exactly this before it starts.
ROWS, COLS = 24, 80

OUT_DIR = Path(os.environ.get("GV_STREAM_TTFB_OUT", "/tmp/gv-stream-ttfb"))
RESULT_FILE = OUT_DIR / "results.json"

#: The repository this tool belongs to (the CLI it drives by default).
REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# the stub provider                                                            #
# --------------------------------------------------------------------------- #


@dataclass
class StubPlan:
    """What the stub serves. Every number is fixed, so the arms are comparable."""

    chunks: int = 40
    chunk_bytes: int = 60
    gap_ms: float = 50.0
    ttft_ms: float = 300.0
    #: the visible answer: the marker byte, then this token repeated to `chunk_bytes`
    token: str = "SGMARK"
    #: whether the model's answer ends with a newline. A model quite often does not — and
    #: then the next line printer lands inside the answer unless the renderer terminates
    #: the row (`--no-trailing-newline` measures that case).
    trailing_newline: bool = True

    def answer_text(self) -> str:
        """The marker byte, then lines of the marker token — 6 tokens per line.

        Lines matter: the renderer only draws its activity footer at a line boundary (it
        must never land inside a line the model was in the middle of), so a stub whose
        answer is one endless line would hide that half of the design.
        """
        per_line = self.token * 6
        lines = max(1, (self.chunk_bytes * self.chunks) // (len(per_line) + 1))
        text = "\x07" + (per_line + "\n") * lines
        return text if self.trailing_newline else text.rstrip("\n")

    def content_unit(self) -> bytes:
        """One answer line's body, as bytes: what must arrive *intact* on any stream.

        Deliberately **without** the newline: a pty translates ``\n`` to ``\r\n`` on
        output (ONLCR), so a check that includes the newline would report 0 intact lines on
        a terminal while the stream was perfectly fine — a measurement bug, not a finding.
        """
        return (self.token * 6).encode("utf-8")

    def expected_units(self) -> int:
        """How many answer lines must arrive intact — counted as *lines*, not newlines.

        A last line without a terminator is still a line (and still must not be split).
        """
        text = self.answer_text().rstrip("\n")
        return text.count("\n") + 1 if text else 0

    def slices(self) -> list[str]:
        text = self.answer_text()
        return [text[i : i + self.chunk_bytes] for i in range(0, len(text), self.chunk_bytes)]

    def total_ms(self) -> float:
        return self.ttft_ms + self.gap_ms * max(0, self.chunks - 1)


def _usage(plan: StubPlan) -> dict[str, int]:
    """The stub's usage payload — fixed, so every arm is billed identically."""
    return {"prompt_tokens": 40, "completion_tokens": plan.chunks, "total_tokens": 80}


class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "gv-stream-stub/1"

    def log_message(self, *args: Any) -> None:  # noqa: A003 — silence the access log
        return

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's name
        plan: StubPlan = self.server.plan  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            body = {}
        streaming = bool(body.get("stream"))
        self.server.requests.append(  # type: ignore[attr-defined]
            {"stream": streaming, "model": body.get("model"), "at": time.monotonic()}
        )
        if streaming:
            self._stream(plan)
        else:
            self._whole(plan)

    def _stream(self, plan: StubPlan) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        time.sleep(plan.ttft_ms / 1000.0)
        for index, piece in enumerate(plan.slices()):
            if index:
                time.sleep(plan.gap_ms / 1000.0)
            payload = json.dumps(
                {"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
            )
            self._chunk(f"data: {payload}\n\n")
        final = json.dumps(
            {
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": _usage(plan),
            }
        )
        self._chunk(f"data: {final}\n\n")
        self._chunk("data: [DONE]\n\n")
        self._chunk("", last=True)
        self.close_connection = True

    def _chunk(self, text: str, *, last: bool = False) -> None:
        data = text.encode("utf-8")
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()
        if last:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    def _whole(self, plan: StubPlan) -> None:
        # A non-streaming provider answers *once*, at the end. That is the "before" arm:
        # the user sees nothing until the model has finished.
        time.sleep(plan.total_ms() / 1000.0)
        payload = json.dumps(
            {
                "id": "stub-1",
                "object": "chat.completion",
                "model": "gv-stub",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": plan.answer_text()},
                        "finish_reason": "stop",
                    }
                ],
                "usage": _usage(plan),
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()


class StubProvider:
    """A local, deterministic provider: fixed TTFT, fixed gap, marker-first answer."""

    def __init__(self, plan: StubPlan) -> None:
        self.plan = plan
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
        self.httpd.daemon_threads = True
        self.httpd.plan = plan  # type: ignore[attr-defined]
        self.httpd.requests = []  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def requests(self) -> list[dict[str, Any]]:
        return list(self.httpd.requests)  # type: ignore[attr-defined]

    def __enter__(self) -> StubProvider:
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# --------------------------------------------------------------------------- #
# the two arms                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class RunFacts:
    arm: str
    mode: str
    ok: bool = False
    exit_code: int | None = None
    error: str = ""
    first_chrome_ms: float | None = None
    first_content_ms: float | None = None
    completion_ms: float | None = None
    reads: int = 0
    reads_per_s: float = 0.0
    bytes_read: int = 0
    cpu_ms: int = 0
    wall_ms: int = 0
    escapes: int = 0
    lines_expected: int = 0
    lines_intact: int = 0
    self_facts: dict[str, Any] = field(default_factory=dict)
    capture: str = ""

    def as_dict(self) -> dict[str, Any]:
        out = {key: value for key, value in self.__dict__.items() if key != "capture"}
        return out


#: Where the measured tree lives. The repo is edited by other blocks while this runs, so
#: every measurement pins a **frozen copy** of ``src/`` (the rule: measure a tree, not a
#: moving target) and records which one it was.
TREE = REPO / "src"


def _child_env(base_url: str, *, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in (
            "PYTHONPATH",
            "HERMES_HOME",
            "GODSV_HOME",
            "SKALDIR_HOME",
            "SKALDIR_LLM_BASE_URL",
            "SKALDIR_LLM_API_KEY",
            "SKALDIR_LLM_MODEL",
            "SKALDIR_LLM_STREAM",
            "NO_COLOR",
        )
    }
    env.update(
        {
            "PYTHONPATH": str(TREE),
            "SKALDIR_HOME": str(RUNTIME),
            "SKALDIR_LLM_BASE_URL": base_url,
            "SKALDIR_LLM_MODEL": "gv-stub",
            "SKALDIR_LLM_API_KEY": "stub-key-not-used",
            "TERM": "xterm-256color",
        }
    )
    env.update(extra or {})
    return env


def _argv(entry: str, *, task: str, workspace: Path, stream: bool | None) -> list[str]:
    """The real product entry point, as a user would start it.

    ``entry`` is either an interpreter (``…/bin/python`` ⇒ ``-m skaldir``) or the console
    script itself (``…/bin/skaldir`` ⇒ used directly). Both are the real CLI; the fork used
    here is the one the measurement was taken with, and it is recorded in the results.
    """
    base = Path(entry).name
    head = [entry, "-m", "skaldir"] if base.startswith(("python", "python3", "pypy")) else [entry]
    argv = [
        *head,
        "agent",
        "run",
        task,
        "--tools",
        "builtin",
        "--workspace",
        str(workspace),
        "--model",
        "gv-stub",
        "--max-steps",
        "2",
        "--run-id",
        "ttfb",
    ]
    if stream is True:
        argv.append("--stream")
    if stream is False:
        argv.append("--no-stream")
    return argv


def _parse_self_facts(text: str) -> dict[str, Any]:
    """Read the run's own measured line back out of the capture (cross-check)."""
    facts: dict[str, Any] = {}
    match = re.search(r"first token at ([\d.]+) s", text)
    if match:
        facts["first_content_ms"] = float(match.group(1)) * 1000.0
    match = re.search(r"([\d.]+) s total", text)
    if match:
        facts["total_ms"] = float(match.group(1)) * 1000.0
    match = re.search(r"(\d+) updates \(([\d.]+)/s\)", text)
    if match:
        facts["updates"] = int(match.group(1))
        facts["updates_per_s"] = float(match.group(2))
    match = re.search(r"(\d+) deltas", text)
    if match:
        facts["deltas"] = int(match.group(1))
    match = re.search(r"(\d+) B written", text)
    if match:
        facts["bytes_written"] = int(match.group(1))
    return facts


def run_pty(
    argv: list[str], env: dict[str, str], *, rows: int = ROWS, cols: int = COLS,
    timeout: float = 120.0, cwd: Path | None = None, plan: StubPlan | None = None,
) -> dict[str, Any]:
    """Run one command on a real 80x24 pty and time every read off the master."""
    master, slave = pty.openpty()
    try:
        fcntl = __import__("fcntl")
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        actual = struct.unpack("HHHH", fcntl.ioctl(slave, termios.TIOCGWINSZ, b"\0" * 8))
        attr = termios.tcgetattr(slave)
        attr[3] &= ~termios.ECHO  # our own command must not be counted as output
        termios.tcsetattr(slave, termios.TCSANOW, attr)
        started = time.monotonic()
        child = subprocess.Popen(  # noqa: S603 — an explicit argv, no shell
            argv, stdin=slave, stdout=slave, stderr=slave, env=env, cwd=str(cwd or REPO),
            start_new_session=True, close_fds=True,
        )
    finally:
        os.close(slave)
    events: list[tuple[float, bytes]] = []
    buffer = bytearray()
    status: int | None = None
    usage: Any = None
    deadline = started + timeout
    try:
        while True:
            if time.monotonic() > deadline:
                child.kill()
                break
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:  # EIO on Linux once the slave is gone
                    break
                if not data:
                    break
                events.append((time.monotonic() - started, data))
                buffer.extend(data)
            elif child.poll() is not None:
                # drain whatever is left, then stop
                try:
                    while True:
                        ready, _, _ = select.select([master], [], [], 0.05)
                        if not ready:
                            break
                        data = os.read(master, 65536)
                        if not data:
                            break
                        events.append((time.monotonic() - started, data))
                        buffer.extend(data)
                except OSError:
                    pass
                break
        _, status, usage = os.wait4(child.pid, 0)
    finally:
        os.close(master)
    excepted = (status >> 8) & 0xFF if status is not None else None
    payload = bytes(buffer)
    first_chrome = events[0][0] * 1000.0 if events else None
    marker_at = payload.find(CONTENT_MARKER)
    if marker_at < 0:
        first_content = None
    else:
        seen = 0
        first_content = None
        for stamp, data in events:
            if first_content is None:
                if marker_at < seen + len(data):
                    first_content = stamp * 1000.0
            seen += len(data)
    content_times = [stamp for stamp, data in events if CONTENT_TOKEN in data]
    completion = (content_times[-1] * 1000.0) if content_times else None
    window = (events[-1][0] - events[0][0]) if len(events) > 1 else 0.0
    cpu_ms = int((usage.ru_utime + usage.ru_stime) * 1000) if usage else 0
    lines_expected = plan.expected_units() if plan is not None else 0
    lines_found = payload.count(plan.content_unit()) if plan is not None else 0
    return {
        "winsize": (actual[0], actual[1]),
        "lines_expected": lines_expected,
        "lines_intact": lines_found,
        "exit_code": excepted,
        "first_chrome_ms": first_chrome,
        "first_content_ms": first_content,
        "completion_ms": completion,
        "reads": len(events),
        "reads_per_s": round(len(events) / window, 1) if window > 0 else 0.0,
        "bytes_read": len(payload),
        "escapes": payload.count(b"\x1b"),
        "cpu_ms": cpu_ms,
        "wall_ms": int((events[-1][0] if events else 0.0) * 1000),
        "marker_count": payload.count(CONTENT_MARKER),
        "payload": payload,
    }


def run_pipe(
    argv: list[str], env: dict[str, str], *, timeout: float = 120.0, cwd: Path | None = None,
    plan: StubPlan | None = None,
) -> dict[str, Any]:
    """Run one command with stdout on a pipe; count the writes the consumer saw."""
    started = time.monotonic()
    child = subprocess.Popen(  # noqa: S603
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(cwd or REPO),
        close_fds=True,
    )
    reads: list[tuple[float, bytes]] = []
    err_chunks: list[bytes] = []

    def pump() -> None:
        assert child.stderr is not None
        for chunk in iter(lambda: child.stderr.read(4096), b""):
            err_chunks.append(chunk)

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    out = bytearray()
    assert child.stdout is not None
    while True:
        if time.monotonic() - started > timeout:
            child.kill()
            break
        reader = getattr(child.stdout, "read1", None) or child.stdout.read
        chunk = reader(65536)
        if not chunk:
            break
        reads.append((time.monotonic() - started, chunk))
        out.extend(chunk)
    cpu_ms = 0
    try:
        _, status, usage = os.wait4(child.pid, 0)
        cpu_ms = int((usage.ru_utime + usage.ru_stime) * 1000) if usage else 0
        child.returncode = (status >> 8) & 0xFF
    except ChildProcessError:
        cpu_ms = 0
    thread.join(timeout=5)
    payload = bytes(out)
    marker_at = payload.find(CONTENT_MARKER)
    first_content = None
    if marker_at >= 0:
        seen = 0
        for stamp, data in reads:
            if first_content is None and marker_at < seen + len(data):
                first_content = stamp * 1000.0
            seen += len(data)
    window = (reads[-1][0] - reads[0][0]) if len(reads) > 1 else 0.0
    # A rate over two reads in a sub-millisecond window is arithmetic, not a measurement:
    # a pipe gets None (with a note in the summary) instead of a `11447 writes/s` artefact.
    rate = round(len(reads) / window, 1) if len(reads) >= 3 and window >= 0.1 else None
    lines_expected = plan.expected_units() if plan is not None else 0
    lines_found = payload.count(plan.content_unit()) if plan is not None else 0
    return {
        "lines_expected": lines_expected,
        "lines_intact": lines_found,
        "exit_code": child.returncode,
        "first_chrome_ms": reads[0][0] * 1000.0 if reads else None,
        "first_content_ms": first_content,
        "completion_ms": reads[-1][0] * 1000.0 if reads else None,
        "reads": len(reads),
        "reads_per_s": rate,
        "cpu_ms": cpu_ms,
        "bytes_read": len(payload),
        "escapes": payload.count(b"\x1b"),
        "payload": payload,
        "stderr": b"".join(err_chunks),
    }


# --------------------------------------------------------------------------- #
# the measurement                                                              #
# --------------------------------------------------------------------------- #


@dataclass
class Layout:
    """Paths for one measurement round (a temp workspace + runtime per round)."""

    root: Path
    python: str

    def __post_init__(self) -> None:
        self.workspace = self.root / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "hello.txt").write_text("hello world\n", encoding="utf-8")


def _capture_text(payload: bytes) -> str:
    """Escapes made visible, so a raw capture can be read in a document."""
    text = payload.decode("utf-8", "replace")
    return text.replace("\x1b", "<ESC>").replace("\x07", "<BEL>")


def measure(
    plans: dict[str, StubPlan],
    *,
    runs: int,
    mode: str,
    python_exe: str,
    keep: bool,
    warmup: int = 1,
) -> list[RunFacts]:
    """Both arms, ``runs`` measured rounds and ``warmup`` discarded ones.

    Two things make the p50/p95 comparison fair, both measured rather than reasoned:
    **a discarded warm-up round** (a cold page cache costs the first process ~500 ms —
    it landed entirely on the arm that happened to run first) and **an alternating arm
    order**, so neither arm is systematically the warm one. Reading a delta against a
    journey that was not touched is the only way the number means anything.
    """
    results: list[RunFacts] = []
    arms = list(plans.items())
    for round_index in range(1, runs + warmup + 1):
        order = arms if round_index % 2 == 1 else list(reversed(arms))
        discard = round_index <= warmup
        root = Path(tempfile.mkdtemp(prefix=f"gv-ttfb-{mode}-{round_index}-"))
        layout = Layout(root, python_exe)
        for arm, plan in order:
            stream = arm != "before"
            with StubProvider(plan) as stub:
                env = _child_env(stub.base_url)
                env["SKALDIR_HOME"] = str(root / "runtime")
                argv = _argv(python_exe, task="say the marker", workspace=layout.workspace,
                             stream=True if stream else False)
                facts = RunFacts(arm=arm, mode=mode)
                if mode == "pty":
                    raw = run_pty(argv, env, cwd=layout.workspace, plan=plan)
                    facts.capture = _capture_text(raw["payload"])
                    facts.first_chrome_ms = raw["first_chrome_ms"]
                    facts.first_content_ms = raw["first_content_ms"]
                    facts.completion_ms = raw["completion_ms"]
                    facts.reads = raw["reads"]
                    facts.reads_per_s = raw["reads_per_s"]
                    facts.bytes_read = raw["bytes_read"]
                    facts.cpu_ms = raw["cpu_ms"]
                    facts.wall_ms = raw["wall_ms"]
                    facts.escapes = raw["escapes"]
                    facts.exit_code = raw["exit_code"]
                    facts.ok = raw["first_content_ms"] is not None
                    facts.lines_expected = int(raw["lines_expected"])
                    facts.lines_intact = int(raw["lines_intact"])
                    facts.self_facts = _parse_self_facts(facts.capture)
                    facts.self_facts["stub_stream_requested"] = [
                        request["stream"] for request in stub.requests
                    ]
                    if keep:
                        (OUT_DIR / f"capture-{mode}-{arm}-{round_index}.raw").write_bytes(
                            raw["payload"]
                        )
                        (OUT_DIR / f"capture-{mode}-{arm}-{round_index}.txt").write_text(
                            facts.capture, encoding="utf-8"
                        )
                else:
                    raw = run_pipe(argv, env, cwd=layout.workspace, plan=plan)
                    facts.capture = _capture_text(raw["payload"])
                    facts.first_chrome_ms = raw["first_chrome_ms"]
                    facts.first_content_ms = raw["first_content_ms"]
                    facts.completion_ms = raw["completion_ms"]
                    facts.reads = raw["reads"]
                    facts.reads_per_s = raw["reads_per_s"] or 0.0
                    facts.bytes_read = raw["bytes_read"]
                    facts.escapes = raw["escapes"]
                    facts.cpu_ms = int(raw.get("cpu_ms") or 0)
                    facts.exit_code = raw["exit_code"]
                    facts.ok = raw["first_content_ms"] is not None
                    facts.lines_expected = int(raw["lines_expected"])
                    facts.lines_intact = int(raw["lines_intact"])
                    facts.self_facts = _parse_self_facts(raw["stderr"].decode("utf-8", "replace"))
                    facts.self_facts["stub_stream_requested"] = [
                        request["stream"] for request in stub.requests
                    ]
                    if keep:
                        (OUT_DIR / f"capture-{mode}-{arm}-{round_index}.raw").write_bytes(
                            raw["payload"]
                        )
                        (OUT_DIR / f"capture-{mode}-{arm}-{round_index}.txt").write_text(
                            facts.capture, encoding="utf-8"
                        )
                facts.self_facts["trailing_newline"] = plan.trailing_newline
                facts.self_facts["round"] = round_index
                facts.self_facts["discarded_warmup"] = bool(discard)
                if not discard:
                    results.append(facts)
        if not keep:
            shutil.rmtree(root, ignore_errors=True)
    return results


def _summary(values: list[float]) -> dict[str, float | None]:
    clean = [value for value in values if value is not None]
    if not clean:
        return {"p50": None, "p95": None, "n": 0}
    clean.sort()
    if len(clean) == 1:
        return {"p50": round(clean[0], 1), "p95": round(clean[0], 1), "n": 1}
    return {
        "p50": round(statistics.median(clean), 1),
        "p95": round(clean[min(len(clean) - 1, int(round(0.95 * (len(clean) - 1))))], 1),
        "n": len(clean),
    }


def summarise(results: list[RunFacts]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for arm in ("before", "after"):
        rows = [row for row in results if row.arm == arm]
        if not rows:
            continue
        out[arm] = {
            "runs": len(rows),
            "first_chrome_ms": _summary([r.first_chrome_ms for r in rows]),
            "first_content_ms": _summary([r.first_content_ms for r in rows]),
            "completion_ms": _summary([r.completion_ms for r in rows]),
            "reads": _summary([float(r.reads) for r in rows]),
            "reads_per_s": _summary(
                [r.reads_per_s for r in rows if r.reads_per_s not in (None, 0.0)]
            ),
            "bytes_read": _summary([float(r.bytes_read) for r in rows]),
            "cpu_ms": _summary([float(r.cpu_ms) for r in rows if r.cpu_ms]),
            "escapes": _summary([float(r.escapes) for r in rows]),
            "self_updates_per_s": _summary(
                [float(r.self_facts.get("updates_per_s") or 0.0) for r in rows]
            ),
            "self_updates": _summary([float(r.self_facts.get("updates") or 0.0) for r in rows]),
            "lines_expected": sorted({r.lines_expected for r in rows}),
            "lines_intact": sorted({r.lines_intact for r in rows}),
            "answer_lines_are_integers": all(
                r.lines_expected == r.lines_intact for r in rows if r.lines_expected
            ),
            "reads_per_s_note": (
                "not reported on a pipe: the answer arrives in one or two writes, so a "
                "rate over that window is arithmetic rather than a measurement"
                if not any(r.reads_per_s for r in rows)
                else ""
            ),
            "ok": all(row.ok for row in rows),
        }
    return out


def markdown_table(summary: dict[str, Any], *, mode: str) -> str:
    """The numbers a reader has to be able to check: p50 **and** p95, both arms."""
    lines = [
        f"| metric ({mode}) | before p50 | before p95 | after p50 | after p95 | change (p50) |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    def row(label: str, key: str) -> None:
        before = summary.get("before", {}).get(key, {})
        after = summary.get("after", {}).get(key, {})
        b50, b95 = before.get("p50"), before.get("p95")
        a50, a95 = after.get("p50"), after.get("p95")
        if b50 is None and a50 is None and b95 is None and a95 is None:
            return
        change = ""
        if b50 and a50:
            change = f"{(a50 - b50) / b50 * 100:+.0f}%"
        lines.append(f"| {label} | {b50} | {b95} | {a50} | {a95} | {change} |")

    row("**Enter → first content byte** (ms)", "first_content_ms")
    row("Enter → first byte of anything (ms)", "first_chrome_ms")
    row("Enter → last content byte (ms)", "completion_ms")
    row("terminal writes, kernel-side (count)", "reads")
    row("terminal writes per second", "reads_per_s")
    row("CPU time, child (ms)", "cpu_ms")
    row("bytes written to the terminal", "bytes_read")
    row("escape sequences seen", "escapes")
    row("content updates/s (self-reported)", "self_updates_per_s")
    row("content updates (self-reported)", "self_updates")
    before_lines = summary.get("before", {}).get("lines_intact")
    after_lines = summary.get("after", {}).get("lines_intact")
    lines.append(
        f"| answer lines intact (found / expected) | {before_lines} / "
        f"{summary.get('before', {}).get('lines_expected')} | — | {after_lines} / "
        f"{summary.get('after', {}).get('lines_expected')} | — | — |"
    )
    n_before = summary.get("before", {}).get("runs")
    n_after = summary.get("after", {}).get("runs")
    lines.append("")
    lines.append(f"runs: before {n_before} · after {n_after} (a discarded warm-up round each)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

RUNTIME = Path(tempfile.gettempdir()) / "gv-stream-ttfb-runtime"


def _load_results() -> dict[str, Any]:
    if not RESULT_FILE.is_file():
        return {}
    try:
        return json.loads(RESULT_FILE.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _save(mode: str, results: list[RunFacts], summary: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = _load_results()
    payload[mode] = {
        "tree": str(TREE),
        "answer_ends_with_newline": bool(results[0].self_facts.get("trailing_newline", True))
        if results else None,
        "summary": summary,
        "runs": [row.as_dict() for row in results],
    }
    RESULT_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _plans(chunks: int, gap_ms: float, ttft_ms: float, *,
           trailing_newline: bool = True) -> dict[str, StubPlan]:
    return {
        "before": StubPlan(chunks=chunks, gap_ms=gap_ms, ttft_ms=ttft_ms,
                           trailing_newline=trailing_newline),
        "after": StubPlan(chunks=chunks, gap_ms=gap_ms, ttft_ms=ttft_ms,
                          trailing_newline=trailing_newline),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("mode", choices=("pty", "pipe", "table", "show"))
    parser.add_argument("--runs", type=int, default=5, help="repetitions per arm (default 5)")
    parser.add_argument("--warmup", type=int, default=1,
                        help="rounds to run and discard (default 1: the first process pays "
                             "for the cold page cache)")
    parser.add_argument("--chunks", type=int, default=40, help="content chunks the stub sends")
    parser.add_argument("--gap-ms", type=float, default=50.0, help="delay between chunks")
    parser.add_argument("--ttft-ms", type=float, default=300.0, help="stub time to first token")
    parser.add_argument("--no-trailing-newline", dest="trailing_newline", action="store_false",
                        default=True,
                        help="the stub's answer ends mid-line (a model often does): proves "
                             "the renderer terminates the row before the next printer writes")
    parser.add_argument("--py", default=sys.executable,
                        help="interpreter (…/bin/python ⇒ '-m skaldir') or the console "
                             "script itself (…/bin/skaldir); default: this interpreter")
    parser.add_argument("--keep", action="store_true", default=True,
                        help="keep the raw pty captures (default: keep)")
    parser.add_argument("--no-keep", dest="keep", action="store_false")
    parser.add_argument("--label", default="",
                        help="key to store this round under (default: the mode); a second "
                             "``pty`` round with a different stub needs its own key")
    parser.add_argument("--tree", default="",
                        help="the frozen src/ tree to measure (sets the child's PYTHONPATH); "
                             "default: this repo's src/")
    args = parser.parse_args(argv)

    if args.mode in ("table", "show"):
        payload = _load_results()
        if not payload:
            print(f"no results yet: {RESULT_FILE}")
            return 1
        if args.mode == "show":
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        for mode in ("pty", "pipe"):
            if mode in payload:
                print(f"\n### {mode}\n")
                print(markdown_table(payload[mode]["summary"], mode=mode))
        return 0

    if args.tree:
        global TREE
        TREE = Path(args.tree).expanduser().resolve()
    if not (TREE / "skaldir" / "cli.py").is_file():
        print(f"not a skaldir src/ tree: {TREE}")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = measure(
        _plans(args.chunks, args.gap_ms, args.ttft_ms,
               trailing_newline=bool(args.trailing_newline)),
        runs=max(1, args.runs),
        mode=args.mode,
        python_exe=args.py,
        keep=bool(args.keep),
        warmup=max(0, args.warmup),
    )
    summary = summarise(results)
    key = args.label or args.mode
    _save(key, results, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print()
    print(markdown_table(summary, mode=args.mode))
    for row in results:
        print(
            f"  {row.mode} {row.arm} #{row.exit_code}: first_content={row.first_content_ms} ms"
            f" reads={row.reads} escapes={row.escapes} cpu={row.cpu_ms} ms"
        )
    return 0 if all(row.ok for row in results) else 2


if __name__ == "__main__":
    sys.exit(main())
