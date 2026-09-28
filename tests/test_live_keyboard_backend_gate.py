"""Live-Tastaturpfad: blockiert melden statt einen nicht zugestellten Tastendruck behaupten.

Der Host-Befund, auf dem diese Tests stehen (alle gemessen, 2026-09-26):
`xdotool key`, `xdotool key --window <wid>`, `xdotool type`, XTEST per Xlib und
`ydotool key` liefern rc=0, während das Zielfenster **kein** Key-Event meldet. X-Fokus,
`WM_STATE` und Aktivierung sind zu diesem Zeitpunkt korrekt, und
`xdotool key --window <ungültig>` liefert sehr wohl `BadWindow` — XTEST funktioniert
also, nur die Zustellung an ein Xwayland-Fenster bleibt aus.

Festgehalten wird:
1. Ein Tastendruck wird nur gebaut, wenn ein Backend wirklich zugestellt hat.
2. Der uinput-Pfad (`ydotool`) übersetzt Key-Namen über die echte Keymap, nicht geraten.
3. Ein Key ohne Keycode wird abgelehnt, nicht still fallen gelassen.
4. Die Verweigerung nennt die Messung, nicht bloß „abgelehnt".
5. Der Bericht des Tores trennt die sechs Zustände und täuscht keinen Vorrat vor.
"""

from __future__ import annotations

import pytest

from skaldir import input_backend as IB
from skaldir.cu_host_session import Action, HostSessionRunner, TargetScopeRefused
from skaldir.input_backend import ProbeResult, ProbeState


@pytest.fixture()
def runner():
    return HostSessionRunner(target="probe-fenster", attach_to_session=True,
                             allow_live_desktop=True, display=":0", timeout=5.0)


# --------------------------------------------------------------------------- #
# 1. Kein Tastendruck ohne zugestelltes Backend
# --------------------------------------------------------------------------- #

def test_ohne_zustellendes_backend_wird_verweigert(runner, monkeypatch):
    """Der gemessene Host-Fall: rc=0 von beiden Wegen, kein Event am eigenen Fenster.

    Das Tor selbst läuft hier wirklich (nur die Prozesse sind ausgetauscht): Es muss
    melden, **was** gemessen wurde, nicht bloß „abgelehnt".
    """
    monkeypatch.setattr(IB, "backend_available", lambda name: (True, "da"))
    monkeypatch.setattr(
        IB, "probe",
        lambda name, **kw: ProbeResult(name, ProbeState.INPUT_SENT, "rc 0, no marker back"),
    )
    with pytest.raises(TargetScopeRefused) as exc:
        runner._live_window_argv(Action(kind="key", payload={"key": "Return"}), "4711")
    text = str(exc.value)
    assert "no keyboard backend" in text
    assert "no key event at the target" in text, (
        "the refusal must name the measurement, not just say 'blocked'"
    )
    assert "sent is dispatch, not delivery" in text


def test_mit_zustellendem_backend_wird_gebaut(runner, monkeypatch):
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "xdotool")
    argv = runner._live_window_argv(Action(kind="key", payload={"key": "Return"}), "4711")
    assert argv[:3] == ["xdotool", "windowfocus", "--sync"]
    assert "4711" in argv and "Return" in argv


# --------------------------------------------------------------------------- #
# 1b. Dasselbe Tor auf dem Vollbild-Pfad (Opt-in ist auf diesem Host an)
# --------------------------------------------------------------------------- #

def test_vollbild_tastendruck_braucht_dasselbe_tor(runner, monkeypatch):
    """Gemessen: Auf diesem Host ist das Live-Desktop-Opt-in AN, also laufen Tasten über den
    Vollbild-Pfad. Ohne Tor wäre genau dort wieder rc=0 = „getippt".

    Das Tor selbst läuft hier wirklich; nur die Prozesse sind ausgetauscht.
    """
    monkeypatch.setattr(IB, "backend_available", lambda name: (True, "da"))
    monkeypatch.setattr(
        IB, "probe",
        lambda name, **kw: ProbeResult(name, ProbeState.INPUT_SENT, "rc 0, no marker back"),
    )
    with pytest.raises(TargetScopeRefused) as exc:
        runner._full_action_argv(Action(kind="key", payload={"key": "Return"}))
    text = str(exc.value)
    assert "no keyboard backend" in text
    assert "no key event at the target" in text
    assert "sent is dispatch, not delivery" in text


def test_vollbild_baut_das_tor_backend(runner, monkeypatch):
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "xdotool")
    argv = runner._full_action_argv(Action(kind="key", payload={"key": "Return"}))
    assert argv == ["xdotool", "key", "--clearmodifiers", "Return"], argv

    text = runner._full_action_argv(Action(kind="type_text", payload={"text": "abc"}))
    assert text == ["xdotool", "type", "--delay", "12", "--", "abc"], text


