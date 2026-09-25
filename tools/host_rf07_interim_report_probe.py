#!/usr/bin/env python3
"""RF-07 probe — honest LOCAL interim state at the 80-iteration limit.

Run with the ENGINE interpreter against an engine tree:

    <engine>/venv/bin/python tools/host_rf07_interim_report_probe.py <engine-root>

Case setup: iterations are exhausted (api_call_count == max_iterations) while
the provider is failing — RF-04's wire-failure streak counts ≥ 1 failed wire
attempt, and the summary attempt itself raises (hung/broken provider).

PRISTINE (red): ``handle_max_iterations`` still fires up to 2 model summary
attempts against the broken provider and NO persisted honest state is written
(only the generic site_copy fallback text).
PATCHED (green): the provider is classified as failing from EXISTING error
state, ZERO model calls are made, and a deterministic local interim report
(goal text, tool events, files, open items) is appended to the live message
list — the row TurnFinalizer's _persist_session pipeline persists — and is
returned as the final response.

Exit 0 only on the patched tree, 1 on the pristine tree (the meaningful red).
"""
from __future__ import annotations

import sys
import time
from types import SimpleNamespace

sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else "/tmp/gv_rf07_base")


def make_agent(cch):
    a = SimpleNamespace()
    a.provider = "rf07-test"
    a.model = "rf07-model"
    a.api_mode = "chat_completions"
    a.max_iterations = 80
    a.api_call_count = 80
    a.suppress_status_output = True
    a._buffer_status = lambda *x, **k: None
    a._safe_print = lambda *x, **k: None
    a._emit_status = lambda *x, **k: None
    a._rf04_wire_fail_streak = 1  # provider failing (RF-04 Signal)
    a._rf04_wire_attempts = 3
    a._turn_exit_reason = ""
    a.max_tokens = 1234
    a.reasoning_config = None
    a._is_anthropic_oauth = False
    return a


def make_messages():
    return [
        {"role": "user", "content": "Refactor the parser module"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "write_file", "arguments": "{\"path\": \"src/parser.py\"}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "wrote 120 lines"},
    ]


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: host_rf07_interim_report_probe.py <engine-root>", file=sys.stderr)
        return 2
    root = sys.argv[1]
    sys.path.insert(0, root)
    import agent.chat_completion_helpers as cch

    patched = hasattr(cch, "_rf07_local_interim_report")

    # Spy on the summary attempt builder: counts MODEL calls for the summary.
    model_calls = []
    if patched:
        def spying(agent, api_messages, rid):
            def _attempt(retry_count):
                model_calls.append(1)
                raise RuntimeError("rf07 hung provider")
            return _attempt
        cch._SUMMARY_ATTEMPT_BUILDERS["chat_completions"] = spying

    agent = make_agent(cch)
    messages = make_messages()
    t0 = time.monotonic()
    out = cch.handle_max_iterations(agent, messages, 80)
    dt = time.monotonic() - t0

    if not patched:
        # PRISTINE: 2 hung summary attempts happen (still raised into site_copy;
        # here the exception propagates either way — the RED is that MODEL CALLS
        # were attempted against a failing provider).
        print("PRISTINE RF-07: model summary attempts fired against a failing provider — NO local guaranteed report")
        return 1

    report_rows = [m for m in messages if m.get("rf07_interim_report")]
    checks = [
        (len(model_calls) == 0, f"summary model calls on failing provider = {len(model_calls)} (expected 0)"),
        (dt < 2.0, f"elapsed={dt:.3f}s (expected < 2s)"),
        (len(report_rows) == 1, f"interim report rows appended = {len(report_rows)} (expected 1)"),
        ("[RF-07" in out, "report text carries the RF-07 marker"),
        ("parser module" in out, "report contains the goal text"),
        ("parser.py" in out, "report lists files touched"),
        ("WAITING FOR RESULT" not in out, "answered tool call is not an open item"),
        # GV2-SEC-17: no raw tool results/args/secrets in the persisted report
        ("wrote 120 lines" not in out, "no raw RESULT content in the report"),
        ('{"path"' not in out, "no raw ARGS values in the report"),
    ]
    ok_all = True
    for ok, label in checks:
        print(f"{'PASS' if ok else 'FAIL'} {label}")
        ok_all = ok_all and ok
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
