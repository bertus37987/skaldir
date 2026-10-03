"""The input probe's OWN temporary window: a fresh process, its own receiver, its own record.

Why this module exists (P0 defects 1-3, architecture.md §2). The old probe read the tail of a
log file and accepted **any** change in it as proof that a keypress arrived. Three things were
wrong with that, and all three are measured facts rather than opinions:

1. **The probe never owned its target.** It typed into whatever happened to have the X focus,
   so an rc=0 "delivery" could describe a keystroke that landed in the *user's* window.
2. **There was no receiver.** A file that changes for any other reason (another process writing
   it, a stale leftover, a rotation) looked exactly like a delivery.
3. **The marker was never evaluated.** `probe_log` gained a line, and that was the whole test.

So the probe here owns its target instead of hoping for one:

* a **fresh process** is spawned (`python -m skaldir.probe_target`), its pid recorded, and it
  creates a small window titled ``SKALDIR-PROBE-<nonce>`` that carries its own pid in
  ``_NET_WM_PID``;
* that window's **own event loop** is the active receiver: every key event is written as one
  JSON record ``(wid, t, keysym, char)`` to the probe's own log, which the *sender never writes
  to*;
* the binding is **confirmed before anything is sent** — the window is on this display, its
  title carries this probe's nonce, ``WM_STATE`` is not Withdrawn, ``_NET_WM_PID`` equals the
  recorded pid, and the X **server's** input focus is the probe window after
  ``xdotool windowactivate --sync``;
* delivery is decided by :func:`correlate_delivery`, and by nothing else: a record counts only
  when the marker ∧ the probe window id ∧ the send-time window all hold.

Two measurements that shaped the details, both from this host:

* ``windowfocus`` alone does not stick (the focus reverts), ``windowactivate --sync`` does —
  so activation, not focusing, is part of the setup.
* a raw X11 window has **no** ``WM_STATE`` at all (it has no frame); only ``Withdrawn`` is a
  real problem. Treating a missing ``WM_STATE`` as "unmapped" would discard exactly the window
  that can prove delivery. The receiver therefore reports its own ``map_state``
  (``IsViewable``), which is the X server's opinion rather than a property the window manager
  may or may not have written.

Cleanup is the mirror of the ownership rule: the receiver destroys its own window on SIGTERM,
the parent signals **only the pid it recorded** (after ``/proc/<pid>/cmdline`` confirms it is
still this probe's receiver), and removes only the temp directory this probe created. No window
is ever closed by title, and no process is ever signalled that this probe did not start.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import select
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The title every probe window carries: ``SKALDIR-PROBE-<nonce>``. The nonce is the per-probe
#: identity that stops one probe's records from being read as another probe's delivery.
PROBE_TITLE_PREFIX = "SKALDIR-PROBE-"


def title_tail(nonce: str) -> str:
    """The ASCII tail the window title carries for this nonce — deterministic, so both ends agree.

    Measured (2026-10-03, live probe on :0): a non-ASCII nonce is stored in the X title
    property as Latin-1 bytes, `xdotool getwindowname` answers those raw bytes, and the
    confirmation compared UNICODE names — the probe refused its OWN window
    (``PROBE_MISCONFIGURED``, unknown, nothing sent). The title is a runtime identity, so
    it is an ASCII slug joined with a short nonce hash, keeping ``äöüß`` markers
    workable while plain markers keep the exact old title.
    """
    slug = "".join(ch if 0x20 <= ord(ch) < 0x7F and ch not in " \t" else "_" for ch in nonce)
    digest = hashlib.blake2b(nonce.encode("utf-8"), digest_size=3).hexdigest()
    return f"{slug}-{digest}" if slug != nonce else slug

#: How the probe's own receiver is started — a FRESH process, so the window really has a pid
#: the probe can record and confirm (an in-process window would carry the *runner's* pid).
RECEIVER_MODULE = "skaldir.probe_target"

#: Where a probe keeps its own temp files. Own only: the probe removes exactly this directory.
PROBE_TMP_PREFIX = "skaldir-input-probe-"

#: The record kinds a receiver writes. ``READY`` is setup, ``KEY``/``TEXT`` are deliveries.
RECORD_KINDS = ("KEY", "TEXT")

#: A bounded subprocess boundary: fixed argv, no shell, one timeout.
Run = Callable[[Sequence[str], float], "tuple[int, str]"]


def run_command(
    argv: Sequence[str], timeout: float = 10.0, env: dict[str, str] | None = None
) -> tuple[int, str]:
    """Run one fixed argv with no shell, bounded, and return ``(rc, combined output)``.

    ``env`` exists because the confirmation commands must interrogate **the display the
    window was created on**: an owned display (``:91``+) is not ``$DISPLAY`` of the shell, and
    querying the wrong one would either find nothing or, worse, find somebody else's window.
    """
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv list, no shell
            [str(part) for part in argv],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
            # Measured 2026-10-03 (feature input-type-charset-fix, live probe on :0):
            # `xdotool getwindowname` answers a window whose WM stores the title as raw
            # bytes (0xe4 = Latin-1) and the strict UTF-8 decode raised UnicodeDecodeError
            # out of `communicate()` — a measurement tool crash, not a probe result.
            # Foreign titles are read as text, whatever bytes they are; "replace" never
            # invents input.
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


# --------------------------------------------------------------------------- #
# what the target itself reports
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class DeliveryRecord:
    """One event the probe's own window reported: ``(wid, t, keysym/char)`` plus its identity.

    ``run`` is the probe nonce and ``wid`` the window that received the event. Both exist so a
    foreign window's event, a previous probe's leftover record, or a marker-less keystroke can
    never be mistaken for this probe's delivery.
    """

    run: str
    wid: int
    t: float
    keycode: int = 0
    keysym: int = 0
    char: str = ""
    press: bool = True
    text: str = ""

    def as_json(self) -> str:
        return json.dumps(
            {
                "kind": "TEXT" if self.text else "KEY",
                "run": self.run,
                "wid": self.wid,
                "t": self.t,
                "keycode": self.keycode,
                "keysym": self.keysym,
                "char": self.char,
                "press": self.press,
                "text": self.text,
            },
            ensure_ascii=False,
        )


def parse_records(text: str) -> list[DeliveryRecord]:
    """Every delivery record in the receiver's log — unreadable lines are skipped, never guessed.

    This is a reader, not a judge: it does not decide whether a record proves anything, it only
    returns what the target said. :func:`correlate_delivery` decides.
    """
    records: list[DeliveryRecord] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        if not isinstance(raw, dict) or raw.get("kind") not in RECORD_KINDS:
            continue
        try:
            records.append(
                DeliveryRecord(
                    run=str(raw.get("run", "")),
                    wid=int(raw.get("wid", 0) or 0),
                    t=float(raw.get("t", 0.0) or 0.0),
                    keycode=int(raw.get("keycode", 0) or 0),
                    keysym=int(raw.get("keysym", 0) or 0),
                    char=str(raw.get("char", "") or ""),
                    press=bool(raw.get("press", True)),
                    text=str(raw.get("text", "") or ""),
                )
            )
        except (TypeError, ValueError):
            continue
    return records


def read_records(log_path: Path) -> list[DeliveryRecord]:
    """The records in one probe's own log (an absent log is an empty receiver, not a delivery)."""
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return parse_records(text)


