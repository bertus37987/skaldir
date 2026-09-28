"""Backend-Wahl für Tastatureingaben — entschieden durch Zustellung, nicht durch rc.

Der Host-Befund dahinter: fünf Wege (`xdotool key` über Fokus, `xdotool key --window`,
`xdotool type`, XTEST per Xlib, `ydotool key`) liefern alle rc=0 und **keines** ein
Key-Event am Zielfenster, obwohl X-Fokus, WM_STATE und Aktivierung korrekt sind. Ein
rc=0 ist deshalb hier nirgends ein Beweis.

Diese Tests halten fest:
1. ein Backend gilt nur bei zugestelltem Event (`delivers`),
2. die Reihenfolge bevorzugt uinput (`ydotool`) vor XTEST (`xdotool`), und der offizielle
   Weg steht danach,
3. die sechs Zustände sind unterscheidbar, und keiner darf für einen anderen gelten,
4. `none` wird ehrlich als blockiert gemeldet, nicht als Erfolg,
5. ein Socket, der existiert, aber keine Verbindung annimmt, ist kein erreichbarer Daemon.

Ersetzt wurden die Tests, die den alten Fehler **festgeschrieben** haben: derselbe Stil, der
„irgendeine Zeile mehr im Protokoll = Zustellung" akzeptierte, und die argv, die `ydotool key
38:1` verlangte (38 ist der X-Code für 'a', nicht der evdev-Code — siehe
`tests/test_input_probe_bounded.py` für die Ersatzfeststellungen).
"""

from __future__ import annotations

import socket
import time
from pathlib import Path

import pytest

from skaldir import input_backend as IB
from skaldir.input_backend import ProbeResult, ProbeState
from skaldir.probe_target import DeliveryRecord, ProbeBinding, ProbeWindow


@pytest.fixture()
def own_log(tmp_path):
    """Ein eigenes Protokoll pro Probe, nicht geteilt — der Grund für die Marker-Korrelation."""
    return tmp_path / f"probe-{IB.new_marker()}.jsonl"


def _found(state: ProbeState, backend: str = "xdotool", detail: str = "") -> ProbeResult:
    return ProbeResult(backend, state, detail or state.value)


# --------------------------------------------------------------------------- #
# Voraussetzungen
# --------------------------------------------------------------------------- #

def test_die_reihenfolge_ist_technologisch_begruendet():
    """Die Reihenfolge ist technologisch begründet, keine Vorliebe.

    uinput spricht den Kernel; ein Compositor, der XTEST-Events für Xwayland-Clients
    nicht zustellt, sieht einen echten Geräte-Event trotzdem. Der Portal-Weg steht danach,
    weil er die Zustimmung der Nutzerin braucht.
    """
    assert IB.BACKENDS[0] == "ydotool"
    assert "xdotool" in IB.BACKENDS
    assert IB.BACKENDS[-1] == "portal"


def test_alle_sechs_zustaende_sind_unterscheidbar():
    """Die sechs Zustände der Zustellungskette müssen getrennt benennbar bleiben."""
    states = [state.value for state in ProbeState]
    assert states == [
        "BACKEND_MISSING",
        "CONSENT_MISSING",
        "PROBE_MISCONFIGURED",
        "INPUT_SENT",
        "INPUT_OBSERVED_AT_TARGET",
        "APP_EFFECT_VERIFIED",
    ]


def test_gesendet_ist_nie_zugestellt():
    """Der Kernfehler: rc=0 (Versand) wurde als Zustellung gelesen."""
    sent = _found(ProbeState.INPUT_SENT)
    assert sent.delivers is False
    assert sent.counts_as_success is False
    assert sent.verify_verdict == "fail"
    observed = _found(ProbeState.INPUT_OBSERVED_AT_TARGET)
    assert observed.delivers is True
    assert observed.counts_as_success is False, (
        "an observed delivery is a proven delivery, but not yet a verified effect"
    )


def test_unbekannt_ist_kein_erfolg():
    """UNKNOWN zählt nie als Erfolg — auch nicht als halber."""
    for state in (ProbeState.PROBE_MISCONFIGURED, ProbeState.CONSENT_MISSING):
        result = _found(state)
        assert result.counts_as_success is False
        assert result.verify_verdict == "unknown"
        assert result.state.is_unknown is True


