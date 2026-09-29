"""Markdown knowledge store with metadata, search, and versioned line reads.

The plan's INFO-01..04 requirements: small searchable Markdown units with
frontmatter metadata, bounded FTS5 search, version-bound line reads with
``STALE_REFERENCE`` on mismatch, and freshness checking on retrieval.

Layout under ``<runtime>/knowledge/``::

    knowledge/
      <scope>/<id>.md          # the canonical Markdown body + YAML frontmatter
      knowledge.db             # SQLite FTS5 index (derived, rebuilt on write)

Every entry has a stable ``id``, ``kind``, ``scope``, ``status``, and a
content ``revision`` (sha256 of the body after frontmatter).  The index is
derived data — deleting it and re-indexing must produce the same state.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "KnowledgeEntry",
    "KnowledgeStore",
    "KINDS",
    "STATUSES",
    "STALE_REFERENCE",
    "LINE_TOO_LARGE",
]

KINDS = ("procedure", "decision", "failure", "fact", "preference", "reference")
STATUSES = ("candidate", "verified", "stale", "superseded", "rejected")

#: Returned when ``expected_revision`` does not match the stored revision.
STALE_REFERENCE = "STALE_REFERENCE"
#: Returned when a single line exceeds ``max_output_tokens``.
LINE_TOO_LARGE = "LINE_TOO_LARGE"


@dataclass
class KnowledgeEntry:
    """One Markdown knowledge unit with metadata."""

    id: str
    title: str
    kind: str = "fact"
    scope: str = "global"
    status: str = "candidate"
    body: str = ""
    path: str = ""
    revision: str = ""
    updated_at: str = ""
    verified_at: str = ""
    valid_until: str = ""
    sources: list[str] = field(default_factory=list)
    applies_to: dict[str, str] = field(default_factory=dict)

    @property
    def is_stale(self) -> bool:
        if not self.valid_until:
            return False
        return self.valid_until < time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "scope": self.scope,
            "status": self.status,
            "revision": self.revision,
            "path": self.path,
            "updated_at": self.updated_at,
            "verified_at": self.verified_at,
            "valid_until": self.valid_until,
            "is_stale": self.is_stale,
            "sources": list(self.sources),
            "applies_to": dict(self.applies_to),
        }


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split YAML frontmatter from body.  Returns ``(meta, body)``."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    raw = text[4:end]
    body = text[end + 5:]
    meta: dict[str, Any] = {}
    current_key = ""
    for line in raw.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = re.match(r"^(\w[\w_-]*):\s*(.*)$", line)
        if m:
            current_key = m.group(1)
            value = m.group(2).strip()
            if value.startswith("[") and value.endswith("]"):
                meta[current_key] = [
                    v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()
                ]
            elif value.startswith("{") and value.endswith("}"):
                inner = value[1:-1]
                meta[current_key] = {}
                for pair in inner.split(","):
                    if ":" in pair:
                        k, v = pair.split(":", 1)
                        meta[current_key][k.strip().strip("'\"")] = v.strip().strip("'\"")
            else:
                meta[current_key] = value.strip("'\"") if value else ""
        elif line.startswith("  ") and current_key:
            # continuation of a list
            item = line.strip().lstrip("- ").strip("'\"")
            if isinstance(meta.get(current_key), list):
                meta[current_key].append(item)
    return meta, body


def _make_frontmatter(entry: KnowledgeEntry) -> str:
    lines = ["---"]
    lines.append(f"id: {entry.id}")
    lines.append(f"title: {entry.title}")
    lines.append(f"kind: {entry.kind}")
    lines.append(f"scope: {entry.scope}")
    lines.append(f"status: {entry.status}")
    lines.append(f"updated_at: {entry.updated_at}")
    if entry.verified_at:
        lines.append(f"verified_at: {entry.verified_at}")
    if entry.valid_until:
        lines.append(f"valid_until: {entry.valid_until}")
    if entry.sources:
        lines.append(f"sources: [{', '.join(entry.sources)}]")
    if entry.applies_to:
        inner = ", ".join(f"{k}: {v}" for k, v in entry.applies_to.items())
        lines.append(f"applies_to: {{{inner}}}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class KnowledgeStore:
    """SQLite-backed knowledge index + Markdown file storage."""

    def __init__(self, runtime: str | Path | None = None):
        if runtime:
            base = Path(runtime).expanduser()
        else:
            from .registry import default_runtime_dir

            base = Path(default_runtime_dir())
        self.root = base / "knowledge"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "knowledge.db"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS entries (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'fact',
                    scope TEXT NOT NULL DEFAULT 'global',
                    status TEXT NOT NULL DEFAULT 'candidate',
                    path TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT '',
                    verified_at TEXT NOT NULL DEFAULT '',
                    valid_until TEXT NOT NULL DEFAULT '',
                    sources TEXT NOT NULL DEFAULT '[]',
                    applies_to TEXT NOT NULL DEFAULT '{}'
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
                    title, body, kind, scope
                );
            """)

    def _entry_path(self, entry_id: str, scope: str) -> Path:
        safe_scope = re.sub(r"[^\w-]", "_", scope) or "global"
        return self.root / safe_scope / f"{entry_id}.md"

    def upsert(self, entry: KnowledgeEntry) -> KnowledgeEntry:
        """Write a knowledge entry to disk + index.  Returns the stored entry."""
        entry.revision = _sha256(entry.body)
        if not entry.updated_at:
            entry.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        path = self._entry_path(entry.id, entry.scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry.path = str(path)
        text = _make_frontmatter(entry) + entry.body
        path.write_text(text, encoding="utf-8")
        import json

        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO entries
                   (id, title, kind, scope, status, path, revision,
                    updated_at, verified_at, valid_until, sources, applies_to)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.id, entry.title, entry.kind, entry.scope, entry.status,
                    entry.path, entry.revision, entry.updated_at, entry.verified_at,
                    entry.valid_until, json.dumps(entry.sources),
                    json.dumps(entry.applies_to),
                ),
            )
            # remove old FTS row and insert fresh
            conn.execute(
                "DELETE FROM entries_fts WHERE rowid IN "
                "(SELECT rowid FROM entries WHERE id = ?)",
                (entry.id,),
            )
            rowid = conn.execute(
                "SELECT rowid FROM entries WHERE id = ?", (entry.id,)
            ).fetchone()
            if rowid:
                conn.execute(
                    "INSERT INTO entries_fts(rowid, title, body, kind, scope) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (rowid[0], entry.title, entry.body, entry.kind, entry.scope),
                )
        return entry

    def get(self, entry_id: str) -> KnowledgeEntry | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    def _row_to_entry(self, row: sqlite3.Row) -> KnowledgeEntry:
        path = Path(row["path"])
        body = ""
        if path.exists():
            text = path.read_text(encoding="utf-8")
            _, body = _parse_frontmatter(text)
        import json

        return KnowledgeEntry(
            id=row["id"],
            title=row["title"],
            kind=row["kind"],
            scope=row["scope"],
            status=row["status"],
            body=body,
            path=row["path"],
            revision=row["revision"],
            updated_at=row["updated_at"],
            verified_at=row["verified_at"],
            valid_until=row["valid_until"],
            sources=json.loads(row["sources"]) if row["sources"] else [],
            applies_to=json.loads(row["applies_to"]) if row["applies_to"] else {},
        )

    def search(
        self,
        query: str,
        *,
        scope: str = "",
        kinds: list[str] | None = None,
        statuses: list[str] | None = None,
        limit: int = 5,
        max_output_tokens: int = 1200,
    ) -> dict[str, Any]:
        """Bounded FTS5 search.  Returns matches with snippets and line ranges."""
        conditions = ["entries_fts MATCH ?"]
        params: list[Any] = [query]
        if scope:
            conditions.append("e.scope = ?")
            params.append(scope)
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            conditions.append(f"e.kind IN ({placeholders})")
            params.extend(kinds)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            conditions.append(f"e.status IN ({placeholders})")
            params.extend(statuses)
        where = " AND ".join(conditions)
        sql = f"""
            SELECT e.*, snippet(entries_fts, 1, '[', ']', '…', 12) AS snip,
                   rank
            FROM entries_fts
            JOIN entries e ON e.rowid = entries_fts.rowid
            WHERE {where}
            ORDER BY rank
            LIMIT ?
        """
        params.append(limit)
        with self._connect() as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                return {"matches": [], "truncated": False, "next_cursor": None}
        matches = []
        budget = max_output_tokens * 4  # rough chars
        truncated = False
        for row in rows:
            entry = self._row_to_entry(row)
            snippet_text = row["snip"] or ""
            body_lines = entry.body.splitlines()
            # find the snippet's line range in the body
            start_line, end_line = 1, min(len(body_lines), 5)
            for i, line in enumerate(body_lines):
                if snippet_text and (
                    snippet_text.replace("[", "").replace("]", "").replace("…", "")[:40] in line
                ):

                    start_line = i + 1
                    end_line = min(i + 3, len(body_lines))
                    break
            match = {
                "id": entry.id,
                "path": entry.path,
                "revision": entry.revision,
                "title": entry.title,
                "kind": entry.kind,
                "status": entry.status,
                "is_stale": entry.is_stale,
                "start_line": start_line,
                "end_line": end_line,
                "snippet": snippet_text,
                "evidence_ids": entry.sources,
            }
            cost = len(snippet_text) + 80
            if budget - cost < 0:
                truncated = True
                break
            budget -= cost
            matches.append(match)
        return {"matches": matches, "truncated": truncated, "next_cursor": None}

    def read_lines(
        self,
        entry_id: str,
        *,
        expected_revision: str = "",
        start_line: int = 1,
        end_line: int = 0,
        max_output_tokens: int = 1200,
    ) -> dict[str, Any]:
        """Read a line range from a specific version of a knowledge entry.

        Returns ``STALE_REFERENCE`` if ``expected_revision`` does not match.
        """
        entry = self.get(entry_id)
        if entry is None:
            return {"error": "NOT_FOUND", "id": entry_id}
        if expected_revision and expected_revision != entry.revision:
            return {
                "error": STALE_REFERENCE,
                "id": entry_id,
                "revision": entry.revision,
                "message": f"expected {expected_revision[:16]}…, found {entry.revision[:16]}…",
            }
        lines = entry.body.splitlines()
        total = len(lines)
        if total == 0:
            return {
                "id": entry_id,
                "revision": entry.revision,
                "total_lines": 0,
                "returned_start_line": 0,
                "returned_end_line": 0,
                "lines": [],
                "truncated": False,
                "next_start_line": None,
            }
        start = max(1, start_line)
        end = min(total, end_line) if end_line > 0 else total
        if start > total:
            return {
                "error": "INVALID_RANGE",
                "id": entry_id,
                "total_lines": total,
                "message": f"start_line {start} exceeds total_lines {total}",
            }
        budget = max_output_tokens * 4
        max_line = budget
        result_lines = []
        truncated = False
        next_start = None
        for i in range(start - 1, end):
            text = lines[i]
            if len(text) > max_line:
                return {
                    "error": LINE_TOO_LARGE,
                    "id": entry_id,
                    "line_number": i + 1,
                    "line_length": len(text),
                    "message": "single line exceeds output budget",
                }
            if budget - len(text) < 0:
                truncated = True
                next_start = i + 1
                break
            budget -= len(text)
            result_lines.append({"number": i + 1, "text": text})
        return {
            "id": entry_id,
            "revision": entry.revision,
            "total_lines": total,
            "returned_start_line": result_lines[0]["number"] if result_lines else 0,
            "returned_end_line": result_lines[-1]["number"] if result_lines else 0,
            "lines": result_lines,
            "truncated": truncated,
            "next_start_line": next_start,
        }

    def answer(
        self,
        question: str,
        entry_ids: list[str] | None = None,
        *,
        max_lines: int = 6,
        max_output_tokens: int = 1200,
    ) -> dict[str, Any]:
        """Answer a short question from **selected** entries, with original references.

        MEM-03: "Zusätzlich kann eine kurze Frage auf ausgewählte Dokumente beantwortet
        werden: Antwort mit Originalreferenzen, kein erneutes Einlesen der ganzen
        Datenbank."

        Two rules shape the answer:

        * **Only the named entries are read.** Passing no ids is refused rather than
          silently falling back to the whole base — "kein erneutes Einlesen der ganzen
          Datenbank" is the point, not a hint.
        * **Every claim carries its origin.** A sentence in the answer is paired with
          ``entry_id:line_start-line_end@revision``. That is what makes it checkable.

        The answer is *extractive*: it quotes the lines that actually match the question
        rather than generating prose over them. When nothing matches, it says so — an
        invented answer would be worse than no answer here.
        """
        question = str(question or "").strip()
        if not question:
            return {"error": "usage", "message": "a question is required"}
        if not entry_ids:
            return {
                "error": "usage",
                "message": ("name the entries to read (entry_ids) — answering would "
                            "otherwise re-read the whole database, which MEM-03 forbids"),
            }

        wanted = list(dict.fromkeys(str(e) for e in entry_ids if str(e).strip()))
        from .memory import lexical_overlap

        findings: list[dict[str, Any]] = []
        missing: list[str] = []

        for entry_id in wanted:
            entry = self.get(entry_id)
            if entry is None:
                missing.append(entry_id)
                continue
            lines = entry.body.splitlines()
            scored: list[tuple[float, int, str]] = []
            for index, line in enumerate(lines, start=1):
                score = lexical_overlap(question, line)
                if score > 0.0:
                    scored.append((score, index, line))
            scored.sort(key=lambda item: (-item[0], item[1]))
            for score, index, line in scored[:max_lines]:
                findings.append({
                    "text": line.strip(),
                    "score": round(score, 4),
                    "reference": f"{entry_id}:{index}-{index}@{entry.revision}",
                    "entry_id": entry_id,
                    "title": entry.title,
                    "kind": entry.kind,
                    "scope": entry.scope,
                    "revision": entry.revision,
                    "line_start": index,
                    "line_end": index,
                })

        findings.sort(key=lambda item: -item["score"])
        findings = findings[:max_lines]

        answer_text = " ".join(f["text"] for f in findings)[:max_output_tokens * 4]
        return {
            "question": question,
            "entry_ids": wanted,
            "read_entries": [f for f in dict.fromkeys(f["entry_id"] for f in findings)],
            "missing_entries": missing,
            "answer": answer_text if findings else "",
            "answered": bool(findings),
            "kind": "extractive",
            "references": [
                {
                    "reference": f["reference"],
                    "entry_id": f["entry_id"],
                    "title": f["title"],
                    "revision": f["revision"],
                    "line_start": f["line_start"],
                    "line_end": f["line_end"],
                    "text": f["text"],
                }
                for f in findings
            ],
            "note": (
                "extractive answer: these are the matching lines from the named entries "
                "with their original references, not generated prose"
                if findings else
                "no line in the named entries matches this question — nothing is invented"
            ),
            "queried_only": wanted,
        }

    def list_entries(self, *, scope: str = "", kind: str = "", status: str = "") -> list[dict]:
        conditions = []
        params: list[Any] = []
        if scope:
            conditions.append("scope = ?")
            params.append(scope)
        if kind:
            conditions.append("kind = ?")
            params.append(kind)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, title, kind, scope, status, revision, updated_at, valid_until "
                f"FROM entries {where} ORDER BY updated_at DESC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]
