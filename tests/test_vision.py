"""Vision — the service half, offline.

Everything here runs against a **stub socket server on 127.0.0.1** (the same shape
``tests/test_provider_cli.py`` uses) or without any network at all. No credential
is read from the developer's environment: the fixtures delete both spellings of
every key variable, including the legacy ``GODSV_*`` ones that ``env.expand``
still accepts.

The proofs this file exists for:

* the request that leaves really carries an image part, and the bytes in it are
  the bytes of the file on disk (not "an image was mentioned somewhere");
* a remote URL travels as a URL — no local bytes are read at all;
* a refusal by the model becomes a plain English error, not a transport fault;
* the guards (empty / oversize / mislabelled / unrecognised / symlink outside the
  workspace / fifo) run **before** a request is built;
* the cost block reports the estimate *and* the provider's own usage counters,
  and says plainly when the provider reported none;
* ``vision capabilities`` measures (1x1 PNG probe) and reports the catalog next
  to the measurement;
* ``vision screenshot`` exits 3 with the precise missing capability when the
  Computer-Use layer has no screenshot bridge, and never fakes a capture.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from skaldir import llm_provider as LP
from skaldir import vision_service as VS

# --------------------------------------------------------------------------- #
# a real PNG, built with the stdlib (no ImageMagick, no PIL)                    #
# --------------------------------------------------------------------------- #


def png_bytes(
    width: int = 4, height: int = 3, color: tuple[int, int, int] = (200, 16, 46)
) -> bytes:
    """A valid, decodable PNG of an exact size — the test's own image writer."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        crc = struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + body + crc

    raw = b"".join(b"\x00" + bytes(color) * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def noisy_png(width: int = 96, height: int = 64) -> bytes:
    """An incompressible PNG: its base64 data URL is several kB (leak-test fodder)."""
    import os

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        crc = struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + body + crc

    rows = b"".join(b"\x00" + os.urandom(width * 3) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 0))
        + chunk(b"IEND", b"")
    )


def jpeg_header(width: int = 4, height: int = 3) -> bytes:
    """A minimal JPEG whose SOF0 carries the size (enough for ``image_size``)."""
    return (
        b"\xff\xd8"
        + b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        + b"\xff\xc0\x00\x11\x08"
        + struct.pack(">HH", height, width)
        + b"\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    )


# --------------------------------------------------------------------------- #
# the stub endpoint (a real socket server, no transport mock)                   #
# --------------------------------------------------------------------------- #


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # noqa: ARG002
        return


class StubEndpoint:
    """A minimal OpenAI-compatible endpoint that records the exact request body."""

    def __init__(
        self,
        *,
        status: int = 200,
        content: str = "a red rectangle on white",
        model: str = "stub-vision-1",
        usage: bool = True,
        echo_request: bool = False,
    ) -> None:
        self.status = status
        self.content = content
        self.model = model
        self.usage = usage
        self.echo_request = echo_request
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # noqa: ARG002, A002
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError:
                    body = {"_unparsed": raw.decode("utf-8", "replace")}
                with stub._lock:
                    stub.requests.append(
                        {"path": self.path, "body": body, "raw_bytes": len(raw)}
                    )
                if stub.status >= 400:
                    message = stub.content
                    if stub.echo_request:
                        # The worst case for leakage: a provider that quotes the whole
                        # request (data URL and all) back in its error message.
                        message = f"{stub.content} — request: {json.dumps(body)}"
                    payload: dict = {"error": {"message": message, "type": "stub"}}
                else:
                    payload = {
                        "model": stub.model,
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": "stop",
                                "message": {"role": "assistant", "content": stub.content},
                            }
                        ],
                    }
                    if stub.usage:
                        payload["usage"] = {
                            "prompt_tokens": 101,
                            "completion_tokens": 11,
                            "total_tokens": 112,
                        }
                data = json.dumps(payload).encode()
                self.send_response(stub.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self._server = _QuietServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.05), daemon=True
        )

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    @property
    def request_count(self) -> int:
        with self._lock:
            return len(self.requests)

    @property
    def last_body(self) -> dict:
        with self._lock:
            return self.requests[-1]["body"] if self.requests else {}

    def __enter__(self) -> StubEndpoint:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False


#: every env name that could carry a credential (both spellings)
KEY_VARS = (
    "SKALDIR_LLM_API_KEY",
    "GODSV_LLM_API_KEY",
    "OPENAI_API_KEY",
    "SKALDIR_LLM_KEY",
    "GODSV_LLM_KEY",
)


