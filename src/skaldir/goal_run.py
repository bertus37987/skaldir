"""``skaldir run "<goal>"`` — the goal-locked multi-turn loop, in the product.

This is the engine behind the product command of the same name. It is the runtime
invariant of layer 7 (ENFORCE) plus the persistence of layer 8 (PERSIST), wired onto
the modules that already exist — nothing here re-implements them:

===============================================  ==================================
what happens                                     module under test
===============================================  ==================================
goal · criteria · budget · checkpoint · cursor    :mod:`skaldir.runs_store`
the completion contract, states, timeline         :mod:`skaldir.completion`
"the model may only *request* completion"          :class:`skaldir.completion.GateRunner`
the checks that really run                        :mod:`skaldir.completion.execute_check`
the turns, the tools, the transcript               :mod:`skaldir.agent_loop`
context threshold + compaction (fresh per turn)    :mod:`skaldir.context_manager`
workspace + conversation snapshots                 :mod:`skaldir.checkpoints`
the independent final verdict                      :mod:`skaldir.verify_service`
===============================================  ==================================

The loop, exactly:

1. **goal + criteria** — ``skaldir run`` creates the durable goal record (criteria, budget
   rows, first checkpoint) or resumes the one named by ``--resume``.
2. **plan** — the first turn asks the model for a numbered plan (``PLAN.md``); the
   runtime separately derives the *real check* for every criterion through
   :func:`skaldir.verify_service.build_plan`, and (unless ``--no-baseline``) executes
   them once so the starting state is measured instead of assumed.
3. **turns** — every turn is a fresh :class:`skaldir.agent_loop.AgentLoop` over the
   workspace's own tools plus two adapters over existing engines: ``edit_apply``
   (:mod:`skaldir.edit_tx`, atomic + syntax-verified) and ``run_tests``
   (:func:`skaldir.completion.execute_check` — the project's own detected runner,
   not a pytest hard-wire). A brand-new
   :class:`skaldir.context_manager.ContextManager` per turn is what "refreshed
   context" means here.
4. **gate** — a turn that ends in an answer is a *completion request*, nothing more:
   the checks run for real (``run_requirement_checks``) and
   :class:`skaldir.completion.GateRunner` decides. ``VERIFIED_COMPLETE`` is the only
   value that means anything; a rejection becomes ``REDIRECT`` (the blockers travel
   into the next turn's task) and a durable checkpoint is written.
5. **continue** — on ``FAIL``/``UNKNOWN`` the loop keeps going: new turn, refreshed
   context, or ``skaldir run --resume <goal-id>`` after a crash. Bounded by
   ``--max-turns`` / ``--max-steps`` / ``--wall-clock-s``.
6. **final verdict** — :func:`skaldir.verify_service.run_plan` over the workspace's
   declared plan. Exit 0 **only** on ``PASS``; the goal record ends
   ``VERIFIED_COMPLETE`` only then. Exhausting the bound with nothing accepted is
   ``BLOCKED_EXTERNAL`` (exit 3) with the blocking criterion named — never a pass.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- exit codes: the product's vocabulary, adopted when it is importable ------ #

try:  # the product contract, adopted when present (tests import this module bare)
    from . import cli_contract as _CONTRACT
except Exception:  # noqa: BLE001 — an older build without the module
    _CONTRACT = None  # type: ignore[assignment]

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NEEDS_CREDENTIALS = int(getattr(_CONTRACT, "EXIT_NEEDS_CREDENTIALS", 4))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))
EXIT_CONFLICT = int(getattr(_CONTRACT, "EXIT_CONFLICT", 6))
EXIT_INTERNAL = int(getattr(_CONTRACT, "EXIT_INTERNAL", 7))

#: The one instruction that opens a run: the plan comes from the model, the checks
#: come from the runtime (``verify_service.build_plan``), and both are recorded.
PLAN_INSTRUCTION = (
    "Step 1 — plan: write the numbered plan for this goal to \"PLAN.md\" "
    "(one step per criterion), then answer."
)

GOAL_SYSTEM_PROMPT = (
    "You are Skaldir working one long goal. You do not decide that the work is "
    "finished: you may only *request* completion with your answer, and the runtime "
    "decides it by running the declared checks. Work in small steps, run the checks "
    "you can run, and report exactly what you did. If a genuine external blocker needs "
    "the owner (access, credentials, a product decision), say so plainly and name it."
)


@dataclass
class GoalRunOptions:
    """Every knob of one goal-locked run — a declaration, not a suggestion."""

    goal: str = ""
    workspace: str = "."
    state_dir: str = ""
    session: str = "goal-run"
    criteria: list[dict[str, Any]] = field(default_factory=list)
    criteria_file: str = ""
    resume_goal_id: str = ""
    max_turns: int = 6
    max_steps: int = 64
    wall_clock_s: float = 900.0
    turn_wall_clock_s: float = 0.0
    check_timeout: float = 300.0
    baseline: bool = True
    snapshot: bool = True
    context_budget_tokens: int = 0
    context_trigger_fraction: float = 0.30
    preserve_recent_messages: int = 1
    asker_probability: float = 0.05
    lease_ttl: float = 900.0
    max_result_chars: int = 6000
    tier: str = "main"
    # --- §94 TOKEN-ECONOMY + PROGRESS-GUARD --------------------------------- #
    #: tool results larger than this are archived (id + digest + summary in the prompt);
    #: ``0`` = the archive's own default. ``archive_tool_results=False`` restores the old
    #: inline-and-truncate behaviour exactly.
    archive_threshold_chars: int = 0
    archive_tool_results: bool = True
    #: the progress guard: it demands a strategy change on a stall and stops on the
    #: second one. ``False`` = exactly the old behaviour (no decision at all).
    progress_guard: bool = True
    #: how many retries the provider may make per attempt **before** the visible commit
    #: point. Past it the gate refuses, whatever this says.
    max_provider_retries: int = 1
    #: §93 SEMANTIC VERIFY — after the deterministic plan passes, the completion claim is
    #: read once by the read-only verifier role (:mod:`skaldir.subagent_roles`) through
    #: Jev's ``semantic_verification`` question, and the gate can **block the promotion**
    #: (never grant one, never change the verdict). ``False`` turns it off, and the run
    #: report then says so by name — the gate is never silently absent.
    semantic_gate: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "workspace": str(self.workspace),
            "session": self.session,
            "criteria": [dict(item) for item in self.criteria],
            "criteria_file": self.criteria_file,
            "resume_goal_id": self.resume_goal_id,
            "max_turns": int(self.max_turns),
            "max_steps": int(self.max_steps),
            "wall_clock_s": float(self.wall_clock_s),
            "check_timeout": float(self.check_timeout),
            "baseline": bool(self.baseline),
            "snapshot": bool(self.snapshot),
            "context_budget_tokens": int(self.context_budget_tokens),
            "context_trigger_fraction": float(self.context_trigger_fraction),
        }


class GoalRunError(RuntimeError):
    """A run that cannot even start: reported with its own exit code."""

    def __init__(self, message: str, *, code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.code = int(code)


# --------------------------------------------------------------------------- #
# criteria                                                                      #
# --------------------------------------------------------------------------- #


def load_criteria_file(path: str | Path) -> list[dict[str, Any]]:
    """Read a criteria document: ``[{id|s cid, text|criterion, check, blocking}]``.

    Two shapes are accepted on purpose, because both already exist in the product: a
    bare list of criteria, and the workspace's own verification plan
    (``.godsv/verify.json`` — ``{"goal": …, "criteria": [{"criterion", "check"}]}``).
    Anything else is a usage error, never a silent empty contract.
    """
    target = Path(str(path)).expanduser()
    if not target.is_file():
        raise GoalRunError(f"criteria file not found: {target}", code=EXIT_NOT_FOUND)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoalRunError(
            f"criteria file is unreadable ({type(exc).__name__}: {exc})"
        ) from exc
    if isinstance(data, Mapping):
        items = data.get("criteria")
    else:
        items = data
    if not isinstance(items, (list, tuple)) or not items:
        raise GoalRunError(
            f"{target} carries no criteria — expected a list or {{'criteria': [...]}}"
        )
    return normalise_criteria(items)


def normalise_criteria(items: Iterable[Any]) -> list[dict[str, Any]]:
    """One canonical criterion record: ``{id, text, check, check_argv, blocking}``.

    ``check`` is either a **check id** (``"pytest"``, ``"api"``, a registered tool name)
    or the word ``"command"`` with the real vector in ``check_argv``. A document that
    declares a command as a list — ``"check": ["python", "spec/check_api.py"]``, the
    natural spelling — is read as that vector. It used to be run through ``str()``, which
    produced the *check id* ``"['/…/python', 'spec/check_api.py']"`` with an empty argv:
    a check nobody could run, so every criterion it declared stayed ``UNKNOWN`` while the
    plan looked well-formed. Nothing is stringified into a name it is not.
    """
    out: list[dict[str, Any]] = []
    for position, item in enumerate(items):
        if isinstance(item, Mapping):
            text = str(item.get("text") or item.get("criterion") or "").strip()
            identifier = str(
                item.get("id") or item.get("criterion_id") or f"c{position + 1}"
            )
            raw_check = (
                item.get("check")
                if item.get("check") not in (None, "")
                else item.get("verification", item.get("check_id", ""))
            )
            check_argv: list[str] = []
            if isinstance(raw_check, (list, tuple)):
                check_argv = [str(part) for part in raw_check if str(part).strip()]
                if not check_argv:
                    raise GoalRunError(
                        f"criterion {identifier!r} declares an empty check command"
                    )
                check = "command"
            else:
                check = str(raw_check or "").strip()
                declared = item.get("check_argv") or item.get("argv")
                if isinstance(declared, (list, tuple)):
                    check_argv = [str(part) for part in declared if str(part).strip()]
                    check = check or "command"
            blocking = bool(item.get("blocking", True))
        else:
            text = str(item).strip()
            identifier = f"c{position + 1}"
            check = ""
            check_argv = []
            blocking = True
        if not text:
            raise GoalRunError(f"criterion {identifier!r} has no text")
        out.append(
            {
                "id": identifier,
                "text": text,
                "check": check,
                "check_argv": check_argv,
                "blocking": blocking,
            }
        )
    if not out:
        raise GoalRunError("a goal-locked run needs at least one criterion")
    return out


def criteria_from_workspace(workspace: str | Path) -> list[dict[str, Any]]:
    """The criteria the workspace itself declares (``.godsv/verify.json``).

    A goal-locked run over a project that already declares what "done" means needs no
    repeated ``--criteria`` argument: the plan file IS the contract. Nothing is invented —
    a workspace without a declaration is a usage error that says so.
    """
    from .verify_service import PLAN_FILENAME, load_plan_file

    data, reason = load_plan_file(workspace)
    items = data.get("criteria") or []
    if not items:
        raise GoalRunError(
            "no criteria: pass --criteria FILE, or declare "
            f"{Path(workspace) / PLAN_FILENAME} in the workspace ({reason})"
        )
    return normalise_criteria(items)


def criteria_to_wire(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Criterion rows as the report carries them (one shape, always)."""
    return [
        {
            "id": str(item.get("id") or item.get("criterion_id") or ""),
            "text": str(item.get("text") or item.get("criterion") or ""),
            "check": str(item.get("check") or item.get("verification") or ""),
            "blocking": bool(item.get("blocking", True)),
        }
        for item in items
    ]


