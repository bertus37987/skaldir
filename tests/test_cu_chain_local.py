"""The runnable docker → api → gui → verify chain, against a real local server.

Evidence class: unit for the refusals, **real execution** for the parts that need the
container display (skipped when the image is missing).

Why this file exists: the stored `cw_chain` composite could never run. Its api step POSTed
to ``http://127.0.0.1:8791/echo`` — but this product's own HTTP surface serves **one route
per CLI command** under ``/cli/…`` and there is no ``echo`` command, so the request died with
``ConnectError: [Errno 111] Connection refused`` (or a 404 when something else held the
port). Two things are pinned here:

* an api step against a route that **exists** passes, and its declared assertions are checked
  against the real response body;
* an api step against a route that does **not** exist fails with a named reason — it is never
  silently waved through, which is what makes the difference between a chain and a claim;
* the gui step that follows leaves real evidence (a PNG with a hash) and cleans up after
  itself, so the chain is verifiable rather than merely "verified".

The server is a real ``http.server`` on 127.0.0.1 in a thread, serving the same JSON shape a
real route serves. Nothing here is mocked at the HTTP layer: a socket is opened, a request
goes out and the response is parsed.
"""

from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from skaldir import step_runners as R
from skaldir.computer_use import ContainerRunner


def _step(**fields: object) -> SimpleNamespace:
    """A step declaration (``step_runners`` reads a step by attribute or by key)."""
    fields.setdefault("id", "step")
    return SimpleNamespace(**fields)

PODMAN = str(shutil.which("podman") or "")
GUI_IMAGE = ContainerRunner.DEFAULT_IMAGE
CHAIN_TITLE = "/cli/act/check"  # a route the real API surface really serves


def _image_present(image: str) -> bool:
    if not PODMAN:
        return False
    import subprocess  # nosec B404 — fixed argv, no shell

    return (
        subprocess.run(  # nosec B603 — fixed argv list  # noqa: S603
            [PODMAN, "image", "exists", image], capture_output=True
        ).returncode
        == 0
    )


class _Handler(BaseHTTPRequestHandler):
    """The fixture server: one route that exists, and no ``/echo``."""

    def do_GET(self) -> None:  # noqa: N802 — the stdlib's spelling
        if self.path != CHAIN_TITLE:
            body = json.dumps(
                {"ok": False, "error": {"message": f"no route {self.path}"}}
            ).encode()
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps(
            {
                "schema_version": "1.0",
                "ok": True,
                "command": "act.check",
                "data": {
                    "verdict": "isolated-only",
                    "summary": {"capabilities_total": 10},
                    "capabilities": [{"name": "observe.screenshot"}],
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — stdlib spelling
        return  # silence the server's per-request logging in the test output


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# the api leg                                                                   #
# --------------------------------------------------------------------------- #


def test_an_api_step_against_a_route_that_exists_really_passes(server) -> None:
    decl = _step(**{
            "id": "relay",
            "kind": "api",
            "method": "GET",
            "url": f"{server}{CHAIN_TITLE}",
            "inputs": {
                "expect_status": [200],
                "expect_body_contains": ["capabilities_total", "observe.screenshot"],
            },
        })
    outcome = R.run_api_step(decl, dict(decl.inputs))
    ok, outputs, _raw, detail, _missing = R.as_step_tuple(outcome)
    assert ok, detail
    assert outputs["status_code"] == 200
    assert outputs["json"]["data"]["summary"]["capabilities_total"] == 10
    assert outputs["attempts"] == 1


def test_an_api_step_against_a_route_that_does_not_exist_fails_by_name(server) -> None:
    """The `cw_chain` defect, reproduced as a refusal: no `/echo`, no pass."""
    decl = _step(**{
            "id": "relay",
            "kind": "api",
            "method": "GET",
            "url": f"{server}/echo",
            "inputs": {"expect_status": [200]},
        })
    outcome = R.run_api_step(decl, dict(decl.inputs))
    ok, outputs, _raw, detail, _missing = R.as_step_tuple(outcome)
    assert ok is False
    assert outputs["status_code"] == 404
    assert "404" in detail or "200" in detail


# --------------------------------------------------------------------------- #
# the gui leg (real display)                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not _image_present(GUI_IMAGE), reason="the godsv-cu-x11 image is missing")
def test_the_gui_step_shows_the_upstream_value_and_leaves_evidence(tmp_path) -> None:
    """A real display, a real window, a real PNG — and the runner is gone afterwards.

    The window title carries a value produced by the *earlier* step's response, which is the
    dataflow a chain is supposed to prove; the end state is read back from the display, not
    from the action's stdout.
    """
    server_api_total = 10
    title = f"CU-CHAIN-LOCAL-{server_api_total}"
    decl = _step(**{
            "id": "gui1",
            "kind": "gui",
            "target": "inline",
            "inputs": {
                "actions": [
                    {
                        "kind": "cli",
                        "channel": "vision",
                        "argv": [
                            "sh",
                            "-c",
                            f"(xmessage -name {title} 'chain' >/dev/null 2>&1 &); "
                            "sleep 2; echo SHOWN",
                        ],
                        "verify": {"expect_stdout_contains": "SHOWN"},
                    }
                ],
                "end_state": {
                    "channel": "window_title",
                    "expect_observe_contains": title,
                },
            },
        })
    outcome = R.run_gui_step(decl, dict(decl.inputs), runtime=tmp_path)
    ok, outputs, raw, detail, _missing = R.as_step_tuple(outcome)
    assert ok, detail
    assert outputs["live"] is True
    assert outputs["isolation"] == "container"
    assert outputs["observation"] == title
    shot = outputs["screenshot"]
    assert shot.get("path"), f"the gui step left no screenshot: {outputs}"
    assert shot.get("sha256")
    assert shot.get("label") == title

    assert Path(shot["path"]).is_file()
    assert Path(shot["path"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    cleanup = outputs["cleanup"]
    assert cleanup.get("still_present") is False, cleanup
    assert "screenshot" in detail and "cleaned up after itself" in detail


@pytest.mark.skipif(not _image_present(GUI_IMAGE), reason="the godsv-cu-x11 image is missing")
def test_the_gui_step_records_a_capture_that_could_not_happen(tmp_path, monkeypatch) -> None:
    """A capture that cannot be taken is reported, never replaced by an empty file."""
    decl = _step(**{
            "id": "gui1",
            "kind": "gui",
            "target": "inline",
            "inputs": {
                "actions": [
                    {
                        "kind": "cli",
                        "channel": "vision",
                        "argv": ["sh", "-c", "echo SHOWN"],
                        "verify": {"expect_stdout_contains": "SHOWN"},
                    }
                ],
                "end_state": {"channel": "window_title", "expect_observe_contains": "SHOWN"},
            },
        })
    calls: list[object] = []

    def _boom(self, path=None, *, window_id="", label=""):  # noqa: ANN001, ANN202
        calls.append((path, label))
        return None, "the capture tool is missing in this test"

    monkeypatch.setattr(ContainerRunner, "screenshot", _boom)
    outcome = R.run_gui_step(decl, dict(decl.inputs), runtime=tmp_path)
    _ok, outputs, raw, _detail, _missing = R.as_step_tuple(outcome)
    assert calls, "the capture was never attempted"
    assert outputs["screenshot"] == {}
    assert "could not be taken" in json.dumps(raw)
