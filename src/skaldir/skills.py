"""Skill mining — turn repeated practice into SKILL.md candidates.

Pipeline (docs/CORE-PLAN.md §3): observe patterns across sessions
(≥ ``min_occurrences`` in ≥ ``min_sessions``) → draft a SKILL.md
(``skill_draft`` prompt, or a deterministic skeleton without an LLM) →
shadow validation → promotion. Everything is propose-first: drafts start as
``candidate`` and nothing is auto-promoted. Drift monitoring: usage stats
(``applied`` / ``succeeded``) feed ``success_rate()``; a promoted skill whose
rate drops below the threshold is demoted for review.

Security: skill bodies are proposals only — stored, never auto-injected into
any prompt; promotion is an explicit review step (SECURITY.md).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .events import EventStore
from .llm import BudgetTracker, LLMClient
from .promptrun import run_prompt
from .prompts import PromptRegistry

STATUS_CANDIDATE = "candidate"
STATUS_SHADOW = "shadow"
STATUS_PROMOTED = "promoted"
STATUS_DEMOTED = "demoted"
VALID_STATUS = {STATUS_CANDIDATE, STATUS_SHADOW, STATUS_PROMOTED, STATUS_DEMOTED}


@dataclass
class SkillCandidate:
    name: str
    title: str
    tool_chain: list[str] = field(default_factory=list)
    occurrences: int = 1
    sessions: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    body: str = ""
    status: str = STATUS_CANDIDATE
    version: str = "0.1.0"
    applied: int = 0
    succeeded: int = 0
    created: float = 0.0
    updated: float = 0.0
    id: int | None = None
    # SKILL-01: structured contract fields
    preconditions: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)

    def success_rate(self) -> float | None:
        if self.applied == 0:
            return None
        return self.succeeded / self.applied


class SkillStore:
    """SQLite-backed skill-candidate memory (thread-safe)."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_candidates (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT NOT NULL UNIQUE,
                    title       TEXT NOT NULL,
                    tool_chain  TEXT NOT NULL DEFAULT '[]',
                    occurrences INTEGER NOT NULL DEFAULT 1,
                    sessions    TEXT NOT NULL DEFAULT '[]',
                    evidence    TEXT NOT NULL DEFAULT '[]',
                    body        TEXT NOT NULL DEFAULT '',
                    status      TEXT NOT NULL DEFAULT 'candidate',
                    version     TEXT NOT NULL DEFAULT '0.1.0',
                    applied     INTEGER NOT NULL DEFAULT 0,
                    succeeded   INTEGER NOT NULL DEFAULT 0,
                    created     REAL NOT NULL DEFAULT 0,
                    updated     REAL NOT NULL DEFAULT 0
                )
                """
            )
            self._db.commit()

    def upsert(self, candidate: SkillCandidate, ts: float | None = None) -> SkillCandidate:
        """Insert/merge by name (occurrences and session sets are merged)."""
        now = time.time() if ts is None else ts
        with self._lock:
            existing = self._get_locked(candidate.name)
            if existing is None:
                candidate.created = now
                candidate.updated = now
                cursor = self._db.execute(
                    """
                    INSERT INTO skill_candidates(name, title, tool_chain, occurrences, sessions,
                        evidence, body, status, version, applied, succeeded, created, updated)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        candidate.name,
                        candidate.title,
                        json.dumps(candidate.tool_chain, ensure_ascii=False),
                        candidate.occurrences,
                        json.dumps(candidate.sessions, ensure_ascii=False),
                        json.dumps(candidate.evidence, ensure_ascii=False),
                        candidate.body,
                        candidate.status,
                        candidate.version,
                        candidate.applied,
                        candidate.succeeded,
                        candidate.created,
                        candidate.updated,
                    ),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("insert into skill_candidates did not return a row id")
                candidate.id = int(cursor.lastrowid)
            else:
                merged_sessions = sorted(set(existing.sessions) | set(candidate.sessions))
                merged_evidence = existing.evidence + [
                    item for item in candidate.evidence if item not in existing.evidence
                ]
                existing.sessions = merged_sessions
                existing.evidence = merged_evidence
                existing.occurrences = max(existing.occurrences, candidate.occurrences)
                existing.updated = now
                if candidate.body and not existing.body:
                    existing.body = candidate.body
                self._db.execute(
                    """
                    UPDATE skill_candidates SET occurrences=?, sessions=?, evidence=?, body=?,
                        updated=? WHERE id=?
                    """,
                    (
                        existing.occurrences,
                        json.dumps(existing.sessions, ensure_ascii=False),
                        json.dumps(existing.evidence, ensure_ascii=False),
                        existing.body,
                        existing.updated,
                        existing.id,
                    ),
                )
                candidate = existing
            self._db.commit()
        return candidate

    def get(self, name: str) -> SkillCandidate | None:
        with self._lock:
            return self._get_locked(name)

    def _get_locked(self, name: str) -> SkillCandidate | None:
        row = self._db.execute(
            "SELECT * FROM skill_candidates WHERE name = ?", (name,)
        ).fetchone()
        return _row_to_candidate(row) if row else None

    def set_status(self, name: str, status: str) -> bool:
        if status not in VALID_STATUS:
            raise ValueError(f"invalid status: {status}")
        with self._lock:
            cursor = self._db.execute(
                "UPDATE skill_candidates SET status = ?, updated = ? WHERE name = ?",
                (status, time.time(), name),
            )
            self._db.commit()
        return cursor.rowcount > 0

    def record_use(self, name: str, success: bool) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "UPDATE skill_candidates SET applied = applied + 1, succeeded = succeeded + ?, "
                "updated = ? WHERE name = ?",
                (1 if success else 0, time.time(), name),
            )
            self._db.commit()
        return cursor.rowcount > 0

    def update_body(self, name: str, body: str) -> bool:
        """Replace a skill's body (used by self-review apply)."""
        with self._lock:
            cursor = self._db.execute(
                "UPDATE skill_candidates SET body = ?, updated = ? WHERE name = ?",
                (body, time.time(), name),
            )
            self._db.commit()
        return cursor.rowcount > 0

    def all(self) -> list[SkillCandidate]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM skill_candidates ORDER BY updated DESC"
            ).fetchall()
        return [_row_to_candidate(row) for row in rows]

    def count(self) -> int:
        with self._lock:
            (total,) = self._db.execute("SELECT COUNT(*) FROM skill_candidates").fetchone()
        return int(total)

    def close(self) -> None:
        with self._lock:
            self._db.close()


