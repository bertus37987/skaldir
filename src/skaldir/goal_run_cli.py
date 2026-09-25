"""``skaldir run "<goal>"`` — the product command that drives the goal-locked loop.

One command, one goal, no completion without verification. It is the CLI half of
:mod:`skaldir.goal_run`; the engine holds the behaviour and this module holds the
envelope, the flags and the exit codes.

::

    skaldir run "<goal>"                     open a new goal-locked run
    skaldir run --resume <goal-id>           continue a goal from its durable checkpoint
    skaldir run "<goal>" --criteria plan.json  criteria from a document, not a guess

What the command guarantees, and where each guarantee lives:

* **goal + criteria** are recorded durably before the first model call
  (:class:`skaldir.runs_store.RunsStore`) — the goal record, its criteria, its budget
  rows and its first checkpoint.
* **the plan is real** — the first turn asks the model for a numbered plan, while
  :func:`skaldir.verify_service.build_plan` derives the check that will certify every
  criterion; ``--baseline`` (on by default) executes those checks once so the starting
  state is measured, not assumed.
* **the model may only request completion** — every answered turn becomes a
  ``CompletionRequest``, the declared checks really run and
  :class:`skaldir.completion.GateRunner` decides. ``VERIFIED_COMPLETE`` is the only
  value that means anything.
* **failure continues** — ``INCOMPLETE``/``UNKNOWN`` becomes ``REDIRECT`` with the named
  blockers, a durable checkpoint is written (plus a unified workspace snapshot unless
  ``--no-snapshot``), and the next turn works the goal with a refreshed context; after a
  crash, ``--resume <goal-id>`` picks it up.
* **the verdict is the last word** — :func:`skaldir.verify_service.run_plan` runs the
  workspace's declared checks. Exit ``0`` happens **only** on ``PASS``.

Exit codes (the product's vocabulary):

    0   ok                   verification PASSED on every blocking criterion
    1   usage                bad flags, no criteria, a workspace that is not a directory
    2   verification failed  a completion was accepted but the independent pass did not
                             reach PASS before the bound
    3   blocked              the bound was reached with no accepted completion: the goal
                             is parked ``BLOCKED_EXTERNAL`` and the unmet criterion is
                             named — never reported as a pass
    4   needs credentials    no usable provider configuration
    5   not found            ``--resume`` named a goal this store does not hold
    7   internal             the run itself failed (reported, never hidden)

Wiring (the CLI owner): two lines in :func:`skaldir.cli.build_parser`, next to the other
groups — ``register_cli`` installs its own ``cli_contract.COMMANDS`` entry, so the dump
cannot drift from the parser::

    from .goal_run_cli import register_cli as _register_goal_run

    _register_goal_run(sub)
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Any

from . import cli_shared
from .env import FRAME_ENV
from .goal_run import (
    EXIT_BLOCKED,
    EXIT_CONFLICT,
    EXIT_INTERNAL,
    EXIT_NEEDS_CREDENTIALS,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_USAGE,
    EXIT_VERIFICATION_FAILED,
    GoalRunError,
    GoalRunOptions,
    load_criteria_file,
    run_goal,
)
from .progress import for_args as _progress_for

try:  # the product contract, adopted when present (the module also runs standalone)
    from . import cli_contract as _CONTRACT
except Exception:  # noqa: BLE001 — an older build without the module
    _CONTRACT = None  # type: ignore[assignment]

GROUP = "run"
COMMAND = "run"

COMMAND_HELP = (
    "drive one goal to verification: plan, work through the agent loop, run the declared "
    "checks, enforce the completion gate, continue on failure — exit 0 only on VERIFY PASS"
)

ASKER_CHOICES = ("provider", "fixture", "off")


# --------------------------------------------------------------------------- #
# contract helpers                                                              #
# --------------------------------------------------------------------------- #
#
# The five envelope helpers live in ``skaldir.cli_shared`` (one home, not a copy per
# group — `tests/test_cli_shared.py` enforces it); the private aliases keep the call
# sites below identical to the other groups'.

_driven = cli_shared.driven
_report = cli_shared.report_envelope
_ev = cli_shared.ev
_human = cli_shared.human
_client_and_status = cli_shared.client_and_status


def _fail(code: int, message: str, *, hint: str = "", details: dict | None = None) -> int:
    if _CONTRACT is not None and _driven() and hasattr(_CONTRACT, "fail"):
        return int(_CONTRACT.fail(code, message, hint=hint, details=details or {}))
    sys.stderr.write(f"skaldir run: {message}\n")
    return int(code)


def _blocked(message: str, *, reason: str, next_command: str = "", hint: str = "",
             details: dict | None = None) -> int:
    if not _driven():
        sys.stderr.write(f"skaldir run: blocked ({reason}): {message}\n")
        return EXIT_BLOCKED
    payload = {"reason": reason, **dict(details or {})}
    return int(
        cli_shared.blocked(
            message, hint=hint, next_command=next_command, reason=reason, details=payload
        )
    )


# --------------------------------------------------------------------------- #
# resolution                                                                    #
# --------------------------------------------------------------------------- #


def _registry_context(args: argparse.Namespace):
    from .registry import resolve_registry

    return resolve_registry(
        runtime=getattr(args, "runtime", "") or getattr(args, "state", "") or None,
        project=getattr(args, "workspace", "") or None,
        db=None,
    )


def _asker_provenance(asker: Any) -> dict[str, Any]:
    """Which decision layer the compaction asker is, in **names only**.

    ``api_key_env`` is the *variable's name* — never a value, and nothing here reads the
    environment. An empty object is not a measurement, so an asker with no provenance
    answers ``{}`` (the report then carries no ``compaction_asker`` key at all) while the
    fixture — which *is* a simulation — answers ``source: fixture``, ``simulation: true``.
    """
    if asker is None:
        return {}
    fields: dict[str, Any] = {
        "source": str(getattr(asker, "source", "") or ""),
        "decision_layer": str(getattr(asker, "decision_layer", "") or ""),
        "model": str(getattr(asker, "model", "") or ""),
        "api_key_env": str(getattr(asker, "api_key_env", "") or ""),
        "tier": str(getattr(asker, "tier", "") or ""),
    }
    if getattr(asker, "simulation", False):
        fields["simulation"] = True
    return fields if any(str(value).strip() for value in fields.values()) else {}


def _asker_for(args: argparse.Namespace) -> Any:
    """The compaction asker the caller asked for: the model, a simulation, or none."""
    from .context_manager import AskerUnavailable, build_provider_asker, fixture_asker

    which = str(getattr(args, "compaction_asker", "off") or "off")
    if which == "off":
        return None
    if which == "fixture":
        asker = fixture_asker(float(getattr(args, "probability", 0.05) or 0.05))
        # the simulation is labelled wherever the real asker is: in the report and on stderr —
        # a fixture answer must never read as a model's judgement
        try:
            # a plain attribute on an asker object (like `system_one_adapters` does with the
            # §17 `code`) — the JevAsker protocol does not carry provenance, and a mock that
            # refuses attributes must not break the run
            setattr(asker, "source", "fixture")  # noqa: B010
            setattr(asker, "simulation", True)  # noqa: B010
        except Exception:  # noqa: BLE001 — an asker that refuses attributes still reports
            pass
        # a simulation is labelled as one, in the same place the real asker is labelled
        sys.stderr.write(
            "skaldir run: compaction asker · source=fixture (SIMULATION — no model judged "
            "these compactions)\n"
        )
        return asker
    try:
        # the cheapest configured tier: keep/shorten/drop is a cheap decision (see
        # `llm_provider.cheap_decision_tier`) — a block with no declared tier still gets main
        asker = build_provider_asker()
    except AskerUnavailable as exc:
        sys.stderr.write(
            f"skaldir run: no compaction asker ({exc}) — running without compaction\n"
        )
        return None
    provenance = _asker_provenance(asker)
    # one line, where the asker is built: the *Jev role's* block is the one that judged the
    # compactions, and before this the report named neither its source nor its layer.
    sys.stderr.write(
        "skaldir run: compaction asker · "
        + " · ".join(f"{key}={value}" for key, value in provenance.items() if value)
        + "\n"
    )
    return asker


def _options_from(args: argparse.Namespace, state_dir: Path) -> GoalRunOptions:
    criteria: list[dict[str, Any]] = []
    criteria_file = str(getattr(args, "criteria", "") or "")
    if criteria_file:
        criteria = load_criteria_file(criteria_file)
    return GoalRunOptions(
        goal=str(getattr(args, "goal", "") or ""),
        workspace=str(getattr(args, "workspace", ".") or "."),
        state_dir=str(state_dir),
        session=str(getattr(args, "session", "") or "goal-run"),
        criteria=criteria,
        criteria_file=criteria_file,
        resume_goal_id=str(getattr(args, "resume", "") or ""),
        max_turns=int(getattr(args, "max_turns", 6) or 6),
        max_steps=int(getattr(args, "max_steps", 64) or 64),
        wall_clock_s=float(getattr(args, "wall_clock_s", 900.0) or 0.0),
        check_timeout=float(getattr(args, "check_timeout", 300.0) or 300.0),
        baseline=not bool(getattr(args, "no_baseline", False)),
        snapshot=not bool(getattr(args, "no_snapshot", False)),
        context_budget_tokens=int(getattr(args, "context_budget", 0) or 0),
        context_trigger_fraction=float(getattr(args, "context_trigger", 0.30) or 0.30),
        preserve_recent_messages=int(getattr(args, "preserve_recent", 1) or 1),
        asker_probability=float(getattr(args, "probability", 0.05) or 0.05),
        lease_ttl=float(getattr(args, "lease_ttl", 900.0) or 900.0),
        max_result_chars=int(getattr(args, "max_result_chars", 6000) or 6000),
        tier=str(getattr(args, "tier", "main") or "main"),
    )


# --------------------------------------------------------------------------- #
# rendering                                                                     #
# --------------------------------------------------------------------------- #


def _render(report: dict[str, Any]) -> None:
    criteria = report.get("criteria") or []
    verify = report.get("verify") or {}
    counts = verify.get("counts") or {}
    _human(f"run {report.get('goal_id')} · {report.get('terminal_state')} "
           f"· verify {verify.get('verdict') or '(not run)'}")
    _human(f"  goal: {str(report.get('goal') or '')[:160]}")
    _human(f"  workspace={report.get('workspace')} session={report.get('session')}")
    _human(f"  criteria={len(criteria)} turns={report.get('turns_run')} "
           f"completion attempts={len(report.get('completions') or [])} "
           f"accepted={report.get('accepted_completions')} "
           f"compactions={len(report.get('compactions') or [])}")
    if report.get("baseline"):
        base = report["baseline"]
        _human(f"  baseline: {base.get('verdict')} "
               f"({json.dumps(base.get('counts') or {}, sort_keys=True)})")
    for row in report.get("completions") or []:
        verdict = row.get("verdict")
        mark = "accepted" if row.get("accepted") else "rejected"
        _human(f"  {row.get('turn')}: completion {verdict} ({mark})")
        for blocker in list(row.get("blocking_failures") or [])[:3]:
            _human(f"    BLOCKER: {blocker}")
    if verify:
        _human(f"  verify {verify.get('verdict')}: "
               f"{json.dumps(counts, sort_keys=True)} — {verify.get('verdict_reason')}")
        for item in verify.get("criteria") or []:
            _human(f"    [{item.get('check_id')}] {item.get('status')} · "
                   f"{str(item.get('criterion') or '')[:80]} · "
                   f"exit={item.get('exit_code')} · "
                   f"sha256={str(item.get('evidence_sha256') or '')[:16]}…")
    if report.get("blocked_reason"):
        _human(f"  blocked: {report['blocked_reason']}")
    for row in report.get("checkpoints") or []:
        _human(f"  checkpoint {row.get('turn')} {row.get('kind')} "
               f"{str(row.get('checkpoint_id') or '')[:16]}… "
               f"({str(row.get('next_action') or row.get('reason') or '')[:60]})")
    if report.get("report_path"):
        _human(f"  report: {report['report_path']}")
    for problem in report.get("runtime_errors") or []:
        _human(f"  note: {problem}")


# --------------------------------------------------------------------------- #
# the surface: `skaldir run` hands the run over to the chat                    #
# --------------------------------------------------------------------------- #
#
# The owner's rule: "`skaldir run` must automatically open the chat too, where messages can be
# added with /steer or /queue". So on a terminal the run does not own stdin/stdout any more — the
# chat does — and the run becomes a *thread* of the same process with two seams:
#
# * ``_RunControl`` — the chat asks for a stop at the next turn boundary (never mid-turn: the
#   checkpoint the last turn wrote is the honest place to stop);
# * ``_RunFeed`` — the run's progress events go into a bounded queue that the *chat's* thread
#   writes to the terminal, so exactly one thread ever touches the screen.
#
# There is no second kind of handle: the chat attaches to the goal id the engine publishes
# (``on_goal``) the moment the record is durable in ``runs.db``, and to nothing else.


class _RunControl:
    """What a surface may ask of a live run: stop at the next boundary, with a reason."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self.stop_reason = ""

    def request_stop(self, reason: str = "") -> None:
        self.stop_reason = str(reason or "stop requested")
        self._stop.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()


