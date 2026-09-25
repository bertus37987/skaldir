#!/usr/bin/env python3
"""Black-box E2E of the ``godsvoice`` CLI — real journeys, envelope + exit-code contract.

WHAT THIS IS
------------
The CLI is driven as a **process**: the console script ``.venv/bin/godsvoice`` is started
with :mod:`subprocess`, never imported. Every journey therefore proves what a user gets,
not what a unit test can reach.

Six journeys, all in a throwaway sandbox (``GODSV_HOME`` + ``cwd`` both inside it, so no
journey touches the owner's runtime):

1. **provider** — a stdlib OpenAI-compatible stub on ``127.0.0.1`` is configured through
   the reachable contract (``GODSV_LLM_*`` env + ``settings.yaml``) and the CLI really
   talks to it: ``jev decide`` returns an accepted advisory, and the stub's request log
   proves the HTTP round trip. Negative controls: a malformed provider answer stays
   ``blocked``/exit 3, a missing answer is a contract error/exit 7, a rejected credential
   is exit 4 — never a fabricated success.
2. **goal** — ``completion begin`` records the goal and returns the honest exit 3 ("no
   execution loop is wired"), ``completion status`` / ``completion history`` agree.
3. **verification** — ``verify plan/run/status/evidence`` is wired into the CLI and
   answers; ``selftest`` still exits 2 while a layer is not reachable and names a
   ``blocked_reason`` for each one (in the sandbox runtime PERSIST/COMPOSE are honestly
   unreachable — there is no tools/runs db yet). ``doctor`` and ``eval`` are exercised too,
   and ``eval`` still declares itself in-process.
4. **capability** — mine → register → call, twice over:
   (a) **container**: ``tools mine --image alpine:3.20`` inspects the surface *inside* the
   container, generates a typed adapter and tests it against the container with an
   independent oracle; ``tools register`` puts it live (version, health, permissions,
   rollback snapshot); ``tools adapter show`` / ``tools health --probe`` inspect it; the
   generated wrapper is called and its answer is compared with a ``docker exec`` oracle;
   ``tools rollback`` retires it and sweeps containers by label scope.
   (b) **plain**: ``tools request`` x2 → ``tools mine`` → ``tools candidates`` →
   ``tools promote`` → ``tools build`` (four gates really run) → ``tools list`` /
   ``tools show`` → the wrapper **and** ``godsvoice mcp serve`` ``tools/call``.
5. **compose** — ``tools compose`` ranks candidates AND the ``compose`` group
   (create/validate/run/show) is wired and declared in the contract; an unknown composite
   is exit 5, never a faked verdict.
6. **credentials** — ``vault add``/``vault list`` with a throwaway master key: handles
   only, the secret never appears in the envelope or on disk.

CONTRACT CHECKS (every call, no exceptions)
-------------------------------------------
* the envelope schema (12 keys), ``schema_version``, ``command``, exit code type;
* ``ok`` ⇔ ``exit_code == 0`` and ``error is None`` ⇔ ``ok``;
* ``evidence`` non-empty whenever ``ok`` is true ("no success without evidence");
* ``data is None`` ⇔ the command's ``contract dump`` entry says ``data_available: false``;
* **exit-code drift**: the observed exit code must appear in the command's declared
  ``exit_codes`` list from ``godsvoice contract dump --json``. A command that returns
  something else has shipped a contract change without saying so.

THE HTTP SURFACE (read this before trusting the Bruno run)
----------------------------------------------------------
The CLI has a real HTTP surface: ``src/skaldir/api_server.py`` (one route per command,
``/cli/<command>`` with ``.`` → ``/``, plus ``/health`` and ``/openapi.json`` and the
``x-godsv-*`` headers), reachable as ``skaldir serve --http`` once ``api_server_cli`` is
wired into ``cli.py`` — this run starts it with the console script when it is registered
and otherwise with the module's own documented wiring snippet
(:data:`API_SERVE_WIRING`). The Bruno collection runs against *that*, and only when it
cannot come up does the runner fall back to a **stand-in shim**
(:data:`SHIM_SERVER_SOURCE`, stdlib, test-only, never in ``src/``) answering the same
routes, the same status mapping and the same headers, so the collection is unchanged. The
assertions are on the CLI's envelope either way. Which transport answered, and how it was
started, is recorded in the report (``http_surface``) — never assumed.

WHAT THIS CANNOT PROVE (recorded, never silently dropped)
---------------------------------------------------------
* There is **no `tools call` verb**: a registered capability is called through its
  generated wrapper (documented by ``tools register`` itself) or through ``mcp serve``
  (python tools only — the container kind is not exported there). The report's
  ``container_capability`` note names exactly which surface was used.
* **ACT / SUPERVISE / COMPOSE-as-a-tool** now have a CLI surface (``act``, ``voice``,
  ``compose``); what this suite still does not do is drive a live desktop or a container
  GUI — the layer table reports the measured ``selftest`` verdict instead of an invented
  test.

USAGE
-----
    .venv/bin/python tools/gv_cli_e2e.py                  # full run (containers, ~1-2 min)
    .venv/bin/python tools/gv_cli_e2e.py --no-containers  # skip the docker journey
    .venv/bin/python tools/gv_cli_e2e.py --json           # machine-readable report
    .venv/bin/python tools/gv_cli_e2e.py --work-dir DIR   # keep the sandbox + artifacts

Exit code: 0 = every journey and every contract check held · 1 = at least one failed ·
3 = could not run (no CLI binary / no scratch space).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GODSVOICE = ROOT / ".venv" / "bin" / "godsvoice"

#: the one envelope every `--json` command must print (cli_contract.build_envelope)
ENVELOPE_KEYS = (
    "schema_version", "ok", "command", "godsvoice_version", "started_at", "duration_ms",
    "exit_code", "data", "error", "evidence", "warnings", "next_actions",
)

#: the product's exit-code vocabulary (cli_contract.EXIT_NAMES)
EXIT_NAMES = {
    0: "ok", 1: "usage", 2: "verification failed", 3: "blocked", 4: "needs credentials",
    5: "not found", 6: "conflict", 7: "internal", 8: "unknown/timeout",
}

#: this wave's labels.
#:
#: ``godsv.test_wave`` names the block; ``godsv.e2e_run`` is per process. Both are used for
#: every sweep, because parallel waves (and two full-suite runs at once) execute this file at
#: the same time on one daemon: with a shared label one run would happily remove another run's
#: fixture container mid-journey. The pair is unique to this process, so no sweep can see a
#: sibling's container — and the multi-label scope is the shape ``docker_runtime`` fixed.
WAVE_LABEL_KEY = "godsv.test_wave"
WAVE_LABEL_VALUE = "gv-cli-e2e-api"
WAVE_LABEL = f"{WAVE_LABEL_KEY}={WAVE_LABEL_VALUE}"
RUN_LABEL_KEY = "godsv.e2e_run"
RUN_LABEL_VALUE = str(os.getpid())
RUN_LABEL = f"{RUN_LABEL_KEY}={RUN_LABEL_VALUE}"
WAVE_SCOPE = (WAVE_LABEL, RUN_LABEL)
OWNER_LABEL = "godsv.owner=skaldir"
FOREIGN_SCOPE = "com.docker.compose.project=penpot-local"


#: the tiny local image the capability's answer is derived from (already on this host)
IMAGE = "alpine:3.20"

DEFAULT_TIMEOUT = 120.0

# --------------------------------------------------------------------------- #
# the two throwaway servers (stdlib only, generated at run time)
# --------------------------------------------------------------------------- #

STUB_SERVER_SOURCE = r'''"""Throwaway OpenAI-compatible stub. Counts and logs every request.

The behaviour can be switched while running through ``STUB_CONTROL`` (a JSON file with
``{"mode": ..., "answer": ...}``), so one stub serves every provider journey.
"""
import json, os, re, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = os.environ["STUB_LOG"]
MODE = os.environ.get("STUB_ANSWER_MODE", "registry")
ANSWER = json.loads(os.environ.get("STUB_ANSWER_JSON") or "{}")
STATE = {"n": 0}
LOCK = threading.Lock()
_QUESTIONS = re.compile(r"^QUESTIONS:\s*$", re.MULTILINE)


def _control():
    path = os.environ.get("STUB_CONTROL")
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return str(data.get("mode") or MODE), data.get("answer", ANSWER)
        except Exception:
            pass
    return MODE, ANSWER


def _answer_for(question):
    kind = question.get("kind")
    if kind == "choice":
        options = [str(o) for o in (question.get("options") or ["unknown"])]
        share = round(1.0 / len(options), 4)
        return {"choice": options[0],
                "probabilities": {o: share for o in options}, "confidence": 0.4}
    if kind == "score":
        legend = list(question.get("legend") or [0.0])
        share = round(1.0 / len(legend), 4)
        return {"score": legend[0], "legend": legend,
                "probabilities": {str(v): share for v in legend}, "confidence": 0.4}
    return {"value": 0.5}


def _registry_answers(prompt):
    match = _QUESTIONS.search(prompt)
    if not match:
        return {}
    answers, started = {}, False
    for line in prompt[match.end():].splitlines():
        line = line.strip()
        if not line:
            if started:
                break
            continue
        if not line.startswith("{"):
            if started:
                break
            continue
        started = True
        try:
            question = json.loads(line)
        except json.JSONDecodeError:
            break
        qid = question.get("id")
        if isinstance(qid, str) and qid:
            answers[qid] = _answer_for(question)
    return answers


def _content_for(body, mode, answer):
    if mode != "registry":
        return json.dumps(answer)
    prompt = ""
    for message in body.get("messages") or []:
        if message.get("role") == "user":
            prompt = str(message.get("content") or "")
    return json.dumps({"answers": _registry_answers(prompt)})


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/health"):
            self._send(200, {"ok": True, "requests": STATE["n"], "mode": MODE})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        size = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(size) if size else b""
        try:
            body = json.loads(raw.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError:
            body = {}
        authenticated = bool(self.headers.get("Authorization"))
        mode, answer = _control()
        with LOCK:
            STATE["n"] += 1
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "seq": STATE["n"], "path": self.path, "auth": authenticated,
                    "model": body.get("model"),
                    "body": raw.decode("utf-8", "replace"),
                }) + "\n")
        if not self.path.endswith("/chat/completions"):
            return self._send(404, {"error": {"message": "unknown path"}})
        if mode == "auth_required" and not authenticated:
            return self._send(401, {"error": {"message":
                "Authentication Fails (governor)", "type": "invalid_request_error"}})
        self._send(200, {
            "id": "stub-1", "object": "chat.completion", "created": 0,
            "model": body.get("model") or "stub-model",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant",
                                     "content": _content_for(body, mode, answer)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H)
    print("ready %s mode=%s" % (sys.argv[1], MODE), flush=True)
    server.serve_forever()
'''

SHIM_SERVER_SOURCE = r'''"""Read-only HTTP stand-in for the product API, used only when it cannot be started.

It mimics ``src/godsv/api_server.py`` on purpose, so ONE Bruno collection runs against
either target:

* ``/health`` answers the same envelope shape (``command: "api.health"``, ``data.routes``);
* ``/cli/<command>`` runs the real console script and returns the envelope it wrote;
* the HTTP status is ``HTTP_STATUS_BY_EXIT[envelope.exit_code]`` — read from the product's
  own constant, never a hand-copied table;