def mine_tool_patterns(
    store: EventStore,
    *,
    min_occurrences: int = 3,
    min_sessions: int = 2,
    max_chain: int = 3,
) -> list[SkillCandidate]:
    """Mine repeated tool-call chains across sessions (repetition = signal)."""
    counts: dict[tuple[str, ...], dict] = {}
    for session_id in store.session_ids():
        steps = [
            (event.step, str(event.payload.get("name", "")))
            for event in store.iter_session(session_id)
            if event.kind == "tool_call" and event.payload.get("name")
        ]
        names = [name for _step, name in steps]
        for size in range(2, max_chain + 1):
            for start in range(len(names) - size + 1):
                key = tuple(names[start : start + size])
                entry = counts.setdefault(key, {"count": 0, "sessions": set(), "examples": []})
                entry["count"] += 1
                if session_id not in entry["sessions"]:
                    entry["sessions"].add(session_id)
                    if len(entry["examples"]) < 5:
                        first, last = steps[start][0], steps[start + size - 1][0]
                        entry["examples"].append(f"{session_id} (Steps {first}-{last})")
    qualified = [
        (len(key), key, entry)
        for key, entry in counts.items()
        if entry["count"] >= min_occurrences and len(entry["sessions"]) >= min_sessions
    ]
    qualified.sort(key=lambda item: -item[0])
    covered: list[tuple[str, ...]] = []
    candidates: list[SkillCandidate] = []
    for _size, key, entry in qualified:
        if any(_is_subchain(key, longer) for longer in covered):
            continue
        covered.append(key)
        candidates.append(
            SkillCandidate(
                name="workflow-" + "-".join(key),
                title=f"Workflow: {' → '.join(key)}",
                tool_chain=list(key),
                occurrences=entry["count"],
                sessions=sorted(entry["sessions"]),
                evidence=list(entry["examples"]),
            )
        )
    return candidates


