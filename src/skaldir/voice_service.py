"""The SUPERVISE layer's service half — one pass over a stored trajectory.

``skaldir voice status|check|triage|explain`` all answer questions about the
**supervisor that already exists** in this repo. Nothing here re-implements a
detector: the pass *replays* the running supervisor's own machinery over the
events a session really stored, and reports what it found, with evidence.

What is reused (never forked)
-----------------------------

===========================================  =========================================
:mod:`skaldir.monitor`                         the deterministic step detector
                                             (``Monitor.ingest``): loops, error
                                             bursts, strategy repeats, scope drift,
                                             slop, hallucinated APIs
:mod:`skaldir.patterns`                        the Block-4 sequence analyzer
                                             (``SequenceAnalyzer`` over normalised
                                             effect signatures): tool/command loops,
                                             oscillation, churn, change without
                                             verification, retry without strategy
:mod:`skaldir.guard`                           the claim/evidence guards
                                             (``evaluate_completion``,
                                             ``find_contradiction``): false
                                             completion claims and contradictions
:mod:`skaldir.voice`                           ``read_trajectory_health`` (the
                                             deterministic "is this healthy
                                             debugging?" reading) and the Voice
                                             budget
:mod:`skaldir.pause`                           ``TRIGGER_RULES``, strictness levels,
                                             the interval costs; ``PauseStore`` for
                                             the *real* interruption history
:mod:`skaldir.system_one_flags`                the flags that decide whether Jev may
                                             answer at all
:mod:`skaldir.jev`                             the ``voice_triage`` pack, reached as
                                             the façade (``decide()``) — the pack is
                                             the substrate's ``voice_triage_v1``
:mod:`skaldir.ledger` / :mod:`skaldir.run_supervisor` / :mod:`skaldir.runs_store`
                                             the durable state a status snapshot
                                             reads (criteria, run invariant, leases)
===========================================  =========================================

The six finding categories are the SUPERVISE contract's own list. Each finding
carries the signal kind that raised it and the event refs it came from, so a
report can always be traced back to the stored trajectory:

``loops`` · ``goal_drift`` · ``missed_errors`` · ``false_completion_claims`` ·
``bad_tool_use`` · ``missing_verification``

Read-only by construction
-------------------------

The event store — the only store a check reads — is opened ``mode=ro`` and its rows
are returned as the store's own :class:`skaldir.events.Event` objects. ``status``
aggregates ``pauses.db`` with a ``mode=ro`` query and opens the run store only when
``runs.db`` already exists; ``triage`` reads the session's **durable interruption
history** through the real :class:`skaldir.pause.PauseStore` (its constructor is an
idempotent ``CREATE TABLE IF NOT EXISTS``, so an existing store is neither created
nor rewritten). No verb opens a pause, delivers a redirect or writes a run record —
deciding "wake the Voice" is a reading, and only the live plugin may act on it.

The owner's rule for triage
---------------------------

**The Voice must not interfere when the agent has already recognised the problem
and is sensibly working on it.** :func:`triage` implements exactly that: a
finding only escalates to ``wake_voice`` when the deterministic reading does
*not* show the agent handling it. The evidence for "handling it" is
:class:`skaldir.voice.TrajectoryHealth` — more than one distinct error signature,
new observations, a localised fix in the file the error named, a verification
run after the change — plus a concrete response *after* the finding's step.
Without that evidence the supervisor stays silent instead of interrupting a
healthy debugging cycle.
"""

from __future__ import annotations

import inspect
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import guard as G
from . import monitor as M
from . import patterns as SEQ
from . import pause as P
from .events import Event
from .pact import Pact
from .research import _terms
from .voice import assess_intervention, read_trajectory_health

# --------------------------------------------------------------------------- #
# the six categories (SUPERVISE contract)                                       #
# --------------------------------------------------------------------------- #

CAT_LOOPS = "loops"
CAT_DRIFT = "goal_drift"
CAT_ERRORS = "missed_errors"
CAT_CLAIMS = "false_completion_claims"
CAT_TOOLUSE = "bad_tool_use"
CAT_VERIFY = "missing_verification"

CATEGORIES: tuple[str, ...] = (
    CAT_LOOPS,
    CAT_DRIFT,
    CAT_ERRORS,
    CAT_CLAIMS,
    CAT_TOOLUSE,
    CAT_VERIFY,
)

CATEGORY_LABELS: dict[str, str] = {
    CAT_LOOPS: "the same thing is repeated without progress",
    CAT_DRIFT: "activity that no longer serves the recorded goal",
    CAT_ERRORS: "an error appeared and nothing was done about it",
    CAT_CLAIMS: "a completion/success claim the trajectory does not back",
    CAT_TOOLUSE: "a tool used in a way that hurts the task (revert, misdiagnosis, churn)",
    CAT_VERIFY: "work changed without being verified afterwards",
}

#: monitor/pattern signal kind → category. One table, so a new detector cannot
#: land in "some" category by accident.
SIGNAL_CATEGORY: dict[str, str] = {
    # -- loops ------------------------------------------------------------- #
    "loop": CAT_LOOPS,
    "error_burst": CAT_LOOPS,
    "strategy_repeat": CAT_LOOPS,
    "known_incident_repeat": CAT_LOOPS,
    "error_recurrence_after_progress": CAT_LOOPS,
    "stall": CAT_LOOPS,
    "diff_churn": CAT_LOOPS,
    "pattern_tool_loop": CAT_LOOPS,
    "pattern_command_loop": CAT_LOOPS,
    "pattern_oscillation": CAT_LOOPS,
    "pattern_retry_without_strategy_change": CAT_LOOPS,
    "pattern_no_new_evidence": CAT_LOOPS,
    # -- goal drift -------------------------------------------------------- #
    "scope_drift": CAT_DRIFT,
    "goal_drift": CAT_DRIFT,
    "pattern_dependency_churn": CAT_DRIFT,
    # -- missed errors ----------------------------------------------------- #
    "error_without_details": CAT_ERRORS,
    "error_cluster": CAT_ERRORS,
    "hallucinated_api": CAT_ERRORS,
    "outage_misdiagnosis": CAT_ERRORS,
    "slop_bare_except": CAT_ERRORS,
    "slop_except_pass": CAT_ERRORS,
    # -- false completion claims ------------------------------------------- #
    "contradiction": CAT_CLAIMS,
    "completion_verdict": CAT_CLAIMS,
    "guard_finding": CAT_CLAIMS,
    "slop_stub": CAT_CLAIMS,
    "slop_todo_placeholder": CAT_CLAIMS,
    "slop_tbd_comment": CAT_CLAIMS,
    "slop_assert_true": CAT_CLAIMS,
    # -- bad tool use ------------------------------------------------------ #
    "revert": CAT_TOOLUSE,
    "dependency_change": CAT_TOOLUSE,
    "tool_call_intercepted": CAT_TOOLUSE,
    "pattern_search_without_progress": CAT_TOOLUSE,
    "pattern_test_without_change": CAT_TOOLUSE,
    # -- missing verification ---------------------------------------------- #
    "pattern_change_without_verification": CAT_VERIFY,
    "no_verification_after_change": CAT_VERIFY,
}

#: category → the Jev trigger kind its request carries. The ``voice_triage`` pack
#: is the substrate registry's ``voice_triage_v1``; this mapping only names which
#: of the pack's trigger groups a finding belongs to.
CATEGORY_TRIGGER: dict[str, str] = {
    CAT_LOOPS: "loop",
    CAT_DRIFT: "scope_drift",
    CAT_ERRORS: "error_burst",
    CAT_CLAIMS: "contradiction",
    CAT_TOOLUSE: "wrong_tool_call",
    CAT_VERIFY: "*",
}

SEVERITY_NAMES = {M.SEV_INFO: "info", M.SEV_WARN: "warn", M.SEV_HIGH: "high"}

#: Findings that wake the Voice although the live pause policy keeps no threshold
#: for their kind: the SUPERVISE contract names them (a false completion claim, a
#: contradicted claim, an unverified change, goal drift) and Jev's
#: ``voice_triage_v1`` pack has a rule for each. Everything else escalates only
#: through the same table the live controller uses (:data:`skaldir.pause.TRIGGER_RULES`).
WAKE_ALWAYS: frozenset[str] = frozenset(
    {
        "guard_finding",
        "completion_verdict",
        "contradiction",
        "goal_drift",
        "no_verification_after_change",
    }
)

