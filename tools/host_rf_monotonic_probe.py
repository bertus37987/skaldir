#!/usr/bin/env python3
"""RF-03 probe: does a wall-clock jump falsify the stream stale detector?

Run with the ENGINE's own interpreter, pointing at an engine tree:

    ~/.hermes/hermes-agent/venv/bin/python tools/host_rf_monotonic_probe.py <engine-root>

RF-03 (chapter 09 §4) requires that a slow-but-healthy stream is not falsely
aborted and that *wall-clock jumps do not falsify runtime limits*. The stream
monitor's stale detector derives its elapsed time from the last real chunk, so
the clock that stamps the chunk and the clock that measures it must be the same
ONE, and it must not be the wall clock.

Two cases, driven through the REAL monitor loop:

  A  invariant — the chunk is stamped now in both clocks, then the wall clock
     jumps +1h. A correct detector stays quiet (elapsed ≈ 0); wall-clock
     arithmetic reports ~3600s and kills a healthy stream.
  B  liveness  — the chunk is stamped 1h in the past in both clocks. The
     detector MUST fire (a fix that never fires would be worse than the bug).

Exit 0 only when A stays quiet AND B fires. Prints one PASS/FAIL line per case.
"""
from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

STALE_TIMEOUT = 10.0  # tiny: any wall-clock jump dwarfs it
WALL_JUMP = 3600.0
WINDOW = 1.2  # seconds of real time case A is observed for


def load_monitor(root: str):
    sys.path.insert(0, root)
    from agent.chat_completion_stream_monitor import StreamingWaitMonitor

    return StreamingWaitMonitor


def make_probe(monitor_cls, stamp_age: float):
    class Probe(monitor_cls):
        def __init__(self) -> None:
            self._call_done = threading.Event()
            self.last_chunk_time = {
                "t": time.time() - stamp_age,
                "mono": time.monotonic() - stamp_age,
            }
            self._stream_stale_timeout = STALE_TIMEOUT
            self.api_kwargs = {"model": "rf03-probe"}
            self.agent = SimpleNamespace(
                base_url="",
                _interrupt_requested=False,
                _emit_wait_notice=lambda *a, **k: None,
                _touch_activity=lambda *a, **k: None,
            )
            self.killed: list[float] = []

        def _kill_stale_stream(self, elapsed: float, **kwargs) -> None:
            # RF-02 adds a `deadline_hit` kwarg to the real method; the probe stays
            # valid on pristine, RF-03-only and RF-02+RF-03 trees alike.
            self.killed.append(float(elapsed))
            self._call_done.set()

    return Probe()


def run_case(monitor_cls, *, stamp_age: float, jump_wall: bool) -> list[float]:
    probe = make_probe(monitor_cls, stamp_age)
    real_time = time.time
    if jump_wall:
        time.time = lambda: real_time() + WALL_JUMP  # noqa: E731 — probe only
    try:
        thread = threading.Thread(target=probe._monitor_loop, daemon=True)
        thread.start()
        thread.join(timeout=WINDOW)
        probe._call_done.set()
        thread.join(timeout=2.0)
    finally:
        time.time = real_time
    return probe.killed


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: host_rf_monotonic_probe.py <engine-root>", file=sys.stderr)
        return 2
    monitor_cls = load_monitor(sys.argv[1])

    quiet = run_case(monitor_cls, stamp_age=0.0, jump_wall=True)
    ok_a = not quiet
    print(
        f"{'PASS' if ok_a else 'FAIL'} A wall-clock jump of +{int(WALL_JUMP)}s is inert "
        f"(timeout {STALE_TIMEOUT}s) — kills={[round(k, 1) for k in quiet]}"
    )

    fired = run_case(monitor_cls, stamp_age=WALL_JUMP, jump_wall=False)
    ok_b = bool(fired) and fired[0] > STALE_TIMEOUT * 10
    print(
        f"{'PASS' if ok_b else 'FAIL'} B a real 1h silence still fires the detector — "
        f"kills={[round(k, 1) for k in fired]}"
    )
    return 0 if (ok_a and ok_b) else 1


if __name__ == "__main__":
    raise SystemExit(main())
