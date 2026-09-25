#!/usr/bin/env python3
"""RF-01: isolated HTTP/SSE test provider with deterministic fault injection.

RFC §3A asks for a reproducible HTTP/SSE test provider plus a cleaned long-task
fixture, and RFC §5 names the deterministic set: no headers, headers without SSE,
pings only, reasoning only, healthy-but-slow, partial tool call + disconnect,
429 with a long Retry-After, 503, auth error, context overflow, a smaller
fallback model, failing compression/summary, clock jump, stop race, late old
attempt, restart and double scheduler delivery.

This provider serves the OpenAI-compatible ``/v1/chat/completions`` shape for the
*provider-side* modes. Every request is logged as a JSONL attribution record with
the phase timestamps RFC §3A wants — no headers, no keys, no prompt bodies.

Usage
  python3 tools/rf_test_provider.py --port 8731 --mode healthy_slow &
  curl -s 'localhost:8731/__mode?name=ping_only'   # switch modes without restart
  curl -s localhost:8731/__log                     # JSONL records so far
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: Modes that answer with a non-200 status before any body byte.
HTTP_ERROR_MODES = {
    "auth_error": (401, {"error": {"message": "invalid api key", "type": "invalid_request_error"}}),
    "rate_limit": (429, {"error": {"message": "rate limited", "type": "rate_limit_error"}}),
    "unavailable": (503, {"error": {"message": "overloaded", "type": "server_error"}}),
    "context_overflow": (
        400,
        {
            "error": {
                "message": "This model's maximum context length is 8192 tokens",
                "type": "invalid_request_error",
            }
        },
    ),
}

MODES = (
    "healthy_fast",
    "healthy_slow",
    "headers_no_sse",
    "ping_only",
    "reasoning_only",
    "partial_toolcall_disconnect",
    "stall_mid",
    "slow_prefill",
    *HTTP_ERROR_MODES,
)


class QuietServer(ThreadingHTTPServer):
    """A fault harness aborts connections on purpose — keep the console clean.

    ``handle_error`` lives on the SERVER (``process_request_thread`` calls it), so
    suppressing it on the handler alone leaves tracebacks in the evidence logs.
    """

    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.mode = "healthy_fast"
        self.records: list[dict] = []
        self.request_index = 0

    def next_index(self) -> int:
        with self.lock:
            self.request_index += 1
            return self.request_index

    def log(self, record: dict) -> None:
        with self.lock:
            self.records.append(record)


def chunk(text: str = "", *, reasoning: str = "", tool: dict | None = None, finish: str | None = None) -> dict:
    delta: dict = {}
    if text:
        delta["content"] = text
    if reasoning:
        delta["reasoning_content"] = reasoning
    if tool is not None:
        delta["tool_calls"] = [tool]
    choice: dict = {"index": 0, "delta": delta}
    if finish is not None:
        choice["finish_reason"] = finish
    return {
        "id": "chatcmpl-rf",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "rf-test",
        "choices": [choice],
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: State

    def log_message(self, *args) -> None:  # console stays clean; we log JSONL
        pass

    # ---------------------------------------------------------------- control --
    def do_GET(self) -> None:
        if self.path.startswith("/__mode"):
            name = self.path.split("name=", 1)[-1] if "name=" in self.path else ""
            if name not in MODES:
                self._json(400, {"error": f"unknown mode; one of {MODES}"})
                return
            with self.state.lock:
                self.state.mode = name
            self._json(200, {"mode": name})
            return
        if self.path.startswith("/__log"):
            with self.state.lock:
                body = "\n".join(json.dumps(r) for r in self.state.records)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Length", str(len(body.encode())))
            self.end_headers()
            self.wfile.write(body.encode())
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self.path.endswith("/chat/completions"):
            self._json(404, {"error": "not found"})
            return
        index = self.state.next_index()
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        with self.state.lock:
            mode = self.state.mode
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = {}
        record = {
            "request_index": index,
            "mode": mode,
            "stream": bool(payload.get("stream")),
            "model": str(payload.get("model") or ""),
            "body_bytes": len(raw),
            "wire_tokens_est": max(1, len(raw) // 4),
            "messages": len(payload.get("messages") or []),
            "tools": len(payload.get("tools") or []),
            "t0_received": time.monotonic(),
        }
        try:
            self._serve(record, payload)
        except (BrokenPipeError, ConnectionResetError):
            record["client_aborted"] = True
        finally:
            record["t_end"] = time.monotonic()
            record["total_secs"] = round(record["t_end"] - record["t0_received"], 3)
            self.state.log(record)

    # ---------------------------------------------------------------- helpers --
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _elapsed(self, record: dict) -> float:
        return round(time.monotonic() - record["t0_received"], 3)

    def _chunked(self, data: bytes) -> None:
        """Write ONE HTTP/1.1 chunk — the SSE response is declared chunked, so the
        body must carry chunk framing (a raw write makes clients parse the first
        line as a chunk header and abort)."""
        self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
        self.wfile.flush()

    def _end_chunks(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _frame(self, record: dict, obj: dict, *, semantic: bool) -> None:
        self._chunked(b"data: " + json.dumps(obj).encode() + b"\n\n")
        record.setdefault("t_headers", self._elapsed(record))
        if semantic:
            record.setdefault("t_first_semantic", self._elapsed(record))
            record["t_last_semantic"] = self._elapsed(record)

    def _sse(self, record: dict) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        record["t_response_headers"] = self._elapsed(record)

    def _delay(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def _client_gone(self) -> bool:
        """True once the peer closed — so a 'silent provider' mode stops promptly."""
        import socket as _socket

        try:
            self.connection.setblocking(False)
            try:
                return self.connection.recv(1, _socket.MSG_PEEK) == b""
            finally:
                self.connection.setblocking(True)
        except (BlockingIOError, InterruptedError):
            return False
        except OSError:
            return True

    def _silent(self, record: dict, seconds: float, *, ping_every: float = 0.0) -> None:
        """Hold the connection open without semantic progress (SSE pings optional)."""
        deadline = time.monotonic() + seconds
        next_ping = time.monotonic() + ping_every if ping_every else None
        while time.monotonic() < deadline:
            if self._client_gone():
                record["client_aborted"] = True
                return
            if next_ping is not None and time.monotonic() >= next_ping:
                self._chunked(b": ping\n\n")  # SSE comment: liveness, no delta
                record["pings"] = int(record.get("pings", 0)) + 1
                next_ping = time.monotonic() + ping_every
            time.sleep(0.1)

    def _serve(self, record: dict, payload: dict) -> None:
        mode = record["mode"]
        opts = self.state.options

        if mode in HTTP_ERROR_MODES:
            status, body = HTTP_ERROR_MODES[mode]
            record["status"] = status
            if mode == "rate_limit":
                self.send_response(status)
                self.send_header("Retry-After", str(opts.retry_after))
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(json.dumps(body).encode())))
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())
                record["retry_after"] = opts.retry_after
            else:
                self._json(status, body)
            record["t_response_headers"] = self._elapsed(record)
            return

        if mode == "slow_prefill":
            record["prefill_secs"] = opts.prefill_secs
            self._delay(opts.prefill_secs)
        self._sse(record)

        if mode == "headers_no_sse":
            # Headers, then nothing at all: the case the wait monitor has to name.
            self._silent(record, opts.silence_max)
            return

        if mode == "ping_only":
            record["stream_shape"] = "ping_only"
            self._silent(record, opts.silence_max, ping_every=opts.ping_every)
            return

        if mode == "reasoning_only":
            record["stream_shape"] = "reasoning_only"
            for i in range(opts.deltas):
                self._frame(record, chunk(reasoning=f"thinking step {i}. "), semantic=True)
                self._delay(opts.interval)
            self._frame(record, chunk(finish="stop"), semantic=False)
            self._end_chunks()
            record["finished"] = True
            return

        if mode == "partial_toolcall_disconnect":
            record["stream_shape"] = "partial_toolcall"
            self._frame(record, chunk("Let me call the tool."), semantic=True)
            self._frame(
                record,
                chunk(tool={"index": 0, "id": "call_rf", "type": "function", "function": {"name": "rf_tool", "arguments": '{"a":'}}),
                semantic=True,
            )
            # Disconnect mid-call: no finish_reason, no complete arguments.
            self.close_connection = True
            record["disconnected_mid_toolcall"] = True
            return

        # healthy_fast / healthy_slow / stall_mid
        healthy = opts.deltas if mode != "stall_mid" else opts.stall_after
        interval = opts.interval if mode != "healthy_fast" else min(opts.interval, 0.05)
        for i in range(healthy):
            self._frame(record, chunk(f"token{i} "), semantic=True)
            self._delay(interval)
        record["semantic_frames"] = healthy
        if mode == "stall_mid":
            record["stall_secs"] = opts.stall_secs
            self._silent(record, opts.stall_secs)
            if record.get("client_aborted"):
                return
        self._frame(record, chunk(finish="stop"), semantic=False)
        self._end_chunks()
        record["finished"] = True


def main() -> int:
    parser = argparse.ArgumentParser(description="RF test provider (OpenAI-compatible SSE)")
    parser.add_argument("--port", type=int, default=8731)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--mode", default="healthy_fast", choices=MODES)
    parser.add_argument("--deltas", type=int, default=8)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--stall-after", type=int, default=3)
    parser.add_argument("--stall-secs", type=float, default=30.0)
    parser.add_argument("--prefill-secs", type=float, default=20.0)
    parser.add_argument("--ping-every", type=float, default=2.0)
    parser.add_argument("--retry-after", type=int, default=120)
    parser.add_argument("--silence-max", type=float, default=45.0)
    parser.add_argument("--max-seconds", type=float, default=0.0, help="auto-exit (0 = forever)")
    args = parser.parse_args()

    state = State()
    state.mode = args.mode
    state.options = args
    Handler.state = state
    server = QuietServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"rf-test-provider on http://{args.host}:{args.port} mode={args.mode}", flush=True)
    if args.max_seconds > 0:
        threading.Timer(args.max_seconds, server.shutdown).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
