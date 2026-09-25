"""Typed long-term memory store — the STORE side (MVP/31).

What this is
------------
The decision side of long-term memory already exists: ``jev.decide`` answers a
typed ``memory_injection`` request (``inject_now`` / ``defer`` / ``drop``, with a
budget and a per-candidate relevance). What was missing is the *store* it decides
over: today's memory is an incident table plus a note table, retrieved with
``memory-search`` — a flat, untyped fact list where "last write wins" is the only
tie-break and nothing knows whether a row still holds.

This module adds that store, **beside** the existing modules, not instead of them:

* ``incidents.py`` / ``notes.py`` / ``retrieval.py`` / ``memory.py`` keep their
  jobs (incident + research store, the applicability gate, ``memory-search``)
  and are not touched. Nothing here rewrites them.
* Typed memories live in their own SQLite file (default
  ``$RUNTIME/typed_memory.db``), so the 16-table ``memory.db`` and the audit
  that counts its tables are unaffected. ``--db`` points either command at any
  file; the two stores can also point at the *same* file without colliding
  (the tables are namespaced ``typed_memories`` / ``memory_journal`` /
  ``consolidation_runs`` / ``memory_vectors`` / ``memory_store_meta``).
* Similarity is **reused, not reinvented**: ``memory.similarity``,
  ``memory.lexical_overlap`` and ``embedding.HashingEmbeddings`` (via
  ``memory.vector_score``). ``embedding.py`` was verified usable before
  building on it — see ``verify_embedding_provider`` and MVP/31 §"measured
  limits": it is a **lexical** trigram-hash provider, not semantic.
* The ranked K can be emitted in the shape of the Jev
  ``MemoryInjectionRequest`` candidates (:func:`TypedMemoryStore.jev_offers`), so
  the façade keeps its contract and only the store changed.

The three properties that make this worth having over a flat fact list
----------------------------------------------------------------------
1. **Typed + scoped + provenance-carrying.** Every memory has a kind, a
   provenance record (source, refs, session, note), a confidence, a scope
   (``global`` / ``project:<id>`` / ``session:<id>``) and a decay policy. A fact
   and an incident are not the same animal: they decay differently and they are
   trusted differently.
2. **Consolidation with a documented resolution rule.** Duplicates merge,
   contradictions are *detected* and then resolved by an ordered, deterministic
   rule (owner-authored wins → weighted score wins → confidence wins → newest
   wins → **otherwise flag, never guess**). See :func:`resolve_contradiction`.
3. **Retrieval that ranks on more than text.** Relevance is combined with
   recency decay, confidence and scope match, each hit carries its own
   explanation, and the policy (TTL) is *enforced*: an expired memory is not
   returned. The measured precision of that ranking, against two baselines, is
   in MVP/31 — including the queries it gets wrong.

No silent writes
----------------
Every mutation writes an append-only ``memory_journal`` row in the same
transaction as the change (:meth:`TypedMemoryStore._write` is the only writer).
:meth:`TypedMemoryStore.journal_consistency` reports any mutated memory that has
no journal row, so "no silent writes" is a checkable claim, not a promise.
``memory consolidate`` is a dry run unless ``--apply`` is given, following the
``nightshift`` convention already in this CLI.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .embedding import DIM, HashingEmbeddings, cosine  # noqa: F401  (DIM: contract re-export)
from .memory import lexical_overlap, similarity, tokens, vector_score

# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #

KINDS: tuple[str, ...] = ("fact", "preference", "decision", "incident", "project", "entity")

SCOPE_GLOBAL = "global"
SCOPE_KINDS: tuple[str, ...] = ("global", "project", "session")

STATUS_ACTIVE = "active"
STATUS_MERGED = "merged"
STATUS_SUPERSEDED = "superseded"
STATUS_CONTRADICTED = "contradicted"
STATUS_FORGOTTEN = "forgotten"
STATUS_EXPIRED = "expired"  # computed, never stored

#: Retrieval weights. Documented because the ranking must be arguable, not magic.
#: They sum to 1.0; change them here and the measurement in MVP/31 stops applying.
W_RELEVANCE = 0.55
W_RECENCY = 0.15
W_CONFIDENCE = 0.15
W_SCOPE = 0.15

DEFAULT_K = 3

#: A hit scoped to another project/session still *shows up* (it is not a lie to
#: know it), but it is scored near zero and ``--strict-scope`` removes it.
SCOPE_MISMATCH_FACTOR = 0.10
#: A flagged contradiction is returned, visibly penalised — unresolved means
#: "usable but suspect", never "silently trusted".
CONTRADICTED_FACTOR = 0.60
#: A global memory answers a project-scoped question: it applies, but it is not
#: the scoped answer. Beats a mismatch, loses to an exact scope.
SCOPE_GLOBAL_FACTOR = 0.70

#: Near-duplicate merge threshold on ``memory.similarity`` (lexical overlap or
#: 0.75 × trigram cosine). Measured on the synthetic corpus in MVP/31.
DEFAULT_NEAR_DUP = 0.80

#: A tie-break is only "decisive" above this gap. Below it, flag for a human
#: instead of letting a float pick the truth.
EVIDENCE_DECISIVE = 0.20
CONFIDENCE_DECISIVE = 0.15
NEWEST_DECISIVE_SECONDS = 60.0

#: Decay: how long a kind stays "fresh" (recency factor halves every half-life).
DEFAULT_HALF_LIFE_DAYS: dict[str, float] = {
    "fact": 180.0,
    "preference": 365.0,
    "decision": 365.0,
    "incident": 30.0,
    "project": 90.0,
    "entity": 180.0,
}

#: Hard expiry, in days, per kind. 0 = never expires. Only incidents get one by
#: default: an operational trace goes stale, an owner preference does not.
DEFAULT_TTL_DAYS: dict[str, float] = {
    "fact": 0.0,
    "preference": 0.0,
    "decision": 0.0,
    "incident": 180.0,
    "project": 0.0,
    "entity": 0.0,
}

#: Where a memory came from, and how much that source is worth when two memories
#: contradict each other. ``owner`` is the R4 case: owner-authored memory is
#: authoritative and is never dropped by a model.
SOURCE_WEIGHT: dict[str, float] = {
    "owner": 1.00,
    "review": 0.70,
    "gate": 0.70,
    "agent": 0.50,
    "import": 0.30,
    "guess": 0.20,
}

MAX_TEXT = 2000

SCHEMA_VERSION = "1.0"

__all__ = [
    "CONFIDENCE_DECISIVE",
    "ConsolidationReport",
    "CorpusPlan",
    "DEFAULT_HALF_LIFE_DAYS",
    "DEFAULT_K",
    "DEFAULT_NEAR_DUP",
    "DEFAULT_TTL_DAYS",
    "EVIDENCE_DECISIVE",
    "Hit",
    "KINDS",
    "Memory",
    "MemoryNotFound",
    "MemoryStoreError",
    "MemoryValidationError",
    "RankingMeasurement",
    "SOURCE_WEIGHT",
    "SCOPE_GLOBAL",
    "STATUS_ACTIVE",
    "STATUS_CONTRADICTED",
    "STATUS_EXPIRED",
    "STATUS_FORGOTTEN",
    "STATUS_MERGED",
    "STATUS_SUPERSEDED",
    "TypedMemoryStore",
    "build_synthetic_corpus",
    "contradiction_reason",
    "decay_factor",
    "effective_score",
    "evidence_weight",
    "measure_ranking",
    "normalize_subject",
    "normalize_text",
    "polarity",
    "resolve_contradiction",
    "split_subject",
    "validate_embedding_provider",
    "values_in",
]


class MemoryStoreError(RuntimeError):
    """Base class for the store's refusals."""


class MemoryValidationError(MemoryStoreError):
    """The caller's input is not a valid typed memory (exit 1 = usage)."""


class MemoryNotFound(MemoryStoreError):
    """No such memory id in this store (exit 5 = not found)."""


# --------------------------------------------------------------------------- #
# text helpers — deterministic, no model required
# --------------------------------------------------------------------------- #


