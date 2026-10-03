"""Live-Tastaturpfad: blockiert melden statt einen nicht zugestellten Tastendruck behaupten.

Der Host-Befund, auf dem diese Tests stehen (alle gemessen, 2026-09-26):
`xdotool key`, `xdotool key --window <wid>`, `xdotool type`, XTEST per Xlib und
`ydotool key` liefern rc=0, während das Zielfenster **kein** Key-Event meldet. X-Fokus,
`WM_STATE` und Aktivierung sind zu diesem Zeitpunkt korrekt, und
`xdotool key --window <ungültig>` liefert sehr wohl `BadWindow` — XTEST funktioniert
also, nur die Zustellung an ein Xwayland-Fenster bleibt aus.

Festgehalten wird:
1. Ein Tastendruck wird nur gebaut, wenn ein Backend wirklich zugestellt hat.
2. Der uinput-Pfad (`ydotool`) übersetzt Key-Namen in **evdev**-Codes (X-'a' = 38 würde im
   Kernel `KEY_L` drücken; 'a' ist `KEY_A = 30`) — Großschreibung als Shift-Halt, Kombos als
   Press/Release-Paare in Drückreihenfolge.
3. Ein Key ohne Keycode wird abgelehnt, nicht geraten.
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
# 2. Der uinput-Pfad übersetzt in EVDEV-Codes, nicht in X-Codes
# --------------------------------------------------------------------------- #

#: Die X-Keymap dieser Sitzung, so wie `xmodmap -pke` sie druckt. Der X-Code 38 für 'a'
#: steht hier ZU RECHT — die Keymap redet in X-Codes. Der P0-Fehler war, diesen Wert
#: unübersetzt an uinput zu übergeben: 38 ist im Kernel `KEY_L`, 'a' ist `KEY_A = 30`.
KEYMAP = (
    "keycode   9 = Escape NoSymbol Escape NoSymbol Escape\n"
    "keycode  11 = 2 at 2 quotedbl twosuperior oneeighth 2 at\n"
    "keycode  21 = equal plus dead_acute dead_grave dead_cedilla dead_ogonek equal plus\n"
    "keycode  36 = Return NoSymbol Return NoSymbol Return\n"
    "keycode  38 = a A a A a A a A\n"
    "keycode  52 = z Z z Z z Z z Z\n"
)


def _fake_run(self, argv):
    return 0, KEYMAP


def test_ydotool_uebersetzt_key_namen_in_evdev_codes(runner, monkeypatch):
    """'a' geht als `KEY_A = 30` raus und **niemals** als X-Code 38 (das wäre `KEY_L`).

    `ydotool key --help` nennt die Quelle selbst: "we're using raw keycodes now … See
    `/usr/include/linux/input-event-codes.h' for available key codes (KEY_*)".
    """
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "ydotool")
    monkeypatch.setattr(HostSessionRunner, "_run", _fake_run)
    argv = runner._live_window_argv(Action(kind="key", payload={"key": "a"}), "4711")
    assert argv[0] == "ydotool"
    assert argv[1] == "key"
    assert argv[2:] == ["30:1", "30:0"], argv
    assert "38:1" not in argv and "38:0" not in argv, (
        "38 is the X keycode of 'a' — in the kernel it is KEY_L, not KEY_A"
    )


def test_kein_roher_xmodmap_code_erreicht_uinput(runner, monkeypatch):
    """Was die Keymap hergibt, wird übersetzt: X-Code − 8 = evdev-Code (hier gemessen)."""
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "ydotool")
    monkeypatch.setattr(HostSessionRunner, "_run", _fake_run)
    for name, evdev in (("a", 30), ("z", 44), ("Return", 28), ("Escape", 1)):
        argv = runner._live_window_argv(Action(kind="key", payload={"key": name}), "4711")
        assert argv[2:] == [f"{evdev}:1", f"{evdev}:0"], (name, argv)
    # und die Tabelle kennt die Tasten auch dann, wenn gar keine Keymap gelesen werden kann
    monkeypatch.setattr(HostSessionRunner, "_run", lambda self, argv: (1, ""))
    assert runner._keycode_for("a") == 30, "the evdev table is the first source, not the keymap"
    assert runner._keycode_for("GibtEsNicht") == 0, "an unknown name returns 0 — never a guess"


def test_ydotool_key_hat_press_und_release(runner, monkeypatch):
    """Ein key braucht beide Hälften; nur press hinterält die Taste gedrückt."""
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "ydotool")
    monkeypatch.setattr(HostSessionRunner, "_run", _fake_run)
    argv = runner._live_window_argv(Action(kind="key", payload={"key": "a"}), "4711")
    assert argv[2:] == ["30:1", "30:0"], argv


def test_grossbuchstabe_ist_shift_halt(runner, monkeypatch):
    """Großschreiben ist Shift-Halt (KEY_LEFTSHIFT = 42) um den Code der eigenen Taste."""
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "ydotool")
    monkeypatch.setattr(HostSessionRunner, "_run", _fake_run)
    argv = runner._live_window_argv(Action(kind="key", payload={"key": "A"}), "4711")
    assert argv[2:] == ["42:1", "30:1", "30:0", "42:0"], argv


def test_kombos_zerfallen_in_press_release(runner, monkeypatch):
    """ctrl+c → 29:1 46:1 46:0 29:0 (KEY_LEFTCTRL = 29, KEY_C = 46), in Drückreihenfolge."""
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "ydotool")
    monkeypatch.setattr(HostSessionRunner, "_run", _fake_run)
    argv = runner._live_window_argv(Action(kind="key", payload={"key": "ctrl+c"}), "4711")
    assert argv[2:] == ["29:1", "46:1", "46:0", "29:0"], argv


def test_ydotool_text_geht_als_zeichengenaue_key_folge_raus(runner, monkeypatch):
    """Text goes as per-character evdev pairs, never again as a silently-dropped string.

    Gemessene Wurzel (calc-live-acceptance 072b98f, wiederholt in der Live-Vermessung
    2026-10-03): `ydotool type` legt die Zeichen an eine eigene virtuelle Tastatur an und
    VERWIRFT jedes Zeichen, das es nicht abbilden kann — rc 0, keine Meldung, das Ziel
    bekommt einen verfälschten Text ('=2+2' kam als '22' an, 'äöüß' als gar nichts). Die
    argv ["ydotool","type","30:1","30:0"] wäre Keycodes als Text gewesen, und der reine
    String-Send trägt die Stille: Beides passt nicht. Der Zustellpfad baut die Zeichen
    daher SELBST: Keymap-Auflösung + evdev-Paar pro Zeichen (`ydotool key`).
    """
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "ydotool")
    monkeypatch.setattr(HostSessionRunner, "_run", _fake_run)
    argv = runner._live_window_argv(Action(kind="type_text", payload={"text": "az"}), "4711")
    assert argv[0] == "ydotool"
    assert argv[1] == "key", argv
    assert argv[2:] == ["30:1", "30:0", "44:1", "44:0"], argv
    assert not any("type" in part for part in argv), "no silently-dropping string send"
    assert not any(part.startswith("-") for part in argv), "no guessed flags in a key sequence"


def test_text_mit_satzzeichen_kommt_zeichengenau_raus(runner, monkeypatch):
    """'=2+2' — die genau Zeichenkette, die die Calc-Abnahme als '22' ankommen sah.

    Die Keymap des Tests ist ein Auszug der echten Messung (2026-10-03,
    logs/input-charset-survey/xmodmap-us-20261003.txt): '=' unshifted auf keycode 21,
    '+' shifted auf derselben Taste.
    """
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "ydotool")
    monkeypatch.setattr(HostSessionRunner, "_run", _fake_run)
    argv = runner._live_window_argv(
        Action(kind="type_text", payload={"text": "=2+2"}), "4711"
    )
    assert argv[:2] == ["ydotool", "key"]
    assert argv[2:] == [
        "13:1", "13:0",                    # '='
        "3:1", "3:0",                      # '2'
        "42:1", "13:1", "13:0", "42:0",    # '+'
        "3:1", "3:0",                      # '2'
    ], argv


def test_xdotool_type_hat_delay_und_trenner(runner, monkeypatch):
    """Ein zu schnelles `type` überfordert langsame Ziele; -- trennt Text von Optionen."""
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "xdotool")
    argv = runner._live_window_argv(Action(kind="type_text", payload={"text": "-x"}), "4711")
    assert "--" in argv, "text that looks like an option needs the separator"
    assert "--delay" in argv


# --------------------------------------------------------------------------- #
# 3. Ein Zeichen ohne Keycode wird abgelehnt
# --------------------------------------------------------------------------- #

def test_unbekannter_key_wird_abgelehnt(runner, monkeypatch):
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "ydotool")
    monkeypatch.setattr(HostSessionRunner, "_run", _fake_run)
    with pytest.raises(TargetScopeRefused) as exc:
        runner._live_window_argv(Action(kind="key", payload={"key": "GibtEsNicht"}), "4711")
    text = str(exc.value)
    assert "no evdev keycode" in text
    assert "GibtEsNicht" in text, "the refusal names the key it could not translate"
    assert "guessed" in text


def test_text_mit_zeichen_ohne_platz_wird_verweigert(runner, monkeypatch):
    """'ä' auf einer Keymap ohne seine Ebene wird VERWEIGERT, nie still gedroppt.

    Der alte Test behauptete, ydotool "erzeugt den Text selbst" — die Live-Abnahme (072b98f)
    und die Vermessung 2026-10-03 widerlegen das messbar: der String-Send verwarf 'ä' mit
    rc 0 still. Ehrlich ist die Verweigerung MIT NAMEN, bevor irgendetwas erreicht — die
    Sechs-Zustands-Ehrlichkeit gilt auch hier: „getippt“ steht nie für „getippt, bis auf ...".
    """
    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "ydotool")
    monkeypatch.setattr(HostSessionRunner, "_run", _fake_run)
    with pytest.raises(TargetScopeRefused) as exc:
        runner._live_window_argv(Action(kind="type_text", payload={"text": "ä"}), "4711")
    text = str(exc.value)
    assert "ä" in text, "the refusal names the character it could not type"
    assert "silently" in text or "refus" in text
    assert "8:1" not in text, "no guessed keycode in a refusal"


def test_die_keymap_wird_nur_einmal_gelesen(runner, monkeypatch):
    """Pro Sitzung wird die Keymap einmal gelesen — nicht pro Namen."""
    calls: list[list[str]] = []

    def _counting_run(self, argv):
        calls.append(list(argv))
        return 0, KEYMAP

    monkeypatch.setattr(runner, "_delivering_input_backend", lambda: "ydotool")
    monkeypatch.setattr(HostSessionRunner, "_run", _counting_run)
    runner._live_window_argv(Action(kind="key", payload={"key": "a"}), "4711")
    runner._live_window_argv(Action(kind="key", payload={"key": "z"}), "4711")
    assert calls == [["xmodmap", "-pke"]], calls


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
    """Ein entschiedenes Urteil wird geehrt, nicht aus einem Protokoll neu abgeleitet.

    Eine gesetzte Antwort (keine Messung dahinter, kein Fingerprint) steht: der nächste
    Tastendruck baut darauf, statt erneut zu messen. Dass eine **gemessene** Antwort — vor
    allem eine negative — NICHT für die Lebensdauer des Runners klebt, sondern auf
    ``invalidate_input_backend()``, Fingerprintwechsel, ``force=True`` und Fristablauf
    neu gemessen wird, hält ``tests/test_input_backend_reprobe.py`` fest.
    """
    monkeypatch.setattr(runner, "_input_backend", "xdotool")
    argv = runner._live_window_argv(Action(kind="key", payload={"key": "Return"}), "4711")
    assert argv[:2] == ["xdotool", "windowfocus"], (
        "the cached answer is the answer, and it is never re-derived from a log"
    )