@dataclass(frozen=True)
class Correlation:
    """The verdict of the correlation rule — with the reason it failed, when it did."""

    delivered: bool
    reason: str
    records: tuple[DeliveryRecord, ...] = ()


def _marker_records(records: Sequence[DeliveryRecord], marker: str) -> list[DeliveryRecord]:
    """The shortest contiguous run of key-press records whose characters spell ``marker``.

    A typed marker arrives as one KeyPress per character, so the proof is a *sequence*, not a
    single event. Non-printable keys (Shift itself, Return) carry no character and are skipped
    without breaking the run.
    """
    wanted = len(marker)
    for start in range(len(records)):
        run: list[DeliveryRecord] = []
        for record in records[start:]:
            if len(run) >= wanted:
                break
            if record.char:
                run.append(record)
        if len(run) < wanted:
            break
        if "".join(item.char for item in run) == marker:
            return run
    return []


def correlate_delivery(
    records: Sequence[DeliveryRecord],
    *,
    marker: str,
    wid: int,
    t_send: float,
    timeout: float,
) -> Correlation:
    """A delivery counts **only** when marker ∧ probe window ∧ send-time window all hold.

    This is the whole proof of VAL-INPUT-002, and it is deliberately three separate gates:

    * **marker** — the event has to carry the random token this probe sent. A keystroke from
      anything else, an unrelated log change, a foreign process's output: no marker, no hit.
    * **probe window** — the record's ``wid`` has to be the window this probe created. An event
      the user's focused window received, or a record a previous probe left behind: no hit.
    * **send-time window** — every record of the match has to sit in
      ``[t_send, t_send + timeout]``. The marker can only prove *this* send.

    Each rejection names which gate failed, so a live run says "no marker" rather than
    "nothing arrived", and nobody has to guess which of the three it was.
    """
    if not marker:
        return Correlation(
            False, "no marker to correlate — an unmarked probe cannot prove anything"
        )
    if not records:
        return Correlation(False, "the target reported no event at all")
    own = [record for record in records if record.wid == int(wid)]
    if not own:
        return Correlation(
            False,
            f"no event at the probe's own window {wid} — the records belong to other windows",
        )
    presses = [record for record in own if record.press]
    upper = float(t_send) + float(timeout)
    for record in presses:
        if marker in record.text:
            if t_send <= record.t <= upper:
                return Correlation(True, "the marker arrived at the probe window", (record,))
            return Correlation(
                False,
                "the marker arrived outside the send-time window — it cannot prove this send",
            )
    run = _marker_records(presses, marker)
    if not run:
        return Correlation(
            False,
            "no event at the probe window carries the marker — an unmarked keystroke "
            "is not a delivery",
        )
    if not all(t_send <= record.t <= upper for record in run):
        return Correlation(
            False,
            "the marker arrived outside the send-time window — it cannot prove this send",
        )
    return Correlation(
        True,
        "the marker arrived at the probe window inside the send-time window",
        tuple(run),
    )