def normalize_text(text: str) -> str:
    """Lowercase, punctuation-free, single-spaced — the comparison form."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text).lower()).split())


_SUBJECT_RE = re.compile(r"^\s*\[?([a-z0-9][a-z0-9 ._/:-]{0,60}?)\]?\s*[:=]\s+(\S.*)$", re.I | re.S)


def normalize_subject(value: str) -> str:
    """``deploy.port`` / ``deploy_port`` / ``Deploy Port`` all compare equal."""
    return " ".join(
        str(value).lower().replace("_", " ").replace("-", " ").replace(".", " ").split()
    )


def split_subject(text: str) -> tuple[str, str]:
    """``"deploy.port: prod listens on 8080"`` → ``("deploy port", "prod listens on 8080")``.

    The ``subject: claim`` form is the *one* explicit convention this store adds;
    it is what makes duplicate/contradiction detection deterministic instead of
    a guess at NLP. Without it the subject is empty and the memory still stores,
    ranks and merges — it just cannot be compared to a paraphrase.
    """
    raw = str(text).strip()
    match = _SUBJECT_RE.match(raw)
    if match:
        return normalize_subject(match.group(1)), match.group(2).strip()
    return "", raw


_NEGATION_MARKERS: tuple[str, ...] = (
    "not",
    "never",
    "no longer",
    "avoid",
    "avoids",
    "avoided",
    "disable",
    "disabled",
    "forbid",
    "forbidden",
    "deprecate",
    "deprecated",
    "remove",
    "removed",
    "stop",
    "stopped",
    "reject",
    "rejected",
    "without",
    "cannot",
    "refuse",
    "refused",
    "unset",
    "dont",
)

#: Antonym pairs used only as a *candidate* signal for contradiction, never as a
#: verdict. Deliberately small and readable: a bigger list buys recall on
#: paraphrase and costs false positives, and a false "contradiction" is a wrong
#: answer while a missed one is only a missed merge (see MVP/31 §limits).
_ANTONYMS: tuple[tuple[str, str], ...] = (
    ("enable", "disable"),
    ("enabled", "disabled"),
    ("allow", "deny"),
    ("allowed", "denied"),
    ("always", "never"),
    ("prefer", "avoid"),
    ("required", "optional"),
    ("keep", "remove"),
    ("true", "false"),
    ("accept", "reject"),
    ("include", "exclude"),
    ("stable", "broken"),
    ("works", "fails"),
    ("journald", "file"),
)

_NUMBER_RE = re.compile(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)(?![0-9])")
_WORD_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _has_word(haystack: str, word: str) -> bool:
    pattern = _WORD_RE_CACHE.get(word)
    if pattern is None:
        pattern = re.compile(rf"(?<![a-z]){re.escape(word)}(?![a-z])")
        _WORD_RE_CACHE[word] = pattern
    return bool(pattern.search(haystack))


def polarity(text: str) -> int:
    """``-1`` when the claim negates its predicate, else ``+1``.

    A *signal*, not a parser: "never use sed in place" is ``-1`` against
    "use sed in place", which is exactly the pair a contradiction detector
    should look at.
    """
    low = f" {normalize_text(text)} "
    for marker in _NEGATION_MARKERS:
        if _has_word(low, marker):
            return -1
    return 1


def values_in(text: str) -> list[str]:
    """Numbers in the claim, in order — "port 8080" → ``["8080"]``."""
    return [match.group(1) for match in _NUMBER_RE.finditer(str(text))]


def antonym_conflict(left: str, right: str) -> bool:
    """True when the two claims use opposite sides of a known antonym pair."""
    low_left = f" {normalize_text(left)} "
    low_right = f" {normalize_text(right)} "
    return any(
        (_has_word(low_left, pos) and _has_word(low_right, neg))
        or (_has_word(low_left, neg) and _has_word(low_right, pos))
        for pos, neg in _ANTONYMS
    )


def contradiction_reason(left: Memory, right: Memory) -> str:
    """Why ``left`` and ``right`` cannot both hold — ``""`` when they can.

    Detection is deliberately conservative and *documented*:

    * the two must share a non-empty subject and be of the same kind
      (otherwise they are two different claims, not a conflict);
    * **scope disjunction suppresses a conflict**: two memories pinned to
      different, non-global scopes are about different environments and may both
      hold (``deploy.port`` in ``project:staging`` = 9090 vs ``project:prod`` =
      8080). This is the measured false-positive guard — see MVP/31.
    * then one of: opposite polarity **and** substantial lexical overlap, or
      different numeric values **and** substantial lexical overlap, or an
      antonym pair, or an explicit ``--contradicts`` link.
    """
    if left.id and right.id and (right.id in left.contradicts or left.id in right.contradicts):
        return "explicit link (--contradicts)"
    if not left.subject or left.kind != right.kind or left.subject != right.subject:
        return ""
    if _scopes_disjoint(left, right):
        return ""
    overlap = lexical_overlap(left.text, right.text)
    if polarity(left.text) != polarity(right.text) and overlap >= 0.5:
        return f"opposite polarity on '{left.subject}'"
    left_values, right_values = values_in(left.text), values_in(right.text)
    if left_values and right_values and set(left_values) != set(right_values) and overlap >= 0.5:
        return (
            f"conflicting values on '{left.subject}' "
            f"({left_values[0]} vs {right_values[0]})"
        )
    if antonym_conflict(left.text, right.text):
        return f"antonym pair on '{left.subject}'"
    return ""


def _scopes_disjoint(left: Memory, right: Memory) -> bool:
    """Two *different* non-global scopes never contradict each other."""
    if left.scope == right.scope == SCOPE_GLOBAL:
        return False
    if left.scope == SCOPE_GLOBAL or right.scope == SCOPE_GLOBAL:
        return False
    return (left.scope, left.scope_id) != (right.scope, right.scope_id)


# --------------------------------------------------------------------------- #
# scoring helpers
# --------------------------------------------------------------------------- #


def evidence_weight(provenance: dict[str, Any]) -> float:
    """``source_weight + 0.10 per evidence ref`` (capped at 1.0).

    An owner-authored memory with three evidence refs outweighs an agent guess
    with none — that is the whole point of carrying provenance.
    """
    source = str(provenance.get("source", "") or "").strip().lower()
    base = SOURCE_WEIGHT.get(source, 0.5)
    refs = provenance.get("evidence") or provenance.get("evidence_refs") or []
    if isinstance(refs, str):
        refs = [refs] if refs else []
    try:
        count = len(list(refs))
    except TypeError:
        count = 0
    return min(1.0, base + 0.10 * min(3, count))


def effective_score(memory: Memory) -> float:
    """``confidence × (0.5 + 0.5 × evidence_weight)`` — the comparison value.

    Confidence alone is not enough: an agent's 0.95 recollection should not
    outrank the owner's 0.9 statement, and with this factor it does not.
    """
    return float(memory.confidence) * (0.5 + 0.5 * evidence_weight(memory.provenance))


def decay_factor(memory: Memory, now: float) -> float:
    """``0.5 ** (age_days / half_life_days)``; 1.0 for a fresh memory."""
    half_life = float(memory.half_life_seconds) or 0.0
    if half_life <= 0:
        return 1.0
    age_days = max(0.0, (now - float(memory.created_at)) / 86400.0)
    return float(0.5 ** (age_days / (half_life / 86400.0)))


def validate_embedding_provider(sample_query: str = "", sample_doc: str = "") -> dict[str, Any]:
    """Prove ``embedding.HashingEmbeddings`` is usable **before** ranking on it.

    Returns the measured facts: dimension, determinism, norm, and — honestly —
    a lexical-vs-semantic probe. A provider that scores a paraphrase at ~0 is a
    lexical provider, and the ranking must not pretend otherwise.
    """
    provider = HashingEmbeddings()
    first = provider.embed("deploy port 8080")
    second = provider.embed("Deploy Port 8080")
    same_after_case_change = cosine(first, second)
    return {
        "provider": "HashingEmbeddings",
        "dim": provider.dim,
        "dim_matches_contract": provider.dim == DIM,
        "usable": bool(provider.dim > 0 and any(abs(value) > 0 for value in first)),
        "deterministic": first == second,
        "norm_is_unit": abs(math.sqrt(sum(value * value for value in first)) - 1.0) < 1e-9,
        "lexical_probe": {
            "same_tokens_reworded": round(
                max(
                    0.0,
                    cosine(
                        provider.embed("deploy port is 8080"),
                        provider.embed("port deploy 8080"),
                    ),
                ),
                4,
            ),
            "case_change_cosine": round(max(0.0, same_after_case_change), 4),
            "paraphrase_no_shared_tokens": round(
                max(0.0, cosine(provider.embed("how do we roll back a bad release"),
                                provider.embed("revert procedure for broken deploys"))),
                4,
            ),
        },
        "semantic": False,
        "note": "character-trigram hashing: lexical overlap only (audit MEM-10)",
    }


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #


@dataclass
class Memory:
    id: str
    kind: str
    text: str
    subject: str
    polarity: int
    provenance: dict[str, Any]
    confidence: float
    scope: str
    scope_id: str
    created_at: float
    updated_at: float
    ttl_seconds: float
    half_life_seconds: float
    status: str = STATUS_ACTIVE
    revision: int = 1
    superseded_by: str = ""
    merged_into: str = ""
    conflict_group: str = ""
    contradicts: list[str] = field(default_factory=list)
    signature: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "subject": self.subject,
            "polarity": self.polarity,
            "provenance": dict(self.provenance),
            "source": self.source,
            "confidence": round(float(self.confidence), 4),
            "scope": self.scope,
            "scope_id": self.scope_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "ttl_seconds": self.ttl_seconds,
            "half_life_seconds": self.half_life_seconds,
            "status": self.status,
            "revision": self.revision,
            "superseded_by": self.superseded_by,
            "merged_into": self.merged_into,
            "conflict_group": self.conflict_group,
            "contradicts": list(self.contradicts),
            "signature": self.signature,
        }

    def scope_key(self) -> tuple[str, str]:
        return (self.scope, self.scope_id)

    def is_expired(self, now: float) -> bool:
        if float(self.ttl_seconds) <= 0:
            return False
        return (now - float(self.created_at)) >= float(self.ttl_seconds)


@dataclass
class Hit:
    """One ranked hit, with everything needed to argue about it."""

    memory_id: str
    text: str
    kind: str
    subject: str
    scope: str
    scope_id: str
    status: str
    score: float
    relevance: float
    recency: float
    confidence: float
    confidence_raw: float
    scope_component: float
    age_days: float
    matched_terms: list[str]
    flags: list[str]
    source: str
    explanation: str
    contradicts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.memory_id,
            "text": self.text,
            "kind": self.kind,
            "subject": self.subject,
            "scope": f"{self.scope}:{self.scope_id}" if self.scope_id else self.scope,
            "status": self.status,
            "score": round(self.score, 4),
            "score_parts": {
                "relevance": round(self.relevance, 4),
                "recency": round(self.recency, 4),
                "confidence": round(self.confidence, 4),
                "confidence_raw": round(self.confidence_raw, 4),
                "scope_match": round(self.scope_component, 4),
            },
            "age_days": round(self.age_days, 1),
            "matched_terms": list(self.matched_terms),
            "flags": list(self.flags),
            "source": self.source,
            "explanation": self.explanation,
            "contradicts": [dict(item) for item in self.contradicts],
        }


@dataclass
class ConsolidationReport:
    dry_run: bool
    scanned: int
    merged: int
    superseded: int
    flagged: int
    unresolved_pairs: int
    actions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    before: dict[str, int] = field(default_factory=dict)
    after: dict[str, int] = field(default_factory=dict)

    @property
    def needs_attention(self) -> bool:
        return self.unresolved_pairs > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "scanned": self.scanned,
            "merged": self.merged,
            "superseded": self.superseded,
            "flagged": self.flagged,
            "unresolved_pairs": self.unresolved_pairs,
            "before": dict(self.before),
            "after": dict(self.after),
            "actions": list(self.actions),
            "warnings": list(self.warnings),
        }


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #


def _row_to_memory(row: sqlite3.Row) -> Memory:
    return Memory(
        id=row["id"],
        kind=row["kind"],
        text=row["text"],
        subject=row["subject"],
        polarity=int(row["polarity"]),
        provenance=json.loads(row["provenance"] or "{}"),
        confidence=float(row["confidence"]),
        scope=row["scope"],
        scope_id=row["scope_id"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        ttl_seconds=float(row["ttl_seconds"]),
        half_life_seconds=float(row["half_life_seconds"]),
        status=row["status"],
        revision=int(row["revision"]),
        superseded_by=row["superseded_by"],
        merged_into=row["merged_into"],
        conflict_group=row["conflict_group"],
        contradicts=json.loads(row["contradicts"] or "[]"),
        signature=row["signature"],
        source=row["source"],
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_store_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS typed_memories (
    id                TEXT PRIMARY KEY,
    kind              TEXT NOT NULL,
    text              TEXT NOT NULL,
    subject           TEXT NOT NULL DEFAULT '',
    polarity          INTEGER NOT NULL DEFAULT 1,
    provenance        TEXT NOT NULL DEFAULT '{}',
    confidence        REAL NOT NULL,
    scope             TEXT NOT NULL DEFAULT 'global',
    scope_id          TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL,
    ttl_seconds       REAL NOT NULL DEFAULT 0,
    half_life_seconds REAL NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'active',
    revision          INTEGER NOT NULL DEFAULT 1,
    superseded_by     TEXT NOT NULL DEFAULT '',
    merged_into       TEXT NOT NULL DEFAULT '',
    conflict_group    TEXT NOT NULL DEFAULT '',
    contradicts       TEXT NOT NULL DEFAULT '[]',
    signature         TEXT NOT NULL DEFAULT '',
    source            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS typed_memories_status ON typed_memories(status);
CREATE INDEX IF NOT EXISTS typed_memories_signature ON typed_memories(signature);
CREATE INDEX IF NOT EXISTS typed_memories_subject ON typed_memories(kind, subject);
CREATE TABLE IF NOT EXISTS memory_journal (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    at         REAL NOT NULL,
    action     TEXT NOT NULL,
    memory_id  TEXT NOT NULL,
    related_id TEXT NOT NULL DEFAULT '',
    actor      TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS memory_journal_memory ON memory_journal(memory_id);
CREATE TABLE IF NOT EXISTS consolidation_runs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       REAL NOT NULL,
    dry_run  INTEGER NOT NULL,
    scanned  INTEGER NOT NULL,
    merged   INTEGER NOT NULL,
    flagged  INTEGER NOT NULL,
    resolved INTEGER NOT NULL,
    report   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_vectors (
    memory_id TEXT PRIMARY KEY,
    dim       INTEGER NOT NULL,
    vector    TEXT NOT NULL,
    at        REAL NOT NULL
);
"""