DEFAULT_WINDOW = 12
#: the same streak the plugin's live drift counter uses (three unrelated edits
#: in a row is a signal, one is noise)
DRIFT_STREAK = 3
DRIFT_TERM_COUNT = 24
_TERM_STEM = 4


def severity_name(severity: int) -> str:
    return SEVERITY_NAMES.get(int(severity), "info")


# --------------------------------------------------------------------------- #
# paths                                                                         #
# --------------------------------------------------------------------------- #


def default_runtime() -> Path:
    """``$SKALDIR_HOME`` → ``$HERMES_HOME/godsv`` → ``~/.hermes/godsv`` (one default)."""
    from .registry import default_runtime_dir

    return Path(default_runtime_dir())


def events_path(runtime: str | Path | None = None) -> Path:
    return Path(runtime or default_runtime()) / "events.db"


def pauses_path(runtime: str | Path | None = None) -> Path:
    return Path(runtime or default_runtime()) / "pauses.db"


def runs_path(runtime: str | Path | None = None) -> Path:
    return Path(runtime or default_runtime()) / "runs.db"


def incidents_path(runtime: str | Path | None = None) -> Path:
    return Path(runtime or default_runtime()) / "memory.db"


def completion_dir(runtime: str | Path | None = None) -> Path:
    return Path(runtime or default_runtime()) / "completion"


def settings_path(runtime: str | Path | None = None) -> Path:
    return Path(runtime or default_runtime()) / "settings.yaml"


# --------------------------------------------------------------------------- #
# the stored trajectory (read-only)                                             #
# --------------------------------------------------------------------------- #


@dataclass
class Trajectory:
    """One session's stored events, read without touching the store."""

    session_id: str
    db_path: Path
    exists: bool
    reason: str = ""
    events: list[Event] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.events)

    @property
    def first_step(self) -> int:
        return min((event.step for event in self.events), default=0)

    @property
    def last_step(self) -> int:
        return max((event.step for event in self.events), default=0)

    @property
    def last_ts(self) -> float:
        return max((event.ts for event in self.events), default=0.0)

    def kinds(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self.events:
            counts[event.kind] = counts.get(event.kind, 0) + 1
        return dict(sorted(counts.items()))

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "events_db": str(self.db_path),
            "exists": self.exists,
            "reason": self.reason,
            "events": self.count,
            "first_step": self.first_step,
            "last_step": self.last_step,
            "last_ts": self.last_ts,
            "kinds": self.kinds(),
        }


class ReadOnlyEventView:
    """The ``EventStore`` surface the guards use — over already-read events.

    :func:`skaldir.guard.evaluate_completion` and
    :func:`skaldir.guard.find_contradiction` read a session through
    ``store.iter_session(session_id)``. This view returns exactly the events that
    were just read from the session's own rows (nothing is synthesised, nothing
    is widened), so a ``voice check`` never has to open the live store for
    writing. The guards' call sites carry a ``type: ignore`` for it: the view
    implements the one method they call, not the whole store class.
    """

    def __init__(self, trajectory: Trajectory) -> None:
        self._trajectory = trajectory

    def iter_session(self, session_id: str) -> list[Event]:
        if str(session_id) != self._trajectory.session_id:
            return []
        return list(self._trajectory.events)


def _read_only(db_path: Path) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None


def pause_defaults() -> dict:
    """The live pause controller's own limits, read from its signature.

    ``cooldown_seconds``/``max_per_hour``/``stale_window`` are ``VoicePause``
    defaults in :mod:`skaldir.pause`; reading them there keeps one source of truth
    instead of restating the numbers in a report.
    """
    parameters = inspect.signature(P.VoicePause.__init__).parameters
    return {
        "default_ttl_s": float(P.DEFAULT_TTL),
        "max_wait_s": float(P.MAX_WAIT),
        "watchdog_interval_s": float(P.WATCHDOG_INTERVAL),
        "cooldown_seconds": float(parameters["cooldown_seconds"].default),
        "max_per_hour": int(parameters["max_per_hour"].default),
        "stale_window": int(parameters["stale_window"].default),
    }


def read_trajectory(
    db_path: str | Path | None = None,
    session_id: str = "",
    *,
    until_step: int = 0,
) -> Trajectory:
    """Load one session's events (read-only). Never raises for a missing store.

    An absent ``events.db``, an unreadable file or a session with no rows are
    all reported as ``exists=False`` with the reason — the caller decides what
    that means; nothing is invented.
    """
    path = Path(db_path or events_path())
    if not path.is_file():
        return Trajectory(session_id=session_id, db_path=path, exists=False,
                          reason=f"no event store at {path}")
    connection = _read_only(path)
    if connection is None:
        return Trajectory(session_id=session_id, db_path=path, exists=False,
                          reason=f"event store {path} is not readable")
    try:
        rows = connection.execute(
            "SELECT seq, ts, session_id, step, kind, payload FROM events "
            "WHERE session_id = ? ORDER BY seq",
            (str(session_id),),
        ).fetchall()
    except sqlite3.Error as exc:
        return Trajectory(session_id=session_id, db_path=path, exists=False,
                          reason=f"event store {path} is unreadable: {exc}")
    finally:
        connection.close()
    events: list[Event] = []
    for seq, ts, sid, step, kind, payload in rows:
        if until_step and int(step) > int(until_step):
            continue
        try:
            decoded = json.loads(payload)
        except (TypeError, ValueError):
            decoded = {"_unreadable_payload": str(payload)[:200]}
        events.append(
            Event(
                seq=int(seq),
                ts=float(ts or 0.0),
                session_id=str(sid),
                step=int(step),
                kind=str(kind),
                payload=decoded if isinstance(decoded, dict) else {"payload": decoded},
            )
        )
    return Trajectory(
        session_id=str(session_id),
        db_path=path,
        exists=bool(events),
        reason="" if events else f"session {session_id!r} has no stored events",
        events=events,
    )


def list_sessions(db_path: str | Path | None = None) -> list[dict]:
    """Every session in the store with its event count and step range."""
    path = Path(db_path or events_path())
    if not path.is_file():
        return []
    connection = _read_only(path)
    if connection is None:
        return []
    try:
        rows = connection.execute(
            "SELECT session_id, COUNT(*), MIN(step), MAX(step), MAX(ts), MAX(seq) "
            "FROM events GROUP BY session_id ORDER BY MAX(seq)",
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    return [
        {
            "session_id": str(session_id),
            "events": int(count),
            "first_step": int(first or 0),
            "last_step": int(last or 0),
            "last_ts": float(ts or 0.0),
            "last_seq": int(seq or 0),
        }
        for session_id, count, first, last, ts, seq in rows
    ]


def newest_session(db_path: str | Path | None = None) -> str:
    sessions = list_sessions(db_path)
    return str(sessions[-1]["session_id"]) if sessions else ""


def resolve_session(
    db_path: str | Path | None = None, session_id: str = ""
) -> tuple[str, str]:
    """``(session_id, how)`` — the named session, else the newest one in the store."""
    if session_id:
        return str(session_id), "argument"
    newest = newest_session(db_path)
    return newest, "newest" if newest else "none"


def resolve_root(
    runtime: str | Path | None = None, events_db: str | Path | None = None
) -> tuple[Path, Path]:
    """``(events_db, runtime_root)`` — one resolution rule for every verb.

    A named ``events_db`` also names its own runtime root (its parent), so a check
    against a store somewhere else never silently reads the incidents, contracts
    or leases of the default runtime.
    """
    if events_db:
        db_path = Path(events_db)
        root = Path(runtime) if runtime else db_path.parent
        return db_path, root
    root = Path(runtime) if runtime else default_runtime()
    return events_path(root), root


# --------------------------------------------------------------------------- #
# findings                                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Finding:
    """One supervision finding — always traceable to the events it came from."""

    category: str
    kind: str
    severity: int
    message: str
    step: int = 0
    fingerprint: str = ""
    evidence: tuple[str, ...] = ()
    refs: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "kind": self.kind,
            "severity": int(self.severity),
            "severity_name": severity_name(self.severity),
            "message": self.message,
            "step": int(self.step),
            "fingerprint": self.fingerprint,
            "evidence": list(self.evidence),
            "refs": dict(self.refs),
        }


def _event_ref(events_db: Path, session_id: str, step: int, kind: str) -> str:
    return f"{events_db}#{session_id}:step={step}/{kind}"


def category_for(kind: str) -> str:
    """The category a signal kind belongs to (``missed_errors`` when unnamed).

    An unmapped kind is reported under the closest honest home — an error it is
    not evidence of, so it goes to ``missed_errors`` with its kind visible.
    """
    if kind.startswith("tool_error_"):
        return CAT_ERRORS
    return SIGNAL_CATEGORY.get(str(kind), CAT_ERRORS)


