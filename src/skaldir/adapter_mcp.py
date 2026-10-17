"""MCP surface adapter — the EXPAND layer's third surface (container | CLI | MCP).

A container **or a local process** may expose an MCP (Model Context Protocol)
server. This module does for MCP what :mod:`skaldir.toolbuilder`'s container half
does for images, in the same order and with the same evidence rules:

    handshake   start (stdio) or connect (streamable HTTP), ``initialize``,
                ``notifications/initialized`` — the raw transcript is kept
    list        ``tools/list`` with every tool's JSON schemas (+ pagination)
    map         each tool -> a Skaldir capability definition with TYPED
                input/output; a tool whose schema cannot be validated is
                REFUSED with its reason instead of being registered
    test        ONE chosen tool is CALLED for real through the adapter; the
                observed answer must match the declared outputs and the
                caller's expectation, and the adapter bytes are hashed into
                the evidence (so an edited adapter is never registered on
                stale evidence)
    register    the TESTED adapter goes through the EXISTING capability
                registration path — :class:`skaldir.tools.ToolSpec` +
                ``ToolStore.publish`` (registry row + publication outbox in one
                transaction), the capability-health ledger and a rollback
                snapshot. Nothing is reimplemented here.
    call        a registered MCP capability is called through its adapter

Why there is no second client implementation: the generated module for a
mapped tool (``<runtime>/generated_tools/<name>/mcp_<name>.py``) is a thin
binding to :func:`call_staged_adapter` in THIS module. A copied client would
drift from the tested one; the container half had to be self-contained because
it runs inside an image, we do not.

Honest residuals (declared, not hidden):

* ``kind`` is registered as ``mcp`` — truthful. The fastmcp **export**
  (``skaldir.mcpserver``) currently exports only ``kind="python"`` specs, so a
  registered MCP capability is callable through ``mcp call`` / the registry but
  is not yet re-exported as a tool by ``skaldir mcp serve``. That is the
  mcpserver owner's one-line filter, recorded as a warning, never faked.
* ``test`` has no independent local oracle (an arbitrary remote tool has no
  local twin). The evidence is therefore: a REAL call, output types checked
  against the declared schema, and equality against a caller-declared
  expectation. No oracle is claimed where none exists.
"""

from __future__ import annotations

import json
import os
import re
import select
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .toolbuilder import _sha256_file, _write_text

# --------------------------------------------------------------------------- #
# constants                                                                     #
# --------------------------------------------------------------------------- #

#: The registry kind of a mapped MCP capability. Truthful, never "python".
MCP_KIND = "mcp"

#: Manifest ``type`` of a mapped MCP capability (``capability_mining.TYPE_MCP``
#: uses the same word, so every existing reader understands the entry).
MCP_MANIFEST_TYPE = "mcp"

MCP_ADAPTER_FILE = "adapter.json"
MCP_EVIDENCE_FILE = "mcp_evidence.json"
MCP_SURFACE_FILE = "mcp_surface.json"
MCP_TOOL_DIR = "generated_tools"

#: The protocol versions this client knows how to speak. A server answering
#: with anything else is refused unless the caller explicitly allows it.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

DEFAULT_TIMEOUT = 30.0
MAX_RESPONSE_BYTES = 1_000_000

#: What a mapped MCP capability is allowed to do. The server is a separate
#: process (or endpoint) the caller configured; the adapter itself passes JSON
#: and owns no host resources.
MCP_PERMISSIONS: dict = {
    "process": {
        "spawns": "one MCP server process (stdio) or one HTTP endpoint",
        "note": "the adapter is a client — it executes no third-party code in-process",
    },
    "network": {
        "allowed_domains": [],
        "note": "stdio: no network; http: the declared endpoint only",
    },
    "filesystem": {
        "access": "none — the adapter exchanges JSON only; the server owns its files",
    },
    "secrets": [],
    "tools": {"allowlist": "only the mapped tool names"},
    "resource_limits": {
        "timeout_seconds": DEFAULT_TIMEOUT,
        "max_response_bytes": MAX_RESPONSE_BYTES,
    },
}

_SLUG = re.compile(r"[^a-z0-9]+")

#: JSON Schema types an input/output can be typed as. Anything else is refused.
_TYPABLE = frozenset({"string", "integer", "number", "boolean", "array", "object", "null"})

#: Keywords that genuinely describe more than one typed value: refused, naming the
#: construct. ``$ref``/``anyOf``/``oneOf``/``allOf`` are NOT here — they are
#: combined down to one typed value first (``_typed_shape``, ``_combine_alternatives``)
#: and only refused when they do not reduce.
_UNMAPPABLE_KEYS = (
    "not",
    "if",
    "then",
    "else",
    "patternProperties",
    "dependentSchemas",
    "dependencies",
    "$dynamicRef",
    "unevaluatedProperties",
)

#: ``$ref`` pointers this mapper follows: only the tool's own ``$defs``/``definitions``.
_REF_PREFIXES = ("#/$defs/", "#/definitions/")

#: Refinements the mapper CARRIES on the parameter but does not enforce. They narrow
#: a value whose type is already known, so they do not block mapping — dropping them
#: silently would be a lie, hence ``constraints`` on the entry.
_CONSTRAINT_KEYS = (
    "pattern",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
)

#: How deep a nested schema may nest before the mapper calls the shape unbounded.
_MAX_SCHEMA_DEPTH = 24
#: Marks "this branch also allows null" while a schema is being combined down.
_NULLABLE = "x-godsv-nullable"


class MCPError(RuntimeError):
    """The server could not be reached, started or spoken to."""


class MCPRefusal(RuntimeError):
    """The tool does not map onto a typed capability — refused with a reason."""


# --------------------------------------------------------------------------- #
# transcript                                                                    #
# --------------------------------------------------------------------------- #


