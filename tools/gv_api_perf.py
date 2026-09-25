#!/usr/bin/env python
"""API latency harness: p50/p95 for the Skaldir HTTP API, per transport, plus the SSE stream.

Everything here is a **real** HTTP call against a **real** ``skaldir serve --http`` process on
loopback, started through the same bootstrap the API tests use (``register_cli`` before
``cli_contract.annotate``). Nothing is mocked, nothing is simulated:

* ``latency``   — p50/p95 for one transport over the three scenarios in ``MVP/56-api-latency.md``:
                  a simple read-only route, a route that runs a tool (``code.run``), and a
                  streamed agent run against a local OpenAI-compatible stub.
* ``audit``     — runs every read-only route that needs no arguments **both** ways (child process
                  and in-process) and reports whether the envelope is the same and what each path
                  costs. This is what produces ``api_server.IN_PROCESS_ALLOWLIST``.
* ``stream``    — one streamed agent run, printed as raw SSE frames with a timestamp per frame.
* ``all``       — latency for every transport, then the stream.

Reproduce (from the repo root)::

    env -u HERMES_HOME -u GODSV_HOME .venv/bin/python tools/gv_api_perf.py all --runs 12
    env -u HERMES_HOME -u GODSV_HOME .venv/bin/python tools/gv_api_perf.py audit --runs 3

Rules this harness keeps (learned in ``MVP/19-performance.md``): one scratch runtime per run
(``HERMES_HOME``/``GODSV_HOME`` are pointed at a temp dir), ``PYTHONPATH`` pins *this* source tree,
the load average is printed with every table (a busy box moves absolute numbers, not the ranking),
and the streaming rows carry the timestamp of the frame they are about — not the total.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
#: Which tree the served process imports. ``GV_API_PERF_SRC=/tmp/other-tree/src`` measures a
#: different checkout (used for the before/after runs; the harness itself always runs from here).
SRC_UNDER_TEST = Path(os.environ.get("GV_API_PERF_SRC") or SRC).resolve()

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from skaldir import api_server  # noqa: E402

#: The CLI owner's wiring, applied to the real parser (identical to tests/test_api_server_http.py).
BOOTSTRAP = """
import argparse, sys
from skaldir import api_server_cli, cli, cli_contract

_real_build_parser = cli.build_parser


def _build_parser():
    parser = _real_build_parser()
    sub = next(item for item in parser._actions
               if isinstance(item, argparse._SubParsersAction))
    api_server_cli.register_cli(sub)
    cli_contract.annotate(parser)
    return parser


cli.build_parser = _build_parser
parser = cli.build_parser()
args = parser.parse_args(sys.argv[1:])
sys.exit(cli_contract.run_command(args, args.func))
"""

TOKEN_LINE = "API token (printed once"

#: The stub provider answers this after ``--stub-delay`` seconds: one plain answer, no tool call,
#: so a run is exactly one provider round trip plus the loop's own bookkeeping.
STUB_ANSWER = "the stub provider answered"

#: The snippet ``code.run`` executes — the "route that runs a tool" scenario.
SNIPPET = "result = {'sum': 1 + 2}\nprint('hello from the sandbox')\n"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def load_average() -> str:
    try:
        one, five, fifteen = os.getloadavg()
        return f"{one:.2f} {five:.2f} {fifteen:.2f}"
    except (OSError, AttributeError):  # pragma: no cover
        return "n/a"


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile — with 5 samples, p95 is the slowest. Honest, not smoothed."""
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(fraction * len(ordered) + 0.5)) - 1))
    return ordered[index]


# --------------------------------------------------------------------------- #
# the local stub provider (the agent run's model)
# --------------------------------------------------------------------------- #