# --------------------------------------------------------------------------- #
# the check pass                                                                #
# --------------------------------------------------------------------------- #


def _monitor_findings(
    trajectory: Trajectory, pact: Pact | None, known_error_fingerprints: set[str] | None
) -> tuple[list[Finding], list[dict]]:
    monitor = M.Monitor(
        pact=pact,
        known_error_fingerprints=set(known_error_fingerprints or ()),
    )
    findings: list[Finding] = []
    raw: list[dict] = []
    for event in trajectory.events:
        for signal in monitor.ingest(event):
            raw.append(
                {
                    "kind": signal.kind,
                    "severity": int(signal.severity),
                    "fingerprint": signal.fingerprint,
                    "message": signal.message,
                    "step": int(event.step),
                }
            )
            findings.append(
                Finding(
                    category=category_for(signal.kind),
                    kind=signal.kind,
                    severity=int(signal.severity),
                    message=signal.message,
                    step=int(event.step),
                    fingerprint=str(signal.fingerprint),
                    evidence=(
                        _event_ref(trajectory.db_path, trajectory.session_id,
                                   event.step, signal.kind),
                    ),
                    refs=dict(signal.refs or {}),
                )
            )
    return findings, raw


def _pattern_findings(trajectory: Trajectory) -> tuple[list[Finding], list[dict]]:
    analyzer = SEQ.SequenceAnalyzer()
    findings: list[Finding] = []
    raw: list[dict] = []
    for event in trajectory.events:
        step = int(event.step)
        hits: list[SEQ.PatternHit] = []
        if event.kind == "tool_call":
            hits = analyzer.ingest(
                SEQ.observe_call(step, str(event.payload.get("name") or ""),
                                 event.payload.get("args") or {})
            )
        elif event.kind == "tool_result":
            error = M.error_text(event.payload)
            hits = analyzer.ingest(
                SEQ.observe_result(
                    step,
                    failed=bool(error),
                    succeeded=not error,
                    error=error,
                    output=str(event.payload.get("output") or event.payload.get("stdout") or ""),
                    tool=str(event.payload.get("tool") or ""),
                )
            )
        elif event.kind == "file_edit":
            hits = analyzer.ingest(
                SEQ.observe_edit(step, str(event.payload.get("path") or ""),
                                 str(event.payload.get("added") or ""))
            )
        for hit in hits:
            kind = f"pattern_{hit.pattern}"
            raw.append(
                {
                    "kind": kind,
                    "severity": int(hit.severity),
                    "fingerprint": hit.fingerprint,
                    "message": hit.message,
                    "step": step,
                }
            )
            findings.append(
                Finding(
                    category=category_for(kind),
                    kind=kind,
                    severity=int(hit.severity),
                    message=hit.message,
                    step=step,
                    fingerprint=str(hit.fingerprint),
                    evidence=(_event_ref(trajectory.db_path, trajectory.session_id, step,
                                         hit.pattern),),
                    refs={"pattern": hit.pattern, **dict(hit.refs or {})},
                )
            )
    return findings, raw


def _shared_stem(a: str, b: str, width: int = _TERM_STEM) -> bool:
    shared = 0
    for left, right in zip(a, b, strict=False):
        if left != right:
            break
        shared += 1
    return shared >= width


def _drift_findings(trajectory: Trajectory, goal_text: str) -> tuple[list[Finding], dict]:
    """Rule: three edits in a row whose path shares no term with the goal.

    The live plugin keeps a streaming counter for this; a stored trajectory is
    replayed with the same conservative rule (term match on the lowered path, or
    a four-character stem overlap) and the same streak length — one signal per
    streak, never one per edit.
    """
    facts = {"goal": goal_text, "terms": [], "unrelated_streak": 0, "edits": 0}
    if not goal_text.strip():
        facts["reason"] = "no recorded goal — goal drift cannot be judged"
        return [], facts
    terms = set(_terms(goal_text, limit=DRIFT_TERM_COUNT))
    facts["terms"] = sorted(terms)
    if not terms:
        facts["reason"] = "the recorded goal carries no usable term"
        return [], facts
    findings: list[Finding] = []
    streak = 0
    longest = 0
    for event in trajectory.events:
        if event.kind != "file_edit":
            continue
        path = str(event.payload.get("path") or "")
        if not path:
            continue
        facts["edits"] += 1
        low = path.lower()
        tokens = [token for token in re.split(r"[^a-z0-9]+", low) if len(token) >= _TERM_STEM]
        related = (
            any(term in low for term in terms)
            or any(_shared_stem(token, term) for token in tokens for term in terms)
        )
        streak = 0 if related else streak + 1
        longest = max(longest, streak)
        if not related and streak >= DRIFT_STREAK:
            streak = 0  # one signal per streak
            all_terms = sorted(terms)[:6]
            findings.append(
                Finding(
                    category=CAT_DRIFT,
                    kind="goal_drift",
                    severity=M.SEV_WARN,
                    message=(
                        f"{DRIFT_STREAK} edits in a row outside the recorded goal's terms "
                        f"({', '.join(all_terms)}) — latest: {path}"
                    ),
                    step=int(event.step),
                    fingerprint=M.error_fingerprint(f"goal_drift:{path}"),
                    evidence=(_event_ref(trajectory.db_path, trajectory.session_id,
                                         event.step, "file_edit"),),
                    refs={"path": path, "goal": goal_text[:200], "terms": list(terms)[:12]},
                )
            )
    facts["unrelated_streak"] = longest
    return findings, facts


def _guard_findings(trajectory: Trajectory) -> tuple[list[Finding], dict]:
    view = ReadOnlyEventView(trajectory)
    claims: list[str] = []
    for event in trajectory.events:
        if event.kind not in ("claim", "guard_check"):
            continue
        text = str(event.payload.get("text") or event.payload.get("snippet") or "").strip()
        if text:
            claims.append(text)
    final_text = "\n".join(claims)
    facts: dict = {"claims_recorded": len(claims), "guard_status": "", "guard_findings": 0,
                   "contradiction": None}
    findings: list[Finding] = []
    if not final_text.strip():
        facts["reason"] = "the session stored no claim text to check"
        return findings, facts
    verdict = G.evaluate_completion(view, trajectory.session_id, final_text)  # type: ignore[arg-type]
    facts["guard_status"] = verdict.status
    facts["claims_checked"] = verdict.claims_checked
    facts["assumptions"] = list(verdict.assumptions)
    for item in verdict.findings:
        facts["guard_findings"] += 1
        findings.append(
            Finding(
                category=CAT_CLAIMS,
                kind="guard_finding",
                severity=M.SEV_HIGH if verdict.status == "unsupported" else M.SEV_WARN,
                message=f"unbacked {item.category} claim: {item.missing}"[:400],
                step=trajectory.last_step,
                fingerprint=M.error_fingerprint(f"guard:{item.category}:{item.claim[:80]}"),
                evidence=(_event_ref(trajectory.db_path, trajectory.session_id,
                                     trajectory.last_step, "claim"),),
                refs={"claim": item.claim, "missing": item.missing,
                      "criterion_id": item.criterion_id, "category": item.category},
            )
        )
    for event in trajectory.events:
        if event.kind != "completion_verdict":
            continue
        status = str(event.payload.get("status") or "")
        blockers = [str(item) for item in (event.payload.get("blockers") or [])]
        if status in ("VERIFIED_COMPLETE", "") or not blockers:
            continue
        findings.append(
            Finding(
                category=CAT_CLAIMS,
                kind="completion_verdict",
                severity=M.SEV_HIGH,
                message=(f"the completion gate returned {status}: " + "; ".join(blockers))[:400],
                step=int(event.step),
                fingerprint=str(event.payload.get("fingerprint") or
                                M.error_fingerprint(f"verdict:{status}")),
                evidence=(_event_ref(trajectory.db_path, trajectory.session_id,
                                     event.step, "completion_verdict"),),
                refs={"status": status, "blockers": blockers[:5],
                      "escalate": bool(event.payload.get("escalate"))},
            )
        )
    contradiction = G.find_contradiction(view, trajectory.session_id)  # type: ignore[arg-type]
    facts["contradiction"] = contradiction
    if contradiction:
        findings.append(
            Finding(
                category=CAT_CLAIMS,
                kind="contradiction",
                severity=M.SEV_HIGH,
                message=(
                    f"success claim at step {contradiction.get('claim_step')} contradicted by a "
                    f"recurring error at step {contradiction.get('error_step')}: "
                    f"{str(contradiction.get('claim'))[:160]}"
                ),
                step=int(contradiction.get("error_step") or 0),
                fingerprint=str(contradiction.get("fingerprint") or ""),
                evidence=(
                    _event_ref(trajectory.db_path, trajectory.session_id,
                               int(contradiction.get("claim_step") or 0), "claim"),
                    _event_ref(trajectory.db_path, trajectory.session_id,
                               int(contradiction.get("error_step") or 0), "tool_result"),
                ),
                refs=dict(contradiction),
            )
        )
    return findings, facts


