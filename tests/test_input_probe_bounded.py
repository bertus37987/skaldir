"""Der begrenzte Eingabe-Versuch: eigenes Fenster, aktiver Empfänger, strikte Korrelation.

Die drei P0-Fehler, die diese Tests festhalten (alle vorher gemessen):

1. **Kein eigenes Ziel.** Der alte Probe tippte in das Fenster, das zufällig den Fokus hatte.
   Ein rc=0 konnte deshalb einen Tastendruck beschreiben, der beim *Nutzer* landete.
2. **Kein Empfänger.** Der alte Probe las das Ende einer Protokolldatei und nahm **jede**
   Änderung darin als Zustellung.
3. **Kein Marker.** `marker` wurde nie ausgewertet.

Diese Tests prüfen deshalb nicht die Implementierung nach, sondern das Verhalten:

* Korrelation: kein Marker → kein Treffer, fremdes Fenster → kein Treffer,
  Marker ∧ Fenster ∧ Zeit → Treffer, Marker außerhalb des Zeitfensters → kein Treffer.
* Aufbau: ohne bestätigte Bindung (PID + wid, gemappt, aktiviert, Server-Fokus,
  ``_NET_WM_PID``) wird **nichts gesendet** und das Ergebnis ist ``PROBE_MISCONFIGURED``.
* Zustände: ``INPUT_SENT`` ist nie ``INPUT_OBSERVED_AT_TARGET``, ``UNKNOWN`` ist nie ein
  Erfolg, und ``APP_EFFECT_VERIFIED`` behauptet der Probe nicht.
* Aufräumen: genau die aufgezeichnete PID und die eigenen Temp-Dateien — nie ein fremder
  Prozess, nie ein Fenster nach Titel.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from skaldir import input_backend as IB
from skaldir import probe_target as PT
from skaldir.input_backend import ProbeState
from skaldir.probe_target import (
    ProbeBinding,
    ProbeWindow,
    correlate_delivery,
    destroy_probe_window,
    spawn_probe_window,
)

T0 = 1_000.0
MARKER = "skp0123456789abcdef"


def _record(char: str, t: float, *, wid: int = 7, run: str = "run-1", press: bool = True,
            keysym: int = 0) -> object:
    return PT.DeliveryRecord(
        run=run, wid=wid, t=t, keycode=0, keysym=keysym or (ord(char) if char else 0),
        char=char, press=press,
    )


# --------------------------------------------------------------------------- #
# 1. Die Korrelation — der einzige Zustellungsbeweis (VAL-INPUT-002)
# --------------------------------------------------------------------------- #

def test_kein_marker_kein_treffer():
    """Ein unmarkiertes Key-Event ist kein Beweis — genau die Lücke des alten Probes."""
    records = [_record("a", T0 + 0.1), _record("b", T0 + 0.2), _record("c", T0 + 0.3)]
    verdict = correlate_delivery(records, marker=MARKER, wid=7, t_send=T0, timeout=5.0)
    assert verdict.delivered is False
    assert "marker" in verdict.reason


def test_fremdes_fenster_kein_treffer():
    """Der Marker kam — aber an einem anderen Fenster. Das zählt nicht."""
    records = [_record(char, T0 + 0.05 * index, wid=99) for index, char in enumerate(MARKER)]
    verdict = correlate_delivery(records, marker=MARKER, wid=7, t_send=T0, timeout=5.0)
    assert verdict.delivered is False
    assert "own window" in verdict.reason
    assert "99" in verdict.reason or "7" in verdict.reason


def test_marker_und_fenster_und_zeit_ist_ein_treffer():
    """Alle drei Bedingungen: Marker, eigenes Fenster, Sendezeitfenster."""
    records = [_record(char, T0 + 0.05 * index, wid=7) for index, char in enumerate(MARKER)]
    verdict = correlate_delivery(records, marker=MARKER, wid=7, t_send=T0, timeout=5.0)
    assert verdict.delivered is True
    assert len(verdict.records) == len(MARKER)


def test_marker_ausserhalb_des_zeitfensters_kein_treffer():
    """Ein Marker von früher (oder später) beweist nichts über DIESE Sendung."""
    records = [_record(char, T0 + 30 + 0.05 * index) for index, char in enumerate(MARKER)]
    verdict = correlate_delivery(records, marker=MARKER, wid=7, t_send=T0, timeout=5.0)
    assert verdict.delivered is False
    assert "outside" in verdict.reason


def test_alter_probelauf_im_selben_protokoll_kein_treffer():
    """Ein Rest eines früheren Laufes trägt eine andere Fenster-Kennung."""
    stale = [_record(char, T0 - 5 + 0.05 * index, wid=4711, run="old-run")
             for index, char in enumerate(MARKER)]
    fresh = [_record(char, T0 + 0.05 * index, wid=7, run="run-1")
             for index, char in enumerate("xy")]
    verdict = correlate_delivery(stale + fresh, marker=MARKER, wid=7, t_send=T0, timeout=5.0)
    assert verdict.delivered is False


def test_leerer_empfaenger_kein_treffer():
    verdict = correlate_delivery([], marker=MARKER, wid=7, t_send=T0, timeout=5.0)
    assert verdict.delivered is False
    assert "no event" in verdict.reason


def test_ohne_marker_wird_nichts_korreliert():
    """Ein leerer Marker ist keine Zustimmung zum Raten."""
    records = [_record(char, T0 + 0.1) for char in "abc"]
    verdict = correlate_delivery(records, marker="", wid=7, t_send=T0, timeout=5.0)
    assert verdict.delivered is False
    assert "no marker" in verdict.reason


def test_shift_und_release_lassen_die_markerkette_ganz():
    """Shift selbst und Loslass-Events tragen kein Zeichen und dürfen nichts zerreißen."""
    records = [_record("", T0, keysym=0xFFE1, press=True)]          # Shift press
    records += [_record(char, T0 + 0.1 * (i + 1)) for i, char in enumerate(MARKER)]
    records += [_record("", T0 + 99, keysym=0xFFE1, press=False)]   # Shift release
    verdict = correlate_delivery(records, marker=MARKER, wid=7, t_send=T0, timeout=5.0)
    assert verdict.delivered is True


def test_ein_text_event_traegt_den_marker_allein():
    """Ein Backend, das einen Text in einem Event liefert, zählt ebenfalls — mit Zeitfenster."""
    inside = PT.DeliveryRecord(run="run-1", wid=7, t=T0 + 0.2, text=f"vor {MARKER} nach")
    verdict = correlate_delivery([inside], marker=MARKER, wid=7, t_send=T0, timeout=5.0)
    assert verdict.delivered is True

    outside = PT.DeliveryRecord(run="run-1", wid=7, t=T0 + 30, text=MARKER)
    verdict = correlate_delivery([outside], marker=MARKER, wid=7, t_send=T0, timeout=5.0)
    assert verdict.delivered is False


def test_protokollzeilen_ohne_ereignis_werden_nicht_mitgelesen():
    """Nur KEY/TEXT-Zeilen sind Ereignisse; READY und Schrott sind keine."""
    text = "\n".join([
        json.dumps({"kind": "READY", "ok": True, "run": "run-1", "wid": 7, "pid": 1}),
        "not json at all",
        json.dumps({"kind": "KEY", "run": "run-1", "wid": 7, "t": T0, "keycode": 38,
                    "keysym": 97, "char": "a"}),
        json.dumps({"kind": "NOTE", "wid": 7, "t": T0, "char": "z"}),
    ])
    records = PT.parse_records(text)
    assert len(records) == 1
    assert records[0].char == "a"


def test_ereignis_ueberlebert_die_json_runde():
    record = PT.DeliveryRecord(run="run-1", wid=7, t=T0, keycode=38, keysym=97, char="a")
    again = PT.parse_records(record.as_json())[0]
    assert (again.run, again.wid, again.t, again.char) == ("run-1", 7, T0, "a")


# --------------------------------------------------------------------------- #
# 2. Der Probe selbst: Reihenfolge, Senden, Zustände (VAL-INPUT-001/005)
# --------------------------------------------------------------------------- #

@pytest.fixture()
def ready(monkeypatch):
    """Das Backend ist benutzbar — gemessen wird hier nicht die Installation."""
    monkeypatch.setattr(IB, "backend_available", lambda name: (True, "installed for the test"))


def _window(tmp_path: Path, *, records=(), wid: int = 7, pid: int = 4242, run: str = "run-1",
            confirmed: bool = True, reason: str = "", directory: Path | None = None) -> ProbeWindow:
    directory = directory if directory is not None else tmp_path
    log = directory / f"probe-{run}.jsonl"
    log.write_text("".join(item.as_json() + "\n" for item in records), encoding="utf-8")
    binding = ProbeBinding(
        run=run, pid=pid, wid=wid, log_path=log, directory=directory,
        title=f"SKALDIR-PROBE-{run}", map_state="IsViewable",
        confirmed=confirmed, reason=reason,
    )
    return ProbeWindow(binding=binding, proc=None)


def _sending(log_path: Path, chars: str = "", *, wid: int = 7, run: str = "run-1",
             rc: int = 0, at: float | None = None):
    """Der Sendungs-Rand: das Ziel meldet seine Events **während** der Sendung.

    Der Zeitstempel entsteht beim Schreiben, nicht beim Bauen der Liste — genau wie beim echten
    Empfänger, dessen Uhr erst läuft, wenn gesendet wird. ``at`` legt einen festen Stempel, um
    ein Event außerhalb des Sendezeitfensters zu legen.
    """
    def _send(argv, timeout, env=None):
        _send.calls.append(list(argv))
        with open(log_path, "a", encoding="utf-8") as handle:
            for index, char in enumerate(chars):
                record = PT.DeliveryRecord(
                    run=run, wid=wid,
                    t=(at if at is not None else time.time()) + index * 0.01,
                    keycode=0, keysym=ord(char), char=char,
                )
                handle.write(record.as_json() + "\n")
        return rc, ""
    _send.calls = []
    return _send


def _counting():
    def _send(argv, timeout, env=None):
        _send.calls.append(list(argv))
        return 0, ""
    _send.calls = []
    return _send


def _no_spawn(**kwargs):
    raise AssertionError("no window may be spawned in this scenario")


def test_ohne_bestaetigte_bindung_wird_nichts_gesendet(tmp_path, ready):
    """Der Aufbau trägt nicht: kein Tastendruck, und ein ehrliches UNBEKANNT."""
    send = _counting()
    own = _window(tmp_path, confirmed=False, reason="_NET_WM_PID is 999, not 4242")
    found = IB.probe("xdotool", timeout=0.4, marker=MARKER, window=own, send=send,
                     spawn=_no_spawn)
    assert send.calls == [], "an unconfirmed binding must never receive input"
    assert found.state is ProbeState.PROBE_MISCONFIGURED
    assert found.sent is False
    assert found.delivers is False
    assert found.counts_as_success is False
    assert found.verify_verdict == "unknown"
    assert "4242" in found.detail, "the refusal must name the binding that failed"


def test_marker_am_eigenen_fenster_ist_am_ziel_angekommen(tmp_path, ready):
    """Der gute Fall: der Marker kommt am eigenen Fenster im Zeitfenster an."""
    log = tmp_path / "probe-run-1.jsonl"
    send = _sending(log, MARKER, wid=7)
    own = _window(tmp_path)
    found = IB.probe("xdotool", timeout=1.0, marker=MARKER, window=own, send=send,
                     spawn=_no_spawn)
    assert found.state is ProbeState.INPUT_OBSERVED_AT_TARGET
    assert found.delivers is True
    assert found.sent is True
    assert found.window["wid"] == 7
    assert found.window["pid"] == 4242
    assert found.records, "the correlated records are part of the evidence"
    assert "".join(item["char"] for item in found.records) == MARKER


def test_marker_an_einem_fremden_fenster_ist_kein_zustellbeweis(tmp_path, ready):
    """Derselbe Marker, aber am Fenster eines anderen Prozesses: kein Treffer."""
    log = tmp_path / "probe-run-1.jsonl"
    send = _sending(log, MARKER, wid=99)
    own = _window(tmp_path)
    found = IB.probe("xdotool", timeout=0.4, marker=MARKER, window=own, send=send,
                     spawn=_no_spawn)
    assert found.state is ProbeState.INPUT_SENT
    assert found.delivers is False
    assert "own window" in found.correlation


def test_marker_vor_der_sendung_ist_kein_zustellbeweis(tmp_path, ready):
    """Ein Event von vor der Sendung (ein Rest, ein anderer Sender) beweist nichts."""
    log = tmp_path / "probe-run-1.jsonl"
    send = _sending(log, MARKER, wid=7, at=time.time() - 30.0)
    own = _window(tmp_path)
    found = IB.probe("xdotool", timeout=0.4, marker=MARKER, window=own, send=send,
                     spawn=_no_spawn)
    assert found.state is ProbeState.INPUT_SENT
    assert found.delivers is False
    assert "outside" in found.correlation


def test_rc0_ohne_marker_ist_gesendet_nicht_zugestellt(tmp_path, ready):
    """Der gemessene Host-Fall: rc=0, Ziel meldet nichts."""
    send = _sending(tmp_path / "probe-run-1.jsonl", "")
    own = _window(tmp_path)
    found = IB.probe("xdotool", timeout=0.4, marker=MARKER, window=own, send=send,
                     spawn=_no_spawn)
    assert found.state is ProbeState.INPUT_SENT
    assert found.sent is True
    assert found.delivers is False, "sent is dispatch, not delivery"
    assert found.counts_as_success is False
    assert found.verify_verdict == "fail"
    assert "not delivery" in found.detail


def test_gescheiterte_sendung_ist_backend_missing(tmp_path, ready):
    send = _sending(tmp_path / "probe-run-1.jsonl", "", rc=1)
    own = _window(tmp_path)
    found = IB.probe("xdotool", timeout=0.4, marker=MARKER, window=own, send=send,
                     spawn=_no_spawn)
    assert found.state is ProbeState.BACKEND_MISSING
    assert found.sent is False
    assert found.send_rc == 1
    assert found.delivers is False


def test_gesendet_wird_an_das_eigene_fenster(tmp_path, ready):
    """P0-Fehler 1: Der alte Probe tippte in das Fenster, das zufällig den Fokus hatte.

    Die argv nennt deshalb die eigene Fenster-Nummer, und zwar **vor** dem Text. Ein Marker
    darf nie in einem Fenster der Nutzerin landen.
    """
    send = _sending(tmp_path / "probe-run-1.jsonl", "")
    own = _window(tmp_path)
    found = IB.probe("xdotool", timeout=0.4, marker=MARKER, window=own, send=send,
                     spawn=_no_spawn)
    argv = send.calls[0]
    assert argv[:3] == ["xdotool", "windowfocus", "--sync"]
    assert str(own.binding.wid) in argv, argv
    assert argv.index(str(own.binding.wid)) < argv.index(MARKER), (
        "the focus on the probe's own window comes before the marker"
    )
    assert found.window["wid"] == own.binding.wid


def test_uinput_wird_unmittelbar_vor_der_sendung_fokussiert(tmp_path, ready):
    """`ydotool` kennt auf Kernel-Ebene kein Fenster — also wird vorher fokussiert.

    Sonst tippte der Marker in das Fenster, das gerade den Fokus hat: beim Nutzer.
    """
    send = _sending(tmp_path / "probe-run-1.jsonl", "")
    own = _window(tmp_path)
    IB.probe("ydotool", timeout=0.4, marker=MARKER, window=own, send=send, spawn=_no_spawn)
    commands = list(send.calls)
    focus = ["xdotool", "windowfocus", "--sync", str(own.binding.wid)]
    assert focus in commands, commands
    assert "type" in commands[-1], commands
    assert commands.index(focus) < len(commands) - 1, "the focus is set before the send"


def test_die_sendung_laeuft_auf_dem_display_des_probe(tmp_path, ready, monkeypatch):
    """P0-Nachbarschaft: der Marker darf nie auf dem Display der Shell landen.

    Gemessen an einem echten Lauf: Ein Runner, der die Sitzung der Besitzerin misst, wurde
    aus einer Shell gestartet, deren `$DISPLAY` auf den Host-Desktop zeigte. Ohne diesen
    Test hätte `xdotool windowfocus` dort ein fremdes Fenster fokussiert und der Marker wäre
    auf dem Desktop der Nutzerin gelandet. Jetzt reist das Display mit dem Befehl.
    """
    seen: list[dict | None] = []

    def _send(argv, timeout, env=None):
        seen.append(env)
        return 0, ""

    own = _window(tmp_path)
    monkeypatch.setenv("DISPLAY", ":0", prepend=False)  # the shell points at the host desktop
    IB.probe("xdotool", timeout=0.3, marker=MARKER, window=own, send=_send,
             display=":92", spawn=_no_spawn)
    assert seen, "the probe really tried to send"
    for env in seen:
        assert env is not None, "the send must carry the display it was created on"
        assert env["DISPLAY"] == ":92", env["DISPLAY"]
    assert os.environ["DISPLAY"] == ":0", "the caller's own display is not overwritten"


def test_nicht_benutzbares_backend_wird_nicht_probiert(monkeypatch):
    monkeypatch.setattr(IB, "backend_available", lambda name: (False, "ydotool is not installed"))
    send = _counting()
    found = IB.probe("ydotool", marker=MARKER, send=send, spawn=_no_spawn)
    assert send.calls == []
    assert found.state is ProbeState.BACKEND_MISSING
    assert "not installed" in found.detail


def test_portal_ohne_zustimmung_ist_consent_missing(monkeypatch):
    """Der offizielle Weg existiert, aber die Zustimmung der Nutzerin fehlt."""
    monkeypatch.setattr(
        IB, "portal_path_state",
        lambda **kw: (IB.PORTAL_NEEDS_CONSENT,
                      "the RemoteDesktop portal needs an explicit user consent dialog"),
    )
    send = _counting()
    found = IB.probe("portal", marker=MARKER, send=send, spawn=_no_spawn)
    assert send.calls == [], "no consent, no send"
    assert found.state is ProbeState.CONSENT_MISSING
    assert found.verify_verdict == "unknown"
    assert "consent" in found.detail


def test_portal_ganz_ohne_weg_ist_backend_missing(monkeypatch):
    monkeypatch.setattr(
        IB, "portal_path_state",
        lambda **kw: (IB.PORTAL_UNAVAILABLE, "libei.so.1 is missing on this host"),
    )
    found = IB.probe("portal", marker=MARKER, send=_counting(), spawn=_no_spawn)
    assert found.state is ProbeState.BACKEND_MISSING
    assert "libei" in found.detail


def test_probe_beansprucht_nie_die_wirkung(tmp_path, ready):
    """`APP_EFFECT_VERIFIED` ist nie das Ergebnis eines Probes — dafür gibt es VERIFY."""
    scenarios = (
        IB.probe("xdotool", timeout=0.3, marker=MARKER, window=_window(tmp_path, confirmed=False),
                 send=_counting(), spawn=_no_spawn),
        IB.probe("xdotool", timeout=0.3, marker=MARKER,
                 window=_window(tmp_path), send=_sending(tmp_path / "probe-run-1.jsonl", ""),
                 spawn=_no_spawn),
    )
    for found in scenarios:
        assert found.state is not ProbeState.APP_EFFECT_VERIFIED
        assert found.counts_as_success is False


# --------------------------------------------------------------------------- #
# 3. Steuerung im Probe: Sperre und Not-Aus
# --------------------------------------------------------------------------- #

def test_sperre_verhindert_die_sendung(tmp_path, ready):
    def _stale_fence():
        raise RuntimeError("the desktop generation moved on")

    send = _counting()
    own = _window(tmp_path)
    found = IB.probe("xdotool", timeout=0.4, marker=MARKER, window=own, send=send,
                     fence=_stale_fence, spawn=_no_spawn)
    assert send.calls == []
    assert found.state is ProbeState.PROBE_MISCONFIGURED
    assert found.aborted is True
    assert found.verify_verdict == "unknown", "an aborted probe is unknown, not a backend defect"


def test_stop_bricht_den_wartetakt_ab(tmp_path, ready):
    """Ein Not-Aus darf keine Zustellung behaupten, die nicht gemessen wurde."""
    own = _window(tmp_path)
    found = IB.probe("xdotool", timeout=5.0, marker=MARKER, window=own,
                     send=_sending(tmp_path / "probe-run-1.jsonl", ""),
                     stop_requested=lambda: True, spawn=_no_spawn)
    assert found.state is ProbeState.PROBE_MISCONFIGURED
    assert found.aborted is True
    assert found.delivers is False


def test_der_marker_geht_als_text_nicht_als_keycode(tmp_path, ready):
    """`type` nimmt Zeichenketten — Keycode-Paare gehören zum Zustellpfad, nicht zum Probe."""
    send = _counting()
    own = _window(tmp_path)
    marker = IB.new_marker()
    IB.probe("xdotool", timeout=0.3, marker=marker, window=own, send=send, spawn=_no_spawn)
    argv = send.calls[0]
    assert argv[0] == "xdotool"
    assert "type" in argv, argv
    assert "--" in argv, "a marker that looks like an option needs the separator"
    assert argv[-1] == marker
    assert not any(":1" in part for part in argv), "no keycode pair in a text send"

    send_back = _counting()
    IB.probe("ydotool", timeout=0.3, marker=marker, window=own, send=send_back,
             spawn=_no_spawn)
    # the first call focuses the probe's own window, the second is the ydotool text send
    assert send_back.calls[1][:2] == ["ydotool", "type"], send_back.calls
    assert send_back.calls[1][-1] == marker
    # the flags are the ones ydotool really knows (measured against the running daemon):
    # --delay is NOT one of them, and it answers rc 0 anyway
    assert "--key-delay" in send_back.calls[1]
    assert "--delay" not in " ".join(send_back.calls[1])
    assert not any(":1" in part for part in send_back.calls[1])


def test_der_marker_ist_ein_zufallstoken():
    first, second = IB.new_marker(), IB.new_marker()
    assert first != second
    assert len(first) >= 12


# --------------------------------------------------------------------------- #
# 4. Aufräumen: genau die eigenen Ressourcen
# --------------------------------------------------------------------------- #

def _dummy_receiver_argv(run: str) -> list[str]:
    """Ein Prozess, der wie der Empfänger *aussieht* (cmdline), aber nie ein Fenster meldet."""
    source = f"import time\n# skaldir.probe_target {run}\ntime.sleep(60)"
    return [sys.executable, "-c", source]


def test_ein_empfaenger_der_nicht_meldet_ist_probe_misconfigured(tmp_path):
    """Ein eigener Prozess, der kein Fenster meldet: kein Ziel, also keine Sendung."""
    send = _counting()

    def _spawn(**kwargs):
        kwargs.pop("workdir", None)
        # The double carries the real receiver's identity in its cmdline (module + run id):
        # that is exactly the guard `destroy_probe_window` checks before it signals a pid.
        return spawn_probe_window(
            **kwargs, argv=_dummy_receiver_argv(kwargs["run"]), timeout=0.6,
            workdir=tmp_path,
        )

    found = IB.probe("xdotool", timeout=0.6, marker=MARKER, send=send, spawn=_spawn)
    assert send.calls == [], "a probe with no target window must send nothing"
    assert found.state is ProbeState.PROBE_MISCONFIGURED
    assert found.window["pid"], "the spawned pid is recorded either way"
    assert not found.window["confirmed"]
    # the probe tore its own child down: no orphan from a failed setup
    assert not PT._pid_alive(int(found.window["pid"]))


def test_zerstoert_genau_die_aufgezeichnete_pid(tmp_path):
    """SIGTERM an die aufgezeichnete PID — und nur an sie."""
    nonce = "run-destroy"
    proc = subprocess.Popen(_dummy_receiver_argv(nonce), start_new_session=True)
    foreign = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    try:
        own_dir = tmp_path / "own"
        own_dir.mkdir()
        log = own_dir / f"probe-{nonce}.jsonl"
        log.write_text("", encoding="utf-8")
        window = ProbeWindow(binding=ProbeBinding(
            run=nonce, pid=proc.pid, wid=0, log_path=log, directory=own_dir,
            title=f"SKALDIR-PROBE-{nonce}",
        ))
        report = destroy_probe_window(window)
        assert report["terminated"] is True
        assert proc.poll() is not None, "the recorded receiver process is gone"
        assert foreign.poll() is None, "a foreign process is never signalled"
        assert report["removed"] == [str(log)], (
            "a directory the caller supplied keeps everything except this probe's own files"
        )
        assert not log.exists()
        assert own_dir.exists(), "the caller's directory is not the probe's to remove"
    finally:
        for leftover in (proc, foreign):
            if leftover.poll() is None:
                leftover.kill()
                leftover.wait(timeout=5)


def test_fremde_pid_bleibt_unberuehrt(tmp_path):
    """Eine fremde PID (auch nach Wiederverwendung der Nummer) wird nicht beendet."""
    foreign = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    try:
        nonce = "run-foreign"
        own_dir = tmp_path / "own"
        own_dir.mkdir()
        log = own_dir / f"probe-{nonce}.jsonl"
        log.write_text("", encoding="utf-8")
        window = ProbeWindow(binding=ProbeBinding(
            run=nonce, pid=foreign.pid, wid=0, log_path=log, directory=own_dir,
        ))
        report = destroy_probe_window(window)
        assert report["foreign_pid_untouched"] is True
        assert report["terminated"] is False
        assert foreign.poll() is None, "the guard must leave a foreign process running"
    finally:
        if foreign.poll() is None:
            foreign.kill()
            foreign.wait(timeout=5)


def test_ueberlebendes_fenster_wird_gemeldet_nicht_geschlossen():
    """Ein Fenster, das nach dem Stopp seines Empfängers noch steht, wird berichtet."""
    window = ProbeWindow(binding=ProbeBinding(
        run="run-leftover", pid=0, wid=31337, log_path=Path("/nonexistent.jsonl"),
        directory=None,
    ))
    report = destroy_probe_window(
        window, run_cmd=lambda argv, timeout: (0, "SKALDIR-PROBE-run-leftover")
    )
    assert report["window_gone"] is False
    assert "never force-closed" in report["reason"]


def test_weg_ist_fenster_wirklich_weg():
    window = ProbeWindow(binding=ProbeBinding(
        run="run-gone", pid=0, wid=31338, log_path=Path("/nonexistent.jsonl"), directory=None
    ))
    report = destroy_probe_window(window, run_cmd=lambda argv, timeout: (1, ""))
    assert report["window_gone"] is True
    assert report["reason"] == ""


# --------------------------------------------------------------------------- #
# 5. Die Bindungsbestätigung, Glied für Glied
# --------------------------------------------------------------------------- #

def _confirm(run="run-1", pid=4242, wid=7, *, pid_seen=None, focus=None,
             title="SKALDIR-PROBE-run-1", state="", activate_rc=0, geometry_rc=0, map_state=""):
    binding = ProbeBinding(
        run=run, pid=pid, wid=wid, log_path=Path("/tmp/x.jsonl"), title=title,
        map_state=map_state,
    )

    def _ask(argv, timeout):
        if argv[1] == "getwindowgeometry":
            return geometry_rc, "WIDTH=460\nHEIGHT=200"
        if argv[1] == "getwindowname":
            return 0, title
        if argv[1] == "windowactivate":
            return activate_rc, ""
        if argv[3] == "WM_STATE":
            return 0, f"WM_STATE(WM_STATE) = window state: {state}" if state else ""
        if argv[3] == "_NET_WM_PID":
            seen = pid if pid_seen is None else pid_seen
            return 0, f"_NET_WM_PID(CARDINAL) = {seen}" if seen else ""
        return 1, ""

    return PT.confirm_binding(binding, run_cmd=_ask, focus=focus)


def test_die_volle_kette_bestaetigt_die_bindung():
    binding = _confirm(pid_seen=4242, focus=lambda: 7, state="Normal")
    assert binding.confirmed is True
    assert binding.reason == ""
    assert binding.checks["net_wm_pid"] == 4242
    assert binding.checks["server_focus"] == 7
    assert binding.checks["wm_state"] == "Normal"


def test_rohes_x_fenster_ohne_wm_state_ist_kein_fehler():
    """Messung: ein rohes X-Fenster hat gar kein WM_STATE — nur Withdrawn wäre ein Problem."""
    binding = _confirm(pid_seen=4242, focus=lambda: 7, state="")
    assert binding.checks["wm_state"] == "(keiner)"
    assert binding.confirmed is True


def test_withdrawnes_fenster_wird_abgelehnt():
    binding = _confirm(pid_seen=4242, focus=lambda: 7, state="Withdrawn")
    assert binding.confirmed is False
    assert "Withdrawn" in binding.reason


def test_fremde_wm_pid_wird_abgelehnt():
    binding = _confirm(pid_seen=999, focus=lambda: 7, state="Normal")
    assert binding.confirmed is False
    assert "_NET_WM_PID" in binding.reason
    assert "4242" in binding.reason


def test_fremder_fokus_wird_abgelehnt():
    """Steht der Server-Fokus woanders, landet der Tastendruck beim Nutzer."""
    binding = _confirm(pid_seen=4242, focus=lambda: 4711, state="Normal")
    assert binding.confirmed is False
    assert "input focus" in binding.reason


def test_aktivierungsscheitern_wird_abgelehnt():
    binding = _confirm(pid_seen=4242, focus=lambda: 7, state="Normal", activate_rc=1)
    assert binding.confirmed is False
    assert "windowactivate" in binding.reason


def test_titel_aus_einem_anderen_lauf_wird_abgelehnt():
    binding = _confirm(pid_seen=4242, focus=lambda: 7, state="Normal",
                       title="SKALDIR-PROBE-jemand-anders")
    assert binding.confirmed is False
    assert "title" in binding.reason


def test_nicht_gemappt_meldet_der_empfaenger_selbst():
    binding = _confirm(pid_seen=4242, focus=lambda: 7, state="Normal", map_state="IsUnmapped")
    assert binding.confirmed is False
    assert "map state" in binding.reason


def test_fehlerhafte_aktivierung_verhindert_das_senden():
    """Die Kette bricht, bevor irgendetwas gesendet wird."""
    binding = _confirm(pid_seen=4242, focus=lambda: 4711, state="Normal", activate_rc=1)
    reasons = binding.reason
    assert "windowactivate" in reasons and "input focus" in reasons


# --------------------------------------------------------------------------- #
# 6. Der Server-Fokus
# --------------------------------------------------------------------------- #

def test_server_fokus_liegt_der_server_meinung_zugrunde():
    wid, method = PT.read_server_focus(reader=lambda: 1234)
    assert wid == 1234
    assert method == "injected reader"


def test_server_fokus_faellt_auf_xdotool_zurueck(monkeypatch):
    monkeypatch.setattr(PT, "_xlib_focus", lambda env=None: None)
    wid, method = PT.read_server_focus(run_cmd=lambda argv, timeout: (0, "0x1a2b3c\n"))
    assert wid == 0x1A2B3C
    assert method == "xdotool getwindowfocus"


def test_server_fokus_liest_auch_dezimal(monkeypatch):
    """xdotool antwortet auf manchen Wegen dezimal (2097179), nicht hex."""
    monkeypatch.setattr(PT, "_xlib_focus", lambda env=None: None)
    wid, method = PT.read_server_focus(run_cmd=lambda argv, timeout: (0, "2097179"))
    assert wid == 2097179
    assert method == "xdotool getwindowfocus"


def test_server_fokus_meldet_unlesbar(monkeypatch):
    monkeypatch.setattr(PT, "_xlib_focus", lambda env=None: None)
    wid, method = PT.read_server_focus(run_cmd=lambda argv, timeout: (1, "no display"))
    assert wid == 0
    assert "not readable" in method


def test_ein_lesefehler_ist_kein_fokus():
    wid, method = PT.read_server_focus(reader=lambda: 1 / 0)
    assert wid == 0
    assert "reader failed" in method


# --------------------------------------------------------------------------- #
# 7. Der Empfänger als eigener Prozess
# --------------------------------------------------------------------------- #

def test_empfaenger_meldet_ehrlich_kein_display(tmp_path):
    """Kein Display: der Empfänger meldet es, statt zu hängen oder vorzutäuschen."""
    log = tmp_path / "probe-run-x.jsonl"
    env = {**os.environ, "DISPLAY": "/tmp/skaldir-does-not-exist:99"}
    env.pop("WAYLAND_DISPLAY", None)
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-m", PT.RECEIVER_MODULE, "--run", "run-x", "--log", str(log)],
        capture_output=True, text=True, timeout=60, env=env, check=False,
    )
    assert proc.returncode != 0
    ready = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert ready["kind"] == "READY"
    assert ready["ok"] is False
    assert "display" in ready["reason"].lower()


def test_empfaenger_hat_eine_lebensgrenze(tmp_path):
    """Ein Waisenkind darf nicht ewig stehen bleiben."""
    log = tmp_path / "probe-run-life.jsonl"
    env = {**os.environ, "DISPLAY": "/tmp/skaldir-does-not-exist:99"}
    env.pop("WAYLAND_DISPLAY", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", PT.RECEIVER_MODULE, "--run", "run-life", "--log", str(log),
         "--lifetime", "1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, start_new_session=True,
    )
    try:
        assert proc.wait(timeout=15) in (0, 3), "the receiver ends on its own, bounded"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_kein_code_schliesst_ein_fenster_nach_titel_oder_prozessname():
    """Der Wächter der Besitzregel: kein Schließen nach Titel, kein Killing nach Namen."""
    forbidden = ("windowclose", "pkill", "killall", "taskkill", "wmctrl -c")
    for path in (Path(PT.__file__), Path(IB.__file__)):
        body = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in body.lower(), (
                f"{path.name} contains {needle!r} — closing a window by title or killing a "
                "process by name is forbidden; only a recorded pid may be signalled"
            )


def test_das_eigene_temp_verzeichnis_wird_angelegt_und_entfernt(tmp_path, monkeypatch):
    """Ein selbst erzeugtes Verzeichnis geht als Ganzes weg; ein geliehenes nurDateien."""
    own = Path("/tmp") / f"{PT.PROBE_TMP_PREFIX}selftest"
    monkeypatch.setattr(PT.tempfile, "gettempdir", lambda: "/tmp")
    try:
        log = own / "probe-run-own.jsonl"
        binding = ProbeBinding(run="run-own", pid=0, wid=0, log_path=log, directory=own)
        window = ProbeWindow(binding=binding)
        report = destroy_probe_window(window, run_cmd=lambda argv, timeout: (1, ""))
        assert not own.exists(), "the probe's own temp directory is removed"
        assert str(own) in report["removed"]
    finally:
        shutil.rmtree(own, ignore_errors=True)
