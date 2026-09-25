"""API latency and streaming: the SSE contract, the transports, and the measured claims.

``tests/test_api_server_http.py`` proves the *surface* (a route per command, the envelope, the
token). This file proves the two things ``MVP/56-api-latency.md`` promises about latency, and it
proves them the way the round demands — with real numbers and raw frames, never with a mock:

1. **Streaming.** ``begin``/``progress``/``end`` frames observed over real HTTP with the time each
   one arrived, including a real long run (an agent loop against a local stub provider) where the
   server has to keep the connection visible while the run says nothing. The terminal envelope is
   compared against the *unstreamed* body of the same route, so "the same envelope at the end" is
   an assertion, not a sentence.
2. **Per-request overhead.** The transport the server chose is on every response
   (``X-Godsv-Transport``); the in-process allowlist is re-proved by running each of its members
   **both ways** and comparing the envelopes byte for byte; a refused command has to name its
   reason; and one long command must not stop the other routes from answering.

The server is started through the same bootstrap the other API test uses (the CLI owner's two-line
paste applied to the real parser), because ``src/skaldir/cli.py`` is the CLI owner's file.
"""

from __future__ import annotations

import json
import os
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

#: The suite's own OpenAI-compatible double (real server, real request bodies).
from test_agent_loop import StubEndpoint, answer  # noqa: E402  # noqa: PLC2701

from skaldir import api_server, api_server_cli, cli, cli_contract, warm_session

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

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

TOKEN_LINE = re.compile(r"API token \(printed once[^)]*\): (\S+)")

#: The stub's answer; asserted on both paths so "the same result" means the same *content*.
STUB_TEXT = "the stub provider answered"
#: How long the stub waits. Long enough that a keep-alive frame must appear, short enough to keep
#: the suite quick.
STUB_DELAY_S = 1.5

# --------------------------------------------------------------------------- #
# the honest latency bound (see test_each_allowlist_member_returns_the_same_envelope_both_ways)
# --------------------------------------------------------------------------- #

#: How many times each allowlist member is measured both ways. Round 0 is the **warm-up**: the
#: first in-process call per member pays the first-touch cost of the modules that member imports,
#: and charging that to the bound would measure the import graph, not the request.
IN_PROCESS_ROUNDS = 3

#: The bound, as a **ratio to the child-process time measured in the same run**.
#:
#: It used to be an absolute 250 ms (``api_server.IN_PROCESS_BUDGET_MS``). That is a claim about
#: the machine, not about this code: measured 2026-09-19, a full-suite run failed the member
#: ``contract.dump`` at 261 ms in-process while the same member read 73-142 ms in isolation and
#: the full suite took 943 s instead of 914 s — the box was loaded. Both numbers of a ratio are
#: taken in the same run, so load moves them together and what is left is the actual claim: the
#: in-process path does not pay the process cost it exists to avoid (measured worst per-member
#: ratio on a quiet machine: 0.24, ``contract.dump``; median ratio across all members: 0.00).
#: 0.5 is double the worst measured reading, and it still bites: an in-process path that spawns
#: again, rebuilds the parser per request or grows with the user's data lands at ~1.0.
IN_PROCESS_RATIO = 0.5

#: The ratio may not be satisfied by a *slow* denominator (a machine loaded enough to make the
#: child process take 6 s would let a 3 s in-process call pass). Ten times the worst
#: quiet-machine in-process reading, so this only fires on a real regression, never on load.
IN_PROCESS_CEILING_MS = 1500


# --------------------------------------------------------------------------- #
# helpers (the bootstrap + a real server, as in tests/test_api_server_http.py)
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("GODSV_FORMAT", None)
    env.pop(api_server.TOKEN_ENV, None)
    env["PYTHONPATH"] = str(SRC)
    env["HERMES_HOME"] = str(home / "hermes")
    env["GODSV_HOME"] = str(home / "godsv")
    env["TMPDIR"] = str(home / "tmp")
    env["PYTHONUNBUFFERED"] = "1"
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    return env


class Bootstrap:
    """A ``skaldir`` invocation through the bootstrap (own process, own runtime home)."""

    def __init__(self, home: Path, extra_env: dict[str, str] | None = None) -> None:
        self.home = home
        self.env = _env(home)
        if extra_env:
            self.env.update(extra_env)

    def popen(self, argv: list[str], *, stdout_path: Path) -> subprocess.Popen:
        self.stdout_handle = stdout_path.open("w", encoding="utf-8")
        return subprocess.Popen(
            [sys.executable, "-c", BOOTSTRAP, *argv],
            stdout=self.stdout_handle, stderr=subprocess.PIPE, text=True, bufsize=1,
            cwd=str(self.home), env=self.env,
        )


