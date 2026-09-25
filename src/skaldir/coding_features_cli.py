"""``skaldir coding …`` — the workhorse coding-agent verbs (one group, ten verbs).

This is the CLI half of ``src/skaldir/coding_features.py``; the engine holds the behaviour and
this module holds the envelope, the flags and the exit codes.

::

    coding events run -- <command…>   run a command and emit begin/progress/end events (JSONL)
    coding events show <stream>       validate a stream file and summarise its items
    coding plan begin <task>          open plan mode for a task (state: <runtime>/coding/plan.json)
    coding plan probe                 the non-mutating exploration the plan is grounded on
    coding plan propose               render the decision-complete <proposed_plan>
    coding plan show                  the current plan, its guards and its proposal
    coding plan check -- <command…>   may this action run right now? (plan mode refuses writes)
    coding plan close --apply|--drop  close plan mode; --apply is the licence to mutate
    coding testfix -- <test argv…>    run → parse → fix → run, bounded, stop on no progress
    coding complete <query>           ``@``-style fuzzy file completion over the real workspace
    coding expand <text…>             resolve every @mention in a prompt (refuses path escapes)
    coding turn begin|record|new|end|list|undo    the turn journal behind "undo last turn"
    coding status                     the session summary: messages, tokens, turns, plan, model
    coding summary [--write]          the handoff summary (Codex's compact shape) as markdown
    coding init [--force]             write an AGENTS.md whose headings project_context injects
    coding model show|set|clear       model + reasoning effort for THIS session, with the layer

**Integration — the only change ``cli.py`` needs (2 lines).** In ``cli.build_parser()``, next
to the other groups, right after ``sub = parser.add_subparsers(...)``::

    from .coding_features_cli import register_cli as _register_coding   # +
    _register_coding(sub)                                              # +

``register_cli`` installs its own ``COMMANDS`` entries through
``cli_shared.install_contract_entries``, so wiring the group cannot forget them.

**Exit codes** are the product CLI's vocabulary and the engine's payloads already carry the
number that fits: ``0`` ok · ``1`` usage · ``2`` a run/test failure (verification failed) ·
``3`` blocked (plan mode declined the action) · ``5`` not found (no turn to undo, unknown
stream) · ``6`` conflict (an existing AGENTS.md, a file that moved since the turn). A command
that exits non-zero under ``coding events run`` is **not** an error of ours — its own exit
code travels in the payload, and the CLI reports ``2``.

**Nothing here mutates the workspace** except the two verbs that say so (``coding init`` writes
the project doc; ``coding turn``/``undo`` writes the journal and the restored files, both under
``<runtime>/coding`` or byte-verified restores of a recorded turn).
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Any

from . import coding_features as coder
from .tools_core.contract import EXIT_OK

try:  # the shared group plumbing (one implementation, never copied)
    from .cli_shared import (
        dump,
        install_contract_entries,
        product_contract,
        runtime_dir,
        share_common_flags,
        text_mode,
    )
except Exception:  # pragma: no cover — a build without the product CLI helpers
    install_contract_entries = None  # type: ignore[assignment]

    def product_contract() -> Any:
        """A build without the product CLI still parses and runs this whole group."""
        return None

    def share_common_flags(parser: Any) -> None:
        """Nothing to add here: ``_flagged`` already declares this group's own ``--text``."""
        return None

    def text_mode(args: argparse.Namespace) -> bool:
        """Without the product CLI there is no ``--format``, so this group is text-first."""
        return True

    def runtime_dir(args: argparse.Namespace) -> Path:
        """The same directory ``cli_shared`` resolves, with the same environment rule."""
        from .registry import default_runtime_dir

        explicit = str(getattr(args, "runtime", "") or "")
        return Path(explicit).expanduser() if explicit else default_runtime_dir()

    def dump(payload: Any) -> None:
        """One JSON document on stdout — the fallback shape of the envelope's ``data``."""
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))


_CONTRACT = product_contract()

#: True while ``python -m skaldir.coding_features_cli`` drives the verbs itself.
_OWN_DRIVER = False

#: The parsed arguments of that standalone run (so a verb can print its own JSON).
_OWN_ARGS: argparse.Namespace = argparse.Namespace()

_GROUP_HELP = "the workhorse coding-agent verbs: event stream, plan mode, test-fix loop, mentions"
_EVIDENCE_KINDS = ("code", "test", "file", "run", "verification")

#: The exit code a streamed run reports per terminal status: a declined action is 3 (blocked),
#: anything that ran and went wrong is 2 (the child's own code travels in ``child_exit_code``).
_STREAM_EXIT = {"completed": 0, "declined": 3, "failed": 2}


def _coding_wants_text(args: argparse.Namespace) -> bool:
    """Human text only when it is explicitly asked for; the envelope owns stdout."""
    if _OWN_DRIVER:
        return not bool(getattr(args, "json", False))
    return text_mode(args)


def _coding_state(args: argparse.Namespace) -> Path:
    """``--state`` when given, else ``<runtime>/coding`` (the group's own runtime area)."""
    given = str(getattr(args, "state", "") or "")
    if given:
        return Path(given).expanduser()
    return runtime_dir(args) / "coding"


def _coding_workspace(args: argparse.Namespace) -> Path:
    """The directory a verb works in: ``--workspace`` else the current directory."""
    given = str(getattr(args, "workspace", "") or "")
    return Path(given).expanduser().resolve() if given else Path.cwd().resolve()


def _coding_publish(
    payload: Any,
    *,
    evidence: tuple[dict[str, Any], ...] = (),
    warnings: tuple[str, ...] = (),
) -> None:
    """Hand the payload, its evidence and its warnings to the shared envelope."""
    if _OWN_DRIVER:
        if bool(getattr(_OWN_ARGS, "json", False)):
            dump(payload)
        return
    if _CONTRACT is None:
        return
    try:
        _CONTRACT.set_data(payload)
    except Exception:  # noqa: BLE001 — an envelope problem must never break a verb
        return
    for item in evidence:
        kind = str(item.get("kind", "code"))
        _CONTRACT.add_evidence(
            str(item.get("ref", "")), kind if kind in _EVIDENCE_KINDS else "code",
            str(item.get("note", "")),
        )
    for note in warnings:
        _CONTRACT.warn(str(note))


