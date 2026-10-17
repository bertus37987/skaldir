"""Welches Eingabe-Backend stellt auf DIESER Sitzung Tastaturevents tatsächlich zu?

Das ist eine gemessene Frage, keine Voreinstellung. Auf dem Host des Auftraggebers
(GNOME/Wayland, Xwayland auf :0) wurde gemessen:

| Backend | rc | X-KeyEvent am Zielfenster |
|---|---|---|
| `xdotool key` (Fokus) | 0 | **keins** |
| `xdotool key --window <wid>` | 0 | **keins** |
| `xdotool type` | 0 | **keins** |
| XTEST per Xlib (`xtest.fake_input`) | 0 | **keins** |
| `ydotool key` (uinput) | 0 | **keins** |

Bei allen fünf war der X-Fokus korrekt (`get_input_focus` = Zielfenster), das Fenster
gemappt (`WM_STATE: Normal`), und `xdotool key --window <ungültig>` liefert `BadWindow` —
XTEST funktioniert also, nur die Zustellung an ein Xwayland-Fenster bleibt aus.
Dieses Modul entscheidet deshalb nicht nach „was ist installiert", sondern nach
„was hat auf dieser Sitzung zugestellt". :func:`probe` führt dazu einen **begrenzten**
Versuch aus: ein eigenes Fenster mit eigenem Empfänger, ein Zufalls-Marker, und eine
Zustellung gilt nur, wenn der Marker am eigenen Fenster im Sendezeitfenster ankam.
Solange kein Backend zustellt, meldet es den gemessenen Zustand statt einen Erfolg
vorzutäuschen.

Wichtig: Ein rc=0 ist hier NIRGENDS ein Beweis. Der einzige Beweis ist ein Event, das
das Ziel selbst meldet.

Die sechs Zustände (Architecture §2, VAL-INPUT-005) — jeder davon wird von :func:`probe`
zurückgegeben, keiner davon darf für einen anderen genommen werden:

* ``BACKEND_MISSING`` — das Backend ist nicht benutzbar (nicht installiert, Socket
  verweigert, Sendung mit rc≠0).
* ``CONSENT_MISSING`` — der zustellende Weg braucht eine ausdrückliche Nutzer-Zustimmung,
  die fehlt.
* ``PROBE_MISCONFIGURED`` — der eigene Versuchsaufbau konnte nicht bestätigt werden; das
  ist **unbekannt**, nie ein Backend- oder Host-Fehler.
* ``INPUT_SENT`` — die Sendung ging mit rc=0 raus: **Versand, nicht Zustellung**.
* ``INPUT_OBSERVED_AT_TARGET`` — der Marker kam am eigenen Zielfenster im Sendezeitfenster an.
* ``APP_EFFECT_VERIFIED`` — die Wirkung in der Zielanwendung ist nachgewiesen (das behauptet
  :func:`probe` nie).

ENFORCE: Nur ``APP_EFFECT_VERIFIED`` ist ein Erfolg. „gesendet" ist nie „zugestellt",
``PROBE_MISCONFIGURED`` ist unbekannt und nie ein Host- oder Compositor-Fehler, und
UNKNOWN zählt nie als Erfolg.
"""

from __future__ import annotations

import os
import secrets
import struct
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from .probe_target import (
    DeliveryRecord,
    ProbeBinding,
    ProbeWindow,
    correlate_delivery,
    destroy_probe_window,
    run_command,
    spawn_probe_window,
)

#: Backends in der Reihenfolge, in der sie probiert werden. Reihenfolge ist
#: Technologie-abhängig, nicht Präferenz: uinput spricht den Kernel direkt und umgeht
#: einen Compositor, der XTEST-Events für Xwayland-Clients abfängt; der Portal-Weg ist die
#: offizielle Wayland-Strecke und braucht die Zustimmung der Nutzerin.
BACKENDS: tuple[str, ...] = ("ydotool", "xdotool", "portal")

#: Env-Variablen für den ydotool-Socket, den `ydotoold` anbietet — in dieser Reihenfolge.
SOCKET_ENV = "YDOTOOL_SOCKET"
#: Wo der Mission-eigene Daemon seinen Socket ablegt (services.yaml `ydotoold`).
MISSION_SOCKET = "/tmp/skaldir-ydotoold/ydotoold.sock"
DEFAULT_SOCKET = "/run/user/1000/.ydotool_socket"

#: Der offizielle Wayland-Eingabeweg: GNOMEs RemoteDesktop-Portal, darüber EIS/libei.
PORTAL_BUS_NAME = "org.gnome.Mutter.RemoteDesktop"
PORTAL_LIBRARIES: tuple[str, ...] = ("libei.so.1", "libeis.so.1")

#: Das Prefix des eigenen Probenfensters (Titel und Protokolldatei tragen dieselbe Kennung).
PROBE_TITLE_PREFIX = "SKALDIR-PROBE-"