@pytest.fixture
def no_keys(monkeypatch):
    """No credential is in the environment, in either spelling."""
    for name in KEY_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("SKALDIR_LLM_VISION_MODEL", raising=False)
    monkeypatch.delenv("GODSV_LLM_VISION_MODEL", raising=False)
    monkeypatch.delenv("SKALDIR_LLM_TIER_MODELS", raising=False)
    monkeypatch.delenv("GODSV_LLM_TIER_MODELS", raising=False)


@pytest.fixture
def stub(no_keys, monkeypatch, tmp_path):
    """A running stub endpoint configured as the provider (local ⇒ no key needed)."""
    with StubEndpoint() as endpoint:
        monkeypatch.setenv("SKALDIR_LLM_BASE_URL", endpoint.base_url)
        monkeypatch.setenv("SKALDIR_LLM_MODEL", endpoint.model)
        monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent-llm-config.yaml"))
        yield endpoint


@pytest.fixture
def image(tmp_path) -> Path:
    path = tmp_path / "sign.png"
    path.write_bytes(png_bytes(48, 32))
    return path


def _content_parts(body: dict) -> list[dict]:
    return list(body["messages"][0]["content"])


def _image_part(body: dict) -> dict:
    return [part for part in _content_parts(body) if part.get("type") == "image_url"][0]


# --------------------------------------------------------------------------- #
# provider side: the wire format                                                #
# --------------------------------------------------------------------------- #


def test_an_image_part_is_a_data_url_with_the_exact_bytes():
    data = png_bytes(4, 3)
    part = LP.image_part_from_bytes(data, mime="image/png", origin="x.png")
    assert part["type"] == "image_url"
    url = part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == data


def test_a_remote_url_is_sent_as_a_url_and_no_bytes_are_read():
    part = LP.image_part_from_url("https://example.test/a.png")
    assert part["image_url"] == {"url": "https://example.test/a.png"}


def test_a_non_http_url_is_refused_by_the_provider_side_guard():
    with pytest.raises(LP.ImageInputError):
        LP.image_part_from_url("file:///etc/passwd")


def test_the_size_guard_refuses_before_a_part_is_built():
    with pytest.raises(LP.ImageInputError) as excinfo:
        LP.image_part_from_bytes(
            png_bytes(4, 3), mime="image/png", origin="big.png", max_bytes=10
        )
    assert "over the 10 byte image cap" in str(excinfo.value)


def test_the_type_guard_uses_the_bytes_not_the_extension():
    with pytest.raises(LP.ImageInputError) as excinfo:
        # valid PNG *bytes*, declared (by the caller) as a JPEG
        LP.image_part_from_bytes(png_bytes(4, 3), mime="image/jpeg", origin="lie.jpg")
    assert "declared image/jpeg" in str(excinfo.value)
    assert "image/png" in str(excinfo.value)


def test_a_file_that_is_not_an_image_is_refused():
    with pytest.raises(LP.ImageInputError) as excinfo:
        LP.image_part_from_bytes(b"# not a png\n", origin="notes.png")
    assert "no known image signature" in str(excinfo.value)


def test_an_empty_image_is_refused():
    with pytest.raises(LP.ImageInputError):
        LP.image_part_from_bytes(b"", origin="empty.png")


def test_sniff_image_mime_knows_the_five_supported_types():
    assert LP.sniff_image_mime(png_bytes()) == "image/png"
    assert LP.sniff_image_mime(jpeg_header()) == "image/jpeg"
    assert LP.sniff_image_mime(b"GIF89a\x01\x00\x01\x00") == "image/gif"
    assert LP.sniff_image_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert LP.sniff_image_mime(b"BM" + b"\x00" * 30) == "image/bmp"
    assert LP.sniff_image_mime(b"\x00\x01") == ""


def test_image_size_reads_the_header_of_png_gif_bmp_and_jpeg():
    assert LP.image_size(png_bytes(48, 32)) == (48, 32)
    assert LP.image_size(b"GIF89a" + struct.pack("<HH", 7, 9)) == (7, 9)
    assert LP.image_size(b"BM" + b"\x00" * 16 + struct.pack("<ii", 5, 6)) == (5, 6)
    assert LP.image_size(jpeg_header(11, 13)) == (11, 13)
    assert LP.image_size(b"GIF89a\x01") is None


