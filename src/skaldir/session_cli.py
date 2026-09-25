"""``skaldir session …`` — the durable session history from the product CLI (MVP/40).

One call adds the group; the CLI owner needs it next to the other groups in
``build_parser()`` (see the hand-over block below) and nothing else — this module owns its
own ``cli_contract.COMMANDS`` entries and installs them from inside
:func:`register_cli`, so wiring the group cannot forget the contract
(``tests/test_cli_contract_dump.py`` stays green the moment the group is reachable).

.. code-block:: python

    # src/skaldir/cli.py, inside build_parser(), with the other PERSIST groups
    from .session_cli import register_cli as _register_session

    _register_session(sub)

Verbs:

* ``session list``    — sessions with title, message count, last activity and lineage.
* ``session show``    — one session: header, a bounded window of messages, the hash check
  and (with ``--events``) the append-only audit trail. Exit ``2`` when the recorded
  history hash does not match the stored messages.
* ``session search``  — bounded, ranked content search across sessions (FTS5 ``bm25``);
  the payload says which engine answered and whether the bound cut the result.
* ``session resume``  — reopen a session: the exact prior history, ``history.matches``,
  the next legal role and the exact ``append`` command to continue with.
* ``session export``  — ``json`` / ``jsonl`` / ``markdown`` / ``text``, to stdout or
  ``--out PATH`` (atomic write, sha256 reported).
* ``session new``     — create an empty session, or fork one with ``--parent``.
* ``session append``  — append one message. Two same-role messages in a row are refused
  with exit ``6`` and an error that names the offending message.
* ``session verify``  — recompute the history hash and compare (read-only, exit 2 on a
  mismatch).
* ``session fork``    — branch a session, copying its prefix into a linked child.
* ``session rotate``  — roll the newest ``--keep-last`` messages into a successor and
  archive the parent (nothing is deleted).
* ``session purge``   — ``archive`` / ``truncate`` / ``delete``; a **dry run unless
  ``--apply``** is given.
* ``session policy``  — the retention/delete/rotate policy as data.
* ``session status``  — store counts, schema, search engine and writer lease.

``python -m skaldir.session_cli …`` runs the same handlers with the same envelope, which
is how the kill/restart durability proof drives the group without the product CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .cli_shared import (
    blocked,
    dump,
    install_contract_entries,
    not_found,
    product_contract,
    runtime_dir,
    session_id,
    verification_failed,
)
from .runs_store import atomic_write_text, owner_string
from .session_store import (
    DEFAULT_KEEP_LAST,
    DEFAULT_MESSAGE_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    DEFAULT_SESSION_LIMIT,
    MAX_MESSAGE_LIMIT,
    PURGE_MODES,
    RETENTION_POLICY,
    ROLES,
    CorruptSessionStore,
    DuplicateMessage,
    RoleAlternationError,
    SessionError,
    SessionStore,
    UnknownRole,
    UnknownSession,
    WriterConflict,
    session_store_path,
)

_CONTRACT = product_contract()

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))
EXIT_CONFLICT = int(getattr(_CONTRACT, "EXIT_CONFLICT", 6))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))


# --------------------------------------------------------------------------- #
# envelope plumbing                                                            #
# --------------------------------------------------------------------------- #


def _hand_over(
    payload: dict,
    *,
    refs: tuple[dict, ...] = (),
    notes: tuple[str, ...] = (),
    follow_ups: tuple[tuple[str, str], ...] = (),
) -> None:
    """Give the payload, its evidence and the follow-ups to the one envelope."""
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(payload)
        for item in refs:
            _CONTRACT.add_evidence(
                str(item.get("ref", "")), str(item.get("kind", "code")), str(item.get("note", ""))
            )
        for text in notes:
            _CONTRACT.warn(str(text))
        for command, reason in follow_ups:
            _CONTRACT.next_action(str(command), str(reason))
    except Exception:  # noqa: BLE001 — an envelope problem must not break the verb
        return


def _refuse(code: int, message: str, *, hint: str = "", details: dict | None = None) -> int:
    """One refusal: through the contract's own code when it is there, by hand otherwise."""
    if _CONTRACT is not None:
        return int(_CONTRACT.fail(code, message, hint=hint, details=details or {}))
    print(f"session: {message}", file=sys.stderr)
    return int(code)


def _conflict(message: str, *, hint: str = "", details: dict | None = None) -> int:
    """Exit 6 — the request contradicts a rule of the store (an alternation violation)."""
    return _refuse(EXIT_CONFLICT, message, hint=hint, details=details)


def _usage(message: str, *, hint: str = "") -> int:
    if _CONTRACT is None:
        print(f"session: {message}", file=sys.stderr)
        return EXIT_USAGE
    return int(_CONTRACT.usage_error(message, hint=hint))


def _human(args: argparse.Namespace) -> bool:
    """Human text only when asked for; ``--json``/``--format json`` mean machine output."""
    if _CONTRACT is not None:
        try:
            return not _CONTRACT.json_requested(args)
        except Exception:  # noqa: BLE001 — fall through to the flag
            pass
    return not bool(getattr(args, "json", False))


def _standalone(payload: Any) -> None:
    """Bare-module fallback: the driver already carries the payload, so print only here."""
    if _CONTRACT is not None:
        return
    dump(payload)


# --------------------------------------------------------------------------- #
# opening the store                                                            #
# --------------------------------------------------------------------------- #


def _store_path(args: argparse.Namespace) -> Path:
    explicit = str(getattr(args, "store", "") or "")
    if explicit:
        return Path(explicit).expanduser()
    return session_store_path(runtime_dir(args))


def _lease_owner(args: argparse.Namespace) -> str:
    """The lease holder for this call: ``--owner`` when given, else this process."""
    given = str(getattr(args, "owner", "") or "").strip()
    return given if given else owner_string()


def _open(args: argparse.Namespace, *, create: bool = False) -> tuple[SessionStore | None, str]:
    """``(store, problem)`` — the store, or the reason it could not be opened.

    An absent file and an unreadable one are different answers: the first is ``missing``
    (nothing has been persisted), the second is ``corrupt`` and is never read as empty.
    """
    path = _store_path(args)
    if not create and not path.is_file():
        return None, "missing"
    try:
        return SessionStore(path, owner=_lease_owner(args)), ""
    except CorruptSessionStore as exc:
        return None, f"corrupt: {exc}"


def _nothing_persisted(args: argparse.Namespace, *, for_command: str = "") -> int:
    path = _store_path(args)
    _hand_over(
        {
            "store": str(path),
            "exists": False,
            "blocked_reason": f"no session store at {path}",
            "command": for_command,
        },
        refs=(
            {
                "ref": str(path),
                "kind": "file",
                "note": "sessions.db is absent: no message history has been persisted here",
            },
            {
                "ref": "src/skaldir/session_store.py",
                "kind": "code",
                "note": "the durable session/message store this command reads",
            },
        ),
        follow_ups=(
            ("skaldir session new --title '<title>' --json", "create the first session"),
        ),
    )
    return not_found(
        f"no session store at {path}",
        hint="create a session first: skaldir session new --title '<title>'",
        details={"store": str(path)},
    )


def _damaged(args: argparse.Namespace, problem: str) -> int:
    path = _store_path(args)
    _hand_over(
        {"store": str(path), "exists": True, "corrupt": True, "blocked_reason": problem},
        refs=(
            {
                "ref": str(path),
                "kind": "file",
                "note": "the file is there but is not a readable session store",
            },
            {
                "ref": "src/skaldir/session_store.py::CorruptSessionStore",
                "kind": "code",
                "note": "the refusal that produced this answer",
            },
        ),
        follow_ups=(
            (
                f"mv {path} {path}.corrupt && skaldir session new --json",
                "quarantine the damaged file and start clean — its bytes are never overwritten",
            ),
        ),
    )
    return verification_failed(
        f"{path} cannot be read as a session store",
        hint="quarantine the file and start a new session; the damaged bytes stay on disk",
        details={"store": str(path), "reason": problem},
    )