class ProbeState(StrEnum):
    """Die sechs unterscheidbaren Zustände der Eingabe-Kette.

    Die Reihenfolge der Member ist die Reihenfolge der Erkenntnis: Zuerst steht fest, ob ein
    Backend überhaupt benutzbar ist, dann ob die Nutzerin zustimmen muss, dann ob der
    Versuchsaufbau selbst trägt, dann was die Sendung getan hat, und erst am Ende, was das
    Ziel berichtet.

    Die beiden Eigenschaften sind die Regeln, die niemand umgehen darf:

    * :attr:`proves_delivery` — der Marker ist am eigenen Zielfenster eingetroffen
      (oder stärker). Nur das gilt als „zugestellt".
    * :attr:`counts_as_success` — nur ``APP_EFFECT_VERIFIED``. ENFORCE lässt nichts
      anderes als Erfolg durch.
    """

    BACKEND_MISSING = "BACKEND_MISSING"
    CONSENT_MISSING = "CONSENT_MISSING"
    PROBE_MISCONFIGURED = "PROBE_MISCONFIGURED"
    INPUT_SENT = "INPUT_SENT"
    INPUT_OBSERVED_AT_TARGET = "INPUT_OBSERVED_AT_TARGET"
    APP_EFFECT_VERIFIED = "APP_EFFECT_VERIFIED"

    @property
    def proves_delivery(self) -> bool:
        """Ist die Zustellung am eigenen Zielfenster bewiesen?"""
        return self in (ProbeState.INPUT_OBSERVED_AT_TARGET, ProbeState.APP_EFFECT_VERIFIED)

    @property
    def counts_as_success(self) -> bool:
        """Darf dieser Zustand als Erfolg gelten? Nur die nachgewiesene Wirkung."""
        return self is ProbeState.APP_EFFECT_VERIFIED

    @property
    def is_unknown(self) -> bool:
        """Ist das Ergebnis unbekannt (und damit kein Fehlervorwurf an ein Backend)?"""
        return self in (ProbeState.PROBE_MISCONFIGURED, ProbeState.CONSENT_MISSING)

    @property
    def verify_verdict(self) -> str:
        """Die VERIFY-Entscheidung zu diesem Zustand: ``pass`` / ``fail`` / ``unknown``."""
        if self.proves_delivery:
            return "pass"
        if self.is_unknown:
            return "unknown"
        return "fail"


#: Die Zustände, in denen eine negative Antwort neu gemessen werden muss statt zu kleben
#: (Architecture §2 „Controlled re-probe"). Hier nur benannt; die Zwischenspeicherung selbst
#: gehört zur Re-Probe-Arbeit.
NEGATIVE_STATES = frozenset({
    ProbeState.BACKEND_MISSING,
    ProbeState.CONSENT_MISSING,
    ProbeState.PROBE_MISCONFIGURED,
    ProbeState.INPUT_SENT,
})


@dataclass(frozen=True)
class ProbeResult:
    """Was eine Probe weiß — und, wichtiger, was sie **nicht** weiß.

    ``sent`` und ``state`` sind getrennte Felder, weil genau diese Verwechslung der P0-Fehler
    war: ``rc=0`` (Versand) wurde als Zustellung gelesen. ``kernel_events`` ist die einzige
    Stelle, an der ein Kernel-Ereignis auftaucht, und es geht in keine Entscheidung ein: ein
    Kernel-Event beweist die Erzeugung, nie die Ankunft (gemessen auf diesem Host).
    """

    backend: str
    state: ProbeState
    detail: str
    marker: str = ""
    sent: bool = False
    aborted: bool = False
    send_rc: int | None = None
    window: dict[str, Any] = field(default_factory=dict)
    correlation: str = ""
    records: tuple[dict[str, Any], ...] = ()
    kernel_events: int | None = None
    kernel_detail: str = ""

    # -- die beiden Fragen, die jeder Aufrufer stellt ----------------------------------- #

    @property
    def available(self) -> bool:
        """War das Backend benutzbar? (Nicht: hat es zugestellt.)"""
        return self.state is not ProbeState.BACKEND_MISSING

    @property
    def delivers(self) -> bool:
        """Hat DIESE Session zugestellt — bewiesen am eigenen Zielfenster?"""
        return self.state.proves_delivery

    @property
    def counts_as_success(self) -> bool:
        """Darf das als Erfolg zählen? Nie, solange nicht die Wirkung nachgewiesen ist."""
        return self.state.counts_as_success

    @property
    def verify_verdict(self) -> str:
        return self.state.verify_verdict

    @property
    def is_success(self) -> bool:
        """Nur :attr:`ProbeState.counts_as_success` — „gesendet" ist nie „gut"."""
        return self.state.counts_as_success

    def as_dict(self) -> dict[str, Any]:
        """The report a caller publishes: the state first, then everything behind it."""
        return {
            "backend": self.backend,
            "state": self.state.value,
            "sent": self.sent,
            "delivers": self.delivers,
            "counts_as_success": self.counts_as_success,
            "verify": self.verify_verdict,
            "marker": self.marker,
            "send_rc": self.send_rc,
            "correlation": self.correlation,
            "aborted": self.aborted,
            "window": dict(self.window),
            "records": [dict(item) for item in self.records],
            "kernel_events": self.kernel_events,
            "kernel_detail": self.kernel_detail,
            "detail": self.detail,
        }


