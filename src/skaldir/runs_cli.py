"""``skaldir runs …`` / ``skaldir goal …`` — the PERSIST layer's CLI surface.

One call adds both groups to the product CLI; the CLI owner needs two lines in
``build_parser()`` (see the hand-over note in the module's docstring of
:mod:`skaldir.runs_store`) plus one ``COMMANDS`` entry per leaf in
``src/skaldir/cli_contract.py``. Nothing in this module edits ``cli.py``.

.. code-block:: python

    # src/skaldir/cli.py, inside build_parser(), right after
    #     sub = parser.add_subparsers(dest="command", required=False)
    from .runs_cli import register_cli as register_runs_cli

    register_runs_cli(sub)

Verbs:

* ``runs status``   — read-only: the store, the goal records, the operations
  (with their side-effect disposition and parked ones), the budget and the run
  supervisor's invariant snapshot. Exit ``5`` when ``runs.db`` does not exist
  yet: no store is not a success.
* ``runs start``    — create the goal record + criteria + budget rows + the first
  checkpoint. Exit ``6`` when the session already has an open goal (a goal is
  never silently re-created).
* ``runs resume``   — the R0-R3 resume protocol: writer lease, re-attach or
  CAS-takeover, classify every in-flight operation (``IDLE``/``REPEAT``/
  ``RESOLVED``/``UNKNOWN``), park what cannot be proven, demote stale evidence,
  hand back the cursor and the next action. Exit ``3`` (blocked) while a parked
  operation waits for ``operator:side_effect_verified``; exit ``2`` for a record
  that cannot be classified.
* ``runs checkpoint`` — the self-sufficient snapshot (file + index row + cursor).
* ``runs list``     — the agent run ids this runtime holds, newest first, read
  from ``<state>/runs/*.jsonl`` through ``agent_loop.list_runs``. An empty list
  is data (exit ``0``); nothing is created or written.
* ``goal show``     — the durable goal record: revision, state, decisions,
  criteria with their typed evidence refs, operations, budget, cursor, owners.
* ``goal criteria`` — the criteria table; with ``--verify`` it flips one from
  **evidence** (``--evidence-ref`` is mandatory, a stale revision or a foreign
  workspace fingerprint is refused, nothing is written).

Every verb reports through ``cli_contract``'s envelope when that module is
present (``set_data`` + ``add_evidence``), so nothing here can report success it
cannot back. ``python -m skaldir.runs_cli …`` runs the same handlers with the same
envelope, which is how the kill/restart durability proof drives it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .cli_shared import product_contract, runtime_dir
from .runs_store import (
    STATUS_VERIFIED,
    CorruptStore,
    EvidenceRequired,
    RunsStore,
    StaleEvidence,
    UnknownGoal,
    owner_string,
    run_store_path,
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


def _report(
    payload: Any, *, evidence: tuple[dict, ...] = (), warnings: tuple[str, ...] = (),
    next_actions: tuple[tuple[str, str], ...] = (),
) -> None:
    """Hand the payload, its evidence and the follow-ups to the shared envelope."""
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(payload)
        for item in evidence:
            _CONTRACT.add_evidence(
                str(item.get("ref", "")), str(item.get("kind", "code")), str(item.get("note", ""))
            )
        for text in warnings:
            _CONTRACT.warn(str(text))
        for command, reason in next_actions:
            _CONTRACT.next_action(str(command), str(reason))
    except Exception:  # noqa: BLE001 — an envelope problem must not break the verb
        return


def _fail(code: int, message: str, *, hint: str = "", details: dict | None = None) -> int:
    if _CONTRACT is None:
        print(f"runs: {message}", file=sys.stderr)
        return int(code)
    return int(_CONTRACT.fail(code, message, hint=hint, details=details or {}))


def _not_found(message: str, *, hint: str = "", details: dict | None = None) -> int:
    if _CONTRACT is None:
        return _fail(EXIT_NOT_FOUND, message)
    return int(_CONTRACT.not_found(message, hint=hint, details=details or {}))


def _verification_failed(message: str, *, hint: str = "", details: dict | None = None) -> int:
    if _CONTRACT is None:
        return _fail(EXIT_VERIFICATION_FAILED, message)
    return int(_CONTRACT.verification_failed(message, hint=hint, details=details or {}))


def _blocked(
    message: str, *, hint: str = "", next_command: str = "", reason: str = "",
    details: dict | None = None,
) -> int:
    if _CONTRACT is None:
        return _fail(EXIT_BLOCKED, message)
    return int(
        _CONTRACT.blocked(
            message, hint=hint, next_command=next_command, reason=reason, details=details or {}
        )
    )


def _usage(message: str, *, hint: str = "") -> int:
    if _CONTRACT is None:
        print(f"runs: {message}", file=sys.stderr)
        return EXIT_USAGE
    return int(_CONTRACT.usage_error(message, hint=hint))


def _text_mode(args: argparse.Namespace) -> bool:
    """Human text unless ``--json``/``--format json`` was asked for."""
    if _CONTRACT is not None:
        return not _CONTRACT.json_requested(args)
    return not bool(getattr(args, "json", False))


def _emit(payload: Any) -> None:
    """Standalone fallback only.

    With ``cli_contract`` present the handler's ``set_data`` payload travels in
    the one envelope, so printing again here would put a second JSON document on
    the stream (run_command moves a handler's stdout to stderr — noisy, not
    wrong, but still a second copy nobody asked for).
    """
    if _CONTRACT is not None:
        return
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


# --------------------------------------------------------------------------- #
# selectors                                                                    #
# --------------------------------------------------------------------------- #


def _store_file(args: argparse.Namespace) -> Path:
    explicit = str(getattr(args, "store", "") or "")
    if explicit:
        return Path(explicit).expanduser()
    return run_store_path(runtime_dir(args))


def _owner(args: argparse.Namespace) -> str:
    return str(getattr(args, "owner", "") or "") or owner_string()


def _open(
    args: argparse.Namespace, *, create: bool = False
) -> tuple[RunsStore | None, Path, str]:
    """``(store, path, problem)`` — ``problem`` is ``""`` when the store is usable.

    An absent store and an unreadable one are different answers: the first is
    ``not_found`` (nothing has been persisted yet), the second is a corrupt record
    (exit 2) that must never be read as an empty store.
    """
    path = _store_file(args)
    if not create and not path.is_file():
        return None, path, "missing"
    try:
        return RunsStore(path, owner=_owner(args)), path, ""
    except CorruptStore as exc:
        return None, path, str(exc)


def _parse_limits(values: list[str] | None) -> dict[str, int]:
    limits: dict[str, int] = {}
    for item in values or []:
        text = str(item)
        if "=" not in text:
            continue
        role, _, raw = text.partition("=")
        try:
            limits[role.strip()] = int(raw)
        except ValueError:
            continue
    return limits


def _criteria(args: argparse.Namespace) -> list[Any]:
    specs: list[Any] = [str(text) for text in (getattr(args, "criterion", None) or [])]
    path = str(getattr(args, "criteria_file", "") or "")
    if path:
        try:
            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenceRequired(f"cannot read --criteria-file {path}: {exc}") from exc
        if isinstance(loaded, list):
            specs.extend(loaded)
        elif isinstance(loaded, dict):
            specs.extend(loaded.get("criteria") or [])
    return specs


def _missing_store_payload(args: argparse.Namespace, path: Path) -> int:
    _report(
        {
            "store": str(path),
            "exists": False,
            "runtime_dir": str(runtime_dir(args)),
            "blocked_reason": (
                f"no run store at {path} — no goal has ever been started in this runtime"
            ),
        },
        evidence=(
            {
                "ref": str(path),
                "kind": "file",
                "note": "runs.db is absent: nothing has been persisted for a goal yet",
            },
            {
                "ref": "src/skaldir/runs_store.py",
                "kind": "code",
                "note": "the durable goal record/operation/ledger schema",
            },
        ),
        next_actions=(
            ("skaldir runs start --goal '<text>' --json", "create the first goal record"),
        ),
    )
    return _not_found(
        f"no run store at {path}",
        hint="start a goal first: skaldir runs start --goal '<text>'",
        details={"store": str(path)},
    )


def _corrupt_store_payload(args: argparse.Namespace, path: Path, problem: str) -> int:
    """Exit 2: the file is there but cannot be read as this store."""
    _report(
        {"store": str(path), "exists": True, "corrupt": True, "blocked_reason": problem},
        evidence=(
            {
                "ref": str(path),
                "kind": "file",
                "note": "runs.db exists but is not a readable store — never read as empty",
            },
            {
                "ref": "src/skaldir/runs_store.py::CorruptStore",
                "kind": "code",
                "note": "the refusal that produced this answer",
            },
        ),
        next_actions=(
            (
                f"mv {path} {path}.corrupt && skaldir runs start --goal '<text>' --json",
                "quarantine the damaged file and start clean — the bytes are never overwritten",
            ),
        ),
    )
    return _verification_failed(
        f"{path} is there but cannot be read as the run store",
        hint="quarantine the file and start a new goal; the damaged bytes stay on disk",
        details={"store": str(path), "reason": problem},
    )


def _store_evidence(path: Path, note: str) -> dict:
    return {"ref": str(path), "kind": "file", "note": note}


# --------------------------------------------------------------------------- #
# runs status                                                                  #
# --------------------------------------------------------------------------- #


def cmd_runs_status(args: argparse.Namespace) -> int:
    """Read-only snapshot of the durable store (never creates it)."""
    store, path, problem = _open(args)
    if store is None:
        if problem != "missing":
            return _corrupt_store_payload(args, path, problem)
        return _missing_store_payload(args, path)
    try:
        snapshot = store.status()
    finally:
        store.close()
    counts = snapshot["counts"]
    operations = snapshot["operations"]
    warnings: list[str] = []
    if operations["parked"]:
        warnings.append(
            f"{len(operations['parked'])} parked operation(s) wait for "
            f"{operations['parked'][0]['wake_source'] or 'operator confirmation'}"
        )
    if snapshot["runs"].get("violations"):
        warnings.append(
            f"{len(snapshot['runs']['violations'])} run(s) violate the lifecycle invariant"
        )
    _report(
        snapshot,
        evidence=(
            _store_evidence(
                path,
                f"{counts['goals']} goal(s), {counts['operations']} operation(s), "
                f"{counts['checkpoints']} checkpoint(s) read from the store",
            ),
            {
                "ref": "src/skaldir/run_supervisor.py",
                "kind": "code",
                "note": "RunStore/describe — the runs table this store shares the file with",
            },
        ),
        warnings=tuple(warnings),
        next_actions=(
            (("skaldir runs resume --json", "take ownership and continue the open goal"),)
            if counts["open_goals"]
            else (("skaldir runs start --goal '<text>' --json", "begin a goal"),)
        ),
    )
    if _text_mode(args):
        print(f"store:    {snapshot['store']} (schema v{snapshot['schema_version']})")
        print(f"writer:   {snapshot['writer']['holder'] or '(none)'}")
        print(
            f"goals:    {counts['goals']} ({counts['open_goals']} open) · "
            f"operations: {counts['operations']} · checkpoints: {counts['checkpoints']}"
        )
        for goal in snapshot["goals"]:
            print(f"  {goal['goal_id']}  {goal['state']}  rev {goal['revision']}  {goal['goal']}")
    else:
        _emit(snapshot)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# runs list                                                                     #
# --------------------------------------------------------------------------- #


def _state_dir(args: argparse.Namespace) -> Path:
    """Where the agent loop keeps its transcripts: ``--state-dir``, else ``<runtime>/agent``.

    The same rule the telemetry verbs use (``telemetry_cli._state_dir``), so the ids
    ``skaldir runs list`` prints are the ids those readers open.
    """
    explicit = str(getattr(args, "state_dir", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return Path(runtime_dir(args)) / "agent"


def cmd_runs_list(args: argparse.Namespace) -> int:
    """Newest-first run ids from the agent loop's run transcripts (read-only).

    Audit 63/V7: session/run ids used to be nameable only by reading a directory by
    hand. This verb reads the same store ``agent_loop.list_runs`` reads — it creates
    nothing, writes nothing, and an empty list is data with exit 0.
    """
    from .agent_loop import list_runs, runs_dir

    state_dir = _state_dir(args)
    limit = max(1, int(getattr(args, "limit", 20) or 20))
    directory = runs_dir(state_dir)
    rows = list_runs(state_dir, limit=limit)
    payload = {
        "state_dir": str(state_dir),
        "runs_dir": str(directory),
        "exists": directory.is_dir(),
        "count": len(rows),
        "limit": limit,
        "runs": rows,
    }
    _report(
        payload,
        evidence=(
            {
                "ref": str(directory),
                "kind": "file",
                "note": (
                    f"{len(rows)} run transcript(s), newest first"
                    if directory.is_dir()
                    else "the run directory does not exist — no run has been recorded here"
                ),
            },
            {
                "ref": "src/skaldir/agent_loop.py",
                "kind": "code",
                "note": "list_runs/read_transcript — the reader this verb reports through",
            },
        ),
        warnings=(
            ()
            if directory.is_dir()
            else (f"no run transcripts under {directory}",)
        ),
        next_actions=(
            ("skaldir voice status --json", "the session ids `voice --session` takes"),
        ),
    )
    if _text_mode(args):
        if not payload["runs"]:
            print("(no runs)")
        for row in payload["runs"]:
            print(f"{row['run_id']}  {row['status'] or '?'}  {row['task']}")
    else:
        _emit(payload)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# runs start                                                                   #
# --------------------------------------------------------------------------- #


def cmd_runs_start(args: argparse.Namespace) -> int:
    """Create the goal record, criteria, budget rows and the first checkpoint."""
    session = str(getattr(args, "session", "") or "cli")
    try:
        specs = _criteria(args)
    except EvidenceRequired as exc:
        return _usage(str(exc), hint="pass a JSON list of {text, blocking, verification_plan}")
    limits = _parse_limits(getattr(args, "budget", None))
    path = _store_file(args)
    store = RunsStore(path, owner=_owner(args))
    try:
        writer = store.acquire_writer(owner=_owner(args))
        if not writer["acquired"]:
            return _blocked(
                f"another live process owns the writer lease of {path} ({writer['holder']})",
                hint="one writer only: let the running process finish, or take over after it dies",
                next_command="skaldir runs status --json",
                reason=writer["reason"] or "single-writer lease is held",
                details={"writer": writer},
            )
        existing = store.goal_for_session(session)
        if existing is not None and not existing.terminal:
            return _fail(
                EXIT_CONFLICT,
                f"session {session!r} already has the open goal {existing.goal_id} "
                f"({existing.state}, revision {existing.revision})",
                hint="resume it, or start this one under a different --session",
                details={"goal_id": existing.goal_id, "state": existing.state},
            )
        goal = store.start_goal(
            str(getattr(args, "goal", "") or ""),
            session_id=session,
            criteria=specs,
            task_base=str(getattr(args, "task_base", "") or ""),
            workspace_fingerprint=str(getattr(args, "workspace_fingerprint", "") or ""),
            limits=limits or None,
            owner=_owner(args),
        )
        criteria = store.criteria(goal.goal_id)
        checkpoint = store.latest_checkpoint(goal.goal_id)
        budget = store.budget_state(goal.goal_id)
    finally:
        store.close()
    payload = {
        "goal_id": goal.goal_id,
        "session_id": goal.session_id,
        "goal": goal.goal_text,
        "revision": goal.revision,
        "state": goal.state,
        "task_base": goal.task_base,
        "criteria": [item.to_dict() for item in criteria],
        "checkpoint": checkpoint,
        "budget": budget,
        "store": str(path),
    }
    _report(
        payload,
        evidence=(
            _store_evidence(path, f"goal {goal.goal_id} revision 1 written to runs.db"),
            {
                "ref": str(checkpoint.get("path") if checkpoint else path),
                "kind": "file",
                "note": "the first checkpoint: the resume cursor exists before any work",
            },
        ),
        next_actions=(
            ("skaldir runs resume --json", "take ownership and start the first criterion"),
        ),
    )
    if _text_mode(args):
        print(f"goal:     {goal.goal_id} (session {goal.session_id}, revision {goal.revision})")
        print(f"store:    {path}")
        for item in criteria:
            print(f"  {item.criterion_id}  {item.text}  [{item.status}]")
    else:
        _emit(payload)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# runs resume                                                                  #
# --------------------------------------------------------------------------- #


def cmd_runs_resume(args: argparse.Namespace) -> int:
    """R0-R3: take ownership, classify the in-flight work, hand back the cursor."""
    store, path, problem = _open(args, create=False)
    if store is None:
        if problem != "missing":
            return _corrupt_store_payload(args, path, problem)
        return _missing_store_payload(args, path)
    account = None
    if bool(getattr(args, "restore_budget", False)):
        from .budget import BudgetAccount

        goal = store.resolve_goal(
            goal_id=str(getattr(args, "goal_id", "") or ""),
            session_id=str(getattr(args, "session", "") or ""),
        )
        account = BudgetAccount(goal_id=goal.goal_id if goal else "unknown")
    try:
        try:
            payload = store.resume(
                str(getattr(args, "goal_id", "") or ""),
                session_id=str(getattr(args, "session", "") or ""),
                owner=_owner(args),
                takeover=not bool(getattr(args, "no_takeover", False)),
                adopt_host=not bool(getattr(args, "no_adopt_host", False)),
                budget_account=account,
            )
        except UnknownGoal as exc:
            return _not_found(
                str(exc),
                hint="skaldir runs status --json lists the goal records",
                details={"store": str(path)},
            )
    finally:
        store.close()

    evidence: list[dict] = [
        _store_evidence(
            path,
            f"goal {payload['goal_id']} revision {payload.get('revision', 0)} read from runs.db",
        )
    ]
    if payload.get("checkpoint"):
        evidence.append(
            {
                "ref": str(payload["checkpoint"]["path"]),
                "kind": "file",
                "note": (
                    f"checkpoint seq {payload['checkpoint']['seq']} "
                    f"(sha256 {'verified' if payload['checkpoint']['sha256_ok'] else 'MISMATCH'})"
                ),
            }
        )
    evidence.append(
        {
            "ref": "src/skaldir/runs_store.py::RunsStore.resume",
            "kind": "code",
            "note": (
                "R0-R3: writer lease, re-attach/CAS takeover, IDLE/REPEAT/RESOLVED/UNKNOWN "
                "classification, park instead of retry"
            ),
        }
    )
    warnings = list(payload.get("warnings") or [])
    wrote = payload.get("wrote") or {}
    if wrote.get("parked"):
        wake = ""
        if payload.get("parked_operations"):
            wake = f" — confirm with {payload['parked_operations'][0]['wake_source']}"
        warnings.append(
            "parked without retry: " + ", ".join(str(item) for item in wrote["parked"]) + wake
        )
    _report(
        payload,
        evidence=tuple(evidence),
        warnings=tuple(warnings),
        next_actions=(
            (
                f"skaldir goal show --goal-id {payload['goal_id']} --json",
                "the full record this cursor belongs to",
            ),
        ),
    )
    if _text_mode(args):
        print(
            f"goal:     {payload['goal_id']} revision {payload.get('revision')} "
            f"state {payload.get('state')}"
        )
        print(f"owner:    {payload['owners']['action']} ({payload['owners']['holder']})")
        print(f"next:     {payload.get('next_action')}")
        print(f"open:     {', '.join(payload.get('open_criteria') or []) or '(none)'}")
        for item in payload["parked_operations"]:
            print(f"  PARKED {item['op_id']} {item['kind']} -> {item['wake_source']}")
    else:
        _emit(payload)

    if payload.get("corrupt"):
        return _verification_failed(
            f"the goal record {payload['goal_id']} cannot be classified",
            hint="inspect runs.db; the record is never resumed silently",
            details={"reason": payload.get("corrupt_reason", "")},
        )
    if not payload.get("authoritative", True):
        return _blocked(
            f"another live process owns the store's writer lease "
            f"({(payload.get('wrote') or {}).get('writer', {}).get('holder')})",
            hint="this run read and classified the record only — one writer at a time",
            next_command="skaldir runs status --json",
            reason="single-writer lease is held by a live process",
            details={"writer": (payload.get("wrote") or {}).get("writer", {})},
        )
    if not payload.get("resumable", True):
        return _blocked(
            f"goal {payload['goal_id']} is {payload.get('state')} — nothing to resume"
            if payload.get("terminal")
            else payload.get("blocked_reason") or "the goal cannot be resumed yet",
            hint="a parked operation needs an explicit operator decision, never a retry",
            next_command="skaldir runs status --json",
            reason=payload.get("blocked_reason") or "operator decision required",
            details={
                "parked_operations": [
                    item["op_id"] for item in payload.get("parked_operations") or []
                ]
            },
        )
    return EXIT_OK


# --------------------------------------------------------------------------- #
# runs checkpoint                                                              #
# --------------------------------------------------------------------------- #


def cmd_runs_checkpoint(args: argparse.Namespace) -> int:
    """Write the self-sufficient snapshot (payload file + index row + cursor)."""
    store, path, problem = _open(args, create=False)
    if store is None:
        if problem != "missing":
            return _corrupt_store_payload(args, path, problem)
        return _missing_store_payload(args, path)
    try:
        goal = store.resolve_goal(
            goal_id=str(getattr(args, "goal_id", "") or ""),
            session_id=str(getattr(args, "session", "") or ""),
        )
        if goal is None:
            return _not_found(
                f"no goal record in {path}",
                hint="skaldir runs start --goal '<text>' --json",
                details={"store": str(path)},
            )
        last_event_seq = getattr(args, "last_event_seq", None)
        if last_event_seq is None and bool(getattr(args, "scan_events", False)):
            last_event_seq = store.max_event_seq()
        checkpoint = store.write_checkpoint(
            goal.goal_id,
            next_action=str(getattr(args, "next_action", "") or ""),
            last_event_seq=last_event_seq,
            directory=str(getattr(args, "directory", "") or "") or None,
        )
        cursor = store.cursor(goal.goal_id)
    finally:
        store.close()
    warnings = (
        ("the checkpoint payload exceeds the 4 kB self-sufficiency budget",)
        if checkpoint["oversize"]
        else ()
    )
    _report(
        {"goal_id": goal.goal_id, "cursor": cursor, **checkpoint},
        evidence=(
            {
                "ref": str(checkpoint["path"]),
                "kind": "file",
                "note": f"checkpoint seq {checkpoint['seq']} sha256 {checkpoint['sha256'][:16]}…",
            },
            _store_evidence(path, "index row + resume cursor written in the same store"),
        ),
        warnings=warnings,
        next_actions=(
            ("skaldir runs resume --json", "resume from this checkpoint after a restart"),
        ),
    )
    if _text_mode(args):
        print(f"checkpoint: seq {checkpoint['seq']} -> {checkpoint['path']}")
        print(f"cursor:     event seq {cursor['last_event_seq']} · {cursor['next_action']}")
    else:
        _emit({"goal_id": goal.goal_id, "cursor": cursor, **checkpoint})
    return EXIT_OK


# --------------------------------------------------------------------------- #
# goal show / goal criteria                                                    #
# --------------------------------------------------------------------------- #


def cmd_goal_show(args: argparse.Namespace) -> int:
    """The durable goal record: revision, criteria, evidence, budget, cursor, owners."""
    store, path, problem = _open(args, create=False)
    if store is None:
        if problem != "missing":
            return _corrupt_store_payload(args, path, problem)
        return _missing_store_payload(args, path)
    try:
        if bool(getattr(args, "all", False)):
            payload = {"store": str(path), "goals": [_goal_payload(store, item)
                                                     for item in store.goals()]}
            evidence = (
                _store_evidence(path, f"{len(payload['goals'])} goal record(s) read"),
            )
        else:
            goal = store.resolve_goal(
                goal_id=str(getattr(args, "goal_id", "") or ""),
                session_id=str(getattr(args, "session", "") or ""),
            )
            if goal is None:
                return _not_found(
                    f"no goal record in {path}",
                    hint="skaldir runs status --json lists what exists",
                    details={"store": str(path)},
                )
            payload = {"store": str(path), **_goal_payload(store, goal)}
            evidence = (
                _store_evidence(path, f"goal {goal.goal_id} revision {goal.revision}"),
                {
                    "ref": "src/skaldir/runs_store.py::RunsStore.criteria",
                    "kind": "code",
                    "note": "criteria carry typed evidence refs, never inlined prose",
                },
            )
    finally:
        store.close()
    _report(payload, evidence=evidence)
    if _text_mode(args):
        goals = payload.get("goals") or [payload]
        for item in goals:
            print(f"goal:     {item['goal_id']} revision {item['revision']} state {item['state']}")
            print(f"  task_base: {item.get('task_base') or '(unset)'}")
            for row in item.get("criteria") or []:
                print(f"  {row['criterion_id']}  [{row['status']}] {row['text']}")
            for row in item.get("operations") or []:
                print(f"  {row['op_id']} [{row['state']}] {row['kind']} -> {row['target']}")
    else:
        _emit(payload)
    return EXIT_OK


def cmd_goal_criteria(args: argparse.Namespace) -> int:
    """List the criteria; with ``--verify`` flip one from evidence (never from a flag)."""
    store, path, problem = _open(args, create=False)
    if store is None:
        if problem != "missing":
            return _corrupt_store_payload(args, path, problem)
        return _missing_store_payload(args, path)
    try:
        goal = store.resolve_goal(
            goal_id=str(getattr(args, "goal_id", "") or ""),
            session_id=str(getattr(args, "session", "") or ""),
        )
        if goal is None:
            return _not_found(
                f"no goal record in {path}",
                hint="skaldir runs start --goal '<text>' --json",
                details={"store": str(path)},
            )
        verify_id = str(getattr(args, "verify", "") or "")
        outcome: dict | None = None
        if verify_id:
            refs = [str(item) for item in (getattr(args, "evidence_ref", None) or [])]
            if not refs:
                return _usage(
                    "verifying a criterion needs --evidence-ref",
                    hint=(
                        "a status without evidence is a claim: pass events:, run: or receipt: refs"
                    ),
                )
            exit_code = int(getattr(args, "exit_code", 0))
            try:
                outcome = store.record_criterion_outcome(
                    goal.goal_id,
                    verify_id,
                    outcome=STATUS_VERIFIED if exit_code == 0 else "failed",
                    evidence_refs=refs,
                    check_id=str(getattr(args, "check_id", "") or ""),
                    cmd=str(getattr(args, "cmd", "") or ""),
                    exit_code=exit_code,
                    artifact=str(getattr(args, "artifact", "") or ""),
                    workspace=str(getattr(args, "workspace", "") or goal.task_base),
                    revision=(
                        int(args.revision)
                        if getattr(args, "revision", None) is not None
                        else None
                    ),
                    workspace_fingerprint=(
                        str(args.workspace_fingerprint)
                        if getattr(args, "workspace_fingerprint", None) is not None
                        else None
                    ),
                )
            except StaleEvidence as exc:
                return _verification_failed(
                    str(exc),
                    hint="re-run the check under the current revision and workspace",
                    details={"goal_id": goal.goal_id, "criterion_id": verify_id},
                )
            except EvidenceRequired as exc:
                return _usage(str(exc), hint="pass at least one --evidence-ref")
            except UnknownGoal as exc:
                return _not_found(str(exc), hint="skaldir goal show --json lists the criteria")
        criteria = store.criteria(goal.goal_id)
        cursor = store.cursor(goal.goal_id)
    finally:
        store.close()
    payload = {
        "goal_id": goal.goal_id,
        "revision": goal.revision,
        "state": goal.state,
        "criteria": [item.to_dict() for item in criteria],
        "open_criteria": [item.criterion_id for item in criteria
                          if item.status != STATUS_VERIFIED],
        "cursor": cursor,
        "outcome": outcome,
    }
    evidence: list[dict] = [
        _store_evidence(path, f"{len(criteria)} criterion/criteria of {goal.goal_id}"),
    ]
    if outcome:
        evidence.extend(
            {"ref": str(ref), "kind": "test", "note": f"evidence for {outcome['criterion_id']}"}
            for ref in outcome["evidence_refs"]
        )
    _report(payload, evidence=tuple(evidence))
    if _text_mode(args):
        for item in criteria:
            mark = {"verified": "+", "failed": "x"}.get(item.status, " ")
            plan = item.verification_plan or "no plan"
            print(f"[{mark}] {item.criterion_id} {item.text} ({plan})")
            for ref in item.evidence_refs:
                print(f"      {ref['ref']}  [{ref['kind']}]")
    else:
        _emit(payload)
    if outcome and outcome["status"] != STATUS_VERIFIED:
        return _verification_failed(
            f"criterion {outcome['criterion_id']} failed its check "
            f"({outcome.get('check_id') or 'unnamed check'}, exit {outcome.get('exit_code')})",
            hint="keep working; only a passing check may verify a criterion",
            details=outcome,
        )
    return EXIT_OK


def _goal_payload(store: RunsStore, goal: Any) -> dict:
    criteria = store.criteria(goal.goal_id)
    operations = store.operations(goal.goal_id)
    checkpoint = store.latest_checkpoint(goal.goal_id)
    return {
        "goal_id": goal.goal_id,
        "session_id": goal.session_id,
        "revision": goal.revision,
        "state": goal.state,
        "terminal": goal.terminal,
        "goal": goal.goal_text,
        "task_base": goal.task_base,
        "workspace_fingerprint": goal.workspace_fingerprint,
        "created": goal.created,
        "updated": goal.updated,
        "revision_log": goal.revision_log,
        "decisions": goal.decisions,
        "last_failure": goal.last_failure,
        "corrupt": goal.corrupt,
        "criteria": [item.to_dict() for item in criteria],
        "open_criteria": [item.criterion_id for item in criteria
                          if item.status != STATUS_VERIFIED],
        "operations": [item.to_dict() for item in operations],
        "parked_operations": [item.op_id for item in operations if item.parked],
        "checkpoint": checkpoint,
        "cursor": store.cursor(goal.goal_id),
        "budget": store.budget_state(goal.goal_id),
        "owners": {
            "holder": goal.lease_owner,
            "lease_expires_at": goal.lease_expires_at,
            "heartbeat_at": goal.heartbeat_at,
        },
    }


# --------------------------------------------------------------------------- #
# the parsers                                                                  #
# --------------------------------------------------------------------------- #


def _add_store_flags(parser: argparse.ArgumentParser, *, owner: bool = True) -> None:
    parser.add_argument(
        "--runtime", default="", help="runtime dir holding runs.db (default: $SKALDIR_HOME/godsv)"
    )
    parser.add_argument("--store", default="", help="explicit runs.db path (tests/CI)")
    if owner:
        parser.add_argument(
            "--owner", default="", help="lease owner string (default pid=<pid>:boot=<boot_id>)"
        )


def _add_selector_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--goal-id", dest="goal_id", default="", help="the goal record to use")
    parser.add_argument(
        "--session", default="", help="pick the session's latest open goal instead of an id"
    )


def _add_runs_verbs(sub: Any) -> Any:
    p_status = sub.add_parser("status", help="read-only snapshot of the durable store")
    _add_store_flags(p_status)
    p_status.set_defaults(func=cmd_runs_status)

    p_list = sub.add_parser(
        "list", help="the agent run ids this runtime holds, newest first"
    )
    p_list.add_argument("--runtime", default="", help="runtime dir (default: the registry's)")
    p_list.add_argument(
        "--state-dir", dest="state_dir", default="",
        help="directory holding runs/ (default: <runtime>/agent)",
    )
    p_list.add_argument("--limit", type=int, default=20, help="how many runs (newest first)")
    p_list.set_defaults(func=cmd_runs_list)

    p_start = sub.add_parser(
        "start", help="create the goal record (criteria + budget + checkpoint)"
    )
    _add_store_flags(p_start)
    p_start.add_argument("--goal", required=True, help="the owner's goal text")
    p_start.add_argument("--session", default="cli", help="session id the goal belongs to")
    p_start.add_argument(
        "--criterion", action="append", default=[], help="acceptance criterion (repeatable)"
    )
    p_start.add_argument(
        "--criteria-file",
        dest="criteria_file",
        default="",
        help="JSON file with a list of {text, blocking, verification_plan}",
    )
    p_start.add_argument("--task-base", dest="task_base", default="", help="workspace root")
    p_start.add_argument(
        "--workspace-fingerprint",
        dest="workspace_fingerprint",
        default="",
        help="fingerprint evidence must match to certify a criterion",
    )
    p_start.add_argument(
        "--budget", action="append", default=[], help="role=limit_tokens (repeatable)"
    )
    p_start.set_defaults(func=cmd_runs_start)

    p_resume = sub.add_parser("resume", help="R0-R3: take ownership and classify in-flight work")
    _add_store_flags(p_resume)
    _add_selector_flags(p_resume)
    p_resume.add_argument(
        "--no-takeover",
        dest="no_takeover",
        action="store_true",
        help="never take over an expired/dead lease (report only)",
    )
    p_resume.add_argument(
        "--no-adopt-host",
        dest="no_adopt_host",
        action="store_true",
        help="do not adopt orphaned host turn leases",
    )
    p_resume.add_argument(
        "--restore-budget",
        dest="restore_budget",
        action="store_true",
        help="also replay the durable ledger into a live budget.BudgetAccount",
    )
    p_resume.set_defaults(func=cmd_runs_resume)

    p_checkpoint = sub.add_parser("checkpoint", help="write the self-sufficient snapshot")
    _add_store_flags(p_checkpoint)
    _add_selector_flags(p_checkpoint)
    p_checkpoint.add_argument(
        "--next-action", dest="next_action", default="", help="imperative first step after resume"
    )
    p_checkpoint.add_argument(
        "--last-event-seq", dest="last_event_seq", type=int, default=None,
        help="highest committed event seq this goal consumed",
    )
    p_checkpoint.add_argument(
        "--scan-events",
        dest="scan_events",
        action="store_true",
        help="take the seq from events.db (MAX(seq)) when --last-event-seq is not given",
    )
    p_checkpoint.add_argument("--directory", default="", help="override the payload directory")
    p_checkpoint.set_defaults(func=cmd_runs_checkpoint)
    return sub


def _add_goal_verbs(sub: Any) -> Any:
    p_show = sub.add_parser("show", help="the durable goal record")
    _add_store_flags(p_show)
    _add_selector_flags(p_show)
    p_show.add_argument("--all", action="store_true", help="every goal record in the store")
    p_show.set_defaults(func=cmd_goal_show)

    p_criteria = sub.add_parser("criteria", help="the criteria table (optionally verify one)")
    _add_store_flags(p_criteria)
    _add_selector_flags(p_criteria)
    p_criteria.add_argument("--verify", default="", help="criterion id to record an outcome for")
    p_criteria.add_argument(
        "--evidence-ref", dest="evidence_ref", action="append", default=[],
        help="evidence ref (events:, run:, receipt:, test:, file:) — mandatory with --verify",
    )
    p_criteria.add_argument("--check-id", dest="check_id", default="", help="the check that ran")
    p_criteria.add_argument("--cmd", default="", help="the command the evidence came from")
    p_criteria.add_argument("--exit-code", dest="exit_code", type=int, default=0)
    p_criteria.add_argument("--artifact", default="", help="artifact the check produced")
    p_criteria.add_argument("--workspace", default="", help="workspace the check ran in")
    p_criteria.add_argument(
        "--workspace-fingerprint", dest="workspace_fingerprint", default=None,
        help="fingerprint the evidence belongs to (default: the goal's)",
    )
    p_criteria.add_argument(
        "--revision", type=int, default=None, help="revision the evidence was produced under"
    )
    p_criteria.set_defaults(func=cmd_goal_criteria)
    return sub


def build_runs_parser(subparsers: Any) -> Any:
    """Add the ``runs`` group to a CLI's ``subparsers`` handle and return it."""
    parser = subparsers.add_parser(
        "runs",
        help="durable run store: goal record, operation ledger, resume protocol",
        description=(
            "The PERSIST layer's store: one sqlite file per runtime with the durable goal "
            "record, the operation ledger (idempotency key + side-effect disposition) and the "
            "resume cursor. Resume classifies in-flight work and parks what cannot be proven "
            "instead of retrying it."
        ),
    )
    sub = parser.add_subparsers(dest="runs_command")
    _add_runs_verbs(sub)
    return parser