class StubProvider:
    """A deterministic, local, credential-free ``/v1/chat/completions`` endpoint."""

    def __init__(self, *, delay_s: float = 0.0, answer: str = STUB_ANSWER) -> None:
        self.delay_s = max(0.0, float(delay_s))
        self.answer = answer
        self.requests: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ARG002
                return

            def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's interface
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except ValueError:
                    body = {"_unparsed": True}
                stub.requests.append({"path": self.path, "stream": bool(body.get("stream"))})
                if stub.delay_s:
                    time.sleep(stub.delay_s)
                payload = {
                    "id": "chatcmpl-stub",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": str(body.get("model") or "stub"),
                    "choices": [{"index": 0, "finish_reason": "stop", "message": {
                        "role": "assistant", "content": stub.answer}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                }
                data = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def __enter__(self) -> StubProvider:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()


# --------------------------------------------------------------------------- #
# a real server
# --------------------------------------------------------------------------- #


class LiveServer:
    """A ``skaldir serve --http`` child process plus the requests made against it."""

    def __init__(self, *, transport: str, home: Path, extra_env: dict[str, str] | None = None,
                 sse_heartbeat_s: float = 10.0, extra_args: list[str] | None = None) -> None:
        self.transport = transport
        self.home = home
        self.port = free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.token = ""
        self.stderr_lines: list[str] = []
        self.env = dict(os.environ)
        for name in ("HERMES_HOME", "GODSV_HOME", api_server.TOKEN_ENV):
            self.env.pop(name, None)
        self.env.update({
            "PYTHONPATH": str(SRC_UNDER_TEST),
            "PYTHONUNBUFFERED": "1",
            "HERMES_HOME": str(home / "hermes"),
            "GODSV_HOME": str(home / "godsv"),
            "TMPDIR": str(home / "tmp"),
        })
        (home / "tmp").mkdir(parents=True, exist_ok=True)
        if extra_env:
            self.env.update(extra_env)
        self._openapi = home / f"openapi-{transport}.json"
        self._argv = [
            sys.executable, "-c", BOOTSTRAP, "serve", "--http", "--port", str(self.port),
            "--transport", transport, "--sse-heartbeat", str(sse_heartbeat_s),
            "--openapi", str(self._openapi), *(extra_args or []),
        ]
        self._proc: subprocess.Popen | None = None
        self._stdout_handle = None

    def start(self, *, timeout_s: float = 90.0) -> LiveServer:
        import httpx

        self._stdout_handle = (self.home / f"server-{self.transport}.stdout").open("w",
                                                                                  encoding="utf-8")
        self._proc = subprocess.Popen(self._argv, stdout=self._stdout_handle,
                                      stderr=subprocess.PIPE, text=True, bufsize=1,
                                      cwd=str(self.home), env=self.env)
        assert self._proc.stderr is not None

        def reader() -> None:
            for line in self._proc.stderr:  # type: ignore[union-attr]
                self.stderr_lines.append(line.rstrip("\n"))

        threading.Thread(target=reader, daemon=True).start()
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._proc.poll() is not None:
                break
            try:
                with httpx.Client(timeout=2.0) as client:
                    if client.get(f"{self.base_url}{api_server.HEALTH_PATH}").status_code == 200:
                        break
            except httpx.HTTPError:
                time.sleep(0.15)
        else:  # pragma: no cover
            self.stop()
            raise SystemExit("the server never answered /health:\n"
                             + "\n".join(self.stderr_lines[-10:]))
        for line in self.stderr_lines:
            if TOKEN_LINE in line:
                self.token = line.rsplit(": ", 1)[-1].strip()
        assert self.token, ("the server printed no token (tree under test "
                            f"{SRC_UNDER_TEST}):\n" + "\n".join(self.stderr_lines[-15:]))
        return self

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=15)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self._proc.kill()
        if self._stdout_handle is not None:
            self._stdout_handle.close()

    def __enter__(self) -> LiveServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


def write_snippet(home: Path) -> Path:
    path = home / "snippet.py"
    path.write_text(SNIPPET, encoding="utf-8")
    return path


def run_agent_http(server: LiveServer, *, stream: bool, task: str, timeout: float = 300.0):
    """One ``/cli/agent/run`` call: the raw response, plus the SSE frames with timestamps.

    Frames carry a short ``preview`` (the first 200 characters) so a table stays readable, and
    the *parsed* documents are kept separately for equality checks — nothing is dropped silently:
    ``chars`` is the real length of every payload.
    """
    import httpx

    headers = {"Authorization": f"Bearer {server.token}"}
    frames: list[dict] = []
    envelopes: list[dict] = []
    started = time.perf_counter()
    body = {"task": task}
    if stream:
        headers["Accept"] = api_server.SSE_MEDIA_TYPE

    def note(event: str, data: str) -> None:
        frames.append({"event": event, "at_ms": (time.perf_counter() - started) * 1000,
                       "chars": len(data), "preview": data[:200]})
        if event in ("progress", "end"):
            try:
                document = json.loads(data)
            except ValueError:
                document = None
            if isinstance(document, dict):
                envelopes.append(document)

    with httpx.Client(base_url=server.base_url, timeout=timeout) as client:
        if not stream:
            response = client.post("/cli/agent/run", json=body, headers=headers)
            try:
                document = response.json()
            except ValueError:
                document = {}
            return {"total_ms": (time.perf_counter() - started) * 1000, "frames": [],
                    "envelope": document, "status": response.status_code}
        with client.stream("POST", "/cli/agent/run", json=body, headers=headers) as response:
            event = ""
            data_lines: list[str] = []
            for line in response.iter_lines():
                if line.startswith("event: "):
                    event = line[len("event: "):].strip()
                elif line.startswith("data: "):
                    data_lines.append(line[len("data: "):])
                elif line.startswith(": "):
                    note("comment", line)
                elif line == "" and (event or data_lines):
                    note(event, "\n".join(data_lines))
                    event, data_lines = "", []
            if event or data_lines:  # a stream that ended without its final blank line
                note(event, "\n".join(data_lines))
    return {"total_ms": (time.perf_counter() - started) * 1000, "frames": frames,
            "envelope": envelopes[-1] if envelopes else None}


# --------------------------------------------------------------------------- #
# scenarios
# --------------------------------------------------------------------------- #


def scenario_read() -> dict:
    return {"method": "GET", "path": "/cli/contract/list", "json": None, "token": False}


def scenario_tool(home: Path) -> dict:
    return {"method": "POST", "path": "/cli/code/run",
            "json": {"file": str(write_snippet(home)), "workspace": str(home)}, "token": True}


def measure(server: LiveServer, scenario: dict, runs: int) -> dict:
    import httpx

    samples: list[float] = []
    transports: set[str] = set()
    last_status = None
    with httpx.Client(base_url=server.base_url, timeout=300.0) as client:
        for _ in range(runs):
            headers = {"Authorization": f"Bearer {server.token}"} if scenario["token"] else {}
            started = time.perf_counter()
            response = client.request(scenario["method"], scenario["path"],
                                      json=scenario["json"], headers=headers)
            samples.append((time.perf_counter() - started) * 1000)
            last_status = response.status_code
            transports.add(response.headers.get("X-Godsv-Transport", "n/a"))
    return {
        "runs": runs,
        "p50_ms": round(statistics.median(samples), 1),
        "p95_ms": round(percentile(samples, 0.95), 1),
        "min_ms": round(min(samples), 1),
        "max_ms": round(max(samples), 1),
        "samples_ms": [round(item, 1) for item in samples],
        "status": last_status,
        "transports": sorted(transports),
    }


def command_latency(args: argparse.Namespace) -> dict:
    out: dict[str, Any] = {"transport": args.transport, "runs": args.runs,
                           "load": load_average(), "scenarios": {}}
    extra_env: dict[str, str] = {}
    if args.transport == "warm":
        extra_env["SKALDIR_WARM_DIR"] = str(Path(args.workdir_root) / "warm")
    with tempfile.TemporaryDirectory(prefix="gv-api-perf-") as tmp, \
            StubProvider(delay_s=args.stub_delay) as stub:
        home = Path(tmp)
        extra_env.update({
            "SKALDIR_LLM_BASE_URL": stub.base_url,
            "SKALDIR_LLM_MODEL": "stub-model",
            "SKALDIR_LLM_API_KEY": "stub-key",
            "SKALDIR_LLM_MAX_RETRIES": "0",
            "SKALDIR_LLM_TIMEOUT": "60",
        })
        warm: subprocess.Popen | None = None
        socket_path = warm_socket_for(home)
        if args.transport == "warm":
            extra_env["SKALDIR_WARM_DIR"] = str(socket_path.parent)
            warm = start_warm_session(home, extra_env, socket_path)
        try:
            with LiveServer(transport=args.transport, home=home,
                            extra_env=extra_env, sse_heartbeat_s=args.sse_heartbeat) as server:
                out["server"] = {"base_url": server.base_url,
                                 "banner": [line for line in server.stderr_lines
                                            if line.startswith("skaldir:")][:6]}
                out["scenarios"]["read"] = measure(server, scenario_read(), args.runs)
                if args.tool_runs:
                    out["scenarios"]["tool"] = measure(server, scenario_tool(home),
                                                       args.tool_runs)
                if args.agent_runs:
                    plain_runs: list[dict] = []
                    stream_runs: list[dict] = []
                    for _ in range(max(1, args.agent_runs)):
                        plain_runs.append(run_agent_http(server, stream=False, task=args.task))
                        stream_runs.append(run_agent_http(server, stream=True, task=args.task))
                    out["scenarios"]["agent"] = agent_summary(plain_runs, stream_runs)
                out["scenarios"]["health"] = measure(
                    server, {"method": "GET", "path": api_server.HEALTH_PATH, "json": None,
                             "token": False}, args.runs)
        finally:
            if warm is not None:
                stop_warm_session(socket_path)
    return out


def warm_socket_for(home: Path) -> Path:
    """The session socket the warm transport mode uses (a private dir under the run's scratch)."""
    return home / "warm" / "warm.sock"


def start_warm_session(home: Path, env: dict[str, str], socket_path: Path) -> subprocess.Popen:
    """One long-lived warm session (``MVP/26``) for the keep-alive transport.

    The socket path is passed explicitly to every caller: the harness process' own environment is
    not the served process' environment, so relying on the default path would poll the wrong socket
    (measured: a 60 s wait for a session that was already answering).
    """
    environment = dict(env)
    environment["PYTHONPATH"] = str(SRC_UNDER_TEST)
    environment["SKALDIR_WARM_DIR"] = str(socket_path.parent)
    log = (home / "warm.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-c", BOOTSTRAP, "warm", "start", "--no-parser-cache"],
        stdout=log, stderr=subprocess.STDOUT, text=True, cwd=str(home), env=environment,
    )
    from skaldir import warm_session

    deadline = time.time() + 60
    while time.time() < deadline:
        if warm_session.status_of(socket_path, timeout=1.0).running:
            return proc
        if proc.poll() is not None:
            break
        time.sleep(0.25)
    return proc