class _RunFeed:
    """The run's progress as chat lines: a bounded queue the chat's own thread drains.

    ``pending()`` is what :class:`skaldir.repl.Shell` polls (and what the frame polls between
    keystrokes); ``event()`` is what the engine is given as its progress callable. Nothing here
    writes to the terminal, and nothing here invents a fact: a line is only printed from the
    fields the event really carried.
    """

    #: a chat transcript, not a log file: past this many unread lines the oldest are dropped and
    #: the count of what was dropped is said once (a silent gap would be a lie)
    CAP = 200

    def __init__(self) -> None:
        self.lines: queue.Queue[str] = queue.Queue()
        self.dropped = 0
        self.closed = False
        self.started = 0.0

    # -- the engine's side --------------------------------------------------- #
    def event(self, kind: str, fields: Any = None, **extra: Any) -> str:
        """One progress event → one chat line (the engine's progress callable)."""
        data = dict(fields or {})
        data.update(extra)
        line = self._line(str(kind), data)
        if line:
            self.post(line)
        return line

    def post(self, line: str) -> None:
        """Queue one line for the chat to write (thread-safe, bounded)."""
        text = str(line).rstrip("\n")
        if not text or self.closed:
            return
        while self.lines.qsize() >= self.CAP:
            try:
                self.lines.get_nowait()
                self.dropped += 1
            except queue.Empty:  # pragma: no cover — the queue is only written here
                break
        self.lines.put(text)

    def close(self, note: str = "") -> None:
        if note:
            self.post(note)
        self.closed = True

    # -- the chat's side ----------------------------------------------------- #
    def pending(self) -> list[str]:
        """Everything queued since the last poll (the chat calls this from its own thread)."""
        out: list[str] = []
        if self.dropped:
            out.append(f"… {self.dropped} earlier run line(s) were not shown (chat busy)")
            self.dropped = 0
        while True:
            try:
                out.append(self.lines.get_nowait())
            except queue.Empty:
                break
        return out

    def _line(self, kind: str, data: dict[str, Any]) -> str:
        """One honest line per event kind — measured values only, omitted when unknown."""
        def num(name: str) -> float | None:
            value = data.get(name)
            try:
                return float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None

        if kind == "model.request":
            bits = [f"step {int(num('step') or 0)}", f"{int(num('messages') or 0)} message(s)"]
            names = data.get("tool_names") or ()
            if names:
                bits.append("tools: " + ", ".join(str(item) for item in names))
            return "  model call · " + " · ".join(bits)
        if kind == "model.response":
            bits = []
            duration = num("duration_ms")
            if duration is not None:
                bits.append(f"{duration / 1000:.1f} s")
            bits.append(f"{int(num('tool_calls') or 0)} tool call(s)")
            chars = num("text_chars")
            if chars:
                bits.append(f"{int(chars)} chars")
            return "  model answered · " + " · ".join(bits)
        if kind == "tool.start":
            name = str(data.get("name") or "tool")
            return f"  running {name}"
        if kind == "tool.end":
            name = str(data.get("name") or "tool")
            ok = bool(data.get("ok"))
            duration = num("duration_ms")
            tail = f" · {duration:.0f} ms" if duration is not None else ""
            return f"  {name} {'ok' if ok else 'failed'}{tail}"
        if kind == "model.tokens":
            chars = num("chars")
            return f"  tokens streaming · {int(chars or 0)} chars" if chars else ""
        bits = [f"{key}={data[key]}" for key in sorted(data) if str(data[key] or "") != ""]
        return f"  {kind}" + (" · " + " · ".join(bits[:4]) if bits else "")