# --------------------------------------------------------------------------- #
# the tools a turn offers                                                       #
# --------------------------------------------------------------------------- #


def turn_tools(
    workspace: Path,
    state_dir: Path,
    session: str,
    bus: dict[str, Any],
    options: GoalRunOptions,
    *,
    delegation_provider: Any = None,
) -> list[Any]:
    """The turn's toolset: the product's builtins + two adapters over real engines.

    ``edit_apply`` carries a transaction document straight into
    :func:`skaldir.edit_tx.run_transaction` (the engine behind ``skaldir edit apply``),
    and ``run_tests`` solves the project's own detected test runner through
    :func:`skaldir.completion.execute_check` — the same producer the completion gate
    reads. Nothing is mocked; both are the product's own engines.

    ``delegation_provider`` is the provider client the run already owns. When it is given, the
    surface carries the real :func:`skaldir.runtime_delegation.spawn_subagents_tool` — the model
    can delegate to a bounded, role-ceilinged subagent family (audit §6b). Without it the tool is
    **not offered** and ``bus["delegation"]`` says why: a tool the runtime cannot honour is not
    put in front of the model. What a delegated child may really do is its role's own table —
    ``explore`` reads, ``verifier`` reads and runs the declared check, ``worker`` also writes
    through the core toolset and the edit-transaction engine — and the bus row reports each
    role's mode so "the model delegated" and "the model delegated something that could change
    the workspace" are different facts in the run's record.
    """
    from . import runtime_delegation
    from .agent_loop import AgentTool, builtin_tools
    from .completion import execute_check
    from .edit_tx import parse_spec, run_transaction

    def edit_apply(
        reason: str,
        files: list[dict[str, Any]],
        verification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        spec, problem = parse_spec(
            {
                "reason": reason,
                "files": files,
                "verification": verification or {"kind": "syntax"},
            }
        )
        if spec is None:
            return {"error": f"the transaction document was refused: {problem}"}
        record = run_transaction(spec, workspace, state_dir=state_dir / "edit_tx")
        payload = {
            "tx_id": record.get("tx_id"),
            "verdict": record.get("verdict"),
            "applied": record.get("applied"),
            "verified": record.get("verified"),
            "applied_paths": [
                item.get("path") for item in record.get("files", []) if item.get("ok")
            ],
            "refused": [
                {"path": item.get("path"), "reason": item.get("reason")}
                for item in record.get("files", [])
                if not item.get("ok")
            ],
            "diff": str(record.get("diff") or "")[:2000],
        }
        bus.setdefault("edits", []).append(
            {
                "tx_id": payload["tx_id"],
                "verdict": payload["verdict"],
                "reason": str(reason),
                "paths": [item.get("path") for item in record.get("files", [])],
                "applied": payload["applied"],
                "verified": payload["verified"],
                "syntax_ok": (record.get("verification") or {}).get("passed"),
                "after_sha256": [
                    item.get("after_sha256") for item in record.get("files", [])
                ],
            }
        )
        return payload

    def run_tests(argv: Sequence[str] | None = None) -> dict[str, Any]:
        """The suite check through the project's OWN runner (audit F05 fix).

        Without ``argv`` the runner is DETECTED from the project's own files
        (package.json test script → npm; pytest markers → pytest; Makefile;
        scripts/run_tests.sh) — a Node project is no longer told "your pytest
        suite failed", and an empty test project reports ``no_tests_found``
        instead of a failure that never happened. With ``argv`` the declared
        command vector runs exactly, like every other declared check.
        """
        vector = [str(part) for part in (argv or []) if str(part).strip()]
        evidence = execute_check(
            "tests",
            workspace,
            argv=vector or None,
            timeout=int(options.check_timeout),
            revision=int(bus.get("revision") or 0),
            workspace=str(workspace),
            session=session,
        )
        status = str(getattr(evidence, "status", "unknown") or "unknown")
        evidence_ok = bool(getattr(evidence, "passed", False))
        payload = {
            "ok": evidence_ok,
            "status": status,
            "check_id": str(getattr(evidence, "check_id", "tests")),
            "runner": str(getattr(evidence, "runner", "") or ""),
            "cmd": str(getattr(evidence, "cmd", "")),
            "exit": getattr(evidence, "exit_code", None),
            "source": str(getattr(evidence, "source", "")),
            "detail": str(getattr(evidence, "detail", "") or "")[:1200],
        }
        bus.setdefault("test_runs", []).append(payload)
        return payload

    tools = list(builtin_tools(workspace))
    tools.append(
        AgentTool(
            name="edit_apply",
            description=(
                "Apply one all-or-nothing edit transaction to the workspace (multi-file, "
                "atomic, syntax-verified). Each file entry is {path, edits:[{old,new}]}; "
                "old=null replaces the whole file."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "why this edit is made"},
                    "files": {
                        "type": "array",
                        "description": "one entry per file: {path, edits:[{old,new}]}",
                        "items": {"type": "object"},
                    },
                    "verification": {
                        "type": "object",
                        "description": "post-write verification: {kind: syntax|command|none}",
                        "default": {"kind": "syntax"},
                    },
                },
                "required": ["reason", "files"],
                "additionalProperties": False,
            },
            handler=edit_apply,
            parallel_safe=False,
            mutating=True,
            source="goal_run",
        )
    )
    tools.append(
        AgentTool(
            name="run_tests",
            description=(
                "Run the project's OWN test runner and return the real result — the "
                "runner is detected from the project's files (npm test for a Node "
                "project, pytest for a pytest project, Makefile/run_tests.sh where "
                "declared). Optional `argv` runs an explicit command vector instead. "
                "Reports status, runner, real exit code and detail; an empty test "
                "project is `no_tests_found`, a project with no runner is "
                "`no_test_runner` — neither is a pass. The completion gate reads "
                "the same producer."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "argv": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "optional: run exactly this command vector as the check "
                            "instead of the detected runner (e.g. [\"npm\", \"run\", "
                            "\"test:unit\"])"
                        ),
                    },
                },
                "additionalProperties": False,
            },
            handler=run_tests,
            parallel_safe=False,
            source="goal_run",
        )
    )
    # ---- delegation: the model's one way to hand work to a child (§6b) ------- #
    if delegation_provider is None:
        bus["delegation"] = {
            "offered": False,
            "reason": (
                "no provider client was handed to this turn's tool surface, so a delegated "
                "child could not be called — the tool is not offered rather than offered and "
                "guaranteed to fail"
            ),
        }
    else:
        role_modes = {
            role: runtime_delegation.role_mode(role)
            for role in runtime_delegation.GRANTABLE_ROLES
        }
        bus["delegation"] = {
            "offered": True,
            "tool": runtime_delegation.SPAWN_TOOL_NAME,
            "roles": list(runtime_delegation.GRANTABLE_ROLES),
            "role_modes": role_modes,
            "max_children": int(runtime_delegation.MAX_DELEGATED_CHILDREN),
            "max_depth": int(runtime_delegation.DELEGATION_MAX_DEPTH),
            "mode": ", ".join(f"{role}: {mode}" for role, mode in role_modes.items()),
            "reason": (
                "children run through the run's own provider client with a role ceiling; "
                "writes go through the product's core toolset and the edit-transaction engine "
                "inside the run's workspace, the declared check runs sandboxed (fail-closed), "
                "and the family is shallow (depth 1), shell-free and bounded in children"
            ),
        }
        tools.append(
            runtime_delegation.spawn_subagents_tool(
                workspace=workspace,
                state_dir=state_dir,
                session=session,
                provider=delegation_provider,
                on_result=lambda payload: bus.setdefault("delegations", []).append(
                    {
                        "run_id": payload.get("run_id"),
                        "run_dir": payload.get("run_dir"),
                        "role": payload.get("role"),
                        "children": payload.get("total"),
                        "delivered": payload.get("delivered"),
                        "tokens": payload.get("tokens"),
                        "mode": payload.get("mode"),
                        "failure_classes": payload.get("failure_classes") or {},
                    }
                ),
            )
        )

    # Paket 1 (audit F01): the goal run used to stop at six file builtins, so a goal could
    # not use the shell, the web or the browser through its own tools at all. The shared,
    # run-bound capability set is the same one the agent entry uses — one capability, one
    # model tool, no duplicate spellings.
    from .core_capabilities import core_capability_tools

    already = {getattr(t, "name", "") for t in tools}
    for cap_tool in core_capability_tools(
        workspace=workspace,
        runtime=state_dir,
        run_id=str(session or ""),
        exclude=already,
    ):
        tools.append(cap_tool)

    from .agent_toolset import (
        knowledge_read_tool,
        knowledge_search_tool,
        tool_search_tool,
    )

    already = {getattr(t, "name", "") for t in tools}
    if "tool_search" not in already:
        tools.append(tool_search_tool(runtime=state_dir, project_id=str(session or "")))
    if "knowledge_search" not in already:
        tools.append(knowledge_search_tool(runtime=state_dir))
    if "knowledge_read" not in already:
        tools.append(knowledge_read_tool(runtime=state_dir))
    return tools


# --------------------------------------------------------------------------- #
# the turn task                                                                 #
# --------------------------------------------------------------------------- #