def _verification_findings(trajectory: Trajectory, health, *, window: int) -> list[Finding]:
    """Edits happened, but nothing verified them — the deterministic reading.

    The per-edit version of this signal is the sequence analyzer's
    ``change_without_verification``; this one states the session-level fact (the
    last change has no verification behind it) so a report cannot look greener
    than the trajectory.
    """
    if health.edits <= 0 or health.verified_after_change:
        return []
    last_edit = 0
    for event in trajectory.events:
        if event.kind == "file_edit":
            last_edit = max(last_edit, int(event.step))
    if not last_edit:
        return []
    return [
        Finding(
            category=CAT_VERIFY,
            kind="no_verification_after_change",
            severity=M.SEV_WARN,
            message=(
                f"{health.edits} edit(s) in the last {window} steps, but no verification ran "
                f"after the last change (step {last_edit}) — the change is unverified"
            ),
            step=last_edit,
            fingerprint=M.error_fingerprint(f"unverified:{last_edit}"),
            evidence=(_event_ref(trajectory.db_path, trajectory.session_id, last_edit,
                                 "file_edit"),),
            refs={"last_edit_step": last_edit, **health.as_refs()},
        )
    ]


def check(
    *,
    runtime: str | Path | None = None,
    events_db: str | Path | None = None,
    session_id: str = "",
    step: int = 0,
    window: int = DEFAULT_WINDOW,
    goal: str = "",
    pact: Pact | None = None,
    goal_from_contract: bool = True,
    incidents: bool = True,
    limit: int = 200,
) -> dict:
    """One pass over the trajectory: findings, facts and the evidence refs.

    The pass is deterministic and read-only. Everything it reports comes from a
    detector that the running supervisor already uses, replayed over the stored
    events; a category with no finding is reported as empty instead of being
    filled with a plausible-sounding observation.

    ``limit`` bounds how many findings the *payload* carries (``0`` = all) —
    ``findings_count`` and ``counts`` always describe the full pass, so a capped
    list can never read as a smaller problem.
    """
    db_path, root = resolve_root(runtime, events_db)
    selected, how = resolve_session(db_path, session_id)
    if not selected:
        return {
            "checked": False,
            "blocked_reason": f"no session to check in {db_path}",
            "session_id": "",
            "session_selected": how,
            "events_db": str(db_path),
            "events_db_exists": db_path.is_file(),
            "sessions": list_sessions(db_path),
            "findings": [],
            "findings_returned": 0,
            "findings_truncated": False,
            "counts": {category: 0 for category in CATEGORIES},
        }
    trajectory = read_trajectory(db_path, selected, until_step=int(step or 0))
    if not trajectory.exists:
        return {
            "checked": False,
            "blocked_reason": trajectory.reason,
            "session_id": selected,
            "session_selected": how,
            "events_db": str(db_path),
            "events_db_exists": db_path.is_file(),
            "sessions": list_sessions(db_path),
            "findings": [],
            "findings_returned": 0,
            "findings_truncated": False,
            "counts": {category: 0 for category in CATEGORIES},
        }

    goal_text, goal_source = _recorded_goal(root, selected, goal)
    known: set[str] = set()
    if incidents:
        known = _incident_signatures(root)

    monitor_findings, monitor_raw = _monitor_findings(trajectory, pact, known)
    pattern_findings, pattern_raw = _pattern_findings(trajectory)
    # drift needs a goal to judge against: the explicit one, or the recorded one
    drift_goal = goal_text if (goal.strip() or goal_from_contract) else ""
    if not drift_goal:
        goal_text, goal_source = "", "disabled" if goal_text else goal_source
    drift_findings, drift_facts = _drift_findings(trajectory, drift_goal)
    guard_findings, guard_facts = _guard_findings(trajectory)

    health = read_trajectory_health(trajectory.events, until_step=trajectory.last_step,
                                    window=max(1, int(window)))
    verify_findings = _verification_findings(trajectory, health, window=max(1, int(window)))

    # Block-4 interceptions are their own event kind (the plugin's pre-execution
    # gate); they are stored facts, not a signal to re-derive.
    intercept_findings = _interception_findings(trajectory)

    findings = sorted(
        [
            *monitor_findings,
            *pattern_findings,
            *drift_findings,
            *guard_findings,
            *verify_findings,
            *intercept_findings,
        ],
        key=lambda item: (int(item.step), -int(item.severity), item.category, item.kind),
    )
    counts = {category: 0 for category in CATEGORIES}
    for finding in findings:
        counts[finding.category] = counts.get(finding.category, 0) + 1
    highest = max((finding.severity for finding in findings), default=0)
    verdict = "clean" if not findings else ("attention" if highest >= M.SEV_HIGH else "warn")
    capped = int(limit or 0)
    returned = findings[:capped] if capped > 0 else findings

    return {
        "checked": True,
        "blocked_reason": "",
        "session_id": selected,
        "session_selected": how,
        "events_db": str(db_path),
        "events_db_exists": True,
        "trajectory": trajectory.as_dict(),
        "until_step": int(step or trajectory.last_step),
        "window": max(1, int(window)),
        "goal": {"text": goal_text, "source": goal_source},
        "findings": [finding.as_dict() for finding in returned],
        "findings_returned": len(returned),
        "findings_truncated": len(returned) < len(findings),
        "counts": counts,
        "findings_count": len(findings),
        "highest_severity": int(highest),
        "highest_severity_name": severity_name(highest) if findings else "",
        "verdict": verdict,
        "categories": {
            category: {
                "label": CATEGORY_LABELS[category],
                "count": counts.get(category, 0),
                "trigger_kind": CATEGORY_TRIGGER[category],
            }
            for category in CATEGORIES
        },
        "facts": {
            "health": health.as_refs(),
            "health_reason": health.reason(),
            "monitor_signals": len(monitor_raw),
            "monitor_thresholds": {
                "loop_repeat": M.Monitor().loop_repeat,
                "loop_window": M.Monitor().loop_window,
                "error_repeat": M.Monitor().error_repeat,
                "error_window": M.Monitor().error_window,
                "stall_after": M.Monitor().stall_after,
            },
            "pattern_hits": len(pattern_raw),
            "drift": drift_facts,
            "guard": guard_facts,
            "known_incident_signatures": len(known),
            "pact": bool(pact),
            "signals": (monitor_raw + pattern_raw)[:200],
        },
    }


def _recorded_goal(runtime: str | Path | None, session_id: str, explicit: str) -> tuple[str, str]:
    """The goal a drift reading is judged against: ``--goal``, the contract, the chat."""
    if explicit.strip():
        return explicit.strip(), "argument"
    root = Path(runtime or default_runtime())
    try:
        from .completion import CompletionStore

        state = CompletionStore(root, session_id).state()
        goal = str((state or {}).get("goal") or "").strip()
        if goal:
            return goal, "completion-contract"
    except Exception:  # noqa: BLE001 — a missing contract is not an error here
        pass
    for event in reversed(read_trajectory(events_path(runtime), session_id).events):
        if event.kind == "goal_set_from_chat":
            goal = str(event.payload.get("goal") or "").strip()
            if goal:
                return goal, "goal_set_from_chat"
    return "", "none"


def _incident_signatures(runtime: str | Path | None) -> set[str]:
    """The known-incident fingerprints, read the same way the plugin reads them."""
    path = incidents_path(runtime)
    if not path.is_file():
        return set()
    try:
        from .incidents import IncidentStore

        store = IncidentStore(path)
        try:
            return set(store.intervention_signatures())
        finally:
            store.close()
    except Exception:  # noqa: BLE001 — without them the check just cannot match incidents
        return set()


