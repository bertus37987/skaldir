"""Live proof (VAL-WEB-003): the agent path reuses the CLI's browser session.

Run with the repo venv and an isolated SKALDIR_HOME that the CLI proof already
used, e.g.:

    cd /home/willy/Agentic_Projects/Skaldir_Agent && \
    SKALDIR_HOME=/tmp/skaldir-browser-reuse/home \
    .venv/bin/python tools/browser_session_agent_path_proof.py

What it proves with the REAL agent-browser binary (real browser, real state):

1. the assembled AgentLoop toolset offers `browser` and its handler drives the
   central ensure lifecycle — session_action is `reused` against the session the
   CLI ensure processes created (same state dir, same start count);
2. a real read through the provisioned product session returns real page content;
3. an empty observation (about:blank) moves the state to `unconfirmed` with a
   diagnosis and starts nothing (launch count stays at 1);
4. the next ensure after that reconciles from the live session without launching.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class _EmptyPage(BaseHTTPRequestHandler):
    """A deterministic, self-owned page that loads but renders NOTHING.

    This is the live empty-observation case (measured: `agent-browser read
    about:blank` is refused outright — "Invalid read URL" — so an empty observation
    in practice is a page that answers with no body text).
    """

    def do_GET(self) -> None:  # noqa: N802 — http.server's spelling
        body = b"<!doctype html><html><head><title>empty</title></head><body></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # noqa: A002 — http.server's name
        pass


def _serve_empty_page() -> tuple[str, ThreadingHTTPServer]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _EmptyPage)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_port}/empty"
    return url, httpd


def main() -> int:
    from skaldir.agent_loop import ToolSet
    from skaldir.browser_layer import SessionManager, session_state_dir
    from skaldir.core_capabilities import core_capability_tools

    home = Path(os.environ.get("SKALDIR_HOME") or "")
    if not home:
        print("SET SKALDIR_HOME to the isolated runtime home of this proof", file=sys.stderr)
        return 2
    workspace = Path(os.environ.get("SKALDIR_PROOF_WS") or home / "agent-ws")
    workspace.mkdir(parents=True, exist_ok=True)
    runtime = home / "agent-run-runtime"

    before = SessionManager(session_state_dir()).discover()
    print(json.dumps({"before": {"state": before["state"], "starts": before["starts"],
                                 "session_id": before["session_id"]}}, sort_keys=True))

    tools = ToolSet.of(core_capability_tools(workspace=workspace, runtime=runtime,
                                            run_id="agent-live-proof"))
    names = [t.name for t in tools.tools]
    print(json.dumps({"toolset_offers_browser": "browser" in names,
                      "browser_count": names.count("browser")}, sort_keys=True))

    tool = next(t for t in tools.tools if t.name == "browser")
    result = tool.handler(do="read", url="https://example.com")
    print(json.dumps({"read_1": {k: result.get(k) for k in
                                 ("ok", "url", "session_action", "session_id",
                                  "session_state_dir", "state", "content")}},
                     sort_keys=True)[:1200])

    empty_url, httpd = _serve_empty_page()
    try:
        empty = tool.handler(do="read", url=empty_url)
        print(json.dumps({"read_empty": {k: empty.get(k) for k in
                                         ("ok", "url", "state", "diagnosis",
                                          "session_action")}}, sort_keys=True))

        after_empty = SessionManager(session_state_dir()).discover()
        print(json.dumps({"state_after_empty": {
            "state": after_empty["state"], "starts": after_empty["starts"],
            "unconfirmed_reason": after_empty["unconfirmed_reason"][:160]}}, sort_keys=True))

        reconciled = tool.handler(do="read", url="https://example.com")
        after_reconciled = SessionManager(session_state_dir()).discover()
        print(json.dumps({"read_after_reconcile": {
            "session_action": reconciled.get("session_action"),
            "ok": reconciled.get("ok"),
            "state": after_reconciled["state"],
            "starts": after_reconciled["starts"]}}, sort_keys=True))
    finally:
        httpd.shutdown()
        httpd.server_close()

    checks = {
        "toolset_offers_browser_once": names.count("browser") == 1,
        "agent_action_reused": result.get("session_action") == "reused",
        "real_content_read": bool(str(result.get("content") or "").strip()),
        "empty_marked_unconfirmed": empty.get("state") == "unconfirmed",
        "empty_diagnosis_present": bool(empty.get("diagnosis")),
        "no_new_start_after_empty": after_empty["starts"] == before["starts"],
        "reconcile_did_not_launch": after_reconciled["starts"] == before["starts"],
        "one_state_dir": str(Path(result.get("session_state_dir") or "")) == str(session_state_dir()),
    }
    print(json.dumps({"checks": checks}, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
