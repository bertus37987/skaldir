"""Incident memory — durable records of problems and their verified fixes.

Schema and lifecycle (docs/CORE-PLAN.md §2): an incident is keyed by its
problem **signature** (fingerprint of error class/text, see
``monitor.error_fingerprint``). New sightings bump ``occurrences``; records
start as ``candidate`` and are promoted to ``active`` after review
(propose-first — nothing auto-promotes). Search order: exact signature →
full-text (FTS5) → cosine similarity over embeddings (when provided).

Block 7 (memory learning) extends the schema with the mandated learning axes:
``failure_class``, ``component``, ``failed_strategies``, ``successful_recovery``,
``scope``, ``valid_until``, ``provenance`` (``evidence`` maps onto the existing
``evidence_refs``, ``first_seen``/``last_seen``/``root_cause``/``confidence``
already existed). The migration is **additive**: ``ALTER TABLE ... ADD COLUMN``
runs only for columns that are missing, so databases written by older versions
keep working and their rows read back with the documented defaults
(``scope`` falls back to ``repo_scope``). See ``memory.py`` for the enforced
retrieval rule (retrieve → applicability check → use/reject).

Thread-safe (Hermes hooks run on worker threads): shared connection with
``check_same_thread=False`` + RLock, like ``EventStore``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .embedding import cosine

STATUS_CANDIDATE = "candidate"
STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
VALID_STATUS = {STATUS_CANDIDATE, STATUS_ACTIVE, STATUS_ARCHIVED}

# Block 7 additions: column name → SQL type/default clause (additive migration).
SCHEMA_V2_COLUMNS: dict[str, str] = {
    "failure_class": "TEXT NOT NULL DEFAULT ''",
    "component": "TEXT NOT NULL DEFAULT ''",
    "failed_strategies": "TEXT NOT NULL DEFAULT '[]'",
    "successful_recovery": "TEXT NOT NULL DEFAULT ''",
    "scope": "TEXT NOT NULL DEFAULT ''",
    "valid_until": "REAL NOT NULL DEFAULT 0",
    "provenance": "TEXT NOT NULL DEFAULT '{}'",
}

# The mandated incident schema (Block 7) — order fixed for the docs/CLI.
SCHEMA_FIELDS = (
    "failure_class",
    "root_cause",
    "component",
    "evidence",
    "failed_strategies",
    "successful_recovery",
    "confidence",
    "scope",
    "first_seen",
    "last_seen",
    "provenance",
)


@dataclass
class Incident:
    signature: str
    title: str
    kind: str = "error"
    root_cause: str = ""
    resolution_steps: list[str] = field(default_factory=list)
    verification: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    status: str = STATUS_CANDIDATE
    confidence: float = 0.5
    occurrences: int = 1
    first_seen: float = 0.0
    last_seen: float = 0.0
    repo_scope: str = "global"
    embedding: list[float] | None = None
    # -- Block 7 learning axes ------------------------------------------------
    failure_class: str = ""
    component: str = ""
    failed_strategies: list[str] = field(default_factory=list)
    successful_recovery: str = ""
    scope: str = ""
    valid_until: float = 0.0
    provenance: dict = field(default_factory=dict)
    id: int | None = None

    @property
    def effective_scope(self) -> str:
        """``scope`` with the legacy ``repo_scope`` as fallback."""
        return self.scope or self.repo_scope or "global"

    @property
    def evidence(self) -> list[str]:
        """Alias for ``evidence_refs`` (mandated schema name)."""
        return self.evidence_refs

    def is_expired(self, now: float | None = None) -> bool:
        """True when ``valid_until`` has passed (0 = no expiry)."""
        if not self.valid_until:
            return False
        return (time.time() if now is None else now) > self.valid_until

    def to_schema_dict(self) -> dict:
        """The record rendered on the mandated schema (Block 7)."""
        return {
            "failure_class": self.failure_class or self.kind,
            "root_cause": self.root_cause,
            "component": self.component,
            "evidence": list(self.evidence_refs),
            "failed_strategies": list(self.failed_strategies),
            "successful_recovery": self.successful_recovery
            or "; ".join(self.resolution_steps),
            "confidence": self.confidence,
            "scope": self.effective_scope,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "provenance": dict(self.provenance),
        }

    def to_evidence_text(self) -> str:
        text = self.title
        if self.root_cause:
            text += f" — Cause: {self.root_cause}"
        if self.resolution_steps:
            text += f" — Fix: {'; '.join(self.resolution_steps[:3])}"
        if self.verification:
            text += f" — Evidence: {self.verification}"
        return text


def _fts_query(text: str) -> str:
    """Build a safe FTS5 OR-query from free text (alnum tokens ≥2 chars)."""
    cleaned = "".join(char if char.isalnum() else " " for char in text.lower())
    tokens: list[str] = []
    for token in cleaned.split():
        if len(token) >= 2 and token not in tokens:
            tokens.append(token)
    return " OR ".join(tokens)


class IncidentStore:
    """SQLite-backed incident memory (shares a file with ``NoteStore`` fine)."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    signature        TEXT NOT NULL UNIQUE,
                    title            TEXT NOT NULL,
                    kind             TEXT NOT NULL DEFAULT 'error',
                    root_cause       TEXT NOT NULL DEFAULT '',
                    resolution_steps TEXT NOT NULL DEFAULT '[]',
                    verification     TEXT NOT NULL DEFAULT '',
                    evidence_refs    TEXT NOT NULL DEFAULT '[]',
                    status           TEXT NOT NULL DEFAULT 'candidate',
                    confidence       REAL NOT NULL DEFAULT 0.5,
                    occurrences      INTEGER NOT NULL DEFAULT 1,
                    first_seen       REAL NOT NULL DEFAULT 0,
                    last_seen        REAL NOT NULL DEFAULT 0,
                    repo_scope       TEXT NOT NULL DEFAULT 'global',
                    embedding        TEXT
                )
                """
            )
            self._migrate_locked()
            self._db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS incidents_fts "
                "USING fts5(title, root_cause, resolution_text)"
            )
            self._backfill_fts_locked()
            self._db.commit()

    def _backfill_fts_locked(self) -> None:
        """Index rows that are missing from the FTS table (idempotent).

        Databases written before the original release had no FTS entries at all,
        which made their incidents invisible to text search. Re-opening such a
        file now indexes the existing rows instead of silently finding nothing.
        """
        self._db.execute(
            "INSERT INTO incidents_fts(rowid, title, root_cause, resolution_text) "
            "SELECT id, title, root_cause, resolution_steps FROM incidents "
            "WHERE id NOT IN (SELECT rowid FROM incidents_fts)"
        )

    def _migrate_locked(self) -> None:
        """Additive, idempotent schema migration (Block 7).

        Only *missing* columns are added, so a database written by an older
        version opens unchanged and its rows read back with defaults. Never
        drops or rewrites a row.
        """
        existing = {row[1] for row in self._db.execute("PRAGMA table_info(incidents)")}
        for column, ddl in SCHEMA_V2_COLUMNS.items():
            if column not in existing:
                self._db.execute(f"ALTER TABLE incidents ADD COLUMN {column} {ddl}")
        self._db.commit()

    # -- writes -------------------------------------------------------------
    def upsert(self, incident: Incident, ts: float | None = None) -> Incident:
        """Insert/update by signature; bumps occurrences + last_seen on repeat."""
        now = time.time() if ts is None else ts
        with self._lock:
            existing = self._get_locked(incident.signature)
            if existing is None:
                if incident.first_seen == 0.0:
                    incident.first_seen = now
                incident.last_seen = now
                incident.provenance = self._ensure_provenance_locked(incident, now)
                cursor = self._db.execute(
                    """
                    INSERT INTO incidents(signature, title, kind, root_cause,
                        resolution_steps, verification, evidence_refs, status,
                        confidence, occurrences, first_seen, last_seen, repo_scope,
                        embedding, failure_class, component, failed_strategies,
                        successful_recovery, scope, valid_until, provenance)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        incident.signature,
                        incident.title,
                        incident.kind,
                        incident.root_cause,
                        json.dumps(incident.resolution_steps, ensure_ascii=False),
                        incident.verification,
                        json.dumps(incident.evidence_refs, ensure_ascii=False),
                        incident.status,
                        incident.confidence,
                        incident.occurrences,
                        incident.first_seen,
                        incident.last_seen,
                        incident.repo_scope,
                        json.dumps(incident.embedding) if incident.embedding is not None else None,
                        incident.failure_class or incident.kind,
                        incident.component,
                        json.dumps(incident.failed_strategies, ensure_ascii=False),
                        incident.successful_recovery,
                        incident.scope or incident.repo_scope,
                        float(incident.valid_until or 0.0),
                        json.dumps(incident.provenance, ensure_ascii=False),
                    ),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("insert into incidents did not return a row id")
                incident.id = int(cursor.lastrowid)
            else:
                existing.last_seen = now
                existing.occurrences += 1
                if incident.title:
                    existing.title = incident.title
                if incident.root_cause:
                    existing.root_cause = incident.root_cause
                if incident.resolution_steps:
                    existing.resolution_steps = incident.resolution_steps
                if incident.verification:
                    existing.verification = incident.verification
                if incident.evidence_refs:
                    existing.evidence_refs = incident.evidence_refs
                if incident.confidence:
                    existing.confidence = incident.confidence
                if incident.embedding is not None:
                    existing.embedding = incident.embedding
                # Block 7 axes: non-empty incoming values win; provenance merges
                # (a repeat sighting must not erase the original origin).
                if incident.failure_class:
                    existing.failure_class = incident.failure_class
                if incident.component:
                    existing.component = incident.component
                if incident.failed_strategies:
                    existing.failed_strategies = incident.failed_strategies
                if incident.successful_recovery:
                    existing.successful_recovery = incident.successful_recovery
                if incident.scope:
                    existing.scope = incident.scope
                if incident.valid_until:
                    existing.valid_until = incident.valid_until
                existing.provenance = _merge_provenance(existing.provenance, incident.provenance)
                self._db.execute(
                    """
                    UPDATE incidents SET title=?, kind=?, root_cause=?, resolution_steps=?,
                        verification=?, evidence_refs=?, confidence=?, occurrences=?, last_seen=?,
                        repo_scope=?, embedding=?, failure_class=?, component=?,
                        failed_strategies=?, successful_recovery=?, scope=?, valid_until=?,
                        provenance=? WHERE id=?
                    """,
                    (
                        existing.title,
                        existing.kind,
                        existing.root_cause,
                        json.dumps(existing.resolution_steps, ensure_ascii=False),
                        existing.verification,
                        json.dumps(existing.evidence_refs, ensure_ascii=False),
                        existing.confidence,
                        existing.occurrences,
                        existing.last_seen,
                        existing.repo_scope,
                        json.dumps(existing.embedding) if existing.embedding is not None else None,
                        existing.failure_class,
                        existing.component,
                        json.dumps(existing.failed_strategies, ensure_ascii=False),
                        existing.successful_recovery,
                        existing.scope,
                        float(existing.valid_until or 0.0),
                        json.dumps(existing.provenance, ensure_ascii=False),
                        existing.id,
                    ),
                )
                incident = existing
            self._sync_fts_locked(incident)
            self._db.commit()
        return incident

    @staticmethod
    def _ensure_provenance_locked(incident: Incident, now: float) -> dict:
        """Fill the unavoidable provenance parts (origin + recorded_at)."""
        provenance = dict(incident.provenance or {})
        if not provenance.get("origin"):
            provenance["origin"] = "incident_proposal"
        if not provenance.get("recorded_at"):
            provenance["recorded_at"] = now
        return provenance

    def _sync_fts_locked(self, incident: Incident) -> None:
        self._db.execute("DELETE FROM incidents_fts WHERE rowid = ?", (incident.id,))
        self._db.execute(
            "INSERT INTO incidents_fts(rowid, title, root_cause, resolution_text) VALUES (?,?,?,?)",
            (incident.id, incident.title, incident.root_cause, " ".join(incident.resolution_steps)),
        )

    def signatures(self) -> set[str]:
        """All known incident signatures — for the voice's cross-session check."""
        with self._lock:
            rows = self._db.execute("SELECT signature FROM incidents").fetchall()
        return {row[0] for row in rows}

    def intervention_signatures(
        self,
        *,
        now: float | None = None,
        min_confidence: float = 0.5,
        include_candidates: bool = False,
    ) -> set[str]:
        """Signatures eligible to escalate to a *strong* intervention (Block 7).

        ``signatures()`` lists everything ever recorded — including archived,
        expired and never-reviewed candidates. Feeding that set into the monitor's
        ``known_incident_repeat`` rule lets a stale entry raise a strong
        intervention, which is exactly what the retrieval rule forbids.

        This method applies the same applicability rule as
        ``memory.evaluate_retrieval`` (review status, ``valid_until``, confidence)
        so a call site can switch to it with one word. Callers that must keep the
        old breadth should keep calling ``signatures()``.
        """
        from .memory import DEFAULT_MIN_CONFIDENCE  # local: avoids an import cycle

        threshold = DEFAULT_MIN_CONFIDENCE if min_confidence is None else min_confidence
        timestamp = time.time() if now is None else now
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_SELECT_COLUMNS} FROM incidents WHERE status != 'archived'"  # nosec B608
            ).fetchall()
        eligible: set[str] = set()
        for row in rows:
            incident = _row_to_incident(row)
            if not include_candidates and incident.status != STATUS_ACTIVE:
                continue
            if incident.valid_until and timestamp > incident.valid_until:
                continue  # expired: never a valid basis
            if incident.confidence < threshold:
                continue
            eligible.add(incident.signature)
        return eligible

    def record_sighting(self, signature: str, ts: float | None = None) -> bool:
        """Bump ``occurrences``/``last_seen`` for a known signature."""
        now = time.time() if ts is None else ts
        with self._lock:
            cursor = self._db.execute(
                "UPDATE incidents SET occurrences = occurrences + 1, last_seen = ? "
                "WHERE signature = ?",
                (now, signature),
            )
            self._db.commit()
        return cursor.rowcount > 0

    def promote(self, signature: str, status: str = STATUS_ACTIVE) -> bool:
        if status not in VALID_STATUS:
            raise ValueError(f"invalid status: {status}")
        with self._lock:
            cursor = self._db.execute(
                "UPDATE incidents SET status = ? WHERE signature = ?", (status, signature)
            )
            self._db.commit()
        return cursor.rowcount > 0

    # -- reads --------------------------------------------------------------
    def get(self, signature: str) -> Incident | None:
        with self._lock:
            return self._get_locked(signature)

    def _get_locked(self, signature: str) -> Incident | None:
        row = self._db.execute(
            f"SELECT {_SELECT_COLUMNS} FROM incidents WHERE signature = ?", (signature,),  # nosec B608
        ).fetchone()
        return _row_to_incident(row) if row else None

    def get_by_id(self, incident_id: int) -> Incident | None:
        with self._lock:
            row = self._db.execute(
                f"SELECT {_SELECT_COLUMNS} FROM incidents WHERE id = ?", (incident_id,),  # nosec B608
            ).fetchone()
        return _row_to_incident(row) if row else None

    def search_text(self, query: str, limit: int = 5) -> list[tuple[Incident, float]]:
        """Full-text search; returns (incident, score) with higher = better."""
        fts = _fts_query(query)
        if not fts:
            return []
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_select_columns('i')}, incidents_fts.rank FROM incidents_fts "  # nosec B608
                "JOIN incidents i ON i.id = incidents_fts.rowid "
                "WHERE incidents_fts MATCH ? AND i.status != 'archived' "
                "ORDER BY incidents_fts.rank LIMIT ?",
                (fts, limit),
            ).fetchall()
        return [(_row_to_incident(row[:-1]), float(-row[-1])) for row in rows]

    def search_similar(self, vector: list[float], limit: int = 5) -> list[tuple[Incident, float]]:
        """Cosine similarity over stored embeddings (higher = better, >0 only).

        Only fires when a caller actually stores embeddings — with the built-in
        ``HashingEmbeddings`` this is *lexical* (trigram) overlap, not semantics.
        """
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_SELECT_COLUMNS} FROM incidents "  # nosec B608
                "WHERE embedding IS NOT NULL AND status != 'archived'"
            ).fetchall()
        scored: list[tuple[Incident, float]] = []
        for row in rows:
            incident = _row_to_incident(row)
            score = cosine(vector, incident.embedding or [])
            if score > 0:
                scored.append((incident, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def all(self, include_archived: bool = False) -> list[Incident]:
        query = f"SELECT {_SELECT_COLUMNS} FROM incidents"  # nosec B608
        if not include_archived:
            query += " WHERE status != 'archived'"
        query += " ORDER BY last_seen DESC"
        with self._lock:
            rows = self._db.execute(query).fetchall()
        return [_row_to_incident(row) for row in rows]

    def set_embedding(self, signature: str, embedding: list[float] | None) -> bool:
        """Store/replace an entry's embedding — the write path for vector search.

        Without a caller the ``embedding`` column stays NULL (audit MEM-10); this
        makes storing one a single explicit call.
        """
        with self._lock:
            cursor = self._db.execute(
                "UPDATE incidents SET embedding = ? WHERE signature = ?",
                (json.dumps(embedding) if embedding is not None else None, signature),
            )
            self._db.commit()
        return cursor.rowcount > 0

    def count(self) -> int:
        with self._lock:
            (total,) = self._db.execute("SELECT COUNT(*) FROM incidents").fetchone()
        return int(total)

    def close(self) -> None:
        with self._lock:
            self._db.close()


def make_provenance(
    origin: str,
    *,
    session_id: str = "",
    step: int | None = None,
    event_refs: list[str] | None = None,
    source: str = "",
    recorded_at: float | None = None,
) -> dict:
    """Build a traceable provenance payload (where from, when, in which context)."""
    return {
        "origin": origin,
        "recorded_at": time.time() if recorded_at is None else recorded_at,
        "session_id": session_id,
        "step": step,
        "event_refs": list(event_refs or []),
        "source": source,
    }


def _merge_provenance(existing: dict | None, incoming: dict | None) -> dict:
    """Merge two provenance payloads (non-empty incoming keys win, refs union)."""
    base = dict(existing) if isinstance(existing, dict) else {}
    for key, value in (incoming or {}).items():
        if value in (None, "", [], {}):
            continue
        base[key] = value
    refs: list[str] = []
    for payload in (existing, incoming):
        if isinstance(payload, dict):
            for ref in payload.get("event_refs", []) or []:
                if ref not in refs:
                    refs.append(ref)
    if refs:
        base["event_refs"] = refs
    return base


_SELECT_COLUMNS = (
    "id, signature, title, kind, root_cause, resolution_steps, verification, "
    "evidence_refs, status, confidence, occurrences, first_seen, last_seen, "
    "repo_scope, embedding, failure_class, component, failed_strategies, "
    "successful_recovery, scope, valid_until, provenance"
)


def _select_columns(alias: str = "") -> str:
    """Column list, optionally prefixed with a table alias (JOIN-safe)."""
    prefix = f"{alias}." if alias else ""
    return ", ".join(f"{prefix}{name.strip()}" for name in _SELECT_COLUMNS.split(","))


def _safe_json(value: Any, fallback: Any) -> Any:
    """Parse a JSON column; a corrupt/legacy value degrades instead of raising."""
    if value in (None, ""):
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


def _cell(row: tuple, index: int) -> Any:
    """Return a row cell or ``None`` when the row is shorter (legacy read)."""
    return row[index] if len(row) > index else None


def _as_text(value: Any, fallback: str = "") -> str:
    return fallback if value is None else str(value)


def _as_float(value: Any, fallback: float = 0.0) -> float:
    return fallback if value is None else float(value)


def _as_int(value: Any, fallback: int = 0) -> int:
    return fallback if value is None else int(value)


def _row_to_incident(row: tuple) -> Incident:
    """Map a full row (see ``_SELECT_COLUMNS``) onto an ``Incident``.

    Reads defensively: rows written before the Block 7 migration carry empty
    defaults, and a short/legacy row still yields a usable record instead of
    crashing the caller.
    """
    raw_id = _cell(row, 0)
    return Incident(
        id=None if raw_id is None else int(raw_id),
        signature=_as_text(_cell(row, 1)),
        title=_as_text(_cell(row, 2)),
        kind=_as_text(_cell(row, 3), "error"),
        root_cause=_as_text(_cell(row, 4)),
        resolution_steps=list(_safe_json(_cell(row, 5), []) or []),
        verification=_as_text(_cell(row, 6)),
        evidence_refs=list(_safe_json(_cell(row, 7), []) or []),
        status=_as_text(_cell(row, 8), STATUS_CANDIDATE),
        confidence=_as_float(_cell(row, 9), 0.5),
        occurrences=_as_int(_cell(row, 10), 1),
        first_seen=_as_float(_cell(row, 11)),
        last_seen=_as_float(_cell(row, 12)),
        repo_scope=_as_text(_cell(row, 13), "global"),
        embedding=_safe_json(_cell(row, 14), None),
        failure_class=_as_text(_cell(row, 15)),
        component=_as_text(_cell(row, 16)),
        failed_strategies=list(_safe_json(_cell(row, 17), []) or []),
        successful_recovery=_as_text(_cell(row, 18)),
        scope=_as_text(_cell(row, 19)),
        valid_until=_as_float(_cell(row, 20)),
        provenance=dict(_safe_json(_cell(row, 21), {}) or {}),
    )