class Transcript:
    """The raw message log of a session — the handshake proof, not a summary."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def add(self, direction: str, kind: str, payload: Any, *, note: str = "") -> None:
        entry = {
            "seq": len(self.entries) + 1,
            "at": time.time(),
            "direction": direction,  # "->" client to server, "<-" server to client
            "kind": kind,  # request | notification | response | error | transport
            "note": note,
            "message": payload,
        }
        self.entries.append(entry)

    def as_list(self) -> list[dict]:
        return [dict(entry) for entry in self.entries]

    def methods(self) -> list[str]:
        return [
            str((entry.get("message") or {}).get("method") or entry.get("kind"))
            for entry in self.entries
        ]


# --------------------------------------------------------------------------- #
# transport: stdio                                                              #
# --------------------------------------------------------------------------- #


class _LineReader:
    """Newline-delimited reads with a real timeout (no blocking readline)."""

    def __init__(self, stream) -> None:
        self.stream = stream
        self.buffer = b""
        self.eof = False

    def readline(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + max(0.01, timeout)
        while True:
            index = self.buffer.find(b"\n")
            if index >= 0:
                line, self.buffer = self.buffer[:index], self.buffer[index + 1 :]
                return line
            if self.eof:
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("no answer from the MCP server")
            ready, _, _ = select.select([self.stream], [], [], min(remaining, 0.5))
            if not ready:
                continue
            chunk = os.read(self.stream.fileno(), 65536)
            if not chunk:
                self.eof = True
                continue
            self.buffer += chunk


class StdioTransport:
    """One MCP server as a child process, newline-delimited JSON-RPC on stdio."""

    name = "stdio"

    def __init__(
        self,
        command: list[str],
        *,
        env: dict | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transcript: Transcript | None = None,
    ) -> None:
        self.command = [str(part) for part in command]
        self.env = dict(env or {})
        self.timeout = float(timeout)
        self.transcript = transcript or Transcript()
        self.process: subprocess.Popen | None = None
        self._reader: _LineReader | None = None
        self._stderr: list[str] = []
        self._stderr_thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.command:
            raise MCPError("a stdio MCP server needs a command")
        environment = dict(os.environ)
        environment.update({str(k): str(v) for k, v in self.env.items()})
        try:
            self.process = subprocess.Popen(  # noqa: S603 - the caller's own command
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=environment,
            )
        except (OSError, ValueError) as exc:
            raise MCPError(f"the MCP server could not be started: {exc}") from exc
        self._reader = _LineReader(self.process.stdout)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        self.transcript.add("->", "transport", {"command": self.command}, note="server started")

    def _drain_stderr(self) -> None:
        """Drain stderr so a chatty server can never block on a full pipe."""
        stream = getattr(self.process, "stderr", None)
        if stream is None:
            return
        while True:
            try:
                chunk = os.read(stream.fileno(), 4096)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            self._stderr.append(chunk.decode("utf-8", "replace"))
            if len(self._stderr) > 200:
                del self._stderr[:100]

    def send(self, message: dict) -> None:
        if self.process is None or self.process.stdin is None:
            raise MCPError("the MCP server is not started")
        blob = json.dumps(message, ensure_ascii=False, default=str)
        try:
            self.process.stdin.write((blob + "\n").encode("utf-8"))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise MCPError(f"the MCP server closed its stdin: {exc}") from exc
        self.transcript.add(
            "->",
            _kind_of(message),
            message,
            note="",
        )

    def notify(self, message: dict) -> None:
        self.send(message)

    def receive(self, want_id: Any, timeout: float | None = None) -> dict | None:
        if self._reader is None:
            raise MCPError("the MCP server is not started")
        deadline = time.monotonic() + float(timeout or self.timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError(
                    f"timed out after {float(timeout or self.timeout):.0f}s "
                    f"waiting for id={want_id}"
                )
            line = self._reader.readline(remaining)
            if line is None:
                raise MCPError(
                    "the MCP server closed stdout before answering"
                    + (f" (stderr: {self.stderr_tail()})" if self.stderr_tail() else "")
                )
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                self.transcript.add("<-", "transport", text[:400], note="not JSON — ignored")
                continue
            self.transcript.add("<-", _kind_of(message), message, note="")
            if want_id is None or message.get("id") == want_id:
                return message

    def stderr_tail(self, limit: int = 400) -> str:
        return "".join(self._stderr)[-limit:].strip()

    def close(self) -> None:
        if self.process is None:
            return
        for step in (self.process.stdin, getattr(self.process, "stdout", None)):
            try:
                if step is not None:
                    step.close()
            except Exception:  # noqa: BLE001 — closing is best effort
                pass
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except Exception:  # noqa: BLE001 — a wedged server must not hang the caller
            try:
                self.process.kill()
            except Exception:  # noqa: BLE001
                pass
        self.transcript.add("->", "transport", {"stopped": self.command}, note="server stopped")


# --------------------------------------------------------------------------- #
# transport: streamable HTTP                                                    #
# --------------------------------------------------------------------------- #


class HttpTransport:
    """Streamable-HTTP JSON-RPC (stdlib only): one POST per message.

    ``send`` queues, ``receive`` performs the POST and returns the answer —
    which is exactly the request/response shape the client drives. A response
    may be ``application/json`` or a ``text/event-stream`` whose last ``data:``
    line carries the message.
    """

    name = "http"

    def __init__(
        self,
        url: str,
        *,
        headers: dict | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transcript: Transcript | None = None,
    ) -> None:
        self.url = str(url)
        self.headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self.timeout = float(timeout)
        self.transcript = transcript or Transcript()
        self.session_id = ""
        self._pending: dict | None = None

    def start(self) -> None:
        if not self.url:
            raise MCPError("an http MCP server needs a url")
        self.transcript.add("->", "transport", {"url": self.url}, note="endpoint configured")

    def _post(self, message: dict | None, *, wait: bool) -> tuple[int, dict | None, dict]:
        body = (
            json.dumps(message, ensure_ascii=False, default=str).encode("utf-8") if message else b""
        )
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = urllib.request.Request(  # noqa: S310 - the caller's own endpoint
            self.url, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                raw = response.read(MAX_RESPONSE_BYTES)
                result = int(response.status)
                response_headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            raw = exc.read() if hasattr(exc, "read") else b""
            result = int(exc.code)
            response_headers = dict(getattr(exc, "headers", {}) or {})
        except (urllib.error.URLError, OSError) as exc:
            raise MCPError(f"the MCP endpoint did not answer: {exc}") from exc
        session = (
            response_headers.get("Mcp-Session-Id") or response_headers.get("mcp-session-id") or ""
        )
        if session:
            self.session_id = str(session)
        if not wait or result in (202, 204):
            return result, None, response_headers
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            return result, None, response_headers
        content_type = str(
            response_headers.get("Content-Type") or response_headers.get("content-type") or ""
        ).lower()
        if "text/event-stream" in content_type:
            payloads = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
            text = payloads[-1] if payloads else ""
        if not text:
            return result, None, response_headers
        try:
            return result, json.loads(text), response_headers
        except json.JSONDecodeError:
            raise MCPError(f"the MCP endpoint returned a non-JSON body: {text[:200]}") from None

    def send(self, message: dict) -> None:
        if self._pending is not None:
            raise MCPError("a previous http message was never answered")
        self._pending = message
        self.transcript.add("->", _kind_of(message), message, note="queued for POST")

    def notify(self, message: dict) -> None:
        self.transcript.add("->", _kind_of(message), message, note="notification POST")
        status, _answer, _headers = self._post(message, wait=False)
        if status not in (200, 202, 204):
            raise MCPError(f"the notification was rejected with HTTP {status}")
        self.transcript.add("<-", "transport", {"status": status}, note="notification accepted")

    def receive(self, want_id: Any, timeout: float | None = None) -> dict | None:
        message = self._pending
        if message is None:
            raise MCPError("nothing to send over http")
        self._pending = None
        status, answer, headers = self._post(message, wait=True)
        if status >= 400 and answer is None:
            raise MCPError(f"the MCP endpoint answered HTTP {status}")
        if answer is None:
            return None
        if answer.get("id") is None and "result" not in answer and "error" not in answer:
            return None
        self.transcript.add("<-", _kind_of(answer), answer, note=f"HTTP {status}")
        return answer

    def stderr_tail(self, limit: int = 0) -> str:
        return ""

    def close(self) -> None:
        if self.session_id:
            # A polite DELETE is the streamable-HTTP terminator; a server that
            # does not implement it is not an error.
            try:
                request = urllib.request.Request(  # noqa: S310
                    self.url,
                    headers={"Mcp-Session-Id": self.session_id, **self.headers},
                    method="DELETE",
                )
                urllib.request.urlopen(request, timeout=5).close()  # noqa: S310
            except Exception:  # noqa: BLE001
                pass
        self.transcript.add("->", "transport", {"closed": self.url}, note="endpoint closed")


def _kind_of(message: dict) -> str:
    """request | notification | response | error — read off the message."""
    if "method" in message:
        return "notification" if message.get("id") is None else "request"
    if "error" in message:
        return "error"
    return "response"


# --------------------------------------------------------------------------- #
# the client: handshake + tools                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class MCPServerSpec:
    """How to reach ONE MCP server: a stdio command or a streamable-HTTP url."""

    name: str = ""
    transport: str = "stdio"
    command: list[str] = field(default_factory=list)
    url: str = ""
    env: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_strings(
        cls,
        *,
        command: str = "",
        args: list[str] | None = None,
        url: str = "",
        name: str = "",
        env: list[str] | None = None,
        headers: list[str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> MCPServerSpec:
        """Build a spec from CLI-shaped input (a command string is shlex-split)."""
        parts = shlex.split(command) if command else []
        parts += [str(item) for item in (args or [])]
        return cls(
            name=name or (parts[0] if parts else url),
            transport="http" if url else "stdio",
            command=parts,
            url=url,
            env=_pairs(env or []),
            headers=_pairs(headers or []),
            timeout=float(timeout),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "transport": self.transport,
            "command": list(self.command),
            "url": self.url,
            "env_keys": sorted(self.env),
            "header_keys": sorted(self.headers),
            "timeout": self.timeout,
        }

    def transport_for(self, transcript: Transcript):
        if self.transport == "http":
            return HttpTransport(
                self.url, headers=self.headers, timeout=self.timeout, transcript=transcript
            )
        return StdioTransport(
            self.command, env=self.env, timeout=self.timeout, transcript=transcript
        )


def _pairs(items: list[str]) -> dict:
    """``["K=V", ...]`` → a dict; a malformed pair is refused, never dropped."""
    out: dict[str, str] = {}
    for item in items:
        key, sep, value = str(item).partition("=")
        if not sep or not key.strip():
            raise MCPError(f"'{item}' is not KEY=VALUE")
        out[key.strip()] = value
    return out


def _client_version() -> str:
    try:
        from . import __version__ as version  # type: ignore[attr-defined]

        return str(version)
    except Exception:  # noqa: BLE001 — a version string is never worth a crash
        return "0.0.0"


class MCPClient:
    """A minimal, transcript-recording MCP client (initialize → list → call)."""

    def __init__(
        self,
        spec: MCPServerSpec,
        *,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
        transcript: Transcript | None = None,
    ) -> None:
        self.spec = spec
        self.protocol_version = protocol_version
        self.transcript = transcript or Transcript()
        self.transport = spec.transport_for(self.transcript)
        self._next_id = 1
        self.initialized = False
        self.server_info: dict = {}
        self.server_capabilities: dict = {}
        self.negotiated_protocol = ""
        self.protocol_supported = False

    # -- plumbing ---------------------------------------------------------- #

    def _request(self, method: str, params: dict | None, *, timeout: float | None = None) -> dict:
        request_id = self._next_id
        self._next_id += 1
        message: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self.transport.send(message)
        answer = self.transport.receive(request_id, timeout=timeout)
        if answer is None:
            raise MCPError(
                f"no answer to {method}: the endpoint returned an empty body "
                f"(a streamable-HTTP server must return the response message)"
            )
        if "error" in answer:
            error = answer.get("error") or {}
            raise MCPError(
                f"{method} failed: {error.get('code')} {str(error.get('message'))[:300]}"
            )
        result = answer.get("result")
        return result if isinstance(result, dict) else {"value": result}

    def _notify(self, method: str, params: dict | None = None) -> None:
        message: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.transport.notify(message)

    # -- the protocol ------------------------------------------------------ #

    def connect(self) -> None:
        self.transport.start()

    def initialize(self) -> dict:
        """The MCP handshake: ``initialize`` then ``notifications/initialized``."""
        self.connect()
        result = self._request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "skaldir-godsv", "version": _client_version()},
            },
        )
        self.server_info = dict(result.get("serverInfo") or {})
        self.server_capabilities = dict(result.get("capabilities") or {})
        self.negotiated_protocol = str(result.get("protocolVersion") or "")
        self.protocol_supported = self.negotiated_protocol in SUPPORTED_PROTOCOL_VERSIONS
        self._notify("notifications/initialized")
        self.initialized = True
        return result

    def list_tools(self, *, limit_pages: int = 10) -> list[dict]:
        """``tools/list`` with pagination — every tool, every schema."""
        tools: list[dict] = []
        cursor = ""
        sent = ""
        for _ in range(max(1, limit_pages)):
            params = {"cursor": cursor} if cursor else {}
            result = self._request("tools/list", params)
            tools.extend(item for item in (result.get("tools") or []) if isinstance(item, dict))
            cursor = str(result.get("nextCursor") or "")
            if not cursor or cursor == sent:
                break
            sent = cursor
        return tools

    def call_tool(self, name: str, arguments: dict, *, timeout: float | None = None) -> dict:
        result = self._request(
            "tools/call", {"name": name, "arguments": dict(arguments)}, timeout=timeout
        )
        return result

    def close(self) -> None:
        self.transport.close()


def handshake(
    spec: MCPServerSpec,
    *,
    protocol_version: str = DEFAULT_PROTOCOL_VERSION,
    allow_unknown_protocol: bool = False,
    require_tools: bool = False,
) -> dict:
    """Start/connect → initialize → list tools. Returns the raw facts or a refusal.

    ``ok`` is ``True`` only when the handshake completed AND the negotiated
    protocol version is one we speak (unless explicitly allowed) AND, with
    ``require_tools``, the server actually listed tools. Every failure carries
    a ``failure_class`` and a ``reason`` — never a silent empty success.
    """
    started = time.time()
    transcript = Transcript()
    client = MCPClient(spec, protocol_version=protocol_version, transcript=transcript)
    base = {
        "server": spec.to_dict(),
        "protocol_requested": protocol_version,
        "started_at": started,
        "duration_ms": 0,
        "tools": [],
        "tools_count": 0,
        "transcript": [],
        "stderr": "",
    }

    def failure(failure_class: str, reason: str) -> dict:
        payload = dict(base)
        payload.update(
            {
                "ok": False,
                "failure_class": failure_class,
                "reason": reason,
                "transcript": transcript.as_list(),
                "duration_ms": int((time.time() - started) * 1000),
            }
        )
        return payload

    if spec.transport not in ("stdio", "http"):
        return failure(
            "transport_unsupported",
            f"unknown transport '{spec.transport}' — stdio and http are supported",
        )
    if spec.transport == "stdio" and not spec.command:
        return failure("command_required", "a stdio MCP server needs --command (or -- server …)")
    if spec.transport == "http" and not spec.url:
        return failure("url_required", "an http MCP server needs --url")

    try:
        client.initialize()
        tools = client.list_tools()
    except (MCPError, TimeoutError) as exc:
        payload = failure("handshake_failed", str(exc)[:400])
        payload.update(
            {
                "stderr": getattr(client.transport, "stderr_tail", lambda **_: "")() or "",
                "transport": spec.transport,
            }
        )
        return payload
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001 — teardown must not mask the real result
            pass

    payload = dict(base)
    payload.update(
        {
            "ok": True,
            "failure_class": "",
            "reason": "",
            "transport": spec.transport,
            "protocol_version": client.negotiated_protocol
            or str((client.server_info or {}).get("protocolVersion") or ""),
            "protocol_supported": bool(client.protocol_supported),
            "server_info": dict(client.server_info),
            "server_capabilities": dict(client.server_capabilities),
            "session_id": str(getattr(client.transport, "session_id", "") or ""),
            "tools": tools,
            "tools_count": len(tools),
            "transcript": transcript.as_list(),
            "stderr": getattr(client.transport, "stderr_tail", lambda **_: "")() or "",
            "duration_ms": int((time.time() - started) * 1000),
        }
    )
    if not client.protocol_supported and not allow_unknown_protocol:
        payload.update(
            {
                "ok": False,
                "failure_class": "protocol_unsupported",
                "reason": (
                    f"the server negotiated protocol {client.negotiated_protocol!r}, which this "
                    f"client does not speak (known: {', '.join(SUPPORTED_PROTOCOL_VERSIONS)}) — "
                    "refused instead of guessing the wire format"
                ),
            }
        )
    elif require_tools and not tools:
        payload.update(
            {
                "ok": False,
                "failure_class": "no_tools",
                "reason": "the server listed no tools — there is nothing to map",
            }
        )
    return payload


# --------------------------------------------------------------------------- #
# mapping: tool schema → typed capability                                       #
# --------------------------------------------------------------------------- #


def _slug(text: str, *, limit: int = 40) -> str:
    slug = _SLUG.sub("_", str(text or "").strip().lower()).strip("_")
    return (slug or "tool")[:limit]


def _json_kind(value: Any) -> str:
    """The JSON Schema type of one literal (``''`` when it has none)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return ""