def test_vollbild_hintergrund_zielt_weiter_auf_das_benannte_fenster(runner, monkeypatch):
    """Der Vollbild-Pfad darf jedes Fenster fahren — aber nur das benannte.

    `background` bleibt die Form „tippen ohne Fokusdiebstahl". Dass `key --window` auf
    einem XWayland-Client nicht zustellt, ist gemessen und steht deshalb im Kommentar der
    argv-Bauers; das Tor oben gilt trotzdem.
    """
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "xdotool")
    argv = runner._full_action_argv(
        Action(kind="key", payload={"key": "Return", "window": "777", "background": True})
    )
    assert argv == ["xdotool", "key", "--window", "777", "--clearmodifiers", "Return"], argv


def test_vollbild_ein_aufruf_argv_wird_nie_ausgefuehrt(runner, monkeypatch):
    """F1 gilt auf beiden Pfaden: ein Kommando aus dem Plan läuft nicht.

    Der Vollbild-Pfad verwirft es nicht mit einem Fehler, er baut einfach seinen eigenen
    Befehl aus dem Payload (gemessen: das alte Verhalten war, dass das argv des Plans den
    Fensterfokus-Pfad überschrieb).
    """
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "xdotool")
    argv = runner._full_action_argv(
        Action(kind="key", payload={"key": "Return"}, argv=["sh", "-c", "echo OVERRIDDEN"])
    )
    assert argv == ["xdotool", "key", "--clearmodifiers", "Return"], argv
    assert "echo" not in " ".join(argv)


def test_text_wird_gebaut(runner, monkeypatch):
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "xdotool")
    argv = runner._live_window_argv(Action(kind="type_text", payload={"text": "ABC"}), "4711")
    assert argv[:3] == ["xdotool", "windowfocus", "--sync"]
    assert "--" in argv and "ABC" in argv


# --------------------------------------------------------------------------- #
# 2. Der uinput-Pfad übersetzt über die echte Keymap
# --------------------------------------------------------------------------- #

KEYMAP = (
    "keycode   9 = Escape NoSymbol Escape NoSymbol Escape\n"
    "keycode  36 = Return NoSymbol Return NoSymbol Return\n"
    "keycode  38 = a A a A a A a A\n"
    "keycode  52 = z Z z Z z Z z Z\n"
)


def _fake_run(self, argv):
    return 0, KEYMAP


def test_ydotool_uebersetzt_key_namen_ueber_die_keymap(runner, monkeypatch):
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "ydotool")
    monkeypatch.setattr(HostSessionRunner, "_run", _fake_run)
    argv = runner._live_window_argv(Action(kind="key", payload={"key": "a"}), "4711")
    assert argv[0] == "ydotool"
    assert argv[1] == "key"
    assert "38:1" in argv and "38:0" in argv, argv


def test_ydotool_type_bekommt_text_nicht_keycodes(runner, monkeypatch):
    """`ydotool type` nimmt STRINGS — Keycodes würden als Text getippt.

    Geprüft mit `ydotool type --help`: "Usage: type [OPTION]... [STRINGS]...".
    Eine argv wie ["ydotool","type","38:1","38:0"] würde den Buchstabensalat
    '38:1 38:0' eintippen, nicht das Zeichen.
    """
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "ydotool")
    monkeypatch.setattr(HostSessionRunner, "_run", _fake_run)
    argv = runner._live_window_argv(Action(kind="type_text", payload={"text": "az"}), "4711")
    assert argv[0] == "ydotool"
    assert argv[1] == "type"
    assert "az" in argv, argv
    assert not any(":1" in part or ":0" in part for part in argv), (
        "keycode pairs belong to `ydotool key`, not to `ydotool type`"
    )


def test_xdotool_type_hat_delay_und_trenner(runner, monkeypatch):
    """Ein zu schnelles `type` überfordert langsame Ziele; -- trennt Text von Optionen."""
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "xdotool")
    argv = runner._live_window_argv(Action(kind="type_text", payload={"text": "-x"}), "4711")
    assert "--" in argv, "text that looks like an option needs the separator"
    assert "--delay" in argv


def test_ydotool_key_hat_press_und_release(runner, monkeypatch):
    """Ein key braucht beide Hälften; nur press hinterält die Taste gedrückt."""
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "ydotool")
    monkeypatch.setattr(HostSessionRunner, "_run", _fake_run)
    argv = runner._live_window_argv(Action(kind="key", payload={"key": "a"}), "4711")
    assert "38:1" in argv and "38:0" in argv, argv


# --------------------------------------------------------------------------- #
# 3. Ein Zeichen ohne Keycode wird abgelehnt
# --------------------------------------------------------------------------- #

def test_unbekannter_key_wird_abgelehnt(runner, monkeypatch):
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "ydotool")
    monkeypatch.setattr(HostSessionRunner, "_run", _fake_run)
    with pytest.raises(TargetScopeRefused) as exc:
        runner._live_window_argv(Action(kind="key", payload={"key": "GibtEsNicht"}), "4711")
    assert "no keycode" in str(exc.value)


