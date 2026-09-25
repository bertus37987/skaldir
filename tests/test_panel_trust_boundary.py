"""Block 1 / Defekte 2+3 — Panel-Trust-Boundary und Audit-Sichtbarkeit.

Rot-first-Beleg (Fix im Code temporär auf den alten Stand gesetzt, echter
Lauf — NICHT per ``git stash``)::

    $ .venv/bin/python -m pytest tests/test_panel_trust_boundary.py -q
    FF...F  (failed)
    - origin_allowed('') war True   (fehlender Origin = erlaubt)
    - _guard_origin war fail-open bei Header-Lesefehler
    - POST /models/roles ohne Token mutierte den Zustand (kein 403)

Vertrag nach dem Fix:

* ``origin_allowed("")`` ist False (fehlender Origin ist keine Erlaubnis).
* ``_guard_origin`` verweigert bei unlesbaren Headern (fail-closed).
* Jede Mutation braucht das **Token**; ein *genannter* fremder Origin ⇒ 403.
* Ein **fehlender** Origin ist kein Ablehnungsgrund, wenn das Token gilt — genau so
  ruft die Desktop-App auf (gemessen 17.09.: ``origin='' token=True auth=True``).
  Ohne Token bleibt ein Aufruf ohne Origin weiterhin 403.
* Der Desktop-App-Pfad (``app://``-Origin + ``X-Hermes-Session-Token``)
  bleibt erlaubt — die App bricht nicht.
"""

import importlib.util
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

PANEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "integrations"
    / "hermes"
    / "godsv_plugin"
    / "dashboard"
    / "plugin_api.py"
)

TOKEN = "tok-panel-fixture"