_JOURNALED_ACTIONS = frozenset(
    {"remember", "merge", "supersede", "contradict", "resolve", "forget", "touch"}
)


class TypedMemoryStore:
    """A SQLite store of typed memories, beside ``memory.db`` (not a rewrite of it)."""

    def __init__(self, path: str | Path, *, clock: Any = time.time) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO memory_store_meta(key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        self._conn.commit()

    # -- lifecycle ---------------------------------------------------------- #

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> TypedMemoryStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def now(self) -> float:
        return float(self._clock())

    # -- write path --------------------------------------------------------- #

    def _journal(
        self,
        action: str,
        memory_id: str,
        *,
        related_id: str = "",
        actor: str = "",
        detail: dict[str, Any] | None = None,
        at: float | None = None,
    ) -> None:
        """Append the journal row. Every mutation calls this in the same txn."""
        self._conn.execute(
            "INSERT INTO memory_journal(at, action, memory_id, related_id, actor, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                float(self.now() if at is None else at),
                action,
                memory_id,
                related_id,
                actor,
                json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
            ),
        )

    def journal(
        self, memory_id: str | None = None, *, include_related: bool = True, limit: int = 500
    ) -> list[dict[str, Any]]:
        """The append-only history (oldest first), optionally for one memory.

        ``include_related`` also returns rows *about* this memory (a merge that
        consumed it, a conflict it is part of). Set ``include_related=False`` for
        "what happened to this row" only.
        """
        sql = "SELECT * FROM memory_journal"
        params: list[Any] = []
        if memory_id:
            sql += " WHERE memory_id = ?" + (" OR related_id = ?" if include_related else "")
            params.append(memory_id)
            if include_related:
                params.append(memory_id)
        sql += " ORDER BY seq ASC LIMIT ?"
        params.append(int(limit))
        return [
            {
                "seq": int(row["seq"]),
                "at": float(row["at"]),
                "action": row["action"],
                "memory_id": row["memory_id"],
                "related_id": row["related_id"],
                "actor": row["actor"],
                "detail": json.loads(row["detail"] or "{}"),
            }
            for row in self._conn.execute(sql, params)
        ]

    def remember(
        self,
        text: str,
        *,
        kind: str = "fact",
        scope: str = SCOPE_GLOBAL,
        scope_id: str = "",
        subject: str | None = None,
        confidence: float = 0.6,
        provenance: dict[str, Any] | None = None,
        source: str = "",
        ttl: float | None = None,
        ttl_seconds: float | None = None,
        half_life_seconds: float | None = None,
        contradicts: Sequence[str] = (),
        actor: str = "",
        dry_run: bool = False,
        now: float | None = None,
    ) -> Memory:
        """Store one typed memory. The explicit write verb — never implicit.

        Refusals (all exit 1 = usage at the CLI): unknown kind/scope, empty or
        over-long text, confidence outside 0..1, a scoped memory without a
        scope id, or a TTL that is not a positive number of seconds.
        """
        if kind not in KINDS:
            raise MemoryValidationError(f"unknown kind {kind!r}; one of {', '.join(KINDS)}")
        if scope not in SCOPE_KINDS:
            raise MemoryValidationError(
                f"unknown scope {scope!r}; one of {', '.join(SCOPE_KINDS)}"
            )
        if scope != SCOPE_GLOBAL and not str(scope_id).strip():
            raise MemoryValidationError(f"scope {scope!r} needs a scope id (--scope-id)")
        if scope == SCOPE_GLOBAL and str(scope_id).strip():
            raise MemoryValidationError("scope global takes no scope id")
        cleaned = " ".join(str(text).split())
        if not cleaned:
            raise MemoryValidationError("empty memory text")
        if len(cleaned) > MAX_TEXT:
            raise MemoryValidationError(f"memory text longer than {MAX_TEXT} chars")
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError) as exc:
            raise MemoryValidationError(f"confidence is not a number: {confidence!r}") from exc
        if not 0.0 <= confidence_value <= 1.0:
            raise MemoryValidationError("confidence must be between 0.0 and 1.0")

        effective_ttl = float(ttl_seconds if ttl_seconds is not None else (ttl or 0.0))
        if effective_ttl < 0:
            raise MemoryValidationError("ttl must not be negative (0 = never expires)")
        default_ttl = DEFAULT_TTL_DAYS.get(kind, 0.0) * 86400.0
        if ttl_seconds is None and ttl is None:
            effective_ttl = default_ttl
        half_life = (
            float(half_life_seconds)
            if half_life_seconds is not None
            else DEFAULT_HALF_LIFE_DAYS.get(kind, 180.0) * 86400.0
        )

        derived_subject, claim = split_subject(cleaned)
        subject_value = normalize_subject(subject) if subject else derived_subject
        stamp = float(self.now() if now is None else now)
        provenance_value = dict(provenance or {})
        if source:
            provenance_value["source"] = str(source).strip().lower()
        if provenance_value.get("source") and provenance_value["source"] not in SOURCE_WEIGHT:
            raise MemoryValidationError(
                f"unknown provenance source {provenance_value['source']!r}; "
                f"one of {', '.join(SOURCE_WEIGHT)}"
            )
        source = str(provenance_value.get("source", "") or "").strip().lower()
        memory = Memory(
            id="",
            kind=kind,
            # The full statement is kept (subject prefix included): it is the
            # memory's content, and retrieval must see all of it. ``subject`` is
            # the *comparison key* on top, not a replacement for the text.
            text=cleaned,
            subject=subject_value,
            polarity=polarity(claim),
            provenance=provenance_value,
            confidence=confidence_value,
            scope=scope,
            scope_id=str(scope_id).strip(),
            created_at=stamp,
            updated_at=stamp,
            ttl_seconds=effective_ttl,
            half_life_seconds=half_life,
            contradicts=[item for item in contradicts if item],
            source=source,
        )
        memory.signature = content_signature(
            memory.kind, memory.subject, memory.text, memory.scope, memory.scope_id
        )
        memory.id = make_id(
            memory.kind,
            memory.subject,
            memory.text,
            memory.created_at,
            memory.scope,
            memory.scope_id,
        )

        if dry_run:
            return memory

        self._conn.execute(
            "INSERT INTO typed_memories (id, kind, text, subject, polarity, provenance, "
            "confidence, scope, scope_id, created_at, updated_at, ttl_seconds, "
            "half_life_seconds, status, revision, superseded_by, merged_into, "
            "conflict_group, contradicts, signature, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                memory.id,
                memory.kind,
                memory.text,
                memory.subject,
                memory.polarity,
                json.dumps(memory.provenance, ensure_ascii=False, sort_keys=True),
                memory.confidence,
                memory.scope,
                memory.scope_id,
                memory.created_at,
                memory.updated_at,
                memory.ttl_seconds,
                memory.half_life_seconds,
                memory.status,
                memory.revision,
                memory.superseded_by,
                memory.merged_into,
                memory.conflict_group,
                json.dumps(memory.contradicts),
                memory.signature,
                memory.source,
            ),
        )
        self._journal(
            "remember",
            memory.id,
            actor=actor,
            detail={
                "kind": memory.kind,
                "scope": memory.scope,
                "scope_id": memory.scope_id,
                "source": memory.source,
                "confidence": memory.confidence,
                "ttl_seconds": memory.ttl_seconds,
                "half_life_seconds": memory.half_life_seconds,
            },
            at=memory.created_at,
        )
        self._conn.commit()
        return memory

    # -- read path ---------------------------------------------------------- #

    def get(self, memory_id: str) -> Memory | None:
        row = self._conn.execute(
            "SELECT * FROM typed_memories WHERE id = ?", (str(memory_id),)
        ).fetchone()
        return _row_to_memory(row) if row is not None else None

    def require(self, memory_id: str) -> Memory:
        memory = self.get(memory_id)
        if memory is None:
            raise MemoryNotFound(f"no memory {memory_id!r} in {self.path}")
        return memory

    def all(
        self,
        *,
        statuses: Iterable[str] | None = None,
        kind: str | None = None,
        scope: str | None = None,
    ) -> list[Memory]:
        sql = "SELECT * FROM typed_memories"
        clauses: list[str] = []
        params: list[Any] = []
        if statuses is not None:
            wanted = list(statuses)
            clauses.append(f"status IN ({','.join('?' * len(wanted))})")
            params.extend(wanted)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at ASC, id ASC"
        return [_row_to_memory(row) for row in self._conn.execute(sql, params)]

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM typed_memories").fetchone()[0])

    def status_counts(self, *, now: float | None = None) -> dict[str, int]:
        """Stored statuses **plus** the computed ``expired`` bucket."""
        stamp = self.now() if now is None else now
        counts: dict[str, int] = {}
        for memory in self.all():
            bucket = STATUS_EXPIRED if memory.is_expired(stamp) else memory.status
            counts[bucket] = counts.get(bucket, 0) + 1
        return counts

    def scope_report(self, *, now: float | None = None) -> dict[str, Any]:
        """Scopes in use, kinds per scope, and the decay policy in force."""
        stamp = self.now() if now is None else now
        scopes: dict[str, dict[str, Any]] = {}
        for memory in self.all():
            key = f"{memory.scope}:{memory.scope_id}" if memory.scope_id else memory.scope
            bucket = scopes.setdefault(key, {"scope": memory.scope, "scope_id": memory.scope_id,
                                            "total": 0, "kinds": {}, "statuses": {},
                                            "expired": 0, "first_at": memory.created_at,
                                            "last_at": memory.created_at})
            bucket["total"] += 1
            bucket["kinds"][memory.kind] = bucket["kinds"].get(memory.kind, 0) + 1
            status = STATUS_EXPIRED if memory.is_expired(stamp) else memory.status
            bucket["statuses"][status] = bucket["statuses"].get(status, 0) + 1
            if status == STATUS_EXPIRED:
                bucket["expired"] += 1
            bucket["first_at"] = min(bucket["first_at"], memory.created_at)
            bucket["last_at"] = max(bucket["last_at"], memory.created_at)
        return {
            "db": str(self.path),
            "total": self.count(),
            "scopes": [scopes[key] for key in sorted(scopes)],
            "decay_policy": {
                kind: {
                    "half_life_days": DEFAULT_HALF_LIFE_DAYS[kind],
                    "ttl_days": DEFAULT_TTL_DAYS[kind],
                }
                for kind in KINDS
            },
            "journal_rows": int(
                self._conn.execute("SELECT COUNT(*) FROM memory_journal").fetchone()[0]
            ),
        }

    def journal_consistency(self) -> dict[str, Any]:
        """Checkable "no silent writes": every non-active/non-active-write has a row.

        A memory is considered journaled when it has at least one ``remember``
        row; a status other than ``active`` additionally needs the matching
        action row (``merge`` / ``supersede`` / ``contradict`` / ``forget``).
        """
        journaled = {
            (row["memory_id"], row["action"])
            for row in self._conn.execute("SELECT memory_id, action FROM memory_journal")
        }
        missing: list[dict[str, str]] = []
        for memory in self.all():
            if (memory.id, "remember") not in journaled:
                missing.append({"id": memory.id, "missing": "remember"})
                continue
            if memory.status == STATUS_MERGED and (memory.id, "merge") not in journaled:
                missing.append({"id": memory.id, "missing": "merge"})
            if memory.status == STATUS_SUPERSEDED and (memory.id, "supersede") not in journaled:
                missing.append({"id": memory.id, "missing": "supersede"})
            if memory.status == STATUS_CONTRADICTED and (memory.id, "contradict") not in journaled:
                missing.append({"id": memory.id, "missing": "contradict"})
            if memory.status == STATUS_FORGOTTEN and (memory.id, "forget") not in journaled:
                missing.append({"id": memory.id, "missing": "forget"})
        return {
            "memories": self.count(),
            "journal_rows": int(
                self._conn.execute("SELECT COUNT(*) FROM memory_journal").fetchone()[0]
            ),
            "missing": missing,
            "ok": not missing,
        }

    # -- forget ------------------------------------------------------------- #

    def forget(
        self,
        memory_id: str,
        *,
        actor: str = "",
        reason: str = "",
        hard: bool = False,
        dry_run: bool = False,
    ) -> Memory:
        """Retract a memory. Soft by default (status ``forgotten`` + journal row).

        A forgotten memory is never retrieved; the row and its provenance stay
        for audit. ``--hard`` deletes the row instead, still journaled.
        """
        memory = self.require(memory_id)
        if dry_run:
            return memory
        if hard:
            self._conn.execute("DELETE FROM memory_vectors WHERE memory_id = ?", (memory.id,))
            self._conn.execute("DELETE FROM typed_memories WHERE id = ?", (memory.id,))
        else:
            self._conn.execute(
                "UPDATE typed_memories SET status = ?, updated_at = ?, revision = revision + 1 "
                "WHERE id = ?",
                (STATUS_FORGOTTEN, self.now(), memory.id),
            )
        self._journal(
            "forget", memory.id, actor=actor, detail={"reason": reason, "hard": bool(hard)}
        )
        self._conn.commit()
        if hard:
            memory.status = STATUS_FORGOTTEN
            return memory
        return self.require(memory.id)

    # -- consolidation ------------------------------------------------------ #

    def consolidate(
        self,
        *,
        apply: bool = False,
        near_dup: float = DEFAULT_NEAR_DUP,
        actor: str = "",
        max_pairs: int = 200_000,
    ) -> ConsolidationReport:
        """Merge duplicates, detect contradictions, resolve them by the rule.

        Dry run by default: without ``apply`` nothing is written at all (no
        journal row either) and the report says exactly what *would* happen.
        The pairwise scan is O(n²) over the candidate pool and is bounded by
        ``max_pairs``; the bound is reported as a warning when it bites, never
        silently skipped.
        """
        stamp = self.now()
        active = [
            memory
            for memory in self.all()
            if memory.status in (STATUS_ACTIVE, STATUS_CONTRADICTED)
        ]
        before = self.status_counts(now=stamp)
        report = ConsolidationReport(
            dry_run=not apply,
            scanned=len(active),
            merged=0,
            superseded=0,
            flagged=0,
            unresolved_pairs=0,
            before=before,
            after=before,
        )

        # 1. duplicates: exact signature first, then near-duplicates.
        groups: dict[str, list[Memory]] = {}
        for memory in active:
            groups.setdefault(memory.signature, []).append(memory)
        consumed: set[str] = set()
        for signature in sorted(groups):
            group = sorted(groups[signature], key=lambda item: effective_score(item), reverse=True)
            if len(group) < 2:
                continue
            canonical = group[0]
            for loser in group[1:]:
                report.actions.append(
                    {
                        "action": "merge",
                        "reason": "identical content (same kind, subject and signature)",
                        "kept": canonical.id,
                        "merged": loser.id,
                        "similarity": 1.0,
                    }
                )
                report.merged += 1
                consumed.add(loser.id)
                if apply:
                    self._merge(canonical, loser, similarity_value=1.0, actor=actor)

        survivors = [memory for memory in active if memory.id not in consumed]

        # Near-duplicates: only inside one kind + one scope, because merging two
        # claims from different scopes would erase the scope distinction.
        pair_budget = 0
        merged_away: set[str] = set()
        for index, left in enumerate(survivors):
            if left.id in merged_away:
                continue
            for right in survivors[index + 1 :]:
                if right.id in merged_away:
                    continue
                if pair_budget >= max_pairs:
                    report.warnings.append(
                        f"pairwise scan stopped at max_pairs={max_pairs}; "
                        "results below are partial"
                    )
                    break
                pair_budget += 1
                if left.kind != right.kind or left.scope_key() != right.scope_key():
                    continue
                if left.subject and right.subject and left.subject != right.subject:
                    continue
                score = similarity(left.text, right.text)
                if score < near_dup:
                    continue
                if contradiction_reason(left, right):
                    continue  # a contradiction is not a duplicate
                keeper, loser = sorted(
                    (left, right),
                    key=lambda item: (effective_score(item), item.created_at),
                    reverse=True,
                )
                report.actions.append(
                    {
                        "action": "merge",
                        "reason": f"near-duplicate (similarity {score:.3f} >= {near_dup:.2f})",
                        "kept": keeper.id,
                        "merged": loser.id,
                        "similarity": round(float(score), 4),
                    }
                )
                report.merged += 1
                consumed.add(loser.id)
                merged_away.add(loser.id)
                if apply:
                    self._merge(keeper, loser, similarity_value=float(score), actor=actor)
                if loser is left:
                    break  # ``left`` is gone; nothing left to compare it against
            if pair_budget >= max_pairs:
                break

        survivors = [memory for memory in survivors if memory.id not in consumed]

        # 2. contradictions among what is left.
        for index, left in enumerate(survivors):
            for right in survivors[index + 1 :]:
                reason = contradiction_reason(left, right)
                if not reason:
                    continue
                winner, loser, verdict, basis = resolve_contradiction(left, right)
                if verdict == "unresolved":
                    report.unresolved_pairs += 1
                    already = left.conflict_group and left.conflict_group == right.conflict_group
                    if already:
                        # Re-running consolidation must not re-flag or re-warn: the
                        # pair is still unresolved, and that is reported once.
                        report.actions.append(
                            {
                                "action": "already-flagged",
                                "reason": reason,
                                "ids": [left.id, right.id],
                                "conflict_group": left.conflict_group,
                                "basis": basis,
                            }
                        )
                        continue
                    report.flagged += 2
                    group_id = f"conflict_{left.id[4:10]}_{right.id[4:10]}"
                    report.actions.append(
                        {
                            "action": "flag",
                            "reason": reason,
                            "ids": [left.id, right.id],
                            "conflict_group": group_id,
                            "basis": basis,
                        }
                    )
                    report.warnings.append(
                        f"unresolved contradiction {group_id}: {left.id} vs {right.id} "
                        f"({reason}) — {basis}; review both with `memory explain <id>`"
                    )
                    if apply:
                        self._mark_contradicted(left, right, group_id, reason, actor=actor)
                    continue
                report.superseded += 1
                report.actions.append(
                    {
                        "action": "supersede",
                        "reason": reason,
                        "kept": winner.id,
                        "superseded": loser.id,
                        "basis": basis,
                    }
                )
                if apply:
                    self._supersede(winner, loser, reason=reason, basis=basis, actor=actor)

        if apply:
            self._conn.execute(
                "INSERT INTO consolidation_runs(at, dry_run, scanned, merged, flagged, "
                "resolved, report) VALUES (?,?,?,?,?,?,?)",
                (
                    stamp,
                    0,
                    report.scanned,
                    report.merged,
                    report.flagged,
                    report.superseded,
                    json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True),
                ),
            )
            self._conn.commit()
            report.after = self.status_counts(now=stamp)
        return report

    def _merge(
        self, canonical: Memory, loser: Memory, *, similarity_value: float, actor: str
    ) -> None:
        stamp = self.now()
        merged_provenance = dict(canonical.provenance)
        existing = list(merged_provenance.get("evidence") or [])
        incoming = list(loser.provenance.get("evidence") or [])
        if incoming:
            merged_provenance["evidence"] = list(dict.fromkeys([*existing, *incoming]))
        merged_provenance.setdefault("merged_from", [])
        merged_provenance["merged_from"] = list(
            dict.fromkeys([*merged_provenance["merged_from"], loser.id])
        )
        if effective_score(loser) > effective_score(canonical):
            merged_provenance["best_confidence"] = loser.confidence
        self._conn.execute(
            "UPDATE typed_memories SET provenance = ?, updated_at = ?, "
            "revision = revision + 1 WHERE id = ?",
            (
                json.dumps(merged_provenance, ensure_ascii=False, sort_keys=True),
                stamp,
                canonical.id,
            ),
        )
        self._conn.execute(
            "UPDATE typed_memories SET status = ?, merged_into = ?, updated_at = ?, "
            "revision = revision + 1 WHERE id = ?",
            (STATUS_MERGED, canonical.id, stamp, loser.id),
        )
        self._journal(
            "merge",
            loser.id,
            related_id=canonical.id,
            actor=actor,
            detail={"similarity": round(float(similarity_value), 4), "kept": canonical.id},
        )

    def _supersede(
        self, winner: Memory, loser: Memory, *, reason: str, basis: str, actor: str
    ) -> None:
        stamp = self.now()
        self._conn.execute(
            "UPDATE typed_memories SET status = ?, superseded_by = ?, updated_at = ?, "
            "revision = revision + 1 WHERE id = ?",
            (STATUS_SUPERSEDED, winner.id, stamp, loser.id),
        )
        self._journal(
            "supersede",
            loser.id,
            related_id=winner.id,
            actor=actor,
            detail={"reason": reason, "basis": basis, "kept": winner.id},
        )
        self._journal(
            "resolve",
            winner.id,
            related_id=loser.id,
            actor=actor,
            detail={"reason": reason, "basis": basis, "superseded": loser.id},
        )

    def _mark_contradicted(
        self, left: Memory, right: Memory, group_id: str, reason: str, *, actor: str
    ) -> None:
        stamp = self.now()
        for memory in (left, right):
            self._conn.execute(
                "UPDATE typed_memories SET status = ?, conflict_group = ?, updated_at = ?, "
                "revision = revision + 1 WHERE id = ?",
                (STATUS_CONTRADICTED, group_id, stamp, memory.id),
            )
            self._journal(
                "contradict",
                memory.id,
                related_id=right.id if memory is left else left.id,
                actor=actor,
                detail={"conflict_group": group_id, "reason": reason},
            )

    # -- retrieval ---------------------------------------------------------- #

    def recall(
        self,
        query: str,
        *,
        k: int = DEFAULT_K,
        scope: str | None = None,
        scope_id: str = "",
        kind: str | None = None,
        strict_scope: bool = False,
        include_superseded: bool = False,
        min_relevance: float = 0.10,
        now: float | None = None,
    ) -> list[Hit]:
        """Rank the store against ``query`` and explain every hit.

        The candidate pool is the *legitimate* one — active and flagged memories
        only, expired ones removed by their TTL — and the ranking is
        ``0.55·relevance + 0.15·recency + 0.15·confidence + 0.15·scope``. Hits
        below ``min_relevance`` (default 0.10, trigram noise: a memory can share
        a couple of character trigrams with anything) are not returned: "no
        memory answers this" is a result, and a padded list is not an answer.
        """
        stamp = self.now() if now is None else now
        wanted_scope = str(scope or "")
        pool = [
            memory
            for memory in self.all()
            if memory.status not in (STATUS_MERGED, STATUS_FORGOTTEN)
        ]
        # A conflict index on (kind, subject) so the live-conflict check is O(1)
        # per candidate instead of a full pairwise scan on every recall.
        by_key: dict[tuple[str, str], list[Memory]] = {}
        for candidate in pool:
            if candidate.subject:
                by_key.setdefault((candidate.kind, candidate.subject), []).append(candidate)

        hits: list[Hit] = []
        for memory in self.all():
            if memory.status in (STATUS_MERGED, STATUS_FORGOTTEN):
                continue
            if memory.status == STATUS_SUPERSEDED and not include_superseded:
                continue
            if memory.is_expired(stamp):
                continue
            if kind and memory.kind != kind:
                continue
            scope_component, allowed = _scope_component(
                memory, wanted_scope, scope_id, strict_scope=strict_scope
            )
            if not allowed:
                continue
            relevance = _relevance(query, memory)
            if query.strip() and not _matches_query(query, memory):
                continue
            if query.strip() and relevance <= min_relevance:
                continue
            recency = decay_factor(memory, stamp)
            # The confidence term is the *effective* score (confidence × the
            # provenance factor), not raw confidence: otherwise a 0.95 agent
            # recollection outranks the owner's 0.90 statement in retrieval while
            # consolidation rightly prefers the owner — two answers from one store.
            # Measured before this change: the agent claim took rank 1 (0.9913 vs
            # 0.9044) until `consolidate` retired it.
            confidence_component = effective_score(memory)
            score = (
                W_RELEVANCE * relevance
                + W_RECENCY * recency
                + W_CONFIDENCE * confidence_component
                + W_SCOPE * scope_component
            )
            flags: list[str] = []
            if memory.status == STATUS_CONTRADICTED:
                score *= CONTRADICTED_FACTOR
                if "unresolved-contradiction" not in flags:
                    flags.append("unresolved-contradiction")
            # A live conflict — two rows that cannot both hold and neither retired —
            # is surfaced even before `consolidate` has run. Without this the
            # ranker would serve the newer side of a dispute and say nothing
            # (measured: the agent claim took rank 1 until this check landed).
            live_conflicts: list[dict[str, Any]] = []
            for other in by_key.get((memory.kind, memory.subject), []):
                if other.id == memory.id or other.status == STATUS_SUPERSEDED:
                    continue
                reason = contradiction_reason(memory, other)
                if not reason:
                    continue
                winner, _loser, verdict, basis = resolve_contradiction(memory, other)
                live_conflicts.append(
                    {
                        "id": other.id,
                        "status": other.status,
                        "reason": reason,
                        # The *documented* rule, applied read-only: recall may not
                        # silently resolve a conflict, but it says who would win
                        # and on what basis, so the caller is never guessing.
                        "would_win": winner.id if verdict == "supersede" else "",
                        "basis": basis,
                    }
                )
                if other.status == STATUS_ACTIVE:
                    score *= CONTRADICTED_FACTOR
                    if "contradicts-active" not in flags:
                        flags.append("contradicts-active")
            if live_conflicts and "unresolved-contradiction" not in flags:
                flags.append("unresolved-contradiction")
            if query.strip():
                matched = sorted(tokens(query) & tokens(memory.text))
            else:
                matched = []
            stale = memory.created_at + 3 * float(memory.half_life_seconds or 0) < stamp
            if stale:
                flags.append("past-3x-half-life")
            hits.append(
                Hit(
                    memory_id=memory.id,
                    text=memory.text,
                    kind=memory.kind,
                    subject=memory.subject,
                    scope=memory.scope,
                    scope_id=memory.scope_id,
                    status=memory.status,
                    score=score,
                    relevance=relevance,
                    recency=recency,
                    confidence=confidence_component,
                    confidence_raw=float(memory.confidence),
                    scope_component=scope_component,
                    age_days=max(0.0, (stamp - memory.created_at) / 86400.0),
                    matched_terms=matched,
                    flags=flags,
                    source=str(memory.provenance.get("source", "") or ""),
                    explanation=_explain_hit(
                        memory, relevance, recency, confidence_component, scope_component, flags
                    ),
                    contradicts=live_conflicts,
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.memory_id))
        return hits[: max(0, int(k))]

    def explain(
        self, memory_id: str, *, query: str = "", now: float | None = None
    ) -> dict[str, Any]:
        """The full record plus the read/write context — what ``memory explain`` prints."""
        stamp = self.now() if now is None else now
        memory = self.require(memory_id)
        provenance = memory.provenance
        conflicts: list[dict[str, Any]] = []
        for other in self.all():
            if other.id == memory.id:
                continue
            reason = contradiction_reason(memory, other)
            if reason:
                conflicts.append(
                    {"id": other.id, "text": other.text, "status": other.status, "reason": reason}
                )
        ranked = self.recall(query, k=DEFAULT_K, scope=memory.scope,
                             scope_id=memory.scope_id, include_superseded=True) if query else []
        return {
            "memory": memory.to_dict(),
            "expired": memory.is_expired(stamp),
            "age_days": round(max(0.0, (stamp - memory.created_at) / 86400.0), 2),
            "decay": {
                "half_life_days": round(memory.half_life_seconds / 86400.0, 2),
                "ttl_days": round(memory.ttl_seconds / 86400.0, 2),
                "factors": {
                    "at_created": 1.0,
                    "now": round(decay_factor(memory, stamp), 4),
                    "at_1_half_life": 0.5,
                    "at_2_half_lives": 0.25,
                    "at_3_half_lives": 0.125,
                    "note": "recency factor is 0.5 ** (age_days / half_life_days)",
                },
            },
            "evidence": {
                "source": memory.source,
                "source_weight": SOURCE_WEIGHT.get(memory.source, 0.5),
                "evidence_weight": round(evidence_weight(provenance), 4),
                "effective_score": round(effective_score(memory), 4),
                "provenance": provenance,
            },
            "conflicts": conflicts,
            "journal": self.journal(memory.id),
            "would_rank": [hit.to_dict() for hit in ranked],
        }

    def jev_offers(
        self,
        query: str,
        *,
        k: int = DEFAULT_K,
        scope: str | None = None,
        scope_id: str = "",
        in_context: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        """The ranked K in the shape of Jev ``MemoryCandidate`` entries.

        Sources are mapped onto the contract's vocabulary so the decision side
        receives candidates it already knows how to weigh; the ranking is *ours*,
        the decision stays Jev's.
        """
        mapped = {"project": "project_fact", "entity": "project_fact", "preference": "project_fact"}
        offers: list[dict[str, Any]] = []
        for hit in self.recall(query, k=k, scope=scope, scope_id=scope_id):
            offers.append(
                {
                    "memory_id": hit.memory_id,
                    "source": mapped.get(hit.kind, hit.kind if hit.kind in
                                         ("incident", "note") else "project_fact"),
                    "title": (hit.subject or hit.text)[:120],
                    "digest": hit.text[:400],
                    "age_days": int(hit.age_days),
                    "retrieval_score": round(hit.score, 4),
                    "already_in_context": hit.memory_id in set(in_context),
                    "protected": hit.source == "owner",
                    "contradicts": (),
                }
            )
        return offers


DEFAULT_PROVENANCE_SOURCES = tuple(SOURCE_WEIGHT)


def content_signature(
    kind: str, subject: str, text: str, scope: str = SCOPE_GLOBAL, scope_id: str = ""
) -> str:
    """Stable hash of (kind, subject, scope, normalized text) — the duplicate key.

    **Scope is part of the key.** Without it, the same claim recorded about
    ``project:prod`` and ``project:staging`` would look like one duplicate pair
    and the merge would erase the scope distinction — caught by
    ``tests/test_memory_upgrade_consolidate.py::test_duplicates_in_different_scopes_are_not_merged``.
    """
    payload = f"{kind}|{subject}|{scope}|{scope_id}|{normalize_text(text)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def make_id(
    kind: str,
    subject: str,
    text: str,
    created_at: float,
    scope: str = SCOPE_GLOBAL,
    scope_id: str = "",
) -> str:
    """Content-addressed, timestamped id — same content twice is *two* rows."""
    payload = (
        f"{kind}|{subject}|{scope}|{scope_id}|{normalize_text(text)}|{float(created_at):.6f}"
    )
    return "mem_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _scope_component(
    memory: Memory, scope: str, scope_id: str, *, strict_scope: bool
) -> tuple[float, bool]:
    """``(factor, allowed)`` for the scope part of the score.

    * no scope asked for → every row, factor 1.0;
    * exact scope match → 1.0;
    * a ``global`` memory answering a scoped question → 0.70 (it applies, it is
      not *the* scoped answer);
    * another project/session → 0.10 (visible, and loses to anything in scope),
      or excluded entirely with ``strict_scope``.
    """
    if not scope:
        return 1.0, True
    if memory.scope == scope and memory.scope_id == str(scope_id or ""):
        return 1.0, True
    if memory.scope == SCOPE_GLOBAL:
        # strict asks for what was said *about this scope*: globals are useful
        # context, but they are not that, so strict drops them (documented).
        return (0.0, False) if strict_scope else (SCOPE_GLOBAL_FACTOR, True)
    if strict_scope:
        return 0.0, False
    return SCOPE_MISMATCH_FACTOR, True


