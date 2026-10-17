"""The browser backend wired to the real `agent-browser` verbs (§5 vertical prototype).

Most of these are contract tests that need no network. The last group is deliberately
marked as a *real* invocation: it runs the pinned binary and is skipped when the tool is
absent, because a stub test cannot prove a transport works.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from skaldir.browser_layer import AgentBrowserBackend, ErrorClass, _unwrap_json


def test_unwrapping_a_json_envelope_yields_the_page_content():
    envelope = {"success": True, "data": {"content": "# Title\n\nBody", "status": 200,
                                          "source": "html-fallback", "contentType": "text/html"}}
    env, content = _unwrap_json(json.dumps(envelope))
    assert content == "# Title\n\nBody"
    assert env["data"]["status"] == 200


def test_plain_text_output_is_not_an_error():
    env, content = _unwrap_json("just some prose")
    assert env == {}
    assert content == "just some prose"


def test_an_empty_answer_is_empty_not_a_crash():
    assert _unwrap_json("") == ({}, "")


def test_an_unsupported_action_is_refused_by_name():
    """§4: "Nur tatsächlich unterstützte Operationen anbieten"."""
    b = AgentBrowserBackend(root="/nonexistent")
    result = b.act({"kind": "teleport"})
    assert result["ok"] is False
    assert result["error_class"] == ErrorClass.UNSUPPORTED.value
    assert "teleport is not supported" in result["detail"]
    assert "known kinds:" in result["detail"]


def test_close_all_is_always_refused_because_of_foreign_tabs():
    """BR-13: Nutzertabs bleiben erhalten."""
    b = AgentBrowserBackend(root="/nonexistent")
    for attempt in ("--all", "*"):
        result = b.close_tab(tab=attempt)
        assert result["ok"] is False
        assert "BR-13" in result["detail"] or "user's own tabs" in result["detail"]


def test_close_tab_without_a_name_is_refused_not_a_wildcard():
    b = AgentBrowserBackend(root="/nonexistent")
    result = b.close_tab(tab="")
    assert result["ok"] is False
    assert "no 'close everything' here" in result["detail"]


def test_the_action_vocabulary_is_the_tools_actual_verbs():
    """The map must not invent verbs the tool does not have."""
    b = AgentBrowserBackend(root="/nonexistent")
    verbs = set(b.ACTION_VERBS.values())
    assert {"open", "click", "type", "fill", "press", "hover", "select", "drag",
            "upload", "download", "scroll", "wait"} <= verbs
    assert "--all" not in verbs
    assert "close" not in verbs, "closing is not an 'action', it is a separate, guarded call"


def test_normalize_browser_key():
    from skaldir.browser_layer import _normalize_browser_key

    assert _normalize_browser_key("CTRL+L") == "Control+l"
    assert _normalize_browser_key("ctrl+shift+r") == "Control+Shift+r"
    assert _normalize_browser_key("ENTER") == "Enter"
    assert _normalize_browser_key("return") == "Enter"
    assert _normalize_browser_key("esc") == "Escape"
    assert _normalize_browser_key("space") == "Space"
    assert _normalize_browser_key("Alt+Tab") == "Alt+Tab"


def test_key_action_verb_mapping():
    b = AgentBrowserBackend(root="/nonexistent")
    assert b.ACTION_VERBS["key"] == "press"
    assert b.ACTION_VERBS["keypress"] == "press"
    assert b.ACTION_VERBS["hotkey"] == "press"
    assert b.ACTION_VERBS["shortcut"] == "press"


# --------------------------------------------------------------------------- #
# real invocation — the vertical prototype (§5)
# --------------------------------------------------------------------------- #


class _ProbePage(BaseHTTPRequestHandler):
    """A deterministic, self-owned page: one paragraph carries a unique marker.

    Serves real HTTP on the loopback (the repo's established ``ThreadingHTTPServer`` on
    ``127.0.0.1:0`` pattern). The marker lives in the *body text* — ``agent-browser read``
    extracts the rendered body, not ``<title>`` — so the read's content assertion can be exact
    instead of depending on a page nobody here controls.
    """

    def do_GET(self) -> None:  # noqa: N802 — http.server's spelling
        body = (
            b"<!doctype html><html><head><title>skaldir read probe</title></head>"
            b"<body><p>SKALDIR-E2E-READ-PROBE deterministic read target.</p></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # noqa: A002 — http.server's name
        pass  # keep the pytest output clean


@pytest.fixture()
def read_probe_url() -> str:
    """Provision the real-read precondition: a loopback page with a known marker.

    The read below used to target the live ``https://example.com`` and assert its rendered
    text. That page is external and IANA-owned: its body text changed in 2025 (the old
    "Example Domain" heading is now only the ``<title>``, which ``agent-browser read`` does
    not return), so the assertion was *environment*-dependent, not deterministic. This
    fixture provisions the precondition instead — a real loopback HTTP page whose content is
    known — so the read is still exercised through the real ``agent-browser`` binary against a
    real URL, just without a dependency no test in this repo controls.
    """
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _ProbePage)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/probe"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _real() -> AgentBrowserBackend:
    return AgentBrowserBackend()


@pytest.fixture()
def close_real_sessions():
    """The real-transport tests below create their own NAMED agent-browser sessions;
    leaving them running leaked one browser per suite run (measured 2026-10-06:
    pytest-launch/pytest-read survived the run). The teardown closes ONLY these
    recorded own-session names — never a foreign session, never `--all`."""
    yield
    backend = _real()
    for name in ("pytest-launch", "pytest-read"):
        backend.run(["--session", name, "close", "--json"])


def test_the_real_binary_reports_a_version():
    cap = _real().probe()
    if not cap.available:
        pytest.skip(f"agent-browser is not installed here: {cap.reason}")
    assert cap.version, "an installed tool that cannot name its version is unusable (§9.2)"
    assert "agent-browser" in cap.version


@pytest.mark.usefixtures("close_real_sessions")
def test_a_real_read_returns_the_page_content_not_the_json_envelope(read_probe_url):
    """BR-07: strukturierte selektive Seiteninhalte — measured against the real tool.

    The load-bearing property is unchanged and as strong as before: a read through the real
    ``agent-browser`` returns the page's *content* and the JSON envelope is unwrapped away.
    What changed is only the *precondition*: the target is now the self-owned deterministic
    page from :func:`read_probe_url` rather than the live example.com, whose rendered text is
    external and changed (its "Example Domain" heading moved into ``<title>``, which the read
    does not return) — that external dependency is what made this test environment-dependent.
    The marker must survive the read, and the content must not be the raw ``{…}`` envelope.
    """
    b = _real()
    if not b.probe().available:
        pytest.skip("agent-browser is not installed here")
    result = b.read(url=read_probe_url, session="pytest-read")
    assert result["ok"] is True, result.get("detail")
    assert "SKALDIR-E2E-READ-PROBE" in result["content"], (
        f"the self-owned probe page content did not survive the read: {result['content'][:200]}"
    )
    assert not result["content"].lstrip().startswith("{"), "the JSON envelope was not unwrapped"
    assert result["url"].startswith("http://127.0.0.1:"), result.get("url")
    assert result["duration_ms"] is not None
    assert result["source"].startswith("agent-browser read")


@pytest.mark.usefixtures("close_real_sessions")
def test_a_real_launch_does_not_close_anything_and_reports_itself():
    b = _real()
    if not b.probe().available:
        pytest.skip("agent-browser is not installed here")
    result = b.launch(session="pytest-launch", url="https://example.com")
    assert result["backend"] == "agent-browser"
    assert result["session_id"] == "pytest-launch"
    # and the guard still refuses the global close afterwards
    assert b.close_tab(tab="--all", session="pytest-launch")["ok"] is False