def _store_ref(path: Path, note: str) -> dict:
    """One evidence row for the store file, carrying the note this call produced."""
    return {"ref": str(path), "kind": "file", "note": str(note)}


# --------------------------------------------------------------------------- #
# session list                                                                 #
# --------------------------------------------------------------------------- #


def cmd_session_list(args: argparse.Namespace) -> int:
    """Read-only: the sessions with their counts, last activity and lineage."""
    store, problem = _open(args)
    if store is None:
        return _nothing_persisted(args, for_command="session list") if problem == "missing" \
            else _damaged(args, problem)
    try:
        limit = max(1, int(getattr(args, "limit", DEFAULT_SESSION_LIMIT)))
        records = store.sessions(
            include_archived=bool(getattr(args, "all", False)),
            limit=limit,
            order=str(getattr(args, "order", "recent") or "recent"),
        )
        counts = store.status()["counts"]
    finally:
        store.close()
    payload = {
        "store": str(_store_path(args)),
        "sessions": records,
        "count": len(records),
        "limit": limit,
        "include_archived": bool(getattr(args, "all", False)),
        "totals": counts,
        "truncated": len(records) >= limit,
    }
    _hand_over(
        payload,
        refs=(
            _store_ref(_store_path(args), f"{len(records)} session row(s) read (bounded)"),
        ),
        notes=(
            (f"the list is capped at {limit}; {counts['sessions']} session(s) exist in total",)
            if len(records) >= limit
            else ()
        ),
    )
    if _human(args):
        if not records:
            print("no sessions yet — skaldir session new --title '<title>'")
        for item in records:
            flag = " [archived]" if item["archived"] else ""
            parent = item["lineage"]["parent_session_id"]
            origin = f" <- {parent}" if parent else ""
            print(
                f"{item['session_id']}  {item['message_count']:>4} msg  "
                f"{item['title'] or '(untitled)'}{origin}{flag}"
            )
            print(
                f"      last {item['updated']:.0f} · head {item['head_role'] or '-'} · "
                f"next {item['next_role']} · tokens≈{item['token_total']}"
            )
    else:
        _standalone(payload)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# session show / verify                                                        #
# --------------------------------------------------------------------------- #


def _report_session_window(
    args: argparse.Namespace, session: Any, store: SessionStore
) -> tuple[dict, dict]:
    """The one shape ``show`` and ``resume`` share: a window plus the hash check."""
    limit = max(1, int(getattr(args, "limit", DEFAULT_MESSAGE_LIMIT)))
    if bool(getattr(args, "last", False)):
        messages = store.messages(session.session_id, limit=limit, tail=True)
    else:
        messages = store.messages(session.session_id, limit=limit)
    history = store.verify_history(session.session_id)
    with_body = not bool(getattr(args, "no_body", False))
    window = {
        "returned": len(messages),
        "omitted": max(0, int(session.message_count) - len(messages)),
        "limit": limit,
        "tail": bool(getattr(args, "last", False)),
        "complete": len(messages) >= int(session.message_count),
        "after_seq": messages[-1].seq if messages else 0,
    }
    payload = {
        "session": session.to_dict(),
        "messages": [item.to_dict(body=with_body) for item in messages],
        "window": window,
        "history": history,
        "next_role": session.next_role,
    }
    if bool(getattr(args, "events", False)):
        payload["events"] = store.events(session.session_id, limit=limit)
    return payload, history


def _print_window(payload: dict, *, store_path: Path) -> None:
    session = payload["session"]
    print(
        f"{session['session_id']}  {session['title'] or '(untitled)'}  "
        f"[{session['message_count']} msg, {session['archived'] and 'archived' or 'active'}]"
    )
    print(f"history sha256 {session['history_sha256']}")
    for item in payload["messages"]:
        body = item.get("content", "")
        preview = body if len(body) <= 96 else body[:93] + "..."
        print(f"  [{item['seq']:>4}] {item['role']:<9} {preview}")
    window = payload["window"]
    if window["omitted"]:
        print(f"  ... {window['omitted']} earlier message(s) not shown (use --last)")
    if payload.get("events") is not None:
        for event in payload["events"]:
            print(f"  event {event['kind']:<20} seq={event['seq']} {event['detail']}")


def cmd_session_show(args: argparse.Namespace) -> int:
    """One session as it stands — and its hash check, which may fail loudly (exit 2)."""
    store, problem = _open(args)
    if store is None:
        return _nothing_persisted(args, for_command="session show") if problem == "missing" \
            else _damaged(args, problem)
    choice = str(getattr(args, "session", "") or "")
    try:
        if not choice:
            records = store.sessions(include_archived=True, limit=1)
            if not records:
                return not_found(
                    f"no session in {_store_path(args)}",
                    hint="skaldir session new --title '<title>' --json",
                )
            choice = str(records[0]["session_id"])
        session = store.require_session(choice)
        payload, history = _report_session_window(args, session, store)
    except UnknownSession as exc:
        store.close()
        return not_found(str(exc), hint="skaldir session list --json names the sessions",
                         details={"session": choice})
    finally:
        store.close()
    payload["requested"] = choice
    _hand_over(
        payload,
        refs=(
            _store_ref(
                _store_path(args),
                f"{payload['window']['returned']} message(s) of {session.message_count} read; "
                f"history sha256 {history['stored_sha256'][:16]}",
            ),
        ),
        notes=(
            () if history["matches"] else
            ("the recorded history hash does not match the stored messages — the history "
             "changed under the store; see history.body_mismatch_seqs",)
        ),
        follow_ups=(
            (f"skaldir session export {session.session_id} --as jsonl --json",
             "export what is actually stored before deciding what to do"),
        )
        if not history["matches"]
        else (),
    )
    if _human(args):
        _print_window(payload, store_path=_store_path(args))
    else:
        _standalone(payload)
    if not history["matches"]:
        return verification_failed(
            f"the recorded history of {session.session_id} does not match the stored messages",
            hint="the store changed outside this API — export the rows before repairing anything",
            details=history,
        )
    return EXIT_OK


def cmd_session_verify(args: argparse.Namespace) -> int:
    """Recompute the history hash and compare it — nothing is written."""
    store, problem = _open(args)
    if store is None:
        return _nothing_persisted(args, for_command="session verify") if problem == "missing" \
            else _damaged(args, problem)
    choice = str(getattr(args, "session", "") or "")
    try:
        if not choice:
            records = store.sessions(include_archived=True, limit=DEFAULT_SESSION_LIMIT)
            if not records:
                return not_found(
                    f"no session in {_store_path(args)}",
                    hint="skaldir session new --title '<title>' --json",
                )
            checks = [store.verify_history(str(item["session_id"])) for item in records]
            payload = {
                "checked": len(checks),
                "sessions": checks,
                "all_match": all(item["matches"] for item in checks),
                "mismatched": [item["session_id"] for item in checks if not item["matches"]],
            }
        else:
            check = store.verify_history(choice)
            payload = {
                "checked": 1,
                "sessions": [check],
                "all_match": check["matches"],
                "mismatched": [] if check["matches"] else [check["session_id"]],
            }
    except UnknownSession as exc:
        store.close()
        return not_found(str(exc), hint="skaldir session list --json names the sessions",
                         details={"session": choice})
    finally:
        store.close()
    _hand_over(
        payload,
        refs=(
            {
                "ref": f"{payload['checked']} session(s) verified against their recorded sha256",
                "kind": "run",
                "note": f"all_match={payload['all_match']}",
            },
        ),
    )
    if _human(args):
        for item in payload["sessions"]:
            mark = "ok" if item["matches"] else "MISMATCH"
            print(
                f"{mark:<8} {item['session_id']}  {item['message_count']} msg  "
                f"{item['stored_sha256'][:16]}"
            )
            if not item["matches"]:
                print(f"         recomputed {item['recomputed_sha256'][:16]}  "
                      f"body mismatches at seq {item['body_mismatch_seqs']}")
    else:
        _standalone(payload)
    if not payload["all_match"]:
        return verification_failed(
            f"{len(payload['mismatched'])} session(s) do not match their recorded history",
            hint="the stored messages are the source of truth; export them before repairing",
            details=payload,
        )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# session search / resume / export                                             #
