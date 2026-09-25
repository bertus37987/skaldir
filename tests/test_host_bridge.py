"""``godsv.host_bridge`` — the host bridge's read-only contract, proven hermetically.

The suite never touches the machine's live host:

* every HTTP test talks to :class:`StubBackend`, a real ``http.server`` on
  ``127.0.0.1:0`` that speaks the same shape as the host backend (public
  ``/api/health`` + ``/api/status``, token-gated everything else, the SPA token in
  ``GET /``);
* ``discover_backends`` is patched to return nothing unless a test is *about*
  discovery, so a running desktop backend can never leak into an assertion;
* ``hermes_binary`` is patched to a throwaway shell script, so nothing spawns the
  developer's real ``hermes``.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from skaldir import host_bridge as HB

# --------------------------------------------------------------------------- #
# the stub host backend                                                         #
# --------------------------------------------------------------------------- #
STUB_TOKEN = "stub-loopback-token"

SESSION_ROW = {
    "id": "20260101_120000_abcdef",
    "title": "A stub session",
    "source": "cli",
    "model": "stub-model",
    "cwd": "/tmp/stub",
    "started_at": 1.0,
    "last_active": 2.0,
    "message_count": 7,
    "tool_call_count": 3,
    "input_tokens": 11,
    "output_tokens": 22,
    "is_active": False,
    "archived": False,
    "pinned": False,
    "profile": "default",
}


def _sessions_payload(query: dict) -> dict:
    """The host's shape, including its habit of appending pinned rows past the page."""
    limit = int((query.get("limit") or ["20"])[0])
    rows = [dict(SESSION_ROW)]
    if limit <= 2:
        rows.append(dict(SESSION_ROW, id="20260101_120001_pinned", title="Pinned", pinned=True))
        rows.append(dict(SESSION_ROW, id="20260101_120002_pinned", title="Pinned 2", pinned=True))
    return {"sessions": rows, "total": 42, "limit": limit, "offset": 0}


class _StubHandler(BaseHTTPRequestHandler):
    server_version = "HermesStub/1.0"

    def log_message(self, *args):  # noqa: D102 — silence the test log
        return

    def _send(self, status: int, body: object, *, raw: bool = False) -> None:
        text = str(body) if raw else json.dumps(body)
        data = text.encode("utf-8")
        self.send_response(status)
        if raw:
            self.send_header("Content-Type", "text/html; charset=utf-8")
        else:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 — http.server's spelling
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        self.server.requests.append(
            {"path": parsed.path, "query": query, "token": self.headers.get(HB.SESSION_HEADER, "")}
        )
        if parsed.path == "/":
            self._send(
                200,
                f'<html><script>window.__HERMES_SESSION_TOKEN__="{self.server.token}";'
                'window.__HERMES_AUTH_REQUIRED__=false;</script></html>',
                raw=True,
            )
            return
        public = parsed.path in ("/api/health", "/api/status")
        if not public and self.headers.get(HB.SESSION_HEADER, "") != self.server.token:
            self._send(401, {"detail": "Unauthorized"})
            return
        handler = self.server.routes.get(parsed.path)
        if handler is None:
            self._send(404, {"detail": f"the stub has no {parsed.path}"})
            return
        self._send(200, handler(query))

    def do_POST(self) -> None:  # noqa: N802 — recorded, then refused
        """The bridge may never POST: record it so a stray write shows up in the suite."""
        self.server.writes.append(self.path)
        self._send(405, {"detail": "this stub is a read-only host surface"})

    do_PUT = do_POST
    do_DELETE = do_POST
    do_PATCH = do_POST


