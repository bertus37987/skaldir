#!/usr/bin/env python3
"""A tiny, dependency-free MCP test server (stdio AND streamable HTTP).

Why a hand-rolled server instead of only ``fastmcp``: the adapter's two most
interesting behaviours are (1) refusing a tool whose JSON schema cannot be typed
and (2) surviving a server that answers over SSE instead of plain JSON. Those
need a server whose schemas and wire format we control exactly. It is stdlib
only, so the test needs no network and no container.

    python tests/fixtures/mcp_test_server.py stdio [--log FILE] [--page-size N]
                                                           [--protocol V] [--omit-tools]
    python tests/fixtures/mcp_test_server.py http  [--host H] [--port N] [--sse]
                                                   [--log FILE] [--page-size N]
                                                   [--protocol V] [--omit-tools] [--announce FILE]

Tools exposed (deliberately mixed):

    add_numbers       typed     {a:int, b:int}      -> {sum:int}
    echo_text         typed     {text:str}          -> {echo:str}
    server_now        typed     {}                  -> {iso:str}
    always_errors     typed     {reason:str}        -> isError
    nested_typed      typed     $defs/$ref, anyOf[T,null], ["T","null"], an array of
                                objects, a nested object, an enum, defaults
    opaque_blob       REFUSED   property type "binary-blob"
    freeform_config   REFUSED   free-form object property
    union_mode        typed     property type ["string","null"] (nullable string)
    missing_required  REFUSED   required names a property that is not declared
    no_schema         REFUSED   no inputSchema at all
    oneof_hetero      REFUSED   oneOf offers two different types
    recursive_node    REFUSED   $ref that points back at itself
    deep_freeform     REFUSED   free-form object nested inside an array of objects

``--log FILE`` writes the raw request/response transcript the SERVER saw, so a
handshake can be proven from both ends.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "godsv-mcp-fixture", "version": "0.1.0"}
SERVER_CAPABILITIES = {"tools": {"listChanged": False}}

_FIXTURE_TOOLS: list[dict] = [
    {
        "name": "add_numbers",
        "title": "Add two numbers",
        "description": "Return the sum of two integers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "integer", "description": "first addend"},
                "b": {"type": "integer", "description": "second addend"},
            },
            "required": ["a", "b"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {"sum": {"type": "integer"}},
            "required": ["sum"],
        },
    },
    {
        "name": "echo_text",
        "title": "Echo",
        "description": "Echo the given text back.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "outputSchema": {"type": "object", "properties": {"echo": {"type": "string"}}},
    },
    {
        "name": "server_now",
        "title": "Server clock",
        "description": "Return the server's notion of the current time.",
        "inputSchema": {"type": "object", "properties": {}},
        "outputSchema": {"type": "object", "properties": {"iso": {"type": "string"}}},
    },
    {
        "name": "always_errors",
        "title": "Always fails",
        "description": "Return an MCP tool error (used to check the error path).",
        "inputSchema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
        },
        "outputSchema": None,
    },
    # --- the refusal cases: each one has a schema the adapter must reject ----
    {
        "name": "opaque_blob",
        "title": "Opaque blob",
        "description": "Takes a blob type that no typed capability can express.",
        "inputSchema": {
            "type": "object",
            "properties": {"blob": {"type": "binary-blob"}},
            "required": ["blob"],
        },
    },
    {
        "name": "freeform_config",
        "title": "Free-form config",
        "description": "Takes an untyped object without declared properties.",
        "inputSchema": {
            "type": "object",
            "properties": {"config": {"type": "object"}},
            "required": ["config"],
        },
    },
    {
        "name": "union_mode",
        "title": "Union mode",
        "description": "Takes a union type instead of one typed value.",
        "inputSchema": {
            "type": "object",
            "properties": {"mode": {"type": ["string", "null"]}},
        },
    },
    {
        "name": "missing_required",
        "title": "Missing required",
        "description": "Declares a required input that its properties do not carry.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["ghost"],
        },
    },
    {
        "name": "no_schema",
        "title": "No schema",
        "description": "Publishes no inputSchema at all.",
    },
    {
        # Every construct the mapper resolves: $defs/$ref, anyOf[..., null],
        # ["T","null"], a typed array of objects, a nested object, an enum and
        # declared defaults. It maps, including its nested input paths.
        "name": "nested_typed",
        "title": "Nested typed",
        "description": "A deeply typed tool: refs, nullable unions, arrays of objects.",
        "inputSchema": {
            "type": "object",
            "$defs": {
                "Point": {
                    "type": "object",
                    "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                    "required": ["x", "y"],
                },
                "Mode": {"type": "string", "enum": ["fast", "slow"]},
            },
            "properties": {
                "mode": {
                    "anyOf": [{"$ref": "#/$defs/Mode"}, {"type": "null"}],
                    "default": None,
                },
                "origin": {
                    "anyOf": [{"$ref": "#/$defs/Point"}, {"type": "null"}],
                    "default": None,
                },
                "path": {"type": "array", "items": {"$ref": "#/$defs/Point"}},
                "label": {"type": ["string", "null"]},
                "retries": {"type": "integer", "minimum": 0, "default": 3},
                "settings": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"verbose": {"type": "boolean", "default": False}},
                },
            },
            "required": ["path"],
        },
        "outputSchema": {
            "type": "object",
            "$defs": {
                "Point": {
                    "type": "object",
                    "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                    "required": ["x", "y"],
                }
            },
            "properties": {
                "total": {"type": "integer"},
                "points": {"type": "array", "items": {"$ref": "#/$defs/Point"}},
                "mode": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                },
            },
            "required": ["total", "points"],
        },
    },
    {
        # oneOf of two different types: genuinely more than one typed value.
        "name": "oneof_hetero",
        "title": "Heterogeneous oneOf",
        "description": "Offers a string or an integer — no single typed value holds it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "value": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
            },
            "required": ["value"],
        },
    },
    {
        # A $ref that points back at itself: the shape has no finite typed form.
        "name": "recursive_node",
        "title": "Recursive node",
        "description": "A linked list: its own $ref nests forever.",
        "inputSchema": {
            "type": "object",
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "child": {"anyOf": [{"$ref": "#/$defs/Node"}, {"type": "null"}]},
                    },
                    "required": ["label"],
                }
            },
            "properties": {"head": {"$ref": "#/$defs/Node"}},
            "required": ["head"],
        },
    },
    {
        # A free-form object nested three levels down: the refusal must name the path.
        "name": "deep_freeform",
        "title": "Deep free-form",
        "description": "Hides an untyped object inside an array of objects.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"payload": {"type": "object"}},
                        "required": ["payload"],
                    },
                }
            },
            "required": ["rows"],
        },
    },
]


def _tools_payload(page_size: int, cursor: str) -> dict:
    start = 0
    if cursor:
        try:
            start = int(cursor)
        except ValueError:
            start = 0
    size = page_size if page_size > 0 else len(_FIXTURE_TOOLS)
    window = _FIXTURE_TOOLS[start : start + size]
    payload: dict = {"tools": window}
    if start + size < len(_FIXTURE_TOOLS):
        payload["nextCursor"] = str(start + size)
    return payload


def _tool_result(name: str, arguments: dict) -> dict:
    if name == "add_numbers":
        a, b = arguments.get("a"), arguments.get("b")
        if (
            not isinstance(a, int)
            or isinstance(a, bool)
            or not isinstance(b, int)
            or isinstance(b, bool)
        ):
            return {
                "isError": True,
                "content": [{"type": "text", "text": "a and b must be integers"}],
            }
        total = a + b
        return {
            "content": [{"type": "text", "text": json.dumps({"sum": total})}],
            "structuredContent": {"sum": total},
            "isError": False,
        }
    if name == "echo_text":
        text = str(arguments.get("text") or "")
        return {
            "content": [{"type": "text", "text": json.dumps({"echo": text})}],
            "structuredContent": {"echo": text},
        }
    if name == "server_now":
        import time

        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return {
            "content": [{"type": "text", "text": json.dumps({"iso": iso})}],
            "structuredContent": {"iso": iso},
        }
    if name == "always_errors":
        return {
            "isError": True,
            "content": [{"type": "text", "text": str(arguments.get("reason") or "always fails")}],
        }
    if name == "nested_typed":
        points = [
            {"x": int(point.get("x") or 0), "y": int(point.get("y") or 0)}
            for point in (arguments.get("path") or [])
            if isinstance(point, dict)
        ]
        mode = arguments.get("mode")
        result = {
            "total": len(points),
            "points": points,
            "mode": mode if isinstance(mode, str) else None,
        }
        return {
            "content": [{"type": "text", "text": json.dumps(result)}],
            "structuredContent": result,
        }
    return {
        "isError": True,
        "content": [{"type": "text", "text": f"unknown tool: {name}"}],
    }


def _dispatch(
    message: dict, *, page_size: int = 0, protocol: str = PROTOCOL_VERSION, omit_tools: bool = False
) -> dict | None:
    """One JSON-RPC message → one response, or None for a notification."""
    method = str(message.get("method") or "")
    params = dict(message.get("params") or {})
    request_id = message.get("id")
    if request_id is None and method.startswith("notifications/"):
        return None
    if method == "initialize":
        result = {
            "protocolVersion": protocol,
            "capabilities": SERVER_CAPABILITIES,
            "serverInfo": SERVER_INFO,
        }
    elif method == "tools/list":
        result = (
            {"tools": []}
            if omit_tools
            else _tools_payload(page_size, str(params.get("cursor") or ""))
        )
    elif method == "tools/call":
        result = _tool_result(str(params.get("name") or ""), dict(params.get("arguments") or {}))
    elif method == "ping":
        result = {}
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _log_writer(path: str):
    if not path:
        return lambda direction, message: None
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    def write(direction: str, message) -> None:
        with lock:
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"direction": direction, "message": message}) + "\n")

    return write


def serve_stdio(
    *, page_size: int = 0, log: str = "", protocol: str = PROTOCOL_VERSION, omit_tools: bool = False
) -> int:
    """Newline-delimited JSON-RPC on stdin/stdout (the MCP stdio transport)."""
    write = _log_writer(log)
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            continue
        write("<-", message)
        answer = _dispatch(message, page_size=page_size, protocol=protocol, omit_tools=omit_tools)
        if answer is None:
            continue
        write("->", answer)
        sys.stdout.write(json.dumps(answer) + "\n")
        sys.stdout.flush()
    return 0


def build_http_server(
    *,
    host: str,
    port: int,
    page_size: int = 0,
    sse: bool = False,
    log: str = "",
    protocol: str = PROTOCOL_VERSION,
    omit_tools: bool = False,
):
    """A streamable-HTTP-ish JSON-RPC endpoint (POST; optional SSE responses)."""
    write = _log_writer(log)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args) -> None:  # noqa: A002 — http.server's name
            return

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _send(self, status: int, payload: dict | None, *, event_stream: bool = False) -> None:
            if payload is None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            blob = json.dumps(payload).encode("utf-8")
            if event_stream:
                body = b"event: message\ndata: " + blob + b"\n\n"
                content_type = "text/event-stream"
            else:
                body = blob
                content_type = "application/json"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Mcp-Session-Id", "fixture-session-1")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 — http.server's naming
            try:
                message = json.loads(self._body().decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send(
                    400,
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "parse error"},
                    },
                )
                return
            write("<-", message)
            answer = _dispatch(
                message, page_size=page_size, protocol=protocol, omit_tools=omit_tools
            )
            if answer is None:
                self._send(202, None)
                return
            write("->", answer)
            self._send(200, answer, event_stream=sse)

        def do_DELETE(self) -> None:  # noqa: N802 — session teardown
            self._send(204, None)

        def do_GET(self) -> None:  # noqa: N802 — readiness probe for tests
            self._send(200, {"ok": True, "server": SERVER_INFO})

    return ThreadingHTTPServer((host, port), Handler)


def serve_http(
    *,
    host: str,
    port: int,
    page_size: int = 0,
    sse: bool = False,
    log: str = "",
    announce: str = "",
    protocol: str = PROTOCOL_VERSION,
    omit_tools: bool = False,
) -> int:
    server = build_http_server(
        host=host,
        port=port,
        page_size=page_size,
        sse=sse,
        log=log,
        protocol=protocol,
        omit_tools=omit_tools,
    )
    actual_port = server.server_address[1]
    if announce:
        Path(announce).write_text(str(actual_port), encoding="utf-8")
    print(f"listening on http://{host}:{actual_port}/mcp", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _option(argv: list[str], name: str, default: str = "") -> str:
    if name in argv:
        index = argv.index(name)
        if index + 1 < len(argv):
            return argv[index + 1]
    return default


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv[0] if argv and not argv[0].startswith("-") else "stdio"
    page_size = int(_option(argv, "--page-size", "0") or 0)
    log = _option(argv, "--log", "")
    protocol = _option(argv, "--protocol", PROTOCOL_VERSION)
    omit_tools = "--omit-tools" in argv
    if mode == "http":
        return serve_http(
            host=_option(argv, "--host", "127.0.0.1"),
            port=int(_option(argv, "--port", "0") or 0),
            page_size=page_size,
            sse="--sse" in argv,
            log=log,
            announce=_option(argv, "--announce", ""),
            protocol=protocol,
            omit_tools=omit_tools,
        )
    return serve_stdio(page_size=page_size, log=log, protocol=protocol, omit_tools=omit_tools)


if __name__ == "__main__":
    raise SystemExit(main())
