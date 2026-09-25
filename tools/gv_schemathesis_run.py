#!/usr/bin/env python3
"""Property tests (Schemathesis) over the CLI's JSON contract — the real numbers.

WHY THIS EXISTS
---------------
``MVP/08-test-matrix.md`` §0 names Tier C (property tests, Schemathesis) and requires one of two
prerequisites before the tier may run:

1. ``skaldir serve --http`` (also ``skaldir daemon serve``) exposes one HTTP route per CLI command
   plus an OpenAPI document, **or**
2. Schemathesis runs in-process against an ASGI router.

**Prerequisite 1 shipped** (``src/skaldir/api_server.py`` + ``api_server_cli.py``): measured
2026-09-18, ``skaldir serve --http --port 8791`` answers ``GET /health`` 200 and serves ``GET
/openapi.json`` with **219 paths** (87 mutating), generated from ``cli_contract.COMMANDS`` and the
real ``build_parser()``. ``--base-url http://127.0.0.1:8791`` therefore runs the very same schema
and checks against the **real** surface with this runner's shim out of the path — that is what the
``product_gap`` payload below now says, and what it names as still open is **this runner's own
document**, not the product: the compiled operations declare the shim's single ``200`` response
while the real surface derives the status from the envelope's ``exit_code``
(``api_server.HTTP_STATUS_BY_EXIT``). Measured on the real surface: ``POST /cli/metrics`` answered
``404`` (the throwaway runtime has no metrics DB — a *correct* mapping) and
``status_code_conformance`` reported it as ``UndefinedStatusCode``, because the document does not
document it yet.

What this runner does — and says so out loud:

* it reads the **real** CLI contract with ``godsvoice contract dump --json`` (73 leaf commands,
  each with its args and its JSON Schema for ``data``),
* compiles that contract into an OpenAPI 3.1 document (one ``POST /cli/<command>`` operation per
  leaf command, response = the CLI envelope, per-command ``data`` schema taken verbatim from the
  contract),
* boots a **stand-in** HTTP surface in this process (stdlib ``ThreadingHTTPServer``; the shim lives
  in this file, never in ``src/godsv/**``) that runs the real CLI as a subprocess —
  ``godsvoice <command> --json`` — inside a throwaway ``GODSV_HOME``/``HERMES_HOME`` sandbox, and
  returns the envelope the CLI really wrote,
* runs Schemathesis against that server for real and reports the real numbers: operations, generated
  cases, unique failures and the failing examples.

The schema is real (it comes from the shipped contract). The HTTP surface this runner serves by
default is still its own shim (it needs no port and no token, which is what keeps the suite gate
runnable offline); pass ``--base-url`` to aim the same schema and the same checks at the real
``skaldir serve --http``. What is still open — a document change here, not a product gap — is stated
in ``--json`` output (``product_gap``) and in ``MVP/15-property-tests.md``.

HONEST LIMITS (recorded per run, never silently dropped)
--------------------------------------------------------
* ``excluded`` operations: named with the reason (host writes, blocking servers, gated pipelines).
* the shim answers ``200`` for **any** envelope the CLI produced, whatever its ``exit_code``: the
  CLI's verdict is the envelope, not the HTTP status. Mapping exit codes onto HTTP statuses would
  turn the CLI's own "internal" verdict into a fake server error and hide a real 5xx.
* ``negative_data_rejection`` is therefore meaningful only at the transport level (structure + the
  contract's declared arg types), which is exactly what the shim validates.

USAGE
-----
    .venv/bin/python tools/gv_schemathesis_run.py                     # full property run
    .venv/bin/python tools/gv_schemathesis_run.py --phases coverage   # deterministic subset
    .venv/bin/python tools/gv_schemathesis_run.py --only metrics,jev.kinds --examples 2
    .venv/bin/python tools/gv_schemathesis_run.py --artifact outputs/schemathesis/run.json

Exit code: 0 = ran, no failure; 1 = ran, failures found; 2 = could not run (config/tool error).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from godsv import cli_contract  # noqa: E402 — the contract under test is the schema source

GODSVOICE = ROOT / ".venv" / "bin" / "godsvoice"
SCHEMATHESIS = ROOT / ".venv" / "bin" / "schemathesis"

#: the four flags ``cli_contract.annotate()`` adds to every parser: the shim sets ``--json`` itself
#: and the other three only shape human output, so they are not part of the generated request body.
COMMON_FLAGS = ("json", "format", "quiet", "no_color")

#: Commands the property tier must not fire. Every entry carries the reason; nothing is dropped
#: silently (a skipped operation is a recorded decision, not an absence).
EXCLUDED: dict[str, str] = {
    "mcp.serve": "blocks by design — a stdio MCP server can never answer a request/response "
                 "HTTP route (the designed `daemon serve --http` cannot expose it either)",
    "desktop.install": "writes the desktop entry + icons outside any sandbox (XDG data dir)",
    "desktop.uninstall": "removes the desktop entry + icons outside any sandbox",
    "desktop.build-app": "runs the real app build",
    "desktop.start": "launches the desktop app",
    "tools.build": "runs the capability build pipeline (containers) — that is Tier B's job "
                   "(MVP/08 §2 L1), and this tier must not start unlabelled containers",
    "tools.fulfill": "runs the capability pipeline end to end (containers/gates)",
    "tools.approve": "runs the full gate pipeline in a generated workdir",
    "tools.promote-global": "publishes a tool user-wide after a 2-context evidence check",
}

#: The checks Schemathesis runs. Kept explicit so the doc can name what was and was not proven.
CHECKS = (
    "not_a_server_error",
    "status_code_conformance",
    "content_type_conformance",
    "response_headers_conformance",
    "response_schema_conformance",
    "negative_data_rejection",
    "positive_data_acceptance",
    "unsupported_method",
    "allow_header_conformance",
    "missing_required_header",
)

PRODUCT_GAP = {
    "missing": "nothing on the product side — the HTTP surface ships (measured below); the "
               "one open item is this runner's own OpenAPI document, which still declares "
               "the stand-in shim's single 200 response",
    "designed_in": "MVP/02-cli-contract.md §5.10 — `serve --http --port P [--openapi PATH]` "
                   "=> one HTTP route per CLI command, same envelope, plus an OpenAPI document",
    "delivered": "src/skaldir/api_server.py + api_server_cli.py: `skaldir serve --http` "
                 "(aliases `skaldir daemon serve`, `--http`/`--openapi`) serves one route per "
                 "CLI command, the CLI's own envelope as the body, and its own OpenAPI "
                 "document — the prerequisite MVP/08 §0 asked for",
    "measured": "skaldir serve --http --port 8791 -> GET /health 200; GET /openapi.json 200, "
                "219 paths, 87 of them mutating (its own words: generated from "
                "cli_contract.COMMANDS and the real build_parser()); `--base-url "
                "http://127.0.0.1:8791 --only metrics --phases coverage` ran Schemathesis "
                "against that surface with this runner's shim out of the path",
    "remaining": [
        "this runner compiles the shim's shape: every operation declares a single 200 "
        "response, while the real surface derives the HTTP status from the envelope's "
        "exit_code (api_server.HTTP_STATUS_BY_EXIT: 0->200, 1->400, 2->422, 3->409, 4->401, "
        "5->404, 6->409, 7->500, 8->504). Against the real surface that shows up as "
        "status_code_conformance/UndefinedStatusCode (measured: POST /cli/metrics answered "
        "404 because the throwaway runtime has no metrics DB — a correct mapping, an "
        "undocumented status)",
        "so the next step is a document change, not a product change: compile "
        "HTTP_STATUS_BY_EXIT into each operation's responses (per-command allowed exits are "
        "already in cli_contract.COMMANDS[command].exit_codes)",
        "Tier C is therefore still not recordable as *passing against the product surface*: "
        "the suite gate (tests/test_schemathesis_cli.py) keeps running the bounded subset on "
        "the in-process shim, which needs no port and no token",
    ],
    "exact_change": [
        "tools/gv_schemathesis_run.py: build each operation's `responses` from "
        "cli_contract.COMMANDS[command].exit_codes mapped through api_server."
        "HTTP_STATUS_BY_EXIT, keeping the one envelope schema under every status",
        "then run the tier with `--base-url http://127.0.0.1:<port>` against `skaldir serve "
        "--http` (start it with a throwaway SKALDIR_HOME and a token file, per MVP/56) — the "
        "schema and every check stay unchanged",
        "keep the in-process shim as the offline path: it is what makes the suite gate "
        "runnable without a port",
    ],
}


# --------------------------------------------------------------------------- #
# 1. the contract -> OpenAPI compiler
# --------------------------------------------------------------------------- #


def contract_dump(godsvoice: Path) -> dict:
    """``godsvoice contract dump --json`` -> the ``data`` payload (the real inventory)."""
    proc = subprocess.run(
        [str(godsvoice), "contract", "dump", "--json"],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise SystemExit(f"contract dump failed ({proc.returncode}): {proc.stderr[-800:]}")
    envelope = json.loads(proc.stdout)
    if not isinstance(envelope.get("data"), dict):
        raise SystemExit("contract dump emitted no data payload")
    return envelope


def envelope_keys() -> list[str]:
    """The envelope's own key list, read from ``build_envelope`` — never hardcoded here."""
    probe = cli_contract.build_envelope(
        command="schemathesis-probe",
        exit_code=cli_contract.EXIT_OK,
        data=None,
        evidence=[{"ref": "tools/gv_schemathesis_run.py", "kind": "code", "note": "key probe"}],
    )
    return list(probe.keys())


