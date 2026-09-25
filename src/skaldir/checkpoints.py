"""Unified checkpoints — one restorable point for the conversation *and* the tree (MVP/55).

The PERSIST layer already owns the pieces: the conversation lives in the message store
(:mod:`skaldir.session_store`, ``<runtime>/sessions.db``) and every file change a run made
lives in an edit transaction (:mod:`skaldir.edit_tx`, ``<runtime>/edit``). What was missing
is the thing a user actually asks for — *"put me back where I was before this run"* — one
record that ties the two together and can be restored **exactly**, or refuse to.

A checkpoint is a directory::

    <state>/                      default <runtime>/checkpoint
      blobs/ab/abcd…              content-addressed file bytes (shared by every checkpoint)
      cp-…/manifest.json          the one record: the declared file set + the message hashes
      cp-…/                      an interrupted create leaves the directory without a manifest

The four rules that make a restore trustworthy
----------------------------------------------
1. **Cheap to store.** File bytes are content-addressed and written once — a second
   checkpoint of an unchanged file adds *no* bytes. The conversation is **referenced, not
   copied**: the manifest carries one hash per message (``content_sha256``) plus the
   identity-free conversation digest, and the bodies stay in ``sessions.db`` where they
   already are. Measured cost for a 3-file / 50-message run is reported by ``create`` in
   ``stats`` (``create_ms``, ``manifest_bytes``, ``blob_bytes_written``,
   ``blob_bytes_reused``) and pinned in ``tests/test_checkpoints_cli.py``.
2. **Exact, or refused.** Every file is checked by sha256 before it is restored — a file
   that changed since the checkpoint is a **refusal** (exit 6) that names the drift; the
   conversation prefix is checked message by message and a store that no longer matches
   the recorded hashes is a refusal too (exit 2). ``--force`` restores anyway and the
   drift is *recorded in the payload*, never swallowed.
3. **The declared set is the whole world.** A restore touches exactly the paths in the
   manifest, each resolved inside the checkpoint's workspace and refused when it is
   absolute, contains ``..`` or resolves outside the tree. Nothing else is read, written
   or deleted.
4. **A kill cannot fake a checkpoint.** Blobs are written under a content address and
   re-hashed before they are ever read; the manifest is published with one atomic
   ``os.replace``, last. A process killed mid-create leaves blobs (shared, harmless) and a
   directory *without* a manifest — which ``list`` reports as ``interrupted`` and which
   cannot be restored, because there is nothing to restore from. A restore killed halfway
   leaves whole files only (every write is a temp file + ``os.replace``) and is re-runnable.

Kill injection
--------------
``SKALDIR_CHECKPOINT_CRASH=after_snapshot`` (or ``mid_restore``) makes the process die at
that point with ``os._exit(137)`` — no cleanup, no ``finally``, no flush — which is how the
durability proof in ``tests/test_checkpoints_durability.py`` places the kill deterministically
instead of hoping for a timing window. The hook is inert unless the variable names a crash
point, and it is read through :mod:`skaldir.env` like every other setting.

The resume protocol, step 0
---------------------------
``restore`` (and its ``resume`` alias) hands back the same continuation
:meth:`skaldir.session_store.SessionStore.resume` produces — ``next_role``, ``next_seq``,
the window and the history check — under ``resume``, so the long-run protocol's step 0 is
literally this call and not a re-implementation of it.

Honest limits (in every payload's ``limits``)
---------------------------------------------
* The conversation **bodies are not copied** into the checkpoint: a purged session cannot
  be restored from a checkpoint (a purge is a decision, and a snapshot must not silently
  undo it). The checkpoint refuses and says so.
* The session store is **append-only**. Messages appended after the checkpoint cannot be
  deleted; a restore returns the conversation to the checkpoint by forking a successor
  session whose prefix is byte-identical (``origin_message_id`` points back), leaving the
  original history intact.
* A file recorded as *absent* is restored by **deleting** the path if it exists now — it is
  part of the declared set, and that is the declared state.
* Restore is all-or-nothing per file, but not across files: every file is written with
  ``os.replace``; a kill between two files leaves a partly restored tree that a second
  restore finishes (the payload names what was written).
* File bytes are stored in the clear (no encryption), like every other store here.
* A blob is verified against its content address before use, so a torn write is detected —
  but that also means a restore reads every blob it writes, and the dedup path re-hashes an
  existing blob.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import stat
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import env
from .session_store import UnknownSession, content_hash, session_store_path

# --------------------------------------------------------------------------- #
# vocabulary                                                                    #
# --------------------------------------------------------------------------- #

SCHEMA_VERSION = "1.0"

#: Directory name under the runtime state dir that holds checkpoints + blobs.
CHECKPOINT_DIRNAME = "checkpoint"

#: Where the content-addressed file bytes live (shared by every checkpoint).
BLOB_DIRNAME = "blobs"

#: The one file that makes a checkpoint a checkpoint.
MANIFEST_NAME = "manifest.json"

#: ``SKALDIR_CHECKPOINT_CRASH`` — the kill-injection hook (see the module note).
CRASH_ENV = "CHECKPOINT_CRASH"
CRASH_AFTER_SNAPSHOT = "after_snapshot"
CRASH_MID_RESTORE = "mid_restore"
CRASH_POINTS: tuple[str, ...] = (CRASH_AFTER_SNAPSHOT, CRASH_MID_RESTORE)

#: What the injected kill exits with (128 + SIGKILL, like a real ``kill -9``).
KILL_EXIT = 137

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_VERIFICATION_FAILED = 2
EXIT_BLOCKED = 3
EXIT_NOT_FOUND = 5
EXIT_CONFLICT = 6
EXIT_INTERNAL = 7

#: The unit :func:`conversation_hash` hashes — named in every payload.
CONVERSATION_HASH_ALGORITHM = (
    "sha256 over the canonical JSON lines of (seq, role, kind, content_sha256) in seq order "
    "— identity-free, so a restore into a fresh session reproduces the same digest"
)

# --- file states ------------------------------------------------------------- #

FILE_IDENTICAL = "identical"
FILE_CHANGED = "changed"
FILE_MISSING = "missing"
FILE_UNEXPECTED = "unexpected_present"

ACTION_SKIP = "skip"
ACTION_WRITE = "write"
ACTION_DELETE = "delete"

# --- conversation states ----------------------------------------------------- #

CONV_NONE = "none"
CONV_IN_PLACE = "in_place"
CONV_DRIFTED = "drifted"
CONV_FORK = "fork"
CONV_GONE = "gone"
CONV_BROKEN = "broken"

# --- verdicts ---------------------------------------------------------------- #

VERDICT_CREATED = "created"
VERDICT_LIST = "list"
VERDICT_PREVIEW = "preview"
VERDICT_CONFIRMATION_REQUIRED = "confirmation_required"
VERDICT_RESTORED = "restored"
VERDICT_RESTORED_PARTIAL = "restored_partial"
VERDICT_REFUSED_DRIFT = "refused_drift"
VERDICT_BLOCKED = "blocked"
VERDICT_NOT_FOUND = "not_found"
VERDICT_CORRUPT = "corrupt"
#: ``prune``: the dry run's plan, and the run that really removed blobs.
VERDICT_PRUNE_PLAN = "prune_plan"
VERDICT_PRUNED = "pruned"

#: Refusals ``--force`` may override, and the ones it never can.
FORCEABLE = ("drift", "workspace_mismatch", "conversation_gone")
UNFORCEABLE = ("conversation_broken", "blob_mismatch", "cap_exceeded", "missing_workspace")

#: The honest limits, repeated in every payload (module note §limits).
LIMITS: tuple[str, ...] = (
    "conversation bodies are referenced, not copied: a purged session cannot be restored",
    "the session store is append-only — a drifted conversation is restored by forking a "
    "byte-identical prefix, never by deleting the newer messages",
    "a file recorded as absent is restored by deleting the path if it exists",
    "files are written one by one with os.replace: a kill between two files leaves a partly "
    "restored tree, and a second restore finishes it",
    "finishing an interrupted restore needs --force: the half-restored files no longer match "
    "the checkpoint, which is indistinguishable from real drift",
    "the blob store is not encrypted",
    "orphan blobs are reclaimed by `checkpoint prune`, which is bounded and a dry run by "
    "default; it reads every readable manifest and refuses the sweep on one it cannot read",
    "every blob is re-hashed against its content address before use",
)


# --------------------------------------------------------------------------- #
# caps                                                                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Caps:
    """The bounds one checkpoint will not cross. Frozen: a cap is not a suggestion."""

    max_files: int = 256
    max_messages: int = 5000
    max_file_bytes: int = 8 * 1024 * 1024
    max_checkpoint_bytes: int = 64 * 1024 * 1024
    max_transactions: int = 32
    max_diff_bytes: int = 60_000
    keep_checkpoints: int = 64
    #: How many orphan blobs one ``prune`` may remove. A garbage collector that is
    #: allowed to sweep the whole store in one call is a delete-all waiting to happen.
    prune_max_blobs: int = 512

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Caps:
        """Defaults, each replaced by its environment override when one is set."""
        values: dict[str, Any] = {}
        for name, row in _CAP_ENV.items():
            raw = env.getenv(row[0], "", environ=environ)
            if raw in (None, ""):
                continue
            try:
                values[name] = row[1](str(raw).strip())
            except ValueError:
                continue  # a malformed override is ignored, never silently zeroed
        return cls(**values)

    def as_dict(self) -> dict[str, dict[str, Any]]:
        """One row per cap: value, unit, the environment name that overrides it."""
        return {
            name: {
                "value": getattr(self, name),
                "unit": row[2],
                "env": env.primary_name(row[0]),
                "what": row[3],
            }
            for name, row in _CAP_ENV.items()
        }


#: field -> (env token, caster, unit, what it bounds). The token is unprefixed on
#: purpose: ``skaldir.env`` owns the two prefixes (see its module note).
_CAP_ENV: dict[str, tuple[str, Any, str, str]] = {
    "max_files": ("CHECKPOINT_MAX_FILES", int, "count", "files in one declared set"),
    "max_messages": ("CHECKPOINT_MAX_MESSAGES", int, "count", "messages of one conversation"),
    "max_file_bytes": ("CHECKPOINT_MAX_FILE_BYTES", int, "bytes", "one snapshotted file"),
    "max_checkpoint_bytes": (
        "CHECKPOINT_MAX_BYTES", int, "bytes", "all captured bytes of one checkpoint",
    ),
    "max_transactions": (
        "CHECKPOINT_MAX_TRANSACTIONS", int, "count", "edit transactions folded in",
    ),
    "max_diff_bytes": ("CHECKPOINT_MAX_DIFF_BYTES", int, "bytes", "one file's preview diff"),
    "keep_checkpoints": ("CHECKPOINT_KEEP", int, "count", "checkpoints kept by `list`"),
    "prune_max_blobs": (
        "CHECKPOINT_PRUNE_MAX_BLOBS", int, "count", "orphan blobs one `prune` may remove",
    ),
}


# --------------------------------------------------------------------------- #
# hashing                                                                       #
# --------------------------------------------------------------------------- #


def sha256_bytes(data: bytes) -> str:
    """``sha256`` of the exact bytes — the one hash for every file and blob."""
    return hashlib.sha256(bytes(data)).hexdigest()


def canonical_conversation_line(message: Mapping[str, Any]) -> str:
    """One message as the identity-free line :func:`conversation_hash` hashes.

    ``seq``, the role, the kind and the body's sha256 — deliberately *not* the message id,
    the timestamp or the token estimate. Those change when a prefix is copied into a
    successor session (a fork), and a digest that moved on a byte-identical restore would
    be measuring the store's bookkeeping instead of the conversation.
    """
    return json.dumps(
        {
            "seq": int(message.get("seq") or 0),
            "role": str(message.get("role") or ""),
            "kind": str(message.get("kind") or ""),
            "content_sha256": str(message.get("content_sha256") or ""),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def conversation_hash(messages: Iterable[Mapping[str, Any]]) -> str:
    """The identity-free conversation digest over ``messages``, in the order given."""
    digest = hashlib.sha256()
    for message in messages:
        digest.update(canonical_conversation_line(message).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def message_rows(messages: Iterable[Any]) -> list[dict[str, Any]]:
    """The manifest's per-message record: identity + body hash, never the body."""
    rows: list[dict[str, Any]] = []
    for item in messages:
        body = str(getattr(item, "content", "") or "")
        rows.append(
            {
                "seq": int(getattr(item, "seq", 0) or 0),
                "message_id": str(getattr(item, "message_id", "") or ""),
                "role": str(getattr(item, "role", "") or ""),
                "kind": str(getattr(item, "kind", "") or ""),
                "content_sha256": content_hash(body),
                "bytes": len(body.encode("utf-8")),
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# paths, ids, atomic writes                                                     #
# --------------------------------------------------------------------------- #


def state_root(state_dir: str | os.PathLike) -> Path:
    """The checkpoint state directory (``<state>/checkpoint`` by the CLI's default)."""
    return Path(str(state_dir)).expanduser()


def blob_path(state_dir: str | os.PathLike, sha: str) -> Path:
    """Where the bytes with this address live — one level of fan-out, no collisions."""
    text = str(sha or "")
    return state_root(state_dir) / BLOB_DIRNAME / text[:2] / text


def new_checkpoint_id(stamp: float | None = None) -> str:
    """``cp-20260918-204412-123456-a1b2c3`` — sorts by time, unique by suffix."""
    moment = float(stamp if stamp is not None else time.time())
    whole = time.strftime("%Y%m%d-%H%M%S", time.localtime(moment))
    micros = int(round((moment - int(moment)) * 1_000_000)) % 1_000_000
    return f"cp-{whole}-{micros:06d}-{uuid.uuid4().hex[:6]}"


def _atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    """Write ``data`` through a temp file in the target's directory + ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if mode is not None:
        os.chmod(temporary, stat.S_IMODE(mode))
    os.replace(temporary, path)


def _drop_checkpoint_dir(path: Path) -> None:
    """Remove the marker directory of a create that refused — but never a published one.

    Only an *empty* directory is removed: a manifest in it means the checkpoint exists and
    a refusal must not take it with it.
    """
    try:
        if path.is_dir() and not (path / MANIFEST_NAME).exists() and not any(path.iterdir()):
            path.rmdir()
    except OSError:  # pragma: no cover — a racing rmdir
        return


def _crash_point(name: str) -> None:
    """Die here, hard, when ``SKALDIR_CHECKPOINT_CRASH`` names this point (module note)."""
    wanted = str(env.getenv(CRASH_ENV, "") or "").strip()
    if wanted and wanted == str(name):
        os._exit(KILL_EXIT)


def _truncate(text: str, cap: int) -> tuple[str, bool]:
    """Cap a rendered diff; the marker says how much was dropped (never silent)."""
    body = str(text or "")
    limit = max(1, int(cap))
    raw = body.encode("utf-8")
    if len(raw) <= limit:
        return body, False
    kept = raw[:limit].decode("utf-8", "ignore")
    return f"{kept}\n…[diff truncated: {len(raw) - limit} bytes dropped]\n", True


def _diff(before: bytes, after: bytes, path: str, *, context: int = 3) -> str:
    """A unified diff between what is on disk and what the checkpoint holds."""
    return "".join(
        difflib.unified_diff(
            before.decode("utf-8", "replace").splitlines(True),
            after.decode("utf-8", "replace").splitlines(True),
            fromfile=f"current/{path}",
            tofile=f"checkpoint/{path}",
            n=max(0, int(context)),
        )
    )


def safe_path(workspace: Path, relative: str) -> tuple[Path | None, str]:
    """Resolve one declared relative path inside ``workspace``, or refuse it.

    The refusal is the whole point of this function: an absolute path, a ``..`` segment or
    a symlink that resolves out of the tree is exactly how a restore would touch a file the
    checkpoint never declared.
    """
    text = str(relative or "").strip()
    if not text:
        return None, "an empty path"
    candidate = Path(text)
    if candidate.is_absolute():
        return None, f"{text}: an absolute path is never a checkpoint member"
    if any(part == ".." for part in candidate.parts):
        return None, f"{text}: a '..' segment would leave the workspace"
    try:
        root = workspace.resolve()
        inside = (workspace / candidate).resolve()
    except OSError as exc:  # pragma: no cover — a broken mount
        return None, f"{text}: cannot be resolved ({exc})"
    if inside != root and root not in inside.parents:
        return None, f"{text}: resolves outside the workspace ({root})"
    return workspace / candidate, ""


# --------------------------------------------------------------------------- #
# the blob store                                                                #
# --------------------------------------------------------------------------- #


def store_blob(state_dir: str | os.PathLike, data: bytes) -> dict[str, Any]:
    """Put ``data`` at its content address; a blob that is already there is not re-written.

    The dedup path re-hashes the existing bytes: a blob whose content does not match its
    own name is **replaced** (content addressing makes that repair safe), so the store can
    never hand a restore bytes that are not what its address promises.
    """
    raw = bytes(data)
    digest = sha256_bytes(raw)
    target = blob_path(state_dir, digest)
    row = {"sha256": digest, "bytes": len(raw), "path": str(target)}
    if target.is_file():
        try:
            existing = target.read_bytes()
        except OSError:
            existing = b""
        if sha256_bytes(existing) == digest:
            row.update({"written": False, "reused": True, "repaired": False})
            return row
        row.update({"written": True, "reused": False, "repaired": True})
        _atomic_write(target, raw)
        return row
    row.update({"written": True, "reused": False, "repaired": False})
    _atomic_write(target, raw)
    return row


def read_blob(state_dir: str | os.PathLike, sha: str) -> tuple[bytes | None, str]:
    """Read a blob **and verify it against its address** before anyone sees the bytes."""
    text = str(sha or "")
    if not text:
        return None, "no content address was recorded"
    target = blob_path(state_dir, text)
    if not target.is_file():
        return None, f"the blob {text[:12]} is missing from the store"
    try:
        raw = target.read_bytes()
    except OSError as exc:
        return None, f"the blob {text[:12]} cannot be read: {exc}"
    actual = sha256_bytes(raw)
    if actual != text:
        return None, (
            f"the blob {text[:12]} holds different bytes (sha256 {actual[:12]}) — the "
            "content address and the content disagree"
        )
    return raw, ""


def _blob_totals(state_dir: str | os.PathLike, *, cap: int = 50_000) -> dict[str, int]:
    """The store's size, measured by walking it (bounded) — never estimated."""
    files = 0
    total = 0
    for path in sorted(state_root(state_dir).rglob("*")):
        if files >= cap:
            break
        try:
            if path.is_file():
                files += 1
                total += path.stat().st_size
        except OSError:  # pragma: no cover — a file that vanished under us
            continue
    return {"files": files, "bytes": total}


# --------------------------------------------------------------------------- #
# reading the two sources of a declared set                                     #
# --------------------------------------------------------------------------- #


def collect_file_set(
    edit_state_dir: str | os.PathLike | None,
    *,
    transactions: Iterable[str] = (),
    include_applied: bool = True,
    declared: Iterable[str] = (),
    caps: Caps | None = None,
) -> dict[str, Any]:
    """The paths a checkpoint declares, from edit transactions plus explicit paths.

    A transaction contributes the files it actually touched (``ok`` records). By default
    every transaction still *in the tree* is folded in, newest first, up to
    ``max_transactions``; ``transactions`` names them explicitly (``"last"`` means newest).
    Explicit ``declared`` paths are added with no transaction behind them.
    """
    from . import edit_tx  # local import: the edit engine is not needed by a pure restore

    caps = caps or Caps.from_env()
    wanted = [str(item) for item in transactions if str(item or "").strip()]
    rows: list[dict[str, Any]] = []
    paths: dict[str, str] = {}
    workspace = ""
    source = "none"
    root = Path(str(edit_state_dir)).expanduser() if edit_state_dir else None
    picked: list[str] = []
    if root is not None and root.is_dir():
        if wanted:
            picked = [edit_tx.resolve_tx_id(root, item) for item in wanted]
            source = "edit:explicit"
        else:
            summaries = [item for item in edit_tx.list_transactions(root) if item.get("in_tree")]
            if not include_applied:
                summaries = []
            summaries = summaries[: int(caps.max_transactions)]
            picked = [str(item.get("tx_id") or "") for item in summaries]
            source = "edit:in_tree" if picked else "none"
        for tx_id in picked:
            record = edit_tx.load_transaction(root, tx_id)
            if record is None:
                rows.append({"tx_id": tx_id, "found": False, "paths": []})
                continue
            touched, skipped = [], []
            for item in record.get("files", []):
                path = str(item.get("path") or "")
                if item.get("ok") and path:
                    touched.append(path)
                    paths.setdefault(path, tx_id)
                    if not workspace:
                        workspace = str(record.get("workspace") or "")
                elif path:
                    skipped.append({"path": path, "reason": str(item.get("reason") or "")})
            rows.append(
                {
                    "tx_id": tx_id,
                    "found": True,
                    "created_at": str(record.get("created_at") or ""),
                    "verdict": str(record.get("verdict") or ""),
                    "in_tree": bool(record.get("in_tree")),
                    "paths": touched,
                    "skipped": skipped,
                }
            )
    for item in declared:
        text = str(item or "").strip()
        if text:
            paths.setdefault(text, "")
    if not workspace:
        workspace = str(Path.cwd())
    return {
        "source": source,
        "transactions": rows,
        "paths": dict(sorted(paths.items())),
        "explicit": [str(item).strip() for item in declared if str(item or "").strip()],
        "workspace": workspace,
    }


def conversation_block(
    store: Any, session_id: str, *, caps: Caps | None = None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The conversation half of a manifest: per-message hashes, no bodies.

    Returns ``(block, problem)`` — a problem is a refusal, never a partial block.
    """
    caps = caps or Caps.from_env()
    if store is None or not session_id:
        return None, None
    try:
        session = store.require_session(session_id)
    except UnknownSession as exc:
        return None, {"reason": "unknown_session", "message": str(exc)}
    rows = store.messages(session.session_id, limit=int(caps.max_messages))
    if len(rows) < int(session.message_count):
        return None, {
            "reason": "cap_exceeded",
            "message": (
                f"the session holds {session.message_count} messages — above the "
                f"{caps.max_messages}-message checkpoint cap; nothing was captured"
            ),
        }
    messages = message_rows(rows)
    verification = store.verify_history(session.session_id)
    body_bytes = sum(int(item.get("bytes") or 0) for item in messages)
    return (
        {
            "session_id": session.session_id,
            "title": session.title,
            "message_count": len(messages),
            "last_seq": int(session.last_seq),
            "head_message_id": session.head_message_id,
            "head_role": session.head_role,
            "next_role": session.next_role,
            "archived": bool(session.archived),
            "history_sha256": str(session.history_sha256 or ""),
            "history_matches_at_snapshot": bool(verification.get("matches")),
            "conversation_sha256": conversation_hash(messages),
            "algorithm": CONVERSATION_HASH_ALGORITHM,
            "body_bytes_referenced": body_bytes,
            "bodies_stored": False,
            "store": str(getattr(store, "path", "")),
            "messages": messages,
        },
        None,
    )


# --------------------------------------------------------------------------- #
# create                                                                        #
# --------------------------------------------------------------------------- #


def open_session_store(
    path: str | os.PathLike | None = None,
    *,
    runtime: str | os.PathLike | None = None,
    create: bool = False,
) -> Any:
    """Open the session store at ``path`` (or ``<runtime>/sessions.db``), or ``None``.

    ``None`` means *there is no store here* — never an empty one. A checkpoint that
    silently opened a fresh database would report a conversation it never saw.
    """
    target = Path(str(path)).expanduser() if path else session_store_path(runtime)
    if not target.is_file():
        return None
    from .session_store import SessionStore

    return SessionStore(target, create=create)


def _refusal(
    kind: str, message: str, *, verdict: str = VERDICT_BLOCKED, **detail: Any
) -> dict[str, Any]:
    """The one shape a refusal has (the CLI turns ``kind`` into an exit code)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "verdict": verdict,
        "error": {"kind": kind, "message": message, "detail": detail},
        "limits": list(LIMITS),
    }


def create_checkpoint(
    *,
    workspace: str | os.PathLike,
    state_dir: str | os.PathLike,
    session_id: str = "",
    runtime: str | os.PathLike | None = None,
    session_store: str | os.PathLike | None = None,
    edit_state_dir: str | os.PathLike | None = None,
    transactions: Iterable[str] = (),
    include_applied: bool = True,
    files: Iterable[str] = (),
    label: str = "",
    owner: str = "skaldir.checkpoints",
    caps: Caps | None = None,
    now: float | None = None,
    store: Any = None,
) -> dict[str, Any]:
    """Snapshot the declared file set and the conversation into one checkpoint.

    Cheap by construction: file bytes are content-addressed (an unchanged file adds
    nothing), the conversation is referenced by hash, and the manifest is one small JSON
    document published atomically, last.
    """
    started = time.time()
    caps = caps or Caps.from_env()
    root = Path(str(workspace)).expanduser()
    try:
        root = root.resolve()
    except OSError as exc:  # pragma: no cover — a broken mount
        return _refusal("blocked", f"workspace: {exc}")
    if not root.is_dir():
        return _refusal("blocked", f"the workspace does not exist: {root}")

    file_set = collect_file_set(
        edit_state_dir,
        transactions=transactions,
        include_applied=include_applied,
        declared=files,
        caps=caps,
    )
    if not str(label or "").strip() and file_set["transactions"]:
        label = f"{len(file_set['transactions'])} transaction(s)"
    if len(file_set["paths"]) > int(caps.max_files):
        return _refusal(
            "cap_exceeded",
            f"the declared set holds {len(file_set['paths'])} paths — above the "
            f"{caps.max_files}-file checkpoint cap",
            cap=caps.max_files,
            paths=sorted(file_set["paths"])[:10],
        )

    conversation: dict[str, Any] | None = None
    conversation_problem: dict[str, Any] | None = None
    resolved_session = str(session_id or "").strip()
    if resolved_session:
        opened = False
        if store is None:
            store = open_session_store(session_store, runtime=runtime)
            opened = store is not None
        if store is None:
            conversation_problem = {
                "reason": "no_session_store",
                "message": (
                    "no session store was found: "
                    f"{session_store or session_store_path(runtime)} does not exist"
                ),
            }
        else:
            try:
                if resolved_session == "last":
                    newest = store.sessions(limit=1)
                    resolved_session = str(newest[0]["session_id"]) if newest else ""
                    if not resolved_session:
                        conversation_problem = {
                            "reason": "unknown_session",
                            "message": "the session store holds no session to snapshot",
                        }
                if resolved_session:
                    conversation, conversation_problem = conversation_block(
                        store, resolved_session, caps=caps
                    )
            finally:
                if opened:
                    store.close()
    if conversation_problem and conversation_problem.get("reason") == "cap_exceeded":
        return _refusal("cap_exceeded", str(conversation_problem["message"]))

    state = state_root(state_dir)
    checkpoint_id = new_checkpoint_id(now)
    # The directory exists before the first blob is written, so a process killed in the
    # middle of a create leaves a marker a reader can see (`list` reports it as
    # interrupted). It stays empty until the manifest is published, last.
    checkpoint_dir = state / checkpoint_id
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    written = 0
    reused = 0
    captured = 0
    problems: list[dict[str, Any]] = []
    for relative, source_tx in file_set["paths"].items():
        target, problem = safe_path(root, relative)
        if target is None:
            problems.append({"path": relative, "reason": "refused", "message": problem})
            continue
        record: dict[str, Any] = {
            "path": relative,
            "source_tx": source_tx,
            "declared_by": "transaction" if source_tx else "cli",
            "exists": target.is_file(),
            "sha256": "",
            "bytes": 0,
            "mode": 0,
        }
        if not record["exists"]:
            entries.append(record)
            continue
        try:
            size = target.stat().st_size
        except OSError as exc:
            problems.append({"path": relative, "reason": "unreadable", "message": str(exc)})
            continue
        if size > int(caps.max_file_bytes):
            problems.append(
                {
                    "path": relative,
                    "reason": "cap_exceeded",
                    "message": (
                        f"{size} bytes — above the {caps.max_file_bytes}-byte per-file cap"
                    ),
                }
            )
            continue
        if captured + size > int(caps.max_checkpoint_bytes):
            _drop_checkpoint_dir(checkpoint_dir)
            return _refusal(
                "cap_exceeded",
                f"the declared set exceeds the {caps.max_checkpoint_bytes}-byte checkpoint "
                "cap; nothing was captured",
                captured_bytes=captured,
                next_file=relative,
            )
        try:
            raw = target.read_bytes()
        except OSError as exc:
            problems.append({"path": relative, "reason": "unreadable", "message": str(exc)})
            continue
        blob = store_blob(state, raw)
        captured += len(raw)
        written += int(blob["bytes"]) if blob["written"] else 0
        reused += int(blob["bytes"]) if blob["reused"] else 0
        record.update(
            {
                "sha256": blob["sha256"],
                "bytes": len(raw),
                "mode": stat.S_IMODE(target.stat().st_mode),
                "blob": str(blob_path(state, blob["sha256"])),
                "blob_written": bool(blob["written"]),
                "blob_repaired": bool(blob["repaired"]),
            }
        )
        entries.append(record)

    if problems:
        _drop_checkpoint_dir(checkpoint_dir)
        return _refusal(
            "blocked",
            "the declared set could not be captured; nothing was published",
            problems=problems,
        )

    # The kill-injection point: every blob is on disk, the manifest is not published yet.
    _crash_point(CRASH_AFTER_SNAPSHOT)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "verdict": VERDICT_CREATED,
        "checkpoint_id": checkpoint_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
        "created_epoch": round(started, 6),
        "label": str(label or ""),
        "workspace": str(root),
        "state_dir": str(state),
        "conversation": conversation,
        "conversation_problem": conversation_problem,
        "files": entries,
        "file_set": {
            "source": file_set["source"],
            "transactions": file_set["transactions"],
            "explicit": file_set["explicit"],
            "count": len(entries),
            "declared": sorted(file_set["paths"]),
        },
        "caps": caps.as_dict(),
        "limits": list(LIMITS),
    }
    manifest["stats"] = {
        "create_ms": int((time.time() - started) * 1000),
        "manifest_bytes": 0,
        "files": len(entries),
        "files_with_content": sum(1 for item in entries if item["exists"]),
        "messages": int((conversation or {}).get("message_count") or 0),
        "captured_bytes": captured,
        "blob_bytes_written": written,
        "blob_bytes_reused": reused,
        "conversation_bytes_referenced": int(
            (conversation or {}).get("body_bytes_referenced") or 0
        ),
    }
    manifest_path = state / checkpoint_id / MANIFEST_NAME
    manifest_bytes = _publish_manifest(manifest, manifest_path)

    totals = _blob_totals(state)
    payload = dict(manifest)
    payload["stats"] = {
        **manifest["stats"],
        "manifest_bytes": manifest_bytes,
        "store_bytes": manifest_bytes + written,
        "state_dir_bytes": totals["bytes"],
        "state_dir_files": totals["files"],
        "total_ms": int((time.time() - started) * 1000),
    }
    payload["manifest_path"] = str(manifest_path)
    payload["store_path"] = str(state)
    return payload


def _publish_manifest(manifest: dict[str, Any], path: Path) -> int:
    """Publish a manifest atomically, last — and make the size it records the real one.

    The recorded ``stats.manifest_bytes`` is the file's own size, so the document is
    re-encoded until the number and the bytes agree (bounded: only the digit count can
    change, so this converges immediately in practice), then written with one
    ``os.replace``. A reader that finds the file finds a complete document; a kill before
    this call leaves no manifest at all.
    """
    stats = manifest.setdefault("stats", {})
    encoded = b""
    size = 0
    for _ in range(6):
        encoded = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        if len(encoded) == size:
            break
        size = len(encoded)
        stats["manifest_bytes"] = size
    _atomic_write(path, encoded)
    actual = path.stat().st_size
    if actual != int(stats.get("manifest_bytes") or 0):
        stats["manifest_bytes"] = actual
        encoded = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        _atomic_write(path, encoded)
        actual = path.stat().st_size
    return actual


# --------------------------------------------------------------------------- #
# list / load / show                                                            #
# --------------------------------------------------------------------------- #


def _row(checkpoint: Mapping[str, Any], manifest_path: Path) -> dict[str, Any]:
    """One row for ``list``: what it holds, what it cost, where it lives."""
    stats = dict(checkpoint.get("stats") or {})
    conversation = checkpoint.get("conversation") or {}
    return {
        "checkpoint_id": str(checkpoint.get("checkpoint_id") or manifest_path.parent.name),
        "created_at": str(checkpoint.get("created_at") or ""),
        "label": str(checkpoint.get("label") or ""),
        "workspace": str(checkpoint.get("workspace") or ""),
        "session_id": str(conversation.get("session_id") or ""),
        "files": len(checkpoint.get("files") or []),
        "messages": int(conversation.get("message_count") or 0),
        "manifest_bytes": int(stats.get("manifest_bytes") or 0),
        "captured_bytes": int(stats.get("captured_bytes") or 0),
        "create_ms": int(stats.get("create_ms") or 0),
        "conversation_sha256": str(conversation.get("conversation_sha256") or ""),
        "manifest_path": str(manifest_path),
    }


def list_checkpoints(
    state_dir: str | os.PathLike, *, limit: int = 50, caps: Caps | None = None
) -> dict[str, Any]:
    """Every readable checkpoint (newest first) plus the interrupted creates.

    A directory without a readable manifest is reported as ``interrupted`` — that is
    exactly the shape a killed create leaves, and it is visible instead of silent.
    """
    caps = caps or Caps.from_env()
    state = state_root(state_dir)
    bound = max(1, int(limit or caps.keep_checkpoints))
    rows: list[dict[str, Any]] = []
    interrupted: list[dict[str, Any]] = []
    if state.is_dir():
        for manifest_path in sorted(state.glob(f"*/{MANIFEST_NAME}"), reverse=True):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                interrupted.append({"path": str(manifest_path), "reason": f"unreadable: {exc}"})
                continue
            if not isinstance(data, dict):
                interrupted.append({"path": str(manifest_path), "reason": "not a JSON object"})
                continue
            rows.append(_row(data, manifest_path))
        for directory in sorted(state.iterdir(), reverse=True):
            if not directory.is_dir() or directory.name == BLOB_DIRNAME:
                continue
            if not (directory / MANIFEST_NAME).is_file():
                interrupted.append(
                    {
                        "path": str(directory),
                        "reason": (
                            "a directory without a manifest — an interrupted create (the "
                            "manifest is published last, so nothing was restorable here)"
                        ),
                    }
                )
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "verdict": VERDICT_LIST,
        "state_dir": str(state),
        "count": len(rows),
        "returned": min(len(rows), bound),
        "limit": bound,
        "checkpoints": rows[:bound],
        "interrupted": interrupted,
        "caps": caps.as_dict(),
        "limits": list(LIMITS),
    }