def _coding_refuse(code: int, message: str, *, details: dict[str, Any] | None = None) -> int:
    """Route a refusal through the contract's own helpers, by code, without importing them.

    One place decides how a code is reported: 2 verification failed, 3 blocked, 5 not found,
    and anything else through the contract's generic ``fail``. A build without the contract
    module prints the message to stderr — never stdout, which a ``--json`` consumer parses.
    """
    handlers = {
        2: "verification_failed",
        3: "blocked",
        4: "needs_credentials",
        5: "not_found",
    }
    name = handlers.get(code)
    if _CONTRACT is not None and name is not None:
        handler = getattr(_CONTRACT, name, None)
        if handler is not None:
            return int(handler(message, details=details))
    if _CONTRACT is not None and hasattr(_CONTRACT, "fail"):
        return int(_CONTRACT.fail(code, message, details=details))
    print(f"error: {message}", file=sys.stderr, flush=True)
    return int(code)


def _coding_finish(
    payload: dict[str, Any],
    *,
    evidence: tuple[dict[str, Any], ...] = (),
    warnings: tuple[str, ...] = (),
) -> int:
    """Publish one verb's payload and turn its own ``exit_code`` into the CLI's exit code."""
    _coding_publish(payload, evidence=evidence, warnings=warnings)
    code = int(payload.get("exit_code", 0) or 0)
    if code == EXIT_OK:
        return EXIT_OK
    message = str(payload.get("reason") or payload.get("status") or "the verb did not complete")
    details = {
        "status": payload.get("status", ""),
        "reason": payload.get("reason", ""),
        "error_kind": payload.get("error_kind", ""),
    }
    if _OWN_DRIVER:
        print(f"error: {message}", file=sys.stderr, flush=True)
        return code
    return _coding_refuse(code, message, details=details)


def _coding_note(text: str) -> None:
    """A human line on stderr — never stdout, which belongs to the envelope."""
    if _coding_wants_text(_OWN_ARGS) or not _OWN_DRIVER:
        print(text, file=sys.stderr, flush=True)


# --------------------------------------------------------------- contract glue


def _coding_entry(path: str, layer: str, ref: str, text: str, **extra: Any) -> Any:
    """One ``COMMANDS`` entry (the compact table shape ``cli_contract`` uses)."""
    if _CONTRACT is None:
        return None
    return _CONTRACT.CommandSpec(
        layer, ({"ref": ref, "kind": "code", "note": path},), text, **extra
    )


_SIMPLE = {"type": "object", "additionalProperties": True}


def coding_contract_entries() -> dict[str, Any]:
    """The ``COMMANDS`` entries this group installs (identical objects, idempotent)."""
    feat = "src/skaldir/coding_features.py"
    table: dict[str, Any] = {
        "coding.events.run": _coding_entry(
            "coding events run", "SUPERVISE", feat,
            "run one command and emit run.started / item.started / item.updated / "
            "item.completed / run.completed as JSONL",
            mutates=True, exit_codes=(0, 1, 2, 3, 7),
            data_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "exit_code": {"type": ["integer", "null"]},
                    "lines": {"type": "integer"},
                    "events": {"type": "integer"},
                },
                "required": ["status", "lines", "events"],
            },
            notes="the command's own exit code travels in data; the CLI reports 2 for a failed "
                  "run and 3 for a declined one (--plan-guard); an item is never both",
        ),
        "coding.events.show": _coding_entry(
            "coding events show", "SUPERVISE", feat,
            "validate one stream file and count its items per terminal status",
            exit_codes=(0, 1, 5), data_schema=_SIMPLE,
            notes="the invariant checked is one terminal event per started item; an orphan is "
                  "reported, never ignored",
        ),
        "coding.plan.begin": _coding_entry(
            "coding plan begin", "ENFORCE", feat,
            "open plan mode for a task (refuses while another plan is open)",
            mutates=True, exit_codes=(0, 1, 6, 7), data_schema=_SIMPLE,
            notes="plan mode is enforced by coding plan check, not by prompt text",
        ),
        "coding.plan.probe": _coding_entry(
            "coding plan probe", "EXPAND", feat,
            "the non-mutating exploration a plan is grounded on (bounded walk + declared commands)",
            exit_codes=(0, 5, 7), data_schema=_SIMPLE,
            notes="reuses codectx.walk_workspace and its ignore rules; a sample that was cut "
                  "short says so",
        ),
        "coding.plan.propose": _coding_entry(
            "coding plan propose", "ENFORCE", feat,
            "render the decision-complete <proposed_plan> for the open plan",
            mutates=True, exit_codes=(0, 3, 5, 7), data_schema=_SIMPLE,
            notes="an unanswered question makes the plan NOT decision complete and it says so",
        ),
        "coding.plan.show": _coding_entry(
            "coding plan show", "ENFORCE", feat,
            "the current plan: status, guarded actions and the rendered proposal",
            exit_codes=(0, 5), data_schema=_SIMPLE,
            notes="no plan recorded exits 5; read-only",
        ),
        "coding.plan.check": _coding_entry(
            "coding plan check", "ENFORCE", feat,
            "classify one action as read_only or mutating and say whether it may run now",
            exit_codes=(0, 1, 3), data_schema=_SIMPLE,
            notes="outside plan mode everything is allowed; inside, a mutating action is "
                  "declined with exit 3 and the reason names the rule",
        ),
        "coding.plan.close": _coding_entry(
            "coding plan close", "ENFORCE", feat,
            "close plan mode with --apply (the licence to mutate) or --drop",
            mutates=True, exit_codes=(0, 1, 5, 7), data_schema=_SIMPLE,
            notes="--apply does not run anything; it ends the refusal",
        ),
        "coding.testfix": _coding_entry(
            "coding testfix", "VERIFY", feat,
            "run the declared tests, parse the failures, hand them to a fixer and run again",
            mutates=True, exit_codes=(0, 1, 2, 3, 7, 8), data_schema=_SIMPLE,
            notes="bounded by --max-rounds AND by a repeated failure fingerprint (no_progress); "
                  "without a fixer the roster is returned and exit is 2 — nothing is claimed",
        ),
        "coding.complete": _coding_entry(
            "coding complete", "EXPAND", feat,
            "fuzzy @-style file completion with match indices and scores",
            exit_codes=(0, 1, 7), data_schema=_SIMPLE,
            notes="a query that matches nothing exits 0 with count 0 (data, not failure); "
                  "gitignored paths are never offered",
        ),
        "coding.expand": _coding_entry(
            "coding expand", "EXPAND", feat,
            "resolve every @mention of a prompt to a real path inside the workspace",
            exit_codes=(0, 1, 3, 7), data_schema=_SIMPLE,
            notes="an absolute/parent mention or a symlink out of the workspace is REFUSED and "
                  "listed; nothing outside the workspace is ever read",
        ),
        "coding.turn.begin": _coding_entry(
            "coding turn begin", "PERSIST", feat,
            "open a turn in the journal (the unit `coding turn undo` reverts)",
            mutates=True, exit_codes=(0, 1, 7), data_schema=_SIMPLE,
            notes="writes <runtime>/coding/turns.jsonl only",
        ),
        "coding.turn.record": _coding_entry(
            "coding turn record", "PERSIST", feat,
            "snapshot one file (bytes + sha256) before the turn changes it",
            mutates=True, exit_codes=(0, 1, 5, 7), data_schema=_SIMPLE,
            notes="a snapshot is taken BEFORE the change; --new declares a file the turn creates",
        ),
        "coding.turn.end": _coding_entry(
            "coding turn end", "PERSIST", feat,
            "close the open turn, recording each file's post-turn sha256",
            mutates=True, exit_codes=(0, 1, 5, 7), data_schema=_SIMPLE,
            notes="undo compares against these hashes, which is how it detects a file that "
                  "moved since the turn",
        ),
        "coding.turn.list": _coding_entry(
            "coding turn list", "PERSIST", feat,
            "every recorded turn with its file set, its open/closed state and its undo state",
            exit_codes=(0, 7), data_schema=_SIMPLE, notes="read-only",
        ),
        "coding.turn.undo": _coding_entry(
            "coding turn undo", "PERSIST", feat,
            "undo the last N completed turns, restoring every file byte-identically",
            mutates=True, exit_codes=(0, 1, 5, 6, 7), data_schema=_SIMPLE,
            notes="a file that changed since the turn refuses the WHOLE turn (exit 6) — a "
                  "partial undo is worse than none; a created file is deleted",
        ),
        "coding.status": _coding_entry(
            "coding status", "PERSIST", feat,
            "the session summary: messages by role, token estimate, turns, open plan, model",
            exit_codes=(0, 7), data_schema=_SIMPLE,
            notes="an unreadable session store is reported as available:false with the reason, "
                  "never as a zero",
        ),
        "coding.summary": _coding_entry(
            "coding summary", "PERSIST", feat,
            "the handoff summary (progress, constraints, what remains, references) as markdown",
            mutates=True, exit_codes=(0, 1, 7), data_schema=_SIMPLE,
            notes="built from measured facts only; --write is the only writer "
                  "(<runtime>/coding/summaries/)",
        ),
        "coding.init": _coding_entry(
            "coding init", "EXPAND", feat,
            "write an AGENTS.md whose headings project_context injects, from measured facts",
            mutates=True, exit_codes=(0, 5, 6, 7), data_schema=_SIMPLE,
            notes="refuses to overwrite (exit 6) and returns the existing sha256, so --force is "
                  "an informed decision; no command is invented",
        ),
        "coding.model.show": _coding_entry(
            "coding model show", "ROUTE", feat,
            "the effective model/reasoning effort with the layer that supplied it",
            exit_codes=(0, 1, 7), data_schema=_SIMPLE, notes="read-only; layers are listed in "
            "precedence order",
        ),
        "coding.model.set": _coding_entry(
            "coding model set", "ROUTE", feat,
            "set the model/reasoning effort for THIS session (mid-session switch)",
            mutates=True, exit_codes=(0, 1, 7), data_schema=_SIMPLE,
            notes="an effort outside low|medium|high is a usage error (exit 1), never coerced",
        ),
        "coding.model.clear": _coding_entry(
            "coding model clear", "ROUTE", feat,
            "drop the session override so the environment/config layer wins again",
            mutates=True, exit_codes=(0, 1, 7), data_schema=_SIMPLE, notes="",
        ),
    }
    return {path: entry for path, entry in table.items() if entry is not None}