def _interception_findings(trajectory: Trajectory) -> list[Finding]:
    findings: list[Finding] = []
    for event in trajectory.events:
        if event.kind != "tool_call_intercepted":
            continue
        classification = str(event.payload.get("classification") or "")
        if classification == "ALLOW":
            continue
        reasons = [str(item) for item in (event.payload.get("reasons") or [])]
        findings.append(
            Finding(
                category=CAT_TOOLUSE,
                kind="tool_call_intercepted",
                severity=M.SEV_HIGH if classification == "BLOCK" else M.SEV_WARN,
                message=(
                    f"tool call intercepted before execution ({classification}): "
                    + "; ".join(reasons or [str(event.payload.get('directive') or '')])
                )[:400],
                step=int(event.step),
                fingerprint=M.error_fingerprint(
                    f"intercepted:{classification}:{event.payload.get('tool')}"
                ),
                evidence=(_event_ref(trajectory.db_path, trajectory.session_id,
                                     event.step, "tool_call_intercepted"),),
                refs={"classification": classification, "reasons": reasons,
                      "tool": str(event.payload.get("tool") or ""),
                      "directive": str(event.payload.get("directive") or "")},
            )
        )
    return findings


# --------------------------------------------------------------------------- #
# triage — the Jev pre-filter                                                   #
# --------------------------------------------------------------------------- #


def _responses_after(trajectory: Trajectory, step: int) -> dict:
    """What the agent did *after* a finding's step: edit / green run / statement."""
    edits: list[int] = []
    greens: list[int] = []
    statements: list[int] = []
    for event in trajectory.events:
        if int(event.step) <= int(step):
            continue
        if event.kind == "file_edit":
            edits.append(int(event.step))
        elif event.kind == "tool_result" and not M.error_text(event.payload):
            greens.append(int(event.step))
        elif event.kind in ("claim", "guard_check", "thought"):
            if str(event.payload.get("text") or event.payload.get("snippet") or "").strip():
                statements.append(int(event.step))
    return {
        "edit_at": min(edits, default=0),
        "green_at": min(greens, default=0),
        "statement_at": min(statements, default=0),
    }


def _handled_reading(trajectory: Trajectory, report: dict, *, window: int) -> dict:
    """Is the agent already recognising and handling the findings?

    Two independent pieces of evidence, both deterministic:

    * :class:`skaldir.voice.TrajectoryHealth` for the window — the same reading the
      live pause uses to close an investigation as "no intervention needed";
    * a concrete response *after* the finding's step (an edit, a green run, a
      statement) for at least one finding inside the window.

    Both are required: health alone could describe an unrelated debugging cycle,
    a response alone could be a coincidence.
    """
    health_refs = dict(report.get("facts", {}).get("health") or {})
    healthy = bool(health_refs.get("markers", 0)) and int(health_refs.get("markers", 0)) >= 3 \
        and int(health_refs.get("new_evidence", 0)) >= 1
    cutoff = max(0, trajectory.last_step - max(1, int(window)))
    responses: list[dict] = []
    for finding in report.get("findings", []):
        step = int(finding.get("step") or 0)
        if step < cutoff:
            continue
        response = _responses_after(trajectory, step)
        if response["edit_at"] or response["green_at"]:
            responses.append({"kind": finding.get("kind"), "category": finding.get("category"),
                              "step": step, **response})
    return {
        "already_working": bool(healthy and responses),
        "healthy": healthy,
        "health_reason": report.get("facts", {}).get("health_reason", ""),
        "markers": health_refs,
        "responses": responses,
        "evidence": (
            [f"health: {key}={value}" for key, value in health_refs.items()]
            + [
                f"response after step {item['step']}: edit@{item['edit_at'] or '-'} "
                f"green@{item['green_at'] or '-'}"
                for item in responses[:4]
            ]
        ),
    }


def _interruptions(state_dir: str | Path | None, session_id: str) -> dict:
    """How often this session was really interrupted (durable, not in-memory)."""
    path = Path(state_dir or default_runtime()) / "pauses.db"
    if not path.is_file():
        return {"count": 0, "delivered": 0, "source": "no pause store"}
    try:
        store = P.PauseStore(path)
    except Exception as exc:  # noqa: BLE001 — a broken store is not a triage verdict
        return {"count": 0, "delivered": 0, "source": f"pause store unreadable: {exc}"}
    try:
        history = store.history_for_session(str(session_id))
        current = store.current(str(session_id))
    finally:
        store.close()
    delivered = sum(1 for row in history if str(row.get("status")) == P.STATUS_DELIVERED)
    return {
        "count": len(history),
        "delivered": delivered,
        "open_lease": (current or {}).get("id", ""),
        "triggers": _count_triggers(history),
        "source": str(path),
    }


