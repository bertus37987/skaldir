#!/usr/bin/env python3
"""RF-04 probe — ONE shared wire-attempt budget bounds the logical request.

Run with the ENGINE interpreter against an engine tree:

    <engine>/venv/bin/python tools/host_rf04_retry_budget_probe.py <engine-root>

Cases (driven through the REAL ``_StreamingCall._call`` retry loop; the wire call
is a subclass override so the harness needs no live provider):

  A  stacking red first, green after — the same agent runs two outer retry legs
     (the way the conversation loop's ``while s.retry_count < s.max_retries`` does)
     with ``HERMES_STREAM_RETRIES=5``. Pristine: no shared budget exists, so each
     leg spends its full stream retry loop → 6 real wire attempts for ONE logical
     request. Patched: the shared counter caps the LOGICAL request at
     ``HERMES_REQUEST_MAX_ATTEMPTS`` (default 3) and later outer legs stop
     opening the socket at all.
  B  classification — hard-fatal ProviderStreamError 401 is not stream-retried on
     either tree; the standard auth classification stays authoritative.
  C  ProviderStreamError 429 (Retry-After path) is not blindly stream-retried on
     either tree; backoff + the authorised fallback remain with the turn layer.

Exit 0 only when every case holds.
"""
from __future__ import annotations

import os
import sys
import threading
from types import SimpleNamespace

WINDOW = 10.0
BUDGET = 3


def load(root: str):
    sys.path.insert(0, root)
    import agent.chat_completion_helpers as cch
    return cch


def make_agent(cch):
    agent = SimpleNamespace(
        base_url="https://rf04.invalid/v1",  # non-local endpoint
        provider="openai",
        api_mode="chat_completions",
        model="rf04-probe",
        _interrupt_requested=False,
        _emit_wait_notice=lambda *a, **k: None,
        _touch_activity=lambda *a, **k: None,
        _buffer_status=lambda *a, **k: None,
        _stream_options_unsupported=False,
        _is_provider_stream_parse_error=lambda e: False,
        _emit_stream_drop=lambda *a, **k: None,
        _log_stream_retry=lambda *a, **k: None,
    )
    agent._rf04_wire_attempts = 0
    agent._rf04_wire_fail_streak = 0
    return agent


def make_call(cch, mode: str, agent, counter: list):
    probe_ref = threading.Event()

    class Call(cch._StreamingCall):
        def _start_stream_attempt(self) -> int:
            counter.append(1)  # probed independently of the engine's own counter
            return super()._start_stream_attempt()

        def _call_wire(self, stream_attempt_id: int):
            if mode == "transient":
                raise ConnectionError("rf04 transport loss")
            status = {"auth": 401, "rate_limit": 429, "unavailable": 503}[mode]
            raise cch.ProviderStreamError(
                status_code=status,
                body={"error": {"message": "rf04 fault", "type": "rate_limit_error"}},
                raw_text=f"HTTP {status}",
            )

    call = Call(agent, api_kwargs={"model": "rf04-probe"}, on_first_delta=None)
    call._call_done = probe_ref
    return call


def run_call(call) -> None:
    try:
        call._call()
    finally:
        call._call_done.set()


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: host_rf04_retry_budget_probe.py <engine-root>", file=sys.stderr)
        return 2
    root = sys.argv[1]
    cch = load(root)
    patched = hasattr(cch, "_rf04_request_max_attempts")
    if patched:
        os.environ["HERMES_REQUEST_MAX_ATTEMPTS"] = str(BUDGET)
    os.environ["HERMES_STREAM_RETRIES"] = "5"
    ok_all = True

    # ------- Case A: two outer legs, one logical request, shared budget -------
    agent = make_agent(cch)
    probe_counter: list[int] = []
    for _ in range(2):
        call = make_call(cch, "transient", agent, probe_counter)
        run_call(call)
    total = len(probe_counter)
    if patched:
        ok = total <= BUDGET
        print(f"{'PASS' if ok else 'FAIL'} A shared budget caps the logical request — wire attempts={total} (limit {BUDGET})")
    else:
        # PRISTINE: stacking is the defect — this FAIL IS the red probe artifact and
        # drives the non-zero exit the repo-pinned non-vacuity test asserts on.
        ok = False
        print(f"FAIL A pristine stacking reproduces the defect — wire attempts={total} (expected ≥ 4 on the unpatched tree: {total} ≥ 4 = {total >= 4})")
    ok_all = ok_all and ok

    if patched:
        before = int(getattr(agent, "_rf04_wire_attempts", 0))
        call = make_call(cch, "transient", agent, probe_counter)
        run_call(call)
        after = int(getattr(agent, "_rf04_wire_attempts", 0))
        ok = (after - before) <= 1
        print(f"{'PASS' if ok else 'FAIL'} A2 exhausted budget: later outer leg opens at most 1 fallback request — added={after - before}")
        ok_all = ok_all and ok

    # ------------- Cases B/C: classification is retained -------------
    for label, mode in (
        ("B auth_error (401) is not stream-retried", "auth"),
        ("C rate_limit (429) Retry-After: no blind stream retry", "rate_limit"),
        ("C2 unavailable (503) is not stream-retried", "unavailable"),
    ):
        agent = make_agent(cch)
        probe_counter = []
        call = make_call(cch, mode, agent, probe_counter)
        run_call(call)
        attempts = len(probe_counter)
        has_error = call.result.get("error") is not None
        ok = attempts == 1 and has_error
        print(f"{'PASS' if ok else 'FAIL'} {label} — attempts={attempts}, error propagated={has_error}")
        ok_all = ok_all and ok

    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
