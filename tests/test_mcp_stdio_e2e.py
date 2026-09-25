"""MCP over a real stdio process (spec E2E): start → discover → invoke → restart.

Unlike ``test_mcp_server.py`` (in-memory fastmcp client), this drives the
shipped server the way Hermes would: a **separate OS process** speaking
newline-delimited JSON-RPC on stdin/stdout.
"""

import json
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from skaldir.toolbuilder import ToolBuilder, ToolBuildRequest
from skaldir.tools import ToolSpec, ToolStore

pytest.importorskip("fastmcp", reason="MCP-Tests brauchen das Extra: uv pip install 'godsv[mcp]'")

SOURCE = (
    '"""Double a number over stdio."""\n'
    "\n"
    "\n"
    "def main(n: int = 2) -> dict:\n"
    '    return {"doubled": n * 2}\n'
)
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "gv-e2e", "version": "0.1"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def _spawn(db: Path, project: Path | None = None) -> subprocess.Popen:
    cmd = [sys.executable, "-m", "skaldir", "mcp", "serve", "--db", str(db)]
    if project is not None:
        cmd += ["--project", str(project)]
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def _rpc(proc: subprocess.Popen, message: dict, want_id: int | None, timeout: float = 60.0) -> dict:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write((json.dumps(message) + "\n").encode())
    proc.stdin.flush()
    if want_id is None:
        return {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _w, _x = select.select([proc.stdout], [], [], 1.0)
        if not ready:
            continue
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP-Server hat stdout geschlossen")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue  # e.g. banner lines
        if payload.get("id") == want_id:
            return payload
    raise TimeoutError(f"keine Antwort auf id={want_id}")


def _handshake(proc: subprocess.Popen) -> dict:
    init = _rpc(proc, INIT, 1)
    assert "result" in init, init
    assert init["result"].get("serverInfo"), init
    _rpc(proc, INITIALIZED, None)
    return init


def _tool_names(proc: subprocess.Popen) -> set[str]:
    listing = _rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, 2)
    assert "result" in listing, listing
    return {tool["name"] for tool in listing["result"]["tools"]}


def test_stdio_start_discover_invoke_restart(tmp_path):
    from skaldir.capability import project_id_for

    runtime = tmp_path / "godsv"
    runtime.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    project_id = project_id_for(project)  # §7: build and serve share the context
    # a real tool through the full pipeline (gates must pass first)
    build = ToolBuilder(runtime).build(
        ToolBuildRequest(
            capability="double a number over MCP stdio",
            name="doubler",
            project_id=project_id,
            source_code=SOURCE,
            module="doubler",
            entrypoint="main",
            sample_input={"n": 21},
        )
    )
    assert build.get("ok"), build
    db = runtime / "tools.db"
    store = ToolStore(db)
    try:
        store.upsert(
            ToolSpec(
                name="draft_tool", title="Draft", module="nope",
                entrypoint="f", status="draft",
            )
        )
    finally:
        store.close()

    # start #1: discovery + filtering + invocation
    proc = _spawn(db, project=project)
    try:
        _handshake(proc)
        names = _tool_names(proc)
        assert "doubler" in names, names
        assert "draft_tool" not in names, names  # only approved tools are exported
        call = _rpc(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "doubler", "arguments": {"arguments": {"n": 5}}},
            },
            3,
        )
        assert "result" in call, call
        assert "10" in json.dumps(call["result"]), call
    finally:
        proc.kill()
        proc.wait(timeout=15)

    # restart: a fresh process still registers the tool (registry, not memory)
    proc2 = _spawn(db, project=project)
    try:
        _handshake(proc2)
        assert "doubler" in _tool_names(proc2)
    finally:
        proc2.kill()
        proc2.wait(timeout=15)
