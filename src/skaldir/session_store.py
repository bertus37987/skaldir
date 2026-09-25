"""Durable session history — the message store the goal record never had (MVP/40).

The PERSIST layer already owns a *goal* record (``runs.db``, :mod:`skaldir.runs_store`)
and an *event* stream (``events.db``, :mod:`skaldir.events`). What it never had is the
thing an agent conversation is actually made of: the **messages**. This module is that
store, in its own file (``<runtime>/sessions.db``) so no existing store's schema or
audit changes:

* ``sessions`` — one row per conversation: title, created/updated, message count, the
  monotonic ``last_seq``, the head message and its role, a running token estimate, and
  the **lineage** (``parent_session_id`` + ``fork_point_message_id`` + ``lineage_root``
  + ``depth``) that makes a fork or a rotation traceable.
* ``messages`` — one row per message: a **globally unique** ``message_id``, the session,
  a per-session **monotonic ``seq``** (``UNIQUE(session_id, seq)``), the role, the body,
  its own ``content_sha256``, a timestamp, a **labelled** token estimate, the previous
  message id, and (for a copied message) the ``origin_message_id`` it came from.
* ``messages_fts`` — an FTS5 index over the bodies, maintained in the same transaction
  as the append; ``session search`` is bounded (``LIMIT``) and ranked by ``bm25``.
* ``message_events`` — the append-only audit trail: every append, every refusal, every
  purge/rotation decision, with metadata only (no body text).
* ``purged_sessions`` — the tombstone of a purged session, so lineage stays honest and
  a deletion is provable after the content is gone.

The four invariants — enforced twice, in code *and* in the schema
------------------------------------------------------------------
The rule that makes a history trustworthy is that it can only grow. Each of these is a
Python refusal **and** a SQLite trigger, so an out-of-band ``UPDATE``/``DELETE``/
``INSERT`` through the sqlite3 CLI hits the same wall a caller does:

1. **Strict role alternation** — two messages of the same role in a row are refused
   (:class:`RoleAlternationError`; trigger ``messages_roles_alternate``). A conversation
   that would start with two ``user`` turns is precisely the bug this catches.
2. **No mutation of a past message** — ``UPDATE`` on ``messages`` aborts
   (:class:`ImmutableMessageError`; trigger ``messages_no_update``). The session's
   identity fields are frozen the same way (trigger ``sessions_identity_immutable``).
3. **Append-only** — ``DELETE`` on ``messages`` aborts unless the transaction holds the
   **purge token** for exactly that session, which only :meth:`SessionStore.purge` sets,
   inside the same transaction, for the one session being purged (trigger
   ``messages_no_delete``). ``message_events`` cannot be updated or deleted at all.
4. **Monotonic sequence** — ``seq`` must be the previous maximum + 1 (trigger
   ``messages_seq_monotonic``), and the unique index is the second fence. A hole or a
   re-used number cannot be written even by hand.

Resume
------
:meth:`SessionStore.resume` reopens a session and returns the exact prior history plus
:meth:`SessionStore.verify_history`'s answer: the stored ``history_sha256`` recomputed
from the rows, a per-message ``content_sha256`` check (which still catches an edited body
even if someone refreshed the session hash), and ``next_role`` — the only role the next
append may use, so a continued conversation keeps the alternation. A history that does
not match is reported (``matches: false``), never repaired and never silently accepted.

Retention: what is kept, what can be purged
-------------------------------------------
See :data:`RETENTION_POLICY` and ``skaldir session policy --json``. In short:

* **Kept forever (by default):** the session row, the messages, the ``message_events``
  audit trail, the ``sessions`` lineage of a purged parent (as a tombstone). Nothing is
  deleted on a timer — there is no TTL here, because a history that silently disappears
  is worse than a large file.
* **Purgeable on request:** a session body — ``truncate`` (drop the oldest messages,
  keep the newest ``keep_last``) or ``delete`` (drop the session's messages and its
  events, keep a tombstone). Both are ``--dry-run`` by default and need ``--apply``.
* **Never purged:** the append-only invariant itself, the tombstone, and the audit rows
  of *other* sessions.

Rotation is the non-destructive answer to a session that grows too long:
:meth:`SessionStore.rotate` copies the newest ``keep_last`` messages into a successor
session (fresh ids, ``origin_message_id`` pointing back), archives the parent and links
the two through the lineage, so nothing is lost and the active window stays small.

Honest limits (measured, not hidden)
------------------------------------
* Token counts are a **labelled** ``chars/4`` estimate (:data:`TOKEN_METHOD`); no
  tokenizer is bundled, so a count is an estimate that says so.
* Search is FTS5 ``unicode61`` — a keyword/token index, **not** semantic. A query whose
  words do not appear in a body finds nothing (:meth:`SessionStore.search` reports its
  ``engine``). Without FTS5 in the sqlite build the store degrades to a bounded ``LIKE``
  scan and says ``engine: "like"``.
* Strict alternation means a turn with several tool results must be stored as **one**
  message (role ``tool``, a list body) or split across assistant/tool pairs — the store
  refuses ``tool`` immediately after ``tool`` on purpose.
* One writer per file, like ``runs.db``: the lease is a compare-and-swap on
  ``pid=<pid>:boot=<boot_id>`` and a second live process is refused, not merged.
* There is no content encryption: the bodies are plain text in the sqlite file, and a
  purge does not securely erase them (no ``VACUUM``/``secure_delete`` guarantee).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .registry import default_runtime_dir
from .runs_store import owner_pid, owner_string, pid_alive

# --- the vocabulary ---------------------------------------------------------- #

SYSTEM = "system"
USER = "user"
ASSISTANT = "assistant"
TOOL = "tool"

#: The roles a message may have. Order is fixed — it is what ``session show`` renders.
ROLES: tuple[str, ...] = (SYSTEM, USER, ASSISTANT, TOOL)

#: The two roles a normal chat turn alternates between.
CHAT_ROLES: tuple[str, ...] = (USER, ASSISTANT)

#: The role a continued conversation is *expected* to use next, per current head. This is
#: a preference, not the invariant: the invariant is only "not the same role as the head"
#: (any of the three others is legal — see :data:`ROLES`).
NEXT_ROLE_PREFERENCE: dict[str, str] = {
    "": USER,
    SYSTEM: USER,
    USER: ASSISTANT,
    ASSISTANT: USER,
    TOOL: ASSISTANT,
}

#: Token counts are an estimate and say which method produced them (MVP/40 §limits).
TOKEN_METHOD = "chars/4"

SCHEMA_VERSION = 1

#: Sequence numbers start at 1 — 0 means "no message".
FIRST_SEQ = 1

# --- bounds (every read in this module is bounded) --------------------------- #

DEFAULT_MESSAGE_LIMIT = 200
MAX_MESSAGE_LIMIT = 5000
DEFAULT_SESSION_LIMIT = 50
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 500
#: How much of a body a search hit carries back (a snippet, never the whole history).
SNIPPET_TOKENS = 14

DEFAULT_LEASE_TTL = 120.0

# --- the delete/rotate policy, as data (``session policy --json`` reads this) -- #

PURGE_MODES: tuple[str, ...] = ("truncate", "delete")
DEFAULT_KEEP_LAST = 20

RETENTION_POLICY: dict = {
    "default_mode": "archive",
    "modes": {
        "archive": {
            "destructive": False,
            "effect": "the session is marked archived and leaves the default listing; "
                      "every message is kept and stays searchable",
            "reversible": True,
        },
        "rotate": {
            "destructive": False,
            "effect": f"the newest {DEFAULT_KEEP_LAST} messages are copied into a successor "
                      "session (fresh ids, origin_message_id pointing back), the parent is "
                      "archived, and the two are linked through the lineage",
            "reversible": True,
            "loss": "none — the parent keeps every message",
        },
        "truncate": {
            "destructive": True,
            "effect": "the OLDEST messages are deleted, the newest keep_last survive",
            "reversible": False,
            "requires": "--apply (dry run by default)",
        },
        "delete": {
            "destructive": True,
            "effect": "the session's messages and its own audit rows are deleted and a "
                      "tombstone is written into purged_sessions",
            "reversible": False,
            "requires": "--apply (dry run by default)",
        },
    },
    "kept": [
        "the session row and every message, by default and without a timer",
        "message_events — the append-only audit trail (metadata only, never a body)",
        "a purged session's tombstone in purged_sessions (id, title, counts, hash, time, "
        "reason) so the lineage of its children stays honest",
        "the alternation / immutability / monotonicity triggers, which no purge lifts",
    ],
    "purgeable": [
        "message bodies, on request, per session (truncate or delete)",
        "a whole archived session, on request (delete)",
        "the messages_fts rows of the purged session (same transaction as the purge)",
    ],
    "never_purged": [
        "another session's rows — the purge token is scoped to one session id",
        "the audit rows of other sessions",
        "a body by a scheduled job: there is no TTL and no automatic purge in this module",
    ],
    "notes": [
        "everything with a body is purgeable on request and by name; nothing is purged "
        "automatically",
        "there is no secure-erase guarantee: a purge removes rows, it does not rewrite "
        "the file (no VACUUM, no secure_delete)",
    ],
}

# --- errors ------------------------------------------------------------------ #


class SessionError(RuntimeError):
    """Base class for this module's refusals."""


class RoleAlternationError(SessionError):
    """Two messages of the same role in a row — the alternation invariant refuses it."""


class UnknownRole(SessionError):
    """A role outside :data:`ROLES`."""


class ImmutableMessageError(SessionError):
    """A past message was mutated: the store is append-only."""