# --------------------------------------------------------------------------- #
# the probe's own window: spawn, confirm, destroy
# --------------------------------------------------------------------------- #

@dataclass
class ProbeBinding:
    """Everything that makes a window *this probe's own* window — and whether it is confirmed.

    ``confirmed`` is the gate the probe obeys: an unconfirmed binding means nothing is sent,
    because sending into an unconfirmed window is exactly how a keypress lands somewhere the
    user is working.
    """

    run: str
    pid: int
    wid: int
    log_path: Path
    title: str = ""
    directory: Path | None = None
    map_state: str = ""
    confirmed: bool = False
    reason: str = ""
    checks: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run": self.run,
            "pid": self.pid,
            "wid": self.wid,
            "title": self.title,
            "log": str(self.log_path),
            "map_state": self.map_state,
            "confirmed": self.confirmed,
            "reason": self.reason,
            "checks": dict(self.checks),
        }


@dataclass
class ProbeWindow:
    """One probe's own window: the receiver process, its log, and its confirmed binding."""

    binding: ProbeBinding
    proc: subprocess.Popen | None = None

    @property
    def records(self) -> list[DeliveryRecord]:
        return read_records(self.binding.log_path)

    def destroy(self, *, run_cmd: Run | None = None, env: dict[str, str] | None = None) -> dict:
        return destroy_probe_window(self, run_cmd=run_cmd, env=env)


def _write_record(log_path: Path, payload: dict[str, Any]) -> None:
    """One line, flushed: the sender reads this while the sender is still running."""
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_ready(log_path: Path) -> dict[str, Any]:
    """The receiver's READY line, or ``{}`` when it has not written one yet."""
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        if isinstance(raw, dict) and raw.get("kind") == "READY":
            return raw
    return {}


def _receiver_argv(run: str, log_path: Path, width: int, height: int) -> list[str]:
    import sys

    return [
        sys.executable,
        "-m",
        RECEIVER_MODULE,
        "--run",
        run,
        "--log",
        str(log_path),
        "--title",
        title_tail(run),
        "--width",
        str(int(width)),
        "--height",
        str(int(height)),
    ]


def _receiver_env(display: str) -> dict[str, str]:
    """The environment the receiver needs: the display, and the import path to this package."""
    env = dict(os.environ)
    if display:
        env["DISPLAY"] = str(display)
    root = str(Path(__file__).resolve().parents[1])
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{root}{os.pathsep}{existing}" if existing else root
    return env


def _pid_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
    except (OSError, ValueError):
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def _is_our_receiver(pid: int, run: str) -> bool:
    """Is this pid still *this probe's* receiver? A recycled pid must never be signalled."""
    if not pid:
        return False
    cmdline = _pid_cmdline(pid)
    return RECEIVER_MODULE in cmdline and str(run) in cmdline


def _wm_state(text: str) -> str:
    """``Normal`` / ``Withdrawn`` / ``Iconic`` / ``(keiner)`` — never a guess.

    Only a real property answer counts (``WM_STATE(WM_STATE) = window state: Normal``): an X
    error message must never be read as a window state, and a raw X window legitimately has
    none at all (measured: it has no frame, so no window manager wrote a state).
    """
    lowered = (text or "").lower()
    if "window state:" not in lowered:
        return "(keiner)"
    tail = lowered.split("window state:", 1)[1].strip()
    for name in ("normal", "withdrawn", "iconic"):
        if tail.startswith(name):
            return name.capitalize()
    return "(keiner)"