def build_goal_parser(subparsers: Any) -> Any:
    """Add the ``goal`` group to a CLI's ``subparsers`` handle and return it."""
    parser = subparsers.add_parser(
        "goal",
        help="the durable goal record (revision, criteria with typed evidence refs)",
        description=(
            "The goal record the runtime — never the model — writes: goal id, revision, state, "
            "criteria with typed evidence refs, decisions, operations, budget and the resume "
            "cursor. A criterion flips to verified only from evidence."
        ),
    )
    sub = parser.add_subparsers(dest="goal_command")
    _add_goal_verbs(sub)
    return parser


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers)`` adds both groups.

    Returns the ``runs`` group's parser (the first one registered); ``goal`` is
    registered on the same handle, exactly like a second call would be.
    """
    runs_parser = build_runs_parser(subparsers)
    build_goal_parser(subparsers)
    return runs_parser


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with both groups (``python -m skaldir.runs_cli …``/tests)."""
    parser = argparse.ArgumentParser(
        prog="skaldir runs", description="PERSIST: durable goal record + resume protocol"
    )
    sub = parser.add_subparsers(dest="command")
    register_cli(sub)
    if _CONTRACT is not None:
        _CONTRACT.annotate(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir runs …`` standalone — same handlers, same envelope."""
    parser = build_parser()
    try:
        args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return EXIT_OK if not exc.code else EXIT_USAGE
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    if _CONTRACT is None:
        return int(func(args) or 0)
    return int(_CONTRACT.run_command(args, func))


if __name__ == "__main__":  # pragma: no cover — module entry point
    raise SystemExit(main())


__all__ = [
    "build_goal_parser",
    "build_parser",
    "build_runs_parser",
    "cmd_goal_criteria",
    "cmd_goal_show",
    "cmd_runs_checkpoint",
    "cmd_runs_list",
    "cmd_runs_resume",
    "cmd_runs_start",
    "cmd_runs_status",
    "main",
    "register_cli",
]