# --------------------------------------------------------------------------- #


def cmd_session_search(args: argparse.Namespace) -> int:
    """Bounded, ranked content search; the payload names the engine and the bound."""
    store, problem = _open(args)
    if store is None:
        return _nothing_persisted(args, for_command="session search") if problem == "missing" \
            else _damaged(args, problem)
    query = str(getattr(args, "query", "") or "")
    limit = max(1, int(getattr(args, "limit", DEFAULT_SEARCH_LIMIT)))
    only = session_id(args)
    try:
        if not query.strip():
            return _usage(
                "session search needs a query",
                hint="an empty query searches nothing: pass a word or a phrase",
            )
        result = store.search(
            query,
            limit=limit,
            session_id=only,
            roles=tuple(getattr(args, "role", None) or ()),
            include_archived=not bool(getattr(args, "no_archived", False)),
        )
    except (UnknownRole, SessionError) as exc:
        store.close()
        return _usage(str(exc), hint="roles are one of " + ", ".join(ROLES))
    finally:
        store.close()
    payload = {**result, "session_filter": only, "store": str(_store_path(args))}
    _hand_over(
        payload,
        refs=(
            _store_ref(
                _store_path(args),
                f"{result['returned']} hit(s) from the {result['engine']} index",
            ),
        ),
        notes=(
            ("the result was cut by the limit — raise --limit or narrow the query",)
            if result["bounded"]
            else ()
        ),
    )
    if _human(args):
        if not payload["hits"]:
            print(f"no message matches {query!r} ({result['engine']} index)")
        for hit in payload["hits"]:
            print(
                f"{hit['session_id']}  seq {hit['seq']}  {hit['role']:<9} "
                f"{hit['snippet']}"
            )
        print(f"{result['returned']} hit(s) · engine {result['engine']} · "
              f"sessions {', '.join(result['sessions']) or '-'}")
    else:
        _standalone(payload)
    return EXIT_OK


def cmd_session_resume(args: argparse.Namespace) -> int:
    """Reopen a session: the prior history, its hash, and the next legal role."""
    store, problem = _open(args)
    if store is None:
        return _nothing_persisted(args, for_command="session resume") if problem == "missing" \
            else _damaged(args, problem)
    choice = session_id(args)
    try:
        if not choice:
            records = store.sessions(include_archived=False, limit=1)
            if not records:
                return not_found(
                    f"no session to resume in {_store_path(args)}",
                    hint="skaldir session new --title '<title>' --json",
                )
            choice = str(records[0]["session_id"])
        payload = store.resume(
            choice, limit=max(1, int(getattr(args, "limit", DEFAULT_MESSAGE_LIMIT)))
        )
    except UnknownSession as exc:
        store.close()
        return not_found(str(exc), hint="skaldir session list --json names the sessions",
                         details={"session": choice})
    finally:
        store.close()
    history = payload["history"]
    payload["requested"] = choice
    _hand_over(
        payload,
        refs=(
            _store_ref(
                _store_path(args),
                f"resumed {choice}: {payload['window']['returned']} message(s), "
                f"history {history['stored_sha256'][:16]}",
            ),
            {
                "ref": history["hash_algorithm"],
                "kind": "code",
                "note": "how the history hash is recomputed on this resume",
            },
        ),
        follow_ups=(
            (payload["continuation"]["command"],
             "continue the conversation in the next legal role"),
        )
        if history["matches"]
        else (),
    )
    if _human(args):
        session = payload["session"]
        print(f"resumed {session['session_id']}  {session['title'] or '(untitled)'}")
        print(f"  messages {session['message_count']} · next role {payload['next_role']} · "
              f"next seq {payload['next_seq']}")
        print(f"  history {history['stored_sha256'][:16]} · matches {history['matches']}")
        for item in payload["messages"]:
            print(f"  [{item['seq']:>4}] {item['role']:<9} {item['content'][:80]}")
        print(f"  continue: {payload['continuation']['command']}")
    else:
        _standalone(payload)
    if not history["matches"]:
        return verification_failed(
            f"the history of {choice} does not match what the store recorded",
            hint="do not continue a history that changed: export it and inspect the mismatch",
            details=history,
        )
    return EXIT_OK


def cmd_session_export(args: argparse.Namespace) -> int:
    """Render a session (or a bounded window of it) and optionally write it out."""
    store, problem = _open(args)
    if store is None:
        return _nothing_persisted(args, for_command="session export") if problem == "missing" \
            else _damaged(args, problem)
    choice = session_id(args)
    try:
        if not choice:
            records = store.sessions(include_archived=True, limit=1)
            if not records:
                return not_found(
                    f"no session to export in {_store_path(args)}",
                    hint="skaldir session new --title '<title>' --json",
                )
            choice = str(records[0]["session_id"])
        exported = store.export(
            choice,
            as_format=str(getattr(args, "as_format", "jsonl") or "jsonl"),
            limit=max(1, int(getattr(args, "limit", MAX_MESSAGE_LIMIT))),
        )
    except UnknownSession as exc:
        store.close()
        return not_found(str(exc), hint="skaldir session list --json names the sessions",
                         details={"session": choice})
    except SessionError as exc:
        store.close()
        return _usage(str(exc), hint="--as is one of json, jsonl, markdown, text")
    finally:
        store.close()
    target = str(getattr(args, "out", "") or "")
    payload: dict = {
        "session_id": exported["session_id"],
        "format": exported["format"],
        "messages": exported["messages"],
        "bytes": exported["bytes"],
        "sha256": exported["sha256"],
        "history_sha256": exported["history_sha256"],
        "matches": exported["matches"],
        "out": target,
        "document": exported["document"],
    }
    refs: list[dict] = [
        _store_ref(
            _store_path(args),
            f"{exported['messages']} message(s) exported as {exported['format']}",
        ),
    ]
    if target:
        path = Path(target).expanduser()
        try:
            atomic_write_text(path, exported["document"])
        except OSError as exc:
            return _refuse(
                EXIT_INTERNAL,
                f"cannot write {path}: {type(exc).__name__}: {exc}",
                hint="pass a writable --out path, or drop --out to print the document",
            )
        payload["written"] = str(path)
        refs.append({"ref": str(path), "kind": "file", "note": "the exported document"})
    _hand_over(payload, refs=tuple(refs))
    if target:
        if _human(args):
            print(f"wrote {exported['messages']} message(s) to {target} "
                  f"({exported['bytes']} bytes, sha256 {exported['sha256'][:16]})")
    elif _human(args):
        print(exported["document"], end="")
    else:
        _standalone(payload)
    if not exported["matches"]:
        return verification_failed(
            f"the history of {exported['session_id']} does not match its recorded hash",
            hint="the export is what is stored; the mismatch is reported, not hidden",
            details={"history_sha256": exported["history_sha256"]},
        )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# mutating verbs                                                               #
# --------------------------------------------------------------------------- #


def _guard_write(args: argparse.Namespace, store: SessionStore) -> int | None:
    """Take the writer lease, or return the exit code of the refusal."""
    lease = store.acquire_writer(owner=_lease_owner(args))
    if lease["acquired"]:
        return None
    return blocked(
        f"another live process owns the writer lease of {_store_path(args)} "
        f"({lease['holder']})",
        hint="one writer only: let the running process finish, or take over after it exits",
        next_command="skaldir session status --json",
        reason=lease["reason"] or "the single-writer lease is held",
        details=lease,
    )


