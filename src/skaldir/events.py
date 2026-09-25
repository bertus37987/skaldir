"""Append-only event store (SQLite WAL) + secret-leak gate.

V0 scope: every agent step is an event (thought, tool_call, tool_result,
file_edit, test_run, user_signal). Monitor and voice consume these events.
Security: payloads are scanned for secrets before they are persisted — a
secret that reached the trajectory is a leak (see docs/SECURITY.md).

Thread safety: Hermes invokes plugin hooks on bounded worker threads, so the
store is shared across threads. The connection is opened with
``check_same_thread=False`` and **all** access is serialized with an RLock
(single writer, many readers).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .secretscan import Finding, redact, scan

KINDS = frozenset(
    {
        "thought",
        "goal_set_from_chat",  # @goal in the chat begins the per-session contract
        "tool_call",
        "tool_result",
        "file_edit",
        "test_run",
        "user_signal",
        "budget_tick",
        "hint_delivered",
        "hint_triage",  # v1: LLM triage verdict (enrichment thread)
        "hint_suppressed",  # v1: triage suppressed a hint
        "evidence_judged",  # v1: same_problem_judge outcome
        "cu_attempt",  # v2: computer-use attempt begin
        "cu_action",  # v2: computer-use action executed
        "cu_verified",  # v2: attempt verified
        "cu_failed",  # v2: attempt verification failed
        "cu_aborted",  # v2: step aborted (stop conditions)
        "cu_blocked",  # v2: policy blocked an attempt
        "treasury_use",  # v2.5: broker injected a handle
        "treasury_denied",  # v2.5: broker refused (scope/grant/rate/expiry)
        "treasury_grant_requested",  # v2.5: agent asked for a grant
        "treasury_grant_approved",  # v2.5: owner granted
        "treasury_grant_denied",  # v2.5: owner denied
        "spend_requested",  # v3: spend awaits human approval
        "spend_approved",  # v3: spend approved (human or policy)
        "spend_denied",  # v3: spend denied (cap/intent/whitelist)
        "spend_completed",  # v3: spend executed and closed
        "claim",  # release: success/completion claim observed in agent text
        "guard_check",  # release: completion check ran (verdict recorded)
        "guard_nudge",  # release: pre_verify continue issued
        "guard_footer",  # release: caveat appended to final answer
        "contradiction",  # release: claim contradicted by later error
        "pause_requested",  # release: voice pause requested (lease opened)
        "pause_delivered",  # release: redirect delivered (tool blocked once)
        "pause_released",  # release: pause released (normal path)
        "pause_expired",  # release: lease expired (ttl/watchdog)
        "pause_stale",  # release: late research result discarded
        "research_started",  # release: research run began
        "research_done",  # release: research finished (redirect ready)
        "research_failed",  # release: research failed (template fallback)
        "skill_consumed",  # release: promoted skill surfaced into context
        "tool_error_class",  # release: classified tool error (auth/rate/…)
        # v1.3.1: voice context budget/accounting (MEASURED chars, ESTIMATED tokens)
        "voice_context_built",
        # v1.3.1: the agent reported a missing capability (tool build follows)
        "capability_requested",
        # tool builder stages (P1/B1: STAGES in toolbuilder.py — sync is
        # test-enforced) + failure
        "tool_build_requested",
        "tool_build_researching",
        "tool_build_generating",
        "tool_build_manifest",
        "tool_build_dependency_inspection",
        "tool_build_gates",
        "tool_build_register",
        "tool_build_ready",
        "tool_build_failed",
        # §4d (B7): a capability was PUBLISHED — registry commit + outbox entry.
        # B9 (discovery) reads this kind plus the registry revision
        # (ToolStore.registry_revision) to invalidate its index cache; the
        # append here is best-effort (the outbox is the durable fact).
        "tool_published",
        # strict completion supervisor (Goal): verdicts, rejections, terminal markers
        "completion_checker_failed",
        "completion_verdict",
        "completion_rejected",
        "completion_blocked",
        "completion_cap_reached",
        "pause_no_action",  # v1: no-intervention outcome — lease abandoned, agent keeps working
        "voice_intervention_assessment",  # cost-gate verdict (pause.py): measured vs estimated
        "treasury_selftest",  # v2.5: owner credential self-test (decrypt + isolated injection)
        # Block B2/§1.6: a tool's health state changed (written by
        # capability_health) — selection must react to a degradation
        "capability_health_changed",
        # Block 4: untrusted-content boundary. A signal is not a verdict — only
        # a confirmed, addressed attempt becomes untrusted_instruction_attempt.
        "prompt_injection_signal",
        "untrusted_instruction_attempt",
        # Block 4: wrong-tool-call interception before execution (pre_tool_call)
        "tool_call_intercepted",
        # Block 4: tool/command sequence patterns (Block 4 §3). One kind per
        # pattern so the pause policy can stay precise (a loop may escalate, an
        # advisory pattern must not).
        "pattern_tool_loop",
        "pattern_command_loop",
        "pattern_oscillation",
        "pattern_dependency_churn",
        "pattern_no_new_evidence",
        "pattern_retry_without_strategy_change",
        "pattern_test_without_change",
        "pattern_change_without_verification",
        "pattern_search_without_progress",
        # TOKEN-ECONOMY (§94): a tool result was archived — the full payload lives in
        # the content-addressed store and the hot context carried id + digest + summary.
        # The saved-token columns ride on this payload (telemetry reads them).
        "tool_archived",
        # PROGRESS-GUARD (§94): the retry gate decided (retry / refuse / re-plan), and
        # the progress guard turned a stall into an action (a directive, or a named stop).
        "retry_gate",
        "progress_decision",
        "progress_directive_delivered",
        "run_sweep",  # run supervisor: sweep found violations or a claim
        # System One / Jev (ABNAHME Teil VIII/§49): advisory evaluation records.
        # The evidence fields travel on the payload; the kinds stay granular so
        # a stale or blocked evaluation is visible without reading the record.
        "system_one_evaluation",  # one provider evaluation completed (evidence payload)
        "system_one_shadow",  # shadow recording: observed, compared, never mutated
        "system_one_error",  # provider/deadline error (classified, policy kept)
        "system_one_blocked",  # structured blocked_external_access (no credential)
        "system_one_stale",  # late answer refused as inert (§92)
        "system_one_decision",  # a policy rule consumed an evaluation (route + rule)
        "system_one_fallback",  # the adapter fell through a fallback rung (named, not hidden)
        # MVP/87: one Jev façade decision, subscribed to the store by skaldir.jev.wiring.
        # Deliberately *not* in CHAT_KINDS — Jev stays inconspicuous in the feed (§10) —
        # and deliberately not part of SYSTEM_ONE_EVENT_KINDS: the payload is the decision
        # document the CLI prints and the ledger stores (shaped by
        # skaldir.jev.wiring.decision_payload), not a §24 lifecycle stage.
        "jev_decision",
        # §24: the five lifecycle kinds of the SystemOneService facade. Dotted
        # names on purpose — they are the §24 spelling, and they read as
        # lifecycle stages of one request rather than as six parallel records.
        "system_one.requested",  # a System-One evaluation was started
        "system_one.completed",  # it came back inside its deadline
        "system_one.failed",  # provider error / contract violation (policy kept)
        "system_one.discarded_stale",  # the answer was refused as inert (§92)
        "system_one.escalated",  # a request for System Two / the user (never an action)
        "system",
    }
)

# Kinds that belong in the live chat feed (§16: what is happening to my task
# right now — interventions, verification, capability work, security gates).
# Everything else stays in the panel/event store; the chat stays quiet.
CHAT_KINDS = frozenset(
    {
        # voice interventions
        "pause_requested",
        "pause_delivered",
        "pause_released",
        "pause_expired",
        "research_started",
        "research_done",
        "research_failed",
        # goal contract (set from the chat via @goal)
        "goal_set_from_chat",
        # completion / gate
        "completion_verdict",
        "completion_rejected",
        "completion_blocked",
        "completion_cap_reached",
        "guard_nudge",
        "contradiction",
        # capability + tools
        "capability_requested",
        # capability health: a tool that degrades/quarantines affects the
        # user's task surface — that belongs in the feed (§1.6)
        "capability_health_changed",
        # tool builder stages (P1/B1: build progress belongs in the chat)
        "tool_build_requested",
        "tool_build_researching",
        "tool_build_generating",
        "tool_build_manifest",
        "tool_build_dependency_inspection",
        "tool_build_gates",
        "tool_build_register",
        "tool_build_ready",
        "tool_build_failed",
        # security / money boundaries
        "treasury_denied",
        "treasury_grant_requested",
        "spend_requested",
        "spend_denied",
        # Block 4: untrusted content / injection boundary (signal AND confirmed)
        "prompt_injection_signal",
        "untrusted_instruction_attempt",
        # Block 4: a tool call was stopped before execution (ALLOW is not chat-worthy,
        # every non-ALLOW classification is)
        "tool_call_intercepted",
        # Block 4: the pattern signals that may escalate (the advisory ones stay
        # in the panel — the chat stays quiet, §16)
        "pattern_no_new_evidence",
        "pattern_retry_without_strategy_change",
        "pattern_dependency_churn",
        "pattern_oscillation",
        # computer use failures
        "cu_blocked",
        "cu_failed",
        # computer use SUCCESS — only ever after the resulting state was verified
        # (§9: success must never be shown before verification; without this kind
        # the chat can only see the failures of a feature that does work)
        "cu_verified",
        # voice: a late research result was discarded because the pause was
        # already resolved — a silently ineffective intervention must be visible
        # instead of looking like "nothing happened" (§16)
        "pause_stale",
        # money: the OUTCOME of a decision the user gave (§12 — after the approval
        # the flow continues and shows what the approval actually did)
        "spend_approved",
        "spend_completed",
        # System One / Jev: only the RESULTING action becomes visible, never the
        # individual questions or scores (§10 — Jev stays inconspicuous)
        "system_one_decision",
        "system_one_blocked",
    }
)

#: §24 event kinds — every kind the System-One lifecycle may emit.
SYSTEM_ONE_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "system_one_evaluation",
        "system_one_shadow",
        "system_one_error",
        "system_one_blocked",
        "system_one_stale",
        "system_one_decision",
        "system_one.requested",
        "system_one.completed",
        "system_one.failed",
        "system_one.discarded_stale",
        "system_one.escalated",
    }
)

#: §24: no secrets and no complete private state in a normal event payload.
#: Only these keys survive :func:`system_one_event_payload` — everything else
#: (chat history, raw state, credentials) is dropped instead of persisted.
SYSTEM_ONE_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "evaluation_id",
        "run_id",
        "goal_id",
        "goal_revision",
        "policy_revision",
        "event_cursor",
        "caller",
        "question_pack_id",
        "question_pack_version",
        "state_fingerprint",
        "workspace_fingerprint",
        "input_scope",
        "provider",
        "provider_model",
        "provider_mode",
        "provider_access_mode",
        "live_jev",
        "status",
        "mode",
        "route",
        "rule",
        "policy_rule",
        "request_only",
        "latency_ms",
        "usage",
        "attempts",
        "sdk_retries",
        "raw_ref",
        "cache",
        "reason",
        "state",
        "error",
        "blocker",
        "missing",
        "criterion_id",
        "criterion_ids",
        "criteria_checked",
        "criteria_flagged",
        "criteria_escalated",
        "shortcut_codes",
        "trace_id",
        "total_ms",
        "sanitization_ms",
        "state_build_ms",
        "provider_latency_ms",
        "decision_policy_ms",
        "queue_ms",
    }
)


def system_one_event_payload(kind: str, payload: dict | None = None) -> dict:
    """The §24 payload guard: keep the contract fields, drop everything else.

    A normal event payload must never carry a secret or a complete private
    state (§24). Secrets are independently caught by the store's leak gate
    (:class:`SecretLeakError`); this function removes the *shape* problem, so a
    caller that passes a chat history or a raw state cannot persist it by
    accident. An unknown System-One kind is a programming error, not a silent
    pass-through.
    """
    if kind not in SYSTEM_ONE_EVENT_KINDS:
        raise ValueError(f"unknown System-One event kind: {kind!r}")
    data = dict(payload or {})
    return {key: value for key, value in data.items() if key in SYSTEM_ONE_PAYLOAD_KEYS}


MAX_PAYLOAD_CHARS = 100_000


class SecretLeakError(ValueError):
    """Raised when a payload would persist a secret into the event store."""

    def __init__(self, findings: list[Finding]):
        self.findings = findings
        kinds = ", ".join(sorted({f.kind for f in findings}))
        super().__init__(
            f"secret detected in event payload ({kinds}); redact before appending"
        )


@dataclass(frozen=True)
class Event:
    seq: int
    ts: float
    session_id: str
    step: int
    kind: str
    payload: dict


class EventStore:
    """SQLite-backed append-only store. Thread-safe: all access is serialized."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts         REAL NOT NULL,
                    session_id TEXT NOT NULL,
                    step       INTEGER NOT NULL,
                    kind       TEXT NOT NULL,
                    payload    TEXT NOT NULL
                )
                """
            )
            self._db.commit()

    def append(
        self,
        session_id: str,
        step: int,
        kind: str,
        payload: dict,
        ts: float | None = None,
    ) -> Event:
        """Append an event. Raises SecretLeakError if payload contains a secret."""
        return self._append(session_id, step, kind, payload, ts=ts, redact_secrets=False)

    def append_redacted(
        self,
        session_id: str,
        step: int,
        kind: str,
        payload: dict,
        ts: float | None = None,
    ) -> Event:
        """Append an event, replacing any found secrets with markers (audited)."""
        return self._append(session_id, step, kind, payload, ts=ts, redact_secrets=True)

    def _append(
        self,
        session_id: str,
        step: int,
        kind: str,
        payload: dict,
        ts: float | None,
        redact_secrets: bool,
    ) -> Event:
        if kind not in KINDS:
            raise ValueError(f"unknown event kind: {kind}")
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if len(serialized) > MAX_PAYLOAD_CHARS:
            raise ValueError(
                f"payload too large ({len(serialized)} chars > {MAX_PAYLOAD_CHARS})"
            )
        findings = scan(serialized)
        stored_payload = payload
        if findings:
            if not redact_secrets:
                raise SecretLeakError(findings)
            redacted_text, _ = redact(serialized)
            stored_payload = json.loads(redacted_text)
            stored_payload["_godsv_redacted"] = len(findings)
        event_ts = time.time() if ts is None else ts
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO events(ts, session_id, step, kind, payload) VALUES (?, ?, ?, ?, ?)",
                (
                    event_ts,
                    session_id,
                    step,
                    kind,
                    json.dumps(stored_payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            self._db.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("insert into events did not return a row id")
        return Event(
            seq=cursor.lastrowid,
            ts=event_ts,
            session_id=session_id,
            step=step,
            kind=kind,
            payload=stored_payload,
        )

    # -- reads -------------------------------------------------------------
    def iter_session(self, session_id: str) -> list[Event]:
        with self._lock:
            rows = self._db.execute(
                "SELECT seq, ts, session_id, step, kind, payload FROM events "
                "WHERE session_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def stream(self, after_seq: int = 0) -> list[Event]:
        with self._lock:
            rows = self._db.execute(
                "SELECT seq, ts, session_id, step, kind, payload FROM events "
                "WHERE seq > ? ORDER BY seq",
                (after_seq,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def count(self) -> int:
        with self._lock:
            (total,) = self._db.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(total)

    def session_ids(self) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT DISTINCT session_id FROM events ORDER BY session_id"
            ).fetchall()
        return [row[0] for row in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _row_to_event(row: tuple) -> Event:
        return Event(
            seq=row[0],
            ts=row[1],
            session_id=row[2],
            step=row[3],
            kind=row[4],
            payload=json.loads(row[5]),
        )