def _relevance(query: str, memory: Memory) -> float:
    """Text relevance, plus a subject bonus — the lexical core of the ranking.

    ``max(lexical_overlap, 0.75 × trigram cosine)`` is ``memory.similarity``'s
    basis, so the typed store and the incident gate agree on what "similar"
    means. A query that names the subject (``deploy port``) is evidence the
    memory's subject *is* the asked-about thing, worth +0.25.
    """
    if not query.strip():
        return 1.0
    base = max(lexical_overlap(query, memory.text), 0.75 * vector_score(query, memory.text))
    bonus = 0.0
    if memory.subject:
        subject_tokens = set(memory.subject.split())
        query_tokens = tokens(query)
        if subject_tokens and subject_tokens <= query_tokens:
            bonus = 0.25
        elif subject_tokens & query_tokens:
            bonus = 0.15
    return min(1.0, base + bonus)


def _explain_hit(
    memory: Memory,
    relevance: float,
    recency: float,
    confidence: float,
    scope_component: float,
    flags: list[str],
) -> str:
    parts = [
        f"relevance {relevance:.2f} (term/trigram match with the query)",
        f"recency {recency:.2f} (half-life {memory.half_life_seconds / 86400.0:.0f}d)",
        f"confidence {confidence:.2f} (raw {float(memory.confidence):.2f} "
        f"× provenance factor {0.5 + 0.5 * evidence_weight(memory.provenance):.2f})",
        f"scope {scope_component:.2f} ({memory.scope}"
        + (f":{memory.scope_id}" if memory.scope_id else "")
        + ")",
    ]
    tail = f"; flags: {', '.join(flags)}" if flags else ""
    return "; ".join(parts) + tail