def _refusal_of(exc: Exception) -> tuple[int, str, str]:
    """``(exit_code, message, hint)`` for one store refusal — one table, no guessing."""
    if isinstance(exc, RoleAlternationError):
        return (
            EXIT_CONFLICT,
            str(exc),
            "alternate the roles: the next message must be a different role than the head",
        )
    if isinstance(exc, DuplicateMessage):
        return EXIT_CONFLICT, str(exc), "message ids are never reused — omit --message-id"
    if isinstance(exc, WriterConflict):
        return EXIT_BLOCKED, str(exc), "one writer only"
    if isinstance(exc, UnknownSession):
        return EXIT_NOT_FOUND, str(exc), "skaldir session list --json names the sessions"
    if isinstance(exc, UnknownRole):
        return EXIT_USAGE, str(exc), "roles are one of " + ", ".join(ROLES)
    if isinstance(exc, SessionError):
        return EXIT_CONFLICT, str(exc), "skaldir session policy --json shows the rules"
    return EXIT_INTERNAL, f"{type(exc).__name__}: {exc}", ""


def cmd_session_new(args: argparse.Namespace) -> int:
    """Create an empty session, or fork an existing one with ``--parent``."""
    path = _store_path(args)
    parent = str(getattr(args, "parent", "") or "")
    try:
        store = SessionStore(path, owner=_lease_owner(args))
    except CorruptSessionStore as exc:
        return _damaged(args, f"corrupt: {exc}")
    try:
        refusal = _guard_write(args, store)
        if refusal is not None:
            return refusal
        if parent:
            forked = store.fork(
                parent,
                at_message_id=str(getattr(args, "at_message_id", "") or ""),
                title=str(getattr(args, "title", "") or ""),
                copy_prefix=not bool(getattr(args, "no_copy", False)),
                session_id_value=str(getattr(args, "session_id_value", "") or ""),
                labels=tuple(getattr(args, "label", None) or ()),
                owner=_lease_owner(args),
            )
            payload = {
                "store": str(path),
                "created": forked["child"]["session_id"],
                "session": forked["child"],
                "parent": forked["parent"]["session_id"],
                "copied_messages": forked["copied_messages"],
                "lineage": forked["lineage"],
                "forked": True,
            }
        else:
            created = store.create_session(
                str(getattr(args, "title", "") or ""),
                session_id=str(getattr(args, "session_id_value", "") or ""),
                labels=tuple(getattr(args, "label", None) or ()),
                owner=_lease_owner(args),
            )
            payload = {
                "store": str(path),
                "created": created.session_id,
                "session": created.to_dict(),
                "parent": "",
                "copied_messages": 0,
                "lineage": store.lineage(created.session_id),
                "forked": False,
            }
    except (SessionError, WriterConflict) as exc:
        code, message, hint = _refusal_of(exc)
        return _refuse(code, message, hint=hint, details={"store": str(path)})
    finally:
        store.close()
    _hand_over(
        payload,
        refs=(
            _store_ref(path, f"session {payload['created']} written (schema v1)"),
            {
                "ref": "src/skaldir/session_store.py::SessionStore.create_session",
                "kind": "code",
                "note": "the one write path that creates a session row + its lineage",
            },
        ),
        follow_ups=(
            (
                f"skaldir session append {payload['created']} --role user"
                " --content '<text>' --json",
                "append the first message (a user turn opens a conversation)",
            ),
        ),
    )
    if _human(args):
        print(f"created {payload['created']}  {payload['session']['title'] or '(untitled)'}")
        if payload["forked"]:
            print(f"  forked from {payload['parent']} · copied {payload['copied_messages']} "
                  f"message(s) · depth {payload['session']['depth']}")
    else:
        _standalone(payload)
    return EXIT_OK


def cmd_session_append(args: argparse.Namespace) -> int:
    """Append **one** message: the only way a history grows (exit 6 on a same-role turn)."""
    path = _store_path(args)
    choice = session_id(args)
    body = str(getattr(args, "content", "") or "")
    source = str(getattr(args, "content_file", "") or "")
    if source:
        try:
            body = Path(source).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            return _usage(
                f"cannot read --content-file {source}: {type(exc).__name__}: {exc}",
                hint="pass a readable file, or the text with --content",
            )
    if not choice:
        return _usage(
            "session append needs a session",
            hint="pass --session <id>, or create one: skaldir session new --title '<title>'",
        )
    try:
        store = SessionStore(path, owner=_lease_owner(args))
    except CorruptSessionStore as exc:
        return _damaged(args, f"corrupt: {exc}")
    outcome: dict | None = None
    failure: int | None = None
    try:
        refusal = _guard_write(args, store)
        if refusal is not None:
            return refusal
        message = store.append(
            choice,
            str(getattr(args, "role", "") or ""),
            body,
            kind=str(getattr(args, "kind", "chat") or "chat"),
            refs=tuple(getattr(args, "ref", None) or ()),
            meta=json.loads(str(getattr(args, "meta", "") or "{}") or "{}"),
            message_id=str(getattr(args, "message_id", "") or ""),
            origin_message_id=str(getattr(args, "origin_message_id", "") or ""),
            owner=_lease_owner(args),
        )
        session = store.require_session(choice)
        verification = store.verify_history(choice)
        outcome = {
            "session_id": session.session_id,
            "message": message.to_dict(),
            "seq": message.seq,
            "role": message.role,
            "message_id": message.message_id,
            "token_estimate": message.token_estimate,
            "token_method": message.token_method,
            "message_count": session.message_count,
            "head_role": session.head_role,
            "next_role": session.next_role,
            "next_seq": int(session.last_seq) + 1,
            "history_sha256": session.history_sha256,
            "history_matches": verification["matches"],
            "store": str(path),
        }
    except json.JSONDecodeError as exc:
        failure = _usage(f"--meta is not JSON: {exc}", hint='--meta \'{"key": "value"}\'')
    except (SessionError, WriterConflict) as exc:
        code, message, hint = _refusal_of(exc)
        failure = _refuse(code, message, hint=hint, details={"session": choice})
    finally:
        store.close()
    if failure is not None:
        return failure
    payload = outcome or {}
    _hand_over(
        payload,
        refs=(
            _store_ref(
                path,
                f"message {payload['message_id']} appended at seq {payload['seq']} "
                f"({payload['role']})",
            ),
            {
                "ref": "src/skaldir/session_store.py::SessionStore.append",
                "kind": "code",
                "note": "the append enforces alternation before the insert and after it",
            },
        ),
        follow_ups=(
            (
                f"skaldir session append {payload['session_id']} --role {payload['next_role']} "
                "--content '<text>' --json",
                "the next message must be this role",
            ),
        ),
    )
    if _human(args):
        print(f"appended {payload['role']} #{payload['seq']} to {payload['session_id']} "
              f"({payload['token_estimate']} tokens≈, {payload['token_method']})")
        print(f"  next role {payload['next_role']} · history "
              f"{payload['history_sha256'][:16]}")
    else:
        _standalone(payload)
    return EXIT_OK