def resolve_checkpoint_id(state_dir: str | os.PathLike, value: str) -> str:
    """``"last"`` (or ``""``) means the newest checkpoint; anything else is literal."""
    text = str(value or "").strip()
    if text and text != "last":
        return text
    state = state_root(state_dir)
    if not state.is_dir():
        return ""
    candidates = sorted(state.glob(f"*/{MANIFEST_NAME}"), reverse=True)
    return candidates[0].parent.name if candidates else ""


def load_checkpoint(
    state_dir: str | os.PathLike, checkpoint_id: str
) -> tuple[dict[str, Any] | None, str]:
    """One manifest by id. Returns ``(manifest, problem)`` — a torn file is a problem."""
    text = str(checkpoint_id or "").strip()
    if not text:
        return None, "no checkpoint id was given"
    manifest_path = state_root(state_dir) / text / MANIFEST_NAME
    if not manifest_path.is_file():
        directory = state_root(state_dir) / text
        if directory.is_dir():
            return None, (
                f"{text}: the directory exists but holds no manifest — an interrupted "
                "create, nothing to restore from"
            )
        return None, f"unknown checkpoint: {text}"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"{text}: the manifest cannot be read ({exc})"
    if not isinstance(data, dict):
        return None, f"{text}: the manifest is not a JSON object"
    data.setdefault("checkpoint_id", text)
    data["manifest_path"] = str(manifest_path)
    data["state_dir"] = str(state_root(state_dir))
    return data, ""