def stop_warm_session(socket_path: Path) -> None:
    from skaldir import warm_session

    try:
        warm_session.stop_session(socket_path=socket_path, force=True)
    except Exception:  # noqa: BLE001 — best effort teardown
        pass


#: Fields inside a command's ``data`` that cannot be equal between two runs of the same command.
VOLATILE_DATA_KEYS = ("run_id", "transcript_path", "duration_ms", "started_at", "session_id",
                      "finished_at", "generated_at")


def stable_data(data: Any) -> Any:
    """``data`` with the per-run identifiers dropped — what two runs must agree on."""
    if isinstance(data, dict):
        return {key: stable_data(value) for key, value in data.items()
                if key not in VOLATILE_DATA_KEYS}
    if isinstance(data, list):
        return [stable_data(item) for item in data]
    return data


def agent_summary(plain_runs: list[dict], stream_runs: list[dict]) -> dict:
    """The before/after of one long run: nothing until the end, versus a frame at transport cost."""
    plain_ms = [item["total_ms"] for item in plain_runs]
    stream_ms = [item["total_ms"] for item in stream_runs]
    begins = [next((frame["at_ms"] for frame in item["frames"] if frame["event"] == "begin"),
                   None) for item in stream_runs]
    ends = [next((frame["at_ms"] for frame in item["frames"] if frame["event"] == "end"),
                 None) for item in stream_runs]
    begins = [item for item in begins if item is not None]
    ends = [item for item in ends if item is not None]
    best = stream_runs[0]
    plain_envelope = plain_runs[0]["envelope"] or {}
    stream_envelope = best["envelope"] or {}
    return {
        "runs": len(plain_runs),
        "unstreamed_p50_ms": round(statistics.median(plain_ms), 1),
        "unstreamed_p95_ms": round(percentile(plain_ms, 0.95), 1),
        "sse_p50_ms": round(statistics.median(stream_ms), 1),
        "sse_begin_p50_ms": round(statistics.median(begins), 1) if begins else None,
        "sse_begin_p95_ms": round(percentile(begins, 0.95), 1) if begins else None,
        "sse_end_p50_ms": round(statistics.median(ends), 1) if ends else None,
        "sse_progress_frames": sum(1 for frame in best["frames"] if frame["event"] == "progress"),
        "sse_keep_alive_frames": sum(1 for frame in best["frames"]
                                     if frame["event"] == "comment"),
        "sse_end_exit_code": stream_envelope.get("exit_code"),
        "unstreamed_exit_code": plain_envelope.get("exit_code"),
        "same_terminal_envelope": bool(
            plain_envelope and stream_envelope
            and plain_envelope.get("exit_code") == stream_envelope.get("exit_code")
            and plain_envelope.get("command") == stream_envelope.get("command")
            and stable_data(plain_envelope.get("data"))
            == stable_data(stream_envelope.get("data"))),
        "volatile_keys_dropped": list(VOLATILE_DATA_KEYS),
        "frames": [{"event": frame["event"], "at_ms": round(frame["at_ms"], 1),
                    "chars": frame["chars"]} for frame in best["frames"]],
    }


