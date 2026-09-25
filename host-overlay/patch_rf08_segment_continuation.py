#!/usr/bin/env python3
"""Host-Overlay RF-08: persistent segment continuation under the same goal identity.

Chapter 09 §3E/§4 RF-08: at the 80-iteration segment boundary the engine ends
the turn with a summary and nothing that survives a restart as an addressable
continuation. This overlay adds a bounded, restart-safe segment-cursor write at
the budget-exhaustion point of ``agent/turn_finalizer.py`` — NOT a goal store:

* ``_rf08_segment_summary`` — deterministic worksheet straight from the live
  session rows: goal text (newest real user row), done = answered tool calls,
  open = ``_rf07_collect_interim_state`` when the RF-07 overlay is present.
* ``_rf08_progress_plausible`` — gate C against the previous record.
* ``_rf08_record_segment_continuation`` — the four gates:
    A goal-authorized (read-only: engine goal row via ``hermes_cli.goals.load_goal``;
      if godsv is importable in-process, ``godsv.completion.CompletionStore``
      against the user's hermes home; never a second goal store),
    B safety holds absent (not aborted/interrupted),
    C progress plausible (open_items decreased OR done_events >= 1 vs previous),
    D cap ``HERMES_RF08_MAX_AUTO_SEGMENTS`` (default 2); higher indices need
      ``HERMES_RF08_ALLOW_MORE_SEGMENTS=1``. Without authorization an ordinary
      chat stays an ordinary chat — no hidden forever-continuation.
* ``finalize_turn`` attaches ``result["rf08_segment_continuation"]`` so callers
  (CLI / gateway / task scheduler) can act on the pending continuation.

Records are persisted as ONE JSON cursor file per session under
``<hermes home>/segment_continuations/`` via ``os.replace`` (WAL-safe, written
once per key: idempotent and restart-safe).

Anchored overlay discipline (same as RF-02..RF-10): MARKER-checked, idempotent,
``.gv-backup``, ``--check``, and a loud abort when an anchor does not match
(no silent no-op). English quotes kept minimal; comments match repo style.

Usage:  python3 host-overlay/patch_rf08_segment_continuation.py [--check] <engine-root>
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

MARKER = "gv-host-overlay RF-08"

TARGET = Path("agent") / "turn_finalizer.py"

# ── Anchor 1: top-of-module import block (module scope, unique) ─────────────
IMPORTS_ANCHOR = '''from agent.message_content import flatten_message_text
from agent.message_metadata import append_message, stamp_message_timestamp
from agent.message_sanitization import _sanitize_surrogates
'''

HELPERS_BLOCK = '''

#: gv-host-overlay RF-08 — persistent segment continuation under the same goal
#: identity (chapter 09 §3E). This is a segment-CURSOR file, not a goal store:
#: one JSON document per (session_id, segment_index) key, written once per key
#: via ``os.replace`` so a restart reads the same cursor and never duplicates.
RF08_SEGMENT_DIR = "segment_continuations"
RF08_SCHEMA_VERSION = 1


def _rf08_env_int(key: str, default: int) -> int:
    try:
        from utils import env_int
        return env_int(key, default)
    except Exception:
        try:
            raw = os.environ.get(key, "").strip()
            return int(raw) if raw else int(default)
        except Exception:
            return int(default)


def _rf08_segment_content_text(message) -> str:
    """Flatten one row's visible text (composers may return non-dicts)."""
    try:
        text = flatten_message_text(message.get("content"))
        return text if isinstance(text, str) else ""
    except Exception:
        content = (message or {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts)
        return ""


def _rf08_segment_summary(agent, messages) -> dict:
    """Deterministic worksheet at the segment boundary (§3E).

    * goal text: the newest real user row (compression/summary rows excluded);
    * done: tool calls that received an answer (``tool_call_id`` resolved);
    * open: unresolved tool calls — via ``_rf07_collect_interim_state`` when the
      RF-07 helper is present, else a local answer-unanswered count.
    No model call, no store write, nothing invented.
    """
    state: dict = {"goal": "", "done": 0, "open": 0, "open_items": []}
    if not isinstance(messages, list):
        return state
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        text = _rf08_segment_content_text(msg).strip()
        if not text:
            continue
        meta = msg.get("display_metadata") or {}
        if isinstance(meta, dict) and (
            meta.get("compressed_summary") or meta.get("_compressed_summary")
        ):
            continue
        if msg.get("_persist_user_override") or msg.get("_DB_PERSISTED_MARKER"):
            continue
        try:
            from agent.context_compressor import MAX_ITERATIONS_SUMMARY_REQUEST
            if MAX_ITERATIONS_SUMMARY_REQUEST[:40] in text:
                continue
        except Exception:
            pass
        # keep the NEWEST real user row (later rows overwrite earlier ones)
        if any(flag in msg for flag in ("rf07_interim_report",)):
            continue
        state["goal"] = text[:2000]
    answered: set = set()
    open_ids: list = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id") or msg.get("call_id")
            if tc_id:
                answered.add(tc_id)
    for msg in messages:
        if not isinstance(msg, dict) or not msg.get("tool_calls"):
            continue
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            tc_id = tc.get("id") or tc.get("call_id")
            if tc_id and tc_id not in answered:
                name = ((tc.get("function") or {}).get("name")) or (tc.get("_type") or "tool")
                open_ids.append(f"unanswered tool call {name} ({tc_id})")
    state["done"] = len(answered)
    state["open"] = len(open_ids)
    state["open_items"] = open_ids[-12:]
    # Prefer RF-07's collector signal when the brother overlay is present.
    try:
        from agent.chat_completion_helpers import _rf07_collect_interim_state
        interim = _rf07_collect_interim_state(messages)
        open_items = interim.get("open_items") or []
        if isinstance(open_items, list):
            # RF-07 reports the same unanswered-call facts — use its view.
            state["open"] = len(open_items)
            state["open_items"] = [str(x)[:120] for x in open_items][-12:]
    except Exception:
        pass
    return state


def _rf08_goal_identity(agent) -> Optional[str]:
    """Gate A — goal-authorized continuation. READ-ONLY, in this order:

    1. an explicit override attr (``agent._rf08_goal_identity``) — probes/tests only;
    2. a live standing-goal row for THIS session (``hermes_cli.goals.load_goal``,
       ``status == 'active'``) — the marker the engine already carries;
    3. if godsv is importable in-process, the completion contract state read
       READ-ONLY via ``godsv.completion.CompletionStore`` against the user's
       hermes home (session-scoped).
    No second goal store is invented; any failure or absent row → ``None``,
    and the turn stays an ordinary chat (no hidden forever-continuation).
    """
    explicit = getattr(agent, "_rf08_goal_identity", None)
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()[:200]
    session_id = str(getattr(agent, "session_id", "") or "").strip()
    if not session_id:
        return None
    # (2) engine standing goal row (SessionDB state_meta ``goal:<session_id>``)
    try:
        from hermes_cli import goals as _goals
        goal_state = _goals.load_goal(session_id)
        if goal_state is not None and str(
            getattr(goal_state, "status", "") or ""
        ).strip() == "active":
            return session_id
    except Exception:
        pass
    # (3) godsv completion contract, READ-ONLY, best-effort local import
    try:
        import godsv.completion as _gsv  # type: ignore[import-not-found]
        from agent.file_safety import _hermes_home_path
        store = _gsv.CompletionStore(_hermes_home_path(), session_id)
        contract_state = store.state() or {}
        if str(contract_state.get("state", "") or "").strip() in (
            "WORKING", "COMPLETION_REQUESTED", "VERIFYING", "INCOMPLETE",
        ):
            return session_id
    except Exception:
        pass
    return None


def _rf08_safety_ok(agent) -> bool:
    """Gate B — the segment is NOT aborted/interrupted."""
    for attr in ("_hard_interrupt_requested", "_interrupt_requested", "_interrupt_requested_sticky"):
        if bool(getattr(agent, attr, False)):
            return False
    if getattr(agent, "_interrupt_message", None):
        return False
    reason = str(getattr(agent, "_turn_exit_reason", "") or "")
    lowered = reason.lower()
    for token in ("interrupt", "abort", "stop", "shutdown", "signal"):
        if token in lowered:
            return False
    return True


def _rf08_progress_plausible(summary: dict, prev_summary: Optional[dict]) -> bool:
    """Gate C vs the previous record — deterministic signals ONLY.

    ABSORBED states: no previous record → the first authorized segment always
    passes; open_items count decreased OR at least one NEW answered tool call
    OR the goal text changed. Identical state across segments is NOT progress.
    """
    if not prev_summary:
        return True
    cur_open = len(summary.get("open_items") or [])
    prev_open = len(prev_summary.get("open_items") or [])
    if cur_open < prev_open:
        return True
    if int(summary.get("done") or 0) > int(prev_summary.get("done") or 0):
        return True
    if str(summary.get("goal") or "").strip() != str(prev_summary.get("goal") or "").strip():
        return True
    # Identical goal + identical open + identical done: no plausible progress.
    return False


def _rf08_segment_dir(agent) -> Path:
    """Profile-aware hermes home segment-cursor dir (estop/reports convention)."""
    try:
        from agent.file_safety import _hermes_home_path
        home = _hermes_home_path()
    except Exception:
        home = os.path.expanduser("~/.hermes")
    from pathlib import Path as _Path
    return _Path(home) / RF08_SEGMENT_DIR


def _rf08_cursor_for(agent, segment_index: int) -> Path:
    """ONE JSON cursor file per session: keyed by (session_id, segment_index)."""
    session_id = str(getattr(agent, "session_id", "") or "").strip()
    safe = "".join(c for c in session_id if c.isalnum() or c in "._-")[:80] or "unknown"
    return _rf08_segment_dir(agent) / f"{safe}.segment_{int(segment_index)}.json"


def _rf08_load_cursor(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        import json
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else None
    except Exception:
        return None


def _rf08_write_cursor(path: Path, record: dict) -> None:
    """``os.replace`` WAL-safe write: temp file in the same dir, then replace."""
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{time.time_ns()}")
    try:
        tmp.write_text(
            json.dumps(record, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise


def _rf08_record_segment_continuation(
    agent, api_call_count: int, messages, exit_reason: str = "",
    *, final_response_text: str = "",
) -> Optional[dict]:
    """Evaluate the four gates and, on PASS, persist the segment cursor.

    Called inside ``_resolve_budget_fallback`` before its final return; the
    return value is attached to ``result["rf08_segment_continuation"]`` by
    ``finalize_turn`` so callers can act on it. Writes NEVER raise: a cursor
    failure cannot break the turn that produced the record.
    """
    try:
        # ── Gate A: goal-authorized (read-only lookups only) ──
        segment_identity = _rf08_goal_identity(agent)
        if not segment_identity:
            return None

        # ── Gate B: safety (no aborted/interrupted/stop state) ──
        if not _rf08_safety_ok(agent):
            return None

        summary = _rf08_segment_summary(agent, messages)

        # ── Gate D: auto-segment cap (restart-safe, env-driven) ──
        try:
            from utils import env_int
            max_auto = env_int("HERMES_RF08_MAX_AUTO_SEGMENTS", 2)
        except Exception:
            try:
                max_auto = int(os.environ.get("HERMES_RF08_MAX_AUTO_SEGMENTS", "").strip() or 2)
            except Exception:
                max_auto = 2
        allow_more = str(os.environ.get("HERMES_RF08_ALLOW_MORE_SEGMENTS", "")).strip().lower() in (
            "1", "true", "yes",
        )
        if max_auto <= 0 and not allow_more:
            return None

        # Idempotent per (session_id, segment_index): prefer an existing
        # same-identity cursor at the lowest index, else the lowest free slot.
        existing_index: Optional[int] = None
        free_index: Optional[int] = None
        span = max(max_auto, 1)
        for segment_no in range(1, span + 1):
            cursor = _rf08_load_cursor(_rf08_cursor_for(agent, segment_no)) or {}
            cursor_identity = str(cursor.get("segment_identity") or "")
            if existing_index is None and cursor_identity == segment_identity:
                existing_index = segment_no
            if free_index is None and not cursor_identity and not cursor:
                free_index = segment_no
        if existing_index is not None:
            segment_index = existing_index
            existing_cursor = _rf08_load_cursor(_rf08_cursor_for(agent, segment_index))
        else:
            if free_index is None:
                if not allow_more:
                    # Cap exhausted; without HERMES_RF08_ALLOW_MORE_SEGMENTS=1
                    # no higher index is written (bounded by design).
                    return None
                free_index = span + 1
            segment_index = free_index
            existing_cursor = None

        # ── Gate C: plausible progress vs. the previous record ──
        previous_summary = (
            existing_cursor.get("segment_summary")
            if isinstance(existing_cursor, dict) else None
        )
        if previous_summary is not None and not _rf08_progress_plausible(summary, previous_summary):
            # Identical cursor state re-persisted on the SAME key: report the
            # existing record back to the caller without rewriting anything
            # (idempotent per (session_id, segment_index); a restart reads this
            # same cursor and no duplicate is created).
            return existing_cursor
        if previous_summary is None and existing_cursor is not None:
            # Cursor exists without a usable summary — treat as fresh, authoritative.
            previous_summary = None

        record: dict = {
            "schema": "rf08.segment_cursor",
            "version": RF08_SCHEMA_VERSION,
            "session_id": str(getattr(agent, "session_id", "") or ""),
            "segment_identity": segment_identity,
            "segment_index": int(segment_index),
            "api_call_count": int(api_call_count or 0),
            "max_iterations": int(getattr(agent, "max_iterations", 0) or 0),
            "exit_reason": str(getattr(agent, "_turn_exit_reason", "") or exit_reason or ""),
            "goal_text": summary.get("goal", "")[:2000],
            "done_events": int(summary.get("done") or 0),
            "open_items": summary.get("open_items") or [],
            "segment_summary": summary,
            "auto_continuation_eligible": True,
            "final_response_text": str(final_response_text or "")[-600:],
            "written_at": time.time(),
        }
        _rf08_write_cursor(_rf08_cursor_for(agent, segment_index), record)
        return record
    except Exception:
        # Never let a bookkeeping write fail the turn that produced the state.
        return None

'''

TIME_IMPORT_ANCHOR = "from __future__ import annotations\n\nimport logging\nimport os\n"
TIME_IMPORT_NEW = "from __future__ import annotations\n\nimport logging\nimport os\nimport time\n"

# ── Anchor 2: helper block sits just BEFORE ``_resolve_budget_fallback`` ─────
RESOLVE_DEF_ANCHOR = "def _resolve_budget_fallback(\n"

# (c) record call goes at the tail of the budget-exhausted branch, right
#     before the kanban bookkeeping that closes out the exhaustion path.
RECORD_CALL_ANCHOR = '''            final_response = agent._handle_max_iterations(messages, api_call_count)

    # A kanban worker must record a terminal outcome whether or not a fallback path
'''
RECORD_CALL_NEW = '''            final_response = agent._handle_max_iterations(messages, api_call_count)

        # gv-host-overlay RF-08: persist the segment-continuation cursor at the
        # segment boundary (§3E). Gate-checked; without an authorized goal
        # identity no record is written and the turn stays an ordinary chat.
        # Stashed on the agent so ``finalize_turn`` can surface it in the
        # result dict (this function returns a 3-tuple — no signature change).
        if _rf08_record_segment_continuation is not None:
            _rf08_record = _rf08_record_segment_continuation(
                agent, api_call_count, messages, _turn_exit_reason
            )
            if _rf08_record is not None:
                agent._rf08_segment_continuation = _rf08_record

    # A kanban worker must record a terminal outcome whether or not a fallback path
'''

# (d) finalize_turn: reset the per-turn stash before the resolve step.
LOCALS_ANCHOR = '''    final_response, _turn_exit_reason, preserved_verification_fallback = _resolve_budget_fallback(
'''
LOCALS_NEW = '''    # gv-host-overlay RF-08: fresh per-turn stash; the record (if any) is
    # attached to the result dict below for the callers to act on (§3E).
    agent._rf08_segment_continuation = None
    final_response, _turn_exit_reason, preserved_verification_fallback = _resolve_budget_fallback(
'''

# (e) result assembly attaches the record for the caller.
RESULT_ANCHOR = '''    if agent._tool_guardrail_halt_decision is not None:
        result["guardrail"] = agent._tool_guardrail_halt_decision.to_metadata()
'''
RESULT_NEW = '''    if agent._tool_guardrail_halt_decision is not None:
        result["guardrail"] = agent._tool_guardrail_halt_decision.to_metadata()
    # gv-host-overlay RF-08: surface the persisted segment-continuation cursor
    # so callers (CLI/gateway/scheduler) can act on the pending continuation.
    _rf08_segment_continuation = getattr(agent, "_rf08_segment_continuation", None)
    if _rf08_segment_continuation is not None:
        result["rf08_segment_continuation"] = _rf08_segment_continuation
'''

EDITS = [
    (TIME_IMPORT_ANCHOR, TIME_IMPORT_NEW, 1),
    (RESOLVE_DEF_ANCHOR, HELPERS_BLOCK + "\n" + RESOLVE_DEF_ANCHOR, 1),
    (RECORD_CALL_ANCHOR, RECORD_CALL_NEW, 1),
    (LOCALS_ANCHOR, LOCALS_NEW, 1),
    (RESULT_ANCHOR, RESULT_NEW, 1),
]


def apply_file(target: Path, edits: list, *, check: bool) -> int:
    if not target.is_file():
        print(f"FAILED not found: {target}")
        return 2
    original = target.read_text(encoding="utf-8")
    if MARKER in original:
        print(f"already patched: {target}")
        return 0
    text = original
    for old, new, expected in edits:
        found = text.count(old)
        if found != expected:
            print(
                f"anchor missing/ambiguous in {target}: {found}x (expected {expected}) — "
                "Hermes version changed? Nothing written."
            )
            return 1
        text = text.replace(old, new, expected)
    if check:
        print(f"would patch: {target}")
        return 0
    backup = target.with_suffix(".py.gv-backup")
    backup.write_text(original, encoding="utf-8")
    target.write_text(text, encoding="utf-8")
    print(f"patched: {target} (backup: {backup.name})")
    return 0


def patch(target_root: Path, *, check: bool = False) -> int:
    return apply_file(target_root / TARGET, EDITS, check=check)


def main() -> int:
    args = list(sys.argv[1:])
    check = "--check" in args
    args = [a for a in args if not a.startswith("--")]
    target = Path(args[0]) if args else DEFAULT_SRC
    _refuse_live_engine(target, check=check)
    return patch(target, check=check)


if __name__ == "__main__":
    raise SystemExit(main())
