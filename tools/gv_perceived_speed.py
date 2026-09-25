#!/usr/bin/env python3
"""Perceived-speed audit — the five journeys a Skaldir user repeats, timed as felt.

What this measures (not what the machine spends): for every journey it records the
*stream of things the user sees*, timestamped, and derives

============================  ====================================================
``first_feedback_ms``         time to the first visible output byte (any output)
``first_result_ms``           time to the first line that reports *what happened*
``first_meaningful_ms``       time to the first content the user actually asked for
``completion_ms``             time until the action is over (prompt back / exit)
``updates``                   how many separate output writes the user's terminal got
``longest_silence_ms``        the worst dead gap — the felt "is it frozen?" moment
============================  ====================================================

The five journeys:

1. ``session-start``   ``skaldir shell`` — launch the interactive session
2. ``question``        ``skaldir agent run "<question>"`` that needs one tool call
3. ``edit``            ``skaldir agent run "<edit request>"`` (read → write → answer)
4. ``test-fix``        the test-fix loop: run the tests, patch, run them again
5. ``resume``          ``skaldir session resume`` after the process that owned the
                       session was killed (a restart)

Method (this is where false numbers come from, so the tool enforces it):

* **The real CLI in a real PTY** (``os.openpty``), so TTY-gated behaviour, colours and
  the readline prompt are the product's, not a pipe's.
* **The real HTTP API** (``skaldir serve --http``) for journey 2, from the same client.
* **A local stub provider** for determinism: a scripted ``/v1/chat/completions``
  endpoint on 127.0.0.1 with a fixed per-call delay. No network, no key, no model —
  the timings are therefore reproducible to a few milliseconds.
* ``PYTHONPATH``/``HERMES_HOME``/``GODSV_HOME`` are pinned per run and the tree is
  named in the output, because a leaked ``PYTHONPATH`` silently measures another
  checkout.

Usage::

    .venv/bin/python tools/gv_perceived_speed.py --label before --runs 3
    .venv/bin/python tools/gv_perceived_speed.py --label after  --runs 3
    .venv/bin/python tools/gv_perceived_speed.py --compare before after

Nothing here writes outside its own output directory (``outputs/perceived-speed/``)
and throwaway scratch homes under ``$TMPDIR``.
"""

from __future__ import annotations

import argparse
import json
import os
import pty
import re
import select
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import termios
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SKAL = REPO / ".venv" / "bin" / "skaldir"
PYTHON = REPO / ".venv" / "bin" / "python"
DEFAULT_DELAY_MS = 400  # the stub's per-model-call cost — the "model thinking" time

JOURNEY_KEYS = ("session-start", "question", "edit", "test-fix", "resume")

JOURNEY_TITLES = {
    "session-start": "start a session (interactive shell)",
    "question": "ask a question that needs one tool call",
    "edit": "ask for an edit",
    "test-fix": "run the test-fix loop (test → patch → test)",
    "resume": "resume after a restart",
}

ANSI = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b[()][0-9A-Za-z]"  # charset select
    r"|\x1b[=>NODM7]"  # single-char escapes
)


# --------------------------------------------------------------------------- #
# the visible stream: ANSI-free, one line per user-visible line               #
# --------------------------------------------------------------------------- #


def visible(text: str) -> str:
    """The text as the user sees it: escapes gone, CR-overwrites resolved."""
    text = ANSI.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def visible_lines(text: str) -> list[str]:
    return [line.rstrip() for line in visible(text).splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# the stub provider — a deterministic OpenAI-compatible endpoint               #
# --------------------------------------------------------------------------- #


def _tool_reply(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "chatcmpl-stub",
        "object": "chat.completion",
        "created": 0,
        "model": "stub-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
    }


def _text_reply(text: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-stub",
        "object": "chat.completion",
        "created": 0,
        "model": "stub-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21},
    }


def _sse_stream(payload: dict[str, Any], *, call_id: str) -> bytes:
    """One answer, as the ``text/event-stream`` body a real endpoint sends for ``stream``.

    A ``stream: true`` request must be answered with SSE: the CLI asks for a stream
    whenever a human is watching a terminal (that is the whole point of the perceived-speed
    audit), and a stub that answers with the whole JSON object instead leaves the product's
    stream reader with zero deltas — measured in the canary test, the journey then reports
    ``incomplete/empty_answer`` and exits 3. Tool calls are fragmented across chunks exactly
    as real endpoints fragment them (name, then the argument string) so the fold is
    exercised rather than assumed. ``usage`` is *not* attached: this stub is never asked for
    ``stream_options.include_usage``, and an endpoint must not invent counters it was not
    asked to send.
    """
    choice = (payload.get("choices") or [{}])[0] or {}
    message = dict(choice.get("message") or {})
    base = {
        "id": payload.get("id", "chatcmpl-stub"),
        "object": "chat.completion.chunk",
        "created": payload.get("created", 0),
        "model": payload.get("model", "stub-model"),
    }
    lines: list[str] = []

    def event(delta: dict[str, Any], finish: str | None = None) -> None:
        body = dict(base)
        body["choices"] = [{"index": 0, "delta": delta, "finish_reason": finish}]
        lines.append("data: " + json.dumps(body))
        lines.append("")

    calls = message.get("tool_calls") or []
    if calls:
        function = dict((calls[0] or {}).get("function") or {})
        event(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": (calls[0] or {}).get("id") or call_id,
                        "type": "function",
                        "function": {"name": str(function.get("name", "")), "arguments": ""},
                    }
                ],
            }
        )
        event(
            {
                "tool_calls": [
                    {"index": 0, "function": {"arguments": str(function.get("arguments") or "")}}
                ]
            }
        )
        event({}, "tool_calls")
    else:
        event({"role": "assistant", "content": str(message.get("content") or "")})
        event({}, "stop")
    lines.append("data: [DONE]")
    lines.append("")
    return ("\n".join(lines) + "\n").encode()


