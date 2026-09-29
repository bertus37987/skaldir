"""Global emergency stop: the double-Escape detector and its input backends.

Why this module exists on its own
---------------------------------
``docs/masterplan/01-MASTERPLAN.md`` STOP-01..STOP-05 make the double-Escape an *early*
feasibility and acceptance point, not a polish item: two physical Escape presses in a short
window must pause the affected host goal run, stop further input and hand control back —
also while a browser, editor, Blender or a fullscreen app has the focus.

STOP-02 forbids depending on the model round, the voice supervisor or the TUI event loop, so
the detector is plain logic over input events and can be driven by any backend thread.
STOP-03 forbids reading the agent's own synthetic Escape as a user stop, and forbids keeping
a general key log — so the event carries an *origin*, and this module only ever looks at the
key signals it needs.

STOP-04: portal/compositor support is **checked, never assumed**. ``org.freedesktop.portal.
GlobalShortcuts`` gives focus-independent shortcuts, which does not imply that a sequence of
two unmodified Escape presses is expressible as one trigger. X11 and Wayland may need
different implementations.

STOP-05 is the honest part: when a backend cannot be *demonstrated* to deliver the double
press, the host-control mode is not released as complete. :func:`probe_backends` reports the
measured availability of each candidate backend and the concrete blocker behind every
``blocked``/``unknown`` — that report is what the release decision reads, not a claim in a
docstring.

Detector invariants (each has a test in ``tests/test_stop_signal.py``)
---------------------------------------------------------------------
* two presses inside the window fire exactly one stop; three presses fire one stop, not two
* a gap *shorter* than :attr:`DoubleEscapeDetector.min_gap_ms` is autorepeat and never counts
* an event whose origin is ``agent``/``synthetic`` never counts
* a press outside the window arms again instead of firing
* the clock is injectable, so the window is tested without sleeping
"""

from __future__ import annotations

import enum
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "Availability",
    "AVAILABILITY",
    "AVAILABILITY_VALUES",
    "BackendProbe",
    "DoubleEscapeDetector",
    "InputEvent",
    "Origin",
    "StopSignal",
    "DEFAULT_WINDOW_MS",
    "DEFAULT_MIN_GAP_MS",
    "detect",
    "probe_backends",
    "write_report",
]

#: STOP-01's proposed window. Two presses closer together than this are one deliberate
#: double press; configurable, and the value is reported rather than hidden.
DEFAULT_WINDOW_MS = 500
#: Anything faster than this is treated as keyboard autorepeat, not a second deliberate
#: press (STOP-01: "Tastatur-Autorepeat zählt nicht als zwei bewusste Drücke").
#: Measured autorepeat delay on Linux is configurable (250 ms default) and the repeat rate
#: is 10-50 ms apart, so 30 ms separates "held down" from "pressed twice" without needing
#: to read the kernel's ``/proc/sys/dev/keyboard/*`` (which this module never reads).
DEFAULT_MIN_GAP_MS = 30


class Availability(enum.StrEnum):
    """What a backend can honestly claim right now.

    ``UNKNOWN`` is not ``AVAILABLE``. It is the state STOP-05 cares about most: a backend
    whose support nobody has demonstrated must not be used to declare the stop complete.
    """

    AVAILABLE = "available"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


AVAILABILITY = Availability
AVAILABILITY_VALUES: tuple[str, ...] = tuple(a.value for a in Availability)