# --------------------------------------------------------------------------- #
# the restore plan (preview + drift)                                            #
# --------------------------------------------------------------------------- #


def _conversation_plan(block: Mapping[str, Any] | None, store: Any) -> dict[str, Any]:
    """What a restore would do to the conversation, and why it would refuse."""
    if not block:
        return {
            "session_id": "",
            "status": CONV_NONE,
            "action": "none",
            "message": "the checkpoint recorded no conversation (a files-only checkpoint)",
            "recorded_messages": 0,
            "current_messages": 0,
            "extra_messages": 0,
            "prefix_ok": None,
            "conversation_sha256_recorded": "",
            "conversation_sha256_current": "",
            "history_matches": None,
            "identical": None,
        }
    session_id = str(block.get("session_id") or "")
    recorded = [item for item in block.get("messages") or [] if isinstance(item, Mapping)]
    plan: dict[str, Any] = {
        "session_id": session_id,
        "status": CONV_DRIFTED,
        "action": "fork",
        "message": "",
        "recorded_messages": len(recorded),
        "current_messages": 0,
        "extra_messages": 0,
        "prefix_ok": None,
        "conversation_sha256_recorded": str(block.get("conversation_sha256") or ""),
        "conversation_sha256_current": "",
        "history_matches": None,
        "identical": None,
    }
    if store is None:
        plan.update({"status": CONV_GONE, "action": "refuse", "message": "no session store"})
        return plan
    try:
        session = store.require_session(session_id)
    except UnknownSession as exc:
        plan.update(
            {
                "status": CONV_GONE,
                "action": "refuse",
                "message": (
                    f"{exc} — the checkpoint referenced the bodies, it did not copy them"
                ),
            }
        )
        return plan
    rows = store.messages(session_id, limit=max(1, len(recorded)))
    plan["current_messages"] = int(session.message_count)
    plan["extra_messages"] = max(0, int(session.message_count) - len(recorded))
    digests: list[dict[str, Any]] = []
    mismatch: list[dict[str, Any]] = []
    for index, item in enumerate(recorded):
        if index >= len(rows):
            mismatch.append({"seq": item.get("seq"), "reason": "missing from the store"})
            continue
        row = rows[index]
        actual = content_hash(str(getattr(row, "content", "") or ""))
        digests.append(
            {
                "seq": int(getattr(row, "seq", 0) or 0),
                "role": str(getattr(row, "role", "") or ""),
                "kind": str(getattr(row, "kind", "") or ""),
                "content_sha256": actual,
            }
        )
        if actual != str(item.get("content_sha256") or "") or int(
            getattr(row, "seq", 0) or 0
        ) != int(item.get("seq") or 0):
            mismatch.append(
                {
                    "seq": int(item.get("seq") or 0),
                    "expected_sha256": str(item.get("content_sha256") or ""),
                    "actual_sha256": actual,
                }
            )
    plan["conversation_sha256_current"] = conversation_hash(digests) if len(
        digests
    ) == len(recorded) else ""
    plan["prefix_ok"] = not mismatch
    plan["mismatch"] = mismatch[:20]
    plan["mismatch_count"] = len(mismatch)
    if mismatch:
        plan.update(
            {
                "status": CONV_BROKEN,
                "action": "refuse",
                "message": (
                    f"{len(mismatch)} stored message(s) no longer hash to what the checkpoint "
                    "recorded — the conversation cannot be restored exactly (a purge or an "
                    "out-of-band edit, not drift)"
                ),
            }
        )
        return plan
    if plan["conversation_sha256_current"] == plan["conversation_sha256_recorded"]:
        plan["identical"] = True
    if plan["extra_messages"] == 0:
        verification = store.verify_history(session_id)
        plan.update(
            {
                "status": CONV_IN_PLACE,
                "action": "none",
                "history_matches": bool(verification.get("matches")),
                "message": (
                    "the session still stands exactly at this checkpoint — nothing to do"
                ),
            }
        )
        return plan
    plan["history_matches"] = False
    plan["message"] = (
        f"{plan['extra_messages']} message(s) were appended after the checkpoint "
        f"(seq {len(recorded) + 1}..{int(session.last_seq)}); the store is append-only, so "
        "the point is returned by forking a byte-identical prefix"
    )
    return plan