def keepalive_probe(args: argparse.Namespace) -> dict:
    """Interleaved A/B: one reused connection versus a fresh connect per request.

    Interleaved (round by round) because this box is busy: drift moves both sides together, and
    ``min`` is reported beside p50 because the floor is the least noisy statistic here. Then one
    *idle* round: the same connection is left alone for ``--idle-s`` seconds and has to answer
    again — that is what the server's own keep-alive window decides, and what a UI screen does
    between two calls.
    """
    import http.client

    with tempfile.TemporaryDirectory(prefix="gv-api-keepalive-") as tmp:
        home = Path(tmp)
        with LiveServer(transport="auto", home=home,
                        extra_args=["--keep-alive", str(args.keep_alive_s)]) as server:
            reused_times: list[float] = []
            fresh_times: list[float] = []
            connection = http.client.HTTPConnection("127.0.0.1", server.port)
            for _ in range(max(2, args.runs)):
                started = time.perf_counter()
                connection.request("GET", args.path)
                connection.getresponse().read()
                reused_times.append((time.perf_counter() - started) * 1000)
                started = time.perf_counter()
                fresh = http.client.HTTPConnection("127.0.0.1", server.port)
                fresh.request("GET", args.path)
                fresh.getresponse().read()
                fresh.close()
                fresh_times.append((time.perf_counter() - started) * 1000)
            idle: dict[str, Any] = {"idle_s": args.idle_s}
            time.sleep(max(0.0, args.idle_s))
            started = time.perf_counter()
            try:
                connection.request("GET", args.path)
                connection.getresponse().read()
                idle.update({"reused_after_idle": True,
                             "ms": round((time.perf_counter() - started) * 1000, 1)})
            except Exception as exc:  # noqa: BLE001 — the refusal IS the measurement
                idle.update({"reused_after_idle": False, "error": type(exc).__name__})
            connection.close()
            out = {
                "path": args.path,
                "runs": len(reused_times),
                "keep_alive_s": args.keep_alive_s,
                "reused_p50_ms": round(statistics.median(reused_times), 1),
                "reused_p95_ms": round(percentile(reused_times, 0.95), 1),
                "reused_min_ms": round(min(reused_times), 1),
                "fresh_p50_ms": round(statistics.median(fresh_times), 1),
                "fresh_p95_ms": round(percentile(fresh_times, 0.95), 1),
                "fresh_min_ms": round(min(fresh_times), 1),
                "idle": idle,
            }
            out["delta_p50_ms"] = round(out["fresh_p50_ms"] - out["reused_p50_ms"], 1)
    pretty = (f"keep-alive A/B on {out['path']} ({out['runs']} interleaved rounds, "
              f"server keep-alive {out['keep_alive_s']}s):\n"
              f"  reused connection : p50 {out['reused_p50_ms']} ms  "
              f"p95 {out['reused_p95_ms']}  min {out['reused_min_ms']}\n"
              f"  fresh connection  : p50 {out['fresh_p50_ms']} ms  "
              f"p95 {out['fresh_p95_ms']}  min {out['fresh_min_ms']}\n"
              f"  delta p50         : {out['delta_p50_ms']} ms\n"
              f"  after {idle['idle_s']}s idle  : reused={idle.get('reused_after_idle')} "
              f"{idle.get('ms') or idle.get('error', '')}")
    print(pretty)
    return out