def _is_subchain(small: tuple[str, ...], large: tuple[str, ...]) -> bool:
    span = len(large) - len(small) + 1
    return any(large[index : index + len(small)] == small for index in range(span))


def draft_skill(
    candidate: SkillCandidate,
    *,
    client: LLMClient | None = None,
    registry: PromptRegistry | None = None,
    budget: BudgetTracker | None = None,
) -> SkillCandidate:
    """Fill ``candidate.body`` — via the ``skill_draft`` prompt or a skeleton."""
    if client is None or registry is None:
        candidate.body = _skeleton_body(candidate)
        return candidate
    result = run_prompt(
        client,
        registry,
        "skill_draft",
        {
            "pattern": f"Tool chain: {' -> '.join(candidate.tool_chain)}",
            "occurrences": str(candidate.occurrences),
            "sessions": ", ".join(candidate.sessions),
            "evidence": "\n".join(candidate.evidence) or "(none)",
        },
        expect_json=False,
        budget=budget,
    )
    text = (result.raw or "").strip()
    candidate.body = text if text.startswith("---") else _skeleton_body(candidate)
    return candidate


def _skeleton_body(candidate: SkillCandidate) -> str:
    chain = " → ".join(candidate.tool_chain)
    return (
        "---\n"
        f"name: {candidate.name}\n"
        f'description: "Workflow {chain} ({candidate.occurrences}× observed)"\n'
        f"version: {candidate.version}\n"
        "---\n\n"
        f"# {candidate.title}\n\n"
        "## When to use\n"
        f"Observed in sessions: {', '.join(candidate.sessions)}\n\n"
        "## Procedure\n"
        f"1. Run the tool chain: {chain}\n"
        "2. (draft pending — refine it with an LLM or extend it manually)\n\n"
        "## Pitfalls\n"
        "- (none observed yet)\n\n"
        "## Verification\n"
        "- Every step runs through without an error event\n"
    )


def parse_skill_header(body: str) -> dict:
    """Parse the YAML frontmatter of a SKILL.md body ({} when absent/invalid)."""
    if not body.startswith("---"):
        return {}
    parts = body.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def is_valid_skill_body(body: str) -> bool:
    header = parse_skill_header(body)
    return bool(header.get("name")) and bool(header.get("description"))


def demote_on_drift(
    store: SkillStore, *, threshold: float = 0.5, min_uses: int = 5
) -> list[str]:
    """Demote promoted skills whose usage success rate dropped (drift)."""
    demoted: list[str] = []
    for skill in store.all():
        if skill.status != STATUS_PROMOTED:
            continue
        rate = skill.success_rate()
        if rate is not None and skill.applied >= min_uses and rate < threshold:
            store.set_status(skill.name, STATUS_DEMOTED)
            demoted.append(skill.name)
    return demoted


def relevant_skills(
    store: SkillStore,
    recent_names: list[str],
    *,
    window: int = 8,
    limit: int = 1,
) -> list[SkillCandidate]:
    """Promoted skills whose tool chain matches the recent call tail.

    Consumption path: **promoted only** — shadows, candidates and demoted
    skills are never surfaced (drift protection); the match is deterministic
    (subchain inside the recent tool-call window).
    """
    tail = [str(name) for name in recent_names[-window:] if name]
    if not tail:
        return []
    matches: list[tuple[int, SkillCandidate]] = []
    for skill in store.all():
        if skill.status != STATUS_PROMOTED or not skill.tool_chain:
            continue
        if _is_subchain(tuple(skill.tool_chain), tuple(tail)):
            matches.append((len(skill.tool_chain), skill))
    matches.sort(key=lambda item: (-item[0], item[1].name))
    return [skill for _size, skill in matches[:limit]]


# --------------------------------------------------------------------------- #
# Self-improvement review — user feedback + experience → skill / AGENTS.md      #
# --------------------------------------------------------------------------- #