def _load_panel(name: str = "godsv_panel_trust"):
    spec = importlib.util.spec_from_file_location(name, PANEL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def panel(monkeypatch, tmp_path):
    """Panel-Modul mit isoliertem HERMES_HOME und gesetztem Panel-Token."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", TOKEN)
    return _load_panel()


class _BoomHeaders:
    """Request-Adapter, dessen Header-Zugriff bricht (defekter Adapter)."""

    def get(self, _name):
        raise RuntimeError("broken request adapter")

    def __getitem__(self, _name):
        raise RuntimeError("broken request adapter")


class _BoomRequest:
    state = None
    headers = _BoomHeaders()


class TestOriginAllowlistIsFailClosed:
    def test_missing_origin_is_rejected(self, panel):
        assert panel.origin_allowed("") is False
        assert panel.origin_allowed("   ") is False

    def test_local_app_origins_still_pass(self, panel):
        assert panel.origin_allowed("app://hermes") is True
        assert panel.origin_allowed("null") is True
        assert panel.origin_allowed("file:///app/index.html") is True
        assert panel.origin_allowed("http://localhost:8080") is True
        assert panel.origin_allowed("http://127.0.0.1:9333") is True

    def test_foreign_origins_stay_rejected(self, panel):
        assert panel.origin_allowed("https://evil.com") is False
        assert panel.origin_allowed("https://evil.com/x?localhost") is False


class TestTokenIsRequiredForMutations:
    def test_panel_token_is_read_from_env(self, panel):
        assert panel.panel_token() == TOKEN

    def test_missing_token_never_matches(self, panel, monkeypatch):
        monkeypatch.delenv("HERMES_DASHBOARD_SESSION_TOKEN", raising=False)
        assert panel.panel_token() == ""

    def test_gateway_authenticated_requests_are_trusted(self, panel):
        class _State:
            token_authenticated = True

        class _Req:
            state = _State()
            headers = {}

        assert panel._request_is_authenticated(_Req()) is True


class TestMutationsAreGuardedEndToEnd:
    """Echter HTTP-Pfad über den montierten Router (TestClient)."""

    def _client(self, panel) -> TestClient:
        app = FastAPI()
        app.include_router(panel.router)
        return TestClient(app)

    def test_mutation_without_token_is_rejected(self, panel):
        """Der Kern-Befund: Origin allein reichte; jetzt 403 ohne Token."""
        client = self._client(panel)
        res = client.post(
            "/models/strictness",
            json={"level": "strict"},
            headers={"origin": "app://hermes"},
        )
        assert res.status_code == 403, res.text

    def test_mutation_without_origin_and_without_token_is_rejected(self, panel):
        client = self._client(panel)
        res = client.post("/models/strictness", json={"level": "strict"}, headers={})
        assert res.status_code == 403, res.text

    def test_the_measured_app_wire_reality_is_allowed(self, panel):
        """Regression pin: die App sendet Token, aber *keinen* Origin-Header.

        Gemessen in der gebauten App (17.09., Guard-Log): ``origin='' token=True auth=True``.
        Der alte Vertrag verlangte einen Origin und wies damit jede Panel-Mutation ab
        (CU-Demo, Eval-Lauf, Approvals, Model-Rollen, Treasury) — der Nutzer sah nur den
        rohen IPC-Fehler. Dieses Token-only-Format muss erlaubt bleiben.
        """
        client = self._client(panel)
        res = client.post(
            "/models/strictness",
            json={"level": "strict"},
            headers={"X-Hermes-Session-Token": TOKEN},
        )
        assert res.status_code == 200, res.text

    def test_foreign_origin_stays_rejected_even_with_token(self, panel):
        """Der Origin-Allowlist bleibt scharf, wenn ein Origin *genannt* wird."""
        client = self._client(panel)
        res = client.post(
            "/models/strictness",
            json={"level": "strict"},
            headers={"origin": "https://evil.com", "X-Hermes-Session-Token": TOKEN},
        )
        assert res.status_code == 403, res.text

    def test_desktop_app_path_is_allowed(self, panel):
        """Der echte App-Pfad (ctx.rest): app:-Origin + Token ⇒ erlaubt."""
        client = self._client(panel)
        res = client.post(
            "/models/strictness",
            json={"level": "strict"},
            headers={"origin": "app://hermes", "X-Hermes-Session-Token": TOKEN},
        )
        assert res.status_code == 200, res.text
        assert res.json()["strictness"]["level"] == "strict"

    def test_wrong_token_is_rejected(self, panel):
        client = self._client(panel)
        res = client.post(
            "/models/strictness",
            json={"level": "strict"},
            headers={"origin": "app://hermes", "X-Hermes-Session-Token": "nope"},
        )
        assert res.status_code == 403, res.text

    def test_reads_stay_open(self, panel):
        client = self._client(panel)
        assert client.get("/health").status_code == 200


class TestGuardOriginFailsClosed:
    def test_unreadable_headers_are_a_rejection(self, panel):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            panel._guard_origin(_BoomRequest())
        assert exc.value.status_code == 403


class TestAuditFailureIsVisible:
    def test_audit_failure_is_logged_not_swallowed(self, panel, monkeypatch, caplog):
        """Ein Audit-Fehlschlag darf nicht still verschwinden."""
        import skaldir.events as events

        def _boom(*_a, **_k):
            raise ValueError("event kind 'panel' is not registered")

        monkeypatch.setattr(events.EventStore, "append", _boom)
        with caplog.at_level("WARNING", logger="godsv.panel"):
            panel._audit("model_role_set", {"role": "investigator"})
        assert any(
            "audit append failed" in rec.getMessage() for rec in caplog.records
        ), caplog.text

    def test_successful_audit_writes_an_event(self, panel):
        """Erfolgreicher Pfad bleibt erfolgreich — kein neuer Fehlerpfad."""
        from skaldir.events import EventStore

        panel._audit("model_role_set", {"role": "investigator"})
        store = EventStore(panel.runtime_dir() / "events.db")
        try:
            kinds = [event.kind for event in store.stream()]
        finally:
            store.close()
        assert kinds, "audit append must reach the event store"