#: How long one streamed request may take before the *test* fails instead of waiting.
#:
#: ``httpx``'s ``timeout`` is a **per-read** timeout, so a server that keeps writing frames — and
#: ``--sse-heartbeat`` exists to keep writing keep-alive comment frames — resets it forever. A
#: stream that never ends therefore never times out: measured 2026-09-20, a full-suite run sat in
#: one streamed read for the better part of sixteen minutes with no output, no failure and no
#: bound, and the main thread was in ``httpcore/_backends/sync.py`` when the run was interrupted.
#: Sixty seconds is ~15x the longest legitimate stream here (the agent run against the stub, which
#: answers after ``STUB_DELAY_S``): a stream has to *end*, and a test that waits for one has to
#: say how long it will wait.
STREAM_BUDGET_S = 60.0


class LiveServer:
    """A running ``serve --http`` process plus what this file observed on the wire."""

    def __init__(self, base_url: str, token: str, stderr_lines: list[str]) -> None:
        self.base_url = base_url
        self.token = token
        self.stderr_lines = stderr_lines
        self.index = 0

    def request(self, method: str, path: str, *, token: bool = False, **kwargs: Any) -> Any:
        import httpx

        headers = dict(kwargs.pop("headers", {}) or {})
        if token:
            headers["Authorization"] = f"Bearer {self.token}"
        with httpx.Client(base_url=self.base_url, timeout=120.0) as client:
            return client.request(method, path, headers=headers, **kwargs)

    def stream(
        self,
        method: str,
        path: str,
        *,
        token: bool = False,
        budget_s: float = STREAM_BUDGET_S,
        **kwargs: Any,
    ):
        """A streaming request, plus the (frame, elapsed_ms) pairs as they arrive.

        Bounded twice over, because one bound is not enough for a stream: the client's read
        timeout catches *silence* (nothing arriving for ``budget_s``), and the per-frame check
        catches a stream that keeps talking without ever ending — the case ``httpx`` alone
        cannot catch, since every frame resets its clock.
        """
        import httpx

        headers = dict(kwargs.pop("headers", {}) or {})
        if token:
            headers["Authorization"] = f"Bearer {self.token}"
        started = time.perf_counter()
        budget_s = max(1.0, float(budget_s))
        deadline = started + budget_s
        client = httpx.Client(base_url=self.base_url, timeout=budget_s)
        with client.stream(method, path, headers=headers, **kwargs) as response:
            frames: list[tuple[str, float]] = []
            for line in response.iter_lines():
                frames.append((line, (time.perf_counter() - started) * 1000.0))
                if time.perf_counter() > deadline:
                    raise AssertionError(
                        f"the stream for {method} {path} did not end within {budget_s:g}s: "
                        f"{len(frames)} frame(s) arrived and the server kept the connection "
                        "open — a stream that never ends is not a slow stream"
                    )
        client.close()
        return response, frames, (time.perf_counter() - started) * 1000.0


def sse_events(frames: list[tuple[str, float]]) -> list[dict]:
    """Parse SSE lines into ``{event, id, data, at_ms}`` records (comments included)."""
    events: list[dict] = []
    current: dict[str, Any] = {"event": "", "id": None, "data": []}
    for line, at_ms in frames:
        if line.startswith("event: "):
            current["event"] = line[len("event: "):].strip()
        elif line.startswith("id: "):
            current["id"] = line[len("id: "):].strip()
        elif line.startswith("retry: "):
            current["retry"] = line[len("retry: "):].strip()
        elif line.startswith(": "):
            events.append({"event": "comment", "text": line, "at_ms": at_ms})
        elif line.startswith("data: "):
            current["data"].append(line[len("data: "):])
        elif line == "" and (current["event"] or current["data"]):
            current["data"] = "\n".join(current["data"])
            current["at_ms"] = at_ms
            events.append(current)
            current = {"event": "", "id": None, "data": []}
    return events


def envelopes_of(events: list[dict]) -> list[dict]:
    return [json.loads(item["data"]) for item in events
            if item["event"] in ("progress", "end") and item.get("data")]


def start_server(bootstrap: Bootstrap, extra: list[str], *, name: str) -> tuple:
    import httpx

    port = _free_port()
    stderr_lines: list[str] = []
    process = bootstrap.popen(
        ["serve", "--http", "--port", str(port), *extra],
        stdout_path=bootstrap.home / f"{name}.stdout",
    )

    def reader() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            stderr_lines.append(line.rstrip("\n"))

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 90
    while time.time() < deadline:
        if process.poll() is not None:
            break
        try:
            with httpx.Client(timeout=2.0) as client:
                if client.get(f"{base_url}{api_server.HEALTH_PATH}").status_code == 200:
                    break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:  # pragma: no cover
        process.kill()
        raise AssertionError("the server never answered /health:\n"
                            + "\n".join(stderr_lines[-15:]))
    token = ""
    for line in stderr_lines:
        match = TOKEN_LINE.search(line)
        if match:
            token = match.group(1)
    assert token, "the server printed no token:\n" + "\n".join(stderr_lines[-10:])
    return LiveServer(base_url, token, stderr_lines), process, thread


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("api-latency")