def turn_task(
    *,
    options: GoalRunOptions,
    criteria: Sequence[Mapping[str, Any]],
    index: int,
    phase: str,
    redirect: str = "",
    next_action: str = "",
    operator: str = "",
) -> str:
    """The task one turn is asked to do — goal first, criteria always, redirect when set.

    ``operator`` is the text of the messages the operator delivered for *this* turn boundary
    (``/steer`` and the one queued message the run's store handed over). It is stated as a
    direction change inside the turn's own task, which is what makes it visible in the turn's
    prompt — and therefore checkable in the model request, not only in the run report.
    """
    lines = [f"GOAL: {options.goal}", ""]
    lines.append("ACCEPTANCE CRITERIA (each is blocking; only a real check certifies one):")
    for item in criteria:
        check = f" — declared check: {item['check']}" if item.get("check") else ""
        lines.append(f"- {item['id']}: {item['text']}{check}")
    lines.append("")
    if operator.strip():
        lines.append("THE OPERATOR HAS STEERED THIS RUN (this is a direction change; the goal")
        lines.append("itself is unchanged — follow this instruction for this turn):")
        for row in operator.strip().splitlines():
            lines.append(f"  {row.strip()}")
        lines.append("")
    if index == 0:
        lines.append(PLAN_INSTRUCTION)
    elif phase == "plan":
        lines.append("Continue the goal: the plan is written, do the next step now.")
    else:
        lines.append(
            "Continue the goal. Verification has not passed yet; do the remaining work "
            "and run the checks."
        )
    if next_action.strip():
        lines.append("")
        lines.append(f"The runtime's own cursor says: {next_action.strip()}")
    if redirect.strip():
        lines.append("")
        lines.append("The runtime rejected the last completion request:")
        for row in redirect.strip().splitlines():
            lines.append(f"  {row.strip()}")
        lines.append(
            "Keep working on the goal; do not claim completion again before this is "
            "fixed."
        )
    return "\n".join(lines)


def operator_block(messages: Sequence[Any]) -> str:
    """The operator's messages as the turn task says them (one line each, kind-labelled).

    The wording is the chat's own vocabulary: a ``/steer`` is urgent, a queued message is one of
    a series. The text is quoted verbatim — the runtime never rewrites what the operator said.
    Accepts the run's own wire rows (dicts, what ``drain_operator_messages`` returns) as well as
    the store's :class:`~skaldir.runs_store.Message` objects: one reader, so a field rename cannot
    silently empty the block (measured: an attribute-only reader turned every steer into a blank
    ``/queue`` line in the turn's prompt).
    """

    def field(item: Any, name: str, default: str = "") -> str:
        if isinstance(item, dict):
            return str(item.get(name) or default)
        return str(getattr(item, name, default) or default)

    lines: list[str] = []
    for item in messages:
        kind = field(item, "kind", "message")
        origin = field(item, "origin")
        body = " ".join(field(item, "text").split())
        label = "/steer" if kind == "steer" else "/queue"
        lines.append(f"{label} ({origin or 'operator'}): {body}".rstrip())
    return "\n".join(lines)


def drain_operator_messages(store: Any, goal_id: str, *, index: int, turn_name: str) -> list[dict]:
    """Take this turn boundary's messages: all steers, then one queued message.

    The store owns the rule (``RunsStore.consume_messages``), so the ordering cannot drift
    between callers; this function is the *run's* use of it, and it returns the wire form the
    report and the turn row carry. Every consumed row is stamped with ``index``/``turn_name``,
    which is what makes "which message changed the direction at which turn" a durable fact.
    """
    try:
        taken = store.consume_messages(goal_id, turn_index=int(index), turn_name=str(turn_name))
    except Exception:  # noqa: BLE001 — a store that cannot answer must not stop the turn
        return []
    return [
        {
            "id": int(item.message_id),
            "kind": str(item.kind),
            "origin": str(item.origin),
            "text": str(item.text),
            "created": float(item.created),
            "consumed_turn": int(item.consumed_turn),
            "turn_name": str(item.turn_name),
        }
        for item in taken
    ]


# --------------------------------------------------------------------------- #
# the run                                                                       #
# --------------------------------------------------------------------------- #


