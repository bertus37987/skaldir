"""Recurrence-Kill-Rate + episode metrics (docs/CORE-PLAN.md §6).

The killer metric of God's Voice: a solved problem class should not recur in
later sessions. Definitions (corpus-level, deterministic):

- A *problem class* is an error fingerprint (see ``monitor.error_fingerprint``)
  that appeared in at least one session.
- *Recurring* classes appear in ≥ 2 distinct sessions.
- ``recurrence_kill_rate`` = 1 − recurring / problem_classes
  (1.0 = every class was a one-off; higher is better).

Plus operational counters (release scope): interventions (pause/redirect/
guard) with deterministic outcome estimates, research duration, recovery
time and token estimates — token numbers are **estimates** (chars/4), never
accounting; ``interventions_held``/``recurred`` approximate "did the same
problem reappear within a short window after the intervention".
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .events import EventStore
from .incidents import IncidentStore
from .monitor import classify_tool_error, error_fingerprint, error_text
from .skills import STATUS_PROMOTED, SkillStore

RECURRENCE_WINDOW = 12  # steps after a delivery in which a comeback counts


@dataclass
class Metrics:
    sessions: int
    events: int
    problem_classes: int
    recurring_classes: int
    recurrence_kill_rate: float
    hints_delivered: int
    incidents: int
    skills_promoted: int
    # release-scope counters (all deterministic, no LLM)
    pauses_requested: int = 0
    pauses_delivered: int = 0
    pauses_expired: int = 0
    pauses_stale: int = 0
    research_runs: int = 0
    research_ms_avg: int = 0
    research_tokens_est: int = 0
    # host-measured values (session DB), NOT estimates — None when unavailable
    main_agent_tokens_measured: dict | None = None
    # §34 voice quality from the durable pause history (None = not queried)
    voice_quality: dict | None = None
    # §26 testing-tool proof stats (None = not queried)
    testing_tools: dict | None = None
    recovery_ms_avg: int = 0
    guard_checks: int = 0
    guard_nudges: int = 0
    guard_footers: int = 0
    claims_recorded: int = 0
    contradictions: int = 0
    skills_consumed: int = 0
    tool_error_classes: dict[str, int] = field(default_factory=dict)
    interventions_held: int = 0
    interventions_recurred: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def measured_session_usage(
    session_db: str | Path, session_id: str
) -> dict[str, int | float] | None:
    """Real, host-measured token usage for one session (state.db).

    Returns input/output/cache/reasoning tokens plus estimated/actual USD
    cost as recorded by the Hermes host — or ``None`` when the DB, table
    or session row is absent (never guessed).
    """
    import sqlite3

    path = Path(session_db)
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT input_tokens, output_tokens, cache_read_tokens,"
                " cache_write_tokens, reasoning_tokens, estimated_cost_usd,"
                " actual_cost_usd FROM sessions WHERE id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    keys = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "estimated_cost_usd",
        "actual_cost_usd",
    )
    return {key: value for key, value in zip(keys, row, strict=False) if value is not None}


def testing_tool_stats(runtime: str | Path) -> dict:
    """Re-exported from capability so callers import one metrics surface."""
    from skaldir.capability import testing_tool_stats as _stats

    return _stats(runtime)


def voice_quality_from_db(pause_db: str | Path) -> dict:
    """Read the pause history (existing pauses.db) and aggregate §34 quality."""
    import sqlite3

    conn = sqlite3.connect(f"file:{Path(pause_db)}?mode=ro", uri=True)
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(pauses)").fetchall()]
        rows = conn.execute("SELECT * FROM pauses ORDER BY created DESC LIMIT 500").fetchall()
    finally:
        conn.close()
    pauses = [dict(zip(cols, row, strict=False)) for row in rows]
    return voice_quality(pauses)


def voice_quality(pauses: list[dict]) -> dict:
    """§34 — intervention quality derived from the durable pause history.

    More interventions are not better: this separates delivered redirects
    that stuck (recovery_success) from those that recurred, and counts the
    pauses the investigator closed without a redirect (no_action — the
    self-restraint path for the old false-pause class).
    """
    totals = {
        "interventions": len(pauses),
        "delivered": 0,
        "no_action": 0,
        "abandoned": 0,
        "expired": 0,
        "stale": 0,
        "recovery_success": 0,
        "recurrence_after_intervention": 0,
        "steps_before_first_intervention": {},
    }
    for pause in pauses:
        status = str(pause.get("status") or "")
        if status == "delivered":
            totals["delivered"] += 1
        elif status == "abandoned":
            reason = str(pause.get("released_reason") or "")
            if "no intervention needed" in reason:
                totals["no_action"] += 1
            else:
                totals["abandoned"] += 1
        elif status == "expired":
            totals["expired"] += 1
        elif status == "stale":
            totals["stale"] += 1
        session = str(pause.get("session_id") or "")
        base = int(pause.get("base_step") or 0)
        if session and (
            session not in totals["steps_before_first_intervention"]
            or base < totals["steps_before_first_intervention"][session]
        ):
            totals["steps_before_first_intervention"][session] = base
    # recurrence: same trigger fingerprint in the same session at a later step
    by_session: dict[str, list[dict]] = {}
    for pause in pauses:
        by_session.setdefault(str(pause.get("session_id") or ""), []).append(pause)
    for rows in by_session.values():
        for pause in rows:
            if str(pause.get("status") or "") != "delivered":
                continue
            fp = str(pause.get("trigger_fp") or "")
            base = int(pause.get("base_step") or 0)
            recurred = any(
                str(other.get("trigger_fp") or "") == fp
                and int(other.get("base_step") or 0) > base
                for other in rows
            )
            if recurred:
                totals["recurrence_after_intervention"] += 1
            else:
                totals["recovery_success"] += 1
    delivered = totals["delivered"]
    totals["recovery_success_rate"] = (
        round(totals["recovery_success"] / delivered, 3) if delivered else None
    )
    totals["no_action_rate"] = (
        round(totals["no_action"] / totals["interventions"], 3) if totals["interventions"] else None
    )
    return totals


def compute_metrics(
    events: EventStore,
    incidents: IncidentStore | None = None,
    skills: SkillStore | None = None,
    session_db: str | Path | None = None,
    session_id: str | None = None,
    pause_db: str | Path | None = None,
    testing_runtime: str | Path | None = None,
) -> Metrics:
    session_ids = events.session_ids()
    fingerprints: dict[str, set[str]] = {}
    delivered = 0
    event_total = 0
    counters: dict[str, int] = {}
    error_classes: dict[str, int] = {}
    research_ms: list[int] = []
    research_tokens = 0
    recovery_ms: list[int] = []
    deliveries: list[tuple[str, int, str]] = []  # (session, step, fingerprint)
    for session_id in session_ids:
        for event in events.iter_session(session_id):
            event_total += 1
            kind = event.kind
            if kind == "hint_delivered":
                delivered += 1
            if kind in (
                "pause_requested",
                "pause_delivered",
                "pause_expired",
                "pause_stale",
                "guard_check",
                "guard_nudge",
                "guard_footer",
                "claim",
                "contradiction",
                "skill_consumed",
            ):
                counters[kind] = counters.get(kind, 0) + 1
            if kind == "pause_delivered":
                payload = event.payload or {}
                fingerprint = str(payload.get("fingerprint") or "")
                deliveries.append((session_id, event.step, fingerprint))
                if isinstance(payload.get("recovery_ms"), int):
                    recovery_ms.append(int(payload["recovery_ms"]))
            if kind == "research_done":
                payload = event.payload or {}
                if isinstance(payload.get("duration_ms"), int):
                    research_ms.append(int(payload["duration_ms"]))
                if isinstance(payload.get("tokens_est"), int):
                    research_tokens += int(payload["tokens_est"])
            if kind == "tool_result":
                text = error_text(event.payload)
                if text:
                    fingerprints.setdefault(error_fingerprint(text), set()).add(session_id)
                    error_class = classify_tool_error(text)
                    if error_class is not None:
                        error_classes[error_class] = error_classes.get(error_class, 0) + 1
    classes = len(fingerprints)
    recurring = sum(1 for sessions in fingerprints.values() if len(sessions) >= 2)
    kill_rate = 1.0 if classes == 0 else 1.0 - recurring / classes
    promoted = 0
    if skills is not None:
        promoted = sum(1 for skill in skills.all() if skill.status == STATUS_PROMOTED)

    # intervention outcome estimate: same fingerprint back within the window?
    held = 0
    recurred = 0
    for session_id, step, fingerprint in deliveries:
        if not fingerprint:
            held += 1
            continue
        window_end = step + RECURRENCE_WINDOW
        comeback = False
        for event in events.iter_session(session_id):
            if event.step <= step or event.step > window_end:
                continue
            if event.kind != "tool_result":
                continue
            text = error_text(event.payload)
            if text and error_fingerprint(text) == fingerprint:
                comeback = True
                break
        if comeback:
            recurred += 1
        else:
            held += 1

    return Metrics(
        sessions=len(session_ids),
        events=event_total,
        problem_classes=classes,
        recurring_classes=recurring,
        recurrence_kill_rate=round(kill_rate, 4),
        hints_delivered=delivered,
        incidents=incidents.count() if incidents is not None else 0,
        skills_promoted=promoted,
        pauses_requested=counters.get("pause_requested", 0),
        pauses_delivered=counters.get("pause_delivered", 0),
        pauses_expired=counters.get("pause_expired", 0),
        pauses_stale=counters.get("pause_stale", 0),
        research_runs=len(research_ms),
        research_ms_avg=int(sum(research_ms) / len(research_ms)) if research_ms else 0,
        research_tokens_est=research_tokens,
        main_agent_tokens_measured=(
            measured_session_usage(session_db, session_id)
            if session_db and session_id
            else None
        ),
        voice_quality=(
            voice_quality_from_db(pause_db) if pause_db and Path(pause_db).is_file() else None
        ),
        testing_tools=(
            testing_tool_stats(testing_runtime) if testing_runtime is not None else None
        ),
        recovery_ms_avg=int(sum(recovery_ms) / len(recovery_ms)) if recovery_ms else 0,
        guard_checks=counters.get("guard_check", 0),
        guard_nudges=counters.get("guard_nudge", 0),
        guard_footers=counters.get("guard_footer", 0),
        claims_recorded=counters.get("claim", 0),
        contradictions=counters.get("contradiction", 0),
        skills_consumed=counters.get("skill_consumed", 0),
        tool_error_classes=dict(sorted(error_classes.items())),
        interventions_held=held,
        interventions_recurred=recurred,
    )
