"""``skaldir vision …`` — the CLI half: the real product parser and the envelope.

What this file proves:

* the group wires into the **real** ``cli.build_parser()`` tree (idempotently) and
  the four leaves carry the dotted ``command_path`` the contract needs;
* all four ``cli_contract.COMMANDS`` entries are installed by ``register_cli``
  itself (layer, data schema, evidence, ``--json`` on every leaf) — the same
  invariants ``tests/test_cli_contract_dump.py`` enforces once the CLI owner wires
  the group in;
* every verb runs through ``cli_contract.run_command`` and returns the documented
  exit code: 0 ok · 1 usage · 2 not measured (``--no-probe``) · 3 blocked (no
  provider, refused symlink, **no Computer-Use screenshot bridge**) · 4 remote
  endpoint without a key · 5 missing file/window;
* ``--json`` emits exactly one document, and the pre-send disclosure goes to
  stderr (and into the payload) — including on a failed call.

The stub is a real socket server on 127.0.0.1; no credential is read from the
developer's environment (both spellings of every key variable are deleted).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from skaldir import cli, cli_contract
from skaldir import vision_cli as VCLI

# --------------------------------------------------------------------------- #
# the stub endpoint                                                             #
# --------------------------------------------------------------------------- #


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # noqa: ARG002
        return


class Stub:
    """A minimal OpenAI-compatible endpoint (records the request bodies)."""

    def __init__(
        self, *, status: int = 200, content: str = "a red rectangle",
        model: str = "stub-vision-1",
    ):
        self.status = status
        self.content = content
        self.model = model
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args) -> None:  # noqa: A002, ARG002
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else {}
                with stub._lock:
                    stub.requests.append({"path": self.path, "body": body})
                if stub.status >= 400:
                    payload = {"error": {"message": stub.content, "type": "stub"}}
                else:
                    payload = {
                        "model": stub.model,
                        "choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": stub.content}}],
                        "usage": {"prompt_tokens": 90, "completion_tokens": 5, "total_tokens": 95},
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

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False


def png_bytes(width: int = 8, height: int = 6) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        crc = struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + body + crc

    raw = b"".join(b"\x00" + bytes((200, 16, 46)) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


KEY_VARS = (
    "SKALDIR_LLM_API_KEY",
    "GODSV_LLM_API_KEY",
    "OPENAI_API_KEY",
    "SKALDIR_LLM_KEY",
    "GODSV_LLM_KEY",
    "SKALDIR_LLM_VISION_MODEL",
    "GODSV_LLM_VISION_MODEL",
)


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    """No credential, in either spelling — the stub is local and needs none."""
    for name in KEY_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def stub(monkeypatch, tmp_path):
    with Stub() as endpoint:
        monkeypatch.setenv("SKALDIR_LLM_BASE_URL", endpoint.base_url)
        monkeypatch.setenv("SKALDIR_LLM_MODEL", endpoint.model)
        monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent-llm.yaml"))
        yield endpoint


@pytest.fixture
def image(tmp_path) -> Path:
    path = tmp_path / "sign.png"
    path.write_bytes(png_bytes())
    return path


# --------------------------------------------------------------------------- #
# wiring                                                                        #
# --------------------------------------------------------------------------- #


def _subparsers(parser: argparse.ArgumentParser):
    return [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)][0]


def _wired_product_parser() -> argparse.ArgumentParser:
    """The real ``skaldir`` parser with the vision group wired in, as the patch does."""
    parser = cli.build_parser()
    sub = _subparsers(parser)
    if "vision" not in sub.choices:
        VCLI.register_cli(sub)
    return parser


def refusal(envelope: dict) -> dict:
    """The facts of a refused call — on a refusal the envelope's ``data`` is null.

    The contract puts the verdict in ``error`` (code, message, hint, details); the
    details carry this group's ``blocked``/``missing_capability``/``source``.
    """
    error = envelope.get("error") or {}
    return {
        "code": error.get("code"),
        "message": error.get("message", ""),
        "hint": error.get("hint", ""),
        **(error.get("details") or {}),
    }


def run_raw(argv, capsys) -> tuple[int, str, str]:
    """Run one verb through the real parser and the real envelope; (code, out, err)."""
    parser = _wired_product_parser()
    args = parser.parse_args(argv)
    code = cli_contract.run_command(args, args.func)
    out, err = capsys.readouterr()
    return code, out, err


def run(argv, capsys) -> tuple[int, dict, str]:
    """Run one verb and give back the parsed envelope (``{}`` when it is not JSON)."""
    code, out, err = run_raw(argv, capsys)
    text = out.strip()
    envelope = json.loads(text.splitlines()[-1]) if text.startswith("{") else {}
    return code, envelope, err


def big_png(width: int = 620, height: int = 700) -> bytes:
    """A PNG that really exceeds 1 MiB (incompressible pixels, stored deflate)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        crc = struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + body + crc

    import os

    rows = b"".join(b"\x00" + os.urandom(width * 3) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 0))
        + chunk(b"IEND", b"")
    )