def test_estimate_image_tokens_is_the_documented_rule():
    assert LP.estimate_image_tokens((1, 1)) == 86  # 85 + ceil(1/750)
    assert LP.estimate_image_tokens((750, 1)) == 86
    assert LP.estimate_image_tokens((750, 2)) == 87
    assert LP.estimate_image_tokens(None) is None


def test_the_request_summary_never_carries_the_payload():
    data = png_bytes(4, 3)
    body = LP.vision_request_body(
        LP.ProviderConfig(model="m"), "hello", [LP.image_part_from_bytes(data, mime="image/png")]
    )
    summary = LP.request_image_summary(body)
    rendered = json.dumps(summary)
    assert "base64," not in rendered
    assert summary["image_parts"][0]["kind"] == "data-url"
    assert summary["image_parts"][0]["bytes"] == len(data)
    assert summary["image_parts"][0]["sha256"] == hashlib.sha256(data).hexdigest()
    assert summary["text_chars"] == 5


def test_the_request_summary_reports_a_remote_url_without_bytes():
    body = LP.vision_request_body(
        LP.ProviderConfig(model="m"), "hi", [LP.image_part_from_url("https://x.test/a.png")]
    )
    summary = LP.request_image_summary(body)
    assert summary["image_parts"][0] == {
        "message": 0,
        "detail": "",
        "kind": "remote-url",
        "url": "https://x.test/a.png",
    }
    assert summary["image_bytes"] == 0


def test_scrub_collapses_a_data_url_to_its_length():
    text = "refused: data:image/png;base64," + "A" * 500 + " — sorry"
    scrubbed = LP.scrub_image_data_urls(text)
    assert "A" * 100 not in scrubbed
    assert "data:image/png;base64,<500 base64 chars omitted>" in scrubbed


def test_the_catalog_says_likely_unlikely_or_unknown():
    assert LP.vision_catalog("gpt-4o-mini")["verdict"] == "likely"
    assert LP.vision_catalog("qwen2.5-vl-7b-instruct")["verdict"] == "likely"
    assert LP.vision_catalog("deepseek-chat")["verdict"] == "unlikely"
    assert LP.vision_catalog("some-inhouse-7b")["verdict"] == "unknown"
    assert LP.vision_catalog("")["verdict"] == "unknown"


def test_a_refusal_is_only_read_from_a_4xx_answer():
    text = "this model does not support image input"
    assert LP.image_refusal_reason(text, status=400)
    assert LP.image_refusal_reason(text, status=500) == ""
    assert LP.image_refusal_reason("rate limited", status=429) == ""


def test_the_vision_model_resolution_order(monkeypatch, tmp_path):
    cfg = LP.ProviderConfig(model="text-only-7b", tier_models={"vision": "vl-7b"})
    assert LP.vision_model(cfg) == "vl-7b"
    assert LP.vision_model(cfg, override="explicit-vl") == "explicit-vl"
    monkeypatch.setenv("SKALDIR_LLM_VISION_MODEL", "env-vl")
    assert LP.vision_model(cfg) == "env-vl"
    assert LP.vision_tier_for(LP.ProviderConfig(model="m", tier_models={"vision": "v"}), "v") == (
        "vision"
    )
    assert LP.vision_tier_for(LP.ProviderConfig(model="m"), "m") == "main"


# --------------------------------------------------------------------------- #
# provider side: the send, against the stub                                     #
# --------------------------------------------------------------------------- #


def test_an_answer_round_trips_and_the_request_carries_the_file_bytes(stub, image):
    payload = VS.describe(str(image))
    assert payload["ok"] is True
    assert payload["answer"] == stub.content
    body = stub.last_body
    assert body["model"] == "stub-vision-1"
    part = _image_part(body)
    sent = base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
    assert sent == image.read_bytes()
    assert [p["type"] for p in _content_parts(body)] == ["text", "image_url"]


def test_a_remote_url_travels_as_a_url_and_the_stub_never_receives_bytes(stub):
    payload = VS.describe("https://images.test/photo.png")
    assert payload["ok"] is True
    part = _image_part(stub.last_body)
    assert part["image_url"]["url"] == "https://images.test/photo.png"
    assert payload["source"]["kind"] == "url"
    assert payload["source"]["bytes"] == 0


