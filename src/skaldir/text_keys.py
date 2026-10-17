"""Text → char-accurate evdev key events, resolved on the SESSION's own layout.

The measured defect (calc-live-acceptance 072b98f; re-measured in the live survey of
2026-10-03, ``logs/input-charset-survey/``): ``ydotool type`` maps characters against its own
built-in virtual keyboard and **silently drops** every character it cannot map — rc stays 0,
so nothing reports the loss, and the target receives a corrupted string: '=2+2' arrived as
'22', 'äöüß', 'é', '€' and '世жة' arrived as nothing at all.

The honest fix runs the other direction: every character is resolved on the *session's real
keymap* (``xmodmap -pke`` — the layout the receiving stack actually applies) and typed as
evdev press/release events with shift/AltGr holds, the same discipline the key path already
has (:mod:`skaldir.evdev_keys`). A character the layout cannot produce is **refused up front**
with its name — never silently dropped, never guessed onto a key. That keeps the six-state
honesty intact: a send that claims "typed" has a definition of which characters it typed.

* The keymap is consulted with the measured X↔evdev semantics already established in
  :mod:`skaldir.evdev_keys`: X keycode − 8 = evdev code
  (:func:`skaldir.evdev_keys.evdev_from_x_keycode`).
* Level columns 1–4 only: plain/shift/AltGr/shift+AltGr. Columns 5+ belong to a second
  group (Mode_switch) that a plain key sequence cannot reach — using them would be a guess.
* Modifier codes come from :data:`MODIFIER_KEYS` (shift = 42, altgr = 100), the same
  constants the combination path holds.
"""

from __future__ import annotations

from dataclasses import dataclass

from .evdev_keys import MODIFIER_KEYS, KeyEvent, evdev_from_x_keycode


class UnmappableTextError(ValueError):
    """Text contains characters this layout cannot produce — refused, with every name.

    This is the exact honesty rule the silent drop broke: a caller that wants the text
    typed must learn WHICH characters cannot be typed before anything reaches the target,
    so the productive path never reports a send the target can silently corrupt.
    """

    def __init__(self, characters: tuple[str, ...]) -> None:
        self.characters = tuple(characters)
        names = ", ".join(repr(char) for char in self.characters)
        super().__init__(
            f"{len(self.characters)} character(s) cannot be typed on this session's layout "
            f"(neither the plain, shifted, AltGr nor shift+AltGr level of its keymap prints "
            f"them) — refusing instead of silently dropping them: {names}"
        )


@dataclass(frozen=True)
class Placement:
    """Where the layout prints one character: its key and the levels to hold."""

    x_keycode: int
    level: int  # 1 plain, 2 shift, 3 AltGr, 4 shift+AltGr


def _keysym_char(name: str) -> str:
    """The character a keysym NAME stands for, or ``""`` when it is not a printable one.

    Resolved through python-xlib's keysym table, the same table the X server itself uses.
    Latin-1 keysyms ARE their character codes; the supplementary plane marks ``0x1000000``
    plus the code point. Modifier names (``Shift_L``), dead keys and unsignable names
    (``NoSymbol``, ``EuroSign`` in this xlib's table) answer ``""`` — no character.
    """
    from Xlib import XK

    keysym = int(XK.string_to_keysym(name) or 0)
    if 0x20 <= keysym <= 0x7E or 0xA0 <= keysym <= 0xFF:
        return chr(keysym)
    if 0x1000000 <= keysym <= 0x1FFFFF:
        return chr(keysym - 0x1000000)
    return ""


def _active_xkb_group() -> int:
    """Return the 0-based active XKB group index when GNOME Wayland has multiple layouts.

    On GNOME Wayland, Xwayland's ``xmodmap -pke`` prints Group 1 in columns 0..1 and Group 2
    in columns 2..3 (with AltGr in 4..5), ordered by ``org.gnome.desktop.input-sources sources``
    (e.g. ``[('xkb', 'us'), ('xkb', 'de')]``), whereas Mutter applies the layout at the front of
    ``mru-sources`` (e.g. ``[('xkb', 'de'), ('xkb', 'us')]``) to uinput/ydotool events.
    """
    import os
    import re
    import subprocess

    override = os.environ.get("SKALDIR_XKB_GROUP", "").strip()
    if override.isdigit():
        return int(override)
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland"):
        return 0
    try:
        mru_proc = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.input-sources", "mru-sources"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        src_proc = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.input-sources", "sources"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if mru_proc.returncode == 0 and src_proc.returncode == 0:
            mru = re.findall(r"\('xkb',\s*'([^']+)'\)", mru_proc.stdout or "")
            srcs = re.findall(r"\('xkb',\s*'([^']+)'\)", src_proc.stdout or "")
            if mru and srcs and mru[0] in srcs:
                return srcs.index(mru[0])
    except Exception:  # noqa: BLE001
        pass
    return 0


def _keymap_depicts_multi_group_layout(keymap: str) -> bool:
    """True only when the keymap is a full multi-group dump where Group 1 and Group 2 differ
    on letter keys (e.g. QWERTY ``y`` vs QWERTZ ``z`` on keycode 29 or 52)."""
    for line in str(keymap or "").splitlines():
        parts = line.split()
        if len(parts) >= 7 and parts[0] == "keycode" and parts[1] in ("29", "52"):
            if parts[3].lower() != parts[5].lower():
                return True
    return False


