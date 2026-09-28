"""Keystroke names → evdev keycodes: the table, the measured X offset, and the refusal.

P0 defect 4 (architecture §2, "Keycode discipline"). The uinput/``ydotool`` path used to hand
**X keycodes** to the kernel: ``ydotool key 38:1 38:0`` for 'a'. That is not a harmless detail:

* 38 is the X keycode of 'a' on this host's keymap (``xmodmap -pke`` →
  ``keycode 38 = a A a A a A a A``);
* the kernel's code for 'a' is ``KEY_A = 30`` and evdev 38 is ``KEY_L`` — both from
  ``/usr/include/linux/input-event-codes.h``, which is installed on this host;
* ``ydotool key --help`` says the same thing itself: "we're using raw keycodes now … See
  `/usr/include/linux/input-event-codes.h' for available key codes (KEY_*)".

So the untranslated code pressed the **wrong key**, silently. This module is the one place the
translation lives:

* :data:`EVDEV_KEYS` — name → evdev code, taken from that Linux header (numbers cited, not
  remembered);
* :data:`MODIFIER_KEYS` — the modifiers a combo may name, in evdev space;
* :data:`X_KEYCODE_OFFSET` — the offset between this session's X keymap and the kernel codes,
  **measured on this host**: X 'a' = 38 → ``KEY_A`` = 30, and the same 8 for every measured
  line (Return 36 → 28, Control_L 37 → 29, Shift_L 50 → 42, space 65 → 57);
* :func:`translate` — the only way to a keycode sequence: a name is decomposed into modifiers
  and a base key, an uppercase letter becomes shift-hold, a combo becomes a press/release pair
  in press order, and a name that is in neither source is **refused**
  (:class:`UnknownKeyError`) — never guessed, never silently dropped.

Text is a different question and is deliberately **not** translated here: ``ydotool type`` takes
strings (its ``--help``: "Usage: type [OPTION]... [STRINGS]..."), so Unicode text goes there as
ONE argument (see ``input_backend.send_argv`` / the runner's text action).
"""

from __future__ import annotations

from dataclasses import dataclass

#: The offset between an X keymap keycode and the kernel's evdev code — measured on this host
#: (``xmodmap -pke``: X 'a' = 38 → ``KEY_A`` = 30; Return 36 → 28; Control_L 37 → 29;
#: Shift_L 50 → 42; space 65 → 57). Not a preference: X adds 8 to the kernel code.
X_KEYCODE_OFFSET = 8

#: The modifiers a combo may name, each in evdev space. Left keys where left and right differ,
#: because xdotool's plain ``ctrl``/``shift``/``alt``/``super`` mean the left one.
MODIFIER_KEYS: dict[str, int] = {
    "ctrl": 29,
    "control": 29,
    "control_l": 29,
    "ctrl_l": 29,
    "control_r": 97,
    "ctrl_r": 97,
    "shift": 42,
    "shift_l": 42,
    "shift_r": 54,
    "alt": 56,
    "alt_l": 56,
    "alt_r": 100,
    "altgr": 100,
    "iso_level3_shift": 100,
    "mode_switch": 100,
    "super": 125,
    "super_l": 125,
    "super_r": 126,
    "meta": 125,
    "meta_l": 125,
    "meta_r": 126,
    "win": 125,
    "cmd": 125,
}

#: ``KEY_LEFTSHIFT`` — the shift the uppercase rule holds.
KEY_LEFTSHIFT = MODIFIER_KEYS["shift"]