class StubBackend:
    """A stand-in host backend: real HTTP, real token gate, recorded requests."""

    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
        self.server.token = STUB_TOKEN
        self.server.requests = []
        self.server.writes = []
        self.server.routes = {
            "/api/health": lambda _q: {"ok": True, "version": "9.9.9-stub", "auth_required": False},
            "/api/status": lambda _q: {
                "version": "9.9.9-stub",
                "auth_required": False,
                "gateway_running": True,
                "gateway_state": "running",
                "gateway_platforms": {"telegram": True},
                "components": {"gateway": {"status": "ok"}, "dashboard": {"status": "ok"}},
            },
            "/api/sessions": _sessions_payload,
            "/api/sessions/search": lambda q: {
                "results": [
                    {
                        "session_id": SESSION_ROW["id"],
                        "title": "A stub session",
                        "role": "tool",
                        "snippet": f"found {(q.get('q') or [''])[0]}",
                        "source": "cli",
                        "model": "stub-model",
                        "lineage_root": SESSION_ROW["id"],
                        "last_active": 3.0,
                    }
                ]
            },
            "/api/skills": lambda _q: [
                {"name": "alpha", "category": "cat", "description": "d", "enabled": True,
                 "provenance": "bundled", "usage": 2},
                {"name": "beta", "category": "cat", "description": "d", "enabled": False,
                 "provenance": "agent", "usage": 0},
            ],
            "/api/cron/jobs": lambda _q: [
                {"id": "job1", "name": "nightly", "schedule": "0 3 * * *", "enabled": True},
            ],
            "/api/tools/toolsets": lambda _q: [
                {"name": "web", "label": "Web", "platform": "cli", "enabled": True,
                 "available": True, "configured": True, "tools": ["web_search", "web_extract"]},
                {"name": "memory", "label": "Memory", "platform": "cli", "enabled": True,
                 "available": True, "configured": False, "tools": ["memory"]},
            ],
            "/api/memory": lambda _q: {
                "active": "", "providers": [], "builtin_files": {"memory": 10, "user": 20}},
            "/api/profiles": lambda _q: {"profiles": [{"name": "default", "is_default": True}]},
            "/api/logs": lambda _q: {"file": "agent", "lines": ["one line"]},
        }
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def requests(self) -> list[dict]:
        return self.server.requests

    def paths(self) -> list[str]:
        return [item["path"] for item in self.server.requests]

    @property
    def writes(self) -> list[str]:
        """Any non-GET the bridge sent (must always be empty)."""
        return self.server.writes

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def stub():
    backend = StubBackend()
    try:
        yield backend
    finally:
        backend.close()


# --------------------------------------------------------------------------- #
# hermeticity: no live host, no real host CLI                                   #
# --------------------------------------------------------------------------- #
FAKE_BANNER = "Hermes Agent v9.9.9 (2026.1.1) · upstream deadbeef"
FAKE_TABLE = """Title                        Workspace          Last Active   ID
────────────────────────────────────────────────────────────────────────────────
Stub session one             /tmp/one           yesterday     20260101_120000_abcdef
  … more not shown (use --limit 10 to see more)
"""


def _write_fake_hermes(
    tmp_path: Path, *, banner: str = FAKE_BANNER, table: str = FAKE_TABLE
) -> str:
    """A throwaway ``hermes`` that answers the two things the bridge ever asks it."""
    script = tmp_path / "hermes"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f'  echo "{banner}"\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "sessions" ] && [ "$2" = "list" ]; then\n'
        "  cat <<'TABLE'\n" + table + "TABLE\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "serve" ] && [ "$2" = "--status" ]; then\n'
        '  echo "No hermes dashboard or serve processes running."\n'
        "  exit 0\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return str(script)


@pytest.fixture(autouse=True)
def hermetic(tmp_path, monkeypatch):
    """No test may see the machine's live host or spawn the developer's ``hermes``.

    Discovery is pinned to an empty ``/proc`` lookalike: the real scanning code runs,
    but it can only ever find processes of the developer's machine if a test asks for
    them explicitly by pointing this seam at the real ``/proc``.
    """
    empty_proc = tmp_path / "proc"
    empty_proc.mkdir(exist_ok=True)
    monkeypatch.setattr(HB, "_proc_root", lambda: empty_proc)
    monkeypatch.delenv(HB.ENV_URL, raising=False)
    monkeypatch.delenv(HB.ENV_TOKEN, raising=False)
    fake = _write_fake_hermes(tmp_path)
    monkeypatch.setattr(HB, "hermes_binary", lambda explicit=None: explicit or fake)
    return fake