def char_placements(keymap: str, *, group: int | None = None) -> dict[str, Placement]:
    """The characters this keymap can type, and where: which key, which level.

    Only :data:`LEVELS` count — a line's columns 5+ are a second group the plain key
    sequence cannot reach; ignoring them keeps the mapping honest about what holding ones
    keys can produce. The SIMPLEST level wins: for every character the lowest level
    (plain beats shift beats AltGr beats shift+AltGr), across all lines and columns. This
    is measured, not sentimental — this session's keymap prints 'equal' a second time at
    level 4 of the '0' key (``keycode 19 = 0 parenright 0 equal …``, line 12, before the
    plain 'equal' on keycode 21): first-line-wins would have typed '=' as
    AltGr+Shift+0 and the marker comparison would have quietly compared against a
    mis-typed stand-in instead of the character.
    """
    active_group = _active_xkb_group() if group is None else int(group)
    use_group_offset = active_group > 0 and _keymap_depicts_multi_group_layout(keymap)
    offset = active_group * 2 if use_group_offset else 0

    placements: dict[str, Placement] = {}
    for line in str(keymap or "").splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] != "keycode":
            continue
        try:
            code = int(parts[1])
        except ValueError:
            continue
        rhs = parts[3:]
        if offset and len(rhs) > offset + 1:
            columns = rhs[offset : offset + LEVELS]
        else:
            columns = rhs[:LEVELS]
        for level, name in enumerate(columns, start=1):
            char = _keysym_char(name)
            if not char:
                continue
            previous = placements.get(char)
            if previous is None or level < previous.level:
                placements[char] = Placement(x_keycode=code, level=level)
    return placements


#: The levels one group offers: plain, shift, AltGr, shift+AltGr.
LEVELS = 4

_LEVEL_KEYS: dict[int, tuple[int, ...]] = {
    1: (),
    2: (MODIFIER_KEYS["shift"],),
    3: (MODIFIER_KEYS["altgr"],),
    4: (MODIFIER_KEYS["altgr"], MODIFIER_KEYS["shift"]),
}

_CONTROL_KEYS = {"\n": "Return", "\r": "Return", "\t": "Tab"}


def text_key_events(text: str, *, keymap: str = "") -> tuple[KeyEvent, ...]:
    """Text → the evdev press/release sequence that types it, character-accurate.

    ``'=2+2'`` with a keymap that prints '+' shifted on the '=' key is
    ``shift-hold ``:1``/``:0`` around :func:`evdev_from_x_keycode` of that key — exactly the
    sequence the old ``ydotool type`` silently dropped. ``'\n'`` and ``'\t'`` map to Return
    and Tab. Every character without a placement (an unsupported Unicode character, a control
    character the layout has no key for) goes into ONE :class:`UnmappableTextError` naming
    them all, raised BEFORE any event is built — partial sends cannot happen, because the
    refusal precedes the first token.
    """
    body = str(text or "")
    events: list[KeyEvent] = []
    unknown: list[str] = []
    if body:
        placements = char_placements(keymap)
        from .evdev_keys import UnknownKeyError, keycode_for, translate

        for char in body:
            if char in _CONTROL_KEYS:
                try:
                    code = keycode_for(_CONTROL_KEYS[char], keymap=keymap)
                except UnknownKeyError:
                    unknown.append(char)
                    continue
                events.extend((KeyEvent(code, True), KeyEvent(code, False)))
                continue
            placement = placements.get(char)
            if placement is None:
                # No keymap placement: the evdev table is the second source — the SAME
                # translation the key path uses, not a guess table of its own. A character
                # that is in neither source ends up in the refusal.
                try:
                    events.extend(translate(char, keymap=keymap))
                except UnknownKeyError:
                    unknown.append(char)
                continue
            events.extend(
                KeyEvent(code, True) for code in _LEVEL_KEYS[placement.level]
            )
            evdev = evdev_from_x_keycode(placement.x_keycode)
            events.extend((KeyEvent(evdev, True), KeyEvent(evdev, False)))
            events.extend(
                KeyEvent(code, False) for code in reversed(_LEVEL_KEYS[placement.level])
            )
    if unknown:
        raise UnmappableTextError(tuple(unknown))
    if not body:
        raise ValueError(
            "an empty text has no characters to type — the caller decides what an empty "
            "payload means, never a guessed empty key sequence"
        )
    return tuple(events)


def text_event_argv(text: str, *, keymap: str = "") -> list[str]:
    """The ``ydotool key`` token list for one text (``'42:1'``, ``'13:0'`` …) — press order.

    Tokens only: the caller composes its own ``ydotool``/``key`` prefix, the same seam the
    key path uses. The X↔evdev offset (:data:`X_KEYCODE_OFFSET`) comes from
    :mod:`skaldir.evdev_keys`, so the keymap's X keycodes never reach uinput raw.
    """
    return [event.as_argv() for event in text_key_events(text, keymap=keymap)]


__all__ = [
    "LEVELS",
    "Placement",
    "UnmappableTextError",
    "char_placements",
    "text_event_argv",
    "text_key_events",
]