def _infer_enum_type(spec: dict) -> str | None:
    """The one type an ``enum``/``const`` allows, or ``None`` when they disagree."""
    held = spec.get("enum")
    values = list(held) if isinstance(held, list) else []
    if "const" in spec:
        values = values + [spec["const"]]
    kinds = {_json_kind(value) for value in values}
    kinds.discard("")
    if not kinds or len(kinds) > 2:
        return None
    if kinds == {"integer", "number"}:
        return "number"
    return sorted(kinds)[0] if len(kinds) == 1 else None


def _ref_target(ref: str, root: Any) -> dict | None:
    """The schema a local ``#/…`` JSON pointer points at, or ``None`` when it is not local."""
    if not isinstance(ref, str) or not any(
        ref.startswith(prefix) for prefix in _REF_PREFIXES
    ):
        return None
    node: Any = root
    for part in ref[1:].split("/"):  # '#/$defs/WindowInfo' -> ['', '$defs', 'WindowInfo']
        if not part:
            continue
        if not isinstance(node, dict):
            return None
        node = node.get(part.replace("~1", "/").replace("~0", "~"))
    return node if isinstance(node, dict) else None


def _merge_schema(base: dict, extra: dict, *, label: str) -> tuple[dict | None, str]:
    """Combine two schemas describing the SAME value (``allOf``/``$ref`` siblings).

    ``(merged, '')`` on success, ``(None, reason)`` when the two disagree about the
    value's type — a disagreement no single typed parameter can carry.
    """
    merged = dict(base)
    for key, value in extra.items():
        if key == "properties":
            if not isinstance(value, dict):
                return None, f"'{label}': 'properties' is not an object"
            current = merged.get("properties")
            if not isinstance(current, dict):
                merged["properties"] = dict(value)
                continue
            combined = dict(current)
            for prop_name, prop_spec in value.items():
                held = combined.get(prop_name)
                if isinstance(held, dict) and isinstance(prop_spec, dict):
                    inner, why = _merge_schema(held, prop_spec, label=f"{label}.{prop_name}")
                    if inner is None:
                        return None, why
                    combined[prop_name] = inner
                else:
                    combined[prop_name] = prop_spec
            merged["properties"] = combined
        elif key == "required":
            held = [str(item) for item in (merged.get("required") or [])]
            added = [str(item) for item in (value or [])]
            merged["required"] = held + [item for item in added if item not in held]
        elif key == "type":
            if "type" in merged and merged["type"] != value:
                return None, (
                    f"'{label}': the combined schema declares conflicting types "
                    f"{merged['type']!r} and {value!r}"
                )
            merged["type"] = value
        elif key in ("$ref", "anyOf", "oneOf", "allOf") and key in merged:
            return None, (
                f"'{label}': '{key}' is declared twice while combining the schema — "
                "which of them applies is not decidable"
            )
        else:
            merged[key] = value
    return merged, ""


def _combine_alternatives(
    label: str, spec: dict, root: Any, seen: frozenset, depth: int
) -> tuple[dict | None, list[str]]:
    """Reduce ``anyOf``/``oneOf`` to the ONE typed SHAPE it really describes.

    A ``{"type": "null"}`` branch makes the value optional/nullable; branches that
    all share one scalar type collapse to that type (their enums union). Branches of
    different types, or several structural alternatives, describe more than one
    typed value — the construct is named in the refusal. The winning branch keeps its
    whole shape, so what it resolves to (a ``$ref`` to a nested object, an array of
    objects) is carried, not flattened to a bare type name.
    """
    key = "anyOf" if "anyOf" in spec else "oneOf"
    branches = spec.get(key)
    nullable = bool(spec.get(_NULLABLE))
    if not isinstance(branches, list) or not branches:
        return None, [f"'{label}': '{key}' is not a non-empty list of schemas"]
    shapes: list[dict] = []
    for index, branch in enumerate(branches):
        if not isinstance(branch, dict):
            return None, [f"'{label}': '{key}[{index}]' is not a schema object"]
        shape, reasons = _typed_shape(
            f"{label}<{key}[{index}]>", branch, root=root, seen=seen, depth=depth + 1
        )
        if shape is None:
            return None, reasons
        if shape["type"] == "null":
            nullable = True
            continue
        shapes.append(shape)
    if not shapes:
        return {"type": "null", "nullable": True, "constraints": {}}, []
    kinds = sorted({shape["type"] for shape in shapes})
    if len(kinds) > 1:
        return None, [
            f"'{label}': '{key}' offers {len(shapes)} alternatives of different types "
            f"{kinds} — one typed parameter cannot hold them"
        ]
    kind = kinds[0]
    sibling = spec.get("type")
    if isinstance(sibling, str) and sibling != kind:
        return None, [
            f"'{label}': '{key}' and its sibling 'type' {sibling!r} disagree about the "
            "value's type"
        ]
    if isinstance(sibling, list) and kind not in sibling:
        return None, [
            f"'{label}': '{key}' and its sibling 'type' {sibling!r} disagree about the "
            "value's type"
        ]
    if len(shapes) > 1 and kind in ("object", "array"):
        return None, [
            f"'{label}': '{key}' offers {len(shapes)} structural alternatives of type "
            f"'{kind}' — which one applies is not decidable from the schema"
        ]
    chosen = {**shapes[0], "nullable": bool(shapes[0].get("nullable")) or nullable}
    for name in ("default", "format"):  # declared on the wrapper, not on a branch
        if name in spec and name not in chosen:
            chosen[name] = spec[name]
    if len(shapes) == 1:
        return chosen, []
    # several scalar alternatives of one type: the type is certain, the branches'
    # enums/consts/refinements are carried rather than dropped
    constraints = dict(chosen.get("constraints") or {})
    alternatives: list[dict] = []
    enums = [shape.get("enum") for shape in shapes]
    if all(isinstance(item, list) for item in enums):
        union: list = []
        for item in enums:
            for value in item:
                if value not in union:
                    union.append(value)
        chosen["enum"] = union
        chosen.pop("const", None)
        return chosen, []
    for shape in shapes:
        for name, value in (shape.get("constraints") or {}).items():
            constraints.setdefault(name, value)
        if isinstance(shape.get("enum"), list):
            alternatives.append({"enum": list(shape["enum"])})
        if "const" in shape:
            alternatives.append({"const": shape["const"]})
        if "format" in shape:
            alternatives.append({"format": shape["format"]})
    if alternatives:
        constraints["alternatives"] = alternatives
        chosen.pop("const", None)
    chosen["constraints"] = constraints
    return chosen, []