def _workspace_of(checkpoint: Mapping[str, Any], workspace: str | os.PathLike | None) -> Path:
    """The tree a restore targets: the caller's, else the one the checkpoint recorded."""
    text = str(workspace or "").strip()
    if not text:
        text = str(checkpoint.get("workspace") or "")
    candidate = Path(text).expanduser()
    try:
        return candidate.resolve()
    except OSError:  # pragma: no cover — a broken mount
        return candidate


def plan_restore(
    checkpoint: Mapping[str, Any],
    *,
    workspace: str | os.PathLike | None = None,
    store: Any = None,
    caps: Caps | None = None,
    state_dir: str | os.PathLike | None = None,
    diff: bool = True,
) -> dict[str, Any]:
    """The preview: per-file status + diff, the conversation's drift, and every refusal.

    Nothing here writes. ``restore_checkpoint`` acts on this plan, and ``show`` prints it.
    """
    caps = caps or Caps.from_env()
    state = state_root(state_dir or checkpoint.get("state_dir") or "")
    recorded_workspace = str(checkpoint.get("workspace") or "")
    root = _workspace_of(checkpoint, workspace)
    plan: dict[str, Any] = {
        "checkpoint_id": str(checkpoint.get("checkpoint_id") or ""),
        "created_at": str(checkpoint.get("created_at") or ""),
        "label": str(checkpoint.get("label") or ""),
        "workspace": str(root),
        "workspace_recorded": recorded_workspace,
        "workspace_matches": str(root) == recorded_workspace,
        "exists": root.is_dir(),
        "files": [],
        "conversation": {},
        "drift": {"files": [], "conversation": [], "count": 0},
        "refusals": [],
        "writes": 0,
        "deletes": 0,
        "skips": 0,
        "bytes_written": 0,
        "diff_truncated": False,
    }
    if not plan["exists"]:
        plan["refusals"].append(
            {
                "kind": "missing_workspace",
                "message": f"the workspace does not exist: {root}",
                "forceable": False,
            }
        )
    if not plan["workspace_matches"]:
        plan["refusals"].append(
            {
                "kind": "workspace_mismatch",
                "message": (
                    f"the checkpoint was created in {recorded_workspace or '(none)'}, not in "
                    f"{root} — restoring into another tree is a different operation"
                ),
                "forceable": True,
            }
        )
    for entry in checkpoint.get("files") or []:
        item = dict(entry)
        relative = str(item.get("path") or "")
        recorded_sha = str(item.get("sha256") or "")
        recorded_exists = bool(item.get("exists"))
        row: dict[str, Any] = {
            "path": relative,
            "recorded_exists": recorded_exists,
            "recorded_sha256": recorded_sha,
            "recorded_bytes": int(item.get("bytes") or 0),
            "recorded_mode": int(item.get("mode") or 0),
            "source_tx": str(item.get("source_tx") or ""),
            "current_sha256": "",
            "current_bytes": 0,
            "status": FILE_IDENTICAL,
            "action": ACTION_SKIP,
            "diff": "",
            "diff_truncated": False,
            "message": "",
        }
        target, problem = safe_path(root, relative)
        if target is None:
            row.update({"status": "refused", "action": "refuse", "message": problem})
            plan["refusals"].append(
                {"kind": "unsafe_path", "message": problem, "forceable": False}
            )
            plan["files"].append(row)
            continue
        present = target.is_file()
        current = b""
        if present:
            try:
                current = target.read_bytes()
            except OSError as exc:
                row["message"] = f"unreadable: {exc}"
            row["current_sha256"] = sha256_bytes(current)
            row["current_bytes"] = len(current)
        if recorded_exists and present:
            if row["current_sha256"] == recorded_sha:
                row["status"] = FILE_IDENTICAL
            else:
                row["status"] = FILE_CHANGED
                row["action"] = ACTION_WRITE
                if diff:
                    stored, problem = read_blob(state, recorded_sha)
                    if stored is None:
                        plan["refusals"].append(
                            {"kind": "blob_mismatch", "message": problem, "forceable": False}
                        )
                        row.update({"status": "unreadable", "action": "refuse"})
                    else:
                        text, truncated = _truncate(
                            _diff(current, stored, relative), caps.max_diff_bytes
                        )
                        row["diff"], row["diff_truncated"] = text, truncated
                        plan["diff_truncated"] = plan["diff_truncated"] or truncated
        elif recorded_exists and not present:
            row.update({"status": FILE_MISSING, "action": ACTION_WRITE})
            if diff:
                stored, problem = read_blob(state, recorded_sha)
                if stored is None:
                    plan["refusals"].append(
                        {"kind": "blob_mismatch", "message": problem, "forceable": False}
                    )
                    row.update({"status": "unreadable", "action": "refuse"})
                else:
                    text, truncated = _truncate(
                        _diff(b"", stored, relative), caps.max_diff_bytes
                    )
                    row["diff"], row["diff_truncated"] = text, truncated
        elif present:
            row.update({"status": FILE_UNEXPECTED, "action": ACTION_DELETE})
        if row["status"] in (FILE_CHANGED, FILE_MISSING, FILE_UNEXPECTED):
            plan["drift"]["files"].append(
                {
                    "path": relative,
                    "status": row["status"],
                    "recorded_sha256": recorded_sha,
                    "current_sha256": row["current_sha256"],
                }
            )
        if row["action"] == ACTION_WRITE:
            plan["writes"] += 1
            plan["bytes_written"] += int(item.get("bytes") or 0)
        elif row["action"] == ACTION_DELETE:
            plan["deletes"] += 1
        else:
            plan["skips"] += 1
        plan["files"].append(row)

    conversation = _conversation_plan(checkpoint.get("conversation"), store)
    plan["conversation"] = conversation
    if conversation["status"] in (CONV_DRIFTED,):
        plan["drift"]["conversation"].append(
            {
                "session_id": conversation["session_id"],
                "extra_messages": conversation["extra_messages"],
                "recorded_messages": conversation["recorded_messages"],
            }
        )
    if conversation["status"] in (CONV_GONE, CONV_DRIFTED):
        plan["refusals"].append(
            {
                "kind": "conversation_gone" if conversation["status"] == CONV_GONE
                else "drift",
                "message": conversation["message"],
                "forceable": True,
                "scope": "conversation",
            }
        )
    if conversation["status"] == CONV_BROKEN:
        plan["refusals"].append(
            {
                "kind": "conversation_broken",
                "message": conversation["message"],
                "forceable": False,
                "scope": "conversation",
            }
        )
    if plan["drift"]["files"]:
        plan["refusals"].append(
            {
                "kind": "drift",
                "message": (
                    f"{len(plan['drift']['files'])} file(s) changed since the checkpoint "
                    "(pass --force to restore anyway; the drift is recorded)"
                ),
                "forceable": True,
                "scope": "files",
            }
        )
    plan["drift"]["count"] = len(plan["drift"]["files"])
    plan["restorable"] = not plan["refusals"]
    plan["restorable_with_force"] = not any(
        not item["forceable"] for item in plan["refusals"]
    )
    plan["would_refuse"] = next(
        (item["kind"] for item in plan["refusals"] if not item["forceable"]),
        next((item["kind"] for item in plan["refusals"]), ""),
    )
    return plan


