"""Tool suites: specs, propose-first store, compose-before-create, MCP descriptors.

CORE-PLAN §4. Rules:

- **Compose before create:** before a new tool is built, existing (approved/
  scanned) tools must be checked for composition candidates; LLM drafts are
  instructed to decide compose-vs-create explicitly.
- **Propose-first:** nothing becomes MCP-registrable without scan + gates +
  an explicit approve step (same discipline as skills).
- Tool specs are data: a name, a description, tags, an optional importable
  entrypoint (``module`` + ``entrypoint``) or a composition (``steps``
  referencing other tools), plus gate inputs (``test_command``,
  ``sample_input``).
- **Publication is transactional (§4d):** :meth:`ToolStore.publish` writes the
  registry row AND an entry in the ``publish_outbox`` table in one SQLite
  transaction, so a crash between the commit and the event dispatch leaves no
  invisible capability; :meth:`ToolStore.drain_outbox` is the idempotent
  consumer API and :func:`resolve_tool` answers availability from the registry
  even when the search index is down.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .llm import BudgetExceeded, BudgetTracker, LLMClient
from .promptrun import PromptError, run_prompt
from .prompts import PromptRegistry

STATUS_CANDIDATE = "candidate"
STATUS_SCANNED = "scanned"
STATUS_APPROVED = "approved"
STATUS_REVOKED = "revoked"
VALID_STATUS = {STATUS_CANDIDATE, STATUS_SCANNED, STATUS_APPROVED, STATUS_REVOKED}

KIND_PYTHON = "python"
KIND_COMPOSITION = "composition"


@dataclass
class ToolSpec:
    name: str
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    kind: str = KIND_PYTHON
    module: str = ""
    entrypoint: str = ""
    steps: list[dict] = field(default_factory=list)
    test_command: list[str] = field(default_factory=list)
    sample_input: dict = field(default_factory=dict)
    rationale: str = ""
    provenance: str = "manual"
    status: str = STATUS_CANDIDATE
    version: str = "0.1.0"
    scan_summary: str = ""
    created: float = 0.0
    updated: float = 0.0
    id: int | None = None

    def to_mcp_descriptor(self) -> dict:
        return {
            "name": self.name,
            "description": self.description or self.title,
            "tags": list(self.tags),
            "kind": self.kind,
        }

    def to_dict(self) -> dict:
        payload = dict(self.__dict__)
        return payload


def _tokens(text: str) -> set[str]:
    return {word.lower() for word in re.findall(r"[A-Za-zÄÖÜäöüß_]{3,}", text)}


class ToolStore:
    """SQLite-backed tool-spec memory (thread-safe) + transactional outbox.

    §4d (master prompt): a publication is a registry commit AND a durable
    publication fact. Both are written in the **same** SQLite transaction
    (:meth:`publish`), so a crash between commit and event dispatch never
    leaves an invisible capability: the outbox entry survives and recovery
    drains it (:meth:`drain_outbox`). Consumers are idempotent.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_specs (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    name         TEXT NOT NULL UNIQUE,
                    title        TEXT NOT NULL,
                    description  TEXT NOT NULL DEFAULT '',
                    tags         TEXT NOT NULL DEFAULT '[]',
                    kind         TEXT NOT NULL DEFAULT 'python',
                    module       TEXT NOT NULL DEFAULT '',
                    entrypoint   TEXT NOT NULL DEFAULT '',
                    steps        TEXT NOT NULL DEFAULT '[]',
                    test_command TEXT NOT NULL DEFAULT '[]',
                    sample_input TEXT NOT NULL DEFAULT '{}',
                    rationale    TEXT NOT NULL DEFAULT '',
                    provenance   TEXT NOT NULL DEFAULT 'manual',
                    status       TEXT NOT NULL DEFAULT 'candidate',
                    version      TEXT NOT NULL DEFAULT '0.1.0',
                    scan_summary TEXT NOT NULL DEFAULT '',
                    created      REAL NOT NULL DEFAULT 0,
                    updated      REAL NOT NULL DEFAULT 0
                )
                """
            )
            # §4d publication outbox: same DB, same transaction as the registry
            # write. UNIQUE(name, version) makes a second publish of the very
            # same version a no-op (one row, one event).
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS publish_outbox (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    name    TEXT NOT NULL,
                    version TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created REAL NOT NULL DEFAULT 0,
                    UNIQUE(name, version)
                )
                """
            )
            # consumer acks: PRIMARY KEY(outbox_id, consumer) ⇒ an idempotent
            # consumer can process a drained entry twice without effect.
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS publish_outbox_acks (
                    outbox_id INTEGER NOT NULL,
                    consumer  TEXT NOT NULL,
                    ts        REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (outbox_id, consumer)
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS registry_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._db.commit()

    # -- registry write (shared by upsert and publish) ------------------------
    def _upsert_locked(self, spec: ToolSpec, now: float) -> ToolSpec:
        """Insert or update one spec. NEVER commits — the caller owns the txn."""
        existing = self._get_locked(spec.name)
        if existing is None:
            spec.created = now
            spec.updated = now
            cursor = self._db.execute(
                """
                INSERT INTO tool_specs(name, title, description, tags, kind, module,
                    entrypoint, steps, test_command, sample_input, rationale, provenance,
                    status, version, scan_summary, created, updated)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    spec.name,
                    spec.title,
                    spec.description,
                    json.dumps(spec.tags, ensure_ascii=False),
                    spec.kind,
                    spec.module,
                    spec.entrypoint,
                    json.dumps(spec.steps, ensure_ascii=False),
                    json.dumps(spec.test_command, ensure_ascii=False),
                    json.dumps(spec.sample_input, ensure_ascii=False),
                    spec.rationale,
                    spec.provenance,
                    spec.status,
                    spec.version,
                    spec.scan_summary,
                    spec.created,
                    spec.updated,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("insert into tool_specs did not return a row id")
            spec.id = int(cursor.lastrowid)
        else:
            spec.id = existing.id
            spec.created = existing.created
            spec.updated = now
            self._db.execute(
                """
                UPDATE tool_specs SET title=?, description=?, tags=?, kind=?, module=?,
                    entrypoint=?, steps=?, test_command=?, sample_input=?, rationale=?,
                    provenance=?, status=?, version=?, scan_summary=?, updated=?
                WHERE id=?
                """,
                (
                    spec.title,
                    spec.description,
                    json.dumps(spec.tags, ensure_ascii=False),
                    spec.kind,
                    spec.module,
                    spec.entrypoint,
                    json.dumps(spec.steps, ensure_ascii=False),
                    json.dumps(spec.test_command, ensure_ascii=False),
                    json.dumps(spec.sample_input, ensure_ascii=False),
                    spec.rationale,
                    spec.provenance,
                    spec.status,
                    spec.version,
                    spec.scan_summary,
                    spec.updated,
                    spec.id,
                ),
            )
        return spec

    def upsert(self, spec: ToolSpec, ts: float | None = None) -> ToolSpec:
        now = time.time() if ts is None else ts
        with self._lock, self._db:
            return self._upsert_locked(spec, now)

    # -- §4d publication -----------------------------------------------------
    def _revision_locked(self) -> int:
        row = self._db.execute(
            "SELECT value FROM registry_meta WHERE key = 'registry_revision'"
        ).fetchone()
        return int(row[0]) if row else 0

    def _bump_revision_locked(self) -> int:
        revision = self._revision_locked() + 1
        self._db.execute(
            "INSERT INTO registry_meta(key, value) VALUES ('registry_revision', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(revision),),
        )
        return revision

    def registry_revision(self) -> int:
        """Monotonic revision of the authorized registry (cache invalidation, B9).

        Bumped on a publication and on every status change (:meth:`set_status`).
        Direct :meth:`upsert` calls (propose/candidate path) do not bump it —
        they are not publications; a consumer that must catch those too reads
        the spec table itself.
        """
        with self._lock:
            return self._revision_locked()

    def publish(
        self,
        spec: ToolSpec,
        *,
        payload: dict | None = None,
        ts: float | None = None,
    ) -> dict:
        """Publish a spec: registry row + outbox entry in ONE transaction (§4d).

        Idempotent per ``(name, version)``: publishing the same name+version
        again writes no second outbox row (``duplicate=True``) and the caller
        must not emit a second event for it.
        """
        now = time.time() if ts is None else ts
        blob = json.dumps(payload or {}, ensure_ascii=False, default=str)
        with self._lock:
            with self._db:  # commit on success, rollback on any failure
                self._upsert_locked(spec, now)
                row = self._db.execute(
                    "SELECT id FROM publish_outbox WHERE name = ? AND version = ?",
                    (spec.name, spec.version),
                ).fetchone()
                duplicate = row is not None
                if duplicate:
                    outbox_id = int(row[0])
                else:
                    cursor = self._db.execute(
                        "INSERT INTO publish_outbox(name, version, payload, created) "
                        "VALUES (?,?,?,?)",
                        (spec.name, spec.version, blob, now),
                    )
                    if cursor.lastrowid is None:
                        raise RuntimeError("insert into publish_outbox returned no row id")
                    outbox_id = int(cursor.lastrowid)
                revision = self._bump_revision_locked()
            return {
                "name": spec.name,
                "version": spec.version,
                "spec_id": spec.id,
                "outbox_id": outbox_id,
                "duplicate": duplicate,
                "revision": revision,
                "created": not duplicate,
            }

    @staticmethod
    def _outbox_row(row: tuple) -> dict:
        try:
            payload = json.loads(row[3])
        except json.JSONDecodeError:
            payload = {}
        return {
            "id": int(row[0]),
            "name": row[1],
            "version": row[2],
            "payload": payload,
            "created": row[4],
        }

    def outbox_entries(self, *, limit: int = 200) -> list[dict]:
        """All publication facts, oldest first (audit view)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, name, version, payload, created FROM publish_outbox "
                "ORDER BY id LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._outbox_row(row) for row in rows]

    def pending_outbox(self, consumer: str = "", *, limit: int = 200) -> list[dict]:
        """Publication facts a consumer has not acked yet (``""`` = none acked)."""
        with self._lock:
            if consumer:
                rows = self._db.execute(
                    "SELECT o.id, o.name, o.version, o.payload, o.created "
                    "FROM publish_outbox o WHERE NOT EXISTS ("
                    "SELECT 1 FROM publish_outbox_acks a "
                    "WHERE a.outbox_id = o.id AND a.consumer = ?) ORDER BY o.id LIMIT ?",
                    (consumer, int(limit)),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT id, name, version, payload, created FROM publish_outbox "
                    "ORDER BY id LIMIT ?",
                    (int(limit),),
                ).fetchall()
        return [self._outbox_row(row) for row in rows]

    def drain_outbox(
        self, consumer: str, *, limit: int = 200, ack: bool = True
    ) -> list[dict]:
        """Idempotent consumer API: hand out un-acked publication facts.

        With ``ack=True`` (default) the entries are acked in the same call, so a
        second drain returns nothing new — double processing is harmless. A
        consumer that must emit an event first uses ``ack=False`` and calls
        :meth:`ack_outbox` afterwards (at-least-once, never silence).
        """
        entries = self.pending_outbox(consumer, limit=limit)
        if ack and entries:
            self.ack_outbox(consumer, [entry["id"] for entry in entries])
        return entries

    def ack_outbox(self, consumer: str, outbox_ids: list[int]) -> int:
        """Ack entries a consumer processed (idempotent: re-acking is a no-op)."""
        acked = 0
        with self._lock, self._db:
            for entry_id in outbox_ids:
                cursor = self._db.execute(
                    "INSERT OR IGNORE INTO publish_outbox_acks(outbox_id, consumer, ts) "
                    "VALUES (?,?,?)",
                    (int(entry_id), consumer, time.time()),
                )
                acked += cursor.rowcount
        return acked

    def acked_outbox(self, consumer: str) -> list[int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT outbox_id FROM publish_outbox_acks WHERE consumer = ? "
                "ORDER BY outbox_id",
                (consumer,),
            ).fetchall()
        return [int(row[0]) for row in rows]

    def get(self, name: str) -> ToolSpec | None:
        with self._lock:
            return self._get_locked(name)

    def _get_locked(self, name: str) -> ToolSpec | None:
        row = self._db.execute("SELECT * FROM tool_specs WHERE name = ?", (name,)).fetchone()
        return _row_to_spec(row) if row else None

    def set_status(self, name: str, status: str, *, scan_summary: str | None = None) -> bool:
        if status not in VALID_STATUS:
            raise ValueError(f"invalid status: {status}")
        with self._lock, self._db:
            if scan_summary is None:
                cursor = self._db.execute(
                    "UPDATE tool_specs SET status = ?, updated = ? WHERE name = ?",
                    (status, time.time(), name),
                )
            else:
                cursor = self._db.execute(
                    "UPDATE tool_specs SET status = ?, scan_summary = ?, updated = ? "
                    "WHERE name = ?",
                    (status, scan_summary, time.time(), name),
                )
            if cursor.rowcount > 0:
                # a status change moves the authorized surface (§4d revision).
                self._bump_revision_locked()
        return cursor.rowcount > 0

    def all(self) -> list[ToolSpec]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM tool_specs ORDER BY name").fetchall()
        return [_row_to_spec(row) for row in rows]

    def by_status(self, status: str) -> list[ToolSpec]:
        return [spec for spec in self.all() if spec.status == status]

    def count(self) -> int:
        with self._lock:
            (total,) = self._db.execute("SELECT COUNT(*) FROM tool_specs").fetchone()
        return int(total)

    def close(self) -> None:
        with self._lock:
            self._db.close()


def compose_candidates_scored(
    store: ToolStore,
    *,
    description: str = "",
    tags: list[str] | None = None,
    limit: int = 5,
    min_score: int = 1,
    ignore: set[str] | None = None,
) -> list[tuple[int, ToolSpec]]:
    """Ranked ``(overlap, spec)`` pairs — the scoring seam behind compose-first.

    The score is the raw number of shared terms, so callers can apply their
    own policy (the builder: 3 terms = duplicate → refuse, 2 = hint only).
    ``ignore`` names terms that must not count as evidence (the builder passes
    capability_mining's boilerplate vocabulary, so "inspect/verify/state"
    never add up to a duplicate).
    """
    wanted = _tokens(description) | {tag.lower() for tag in (tags or [])}
    if ignore:
        wanted -= {str(term).lower() for term in ignore}
    if not wanted:
        return []
    floor = max(1, int(min_score))
    scored: list[tuple[int, ToolSpec]] = []
    for spec in store.all():
        if spec.status not in (STATUS_APPROVED, STATUS_SCANNED):
            continue
        have = _tokens(" ".join([spec.name, spec.title, spec.description, " ".join(spec.tags)]))
        score = len(wanted & have)
        if score >= floor:
            scored.append((score, spec))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    return scored[:limit]


def compose_candidates(
    store: ToolStore,
    *,
    description: str = "",
    tags: list[str] | None = None,
    limit: int = 5,
    min_score: int = 1,
    ignore: set[str] | None = None,
) -> list[ToolSpec]:
    """Rank existing tools that overlap the needed capability (compose first)."""
    return [
        spec
        for _score, spec in compose_candidates_scored(
            store,
            description=description,
            tags=tags,
            limit=limit,
            min_score=min_score,
            ignore=ignore,
        )
    ]


def propose_tool(store: ToolStore, spec: ToolSpec) -> tuple[ToolSpec, list[ToolSpec]]:
    """Store a proposal and report composition candidates (compose before create)."""
    composables = [
        item
        for item in compose_candidates(
            store,
            description=f"{spec.title} {spec.description}",
            tags=spec.tags,
        )
        if item.name != spec.name
    ]
    stored = store.upsert(spec)
    return stored, composables


def validate_composition(store: ToolStore, spec: ToolSpec) -> list[str]:
    """Return problems with a composition spec ([] = ok)."""
    problems: list[str] = []
    if not spec.steps:
        problems.append("composition without steps")
    for step in spec.steps:
        name = str(step.get("tool", ""))
        target = store.get(name) if name else None
        if target is None:
            problems.append(f"unknown tool in step: {name or '(empty)'}")
        elif target.status == STATUS_REVOKED:
            problems.append(f"tool revoked: {name}")
    return problems


@dataclass
class ToolDraft:
    """Outcome of an LLM draft: the spec, or WHY it failed (§1.8).

    ``kind`` is ``""`` on success and otherwise a coarse reason:
    ``"budget"`` (BudgetExceeded — the bounded build budget ran out),
    ``"invalid"`` (no parseable/nameable spec), ``"llm"`` (client/transport).
    A budget abort is a terminal error state — never a half-built tool.
    """

    spec: ToolSpec | None = None
    error: str = ""
    kind: str = ""
    usage: dict[str, int] | None = None

    @property
    def ok(self) -> bool:
        return self.spec is not None


def _budget_usage(budget: BudgetTracker | None) -> dict[str, int] | None:
    return dict(budget.usage) if budget is not None else None


def draft_tool_spec_result(
    client: LLMClient,
    registry: PromptRegistry,
    *,
    need: str,
    existing_tools: list[str],
    budget: BudgetTracker | None = None,
) -> ToolDraft:
    """Draft a tool spec (compose-first) and report the real failure reason.

    Uses the injected client + budget only — there is no second provider
    configuration and no fallback client: no client access is the caller's
    blocker to report, not something to paper over.
    """
    try:
        result = run_prompt(
            client,
            registry,
            "tool_spec_draft",
            {"need": need, "existing_tools": "\n".join(existing_tools) or "(none)"},
            budget=budget,
        )
    except BudgetExceeded as exc:
        return ToolDraft(
            error=f"build budget exceeded: {exc}", kind="budget", usage=_budget_usage(budget)
        )
    except PromptError as exc:
        return ToolDraft(
            error=f"draft was not valid JSON: {exc}", kind="invalid", usage=_budget_usage(budget)
        )
    except Exception as exc:  # client/transport failure — reported, never invented
        return ToolDraft(
            error=f"draft call failed: {type(exc).__name__}: {exc}",
            kind="llm",
            usage=_budget_usage(budget),
        )
    data = result.parsed if isinstance(result.parsed, dict) else {}
    name = str(data.get("name", "")).strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,39}", name):
        return ToolDraft(
            error=f"draft rejected: name {name!r} is not a valid tool name",
            kind="invalid",
            usage=_budget_usage(budget),
        )
    kind = str(data.get("kind", KIND_PYTHON))
    if kind not in (KIND_PYTHON, KIND_COMPOSITION):
        kind = KIND_PYTHON
    steps = data.get("steps") if isinstance(data.get("steps"), list) else []
    steps = [step for step in steps if isinstance(step, dict)][:10]
    spec = ToolSpec(
        name=name,
        title=str(data.get("title", name))[:120],
        description=str(data.get("description", ""))[:500],
        tags=[str(tag) for tag in (data.get("tags") or [])][:8],
        kind=kind,
        steps=steps,
        rationale=str(data.get("rationale", ""))[:300],
        provenance="llm:tool_spec_draft",
    )
    return ToolDraft(spec=spec, usage=_budget_usage(budget))


def draft_tool_spec(
    client: LLMClient,
    registry: PromptRegistry,
    *,
    need: str,
    existing_tools: list[str],
    budget: BudgetTracker | None = None,
) -> ToolSpec | None:
    """LLM draft of a tool spec (compose-first); None on any failure.

    Thin seam over :func:`draft_tool_spec_result` for callers that only want
    the spec; the builder uses the result form to surface the exact blocker.
    """
    return draft_tool_spec_result(
        client, registry, need=need, existing_tools=existing_tools, budget=budget
    ).spec


def resolve_tool(
    store: ToolStore,
    name: str,
    *,
    index: Callable[[str], dict | None] | None = None,
) -> dict:
    """Resolve a tool for use — **availability never depends on the index** (§4d).

    The registry (``tools.db``) is the source of truth. A search index (B9) is
    only an accelerator that may propose a name; if it is unavailable or stale,
    the answer comes from the registry instead of an outage:

    * ``via="index"``            — the index proposed the name, the registry has it,
    * ``via="registry-fallback"` — the index failed (or pointed nowhere useful);
      the registry answered anyway (``index_error`` names the reason),
    * ``found=False``            — genuinely unknown (the registry says so).
    """
    index_error = ""
    hit_name = ""
    if index is not None:
        try:
            proposal = index(name)
            if isinstance(proposal, dict):
                hit_name = str(proposal.get("name") or "")
            elif isinstance(proposal, str):
                hit_name = str(proposal)
        except Exception as exc:  # a broken accelerator is not an outage
            index_error = f"{type(exc).__name__}: {exc}"
    for candidate in (hit_name, str(name)):
        if not candidate:
            continue
        spec = store.get(candidate)
        if spec is not None:
            used_index = bool(hit_name) and candidate == hit_name and not index_error
            return {
                "found": True,
                "name": spec.name,
                "status": spec.status,
                "kind": spec.kind,
                "version": spec.version,
                "via": "index" if used_index else "registry-fallback",
                "index_error": index_error,
            }
    return {
        "found": False,
        "name": str(name),
        "status": "",
        "kind": "",
        "version": "",
        "via": "registry-fallback" if index_error else "registry",
        "index_error": index_error,
    }


def _row_to_spec(row: tuple) -> ToolSpec:
    return ToolSpec(
        id=row[0],
        name=row[1],
        title=row[2],
        description=row[3],
        tags=json.loads(row[4]),
        kind=row[5],
        module=row[6],
        entrypoint=row[7],
        steps=json.loads(row[8]),
        test_command=json.loads(row[9]),
        sample_input=json.loads(row[10]),
        rationale=row[11],
        provenance=row[12],
        status=row[13],
        version=row[14],
        scan_summary=row[15],
        created=row[16],
        updated=row[17],
    )