def test_ydotool_type_akzeptiert_zeichen_ohne_keycode(runner, monkeypatch):
    """`ydotool type` tippt Strings — es braucht dafür keine eigene Keycode-Tabelle.

    Ein Zeichen, das die X-Keymap nicht kennt (z. B. 'ä' auf einem US-Layout), wird
    deshalb nicht abgelehnt: ydotool erzeugt den Text selbst. Das ist der Unterschied
    zu `ydotool key`, das den Keycode unbedingt braucht.
    """
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "ydotool")
    monkeypatch.setattr(HostSessionRunner, "_run", _fake_run)
    argv = runner._live_window_argv(Action(kind="type_text", payload={"text": "ä"}), "4711")
    assert argv[:2] == ["ydotool", "type"]
    assert "ä" in argv, argv


def test_keymap_ohne_xmodmap_liefert_null(runner, monkeypatch):
    """If the keymap cannot be read, nothing is sent — never a guessed keycode."""
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "ydotool")
    monkeypatch.setattr(HostSessionRunner, "_run", lambda self, argv: (1, ""))
    assert runner._keycode_for("a") == 0


# --------------------------------------------------------------------------- #
# 4. Das Urteil des Tores wird berichtet, nicht nur benutzt
# --------------------------------------------------------------------------- #

def test_das_tor_berichtet_jeden_gemessenen_zustand(runner, monkeypatch):
    """Was gemessen wurde, ist teil des Berichts — ein Vorrat glaubt es nicht.

    `input_probe_report()` nennt pro Backend den Zustand und sagt, ob überhaupt etwas nur
    gesendet wurde. Genau dieser Bericht ist es, was eine Live-Abnahme zeigt; ein grüner
    Unit-Test ist die Fähigkeit nie.
    """
    findings = [
        ProbeResult("xdotool", ProbeState.INPUT_SENT, "rc 0, no marker back"),
        ProbeResult("ydotool", ProbeState.BACKEND_MISSING, "no socket"),
    ]
    monkeypatch.setattr(runner, "_input_probes", findings)
    monkeypatch.setattr(runner, "_input_backend", "")
    report = runner.input_probe_report()
    assert report["backend"] == ""
    assert report["delivers"] is False
    assert report["states"] == ["INPUT_SENT", "BACKEND_MISSING"]
    assert report["sent_only"] is True, "one backend sent while nothing was delivered"
    assert report["counts_as_success"] is False
    assert len(report["probes"]) == 2


def test_der_bericht_trennt_nicht_installiert_von_nur_gesendet(runner, monkeypatch):
    """Ein fehlendes Backend sendet nichts — es darf nicht wie „nur gesendet" aussehen."""
    monkeypatch.setattr(
        runner, "_input_probes",
        [ProbeResult("xdotool", ProbeState.BACKEND_MISSING, "not installed")],
    )
    monkeypatch.setattr(runner, "_input_backend", "")
    report = runner.input_probe_report()
    assert report["sent_only"] is False
    assert report["states"] == ["BACKEND_MISSING"]


def test_der_bericht_nennt_eine_fehlende_zustimmung(runner, monkeypatch):
    """Ein CONSENT_MISSING ist die Antwort der Nutzerin, nicht ein Defekt."""
    monkeypatch.setattr(
        runner, "_input_probes",
        [ProbeResult("portal", ProbeState.CONSENT_MISSING, "consent dialog")],
    )
    monkeypatch.setattr(runner, "_input_backend", "")
    report = runner.input_probe_report()
    assert report["states"] == ["CONSENT_MISSING"]
    assert report["counts_as_success"] is False
    with pytest.raises(TargetScopeRefused) as exc:
        runner._live_window_argv(Action(kind="key", payload={"key": "Return"}), "4711")
    text = str(exc.value)
    assert "consent" in text.lower()
    assert "never auto-accepted" in text


def test_das_tor_meldet_eine_unbekannte_probe_ehrlich(runner, monkeypatch):
    """Eine nicht einrichtbare Probe ist UNKNOWN — kein Vorwurf an Host oder Compositor."""
    monkeypatch.setattr(
        runner, "_input_probes",
        [ProbeResult("xdotool", ProbeState.PROBE_MISCONFIGURED, "own window not confirmed")],
    )
    monkeypatch.setattr(runner, "_input_backend", "")
    with pytest.raises(TargetScopeRefused) as exc:
        runner._live_window_argv(Action(kind="key", payload={"key": "Return"}), "4711")
    text = str(exc.value)
    assert "UNKNOWN" in text
    assert "not a host or compositor defect" in text or "not proven broken" in text


def test_ein_vorrat_wird_nicht_erfunden(runner, monkeypatch):
    """Ein einmal gemessenes Urteil wird nicht einfach weitergegeben.

    Der Schreiber dieses Tores hat gemessen, was drei Suchen pro geschriebener Taste
    kosten: jede Suche baut ein eigenes Fenster und lässt es verschwinden. Dass das Urteil
    für die Lebensdauer einer Sitzung gilt, ist Teil von ``input-backend-cache`` — bis
    dahin gilt: kein Vorrat aus einer fremden Messung.
    """
    monkeypatch.setattr(runner, "_input_backend", "xdotool")
    argv = runner._live_window_argv(Action(kind="key", payload={"key": "Return"}), "4711")
    assert argv[:2] == ["xdotool", "windowfocus"], (
        "the cached answer is the answer, and it is never re-derived from a log"
    )