def run_goal(
    options: GoalRunOptions,
    *,
    client: Any,
    asker: Any = None,
    progress: Callable[[str, dict[str, Any]], Any] | None = None,
    on_goal: Callable[[str], None] | None = None,
    control: Any = None,
) -> dict[str, Any]:
    """Drive one goal-locked run and return its report (never a bare exit code).

    The report is the evidence: every turn, every completion attempt, the executed
    checks, the final verdict and the durable record behind them. ``report["exit_code"]``
    is the code the CLI must return, and it is 0 **only** when verification passed.

    ``on_goal`` is called with the goal id the moment the record is durable (the hand-over
    hook of ``skaldir run``: the chat attaches to a real row, never to a promise), and
    ``control`` — when given — is asked whether the run should stop at the next turn boundary
    (``control.stop_requested``), which is how a chat that closes ends its run honestly.
    """
    from . import verify_service
    from .agent_loop import AgentLoop, LoopLimits
    from .completion import (
        BLOCKED_EXTERNAL,
        TERMINAL_STATES,
        CompletionStore,
        DodItem,
    )
    from .context_manager import CompactingClient, ContextManager
    from .progress_guard import (
        ACTION_STOP,
        ACTION_STRATEGY_CHANGE,
        STRATEGY_CHANGE_DIRECTIVE,
        ProgressGuard,
        RetryGate,
        TurnObservation,
    )
    from .runs_store import RunsStore
    from .tool_archive import DEFAULT_THRESHOLD_CHARS as DEFAULT_ARCHIVE_THRESHOLD
    from .tool_archive import ToolArchive

    workspace = Path(str(options.workspace or ".")).expanduser()
    if not workspace.is_dir():
        raise GoalRunError(f"workspace is not a directory: {workspace}")
    workspace = workspace.resolve()
    state_dir = Path(str(options.state_dir or "")).expanduser() if options.state_dir else None
    if state_dir is None:
        raise GoalRunError("no runtime directory was resolved for this run")
    state_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    report: dict[str, Any] = {
        "goal": options.goal,
        "workspace": str(workspace),
        "state_dir": str(state_dir),
        "session": options.session,
        "resumed": bool(options.resume_goal_id),
        "criteria": [],
        "plan": {},
        "baseline": {},
        "turns": [],
        "completions": [],
        "checkpoints": [],
        "compactions": [],
        "verify": {},
        "semantic_gate": {},
        "terminal_state": "",
        "blocked_reason": "",
        "accepted_completions": 0,
        "turns_run": 0,
        "options": options.as_dict(),
        "exit_code": EXIT_BLOCKED,
        "runtime_errors": [],
    }

    store = RunsStore(state_dir / "runs.db")
    cstore = CompletionStore(state_dir, session_id=options.session)
    lease: dict[str, Any] = {}
    try:
        lease = store.acquire_writer(ttl=options.lease_ttl)
        report["lease"] = lease
        if lease.get("acquired") is False:
            raise GoalRunError(
                str(lease.get("reason") or "another writer holds the run store"),
                code=EXIT_CONFLICT,
            )

        # ---------------- goal: create it, or read it back ------------------- #
        if options.resume_goal_id:
            goal_record = store.goal(str(options.resume_goal_id))
            if goal_record is None:
                raise GoalRunError(
                    f"no goal {options.resume_goal_id!r} in {store.path}", code=EXIT_NOT_FOUND
                )
            goal_id = goal_record.goal_id
            goal_text = goal_record.goal_text
            if goal_record.terminal:
                raise GoalRunError(
                    f"goal {goal_id} is frozen in {goal_record.state} — only a new goal "
                    "may continue",
                    code=EXIT_CONFLICT,
                )
            criteria = criteria_to_wire(
                [
                    {
                        "id": item.criterion_id,
                        "text": item.text,
                        "check": item.verification_plan,
                        "blocking": item.blocking,
                    }
                    for item in store.criteria(goal_id)
                ]
            )
            options.goal = goal_text
            resume = _resume_context(store, cstore, goal_id)
            report["resume"] = resume
            report["durable_state_before"] = _durable_state(store, cstore, goal_id)
        else:
            goal_text = str(options.goal or "").strip()
            if not goal_text:
                raise GoalRunError("a goal-locked run needs a goal text")
            # A finished contract must not be renamed back into an active one.
            # The same goal text against a terminal contract is not a *new* goal
            # (completion's Block 3 invariant), and it is refused HERE — by name,
            # before a goal row is written and before a single model call —
            # instead of surfacing as a bare ValueError from begin_goal, which
            # was a traceback and exit 7 (a crash where a refusal belongs).
            contract = cstore.state()
            contract_state = str(contract.get("state") or "")
            if (
                contract_state in TERMINAL_STATES
                and str(contract.get("goal") or "").strip() == goal_text
            ):
                raise GoalRunError(
                    f"session contract is already {contract_state} for this goal — a "
                    "finished run is not reopened by re-running the same goal: give "
                    "the work a new identity (a different goal text) or use a fresh "
                    "--session",
                    code=EXIT_CONFLICT,
                )
            criteria = (
                normalise_criteria(options.criteria)
                if options.criteria
                else criteria_from_workspace(workspace)
            )
            fingerprint = str(verify_service.workspace_digest(workspace).get("digest") or "")
            goal_record = store.start_goal(
                goal_text,
                session_id=options.session,
                criteria=[
                    {
                        "criterion_id": item["id"],
                        "text": item["text"],
                        "blocking": item["blocking"],
                        "verification_plan": item["check"],
                    }
                    for item in criteria
                ],
                task_base=str(workspace),
                workspace_fingerprint=fingerprint,
            )
            goal_id = goal_record.goal_id
            report["workspace_fingerprint"] = fingerprint
            cstore.begin_goal(
                options.session,
                goal_text,
                [
                    DodItem(
                        id=item["id"],
                        text=item["text"],
                        blocking=item["blocking"],
                        verification=item["check"],
                    )
                    for item in criteria
                ],
                task_base=str(workspace),
            )
            resume = {"started": True, "goal_id": goal_id, "reason": "goal created"}
            report["resume"] = resume

        report["goal_id"] = goal_id
        report["goal"] = goal_text
        dod = _dod_for(criteria)
        if on_goal is not None:
            # the goal is durable *now*: whoever is waiting for the handle is told here, before
            # the plan and the turn loop, so an attached surface never races the record
            try:
                on_goal(str(goal_id))
            except Exception:  # noqa: BLE001 — a listener that raises must not stop the run
                pass

        # ---------------- the plan: the real check behind every criterion ---- #
        plan = verify_service.build_plan(
            criteria,
            goal=goal_text,
            workspace=workspace,
            revision=int(cstore.state().get("revision") or 0),
        )
        report["plan"] = plan.as_dict()
        if not plan.criteria:
            raise GoalRunError(
                "the run could not plan a single criterion — nothing would ever certify "
                "this goal",
                code=EXIT_BLOCKED,
            )
        # The plan is the authority on *which check* certifies each criterion.
        planned = [item.check_id for item in plan.criteria]
        for position, item in enumerate(criteria):
            if not item.get("check") and position < len(planned):
                item["check"] = planned[position]
        dod = _dod_for(criteria)

        # ---------------- the starting state, measured ---------------------- #
        if options.baseline and not options.resume_goal_id:
            baseline = verify_service.run_plan(
                plan,
                timeout=int(options.check_timeout),
                session=options.session,
                state_dir=state_dir / "verify",
            )
            report["baseline"] = _verify_wire(baseline, workspace)
            _record_outcomes(store, report, goal_id, workspace, baseline, options)

        # ---------------- the turns ----------------------------------------- #
        start_index = int(resume.get("next_index") or 0)
        redirect = str(resume.get("redirect") or "")
        next_action = str(resume.get("next_action") or "")
        bus: dict[str, Any] = {"revision": int(cstore.state().get("revision") or 0)}
        accepted = False
        verify_wire: dict[str, Any] = {}
        #: the raw verify pass behind ``verify_wire`` — the gate's own input
        gate_source: dict[str, Any] = {}
        last_rejection: dict[str, Any] = {}
        report["operator"] = report.get("operator") or []
        stopped_by: str = ""
        # ---- §94 PROGRESS-GUARD + TOKEN-ECONOMY ---------------------------- #
        # The guard is the *runtime decision* the run makes about itself; the archive is
        # the lever that keeps an oversized tool result out of the prompt; the retry gate
        # is the commit point a provider retry must stay behind.
        guard_enabled = bool(getattr(options, "progress_guard", True))
        guard = ProgressGuard(enabled=guard_enabled)
        archive = ToolArchive(
            state_dir,
            threshold_chars=int(getattr(options, "archive_threshold_chars", 0) or 0)
            or DEFAULT_ARCHIVE_THRESHOLD,
            enabled=bool(getattr(options, "archive_tool_results", True)),
        )
        retry_gate = RetryGate(max_attempts=int(getattr(options, "max_provider_retries", 1)))
        report["progress"] = {
            "enabled": guard_enabled,
            "stop_after": guard.stop_after,
            "decisions": [],
            "actions": [],
        }
        report["archive"] = {
            "enabled": archive.enabled,
            "threshold_chars": archive.threshold_chars,
            "root": str(archive.root),
            "results": [],
        }
        # The durable RUN RECORD of this goal (§94): the `runs` row the run supervisor
        # declares. Before this, `skaldir run` wrote no run record at all, so the progress
        # signals had nowhere to live and a resumed run could not tell a slow goal from a
        # stuck one. One row per goal, reused on resume (``create_run`` is idempotent).
        run_record_id = ""
        supervisor = store.supervisor()
        if supervisor is not None:
            with contextlib.suppress(Exception):
                run_record_id = supervisor.create_run(
                    str(options.session or "goal-run"),
                    run_id=f"goal-{goal_id}",
                    state="ACTIVE",
                    goal_revision=int(cstore.state().get("revision") or 0),
                    turn=int(resume.get("next_index") or 0),
                ).run_id
        report["run_record_id"] = run_record_id
        report["run_record_touches"] = []
        # ---- §94 PERSIST: the durable stall survives the process --------------- #
        # The signals are written every turn (``record_progress``); here they are READ
        # BACK, so a resumed run knows it was already told to change strategy instead of
        # re-arming the identical nudge and stalling in the identical place.
        restored_progress: dict[str, Any] = {}
        if run_record_id:
            try:
                durable = store.progress(run_record_id)
            except Exception as exc:  # noqa: BLE001 — an unreadable record is reported
                restored_progress = {
                    "restored": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            else:
                if durable is not None and int(getattr(durable, "turn", 0) or 0) > 0:
                    restored_progress = guard.restore(durable)
                elif durable is not None:
                    restored_progress = {
                        "restored": False,
                        "reason": "the durable record carries no turn yet (a fresh goal)",
                    }
        report["progress"]["restored"] = restored_progress

        for index in range(start_index, max(start_index, int(options.max_turns))):
            # A surface (a chat that closes) asks the run to stop here, at a boundary — never
            # mid-turn: the checkpoint the previous turn wrote is the honest place to stop, and
            # the durable goal stays resumable.
            if control is not None and bool(getattr(control, "stop_requested", False)):
                stopped_by = str(getattr(control, "stop_reason", "") or "stop requested")
                break
            phase = "plan" if index == 0 else "work"
            turn_name = f"t{index + 1}-{phase}"
            # the operator's delivery for *this* boundary: every pending /steer, then one
            # /queue message. Consuming stamps each row with this turn, so the durable record
            # says which message changed the direction at which turn.
            operator = drain_operator_messages(store, goal_id, index=index, turn_name=turn_name)
            report["operator"].extend(operator)
            task = turn_task(
                options=options,
                criteria=criteria,
                index=index,
                phase=phase,
                redirect=redirect,
                next_action=next_action,
                operator=operator_block(operator),
            )
            redirect, next_action = "", ""
            # MVP/87: the compaction decisions of this turn are published to the runtime's own
            # event store, so "Jev compacted the context" is visible in the timeline and not
            # only in the run report. `bus_for` yields a real subscription or an honest
            # NullBus — a store that cannot be written never stops a turn.
            from .jev import wiring as jev_wiring

            compaction_bus = jev_wiring.bus_for(
                state_dir,
                session_id=str(options.session or "goal-run"),
                step=index,
                source="context_manager.compaction",
            )
            manager = ContextManager(
                budget_tokens=int(options.context_budget_tokens) or None,
                trigger_fraction=float(options.context_trigger_fraction),
                goal=goal_text,
                criteria=[item["text"] for item in criteria],
                preserve_recent_messages=int(options.preserve_recent_messages),
                asker=asker,
                emit=jev_wiring.compaction_sink(compaction_bus),
            )
            wrapper = CompactingClient(client, manager)
            loop = AgentLoop(
                wrapper,
                turn_tools(
                    workspace,
                    state_dir,
                    options.session,
                    bus,
                    options,
                    # The provider the run already owns — **not** the compaction wrapper: a
                    # delegated child's call must not share the parent turn's retry gate, and
                    # the child's prompts are its own business (audit §6b).
                    delegation_provider=client,
                ),
                state_dir=state_dir / "agent",
                workspace=workspace,
                limits=LoopLimits(
                    max_steps=int(options.max_steps),
                    wall_clock_s=float(options.turn_wall_clock_s or 0.0),
                ),
                goal=goal_text,
                system_prompt=GOAL_SYSTEM_PROMPT,
                policy=True,
                max_result_chars=int(options.max_result_chars),
                progress=progress,
                archive=archive,
                retry_gate=retry_gate,
            )
            turn_started = time.time()
            result = loop.run(
                task, run_id=f"{options.session}-{turn_name}-{int(time.time())}"
            )
            bus["revision"] = int(cstore.state().get("revision") or 0)
            row = {
                "index": index,
                "name": turn_name,
                "phase": phase,
                "task": task,
                "run_id": result.run_id,
                "status": result.status,
                "reason": result.reason,
                "steps": int(result.steps),
                "tool_calls": int(result.tool_calls),
                "executed_calls": int(result.executed_calls),
                "usage": dict(result.usage),
                "usage_estimated": bool(result.usage_estimated),
                "duration_ms": int((time.time() - turn_started) * 1000),
                "answer": str(result.answer or ""),
                "transcript": result.transcript_path,
                "edits": list(bus.get("edits", []))[-3:],
                "test_runs": list(bus.get("test_runs", []))[-3:],
                # §6b: what the model delegated this turn (the run ids and their ceilings) —
                # "no child ran" and "a child ran and delivered nothing" are different facts
                "delegations": list(bus.get("delegations", []))[-3:],
                "delegation": dict(bus.get("delegation") or {}),
                "compactions": [
                    _compaction_wire(item) for item in manager.reports
                ],
                # what the operator delivered at *this* boundary (empty = nothing was waiting)
                "operator": list(operator),
                # §94: what the retry gate decided during this turn (named, with reasons)
                "retry": list(result.retry or []),
                # §94: whether this turn got past its commit point (a re-run of it
                # would re-emit visible output or run a tool twice) — read here, where
                # the whole-turn retry decision is really taken
                "commit": dict(getattr(result, "commit", {}) or {}),
                "tools_used": list(result.tools_used or []),
            }
            report["turns"].append(row)
            report["compactions"].extend(row["compactions"])
            report["turns_run"] = len(report["turns"])
            # §94 PERSIST: the run record is kept as current as the checkpoint beside it.
            # The recovered long run left `turn: 0` and a five-minute-stale heartbeat
            # after a turn had really run and checkpoint seq 2 was written, so the durable
            # row could not tell "no turn yet" from "a turn ran and the provider failed".
            touched = _touch_run_record(store, run_record_id, row=row)
            report["run_record_touches"].append(touched)

            if result.status != "answered":
                # A bound is not a completion request: record the cursor and continue.
                if result.reason == "needs_credentials":
                    report["exit_code"] = EXIT_NEEDS_CREDENTIALS
                    report["runtime_errors"].append(result.message or result.reason)
                    break
                if result.reason == "internal_error":
                    report["exit_code"] = EXIT_INTERNAL
                    report["runtime_errors"].append(result.message or result.reason)
                    break
                next_action = (
                    f"the previous turn hit its bound ({result.reason}) — "
                    f"{result.message or 'continue the goal'}"
                )
                # §94: a turn that failed past its commit point carries a **re-plan**
                # instead of a retry: the next turn is told what already happened (a
                # tool that ran, an answer the user saw) and continues from there. The
                # commit point itself is on the turn row, so the record shows why.
                replan = next(
                    (
                        str(item.get("replan") or "")
                        for item in reversed(list(result.retry or []))
                        if str(item.get("replan") or "")
                    ),
                    "",
                )
                if replan:
                    next_action = replan
                checkpoint = _write_checkpoint(
                    store,
                    cstore,
                    goal_id,
                    workspace,
                    state_dir,
                    options,
                    report,
                    index=index,
                    turn_name=turn_name,
                    phase=phase,
                    payload_extra={"bound": result.reason},
                    next_task=next_action,
                    criteria=criteria,
                )
                if not checkpoint:
                    continue
                continue

            # ---------------- the model asked; the gate decides ------------ #
            attempt = _attempt_completion(
                options=options,
                workspace=workspace,
                cstore=cstore,
                dod=dod,
                criteria=criteria,
                goal_text=goal_text,
                turn_name=turn_name,
                claim=str(result.answer or ""),
                bus=bus,
            )
            report["completions"].append(attempt)
            row["completion"] = attempt
            if attempt["accepted"]:
                accepted = True
                report["accepted_completions"] = len(
                    [item for item in report["completions"] if item["accepted"]]
                )
                verify_run = verify_service.run_plan(
                    plan,
                    timeout=int(options.check_timeout),
                    session=options.session,
                    state_dir=state_dir / "verify",
                )
                verify_wire = _verify_wire(verify_run, workspace)
                report["verify"] = verify_wire
                gate_source = dict(verify_run)
                _record_outcomes(store, report, goal_id, workspace, verify_run, options)
                if verify_wire["verdict"] == verify_service.VERDICT_PASS:
                    # §93: the deterministic plan passed — now the *claim* is read once by
                    # the read-only verifier role. The gate cannot grant or change the
                    # verdict; it can only refuse the promotion, and that refusal is a
                    # blocker the next turn must answer (never a silent close).
                    gate = _semantic_gate(
                        verify_run=verify_run,
                        wire=verify_wire,
                        goal_text=goal_text,
                        claim=str(result.answer or ""),
                        workspace=workspace,
                        options=options,
                    )
                    verify_wire["semantic_gate"] = gate
                    report["semantic_gate"] = gate
                    if not gate.get("blocking"):
                        break
                    redirect = _gate_redirect(gate)
                    next_action = "the semantic verifier refused the promotion"
                    _write_checkpoint(
                        store,
                        cstore,
                        goal_id,
                        workspace,
                        state_dir,
                        options,
                        report,
                        index=index,
                        turn_name=turn_name,
                        phase=phase,
                        payload_extra={
                            "verify_verdict": verify_wire["verdict"],
                            "semantic_gate": str(gate.get("state") or ""),
                            "promotion": str(gate.get("promotion") or ""),
                        },
                        next_task=redirect,
                        criteria=criteria,
                    )
                    continue
                # accepted by the gate but the independent pass did not pass: continue.
                redirect = _verdict_redirect(verify_wire)
                next_action = "the independent verification pass did not pass"
                _write_checkpoint(
                    store,
                    cstore,
                    goal_id,
                    workspace,
                    state_dir,
                    options,
                    report,
                    index=index,
                    turn_name=turn_name,
                    phase=phase,
                    payload_extra={"verify_verdict": verify_wire["verdict"]},
                    next_task=redirect,
                    criteria=criteria,
                )
                continue

            last_rejection = attempt
            _redirect_contract(cstore, turn_name, attempt)
            redirect = _rejection_redirect(attempt)
            next_action = "continue the goal: the completion request was rejected"
            # ---- §94 PROGRESS-GUARD: the decision, not a log line ----------- #
            # The four conditions are measured on THIS turn's real material: the answer's
            # fingerprint, the tools that really executed, the completion gate's evidence
            # fingerprint, and the durable revision. If all four say "nothing moved", the
            # runtime acts — it demands a strategy change, and stops if one does not come.
            if not attempt["accepted"]:
                observation = TurnObservation(
                    turn=index + 1,
                    answer_text=str(result.answer or ""),
                    tool_calls=tuple(str(name) for name in (result.tools_used or [])),
                    executed=int(getattr(result, "executed_calls", 0) or 0),
                    evidence=str(attempt.get("fingerprint") or ""),
                    state=str(bus.get("revision") or ""),
                    verification=str(attempt.get("verdict") or ""),
                    tokens_used=int(
                        sum(
                            int(value)
                            for value in (result.usage or {}).values()
                            if isinstance(value, (int, float))
                        )
                    ),
                    token_budget=int(options.context_budget_tokens or 0),
                    steps=int(result.steps),
                    max_steps=int(options.max_steps),
                )
                decision = guard.observe(observation)
                decision_wire = decision.to_dict()
                row["progress"] = decision_wire
                report["progress"]["decisions"].append(
                    {"turn": turn_name, "index": index, **decision_wire}
                )
                if run_record_id:
                    store.record_progress(run_record_id, decision=decision)
                progress_bus = jev_wiring.bus_for(
                    state_dir,
                    session_id=str(options.session or "goal-run"),
                    step=index,
                    source="progress_guard.decision",
                )
                progress_bus.emit(
                    "progress_decision",
                    {
                        "turn": turn_name,
                        "signal": decision.signal,
                        "action": decision.action,
                        "reason": decision.reason,
                        "conditions": decision.conditions,
                    },
                )
                if decision.action == ACTION_STRATEGY_CHANGE:
                    # a REAL action: the directive becomes the next turn's task material,
                    # so the model is told what to change rather than being re-asked
                    redirect = STRATEGY_CHANGE_DIRECTIVE
                    next_action = (
                        "the progress guard demanded a strategy change after "
                        f"{guard.state().no_progress_streak} stalled turn(s)"
                    )
                    report["progress"]["actions"].append(
                        {
                            "turn": turn_name,
                            "action": decision.action,
                            "signal": decision.signal,
                            "delivered": True,
                        }
                    )
                    if progress_bus is not None:
                        progress_bus.emit(
                            "progress_directive_delivered",
                            {"turn": turn_name, "signal": decision.signal},
                        )
                elif decision.action == ACTION_STOP:
                    reason = f"progress guard: {decision.reason}"
                    report["progress"]["actions"].append(
                        {
                            "turn": turn_name,
                            "action": decision.action,
                            "signal": decision.signal,
                            "delivered": False,
                        }
                    )
                    stopped_by = reason
                    report["blocked_reason"] = (
                        f"the run stopped at {turn_name} because it was stuck: "
                        f"{decision.reason} (signal {decision.signal}; "
                        f"no_progress_streak={guard.state().no_progress_streak}, "
                        f"no_tool_streak={guard.state().no_tool_streak})"
                    )
                    report["exit_code"] = EXIT_BLOCKED
                    _write_checkpoint(
                        store,
                        cstore,
                        goal_id,
                        workspace,
                        state_dir,
                        options,
                        report,
                        index=index,
                        turn_name=turn_name,
                        phase=phase,
                        payload_extra={"stopped": "progress_guard", "reason": decision.reason},
                        next_task="",
                        criteria=criteria,
                    )
                    break
            _write_checkpoint(
                store,
                cstore,
                goal_id,
                workspace,
                state_dir,
                options,
                report,
                index=index,
                turn_name=turn_name,
                phase=phase,
                payload_extra={
                    "verdict": attempt["verdict"],
                    "fingerprint": attempt["fingerprint"],
                    "blocking_failures": list(attempt["blocking_failures"]),
                },
                next_task=redirect,
                criteria=criteria,
            )
        # end of the turns

        # ---- §94 TOKEN-ECONOMY: what the archive actually saved this goal ----- #
        report["archive"]["results"] = list(archive.learned)
        report["archive"]["puts"] = len(archive.learned)
        report["archive"]["tokens_before"] = sum(
            int(item.get("tokens_before") or 0) for item in archive.learned
        )
        report["archive"]["tokens_after"] = sum(
            int(item.get("tokens_after") or 0) for item in archive.learned
        )
        report["archive"]["tokens_saved"] = sum(
            int(item.get("tokens_saved") or 0) for item in archive.learned
        )
        report["archive"]["token_source"] = "estimate/chars-4"
        report["progress"]["signals"] = guard.state().to_dict()
        retry_rows: list[dict[str, Any]] = []
        for turn_row in report["turns"]:
            for decision_row in turn_row.get("retry") or []:
                retry_rows.append({"turn": turn_row.get("name", ""), **decision_row})
        report["retry"] = {
            "max_attempts": retry_gate.max_attempts,
            "retries": retry_gate.retries,
            "refusals": retry_gate.refusals,
            "decisions": retry_rows,
            "budgets": _retry_budget_report(retry_gate, client),
        }

        # ---------------- the outcome --------------------------------------- #
        # A stop is not a verdict. When a surface (the chat that `skaldir run` opened) closes, it
        # ends the *surface*, not the goal: the goal stays open (WORKING) with the cursor the last
        # turn wrote, so `--resume <goal-id>` continues it, and the report says exactly why.
        if stopped_by:
            report["stopped_by"] = stopped_by
            report["blocked_reason"] = (
                f"the run stopped at a turn boundary ({stopped_by}) — the goal is still open; "
                f"`skaldir run --resume {goal_id}` continues it"
            )
            report["exit_code"] = EXIT_BLOCKED
        if not stopped_by and not report["verify"]:
            final_run = verify_service.run_plan(
                plan,
                timeout=int(options.check_timeout),
                session=options.session,
                state_dir=state_dir / "verify",
            )
            verify_wire = _verify_wire(final_run, workspace)
            report["verify"] = verify_wire
            _record_outcomes(store, report, goal_id, workspace, final_run, options)

        verdict = str(verify_wire.get("verdict") or "")
        # The gate record exists for EVERY terminal path — a run that never reached a
        # deterministic PASS carries the gate's own "nothing to promote" reason, so
        # "nobody read the claim" is never read as "the claim was checked".
        gate = report.get("semantic_gate") or _semantic_gate(
            verify_run=gate_source,
            wire=verify_wire,
            goal_text=goal_text,
            claim=_completion_claim(report),
            workspace=workspace,
            options=options,
        )
        report["semantic_gate"] = gate
        if verify_wire:
            verify_wire["semantic_gate"] = gate
        if stopped_by:
            pass  # the exit code and the reason are already set above; no state may be closed
        elif verdict == verify_service.VERDICT_PASS and gate.get("blocking"):
            # The checks passed; the *claim* did not survive the read. The goal is not
            # closed, the deterministic PASS is not weakened, and the reason is named.
            reason = (
                "the deterministic verification passed, but the semantic gate blocked "
                f"the promotion ({gate.get('state')}): "
                f"{gate.get('promotion_reason') or 'no reason given'}"
            )
            report["blocked_reason"] = reason
            report["blocked_criteria"] = []
            _finish_goal(
                store,
                cstore,
                report,
                goal_id,
                state_dir,
                options,
                criteria=criteria,
                state=BLOCKED_EXTERNAL,
                blocked_reason=reason,
            )
            report["exit_code"] = EXIT_BLOCKED
        elif verdict == verify_service.VERDICT_PASS:
            _finish_goal(
                store,
                cstore,
                report,
                goal_id,
                state_dir,
                options,
                criteria=criteria,
                state="VERIFIED_COMPLETE",
            )
            report["exit_code"] = EXIT_OK
        elif accepted:
            _finish_goal(
                store,
                cstore,
                report,
                goal_id,
                state_dir,
                options,
                criteria=criteria,
                state="INCOMPLETE",
            )
            report["exit_code"] = EXIT_VERIFICATION_FAILED
        else:
            reason = _blocked_reason(last_rejection, verify_wire)
            report["blocked_reason"] = reason
            report["blocked_criteria"] = [
                str(item.get("criterion_id") or item.get("criterion"))
                for item in verify_wire.get("criteria", [])
                if str(item.get("status")) != "passed"
            ]
            _finish_goal(
                store,
                cstore,
                report,
                goal_id,
                state_dir,
                options,
                criteria=criteria,
                state=BLOCKED_EXTERNAL,
                blocked_reason=reason,
            )
            report["exit_code"] = EXIT_BLOCKED
        report["duration_ms"] = int((time.time() - started) * 1000)
        report["durable_state_after"] = _durable_state(store, cstore, goal_id)
        report["terminal_state"] = str(report["durable_state_after"].get("goal_state") or "")
        _write_report(state_dir, report)
        return report
    finally:
        with_errors = report.get("runtime_errors") or []
        report.setdefault("runtime_errors", with_errors)
        try:
            store.release_writer(owner=str(lease.get("owner") or ""))  # type: ignore[has-type]
        except Exception:  # noqa: BLE001 — a released lease is best effort on the way out
            pass
        with_errors = report.setdefault("runtime_errors", [])
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# the pieces                                                                    #
# --------------------------------------------------------------------------- #


def _dod_for(criteria: Sequence[Mapping[str, Any]]) -> list[Any]:
    """The definition-of-done the completion gate reads — one item per criterion.

    A criterion that declares its check as a **command vector** carries the vector and names
    its own check id (``command:<criterion id>``), so the completion gate's runner executes
    *that* criterion's command and its DoD item matches the evidence it produces. While the
    vector was dropped here, the gate held a criterion with the bare id ``command`` that no
    runner knew: the claim was refused (safely, ``not-certifiable``) and only the independent
    verify pass could ever close the goal (audit §6c).
    """
    from .completion import DodItem

    items: list[DodItem] = []
    for position, item in enumerate(criteria):
        identifier = str(item.get("id") or f"c{position + 1}")
        vector = tuple(
            str(part) for part in (item.get("check_argv") or ()) if str(part).strip()
        )
        items.append(
            DodItem(
                id=identifier,
                text=str(item.get("text") or ""),
                blocking=bool(item.get("blocking", True)),
                verification=f"command:{identifier}" if vector else str(item.get("check") or ""),
                check_argv=vector,
            )
        )
    return items


def _resume_context(store: Any, cstore: Any, goal_id: str) -> dict[str, Any]:
    """What a resumed process reads back: the cursor, the checkpoint, the rejection."""
    checkpoint = store.latest_checkpoint(goal_id) or {}
    payload = store.checkpoint_payload(checkpoint) if checkpoint else {}
    document = (payload.get("document") or {}) if isinstance(payload, dict) else {}
    stored = (document.get("payload") or {}) if isinstance(document, dict) else {}
    cursor = store.cursor(goal_id)
    prior_rejections = 0
    try:
        prior_rejections = len(
            [
                row
                for row in cstore.timeline(limit=500)
                if row.get("kind") == "verification"
                and str(row.get("status") or "") != "VERIFIED_COMPLETE"
            ]
        )
    except Exception:  # noqa: BLE001 — a broken timeline never blocks a resume
        prior_rejections = 0
    return {
        "goal_id": goal_id,
        "checkpoint_id": str(checkpoint.get("checkpoint_id") or ""),
        "checkpoint_seq": int(checkpoint.get("seq") or 0),
        "checkpoint_next_action": str(checkpoint.get("next_action") or ""),
        "checkpoint_sha256": str(checkpoint.get("sha256") or ""),
        "payload_readable": bool(payload.get("readable")),
        "payload_sha256_matches": bool(payload.get("sha256_ok")),
        "previous_turn": str(stored.get("turn") or ""),
        "previous_phase": str(stored.get("phase") or ""),
        "blocking_failures": list(stored.get("blocking_failures") or []),
        "next_index": int(stored.get("turn_index") or -1) + 1,
        "redirect": str(stored.get("next_task") or ""),
        "next_action": str(cursor.get("next_action") or checkpoint.get("next_action") or ""),
        "open_criteria": list(cursor.get("open_criteria") or []),
        "prior_rejections": prior_rejections,
    }


def _durable_state(store: Any, cstore: Any, goal_id: str) -> dict[str, Any]:
    """The durable record, read back — the goal, its criteria, the contract state."""
    goal = store.goal(goal_id)
    contract = {}
    try:
        state = cstore.state()
        contract = {
            "state": state.get("state"),
            "revision": int(state.get("revision") or 0),
            "goal": state.get("goal"),
            "timeline": len(cstore.timeline(limit=500)),
        }
    except Exception as exc:  # noqa: BLE001 — a broken contract is reported, not hidden
        contract = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "goal_id": goal_id,
        "goal_state": goal.state if goal else "",
        "blocked_reason": goal.blocked_reason if goal else "",
        "criteria": [
            {"id": item.criterion_id, "status": item.status, "check": item.verification_plan}
            for item in store.criteria(goal_id)
        ],
        "cursor": store.cursor(goal_id),
        "contract": contract,
    }