def _needs_alternative_reason(label: str, spec: dict) -> list[str]:
    """Why the constructs that describe MORE than one typed value cannot be reduced."""
    reasons: list[str] = []
    for keyword in _UNMAPPABLE_KEYS:
        if keyword in spec:
            reasons.append(f"'{label}': '{keyword}' cannot be reduced to one typed value")
    return reasons


def _typed_shape(
    label: str,
    spec: Any,
    *,
    root: Any = None,
    seen: frozenset = frozenset(),
    depth: int = 0,
) -> tuple[dict | None, list[str]]:
    """The ONE typed shape a schema fragment describes — or why it has none.

    ``shape`` is ``{type, nullable, required, [enum], [const], [format], [items],
    [fields], [additional], constraints}``, resolved through ``$ref``/``$defs``,
    ``allOf``, ``anyOf``/``oneOf`` and union types. The second element is never
    empty on failure: it names the exact property and the construct that blocked it.
    """
    if depth > _MAX_SCHEMA_DEPTH:
        return None, [
            f"'{label}': the schema nests deeper than {_MAX_SCHEMA_DEPTH} levels — "
            "the shape is unbounded"
        ]
    if not isinstance(spec, dict):
        return None, [f"'{label}': the schema is not an object"]
    if root is None:
        root = spec
    if "$ref" in spec:
        ref = spec.get("$ref")
        if not isinstance(ref, str):
            return None, [f"'{label}': '$ref' is not a string"]
        if ref in seen:
            return None, [
                f"'{label}': '$ref' {ref!r} is recursive — its shape has no finite "
                "typed form"
            ]
        target = _ref_target(ref, root)
        if target is None:
            return None, [
                f"'{label}': '$ref' {ref!r} does not resolve inside this tool's schema "
                "(only '#/$defs/…' and '#/definitions/…' can be typed)"
            ]
        siblings = {name: value for name, value in spec.items() if name != "$ref"}
        merged, why = _merge_schema(dict(target), siblings, label=label)
        if merged is None:
            return None, [why]
        return _typed_shape(label, merged, root=root, seen=seen | {ref}, depth=depth + 1)
    if "allOf" in spec:
        branches = spec.get("allOf")
        if not isinstance(branches, list) or not branches:
            return None, [f"'{label}': 'allOf' is not a non-empty list of schemas"]
        merged = {name: value for name, value in spec.items() if name != "allOf"}
        for index, branch in enumerate(branches):
            if not isinstance(branch, dict):
                return None, [f"'{label}': 'allOf[{index}]' is not a schema object"]
            combined, why = _merge_schema(merged, branch, label=label)
            if combined is None:
                return None, [why]
            merged = combined
        return _typed_shape(label, merged, root=root, seen=seen, depth=depth + 1)
    if "anyOf" in spec or "oneOf" in spec:
        return _combine_alternatives(label, spec, root, seen, depth)
    reasons = _needs_alternative_reason(label, spec)
    if reasons:
        return None, reasons
    declared = spec.get("type")
    nullable = bool(spec.get(_NULLABLE))
    if isinstance(declared, list):
        non_null = [str(item) for item in declared if item != "null"]
        if "null" in declared:
            nullable = True
        if not non_null:
            declared = "null"
        elif len(non_null) == 1:
            declared = non_null[0]
        else:
            return None, [
                f"'{label}': the union type {declared!r} is not mappable to one typed value"
            ]
    if declared is None:
        if "properties" in spec or "additionalProperties" in spec:
            declared = "object"
        elif "items" in spec or "prefixItems" in spec:
            declared = "array"
        elif "enum" in spec or "const" in spec:
            declared = _infer_enum_type(spec)
            if declared is None:
                return None, [
                    f"'{label}': 'enum'/'const' values of different types cannot be "
                    "reduced to one typed value"
                ]
        else:
            return None, [
                f"'{label}': no declared type (an untyped value cannot be validated)"
            ]
    declared = str(declared)
    if declared not in _TYPABLE:
        return None, [f"'{label}': unknown type {declared!r}"]
    shape: dict = {"type": declared, "nullable": nullable, "constraints": {}}
    if isinstance(spec.get("enum"), list):
        shape["enum"] = list(spec["enum"])
    elif "enum" in spec:
        shape["enum"] = [spec["enum"]]
    if "const" in spec:
        shape["const"] = spec["const"]
    if "default" in spec:
        shape["default"] = spec["default"]
    if isinstance(spec.get("format"), str):
        shape["format"] = spec["format"]
    shape["constraints"] = {
        keyword: spec[keyword] for keyword in _CONSTRAINT_KEYS if keyword in spec
    }
    if declared == "object":
        properties = spec.get("properties")
        additional = spec.get("additionalProperties")
        required = [str(item) for item in (spec.get("required") or [])]
        if isinstance(properties, dict) and properties:
            shape["fields"] = {}
            for prop_name, prop_spec in sorted(properties.items()):
                child, reasons = _typed_shape(
                    f"{label}.{prop_name}", prop_spec, root=root, seen=seen, depth=depth + 1
                )
                if child is None:
                    return None, reasons
                shape["fields"][prop_name] = child
            shape["required"] = [name for name in required if name in shape["fields"]]
        elif additional is False:
            # An object that may carry no keys at all: a real, finite shape.
            shape["fields"] = {}
            shape["required"] = []
        elif isinstance(additional, dict) and additional:
            # A MAP: the keys are open, but every VALUE carries a declared type, so the
            # shape is finite and checkable — `{"type": "object", "additionalProperties":
            # {"type": "string"}}` is a string map, not a free-form bag. A bare
            # `{"type": "object"}` and `additionalProperties: true` stay refused below:
            # neither declares anything about the values.
            values, reasons = _typed_shape(
                f"{label}[<key>]", additional, root=root, seen=seen, depth=depth + 1
            )
            if values is None:
                return None, reasons
            shape["fields"] = {}
            shape["required"] = []
            shape["values"] = values
        else:
            return None, [
                f"'{label}': a free-form object carries no typed properties "
                "(no 'properties' are declared and 'additionalProperties' is neither "
                "a typed value schema nor false) — its shape cannot be validated"
            ]
        if isinstance(additional, bool):
            shape["additional"] = additional
    elif declared == "array":
        items = spec.get("items")
        if isinstance(items, list) or "prefixItems" in spec:
            return None, [
                f"'{label}': 'prefixItems' is a positional item list — its element "
                "types depend on position, which is not one typed array"
            ]
        if not isinstance(items, dict):
            return None, [f"'{label}': an array without an item schema is not typed"]
        child, reasons = _typed_shape(
            f"{label}[]", items, root=root, seen=seen, depth=depth + 1
        )
        if child is None:
            return None, reasons
        shape["items"] = child
    return shape, []


def _typed_entry(name: str, shape: dict, *, required: bool, path: str) -> dict:
    """One parameter entry: type, required/optional, defaults, and its own sub-shape.

    Scalar parameters keep the original three-key entry (``type``/``required``/
    ``path``) so every existing reader and every staged adapter keeps working; the
    richer keys appear only where the schema actually declares them.
    """
    entry: dict = {"type": shape["type"], "required": bool(required), "path": path}
    if shape.get("nullable"):
        entry["nullable"] = True
    if shape.get("enum"):
        entry["enum"] = list(shape["enum"])
    if "const" in shape:
        entry["const"] = shape["const"]
    if "default" in shape:
        entry["default"] = shape["default"]
    if shape.get("format"):
        entry["format"] = shape["format"]
    if shape.get("constraints"):
        entry["constraints"] = dict(shape["constraints"])
    if isinstance(shape.get("items"), dict):
        element = _typed_entry(name + "[]", shape["items"], required=False, path=path + "[]")
        element.pop("required", None)
        entry["items"] = element
    if isinstance(shape.get("values"), dict):
        # a map: one typed entry for the VALUES, spelled `name[<key>]` (the keys are open)
        entry["values"] = _typed_entry(
            f"{name}[<key>]", shape["values"], required=False, path=f"{path}[<key>]"
        )
        entry["values"].pop("required", None)
    if isinstance(shape.get("fields"), dict):
        required_fields = {str(item) for item in (shape.get("required") or [])}
        entry["fields"] = {
            field: _typed_entry(field, child, required=field in required_fields,
                                path=f"{path}.{field}")
            for field, child in shape["fields"].items()
        }
    if "additional" in shape:
        entry["additional"] = bool(shape["additional"])
    return entry