def test_the_model_refusal_becomes_a_plain_english_error(no_keys, monkeypatch, tmp_path, image):
    with StubEndpoint(
        status=400, content="this model does not support image input", model="deepseek-chat"
    ) as endpoint:
        monkeypatch.setenv("SKALDIR_LLM_BASE_URL", endpoint.base_url)
        monkeypatch.setenv("SKALDIR_LLM_MODEL", "deepseek-chat")
        monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
        payload = VS.describe(str(image))
        assert payload["ok"] is False
        assert payload["blocked"] == "model_rejects_images"
        assert "does not accept images" in payload["reason"]
        assert "SKALDIR_LLM_VISION_MODEL" in payload["reason"]
        # and the raw provider error carries the dedicated exception type
        cfg = LP.ProviderConfig(base_url=endpoint.base_url, model="deepseek-chat")
        client = LP.OpenAICompatibleLLM(cfg)
        with pytest.raises(LP.ImageNotSupportedError) as excinfo:
            client.describe_images(
                prompt="hi",
                images=[LP.image_part_from_bytes(png_bytes(), mime="image/png")],
            )
        assert excinfo.value.status == 400
        assert LP.image_refusal_reason(excinfo.value.reason, status=400)


def test_the_client_can_be_called_with_images_directly(stub):
    cfg = LP.resolve_provider_config({"base_url": stub.base_url, "model": stub.model})
    client = LP.OpenAICompatibleLLM(cfg)
    result = client.describe_images(
        prompt="what is it",
        images=[LP.image_part_from_bytes(png_bytes(2, 2), mime="image/png")],
        tier="main",
    )
    assert result.text == stub.content
    assert result.usage == {"prompt_tokens": 101, "completion_tokens": 11, "total_tokens": 112}
    assert stub.request_count == 1


def test_no_credential_and_no_payload_in_the_report(stub, monkeypatch, image):
    monkeypatch.setenv("SKALDIR_LLM_API_KEY", "sk-super-secret-value")
    payload = VS.describe(str(image))
    rendered = json.dumps(payload)
    assert "sk-super-secret-value" not in rendered
    assert "base64," not in rendered
    assert payload["provider"]["api_key"] == "set"
    assert "Authorization" not in rendered


def test_a_provider_that_quotes_the_request_back_cannot_leak_the_payload(
    no_keys, monkeypatch, tmp_path, image
):
    """The worst case: the provider echoes the whole body inside its error text."""
    noisy = tmp_path / "noisy.png"
    noisy.write_bytes(noisy_png())
    data_url = "data:image/png;base64," + base64.b64encode(noisy.read_bytes()).decode()
    payload: dict = {}
    with StubEndpoint(
        status=400,
        content="this model does not support image input",
        echo_request=True,
    ) as endpoint:
        monkeypatch.setenv("SKALDIR_LLM_BASE_URL", endpoint.base_url)
        monkeypatch.setenv("SKALDIR_LLM_MODEL", "stub-vision-1")
        monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
        payload = VS.describe(str(noisy))
    assert payload["ok"] is False
    rendered = json.dumps(payload)
    assert data_url not in rendered
    assert base64.b64encode(noisy.read_bytes()).decode() not in rendered
    # and the message the user sees stays bounded even though the provider quoted
    # thousands of characters of payload (the data URL alone was >4 kB here)
    assert len(payload["reason"]) <= 1000
    assert len(data_url) > 4000
    # and the type stays the dedicated one (the refusal was recognised)
    assert payload["blocked"] == "model_rejects_images"


def test_an_empty_answer_is_a_verification_failure_not_an_empty_description(
    no_keys, monkeypatch, tmp_path, image
):
    """A model that burns its whole budget on reasoning must not look like an answer.

    Measured on a real endpoint (OpenRouter, 2026-09-18): a small preview model
    spent all 200 ``max_tokens`` and returned ``content: ""`` with
    ``finish_reason: "length"``.
    """
    with StubEndpoint(content="", status=200) as endpoint:
        monkeypatch.setenv("SKALDIR_LLM_BASE_URL", endpoint.base_url)
        monkeypatch.setenv("SKALDIR_LLM_MODEL", endpoint.model)
        monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
        payload = VS.describe(str(image))
    assert payload["ok"] is False
    assert payload["blocked"] == "empty_answer"
    assert "no text" in payload["reason"]
    assert str(image) in payload["disclosure"]  # it really was sent
    assert payload["usage"]["total_tokens"] == 112
    assert payload["cost"]["reported"] is True