def _chat_wanted(args: argparse.Namespace) -> bool:
    """Should this ``skaldir run`` open the chat? The rule, in one place.

    Only a real terminal on **both** ends and no explicit ``--no-chat``/``--json``/``--quiet``: a
    pipe, a script, a captured envelope or an automated caller keeps today's foreground run, and
    ``--no-chat`` is the documented escape hatch for a human who wants the old shape. A frame
    that already owns this terminal (``repl.Shell.run_command`` marks it, ``SKALDIR_FRAME``) is
    the fourth refusal: opening a chat *inside* a chat draws a second frame over the first and
    the command never returns, so the outer frame keeps its live row and never stamps a result.
    """
    if bool(getattr(args, "no_chat", False)) or bool(getattr(args, "json", False)):
        return False
    if bool(getattr(args, "quiet", False)):
        return False
    if str(os.environ.get(FRAME_ENV) or "").strip():
        return False
    try:
        return bool(sys.stdin.isatty()) and bool(sys.stdout.isatty())
    except Exception:  # noqa: BLE001 — a stream without isatty is not a terminal
        return False


def _finish_run(report: dict[str, Any], *, state_dir: Path, status: Any, progress: Any,
                asker: Any = None) -> int:
    """Report one finished run and map its verdict to the product's exit code.

    ``asker`` is the compaction asker this run used: its provenance (source, decision layer,
    model, the credential's *name*) travels in ``data.compaction_asker`` so a report says
    **which** provider judged the compactions — names only, never a secret. Absent/``off``
    stays absent (an empty object would read like a measurement).
    """
    report["provider"] = status
    provenance = _asker_provenance(asker)
    if provenance:
        report["compaction_asker"] = provenance
    verify = report.get("verify") or {}
    progress.settle(
        str(verify.get("verdict") or report.get("terminal_state") or "unknown"),
        f"{report.get('turns_run')} turn(s) · "
        f"{len(report.get('completions') or [])} completion attempt(s)",
    )
    progress.close()
    _report(
        report,
        [
            _ev(str(report.get("report_path") or ""), "file",
                "the run report: every turn, every completion attempt, every executed "
                "check and the final verdict"),
            _ev("src/skaldir/goal_run.py", "code",
                "the goal-locked engine (plan → turns → gate → verify → continue)"),
            _ev("src/skaldir/completion.py", "code",
                "the gate that decides a completion request and the checks it executes"),
            _ev("src/skaldir/verify_service.py", "code",
                "build_plan/run_plan — the independent verdict behind the exit code"),
            _ev(str(state_dir / "runs.db"), "file",
                "the durable goal record, its criteria, its checkpoints and its cursor"),
        ],
    )
    _render(report)

    # ``0`` is a real exit code: ``or`` would turn a PASS into a blocked run.
    raw_code = report.get("exit_code")
    code = int(EXIT_BLOCKED if raw_code is None else raw_code)
    if code == EXIT_OK:
        return EXIT_OK
    if code == EXIT_VERIFICATION_FAILED:
        return _fail(
            EXIT_VERIFICATION_FAILED,
            "a completion was accepted but the independent verification pass did not "
            f"reach PASS ({verify.get('verdict') or 'UNKNOWN'})",
            hint="raise --max-turns, or fix the criteria the report names",
            details={"verify": verify, "goal_id": report.get("goal_id")},
        )
    if code == EXIT_NEEDS_CREDENTIALS:
        return _fail(EXIT_NEEDS_CREDENTIALS, "the provider refused the credential")
    if code == EXIT_CONFLICT:
        return _fail(
            EXIT_CONFLICT,
            str(report.get("blocked_reason") or "another writer holds the run store"),
            hint="one writer per run store — wait for the live run, or use another --state",
        )
    if code == EXIT_BLOCKED:
        return _blocked(
            str(report.get("blocked_reason") or "the goal was not verified"),
            reason="goal_not_verified",
            next_command=f"skaldir run --resume {report.get('goal_id')}",
            hint="the goal is parked BLOCKED_EXTERNAL in the durable record; no "
                 "completion was accepted and no criterion was claimed without evidence",
            details={
                "goal_id": report.get("goal_id"),
                "terminal_state": report.get("terminal_state"),
                "turns": report.get("turns_run"),
                "verify": verify,
            },
        )
    return _fail(EXIT_INTERNAL, "the run ended without a verdict")


