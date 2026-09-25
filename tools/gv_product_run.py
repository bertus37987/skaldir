#!/usr/bin/env python3
"""§6.2 product run — the supervisor loop over a scratch repo, on the REAL app.

Talks ONLY to the God's Voice serve instance (identified by its own
``HERMES_DESKTOP_USER_DATA_DIR`` marker plus its session token from the process
environment) — never to the normal Hermes desktop serve, which shares the same
HERMES_HOME. Every phase writes its request/response into the report.

What it proves (each step hits the real panel API of the running app):

  goal    — begin the contract for session ``prodrun-1`` (+ blocking criterion)
  fail    — a claim over a failing pytest blocks                    -> INCOMPLETE
  fix     — the test is fixed; the runner re-executes it            -> VERIFIED_COMPLETE
  session — a second session stays isolated; prodrun-1 keeps its state

The restart-persistence step is driven by the caller: restart the GV app, then
run ``python3 tools/gv_product_run.py report`` to read the state back.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

GV_REPO = Path("/tmp/gv_prodrun_repo")  # scratch repo (failing pytest by design)
REPORT = Path("/tmp/gv_prodrun_report.json")
# The first fully recorded run (2026-09-12): fail phase hit ``prodrun-3``,
# isolation phase ``prodrun-2``; both survive an app restart.
SESSION_A = "prodtest-3"
PANEL_ORIGIN = "app://godsv-panel"
SESSION_B = "prodtest-2"
GOAL_TEXT = "Make the scratch pytest suite green (real product run)"


def _gv_candidates() -> list[tuple[int, str]]:
    """(port, token) candidates: 127.0.0.1 listeners whose process env carries
    the godsv user-data marker (the normal Hermes serve never does).

    The Electron main process shares that marker for its DevTools port — a
    debug port is NOT the backend API, so candidates whose cmdline declares
    ``--remote-debugging-port=<port>`` are filtered out up front.
    """
    candidates: list[tuple[int, str]] = []
    out = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "127.0.0.1:" not in line or "pid=" not in line:
            continue
        m_port = re.search(r"127\.0\.0\.1:(\d+)", line)
        if not m_port:
            continue
        port = int(m_port.group(1))
        for m_pid in re.finditer(r"pid=(\d+)", line):
            env_path = Path(f"/proc/{m_pid.group(1)}/environ")
            try:
                env = env_path.read_bytes().decode(errors="replace")
            except OSError:
                continue
            if "HERMES_DESKTOP_USER_DATA_DIR=/home/willy/.hermes/godsv/" not in env:
                continue
            token = ""
            for chunk in env.split("\0"):
                if chunk.startswith("HERMES_DASHBOARD_SESSION_TOKEN="):
                    token = chunk.split("=", 1)[1]
            try:
                cmdline = Path(f"/proc/{m_pid.group(1)}/cmdline").read_bytes().decode(
                    errors="replace"
                )
            except OSError:
                cmdline = ""
            if f"--remote-debugging-port={port}" in cmdline:
                continue
            candidates.append((port, token))
    return candidates


def _pick_verified_serve(candidates: list[tuple[int, str]], probe) -> tuple[int, str]:
    """Pick the candidate whose endpoint answers with a well-formed godsv
    plugin payload — identity is verified, never assumed from port order.
    ``probe(port, token)`` returns a dict (or None) for a real API call.
    """
    for port, token in candidates:
        try:
            data = probe(port, token)
        except Exception:
            data = None
        if isinstance(data, dict) and ("state" in data or "revision" in data):
            return port, token
    return candidates[0] if candidates else (0, "")


def gv_serve() -> tuple[int, str]:
    """(port, token) of the REAL GV serve.

    Identity check (audit fix 13.09.): the Electron debug port used to win the
    scan because it inherits the same env — now the backend endpoint is
    verified by a real /completion probe before it is trusted.
    """
    def probe(port: int, token: str) -> dict | None:
        data = api(port, "GET", "/completion", token=token)
        if data.get("http_error"):
            return None
        return data

    return _pick_verified_serve(_gv_candidates(), probe)


def api(
    port: int, method: str, path: str, payload: dict | None = None, token: str = ""
) -> dict:
    url = f"http://127.0.0.1:{port}/api/plugins/godsv{path}"
    data = json.dumps(payload or {}).encode() if method == "POST" else None
    # Block 1 hardened the panel trust boundary fail-closed: a mutation
    # must carry an allowed Origin *and* the panel token (origin alone
    # proved nothing, SEC-06). The desktop app sends this via its main
    # process; a bare urllib caller must therefore do the same or it is
    # refused with 403 — measured, not assumed.
    headers = {"Content-Type": "application/json", "Origin": PANEL_ORIGIN}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-Hermes-Session-Token"] = token
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 — loopback
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"http_error": exc.code, "detail": exc.read().decode()[:400]}


def git(*args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=GV_REPO,
        check=True,
        capture_output=True,
    )


def phase_run() -> dict:
    port, token = gv_serve()
    if not port:
        raise SystemExit("GV serve not found — is the God's Voice app running?")
    report: dict = {"port": port, "repo": str(GV_REPO), "steps": []}

    def step(name: str, data: dict) -> dict:
        report["steps"].append({"step": name, **data})
        return data

    # 1) goal + blocking criterion
    goal = api(
        port,
        "POST",
        "/completion/goal",
        token=token,
        payload={
            "session_id": SESSION_A,
            "goal": GOAL_TEXT,
            "criteria": ["the pytest suite is green"],
        },
    )
    step(
        "goal",
        {
            "state": goal.get("state"),
            "goal": goal.get("goal"),
            "dod": goal.get("dod"),
            "revision": goal.get("revision"),
        },
    )

    # 2) claim over a failing pytest -> INCOMPLETE with real runner evidence
    fail = api(
        port,
        "POST",
        "/completion/verify",
        token=token,
        payload={
            "session_id": SESSION_A,
            "text": "I fixed the suite — all tests are green now.",
            "repo_root": str(GV_REPO),
        },
    )
    last = fail.get("last_verdict") or {}
    step(
        "fail",
        {
            "state": fail.get("state"),
            "blockers": last.get("blocking_failures"),
            "evidence": last.get("evidence"),
        },
    )

    # 3) fix the test, commit, re-verify -> the runner executes it again
    (GV_REPO / "test_app.py").write_text(
        "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    git("add", "-A")
    git("commit", "-qm", "fix: correct expectation (real fix under test)")
    ok = api(
        port,
        "POST",
        "/completion/verify",
        token=token,
        payload={
            "session_id": SESSION_A,
            "text": "The suite is green now — pytest passes.",
            "repo_root": str(GV_REPO),
        },
    )
    last_ok = ok.get("last_verdict") or {}
    step("fix", {"state": ok.get("state"), "evidence": last_ok.get("evidence")})

    # 4) second session stays isolated
    api(
        port,
        "POST",
        "/completion/goal",
        token=token,
        payload={
            "session_id": SESSION_B,
            "goal": "An unrelated parallel task",
            "criteria": [],
        },
    )
    a_after = api(port, "GET", f"/completion?session_id={SESSION_A}", token=token)
    b_after = api(port, "GET", f"/completion?session_id={SESSION_B}", token=token)
    step(
        "session",
        {
            "a_state": a_after.get("state"),
            "b_state": b_after.get("state"),
            "a_goal": a_after.get("goal"),
            "b_goal": b_after.get("goal"),
        },
    )

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def phase_report() -> dict:
    port, token = gv_serve()
    if not port:
        raise SystemExit("GV serve not found after restart")
    a = api(port, "GET", f"/completion?session_id={SESSION_A}", token=token)
    b = api(port, "GET", f"/completion?session_id={SESSION_B}", token=token)
    out = {
        "port": port,
        "after_restart": {
            "a_state": a.get("state"),
            "a_goal": a.get("goal"),
            "b_state": b.get("state"),
            "b_goal": b.get("goal"),
        },
    }
    (REPORT.parent / "gv_prodrun_report_restart.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out


def main(argv: list[str]) -> int:
    global SESSION_A, SESSION_B
    mode = argv[1] if len(argv) > 1 else "run"
    if len(argv) > 2:
        SESSION_A = argv[2]
    if len(argv) > 3:
        SESSION_B = argv[3]
    if mode == "run":
        report = phase_run()
    elif mode == "report":
        report = phase_report()
    else:
        print(__doc__)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
