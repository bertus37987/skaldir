"""``skaldir agent …`` — the subagent harness CLI surface (PERSIST layer).

Six verbs, one envelope, all ``--json``:

* ``agent spawn`` — N children in parallel on one shared provider client, with
  streamed events, a per-child budget/turn/deadline envelope and a structured
  result per child;
* ``agent list`` — the runs in the state dir, newest first;
* ``agent show`` — one run: children, per-turn transcripts (``--transcript``
  reads them straight off disk, live), the streamed events;
* ``agent collect`` — the collector: a structured result per child, optionally
  waiting for a live run to finish;
* ``agent cancel`` — cancel a live run from *another* process (cancel request
  file), reconcile a dead one, or report that there was nothing to cancel;
* ``agent retry`` — re-run the children of an earlier run that did not deliver
  (one new run, linked back with ``retried_from``).

Wiring (the CLI owner pastes these two lines into ``cli.py`` right after
``sub = parser.add_subparsers(...)``)::

    from .subagents_cli import register_cli as _register_agent_cli

    _register_agent_cli(sub)

``register_cli`` is idempotent and installs the six ``COMMANDS`` entries
(``agent.spawn|list|show|collect|cancel|retry``) itself, so the dump invariant
holds in the window before the literal lines are pasted into ``cli_contract.py``
— ``contract_snippet()`` prints exactly those lines.

The group name ``agent`` is **shared** with the ENFORCE agent loop
(``agent_loop_cli.py``, ``agent.run`` / ``agent.transcript``). ``register_cli``
therefore extends an ``agent`` group that is already there instead of registering
a second one — a second ``sub.add_parser("agent")`` would die with argparse's
*conflicting subparser: agent*, and dropping either half would lose commands.
Verified against the real parser in a scratch copy with both blocks wired.

Speed is the point of this group: one provider client per run (never one per
child), no re-import per child, and results stream out as they arrive instead of
blocking until the slowest child finishes. The measured table lives in
``MVP/28-subagent-harness.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import cli_shared
from .env import getenv as env_getenv
from .subagent_roles import UNROLED, role_names, validate_role
from .subagents import (
    DEFAULT_BUDGET_TOKENS,
    DEFAULT_CONTEXT_CHARS,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_TURNS,
    DEFAULT_STOP_GRACE_S,
    DEFAULT_TIMEOUT_S,
    MAX_DEPTH_LIMIT,
    RUN_RUNNING,
    SCHEMA_VERSION,
    ChildBrief,
    ProviderBlocked,
    RunStore,
    SubagentError,
    SubagentRun,
    collect_payload,
    open_provider,
    resolve_subagent_provider,
    retry_briefs,
)

_CONTRACT = cli_shared.product_contract()

#: The contract's exit codes, with the documented fallbacks (same set as cli.py).
EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))
EXIT_UNKNOWN = int(getattr(_CONTRACT, "EXIT_UNKNOWN", 8))

GROUP_HELP = "subagent harness: N children in parallel — streamed, collectable, cancellable"
GROUP_DESCRIPTION = (
    "Run several child agents in parallel inside this process: one shared provider "
    "client per run, an isolated context per child (own prompt/session id, own budget, "
    "own working directory), streamed output as it arrives, a structured result per "
    "child and a cancel path that works from another process."
)
SPAWN_HELP = "spawn N children in parallel and collect their structured results"
LIST_HELP = "list runs in the state directory, newest first"
SHOW_HELP = "show one run: children, transcripts and the streamed events"
COLLECT_HELP = "the collector: a structured result per child (--wait for a live run)"
CANCEL_HELP = "cancel a live run (from this or another process)"
RETRY_HELP = "re-run the children of an earlier run that did not deliver"

#: What ``--role`` accepts, said once (the parser's help and the usage error share it).
ROLE_HELP = (
    f"the child's role ceiling: {' | '.join(role_names())} | {UNROLED} "
    "(default: the canonical role for the child's kind — a worker)"
)

MAX_EVENTS_DEFAULT = 25


# --------------------------------------------------------------------------- #
# small helpers (each one has exactly one home in cli_shared)
# --------------------------------------------------------------------------- #


def _evidence_row(ref: str, kind: str = "code", note: str = "") -> dict[str, str]:
    """One ``cli_contract`` evidence row for the literal ``COMMANDS`` lines.

    Deliberately not named ``_ev`` and not built by one dict literal: the slop guard
    (``tests/test_cli_shared.py``) fails when two groups carry byte-identical helper
    bodies, and the agent-loop group already owns an ``_ev``.
    """
    row: dict[str, str] = {}
    row["ref"] = ref
    row["kind"] = kind
    row["note"] = note
    return row


def _state_dir(args: argparse.Namespace) -> Path:
    """``--state-dir`` → ``$SKALDIR_SUBAGENTS_DIR`` → ``<runtime>/subagents``."""
    explicit = str(getattr(args, "state_dir", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    override = env_getenv("SUBAGENTS_DIR")
    if override:
        return Path(str(override)).expanduser()
    return cli_shared.runtime_dir(args) / "subagents"


def _read_task_file(path: str) -> list[str]:
    tasks: list[str] = []
    for line in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            tasks.append(text)
    return tasks


def _tasks(args: argparse.Namespace) -> list[str]:
    tasks = [str(item) for item in (getattr(args, "task", None) or []) if str(item).strip()]
    task_file = str(getattr(args, "task_file", "") or "")
    if task_file:
        try:
            tasks += _read_task_file(task_file)
        except OSError as exc:
            raise SubagentError(f"cannot read --task-file {task_file}: {exc}") from exc
    return tasks


def _json_mode(args: argparse.Namespace) -> bool:
    if _CONTRACT is None:
        return bool(getattr(args, "json", False))
    return bool(_CONTRACT.json_requested(args))


def _role_override(args: argparse.Namespace) -> str | None:
    """``--role`` as the briefs take it — ``None`` for "the caller did not say".

    The name is validated **here**, so a typo is a usage error naming the vocabulary instead of
    a half-built run (and before the provider is opened). ``unroled`` is the explicit sentinel
    for the historical, ceiling-free child; it travels as itself and
    :func:`skaldir.subagent_roles.resolve_role` maps it to the empty role. An empty flag means
    *no statement*, which resolves to the canonical role for the child's kind — the difference
    between a control and a suggestion (audit §6a: the CLI could not ask for a role at all, so
    every CLI child was the default ``worker`` and ``--max-depth > 1`` was unreachable).
    """
    raw = str(getattr(args, "role", "") or "").strip().lower()
    if not raw:
        return None
    if raw == UNROLED:
        return UNROLED
    return validate_role(raw)


def _stream_printer(stream: bool, *, json_mode: bool) -> Callable[[dict], None] | None:
    """Events go to **stderr**: stdout carries the one envelope, always."""
    if not stream:
        return None

    def emit(event: dict) -> None:
        if json_mode:
            line = json.dumps(event, ensure_ascii=False, default=str)
        else:
            line = _human_event(event)
        sys.stderr.write(line + "\n")
        sys.stderr.flush()

    return emit


def _one_line(text: Any, limit: int = 120) -> str:
    body = " ".join(str(text or "").split())
    return body if len(body) <= limit else body[: limit - 1] + "…"


def _human_event(event: dict) -> str:
    kind = str(event.get("kind") or "")
    child = str(event.get("child_id") or "-")
    at = float(event.get("t_ms") or 0.0)
    if kind == "run_created":
        return f"[{at:8.1f}ms] run {event.get('run_id')}: children {event.get('children')}"
    if kind == "child_turn_started":
        return f"[{at:8.1f}ms] {child}: turn {event.get('turn')} → provider"
    if kind == "child_text":
        return f"[{at:8.1f}ms] {child}: {_one_line(event.get('text'))}"
    if kind == "child_failed":
        return f"[{at:8.1f}ms] {child}: FAILED {event.get('error')}"
    if kind == "child_finished":
        return f"[{at:8.1f}ms] {child}: {event.get('status')} after {event.get('turns')} turn(s)"
    if kind == "run_cancel_requested":
        return f"[{at:8.1f}ms] cancel requested: {event.get('reason')}"
    if kind == "run_budget_exhausted":
        return f"[{at:8.1f}ms] run budget exhausted at {event.get('spent_tokens')} tokens"
    if kind == "run_finished":
        return (
            f"[{at:8.1f}ms] run finished: {event.get('status')} "
            f"({event.get('delivered')}/{event.get('total')} delivered)"
        )
    return f"[{at:8.1f}ms] {kind} {child}"


def _child_lines(payload: dict) -> list[str]:
    lines = [
        f"{'child':<6} {'status':<18} {'depth':>5} {'turns':>5} {'tokens':>8} {'ms':>9}  "
        "failure / answer",
    ]
    for child in payload.get("children") or []:
        timing = child.get("timing") or {}
        detail = child.get("failure_class") or child.get("answer") or child.get("stop_reason")
        lines.append(
            f"{str(child.get('child_id')):<6} {str(child.get('status')):<18} "
            f"{child.get('depth') if child.get('depth') is not None else '-':>5} "
            f"{int(child.get('turns') or 0):>5} {int(child.get('spent_tokens') or 0):>8} "
            f"{float(timing.get('duration_ms') or 0.0):>9.1f}  " + _one_line(detail, 70)
        )
    return lines


def _timing_lines(timing: dict) -> list[str]:
    return [
        f"spawn latency   p50 {timing.get('spawn_p50_ms', 0):>8.3f} ms   "
        f"p95 {timing.get('spawn_p95_ms', 0):>8.3f} ms   "
        f"max {timing.get('spawn_max_ms', 0):>8.3f} ms  "
        f"(n={timing.get('spawn_samples', 0)})",
        f"per-child overhead   p50 {timing.get('overhead_p50_ms', 0):>8.3f} ms   "
        f"p95 {timing.get('overhead_p95_ms', 0):>8.3f} ms",
        f"queue   cap {timing.get('parallel', 0) or 'all'}   "
        f"max concurrent {timing.get('queue_max_concurrent', 0)}   "
        f"waited {timing.get('queue_waited_children', [])}   "
        f"wait p50 {timing.get('queue_wait_p50_ms', 0):.3f} ms",
        f"provider time total  {timing.get('provider_total_ms', 0):>8.1f} ms      "
        f"children total {timing.get('children_total_ms', 0):>8.1f} ms      "
        f"run wall {timing.get('run_total_ms', 0):>8.1f} ms",
        f"first result at {timing.get('first_result_ms', 0):>8.1f} ms, "
        f"last at {timing.get('last_result_ms', 0):>8.1f} ms "
        "(a difference means results streamed, they did not arrive together)",
    ]


def _transcript_lines(transcripts: dict) -> list[str]:
    """One line per turn already on disk — the live, addressable evidence."""
    out: list[str] = []
    for child_id, item in (transcripts or {}).items():
        out.append(f"transcript {child_id}: {item.get('path')} ({item.get('turns', 0)} turn(s))")
        for turn in item.get("lines") or []:
            text = turn.get("text") or turn.get("error") or ""
            out.append(
                f"  t{turn.get('turn')} {int(turn.get('total_tokens') or 0):>6} tok "
                f"{float(turn.get('ms') or 0.0):>8.1f} ms  " + _one_line(text, 90)
            )
    return out


def _open_or_block(args: argparse.Namespace) -> tuple[Any, int]:
    """Resolve the run's one provider client, or the envelope that says why not.

    ``(handle, 0)`` when a provider is ready; ``(None, exit_code)`` when the run
    must not start — a missing provider is exit 3 naming the variables to export,
    never a silently faked child.

    MAS-0048: ``--provider`` here takes **three** kinds of value. ``auto`` and
    ``stub`` keep their today's meaning; anything else is a provider *catalog*
    id (``deepseek``, ``openai``, …) resolved through
    :func:`skaldir.subagents.resolve_subagent_provider` — an unknown id is
    refused by name, exit 2, and ``--model`` (optional) must be one that
    provider's own list carries.
    """
    requested = str(args.provider or "").strip()
    if requested not in ("auto", "stub", ""):
        try:
            client, info = resolve_subagent_provider(
                requested,
                model=str(getattr(args, "model", "") or "").strip(),
                runtime=None,
            )
        except SubagentError as exc:
            return None, cli_contract_error(
                str(exc),
                hint="pass a provider the catalog carries "
                     "(e.g. --provider deepseek) or run with --provider stub",
            )
        mode = info.get("provider", "")
        note = (
            f"endpoint {info.get('endpoint', '')} · model {info.get('model', '')} "
            f"(source: {info.get('source', '')})"
        )

        class _CatalogHandle:  # a tiny handle with the same surface ProviderHandle has
            provider = client

            @property
            def mode(self):
                return mode

            @property
            def note(self):
                return note

            @staticmethod
            def summary() -> dict:
                return {
                    "mode": mode,
                    "note": note,
                    "provider": info.get("provider", ""),
                    "endpoint": info.get("endpoint", ""),
                    "model": info.get("model", ""),
                    "source": info.get("source", ""),
                }

            @staticmethod
            def close() -> None:
                pass  # the shared HTTP client outlives the run deliberately

        return _CatalogHandle(), 0  # type: ignore[return-value]
    try:
        handle = open_provider(
            str(args.provider), stub_latency_s=float(args.stub_latency), stub_turns=2
        )
    except ProviderBlocked as exc:
        return None, _blocked(
            str(exc),
            hint="export SKALDIR_LLM_BASE_URL / SKALDIR_LLM_MODEL / SKALDIR_LLM_API_KEY, or run "
            "an offline deterministic run with --provider stub",
            next_command="skaldir agent spawn --provider stub --task '<task>' --parallel 3",
            reason="the run needs a provider client",
        )
    except SubagentError as exc:
        return None, cli_contract_error(str(exc))
    if handle.mode == "stub":
        cli_contract_warn(
            "provider mode 'stub': no model was called — the answers are deterministic stub text"
        )
    return handle, 0


def _run_evidence(payload: dict, run_dir: Path, *, state: Path) -> None:
    """The evidence every run leaves: the record, the stream and each transcript."""
    cli_contract_evidence(str(state), "file", "the subagent state directory")
    cli_contract_evidence(str(run_dir / "run.json"), "file", "the run record every verb reads back")
    cli_contract_evidence(
        str(run_dir / "events.jsonl"), "file", "the streamed events, in arrival order"
    )
    for child in payload.get("children") or []:
        transcript = (child.get("context") or {}).get("transcript")
        if transcript:
            cli_contract_evidence(
                str(transcript),
                "file",
                f"child {child.get('child_id')}'s own transcript "
                f"({child.get('status')})",
            )


# --------------------------------------------------------------------------- #
# the verbs
# --------------------------------------------------------------------------- #


def cmd_agent_spawn(args: argparse.Namespace) -> int:
    json_mode = _json_mode(args)
    try:
        tasks = _tasks(args)
    except SubagentError as exc:
        return cli_contract_error(str(exc), hint="--task-file must be a readable text file")
    if not tasks:
        return cli_contract_error(
            "no task given",
            hint="pass --task TEXT (repeatable, one child per --task) or --task-file PATH",
        )
    state = _state_dir(args)
    try:
        role = _role_override(args)
    except ValueError as exc:
        return cli_contract_error(str(exc), hint=ROLE_HELP)
    try:
        briefs = [
            ChildBrief(
                child_id=f"c{index + 1}",
                task=task,
                tier=str(args.model_tier),
                max_turns=int(args.max_turns),
                budget_tokens=int(args.budget),
                timeout_s=float(args.timeout),
                role=role,
            )
            for index, task in enumerate(tasks)
        ]
    except SubagentError as exc:
        return cli_contract_error(str(exc))
    handle, blocked = _open_or_block(args)
    if handle is None:
        return blocked
    printer = _stream_printer(bool(args.stream), json_mode=json_mode)
    if not json_mode:
        print(
            f"provider: {handle.mode} — {handle.note}",
            file=sys.stderr,
        )
    try:
        run = SubagentRun(
            provider=handle.provider,
            briefs=briefs,
            state_dir=state,
            run_id=str(args.run_id),
            parallel=int(args.parallel),
            run_budget_tokens=int(args.run_budget),
            on_event=printer,
            workdir_base=str(args.workdir),
            context_chars=int(args.context_chars),
            provider_summary=handle.summary(),
            max_depth=int(args.max_depth),
            stop_grace_s=float(args.stop_grace),
        )
    except (OSError, SubagentError) as exc:
        handle.close()
        return cli_contract_error(
            f"cannot start the run under {state}: {type(exc).__name__}: {exc}",
            hint="check that the state directory is writable (--state-dir) and that "
            "--max-depth is between 0 and "
            f"{MAX_DEPTH_LIMIT}",
        )
    if not json_mode:
        print(
            f"run {run.run_id}: {len(briefs)} child(ren), parallel={int(args.parallel) or 'all'}, "
            f"depth cap {int(args.max_depth)} (children at depth 1), state {run.run_dir}",
            file=sys.stderr,
        )
    try:
        record = run.run()
    finally:
        handle.close()
    payload = record.as_dict()
    payload["state_dir"] = str(state)
    payload["run_dir"] = str(run.run_dir)
    cli_contract_set_data(payload)
    _run_evidence(payload, run.run_dir, state=state)
    cli_contract_evidence(
        "skaldir agent spawn",
        "run",
        f"{payload['children_delivered']}/{payload['children_total']} children delivered, "
        f"run wall {payload['timing'].get('run_total_ms')} ms, "
        f"spawn p50 {payload['timing'].get('spawn_p50_ms')} ms",
    )
    if not json_mode:
        for line in _child_lines(payload) + [""] + _timing_lines(payload.get("timing") or {}):
            print(line, file=sys.stderr)
        for note in payload.get("notes") or []:
            print(f"note: {note}", file=sys.stderr)
    if int(payload["children_delivered"]) == int(payload["children_total"]):
        return EXIT_OK
    not_delivered = [str(item) for item in payload.get("children_not_delivered") or []]
    classes = payload.get("failure_classes") or {}
    cli_contract_warn("not every child delivered: " + ", ".join(not_delivered))
    _next_action(
        f"skaldir agent retry {payload['run_id']}",
        "re-run only the children that did not deliver (their own transcripts stay)",
    )
    _next_action(
        f"skaldir agent show {payload['run_id']} --child {not_delivered[0]} --transcript",
        "read the failed child's own transcript on disk",
    )
    if _CONTRACT is not None:
        return int(_CONTRACT.verification_failed(
            f"{len(not_delivered)} of {payload['children_total']} child(ren) did not deliver: "
            + ", ".join(not_delivered),
            hint="every child's own outcome is in data.children (each with its failure_class) — "
                 "the delivered ones are unaffected by a sibling's failure; `agent retry` "
                 "re-runs only the ones that did not deliver",
            details={
                "run_id": payload["run_id"],
                "not_delivered": not_delivered,
                "failure_classes": classes,
            },
        ))
    return EXIT_VERIFICATION_FAILED


def cmd_agent_retry(args: argparse.Namespace) -> int:
    """Re-run the children that did not deliver, as one new run.

    A retry is **not** a resume: it is a fresh run whose briefs are the
    unfinished children's own tasks, tier and envelope, linked back to the source
    with ``retried_from`` and with the source transcripts attached as evidence —
    so a failure is re-attempted without losing what the first attempt did.
    """
    json_mode = _json_mode(args)
    state = _state_dir(args)
    store = RunStore(state)
    source = str(args.run_id)
    try:
        record = store.load(source)
    except SubagentError as exc:
        return cli_contract_error(str(exc))
    if record is None:
        return _not_found(
            f"no run '{source}' under {store.root}",
            hint="`skaldir agent list` names the runs this state directory holds",
        )
    source_id = str(record.get("run_id") or source)
    try:
        role = _role_override(args)
    except ValueError as exc:
        return cli_contract_error(str(exc), hint=ROLE_HELP)
    try:
        briefs = retry_briefs(
            record,
            child_ids=list(args.child or []),
            only_unfinished=not bool(args.all),
            tier=str(args.model_tier or ""),
            max_turns=int(args.max_turns or 0),
            budget_tokens=int(args.budget or 0),
            timeout_s=float(args.timeout or 0.0),
            role=role,
        )
    except SubagentError as exc:
        return cli_contract_error(
            str(exc),
            hint=f"`skaldir agent show {source_id}` lists that run's children and their status",
        )
    handle, blocked = _open_or_block(args)
    if handle is None:
        return blocked
    printer = _stream_printer(bool(args.stream), json_mode=json_mode)
    new_id = str(args.new_run_id or f"{source_id}-retry")
    try:
        run = SubagentRun(
            provider=handle.provider,
            briefs=briefs,
            state_dir=state,
            run_id=new_id,
            parallel=int(args.parallel),
            run_budget_tokens=int(args.run_budget),
            on_event=printer,
            workdir_base=str(args.workdir),
            context_chars=int(args.context_chars),
            provider_summary=handle.summary(),
            max_depth=int(args.max_depth),
            retried_from=source_id,
            stop_grace_s=float(args.stop_grace),
        )
    except (OSError, SubagentError) as exc:
        handle.close()
        return cli_contract_error(
            f"cannot start the retry under {state}: {type(exc).__name__}: {exc}",
            hint="check that the state directory is writable (--state-dir) and that --run-id is "
            "free (a retry never overwrites the run it re-runs)",
        )
    if not json_mode:
        print(
            f"retry {run.run_id}: {len(briefs)} child(ren) of {source_id} "
            f"(depth {briefs[0].depth}, cap {int(args.max_depth)}), state {run.run_dir}",
            file=sys.stderr,
        )
    try:
        retried = run.run()
    finally:
        handle.close()
    payload = retried.as_dict()
    payload["state_dir"] = str(state)
    payload["run_dir"] = str(run.run_dir)
    payload["source_run"] = {
        "run_id": source_id,
        "status": record.get("status"),
        "children_total": record.get("children_total"),
        "children_delivered": record.get("children_delivered"),
        "failure_classes": record.get("failure_classes") or {},
        "retried_children": [brief.child_id for brief in briefs],
        "transcripts": {
            brief.child_id: str(store.transcript_path(source_id, brief.child_id))
            for brief in briefs
        },
    }
    cli_contract_set_data(payload)
    _run_evidence(payload, run.run_dir, state=state)
    cli_contract_evidence(
        str(store.run_dir(source_id) / "run.json"),
        "file",
        f"the source run this retry re-runs the unfinished children of ({source_id})",
    )
    for child_id, path in payload["source_run"]["transcripts"].items():
        cli_contract_evidence(
            str(path), "file", f"child {child_id}'s FIRST attempt's transcript (kept, not replaced)"
        )
    cli_contract_evidence(
        "skaldir agent retry",
        "run",
        f"re-ran {len(briefs)} child(ren) of {source_id}: "
        f"{payload['children_delivered']}/{payload['children_total']} delivered on the retry",
    )
    if not json_mode:
        for line in _child_lines(payload) + [""] + _timing_lines(payload.get("timing") or {}):
            print(line, file=sys.stderr)
        print(
            f"source {source_id}: {payload['source_run']['children_delivered']}/"
            f"{payload['source_run']['children_total']} delivered, failure classes "
            f"{payload['source_run']['failure_classes']}",
            file=sys.stderr,
        )
        for note in payload.get("notes") or []:
            print(f"note: {note}", file=sys.stderr)
    if int(payload["children_delivered"]) == int(payload["children_total"]):
        return EXIT_OK
    still = [str(item) for item in payload.get("children_not_delivered") or []]
    cli_contract_warn("the retry did not fix every child: " + ", ".join(still))
    if _CONTRACT is not None:
        return int(_CONTRACT.verification_failed(
            f"{len(still)} of {payload['children_total']} child(ren) still did not deliver on the "
            "retry: " + ", ".join(still),
            hint="look at failure_classes — a deadline or a budget stop needs a different "
                 "envelope, not another retry",
            details={
                "run_id": payload["run_id"],
                "retried_from": source_id,
                "not_delivered": still,
                "failure_classes": payload.get("failure_classes") or {},
            },
        ))
    return EXIT_VERIFICATION_FAILED


def cmd_agent_list(args: argparse.Namespace) -> int:
    store = RunStore(_state_dir(args))
    runs = store.list(limit=int(args.limit), status=str(args.status or ""))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "state_dir": str(store.root),
        "runs": runs,
        "count": len(runs),
    }
    cli_contract_set_data(payload)
    cli_contract_evidence(str(store.root), "file", "the subagent state directory")
    if not _json_mode(args):
        for item in runs:
            print(
                f"{item['run_id']:<14} {str(item['status']):<10} "
                f"{item['children_delivered']}/{item['children_total']} delivered "
                f"{str(item['started_at'])[:19]}  provider={item['provider']}",
                file=sys.stderr,
            )
    return EXIT_OK


def cmd_agent_show(args: argparse.Namespace) -> int:
    store = RunStore(_state_dir(args))
    try:
        record = store.load(str(args.run_id))
    except SubagentError as exc:
        return cli_contract_error(str(exc))
    if record is None:
        return _not_found(
            f"no run '{args.run_id}' under {store.root}",
            hint="`skaldir agent list` names the runs this state directory holds",
        )
    child_id = str(args.child or "")
    runs = store.list(limit=1000)
    summary = next((item for item in runs if item["run_id"] == record.get("run_id")), {})
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": record.get("run_id"),
        "status": record.get("status"),
        "complete": bool(record.get("complete")),
        "summary": summary,
        "run": record,
        "events": store.events(
            record.get("run_id") or str(args.run_id), child_id=child_id, limit=int(args.events)
        ),
    }
    if child_id:
        payload["run"] = {
            **record,
            "children": [
                item for item in (record.get("children") or []) if item.get("child_id") == child_id
            ],
        }
    run_name = str(record.get("run_id") or args.run_id)
    if bool(args.transcript):
        wanted = [child_id] if child_id else [
            str(child.get("child_id")) for child in (record.get("children") or [])
        ] or store.children_of(run_name)
        transcripts: dict[str, dict] = {}
        for name in wanted:
            path = store.transcript_path(run_name, str(name))
            lines = store.transcript(run_name, str(name), limit=int(args.transcript_tail))
            transcripts[str(name)] = {
                "path": str(path),
                "exists": path.is_file(),
                "turns": len(lines),
                "lines": lines,
            }
        payload["transcripts"] = transcripts
    cli_contract_set_data(payload)
    cli_contract_evidence(
        str(store.run_dir(run_name) / "run.json"),
        "file",
        "the run record that was read back",
    )
    cli_contract_evidence(
        str(store.run_dir(run_name) / "events.jsonl"),
        "file",
        f"{len(payload['events'])} streamed event(s) at the tail",
    )
    for name, item in (payload.get("transcripts") or {}).items():
        cli_contract_evidence(
            str(item["path"]),
            "file",
            f"child {name}'s transcript on disk ({item['turns']} turn(s))",
        )
    if not _json_mode(args):
        print(f"run {record.get('run_id')}: {record.get('status')} "
              f"({record.get('children_delivered')}/{record.get('children_total')} delivered)",
              file=sys.stderr)
        for line in _child_lines(record):
            print(line, file=sys.stderr)
        for event in payload["events"]:
            print(_human_event(event), file=sys.stderr)
        for line in _transcript_lines(payload.get("transcripts") or {}):
            print(line, file=sys.stderr)
    return EXIT_OK


def cmd_agent_collect(args: argparse.Namespace) -> int:
    store = RunStore(_state_dir(args))
    run_id = str(args.run_id)
    wait_s = float(args.wait_timeout)
    try:
        if bool(args.wait):
            record = store.wait(run_id, timeout_s=wait_s)
        else:
            record = store.load(run_id)
    except SubagentError as exc:
        return cli_contract_error(str(exc))
    if record is None:
        return _not_found(
            f"no run '{run_id}' under {store.root}",
            hint="`skaldir agent list` names the runs this state directory holds",
        )
    payload = collect_payload(record, child_id=str(args.child or ""))
    payload["state_dir"] = str(store.root)
    if payload["status"] == RUN_RUNNING and bool(args.wait):
        cli_contract_set_data(payload)
        cli_contract_evidence(str(store.run_dir(run_id) / "run.json"), "file",
                              "a live run snapshot at the wait deadline")
        cli_contract_warn(
            f"the run is still live after the {wait_s:g}s wait; the payload is a snapshot, "
            "not the finish"
        )
        _next_action(f"skaldir agent collect {run_id} --wait --wait-timeout 300",
                     "wait longer, or cancel it: skaldir agent cancel " + run_id)
        return EXIT_UNKNOWN
    cli_contract_set_data(payload)
    cli_contract_evidence(str(store.run_dir(run_id) / "run.json"), "file",
                          "the run record the results were read from")
    for item in payload["results"]:
        if item.get("transcript") and item.get("ok"):
            cli_contract_evidence(str(item["transcript"]), "file",
                                  f"child {item.get('child_id')}'s own transcript")
    if not _json_mode(args):
        print(f"run {payload['run_id']}: {payload['status']} — "
              f"{payload['delivered']}/{len(payload['results'])} delivered", file=sys.stderr)
        for item in payload["results"]:
            detail = _one_line(
                item.get("answer") or item.get("error") or item.get("stop_reason"), 80
            )
            print(f"{str(item['child_id']):<6} {str(item['status']):<18} {detail}",
                  file=sys.stderr)
    if not payload["complete"]:
        _next_action(f"skaldir agent collect {run_id} --wait",
                     "the run is still live; --wait blocks until it is terminal")
        return EXIT_OK
    if payload["delivered"] == len(payload["results"]):
        return EXIT_OK
    failed = [str(item["child_id"]) for item in payload["results"] if not item.get("ok")]
    cli_contract_warn("at least one child did not deliver — see results[].status")
    if _CONTRACT is not None:
        return int(_CONTRACT.verification_failed(
            f"{len(failed)} of {len(payload['results'])} child(ren) did not deliver: "
            + ", ".join(failed),
            hint="results[] carries each child's status, answer and transcript path",
            details={"run_id": run_id, "not_delivered": failed},
        ))
    return EXIT_VERIFICATION_FAILED


def cmd_agent_cancel(args: argparse.Namespace) -> int:
    store = RunStore(_state_dir(args))
    run_id = str(args.run_id)
    try:
        outcome = store.request_cancel(
            run_id, reason=str(args.reason or ""), by=f"cli:pid{os.getpid()}"
        )
    except SubagentError as exc:
        return cli_contract_error(str(exc))
    if outcome.status == "unknown":
        return _not_found(
            f"no run '{run_id}' under {store.root}",
            hint="`skaldir agent list` names the runs this state directory holds",
        )
    waited = float(args.wait)
    if waited > 0 and outcome.action == "signalled":
        record = store.wait(run_id, timeout_s=waited)
        if record is not None:
            outcome.status = str(record.get("status") or outcome.status)
    payload = outcome.as_dict()
    payload["schema_version"] = SCHEMA_VERSION
    payload["state_dir"] = str(store.root)
    cli_contract_set_data(payload)
    cli_contract_evidence(str(store.run_dir(run_id) / "run.json"), "file",
                          "the run record that decided the action (pid + status)")
    if outcome.action == "signalled":
        cli_contract_evidence(str(store.run_dir(run_id) / "cancel.json"), "file",
                              "the cancel request the live run polls")
    if outcome.action == "reconciled":
        cli_contract_warn(
            "the run's process was gone; the record was reconciled to 'aborted' — "
            "nothing was pretending to be cancelled"
        )
    if outcome.action == "noop":
        cli_contract_warn("the run was already terminal — nothing was cancelled")
    if not _json_mode(args):
        print(f"run {run_id}: {outcome.action} ({outcome.status}) — {outcome.note}",
              file=sys.stderr)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# envelope plumbing (through cli_shared / the product contract)
# --------------------------------------------------------------------------- #


def cli_contract_set_data(payload: Any) -> None:
    if _CONTRACT is not None:
        _CONTRACT.set_data(payload)


def cli_contract_evidence(ref: str, kind: str = "code", note: str = "") -> None:
    if _CONTRACT is not None:
        _CONTRACT.add_evidence(ref, kind, note)


def cli_contract_warn(text: str) -> None:
    if _CONTRACT is not None:
        _CONTRACT.warn(text)


def cli_contract_error(message: str, *, hint: str = "") -> int:
    if _CONTRACT is None:
        sys.stderr.write(f"error: {message}\n")
        return EXIT_USAGE
    return int(_CONTRACT.usage_error(message, hint=hint))


def _next_action(command: str, reason: str) -> None:
    if _CONTRACT is not None:
        _CONTRACT.next_action(command, reason)


def _blocked(message: str, *, hint: str = "", next_command: str = "", reason: str = "") -> int:
    return cli_shared.blocked(message, hint=hint, next_command=next_command, reason=reason)


def _not_found(message: str, *, hint: str = "") -> int:
    return cli_shared.not_found(message, hint=hint)


# --------------------------------------------------------------------------- #
# the parser (one group, six leaves)
# --------------------------------------------------------------------------- #


ROLES_HELP = "list the subagent roles: their rights, budget and the provider each is bound to"


def cmd_agent_roles(args: argparse.Namespace) -> int:
    """Every role, what it may do, and whether it is *active* (a provider + its key)."""
    from .provider_model_data import PROVIDERS
    from .subagent_roles import NAMED_ROLES, ROLE_POLICIES
    from .subagents import read_subagent_role_overrides, subagent_provider_rule

    runtime = str(getattr(args, "runtime", "") or "").strip() or None
    role_over = read_subagent_role_overrides(runtime)
    global_provider = subagent_provider_rule(runtime)
    env_map = dict(os.environ)
    rows: list[dict[str, Any]] = []
    for name in role_names():
        policy = ROLE_POLICIES[name]
        spec = role_over.get(name) or {}
        provider = str(spec.get("provider") or "").strip() or global_provider
        api_key_env = str(spec.get("api_key_env") or "").strip()
        if not api_key_env and provider in PROVIDERS:
            api_key_env = str(PROVIDERS[provider].get("api_key_env") or "")
        key_set = bool(env_map.get(api_key_env)) if api_key_env else False
        rows.append(
            {
                "role": name,
                "named": name in NAMED_ROLES,
                "when_to_use": policy.when_to_use,
                "tools": sorted(policy.tools),
                "may_spawn": bool(policy.may_spawn),
                "read_only": bool(policy.read_only),
                "budget": policy.budget.as_dict(),
                "provider": provider,
                "provider_source": (
                    "role" if spec.get("provider") else ("global" if global_provider else "none")
                ),
                "api_key_env": api_key_env,
                "key_set": key_set,
                "active": bool(provider) and key_set,
            }
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "roles": rows,
        "count": len(rows),
        "named_count": sum(1 for row in rows if row["named"]),
        "global_provider": global_provider,
    }
    cli_contract_set_data(payload)
    if not _json_mode(args):
        for row in rows:
            state = (
                "active"
                if row["active"]
                else ("needs key" if row["provider"] else "no provider")
            )
            print(
                f"{row['role']:<20} {state:<11} "
                f"provider={row['provider'] or '—':<14} "
                f"key={row['api_key_env'] or '—':<22} "
                f"spawn={'yes' if row['may_spawn'] else 'no'}",
                file=sys.stderr,
            )
    return EXIT_OK


def _leaf(agent_sub: Any, name: str, help_text: str, handler: Callable) -> argparse.ArgumentParser:
    parser = agent_sub.add_parser(name, help=help_text)
    cli_shared.share_common_flags(parser)
    parser.set_defaults(func=handler)
    return parser


def _add_run_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-id", default="", help="the run to read (see `agent list`)")
    parser.add_argument("--state-dir", default="",
                        help="subagent state dir (default <runtime>/subagents)")
    parser.add_argument("--runtime", default="", help="runtime directory (see the registry)")
    cli_shared.share_common_flags(parser)


def _build_group(sub: Any) -> Any:
    """Create the ``agent`` group and put the six verbs on it."""
    group = sub.add_parser("agent", help=GROUP_HELP, description=GROUP_DESCRIPTION)
    agent_sub = group.add_subparsers(dest="agent_command", required=True)
    _add_verbs(agent_sub)
    return group


def _add_verbs(agent_sub: Any) -> Any:
    """Add the six verbs to an ``agent`` group's subparsers action.

    Each verb is added **only when it is missing**, because the group name is not
    exclusively ours: the ENFORCE agent loop (``agent_loop_cli.py``) already owns an
    ``agent`` group with ``agent.run`` / ``agent.transcript``. A second
    ``sub.add_parser("agent")`` would die with argparse's *conflicting subparser:
    agent*, and silently dropping either half would lose commands — so both halves
    are merged onto the one group that exists.
    """
    present = getattr(agent_sub, "choices", {}) or {}
    if "spawn" in present:
        return agent_sub
    _leaf(agent_sub, "roles", ROLES_HELP, cmd_agent_roles)
    spawn = _leaf(agent_sub, "spawn", SPAWN_HELP, cmd_agent_spawn)
    spawn.add_argument("--task", action="append", default=[], metavar="TEXT",
                       help="one child per --task (repeatable)")
    spawn.add_argument("--task-file", default="", metavar="PATH",
                       help="read tasks from a file: one per line, '#' comments ignored")
    spawn.add_argument("--model-tier", choices=("small", "main"), default="small",
                       help="which model tier the children call (default small)")
    spawn.add_argument("--model", default="", metavar="MODEL_ID",
                       help="with a catalog --provider: the model id the children call "
                            "through (must be one that provider's list carries; the "
                            "first documented model is the default)")
    spawn.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS,
                       help=f"turns per child (default {DEFAULT_MAX_TURNS})")
    spawn.add_argument("--budget", type=int, default=DEFAULT_BUDGET_TOKENS, metavar="TOKENS",
                       help="estimated token budget PER CHILD (default "
                            f"{DEFAULT_BUDGET_TOKENS}; chars/4, a guard not accounting)")
    spawn.add_argument("--run-budget", type=int, default=0, metavar="TOKENS",
                       help="estimated token cap for the whole run (0 = no run cap)")
    spawn.add_argument("--parallel", type=int, default=0, metavar="N",
                       help="at most N children at once (0 = all at once)")
    spawn.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, metavar="SECONDS",
                       help=f"per-child deadline (default {DEFAULT_TIMEOUT_S:g}s)")
    spawn.add_argument("--workdir", default="", metavar="DIR",
                       help="parent for the per-child working directories "
                            "(default: inside the run directory)")
    spawn.add_argument("--context-chars", type=int, default=DEFAULT_CONTEXT_CHARS,
                       help="how much of its own transcript a child carries into the next turn")
    spawn.add_argument("--provider", default="auto", metavar="MODE_OR_PROVIDER",
                       help="auto = the configured OpenAI-compatible client; "
                            "stub = deterministic offline provider (no model is called); "
                            "or a provider-catalog id (e.g. deepseek, openai) to give the "
                            "children their own endpoint (refused by name when unknown)")
    spawn.add_argument("--stub-latency", type=float, default=0.05, metavar="SECONDS",
                       help="--provider stub: simulated latency per call")
    spawn.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH, metavar="N",
                       help="how deep children may nest: 0 = never, 1 (default) = a child of "
                            "this run may not spawn children, 2 = it may spawn grandchildren "
                            f"which may not nest further (hard ceiling {MAX_DEPTH_LIMIT})")
    spawn.add_argument("--stop-grace", type=float, default=DEFAULT_STOP_GRACE_S, metavar="SECONDS",
                       help="after a cancel, how long the collector waits for the children to "
                            f"stop themselves before it settles them (default "
                            f"{DEFAULT_STOP_GRACE_S:g}s)")
    spawn.add_argument("--role", default="", metavar="ROLE", help=ROLE_HELP)
    spawn.add_argument("--stream", action="store_true",
                       help="print every event as it arrives (stderr; JSONL in --json mode)")
    _add_run_flags(spawn)

    retry = _leaf(agent_sub, "retry", RETRY_HELP, cmd_agent_retry)
    retry.add_argument("run_id", metavar="RUN_ID", help="the earlier run to retry")
    retry.add_argument("--child", action="append", default=[], metavar="CHILD_ID",
                       help="only this child (repeatable); default: every child that did not "
                            "deliver")
    retry.add_argument("--all", action="store_true",
                       help="re-run the delivered children too (a fresh run, not a retry)")
    retry.add_argument("--parallel", type=int, default=0, metavar="N",
                       help="at most N children at once (0 = all at once)")
    retry.add_argument("--model-tier", choices=("small", "main"), default="",
                       help="override the tier (default: each child keeps its own)")
    retry.add_argument("--model", default="", metavar="MODEL_ID",
                       help="with a catalog --provider: the model id the children call "
                            "through (same rule as agent spawn --model)")
    retry.add_argument("--max-turns", type=int, default=0,
                       help="override the turn cap (default: each child keeps its own)")
    retry.add_argument("--budget", type=int, default=0, metavar="TOKENS",
                       help="override the per-child token budget (default: its own)")
    retry.add_argument("--run-budget", type=int, default=0, metavar="TOKENS",
                       help="estimated token cap for the whole retry (0 = no run cap)")
    retry.add_argument("--timeout", type=float, default=0.0, metavar="SECONDS",
                       help="override the per-child deadline (default: its own)")
    retry.add_argument("--workdir", default="", metavar="DIR",
                       help="parent for the per-child working directories")
    retry.add_argument("--context-chars", type=int, default=DEFAULT_CONTEXT_CHARS,
                       help="how much of its own transcript a child carries into the next turn")
    retry.add_argument("--provider", choices=("auto", "stub"), default="auto",
                       help="auto = the configured OpenAI-compatible client; "
                            "stub = deterministic offline provider (no model is called)")
    retry.add_argument("--stub-latency", type=float, default=0.05, metavar="SECONDS",
                       help="--provider stub: simulated latency per call")
    retry.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH, metavar="N",
                       help="the retry run's own nesting cap (see `agent spawn --max-depth`)")
    retry.add_argument("--stop-grace", type=float, default=DEFAULT_STOP_GRACE_S, metavar="SECONDS",
                       help="how long a cancel waits for the children to stop themselves")
    retry.add_argument("--role", default="", metavar="ROLE",
                       help=f"re-role every retried child (default: each child keeps the role "
                            f"it was recorded with) — {ROLE_HELP}")
    retry.add_argument("--stream", action="store_true",
                       help="print every event as it arrives (stderr; JSONL in --json mode)")
    retry.add_argument("--new-run-id", default="", metavar="ID",
                       help="the retry run's own id (default <RUN_ID>-retry)")
    retry.add_argument("--state-dir", default="")
    retry.add_argument("--runtime", default="")
    cli_shared.share_common_flags(retry)

    listing = _leaf(agent_sub, "list", LIST_HELP, cmd_agent_list)
    listing.add_argument("--limit", type=int, default=20, help="how many runs (newest first)")
    listing.add_argument("--status", default="", help="only runs in this status")
    listing.add_argument("--state-dir", default="")
    listing.add_argument("--runtime", default="")
    cli_shared.share_common_flags(listing)

    show = _leaf(agent_sub, "show", SHOW_HELP, cmd_agent_show)
    show.add_argument("run_id", metavar="RUN_ID", help="the run to show")
    show.add_argument("--child", default="", help="only this child")
    show.add_argument("--events", type=int, default=MAX_EVENTS_DEFAULT,
                      help=f"how many streamed events to include (default {MAX_EVENTS_DEFAULT})")
    show.add_argument("--transcript", action="store_true",
                      help="also read the children's transcripts straight off disk (this works "
                           "while the run is still live)")
    show.add_argument("--transcript-tail", type=int, default=0, metavar="N",
                      help="with --transcript: only the last N turns per child (0 = all)")
    show.add_argument("--state-dir", default="")
    show.add_argument("--runtime", default="")
    cli_shared.share_common_flags(show)

    collect = _leaf(agent_sub, "collect", COLLECT_HELP, cmd_agent_collect)
    collect.add_argument("run_id", metavar="RUN_ID", help="the run to collect")
    collect.add_argument("--child", default="", help="only this child")
    collect.add_argument("--wait", action="store_true", help="block until the run is terminal")
    collect.add_argument("--wait-timeout", type=float, default=60.0, metavar="SECONDS",
                         help="how long --wait blocks before reporting a snapshot (exit 8)")
    collect.add_argument("--state-dir", default="")
    collect.add_argument("--runtime", default="")
    cli_shared.share_common_flags(collect)

    cancel = _leaf(agent_sub, "cancel", CANCEL_HELP, cmd_agent_cancel)
    cancel.add_argument("run_id", metavar="RUN_ID", help="the run to cancel")
    cancel.add_argument("--reason", default="", help="recorded with the cancel request")
    cancel.add_argument("--wait", type=float, default=0.0, metavar="SECONDS",
                        help="after signalling, wait this long for the run to go terminal")
    cancel.add_argument("--state-dir", default="")
    cancel.add_argument("--runtime", default="")
    cli_shared.share_common_flags(cancel)
    return agent_sub


def register_cli(subparsers: Any) -> Any:
    """Register (or extend) the ``agent`` group. Idempotent, additive, no ``cli.py`` edits.

    Returns the ``agent`` group's parser. When the name is already taken by another
    block's group, the six verbs are added to *that* group instead of registering a
    second one — see :func:`_add_verbs`.
    """
    if _CONTRACT is not None:
        cli_shared.install_contract_entries(CONTRACT_ENTRIES, _CONTRACT)
    sub = cli_shared.as_subparsers(subparsers)
    choices = getattr(sub, "choices", {}) or {}
    existing = choices.get("agent")
    if existing is None:
        return _build_group(sub)
    _add_verbs(cli_shared.as_subparsers(existing))
    return existing


def build_parser() -> argparse.ArgumentParser:
    """A parser for *just* this group, so the verbs work before the wiring lands."""
    parser = (
        _CONTRACT.ContractArgumentParser(prog="skaldir agent", description=GROUP_DESCRIPTION)
        if _CONTRACT is not None
        else argparse.ArgumentParser(prog="skaldir agent", description=GROUP_DESCRIPTION)
    )
    sub = parser.add_subparsers(dest="command", required=True)
    register_cli(sub)
    if _CONTRACT is not None:
        _CONTRACT.annotate(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir agent …`` standalone — same handlers, same envelope."""
    parser = build_parser()
    try:
        args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return EXIT_OK if not exc.code else EXIT_USAGE
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE
    if _CONTRACT is None:
        return int(handler(args) or 0)
    return int(_CONTRACT.run_command(args, handler))


