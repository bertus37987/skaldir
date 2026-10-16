"""``skaldir agent …`` — the agent loop from the CLI (``run`` · ``transcript``).

The group is added by the product CLI through one call:

.. code-block:: python

    # src/skaldir/cli.py, inside build_parser(), right after
    #     sub = parser.add_subparsers(dest="command", required=False)
    from .agent_loop_cli import register_cli as _register_agent_cli

    _register_agent_cli(sub)

``register_cli`` is the whole contract: it takes the CLI's own ``subparsers``
handle, adds the ``agent`` group with its two verbs, and returns the parser.
Nothing here edits ``cli.py``.

Verbs
-----

=============================  =================================================
``agent run "<task>"``          run the loop: the model asks for tools, the loop
                                executes them and feeds the results back, until
                                the model answers or a bound is hit
``agent transcript <run-id>``   read one run back: every request body (with the
                                ``tools`` array), every answer, every tool call
                                with its arguments, policy verdict, duration and
                                result digest
=============================  =================================================

Exit codes (the product's ``cli_contract`` vocabulary; adopted when that module
is importable and never re-invented) — the mapping is the loop's own, and it is
the point of the whole command:

    0   ok                 the model produced a final answer (status ``answered``)
    1   usage              bad flags, empty task, unknown tool set, unpriced cost
                           budget, a run id that is not a file name
    3   blocked            a bound was hit (``max_steps`` · ``token_budget`` ·
                           ``cost_budget`` · ``wall_clock`` · ``cancelled``) or the
                           provider failed — **no answer is reported**, the reason
                           is named and ``data.answer`` stays null
    4   needs credentials  no usable provider configuration, or the provider
                           refused the credential
    5   not found          no such run id
    7   internal           the loop itself failed (reported, never hidden)
    8   —                  (not used: a provider timeout is bounded by the
                           provider client's own policy and surfaces as 3)

A bound never turns into a "done": :mod:`skaldir.agent_loop` builds the result and
this module only reports it (``data.status``/``data.reason``/``data.answer``).

Hand-over for the CLI owner — three lines of wiring and two contracts
---------------------------------------------------------------------

The three lines are the ``code-block`` above. The group is also responsible for
its two ``cli_contract.COMMANDS`` entries: :func:`install_contract_entries` adds
them when ``register_cli`` runs (idempotent, never overwrites), and
:data:`CONTRACTS_SNIPPET` carries the literal lines for
``src/skaldir/cli_contract.py``. ``tests/test_agent_loop_cli.py`` fails while a
path is missing from ``cli_contract.COMMANDS`` — the same invariant
``tests/test_cli_contract_dump.py`` enforces for the whole tree.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Any

from . import cli_shared
from .agent_loop import (
    REASON_INTERNAL_ERROR,
    REASON_NEEDS_CREDENTIALS,
    STATUS_ANSWERED,
    STATUS_INCOMPLETE,
    AgentLoop,
    AgentLoopError,
    LoopLimits,
    read_transcript,
    run_path,
    tool_set_for,
)
from .progress import RunProgress
from .progress import for_args as _progress_for
from .stream_render import DEFAULT_INTERVAL_MS as _STREAM_INTERVAL_MS
from .stream_render import MarkdownStream as _MarkdownStream
from .stream_render import enabled_for as _streaming_enabled_for

try:  # the product contract, adopted when present (the group also runs standalone)
    from . import cli_contract as _CONTRACT
except Exception:  # noqa: BLE001 — an older build without the module
    _CONTRACT = None  # type: ignore[assignment]

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NEEDS_CREDENTIALS = int(getattr(_CONTRACT, "EXIT_NEEDS_CREDENTIALS", 4))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))

GROUP = "agent"
TOOL_CHOICES = ("builtin", "registry", "both")
CHOICE_CHOICES = ("auto", "none", "required")


# --------------------------------------------------------------------------- #
# contract helpers (the group reports through the product contract)            #
# --------------------------------------------------------------------------- #
#
# The envelope plumbing is one implementation, in ``skaldir.cli_shared``: the goal-run
# command needs the same five helpers, and `tests/test_cli_shared.py` fails the moment a
# second ``*_cli.py`` defines a body the shared home already has. The private aliases keep
# every call site in this module unchanged.

_driven = cli_shared.driven
_report = cli_shared.report_envelope
_ev = cli_shared.ev
_human = cli_shared.human
_client_and_status = cli_shared.client_and_status


def _fail(code: int, message: str, *, hint: str = "", details: dict | None = None) -> int:
    if _CONTRACT is not None and _driven() and hasattr(_CONTRACT, "fail"):
        return int(_CONTRACT.fail(code, message, hint=hint, details=details or {}))
    sys.stderr.write(f"skaldir agent: {message}\n")
    return int(code)


def _blocked(message: str, *, reason: str, next_command: str = "", hint: str = "",
             details: dict | None = None) -> int:
    """Exit 3 through the shared helper; the reason also travels in ``details``."""
    if not _driven():
        sys.stderr.write(f"skaldir agent: blocked ({reason}): {message}\n")
        return EXIT_BLOCKED
    payload = {"reason": reason, **dict(details or {})}  # machine-readable, not prose
    return int(
        cli_shared.blocked(
            message, hint=hint, next_command=next_command, reason=reason, details=payload
        )
    )


# --------------------------------------------------------------------------- #
# shared resolution                                                            #
# --------------------------------------------------------------------------- #


def _promoted_skill_lines(runtime: Path | None) -> tuple[str, ...]:
    """The proven-procedure hints the skills store really carries (promoted only).

    The skills module is probed by importability: a runtime without the store
    (or without the module) simply contributes no lines, so the prompt never
    claims a memory surface that is not wired in this build. Shadows and plain
    candidates are never surfaced (drift protection, as in ``prompt_builder``).
    """
    if runtime is None:
        return ()
    db = Path(runtime) / "memory.db"
    if not db.is_file():
        return ()
    try:
        from .skills import STATUS_PROMOTED, SkillStore, consumption_hint
    except ImportError:  # the store is not wired in this build — say nothing
        return ()
    try:
        store = SkillStore(db)
        try:
            rows = sorted(store.all(), key=lambda item: item.name)
        finally:
            store.close()
    except Exception:  # noqa: BLE001 — a broken store must not break the prompt
        return ()
    return tuple(
        consumption_hint(skill) for skill in rows if skill.status == STATUS_PROMOTED
    )


def _registry_context(args: argparse.Namespace):
    from .registry import resolve_registry

    return resolve_registry(
        runtime=getattr(args, "runtime", "") or None,
        project=getattr(args, "workspace", "") or None,
        db=getattr(args, "db", None) or None,
    )


def _state_dir(runtime_dir: str | Path) -> Path:
    """Where this group's runs live: ``<runtime>/agent/runs/<run-id>.jsonl``."""
    return Path(runtime_dir) / "agent"