def concurrency_probe(args: argparse.Namespace) -> dict:
    """Does one long command block the other routes? (The event-loop question, measured.)

    Five ``GET /health`` calls are fired while one ``agent.run`` is in flight. A server that runs
    blocking command code on the event loop answers them only after the run finishes.
    """
    import httpx

    with tempfile.TemporaryDirectory(prefix="gv-api-concurrency-") as tmp, \
            StubProvider(delay_s=args.stub_delay) as stub:
        home = Path(tmp)
        extra_env = {"SKALDIR_LLM_BASE_URL": stub.base_url, "SKALDIR_LLM_MODEL": "stub-model",
                     "SKALDIR_LLM_API_KEY": "stub-key"}
        with LiveServer(transport="auto", home=home, extra_env=extra_env) as server:
            readers: list[dict] = []
            headers = {"Authorization": f"Bearer {server.token}"}
            agents: list[float] = []

            def long_run() -> None:
                started = time.perf_counter()
                with httpx.Client(base_url=server.base_url, timeout=120.0) as client:
                    client.post("/cli/agent/run", json={"task": args.task}, headers=headers)
                agents.append((time.perf_counter() - started) * 1000)

            def reader(index: int) -> None:
                time.sleep(0.25)
                with httpx.Client(base_url=server.base_url, timeout=120.0) as client:
                    started = time.perf_counter()
                    response = client.get(api_server.HEALTH_PATH)
                    readers.append({"index": index, "status": response.status_code,
                                    "ms": round((time.perf_counter() - started) * 1000, 1)})

            threads = [threading.Thread(target=long_run)]
            threads += [threading.Thread(target=reader, args=(index,))
                        for index in range(max(1, args.readers))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            slowest = max((item["ms"] for item in readers), default=0.0)
            out = {
                "agent_ms": [round(item, 1) for item in agents],
                "readers": sorted(readers, key=lambda item: item["index"]),
                "readers_max_ms": round(slowest, 1),
                "readers_p50_ms": round(statistics.median([item["ms"] for item in readers]), 1),
                "blocked": bool(slowest >= 0.8 * (agents[0] if agents else 0)),
            }
    print(json.dumps(out, indent=2))
    return out


# --------------------------------------------------------------------------- #
# the in-process audit
# --------------------------------------------------------------------------- #


def canonical(envelope: dict) -> str:
    """A comparison key: the envelope with the transport's own timing dropped."""
    trimmed = {key: value for key, value in envelope.items()
               if key not in ("duration_ms", "started_at")}
    return json.dumps(trimmed, sort_keys=True, default=str)


def sample_values(route) -> dict | None:
    """Argument values for the audit, or ``None`` when the route cannot be driven blind.

    A single required string positional gets a deliberately absent sentinel: the command still
    walks its whole path and answers an envelope (``not_found``), which is exactly what the
    comparison needs. Anything else — a required integer, two positionals, a credential — is
    skipped and named in the output rather than guessed at.
    """
    values: dict = {}
    for spec in route.args:
        if not spec["required"]:
            continue
        if not spec["positional"] or spec["type"] != "string":
            return None
        values[spec["dest"]] = "gv-api-audit-absent"
    return values


def audit(args: argparse.Namespace) -> dict:
    """Every driveable read-only route, both transports, side by side."""
    routes = [route for route in api_server.routes()
              if not route.mutates and not route.requires_credentials]
    rows: list[dict] = []
    skipped: list[dict] = []
    for route in routes:
        values = sample_values(route)
        if values is None:
            skipped.append({"command": route.command,
                            "reason": "needs an argument the audit cannot invent"})
            continue
        argv = [*route.argv, *[item for pair in values.items() for item in pair], "--json"] \
            if values else [*route.argv, "--json"]
        verdict, reason = api_server.in_process_verdict(route.command, route)
        row: dict[str, Any] = {"command": route.command, "allowed": verdict, "reason": reason,
                               "sampled": bool(values)}
        spawn_times: list[float] = []
        inproc_times: list[float] = []
        spawn_env: str | None = None
        inproc_env: str | None = None
        for _ in range(max(1, args.runs)):
            result = api_server.run_cli(argv, command=route.command, route=route, timeout_s=90)
            spawn_times.append(result.duration_ms)
            spawn_env = canonical(result.envelope)
            try:
                fast = api_server.run_cli_inprocess(argv, command=route.command, route=route)
                inproc_times.append(int(fast.duration_ms or 0))
                inproc_env = canonical(fast.envelope)
            except api_server.TransportUnavailable as exc:
                row["in_process_error"] = exc.reason
                inproc_env = None
                break
        row["spawn_p50_ms"] = round(statistics.median(spawn_times), 1)
        row["in_process_p50_ms"] = (round(statistics.median(inproc_times), 1)
                                    if inproc_times else None)
        row["same_envelope"] = bool(spawn_env is not None and spawn_env == inproc_env)
        row["exit_code"] = json.loads(spawn_env or "{}").get("exit_code")
        rows.append(row)
        print(f"  {route.command:28s} spawn {row['spawn_p50_ms']:7.1f} ms  "
              f"in-process {row['in_process_p50_ms']:7.1f} ms  "
              f"same={row['same_envelope']} allowed={row['allowed']}", file=sys.stderr)
    identical = [row["command"] for row in rows if row["same_envelope"]]
    slower = [row["command"] for row in rows
              if row["same_envelope"] and row["in_process_p50_ms"] is not None
              and row["in_process_p50_ms"] > api_server.IN_PROCESS_BUDGET_MS]
    return {
        "checked": len(rows),
        "skipped": skipped,
        "same_envelope": len(identical),
        "candidate_allowlist": sorted(
            row["command"] for row in rows
            if row["same_envelope"] and row["in_process_p50_ms"] is not None
            and row["in_process_p50_ms"] <= api_server.IN_PROCESS_BUDGET_MS),
        "same_but_over_budget_ms": slower,
        "budget_ms": api_server.IN_PROCESS_BUDGET_MS,
        "rows": rows,
    }


# --------------------------------------------------------------------------- #
# the stream, frame by frame
# --------------------------------------------------------------------------- #


def stream_run(args: argparse.Namespace) -> dict:
    extra_env = {
        "SKALDIR_LLM_BASE_URL": "", "SKALDIR_LLM_MODEL": "stub-model",
        "SKALDIR_LLM_API_KEY": "stub-key",
    }
    with tempfile.TemporaryDirectory(prefix="gv-api-stream-") as tmp, \
            StubProvider(delay_s=args.stub_delay) as stub:
        home = Path(tmp)
        extra_env["SKALDIR_LLM_BASE_URL"] = stub.base_url
        with LiveServer(transport="spawn", home=home, extra_env=extra_env,
                        sse_heartbeat_s=args.sse_heartbeat) as server:
            result = run_agent_http(server, stream=True, task=args.task)
    print(json.dumps({"base_url": server.base_url, "started": True,
                      "frames": result["frames"], "total_ms": round(result["total_ms"], 1)},
                     indent=2))
    return {"frames": result["frames"], "total_ms": result["total_ms"]}


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #


def table(result: dict) -> str:
    lines = [f"transport={result['transport']}  runs={result['runs']}  load={result['load']}",
             f"{'scenario':10s} {'p50 ms':>9s} {'p95 ms':>9s} {'min':>8s} {'max':>8s}  "
             f"{'status':>6s}  transport(s)"]
    for name, row in result["scenarios"].items():
        if "p50_ms" in row:
            lines.append(f"{name:10s} {row['p50_ms']:>9.1f} {row['p95_ms']:>9.1f} "
                         f"{row['min_ms']:>8.1f} {row['max_ms']:>8.1f}  "
                         f"{str(row.get('status')):>6s}  {','.join(row['transports'])}")
        else:
            lines.append(
                f"{name:10s} unstreamed p50 {row['unstreamed_p50_ms']:>7.1f} ms -> SSE begin p50 "
                f"{row['sse_begin_p50_ms']} ms (p95 {row['sse_begin_p95_ms']}), end p50 "
                f"{row['sse_end_p50_ms']} ms, {row['sse_progress_frames']} progress, "
                f"{row['sse_keep_alive_frames']} keep-alive, exits "
                f"{row['unstreamed_exit_code']}/{row['sse_end_exit_code']}, "
                f"same terminal envelope={row['same_terminal_envelope']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Skaldir API latency harness (MVP/56)")
    parser.add_argument("mode", choices=("latency", "audit", "stream", "keepalive", "concurrency",
                                         "all"))
    parser.add_argument("--transport", default="auto", choices=list(api_server.TRANSPORTS))
    parser.add_argument("--path", default="/cli/contract/list",
                        help="the route the keep-alive A/B hammers")
    parser.add_argument("--readers", type=int, default=5,
                        help="concurrent /health calls during one long command")
    parser.add_argument("--idle-s", dest="idle_s", type=float, default=6.0,
                        help="idle seconds after which the keep-alive probe reuses its connection")
    parser.add_argument("--keep-alive-s", dest="keep_alive_s", type=float, default=30.0,
                        help="the server's HTTP keep-alive window for the keepalive mode")
    parser.add_argument("--runs", type=int, default=12, help="samples per read-only scenario")
    parser.add_argument("--tool-runs", type=int, default=5)
    parser.add_argument("--agent-runs", type=int, default=1)
    parser.add_argument("--stub-delay", type=float, default=1.5,
                        help="seconds the stub provider waits before answering")
    parser.add_argument("--sse-heartbeat", type=float, default=2.0,
                        help="SSE keep-alive interval used for the streamed run")
    parser.add_argument("--task", default="report the stub answer")
    parser.add_argument("--workdir-root", default="/tmp/gv-api-perf-warm")
    parser.add_argument("--write-json", default="")
    args = parser.parse_args(argv)

    results: dict[str, Any] = {"load": load_average(), "python": sys.version.split()[0],
                               "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if args.mode in ("latency", "all"):
        transports = ["spawn", "inprocess", "warm", "auto"] if args.mode == "all" \
            else [args.transport]
        results["latency"] = {}
        for transport in transports:
            print(f"== measuring transport={transport} ==", file=sys.stderr)
            one = command_latency(argparse.Namespace(**{**vars(args), "transport": transport}))
            results["latency"][transport] = one
            print(table(one))
    if args.mode == "audit":
        results["audit"] = audit(args)
        print(json.dumps({key: value for key, value in results["audit"].items()
                          if key != "rows"}, indent=2))
    if args.mode == "keepalive":
        results["keepalive"] = keepalive_probe(args)
    if args.mode == "concurrency":
        results["concurrency"] = concurrency_probe(args)
    if args.mode in ("stream", "all"):
        results["stream"] = stream_run(args)
    if args.write_json:
        Path(args.write_json).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"written: {args.write_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