def _letters_and_functions() -> dict[str, int]:
    """The rows the header defines by physical position, not alphabetically.

    ``KEY_Q`` = 16 sits on the top row, ``KEY_A`` = 30 on the home row and ``KEY_Z`` = 44 on
    the bottom row — an evdev code names the **key**, and the header keeps the keys in keyboard
    order (Q–P, A–L, Z–M). That is exactly why the X keycode of 'a' (38) is not 'a' but 'l'.
    """
    table: dict[str, int] = {}
    table.update({letter: 16 + index for index, letter in enumerate("qwertyuiop")})
    table.update({letter: 30 + index for index, letter in enumerate("asdfghjkl")})
    table.update({letter: 44 + index for index, letter in enumerate("zxcvbnm")})
    table.update({str(digit): 1 + digit for digit in range(1, 10)})  # KEY_1 = 2 … KEY_9 = 10
    table["0"] = 11  # KEY_0 = 11 — the zero sits after the nine, not before the one
    table.update({f"f{number}": 58 + number for number in range(1, 11)})
    # KEY_F11 = 87 and KEY_F12 = 88: numlock, scrolllock and the whole keypad sit in between
    table["f11"] = 87
    table["f12"] = 88
    return table


#: The keypad (``KP*``). One entry per physical key, again by shape — in both spellings the
#: X/xdotool vocabulary uses (``KP_0`` and ``kp0``).
_KEYPAD: dict[str, int] = {
    "kp_enter": 96,
    "kpenter": 96,
    "kp_0": 82,
    "kp0": 82,
    "kp_1": 79,
    "kp1": 79,
    "kp_2": 80,
    "kp2": 80,
    "kp_3": 81,
    "kp3": 81,
    "kp_4": 75,
    "kp4": 75,
    "kp_5": 76,
    "kp5": 76,
    "kp_6": 77,
    "kp6": 77,
    "kp_7": 71,
    "kp7": 71,
    "kp_8": 72,
    "kp8": 72,
    "kp_9": 73,
    "kp9": 73,
    "kp_decimal": 83,
    "kp_dot": 83,
    "kp_period": 83,
    "kp_add": 78,
    "kp_plus": 78,
    "kp_subtract": 74,
    "kp_minus": 74,
    "kp_multiply": 55,
    "kp_asterisk": 55,
    "kp_divide": 98,
    "kp_slash": 98,
    "kp_equal": 117,
    "kp_separator": 121,
    "kp_comma": 121,
}

#: The name → evdev table. Names follow the xdotool/X keysym vocabulary, codes are the
#: ``KEY_*`` values of ``/usr/include/linux/input-event-codes.h`` (the header is installed on
#: this host; ``tests/test_evdev_keys.py`` cross-checks every code against it). One entry per
#: physical key: a **shifted** symbol is deliberately not an entry — it is a modifier question
#: ('A' is shift + 'a'), because an evdev code names the key, not what the key prints.
EVDEV_KEYS: dict[str, int] = {
    **_letters_and_functions(),
    "esc": 1,
    "escape": 1,
    "minus": 12,
    "-": 12,
    "equal": 13,
    "=": 13,
    "backspace": 14,
    "back_space": 14,
    "tab": 15,
    "bracketleft": 26,
    "[": 26,
    "bracketright": 27,
    "]": 27,
    "return": 28,
    "enter": 28,
    "semicolon": 39,
    ";": 39,
    "apostrophe": 40,
    "'": 40,
    "grave": 41,
    "`": 41,
    "backslash": 43,
    "\\": 43,
    "comma": 51,
    ",": 51,
    "period": 52,
    "dot": 52,
    ".": 52,
    "slash": 53,
    "/": 53,
    "space": 57,
    "capslock": 58,
    "caps_lock": 58,
    "numlock": 69,
    "num_lock": 69,
    "scrolllock": 70,
    "scroll_lock": 70,
    "sysreq": 99,
    "sys_req": 99,
    "print": 99,
    "linefeed": 101,
    "home": 102,
    "up": 103,
    "pageup": 104,
    "page_up": 104,
    "prior": 104,
    "left": 105,
    "right": 106,
    "end": 107,
    "down": 108,
    "pagedown": 109,
    "page_down": 109,
    "next": 109,
    "insert": 110,
    "delete": 111,
    "undo": 131,
    "again": 129,
    "copy": 133,
    "paste": 135,
    "find": 136,
    "cut": 137,
    "help": 138,
    "menu": 139,
    "select": 353,
    "compose": 127,
    "multi_key": 127,
    "102nd": 86,
    "lessgreater": 86,
    "ro": 89,
    "yen": 124,
    "pause": 119,
    "mute": 113,
    "volumedown": 114,
    "volumeup": 115,
    "nextsong": 163,
    "playpause": 164,
    "prevsong": 165,
    "stopcd": 166,
    "www": 150,
    "mail": 155,
    "calc": 140,
    "sleep": 142,
    "search": 217,
    **_KEYPAD,
}