def promote_to_effect_verified(result: ProbeResult, *, evidence: str) -> ProbeResult:
    """Der EINZIGE Weg zu ``APP_EFFECT_VERIFIED``: eine Verifikation benennt ihren Beleg.

    Ohne belegte Zustellung am Ziel gibt es keine Wirkung, die man nachweisen könnte — ein
    Versuch, diesen Zustand aus ``INPUT_SENT`` heraufzustufen, ist deshalb ein Fehler und keine
    Großzügigkeit.
    """
    if not result.state.proves_delivery:
        raise ValueError(
            f"APP_EFFECT_VERIFIED needs an observed delivery first — this result is "
            f"{result.state.value} ({result.detail})"
        )
    if not str(evidence or "").strip():
        raise ValueError("APP_EFFECT_VERIFIED needs named evidence, not an empty claim")
    return replace(
        result,
        state=ProbeState.APP_EFFECT_VERIFIED,
        detail=f"{result.detail} · effect verified: {evidence}" if result.detail else str(evidence),
    )


# --------------------------------------------------------------------------- #
# Voraussetzungen
# --------------------------------------------------------------------------- #

def _have(binary: str) -> str:
    return _which(binary) or ""


def _which(binary: str) -> str:
    import shutil

    return shutil.which(binary) or ""


def ydotool_sockets() -> list[str]:
    """Die ydotoold-Sockets in der Reihenfolge, in der sie gelten.

    Gemessen, nicht angenommen: `YDOTOOL_SOCKET` zuerst (der Daemon des Auftraggebers), dann
    der Mission-eigene Socket aus `services.yaml`, dann die Desktop-Voreinstellung. Ein Socket,
    der existiert, aber keine Verbindung annimmt, ist **kein** erreichbarer Daemon.
    """
    found: list[str] = []
    for candidate in (os.environ.get(SOCKET_ENV, "") or "", MISSION_SOCKET, DEFAULT_SOCKET):
        candidate = candidate.strip()
        if candidate and candidate not in found:
            found.append(candidate)
    return found


def active_ydotool_socket() -> str:
    """Return the first reachable ``ydotoold`` socket path, or ``""`` if none connects."""
    for sock in ydotool_sockets():
        if not Path(sock).exists():
            continue
        try:
            client = _unix_connect(sock)
        except OSError:
            continue
        try:
            client.close()
        except OSError:
            pass
        return sock
    return ""


