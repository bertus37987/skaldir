"""Subagent **roles** — a named bundle of tool rights and a hard ceiling.

The ask this answers (owner, after studying the MiniMax harness — see
``MVP/93-subagent-roles.md``): a child agent is not just "a model with a turn
budget", it is a *role*. A role is three things and nothing else:

1. a **tool right set** — which tool names the child may call at all;
2. a **spawn right** — whether the child may delegate further (a ``worker`` may
   not, on purpose: a fan-out that can fan out again is an unbounded tree);
3. a **budget** — its own turns / tokens / tool calls, so a role is bounded in
   cost as well as in rights.

Why the ceiling lives here and not in the prompt
------------------------------------------------
"An ``explore`` child has no edit tool" is a *provisioning* fact and a
*dispatch* fact, never a sentence in a prompt:

* :func:`provision_tools` is the pure filter that removes what the role may not
  have, so an ``explore`` child is **not offered** ``edit``/``write``/``exec``
  in the first place — the model cannot pick a tool it was never handed;
* :class:`RoleToolDispatcher` is the enforcement point for the other half: a
  tool call that arrives anyway (a hallucinated name, a replayed message, a
  brief that claims a right it was not granted) is **refused, named, and never
  executed**. The caller gets :class:`ToolCeilingRefused`, whose ``code`` is a
  stable machine token (``role_ceiling.tool_not_granted``), not a polite
  request to the model to behave.

The refusal is also *recorded*: every dispatch appends to ``report()``, so a
run's own record shows what a role attempted and what the ceiling did — a
refusal is evidence, not a lost event.

What this module deliberately is **not**
----------------------------------------
* It is not an executor. A granted tool with no handler is reported as
  ``mode: "declared"`` (the right exists, this process did not run it) —
  the ceiling is about *rights*; execution stays with the layers that own it.
* It is not a permission system. The permission profile / approval layer still
  decides whether a granted tool may touch a given path. A role narrows a
  child; it never widens anybody's authority.
* It does not consult the model. Nothing here asks the child to respect its
  role; the child is *given* a smaller tool list and is refused at the call.

Roles (the canonical three, plus the unroled historical case):

============  ==================================================  ===========
role          tools                                               may spawn
============  ==================================================  ===========
``explore``   read/glob/grep/git_read/web_search/web_fetch          no
``worker``    explore **+** edit/write/exec/test_run                 **no**
``verifier``  read/glob/grep/git_read/web_search/web_fetch/test_run  no
``main``      everything (the root's own, unrestricted surface)      yes
``""``        unroled — the harness' pre-role behaviour (depth cap only)
============  ==================================================  ===========
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CANONICAL_ROLES",
    "DEFAULT_CHILD_ROLE",
    "DELEGATION_TOOLS",
    "EXEC_TOOLS",
    "KNOWN_TOOLS",
    "READ_TOOLS",
    "ROLE_BUDGETS",
    "ROLE_MAIN",
    "ROLE_POLICIES",
    "ROLE_ROOT",
    "ROLE_EXPLORE",
    "ROLE_VERIFIER",
    "ROLE_WORKER",
    "ROLES",
    "TEST_TOOLS",
    "UNROLED",
    "WRITE_TOOLS",
    "REFUSAL_BUDGET_EXHAUSTED",
    "REFUSAL_DELEGATION_FORBIDDEN",
    "REFUSAL_TOOL_NOT_GRANTED",
    "REFUSAL_UNKNOWN_ROLE",
    "RoleBudget",
    "RoleBudgetExhausted",
    "RoleToolDispatcher",
    "SubagentRole",
    "ToolCallRecord",
    "ToolCeilingRefused",
    "ToolRefusal",
    "canonical_role",
    "ceiling_filter",
    "ceiling_report",
    "contract_tool_classes",
    "dispatch_for_role",
    "is_role",
    "policy_for",
    "provision_tools",
    "resolve_role",
    "role_for_kind",
    "role_names",
    "tool_classes",
    "tools_for",
    "validate_role",
    "verifier_dispatcher",
]

# --------------------------------------------------------------------------- #
# the vocabulary                                                                #
# --------------------------------------------------------------------------- #

ROLE_EXPLORE = "explore"
ROLE_WORKER = "worker"
ROLE_VERIFIER = "verifier"
#: The owner's named research/production roles — each a distinct right bundle and,
#: through settings, its own provider/api-key (`subagent.roles.<role>.*`).
ROLE_RESEARCHER = "researcher"
ROLE_REPO_SCOUT = "repo_scout"
ROLE_DEBUGGER = "debugger"
ROLE_EVIDENCE_COLLECTOR = "evidence_collector"
ROLE_IMPLEMENTER = "implementer"
ROLE_PLANNER = "planner"
#: The root's own surface: no ceiling. Kept as a *named* role so that "who may
#: do everything" is stated in one table instead of being an implicit default.
ROLE_MAIN = "main"
#: Alias used by callers that think in the harness' terms (the root run).
ROLE_ROOT = ROLE_MAIN

#: The three roles a *child* may hold (MiniMax' canonical enrolment).
#: The owner's named roles — the research/production set the orchestrator offers.
NAMED_ROLES: tuple[str, ...] = (
    ROLE_RESEARCHER,
    ROLE_REPO_SCOUT,
    ROLE_DEBUGGER,
    ROLE_EVIDENCE_COLLECTOR,
    ROLE_IMPLEMENTER,
    ROLE_VERIFIER,
    ROLE_PLANNER,
)
CANONICAL_ROLES: tuple[str, ...] = (ROLE_EXPLORE, ROLE_WORKER, ROLE_VERIFIER)
#: Every role name this module knows — the canonical three first (the documented
#: enrolment order), then the owner's named set, then the root.
ROLES: tuple[str, ...] = tuple(dict.fromkeys(
    (ROLE_EXPLORE, ROLE_WORKER, ROLE_VERIFIER) + NAMED_ROLES + (ROLE_MAIN,)
))

#: Read / research tools — no side effect on the workspace.
#:
#: **Derived, never hand-written.** A ceiling over a vocabulary the runtime does not
#: speak is not a ceiling: the first version of this table named ``read``/``edit``/
#: ``exec`` while the product's registry offers ``read_file``/``patch``/``terminal``, so
#: every real tool was "unknown" and an ``explore`` child could not read a file at all.
#: The classes below come from the tools' own declarations
#: (:mod:`skaldir.tools_core.contract`: ``mutates``, ``permission.subprocess``), with a
#: documented static part for the names the agent loop offers itself (``read_text``,
#: ``edit_apply``, ``run_tests``, …) and for the short spellings the briefs use. A test
#: asserts the two never drift and that no name lands in two classes.
_CONTRACT_READ = "read"
_CONTRACT_WRITE = "write"
_CONTRACT_EXEC = "exec"

#: The names the *agent loop* offers (``agent_loop.builtin_tools`` + the goal run's two
#: adapters) and the child's answer to a ``[[spawn:…]]`` marker. Each one is a real tool
#: with a real effect, so each one has a class.
_LOOP_TOOL_CLASSES: dict[str, str] = {
    "read_text": _CONTRACT_READ,
    "sha256_file": _CONTRACT_READ,
    "read_archive": _CONTRACT_READ,
    "list_dir": _CONTRACT_READ,
    "write_text": _CONTRACT_WRITE,
    "edit_apply": _CONTRACT_WRITE,
    "run_tests": "test",
    # the short spellings a brief or a prompt may use - the same classes as the real
    # tools they denote, so a role's right set means the same thing in both dialects
    "read": _CONTRACT_READ,
    "glob": _CONTRACT_READ,
    "grep": _CONTRACT_READ,
    "git_read": _CONTRACT_READ,
    "edit": _CONTRACT_WRITE,
    "write": _CONTRACT_WRITE,
    "exec": _CONTRACT_EXEC,
    "test_run": "test",
}

#: Delegation: never a class a child holds. ``spawn`` starts a task, ``spawn_append``
#: continues one — the same entry point, so the same ceiling covers both (MiniMax gates
#: ``task_append`` with ``task`` for exactly this reason).
DELEGATION_TOOLS: frozenset[str] = frozenset({"spawn", "spawn_append"})


def contract_tool_classes() -> dict[str, str]:
    """``name -> read | write | exec`` from the core toolset's **own contracts**.

    Read from the registry when it is importable (the product's single source of truth
    for what a tool may touch); an unimportable registry yields ``{}`` — the static part
    of the vocabulary still stands, and nothing here invents a tool.
    """
    classes: dict[str, str] = {}
    try:
        import sys

        core_registry = sys.modules.get("skaldir.tools_core.registry")
        if core_registry is None:
            from .tools_core import registry as core_registry
    except Exception:  # noqa: BLE001 — a build without the core toolset
        return classes
    try:
        contracts = core_registry.contracts()
    except Exception:  # noqa: BLE001
        return classes
    for name, contract in dict(contracts).items():
        try:
            subprocess = contract.permission.subprocess
            if bool(subprocess.shell) or bool(subprocess.allowed):
                classes[str(name)] = _CONTRACT_EXEC
            elif bool(contract.mutates):
                classes[str(name)] = _CONTRACT_WRITE
            else:
                classes[str(name)] = _CONTRACT_READ
        except Exception:  # noqa: BLE001 — a contract shape we cannot read is skipped
            continue
    return classes


def tool_classes() -> dict[str, str]:
    """Every known tool name and its class (contracts first, loop names completed)."""
    classes = dict(contract_tool_classes())
    for name, kind in _LOOP_TOOL_CLASSES.items():
        classes.setdefault(name, kind)
    return classes


_CLASSES: dict[str, str] = tool_classes()

try:
    from .toolcall import WRITE_TOOLS as _TOOLCALL_WRITE_TOOLS
    _TOOLCALL_WRITE_NAMES: frozenset[str] = frozenset(_TOOLCALL_WRITE_TOOLS)
except Exception:  # noqa: BLE001 — a build without the policy module
    _TOOLCALL_WRITE_NAMES: frozenset[str] = frozenset()  # type: ignore[no-redef]

#: Tools that change files in the workspace. Audit 46 #10: one vocabulary, one copy
#: — this set is :data:`skaldir.toolcall.WRITE_TOOLS`'s names, classed against this
#: module's own contracts (never the policy's terminal/exec spellings), plus the
#: short spellings. Names are **added**, never dropped.
WRITE_TOOLS: frozenset[str] = frozenset(
    {"edit", "write"}
    | {name for name in _TOOLCALL_WRITE_NAMES if _CLASSES.get(name) == _CONTRACT_WRITE}
    | {name for name, kind in _CLASSES.items() if kind == "write"}
)
#: Arbitrary command execution — anything the toolset lets start a process.
EXEC_TOOLS: frozenset[str] = frozenset(
    {"exec"} | {name for name, kind in _CLASSES.items() if kind == "exec"}
)
#: Running the project's own declared test/check command — narrower than ``exec``
#: on purpose: it takes no free-form command line.
TEST_TOOLS: frozenset[str] = frozenset(
    {"test_run"} | {name for name, kind in _CLASSES.items() if kind == "test"}
)
#: Read / research tools — no side effect on the workspace, no process started.
READ_TOOLS: frozenset[str] = frozenset(
    {"read", "glob", "grep", "git_read", "web_search", "web_fetch"}
    | {
        name
        for name, kind in _CLASSES.items()
        if kind == "read" and name not in WRITE_TOOLS | EXEC_TOOLS | TEST_TOOLS
    }
)

#: The stable refusal codes. A caller branches on these; nothing parses prose.
REFUSAL_TOOL_NOT_GRANTED = "role_ceiling.tool_not_granted"
REFUSAL_DELEGATION_FORBIDDEN = "role_ceiling.delegation_forbidden"
REFUSAL_UNKNOWN_ROLE = "role_ceiling.unknown_role"
REFUSAL_BUDGET_EXHAUSTED = "role_ceiling.budget_exhausted"


class ToolCeilingRefused(RuntimeError):
    """A tool call a role may not make — named, never executed.

    Deliberately **not** a subclass of the harness' ``SubagentError``: this
    module must stay importable by the policy tests without dragging the
    subagent harness (and its provider edge) in. The harness catches this type
    and turns it into its own event/note (see ``subagents.py``).
    """

    def __init__(self, refusal: ToolRefusal) -> None:
        super().__init__(
            f"[{refusal.code}] role {refusal.role!r} may not call tool "
            f"{refusal.tool!r}: {refusal.reason}"
        )
        self.refusal = refusal
        self.code = refusal.code
        self.role = refusal.role
        self.tool = refusal.tool
        self.reason = refusal.reason

    def as_dict(self) -> dict:
        return self.refusal.as_dict()


class RoleBudgetExhausted(ToolCeilingRefused):
    """The role's own tool-call budget is spent — a ceiling too."""