* ``x-godsv-command`` / ``x-godsv-exit-code`` / ``x-godsv-schema-version`` mirror the body;
* an unknown route answers 404 with a ``not_found`` envelope, exactly like the product.
"""
import json, os, subprocess, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROUTES = json.loads(os.environ["SHIM_ROUTES"])          # route -> argv list
GODSVOICE = os.environ["SHIM_GODSVOICE"]
CWD = os.environ["SHIM_CWD"]
STATUS_BY_EXIT = {int(k): int(v) for k, v in json.loads(os.environ["SHIM_STATUS_BY_EXIT"]).items()}
ENV = dict(os.environ)
for key in ("SHIM_ROUTES", "SHIM_GODSVOICE", "SHIM_CWD", "SHIM_STATUS_BY_EXIT"):
    ENV.pop(key, None)


def _status(exit_code):
    return STATUS_BY_EXIT.get(int(exit_code), 500)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, status, envelope, extra=None):
        payload = json.dumps(envelope).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.rstrip("/") in ("", "/health"):
            return self._send(200, {
                "schema_version": "1.0", "ok": True, "command": "api.health",
                "godsvoice_version": "1.5.0", "started_at": "", "duration_ms": 0,
                "exit_code": 0,
                "data": {"status": "ok", "routes": len(ROUTES), "stand_in": True,
                         "http_status_by_exit": {str(k): v for k, v in sorted(STATUS_BY_EXIT.items())}},
                "error": None, "evidence": [{"ref": "tools/gv_cli_e2e.py", "kind": "code",
                                             "note": "test-only stand-in for the product API"}],
                "warnings": [], "next_actions": []})
        argv = ROUTES.get(self.path.rstrip("/") or self.path)
        if not argv:
            return self._send(404, {
                "schema_version": "1.0", "ok": False, "command": "does.not.exist",
                "godsvoice_version": "1.5.0", "started_at": "", "duration_ms": 0,
                "exit_code": 5,
                "data": None,
                "error": {"code": "not_found", "message": "no such command route",
                          "hint": "see GET /openapi.json", "retryable": False, "details": {}},
                "evidence": [], "warnings": [], "next_actions": []},
                {"x-godsv-command": "does.not.exist", "x-godsv-exit-code": "5",
                 "x-godsv-schema-version": "1.0"})
        proc = subprocess.run([GODSVOICE, *argv, "--json"], cwd=CWD, env=ENV,
                              capture_output=True, text=True, timeout=300)
        if not proc.stdout.strip():
            return self._send(502, {
                "schema_version": "1.0", "ok": False, "command": " ".join(argv),
                "godsvoice_version": "1.5.0", "started_at": "", "duration_ms": 0,
                "exit_code": 7, "data": None,
                "error": {"code": "internal", "message": "the CLI produced no envelope",
                          "hint": "", "retryable": False,
                          "details": {"stderr": proc.stderr[-400:]}},
                "evidence": [], "warnings": [], "next_actions": []})
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return self._send(502, {"schema_version": "1.0", "ok": False, "command": " ".join(argv),
                                    "godsvoice_version": "1.5.0", "started_at": "", "duration_ms": 0,
                                    "exit_code": 7, "data": None,
                                    "error": {"code": "internal",
                                              "message": "stdout was not one JSON document",
                                              "hint": "", "retryable": False, "details": {}},
                                    "evidence": [], "warnings": [], "next_actions": []})
        return self._send(_status(envelope.get("exit_code", 7)), envelope, {
            "x-godsv-command": str(envelope.get("command") or ""),
            "x-godsv-exit-code": str(envelope.get("exit_code")),
            "x-godsv-schema-version": str(envelope.get("schema_version") or ""),
        })


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H)
    print("ready %s" % sys.argv[1], flush=True)
    server.serve_forever()
'''

#: the Bruno collection the HTTP surface is addressed with (generated, not committed).
#:
#: Written against the **product API's** contract (`src/godsv/api_server.py`): the route
#: spelling is ``/cli/<command>`` with ``.`` → ``/``, the HTTP status is
#: ``HTTP_STATUS_BY_EXIT[exit_code]``, ``/health`` answers an envelope, and the
#: ``x-godsv-`` headers mirror the body. The stand-in shim answers the same way, so this
#: collection is the same artefact for either target.
BRUNO_COLLECTION_JSON = """{
  "version": "1",
  "name": "skaldir-cli-envelope",
  "type": "collection",
  "ignore": ["node_modules", ".git"]
}
"""

BRUNO_ENTRIES: dict[str, str] = {
    "01-health.bru": """meta {
  name: 01 health
  type: http
  seq: 1
}

get {
  url: {{baseUrl}}/health
  body: none
  auth: none
}

tests {
  test("health answers 200", function() {
    expect(res.getStatus()).to.equal(200);
  });
  test("health is the one envelope", function() {
    expect(res.getBody().schema_version).to.equal("1.0");
    expect(res.getBody().ok).to.equal(true);
    expect(res.getBody().error).to.equal(null);
  });
  test("the server publishes its routes", function() {
    expect(res.getBody().data.routes).to.be.above(3);
  });
  test("the exit-code to HTTP-status mapping is published", function() {
    const map = res.getBody().data.http_status_by_exit;
    expect(map["0"]).to.equal(200);
    expect(map["2"]).to.equal(422);
    expect(map["5"]).to.equal(404);
  });
}
""",
    "02-envelope-shape.bru": """meta {
  name: 02 envelope shape
  type: http
  seq: 2
}

get {
  url: {{baseUrl}}/cli/contract/dump
  body: none
  auth: none
}

tests {
  // The body is decoded ONCE, into one local, and every test reads that local.
  // Measured (Bruno CLI 4.1.0, quickjs-wasmfile-release-sync): a fourth `res.getBody()`
  // call on this 688 KB contract dump aborts the WASM runtime with
  // `Assertion failed: list_empty(&rt->gc_obj_list), at quickjs.c:2036, JS_FreeRuntime`
  // *after* the tests passed, so `bru run` exits 1 although every assertion held. Three
  // reads are fine, four are not, and the limit is the response size (the same script
  // against /health, /cli/tools/list or /cli/registry/status never aborts). This is a
  // Bruno/quickjs bug, not a product one; one read keeps the full assertion set.
  const body = res.getBody();
  test("an ok command answers the mapped status 200", function() {
    expect(res.getStatus()).to.equal(200);
  });
  test("content type is json", function() {
    expect(res.getHeader("content-type")).to.contain("application/json");
  });
  test("schema_version is 1.0", function() {
    expect(body.schema_version).to.equal("1.0");
  });
  test("all 12 envelope keys are present", function() {
    const keys = ["schema_version","ok","command","godsvoice_version","started_at",
                  "duration_ms","exit_code","data","error","evidence","warnings",
                  "next_actions"];
    keys.forEach(function(k) {
      expect(typeof body[k]).to.not.equal("undefined");
    });
  });
  test("command is the contract dump", function() {
    expect(body.command).to.equal("contract.dump");
  });
  test("ok is true and error is null", function() {
    expect(body.ok).to.equal(true);
    expect(body.error).to.equal(null);
  });
  test("a successful envelope carries evidence", function() {
    expect(body.evidence.length).to.be.above(0);
  });
  test("the headers mirror the envelope", function() {
    expect(res.getHeader("x-godsv-command")).to.equal(body.command);
    expect(Number(res.getHeader("x-godsv-exit-code"))).to.equal(body.exit_code);
    expect(res.getHeader("x-godsv-schema-version")).to.equal(body.schema_version);
  });
}
""",
    "03-blocked-verdict.bru": """meta {
  name: 03 blocked verdict
  type: http
  seq: 3
}

get {
  url: {{baseUrl}}/cli/selftest
  body: none
  auth: none
}

tests {
  test("a blocked verdict is not hidden behind HTTP 200", function() {
    expect(res.getStatus()).to.equal(422);
  });
  test("selftest is not ok and does not pretend to be", function() {
    expect(res.getBody().ok).to.equal(false);
  });
  test("the envelope carries the CLI's own exit code", function() {
    expect(res.getBody().exit_code).to.equal(2);
  });
  test("a failed verdict names the layers that are not reachable", function() {
    expect(res.getBody().data.failed_layers.length).to.be.above(0);
  });
  test("error is present exactly because ok is false", function() {
    expect(res.getBody().error).to.not.equal(null);
  });
  test("the headers mirror the blocked verdict", function() {
    expect(Number(res.getHeader("x-godsv-exit-code"))).to.equal(res.getBody().exit_code);
    expect(res.getHeader("x-godsv-command")).to.equal(res.getBody().command);
  });
}
""",
    "04-unknown-route.bru": """meta {
  name: 04 unknown route
  type: http
  seq: 4
}

get {
  url: {{baseUrl}}/cli/does/not/exist
  body: none
  auth: none
}