@dataclass
class ScriptedProvider:
    """A local, scripted ``/v1/chat/completions`` endpoint on 127.0.0.1.

    ``steps`` are consumed in order and the last one repeats. ``delay_ms`` is a
    fixed per-call cost — that is the model's "thinking" time in this audit, and
    keeping it constant is what makes before/after numbers comparable.
    """

    steps: list[dict[str, Any]]
    delay_ms: int = DEFAULT_DELAY_MS

    def __post_init__(self) -> None:
        self._t0 = time.monotonic()
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            # A keep-alive connection whose client died would otherwise block the handler
            # thread inside `handle_one_request` forever — and `shutdown()` waits for the
            # serve loop, so the audit itself would hang at teardown. Two seconds is far
            # longer than any request here.
            timeout = 2

            def log_message(self, *args: Any) -> None:  # noqa: ARG002
                return

            def do_POST(self) -> None:  # noqa: N802 — http.server's name
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8", "replace") or "{}")
                except ValueError:
                    body = {"_unparsed": raw.decode("utf-8", "replace")}
                with stub._lock:
                    position = len(stub.requests)
                    stub.requests.append(
                        {
                            "at_ms": round((time.monotonic() - stub._t0) * 1000, 2),
                            "body_bytes": len(raw),
                            "messages": len(body.get("messages") or []),
                            "tools": len(body.get("tools") or []),
                            "stream": bool(body.get("stream")),
                        }
                    )
                    step = stub.steps[min(position, len(stub.steps) - 1)]
                if stub.delay_ms:
                    time.sleep(stub.delay_ms / 1000)
                if "tool" in step:
                    payload = _tool_reply(
                        f"call_{position}", str(step["tool"]), dict(step.get("arguments") or {})
                    )
                else:
                    payload = _text_reply(str(step.get("text", "")))
                if body.get("stream"):  # an SSE request gets an SSE answer, never a JSON body
                    data = _sse_stream(payload, call_id=f"call_{position}")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def calls(self) -> int:
        return len(self.requests)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


# --------------------------------------------------------------------------- #
# the PTY runner                                                              #
# --------------------------------------------------------------------------- #


@dataclass
class Interaction:
    """Write bytes to the session when a pattern appears (or after a delay)."""

    send: bytes
    when_pattern: bytes | None = None
    after_ms: float = 0.0
    once: bool = True


@dataclass
class Capture:
    """Everything one journey run produced, timestamped from the launch moment."""

    events: list[tuple[float, bytes]] = field(default_factory=list)
    stdin_writes: list[tuple[float, bytes]] = field(default_factory=list)
    exit_code: int = -1
    total_ms: float = 0.0
    timed_out: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return b"".join(data for _, data in self.events).decode("utf-8", "replace")


def run_pty(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    env: dict[str, str],
    interactions: Sequence[Interaction] = (),
    timeout_s: float = 180.0,
) -> Capture:
    """Run ``argv`` under a real PTY and record every write with its timestamp.

    TTY echo is switched off on the slave: the terminal driver's echo of *our*
    programmatic input is not product output, and counting it would fake both the
    first-feedback time and the update count.
    """
    master, slave = pty.openpty()
    try:
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
    except termios.error:  # pragma: no cover — a pty without termios is not a pty
        pass
    start = time.monotonic()

    def ms() -> float:
        return (time.monotonic() - start) * 1000

    process = subprocess.Popen(  # noqa: S603 — argv, never a shell
        [str(item) for item in argv],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=str(cwd),
        env=env,
        start_new_session=True,
        close_fds=True,
    )
    os.close(slave)
    capture = Capture()
    pending = list(interactions)
    seen = bytearray()
    deadline = start + timeout_s
    try:
        while True:
            ready, _, _ = select.select([master], [], [], 0.002)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    data = b""
                if data:
                    capture.events.append((round(ms(), 2), data))
                    seen.extend(data)
                    continue
            for item in list(pending):
                fire = False
                if item.when_pattern is not None and item.when_pattern in seen:
                    fire = True
                elif item.when_pattern is None and ms() >= item.after_ms:
                    fire = True
                if not fire:
                    continue
                os.write(master, item.send)
                capture.stdin_writes.append((round(ms(), 2), item.send))
                if item.once:
                    pending.remove(item)
                elif item.when_pattern is not None:
                    seen.clear()
            if process.poll() is not None:
                # drain what is still in the buffer
                for _ in range(200):
                    ready, _, _ = select.select([master], [], [], 0.02)
                    if not ready:
                        break
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    capture.events.append((round(ms(), 2), data))
                break
            if time.monotonic() > deadline:
                capture.timed_out = True
                with _suppress():
                    os.killpg(process.pid, signal.SIGKILL)
                break
    finally:
        with _suppress():
            process.wait(timeout=10)
        capture.exit_code = int(process.returncode if process.returncode is not None else -1)
        capture.total_ms = round(ms(), 2)
        with _suppress():
            os.close(master)
    return capture


