"""The Voice cockpit payload: one dict the chat layer can serve as `/voice cockpit`.

Pure function over the recorded trajectory (import-only use of the
``voice_service`` read functions) — status, the latest triage and the one
next best action, nothing else. Writes nothing, opens no session.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import voice_service

__all__ = ["AVAILABLE_DECISIONS", "cockpit_payload"]


#: the decision strings ``voice_service._triage_decision`` returns (TA states, not invented).
AVAILABLE_DECISIONS = ("wake_voice", "defer", "stay_silent")


def _status(runtime: Path, session_id: str) -> dict[str, Any]:
    sessions = voice_service.list_sessions(
        voice_service.events_path(runtime)  # type: ignore[arg-type]
    )
    selected = session_id or (
        str(sessions[-1]["session_id"]) if sessions else ""
    )
    row = next((item for item in sessions if item["session_id"] == selected), None)
    return {
        "live": bool(row),
        "session_id": selected,
        "events": int(row["events"]) if row else 0,
        "first_step": int(row["first_step"]) if row else 0,
        "last_step": int(row["last_step"]) if row else 0,
        "reason": "" if row else f"no recorded session in {voice_service.events_path(runtime)}",
    }


def _triage(runtime: Path, session_id: str) -> dict[str, Any]:
    return voice_service.triage(runtime=runtime, session_id=session_id)


def _next_best(triage_report: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    if not status["live"]:
        return {
            "action": "none",
            "why": status["reason"],
        }
    if not triage_report.get("checked"):
        return {
            "action": "none",
            "why": str(triage_report.get("blocked_reason") or "the trajectory could not be checked"),
        }
    decision = str(triage_report.get("decision") or "")
    counts = dict(triage_report.get("counts") or {})
    hottest = max(counts, key=lambda key: int(counts.get(key) or 0), default="")
    if triage_report.get("wake") or decision == "wake_voice":
        return {
            "action": "wake_the_voice",
            "why": str(triage_report.get("reason_code") or decision),
        }
    if decision == "blocked":
        return {
            "action": "none",
            "why": str(triage_report.get("blocked_reason") or "the triage could not run"),
        }
    if hottest and int(counts.get(hottest) or 0) > 0:
        return {
            "action": "watch_category",
            "why": f"no wake: {hottest} findings are accumulating ({counts.get(hottest)})",
        }
    return {
        "action": "none",
        "why": str(triage_report.get("reason") or "nothing to act on"),
    }


def cockpit_payload(
    session_id: str = "",
    *,
    runtime: str | Path | None = None,
) -> dict[str, Any]:
    """One read-only payload: status + latest triage + next best action."""
    root = Path(runtime) if runtime is not None else voice_service.default_runtime()
    status = _status(root, session_id)
    triage_report = _triage(root, status["session_id"]) if status["live"] else {
        "checked": False,
        "blocked_reason": status["reason"],
    }
    return {
        "session_id": status["session_id"],
        "status": status,
        "triage": triage_report,
        "next_best_action": _next_best(triage_report, status),
    }