# --------------------------------------------------------------------------- #
# read-only is mechanical, not a promise                                        #
# --------------------------------------------------------------------------- #
def test_http_get_refuses_any_route_outside_the_whitelist():
    for path in ("/api/sessions/bulk-delete", "/api/cron/jobs/job1/trigger", "/api/skills/toggle"):
        with pytest.raises(ValueError, match="not a whitelisted read route"):
            HB.http_get(HB.Backend(url="http://127.0.0.1:1"), path)


def test_http_get_refuses_any_method_but_get():
    with pytest.raises(ValueError, match="read-only"):
        HB._assert_read_only("/api/sessions", "POST")
    with pytest.raises(ValueError, match="read-only"):
        HB._assert_read_only("/api/cron/jobs", "DELETE")


def test_every_whitelisted_route_is_a_get_route_on_the_host_backend():
    assert set(HB.READ_ONLY_ROUTES) >= {
        "/api/health",
        "/api/status",
        "/api/sessions",
        "/api/sessions/search",
        "/api/skills",
        "/api/cron/jobs",
        "/api/tools/toolsets",
    }
    assert all(path.startswith("/api/") for path in HB.READ_ONLY_ROUTES)


def test_the_module_never_writes_anything_anywhere():
    """A source scan, because "read-only" must be checkable, not asserted."""
    source = Path(HB.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "httpx.post",
        "httpx.put",
        "httpx.delete",
        "httpx.patch",
        '.write_text(',
        '.write_bytes(',
        "shutil.copy",
        "os.remove",
        "os.unlink",
        "os.rename",
        "subprocess.Popen",
    ):
        assert forbidden not in source, f"host_bridge must stay read-only: found {forbidden}"


# --------------------------------------------------------------------------- #
# the HTTP door                                                                 #
# --------------------------------------------------------------------------- #
def test_http_get_reports_a_401_honestly_instead_of_flattening_it(stub):
    unauthenticated = HB.Backend(url=stub.url, token="", token_source="none")
    result = HB.http_get(unauthenticated, "/api/sessions")
    assert result["ok"] is False
    assert result["status"] == 401
    assert "HTTP 401" in result["error"]
    assert "Unauthorized" in result["error"]


def test_public_routes_answer_without_a_token(stub):
    anonymous = HB.Backend(url=stub.url, token="", token_source="none")
    for path in ("/api/health", "/api/status"):
        result = HB.http_get(anonymous, path)
        assert result["ok"] is True, path
        assert result["json"]["version"] == "9.9.9-stub"


def test_the_token_travels_in_the_hosts_own_header(stub):
    authenticated = HB.Backend(url=stub.url, token=STUB_TOKEN, token_source="flag")
    assert HB.http_get(authenticated, "/api/sessions")["ok"] is True
    assert stub.requests[-1]["token"] == STUB_TOKEN


def test_a_feature_call_maps_a_401_onto_the_blocked_verdict(stub):
    anonymous = HB.Backend(url=stub.url, token="")
    with pytest.raises(HB.HostBlocked) as excinfo:
        HB.sessions(backend=anonymous)
    assert excinfo.value.exit_code == HB.EXIT_BLOCKED
    assert "refused the loopback token" in excinfo.value.reason
    assert excinfo.value.details["token_source"] == "none"


# --------------------------------------------------------------------------- #
# discovery: /proc is the only guess-free way to find a --port 0 backend        #
# --------------------------------------------------------------------------- #
def test_the_serve_argv_shapes_this_machine_actually_has():
    console_script = ["/home/w/.local/bin/hermes", "serve", "--host", "127.0.0.1", "--port", "0"]
    venv_entry = ["/home/w/.hermes/hermes-agent/venv/bin/python",
                  "/home/w/.hermes/hermes-agent/hermes", "serve", "--host", "127.0.0.1",
                  "--port", "0"]
    assert HB._serve_subcommand(console_script) == "serve"
    assert HB._serve_subcommand(venv_entry) == "serve"
    assert HB._serve_subcommand(["/usr/bin/python", "-m", "pytest", "tests"]) == ""
    assert HB._serve_subcommand(["hermes", "sessions", "list"]) == ""
    assert HB._argv_port(console_script) == 0
    assert HB._argv_port(["hermes", "serve", "--port=9119"]) == 9119
    assert HB._argv_port(["hermes", "serve"]) == 0