# ---------------------------------------------------------------------- verbs


def _coding_hoist_trailing(args: argparse.Namespace, known: dict[str, Any]) -> list[str]:
    """Move this group's own flags out of a ``REMAINDER`` command back onto ``args``.

    ``argparse`` sends **everything** after ``--`` into a ``REMAINDER`` positional, including
    our own ``--json`` — measured: ``coding events run -- bash -c '…' --json`` ran
    ``bash -c … --json`` and the flag never reached the envelope. Rather than force users to
    put the flags before the separator, the flags are recognised again here, applied to
    ``args`` (a trailing occurrence wins, as a CLI user expects) and removed from the command.
    """
    tokens = list(getattr(args, "command", []) or [])
    if tokens and tokens[0] == "--":
        tokens = tokens[1:]
    kept: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        converter = known.get(token)
        destination = token[2:].replace("-", "_") if converter is not None else ""
        if converter is bool:
            setattr(args, destination, True)
            index += 1
            continue
        if converter is not None and index + 1 < len(tokens):
            try:
                setattr(args, destination, converter(tokens[index + 1]))
            except ValueError:
                kept.extend(tokens[index : index + 2])
            index += 2
            continue
        if converter is str:
            setattr(args, destination, "")
            index += 1
            continue
        kept.append(token)
        index += 1
    args.command = kept
    return kept


def _coding_shared_trailing() -> dict[str, Any]:
    """The flags a ``REMAINDER`` verb accepts: name -> converter (``bool`` = store_true)."""
    return {
        "--json": bool,
        "--text": bool,
        "--quiet": bool,
        "--no-color": bool,
        "--format": str,
        "--workspace": str,
        "--state": str,
    }


def _coding_run_flags() -> dict[str, Any]:
    """``coding events run`` flags (the shared ones plus its own)."""
    return {
        **_coding_shared_trailing(),
        "--jsonl": str,
        "--run-id": str,
        "--progress-every": int,
        "--timeout": float,
        "--plan-guard": bool,
    }


def _coding_testfix_flags() -> dict[str, Any]:
    """``coding testfix`` flags (the shared ones plus its own)."""
    return {
        **_coding_shared_trailing(),
        "--jsonl": str,
        "--fix-cmd": str,
        "--max-rounds": int,
        "--timeout": float,
    }