def _compaction_wire(report: Any) -> dict[str, Any]:
    wire = report.to_wire() if hasattr(report, "to_wire") else {}
    if wire:
        return wire
    return {
        "reason": getattr(report, "reason", ""),
        "fired": getattr(report, "fired", None),
        "applied": getattr(report, "applied", None),
        "error": getattr(report, "error", ""),
    }


def _attempt_completion(
    *,
    options: GoalRunOptions,
    workspace: Path,
    cstore: Any,
    dod: Sequence[Any],
    criteria: Sequence[Mapping[str, Any]],
    goal_text: str,
    turn_name: str,
    claim: str,
    bus: dict[str, Any],
) -> dict[str, Any]:
    """The model asked for completion. The runtime runs the checks and asks the gate."""
    from .completion import (
        COMPLETION_REQUESTED,
        VERIFYING,
        CompletionRequest,
        GateRunner,
        run_requirement_checks,
    )

    revision = int(cstore.state().get("revision") or 0)
    claimed = [item.text for item in dod] if dod else [item["text"] for item in criteria]
    cstore.transition(COMPLETION_REQUESTED, attempt_id=turn_name, claimed=claimed)
    cstore.transition(VERIFYING, attempt_id=turn_name)
    evidence = run_requirement_checks(
        list(dod),
        workspace,
        timeout=int(options.check_timeout),
        revision=revision,
        workspace=str(workspace),
        session=options.session,
    )
    changed = sorted(
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if path.is_file() and path.suffix in (".py", ".md", ".json")
    )
    request = CompletionRequest(
        user_goal=goal_text,
        claimed_completed=claimed,
        implementation_summary=str(claim or ""),
        files_changed=changed,
        tests_run=[str(item.cmd) for item in evidence],
        test_results="; ".join(f"{item.check_id}={item.status}" for item in evidence),
        attempt_id=turn_name,
        created=time.time(),
    )
    gate = GateRunner(
        repo_root=workspace,
        dod=list(dod),
        task_base=str(workspace),
        revision=revision,
        workspace=str(workspace),
        session=options.session,
    )
    result = gate.verify(request, evidence, store=cstore)
    return {
        "turn": turn_name,
        "claim": str(claim or "")[:2000],
        "revision": revision,
        "verdict": result.status,
        "blocking_failures": list(result.blocking_failures),
        "major_failures": list(result.major_failures),
        "next_actions": list(result.next_actions),
        "fingerprint": result.fingerprint,
        "repeat_count": int(result.repeat_count),
        "escalate": bool(result.escalate),
        "redirect": result.redirect_md(),
        "accepted": result.status == "VERIFIED_COMPLETE",
        "evidence": [
            {
                "check_id": item.check_id,
                "status": item.status,
                "cmd": item.cmd,
                "source": item.source,
                "detail": str(item.detail or "")[:400],
                "exit_code": getattr(item, "exit_code", None),
                "revision": item.revision,
                "session": item.session,
            }
            for item in evidence
        ],
    }