def test_the_group_registers_on_a_plain_subparsers_handle():
    parser = argparse.ArgumentParser(prog="x")
    sub = parser.add_subparsers(dest="command")
    assert VCLI.register_cli(sub) is not None
    for verb in ("describe", "ocr", "capabilities", "screenshot"):
        assert parser.parse_args(["vision", verb]).func is not None
    assert parser.parse_args(["vision", "describe", "a.png"]).source == "a.png"


def test_registering_twice_is_a_no_op():
    parser = argparse.ArgumentParser(prog="x")
    sub = parser.add_subparsers(dest="command")
    first = VCLI.register_cli(sub)
    assert VCLI.register_cli(sub) is first


def test_the_group_declares_exactly_four_contract_paths():
    assert set(VCLI.contract_entries()) == {
        "vision.describe",
        "vision.ocr",
        "vision.capabilities",
        "vision.screenshot",
    }


def test_register_cli_installs_the_entries_itself():
    """Nothing has to be pasted into ``cli_contract.COMMANDS`` by hand."""
    VCLI.register_contract_entries()
    for path in VCLI.contract_entries():
        assert path in cli_contract.COMMANDS


def test_the_entries_survive_the_dump_invariants():
    parser = _wired_product_parser()
    inventory = cli_contract.dump(parser)
    leaves = [p for p, _ in cli_contract.leaf_commands(parser) if p.startswith("vision.")]
    assert sorted(leaves) == sorted(VCLI.contract_entries())
    assert inventory["unregistered"] == []
    for record in inventory["commands"]:
        if not record["command"].startswith("vision."):
            continue
        assert record["registered"] is True
        assert record["layer"] in set(cli_contract.LAYERS) | {"kernel"}
        assert record["has_json_flag"] is True
        assert record["data_schema"]["type"] == "object"
        assert record["default_evidence"], record["command"]
        assert record["exit_codes"]
        assert record["args"]


def test_the_command_paths_are_the_dotted_ones_the_contract_uses():
    parser = _wired_product_parser()
    for verb in ("describe", "ocr", "capabilities", "screenshot"):
        args = parser.parse_args(["vision", verb])
        assert args.command_path == f"vision.{verb}"


def test_main_without_a_verb_or_with_help_is_usage(capsys):
    assert VCLI.main([]) == VCLI.EXIT_USAGE
    assert VCLI.main(["--help"]) == VCLI.EXIT_OK


def test_the_standalone_parser_has_the_same_four_verbs():
    parser = VCLI.build_parser()
    for verb in ("describe", "ocr", "capabilities", "screenshot"):
        assert parser.parse_args([verb]).func is not None


# --------------------------------------------------------------------------- #
# usage + refusals that need no provider                                        #
# --------------------------------------------------------------------------- #


def test_a_missing_source_argument_is_a_usage_error(capsys):
    code, envelope, _ = run(["vision", "describe", "--json"], capsys)
    assert code == VCLI.EXIT_USAGE
    assert envelope["exit_code"] == VCLI.EXIT_USAGE


def test_a_missing_file_is_not_found(capsys, tmp_path):
    missing = tmp_path / "nope.png"
    code, envelope, _ = run(["vision", "describe", str(missing), "--json"], capsys)
    assert code == VCLI.EXIT_NOT_FOUND
    assert "does not exist" in json.dumps(envelope)