tests {
  test("an unknown route answers 404", function() {
    expect(res.getStatus()).to.equal(404);
  });
  test("and reports not_found instead of inventing a result", function() {
    expect(res.getBody().ok).to.equal(false);
    expect(res.getBody().exit_code).to.equal(5);
    expect(res.getBody().error.code).to.equal("not_found");
  });
}
""",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def free_port() -> int:
    """A port that was free a moment ago (the server binds it immediately after)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_http(url: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return response.status == 200
        except Exception:
            time.sleep(0.15)
    return False


@dataclass
class Result:
    """One CLI invocation and what it produced."""

    argv: list[str]
    code: int
    stdout: str
    stderr: str
    envelope: dict | None
    parse_error: str = ""

    @property
    def command(self) -> str:
        if self.envelope:
            return str(self.envelope.get("command") or "")
        return ""

    def describe(self) -> str:
        head = " ".join(self.argv[:3])
        return f"`godsvoice {head} …` exit={self.code}"


class StubProvider:
    """The throwaway OpenAI-compatible server the provider journey talks to."""

    def __init__(self, work_dir: Path, mode: str = "registry", answer: dict | None = None):
        self.dir = work_dir / "stub"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.script = self.dir / "stub_llm.py"
        self.script.write_text(STUB_SERVER_SOURCE, encoding="utf-8")
        self.log = self.dir / "requests.jsonl"
        self.control = self.dir / "control.json"
        self.port = free_port()
        self._mode = mode
        self._answer = answer or {}
        self.proc: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def answer(self) -> dict:
        return self._answer

    def set(self, mode: str, answer: dict | None = None) -> None:
        """Switch the stub's behaviour for the next request (no restart)."""
        self._mode = mode
        self._answer = {} if answer is None else answer
        if self.proc is not None:
            self.control.write_text(json.dumps({"mode": self._mode, "answer": self._answer}),
                                    encoding="utf-8")

    def start(self) -> "StubProvider":
        env = dict(os.environ)
        env["STUB_LOG"] = str(self.log)
        env["STUB_ANSWER_MODE"] = self._mode
        env["STUB_ANSWER_JSON"] = json.dumps(self._answer)
        env["STUB_CONTROL"] = str(self.control)
        self.proc = subprocess.Popen(
            [sys.executable, str(self.script), str(self.port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )
        if not wait_for_http(f"http://127.0.0.1:{self.port}/health"):
            self.stop()
            raise RuntimeError("the stub provider did not come up")
        return self

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                self.proc.kill()

    def requests(self) -> list[dict]:
        if not self.log.exists():
            return []
        rows = []
        for line in self.log.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def reset(self) -> None:
        self.log.unlink(missing_ok=True)


class CliShim:
    """The HTTP stand-in, used only when the product API cannot be started (never a product surface)."""

    def __init__(self, work_dir: Path, cli: "Cli", routes: dict[str, list[str]],
                 status_by_exit: dict | None = None):
        self.cli = cli
        self.dir = work_dir / "shim"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.script = self.dir / "cli_shim.py"
        self.script.write_text(SHIM_SERVER_SOURCE, encoding="utf-8")
        self.routes = routes
        self.status_by_exit = status_by_exit or product_status_map()
        self.port = free_port()
        self.proc: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "CliShim":
        env = self.cli.env()
        env["SHIM_ROUTES"] = json.dumps(self.routes)
        env["SHIM_GODSVOICE"] = str(GODSVOICE)
        env["SHIM_CWD"] = str(self.cli.workspace)
        env["SHIM_STATUS_BY_EXIT"] = json.dumps(self.status_by_exit)
        self.stderr_path = self.dir / "shim.err"
        self.stderr_handle = open(self.stderr_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, str(self.script), str(self.port)],
            stdout=subprocess.DEVNULL, stderr=self.stderr_handle, env=env,
            cwd=str(self.cli.workspace),
        )
        if not wait_for_http(f"{self.base_url}/health"):
            detail = ""
            try:
                self.stderr_handle.flush()
                detail = self.stderr_path.read_text(encoding="utf-8")[-400:]
            except OSError:
                pass
            self.stop()
            raise RuntimeError(f"the CLI shim did not come up: {detail}")
        return self

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                self.proc.kill()
        handle = getattr(self, "stderr_handle", None)
        if handle and not handle.closed:
            handle.close()

    def get(self, path: str, timeout: float = 300.0) -> tuple[int, dict, str]:
        try:
            with urllib.request.urlopen(self.base_url + path, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return response.status, json.loads(body or "null"), response.headers.get(
                    "X-Godsvoice-Exit-Code", ""
                )
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "null"), ""


class Cli:
    """The black box: the real console script, a sandbox home and a sandbox cwd."""

    def __init__(self, work_dir: Path, name: str = "sandbox", env_extra: dict | None = None):
        self.work_dir = work_dir
        self.workspace = work_dir / name
        self.runtime = self.workspace / "home"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.env_extra = dict(env_extra or {})
        self.calls: list[Result] = []

    def env(self) -> dict[str, str]:
        """The CLI's environment: **every** runtime spelling is scrubbed, then pinned.

        The rename means `SKALDIR_HOME` outranks `GODSV_HOME` (and `SKALDIR_*` outranks
        `GODSV_*`), so scrubbing only the old prefix leaves a leaked `SKALDIR_HOME` from
        the caller's shell winning over the sandbox: the CLI then writes its registry,
        its generated wrappers and its capability requests into a *foreign* runtime while
        this suite looks in its own. Measured: with `SKALDIR_HOME=/tmp/coding-wire/home`
        exported, `registry status` reported that directory and the plain capability
        journey refused with "reuse instead of duplicate: 'e2e_greet' (tool_hub)" — a
        leftover from an earlier run in the foreign home.
        """
        env = dict(os.environ)
        for key in list(env):
            upper = key.upper()
            if upper.startswith(("GODSV_", "SKALDIR_")) or upper.endswith("_VAULT_KEY") \
                    or key in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
                env.pop(key)
        env["GODSV_HOME"] = str(self.runtime)
        env["SKALDIR_HOME"] = str(self.runtime)
        env.update(self.env_extra)
        return env

    def run(self, *argv: str, stdin: str | None = None, timeout: float = DEFAULT_TIMEOUT,
            json_mode: bool = True, expect_envelope: bool = True) -> Result:
        args = list(argv)
        if json_mode and "--json" not in args:
            args.append("--json")
        proc = subprocess.run(
            [str(GODSVOICE), *args], cwd=str(self.workspace), env=self.env(),
            input=stdin, capture_output=True, text=True, timeout=timeout,
        )
        envelope = None
        parse_error = ""
        if expect_envelope:
            try:
                parsed = json.loads(proc.stdout)
                envelope = parsed if isinstance(parsed, dict) else None
                if envelope is None:
                    parse_error = f"stdout was {type(parsed).__name__}, not one object"
            except json.JSONDecodeError as exc:
                parse_error = f"stdout is not one JSON document: {exc}"
        result = Result(args, proc.returncode, proc.stdout, proc.stderr, envelope, parse_error)
        self.calls.append(result)
        return result


# --------------------------------------------------------------------------- #
# contract + envelope checks
# --------------------------------------------------------------------------- #

def contract_specs(cli: Cli) -> dict[str, dict]:
    """The declared contract per leaf command: `godsvoice contract dump --json`."""
    result = cli.run("contract", "dump", timeout=120)
    if result.code != 0 or not result.envelope:
        raise RuntimeError(f"contract dump failed: exit {result.code}: {result.stderr[-300:]}")
    return {row["command"]: row for row in result.envelope["data"]["commands"]}


#: A vendor hostname inside the contract payload is a smell: `contract dump` is the GUI's
#: source of truth, not a configuration file. Measured: the `provider add --base-url` help
#: carried `https://api.deepseek.com/v1`, and four `image.*` verbs named `docker.io` /
#: `mirror.gcr.io`, so the CLI's own `contract dump` output mentioned remote hosts — which
#: makes the black-box claim "the provider journey never left the host" unreadable (the
#: assertion below scans *every* emitted byte, including stderr).
HOSTNAME_IN_TEXT = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:com|org|net|io|ai|dev|co\.uk|de|eu)\b",
    re.IGNORECASE,
)


def hostnames_in(node: Any, path: str = "") -> list[tuple[str, str]]:
    """Every hostname-looking string in a JSON-ish tree, with the path that carries it."""
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found += hostnames_in(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += hostnames_in(value, f"{path}[{index}]")
    elif isinstance(node, str):
        found += [(path, match.group(0)) for match in HOSTNAME_IN_TEXT.finditer(node)]
    return found


def envelope_problems(result: Result, spec: dict | None) -> list[str]:
    """Every envelope rule, checked on one call. Empty list = clean."""
    problems: list[str] = []
    envelope = result.envelope
    if result.parse_error:
        problems.append(f"{result.describe()}: {result.parse_error}")
        return problems
    assert envelope is not None
    missing = [key for key in ENVELOPE_KEYS if key not in envelope]
    if missing:
        problems.append(f"{result.describe()}: envelope is missing {missing}")
    if envelope.get("schema_version") != "1.0":
        problems.append(f"{result.describe()}: schema_version={envelope.get('schema_version')!r}")
    if not isinstance(envelope.get("ok"), bool):
        problems.append(f"{result.describe()}: ok is not a boolean")
    if envelope.get("exit_code") != result.code:
        problems.append(
            f"{result.describe()}: envelope exit_code={envelope.get('exit_code')} but the "
            f"process exited {result.code}"
        )
    if envelope.get("exit_code") not in EXIT_NAMES:
        problems.append(f"{result.describe()}: exit code {envelope.get('exit_code')} is outside the vocabulary")
    if bool(envelope.get("ok")) != (result.code == 0):
        problems.append(f"{result.describe()}: ok={envelope.get('ok')} with exit {result.code}")
    if envelope.get("ok") and envelope.get("error") is not None:
        problems.append(f"{result.describe()}: ok=true but error={envelope.get('error')!r}")
    if not envelope.get("ok") and envelope.get("error") is None:
        problems.append(f"{result.describe()}: ok=false but error is null")
    if envelope.get("ok") and not envelope.get("evidence"):
        problems.append(f"{result.describe()}: ok=true with no evidence (the contract forbids this)")
    if result.code == 0 and envelope.get("data") is None and spec is not None:
        if spec.get("data_available"):
            problems.append(
                f"{result.describe()}: exit 0 with data=null although the contract declares a payload"
            )
    if spec is not None and spec.get("command") not in (envelope.get("command"), None):
        # a group-level usage error reports the group; a leaf must report its dotted path
        if result.code != 1:
            problems.append(
                f"{result.describe()}: envelope command={envelope.get('command')!r}, "
                f"contract says {spec.get('command')!r}"
            )
    return problems


def drift_record(result: Result, spec: dict | None) -> dict | None:
    """Exit-code drift: what the command returned vs what the contract declares."""
    if spec is None:
        return {"command": result.command or " ".join(result.argv[:2]),
                "observed": result.code, "declared": None,
                "verdict": "no contract entry"}
    declared = list(spec.get("exit_codes") or [])
    return {
        "command": spec["command"],
        "observed": result.code,
        "declared": declared,
        "verdict": "declared" if result.code in declared else "DRIFT",
    }


# --------------------------------------------------------------------------- #
# docker (label-scoped; the 7 foreign penpot containers are never touched)
# --------------------------------------------------------------------------- #

def docker(*args: str, timeout: float = 60.0) -> tuple[int, str, str]:
    proc = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def docker_ids(*scopes: str) -> list[str]:
    """Container ids matching every label scope (one ``--filter`` per label)."""
    args = ["ps", "-a"]
    for scope in scopes:
        args += ["--filter", f"label={scope}"]
    args += ["-q"]
    code, out, _ = docker(*args)
    return sorted(line for line in out.splitlines() if line) if code == 0 else []


class TinyContainer:
    """One tiny labelled container: it supplies the derived answer and is swept by label.

    Safety model (the 7 foreign ``penpot-local`` containers plus every other wave of this
    product share the daemon): the only things this wave may remove are containers that
    carry **this wave's** ``godsv.test_wave`` label or the capability label of the tool it
    registers. Everything else is snapshotted before and compared after.
    """

    def __init__(self, work_dir: Path, capability_name: str = ""):
        self.dir = work_dir
        self.capability_name = capability_name or f"e2e_wavecli_{os.getpid()}"
        self.id = ""
        self.foreign_before: list[str] = []
        self.others_before: list[str] = []
        self.capability_scope = f"godsv.capability={self.capability_name}"

    def available(self) -> bool:
        code, _, _ = docker("version", "--format", "{{.Server.Version}}")
        return code == 0

    def _other_skaldir_ids(self) -> list[str]:
        """Skaldir-labelled containers that are NOT this run's (never ours to remove)."""
        code, out, _ = docker("ps", "-a", "--filter", f"label={OWNER_LABEL}", "-q")
        if code != 0:
            return []
        mine = set(docker_ids(*WAVE_SCOPE)) | set(docker_ids(self.capability_scope))
        if self.id:
            mine.add(self.id[:12])
        return sorted(cid for cid in out.splitlines()
                      if cid and not any(cid.startswith(prefix) for prefix in mine))

    def start(self) -> "TinyContainer":
        self.foreign_before = docker_ids(FOREIGN_SCOPE)
        self.others_before = self._other_skaldir_ids()
        code, out, err = docker(
            "run", "-d", "--rm", "--network", "none",
            "--label", OWNER_LABEL, "--label", WAVE_LABEL, "--label", RUN_LABEL,
            "--label", "purpose=test",
            IMAGE, "sleep", "600",
        )
        if code != 0:
            raise RuntimeError(f"cannot start the tiny container: {err[:200]}")
        self.id = out.strip()
        return self

    def exec(self, *argv: str) -> str:
        code, out, err = docker("exec", self.id, *argv)
        if code != 0:
            raise RuntimeError(f"docker exec failed: {err[:200]}")
        return out

    def _labels_of(self, cid: str) -> dict:
        code, out, _ = docker("inspect", cid, "--format", "{{json .Config.Labels}}")
        if code != 0 or not out:
            return {}
        try:
            return json.loads(out) or {}
        except json.JSONDecodeError:
            return {}

    def teardown(self) -> dict:
        """Remove this run's containers by label, then verify the effect.

        Before anything is removed, every id that is about to be removed is inspected: it
        must carry this run's labels or this capability's label, or be this run's own
        container. Anything else would be a scope violation and is reported as one.
        """
        candidates = docker_ids(*WAVE_SCOPE)
        violations: list[dict] = []
        for cid in candidates + ([self.id] if self.id else []):
            labels = self._labels_of(cid)
            if cid == self.id:
                continue
            if not (labels.get(WAVE_LABEL_KEY) == WAVE_LABEL_VALUE
                    or labels.get(RUN_LABEL_KEY) == RUN_LABEL_VALUE
                    or labels.get("godsv.capability") == self.capability_name):
                violations.append({"id": cid, "labels": labels})
        wave_ids = [cid for cid in candidates if cid != self.id]
        code, out, err = docker("rm", "-f", *wave_ids, timeout=120)
        wave_rm = {"exit": code, "removed": out, "err": err}
        self_rm = {"exit": 0, "removed": "", "err": ""}
        if self.id:
            self_rm = dict(zip(("exit", "removed", "err"), docker("rm", "-f", self.id, timeout=60)))
        remaining = docker_ids(*WAVE_SCOPE)
        capability_left = docker_ids(self.capability_scope)
        foreign_after = docker_ids(FOREIGN_SCOPE)
        others_after = self._other_skaldir_ids()
        return {
            "wave_rm": wave_rm, "self_rm": self_rm,
            "remaining": remaining,
            "wave_scope": list(WAVE_SCOPE),
            "scope_violations": violations,
            "removal_candidates": candidates + ([self.id] if self.id else []),
            "capability_scope": self.capability_scope,
            "capability_remaining": capability_left,
            "foreign_before": self.foreign_before,
            "foreign_after": foreign_after,
            "foreign_unchanged": self.foreign_before == foreign_after,
            "others_before": self.others_before,
            "others_after": others_after,
            "others_unchanged": self.others_before == others_after,
        }


# --------------------------------------------------------------------------- #
# the journeys
# --------------------------------------------------------------------------- #

class Report:
    def __init__(self, strict: bool = False) -> None:
        self.checks: list[dict] = []
        self.problems: list[str] = []
        self.observations: list[Result] = []
        self.drift: list[dict] = []
        self.layers: list[dict] = []
        self.notes: dict[str, Any] = {}
        self.specs: dict[str, dict] = {}
        self.payload_gaps: list[str] = []
        self.strict = strict

    def check(self, name: str, ok: bool, detail: str = "", known: bool = False) -> bool:
        """Record one check. ``known=True`` = a documented defect: it is reported as
        ``[KNOWN]`` and does not fail the run unless ``--strict`` is asked for."""
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail,
                            "known": bool(known)})
        if not ok and not known:
            self.problems.append(f"{name}: {detail}" if detail else name)
        elif not ok and known and self.strict:
            self.problems.append(f"[strict] {name}: {detail}" if detail else f"[strict] {name}")
        return bool(ok)

    def spec_for(self, result: Result) -> dict | None:
        """The declared contract entry for one call.

        ``argparse`` rejects a command line **before** the leaf is known, so a usage error
        reports the group (``tools``, or ``verify`` for a group that does not exist). The
        intended leaf is rebuilt from the argv, and only then is the exit code compared.
        """
        command = result.command
        if command in self.specs:
            return self.specs[command]
        argv = [token for token in result.argv if not token.startswith("-")]
        for depth in (3, 2, 1):
            if len(argv) >= depth:
                candidate = ".".join(argv[:depth])
                if candidate in self.specs:
                    return self.specs[candidate]
        return None

    def record(self, result: Result, note: str = "") -> Result:
        """Every invocation goes through here: envelope + drift, no exceptions."""
        spec = self.spec_for(result)
        self.observations.append(result)
        for problem in envelope_problems(result, spec):
            if "although the contract declares a payload" in problem:
                # a declared payload the command did not produce: recorded, not fatal —
                # the contract/drift assertion for it is added once, at the end
                self.payload_gaps.append(problem)
            else:
                self.problems.append(problem)
        drift = drift_record(result, spec)
        if drift:
            self.drift.append(drift)
        if note and drift:
            self.notes.setdefault("call_notes", []).append({"note": note, **drift})
        return result


