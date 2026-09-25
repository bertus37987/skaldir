#!/usr/bin/env python3
"""Host-Overlay RF-02: eine requestweite Deadline statt verschachtelter Wartezeiten.

Befund (Kapitel 09 §3B/§4): Jede Ebene bringt ihr eigenes Zeitbudget mit — SDK- und
Transport-Retries, Backoff, Fallback und der Stale-Detektor. Der Stale-Detektor wartet
allein 180s (bei 152k Kontext 300s, gemessen mit `tools/rf_measure.py`), und ein
stummer Provider kann diese Zeit je Versuch erneut ausschöpfen. Es gibt keine
Obergrenze für die LOGISCHE Anfrage; das gemeldete Symptom endete deshalb erst nach
`auto-reconnect at 300s`.

Fix (kleinste wirksame Änderung, kein zweiter Fortsetzungsloop):

* `_request_deadline_secs(agent)` — Gesamtbudget einer logischen Anfrage
  (`HERMES_REQUEST_DEADLINE_SECS`, Pilotprofil 180s aus RFC §3B; `0` schaltet die
  Grenze bewusst ab). Kein bestehender Config-Key aus Kapitel 09 — die Pilotwerte
  sind laut RFC ausdrücklich keine vorhandenen Keys.
* `_StreamingCall` fixiert beim Aufbau EINE monotone Deadline
  (`_request_deadline_mono`).
* Der Stale-Detektor darf nur noch die RESTLICHE Zeit verwenden
  (`min(stale_timeout, Restbudget)`) und meldet getrennt, welche Grenze gegriffen hat
  (Stille vs. Gesamtbudget) — sonst ist im Log nicht unterscheidbar, ob der Provider
  still war oder die Anfrage ihr Budget verbraucht hat.

Damit ist die Deadline die äußere Grenze; Cleanup passiert wie bisher im selben
Pfad und ist durch den Monitor-Takt (0.3s) plus Schließen begrenzt.

Verankert als versioniertes Overlay: `python
host-overlay/patch_rf02_request_deadline.py [--check] [HERMES_SRC]`.
Idempotent; bricht laut ab, wenn ein Anker fehlt. Reihenfolge-unabhängig zu
`patch_rf03_monotonic_stream_clock.py` (kein gemeinsamer Anker).

Nachweis: `tools/host_rf02_deadline_probe.py` (Kill am Budget, Toleranz ≤5s) und
`tests/test_rf02_request_deadline.py`.
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

MARKER = "gv-host-overlay RF-02"

HELPERS = Path("agent") / "chat_completion_helpers.py"
MONITOR = Path("agent") / "chat_completion_stream_monitor.py"

HELPERS_BUDGET_ANCHOR = "def _configured_stale_base(agent) -> float:\n"
HELPERS_BUDGET_NEW = '''#: Request-wide budget for ONE logical model request (RF-02).
#:
#: The interactive pilot profile from RFC §3B: 180s in total, including attempts,
#: backoff and fallback. Every lower layer (SDK retry, transport retry, stale
#: detector, cleanup) may only spend what is LEFT of this budget — no layer below
#: invents a fresh full deadline of its own.
REQUEST_DEADLINE_ENV = "HERMES_REQUEST_DEADLINE_SECS"


def _request_deadline_secs(agent) -> float:
    """Total wall budget for one logical request (seconds); ``0`` = unbounded.

    An explicit per-agent value wins over the environment so tests and hosts can
    pin it without touching the process environment.
    """
    per_agent = getattr(agent, "request_deadline_secs", None)
    if isinstance(per_agent, (int, float)):
        return float(per_agent)
    return env_float(REQUEST_DEADLINE_ENV, 180.0)


'''

HELPERS_INIT_OLD = "        self._stream_stale_timeout = None\n"
HELPERS_INIT_NEW = '''        self._stream_stale_timeout = None
        # gv-host-overlay RF-02: ONE monotonic deadline per logical request, fixed at
        # construction so stream retries, backoff, fallback and the stale detector can
        # only spend the remainder. None = bound switched off on purpose.
        _request_budget = _request_deadline_secs(agent)
        self._request_deadline_mono = (time.monotonic() + _request_budget) if _request_budget else None
'''

KILL_SIG_OLD = "    def _kill_stale_stream(self, elapsed: float) -> None:\n"
KILL_SIG_NEW = "    def _kill_stale_stream(self, elapsed: float, *, deadline_hit: bool = False) -> None:\n"

KILL_LOG_OLD = '''        logger.warning(
            "Stream stale for %.0fs (threshold %.0fs) — no chunks received. model=%s context=~%s tokens. Killing connection.",
            elapsed, self._stream_stale_timeout, self.api_kwargs.get("model", "unknown"), f"{_est_ctx:,}",
        )'''
KILL_LOG_NEW = '''        # gv-host-overlay RF-02: name which bound fired — provider silence or the
        # request-wide budget. Without this the two are indistinguishable in the log.
        if deadline_hit:
            logger.warning(
                "Request deadline reached after %.0fs (budget %.0fs, stale threshold %.0fs) — "
                "no chunks received. model=%s context=~%s tokens. Killing connection.",
                elapsed, elapsed, self._stream_stale_timeout,
                self.api_kwargs.get("model", "unknown"), f"{_est_ctx:,}",
            )
        else:
            logger.warning(
                "Stream stale for %.0fs (threshold %.0fs) — no chunks received. model=%s context=~%s tokens. Killing connection.",
                elapsed, self._stream_stale_timeout, self.api_kwargs.get("model", "unknown"), f"{_est_ctx:,}",
            )'''

HELPERS_EDITS = [
    (HELPERS_BUDGET_ANCHOR, HELPERS_BUDGET_NEW + HELPERS_BUDGET_ANCHOR, 1),
    (HELPERS_INIT_OLD, HELPERS_INIT_NEW, 1),
    (KILL_SIG_OLD, KILL_SIG_NEW, 1),
    (KILL_LOG_OLD, KILL_LOG_NEW, 1),
]

# ---------------------------------------------------------------- monitor ----
MON_HEARTBEAT_ANCHOR = "    def _heartbeat(self, waiting_secs: int) -> None:\n"
MON_DEADLINE_HELPER = '''    def _deadline_left(self):
        """gv-host-overlay RF-02: seconds left of the request-wide budget.

        ``None`` when no deadline was fixed (older callers, tests) or the bound was
        switched off. Every lower layer is bounded by this remainder, so a stalled
        provider cannot restart the full patience on each attempt.
        """
        deadline = getattr(self, "_request_deadline_mono", None)
        if deadline is None:
            return None
        return deadline - time.monotonic()

'''

MON_STALE_OLD = '''            if _stale_elapsed > self._stream_stale_timeout:
                self._mon.wait_notice_started_ts = None  # Reconnect status has its own owner.
                self._kill_stale_stream(_stale_elapsed)
'''
MON_STALE_NEW = '''            # gv-host-overlay RF-02: the request-wide deadline is an ABSOLUTE bound on the
            # logical request — it is not a second staleness value, so it is NOT combined
            # with the stale threshold (elapsed-since-chunk and time-left are different
            # quantities). Either bound may end the attempt; the log names which one did.
            _budget_left = self._deadline_left()
            _deadline_expired = _budget_left is not None and _budget_left <= 0.0
            if _stale_elapsed > self._stream_stale_timeout or _deadline_expired:
                self._mon.wait_notice_started_ts = None  # Reconnect status has its own owner.
                self._kill_stale_stream(
                    _stale_elapsed,
                    deadline_hit=_deadline_expired
                    and _stale_elapsed <= self._stream_stale_timeout,
                )
'''

MONITOR_EDITS = [
    (MON_HEARTBEAT_ANCHOR, MON_DEADLINE_HELPER + MON_HEARTBEAT_ANCHOR, 1),
    (MON_STALE_OLD, MON_STALE_NEW, 1),
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