def _arg_schema(record: dict) -> dict:
    """The JSON Schema for one declared CLI argument (the shim maps it back to argv).

    The schema declares what the CLI really accepts — argparse types are unbounded, so no
    ``maxLength``/``minimum``/``maximum`` is invented here. The shim validates bodies against
    exactly this document, so an artificial bound would reject inputs the CLI accepts.
    """
    kind = str(record.get("type") or "string")
    if kind == "flag":
        return {"type": "boolean"}
    if kind == "count":
        return {"type": "integer"}
    if kind == "integer":
        return {"type": "integer"}
    if kind == "number":
        return {"type": "number"}
    if kind == "choice" and record.get("choices"):
        return {"type": "string", "enum": [str(item) for item in record["choices"]]}
    return {"type": "string"}


def _arg_property(record: dict) -> dict:
    base = _arg_schema(record)
    if record.get("repeatable"):
        # argparse append/nargs: one occurrence is as legal as many
        return {"anyOf": [base, {"type": "array", "items": base}]}
    return base


def declared_args(record: dict) -> list[dict]:
    """The command's args minus the four common flags the shim injects itself."""
    return [
        item for item in record.get("args", [])
        if str(item.get("dest") or "") not in COMMON_FLAGS
        and str(item.get("name") or "").lstrip("-").replace("-", "_") not in COMMON_FLAGS
    ]


def command_path(command: str) -> str:
    """``completion.begin`` -> ``/cli/completion/begin`` (one concrete path per command)."""
    return "/cli/" + command.replace(".", "/")


def build_openapi(dump: dict, *, exclude: dict[str, str] | None = None,
                  only: list[str] | None = None) -> tuple[dict, list[dict], list[dict]]:
    """Compile the contract into an OpenAPI 3.1 document.

    Returns ``(document, operations, excluded)`` where ``operations`` carries the mapping the shim
    needs (dotted command path <-> HTTP path <-> declared args).
    """
    exclude = dict(EXCLUDED if exclude is None else exclude)
    keys = envelope_keys()
    paths: dict[str, dict] = {}
    operations: list[dict] = []
    skipped: list[dict] = []
    for record in dump["data"]["commands"]:
        command = str(record["command"])
        if not record.get("registered"):
            skipped.append(
                {"command": command, "reason": "no contract entry in cli_contract.COMMANDS"}
            )
            continue
        if command in exclude:
            skipped.append({"command": command, "reason": exclude[command]})
            continue
        if only is not None and command not in only:
            continue
        data_schema = record.get("data_schema") or {"type": "null"}
        data_available = bool(record.get("data_available"))
        data_field: dict = (
            {"anyOf": [data_schema, {"type": "null"}]} if data_available else {"type": "null"}
        )
        envelope: dict = {
            "type": "object",
            "required": keys,
            "properties": {
                "schema_version": {"type": "string", "enum": [cli_contract.SCHEMA_VERSION]},
                "ok": {"type": "boolean"},
                "command": {"type": "string", "minLength": 1},
                "godsvoice_version": {"type": "string"},
                "started_at": {"type": "string"},
                "duration_ms": {"type": "integer", "minimum": 0},
                "exit_code": {
                    "type": "integer",
                    "minimum": min(cli_contract.EXIT_NAMES),
                    "maximum": max(cli_contract.EXIT_NAMES),
                },
                "data": data_field,
                "error": {
                    "anyOf": [
                        {
                            "type": "object",
                            "required": ["code", "message"],
                            "properties": {
                                "code": {"type": "string",
                                         "enum": list(cli_contract.EXIT_ERROR_CODES.values())},
                                "message": {"type": "string"},
                                "hint": {"type": ["string", "null"]},
                                "retryable": {"type": "boolean"},
                                "details": {"type": "object"},
                            },
                        },
                        {"type": "null"},
                    ]
                },
                "evidence": {"type": "array", "items": {"type": "object"}},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "next_actions": {"type": "array", "items": {"type": "object"}},
            },
            # registry.status and tools.promote-global mirror their payload at top level
            # (cli_contract.Contract.compat_top_level_keys) — the contract allows extra keys.
            "additionalProperties": True,
        }
        args_props = {
            str(item["dest"]): _arg_property(item) for item in declared_args(record)
        }
        request_schema: dict = {
            "type": "object",
            "additionalProperties": False,
            "required": ["args"],
            "properties": {
                "args": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": args_props,
                    "description": (
                        "CLI arguments by dest name; the shim renders them back to "
                        "argv and always appends --json"
                    ),
                },
                "stdin": {
                    "type": "string",
                    "description": "text stdin for commands that read a request body",
                },
            },
        }
        operation = {
            "operationId": "cli_" + command.replace(".", "_").replace("-", "_"),
            "summary": str(record.get("description") or command),
            "tags": [str(record.get("layer") or "kernel")],
            "x-godsvoice-command": command,
            "x-godsvoice-layer": str(record.get("layer") or "kernel"),
            "x-godsvoice-mutates": bool(record.get("mutates")),
            "x-godsvoice-exit-codes": list(record.get("exit_codes") or []),
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": request_schema}},
            },
            "responses": {
                "200": {
                    "description": "the CLI answered with one envelope; exit_code is the verdict",
                    "content": {"application/json": {"schema": envelope}},
                },
                "400": {
                    "description": "the request body is not a valid invocation of the CLI contract",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["error", "command"],
                                "properties": {
                                    "error": {"type": "string"},
                                    "command": {"type": "string"},
                                    "detail": {"type": "string"},
                                },
                            }
                        }
                    },
                },
            },
        }
        paths[command_path(command)] = {"post": operation}
        operations.append(
            {
                "command": command,
                "path": command_path(command),
                "operation_id": operation["operationId"],
                "layer": operation["x-godsvoice-layer"],
                "mutates": bool(record.get("mutates")),
                "args": declared_args(record),
                "data_available": data_available,
                "request_schema": request_schema,
            }
        )
    document = {
        "openapi": "3.1.0",
        "info": {
            "title": "Skaldir CLI JSON contract (compiled by tools/gv_schemathesis_run.py)",
            "version": str(dump["data"]["schema_version"]),
            "description": (
                "Compiled from `skaldir contract dump --json` by tools/gv_schemathesis_run.py. "
                "The schema is the shipped contract. Served against this file's own test shim "
                "unless --base-url points at the real surface (`skaldir serve --http`), whose "
                "HTTP status comes from the envelope's exit_code "
                "(api_server.HTTP_STATUS_BY_EXIT); these operations declare that one envelope "
                "under the shim's single status, so a status_conformance finding against the "
                "real surface is this document's, not the product's."
            ),
        },
        "servers": [{"url": "/"}],
        "paths": paths,
    }
    return document, operations, skipped