def cmd_session_fork(args: argparse.Namespace) -> int:
    """Branch a session into a linked child, optionally copying a prefix of it."""
    path = _store_path(args)
    choice = session_id(args)
    if not choice:
        return _usage(
            "session fork needs a session", hint="pass --session <id> (see session list)"
        )
    try:
        store = SessionStore(path, owner=_lease_owner(args))
    except CorruptSessionStore as exc:
        return _damaged(args, f"corrupt: {exc}")
    try:
        refusal = _guard_write(args, store)
        if refusal is not None:
            return refusal
        result = store.fork(
            choice,
            at_message_id=str(getattr(args, "at_message_id", "") or ""),
            title=str(getattr(args, "title", "") or ""),
            copy_prefix=not bool(getattr(args, "no_copy", False)),
            labels=tuple(getattr(args, "label", None) or ()),
            owner=_lease_owner(args),
        )
        payload = {"store": str(path), "session_id": choice, **result}
    except (SessionError, WriterConflict) as exc:
        code, message, hint = _refusal_of(exc)
        store.close()
        return _refuse(code, message, hint=hint, details={"session": choice})
    finally:
        store.close()
    _hand_over(
        payload,
        refs=(
            _store_ref(
                path,
                f"fork {payload['child']['session_id']} of {choice} "
                f"with {payload['copied_messages']} copied message(s)",
            ),
        ),
        follow_ups=(
            (
                f"skaldir session resume {payload['child']['session_id']} --json",
                "continue in the fork (its history is its own, with fresh message ids)",
            ),
        ),
    )
    if _human(args):
        print(f"forked {choice} -> {payload['child']['session_id']} "
              f"({payload['copied_messages']} message(s) copied, depth "
              f"{payload['child']['depth']})")
    else:
        _standalone(payload)
    return EXIT_OK


def cmd_session_rotate(args: argparse.Namespace) -> int:
    """Roll the newest messages into a successor session; the parent keeps every one."""
    path = _store_path(args)
    choice = session_id(args)
    if not choice:
        return _usage(
            "session rotate needs a session", hint="pass --session <id> (see session list)"
        )
    try:
        store = SessionStore(path, owner=_lease_owner(args))
    except CorruptSessionStore as exc:
        return _damaged(args, f"corrupt: {exc}")
    try:
        refusal = _guard_write(args, store)
        if refusal is not None:
            return refusal
        result = store.rotate(
            choice,
            keep_last=max(1, int(getattr(args, "keep_last", DEFAULT_KEEP_LAST))),
            title=str(getattr(args, "title", "") or ""),
            owner=_lease_owner(args),
        )
        payload = {"store": str(path), "session_id": choice, **result}
    except (SessionError, WriterConflict) as exc:
        code, message, hint = _refusal_of(exc)
        store.close()
        return _refuse(code, message, hint=hint, details={"session": choice})
    finally:
        store.close()
    if not payload["rotated"]:
        _hand_over(
            payload,
            refs=(_store_ref(path, f"no rotation: {payload['reason']}"),),
        )
        if _human(args):
            print(f"no rotation: {payload['reason']}")
        else:
            _standalone(payload)
        return EXIT_OK
    successor = payload["successor"]["session_id"]
    _hand_over(
        payload,
        refs=(
            _store_ref(
                path,
                f"rotated {choice} into {successor}: {payload['copied_messages']} message(s) "
                f"copied, parent archived with all {payload['parent_messages_kept']} kept",
            ),
        ),
        follow_ups=(
            (f"skaldir session resume {successor} --json", "continue in the successor session"),
        ),
    )
    if _human(args):
        print(f"rotated {choice} -> {successor} "
              f"(kept newest {payload['copied_messages']}; parent archived, nothing deleted)")
    else:
        _standalone(payload)
    return EXIT_OK


def cmd_session_purge(args: argparse.Namespace) -> int:
    """Archive / truncate / delete — a **dry run** unless ``--apply`` is given."""
    path = _store_path(args)
    choice = session_id(args)
    mode = str(getattr(args, "mode", "archive") or "archive")
    keep_last = max(1, int(getattr(args, "keep_last", DEFAULT_KEEP_LAST)))
    reason = str(getattr(args, "reason", "") or "")
    apply_now = bool(getattr(args, "apply", False))
    if not choice:
        return _usage("session purge needs a session", hint="pass --session <id>")
    if mode not in (*PURGE_MODES, "archive"):
        return _usage(
            f"unknown purge mode {mode!r}",
            hint="--mode is one of archive, " + ", ".join(PURGE_MODES),
        )
    try:
        store = SessionStore(path, owner=_lease_owner(args))
    except CorruptSessionStore as exc:
        return _damaged(args, f"corrupt: {exc}")
    try:
        if not apply_now:
            plan = store.purge_plan(choice, mode=mode, keep_last=keep_last)
            payload = {"store": str(path), **plan, "dry_run": True, "reason": reason}
        else:
            refusal = _guard_write(args, store)
            if refusal is not None:
                return refusal
            payload = {
                "store": str(path),
                **store.purge(
                    choice,
                    mode=mode,
                    keep_last=keep_last,
                    reason=reason,
                    dry_run=False,
                    owner=_lease_owner(args),
                ),
            }
    except (SessionError, WriterConflict) as exc:
        code, message, hint = _refusal_of(exc)
        store.close()
        return _refuse(code, message, hint=hint, details={"session": choice, "mode": mode})
    finally:
        store.close()
    if not apply_now:
        notes = (
            f"dry run: nothing was deleted ({payload['messages_removed']} message(s) would go)",
            "re-run with --apply to make it happen",
        )
    else:
        notes = (
            f"{payload['messages_removed']} message(s) removed ({mode})",
        ) if payload["messages_removed"] else ()
    _hand_over(
        payload,
        refs=(
            _store_ref(
                path,
                f"purge plan for {choice}: mode={mode} removes={payload['messages_removed']} "
                f"keeps={payload['messages_kept']} applied={apply_now}",
            ),
            {
                "ref": "src/skaldir/session_store.py::SessionStore.purge",
                "kind": "code",
                "note": "the per-session purge token the delete trigger requires",
            },
        ),
        notes=notes,
        follow_ups=(
            (f"skaldir session purge {choice} --mode {mode} --apply --json",
             "apply the purge that was just planned"),
        )
        if not apply_now
        else (),
    )
    if _human(args):
        verb = "removed" if apply_now else "would remove"
        print(f"{choice}: {mode} {verb} {payload['messages_removed']} message(s), "
              f"keep {payload['messages_kept']}")
        if payload.get("tombstone"):
            print(f"  tombstone: {payload['tombstone']['mode']} "
                  f"{payload['tombstone']['message_count']} message(s) at "
                  f"{payload['tombstone']['purged_at']:.0f}")
        if not apply_now:
            print("  (dry run — add --apply to delete)")
    else:
        _standalone(payload)
    return EXIT_OK


def cmd_session_archive(args: argparse.Namespace) -> int:
    """Archive or unarchive a session: read-only afterwards, nothing deleted."""
    path = _store_path(args)
    choice = session_id(args)
    if not choice:
        return _usage("session archive needs a session", hint="pass --session <id>")
    want = not bool(getattr(args, "unarchive", False))
    try:
        store = SessionStore(path, owner=_lease_owner(args))
    except CorruptSessionStore as exc:
        return _damaged(args, f"corrupt: {exc}")
    try:
        refusal = _guard_write(args, store)
        if refusal is not None:
            return refusal
        session = store.archive_session(
            choice, reason=str(getattr(args, "reason", "") or ""), archived=want
        )
        payload = {
            "store": str(path),
            "session_id": session.session_id,
            "archived": session.archived,
            "message_count": session.message_count,
            "session": session.to_dict(),
        }
    except (SessionError, WriterConflict) as exc:
        code, message, hint = _refusal_of(exc)
        store.close()
        return _refuse(code, message, hint=hint, details={"session": choice})
    finally:
        store.close()
    _hand_over(
        payload,
        refs=(
            _store_ref(
                path,
                f"{choice} {'archived' if want else 'unarchived'} "
                f"({session.message_count} message(s) kept)",
            ),
        ),
    )
    if _human(args):
        print(f"{choice} {'archived' if want else 'unarchived'} — "
              f"{session.message_count} message(s) kept")
    else:
        _standalone(payload)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# read-only policy / status                                                    #
# --------------------------------------------------------------------------- #