def _limits_from(args: argparse.Namespace) -> LoopLimits:
    return LoopLimits(
        max_steps=int(getattr(args, "max_steps", 64)),
        token_budget=int(getattr(args, "token_budget", 0)),
        cost_budget_usd=float(getattr(args, "cost_budget_usd", 0.0)),
        price_per_1k_tokens=float(getattr(args, "price_per_1k_tokens", 0.0)),
        wall_clock_s=float(getattr(args, "wall_clock_s", 0.0)),
    )



def _answer_renderer(args: argparse.Namespace) -> Any:
    """The content sink for this run — or ``None`` when nothing should stream.

    One rule, no guessing:

    * a human on a **terminal** gets the answer token by token, by default;
    * a **pipe, a redirect, ``--json`` or ``--quiet``** gets none of it unless ``--stream``
      asks explicitly — a machine consumer wants its one envelope, and a log wants no
      per-chunk noise (``MVP/58-streaming.md`` §4);
    * ``--no-stream`` always wins, and the answer is then printed once, complete.

    Returning ``None`` (rather than a disabled renderer) is what keeps the old behaviour
    *bit-identical* when streaming is off: the loop is handed no sink and no ``stream=True``,
    so the client answers exactly as it did before this flag existed.
    """
    if bool(getattr(args, "json", False)):
        return None  # stdout carries exactly one envelope — that is the contract
    requested = getattr(args, "stream", None)
    if requested is False:
        return None
    chat_output = bool(getattr(args, "chat_output", False))
    if requested is None and chat_output:
        requested = True
    stream = sys.stdout
    if not _streaming_enabled_for(
        stream, quiet=bool(getattr(args, "quiet", False)), requested=requested
    ):
        return None
    hz = float(getattr(args, "stream_hz", 0.0) or 0.0)
    interval_ms = int(round(1000.0 / hz)) if hz > 0 else _STREAM_INTERVAL_MS
    try:
        tty = bool(stream.isatty())
    except Exception:  # noqa: BLE001 — a stream without isatty is not a terminal
        tty = False
    if chat_output:
        tty = True
    # The markdown-lite sink (`MVP/69-streaming-look.md`): the same renderer, with the
    # answer written a *line* at a time — bold, inline code, lists and a ruled code block on
    # a terminal; and the model's own bytes, once, on a pipe (``MarkdownStream.plain``), so
    # the gate above still decides *whether* streaming happens and this only decides its look.
    return _MarkdownStream(
        stream,
        interval_ms=interval_ms,
        tty=tty,
        columns=None,
        label="streaming",
        footer=not chat_output,
        incremental=chat_output,
    )


def _render_result(data: dict[str, Any], *, streamed_text: str = "",
                   chat_output: bool = False) -> None:
    if chat_output:
        answer = str(data.get("answer") or "")
        if data.get("status") == STATUS_ANSWERED and answer:
            if not streamed_text.rstrip().endswith(answer.strip()):
                _human(answer)
        elif data.get("status") != STATUS_ANSWERED:
            _human(f"Stopped: {data.get('message') or data.get('reason') or 'no answer'}")
        # Keep the existing _fail/_blocked path for the machine-readable verdict.
        return
    _human(f"run {data['run_id']} · {data['status']}"
           + (f" · {data['reason']}" if data.get("reason") else ""))
    _human(f"  steps={data['steps']} tool_calls={data['tool_calls']} "
           f"executed={data['executed_calls']} duration_ms={data['duration_ms']}")
    _human(f"  usage={json.dumps(data['usage'], sort_keys=True)}"
           + ("  (estimated)" if data.get("usage_estimated") else ""))
    answer = str(data.get("answer") or "")
    already_shown = bool(answer.strip()) and streamed_text.rstrip().endswith(answer.strip())
    streaming = data.get("streaming") or {}
    if data.get("status") == STATUS_ANSWERED and already_shown:
        # The answer is on the screen already — it was written while the model generated
        # it. Reprinting it would double a block the terminal cannot un-write.
        _human(f"  answer: streamed above ({len(answer)} chars, "
               f"{int(streaming.get('deltas') or 0)} deltas)")
    elif data.get("status") == STATUS_ANSWERED:
        if streaming.get("streamed") and not already_shown:
            _human("  answer (only part of it streamed):")
        else:
            _human("  answer:")
        for line in str(data.get("answer") or "").splitlines() or [""]:
            _human(f"    {line}")
    else:
        _human(f"  no answer: {data.get('message') or data.get('reason')}")
    _human(f"  transcript: {data['transcript_path']}")


def _render_transcript(summary: dict[str, Any]) -> None:
    _human(f"run {summary['run_id']} · {summary['status']}"
           + (f" · {summary['reason']}" if summary.get("reason") else ""))
    _human(f"  task: {summary['task']}")
    _human(f"  workspace: {summary['workspace']} · limits "
           f"{json.dumps(summary['limits'], sort_keys=True)}")
    for request, answer in zip(summary["requests"], summary["answers"], strict=False):
        body = request.get("body") or {}
        names = [str((item.get("function") or {}).get("name", ""))
                 for item in (body.get("tools") or [])]
        _human(f"  step {request.get('step')}: request model={body.get('model')} "
               f"messages={len(body.get('messages') or [])} tools={names}")
        _human(f"    answer finish_reason={answer.get('finish_reason')} "
               f"text_chars={len(str(answer.get('text') or ''))} "
               f"tool_calls={len(answer.get('tool_calls') or [])}")
    for call in summary["tool_calls"]:
        _human(f"  tool {call.get('name')}({json.dumps(call.get('arguments'), sort_keys=True)}) "
               f"· {call.get('decision')} · ok={call.get('ok')} "
               f"· {call.get('duration_ms')}ms · sha256={str(call.get('result_sha256'))[:16]}…")
    _human(f"  status={summary['status']} reason={summary['reason'] or '-'} "
           f"answer={'yes' if summary.get('answer') else 'no'}")


