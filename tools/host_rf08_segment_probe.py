#!/usr/bin/env python3
"""RF-08 probe — persistent segment-continuation cursor at the segment boundary.

Run with the ENGINE interpreter against an engine tree:

    <engine-venv>/bin/python tools/host_rf08_segment_probe.py <engine-root>

Case setup: the turn ends budget-exhausted (api_call_count == max_iterations)
inside the REAL ``TurnFinalizer.finalize_turn`` of the given tree, with an
ACTIVE engine goal row for the test session (Gate A via the engine's own
``hermes_cli.goals`` SessionDB state_meta key — a production-visible marker),
HERMES_HOME pointed at a fresh scratch dir, and answered tool calls in the
transcript (Gate C — plausible progress vs no previous record).

PRISTINE (red): no ``_rf08_record_segment_continuation`` exists on the engine;
``finalize_turn`` attaches no ``rf08_segment_continuation`` and writes no
cursor — RC=1 (the meaningful red).

PATCHED (green):
  * the record is written EXACTLY ONCE (one JSON cursor file per key),
  * a second identical call does not duplicate the write,
  * a RESTART test (fresh interpreter, re-read from disk) sees the same cursor
    and no duplicate on re-call,
  * Gate D: no higher segment index without ``HERMES_RF08_ALLOW_MORE_SEGMENTS``.

Exit 0 only on the patched tree, 1 on the pristine tree.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


def make_agent(session_id: str):
    """Agent stand-in with the attrs ``finalize_turn`` actually touches."""
    a = SimpleNamespace(
        max_iterations=80,
        api_call_count=80,
        iteration_budget=None,
        quiet_mode=True,
        suppress_status_output=True,
        _buffer_status=lambda *x, **k: None,
        _safe_print=lambda *x, **k: None,
        _emit_status=lambda *x, **k: None,
        _handle_max_iterations=lambda messages, n: "MODEL SUMMARY ATTEMPTED",
        _response_was_previewed=False,
        session_id=session_id,
        model="rf08-model",
        provider=None,
        base_url=None,
        _tool_guardrail_halt_decision=None,
        _drain_pending_steer=lambda: None,
        _interrupt_message=None,
        clear_interrupt=lambda: None,
        _stream_callback=None,
        _skill_nudge_interval=0,
        _iters_since_skill=0,
        valid_tool_names=set(),
        _sync_external_memory_for_turn=lambda **k: None,
        skip_background_review=True,
        _persist_disabled=True,
        _save_trajectory=lambda *x, **k: None,
        _cleanup_task_resources=lambda *x, **k: None,
        _persist_session=lambda *x, **k: None,
        _drop_trailing_empty_response_scaffolding=lambda *x, **k: None,
    )
    for key in (
        "session_input_tokens", "session_output_tokens", "session_cache_read_tokens",
        "session_cache_write_tokens", "session_reasoning_tokens", "session_prompt_tokens",
        "session_completion_tokens", "session_total_tokens", "session_estimated_cost_usd",
        "session_cost_status", "session_cost_source",
    ):
        setattr(a, key, 0)
    a.session_cost_status = "na"
    a.session_cost_source = "none"
    a.context_compressor = SimpleNamespace(last_real_prompt_tokens=0, last_prompt_tokens=0)
    return a


def make_messages():
    return [
        {"role": "user", "content": "Refactor the parser module and keep tests green"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "write_file", "arguments": "{\"path\": \"src/parser.py\"}"}},
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "wrote 120 lines"},
    ]


def _seed_engine_goal(engine_root: Path, session_id: str, home: Path) -> None:
    """Persist the SessionDB goal row the ENGINE'S own goals store reads.

    Uses the tree's SessionDB plus ``hermes_cli.goals.save_goal`` so Gate A in
    the overlay resolves through the production marker
    (``state_meta ``goal:<session_id>``, status active) — not a synthetic attr.
    """
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(engine_root)!r})\n"
        f"from hermes_cli.goals import GoalState, save_goal\n"
        f"save_goal({session_id!r}, GoalState(goal='Refactor the parser module'))\n"
    )
    proc = subprocess.run(
        [str(engine_root / "venv" / "bin" / "python"), "-c", code],
        capture_output=True, text=True, timeout=180,
        env={**os.environ, "HERMES_HOME": str(home)},
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"goal row seed failed rc={proc.returncode}: {proc.stdout[-400:]!r} {proc.stderr[-400:]!r}"
        )


def _cursor_files(home: Path, session_id: str) -> list:
    base = home / "segment_continuations"
    if not base.is_dir():
        return []
    safe = "".join(c for c in session_id if c.isalnum() or c in "._-")[:80] or "unknown"
    return sorted(base.glob(f"{safe}.segment_*.json"))


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: host_rf08_segment_probe.py <engine-root>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    engine_python = str(root / "venv" / "bin" / "python")

    # Fresh scratch HERMES_HOME so the probe is hermetic.
    home = Path(tempfile.mkdtemp(prefix="rf08_probe_home_"))
    os.environ["HERMES_HOME"] = str(home)

    sys.path.insert(0, str(root))
    import agent.turn_finalizer as tf  # noqa: E402

    if not hasattr(tf, "_rf08_record_segment_continuation"):
        print(
            "PRISTINE RF-08: no _rf08_record_segment_continuation on the engine — "
            "no segment-continuation cursor is written at the segment boundary"
        )
        return 1

    session_id = "rf08-probe-session"
    _seed_engine_goal(root, session_id, home)

    # ── Case 1: first exhausted turn → record written exactly once ──
    agent = make_agent(session_id)
    messages = make_messages()
    result = tf.finalize_turn(
        agent, final_response=None, api_call_count=80, interrupted=False, failed=False,
        messages=messages, conversation_history=messages, effective_task_id="t",
        turn_id="1", user_message="Refactor the parser module and keep tests green",
        original_user_message="Refactor the parser module and keep tests green",
        _should_review_memory=False, _turn_exit_reason="unknown",
    )
    record_1 = result.get("rf08_segment_continuation")
    cursors_1 = _cursor_files(home, session_id)

    checks: list = []
    checks.append((bool(record_1), f"finalize_turn attached rf08_segment_continuation = {type(record_1).__name__}"))
    checks.append((len(cursors_1) == 1, f"cursor files after first call = {len(cursors_1)} (expected 1)"))
    if not record_1:
        for ok, label in checks:
            print(f"{'PASS' if ok else 'FAIL'} {label}")
        return 1

    recorded_index = int(record_1.get("segment_index") or -1)
    checks.append((recorded_index >= 1, f"record carries segment_index={recorded_index}"))
    checks.append((record_1.get("segment_identity") == session_id, "record keyed on the goal-identity session"))
    checks.append((int(record_1.get("api_call_count") or 0) == 80 and int(record_1.get("max_iterations") or 0) == 80,
                   "record carries the exhaustion point (80/80)"))
    checks.append((str(record_1.get("goal_text") or "").startswith("Refactor the parser"),
                   "record carries the goal text"))
    checks.append((int(record_1.get("done_events") or 0) >= 1,
                   "record counts ≥1 answered tool call (Gate C progress plausible)"))
    checks.append(((home / "segment_continuations").is_dir(), "cursor dir lives under the profile-aware hermes home"))
    patched_src = (root / "agent" / "turn_finalizer.py").read_text(encoding="utf-8")
    checks.append(("gv-host-overlay RF-08" in patched_src, "engine file carries the RF-08 overlay marker"))

    # ── Case 2: second identical call must NOT duplicate the cursor ──
    agent2 = make_agent(session_id)
    messages2 = make_messages()
    result2 = tf.finalize_turn(
        agent2, final_response=None, api_call_count=80, interrupted=False, failed=False,
        messages=messages2, conversation_history=messages2, effective_task_id="t",
        turn_id="2", user_message="Refactor the parser module and keep tests green",
        original_user_message="Refactor the parser module and keep tests green",
        _should_review_memory=False, _turn_exit_reason="unknown",
    )
    record_2 = result2.get("rf08_segment_continuation")
    cursors_2 = _cursor_files(home, session_id)

    checks.append((len(cursors_2) == 1, f"cursor files after duplicate call = {len(cursors_2)} (expected 1)"))
    checks.append((
        bool(record_2) and int(record_2.get("segment_index") or -2) == recorded_index,
        "second call resolves the SAME segment_index (idempotent key)",
    ))
    checks.append((recorded_index <= 2, "Gate D: index within the default cap (no ALLOW_MORE)"))

    # ── Case 3: restart — fresh interpreter re-reads the SAME cursor ──
    restart_py = f"""