def journey_provider(cli: Cli, report: Report, work_dir: Path) -> None:
    """Configure a provider against a local stub and get a real advisory out."""
    settings = cli.runtime / "settings.yaml"
    settings.write_text(
        "system_one:\n  enabled: true\n  provider: llm_system_one_adapter\n", encoding="utf-8"
    )
    request = work_dir / "voice_triage_request.json"
    request.write_text(json.dumps({
        "run_id": "e2e-run-1", "goal_id": "e2e-goal-1",
        "trigger_kind": "strategy_repeat", "step": 4,
        "signals": {"repeat": 3, "note": "the same failing step three times"},
    }), encoding="utf-8")

    stub = StubProvider(work_dir, mode="registry").start()
    try:
        live = Cli(work_dir, "provider-home", {
            "GODSV_LLM_BASE_URL": stub.base_url,
            "GODSV_LLM_MODEL": "stub-model",
            "GODSV_LLM_API_KEY": "stub-key",
            "GODSV_LLM_CONFIG": "",
        })
        report.specs.update(contract_specs(live))
        (live.runtime / "settings.yaml").write_text(
            settings.read_text(encoding="utf-8"), encoding="utf-8"
        )

        # --- the honest needs-credentials path, from the same stub (401) ---
        no_key = Cli(work_dir, "provider-home", {
            "GODSV_LLM_BASE_URL": stub.base_url, "GODSV_LLM_MODEL": "stub-model",
        })
        stub.reset()
        stub.set("auth_required")
        denied = no_key.run("jev", "decide", "--kind", "voice_triage",
                            "--request", str(request), "--provider",
                            "llm_system_one_adapter")
        report.record(denied, "provider without a credential")
        report.check(
            "a rejected credential is exit 4 and never a success",
            denied.code == 4 and (denied.envelope or {}).get("ok") is False,
            f"exit={denied.code} error={(denied.envelope or {}).get('error')}",
        )

        # --- a malformed provider answer must not become an advisory ---
        stub.set("fixed", {"chosen": "defer", "reason_code": "not_the_registered_shape"})
        stub.reset()
        malformed = live.run("jev", "decide", "--kind", "voice_triage",
                            "--request", str(request), "--provider",
                            "llm_system_one_adapter")
        report.record(malformed, "malformed provider answer")
        malformed_data = (malformed.envelope or {}).get("data") or {}
        report.check(
            "a malformed provider answer stays blocked with a named reason",
            malformed.code in (3, 7) and malformed_data.get("status") == "blocked"
            and bool(str(malformed_data.get("refusal_reason") or "").strip()),
            f"exit={malformed.code} status={malformed_data.get('status')} "
            f"refusal={malformed_data.get('refusal_reason')}",
        )

        # --- an empty answer set is a contract error, not a default ---
        stub.set("fixed", {})
        stub.reset()
        empty = live.run("jev", "decide", "--kind", "voice_triage",
                        "--request", str(request), "--provider", "llm_system_one_adapter")
        report.record(empty, "provider with no answers")
        report.check(
            "a provider with no answers is refused, never defaulted",
            empty.code != 0 and (empty.envelope or {}).get("ok") is False,
            f"exit={empty.code} stderr={empty.stderr.strip()[:120]}",
        )

        # --- the real round trip: registry answers -> accepted advisory ---
        stub.set("registry")
        stub.reset()
        ok = live.run("jev", "decide", "--kind", "voice_triage",
                      "--request", str(request), "--provider", "llm_system_one_adapter")
        report.record(ok, "provider configured against the local stub")
        data = (ok.envelope or {}).get("data") or {}
        calls = stub.requests()
        report.check(
            "the CLI really talked to the stub provider",
            len(calls) >= 1 and bool(calls[0].get("auth")),
            f"{len(calls)} HTTP request(s); first={calls[0] if calls else 'none'}",
        )
        report.check(
            "the advisory is accepted and reports the live provider mode",
            ok.code == 0 and data.get("status") == "ok"
            and data.get("provider_mode") == "llm_system_one_adapter",
            f"exit={ok.code} status={data.get('status')} mode={data.get('provider_mode')}",
        )
        report.check(
            "a success envelope is backed by provider usage, not a guess",
            bool((data.get("usage") or {}).get("prompt_chars")) and int(data.get("attempts") or 0) >= 1,
            f"usage={data.get('usage')} attempts={data.get('attempts')}",
        )
        report.check(
            "the answer is the registered pack's, not the model's free text",
            (data.get("answer") or {}).get("kind") == "voice_triage"
            and bool((data.get("answer") or {}).get("route_hint")),
            f"answer={data.get('answer')}",
        )
        report.notes["provider"] = {
            "base_url": stub.base_url,
            "requests_seen": len(calls),
            "authorization_header": bool(calls[0].get("auth")) if calls else False,
            "model": calls[0].get("model") if calls else None,
            "local_only": stub.base_url.startswith("http://127.0.0.1:"),
            "config_surface": "settings.yaml + GODSV_LLM_* (the `provider` group is not registered "
                              "in cli.py — measured)",
        }

        history = live.run("jev", "history")
        report.record(history, "the decision ledger")
        decisions = ((history.envelope or {}).get("data") or {}).get("decisions") or []
        report.check(
            "the accepted advisory is in the append-only decision ledger",
            any(str(row.get("status")) == "ok"
                and (str(row.get("provider_mode")) in ("llm_system_one_adapter", "")
                     or row.get("provider_mode") is None)
                for row in decisions),
            f"{len(decisions)} ledger rows: {json.dumps(decisions[:2])[:220]}",
        )
    finally:
        stub.stop()