def _cmd_run_with_chat(args: argparse.Namespace, options: Any, state_dir: Path) -> int:
    """``skaldir run "<goal>"`` on a terminal: the goal, then the chat as its surface.

    Order matters and is the owner's: the run starts, the engine publishes the goal id once the
    record is durable, and the chat opens attached to *that* id — so the band shows the real goal
    and ``/steer`` + ``/queue`` have a real target. The run's progress streams into the chat's
    transcript (through the feed, written by the chat's own thread), and closing the chat asks the
    run to stop at its next turn boundary. If the run is over by then, its own exit code is the
    one ``skaldir run`` returns.
    """
    from . import chatbar
    from .llm_provider import ProviderConfigError

    control = _RunControl()
    feed = _RunFeed()
    holder: dict[str, Any] = {}
    ready = threading.Event()
    feed.post(f'run · "{str(options.goal)[:70]}" · workspace {options.workspace}')

    def announced(goal_id: str) -> None:
        holder["goal_id"] = str(goal_id)
        ready.set()

    def worker() -> None:
        try:
            client, status = _client_and_status(args)
        except ProviderConfigError as exc:
            holder["error"] = (EXIT_NEEDS_CREDENTIALS, str(exc))
            feed.close("the run could not start: the provider refused the credential")
            ready.set()
            return
        except Exception as exc:  # noqa: BLE001 — a broken provider layer is reported
            holder["error"] = (EXIT_INTERNAL, f"{type(exc).__name__}: {exc}")
            feed.close("the run could not start")
            ready.set()
            return
        holder["status"] = status
        try:
            asker = _asker_for(args)
            holder["asker"] = asker
            holder["report"] = run_goal(
                options,
                client=client,
                asker=asker,
                progress=feed.event,
                on_goal=announced,
                control=control,
            )
        except GoalRunError as exc:
            holder["error"] = (int(exc.code), str(exc))
        except Exception as exc:  # noqa: BLE001 — the run itself failed; say so
            holder["error"] = (EXIT_INTERNAL, f"{type(exc).__name__}: {exc}")
        finally:
            report = holder.get("report") or {}
            verdict = str((report.get("verify") or {}).get("verdict")
                          or report.get("terminal_state") or "")
            feed.close(f"run finished · {verdict}" if verdict else "run finished")
            ready.set()

    thread = threading.Thread(target=worker, name="skaldir-goal-run", daemon=False)
    thread.start()
    # bounded wait for the durable handle: the chat never claims a goal that is not in runs.db
    ready.wait(timeout=120.0)
    goal_id = str(holder.get("goal_id") or "")
    feed.post(f"goal {goal_id}" if goal_id else "no goal was recorded by this run")

    code = chatbar.launch_attached_chat(
        goal_id=goal_id,
        runtime=str(state_dir),
        feed=feed,
        session=str(getattr(args, "session", "") or "goal-run"),
        no_color=bool(getattr(args, "no_color", False)),
    )
    # the chat is over: stop at the next boundary (never mid-turn) and let the run answer
    control.request_stop("the chat was closed")
    thread.join(timeout=float(getattr(args, "wall_clock_s", 900.0) or 900.0) + 30.0)
    if thread.is_alive():  # a turn that will not end is reported, never hidden
        sys.stderr.write(
            "skaldir run: the run is still inside a turn — the goal is durable in "
            f"{state_dir / 'runs.db'} and `skaldir run --resume {goal_id or '<goal-id>'}` "
            "continues it\n"
        )
    report = holder.get("report")
    error = holder.get("error")
    if report is not None:
        return _finish_run(report, state_dir=state_dir, status=holder.get("status") or {},
                           progress=_progress_for(args), asker=holder.get("asker"))
    if error is not None:
        return _fail(int(error[0]), str(error[1]))
    sys.stderr.write(
        f"skaldir run: the chat closed before the run reported a verdict "
        f"(goal {goal_id or 'unknown'})\n"
    )
    return int(code) if code else EXIT_BLOCKED