@dataclass
class ReviewProposal:
    """One proposed update from a self-improvement review pass."""

    target: str  # skill name, "AGENTS.md", or "soul.md"
    kind: str  # "body-update" | "correction" | "drift" | "new-section"
    summary: str
    evidence: list[str] = field(default_factory=list)
    suggested_body: str = ""
    applied: bool = False


def record_correction(
    store: SkillStore,
    name: str,
    correction: str,
    *,
    context: str = "",
) -> bool:
    """Store a user correction as evidence on a skill (drives the review pass).

    The owner says "no, use Super+Space not Alt+F2" → the correction is recorded
    so the next ``review_skill`` can fold it into the body.
    """
    skill = store.get(name)
    if skill is None:
        return False
    evidence = list(skill.evidence)
    entry = f"correction: {correction}"
    if context:
        entry += f" (context: {context})"
    if entry not in evidence:
        evidence.append(entry)
    skill.evidence = evidence
    store.upsert(skill)
    return True


def review_skill(store: SkillStore, name: str) -> ReviewProposal | None:
    """Propose a body update for one skill from its corrections + usage stats.

    Signals: (1) user corrections not yet reflected in the body, (2) low
    success rate with enough uses (drift), (3) unused skill (never applied).
    """
    skill = store.get(name)
    if skill is None:
        return None
    corrections = [e for e in skill.evidence if e.startswith("correction:")]
    rate = skill.success_rate()

    # 1. Corrections not yet in the body → propose appending a "Learned corrections" section
    missing = [c for c in corrections if c.removeprefix("correction: ").strip() not in skill.body]
    if missing:
        section = "\n## Learned corrections\n\n" + "\n".join(
            f"- {c.removeprefix('correction: ').strip()}" for c in missing
        )
        body = skill.body
        if "## Learned corrections" in body:
            body = body.rstrip() + "\n" + "\n".join(
                f"- {c.removeprefix('correction: ').strip()}" for c in missing
            ) + "\n"
        else:
            body = body.rstrip() + "\n" + section + "\n"
        return ReviewProposal(
            target=name,
            kind="correction",
            summary=f"{len(missing)} user correction(s) not yet in the body",
            evidence=missing,
            suggested_body=body,
        )

    # 2. Drift: low success rate
    if rate is not None and skill.applied >= 5 and rate < 0.5:
        return ReviewProposal(
            target=name,
            kind="drift",
            summary=f"success rate {rate:.0%} over {skill.applied} uses — body needs revision",
            evidence=[f"applied={skill.applied} succeeded={skill.succeeded}"],
        )

    # 3. Unused: never applied
    if skill.applied == 0 and skill.occurrences > 3:
        return ReviewProposal(
            target=name,
            kind="drift",
            summary=f"never applied despite {skill.occurrences} occurrences — is it reachable?",
            evidence=[f"occurrences={skill.occurrences} applied=0"],
        )
    return None


def review_all_skills(store: SkillStore) -> list[ReviewProposal]:
    """Review every skill in the store; return proposals (empty list = nothing to fix)."""
    proposals: list[ReviewProposal] = []
    for skill in store.all():
        proposal = review_skill(store, skill.name)
        if proposal is not None:
            proposals.append(proposal)
    return proposals


def apply_review(store: SkillStore, proposal: ReviewProposal) -> bool:
    """Apply a review proposal to the skill body (promotes the update in-place)."""
    if proposal.kind in ("drift",) or not proposal.suggested_body:
        return False
    skill = store.get(proposal.target)
    if skill is None:
        return False
    ok = store.update_body(proposal.target, proposal.suggested_body)
    if ok:
        proposal.applied = True
    return ok