class SequenceError(SessionError):
    """A message was offered out of sequence (a hole, a re-use, a regression)."""


class UnknownSession(SessionError):
    """No session row matches the id."""


class CorruptSessionStore(SessionError):
    """``sessions.db`` is not a readable store — never silently recreated."""


class WriterConflict(SessionError):
    """Another live process owns the store's single-writer lease."""


class DuplicateMessage(SessionError):
    """A message id that already exists — ids are never reused."""


# --------------------------------------------------------------------------- #
# small, pure helpers                                                          #
# --------------------------------------------------------------------------- #


def _now(value: float | None) -> float:
    return time.time() if value is None else float(value)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _loads(text: object, default: object) -> object:
    try:
        loaded = json.loads(str(text or ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        return default
    return loaded


def _load_list(text: object) -> list:
    loaded = _loads(text, [])
    return list(loaded) if isinstance(loaded, (list, tuple)) else []


def _load_dict(text: object) -> dict:
    loaded = _loads(text, {})
    return dict(loaded) if isinstance(loaded, Mapping) else {}


def estimate_tokens(text: str) -> int:
    """The labelled ``chars/4`` estimate — the only token count this store has.

    Empty text is 0; anything else is at least 1, because a message that exists costs
    something. :data:`TOKEN_METHOD` is what a reader is told, so an estimate never looks
    like a tokenizer measurement.
    """
    body = str(text or "")
    if not body:
        return 0
    return max(1, (len(body) + 3) // 4)


def content_hash(text: str) -> str:
    """``sha256`` of the body's exact UTF-8 bytes."""
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def canonical_message_line(message: Mapping[str, Any]) -> str:
    """One message as a canonical line — the unit :func:`history_hash` hashes.

    Everything that identifies the message is included (id, seq, role, body, time,
    estimate, kind), so any of them changing moves the history hash. ``created`` is
    rounded to microseconds and the JSON is key-sorted, so the same history hashes the
    same on any machine.
    """
    return json.dumps(
        {
            "seq": int(message.get("seq") or 0),
            "message_id": str(message.get("message_id") or ""),
            "role": str(message.get("role") or ""),
            "content": str(message.get("content") or ""),
            "created": round(float(message.get("created") or 0.0), 6),
            "token_estimate": int(message.get("token_estimate") or 0),
            "kind": str(message.get("kind") or ""),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def history_hash(messages: Iterable[Mapping[str, Any]]) -> str:
    """``sha256`` over the canonical lines of ``messages``, in the order given.

    Not the stored value — the *recomputation* — so :meth:`SessionStore.verify_history`
    has something independent to compare the recorded hash against.
    """
    digest = hashlib.sha256()
    for message in messages:
        digest.update(canonical_message_line(message).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def new_message_id(session_id: str, seq: int) -> str:
    """A globally unique message id: session prefix + sequence + a random suffix.

    Unique by construction (the suffix) *and* by constraint (``messages.message_id`` is
    the primary key), which is why a duplicate is refused rather than merged.
    """
    prefix = "".join(char for char in str(session_id) if char.isalnum())[:12] or "s"
    seq_text = "".join(char for char in str(seq) if char.isdigit()).rjust(6, "0")
    return f"m-{prefix}-{seq_text}-{uuid.uuid4().hex[:12]}"


def session_store_path(runtime: str | os.PathLike | None = None) -> Path:
    """``<runtime>/sessions.db`` — beside ``runs.db`` and ``events.db``."""
    base = Path(runtime).expanduser() if runtime else default_runtime_dir()
    return base / "sessions.db"


def validate_role(role: str) -> str:
    """The role, or :class:`UnknownRole` — an exact match, never a silent coercion.

    A role must be one of :data:`ROLES` character for character: accepting ``"USER"``
    here while the ``messages`` table's CHECK constraint only knows ``'user'`` would be
    two different rules for one field.
    """
    text = str(role or "")
    if text not in ROLES:
        raise UnknownRole(
            f"unknown role {role!r} — a message is exactly one of {', '.join(ROLES)}"
        )
    return text


def _fts_query(text: str) -> str:
    """A safe FTS5 OR-query from free text (reused from the incident store)."""
    try:  # one query builder for the whole product rather than a second one here
        from .incidents import _fts_query as shared

        return str(shared(text))
    except Exception:  # noqa: BLE001 — standalone build: same rule, inline
        cleaned = "".join(char if char.isalnum() else " " for char in str(text).lower())
        tokens: list[str] = []
        for token in cleaned.split():
            if len(token) >= 2 and token not in tokens:
                tokens.append(token)
        return " OR ".join(tokens)


def _snippet(text: str, query: str, *, tokens: int = SNIPPET_TOKENS) -> str:
    """A window around the first matching token — the fallback when FTS5 is absent."""
    body = str(text or "")
    wanted = [word for word in _fts_query(query).split(" OR ") if word]
    lowered = body.lower()
    position = -1
    for word in wanted:
        found = lowered.find(word)
        if found >= 0 and (position < 0 or found < position):
            position = found
    if position < 0:
        position = 0
    start = max(0, position - 24)
    end = min(len(body), position + max(1, tokens) * 6)
    prefix = "…" if start else ""
    suffix = "…" if end < len(body) else ""
    return f"{prefix}{body[start:end]}{suffix}"


def fts5_available() -> bool:
    """Whether this sqlite build has FTS5 (the search engine, or the degraded path)."""
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute("CREATE VIRTUAL TABLE probe_fts USING fts5(body)")
    except sqlite3.Error:
        return False
    finally:
        probe.close()
    return True


# --------------------------------------------------------------------------- #
# records                                                                      #
# --------------------------------------------------------------------------- #


@dataclass
class Session:
    """The durable conversation record — one row in ``sessions``."""

    session_id: str
    title: str = ""
    created: float = 0.0
    updated: float = 0.0
    parent_session_id: str = ""
    fork_point_message_id: str = ""
    lineage_root: str = ""
    depth: int = 0
    labels: list[str] = field(default_factory=list)
    message_count: int = 0
    last_seq: int = 0
    head_message_id: str = ""
    head_role: str = ""
    token_total: int = 0
    archived: bool = False
    archived_at: float = 0.0
    history_sha256: str = ""
    schema_version: int = SCHEMA_VERSION

    @property
    def next_role(self) -> str:
        """The role a continuation is expected to use next (the invariant, suggested).

        The hard rule is only *"not the head's role"*; this property names the role a
        normal conversation would use, so a resumed session continues sensibly without
        the caller having to re-derive the alternation.
        """
        return NEXT_ROLE_PREFERENCE.get(str(self.head_role), USER)

    def lineage(self) -> dict:
        """Where this session sits: parent, root and depth (the link, not the history)."""
        return {
            "parent_session_id": self.parent_session_id,
            "fork_point_message_id": self.fork_point_message_id,
            "lineage_root": self.lineage_root or self.session_id,
            "depth": int(self.depth),
            "forked": bool(self.parent_session_id),
        }

    def to_dict(self) -> dict:
        data = dict(self.__dict__)
        data["archived"] = bool(self.archived)
        data["next_role"] = self.next_role
        data["lineage"] = self.lineage()
        data["token_method"] = TOKEN_METHOD
        return data


@dataclass
class Message:
    """One message — append-only, identified by ``message_id``, ordered by ``seq``."""

    message_id: str
    session_id: str
    seq: int
    role: str
    content: str = ""
    content_sha256: str = ""
    created: float = 0.0
    token_estimate: int = 0
    token_method: str = TOKEN_METHOD
    prev_message_id: str = ""
    origin_message_id: str = ""
    kind: str = "chat"
    refs: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def to_dict(self, *, body: bool = True) -> dict:
        data = dict(self.__dict__)
        if not body:
            data.pop("content", None)
        data["body_included"] = bool(body)
        data["token_method"] = self.token_method or TOKEN_METHOD
        return data


def _session_from_row(row: Mapping) -> Session:
    return Session(
        session_id=str(row["session_id"]),
        title=str(row["title"] or ""),
        created=float(row["created"] or 0.0),
        updated=float(row["updated"] or 0.0),
        parent_session_id=str(row["parent_session_id"] or ""),
        fork_point_message_id=str(row["fork_point_message_id"] or ""),
        lineage_root=str(row["lineage_root"] or ""),
        depth=int(row["depth"] or 0),
        labels=[str(item) for item in _load_list(row["labels"])],
        message_count=int(row["message_count"] or 0),
        last_seq=int(row["last_seq"] or 0),
        head_message_id=str(row["head_message_id"] or ""),
        head_role=str(row["head_role"] or ""),
        token_total=int(row["token_total"] or 0),
        archived=bool(row["archived"]),
        archived_at=float(row["archived_at"] or 0.0),
        history_sha256=str(row["history_sha256"] or ""),
        schema_version=int(row["schema_version"] or SCHEMA_VERSION),
    )


def _message_from_row(row: Mapping) -> Message:
    return Message(
        message_id=str(row["message_id"]),
        session_id=str(row["session_id"]),
        seq=int(row["seq"]),
        role=str(row["role"]),
        content=str(row["content"] or ""),
        content_sha256=str(row["content_sha256"] or ""),
        created=float(row["created"] or 0.0),
        token_estimate=int(row["token_estimate"] or 0),
        token_method=str(row["token_method"] or TOKEN_METHOD),
        prev_message_id=str(row["prev_message_id"] or ""),
        origin_message_id=str(row["origin_message_id"] or ""),
        kind=str(row["kind"] or "chat"),
        refs=[str(item) for item in _load_list(row["refs"])],
        meta=_load_dict(row["meta"]),
    )


# --------------------------------------------------------------------------- #
# schema                                                                       #
# --------------------------------------------------------------------------- #

_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS session_store_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id            TEXT PRIMARY KEY,
        schema_version        INTEGER NOT NULL DEFAULT 1,
        title                 TEXT NOT NULL DEFAULT '',
        created               REAL NOT NULL DEFAULT 0,
        updated               REAL NOT NULL DEFAULT 0,
        parent_session_id     TEXT NOT NULL DEFAULT '',
        fork_point_message_id TEXT NOT NULL DEFAULT '',
        lineage_root          TEXT NOT NULL DEFAULT '',
        depth                 INTEGER NOT NULL DEFAULT 0,
        labels                TEXT NOT NULL DEFAULT '[]',
        message_count         INTEGER NOT NULL DEFAULT 0,
        last_seq              INTEGER NOT NULL DEFAULT 0,
        head_message_id       TEXT NOT NULL DEFAULT '',
        head_role             TEXT NOT NULL DEFAULT '',
        token_total           INTEGER NOT NULL DEFAULT 0,
        archived              INTEGER NOT NULL DEFAULT 0,
        archived_at           REAL NOT NULL DEFAULT 0,
        history_sha256        TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        message_id       TEXT PRIMARY KEY,
        session_id       TEXT NOT NULL,
        seq              INTEGER NOT NULL,
        role             TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
        content          TEXT NOT NULL DEFAULT '',
        content_sha256   TEXT NOT NULL DEFAULT '',
        created          REAL NOT NULL DEFAULT 0,
        token_estimate   INTEGER NOT NULL DEFAULT 0,
        token_method     TEXT NOT NULL DEFAULT 'chars/4',
        prev_message_id  TEXT NOT NULL DEFAULT '',
        origin_message_id TEXT NOT NULL DEFAULT '',
        kind             TEXT NOT NULL DEFAULT 'chat',
        refs             TEXT NOT NULL DEFAULT '[]',
        meta             TEXT NOT NULL DEFAULT '{}',
        UNIQUE (session_id, seq)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS message_events (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        at         REAL NOT NULL DEFAULT 0,
        kind       TEXT NOT NULL DEFAULT '',
        seq        INTEGER NOT NULL DEFAULT 0,
        message_id TEXT NOT NULL DEFAULT '',
        role       TEXT NOT NULL DEFAULT '',
        detail     TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS purged_sessions (
        session_id        TEXT PRIMARY KEY,
        title             TEXT NOT NULL DEFAULT '',
        parent_session_id TEXT NOT NULL DEFAULT '',
        message_count     INTEGER NOT NULL DEFAULT 0,
        last_seq          INTEGER NOT NULL DEFAULT 0,
        history_sha256    TEXT NOT NULL DEFAULT '',
        purged_at         REAL NOT NULL DEFAULT 0,
        mode              TEXT NOT NULL DEFAULT '',
        reason            TEXT NOT NULL DEFAULT ''
    )
    """,
    # -- the invariants, as triggers: an out-of-band write hits the same wall -- #
    """
    CREATE TRIGGER IF NOT EXISTS messages_no_update
    BEFORE UPDATE ON messages
    BEGIN
        SELECT RAISE(ABORT,
            'messages are append-only: a past message is never mutated');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_no_delete
    BEFORE DELETE ON messages
    WHEN COALESCE(
            (SELECT value FROM session_store_meta WHERE key = 'purge_token'), '')
         <> OLD.session_id
    BEGIN
        SELECT RAISE(ABORT,
            'messages are append-only: only a purge of this very session may delete one');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_seq_monotonic
    BEFORE INSERT ON messages
    WHEN NEW.seq <> COALESCE(
            (SELECT MAX(seq) FROM messages WHERE session_id = NEW.session_id), 0) + 1
    BEGIN
        SELECT RAISE(ABORT,
            'sequence must be the previous maximum + 1: no hole, no regression');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_roles_alternate
    BEFORE INSERT ON messages
    WHEN NEW.role = COALESCE(
            (SELECT role FROM messages WHERE session_id = NEW.session_id
             ORDER BY seq DESC LIMIT 1), '')
    BEGIN
        SELECT RAISE(ABORT,
            'roles must alternate: two messages of the same role in a row');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS sessions_identity_immutable
    BEFORE UPDATE ON sessions
    WHEN NEW.session_id <> OLD.session_id
      OR NEW.created <> OLD.created
      OR NEW.lineage_root <> OLD.lineage_root
      OR NEW.parent_session_id <> OLD.parent_session_id
    BEGIN
        SELECT RAISE(ABORT,
            'a session identity (id, created, parent, lineage root) is immutable');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS message_events_no_update
    BEFORE UPDATE ON message_events
    BEGIN
        SELECT RAISE(ABORT, 'message_events is an append-only audit trail');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS message_events_no_delete
    BEFORE DELETE ON message_events
    BEGIN
        SELECT RAISE(ABORT, 'message_events is an append-only audit trail');
    END
    """,
    "CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, seq)",
    "CREATE INDEX IF NOT EXISTS messages_role ON messages(role)",
    "CREATE INDEX IF NOT EXISTS messages_created ON messages(created)",
    "CREATE INDEX IF NOT EXISTS messages_origin ON messages(origin_message_id)",
    "CREATE INDEX IF NOT EXISTS sessions_updated ON sessions(updated)",
    "CREATE INDEX IF NOT EXISTS sessions_parent ON sessions(parent_session_id)",
    "CREATE INDEX IF NOT EXISTS events_session ON message_events(session_id, id)",
)

#: The FTS index, separate because it must be created after ``messages`` exists and
#: because a build without FTS5 has to carry on without it.
_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
USING fts5(content, message_id UNINDEXED, session_id UNINDEXED, role UNINDEXED, seq UNINDEXED)
"""

#: Additive columns a future build (or a sibling wave) may add to an existing table.
_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "sessions": {
        "archived": "INTEGER NOT NULL DEFAULT 0",
        "archived_at": "REAL NOT NULL DEFAULT 0",
        "history_sha256": "TEXT NOT NULL DEFAULT ''",
        "token_total": "INTEGER NOT NULL DEFAULT 0",
        "depth": "INTEGER NOT NULL DEFAULT 0",
        "labels": "TEXT NOT NULL DEFAULT '[]'",
    },
    "messages": {
        "origin_message_id": "TEXT NOT NULL DEFAULT ''",
        "kind": "TEXT NOT NULL DEFAULT 'chat'",
        "token_method": "TEXT NOT NULL DEFAULT 'chars/4'",
        "refs": "TEXT NOT NULL DEFAULT '[]'",
        "meta": "TEXT NOT NULL DEFAULT '{}'",
    },
}

_META_WRITER = "writer"
_META_PURGE_TOKEN = "purge_token"


# --------------------------------------------------------------------------- #
# the store                                                                    #
# --------------------------------------------------------------------------- #


class SessionStore:
    """The single writer of ``sessions.db``: sessions, messages, search, purge.

    Write mechanics are the ones already proven in this package (``runs_store``): one
    ``BEGIN IMMEDIATE`` transaction per change under a re-entrant in-process lock, WAL +
    ``synchronous=FULL`` so a committed message survives ``kill -9``, atomic
    temp+``fsync``+``os.replace`` for any file written, and an additive ``ALTER TABLE``
    migration behind a ``schema_version`` fence.
    """

    def __init__(
        self,
        path: str | os.PathLike | None = None,
        *,
        owner: str = "",
        lease_ttl: float = DEFAULT_LEASE_TTL,
        create: bool = True,
    ) -> None:
        self.path = str(Path(path).expanduser() if path else session_store_path())
        self.lease_ttl = float(lease_ttl)
        self.owner = owner or owner_string()
        self._lock = threading.RLock()
        self._tx_depth = 0
        self.fts = fts5_available()
        if create:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            self.path, check_same_thread=False, timeout=30.0, isolation_level=None
        )
        self._db.row_factory = sqlite3.Row
        try:
            with self._lock:
                self._db.execute("PRAGMA journal_mode=WAL")
                # FULL, not NORMAL: a committed message must survive kill -9.
                self._db.execute("PRAGMA synchronous=FULL")
                self._db.execute("PRAGMA busy_timeout=30000")
                self._db.execute("PRAGMA foreign_keys=ON")
                for statement in _DDL:
                    self._db.execute(statement)
                if self.fts:
                    self._db.execute(_FTS_DDL)
                self.migrate()
        except sqlite3.DatabaseError as exc:
            with contextlib.suppress(Exception):
                self._db.close()
            raise CorruptSessionStore(
                f"{self.path} is not a readable session store: {type(exc).__name__}: {exc}"
            ) from exc

    # -- lifecycle ---------------------------------------------------------- #

    def close(self) -> None:
        with self._lock:
            with contextlib.suppress(Exception):
                self._db.close()

    def __enter__(self) -> SessionStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextlib.contextmanager
    def transaction(self):
        """One ``BEGIN IMMEDIATE`` transaction; re-entrant (inner blocks join it)."""
        with self._lock:
            outermost = self._tx_depth == 0
            if outermost:
                self._db.execute("BEGIN IMMEDIATE")
            self._tx_depth += 1
            try:
                yield self._db
            except BaseException:
                self._tx_depth -= 1
                if self._tx_depth == 0:
                    with contextlib.suppress(sqlite3.Error):
                        self._db.execute("ROLLBACK")
                raise
            else:
                self._tx_depth -= 1
                if self._tx_depth == 0:
                    self._db.execute("COMMIT")

    # -- schema / migrations ------------------------------------------------ #

    def schema_version(self) -> int:
        value = self.meta("schema_version")
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    def migrate(self) -> int:
        """Additive columns, then the version fence (idempotent)."""
        with self._lock:
            for table, columns in _ADDITIVE_COLUMNS.items():
                existing = {
                    str(row["name"]) for row in self._db.execute(f"PRAGMA table_info({table})")
                }
                for column, spec in columns.items():
                    if column in existing:
                        continue
                    # identifiers are this module's constants, never caller input
                    self._db.execute(  # nosec B608
                        f"ALTER TABLE {table} ADD COLUMN {column} {spec}"
                    )
            if self.schema_version() < SCHEMA_VERSION:
                self.set_meta("schema_version", str(SCHEMA_VERSION))
                self.set_meta("migrated_at", str(_now(None)))
            self._backfill_fts_locked()
        return self.schema_version()

    def _backfill_fts_locked(self) -> None:
        """Index messages missing from the FTS table (idempotent, index repair)."""
        if not self.fts:
            return
        self._db.execute(
            """
            INSERT INTO messages_fts(rowid, content, message_id, session_id, role, seq)
            SELECT m.rowid, m.content, m.message_id, m.session_id, m.role, m.seq
            FROM messages m
            WHERE m.message_id NOT IN (SELECT message_id FROM messages_fts)
            """
        )

    def index_consistency(self) -> dict:
        """Is the FTS index the same size as the message table? (a checkable claim)"""
        with self._lock:
            total = int(self._db.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
            indexed = (
                int(self._db.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0])
                if self.fts
                else 0
            )
        return {
            "engine": "fts5" if self.fts else "like",
            "messages": total,
            "indexed": indexed,
            "consistent": bool(self.fts) and total == indexed,
        }

    def reindex(self) -> dict:
        """Rebuild the FTS index from ``messages`` and report the delta."""
        before = self.index_consistency()
        with self.transaction() as db:
            if self.fts:
                db.execute("DELETE FROM messages_fts")
        self._backfill_fts_locked()
        after = self.index_consistency()
        return {"before": before, "after": after, "repaired": after["indexed"] - before["indexed"]}

    # -- meta --------------------------------------------------------------- #

    def meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM session_store_meta WHERE key = ?", (str(key),)
            ).fetchone()
        return str(row["value"]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO session_store_meta(key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(key), str(value)),
            )

    # -- single writer ------------------------------------------------------ #

    def acquire_writer(
        self,
        *,
        owner: str = "",
        ttl: float | None = None,
        now: float | None = None,
        alive: Callable[[int | None], bool] = pid_alive,
        takeover: bool = True,
    ) -> dict:
        """Acquire/heartbeat the single-writer lease with a compare-and-swap.

        ``action`` is ``acquired``/``heartbeat``/``takeover`` on success and
        ``refused``/``lost`` when another live process holds it (or won the CAS).
        """
        moment = _now(now)
        ttl = self.lease_ttl if ttl is None else float(ttl)
        holder = str(owner or self.owner)
        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM session_store_meta WHERE key = ?", (_META_WRITER,)
            ).fetchone()
            previous = str(row["value"]) if row else ""
            info = _load_dict(previous) if previous else {}
            current = str(info.get("holder") or "")
            expires_at = float(info.get("expires_at") or 0.0)
            same = bool(current) and current == holder
            live = bool(current) and expires_at > moment and alive(owner_pid(current))
            if previous and not same:
                if live:
                    return {
                        "acquired": False,
                        "action": "refused",
                        "holder": current,
                        "expires_at": expires_at,
                        "reason": (
                            f"{self.path} is held by {current} (pid alive, lease until "
                            f"{expires_at:.0f}) — one writer only"
                        ),
                        "owner": holder,
                        "path": self.path,
                    }
                if not takeover:
                    return {
                        "acquired": False,
                        "action": "refused",
                        "holder": current,
                        "expires_at": expires_at,
                        "reason": f"the holder {current} is gone but takeover is disabled",
                        "owner": holder,
                        "path": self.path,
                    }
            action = "heartbeat" if same else ("takeover" if previous else "acquired")
            payload = _json(
                {
                    "holder": holder,
                    "expires_at": moment + ttl,
                    "heartbeat_at": moment,
                    "acquired_at": float(info.get("acquired_at") or moment),
                }
            )
            if not previous:
                cursor = db.execute(
                    "INSERT INTO session_store_meta(key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO NOTHING",
                    (_META_WRITER, payload),
                )
            else:
                cursor = db.execute(
                    "UPDATE session_store_meta SET value = ? WHERE key = ? AND value = ?",
                    (payload, _META_WRITER, previous),
                )
            if cursor.rowcount <= 0:
                fresh = _load_dict(
                    db.execute(
                        "SELECT value FROM session_store_meta WHERE key = ?", (_META_WRITER,)
                    ).fetchone()["value"]
                )
                return {
                    "acquired": False,
                    "action": "lost",
                    "holder": str(fresh.get("holder") or "") or None,
                    "expires_at": float(fresh.get("expires_at") or 0.0),
                    "reason": "another process won the compare-and-swap for the writer lease",
                    "owner": holder,
                    "path": self.path,
                }
        return {
            "acquired": True,
            "action": action,
            "holder": holder,
            "expires_at": moment + ttl,
            "reason": "",
            "owner": holder,
            "path": self.path,
        }

    def release_writer(self, *, owner: str = "") -> bool:
        holder = str(owner or self.owner)
        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM session_store_meta WHERE key = ?", (_META_WRITER,)
            ).fetchone()
            if not row:
                return False
            info = _load_dict(str(row["value"]))
            if str(info.get("holder") or "") != holder:
                return False
            cursor = db.execute(
                "UPDATE session_store_meta SET value = ? WHERE key = ? AND value = ?",
                ("", _META_WRITER, str(row["value"])),
            )
        return cursor.rowcount > 0

    def writer(self, *, now: float | None = None) -> dict:
        moment = _now(now)
        info = _load_dict(self.meta(_META_WRITER) or "{}")
        holder = str(info.get("holder") or "")
        expires_at = float(info.get("expires_at") or 0.0)
        return {
            "holder": holder,
            "owner_pid": owner_pid(holder),
            "pid_alive": pid_alive(owner_pid(holder)) if holder else False,
            "expires_at": expires_at,
            "expired": bool(holder) and expires_at <= moment,
            "heartbeat_at": float(info.get("heartbeat_at") or 0.0),
            "held": bool(holder),
        }

    def assert_writer(self, *, owner: str = "", now: float | None = None) -> None:
        """Refuse a write while another live process owns the store."""
        info = self.writer(now=now)
        if not info["held"]:
            return
        if str(info["holder"]) == str(owner or self.owner):
            return
        if info["pid_alive"] and not info["expired"]:
            raise WriterConflict(
                f"{self.path} is held by {info['holder']} (pid alive, lease until "
                f"{info['expires_at']:.0f}) — one writer only"
            )

    # -- audit trail -------------------------------------------------------- #

    def _record_locked(
        self,
        db: sqlite3.Connection,
        session_id: str,
        kind: str,
        *,
        seq: int = 0,
        message_id: str = "",
        role: str = "",
        detail: str = "",
        at: float = 0.0,
    ) -> None:
        """One append-only audit row — metadata only, never a body."""
        db.execute(
            """
            INSERT INTO message_events(session_id, at, kind, seq, message_id, role, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(session_id), float(at), str(kind), int(seq), str(message_id), str(role),
             str(detail)),
        )

    def events(self, session_id: str = "", *, limit: int = DEFAULT_MESSAGE_LIMIT) -> list[dict]:
        """The audit trail, oldest first, bounded."""
        query = "SELECT * FROM message_events WHERE 1 = 1"
        params: list = []
        if session_id:
            query += " AND session_id = ?"
            params.append(str(session_id))
        query += " ORDER BY id LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._db.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    # -- sessions ----------------------------------------------------------- #

    def create_session(
        self,
        title: str = "",
        *,
        session_id: str = "",
        parent_session_id: str = "",
        fork_point_message_id: str = "",
        labels: Iterable[str] = (),
        at: float | None = None,
        owner: str = "",
    ) -> Session:
        """Create an empty session; the lineage is recorded, never inferred later."""
        moment = _now(at)
        self.assert_writer(owner=owner)
        identifier = str(session_id or "").strip() or f"s-{uuid.uuid4().hex[:12]}"
        parent = str(parent_session_id or "")
        depth = 0
        root = identifier
        if parent:
            parent_record = self.session(parent)
            if parent_record is None:
                raise UnknownSession(
                    f"parent session {parent!r} does not exist — a lineage link names a real "
                    "parent (a purged one is a tombstone, not a parent)"
                )
            depth = int(parent_record.depth) + 1
            root = parent_record.lineage_root or parent_record.session_id
        with self.transaction() as db:
            try:
                db.execute(
                    """
                    INSERT INTO sessions(session_id, schema_version, title, created, updated,
                        parent_session_id, fork_point_message_id, lineage_root, depth, labels,
                        message_count, last_seq, head_message_id, head_role, token_total,
                        archived, archived_at, history_sha256)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, '', '', 0, 0, 0, ?)
                    """,
                    (
                        identifier,
                        SCHEMA_VERSION,
                        str(title or ""),
                        moment,
                        moment,
                        parent,
                        str(fork_point_message_id or ""),
                        root,
                        depth,
                        _json([str(item) for item in labels]),
                        history_hash([]),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise SessionError(f"session {identifier!r} already exists") from exc
            self._record_locked(
                db, identifier, "session_created", detail=f"title={title!r} parent={parent!r}",
                at=moment,
            )
        record = self.session(identifier)
        if record is None:  # pragma: no cover — defensive
            raise SessionError(f"session {identifier!r} vanished after insert")
        return record

    def session(self, session_id: str) -> Session | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (str(session_id),)
            ).fetchone()
        return _session_from_row(row) if row else None

    def require_session(self, session_id: str) -> Session:
        record = self.session(session_id)
        if record is None:
            tombstone = self.tombstone(session_id)
            if tombstone is not None:
                raise UnknownSession(
                    f"session {session_id!r} was purged at {tombstone['purged_at']:.0f} "
                    f"({tombstone['mode']}: {tombstone['message_count']} message(s) removed, "
                    f"history {tombstone['history_sha256'][:12] or '(none)'}) — the tombstone "
                    "remains, the bodies do not"
                )
            raise UnknownSession(f"no session {session_id!r} in {self.path}")
        return record

    def tombstone(self, session_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM purged_sessions WHERE session_id = ?", (str(session_id),)
            ).fetchone()
        return dict(row) if row else None

    def _lineage_locked(self, session_id: str, *, limit: int = 32) -> list[dict]:
        """Walk the parent chain from the session up to its root (bounded)."""
        chain: list[dict] = []
        current = str(session_id)
        seen: set[str] = set()
        while current and current not in seen and len(chain) < int(limit):
            seen.add(current)
            record = self.session(current)
            if record is None:
                tomb = self.tombstone(current)
                chain.append(
                    {
                        "session_id": current,
                        "present": False,
                        "purged": tomb is not None,
                        "title": str((tomb or {}).get("title") or ""),
                        "parent_session_id": str((tomb or {}).get("parent_session_id") or ""),
                    }
                )
                current = str((tomb or {}).get("parent_session_id") or "")
                continue
            chain.append(
                {
                    "session_id": record.session_id,
                    "present": True,
                    "purged": False,
                    "title": record.title,
                    "parent_session_id": record.parent_session_id,
                }
            )
            current = record.parent_session_id
        return chain

    def lineage(self, session_id: str) -> dict:
        """The chain from the session up to its root, plus the children it forked.

        A **purged** session still has a lineage: its tombstone keeps the id, the title
        and the parent, so a child's ancestry is reported with ``purged: true`` instead of
        raising. Only an id that never existed is :class:`UnknownSession`.
        """
        record = self.session(session_id)
        tomb = None if record is not None else self.tombstone(session_id)
        if record is None and tomb is None:
            raise UnknownSession(f"no session {session_id!r} in {self.path}")
        with self._lock:
            children = self._db.execute(
                "SELECT session_id, title, message_count, updated FROM sessions"
                " WHERE parent_session_id = ? ORDER BY updated",
                (str(session_id),),
            ).fetchall()
        chain = self._lineage_locked(session_id)
        self_purged = record is None and tomb is not None
        return {
            "session_id": str(session_id),
            "present": record is not None,
            "purged": self_purged,
            "root": chain[-1]["session_id"] if chain else str(session_id),
            "depth": len(chain) - 1,
            "chain": chain,
            "children": [dict(row) for row in children],
            "purged_ancestors": [item["session_id"] for item in chain if item["purged"]],
            "tombstone": tomb,
        }

    def sessions(
        self,
        *,
        include_archived: bool = False,
        limit: int = DEFAULT_SESSION_LIMIT,
        order: str = "recent",
    ) -> list[dict]:
        """The session list: title, message count, last activity, lineage (bounded)."""
        query = "SELECT * FROM sessions"
        params: list = []
        if not include_archived:
            query += " WHERE archived = 0"
        if order == "created":
            query += " ORDER BY created DESC, session_id"
        else:
            query += " ORDER BY updated DESC, session_id"
        query += " LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._db.execute(query, tuple(params)).fetchall()
        out: list[dict] = []
        for row in rows:
            record = _session_from_row(row)
            item = record.to_dict()
            item["parent_present"] = (
                True if not record.parent_session_id else self.session(record.parent_session_id)
                is not None
            )
            item["lineage_chain"] = [
                entry["session_id"] for entry in self._lineage_locked(record.session_id)
            ]
            out.append(item)
        return out

    def label_session(self, session_id: str, labels: Iterable[str]) -> Session:
        record = self.require_session(session_id)
        values = [str(item) for item in labels]
        with self.transaction() as db:
            db.execute(
                "UPDATE sessions SET labels = ?, updated = updated WHERE session_id = ?",
                (_json(values), record.session_id),
            )
        return self.require_session(record.session_id)

    def retitle(self, session_id: str, title: str, *, at: float | None = None) -> Session:
        """Set a title. The session's identity fields stay frozen; a title may change."""
        moment = _now(at)
        record = self.require_session(session_id)
        with self.transaction() as db:
            db.execute(
                "UPDATE sessions SET title = ? WHERE session_id = ?",
                (str(title or ""), record.session_id),
            )
            self._record_locked(
                db, record.session_id, "title", detail=f"title={title!r}", at=moment
            )
        return self.require_session(record.session_id)

    def archive_session(
        self, session_id: str, *, reason: str = "", at: float | None = None, archived: bool = True
    ) -> Session:
        """Archive/unarchive: nothing is deleted, the session leaves the default list."""
        moment = _now(at)
        record = self.require_session(session_id)
        with self.transaction() as db:
            db.execute(
                "UPDATE sessions SET archived = ?, archived_at = ? WHERE session_id = ?",
                (int(bool(archived)), moment if archived else 0.0, record.session_id),
            )
            self._record_locked(
                db,
                record.session_id,
                "archived" if archived else "unarchived",
                detail=str(reason),
                at=moment,
            )
        return self.require_session(record.session_id)

    # -- messages ----------------------------------------------------------- #

    def _last_message_locked(self, db: sqlite3.Connection, session_id: str) -> Message | None:
        row = db.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY seq DESC LIMIT 1",
            (str(session_id),),
        ).fetchone()
        return _message_from_row(row) if row else None

    def _write_message_locked(
        self,
        db: sqlite3.Connection,
        session: Session,
        *,
        role: str,
        content: str,
        at: float,
        kind: str,
        refs: Sequence[str],
        meta: Mapping[str, object],
        message_id: str = "",
        origin_message_id: str = "",
    ) -> Message:
        """Insert one message and move the session head — the single write path.

        The alternation check runs **before** the insert so the caller gets an error that
        names the offending role, sequence and message; the trigger behind it is the
        backstop for a write that never came through here.
        """
        last = self._last_message_locked(db, session.session_id)
        if last is not None and last.role == role:
            raise RoleAlternationError(
                f"two {role} messages in a row: message {last.seq} of "
                f"{session.session_id} is already {last.role} ({last.message_id}) — the "
                f"next message must be one of "
                f"{', '.join(item for item in ROLES if item != role)}"
            )
        seq = int(last.seq) + 1 if last else FIRST_SEQ
        identifier = str(message_id or "") or new_message_id(session.session_id, seq)
        body = str(content or "")
        estimate = estimate_tokens(body)
        digest = content_hash(body)
        prev_id = last.message_id if last else ""
        try:
            db.execute(
                """
                INSERT INTO messages(message_id, session_id, seq, role, content,
                    content_sha256, created, token_estimate, token_method, prev_message_id,
                    origin_message_id, kind, refs, meta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    session.session_id,
                    seq,
                    role,
                    body,
                    digest,
                    at,
                    estimate,
                    TOKEN_METHOD,
                    prev_id,
                    str(origin_message_id or ""),
                    str(kind or "chat"),
                    _json([str(item) for item in refs]),
                    _json(dict(meta or {})),
                ),
            )
        except sqlite3.IntegrityError as exc:
            text = str(exc)
            if "messages.message_id" in text or "UNIQUE constraint failed: messages" in text:
                raise DuplicateMessage(
                    f"message id {identifier!r} already exists — ids are never reused"
                ) from exc
            if "messages.session_id, messages.seq" in text:
                raise SequenceError(
                    f"sequence {seq} already exists in {session.session_id}"
                ) from exc
            raise SessionError(f"the message could not be written: {text}") from exc
        message = Message(
            message_id=identifier,
            session_id=session.session_id,
            seq=seq,
            role=role,
            content=body,
            content_sha256=digest,
            created=at,
            token_estimate=estimate,
            token_method=TOKEN_METHOD,
            prev_message_id=prev_id,
            origin_message_id=str(origin_message_id or ""),
            kind=str(kind or "chat"),
            refs=[str(item) for item in refs],
            meta=dict(meta or {}),
        )
        rows = self._messages_locked(db, session.session_id)
        self._update_head_locked(db, session, message, rows, at=at)
        self._index_locked(db, message)
        self._record_locked(
            db,
            session.session_id,
            "append",
            seq=seq,
            message_id=identifier,
            role=role,
            detail=f"kind={message.kind} tokens≈{estimate}",
            at=at,
        )
        return message

    def append(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        kind: str = "chat",
        refs: Iterable[str] = (),
        meta: Mapping[str, object] | None = None,
        message_id: str = "",
        origin_message_id: str = "",
        at: float | None = None,
        owner: str = "",
    ) -> Message:
        """Append one message — the only way a history grows.

        Refuses, writing nothing (and recording the refusal in the audit trail):
        a role outside :data:`ROLES`, a role equal to the previous message's role
        (:class:`RoleAlternationError`), or a reused message id.

        ``origin_message_id`` is set when this message is a *copy* of an earlier one
        (a fork or a rotation) and names the message it came from — provenance travels
        with the body.
        """
        moment = _now(at)
        validated = validate_role(role)
        self.assert_writer(owner=owner)
        session = self.require_session(session_id)
        if session.archived:
            raise SessionError(
                f"session {session.session_id} is archived — unarchive it before appending "
                "(an archived history is read-only)"
            )
        try:
            with self.transaction() as db:
                message = self._write_message_locked(
                    db,
                    session,
                    role=validated,
                    content=content,
                    at=moment,
                    kind=kind,
                    refs=tuple(refs or ()),
                    meta=meta or {},
                    message_id=message_id,
                    origin_message_id=origin_message_id,
                )
        except RoleAlternationError as exc:
            with self.transaction() as db:
                self._record_locked(
                    db,
                    session.session_id,
                    "refused_alternation",
                    role=validated,
                    detail=str(exc),
                    at=moment,
                )
            raise
        return message

    def append_turn(
        self,
        session_id: str,
        user_text: str,
        assistant_text: str,
        *,
        at: float | None = None,
        owner: str = "",
    ) -> list[Message]:
        """Append a user→assistant turn in one transaction (both or neither)."""
        moment = _now(at)
        self.assert_writer(owner=owner)
        session = self.require_session(session_id)
        with self.transaction() as db:
            first = self._write_message_locked(
                db, session, role=USER, content=user_text, at=moment, kind="chat",
                refs=(), meta={},
            )
            session = self.require_session(session.session_id)
            second = self._write_message_locked(
                db, session, role=ASSISTANT, content=assistant_text, at=moment + 1e-6,
                kind="chat", refs=(), meta={},
            )
        return [first, second]

    def _index_locked(self, db: sqlite3.Connection, message: Message) -> None:
        if not self.fts:
            return
        db.execute(
            """
            INSERT INTO messages_fts(rowid, content, message_id, session_id, role, seq)
            SELECT m.rowid, m.content, m.message_id, m.session_id, m.role, m.seq
            FROM messages m WHERE m.message_id = ?
            """,
            (message.message_id,),
        )

    def _messages_locked(
        self, db: sqlite3.Connection, session_id: str
    ) -> list[Message]:
        rows = db.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY seq", (str(session_id),)
        ).fetchall()
        return [_message_from_row(row) for row in rows]

    def _update_head_locked(
        self,
        db: sqlite3.Connection,
        session: Session,
        message: Message,
        rows: Sequence[Message],
        *,
        at: float,
    ) -> None:
        """Move the session head/counters and re-record the history hash."""
        db.execute(
            """
            UPDATE sessions SET updated = ?, message_count = ?, last_seq = ?,
                head_message_id = ?, head_role = ?, token_total = ?, history_sha256 = ?
            WHERE session_id = ?
            """,
            (
                at,
                len(rows),
                int(message.seq),
                message.message_id,
                message.role,
                sum(int(item.token_estimate) for item in rows),
                history_hash([item.to_dict() for item in rows]),
                session.session_id,
            ),
        )

    def messages(
        self,
        session_id: str,
        *,
        limit: int = DEFAULT_MESSAGE_LIMIT,
        after_seq: int = 0,
        tail: bool = False,
        before_seq: int | None = None,
    ) -> list[Message]:
        """A **bounded** window of the history: forward by default, the tail on request.

        ``limit`` is clamped to :data:`MAX_MESSAGE_LIMIT`; a caller that wants more pages
        with ``after_seq`` (forward) or ``before_seq`` (backward) instead of one unbounded
        read.
        """
        bound = max(1, min(int(limit), MAX_MESSAGE_LIMIT))
        query = "SELECT * FROM messages WHERE session_id = ?"
        params: list = [str(session_id)]
        if after_seq:
            query += " AND seq > ?"
            params.append(int(after_seq))
        if before_seq is not None:
            query += " AND seq < ?"
            params.append(int(before_seq))
        query += " ORDER BY seq DESC LIMIT ?" if tail else " ORDER BY seq LIMIT ?"
        params.append(bound)
        with self._lock:
            rows = self._db.execute(query, tuple(params)).fetchall()
        ordered = [_message_from_row(row) for row in rows]
        if tail:
            ordered.reverse()
        return ordered

    def message(self, message_id: str) -> Message | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM messages WHERE message_id = ?", (str(message_id),)
            ).fetchone()
        return _message_from_row(row) if row else None

    # -- verification / resume ---------------------------------------------- #

    def verify_history(self, session_id: str, *, limit: int = MAX_MESSAGE_LIMIT) -> dict:
        """Recompute the history and compare it with what the session recorded.

        Two independent checks, because one of them can be fooled alone:

        * **body** — every ``messages.content_sha256`` is recomputed from the stored body.
          This still catches an edited message even if someone refreshed the session's
          history hash afterwards.
        * **history** — :func:`history_hash` over the rows in ``seq`` order against the
          session's stored ``history_sha256``. This catches an insertion, a removal or a
          reordering.
        """
        session = self.require_session(session_id)
        rows = self.messages(session.session_id, limit=limit)
        bodies = {item.message_id: content_hash(item.content) for item in rows}
        body_mismatch = [
            item.seq for item in rows if str(item.content_sha256) != bodies[item.message_id]
        ]
        recomputed = history_hash([item.to_dict() for item in rows])
        stored = str(session.history_sha256 or "")
        truncated = len(rows) < int(session.message_count)
        return {
            "session_id": session.session_id,
            "message_count": len(rows),
            "recorded_message_count": int(session.message_count),
            "stored_sha256": stored,
            "recomputed_sha256": recomputed,
            "matches": bool(stored) and stored == recomputed and not body_mismatch,
            "body_mismatch_seqs": body_mismatch,
            "bodies_ok": not body_mismatch,
            "truncated_read": truncated,
            "read_limit": max(1, min(int(limit), MAX_MESSAGE_LIMIT)),
            "hash_algorithm": "sha256 over the canonical lines of the messages in seq order",
        }

    def resume(
        self,
        session_id: str,
        *,
        limit: int = DEFAULT_MESSAGE_LIMIT,
        verify: bool = True,
    ) -> dict:
        """Reopen a session: the exact prior history, its hash, and the next legal role.

        ``verify`` runs :meth:`verify_history` over **the whole session** (not only the
        returned window), so ``history.matches`` is about the history, and
        ``messages``/``omitted`` describe the window this call returned.
        """
        session = self.require_session(session_id)
        rows = self.messages(session.session_id, limit=limit)
        verification = (
            self.verify_history(session.session_id)
            if verify
            else {
                "session_id": session.session_id,
                "matches": None,
                "stored_sha256": session.history_sha256,
                "recomputed_sha256": "",
                "body_mismatch_seqs": [],
                "message_count": int(session.message_count),
                "recorded_message_count": int(session.message_count),
                "bodies_ok": None,
                "truncated_read": False,
                "read_limit": 0,
                "hash_algorithm": "not run",
            }
        )
        omitted = max(0, int(session.message_count) - len(rows))
        return {
            "session": session.to_dict(),
            "messages": [item.to_dict() for item in rows],
            "window": {
                "returned": len(rows),
                "omitted": omitted,
                "limit": max(1, min(int(limit), MAX_MESSAGE_LIMIT)),
                "complete": omitted == 0,
                "use_after_seq": rows[-1].seq if rows else 0,
            },
            "history": verification,
            "next_role": session.next_role,
            "next_seq": int(session.last_seq) + 1,
            "resumed": True,
            "continuation": {
                "role": session.next_role,
                "seq": int(session.last_seq) + 1,
                "prev_message_id": session.head_message_id,
                "command": (
                    f"skaldir session append {session.session_id} --role "
                    f"{session.next_role} --content '<text>' --json"
                ),
            },
        }

    # -- search / list ------------------------------------------------------ #

    def search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
        session_id: str = "",
        roles: Iterable[str] = (),
        include_archived: bool = True,
    ) -> dict:
        """Bounded content search across sessions, ranked (FTS5 ``bm25`` when available).

        Returns a payload, not a bare list, because the honest answer includes which
        engine answered and how many hits were dropped by the bound.
        """
        text = str(query or "").strip()
        bound = max(1, min(int(limit), MAX_SEARCH_LIMIT))
        if not text:
            return {
                "query": "",
                "engine": "fts5" if self.fts else "like",
                "hits": [],
                "returned": 0,
                "limit": bound,
                "sessions": [],
                "note": "an empty query searches nothing — pass a word or a phrase",
            }
        wanted_roles = [validate_role(item) for item in roles or ()]
        rows: list[sqlite3.Row]
        if self.fts:
            expression = _fts_query(text)
            if not expression:  # every token was too short for the index
                expression = None
            if expression is None:
                rows = self._like_search(text, bound, session_id, wanted_roles, include_archived)
            else:
                where = ["messages_fts MATCH ?"]
                params: list = [expression]
                if session_id:
                    where.append("m.session_id = ?")
                    params.append(str(session_id))
                if wanted_roles:
                    where.append(f"m.role IN ({', '.join('?' for _ in wanted_roles)})")
                    params.extend(wanted_roles)
                if not include_archived:
                    where.append("s.archived = 0")
                params.append(bound)
                with self._lock:
                    rows = self._db.execute(
                        f"""
                        SELECT m.message_id, m.session_id, m.seq, m.role, m.created,
                               m.token_estimate, s.title AS session_title, s.archived,
                               snippet(messages_fts, 0, '[', ']', '…', {SNIPPET_TOKENS})
                                   AS snippet,
                               bm25(messages_fts) AS score
                        FROM messages_fts
                        JOIN messages m ON m.message_id = messages_fts.message_id
                        JOIN sessions s ON s.session_id = m.session_id
                        WHERE {' AND '.join(where)}
                        ORDER BY score, m.session_id, m.seq
                        LIMIT ?
                        """,  # nosec B608 — where is built from constants
                        tuple(params),
                    ).fetchall()
        else:
            rows = self._like_search(text, bound, session_id, wanted_roles, include_archived)
        hits = [dict(row) for row in rows]
        return {
            "query": text,
            "engine": "fts5" if self.fts else "like",
            "indexed": self.index_consistency(),
            "hits": hits,
            "returned": len(hits),
            "limit": bound,
            "bounded": len(hits) >= bound,
            "sessions": sorted({str(hit["session_id"]) for hit in hits}),
            "note": (
                "keyword/token search (FTS5 unicode61) — not semantic: a query whose words "
                "do not appear in a body finds nothing"
            ),
        }

    def _like_search(
        self,
        query: str,
        bound: int,
        session_id: str,
        roles: Sequence[str],
        include_archived: bool,
    ) -> list[sqlite3.Row]:
        """The degraded path: a bounded ``LIKE`` scan, used when FTS5 is unavailable."""
        words = [word for word in _fts_query(query).split(" OR ") if word] or [query.lower()]
        pattern = f"%{words[0]}%"
        where = ["LOWER(m.content) LIKE ?"]
        params: list = [pattern]
        if session_id:
            where.append("m.session_id = ?")
            params.append(str(session_id))
        if roles:
            where.append(f"m.role IN ({', '.join('?' for _ in roles)})")
            params.extend(roles)
        if not include_archived:
            where.append("s.archived = 0")
        params.append(bound)
        with self._lock:
            return self._db.execute(
                f"""
                SELECT m.message_id, m.session_id, m.seq, m.role, m.created,
                       m.token_estimate, s.title AS session_title, s.archived,
                       substr(m.content, 1, 160) AS snippet, 0.0 AS score
                FROM messages m
                JOIN sessions s ON s.session_id = m.session_id
                WHERE {' AND '.join(where)}
                ORDER BY m.created DESC
                LIMIT ?
                """,  # nosec B608 — where is built from constants
                tuple(params),
            ).fetchall()

    # -- lineage operations -------------------------------------------------- #

    def fork(
        self,
        session_id: str,
        *,
        at_message_id: str = "",
        title: str = "",
        copy_prefix: bool = True,
        session_id_value: str = "",
        labels: Iterable[str] = (),
        at: float | None = None,
        owner: str = "",
    ) -> dict:
        """Branch a session: a child that records where it came from.

        With ``copy_prefix`` the parent's messages up to (and including)
        ``at_message_id`` are copied into the child — **fresh** message ids, with
        ``origin_message_id`` naming the parent message each copy came from — so the
        child starts with a complete history and alternation is preserved. Without it the
        child is empty and only the link remains.
        """
        moment = _now(at)
        parent = self.require_session(session_id)
        if at_message_id:
            point = self.message(at_message_id)
            if point is None or point.session_id != parent.session_id:
                raise UnknownSession(
                    f"no message {at_message_id!r} in {parent.session_id} — a fork point "
                    "must be a message of the session being forked"
                )
            cut = int(point.seq)
        else:
            cut = int(parent.last_seq)
        child = self.create_session(
            title or f"{parent.title or parent.session_id} (fork)",
            session_id=session_id_value,
            parent_session_id=parent.session_id,
            fork_point_message_id=str(at_message_id or parent.head_message_id or ""),
            labels=list(labels) or list(parent.labels),
            at=moment,
            owner=owner,
        )
        copied = 0
        if copy_prefix and cut > 0:
            sources = self.messages(parent.session_id, limit=cut)
            for source in sources:
                origin = source.origin_message_id or source.message_id
                self.append(
                    child.session_id,
                    source.role,
                    source.content,
                    kind=source.kind,
                    refs=source.refs,
                    meta={**source.meta, "forked_from": parent.session_id},
                    origin_message_id=origin,
                    at=moment,
                    owner=owner,
                )
                copied += 1
        with self.transaction() as db:
            self._record_locked(
                db,
                child.session_id,
                "forked",
                detail=f"parent={parent.session_id} cut={cut} copied={copied}",
                at=moment,
            )
            self._record_locked(
                db,
                parent.session_id,
                "forked_into",
                detail=f"child={child.session_id} cut={cut}",
                at=moment,
            )
        return {
            "parent": parent.to_dict(),
            "child": self.require_session(child.session_id).to_dict(),
            "fork_point_message_id": str(at_message_id or parent.head_message_id or ""),
            "copied_messages": copied,
            "copy_prefix": bool(copy_prefix),
            "lineage": self.lineage(child.session_id),
        }

    def rotate(
        self,
        session_id: str,
        *,
        keep_last: int = DEFAULT_KEEP_LAST,
        title: str = "",
        at: float | None = None,
        owner: str = "",
    ) -> dict:
        """Roll the active window into a successor session without losing anything.

        The newest ``keep_last`` messages are copied into a child (fresh ids, origin
        links), the child continues the lineage, and the parent is archived — it keeps
        every message, so this is the non-destructive alternative to a truncating purge.
        """
        moment = _now(at)
        parent = self.require_session(session_id)
        keep = max(1, min(int(keep_last), MAX_MESSAGE_LIMIT))
        tail = self.messages(parent.session_id, limit=keep, tail=True)
        if not tail:
            raise SessionError(
                f"session {parent.session_id} has no messages — nothing to rotate"
            )
        if keep >= int(parent.message_count):
            return {
                "rotated": False,
                "reason": (
                    f"the session holds {parent.message_count} message(s) and keep_last is "
                    f"{keep} — a rotation would copy everything and shrink nothing"
                ),
                "parent": parent.to_dict(),
                "successor": None,
                "copied_messages": 0,
            }
        first = tail[0]
        child = self.create_session(
            title or f"{parent.title or parent.session_id} (continued)",
            parent_session_id=parent.session_id,
            fork_point_message_id=first.message_id,
            labels=list(parent.labels),
            at=moment,
            owner=owner,
        )
        for source in tail:
            self.append(
                child.session_id,
                source.role,
                source.content,
                kind=source.kind,
                refs=source.refs,
                meta={**source.meta, "rotated_from": parent.session_id},
                origin_message_id=source.origin_message_id or source.message_id,
                at=moment,
                owner=owner,
            )
        self.archive_session(
            parent.session_id,
            reason=f"rotated into {child.session_id} (kept the newest {len(tail)})",
            at=moment,
        )
        with self.transaction() as db:
            self._record_locked(
                db,
                child.session_id,
                "rotated_from",
                detail=f"parent={parent.session_id} kept={len(tail)}",
                at=moment,
            )
        return {
            "rotated": True,
            "reason": "",
            "parent": self.require_session(parent.session_id).to_dict(),
            "successor": self.require_session(child.session_id).to_dict(),
            "copied_messages": len(tail),
            "kept_from_seq": int(first.seq),
            "lineage": self.lineage(child.session_id),
            "parent_messages_kept": int(parent.message_count),
        }

    # -- purge / retention --------------------------------------------------- #

    def purge_plan(
        self,
        session_id: str,
        *,
        mode: str = "archive",
        keep_last: int = DEFAULT_KEEP_LAST,
    ) -> dict:
        """What a purge *would* do — computed without writing anything."""
        session = self.require_session(session_id)
        if mode not in (*PURGE_MODES, "archive"):
            raise SessionError(
                f"unknown purge mode {mode!r} — one of archive, {', '.join(PURGE_MODES)}"
            )
        keep = max(1, min(int(keep_last), MAX_MESSAGE_LIMIT))
        removes = 0
        keeps = int(session.message_count)
        if mode == "truncate":
            removes = max(0, int(session.message_count) - keep)
            keeps = int(session.message_count) - removes
        elif mode == "delete":
            removes = int(session.message_count)
            keeps = 0
        children = [
            item["session_id"] for item in self.lineage(session.session_id)["children"]
        ]
        return {
            "session_id": session.session_id,
            "title": session.title,
            "mode": mode,
            "destructive": mode in PURGE_MODES,
            "message_count": int(session.message_count),
            "messages_removed": removes,
            "messages_kept": keeps,
            "keep_last": keep if mode == "truncate" else 0,
            "writes_tombstone": mode == "delete",
            "history_sha256_before": session.history_sha256,
            "children": children,
            "policy": RETENTION_POLICY["modes"][mode],
            "applied": False,
        }

    def purge(
        self,
        session_id: str,
        *,
        mode: str = "delete",
        keep_last: int = DEFAULT_KEEP_LAST,
        reason: str = "",
        dry_run: bool = True,
        at: float | None = None,
        owner: str = "",
    ) -> dict:
        """Delete (or truncate) a session's bodies — **dry run unless ``dry_run=False``**.

        The deletion runs inside one transaction that holds the purge token for exactly
        this session id: the ``messages_no_delete`` trigger refuses every other delete,
        so a purge cannot take a neighbour's rows with it. A ``delete`` writes a tombstone
        (id, title, counts, hash, time, reason) and keeps the lineage of the children
        honest; a ``truncate`` deletes the oldest messages, keeps the newest
        ``keep_last`` and re-records the session hash over what remains.
        """
        moment = _now(at)
        self.assert_writer(owner=owner)
        plan = self.purge_plan(session_id, mode=mode, keep_last=keep_last)
        if dry_run:
            return {**plan, "dry_run": True, "reason": str(reason)}
        session = self.require_session(session_id)
        if mode == "archive":
            self.archive_session(session.session_id, reason=reason or "purge --mode archive",
                                 at=moment)
            return {
                **plan,
                "dry_run": False,
                "reason": str(reason),
                "archived": True,
                "purged_at": moment,
            }
        keep = int(plan["messages_kept"])
        with self.transaction() as db:
            db.execute(
                "INSERT INTO session_store_meta(key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_META_PURGE_TOKEN, session.session_id),
            )
            try:
                if mode == "truncate":
                    db.execute(
                        "DELETE FROM messages WHERE session_id = ? AND seq <= ?",
                        (session.session_id, int(session.last_seq) - keep),
                    )
                else:
                    db.execute(
                        "DELETE FROM messages WHERE session_id = ?", (session.session_id,)
                    )
                if self.fts:
                    db.execute(
                        "DELETE FROM messages_fts WHERE session_id = ?", (session.session_id,)
                    )
                if mode == "delete":
                    db.execute(
                        """
                        INSERT INTO purged_sessions(session_id, title, parent_session_id,
                            message_count, last_seq, history_sha256, purged_at, mode, reason)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(session_id) DO UPDATE SET
                            purged_at = excluded.purged_at, mode = excluded.mode,
                            reason = excluded.reason
                        """,
                        (
                            session.session_id,
                            session.title,
                            session.parent_session_id,
                            int(session.message_count),
                            int(session.last_seq),
                            session.history_sha256,
                            moment,
                            mode,
                            str(reason),
                        ),
                    )
                    db.execute(
                        "DELETE FROM sessions WHERE session_id = ?", (session.session_id,)
                    )
                    remaining: list[Message] = []
                else:
                    remaining = self._messages_locked(db, session.session_id)
                    db.execute(
                        """
                        UPDATE sessions SET message_count = ?, head_message_id = ?,
                            head_role = ?, token_total = ?, history_sha256 = ?, updated = ?
                        WHERE session_id = ?
                        """,
                        (
                            len(remaining),
                            remaining[-1].message_id if remaining else "",
                            remaining[-1].role if remaining else "",
                            sum(int(item.token_estimate) for item in remaining),
                            history_hash([item.to_dict() for item in remaining]),
                            moment,
                            session.session_id,
                        ),
                    )
                self._record_locked(
                    db,
                    session.session_id,
                    "purged",
                    detail=(
                        f"mode={mode} removed={plan['messages_removed']} "
                        f"kept={plan['messages_kept']} reason={reason or '(none)'}"
                    ),
                    at=moment,
                )
            finally:
                db.execute(
                    "DELETE FROM session_store_meta WHERE key = ?", (_META_PURGE_TOKEN,)
                )
        return {
            **plan,
            "dry_run": False,
            "reason": str(reason),
            "purged_at": moment,
            "tombstone": self.tombstone(session.session_id),
            "remaining_messages": int(plan["messages_kept"]),
            "session_removed": mode == "delete",
        }

    # -- export -------------------------------------------------------------- #

    def export(
        self,
        session_id: str,
        *,
        as_format: str = "jsonl",
        limit: int = MAX_MESSAGE_LIMIT,
    ) -> dict:
        """Render a session as ``json`` / ``jsonl`` / ``markdown`` / ``text``.

        The exported document carries the header, the lineage and the message list, plus
        the hash check — an export that cannot be verified would be a copy of unknown
        provenance.
        """
        want = str(as_format or "jsonl").strip().lower()
        if want not in ("json", "jsonl", "markdown", "text"):
            raise SessionError(
                f"unknown export format {as_format!r} — one of json, jsonl, markdown, text"
            )
        session = self.require_session(session_id)
        rows = self.messages(session.session_id, limit=limit)
        verification = self.verify_history(session.session_id)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "exported_at": _now(None),
            "session": session.to_dict(),
            "lineage": self.lineage(session.session_id),
            "messages": [item.to_dict() for item in rows],
            "history": verification,
            "token_method": TOKEN_METHOD,
        }
        if want == "json":
            document = _json(payload) + "\n"
        elif want == "jsonl":
            lines = [_json({"record": "session", **session.to_dict()})]
            lines.extend(_json({"record": "message", **item.to_dict()}) for item in rows)
            lines.append(_json({"record": "history", **verification}))
            document = "\n".join(lines) + "\n"
        elif want == "markdown":
            document = self._markdown(payload, rows)
        else:
            document = self._plain_text(payload, rows)
        return {
            "session_id": session.session_id,
            "format": want,
            "messages": len(rows),
            "bytes": len(document.encode("utf-8")),
            "sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
            "history_sha256": session.history_sha256,
            "matches": verification["matches"],
            "document": document,
        }

    @staticmethod
    def _markdown(payload: Mapping[str, Any], rows: Sequence[Message]) -> str:
        session = dict(payload.get("session") or {})
        history = dict(payload.get("history") or {})
        out = [
            f"# {session.get('title') or session.get('session_id')}",
            "",
            f"- session: `{session.get('session_id')}`",
            f"- created: {float(session.get('created') or 0):.0f}",
            f"- messages: {session.get('message_count')}",
            f"- history sha256: `{session.get('history_sha256')}`",
            f"- verified: {history.get('matches')}",
            "",
        ]
        for item in rows:
            out.append(f"## {item.seq}. {item.role}")
            out.append("")
            out.append(item.content)
            out.append("")
        return "\n".join(out).rstrip("\n") + "\n"

    @staticmethod
    def _plain_text(payload: Mapping[str, Any], rows: Sequence[Message]) -> str:
        session = dict(payload.get("session") or {})
        out = [
            f"session {session.get('session_id')} — {session.get('title') or '(untitled)'}",
            f"created {float(session.get('created') or 0):.0f} · "
            f"{session.get('message_count')} message(s) · "
            f"history {str(session.get('history_sha256') or '')[:12]}",
            "",
        ]
        for item in rows:
            out.append(f"[{item.seq}] {item.role}: {item.content}")
        return "\n".join(out) + "\n"

    # -- status -------------------------------------------------------------- #

    def status(self) -> dict:
        with self._lock:
            sessions = int(self._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
            archived = int(
                self._db.execute("SELECT COUNT(*) FROM sessions WHERE archived = 1").fetchone()[0]
            )
            messages = int(self._db.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
            events = int(self._db.execute("SELECT COUNT(*) FROM message_events").fetchone()[0])
            purged = int(
                self._db.execute("SELECT COUNT(*) FROM purged_sessions").fetchone()[0]
            )
            tokens = int(
                self._db.execute(
                    "SELECT COALESCE(SUM(token_estimate), 0) FROM messages"
                ).fetchone()[0]
            )
            newest = self._db.execute(
                "SELECT MAX(updated) FROM sessions"
            ).fetchone()[0]
            oldest = self._db.execute(
                "SELECT MIN(created) FROM sessions"
            ).fetchone()[0]
        return {
            "store": self.path,
            "schema_version": self.schema_version(),
            "writer": self.writer(),
            "counts": {
                "sessions": sessions,
                "archived": archived,
                "messages": messages,
                "events": events,
                "purged_sessions": purged,
                "token_estimate_total": tokens,
            },
            "token_method": TOKEN_METHOD,
            "search": self.index_consistency(),
            "activity": {
                "oldest_session": float(oldest or 0.0),
                "newest_update": float(newest or 0.0),
            },
            "retention": RETENTION_POLICY,
            "invariants": {
                "role_alternation": "refused in code (RoleAlternationError) and by trigger",
                "immutable_messages": "UPDATE on messages aborts (trigger)",
                "append_only": "DELETE needs the per-session purge token (trigger)",
                "monotonic_sequence": "seq must be MAX(seq)+1 (trigger + UNIQUE index)",
            },
        }


def open_store(
    path: str | os.PathLike | None = None,
    *,
    runtime: str | os.PathLike | None = None,
    owner: str = "",
    create: bool = True,
) -> SessionStore:
    """Open the store at ``path`` (or ``<runtime>/sessions.db``)."""
    target = Path(path).expanduser() if path else session_store_path(runtime)
    return SessionStore(target, owner=owner, create=create)


__all__ = [
    "ASSISTANT",
    "CHAT_ROLES",
    "CorruptSessionStore",
    "DEFAULT_KEEP_LAST",
    "DEFAULT_MESSAGE_LIMIT",
    "DEFAULT_SEARCH_LIMIT",
    "DEFAULT_SESSION_LIMIT",
    "DuplicateMessage",
    "FIRST_SEQ",
    "ImmutableMessageError",
    "MAX_SEARCH_LIMIT",
    "Message",
    "PURGE_MODES",
    "RETENTION_POLICY",
    "ROLES",
    "RoleAlternationError",
    "SCHEMA_VERSION",
    "SYSTEM",
    "SequenceError",
    "Session",
    "SessionError",
    "SessionStore",
    "TOKEN_METHOD",
    "TOOL",
    "USER",
    "UnknownRole",
    "UnknownSession",
    "WriterConflict",
    "canonical_message_line",
    "content_hash",
    "estimate_tokens",
    "fts5_available",
    "history_hash",
    "new_message_id",
    "open_store",
    "session_store_path",
    "validate_role",
]