class Origin(enum.StrEnum):
    """Where an input event came from — the STOP-03 filter.

    ``AGENT`` covers everything Skaldir itself injects (``ydotool``, ``xdotool``, ``uinput``,
    a synthetic XTest event). A stop must only ever come from ``USER``.
    """

    USER = "user"
    AGENT = "agent"
    SYNTHETIC = "synthetic"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class InputEvent:
    """One key event, already reduced to what the detector needs.

    ``autorepeat`` mirrors the evdev ``value`` field (0 = release, 1 = press, 2 = repeat).
    Backends that cannot see a repeat flag leave it ``False`` and rely on the gap instead.
    """

    key: str = "esc"
    #: ``None`` = the caller gave no timestamp and the detector's clock is used. ``0.0``
    #: is a legitimate timestamp (the start of a synthetic sequence), which is exactly why
    #: this is not a falsy check: ``0.0 or clock`` silently threw away the first press.
    at: float | None = None
    origin: Origin = Origin.USER
    autorepeat: bool = False
    pressed: bool = True

    @property
    def is_escape(self) -> bool:
        return self.key.strip().lower() in {"esc", "escape", "key_esc", "KEY_ESC"}


@dataclass(frozen=True)
class StopSignal:
    """A fired emergency stop: who asked, when, and which two presses caused it."""

    at: float
    first_at: float
    second_at: float
    gap_ms: float
    window_ms: int
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "double_escape_stop",
            "at": self.at,
            "first_at": self.first_at,
            "second_at": self.second_at,
            "gap_ms": round(self.gap_ms, 3),
            "window_ms": self.window_ms,
            "source": self.source,
        }


@dataclass
class DoubleEscapeDetector:
    """The STOP-01..STOP-03 decision, as pure logic with an injectable clock.

    Feed it events in arrival order with :meth:`feed`; it returns a :class:`StopSignal` on
    the press that completes a double press and ``None`` otherwise. It holds **no** event
    history beyond the one arming press (STOP-03: "keine allgemeine Tastaturaufzeichnung
    speichern").
    """

    window_ms: int = DEFAULT_WINDOW_MS
    min_gap_ms: int = DEFAULT_MIN_GAP_MS
    source: str = ""
    #: fired at most once per arming sequence; reset only by :meth:`reset` or a long gap
    _armed_at: float | None = field(default=None, init=False, repr=False)
    _fired: bool = field(default=False, init=False, repr=False)

    def reset(self) -> None:
        """Forget the arming press. Called after a stop so a third press does not re-fire."""
        self._armed_at = None
        self._fired = False

    def feed(self, event: InputEvent, *, now: float | None = None) -> StopSignal | None:
        """Consume one event. Returns the stop signal when this press completes a double."""
        clock = time.monotonic() if now is None else float(now)
        if not event.is_escape:
            return None
        if not event.pressed:
            # A release never arms and never fires; it only ever clears nothing.
            return None
        # STOP-03: the agent's own synthetic Escape is not a user stop — and it *breaks*
        # the sequence rather than joining it. A single user press, an agent-typed Escape
        # and a later user press is one user Escape with noise around it, not a deliberate
        # double press.
        if event.origin is not Origin.USER:
            self._armed_at = None
            self._fired = False
            return None
        # STOP-01: keyboard autorepeat is one press held down, never two deliberate presses.
        if event.autorepeat:
            return None

        stamp = clock if event.at is None else float(event.at)
        armed = self._armed_at
        if armed is None:
            self._armed_at = stamp
            return None

        gap_ms = (stamp - armed) * 1000.0
        if gap_ms < 0:
            # A clock that went backwards is not a double press; re-arm on the newer stamp.
            self._armed_at = stamp
            return None
        if gap_ms < float(self.min_gap_ms):
            # autorepeat: keep the ORIGINAL arming press, do not slide the window forward,
            # otherwise a key held down slowly walks across the screen and fires.
            return None
        if gap_ms > float(self.window_ms):
            self._armed_at = stamp
            self._fired = False
            return None
        if self._fired:
            return None

        self._fired = True
        self._armed_at = None
        return StopSignal(
            at=clock,
            first_at=armed,
            second_at=stamp,
            gap_ms=gap_ms,
            window_ms=int(self.window_ms),
            source=self.source,
        )