def cmd_session_policy(args: argparse.Namespace) -> int:
    """What is kept, what can be purged, and the four invariants — as data."""
    payload = {
        "retention": RETENTION_POLICY,
        "invariants": {
            "role_alternation": (
                "two messages of the same role in a row are refused (exit 6) in code and "
                "by the messages_roles_alternate trigger"
            ),
            "immutable_messages": (
                "UPDATE on messages aborts (messages_no_update): a past message is never "
                "rewritten"
            ),
            "append_only": (
                "DELETE needs the per-session purge token (messages_no_delete); "
                "message_events cannot be updated or deleted at all"
            ),
            "monotonic_sequence": (
                "seq must be MAX(seq)+1 per session (messages_seq_monotonic) and is unique "
                "per session"
            ),
        },
        "defaults": {
            "keep_last": DEFAULT_KEEP_LAST,
            "message_limit": DEFAULT_MESSAGE_LIMIT,
            "session_limit": DEFAULT_SESSION_LIMIT,
            "search_limit": DEFAULT_SEARCH_LIMIT,
            "max_message_limit": MAX_MESSAGE_LIMIT,
        },
        "roles": list(ROLES),
    }
    _hand_over(
        payload,
        refs=(
            {
                "ref": "src/skaldir/session_store.py::RETENTION_POLICY",
                "kind": "code",
                "note": "the policy this command prints, taken from the module constant",
            },
        ),
        notes=(
            "there is no TTL and no automatic purge: only an explicit purge of a named "
            "session removes a body",
        ),
    )
    if _human(args):
        retention = payload["retention"]
        print("kept:        " + "; ".join(retention["kept"][:2]))
        print("purgeable:   " + "; ".join(retention["purgeable"]))
        print("never purged:" + "; ".join(retention["never_purged"]))
        print("modes:       " + ", ".join(retention["modes"]))
        for name, text in payload["invariants"].items():
            print(f"  {name}: {text}")
    else:
        _standalone(payload)
    return EXIT_OK


def cmd_session_status(args: argparse.Namespace) -> int:
    """Read-only: counts, schema, search engine and the writer lease."""
    store, problem = _open(args)
    if store is None:
        return _nothing_persisted(args, for_command="session status") if problem == "missing" \
            else _damaged(args, problem)
    try:
        snapshot = store.status()
    finally:
        store.close()
    _hand_over(
        snapshot,
        refs=(
            _store_ref(
                _store_path(args),
                f"{snapshot['counts']['sessions']} session(s), "
                f"{snapshot['counts']['messages']} message(s), "
                f"{snapshot['counts']['events']} audit row(s)",
            ),
            {
                "ref": f"search engine: {snapshot['search']['engine']}",
                "kind": "code",
                "note": "FTS5 bm25 when available, otherwise a bounded LIKE scan",
            },
        ),
        notes=(
            ("the FTS index and the message table disagree — run a reindex",)
            if not snapshot["search"]["consistent"]
            else ()
        ),
    )
    if _human(args):
        counts = snapshot["counts"]
        print(f"store    {snapshot['store']} (schema v{snapshot['schema_version']})")
        print(f"writer   {snapshot['writer']['holder'] or '(none)'}")
        print(f"counts   {counts['sessions']} session(s) ({counts['archived']} archived) · "
              f"{counts['messages']} message(s) · {counts['purged_sessions']} purged")
        print(f"search   {snapshot['search']['engine']} · indexed "
              f"{snapshot['search']['indexed']}/{snapshot['search']['messages']}")
    else:
        _standalone(snapshot)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# the parsers                                                                  #
# --------------------------------------------------------------------------- #


def _entry(layer: str, evidence: tuple[dict, ...], description: str, **kwargs: Any) -> Any:
    """One ``cli_contract.CommandSpec`` for a ``session.*`` verb (its own helper shape)."""
    if _CONTRACT is None:
        return None
    return _CONTRACT.CommandSpec(
        layer=layer,
        evidence=evidence,
        description=description,
        **kwargs,
    )


def _code_ref(path: str, note: str) -> dict:
    return {"ref": path, "kind": "code", "note": note}


def _file_ref(path: str, note: str) -> dict:
    return {"ref": path, "kind": "file", "note": note}


_STORE_MODULE = "src/skaldir/session_store.py"
_CLI_MODULE = "src/skaldir/session_cli.py"

_OBJECT_SCHEMA: dict = {"type": "object", "additionalProperties": True}

