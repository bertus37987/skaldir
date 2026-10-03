"""The workhorse coding-agent features — event stream, plan mode, test-fix loop, mentions,
turn undo, session summary, project init, mid-session model switch.

Everything in here is engine, no CLI: ``coding_features_cli.py`` is the ``skaldir coding``
group and drives these functions. The module is stdlib-only and imports nothing of ours at
module level, so it stays usable from a bare checkout as well as from the product CLI.

What it adds, and where the design comes from
=============================================

``openai/codex`` (Apache-2.0, read-only study in ``MVP/42-codex-adoption.md``; clone at
``/tmp/codex-study``) is the reference for six of the eight, but **no code was copied** —
these are Python implementations of designs that were read:

1. **Run-scoped JSON event stream.** Codex emits JSONL, one tagged object per line, with
   ``item.started`` / ``item.updated`` / ``item.completed`` carrying a ``status`` that
   separates **declined** from **failed** (``exec/src/exec_events.rs:8-35,96-167``). Ours is
   the same shape with our own vocabulary (``events.py`` kinds), and it exists because our
   envelope is one JSON document per *command*: a GUI watching a long run sees nothing until
   the end. :class:`RunStream` emits begin/progress/end events, :func:`validate_stream`
   checks the invariant that matters (every item gets exactly one terminal event), and
   :func:`stream_command` is a real runner that produces them.
2. **Plan mode.** Codex's ``collaboration-mode-templates/templates/plan.md`` is a mode with
   strict rules: explore non-mutating, ask only what cannot be discovered, and finish with a
   decision-complete ``<proposed_plan>``. Ours is a *state* (``<state>/coding/plan.json``)
   plus a real gate: :func:`plan_guard` classifies an action and refuses a mutating one while
   a plan is open, so "plan mode" is enforced rather than requested in a prompt.
3. **Test-fix loop.** Run the tests, parse the failures, hand them to a fixer, run again —
   bounded by rounds and by a *no-progress* rule (a repeated failure fingerprint stops the
   loop instead of burning the bound). The fixer is the caller's (a command or a callback):
   without one the loop reports the failure roster and never pretends to have fixed it.
4. **File mentions.** Codex drives a nucleo-backed fuzzy search for its ``@token``
   (``tui/src/file_search.rs``, ``file-search/src/lib.rs``). We implement the same
   subsequence scoring in Python with match indices for highlighting, and reuse the ONE
   walker we already have (``codectx.walk_workspace``) so ignores are not re-implemented.
5. **Undo last turn.** A turn journal records, per turn, every file the turn touched with
   the bytes *before* and the sha256 *after*; :func:`undo_turns` restores them and verifies
   byte-identity, and refuses the whole turn when a file moved since (the same
   "changed since plan" discipline as ``edit_tx``). An undo that does not verify is not an
   undo.
6. **Session summary / status** and **``/init``**: a handoff summary built from real facts
   (session store, event store, plan state, journal) and an ``AGENTS.md`` skeleton whose
   headings are exactly the ones ``project_context.py`` injects.

Two candidates from the brief are deliberately **not** built here; the reasons are stated in
``MVP/46-coding-features.md`` §6 (``/compact`` already exists as ``context compact``;
command-history search belongs to the REPL's readline history, not to a new store).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# 1. Run-scoped JSON event stream
# --------------------------------------------------------------------------- #

STREAM_SCHEMA = "skaldir.coding.stream/1"

#: The item types a stream may carry. ``command_execution`` and ``file_change`` are Codex's
#: names on purpose (a consumer written against their exec stream reads ours too).
ITEM_TYPES = (
    "command_execution",
    "file_change",
    "test_run",
    "agent_message",
    "reasoning",
    "plan",
    "tool_call",
    "error",
)

#: The status vocabulary. ``declined`` is NOT ``failed``: a policy refusal is not a defect,
#: and a monitor must be able to tell "we were not allowed to" from "it broke".
ITEM_STATUSES = ("in_progress", "completed", "failed", "declined")

TERMINAL_ITEM_STATUSES = ("completed", "failed", "declined")

#: How much of a command's output rides on an ``item.updated`` event.
STREAM_OUTPUT_TAIL_CHARS = 2000

RUN_STATUSES = ("completed", "failed", "declined")


def _now() -> float:
    """Wall clock, isolated so a test can freeze it through the ``clock`` parameter."""
    return time.time()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


class _Emitter:
    """Where stream events go: a JSONL file, a callback, or both.

    A stream is written **line by line and flushed**, because the whole point is that a
    reader sees progress while the run is still going.
    """

    def __init__(
        self,
        *,
        path: str | Path | None = None,
        callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.path = Path(path) if path else None
        self.callback = callback
        self._handle: Any = None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("w", encoding="utf-8")

    def send(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        if self._handle is not None:
            self._handle.write(line + "\n")
            self._handle.flush()
        if self.callback is not None:
            self.callback(event)

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


@dataclass
class RunStream:
    """The emitter for one run: ``run.started`` → item lifecycle → ``run.completed``.

    Item ids are stable and monotonic inside a run (``item-1``, ``item-2``, …), so a
    consumer can join an update to the item it belongs to without keeping a map of payloads.
    """

    run_id: str
    emitter: _Emitter
    clock: Callable[[], float] = _now
    seq: int = 0
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = 0.0

    def _emit(self, kind: str, **fields: Any) -> dict[str, Any]:
        self.seq += 1
        event: dict[str, Any] = {
            "type": kind,
            "seq": self.seq,
            "ts": round(self.clock(), 6),
            "run_id": self.run_id,
            "schema": STREAM_SCHEMA,
        }
        event.update(fields)
        self.events.append(event)
        self.emitter.send(event)
        return event

    # -- run level ---------------------------------------------------------#

    def run_started(self, command: str | Sequence[str], **meta: Any) -> dict[str, Any]:
        self.started_at = self.clock()
        return self._emit("run.started", command=command, **meta)

    def run_completed(self, status: str = "completed", **fields: Any) -> dict[str, Any]:
        if status not in RUN_STATUSES:
            raise ValueError(f"unknown run status: {status!r}")
        return self._emit(
            "run.completed",
            status=status,
            duration_ms=int((self.clock() - self.started_at) * 1000) if self.started_at else 0,
            items=len(self.items),
            **fields,
        )

    def run_failed(self, message: str, **fields: Any) -> dict[str, Any]:
        self._emit("error", message=message)
        return self._emit("run.completed", status="failed", message=message, **fields)

    # -- item level --------------------------------------------------------#

    def item_started(self, item_type: str, **details: Any) -> str:
        if item_type not in ITEM_TYPES:
            raise ValueError(f"unknown item type: {item_type!r}")
        item_id = f"item-{len(self.items) + 1}"
        item = {"id": item_id, "type": item_type, "status": "in_progress", **details}
        self.items[item_id] = item
        self._emit("item.started", item=dict(item))
        return item_id

    def item_updated(self, item_id: str, **details: Any) -> dict[str, Any]:
        item = self._require(item_id)
        item.update(details)
        return self._emit("item.updated", item=dict(item))

    def item_completed(
        self, item_id: str, status: str = "completed", **details: Any
    ) -> dict[str, Any]:
        if status not in TERMINAL_ITEM_STATUSES:
            raise ValueError(f"an item cannot end in {status!r}")
        item = self._require(item_id)
        if item["status"] != "in_progress":
            raise ValueError(f"item {item_id} already ended as {item['status']!r}")
        item.update(details)
        item["status"] = status
        return self._emit("item.completed", item=dict(item))

    def _require(self, item_id: str) -> dict[str, Any]:
        item = self.items.get(item_id)
        if item is None:
            raise KeyError(f"unknown item id: {item_id!r}")
        if item["status"] != "in_progress":
            raise ValueError(f"item {item_id} already ended as {item['status']!r}")
        return item


def read_stream(path: str | Path) -> list[dict[str, Any]]:
    """Parse a JSONL stream, refusing a line that is not one JSON object."""
    events: list[dict[str, Any]] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:  # the stream contract is one object per line
            raise ValueError(f"line {number} is not JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"line {number} is not a JSON object")
        events.append(parsed)
    return events


def validate_stream(events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The stream's invariants, as a payload a caller can act on.

    * every line is one JSON object with our ``schema``;
    * sequence numbers are 1..N in order;
    * every item that started reached **exactly one** terminal state (no orphans, no
      double terminal);
    * a ``declined`` item carries **no** exit code, a ``failed`` command item does.
    """
    problems: list[str] = []
    started: dict[str, int] = {}
    terminal: dict[str, int] = {}
    statuses: dict[str, str] = {}
    sequences = 0
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            problems.append(f"event {index} is not an object")
            continue
        if event.get("schema") != STREAM_SCHEMA:
            problems.append(f"event {index} carries schema {event.get('schema')!r}")
        kind = event.get("type")
        if event.get("seq") != index:
            problems.append(f"event {index} has seq {event.get('seq')!r}")
        sequences += 1
        item = event.get("item")
        if kind == "item.started" and isinstance(item, dict):
            item_id = str(item.get("id", ""))
            if item_id in started:
                problems.append(f"item {item_id} started twice")
            started[item_id] = index
        elif kind in ("item.updated", "item.completed") and isinstance(item, dict):
            item_id = str(item.get("id", ""))
            if item_id not in started:
                verb = "completed" if kind == "item.completed" else "updated"
                problems.append(f"item {item_id} was {verb} before it started")
            if kind == "item.completed":
                if item_id in terminal:
                    problems.append(f"item {item_id} completed twice")
                terminal[item_id] = index
                statuses[item_id] = str(item.get("status", ""))
                if item.get("status") == "declined" and item.get("exit_code") is not None:
                    problems.append(f"declined item {item_id} carries an exit code")
                if (
                    item.get("type") == "command_execution"
                    and item.get("status") == "failed"
                    and item.get("exit_code") is None
                ):
                    problems.append(f"failed command item {item_id} has no exit code")
    orphans = sorted(set(started) - set(terminal))
    if orphans:
        problems.append(f"items without a terminal event: {', '.join(orphans)}")
    return {
        "schema": STREAM_SCHEMA,
        "ok": not problems,
        "events": sequences,
        "items_started": len(started),
        "items_completed": sum(1 for s in statuses.values() if s == "completed"),
        "items_failed": sum(1 for s in statuses.values() if s == "failed"),
        "items_declined": sum(1 for s in statuses.values() if s == "declined"),
        "orphans": orphans,
        "problems": problems,
    }


