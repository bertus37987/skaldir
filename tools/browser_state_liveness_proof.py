"""Live proof (browser-state-liveness-reconcile): state honesty of the session lifecycle.

Run with the repo venv and an ISOLATED runtime home, e.g.:

    cd /home/willy/Agentic_Projects/Skaldir_Agent && \
    SKALDIR_HOME=/tmp/skaldir-browser-liveness/home \
    .venv/bin/python tools/browser_state_liveness_proof.py

What it proves with the REAL agent-browser binary (real browser, real state file):

1. `ensure` launches the product's own named session and counts it (starts 0->1);
2. `discover` on a live session reports ready/live=true and starts nothing;
3. after an OUT-OF-BAND close of the browser, `discover` is NOT a bare ready: the state
   is reconciled (unknown + live=false + the diagnosis), and the state file records it;
4. the next `ensure` is the product's OWN observed launch and moves the counter (1->2);
5. a per-call relaunch agent-browser performs itself (measured: any transport call on a
   dead session) is SURFACED by the transport (`relaunched: true`) and, when the product
   records it, `starts` counts it (2->3) — the counter never silently misses a launch;
6. cleanup closes only our own named session; no `close --all`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _agent_browser() -> str:
    here = Path(__file__).resolve().parent.parent
    local = here / "node_modules" / ".bin" / "agent-browser"
    return str(local) if local.exists() else "agent-browser"


def _close_session(session: str) -> None:
    subprocess.run([_agent_browser(), "--session", session, "close", "--json"],
                   capture_output=True, text=True, timeout=60, check=False)


def main() -> int:
    from skaldir.browser_layer import (
        PRODUCT_SESSION_NAME,
        AgentBrowserBackend,
        SessionManager,
        session_state_dir,
    )

    if not os.environ.get("SKALDIR_HOME"):
        print("SET SKALDIR_HOME to an isolated runtime home for this proof", file=sys.stderr)
        return 2
    session = PRODUCT_SESSION_NAME
    backend = AgentBrowserBackend()
    if not backend.probe().available:
        print("agent-browser is not available; the proof needs the real transport",
              file=sys.stderr)
        return 2
    manager = SessionManager(session_state_dir(), backend=backend)

    launched = manager.ensure(task="liveness-proof")
    ready = manager.discover()
    # out-of-band death: close the browser behind the product's back
    _close_session(session)
    after_close = manager.discover()
    state_after_close = json.loads(
        (session_state_dir() / "browser-session.json").read_text(encoding="utf-8"))
    counted = manager.ensure(task="liveness-proof-2")
    # the measured per-call relaunch: a transport call on a dead session
    _close_session(session)
    tabs_seen = backend.tabs(session=session)
    manager.record_observed_relaunch(
        session_id=session, detail="live proof: tab list auto-launched the browser")

    try:
        checks = {
            "ensure_launches_and_counts": launched.get("action") == "launched"
            and launched.get("starts") == 1,
            "live_session_reports_ready": ready.get("state") == "ready"
            and ready.get("live") is True,
            "dead_session_not_a_bare_ready": after_close.get("state") == "unknown"
            and after_close.get("live") is False
            and bool(after_close.get("diagnosis")),
            "state_file_records_reconciliation":
                state_after_close.get("reconciled_dead_session") == session,
            "ensure_counts_its_own_launch": counted.get("action") == "launched"
            and counted.get("starts") == 2,
            "relaunch_surfaced_by_transport": tabs_seen.get("relaunched") is True,
            "relaunch_counted_in_state_file":
                manager.discover().get("starts") == 3,
        }
        print(json.dumps({"checks": checks}, sort_keys=True))
        return 0 if all(checks.values()) else 1
    finally:
        # cleanup: only our own named session — never `close --all`
        _close_session(session)


if __name__ == "__main__":
    sys.exit(main())