@dataclass(frozen=True)
class ToolRefusal:
    """One refused call, in the shape a run record can carry."""

    role: str
    tool: str
    code: str
    reason: str
    at_ms: float = 0.0

    def as_dict(self) -> dict:
        return {
            "role": self.role,
            "tool": self.tool,
            "code": self.code,
            "reason": self.reason,
            "at_ms": self.at_ms,
        }


@dataclass(frozen=True)
class RoleBudget:
    """A role's own cost envelope: turns, tokens and tool calls.

    ``max_tool_calls`` is what makes a dispatched-call ceiling possible at all;
    turns and tokens are the same two numbers the harness already enforces per
    child, restated per role so a role cannot be granted more than its envelope.
    """

    max_turns: int
    max_tokens: int
    max_tool_calls: int

    def __post_init__(self) -> None:
        for name in ("max_turns", "max_tokens", "max_tool_calls"):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"role budget {name} must be >= 1")

    def as_dict(self) -> dict:
        return {
            "max_turns": int(self.max_turns),
            "max_tokens": int(self.max_tokens),
            "max_tool_calls": int(self.max_tool_calls),
        }

    def narrowed_by(self, other: RoleBudget) -> RoleBudget:
        """The tighter of two envelopes — a caller may narrow, never widen."""
        return RoleBudget(
            max_turns=min(int(self.max_turns), int(other.max_turns)),
            max_tokens=min(int(self.max_tokens), int(other.max_tokens)),
            max_tool_calls=min(int(self.max_tool_calls), int(other.max_tool_calls)),
        )