# --------------------------------------------------------------------------- #
# the contradiction rule — one place, documented, testable
# --------------------------------------------------------------------------- #


def resolve_contradiction(left: Memory, right: Memory) -> tuple[Memory, Memory, str, str]:
    """Resolve two memories that cannot both hold → ``(winner, loser, verdict, basis)``.

    Order matters and is the documented policy (MVP/31 §resolution). Every step
    is deterministic; when no step is decisive the verdict is ``unresolved`` and
    both are flagged — the store does **not** let a float, or a model, pick the
    owner's truth:

    1. **owner-authored wins** — exactly one side carries
       ``provenance.source == "owner"`` (R4: owner-authored memory is
       authoritative). Basis: ``owner-authored``.
    2. **weighted score wins** — ``confidence × (0.5 + 0.5·evidence_weight)``
       differing by ≥ ``EVIDENCE_DECISIVE`` (0.20). This is the evidence-based
       branch: more/stronger provenance beats a higher raw confidence.
       Basis: ``evidence-decisive``.
    3. **confidence wins** — raw confidences differing by ≥
       ``CONFIDENCE_DECISIVE`` (0.15). Basis: ``confidence-decisive``.
    4. **newest wins** — created ≥ ``NEWEST_DECISIVE_SECONDS`` (60 s) apart.
       Basis: ``newest``.
    5. **otherwise: unresolved** — flag both, keep both, exit 2, ask the agent.
       Basis names why (``undecidable: equal weight, confidence and timestamp``).
    """
    left_owner = left.source == "owner"
    right_owner = right.source == "owner"
    if left_owner != right_owner:
        winner, loser = (left, right) if left_owner else (right, left)
        return winner, loser, "supersede", "owner-authored wins over non-owner"

    left_effective, right_effective = effective_score(left), effective_score(right)
    if abs(left_effective - right_effective) >= EVIDENCE_DECISIVE:
        winner, loser = (
            (left, right) if left_effective > right_effective else (right, left)
        )
        return (
            winner,
            loser,
            "supersede",
            f"evidence-decisive: weighted score {left_effective:.3f} vs {right_effective:.3f}",
        )

    if abs(float(left.confidence) - float(right.confidence)) >= CONFIDENCE_DECISIVE:
        winner, loser = (
            (left, right) if float(left.confidence) > float(right.confidence) else (right, left)
        )
        return (
            winner,
            loser,
            "supersede",
            f"confidence-decisive: {left.confidence:.2f} vs {right.confidence:.2f}",
        )

    if abs(float(left.created_at) - float(right.created_at)) >= NEWEST_DECISIVE_SECONDS:
        winner, loser = (
            (left, right) if float(left.created_at) > float(right.created_at) else (right, left)
        )
        return (
            winner,
            loser,
            "supersede",
            "newest wins: same weight and confidence, later timestamp",
        )

    return (
        left,
        right,
        "unresolved",
        "undecidable: equal weight, confidence and timestamp — needs an owner-authored decision",
    )


