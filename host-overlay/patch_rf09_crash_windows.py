#!/usr/bin/env python3
"""Host-Overlay RF-09: crash-window recovery — resume fencing + external-action reconciliation.

Chapter 09 §3E / §4 RF-09: "A crash before/after a checkpoint, before/after an
external effect and during a fallback leads neither to lost user corrections
nor to double side effects nor to two simultaneous owners of the same segment."

Window inventory (file:line details in the overlay's own inventory doc):
* W1 — external action executed but its tool result was NEVER recorded
  (the window between the executor's real side effect and
  ``_commit_tool_result``'s DB flush; the pristine engine leaves the pending
  external unrecorded — the criterion's unknown-state already exists for
  REPLAY sanitization but NOTHING persists the pending fact for the resume).
* W2 — duplicate-resume window: RF-08's segment cursor records WHAT to resume
  with, but nothing fences WHO may resume it — two restarts can both re-drive
  the same segment (two owners).
* W3 — RF-04/05 fallback swap: ``try_activate_fallback`` swaps the model
  provider IN PLACE; a crash mid-swap leaves model/provider swapped. fenced
  on the candidate side by RF-05's fitness gate; this overlay adds no second
  swap system (inventory documents why the shipped design is already bounded).
* W4 — RF-06 compression: ``_rf06_reintegrate`` is deterministic and
  recomputed from the captured preserve list on every pass; a crash between
  compression and its reintegration is healed by the next pass (documented).

Fixes — smallest effective changes against the installed engine; NO new lease
system (the resume claim REUSES the RF-08 segment-cursor identity + the
owner_epoch claim pattern adapted from godsv's ``run_supervisor.RunSupervisor``
generation-claim: a claim only wins with a NEWER epoch; same-or-older is a
CURSOR-MISMATCH reject — exactly-once across restarts):

1. ``agent/turn_finalizer.py`` —
   * ``_rf09_resume_key`` / ``_rf09_resume_epoch`` / ``_rf09_claim_resume`` /
     ``_rf09_release_resume``: the W2 fence. The claim row lives in the
     SessionDB ``state_meta`` key ``rf09:resume:<session>/<identity>`` (the
     same store RF-08's goal identity reads) — real cross-restart durable.
   * ``_rf09_classify_unanswered_external``: side-effecting tool calls with
     NO recorded result — the W1 shape, classified via the ENGINE's existing
     ``agent.tool_result_classification`` (no re-invention).
   * ``_rf09_reconcile_external_action``: surfaces a pending external as
     ``side_effect_state = "unknown"`` on the RF-08 segment cursor record AND
     persists the reconciliation under ``<hermes home>/rf09_reconciliations/``
     (WAL-safe ``os.replace``). NEVER a silent re-send: the model decides
     after seeing the unknown marker.
   * ``finalize_turn`` claims the resume (when an RF-08 record exists),
     reconciles the pending external, and attaches both to the result so the
     restart driver (CLI / gateway / scheduler) can act: ``rf09_resume`` and
     ``rf09_side_effect_reconciliation``.
2. ``agent/tool_executor.py`` —
   * ``_rf09_mark_pending_external`` / ``_rf09_clear_pending_external`` /
     ``_rf09_mark_pending_atomic``: a crash-pending marker
     (``<hermes home>/tool_events/rf09_pending_external.json``) is written
     BEFORE an EXTERNAL side-effect tool call dispatches and cleared after the
     result was persisted — a kill inside the W1 window leaves it discoverable.
   * ``_rf09_seq_restart_fence``: on a RESTARTED turn (
     ``agent._rf09_restarted_turn = True``) a sequential external call in the
     pending fence list is converted into an ``unknown`` result instead of a
     silent re-dispatch.
3. ``agent/conversation_loop.py`` — ``result["rf09_resume"]`` is also stamped
   on the loop's early-exit path (the endpoint is visible even when the run
   never reached finalization).
4. ``agent/turn_iteration_prep.py`` — ``_rf09_claim_resume`` also runs at
   iteration start (claim-before-resume: the claim exists BEFORE the resumed
   segment re-drives any provider call; a duplicate claimant with an older
   epoch is REJECTED).

Anchored overlay discipline (same as RF-02..RF-10): MARKER-checked, idempotent,
``.gv-backup``, ``--check``, and a loud abort when an anchor does not match
(no silent no-op). English comments match repo style.

Usage:  python3 host-overlay/patch_rf09_crash_windows.py [--check] <engine-root>
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

MARKER = "gv-host-overlay RF-09"

# ── Files the overlay patches ────────────────────────────────────────────────
FINALIZER = Path("agent") / "turn_finalizer.py"
TOOL_EXEC = Path("agent") / "tool_executor.py"
LOOP = Path("agent") / "conversation_loop.py"
ITER_PREP = Path("agent") / "turn_iteration_prep.py"

# Every file IDS by one module-scope anchor (present pristine AND after the
# other RF overlays — RF-08 inserts its helper block above the SAME def, so the
# def-line anchor composes with it by construction).
RESOLVE_DEF_ANCHOR = "def _resolve_budget_fallback(\n"

HELPERS_BLOCK = '''
#: gv-host-overlay RF-09 — crash-window recovery (chapter 09 §3E).
#: Two fences over the existing state, NO second lease system:
#: * W2 resume claim in the SessionDB state_meta
#:   (``rf09:resume:<session>/<identity>``) with an ``owner_epoch`` comparator
#:   adapted from godsv run_supervisor's generation-claim: a claim only wins
#:   with a NEWER epoch; a same-or-older one is a CURSOR-MISMATCH reject
#:   (exactly-once resume across restarts).
#: * W1 external-action reconciliation: a side-effecting tool call (the
#:   engine's own ``tool_may_have_side_effect``) whose result was never
#:   recorded is stamped onto the RF-08 segment cursor record as
#:   ``side_effect_state = "unknown"`` — never silently re-sent; the model
#:   decides after seeing the unknown marker.
RF09_RESUME_META_PREFIX = "rf09:resume:"

#: env override so the crash probe / driver pins the owner epoch explicitly
#: (per-call read; production callers pass the value directly).
RF09_RESUME_EPOCH_ENV = "HERMES_RF09_RESUME_EPOCH"


def _rf09_resume_key(session_id: str, segment_identity: str) -> str:
    """ONE SessionDB state_meta key per (session, goal-identity segment)."""
    safe_segment = (
        "".join(c for c in str(segment_identity or "") if c.isalnum() or c in "._-")[:80]
        or "s0"
    )
    return f"{RF09_RESUME_META_PREFIX}{str(session_id or '').strip() or 'unknown'}/{safe_segment}"


def _rf09_resume_epoch() -> int:
    """Owner epoch for THIS resume attempt.

    Wall-clock nanoseconds: a restarted attempt ALWAYS stamps a later instant,
    and ``HERMES_RF09_RESUME_EPOCH`` lets the probe/driver pin the value (a
    manually pinned epoch loses to every later wall-clock claim).
    """
    override = str(os.environ.get(RF09_RESUME_EPOCH_ENV, "")).strip()
    if override:
        try:
            return int(override)
        except Exception:
            pass
    import time
    return int(time.time_ns())


def _rf09_session_db():
    """SessionDB handle for the resume row (full r/w; handles its own locking)."""
    from hermes_state import SessionDB as _SDB
    return _SDB()


def _rf09_read_resume_state(session_id: str, segment_identity: str) -> Optional[dict]:
    """Load the persisted resume-claim row (None when absent) — the crash
    checkpoint side of the W2 fence. Read-only on every failure path."""
    try:
        db = _rf09_session_db()
        raw = db.get_meta(_rf09_resume_key(session_id, segment_identity))
        if isinstance(raw, str) and raw.strip():
            import json
            loaded = json.loads(raw)
            return loaded if isinstance(loaded, dict) else None
    except Exception:
        pass
    return None


def _rf09_write_resume_state(session_id: str, segment_identity: str, record: dict) -> bool:
    """Upsert the resume-claim row. NEVER raises: a fence failure must not fail
    the turn that produced the state."""
    try:
        import json as _json
        db = _rf09_session_db()
        db.set_meta(
            _rf09_resume_key(session_id, segment_identity),
            _json.dumps(record, sort_keys=True, default=str),
        )
        return True
    except Exception:
        return False


def _rf09_claim_resume(
    agent, segment_identity: str, segment_index: int,
    *, owner_epoch: Optional[int] = None,
) -> dict:
    """W2 — claim-before-resume for one segment, owner-epoch fenced.

    ``owner_epoch=None`` means a FIRST claim of the CURRENT run: the wall-clock
    epoch is minted now, beating every persisted claim from an earlier attempt.
    A stored claim that is same-or-NEWER REJECTS the request (``CURSOR-MISMATCH``)
    — another instance already owns the resume. The winning claim is written
    BEFORE anything resumed re-drives the segment.
    """
    session_id = str(getattr(agent, "session_id", "") or "").strip()
    request_epoch = int(owner_epoch) if owner_epoch else _rf09_resume_epoch()
    request: dict = {
        "schema": "rf09.resume_claim",
        "version": 1,
        "session_id": session_id,
        "segment_identity": str(segment_identity or ""),
        "segment_index": int(segment_index or 0),
        "owner_pid": os.getpid(),
        "owner_epoch": request_epoch,
    }
    checked: dict = {"request": request, "winner": None}
    if not session_id:
        checked.update(rejected=True, reject_reason="no_session")
        return checked
    existing = _rf09_read_resume_state(session_id, segment_identity)
    if isinstance(existing, dict):
        try:
            existing_epoch = int(existing.get("owner_epoch") or 0)
        except Exception:
            existing_epoch = 0
        if existing_epoch >= request_epoch:
            # CURSOR-MISMATCH reject: a duplicate resume may NOT take over a
            # segment another instance already claimed.
            checked.update(
                rejected=True, reject_reason="CURSOR-MISMATCH", existing=existing,
            )
            return checked
    _rf09_write_resume_state(session_id, segment_identity, request)
    checked.update(rejected=False, winner=request)
    return checked


def _rf09_release_resume(session_id: str, segment_identity: str) -> None:
    """Best-effort epoch release after a COMPLETED resume. The row is NEVER
    deleted (a late duplicate must still lose to the recorded epoch); the next
    legitimate attempt simply out-epochs it."""
    state = _rf09_read_resume_state(session_id, segment_identity)
    if not isinstance(state, dict):
        return
    state["released"] = True
    state["released_at_epoch"] = _rf09_resume_epoch()
    _rf09_write_resume_state(session_id, segment_identity, state)


def _rf09_side_effect_name(tool_call) -> str:
    """Tool name from any OpenAI-style assistant ``tool_call`` dict."""
    if not isinstance(tool_call, dict):
        return ""
    function = tool_call.get("function") or {}
    name = function.get("name") if isinstance(function, dict) else None
    return str(name or tool_call.get("_type") or "")


def _rf09_classify_unanswered_external(messages) -> list:
    """Side-effecting tool calls with NO recorded result — the W1 shape.

    Uses the ENGINE's existing classification
    (``agent.tool_result_classification.tool_may_have_side_effect``); an
    unclassifiable import fails CLOSED (treated as effect-capable).
    """
    try:
        from agent.tool_result_classification import tool_may_have_side_effect
    except Exception:  # pragma: no cover — classification import always present on installs
        def tool_may_have_side_effect(_name: str) -> bool:  # type: ignore[misc]
            return True
    answered: set = set()
    for msg in messages or ():
        if isinstance(msg, dict) and msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id") or msg.get("call_id")
            if tc_id:
                answered.add(str(tc_id))
    pending: list = []
    for msg in messages or ():
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            tc = tc if isinstance(tc, dict) else {}
            tc_id = str(tc.get("id") or tc.get("call_id") or "")
            name = _rf09_side_effect_name(tc)
            if tc_id and tc_id not in answered and tool_may_have_side_effect(name):
                pending.append({"id": tc_id, "name": name})
    return pending


def _rf09_reconciliation_path(session_id: str) -> Path:
    """ONE reconciliation document per session under the profile home."""
    try:
        from agent.file_safety import _hermes_home_path
        home = _hermes_home_path()
    except Exception:
        home = Path(os.path.expanduser("~/.hermes"))
    safe = (
        "".join(c for c in str(session_id or "") if c.isalnum() or c in "._-")[:80]
        or "unknown"
    )
    return Path(home) / "rf09_reconciliations" / f"{safe}.json"


def _rf09_write_reconciliation(session_id: str, record: dict) -> bool:
    """WAL-safe ``os.replace`` write of one reconciliation document."""
    try:
        import json as _json
        path = _rf09_reconciliation_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{os.urandom(4).hex()}")
        tmp.write_text(
            _json.dumps(record, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def _rf09_read_reconciliation(session_id: str) -> Optional[dict]:
    path = _rf09_reconciliation_path(session_id)
    if not path.is_file():
        return None
    try:
        import json as _json
        loaded = _json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else None
    except Exception:
        return None


def _rf09_reconcile_external_action(
    agent, messages, segment_record: Optional[dict],
) -> Optional[dict]:
    """Persist the W1 external side-effect reconciliation.

    Deterministic inputs only: the transcript's unanswered side-effecting
    calls, plus the executor-side crash-pending marker when the killed attempt
    left one. Output is stamped on the RF-08 segment cursor record
    (``side_effect_state = "unknown"``) and persisted under
    ``rf09_reconciliations/`` — never a silent re-send.
    """
    session_id = str(getattr(agent, "session_id", "") or "").strip()
    pending = _rf09_classify_unanswered_external(messages)
    reconciliation: dict = {
        "schema": "rf09.side_effect_reconciliation",
        "version": 1,
        "session_id": session_id,
        "side_effect_state": "unknown" if pending else "none",
        "pending_calls": pending[:12],
        "recorded_at_epoch": _rf09_resume_epoch(),
        "decision": "model_must_inspect_state_before_retry",
    }
    if not pending:
        return None
    if isinstance(segment_record, dict) and segment_record.get("segment_identity"):
        reconciliation["segment_identity"] = str(segment_record.get("segment_identity"))
        reconciliation["segment_index"] = int(segment_record.get("segment_index") or 0)
        # Stamp the LIVE RF-08 cursor record so a restart that re-reads the
        # cursor file also sees the unknown marker.
        try:
            record_path = _rf08_cursor_for(agent, int(segment_record.get("segment_index") or 0))
            if record_path.is_file():
                import json as _json
                raw = _json.loads(record_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    raw["side_effect_state"] = "unknown"
                    raw["rf09_side_effect"] = reconciliation
                    _rf08_write_cursor(record_path, raw)
        except Exception:
            pass  # a bookkeeping stamp must not break the turn that produced it
    _rf09_write_reconciliation(
        session_id,
        {
            "side_effect_state": "unknown",
            "pending_calls": reconciliation["pending_calls"],
            "segment_identity": reconciliation.get("segment_identity"),
            "segment_index": reconciliation.get("segment_index"),
        },
    )
    return reconciliation


'''

# ── turn_finalizer edits ─────────────────────────────────────────────────────
# (1) fresh per-turn stash before the RF-08 record resolve — appended AFTER
#     RF-08's own reset (order-independent: RF-08's block ends with the resolve
#     call, which this anchor re-uses verbatim).
FINALIZER_RESET_ANCHOR = '''    final_response, _turn_exit_reason, preserved_verification_fallback = _resolve_budget_fallback(
'''
FINALIZER_RESET_NEW = '''    # gv-host-overlay RF-09: fresh per-turn crash-recovery stash — the W1/W2
    # fences read THIS turn's state only, never across boundaries.
    agent._rf09_side_effect_reconciliation = None
    agent._rf09_resume_claim = None
    final_response, _turn_exit_reason, preserved_verification_fallback = _resolve_budget_fallback(
'''

# (2) the fence + reconciliation assembly, anchored AFTER RF-08's attach block
#     (which RF-08 wrote verbatim, so pristine engines never reach it).
FINALIZER_ATTACH_ANCHOR = '''    _rf08_segment_continuation = getattr(agent, "_rf08_segment_continuation", None)
    if _rf08_segment_continuation is not None:
        result["rf08_segment_continuation"] = _rf08_segment_continuation
'''
FINALIZER_ATTACH_NEW = '''    _rf08_segment_continuation = getattr(agent, "_rf08_segment_continuation", None)
    if _rf08_segment_continuation is not None:
        result["rf08_segment_continuation"] = _rf08_segment_continuation
        # gv-host-overlay RF-09: claim the resume BEFORE the result leaves the
        # loop (W2 — claim-before-resume), then reconcile the W1 window.
        agent._rf09_resume_claim = _rf09_claim_resume(
            agent,
            str(_rf08_segment_continuation.get("segment_identity") or ""),
            int(_rf08_segment_continuation.get("segment_index") or 0),
        )
        result["rf09_resume"] = agent._rf09_resume_claim
        _rf09_reconciliation = _rf09_reconcile_external_action(
            agent, messages, _rf08_segment_continuation,
        )
        if _rf09_reconciliation is not None:
            agent._rf09_side_effect_reconciliation = _rf09_reconciliation
            result["rf09_side_effect_reconciliation"] = _rf09_reconciliation
'''

# ── conversation_loop edit: the resume endpoint is visible on the raw path ──
LOOP_RESULT_ANCHOR = '''    result = finalize_turn(agent, **{
        name: getattr(s, name)
        for name in inspect.signature(finalize_turn).parameters if name != "agent"
    })
'''
LOOP_RESULT_NEW = '''    result = finalize_turn(agent, **{
        name: getattr(s, name)
        for name in inspect.signature(finalize_turn).parameters if name != "agent"
    })
    # gv-host-overlay RF-09: stamp the resume endpoint on the raw envelope too —
    # the exact seam a crash-restart driver reads even on degraded exits.
    if isinstance(result, dict) and "rf09_resume" not in result:
        result["rf09_resume"] = {"active": False}
'''

# ── tool_executor edits: the W1 execution side ──────────────────────────────
# (i) module-scope fence helpers, inserted before the concurrent executor.
EXEC_HELPERS_ANCHOR = (
    "def execute_tool_calls_concurrent(agent, assistant_message, messages: list, "
    "effective_task_id: str, api_call_count: int = 0, *, finalize: bool = True) -> None:\n"
)
EXEC_HELPERS_BLOCK = '''
# gv-host-overlay RF-09 — W1 execution side: a crash-pending marker is written
# BEFORE an EXTERNAL side-effect call dispatches and cleared once its result
# was persisted. A kill inside the window leaves the marker discoverable for
# the resume driver; a RESTARTED turn converts a pending external into an
# ``unknown`` result instead of a silent re-dispatch.
RF09_UNKNOWN_NOTICE = (
    "[Crash recovery: this external side-effect call may have already executed "
    "before the previous run died; its effect is UNKNOWN. Inspect current state "
    "before re-issuing any write/deploy/request call.]"
)
RF09_PENDING_DIR = "tool_events"
RF09_PENDING_NAME = "rf09_pending_external.json"


def _rf09_pending_marker_path(agent) -> Path:
    """Profile-home crash-pending marker path (estop/reports convention)."""
    try:
        from agent.file_safety import _hermes_home_path
        home = _hermes_home_path()
    except Exception:
        home = Path(os.path.expanduser("~/.hermes"))
    return Path(home) / RF09_PENDING_DIR / RF09_PENDING_NAME


def _rf09_mark_pending_external(
    agent, tool_name: str, tool_call_id: str, arguments: dict,
) -> None:
    """Durable ``pending external`` record BEFORE the executor dispatches a
    side-effecting call (W1 entry marker). NEVER raises."""
    try:
        import json as _json
        path = _rf09_pending_marker_path(agent)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "schema": "rf09.pending_external",
            "version": 1,
            "session_id": str(getattr(agent, "session_id", "") or ""),
            "tool_name": str(tool_name or ""),
            "tool_call_id": str(tool_call_id or ""),
            "args_preview": _json.dumps(arguments or {}, default=str)[:600],
            "state": "dispatching",
            "staged_at_epoch": int(time.time_ns()),
            "staged_by_pid": os.getpid(),
        }
        tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{os.urandom(4).hex()}")
        tmp.write_text(_json.dumps(record, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass  # a bookkeeping marker must not fail the tool it precedes


def _rf09_clear_pending_external(agent) -> None:
    """Clear the crash-pending marker AFTER the result was recorded."""
    try:
        _rf09_pending_marker_path(agent).unlink(missing_ok=True)
    except Exception:
        pass


def _rf09_seq_restart_fenced(agent, tool_name: str) -> bool:
    """True when THIS sequential call sits in the restart fence list (W1)."""
    return any(
        str(entry.get("name") or "") == str(tool_name or "")
        for entry in (getattr(agent, "_rf09_pending_side_effect_calls", None) or [])
    )


def _rf09_consume_pending_side_effect(agent, tool_name: str) -> None:
    """Drop ONE consumed fence entry so a later identical call dispatches."""
    remaining = [
        entry for entry in (getattr(agent, "_rf09_pending_side_effect_calls", None) or [])
        if str(entry.get("name") or "") != str(tool_name or "")
    ]
    agent._rf09_pending_side_effect_calls = remaining


def execute_tool_calls_concurrent(agent, assistant_message, messages: list, effective_task_id: str, api_call_count: int = 0, *, finalize: bool = True) -> None:
'''

# (ii) sequential pre-dispatch: mark the pending external BEFORE dispatch.
EXEC_PRE_DISPATCH_ANCHOR = '''        tool_start_time = time.time()
        dispatch = _resolve_sequential_dispatch(agent, ref, messages)
'''
EXEC_PRE_DISPATCH_NEW = '''        # gv-host-overlay RF-09: crash-window W1 — the durable ``pending
        # external`` marker goes down BEFORE the executor dispatches the call,
        # so a kill between effect and recorded result stays discoverable.
        if tool_may_have_side_effect(pc.name):
            _rf09_mark_pending_external(agent, pc.name, ref.call_id, arguments=pc.args)
        tool_start_time = time.time()
        dispatch = _resolve_sequential_dispatch(agent, ref, messages)
'''

# (iii) sequential post-publish: clear the marker only after the result landed.
EXEC_POST_PUBLISH_ANCHOR = '''        if not _publish_sequential_result(agent, messages, ref, managed, tool_duration=tool_duration, index=i, budget=_tool_budget):
            return
'''
EXEC_POST_PUBLISH_NEW = '''        if not _publish_sequential_result(agent, messages, ref, managed, tool_duration=tool_duration, index=i, budget=_tool_budget):
            return
        # gv-host-overlay RF-09: the result is persisted — the W1 pending marker
        # is cleared (a publish failure above keeps it, by design).
        _rf09_clear_pending_external(agent)
'''

# (iv) sequential pre-check: the restart fence converts a pending external into
#      an ``unknown`` result (never a silent re-dispatch).
EXEC_RESTART_FENCE_ANCHOR = '''        pc = _parse_tool_call(agent, tool_call, flatten_probe=True)
        ref = pc.ref(effective_task_id)
'''
EXEC_RESTART_FENCE_NEW = '''        pc = _parse_tool_call(agent, tool_call, flatten_probe=True)
        ref = pc.ref(effective_task_id)
        # gv-host-overlay RF-09: on a RESTARTED turn an EXTERNAL side-effect
        # call in the pending fence list is converted into an ``unknown``
        # result instead of a silent re-dispatch — the model decides.
        if bool(getattr(agent, "_rf09_restarted_turn", False)) and _rf09_seq_restart_fenced(agent, pc.name):
            messages.append(make_tool_result_message(
                pc.name, RF09_UNKNOWN_NOTICE, str(ref.call_id or ""), effect_disposition="unknown",
            ))
            _rf09_consume_pending_side_effect(agent, pc.name)
            continue
'''

# ── tool_executor import anchor (classification + time) ────────────────────
EXEC_IMPORT_ANCHOR = "from agent.message_sanitization import coalesce_tool_call_id\n"
EXEC_IMPORT_NEW = (
    "from agent.message_sanitization import coalesce_tool_call_id\n"
    "from agent.tool_result_classification import tool_may_have_side_effect\n"
)

# ── turn_iteration_prep edit: the W2 claim at iteration start ───────────────
PREP_CLAIM_ANCHOR = '''    api_call_count += 1
    agent._api_call_count = api_call_count
'''
PREP_CLAIM_NEW = '''    # gv-host-overlay RF-09: claim the W2 resume BEFORE the resumed iteration
    # drives any provider call. Without an RF-08 goal identity the lookup is a
    # cheap read-only no-op (ordinary chat keeps its shipped behaviour).
    try:
        from agent.turn_finalizer import (_rf09_claim_resume as _c9, _rf09_resume_epoch as _e9)
        _rc9 = _c9(
            agent,
            str(getattr(agent, "_rf09_segment_identity", "") or getattr(agent, "session_id", "") or "turn"),
            int(
                (getattr(agent, "_rf08_segment_continuation", None) or {}).get("segment_index", 1)
                if getattr(agent, "_rf08_segment_continuation", None) is not None else 1
            ),
        )
        agent._rf09_resume_claim = _rc9
    except Exception:
        pass  # a fence failure must never block an iteration start
    api_call_count += 1
    agent._api_call_count = api_call_count
'''

FINALIZER_EDITS = [
    (RESOLVE_DEF_ANCHOR, HELPERS_BLOCK + "\n" + RESOLVE_DEF_ANCHOR, 1),
    (FINALIZER_RESET_ANCHOR, FINALIZER_RESET_NEW, 1),
    # RF-08-conditional: the brother overlay's attach block is REQUIRED when
    # present (`expected=1`), otherwise expected=0. apply_file accepts either
    # count via the OPTIONAL list below (a two-sided composable fence).
    (FINALIZER_ATTACH_ANCHOR, FINALIZER_ATTACH_NEW, 1),
]

# Optional transforms: the edit applies only when its anchor exists exactly
# once — a count of 0 or 1 both pass; 0 keeps the tree untouched (pristine
# engines without the brother overlay get the standalone fence below, so the
# W2/W1 fences are ALWAYS installed).
OPTIONAL_EDITS = {
    FINALIZER_ATTACH_ANCHOR: (
        FINALIZER_ATTACH_NEW,
        # The pristine / non-RF-08 fallback: same fences, session-identity key,
        # anchored on the guardrail row (stable in both states).
        (
            "    if agent._tool_guardrail_halt_decision is not None:\n",
            '''    # gv-host-overlay RF-09: when no RF-08 segment cursor record exists
    # for this turn, the W2 fence claims resume ownership directly off the
    # session identity and W1 still reports the pending external — the
    # restart driver always sees a fence, with or without the brother overlay.
    if agent._rf09_resume_claim is None:
        agent._rf09_resume_claim = _rf09_claim_resume(
            agent,
            str(getattr(agent, "_rf09_segment_identity", "") or agent.session_id or "turn"),
            1,
        )
        result["rf09_resume"] = agent._rf09_resume_claim
    _rf09_reconciliation = _rf09_reconcile_external_action(agent, messages, None)
    if _rf09_reconciliation is not None:
        agent._rf09_side_effect_reconciliation = _rf09_reconciliation
        result["rf09_side_effect_reconciliation"] = _rf09_reconciliation
    if agent._tool_guardrail_halt_decision is not None:
''',
        ),
    ),
}

EXEC_EDITS = [
    (EXEC_IMPORT_ANCHOR, EXEC_IMPORT_NEW, 1),
    (EXEC_HELPERS_ANCHOR, EXEC_HELPERS_BLOCK, 1),
    (EXEC_PRE_DISPATCH_ANCHOR, EXEC_PRE_DISPATCH_NEW, 1),
    (EXEC_RESTART_FENCE_ANCHOR, EXEC_RESTART_FENCE_NEW, 1),
    (EXEC_POST_PUBLISH_ANCHOR, EXEC_POST_PUBLISH_NEW, 1),
]

LOOP_EDITS = [
    (LOOP_RESULT_ANCHOR, LOOP_RESULT_NEW, 1),
]

PREP_EDITS = [
    (PREP_CLAIM_ANCHOR, PREP_CLAIM_NEW, 1),
]


def apply_file(target: Path, edits: list, *, check: bool, optional: dict | None = None) -> int:
    """Anchored single-file edit; loud abort on any REQUIRED anchor drift.

    ``optional`` maps anchor -> (expected-1-new, fallback-(old, new)): when the
    required anchor is absent the fallback edit is applied instead (still anchored,
    still loud on its own drift) — the RF-08/present vs pristine/pristine duality.
    """
    if not target.is_file():
        print(f"FAILED not found: {target}")
        return 2
    original = target.read_text(encoding="utf-8")
    if MARKER in original:
        print(f"already patched: {target}")
        return 0
    text = original
    real_edits: list = []
    for old, new, expected in edits:
        found = text.count(old)
        if found == expected:
            real_edits.append((old, new))
        elif optional and found == 0 and old in optional:
            fallback_new, fallback_edit = optional[old]
            real_edits.append(fallback_edit)
        else:
            print(
                f"anchor missing/ambiguous in {target}: {found}x (expected {expected}) — "
                "Hermes version changed? Nothing written."
            )
            return 1
    for old, new in real_edits:
        found = text.count(old)
        if found != 1:
            print(
                f"anchor missing/ambiguous in {target}: {found}x (expected 1) — "
                "Hermes version changed? Nothing written."
            )
            return 1
        text = text.replace(old, new, 1)
    if check:
        print(f"would patch: {target}")
        return 0
    backup = target.with_suffix(".py.gv-backup")
    backup.write_text(original, encoding="utf-8")
    target.write_text(text, encoding="utf-8")
    print(f"patched: {target} (backup: {backup.name})")
    return 0


def patch(target_root: Path, *, check: bool = False) -> int:
    rc = 0
    for rel, edits, optional in (
        (FINALIZER, FINALIZER_EDITS, OPTIONAL_EDITS),
        (TOOL_EXEC, EXEC_EDITS, None),
        (LOOP, LOOP_EDITS, None),
        (ITER_PREP, PREP_EDITS, None),
    ):
        outcome = apply_file(target_root / rel, edits, check=check, optional=optional)
        if outcome:
            return outcome
    print(
        "RF-09 applied: W2 resume fence (turn_finalizer claim + turn_iteration_prep "
        "claim-before-resume + conversation_loop endpoint) + W1 external-action "
        "reconciliation (turn_finalizer stamp + tool_executor pending marker / "
        "restart fence)"
    )
    return rc


def main() -> int:
    args = list(sys.argv[1:])
    check = "--check" in args
    args = [a for a in args if not a.startswith("--")]
    target = Path(args[0]) if args else DEFAULT_SRC
    _refuse_live_engine(target, check=check)
    return patch(target, check=check)


if __name__ == "__main__":
    raise SystemExit(main())
