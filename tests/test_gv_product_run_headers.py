"""The product-run tool must present the panel credentials the desktop app presents.

Block 1 hardened the panel trust boundary fail-closed: a mutation needs an allowed
Origin *and* a proven caller (SEC-06 — origin alone proved nothing). The runner
predated that hardening and sent neither, so every mutation came back
``403 cross-origin panel mutation rejected`` and the run reported ``null`` fields
without failing. This test pins the wire contract.
"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "gv_product_run", REPO / "tools" / "gv_product_run.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture(mod, monkeypatch, token):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    out = mod.api(1234, "POST", "/completion/goal", {"session_id": "s"}, token=token)
    return out, seen["headers"]


def test_api_sends_allowed_origin_and_session_token(monkeypatch):
    mod = _load_runner()
    out, headers = _capture(mod, monkeypatch, "tok-123")

    assert out == {"ok": True}
    assert headers.get("origin") == mod.PANEL_ORIGIN, "mutation needs an allowed Origin"
    # origin alone is not sufficient since Block 1 (SEC-06): the token rides along
    assert headers.get("x-hermes-session-token") == "tok-123"
    assert headers.get("authorization") == "Bearer tok-123"


def test_api_keeps_the_origin_even_without_a_token(monkeypatch):
    """The caller always identifies itself; the guard decides, not the caller."""
    mod = _load_runner()
    _out, headers = _capture(mod, monkeypatch, "")

    assert headers.get("origin") == mod.PANEL_ORIGIN
    assert "x-hermes-session-token" not in headers
    assert "authorization" not in headers