# --------------------------------------------------------------------------- #
# measurement — synthetic corpus with known ground truth
# --------------------------------------------------------------------------- #


@dataclass
class CorpusQuery:
    query: str
    expected: str
    scope: str = ""
    scope_id: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "expected": self.expected,
            "scope": f"{self.scope}:{self.scope_id}" if self.scope_id else self.scope,
            "note": self.note,
        }


@dataclass
class CorpusPlan:
    """What the synthetic corpus is *supposed* to contain (before/after counts)."""

    stored: int
    expected_merges: int
    expected_contradictions: int
    expected_supersessions: int
    expected_flags: int
    expected_expired: int
    expected_unresolved_pairs: int = 1
    expected_active_after: int = 0
    queries: list[CorpusQuery] = field(default_factory=list)
    ids: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    known_false_negatives: list[dict[str, Any]] = field(default_factory=list)
    expected_ranking: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stored": self.stored,
            "expected_merges": self.expected_merges,
            "expected_contradictions": self.expected_contradictions,
            "expected_supersessions": self.expected_supersessions,
            "expected_flags": self.expected_flags,
            "expected_expired": self.expected_expired,
            "expected_unresolved_pairs": self.expected_unresolved_pairs,
            "expected_active_after": self.expected_active_after,
            "queries": [query.to_dict() for query in self.queries],
            "ids": dict(self.ids),
            "notes": list(self.notes),
            "known_false_negatives": list(self.known_false_negatives),
            "expected_ranking": dict(self.expected_ranking),
        }