def _leaves(name: str, entry: dict, out: list[dict]) -> list[dict]:
    """Every leaf typed path under one parameter (nested objects and arrays included).

    An array contributes one ``name[]`` path per element, so a leaf under an array is
    spelled ``rows[].payload`` — the type belongs to each element, not to the list.
    """
    if isinstance(entry.get("items"), dict):
        _leaves(f"{name}[]", entry["items"], out)
        return out
    if isinstance(entry.get("values"), dict):
        _leaves(f"{name}[<key>]", entry["values"], out)
        return out
    if isinstance(entry.get("fields"), dict):
        for field, child in entry["fields"].items():
            _leaves(f"{name}.{field}", child, out)
        return out
    leaf = {
        "path": name,
        "type": str(entry.get("type") or ""),
        "required": bool(entry.get("required")),
    }
    if entry.get("nullable"):
        leaf["nullable"] = True
    if "default" in entry:
        leaf["default"] = entry["default"]
    if entry.get("enum"):
        leaf["enum"] = list(entry["enum"])
    out.append(leaf)
    return out


def validate_tool_schema(tool: dict) -> tuple[bool, str]:
    """``(usable, reason)`` for one ``tools/list`` entry.

    ``usable`` is ``False`` (never a shrug) when the tool has no name, no
    ``inputSchema``, an input schema that is not an object schema, a ``required``
    name that is not declared, a construct that describes more than one typed value
    (``not``/``if``/…, a union type, several alternatives of different types), an
    untyped property or a free-form object. The reason names the exact property and
    construct. ``$ref``/``$defs``, ``allOf``, ``anyOf``/``oneOf`` and ``["T","null"]``
    unions are RESOLVED first — they are only a refusal when they do not reduce.
    """
    if not isinstance(tool, dict):
        return False, "the tool entry is not an object"
    name = str(tool.get("name") or "")
    if not name:
        return False, "the tool has no name"
    schema = tool.get("inputSchema")
    if schema is None:
        return False, "no inputSchema — the tool's inputs cannot be validated"
    if not isinstance(schema, dict):
        return False, "inputSchema is not an object"
    reasons = _needs_alternative_reason("input", schema)
    if reasons:
        return False, "; ".join(reasons[:4])
    declared = schema.get("type")
    if isinstance(declared, list):
        declared = [item for item in declared if item != "null"] or ["null"]
        declared = declared[0] if len(declared) == 1 else declared
    if declared is not None and declared != "object":
        return False, f"the input schema is not an object schema (type={declared!r})"
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return False, "'properties' is not an object"
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        return False, "'required' is not a list of property names"
    missing = [item for item in required if item not in properties]
    if missing:
        return False, f"required input(s) {missing} are not declared in properties"
    reasons = []
    for prop_name, prop_spec in sorted(properties.items()):
        _shape, why = _typed_shape(prop_name, prop_spec, root=schema)
        reasons.extend(why)
    if reasons:
        return False, "; ".join(reasons[:4])
    out_schema = tool.get("outputSchema")
    if out_schema is not None:
        if not isinstance(out_schema, dict):
            return False, "outputSchema is not an object"
        for prop_name, prop_spec in sorted((out_schema.get("properties") or {}).items()):
            _shape, why = _typed_shape(f"output.{prop_name}", prop_spec, root=out_schema)
            reasons.extend(why)
        if reasons:
            return False, "; ".join(reasons[:4])
    return True, ""