def review_agents_md(
    agents_md_path: str | Path,
    corrections: list[str],
) -> ReviewProposal | None:
    """Propose an AGENTS.md update from accumulated corrections (no auto-apply).

    The owner's product rules ("live-desktop may control everything") land here
    when they are not yet in AGENTS.md. The proposal is for the owner to accept —
    AGENTS.md is the product standard and is never silently rewritten.
    """
    path = Path(agents_md_path)
    if not path.exists():
        return ReviewProposal(
            target="AGENTS.md",
            kind="new-section",
            summary="AGENTS.md not found — cannot review",
        )
    text = path.read_text(encoding="utf-8", errors="replace")
    text_lower = text.lower()
    missing = [c for c in corrections if c.strip() and c.strip().lower() not in text_lower]
    if not missing:
        return None
    return ReviewProposal(
        target="AGENTS.md",
        kind="correction",
        summary=f"{len(missing)} correction(s) not yet in AGENTS.md — owner must accept",
        evidence=missing,
        suggested_body=text.rstrip() + "\n\n## Learned corrections (from review)\n\n" + "\n".join(
            f"- {c}" for c in missing
        ) + "\n",
    )


def consumption_hint(skill: SkillCandidate) -> str:
    """Short hint surfacing a promoted skill into the live loop."""
    chain = " → ".join(skill.tool_chain)
    return (
        f"Proven procedure available (`{skill.name}`, confirmed {skill.occurrences}×): "
        f"{chain}. Run the steps deliberately and verify them as described there."
    )


def _row_to_candidate(row: tuple) -> SkillCandidate:
    return SkillCandidate(
        id=row[0],
        name=row[1],
        title=row[2],
        tool_chain=json.loads(row[3]),
        occurrences=row[4],
        sessions=json.loads(row[5]),
        evidence=json.loads(row[6]),
        body=row[7],
        status=row[8],
        version=row[9],
        applied=row[10],
        succeeded=row[11],
        created=row[12],
        updated=row[13],
    )


@dataclass
class ValidationReport:
    """Deterministic shadow-validation result (pre-promotion review)."""

    name: str
    ok: bool
    checks: list[tuple[bool, str]] = field(default_factory=list)

    def to_lines(self) -> list[str]:
        return [("✓ " if passed else "✗ ") + text for passed, text in self.checks]


def validate_candidate(
    skill: SkillCandidate,
    events: EventStore | None = None,
    *,
    min_occurrences: int = 3,
    min_sessions: int = 2,
) -> ValidationReport:
    """Re-verify a skill candidate against the live event store.

    Checks: (1) the body is a valid SKILL.md; (2) the mined pattern still
    holds in the events (≥ thresholds across ≥ sessions). Promotion without a
    store is refused (propose-first: no promotion on unverified claims).
    """
    checks: list[tuple[bool, str]] = []
    body_ok = is_valid_skill_body(skill.body)
    checks.append((body_ok, "SKILL.md frontmatter valid"))
    if events is None:
        checks.append((False, "event store missing — pattern not re-verified"))
        return ValidationReport(name=skill.name, ok=False, checks=checks)
    occurrences, sessions = _count_chain(events, skill.tool_chain)
    counts_ok = occurrences >= min_occurrences and len(sessions) >= min_sessions
    checks.append(
        (
            counts_ok,
            f"Pattern confirmed: {occurrences}× in {len(sessions)} sessions "
            f"(threshold {min_occurrences}×/{min_sessions})",
        )
    )
    error_sessions = _sessions_with_errors(events, sessions)
    checks.append(
        (
            True,
            f"Context: {error_sessions}/{len(sessions)} pattern sessions with error events "
            "(informational)",
        )
    )
    return ValidationReport(name=skill.name, ok=body_ok and counts_ok, checks=checks)


def _count_chain(events: EventStore, chain: list[str]) -> tuple[int, set[str]]:
    occurrences = 0
    sessions: set[str] = set()
    size = len(chain)
    if size == 0:
        return 0, sessions
    for session_id in events.session_ids():
        names = [
            str(event.payload.get("name", ""))
            for event in events.iter_session(session_id)
            if event.kind == "tool_call"
        ]
        for start in range(len(names) - size + 1):
            if names[start : start + size] == chain:
                occurrences += 1
                sessions.add(session_id)
    return occurrences, sessions


def _sessions_with_errors(events: EventStore, sessions: set[str]) -> int:
    from .monitor import error_text  # local import keeps the module import graph flat

    count = 0
    for session_id in sessions:
        for event in events.iter_session(session_id):
            if event.kind == "tool_result" and error_text(event.payload):
                count += 1
                break
    return count
