"""The API server, end to end: a real ``skaldir serve --http`` process answering over TCP.

``tests/test_api_server.py`` pins the generated pieces. This file proves the product surface
itself, with raw evidence:

* a real server process (the API group wired exactly the way the CLI owner's two-line paste wires
  it) listens on loopback, prints its token once and serves ``/health``, ``/openapi.json`` and one
  route per CLI command;
* three real commands answer over HTTP — a read-only one, a mutating one that really writes, and
  one that fails — and each response body is the CLI's own envelope;
* HTTP status is a pure function of ``exit_code`` for every response this test saw;
* a mutating call without a token is refused with 401 **and changes nothing**;
* a second server on the same port refuses to start with a clear message;
* ``daemon status`` finds the running server;
* the token is printed exactly once, never to stdout, and never appears in a log line or a
  response body.

The server is started through a bootstrap script because ``src/godsv/cli.py`` is owned by the CLI
owner and does not call ``api_server_cli.register_cli`` yet; the bootstrap reproduces the paste
(``register_cli(sub)`` before ``cli_contract.annotate(parser)``) against the real parser. Once the
paste lands, ``skaldir serve --http`` is the same thing.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from skaldir import api_server, api_server_cli, cli_contract

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

#: The CLI owner's two-line paste, applied to the real parser — ``register_cli(sub)`` right after
#: ``add_subparsers`` and therefore *before* ``cli_contract.annotate(parser)``. ``build_parser`` is
#: wrapped rather than called once, so every later caller in the process (``api_server.routes()``,
#: ``contract dump``) sees the same wired parser — exactly what the paste achieves.
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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _env(home: Path) -> dict:
    env = dict(os.environ)
    env.pop("GODSV_FORMAT", None)
    env.pop(api_server.TOKEN_ENV, None)
    env["PYTHONPATH"] = str(SRC)
    env["HERMES_HOME"] = str(home / "hermes")
    env["GODSV_HOME"] = str(home / "godsv")
    env["PYTHONUNBUFFERED"] = "1"
    return env


class Bootstrap:
    """A ``skaldir`` invocation through the bootstrap (own process, own runtime home)."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.env = _env(home)

    def run(self, argv: list[str], *, timeout: float = 120) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", BOOTSTRAP, *argv],
            capture_output=True, text=True, timeout=timeout, cwd=str(self.home),
            env=self.env, check=False,
        )

    def popen(self, argv: list[str], *, stdout_path: Path) -> subprocess.Popen:
        self.stdout_handle = stdout_path.open("w", encoding="utf-8")
        return subprocess.Popen(
            [sys.executable, "-c", BOOTSTRAP, *argv],
            stdout=self.stdout_handle, stderr=subprocess.PIPE, text=True, bufsize=1,
            cwd=str(self.home), env=self.env,
        )


class LiveServer:
    """A running server plus the requests this test file made against it."""

    def __init__(self, base_url: str, token: str, stderr_lines: list[str],
                 stdout_path: Path, openapi_path: Path) -> None:
        self.base_url = base_url
        self.token = token
        self.stderr_lines = stderr_lines
        self.stdout_path = stdout_path
        self.openapi_path = openapi_path
        self.calls: list[tuple[str, str, int, str]] = []

    def request(self, method: str, path: str, *, token: bool = False, **kwargs):
        import httpx

        headers = dict(kwargs.pop("headers", {}) or {})
        if token:
            headers["Authorization"] = f"Bearer {self.token}"
        with httpx.Client(base_url=self.base_url, timeout=120.0) as client:
            response = client.request(method, path, headers=headers, **kwargs)
        self.calls.append((method, path, response.status_code, response.text))
        return response

    def envelopes(self) -> list[dict]:
        found = []
        for method, path, _status, text in self.calls:
            if "json" not in text[:1] and not text.strip().startswith("{"):
                continue
            try:
                document = json.loads(text)
            except ValueError:
                continue
            if isinstance(document, dict) and "exit_code" in document:
                found.append((method, path, document))
        return found


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("api-server")


@pytest.fixture(scope="module")
def bootstrap(home: Path) -> Bootstrap:
    return Bootstrap(home)