# --------------------------------------------------------------------------- #
# handlers                                                                     #
# --------------------------------------------------------------------------- #


def _store_for(args: argparse.Namespace, context: Any):
    """Open the resolved tools DB (the registered-tool surface) or report why not."""
    from .tools import ToolStore

    path = Path(str(context.tools_db))
    if not path.is_file():
        return None, path
    return ToolStore(str(path)), path


def _begin_progress(progress: RunProgress, args: argparse.Namespace) -> None:
    """The immediate acknowledgment line: what is running, and where."""
    from .render import truncate

    task = str(getattr(args, "task", "") or "")
    facts: dict[str, Any] = {}
    if str(getattr(args, "run_id", "") or ""):
        facts["run"] = str(args.run_id)
    progress.begin(f'{GROUP} run · "{truncate(task, 80)}"', **facts)


def _chat_progress(progress: RunProgress, *, chat_output: bool, streamed: bool = False):
    """``progress.event`` plus one **history line per step** in the chat.

    The owner's ask: "streams and outputs each step, so there is an actual chat
    history". Each event is a line above the frame (``→ skill_act …`` / ``← ok`` /
    ``⋯ thinking``) and the live row stays in sync. Quiet RunProgress means the
    folded status lines stay out of the transcript.
    """
    base = progress.event

    def event(kind: str, fields=None, **extra):
        line = base(kind, fields, **extra)
        if chat_output:
            try:
                data = dict(fields or {})
                data.update(extra)
                handle = getattr(__import__("os"), "_skaldir_chat_front", None)
                front = handle() if callable(handle) else handle
                if front is not None:
                    # live UI present: history lines + live-row updates
                    if kind == "model.request":
                        step = data.get("step") or 1
                        _human(f"\n  ⋯ thinking (step {step})")
                    elif kind == "tool.start":
                        name = str(data.get("name") or "tool")
                        args = data.get("arguments") or {}
                        try:
                            brief = json.dumps(args, ensure_ascii=False, sort_keys=True)
                        except Exception:  # noqa: BLE001
                            brief = str(args)
                        if len(brief) > 120:
                            brief = brief[:117] + "…"
                        _human(f"  → {name}")
                        _human(f"      {brief}")
                    elif kind == "tool.end":
                        name = str(data.get("name") or "tool")
                        ok = bool(data.get("ok"))
                        ms = data.get("duration_ms")
                        note = f" · {int(ms)} ms" if ms not in (None, "") else ""
                        status = str(data.get("decision") or "")
                        label = "ok" if ok else f"FAILED{(' ' + status) if status else ''}"
                        note2 = str(data.get("note") or "")[:160]
                        _human(f"  ← {name} {label}{note}")
                        if note2 and not ok:
                            _human(f"      {note2}")
                    elif kind == "model.response":
                        calls = int(data.get("tool_calls") or 0)
                        chars = int(data.get("text_chars") or 0)
                        if calls:
                            _human(f"  ⋯ {calls} tool call(s) requested")
                        elif chars and not streamed:
                            _human(f"  ⋯ answer {chars} chars")
                    # live-row updates
                    if kind == "tool.start":
                        front.activity("tool", str(data.get("name") or "tool"))
                    elif kind == "model.request":
                        front.activity("thinking", f"step {data.get('step') or 1}")
                    elif kind == "tool.end":
                        front.activity("working", "…")
            except Exception:  # noqa: BLE001 — a broken live row never fails a run
                pass
            return line
        try:
            data = dict(fields or {})
            data.update(extra)
            if kind == "model.request":
                step = data.get("step") or 1
                _human(f"\n  ⋯ thinking (step {step})")
            elif kind == "tool.start":
                name = str(data.get("name") or "tool")
                args = data.get("arguments") or {}
                try:
                    brief = json.dumps(args, ensure_ascii=False, sort_keys=True)
                except Exception:  # noqa: BLE001
                    brief = str(args)
                if len(brief) > 120:
                    brief = brief[:117] + "…"
                # one grouped block per call: name first, payload indented
                _human(f"  → {name}")
                _human(f"      {brief}")
            elif kind == "tool.end":
                name = str(data.get("name") or "tool")
                ok = bool(data.get("ok"))
                ms = data.get("duration_ms")
                note = f" · {int(ms)} ms" if ms not in (None, "") else ""
                status = str(data.get("decision") or "")
                label = "ok" if ok else f"FAILED{(' ' + status) if status else ''}"
                note2 = str(data.get("note") or "")[:160]
                _human(f"  ← {name} {label}{note}")
                if note2 and not ok:
                    _human(f"      {note2}")
            elif kind == "model.response":
                calls = int(data.get("tool_calls") or 0)
                chars = int(data.get("text_chars") or 0)
                if calls:
                    _human(f"  ⋯ {calls} tool call(s) requested")
                elif chars and not streamed:
                    _human(f"  ⋯ answer {chars} chars")
            handle = getattr(__import__("os"), "_skaldir_chat_front", None)
            front = handle() if callable(handle) else handle
            if front is not None:
                if kind == "tool.start":
                    front.activity("tool", str(data.get("name") or "tool"))
                elif kind == "model.request":
                    front.activity("thinking", f"step {data.get('step') or 1}")
                elif kind == "tool.end":
                    front.activity("working", "…")
        except Exception:  # noqa: BLE001 — a broken live row never fails a run
            pass
        return line

    return event