@pytest.fixture(scope="module")
def stub() -> Iterator[StubEndpoint]:
    """A real ``/v1/chat/completions`` endpoint that waits, so a run is genuinely long."""

    def slow(_body: dict) -> dict:
        time.sleep(STUB_DELAY_S)
        return answer(STUB_TEXT)

    with StubEndpoint([slow]) as endpoint:
        yield endpoint


@pytest.fixture(scope="module")
def llm_env(stub: StubEndpoint) -> dict[str, str]:
    return {
        "SKALDIR_LLM_BASE_URL": stub.base_url,
        "SKALDIR_LLM_MODEL": "stub-model",
        "SKALDIR_LLM_API_KEY": "stub-key",
        "SKALDIR_LLM_MAX_RETRIES": "0",
        "SKALDIR_LLM_TIMEOUT": "60",
    }


@pytest.fixture(scope="module")
def bootstrap(home: Path, llm_env: dict[str, str]) -> Bootstrap:
    return Bootstrap(home, llm_env)


@pytest.fixture(scope="module")
def server(bootstrap: Bootstrap) -> Iterator[LiveServer]:
    """The default server: ``--transport auto``, with a short SSE heartbeat.

    ``--sse-heartbeat 0.4`` is what makes the keep-alive *observable* in a 2-second test run; the
    shipped default is 10 s (long enough for a real run, short enough that a proxy does not drop
    an idle connection).
    """
    live, process, thread = start_server(
        bootstrap, ["--transport", "auto", "--sse-heartbeat", "0.4"], name="auto")
    yield live
    process.terminate()
    process.wait(timeout=20)
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def hard_server(bootstrap: Bootstrap) -> Iterator[LiveServer]:
    """The same server with the transport pinned to the child process (the pre-change path)."""
    live, process, thread = start_server(bootstrap, ["--transport", "spawn"], name="spawn")
    yield live
    process.terminate()
    process.wait(timeout=20)
    thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# 1. the frame shapes, on their own
# --------------------------------------------------------------------------- #


def _envelope(index: int, *, exit_code: int = 0) -> dict:
    return {"schema_version": cli_contract.SCHEMA_VERSION, "ok": exit_code == 0,
            "command": "demo.run", "exit_code": exit_code, "data": {"index": index}}


def test_the_frame_stream_is_begin_progress_end_in_order() -> None:
    ticks = iter([_envelope(1), None, _envelope(2), _envelope(3)])
    text = "".join(api_server.sse_frames(
        ticks, command="demo.run", route_path="/cli/demo/run", transport="inprocess",
        transport_note="measured allowlist", heartbeat_s=1.0, api_version="x",
        schema_version=cli_contract.SCHEMA_VERSION,
    ))
    events = sse_events([(line, 0.0) for line in text.splitlines()])
    assert [item["event"] for item in events] == ["begin", "progress", "comment",
                                                  "progress", "progress", "end"]
    begin = json.loads(events[0]["data"])
    assert begin["stream_contract"] == api_server.SSE_STREAM_CONTRACT
    assert begin["events"] == list(api_server.SSE_EVENTS)
    assert begin["transport"] == "inprocess" and begin["heartbeat_s"] == 1.0
    assert events[0]["retry"] == str(api_server.DEFAULT_SSE_RETRY_MS)
    # every progress frame is one of the command's envelopes, verbatim, and the end frame repeats
    # the terminal one — so "the same envelope at the end" holds whatever happened before it
    assert [item.get("id") for item in events
            if item["event"] in ("progress", "end")] == ["1", "2", "3", "4"]
    assert json.loads(events[-1]["data"]) == _envelope(3)
    assert events[2]["text"].startswith(": keep-alive ")


def test_a_frame_is_well_formed_when_the_data_is_multiline() -> None:
    frame = api_server.sse_frame("progress", {"text": "one\ntwo"}, event_id=7)
    assert frame.startswith("id: 7\nevent: progress\n")
    assert "data: " in frame and frame.endswith("\n\n")
    # a JSON document never really contains a raw newline, but a frame must survive one anyway
    assert frame.count("\n") >= 4


def test_an_empty_stream_still_ends_with_an_envelope() -> None:
    text = "".join(api_server.sse_frames(iter([]), command="demo.run"))
    events = sse_events([(line, 0.0) for line in text.splitlines()])
    assert [item["event"] for item in events] == ["begin", "end"]
    assert json.loads(events[-1]["data"])["exit_code"] == cli_contract.EXIT_INTERNAL


# --------------------------------------------------------------------------- #
# 2. the stream over real HTTP
# --------------------------------------------------------------------------- #