def _net_wm_pid(text: str) -> int:
    """The pid from ``_NET_WM_PID(CARDINAL) = 1234`` — 0 when the property is not there.

    The ``=`` is the point: a first-number-anywhere parse would happily read the opcode out of
    an X error message and hand the probe a foreign pid as if it were its own.
    """
    match = re.search(r"=\s*(\d+)", text or "")
    return int(match.group(1)) if match else 0


def _xlib_focus(env: dict[str, str] | None = None) -> int | None:
    """The X **server's** input focus, read through python-xlib — the only opinion that counts.

    ``None`` when python-xlib is not importable or no display answers; the caller then falls
    back to ``xdotool getwindowfocus``, which reads the *window manager's* idea of the active
    window and can diverge from the server's (measured). The fallback is named in the report so
    a divergence is visible instead of silent.
    """
    try:
        from Xlib import display as xdisplay
    except Exception:  # noqa: BLE001 — a missing optional capability is not an error here
        return None
    # python-xlib reads `DISPLAY` from the environment when the connection is opened, so the
    # wanted display is put there for the length of that one call and restored afterwards —
    # a probe must never leave a different display behind in its own process.
    saved = {key: os.environ.get(key) for key in (env or {})}
    try:
        for key, value in (env or {}).items():
            os.environ[key] = value
        connection = xdisplay.Display()
        try:
            focus = connection.get_input_focus()
            return int(getattr(focus.focus, "id", 0) or 0)
        finally:
            connection.close()
    except Exception:  # noqa: BLE001 — no reachable display answers nothing here
        return None
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _window_id_from(text: str) -> int:
    """A window id from command output: decimal, or ``0x``-hex. ``0`` when it is neither."""
    first = (text or "").strip().splitlines()
    first = first[0].strip() if first else ""
    if not first:
        return 0
    if first.lower().startswith("0x"):
        try:
            return int(first, 16)
        except ValueError:
            return 0
    if first.isdigit():
        return int(first, 10)
    for token in first.split():
        candidate = token.strip().lower()
        if candidate.startswith("0x"):
            candidate = candidate[2:]
            try:
                return int(candidate, 16)
            except ValueError:
                continue
        if candidate.isdigit():
            return int(candidate, 10)
    return 0