CONTRACT_ENTRIES: dict[str, Any] = {
    "session.list": _entry(
        "PERSIST",
        (
            _code_ref(_STORE_MODULE, "sessions + messages schema this listing reads"),
            _code_ref("src/skaldir/session_cli.py::cmd_session_list", "the handler"),
        ),
        "list sessions: title, message count, last activity, lineage (bounded)",
        exit_codes=(0, 5, 7),
        data_schema={
            "type": "object",
            "properties": {
                "sessions": {"type": "array", "items": {"type": "object"}},
                "count": {"type": "integer"},
                "totals": {"type": "object"},
            },
            "required": ["sessions", "count"],
        },
    ),
    "session.show": _entry(
        "PERSIST",
        (
            _code_ref(_STORE_MODULE, "the session header and its message window"),
            _code_ref(
                "src/skaldir/session_store.py::SessionStore.verify_history",
                "the hash check that may fail (exit 2)",
            ),
        ),
        "one session: header, a bounded message window, the hash check, optional audit",
        exit_codes=(0, 2, 5, 7),
        data_schema={
            "type": "object",
            "properties": {
                "session": {"type": "object"},
                "messages": {"type": "array", "items": {"type": "object"}},
                "window": {"type": "object"},
                "history": {"type": "object"},
            },
            "required": ["session", "messages", "history"],
        },
        notes="exit 2 when the recorded history hash does not match the stored messages",
    ),
    "session.verify": _entry(
        "PERSIST",
        (
            _code_ref(
                "src/skaldir/session_store.py::SessionStore.verify_history",
                "recomputes the history hash and every body hash",
            ),
        ),
        "recompute the history hash and compare it with what the session recorded",
        exit_codes=(0, 2, 5, 7),
        data_schema={
            "type": "object",
            "properties": {
                "checked": {"type": "integer"},
                "sessions": {"type": "array", "items": {"type": "object"}},
                "all_match": {"type": "boolean"},
                "mismatched": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["checked", "sessions", "all_match"],
        },
        notes="read-only; exit 2 when any checked session does not match",
    ),
    "session.search": _entry(
        "PERSIST",
        (
            _code_ref(_STORE_MODULE, "messages_fts + bm25 over the message bodies"),
            _file_ref("sessions.db", "the FTS index lives in the same store file"),
        ),
        "search message content across sessions (bounded, ranked, FTS5)",
        exit_codes=(0, 1, 5, 7),
        data_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "engine": {"type": "string"},
                "hits": {"type": "array", "items": {"type": "object"}},
                "returned": {"type": "integer"},
                "bounded": {"type": "boolean"},
                "sessions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query", "engine", "hits", "returned"],
        },
        notes="keyword/token search (FTS5 unicode61), not semantic; an empty query exits 1",
    ),
    "session.resume": _entry(
        "PERSIST",
        (
            _code_ref(_STORE_MODULE, "the history the session was reopened from"),
            _code_ref(
                "src/skaldir/session_store.py::SessionStore.resume",
                "returns the window, the hash verdict and the next legal role",
            ),
        ),
        "reopen a session: prior history + hash check + the next legal role to continue with",
        exit_codes=(0, 2, 5, 7),
        data_schema={
            "type": "object",
            "properties": {
                "session": {"type": "object"},
                "messages": {"type": "array", "items": {"type": "object"}},
                "history": {"type": "object"},
                "next_role": {"type": "string"},
                "continuation": {"type": "object"},
            },
            "required": ["session", "messages", "history", "next_role"],
        },
        notes="exit 2 when the history does not match: a changed history is not continued",
    ),
    "session.export": _entry(
        "PERSIST",
        (
            _code_ref("src/skaldir/session_store.py::SessionStore.export", "the renderers"),
            _file_ref("--out PATH", "the atomic write of the exported document"),
        ),
        "export a session as json / jsonl / markdown / text (stdout or --out)",
        exit_codes=(0, 1, 2, 5, 7),
        mutates=True,
        data_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "format": {"type": "string"},
                "messages": {"type": "integer"},
                "sha256": {"type": "string"},
                "document": {"type": "string"},
                "written": {"type": "string"},
            },
            "required": ["session_id", "format", "document"],
        },
        notes="--as picks the format (--format is the CLI-wide flag); --out writes atomically",
    ),
    "session.new": _entry(
        "PERSIST",
        (
            _code_ref("src/skaldir/session_store.py::SessionStore.create_session", "insert"),
            _code_ref("src/skaldir/session_store.py::SessionStore.fork", "the --parent path"),
        ),
        "create an empty session (or fork one with --parent)",
        exit_codes=(0, 1, 3, 5, 6, 7),
        mutates=True,
        data_schema={
            "type": "object",
            "properties": {
                "created": {"type": "string"},
                "session": {"type": "object"},
                "lineage": {"type": "object"},
                "forked": {"type": "boolean"},
            },
            "required": ["created", "session"],
        },
    ),
    "session.append": _entry(
        "PERSIST",
        (
            _code_ref("src/skaldir/session_store.py::SessionStore.append", "the one write path"),
            _code_ref(
                "src/skaldir/session_store.py::RoleAlternationError",
                "the refusal a same-role turn produces (exit 6)",
            ),
        ),
        "append one message; two same-role turns in a row are refused with exit 6",
        exit_codes=(0, 1, 3, 5, 6, 7),
        mutates=True,
        data_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "message": {"type": "object"},
                "seq": {"type": "integer"},
                "next_role": {"type": "string"},
                "history_sha256": {"type": "string"},
            },
            "required": ["session_id", "message", "seq", "next_role"],
        },
        notes="strict alternation: the head's role cannot repeat (RoleAlternationError, exit 6)",
    ),
    "session.fork": _entry(
        "PERSIST",
        (
            _code_ref("src/skaldir/session_store.py::SessionStore.fork", "the fork + copy"),
            _code_ref("src/skaldir/session_store.py::SessionStore.lineage", "the link"),
        ),
        "branch a session into a linked child, copying its prefix when asked",
        exit_codes=(0, 1, 3, 5, 6, 7),
        mutates=True,
        data_schema={
            "type": "object",
            "properties": {
                "child": {"type": "object"},
                "parent": {"type": "object"},
                "copied_messages": {"type": "integer"},
                "lineage": {"type": "object"},
            },
            "required": ["child", "parent", "copied_messages"],
        },
    ),
    "session.rotate": _entry(
        "PERSIST",
        (
            _code_ref("src/skaldir/session_store.py::SessionStore.rotate", "rotate + archive"),
            _code_ref("src/skaldir/session_store.py::SessionStore.archive_session", "the parent"),
        ),
        "roll the newest --keep-last messages into a successor session (nothing deleted)",
        exit_codes=(0, 1, 3, 5, 6, 7),
        mutates=True,
        data_schema={
            "type": "object",
            "properties": {
                "rotated": {"type": "boolean"},
                "successor": {"type": "object"},
                "parent": {"type": "object"},
                "copied_messages": {"type": "integer"},
            },
            "required": ["rotated"],
        },
        notes="the non-destructive answer to a long session; rotated=false is still exit 0",
    ),
    "session.purge": _entry(
        "PERSIST",
        (
            _code_ref("src/skaldir/session_store.py::SessionStore.purge",
                      "the purge token + writes"),
            _code_ref(
                "src/skaldir/session_store.py::RETENTION_POLICY",
                "what this command may remove and what it never touches",
            ),
        ),
        "archive / truncate / delete a session's bodies — dry run unless --apply",
        exit_codes=(0, 1, 3, 5, 6, 7),
        mutates=True,
        data_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "messages_removed": {"type": "integer"},
                "messages_kept": {"type": "integer"},
                "tombstone": {"type": "object"},
            },
            "required": ["mode", "dry_run", "messages_removed", "messages_kept"],
        },
        notes="dry run by default (exit 0, nothing written); --apply performs the purge",
    ),
    "session.archive": _entry(
        "PERSIST",
        (_code_ref("src/skaldir/session_store.py::SessionStore.archive_session", "the flag"),),
        "archive or unarchive a session (read-only afterwards, nothing deleted)",
        exit_codes=(0, 1, 3, 5, 6, 7),
        mutates=True,
        data_schema=_OBJECT_SCHEMA,
    ),
    "session.policy": _entry(
        "PERSIST",
        (
            _code_ref(
                "src/skaldir/session_store.py::RETENTION_POLICY",
                "the policy constant this command prints",
            ),
        ),
        "the retention / delete / rotate policy and the four invariants, as data",
        exit_codes=(0, 7),
        data_schema={
            "type": "object",
            "properties": {
                "retention": {"type": "object"},
                "invariants": {"type": "object"},
                "defaults": {"type": "object"},
                "roles": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["retention", "invariants"],
        },
    ),
    "session.status": _entry(
        "PERSIST",
        (
            _file_ref("sessions.db", "the store this command counts"),
            _code_ref("src/skaldir/session_store.py::SessionStore.status", "the snapshot"),
        ),
        "read-only store snapshot: counts, schema, search engine, writer lease",
        exit_codes=(0, 5, 7),
        data_schema={
            "type": "object",
            "properties": {
                "store": {"type": "string"},
                "schema_version": {"type": "integer"},
                "counts": {"type": "object"},
                "search": {"type": "object"},
                "writer": {"type": "object"},
            },
            "required": ["store", "counts"],
        },
    ),
}


def _store_flags(parser: argparse.ArgumentParser, *, owner: bool = True) -> None:
    parser.add_argument(
        "--runtime", default="", help="runtime dir holding sessions.db (default: $SKALDIR_HOME)"
    )
    parser.add_argument("--store", default="", help="explicit sessions.db path (tests/CI)")
    if owner:
        parser.add_argument(
            "--owner", default="", help="lease owner string (default pid=<pid>:boot=<boot_id>)"
        )


def _select(parser: argparse.ArgumentParser, *, required: bool = False) -> None:
    parser.add_argument(
        "--session", default="", required=required, help="the session id to act on"
    )


