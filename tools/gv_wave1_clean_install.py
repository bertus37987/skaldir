#!/usr/bin/env python
"""Wave 1 proof: a clean install into a FRESH HERMES_HOME leaves a working entry.

Checks, in order (each one prints a real measurement, never an assumption):
  1. install() deploys the panel and leaves the plugin enabled (host CLI, verified by reading back)
  2. the host admission check passes on the installed plugin dir (engine interpreter, no PYTHONPATH)
  3. the engine starts with that home: plugin loads, no load errors, vendor routes mounted
  4. the plugin's own trace shows module import + register() in the engine process
  5. the panel API answers on the live route (status code is the evidence)

Everything happens under a throwaway home + XDG dirs, so the developer's install is untouched.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENGINE = Path.home() / ".hermes" / "hermes-agent"
ENGINE_PY = ENGINE / "venv" / "bin" / "python"
RESULTS: list[dict] = []


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append({"check": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def base_env(home: Path, xdg: Path) -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"PYTHONPATH", "HERMES_HOME", "GODSV_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME"}
    }
    env.update(
        HERMES_HOME=str(home),
        XDG_DATA_HOME=str(xdg / "data"),
        XDG_CONFIG_HOME=str(xdg / "config"),
        GODSV_MODE="whisper",
        GODSV_DEBUG_TRACE="1",
    )
    return env


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="godsv-clean-install-"))
    home = tmp / "hermes-home"
    xdg = tmp / "xdg"
    home.mkdir(parents=True, exist_ok=True)
    env = base_env(home, xdg)
    trace = tmp / "plugin-trace.log"
    env["GODSV_TRACE_FILE"] = str(trace)
    print(f"clean home : {home}")

    # 1) install ---------------------------------------------------------------------------
    code = "from godsv import desktop; import json; print(json.dumps(desktop.install(link_cli=False)))"
    done = subprocess.run(
        [str(REPO / ".venv" / "bin" / "python"), "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO,
        env=env,
        timeout=300,
    )
    try:
        report = json.loads(done.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        report = {}
    record(
        "install() deploys the panel link",
        bool(report.get("panel_link")),
        str(report.get("panel_link") or done.stderr[-200:]),
    )
    state = report.get("plugin_state") or {}
    record(
        "install() leaves the plugin ENABLED (host CLI, verified)",
        state.get("enabled") is True,
        json.dumps({"action": report.get("plugin_action"), "state": state}, ensure_ascii=False),
    )

    # 2) host admission check --------------------------------------------------------------
    done = subprocess.run(
        [str(ENGINE_PY), str(REPO / "tools" / "gv_plugin_validate.py"), str(home / "plugins" / "godsv")],
        capture_output=True,
        text=True,
        env={k: v for k, v in env.items() if k != "PYTHONPATH"},
        timeout=300,
    )
    ok = "ok         : True" in done.stdout
    record("plugin passes the host admission check", ok, done.stdout.strip().splitlines()[-1] if done.stdout else done.stderr[-200:])

    # 3+4) the engine starts with this home ------------------------------------------------
    port = free_port()
    log = tmp / "serve.log"
    handle = log.open("wb")
    proc = subprocess.Popen(
        [str(ENGINE_PY), str(ENGINE / "hermes"), "serve", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(REPO),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    home_log = home / "logs" / "agent.log"
    ready = False
    deadline = time.time() + 60
    while time.time() < deadline:
        time.sleep(1.5)
        stdout_text = log.read_text(errors="ignore") if log.exists() else ""
        # the backend announces readiness itself (`HERMES_BACKEND_READY port=…`); an invented
        # marker would have made this check pass or fail for the wrong reason (measured once)
        if f"HERMES_BACKEND_READY port={port}" in stdout_text or "backend listening on" in stdout_text:
            ready = True
            break
        if proc.poll() is not None:
            break
    record("engine starts and serves", ready, f"pid={proc.pid} port={port} rc={proc.poll()}")
    # engine-side log lines live in $HERMES_HOME/logs/agent.log, not on stdout
    engine_log = home_log.read_text(errors="ignore") if home_log.exists() else ""
    load_errors = [ln for ln in engine_log.splitlines() if "Failed to load plugin" in ln]
    record("no plugin load errors at startup", not load_errors, load_errors[0][:160] if load_errors else "none")
    mounted = [ln for ln in engine_log.splitlines() if "Mounted plugin API routes: /api/plugins/godsv/" in ln]
    record("godsv plugin API routes mounted", bool(mounted), mounted[0].split("INFO")[-1].strip() if mounted else "not mounted")

    time.sleep(2)
    trace_lines = trace.read_text(errors="ignore").splitlines() if trace.exists() else []
    mine = [ln for ln in trace_lines if f"pid={proc.pid}" in ln]
    record(
        "plugin imported + register() ran inside the ENGINE process",
        any("module imported" in ln for ln in mine) and any("register()" in ln for ln in mine),
        (mine[0].split(" ", 2)[-1] if mine else "no trace line from the engine pid"),
    )

    # 5) the panel API answers -------------------------------------------------------------
    if ready:
        answers = {}
        for route in ("/health", "/summary", "/tools", "/events"):
            url = f"http://127.0.0.1:{port}/api/plugins/godsv{route}"
            try:
                with urllib.request.urlopen(url, timeout=10) as res:  # noqa: S310 — loopback
                    answers[route] = res.status
            except urllib.error.HTTPError as exc:
                answers[route] = exc.code
            except Exception as exc:  # noqa: BLE001
                answers[route] = type(exc).__name__
        reachable = all(isinstance(v, int) and v < 500 for v in answers.values())
        record(
            "panel routes answer without a server error",
            reachable,
            json.dumps(answers),
        )
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass

    failures = [item for item in RESULTS if not item["ok"]]
    print(json.dumps({"home": str(home), "checks": len(RESULTS), "failed": len(failures)}, indent=1))
    if not failures:
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print(f"kept for inspection: {tmp}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