import sys, os, json
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, {str(root)!r})
os.environ["HERMES_HOME"] = {str(home)!r}
import agent.turn_finalizer as tf

def make_agent(session_id):
    a = SimpleNamespace(
        max_iterations=80, api_call_count=80, iteration_budget=None,
        quiet_mode=True, suppress_status_output=True,
        _buffer_status=lambda *x, **k: None, _safe_print=lambda *x, **k: None,
        _emit_status=lambda *x, **k: None,
        _handle_max_iterations=lambda messages, n: "MODEL SUMMARY ATTEMPTED",
        _response_was_previewed=False, session_id=session_id,
        model="rf08-model", provider=None, base_url=None,
        _tool_guardrail_halt_decision=None, _drain_pending_steer=lambda: None,
        _interrupt_message=None, clear_interrupt=lambda: None, _stream_callback=None,
        _skill_nudge_interval=0, _iters_since_skill=0, valid_tool_names=set(),
        _sync_external_memory_for_turn=lambda **k: None,
        skip_background_review=True, _persist_disabled=True,
        _save_trajectory=lambda *x, **k: None,
        _cleanup_task_resources=lambda *x, **k: None,
        _persist_session=lambda *x, **k: None,
        _drop_trailing_empty_response_scaffolding=lambda *x, **k: None,
    )
    for key in ("session_input_tokens","session_output_tokens","session_cache_read_tokens",
                "session_cache_write_tokens","session_reasoning_tokens","session_prompt_tokens",
                "session_completion_tokens","session_total_tokens","session_estimated_cost_usd",
                "session_cost_status","session_cost_source"):
        setattr(a, key, 0)
    a.session_cost_status = "na"; a.session_cost_source = "none"
    a.context_compressor = SimpleNamespace(last_real_prompt_tokens=0, last_prompt_tokens=0)
    return a

