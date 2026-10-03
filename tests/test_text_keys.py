"""Char-accurate text typing: every character resolved on the SESSION LAYOUT, never dropped.

The measured defect this file is built around (calc-live-acceptance 072b98f, re-measured in the
live survey 2026-10-03, logs/input-charset-survey/): `ydotool type` maps characters on its own
built-in virtual keyboard and SILENTLY DROPS every character it cannot map — rc stays 0 and the
target receives a corrupted string ('=2+2' arrived as '22' in the acceptance; today's survey:
letters/digits/ASCII punctuation arrive, but 'äöüß', 'é', '€', '世жة' vanish, rc 0).

The fix runs the other direction: each character is resolved on the *session's own keymap*
(``xmodmap -pke``, the layout the receiving stack actually applies) and typed as evdev
press/release events with shift/AltGr holds — the same discipline the key path already has.
A character the layout cannot produce is refused up front with its name, never silently
dropped: a typed string that reaches the target complete is the only thing the productive
path may claim to have typed.
"""

from __future__ import annotations

import pytest

from skaldir.evdev_keys import UnknownKeyError
from skaldir.text_keys import (
    UnmappableTextError,
    char_placements,
    text_event_argv,
    text_key_events,
)

#: Real lines from THIS session's ``xmodmap -pke`` (measured 2026-10-03,
#: full keymap: logs/input-charset-survey/xmodmap-us-20261003.txt). Not invented: the same
#: lines name the levels 1–4 meaning unshifted/shift/AltGr/shift+AltGr, and this "us" layout
#: does carry the German umlauts and ß on the AltGr level — `ydotool type` drops them anyway,
#: which is why the mapping refuses nothing that the LAYOUT can really type.
KEYMAP = (
    "keycode  11 = 2 at 2 quotedbl twosuperior oneeighth 2 at\n"
    "keycode  16 = 7 ampersand 7 slash braceleft seveneighths 7 ampersand\n"
    "keycode  20 = minus underscore ssharp question backslash questiondown U1E9E minus underscore\n"
    "keycode  21 = equal plus dead_acute dead_grave dead_cedilla dead_ogonek equal plus\n"
    "keycode  23 = Tab ISO_Left_Tab Tab ISO_Left_Tab Tab ISO_Left_Tab\n"
    "keycode  24 = q Q q Q at Greek_OMEGA q Q\n"
    "keycode  26 = e E e E EuroSign EuroSign e E\n"
    "keycode  34 = bracketleft braceleft udiaeresis Udiaeresis dead_diaeresis dead_abovering\n"
    "keycode  36 = Return NoSymbol Return NoSymbol Return\n"
    "keycode  38 = a A a A ae AE a A\n"
    "keycode  40 = d D d D eth ETH d D\n"
    "keycode  47 = semicolon colon odiaeresis Odiaeresis dead_doubleacute dead_belowdot\n"
    "keycode  48 = apostrophe quotedbl adiaeresis Adiaeresis dead_circumflex dead_caron\n"
    "keycode  50 = Shift_L NoSymbol Shift_L NoSymbol Shift_L\n"
    "keycode  52 = z Z z Z z Z z Z\n"
    "keycode  54 = c C c C cent copyright c C\n"
    "keycode  56 = b B b B leftdoublequotemark leftsinglequotemark b B\n"
    "keycode  59 = comma less comma semicolon periodcentered multiply comma less\n"
    "keycode  60 = period greater period colon U2026 division period greater\n"
    "keycode  61 = slash question minus underscore endash emdash slash question\n"
    "keycode  65 = space NoSymbol space NoSymbol space\n"
    "keycode 108 = Alt_R NoSymbol ISO_Level3_Shift\n"
)


def test_die_rechner_ziffernpunkte_sind_im_layout_ablesbar():
    from skaldir.text_keys import Placement

    placements = char_placements(KEYMAP)
    assert placements["2"] == Placement(11, 1)
    assert placements["a"] == Placement(38, 1)
    assert placements["="] == Placement(21, 1), "the acceptance marker '=' is unshifted on this key"
    assert placements["+"] == Placement(21, 2), "shifted '+' is the same key with the shift level"
    assert placements["A"] == Placement(38, 2), "uppercase letters resolve on the same key"
    assert placements["ä"] == Placement(48, 3), "the level-3 umlauts of this layout are real"
    assert placements["ß"] == Placement(20, 3)
    assert placements["ü"] == Placement(34, 3)
    assert placements["@"] == Placement(11, 2), "@ is the shifted level of the '2' key"
    assert "首" not in placements and "€" not in placements, (
        "chars this layout cannot produce must be missing — a level the layout has no key for "
        "is a refusal, never a guessed key"
    )