def journey_goal(cli: Cli, report: Report) -> None:
    """Set a goal: the record is durable, the loop is honestly not wired."""
    begin = cli.run("completion", "begin", "--goal", "ship the black-box CLI suite",
                    "--criterion", "every journey is proven by raw output")
    report.record(begin, "goal recorded")
    data = (begin.envelope or {}).get("data") or {}
    report.check(
        "a new goal reports the honest blocked exit 3",
        begin.code == 3 and (begin.envelope or {}).get("ok") is False
        and str(((begin.envelope or {}).get("error") or {}).get("code")) == "blocked",
        f"exit={begin.code} error={(begin.envelope or {}).get('error')}",
    )
    report.check(
        "the blocked goal is still recorded with its criteria",
        data.get("goal") == "ship the black-box CLI suite" and data.get("loop_wired") is False
        and any(str(c.get("text")) == "every journey is proven by raw output"
                and c.get("blocking") is True for c in (data.get("criteria") or [])),
        f"data={json.dumps(data)[:260]}",
    )

    status = cli.run("completion", "status")
    report.record(status, "goal state")
    status_data = (status.envelope or {}).get("data") or {}
    report.check(
        "completion status agrees with the recorded goal",
        status.code == 0 and status_data.get("state") == "WORKING"
        and status_data.get("terminal") is False
        and status_data.get("goal") == "ship the black-box CLI suite"
        and any(str(c.get("text")) == "every journey is proven by raw output"
                for c in (status_data.get("criteria") or []))
        and int(status_data.get("verified") or 0) <= int(status_data.get("total") or 0) >= 1,
        f"data={json.dumps(status_data)[:260]}",
    )

    history = cli.run("completion", "history")
    report.record(history, "goal history")
    rows = ((history.envelope or {}).get("data") or {}).get("rows") or []
    report.check("the goal history has the record", len(rows) >= 1, f"{len(rows)} rows")

    override = cli.run("completion", "override", "--reason", "e2e smoke")
    report.record(override, "user override")
    report.check(
        "a user override never claims a verified completion",
        "VERIFIED_COMPLETE" not in json.dumps(override.envelope or {}),
        f"exit={override.code}",
    )


def journey_verification(cli: Cli, report: Report) -> None:
    """The verification layer: wired, measured, no invented PASS."""
    planned = cli.run("verify", "plan")
    report.record(planned, "`verify` is wired into the CLI")
    report.check(
        "the verify group is wired and answers (measured, not assumed)",
        planned.code in (0, 1, 3, 5)
        and "invalid choice" not in planned.stderr,
        f"exit={planned.code} stderr={planned.stderr.strip()[:160]}",
    )
    report.check(
        "the wired verify.* commands are all declared in the contract, so no schema lies "
        "about them",
        all(
            any(name == f"verify.{verb}" for name in report.specs)
            for verb in ("plan", "run", "status", "evidence")
        ),
        f"{sorted(n for n in report.specs if n.startswith('verify.'))}",
    )

    selftest = cli.run("selftest")
    report.record(selftest, "the eight-layer probe")
    layers = {row["layer"]: row for row in ((selftest.envelope or {}).get("data") or {}).get("layers", [])}
    report.check(
        "selftest exits 2 while a layer is not reachable",
        selftest.code == 2 and (selftest.envelope or {}).get("ok") is False,
        f"exit={selftest.code} failed={(selftest.envelope or {}).get('data', {}).get('failed_layers')}",
    )
    report.check(
        "the unreachable layers each name a blocked reason",
        all(not row.get("ok") and row.get("blocked_reason") for row in layers.values() if not row.get("ok")),
        f"no reason for: {[n for n, r in layers.items() if not r.get('ok') and not r.get('blocked_reason')]}",
    )
    report.check(
        "VERIFY reports itself reachable now that `verify run` exists — and still never "
        "certifies with UNKNOWN",
        layers.get("VERIFY", {}).get("ok") is True
        and all(
            isinstance(row.get("ok"), bool)
            for row in (layers.get("VERIFY", {}).get("probes") or [])
        ),
        f"VERIFY={json.dumps(layers.get('VERIFY', {}))[:200]}",
    )

    doctor = cli.run("doctor")
    report.record(doctor, "release health check")
    doctor_envelope = doctor.envelope or {}
    doctor_data = doctor_envelope.get("data") or {}
    report.check(
        "doctor is healthy (0) or names its failure (2) — never a false ok",
        (doctor.code == 0 and doctor_envelope.get("ok") is True)
        or (doctor.code == 2 and doctor_envelope.get("ok") is False
            and bool((doctor_data or {}).get("failed_checks")
                     or (doctor_envelope.get("error") or {}).get("details"))),
        f"exit={doctor.code} error={doctor_envelope.get('error')} "
        f"failed={(doctor_data or {}).get('failed_checks')}",
    )

    evaluation = cli.run("eval")
    report.record(evaluation, "in-process eval episodes")
    eval_data = (evaluation.envelope or {}).get("data") or {}
    report.check(
        "eval is honest about being in-process (not real-world verification)",
        evaluation.code == 0 and eval_data.get("scope") == "in-process episodes"
        and any("not real-world verification" in str(w)
                for w in (evaluation.envelope or {}).get("warnings") or []),
        f"exit={evaluation.code} scope={eval_data.get('scope')} "
        f"warnings={(evaluation.envelope or {}).get('warnings')}",
    )
    report.notes["verify_command"] = {
        "present": True,
        "designed": "verify plan/run/status/evidence (register_cli in src/skaldir/verify_cli.py)",
        "measured": f"skaldir verify plan -> exit {planned.code}: {planned.stderr.strip()[:120]}",
    }


def journey_capability(cli: Cli, report: Report, work_dir: Path, container: TinyContainer | None) -> None:
    """Mine → register → call, twice over: a **container** capability and a plain one.

    The container path is the MVP's acceptance item 4 (``tools mine --image`` →
    ``tools register`` → ``tools adapter show`` → ``tools health --probe`` → call →
    ``tools rollback``). The plain path adds the ``mcp serve`` ``tools/call`` surface,
    which the container path does not expose.
    """
    name = f"e2e_wavecli_{os.getpid()}"
    oracle = ""
    if container is None:
        # no reachable daemon: the container journey is a gap, never a pass — the plain
        # capability path below still runs (it needs no container)
        pass
    else:
        container.start()
        oracle = container.exec(
            "sh", "-c", "printf %s skaldir | sha256sum | cut -d' ' -f1"
        ).strip()
        report.check(
            "the run's own labelled container is up and answers",
            len(oracle) == 64,
            f"docker exec returned {oracle!r}",
        )

        mined = cli.run("tools", "mine", "--image", IMAGE, "--capability",
                        "compute the sha-256 digest of a utf-8 string", "--name", name,
                        timeout=300)
        report.record(mined, "container capability mined from a tiny local image")
        mined_data = (mined.envelope or {}).get("data") or {}
        surface = mined_data.get("surface") or {}
        report.check(
            "the capability is inspected INSIDE the container (real surface, pinned digest)",
            mined.code == 0 and surface.get("inspected") is True
            and str(surface.get("image_digest") or "").startswith("sha256:")
            and (surface.get("verbs") or {}).get("matched") == "sha256sum",
            f"exit={mined.code} surface={json.dumps(surface)[:220]}",
        )
        report.check(
            "the adapter is tested against the container before it is staged",
            (mined_data.get("test") or {}).get("passed") is True
            or (mined_data.get("test") or {}).get("observed") == oracle,
            f"test={json.dumps(mined_data.get('test'))[:200]} oracle={oracle}",
        )

        registered = cli.run("tools", "register", name, timeout=300)
        report.record(registered, "container capability registered live")
        reg_data = (registered.envelope or {}).get("data") or {}
        report.check(
            "registration is live: version, health, permissions and a rollback snapshot",
            registered.code == 0 and reg_data.get("registered") is True
            and reg_data.get("health_state") == "HEALTHY"
            and (reg_data.get("permissions") or {}).get("privileged") is False
            and (reg_data.get("permissions") or {}).get("network") == "none"
            and bool(reg_data.get("rollback_id")),
            f"exit={registered.code} data={json.dumps(reg_data)[:260]}",
        )

        adapter = cli.run("tools", "adapter", "show", name)
        report.record(adapter, "the typed adapter")
        adapter_data = ((adapter.envelope or {}).get("data") or {}).get("adapter") or {}
        report.check(
            "the registered adapter is typed and bound to the pinned digest",
            adapter.code == 0 and (adapter_data.get("operation") or {}).get("id") == "sha256sum"
            and bool((adapter_data.get("operation") or {}).get("inputs"))
            and bool((adapter_data.get("operation") or {}).get("outputs")),
            f"exit={adapter.code} operation={json.dumps(adapter_data.get('operation'))[:220]}",
        )

        health = cli.run("tools", "health", name, "--probe", timeout=300)
        report.record(health, "live health probe")
        health_data = (health.envelope or {}).get("data") or {}
        report.check(
            "the live health probe runs the container and stays HEALTHY",
            health.code == 0 and (health_data.get("health") or {}).get("state") == "HEALTHY"
            and (health_data.get("probe") or {}).get("ok") is True,
            f"exit={health.code} data={json.dumps(health_data)[:240]}",
        )

        wrapper = cli.runtime / "generated_tools" / name / "bin" / name
        if wrapper.exists():
            proc = subprocess.run([str(wrapper), json.dumps({"text": "skaldir"})],
                                  capture_output=True, text=True, timeout=120)
            called = proc.stdout.strip()
            report.check(
                "the registered container capability is CALLED and matches an independent oracle",
                proc.returncode == 0 and oracle in called,
                f"exit={proc.returncode} stdout={called[:160]} oracle={oracle}",
            )
        else:
            report.check("the container capability has a callable wrapper", False,
                         f"missing: {wrapper}")

        rolled = cli.run("tools", "rollback", name, "--reason", "e2e journey", timeout=300)
        report.record(rolled, "rollback")
        roll_data = (rolled.envelope or {}).get("data") or {}
        report.check(
            "rollback retires the capability and sweeps containers by label scope",
            rolled.code == 0 and roll_data.get("rolled_back") is True
            and roll_data.get("teardown_ok") is True
            and (roll_data.get("teardown_scope") or "").startswith("godsv.")
            and (roll_data.get("health") or {}).get("state") == "RETIRED",
            f"exit={rolled.code} data={json.dumps(roll_data)[:260]}",
        )
        gone = cli.run("tools", "health", name)
        report.record(gone, "health after the rollback")
        report.check(
            "a retired capability is not found any more, it is not served",
            gone.code == 5 and str(((gone.envelope or {}).get("error") or {}).get("code")) == "not_found",
            f"exit={gone.code} error={(gone.envelope or {}).get('error')}",
        )

    # ---- the plain (python) path: build → list → show → call through MCP ----
    for _ in range(2):
        report.record(cli.run("tools", "request", "--capability",
                              "greet a name without any container",
                              "--reason", "seen twice in one session", "--name", "e2e_greet"))
    mined_requests = cli.run("tools", "mine")
    report.record(mined_requests, "mining the recurring need")
    match = re.search(r"(\d+)\s+candidate update", mined_requests.stderr)
    mined_updates = int(match.group(1)) if match else 0
    report.check(
        "mining turns a repeated need into a candidate",
        mined_requests.code == 0 and mined_updates >= 1,
        f"exit={mined_requests.code} updates={mined_updates} "
        f"stderr={mined_requests.stderr.strip()[:160]}",
    )

    candidates_result = cli.run("tools", "candidates")
    report.record(candidates_result, "the mined candidates")
    candidate_id = ""
    for line in candidates_result.stderr.splitlines():
        token = line.split()[0] if line.split() else ""
        if token.startswith("cand-"):
            candidate_id = token
            break
    report.check("the candidate is listed for the user", bool(candidate_id),
                 f"stderr={candidates_result.stderr.strip()[:160]}")

    source = work_dir / "e2e_greet_tool.py"
    source.write_text(
        '"""Tiny deterministic capability for the black-box E2E journey."""\n'
        "\n"
        "\n"
        'def main(name: str = "", **_extra) -> dict:\n'
        '    """Greet `name`."""\n'
        '    return {"greeting": f"hello {str(name).strip()}"}\n',
        encoding="utf-8",
    )
    tests = work_dir / "test_e2e_greet_tool.py"
    tests.write_text(
        "import e2e_greet_tool\n"
        "\n"
        "\n"
        "def test_greets_the_name():\n"
        "    assert e2e_greet_tool.main(name=\"skaldir\") == {\"greeting\": \"hello skaldir\"}\n"
        "\n"
        "\n"
        "def test_is_deterministic():\n"
        "    assert e2e_greet_tool.main(name=\"x\") == e2e_greet_tool.main(name=\"x\")\n",
        encoding="utf-8",
    )

    build = cli.run("tools", "build", "--capability", "greet a name without any container",
                    "--reason", "e2e journey", "--name", "e2e_greet",
                    "--source", str(source), "--tests", str(tests),
                    "--module", "e2e_greet_tool", "--entrypoint", "main",
                    "--sample", json.dumps({"name": "skaldir"}), timeout=300)
    report.record(build, "the gated build pipeline")
    build_data = (build.envelope or {}).get("data") or {}
    gates = {gate.get("name"): bool(gate.get("ok")) for gate in build_data.get("gates") or []}
    report.check(
        "the gated build really ran scan/import/test/determinism",
        set(gates) >= {"scan", "import", "test", "determinism"},
        f"gates={gates} error={build_data.get('error')}",
    )
    report.check(
        "the built capability is registered",
        bool(build_data.get("tool")) and gates.get("test") is True,
        f"tool={build_data.get('tool')} wrapper={build_data.get('wrapper')}",
    )

    promoted = cli.run("tools", "promote", candidate_id) if candidate_id else None
    if promoted is not None:
        report.record(promoted, "candidate -> build request")
        report.check(
            "promote turns the candidate into a build request",
            promoted.code == 0 and bool(((promoted.envelope or {}).get("data") or {}).get("request_id")),
            f"exit={promoted.code} data={(promoted.envelope or {}).get('data')}",
        )

    listing = cli.run("tools", "list")
    report.record(listing, "the tool registry")
    names = [row.get("name") for row in ((listing.envelope or {}).get("data") or {}).get("tools") or []]
    report.check("the registered capability is in the tool registry", "e2e_greet" in names,
                 f"tools={names}")

    shown = cli.run("tools", "show", "e2e_greet")
    report.record(shown, "one tool spec")
    shown_data = (shown.envelope or {}).get("data") or {}
    report.check(
        "tools show returns the approved spec",
        shown.code == 0 and shown_data.get("status") == "approved"
        and shown_data.get("kind") == "python",
        f"status={shown_data.get('status')} kind={shown_data.get('kind')}",
    )

    wrapper_text = str(build_data.get("wrapper") or "").strip()
    plain_wrapper = Path(wrapper_text) if wrapper_text else None
    if plain_wrapper is not None and plain_wrapper.is_file():
        proc = subprocess.run([str(plain_wrapper), json.dumps({"name": "skaldir"})],
                              capture_output=True, text=True, timeout=60)
        report.check(
            "the generated wrapper runs and returns the capability's answer",
            proc.returncode == 0 and "hello skaldir" in proc.stdout,
            f"exit={proc.returncode} stdout={proc.stdout.strip()[:120]}",
        )
    else:
        report.check(
            "the generated wrapper exists",
            False,
            f"the build reported no wrapper (exit={build.code}, "
            f"data.ok={build_data.get('ok')}, error={build_data.get('error')!r}, "
            f"gates={gates})",
        )

    mcp = mcp_call(cli, "e2e_greet", {"name": "skaldir"})
    report.check(
        "the CLI's MCP surface calls the registered capability",
        "hello skaldir" in json.dumps(mcp),
        f"tools/call -> {json.dumps(mcp)[:220]}",
    )
    report.notes["container_capability"] = {
        "image": IMAGE,
        "name": name,
        "oracle": oracle,
        "call_surface": "generated wrapper (bin/<name>); no `tools call` verb exists, "
                        "`tools health --probe` is the only container-executing inspect verb",
    }