def _redirect_contract(cstore: Any, turn_name: str, attempt: Mapping[str, Any]) -> None:
    """Rejection ⇒ REDIRECT ⇒ WORKING: the goal stays open, in the durable contract."""
    from .completion import REDIRECT, WORKING

    cstore.transition(
        REDIRECT,
        attempt_id=turn_name,
        redirect=list(attempt.get("blocking_failures") or [])[:3],
    )
    cstore.transition(WORKING, attempt_id=turn_name)


def _rejection_redirect(attempt: Mapping[str, Any]) -> str:
    lines = []
    for item in list(attempt.get("blocking_failures") or [])[:5]:
        lines.append(f"- BLOCKER: {item}")
    for item in list(attempt.get("major_failures") or [])[:3]:
        lines.append(f"- MAJOR: {item}")
    if not lines:
        lines.append("- the completion gate produced no named blocker; re-run the checks")
    return "\n".join(lines)


def _verdict_redirect(verify_wire: Mapping[str, Any]) -> str:
    lines = [f"- {item.get('criterion')} [{item.get('check_id')}]: {item.get('status')} "
             f"— {str(item.get('reason') or '')[:200]}"
             for item in verify_wire.get("criteria", [])
             if item.get("status") != "passed"]
    return "\n".join(lines) or "- the independent verification pass did not pass"