#: One envelope per role. A ``verifier`` is the *cheapest* — it reads, runs the
#: declared check, and answers; it must never be a second full worker.
ROLE_BUDGETS: dict[str, RoleBudget] = {
    ROLE_EXPLORE: RoleBudget(max_turns=6, max_tokens=20_000, max_tool_calls=30),
    ROLE_WORKER: RoleBudget(max_turns=12, max_tokens=60_000, max_tool_calls=80),
    ROLE_VERIFIER: RoleBudget(max_turns=4, max_tokens=16_000, max_tool_calls=12),
    ROLE_MAIN: RoleBudget(max_turns=24, max_tokens=200_000, max_tool_calls=400),
}

_READ_OR_RESEARCH = READ_TOOLS


@dataclass(frozen=True)
class SubagentRole:
    """A role is its rights and its budget — no prose can widen either."""

    name: str
    when_to_use: str
    tools: frozenset[str]
    may_spawn: bool
    budget: RoleBudget
    #: ``read_only`` is derived, not declared: it is true iff the role holds no
    #: tool that can change the workspace or run a free-form command.
    read_only: bool = True

    def allows(self, tool: str) -> bool:
        return str(tool) in self.tools

    def as_dict(self) -> dict:
        return {
            "role": self.name,
            "when_to_use": self.when_to_use,
            "tools": sorted(self.tools),
            "may_spawn": bool(self.may_spawn),
            "may_write": bool(self.tools & WRITE_TOOLS),
            "may_exec": bool(self.tools & EXEC_TOOLS),
            "read_only": bool(self.read_only),
            "budget": self.budget.as_dict(),
        }