def test_a_symlink_outside_the_workspace_is_blocked_and_named(capsys, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "secret.png"
    outside.write_bytes(png_bytes())
    link = workspace / "escape.png"
    link.symlink_to(outside)
    code, envelope, _ = run(
        ["vision", "describe", str(link), "--workspace", str(workspace), "--json"], capsys
    )
    assert code == VCLI.EXIT_BLOCKED
    message = json.dumps(envelope)
    assert "refusing the symlink" in message
    assert str(outside) in message


def test_a_text_file_named_png_is_blocked_before_the_request(capsys, tmp_path, stub):
    path = tmp_path / "notes.png"
    path.write_text("not an image\n", encoding="utf-8")
    code, envelope, _ = run(["vision", "describe", str(path), "--json"], capsys)
    assert code == VCLI.EXIT_BLOCKED
    assert "no known image signature" in refusal(envelope)["message"]
    assert refusal(envelope)["blocked"] == "image_guard"
    assert stub.requests == []


def test_an_oversize_image_is_blocked_with_the_cap_in_the_message(capsys, tmp_path, stub):
    path = tmp_path / "big.png"
    path.write_bytes(big_png())
    assert path.stat().st_size > 1024 * 1024
    code, envelope, _ = run(
        ["vision", "describe", str(path), "--max-image-mb", "1", "--json"], capsys
    )
    assert code == VCLI.EXIT_BLOCKED
    assert "image cap" in refusal(envelope)["message"]
    assert refusal(envelope)["blocked"] == "image_guard"
    assert stub.requests == []


def test_a_remote_endpoint_without_a_key_exits_four(capsys, monkeypatch, image, tmp_path):
    monkeypatch.setenv("SKALDIR_LLM_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setenv("SKALDIR_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
    code, envelope, _ = run(["vision", "describe", str(image), "--json"], capsys)
    assert code == VCLI.EXIT_NEEDS_CREDENTIALS
    assert "no request was sent" in refusal(envelope)["message"]


def test_the_model_refusing_the_image_is_blocked_not_a_crash(capsys, monkeypatch, tmp_path, image):
    with Stub(status=400, content="this model does not support image input") as endpoint:
        monkeypatch.setenv("SKALDIR_LLM_BASE_URL", endpoint.base_url)
        monkeypatch.setenv("SKALDIR_LLM_MODEL", "deepseek-chat")
        monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
        code, envelope, _ = run(["vision", "describe", str(image), "--json"], capsys)
    assert code == VCLI.EXIT_BLOCKED
    bad = refusal(envelope)
    assert bad["blocked"] == "model_rejects_images"
    assert "does not accept images" in bad["message"]
    assert "SKALDIR_LLM_VISION_MODEL" in bad["hint"]


# --------------------------------------------------------------------------- #
# describe / ocr through the envelope                                           #
# --------------------------------------------------------------------------- #


def test_describe_round_trips_through_the_envelope(capsys, stub, image):
    code, envelope, err = run(["vision", "describe", str(image), "--json"], capsys)
    assert code == VCLI.EXIT_OK
    assert envelope["command"] == "vision.describe"
    data = envelope["data"]
    assert data["ok"] is True
    assert data["answer"] == stub.content
    assert data["sent_to"] == f"{stub.base_url}/chat/completions"
    assert str(image) in data["disclosure"]
    assert data["source"]["sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert envelope["evidence"]
    # the disclosure reached stderr *before* the answer was printed
    assert "sending the bytes of" in err
    assert str(image) in err


def test_the_request_carries_the_file_bytes_and_the_text_prompt(capsys, stub, image):
    run(["vision", "describe", str(image), "--json"], capsys)
    body = stub.requests[-1]["body"]
    parts = body["messages"][0]["content"]
    assert [p["type"] for p in parts] == ["text", "image_url"]
    assert parts[0]["text"].startswith("Describe this image")
    sent = base64.b64decode(parts[1]["image_url"]["url"].split(",", 1)[1])
    assert sent == image.read_bytes()


def test_the_prompt_can_be_overridden(capsys, stub, image):
    run(["vision", "describe", str(image), "--prompt", "How many letters?", "--json"], capsys)
    body = stub.requests[-1]["body"]
    assert body["messages"][0]["content"][0]["text"] == "How many letters?"


def test_ocr_asks_for_the_text_and_echoes_it_as_text(capsys, stub, image):
    code, envelope, _ = run(["vision", "ocr", str(image), "--json"], capsys)
    assert code == VCLI.EXIT_OK
    body = stub.requests[-1]["body"]
    assert body["messages"][0]["content"][0]["text"].startswith("Read the text in this image")
    data = envelope["data"]
    assert data["verb"] == "ocr"
    assert data["text"] == data["answer"] == stub.content


def test_a_remote_url_is_sent_as_a_url(capsys, stub):
    code, envelope, _ = run(["vision", "describe", "https://images.test/a.png", "--json"], capsys)
    assert code == VCLI.EXIT_OK
    body = stub.requests[-1]["body"]
    assert body["messages"][0]["content"][1]["image_url"]["url"] == "https://images.test/a.png"
    assert envelope["data"]["source"]["kind"] == "url"


def test_no_remote_refuses_a_url(capsys, stub):
    code, envelope, _ = run(
        ["vision", "describe", "https://images.test/a.png", "--no-remote", "--json"], capsys
    )
    assert code == VCLI.EXIT_BLOCKED
    assert "--no-remote" in refusal(envelope)["message"]
    assert stub.requests == []


def test_json_mode_writes_exactly_one_document(capsys, stub, image):
    code, envelope, err = run(["vision", "describe", str(image), "--json"], capsys)
    assert code == VCLI.EXIT_OK
    assert "sending the bytes of" in err  # the human copy is on stderr, not stdout
    assert envelope["schema_version"]
    assert "base64," not in json.dumps(envelope)


def test_text_mode_prints_the_answer_and_the_cost(capsys, stub, image):
    code, out, err = run_raw(["vision", "describe", str(image), "--text"], capsys)
    assert code == VCLI.EXIT_OK
    assert stub.content in out
    assert "cost:" in out
    assert "tokens" in out
    assert "sending the bytes of" in err


def test_quiet_silences_the_disclosure_on_stderr_but_keeps_it_in_the_payload(
    capsys, stub, image
):
    code, envelope, err = run(["vision", "describe", str(image), "--json", "--quiet"], capsys)
    assert code == VCLI.EXIT_OK
    assert err.strip() == ""
    assert str(image) in envelope["data"]["disclosure"]


def test_a_failed_call_still_reports_what_would_have_been_sent(
    capsys, monkeypatch, tmp_path, image
):
    with Stub(status=400, content="this model does not support image input") as endpoint:
        monkeypatch.setenv("SKALDIR_LLM_BASE_URL", endpoint.base_url)
        monkeypatch.setenv("SKALDIR_LLM_MODEL", "deepseek-chat")
        monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
        code, envelope, err = run(["vision", "describe", str(image), "--json"], capsys)
    assert code == VCLI.EXIT_BLOCKED
    bad = refusal(envelope)
    assert str(image) in bad["disclosure"]
    assert bad["blocked"] == "model_rejects_images"
    assert "sending the bytes of" in err  # named before the bytes moved
    assert str(image) in err


def test_an_empty_answer_exits_two_and_never_prints_an_empty_description(
    capsys, monkeypatch, tmp_path, image
):
    with Stub(status=200, content="") as endpoint:
        monkeypatch.setenv("SKALDIR_LLM_BASE_URL", endpoint.base_url)
        monkeypatch.setenv("SKALDIR_LLM_MODEL", endpoint.model)
        monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
        code, envelope, err = run(["vision", "describe", str(image), "--json"], capsys)
    assert code == VCLI.EXIT_VERIFICATION_FAILED
    bad = refusal(envelope)
    assert bad["blocked"] == "empty_answer"
    assert "no text" in bad["message"]
    assert str(image) in err  # it was sent, and that is said out loud


# --------------------------------------------------------------------------- #
# capabilities                                                                  #
# --------------------------------------------------------------------------- #


def test_capabilities_measures_yes_and_exits_zero(capsys, stub):
    code, envelope, _ = run(["vision", "capabilities", "--json"], capsys)
    assert code == VCLI.EXIT_OK
    data = envelope["data"]
    assert data["accepts_images"] is True
    assert data["measured"] is True
    assert data["probe"]["image"]["bytes"] == 70  # the inlined 1x1 PNG
    assert stub.requests, "the probe must have sent a request"
    part = stub.requests[-1]["body"]["messages"][0]["content"][1]
    assert part["type"] == "image_url"


def test_capabilities_measures_no_and_still_exits_zero(capsys, monkeypatch, tmp_path):
    with Stub(status=400, content="this model does not support image input") as endpoint:
        monkeypatch.setenv("SKALDIR_LLM_BASE_URL", endpoint.base_url)
        monkeypatch.setenv("SKALDIR_LLM_MODEL", "deepseek-chat")
        monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
        code, envelope, _ = run(["vision", "capabilities", "--json"], capsys)
    assert code == VCLI.EXIT_OK  # a measured "no" is the answer
    data = envelope["data"]
    assert data["accepts_images"] is False
    assert data["measured"] is True
    assert data["agreement"] == "catalog_agrees"
    assert envelope["warnings"]


def test_no_probe_is_verification_failed_because_nothing_was_measured(capsys, stub):
    code, envelope, _ = run(["vision", "capabilities", "--no-probe", "--json"], capsys)
    assert code == VCLI.EXIT_VERIFICATION_FAILED
    data = envelope["data"]
    assert data["measured"] is False
    assert data["accepts_images"] is None
    assert "not a measurement" in data["reason"]
    assert stub.requests == []


def test_capabilities_without_a_provider_is_blocked_and_names_the_variables(
    capsys, monkeypatch, tmp_path
):
    for name in ("SKALDIR_LLM_BASE_URL", "GODSV_LLM_BASE_URL", "OPENAI_BASE_URL",
                 "SKALDIR_LLM_MODEL", "GODSV_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SKALDIR_LLM_CONFIG", str(tmp_path / "absent.yaml"))
    code, envelope, _ = run(["vision", "capabilities", "--json"], capsys)
    assert code == VCLI.EXIT_BLOCKED
    assert "SKALDIR_LLM_BASE_URL" in refusal(envelope)["message"]


# --------------------------------------------------------------------------- #
# screenshot — the Computer-Use bridge                                          #
# --------------------------------------------------------------------------- #


def test_screenshot_without_a_bridge_exits_three_and_names_the_capability(
    capsys, monkeypatch, tmp_path, stub
):
    monkeypatch.setattr(
        "skaldir.workflows.screenshot",
        lambda path, *, xid="": (False, "import failed: missing an image filename"),
    )
    out = tmp_path / "shot.png"
    code, envelope, _ = run(["vision", "screenshot", "--out", str(out), "--json"], capsys)
    assert code == VCLI.EXIT_BLOCKED
    bad = refusal(envelope)
    assert bad["blocked"] == "no_cu_bridge"
    assert bad["missing_capability"]
    assert "import failed" in bad["message"]
    assert stub.requests == []  # nothing was sent


def test_the_real_cu_layer_on_this_host_is_probed_and_never_faked(
    capsys, monkeypatch, tmp_path, stub
):
    """Either it captured and described (0) or it named what is missing (3)."""
    monkeypatch.setattr("skaldir.workflows.x11_windows", list)
    out = tmp_path / "real.png"
    code, envelope, _ = run(
        ["vision", "screenshot", "--out", str(out), "--capture-only", "--json"], capsys
    )
    assert code in (VCLI.EXIT_OK, VCLI.EXIT_BLOCKED), json.dumps(envelope)[:600]
    if code == VCLI.EXIT_BLOCKED:
        assert refusal(envelope)["missing_capability"]
        assert not out.exists()
    else:
        assert out.exists() and out.stat().st_size > 0


def test_capture_only_sends_nothing_but_still_captures(capsys, monkeypatch, tmp_path, stub):
    out = tmp_path / "shot.png"
    out.write_bytes(png_bytes(12, 9))

    def capture(path, *, xid=""):
        path.write_bytes(png_bytes(12, 9))
        return True, "import"

    monkeypatch.setattr("skaldir.workflows.screenshot", capture)
    code, envelope, _ = run(
        ["vision", "screenshot", "--out", str(out), "--capture-only", "--json"], capsys
    )
    assert code == VCLI.EXIT_OK
    data = envelope["data"]
    assert data["describe"]["skipped"] == "capture-only"
    assert data["disclosure"].startswith("nothing was sent")
    assert stub.requests == []
    assert data["capture"]["bytes"] == out.stat().st_size


def test_screenshot_describes_the_capture(capsys, monkeypatch, tmp_path, stub):
    out = tmp_path / "shot.png"

    def capture(path, *, xid=""):
        path.write_bytes(png_bytes(12, 9))
        return True, "import"

    monkeypatch.setattr("skaldir.workflows.screenshot", capture)
    code, envelope, _ = run(
        ["vision", "screenshot", "--out", str(out), "--workspace", str(tmp_path), "--json"],
        capsys,
    )
    assert code == VCLI.EXIT_OK
    data = envelope["data"]
    assert data["answer"] == stub.content
    assert data["capture"]["path"] == str(out)
    assert data["source"]["path"] == str(out)
    part = stub.requests[-1]["body"]["messages"][0]["content"][1]
    assert base64.b64decode(part["image_url"]["url"].split(",", 1)[1]) == out.read_bytes()


def test_a_window_name_that_matches_nothing_is_not_found(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr("skaldir.workflows.x11_windows", lambda: [
        {"xid": "0x1", "title": "Terminal", "wm_class": "Ptyxis"}
    ])
    code, envelope, _ = run(
        ["vision", "screenshot", "--window", "NoSuchApp", "--out", str(tmp_path / "x.png"),
         "--json"],
        capsys,
    )
    assert code == VCLI.EXIT_NOT_FOUND
    assert "no window matches" in refusal(envelope)["message"]


def test_list_windows_reports_the_inventory_without_capturing(capsys, monkeypatch, stub):
    monkeypatch.setattr("skaldir.workflows.x11_windows", lambda: [
        {"xid": "0x2a", "title": "Skaldir Agent", "wm_class": "skaldir"}
    ])
    code, envelope, _ = run(["vision", "screenshot", "--list-windows", "--json"], capsys)
    assert code == VCLI.EXIT_OK
    data = envelope["data"]
    assert data["windows"][0]["xid"] == "0x2a"
    assert data["windows_seen"] == 1
    assert stub.requests == []


def test_list_windows_with_an_empty_inventory_says_so(capsys, monkeypatch):
    monkeypatch.setattr("skaldir.workflows.x11_windows", list)
    code, envelope, _ = run(["vision", "screenshot", "--list-windows", "--json"], capsys)
    assert code == VCLI.EXIT_OK
    assert envelope["data"]["windows"] == []
    assert "inventory is empty" in envelope["data"]["warning"]


# --------------------------------------------------------------------------- #
# rendering                                                                     #
# --------------------------------------------------------------------------- #


def test_render_text_says_yes_no_or_unknown_for_capabilities():
    base = {"ok": True, "verb": "capabilities", "model": "m", "endpoint": "e",
            "catalog": {"verdict": "unknown", "reason": "r"}, "agreement": "x",
            "probe": {"attempted": True, "image": {"bytes": 70}, "status": 200,
                      "accepts_images": True, "answer": "ok", "detail": ""},
            "cost": {}}
    assert "accepts images: yes" in VCLI.render_text({**base, "accepts_images": True,
                                                     "measured": True})
    assert "accepts images: no" in VCLI.render_text({**base, "accepts_images": False,
                                                    "measured": True})
    assert "accepts images: unknown" in VCLI.render_text({**base, "accepts_images": None,
                                                         "measured": False})


def test_render_text_of_a_refusal_names_the_reason():
    text = VCLI.render_text({"ok": False, "verb": "screenshot", "blocked": "no_cu_bridge",
                             "reason": "the Computer-Use layer has no screenshot backend"})
    assert "no_cu_bridge" in text
    assert "Computer-Use" in text


def test_the_module_runner_emits_the_product_envelope(capsys, stub, image):
    """``python -m skaldir.vision_cli`` goes through the shared envelope, like the product."""
    code = VCLI.main(["describe", str(image), "--json"])
    out = capsys.readouterr().out.strip()
    envelope = json.loads(out)
    assert code == VCLI.EXIT_OK
    assert envelope["command"] == "vision.describe"
    assert envelope["data"]["answer"] == stub.content


def test_the_module_runner_without_a_verb_is_usage_and_help_is_free(capsys):
    assert VCLI.main([]) == VCLI.EXIT_USAGE
    assert "describe" in capsys.readouterr().err  # argparse's own refusal
    assert VCLI.main(["--help"]) == VCLI.EXIT_OK
    assert "describe" in capsys.readouterr().out


def test_list_windows_text_mode_lists_them(capsys, monkeypatch):
    monkeypatch.setattr("skaldir.workflows.x11_windows", lambda: [
        {"xid": "0x2a", "title": "Skaldir Agent", "wm_class": "skaldir"}
    ])
    code, out, _ = run_raw(["vision", "screenshot", "--list-windows", "--text"], capsys)
    assert code == VCLI.EXIT_OK
    assert "0x2a" in out
    assert "Skaldir Agent" in out