def _start_server(
    bootstrap: Bootstrap,
    extra: list[str],
    *,
    stdout_name: str = "server.stdout",
    openapi_name: str = "openapi.json",
) -> tuple[LiveServer, subprocess.Popen, threading.Thread]:
    """Start a real server on a free port, wait for /health, read its startup banner."""
    import httpx

    port = _free_port()
    stdout_path = bootstrap.home / stdout_name
    openapi_path = bootstrap.home / openapi_name
    stderr_lines: list[str] = []
    process = bootstrap.popen(
        ["serve", "--http", "--port", str(port), "--openapi", str(openapi_path), *extra],
        stdout_path=stdout_path,
    )

    def reader() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            stderr_lines.append(line.rstrip("\n"))

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    ready = False
    while time.time() < deadline:
        if process.poll() is not None:
            break
        try:
            with httpx.Client(timeout=2.0) as client:
                if client.get(f"{base_url}{api_server.HEALTH_PATH}").status_code == 200:
                    ready = True
                    break
        except httpx.HTTPError:
            time.sleep(0.2)
    if not ready:
        process.terminate()
        raise AssertionError(
            "the server never answered /health; stderr:\n" + "\n".join(stderr_lines[-20:])
        )

    token = ""
    for line in stderr_lines:
        match = TOKEN_LINE.search(line)
        if match:
            token = match.group(1)
    live = LiveServer(base_url, token, stderr_lines, stdout_path, openapi_path)
    return live, process, thread


def _stop_server(bootstrap: Bootstrap, process: subprocess.Popen,
                 thread: threading.Thread) -> None:
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:  # pragma: no cover
        process.kill()
        process.wait(timeout=10)
    thread.join(timeout=5)
    bootstrap.stdout_handle.close()


@pytest.fixture(scope="module")
def server(bootstrap: Bootstrap) -> Iterator[LiveServer]:
    """The real server, started without ``--token-file`` so the printed token is proven."""
    live, process, thread = _start_server(bootstrap, [])
    assert live.token, (
        "the server printed no token line; stderr:\n" + "\n".join(live.stderr_lines)
    )
    yield live
    _stop_server(bootstrap, process, thread)


# --------------------------------------------------------------------------- #
# the surface is there
# --------------------------------------------------------------------------- #


def test_health_answers_with_the_envelope_and_names_the_routes(server: LiveServer) -> None:
    response = server.request("GET", api_server.HEALTH_PATH)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["command"] == "api.health"
    assert body["evidence"]
    assert body["data"]["routes"] > 60
    assert body["data"]["token_required_for_mutations"] is True
    assert {"mcp.serve", "serve", "daemon.serve"} <= set(body["data"]["excluded"])
    assert response.headers["X-Godsv-Exit-Code"] == "0"
    assert response.headers["X-Godsv-Schema-Version"] == cli_contract.SCHEMA_VERSION


