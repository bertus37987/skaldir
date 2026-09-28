"""Kontrollierte Re-Probe: eine negative Wahl ist neu messbar (VAL-INPUT-004/006).

Der P0-Defekt dahinter: Der Runner merkte sich eine **negative** Eingabe-Backend-Antwort
(``_input_backend = ""``) für die ganze Lebensdauer des Runners. Ein Daemon, eine Zustimmung
oder ein Display, die danach verfügbar wurden, wurden deshalb nie erneut geprüft — das erste
„hier zustellt nichts" gewann für immer. Architecture §2 „Controlled re-probe" und VAL-INPUT-004
verlangen stattdessen: Cache ``(result, probed_at, fingerprint)``, ``invalidate_input_backend()``
bei Lease, Stop/Generationswechsel und Fingerprintwechsel, ``force=True``, und gebremste Re-Probes.

Diese Tests laufen mit dem **echten** :meth:`HostSessionRunner._delivering_input_backend`-Körper.
Ausgetauscht wird ausschließlich die Prozess-/Daemon-Grenze (``input_backend.probe`` und
``backend_available``), mit einem Aufrufzähler — die geprüfte Methode ist nie der Mock
(VAL-INPUT-006).
"""

from __future__ import annotations

import pytest

from skaldir import input_backend as IB
from skaldir.cu_host_session import HostSessionRunner
from skaldir.input_backend import ProbeResult, ProbeState


@pytest.fixture()
def runner():
    return HostSessionRunner(target="probe", attach_to_session=True,
                             allow_live_desktop=True, display=":0", timeout=5.0)


def _wire(monkeypatch, *, state: ProbeState = ProbeState.INPUT_SENT):
    """Nur die Prozessgrenze ersetzen; JEDEN ``probe()``-Aufruf mitzählen.

    Nur ``xdotool`` gilt als benutzbar, damit ein Messlauf genau ein ``probe()`` auslöst und
    der Zähler sauber ist. ``IB.probe`` und ``IB.backend_available`` beide ausgetauscht; der
    Körpe r von :meth:`_delivering_input_backend` läuft unverändert darunter.
    """
    calls: list[str] = []

    def _probe(name, **kw):
        calls.append(name)
        return ProbeResult(name, state, f"{name}: {state.value} (Grenze gemockt)")

    monkeypatch.setattr(IB, "probe", _probe)
    monkeypatch.setattr(IB, "backend_available", lambda name: (name == "xdotool", "Grenze"))
    return calls


# --------------------------------------------------------------------------- #
# VAL-INPUT-004: ein einmal gemessenes Urteil klebt nicht mehr für immer
# --------------------------------------------------------------------------- #

def test_zwei_aufrufe_probieren_einmal(runner, monkeypatch):
    """Die Entscheidung wird behalten: ein zweiter Aufruf misst NICHT nochmal."""
    calls = _wire(monkeypatch, state=ProbeState.INPUT_SENT)  # eine gemessene negative Antwort
    assert runner._delivering_input_backend() == ""         # erster Aufruf: ein Messlauf
    assert len(calls) == 1
    assert runner._delivering_input_backend() == ""         # zweiter Aufruf: behalten, keine Probe
    assert len(calls) == 1, "zwei Aufrufe, eine Probe — die Antwort wird nicht neu abgeleitet"


def test_ein_zustellendes_backend_wird_gecacht(runner, monkeypatch):
    """Ein wirklich zustellendes Backend bleibt gültig, solange die Welt sich nicht ändert."""
    calls = _wire(monkeypatch, state=ProbeState.INPUT_OBSERVED_AT_TARGET)  # positiv
    assert runner._delivering_input_backend() == "xdotool"
    assert runner._delivering_input_backend() == "xdotool"
    assert len(calls) == 1


def test_invalidierung_erzwingt_eine_neue_messung(runner, monkeypatch):
    """``invalidate_input_backend()`` (neue Lease, Stop, Generationswechsel) → sofort neu messen."""
    calls = _wire(monkeypatch)
    runner._delivering_input_backend()
    assert len(calls) == 1
    runner.invalidate_input_backend()
    assert runner._delivering_input_backend() == ""
    assert len(calls) == 2, "nach invalidate_input_backend() wird sofort neu gemessen"


def test_eine_negative_antwort_wird_nach_der_frist_neu_vermessen(runner, monkeypatch):
    """Eine gealterte negative Antwort verfällt: sie wird neu gemessen, nicht für immer behalten."""
    calls = _wire(monkeypatch)
    assert runner._delivering_input_backend() == ""
    assert len(calls) == 1
    probed_at, fingerprint = runner._input_backend_cache
    # Den Messstempel weit in die Vergangenheit legen — echter Fingerprint, nur altes probed_at:
    runner._input_backend_cache = (probed_at - 9999.0, fingerprint)
    assert runner._delivering_input_backend() == ""
    assert len(calls) == 2, "die negative Antwort verfällt und wird neu gemessen"


def test_die_rate_bremst_eine_alternde_negative(runner, monkeypatch):
    """„Reif" ist nicht „jetzt messbar": Die Rate-Bremse hält die Re-Probe zurück.

    Die Vorgabewerte: eine negative Antwort ist ab ``INPUT_BACKEND_NEGATIVE_TTL`` (4 s) reif,
    aber erst ab ``INPUT_BACKEND_MIN_PROBE_INTERVAL`` (6 s) wirklich neu messbar. Dazwischen
    gilt die alte Antwort weiter — sonst würde ein Tasten-Sturm ein Fenster-Sturm.
    """
    calls = _wire(monkeypatch)
    runner._delivering_input_backend()
    assert len(calls) == 1
    probed_at, fingerprint = runner._input_backend_cache
    # abgelaufen (> TTL 4), aber noch unter der Bremse (< 6): behalten, nicht neu messen
    runner._input_backend_cache = (probed_at - 5.0, fingerprint)
    assert runner._delivering_input_backend() == ""
    assert len(calls) == 1, "unter der Rate-Bremse bleibt die gealterte negative Antwort stehen"
    # über der Bremse: neu messen
    runner._input_backend_cache = (probed_at - 7.0, fingerprint)
    assert runner._delivering_input_backend() == ""
    assert len(calls) == 2, "hinter der Rate-Bremse wird die negative Antwort neu gemessen"