def _stop_progress(progress: RunProgress, why: str) -> dict[str, Any]:
    """Close the live row **before** an early return, saying why it stopped.

    The row is a live object with a keep-alive thread: left open it goes on writing
    ``still working · no new event for N s`` long after the command has printed its refusal
    and returned. Every exit from :func:`cmd_run` after :func:`_begin_progress` therefore
    closes it — the success path through ``progress.settle`` + ``progress.close``, and every
    refusal through here. A row that outlives its command is a lie about what is running.
    """
    try:
        progress.settle("stopped", why)
    except Exception:  # noqa: BLE001 — a row that cannot be settled must not hide the refusal
        pass
    try:
        return dict(progress.close() or {})
    except Exception:  # noqa: BLE001 — and neither must a row that cannot be closed
        return {}


def _history_from(args: argparse.Namespace) -> list[dict[str, Any]]:
    """The conversation this run continues, read out of ``--previous``.

    The path is whatever ``agent run`` printed as its transcript (``data.transcript_path``),
    which is also what ``/context --transcript`` takes — one handle, not two. A path that
    cannot be read is **reported** rather than silently ignored: a chat that believes it is
    continuing a conversation and is not is the exact defect this flag exists to close.
    """
    from .agent_loop import AgentLoopError, transcript_history

    raw = str(getattr(args, "previous", "") or "").strip()
    if not raw:
        return []
    path = Path(raw).expanduser()
    try:
        return transcript_history(path)
    except (AgentLoopError, OSError, ValueError) as exc:
        raise AgentLoopError(f"--previous {path} could not be read: {exc}") from exc


