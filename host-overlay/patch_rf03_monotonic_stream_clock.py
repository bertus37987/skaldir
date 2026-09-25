#!/usr/bin/env python3
"""Host-Overlay RF-03: Langlauf-Limits auf der monotonen Uhr messen.

Problem (Kapitel 09 §4, RF-03): Der Stale-Detektor des Stream-Monitors leitet
seine verstrichene Zeit aus dem letzten ECHTEN Chunk ab, stempelt diesen aber mit
`time.time()` und misst mit `time.time()`. Ein Wall-Clock-Sprung (NTP-Step,
Suspend/Resume, DST) erschien damit als Stille: ein gesunder, langsam streamender
Provider wurde abgebrochen — reproduziert mit `tools/host_rf_monotonic_probe.py`
(+3600s Sprung → Kill bei 3600.3s trotz 10s Timeout, Fall A rot).

Fix: Der Chunk-Stempel traegt BEIDE Uhren. `t` bleibt Wall-Clock, weil Diagnose
(`first_chunk_at`) und Mensch sie so lesen; `mono` misst jede Dauer/Deadline.
Der Monitor rechnet ausschliesslich monoton und uebersetzt einen reinen
Wall-Clock-Stempel ueber den aktuellen Offset — die beiden Uhren werden nie
innerhalb einer Subtraktion gemischt.

Kleinste wirksame Aenderung an vorhandenen Mechanismen: keine neue Deadline,
kein neues Config-Feld, keine zweite Runtime (Kapitel 09 §3B/§6).

Verankert als versioniertes Overlay (nicht als manueller Eingriff in einen
installierten Hermes): `python host-overlay/patch_rf03_monotonic_stream_clock.py
[--check] [HERMES_SRC]`. Idempotent; bricht laut ab, wenn ein Anker fehlt.

Nachweis: `tools/host_rf_monotonic_probe.py` (rotes Vorher, gruenes Nachher) und
die Engine-eigene Regression `tests/agent/test_stream_wait_notice.py`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_SRC = Path.home() / ".hermes" / "hermes-agent"


def _refuse_live_engine(target: Path, *, check: bool) -> None:
    """Never write into the RUNNING Hermes install by default.

    ``DEFAULT_SRC`` is ``~/.hermes/hermes-agent`` -- the live tree. A bare
    invocation used to mean "patch the engine that is running right now": that
    silently changed the host and made every later "pristine vs patched"
    measurement meaningless (one overlay was found applied in the live tree).
    Writing there now requires GV_ALLOW_LIVE_ENGINE=1; ``--check`` stays allowed
    because it changes nothing.
    """
    if check:
        return
    try:
        is_live = target.resolve() == DEFAULT_SRC.resolve()
    except OSError:
        is_live = False
    if is_live and os.environ.get("GV_ALLOW_LIVE_ENGINE", "") != "1":
        print(
            f"refusing to patch the live engine tree ({target}).\n"
            "Pass an explicit target copy, e.g.:\n"
            "    cp -a ~/.hermes/hermes-agent /tmp/gv-try && "
            "python3 " + Path(__file__).name + " /tmp/gv-try\n"
            "Set GV_ALLOW_LIVE_ENGINE=1 only if you really mean the running install.",
            file=sys.stderr,
        )
        raise SystemExit(2)

# wörtlicher Marker für die Idempotenz-Prüfung (beide Dateien)
MARKER = "gv-host-overlay RF-03"

HELPERS = Path("agent") / "chat_completion_helpers.py"
MONITOR = Path("agent") / "chat_completion_stream_monitor.py"

#: Der Stempel-Schreibvorgang steht fünfmal wortgleich in den Helpers.
STAMP_OLD = '        self.last_chunk_time["t"] = time.time()\n'
STAMP_NEW = "        self._touch_chunk_time()\n"
STAMP_COUNT = 5

HELPERS_INIT_OLD = '        self.last_chunk_time = {"t": time.time()}\n'
HELPERS_INIT_NEW = (
    '        # gv-host-overlay RF-03: `t` = wall clock (diagnostics), `mono` = the clock\n'
    "        # every duration/deadline is measured with; a clock jump cannot fake a stall.\n"
    '        self.last_chunk_time = {"t": time.time(), "mono": time.monotonic()}\n'
)

HELPERS_HELPER_ANCHOR = "    def _count_chunk(self, diag, chunk) -> None:\n"
HELPERS_HELPER_NEW = '''    def _touch_chunk_time(self) -> None:
        """gv-host-overlay RF-03: stamp a real chunk in BOTH clocks.

        ``t`` stays the wall clock because diagnostics and humans read it at face
        value (``first_chunk_at``); ``mono`` is what every duration and deadline is
        MEASURED with, so a wall-clock jump neither fabricates a stall nor masks
        one. Persisted/reported values keep using ``t``.
        """
        self.last_chunk_time["t"] = time.time()
        self.last_chunk_time["mono"] = time.monotonic()

'''


# ---------------------------------------------------------------- monitor ----
CHUNK_STAMP_METHOD = '''    def _chunk_clock(self) -> tuple:
        """gv-host-overlay RF-03: ``(now, last_chunk_at)`` on ONE clock.

        Production stamps both clocks (``t`` wall for diagnostics, ``mono`` for
        measurement) and therefore measures monotonically — a wall-clock jump can
        neither fabricate a stall nor mask one. A wall-only stamp (older callers,
        tests that inject a fake clock) keeps the pre-existing wall-clock
        behaviour. The invariant is that the two clocks are never mixed inside one
        subtraction, not that one clock is forced on every caller.
        """
        stamped = self.last_chunk_time.get("mono")
        if stamped is None:
            return time.time(), self.last_chunk_time["t"]
        return time.monotonic(), stamped

'''

MON_SIG_OLD = "    def _poll_local_load_notice(self, now: float) -> bool:\n"
MON_SIG_NEW = (
    CHUNK_STAMP_METHOD
    + "    def _poll_local_load_notice(self, now: float, last_chunk_at: float) -> bool:\n"
)

POLL_OLD = '''        if now - self.last_chunk_time["t"] < 2.0 or now - m.last_load_poll < 1.0:
            return False
        m.last_load_poll = now
'''
POLL_NEW = '''        if now - last_chunk_at < 2.0 or now - m.last_load_poll < 1.0:
            return False
        m.last_load_poll = now
'''

LOAD_HB_OLD = "            m.load_notice_shown, m.load_notice_misses, m.last_heartbeat = True, 0, now  # loading IS liveness\n"
LOAD_HB_NEW = "            m.load_notice_shown, m.load_notice_misses, m.last_heartbeat = True, 0, now  # loading IS liveness\n"

LOOP_OLD = '''        self._mon = SimpleNamespace(
            last_heartbeat=time.time(), last_load_poll=0.0,
            load_notice_shown=False, load_notice_misses=0, wait_notice_started_ts=None,
        )
        _is_local_base = bool(self.agent.base_url) and is_local_endpoint(self.agent.base_url)
        while not self._call_done.is_set():
            self._call_done.wait(timeout=0.3)
            _hb_now = time.time()
            if _is_local_base and self._poll_local_load_notice(_hb_now):
                continue
'''
LOOP_NEW = '''        # gv-host-overlay RF-03: the monitor runs on ONE clock per read — the same
        # clock the chunk stamp carries (monotonic once stamped with `mono`).
        _hb_now, _last_chunk_at = self._chunk_clock()
        self._mon = SimpleNamespace(
            last_heartbeat=_hb_now, last_load_poll=0.0,
            load_notice_shown=False, load_notice_misses=0, wait_notice_started_ts=None,
        )
        _is_local_base = bool(self.agent.base_url) and is_local_endpoint(self.agent.base_url)
        while not self._call_done.is_set():
            self._call_done.wait(timeout=0.3)
            _hb_now, _last_chunk_at = self._chunk_clock()
            if _is_local_base and self._poll_local_load_notice(_hb_now, _last_chunk_at):
                continue
'''

CLEAR_OLD = '''            if (self._mon.wait_notice_started_ts is not None
                    and self.last_chunk_time["t"] > self._mon.wait_notice_started_ts):
'''
CLEAR_NEW = '''            if (self._mon.wait_notice_started_ts is not None
                    and self._chunk_clock()[1] > self._mon.wait_notice_started_ts):
'''

TAIL_OLD = '''                self._heartbeat(int(_hb_now - self.last_chunk_time["t"]))
            _stale_elapsed = time.time() - self.last_chunk_time["t"]
'''
TAIL_NEW = '''                self._heartbeat(int(_hb_now - self._chunk_clock()[1]))
            _stale_elapsed = _hb_now - self._chunk_clock()[1]
'''

#: Reihenfolge ist bindend: der Stempel-Anker existiert genau fünfmal, BEVOR die
#: neue Hilfsmethode (deren Rumpf denselben Ausdruck enthält) eingefügt wird.
HELPERS_EDITS = [
    (HELPERS_INIT_OLD, HELPERS_INIT_NEW, 1),
    (STAMP_OLD, STAMP_NEW, STAMP_COUNT),
    (HELPERS_HELPER_ANCHOR, HELPERS_HELPER_NEW + HELPERS_HELPER_ANCHOR, 1),
]
MONITOR_EDITS = [
    (MON_SIG_OLD, MON_SIG_NEW, 1),
    (POLL_OLD, POLL_NEW, 1),
    (LOAD_HB_OLD, LOAD_HB_NEW, 1),
    (LOOP_OLD, LOOP_NEW, 1),
    (CLEAR_OLD, CLEAR_NEW, 1),
    (TAIL_OLD, TAIL_NEW, 1),
]


def apply_file(path: Path, edits: list[tuple[str, str, int]], *, check: bool) -> int:
    if not path.is_file():
        print(f"✗ not found: {path}")
        return 2
    original = path.read_text(encoding="utf-8")
    if MARKER in original:
        print(f"✓ already patched: {path}")
        return 0
    text = original
    for old, new, expected in edits:
        found = text.count(old)
        if found != expected:
            print(
                f"✗ anchor missing/ambiguous in {path}: {found}× {old.strip()[:60]!r} "
                f"(expected {expected}) — Hermes version changed? Nothing written."
            )
            return 1
        text = text.replace(old, new, expected)
    if check:
        print(f"• would patch: {path}")
        return 0
    backup = path.with_suffix(".py.gv-backup")
    backup.write_text(original, encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    print(f"✓ patched: {path} (backup: {backup.name})")
    return 0


def patch(target: Path, *, check: bool = False) -> int:
    rc = apply_file(target / HELPERS, HELPERS_EDITS, check=check)
    if rc:
        return rc
    return apply_file(target / MONITOR, MONITOR_EDITS, check=check)


def main() -> int:
    args = [a for a in sys.argv[1:]]
    check = "--check" in args
    args = [a for a in args if not a.startswith("--")]
    target = Path(args[0]) if args else DEFAULT_SRC
    _refuse_live_engine(target, check=check)
    return patch(target, check=check)


if __name__ == "__main__":
    raise SystemExit(main())