class _suppress:
    """``contextlib.suppress(Exception)`` without the import ceremony."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: Any) -> bool:
        return True


# --------------------------------------------------------------------------- #
# metrics                                                                     #
# --------------------------------------------------------------------------- #


def _first_match_at(capture: Capture, pattern: re.Pattern[str]) -> float | None:
    """When the pattern first appeared in the user-visible stream, in ms."""
    buffer = ""
    for at, data in capture.events:
        buffer += visible(data.decode("utf-8", "replace"))
        if pattern.search(buffer):
            return at
    return None


@dataclass
class Marks:
    """The per-journey regexes that turn a raw stream into felt timings."""

    result: str
    meaningful: str
    complete: str = ""


def metrics_for(capture: Capture, marks: Marks) -> dict[str, Any]:
    """Derive the felt timings from a capture (all times relative to launch)."""
    visible_events = [
        (at, data)
        for at, data in capture.events
        if visible(data.decode("utf-8", "replace")).strip()
    ]
    first_feedback = visible_events[0][0] if visible_events else None
    longest_silence = 0.0
    previous = 0.0
    for at, _ in visible_events:
        longest_silence = max(longest_silence, at - previous)
        previous = at
    lines = visible_lines(capture.text)
    first_result = _first_match_at(capture, re.compile(marks.result, re.MULTILINE))
    first_meaningful = _first_match_at(capture, re.compile(marks.meaningful, re.MULTILINE))
    complete = capture.total_ms
    if marks.complete:
        found = _first_match_at(capture, re.compile(marks.complete, re.MULTILINE))
        if found is not None:
            complete = found
    return {
        "first_feedback_ms": first_feedback,
        "first_result_ms": first_result,
        "first_meaningful_ms": first_meaningful,
        "completion_ms": complete,
        "exit_ms": capture.total_ms,
        "updates": len(visible_events),
        "lines": len(lines),
        "longest_silence_ms": round(longest_silence, 2),
        "exit_code": capture.exit_code,
        "timed_out": capture.timed_out,
        "text_chars": len(capture.text),
        "visible_text": capture.text,
        "stdin_writes": [
            [at, data.decode("utf-8", "replace")] for at, data in capture.stdin_writes
        ],
    }


# --------------------------------------------------------------------------- #
# environments + fixtures                                                     #
# --------------------------------------------------------------------------- #


#: When set, the tree under measurement is this ``src/`` directory: it is pinned
#: through ``PYTHONPATH`` (which beats the repo's editable install), so a sibling
#: block editing the working tree cannot change the numbers mid-run. The resolved
#: module path is probed and recorded with the result.
TREE_SRC: Path | None = None


def base_env(home: Path, *, provider: ScriptedProvider | None = None) -> dict[str, str]:
    """A pinned environment: no leaked PYTHONPATH, no ambient provider, no host home."""
    env = {
        "PATH": f"{REPO / '.venv' / 'bin'}:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", "/tmp"),
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": os.environ.get("TMPDIR", tempfile.gettempdir()),
        "SKALDIR_HOME": str(home),
        "GODSV_HOME": str(home),
        "NO_COLOR": "",
    }
    if TREE_SRC is not None:
        env["PYTHONPATH"] = str(TREE_SRC)
    if provider is not None:
        env.update(
            {
                "SKALDIR_LLM_BASE_URL": provider.base_url,
                "SKALDIR_LLM_MODEL": "stub-model",
                "SKALDIR_LLM_API_KEY": "stub-key",
                "SKALDIR_LLM_STREAM": "0",
                "SKALDIR_LLM_MAX_RETRIES": "0",
                "SKALDIR_LLM_TIMEOUT": "60",
            }
        )
    return env


def cli(*args: str) -> list[str]:
    return [str(SKAL), *args]


def ensure_cli() -> None:
    if not SKAL.exists():
        raise SystemExit(f"no CLI at {SKAL} — run this from the repo with .venv present")


def prep_question_workspace(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "sample.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    (root / "notes.md").write_text("# notes\n\nthree files in here.\n", encoding="utf-8")
    return root


def prep_edit_workspace(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "sample.py").write_text(
        "def greet(name):\n    return f'hi {name}'\n\n\ndef other():\n    return greet('x')\n",
        encoding="utf-8",
    )
    return root


TEST_FIX_CALC = "def add(a, b):\n    return a - b\n"
TEST_FIX_TEST = (
    "from calc import add\n\n\n"
    "def test_add():\n"
    "    assert add(2, 2) == 4\n"
)


def prep_test_fix_workspace(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "calc.py").write_text(TEST_FIX_CALC, encoding="utf-8")
    tests = root / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_math.py").write_text(TEST_FIX_TEST, encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# the journeys                                                                #
# --------------------------------------------------------------------------- #


@dataclass
class JourneyResult:
    key: str
    run: int
    metrics: dict[str, Any]
    provider_calls: list[dict[str, Any]]
    notes: dict[str, Any] = field(default_factory=dict)
    events: list[tuple[float, bytes]] = field(default_factory=list)


def journey_session_start(run: int, work: Path, *, delay_ms: int) -> JourneyResult:
    """1 — start a session: `skaldir shell`, until the prompt is usable."""
    home = work / f"home-{run}"
    home.mkdir(parents=True, exist_ok=True)
    env = base_env(home)
    capture = run_pty(
        cli("shell", "--limit", "0"),
        cwd=work,
        env=env,
        interactions=[Interaction(send=b"quit\n", when_pattern=b"ready:")],
        timeout_s=60,
    )
    marks = Marks(
        result=r"Skaldir v[\d.]+ — interactive shell",
        # The shell's prompt on a real terminal is the framed composer's own row
        # (``chatbar`` owns the input line; `repl.read_line()` delegates to it), so the old
        # ``skaldir\s*❯`` mark measured nothing at all — this journey reported
        # ``first content = —`` for a prompt that was right there on the screen.
        meaningful=r"^  ❯",
        complete=r"^  ❯",
    )
    data = metrics_for(capture, marks)
    return JourneyResult(
        "session-start", run, data, [], {"home": str(home)}, list(capture.events)
    )


def journey_question(run: int, work: Path, *, delay_ms: int) -> JourneyResult:
    """2 — a question that needs one tool call (builtin tools)."""
    home = work / f"home-{run}"
    home.mkdir(parents=True, exist_ok=True)
    workspace = prep_question_workspace(work / f"ws-{run}")
    provider = ScriptedProvider(
        steps=[
            {"tool": "list_dir", "arguments": {"path": "."}},
            {"text": "Three files: sample.py, notes.md — and that is all of them."},
        ],
        delay_ms=delay_ms,
    )
    try:
        env = base_env(home, provider=provider)
        capture = run_pty(
            cli(
                "agent", "run", "which files are in this workspace?",
                "--workspace", str(workspace), "--tools", "builtin", "--max-steps", "4",
            ),
            cwd=workspace,
            env=env,
            timeout_s=120,
        )
    finally:
        calls = list(provider.requests)
        provider.stop()
    marks = Marks(
        result=r"^run \S+ · answered",
        meaningful=r"Three files: sample\.py, notes\.md",
        complete=r"^  transcript:",
    )
    data = metrics_for(capture, marks)
    return JourneyResult(
        "question", run, data, calls, {"workspace": str(workspace)}, list(capture.events)
    )


def journey_edit(run: int, work: Path, *, delay_ms: int) -> JourneyResult:
    """3 — ask for an edit: the model reads the file, writes it, answers."""
    home = work / f"home-{run}"
    home.mkdir(parents=True, exist_ok=True)
    workspace = prep_edit_workspace(work / f"ws-{run}")
    changed = (
        "def greet(name):\n    return f'hello {name}'\n\n\ndef other():\n    return greet('x')\n"
    )
    provider = ScriptedProvider(
        steps=[
            {"tool": "read_text", "arguments": {"path": "sample.py"}},
            {"tool": "write_text", "arguments": {"path": "sample.py", "text": changed}},
            {"text": "Changed the greeting in sample.py from 'hi' to 'hello'."},
        ],
        delay_ms=delay_ms,
    )
    try:
        env = base_env(home, provider=provider)
        capture = run_pty(
            cli(
                "agent", "run", "change the greeting in sample.py from hi to hello",
                "--workspace", str(workspace), "--tools", "builtin", "--max-steps", "6",
            ),
            cwd=workspace,
            env=env,
            timeout_s=120,
        )
    finally:
        calls = list(provider.requests)
        provider.stop()
    on_disk = (workspace / "sample.py").read_text(encoding="utf-8")
    marks = Marks(
        result=r"^run \S+ · answered",
        meaningful=r"Changed the greeting in sample\.py",
        complete=r"^  transcript:",
    )
    data = metrics_for(capture, marks)
    return JourneyResult(
        "edit", run, data, calls,
        {"workspace": str(workspace), "edit_landed": on_disk == changed},
        list(capture.events),
    )


def journey_test_fix(run: int, work: Path, *, delay_ms: int) -> JourneyResult:
    """4 — the test-fix loop over the registry toolset (terminal + patch)."""
    home = work / f"home-{run}"
    home.mkdir(parents=True, exist_ok=True)
    workspace = prep_test_fix_workspace(work / f"ws-{run}")
    env = base_env(home)
    subprocess.run(  # noqa: S603 — the product publishing its own core toolset
        cli("tools-core", "register"),
        cwd=workspace,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    provider = ScriptedProvider(
        steps=[
            {"tool": "terminal", "arguments": {"command": "python -m pytest -q", "timeout_s": 60}},
            {"tool": "read_file", "arguments": {"path": "calc.py"}},
            {"tool": "patch", "arguments": {
                "path": "calc.py", "old_string": "return a - b", "new_string": "return a + b"}},
            {"tool": "terminal", "arguments": {"command": "python -m pytest -q", "timeout_s": 60}},
            {"text": "Fixed add() in calc.py (a - b → a + b); the test passes now."},
        ],
        delay_ms=delay_ms,
    )
    try:
        env_llm = base_env(home, provider=provider)
        capture = run_pty(
            cli(
                "agent", "run", "run the tests and fix the failing one",
                "--workspace", str(workspace), "--tools", "registry", "--max-steps", "8",
            ),
            cwd=workspace,
            env=env_llm,
            timeout_s=180,
        )
    finally:
        calls = list(provider.requests)
        provider.stop()
    fixed = "return a + b" in (workspace / "calc.py").read_text(encoding="utf-8")
    marks = Marks(
        result=r"^run \S+ · answered",
        meaningful=r"Fixed add\(\) in calc\.py",
        complete=r"^  transcript:",
    )
    data = metrics_for(capture, marks)
    return JourneyResult(
        "test-fix", run, data, calls, {"workspace": str(workspace), "fix_landed": fixed},
        list(capture.events),
    )


def journey_resume(run: int, work: Path, *, delay_ms: int) -> JourneyResult:
    """5 — resume after a restart: the owner process is SIGKILLed, then resumed."""
    home = work / f"home-{run}"
    home.mkdir(parents=True, exist_ok=True)
    env = base_env(home)
    created = subprocess.run(  # noqa: S603 — build the history the real way
        cli("session", "new", "--title", "perf", "--json"),
        cwd=work, env=env, capture_output=True, text=True, check=False,
    )
    session_id = ""
    with _suppress():
        session_id = json.loads(created.stdout)["data"]["created"]
    for index in range(12):
        role = "user" if index % 2 == 0 else "assistant"
        subprocess.run(  # noqa: S603
            cli(
                "session", "append", "--session", session_id, "--role", role,
                "--content", f"turn {index + 1}: the perceived-speed audit session history",
                "--json",
            ),
            cwd=work, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    # a real restart: hold the session in a shell, then kill that process outright
    holder = subprocess.Popen(  # noqa: S603
        cli("shell", "--limit", "0"),
        cwd=work, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    time.sleep(1.0)
    with _suppress():
        os.killpg(holder.pid, signal.SIGKILL)
    holder.wait(timeout=10)
    capture = run_pty(
        cli("session", "resume", "--session", session_id),
        cwd=work,
        env=env,
        timeout_s=60,
    )
    marks = Marks(
        result=r"^resumed \S+",
        meaningful=r"the perceived-speed audit session history",
    )
    data = metrics_for(capture, marks)
    return JourneyResult(
        "resume", run, data, [],
        {"session_id": session_id, "messages": 12},
        list(capture.events),
    )


JOURNEYS: dict[str, Callable[..., JourneyResult]] = {
    "session-start": journey_session_start,
    "question": journey_question,
    "edit": journey_edit,
    "test-fix": journey_test_fix,
    "resume": journey_resume,
}


# --------------------------------------------------------------------------- #
# journey 2 over the real HTTP API                                            #
# --------------------------------------------------------------------------- #


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _api_body(workspace: Path, *, stream: bool = False) -> bytes:
    """The request body for ``POST /cli/agent/run`` (argument **dest** names)."""
    payload: dict[str, Any] = {
        "task": "which files are in this workspace?",
        "workspace": str(workspace),
        "tools": "builtin",
        "max_steps": 4,
    }
    if stream:
        payload["_sse"] = True
    return json.dumps(payload).encode()


def _http_post(
    url: str, body: bytes, token: str, *, accept: str = "application/json", timeout: float = 120.0
) -> dict[str, Any]:
    """One POST, timed the way a client feels it: headers, first bytes, first answer, done."""
    request = urllib.request.Request(  # noqa: S310 — loopback only
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": accept,
            "x-godsv-token": token,
        },
        method="POST",
    )
    started = time.monotonic()
    out: dict[str, Any] = {"status": None, "chunks": [], "frames": []}
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            out["status"] = response.status
            out["http_headers_ms"] = round((time.monotonic() - started) * 1000, 2)
            first_byte = None
            for raw in response:
                now = round((time.monotonic() - started) * 1000, 2)
                if first_byte is None:
                    first_byte = now
                out["chunks"].append([now, len(raw)])
                if accept == "text/event-stream":
                    text = raw.decode("utf-8", "replace").strip()
                    if text.startswith("event:"):
                        out["frames"].append([now, text.splitlines()[0].split(":", 1)[1].strip()])
                    if '"answer"' in text and out.get("answer_ms") is None:
                        out["answer_ms"] = now
            out["http_first_byte_ms"] = first_byte
    except urllib.error.HTTPError as exc:
        out["status"] = exc.code
        out["error_body"] = exc.read().decode("utf-8", "replace")[:600]
    except Exception as exc:  # noqa: BLE001 — a transport failure is a measurement, not a crash
        out["error"] = f"{type(exc).__name__}: {exc}"
    out["http_completion_ms"] = round((time.monotonic() - started) * 1000, 2)
    out["http_chunks"] = len(out["chunks"])
    return out


def measure_api_question(run: int, work: Path, *, delay_ms: int) -> dict[str, Any]:
    """The same question through `skaldir serve --http`: real API, same stub.

    Two variants, because the API surface offers two: a plain JSON POST (what a
    script does) and the SSE stream (``Accept: text/event-stream``), which the
    server answers with ``begin``/``progress``/``end`` frames plus heartbeats.
    """
    home = work / f"api-home-{run}"
    home.mkdir(parents=True, exist_ok=True)
    workspace = prep_question_workspace(work / f"api-ws-{run}")
    provider = ScriptedProvider(
        steps=[
            {"tool": "list_dir", "arguments": {"path": "."}},
            {"text": "Three files: sample.py, notes.md — and that is all of them."},
        ],
        delay_ms=delay_ms,
    )
    port = free_port()
    token = "perf-audit-token"
    token_file = home / "token"
    token_file.write_text(token + "\n", encoding="utf-8")
    token_file.chmod(0o600)
    env = base_env(home, provider=provider)
    server = subprocess.Popen(  # noqa: S603
        cli("serve", "--http", "--port", str(port), "--token-file", str(token_file)),
        cwd=workspace, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True,
    )
    result: dict[str, Any] = {"port": port, "ready": False}
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(  # noqa: S310 — loopback only
                    f"http://127.0.0.1:{port}/cli/health", timeout=2
                ) as response:
                    if response.status < 500:
                        result["ready"] = True
                        break
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    result["ready"] = True
                    break
            except Exception:
                time.sleep(0.2)
        if not result["ready"]:
            result["error"] = "the API server did not come up"
            return result
        url = f"http://127.0.0.1:{port}/cli/agent/run"
        result["plain"] = _http_post(url, _api_body(workspace), token)
        result["sse"] = _http_post(
            url, _api_body(workspace, stream=True), token, accept="text/event-stream"
        )
        result["provider_calls"] = len(provider.requests)
    finally:
        with _suppress():
            server.terminate()
            server.wait(timeout=10)
        provider.stop()
    return result


# --------------------------------------------------------------------------- #
# the audit                                                                   #
# --------------------------------------------------------------------------- #


def tree_report(tree_src: Path | None) -> dict[str, Any]:
    """What tree is actually being measured: the resolved module and its hashes.

    A leaked ``PYTHONPATH`` (or a sibling checkout) is the classic way to measure
    the wrong code, so the answer is recorded next to the numbers, not assumed.
    """
    source = tree_src if tree_src is not None else REPO / "src"
    env = {**os.environ}
    env["PYTHONPATH"] = str(source)
    probe = subprocess.run(  # noqa: S603
        [str(PYTHON), "-c", "import skaldir; print(skaldir.__file__)"],
        env=env, capture_output=True, text=True, check=False, cwd="/tmp",
    )
    hashes = {}
    for name in ("cli.py", "agent_loop.py", "agent_loop_cli.py", "api_server.py", "repl.py"):
        path = source / "skaldir" / name
        if path.is_file():
            hashes[name] = __import__("hashlib").sha256(path.read_bytes()).hexdigest()[:16]
    return {
        "src": str(source),
        "module_file": probe.stdout.strip() or probe.stderr.strip().splitlines()[-1:],
        "module_hashes": hashes,
        "snapshot": str(tree_src) if tree_src is not None else "the working tree",
    }


def _summarise(values: Iterable[float | int | None]) -> dict[str, Any]:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return {"n": 0, "p50": None, "min": None, "max": None}
    return {
        "n": len(numbers),
        "p50": round(statistics.median(numbers), 2),
        "min": round(min(numbers), 2),
        "max": round(max(numbers), 2),
    }


#: The felt-latency gate (``--budget``): what a *user* must not have to wait for. Deliberately
#: generous — it catches a regression in feel (a command that went silent again), not a slower
#: machine. Only the journeys the progress renderer owns are gated.
FELT_BUDGETS: dict[str, float] = {
    "first_feedback_ms": 750.0,
    "longest_silence_ms": 2000.0,
}
BUDGET_JOURNEYS = ("question", "edit", "test-fix")


def budget_violations(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Every measured journey that broke a felt-latency budget (p50 over the runs)."""
    out: list[dict[str, Any]] = []
    for key in BUDGET_JOURNEYS:
        entry = document["journeys"].get(key)
        if not entry:
            continue
        for field_name, limit in FELT_BUDGETS.items():
            value = entry["summary"].get(field_name, {}).get("p50")
            if value is None or float(value) > limit:
                out.append(
                    {
                        "journey": key,
                        "metric": field_name,
                        "value": value,
                        "budget_ms": limit,
                    }
                )
    return out