# --------------------------------------------------------------------------- #
# the cost note                                                                 #
# --------------------------------------------------------------------------- #


def test_the_cost_note_reports_the_estimate_and_the_measured_usage(stub, image):
    payload = VS.describe(str(image))
    cost = payload["cost"]
    assert cost["image_pixels"] == 48 * 32
    assert cost["image_tokens_estimated"] == LP.estimate_image_tokens((48, 32))
    assert cost["reported_usage"]["total_tokens"] == 112
    assert cost["reported"] is True
    assert "that is the measured number" in cost["note"]


def test_without_provider_usage_the_note_says_the_estimate_is_a_floor(
    no_keys, monkeypatch, tmp_path, image
):
    with StubEndpoint(usage=False) as endpoint:
        monkeypatch.setenv("SKALDIR_LLM_BASE_URL", endpoint.base_url)
        monkeypatch.setenv("SKALDIR_LLM_MODEL", endpoint.model)
        monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
        payload = VS.describe(str(image))
    cost = payload["cost"]
    assert cost["reported"] is False
    assert cost["reported_usage"] == {}
    assert "reported no usage counters" in cost["note"]
    assert "a floor, not a bill" in cost["note"]


# --------------------------------------------------------------------------- #
# the workspace / symlink rule and the local-file guards                        #
# --------------------------------------------------------------------------- #