def mcp_call(cli: Cli, tool: str | None, arguments: dict | None,
             timeout: float = 120.0) -> dict:
    """One stdio MCP round trip against ``godsvoice mcp serve`` (newline JSON-RPC)."""
    proc = subprocess.Popen(
        [str(GODSVOICE), "mcp", "serve"], cwd=str(cli.workspace), env=cli.env(),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    try:
        def send(payload: dict) -> None:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()

        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "gv-cli-e2e", "version": "1"}}})
        time.sleep(1.2)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        time.sleep(0.4)
        wanted = 2
        if tool is None:
            send({"jsonrpc": "2.0", "id": wanted, "method": "tools/list", "params": {}})
        else:
            send({"jsonrpc": "2.0", "id": wanted, "method": "tools/call",
                  "params": {"name": tool, "arguments": {"arguments": arguments or {}}}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            assert proc.stdout is not None
            line = proc.stdout.readline()
            if not line:
                break
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("id") == wanted:
                return payload
        return {"error": {"message": "no response from the MCP server"}}
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            proc.kill()


def journey_false_green(cli: Cli, report: Report, work_dir: Path) -> None:
    """Two measured cases where the CLI's own verdict is greener than the work.

    Both are **documented defects** (``known=True``): the check records what the CLI
    really does today, and ``--strict`` turns them into failures so the fix cannot land
    unnoticed. Neither is a fabricated result — they are raw, reproducible envelopes.
    """
    source = work_dir / "broken_tool.py"
    source.write_text(
        '"""A capability whose own test fails — the build must not report success."""\n'
        "\n"
        "\n"
        'def main(name: str = "", **_extra) -> dict:\n'
        '    return {"greeting": f"hello {name}"}\n',
        encoding="utf-8",
    )
    broken_tests = work_dir / "test_broken_tool.py"
    broken_tests.write_text(
        "import broken_tool\n"
        "\n"
        "\n"
        "def test_that_fails_on_purpose():\n"
        "    assert broken_tool.main(name=\"x\") == {\"greeting\": \"not what it returns\"}\n",
        encoding="utf-8",
    )
    failed = cli.run("tools", "build", "--capability", "a capability that fails its test",
                     "--reason", "e2e negative control", "--name", "e2e_broken",
                     "--source", str(source), "--tests", str(broken_tests),
                     "--module", "broken_tool", "--entrypoint", "main",
                     "--sample", json.dumps({"name": "skaldir"}), timeout=300)
    report.record(failed, "a build whose gates fail")
    failed_data = (failed.envelope or {}).get("data") or {}
    gates = {gate.get("name"): bool(gate.get("ok")) for gate in failed_data.get("gates") or []}
    report.check(
        "a FAILED build never exits 0 (tools.build returns its own verdict)",
        failed.code != 0 and failed_data.get("ok") is not True,
        f"exit={failed.code} data.ok={failed_data.get('ok')} gates={gates} "
        f"error={failed_data.get('error')}",
    )

    request = cli.run("tools", "request", "--capability",
                      "a capability nothing can build without source", "--name", "e2e_unbuildable")
    report.record(request, "an open request nothing can build")
    request_id = ((request.envelope or {}).get("data") or {}).get("request_id") or ""
    if request_id:
        fulfilled = cli.run("tools", "fulfill", request_id, timeout=300)
        report.record(fulfilled, "the pause-less pipeline")
        fulfilled_data = (fulfilled.envelope or {}).get("data") or {}
        report.check(
            "a build that produced nothing is not reported as ok (tools.fulfill)",
            fulfilled.code != 0
            and fulfilled_data.get("status") != "ok",
            f"exit={fulfilled.code} status={fulfilled_data.get('status')} "
            f"direction={str(fulfilled_data.get('direction'))[:160]}",
        )
    else:
        report.check("a capability request can be recorded", False, "no request_id")


def journey_compose(cli: Cli, report: Report) -> None:
    """The COMPOSE layer: candidates are ranked, but nothing can be created or run."""
    composed = cli.run("tools", "compose", "greet a name and then another name")
    report.record(composed, "composition candidates")
    data = (composed.envelope or {}).get("data") or {}
    report.check(
        "tools compose ranks candidates for a need",
        composed.code == 0 and int(data.get("count") or 0) >= 1,
        f"exit={composed.code} data={json.dumps(data)[:200]}",
    )
    compose_verbs = sorted(name for name in report.specs if name.startswith("compose."))
    report.check(
        "the COMPOSE group has create/validate/run/show and they are in the contract",
        {"compose.create", "compose.validate", "compose.run", "compose.show"}
        <= set(compose_verbs),
        f"compose verbs in the contract: {compose_verbs}",
    )
    refused = cli.run("compose", "run", "--name", "e2e_ghost", expect_envelope=True)
    report.record(refused, "compose run of a name that was never created")
    report.check(
        "compose run of an unknown composite is exit 5 and never a faked verdict",
        refused.code == 5,
        f"exit={refused.code} stderr={refused.stderr.strip()[:120]}",
    )


def journey_act(cli: Cli, report: Report) -> None:
    """The ACT layer: no desktop is touched, and the CLI says so out loud."""
    check = cli.run("cu", "check")
    report.record(check, "computer-use runners")
    runners = ((check.envelope or {}).get("data") or {}).get("runners") or {}
    report.check(
        "cu check reports the real runner availability",
        check.code == 0 and "dry_run" in runners and "container_podman" in runners,
        f"exit={check.code} runners={sorted(runners)}",
    )
    demo = cli.run("cu", "demo")
    report.record(demo, "computer-use fixture")
    demo_data = (demo.envelope or {}).get("data") or {}
    report.check(
        "cu demo declares itself a fixture and claims no desktop action",
        demo.code == 0 and demo_data.get("scope") == "in-process fixture"
        and demo_data.get("desktop_touched") is False and demo_data.get("live") is False
        and any("no computer use happened" in str(w) for w in (demo.envelope or {}).get("warnings") or []),
        f"data={json.dumps(demo_data)[:200]} warnings={(demo.envelope or {}).get('warnings')}",
    )


def journey_credentials(cli: Cli, report: Report) -> None:
    """The credential surface: handles only, the secret never comes back."""
    no_key = cli.run("vault", "list")
    report.record(no_key, "vault without a master key")
    report.check(
        "the vault refuses to open without the master key, with a named error",
        no_key.code == 1 and "SKALDIR_VAULT_KEY" in no_key.stderr,
        f"exit={no_key.code} stderr={no_key.stderr.strip()[:120]}",
    )

    # Both spellings are set: `SKALDIR_VAULT_KEY` is the primary name the product prints,
    # `GODSV_VAULT_KEY` the deprecated alias it still accepts (`skaldir/env.py`
    # `first_env`). The suite rides the alias path too, so a rename that drops it fails
    # here instead of in the field.
    vault_cli = Cli(cli.work_dir, cli.workspace.name,
                    dict(cli.env_extra, SKALDIR_VAULT_KEY="e2e-master-key",
                         GODSV_VAULT_KEY="e2e-master-key"))
    added = vault_cli.run("vault", "add", "--service", "llm", "--name", "e2e-stub",
                          "--description", "throwaway e2e handle",
                          stdin="super-secret-value\n")
    report.record(added, "credential stored (secret via stdin, never argv)")
    handle = ((added.envelope or {}).get("data") or {}).get("handle") or ""
    report.check(
        "a credential is stored as a handle and the secret never reaches stdout",
        added.code == 0 and handle.startswith("secret://")
        and "super-secret-value" not in added.stdout
        and "super-secret-value" not in added.stderr,
        f"exit={added.code} handle={handle}",
    )
    vault_file = vault_cli.workspace / "vault.json"
    vault_paths = [vault_file] + sorted(vault_cli.workspace.glob("*.json"))
    report.check(
        "the secret is not on disk in plaintext",
        all("super-secret-value" not in path.read_text(errors="replace")
            for path in vault_paths if path.exists()),
        f"checked {[str(p) for p in vault_paths if p.exists()]}",
    )
    listed = vault_cli.run("vault", "list")
    report.record(listed, "credential handles")
    report.check(
        "vault list shows the handle after the secret",
        listed.code == 0 and handle in json.dumps(listed.envelope or {}),
        f"data={json.dumps((listed.envelope or {}).get('data'))[:200]}",
    )


# --------------------------------------------------------------------------- #
# the product HTTP API (`godsvoice serve --http`, src/godsv/api_server.py)
# --------------------------------------------------------------------------- #

#: The documented hand-over wiring for `src/godsv/api_server_cli.py`.
#:
#: The API module ships `register_cli(subparsers)` and a three-line snippet for `cli.py`
#: (its own module docstring), but the snippet is the CLI owner's paste — measured:
#: `godsvoice serve --http` → argparse "invalid choice: 'serve'". This wrapper applies the
#: module's *own* documented snippet in-process and runs the verb, so the suite exercises
#: the real `api_server` on the real parser. Nothing here edits `cli.py`.
API_SERVE_WIRING = r'''
import argparse, sys
from godsv import api_server_cli, cli, cli_contract

_real_build_parser = cli.build_parser


def _build_parser():
    parser = _real_build_parser()
    sub = next(action for action in parser._actions
               if isinstance(action, argparse._SubParsersAction))
    api_server_cli.register_cli(sub)
    cli_contract.annotate(parser)
    return parser


cli.build_parser = _build_parser
parser = cli.build_parser()
args = parser.parse_args(sys.argv[1:])
sys.exit(cli_contract.run_command(args, args.func))
'''


def product_status_map() -> dict[int, int]:
    """The product's own exit-code → HTTP-status table (never a hand-copied copy)."""
    try:
        probe = subprocess.run(
            [sys.executable, "-c",
             "import json; from godsv import api_server as a; "
             "print(json.dumps(a.HTTP_STATUS_BY_EXIT))"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60,
        )
        if probe.returncode == 0 and probe.stdout.strip():
            return {int(key): int(value) for key, value in json.loads(probe.stdout).items()}
    except Exception:  # noqa: BLE001 — the table is only needed for the stand-in
        pass
    return {0: 200, 1: 400, 2: 422, 3: 409, 4: 401, 5: 404, 6: 409, 7: 500, 8: 504}


class ApiServer:
    """The product's HTTP API, started through its own wiring snippet.

    ``godsvoice serve --http`` is not registered in ``cli.py`` yet (the module ships the
    snippet; the paste is the CLI owner's), so the server is started with the module's own
    documented wiring. When the paste lands, ``Cli.run("serve", "--http", …)`` works
    directly and this class is the same thing without the wrapper.
    """

    def __init__(self, work_dir: Path, cli: Cli):
        self.cli = cli
        self.dir = work_dir / "api"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.wiring = self.dir / "serve_wiring.py"
        self.wiring.write_text(API_SERVE_WIRING, encoding="utf-8")
        self.openapi = self.dir / "openapi.json"
        self.stderr_path = self.dir / "server.err"
        self.port = free_port()
        self.proc: subprocess.Popen | None = None
        self.via_console_script = False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "ApiServer":
        """Start the server; raise with the server's own message when it cannot."""
        console = GODSVOICE.exists() and self._console_script_has_serve()
        if console:
            argv = [str(GODSVOICE), "serve", "--http", "--port", str(self.port),
                    "--openapi", str(self.openapi)]
            self.via_console_script = True
        else:
            argv = [sys.executable, str(self.wiring), "serve", "--http",
                    "--port", str(self.port), "--openapi", str(self.openapi)]
        self.stderr_handle = open(self.stderr_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            argv, cwd=str(self.cli.workspace), env=self.cli.env(),
            stdout=subprocess.DEVNULL, stderr=self.stderr_handle,
        )
        if not wait_for_http(f"{self.base_url}/health", timeout=45):
            detail = self._server_message()
            self.stop()
            raise RuntimeError(f"the API server did not come up: {detail}")
        return self

    def _console_script_has_serve(self) -> bool:
        help_text = subprocess.run([str(GODSVOICE), "--help"], capture_output=True,
                                   text=True, timeout=60)
        return "serve" in (help_text.stdout or "") + (help_text.stderr or "")

    def _server_message(self) -> str:
        """The server's own stderr, with any token redacted (never printed)."""
        try:
            self.stderr_handle.flush()
            text = self.stderr_path.read_text(encoding="utf-8")
        except OSError:
            return "(no stderr)"
        lines = [line for line in text.splitlines() if "API token" not in line]
        return " | ".join(lines[-3:])[:400]

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                self.proc.kill()
        handle = getattr(self, "stderr_handle", None)
        if handle and not handle.closed:
            handle.close()

    def get(self, path: str, timeout: float = 120.0) -> tuple[int, dict, dict]:
        try:
            with urllib.request.urlopen(self.base_url + path, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return response.status, json.loads(body or "null"), dict(response.headers)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(body or "null"), dict(exc.headers)
            except json.JSONDecodeError:
                return exc.code, {}, dict(exc.headers)


# --------------------------------------------------------------------------- #
# the HTTP stand-in + Bruno
# --------------------------------------------------------------------------- #

#: The routes the collection addresses, in the product's spelling (`/cli/<cmd>`).
SHIM_ROUTES = {
    "/health": [],
    "/cli/contract/dump": ["contract", "dump"],
    "/cli/selftest": ["selftest"],
    "/cli/completion/status": ["completion", "status"],
    "/cli/tools/list": ["tools", "list"],
    "/cli/registry/status": ["registry", "status"],
}


def write_bruno_collection(target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    (target / "bruno.json").write_text(BRUNO_COLLECTION_JSON, encoding="utf-8")
    for name, body in BRUNO_ENTRIES.items():
        (target / name).write_text(body, encoding="utf-8")
    return target


def run_bruno(cli: Cli, report: Report, work_dir: Path) -> None:
    """One Bruno collection against the CLI's HTTP surface.

    Target order (recorded in ``notes['http_surface']``, never guessed):

    1. **the product API** — ``godsvoice serve --http`` (``src/godsv/api_server.py``), started
       through the module's own hand-over wiring while ``cli.py`` has not pasted the snippet;
    2. **the stand-in shim** — only if the API cannot be started (missing fastapi/uvicorn, a
       port that cannot be bound, …). It answers the same routes, the same status mapping and
       the same headers, so the collection is unchanged.
    """
    bru = shutil.which("bru")
    if not bru:
        report.check("Bruno CLI is installed", False, "`bru` is not on PATH")
        report.notes["http_surface"] = {"used": "none", "reason": "bru missing"}
        return

    collection = write_bruno_collection(work_dir / "bruno")
    status_map = product_status_map()
    target = None
    server = None
    reason = ""
    try:
        server = ApiServer(work_dir, cli).start()
        target = server.base_url
        report.notes["http_surface"] = {
            "used": "product HTTP API (src/godsv/api_server.py)",
            "base_url": target,
            "route_spelling": "/cli/<command> ('.' → '/'), /health, /openapi.json",
            "started_via": ("console script `godsvoice serve --http`"
                            if server.via_console_script
                            else "the module's own documented wiring snippet "
                                 "(api_server_cli.register_cli; `cli.py` has no `serve` yet)"),
            "status_map": status_map,
            "collection": str(collection),
        }
        report.check(
            "the product HTTP API answers /health with the envelope",
            server.get("/health")[1].get("command") == "api.health",
            f"{target}/health",
        )
        status, envelope, headers = server.get("/cli/contract/dump")
        report.check(
            "the API returns the CLI's own envelope plus its x-godsv- headers",
            status == 200 and envelope.get("command") == "contract.dump"
            and headers.get("x-godsv-exit-code") == str(envelope.get("exit_code")),
            f"status={status} headers={ {k: v for k, v in headers.items() if k.startswith('x-godsv')} }",
        )
        blocked_status, blocked, _ = server.get("/cli/selftest")
        report.check(
            "a blocked verdict is mapped onto a non-2xx status (exit 2 → 422)",
            blocked_status == status_map.get(2, 422) and blocked.get("exit_code") == 2,
            f"HTTP {blocked_status} exit={blocked.get('exit_code')}",
        )
        not_found_status, not_found, _ = server.get("/cli/does/not/exist")
        report.check(
            "an unknown route reports not_found instead of inventing a result",
            not_found_status == 404 and (not_found.get("error") or {}).get("code") == "not_found",
            f"HTTP {not_found_status} body={json.dumps(not_found)[:160]}",
        )
    except Exception as exc:  # noqa: BLE001 — the API is the preferred, not the only target
        reason = f"{type(exc).__name__}: {exc}"
        report.check(
            "the product HTTP API could be started",
            False,
            f"{reason} — falling back to the stand-in shim",
            known=True,
        )
        if server is not None:
            server.stop()
        shim = CliShim(work_dir, cli, SHIM_ROUTES, status_map)
        shim.start()
        server = shim
        target = shim.base_url
        report.notes["http_surface"] = {
            "used": "local stand-in shim (the product API could not be started)",
            "base_url": target,
            "reason": reason,
            "collection": str(collection),
        }

    try:
        report.check(
            "the HTTP surface serves the CLI's real envelope bytes",
            target is not None and server.get("/cli/contract/dump")[0] in (200, 400, 404, 409, 422),
            f"{target}/cli/contract/dump",
        )
        proc = subprocess.run(
            [bru, "run", "--env-var", f"baseUrl={target}",
             "--reporter-json", str(work_dir / "bruno-report.json")],
            cwd=str(collection), capture_output=True, text=True, timeout=300,
        )
        report.notes["bruno"] = {
            "collection": str(collection),
            "target": target,
            "target_kind": report.notes["http_surface"]["used"],
            "exit": proc.returncode,
            "output": proc.stdout[-1500:],
        }
        report.check(
            "the Bruno collection passes against the CLI's HTTP surface",
            proc.returncode == 0,
            f"bru exit={proc.returncode}: {proc.stdout[-400:]}",
        )

        # negative control: the same collection must fail against a wrong base
        negative = subprocess.run(
            [bru, "run", "--env-var", f"baseUrl={target}/nope"],
            cwd=str(collection), capture_output=True, text=True, timeout=300,
        )
        report.check(
            "the same collection fails correctly on a wrong base URL (assertions are not vacuous)",
            negative.returncode != 0,
            f"bru exit={negative.returncode}",
        )
    finally:
        if server is not None:
            server.stop()


# --------------------------------------------------------------------------- #
# the layer table
# --------------------------------------------------------------------------- #

LAYERS = (
    ("EXPAND", "Self-Expanding Tool System"),
    ("ACT", "Computer Use + GUI Sub-Tools"),
    ("COMPOSE", "Learned Composite Tools"),
    ("ROUTE", "Jev Decision Layer"),
    ("SUPERVISE", "Voice Supervisor"),
    ("ENFORCE", "Goal-Locked Execution"),
    ("VERIFY", "Real-World Verification"),
    ("PERSIST", "Long-Run Agent + Learning"),
)

COVERAGE_NOTES = {
    "EXPAND": ("yes", "container path: tools mine --image → register → adapter show → "
               "health --probe → wrapper call → rollback; plain path: request/mine/"
               "candidates/promote/build/list/show + MCP tools/call"),
    "ACT": ("no", "no live desktop bridge: `cu check`/`cu demo` are in-process runners; "
            "selftest reports ACT unreachable"),
    "COMPOSE": ("no", "`tools compose` only ranks candidates; no create/validate/run verb exists"),
    "ROUTE": ("yes", "jev decide/kinds/packs/selftest/history through a real provider stub "
              "(exit 0/3/4/7 all observed)"),
    "SUPERVISE": ("no", "no CLI surface at all; selftest reports the missing pause store"),
    "ENFORCE": ("partial", "completion begin/status/history/override run; the loop itself is "
                "honestly exit 3 (not wired)"),
    "VERIFY": ("no", "the verify group is not registered in cli.py; selftest reports VERIFY "
               "unreachable and doctor/eval are the reachable surfaces"),
    "PERSIST": ("partial", "completion/completion history + metrics work; no runs.db, memory "
                "commands exit 1 on a missing db (pinned legacy code)"),
}


def coverage_table(report: Report) -> list[dict]:
    selftest = next((row for row in report.observations if row.command == "selftest"), None)
    live = {}
    if selftest and selftest.envelope:
        for row in (((selftest.envelope or {}).get("data") or {}).get("layers") or []):
            live[row["layer"]] = row
    table = []
    for key, title in LAYERS:
        covered, why = COVERAGE_NOTES[key]
        probe = live.get(key) or {}
        table.append({
            "layer": key, "name": title, "black_box_test": covered, "how": why,
            "selftest_reachable": bool(probe.get("ok")),
            "selftest_blocked_reason": str(probe.get("blocked_reason") or ""),
        })
    return table


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def build_report(work_dir: Path, containers: bool, want_bruno: bool,
                 strict: bool = False) -> Report:
    report = Report(strict=strict)
    cli = Cli(work_dir)
    if not GODSVOICE.exists():
        raise RuntimeError(f"the console script is missing: {GODSVOICE}")
    report.specs.update(contract_specs(cli))

    contract = cli.run("contract", "dump")
    report.record(contract, "the contract is readable")
    report.check(
        "the contract is the single source of truth",
        contract.code == 0 and isinstance((contract.envelope or {}).get("data"), dict)
        and int(contract.envelope["data"].get("leaf_commands") or 0) > 0,
        f"exit={contract.code}",
    )
    report.check(
        "every parser node has a contract entry",
        not (contract.envelope or {}).get("data", {}).get("unregistered"),
        f"unregistered={(contract.envelope or {}).get('data', {}).get('unregistered')}",
    )
    report.check(
        "every command declares its exit codes",
        all(row.get("exit_codes") for row in report.specs.values()),
        f"{sum(1 for row in report.specs.values() if not row.get('exit_codes'))} without codes",
    )

    # The contract is the GUI's source of truth, never a config file: a vendor hostname in
    # it (in a schema, an arg help, a note) is a smell — measured, `provider add --base-url`
    # shipped `https://api.deepseek.com/v1` inside the dumped schema of a CLI that must not
    # talk to a remote provider in this suite. Scanned over the whole payload, so this also
    # documents the routes no `data_schema` covers.
    payload = (contract.envelope or {}).get("data") or {}
    schema_hosts = [
        {"command": row.get("command"), "path": path, "hostname": host}
        for row in payload.get("commands") or []
        for path, host in hostnames_in(row.get("data_schema"), "data_schema")
    ]
    payload_hosts = [
        {"command": row.get("command"), "path": path, "hostname": host}
        for row in payload.get("commands") or []
        for path, host in hostnames_in({key: value for key, value in row.items()
                                        if key != "data_schema"}, "")
    ]
    report.notes["contract_hostnames"] = {
        "data_schema": schema_hosts,
        "elsewhere_in_the_payload": payload_hosts,
        "scan": "hostname = host.tld for com|org|net|io|ai|dev|co.uk|de|eu, case-insensitive",
    }
    report.check(
        "no hostname appears in a dumped data_schema (the contract stays vendor-neutral)",
        not schema_hosts,
        "; ".join(f"{row['command']} {row['path']}={row['hostname']}" for row in schema_hosts[:5]),
    )
    report.check(
        "no hostname appears anywhere else in the contract payload (args, help, notes)",
        not payload_hosts,
        "; ".join(f"{row['command']} {row['path']}={row['hostname']}"
                  for row in payload_hosts[:5]),
    )

    registry = cli.run("registry", "status")
    report.record(registry, "the runtime the CLI bound to")
    registry_data = (registry.envelope or {}).get("data") or {}
    report.check(
        "the CLI ran against its own sandbox (GODSV_HOME and cwd), not the owner's runtime",
        registry.code == 0
        and str(registry_data.get("runtime_dir") or "").startswith(str(cli.runtime))
        and all(str(row.get("path") or "").startswith(str(cli.workspace))
                for row in registry_data.get("legacy_candidates") or []),
        f"runtime_dir={registry_data.get('runtime_dir')} expected under {cli.runtime}",
    )

    journey_provider(cli, report, work_dir)
    journey_goal(cli, report)
    journey_verification(cli, report)
    journey_credentials(cli, report)
    journey_act(cli, report)
    journey_false_green(cli, report, work_dir)

    container = TinyContainer(work_dir) if containers and TinyContainer(work_dir).available() else None
    if containers and container is None:
        report.check("docker is reachable for the container journey", False,
                     "no docker daemon — re-run with --no-containers to accept the gap")
    try:
        journey_capability(cli, report, work_dir, container)
        journey_compose(cli, report)
    finally:
        if container is not None:
            teardown = container.teardown()
            report.notes["container_teardown"] = teardown
            report.check(
                "the wave's container is torn down (by label, and by id)",
                not teardown["remaining"],
                f"remaining={teardown['remaining']} self_rm={teardown['self_rm']}",
            )
            report.check(
                "no container of the registered capability is left behind",
                not teardown["capability_remaining"],
                f"{teardown['capability_scope']} -> {teardown['capability_remaining']}",
            )
            report.check(
                "the foreign penpot containers are untouched",
                teardown["foreign_unchanged"],
                f"before={teardown['foreign_before']} after={teardown['foreign_after']}",
            )
            report.check(
                "only containers carrying this run's labels were removed",
                not teardown["scope_violations"],
                f"violations={teardown['scope_violations']} "
                f"candidates={teardown['removal_candidates']}",
            )
            report.notes["other_skaldir_containers"] = {
                "before": teardown["others_before"], "after": teardown["others_after"],
                "note": "informational: parallel waves create and remove their own containers on "
                        "the shared daemon, so this set is not stable and is not asserted on",
            }

    if want_bruno:
        run_bruno(cli, report, work_dir)

    # the drift ledger, deduplicated per (command, observed, declared)
    seen = set()
    drift = []
    for row in report.drift:
        key = (row["command"], row["observed"], tuple(row["declared"] or []))
        if key in seen:
            continue
        seen.add(key)
        drift.append(row)
    report.drift = drift
    hard = [row for row in report.drift if row["verdict"] == "DRIFT"]
    report.check(
        "no exit-code drift against the declared contract",
        not hard,
        "; ".join(f"{r['command']} returned {r['observed']} (declared {r['declared']})"
                  for r in hard),
    )
    unknown = [row for row in report.drift if row["verdict"] == "no contract entry"]
    report.notes["undeclared_calls"] = [
        {"command": row["command"] or "(group-level usage error)", "observed": row["observed"]}
        for row in unknown
    ]
    report.check(
        "every command that declares a payload really carries one (data is not null on exit 0)",
        not report.payload_gaps,
        "; ".join(sorted(set(report.payload_gaps)))[:400],
        known=True,
    )
    report.layers = coverage_table(report)
    report.notes.setdefault("http_surface", {
        "used": "none (--no-bruno)",
        "note": "the collection was skipped; the product API lives at src/skaldir/api_server.py "
                "(`skaldir serve --http` once api_server_cli.register_cli is in cli.py)",
    })
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--work-dir", default=None, help="keep the sandbox here (default: tempdir)")
    parser.add_argument("--no-containers", action="store_true", help="skip the docker journey")
    parser.add_argument("--no-bruno", action="store_true", help="skip the Bruno collection")
    parser.add_argument("--strict", action="store_true",
                        help="documented defects fail the run too (used to catch the fix)")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args(argv)

    keep = bool(args.work_dir)
    work_dir = (Path(args.work_dir).resolve() if args.work_dir
                else Path(tempfile.mkdtemp(prefix="gv-cli-e2e-")))
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        report = build_report(work_dir, not args.no_containers, not args.no_bruno, args.strict)
    except Exception as exc:  # noqa: BLE001 — a blocked run is not a failure of the CLI
        import traceback
        traceback.print_exc()
        print(f"could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    finally:
        if not keep:
            shutil.rmtree(work_dir, ignore_errors=True)

    if args.json:
        print(json.dumps({
            "checks": report.checks, "problems": report.problems,
            "drift": report.drift, "layers": report.layers, "notes": report.notes,
            "calls": len(report.observations),
        }, indent=2, sort_keys=True))
    else:
        print(f"gv CLI black-box E2E — {len(report.observations)} CLI invocations, "
              f"{sum(1 for c in report.checks if c['ok'])}/{len(report.checks)} checks held")
        for row in report.checks:
            if row["ok"]:
                mark = "PASS "
            else:
                mark = "KNOWN" if row.get("known") else "FAIL "
            print(f"  [{mark}] {row['check']}"
                  + (f" — {row['detail']}" if row["detail"] and not row["ok"] else ""))
        print("\nexit-code contract (observed vs declared):")
        marks = {"declared": "ok ", "DRIFT": "DRIFT", "no contract entry": "n/a "}
        for row in report.drift:
            mark = marks.get(row["verdict"], row["verdict"])
            print(f"  {mark} {row['command'] or '(group)':<24} exit={row['observed']} "
                  f"declared={row['declared']}")
        print("\nlayer coverage (black-box):")
        for row in report.layers:
            print(f"  {row['layer']:<10} {row['black_box_test']:<20} selftest_reachable={row['selftest_reachable']}")
        if report.problems:
            print(f"\n{len(report.problems)} problem(s):")
            for problem in report.problems:
                print(f"  - {problem}")
        print("\nprobe result: " + ("PASS" if not report.problems else "FAIL"))
    return 0 if not report.problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