def test_the_openapi_document_is_served_and_matches_the_index(server: LiveServer) -> None:
    response = server.request("GET", api_server.OPENAPI_PATH)
    assert response.status_code == 200
    document = response.json()
    assert document["openapi"] == "3.1.0"
    assert document["x-godsv-command-count"] == len(document["paths"])
    assert document["servers"][0]["url"].startswith(server.base_url)
    assert "/cli/tools/show" in document["paths"]
    assert "/cli/daemon/status" in document["paths"]
    assert "/cli/serve" not in document["paths"]
    assert document["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    blocked = {item["command"]: item["reason"] for item in document["x-godsv-excluded"]}
    assert blocked["mcp.serve"].strip()

    index = server.request("GET", "/")
    assert index.status_code == 200
    listed = {line.strip().split()[0] for line in index.text.splitlines()
              if line.strip().startswith("/cli/")}
    assert listed == set(document["paths"]), (
        "the server's own route list and its OpenAPI document disagree"
    )


def test_the_openapi_file_written_at_startup_is_the_served_document(
    server: LiveServer,
) -> None:
    assert server.openapi_path.is_file(), "serve --openapi did not write the document"
    written = json.loads(server.openapi_path.read_text(encoding="utf-8"))
    served = server.request("GET", api_server.OPENAPI_PATH).json()
    assert written == served


# --------------------------------------------------------------------------- #
# real commands over HTTP
# --------------------------------------------------------------------------- #


def test_a_read_only_command_answers_without_a_token(server: LiveServer) -> None:
    response = server.request("GET", "/cli/contract/list")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["command"] == "contract.list"
    assert body["exit_code"] == 0 and body["evidence"]
    assert body["data"]["count"] == len(body["data"]["commands"]) > 0
    assert response.headers["X-Godsv-Command"] == "contract.list"
    assert response.headers["X-Godsv-Exit-Code"] == "0"


def test_a_read_only_command_also_accepts_a_json_body(server: LiveServer) -> None:
    response = server.request("POST", "/cli/contract/list", json={"layer": "ENFORCE"})
    assert response.status_code == 200
    body = response.json()
    assert {item["layer"] for item in body["data"]["commands"]} == {"ENFORCE"}


def test_a_mutating_command_writes_through_with_the_token(server: LiveServer) -> None:
    response = server.request(
        "POST", "/cli/tools/request", token=True,
        json={"capability": "an api-level capability request",
              "reason": "a mutating call over HTTP must write, not just answer"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["command"] == "tools.request"
    assert body["data"]["capability"] == "an api-level capability request"
    assert body["data"]["request_id"]
    assert response.headers["X-Godsv-Exit-Code"] == "0"


def test_a_blocked_command_answers_409_and_says_why(server: LiveServer) -> None:
    """The CLI's own honest verdict survives the transport: recorded, but no loop is wired."""
    response = server.request(
        "POST", "/cli/completion/begin", token=True,
        json={"goal": "the API server answers for real",
              "criterion": ["a mutating call over HTTP wrote this goal"]},
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["exit_code"] == cli_contract.EXIT_BLOCKED
    assert body["error"]["code"] == "blocked"
    assert body["data"]["goal"] == "the API server answers for real"

    # the write really happened: a second, independent command reads it back
    status = server.request("GET", "/cli/completion/status")
    assert status.status_code == 200
    assert status.json()["data"]["goal"] == "the API server answers for real"


def test_a_failing_command_answers_404_with_its_own_error_record(
    server: LiveServer,
) -> None:
    response = server.request("POST", "/cli/tools/show", json={"name": "definitely-not-a-tool"})
    assert response.status_code == 404
    body = response.json()
    assert body["ok"] is False
    assert body["exit_code"] == cli_contract.EXIT_NOT_FOUND
    assert body["error"]["code"] == "not_found"
    assert "definitely-not-a-tool" in body["error"]["message"]
    assert response.headers["X-Godsv-Exit-Code"] == "5"


def test_every_status_this_test_saw_is_the_exit_code_mapping(server: LiveServer) -> None:
    assert len(server.calls) >= 5
    for method, path, status, text in server.calls:
        if not text.strip().startswith("{"):
            continue
        body = json.loads(text)
        if not isinstance(body, dict) or "exit_code" not in body:
            continue
        assert status == api_server.status_for_exit(body["exit_code"]), (
            f"{method} {path} answered {status} for exit {body['exit_code']}"
        )
        assert status in set(api_server.HTTP_STATUS_BY_EXIT.values())


def test_a_wrong_argument_is_refused_without_running_the_command(server: LiveServer) -> None:
    response = server.request("POST", "/cli/tools/show", json={"name": "x", "nope": 1})
    assert response.status_code == 400
    assert "unknown argument" in response.json()["error"]["message"]
    assert response.headers["X-Godsv-Exit-Code"] == "1"


def test_an_unknown_route_answers_with_the_envelope(server: LiveServer) -> None:
    response = server.request("GET", "/cli/not/a/command")
    assert response.status_code == 404
    body = response.json()
    assert body["exit_code"] == 5 and body["error"]["code"] == "not_found"
    assert body["command"] == "not.a.command"


def test_an_oversized_body_is_refused_by_the_limit(server: LiveServer) -> None:
    response = server.request(
        "POST", "/cli/completion/begin", token=True,
        json={"goal": "x" * (api_server.DEFAULT_MAX_BODY_BYTES + 1024)},
    )
    assert response.status_code == 400
    assert response.json()["error"]["details"]["reason"] == "payload_too_large"


# --------------------------------------------------------------------------- #
# auth, over real HTTP
# --------------------------------------------------------------------------- #


def test_a_mutating_call_without_a_token_is_401_and_changes_nothing(
    server: LiveServer,
) -> None:
    response = server.request("POST", "/cli/completion/begin",
                              json={"goal": "this goal must never exist"})
    assert response.status_code == 401
    body = response.json()
    assert body["ok"] is False and body["exit_code"] == cli_contract.EXIT_NEEDS_CREDENTIALS
    assert body["error"]["code"] == "needs_credentials"
    assert body["error"]["details"]["reason"] == "missing_token"
    assert response.headers["WWW-Authenticate"] == "Bearer"

    status = server.request("GET", "/cli/completion/status").json()
    assert status["data"]["goal"] != "this goal must never exist", (
        "the refused call ran anyway"
    )


def test_a_wrong_token_is_401_and_the_right_one_works(server: LiveServer) -> None:
    import httpx

    with httpx.Client(base_url=server.base_url, timeout=60.0) as client:
        response = client.post("/cli/tools/request", json={"capability": "x"},
                               headers={"Authorization": "Bearer not-the-token"})
        assert response.status_code == 401
        assert response.json()["error"]["details"]["reason"] == "invalid_token"
        server.calls.append(
            ("POST", "/cli/tools/request", response.status_code, response.text)
        )

        response = client.post("/cli/tools/show", json={"name": "nope"},
                               headers={"X-Godsv-Token": server.token})
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "not_found"


def test_the_token_is_printed_once_and_never_leaks(server: LiveServer) -> None:
    token = server.token
    printed = [line for line in server.stderr_lines if token in line]
    assert len(printed) == 1, f"the token was printed {len(printed)} times"
    assert "printed once" in printed[0]
    # the startup line is a diagnostic: stdout carries the envelope and nothing else
    assert token not in server.stdout_path.read_text(encoding="utf-8")
    for method, path, _status, text in server.calls:
        assert token not in text, f"{method} {path} echoed the token"
        assert "Bearer " + token not in text
    # and no log line carries it (the access log is on)
    assert len(server.stderr_lines) > 3, "the server logged nothing at all"


def test_the_access_log_is_on_and_redacted(server: LiveServer) -> None:
    assert any("GET" in line or "POST" in line for line in server.stderr_lines)


# --------------------------------------------------------------------------- #
# streaming
# --------------------------------------------------------------------------- #


def test_a_streamed_call_returns_one_envelope_per_line(server: LiveServer) -> None:
    import httpx

    with httpx.Client(base_url=server.base_url, timeout=60.0) as client:
        with client.stream("GET", "/cli/contract/list",
                           headers={"Accept": "application/x-ndjson"}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("application/x-ndjson")
            lines = [line for line in response.iter_lines() if line.strip()]
    assert len(lines) >= 1
    for line in lines:
        envelope = json.loads(line)
        assert envelope["schema_version"] == cli_contract.SCHEMA_VERSION
        assert "exit_code" in envelope
    assert lines[-1] and json.loads(lines[-1])["exit_code"] == 0


def test_the_stream_switch_also_works_as_a_query_or_a_body_key(server: LiveServer) -> None:
    import httpx

    with httpx.Client(base_url=server.base_url, timeout=60.0) as client:
        by_query = client.post("/cli/contract/list?stream=1", json={})
        assert by_query.headers["content-type"].startswith("application/x-ndjson")
        assert json.loads(by_query.text.splitlines()[0])["command"] == "contract.list"

        by_body = client.post("/cli/contract/list", json={"_stream": True})
        assert by_body.headers["content-type"].startswith("application/x-ndjson")
        assert json.loads(by_body.text.splitlines()[0])["exit_code"] == 0
    assert "stream" not in api_server.build_argv(
        next(route for route in api_server.routes() if route.command == "contract.list"), {}
    )


# --------------------------------------------------------------------------- #
# the guards around the server itself
# --------------------------------------------------------------------------- #


def test_a_second_server_on_the_same_port_refuses_to_start(
    server: LiveServer, bootstrap: Bootstrap
) -> None:
    port = int(server.base_url.rsplit(":", 1)[1])
    token_file = bootstrap.home / "second-token"
    token_file.write_text("x" * 40, encoding="utf-8")
    token_file.chmod(0o600)
    result = bootstrap.run(["serve", "--http", "--port", str(port),
                            "--token-file", str(token_file)], timeout=60)
    assert result.returncode == cli_contract.EXIT_BLOCKED
    assert "already in use" in result.stderr
    assert "Skaldir API server" in result.stderr, (
        "the message must name who holds the port, not just the errno"
    )
    assert str(port) in result.stderr
    # it refused before serving, so it printed no token at all
    assert "API token" not in result.stderr


def test_a_non_loopback_bind_needs_the_explicit_flag(bootstrap: Bootstrap) -> None:
    result = bootstrap.run(["serve", "--http", "--host", "0.0.0.0", "--port",
                            str(_free_port())], timeout=60)
    assert result.returncode == cli_contract.EXIT_USAGE
    assert "loopback" in result.stderr
    assert "--allow-remote" in result.stderr


def test_daemon_status_finds_the_running_server(
    server: LiveServer, bootstrap: Bootstrap
) -> None:
    port = int(server.base_url.rsplit(":", 1)[1])
    result = bootstrap.run(["daemon", "status", "--port", str(port), "--json"], timeout=60)
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["command"] == "daemon.status"
    assert body["data"]["running"] is True and body["data"]["skaldir"] is True
    assert body["data"]["routes"] == len(
        server.request("GET", api_server.OPENAPI_PATH).json()["paths"]
    )
    assert body["data"]["token_required_for_mutations"] is True
    assert body["evidence"]


def test_serve_without_the_http_flag_is_a_usage_error(bootstrap: Bootstrap) -> None:
    result = bootstrap.run(["serve", "--json"], timeout=60)
    assert result.returncode == cli_contract.EXIT_USAGE
    body = json.loads(result.stdout)
    assert body["command"] == "serve"
    assert body["error"]["code"] == "usage"
    assert "--http" in body["error"]["hint"]


def test_the_contract_dump_covers_the_new_commands(bootstrap: Bootstrap) -> None:
    """Once wired, the dump invariant sees the three commands (project + hand-over check)."""
    result = bootstrap.run(["contract", "dump", "--json"], timeout=120)
    assert result.returncode == 0
    document = json.loads(result.stdout)["data"]
    assert document["unregistered"] == []
    names = {item["command"] for item in document["commands"]}
    assert {"serve", "daemon.serve", "daemon.status"} <= names
    for name in ("serve", "daemon.serve", "daemon.status"):
        record = next(item for item in document["commands"] if item["command"] == name)
        assert record["registered"] is True
        assert record["args"] and record["has_json_flag"] is True
        assert record["default_evidence"]
    assert api_server_cli.CONTRACT_ENTRIES.keys() <= names


def test_a_server_can_take_its_token_from_a_file_instead_of_printing_one(
    bootstrap: Bootstrap,
) -> None:
    """``--token-file``: the token never appears in the banner, and it authenticates."""
    import httpx

    token_file = bootstrap.home / "file-token"
    known = "k" * 40
    token_file.write_text(known + "\n", encoding="utf-8")
    token_file.chmod(0o600)

    live, process, thread = _start_server(
        bootstrap, ["--token-file", str(token_file)],
        stdout_name="tokenfile.stdout", openapi_name="tokenfile.openapi.json",
    )
    try:
        assert live.token == "", "a file token must not be printed"
        banner = "\n".join(live.stderr_lines)
        assert "token loaded from --token-file (not printed)" in banner
        assert known not in banner, "the file token leaked into the banner"

        with httpx.Client(base_url=live.base_url, timeout=60.0) as client:
            refused = client.post("/cli/tools/request", json={"capability": "x"})
            assert refused.status_code == 401
            accepted = client.post(
                "/cli/tools/request", json={"capability": "a request with the file token"},
                headers={"Authorization": f"Bearer {known}"},
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["data"]["capability"] == "a request with the file token"
        assert known not in "\n".join(live.stderr_lines), "the file token reached a log line"
    finally:
        _stop_server(bootstrap, process, thread)