def _snapshot(workspace: Path, state_dir: Path, label: str) -> dict[str, Any]:
    """One unified workspace+conversation checkpoint, through ``skaldir.checkpoints``."""
    from .checkpoints import create_checkpoint

    return create_checkpoint(
        workspace=workspace,
        state_dir=state_dir / "checkpoints",
        edit_state_dir=state_dir / "edit_tx",
        label=f"{label}: the completion request was not accepted",
    )


def _write_checkpoint(
    store: Any,
    cstore: Any,
    goal_id: str,
    workspace: Path,
    state_dir: Path,
    options: GoalRunOptions,
    report: dict[str, Any],
    *,
    index: int,
    turn_name: str,
    phase: str,
    payload_extra: Mapping[str, Any],
    next_task: str,
    criteria: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """One durable checkpoint per turn that did not close the goal.

    Two records, on purpose: the run store's own checkpoint (cursor + hash-verified
    payload, what ``--resume`` reads back) and, when ``--snapshot`` is on, a unified
    workspace+conversation checkpoint through :mod:`skaldir.checkpoints`. A snapshot that
    cannot be taken is reported, never hidden.
    """
    snapshot_id = ""
    if options.snapshot:
        try:
            snapshot = _snapshot(workspace, state_dir, turn_name)
            snapshot_id = str(snapshot.get("checkpoint_id") or "")
            report.setdefault("checkpoints", []).append(
                {
                    "turn": turn_name,
                    "kind": "workspace",
                    "checkpoint_id": snapshot_id,
                    "status": str(snapshot.get("status") or snapshot.get("verdict") or ""),
                    "reason": str(snapshot.get("reason") or ""),
                    "path": str(snapshot.get("path") or ""),
                }
            )
        except Exception as exc:  # noqa: BLE001 — a snapshot failure is not a lost goal
            report.setdefault("runtime_errors", []).append(
                f"workspace checkpoint failed: {type(exc).__name__}: {exc}"
            )
    payload = {
        "phase": phase,
        "turn": turn_name,
        "turn_index": int(index),
        "goal_id": goal_id,
        "session": options.session,
        "next_task": str(next_task or ""),
        "criteria_open": [
            item.criterion_id
            for item in store.criteria(goal_id)
            if item.status != "verified"
        ],
        "test_runs": list(
            (report["turns"][-1].get("test_runs") if report["turns"] else []) or []
        ),
        "edits": [
            {"tx_id": item.get("tx_id"), "paths": item.get("paths"), "verdict": item.get("verdict")}
            for turn in report["turns"]
            for item in turn.get("edits", [])
        ],
        "workspace_checkpoint": snapshot_id,
        **dict(payload_extra),
    }
    checkpoint = store.write_checkpoint(
        goal_id,
        next_action=str(next_task or "continue the goal"),
        payload=payload,
    )
    report.setdefault("checkpoints", []).append(
        {
            "turn": turn_name,
            "kind": "run",
            "checkpoint_id": str(checkpoint.get("checkpoint_id") or ""),
            "seq": int(checkpoint.get("seq") or 0),
            "sha256": str(checkpoint.get("sha256") or ""),
            "path": str(checkpoint.get("path") or ""),
            "next_action": str(checkpoint.get("next_action") or ""),
        }
    )
    return checkpoint


def _record_outcomes(
    store: Any,
    report: dict[str, Any],
    goal_id: str,
    workspace: Path,
    verify_run: Mapping[str, Any],
    options: GoalRunOptions,
) -> None:
    """Every criterion's outcome goes into the durable ledger — with its evidence ref.

    ``verified``/``failed`` come from the executed run only; the ref carries the run id
    and the criterion's own evidence hash, so nothing here can be a status without
    evidence.
    """
    run_id = str(verify_run.get("run_id") or "")
    for row in verify_run.get("results") or []:
        status = str(row.get("status") or "")
        outcome = "verified" if status == "passed" else "failed"
        criterion_id = str(row.get("criterion_id") or "")
        if not criterion_id:
            continue
        ref = f"verify:{run_id}:{criterion_id}:{str(row.get('evidence_sha256') or '')[:16]}"
        try:
            store.record_criterion_outcome(
                goal_id,
                criterion_id,
                outcome=outcome,
                evidence_refs=[ref],
                check_id=str(row.get("check_id") or ""),
                workspace=str(workspace),
                revision=None,
                cmd=str(row.get("cmd") or ""),
                exit_code=int(row.get("exit_code") if row.get("exit_code") is not None else -1),
                run_id=run_id,
            )
        except Exception as exc:  # noqa: BLE001 — recorded, never hidden
            report.setdefault("runtime_errors", []).append(
                f"criterion {criterion_id} outcome not recorded: {type(exc).__name__}: {exc}"
            )


def _finish_goal(
    store: Any,
    cstore: Any,
    report: dict[str, Any],
    goal_id: str,
    state_dir: Path,
    options: GoalRunOptions,
    *,
    criteria: Sequence[Mapping[str, Any]],
    state: str,
    blocked_reason: str = "",
) -> None:
    """Park the durable goal in its terminal state — the contract follows, once."""
    from .completion import (
        BLOCKED_EXTERNAL,
        VERIFIED_COMPLETE,
    )

    try:
        current_cstate = getattr(cstore, "state", None)
        if state == VERIFIED_COMPLETE and current_cstate != VERIFIED_COMPLETE:
            cstore.transition(VERIFIED_COMPLETE, attempt_id="goal-run")
        elif state == BLOCKED_EXTERNAL and current_cstate != BLOCKED_EXTERNAL:
            cstore.transition(
                BLOCKED_EXTERNAL, reason=blocked_reason, attempt_id="goal-run"
            )
    except Exception as exc:  # noqa: BLE001 — the durable record is still written
        report.setdefault("runtime_errors", []).append(
            f"completion contract not parked: {type(exc).__name__}: {exc}"
        )
    try:
        store.set_goal_state(
            goal_id,
            state,
            blocked_reason=str(blocked_reason or ""),
            wake_source="operator:supply the missing external input" if blocked_reason else "",
        )
    except Exception as exc:  # noqa: BLE001 — reported, not swallowed silently
        report.setdefault("runtime_errors", []).append(
            f"goal state not parked: {type(exc).__name__}: {exc}"
        )
    try:
        store.write_checkpoint(
            goal_id,
            next_action=(
                "goal verified — nothing left to do"
                if state == VERIFIED_COMPLETE
                else f"goal parked in {state}: {blocked_reason or 'the bound was reached'}"
            ),
            payload={
                "terminal": True,
                "state": state,
                "verify_verdict": str(report.get("verify", {}).get("verdict") or ""),
            },
        )
    except Exception as exc:  # noqa: BLE001
        report.setdefault("runtime_errors", []).append(
            f"final checkpoint not written: {type(exc).__name__}: {exc}"
        )


# --------------------------------------------------------------------------- #
# the semantic gate — the promotion read (MVP/93 §3)                            #
# --------------------------------------------------------------------------- #


def _gate_input(
    *,
    verify_run: Mapping[str, Any],
    wire: Mapping[str, Any],
    goal_text: str,
    workspace: Path,
) -> dict[str, Any]:
    """The deterministic run, in the shape ``verify_service.semantic_gate`` reads.

    Nothing is invented and nothing is re-run: the verdict, the plan digest, the
    executed checks with their exit codes and the evidence hashes are what the verify
    pass already recorded. The trimmed ``wire`` is the fallback for a caller that only
    kept the report.
    """
    raw = dict(verify_run or {})
    if not raw.get("results"):
        raw["results"] = [
            {
                "criterion": item.get("criterion"),
                "criterion_id": item.get("criterion_id"),
                "status": item.get("status"),
                "blocking": item.get("blocking", True),
                "cmd": item.get("cmd"),
                "exit_code": item.get("exit_code"),
                "evidence_sha256": item.get("evidence_sha256"),
                "output_sha256": item.get("output_sha256"),
                "output_tail": item.get("output_tail"),
            }
            for item in (wire.get("criteria") or [])
        ]
    raw.setdefault("verdict", wire.get("verdict"))
    raw.setdefault("plan_sha256", wire.get("plan_sha256"))
    raw.setdefault("goal", goal_text)
    raw.setdefault("workspace", str(workspace))
    if not raw.get("workspace_digest") and wire.get("workspace_digest"):
        raw["workspace_digest"] = {"digest": wire.get("workspace_digest")}
    return raw


def _semantic_gate(
    *,
    verify_run: Mapping[str, Any],
    wire: Mapping[str, Any],
    goal_text: str,
    claim: str,
    workspace: Path,
    options: Any,
) -> dict[str, Any]:
    """Read the completion claim with the **read-only verifier role** — the gate itself.

    The chain is ``deterministic VERIFY → verifier role → Jev → promotion``. Three
    things this wiring guarantees, because each of them was violated by an unwired gate:

    * the gate **runs** on the production path (a module only its own test imports is
      not wired), and it runs with a real :func:`subagent_roles.verifier_dispatcher` —
      not a fixture — whose role and offered tools are recorded beside the answer;
    * the gate is **never absent from the record**: a refusal, an unreachable verifier,
      a disabled gate and a non-``PASS`` verdict each produce a payload with a *named*
      reason, so ``no semantic advisory was obtained`` can never be read as agreement;
    * the gate can only **block** a promotion. ``verdict_after_gate`` is asserted to
      equal the deterministic verdict, and a gate answer can never turn a ``FAIL`` or an
      ``UNKNOWN`` into anything else — it is not even consulted for one.
    """
    from . import subagent_roles, verify_service

    enabled = bool(getattr(options, "semantic_gate", True))
    if not enabled:
        return {
            "schema": verify_service.SEMANTIC_GATE_SCHEMA,
            "gate": "semantic_verification",
            "deterministic_verdict": str(wire.get("verdict") or ""),
            "verdict_after_gate": str(wire.get("verdict") or ""),
            "state": verify_service.GATE_SKIPPED,
            "promotion": verify_service.PROMOTION_SKIPPED,
            "blocking": False,
            "authority": "advisory",
            "certifies": False,
            "disabled": True,
            "replaces_deterministic_checks": False,
            "promotion_reason": (
                "the semantic gate was turned off for this run "
                "(GoalRunOptions.semantic_gate=False) — the deterministic verdict "
                "stands alone and is not to be read as having been checked semantically"
            ),
            "reasons": ["the semantic gate was disabled by the caller"],
            "verifier": {"role": "", "read_only": True, "tools_offered": [], "source": ""},
        }

    dispatcher = subagent_roles.verifier_dispatcher()
    gate = verify_service.semantic_gate(
        _gate_input(
            verify_run=verify_run, wire=wire, goal_text=goal_text, workspace=workspace
        ),
        claim=str(claim or ""),
        workspace=workspace,
        dispatcher=dispatcher,
        goal_id=str(wire.get("run_id") or ""),
    )
    policy = subagent_roles.ROLE_POLICIES.get(dispatcher.role)
    gate["verifier"] = {
        "role": str(dispatcher.role),
        "read_only": bool(getattr(policy, "read_only", False)),
        "tools_offered": list(dispatcher.offered),
        "source": "skaldir.subagent_roles.verifier_dispatcher()",
    }
    # the two invariants, checked where the promotion decision is taken
    if str(gate.get("verdict_after_gate") or "") != str(wire.get("verdict") or ""):
        gate["promotion"] = verify_service.PROMOTION_BLOCKED
        gate["blocking"] = True
        gate["tampered"] = True
        gate["promotion_reason"] = (
            "the gate reported a different verdict than the deterministic pass — a "
            "semantic advisory may never replace a check"
        )
    if str(gate.get("promotion")) == verify_service.PROMOTION_ALLOWED and str(
        wire.get("verdict") or ""
    ) != verify_service.VERDICT_PASS:
        gate["promotion"] = verify_service.PROMOTION_BLOCKED
        gate["blocking"] = True
        gate["tampered"] = True
        gate["promotion_reason"] = (
            "a promotion was allowed for a run that did not pass deterministic "
            "verification — refused by the runtime, not by the gate"
        )
    # Only a **live** advisory may stop a goal that passed its own checks. A canned or
    # fixture answer would otherwise be a test convenience holding a real run hostage;
    # it is recorded, and it is not obeyed.
    if gate.get("blocking") and not bool(gate.get("live_jev")):
        gate["advisory_only"] = True
        gate["blocking"] = False
        gate["promotion"] = verify_service.PROMOTION_UNREAD
        gate["promotion_reason"] = (
            "the verifier's answer did not come from a live provider "
            f"(provider_mode={gate.get('provider_mode')!r}, live_jev=False) — an "
            "advisory no model made cannot stop a goal that passed its own checks; "
            "the deterministic verdict stands and this is recorded, not obeyed"
        )
    return gate


def _gate_redirect(gate: Mapping[str, Any]) -> str:
    """The next turn's task material when the semantic gate refuses the promotion."""
    lines = ["- SEMANTIC GATE BLOCKED the promotion: " + str(gate.get("state") or "")]
    for item in list(gate.get("reasons") or [])[:5]:
        lines.append(f"- WHY: {item}")
    for item in list(gate.get("evidence_gaps") or [])[:5]:
        lines.append(f"- EVIDENCE GAP: {item}")
    for item in list(gate.get("unverifiable") or [])[:3]:
        lines.append(f"- UNVERIFIABLE: {item}")
    lines.append(
        "- the deterministic checks passed; this is a read of the *claim*. Close the "
        "gap with evidence (a run, an artifact, a citation), or state the claim exactly "
        "as the evidence supports it."
    )
    return "\n".join(lines)


def _touch_run_record(store: Any, run_id: str, *, row: Mapping[str, Any]) -> dict[str, Any]:
    """Push this turn's facts onto the durable run record (turn, tokens, heartbeat).

    The run record is what a supervisor, a `runs check` and a *resumed* process read
    first; a checkpoint without it is half the truth. The recovered long run
    (``/home/willy/gv-lr83``) shows the failure mode: a turn really ran and failed on the
    provider, checkpoint seq 2 was written — and the ``runs`` row still said ``turn: 0``
    with a heartbeat five minutes in the past, which reads as "nothing ever started".

    Best effort by construction: a record write never breaks a turn, and what it did (or
    why it did not) is reported back to the caller rather than swallowed.
    """
    if not run_id:
        return {"updated": False, "reason": "this run has no run record"}
    supervisor = store.supervisor()
    if supervisor is None:
        return {"updated": False, "reason": "the store carries no run supervisor"}
    tokens = 0
    for value in (row.get("usage") or {}).values():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            tokens += int(value)
    fields = {
        "turn": int(row.get("index") or 0) + 1,
        "tokens_used": int(tokens),
        "last_verification": str(row.get("status") or ""),
    }
    try:
        ok = bool(supervisor.update(run_id, **fields))
    except Exception as exc:  # noqa: BLE001 — a record write must not stop a turn
        return {
            "updated": False,
            "reason": f"{type(exc).__name__}: {exc}",
            **fields,
        }
    return {"updated": ok, **fields}


def _retry_budget_report(retry_gate: Any, client: Any) -> dict[str, Any]:
    """The two retry budgets, reconciled on the record instead of in prose.

    The provider brings its own §43 ceiling (``max_retries + 1`` attempts) and the
    runtime's gate brings ``max_provider_retries``. They are not rivals: the gate may
    only **narrow** the provider's policy, never widen it. Both numbers and which one
    bound the run are reported, so a run can be read without guessing.
    """
    policy_attempts = 0
    try:
        config = getattr(client, "config", None)
        policy_attempts = int(getattr(config, "max_retries", 0) or 0) + 1
    except Exception:  # noqa: BLE001 — an unreadable client config is reported as unknown
        policy_attempts = 0
    payload: dict[str, Any] = {
        "unit": "attempts made on one step; the gate's ``max_attempts`` counts retries",
        "gate": retry_gate.to_dict(),
    }
    if policy_attempts:
        payload.update(retry_gate.effective_attempts(policy_attempts))
    else:
        payload["provider_max_attempts"] = None
        payload["note"] = "the provider's §43 ceiling could not be read from the client"
    return payload


def _completion_claim(report: Mapping[str, Any]) -> str:
    """The claim the gate reads: the last accepted completion, else the last attempt."""
    completions = list(report.get("completions") or [])
    if not completions:
        return ""
    accepted = [item for item in completions if item.get("accepted")]
    return str((accepted or completions)[-1].get("claim") or "")


def _verify_wire(run: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    """The verification run, trimmed to the fields a report needs (nothing invented)."""
    from . import verify_service

    freshness: dict[str, Any] = {}
    try:
        freshness = verify_service.run_freshness(dict(run), workspace)
    except Exception as exc:  # noqa: BLE001
        freshness = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "run_id": str(run.get("run_id") or ""),
        "verdict": str(run.get("verdict") or ""),
        "verdict_reason": str(run.get("verdict_reason") or ""),
        "counts": dict(run.get("counts") or {}),
        "plan_sha256": str(run.get("plan_sha256") or ""),
        "workspace_digest": str((run.get("workspace_digest") or {}).get("digest") or ""),
        "workspace_changed_during_run": bool(run.get("workspace_changed_during_run")),
        "freshness": freshness,
        "criteria": [
            {
                "criterion": str(item.get("criterion") or ""),
                "criterion_id": str(item.get("criterion_id") or ""),
                "check_id": str(item.get("check_id") or ""),
                "kind": str(item.get("kind") or ""),
                "status": str(item.get("status") or ""),
                "blocking": bool(item.get("blocking", True)),
                "cmd": str(item.get("cmd") or ""),
                "exit_code": item.get("exit_code"),
                "reason": str(item.get("reason") or ""),
                "evidence_sha256": str(item.get("evidence_sha256") or ""),
                "artifact_sha256": str(item.get("artifact_sha256") or ""),
                "output_sha256": str(item.get("output_sha256") or ""),
                "duration_ms": item.get("duration_ms"),
                "output_tail": str(item.get("output_tail") or "")[-400:],
            }
            for item in (run.get("results") or [])
        ],
    }


def _blocked_reason(last_rejection: Mapping[str, Any], verify_wire: Mapping[str, Any]) -> str:
    """Why the goal is blocked — the unmet criterion, named, with its check."""
    failing = [
        item
        for item in verify_wire.get("criteria", [])
        if str(item.get("status")) != "passed"
    ]
    if failing:
        names = ", ".join(
            f"{item.get('criterion_id') or item.get('criterion')} "
            f"[{item.get('check_id')}]"
            for item in failing[:3]
        )
        detail = "; ".join(str(item.get("reason") or "")[:160] for item in failing[:2])
        return (
            f"{len(failing)} criterion/criteria could not be met: {names}. {detail}".strip()
        )
    blockers = list(last_rejection.get("blocking_failures") or [])
    if blockers:
        return "criterion could not be met: " + "; ".join(blockers[:3])
    return "the goal was not verified before the bound was reached"


def _write_report(state_dir: Path, report: Mapping[str, Any]) -> str:
    """Keep the run report next to the state it describes (one file per goal)."""
    target = state_dir / "goal_run" / f"{report.get('goal_id') or 'run'}.json"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        tmp.replace(target)
    except OSError:
        return ""
    report_path = str(target)
    try:
        report["report_path"] = report_path  # type: ignore[index]
    except TypeError:  # pragma: no cover — a Mapping that refuses the key
        pass
    return report_path


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_INTERNAL",
    "EXIT_NEEDS_CREDENTIALS",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_VERIFICATION_FAILED",
    "GOAL_SYSTEM_PROMPT",
    "PLAN_INSTRUCTION",
    "GoalRunError",
    "GoalRunOptions",
    "criteria_from_workspace",
    "criteria_to_wire",
    "load_criteria_file",
    "normalise_criteria",
    "run_goal",
    "turn_task",
    "turn_tools",
]
