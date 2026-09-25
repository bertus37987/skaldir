"""RF fixture contract — the isolated HTTP/SSE fault provider behaves as specified.

RFC §3A requires a reproducible test provider; RFC §5 names the deterministic fault
set. This pins the provider's own contract (real HTTP/1.1 + SSE, real chunked
framing, real status codes) so the RF measurement in ``tools/rf_measure.py`` stands
on a verified fixture rather than on trust. Stdlib only — no SDK needed.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))


def _wait_records(state, count: int = 1, timeout: float = 5.0, interval: float = 0.02):
    """The provider appends its attribution record at the END of the request handler.

    Reading ``records[-1]`` immediately after the client closed the socket is a race
    that only shows up under full-suite load (measured 17.09.: 1 red in the full run,
    3/3 green when the file runs alone). Wait bounded instead of asserting a timing.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(state.records) >= count:
            return state.records
        time.sleep(interval)
    raise AssertionError(
        f"provider wrote {len(state.records)} attribution record(s), expected >= {count}"
    )


def _load(port: int, **opts):
    provider_path = REPO / "tools" / "rf_test_provider.py"
    spec = importlib.util.spec_from_file_location("rf_provider_t", provider_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["rf_provider_t"] = module
    spec.loader.exec_module(module)
    state = module.State()
    state.mode = "healthy_fast"
    state.options = SimpleNamespace(**opts)
    module.Handler.state = state
    server = module.QuietServer(("127.0.0.1", port), module.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return module, state, server


@pytest.fixture()
def provider():
    port = _free_port()
    module, state, server = _load(
        port, deltas=3, interval=0.05, stall_after=1, stall_secs=1.0,
        prefill_secs=0.2, ping_every=0.3, retry_after=120, silence_max=1.0,
    )
    yield module, state, port
    server.shutdown()
    server.server_close()


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _request(port: int, *, stream=True, timeout=6.0):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    payload = {"model": "rf-test", "stream": stream, "messages": [{"role": "user", "content": "x"}]}
    conn.request(
        "POST",
        "/v1/chat/completions",
        body=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    return conn, conn.getresponse()


def _set_mode(port: int, mode: str) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", f"/__mode?name={mode}")
    assert conn.getresponse().status == 200
    conn.close()


def test_healthy_stream_terminates_with_proper_chunked_framing(provider):
    module, state, port = provider
    _set_mode(port, "healthy_fast")
    conn, resp = _request(port)
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "text/event-stream"
    body = resp.read()  # de-chunks and stops at the terminating zero chunk
    conn.close()
    frames = [json.loads(line[5:]) for line in body.splitlines() if line.startswith(b"data:")]
    assert len(frames) == 4  # 3 real deltas + the finish frame
    assert frames[-1]["choices"][0]["finish_reason"] == "stop"
    record = _wait_records(state)[-1]
    assert record["finished"] is True
    assert record["semantic_frames"] == 3
    assert record["mode"] == "healthy_fast"


def test_attribution_record_has_the_rfc3a_fields(provider):
    module, state, port = provider
    _set_mode(port, "healthy_slow")
    conn, resp = _request(port)
    resp.read()
    conn.close()
    record = _wait_records(state)[-1]
    for field in (
        "request_index", "mode", "model", "stream", "messages", "tools", "body_bytes",
        "wire_tokens_est", "t0_received", "t_response_headers", "t_headers",
        "t_first_semantic", "t_last_semantic", "t_end", "total_secs", "finished",
    ):
        assert field in record, f"missing attribution field {field}"


def test_rate_limit_carries_retry_after(provider):
    module, state, port = provider
    _set_mode(port, "rate_limit")
    conn, resp = _request(port)
    assert resp.status == 429
    assert resp.getheader("Retry-After") == "120"
    assert json.loads(resp.read())["error"]["type"] == "rate_limit_error"
    conn.close()
    assert state.records[-1]["retry_after"] == 120


def test_headers_without_sse_sends_no_single_byte(provider):
    module, state, port = provider
    _set_mode(port, "headers_no_sse")
    # Client timeout < the provider's silence window: the read must time out while
    # the connection is still open, which is exactly the 'headers then nothing' fault.
    conn, resp = _request(port, timeout=0.4)
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "text/event-stream"
    with pytest.raises(TimeoutError):
        resp.read(1)
    conn.close()
    for _ in range(40):
        if state.records and state.records[-1]["mode"] == "headers_no_sse":
            break
        time.sleep(0.1)
    record = state.records[-1]
    assert record["mode"] == "headers_no_sse"
    assert "t_first_semantic" not in record
    assert record.get("finished") is not True


def test_ping_only_is_liveness_without_progress(provider):
    module, state, port = provider
    _set_mode(port, "ping_only")
    conn, resp = _request(port, timeout=0.5)
    assert resp.status == 200
    lines = []
    try:
        while len(lines) < 8:
            lines.append(resp.fp.readline())
    except TimeoutError:
        pass
    conn.close()
    assert lines, "expected SSE ping frames within the window"
    assert not any(line.startswith(b"data:") for line in lines), "pings are not progress"
    assert any(b": ping" in line for line in lines)


def test_http_error_modes_and_mode_switch(provider):
    module, state, port = provider
    for mode, status in (("auth_error", 401), ("unavailable", 503), ("context_overflow", 400)):
        _set_mode(port, mode)
        conn, resp = _request(port)
        assert resp.status == status, mode
        resp.read()
        conn.close()
    _set_mode(port, "healthy_fast")
    assert state.mode == "healthy_fast"