def test_nur_die_nachgewiesene_wirkung_ist_ein_erfolg():
    assert ProbeState.APP_EFFECT_VERIFIED.counts_as_success is True
    verified = _found(ProbeState.APP_EFFECT_VERIFIED)
    assert verified.verify_verdict == "pass"


def test_die_wirkung_braucht_eine_vorherige_zustellung():
    """Aus `INPUT_SENT` wird keine Wirkung hochgestuft — das wäre ein erfundener Erfolg."""
    sent = _found(ProbeState.INPUT_SENT)
    with pytest.raises(ValueError):
        IB.promote_to_effect_verified(sent, evidence="the cell shows 42")


def test_die_wirkung_braucht_einen_beleg():
    observed = _found(ProbeState.INPUT_OBSERVED_AT_TARGET)
    with pytest.raises(ValueError):
        IB.promote_to_effect_verified(observed, evidence="   ")


def test_die_wirkung_wird_mit_beleg_hochgestuft():
    observed = _found(ProbeState.INPUT_OBSERVED_AT_TARGET)
    verified = IB.promote_to_effect_verified(observed, evidence="cell B2 shows 42")
    assert verified.state is ProbeState.APP_EFFECT_VERIFIED
    assert verified.counts_as_success is True
    assert "cell B2 shows 42" in verified.detail


def test_backend_available_meldet_fehler_mit_grund(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: "")
    ok, why = IB.backend_available("xdotool")
    assert ok is False
    assert "not installed" in why


def test_unbekanntes_backend_wird_abgelehnt():
    ok, why = IB.backend_available("gibtesnicht")
    assert ok is False
    assert "unknown backend" in why


def test_ydotool_braucht_einen_socket(monkeypatch, tmp_path):
    """Ohne einen erreichbaren Socket ist uinput nicht benutzbar — mit Grund."""
    import shutil

    monkeypatch.setattr(IB, "MISSION_SOCKET", str(tmp_path / "mission.sock"))
    monkeypatch.setattr(IB, "DEFAULT_SOCKET", str(tmp_path / "default.sock"))
    monkeypatch.setenv(IB.SOCKET_ENV, str(tmp_path / "from-env.sock"))
    monkeypatch.setattr(
        shutil, "which",
        lambda name: "/usr/bin/ydotool" if name == "ydotool" else "",
    )
    ok, why = IB.backend_available("ydotool")
    assert ok is False
    assert "socket" in why.lower()
    assert "from-env.sock" in why, "the discovery order names where it looked"


def test_ein_socket_der_verbindungen_verweigert_ist_nicht_erreichbar(monkeypatch, tmp_path):
    """Gemessen: Die Datei existiert, der Daemon nimmt keine Verbindung an.

    Das ist ``BACKEND_MISSING`` — ein Timeout oder eine Verweigerung darf nie wie eine
    Zustellung aussehen.
    """
    import shutil

    path = tmp_path / "refused.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(path))
        listener.listen(1)
    finally:
        listener.close()  # the file stays, the listener is gone → ECONNREFUSED
    monkeypatch.setattr(IB, "MISSION_SOCKET", str(path))
    monkeypatch.setattr(IB, "DEFAULT_SOCKET", str(tmp_path / "none.sock"))
    monkeypatch.setenv(IB.SOCKET_ENV, str(tmp_path / "env.sock"))
    monkeypatch.setattr(
        shutil, "which",
        lambda name: "/usr/bin/ydotool" if name == "ydotool" else "",
    )
    ok, why = IB.backend_available("ydotool")
    assert ok is False
    assert "unusable" in why, why
    assert str(path) in why


def test_die_socket_reihenfolge_gilt(monkeypatch):
    """`YDOTOOL_SOCKET` zuerst, dann der Mission-Socket, dann die Voreinstellung."""
    monkeypatch.setenv(IB.SOCKET_ENV, "/first.sock")
    monkeypatch.setattr(IB, "MISSION_SOCKET", "/second.sock")
    monkeypatch.setattr(IB, "DEFAULT_SOCKET", "/third.sock")
    assert IB.ydotool_sockets() == ["/first.sock", "/second.sock", "/third.sock"]


# --------------------------------------------------------------------------- #
# probe(): nur ein gemeldetes Event zählt
# --------------------------------------------------------------------------- #