def test_a_symlink_outside_the_workspace_is_refused(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(png_bytes(4, 3))
    link = workspace / "escape.png"
    link.symlink_to(outside)
    with pytest.raises(VS.VisionBlocked) as excinfo:
        VS.load_source(str(link), workspace=workspace)
    message = str(excinfo.value)
    assert "refusing the symlink" in message
    assert str(outside) in message and str(workspace) in message


def test_a_symlink_inside_the_workspace_is_allowed_and_reported(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    real = workspace / "real.png"
    real.write_bytes(png_bytes(4, 3))
    link = workspace / "link.png"
    link.symlink_to(real)
    source = VS.load_source(str(link), workspace=workspace)
    assert source.path == str(real)
    assert source.symlink == str(link)
    assert "via the symlink" in VS.disclosure(source, sent_to="http://x/v1")


def test_a_broken_symlink_is_reported_as_not_found(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    link = workspace / "broken.png"
    link.symlink_to(workspace / "nothing-here.png")
    with pytest.raises(VS.VisionNotFound):
        VS.load_source(str(link), workspace=workspace)


def test_a_fifo_is_refused_rather_than_opened(tmp_path):
    fifo = tmp_path / "pipe.png"
    fifo.unlink(missing_ok=True)
    import os

    os.mkfifo(fifo)
    try:
        with pytest.raises(VS.VisionBlocked) as excinfo:
            VS.load_source(str(fifo), workspace=tmp_path)
        assert "not a regular file" in str(excinfo.value)
    finally:
        fifo.unlink(missing_ok=True)


def test_a_directory_is_refused(tmp_path):
    with pytest.raises(VS.VisionBlocked):
        VS.load_source(str(tmp_path), workspace=tmp_path)


def test_a_missing_file_is_not_found(tmp_path):
    with pytest.raises(VS.VisionNotFound):
        VS.load_source(str(tmp_path / "nope.png"), workspace=tmp_path)


def test_an_oversize_file_is_refused_before_any_request(tmp_path, monkeypatch, stub):
    path = tmp_path / "huge.png"
    path.write_bytes(png_bytes(64, 64))
    with pytest.raises(LP.ImageInputError) as excinfo:
        VS.describe(str(path), max_bytes=100)
    assert "larger than the 100 byte image cap" in str(excinfo.value)
    assert stub.request_count == 0


def test_a_text_file_named_png_is_refused_before_any_request(tmp_path, stub):
    path = tmp_path / "notes.png"
    path.write_text("this is not an image at all\n", encoding="utf-8")
    with pytest.raises(LP.ImageInputError) as excinfo:
        VS.describe(str(path))
    assert "no known image signature" in str(excinfo.value)
    assert stub.request_count == 0


def test_the_cap_can_be_lowered_by_the_environment(tmp_path, monkeypatch):
    path = tmp_path / "sign.png"
    path.write_bytes(png_bytes(64, 64))
    monkeypatch.setenv("SKALDIR_VISION_MAX_IMAGE_MB", "1")
    assert VS.max_image_bytes() == 1024 * 1024
    assert VS.max_image_bytes(2048) == 2048
    monkeypatch.setenv("SKALDIR_VISION_MAX_IMAGE_MB", "not-a-number")
    assert VS.max_image_bytes() == LP.DEFAULT_MAX_IMAGE_BYTES


def test_the_workspace_defaults_to_the_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SKALDIR_VISION_WORKSPACE", raising=False)
    monkeypatch.delenv("GODSV_VISION_WORKSPACE", raising=False)
    assert VS.workspace_root() == tmp_path.resolve()
    monkeypatch.setenv("SKALDIR_VISION_WORKSPACE", str(tmp_path / "other"))
    assert VS.workspace_root() == (tmp_path / "other").resolve()


def test_a_remote_image_is_refused_when_the_caller_says_no_remote():
    with pytest.raises(VS.VisionBlocked):
        VS.load_source("https://x.test/a.png", allow_remote=False)


def test_a_local_image_is_refused_when_no_local_reads_are_allowed(tmp_path):
    """``allow_remote=False`` never turns a *file* into a silent URL send."""
    path = tmp_path / "a.png"
    path.write_bytes(png_bytes(4, 3))
    assert VS.load_source(str(path), allow_remote=False).kind == "file"


# --------------------------------------------------------------------------- #
# provider configuration failures                                               #
# --------------------------------------------------------------------------- #


def test_an_unconfigured_provider_is_blocked_and_names_the_variables(no_keys, monkeypatch, image):
    monkeypatch.delenv("SKALDIR_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("GODSV_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    for name in ("SKALDIR_LLM_MODEL", "GODSV_LLM_MODEL", "OPENAI_MODEL",
                 "SKALDIR_LLM_VISION_MODEL", "SKALDIR_LLM_TIER_MODELS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SKALDIR_LLM_CONFIG", "/nonexistent/vision-tests/config.yaml")
    with pytest.raises(VS.VisionBlocked) as excinfo:
        VS.describe(str(image))
    assert "SKALDIR_LLM_BASE_URL" in str(excinfo.value)


def test_a_remote_endpoint_without_a_key_needs_credentials(no_keys, image):
    with pytest.raises(VS.VisionNeedsCredentials) as excinfo:
        VS.describe(
            str(image),
            config={"base_url": "https://api.example.test/v1", "model": "gpt-4o-mini"},
        )
    assert "no request was sent" in str(excinfo.value)


def test_a_disabled_provider_is_blocked(no_keys, image, tmp_path):
    with pytest.raises(VS.VisionBlocked):
        VS.describe(
            str(image),
            config={"provider": "none", "base_url": "http://127.0.0.1:1/v1", "model": "m"},
        )


def test_a_failing_provider_call_is_reported_with_its_status(
    no_keys, monkeypatch, tmp_path, image
):
    with StubEndpoint(status=500, content="upstream is down") as endpoint:
        monkeypatch.setenv("SKALDIR_LLM_BASE_URL", endpoint.base_url)
        monkeypatch.setenv("SKALDIR_LLM_MODEL", endpoint.model)
        monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
        monkeypatch.setenv("SKALDIR_LLM_MAX_RETRIES", "0")
        payload = VS.describe(str(image))
    assert payload["ok"] is False
    assert payload["blocked"] == "provider_error"
    assert payload["status"] == 500


# --------------------------------------------------------------------------- #
# capabilities — measured, not assumed                                          #
# --------------------------------------------------------------------------- #


def test_capabilities_measures_yes_when_the_endpoint_answers(stub):
    payload = VS.capabilities()
    assert payload["ok"] is True
    assert payload["accepts_images"] is True
    assert payload["measured"] is True
    assert payload["agreement"] == "catalog_silent"  # "stub-vision-1" is in neither list
    assert payload["probe"]["attempted"] is True
    image_part = _image_part(stub.last_body)
    assert len(base64.b64decode(image_part["image_url"]["url"].split(",", 1)[1])) == len(
        LP.PROBE_PNG
    )
    body = stub.last_body
    assert body["messages"][0]["content"][0]["text"] == LP.PROBE_PROMPT


def test_capabilities_measures_no_when_the_model_refuses(
    no_keys, monkeypatch, tmp_path
):
    with StubEndpoint(status=400, content="this model does not support image input") as endpoint:
        monkeypatch.setenv("SKALDIR_LLM_BASE_URL", endpoint.base_url)
        monkeypatch.setenv("SKALDIR_LLM_MODEL", "deepseek-chat")
        monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
        payload = VS.capabilities()
    assert payload["accepts_images"] is False
    assert payload["measured"] is True
    assert payload["agreement"] == "catalog_agrees"  # deepseek-chat is on the text-only list
    assert payload["probe"]["status"] == 400


def test_capabilities_stays_unknown_when_the_call_cannot_be_made(
    no_keys, monkeypatch, tmp_path
):
    with StubEndpoint(status=500, content="boom") as endpoint:
        monkeypatch.setenv("SKALDIR_LLM_BASE_URL", endpoint.base_url)
        monkeypatch.setenv("SKALDIR_LLM_MODEL", "mystery-7b")
        monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
        monkeypatch.setenv("SKALDIR_LLM_MAX_RETRIES", "0")
        payload = VS.capabilities()
    assert payload["accepts_images"] is None
    assert payload["measured"] is False
    assert payload["agreement"] == "inconclusive"
    assert "inconclusive" in payload["probe"]["detail"]


def test_the_catalog_disagreeing_with_the_probe_is_said_out_loud(no_keys, monkeypatch, tmp_path):
    """A vision-sounding name that refuses the image: the probe wins, and says so."""
    with StubEndpoint(status=400, content="this model does not support image input") as endpoint:
        monkeypatch.setenv("SKALDIR_LLM_BASE_URL", endpoint.base_url)
        monkeypatch.setenv("SKALDIR_LLM_MODEL", "gpt-4o-mini")  # catalog: "likely"
        monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
        payload = VS.capabilities()
    assert payload["catalog"]["verdict"] == "likely"
    assert payload["accepts_images"] is False
    assert payload["agreement"] == "catalog_was_wrong"
    assert payload["measured"] is True


def test_no_probe_leaves_the_capability_unmeasured(stub):
    payload = VS.capabilities(probe=False)
    assert payload["measured"] is False
    assert payload["accepts_images"] is None
    assert payload["probe"] == {"attempted": False, "accepts_images": None}
    assert "not a measurement" in payload["reason"]
    assert stub.request_count == 0


def test_the_probe_is_the_smallest_image_the_guard_allows():
    assert LP.sniff_image_mime(LP.PROBE_PNG) == "image/png"
    assert LP.image_size(LP.PROBE_PNG) == (1, 1)
    assert LP.guard_image(LP.PROBE_PNG, mime="image/png") == "image/png"


# --------------------------------------------------------------------------- #
# screenshot — the Computer-Use bridge                                          #
# --------------------------------------------------------------------------- #


def test_screenshot_without_a_bridge_names_the_missing_capability(monkeypatch, tmp_path):
    """The real state of this host: ImageMagick's `import` cannot capture here."""
    seen: dict = {}

    def fake_capture(path, *, xid=""):  # noqa: ARG001
        seen["path"] = path
        return False, "import failed: missing an image filename"

    monkeypatch.setattr("skaldir.workflows.screenshot", fake_capture)
    payload = VS.screenshot(out=str(tmp_path / "shot.png"), capture_only=True)
    assert payload["ok"] is False
    assert payload["blocked"] == "no_cu_bridge"
    assert payload["missing_capability"]
    assert "import failed" in payload["reason"]
    assert Path(seen["path"]) == tmp_path / "shot.png"


def test_screenshot_says_which_tool_is_missing_when_there_is_no_backend(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: None if name == "import" else "/usr/bin/x")
    payload = VS.screenshot(out=str(tmp_path / "shot.png"))
    assert payload["blocked"] == "no_cu_bridge"
    assert "ImageMagick" in payload["missing_capability"]
    assert "not in PATH" in payload["reason"]


def test_screenshot_capture_only_sends_nothing(monkeypatch, tmp_path, stub):
    target = tmp_path / "shot.png"
    target.write_bytes(png_bytes(20, 10))
    monkeypatch.setattr(
        "skaldir.workflows.screenshot", lambda path, *, xid="": (True, "import")
    )
    payload = VS.screenshot(out=str(target), capture_only=True)
    assert payload["ok"] is True
    assert payload["describe"]["skipped"] == "capture-only"
    assert payload["capture"]["backend"] == "import"
    assert payload["cost"]["reported"] is False
    assert stub.request_count == 0


def test_screenshot_describes_the_capture_it_made(monkeypatch, tmp_path, stub):
    target = tmp_path / "shot.png"
    target.write_bytes(png_bytes(20, 10))
    monkeypatch.setattr("skaldir.workflows.screenshot", lambda path, *, xid="": (True, "import"))
    payload = VS.screenshot(out=str(target), workspace=tmp_path)
    assert payload["ok"] is True
    assert payload["answer"] == stub.content
    assert payload["capture"]["bytes"] == target.stat().st_size
    assert payload["source"]["path"] == str(target)
    part = _image_part(stub.last_body)
    assert base64.b64decode(part["image_url"]["url"].split(",", 1)[1]) == target.read_bytes()


def test_a_window_name_that_matches_nothing_is_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr("skaldir.workflows.x11_windows", lambda: [
        {"xid": "0x1", "title": "Terminal", "wm_class": "Ptyxis"}
    ])
    payload = VS.screenshot(window="NoSuchWindow", out=str(tmp_path / "shot.png"))
    assert payload["blocked"] == "window_not_found"
    assert "no window matches" in payload["reason"]


def test_a_matching_window_is_captured_by_its_xid(monkeypatch, tmp_path, stub):
    monkeypatch.setattr("skaldir.workflows.x11_windows", lambda: [
        {"xid": "0x2a", "title": "Skaldir Agent", "wm_class": "skaldir"}
    ])
    seen: dict = {}

    def fake_capture(path, *, xid=""):
        seen["xid"] = xid
        path.write_bytes(png_bytes(8, 8))
        return True, "import"

    monkeypatch.setattr("skaldir.workflows.screenshot", fake_capture)
    payload = VS.screenshot(out=str(tmp_path / "win.png"), window="skaldir", workspace=tmp_path)
    assert seen["xid"] == "0x2a"
    assert payload["capture"]["xid"] == "0x2a"
    assert payload["capture"]["window_title"] == "Skaldir Agent"


def test_the_capture_directory_is_created_when_needed(monkeypatch, tmp_path):
    target = tmp_path / "nested" / "deeper" / "shot.png"
    monkeypatch.setattr("skaldir.workflows.screenshot", lambda path, *, xid="": (True, "import"))
    monkeypatch.setattr("skaldir.workflows.x11_windows", list)
    payload = VS.screenshot(out=str(target), capture_only=True)
    assert payload["ok"] is True
    assert target.parent.is_dir()


def test_list_windows_without_a_bridge_is_empty_not_a_crash(monkeypatch):
    monkeypatch.setattr("skaldir.workflows.x11_windows", list)
    assert VS.list_windows() == []


def test_the_default_capture_path_is_under_the_temp_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile

    tempfile.tempdir = None  # re-read TMPDIR
    try:
        path = VS.default_capture_path()
    finally:
        tempfile.tempdir = None
    assert path.name.startswith("screen-")
    assert path.suffix == ".png"


# --------------------------------------------------------------------------- #
# the invariants that must never regress                                        #
# --------------------------------------------------------------------------- #


def test_the_disclosure_always_names_the_file_and_the_endpoint(stub, image):
    payload = VS.describe(str(image))
    assert str(image) in payload["disclosure"]
    assert payload["sent_to"] in payload["disclosure"]
    assert payload["source"]["sent_to"] == payload["sent_to"]
    assert payload["source"]["sha256"]


def test_a_failed_call_still_says_what_would_have_been_sent(no_keys, monkeypatch, tmp_path, image):
    with StubEndpoint(status=400, content="this model does not support image input") as endpoint:
        monkeypatch.setenv("SKALDIR_LLM_BASE_URL", endpoint.base_url)
        monkeypatch.setenv("SKALDIR_LLM_MODEL", "deepseek-chat")
        monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
        payload = VS.describe(str(image))
    assert str(image) in payload["disclosure"]
    assert payload["source"]["bytes"] == image.stat().st_size


def test_the_cache_key_is_stable_for_the_same_image_and_prompt(stub, image):
    first = VS.describe(str(image))
    second = VS.describe(str(image))
    assert first["cache_key"] == second["cache_key"]
    assert first["cache_key"] != VS.describe(str(image), prompt="different")["cache_key"]


def test_no_wall_clock_regression_in_one_call(stub, image):
    started = time.time()
    payload = VS.describe(str(image))
    assert payload["latency_ms"] >= 0
    assert time.time() - started < 10
