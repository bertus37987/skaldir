#!/usr/bin/env python3
"""RF-02 probe: does ONE request-wide budget bound the whole logical request?

Run with an engine interpreter:

    <engine>/venv/bin/python tools/host_rf02_deadline_probe.py <engine-root>

RF-02 (chapter 09 §4): a silent provider, a ping-only stream and a non-cooperative
abort must not exceed the configured TOTAL deadline by more than a justified
cleanup tolerance (≤5s in the local pilot), and no lower layer may invent a fresh
full deadline of its own (RFC §3B).

Cases, all driven through the REAL monitor loop:

  A  budget 3s, stale patience 300s  → the kill lands at the BUDGET (~3s), and it is
     reported as a deadline hit. Without RF-02 the same setup would wait 300s.
  B  budget disabled (0)             → nothing fires inside the window: the opt-out
     really restores the previous unbounded behaviour.
  C  budget 300s, stale patience 2s  → the kill lands at the STALE threshold (~2s)
     and is reported as provider silence. A deadline must bound, never loosen.

Exit 0 only when all three hold.
"""
from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

WINDOW = 12.0
TOLERANCE = 5.0


def load_monitor(root: str):
    sys.path.insert(0, root)
    from agent.chat_completion_stream_monitor import StreamingWaitMonitor

    return StreamingWaitMonitor


class Probe:
    """A monitor-shaped fake: only what the loop and RF-02 touch."""

    def __init__(self, monitor_cls, *, budget: float | None, stale: float) -> None:
        self.monitor_cls = monitor_cls
        self.budget = budget
        self.stale = stale
        self.kills: list[tuple[float, bool]] = []
        self._call_done = threading.Event()

    def build(self):
        probe = self

        class Call(probe.monitor_cls):
            def __init__(self) -> None:
                now = time.monotonic()
                self._call_done = probe._call_done
                self.last_chunk_time = {"t": time.time(), "mono": now}
                self._stream_stale_timeout = probe.stale
                if probe.budget is not None:
                    self._request_deadline_mono = now + probe.budget
                self.api_kwargs = {"model": "rf02-probe"}
                self.agent = SimpleNamespace(
                    base_url="",
                    _interrupt_requested=False,
                    _emit_wait_notice=lambda *a, **k: None,
                    _touch_activity=lambda *a, **k: None,
                )

            def _kill_stale_stream(self, elapsed: float, *, deadline_hit: bool = False) -> None:
                probe.kills.append((float(elapsed), bool(deadline_hit)))
                probe._call_done.set()

        return Call()

    def run(self) -> tuple[list[tuple[float, bool]], float]:
        call = self.build()
        started = time.monotonic()
        thread = threading.Thread(target=call._monitor_loop, daemon=True)
        thread.start()
        thread.join(timeout=WINDOW)
        observed = time.monotonic() - started
        self._call_done.set()
        thread.join(timeout=2.0)
        return self.kills, observed


def case(monitor_cls, label: str, *, budget: float | None, stale: float, want_kill: bool) -> bool:
    probe = Probe(monitor_cls, budget=budget, stale=stale)
    kills, observed = probe.run()
    if not want_kill:
        ok = not kills
        print(
            f"{'PASS' if ok else 'FAIL'} {label} — expected no kill, got "
            f"{[round(k[0], 1) for k in kills]} within {observed:.1f}s"
        )
        return ok
    if not kills:
        print(f"FAIL {label} — no kill within {WINDOW:.0f}s (budget/stale never fired)")
        return False
    elapsed, deadline_hit = kills[0]
    target = budget if (budget is not None and budget < stale) else stale
    overshoot = elapsed - target
    ok = -0.5 <= overshoot <= TOLERANCE and deadline_hit == (budget is not None and budget < stale)
    print(
        f"{'PASS' if ok else 'FAIL'} {label} — fired after {elapsed:.2f}s "
        f"(target {target:.0f}s, overshoot {overshoot:+.2f}s, deadline_hit={deadline_hit})"
    )
    return ok


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    monitor_cls = load_monitor(sys.argv[1])
    results = [
        case(monitor_cls, "A budget 3s / stale 300s → kill at the budget",
             budget=3.0, stale=300.0, want_kill=True),
        case(monitor_cls, "B budget disabled → unchanged (no kill in window)",
             budget=None, stale=300.0, want_kill=False),
        case(monitor_cls, "C budget 300s / stale 2s → kill at the stale threshold",
             budget=300.0, stale=2.0, want_kill=True),
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