def stream_command(
    argv: Sequence[str],
    *,
    stream: RunStream,
    cwd: str | Path | None = None,
    timeout_s: float = 120.0,
    env: dict[str, str] | None = None,
    progress_every: int = 1,
    policy: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Run one real command and emit its whole lifecycle on ``stream``.

    ``policy`` is called with the rendered command line; a returned string is the reason the
    run is **declined** — the process is never started, the item fails no exit code, and the
    run ends ``declined``. That is the one status a UI must not confuse with a crash.
    """
    rendered = " ".join(shlex.quote(part) for part in argv)
    if policy is not None:
        reason = policy(rendered)
        if reason:
            item_id = stream.item_started(
                "command_execution", command=rendered, aggregated_output="", lines=0
            )
            stream.item_completed(item_id, "declined", exit_code=None, declined_reason=reason)
            stream.run_completed("declined", decline_reason=reason)
            return {
                "status": "declined",
                "reason": reason,
                "exit_code": None,
                "lines": 0,
                "output_tail": "",
                "item_id": item_id,
            }

    item_id = stream.item_started(
        "command_execution", command=rendered, aggregated_output="", lines=0
    )
    started = stream.clock()
    keep = progress_every if progress_every > 0 else 1
    lines: list[str] = []
    exit_code: int | None = None
    timed_out = False
    process_env = {**os.environ, **(env or {})}
    try:
        process = subprocess.Popen(  # noqa: S603 — a declared argv, never a shell string
            list(argv),
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=process_env,
        )
    except OSError as exc:
        stream.item_completed(item_id, "failed", exit_code=None, error=str(exc), lines=0)
        stream.run_failed(f"the command could not be started: {exc}", exit_code=None)
        return {
            "status": "failed",
            "reason": str(exc),
            "exit_code": None,
            "lines": 0,
            "output_tail": "",
            "item_id": item_id,
        }

    try:
        if process.stdout is not None:
            for line in process.stdout:
                lines.append(line.rstrip("\n"))
                if len(lines) % keep == 0:
                    tail = "\n".join(lines)[-STREAM_OUTPUT_TAIL_CHARS:]
                    stream.item_updated(
                        item_id,
                        aggregated_output=tail,
                        lines=len(lines),
                        last_line=lines[-1][:200],
                        elapsed_ms=int((stream.clock() - started) * 1000),
                    )
        process.wait(timeout=timeout_s)
        exit_code = process.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
        exit_code = None
    finally:
        if process.stdout is not None:
            process.stdout.close()

    output = "\n".join(lines)
    tail = output[-STREAM_OUTPUT_TAIL_CHARS:]
    duration_ms = int((stream.clock() - started) * 1000)
    if timed_out:
        stream.item_completed(
            item_id,
            "failed",
            exit_code=None,
            timeout_s=timeout_s,
            aggregated_output=tail,
            lines=len(lines),
            duration_ms=duration_ms,
        )
        stream.run_completed("failed", timeout=True, exit_code=None, lines=len(lines))
        return {
            "status": "failed",
            "reason": f"timed out after {timeout_s}s",
            "exit_code": None,
            "lines": len(lines),
            "output_tail": tail,
            "item_id": item_id,
            "timeout": True,
        }

    status = "completed" if exit_code == 0 else "failed"
    stream.item_completed(
        item_id,
        status,
        exit_code=exit_code,
        aggregated_output=tail,
        lines=len(lines),
        duration_ms=duration_ms,
    )
    stream.run_completed("completed" if exit_code == 0 else "failed", exit_code=exit_code)
    return {
        "status": status,
        "reason": "" if status == "completed" else f"exit code {exit_code}",
        "exit_code": exit_code,
        "lines": len(lines),
        "output_tail": tail,
        "item_id": item_id,
        "duration_ms": duration_ms,
    }


# --------------------------------------------------------------------------- #
# 2. Plan mode (propose before touching files)
# --------------------------------------------------------------------------- #

PLAN_SCHEMA = "skaldir.coding.plan/1"

#: Commands that only read. Anything else is treated as mutating and refused while a plan
#: is open — the safe default, because a wrong "read-only" guess would let the plan mode
#: gate leak. ``git status`` is listed even though git also writes ``.git/index`` (our own
#: harness documents that; the write is git's bookkeeping, not workspace content).
READ_ONLY_COMMANDS = frozenset(
    {
        "cat",
        "cd",
        "cut",
        "df",
        "diff",
        "du",
        "env",
        "file",
        "find",
        "grep",
        "head",
        "jq",
        "ls",
        "pwd",
        "rg",
        "sed-n",
        "sort",
        "stat",
        "tail",
        "tree",
        "uniq",
        "wc",
        "which",
        "whoami",
    }
)

#: Runners and checkers whose declared purpose is to **report**, not to change. Codex's plan
#: mode allows tests/builds that only write caches; we cannot decide that statically, so the
#: list is explicit and short, and anything not on it must be approved. ``pytest`` is on it
#: with any arguments (``pytest tests -q``); ``python -m pytest|unittest`` is matched through
#: :data:`READ_ONLY_SUBCOMMANDS`.
READ_ONLY_TEST_COMMANDS = frozenset(
    {"pytest", "py.test", "tox", "nox", "mypy", "pyright", "eslint", "rubocop", "gofmt"}
)

#: Sub-commands that are read-only even though the program can write.
READ_ONLY_SUBCOMMANDS = {
    "git": frozenset(
        {"status", "diff", "log", "show", "blame", "rev-parse", "ls-files", "branch"}
    ),
    "docker": frozenset({"ps", "images", "inspect", "version"}),
    "skaldir": frozenset({"selftest", "contract", "code", "context", "prompt"}),
    "pytest": frozenset({"--collect-only", "--co", "-h", "--help"}),
    "ruff": frozenset({"check"}),
    "cargo": frozenset({"test", "check", "clippy"}),
    "go": frozenset({"test", "vet"}),
    "npm": frozenset({"test", "run"}),
    "python": frozenset({"-m", "-c"}),
    "python3": frozenset({"-m", "-c"}),
}

#: ``python -c`` is only read-only when the snippet itself is a syntax/compile check.
PYTHON_CHECK_MODULES = frozenset(
    {"pytest", "unittest", "compileall", "mypy", "pyright", "ruff", "json.tool"}
)

#: Shell operators that mean the command is not a plain read: a redirect or a substitution
#: can write anything, so the classification falls back to "mutating".
WRITE_OPERATORS = (" >", ">>", "| tee", "&&", "||", ";", "$(", "`", " >|")


def classify_action(action: str) -> dict[str, Any]:
    """``read_only`` or ``mutating`` for one command line, with the reason stated.

    The rule is deliberately conservative: only a command whose first token is on the
    read-only list (or is a listed read-only sub-command) **and** which carries no shell
    write operator or redirect is read-only. Everything else must be approved, because the
    cost of being wrong is a write inside plan mode.
    """
    text = str(action or "").strip()
    if not text:
        return {"classification": "mutating", "reason": "empty command", "command": text}
    lowered = text.lower()
    for operator in WRITE_OPERATORS:
        if operator in text:
            return {
                "classification": "mutating",
                "reason": f"shell write operator {operator!r}",
                "command": text,
            }
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()
    if not tokens:
        return {"classification": "mutating", "reason": "empty command", "command": text}
    program = Path(tokens[0]).name.lower()
    if program in READ_ONLY_COMMANDS or program in READ_ONLY_TEST_COMMANDS:
        return {
            "classification": "read_only",
            "reason": f"{program} is a declared read-only command or checker",
            "command": text,
        }
    allowed = READ_ONLY_SUBCOMMANDS.get(program)
    if program in ("python", "python3") and len(tokens) > 2:
        sub = tokens[1].lower()
        if sub == "-m":
            module = tokens[2].split(".")[0].lower()
            if module in PYTHON_CHECK_MODULES:
                return {
                    "classification": "read_only",
                    "reason": f"python -m {module} is a test/check module",
                    "command": text,
                }
            return {
                "classification": "mutating",
                "reason": f"python -m {module} is not a declared test/check module",
                "command": text,
            }
        if sub == "-c":
            snippet = tokens[2]
            if any(token in snippet for token in ("compile(", "py_compile", "json.tool")):
                return {
                    "classification": "read_only",
                    "reason": "python -c is a compile or JSON-format check",
                    "command": text,
                }
            return {
                "classification": "mutating",
                "reason": "python -c runs an arbitrary snippet, not a declared check",
                "command": text,
            }
    if allowed is not None and len(tokens) > 1:
        sub = tokens[1].lower()
        if sub in allowed:
            if program == "npm" and sub == "run":
                script = str(tokens[2]) if len(tokens) > 2 else "?"
                if not script.startswith(("test", "lint", "check")):
                    return {
                        "classification": "mutating",
                        "reason": f"npm run {script} is not a declared test/lint script",
                        "command": text,
                    }
            return {
                "classification": "read_only",
                "reason": f"{program} {sub} is a declared read-only sub-command",
                "command": text,
            }
    if program in READ_ONLY_SUBCOMMANDS:
        return {
            "classification": "mutating",
            "reason": f"{program} without a read-only sub-command ({lowered})",
            "command": text,
        }
    return {
        "classification": "mutating",
        "reason": f"{program} is not a declared read-only command",
        "command": text,
    }


def plan_state_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / "plan.json"


def load_plan(state_dir: str | Path) -> dict[str, Any] | None:
    path = plan_state_path(state_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_plan(state_dir: str | Path, plan: dict[str, Any]) -> Path:
    path = plan_state_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return path


def plan_begin(
    *,
    state_dir: str | Path,
    workspace: str | Path,
    task: str,
    clock: Callable[[], float] = _now,
) -> dict[str, Any]:
    """Open plan mode for one task. Refuses while another plan is open (``conflict``)."""
    existing = load_plan(state_dir)
    if existing is not None and existing.get("status") == "planning":
        raise PlanConflict(
            f"plan mode is already open for {existing.get('task')!r}; "
            "close it with `coding plan apply` or `coding plan drop`"
        )
    plan = {
        "schema": PLAN_SCHEMA,
        "status": "planning",
        "task": str(task),
        "workspace": str(Path(workspace).resolve()),
        "opened_at": round(clock(), 6),
        "closed_at": None,
        "probe": None,
        "proposal": "",
        "guarded_actions": [],
        "version": (int(existing.get("version", 0)) + 1) if existing else 1,
    }
    save_plan(state_dir, plan)
    return plan


class PlanConflict(RuntimeError):
    """A second plan opened while one was still being written (exit 6 through the CLI)."""


def plan_guard(
    plan: dict[str, Any] | None,
    action: str,
    *,
    record: bool = True,
    state_dir: str | Path | None = None,
) -> dict[str, Any]:
    """May ``action`` run right now? The answer carries the classification and the reason.

    With no open plan everything is allowed. With plan mode open, a ``mutating`` action is
    declined. Every decision made while a plan is open is appended to ``guarded_actions`` so
    the plan itself shows what it refused.
    """
    verdict = classify_action(action)
    open_plan = bool(plan is not None and plan.get("status") == "planning")
    allowed = True
    if open_plan and verdict["classification"] == "mutating":
        allowed = False
    result = {
        "allowed": allowed,
        "mode": "plan" if open_plan else "default",
        "classification": verdict["classification"],
        "reason": verdict["reason"],
        "command": verdict["command"],
        "declined_reason": "" if allowed else f"plan mode: {verdict['reason']}",
    }
    if open_plan and record and plan is not None:
        plan.setdefault("guarded_actions", []).append(
            {
                "command": verdict["command"],
                "classification": verdict["classification"],
                "allowed": allowed,
                "reason": verdict["reason"],
            }
        )
        if state_dir is not None:
            save_plan(state_dir, plan)
    return result


def detect_project_commands(workspace: str | Path) -> dict[str, Any]:
    """The real commands this project declares — read from its own files, never invented."""
    root = Path(workspace).resolve()
    found: dict[str, list[str]] = {}
    notes: list[str] = []

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            import tomllib

            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            project = data.get("project", {})
            for name in sorted((project.get("scripts") or {}).keys()):
                found.setdefault("run", []).append(name)
            if (root / "tests").is_dir() or (project.get("optional-dependencies", {}) or {}).get(
                "dev"
            ):
                found.setdefault("test", []).append(
                    "python -m pytest tests -q -o addopts=" + '\"\"'
                )
            if (data.get("tool", {}) or {}).get("ruff"):
                found.setdefault("lint", []).append("ruff check .")
        except Exception as exc:  # noqa: BLE001 — a broken manifest is a note, not a failure
            notes.append(f"pyproject.toml could not be parsed: {exc}")

    package_json = root / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            for name in sorted((data.get("scripts") or {}).keys()):
                key = "test" if "test" in name else ("lint" if "lint" in name else "run")
                found.setdefault(key, []).append(f"npm run {name}")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"package.json could not be parsed: {exc}")

    makefile = root / "Makefile"
    if makefile.exists():
        for line in makefile.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^([A-Za-z0-9_.-]+):(?!=)", line)
            if match:
                found.setdefault("make", []).append(f"make {match.group(1)}")

    for candidate, key, argv in (
        ("tests", "test", "python -m pytest tests -q"),
        ("scripts/run_tests.sh", "test", "./scripts/run_tests.sh"),
        ("justfile", "make", "just"),
    ):
        if (root / candidate).exists() and not found.get(key):
            found.setdefault(key, []).append(argv)

    return {
        "commands": {key: sorted(set(values)) for key, values in found.items()},
        "notes": notes,
    }


def plan_probe(
    workspace: str | Path,
    task: str,
    *,
    max_files: int = 40,
    clock: Callable[[], float] = _now,
) -> dict[str, Any]:
    """Phase 1 of plan mode: ground the plan in the environment, non-mutating.

    Everything here is read-only by construction: a bounded walk (through the ONE walker,
    ``codectx.walk_workspace``, when it is importable), the declared commands, and the test
    layout. Nothing is asked of the user that the repo can answer.
    """
    root = Path(workspace).resolve()
    files: list[dict[str, Any]] = []
    walker = "codectx.walk_workspace"
    try:
        from . import codectx

        rules = codectx.load_ignore_rules(root)
        walk = codectx.walk_workspace(root, rules=rules, max_files=max_files)
        for rel, size, _mtime, _ino in walk.files[:max_files]:
            files.append({"path": rel, "size": size})
        ignored = walk.ignored
        cut = walk.cut
    except Exception:  # noqa: BLE001 — a build without codectx still gets a bounded walk
        walker = "builtin"
        ignored = 0
        cut = False
        skip = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}
        for path in sorted(root.rglob("*")):
            if len(files) >= max_files:
                cut = True
                break
            if any(part in skip for part in path.parts):
                continue
            if path.is_file():
                rel = str(path.relative_to(root))
                try:
                    files.append({"path": rel, "size": path.stat().st_size})
                except OSError:  # pragma: no cover
                    continue

    languages: dict[str, int] = {}
    for entry in files:
        suffix = Path(entry["path"]).suffix.lstrip(".") or "none"
        languages[suffix] = languages.get(suffix, 0) + 1
    tests = [entry["path"] for entry in files if "test" in Path(entry["path"]).name]
    return {
        "workspace": str(root),
        "task": str(task),
        "walker": walker,
        "files_sampled": len(files),
        "files": files,
        "languages": dict(sorted(languages.items(), key=lambda item: (-item[1], item[0]))),
        "test_files_in_sample": tests[:20],
        "ignored_paths": ignored,
        "sample_cut": cut,
        "declared_commands": detect_project_commands(root),
        "probed_at": round(clock(), 6),
    }


PLAN_TEMPLATE_HEADINGS = ("Summary", "Implementation Changes", "Test Plan", "Assumptions")


def _language_summary(languages: dict[str, int], limit: int = 6) -> str:
    """``py:41 md:3`` — a deliberately plain rendering (no f-string nesting games)."""
    pairs = sorted(languages.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return ", ".join(f"{name}:{count}" for name, count in pairs) or "none"


def plan_propose(
    plan: dict[str, Any],
    *,
    changes: Sequence[str] = (),
    tests: Sequence[str] = (),
    assumptions: Sequence[str] = (),
    open_questions: Sequence[str] = (),
    clock: Callable[[], float] = _now,
) -> dict[str, Any]:
    """Render the plan as the decision-complete document Codex's plan mode ends with.

    ``open_questions`` is not decoration: an unresolved question is exactly what makes a plan
    *not* decision complete, so it is printed as a blocker and the plan says so.
    """
    probe = plan.get("probe") or {}
    commands = (probe.get("declared_commands") or {}).get("commands") or {}
    declared_tests = commands.get("test") or []
    key_changes = list(changes) or [
        "no change was declared — the plan names the files it would touch before it is applied"
    ]
    test_plan = list(tests) or (
        [f"`{declared_tests[0]}` (the project's own declared runner)"] if declared_tests else []
    )
    if not test_plan:
        test_plan = ["no test command was discovered — name one before applying"]
    lines = [
        "<proposed_plan>",
        f"# Plan: {plan.get('task')}",
        "",
        "## Summary",
        f"- Workspace: `{probe.get('workspace', plan.get('workspace'))}`",
        f"- Grounded on {probe.get('files_sampled', 0)} file(s) "
        f"({'sample was cut short' if probe.get('sample_cut') else 'full sample'}), "
        f"{len(commands)} command group(s) declared by the project.",
        "- Languages in the sample: "
        + _language_summary(probe.get("languages") or {})
        + ".",
        "",
        "## Implementation Changes",
    ]
    lines += [f"- {item}" for item in key_changes]
    lines += ["", "## Test Plan"]
    lines += [f"- {item}" for item in test_plan]
    lines += ["", "## Assumptions"]
    defaults = [
        "the declared commands above are the project's own; no other command is invented",
        "plan mode refuses mutating actions until this plan is applied",
    ]
    lines += [f"- {item}" for item in list(assumptions) + defaults]
    if open_questions:
        lines += ["", "## Open Questions (this plan is NOT decision complete)"]
        lines += [f"- {item}" for item in open_questions]
    lines += ["</proposed_plan>"]
    text = "\n".join(lines) + "\n"
    plan["proposal"] = text
    plan["proposed_at"] = round(clock(), 6)
    plan["decision_complete"] = not open_questions
    return {
        "plan": text,
        "decision_complete": not open_questions,
        "open_questions": list(open_questions),
        "headings": list(PLAN_TEMPLATE_HEADINGS),
    }


def plan_close(
    plan: dict[str, Any],
    *,
    state_dir: str | Path,
    outcome: str,
    clock: Callable[[], float] = _now,
) -> dict[str, Any]:
    """Close plan mode: ``applied`` (the plan is the licence to mutate) or ``dropped``."""
    if outcome not in ("applied", "dropped"):
        raise ValueError(f"unknown plan outcome: {outcome!r}")
    plan["status"] = outcome
    plan["closed_at"] = round(clock(), 6)
    plan["application"] = {
        "outcome": outcome,
        "decision_complete": bool(plan.get("decision_complete", False)),
        "guarded_actions": len(plan.get("guarded_actions") or []),
    }
    save_plan(state_dir, plan)
    return plan


# --------------------------------------------------------------------------- #
# 3. Test-fix loop
# --------------------------------------------------------------------------- #

TEST_SCHEMA = "skaldir.coding.tests/1"

#: ``FAILED path::name - reason`` (pytest), ``path:line:col: error: msg`` (tooling),
#: ``FAIL: name`` (unittest) and ``--- FAIL: Name`` (go). One parser, four shapes.
_FAILURE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "pytest",
        re.compile(r"^(FAILED|ERROR)\s+(?P<path>[^\s:]+)(?:::(?P<name>[^\s]+))?(?:\s+-\s+(?P<reason>.*))?$"),
    ),
    ("lint", re.compile(r"^(?P<path>[^\s:]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<reason>.+)$")),
    ("unittest", re.compile(r"^(FAIL|ERROR):\s+(?P<name>\S+)(?:\s+\((?P<path>[^)]+)\))?")),
    ("go", re.compile(r"^---\s+(FAIL|PASS):\s+(?P<name>\S+)")),
)

_LINT_NOISE = ("warning:", "note:", "info:", "help:")

# ANSI escape sequences (CSI), e.g. ``\x1b[31m`` / ``\x1b[1m``: pytest >= 9 and uv colorize
# their output even when stdout is not a TTY (FORCE_COLOR/CLICOLOR_FORCE). The ANSI prefixes
# break every ``^(FAILED|ERROR)…`` anchor, so the parser strips them instead of demanding a
# colorless environment — colored output in the wild must stay parseable.
_ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def parse_failures(output: str, *, runner: str = "auto") -> list[dict[str, Any]]:
    """Extract typed failures from a real test output. No match ⇒ an empty list, never a guess."""
    failures: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in output.splitlines():
        line = _ANSI_CSI.sub("", raw).strip()
        if not line:
            continue
        for name, pattern in _FAILURE_PATTERNS:
            if runner not in ("auto", name):
                continue
            match = pattern.match(line)
            if not match:
                continue
            groups = match.groupdict()
            if name == "lint" and any(token in line.lower() for token in _LINT_NOISE):
                break
            if name == "go" and line.startswith("--- PASS"):
                break
            entry = {
                "runner": name,
                "path": str(groups.get("path") or ""),
                "name": str(groups.get("name") or ""),
                "reason": str(groups.get("reason") or line)[:300],
                "line": int(groups["line"]) if groups.get("line") else None,
                "raw": line[:300],
            }
            key = (entry["path"], entry["name"] or entry["raw"])
            if key in seen:
                break
            seen.add(key)
            failures.append(entry)
            break
    # A pytest traceback line (``path:line: AssertionError``) and the summary line
    # (``FAILED path::test - ...``) describe the SAME failure. Keep the named, structured one
    # and drop the unnamed traceback twin, so the failure count and the fingerprint stay
    # comparable between rounds instead of double-counting one defect.
    named_paths = {item["path"] for item in failures if item["name"]}
    return [item for item in failures if item["name"] or item["path"] not in named_paths]


def failure_fingerprint(failures: Sequence[dict[str, Any]]) -> str:
    """A stable digest of the failure set — the same set twice means the fix changed nothing."""
    parts = sorted(f"{item.get('path', '')}::{item.get('name', '')}" for item in failures)
    return _sha256_text("\n".join(parts))


def run_tests(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    timeout_s: float = 900.0,
    env: dict[str, str] | None = None,
    stream: RunStream | None = None,
) -> dict[str, Any]:
    """Run the project's real test command once and classify the result."""
    rendered = " ".join(shlex.quote(part) for part in argv)
    item_id = (
        stream.item_started(
            "test_run", command=rendered, aggregated_output="", status="in_progress"
        )
        if stream
        else None
    )
    started = time.time()
    try:
        completed = subprocess.run(  # noqa: S603 — a declared argv
            list(argv),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env={**os.environ, **(env or {})},
            check=False,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        exit_code: int | None = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        exit_code = None
        timed_out = True
    except OSError as exc:
        output = ""
        exit_code = None
        timed_out = False
        completed = None  # type: ignore[assignment]
        if stream and item_id:
            stream.item_completed(item_id, "failed", exit_code=None, error=str(exc))
        return {
            "schema": TEST_SCHEMA,
            "command": rendered,
            "exit_code": None,
            "status": "failed",
            "reason": f"the test command could not be started: {exc}",
            "duration_ms": int((time.time() - started) * 1000),
            "output_tail": "",
            "output_sha256": _sha256_text(""),
            "failures": [],
            "failure_fingerprint": failure_fingerprint([]),
            "item_id": item_id,
            "timeout": False,
        }
    failures = parse_failures(output, runner="auto")
    if timed_out:
        status = "failed"
    elif exit_code == 0:
        status = "completed"
    else:
        status = "failed"
    run = {
        "schema": TEST_SCHEMA,
        "command": rendered,
        "exit_code": exit_code,
        "status": status,
        "reason": (
            "timed out"
            if timed_out
            else ("" if exit_code == 0 else f"exit code {exit_code}")
        ),
        "duration_ms": int((time.time() - started) * 1000),
        "output_tail": output[-STREAM_OUTPUT_TAIL_CHARS:],
        "output_sha256": _sha256_text(output),
        "failures": failures,
        "failure_fingerprint": failure_fingerprint(failures),
        "item_id": item_id,
        "timeout": timed_out,
    }
    if stream and item_id:
        stream.item_completed(
            item_id,
            "completed" if status == "completed" else "failed",
            exit_code=exit_code,
            failures=len(failures),
            duration_ms=run["duration_ms"],
        )
    return run


#: What a fixer may answer.
FIXER_OUTCOMES = ("fixed", "stop", "no_change")


@dataclass
class FixOutcome:
    """One fixer round's answer: what it changed and whether to keep going."""

    outcome: str = "fixed"
    summary: str = ""
    changed_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "summary": self.summary,
            "changed_paths": list(self.changed_paths),
        }


def invalidate_python_caches(paths: Sequence[str], *, workspace: str | Path) -> list[str]:
    """Drop the ``__pycache__`` directories a fixer's changed files would be served from.

    **Measured trap (2026-09-18):** rewriting ``tests/test_mod.py`` from ``assert 1 == 1`` to
    ``assert 1 == 2`` inside the same second and with the same byte count left pytest's
    assertion-rewrite cache (``tests/__pycache__/test_mod.cpython-312-pytest-9.1.1.pyc``)
    "valid" — the fixer's change was invisible and the loop reported *passed* on a test that
    still failed. A test-fix loop that cannot see its own fix is worthless, so the loop
    removes the stale caches of the changed files before it re-runs. Cache directories only;
    no source file is ever touched.
    """
    root = Path(workspace).resolve()
    removed: list[str] = []
    for raw in paths:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / candidate
        directory = candidate.resolve().parent
        if not str(directory).startswith(str(root)):
            continue
        for current in (directory, *directory.parents):
            if current == root.parent or not str(current).startswith(str(root)):
                break
            cache = current / "__pycache__"
            if cache.is_dir():
                import shutil

                shutil.rmtree(cache, ignore_errors=True)
                removed.append(str(cache))
            if current == root:
                break
    return removed


def test_fix_loop(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    fixer: Callable[[int, dict[str, Any]], FixOutcome | dict[str, Any] | None] | None = None,
    fixer_argv: Sequence[str] = (),
    max_rounds: int = 3,
    timeout_s: float = 900.0,
    env: dict[str, str] | None = None,
    stream: RunStream | None = None,
) -> dict[str, Any]:
    """Run → parse → fix → run, bounded, and stop when the fix stops changing anything.

    The statuses are the point:

    * ``already_green`` — the first run passed; nothing was changed (this is not a fix).
    * ``passed`` — the failures disappeared after a fixer round.
    * ``no_progress`` — the failure set repeated; stopped **before** the round bound.
    * ``rounds_exhausted`` — the bound was reached with failures left.
    * ``no_fixer`` — failures were found and no fixer was supplied: the roster is returned and
      nothing is claimed. Exit code 2 (verification failed) through the CLI.
    * ``fixer_failed`` — the fixer could not run (a declared argv that will not start).

    ``fixer_argv`` is the shell-free way to supply a fixer: it is run with the round number
    in ``SKALDIR_FIX_ROUND`` and the failure fingerprint in ``SKALDIR_FIX_FINGERPRINT``.
    """
    rounds: list[dict[str, Any]] = []
    if max_rounds < 0:
        raise ValueError("max_rounds must not be negative")
    first = run_tests(argv, cwd=cwd, timeout_s=timeout_s, env=env, stream=stream)
    rounds.append({"round": 0, "fix": None, "test": first})
    if first["status"] == "completed":
        return {
            "schema": TEST_SCHEMA,
            "status": "already_green",
            "rounds": rounds,
            "rounds_used": 0,
            "max_rounds": max_rounds,
            "failures": [],
            "reason": "the first test run passed; nothing was changed",
            "exit_code": 0,
        }
    if fixer is None and not fixer_argv:
        return {
            "schema": TEST_SCHEMA,
            "status": "no_fixer",
            "rounds": rounds,
            "rounds_used": 0,
            "max_rounds": max_rounds,
            "failures": first["failures"],
            "reason": "failures found and no fixer was supplied — nothing was changed",
            "exit_code": 2,
        }

    seen: dict[str, int] = {first["failure_fingerprint"]: 0}
    last = first
    for round_number in range(1, max_rounds + 1):
        feedback = {
            "round": round_number,
            "command": last["command"],
            "exit_code": last["exit_code"],
            "failures": last["failures"],
            "output_tail": last["output_tail"],
        }
        item_id = (
            stream.item_started("reasoning", round=round_number, failures=len(last["failures"]))
            if stream
            else None
        )
        try:
            if fixer is not None:
                answer = fixer(round_number, feedback)
                fix = _coerce_fix(answer)
            else:
                fix = _run_fixer_command(fixer_argv, feedback, cwd=cwd, timeout_s=timeout_s)
        except OSError as exc:
            if stream and item_id:
                stream.item_completed(item_id, "failed", exit_code=None, error=str(exc))
            rounds.append({"round": round_number, "fix": None, "test": None, "error": str(exc)})
            return {
                "schema": TEST_SCHEMA,
                "status": "fixer_failed",
                "rounds": rounds,
                "rounds_used": round_number - 1,
                "max_rounds": max_rounds,
                "failures": last["failures"],
                "reason": f"the fixer could not run: {exc}",
                "exit_code": 3,
            }
        if stream and item_id:
            stream.item_completed(
                item_id,
                "completed",
                fix=fix.to_dict(),
                changed_paths=list(fix.changed_paths),
            )
        if fix.outcome == "stop":
            rounds.append({"round": round_number, "fix": fix.to_dict(), "test": None})
            return {
                "schema": TEST_SCHEMA,
                "status": "fixer_stopped",
                "rounds": rounds,
                "rounds_used": round_number - 1,
                "max_rounds": max_rounds,
                "failures": last["failures"],
                "reason": fix.summary or "the fixer stopped the loop",
                "exit_code": 2,
            }
        caches_cleared = invalidate_python_caches(fix.changed_paths, workspace=cwd) if cwd else []
        current = run_tests(argv, cwd=cwd, timeout_s=timeout_s, env=env, stream=stream)
        rounds.append(
            {
                "round": round_number,
                "fix": fix.to_dict(),
                "caches_cleared": caches_cleared,
                "test": current,
            }
        )
        if current["status"] == "completed":
            return {
                "schema": TEST_SCHEMA,
                "status": "passed",
                "rounds": rounds,
                "rounds_used": round_number,
                "max_rounds": max_rounds,
                "failures": [],
                "reason": f"green after round {round_number}",
                "exit_code": 0,
            }
        fingerprint = current["failure_fingerprint"]
        if fingerprint in seen:
            return {
                "schema": TEST_SCHEMA,
                "status": "no_progress",
                "rounds": rounds,
                "rounds_used": round_number,
                "max_rounds": max_rounds,
                "failures": current["failures"],
                "reason": (
                    f"the failure set did not change in round {round_number} "
                    f"(fingerprint {fingerprint[:12]}) — stopped before the round bound"
                ),
                "exit_code": 2,
            }
        seen[fingerprint] = round_number
        last = current

    return {
        "schema": TEST_SCHEMA,
        "status": "rounds_exhausted",
        "rounds": rounds,
        "rounds_used": max_rounds,
        "max_rounds": max_rounds,
        "failures": last["failures"],
        "reason": f"{len(last['failures'])} failure(s) left after {max_rounds} fixer round(s)",
        "exit_code": 2,
    }


def _coerce_fix(answer: Any) -> FixOutcome:
    if answer is None:
        return FixOutcome(outcome="no_change", summary="the fixer returned nothing")
    if isinstance(answer, FixOutcome):
        return answer
    if isinstance(answer, dict):
        return FixOutcome(
            outcome=str(answer.get("outcome", "fixed")),
            summary=str(answer.get("summary", "")),
            changed_paths=tuple(str(path) for path in answer.get("changed_paths", ())),
        )
    raise TypeError("a fixer must return FixOutcome, a mapping or None")


def _run_fixer_command(
    argv: Sequence[str],
    feedback: dict[str, Any],
    *,
    cwd: str | Path,
    timeout_s: float,
) -> FixOutcome:
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(feedback, handle, ensure_ascii=False)
        feedback_path = handle.name
    environment = {
        **os.environ,
        "SKALDIR_FIX_ROUND": str(feedback["round"]),
        "SKALDIR_FIX_FEEDBACK": feedback_path,
        "SKALDIR_FIX_FAILURES": str(len(feedback["failures"])),
    }
    try:
        completed = subprocess.run(  # noqa: S603 — a declared argv
            list(argv),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=environment,
            check=False,
        )
    finally:
        try:
            os.unlink(feedback_path)
        except OSError:  # pragma: no cover
            pass
    payload: dict[str, Any] = {}
    for line in reversed((completed.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if completed.returncode != 0 and not payload:
        return FixOutcome(
            outcome="stop",
            summary=f"the fixer exited {completed.returncode}",
            changed_paths=(),
        )
    return _coerce_fix(payload or {"outcome": "fixed", "summary": "fixer ran"})


# --------------------------------------------------------------------------- #
# 4. File mentions (``@path`` completion + expansion)
# --------------------------------------------------------------------------- #

MENTION_RE = re.compile(r"(?<![\w/])@(?P<path>[A-Za-z0-9._\-/][A-Za-z0-9._\-/]*)")


def fuzzy_indices(query: str, text: str) -> list[int] | None:
    """Subsequence positions of ``query`` in ``text`` (case-insensitive), or ``None``.

    Greedy left-to-right, which is the cheap approximation of a fuzzy matcher and enough for
    the highlighting a completion list needs.
    """
    if not query:
        return []
    lowered_text = text.lower()
    lowered_query = query.lower()
    positions: list[int] = []
    cursor = 0
    for character in lowered_query:
        found = lowered_text.find(character, cursor)
        if found < 0:
            return None
        positions.append(found)
        cursor = found + 1
    return positions


def score_match(query: str, path: str, indices: Sequence[int]) -> int:
    """A deterministic relevance score: boundary hits and short paths win.

    Weights are documented because they are a heuristic, not a model: a match on a path
    separator or after ``.``/``_``/``-`` scores 12, a camel-case boundary 8, a contiguous run
    6, and every character of tail length costs 1. The score never decides on its own — the
    sort key is ``(-score, path)`` so equal scores stay deterministic.
    """
    if not indices:
        return 0
    score = 0
    previous = -2
    for index in indices:
        if index == 0:
            score += 12
        else:
            before = path[index - 1]
            if before in "/\\._-":
                score += 12
            elif before.islower() and path[index].isupper():
                score += 8
        if index == previous + 1:
            score += 6
        previous = index
    score += 10 * len(indices) // max(1, len(query))
    score -= len(path) // 8
    return score


def complete_mention(
    query: str,
    *,
    workspace: str | Path,
    limit: int = 20,
    max_files: int = 20000,
) -> dict[str, Any]:
    """``@``-style file completion over the real workspace, ignoring what git ignores.

    Reuses ``codectx.walk_workspace`` (+ its ignore rules) when importable: a second walker
    would drift from the one the rest of the harness uses, including the ``SKIP_DIRS`` rule
    that is *not* the same thing as ``.gitignore``.
    """
    root = Path(workspace).resolve()
    query = str(query or "").lstrip("@")
    trailing_slash = query.endswith("/")
    named = query.rstrip("/")
    entries = _workspace_paths(root, max_files=max_files)
    results: list[dict[str, Any]] = []
    for row in entries["files"]:
        rel = row["path"]
        if trailing_slash:
            if not rel.startswith(named + "/") and rel != named:
                continue
            indices = fuzzy_indices(named, rel) or []
            if not indices:
                continue
        else:
            indices = fuzzy_indices(query, rel) if query else []
            if indices is None:
                continue  # a path that does not carry the query is not a match at all
        # A query without a separator also matches against the file name alone (``@events``
        # should find ``src/skaldir/events.py``); the better of the two scores wins.
        score = score_match(query, rel, indices)
        if query and "/" not in query:
            name = Path(rel).name
            name_indices = fuzzy_indices(query, name)
            if name_indices is not None:
                name_score = score_match(query, name, name_indices) + 6
                if name_score > score:
                    score = name_score
                    indices = [len(rel) - len(name) + index for index in name_indices]
        results.append(
            {
                "path": rel,
                "kind": row["kind"],
                "size": row["size"],
                "score": score,
                "indices": indices,
            }
        )
    results.sort(key=lambda item: (-item["score"], item["path"]))
    seen_paths = {item["path"] for item in results}
    if trailing_slash:
        # A directory query names the directory even when it holds no matching file of its own.
        for directory in entries["dirs"]:
            if directory in seen_paths or not directory.startswith(named):
                continue
            results.append(
                {
                    "path": directory,
                    "kind": "dir",
                    "size": 0,
                    "score": score_match(query, directory, fuzzy_indices(named, directory) or []),
                    "indices": fuzzy_indices(named, directory) or [],
                }
            )
    results.sort(key=lambda item: (-item["score"], item["path"]))
    return {
        "query": query,
        "workspace": str(root),
        "walker": entries["walker"],
        "files_scanned": len(entries["files"]),
        "ignored": entries["ignored"],
        "truncated": entries["truncated"],
        "matches": results[:limit],
        "total_matches": len(results),
    }


def _workspace_paths(root: Path, *, max_files: int) -> dict[str, Any]:
    """The workspace file list, through ``codectx``'s walker when it is importable."""
    try:
        from . import codectx

        rules = codectx.load_ignore_rules(root)
        walk = codectx.walk_workspace(root, rules=rules, max_files=max_files)
        files = [
            {"path": rel, "size": size, "kind": "file"}
            for rel, size, _mtime, _ino in walk.files
        ]
        dirs = sorted(
            {str(Path(rel).parent) for rel, *_ in walk.files if str(Path(rel).parent) != "."}
        )
        return {
            "walker": "codectx.walk_workspace",
            "files": files,
            "dirs": dirs,
            "ignored": walk.ignored,
            "truncated": walk.cut,
        }
    except Exception:  # noqa: BLE001 — a build without codectx gets a bounded fallback
        skip = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".mypy_cache"}
        files: list[dict[str, Any]] = []
        dirs_found: set[str] = set()
        for path in sorted(root.rglob("*")):
            if len(files) >= max_files:
                break
            if any(part in skip for part in path.parts):
                continue
            if path.is_file():
                rel = str(path.relative_to(root))
                try:
                    size = path.stat().st_size
                except OSError:  # pragma: no cover
                    continue
                files.append({"path": rel, "size": size, "kind": "file"})
                parent = str(Path(rel).parent)
                if parent != ".":
                    dirs_found.add(parent)
        return {
            "walker": "builtin",
            "files": files,
            "dirs": sorted(dirs_found),
            "ignored": 0,
            "truncated": len(files) >= max_files,
        }


def expand_mentions(text: str, *, workspace: str | Path) -> dict[str, Any]:
    """Resolve every ``@mention`` in a prompt to a real path inside the workspace.

    Three outcomes, all reported: ``resolved`` (a real file or directory), ``unresolved`` (no
    such path — the caller decides) and ``refused`` (the mention escapes the workspace, which
    is never silently followed). Symlinks whose resolved target leaves the workspace count as
    refused, the same rule ``project_context.py`` applies to its own reads.
    """
    root = Path(workspace).resolve()
    resolved: list[dict[str, Any]] = []
    unresolved: list[str] = []
    refused: list[dict[str, str]] = []
    for match in MENTION_RE.finditer(str(text or "")):
        raw = match.group("path")
        if "@" in raw or raw.startswith("/") or raw.startswith(".."):
            refused.append({"mention": raw, "reason": "the mention is an absolute or parent path"})
            continue
        candidate = root / raw
        try:
            real = candidate.resolve()
        except OSError:  # pragma: no cover
            unresolved.append(raw)
            continue
        if not str(real).startswith(str(root)):
            refused.append(
                {"mention": raw, "reason": "the resolved path leaves the workspace"}
            )
            continue
        if not candidate.exists():
            unresolved.append(raw)
            continue
        resolved.append(
            {
                "mention": raw,
                "path": str(candidate.relative_to(root)),
                "kind": "dir" if candidate.is_dir() else "file",
                "size": candidate.stat().st_size if candidate.is_file() else 0,
            }
        )
    return {
        "workspace": str(root),
        "mentions": resolved,
        "unresolved": unresolved,
        "refused": refused,
        "count": len(resolved) + len(unresolved) + len(refused),
    }


# --------------------------------------------------------------------------- #
# 5. Undo last turn (turn journal)
# --------------------------------------------------------------------------- #

TURN_SCHEMA = "skaldir.coding.turns/1"


def turns_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / "turns.jsonl"


def turn_backup_dir(state_dir: str | Path, turn_id: str) -> Path:
    return Path(state_dir) / "turn-backups" / turn_id


def read_turns(state_dir: str | Path) -> list[dict[str, Any]]:
    path = turns_path(state_dir)
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _append_turn(state_dir: str | Path, record: dict[str, Any]) -> None:
    path = turns_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def turn_snapshot(entry_path: str | Path, *, workspace: str | Path) -> dict[str, Any]:
    """The journal entry for one path as it is **now** — the before-state of a change."""
    root = Path(workspace).resolve()
    candidate = Path(entry_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if not str(candidate).startswith(str(root)):
        raise ValueError(f"{candidate} is outside the workspace {root}")
    rel = str(candidate.relative_to(root))
    if candidate.exists() and candidate.is_file():
        data = candidate.read_bytes()
        return {
            "path": rel,
            "existed": True,
            "before_sha256": _sha256_bytes(data),
            "before_bytes": len(data),
            "backup": "",
        }
    return {
        "path": rel,
        "existed": False,
        "before_sha256": None,
        "before_bytes": 0,
        "backup": "",
        "expected_creation": True,
    }


def open_turn(state_dir: str | Path) -> dict[str, Any] | None:
    """The turn a cross-process caller may still add files to (its ``turn.end`` is missing).

    The ``turn.file`` records are folded in, because that is where a shell's snapshots live —
    reading only the ``turn.begin`` line would hand back a turn with no files and the close
    would then write an empty file list (measured: undo then refused every turn as
    ``changed_since_turn``).
    """
    open_id = ""
    opening: dict[str, Any] = {}
    files: dict[str, dict[str, Any]] = {}
    for record in read_turns(state_dir):
        event = record.get("event")
        turn_id = str(record.get("turn_id", ""))
        if event == "turn.begin":
            open_id = turn_id
            opening = record
            files = {}
        elif event == "turn.file" and turn_id == open_id:
            incoming = record.get("file")
            if isinstance(incoming, dict) and incoming.get("path"):
                files[str(incoming["path"])] = incoming
        elif event == "turn.end" and turn_id == open_id:
            open_id = ""
            opening = {}
            files = {}
    if not open_id:
        return None
    return {
        "turn_id": open_id,
        **opening,
        "files": sorted(files.values(), key=lambda item: str(item["path"])),
    }


def journal_record_file(
    state_dir: str | Path,
    *,
    workspace: str | Path,
    path: str,
    expect_new: bool = False,
) -> dict[str, Any]:
    """Append one file to the **open** turn — the cross-process half of :class:`TurnJournal`.

    A shell cannot hold a Python object between two commands, so ``coding turn record``
    writes a ``turn.file`` record into the journal instead; :func:`turn_summaries` folds those
    records into the turn exactly as if :meth:`TurnJournal.record_file` had been called.
    """
    turn = open_turn(state_dir)
    if turn is None:
        raise LookupError("no open turn: run `coding turn begin` first")
    root = Path(workspace).resolve()
    entry = turn_snapshot(path, workspace=root)
    if not expect_new and entry["existed"]:
        backup_dir = turn_backup_dir(state_dir, str(turn["turn_id"]))
        backup_dir.mkdir(parents=True, exist_ok=True)
        count = len(turn.get("files") or [])
        backup = backup_dir / f"{count:03d}-{Path(entry['path']).name}"
        backup.write_bytes((root / entry["path"]).read_bytes())
        entry["backup"] = str(backup)
    _append_turn(
        state_dir,
        {
            "schema": TURN_SCHEMA,
            "event": "turn.file",
            "turn_id": str(turn["turn_id"]),
            "ts": round(_now(), 6),
            "file": entry,
        },
    )
    return entry


def close_open_turn(
    state_dir: str | Path, *, workspace: str | Path, clock: Callable[[], float] = _now
) -> dict[str, Any]:
    """Close the open turn, filling in each file's post-turn hash."""
    turn = open_turn(state_dir)
    if turn is None:
        raise LookupError("no open turn to close")
    root = Path(workspace).resolve()
    files: list[dict[str, Any]] = []
    for entry in turn.get("files") or []:
        recorded = dict(entry)
        candidate = (root / str(entry["path"])).resolve()
        if candidate.is_file():
            recorded["after_sha256"] = _sha256_bytes(candidate.read_bytes())
            recorded["after_bytes"] = candidate.stat().st_size
        else:
            recorded["after_sha256"] = None
            recorded["after_bytes"] = 0
        files.append(recorded)
    record = {
        "schema": TURN_SCHEMA,
        "event": "turn.end",
        "turn_id": str(turn["turn_id"]),
        "ts": round(clock(), 6),
        "workspace": str(root),
        "files": files,
    }
    _append_turn(state_dir, record)
    return record


@dataclass
class TurnJournal:
    """One turn's file operations, recorded so the turn can be undone byte-identically.

    ``record_file`` copies the bytes **before** the change into ``turn-backups/`` and stores
    both hashes. A file that did not exist yet stores ``before: null`` — undoing it deletes
    the file, which is the correct inverse of "created".
    """

    state_dir: Path
    turn_id: str
    started_at: float
    files: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def begin(
        cls,
        state_dir: str | Path,
        *,
        label: str = "",
        clock: Callable[[], float] = _now,
    ) -> TurnJournal:
        started = clock()
        turn_id = f"turn-{int(started * 1000)}-{os.getpid()}"
        journal = cls(state_dir=Path(state_dir), turn_id=turn_id, started_at=started)
        _append_turn(
            state_dir,
            {
                "schema": TURN_SCHEMA,
                "event": "turn.begin",
                "turn_id": turn_id,
                "label": label,
                "ts": round(started, 6),
                "workspace": str(Path.cwd()),
                "files": [],
            },
        )
        return journal

    def record_file(self, path: str | Path, *, workspace: str | Path) -> dict[str, Any]:
        """Snapshot ``path`` as it is now — call this *before* the turn changes it."""
        root = Path(workspace).resolve()
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        if not str(candidate).startswith(str(root)):
            raise ValueError(f"{candidate} is outside the workspace {root}")
        rel = str(candidate.relative_to(root))
        if any(entry["path"] == rel for entry in self.files):
            return next(entry for entry in self.files if entry["path"] == rel)
        existed = candidate.exists()
        before_bytes = candidate.read_bytes() if existed and candidate.is_file() else b""
        before_sha = _sha256_bytes(before_bytes) if existed and candidate.is_file() else None
        backup_name = ""
        if existed and candidate.is_file():
            directory = turn_backup_dir(self.state_dir, self.turn_id)
            directory.mkdir(parents=True, exist_ok=True)
            backup = directory / f"{len(self.files):03d}-{candidate.name}"
            backup.write_bytes(before_bytes)
            backup_name = str(backup)
        entry = {
            "path": rel,
            "existed": bool(existed),
            "before_sha256": before_sha,
            "before_bytes": len(before_bytes),
            "backup": backup_name,
        }
        self.files.append(entry)
        return entry

    def expect_new(self, path: str | Path, *, workspace: str | Path) -> dict[str, Any]:
        """Declare that the turn will **create** ``path`` (call it before the write).

        Undoing a created file means deleting it, so the journal must know the file was not
        there. ``record_file`` cannot tell a creation from an edit — it sees whatever exists —
        which is why this is a separate, explicit call.
        """
        root = Path(workspace).resolve()
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        if not str(candidate).startswith(str(root)):
            raise ValueError(f"{candidate} is outside the workspace {root}")
        rel = str(candidate.relative_to(root))
        existing = next((entry for entry in self.files if entry["path"] == rel), None)
        if existing is not None:
            return existing
        entry = {
            "path": rel,
            "existed": False,
            "before_sha256": None,
            "before_bytes": 0,
            "backup": "",
            "expected_creation": True,
        }
        self.files.append(entry)
        return entry

    def close(self, *, workspace: str | Path, clock: Callable[[], float] = _now) -> dict[str, Any]:
        """Close the turn, recording each file's post-turn hash so undo can detect a move."""
        root = Path(workspace).resolve()
        entries: list[dict[str, Any]] = []
        for entry in self.files:
            recorded = dict(entry)
            candidate = (root / entry["path"]).resolve()
            if candidate.exists() and candidate.is_file():
                recorded["after_sha256"] = _sha256_bytes(candidate.read_bytes())
                recorded["after_bytes"] = candidate.stat().st_size
            else:
                recorded["after_sha256"] = None
                recorded["after_bytes"] = 0
            entries.append(recorded)
        record = {
            "schema": TURN_SCHEMA,
            "event": "turn.end",
            "turn_id": self.turn_id,
            "ts": round(clock(), 6),
            "workspace": str(root),
            "files": entries,
        }
        _append_turn(self.state_dir, record)
        return record


def turn_summaries(state_dir: str | Path) -> list[dict[str, Any]]:
    """Every completed turn with its file set and whether it has been undone."""
    turns: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in read_turns(state_dir):
        turn_id = str(record.get("turn_id", ""))
        if not turn_id:
            continue
        if turn_id not in turns:
            turns[turn_id] = {
                "turn_id": turn_id,
                "label": record.get("label", ""),
                "workspace": record.get("workspace", ""),
                "opened_at": record.get("ts"),
                "closed_at": None,
                "open": True,
                "files": [],
                "undone": False,
            }
            order.append(turn_id)
        entry = turns[turn_id]
        if record.get("event") == "turn.begin":
            entry["label"] = record.get("label", entry["label"])
            entry["workspace"] = record.get("workspace", entry["workspace"])
        elif record.get("event") == "turn.file":
            entry = turns[turn_id]
            entry["open"] = entry["closed_at"] is None
            incoming = record.get("file")
            if isinstance(incoming, dict) and incoming.get("path"):
                by_path = {item["path"]: item for item in entry["files"]}
                by_path[str(incoming["path"])] = incoming
                entry["files"] = sorted(by_path.values(), key=lambda item: item["path"])
        elif record.get("event") == "turn.end":
            entry["closed_at"] = record.get("ts")
            entry["open"] = False
            ended = record.get("files", [])
            if ended:
                entry["files"] = ended
            entry["workspace"] = record.get("workspace", entry["workspace"])
        elif record.get("event") == "turn.undone":
            entry["undone"] = True
            entry["undone_at"] = record.get("ts")
    return [turns[turn_id] for turn_id in order]


def undo_turns(
    state_dir: str | Path,
    *,
    workspace: str | Path,
    count: int = 1,
    clock: Callable[[], float] = _now,
) -> dict[str, Any]:
    """Undo the last ``count`` completed turns, in reverse order, verifying every byte.

    Refusal rules, in order:

    * nothing to undo (``nothing_to_undo``);
    * the newest completed turn was already undone (``already_undone``);
    * a file the turn touched moved since (``changed_since_turn``) — the **whole turn** is
      refused, nothing is written: a partial undo is worse than none.
    """
    root = Path(workspace).resolve()
    completed = [
        turn
        for turn in turn_summaries(state_dir)
        if turn["closed_at"] is not None and turn["files"]
    ]
    undone_before = [turn for turn in completed if turn["undone"]]
    pending = [turn for turn in completed if not turn["undone"]]
    if not pending:
        return {
            "schema": TURN_SCHEMA,
            "status": "nothing_to_undo",
            "undone": [],
            "reason": (
                "every recorded turn was already undone"
                if undone_before
                else "no completed turn is recorded in the journal"
            ),
            "exit_code": 5,
        }
    target = pending[-count:]
    if len(target) != count:
        return {
            "schema": TURN_SCHEMA,
            "status": "not_enough_turns",
            "undone": [],
            "reason": f"{len(target)} turn(s) available, {count} requested",
            "exit_code": 5,
        }
    for turn in target:
        for entry in turn["files"]:
            full = (root / entry["path"]).resolve()
            if not str(full).startswith(str(root)):
                return _undo_refusal(
                    turn, f"{entry['path']} resolves outside the workspace", "escapes_workspace"
                )
            if not full.exists():
                if entry.get("after_sha256") is not None:
                    return _undo_refusal(
                        turn, f"{entry['path']} is missing but the turn left it in place",
                        "changed_since_turn",
                    )
                continue
            if not full.is_file():
                continue
            current = _sha256_bytes(full.read_bytes())
            if entry.get("after_sha256") != current:
                return _undo_refusal(
                    turn,
                    f"{entry['path']} changed since the turn "
                    f"({str(current)[:12]} != {str(entry.get('after_sha256'))[:12]})",
                    "changed_since_turn",
                )
    restored: list[dict[str, Any]] = []
    for turn in reversed(target):
        for entry in turn["files"]:
            full = (root / entry["path"]).resolve()
            if entry.get("before_sha256") is None:
                if full.exists():
                    full.unlink()
                    restored.append({"path": entry["path"], "action": "deleted", "sha256": None})
                continue
            if not entry.get("backup"):
                return _undo_refusal(turn, f"{entry['path']} has no backup", "backup_missing")
            backup = Path(entry["backup"])
            if not backup.exists():
                return _undo_refusal(turn, f"{entry['path']} backup is gone", "backup_missing")
            data = backup.read_bytes()
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(data)
            restored_sha = _sha256_bytes(full.read_bytes())
            if restored_sha != entry["before_sha256"]:
                return _undo_refusal(
                    turn,
                    f"{entry['path']} did not restore byte-identically "
                    f"({restored_sha[:12]} != {str(entry['before_sha256'])[:12]})",
                    "restore_mismatch",
                )
            restored.append(
                {"path": entry["path"], "action": "restored", "sha256": restored_sha}
            )
        _append_turn(
            state_dir,
            {
                "schema": TURN_SCHEMA,
                "event": "turn.undone",
                "turn_id": turn["turn_id"],
                "ts": round(clock(), 6),
                "workspace": str(root),
                "files": [item["path"] for item in restored],
            },
        )
    return {
        "schema": TURN_SCHEMA,
        "status": "undone",
        "turns": [turn["turn_id"] for turn in reversed(target)],
        "restored": restored,
        "files_restored": len(restored),
        "reason": "",
        "transcript": {
            "reverted": False,
            "reason": (
                "the session store keeps an immutable transcript and has no per-turn revert "
                "verb; this undo restores the turn's FILE effects, verified by sha256"
            ),
        },
        "exit_code": 0,
    }


def _undo_refusal(turn: dict[str, Any], reason: str, kind: str) -> dict[str, Any]:
    return {
        "schema": TURN_SCHEMA,
        "status": "refused",
        "undone": [],
        "reason": reason,
        "error_kind": kind,
        "turn_id": turn["turn_id"],
        "exit_code": 6 if kind == "changed_since_turn" else 5,
    }


# --------------------------------------------------------------------------- #
# 6. Session summary / status
# --------------------------------------------------------------------------- #

SUMMARY_SCHEMA = "skaldir.coding.summary/1"


def session_facts(
    *,
    session_id: str = "",
    runtime: str | Path | None = None,
    state_dir: str | Path | None = None,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """The facts a session status/summary is made of — each one measured or ``None``.

    Nothing here is estimated silently: token counts are labelled ``chars/4``, and a store
    that cannot be read is reported as ``available: false`` with the reason instead of an
    empty-looking zero.
    """
    facts: dict[str, Any] = {
        "schema": SUMMARY_SCHEMA,
        "session_id": session_id,
        "workspace": str(Path(workspace or Path.cwd()).resolve()),
        "messages": {"total": 0, "by_role": {}, "available": False, "reason": ""},
        "session_found": None,
        "history_hash": "",
        "title": "",
        "token_estimate": {"method": "chars/4", "tokens": 0},
        "last_turn": None,
        "turns": {"recorded": 0, "undone": 0, "files": 0},
        "plan": None,
        "events": {"total": 0, "available": False, "reason": "", "by_kind": {}},
        "model": None,
    }
    if session_id:
        try:
            from . import session_store

            path = session_store.session_store_path(runtime)
            with session_store.SessionStore(str(path)) as store:
                session = store.session(session_id)
                if session is None:
                    raise LookupError(f"session {session_id!r} is not in {path}")
                messages = store.messages(session_id, limit=session_store.MAX_MESSAGE_LIMIT)
                by_role: dict[str, int] = {}
                chars = 0
                tokens = 0
                for message in messages:
                    content = str(getattr(message, "content", "") or "")
                    chars += len(content)
                    tokens += int(getattr(message, "token_estimate", 0) or 0)
                    role = str(getattr(message, "role", "?"))
                    by_role[role] = by_role.get(role, 0) + 1
                facts["session_found"] = True
                facts["messages"] = {
                    "total": int(getattr(session, "message_count", len(messages)) or len(messages)),
                    "read": len(messages),
                    "by_role": by_role,
                    "available": True,
                    "reason": "",
                    "store": str(path),
                    "clamped": len(messages) >= session_store.MAX_MESSAGE_LIMIT,
                }
                facts["history_hash"] = str(
                    getattr(session, "history_sha256", "")
                    or session_store.history_hash(
                        [message.to_dict() for message in messages]
                    )
                )
                facts["title"] = str(getattr(session, "title", "") or "")
                facts["token_estimate"] = {
                    "method": f"{session_store.TOKEN_METHOD} (sum of per-message estimates)",
                    "tokens": tokens
                    or (session_store.estimate_tokens("x" * chars) if chars else 0),
                    "chars": chars,
                }
                if messages:
                    last = messages[-1]
                    facts["last_turn"] = {
                        "role": str(getattr(last, "role", "")),
                        "seq": int(getattr(last, "seq", 0) or 0),
                        "chars": len(str(getattr(last, "content", "") or "")),
                    }
        except LookupError as exc:
            facts["messages"]["reason"] = str(exc)
            facts["session_found"] = False
        except Exception as exc:  # noqa: BLE001 — an unreadable store is a fact, not a crash
            facts["messages"]["reason"] = f"session store not readable: {exc}"
    if state_dir is not None:
        turns = turn_summaries(state_dir)
        facts["turns"] = {
            "recorded": len(turns),
            "undone": sum(1 for turn in turns if turn["undone"]),
            "files": sum(len(turn["files"]) for turn in turns),
        }
        plan = load_plan(state_dir)
        if plan is not None:
            facts["plan"] = {
                "status": plan.get("status"),
                "task": plan.get("task"),
                "decision_complete": plan.get("decision_complete"),
                "guarded_actions": len(plan.get("guarded_actions") or []),
            }
    facts["model"] = effective_session_model(session_id, state_dir=state_dir)
    return facts


def render_summary_markdown(facts: dict[str, Any]) -> str:
    """A handoff summary in the shape Codex's compact prompt asks for (progress, decisions,
    what remains, references) — built from ``facts`` only, so it cannot describe work that
    was not measured."""
    messages = facts.get("messages") or {}
    turns = facts.get("turns") or {}
    plan = facts.get("plan") or {}
    model = facts.get("model") or {}
    lines = [
        f"# Session summary — {facts.get('session_id') or '(no session id)'}",
        "",
        "## Current progress and key decisions",
        f"- {messages.get('total', 0)} message(s) in the session "
        f"({', '.join(f'{k}:{v}' for k, v in (messages.get('by_role') or {}).items()) or 'none'}), "
        f"history hash `{str(facts.get('history_hash', ''))[:16]}`.",
        f"- ~{((facts.get('token_estimate') or {}).get('tokens', 0))} tokens "
        f"({((facts.get('token_estimate') or {}).get('method', 'chars/4'))} estimate, "
        "not a tokenizer).",
        f"- {turns.get('recorded', 0)} turn(s) in the journal, {turns.get('undone', 0)} undone, "
        f"{turns.get('files', 0)} file operation(s) recorded.",
        f"- Model: `{model.get('model') or 'unset'}` (reasoning "
        f"`{model.get('reasoning_effort') or 'unset'}`, from {model.get('source') or 'n/a'}).",
        "",
        "## Important context and constraints",
        f"- Workspace: `{facts.get('workspace')}`.",
        f"- Title: {facts.get('title') or '(untitled)'}.",
    ]
    if plan:
        lines.append(
            f"- Plan mode: {plan.get('status')} for {plan.get('task')!r} "
            f"(decision complete: {plan.get('decision_complete')}, "
            f"{plan.get('guarded_actions', 0)} action(s) guarded)."
        )
    else:
        lines.append("- Plan mode: no plan recorded.")
    lines += ["", "## What remains to be done"]
    remaining: list[str] = []
    if plan and plan.get("status") == "planning":
        remaining.append("apply or drop the open plan before mutating the workspace")
    if turns.get("undone"):
        remaining.append("re-run whatever the undone turn(s) were supposed to deliver")
    if not messages.get("available", False):
        remaining.append(f"session store unreadable: {messages.get('reason', 'unknown reason')}")
    last_turn = facts.get("last_turn")
    if last_turn:
        remaining.append(
            f"the last recorded message is role `{last_turn.get('role')}` "
            f"at seq {last_turn.get('seq')}"
        )
    lines += [f"- {item}" for item in (remaining or ["nothing outstanding was measured"])]
    lines += [
        "",
        "## References needed to continue",
        f"- session id: `{facts.get('session_id')}`",
        f"- session store: `{(messages.get('store') or 'not read')}`",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 7. Project setup (``/init``-like) — an AGENTS.md with injectable headings
# --------------------------------------------------------------------------- #

#: Exactly the headings ``project_context.py`` injects. Anything else is reported, not used.
INJECTABLE_HEADINGS = ("role", "rules", "constraints", "conventions", "commands", "glossary")


def render_agents_md(facts: dict[str, Any], *, project_name: str = "") -> str:
    """Build the AGENTS.md skeleton from measured facts (no invented commands)."""
    commands = (facts.get("commands") or {}).get("commands") or {}
    notes = (facts.get("commands") or {}).get("notes") or []
    command_lines: list[str] = []
    for group in ("test", "lint", "run", "make"):
        for command in commands.get(group, []):
            command_lines.append(f"- {group}: `{command}`")
    if not command_lines:
        command_lines.append("- no command was discovered in this project's own files")
    languages = ", ".join(
        f"{name}:{count}" for name, count in list((facts.get("languages") or {}).items())[:6]
    )
    lines = [
        f"# {project_name or facts.get('project_name') or 'Repository'} Guidelines",
        "",
        "## Role",
        f"Contributor guide for this repository. Files sampled: {facts.get('files_sampled', 0)}"
        f" (walker: {facts.get('walker', 'unknown')}). Languages: {languages or 'unknown'}.",
        "",
        "## Rules",
        "- Only the commands under *Commands* are declared; do not invent another one.",
        "- Never write outside the workspace; never rewrite history that is already committed.",
        "",
        "## Constraints",
        f"- Workspace: `{facts.get('workspace')}`.",
        f"- {facts.get('ignored_paths', 0)} path(s) are ignored by the project's own rules and "
        "are not part of the readable surface.",
        "",
        "## Conventions",
        "- Keep changes small and verifiable; a change without a check is not finished.",
        "",
        "## Commands",
    ]
    lines += command_lines
    lines += ["", "## Glossary"]
    if notes:
        lines += [f"- (manifest note) {note}" for note in notes]
    else:
        lines += ["- (none recorded)"]
    return "\n".join(lines) + "\n"


def write_project_doc(
    workspace: str | Path,
    *,
    filename: str = "AGENTS.md",
    force: bool = False,
    name: str = "",
) -> dict[str, Any]:
    """Write the project doc — refusing to overwrite an existing one unless forced.

    The refusal carries the existing file's sha256, so ``--force`` is an informed decision
    rather than a blind one.
    """
    root = Path(workspace).resolve()
    target = root / filename
    if target.exists() and not force:
        data = target.read_bytes()
        return {
            "status": "exists",
            "path": str(target),
            "sha256": _sha256_bytes(data),
            "bytes": len(data),
            "reason": f"{filename} already exists; rerun with --force to replace it "
            f"(current sha256 {_sha256_bytes(data)[:16]})",
            "exit_code": 6,
        }
    probe = plan_probe(root, "project init", max_files=200)
    facts = {
        **probe,
        "commands": probe["declared_commands"],
        "project_name": name or root.name,
    }
    text = render_agents_md(facts, project_name=name or root.name)
    previous_sha = _sha256_bytes(target.read_bytes()) if target.exists() else None
    target.write_text(text, encoding="utf-8")
    return {
        "status": "written",
        "path": str(target),
        "bytes": len(text.encode("utf-8")),
        "sha256": _sha256_text(text),
        "replaced_sha256": previous_sha,
        "headings": list(INJECTABLE_HEADINGS),
        "commands_found": facts["commands"]["commands"],
        "reason": "",
        "exit_code": 0,
    }


# --------------------------------------------------------------------------- #
# 8. Model + reasoning effort, mid-session
# --------------------------------------------------------------------------- #

EFFORTS = ("low", "medium", "high")


def session_model_path(state_dir: str | Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "default")
    return Path(state_dir) / "session-model" / f"{safe}.json"


def set_session_model(
    *,
    state_dir: str | Path,
    session_id: str,
    model: str = "",
    reasoning_effort: str = "",
    provider: str = "",
    clock: Callable[[], float] = _now,
) -> dict[str, Any]:
    """Set the per-session model/effort override — validated, never coerced."""
    if reasoning_effort and reasoning_effort not in EFFORTS:
        raise ValueError(f"reasoning effort must be one of {EFFORTS}, got {reasoning_effort!r}")
    path = session_model_path(state_dir, session_id)
    existing: dict[str, Any] = {}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    record = {
        "schema": "skaldir.coding.session-model/1",
        "session_id": session_id,
        "model": model or existing.get("model", ""),
        "reasoning_effort": reasoning_effort or existing.get("reasoning_effort", ""),
        "provider": provider or existing.get("provider", ""),
        "updated_at": round(clock(), 6),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return record


def clear_session_model(*, state_dir: str | Path, session_id: str) -> dict[str, Any]:
    path = session_model_path(state_dir, session_id)
    existed = path.exists()
    if existed:
        path.unlink()
    return {"session_id": session_id, "cleared": existed, "path": str(path)}


def effective_session_model(
    session_id: str,
    *,
    state_dir: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The effective model/effort with the layer that supplied it — never a silent default."""
    environment = dict(os.environ if env is None else env)
    layers: list[dict[str, Any]] = []
    if state_dir is not None and session_id:
        path = session_model_path(state_dir, session_id)
        if path.exists():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("model") or record.get("reasoning_effort"):
                    layers.append(
                        {
                            "layer": "session-override",
                            "precedence": 30,
                            "source": str(path),
                            "model": record.get("model", ""),
                            "reasoning_effort": record.get("reasoning_effort", ""),
                            "provider": record.get("provider", ""),
                        }
                    )
            except json.JSONDecodeError:  # pragma: no cover — a hand-edited file
                pass
    from .env import getenv

    env_model = str(environment.get("SKALDIR_LLM_MODEL") or getenv("SKALDIR_LLM_MODEL") or "")
    env_effort = str(
        environment.get("SKALDIR_LLM_REASONING") or getenv("SKALDIR_LLM_REASONING") or ""
    )
    if env_model or env_effort:
        layers.append(
            {
                "layer": "environment",
                "precedence": 20,
                "source": "SKALDIR_LLM_MODEL / SKALDIR_LLM_REASONING",
                "model": env_model,
                "reasoning_effort": env_effort,
                "provider": str(environment.get("SKALDIR_LLM_PROVIDER", "")),
            }
        )
    winner = max(layers, key=lambda item: item["precedence"]) if layers else None
    return {
        "session_id": session_id,
        "model": (winner or {}).get("model", ""),
        "reasoning_effort": (winner or {}).get("reasoning_effort", ""),
        "provider": (winner or {}).get("provider", ""),
        "source": (winner or {}).get("layer", "unset"),
        "source_detail": (winner or {}).get("source", ""),
        "layers": sorted(layers, key=lambda item: -item["precedence"]),
        "efforts": list(EFFORTS),
    }


__all__ = [
    "EFFORTS",
    "FIXER_OUTCOMES",
    "INJECTABLE_HEADINGS",
    "ITEM_STATUSES",
    "ITEM_TYPES",
    "MENTION_RE",
    "PLAN_SCHEMA",
    "READ_ONLY_COMMANDS",
    "STREAM_SCHEMA",
    "SUMMARY_SCHEMA",
    "TURN_SCHEMA",
    "FixOutcome",
    "PlanConflict",
    "RunStream",
    "TurnJournal",
    "classify_action",
    "clear_session_model",
    "complete_mention",
    "close_open_turn",
    "detect_project_commands",
    "effective_session_model",
    "expand_mentions",
    "failure_fingerprint",
    "fuzzy_indices",
    "invalidate_python_caches",
    "journal_record_file",
    "load_plan",
    "open_turn",
    "parse_failures",
    "plan_begin",
    "plan_close",
    "plan_guard",
    "plan_probe",
    "plan_propose",
    "plan_state_path",
    "read_stream",
    "read_turns",
    "render_agents_md",
    "render_summary_markdown",
    "run_tests",
    "save_plan",
    "score_match",
    "session_facts",
    "session_model_path",
    "set_session_model",
    "stream_command",
    "test_fix_loop",
    "turn_summaries",
    "undo_turns",
    "validate_stream",
    "write_project_doc",
]