#: The first key a keymap may name: X keycodes below this are not keys (0 is an alias for 8).
LOWEST_EVDEV_KEY = 1


class UnknownKeyError(ValueError):
    """A key name that no source knows — refused, so the caller can say so instead of guessing."""


@dataclass(frozen=True)
class KeyEvent:
    """One evdev event: a code, and whether it went down or up."""

    code: int
    pressed: bool

    def as_argv(self) -> str:
        """The ``ydotool key`` token for this single event (``30:1``, ``30:0``)."""
        return f"{self.code}:{1 if self.pressed else 0}"


def parse_x_keymap(text: str) -> dict[str, int]:
    """The unshifted keysym names of ``xmodmap -pke`` output, mapped to their X keycode.

    Only the **unshifted column** of each line is read — ``keycode 38 = a A a A a A a A``
    becomes ``{'a': 38}``. A name labels the key it types unmodified; the columns behind it are
    that key's shifted symbols, which are a modifier question ('A' = shift + 'a'), not another
    key. Reading them here would hand out the key behind a shifted symbol as if it were its own.
    """
    table: dict[str, int] = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 4 or not parts[0].startswith("keycode"):
            continue
        try:
            code = int(parts[1])
        except ValueError:
            continue
        symbol = parts[3]
        if symbol and symbol not in table:
            table[symbol] = code
    return table


def evdev_from_x_keycode(x_keycode: int) -> int:
    """An X keymap keycode → the kernel's evdev code (the measured offset).

    X 'a' = 38 → ``KEY_A`` = 30. The same 8 was measured for every line this host's keymap
    prints (Return 36 → 28, Control_L 37 → 29, Shift_L 50 → 42, space 65 → 57). A keycode
    below the offset has no evdev key and is refused rather than wrapped around.
    """
    code = int(x_keycode) - X_KEYCODE_OFFSET
    if code < LOWEST_EVDEV_KEY:
        raise UnknownKeyError(
            f"X keycode {x_keycode} is below the first evdev key (the measured offset is "
            f"{X_KEYCODE_OFFSET}) — refusing to translate it into a different key"
        )
    return code


def _table_code(name: str) -> int | None:
    """The table entry for a name — exact first, then case-insensitive (``Return``/``return``)."""
    code = EVDEV_KEYS.get(name)
    if code is None:
        code = EVDEV_KEYS.get(name.lower())
    return code


def _keymap_code(name: str, keymap: str) -> int | None:
    """The code for a name this session's keymap prints unshifted (translated by the offset)."""
    for symbol, x_keycode in parse_x_keymap(keymap).items():
        if symbol == name or symbol.lower() == name.lower():
            return evdev_from_x_keycode(x_keycode)
    return None


def keycode_for(name: str, *, keymap: str = "") -> int:
    """The evdev code of ONE key name — no modifiers, no combos, no guessing.

    Two sources, in this order: :data:`EVDEV_KEYS` (the header table), then the session's own
    keymap (``xmodmap -pke`` output) translated through :data:`X_KEYCODE_OFFSET`. A name that
    is in neither is refused with :class:`UnknownKeyError` — the caller must say which key it
    could not send, never press a code somebody hoped was right.
    """
    text = str(name or "").strip()
    if not text:
        raise UnknownKeyError("an empty key name cannot be translated")
    if "+" in text:
        raise UnknownKeyError(
            f"{text!r} is a combination, not one key — use translate() for its keycode sequence"
        )
    code = _table_code(text)
    if code is not None:
        return code
    code = _keymap_code(text, keymap)
    if code is not None:
        return code
    raise UnknownKeyError(
        f"no evdev keycode for {text!r}: it is neither in the evdev key table "
        "(/usr/include/linux/input-event-codes.h) nor unshifted in this session's keymap"
    )


