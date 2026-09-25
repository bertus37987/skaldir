"""Voice pause — controlled interruption with leases, watchdog, exactly-once resume.

Flow (release spec): a concrete failure signal (loop ×N, repeated error burst,
contradiction, strong drift) opens a **pause lease**; research runs off-thread
with heartbeats; the next tool call can hold briefly for the redirect; if it is
ready in time, the tool call is **blocked once** with the redirect as its error
text — the main agent re-plans and continues (single resume, no forked runs).

Fault tolerance:
- leases have a TTL and a max total duration; research heartbeats every ~2s,
- a watchdog thread (independent of the research thread) expires dead leases,
- state is persisted in SQLite: any later process can recover/expire leases,
- delivery is conditional (``status='ready' → 'delivered'``): exactly once,
- late results are discarded against the lease's base step (stale window),
- ``voice`` leases are the only ones this module ever auto-releases; ``user``
  and ``safety`` are separate namespaces — and no pause path grants permissions.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .voice import assess_intervention

KIND_VOICE = "voice"
KIND_USER = "user"
KIND_SAFETY = "safety"
LEASE_KINDS = {KIND_VOICE, KIND_USER, KIND_SAFETY}

STATUS_REQUESTED = "requested"
STATUS_RESEARCHING = "researching"
STATUS_READY = "ready"
STATUS_DELIVERED = "delivered"
STATUS_EXPIRED = "expired"
STATUS_ABANDONED = "abandoned"
STATUS_STALE = "stale"
TERMINAL = {STATUS_DELIVERED, STATUS_EXPIRED, STATUS_ABANDONED, STATUS_STALE}

DEFAULT_TTL = 90.0
MAX_WAIT = 20.0
DEFAULT_STALE_WINDOW = 15
HEARTBEAT_STALE = 30.0
WATCHDOG_INTERVAL = 2.5

# pause triggers: signal kind → (occurrences to escalate, stale-window ok)
STRICTNESS_LEVELS = ("relaxed", "standard", "strict")
# How the strictness level shifts every trigger threshold (floor 1).
_STRICTNESS_DELTA = {"relaxed": 1, "standard": 0, "strict": -1}


def strictness_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / "settings.yaml"


def load_strictness(state_dir: str | Path) -> str:
    """Current voice strictness — read live, defaults to 'standard'."""
    import yaml

    try:
        data = yaml.safe_load(strictness_path(state_dir).read_text(encoding="utf-8")) or {}
        level = str(data.get("strictness") or "standard")
        return level if level in STRICTNESS_LEVELS else "standard"
    except Exception:
        return "standard"


def save_strictness(state_dir: str | Path, level: str) -> str:
    """Persist one strictness level (validated) — raises ValueError otherwise."""
    import yaml

    if level not in STRICTNESS_LEVELS:
        raise ValueError(f"unknown strictness: {level!r} (expected one of {STRICTNESS_LEVELS})")
    path = strictness_path(state_dir)
    data = {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            data = loaded
    except Exception:
        data = {}
    data["strictness"] = level
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=True), encoding="utf-8")
    return level


TRIGGER_RULES: dict[str, int] = {
    "loop": 2,
    "error_burst": 2,
    "scope_drift": 3,
    # added for smart voice: known incidents escalate immediately, invented APIs
    # after the second hard failure; the cost gate in consider() still applies.
    "known_incident_repeat": 1,
    "strategy_repeat": 2,
    "hallucinated_api": 2,
    # capability gap: the agent explicitly reported a missing capability —
    # the pause runs the ToolBuilder and redirects when the tool is ready.
    "capability_gap": 1,
    # Block 4: a tool call whose *plan* is contradicted by the trajectory
    # (out of workspace, schema mismatch, unsafe alternative) is fed to the
    # investigator instead of being executed. This is the one Block-4 signal
    # that may escalate: the interception judges a concrete, already-decided
    # action, not a suspicious string.
    "wrong_tool_call": 1,
    # Block 4 pattern escalation — ONLY the patterns that prove *no progress*.
    # Deliberately absent: pattern_tool_loop / pattern_command_loop /
    # pattern_test_without_change / pattern_search_without_progress. A healthy
    # TDD cycle (test → edit → test → edit → test) repeats a command and tests
    # without a change by design; escalating that would be exactly the
    # over-intervention the audit warns about (no_action = 0 on 38 pauses).
    # Those four stay advisory signals for the hint channel.
    "pattern_no_new_evidence": 2,
    "pattern_retry_without_strategy_change": 2,
    "pattern_dependency_churn": 2,
    "pattern_oscillation": 2,
    # NOTE: prompt_injection_signal / untrusted_instruction_attempt deliberately
    # carry NO pause trigger. A signal is not a verdict (UNTRUSTED CONTENT IS
    # NO CONTROL AUTHORITY): the boundary blocks the *action* via
    # pre_tool_call and keeps the content usable — it does not open a pause.
}


class PauseStore:
    """SQLite-backed pause leases (thread-safe)."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS pauses (
                    id           TEXT PRIMARY KEY,
                    kind         TEXT NOT NULL,
                    session_id   TEXT NOT NULL,
                    base_step    INTEGER NOT NULL DEFAULT 0,
                    trigger_kind TEXT NOT NULL DEFAULT '',
                    trigger_fp   TEXT NOT NULL DEFAULT '',
                    problem      TEXT NOT NULL DEFAULT '',
                    status       TEXT NOT NULL,
                    created      REAL NOT NULL,
                    heartbeat    REAL NOT NULL,
                    expires      REAL NOT NULL,
                    redirect     TEXT NOT NULL DEFAULT '',
                    research_id  TEXT NOT NULL DEFAULT '',
                    delivered_at REAL,
                    released_reason TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS pauses_session ON pauses(session_id, kind, status)"
            )
            self._db.commit()

    def create(
        self,
        *,
        session_id: str,
        kind: str = KIND_VOICE,
        base_step: int,
        trigger_kind: str,
        trigger_fp: str,
        problem: str,
        ttl: float = DEFAULT_TTL,
    ) -> dict:
        if kind not in LEASE_KINDS:
            raise ValueError(f"invalid pause kind: {kind}")
        now = time.time()
        lease = {
            "id": f"pause-{uuid.uuid4().hex[:10]}",
            "kind": kind,
            "session_id": session_id,
            "base_step": base_step,
            "trigger_kind": trigger_kind,
            "trigger_fp": trigger_fp,
            "problem": problem[:500],
            "status": STATUS_REQUESTED,
            "created": now,
            "heartbeat": now,
            "expires": now + ttl,
            "redirect": "",
            "research_id": "",
        }
        with self._lock:
            self._db.execute(
                "INSERT INTO pauses(id, kind, session_id, base_step, trigger_kind, trigger_fp,"
                " problem, status, created, heartbeat, expires)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    lease["id"],
                    kind,
                    session_id,
                    base_step,
                    trigger_kind,
                    trigger_fp,
                    lease["problem"],
                    lease["status"],
                    now,
                    now,
                    lease["expires"],
                ),
            )
            self._db.commit()
        return lease

    def get(self, pause_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM pauses WHERE id = ?", (pause_id,)).fetchone()
        return _row(row) if row else None

    def history_for_session(self, session_id: str, *, kind: str = KIND_VOICE) -> list[dict]:
        """All pauses of one session, newest first (persisted, cross-turn).

        Used by the escalation logic: repetitions must be judged from the
        durable record, not from in-memory counters that die with the
        session object at turn boundaries.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM pauses WHERE session_id = ? AND kind = ? ORDER BY created DESC",
                (session_id, kind),
            ).fetchall()
        return [_row(row) for row in rows]

    def current(self, session_id: str, *, kind: str = KIND_VOICE) -> dict | None:
        """Latest non-terminal lease for the session (per kind)."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM pauses WHERE session_id = ? AND kind = ?"
                " AND status NOT IN (?,?,?,?) ORDER BY created DESC LIMIT 1",
                (session_id, kind, *sorted(TERMINAL)),
            ).fetchone()
        return _row(row) if row else None

    def set_status(self, pause_id: str, status: str, *, from_statuses=None, **fields) -> bool:
        updates = ["status = ?"]
        values: list = [status]
        for key in ("redirect", "research_id", "released_reason"):
            if key in fields:
                updates.append(f"{key} = ?")
                values.append(fields[key])
        if "delivered_at" in fields:
            updates.append("delivered_at = ?")
            values.append(fields["delivered_at"])
        where = "id = ?"
        values.append(pause_id)
        if from_statuses:
            placeholders = ",".join("?" for _ in from_statuses)
            where += f" AND status IN ({placeholders})"
            values.extend(from_statuses)
        with self._lock:
            # SET/WHERE fragments come only from fixed code constants; all values are
            # parameterized placeholders (no user data is interpolated).
            query = f"UPDATE pauses SET {', '.join(updates)} WHERE {where}"  # nosec B608
            cursor = self._db.execute(query, values)
            self._db.commit()
        return cursor.rowcount > 0

    def heartbeat(self, pause_id: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE pauses SET heartbeat = ? WHERE id = ?", (time.time(), pause_id)
            )
            self._db.commit()

    def mark_delivered(self, pause_id: str) -> bool:
        """Conditional ready→delivered transition — the exactly-once gate."""
        return self.set_status(
            pause_id, STATUS_DELIVERED, from_statuses=[STATUS_READY], delivered_at=time.time()
        )

    def expire_stale(self, *, now: float | None = None, kind: str = KIND_VOICE) -> list[dict]:
        """Expire TTL-passed leases (voice kind only — never user/safety)."""
        moment = time.time() if now is None else now
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM pauses WHERE kind = ? AND status NOT IN (?,?,?,?) AND expires < ?",
                (kind, *sorted(TERMINAL), moment),
            ).fetchall()
        expired: list[dict] = []
        for row in rows:
            lease = _row(row)
            if self.set_status(lease["id"], STATUS_EXPIRED, from_statuses=[lease["status"]]):
                expired.append(lease)
        return expired

    def abandon_dead_research(
        self, *, now: float | None = None, stale_after: float = HEARTBEAT_STALE
    ) -> list[dict]:
        """Research thread died without updating the lease (heartbeat too old)."""
        moment = time.time() if now is None else now
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM pauses WHERE kind = ? AND status IN (?,?) AND heartbeat < ?",
                (KIND_VOICE, STATUS_REQUESTED, STATUS_RESEARCHING, moment - stale_after),
            ).fetchall()
        dead: list[dict] = []
        for row in rows:
            lease = _row(row)
            if self.set_status(lease["id"], STATUS_ABANDONED, from_statuses=[lease["status"]]):
                dead.append(lease)
        return dead

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _row(row: tuple) -> dict:
    keys = (
        "id",
        "kind",
        "session_id",
        "base_step",
        "trigger_kind",
        "trigger_fp",
        "problem",
        "status",
        "created",
        "heartbeat",
        "expires",
        "redirect",
        "research_id",
        "delivered_at",
        "released_reason",
    )
    return dict(zip(keys, row, strict=False))