METRIC_FIELDS = (
    "first_feedback_ms",
    "first_result_ms",
    "first_meaningful_ms",
    "completion_ms",
    "exit_ms",
    "updates",
    "lines",
    "longest_silence_ms",
)


def run_audit(
    *,
    label: str,
    runs: int,
    out_dir: Path,
    only: Sequence[str],
    delay_ms: int,
    with_api: bool,
    tree: Path | None = None,
    budget: bool = False,
) -> dict[str, Any]:
    global TREE_SRC
    ensure_cli()
    if tree is not None:
        source = tree
        if not (source / "skaldir").is_dir() and (source / "src" / "skaldir").is_dir():
            source = source / "src"
        if not (source / "skaldir").is_dir():
            raise SystemExit(
                f"--tree {tree}: no skaldir package in {tree} or {tree / 'src'} "
                "(pass the repo root or its src/ directory)"
            )
        TREE_SRC = source
    started = datetime.now(UTC).isoformat()
    load_start = list(os.getloadavg())
    work = Path(tempfile.mkdtemp(prefix=f"gvps-{label}-"))
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = {
        "label": label,
        "started_at": started,
        "repo": str(REPO),
        "stub_delay_ms": delay_ms,
        "runs": runs,
        "journeys": {},
        "api": {},
        "tree": tree_report(TREE_SRC),
        "loadavg": {"start": [round(value, 2) for value in load_start],
                    "end": [round(value, 2) for value in os.getloadavg()]},
        "environment": {
            "python": sys.version.split()[0],
            "skaldir_entry": str(SKAL),
            "host": os.uname().nodename,
            "cwd": os.getcwd(),
        },
    }
    keys = [key for key in JOURNEY_KEYS if key in only] or list(JOURNEY_KEYS)
    for key in keys:
        journey_started = time.monotonic()
        results: list[JourneyResult] = []
        for run in range(1, runs + 1):
            result = JOURNEYS[key](run, work, delay_ms=delay_ms)
            results.append(result)
            print(
                f"  [{label}] {key} run {run}/{runs}: "
                f"feedback={result.metrics['first_feedback_ms']} "
                f"meaningful={result.metrics['first_meaningful_ms']} "
                f"done={result.metrics['completion_ms']} "
                f"updates={result.metrics['updates']} exit={result.metrics['exit_code']}",
                flush=True,
            )
        blob = {
            "journey": key,
            "title": JOURNEY_TITLES[key],
            "runs": [
                {
                    "run": item.run,
                    "metrics": item.metrics,
                    "provider_calls": item.provider_calls,
                    "notes": item.notes,
                }
                for item in results
            ],
        }
        (raw_dir / f"{label}-{key}.json").write_text(
            json.dumps(blob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        # a human-readable replay of the first run, escapes stripped
        (raw_dir / f"{label}-{key}-replay.txt").write_text(
            f"# {key} — {JOURNEY_TITLES[key]} (run 1 of {runs}, {label})\n"
            f"# stub delay {delay_ms} ms per model call\n"
            f"# every write to the PTY, timestamped from launch\n\n"
            + "\n".join(
                f"[{at:>9.1f} ms] {visible(data.decode('utf-8', 'replace')).rstrip()}"
                for at, data in results[0].events
            )
            + "\n",
            encoding="utf-8",
        )
        document["journeys"][key] = {
            "title": JOURNEY_TITLES[key],
            "runs": [item.metrics for item in results],
            "summary": {
                field_name: _summarise(run[field_name] for run in
                                       (item.metrics for item in results))
                for field_name in METRIC_FIELDS
            },
            "provider_calls": {
                "p50": _summarise(len(item.provider_calls) for item in results),
                "first_call_ms": _summarise(
                    (item.provider_calls[0]["at_ms"] if item.provider_calls else None)
                    for item in results
                ),
            },
            "notes": [item.notes for item in results],
            "wall_ms": round((time.monotonic() - journey_started) * 1000, 2),
        }
        if with_api and key == "question":
            document["api"]["question"] = [
                measure_api_question(run, work, delay_ms=delay_ms) for run in range(1, runs + 1)
            ]
    (out_dir / f"metrics-{label}.json").write_text(
        json.dumps(document, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    if budget:
        document["budget"] = {
            "limits_ms": dict(FELT_BUDGETS),
            "journeys": list(BUDGET_JOURNEYS),
            "violations": budget_violations(document),
        }
        (out_dir / f"metrics-{label}.json").write_text(
            json.dumps(document, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
    (out_dir / f"table-{label}.md").write_text(render_table(document), encoding="utf-8")
    return document


# --------------------------------------------------------------------------- #
# reporting                                                                   #
# --------------------------------------------------------------------------- #


FELT_COLUMNS = (
    ("first_feedback_ms", "first feedback"),
    ("first_meaningful_ms", "first content"),
    ("completion_ms", "complete"),
    ("updates", "updates"),
)


def _cell(summary: Any, field_name: str, *, unit: str = "ms") -> str:
    """One table cell from a summary block (``{n, p50, min, max}``) or a bare number."""
    value: Any = summary
    if isinstance(value, dict):
        value = value.get(field_name)
    if isinstance(value, dict):  # a nested summary block
        value = value.get("p50")
    if value is None:
        return "—"
    number = f"{float(value):g}"
    return f"{number}{unit}" if unit else number


def render_table(document: dict[str, Any]) -> str:
    label = document["label"]
    lines = [
        f"# Perceived speed — {label}",
        "",
        f"stub delay {document['stub_delay_ms']} ms per model call · {document['runs']} run(s) "
        f"per journey · p50 shown · {document['started_at']}",
        "",
        "| journey | first feedback | first content | complete | updates | "
        "longest silence | model calls | first call at |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key in JOURNEY_KEYS:
        entry = document["journeys"].get(key)
        if not entry:
            continue
        summary = entry["summary"]
        lines.append(
            "| {title} | {fb} | {fc} | {done} | {upd} | {sil} | {calls} | {first} |".format(
                title=entry["title"],
                fb=_cell(summary, "first_feedback_ms"),
                fc=_cell(summary, "first_meaningful_ms"),
                done=_cell(summary, "completion_ms"),
                upd=_cell(summary, "updates", unit=""),
                sil=_cell(summary, "longest_silence_ms"),
                calls=_cell(entry["provider_calls"]["p50"], "p50", unit=""),
                first=_cell(entry["provider_calls"]["first_call_ms"], "p50"),
            )
        )
    lines.append("")
    if document.get("api", {}).get("question"):
        lines.append("## the same question over the HTTP API (`POST /cli/agent/run`)")
        lines.append("")
        lines.append(
            "| run | variant | first body byte | first content | complete | reads |"
            " frames | status |"
        )
        lines.append("|---|---|---:|---:|---:|---:|---:|---|")
        for index, entry in enumerate(document["api"]["question"], start=1):
            for variant in ("plain", "sse"):
                part = entry.get(variant) or {}
                lines.append(
                    f"| {index} | {variant} | {part.get('http_first_byte_ms')} | "
                    f"{part.get('answer_ms', '—')} | {part.get('http_completion_ms')} | "
                    f"{part.get('http_chunks')} | {len(part.get('frames') or [])} | "
                    f"{part.get('status')} |"
                )
        lines.append("")
    return "\n".join(lines) + "\n"


def load_document(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def compare_table(before: dict[str, Any], after: dict[str, Any]) -> str:
    lines = [
        f"# Perceived speed — before vs after ({before['label']} → {after['label']})",
        "",
        f"stub delay {before['stub_delay_ms']} ms per model call · p50 over "
        f"{before['runs']} run(s) each",
        "",
        "| journey | metric | before | after | delta |",
        "|---|---|---:|---:|---:|",
    ]
    for key in JOURNEY_KEYS:
        old = before["journeys"].get(key)
        new = after["journeys"].get(key)
        if not old or not new:
            continue
        for field_name, title in FELT_COLUMNS:
            old_value = old["summary"].get(field_name, {}).get("p50")
            new_value = new["summary"].get(field_name, {}).get("p50")
            if old_value is None or new_value is None:
                continue
            unit = "" if field_name == "updates" else " ms"
            lines.append(
                f"| {old['title']} | {title} | {old_value:g}{unit} | {new_value:g}{unit} | "
                f"{new_value - old_value:+g}{unit} |"
            )
    lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# entry point                                                                 #
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gv_perceived_speed.py",
        description="Measure the felt speed of the five journeys a Skaldir user repeats.",
    )
    parser.add_argument("--label", default="before", help="name of this measurement")
    parser.add_argument("--runs", type=int, default=3, help="runs per journey (default 3)")
    parser.add_argument("--only", default="", help="comma-separated journey keys")
    parser.add_argument("--delay-ms", type=int, default=DEFAULT_DELAY_MS,
                        help="stub cost per model call (default 400)")
    parser.add_argument(
        "--out", default="",
        help="output directory (default outputs/perceived-speed/<stamp>)",
    )
    parser.add_argument("--no-api", action="store_true", help="skip the HTTP API measurement")
    parser.add_argument(
        "--tree", default="",
        help="pin the measured tree: a repo dir or a src/ dir (default: the working tree). "
             "Use a snapshot when siblings are editing the tree.",
    )
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                        help="render the before/after table from two labels")
    parser.add_argument("--json", action="store_true", help="print the document as JSON")
    parser.add_argument(
        "--budget", action="store_true",
        help="gate the felt-latency budgets for question/edit/test-fix and exit 2 on a breach",
    )
    args = parser.parse_args(argv)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else REPO / "outputs" / "perceived-speed" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.compare:
        before_path = out_dir / f"metrics-{args.compare[0]}.json"
        after_path = out_dir / f"metrics-{args.compare[1]}.json"
        for path in (before_path, after_path):
            if not path.exists():
                parser.error(f"no measurement at {path}")
        text = compare_table(load_document(before_path), load_document(after_path))
        (out_dir / f"compare-{args.compare[0]}-{args.compare[1]}.md").write_text(
            text, encoding="utf-8"
        )
        print(text)
        return 0

    only = tuple(item.strip() for item in args.only.split(",") if item.strip())
    document = run_audit(
        label=args.label,
        runs=args.runs,
        out_dir=out_dir,
        only=only,
        delay_ms=args.delay_ms,
        with_api=not args.no_api,
        tree=Path(args.tree) if args.tree else None,
        budget=args.budget,
    )
    if args.budget:
        violations = document.get("budget", {}).get("violations") or []
        for item in violations:
            print(
                f"BUDGET: {item['journey']} · {item['metric']} = {item['value']} ms "
                f"> {item['budget_ms']:g} ms"
            )
        if violations:
            print(f"{len(violations)} felt-latency budget violation(s) in {args.label}")
    if args.json:
        print(json.dumps(document, indent=2, ensure_ascii=False, default=str))
    else:
        print()
        print((out_dir / f"table-{args.label}.md").read_text(encoding="utf-8"))
    if args.budget and (document.get("budget", {}).get("violations") or []):
        print(f"evidence: {out_dir}")
        return 2
    print(f"evidence: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
