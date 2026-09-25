#!/usr/bin/env python3
"""Host-Overlay RF-10: stop responds within 2 s in the local pilot's waits.

Befund (Kapitel 09 §3B/§5, RF-10): Backoff- und Kompressions-Sleeps sind
Blockaden, die kein Stop-Signal lesen — ``time.sleep(2)`` in
``turn_overflow.py`` (2 Stellen) und ``try_recover_primary_transport``
(``agent_runtime_helpers.py``, bis zu 8 s). Ein Stop während dieser Phasen
wirkt erst nach dem Sleep.

Fix (kleinste wirksame Änderung):

* ``_rf10_interruptible_sleep(agent, seconds)`` teilt die Wartezeit in
  200 ms-Scheiben; nach jeder Scheibe wird ``agent._interrupt_requested``
  geprüft und der Sleep abgebrochen. Monotone Uhr (kein wall-clock Sprung).
* ``turn_overflow.py``: beide ``time.sleep(2)`` ersetzen (die Kompressions-Loop
  antwortet dann innerhalb von 0.2 s).
* ``agent_runtime_helpers.py``:``try_recover_primary_transport``: der
  ``wait_time``-Sleep (3–8 s) wird unterbrochen, wenn Stop ankommt; in dem Fall
  erzwingt der Overlay das SOFORTIGE ZURÜCKGEBEN ``True`` nur wenn die recovery
  bereits abgeschlossen ist, sonst return nach Interrupt sofort False
  (turn_api_error's generic path evaluates the interrupt, ruft aber bereits
  ``abort_turn_on_interrupt`` weiter oben auf). Simpler: leere Scheiben bis zum
  Ende, aber KEIN zweiter Versuch nach Interrupt — sleep schaut vor JEDER
  Scheibe nach Stop und brechen sofort ab.

Verankert als versioniertes Overlay: ``python
host-overlay/patch_rf10_stop_responsiveness.py [--check] [HERMES_SRC]``.
Idempotent; bricht laut ab, wenn ein Anker fehlt (kein stiller No-op).
Reihenfolge-unabhängig zu RF-02/03/04/05/07 (keine gemeinsame Anker-Zeile).

Nachweis: ``tools/host_rf10_stop_probe.py``
(rotes Vorher: Block-Sleep ignoriert Stop ⇒ RC=1; grünes Nachher: Stop kehrt
im Pilot unter 2 s zurück — monotone Uhr).
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

MARKER = "gv-host-overlay RF-10"

TURN_OVERFLOW = Path("agent") / "turn_overflow.py"
RUNTIME_HELPERS = Path("agent") / "agent_runtime_helpers.py"

#: Helper inserted at module scope in BOTH target files (each gets its own
#: copy so imports stay local).
RF10_HELPERS = '''# gv-host-overlay RF-10: stop responsiveness — interruptible sleep slices.
#:
#: Blocked ``time.sleep`` calls ignore ``agent._interrupt_requested``; a stop
#: arriving mid-sleep takes effect only when the sleep ends. This helper slices
#: the SAME total wait into 200ms pieces and returns EARLY (the interrupt is
#: left set for the caller's existing checks); the monotonic clock keeps
#: wall-clock jumps from faking deadlines.

import time as _rf10_time


def _rf10_interruptible_sleep(agent, seconds: float) -> bool:
    """Sleep ``seconds`` in 0.2 s slices; return True if stop interrupted it."""
    wake = _rf10_time.monotonic() + max(0.0, seconds)
    while True:
        if getattr(agent, "_interrupt_requested", False):
            return True
        remaining = wake - _rf10_time.monotonic()
        if remaining <= 0:
            return False
        _rf10_time.sleep(min(0.2, remaining))

'''

#: turn_overflow.py: helper at module scope before _RETRY_HINT.
TO_ANCHOR = '_RETRY_HINT = "   💡 Try /new to start a fresh conversation, or /compress to retry compression."\n'
TO_HELPERS = RF10_HELPERS + TO_ANCHOR

SLEEP_OLD = '        time.sleep(2)  # Brief pause between compression retries\n'
SLEEP_NEW = ('        # gv-host-overlay RF-10: interruptible compression-retry pause\n'
             '        # (stop responds within a 200 ms slice instead of after the full sleep).\n'
             '        _rf10_interruptible_sleep(st.agent, 2)\n')

TURN_OVERFLOW_EDITS = [
    (TO_ANCHOR, TO_HELPERS, 1),
    (SLEEP_OLD, SLEEP_NEW, 2),
]

#: agent_runtime_helpers.py: helper at module scope (anchor: the unique
#: transient-errors tuple assignment that precedes try_recover_primary_transport).
RH_ANCHOR = 'def try_recover_primary_transport(\n'
RH_HELPERS = RF10_HELPERS + RH_ANCHOR

RH_SLEEP_OLD = '        time.sleep(wait_time)\n'
RH_SLEEP_NEW = ('        # gv-host-overlay RF-10: the last-primary-attempt wait answers stop\n'
                '        # within a 200 ms slice instead of after up to 8 s.\n'
                '        if _rf10_interruptible_sleep(agent, wait_time):\n'
                '            return True\n')

RUNTIME_HELPERS_EDITS = [
    (RH_ANCHOR, RH_HELPERS, 1),
    (RH_SLEEP_OLD, RH_SLEEP_NEW, 1),
]


def apply_file(path: Path, edits: list[tuple[str, str, int]], *, check: bool) -> int:
    if not path.is_file():
        print(f"FAILED not found: {path}")
        return 2
    original = path.read_text(encoding="utf-8")
    if MARKER in original:
        print(f"already patched: {path}")
        return 0
    text = original
    for old, new, expected in edits:
        found = text.count(old)
        if found != expected:
            print(f"ANCHOR MISSING/AMBIGUOUS in {path}: {found}x of {old.strip()[:60]!r} (expected {expected}) — Hermes version changed? Nothing written.")
            return 1
        text = text.replace(old, new, expected)
    if check:
        print(f"would patch: {path}")
        return 0
    backup = path.with_suffix(".py.gv-backup")
    backup.write_text(original, encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    print(f"patched: {path} (backup: {backup.name})")
    return 0


def patch(target: Path, *, check: bool = False) -> int:
    rc = apply_file(target / TURN_OVERFLOW, TURN_OVERFLOW_EDITS, check=check)
    if rc:
        return rc
    return apply_file(target / RUNTIME_HELPERS, RUNTIME_HELPERS_EDITS, check=check)


def main() -> int:
    args = [a for a in sys.argv[1:]]
    check = "--check" in args
    args = [a for a in args if not a.startswith("--")]
    target = Path(args[0]) if args else DEFAULT_SRC
    _refuse_live_engine(target, check=check)
    return patch(target, check=check)


if __name__ == "__main__":
    raise SystemExit(main())