def test_rc_0_allein_ist_keine_zustellung(monkeypatch, tmp_path):
    """Der gemessene Host-Fehler: rc=0, Ziel meldet nichts."""
    monkeypatch.setattr(IB, "backend_available", lambda name: (True, "da"))
    calls: list[list[str]] = []

    def _send(argv, timeout, env=None):
        calls.append(list(argv))
        return 0, ""

    found = IB.probe("xdotool", timeout=0.3, marker="m", send=_send,
                     window=_injected_window(tmp_path), spawn=_no_window)
    assert found.state is ProbeState.INPUT_SENT
    assert found.sent is True
    assert found.delivers is False, "rc=0 ohne Event am Ziel ist keine Zustellung"
    assert "not delivery" in found.detail
    assert calls, "the probe really tried to send"


def test_ein_gemeldetes_event_zaehlt(monkeypatch, tmp_path):
    """Nicht irgendeine Zeile: der Marker am eigenen Fenster im Zeitfenster."""
    monkeypatch.setattr(IB, "backend_available", lambda name: (True, "da"))
    window = _injected_window(tmp_path)

    def _send(argv, timeout, env=None):
        _report(window, "SKPmarker")
        return 0, ""

    found = IB.probe("xdotool", timeout=1.0, marker="SKPmarker", window=window, send=_send,
                     spawn=_no_window)
    assert found.state is ProbeState.INPUT_OBSERVED_AT_TARGET
    assert found.delivers is True


def test_eine_fremde_zeile_zaehlt_nicht(monkeypatch, tmp_path):
    """Ein unmarkiertes Event (oder ein fremdes Fenster) ist keine Zustellung."""
    monkeypatch.setattr(IB, "backend_available", lambda name: (True, "da"))
    window = _injected_window(tmp_path)

    def _send(argv, timeout, env=None):
        _report(window, "ganz was anderes")
        return 0, ""

    found = IB.probe("xdotool", timeout=0.3, marker="SKPmarker", window=window, send=_send,
                     spawn=_no_window)
    assert found.state is ProbeState.INPUT_SENT
    assert found.delivers is False
    assert "marker" in found.correlation


def test_nicht_verfuegbares_backend_wird_nicht_probiert(monkeypatch):
    """Ein nicht installiertes Backend wird nicht ausgeführt — mit Grund."""
    monkeypatch.setattr(
        IB, "backend_available", lambda name: (False, "xdotool is not installed")
    )
    found = IB.probe("xdotool", marker="m", send=_no_send(), spawn=_no_window)
    assert found.available is False
    assert found.state is ProbeState.BACKEND_MISSING
    assert found.delivers is False
    assert "not installed" in found.detail


def test_der_kernel_beobachter_entscheidet_nichts(monkeypatch, tmp_path):
    """Ein Kernel-Ereignis wird getrennt berichtet und liest nie die Zustellung."""
    monkeypatch.setattr(IB, "backend_available", lambda name: (True, "da"))
    found = IB.probe("xdotool", timeout=0.3, marker="m",
                     window=_injected_window(tmp_path), send=_quiet_send(), spawn=_no_window,
                     kernel_device="/dev/input/does-not-exist")
    assert found.kernel_events == 0
    assert found.kernel_detail, "the observer says why it saw nothing"
    assert found.state is ProbeState.INPUT_SENT, (
        "a kernel count never turns a sent keypress into a delivery"
    )


# --------------------------------------------------------------------------- #
# choose(): ehrlich blockiert statt Erfolg vorgetäuscht
# --------------------------------------------------------------------------- #

def _choose(monkeypatch, states, backend_names=("ydotool", "xdotool", "portal")):
    assert len(backend_names) == len(states), "every backend gets its own answer"
    answers = dict(zip(backend_names, states, strict=True))
    monkeypatch.setattr(
        IB, "probe",
        lambda name, **kw: ProbeResult(name, answers[name], f"{name}: {answers[name].value}"),
    )
    return IB.choose(timeout=0.2)


def test_choose_meldet_blockiert_wenn_nichts_zustellt(monkeypatch):
    chosen, findings = _choose(monkeypatch, [ProbeState.INPUT_SENT] * 3)
    assert chosen.backend == "none"
    assert chosen.state is ProbeState.INPUT_SENT
    assert chosen.delivers is False
    assert chosen.counts_as_success is False
    assert "sent is not delivered" in chosen.detail
    assert [f.backend for f in findings] == list(IB.BACKENDS)


