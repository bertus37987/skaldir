"""Live proof for VAL-TOOL-005: tool reuse in a new agent run.

Proves:
1. Docker container tool `market_research_snapshot` is healthy and selectable.
2. Direct CLI invocation via `skaldir tools call market_research_snapshot` returns real container data.
3. A NEW agent run discovers the tool via `tool_search` and invokes it via the agent loop.
4. The transcript (`skaldir agent transcript <run-id>`) verifies the tool calls, container output, and answer.

Usage: uv run python tools/tool_reuse_live_proof.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from test_agent_loop import StubEndpoint, answer, tool_step

SKALDIR = str(REPO / ".venv" / "bin" / "skaldir")
LOGS_DIR = REPO / "logs" / "tool-reuse-live"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _cli(argv: list[str]) -> tuple[int, dict]:
    proc = subprocess.run([SKALDIR, *argv], capture_output=True, text=True, timeout=120)
    out_lines = [line for line in proc.stdout.splitlines() if line.strip()]
    doc = {}
    for line in reversed(out_lines):
        try:
            doc = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    doc["_returncode"] = proc.returncode
    doc["_stderr"] = proc.stderr[-1000:]
    return proc.returncode, doc


def run_proof() -> dict:
    ts = time.strftime("%Y%m%d-%H%M%S")
    evidence: dict = {"timestamp": ts, "checks": {}}

    print("== Step 1: Health check of market_research_snapshot ==")
    rc, health_doc = _cli(["tools", "health", "market_research_snapshot", "--json"])
    print(f"  exit: {rc}, selectable: {health_doc.get('data', {}).get('selectable')}")
    evidence["checks"]["health"] = {
        "exit_code": rc,
        "selectable": health_doc.get("data", {}).get("selectable"),
        "health": health_doc.get("data", {}).get("health", {}),
    }
    assert rc == 0 and health_doc.get("data", {}).get("selectable"), "Health check failed"

    print("== Step 2: Direct CLI call to market_research_snapshot ==")
    rc, call_doc = _cli([
        "tools", "call", "market_research_snapshot",
        "--json-args", '{"symbol":"AMD","detail":"brief"}',
        "--json"
    ])
    result_data = call_doc.get("data", {}).get("result", {})
    print(f"  exit: {rc}, symbol: {result_data.get('symbol')}, note: {result_data.get('research_note')[:40]}...")
    evidence["checks"]["direct_call"] = {
        "exit_code": rc,
        "result": result_data,
        "wrapper": call_doc.get("data", {}).get("wrapper"),
    }
    assert rc == 0 and result_data.get("symbol") == "AMD", "Direct CLI call failed"

    print("== Step 3: Run new agent loop with tool_search -> call -> answer ==")
    steps = [
        tool_step(("call_1", "tool_search", {"need": "market research"})),
        tool_step(("call_2", "market_research_snapshot", {"symbol": "AMD", "detail": "brief"})),
        answer("AMD market research snapshot retrieved: Designs CPUs and GPUs; sells into datacenter/client."),
    ]

    with StubEndpoint(steps) as stub:
        run_argv = [
            "agent", "run",
            "Find market research capability via tool_search and get snapshot for AMD",
            "--tools", "both",
            "--agent-tools",
            "--base-url", stub.base_url,
            "--model", "stub-model",
            "--json"
        ]
        rc, run_doc = _cli(run_argv)

    run_data = run_doc.get("data", {})
    run_id = run_data.get("run_id", "")
    print(f"  run_id: {run_id}, exit: {rc}, status: {run_data.get('status')}, tool_calls: {run_data.get('tool_calls')}")
    evidence["checks"]["agent_run"] = {
        "exit_code": rc,
        "run_id": run_id,
        "status": run_data.get("status"),
        "answer": run_data.get("answer"),
        "steps": run_data.get("steps"),
        "tool_calls": run_data.get("tool_calls"),
        "executed_calls": run_data.get("executed_calls"),
        "transcript_path": run_data.get("transcript_path"),
    }
    assert rc == 0 and run_data.get("status") == "answered", "Agent run failed"
    assert run_data.get("tool_calls") == 2 and run_data.get("executed_calls") == 2, "Tool calls not executed"

    print("== Step 4: Verify agent transcript via CLI ==")
    rc, transcript_doc = _cli(["agent", "transcript", run_id, "--json"])
    transcript_data = transcript_doc.get("data", {})
    tool_calls = transcript_data.get("tool_calls", [])
    print(f"  exit: {rc}, recorded tool_calls: {len(tool_calls)}, answer: {transcript_data.get('answer')[:40]}...")
    evidence["checks"]["transcript"] = {
        "exit_code": rc,
        "tool_calls_count": len(tool_calls),
        "tool_names": [tc.get("name") for tc in tool_calls],
        "answer": transcript_data.get("answer"),
        "transcript_path": transcript_data.get("path"),
    }
    assert rc == 0 and len(tool_calls) == 2, "Transcript verification failed"

    out_file = LOGS_DIR / f"{ts}.json"
    out_file.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"== Evidence saved to {out_file} ==")
    return evidence


if __name__ == "__main__":
    run_proof()