def cmd_run(args: argparse.Namespace) -> int:
    """Drive one goal-locked run and report exactly what the checks returned."""
    from .llm_provider import ProviderConfigError

    goal_text = str(getattr(args, "goal", "") or "").strip()
    resume_id = str(getattr(args, "resume", "") or "").strip()
    if not goal_text and not resume_id:
        return _fail(
            EXIT_USAGE,
            "a goal-locked run needs a goal (or --resume <goal-id>)",
            hint='example: skaldir run "make the checks pass" --criteria plan.json',
        )
    if goal_text and resume_id:
        return _fail(
            EXIT_USAGE,
            "pass either a goal text or --resume <goal-id>, not both",
            hint="--resume continues the stored goal text",
        )
    workspace = Path(str(getattr(args, "workspace", ".") or ".")).expanduser()
    if not workspace.is_dir():
        return _fail(EXIT_USAGE, f"workspace is not a directory: {workspace}",
                     hint="pass --workspace <dir>")

    context = _registry_context(args)
    state_dir = Path(str(getattr(args, "state", "") or "") or context.runtime_dir).expanduser()

    try:
        options = _options_from(args, state_dir)
    except GoalRunError as exc:
        return _fail(int(exc.code), str(exc))

    progress = _progress_for(args)
    progress.begin(f'{GROUP} · "{goal_text[:70] or resume_id}"',
                   workspace=str(workspace), turns=int(options.max_turns))

    # On a real terminal the run hands the screen over to the chat (the owner's rule: `skaldir
    # run` sets the goal *and* opens the chat attached to it). A pipe, `--json`, `--quiet` or
    # `--no-chat` keeps the foreground run, byte for byte, below.
    if _chat_wanted(args):
        progress.close()
        return _cmd_run_with_chat(args, options, state_dir)

    try:
        client, status = _client_and_status(args)
    except ProviderConfigError as exc:
        return _fail(
            EXIT_NEEDS_CREDENTIALS,
            str(exc),
            hint="export SKALDIR_LLM_BASE_URL + SKALDIR_LLM_MODEL (+ SKALDIR_LLM_API_KEY), "
                 "or run: skaldir provider add",
            details={"provider": {"ready": False}},
        )

    try:
        asker = _asker_for(args)
        report = run_goal(
            options,
            client=client,
            asker=asker,
            progress=progress.event,
        )
    except GoalRunError as exc:
        progress.settle("blocked", str(exc)[:80])
        return _fail(int(exc.code), str(exc))
    except Exception as exc:  # noqa: BLE001 — the run itself failed; say so
        progress.settle("internal", f"{type(exc).__name__}")
        return _fail(
            EXIT_INTERNAL,
            f"the goal run failed: {type(exc).__name__}: {exc}",
            hint="the workspace and the durable goal are untouched by this failure "
                 "where the report reached the disk — see the run report",
        )

    return _finish_run(report, state_dir=state_dir, status=status, progress=progress,
                       asker=asker)