def _corpus_rows() -> list[dict[str, Any]]:
    """The synthetic corpus: >= 50 memories with known duplicates, contradictions, staleness.

    Ages are relative (``age_days`` before *now*), so the corpus is deterministic
    in structure whatever the wall clock says. ``tag`` names the row so the
    labeled queries can point at an id without hard-coding a hash.
    """

    def row(
        tag: str,
        text: str,
        *,
        kind: str = "fact",
        source: str = "agent",
        confidence: float = 0.6,
        age_days: float = 30.0,
        scope: str = SCOPE_GLOBAL,
        scope_id: str = "",
        ttl_days: float | None = None,
        evidence: int = 0,
        session: str = "",
    ) -> dict[str, Any]:
        return {
            "tag": tag,
            "text": text,
            "kind": kind,
            "source": source,
            "confidence": confidence,
            "age_days": float(age_days),
            "scope": scope,
            "scope_id": scope_id,
            "ttl_days": ttl_days,
            "evidence": int(evidence),
            "session": session,
        }

    rows: list[dict[str, Any]] = [
        # -- group A: the prod deploy port, seen four ways ------------------- #
        # The owner-authored truth is old; three newer/oral variants exist.
        row("port_prod_owner", "deploy.port: prod listens on 8080", source="owner",
            confidence=0.9, age_days=120, scope="project", scope_id="prod", evidence=2,
            session="s-owner-1"),
        row("port_staging", "deploy.port: staging listens on 9090", confidence=0.8,
            age_days=3, scope="project", scope_id="staging", session="s-staging"),
        row("port_prod_agent", "deploy.port: prod listens on 9090", confidence=0.6,
            age_days=10, scope="project", scope_id="prod", session="s-agent-7"),
        row("port_prod_dup", "deploy.port: prod listens on 8080", confidence=0.7,
            age_days=40, scope="project", scope_id="prod", evidence=1, session="s-agent-9",
            source="agent"),

        # -- group B: contradiction decided by weighted evidence ------------- #
        row("log_winner", "logging.sink: use journald", confidence=0.9, age_days=60,
            session="s-log-1"),
        row("log_loser", "logging.sink: avoid journald, use a file", confidence=0.45,
            age_days=8, session="s-log-2"),

        # -- group C: contradiction decided by newest ------------------------ #
        row("timeout_old", "test.timeout: the suite timeout is 30 seconds", confidence=0.6,
            age_days=30, session="s-time-1"),
        row("timeout_new", "test.timeout: the suite timeout is 20 seconds", confidence=0.6,
            age_days=5, session="s-time-2"),

        # -- group D: a genuine tie → flagged, never guessed ----------------- #
        # Both owner-authored, same confidence, same evidence weight and the
        # same timestamp: no rule step is decisive, so the pair is flagged.
        row("tie_redis", "cache.eviction: prefer redis", source="owner",
            confidence=0.8, age_days=90, evidence=1, session="s-cache-1"),
        row("tie_memcached", "cache.eviction: avoid redis, keep memcached", source="owner",
            confidence=0.8, age_days=90, evidence=1, session="s-cache-1"),
        # A *known false negative* of the lexical detector, kept in the corpus on
        # purpose: two conflicting proper nouns under one subject share polarity,
        # share no antonym pair and carry no number, so nothing flags them. The
        # measurement reports it instead of pretending the detector is complete.
        row("cache_redis", "cache.backend: redis is the cache backend",
            confidence=0.7, age_days=100, session="s-cache-2"),
        row("cache_memcached", "cache.backend: memcached is the cache backend",
            confidence=0.7, age_days=88, session="s-cache-3"),

        # -- group E: owner-authored entity fact vs a newer agent claim ------ #
        row("release_owner_truth", "release.checklist.owner: marta", kind="entity",
            source="owner", confidence=0.9, age_days=200, evidence=2, session="s-rel-1"),
        row("release_owner_agent", "release.checklist.owner: not marta any more, tomas owns it",
            kind="entity", confidence=0.95, age_days=2, session="s-rel-2"),

        # -- group F: exact duplicates -------------------------------------- #
        row("dup_uv_a", "package.manager: uv is the only package manager used here",
            kind="preference", confidence=0.8, age_days=100),
        row("dup_uv_b", "package.manager: uv is the only package manager used here",
            kind="preference", confidence=0.6, age_days=12),
        row("dup_ruff_a", "style.tool: ruff formats and lints the tree", kind="preference",
            confidence=0.7, age_days=80),
        row("dup_ruff_b", "style.tool: ruff formats and lints the tree", kind="preference",
            confidence=0.5, age_days=20),
        row("dup_gate_a", "gate.rule: no command may exit 0 while pretending", kind="decision",
            source="owner", confidence=0.9, age_days=150, evidence=1),
        row("dup_gate_b", "gate.rule: no command may exit 0 while pretending", kind="decision",
            confidence=0.5, age_days=60),
        row("dup_jevcache_a", "jev.cache: decisions are cached per request digest", kind="fact",
            confidence=0.7, age_days=70),
        row("dup_jevcache_b", "jev.cache: decisions are cached per request digest", kind="fact",
            confidence=0.55, age_days=9),
        row("dup_lock_a", "lock.rule: never take the lock while holding a transaction",
            kind="decision", confidence=0.75, age_days=95),
        row("dup_lock_b", "lock.rule: never take the lock while holding a transaction",
            kind="decision", confidence=0.6, age_days=25),
        row("dup_events_a", "events.schema: events carry a session id and a ts", kind="fact",
            confidence=0.8, age_days=110),
        row("dup_events_b", "events.schema: events carry a session id and a ts", kind="fact",
            confidence=0.5, age_days=15),

        # -- group G: near-duplicates (paraphrase, shared subject) ----------- #
        row("near_pytest_a", "test.runner: pytest runs the test suite", kind="preference",
            confidence=0.7, age_days=85),
        row("near_pytest_b", "test.runner: pytest runs the full test suite", kind="preference",
            confidence=0.65, age_days=11),
        row("near_sed_a", "shell.rule: never use sed in place for edits", kind="preference",
            confidence=0.8, age_days=75, source="owner", evidence=1),
        row("near_sed_b", "shell.rule: never use sed inplace edits on the tree",
            kind="preference", confidence=0.6, age_days=14),
        row("near_docker_a", "docker.rule: the desktop app runs in a container", kind="fact",
            confidence=0.7, age_days=65),
        row("near_docker_b", "docker.rule: the desktop app runs inside a container", kind="fact",
            confidence=0.6, age_days=18),
        row("near_strict_a", "test.rule: a test must never write the real hermes home",
            kind="decision", source="owner", confidence=0.9, age_days=130, evidence=2),
        row("near_strict_b", "test.rule: a test must never write the real hermes home dir",
            kind="decision", confidence=0.7, age_days=22),

        # -- group H: staleness --------------------------------------------- #
        # Newer than the durable fact it competes with, and expired anyway: a
        # short-TTL operational note. This is the hit a flat, TTL-blind list
        # happily returns and this store must not.
        row("flaky_expired", "flaky.incident: the checkout suite flakes under CI load",
            kind="incident", confidence=0.9, age_days=20, ttl_days=5, evidence=2,
            session="s-flake-1"),
        row("flaky_durable", "ci.policy: retries must not hide flakes in the checkout suite",
            kind="decision", confidence=0.6, age_days=400, session="s-flake-2"),
        row("old_incident", "deploy.incident: the 2025 rollout stalled on a stuck migration",
            kind="incident", confidence=0.7, age_days=200, ttl_days=180, session="s-old-1"),
        row("rollout_policy", "rollout.policy: a stuck migration aborts the rollout",
            kind="decision", confidence=0.7, age_days=250, session="s-old-3"),
        row("old_note", "nightshift.incident: consolidation once double-counted two sessions",
            kind="incident", confidence=0.6, age_days=250, ttl_days=180, session="s-old-2"),
        row("old_fact", "python.version: the project runs on python 3.12", age_days=340),
        row("old_pref", "commit.style: commit subjects stay imperative and short",
            kind="preference", age_days=320),

        # -- group I: distinct, ordinary memories (the corpus body) ---------- #
        row("proj_checkout", "checkout.project: the checkout service owns cart and payment",
            kind="project", confidence=0.8, age_days=210, scope="project", scope_id="checkout"),
        row("ent_marta", "person.marta: owns the release checklist and the deploy keys",
            kind="entity", confidence=0.8, age_days=180),
        row("ent_tomas", "person.tomas: owns the checkout service", kind="entity",
            confidence=0.7, age_days=150),
        row("pref_typing", "typing.rule: no untyped dicts across module boundaries",
            kind="preference", confidence=0.85, age_days=140, source="owner", evidence=1),
        row("pref_naming", "naming.rule: public functions get docstrings with a purpose line",
            kind="preference", confidence=0.7, age_days=120),
        row("dec_exit", "exit.codes: 0 ok, 1 usage, 2 verification failed, 5 not found",
            kind="decision", confidence=0.9, age_days=200, source="owner", evidence=2),
        row("dec_dryrun", "write.policy: every mutating command is a dry run unless --apply",
            kind="decision", confidence=0.85, age_days=170, source="owner", evidence=1),
        row("fact_sqlite", "storage.engine: memory stores are sqlite files under the runtime",
            kind="fact", confidence=0.8, age_days=160),
        row("fact_wal", "sqlite.mode: journal_mode stays default, no WAL", kind="fact",
            confidence=0.6, age_days=100),
        row("fact_fts", "search.engine: fts5 backs the incident signature search", kind="fact",
            confidence=0.75, age_days=190),
        row("fact_embedding", "embedding.provider: hashing embeddings use character trigrams",
            kind="fact", confidence=0.9, age_days=45, source="owner", evidence=1),
        row("pref_json", "output.rule: --json prints one envelope on stdout, text on stderr",
            kind="preference", confidence=0.8, age_days=130),
        row("ent_sam", "person.sam: reviews the security findings", kind="entity",
            confidence=0.6, age_days=95),
        row("proj_gv", "godsvoice.version: the product CLI reports 1.5.0", kind="project",
            confidence=0.7, age_days=88),
        row("dec_scope", "memory.scope: global memories apply everywhere, project ones do not",
            kind="decision", confidence=0.8, age_days=105),
        row("fact_ttl", "memory.ttl: incidents default to a 180 day expiry", kind="fact",
            confidence=0.7, age_days=60),
        row("pref_lang", "language.rule: all product strings are english", kind="preference",
            confidence=0.9, age_days=300, source="owner", evidence=2),
        row("fact_registry", "registry.truth: one runtime dir, one tools db, one project id",
            kind="fact", confidence=0.8, age_days=175),
        row("dec_budget", "budget.rule: spend is reserved before it is spent", kind="decision",
            confidence=0.8, age_days=185),
        row("inc_rename", "rename.incident: the godsv to skaldir rename broke one import path",
            kind="incident", confidence=0.7, age_days=240, ttl_days=180),
        row("inc_secrets", "secretscan.incident: a token leaked into a test fixture once",
            kind="incident", confidence=0.6, age_days=220, ttl_days=180),
        row("ent_nous", "vendor.nous: owns the hermes agent runtime", kind="entity",
            confidence=0.7, age_days=260),
        row("proj_desktop", "desktop.project: the panel talks to the CLI over the host bridge",
            kind="project", confidence=0.7, age_days=115),
        row("fact_hostb", "host.bridge: binds a unix socket in the runtime dir", kind="fact",
            confidence=0.75, age_days=125),
        row("pref_docs", "docs.rule: MVP notes record the measured number, not the intent",
            kind="preference", confidence=0.85, age_days=55, source="owner", evidence=1),
        row("dec_review", "review.rule: a security finding needs an independent reviewer",
            kind="decision", confidence=0.8, age_days=145),
        row("fact_mcp", "mcp.server: the tool hub is exposed over stdio", kind="fact",
            confidence=0.7, age_days=135),
        row("ent_redis", "service.redis: runs in the compose stack for tests", kind="entity",
            confidence=0.6, age_days=195),
        row("pref_branch", "branch.rule: work lands through a reviewable branch", kind="preference",
            confidence=0.7, age_days=165),
        # the documented lexical-limit target: no token shared with its query
        row("rollback_proc", "rollback.procedure: revert procedure for broken deploys",
            kind="decision", confidence=0.8, age_days=50),
    ]
    return rows


def build_synthetic_corpus(
    store: TypedMemoryStore, *, now: float | None = None
) -> CorpusPlan:
    """Fill ``store`` with the synthetic corpus and return the labeled plan.

    Deterministic: same rows, same relative ages, same ids for a given ``now``.
    Everything the later measurement asserts (which pairs must merge, which must
    be flagged, which are expired) is declared in the plan, so a failure names
    the expectation rather than a number.
    """
    stamp = store.now() if now is None else float(now)
    ids: dict[str, str] = {}
    rows = _corpus_rows()
    for row in rows:
        created_at = stamp - row["age_days"] * 86400.0
        ttl_seconds = None if row["ttl_days"] is None else row["ttl_days"] * 86400.0
        memory = store.remember(
            row["text"],
            kind=row["kind"],
            scope=row["scope"],
            scope_id=row["scope_id"],
            confidence=row["confidence"],
            provenance={
                "source": row["source"],
                "session": row["session"],
                "evidence": [
                    f"run:{row['session']}-{index + 1}" for index in range(row["evidence"])
                ],
                "note": "synthetic corpus (MVP/31) — not a real observation",
            },
            ttl_seconds=ttl_seconds,
            now=created_at,
            actor="corpus",
        )
        ids[row["tag"]] = memory.id

    queries = [
        CorpusQuery("what port does the prod deploy listen on", ids["port_prod_owner"],
                    scope="project", scope_id="prod",
                    note="owner truth vs a newer agent claim in the same scope"),
        CorpusQuery("which port does the staging deploy listen on", ids["port_staging"],
                    scope="project", scope_id="staging",
                    note="scope decides against the prod/global memories"),
        CorpusQuery("release checklist owner", ids["release_owner_truth"],
                    note="owner-authored beats a newer, more confident agent claim"),
        CorpusQuery("logging sink decision", ids["log_winner"],
                    note="weighted evidence beats raw confidence; the loser is newer"),
        CorpusQuery("test timeout for the suite", ids["timeout_new"],
                    note="newest wins inside the flag rule; the loser is older"),
        CorpusQuery("do retries hide flakes in the checkout suite", ids["flaky_durable"],
                    note="the TTL-expired incident is the better textual match"),
        CorpusQuery("stuck migration during the rollout", ids["rollout_policy"],
                    note="the expired incident is newer and matches better; TTL forbids it"),
        CorpusQuery("package manager used here", ids["dup_uv_a"],
                    note="its exact duplicate must be merged away, both copies identical"),
        CorpusQuery("suite timeout is 20 seconds", ids["timeout_new"],
                    note="easy: every ranker agrees"),
        CorpusQuery("exit codes for the cli", ids["dec_exit"], note="easy"),
        CorpusQuery("who owns the checkout service", ids["ent_tomas"], note="easy"),
        CorpusQuery("python version in use", ids["old_fact"],
                    note="easy, and the memory is 340 days old"),
        CorpusQuery("how do we roll back a bad release", ids["rollback_proc"],
                    note="KNOWN MISS: no token shared with the memory"),
    ]
    return CorpusPlan(
        stored=len(rows),
        expected_merges=11,
        expected_contradictions=6,
        expected_supersessions=4,
        expected_flags=2,  # the tie pair: two memories flagged and left unresolved
        expected_expired=5,
        expected_unresolved_pairs=1,
        expected_active_after=49,
        queries=queries,
        ids=ids,
        notes=[
            "ages are relative to the build clock, so structure is deterministic",
            "group F is six exact-duplicate pairs; group G is four near-duplicate pairs; "
            "plus one exact duplicate of the prod-port owner memory (group A)",
            "expected_expired counts the incidents whose default 180-day TTL has passed "
            "plus the short-TTL flake note",
        ],
        known_false_negatives=[
            {
                "ids": [ids["cache_redis"], ids["cache_memcached"]],
                "why": "conflicting proper nouns under one subject: same polarity, no antonym "
                       "pair, no number — the lexical detector cannot see it",
                "policy": "left active; a semantic provider would be needed to catch it",
            }
        ],
        expected_ranking={
            "queries": 13,
            "k": 3,
            "top1": 12,
            "topk": 12,
            "mrr": 0.9231,
            "flat_list_top1": 3,
            "lexical_only_top1": 11,
            "known_miss": "how do we roll back a bad release",
            "measured_on": "2026-09-18, venv python 3.12.13, HashingEmbeddings(dim=256)",
        },
    )