def read_server_focus(
    *,
    run_cmd: Run | None = None,
    reader: Callable[[], int] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """The X server's input focus as ``(wid, method)`` — ``0`` when it cannot be read at all."""
    if reader is not None:
        try:
            return int(reader() or 0), "injected reader"
        except Exception:  # noqa: BLE001 — a reader that fails answers nothing
            return 0, "reader failed"
    value = _xlib_focus(env=env)
    if value:
        return int(value), "xlib get_input_focus"
    if run_cmd is not None:
        code, output = run_cmd(["xdotool", "getwindowfocus"], 10.0)
    else:
        code, output = run_command(["xdotool", "getwindowfocus"], 10.0, env=env)
    found = _window_id_from(output)
    if found:
        return found, "xdotool getwindowfocus"
    return 0, f"not readable (rc {code})"


def confirm_binding(
    binding: ProbeBinding,
    *,
    run_cmd: Run | None = None,
    focus: Callable[[], int] | None = None,
    activate: bool = True,
    env: dict[str, str] | None = None,
) -> ProbeBinding:
    """Confirm that ``binding`` is this probe's own, mapped, activated window — before any send.

    Six checks, each recorded so a live failure names the check that failed instead of "blocked":

    ============================  ==========================================================
    ``window_on_display``         the recorded id really is a window on this display
    ``title_is_ours``             carries this run's nonce or its ASCII slug (a reused id fails)
    ``wm_state``                  ``Withdrawn`` fails; a raw X window legitimately has none
    ``map_state``                 the receiver's own view: ``IsViewable`` is mapped
    ``net_wm_pid``                equals the pid this probe recorded, so it is our window
    ``server_focus``              the X server's input focus is this window after activation
    ============================  ==========================================================

    Anything unconfirmed makes ``confirmed`` False with the reason, and the caller sends nothing.
    """
    runner = run_cmd or run_command
    checks: dict[str, Any] = {}
    reasons: list[str] = []
    wid = int(binding.wid)
    # Every question is asked of the display the window was created on: an owned display is not
    # `$DISPLAY` of the shell, and a query against the wrong one finds nothing (or worse, finds
    # somebody else's window and reads it as ours).
    if run_cmd is not None and run_cmd is not run_command:

        def ask(argv: list[str]) -> tuple[int, str]:
            return run_cmd(argv, 10.0)  # type: ignore[misc]
    else:

        def ask(argv: list[str]) -> tuple[int, str]:
            return run_command(argv, 10.0, env=env)

    code, _out = ask(["xdotool", "getwindowgeometry", "--shell", str(wid)])
    checks["window_on_display"] = code == 0
    if code != 0:
        reasons.append(f"the recorded window {wid} is not on this display (rc {code})")

    _code, name = ask(["xdotool", "getwindowname", str(wid)])
    title = (name or "").strip()
    # The window must carry THIS probe's identity — the nonce itself when it is ASCII,
    # otherwise the deterministic ASCII slug :func:`title_tail` computed from it (a non-ASCII
    # nonce is stored as Latin-1 bytes in the X title property, so the literal token must
    # never be demanded here). A foreign run's title fails both.
    ours = str(binding.title or "")
    slug = title_tail(str(binding.run or ""))
    checks["title_is_ours"] = bool(ours) and ours in title and (
        binding.run in title or slug in title
    )
    if not checks["title_is_ours"]:
        reasons.append(
            f"the title of window {wid} is {title!r}, not this probe's {ours!r}"
        )

    if activate:
        code, output = ask(["xdotool", "windowactivate", "--sync", str(wid)])
        checks["activate_rc"] = code
        if code != 0:
            reasons.append(f"xdotool windowactivate --sync returned rc {code} ({output[:120]})")

    _code, output = ask(["xprop", "-id", str(wid), "WM_STATE"])
    state = _wm_state(output)
    checks["wm_state"] = state
    if state == "Withdrawn":
        reasons.append("the probe window is Withdrawn — not mapped, so it cannot receive input")

    checks["map_state"] = binding.map_state
    if binding.map_state and binding.map_state != "IsViewable":
        reasons.append(f"the probe window reports map state {binding.map_state}")

    code, output = ask(["xprop", "-id", str(wid), "_NET_WM_PID"])
    pid_seen = _net_wm_pid(output)
    checks["net_wm_pid"] = pid_seen
    if pid_seen != int(binding.pid):
        reasons.append(
            f"_NET_WM_PID of window {wid} is {pid_seen or 'absent'}, not the recorded probe "
            f"pid {binding.pid} — the window is not proven to be this probe's own"
        )

    if runner is run_command:
        focus_wid, method = read_server_focus(run_cmd=None, reader=focus, env=env)
    else:
        focus_wid, method = read_server_focus(run_cmd=runner, reader=focus)
    checks["server_focus"] = focus_wid
    checks["focus_method"] = method
    if not focus_wid:
        reasons.append("the X server's input focus could not be read")
    elif int(focus_wid) != wid:
        reasons.append(
            f"the X server's input focus is {focus_wid}, not the probe window {wid} — a send "
            "now would land in someone else's window"
        )

    binding.checks = checks
    binding.confirmed = not reasons
    binding.reason = "" if not reasons else "; ".join(reasons)
    return binding


def spawn_probe_window(
    *,
    run: str | None = None,
    workdir: Path | None = None,
    display: str = "",
    timeout: float = 10.0,
    width: int = 460,
    height: int = 200,
    run_cmd: Run | None = None,
    focus: Callable[[], int] | None = None,
    env: dict[str, str] | None = None,
    argv: list[str] | None = None,
) -> ProbeWindow:
    """Spawn the probe's own receiving window and confirm the binding to it.

    ``argv`` and ``focus`` are the two boundaries a test may replace: the process that is
    spawned (a subprocess) and the display read that decides where a keystroke would land.
    Everything else — the READY wait, the six confirmation checks, the destroy — is the real
    code path.

    Never raises for a host problem: an unspawnable, unreporting or unconfirmed window comes
    back as a :class:`ProbeWindow` with ``confirmed=False`` and a reason, so the caller reports
    a misconfigured probe instead of sending into nothing.
    """
    runner = run_cmd or run_command
    nonce = str(run or f"{os.getpid()}-{secrets.token_hex(4)}")
    directory = Path(workdir) if workdir is not None else Path(
        tempfile.mkdtemp(prefix=PROBE_TMP_PREFIX)
    )
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    log_path = directory / f"probe-{nonce}.jsonl"
    spawn_argv = list(argv) if argv is not None else _receiver_argv(nonce, log_path, width, height)
    err_path = directory / "receiver.err"
    spawn_env = dict(env) if env is not None else _receiver_env(display)

    proc: subprocess.Popen | None = None
    try:
        err = open(err_path, "wb")  # own temp file, removed with the probe's own directory
        try:
            proc = subprocess.Popen(  # noqa: S603 — fixed argv list, no shell
                spawn_argv,
                stdout=err,
                stderr=err,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=spawn_env,
            )
        finally:
            err.close()
    except OSError as exc:
        binding = ProbeBinding(
            run=nonce, pid=0, wid=0, log_path=log_path, directory=directory,
            title=f"{PROBE_TITLE_PREFIX}{title_tail(nonce)}",
            reason=f"the probe receiver could not be started: {type(exc).__name__}: {exc}",
        )
        return ProbeWindow(binding=binding, proc=None)

    binding = ProbeBinding(
        run=nonce,
        pid=int(proc.pid),
        wid=0,
        log_path=log_path,
        title=f"{PROBE_TITLE_PREFIX}{title_tail(nonce)}",
        directory=directory,
    )
    ready: dict[str, Any] = {}
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        ready = _read_ready(log_path)
        if ready:
            break
        if proc.poll() is not None:
            break
        time.sleep(0.05)

    if proc.poll() is not None and not ready:
        detail = ""
        try:
            detail = err_path.read_text(encoding="utf-8", errors="replace").strip()[-300:]
        except OSError:
            pass
        binding.reason = (
            f"the probe receiver exited with rc {proc.returncode} before it reported a window"
            + (f": {detail}" if detail else "")
        )
        return ProbeWindow(binding=binding, proc=proc)
    if not ready:
        binding.reason = (
            f"the probe receiver reported no window within {timeout}s — no target to send to"
        )
        return ProbeWindow(binding=binding, proc=proc)
    if ready.get("ok") is False:
        binding.reason = str(
            ready.get("reason") or "the probe receiver could not create its window"
        )
        return ProbeWindow(binding=binding, proc=proc)

    binding.wid = int(ready.get("wid", 0) or 0)
    binding.map_state = str(ready.get("map_state", "") or "")
    if not binding.wid:
        binding.reason = "the probe receiver reported no window id — nothing is bound"
        return ProbeWindow(binding=binding, proc=proc)
    return ProbeWindow(
        binding=confirm_binding(binding, run_cmd=runner, focus=focus, env=spawn_env),
        proc=proc,
    )


def destroy_probe_window(
    window: ProbeWindow, *, run_cmd: Run | None = None, env: dict[str, str] | None = None
) -> dict:
    """Remove **exactly** this probe's own resources: its recorded pid and its own files.

    The guard, which is the point of the whole design:

    * only the pid this probe recorded is signalled, and only after ``/proc/<pid>/cmdline``
      still shows this probe's receiver — a recycled pid or a foreign process is left alone;
    * the receiver destroys its **own** window when it is asked to stop, so nothing is closed
      by title, by class, or on the strength of looking like a probe window;
    * only the temp directory this probe created is removed.

    The report says what actually happened, including a window that stubbornly survived, so a
    leftover is reported rather than quietly assumed away.
    """
    binding = window.binding
    report: dict[str, Any] = {
        "run": binding.run,
        "pid": binding.pid,
        "wid": binding.wid,
        "terminated": False,
        "foreign_pid_untouched": False,
        "window_gone": None,
        "removed": [],
        "reason": "",
    }
    proc = window.proc
    pid = int(binding.pid)
    if proc is not None:
        if proc.poll() is None:
            ours = _is_our_receiver(pid, binding.run)
            if not ours:
                report["foreign_pid_untouched"] = True
                report["reason"] = (
                    f"pid {pid} is no longer this probe's receiver — left untouched"
                )
            else:
                try:
                    proc.terminate()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                        proc.wait(timeout=5.0)
                    except (OSError, subprocess.SubprocessError):
                        pass
                report["terminated"] = True
        else:
            proc.wait()
            report["terminated"] = True
    elif pid and _pid_alive(pid):
        if _is_our_receiver(pid, binding.run):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            for _ in range(50):
                if not _pid_alive(pid):
                    break
                time.sleep(0.1)
            if _pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            report["terminated"] = True
        else:
            report["foreign_pid_untouched"] = True
            report["reason"] = f"pid {pid} is not this probe's receiver — left untouched"

    if binding.wid:
        if run_cmd is not None:
            code, _output = run_cmd(["xdotool", "getwindowname", str(binding.wid)], 5.0)
        else:
            code, _output = run_command(
                ["xdotool", "getwindowname", str(binding.wid)], 5.0, env=env
            )
        report["window_gone"] = code != 0
        if code == 0:
            report["reason"] = (
                f"the probe window {binding.wid} is still on the display after its own receiver "
                "stopped — reported, never force-closed by title or id"
            )

    directory = binding.directory
    if directory is not None:
        own_dir = (
            Path(directory).name.startswith(PROBE_TMP_PREFIX)
            and Path(directory).parent == Path(tempfile.gettempdir())
        )
        if own_dir:
            # A directory this probe created for itself goes away whole.
            shutil.rmtree(directory, ignore_errors=True)
            report["removed"].append(str(directory))
        else:
            # A caller-supplied directory (a test's tmp_path, a run's own workspace) is not
            # ours to guess about: only the files this probe wrote inside it are removed.
            for name in (f"probe-{binding.run}.jsonl", "receiver.err"):
                candidate = Path(directory) / name
                try:
                    candidate.unlink()
                except OSError:
                    continue
                report["removed"].append(str(candidate))
    return report


# --------------------------------------------------------------------------- #
# the receiver itself (a fresh process: `python -m skaldir.probe_target --run …`)
# --------------------------------------------------------------------------- #

def _printable_char(keysym: int) -> str:
    """The character a keysym produced, or ``""`` when it is not a printable one.

    X keysyms for the Latin-1 range *are* the character codes, which is why a typed marker can
    be reconstructed from the key events alone — and why Shift itself, Return and the multimedia
    keys contribute nothing (they are outside this range).
    """
    if 0x20 <= keysym <= 0x7E or 0xA0 <= keysym <= 0xFF:
        return chr(keysym)
    return ""


#: Modifier masks of the event's ``state`` field (X protocol constants, ``Xlib.X`` module
#: values spelled out so the whole receiver needs no X display at decode time).
X_SHIFT_MASK = 1  # X.ShiftMask
X_MOD5_MASK = 128  # X.Mod5Mask — the AltGr / ISO_Level3_Shift level


def _keysym_index(state: int) -> int:
    """The keysym index the CURRENT modifier state selects — Shift is not the whole truth.

    Measured (2026-10-03, live probe on :0, feature input-type-charset-fix): a text marker of
    the fixed path types 'äöüß' with a Level-3 (AltGr/Mod5) hold, and the receiver decoded
    every such event with the shift index only — '.' arrived as '[' — and the honest
    marker correlation rejected its OWN delivery. The index here is the same level table the
    layout parser reads in :mod:`skaldir.text_keys`: plain 0, shift 1, AltGr 2, shift+AltGr 3.
    """
    altgr = bool(state & X_MOD5_MASK)
    shift = bool(state & X_SHIFT_MASK)
    if altgr and shift:
        return 3
    if altgr:
        return 2
    if shift:
        return 1
    return 0


def _map_state_name(value: Any) -> str:
    return {0: "IsUnmapped", 1: "IsUnviewable", 2: "IsViewable"}.get(int(value or 0), str(value))


def receiver_main(argv: Sequence[str] | None = None) -> int:
    """The probe window's own event loop — the ACTIVE receiver.

    It is a separate process on purpose: the window then has a pid of its own, which the probe
    records and which the window publishes in ``_NET_WM_PID``. Every key event is written to
    the probe's own log as ``(wid, t, keysym, char)``; the sender only ever *reads* that log.

    Bounded so an orphan can never linger: it stops after ``--max-events`` or ``--lifetime``
    seconds, whichever comes first, and it always destroys its own window on the way out.
    """
    import argparse

    parser = argparse.ArgumentParser(prog=RECEIVER_MODULE)
    parser.add_argument("--run", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--width", type=int, default=460)
    parser.add_argument("--height", type=int, default=200)
    parser.add_argument("--max-events", type=int, default=96)
    parser.add_argument("--lifetime", type=float, default=120.0)
    args = parser.parse_args(list(argv) if argv is not None else None)
    log_path = Path(args.log)

    def _stop(signum: int, _frame: Any) -> None:
        raise SystemExit(0)

    for name in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(name, _stop)
        except (ValueError, OSError, RuntimeError):
            continue

    try:
        from Xlib import X, Xatom
        from Xlib import display as xdisplay
    except Exception as exc:  # noqa: BLE001 — a missing optional capability is a reportable fact
        _write_record(log_path, {
            "kind": "READY", "ok": False, "run": args.run,
            "reason": f"python-xlib is not importable here: {type(exc).__name__}: {exc}",
        })
        return 3
    try:
        connection = xdisplay.Display()
    except Exception as exc:  # noqa: BLE001 — no display is a reportable fact, not a crash
        _write_record(log_path, {
            "kind": "READY", "ok": False, "run": args.run,
            "reason": f"no reachable X display: {type(exc).__name__}: {exc}",
        })
        return 3

    pid = os.getpid()
    screen = connection.screen()
    window = screen.root.create_window(
        30, 30, int(args.width), int(args.height), 1, X.CopyFromParent
    )
    try:
        # The title is the probe's identity at the window manager. A non-ASCII nonce goes
        # into the X string property as Latin-1 bytes and `xdotool getwindowname` answers
        # them raw — so the title is the ASCII slug (--title, computed identically by
        # :func:`title_tail`), never the raw nonce.
        window.set_wm_name(f"{PROBE_TITLE_PREFIX}{args.title or title_tail(args.run)}")
        # The window publishes its OWN pid, so the probe can confirm the binding belongs to the
        # process it started rather than to whatever else holds a similar window.
        window.change_property(
            connection.intern_atom("_NET_WM_PID"), Xatom.CARDINAL, 32, [pid]
        )
        window.change_attributes(
            event_mask=X.KeyPressMask | X.KeyReleaseMask | X.StructureNotifyMask
        )
        window.map()
        connection.flush()
        map_state = "IsUnmapped"
        for _ in range(40):
            try:
                map_state = _map_state_name(window.get_attributes().map_state)
            except Exception:  # noqa: BLE001 — a transient window query is retried, not fatal
                map_state = "IsUnmapped"
            if map_state == "IsViewable":
                break
            time.sleep(0.1)
        _write_record(log_path, {
            "kind": "READY", "ok": True, "run": args.run, "wid": int(window.id), "pid": pid,
            "title": f"{PROBE_TITLE_PREFIX}{args.title or title_tail(args.run)}",
            "map_state": map_state, "set_net_wm_pid": True,
        })

        events = 0
        deadline = time.monotonic() + float(args.lifetime)
        while events < int(args.max_events) and time.monotonic() < deadline:
            # An ACTIVE receiver: it waits on its own X socket instead of blocking in
            # `next_event()`, so a lifetime bound and a stop are still honoured between events.
            try:
                readable, _w, _x = select.select([connection.fileno()], [], [], 0.01)
            except OSError as exc:
                _write_record(log_path, {
                    "kind": "READY", "ok": False, "run": args.run,
                    "reason": f"the probe receiver lost its display: {type(exc).__name__}: {exc}",
                })
                return 5
            if not readable:
                continue
            event = connection.next_event()
            if event.type == X.DestroyNotify:
                break
            if event.type not in (X.KeyPress, X.KeyRelease):
                continue
            press = event.type == X.KeyPress
            events += 1
            keycode = int(getattr(event, "detail", 0) or 0)
            state = int(getattr(event, "state", 0) or 0)
            try:
                base = int(connection.keycode_to_keysym(keycode, 0) or 0)
                level = int(connection.keycode_to_keysym(keycode, _keysym_index(state)) or 0)
            except Exception:  # noqa: BLE001 — an unmappable keycode is recorded as it is
                base, level = 0, 0
            keysym = level if press else base
            _write_record(log_path, {
                "kind": "KEY", "run": args.run, "wid": int(window.id), "t": time.time(),
                "keycode": keycode, "keysym": keysym, "char": _printable_char(keysym),
                "press": press,
            })
    except SystemExit:
        pass
    except Exception as exc:  # noqa: BLE001 — the reason is recorded, never a bare traceback
        _write_record(log_path, {
            "kind": "READY", "ok": False, "run": args.run,
            "reason": f"the probe receiver failed: {type(exc).__name__}: {exc}",
        })
        return 4
    finally:
        try:
            window.destroy()  # the window's own owner ends it — never a title-based close
        except Exception:  # noqa: BLE001 — cleanup never raises
            pass
        try:
            connection.close()
        except Exception:  # noqa: BLE001 — cleanup never raises
            pass
    return 0


def receiver_available() -> tuple[bool, str]:
    """Can this process run the active receiver at all? (python-xlib and an importable module.)"""
    try:
        import Xlib  # noqa: F401 — presence is the whole check
    except Exception as exc:  # noqa: BLE001 — reported, so a probe says why it is blind
        return False, f"python-xlib is not importable here: {type(exc).__name__}: {exc}"
    return True, "python-xlib importable"


if __name__ == "__main__":  # pragma: no cover - the process boundary the probe spawns
    raise SystemExit(receiver_main())