ROLE_POLICIES: dict[str, SubagentRole] = {
    ROLE_EXPLORE: SubagentRole(
        name=ROLE_EXPLORE,
        when_to_use=(
            "Read-only mapping of an unfamiliar or cross-file question; it may search, "
            "read and run read-only git, and it cannot create or change a file."
        ),
        tools=frozenset(_READ_OR_RESEARCH),
        may_spawn=False,
        budget=ROLE_BUDGETS[ROLE_EXPLORE],
        read_only=True,
    ),
    ROLE_WORKER: SubagentRole(
        name=ROLE_WORKER,
        when_to_use=(
            "Bounded production work with explicit scope, ownership, deliverable and "
            "acceptance criteria. It may edit and run tests, and it may not delegate "
            "further."
        ),
        tools=frozenset(_READ_OR_RESEARCH | WRITE_TOOLS | EXEC_TOOLS | TEST_TOOLS),
        may_spawn=False,
        budget=ROLE_BUDGETS[ROLE_WORKER],
        read_only=False,
    ),
    ROLE_VERIFIER: SubagentRole(
        name=ROLE_VERIFIER,
        when_to_use=(
            "Independently check an existing deliverable and report what it found; no "
            "project-file changes — read-only plus the project's own declared check."
        ),
        tools=frozenset(_READ_OR_RESEARCH | TEST_TOOLS),
        may_spawn=False,
        budget=ROLE_BUDGETS[ROLE_VERIFIER],
        read_only=True,
    ),
    ROLE_MAIN: SubagentRole(
        name=ROLE_MAIN,
        when_to_use=(
            "The root's own surface: no ceiling. A child is never given this role — "
            "it exists so 'who may do everything' is one table row."
        ),
        tools=frozenset(
            _READ_OR_RESEARCH | WRITE_TOOLS | EXEC_TOOLS | TEST_TOOLS | DELEGATION_TOOLS
        ),
        may_spawn=True,
        budget=ROLE_BUDGETS[ROLE_MAIN],
        read_only=False,
    ),
}

# The role-table invariant runs after every role is registered (see below): the named
# roles are added to ROLE_POLICIES/ROLE_BUDGETS in one block further down.

#: The union of every tool this module knows about. A tool outside it is not
#: *forbidden for a role*, it is **unknown**, and an unknown tool is refused by
#: every role — including ``main`` — because a role ceiling that lets through
#: names it has never seen is not a ceiling.
KNOWN_TOOLS: frozenset[str] = frozenset().union(
    *(policy.tools for policy in ROLE_POLICIES.values())
)