def _build_verbs(sub: Any) -> Any:
    listed = sub.add_parser("list", help="list sessions (title, count, activity, lineage)")
    _store_flags(listed)
    listed.add_argument("--limit", type=int, default=DEFAULT_SESSION_LIMIT)
    listed.add_argument("--all", action="store_true", help="include archived sessions")
    listed.add_argument("--order", choices=("recent", "created"), default="recent")
    listed.set_defaults(func=cmd_session_list)

    shown = sub.add_parser("show", help="one session: header, window, hash check, audit")
    _store_flags(shown)
    _select(shown)
    shown.add_argument("--limit", type=int, default=DEFAULT_MESSAGE_LIMIT,
                       help="how many messages to return (bounded)")
    shown.add_argument("--last", action="store_true", help="return the newest window instead")
    shown.add_argument("--no-body", dest="no_body", action="store_true",
                       help="omit the message bodies (seq, role, hashes only)")
    shown.add_argument("--events", action="store_true", help="include the audit trail")
    shown.set_defaults(func=cmd_session_show)

    verified = sub.add_parser("verify", help="recompute the history hash and compare it")
    _store_flags(verified)
    _select(verified)
    verified.set_defaults(func=cmd_session_verify)

    searched = sub.add_parser("search", help="search message content across sessions")
    _store_flags(searched)
    _select(searched)
    searched.add_argument("query", help="the words to look for")
    searched.add_argument("--limit", type=int, default=DEFAULT_SEARCH_LIMIT)
    searched.add_argument("--role", action="append", default=[], help="restrict to a role")
    searched.add_argument("--no-archived", dest="no_archived", action="store_true",
                          help="skip archived sessions")
    searched.set_defaults(func=cmd_session_search)

    resumed = sub.add_parser("resume", help="reopen a session and continue in the next role")
    _store_flags(resumed)
    _select(resumed)
    resumed.add_argument("--limit", type=int, default=DEFAULT_MESSAGE_LIMIT)
    resumed.set_defaults(func=cmd_session_resume)

    exported = sub.add_parser("export", help="export a session as json/jsonl/markdown/text")
    _store_flags(exported)
    _select(exported)
    exported.add_argument(
        "--as", dest="as_format", choices=("json", "jsonl", "markdown", "text"), default="jsonl",
        help="the export format (--format is the CLI-wide flag)",
    )
    exported.add_argument("--limit", type=int, default=MAX_MESSAGE_LIMIT)
    exported.add_argument("--out", default="", help="write here instead of stdout (atomic)")
    exported.set_defaults(func=cmd_session_export)

    created = sub.add_parser("new", help="create an empty session, or fork with --parent")
    _store_flags(created)
    created.add_argument("--title", default="", help="a human label for the session")
    created.add_argument("--session-id", dest="session_id_value", default="",
                         help="use this id instead of a generated one")
    created.add_argument("--parent", default="", help="fork this session instead of creating")
    created.add_argument("--at-message-id", dest="at_message_id", default="",
                         help="fork point: copy the parent up to this message")
    created.add_argument("--no-copy", dest="no_copy", action="store_true",
                         help="fork without copying the parent's prefix")
    created.add_argument("--label", action="append", default=[], help="a label (repeatable)")
    created.set_defaults(func=cmd_session_new)

    appended = sub.add_parser("append", help="append one message (strict role alternation)")
    _store_flags(appended)
    _select(appended, required=True)
    appended.add_argument("--role", required=True, choices=ROLES)
    appended.add_argument("--content", default="", help="the message body")
    appended.add_argument("--content-file", dest="content_file", default="",
                          help="read the body from a file instead")
    appended.add_argument("--kind", default="chat", help="a message kind (chat, tool_result, …)")
    appended.add_argument("--ref", action="append", default=[],
                          help="an evidence/tool ref (repeatable)")
    appended.add_argument("--meta", default="", help="a JSON object carried with the message")
    appended.add_argument("--message-id", dest="message_id", default="",
                          help="use this id instead of a generated one")
    appended.add_argument("--origin-message-id", dest="origin_message_id", default="",
                          help="the message this one was copied from (provenance)")
    appended.set_defaults(func=cmd_session_append)

    forked = sub.add_parser("fork", help="branch a session into a linked child")
    _store_flags(forked)
    _select(forked)
    forked.add_argument("--at-message-id", dest="at_message_id", default="")
    forked.add_argument("--title", default="")
    forked.add_argument("--no-copy", dest="no_copy", action="store_true")
    forked.add_argument("--label", action="append", default=[])
    forked.set_defaults(func=cmd_session_fork)

    rotated = sub.add_parser("rotate", help="roll the newest messages into a successor")
    _store_flags(rotated)
    _select(rotated)
    rotated.add_argument("--keep-last", dest="keep_last", type=int, default=DEFAULT_KEEP_LAST)
    rotated.add_argument("--title", default="")
    rotated.set_defaults(func=cmd_session_rotate)

    purged = sub.add_parser("purge", help="archive/truncate/delete (dry run unless --apply)")
    _store_flags(purged)
    _select(purged)
    purged.add_argument("--mode", choices=("archive", *PURGE_MODES), default="archive")
    purged.add_argument("--keep-last", dest="keep_last", type=int, default=DEFAULT_KEEP_LAST)
    purged.add_argument("--reason", default="", help="recorded in the audit row / tombstone")
    purged.add_argument("--apply", action="store_true",
                        help="perform it; without this flag the command only plans")
    purged.set_defaults(func=cmd_session_purge)

    archived = sub.add_parser("archive", help="archive or unarchive a session")
    _store_flags(archived)
    _select(archived)
    archived.add_argument("--unarchive", action="store_true", help="make it writable again")
    archived.add_argument("--reason", default="")
    archived.set_defaults(func=cmd_session_archive)

    policy = sub.add_parser("policy", help="the retention / purge / rotate policy as data")
    policy.set_defaults(func=cmd_session_policy)

    status = sub.add_parser("status", help="read-only store snapshot")
    _store_flags(status)
    status.set_defaults(func=cmd_session_status)
    return sub


def build_session_parser(subparsers: Any) -> Any:
    """Add the ``session`` group to a CLI's ``subparsers`` handle and return it."""
    parser = subparsers.add_parser(
        "session",
        help="durable session history: append-only messages, resume, search, purge policy",
        description=(
            "The PERSIST layer's message store (sessions.db): one row per session with its "
            "lineage, one row per message with a unique id and a monotonic per-session "
            "sequence. Roles strictly alternate, a past message is never mutated and the "
            "history is append-only — enforced in code and by SQLite triggers. resume "
            "returns the prior history plus its hash verdict and the next legal role."
        ),
    )
    sub = parser.add_subparsers(dest="session_command")
    _build_verbs(sub)
    return parser


def register_cli(subparsers: Any) -> Any:
    """``register_cli(subparsers)`` adds the ``session`` group and its contract entries.

    Installing the entries here (not in ``cli.py``) means wiring the group cannot
    forget them: ``tests/test_cli_contract_dump.py`` stays green the moment the parser
    is reachable, and the CLI owner needs exactly one added line.
    """
    group = build_session_parser(subparsers)
    install_contract_entries(CONTRACT_ENTRIES, _CONTRACT)
    return group


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with the group (``python -m skaldir.session_cli …``/tests).

    The parser class is the contract's own when it is present, so a bad flag exits ``1``
    (``EXIT_USAGE``) here exactly like it does in the product CLI instead of argparse's
    default ``2``.
    """
    parser_class = (
        getattr(_CONTRACT, "ContractArgumentParser", argparse.ArgumentParser)
        if _CONTRACT is not None
        else argparse.ArgumentParser
    )
    parser = parser_class(
        prog="skaldir",
        description="PERSIST: durable session history — append-only messages, resume, search",
    )
    root = parser.add_subparsers(dest="command")
    register_cli(root)
    if _CONTRACT is not None:
        _CONTRACT.annotate(parser)
    return parser


def _json_in_argv(argv: list[str]) -> bool:
    """``--json`` in any accepted spelling, for a parse that failed before the handler."""
    for item in argv:
        text = str(item)
        if text in ("--json", "--format=json", "--format=jsonl"):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    """``skaldir session …`` standalone — the same handlers, the same envelope."""
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(arguments)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else EXIT_USAGE
        if code != EXIT_OK and _CONTRACT is not None and _json_in_argv(arguments):
            # one document on stdout even when argparse never reached a handler
            _CONTRACT.emit_json_line(_CONTRACT.usage_envelope(arguments, code))
        return EXIT_OK if not code else int(code)
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE
    if _CONTRACT is None:
        return int(handler(args) or 0)
    return int(_CONTRACT.run_command(args, handler))


if __name__ == "__main__":  # pragma: no cover — module entry point
    raise SystemExit(main())


__all__ = [
    "CONTRACT_ENTRIES",
    "build_parser",
    "build_session_parser",
    "cmd_session_append",
    "cmd_session_archive",
    "cmd_session_export",
    "cmd_session_fork",
    "cmd_session_list",
    "cmd_session_new",
    "cmd_session_policy",
    "cmd_session_purge",
    "cmd_session_resume",
    "cmd_session_rotate",
    "cmd_session_search",
    "cmd_session_show",
    "cmd_session_status",
    "cmd_session_verify",
    "main",
    "register_cli",
]