def map_tool_to_capability(
    tool: dict,
    *,
    server: dict,
    capability: str = "",
    name: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """The TYPED capability definition for one MCP tool — or :class:`MCPRefusal`.

    The adapter carries the same shape the container half uses
    (``operation.inputs`` / ``operation.outputs`` keyed by name with a ``type``
    and ``required``), so every existing reader of an adapter keeps working.
    """
    tool_name = str(tool.get("name") or "")
    usable, reason = validate_tool_schema(tool)
    if not usable:
        raise MCPRefusal(f"tool '{tool_name}' was refused: {reason}")

    schema = dict(tool.get("inputSchema") or {})
    properties = dict(schema.get("properties") or {})
    required = {str(item) for item in (schema.get("required") or [])}
    inputs: dict[str, dict] = {}
    for prop_name, prop_spec in sorted(properties.items()):
        shape, reasons = _typed_shape(prop_name, prop_spec, root=schema)
        if shape is None:
            raise MCPRefusal(
                f"tool '{tool_name}' was refused: {'; '.join(reasons[:4])}"
            )
        entry = _typed_entry(
            prop_name, shape, required=prop_name in required, path=prop_name
        )
        if prop_spec.get("description"):
            entry["description"] = str(prop_spec["description"])[:200]
        inputs[prop_name] = entry
    typed_paths: list[dict] = []
    for prop_name in inputs:
        _leaves(prop_name, inputs[prop_name], typed_paths)

    out_schema = tool.get("outputSchema")
    structured = (
        isinstance(out_schema, dict)
        and isinstance(out_schema.get("properties"), dict)
        and bool(out_schema.get("properties"))
    )
    output_paths: list[dict] = []
    if structured:
        out_props = dict((out_schema or {}).get("properties") or {})
        out_required = {str(item) for item in ((out_schema or {}).get("required") or [])}
        outputs: dict[str, dict] = {}
        for out_name, out_spec in sorted(out_props.items()):
            shape, reasons = _typed_shape(
                f"output.{out_name}", out_spec, root=out_schema
            )
            if shape is None:
                raise MCPRefusal(
                    f"tool '{tool_name}' was refused: {'; '.join(reasons[:4])}"
                )
            entry = _typed_entry(
                out_name, shape, required=out_name in out_required, path=out_name
            )
            entry.pop("required", None)
            entry["from"] = "structuredContent"
            outputs[out_name] = entry
        output_mode = "structuredContent"
        for out_name in outputs:
            _leaves(out_name, outputs[out_name], output_paths)
    else:
        outputs = {"result": {"type": "string", "from": "content", "path": "text"}}
        output_mode = "content-text"

    return {
        "schema_version": 1,
        "name": name or f"mcp_{_slug(tool_name)}",
        "capability": (capability or str(tool.get("description") or tool_name))[:200],
        "kind": MCP_KIND,
        "tool": tool_name,
        "title": str(tool.get("title") or tool_name)[:120],
        "description": str(tool.get("description") or "")[:600],
        "server": dict(server),
        "operation": {
            "id": tool_name,
            "inputs": inputs,
            "outputs": outputs,
            "typed_paths": typed_paths,
            "typed_output_paths": output_paths,
            "input_schema": schema,
            "output_schema": out_schema if isinstance(out_schema, dict) else None,
            "output_mode": output_mode,
        },
        "permissions": dict(MCP_PERMISSIONS),
        "timeout_seconds": float(timeout),
        "created_at": time.time(),
        "created_by": "godsv-mcp-adapter",
    }


def map_surface(
    report: dict,
    *,
    capability: str = "",
    tools: list[str] | None = None,
    name: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Map a handshake report: ``{ok, mapped, refused, tools_count}``.

    ``refused`` is a first-class part of the answer — every tool that could not
    be typed appears there with its reason. Nothing is dropped silently and
    nothing is registered.
    """
    if not report.get("ok"):
        return {
            "ok": False,
            "failure_class": str(report.get("failure_class") or "no_handshake"),
            "reason": str(report.get("reason") or "no completed handshake to map"),
            "mapped": [],
            "refused": [],
            "tools_count": 0,
        }
    server = {
        "name": str((report.get("server") or {}).get("name") or ""),
        "transport": str(report.get("transport") or ""),
        "command": list((report.get("server") or {}).get("command") or []),
        "url": str((report.get("server") or {}).get("url") or ""),
        "env": dict((report.get("server") or {}).get("env") or {}),
        "headers": dict((report.get("server") or {}).get("headers") or {}),
        "protocol_version": str(report.get("protocol_version") or ""),
        "server_info": dict(report.get("server_info") or {}),
        "tools_listed": int(report.get("tools_count") or 0),
    }
    wanted = [str(item) for item in (tools or []) if str(item).strip()]
    mapped: list[dict] = []
    refused: list[dict] = []
    for tool in report.get("tools") or []:
        tool_name = str(tool.get("name") or "")
        if wanted and tool_name not in wanted:
            continue
        try:
            adapter = map_tool_to_capability(
                tool,
                server=server,
                capability=capability,
                name=name if len(wanted) == 1 and name else "",
                timeout=timeout,
            )
        except MCPRefusal as exc:
            refused.append({"tool": tool_name, "reason": str(exc)})
            continue
        mapped.append(adapter)
    if wanted:
        found = {item["tool"] for item in mapped} | {item["tool"] for item in refused}
        for missing in sorted(set(wanted) - found):
            refused.append({"tool": missing, "reason": "the server did not list this tool"})
    if mapped:
        ok, failure_class, reason = True, "", ""
    elif not int(report.get("tools_count") or 0):
        ok, failure_class = False, "no_tools"
        reason = "the server listed no tools — there is nothing to map onto a capability"
    else:
        ok, failure_class = False, "nothing_mappable"
        reason = "no listed tool mapped onto a typed capability"
    return {
        "ok": ok,
        "failure_class": failure_class,
        "reason": reason,
        "mapped": mapped,
        "refused": refused,
        "tools_count": int(report.get("tools_count") or 0),
    }


# --------------------------------------------------------------------------- #
# calling: a real tools/call through the adapter                                #
# --------------------------------------------------------------------------- #


def _type_ok(value: Any, declared: str, nullable: bool = False) -> tuple[bool, str]:
    """Is ``value`` of the declared JSON type? (bool is not an integer here.)

    ``nullable`` is the ``["T", "null"]``/``anyOf[T, null]`` case: ``null`` is then
    a legal value of a parameter whose declared type stays ``T``.
    """
    if declared == "":
        return True, ""
    if value is None:
        if declared == "null" or nullable:
            return True, ""
        return False, "is null but the declared type is not nullable"
    if declared == "null":
        return False, "is not null"
    if declared == "boolean":
        return (isinstance(value, bool), "is not a boolean")
    if declared == "integer":
        return (isinstance(value, int) and not isinstance(value, bool), "is not an integer")
    if declared == "number":
        return (
            isinstance(value, (int, float)) and not isinstance(value, bool),
            "is not a number",
        )
    if declared == "string":
        return (isinstance(value, str), "is not a string")
    if declared == "array":
        return (isinstance(value, list), "is not an array")
    if declared == "object":
        return (isinstance(value, dict), "is not an object")
    return False, f"declares the unknown type {declared!r}"


def _nested_mismatch(label: str, value: Any, spec: dict) -> str:
    """The first nested type error under ``label``, or ``''`` when there is none.

    Nested values are checked only when they are PRESENT: a declared sub-field a
    server omits is not an error, a declared sub-field it sends with the wrong type
    is. Extra keys are accepted unless the schema declares ``additionalProperties``
    false (a policy the mapper carries, so it can be enforced rather than ignored).
    """
    ok, why = _type_ok(value, str(spec.get("type") or ""), bool(spec.get("nullable")))
    if not ok:
        return f"{label} {why} ({value!r})"
    allowed = spec.get("enum")
    if allowed and value not in allowed:
        return f"{label} is not one of {allowed}: {value!r}"
    if "const" in spec and value != spec["const"]:
        return f"{label} is not the declared constant {spec['const']!r}: {value!r}"
    items = spec.get("items")
    if isinstance(items, dict) and isinstance(value, list):
        for index, element in enumerate(value):
            problem = _nested_mismatch(f"{label}[{index}]", element, items)
            if problem:
                return problem
    fields = spec.get("fields")
    if isinstance(fields, dict) and isinstance(value, dict):
        unknown = sorted(set(value) - set(fields))
        if unknown and spec.get("additional") is False:
            return (
                f"{label} carries undeclared key(s) {unknown} "
                "(the schema declares additionalProperties: false)"
            )
        for field, child in fields.items():
            if field in value:
                problem = _nested_mismatch(f"{label}.{field}", value[field], child)
                if problem:
                    return problem
    values_spec = spec.get("values")
    if isinstance(values_spec, dict) and isinstance(value, dict):
        # a map's keys are open, so every value is checked against the declared type
        for key, item in value.items():
            problem = _nested_mismatch(f"{label}.{key}", item, values_spec)
            if problem:
                return problem
    return ""


def validate_payload(adapter: dict, payload: dict) -> dict:
    """Check a call payload against the declared typed inputs (fail-closed)."""
    inputs = dict((adapter.get("operation") or {}).get("inputs") or {})
    unknown = sorted(set(payload) - set(inputs))
    if unknown:
        raise MCPRefusal(
            "unknown input(s) for this adapter: "
            + ", ".join(unknown)
            + " (declared: "
            + ", ".join(sorted(inputs))
            + ")"
        )
    resolved: dict = {}
    for param, spec in inputs.items():
        if param in payload:
            value = payload[param]
        elif spec.get("required"):
            raise MCPRefusal(f"missing required input: {param}")
        elif "default" in spec and spec["default"] is not None:
            value = spec["default"]
        else:
            # optional and nothing concrete to send: a declared ``default: null``
            # (what read-only desktop tools declare on nearly every optional
            # argument) means "absent", so the argument is left out and the server
            # applies its own default instead of being handed an explicit null.
            continue
        # one pass: the declared type, its enum/const, and every nested value
        problem = _nested_mismatch(f"input '{param}'", value, spec)
        if problem:
            raise MCPRefusal(problem)
        resolved[param] = value
    return resolved


def read_tool_result(adapter: dict, result: dict) -> tuple[bool, dict, str]:
    """Map a ``tools/call`` result onto the declared outputs, type-checked."""
    outputs = dict((adapter.get("operation") or {}).get("outputs") or {})
    if result.get("isError"):
        text = " ".join(
            str(part.get("text") or "")
            for part in (result.get("content") or [])
            if isinstance(part, dict)
        )
        return False, {}, f"the tool reported an error: {text[:200] or 'no detail'}"
    structured = result.get("structuredContent")
    parsed: dict = {}
    for out_name, spec in outputs.items():
        source = str(spec.get("from") or "content")
        if source == "structuredContent":
            if not isinstance(structured, dict):
                return (
                    False,
                    {},
                    (
                        f"the declared output '{out_name}' comes from structuredContent, "
                        "but the server returned none"
                    ),
                )
            if out_name not in structured:
                return (
                    False,
                    {},
                    f"structuredContent carries no '{out_name}' "
                    f"(keys: {', '.join(sorted(structured)) or 'none'})",
                )
            value = structured[out_name]
        else:
            value = _content_text(result)
        problem = _nested_mismatch(f"output '{out_name}'", value, spec)
        if problem:
            return False, {}, problem
        parsed[out_name] = value
    return True, parsed, ""


def _content_text(result: dict) -> str:
    """The first text part of a ``tools/call`` result ('' when there is none)."""
    for part in result.get("content") or []:
        if isinstance(part, dict) and part.get("type") == "text":
            return str(part.get("text") or "")
    return ""


def call_mapped_tool(
    adapter: dict,
    payload: dict,
    *,
    timeout: float | None = None,
) -> dict:
    """Call ONE mapped tool for real: start/connect → initialize → tools/call.

    Returns the typed outputs, the raw answer, the transcript of THIS call and a
    structured failure when anything went wrong. Never raises — not for a
    server-side failure and not for a payload that violates the declared typed
    inputs (a caller must never get an empty stdout instead of a reason).
    """
    started = time.time()
    try:
        resolved = validate_payload(adapter, dict(payload))
    except MCPRefusal as exc:
        return {
            "ok": False,
            "failure_class": "invalid_input",
            "reason": str(exc)[:400],
            "name": adapter.get("name", ""),
            "tool": adapter.get("tool", ""),
            "input": dict(payload),
            "output": {},
            "raw": {},
            "transcript": [],
            "duration_ms": 0,
        }
    server = dict(adapter.get("server") or {})
    command = [str(part) for part in (server.get("command") or [])]
    spec = MCPServerSpec(
        name=str(server.get("name") or ""),
        transport=str(server.get("transport") or "stdio"),
        command=command,
        url=str(server.get("url") or ""),
        env={str(k): str(v) for k, v in (server.get("env") or {}).items()},
        headers={str(k): str(v) for k, v in (server.get("headers") or {}).items()},
        timeout=float(timeout or adapter.get("timeout_seconds") or DEFAULT_TIMEOUT),
    )
    transcript = Transcript()
    client = MCPClient(spec, transcript=transcript)
    try:
        client.initialize()
        answer = client.call_tool(str(adapter.get("tool") or ""), resolved, timeout=spec.timeout)
    except (MCPError, TimeoutError) as exc:
        return {
            "ok": False,
            "failure_class": "call_failed",
            "reason": str(exc)[:400],
            "name": adapter.get("name", ""),
            "tool": adapter.get("tool", ""),
            "input": resolved,
            "output": {},
            "transcript": transcript.as_list(),
            "stderr": getattr(client.transport, "stderr_tail", lambda **_: "")() or "",
            "duration_ms": int((time.time() - started) * 1000),
        }
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    ok, parsed, detail = read_tool_result(adapter, answer)
    return {
        "ok": bool(ok),
        "failure_class": "" if ok else "output_mismatch",
        "reason": detail,
        "name": adapter.get("name", ""),
        "tool": adapter.get("tool", ""),
        "transport": spec.transport,
        "input": resolved,
        "output": parsed,
        "raw": answer,
        "transcript": transcript.as_list(),
        "stderr": getattr(client.transport, "stderr_tail", lambda **_: "")() or "",
        "duration_ms": int((time.time() - started) * 1000),
    }


def compare_expectation(outputs: dict, expect: dict) -> tuple[bool, str]:
    """``(matches, reason)`` for a caller-declared expectation (no oracle claimed)."""
    if not expect:
        return True, ""
    differences = [
        f"{key}: expected {expect[key]!r}, observed {outputs.get(key)!r}"
        for key in sorted(expect)
        if outputs.get(key) != expect[key]
    ]
    if differences:
        return False, "; ".join(differences)[:400]
    return True, ""


# --------------------------------------------------------------------------- #
# staging on disk                                                               #
# --------------------------------------------------------------------------- #


def _module_name_for(name: str) -> str:
    """The generated module's name — never ``mcp_mcp_add_numbers``."""
    base = str(name or "tool")
    return (base if base.startswith("mcp_") else f"mcp_{base}")[:60]


def mcp_tool_paths(runtime: str | Path, name: str) -> dict:
    """Every path of one mapped MCP capability (one place, so nothing guesses)."""
    tool_dir = Path(runtime) / MCP_TOOL_DIR / str(name)
    return {
        "tool_dir": tool_dir,
        "adapter": tool_dir / MCP_ADAPTER_FILE,
        "surface": tool_dir / MCP_SURFACE_FILE,
        "manifest": tool_dir / "manifest.yaml",
        "module": tool_dir / f"mcp_{name}.py",
        "wrapper": tool_dir / "bin" / str(name),
        "tests": tool_dir / "tests" / f"test_{name}.py",
        "evidence": tool_dir / MCP_EVIDENCE_FILE,
        "rollbacks": Path(runtime) / "mcp_rollbacks" / str(name),
    }


_MCP_MODULE = '''"""MCP adapter '__TOOL__' — generated by the godsv MCP surface adapter.

Thin binding, on purpose: the client that was TESTED is
``skaldir.adapter_mcp`` — a copied client would drift from the tested one.
Reads adapter.json next to this file and calls the mapped tool for real.

The package the call was tested against is recorded below: this file is executed by
whatever ``python3`` the caller's PATH resolves (the wrapper's shebang), which is not
necessarily the interpreter that generated it — so the tested client has to be
reachable from here, and it is, by path.
"""
from __future__ import annotations

import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
ADAPTER_PATH = _HERE / "adapter.json"

#: Where the tested ``skaldir`` package lived when this file was generated.
_PACKAGE_ROOT = __PKG__
if _PACKAGE_ROOT and _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)


def adapter() -> dict:
    return json.loads(ADAPTER_PATH.read_text(encoding="utf-8"))


def call(payload: dict) -> dict:
    """Call the mapped tool and return its declared outputs (raises on failure)."""
    from skaldir.adapter_mcp import call_staged_adapter

    return call_staged_adapter(ADAPTER_PATH, dict(payload))


def main(**payload) -> dict:
    return call(payload)


if __name__ == "__main__":  # pragma: no cover
    _payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    _result = call(_payload)
    print(json.dumps(_result, ensure_ascii=False, default=str, sort_keys=True))
'''


_MCP_WRAPPER = '''#!/usr/bin/env python3
"""CLI wrapper for the generated MCP capability '__TOOL__'."""
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import __MODULE__ as _tool


def _record(ok, error='', started=None):
    try:
        log = _ROOT.parent.parent / 'tool_usage.jsonl'
        entry = {'ts': time.time(), 'name': __NAME__, 'ok': bool(ok)}
        if started is not None:
            entry['duration_ms'] = int((time.monotonic() - started) * 1000)
        if error:
            entry['error'] = str(error)[:300]
        with log.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + '\\n')
    except Exception:
        pass


if __name__ == '__main__':
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    _started = time.monotonic()
    try:
        out = _tool.call(payload)
    except Exception as exc:
        _record(False, f'{type(exc).__name__}: {exc}', _started)
        raise
    _record(True, '', _started)
    print(json.dumps(out, ensure_ascii=False, default=str, sort_keys=True))
'''


def call_staged_adapter(adapter_path: str | Path, payload: dict) -> dict:
    """The one call path used by the generated module AND by ``mcp call``.

    Reads the adapter from disk, calls for real, returns the declared outputs.
    A failed call raises — the declared outputs are the contract, and a silent
    ``{}`` would look like success to every caller.
    """
    path = Path(adapter_path)
    adapter = json.loads(path.read_text(encoding="utf-8"))
    result = call_mapped_tool(adapter, dict(payload))
    if not result.get("ok"):
        raise MCPError(
            f"{result.get('failure_class') or 'call_failed'}: "
            f"{result.get('reason') or 'the call did not return the declared outputs'}"
        )
    return dict(result.get("output") or {})


def stage_mcp_tool(
    adapter: dict,
    *,
    runtime: str | Path,
    report: dict | None = None,
) -> dict:
    """Write the staged capability: adapter, module, wrapper, manifest, surface.

    No evidence is written here — a capability has evidence only after a REAL
    call (``test_mapped_tool``), and ``register_mapped_tool`` refuses without it.
    """
    name = str(adapter.get("name") or "")
    if not name:
        return {"ok": False, "failure_class": "name_required", "reason": "the adapter has no name"}
    paths = mcp_tool_paths(runtime, name)
    module_name = _module_name_for(name)
    try:
        _write_text(paths["adapter"], json.dumps(adapter, indent=2, sort_keys=True) + "\n")
        if report is not None:
            _write_text(
                paths["surface"],
                json.dumps(report, indent=2, sort_keys=True, default=str),
            )
        _write_text(
            paths["tool_dir"] / f"{module_name}.py",
            _MCP_MODULE.replace("__TOOL__", name).replace(
                "__PKG__", repr(str(Path(__file__).resolve().parents[1]))
            ),
        )
        paths["module"] = paths["tool_dir"] / f"{module_name}.py"
        _write_text(
            paths["wrapper"],
            _MCP_WRAPPER.replace("__TOOL__", name)
            .replace("__MODULE__", module_name)
            .replace("__NAME__", repr(name)),
            executable=True,
        )
        manifest = mcp_manifest(adapter, name=name, status="built", module_name=module_name)
        _write_text(paths["manifest"], yaml.safe_dump(manifest, sort_keys=False))
    except OSError as exc:
        return {
            "ok": False,
            "failure_class": "stage_failed",
            "reason": f"the staged capability could not be written: {exc}",
        }
    return {
        "ok": True,
        "name": name,
        "tool": adapter.get("tool", ""),
        "tool_dir": str(paths["tool_dir"]),
        "adapter_path": str(paths["adapter"]),
        "module": str(paths["module"]),
        "wrapper": str(paths["wrapper"]),
        "manifest": str(paths["manifest"]),
        "adapter_digest": _sha256_file(paths["adapter"]),
    }


def mcp_manifest(
    adapter: dict,
    *,
    name: str,
    status: str,
    module_name: str,
    version: str = "0.1.0",
    project_id: str = "",
) -> dict:
    """The tool manifest of a mapped MCP capability (the registry's own shape)."""
    perms = dict(adapter.get("permissions") or MCP_PERMISSIONS)
    server = dict(adapter.get("server") or {})
    return {
        "name": name,
        "version": version,
        "scope": "project",
        "project_id": project_id,
        "type": MCP_MANIFEST_TYPE,
        "purpose": adapter.get("capability", ""),
        "created_for": adapter.get("capability", ""),
        "created_by": "godsv-mcp-adapter",
        "commands_or_tools": [adapter.get("tool", "")],
        "network": {
            "allowed_domains": list((perms.get("network") or {}).get("allowed_domains") or [])
        },
        "filesystem": {
            "access": (perms.get("filesystem") or {}).get("access", "none"),
            "mounts": [],
        },
        "required_secrets": list(perms.get("secrets") or []),
        "dependencies": [],
        "tests": [sys.executable, "-m", "pytest", "-q", "tests/"],
        "risk_level": "medium",
        "status": status,
        "created_at": time.time(),
        "last_tested_at": None,
        "runtime": MCP_KIND,
        "transport": str(server.get("transport") or ""),
        "server_command": list(server.get("command") or []),
        "server_url": str(server.get("url") or ""),
        "protocol_version": str(server.get("protocol_version") or ""),
        "tool": str(adapter.get("tool") or ""),
        "inputs": list((adapter.get("operation") or {}).get("inputs") or {}),
        "outputs": list((adapter.get("operation") or {}).get("outputs") or {}),
        "permissions": perms,
        "adapter": {"file": MCP_ADAPTER_FILE, "module": module_name},
        "validation": {"status": "not_declared"},
    }


def load_mapped_tool(runtime: str | Path, name: str) -> dict:
    """The staged adapter + its evidence, or a structured "not found"."""
    paths = mcp_tool_paths(runtime, name)
    if not paths["adapter"].is_file():
        return {
            "ok": False,
            "failure_class": "not_found",
            "reason": f"no staged MCP adapter for '{name}'",
            "paths": {},
        }
    try:
        adapter = json.loads(paths["adapter"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "failure_class": "adapter_unreadable",
            "reason": f"adapter.json is not readable: {exc}",
            "paths": {},
        }
    evidence: dict = {}
    if paths["evidence"].is_file():
        try:
            evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            evidence = {}
    return {
        "ok": True,
        "adapter": adapter,
        "evidence": evidence,
        "paths": {key: str(value) for key, value in paths.items()},
    }


# --------------------------------------------------------------------------- #
# test → evidence                                                               #
# --------------------------------------------------------------------------- #


def test_mapped_tool(
    runtime: str | Path,
    name: str,
    *,
    payload: dict,
    expect: dict | None = None,
    timeout: float | None = None,
) -> dict:
    """CALL the staged tool for real and record the evidence behind the claim.

    The evidence binds to the adapter BYTES (``adapter_digest``): an adapter
    edited after its test can never be registered on stale evidence.
    """
    loaded = load_mapped_tool(runtime, name)
    if not loaded["ok"]:
        return loaded
    adapter = loaded["adapter"]
    call = call_mapped_tool(adapter, dict(payload), timeout=timeout)
    expected = dict(expect or {})
    matches, difference = compare_expectation(dict(call.get("output") or {}), expected)
    call_ok = bool(call.get("ok"))
    ok = call_ok and matches
    reason = ""
    if not ok:
        reason = (
            str(call.get("reason") or "")
            or difference
            or ("the call did not return the declared outputs")
        )
    evidence = {
        "ok": bool(ok),
        "failure_class": "" if ok else str(call.get("failure_class") or "expectation_mismatch"),
        "reason": reason,
        "name": name,
        "tool": adapter.get("tool", ""),
        "transport": adapter.get("server", {}).get("transport", ""),
        "input": dict(call.get("input") or {}),
        "expected": expected,
        "observed": dict(call.get("output") or {}),
        "match": bool(matches),
        "difference": difference,
        "oracle": "none — a real MCP call, typed against the declared outputs and "
        "compared with the caller's expectation",
        "seen_at": dict(call.get("raw") or {}),
        "call_transcript": list(call.get("transcript") or []),
        "duration_ms": int(call.get("duration_ms") or 0),
        "adapter_digest": _sha256_file(Path(loaded["paths"]["adapter"])),
        "adapter_file": loaded["paths"]["adapter"],
        "tested_at": time.time(),
        "registered": False,
    }
    paths = mcp_tool_paths(runtime, name)
    try:
        _write_text(paths["evidence"], json.dumps(evidence, indent=2, sort_keys=True))
    except OSError as exc:
        evidence["reason"] = f"the evidence could not be recorded: {exc}"
    evidence["evidence_path"] = str(paths["evidence"])
    return evidence


# --------------------------------------------------------------------------- #
# register → the EXISTING capability registration path                          #
# --------------------------------------------------------------------------- #


def register_mapped_tool(
    runtime: str | Path,
    name: str,
    store,
    *,
    version: str = "",
    project_id: str = "",
    requested_by: str = "cli",
) -> dict:
    """Register a TESTED MCP adapter live — evidence, or nothing.

    Reuses the existing path unchanged: ``ToolSpec`` + ``ToolStore.publish``
    (registry row and publication outbox in one transaction), the
    ``capability_health`` ledger and a rollback snapshot. There is no second
    registry here.
    """
    from . import capability_health as health
    from .tools import KIND_PYTHON, STATUS_APPROVED, ToolSpec

    loaded = load_mapped_tool(runtime, name)
    if not loaded["ok"]:
        return loaded
    adapter = loaded["adapter"]
    evidence = loaded["evidence"]
    paths = mcp_tool_paths(runtime, name)
    digest_now = _sha256_file(paths["adapter"])
    if not evidence:
        return {
            "ok": False,
            "failure_class": "unverified",
            "reason": (
                f"'{name}' has no test evidence — an MCP capability is only registered "
                "after its adapter called the real tool"
            ),
        }
    if str(evidence.get("adapter_digest") or "") != digest_now:
        return {
            "ok": False,
            "failure_class": "adapter_changed",
            "reason": (
                "adapter.json changed after the test — the evidence no longer covers these "
                "bytes; re-map and re-test instead of registering"
            ),
        }
    if not evidence.get("ok"):
        return {
            "ok": False,
            "failure_class": "test_failed",
            "reason": (
                "the recorded MCP call did not pass: "
                + str(evidence.get("reason") or evidence.get("difference") or "no detail")[:200]
            ),
        }

    previous = store.get(name)
    version = version or _next_version(previous)
    manifest_path = paths["manifest"]
    module_name = _module_name_for(name)
    if manifest_path.is_file():
        try:
            module_name = str(
                (yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {})
                .get("adapter", {})
                .get("module", module_name)
            )
        except Exception:  # noqa: BLE001 — a broken manifest falls back to the default
            pass
    manifest = mcp_manifest(
        adapter,
        name=name,
        status="ready",
        module_name=module_name,
        version=version,
        project_id=project_id,
    )
    manifest["last_tested_at"] = float(evidence.get("tested_at") or time.time())
    manifest["validation"] = {
        "status": "validated",
        "at": evidence.get("tested_at"),
        "detail": (
            f"real tools/call over {adapter.get('server', {}).get('transport')}: "
            f"observed {json.dumps(evidence.get('observed'), sort_keys=True, default=str)} "
            f"== expected {json.dumps(evidence.get('expected'), sort_keys=True, default=str)}"
        )[:300],
    }
    server = dict(adapter.get("server") or {})
    spec = ToolSpec(
        name=name,
        title=f"MCP: {adapter.get('capability', name)}",
        description=(
            f"{adapter.get('capability')} — calls MCP tool '{adapter.get('tool')}' over "
            f"{server.get('transport')} (protocol {server.get('protocol_version')}); "
            "inputs and outputs are typed from the server's own JSON schemas"
        )[:600],
        tags=["mcp", str(adapter.get("tool") or ""), str(server.get("transport") or "")],
        kind=MCP_KIND,
        module=module_name,
        entrypoint="main",
        test_command=[sys.executable, "-m", "pytest", "-q", "tests/"],
        sample_input=dict(evidence.get("input") or {}),
        rationale=(
            f"mapped from the MCP surface of '{server.get('name')}'; the adapter called the "
            f"real tool ({json.dumps(evidence.get('observed'), sort_keys=True, default=str)})"
        )[:600],
        provenance=f"mcp-adapter:{requested_by}",
        status=STATUS_APPROVED,
        version=version,
        scan_summary=(
            "MCP adapter: typed from the server's JSON schemas, one real call recorded, "
            "no host filesystem access, transport "
            + str(server.get("transport") or "")
            + (
                " (stdio child process)"
                if server.get("transport") == "stdio"
                else " (declared endpoint only)"
            )
        ),
    )
    publish = store.publish(
        spec,
        payload={
            "kind": MCP_KIND,
            "runtime": MCP_KIND,
            "transport": server.get("transport"),
            "tool": adapter.get("tool"),
            "server_command": list(server.get("command") or []),
            "server_url": server.get("url", ""),
            "protocol_version": server.get("protocol_version"),
            "version": version,
            "adapter_digest": digest_now,
            "tested_at": evidence.get("tested_at"),
        },
    )
    rollback_id = snapshot_for_rollback(
        runtime, name, previous.to_dict() if previous is not None else None
    )
    _write_text(manifest_path, yaml.safe_dump(manifest, sort_keys=False))
    evidence = dict(evidence)
    evidence.update(
        {
            "registered": True,
            "registered_at": time.time(),
            "version": version,
            "rollback_id": rollback_id,
        }
    )
    _write_text(paths["evidence"], json.dumps(evidence, indent=2, sort_keys=True))
    state = health.record_verification(
        runtime,
        name,
        ok=True,
        detail=(
            f"mapped MCP tool '{adapter.get('tool')}' over {server.get('transport')} answered "
            f"{json.dumps(evidence.get('observed'), sort_keys=True, default=str)}"
        )[:200],
    )
    health_report = health.file_state(runtime, name)
    exportable = spec.kind == KIND_PYTHON
    return {
        "ok": True,
        "registered": True,
        "name": name,
        "version": version,
        "status": spec.status,
        "kind": MCP_KIND,
        "tool": adapter.get("tool"),
        "server": server,
        "permissions": dict(adapter.get("permissions") or MCP_PERMISSIONS),
        "health": health_report,
        "health_state": str(state),
        "rollback_id": rollback_id,
        "publish": publish,
        "module": str(paths["module"]),
        "wrapper": str(paths["wrapper"]),
        "manifest": str(manifest_path),
        "spec": spec.to_dict(),
        "exportable_as_python_tool": exportable,
        "warnings": []
        if exportable
        else [
            "kind=mcp is not re-exported by `skaldir mcp serve` (it exports approved "
            "kind=python specs only) — the capability is registered and callable via "
            "`mcp call`, the export filter is the mcpserver owner's change"
        ],
        "test": {
            "input": evidence.get("input"),
            "expected": evidence.get("expected"),
            "observed": evidence.get("observed"),
            "oracle": evidence.get("oracle"),
            "tested_at": evidence.get("tested_at"),
        },
    }


def _next_version(previous: object) -> str:
    """A re-registered capability gets a NEW patch version."""
    if previous is None:
        return "0.1.0"
    try:
        numbers = [int(part) for part in str(getattr(previous, "version", "0.1.0")).split(".")[:3]]
    except ValueError:
        return "0.1.0"
    while len(numbers) < 3:
        numbers.append(0)
    numbers[2] += 1
    return ".".join(str(item) for item in numbers)


def snapshot_for_rollback(runtime: str | Path, name: str, previous_spec: dict | None) -> str:
    """Record what rollback must restore (the previous spec + whether files existed)."""
    snapshot_dir = mcp_tool_paths(runtime, name)["rollbacks"]
    snapshot_id = f"{int(time.time() * 1000)}"
    payload = {
        "name": name,
        "at": time.time(),
        "previous_spec": previous_spec,
        "had_adapter": mcp_tool_paths(runtime, name)["adapter"].is_file(),
    }
    try:
        _write_text(
            snapshot_dir / f"{snapshot_id}.json", json.dumps(payload, indent=2, sort_keys=True)
        )
    except OSError:
        return ""
    return snapshot_id


# --------------------------------------------------------------------------- #
# one-shot: handshake → map → test (what the CLI and a test drive)              #
# --------------------------------------------------------------------------- #


def probe_server(
    spec: MCPServerSpec,
    *,
    protocol_version: str = DEFAULT_PROTOCOL_VERSION,
    allow_unknown_protocol: bool = False,
) -> dict:
    """Handshake + tool list, with a transcript log. Nothing is written."""
    report = handshake(
        spec,
        protocol_version=protocol_version,
        allow_unknown_protocol=allow_unknown_protocol,
    )
    report["summary"] = {
        "transport": report.get("transport") or spec.transport,
        "server": dict(report.get("server_info") or {}),
        "protocol_version": report.get("protocol_version") or "",
        "tools": [
            {
                "name": str(tool.get("name") or ""),
                "typed": validate_tool_schema(tool)[0],
                "inputs": sorted((tool.get("inputSchema") or {}).get("properties") or {}),
                "outputs": sorted((tool.get("outputSchema") or {}).get("properties") or {}),
            }
            for tool in report.get("tools") or []
        ],
    }
    return report


def _now_id() -> str:
    return f"mcp-{uuid.uuid4().hex[:10]}"


__all__ = [
    "DEFAULT_PROTOCOL_VERSION",
    "DEFAULT_TIMEOUT",
    "HttpTransport",
    "MCP_ADAPTER_FILE",
    "MCP_EVIDENCE_FILE",
    "MCP_KIND",
    "MCP_MANIFEST_TYPE",
    "MCP_PERMISSIONS",
    "MCPClient",
    "MCPError",
    "MCPRefusal",
    "MCPServerSpec",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "StdioTransport",
    "Transcript",
    "call_mapped_tool",
    "call_staged_adapter",
    "compare_expectation",
    "handshake",
    "load_mapped_tool",
    "map_surface",
    "map_tool_to_capability",
    "mcp_manifest",
    "mcp_tool_paths",
    "probe_server",
    "read_tool_result",
    "register_mapped_tool",
    "snapshot_for_rollback",
    "stage_mcp_tool",
    "test_mapped_tool",
    "validate_payload",
    "validate_tool_schema",
]