def _count_triggers(history: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for row in history:
        key = str(row.get("trigger_kind") or "")
        if key:
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _triage_decision(report: dict, handled: dict, interruptions: dict) -> dict:
    """The pre-filter itself: wake, defer, or stay silent — and why.

    Ordered by the owner's rule: an unhandled finding that the agent has *not*
    recognised is the only thing that escalates. Everything else stays quiet —
    which is a decision, not an omission.
    """
    findings = report.get("findings") or []
    if not report.get("checked"):
        return {
            "decision": "stay_silent",
            "wake": False,
            "reason_code": "nothing_to_check",
            "reason": str(report.get("blocked_reason") or "no trajectory"),
        }
    if not findings:
        return {
            "decision": "stay_silent",
            "wake": False,
            "reason_code": "no_finding",
            "reason": "the pass raised no finding — there is nothing to wake the Voice for",
        }
    if handled.get("already_working"):
        return {
            "decision": "stay_silent",
            "wake": False,
            "reason_code": "already_recognised_and_working",
            "reason": (
                "the agent has already recognised the problem and is working on it — "
                + str(handled.get("health_reason") or "evidence-backed debugging")
            ),
        }
    if int(interruptions.get("count") or 0) >= 2:
        return {
            "decision": "defer",
            "wake": False,
            "reason_code": "repeated_interventions",
            "reason": (
                f"this session was already paused {interruptions['count']}× — another "
                "interruption has to be earned, so the reading is recorded, not delivered"
            ),
        }
    categories = sorted({
        str(item.get("category")) for item in findings
        if int(item.get("severity") or 0) >= M.SEV_WARN and category_wakes(item)
    })
    if not categories:
        return {
            "decision": "stay_silent",
            "wake": False,
            "reason_code": "advisory_only",
            "reason": (
                "the findings are advisory signals only (no wake-worthy category) — "
                "they stay in the report"
            ),
        }
    return {
        "decision": "wake_voice",
        "wake": True,
        "reason_code": "unhandled_finding",
        "reason": (
            "no evidence that the agent recognised the finding, and it is not a case of "
            "normal recoverable debugging: " + ", ".join(categories)
        ),
    }


def category_wakes(finding: dict) -> bool:
    """Does this finding carry a pause trigger in the live policy?

    The cheapest honest answer: ask the same table the live controller escalates
    with (:data:`skaldir.pause.TRIGGER_RULES`) — a kind that never escalates there
    must not escalate in the pre-filter either. :data:`WAKE_ALWAYS` names the
    handful of SUPERVISE findings the live table does not carry a threshold for
    (a false completion claim must not be silenced by a missing table row).
    """
    kind = str(finding.get("kind") or "")
    if kind in WAKE_ALWAYS:
        return True
    if kind in P.TRIGGER_RULES:
        return True
    pattern = str(finding.get("refs", {}).get("pattern") or "")
    return bool(pattern) and f"pattern_{pattern}" in P.TRIGGER_RULES


def triage(
    *,
    runtime: str | Path | None = None,
    events_db: str | Path | None = None,
    session_id: str = "",
    step: int = 0,
    window: int = DEFAULT_WINDOW,
    goal: str = "",
    pact: Pact | None = None,
    jev: bool = True,
    provider: str = "",
    interruptions_so_far: int | None = None,
    limit: int = 200,
    record: bool = False,
) -> dict:
    """Should the Voice be woken at all? The deterministic pre-filter + Jev.

    The deterministic decision is computed first and is **never** replaced by the
    Jev reading: ``voice_triage`` is the substrate's ``voice_triage_v1`` pack,
    reached through :func:`skaldir.jev.decide` as the façade, and its answer is
    recorded as an advisory (``authority: advisory``). When Jev has no provider
    it answers ``defer`` (its neutral reading) — the report says so plainly, and
    the caller's own decision stands, exactly as
    :func:`skaldir.voice.triage_with_system_one` keeps the deterministic hint.
    """
    # ``limit=0``: the decision must rest on the *full* pass — a capped finding
    # list would let a response that lies beyond the cap look like no response.
    # The payload below exposes counts, never the raw list, so nothing is hidden.
    report = check(runtime=runtime, events_db=events_db, session_id=session_id, step=step,
                   window=window, goal=goal, pact=pact, limit=0)
    db_path, root = resolve_root(runtime, events_db)
    selected = str(report.get("session_id") or "")
    trajectory = read_trajectory(db_path, selected, until_step=int(step or 0)) if selected \
        else read_trajectory(db_path, "")
    handled = _handled_reading(trajectory, report, window=window) if report.get("checked") \
        else {"already_working": False, "healthy": False, "evidence": []}
    interruptions = _interruptions(root, selected)
    if interruptions_so_far is not None:
        interruptions = {**interruptions, "count": int(interruptions_so_far),
                         "source": f"{interruptions.get('source')} (count overridden)"}
    decision = _triage_decision(report, handled, interruptions)

    advisory: dict | None = None
    if jev and report.get("checked"):
        advisory = _jev_advisory(report, trajectory, handled, interruptions, decision,
                                 provider=provider, runtime=root, record=record)

    return {
        "checked": bool(report.get("checked")),
        "blocked_reason": report.get("blocked_reason", ""),
        "session_id": selected,
        "session_selected": report.get("session_selected", ""),
        "step": int(step or 0),
        "window": int(window),
        "decision": decision["decision"],
        "wake": bool(decision["wake"]),
        "reason_code": decision["reason_code"],
        "reason": decision["reason"],
        "intervene": bool(decision["wake"]),
        "handled": handled,
        "interruptions": interruptions,
        "findings_count": int(report.get("findings_count") or 0),
        "counts": report.get("counts") or {},
        "highest_severity": int(report.get("highest_severity") or 0),
        "verdict": report.get("verdict", ""),
        "jev": advisory,
        "advisory_applied": False,
        "note": (
            "the deterministic pre-filter decides; the Jev reading is recorded as an "
            "advisory and never replaces it"
        ),
        "evidence": _triage_evidence(report, handled, interruptions, decision),
    }


def _triage_evidence(report: dict, handled: dict, interruptions: dict, decision: dict) -> list[str]:
    evidence: list[str] = []
    for item in (handled.get("evidence") or [])[:6]:
        evidence.append(str(item))
    for finding in (report.get("findings") or [])[:4]:
        evidence.extend(str(ref) for ref in finding.get("evidence") or ())
    count = int(interruptions.get("count") or 0)
    if count:
        evidence.append(
            f"{count} recorded pause(s) for this session ({interruptions.get('source')})"
        )
    evidence.append(f"decision {decision['decision']} ({decision['reason_code']})")
    return evidence


def _jev_evidence_refs(report: dict, trajectory: Trajectory) -> tuple[str, ...]:
    refs: list[str] = []
    for finding in (report.get("findings") or [])[:6]:
        refs.extend(str(ref) for ref in finding.get("evidence") or ())
    if not refs and trajectory.exists:
        refs.append(_event_ref(trajectory.db_path, trajectory.session_id,
                               trajectory.last_step, "trajectory"))
    return tuple(refs)


def _trajectory_window(trajectory: Trajectory, *, limit: int = 12) -> tuple[Any, ...]:
    """The last few steps as the pack's ``TrajectoryItem`` list (no new fields)."""
    from .jev import contracts as JC

    items = []
    for event in trajectory.events[-max(1, int(limit)):]:
        if event.kind not in ("tool_call", "tool_result", "file_edit", "claim"):
            continue
        name = str(event.payload.get("name") or event.payload.get("tool") or event.kind)
        error = M.error_text(event.payload) if event.kind == "tool_result" else ""
        items.append(
            JC.TrajectoryItem(
                action=name[:120],
                kind=event.kind,
                error_class=(M.classify_tool_error(error) or "") if error else "",
                hash=M.error_fingerprint(f"{event.kind}:{name}:{event.step}"),
            )
        )
    return tuple(items)


def _configured_provider(runtime: str | Path | None, *, explicit: str = "") -> str:
    """The provider this call should use — the explicit one, else the runtime's own.

    The defect this closes (MVP/78-jev-live.md §2.4): ``voice triage`` and ``voice explain``
    passed ``provider=str(getattr(args, "provider", "") or "")``, i.e. the *verb's* default
    ``""``, so the advisory was built with ``SystemOneFlags(provider=PROVIDER_DISABLED)`` and
    reported ``blocked / provider_disabled`` **after** ``system_one.voice_triage`` had been
    switched on. The advisory stayed opt-in at the verb while the settings said it was armed.

    Now the settings decide, and ``--provider`` is what it always was: an override. A runtime
    whose settings name no provider still answers ``disabled`` — never a silent fallback.
    """
    name = str(explicit or "").strip()
    if name:
        return name
    from .system_one import PROVIDER_DISABLED
    from .system_one_flags import load_system_one_flags

    root = Path(runtime) if runtime else default_runtime()
    return str(load_system_one_flags(root).provider or PROVIDER_DISABLED)


def _jev_advisory(report: dict, trajectory: Trajectory, handled: dict, interruptions: dict,
                  decision: dict, *, provider: str = "", runtime: str | Path | None = None,
                  record: bool = False) -> dict:
    """Ask the Jev façade for the ``voice_triage`` reading — record it, never obey it.

    ``record`` is the subscription switch and it defaults to ``False`` on purpose: the
    inspection verbs (``voice check``/``triage``/``explain``) are read-only over the store they
    read, which ``tests/test_voice_service.py::test_does_not_write_into_the_store`` pins. With
    ``record=True`` the advisory is written into that runtime's event store as a
    ``jev_decision`` row — the same subscription the runtime paths use, asked for explicitly
    (``skaldir voice triage --record-advisory``).
    """
    from .jev import contracts as JC
    from .jev import decide
    from .jev import wiring as jev_wiring
    from .jev.decide import provider_attempted
    from .system_one import PROVIDER_DISABLED
    from .system_one_flags import SystemOneFlags

    findings = report.get("findings") or []
    top = max(findings, key=lambda item: (int(item.get("severity") or 0),
                                          -int(item.get("step") or 0))) if findings else {}
    category = str(top.get("category") or "")
    trigger_kind = CATEGORY_TRIGGER.get(category, "*") if category else "*"
    signals: dict[str, Any] = {
        f"finding_{category or 'none'}": True,
        "findings": int(report.get("findings_count") or 0),
        "highest_severity": int(report.get("highest_severity") or 0),
        "verdict": str(report.get("verdict") or ""),
    }
    for name, value in (handled.get("markers") or {}).items():
        if isinstance(value, (bool, int, float, str)):
            signals[f"health_{name}"] = value
    request = JC.JevVoiceTriageRequest(
        run_id=f"voice-check:{trajectory.session_id}",
        goal_id=str((report.get("goal") or {}).get("text") or "")[:120] or "unrecorded-goal",
        trigger_kind=trigger_kind,  # type: ignore[arg-type]  # from CATEGORY_TRIGGER, itself typed
        signals=signals,
        step=int(trajectory.last_step),
        trajectory_window=_trajectory_window(trajectory),
        deterministic_hint=str(decision.get("reason") or "")[:400],
        agent_on_sensible_path=bool(handled.get("already_working")),
        interruptions_so_far=int(interruptions.get("count") or 0),
        evidence_refs=_jev_evidence_refs(report, trajectory),
        caution_baseline=0,
    )
    flags = SystemOneFlags(
        enabled=True,
        provider=str(_configured_provider(runtime, explicit=provider) or PROVIDER_DISABLED),
        shadow_mode=False,
        voice_triage=True,
    )
    # The subscription: a real bus when the caller asked for one, else an honest NullBus that
    # writes nothing and says why (the read-only inspection verbs stay read-only). `create=False`
    # on the real path too: asking "would the Voice wake?" must not bring a store into existence.
    bus = (
        jev_wiring.bus_for(
            runtime,
            session_id=str(trajectory.session_id or report.get("session_id") or "voice-triage"),
            step=int(trajectory.last_step or 0),
            source="voice_service.triage",
            create=False,
        )
        if record
        else jev_wiring.NullBus(
            "the read-only inspection verb does not write into the store it reads — pass "
            "record=True (skaldir voice triage/explain --record-advisory) to subscribe"
        )
    )
    try:
        result = decide(request, flags=flags, emit=bus.emit)
    except Exception as exc:  # noqa: BLE001 — a Jev problem is reported, never invented
        bus.close()
        return {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            # Unknown, not "disabled": an exception can land after a real attempt,
            # so the split is only stated when the decision itself states it.
            "attempted": None,
            "refused": True,
            "mode": "unknown",
            "provider_mode": "",
            "note": "the Jev advisory could not be produced; the deterministic decision stands",
        }
    document = result.model_dump(mode="json")
    # the emit above already published this decision through the bus when it is real; the
    # call below is the fallback for a bus that was not the emitter, and is deduped when the
    # row exists — one decision, one row.
    recorded = bus.decision(
        document, source="voice_service.triage", step=int(trajectory.last_step or 0)
    )
    events_db = str(getattr(bus, "path", "") or "")
    bus.close()
    answer = document.get("answer") or {}
    status = str(document.get("status") or "")
    # -- the audit 63/V2 split ------------------------------------------------ #
    # `provider_mode` is the §4.2 *contract* label and stays exactly as Jev computed
    # it (`disabled` = "no evaluation was produced"). It never said whether a call
    # went out, so `attempted`/`refused`/`mode` say it in full: a real outbound
    # attempt that failed (HTTP 402 Insufficient Balance) is `attempted: true`,
    # `refused: true` with its reason, `mode: attempted_failed` — only a decision
    # where nothing was ever tried reads `attempted: false` + `mode: disabled`.
    attempted = provider_attempted(document)
    refused = status in ("blocked", "refused")
    mode = (
        "disabled"
        if not attempted
        else ("attempted_failed" if status != "ok" else str(document.get("provider_mode") or ""))
    )
    return {
        "decision_id": document.get("decision_id", ""),
        "status": status,
        "provider_mode": document.get("provider_mode", ""),
        "attempted": attempted,
        "refused": refused,
        "mode": mode,
        "provider": str(flags.provider),
        "live_jev": bool(document.get("live_jev")),
        # MVP/87: where this advisory was recorded, and whether the store took it. The
        # subscription is part of the advisory the caller reads, so "was Jev armed?" is
        # answered by the record rather than by asking the operator to grep.
        "recorded": bool(recorded),
        "subscribed": bool(record),
        "subscription": str(getattr(bus, "reason", "") or "the runtime event store"),
        "events_db": events_db,
        "question_pack_id": document.get("question_pack_id", ""),
        "question_pack_version": document.get("question_pack_version", ""),
        "caution_level": int(document.get("caution_level") or 0),
        "confidence": float(document.get("confidence") or 0.0),
        "authority": document.get("authority", "advisory"),
        "chosen": str(answer.get("chosen") or ""),
        "reason_code": str(answer.get("reason_code") or ""),
        "already_handled": bool(answer.get("already_handled")),
        "on_sensible_path": bool(answer.get("on_sensible_path")),
        "route_hint": str(answer.get("route_hint") or ""),
        "refusal_reason": document.get("refusal_reason", ""),
        "fallback": document.get("fallback", ""),
        "agrees_with_decision": str(answer.get("chosen") or "") == str(decision.get("decision")),
        "note": (
            "advisory only: the substrate's voice_triage_v1 pack through the Jev façade — "
            "it never replaces the deterministic decision"
        ),
    }


# --------------------------------------------------------------------------- #
# explain                                                                       #
# --------------------------------------------------------------------------- #


def _strictness_shift(level: str) -> int:
    """The same shift the live controller applies (one table, from ``pause``)."""
    return int(getattr(P, "_STRICTNESS_DELTA", {}).get(str(level), 0))


def explain(
    *,
    runtime: str | Path | None = None,
    events_db: str | Path | None = None,
    session_id: str = "",
    step: int = 0,
    window: int = DEFAULT_WINDOW,
    goal: str = "",
    pact: Pact | None = None,
    jev: bool = True,
    provider: str = "",
    limit: int = 200,
    record: bool = False,
) -> dict:
    """Why the supervisor would — or would not — intervene, with the whole chain.

    Three deterministic gates, in the order the live path applies them:

    1. **Does a finding carry a pause trigger?** (:data:`skaldir.pause.TRIGGER_RULES`,
       shifted by the session's strictness.)
    2. **Would the pause be worth its cost?** (:func:`skaldir.voice.assess_intervention`
       — measured occurrences/severity against the estimated token costs.)
    3. **Is the agent already handling it?** (:func:`_handled_reading` — the same
       evidence the live pause uses to close as "no intervention needed".)

    The answer states plainly which gate stopped it. A gate that stopped an
    intervention is a *result*, not a failure.
    """
    trajectory_report = triage(
        runtime=runtime, events_db=events_db, session_id=session_id, step=step,
        window=window, goal=goal, pact=pact, jev=jev, provider=provider, limit=limit,
        record=record,
    )
    _, root = resolve_root(runtime, events_db)
    selected = str(trajectory_report.get("session_id") or "")
    # full pass for the chain; the grouped trigger list is what gets capped
    report = check(runtime=runtime, events_db=events_db, session_id=selected, step=step,
                   window=window, goal=goal, pact=pact, limit=0)
    level = P.load_strictness(root)
    shift = _strictness_shift(level)
    handled = trajectory_report.get("handled") or {}
    interruptions = trajectory_report.get("interruptions") or {}

    # One entry per trigger *class* (kind + fingerprint): a report that repeats
    # the same trigger 40× describes one problem, not forty.
    grouped: dict[tuple[str, str], dict] = {}
    for finding in report.get("findings") or []:
        kind = str(finding.get("kind") or "")
        fingerprint = str(finding.get("fingerprint") or "")
        key = (kind, fingerprint)
        entry = grouped.get(key)
        if entry is None:
            base = P.TRIGGER_RULES.get(kind)
            if base is None:
                pattern = str(finding.get("refs", {}).get("pattern") or "")
                base = P.TRIGGER_RULES.get(f"pattern_{pattern}") if pattern else None
            entry = {
                "kind": kind,
                "category": finding.get("category"),
                "first_step": int(finding.get("step") or 0),
                "severity": int(finding.get("severity") or 0),
                "severity_name": finding.get("severity_name"),
                "has_pause_trigger": base is not None,
                "threshold": max(1, int(base) + shift) if base is not None else 0,
                "occurrences": 0,
                "message": finding.get("message"),
            }
            grouped[key] = entry
        entry["occurrences"] += 1
        entry["severity"] = max(entry["severity"], int(finding.get("severity") or 0))

    triggers: list[dict] = []
    for entry in sorted(grouped.values(), key=lambda item: item["first_step"]):
        entry["reaches_threshold"] = bool(
            entry["has_pause_trigger"] and entry["occurrences"] >= entry["threshold"]
        )
        if entry["has_pause_trigger"]:
            gate = assess_intervention(
                trigger_kind=entry["kind"],
                severity=int(entry["severity"]),
                occurrences=int(entry["occurrences"]),
                strictness=level,
            )
            entry["cost_gate"] = {
                "intervene": bool(gate.intervene),
                "measured": gate.measured,
                "estimated": gate.estimated,
                "continuation_tokens": gate.continuation_tokens,
                "intervention_tokens": gate.intervention_tokens,
            }
        else:
            entry["cost_gate"] = None
        triggers.append(entry)

    reasons: list[dict] = []

    def _reason(code: str, detail: str) -> None:
        """One entry per (code, detail): the same gate must not be listed twice."""
        if not any(item["code"] == code and item["detail"] == detail for item in reasons):
            reasons.append({"code": code, "detail": detail})

    decision = str(trajectory_report.get("decision") or "")
    if not report.get("checked"):
        _reason("nothing_to_check", str(report.get("blocked_reason")))
    elif decision == "wake_voice":
        _reason("unhandled_finding", str(trajectory_report.get("reason") or ""))
    elif decision == "stay_silent" and trajectory_report.get("reason_code") == \
            "already_recognised_and_working":
        _reason(
            "agent_already_on_it",
            "the agent has already recognised the problem and is working on it "
            "sensibly — the Voice must not interfere",
        )
    else:
        _reason(str(trajectory_report.get("reason_code") or ""),
                str(trajectory_report.get("reason") or ""))
    for trigger in triggers:
        if trigger["has_pause_trigger"] and not trigger["reaches_threshold"]:
            _reason(
                "below_threshold",
                f"{trigger['kind']} seen {trigger['occurrences']}× — the pause policy "
                f"needs {trigger['threshold']}× (strictness {level})",
            )
        if trigger["cost_gate"] is not None and not trigger["cost_gate"]["intervene"]:
            _reason(
                "cost_gate",
                f"{trigger['kind']}: continuing is estimated at "
                f"{trigger['cost_gate']['continuation_tokens']} tokens vs "
                f"{trigger['cost_gate']['intervention_tokens']} for the pause — not worth it",
            )
        if not trigger["has_pause_trigger"]:
            _reason(
                "no_pause_trigger",
                f"{trigger['kind']} carries no pause threshold in the live policy — it "
                "stays a finding in the report",
            )
    if int(interruptions.get("count") or 0) >= 2 and decision != "wake_voice":
        _reason("interruption_budget",
                f"{interruptions['count']} pause(s) already recorded for this session")
    if handled.get("already_working"):
        _reason("health_evidence", str(handled.get("health_reason") or ""))

    capped = int(limit or 0)
    exposed = triggers[:capped] if capped > 0 else triggers

    return {
        "checked": bool(report.get("checked")),
        "blocked_reason": report.get("blocked_reason", ""),
        "session_id": selected,
        "step": int(step or 0),
        "would_intervene": bool(trajectory_report.get("wake")),
        "decision": decision,
        "reason_code": trajectory_report.get("reason_code", ""),
        "statement": _statement(decision, handled),
        "reasons": reasons,
        "triggers": exposed,
        "triggers_count": len(triggers),
        "triggers_truncated": len(exposed) < len(triggers),
        "gate": {
            "strictness": level,
            "threshold_shift": shift,
            "trigger_rules": dict(P.TRIGGER_RULES),
            **pause_defaults(),
            "interruptions": interruptions,
            "open_lease": interruptions.get("open_lease", ""),
        },
        "handled": handled,
        "findings_count": int(report.get("findings_count") or 0),
        "counts": report.get("counts") or {},
        "verdict": report.get("verdict", ""),
        "jev": trajectory_report.get("jev"),
        "advisory_applied": False,
    }


def _statement(decision: str, handled: dict) -> str:
    if decision == "wake_voice":
        return (
            "The Voice would intervene: the trajectory shows a finding the agent has not "
            "recognised, and it is not normal recoverable debugging."
        )
    if decision == "defer":
        return (
            "The Voice would not intervene now: the session has already been interrupted "
            "often enough that another pause has to be earned. The reading is recorded."
        )
    if handled.get("already_working"):
        return (
            "The Voice would NOT intervene: the agent already sees the problem and is "
            "working on it sensibly — " + str(handled.get("health_reason") or "")
        )
    return (
        "The Voice would NOT intervene: there is nothing to interrupt for "
        "(no finding, or only advisory signals)."
    )


# --------------------------------------------------------------------------- #
# status                                                                        #
# --------------------------------------------------------------------------- #


def _pause_snapshot(runtime: str | Path | None, *, limit: int = 20) -> dict:
    path = pauses_path(runtime)
    snapshot: dict = {
        "path": str(path),
        "exists": path.is_file(),
        "leases": 0,
        "statuses": {},
        "triggers": {},
        "sessions": {},
        "open": [],
        "strictness": P.load_strictness(runtime or default_runtime()),
    }
    if not path.is_file():
        return snapshot
    # Read-only aggregate over the very table ``PauseStore`` owns: the writes stay
    # in the store, a status snapshot never opens one.
    connection = _read_only(path)
    if connection is None:
        snapshot["error"] = f"pause store {path} is not readable"
        return snapshot
    try:
        rows = connection.execute(
            "SELECT id, kind, session_id, trigger_kind, status, base_step, created "
            "FROM pauses ORDER BY created DESC"
        ).fetchall()
    except sqlite3.Error as exc:
        snapshot["error"] = f"{type(exc).__name__}: {exc}"
        return snapshot
    finally:
        connection.close()
    statuses: dict[str, int] = {}
    triggers: dict[str, int] = {}
    sessions: dict[str, int] = {}
    open_leases: list[dict] = []
    for row in rows:
        lease_id, kind, session_id, trigger_kind, status, base_step, created = row
        statuses[str(status)] = statuses.get(str(status), 0) + 1
        triggers[str(trigger_kind)] = triggers.get(str(trigger_kind), 0) + 1
        sessions[str(session_id)] = sessions.get(str(session_id), 0) + 1
        if str(status) not in P.TERMINAL and len(open_leases) < limit:
            open_leases.append({
                "id": str(lease_id),
                "kind": str(kind),
                "session_id": str(session_id),
                "trigger_kind": str(trigger_kind),
                "status": str(status),
                "base_step": int(base_step or 0),
                "created": float(created or 0.0),
            })
    snapshot.update({
        "leases": len(rows),
        "statuses": dict(sorted(statuses.items())),
        "triggers": dict(sorted(triggers.items())),
        "sessions": dict(sorted(sessions.items())),
        "open": open_leases,
    })
    return snapshot


def _runs_snapshot(runtime: str | Path | None) -> dict:
    path = runs_path(runtime)
    snapshot: dict = {"path": str(path), "exists": path.is_file(), "runs": 0}
    if not path.is_file():
        return snapshot
    try:
        from .run_supervisor import RunStore, describe

        store = RunStore(path)
        try:
            described = describe(store)
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001
        snapshot["error"] = f"{type(exc).__name__}: {exc}"
        return snapshot
    snapshot.update({
        "invariant": described.get("invariant", ""),
        "runs": len(described.get("runs") or []),
        "non_terminal": sum(
            1 for run in (described.get("runs") or []) if str(run.get("state")) not in
            ("COMPLETE", "FAILED", "CANCELLED", "ABANDONED")
        ),
        "violations": described.get("violations") or [],
        "protected": described.get("protected") or [],
        "ok": bool(described.get("ok")),
    })
    return snapshot


def status(
    *,
    runtime: str | Path | None = None,
    limit: int = 20,
) -> dict:
    """What the supervisor is watching: sessions, leases, runs and the policy."""
    from .system_one_flags import load_system_one_flags

    root = Path(runtime or default_runtime())
    db_path = events_path(root)
    sessions = list_sessions(db_path)
    pauses = _pause_snapshot(root, limit=limit)
    runs = _runs_snapshot(root)
    flags = load_system_one_flags(root)
    monitor = M.Monitor()
    return {
        "runtime": str(root),
        "runtime_exists": root.is_dir(),
        "events": {
            "path": str(db_path),
            "exists": db_path.is_file(),
            "events": sum(item["events"] for item in sessions),
            "sessions": len(sessions),
            "list": sessions[-max(1, int(limit)):],
        },
        "pauses": pauses,
        "runs": runs,
        "watched": {
            "categories": list(CATEGORIES),
            "monitor": {
                "loop_repeat": monitor.loop_repeat,
                "loop_window": monitor.loop_window,
                "error_repeat": monitor.error_repeat,
                "error_window": monitor.error_window,
                "stall_after": monitor.stall_after,
                "churn_lines": monitor.churn_lines,
                "outage_window": monitor.outage_window,
            },
            "pause": {
                "trigger_rules": dict(P.TRIGGER_RULES),
                "strictness": P.load_strictness(root),
                "strictness_levels": list(P.STRICTNESS_LEVELS),
                **pause_defaults(),
            },
            "patterns": list(SEQ.PATTERNS),
        },
        "jev": {
            "flags": flags.as_dict(),
            "voice_triage": bool(flags.voice_triage and flags.enabled
                                 and flags.provider != "disabled"),
            "effect": (
                "the Jev reading is attached to a triage"
                if flags.voice_triage and flags.enabled and flags.provider != "disabled"
                else "no provider/flags: triage answers deterministically (legacy path)"
            ),
        },
        "settings": str(settings_path(root)),
        "completion_dir": {
            "path": str(completion_dir(root)),
            "exists": completion_dir(root).is_dir(),
            "contracts": sorted(
                path.name for path in completion_dir(root).glob("*.json")
            ) if completion_dir(root).is_dir() else [],
        },
    }


__all__ = [
    "CATEGORIES",
    "CATEGORY_LABELS",
    "CATEGORY_TRIGGER",
    "CAT_CLAIMS",
    "CAT_DRIFT",
    "CAT_ERRORS",
    "CAT_LOOPS",
    "CAT_TOOLUSE",
    "CAT_VERIFY",
    "DEFAULT_WINDOW",
    "Finding",
    "ReadOnlyEventView",
    "SIGNAL_CATEGORY",
    "Trajectory",
    "category_for",
    "category_wakes",
    "check",
    "completion_dir",
    "default_runtime",
    "events_path",
    "explain",
    "incidents_path",
    "list_sessions",
    "newest_session",
    "pauses_path",
    "read_trajectory",
    "resolve_session",
    "runs_path",
    "severity_name",
    "status",
    "triage",
]