# --------------------------------------------------------------------------- #
# parser                                                                        #
# --------------------------------------------------------------------------- #


def _add_verbs(sub: Any) -> Any:
    """Add the ``run`` verb when it is not already there (idempotent wiring)."""
    existing = getattr(sub, "choices", None) or {}
    if GROUP in existing:
        return existing[GROUP]
    parser = sub.add_parser(GROUP, help=COMMAND_HELP, description=COMMAND_HELP)
    parser.add_argument("goal", nargs="?", default="",
                        help="the goal, in one sentence — what must be true when done")
    parser.add_argument("--resume", default="",
                        help="continue this goal id from its durable checkpoint")
    parser.add_argument("--criteria", default="",
                        help="criteria document (JSON list, or a .godsv/verify.json-shaped "
                             "object); without it the workspace's declared plan is used")
    parser.add_argument("--workspace", default=".", help="the project to work in")
    parser.add_argument("--session", default="goal-run",
                        help="the contract/session id this goal belongs to")
    parser.add_argument("--max-turns", dest="max_turns", type=int, default=6,
                        help="hard bound on the turns of this goal (default 6)")
    parser.add_argument("--max-steps", dest="max_steps", type=int, default=64,
                        help="steps inside one turn (default 14)")
    parser.add_argument("--wall-clock-s", dest="wall_clock_s", type=float, default=900.0,
                        help="wall-clock bound of the whole run in seconds")
    parser.add_argument("--check-timeout", dest="check_timeout", type=float, default=300.0,
                        help="per-check timeout in seconds (default 300)")
    parser.add_argument("--lease-ttl", dest="lease_ttl", type=float, default=900.0,
                        help="writer-lease lifetime of this run in seconds")
    parser.add_argument("--no-baseline", dest="no_baseline", action="store_true",
                        help="do not execute the declared checks before the first turn")
    parser.add_argument("--no-snapshot", dest="no_snapshot", action="store_true",
                        help="do not write a unified workspace checkpoint per rejection")
    parser.add_argument("--context-budget", dest="context_budget", type=int, default=0,
                        help="context budget in tokens (0 = the manager's own default)")
    parser.add_argument("--context-trigger", dest="context_trigger", type=float, default=0.30,
                        help="fraction of the budget that fires compaction (default 0.30)")
    parser.add_argument("--preserve-recent", dest="preserve_recent", type=int, default=1,
                        help="messages at the tail of the history compaction never touches")
    parser.add_argument("--compaction-asker", dest="compaction_asker",
                        choices=ASKER_CHOICES, default="provider",
                        help="provider = the real model judges the history (default); "
                             "fixture = an offline simulation, labelled as such; "
                             "off = no compaction")
    parser.add_argument("--probability", type=float, default=0.05,
                        help="the fixture asker's flat keep probability (default 0.05)")
    parser.add_argument("--max-result-chars", dest="max_result_chars", type=int, default=6000,
                        help="truncation cap for one tool result fed back to the model")
    parser.add_argument("--tier", default="main", help="the model tier to run as")
    parser.add_argument("--base-url", dest="base_url", default="",
                        help="provider base URL override")
    parser.add_argument("--model", default="", help="model override")
    parser.add_argument("--state", default="", help="runtime directory (default: SKALDIR_HOME)")
    parser.add_argument("--progress", dest="progress", action="store_true", default=None,
                        help="force the live progress lines on (they need a TTY otherwise)")
    parser.add_argument("--no-chat", dest="no_chat", action="store_true",
                        help="never hand the terminal to the chat: keep the foreground run "
                             "(the default on a pipe, a script, --json or --quiet)")
    parser.add_argument("--no-progress", dest="progress", action="store_false", default=None,
                        help="no live progress lines, whatever the terminal")
    parser.add_argument("--text", action="store_true", help="human output instead of JSON")
    parser.set_defaults(func=cmd_run, command_path=COMMAND)
    cli_shared.share_common_flags(parser)
    return parser


