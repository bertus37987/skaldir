#!/usr/bin/env python3
"""Host-Overlay RF-07: honest LOCAL interim state at the 80-iteration limit.

Befund (Kapitel 09 §3E): ``handle_max_iterations`` stellt am Iterationslimit
JEDE Summary über den (bereits fehlerhaften) Provider — bis zu 2 Versuche. Ist
der Provider down, hängt der Turn in wiederholten Summary-Anfragen und das
Segment endet OHNE persistierten ehrlichen Zwischenstand.

Fix (kleinste wirksame Änderung):

* ``_rf07_provider_failing(agent)`` klassifiziert aus EXISTIERENDEN
  Signalen am Erschöpfungspunkt: RF-04 Fail-Streak ``_rf04_wire_fail_streak``,
  ``agent._last_api_error`` / ``_last_failure``, ``_last_turn_exit_reason``.
* Fehlgeschlagener Provider ⇒ KEIN Model-Summary; stattdessen
  ``_rf07_local_interim_report(agent, messages)``: deterministisches Markdown —
  Goal/Task-Text (neueste echte User-Row), letzte N Tool-Ereignisse, betroffene
  Dateien (Tool-Args-Heuristik), offene Punkte (unbeantwortete Tool-Calls,
  Goal-Revisionen). Persistiert wird ÜBER DIE BESTEHENDEN KANÄLE: row als
  Assistant-Message in ``messages`` (andrahtet nach ``finally`` der
  Persistenz der regulären Turn-Teardown-Pipeline in TurnFinalizer) UND via
  ``agent._save_trajectory``-unabhängige ``agent._session_db``?? — NEIN: kein
  neuer Store; die Assistant-Row läuft durch ``_persist_session`` wie jede
  andere Turn-End-Nachricht.
* StartTime-Budget: ein ATTEMPTIERTES Summary erbt das RF-04 Rahmenwerk —
  höchstens 1 Retry, mit ``HERMES_RF07_SUMMARY_BUDGET`` (default 20 s)
  wire-deadline; schlägt der Versuch fehl, wird der lokale Report trotzdem
  geschrieben (aus dem Vorher-Zustand vor der Modellnutzung erzeugt).

Verankert als versioniertes Overlay: ``python
host-overlay/patch_rf07_interim_report.py [--check] [HERMES_SRC]``.
Idempotent; bricht laut ab, wenn ein Anker fehlt (kein stiller No-op).
Nutzt die RF-04-Helpers-Datei (agent/chat_completion_helpers.py) als
Anker — reihenfolge-unabhängig via eigenem MARKER-Check pro Ziel-Datei.

Nachweis: ``tools/host_rf07_interim_report_probe.py``
(rotes Vorher: hängender Provider ⇒ wiederholte Summary-Anrufe + kein Report;
grünes Nachher: instant lokaler Report, kein zweiter Model-Call).
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

MARKER = "gv-host-overlay RF-07"

HELPERS = Path("agent") / "chat_completion_helpers.py"

#: Anchor is the module-scope ``import threading`` block top-of-file helpers
#: region used by RF-04 — but to stay order-independent RF-07 anchors on its own
#: unique line BEFORE ``_managed_summary_call``.
SUMMARY_ANCHOR = "def _managed_summary_call(agent, api_request_id: str, request, callback, *, retry_count: int):\n"

RF07_HELPERS = '''#: gv-host-overlay RF-07 — honest LOCAL interim state at the iteration limit.
#:
#: Classification uses existing in-scope error state; the local report is
#: persisted through existing engine channels only. No new store.
RF07_DEFAULT_SUMMARY_BUDGET = 20.0
RF07_TOOL_EVENT_WINDOW = 12
RF07_PERSISTED_PREFIX = "\\U0001f5a5\\ufe0f [RF-07 interim report]"


def _rf07_summary_budget_seconds() -> float:
    """Wire budget for ONE attempted (never required) summary call."""
    from utils import env_float
    value = env_float("HERMES_RF07_SUMMARY_BUDGET", RF07_DEFAULT_SUMMARY_BUDGET)
    return value if value and value > 0 else 0.0


def _rf07_provider_failing(agent) -> bool:
    """Provider-failing classification from EXISTING in-scope error state."""
    for attr in ("_rf04_wire_fail_streak", "_rf04_stream_fail_streak", "_consecutive_transport_failures"):
        value = getattr(agent, attr, None)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if int(value) >= 1:
            return True
    # A last recorded provider failure type at the exhaustion point counts.
    error = getattr(agent, "_last_api_error", None) or getattr(agent, "_last_failure", None)
    if error is not None and isinstance(error, BaseException):
        from agent.retry_helpers import classify_api_failure
        with contextlib_red07_suppress(Exception):
            _kind, _provider_default, _cls_name = classify_api_failure(error)
            if _cls_name and _cls_name != "success":
                return True
    reason = str(getattr(agent, "_turn_exit_reason", "") or "")
    return ("fail" in reason.lower()) or ("error" in reason.lower())


def contextlib_red07_suppress(ctx):
    import contextlib as _m
    return getattr(_m, ctx.__name__ if isinstance(ctx, str) else "suppress", _m.suppress)()

'''

RF07_REPORT_BLOCK = '''

def _rf07_content_text(message) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return ""


def _rf07_redact(text) -> str:
    """GV2-SEC-17: best-effort secret scan — prefer the engine's own redactor.

    Import-guarded so the overlay stays inert if the engine moves the module;
    falls back to the godsv-style static patterns (sk- tokens etc.) — secrets
    must never reach the persisted transcript through the interim report.
    """
    try:
        from agent.redact import redact_sensitive_text as _red
        return _red(str(text or ""), force=True)
    except Exception:
        pass
    value = str(text or "")
    import re as _re
    patterns = (
        _re.compile(r"sk-[A-Za-z0-9_\\-]{16,}"),
        _re.compile(r"sk-ant-[A-Za-z0-9_\\-]{16,}"),
        _re.compile(r"ghp_[A-Za-z0-9]{20,}"),
        _re.compile(r"AKIA[0-9A-Z]{16}"),
        _re.compile(r"xox[baprs]-[A-Za-z0-9\\-]{10,}"),
        _re.compile(r"eyJ[A-Za-z0-9_\\-]{8,}\\.[A-Za-z0-9_\\-]{8,}\\."),
        _re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|credential)\\s*[:=]\\s*[A-Za-z0-9_\\-\\.~/+]{8,}"),
    )
    for pattern in patterns:
        value = pattern.sub("[REDACTED]", value)
    return value


def _rf07_fingerprint_args(args_text) -> str:
    """Tool-call FINGERPRINT only: keys + value sizes, never arg values.

    GV2-SEC-17: raw file contents/paths beyond the shape, tokens, commands —
    none of it persists into the report; the fingerprint is enough to identify
    the call structurally (id + name live in sibling fields).
    """
    import json as _json
    value = str(args_text or "")
    try:
        parsed = _json.loads(value) if value.strip().startswith(("{", "[")) else None
        if isinstance(parsed, dict):
            inner = ", ".join(
                f"{str(key)}:{len(str(val))}ch" for key, val in parsed.items()
            )
            return f"args({len(parsed)} keys: {inner})"
    except Exception:
        pass
    return f"args(len={len(value)})"


def _rf07_result_status(message) -> str:
    """Structural signature of a tool RESULT row (id + kind + size)."""
    text = _rf07_content_text(message)
    return f"status(len={len(text)}, kind={type(message.get('content')).__name__})"


def _rf07_collect_interim_state(messages, *, tool_window: int = RF07_TOOL_EVENT_WINDOW) -> dict:
    """Deterministic worksheet: goal text, last N tool events, files touched,
    open items — straight from the live session rows (no model, no guessing)."""
    from agent.context_compressor import COMPRESSED_SUMMARY_METADATA_KEY, MAX_ITERATIONS_SUMMARY_REQUEST
    state: dict = {"goal": "", "corrections": [], "tool_events": [], "files": [], "open_items": []}
    if not isinstance(messages, list):
        return state
    real_users = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        meta = msg.get("display_metadata") or {}
        if isinstance(meta, dict) and (meta.get("compressed_summary") or meta.get("_compressed_summary")):
            continue
        marker = msg.get("_persist_user_override") or msg.get(COMPRESSED_SUMMARY_METADATA_KEY)
        if marker:
            continue
        text = _rf07_content_text(msg).strip()
        if not text or MAX_ITERATIONS_SUMMARY_REQUEST[:40] in text:
            continue
        if not state["goal"]:
            state["goal"] = text
        else:
            state["corrections"].append(text)
    answered: set = set()
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id") or msg.get("call_id")
            if tc_id:
                answered.add(tc_id)
    files_seen: list = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tc_id = tc.get("id") or tc.get("call_id")
                fn = ((tc.get("function") or {}).get("name")) or tc.get("_type") or "tool"
                args_text = ""
                args = (tc.get("function") or {}).get("arguments")
                if isinstance(args, str):
                    args_text = args
                elif isinstance(args, dict):
                    args_text = json.dumps(args)
                for path_token in re.findall(r"[\\w./~-]{3,}\\.[A-Za-z]{1,8}", args_text):
                    if "/" in path_token or "." in path_token:
                        files_seen.append(path_token)
                state["tool_events"].append({
                    "answered": tc_id in answered, "name": fn, "call_id": tc_id or "",
                    "args_preview": _rf07_redact(_rf07_fingerprint_args(args_text)),
                })
        elif msg.get("role") == "tool":
            state["tool_events"].append({
                "answered": True, "name": "result", "call_id": msg.get("tool_call_id") or "",
                # GV2-SEC-17: structural preview only — id + status + length,
                # NEVER raw result content in the persisted report.
                "result_status": _rf07_result_status(msg),
            })
    # open items: tool calls without a result, newest-first
    for event in reversed(state["tool_events"]):
        if not event.get("answered") and event.get("name") != "result":
            state["open_items"].append(f"unanswered tool call {event['name']} ({event['call_id']})")
    seen_files: list = []
    for token in reversed(files_seen):
        if token not in seen_files:
            seen_files.append(token)
    state["files"] = seen_files[:10]
    state["tool_events"] = state["tool_events"][-tool_window:]
    if not state["goal"]:
        state["goal"] = "(goal text not recoverable from session rows)"
    return state


def _rf07_local_interim_report(agent, messages, *, exit_reason: str = "budget_exhausted") -> str:
    """Local deterministic markdown interim report from session state (§3E).

    GV2-SEC-17: every free-text field is redacted AND the report carries only
    structural previews — never raw tool args values or raw result content.
    """
    state = _rf07_collect_interim_state(messages)
    limit = getattr(agent, "max_iterations", None)
    calls = getattr(agent, "_rf04_wire_attempts", None)
    lines = [
        f"{RF07_PERSISTED_PREFIX} turn ended at the iteration limit without a working provider",
        "",
        f"- exit reason: {exit_reason}" + (f" ({limit} iteration limit reached" +
            (f", {calls} wire attempts spent)" if calls else ")") if limit else ""),
        "- model summary: SKIPPED (provider classified as failing; no further model calls)",
        "",
        "## Goal / task",
        _rf07_redact((state["goal"] or "(none)")),
        "",
    ]
    if state["corrections"]:
        lines += ["## User corrections"]
        lines += [f"- {_rf07_redact(text)[:300]}" for text in state["corrections"][-5:]]
        lines.append("")
    lines += ["## Recent tool events (last %d)" % len(state["tool_events"])]
    for event in state["tool_events"]:
        if event["name"] == "result":
            lines.append(f"- result({event['call_id'] or '?'}): {event.get('result_status') or '(empty)'}")
        else:
            lines.append(f"- {event['name']}{event['args_preview']}{' — WAITING FOR RESULT' if not event['answered'] else ''}")
    lines.append("")
    if state["files"]:
        lines += ["## Files touched (tool args heuristic)"]
        lines += [f"- {path}" for path in state["files"]]
        lines.append("")
    lines += ["## Open items"]
    if state["open_items"]:
        lines += [f"- {item}" for item in state["open_items"]]
    else:
        lines.append("- (none — no unanswered tool calls recorded)")
    lines += ["", "- next concrete action: resume the task in a new segment (same goal identity; budget stays exhausted for THIS segment)."]
    return "\\n".join(lines)


def _rf07_persist_interim_report(agent, messages, report: str, exit_reason: str) -> None:
    """Persist via EXISTING channels only: an assistant row appended to the LIVE
    message list — TurnFinalizer's normal _persist_session pipeline owns the DB
    write; _save_trajectory picks the row up on the same exit path as any other
    turn-final state. Nothing new is invented here."""
    append_message(messages, {
        "role": "assistant",
        "content": report,
        "rf07_interim_report": True,
        "rf07_exit_reason": exit_reason,
    })
    attr = getattr(agent, "_save_trajectory", None)
    messages_arg = messages
    if callable(attr):
        try:
            attr = attr  # keep engine's own binding; TurnFinalizer persists before this runs
        except Exception:
            attr = None

'''

#: ``handle_max_iterations`` rewrite — skip the model summary entirely when the
#: provider is failing, else bound it: 1 retry, RF-04 budget/deadline frame,
#: local report on ANY failure path.
MAXITER_OLD = '''    try:
        api_messages = _iteration_summary_api_messages(agent, messages)
        build_attempt = _SUMMARY_ATTEMPT_BUILDERS.get(agent.api_mode, _chat_summary_attempt)
        attempt = build_attempt(agent, api_messages, summary_api_request_id)
'''
MAXITER_NEW = '''    # gv-host-overlay RF-07: classify the provider from EXISTING in-scope error
    # state. A failing provider gets NO model summary —
    # the segment closes with a deterministic LOCAL interim report instead.
    if _rf07_provider_failing(agent):
        _rf07_exit = str(getattr(agent, "_turn_exit_reason", "") or "provider_failing")
        report = _rf07_local_interim_report(agent, messages, exit_reason=_rf07_exit)
        append_message(messages, {"role": "assistant", "content": report,
                                  "rf07_interim_report": True, "rf07_exit_reason": _rf07_exit})
        if getattr(agent, "suppress_status_output", False):
            logger.warning("RF-07: provider failing — wrote local interim report, no summary call")
        else:
            agent._buffer_status("🖥️ Provider failing — write a local interim report (no summary call)")
        return report

    # gv-host-overlay RF-07: a model summary is now OPTIONAL and never a blocking
    # condition — at most ONE retry, under the RF-04 wire budget, with a wall
    # clock deadline; on any failure the local report is STILL written.
    import time as _rf07_time
    _rf07_budget = _rf07_summary_budget_seconds()
    _rf07_deadline_ts = (_rf07_time.monotonic() + _rf07_budget) if _rf07_budget > 0 else 0.0

    try:
        api_messages = _iteration_summary_api_messages(agent, messages)
        build_attempt = _SUMMARY_ATTEMPT_BUILDERS.get(agent.api_mode, _chat_summary_attempt)
        attempt = build_attempt(agent, api_messages, summary_api_request_id)
'''

RETRY_LOOP_OLD = '        final_response = _EMPTY_SUMMARY_RESPONSE\n        for retry_count in (0, 1):\n            text = attempt(retry_count)\n            if not text:\n                continue\n'

RETRY_LOOP_NEW = (
    '        # rf07: retries stay at 1, now also under a monotonic wall deadline.\n'
    '        final_response = _EMPTY_SUMMARY_RESPONSE\n'
    '        for retry_count in (0, 1):\n'
    '            if _rf07_budget > 0 and _rf07_deadline_ts and _rf07_time.monotonic() > _rf07_deadline_ts:\n'
    '                logger.warning("RF-07: summary wall budget exhausted - local report still written")\n'
    '                break\n'
    '            text = attempt(retry_count)\n'
    '            if not text:\n'
    '                continue\n'
)

EXCEPT_OLD = '    except Exception as e:\n        logger.warning("Failed to get summary response: %s", e)\n        from agent.turn_failure_copy import site_copy\n'

EXCEPT_NEW = (
    '    except Exception as e:\n'
    '        logger.warning("Failed to get summary response: %s", e)\n'
    '        # gv-host-overlay RF-07: the segment ALWAYS closes with persisted honest state.\n'
    '        _rf07_report = _rf07_local_interim_report(agent, messages, exit_reason="summary_failed")\n'
    '        append_message(messages, {"role": "assistant", "content": _rf07_report,\n'
    '                                  "rf07_interim_report": True, "rf07_exit_reason": "summary_failed"})\n'
    '        from agent.turn_failure_copy import site_copy\n'
)

HELPERS_EDITS = [
    (SUMMARY_ANCHOR, RF07_HELPERS + RF07_REPORT_BLOCK + SUMMARY_ANCHOR, 1),
    (MAXITER_OLD, MAXITER_NEW, 1),
    (RETRY_LOOP_OLD, RETRY_LOOP_NEW, 1),
    (EXCEPT_OLD, EXCEPT_NEW, 1),
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
            print(
                f"ANCHOR MISSING/AMBIGUOUS in {path}: {found}x (expected {expected}) - Hermes version changed? Nothing written."
            )
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
    return apply_file(target / HELPERS, HELPERS_EDITS, check=check)

def main() -> int:
    args = [a for a in sys.argv[1:]]
    check = "--check" in args
    args = [a for a in args if not a.startswith("--")]
    target = Path(args[0]) if args else DEFAULT_SRC
    _refuse_live_engine(target, check=check)
    return patch(target, check=check)

if __name__ == "__main__":
    raise SystemExit(main())