class VoicePause:
    """Per-session controller: decide, lease, research, deliver once, watchdog."""

    def __init__(
        self,
        store: PauseStore,
        session_id: str,
        *,
        research_runner=None,
        emit=None,
        ttl: float = DEFAULT_TTL,
        stale_window: int = DEFAULT_STALE_WINDOW,
        cooldown_seconds: float = 60.0,
        max_per_hour: int = 4,
        state_dir: str | Path | None = None,
    ) -> None:
        self.store = store
        self.state_dir = Path(state_dir) if state_dir else None
        self.session_id = session_id
        self.research = research_runner
        self.emit = emit or (lambda kind, payload: None)
        self.ttl = ttl
        self.stale_window = stale_window
        self.cooldown_seconds = cooldown_seconds
        self.max_per_hour = max_per_hour
        self._lock = threading.RLock()
        self._counts: dict[str, int] = {}
        self._last_pause: dict[str, float] = {}
        self._pauses: list[float] = []
        self._watchdog: threading.Thread | None = None
        self._stop = threading.Event()

    # -- trigger policy -----------------------------------------------------
    def note_signals(self, signals, step: int) -> None:
        """Count signal fingerprints per session (cheap, in-memory)."""
        with self._lock:
            for signal in signals:
                key = f"{signal.kind}:{signal.fingerprint}"
                self._counts[key] = self._counts.get(key, 0) + 1

    def consider(
        self,
        *,
        step: int,
        signals=(),
        contradiction: dict | None = None,
        problem: str = "",
    ) -> str | None:
        """Request a pause when a concrete trigger is met (or return None)."""
        with self._lock:
            if step == 0:
                return None
            if signals:
                self.note_signals(signals, step)
            if self.store.current(self.session_id) is not None:
                return None
            now = time.time()
            self._pauses = [ts for ts in self._pauses if now - ts < 3600]
            if len(self._pauses) >= self.max_per_hour:
                return None
            candidate: tuple[str, str, str, int, int] | None = None
            if contradiction is not None:
                candidate = (
                    "contradiction",
                    str(contradiction.get("fingerprint", ""))[:16],
                    problem or str(contradiction.get("claim", "")),
                    3,
                    2,
                )
            else:
                delta = 0
                if self.state_dir is not None:
                    delta = _STRICTNESS_DELTA.get(load_strictness(self.state_dir), 0)
                for signal in signals:
                    base = TRIGGER_RULES.get(signal.kind)
                    if base is None:
                        continue
                    threshold = max(1, base + delta)
                    if signal.kind == "scope_drift":
                        # drift spreads over DIFFERENT paths; a per-path
                        # fingerprint would never reach the threshold there
                        # (gradual drift, §33). Count the class instead.
                        count = sum(
                            value
                            for key, value in self._counts.items()
                            if key.startswith("scope_drift:")
                        )
                    else:
                        key = f"{signal.kind}:{signal.fingerprint}"
                        count = self._counts.get(key, 0)
                    if count >= threshold:
                        candidate = (
                            signal.kind,
                            signal.fingerprint,
                            problem or signal.message,
                            signal.severity,
                            count,
                        )
                        break
            if candidate is None:
                return None
            trigger_kind, trigger_fp, trigger_problem, trigger_severity, occurrences = candidate
            # P1-5: how often was THIS failure class paused before? — durable
            # count from the store; repeated interventions must change the
            # strategy instead of looping pause → redirect → same mistake.
            repeated = 0
            try:
                repeated = sum(
                    1
                    for row in self.store.history_for_session(self.session_id)
                    if row.get("trigger_kind") == trigger_kind
                )
            except Exception:
                repeated = 0
            novel_required = repeated >= 2
            if novel_required:
                trigger_problem = (
                    f"{trigger_problem}\n\nREPEATED INTERVENTION (#{repeated + 1} for "
                    f"'{trigger_kind}'): the same class of problem was already paused and "
                    "redirected in this session. The next attempt must be a NOVEL strategy — "
                    "state first, concretely, why it differs from the previous directions, and "
                    "do not repeat a strategy that already failed."
                )
            last = self._last_pause.get(trigger_fp)
            if last is not None and now - last < self.cooldown_seconds:
                return None
            assessment = assess_intervention(
                trigger_kind=trigger_kind,
                severity=trigger_severity,
                occurrences=occurrences,
                strictness=(
                    load_strictness(self.state_dir) if self.state_dir is not None else "standard"
                ),
            )
            self.emit(
                "voice_intervention_assessment",
                {
                    "trigger": trigger_kind,
                    "occurrences": occurrences,
                    "measured": assessment.measured,
                    "estimated": assessment.estimated,
                    "intervene": assessment.intervene,
                },
            )
            if not assessment.intervene:
                return None
            for signal in signals:  # cooldown also by step distance
                key = f"{signal.kind}:{signal.fingerprint}"
                self._counts[key] = 0  # reset counter so escalation needs fresh evidence
            self._last_pause[trigger_fp] = now
            self._pauses.append(now)
            lease = self.store.create(
                session_id=self.session_id,
                base_step=step,
                trigger_kind=trigger_kind,
                trigger_fp=trigger_fp,
                problem=trigger_problem,
                ttl=self.ttl,
            )
        self.emit(
            "pause_requested",
            {
                "pause": lease["id"],
                "trigger": trigger_kind,
                "fingerprint": trigger_fp,
                "base_step": step,
                "ttl": self.ttl,
                "repeated": repeated,
                "novel_strategy_required": novel_required,
            },
        )
        self._start_research(lease, step, trigger_kind, trigger_fp, trigger_problem)
        return lease["id"]

    def _start_research(
        self, lease: dict, step: int, trigger_kind: str, trigger_fp: str, problem: str
    ) -> None:
        if self.research is None:
            self.store.set_status(
                lease["id"], STATUS_ABANDONED, released_reason="no research runner"
            )
            return
        runner = self.research
        self.store.set_status(lease["id"], STATUS_RESEARCHING, from_statuses=[STATUS_REQUESTED])
        self.emit("research_started", {"pause": lease["id"], "trigger": trigger_kind})
        beat_stop = threading.Event()

        def beat() -> None:
            while not beat_stop.wait(2.0):
                try:
                    self.store.heartbeat(lease["id"])
                except Exception:
                    return

        def work() -> None:
            try:
                result = runner.run(
                    session_id=self.session_id,
                    base_step=step,
                    trigger_kind=trigger_kind,
                    trigger_fp=trigger_fp,
                    problem=problem,
                )
                if result.status == "ok" and not getattr(result, "intervention_needed", True):
                    # §10: the investigation concluded NO_INTERVENTION_NEEDED —
                    # close the pause without a redirect; the main agent keeps
                    # working undisturbed (normal recoverable debugging).
                    reason = (
                        "Investigation: no intervention needed — "
                        + (
                            getattr(result, "no_intervention_reason", "")
                            or "normal recoverable progress"
                        )
                    )[:300]
                    self.store.set_status(
                        lease["id"],
                        STATUS_ABANDONED,
                        from_statuses=[STATUS_REQUESTED, STATUS_RESEARCHING],
                        research_id=result.research_id,
                        released_reason=reason,
                    )
                    self.emit(
                        "pause_no_action",
                        {
                            "pause": lease["id"],
                            "research": result.research_id,
                            "reason": reason,
                        },
                    )
                    return
                if result.status == "ok":
                    self.store.set_status(
                        lease["id"],
                        STATUS_READY,
                        from_statuses=[STATUS_REQUESTED, STATUS_RESEARCHING],
                        redirect=result.redirect_md(),
                        research_id=result.research_id,
                    )
                    self.emit(
                        "research_done",
                        {
                            "pause": lease["id"],
                            "research": result.research_id,
                            "duration_ms": result.duration_ms,
                            "used_llm": result.used_llm,
                            "tokens_est": result.tokens_est,
                            "archive": result.archive_path,
                            "problem": result.problem[:200],
                            "direction": result.direction[:300],
                            "evidence": result.evidence[:3],
                            "uncertainty": result.uncertainty[:200],
                            "trigger": trigger_kind,
                        },
                    )
                else:
                    self.store.set_status(
                        lease["id"],
                        STATUS_ABANDONED,
                        from_statuses=[STATUS_REQUESTED, STATUS_RESEARCHING],
                        released_reason="research failed",
                    )
                    self.emit(
                        "research_failed",
                        {"pause": lease["id"], "research": result.research_id},
                    )
            except Exception as exc:  # never take the pause machinery down
                try:
                    self.store.set_status(
                        lease["id"],
                        STATUS_ABANDONED,
                        from_statuses=[STATUS_REQUESTED, STATUS_RESEARCHING],
                        released_reason=f"research error: {type(exc).__name__}",
                    )
                    self.emit(
                        "research_failed", {"pause": lease["id"], "error": type(exc).__name__}
                    )
                except Exception:  # nosec B110 - last resort: the worker must never die
                    pass
            finally:
                beat_stop.set()

        threading.Thread(target=beat, name="godsv-pause-heartbeat", daemon=True).start()
        threading.Thread(target=work, name="godsv-pause-research", daemon=True).start()
        self._ensure_watchdog()

    # -- delivery (called from pre_tool_call) -------------------------------
    def deliverable(self, *, step: int, wait: float = 0.0) -> dict | None:
        """Return {"pause_id", "redirect"} when a redirect may be delivered now.

        Waits up to ``wait`` seconds for a researching lease to become ready.
        Exactly-once: the ready→delivered transition is a conditional update.
        """
        lease = self.store.current(self.session_id)
        if lease is None:
            return None
        if lease["expires"] < time.time():
            if self.store.set_status(lease["id"], STATUS_EXPIRED, from_statuses=[lease["status"]]):
                self.emit("pause_expired", {"pause": lease["id"], "reason": "ttl"})
            return None
        deadline = time.monotonic() + max(0.0, min(wait, MAX_WAIT))
        while lease["status"] != STATUS_READY and time.monotonic() < deadline:
            if lease["status"] in TERMINAL:
                return None
            time.sleep(0.1)
            lease = self.store.current(self.session_id)
            if lease is None:
                return None
        if lease["status"] != STATUS_READY:
            return None
        if step - lease["base_step"] > self.stale_window:
            if self.store.set_status(
                lease["id"], STATUS_STALE, from_statuses=[STATUS_READY],
                released_reason="result stale (session advanced)",
            ):
                self.emit("pause_stale", {"pause": lease["id"], "base_step": lease["base_step"]})
            return None
        if self.store.mark_delivered(lease["id"]):
            self.emit(
                "pause_delivered",
                {
                    "pause": lease["id"],
                    "trigger": lease["trigger_kind"],
                    "fingerprint": lease["trigger_fp"],
                    "recovery_ms": int((time.time() - lease["created"]) * 1000),
                },
            )
            return {"pause_id": lease["id"], "redirect": lease["redirect"]}
        return None

    def release(self, reason: str) -> bool:
        """Release an open voice lease (normal path or abandon)."""
        lease = self.store.current(self.session_id)
        if lease is None:
            return False
        if self.store.set_status(
            lease["id"], STATUS_ABANDONED, from_statuses=[lease["status"]], released_reason=reason
        ):
            self.emit("pause_released", {"pause": lease["id"], "reason": reason})
            return True
        return False

    # -- watchdog -----------------------------------------------------------
    def _ensure_watchdog(self) -> None:
        if self._watchdog is not None and self._watchdog.is_alive():
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.wait(WATCHDOG_INTERVAL):
                try:
                    for lease in self.store.expire_stale():
                        self.emit("pause_expired", {"pause": lease["id"], "reason": "watchdog-ttl"})
                    for lease in self.store.abandon_dead_research():
                        self.emit(
                            "pause_released",
                            {"pause": lease["id"], "reason": "watchdog: Research ohne Heartbeat"},
                        )
                except Exception:  # nosec B112 - watchdog must keep ticking
                    continue

        self._watchdog = threading.Thread(target=loop, name="godsv-pause-watchdog", daemon=True)
        self._watchdog.start()

    def stop(self) -> None:
        self._stop.set()