def test_choose_nimmt_das_erste_zustellende(monkeypatch):
    chosen, findings = _choose(
        monkeypatch,
        [ProbeState.INPUT_SENT, ProbeState.INPUT_OBSERVED_AT_TARGET, ProbeState.BACKEND_MISSING],
    )
    assert chosen.backend == "xdotool"
    assert chosen.delivers is True


def test_choose_laesst_die_zustimmung_nicht_verschwinden(monkeypatch):
    """Fehlt nur die Zustimmung der Nutzerin, ist die Antwort CONSENT_MISSING."""
    chosen, _findings = _choose(
        monkeypatch,
        [ProbeState.BACKEND_MISSING, ProbeState.INPUT_SENT, ProbeState.CONSENT_MISSING],
    )
    assert chosen.state is ProbeState.CONSENT_MISSING
    assert "consent" in chosen.detail.lower()


def test_choose_haelt_unknown_fuer_unknown(monkeypatch):
    """Eine nicht eingerichtete Probe ist unbekannt — kein Vorwurf an ein Backend."""
    chosen, _findings = _choose(
        monkeypatch,
        [ProbeState.PROBE_MISCONFIGURED, ProbeState.INPUT_SENT, ProbeState.BACKEND_MISSING],
    )
    assert chosen.state is ProbeState.PROBE_MISCONFIGURED
    assert chosen.verify_verdict == "unknown"
    assert "not a host or compositor defect" in chosen.detail


def test_choose_meldet_fehlende_backends(monkeypatch):
    chosen, _findings = _choose(monkeypatch, [ProbeState.BACKEND_MISSING] * 3)
    assert chosen.state is ProbeState.BACKEND_MISSING
    assert "no input backend is usable" in chosen.detail


def test_choose_faellt_auf_xdotool_zurueck_wenn_uinput_fehlt(monkeypatch):
    """Wenn nur xdotool zustellt, ist das das Ergebnis — nicht die Vorliebe."""
    chosen, _findings = _choose(
        monkeypatch,
        [ProbeState.BACKEND_MISSING, ProbeState.INPUT_OBSERVED_AT_TARGET,
         ProbeState.CONSENT_MISSING],
    )
    assert chosen.backend == "xdotool"
    assert chosen.delivers is True


# --------------------------------------------------------------------------- #
# seams used above — process boundaries, never the method under test
# --------------------------------------------------------------------------- #

def _no_send():
    def _send(argv, timeout, env=None):
        raise AssertionError("nothing may be sent in this scenario")
    return _send


def _quiet_send():
    """Versand, der rc=0 liefert und am eigenen Fenster nichts meldet."""
    def _send(argv, timeout, env=None):
        return 0, ""
    return _send


def _no_window(**kwargs):
    raise AssertionError("no window may be spawned in this scenario")


def _injected_window(tmp_path: Path, *, confirmed: bool = True, reason: str = "") -> object:
    """Ein eigenes Fenster, das der Probe schon vorliegt (Nahtstelle, nicht die Methode).

    Jeder Aufruf bekommt seine eigene Kennung und sein eigenes Protokoll: Ein geteiltes
    Protokoll wäre genau der Fehler, den die Marker-Korrelation verhindern soll. Es liegt im
    `tmp_path` des Tests — kein Test hinterlässt etwas in `/tmp`.
    """
    run = IB.new_marker()
    log = tmp_path / f"probe-{run}.jsonl"
    binding = ProbeBinding(
        run=run, pid=4242, wid=7, log_path=log, directory=log.parent,
        title=f"SKALDIR-PROBE-{run}", map_state="IsViewable",
        confirmed=confirmed, reason=reason,
    )
    return ProbeWindow(binding=binding, proc=None)


def _report(window, chars: str, *, wid: int | None = None) -> None:
    """Der Empfänger meldet: genau das schreibt das eigene Fenster in sein Protokoll."""
    with open(window.binding.log_path, "a", encoding="utf-8") as handle:
        for index, char in enumerate(chars):
            record = DeliveryRecord(
                run=window.binding.run, wid=wid if wid is not None else window.binding.wid,
                t=time.time() + index * 0.01, keysym=ord(char), char=char,
            )
            handle.write(record.as_json() + "\n")