def test_listening_ports_reads_our_own_socket_out_of_proc():
    """The real mechanism (process fds → /proc/net/tcp inodes), against ourselves."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert port in HB._listening_ports(os.getpid())
    finally:
        server.close()


def test_the_token_is_readable_from_the_backend_processs_own_environment():
    """The desktop app injects HERMES_DASHBOARD_SESSION_TOKEN; a child proves the read."""
    child = subprocess.Popen(  # nosec B603 — a fixed argv probe
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env={**os.environ, "HERMES_DASHBOARD_SESSION_TOKEN": "from-the-process-env"},
    )
    try:
        for _ in range(40):
            if HB._proc_environ(child.pid):
                break
            time.sleep(0.05)
        token, source = HB._read_token(child.pid, "http://127.0.0.1:1")
        assert (token, source) == ("from-the-process-env", "proc-environ")
    finally:
        child.terminate()
        child.wait(timeout=10)


def test_the_token_is_readable_from_the_spa_document_as_a_fallback(stub, tmp_path):
    """No process to ask: the backend hands the same token to loopback clients at /."""
    child = subprocess.Popen(  # nosec B603 — a fixed argv probe
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env={key: value for key, value in os.environ.items()
             if key != "HERMES_DASHBOARD_SESSION_TOKEN"},
    )
    try:
        for _ in range(40):
            if HB._proc_environ(child.pid):
                break
            time.sleep(0.05)
        token, source = HB._read_token(child.pid, stub.url)
        assert (token, source) == (STUB_TOKEN, "spa-html")
    finally:
        child.terminate()
        child.wait(timeout=10)


def test_discovery_without_proc_says_so_instead_of_guessing(monkeypatch, tmp_path):
    monkeypatch.setattr(HB, "_proc_root", lambda: tmp_path / "no-such-proc")
    found = HB.discover_backends()
    assert found.method == "unsupported"
    assert found.backends == []
    assert any("--url" in note for note in found.notes)


def test_discovery_over_an_empty_proc_finds_nothing_and_says_why():
    found = HB.discover_backends()  # the autouse fixture's empty /proc lookalike
    assert found.method == "proc-scan"
    assert found.backends == []
    assert any("no running" in note for note in found.notes)


def test_resolve_backend_over_an_empty_proc_is_blocked_with_the_scan_as_evidence():
    with pytest.raises(HB.HostBlocked) as excinfo:
        HB.resolve_backend()
    assert excinfo.value.exit_code == HB.EXIT_BLOCKED
    assert excinfo.value.details["backend_count"] == 0


def test_an_explicit_url_that_answers_nothing_is_blocked_with_the_reason():
    with pytest.raises(HB.HostBlocked) as excinfo:
        HB._from_env(url="http://127.0.0.1:1")
    assert "no host backend answers at http://127.0.0.1:1" in excinfo.value.reason
    assert excinfo.value.exit_code == HB.EXIT_BLOCKED


# --------------------------------------------------------------------------- #
# install facts                                                                 #
# --------------------------------------------------------------------------- #
def test_host_version_reads_the_hosts_own_banner(hermetic):
    info = HB.host_version()
    assert info["installed"] is True
    assert info["version"] == "9.9.9"
    assert info["source"] == "cli-text"
    assert "Hermes Agent v9.9.9" in info["raw"]


def test_host_version_reports_a_missing_host_plainly(monkeypatch):
    monkeypatch.setattr(HB, "hermes_binary", lambda explicit=None: None)
    info = HB.host_version()
    assert info["installed"] is False
    assert info["version"] is None
    assert "no `hermes` binary found" in info["error"]


def test_host_home_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "host-home"))
    assert HB.host_home() == tmp_path / "host-home"


# --------------------------------------------------------------------------- #
# the features                                                                  #
# --------------------------------------------------------------------------- #
def test_status_separates_installed_from_running(stub):
    payload = HB.status(url=stub.url, token=STUB_TOKEN)
    assert payload["installed"] is True
    assert payload["running"] is True
    assert payload["version"] == "9.9.9-stub"
    assert payload["version_source"] == "http"
    assert payload["backend"]["url"] == stub.url
    assert payload["backend"]["token_source"] == "flag"
    assert payload["gateway"]["running"] is True
    assert payload["components"]["dashboard"] == "ok"


def test_status_is_honest_when_no_backend_answers():
    payload = HB.status(url="http://127.0.0.1:1")
    assert payload["installed"] is True
    assert payload["running"] is False
    assert "no host backend answers" in payload["reason"]
    assert payload["backend"] is None


def test_status_is_honest_when_no_host_is_installed(monkeypatch):
    monkeypatch.setattr(HB, "hermes_binary", lambda explicit=None: None)
    payload = HB.status()
    assert payload["installed"] is False
    assert payload["running"] is False
    assert payload["reason"]


def test_sessions_names_the_profile_it_read(stub):
    payload = HB.sessions(url=stub.url, token=STUB_TOKEN, limit=5)
    assert payload["request"].startswith("GET /api/sessions?")
    assert "profile=default" in payload["request"]
    assert stub.requests[-1]["query"]["profile"] == ["default"]


def test_sessions_omits_the_profile_when_asked_to(stub):
    payload = HB.sessions(url=stub.url, token=STUB_TOKEN, profile="-", limit=1)
    assert "profile" not in stub.requests[-1]["query"]
    assert payload["profile"] == "(omitted)"


def test_sessions_reports_pinned_rows_past_the_page(stub):
    payload = HB.sessions(url=stub.url, token=STUB_TOKEN, limit=1)
    assert payload["count"] > payload["limit"]
    assert payload["pinned"] == 2
    assert "pinned" in payload["note"]
    assert payload["fragile"] is False


def test_a_page_that_fits_is_not_reported_as_overflowing(stub):
    payload = HB.sessions(url=stub.url, token=STUB_TOKEN, limit=5)
    assert payload["count"] == 1
    assert payload["pinned"] == 0
    assert payload["note"] == ""


def test_sessions_carries_only_the_fields_a_reader_needs(stub):
    payload = HB.sessions(url=stub.url, token=STUB_TOKEN, limit=1)
    row = payload["sessions"][0]
    assert row["id"] == SESSION_ROW["id"]
    assert row["title"] == "A stub session"
    assert row["message_count"] == 7
    assert "system_prompt" not in row


def test_sessions_via_cli_is_labelled_fragile_and_parses_the_real_table(hermetic):
    payload = HB.sessions_via_cli(10, binary=hermetic)
    assert payload["fragile"] is True
    assert payload["transport"] == "cli-text"
    assert payload["count"] == 1
    assert payload["sessions"][0]["id"] == "20260101_120000_abcdef"
    assert payload["sessions"][0]["title"] == "Stub session one"
    assert payload["sessions"][0]["workspace"] == "/tmp/one"
    assert "human" in payload["fragile_note"]


def test_sessions_via_cli_raises_needs_setup_without_a_host(monkeypatch):
    monkeypatch.setattr(HB, "hermes_binary", lambda explicit=None: None)
    with pytest.raises(HB.HostNeedsSetup):
        HB.sessions_via_cli(5)


def test_memory_search_names_the_index_it_searched(stub):
    payload = HB.memory_search("penpot", url=stub.url, token=STUB_TOKEN, limit=3)
    assert payload["query"] == "penpot"
    assert "FTS5" in payload["source"]
    assert "not MEMORY.md" in payload["searches"]
    assert payload["count"] == 1
    assert payload["results"][0]["snippet"] == "found penpot"
    assert stub.requests[-1]["query"]["q"] == ["penpot"]


def test_memory_search_refuses_an_empty_query(stub):
    with pytest.raises(HB.HostBlocked, match="empty query"):
        HB.memory_search("   ", url=stub.url, token=STUB_TOKEN)


def test_skills_counts_provenance_and_enabled(stub):
    payload = HB.skills(url=stub.url, token=STUB_TOKEN)
    assert payload["count"] == 2
    assert payload["enabled"] == 1
    assert payload["by_provenance"] == {"bundled": 1, "agent": 1}
    assert payload["skills"][0]["name"] == "alpha"


def test_cron_lists_jobs_read_only(stub):
    payload = HB.cron_jobs(url=stub.url, token=STUB_TOKEN)
    assert payload["count"] == 1
    assert payload["jobs"][0]["name"] == "nightly"
    assert payload["request"] == "GET /api/cron/jobs?profile=all"
    assert payload["fragile"] is False


def test_toolsets_count_tools(stub):
    payload = HB.toolsets(url=stub.url, token=STUB_TOKEN)
    assert payload["count"] == 2
    assert payload["tool_count"] == 3
    assert payload["toolsets"][0]["tools"] == ["web_search", "web_extract"]


# --------------------------------------------------------------------------- #
# doctor                                                                        #
# --------------------------------------------------------------------------- #
def test_doctor_probes_every_route_a_verb_uses(stub):
    payload = HB.doctor(url=stub.url, token=STUB_TOKEN)
    assert payload["installed"] is True
    assert payload["running"] is True
    assert payload["blocked"] == {}
    probed = {entry["path"] for entry in payload["routes"]}
    assert probed >= {
        "/api/health",
        "/api/status",
        "/api/sessions",
        "/api/sessions/search",
        "/api/skills",
        "/api/cron/jobs",
        "/api/tools/toolsets",
    }
    assert all(entry["ok"] for entry in payload["routes"])
    # the probe params are the verbs' own params, not a looser guess
    sessions_probe = next(e for e in payload["routes"] if e["path"] == "/api/sessions")
    assert sessions_probe["params"]["profile"] == "default"
    search_probe = next(e for e in payload["routes"] if e["path"] == "/api/sessions/search")
    assert search_probe["params"]["q"] == "skaldir"


def test_doctor_reports_a_blocked_route_with_its_status(stub, monkeypatch):
    anonymous = HB.Backend(url=stub.url, token="")
    monkeypatch.setattr(
        HB, "resolve_backend", lambda **kwargs: (anonymous, HB.Discovery(method="test"))
    )
    payload = HB.doctor()
    assert payload["running"] is True
    assert payload["blocked"], "a token-less backend must show blocked routes, not a green report"
    assert payload["reachable"]["health"] == "keys: auth_required, ok, version"
    assert any("blocked" in note for note in payload["notes"])


def test_doctor_is_blocked_without_a_backend():
    payload = HB.doctor(url="http://127.0.0.1:1")
    assert payload["installed"] is True
    assert payload["running"] is False
    assert payload["routes"] == []
    assert payload["reason"]


def test_doctor_without_a_host_needs_setup(monkeypatch):
    monkeypatch.setattr(HB, "hermes_binary", lambda explicit=None: None)
    payload = HB.doctor()
    assert payload["installed"] is False
    assert "needs setup" in " ".join(payload["notes"])


def test_the_whole_run_sends_nothing_but_get(stub):
    """One sweep of every verb, then: the stub recorded no write of any kind."""
    HB.status(url=stub.url, token=STUB_TOKEN)
    HB.sessions(url=stub.url, token=STUB_TOKEN, limit=2)
    HB.memory_search("x", url=stub.url, token=STUB_TOKEN)
    HB.skills(url=stub.url, token=STUB_TOKEN)
    HB.cron_jobs(url=stub.url, token=STUB_TOKEN)
    HB.toolsets(url=stub.url, token=STUB_TOKEN)
    HB.doctor(url=stub.url, token=STUB_TOKEN)
    assert stub.writes == []
    assert set(stub.paths()) <= set(HB.READ_ONLY_ROUTES)


def test_doctor_can_report_the_host_clis_blind_spot_as_evidence(stub):
    payload = HB.doctor(url=stub.url, token=STUB_TOKEN, probe_cli=True)
    status = payload["cli_serve_status"]
    assert status["ran"] is True
    assert status["agrees_with_bridge"] is False
    assert "--port 0" in status["known_blind_spot"]