def cmd_run(args: argparse.Namespace) -> int:
    """Run one task through the loop and report exactly what happened."""
    from .llm_provider import ProviderConfigError

    limits = _limits_from(args)
    workspace = Path(str(getattr(args, "workspace", ".") or ".")).expanduser()
    if not workspace.is_dir():
        return _fail(EXIT_USAGE, f"workspace is not a directory: {workspace}",
                     hint="pass --workspace <dir>")
    if limits.cost_budget_usd and not limits.price_per_1k_tokens:
        return _fail(
            EXIT_USAGE,
            "a cost budget needs a declared price (--price-per-1k-tokens)",
            hint="the loop never invents a price for a model — pass the rate you pay, "
                 "or use --token-budget instead",
        )
    # The first visible byte of the whole command: written *before* the provider is
    # resolved, so the felt latency is the CLI's own startup instead of the model's
    # answer time. It goes to stderr (a human line), never to the envelope's stdout.
    chat_output = bool(getattr(args, "chat_output", False)) and not bool(
        getattr(args, "json", False)
    )
    # Chat path: progress *events* drive the live row (tool/thinking) but the
    # folded status lines must not flood the transcript (owner: "ganz komisch").
    # A quiet RunProgress still counts/folds; only the frame's live row is the feedback.
    if chat_output:
        progress = RunProgress(stream=io.StringIO(), enabled=True, keepalive_thread=False)
    else:
        progress = _progress_for(args)
    _begin_progress(progress, args)

    try:
        client, status = _client_and_status(args)
    except ProviderConfigError as exc:
        _stop_progress(progress, "the provider refused the credential")
        return _fail(
            EXIT_NEEDS_CREDENTIALS,
            str(exc),
            hint="export SKALDIR_LLM_BASE_URL + SKALDIR_LLM_MODEL (+ SKALDIR_LLM_API_KEY), "
                 "or run: skaldir provider add",
            details={"provider": {"ready": False}},
        )

    context = _registry_context(args)
    state = _state_dir(context.runtime_dir)
    store = None
    store_path = Path(str(context.tools_db))
    discovery_note = ""
    if str(getattr(args, "tools", "builtin")) in ("registry", "both"):
        store, store_path = _store_for(args, context)
        if store is None:
            # A fresh setup must still work: "Grundfaehigkeiten muessen nach einem
            # unterstuetzten frischen Setup verfuegbar oder ueber funktionierende Discovery
            # unmittelbar aufrufbar sein." Blocking the whole run because no tool has been
            # *built* yet meant the entry was unusable on day one. The run continues with
            # the shared capabilities and gains a `tool_discovery` tool that answers the
            # question honestly instead of the run refusing to start.
            discovery_note = (
                f"no tool registry at {store_path} yet — nothing has been built or "
                f"registered; the tool_discovery tool reports this and the built-in "
                f"capabilities are available regardless"
            )
    try:
        # `tool_set_for` deliberately refuses to invent a store (a test pins that). So the
        # *caller* decides what a fresh setup means: with no registry the run gets the
        # built-in capabilities plus `tool_discovery`, instead of refusing to start.
        effective_kind = str(getattr(args, "tools", "builtin"))
        if discovery_note and effective_kind in ("both", "registry"):
            effective_kind = "builtin"
        toolset = tool_set_for(
            workspace=workspace,
            kind=effective_kind,
            store=store,
            runtime=context.runtime_dir,
        )
        if discovery_note:
            # "Lazy Loading muss Discovery -> Schema -> Ausfuehrung vollstaetig
            # unterstuetzen. Ohne verfuegbare Registry darf --agent-tools nicht
            # blockieren, sondern soll ein Modellwerkzeug 'tool_discovery' und danach
            # vollstaendige Tools ausgeben."
            from .agent_loop import AgentTool as _AgentTool

            note = discovery_note

            def _tool_discovery(query: str = "") -> dict:
                return {
                    "registered_tools": [],
                    "registry": note,
                    "available_now": sorted(
                        t.name for t in toolset.tools if t.name != "tool_discovery"
                    ),
                    "how_to_get_more": (
                        "build one with `skaldir toolbuild`, or register an existing "
                        "binary; until then the capabilities above are the full set"
                    ),
                }

            toolset.tools.append(_AgentTool(
                name="tool_discovery",
                description=(
                    "List what this run can actually call. Use this first when a "
                    "capability seems missing — it names the real tools and how to add "
                    "more, instead of guessing."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "additionalProperties": False,
                },
                handler=_tool_discovery,
                parallel_safe=True,
                mutating=False,
            ))
        if bool(getattr(args, "agent_tools", False)):
            from .agent_toolset import build_agent_toolset

            toolset = build_agent_toolset(
                workspace=workspace,
                kind=str(getattr(args, "tools", "both")),
                store=store,
                runtime=context.runtime_dir,
                project_id=str(getattr(args, "project_id", "") or ""),
                # F07 (Paket 4): the run's OWN provider client rides along — a
                # bare-need build drafts through it instead of reporting "no
                # model client configured" inside a configured run.
                llm=client,
            )
        # The prompt is data-built from the toolset that was actually assembled this
        # run — capability text can never drift from the tools array. An explicit
        # --system-prompt still wins; empty means built.
    except AgentLoopError as exc:
        _stop_progress(progress, str(exc))
        return _fail(EXIT_USAGE, str(exc))

    # The content sink: with it the answer appears *while* the model writes it, which is
    # the whole point of this path. Without it (a pipe, --json, --no-stream) nothing
    # changes — the client is not even asked to stream.
    renderer = _answer_renderer(args)
    # MVP/87: the Jev decision layer subscribed to the run's turn boundaries. Armed from
    # this runtime's own settings (system_one.enabled/provider), not from a flag on every
    # call site: an operator who turned Jev on gets its decisions in the timeline; one who
    # did not gets `blocked / provider_disabled` rows naming exactly that. `--no-jev-turns`
    # is the explicit way to run a loop with no decision layer at all.
    decisions = None
    if not bool(getattr(args, "no_jev_turns", False)):
        from .jev import wiring as jev_wiring

        decisions = jev_wiring.loop_decisions(context.runtime_dir)
    try:
        history = _history_from(args)
    except AgentLoopError as exc:
        _stop_progress(progress, str(exc))
        if decisions is not None:
            decisions.close()
        return _fail(EXIT_USAGE, str(exc))
    from .system_prompt import build_system_prompt

    skills = _promoted_skill_lines(context.runtime_dir)
    built_prompt = build_system_prompt(
        toolset,
        goal=str(args.task),
        skills=skills,
        jev_armed=decisions is not None and bool(getattr(decisions.flags, "enabled", False)),
    )
    explicit = str(getattr(args, "system_prompt", "") or "")
    loop = AgentLoop(
        client,
        toolset,
        state_dir=state,
        workspace=workspace,
        limits=limits,
        goal=str(args.task),
        system_prompt=explicit if explicit else built_prompt,
        tool_choice=str(getattr(args, "tool_choice", "auto")),
        tier=str(getattr(args, "tier", "main") or "main"),
        policy=not bool(getattr(args, "no_policy", False)),
        cancel_path=str(getattr(args, "cancel_file", "") or "") or None,
        max_result_chars=int(getattr(args, "max_result_chars", 4000)),
        progress=_chat_progress(
            progress, chat_output=chat_output, streamed=renderer is not None
        ),
        on_delta=renderer.delta if renderer is not None else None,
        on_request=renderer.arm if renderer is not None else None,
        on_answer=renderer.close_call if renderer is not None else None,
        stream=True if renderer is not None else None,
        decisions=decisions,
        history=history,
    )
    try:
        result = loop.run(str(args.task), run_id=str(getattr(args, "run_id", "") or ""))
    except AgentLoopError as exc:
        message = str(exc)
        _stop_progress(progress, message)
        if "cannot call tools" in message:
            return _blocked(message, reason="client_without_tool_calling",
                            next_command="skaldir provider doctor")
        return _fail(EXIT_USAGE, message)
    finally:
        if decisions is not None:
            decisions.close()

    render_facts = renderer.finish() if renderer is not None else {}
    progress.settle(
        result.status,
        f"{result.steps} step(s) · {result.tool_calls} tool call(s) · {result.duration_ms} ms",
    )
    progress.close()
    data = result.to_dict()
    streaming = dict(data.get("streaming") or {})
    if renderer is not None:
        streaming["render"] = render_facts
        streaming["rendered_chars"] = len(renderer.text)
        streaming["rendered"] = bool(render_facts.get("updates"))
    data["streaming"] = streaming
    data["provider"] = status
    data["limits"] = limits.to_dict()
    data["workspace"] = str(workspace)
    data["tool_set"] = str(getattr(args, "tools", "builtin"))
    # the conversation this turn continued: 0 means "an independent run" (the old shape),
    # a number means the request body really carried that many earlier messages
    data["history_messages"] = len(history)
    # MVP/87: what the decision layer did with this run — the same numbers the
    # `jev_decision` rows in the runtime's event store carry
    if decisions is not None:
        data["jev_turns"] = decisions.as_dict()
    _report(
        data,
        [
            _ev(result.transcript_path, "file",
                "the run transcript: every request body (with the tools array), every "
                "answer, every tool call with arguments, verdict, duration and result digest"),
            _ev("src/skaldir/agent_loop.py", "code",
                "the loop that produced this result (schema gen → tool_calls → execute → "
                "feed back → stop)"),
        ]
        + (
            [_ev("src/skaldir/mcpserver.py", "code",
                 "registered tools execute through its wrapper (sandboxed for generated code)")]
            if str(getattr(args, "tools", "builtin")) in ("registry", "both")
            else []
        ),
    )
    facts_line = renderer.facts_line() if renderer is not None else ""
    if facts_line and not chat_output:
        # one measured line about the stream itself, never about the answer's content
        _human(f"  {facts_line}")
    _render_result(data, streamed_text=renderer.text if renderer is not None else "",
                   chat_output=chat_output)

    if result.status == STATUS_ANSWERED:
        return EXIT_OK
    if result.reason == REASON_NEEDS_CREDENTIALS:
        return _fail(EXIT_NEEDS_CREDENTIALS, result.message,
                     hint="the provider refused the credential — check SKALDIR_LLM_API_KEY")
    if result.reason == REASON_INTERNAL_ERROR:
        return _fail(EXIT_INTERNAL, result.message,
                     hint="this is a loop failure, not a model failure")
    details = {
        "run_id": result.run_id,
        "reason": result.reason,
        "steps": result.steps,
        "tool_calls": result.tool_calls,
        "transcript": result.transcript_path,
        "answer": None,
    }
    if result.status == STATUS_INCOMPLETE:
        return _blocked(
            result.message or f"the run hit its bound ({result.reason})",
            reason=result.reason,
            next_command=f"skaldir agent transcript {result.run_id}",
            hint="raise --max-steps / --token-budget / --wall-clock-s, or continue from "
                 "the transcript — there is no answer to report",
            details=details,
        )
    return _blocked(
        result.message or "the provider call failed — no answer",
        reason=result.reason or "provider_error",
        next_command=f"skaldir agent transcript {result.run_id}",
        details=details,
    )