#: The ``cli_contract.COMMANDS`` entry this command hands over. ``install_contract_entries``
#: adds the same data at wiring time, so the dump is consistent either way.
def contract_entries() -> dict[str, Any]:
    """The ``COMMANDS`` entry for ``run`` — built here, installed on wiring."""
    contract = _CONTRACT
    if contract is None or not hasattr(contract, "CommandSpec"):
        return {}
    spec = contract.CommandSpec(
        layer="ENFORCE",
        evidence=(
            _ev("src/skaldir/goal_run.py",
                note="the goal-locked engine: plan → turns → gate → verify → continue"),
            _ev("src/skaldir/completion.py:2759", "code",
                note="GateRunner.verify — the only thing that may accept a completion"),
            _ev("src/skaldir/verify_service.py:1055", "code",
                note="run_plan — the independent verdict behind the exit code"),
            _ev("src/skaldir/runs_store.py:916", "code",
                note="start_goal + write_checkpoint: the durable goal, criteria and cursor"),
        ),
        description=COMMAND_HELP,
        exit_codes=(
            EXIT_OK, EXIT_USAGE, EXIT_VERIFICATION_FAILED, EXIT_BLOCKED,
            EXIT_NEEDS_CREDENTIALS, EXIT_NOT_FOUND, EXIT_CONFLICT, EXIT_INTERNAL,
        ),
        mutates=True,
        requires_credentials=True,
        data_schema={
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "goal": {"type": "string"},
                "workspace": {"type": "string"},
                "session": {"type": "string"},
                "resumed": {"type": "boolean"},
                "turns_run": {"type": "integer"},
                "accepted_completions": {"type": "integer"},
                "terminal_state": {"type": "string"},
                "blocked_reason": {"type": "string"},
                "exit_code": {"type": "integer"},
                "report_path": {"type": "string"},
                "criteria": {"type": "array"},
                "plan": {"type": "object"},
                "baseline": {"type": "object"},
                "turns": {"type": "array"},
                "completions": {"type": "array"},
                "checkpoints": {"type": "array"},
                "compactions": {"type": "array"},
                "compaction_asker": {
                    "type": "object",
                    "properties": {"source": {"type": "string"},
                                   "decision_layer": {"type": "string"},
                                   "model": {"type": "string"},
                                   "api_key_env": {"type": "string"},
                                   "tier": {"type": "string"},
                                   "simulation": {"type": "boolean"}},
                    "description": "which decision layer judged the compactions — the "
                                   "credential's *name*, never its value; `simulation: true` "
                                   "marks the fixture asker; absent when no asker was built"},
                "verify": {"type": "object"},
                "runtime_errors": {"type": "array"},
            },
            "required": [
                "goal_id", "workspace", "turns_run", "accepted_completions",
                "terminal_state", "verify", "exit_code",
            ],
        },
        compat_top_level_keys=False,
        notes=(
            "exit 0 is reserved for a verification PASS; a bound reached with no accepted "
            "completion parks the goal BLOCKED_EXTERNAL and exits 3 (the unmet criterion is "
            "named); a completion accepted by the gate whose independent pass still does not "
            "reach PASS exits 2. --resume continues the stored goal from its durable "
            "checkpoint (files, cursor, prior rejections) — the process may die at any point "
            "and the goal survives. --compaction-asker fixture marks every compaction as a "
            "simulation in the report; 'provider' (the default) uses the real model."
        ),
    )
    return {COMMAND: spec}