def test_die_einfachste_ebene_gewinnt_nicht_die_erste_zeile():
    """Real measured trap (whole keymap, 2026-10-03): 'equal' appears TWICE — level 4 of the
    '0' key (keycode 19, EARLIER in the file) and level 1 of keycode 21. First-line-wins
    would type '=' as AltGr+Shift+0; the live debug printed exactly that (')' stand-in).
    The plain level wins over any deeper level, wherever the line sits.
    """
    from skaldir.text_keys import Placement

    real = KEYMAP + (
        "keycode  19 = 0 parenright 0 equal braceright degree 0 parenright\n"
    )
    assert char_placements(real)["="] == Placement(21, 1), (
        "the plain '=' key must win over the level-4 duplicate"
    )
    assert text_event_argv("=", keymap=real) == ["13:1", "13:0"]


def test_die_hoehlen_5_plus_werden_nie_als_altgr_gelesen():
    """Level 5+ is a second group (Mode_switch), NOT reachable by holding AltGr."""
    from skaldir.text_keys import Placement

    placements = char_placements(KEYMAP)
    assert "€" not in placements, "EuroSign sits at level 5 of this layout — not typeable by us"
    assert "7" in placements and placements["7"] == Placement(16, 1), "plain levels keep their key"


def test_ascii_text_wird_zu_zeichengenauen_key_erlebnissen():
    """'=2+2' — exactly the marker the Calc acceptance dropped — is press/release per char."""
    assert text_event_argv("=2+2", keymap=KEYMAP) == [
        "13:1", "13:0",                    # '=' unshifted (evdev 13, KEY_EQUAL)
        "3:1", "3:0",                      # '2' (KEY_2 = 3)
        "42:1", "13:1", "13:0", "42:0",    # '+' = shift-hold around the same key
        "3:1", "3:0",                      # '2'
    ]


def test_umlaut_fahrt_den_altgr_halt():
    """'ä' = AltGr-hold + the 'apostrophe' key (evdev 40): the layout says so."""
    assert text_event_argv("ä", keymap=KEYMAP) == ["100:1", "40:1", "40:0", "100:0"]


def test_stufe4_haehlt_shift_und_altgr_gemeinsam():
    """Level 4 ('Ö'): both modifiers hold, the key in the middle, releases reversed."""
    assert text_event_argv("Ö", keymap=KEYMAP) == ["100:1", "42:1", "39:1", "39:0", "42:0", "100:0"]


def test_zeichen_ohne_platz_wird_verweigert_mit_namen():
    """The refusal names EVERY untypeable character — never a partial send, never a guess."""
    with pytest.raises(UnmappableTextError) as exc:
        text_key_events("2a首ж", keymap=KEYMAP)
    message = str(exc.value)
    for char in ("首", "ж"):
        assert char in message
    assert exc.value.characters == ("首", "ж")


def test_ohne_layout_bleibt_ascii_losbar_und_mehr_nicht():
    """Without a keymap the evdev table answers for its named characters (key-name semantics).

    The header table knows 'equal' = KEY_EQUAL = 13, so '=' stays typeable exactly like the
    key path already types the NAME equal. A character in neither source ('é' outside the
    Latin-1 header names) is still a refusal, not a drop.
    """
    assert text_event_argv("az09", keymap="") == [
        "30:1", "30:0", "44:1", "44:0", "11:1", "11:0", "10:1", "10:0",
    ]
    assert text_event_argv("A0", keymap="") == [
        "42:1", "30:1", "30:0", "42:0", "11:1", "11:0",
    ]
    assert text_event_argv("=", keymap="") == ["13:1", "13:0"], "header-table name semantics"
    with pytest.raises(UnmappableTextError):
        text_key_events("é", keymap="")


def test_newline_und_tab_sind_teil_vom_text():
    assert text_event_argv("a\nb\tc", keymap=KEYMAP) == [
        "30:1", "30:0",
        "28:1", "28:0",          # '\n' is Return (KEY_ENTER = 28)
        "48:1", "48:0",
        "15:1", "15:0",          # '\t' is Tab (KEY_TAB = 15)
        "46:1", "46:0",
    ]


def test_ein_leerer_text_ist_kein_senden():
    with pytest.raises(ValueError):
        text_key_events("", keymap=KEYMAP)


def test_die_transtab_bleift_fuer_key_namen_unberuehrt():
    """The new text mapping must not break the old key-name path."""
    from skaldir.evdev_keys import key_event_argv

    assert key_event_argv("a", keymap=KEYMAP) == ["30:1", "30:0"]
    assert key_event_argv("A", keymap=KEYMAP) == ["42:1", "30:1", "30:0", "42:0"]
    with pytest.raises(UnknownKeyError):
        key_event_argv("首", keymap=KEYMAP)
