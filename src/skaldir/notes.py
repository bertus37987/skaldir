"""Research notes — atomic findings with provenance, trust and expiry.

Schema (docs/CORE-PLAN.md §2): one note = one atomic claim with source
metadata. ``valid_until`` marks version-scoped claims (expiry); ``trust`` is
0..1; ``contradicts`` links conflicting notes. Notes are *external-derived*
content — consumers must treat them as untrusted (spotlight) when they enter
prompts; see ``retrieval.py``.

``curate_finding`` wires the ``research_note`` prompt: raw finding + source
metadata in, a validated ``ResearchNote`` out (propose-first — the caller
decides whether to store it).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .llm import BudgetTracker, LLMClient
from .promptrun import run_prompt
from .prompts import PromptRegistry

STATUS_ACTIVE = "active"
STATUS_STALE = "stale"
STATUS_CONTRADICTED = "contradicted"
STATUS_ARCHIVED = "archived"
VALID_STATUS = {STATUS_ACTIVE, STATUS_STALE, STATUS_CONTRADICTED, STATUS_ARCHIVED}


@dataclass
class ResearchNote:
    topic: str
    claim: str
    source_type: str = "docs"
    source_url: str = ""
    retrieved_at: str = ""
    valid_until: str | None = None
    trust: float = 0.5
    repo_scope: str = "global"
    status: str = STATUS_ACTIVE
    contradicts: list[str] = field(default_factory=list)
    created: float = 0.0
    id: int | None = None

    def is_stale(self, today: str) -> bool:
        """True when the claim's ``valid_until`` date has passed."""
        return bool(self.valid_until) and self.valid_until < today

    def to_evidence_text(self) -> str:
        text = f"{self.topic}: {self.claim}"
        if self.source_type:
            text += f" (source: {self.source_type}"
            if self.source_url:
                text += f" {self.source_url}"
            text += ")"
        return text