def install_contract_entries() -> list[str]:
    """Add this command's ``COMMANDS`` entry (idempotent); returns the paths added."""
    entries = contract_entries()
    if not entries or _CONTRACT is None:
        return []
    added: list[str] = []
    for path, entry in entries.items():
        if path not in _CONTRACT.COMMANDS:
            _CONTRACT.COMMANDS[path] = entry
            added.append(path)
    return added


def register_cli(subparsers: Any) -> Any:
    """The CLI envelope contract: ``register_cli(subparsers) -> parser``.

    Idempotent, like the other groups: a second call on a parser that already holds
    ``run`` hands that parser back instead of raising argparse's conflict. The
    ``COMMANDS`` entry is installed here, so wiring the command cannot leave the dump
    behind (``tests/test_cli_contract_dump.py`` fails while the path is missing).
    """
    install_contract_entries()
    sub = cli_shared.as_subparsers(subparsers)
    return _add_verbs(sub)


def build_parser() -> argparse.ArgumentParser:
    """A standalone parser with just this verb (tests / ``--help`` / ``python -m``)."""
    parser = argparse.ArgumentParser(
        prog="skaldir run", description=COMMAND_HELP
    )
    cli_shared.share_common_flags(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    _add_verbs(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """``python -m skaldir.goal_run_cli …`` — the same verb, the same exit codes."""
    args = [str(item) for item in (sys.argv[1:] if argv is None else argv)]
    if args and args[0] == GROUP:
        args = args[1:]
    install_contract_entries()
    parser = build_parser()
    try:
        parsed = parser.parse_args(args)
    except SystemExit as exc:  # argparse already chose a code (--help exits 0)
        return EXIT_OK if not exc.code else EXIT_USAGE
    handler = getattr(parsed, "func", None)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE
    if _CONTRACT is not None and hasattr(_CONTRACT, "run_command"):
        return int(_CONTRACT.run_command(parsed, handler))
    return int(handler(parsed))


__all__ = [
    "COMMAND",
    "COMMAND_HELP",
    "GROUP",
    "build_parser",
    "cmd_run",
    "contract_entries",
    "install_contract_entries",
    "main",
    "register_cli",
]