# --------------------------------------------------------------------------- #
# 2. the stand-in HTTP surface (test-only; never in src/godsv/**)
# --------------------------------------------------------------------------- #


@dataclass
class Invocation:
    command: str
    argv: list[str]
    stdin: str = ""


@dataclass
class HttpResponse:
    status: int
    payload: dict
    raw: bytes = b""
    wall_ms: int = 0


@dataclass
class ShimStats:
    requests: int = 0
    cli_calls: int = 0
    cli_seconds: float = 0.0
    transport_rejections: int = 0
    envelope_failures: int = 0
    timeouts: int = 0
    statuses: dict[int, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    #: A CLI command that changes the mode of the directory it runs in is a defect (it breaks the
    #: next process that tries to start there). Recorded, then the shim repairs its own temp dir so
    #: the property run stays usable — the finding is the point, the repair is only hygiene.
    sandbox_mode_changes: list[dict] = field(default_factory=list)


#: The mode the shim creates its sandbox with (``tempfile.mkdtemp`` is 0700).
SANDBOX_MODE = 0o700


def guard_sandbox(sandbox: Path, stats: ShimStats, command: str, argv: list[str]) -> None:
    """Record (and repair) a sandbox whose mode a CLI command changed under us."""
    try:
        mode = sandbox.stat().st_mode & 0o777
    except OSError as exc:
        stats.errors.append(f"{command}: sandbox unreadable: {exc}")
        return
    if mode == SANDBOX_MODE:
        return
    stats.sandbox_mode_changes.append({
        "command": command,
        "argv": argv,
        "observed_mode": oct(mode),
        "restored_mode": oct(SANDBOX_MODE),
        "detail": f"`godsvoice {' '.join(argv)} --json` left its own working directory at "
                  f"{oct(mode)} (no execute bit: every later process fails with EACCES)",
    })
    try:
        os.chmod(sandbox, SANDBOX_MODE)
    except OSError as exc:
        stats.errors.append(f"{command}: could not repair the sandbox: {exc}")


class ShimError(Exception):
    """The shim itself could not turn the body into an invocation (HTTP 400)."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


def render_argv(command: str, args: dict, declared: list[dict]) -> list[str]:
    """Map the JSON ``args`` object back onto argv using the contract's own arg records."""
    by_dest = {str(item["dest"]): item for item in declared}
    argv: list[str] = command.split(".")
    for key in sorted(args):
        if key not in by_dest:
            raise ShimError(f"unknown argument: {key}", f"{command} declares {sorted(by_dest)}")
        record = by_dest[key]
        value = args[key]
        values = value if isinstance(value, list) else [value]
        for item in values:
            if record["type"] == "flag":
                if isinstance(item, bool) and item:
                    argv.append(str(record["name"]))
                elif not isinstance(item, bool):
                    raise ShimError(f"{key} is a flag: expected boolean")
                continue
            if item is None:
                raise ShimError(f"{key}: null is not a value")
            text = str(item) if not isinstance(item, bool) else str(item).lower()
            if record["positional"]:
                argv.append(text)
            else:
                argv.extend([str(record["name"]), text])
    return argv


def build_shim(godsvoice: Path, operations: list[dict], *, sandbox: Path,
               timeout_s: float) -> tuple[ThreadingHTTPServer, ShimStats, type]:
    """Start the stand-in surface. Returns ``(server, stats, handler_class)``.

    The shim validates every body against the very requestBody schema the OpenAPI document
    declares (``jsonschema``, the same Draft 2020-12 dialect OpenAPI 3.1 is built on), so a
    ``negative_data_rejection`` verdict is about the document we published, not about a private
    rule invented here.
    """
    import jsonschema

    by_path = {item["path"]: item for item in operations}
    validators = {
        item["path"]: jsonschema.Draft202012Validator(item["request_schema"]) for item in operations
    }
    stats = ShimStats()
    env = dict(os.environ)
    env.update(
        {
            "GODSV_HOME": str(sandbox),
            "HERMES_HOME": str(sandbox),
            "NO_COLOR": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    env.pop("GODSV_FORMAT", None)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "gv-schemathesis-shim/1.0"
        sys_version = ""

        def __getattr__(self, name: str) -> Any:
            # every HTTP method lands on the same dispatcher (unknown ones answer 405, not 501)
            if name.startswith("do_"):
                return self._handle
            raise AttributeError(name)

        def _send(self, status: int, payload: dict, raw: bytes = b"") -> None:
            body = raw or json.dumps(payload, ensure_ascii=False).encode()
            with lock:
                stats.statuses[status] = stats.statuses.get(status, 0) + 1
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if status == 405:
                self.send_header("Allow", "POST")
            self.end_headers()
            self.wfile.write(body)

        def _reject(self, message: str, detail: str = "", command: str = "") -> None:
            with lock:
                stats.transport_rejections += 1
            self._send(400, {"error": message, "command": command, "detail": detail})

        def _method_not_allowed(self) -> None:
            self._send(405, {"error": "method not allowed", "command": "", "detail": "POST only"})

        def _handle(self) -> None:
            with lock:
                stats.requests += 1
            operation = by_path.get(self.path.split("?")[0])
            if operation is None:
                self._send(404, {"error": "unknown route", "command": "", "detail": self.path})
                return
            if self.command != "POST":
                self._method_not_allowed()
                return
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw.decode() or "{}")
            except (ValueError, UnicodeDecodeError) as exc:
                self._reject("body is not JSON", str(exc), operation["command"])
                return
            errors = sorted(validators[operation["path"]].iter_errors(body), key=str)
            if errors:
                first = errors[0]
                where = "/".join(str(part) for part in first.absolute_path)
                self._reject(
                    f"request body violates the documented schema at /{where}: {first.message}",
                    f"{len(errors)} violation(s)", operation["command"],
                )
                return
            args = body["args"]
            try:
                argv = render_argv(operation["command"], args, operation["args"])
            except ShimError as exc:
                self._reject(exc.message, exc.detail, operation["command"])
                return
            stdin = body.get("stdin")
            with lock:
                stats.cli_calls += 1
            started = time.time()
            try:
                proc = subprocess.run(
                    [str(godsvoice), *argv, "--json"],
                    capture_output=True, text=True, cwd=str(sandbox), env=env,
                    input=stdin if stdin else None, timeout=timeout_s,
                )
                stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
            except subprocess.TimeoutExpired:
                with lock:
                    stats.timeouts += 1
                    stats.errors.append(f"{operation['command']}: no answer within {timeout_s}s")
                guard_sandbox(sandbox, stats, operation["command"], argv)
                self._send(500, {
                    "error": "cli_timeout",
                    "command": operation["command"],
                    "detail": f"`godsvoice {operation['command']} --json` did not answer within "
                              f"{timeout_s}s",
                })
                return
            except OSError as exc:
                # the shim could not even start the CLI (sandbox gone, exec denied, ...) — say so
                # instead of dropping the connection: a hidden error must not look like a network
                # hiccup on the client side.
                detail = f"{type(exc).__name__}: {exc}"
                try:
                    stat = sandbox.stat()
                    detail += (f" | sandbox exists={sandbox.exists()} is_dir={sandbox.is_dir()} "
                               f"mode={oct(stat.st_mode & 0o777)} "
                               f"x_ok={os.access(sandbox, os.X_OK)}")
                except OSError as probe_error:
                    detail += f" | sandbox stat failed: {probe_error}"
                with lock:
                    stats.envelope_failures += 1
                    stats.errors.append(f"{operation['command']}: spawn failed: {detail}")
                guard_sandbox(sandbox, stats, operation["command"], argv)
                self._send(500, {
                    "error": "cli_spawn_failed",
                    "command": operation["command"],
                    "detail": detail,
                })
                return
            wall = time.time() - started
            with lock:
                stats.cli_seconds += wall
            guard_sandbox(sandbox, stats, operation["command"], argv)
            try:
                envelope = json.loads(stdout)
            except ValueError as exc:
                with lock:
                    stats.envelope_failures += 1
                    stats.errors.append(
                        f"{operation['command']}: stdout is not one JSON document "
                        f"(exit {code}, {len(stdout)} bytes): {stdout[:120]!r}"
                    )
                self._send(500, {
                    "error": "cli_emitted_no_envelope",
                    "command": operation["command"],
                    "detail": (f"exit {code}; stdout={stdout[:200]!r}; stderr={stderr[-200:]!r}"
                               f" ({exc})"),
                })
                return
            if not isinstance(envelope, dict):
                with lock:
                    stats.envelope_failures += 1
                self._send(500, {"error": "envelope is not an object",
                                 "command": operation["command"],
                                 "detail": type(envelope).__name__})
                return
            # return the CLI's own bytes: the shim must not rewrite the answer
            self._send(200, envelope, raw=json.dumps(envelope, ensure_ascii=False).encode())

        do_POST = _handle
        do_GET = _handle
        do_PUT = _handle
        do_DELETE = _handle
        do_PATCH = _handle
        do_HEAD = _handle
        do_OPTIONS = _handle
        do_TRACE = _handle

        def log_message(self, format: str, *args: Any) -> None:  # no verbose server log
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    return server, stats, Handler


# --------------------------------------------------------------------------- #
# 3. run Schemathesis
# --------------------------------------------------------------------------- #


def run_schemathesis(schema_path: Path, base_url: str, *, examples: int, seed: int,
                     phases: str, mode: str, workers: int, checks: tuple[str, ...],
                     json_report: Path, ndjson_report: Path,
                     schemathesis: Path = SCHEMATHESIS, timeout_s: int = 3600,
                     max_time: int | None = None,
                     workdir: Path | None = None) -> subprocess.CompletedProcess:
    """Run ``schemathesis run`` against the served schema.

    ``workdir`` is where Schemathesis may write its own state (``.schemathesis/cache/crashes``,
    ``st replay`` material). It must NOT be the repository root: the runner leaves no untracked
    junk in the tree, so the default is the report directory. ``--generation-database none`` keeps
    every run independent of a previous run's discovered examples — the numbers below are the
    numbers of *this* run.
    """
    cmd = [
        str(schemathesis), "run", str(schema_path.resolve()),
        "-u", base_url,
        "--report", "json,ndjson",
        "--report-json-path", str(json_report.resolve()),
        "--report-ndjson-path", str(ndjson_report.resolve()),
        "-n", str(examples),
        "--seed", str(seed),
        "--phases", phases,
        "--mode", mode,
        "--workers", str(workers),
        "--checks", ",".join(checks),
        "--request-timeout", "120",
        "--generation-database", "none",
        "--no-color",
    ]
    if max_time is not None:
        cmd.extend(["--max-time", str(max_time)])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s,
                          cwd=str(workdir or json_report.parent))


# --------------------------------------------------------------------------- #
# 4. read the reports back: real numbers, real examples, a real classification
# --------------------------------------------------------------------------- #


def _decode_content(content: Any, limit: int = 400) -> str:
    if isinstance(content, dict) and "$base64" in content:
        try:
            return base64.b64decode(str(content["$base64"])).decode("utf-8", "replace")[:limit]
        except Exception:  # noqa: BLE001 — a report read must never raise
            return "<undecodable>"
    return str(content)[:limit]


def examples_from_ndjson(path: Path) -> list[dict]:
    """Per-case failing examples out of the NDJSON event stream."""
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        scenario = event.get("ScenarioFinished")
        if not scenario or scenario.get("status") != "failure":
            continue
        recorder = scenario.get("recorder") or {}
        for case_id, entry in (recorder.get("cases") or {}).items():
            value = entry.get("value") or {}
            meta = value.get("meta") or {}
            interaction = (recorder.get("interactions") or {}).get(case_id) or {}
            response = interaction.get("response") or {}
            for check in (recorder.get("checks") or {}).get(case_id, []):
                if check.get("status") != "failure":
                    continue
                info = ((check.get("failure_info") or {}).get("failure")) or {}
                out.append(
                    {
                        "case_id": case_id,
                        "phase": scenario.get("phase"),
                        "check": check.get("name"),
                        "failure_type": info.get("type"),
                        "title": info.get("title"),
                        "severity": info.get("severity"),
                        "message": str(info.get("message") or "")[:900],
                        "request": {
                            "method": (interaction.get("request") or {}).get("method"),
                            "uri": (interaction.get("request") or {}).get("uri"),
                            "body": _decode_content(
                                ((value.get("generated_body") or {}).get("value"))
                                or (interaction.get("request") or {}).get("body"), 300),
                        },
                        "response": {
                            "status_code": response.get("status_code"),
                            "body": _decode_content((response.get("content") or {}), 300),
                        },
                        "generation_mode": (meta.get("generation") or {}).get("mode"),
                    }
                )
    return out


def classify(example: dict, operation: dict | None, *, target: str = "shim") -> str:
    """real_bug | expected_4xx | schema_gap — with the rule that produced it, stated.

    ``target`` says who answered: this file's own shim (which answers 200 for any envelope,
    by design) or the real surface (which maps the envelope's ``exit_code`` onto the HTTP
    status). An undocumented status is a *document* gap against the real surface, and a
    stand-in answer against the shim — the same check, two different owners.
    """
    check = str(example.get("check") or "")
    message = str(example.get("message") or "")
    status = (example.get("response") or {}).get("status_code")
    if check == "not_a_server_error":
        return "real_bug (the CLI produced no envelope at all / did not answer)"
    if check in {"status_code_conformance", "unsupported_method", "allow_header_conformance"}:
        if target == "real":
            return (
                "schema_gap (the real surface answered a status this document does not "
                "declare — the operations carry the shim's single 200 while the surface "
                "maps exit_code to a status, see product_gap)"
            )
        return "real_bug (the shim answered outside its declared statuses)"
    if check == "content_type_conformance":
        return "real_bug (non-JSON content type on a JSON contract)"
    if check in {"negative_data_rejection", "positive_data_acceptance"}:
        return f"expected_4xx (negative/positive data handling, status={status})"
    if check == "response_schema_conformance":
        if "/data" in message:
            return ("schema_gap (the declared per-command data schema disagrees with the payload "
                    "the command really returns)")
        return ("real_bug (the envelope itself violates cli_contract.build_envelope's documented "
                "shape)")
    return f"unclassified ({check})"


def summarize(json_report: Path, ndjson_report: Path,
              operations: list[dict], *, target: str = "shim") -> tuple[dict, list[dict]]:
    report = json.loads(json_report.read_text())
    by_path = {item["path"]: item for item in operations}
    examples = examples_from_ndjson(ndjson_report)
    for example in examples:
        uri = str((example.get("request") or {}).get("uri") or "")
        path = "/" + uri.split("/", 3)[-1] if uri else ""
        operation = by_path.get(path)
        example["operation"] = (operation or {}).get("command") or path
        example["layer"] = (operation or {}).get("layer")
        example["classification"] = classify(example, operation, target=target)
        example["known_gap"] = match_known_gap(example)
    return report, examples


# --------------------------------------------------------------------------- #
# 4b. the CLI's own invariants, checked on every answer Schemathesis observed
# --------------------------------------------------------------------------- #


def envelope_violations(envelope: dict) -> list[str]:
    """The invariants ``cli_contract.build_envelope`` documents, verified per response.

    These are the contract's hard rules — not a Schemathesis schema check. A violation here is a
    real defect in the CLI, whatever the HTTP status was.
    """
    problems: list[str] = []
    if str(envelope.get("schema_version")) != cli_contract.SCHEMA_VERSION:
        problems.append(f"schema_version={envelope.get('schema_version')!r} "
                        f"(contract says {cli_contract.SCHEMA_VERSION!r})")
    exit_code = envelope.get("exit_code")
    if exit_code not in cli_contract.EXIT_NAMES:
        problems.append(f"exit_code={exit_code!r} is not in the shared exit-code set")
    if bool(envelope.get("ok")) != (exit_code == cli_contract.EXIT_OK):
        problems.append(f"ok={envelope.get('ok')!r} contradicts exit_code={exit_code!r}")
    error = envelope.get("error")
    if exit_code != cli_contract.EXIT_OK and not isinstance(error, dict):
        problems.append(f"exit_code={exit_code!r} without an error record")
    if isinstance(error, dict):
        code = error.get("code")
        if code not in cli_contract.EXIT_ERROR_CODES.values():
            problems.append(f"error.code={code!r} is not in the stable vocabulary")
        if not str(error.get("message") or "").strip():
            problems.append("error.message is empty")
    if envelope.get("ok") and not envelope.get("evidence"):
        problems.append("ok: true without evidence (the contract forbids it)")
    for key in ("evidence", "warnings", "next_actions"):
        if not isinstance(envelope.get(key), list):
            problems.append(f"{key} is {type(envelope.get(key)).__name__}, not a list")
    return problems


def command_field_mismatch(envelope: dict, routed: str) -> str:
    """The envelope's ``command`` vs the routed leaf path.

    ``cli_contract.usage_envelope`` names only the first non-flag token of argv, so a usage error
    reports ``tools`` where the route is ``tools.list``. That laxity is *reported*, not asserted:
    a consumer can still find the group, but not the leaf.
    """
    reported = str(envelope.get("command") or "")
    if reported == routed:
        return ""
    group = routed.rsplit(".", 1)[0] if "." in routed else routed
    if reported == group:
        return f"command={reported!r} names the group, not the leaf {routed!r}"
    return f"command={reported!r} matches neither {routed!r} nor its group {group!r}"


def interactions_from_ndjson(path: Path) -> list[dict]:
    """Every request/response pair Schemathesis really made, with the body parsed."""
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        scenario = event.get("ScenarioFinished")
        if not scenario:
            continue
        recorder = scenario.get("recorder") or {}
        for case_id, entry in (recorder.get("interactions") or {}).items():
            value = ((recorder.get("cases") or {}).get(case_id) or {}).get("value") or {}
            response = entry.get("response") or {}
            body_text = _decode_content(response.get("content") or {}, 20000)
            try:
                body = json.loads(body_text)
            except ValueError:
                body = None
            out.append(
                {
                    "case_id": case_id,
                    "phase": scenario.get("phase"),
                    "method": (entry.get("request") or {}).get("method"),
                    "uri": (entry.get("request") or {}).get("uri"),
                    "path": value.get("path"),
                    "status_code": response.get("status_code"),
                    "body": body,
                    "body_text": body_text[:200],
                }
            )
    return out


#: Failures that are real, reproduced and *recorded* — each one is a contract defect somebody has
#: to fix, not a shrug. The key is deliberate: a NEW command with the same defect is not covered
#: by an entry for another command, so the gate still fires.
KNOWN_CONTRACT_GAPS: tuple[dict, ...] = (
    {
        "operation": "skills.list",
        "check": "response_schema_conformance",
        "contains": "/data",
        "kind": "schema_gap",
        "reproduced_by": "reference_pass",
        "why": "cli_contract.COMMANDS['skills.list'] declares no data schema "
               "(data_available: false, "
               "NO_SCHEMA = type null), but the command emits data {skills, count, db}",
        "fix": "declare the payload schema for skills.list in cli_contract.COMMANDS (or stop "
               "data) — the GUI renders forms from that schema",
    },
    {
        "operation": "cu.demo",
        "check": "response_schema_conformance",
        "contains": "/data",
        "kind": "schema_gap",
        "reproduced_by": "reference_pass",
        "why": "cli_contract.COMMANDS['cu.demo'] declares data_available: false, but the command "
               "emits the fixture verdict as data {scope, status, actions, ...}",
        "fix": "declare the cu.demo payload schema (it is the documented in-process fixture "
               "scope='in-process fixture')",
    },
    {
        "operation": "desktop.status",
        "check": "response_schema_conformance",
        "contains": "/data",
        "kind": "schema_gap",
        "reproduced_by": "reference_pass",
        "why": "cli_contract.COMMANDS['desktop.status'] declares data_available: false, but the "
               "command emits data {desktop_installed, hermes, hermes_ok, plugin_dir, plugin_ok}",
        "fix": "declare the desktop.status payload schema (the GUI's deployment screen needs it)",
    },
    {
        "operation": "memory-retrieval-check",
        "check": "response_schema_conformance",
        "contains": "/data",
        "kind": "schema_gap",
        # measured 2026-09-25 in the Skaldir_Agent workspace: this one comes back from the
        # reference pass — but not on every run, because the command only emits data in
        # `conditional` is what says so.
        "reproduced_by": "reference_pass",
        "conditional": True,
        "condition": "only on a successful invocation — the reference pass calls it with "
                     "placeholder arguments that fail the command first, so it sees data: "
                     "null and reports no contradiction",""
        "why": "cli_contract.COMMANDS['memory-retrieval-check'] declares data_available: false, "
               "successful invocation emits data {stdout, enforcement_gaps, ...} — the reference "
               "pass sees data: null only because its placeholder arguments fail the command first",
        "fix": "declare the memory-retrieval-check payload schema in cli_contract.COMMANDS",
    },
    {
        "operation": "completion.override",
        "check": "reference_pass",
        "contains": "",
        "kind": "schema_gap",
        # Re-measured 2026-09-25 across four runs: `completion.override` and
        # `memory-retrieval-check` alternate as "reproduced / not reproduced", because each
        # only emits data in some state. The contract gap is constant (both declare
        # `data_schema: null`); the *observation* of it is not. `conditional` is the honest
        # flag for that, and the gate below allows a missing reproduction only for entries
        # that carry it — never for a gap that is supposed to be unconditional.
        "reproduced_by": "reference_pass",
        "conditional": True,
        "condition": "only when a completion is open — the reference pass calls it with no "
                     "completion, so the emission (and the contradiction) appears on some "
                     "runs and not on others",
        "why": "cli_contract.COMMANDS['completion.override'] declares no data schema, and the "
               "command emits data {state, reason, is_verified_complete} with exit 0 — but "
               "only in some states, so the reference pass reproduces it on some runs and "
               "not on others (measured 2026-09-25 over four runs)",
        "fix": "declare the completion.override payload schema in cli_contract.COMMANDS, and "
               "have the reference pass open a completion first so the gap is reproducible",
    },
)


def match_known_gap(example: dict) -> dict | None:
    for gap in KNOWN_CONTRACT_GAPS:
        if (example.get("operation") == gap["operation"]
                and example.get("check") == gap["check"]
                and gap["contains"] in str(example.get("message") or "")):
            return gap
    return None


#: Findings this tier reproduced that are **not** Schemathesis check failures: the CLI's answer was
#: a well-formed envelope, the defect is behind it. Each entry is measured, has a reproducer, and
#: names the fix. The property run reports them; the tests gate them by attribution — a mode change
#: no entry explains is a failure.
KNOWN_DEFECTS: tuple[dict, ...] = (
    {
        "id": "cwd-chmod-on-empty-path-argument",
        "kind": "real_bug",
        "severity": "high",
        "commands": ("spend.allowance", "spend.approve", "spend.deny", "spend.complete",
                     "spend.list", "spend.request"),
        "where": "src/godsv/treasury.py:674 `_restrict(Path(self.path), 0o600)` in "
                 "TreasuryState.__init__, reached with `--db \"\"` (Path('') == Path('.'))",
        "evidence": "measured: `godsvoice spend allowance --db \"\" --json` answers exit 0, ok:"
                    " true and leaves the WORKING DIRECTORY at mode 0600 (no execute bit). The "
                    "next process started there fails with EACCES and `cd` into it is refused — a "
                    "green envelope over a destructive side effect on a path nobody named.",
        "fix": "resolve path arguments once at parse time: an empty value must fall back to the "
               "documented default or be refused with exit 1 (usage); and `_restrict` must never "
               "chmod a directory (only `path if path.is_file()`), nor chmod `path.parent` of a "
               "caller-supplied relative name (treasury.py:109 has the same shape for Vault)",
    },
    {
        "id": "exit-7-internal-for-a-missing-path-argument",
        "kind": "real_bug",
        "severity": "medium",
        "commands": ("scan", "tools.scan", "tools.propose", "pact-show", "ledger-show"),
        "where": "src/godsv/secretscan.py / src/godsv/pact.py / src/godsv/ledger.py — the path is "
                 "opened without a guard, the FileNotFoundError is caught by "
                 "cli_contract.run_command and reported as exit 7 (internal)",
        "evidence": "measured: `godsvoice scan /nonexistent --json` -> exit 7 with "
                    "error.message 'FileNotFoundError: [Errno 2] ...' and a traceback on stderr; "
                    "`godsvoice pact-show --json` in a directory without .godsv/ does the same. "
                    "Exit 7 means 'this is a bug in the command' — a missing file is the caller's "
                    "input, and the designed verdict for it is 5 (not_found).",
        "fix": "guard the open()/read in the handlers and return cli_contract.not_found(...) "
               "(exit 5) for a path that does not exist; keep exit 7 for real internal failures",
    },
)


def match_known_defect(change: dict) -> dict | None:
    """Which recorded defect explains a sandbox mode change? ``None`` = unexplained."""
    command = str(change.get("command") or "")
    for defect in KNOWN_DEFECTS:
        if command in defect["commands"]:
            return defect
    return None


# --------------------------------------------------------------------------- #
# 4c. the reference pass: one real invocation per operation, contract vs reality
# --------------------------------------------------------------------------- #


def reference_argv(operation: dict) -> tuple[list[str], dict]:
    """Minimal valid argv for one command: every required arg gets a typed placeholder."""
    args: dict = {}
    for record in operation["args"]:
        if not record["required"]:
            continue
        kind = record["type"]
        if kind == "flag":
            args[str(record["dest"])] = False
        elif kind == "choice" and record["choices"]:
            args[str(record["dest"])] = str(record["choices"][0])
        elif kind in ("integer", "count"):
            args[str(record["dest"])] = 1
        elif kind == "number":
            args[str(record["dest"])] = 1
        else:
            args[str(record["dest"])] = "gv-schemathesis"
    return render_argv(operation["command"], args, operation["args"]), args


def reference_pass(operations: list[dict], godsvoice: Path, sandbox: Path, *,
                   timeout_s: float, stats: ShimStats | None = None) -> list[dict]:
    """Call every operation once with its required args — the complete per-command comparison.

    Schemathesis' generation covers some combinations; this pass covers *every* command, so the
    "declared data schema vs real payload" class can be reported exhaustively instead of sampled.
    """
    env = dict(os.environ)
    env.update({"GODSV_HOME": str(sandbox), "HERMES_HOME": str(sandbox), "NO_COLOR": "1"})
    env.pop("GODSV_FORMAT", None)
    rows: list[dict] = []
    for operation in operations:
        argv, args = reference_argv(operation)
        row: dict = {"command": operation["command"], "data_available": operation["data_available"]}
        try:
            proc = subprocess.run(
                [str(godsvoice), *argv, "--json"], capture_output=True, text=True,
                cwd=str(sandbox), env=env, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            rows.append(dict(row, status="timeout"))
            continue
        except OSError as exc:
            rows.append(dict(row, status="spawn_failed", detail=str(exc)))
            continue
        finally:
            if stats is not None:
                guard_sandbox(sandbox, stats, operation["command"], argv)
        try:
            envelope = json.loads(proc.stdout)
        except ValueError:
            rows.append(dict(row, status="no_envelope", exit_code=proc.returncode,
                             stdout=proc.stdout[:200], stderr=proc.stderr[-200:]))
            continue
        data = envelope.get("data")
        row.update(
            {
                "status": "answered",
                "exit_code": envelope.get("exit_code"),
                "ok": envelope.get("ok"),
                "data_is_null": data is None,
                "data_keys": sorted(data) if isinstance(data, dict) else None,
                "violations": envelope_violations(envelope),
                "command_field": command_field_mismatch(envelope, operation["command"]),
            }
        )
        # Two different things, kept apart on purpose:
        #  * data_mismatch — the contract explicitly promises `data: null` (NO_SCHEMA) and the
        #    command sent a payload anyway: the self-description is wrong.
        #  * data_absent_on_error — a failing exit returned no payload although the command declares
        #    a data schema. The envelope allows null there, so this is an observation, not a defect.
        row["data_mismatch"] = (not operation["data_available"]) and data is not None
        row["data_absent_on_error"] = (
            operation["data_available"] and data is None
            and envelope.get("exit_code") != cli_contract.EXIT_OK
        )
        if row["data_mismatch"]:
            row["mismatch"] = (
                f"declared data_available={operation['data_available']} but data is "
                f"{type(data).__name__}"
            )
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# 5. CLI
# --------------------------------------------------------------------------- #


def _fmt_example(example: dict) -> str:
    lines = [line for line in str(example.get("message") or "").splitlines() if line.strip()]
    return (
        f"  - [{example['classification']}] {example['operation']} "
        f"({example.get('phase')}/{example.get('generation_mode')}) check={example.get('check')}\n"
        f"      case {example.get('case_id')} -> {example.get('title')}\n"
        f"      {(lines[0] if lines else '<no message>')[:200]}\n"
        f"      request body: {str((example.get('request') or {}).get('body'))[:200]}\n"
        f"      response {((example.get('response') or {}).get('status_code'))}: "
        f"{str((example.get('response') or {}).get('body'))[:200]}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Schemathesis property tests over the CLI's JSON contract (honest gap report)."
    )
    parser.add_argument("--examples", "-n", type=int, default=3,
                        help="max generated test cases per operation (default 3)")
    parser.add_argument("--seed", type=int, default=20260918, help="Schemathesis seed")
    parser.add_argument("--phases", default="coverage,fuzzing",
                        help="Schemathesis phases (default coverage,fuzzing)")
    parser.add_argument("--mode", default="all", choices=("positive", "negative", "all"))
    parser.add_argument("--workers", type=int, default=1, help="Schemathesis workers (default 1)")
    parser.add_argument("--max-time", type=int, default=None, help="time budget for the run")
    parser.add_argument("--only", default="", help="comma-separated command paths to include")
    parser.add_argument("--skip", default="", help="extra command paths to exclude")
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="seconds one CLI invocation may take (default 120)")
    parser.add_argument("--base-url", default="",
                        help="target an already running `skaldir serve --http` instead of the "
                             "stand-in shim (same schema, same checks, no shim in the path)")
    parser.add_argument("--schema-out", default="", help="where to write the OpenAPI document")
    parser.add_argument("--report-dir", default="", help="where the Schemathesis reports go")
    parser.add_argument("--artifact", default="", help="the JSON artifact of this run")
    parser.add_argument("--keep-sandbox", action="store_true",
                        help="keep the throwaway GODSV_HOME (debugging only)")
    parser.add_argument("--no-reference-pass", action="store_true",
                        help="skip the per-command reference pass (one real invocation each)")
    args = parser.parse_args(argv)

    started = time.time()
    if not GODSVOICE.exists():
        raise SystemExit(f"no godsvoice CLI at {GODSVOICE}")
    if not args.base_url and not SCHEMATHESIS.exists():
        raise SystemExit(f"no schemathesis at {SCHEMATHESIS}")

    report_dir = Path(args.report_dir) if args.report_dir else ROOT / "outputs/schemathesis"
    report_dir.mkdir(parents=True, exist_ok=True)
    schema_path = (Path(args.schema_out) if args.schema_out
                   else report_dir / "cli_contract.openapi.json")
    json_report = report_dir / "schemathesis-report.json"
    ndjson_report = report_dir / "schemathesis-report.ndjson"
    artifact_path = Path(args.artifact) if args.artifact else report_dir / "schemathesis-run.json"

    dump = contract_dump(GODSVOICE)
    exclude = dict(EXCLUDED)
    for extra in args.skip.split(","):
        if extra.strip():
            exclude[extra.strip()] = "excluded by --skip"
    only = [item.strip() for item in args.only.split(",") if item.strip()] or None
    document, operations, skipped = build_openapi(dump, exclude=exclude, only=only)
    schema_path.write_text(json.dumps(document, indent=2, ensure_ascii=False))

    print(f"contract: {dump['data']['leaf_commands']} leaf commands, "
          f"schema_version={dump['data']['schema_version']}, "
          f"godsvoice {dump['data']['godsvoice_version']}")
    print(f"openapi:  {len(operations)} operations -> {schema_path}")
    print(f"excluded: {len(skipped)} operation(s)")
    for item in skipped:
        print(f"  - {item['command']}: {item['reason']}")

    sandbox = Path(tempfile.mkdtemp(prefix="gv-schemathesis-"))
    server = None
    stats = ShimStats()
    base_url = args.base_url
    rows: list[dict] = []
    interactions: list[dict] = []
    try:
        if not base_url:
            server, stats, _ = build_shim(GODSVOICE, operations, sandbox=sandbox,
                                         timeout_s=args.timeout)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            print(f"shim:     {base_url} (stand-in; the CLI runs as a subprocess in {sandbox})")
        else:
            print(f"target:   {base_url} (real HTTP surface — no shim)")

        if not args.no_reference_pass:
            rows = reference_pass(operations, GODSVOICE, sandbox,
                                  timeout_s=args.timeout, stats=stats)
            answered = [row for row in rows if row["status"] == "answered"]
            mismatches = [row for row in answered if row.get("data_mismatch")]
            print(f"reference: {len(answered)}/{len(rows)} command(s) answered; "
                  f"{len(mismatches)} declared-data mismatch(es); "
                  f"{sum(1 for row in answered if row['violations'])} with envelope violations")

        proc = run_schemathesis(
            schema_path, base_url, examples=args.examples, seed=args.seed, phases=args.phases,
            mode=args.mode, workers=args.workers, checks=CHECKS,
            json_report=json_report, ndjson_report=ndjson_report, max_time=args.max_time,
        )
        print("=" * 78)
        print(proc.stdout.strip())
        print("=" * 78)
        if proc.stderr.strip():
            print("schemathesis stderr tail:")
            print("\n".join(proc.stderr.strip().splitlines()[-10:]))
        if not json_report.exists():
            print("no JSON report was written — the run did not complete")
            return 2
        report, examples = summarize(
            json_report, ndjson_report, operations,
            target="real" if args.base_url else "shim",
        )
        interactions = interactions_from_ndjson(ndjson_report)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if not args.keep_sandbox:
            shutil.rmtree(sandbox, ignore_errors=True)

    # the CLI's own hard rules, verified on every answer that really came back
    invariant_violations: list[dict] = []
    command_field_mismatches: list[dict] = []
    answered_interactions = 0
    for item in interactions:
        body = item.get("body")
        if not isinstance(body, dict) or item.get("status_code") != 200:
            continue
        answered_interactions += 1
        routed = next(
            (op["command"] for op in operations
             if op["path"] == "/" + str(item.get("uri") or "").split("/", 3)[-1]),
            "",
        )
        problems = envelope_violations(body)
        if problems:
            invariant_violations.append({"case_id": item["case_id"], "routed": routed,
                                         "problems": problems})
        mismatch = command_field_mismatch(body, routed) if routed else ""
        if mismatch:
            command_field_mismatches.append({"case_id": item["case_id"], "routed": routed,
                                             "detail": mismatch})
    for row in rows:
        if row.get("violations"):
            invariant_violations.append({"reference_pass": row["command"],
                                         "problems": row["violations"]})
        if row.get("command_field"):
            command_field_mismatches.append({"reference_pass": row["command"],
                                             "detail": row["command_field"]})

    known = [example for example in examples if example.get("known_gap")]
    new_failures = [example for example in examples if not example.get("known_gap")]

    # every destroyed-cwd observation must be explained by a recorded defect
    mode_changes = []
    for change in stats.sandbox_mode_changes:
        defect = match_known_defect(change)
        mode_changes.append(dict(change, defect_id=(defect or {}).get("id") or "",
                                 classified=bool(defect)))
    unexplained_mode_changes = [change for change in mode_changes if not change["classified"]]

    counts: dict[str, int] = {}
    for example in examples:
        counts[example["classification"].split(" (")[0]] = (
            counts.get(example["classification"].split(" (")[0], 0) + 1
        )

    print("=" * 78)
    print("REAL NUMBERS")
    print(f"operations declared by the contract : {dump['data']['leaf_commands']}")
    print(f"operations compiled into OpenAPI     : {len(operations) + len(skipped)}")
    print(f"operations excluded (recorded)       : {len(skipped)}")
    print(f"operations selected by Schemathesis  : {report['operations']['selected']}")
    print(f"operations tested                    : {report['operations']['tested']}")
    print(f"test phases                          : {report['phases']}")
    print(f"generated test cases                 : {report['test_cases']['generated']}")
    print(f"cases with failures                  : {report['test_cases']['with_failures']}")
    print(f"unique failures                      : {report['test_cases']['unique_failures']}")
    print(f"unique failure types                 : {report['failures']}")
    print(f"schemathesis exit code               : {report['exit_code']}")
    print(f"answers re-checked against the CLI   : {answered_interactions} (200 responses)")
    print(f"envelope invariant violations        : {len(invariant_violations)}")
    print(f"envelope.command naming the group    : {len(command_field_mismatches)} "
          f"(reported, not asserted — see below)")
    if rows:
        answered_rows = [row for row in rows if row["status"] == "answered"]
        print(f"reference pass (1 real call/command) : {len(answered_rows)}/{len(rows)} answered, "
              f"{sum(1 for row in answered_rows if row.get('data_mismatch'))} data mismatch(es)")
        for row in rows:
            if row.get("data_mismatch"):
                print(f"    ! {row['command']}: {row['mismatch']} — exit {row.get('exit_code')}")
    print(f"failures matched to a recorded gap   : {len(known)}")
    print(f"NEW failures (no recorded gap)       : {len(new_failures)}")
    print(f"commands that chmod their own cwd    : {len(mode_changes)} invocation(s) "
          f"({len(unexplained_mode_changes)} unexplained)")
    for change in mode_changes[:8]:
        print(f"    ! [{change['defect_id'] or 'UNEXPLAINED'}] {change['detail']}")
    if rows:
        absent = [row["command"] for row in rows if row.get("data_absent_on_error")]
        print(f"declared data but none on a failing exit: {len(absent)} command(s) "
              f"(allowed by the envelope, observed: {', '.join(absent[:6])}"
              f"{' ...' if len(absent) > 6 else ''})")
    if server is not None:
        print(f"shim: {stats.requests} request(s), {stats.cli_calls} CLI invocation(s), "
              f"{stats.cli_seconds:.1f}s inside the CLI, "
              f"{stats.transport_rejections} transport rejection(s), "
              f"{stats.envelope_failures} envelope failure(s), {stats.timeouts} timeout(s), "
              f"statuses={stats.statuses}")
    print(f"wall time                            : {time.time() - started:.1f}s")
    if examples:
        print("-" * 78)
        print(f"FAILING EXAMPLES ({len(examples)})")
        for example in examples[:40]:
            print(_fmt_example(example))
        if len(examples) > 40:
            print(f"  ... {len(examples) - 40} more in the artifact")
    print("=" * 78)
    print("PRODUCT GAP: " + PRODUCT_GAP["missing"])
    print("  delivered:  " + " ".join(PRODUCT_GAP["delivered"].split()))
    print("  measured:   " + " ".join(PRODUCT_GAP["measured"].split()))
    for line in PRODUCT_GAP["remaining"]:
        print("  still open: " + " ".join(line.split()))
    for step in PRODUCT_GAP["exact_change"]:
        print(f"    * {step}")

    artifact = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "runner": "tools/gv_schemathesis_run.py",
        "schema_source": "godsvoice contract dump --json",
        "schema_version": dump["data"]["schema_version"],
        "godsvoice_version": dump["data"]["godsvoice_version"],
        "target": base_url,
        "target_kind": "real http surface" if args.base_url else "stand-in shim (test only)",
        "openapi_path": str(schema_path),
        "schemathesis": {
            "version": report.get("schemathesis_version"),
            "command": report.get("command"),
            "seed": report.get("seed"),
            "phases": report.get("phases"),
            "operations": report.get("operations"),
            "test_cases": report.get("test_cases"),
            "failures": report.get("failures"),
            "exit_code": report.get("exit_code"),
            "running_time_s": report.get("running_time"),
            "checks": list(CHECKS),
        },
        "contract": {
            "leaf_commands": dump["data"]["leaf_commands"],
            "unregistered": dump["data"]["unregistered"],
            "operations_compiled": len(operations) + len(skipped),
            "operations_selected": len(operations),
            "excluded": skipped,
        },
        "cli_invariants": {
            "answers_rechecked": answered_interactions,
            "envelope_violations": invariant_violations,
            "envelope_command_naming_the_group": command_field_mismatches,
            "note": "usage envelopes (cli_contract.usage_envelope) name the first non-flag argv "
                    "token, so a usage error reports the group instead of the leaf command; the "
                    "property tier reports that gap instead of asserting it away",
        },
        "reference_pass": {
            "rows": rows,
            "data_mismatches": [
                {"command": row["command"], "mismatch": row.get("mismatch"),
                 "exit_code": row.get("exit_code"), "data_keys": row.get("data_keys")}
                for row in rows if row.get("data_mismatch")
            ],
        },
        "known_gaps": KNOWN_CONTRACT_GAPS,
        "known_defects": KNOWN_DEFECTS,
        "failures_matched_to_a_gap": len(known),
        "new_failures": [example["case_id"] + " " + str(example["operation"])
                         for example in new_failures],
        "shim": {
            "used": not bool(args.base_url),
            "requests": stats.requests,
            "cli_invocations": stats.cli_calls,
            "cli_seconds": round(stats.cli_seconds, 3),
            "transport_rejections": stats.transport_rejections,
            "envelope_failures": stats.envelope_failures,
            "timeouts": stats.timeouts,
            "sandbox_mode_changes": mode_changes,
            "unexplained_mode_changes": unexplained_mode_changes,
            "statuses": {str(key): value for key, value in stats.statuses.items()},
            "errors": stats.errors[:50],
        },
        "failures_total": len(examples),
        "failures_by_class": counts,
        "failing_examples": examples,
        "product_gap": PRODUCT_GAP,
    }
    artifact_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False))
    print(f"artifact: {artifact_path}")
    return 1 if examples else 0


if __name__ == "__main__":
    raise SystemExit(main())