class NoteStore:
    """SQLite-backed research-note memory (thread-safe, shares files fine)."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS notes (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic        TEXT NOT NULL,
                    claim        TEXT NOT NULL,
                    source_type  TEXT NOT NULL DEFAULT 'docs',
                    source_url   TEXT NOT NULL DEFAULT '',
                    retrieved_at TEXT NOT NULL DEFAULT '',
                    valid_until  TEXT,
                    trust        REAL NOT NULL DEFAULT 0.5,
                    repo_scope   TEXT NOT NULL DEFAULT 'global',
                    status       TEXT NOT NULL DEFAULT 'active',
                    contradicts  TEXT NOT NULL DEFAULT '[]',
                    created      REAL NOT NULL DEFAULT 0
                )
                """
            )
            self._db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(topic, claim)"
            )
            self._db.commit()

    # -- writes -------------------------------------------------------------
    def add(self, note: ResearchNote, ts: float | None = None) -> ResearchNote:
        note.created = time.time() if ts is None else ts
        with self._lock:
            cursor = self._db.execute(
                """
                INSERT INTO notes(topic, claim, source_type, source_url, retrieved_at,
                    valid_until, trust, repo_scope, status, contradicts, created)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    note.topic,
                    note.claim,
                    note.source_type,
                    note.source_url,
                    note.retrieved_at,
                    note.valid_until,
                    note.trust,
                    note.repo_scope,
                    note.status,
                    json.dumps(note.contradicts, ensure_ascii=False),
                    note.created,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("insert into notes did not return a row id")
            note.id = int(cursor.lastrowid)
            self._db.execute(
                "INSERT INTO notes_fts(rowid, topic, claim) VALUES (?,?,?)",
                (note.id, note.topic, note.claim),
            )
            self._db.commit()
        return note

    def set_status(self, note_id: int, status: str) -> bool:
        if status not in VALID_STATUS:
            raise ValueError(f"invalid status: {status}")
        with self._lock:
            cursor = self._db.execute(
                "UPDATE notes SET status = ? WHERE id = ?", (status, note_id)
            )
            self._db.commit()
        return cursor.rowcount > 0

    def link_contradiction(self, note_id: int, other_ref: str) -> bool:
        """Record that ``note_id`` contradicts ``other_ref`` (e.g. 'note#7')."""
        with self._lock:
            row = self._db.execute(
                "SELECT contradicts FROM notes WHERE id = ?", (note_id,)
            ).fetchone()
            if row is None:
                return False
            contradicts = json.loads(row[0])
            if other_ref not in contradicts:
                contradicts.append(other_ref)
            self._db.execute(
                "UPDATE notes SET contradicts = ?, status = ? WHERE id = ?",
                (json.dumps(contradicts, ensure_ascii=False), STATUS_CONTRADICTED, note_id),
            )
            self._db.commit()
        return True

    # -- reads --------------------------------------------------------------
    def get(self, note_id: int) -> ResearchNote | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return _row_to_note(row) if row else None

    def search_text(self, query: str, limit: int = 5) -> list[tuple[ResearchNote, float]]:
        from .incidents import _fts_query  # shared FTS query builder

        fts = _fts_query(query)
        if not fts:
            return []
        with self._lock:
            rows = self._db.execute(
                "SELECT n.*, notes_fts.rank FROM notes_fts "
                "JOIN notes n ON n.id = notes_fts.rowid "
                "WHERE notes_fts MATCH ? AND n.status != 'archived' "
                "ORDER BY notes_fts.rank LIMIT ?",
                (fts, limit),
            ).fetchall()
        return [(_row_to_note(row[:-1]), float(-row[-1])) for row in rows]

    def all(self, include_archived: bool = False) -> list[ResearchNote]:
        query = "SELECT * FROM notes"
        if not include_archived:
            query += " WHERE status != 'archived'"
        query += " ORDER BY created DESC"
        with self._lock:
            rows = self._db.execute(query).fetchall()
        return [_row_to_note(row) for row in rows]

    def count(self) -> int:
        with self._lock:
            (total,) = self._db.execute("SELECT COUNT(*) FROM notes").fetchone()
        return int(total)

    def close(self) -> None:
        with self._lock:
            self._db.close()


def curate_finding(
    client: LLMClient,
    registry: PromptRegistry,
    *,
    raw_finding: str,
    source_meta: dict,
    budget: BudgetTracker | None = None,
) -> ResearchNote:
    """Run the ``research_note`` prompt and return a validated note proposal."""
    result = run_prompt(
        client,
        registry,
        "research_note",
        {
            "raw_finding": raw_finding,
            "source_meta": json.dumps(source_meta, ensure_ascii=False, sort_keys=True),
        },
        budget=budget,
    )
    data = result.parsed if isinstance(result.parsed, dict) else {}
    trust = data.get("trust", 0.5)
    trust = float(trust) if isinstance(trust, (int, float)) else 0.5
    trust = min(1.0, max(0.0, trust))
    valid_until = data.get("valid_until")
    if not isinstance(valid_until, str) or valid_until.lower() in ("null", "none", ""):
        valid_until = None
    return ResearchNote(
        topic=str(data.get("topic", ""))[:200],
        claim=str(data.get("claim", ""))[:2000],
        source_type=str(data.get("source_type", source_meta.get("type", "docs"))),
        source_url=str(data.get("source_url", source_meta.get("url", ""))),
        retrieved_at=str(data.get("retrieved_at", source_meta.get("retrieved_at", ""))),
        valid_until=valid_until,
        trust=trust,
        repo_scope=str(data.get("repo_scope", "global")),
    )


def _row_to_note(row: tuple) -> ResearchNote:
    return ResearchNote(
        id=row[0],
        topic=row[1],
        claim=row[2],
        source_type=row[3],
        source_url=row[4],
        retrieved_at=row[5],
        valid_until=row[6],
        trust=row[7],
        repo_scope=row[8],
        status=row[9],
        contradicts=json.loads(row[10]),
        created=row[11],
    )