def cmd_transcript(args: argparse.Namespace) -> int:
    """Read one run back — the transcript is the evidence, not a summary of it."""
    context = _registry_context(args)
    state = _state_dir(context.runtime_dir)
    run_id = str(getattr(args, "run_id", "") or "")
    try:
        path = run_path(state, run_id)
    except AgentLoopError as exc:
        return _fail(EXIT_USAGE, str(exc))
    if not path.is_file():
        return _fail(
            EXIT_NOT_FOUND,
            f"no run {run_id!r} in {path.parent}",
            hint="run ids are printed by `skaldir agent run`",
        )
    summary = read_transcript(path)
    _report(
        summary,
        [
            _ev(str(path), "file", "the transcript read back"),
            _ev("src/skaldir/agent_loop.py", "code", "read_transcript — the parser"),
        ],
    )
    _render_transcript(summary)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# parser                                                                       #
# --------------------------------------------------------------------------- #


def _add_run_verb(sub: Any) -> Any:
    parser = sub.add_parser(
        "run",
        help="run one task: the model asks for tools, the loop executes and feeds back",
        description=(
            "Run the agent loop over one task. The model receives the tool schemas of the "
            "selected tool set, its tool_calls are executed (in parallel where the tool "
            "declares it safe, sequentially otherwise), every result is fed back as a "
            "role=tool message, and the loop repeats until the model answers or a bound is "
            "hit. A bound is reported as incomplete/blocked — never as an answer."
        ),
    )
    parser.add_argument("task", help="the task, in one argument")
    parser.add_argument("--chat-output", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--max-steps", type=int, default=64,
                        help="maximum number of model calls (default 8)")
    parser.add_argument("--token-budget", type=int, default=0,
                        help="stop after this many tokens (0 = unbounded)")
    parser.add_argument("--cost-budget-usd", type=float, default=0.0,
                        help="stop after this many USD (needs --price-per-1k-tokens)")
    parser.add_argument("--price-per-1k-tokens", type=float, default=0.0,
                        help="declared price per 1000 tokens; the loop never invents one")
    parser.add_argument("--wall-clock-s", type=float, default=0.0,
                        help="stop after this many seconds (0 = unbounded)")
    parser.add_argument("--tools", choices=TOOL_CHOICES, default="builtin",
                        help="tool set to expose: builtin (workspace file tools), "
                             "registry (approved specs), both")
    parser.add_argument("--agent-tools", dest="agent_tools", action="store_true",
                        help="add the layer tools (skill_expand/act/compose/verify — "
                             "all eight layers reachable, not just file work)")
    parser.add_argument("--project-id", dest="project_id", default="",
                        help="registry scope id the layer tools write under")
    parser.add_argument("--db", default="", help="explicit tools DB (registry tool set)")
    parser.add_argument("--runtime", default="", help="runtime dir (default: the registry's)")
    parser.add_argument("--workspace", default=".", help="workspace the tools may touch")
    parser.add_argument("--tier", default="main", help="model tier (small|mid|main)")
    parser.add_argument("--model", default="", help="override the model")
    parser.add_argument("--base-url", default="", help="override the provider base_url")
    parser.add_argument("--run-id", default="", help="explicit run id (default: generated)")
    parser.add_argument("--previous", default="",
                        help="continue a conversation: the transcript of the run whose "
                             "exchange this one should follow (as printed by `agent run`); "
                             "its messages are carried into this request")
    parser.add_argument("--system-prompt", default="", help="override the loop's system prompt")
    parser.add_argument("--tool-choice", choices=CHOICE_CHOICES, default="auto",
                        help="what the provider may do with the tools array")
    parser.add_argument("--no-policy", action="store_true",
                        help="skip the pre-execution policy gate (records that it was skipped)")
    parser.add_argument("--no-jev-turns", dest="no_jev_turns", action="store_true",
                        help="run with no Jev decision layer on the turn boundaries (the "
                             "decision rows in the runtime's event store are then absent)")
    parser.add_argument("--cancel-file", default="",
                        help="if this file appears, the loop stops at the next step (cancelled)")
    parser.add_argument("--max-result-chars", type=int, default=4000,
                        help="how much of one tool result is fed back to the model")
    parser.add_argument("--stream", dest="stream", action="store_true", default=None,
                        help="stream the model's answer to stdout as it is generated "
                             "(default: on when stdout is a terminal, off for a pipe, "
                             "--json or --quiet)")
    parser.add_argument("--no-stream", dest="stream", action="store_false", default=None,
                        help="wait for the whole answer, exactly as before this flag existed "
                             "(the answer is printed once, complete)")
    parser.add_argument("--stream-hz", type=float, default=0.0,
                        help="upper bound on terminal updates per second while streaming "
                             f"(0 = the {int(round(1000 / _STREAM_INTERVAL_MS))}/s default; "
                             "a pipe is never paced)")
    parser.add_argument("--progress", dest="progress", action="store_true", default=None,
                        help="write live progress lines to stderr (default: on when stderr "
                             "is a terminal; never on stdout, never in --quiet)")
    parser.add_argument("--no-progress", dest="progress", action="store_false", default=None,
                        help="write no progress lines at all")
    parser.set_defaults(func=cmd_run, command_path=f"{GROUP}.run")
    _share_flags(parser)
    return parser


def _add_transcript_verb(sub: Any) -> Any:
    parser = sub.add_parser(
        "transcript",
        help="read one run back: requests, answers, tool calls, digests",
    )
    parser.add_argument("run_id", help="the run id printed by `agent run`")
    parser.add_argument("--db", default="", help="explicit tools DB (runtime resolution)")
    parser.add_argument("--runtime", default="", help="runtime dir (default: the registry's)")
    parser.set_defaults(func=cmd_transcript, command_path=f"{GROUP}.transcript")
    _share_flags(parser)
    return parser


