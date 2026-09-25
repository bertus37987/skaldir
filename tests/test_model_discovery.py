"""Exercise model discovery on the wire, not a more permissive fake client."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from skaldir import model_discovery as md


@pytest.fixture
def server():
    calls = []
    replies = [(200, {"data": [{"id": "first"}, {"id": "second"}, {"id": "first"}]}, {})]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, dict(self.headers)))
            status, payload, headers = replies[min(len(calls) - 1, len(replies) - 1)]
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{http.server_port}/v1", calls, replies
    http.shutdown()
    http.server_close()
    thread.join(2)


def test_live_ids_are_exact_deduplicated_and_use_selected_endpoint(server):
    base, calls, _ = server
    result = md.discover_models(base, api_key="synthetic-test-key")
    assert result.ids == ("first", "second")
    assert result.source == "live"
    assert calls == [("/v1/models", calls[0][1])]
    assert calls[0][1].get("Authorization", calls[0][1].get("authorization")) == (
        "Bearer synthetic-test-key"
    )


@pytest.mark.parametrize("status,payload", [(401, {"data": [{"id": "bad"}]}),
                                          (200, {"data": []}), (200, b"not json")])
def test_error_empty_or_malformed_are_not_live_success(server, status, payload):
    base, _, replies = server
    replies[:] = [(status, payload, {})]
    result = md.discover_models(base, api_key="synthetic-secret")
    assert result.source == "unavailable"
    assert result.ids == ()
    assert "synthetic-secret" not in repr(result)


def test_anthropic_pagination_is_json_after_id_not_headers(server):
    base, calls, replies = server
    replies[:] = [(200, {"data": [{"id": "one"}], "has_more": True, "last_id": "one"}, {}),
                  (200, {"data": [{"id": "two"}], "has_more": False}, {})]
    result = md.discover_models(base, api_key="synthetic", protocol="anthropic")
    assert result.ids == ("one", "two")
    assert calls[1][0] == "/v1/models?after_id=one"
    assert calls[0][1]["x-api-key"] == "synthetic"


def test_nonchat_metadata_is_filtered_but_vision_input_is_not(server):
    base, _, replies = server
    replies[:] = [(200, {"data": [
        {"id": "vision", "architecture": {"input_modalities": ["image", "text"],
                                             "output_modalities": ["text"]}},
        {"id": "picture", "architecture": {"output_modalities": ["image"]}},
        {"id": "vec", "type": "embedding"},
        {"id": "movie", "metadata": {"tags": ["text-to-video"]}},
        {"id": "bad\u001b[31m"}, {"id": "*"}, {"id": " spaced "}, {"id": "~route/model"},
    ]}, {})]
    assert md.discover_models(base).ids == ("vision", "~route/model")


@pytest.mark.parametrize("suffix", ["?token=synthetic", "#secret", "?other=synthetic"])
def test_url_query_or_fragment_is_refused_before_contact(server, suffix):
    base, calls, _ = server
    assert md.discover_models(base + suffix).source == "unavailable"
    assert calls == []


def test_redirect_is_never_followed_even_on_same_origin(server):
    base, calls, replies = server
    replies[:] = [(302, {}, {"Location": base + "/other"})]
    assert md.discover_models(base, api_key="synthetic").source == "unavailable"
    assert len(calls) == 1