def _base_events(name: str, *, keymap: str = "") -> tuple[KeyEvent, ...]:
    """The press/release of one base key, with the uppercase rule applied.

    An uppercase letter is shift-hold around the key that carries the *lowercase* label:
    'A' is ``KEY_LEFTSHIFT`` down, ``KEY_A`` (30) down/up, shift up — never 38, which is
    ``KEY_L``. The rule runs before any lookup, so the keymap's shifted column can never
    answer for an uppercase letter and drop the shift.
    """
    text = str(name or "").strip()
    if len(text) == 1 and "A" <= text <= "Z":
        code = keycode_for(text.lower(), keymap=keymap)
        return (
            KeyEvent(KEY_LEFTSHIFT, True),
            KeyEvent(code, True),
            KeyEvent(code, False),
            KeyEvent(KEY_LEFTSHIFT, False),
        )
    code = keycode_for(text, keymap=keymap)
    return (KeyEvent(code, True), KeyEvent(code, False))


def translate(name: str, *, keymap: str = "") -> tuple[KeyEvent, ...]:
    """A key name → the evdev event sequence, in press order (modifiers first, released last).

    ``'a'`` → ``30:1 30:0``. ``'A'`` → shift-hold + ``30`` (``KEY_LEFTSHIFT`` = 42).
    ``'ctrl+c'`` → ``29:1 46:1 46:0 29:0`` (``KEY_LEFTCTRL`` = 29, ``KEY_C`` = 46): each key
    pressed once, each released once, releases in reverse press order. A name with an unknown
    part — an unknown modifier, an unknown key, an empty part beside a ``+`` — raises
    :class:`UnknownKeyError`, so a caller refuses the action instead of sending a guess.
    """
    text = str(name or "").strip()
    if not text:
        raise UnknownKeyError("an empty key name cannot be translated")
    parts = [part.strip() for part in text.split("+")]
    if any(not part for part in parts):
        raise UnknownKeyError(
            f"the key {name!r} has an empty part beside a '+' — refusing to guess which key "
            "was meant"
        )
    held: list[int] = []
    for part in parts[:-1]:
        code = MODIFIER_KEYS.get(part.lower())
        if code is None:
            raise UnknownKeyError(
                f"{part!r} is not a modifier this table knows (ctrl, shift, alt, super, altgr) "
                f"— refusing the key {name!r}"
            )
        if code not in held:
            held.append(code)
    events: list[KeyEvent] = [KeyEvent(code, True) for code in held]
    events.extend(_base_events(parts[-1], keymap=keymap))
    events.extend(KeyEvent(code, False) for code in reversed(held))
    return tuple(events)


def key_event_argv(name: str, *, keymap: str = "") -> list[str]:
    """The ``ydotool key`` arguments for a name: ``<keycode>:<pressed>`` in press order.

    This is the only shape that may reach uinput: the codes are evdev (``ydotool key --help``
    cites the header), never the raw X keycode of the session's keymap.
    """
    return [event.as_argv() for event in translate(name, keymap=keymap)]


def describe(name: str, *, keymap: str = "") -> str:
    """A human-readable form of a translated key ("ctrl+c → 29:1 46:1 46:0 29:0")."""
    events = translate(name, keymap=keymap)
    return f"{name} → " + " ".join(event.as_argv() for event in events)


__all__ = [
    "EVDEV_KEYS",
    "KEY_LEFTSHIFT",
    "LOWEST_EVDEV_KEY",
    "MODIFIER_KEYS",
    "UnknownKeyError",
    "X_KEYCODE_OFFSET",
    "KeyEvent",
    "describe",
    "evdev_from_x_keycode",
    "key_event_argv",
    "keycode_for",
    "parse_x_keymap",
    "translate",
]