# --- drift guards: the table above is derived, so it must stay consistent ------ #

_CLASS_SETS: dict[str, frozenset[str]] = {
    "read": READ_TOOLS,
    "write": WRITE_TOOLS,
    "exec": EXEC_TOOLS,
    "test": TEST_TOOLS,
}
_overlap = {
    name
    for name in KNOWN_TOOLS
    if sum(1 for members in _CLASS_SETS.values() if name in members) > 1
}
assert not _overlap, f"a tool name is in two classes: {sorted(_overlap)}"
_unclassified = sorted(name for name in _CLASSES if name not in KNOWN_TOOLS)
assert not _unclassified, f"a known tool has no role class: {_unclassified}"
_missing = sorted(set(contract_tool_classes()) - KNOWN_TOOLS)
assert not _missing, (
    "the product's own core tools are not in the role vocabulary — a role ceiling "
    f"over a vocabulary the runtime does not speak: {_missing}"
)


# --- the child's default role -------------------------------------------------- #

#: The role a child gets when the caller did not name one. The ceiling used to be
#: **opt-in**: ``role=""`` (unlimited) was the default, so every child that nobody
#: thought about was ceiling-free — the control existed and was off. A child is now
#: bounded unless the caller *asks* for the historical ceiling-free child by name.
DEFAULT_CHILD_ROLE: str = ROLE_WORKER
#: The explicit sentinel for the historical, ceiling-free child. It is a word, not an
#: empty string, so a caller cannot land there by forgetting a parameter.
UNROLED: str = "unroled"


def resolve_role(value: str | None, *, kind: str = "", default: str = DEFAULT_CHILD_ROLE) -> str:
    """The role a child really gets: named → normalised, ``unroled`` → ``""``, else default.

    ``None``/``""`` mean "the caller did not say" and resolve to the canonical role for
    ``kind`` (``explore`` for research, ``verifier`` for a check, ``worker`` otherwise).
    ``UNROLED`` ("unroled") is the one way to ask for the ceiling-free child, and it is
    spelled out at the call site instead of happening by omission.
    """
    text = str(value or "").strip().lower()
    if text == UNROLED:
        return ""
    if text == "":
        return validate_role(role_for_kind(kind, default=default))
    return validate_role(text)


def verifier_dispatcher(
    *,
    handlers: Mapping[str, Callable[..., Any]] | None = None,
    budget: RoleBudget | None = None,
) -> RoleToolDispatcher:
    """A **read-only verifier** dispatcher — the one the semantic gate accepts.

    ``verify_service.semantic_gate`` refuses any dispatcher that is not this role, so the
    production path needs a real one instead of a test fixture. It is read-only by
    construction (the role's tool set has no write/exec entry) and bounded by the
    verifier's own envelope.
    """
    return RoleToolDispatcher(ROLE_VERIFIER, handlers=handlers, budget=budget)


def role_names() -> tuple[str, ...]:
    return ROLES


def is_role(value: str) -> bool:
    return str(value) in ROLE_POLICIES