#: The loop's verbs: name → builder. Another group may extend the same `agent` group.
VERB_BUILDERS: tuple[tuple[str, Any], ...] = (
    ("run", _add_run_verb),
    ("transcript", _add_transcript_verb),
)


def _add_verbs(agent_sub: Any) -> Any:
    """Add the loop's verbs to an ``agent`` group's subparsers action (only when absent)."""
    for name, builder in VERB_BUILDERS:
        existing = (getattr(agent_sub, "choices", {}) or {}).get(name)
        if existing is not None:
            _share_flags(existing)  # a verb that is already there keeps the shared flags
            continue
        builder(agent_sub)
    return agent_sub


def build_agent_parser(subparsers: Any) -> Any:
    """Add the ``agent`` group to an existing ``subparsers`` handle."""
    parser = subparsers.add_parser(
        GROUP,
        help="the agent loop: model-driven tool calling (run · transcript)",
        description=(
            "The agent loop. `agent run` offers the tool schemas to an OpenAI-compatible "
            "model, executes the tool_calls it asks for, feeds the results back and stops "
            "when the model answers or a bound (steps/tokens/cost/time/cancel) is hit. "
            "`agent transcript <run-id>` reads one run back in full."
        ),
    )
    sub = parser.add_subparsers(dest="agent_command", required=True)
    _add_verbs(sub)
    _share_flags(parser)
    return parser