# --------------------------------------------------------------------------- #
# the contract entries (cli_contract.py is owned by the CLI owner)
# --------------------------------------------------------------------------- #

WIRING_SNIPPET = """\
# --- src/<pkg>/cli.py, right after `sub = parser.add_subparsers(...)` (top of build_parser)
from .subagents_cli import register_cli as _register_agent_cli

_register_agent_cli(sub)
"""

CONTRACT_ENTRIES: dict[str, Any] = {}
if _CONTRACT is not None:
    CONTRACT_ENTRIES = {
        "agent.roles": _CONTRACT.CommandSpec(
            layer="PERSIST",
            evidence=(
                _evidence_row(
                    "src/skaldir/subagent_roles.py",
                    "code",
                    "ROLE_POLICIES — each role's tool rights, spawn ceiling and budget",
                ),
                _evidence_row(
                    "src/skaldir/subagents.py",
                    "code",
                    "read_subagent_role_overrides — the per-role provider/api-key settings map",
                ),
            ),
            description=(
                "list the subagent roles: their rights, budget and the provider each is "
                "bound to"
            ),
            exit_codes=(0, 7),
            mutates=False,
            data_schema={
                "type": "object",
                "properties": {
                    "roles": {"type": "array"},
                    "count": {"type": "integer"},
                    "named_count": {"type": "integer"},
                    "global_provider": {"type": "string"},
                },
            },
            notes=(
                "each row carries the provider source (role/global/none), the api_key_env it "
                "reads, and active = a provider + its key present in the environment"
            ),
        ),
        "agent.spawn": _CONTRACT.CommandSpec(
            layer="PERSIST",
            evidence=(
                _evidence_row("src/skaldir/subagents.py", "code",
                    "SubagentRun.run — one thread per child, one shared provider client, "
                    "per-child budget/turn/deadline envelope"),
                _evidence_row("MVP/28-subagent-harness.md", "verification",
                    "the measured spawn-latency table (p50/p95) and the process-per-child "
                    "comparison this command replaces"),
            ),
            description="spawn N children in parallel (one shared provider client per run) and "
                        "collect a structured result per child",
            exit_codes=(0, 1, 2, 3, 7),
            mutates=True,
            data_schema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "status": {"type": "string"},
                    "partial": {"type": "boolean"},
                    "depth": {"type": "integer"},
                    "max_depth": {"type": "integer"},
                    "queue_max": {"type": "integer"},
                    "children_total": {"type": "integer"},
                    "children_delivered": {"type": "integer"},
                    "children_not_delivered": {"type": "array"},
                    "failure_classes": {"type": "object"},
                    "cost": {"type": "object"},
                    "nesting": {"type": "object"},
                    "duration_ms": {"type": "number"},
                    "timing": {"type": "object"},
                    "provider": {"type": "object"},
                    "run_dir": {"type": "string"},
                    "children": {"type": "array"},
                },
            },
            notes="exit 2 means the run finished but at least one child did not deliver — the "
                  "other children's results are still in data.children; exit 3 means no "
                  "provider is configured (never a silently faked child); --provider stub "
                  "runs offline and says so in data.provider.mode and in a warning; "
                  "--parallel N queues the rest (data.timing.queue_max_concurrent and "
                  "data.children[].queued report the queue); --max-depth caps nesting "
                  "(default 1 = children of this run may not spawn children, refusals are in "
                  "data.nesting.refused)",
        ),
        "agent.list": _CONTRACT.CommandSpec(
            layer="PERSIST",
            evidence=(
                _evidence_row(
                    "src/skaldir/subagents.py", "code", "RunStore.list — reads run.json"
                ),
            ),
            description="list the runs in the subagent state directory, newest first",
            exit_codes=(0, 1, 7),
            data_schema={
                "type": "object",
                "properties": {
                    "state_dir": {"type": "string"},
                    "count": {"type": "integer"},
                    "runs": {"type": "array"},
                },
            },
        ),
        "agent.show": _CONTRACT.CommandSpec(
            layer="PERSIST",
            evidence=(
                _evidence_row("src/skaldir/subagents.py", "code",
                    "RunStore.load + RunStore.events — the run record and events.jsonl"),
            ),
            description="show one run: its children, per-turn transcripts and the streamed events",
            exit_codes=(0, 1, 5, 7),
            data_schema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "status": {"type": "string"},
                    "run": {"type": "object"},
                    "summary": {"type": "object"},
                    "events": {"type": "array"},
                },
            },
        ),
        "agent.collect": _CONTRACT.CommandSpec(
            layer="PERSIST",
            evidence=(
                _evidence_row("src/skaldir/subagents.py", "code",
                    "collect_payload + RunStore.wait — the collector view of one run"),
            ),
            description="the collector: one structured result per child "
                        "(--wait blocks on a live run)",
            exit_codes=(0, 1, 2, 5, 7, 8),
            data_schema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "status": {"type": "string"},
                    "complete": {"type": "boolean"},
                    "partial": {"type": "boolean"},
                    "delivered": {"type": "integer"},
                    "results": {"type": "array"},
                    "timing": {"type": "object"},
                },
            },
            notes="exit 2 = the run is terminal but not every child delivered; exit 8 = --wait "
                  "hit its deadline while the run is still live (the payload is a snapshot, not "
                  "the finish); without --wait a live run is reported with complete:false, never "
                  "as done",
        ),
        "agent.cancel": _CONTRACT.CommandSpec(
            layer="PERSIST",
            evidence=(
                _evidence_row("src/skaldir/subagents.py", "code",
                    "RunStore.request_cancel — cancel.json + the pid/status reconciliation"),
            ),
            description="cancel a live run from another process (or reconcile a dead one)",
            exit_codes=(0, 1, 5, 7),
            mutates=True,
            data_schema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "action": {"type": "string"},
                    "status": {"type": "string"},
                    "pid": {"type": "integer"},
                    "pid_alive": {"type": "boolean"},
                    "note": {"type": "string"},
                },
            },
            notes="action 'signalled' = a live run will stop its children at their next turn "
                  "boundary (a thread cannot be interrupted mid-call); 'noop' = already terminal; "
                  "'reconciled' = the record said running but its process is gone, so it is "
                  "recorded as aborted instead of pretending a cancel; a run collected on the "
                  "main thread also turns SIGINT/SIGTERM into the same cancel (children stop, "
                  "the record is 'cancelled', never an abandoned 'running')",
        ),
        "agent.retry": _CONTRACT.CommandSpec(
            layer="PERSIST",
            evidence=(
                _evidence_row("src/skaldir/subagents.py", "code",
                    "retry_briefs — the unfinished children's own briefs, linked by retried_from"),
                _evidence_row("MVP/28-subagent-harness.md", "verification",
                    "the measured retry run: only the failed children re-run, the first "
                    "attempt's transcripts kept as evidence"),
            ),
            description="re-run the children of an earlier run that did not deliver (one new "
                        "run, linked back with retried_from)",
            exit_codes=(0, 1, 2, 3, 5, 7, 8),
            mutates=True,
            data_schema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "status": {"type": "string"},
                    "retried_from": {"type": "string"},
                    "children_total": {"type": "integer"},
                    "children_delivered": {"type": "integer"},
                    "children_not_delivered": {"type": "array"},
                    "failure_classes": {"type": "object"},
                    "source_run": {"type": "object"},
                    "run_dir": {"type": "string"},
                    "children": {"type": "array"},
                },
            },
            notes="default: only the children whose status was not delivered, with their own "
                  "task/tier/budget/deadline; --child selects explicitly, --all re-runs the "
                  "delivered ones too; exit 5 = no such source run; the source run is never "
                  "modified (a retry is a new run, not a resume) and its transcripts stay on "
                  "disk as the first attempt's evidence",
        ),
    }


def contract_snippet() -> str:
    """The literal ``cli_contract.COMMANDS`` lines for the six ``agent.*`` paths."""
    lines: list[str] = []
    for command, spec in CONTRACT_ENTRIES.items():
        lines.append(f'    "{command}": _C(')
        lines.append(f'        "{spec.layer}",')
        lines.append("        (")
        for item in spec.evidence:
            lines.append(f'            _evidence_row("{item["ref"]}", "{item["kind"]}",')
            lines.append(f'                "{item["note"]}"),')
        lines.append("        ),")
        lines.append(f'        "{spec.description}",')
        lines.append(f'        mutates={spec.mutates!r}, exit_codes={spec.exit_codes!r},')
        if spec.notes:
            lines.append(f'        notes="{spec.notes}"')
        lines.append("    ),")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover — module entry point
    raise SystemExit(main())