def detect(events: list[InputEvent], *, window_ms: int = DEFAULT_WINDOW_MS,
           min_gap_ms: int = DEFAULT_MIN_GAP_MS, now: float | None = None) -> StopSignal | None:
    """Convenience wrapper: run a whole event list through one detector."""
    det = DoubleEscapeDetector(window_ms=window_ms, min_gap_ms=min_gap_ms)
    clock = time.monotonic() if now is None else float(now)
    for event in events:
        hit = det.feed(event, now=clock)
        if hit is not None:
            return hit
    return None


# --------------------------------------------------------------------------- #
# backends: measured availability, never assumed (STOP-04 / STOP-05)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BackendProbe:
    """One candidate source of physical Escape events, and what was actually measured."""

    name: str
    availability: Availability = Availability.UNKNOWN
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    #: what the owner would have to do to make this backend usable
    unblock: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "availability": self.availability.value,
            "reason": self.reason,
            "unblock": self.unblock,
            "detail": dict(self.detail),
        }


def _run(cmd: list[str], *, timeout: float = 6.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - environment
        return subprocess.CompletedProcess(cmd, 127, "", f"{type(exc).__name__}: {exc}")


def _probe_evdev() -> BackendProbe:
    """Read the physical keyboard through the kernel evdev nodes.

    This is the one backend that sees the key *while another app has the focus* on both X11
    and Wayland without asking the compositor — and it needs read access to
    ``/dev/input/event*``, which is ``root:input`` on a stock Fedora. Being in group ``input``
    is a system change the owner makes; this module never asks for root and never pretends.
    """
    nodes = sorted(Path("/dev/input").glob("event*")) if Path("/dev/input").is_dir() else []
    readable = [str(n) for n in nodes if os.access(n, os.R_OK)]
    if not nodes:
        return BackendProbe(
            "evdev", Availability.BLOCKED,
            "no /dev/input/event* nodes on this host",
            {"nodes": 0},
            "attach a keyboard (or run on a host that exposes evdev)",
        )
    if not readable:
        return BackendProbe(
            "evdev", Availability.BLOCKED,
            f"{len(nodes)} evdev node(s) present but not readable by uid {os.getuid()}: "
            "/dev/input/event* is root:input and this user is not in group 'input'",
            {"nodes": len(nodes), "readable": 0, "uid": os.getuid()},
            "the owner adds the user to group 'input' (a system change, needs root) and "
            "re-logs in — Skaldir does not request sudo",
        )
    return BackendProbe(
        "evdev", Availability.AVAILABLE,
        f"{len(readable)} of {len(nodes)} evdev node(s) readable",
        {"nodes": len(nodes), "readable": len(readable), "sample": readable[:4]},
        "",
    )


def _probe_libinput() -> BackendProbe:
    """``libinput debug-events`` is the same kernel source behind a friendlier CLI."""
    binary = shutil.which("libinput")
    if not binary:
        return BackendProbe("libinput", Availability.BLOCKED, "libinput CLI not installed",
                            {"binary": None}, "install the 'libinput' package")
    probe = _run([binary, "list-devices"])
    denied = "Permission denied" in (probe.stderr or "")
    if denied:
        # `list-devices` exits 0 even when it could not open most event nodes: it lists
        # what it could read. A capture path that cannot open the node cannot capture, so
        # "the CLI ran" is not availability — it is a listing.
        return BackendProbe(
            "libinput", Availability.BLOCKED,
            "libinput cannot open the event nodes (Permission denied) — it can list "
            "devices but cannot read their events",
            {"binary": binary, "returncode": probe.returncode, "denied": True},
            "same as evdev: read access to /dev/input/event* (group 'input')",
        )
    return BackendProbe(
        "libinput", Availability.AVAILABLE if probe.returncode == 0 else Availability.UNKNOWN,
        "libinput list-devices ran" if probe.returncode == 0 else
        f"libinput list-devices exited {probe.returncode}",
        {"binary": binary, "returncode": probe.returncode},
        "",
    )


def _probe_global_shortcuts() -> BackendProbe:
    """``org.freedesktop.portal.GlobalShortcuts`` — the supported Wayland path.

    The portal is *focus independent* by design, which is exactly what STOP-01 needs. What
    it does **not** promise (STOP-04) is that a sequence of two unmodified Escape presses can
    be one trigger: ``BindShortcuts`` offers a shortcut id and a preferred trigger and the
    compositor's own editor decides what the user may bind. So this backend is reported
    ``available`` for *binding a stop shortcut*, and its ``detail`` says explicitly that the
    double-Escape sequence is ``unproven`` rather than quietly claiming it works.
    """
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS") and not os.environ.get("XDG_RUNTIME_DIR"):
        return BackendProbe("xdg-global-shortcuts", Availability.BLOCKED,
                            "no D-Bus session bus in this environment", {},
                            "run inside the user's desktop session")
    out = _run(["gdbus", "introspect", "--session", "--dest", "org.freedesktop.portal.Desktop",
                "--object-path", "/org/freedesktop/portal/desktop"])
    body = out.stdout or ""
    if "org.freedesktop.portal.GlobalShortcuts" not in body:
        return BackendProbe(
            "xdg-global-shortcuts", Availability.BLOCKED,
            "the xdg-desktop-portal on this session exposes no GlobalShortcuts interface",
            {"gdbus_exit": out.returncode},
            "install/enable a portal backend implementing GlobalShortcuts (GNOME does)",
        )
    has_bind = "BindShortcuts" in body
    return BackendProbe(
        "xdg-global-shortcuts", Availability.AVAILABLE if has_bind else Availability.UNKNOWN,
        "GlobalShortcuts portal reachable; BindShortcuts present" if has_bind else
        "GlobalShortcuts present but no BindShortcuts method",
        {
            "interface_version": "2" if "version = 2" in body else "unknown",
            "methods": sorted({m for m in ("CreateSession", "BindShortcuts", "ListShortcuts",
                                           "ConfigureShortcuts") if m in body}),
            "signals": sorted({s for s in ("Activated", "Deactivated", "ShortcutsChanged")
                               if s in body}),
            # the honest limit, repeated where anyone reading the probe will see it
            "double_escape_sequence": "unproven",
            "double_escape_note": (
                "a focus-independent shortcut is bindable here, but the portal does not "
                "guarantee that 'Escape twice within 500 ms' is expressible as one trigger; "
                "binding also opens the compositor's shortcut editor, which is a user action"
            ),
        },
        "",
    )


def _probe_x11_record() -> BackendProbe:
    """X11 XRecord grabs keys without focus — X11 only, and needs python-xlib."""
    if not os.environ.get("DISPLAY"):
        return BackendProbe(
            "x11-record", Availability.BLOCKED,
            "no DISPLAY in this environment", {},
            "run under an X11/XWayland display",
        )
    try:
        import Xlib.display  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - an optional extra, refused structurally
        return BackendProbe("x11-record", Availability.BLOCKED,
                            f"python-xlib not importable: {type(exc).__name__}",
                            {}, "uv pip install -e '.[computer-use]'")
    try:
        import Xlib.display as _xd
        import Xlib.ext.record as _xr  # noqa: F401
        disp = _xd.Display()
        has_record = bool(disp.has_extension("RECORD"))
        detail = {"display": os.environ.get("DISPLAY", ""), "record_extension": has_record}
        disp.close()
    except Exception as exc:  # noqa: BLE001
        return BackendProbe("x11-record", Availability.UNKNOWN,
                            f"could not query the X server: {type(exc).__name__}: {exc}",
                            {"display": os.environ.get("DISPLAY", "")},
                            "verify the X server allows the RECORD extension")
    if not has_record:
        return BackendProbe("x11-record", Availability.BLOCKED,
                            "the X server does not advertise the RECORD extension",
                            detail, "use a server with RECORD, or the evdev/portal backend")
    return BackendProbe(
        "x11-record", Availability.AVAILABLE,
        "X11 RECORD extension present — focus-independent key capture is possible on X11",
        {**detail, "scope_note": "covers X11 clients only; native Wayland clients need evdev "
                                 "or the portal"},
        "",
    )


def _probe_synthetic_injection() -> BackendProbe:
    """Can *we* inject input? Reported for completeness — it can never be a stop source."""
    found = [b for b in ("ydotool", "xdotool", "wtype") if shutil.which(b)]
    return BackendProbe(
        "agent-injection", Availability.AVAILABLE if found else Availability.BLOCKED,
        ("present: " + ", ".join(found)) if found else "no input-injection tool found",
        {"tools": found,
         "note": "this is the agent's OWN input path; per STOP-03 its events are always "
                 "origin=agent and can never fire a stop"},
        "" if found else "install ydotool (Wayland) or xdotool (X11) for computer use",
    )


def probe_backends() -> list[BackendProbe]:
    """Measure every candidate backend once. Cheap, read-only, and never writes state."""
    return [
        _probe_evdev(),
        _probe_libinput(),
        _probe_global_shortcuts(),
        _probe_x11_record(),
        _probe_synthetic_injection(),
    ]


def _session_type() -> str:
    """The display session, because X11 and Wayland are separate acceptance targets.

    The masterplan is explicit: "X11 und Wayland separat abnehmen; XWayland ist kein
    Ersatz fuer beide Nachweise." Measured on this host a raw ``/dev/input/event*`` read
    sees **no keys at all** under Wayland even with the right group, because the
    compositor owns the stream — while ``libinput`` does see them. So the backend that
    proves the stop depends on the session, and the report has to say which one it is in.
    """
    return (
        os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
        or ("wayland" if os.environ.get("WAYLAND_DISPLAY") else
            "x11" if os.environ.get("DISPLAY") else "unknown")
    )


def write_report(path: str | Path, *, session: dict[str, Any] | None = None,
                 proof: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write the measured feasibility report the release decision reads (STOP-05).

    ``proof`` is the live evidence for one backend: what was observed, from where, and on
    which session. Without it the report honestly says the stop is unproven — it never
    claims a proof it was not handed.
    """
    probes = probe_backends()
    # STOP-03: the agent's own injection path can never deliver a user stop, so it does not
    # count towards "usable stop backends" even though it is available as a tool.
    usable = [p for p in probes
              if p.availability is Availability.AVAILABLE and p.name != "agent-injection"]
    session_type = _session_type()
    proof = dict(proof or {})
    proven = bool(proof.get("double_escape_proven"))
    proven_backend = str(proof.get("source") or "")

    if proven:
        statement = (
            f"A physical double-Escape stop is proven on {session_type} via "
            f"{proven_backend or 'a measured backend'}: {proof.get('detected', 0)} "
            "double-Escape(s) observed from the real keyboard. X11 and Wayland are "
            "separate acceptance targets — this proof covers this session only."
        )
    else:
        statement = (
            "A physical double-Escape stop is NOT declared complete. The detector logic "
            "is implemented and tested, but no backend on this host has been shown to "
            "deliver two physical Escape presses from the real keyboard while another "
            "application holds the focus."
        )

    report: dict[str, Any] = {
        "kind": "skaldir.stop-feasibility",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "session": dict(session or {}),
        "session_type": session_type,
        "window_ms": DEFAULT_WINDOW_MS,
        "min_gap_ms": DEFAULT_MIN_GAP_MS,
        "backends": [p.to_dict() for p in probes],
        "verdict": {
            "double_escape_proven": proven,
            "proven_backend": proven_backend,
            "usable_backends": [p.name for p in usable],
            "proof": proof,
            "statement": statement,
        },
        "notes": [
            ("X11 and Wayland are accepted separately; XWayland is not a substitute for "
             "both (masterplan)."),
            ("A raw /dev/input/event* read is NOT the proof path on Wayland: the "
             "compositor owns the event stream, so it sees no keys even with the input "
             "group. `libinput` does."),
            ("agent-injection (ydotool/xdotool/wtype) can never prove a user stop: "
             "synthesised input cannot show that a user can stop a run (STOP-03)."),
        ],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report