def _verb_events_run(args: argparse.Namespace) -> int:
    argv = _coding_hoist_trailing(args, _coding_run_flags())
    if not argv:
        _coding_publish({"status": "usage", "reason": "no command given", "exit_code": 1})
        return 1
    state = _coding_state(args)
    raw_stream = str(getattr(args, "jsonl", "") or "")
    stream_path = Path(raw_stream).expanduser() if raw_stream else None
    sink = coder._Emitter(path=stream_path) if stream_path else coder._Emitter()
    stream = coder.RunStream(run_id=str(getattr(args, "run_id", "") or "coding"), emitter=sink)
    policy: Any = None
    if bool(getattr(args, "plan_guard", False)):
        plan = coder.load_plan(state)
        if plan is not None and plan.get("status") == "planning":

            def _open_plan_policy(action: str) -> str | None:
                verdict = coder.plan_guard(plan, action, state_dir=state)
                return None if verdict["allowed"] else verdict["declined_reason"]

            policy = _open_plan_policy

    stream.run_started(argv, workspace=str(_coding_workspace(args)))
    result = coder.stream_command(
        argv,
        stream=stream,
        cwd=_coding_workspace(args),
        timeout_s=float(getattr(args, "timeout", 120.0) or 120.0),
        progress_every=int(getattr(args, "progress_every", 1) or 1),
        policy=policy,
    )
    sink.close()
    payload = {
        "status": result["status"],
        "reason": result["reason"],
        "exit_code": _STREAM_EXIT.get(result["status"], 2),
        "command": result.get("item_id") and argv,
        "child_exit_code": result["exit_code"],
        "lines": result["lines"],
        "events": len(stream.events),
        "stream": str(stream_path) if stream_path else "",
        "output_tail": result["output_tail"],
        "invariants": coder.validate_stream(stream.events),
    }
    evidence = (
        {"ref": " ".join(shlex.quote(part) for part in argv), "kind": "run",
         "note": f"{payload['lines']} output line(s), {payload['events']} event(s)"},
    )
    if stream_path:
        evidence = evidence + (
            {"ref": str(stream_path), "kind": "file", "note": "the JSONL stream this run emitted"},
        )
    if _coding_wants_text(args):
        _coding_note(
            f"{payload['status']}: {payload['lines']} line(s), {payload['events']} event(s)"
            + (f" -> {stream_path}" if stream_path else "")
        )
    return _coding_finish(payload, evidence=evidence)