def test_force_probt_sofort_neu(runner, monkeypatch):
    """``force=True`` überschreibt Frist und Bremse: eine frische Messung auf Wunsch."""
    calls = _wire(monkeypatch)
    runner._delivering_input_backend()
    assert len(calls) == 1
    runner._delivering_input_backend(force=True)   # innerhalb der ganzen Fristen
    assert len(calls) == 2
    runner._delivering_input_backend(force=True)
    assert len(calls) == 3


def test_gecachtes_leer_ueberlebt_keinen_fingerprintwechsel(runner, monkeypatch):
    """Ein Zustandswechsel (hier: das Display) verwirft die gecachte negative Antwort sofort."""
    calls = _wire(monkeypatch)
    assert runner._delivering_input_backend() == ""   # an ":0" gemessen, negativ gecacht
    assert len(calls) == 1
    runner.display = ":99"                             # die gemessene Welt hat sich bewegt
    assert runner._delivering_input_backend() == ""    # Fingerprint verändert → sofort neu messen
    assert len(calls) == 2, "ein gecachtes '' wird nach einem Fingerprintwechsel nicht geehrt"


def test_ein_zustellendes_backend_wird_bei_zustandswechsel_neu_vermessen(runner, monkeypatch):
    """Auch eine positive Wahl wird bei einem Zustandswechsel neu vermessen, nicht geehrt."""
    calls = _wire(monkeypatch, state=ProbeState.INPUT_OBSERVED_AT_TARGET)
    assert runner._delivering_input_backend() == "xdotool"
    runner.display = ":99"   # z. B. an eine andere Sitzung gebunden
    assert runner._delivering_input_backend() == "xdotool"
    assert len(calls) == 2, "auch ein positives Urteil wird neu gemessen, wenn die Welt sich bewegt"


# --------------------------------------------------------------------------- #
# Der Fingerprint: Daemon + Portal + Display (der Grund für die Invalidierung)
# --------------------------------------------------------------------------- #

def test_fingerprint_nennt_daemon_portal_display(runner):
    """Der Fingerprint trägt die drei Zustände, die ein Urteil abhängig machen."""
    daemon, portal, display = runner._input_backend_fingerprint()
    assert display == ":0"
    assert isinstance(daemon, tuple), "Daemon-Socket-Zustände (vorhanden/nicht vorhanden)"
    assert str(portal), "der Portal-Weg hat einen gemessenen Zustand (unavailable/needs-consent)"


def test_displaywechsel_bewegt_den_fingerprint(runner):
    """Das Display ist Teil des Fingerprints — ein neues Display ist ein neuer Zustand."""
    before = runner._input_backend_fingerprint()
    runner.display = ":42"
    after = runner._input_backend_fingerprint()
    assert before != after, "ein anderes Display ändert den Fingerprint"


# --------------------------------------------------------------------------- #
# Die Verdrahtung: Lease-Erwerb und Stop/Generationswechsel invalidieren (DESK-02)
# --------------------------------------------------------------------------- #

def test_lease_erwerb_invalidiert_die_mitgebrachte_wahl(runner, monkeypatch, tmp_path):
    """Eine neue Lease ist eine neue Eingabewelt: die mitgebrachte Wahl wird verworfen."""
    from skaldir.act_service import _bind_desktop_fence
    from skaldir.desktop_lease import DesktopLeaseStore

    _wire(monkeypatch)
    runner._delivering_input_backend()          # gemessen, negative Antwort gecacht
    assert runner._input_backend == ""
    store = DesktopLeaseStore(tmp_path / "lease.json")
    lease = store.acquire("run-1")
    assert _bind_desktop_fence(runner, store, "run-1", lease.generation) is True
    assert runner._input_backend is None, (
        "ein neu erworbener Lease verwirft die mitgebrachte Wahl"
    )


def test_stop_und_generationswechsel_invalidieren(runner, monkeypatch, tmp_path):
    """Ein Not-Aus (request_stop) hebt die Generation → die gewählte Wahl fällt, die Sperre hält.

    Der eigentliche Auslöser in ``desktop_lease.py``: ``request_stop`` setzt
    ``stop_requested`` und hebt die Generation. Die DESK-02-Sperre des Runners merkt das am
    nächsten Action-Takt (StaleFence) und verwirft dabei die Backend-Wahl, statt sie über den
    Stopp hinweg zu tragen — die Sperre weist die alte Aktion weiterhin ab.
    """
    from skaldir.act_service import _bind_desktop_fence
    from skaldir.desktop_lease import DesktopLeaseStore, StaleFence

    _wire(monkeypatch)
    runner._delivering_input_backend()
    store = DesktopLeaseStore(tmp_path / "lease.json")
    lease = store.acquire("run-1")
    _bind_desktop_fence(runner, store, "run-1", lease.generation)
    runner._input_backend = ""        # eine Wahl, die in die nächste Aktion getragen würde
    store.request_stop(reason="double-escape")   # hebt die Generation
    with pytest.raises(StaleFence):
        runner._input_fence()
    assert runner._input_backend is None, (
        "ein Stop/Generationswechsel verwirft die gewählte Wahl, damit sie neu gemessen wird"
    )