sid = {session_id!r}
agent = make_agent(sid)
messages = [
    {{"role": "user", "content": "Refactor the parser module and keep tests green"}},
    {{"role": "assistant", "content": "", "tool_calls": [
        {{"id": "call_1", "type": "function",
          "function": {{"name": "write_file", "arguments": "{{\\"path\\": \\"src/parser.py\\"}}"}}}},
    ]}},
    {{"role": "tool", "tool_call_id": "call_1", "content": "wrote 120 lines"}},
]
res = tf.finalize_turn(
    agent, final_response=None, api_call_count=80, interrupted=False, failed=False,
    messages=messages, conversation_history=messages, effective_task_id="t",
    turn_id="3", user_message="Refactor the parser module and keep tests green",
    original_user_message="Refactor the parser module and keep tests green",
    _should_review_memory=False, _turn_exit_reason="unknown",
)
files = sorted((Path({str(home)!r}) / "segment_continuations").glob(f"{{sid}}.segment_*.json"))
rec = res.get("rf08_segment_continuation")
print("RESTART_RESULT", json.dumps({{
    "n_cursors": len(files),
    "has_record": bool(rec),
    "same_index": bool(rec) and int(rec.get("segment_index") or -1) == {recorded_index},
}}))
"""
    restart = subprocess.run(
        [engine_python, "-c", restart_py], capture_output=True, text=True, timeout=180,
        env={**os.environ, "HERMES_HOME": str(home)},
    )
    line = [row for row in (restart.stdout or "").splitlines() if row.startswith("RESTART_RESULT")]
    ok_restart = False
    detail = f"rc={restart.returncode} stdout={restart.stdout[-300:]!r} stderr={restart.stderr[-300:]!r}"
    if line:
        payload = json.loads(line[0].split("RESTART_RESULT", 1)[1])
        ok_restart = (
            payload.get("n_cursors") == 1
            and payload.get("has_record") is True
            and payload.get("same_index") is True
        )
    checks.append((ok_restart, f"restart: fresh interpreter re-reads the SAME cursor, no duplicate ({detail})"))

    ok_all = True
    for ok, label in checks:
        print(f"{'PASS' if ok else 'FAIL'} {label}")
        ok_all = ok_all and ok
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