def _verb_events_show(args: argparse.Namespace) -> int:
    path = Path(str(args.stream)).expanduser()
    if not path.exists():
        _coding_publish({"status": "missing", "reason": f"{path} does not exist", "exit_code": 5})
        return 5
    try:
        events = coder.read_stream(path)
    except ValueError as exc:
        _coding_publish({"status": "invalid", "reason": str(exc), "exit_code": 1})
        return 1
    report = coder.validate_stream(events)
    payload = {
        "status": "ok" if report["ok"] else "invalid",
        "reason": "",
        "exit_code": 0,
        **report,
    }
    payload["path"] = str(path)
    if not report["ok"]:
        payload["reason"] = "; ".join(report["problems"][:3])
    if _coding_wants_text(args):
        _coding_note(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return _coding_finish(
        payload, evidence=({"ref": str(path), "kind": "file", "note": f"{len(events)} event(s)"},)
    )


def _verb_plan_begin(args: argparse.Namespace) -> int:
    state = _coding_state(args)
    try:
        plan = coder.plan_begin(
            state_dir=state, workspace=_coding_workspace(args), task=str(args.task)
        )
    except coder.PlanConflict as exc:
        _coding_publish(
            {
                "status": "conflict",
                "reason": str(exc),
                "exit_code": 6,
                "plan": coder.load_plan(state),
            }
        )
        return 6
    payload = {
        "status": "planning",
        "reason": "",
        "exit_code": 0,
        "plan": plan,
        "state_file": str(coder.plan_state_path(state)),
    }
    if _coding_wants_text(args):
        _coding_note(f"plan mode open for {args.task!r} -> {payload['state_file']}")
    return _coding_finish(
        payload,
        evidence=({"ref": str(coder.plan_state_path(state)), "kind": "file",
                   "note": "the plan state plan mode refuses writes from"},),
    )


def _verb_plan_probe(args: argparse.Namespace) -> int:
    state = _coding_state(args)
    plan = coder.load_plan(state)
    probe = coder.plan_probe(
        _coding_workspace(args),
        str(getattr(args, "task", "") or (plan or {}).get("task", "")),
        max_files=int(getattr(args, "files", 40) or 40),
    )
    if plan is not None:
        plan["probe"] = probe
        coder.save_plan(state, plan)
    payload = {
        "status": "probed",
        "reason": "",
        "exit_code": 0,
        "probe": probe,
        "attached_to_plan": plan is not None,
    }
    if _coding_wants_text(args):
        _coding_note(json.dumps(probe["declared_commands"], ensure_ascii=False, indent=2))
    return _coding_finish(
        payload,
        evidence=(
            {"ref": str(probe["workspace"]), "kind": "file",
             "note": f"{probe['files_sampled']} file(s) sampled through {probe['walker']}"},
        ),
    )


def _verb_plan_propose(args: argparse.Namespace) -> int:
    state = _coding_state(args)
    plan = coder.load_plan(state)
    if plan is None:
        _coding_publish(
            {"status": "no_plan", "reason": "no plan is open; run `coding plan begin <task>`",
             "exit_code": 5}
        )
        return 5
    if plan.get("probe") is None:
        plan["probe"] = coder.plan_probe(
            plan.get("workspace") or _coding_workspace(args), str(plan.get("task", ""))
        )
    rendered = coder.plan_propose(
        plan,
        changes=[str(item) for item in (getattr(args, "change", None) or [])],
        tests=[str(item) for item in (getattr(args, "test", None) or [])],
        assumptions=[str(item) for item in (getattr(args, "assumption", None) or [])],
        open_questions=[str(item) for item in (getattr(args, "question", None) or [])],
    )
    coder.save_plan(state, plan)
    payload = {
        "status": "proposed",
        "reason": "",
        "exit_code": 0,
        "decision_complete": rendered["decision_complete"],
        "open_questions": rendered["open_questions"],
        "plan": rendered["plan"],
        "state_file": str(coder.plan_state_path(state)),
    }
    if _coding_wants_text(args):
        print(rendered["plan"])
    return _coding_finish(
        payload,
        evidence=({"ref": str(coder.plan_state_path(state)), "kind": "file",
                   "note": "the proposal was written into the plan state"},),
    )


def _verb_plan_show(args: argparse.Namespace) -> int:
    state = _coding_state(args)
    plan = coder.load_plan(state)
    if plan is None:
        _coding_publish({"status": "no_plan", "reason": f"no plan recorded under {state}",
                         "exit_code": 5})
        return 5
    payload = {
        "status": str(plan.get("status", "")),
        "reason": "",
        "exit_code": 0,
        "plan": plan,
        "guarded_actions": plan.get("guarded_actions") or [],
    }
    if _coding_wants_text(args):
        _coding_note(json.dumps(payload["plan"], ensure_ascii=False, indent=2, sort_keys=True))
    return _coding_finish(
        payload,
        evidence=({"ref": str(coder.plan_state_path(state)), "kind": "file",
                   "note": f"plan status {plan.get('status')}"},),
    )


def _verb_plan_check(args: argparse.Namespace) -> int:
    argv = _coding_hoist_trailing(args, _coding_shared_trailing())
    action = " ".join(shlex.quote(part) for part in argv) if argv else str(
        getattr(args, "action", "") or ""
    )
    if not action:
        _coding_publish({"status": "usage", "reason": "no action given", "exit_code": 1})
        return 1
    state = _coding_state(args)
    verdict = coder.plan_guard(coder.load_plan(state), action, state_dir=state)
    payload = {
        "status": "allowed" if verdict["allowed"] else "declined",
        "reason": verdict["declined_reason"],
        "exit_code": 0 if verdict["allowed"] else 3,
        **verdict,
        "state_file": str(coder.plan_state_path(state)),
    }
    if _coding_wants_text(args):
        _coding_note(
            f"{payload['status']}: {action} "
            f"({verdict['classification']} — {verdict['reason']})"
        )
    return _coding_finish(
        payload,
        evidence=({"ref": action, "kind": "run", "note": verdict["reason"]},),
    )


def _verb_plan_close(args: argparse.Namespace) -> int:
    state = _coding_state(args)
    plan = coder.load_plan(state)
    if plan is None:
        _coding_publish({"status": "no_plan", "reason": "no plan is open", "exit_code": 5})
        return 5
    outcome = "applied" if bool(getattr(args, "apply", False)) else "dropped"
    if not bool(getattr(args, "apply", False)) and not bool(getattr(args, "drop", False)):
        _coding_publish(
            {"status": "usage", "reason": "pass --apply or --drop", "exit_code": 1}
        )
        return 1
    closed = coder.plan_close(plan, state_dir=state, outcome=outcome)
    payload = {"status": outcome, "reason": "", "exit_code": 0, "plan": closed}
    if _coding_wants_text(args):
        _coding_note(f"plan mode closed: {outcome}")
    return _coding_finish(
        payload,
        evidence=({"ref": str(coder.plan_state_path(state)), "kind": "file",
                   "note": f"plan closed as {outcome}"},),
    )


def _verb_testfix(args: argparse.Namespace) -> int:
    argv = _coding_hoist_trailing(args, _coding_testfix_flags())
    if not argv:
        _coding_publish({"status": "usage", "reason": "no test command given", "exit_code": 1})
        return 1
    fix_cmd = str(getattr(args, "fix_cmd", "") or "")
    fixer_argv = shlex.split(fix_cmd) if fix_cmd else []
    stream = None
    sink = None
    stream_path = str(getattr(args, "jsonl", "") or "")
    if stream_path:
        sink = coder._Emitter(path=Path(stream_path).expanduser())
        stream = coder.RunStream(run_id="testfix", emitter=sink)
        stream.run_started(argv, workspace=str(_coding_workspace(args)))
    report = coder.test_fix_loop(
        argv,
        cwd=_coding_workspace(args),
        fixer_argv=fixer_argv,
        max_rounds=int(getattr(args, "max_rounds", 3) or 3),
        timeout_s=float(getattr(args, "timeout", 900.0) or 900.0),
        stream=stream,
    )
    if sink is not None:
        sink.close()
    report.pop("regex", None)
    rounds = report.get("rounds") or []
    first = (rounds[0].get("test") if rounds else None) or {}
    payload = {
        "status": report["status"],
        "reason": report["reason"],
        "exit_code": int(report.get("exit_code", 2)),
        "rounds_used": report.get("rounds_used", 0),
        "max_rounds": report.get("max_rounds", 0),
        "failures": report.get("failures", []),
        "fixer": fix_cmd,
        "stream": stream_path,
        "first_run": {
            "exit_code": first.get("exit_code"),
            "failures": len(first.get("failures") or []),
        },
        "rounds": rounds,
    }
    if _coding_wants_text(args):
        _coding_note(
            f"{payload['status']}: {payload['rounds_used']}/{payload['max_rounds']} round(s); "
            f"{payload['reason']}"
        )
    evidence = (
        {
            "ref": " ".join(shlex.quote(part) for part in argv),
            "kind": "test",
            "note": (
                f"{payload['rounds_used']} fixer round(s), "
                f"first run exit {first.get('exit_code')}"
            ),
        },
    )
    return _coding_finish(payload, evidence=evidence)


def _verb_complete(args: argparse.Namespace) -> int:
    result = coder.complete_mention(
        str(args.query), workspace=_coding_workspace(args),
        limit=int(getattr(args, "limit", 20) or 20),
    )
    payload = {"status": "ok", "reason": "", "exit_code": 0, **result}
    if bool(getattr(args, "inline", False)):
        payload["inline"] = [item["path"] for item in result["matches"]]
    if _coding_wants_text(args):
        for item in result["matches"]:
            _coding_note(f"{item['score']:>6}  {item['path']}")
    return _coding_finish(
        payload,
        evidence=(
            {
                "ref": str(result["workspace"]),
                "kind": "file",
                "note": (
                    f"{result['total_matches']} match(es) in "
                    f"{result['files_scanned']} file(s)"
                ),
            },
        ),
    )


def _verb_expand(args: argparse.Namespace) -> int:
    text = str(getattr(args, "text", "") or "")
    result = coder.expand_mentions(text, workspace=_coding_workspace(args))
    warnings = tuple(
        f"refused @{item['mention']}: {item['reason']}" for item in result["refused"]
    )
    payload = {
        "status": "ok",
        "reason": "",
        "exit_code": 0,
        **result,
        "text": text,
    }
    if _coding_wants_text(args):
        for item in result["mentions"]:
            _coding_note(f"{item['kind']:4} {item['path']}")
    return _coding_finish(
        payload,
        evidence=({"ref": str(result["workspace"]), "kind": "file",
                   "note": f"{result['count']} mention(s) processed"},),
        warnings=warnings,
    )


def _verb_turn_begin(args: argparse.Namespace) -> int:
    state = _coding_state(args)
    journal = coder.TurnJournal.begin(state, label=str(getattr(args, "label", "") or ""))
    journal.close(workspace=_coding_workspace(args))
    payload = {
        "status": "open",
        "reason": "",
        "exit_code": 0,
        "turn_id": journal.turn_id,
        "journal": str(coder.turns_path(state)),
    }
    # A CLI turn is opened *and left open*: the next `turn end` closes it. The engine's
    # TurnJournal is the in-process API; a shell needs the cross-process pair below.
    coder._append_turn(
        state,
        {
            "schema": coder.TURN_SCHEMA,
            "event": "turn.begin",
            "turn_id": journal.turn_id,
            "label": str(getattr(args, "label", "") or ""),
            "ts": journal.started_at,
            "workspace": str(_coding_workspace(args)),
        },
    )
    if _coding_wants_text(args):
        _coding_note(f"turn {payload['turn_id']} open — record files, then `coding turn end`")
    return _coding_finish(
        payload, evidence=({"ref": payload["journal"], "kind": "file", "note": "turn journal"},)
    )


def _verb_turn_record(args: argparse.Namespace) -> int:
    state = _coding_state(args)
    try:
        entry = coder.journal_record_file(
            state,
            workspace=_coding_workspace(args),
            path=str(args.path),
            expect_new=bool(getattr(args, "new", False)),
        )
    except LookupError as exc:
        _coding_publish({"status": "no_open_turn", "reason": str(exc), "exit_code": 5})
        return 5
    except ValueError as exc:
        _coding_publish({"status": "outside_workspace", "reason": str(exc), "exit_code": 1})
        return 1
    payload = {"status": "recorded", "reason": "", "exit_code": 0, "file": entry,
               "journal": str(coder.turns_path(state))}
    if _coding_wants_text(args):
        _coding_note(f"recorded {entry['path']} (existed={entry['existed']})")
    return _coding_finish(
        payload,
        evidence=({"ref": str(entry.get("backup") or entry["path"]), "kind": "file",
                   "note": f"before-state of {entry['path']}"},),
    )


def _verb_turn_end(args: argparse.Namespace) -> int:
    state = _coding_state(args)
    try:
        record = coder.close_open_turn(state, workspace=_coding_workspace(args))
    except LookupError as exc:
        _coding_publish({"status": "no_open_turn", "reason": str(exc), "exit_code": 5})
        return 5
    payload = {
        "status": "closed",
        "reason": "",
        "exit_code": 0,
        "turn_id": record["turn_id"],
        "files": record["files"],
    }
    if _coding_wants_text(args):
        _coding_note(f"turn {record['turn_id']} closed with {len(record['files'])} file(s)")
    return _coding_finish(
        payload, evidence=({"ref": str(coder.turns_path(state)), "kind": "file",
                            "note": "turn.end written"},)
    )


def _verb_turn_list(args: argparse.Namespace) -> int:
    state = _coding_state(args)
    turns = coder.turn_summaries(state)
    payload = {
        "status": "ok",
        "reason": "",
        "exit_code": 0,
        "turns": turns,
        "count": len(turns),
        "open": coder.open_turn(state),
        "journal": str(coder.turns_path(state)),
    }
    if _coding_wants_text(args):
        for turn in turns:
            state_name = "undone" if turn["undone"] else ("open" if turn.get("open") else "closed")
            _coding_note(f"{turn['turn_id']} {state_name} {len(turn['files'])} file(s)")
    return _coding_finish(
        payload,
        evidence=({"ref": payload["journal"], "kind": "file", "note": f"{len(turns)} turn(s)"},),
    )


def _verb_turn_undo(args: argparse.Namespace) -> int:
    state = _coding_state(args)
    result = coder.undo_turns(
        state, workspace=_coding_workspace(args), count=int(getattr(args, "turns", 1) or 1)
    )
    payload = {
        "status": result["status"],
        "reason": result["reason"],
        "exit_code": int(result.get("exit_code", 0)),
        "turns": result.get("turns", []),
        "restored": result.get("restored", []),
        "files_restored": result.get("files_restored", 0),
        "transcript": result.get("transcript"),
    }
    if _coding_wants_text(args):
        _coding_note(
            f"{payload['status']}: {payload['files_restored']} file(s) — {payload['reason']}"
        )
    evidence = tuple(
        {"ref": str(item["path"]), "kind": "file", "note": item["action"]}
        for item in payload["restored"]
    )
    return _coding_finish(payload, evidence=evidence)


def _verb_status(args: argparse.Namespace) -> int:
    state = _coding_state(args)
    facts = coder.session_facts(
        session_id=str(getattr(args, "session", "") or ""),
        runtime=str(runtime_dir(args)),
        state_dir=state,
        workspace=_coding_workspace(args),
    )
    missing = facts.get("session_found") is False
    payload = {
        "status": "session_not_found" if missing else "ok",
        "reason": facts["messages"]["reason"] if missing else "",
        "exit_code": 5 if missing else 0,
        **facts,
    }
    if _coding_wants_text(args):
        _coding_note(coder.render_summary_markdown(facts))
    warnings: tuple[str, ...] = ()
    if not facts["messages"]["available"]:
        warnings = (f"session store not readable: {facts['messages']['reason']}",)
    return _coding_finish(
        payload,
        evidence=(
            {"ref": facts["messages"].get("store") or str(state), "kind": "file",
             "note": f"{facts['messages']['total']} message(s) recorded"},
        ),
        warnings=warnings,
    )


def _verb_summary(args: argparse.Namespace) -> int:
    state = _coding_state(args)
    facts = coder.session_facts(
        session_id=str(getattr(args, "session", "") or ""),
        runtime=str(runtime_dir(args)),
        state_dir=state,
        workspace=_coding_workspace(args),
    )
    markdown = coder.render_summary_markdown(facts)
    written = ""
    if bool(getattr(args, "write", False)):
        name = str(getattr(args, "session", "") or "session")
        given_out = str(getattr(args, "out", "") or "")
        target = (
            Path(given_out).expanduser()
            if given_out
            else state / "summaries" / f"{name}.md"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")
        written = str(target)
    payload = {
        "status": "ok",
        "reason": "",
        "exit_code": 0,
        "markdown": markdown,
        "written": written,
        "facts": facts,
    }
    if _coding_wants_text(args):
        print(markdown)
    evidence = ({"ref": written or f"session:{facts.get('session_id') or ''}", "kind": "file",
                 "note": "handoff summary"},)
    return _coding_finish(payload, evidence=evidence)


def _verb_init(args: argparse.Namespace) -> int:
    result = coder.write_project_doc(
        _coding_workspace(args),
        filename=str(getattr(args, "file", "") or "AGENTS.md"),
        force=bool(getattr(args, "force", False)),
        name=str(getattr(args, "name", "") or ""),
    )
    if result["status"] == "written" and _coding_wants_text(args):
        _coding_note(f"wrote {result['path']} ({result['bytes']} bytes)")
    payload = {**result, "reason": result.get("reason", "")}
    return _coding_finish(
        payload,
        evidence=(
            {
                "ref": result["path"],
                "kind": "file",
                "note": (
                    f"{result['status']}; headings "
                    f"{', '.join(result.get('headings') or [])}"
                ),
            },
        ),
    )


def _verb_model(args: argparse.Namespace) -> int:
    state = _coding_state(args)
    session = str(getattr(args, "session", "") or "default")
    action = str(getattr(args, "model_command", "show") or "show")
    if action == "set":
        effort = str(getattr(args, "effort", "") or "")
        if effort and effort not in coder.EFFORTS:
            _coding_publish(
                {
                    "status": "usage",
                    "reason": f"reasoning effort must be one of {coder.EFFORTS}, got {effort!r}",
                    "exit_code": 1,
                }
            )
            return 1
        record = coder.set_session_model(
            state_dir=state,
            session_id=session,
            model=str(getattr(args, "model", "") or ""),
            reasoning_effort=effort,
            provider=str(getattr(args, "provider", "") or ""),
        )
        payload = {"status": "set", "reason": "", "exit_code": 0, "record": record}
    elif action == "clear":
        result = coder.clear_session_model(state_dir=state, session_id=session)
        payload = {"status": "cleared", "reason": "", "exit_code": 0, **result}
    else:
        payload = {
            "status": "ok",
            "reason": "",
            "exit_code": 0,
            "effective": coder.effective_session_model(session, state_dir=state),
        }
    if _coding_wants_text(args):
        _coding_note(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return _coding_finish(
        payload,
        evidence=({"ref": str(coder.session_model_path(state, session)), "kind": "file",
                   "note": f"session model layer for {session}"},),
    )


# --------------------------------------------------------------------- wiring


def _flagged(parser: Any) -> Any:
    """Add this group's own text flag and the shared ones (``--json`` and friends)."""
    parser.add_argument(
        "--text", action="store_true", help="human-readable output on stderr instead of JSON"
    )
    share_common_flags(parser)
    return parser


def _space_flags(parser: Any) -> Any:
    """``--workspace`` / ``--state`` — where a verb works and where it keeps its own state."""
    parser.add_argument("--workspace", default="", help="the directory to work in (default: cwd)")
    parser.add_argument("--runtime", default="", help="the runtime dir (default: the registry's)")
    parser.add_argument(
        "--state", default="", help="the state dir (default <runtime>/coding)"
    )
    return parser


def _add_coding_verbs(verbs: Any) -> None:
    """Every leaf of the group. One place, so ``register_cli`` and ``build_parser`` agree."""
    events = verbs.add_parser("events", help="the run-scoped JSON event stream")
    event_verbs = events.add_subparsers(dest="events_command", required=True)
    run = event_verbs.add_parser("run", help="run a command and emit its item lifecycle")
    run.add_argument("command", nargs=argparse.REMAINDER, help="the command to run (after --)")
    run.add_argument("--jsonl", default="", help="write the stream to this file (JSONL)")
    run.add_argument("--progress-every", type=int, default=1, help="emit an update every N lines")
    run.add_argument(
        "--timeout", type=float, default=120.0, help="kill the command after N seconds"
    )
    run.add_argument("--run-id", default="", help="the run id the events carry")
    run.add_argument("--plan-guard", action="store_true",
                     help="decline a mutating command while plan mode is open")
    _space_flags(run)
    _flagged(run)
    run.set_defaults(func=_verb_events_run)

    show = event_verbs.add_parser("show", help="validate a stream file and count its items")
    show.add_argument("stream", help="the JSONL stream file")
    _flagged(show)
    show.set_defaults(func=_verb_events_show)

    plan = verbs.add_parser("plan", help="plan mode: propose before touching files")
    plan_verbs = plan.add_subparsers(dest="plan_command", required=True)
    p_begin = plan_verbs.add_parser("begin", help="open plan mode for a task")
    p_begin.add_argument("task", help="what the plan is for")
    _space_flags(p_begin)
    _flagged(p_begin)
    p_begin.set_defaults(func=_verb_plan_begin)

    p_probe = plan_verbs.add_parser("probe", help="the non-mutating exploration behind the plan")
    p_probe.add_argument("--task", default="", help="the task (default: the open plan's)")
    p_probe.add_argument("--files", type=int, default=40, help="how many files to sample")
    _space_flags(p_probe)
    _flagged(p_probe)
    p_probe.set_defaults(func=_verb_plan_probe)

    p_propose = plan_verbs.add_parser("propose", help="render the <proposed_plan>")
    p_propose.add_argument("--change", action="append", default=[], help="one planned change")
    p_propose.add_argument("--test", action="append", default=[], help="one test-plan line")
    p_propose.add_argument("--assumption", action="append", default=[], help="one assumption")
    p_propose.add_argument("--question", action="append", default=[],
                           help="an open question (makes the plan NOT decision complete)")
    _space_flags(p_propose)
    _flagged(p_propose)
    p_propose.set_defaults(func=_verb_plan_propose)

    p_show = plan_verbs.add_parser("show", help="the current plan, its guards and proposal")
    _space_flags(p_show)
    _flagged(p_show)
    p_show.set_defaults(func=_verb_plan_show)

    p_check = plan_verbs.add_parser("check", help="may this action run right now?")
    p_check.add_argument("command", nargs=argparse.REMAINDER, help="the action (after --)")
    p_check.add_argument("--action", default="", help="the action as one string")
    _space_flags(p_check)
    _flagged(p_check)
    p_check.set_defaults(func=_verb_plan_check)

    p_close = plan_verbs.add_parser("close", help="close plan mode")
    p_close.add_argument("--apply", action="store_true", help="the plan is the licence to mutate")
    p_close.add_argument("--drop", action="store_true", help="abandon the plan")
    _space_flags(p_close)
    _flagged(p_close)
    p_close.set_defaults(func=_verb_plan_close)

    testfix = verbs.add_parser("testfix", help="run tests, fix, run again (bounded)")
    testfix.add_argument("command", nargs=argparse.REMAINDER, help="the test command (after --)")
    testfix.add_argument("--fix-cmd", default="",
                         help="a shell-free fixer argv (parsed with shlex, no shell)")
    testfix.add_argument("--max-rounds", type=int, default=3, help="the hard round bound")
    testfix.add_argument("--timeout", type=float, default=900.0, help="per test-run timeout")
    testfix.add_argument("--jsonl", default="", help="write the loop's events to this file")
    _space_flags(testfix)
    _flagged(testfix)
    testfix.set_defaults(func=_verb_testfix)

    complete = verbs.add_parser("complete", help="fuzzy @-style file completion")
    complete.add_argument("query", help="the partial path (a leading @ is allowed)")
    complete.add_argument("--limit", type=int, default=20, help="how many matches to return")
    complete.add_argument("--inline", action="store_true", help="add a plain path list for shells")
    _space_flags(complete)
    _flagged(complete)
    complete.set_defaults(func=_verb_complete)

    expand = verbs.add_parser("expand", help="resolve the @mentions of a prompt")
    expand.add_argument("text", help="the prompt text carrying @mentions")
    _space_flags(expand)
    _flagged(expand)
    expand.set_defaults(func=_verb_expand)

    turn = verbs.add_parser("turn", help="the turn journal behind undo-last-turn")
    turn_verbs = turn.add_subparsers(dest="turn_command", required=True)
    t_begin = turn_verbs.add_parser("begin", help="open a turn")
    t_begin.add_argument("--label", default="", help="what this turn is")
    _space_flags(t_begin)
    _flagged(t_begin)
    t_begin.set_defaults(func=_verb_turn_begin)

    t_record = turn_verbs.add_parser("record", help="snapshot a file before the turn changes it")
    t_record.add_argument("path", help="the file the turn is about to change")
    t_record.add_argument("--new", action="store_true", help="the turn CREATES this file")
    _space_flags(t_record)
    _flagged(t_record)
    t_record.set_defaults(func=_verb_turn_record)

    t_end = turn_verbs.add_parser("end", help="close the open turn")
    _space_flags(t_end)
    _flagged(t_end)
    t_end.set_defaults(func=_verb_turn_end)

    t_list = turn_verbs.add_parser("list", help="every recorded turn")
    _space_flags(t_list)
    _flagged(t_list)
    t_list.set_defaults(func=_verb_turn_list)

    t_undo = turn_verbs.add_parser("undo", help="undo the last N completed turns")
    t_undo.add_argument("--turns", type=int, default=1, help="how many turns to undo")
    _space_flags(t_undo)
    _flagged(t_undo)
    t_undo.set_defaults(func=_verb_turn_undo)

    status = verbs.add_parser("status", help="the session summary")
    status.add_argument("--session", default="", help="the session id to summarise")
    _space_flags(status)
    _flagged(status)
    status.set_defaults(func=_verb_status)

    summary = verbs.add_parser("summary", help="the handoff summary as markdown")
    summary.add_argument("--session", default="", help="the session id to summarise")
    summary.add_argument("--write", action="store_true", help="write it to a file")
    summary.add_argument(
        "--out", default="", help="where to write it (default under the state dir)"
    )
    _space_flags(summary)
    _flagged(summary)
    summary.set_defaults(func=_verb_summary)

    init = verbs.add_parser("init", help="write an AGENTS.md from measured facts")
    init.add_argument("--force", action="store_true", help="replace an existing file")
    init.add_argument("--file", default="AGENTS.md", help="the file name to write")
    init.add_argument("--name", default="", help="the project name in the title")
    _space_flags(init)
    _flagged(init)
    init.set_defaults(func=_verb_init)

    model = verbs.add_parser("model", help="model + reasoning effort for this session")
    model_verbs = model.add_subparsers(dest="model_command")
    m_show = model_verbs.add_parser("show", help="the effective model and the layer that set it")
    for parser in (m_show,):
        parser.add_argument("--session", default="", help="the session id")
        _space_flags(parser)
        _flagged(parser)
        parser.set_defaults(func=_verb_model, model_command="show")
    m_set = model_verbs.add_parser("set", help="switch model/effort mid-session")
    m_set.add_argument("--model", default="", help="the model name")
    m_set.add_argument("--effort", default="", help=f"reasoning effort: {'|'.join(coder.EFFORTS)}")
    m_set.add_argument("--provider", default="", help="the provider name")
    m_set.add_argument("--session", default="", help="the session id")
    _space_flags(m_set)
    _flagged(m_set)
    m_set.set_defaults(func=_verb_model, model_command="set")
    m_clear = model_verbs.add_parser("clear", help="drop the session override")
    m_clear.add_argument("--session", default="", help="the session id")
    _space_flags(m_clear)
    _flagged(m_clear)
    m_clear.set_defaults(func=_verb_model, model_command="clear")
    model.set_defaults(model_command="show")


def _coding_verb_action(parent: Any) -> Any:
    """The verb action ``parent`` already carries, or ``None`` when it has none yet.

    Resolved through a generator rather than a loop: this group shares the top-level namespace
    with every other block, and a helper whose body is byte-identical to another group's is a
    copy, not a shared helper — ``tests/test_cli_shared.py`` fails on exactly that.
    """
    actions = list(getattr(parent, "_actions", []))
    return next(
        (item for item in actions if isinstance(item, argparse._SubParsersAction)), None
    )


def register_cli(subparsers: Any) -> Any:
    """Add the ``coding`` group, idempotently (a second call adds nothing)."""
    from .cli_shared import as_subparsers

    sub = as_subparsers(subparsers)
    parent = (getattr(sub, "choices", None) or {}).get("coding")
    if parent is None:
        parent = sub.add_parser("coding", help=_GROUP_HELP)
    verbs = _coding_verb_action(parent)
    if verbs is None:
        verbs = parent.add_subparsers(dest="coding_command", required=True)
    if "events" not in getattr(verbs, "choices", {}):
        _add_coding_verbs(verbs)
    for parser in (parent, verbs):
        share_common_flags(parser)
    if install_contract_entries is not None and _CONTRACT is not None:
        install_contract_entries(coding_contract_entries(), _CONTRACT)
    return parent


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (tests / ``--help``)."""
    parser = argparse.ArgumentParser(prog="skaldir coding", description=_GROUP_HELP)
    verbs = parser.add_subparsers(dest="coding_command", required=True)
    _add_coding_verbs(verbs)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir coding …`` standalone: the exit code, never a raise for a request."""
    global _OWN_DRIVER, _OWN_ARGS
    invoked = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    _OWN_DRIVER = True
    try:
        args = parser.parse_args(invoked)
    except SystemExit as exc:
        _OWN_DRIVER = False
        return EXIT_OK if not exc.code else 1
    _OWN_ARGS = args
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        _OWN_DRIVER = False
        return 1
    return int(handler(args))


if __name__ == "__main__":  # pragma: no cover — the module's own entry point
    raise SystemExit(main(sys.argv[1:]))


__all__ = [
    "build_parser",
    "coding_contract_entries",
    "main",
    "register_cli",
]