def test_sse_over_http_ends_with_the_unstreamed_envelope(server: LiveServer) -> None:
    """The headline contract: the streamed terminal envelope *is* the unstreamed body."""
    plain = server.request("GET", "/cli/contract/list").json()
    response, frames, _total = server.stream(
        "GET", "/cli/contract/list", headers={"Accept": api_server.SSE_MEDIA_TYPE}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(api_server.SSE_MEDIA_TYPE)
    assert response.headers["X-Godsv-Stream-Contract"] == api_server.SSE_STREAM_CONTRACT
    assert response.headers["X-Godsv-Sse-Events"] == "begin,progress,end"
    assert response.headers["Cache-Control"] == "no-store"

    events = sse_events(frames)
    assert events[0]["event"] == "begin" and events[-1]["event"] == "end"
    assert json.loads(events[0]["data"])["route"] == "/cli/contract/list"
    progress = envelopes_of([item for item in events if item["event"] == "progress"])
    assert progress and progress[-1]["exit_code"] == 0
    terminal = envelopes_of(events)[-1]
    for field in ("exit_code", "command", "data", "ok", "schema_version", "error"):
        assert terminal[field] == plain[field], f"the stream and the body disagree on {field}"


def test_sse_can_be_asked_for_by_query_or_body(server: LiveServer) -> None:
    import httpx

    with httpx.Client(base_url=server.base_url, timeout=60.0) as client:
        by_query = client.get("/cli/contract/list?sse=1")
        assert by_query.headers["content-type"].startswith(api_server.SSE_MEDIA_TYPE)
        assert "event: begin" in by_query.text

        by_body = client.post("/cli/contract/list", json={"_sse": True})
        assert by_body.headers["content-type"].startswith(api_server.SSE_MEDIA_TYPE)
        assert "event: end" in by_body.text


def test_the_request_metadata_keys_are_not_arguments() -> None:
    route = api_server.route_for("contract.list")
    body, stream = api_server._consume_stream_flag(  # noqa: SLF001
        {"_sse": True, "sse": True, "_stream": True})
    assert body == {} and stream is True
    assert api_server.body_arguments(body) == {}
    assert api_server.build_argv(route, api_server.body_arguments(body)) == \
        api_server.build_argv(route, {})


class _HeartbeatForever(BaseHTTPRequestHandler):
    """A stream that never ends — exactly what a keep-alive frame makes possible.

    The server answers 200, then writes one comment frame forever and never closes. This is the
    shape the suite met on 2026-09-20: the frames keep coming (so ``httpx``'s per-read timeout
    never fires) and the response never ends.
    """

    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — stdlib signature
        return

    def do_GET(self) -> None:  # noqa: N802 — stdlib signature
        self.send_response(200)
        self.send_header("Content-Type", api_server.SSE_MEDIA_TYPE)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            while True:
                self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
                time.sleep(0.1)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True


def test_a_stream_that_never_ends_fails_instead_of_waiting() -> None:
    """The stall's mechanism, turned into a failure: a stream must end, or the test says so.

    ``httpx``'s timeout is per-read, so a server that keeps writing resets it — which is the
    whole purpose of ``--sse-heartbeat``. Before this bound, the only way out of an endless
    stream was to interrupt the entire run (measured: sixteen minutes standing still at 4% of a
    full-suite run, main thread inside httpcore's sync backend, no failure and no output). The
    assertion that matters here is the *time*: the bound has to bite in seconds, not minutes.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HeartbeatForever)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="endless-stream")
    thread.start()
    try:
        live = LiveServer(f"http://127.0.0.1:{server.server_address[1]}", "", [])
        started = time.perf_counter()
        with pytest.raises(AssertionError) as exc:
            live.stream("GET", "/cli/contract/list", budget_s=2.0)
        elapsed = time.perf_counter() - started
        assert "did not end within" in str(exc.value), str(exc.value)
        assert elapsed < 10.0, f"the stream budget took {elapsed:.1f}s to bite"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive(), "the endless-stream server did not shut down"


def test_a_long_agent_run_streams_while_it_runs(server: LiveServer) -> None:
    """Raw timestamps: the begin frame is immediate and keep-alives cover the silent run.

    Before this round the client saw *nothing* until the run finished (measured: the unstreamed
    call below). That difference — not a nicer format — is what the streaming transport buys.
    """
    task = "report the stub answer"
    plain = server.request("POST", "/cli/agent/run", json={"task": task}, token=True)
    assert plain.status_code == 200, plain.text
    assert plain.json()["data"]["answer"] == STUB_TEXT

    _response, frames, total_ms = server.stream(
        "POST", "/cli/agent/run", json={"task": task}, token=True,
        headers={"Accept": api_server.SSE_MEDIA_TYPE},
    )
    events = sse_events(frames)
    order = [item["event"] for item in events]
    assert order[0] == "begin" and order[-1] == "end"
    assert "progress" in order

    begin = events[0]
    comments = [item for item in events if item["event"] == "comment"]
    terminal = envelopes_of(events)[-1]
    assert begin["at_ms"] < 500, f"the begin frame arrived after {begin['at_ms']:.0f} ms"
    assert comments, "a silent run produced no keep-alive frame"
    assert terminal["exit_code"] == 0
    assert terminal["command"] == "agent.run"
    assert terminal["data"]["answer"] == STUB_TEXT
    assert terminal["data"]["status"] == "answered"
    assert total_ms >= STUB_DELAY_S * 1000, "the stub was not actually slow"
    assert begin["at_ms"] < total_ms / 2, "the begin frame arrived too late to be a stream"


def test_the_sse_transport_carries_the_child_process_note(server: LiveServer) -> None:
    _response, frames, _total = server.stream(
        "GET", "/cli/contract/list", headers={"Accept": api_server.SSE_MEDIA_TYPE}
    )
    begin = json.loads(sse_events(frames)[0]["data"])
    assert begin["transport"] == api_server.SPAWN_TRANSPORT
    assert "child process" in begin["transport_note"]


# --------------------------------------------------------------------------- #
# 3. which transport answered, and why
# --------------------------------------------------------------------------- #


def test_an_allowlisted_route_answers_from_this_process(server: LiveServer) -> None:
    response = server.request("GET", "/cli/contract/list")
    assert response.status_code == 200
    assert response.headers["X-Godsv-Transport"] == api_server.IN_PROCESS_TRANSPORT
    assert "allowlist" in response.headers["X-Godsv-Transport-Note"]
    assert response.json()["command"] == "contract.list"


def test_a_mutating_route_keeps_its_own_process(server: LiveServer) -> None:
    response = server.request("POST", "/cli/tools/request", json={"capability": "x"}, token=True)
    assert response.status_code == 200, response.text
    assert response.headers["X-Godsv-Transport"] == api_server.SPAWN_TRANSPORT
    assert "mutating" in response.headers["X-Godsv-Transport-Note"]


def test_transport_spawn_forces_the_pre_change_path(hard_server: LiveServer) -> None:
    response = hard_server.request("GET", "/cli/contract/list")
    assert response.status_code == 200
    assert response.headers["X-Godsv-Transport"] == api_server.SPAWN_TRANSPORT
    assert "forced" in response.headers["X-Godsv-Transport-Note"]


def test_health_reports_the_transport_and_its_counters(server: LiveServer) -> None:
    server.request("GET", "/cli/contract/list")
    payload = server.request("GET", api_server.HEALTH_PATH).json()["data"]
    assert payload["transport"] == api_server.DEFAULT_TRANSPORT
    assert payload["transports_available"] == list(api_server.TRANSPORTS)
    assert payload["sse_stream_contract"] == api_server.SSE_STREAM_CONTRACT
    assert payload["sse_events"] == list(api_server.SSE_EVENTS)
    assert payload["in_process_allowlist"] == sorted(api_server.IN_PROCESS_ALLOWLIST)
    assert payload["transports_used"], "no transport counter moved"


# --------------------------------------------------------------------------- #
# 4. the in-process allowlist, re-proved rather than trusted
# --------------------------------------------------------------------------- #


def test_every_allowlist_member_is_a_read_only_route() -> None:
    by_command = {route.command: route for route in api_server.routes()}
    assert api_server.IN_PROCESS_ALLOWLIST, "an empty allowlist would prove nothing"
    for command in sorted(api_server.IN_PROCESS_ALLOWLIST):
        route = by_command.get(command)
        assert route is not None, f"{command} is on the allowlist but is not an HTTP route"
        assert not route.mutates, f"{command} mutates and must never run in-process"
        assert not route.requires_credentials, f"{command} needs credentials"
        assert command not in api_server.IN_PROCESS_REFUSED, f"{command} is allowed and refused"
        allowed, reason = api_server.in_process_verdict(command, route)
        assert allowed and reason


def test_every_refusal_names_a_reason() -> None:
    by_command = {route.command: route for route in api_server.routes()}
    for command, reason in sorted(api_server.IN_PROCESS_REFUSED.items()):
        assert len(reason) > 8, f"{command} has a placeholder reason: {reason!r}"
        route = by_command.get(command)
        if route is None:
            continue
        if route.mutates:
            allowed, verdict_reason = api_server.in_process_verdict(command, route)
            assert not allowed and "mutating" in verdict_reason


def test_each_allowlist_member_returns_the_same_envelope_both_ways(tmp_path: Path) -> None:
    """The measured claim, re-measured: child process versus this process, same bytes.

    Three rounds per member, because a *sometimes* identical answer is not identical: the
    audit caught three members (``runs.status``, ``spend.list``, ``spend.allowance``) that
    matched on one run and disagreed on the next, and they were moved out of the allowlist for
    it. Round 0 is the **warm-up** and is excluded from the latency verdict only (its byte
    identity is still asserted): the first in-process call per member pays the first-touch
    cost of the modules that member imports — measured on 2026-09-19, ``contract.dump`` read
    115 ms cold against 88 ms warm.

    The parser is passed explicitly (built here, with this environment) so the test does not
    depend on — or poison — the server-wide parser cache.

    **The latency bound is a ratio, not a wall clock.** See :data:`IN_PROCESS_RATIO`:
    the old absolute 250 ms budget failed in a full-suite run at 261 ms for ``contract.dump``
    while the same member read 73-142 ms alone, because the box was loaded — a wall-clock
    constant is a claim about the machine. Both numbers in a ratio are measured *in this same
    run*, so load moves them together; what is left is the thing being claimed, namely that
    the in-process path does not pay the process cost.
    """
    parser = cli.build_parser()
    fingerprint = api_server.parser_env_fingerprint()
    by_command = {route.command: route for route in api_server.routes()}
    rows: list[tuple[str, int, int, bool]] = []
    for command in sorted(api_server.IN_PROCESS_ALLOWLIST):
        route = by_command[command]
        argv = [*route.argv, "--json", "--no-color"]
        for round_index in range(IN_PROCESS_ROUNDS):
            spawn = api_server.run_cli(argv, command=command, route=route, timeout_s=120)
            fast = api_server.run_cli_inprocess(argv, command=command, route=route, parser=parser,
                                                parser_env=fingerprint)
            drop = ("duration_ms", "started_at")
            spawn_body = {key: value for key, value in spawn.envelope.items() if key not in drop}
            fast_body = {key: value for key, value in fast.envelope.items() if key not in drop}
            assert fast.exit_code == spawn.exit_code, (
                f"{command}: in-process exit {fast.exit_code}, child process exit "
                f"{spawn.exit_code} (round {round_index})")
            assert json.dumps(fast_body, sort_keys=True, default=str) == \
                json.dumps(spawn_body, sort_keys=True, default=str), (
                    f"{command}: the two paths disagree (round {round_index}) — in-process must "
                    "never be a second implementation")
            rows.append((command, spawn.duration_ms, fast.duration_ms, round_index > 0))
    # the claim, asserted per member (never on average): the warm in-process call costs less
    # than IN_PROCESS_RATIO of the child process it replaces, measured in this same run
    over = [row for row in rows if row[3] and row[2] > IN_PROCESS_RATIO * row[1]]
    assert not over, (
        "in-process members over the ratio bound "
        f"{IN_PROCESS_RATIO} of the child process: {over}\n"
        f"all rows (command, spawn_ms, fast_ms, warm): {rows}")
    # and the ratio may not be satisfied by a slow denominator: an in-process call that takes
    # seconds is broken whatever the child costs (10x the worst quiet-machine reading)
    ceiling = [row for row in rows if row[2] > IN_PROCESS_CEILING_MS]
    assert not ceiling, (
        f"in-process members over the {IN_PROCESS_CEILING_MS} ms ceiling: {ceiling}\n"
        f"the ratio is not a licence to be slow: {rows}")
    warm = [row for row in rows if row[3]]
    cold = [row for row in rows if not row[3]]
    medians = {name: statistics.median([row[2] for row in group])
               for name, group in _by_command(rows).items()}
    # the child process pays real process cost — otherwise the comparison measures nothing
    process_bound = [row for row in rows if row[1] >= 200]
    assert len(process_bound) >= len(rows) - 1, (
        "the child process was fast for too many members; the comparison is not measuring the "
        f"process cost it claims to: {rows}")
    # the numbers are the deliverable: report them, so a human can see the margin
    print(f"\nin-process latency ({IN_PROCESS_ROUNDS} rounds, round 0 = warm-up): "
          f"warm median {statistics.median([row[2] for row in warm]):.0f} ms, "
          f"worst warm {max(row[2] for row in warm)} ms; "
          f"cold median {statistics.median([row[2] for row in cold]):.0f} ms; "
          f"child median {statistics.median([row[1] for row in rows]):.0f} ms; "
          f"worst ratio {max(row[2] / row[1] for row in warm):.2f}; "
          f"worst member {max(medians, key=lambda name: medians[name])}")
    # the absolute figure the audit records is reported too — over it is not a failure here,
    # it is the number this test exists to keep honest (the loaded-run 261 ms case)
    over_budget = [row[0] for row in rows if row[2] > api_server.IN_PROCESS_BUDGET_MS]
    print(f"rows over the absolute {api_server.IN_PROCESS_BUDGET_MS} ms audit budget "
          f"(reported, not gated): {sorted(set(over_budget))}")


def _by_command(rows: list[tuple[str, int, int, bool]]) -> dict[str, list[tuple]]:
    """``command -> rows`` (a plain group-by, so the medians above are per member)."""
    grouped: dict[str, list[tuple]] = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(row)
    return grouped


def test_the_in_process_ratio_is_the_honest_bound() -> None:
    """The bound is a ratio measured in one run, and it is a bound with teeth.

    The predicate the measurement above uses, exercised on the numbers that matter: a quiet
    reading passes, an in-process path that pays the process cost again fails, a path that is
    merely slow fails on the ceiling whatever the child costs, and the warm-up round is not
    judged (it still has to return the same bytes — asserted in the measurement itself).
    """
    assert 0.0 < IN_PROCESS_RATIO < 1.0
    assert IN_PROCESS_ROUNDS >= 2, "one round is not a warm-up plus a measurement"
    assert IN_PROCESS_CEILING_MS > api_server.IN_PROCESS_BUDGET_MS, (
        "the ceiling guards a loaded machine; it may not be tighter than the quiet budget")

    def flagged(rows: list[tuple[str, int, int, bool]]) -> list[tuple]:
        return [row for row in rows if row[3] and row[2] > IN_PROCESS_RATIO * row[1]]

    # measured quiet-machine numbers (contract.dump: 570 ms child, 142 ms in-process)
    assert flagged([("contract.dump", 570, 142, True)]) == []
    # a regression that made the fast path pay the process cost again
    assert flagged([("contract.dump", 570, 480, True)]) == [("contract.dump", 570, 480, True)]
    # a member that grew with the user's data: the ratio still passes (the child grew too),
    # so the ceiling is what catches it
    assert flagged([("contract.dump", 6000, 3000, True)]) == []
    assert [row for row in [("contract.dump", 6000, 3000, True)]
            if row[2] > IN_PROCESS_CEILING_MS], "the ceiling is the guard the ratio is not"
    # the warm-up round is never the verdict
    assert flagged([("contract.dump", 570, 480, False)]) == []


def test_the_in_process_budget_is_a_real_bound() -> None:
    assert 0 < api_server.IN_PROCESS_BUDGET_MS <= 500
    assert api_server.IN_PROCESS_ALLOWLIST.isdisjoint(api_server.IN_PROCESS_REFUSED)


def test_the_parser_cache_refuses_a_changed_environment(monkeypatch) -> None:
    """A cached parser freezes what the build read — a changed environment must refuse the path."""
    route = api_server.route_for("contract.list")
    fake_env = (("HOME", "/somewhere-else"),)
    monkeypatch.setattr(api_server, "_PARSER_CACHE", (cli.build_parser(), fake_env), raising=False)
    with pytest.raises(api_server.TransportUnavailable) as exc:
        api_server.run_cli_inprocess(["contract", "list", "--json"], command="contract.list",
                                     route=route)
    assert "different environment" in exc.value.reason
    monkeypatch.setattr(api_server, "_PARSER_CACHE", None, raising=False)


def test_a_long_command_does_not_block_the_other_routes(server: LiveServer) -> None:
    """The event loop stays free while a command runs (before: every route waited for it)."""
    import httpx

    headers = {"Authorization": f"Bearer {server.token}"}
    observed: list[float] = []
    agent_ms: list[float] = []

    def run_agent() -> None:
        started = time.perf_counter()
        with httpx.Client(base_url=server.base_url, timeout=120.0) as client:
            response = client.post("/cli/agent/run",
                                   json={"task": "report the stub answer"}, headers=headers)
            assert response.status_code == 200
        agent_ms.append((time.perf_counter() - started) * 1000)

    def read_health() -> None:
        time.sleep(0.3)
        with httpx.Client(base_url=server.base_url, timeout=120.0) as client:
            started = time.perf_counter()
            assert client.get(api_server.HEALTH_PATH).status_code == 200
            observed.append((time.perf_counter() - started) * 1000)

    threads = [threading.Thread(target=run_agent)]
    threads += [threading.Thread(target=read_health) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    slowest = max(observed)
    assert agent_ms and agent_ms[0] > STUB_DELAY_S * 1000
    assert slowest < agent_ms[0] * 0.8, (
        f"a reader waited {slowest:.0f} ms while the run took {agent_ms[0]:.0f} ms — the handler "
        "is running blocking command code on the event loop")


# --------------------------------------------------------------------------- #
# 5. the warm session as the keep-alive path
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def warm_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("api-latency-warm")


@pytest.fixture(scope="module")
def warm_server(warm_home: Path, llm_env: dict[str, str]) -> Iterator[LiveServer]:
    """A server plus a real warm session, so a refused command still runs out of process."""
    socket_path = warm_home / "warm" / "warm.sock"
    env = _env(warm_home)
    env.update(llm_env)
    env["SKALDIR_WARM_DIR"] = str(socket_path.parent)
    started = warm_session.start_session(socket_path=socket_path, env=env, parser_cache=False,
                                         wait_s=45)
    assert started.running, f"the warm session did not start: {started.state} — {started.note}"

    bootstrap = Bootstrap(warm_home, llm_env)
    live, process, thread = start_server(
        bootstrap, ["--transport", "auto", "--warm-socket", str(socket_path)], name="warm")
    try:
        yield live
    finally:
        process.terminate()
        process.wait(timeout=20)
        thread.join(timeout=5)
        warm_session.stop_session(socket_path=socket_path, force=True)


def test_the_warm_session_serves_a_command_the_allowlist_refuses(warm_server: LiveServer) -> None:
    """``agent.list`` is refused in-process (a store that grows) and runs warm instead."""
    allowed, reason = api_server.in_process_verdict(
        "agent.list", api_server.route_for("agent.list"))
    assert not allowed and reason

    response = warm_server.request("GET", "/cli/agent/list")
    assert response.status_code == 200, response.text
    assert response.headers["X-Godsv-Transport"] in (
        api_server.WARM_TRANSPORT, api_server.SPAWN_TRANSPORT), (
            "the transport header has to name the real path")
    payload = response.json()
    assert payload["command"] == "agent.list"
    assert isinstance(payload["data"], dict)


def test_without_a_session_the_same_route_falls_back_to_a_child_process(
        warm_server: LiveServer, warm_home: Path, llm_env: dict[str, str]) -> None:
    """The honest fallback: no session is never an error, it is a slower answer with a reason."""
    warm_session.stop_session(socket_path=warm_home / "warm" / "warm.sock", force=True)
    time.sleep(2.5)  # the availability cache is deliberately short (2 s), so wait it out

    response = warm_server.request("GET", "/cli/agent/list")
    assert response.status_code == 200
    assert response.headers["X-Godsv-Transport"] == api_server.SPAWN_TRANSPORT


# --------------------------------------------------------------------------- #
# 6. metadata and the argparse group it reports
# --------------------------------------------------------------------------- #


def test_the_openapi_document_publishes_the_streaming_contract() -> None:
    route_list = api_server.routes()
    document = api_server.openapi_document(route_list)
    assert api_server.validate_document(document, route_list) == []
    assert document["x-godsv-sse"]["contract"] == api_server.SSE_STREAM_CONTRACT
    assert document["x-godsv-sse"]["events"] == list(api_server.SSE_EVENTS)
    assert document["x-godsv-transports"] == list(api_server.TRANSPORTS)
    assert document["x-godsv-in-process-allowlist"] == sorted(api_server.IN_PROCESS_ALLOWLIST)
    assert document["x-godsv-http-keep-alive-s"] == api_server.DEFAULT_KEEP_ALIVE_S
    ok = document["paths"]["/cli/contract/list"]["get"]["responses"]["200"]["content"]
    assert api_server.SSE_MEDIA_TYPE in ok and api_server.NDJSON_MEDIA_TYPE in ok


def test_the_mutually_exclusive_report_names_only_the_real_group() -> None:
    """A regression guard for a measured trap.

    ``argparse``'s group shares the parser's ``_actions`` list, so reading that attribute reports
    every option of a parser that owns one exclusive group — ``code.run`` reported 22 "members"
    instead of ``--file``/``--stdin``, and ``/cli/code/run`` refused every request that needed two
    arguments.
    """
    code = api_server.route_for("code.run")
    assert code.mutually_exclusive == (("--file", "--stdin"),)
    argv = api_server.build_argv(code, {"file": "snippet.py", "workspace": "/tmp"})
    assert argv == ["code", "run", "--file", "snippet.py", "--workspace", "/tmp",
                    "--json", "--no-color"]
    with pytest.raises(api_server.ApiRequestError):
        api_server.build_argv(code, {"file": "a.py", "stdin": True})

    migrate = api_server.route_for("registry.migrate")
    assert migrate.mutually_exclusive == (("--apply", "--dry-run"),)
    with pytest.raises(api_server.ApiRequestError):
        api_server.build_argv(migrate, {"dry_run": True, "apply": True})


def test_cli_python_is_never_asked_for_the_api_token() -> None:
    """The transports must not hand the transport's own credential to a command."""
    env = api_server.child_env({api_server.TOKEN_ENV: "secret", "SKALDIR_FORMAT": "json",
                                "PATH": "/usr/bin"})
    assert api_server.TOKEN_ENV not in env
    assert "SKALDIR_FORMAT" not in env
    assert env["PATH"] == "/usr/bin"


def test_the_serve_flags_expose_the_transport_choices() -> None:
    import argparse

    parser = cli.build_parser()
    sub = next(item for item in parser._actions
               if isinstance(item, argparse._SubParsersAction))
    api_server_cli.register_cli(sub)
    serve = sub.choices["serve"]
    actions = {action.dest: action for action in serve._actions}
    assert list(actions["transport"].choices) == list(api_server.TRANSPORTS)
    assert actions["sse_heartbeat"].default == api_server.DEFAULT_SSE_HEARTBEAT_S
    assert float(actions["keep_alive"].default) == float(api_server.DEFAULT_KEEP_ALIVE_S)