def canonical_role(value: str) -> str | None:
    """Normalise a requested role name to a canonical child role, or ``None``."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in CANONICAL_ROLES else None


def validate_role(value: str, *, allow_unroled: bool = False) -> str:
    """Normalise a role name; raise on anything that is not a known role.

    ``allow_unroled`` admits ``""`` — the harness' historical, ceiling-free child.
    That case exists for one reason and is documented as an honest limit in
    ``MVP/93-subagent-roles.md``: a brief written before roles existed must not
    silently acquire a role it was never reviewed against.
    """
    normalized = str(value or "").strip().lower()
    if normalized == "" and allow_unroled:
        return ""
    if normalized not in ROLE_POLICIES:
        raise ValueError(
            f"unknown subagent role {value!r} (known: {', '.join(ROLES)})"
        )
    return normalized


def policy_for(value: str, *, allow_unroled: bool = False) -> SubagentRole | None:
    """The policy of a role; ``None`` for the unroled child."""
    normalized = validate_role(value, allow_unroled=allow_unroled)
    if normalized == "":
        return None
    return ROLE_POLICIES[normalized]


def tools_for(value: str, *, allow_unroled: bool = True) -> tuple[str, ...]:
    """The tool names a role is **offered**. Unroled gets the whole vocabulary."""
    policy = policy_for(value, allow_unroled=allow_unroled)
    if policy is None:
        return tuple(sorted(KNOWN_TOOLS))
    return tuple(sorted(policy.tools))


def provision_tools(names: Iterable[str], role: str, *, allow_unroled: bool = True) -> list[str]:
    """Filter an already-built tool list down to a role's ceiling (pure).

    This is the pure helper the runtime's tool-list owners call — the same
    reason MiniMax keeps its equivalent pure: two catalog owners must not be
    able to drift on ``explore``/``worker``/``verifier`` semantics. An
    ``explore`` child's list comes back with no edit/write/exec entry at all,
    so the ceiling is a provisioning fact before it is a dispatch refusal.
    """
    policy = policy_for(role, allow_unroled=allow_unroled)
    kept = [str(name) for name in names if str(name) in KNOWN_TOOLS]
    if policy is None:
        return kept
    return [name for name in kept if policy.allows(name)]


def role_for_kind(kind: str, *, default: str = ROLE_WORKER) -> str:
    """Map a harness worker kind (``code``/``research``…) onto a canonical role."""
    normalized = str(kind or "").strip().lower()
    if normalized in {"research", "explore", "read", "recon", "reviewer_diff"}:
        return ROLE_EXPLORE
    if normalized in {"verify", "verifier", "check"}:
        return ROLE_VERIFIER
    if normalized in {"code", "build", "test", "worker"}:
        return ROLE_WORKER
    return validate_role(default)


# --------------------------------------------------------------------------- #
# the dispatcher — where the ceiling is enforced                                  #
# --------------------------------------------------------------------------- #


def _digest(value: Any) -> str:
    try:
        payload = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        payload = str(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class ToolCallRecord:
    """One dispatched call: what ran, or why it did not."""

    tool: str
    role: str
    allowed: bool
    code: str = ""
    reason: str = ""
    args_digest: str = ""
    result: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "tool": self.tool,
            "role": self.role,
            "allowed": bool(self.allowed),
            "code": self.code,
            "reason": self.reason,
            "args_digest": self.args_digest,
            "result": self.result,
        }


#: The owner's named research/production roles (audit N1): one right bundle + budget
#: each, and — through settings ``subagent.roles.<role>.{provider,model,api_key_env}``
#: — its own provider and API key. The tables are *appended to*, never rewritten, so the
#: legacy ``explore``/``worker``/``verifier`` enrolment keeps working.
ROLE_BUDGETS.update(
    {
        ROLE_RESEARCHER: RoleBudget(max_turns=6, max_tokens=20_000, max_tool_calls=30),
        ROLE_REPO_SCOUT: RoleBudget(max_turns=6, max_tokens=20_000, max_tool_calls=30),
        ROLE_DEBUGGER: RoleBudget(max_turns=10, max_tokens=40_000, max_tool_calls=50),
        ROLE_EVIDENCE_COLLECTOR: RoleBudget(
            max_turns=8, max_tokens=30_000, max_tool_calls=40
        ),
        ROLE_IMPLEMENTER: RoleBudget(
            max_turns=12, max_tokens=60_000, max_tool_calls=80
        ),
        ROLE_PLANNER: RoleBudget(max_turns=6, max_tokens=20_000, max_tool_calls=30),
    }
)
ROLE_POLICIES.update(
    {
        ROLE_RESEARCHER: SubagentRole(
            name=ROLE_RESEARCHER,
            when_to_use=(
                "Research a topic, feature or question broadly — read code and the web, "
                "produce a cited answer, and change nothing."
            ),
            tools=frozenset(READ_TOOLS),
            may_spawn=False,
            budget=ROLE_BUDGETS[ROLE_RESEARCHER],
            read_only=True,
        ),
        ROLE_REPO_SCOUT: SubagentRole(
            name=ROLE_REPO_SCOUT,
            when_to_use=(
                "Find the relevant files, dependencies and existing patterns for a task — "
                "repo traversal and git history, not the open web."
            ),
            tools=frozenset(READ_TOOLS - {"web_search", "web_fetch"}),
            may_spawn=False,
            budget=ROLE_BUDGETS[ROLE_REPO_SCOUT],
            read_only=True,
        ),
        ROLE_DEBUGGER: SubagentRole(
            name=ROLE_DEBUGGER,
            when_to_use=(
                "Reproduce a failure and find its root cause — read the code, run a "
                "reproduction and the relevant tests, name the defect precisely."
            ),
            tools=frozenset(READ_TOOLS | EXEC_TOOLS | TEST_TOOLS),
            may_spawn=False,
            budget=ROLE_BUDGETS[ROLE_DEBUGGER],
            read_only=False,
        ),
        ROLE_EVIDENCE_COLLECTOR: SubagentRole(
            name=ROLE_EVIDENCE_COLLECTOR,
            when_to_use=(
                "Collect logs, screenshots, test output and artifacts into a verifiable "
                "evidence set — capture and read, no product changes."
            ),
            tools=frozenset(READ_TOOLS | EXEC_TOOLS | TEST_TOOLS),
            may_spawn=False,
            budget=ROLE_BUDGETS[ROLE_EVIDENCE_COLLECTOR],
            read_only=False,
        ),
        ROLE_IMPLEMENTER: SubagentRole(
            name=ROLE_IMPLEMENTER,
            when_to_use=(
                "Bounded production work with explicit scope, ownership and acceptance "
                "criteria — may edit, run tests and delegate to further children."
            ),
            tools=frozenset(READ_TOOLS | WRITE_TOOLS | EXEC_TOOLS | TEST_TOOLS),
            may_spawn=True,
            budget=ROLE_BUDGETS[ROLE_IMPLEMENTER],
            read_only=False,
        ),
        ROLE_PLANNER: SubagentRole(
            name=ROLE_PLANNER,
            when_to_use=(
                "Decompose a goal into ordered steps and acceptance criteria — read and "
                "reason, write nothing."
            ),
            tools=frozenset(READ_TOOLS),
            may_spawn=False,
            budget=ROLE_BUDGETS[ROLE_PLANNER],
            read_only=True,
        ),
    }
)
# The role-table invariant: every role name has a policy AND a budget, and the vocabulary
# (ROLES) is exactly the table. Runs here so it sees the named roles too.
assert set(ROLE_POLICIES) == set(ROLE_BUDGETS) == set(ROLES), (
    "the role table and the role vocabulary drifted"
)


class RoleToolDispatcher:
    """The tool dispatcher for one role-bearing agent.

    Every call goes through :meth:`dispatch`. Two things can happen:

    * the role holds the tool → the handler (if any) runs and a result is
      returned;
    * the role does not hold it → :class:`ToolCeilingRefused` is raised with a
      **named code**, and the call is recorded as refused. The handler is never
      invoked: the refusal is not advisory.

    The dispatcher also owns the role's tool-call budget: past
    ``budget.max_tool_calls`` the next call is refused with
    :data:`REFUSAL_BUDGET_EXHAUSTED` (a ceiling, so the same exception type).
    """

    def __init__(
        self,
        role: str,
        *,
        handlers: Mapping[str, Callable[..., Any]] | None = None,
        budget: RoleBudget | None = None,
        allow_unroled: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.role = validate_role(role, allow_unroled=allow_unroled)
        self.policy = ROLE_POLICIES.get(self.role)
        self.handlers: dict[str, Callable[..., Any]] = dict(handlers or {})
        self.budget = budget or (
            self.policy.budget if self.policy is not None else None
        )
        self._clock = clock
        self.calls: list[ToolCallRecord] = []
        self._started = float(clock())

    # -- introspection -------------------------------------------------------

    @property
    def offered(self) -> tuple[str, ...]:
        """The tools this dispatcher will actually run (the provisioning list)."""
        return tools_for(self.role)

    def may_call(self, tool: str) -> bool:
        return self.policy is None or self.policy.allows(str(tool))

    def refusals(self) -> list[dict]:
        return [record.as_dict() for record in self.calls if not record.allowed]

    def executed(self) -> list[dict]:
        return [record.as_dict() for record in self.calls if record.allowed]

    def _refuse(self, tool: str, code: str, reason: str) -> ToolCeilingRefused:
        refusal = ToolRefusal(
            role=self.role,
            tool=str(tool),
            code=code,
            reason=reason,
            at_ms=(float(self._clock()) - self._started) * 1000.0,
        )
        record = ToolCallRecord(
            tool=refusal.tool,
            role=self.role,
            allowed=False,
            code=code,
            reason=reason,
        )
        self.calls.append(record)
        if code == REFUSAL_BUDGET_EXHAUSTED:
            return RoleBudgetExhausted(refusal)
        return ToolCeilingRefused(refusal)

    # -- the one entry point -------------------------------------------------

    def dispatch(self, tool: str, /, **args: Any) -> dict:
        """Run ``tool`` for this role, or refuse it by name. Never both."""
        name = str(tool or "").strip()
        if name not in KNOWN_TOOLS:
            raise self._refuse(
                name,
                REFUSAL_TOOL_NOT_GRANTED,
                "unknown tool name — no role grants a tool outside the known vocabulary",
            )
        if self.policy is None:
            # unroled: the historical behaviour, still bounded by the budget below
            allowed = True
        else:
            allowed = self.policy.allows(name)
        if not allowed:
            code = (
                REFUSAL_DELEGATION_FORBIDDEN
                if name in DELEGATION_TOOLS
                else REFUSAL_TOOL_NOT_GRANTED
            )
            raise self._refuse(
                name,
                code,
                (
                    f"role {self.role!r} may not delegate; spawning is reserved for the root"
                    if code == REFUSAL_DELEGATION_FORBIDDEN
                    else f"role {self.role!r} does not hold this tool"
                ),
            )
        if self.budget is not None and len(self.executed()) >= int(self.budget.max_tool_calls):
            raise self._refuse(
                name,
                REFUSAL_BUDGET_EXHAUSTED,
                f"role tool-call budget of {int(self.budget.max_tool_calls)} is spent",
            )

        handler = self.handlers.get(name)
        args_digest = _digest(args)
        if handler is None:
            result: dict = {"ok": True, "tool": name, "mode": "declared", "executed": False}
        else:
            try:
                produced = handler(**args)
            except Exception as exc:  # noqa: BLE001 — a handler failure is a result, not a refusal
                result = {
                    "ok": False,
                    "tool": name,
                    "mode": "handler",
                    "executed": True,
                    "error": f"{type(exc).__name__}: {exc}"[:400],
                }
            else:
                result = (
                    produced
                    if isinstance(produced, dict)
                    else {"ok": True, "tool": name, "mode": "handler", "value": produced}
                )
                result.setdefault("executed", True)
                result.setdefault("mode", "handler")
        self.calls.append(
            ToolCallRecord(
                tool=name,
                role=self.role,
                allowed=True,
                args_digest=args_digest,
                result=result,
            )
        )
        return {"tool": name, "role": self.role, "ok": bool(result.get("ok", True)), **result}

    def try_dispatch(self, tool: str, /, **args: Any) -> tuple[dict | None, dict | None]:
        """``(result, refusal)`` — exactly one of the two is ``None``. No exception."""
        try:
            return self.dispatch(tool, **args), None
        except ToolCeilingRefused as exc:
            return None, exc.as_dict()

    def report(self) -> dict:
        """What this dispatcher did — the record a run can carry."""
        refusals = self.refusals()
        return {
            "role": self.role,
            "offered_tools": list(self.offered),
            "budget": self.budget.as_dict() if self.budget is not None else None,
            "calls": len(self.calls),
            "executed": len(self.calls) - len(refusals),
            "refused": len(refusals),
            "refusals": refusals,
            "call_log": [record.as_dict() for record in self.calls],
        }


# --------------------------------------------------------------------------- #
# the self-audit surface                                                         #
# --------------------------------------------------------------------------- #


def ceiling_report() -> dict:
    """Every role, its tool list, its spawn right and its budget — one payload.

    This is the surface a ``selftest``/CLI audit prints; it is computed from the
    policy table, so it cannot disagree with what the dispatcher enforces.
    """
    roles: list[dict] = []
    for name in ROLES:
        policy = ROLE_POLICIES[name]
        payload = policy.as_dict()
        forbidden: list[str] = []
        for tool in sorted(KNOWN_TOOLS):
            if not policy.allows(tool):
                forbidden.append(tool)
        payload["forbidden_tools"] = forbidden
        roles.append(payload)
    return {
        "schema": "skaldir.subagent_roles.v1",
        "roles": roles,
        "known_tools": sorted(KNOWN_TOOLS),
        "refusal_codes": [
            REFUSAL_TOOL_NOT_GRANTED,
            REFUSAL_DELEGATION_FORBIDDEN,
            REFUSAL_UNKNOWN_ROLE,
            REFUSAL_BUDGET_EXHAUSTED,
        ],
        "enforcement": (
            "provision_tools() removes what a role may not be offered; "
            "RoleToolDispatcher.dispatch() refuses by name and never executes"
        ),
    }


def dispatch_for_role(
    role: str,
    *,
    handlers: Mapping[str, Callable[..., Any]] | None = None,
    budget: RoleBudget | None = None,
) -> RoleToolDispatcher:
    """Build a dispatcher for a role (the constructor's one-line alias)."""
    return RoleToolDispatcher(role, handlers=handlers, budget=budget)


#: Sequence-typed helper mirroring MiniMax' ``filterCanonicalNativeToolCeiling``
#: shape, for callers that hold tool *definitions* rather than names.
def ceiling_filter(
    tools: Sequence[Any], role: str, *, name_of: Callable[[Any], str] | None = None
) -> list[Any]:
    """Keep only the definitions a role may be offered (order preserved)."""
    getter = name_of or (lambda item: getattr(item, "name", str(item)))
    policy = policy_for(role)
    if policy is None:
        return list(tools)
    return [item for item in tools if str(getter(item)) in policy.tools]