def ensure_ydotoold() -> tuple[bool, str]:
    """Ensure a user-owned ``ydotoold`` is running when ``/dev/uinput`` is writable.

    Only auto-starts when no explicit ``YDOTOOL_SOCKET`` override is set, ``MISSION_SOCKET``
    is at its standard path (never during isolated unit-test monkeypatches), ``ydotoold`` is
    installed, and ``/dev/uinput`` is readable/writable by the current user.
    """
    sock = active_ydotool_socket()
    if sock:
        return True, f"ydotoold reachable at {sock}"
    if (
        not os.environ.get(SOCKET_ENV)
        and MISSION_SOCKET == "/tmp/skaldir-ydotoold/ydotoold.sock"
        and _have("ydotoold")
        and os.access("/dev/uinput", os.R_OK | os.W_OK)
    ):
        import subprocess

        sock_path = Path(MISSION_SOCKET)
        try:
            sock_path.parent.mkdir(parents=True, exist_ok=True)
            if sock_path.exists():
                sock_path.unlink(missing_ok=True)
            subprocess.Popen(  # nosec B603 — fixed argv, no shell
                [
                    _have("ydotoold"),
                    "--socket-path",
                    str(sock_path),
                    "--socket-own",
                    f"{os.getuid()}:{os.getgid()}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            for _ in range(15):
                time.sleep(0.05)
                if active_ydotool_socket():
                    break
        except OSError:
            pass
    return ydotool_reachable(auto_start=False)


def ydotool_reachable(*, auto_start: bool = True) -> tuple[bool, str]:
    """Kann dieser Prozess einen laufenden `ydotoold` erreichen?

    „Socket-Datei existiert, connect wird verweigert" ist ``BACKEND_MISSING``, nicht
    „erreichbar": ein Timeout oder eine Verweigerung darf nie wie eine Zustellung aussehen.
    """
    problems: list[str] = []
    for sock in ydotool_sockets():
        if not Path(sock).exists():
            problems.append(f"no socket at {sock}")
            continue
        try:
            client = _unix_connect(sock)
        except OSError as exc:
            problems.append(f"{sock} unusable ({type(exc).__name__}: {exc})")
            continue
        try:
            client.close()
        except OSError:
            pass
        return True, f"ydotoold reachable at {sock}"
    if (
        auto_start
        and not os.environ.get(SOCKET_ENV)
        and MISSION_SOCKET == "/tmp/skaldir-ydotoold/ydotoold.sock"
        and _have("ydotoold")
        and os.access("/dev/uinput", os.R_OK | os.W_OK)
    ):
        ok, reason = ensure_ydotoold()
        if ok:
            return ok, reason
    return False, "; ".join(problems)


def _unix_connect(sock: str):
    """Verbinde mit dem Socket-Typ, den der Daemon wirklich spricht.

    Gemessen 2026-09-30 auf dem Host: ydotoold bietet einen **SOCK_DGRAM**-Socket an
    (`ss -xl` → `u_dgr`); ein reiner SOCK_STREAM-Versuch bekam Errno 91 (EPROTOTYPE)
    und klassifizierte einen **lebenden** Daemon als ``BACKEND_MISSING``. Der Check
    probiert deshalb beide Typen und zählt nur einen echten connect als erreichbar.
    Eine nur noch liegende Socket-Datei täuscht das hier nicht weg: spätestens die
    Sendung endet mit rc≠0, und die Vorbedingung ist nie der Beweis.
    """
    import socket as _socket

    problems: list[str] = []
    for socket_type in (_socket.SOCK_STREAM, _socket.SOCK_DGRAM):
        client = _socket.socket(_socket.AF_UNIX, socket_type)
        client.settimeout(2.0)
        try:
            client.connect(sock)
        except OSError as exc:
            try:
                client.close()
            except OSError:
                pass
            problems.append(f"{socket_type.name}: {exc}")
            continue
        return client
    raise OSError("; ".join(problems) or f"no connect to {sock}")


def backend_available(name: str) -> tuple[bool, str]:
    """Ist das Backend benutzbar — mit dem Grund, wenn nicht.

    Das ist die *Voraussetzung*, nie der Beweis: Ein installiertes `xdotool` sagt nichts über
    Zustellung, und ein erreichbarer ydotoold-Socket auch nicht.
    """
    if name == "ydotool":
        if not _have("ydotool"):
            return False, "ydotool is not installed"
        return ydotool_reachable()
    if name == "xdotool":
        if not _have("xdotool"):
            return False, "xdotool is not installed"
        return True, "xdotool installed"
    if name == "wtype":
        if not _have("wtype"):
            return False, "wtype is not installed"
        return True, "wtype installed"
    if name == "hyprctl":
        if not _have("hyprctl"):
            return False, "hyprctl is not installed"
        if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            return False, "HYPRLAND_INSTANCE_SIGNATURE is not set"
        return True, "hyprctl reachable"
    if name == "portal":
        state, why = portal_path_state()
        return state == "ready", why
    return False, f"unknown backend {name!r}"


#: Die Zustände des offiziellen Wegs: gar nicht vorhanden / vorhanden, aber ohne Zustimmung /
#: zustimmungsbereit (noch nie von einem Probe erreicht — eine Zustimmung erteilt die Nutzerin).
PORTAL_UNAVAILABLE = "unavailable"
PORTAL_NEEDS_CONSENT = "needs-consent"
PORTAL_READY = "ready"


def portal_path_state(
    *, run_cmd: Callable[..., tuple[int, str]] | None = None
) -> tuple[str, str]:
    """Der Zustand des **offiziellen** Wayland-Eingabewegs (`org.gnome.Mutter.RemoteDesktop`/EIS).

    Zwei Dinge werden gemessen und zwei werden **nie** getan:

    * gemessen wird, ob `libei`/`libeis` überhaupt installiert sind und ob das Portal auf dem
      Session-Bus liegt;
    * nie wird ein Portal-Session erzeugt und nie ein Zustimmungsdialog angenommen oder
      geschlossen. Eine Zustimmung ist die der Nutzerin: Solange keine erteilt ist, lautet die
      ehrliche Antwort ``CONSENT_MISSING``, und der Grund sagt, was zu tun ist.

    Rückgabe ``(state, reason)`` mit ``state`` ∈ :data:`PORTAL_UNAVAILABLE` /
    :data:`PORTAL_NEEDS_CONSENT` / :data:`PORTAL_READY`. „Unavailable" heißt nur „dieser Weg
    kann auf dieser Session nicht benutzt werden", nie „der Host ist kaputt".
    """
    runner = run_cmd or run_command
    missing = [
        name for name in PORTAL_LIBRARIES
        if not any((Path(folder) / name).exists() for folder in _library_folders())
    ]
    if missing:
        return PORTAL_UNAVAILABLE, (
            f"the portal/EIS path is missing on this host ({', '.join(missing)})"
        )
    owner = runner(
        ["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus",
         "--object-path", "/org/freedesktop/DBus",
         "--method", "org.freedesktop.DBus.NameHasOwner", PORTAL_BUS_NAME],
        5.0,
    )
    if owner[0] != 0 or "true" not in (owner[1] or "").lower():
        return "unavailable", f"{PORTAL_BUS_NAME} is not on this session bus — no portal to ask"
    return "needs-consent", (
        "the RemoteDesktop portal is present and needs an explicit user consent dialog; no "
        f"consent is recorded (the probe never creates a portal session nor accepts a dialog — "
        f"grant {PORTAL_BUS_NAME} in the dialog the user sees)"
    )


def _library_folders() -> tuple[str, ...]:
    candidates = [Path("/usr/lib64"), Path("/usr/lib"), Path("/lib64")]
    extra = os.environ.get("LD_LIBRARY_PATH", "") or ""
    for part in extra.split(os.pathsep):
        if part:
            candidates.append(Path(part))
    return tuple(str(item) for item in candidates)


# --------------------------------------------------------------------------- #
# der optionale Kernel-Beobachter — getrennt berichtet, nie eine Entscheidung
# --------------------------------------------------------------------------- #

#: `struct input_event` auf x86-64: timeval (2×long) + type + code + value.
INPUT_EVENT_STRUCT = struct.Struct("@LLHHi")
INPUT_EVENT_SIZE = INPUT_EVENT_STRUCT.size


class KernelEventObserver:
    """Ein **nur lesender** Beobachter der Kernel-Eingabe-Ereignisse, getrennt berichtet.

    Warum es ihn gibt und warum er nichts entscheidet: Auf diesem Host ist gemessen, dass ein
    uinput-Event im Kernel entsteht (`type=1 code=38 value=1` — damals der **unübersetzte**
    X-Code 38 für 'a', also `KEY_L` statt `KEY_A=30`), während am Zielfenster nichts ankommt.
    Beides zu sehen ist nützlich, beides zu verwechseln ist der P0-Fehler. Der Beobachter zählt
    deshalb nur, öffnet den Knoten rein lesend, und seine Zahl landet in
    :attr:`ProbeResult.kernel_events` — ein Feld, das keine einzige Entscheidung in
    :func:`probe` liest.
    """

    def __init__(self, device: str) -> None:
        self.device = str(device or "")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._count = 0
        self.reason = ""

    def start(self) -> None:
        if not self.device:
            self.reason = "no event node given"
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            fd = os.open(self.device, os.O_RDONLY)
        except OSError as exc:
            self.reason = f"{type(exc).__name__}: {exc}"
            return
        try:
            while not self._stop.is_set():
                try:
                    chunk = os.read(fd, INPUT_EVENT_SIZE * 32)
                except OSError as exc:
                    self.reason = f"{type(exc).__name__}: {exc}"
                    return
                if not chunk:
                    return
                self._count += len(chunk) // INPUT_EVENT_SIZE
                self._stop.wait(0.05)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        return {
            "device": self.device,
            "events": int(self._count),
            "available": not self.reason,
            "reason": self.reason,
        }


# --------------------------------------------------------------------------- #
# der begrenzte Versuch
# --------------------------------------------------------------------------- #

def send_argv(backend: str, marker: str, wid: int = 0, *, keymap: str = "") -> list[str]:
    """Die argv, die den Marker als **Text** durch `backend` schickt.

    Der Marker ist ein Text-Token, weil nur ein Text den Zufallswert trägt, an dem die
    Korrelation ihn wiedererkennt. Keycodes gehören nicht hierher: Sie sind eine Übersetzungsfrage
    (X-Code → evdev) und stehen im Zustellpfad, nicht im Versuchsaufbau.

    Mit ``keymap`` (der Keymap dieser Session) verlässt der Marker die zeichengenaue Form:
    ein evdev-press/release-Paar pro Zeichen (gemessene Wurzel des Charset-Defekts,
    2026-10-03: `ydotool type` verwarf nicht abbildbare Zeichen still, rc 0 — '=2+2' kam als
    '22' an). Der Versuch misst dann die Zustellung in genau der Form, in der der
    Zustellpfad sendet. Ohne ``keymap`` bleibt der gemessene `type`-Send (die
    Marker-Alphabet-Senden dieses Probes sind ohnehin zeichengenau). Ein Zeichen, das die
    Keymap nicht tragen kann, ist eine Verweigerung mit Namen — niemals ein stiller Verlust.

    ``wid`` ist die eigene Fenster-Nummer: gesendet wird **an das eigene Fenster**, nie in das,
    was gerade zufällig den Fokus hat. Bei `xdotool` ist die Fokussierung Teil derselben argv;
    `ydotool` kennt auf Kernel-Ebene kein Fenster, dort wird unmittelbar vorher separat
    fokussiert (:func:`focus_argv`).
    """
    if backend == "ydotool":
        if keymap:
            from .text_keys import text_event_argv

            return ["ydotool", "key", *text_event_argv(marker, keymap=keymap)]
        # `ydotool type` nimmt STRINGS (geprüft mit laufendem Daemon:
        # "Usage: type [OPTION]... [STRINGS]...") — Keycodes würden als Text getippt. Und die
        # Flags sind die echten: `--key-delay` und `--key-hold` (beide Vorgabe 20 ms);
        # `--delay` kennt ydotool **nicht** (gemessen: "type: unrecognized option '--delay'",
        # und der Rückgabewert bleibt trotzdem 0 — ein falsches Flag darf nie wie Erfolg
        # aussehen). This branch stays the legacy one deliberately: without a keymap it
        # measures backend capability, not the product's character accuracy.
        return ["ydotool", "type", "--key-delay", "40", marker]
    if backend == "wtype":
        return ["wtype", "-d", "40", "--", marker]
    if wid > 0:
        # Derselbe Bau wie im Zustellpfad (`_live_window_argv`): erst Fokus auf das eigene
        # Fenster, dann der Text. Ein Marker darf nie in einem fremden Fenster landen.
        return [
            "xdotool", "windowfocus", "--sync", str(wid),
            "type", "--delay", "40", "--", marker,
        ]
    return ["xdotool", "type", "--delay", "40", "--", marker]


def focus_argv(wid: int) -> list[str]:
    """Die Fokussierung des eigenen Fensters — für Backends ohne Fensterbindung (uinput)."""
    if wid <= 0:
        return []
    return ["xdotool", "windowfocus", "--sync", str(wid)]


def new_marker() -> str:
    """Ein Zufalls-Marker: nur Kleinbuchstaben und Ziffern, damit kein Shift nötig ist."""
    return f"SKP{secrets.token_hex(6)}"


def probe(
    backend: str,
    *,
    timeout: float = 6.0,
    marker: str | None = None,
    window: ProbeWindow | None = None,
    send: Callable[[Sequence[str], float], tuple[int, str]] | None = None,
    fence: Callable[[], None] | None = None,
    stop_requested: Callable[[], bool] | None = None,
    kernel_device: str = "",
    workdir: Path | None = None,
    display: str = "",
    keymap: str = "",
    spawn: Callable[..., ProbeWindow] | None = None,
    focus: Callable[[], int] | None = None,
) -> ProbeResult:
    """Begrenzter Versuch: eigenes Fenster, eigener Empfänger, Marker-Korrelation.

    Die Reihenfolge ist die Sicherheit:

    1. **Voraussetzung** — kein benutzbares Backend ⇒ ``BACKEND_MISSING`` (mit Grund).
    2. **Zustimmung** — der offizielle Wayland-Weg braucht die Nutzerin ⇒ ``CONSENT_MISSING``.
    3. **Eigenes Fenster** — ohne bestätigte Bindung (PID+wid, gemappt, aktiviert,
       Server-Fokus, ``_NET_WM_PID``) ⇒ ``PROBE_MISCONFIGURED``, und **es wird nichts gesendet**.
    4. **Eingangssperre** — die DESK-02-Sperre läuft vor jeder Sendung; ein Abbruch bricht den
       Versuch ab und gilt als unbekannt, nie als Fehlschlag eines Backends.
    5. **Senden** — der Marker geht als Text raus; ``t_send`` wird **vorher** gestempelt.
    6. **Warten** — die Schleife fragt in jedem Tick den Empfänger und den Stop-Wunsch; ein
       physisches Doppel-Escape bricht sie innerhalb eines Ticks ab.
    7. **Korrelation** — Marker ∧ eigenes Fenster ∧ Sendezeitfenster. Trifft alles zu:
       ``INPUT_OBSERVED_AT_TARGET``. Sonst ``INPUT_SENT``, wenn rc=0 war, und
       ``BACKEND_MISSING``, wenn die Sendung selbst scheiterte.

    ``APP_EFFECT_VERIFIED`` wird hier **nie** behauptet: Eine Probe misst die Zustellung, nicht
    die Wirkung in einer Anwendung. :func:`promote_to_effect_verified` ist der einzige Weg dahin.

    Die Nahtstellen sind Prozessgrenzen, nicht die Methode selbst: ``window`` (ein schon
    gebundenes eigenes Fenster), ``send`` (das Sendewerkzeug), ``spawn`` (der Fensterprozess),
    ``focus`` (die Display-Abfrage) und ``fence``/``stop_requested`` (die Steuerung) können
    ersetzt werden. Die Reihenfolge, die Korrelation und die Zustandsabbildung sind echter Code.
    """
    token = str(marker or new_marker())
    runner = send or run_command
    spawner = spawn or spawn_probe_window
    observer: KernelEventObserver | None = (
        KernelEventObserver(kernel_device) if kernel_device else None
    )
    # The send runs on the display the probe's own window was created on — never on
    # `$DISPLAY` of the caller's shell. A runner that measures the owner's session but was
    # started from a shell that still points at the host display would otherwise focus and
    # type into a window of the *user's* desktop. That is the one thing this probe must
    # never do, so the display travels with the command.
    send_env: dict[str, str] | None = (
        {**os.environ, "DISPLAY": display} if str(display or "") else None
    )

    def deliver(argv: Sequence[str], timeout: float) -> tuple[int, str]:
        """Send on the probe's own display (``send_env`` is None → the caller's, as before)."""
        return runner(argv, timeout, env=send_env)

    if backend == "portal":
        # The official Wayland path is the one place where the missing piece is a *consent*,
        # not a capability, so its state decides between CONSENT_MISSING and BACKEND_MISSING
        # instead of both being reported as "unavailable".
        state, why = portal_path_state()
        if state != PORTAL_READY:
            return ProbeResult(
                backend,
                ProbeState.CONSENT_MISSING if state == PORTAL_NEEDS_CONSENT
                else ProbeState.BACKEND_MISSING,
                why,
                marker=token,
            )
    else:
        available, why = backend_available(backend)
        if not available:
            return ProbeResult(backend, ProbeState.BACKEND_MISSING, why, marker=token)
        if backend == "ydotool" and not (send_env or os.environ).get(SOCKET_ENV):
            sock = active_ydotool_socket() or (
                MISSION_SOCKET if Path(MISSION_SOCKET).exists() else ""
            )
            if sock:
                send_env = {**(send_env or os.environ), SOCKET_ENV: sock}

    own = window if window is not None else spawner(
        run=token, workdir=workdir, display=display, focus=focus,
    )
    window_report = own.binding.as_dict()
    try:
        if not own.binding.confirmed:
            return ProbeResult(
                backend,
                ProbeState.PROBE_MISCONFIGURED,
                f"the probe window was not confirmed ({own.binding.reason}) — nothing was sent; "
                "this is an unknown probe setup, not a backend defect",
                marker=token,
                window=window_report,
            )

        if fence is not None:
            try:
                fence()
            except Exception as exc:  # noqa: BLE001 — jede Sperrenverletzung heißt „nicht senden"
                return ProbeResult(
                    backend,
                    ProbeState.PROBE_MISCONFIGURED,
                    f"the DESK-02 input fence refused the probe before sending ({exc}) — the "
                    "probe ran no measurement, so the backend is neither confirmed nor blamed",
                    marker=token,
                    window=window_report,
                    aborted=True,
                )

        argv = send_argv(backend, token, own.binding.wid, keymap=str(keymap or ""))
        if observer is not None:
            observer.start()
        if backend != "xdotool":
            # uinput kennt kein Fenster: unmittelbar vor der Sendung bekommt das eigene
            # Fenster den Fokus, damit der Marker nicht in einem fremden landet.
            focus_argv_now = focus_argv(own.binding.wid)
            if focus_argv_now:
                deliver(focus_argv_now, timeout)
        t_send = time.time()
        send_rc, send_output = deliver(argv, timeout)
        sent = send_rc == 0

        records: list[DeliveryRecord] = []
        correlation = ""
        deadline = time.monotonic() + float(timeout)
        while True:
            records = own.records
            verdict = correlate_delivery(
                records, marker=token, wid=own.binding.wid, t_send=t_send, timeout=timeout
            )
            correlation = verdict.reason
            if verdict.delivered:
                break
            if stop_requested is not None and stop_requested():
                return ProbeResult(
                    backend,
                    ProbeState.PROBE_MISCONFIGURED,
                    "the probe was stopped while waiting for its own target — no measurement, "
                    "so the backend is neither confirmed nor blamed",
                    marker=token,
                    window=window_report,
                    sent=sent,
                    send_rc=send_rc,
                    correlation=correlation,
                    aborted=True,
                )
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)

        kernel_report = observer.stop() if observer is not None else None
        if verdict.delivered:
            state = ProbeState.INPUT_OBSERVED_AT_TARGET
            detail = (
                f"{argv[0]} sent the marker (rc {send_rc}) and the probe window reported it: "
                f"{correlation}"
            )
        elif sent:
            state = ProbeState.INPUT_SENT
            detail = (
                f"{argv[0]} returned rc {send_rc} but the probe window reported no marker "
                f"({correlation}) — sent is dispatch, not delivery"
            )
        else:
            state = ProbeState.BACKEND_MISSING
            detail = f"{argv[0]} failed with rc {send_rc} ({(send_output or '').strip()[:160]})"
        return ProbeResult(
            backend,
            state,
            detail,
            marker=token,
            sent=sent,
            send_rc=send_rc,
            window=window_report,
            correlation=correlation,
            records=tuple(
                {
                    "wid": item.wid,
                    "t": item.t,
                    "keycode": item.keycode,
                    "keysym": item.keysym,
                    "char": item.char,
                }
                for item in (verdict.records or records[-8:])
            ),
            kernel_events=(kernel_report or {}).get("events"),
            kernel_detail=(kernel_report or {}).get("reason", ""),
        )
    finally:
        if observer is not None:
            observer.stop()
        destroy_probe_window(own)


# --------------------------------------------------------------------------- #
# choose(): das erste zustellende Backend — oder der ehrliche Befund
# --------------------------------------------------------------------------- #

#: Welcher Zustand eine Gesamtaussage trägt, wenn KEIN Backend zugestellt hat. Die Reihenfolge
#: ist eine Ehrlichkeitsregel: Eine nicht eingerichtete Probe sagt nichts über ein Backend
#: (unbekannt), eine fehlende Zustimmung ist ein echter Halt, ein rc=0 ohne Ziel-Event ist ein
#: gemessener Fehlschlag, und „nichts installiert" ist die kleinste Aussage.
_AGGREGATE_ORDER: tuple[ProbeState, ...] = (
    ProbeState.PROBE_MISCONFIGURED,
    ProbeState.CONSENT_MISSING,
    ProbeState.INPUT_SENT,
    ProbeState.BACKEND_MISSING,
)


def choose(
    *,
    timeout: float = 6.0,
    marker: str | None = None,
    backends: Sequence[str] | None = None,
    **kwargs: Any,
) -> tuple[ProbeResult, list[ProbeResult]]:
    """Das erste Backend, das wirklich zustellt — oder der blockierte Befund.

    Die Reihenfolge der Untersuchung ist :data:`BACKENDS`; ein Backend, das zustellt, beendet
    die Suche. Wenn keines zustellt, trägt die Antwort den **sagenwirksamsten** Zustand der
    Einzelbefunde (siehe :data:`_AGGREGATE_ORDER`) und nie ein „Erfolg".
    """
    candidates = tuple(backends) if backends is not None else BACKENDS
    findings = [
        probe(name, timeout=timeout, marker=marker, **kwargs) for name in candidates
    ]
    for found in findings:
        if found.delivers:
            return found, findings
    seen = {item.state for item in findings}
    state = next((item for item in _AGGREGATE_ORDER if item in seen), ProbeState.INPUT_SENT)
    if state is ProbeState.PROBE_MISCONFIGURED:
        detail = (
            "no input backend delivered a key event, and at least one probe was not correctly "
            "set up — that is unknown, not a host or compositor defect: "
            + "; ".join(f"{f.backend}: {f.detail}" for f in findings)
        )
    elif state is ProbeState.CONSENT_MISSING:
        detail = (
            "no input backend delivered a key event; the delivering path (the RemoteDesktop "
            "portal) needs an explicit user consent that is not recorded — the consent dialog "
            "is the user's to answer: "
            + "; ".join(f"{f.backend}: {f.detail}" for f in findings)
        )
    elif state is ProbeState.INPUT_SENT:
        detail = (
            "no input backend delivered a key event on this session — every usable backend "
            "returned rc 0 while its own probe window reported no marker (sent is not "
            "delivered): " + "; ".join(f"{f.backend}: {f.detail}" for f in findings)
        )
    else:
        detail = (
            "no input backend is usable on this session (nothing installed, no reachable "
            "daemon): " + "; ".join(f"{f.backend}: {f.detail}" for f in findings)
        )
    return (
        ProbeResult(backend="none", state=state, detail=detail, marker=str(marker or "")),
        findings,
    )


__all__ = [
    "BACKENDS",
    "DEFAULT_SOCKET",
    "MISSION_SOCKET",
    "PORTAL_BUS_NAME",
    "ProbeBinding",
    "ProbeResult",
    "ProbeState",
    "ProbeWindow",
    "SOCKET_ENV",
    "active_ydotool_socket",
    "backend_available",
    "choose",
    "correlate_delivery",
    "ensure_ydotoold",
    "focus_argv",
    "new_marker",
    "portal_path_state",
    "probe",
    "promote_to_effect_verified",
    "send_argv",
    "ydotool_reachable",
    "ydotool_sockets",
]