def _share_flags(parser: argparse.ArgumentParser) -> None:
    """The product's shared flags via ``cli_shared``; the two-flag fallback keeps the
    group usable in a build without ``cli_contract`` (a standalone ``--json`` must work)."""
    cli_shared.share_common_flags(parser)
    existing = {option for action in parser._actions for option in action.option_strings}
    for flag, help_text in (
        ("--json", "machine-readable output: one JSON envelope on stdout"),
        ("--quiet", "suppress the human lines on stderr"),
    ):
        if flag not in existing:
            parser.add_argument(flag, action="store_true", help=help_text)


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``.

    Two properties make this safe next to a sibling group and on a second call:

    * **idempotent** — each verb is added only when missing, so wiring the group in
      ``build_parser()`` *and* in a test does not raise argparse's
      "conflicting subparser: agent";
    * **additive** — the ``agent`` group name is shared with the subagent harness
      (``subagents_cli.py``: ``spawn``/``list``/``show``/``collect``/``cancel``).
      When that group already exists, ``run``/``transcript`` are added to it instead
      of registering a second ``agent`` parser, which argparse refuses outright.

    The group's ``COMMANDS`` entries are installed here (through
    ``cli_shared.install_contract_entries``), so the wiring and the contract dump
    agree in the same step.
    """
    install_contract_entries()
    sub = cli_shared.as_subparsers(subparsers)
    existing = (getattr(sub, "choices", {}) or {}).get(GROUP)
    if existing is None:
        return build_agent_parser(sub)
    _add_verbs(cli_shared.as_subparsers(existing))
    return existing


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this group's verbs (``python -m`` / tests).

    The common flags sit on the verbs as well as on the root, so
    ``agent run "…" --json`` works exactly as it does once the group is wired into
    the product CLI (where ``cli_contract.annotate`` puts ``--json`` on every leaf
    as well — ``add_common_flags`` never adds a flag twice).
    """
    parser = argparse.ArgumentParser(
        prog="skaldir agent", description="the agent loop: model-driven tool calling"
    )
    _share_flags(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    _add_verbs(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``skaldir agent …`` — returns the exit code, never raises for a request."""
    args = [str(item) for item in (sys.argv[1:] if argv is None else argv)]
    if args and args[0] == GROUP:
        args = args[1:]
    install_contract_entries()
    parser = build_parser()
    try:
        parsed = parser.parse_args(args)
    except SystemExit as exc:  # argparse already chose a code (--help exits 0)
        return EXIT_OK if not exc.code else EXIT_USAGE
    func = getattr(parsed, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_USAGE
    if _CONTRACT is not None and hasattr(_CONTRACT, "run_command"):
        return int(_CONTRACT.run_command(parsed, func))
    return int(func(parsed))


#: The ``cli_contract.COMMANDS`` entries this group hands over. The CLI owner pastes
#: the block next to the other entries; ``install_contract_entries`` already adds the
#: same data at wiring time, so the dump is consistent either way.
CONTRACTS_SNIPPET = '''
    # -- the agent loop (ENFORCE: bound stop conditions, honest completion) --- #
    # `register_cli(sub)` is called in `build_parser()` right after the root
    # `add_subparsers(...)`; the group owns its two entries below.
    "agent.run": _C("ENFORCE", (_ev("src/skaldir/agent_loop.py",
                                    note="the loop: schema gen, tool_calls, execution, bounds"),
                                _ev("tests/test_agent_loop.py", "test",
                                    note="the stub-endpoint proof of loop and bounds")),
                    "run one task through the loop (model-driven tool calling)",
                    exit_codes=(0, 1, 3, 4, 7), mutates=True, requires_credentials=False,
                    data_schema={"type": "object", "properties": {
                        "run_id": {"type": "string"}, "status": {"type": "string"},
                        "reason": {"type": "string"},
                        "answer": {"type": ["string", "null"]}, "last_text": {"type": "string"},
                        "message": {"type": "string"}, "steps": {"type": "integer"},
                        "tool_calls": {"type": "integer"}, "executed_calls": {"type": "integer"},
                        "usage": {"type": "object"}, "usage_estimated": {"type": "boolean"},
                        "cost_usd": {"type": "number"}, "duration_ms": {"type": "integer"},
                        "transcript_path": {"type": "string"}, "tools": {"type": "array"},
                        "provider": {"type": "object"}, "limits": {"type": "object"},
                        "ok": {"type": "boolean"}, "exit_code": {"type": "integer"}},
                        "required": ["run_id", "status", "answer", "steps", "exit_code"]},
                    notes="a bound (max_steps/token_budget/cost_budget/wall_clock/cancelled) "
                          "exits 3 with data.answer null — the loop never reports a bound as "
                          "an answer; exit 4 when no provider is configured or the credential "
                          "was refused"),
    "agent.transcript": _C("ENFORCE", (_ev("src/skaldir/agent_loop.py",
                                           note="read_transcript + the JSONL writer"),
                                       _ev("src/skaldir/agent_loop_cli.py",
                                           note="the group and this entry")),
                           "read one run back (requests, answers, tool calls, digests)",
                           exit_codes=(0, 1, 5, 7),
                           data_schema={"type": "object", "properties": {
                               "run_id": {"type": "string"}, "status": {"type": "string"},
                               "reason": {"type": "string"},
                               "answer": {"type": ["string", "null"]},
                               "task": {"type": "string"}, "steps": {"type": "integer"},
                               "tool_calls": {"type": "array"},
                               "tool_call_count": {"type": "integer"},
                               "requests": {"type": "array"}, "answers": {"type": "array"},
                               "events": {"type": "array"}, "usage": {"type": "object"},
                               "limits": {"type": "object"}, "tools": {"type": "array"},
                               "transcript_path": {"type": "string"}},
                               "required": ["run_id", "status", "events", "tool_calls"]},
                           notes="exit 5 for an unknown run id; the transcript holds every "
                                 "request body (with the tools array) and every tool result "
                                 "digest, so a run is inspectable, not summarised"),
'''

#: The three lines the CLI owner adds in ``build_parser()``.
WIRING_SNIPPET = '''
# --- src/skaldir/cli.py, inside build_parser(), right after
#     sub = parser.add_subparsers(dest="command", required=False)
from .agent_loop_cli import register_cli as _register_agent_cli

_register_agent_cli(sub)
'''


def _spec_pairs() -> dict[str, Any]:
    """``{command path: CommandSpec}`` built lazily (does not import the CLI itself)."""
    if _CONTRACT is None or not hasattr(_CONTRACT, "CommandSpec"):
        return {}
    C = _CONTRACT.CommandSpec
    return {
        f"{GROUP}.run": C(
            "ENFORCE",
            (
                _ev("src/skaldir/agent_loop.py",
                    note="the loop: schema gen, tool_calls, execution, bounds"),
                _ev("tests/test_agent_loop.py", "test",
                    note="the stub-endpoint proof of loop and bounds"),
            ),
            "run one task through the loop (model-driven tool calling)",
            exit_codes=(0, 1, 3, 4, 7),
            mutates=True,
            data_schema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "status": {"type": "string"},
                    "reason": {"type": "string"},
                    "answer": {"type": ["string", "null"]},
                    "steps": {"type": "integer"},
                    "tool_calls": {"type": "integer"},
                    "executed_calls": {"type": "integer"},
                    "usage": {"type": "object"},
                    "cost_usd": {"type": "number"},
                    "duration_ms": {"type": "integer"},
                    "transcript_path": {"type": "string"},
                    "tools": {"type": "array"},
                    "provider": {"type": "object"},
                    "limits": {"type": "object"},
                    "ok": {"type": "boolean"},
                    "exit_code": {"type": "integer"},
                    "streaming": {
                        "type": "object",
                        "description": "what the streamed-token path measured: deltas, chars, "
                                       "first_content_ms (provider counts) plus the render "
                                       "side's updates/bytes_written; streamed=false carries "
                                       "the reason it did not stream",
                    },
                },
                "required": ["run_id", "status", "answer", "steps", "exit_code"],
            },
            notes="a bound (max_steps/token_budget/cost_budget/wall_clock/cancelled) exits 3 "
                  "with data.answer null — the loop never reports a bound as an answer; exit 4 "
                  "when no provider is configured or the credential was refused. With "
                  "--stream (a terminal) the answer is written as the model generates it and "
                  "data.streaming carries the measured numbers; the token count is an "
                  "estimate (chars/4) while deltas and millisecond stamps are measured",
        ),
        f"{GROUP}.transcript": C(
            "ENFORCE",
            (
                _ev("src/skaldir/agent_loop.py", note="read_transcript + the JSONL writer"),
                _ev("src/skaldir/agent_loop_cli.py", note="the group and this entry"),
            ),
            "read one run back (requests, answers, tool calls, digests)",
            exit_codes=(0, 1, 5, 7),
            data_schema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "status": {"type": "string"},
                    "reason": {"type": "string"},
                    "answer": {"type": ["string", "null"]},
                    "task": {"type": "string"},
                    "steps": {"type": "integer"},
                    "tool_calls": {"type": "array"},
                    "tool_call_count": {"type": "integer"},
                    "requests": {"type": "array"},
                    "answers": {"type": "array"},
                    "events": {"type": "array"},
                    "usage": {"type": "object"},
                    "limits": {"type": "object"},
                    "tools": {"type": "array"},
                    "transcript_path": {"type": "string"},
                },
                "required": ["run_id", "status", "events", "tool_calls"],
            },
            notes="exit 5 for an unknown run id; the transcript holds every request body "
                  "(with the tools array) and every tool result digest",
        ),
    }


def install_contract_entries() -> list[str]:
    """Add this group's ``COMMANDS`` entries (idempotent, never overwrites).

    Keeps ``tests/test_cli_contract_dump.py`` ("a new command cannot ship without a
    contract") green in the window between wiring ``register_cli`` and pasting
    :data:`CONTRACTS_SNIPPET` into ``cli_contract.py``.
    """
    if _CONTRACT is None or not hasattr(_CONTRACT, "COMMANDS"):
        return []
    return list(cli_shared.install_contract_entries(_spec_pairs(), _CONTRACT))


__all__ = [
    "CONTRACTS_SNIPPET",
    "WIRING_SNIPPET",
    "build_agent_parser",
    "build_parser",
    "cmd_run",
    "cmd_transcript",
    "install_contract_entries",
    "main",
    "register_cli",
]


if __name__ == "__main__":  # python -m skaldir.agent_loop_cli agent run "…"
    raise SystemExit(main())