@dataclass
class RankingMeasurement:
    k: int
    queries: int
    top1: int
    topk: int
    mrr: float
    baseline_newest: dict[str, Any]
    baseline_lexical: dict[str, Any]
    failures: list[dict[str, Any]] = field(default_factory=list)
    margins: list[dict[str, Any]] = field(default_factory=list)
    per_query: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "queries": self.queries,
            "top1": self.top1,
            "topk": self.topk,
            "precision_at_1": round(self.top1 / self.queries, 4) if self.queries else 0.0,
            "precision_at_k": round(self.topk / self.queries, 4) if self.queries else 0.0,
            "mrr": round(self.mrr, 4),
            "baseline_newest": dict(self.baseline_newest),
            "baseline_lexical": dict(self.baseline_lexical),
            "failures": list(self.failures),
            "margins": list(self.margins),
            "per_query": list(self.per_query),
            "notes": list(self.notes),
        }


def _rank_pool(
    store: TypedMemoryStore,
    *,
    now: float,
    include_expired: bool,
    allowed: set[str],
) -> list[Memory]:
    pool: list[Memory] = []
    for memory in store.all():
        if memory.id not in allowed:
            continue
        if memory.status in (STATUS_MERGED, STATUS_FORGOTTEN, STATUS_SUPERSEDED):
            continue
        if not include_expired and memory.is_expired(now):
            continue
        pool.append(memory)
    return pool


def _matches_query(query: str, memory: Memory) -> bool:
    """Is there *evidence* of a match, or only stray character trigrams?

    With a lexical provider a couple of shared trigrams is noise: measured on the
    synthetic corpus, an unrelated query ("zzzz qqqq wwww vvvv") still scores up
    to 0.16 relevance on rows it has nothing to do with. So a non-empty query
    requires either a shared meaningful token, a named subject, or a *strong*
    trigram score (≥ 0.30). Below that, "no memory answers this" is the honest
    answer, not a padded list.
    """
    if not query.strip():
        return True
    if tokens(query) & tokens(memory.text):
        return True
    if memory.subject and set(memory.subject.split()) & tokens(query):
        return True
    return vector_score(query, memory.text) >= 0.30


def _keyword_match(query: str, memory: Memory) -> bool:
    """The flat-list match test: at least one meaningful token in common."""
    return bool(tokens(query) & tokens(memory.text))


def _margin_mechanism(store: TypedMemoryStore, query: CorpusQuery, flat_id: str,
                      now: float) -> str:
    """Name *why* the flat list serves a different row than the ranker does.

    The point of the measurement is not the score gap but the mechanism, so each
    margin is labelled with the policy that produced it.
    """
    row = store.get(flat_id)
    if row is None:
        return "unknown row"
    if row.status == STATUS_SUPERSEDED:
        return "supersession: the flat list serves a retired row"
    if row.status == STATUS_MERGED:
        return "merge: the flat list serves a duplicate copy"
    if row.status == STATUS_FORGOTTEN:
        return "forget: the flat list serves a withdrawn row"
    if row.is_expired(now):
        return "TTL: the flat list serves an expired row"
    if query.scope and (row.scope != query.scope or row.scope_id != query.scope_id):
        return "scope: the flat list serves a row from another scope"
    return "ranking: same pool, recency/confidence/scope changed the order"


def measure_ranking(
    store: TypedMemoryStore,
    queries: Sequence[CorpusQuery],
    *,
    k: int = DEFAULT_K,
    now: float | None = None,
    ids: dict[str, str] | None = None,
) -> RankingMeasurement:
    """Measure the ranking against two baselines on the same candidate pool.

    * **flat-list (newest-first)** — what a flat fact list does: the most recent
      matching row wins. Its pool is **every row ever written** — retired
      (superseded/merged/forgotten) and TTL-expired rows included — because a
      flat list has no supersession, no merge and no expiry. That is the
      hypothesis under test, not a handicap: those rows are exactly what it
      would serve.
    * **lexical-only** — ``memory.similarity`` against the query, over the *same
      live pool* as our ranker, so the difference isolates the ranking policy
      (recency/confidence/scope/supersession) from the haystack.

    Both baselines use the same keyword match test a flat list can do
    (:func:`_keyword_match`), so neither is defeated by trigram noise.
    """
    stamp = store.now() if now is None else float(now)
    live_ids = {
        memory.id
        for memory in store.all()
        if memory.status not in (STATUS_MERGED, STATUS_FORGOTTEN, STATUS_SUPERSEDED)
    }
    all_memories = store.all()
    top1 = topk = 0
    reciprocal = 0.0
    flat_top1 = flat_topk = 0
    lexical_top1 = lexical_topk = 0
    failures: list[dict[str, Any]] = []
    margins: list[dict[str, Any]] = []
    per_query: list[dict[str, Any]] = []

    for query in queries:
        ours = store.recall(
            query.query, k=k, scope=query.scope or None, scope_id=query.scope_id, now=stamp
        )
        ours_ids = [hit.memory_id for hit in ours]
        if ours_ids and ours_ids[0] == query.expected:
            top1 += 1
        if query.expected in ours_ids:
            topk += 1
        if query.expected in ours_ids:
            reciprocal += 1.0 / (ours_ids.index(query.expected) + 1)

        # flat-list: every row ever written, most recent keyword match first.
        flat_order = sorted(all_memories, key=lambda item: item.created_at, reverse=True)
        flat_match = [item for item in flat_order if _keyword_match(query.query, item)]
        flat_top = flat_match[0].id if flat_match else ""
        if flat_top == query.expected:
            flat_top1 += 1
        if query.expected in [item.id for item in flat_match[:k]]:
            flat_topk += 1

        # lexical-only: the same live pool as ours, ranked on text alone.
        pool_strict = _rank_pool(store, now=stamp, include_expired=False, allowed=live_ids)
        lexical_order = sorted(
            pool_strict, key=lambda item: similarity(query.query, item.text), reverse=True
        )
        lexical_match = [item for item in lexical_order if _keyword_match(query.query, item)]
        lexical_top = lexical_match[0].id if lexical_match else ""
        if lexical_top == query.expected:
            lexical_top1 += 1
        if query.expected in [item.id for item in lexical_match[:k]]:
            lexical_topk += 1

        per_query.append(
            {
                "query": query.query,
                "expected": query.expected,
                "ours": ours_ids,
                "flat_list": flat_top,
                "lexical_only": lexical_top,
                "ours_right": bool(ours_ids) and ours_ids[0] == query.expected,
                "flat_right": flat_top == query.expected,
                "lexical_right": lexical_top == query.expected,
                "note": query.note,
            }
        )

        if not ours_ids or ours_ids[0] != query.expected:
            runner_up = next((hit for hit in ours if hit.memory_id != query.expected), None)
            failures.append(
                {
                    "query": query.query,
                    "expected": query.expected,
                    "ours": ours_ids,
                    "flat_list": flat_top,
                    "lexical_only": lexical_top,
                    "note": query.note,
                    "runner_up": runner_up.to_dict() if runner_up else None,
                }
            )
        elif flat_top and flat_top != query.expected:
            wrong_pick = next(
                (hit for hit in ours if hit.memory_id == flat_top), None
            )
            margins.append(
                {
                    "query": query.query,
                    "mechanism": _margin_mechanism(store, query, flat_top, stamp),
                    "ours": ours[0].to_dict(),
                    "baseline_pick": wrong_pick.to_dict() if wrong_pick else {"id": flat_top},
                    "score_gap": (
                        round(ours[0].score - wrong_pick.score, 4) if wrong_pick else None
                    ),
                }
            )

    total = len(queries)
    return RankingMeasurement(
        k=k,
        queries=total,
        top1=top1,
        topk=topk,
        mrr=(reciprocal / total) if total else 0.0,
        baseline_newest={
            "top1": flat_top1,
            "topk": flat_topk,
            "precision_at_1": round(flat_top1 / total, 4) if total else 0.0,
            "policy": "flat fact list: newest keyword-matching row in the whole table",
            "pool": "every row ever written, retired and expired rows included",
        },
        baseline_lexical={
            "top1": lexical_top1,
            "topk": lexical_topk,
            "precision_at_1": round(lexical_top1 / total, 4) if total else 0.0,
            "policy": "memory.similarity only, no recency/confidence/scope",
            "pool": "the same live pool our ranker sees",
        },
        failures=failures,
        margins=margins,
        per_query=per_query,
        notes=[
            "synthetic corpus, self-authored labels: an upper bound, not field data",
            "queries where every ranker misses are reported in failures, uncorrected",
            "flat-list pool: every row ever written (retired and expired included) — "
            "that is what a list with no supersession/merge/TTL actually serves",
            "lexical-only pool: the same live set our ranker sees, so the gap is the "
            "ranking policy and not a smaller haystack",
        ],
    )
