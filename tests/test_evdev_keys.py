"""Keycode-Übersetzung X → evdev — der P0-Defekt 4 als Tests.

Der Host-Befund dahinter: Für 'a' wurde `ydotool key 38:1 38:0` gebaut, weil 38 der X-Keycode
von 'a' ist (`xmodmap -pke` → `keycode 38 = a A a A a A a A`). Der Kernel meint mit 38 aber
`KEY_L`; 'a' ist `KEY_A = 30` (beide aus `/usr/include/linux/input-event-codes.h`, das auf
diesem Host installiert ist). `ydotool key --help` sagt es selbst: "we're using raw keycodes
now … See `/usr/include/linux/input-event-codes.h' for available key codes (KEY_*)".

Festgehalten wird:
1. 'a' → 30 und **niemals** 38 (38 ist `KEY_L` — der Taste, die der alte Code stattdessen drückte),
2. 'A' → Shift-Halt + 30 (`KEY_LEFTSHIFT` = 42),
3. 'ctrl+c' → 29/46 als Press-Release-Paar in Drückreihenfolge (`KEY_LEFTCTRL` = 29, `KEY_C` = 46),
4. 'Return' → 28 als Paar (`KEY_ENTER` = 28),
5. ein unbekannter Name wird **verweigert** — kein geratener Code, kein stiller Fallback,
6. der X−8-Offset ist der auf diesem Host gemessene, und jede Tabellenzahl steht im Header.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from skaldir import evdev_keys as EK
from skaldir.evdev_keys import UnknownKeyError

#: Der Header der Kernel-Keycodes — die Quelle der Tabelle, nicht die Erinnerung.
KEYCODE_HEADER = Path("/usr/include/linux/input-event-codes.h")


def _header_codes() -> dict[str, int]:
    """Jedes `#define KEY_<NAME> <code>` des Headers (leer, wenn der Header fehlt).

    Zeilen mit nachgestelltem Kommentar (`#define KEY_UNDO 131 /* AC Undo */`) und Hex-Werte
    (`#define KEY_SELECT 0x161`) zählen mit — der Header schreibt beides.
    """
    if not KEYCODE_HEADER.is_file():
        return {}
    codes: dict[str, int] = {}
    for line in KEYCODE_HEADER.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[0].startswith("#define") or not parts[1].startswith("KEY_"):
            continue
        try:
            codes[parts[1]] = int(parts[2], 0)
        except ValueError:
            continue
    return codes


pytestmark_skip_without_header = pytest.mark.skipif(
    not KEYCODE_HEADER.is_file(),
    reason="the keycode header is not installed on this machine",
)


# --------------------------------------------------------------------------- #
# 1. 'a' ist KEY_A, nicht KEY_L
# --------------------------------------------------------------------------- #

def test_a_ist_key_a_und_nicht_key_l():
    """Der Kern des Defekts: der X-Code 38 für 'a' wäre im Kernel `KEY_L`."""
    assert EK.keycode_for("a") == 30
    assert EK.keycode_for("a") != 38, "38 is the X keycode of 'a', not the evdev one"
    assert EK.keycode_for("l") == 38, "evdev 38 IS KEY_L — the key the untranslated code pressed"
    assert EK.key_event_argv("a") == ["30:1", "30:0"]


def test_die_ganze_tastatur_hat_ihre_eigenen_codes():
    """Der Header zählt die Tasten in Tastatur-Reihen, nicht alphabetisch."""
    for row, start in (("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)):
        for index, letter in enumerate(row):
            assert EK.keycode_for(letter) == start + index
    for digit, code in zip("123456789", range(2, 11), strict=True):
        assert EK.keycode_for(digit) == code
    assert EK.keycode_for("0") == 11


# --------------------------------------------------------------------------- #
# 2. Großbuchstabe = Shift-Halt + Code
# --------------------------------------------------------------------------- #

def test_grossbuchstabe_ist_shift_halt():
    assert EK.key_event_argv("A") == ["42:1", "30:1", "30:0", "42:0"]
    assert EK.key_event_argv("Z") == ["42:1", "44:1", "44:0", "42:0"]
    assert EK.keycode_for("A") == 30, "the key behind 'A' is the 'a' key; the shift is a sequence"


def test_shift_plus_kleinbuchstabe_ist_dasselbe():
    assert EK.key_event_argv("shift+c") == ["42:1", "46:1", "46:0", "42:0"]


def test_ein_shift_zeichenpaar_wird_kein_zweiter_key():
    """'A' darf nie über die Shift-Spalte der Keymap auf einen anderen Key zeigen."""
    keymap = "keycode  38 = a A a A a A a A\n"
    assert EK.key_event_argv("A", keymap=keymap) == ["42:1", "30:1", "30:0", "42:0"]


# --------------------------------------------------------------------------- #
# 3. Kombos zerfallen in Press-Release-Paare
# --------------------------------------------------------------------------- #

def test_ctrl_c_zerfaellt_in_drueckreihenfolge():
    assert EK.key_event_argv("ctrl+c") == ["29:1", "46:1", "46:0", "29:0"]


def test_mehrere_modifikatoren_werden_geschachtelt_und_einmal_gehalten():
    assert EK.key_event_argv("ctrl+shift+a") == [
        "29:1", "42:1", "30:1", "30:0", "42:0", "29:0",
    ]
    assert EK.key_event_argv("ctrl+ctrl+a") == ["29:1", "30:1", "30:0", "29:0"]


def test_jeder_gedrueckte_key_wird_wieder_losgelassen():
    events = EK.translate("ctrl+alt+Delete")
    codes = [event.code for event in events if event.pressed]
    released = [event.code for event in events if not event.pressed]
    assert codes and sorted(codes) == sorted(released), "a held key must not stay held"
    assert released == list(reversed(codes)), "releases run in reverse press order"


def test_return_ist_ein_paar():
    assert EK.key_event_argv("Return") == ["28:1", "28:0"]
    assert EK.key_event_argv("return") == ["28:1", "28:0"], "names match case-insensitively"
    assert EK.keycode_for("Return") == 28


def test_benannte_tasten_haben_die_codes_des_headers():
    assert EK.keycode_for("space") == 57
    assert EK.keycode_for("BackSpace") == 14
    assert EK.keycode_for("Tab") == 15
    assert EK.keycode_for("Escape") == 1
    assert EK.keycode_for("Delete") == 111
    assert EK.keycode_for("Home") == 102
    assert EK.keycode_for("End") == 107
    assert EK.keycode_for("Page_Up") == 104
    assert EK.keycode_for("Page_Down") == 109
    assert EK.keycode_for("Up") == 103
    assert EK.keycode_for("Down") == 108
    assert EK.keycode_for("Left") == 105
    assert EK.keycode_for("Right") == 106


def test_f11_und_f12_stehen_hinter_numlock_und_keypad():
    """Im Header liegt F11 bei 87 und F12 bei 88 — davor stehen NUMLOCK/SCROLLLOCK/Keypad."""
    assert EK.keycode_for("F11") == 87
    assert EK.keycode_for("F12") == 88
    assert EK.keycode_for("F1") == 59
    assert EK.keycode_for("F10") == 68


def test_der_keypad_hat_seine_eigenen_codes():
    assert EK.keycode_for("KP_Enter") == 96
    assert EK.keycode_for("KP_0") == 82
    assert EK.keycode_for("KP_9") == 73
    assert EK.keycode_for("KP_Add") == 78
    assert EK.keycode_for("KP_Subtract") == 74


# --------------------------------------------------------------------------- #
# 4. Unbekannt wird verweigert — nie geraten
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", [
    "", "   ", "GibtEsNicht", "hyper", "ctrl+hyper", "ctrl+", "+c", "ctrl++c", "++",
    "ctrl+GibtEsNicht", "ä", "quotedbl", "dead_acute",
])
def test_unbekannter_name_wird_verweigert(name):
    with pytest.raises(UnknownKeyError):
        EK.key_event_argv(name)
    with pytest.raises(UnknownKeyError):
        EK.keycode_for(name)


def test_die_verweigerung_nennt_den_namen():
    with pytest.raises(UnknownKeyError) as exc:
        EK.keycode_for("GibtEsNicht")
    text = str(exc.value)
    assert "GibtEsNicht" in text
    assert "input-event-codes.h" in text


def test_eine_kombination_ist_kein_einzelcode():
    with pytest.raises(UnknownKeyError):
        EK.keycode_for("ctrl+c")


def test_shift_der_rechten_hand_ist_ein_modifier():
    assert EK.key_event_argv("Shift_R+a") == ["54:1", "30:1", "30:0", "54:0"]
    assert EK.key_event_argv("alt_r+a") == ["100:1", "30:1", "30:0", "100:0"]
    assert EK.key_event_argv("super+a") == ["125:1", "30:1", "30:0", "125:0"]


# --------------------------------------------------------------------------- #
# 5. Der gemessene X−8-Offset
# --------------------------------------------------------------------------- #

def test_der_offset_ist_auf_diesem_host_gemessen():
    assert EK.X_KEYCODE_OFFSET == 8
    assert EK.evdev_from_x_keycode(38) == 30, "X 'a' = 38 → KEY_A = 30 (measured)"
    assert EK.evdev_from_x_keycode(36) == 28, "X Return = 36 → KEY_ENTER = 28 (measured)"
    assert EK.evdev_from_x_keycode(37) == 29, "X Control_L = 37 → KEY_LEFTCTRL = 29 (measured)"
    assert EK.evdev_from_x_keycode(50) == 42, "X Shift_L = 50 → KEY_LEFTSHIFT = 42 (measured)"
    assert EK.evdev_from_x_keycode(65) == 57, "X space = 65 → KEY_SPACE = 57 (measured)"


def test_ein_keycode_unter_dem_offset_wird_verweigert():
    with pytest.raises(UnknownKeyError):
        EK.evdev_from_x_keycode(EK.X_KEYCODE_OFFSET - 1)


def test_die_keymap_wird_mit_dem_offset_uebersetzt():
    """Die zweite Quelle: was auf dieser Sitzung _unshifted_ auf einem Key steht."""
    keymap = (
        "keycode   9 = Escape NoSymbol Escape\n"
        "keycode  38 = a A a A a A a A\n"
        "keycode  48 = udiaeresis Adiaeresis odiaeresis Odiaeresis\n"
    )
    assert EK.keycode_for("udiaeresis", keymap=keymap) == 40
    assert EK.parse_x_keymap(keymap) == {"Escape": 9, "a": 38, "udiaeresis": 48}


def test_die_shift_spalte_der_keymap_wird_nicht_als_key_gelesen():
    """Was nur in der Shift-Spalte steht, ist kein eigener Key — 'Ä' wäre Shift + udiaeresis."""
    keymap = "keycode  48 = udiaeresis Adiaeresis odiaeresis Odiaeresis\n"
    with pytest.raises(UnknownKeyError):
        EK.keycode_for("Adiaeresis", keymap=keymap)
    assert EK.key_event_argv("A") == ["42:1", "30:1", "30:0", "42:0"]


def test_eine_keymap_ohne_passenden_namen_aendert_nichts():
    keymap = "keycode  20 = minus underscore ssharp question\n"
    with pytest.raises(UnknownKeyError):
        EK.keycode_for("ssharp", keymap=keymap), "nur die Unshifted-Spalte zählt"
    assert EK.key_event_argv("minus", keymap=keymap) == ["12:1", "12:0"]


# --------------------------------------------------------------------------- #
# 6. Jede Tabellenzahl steht im Header
# --------------------------------------------------------------------------- #

@pytestmark_skip_without_header
def test_jede_tabellenzahl_ist_ein_key_des_headers():
    codes = _header_codes()
    assert codes, "the keycode header was read"
    defined = set(codes.values())
    missing = {name: code for name, code in EK.EVDEV_KEYS.items() if code not in defined}
    assert not missing, f"codes that are not KEY_* of the header: {missing}"


@pytestmark_skip_without_header
def test_die_werte_die_der_defekt_verwechselt_hat_stehen_so_im_header():
    codes = _header_codes()
    assert codes["KEY_A"] == 30
    assert codes["KEY_L"] == 38
    assert codes["KEY_LEFTSHIFT"] == 42
    assert codes["KEY_LEFTCTRL"] == 29
    assert codes["KEY_ENTER"] == 28
    assert codes["KEY_SPACE"] == 57
    assert codes["KEY_C"] == 46
    assert codes["KEY_Z"] == 44
    assert codes["KEY_F11"] == 87
    assert codes["KEY_F12"] == 88
    assert codes["KEY_LEFTMETA"] == 125
    assert codes["KEY_RIGHTALT"] == 100


def test_beschreiben_nennt_die_uebersetzung():
    assert EK.describe("ctrl+c") == "ctrl+c → 29:1 46:1 46:0 29:0"