def _forced_drift_rows(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The drift a ``--force`` restore walked over, in one uniform shape per record."""
    rows: list[dict[str, Any]] = [
        {
            "kind": "file",
            "target": str(item.get("path") or ""),
            "status": str(item.get("status") or ""),
            "recorded_sha256": str(item.get("recorded_sha256") or ""),
            "current_sha256": str(item.get("current_sha256") or ""),
        }
        for item in plan.get("drift", {}).get("files", [])
    ]
    rows.extend(
        {
            "kind": "conversation",
            "target": str(item.get("session_id") or ""),
            "status": CONV_DRIFTED,
            "extra_messages": int(item.get("extra_messages") or 0),
            "recorded_messages": int(item.get("recorded_messages") or 0),
        }
        for item in plan.get("drift", {}).get("conversation", [])
    )
    return rows


def show_checkpoint(
    state_dir: str | os.PathLike,
    checkpoint_id: str = "",
    *,
    workspace: str | os.PathLike | None = None,
    store: Any = None,
    caps: Caps | None = None,
    diff: bool = True,
) -> dict[str, Any]:
    """One checkpoint: the manifest, its cost, and the exact preview of a restore."""
    caps = caps or Caps.from_env()
    state = state_root(state_dir)
    resolved = resolve_checkpoint_id(state, checkpoint_id)
    if not resolved:
        return _refusal("not_found", f"no checkpoint in {state}")
    checkpoint, problem = load_checkpoint(state, resolved)
    if checkpoint is None:
        return _refusal("not_found", problem)
    plan = plan_restore(
        checkpoint, workspace=workspace, store=store, caps=caps, state_dir=state, diff=diff
    )
    payload = dict(checkpoint)
    payload.update(
        {
            "verdict": VERDICT_PREVIEW,
            "plan": plan,
            "preview": {
                "workspace": plan["workspace"],
                "files": plan["files"],
                "conversation": plan["conversation"],
                "drift": plan["drift"],
                "writes": plan["writes"],
                "deletes": plan["deletes"],
                "skips": plan["skips"],
                "restorable": plan["restorable"],
                "would_refuse": plan["would_refuse"],
                "refusals": plan["refusals"],
            },
            "confirmation_command": (
                f"skaldir checkpoint restore {resolved} --yes --json"
            ),
        }
    )
    return payload


# --------------------------------------------------------------------------- #
# restore                                                                       #
# --------------------------------------------------------------------------- #


def _summarise_resume(store: Any, session_id: str) -> dict[str, Any]:
    """The long-run continuation, taken from the store's own ``resume`` (step 0)."""
    block: dict[str, Any] = {
        "step": 0,
        "protocol": (
            "long-run resume: the checkpoint is what the protocol restores before the next "
            "turn — files first, then the conversation"
        ),
        "session_id": session_id,
        "next_role": "",
        "next_seq": 0,
        "complete": False,
        "continuation_command": "",
        "store_resume": None,
    }
    if store is None or not session_id:
        return block
    try:
        answer = store.resume(session_id, limit=20)
    except UnknownSession as exc:  # pragma: no cover — the plan already checked this
        block["reason"] = str(exc)
        return block
    window = answer.get("window") or {}
    history = answer.get("history") or {}
    block.update(
        {
            "next_role": str(answer.get("next_role") or ""),
            "next_seq": int(answer.get("next_seq") or 0),
            "complete": bool(history.get("matches")),
            "continuation_command": str(
                (answer.get("continuation") or {}).get("command") or ""
            ),
            "history_matches": bool(history.get("matches")),
            "window": dict(window),
            "store_resume": {
                "session": (answer.get("session") or {}).get("session_id", ""),
                "next_role": answer.get("next_role"),
                "next_seq": answer.get("next_seq"),
                "history": {
                    "matches": history.get("matches"),
                    "stored_sha256": history.get("stored_sha256"),
                    "recomputed_sha256": history.get("recomputed_sha256"),
                },
                "window": window,
            },
        }
    )
    return block


def restore_checkpoint(
    state_dir: str | os.PathLike,
    checkpoint_id: str = "",
    *,
    workspace: str | os.PathLike | None = None,
    force: bool = False,
    dry_run: bool = False,
    confirm: bool = False,
    runtime: str | os.PathLike | None = None,
    store: Any = None,
    caps: Caps | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Preview, then restore the declared files and the conversation — or refuse.

    The order is deliberate: **plan, confirm, act**. Without ``confirm`` nothing is
    written and the payload carries the full preview (``confirmation_required``); with
    ``confirm`` a drift refuses (exit 6) unless ``force`` is given, and the forced drift is
    recorded in the payload. Every written file is re-hashed and reported as
    ``byte_identical`` or not — a restore never claims a byte it did not verify.
    """
    started = time.time()
    caps = caps or Caps.from_env()
    state = state_root(state_dir)
    resolved = resolve_checkpoint_id(state, checkpoint_id)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "state_dir": str(state),
        "checkpoint_id": resolved,
        "limits": list(LIMITS),
    }
    if not resolved:
        payload.update(
            _refusal("not_found", f"no checkpoint in {state}", verdict=VERDICT_NOT_FOUND)
        )
        return payload
    checkpoint, problem = load_checkpoint(state, resolved)
    if checkpoint is None:
        payload.update(_refusal("not_found", problem, verdict=VERDICT_NOT_FOUND))
        payload["checkpoint_id"] = resolved
        return payload

    owns_store = False
    if store is None:
        store, owns_store = _open_session_store(checkpoint, runtime)

    plan = plan_restore(
        checkpoint,
        workspace=workspace,
        store=store,
        caps=caps,
        state_dir=state,
        diff=True,
    )
    payload.update(
        {
            "ok": True,
            "verdict": VERDICT_PREVIEW,
            "created_at": checkpoint.get("created_at", ""),
            "label": checkpoint.get("label", ""),
            "workspace": plan["workspace"],
            "workspace_recorded": plan["workspace_recorded"],
            "files": plan["files"],
            "conversation": plan["conversation"],
            "drift": plan["drift"],
            "refusals": plan["refusals"],
            "preview": {
                "writes": plan["writes"],
                "deletes": plan["deletes"],
                "skips": plan["skips"],
                "bytes_written": plan["bytes_written"],
                "restorable": plan["restorable"],
                "would_refuse": plan["would_refuse"],
            },
            "dry_run": bool(dry_run),
            "forced": bool(force),
            "written": [],
            "complete": False,
            "forced_drift": [],
        }
    )
    blocking = [item for item in plan["refusals"] if not item["forceable"]]
    forcing = [item for item in plan["refusals"] if item["forceable"]]

    if dry_run or not confirm:
        payload["verdict"] = (
            VERDICT_PREVIEW if dry_run else VERDICT_CONFIRMATION_REQUIRED
        )
        payload["confirmation_required"] = not confirm
        payload["confirmation_command"] = f"skaldir checkpoint restore {resolved} --yes --json"
        payload["stats"] = {"restore_ms": int((time.time() - started) * 1000)}
        if store is not None and owns_store:
            store.close()
        return payload

    if blocking:
        payload.update(
            _refusal(
                str(blocking[0]["kind"]),
                str(blocking[0]["message"]),
                refusals=blocking,
            )
        )
        payload["verdict"] = (
            VERDICT_CORRUPT if blocking[0]["kind"] == "blob_mismatch" else VERDICT_BLOCKED
        )
        if blocking[0]["kind"] == "conversation_broken":
            payload["verdict"] = "verification_failed"
        if store is not None and owns_store:
            store.close()
        return payload

    if forcing and not force:
        payload.update(
            _refusal(
                str(forcing[0]["kind"]),
                str(forcing[0]["message"]),
                refusals=forcing,
                drift=plan["drift"],
            )
        )
        payload["verdict"] = VERDICT_REFUSED_DRIFT
        if store is not None and owns_store:
            store.close()
        return payload

    payload["forced_drift"] = _forced_drift_rows(plan) if force else []
    written: list[dict[str, Any]] = []
    for row in plan["files"]:
        if row["action"] == ACTION_SKIP:
            row["result"] = "skipped"
            continue
        target, problem = safe_path(Path(plan["workspace"]), str(row["path"]))
        if target is None:  # pragma: no cover — the plan refused this already
            row["result"] = "refused"
            row["message"] = problem
            continue
        if row["action"] == ACTION_DELETE:
            try:
                target.unlink()
                row["result"] = "deleted"
                row["byte_identical"] = True
            except OSError as exc:
                row["result"] = "failed"
                row["message"] = str(exc)
            written.append(_written_row(row))
            _crash_point(CRASH_MID_RESTORE)
            continue
        stored, problem = read_blob(state, str(row["recorded_sha256"]))
        if stored is None:
            payload.update(_refusal("blob_mismatch", problem, path=row["path"]))
            payload["verdict"] = VERDICT_CORRUPT
            payload["written"] = written
            if store is not None and owns_store:
                store.close()
            return payload
        mode = int(row.get("recorded_mode") or 0) or None
        try:
            _atomic_write(target, stored, mode=mode)
        except OSError as exc:
            row["result"] = "failed"
            row["message"] = str(exc)
            written.append(_written_row(row))
            continue
        after = sha256_bytes(target.read_bytes()) if target.is_file() else ""
        row["result"] = "written"
        row["sha256_after"] = after
        row["byte_identical"] = after == str(row["recorded_sha256"])
        if not row["byte_identical"]:
            row["message"] = "the written file does not hash to the recorded sha256"
        written.append(_written_row(row))
        _crash_point(CRASH_MID_RESTORE)

    conversation = plan["conversation"]
    conversation_result = dict(conversation)
    if conversation["status"] == CONV_IN_PLACE:
        conversation_result.update({"restored": True, "mode": CONV_IN_PLACE})
    elif conversation["status"] == CONV_NONE:
        conversation_result.update({"restored": True, "mode": CONV_NONE, "identical": None})
    elif conversation["status"] == CONV_DRIFTED:
        conversation_result.update(
            _fork_conversation(store, checkpoint, plan, conversation, caps=caps)
        )
    else:
        conversation_result.update({"restored": False, "mode": conversation["status"]})

    payload.update(
        {
            "written": written,
            "conversation": conversation_result,
            "complete": bool(
                all(item.get("byte_identical") for item in written)
                and conversation_result.get("restored")
            ),
        }
    )
    payload["verdict"] = (
        VERDICT_RESTORED if payload["complete"] else VERDICT_RESTORED_PARTIAL
    )
    target_session = str(
        conversation_result.get("restore_session_id") or conversation.get("session_id") or ""
    )
    payload["resume"] = _summarise_resume(store, target_session)
    payload["stats"] = {
        "restore_ms": int((time.time() - started) * 1000),
        "files_written": sum(1 for item in written if item.get("result") == "written"),
        "files_deleted": sum(1 for item in written if item.get("result") == "deleted"),
        "files_skipped": plan["skips"],
        "bytes_written": sum(int(item.get("bytes") or 0) for item in written),
        "drift_recorded": len(payload["forced_drift"]),
        "conversation_messages_replayed": int(
            conversation_result.get("message_count") or 0
        )
        if conversation_result.get("restore_session_id")
        else 0,
    }
    if store is not None and owns_store:
        store.close()
    return payload


def _written_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """One file's restore result — the sha256 before and after, never an adjective alone."""
    return {
        "path": str(row.get("path") or ""),
        "action": str(row.get("action") or ""),
        "result": str(row.get("result") or ""),
        "byte_identical": row.get("byte_identical"),
        "sha256_before": str(row.get("current_sha256") or ""),
        "sha256_checkpoint": str(row.get("recorded_sha256") or ""),
        "sha256_after": str(row.get("sha256_after") or ""),
        "bytes": int(row.get("recorded_bytes") or 0),
        "message": str(row.get("message") or ""),
    }


def _fork_conversation(
    store: Any,
    checkpoint: Mapping[str, Any],
    plan: Mapping[str, Any],
    conversation: Mapping[str, Any],
    *,
    caps: Caps,
) -> dict[str, Any]:
    """Return the conversation to the checkpoint by forking a byte-identical prefix."""
    block = checkpoint.get("conversation") or {}
    session_id = str(conversation.get("session_id") or "")
    result: dict[str, Any] = {
        "restored": False,
        "mode": CONV_FORK,
        "restore_session_id": "",
        "conversation_sha256_recorded": str(block.get("conversation_sha256") or ""),
        "identical": False,
        "history_matches": None,
        "next_role": "",
        "next_seq": 0,
        "rules": [
            "the store is append-only: the newer messages are kept, not deleted",
            "the successor's prefix is byte-identical (origin_message_id points back)",
        ],
    }
    if store is None:
        result["reason"] = "no session store was reachable"
        return result
    lease = store.acquire_writer(owner="skaldir.checkpoints")
    if not lease.get("acquired"):
        result["reason"] = (
            f"the session store is held by {lease.get('holder')!r} — the conversation half "
            "of this restore needs the writer lease"
        )
        return result
    try:
        answer = store.fork(
            session_id,
            at_message_id=str(block.get("head_message_id") or ""),
            title=f"restored {str(checkpoint.get('checkpoint_id') or '')}".strip(),
            labels=["checkpoint-restore", str(checkpoint.get("checkpoint_id") or "")],
        )
        child = str((answer.get("child") or {}).get("session_id") or "")
        expected = int(block.get("message_count") or 0)
        rows = store.messages(child, limit=max(1, expected)) if child else []
        digests = [
            {
                "seq": int(getattr(item, "seq", 0) or 0),
                "role": str(getattr(item, "role", "") or ""),
                "kind": str(getattr(item, "kind", "") or ""),
                "content_sha256": content_hash(str(getattr(item, "content", "") or "")),
            }
            for item in rows
        ]
        digest = conversation_hash(digests) if len(digests) == expected else ""
        verification = store.verify_history(child) if child else {"matches": None}
        session = store.session(child) if child else None
        result.update(
            {
                "restored": bool(child) and digest == result["conversation_sha256_recorded"],
                "restore_session_id": child,
                "conversation_sha256_after": digest,
                "identical": bool(child) and digest == result["conversation_sha256_recorded"],
                "history_matches": bool(verification.get("matches")),
                "message_count": len(rows),
                "replayed": int(answer.get("copied_messages") or 0),
                "next_role": str(getattr(session, "next_role", "") or ""),
                "next_seq": int(getattr(session, "last_seq", 0) or 0) + 1,
                "lineage": (answer.get("child") or {}).get("lineage", {}),
            }
        )
        result["reason"] = (
            "" if result["restored"] else "the successor's prefix did not reproduce the digest"
        )
    except Exception as exc:  # noqa: BLE001 — a fork refusal is a result, not a crash
        result["reason"] = f"the fork was refused: {exc}"
    finally:
        store.release_writer(owner="skaldir.checkpoints")
    return result


def _open_session_store(
    checkpoint: Mapping[str, Any], runtime: str | os.PathLike | None
) -> tuple[Any, bool]:
    """Open the session store a checkpoint references (read-only: only if it is there)."""
    block = checkpoint.get("conversation") or {}
    path = str(block.get("store") or "")
    store = open_session_store(path or None, runtime=runtime)
    return (store, True) if store is not None else (None, False)


# --------------------------------------------------------------------------- #
# the blob store's garbage collection (`checkpoint prune`)                       #
# --------------------------------------------------------------------------- #

#: Every object the pruner refuses to delete on, and why (one home for the reason text).
PRUNE_REFUSALS: dict[str, str] = {
    "unreadable_manifest": (
        "a manifest that cannot be read may reference blobs this prune cannot see. An "
        "unreadable checkpoint is repaired or removed first, never swept around"
    ),
    "in_flight_create": (
        "a directory without a manifest is the shape a killed (or still running) create "
        "leaves: its blobs are written before the manifest, so removing them would "
        "corrupt a checkpoint that is about to exist. Pass --force (recorded in the "
        "payload) to reclaim from interrupted directories"
    ),
}


def manifest_blob_refs(checkpoint: Mapping[str, Any]) -> set[str]:
    """Every blob address one manifest names.

    The recorded blob is the file half: each ``files[]`` record carries the address of
    the bytes it snapshotted (``sha256``, plus the ``blob`` path it was written to).
    Conversation *bodies* stay in the session store and are referenced by message hash,
    so a message's ``content_sha256`` is deliberately **not** a blob address here —
    treating it as one would keep unrelated file blobs alive forever.
    """
    refs: set[str] = set()
    for entry in checkpoint.get("files") or []:
        if not isinstance(entry, Mapping):
            continue
        sha = str(entry.get("sha256") or "").strip().lower()
        if sha:
            refs.add(sha)
        blob = str(entry.get("blob") or "").strip()
        if blob:
            refs.add(Path(blob).name.lower())
    return refs


def _blob_files(state: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(blobs, unexpected)`` — the store's files, split into addressed and stray.

    A file whose name is not a 64-hex content address is never a candidate: it is
    reported so a human can decide, because this function cannot tell whose bytes it is.
    """
    blobs: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []
    directory = state / BLOB_DIRNAME
    if not directory.is_dir():
        return blobs, unexpected
    for path in sorted(directory.rglob("*")):
        try:
            if not path.is_file():
                continue
            size = path.stat().st_size
        except OSError as exc:  # pragma: no cover — a file that vanished under us
            unexpected.append({"path": str(path), "reason": f"unreadable: {exc}"})
            continue
        name = path.name.lower()
        if len(name) != 64 or any(char not in "0123456789abcdef" for char in name):
            unexpected.append(
                {"path": str(path), "reason": "not a content address (never swept)", "bytes": size}
            )
            continue
        blobs.append({"sha256": name, "path": path, "bytes": size})
    return blobs, unexpected


def prune_checkpoints(
    state_dir: str | os.PathLike,
    *,
    dry_run: bool = True,
    force: bool = False,
    limit: int | None = None,
    caps: Caps | None = None,
) -> dict[str, Any]:
    """Reclaim the blobs **no readable checkpoint references** — a dry run by default.

    The contract, in the order it matters:

    * every readable manifest is read, and nothing whose address one of them names is
      ever a candidate — the whole store is read, not the ``list`` window;
    * two states refuse the sweep instead of guessing: an **unreadable manifest** (it may
      name blobs we cannot see) and a **directory without a manifest** (a create writes
      its blobs *before* publishing the manifest, so that directory's blobs may belong to
      a checkpoint that is about to exist). ``--force`` overrides only the second, and the
      override is recorded;
    * the sweep is **bounded** (``caps.prune_max_blobs``, overridable with
      ``SKALDIR_CHECKPOINT_PRUNE_MAX_BLOBS``): what did not fit is reported as
      ``truncated`` with the count left for the next run;
    * a stray file (a name that is not a content address) is reported, never deleted.

    ``dry_run`` (the default) reports the candidates without touching a byte.
    """
    caps = caps or Caps.from_env()
    state = state_root(state_dir)
    reference: dict[str, list[str]] = {}
    unreadable: list[dict[str, Any]] = []
    readable = 0
    for manifest_path in sorted(state.glob(f"*/{MANIFEST_NAME}")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            unreadable.append(
                {"path": str(manifest_path), "reason": f"unreadable: {exc}"[:200]}
            )
            continue
        if not isinstance(data, dict):
            unreadable.append({"path": str(manifest_path), "reason": "not a JSON object"})
            continue
        readable += 1
        checkpoint_id = str(data.get("checkpoint_id") or manifest_path.parent.name)
        for sha in manifest_blob_refs(data):
            reference.setdefault(sha, []).append(checkpoint_id)

    interrupted: list[dict[str, Any]] = []
    if state.is_dir():
        for directory in sorted(state.iterdir()):
            if not directory.is_dir() or directory.name == BLOB_DIRNAME:
                continue
            if not (directory / MANIFEST_NAME).is_file():
                interrupted.append({"path": str(directory), "name": directory.name})

    blobs, unexpected = _blob_files(state)
    candidates = [row for row in blobs if row["sha256"] not in reference]
    referenced_bytes = sum(row["bytes"] for row in blobs) - sum(
        row["bytes"] for row in candidates
    )
    reclaimable = sum(row["bytes"] for row in candidates)
    bound = max(0, int(limit if limit is not None else caps.prune_max_blobs))
    selected, left = candidates[:bound], candidates[bound:]

    refusals: list[str] = []
    if unreadable:
        refusals.append(PRUNE_REFUSALS["unreadable_manifest"])
    if interrupted and not dry_run and not force:
        refusals.append(PRUNE_REFUSALS["in_flight_create"])

    removed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    if not dry_run and not refusals:
        for row in selected:
            sha = str(row["sha256"])
            path = Path(row["path"])
            try:
                path.unlink()
            except OSError as exc:
                failed.append({"sha256": sha, "path": str(path), "reason": str(exc)[:200]})
                continue
            _prune_fanout(path.parent)
            removed.append({"sha256": sha, "bytes": int(row["bytes"]), "path": str(path)})

    verdict = VERDICT_PRUNE_PLAN if dry_run else VERDICT_PRUNED
    if refusals:
        verdict = VERDICT_BLOCKED
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": not refusals,
        "verdict": verdict,
        "state_dir": str(state),
        "dry_run": bool(dry_run),
        "force": bool(force),
        "checkpoints": {
            "readable": readable,
            "unreadable": len(unreadable),
            "interrupted": len(interrupted),
        },
        "unreadable": unreadable[:20],
        "interrupted": interrupted[:20],
        "blobs": {
            "total": len(blobs),
            "bytes": referenced_bytes + reclaimable,
            "referenced": len(blobs) - len(candidates),
            "referenced_bytes": referenced_bytes,
            "unreferenced": len(candidates),
            "reclaimable_bytes": reclaimable,
            "selected": len(selected),
            "removed": len(removed),
            "removed_bytes": sum(int(row["bytes"]) for row in removed),
            "failed": len(failed),
            "left": len(left),
        },
        "limit": bound,
        "truncated": bool(left),
        "candidates": [
            {"sha256": str(row["sha256"]), "bytes": int(row["bytes"]), "path": str(row["path"])}
            for row in selected[:50]
        ],
        "removed": removed[:50],
        "failed": failed[:20],
        "unexpected": unexpected[:20],
        "referenced_by": {
            sha: sorted(set(names))[:5] for sha, names in sorted(reference.items())
        },
        "refusals": refusals,
        "error": (
            {"kind": "blocked", "message": " ".join(refusals), "detail": {
                "unreadable": unreadable[:5], "interrupted": interrupted[:5]}}
            if refusals
            else {}
        ),
        "caps": caps.as_dict(),
        "limits": [
            *LIMITS,
            "prune reads every readable manifest and still refuses on an unreadable one "
            "or on a directory without a manifest (a create publishes its manifest last)",
            f"prune removes at most {bound} orphan blob(s) per call "
            "(SKALDIR_CHECKPOINT_PRUNE_MAX_BLOBS); the rest is reported as truncated",
        ],
    }


def _prune_fanout(directory: Path) -> None:
    """Remove an empty ``blobs/ab`` fan-out directory (ignore a non-empty or foreign one)."""
    try:
        directory.rmdir()
    except OSError:  # still holds blobs, or somebody else owns it
        return


def exit_code_for(payload: Mapping[str, Any]) -> int:
    """The one mapping from a checkpoint verdict to an exit code."""
    verdict = str(payload.get("verdict") or "")
    error = payload.get("error") or {}
    kind = str(error.get("kind") or "")
    if verdict in (VERDICT_CREATED, VERDICT_LIST, VERDICT_PREVIEW, VERDICT_PRUNED,
                   VERDICT_PRUNE_PLAN):
        return EXIT_OK
    if verdict == VERDICT_RESTORED:
        return EXIT_OK
    if verdict == VERDICT_CONFIRMATION_REQUIRED:
        return EXIT_USAGE
    if verdict == VERDICT_REFUSED_DRIFT or kind == "drift" or kind == "workspace_mismatch":
        return EXIT_CONFLICT
    if verdict == VERDICT_NOT_FOUND or kind == "not_found":
        return EXIT_NOT_FOUND
    if verdict == "verification_failed" or kind in ("conversation_broken", "blob_mismatch"):
        return EXIT_VERIFICATION_FAILED
    if verdict in (VERDICT_BLOCKED, VERDICT_RESTORED_PARTIAL, VERDICT_CORRUPT):
        return EXIT_BLOCKED
    if kind in ("cap_exceeded", "blocked"):
        return EXIT_BLOCKED
    return EXIT_INTERNAL


# --------------------------------------------------------------------------- #
# human text                                                                    #
# --------------------------------------------------------------------------- #


def _file_lines(rows: Iterable[Mapping[str, Any]], *, show_diff: bool) -> list[str]:
    lines: list[str] = []
    for item in rows:
        mark = {
            ACTION_WRITE: "update",
            ACTION_DELETE: "delete",
            ACTION_SKIP: "keep  ",
        }.get(str(item.get("action")), "keep  ")
        lines.append(
            f"  {mark} {item.get('path')}  "
            f"({item.get('status') or item.get('result')}, {item.get('current_bytes', 0)} bytes)"
        )
        if show_diff and item.get("diff"):
            lines.extend(f"      {line}" for line in str(item["diff"]).splitlines())
    return lines


def render_text(payload: Mapping[str, Any]) -> str:
    """The human view of any checkpoint payload — plain ASCII, no escapes."""
    verdict = str(payload.get("verdict") or "")
    lines: list[str] = []
    error = payload.get("error") or {}
    if error:
        lines.append(f"checkpoint: {error.get('message')}")
        lines.append(f"  kind: {error.get('kind')}")
        detail = error.get("detail") or {}
        for key in ("refusals", "drift", "problems"):
            if detail.get(key):
                lines.append(f"  {key}: {json.dumps(detail[key], default=str)[:600]}")
        return "\n".join(lines)
    if verdict in (VERDICT_PRUNE_PLAN, VERDICT_PRUNED, VERDICT_BLOCKED) and "blobs" in payload:
        blobs = payload.get("blobs") or {}
        lines.append(
            f"checkpoint prune {'(dry run)' if payload.get('dry_run') else ''} in "
            f"{payload.get('state_dir')}: {verdict}"
        )
        lines.append(
            f"  checkpoints: {payload.get('checkpoints', {}).get('readable')} readable, "
            f"{payload.get('checkpoints', {}).get('unreadable')} unreadable, "
            f"{payload.get('checkpoints', {}).get('interrupted')} interrupted"
        )
        lines.append(
            f"  blobs: {blobs.get('total')} total ({blobs.get('bytes')} bytes); "
            f"{blobs.get('referenced')} referenced by a checkpoint, "
            f"{blobs.get('unreferenced')} not ({blobs.get('reclaimable_bytes')} bytes)"
        )
        tail = (
            f" — limited, {blobs.get('left')} left for the next run"
            if payload.get("truncated")
            else ""
        )
        lines.append(
            f"  {'removed' if not payload.get('dry_run') else 'would remove'}: "
            f"{blobs.get('selected')} blob(s), {blobs.get('removed_bytes')} bytes{tail}"
        )
        for row in payload.get("candidates") or []:
            lines.append(f"    orphan {row['sha256'][:12]} {row['bytes']} bytes")
        for item in payload.get("unexpected") or []:
            lines.append(f"    kept, never swept: {item.get('path')} ({item.get('reason')})")
        for item in payload.get("failed") or []:
            lines.append(f"    failed: {item.get('path')} ({item.get('reason')})")
        if not payload.get("dry_run"):
            lines.append("  pass --dry-run to preview without removing anything")
        return "\n".join(lines)
    if verdict == VERDICT_LIST:
        lines.append(f"checkpoints in {payload.get('state_dir')}: {payload.get('count')}")
        for row in payload.get("checkpoints") or []:
            lines.append(
                f"  {row['checkpoint_id']}  {row['created_at']}  "
                f"{row['files']} file(s), {row['messages']} message(s), "
                f"{row['manifest_bytes']} bytes, {row['create_ms']} ms  {row['label']}"
            )
        for item in payload.get("interrupted") or []:
            lines.append(f"  interrupted: {item.get('path')} — {item.get('reason')}")
        return "\n".join(lines)
    if verdict == VERDICT_CREATED:
        stats = payload.get("stats") or {}
        conversation = payload.get("conversation") or {}
        lines.append(f"checkpoint {payload.get('checkpoint_id')} created")
        lines.append(f"  workspace: {payload.get('workspace')}")
        lines.append(
            f"  files: {stats.get('files_with_content')} with content, "
            f"{stats.get('captured_bytes')} bytes captured"
        )
        lines.append(
            f"  conversation: {conversation.get('message_count', 0)} message(s) referenced "
            f"({conversation.get('body_bytes_referenced', 0)} bytes stay in the session store)"
        )
        lines.append(
            f"  cost: {stats.get('create_ms')} ms, {stats.get('manifest_bytes')} manifest "
            f"bytes, {stats.get('blob_bytes_written')} blob bytes written, "
            f"{stats.get('blob_bytes_reused')} reused"
        )
        lines.append(f"  manifest: {payload.get('manifest_path')}")
        if payload.get("conversation_problem"):
            lines.append(f"  conversation not captured: {payload['conversation_problem']}")
        return "\n".join(lines)
    if verdict in (VERDICT_PREVIEW, VERDICT_CONFIRMATION_REQUIRED, VERDICT_RESTORED,
                   VERDICT_RESTORED_PARTIAL, VERDICT_REFUSED_DRIFT):
        plan = payload.get("plan") or payload
        preview = payload.get("preview") or {}
        lines.append(
            f"checkpoint {payload.get('checkpoint_id')} -> {plan.get('workspace')} "
            f"({verdict})"
        )
        lines.extend(_file_lines(plan.get("files") or [], show_diff=bool(preview.get("files"))))
        conversation = plan.get("conversation") or payload.get("conversation") or {}
        lines.append(
            f"  conversation: {conversation.get('status', conversation.get('mode'))} "
            f"{conversation.get('recorded_messages', conversation.get('message_count', 0))} "
            f"message(s); {conversation.get('message', '')}".rstrip()
        )
        if verdict == VERDICT_CONFIRMATION_REQUIRED:
            lines.append("  nothing was written: pass --yes to confirm the restore")
            lines.append(f"  confirm with: {payload.get('confirmation_command')}")
        for item in payload.get("written") or []:
            lines.append(
                f"  {item['result']} {item['path']} byte_identical={item['byte_identical']}"
            )
        if payload.get("forced_drift"):
            lines.append(f"  forced over {len(payload['forced_drift'])} drift record(s)")
        stats = payload.get("stats") or {}
        if stats:
            lines.append(f"  restore: {stats.get('restore_ms')} ms, "
                         f"{stats.get('files_written')} written")
        return "\n".join(lines)
    lines.append(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return "\n".join(lines)


__all__ = [
    "ACTION_DELETE",
    "ACTION_SKIP",
    "ACTION_WRITE",
    "CHECKPOINT_DIRNAME",
    "CONV_BROKEN",
    "CONV_DRIFTED",
    "CONV_FORK",
    "CONV_GONE",
    "CONV_IN_PLACE",
    "CONV_NONE",
    "CONVERSATION_HASH_ALGORITHM",
    "CRASH_AFTER_SNAPSHOT",
    "CRASH_ENV",
    "CRASH_MID_RESTORE",
    "CRASH_POINTS",
    "Caps",
    "EXIT_BLOCKED",
    "EXIT_CONFLICT",
    "EXIT_INTERNAL",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_VERIFICATION_FAILED",
    "FILE_CHANGED",
    "FILE_IDENTICAL",
    "FILE_MISSING",
    "FILE_UNEXPECTED",
    "FORCEABLE",
    "KILL_EXIT",
    "LIMITS",
    "MANIFEST_NAME",
    "PRUNE_REFUSALS",
    "SCHEMA_VERSION",
    "UNFORCEABLE",
    "VERDICT_BLOCKED",
    "VERDICT_CONFIRMATION_REQUIRED",
    "VERDICT_CORRUPT",
    "VERDICT_CREATED",
    "VERDICT_LIST",
    "VERDICT_NOT_FOUND",
    "VERDICT_PREVIEW",
    "VERDICT_REFUSED_DRIFT",
    "VERDICT_RESTORED",
    "VERDICT_RESTORED_PARTIAL",
    "VERDICT_PRUNED",
    "VERDICT_PRUNE_PLAN",
    "blob_path",
    "canonical_conversation_line",
    "collect_file_set",
    "conversation_block",
    "conversation_hash",
    "create_checkpoint",
    "exit_code_for",
    "list_checkpoints",
    "load_checkpoint",
    "manifest_blob_refs",
    "message_rows",
    "new_checkpoint_id",
    "open_session_store",
    "plan_restore",
    "prune_checkpoints",
    "read_blob",
    "render_text",
    "resolve_checkpoint_id",
    "restore_checkpoint",
    "safe_path",
    "sha256_bytes",
    "show_checkpoint",
    "state_root",
    "store_blob",
]
