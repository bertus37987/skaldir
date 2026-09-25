"""The performance seams: pooled client, cached config read, kill switch.

These tests pin the *behaviour* the CODEX-PERF block changed, so a later
refactor cannot silently restore a client-per-call or a config-parse-per-call:

* one ``httpx.Client`` per process (reused), and the real proof — a stub
  endpoint that counts TCP connections sees **one** for several calls;
* a config block read once per file identity, invalidated when the file
  changes;
* ``SKALDIR_PERF_NOCACHE=1`` restoring exactly the pre-fix lifecycle (fresh
  client, closed per call), because the before/after table depends on it.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from skaldir import perf_runtime as pr
from skaldir.llm_provider import OpenAICompatibleLLM, resolve_provider_config


@pytest.fixture(autouse=True)
def _clean_seams():
    pr.reset_caches()
    yield
    pr.reset_caches()


# ------------------------------------------------------------------- stub server


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # noqa: ARG002
        return


class CountingStub:
    """A minimal OpenAI-compatible endpoint that counts TCP connections."""

    def __init__(self) -> None:
        self.ports: list[int] = []
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            disable_nagle_algorithm = True

            def log_message(self, format, *args):  # noqa: A002, ARG002
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                with stub._lock:
                    stub.ports.append(self.client_address[1])
                body = json.dumps(
                    {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = _Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.02), daemon=True
        )

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    @property
    def connections(self) -> int:
        with self._lock:
            return len(set(self.ports))

    def __enter__(self) -> CountingStub:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False


def client_for(stub: CountingStub, **overrides) -> OpenAICompatibleLLM:
    settings = {"base_url": stub.base_url, "model": "m", "api_key": "k"}
    settings.update(overrides)
    cfg = resolve_provider_config(settings, env={}, config_path=Path("/nonexistent/absent.yaml"))
    return OpenAICompatibleLLM(cfg)


# ------------------------------------------------------------------- the client


def test_http_client_is_reused_for_the_same_options():
    first = pr.http_client(follow_redirects=False)
    second = pr.http_client(follow_redirects=False)
    assert first is second, "a second call must hand back the pooled client"
    report = pr.runtime_report()
    assert report["http_clients_open"] == 1
    assert report["counters"]["http_client_created"] == 1
    assert report["counters"]["http_client_reused"] >= 1


def test_two_provider_calls_share_one_tcp_connection():
    """The whole point: a multi-step run must not connect once per step."""
    with CountingStub() as stub:
        client = client_for(stub)
        client.chat([{"role": "user", "content": "one"}])
        client.chat([{"role": "user", "content": "two"}])
        assert stub.connections == 1, "the pooled client must keep the connection"


def test_kill_switch_restores_one_connection_per_call(monkeypatch):
    """``SKALDIR_PERF_NOCACHE=1`` is the pre-fix lifecycle — and it is measured."""
    monkeypatch.setattr(pr, "CACHE_ENABLED", False)
    with CountingStub() as stub:
        client = client_for(stub)
        client.chat([{"role": "user", "content": "one"}])
        client.chat([{"role": "user", "content": "two"}])
        assert stub.connections == 2, "without pooling every call opens its own connection"


def test_an_injected_client_is_used_and_never_closed():
    class Recording:
        def __init__(self) -> None:
            self.closed = False
            self.calls = 0

        def post(self, url, **kwargs):  # noqa: ARG002
            self.calls += 1
            raise RuntimeError("stop here — only the transport is under test")

        def close(self) -> None:
            self.closed = True

    injected = Recording()
    cfg = resolve_provider_config(
        {"base_url": "http://127.0.0.1:9/v1", "model": "m", "api_key": "k", "max_retries": 0},
        env={},
        config_path=Path("/nonexistent/absent.yaml"),
    )
    client = OpenAICompatibleLLM(cfg, client=injected)
    with pytest.raises(Exception):  # noqa: B017 - the transport raises by design
        client.chat([{"role": "user", "content": "x"}])
    assert injected.calls == 1
    assert injected.closed is False, "an injected client is the caller's to close"
    client.close()  # a no-op for an injected client — must not touch it
    assert injected.closed is False


def test_reset_caches_closes_and_clears():
    pr.http_client(follow_redirects=False)
    assert pr.runtime_report()["http_clients_open"] == 1
    assert pr.reset_caches() is None
    assert pr.runtime_report()["http_clients_open"] == 0


# ------------------------------------------------------------------- the config


def test_config_block_is_read_once_then_cached(tmp_path: Path):
    path = tmp_path / "llm.yaml"
    path.write_text("godsv:\n  llm:\n    model: first\n", encoding="utf-8")
    calls: list[int] = []

    def loader(target: Path):
        calls.append(1)
        return target.read_text(encoding="utf-8")

    assert pr.config_block(path, loader) == "godsv:\n  llm:\n    model: first\n"
    assert pr.config_block(path, loader) == "godsv:\n  llm:\n    model: first\n"
    assert len(calls) == 1, "a second read of an unchanged file must be a cache hit"
    assert pr.cache_stats()["config_cache_hit"] == 1


def test_config_block_sees_a_rewrite(tmp_path: Path):
    path = tmp_path / "llm.yaml"
    path.write_text("a: 1\n", encoding="utf-8")
    first = pr.config_block(path, lambda target: target.read_text(encoding="utf-8"))
    time.sleep(0.002)  # a distinct mtime_ns
    path.write_text("a: 22\n", encoding="utf-8")  # different size AND mtime
    second = pr.config_block(path, lambda target: target.read_text(encoding="utf-8"))
    assert first != second, "a changed file must never be served from the cache"
    assert second == "a: 22\n"


def test_kill_switch_disables_the_config_cache(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(pr, "CACHE_ENABLED", False)
    path = tmp_path / "llm.yaml"
    path.write_text("a: 1\n", encoding="utf-8")
    calls: list[int] = []

    def loader(target: Path):
        calls.append(1)
        return target.read_text(encoding="utf-8")

    pr.config_block(path, loader)
    pr.config_block(path, loader)
    assert len(calls) == 2, "with the switch off every call reads the file again"
    assert pr.cache_stats()["config_cache_hit"] == 0


def test_resolve_provider_config_still_reads_the_file_it_is_given(tmp_path: Path):
    """A config *change* must be visible to the shipped resolution path."""
    path = tmp_path / "config.yaml"
    path.write_text("godsv:\n  llm:\n    model: alpha\n", encoding="utf-8")
    first = resolve_provider_config({}, env={}, config_path=path)
    assert first.model == "alpha"
    time.sleep(0.002)
    path.write_text("godsv:\n  llm:\n    model: beta-model\n", encoding="utf-8")
    second = resolve_provider_config({}, env={}, config_path=path)
    assert second.model == "beta-model", "the cached block must follow the file"


def test_runtime_report_names_the_kill_switch():
    report = pr.runtime_report()
    assert set(report) >= {"cache_enabled", "http_clients_open", "config_entries", "counters"}
    assert report["kill_switch"] == "SKALDIR_PERF_NOCACHE=1"
