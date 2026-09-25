"""The fast subagent harness: N child agents in parallel, one process.

Why this exists
---------------
The thing it replaces is *a CLI process per child*: every child paid the
interpreter start, the whole import graph and a fresh provider client before it
did any work. The harness runs every child as a **thread inside one process**:

* **one provider client per run**, built once and shared by every child — the
  resolved configuration, the HTTP connection pool and the retry/deadline policy
  are per *run*, not per child;
* **nothing is re-imported per child** — the child loop is a module-level
  function and the import graph is paid once, at start-up;
* **results stream as they arrive**: every state change appends a line to
  ``events.jsonl`` and rewrites the run snapshot, so a second process can watch
  a live run (``agent show``, ``agent collect --wait``) instead of blocking on
  the slowest child.

The measured comparison against a process per child (spawn latency p50/p95,
per-child overhead, wall clock) is in ``MVP/28-subagent-harness.md``.

Isolation — what "an isolated context" means here
-------------------------------------------------
Each child owns:

* its **own prompt id** (``subagent.<run>.<child>.t<turn>``) and its **own
  session id** (``<run>:<child>``);
* its **own context window** — it only ever sees its own transcript, truncated
  to ``context_chars``; a sibling's transcript never enters another child's
  prompt;
* its **own token budget** (``budget_tokens``) and its **own turn budget**
  (``max_turns``);
* its **own working directory** and its **own transcript file** under the run
  directory;
* its **own deadline** (``timeout_s``).

The parent's conversation is *never* handed to a child: the run record is the
parent's, the child only receives its own brief plus its own turns.
``tests/test_subagents.py::test_a_child_cannot_see_the_parent_conversation_or_a_sibling``
holds the proof (a parent-side sentinel string appears in no prompt and in no
file the child can read, and a sibling's answer never reaches another child).

Nesting: can a child spawn children?
------------------------------------
**Decided: yes, but only below an explicit depth cap.** The root run is depth
``0``; its children are depth ``1``. A child may spawn grandchildren *only* when
its own depth is below ``max_depth`` (``DEFAULT_MAX_DEPTH = 1`` ⇒ **children of
the root may not nest**, which is the default the product ships), and the check
lives in one place — :meth:`SubagentRun.nested_spawner` raises
:class:`DepthCapReached` before anything is created. The cap is propagated into
every nested run (a grandchild inherits the parent's ``max_depth``), so a
configured depth can never be exceeded by a deeper chain, and the hard ceiling
``MAX_DEPTH_LIMIT`` bounds what a caller may configure at all. A refusal is an
**event plus a note on that child's result**, never a silent no-op: the child
keeps working, the run records ``child_nesting_refused`` with the depth it was
at. A nested run is addressable evidence — it lives under
``children/<child>/nested/<grandchild-run-id>/`` and carries
``parent: {run_id, child_id}`` plus its ``depth``.

Roles: what a child may do at all
---------------------------------
A brief may carry a **role** (``explore`` · ``worker`` · ``verifier`` · ``main``),
and a role is a hard ceiling on tools, on delegation and on cost
(:mod:`skaldir.subagent_roles`, designed in ``MVP/93-subagent-roles.md``):

* ``explore`` is offered read/research tools only — it has no ``edit``/``write``/
  ``exec`` **in its tool list at all** (:func:`ChildBrief.tools`), and a call that
  arrives anyway is refused at the dispatcher;
* ``worker`` may edit and run tests but may **not** delegate: its declared
  ``[[spawn:…]]`` is refused with ``role_ceiling.delegation_forbidden``, and the
  refusal is recorded (event + run record + a note) while the child keeps working;
* ``verifier`` is read-only plus the project's own declared check — and its
  budget is the tightest of the three, because a verifier that costs like a
  second worker is not a gate, it is a second try.

The ceiling is enforced in the **dispatcher**, never by asking the model to
behave: :meth:`SubagentRun.dispatch_tool` routes every call through
:class:`~skaldir.subagent_roles.RoleToolDispatcher`, whose refusals carry a
stable machine code. :meth:`SubagentRun.role_report` is the run-level view of
what each child held and what was refused. ``role`` defaults to ``""`` — the
historical, ceiling-free child — so a brief written before roles existed keeps
its meaning; ``MVP/93`` §5 states that as an honest limit rather than a design
goal.

Honest limits — what a child cannot do
--------------------------------------
* **A child cannot be killed.** Threads are not interruptible: a cancel, a
  timeout or a budget stop lands at the child's **next turn boundary**. A child
  blocked inside a provider call keeps that call until the provider's own
  timeout ends (the thread is a daemon, so it never keeps the CLI alive).
  ``stop_grace_s`` bounds how long the *collector* waits after a cancel before
  it settles the outstanding children itself and reports ``thread_alive: true``.
* **No process-level cwd.** ``os.chdir`` is process-global and unsafe with
  threads, so a child never chdirs — it *receives* its working directory. A
  child that must run a command with a real cwd has to spawn it with ``cwd=``.
* **No shared memory between children by design.** The only channels are the
  run directory (files) and the parent's collector.
* **No native tool protocol.** The wire-level ``tool_calls`` protocol belongs to
  the agent loop (``agent_loop``); a child speaks text to ``complete()``. What a
  child *can* ask for is therefore **declared in its answer**
  (``[[tool:NAME]] {json}``, parsed by :func:`child_tool_calls` and executed
  through its own role dispatcher — see below). A child cannot invent a
  capability: a name outside its role's vocabulary is refused with a named code
  and a granted tool with no registered handler is reported ``declared``, never
  run. Nesting is likewise declared in the child's brief (``[[spawn:TASK]]``)
  and executed through the depth-checked spawner.
* **A child's tools are whatever its run registered.** The harness dispatches; it
  does not implement. With an empty handler map (the default, and what
  ``skaldir agent spawn`` uses) every granted tool is ``declared`` and a child's
  directives do nothing but report that fact. ``skaldir run``'s delegation seam
  registers the real ones (``runtime_delegation``), and only for the tools the
  granted role holds.
* **Budgets are guards, not accounting** — tokens are estimated as
  ``chars / 4`` (:func:`skaldir.llm.estimate_tokens`), the estimate the rest of
  the runtime already uses. A nested run's tokens are reported separately in
  ``cost.nested_tokens`` and do **not** silently consume the child's own
  budget.
* **The harness reports outcomes, it does not certify them.** A ``delivered``
  child means its answer carried the final marker inside the budget; whether the
  work is *right* is the VERIFY layer's question, not this one's.
* **No pricing is bundled.** ``cost`` reports tokens, turns, provider calls and
  milliseconds; a money figure would need a price table this project does not
  ship, so it is deliberately absent.
"""

from __future__ import annotations

import json
import math
import os
import re
import signal
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .llm import estimate_tokens
from .subagent_roles import (
    UNROLED,
    RoleBudget,
    RoleToolDispatcher,
    ToolCeilingRefused,
    policy_for,
    resolve_role,
    tools_for,
)

__all__ = [
    "CANCEL_FILE",
    "CHILD_TOOL_MARKER",
    "DEFAULT_MAX_DEPTH",
    "EVENTS_FILE",
    "FAILURE_CLASSES",
    "FINAL_MARKER",
    "MAX_DEPTH_LIMIT",
    "RUN_FILE",
    "STATUS_BUDGET_EXHAUSTED",
    "STATUS_CANCELLED",
    "STATUS_DELIVERED",
    "STATUS_FAILED",
    "STATUS_MAX_TURNS",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "STATUS_TIMED_OUT",
    "ChildBrief",
    "ChildContext",
    "ChildResult",
    "CancelOutcome",
    "DepthCapReached",
    "ProviderBlocked",
    "ProviderHandle",
    "RoleToolDispatcher",
    "RunRecord",
    "RunStore",
    "StubProvider",
    "SubagentError",
    "SubagentRun",
    "ToolCeilingRefused",
    "briefs_from_tasks",
    "child_tool_calls",
    "failure_class_of",
    "nesting_tasks",
    "open_provider",
    "percentile",
    "retry_briefs",
    "spawn",
]

SCHEMA_VERSION = "skaldir.subagents.v1"

#: The marker that ends a child's work: its answer carries ``FINAL: <text>``.
FINAL_MARKER = "FINAL:"

DEFAULT_MAX_TURNS = 3
DEFAULT_BUDGET_TOKENS = 20_000
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_CONTEXT_CHARS = 6_000
DEFAULT_STOP_GRACE_S = 5.0
TIERS = ("small", "main")

#: The depth cap. The root run is depth 0, its children are depth 1. ``1`` means
#: "a child of the root may not spawn children" — the default this product ships.
DEFAULT_MAX_DEPTH = 1
#: The hard ceiling on what a caller may configure, so no chain can be unbounded.
MAX_DEPTH_LIMIT = 4

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DELIVERED = "delivered"
STATUS_FAILED = "failed"
STATUS_BUDGET_EXHAUSTED = "budget_exhausted"
STATUS_MAX_TURNS = "max_turns_reached"
STATUS_CANCELLED = "cancelled"
STATUS_TIMED_OUT = "timed_out"

#: Every non-delivery has a machine-readable class — a caller must be able to
#: branch on *why* a child stopped, not parse a status string. ``""`` = delivered.
FAILURE_CLASSES = (
    "provider_error",
    "child_error",
    "timeout",
    "cancelled",
    "parent_cancelled",
    "run_budget",
    "budget_exhausted",
    "max_turns",
)

TERMINAL_CHILD = frozenset(
    {
        STATUS_DELIVERED,
        STATUS_FAILED,
        STATUS_BUDGET_EXHAUSTED,
        STATUS_MAX_TURNS,
        STATUS_CANCELLED,
        STATUS_TIMED_OUT,
    }
)
DELIVERED_CHILD = frozenset({STATUS_DELIVERED})

RUN_RUNNING = "running"
RUN_COMPLETE = "complete"
RUN_CANCELLED = "cancelled"
RUN_ABORTED = "aborted"

RUN_FILE = "run.json"
EVENTS_FILE = "events.jsonl"
CANCEL_FILE = "cancel.json"

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class SubagentError(RuntimeError):
    """A contract violation of the harness (bad id, bad brief)."""


class ProviderBlocked(SubagentError):
    """No usable provider: the run cannot start — say why, never fake a child."""


class DepthCapReached(SubagentError):
    """A child at the depth cap tried to spawn children — refused, never faked.

    Raised by :meth:`SubagentRun.nested_spawner` *before* any directory, thread
    or provider call exists, so a refused nesting leaves no half-built run.
    """

    def __init__(self, *, child_id: str, depth: int, max_depth: int) -> None:
        self.child_id = str(child_id)
        self.depth = int(depth)
        self.max_depth = int(max_depth)
        super().__init__(
            f"child {self.child_id} is at depth {self.depth} of max {self.max_depth}: "
            "the nesting cap is reached, so it may not spawn children "
            "(raise --max-depth to allow one more level)"
        )


def _safe_id(value: str, *, what: str = "run id") -> str:
    """A run/child id that can be a directory name — or an error, never a traversal."""
    text = str(value or "").strip()
    if not _ID_PATTERN.match(text):
        raise SubagentError(
            f"invalid {what} {value!r}: use 1-64 characters of letters, digits, '.', '_' or '-'"
        )
    return text


def _iso(ts: float | None = None) -> str:
    moment = datetime.fromtimestamp(time.time() if ts is None else float(ts), UTC)
    return moment.isoformat(timespec="milliseconds")


def _round_ms(seconds: float) -> float:
    return round(float(seconds) * 1000.0, 3)


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile (``q`` in 0..1); ``0.0`` for an empty sample."""
    data = sorted(float(item) for item in values)
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]
    position = max(0.0, min(1.0, float(q))) * (len(data) - 1)
    lower = int(math.floor(position))
    upper = min(lower + 1, len(data) - 1)
    weight = position - lower
    return data[lower] * (1.0 - weight) + data[upper] * weight


def _write_json_atomic(path: Path, payload: Any) -> None:
    """Write one JSON document so a reader never sees half of it.

    The temporary name carries a per-call suffix: two children writing their
    state at the same moment must not fight over one ``.tmp`` file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex[:8]}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _append_line(path: Path, payload: dict) -> None:
    """Append one JSONL record and flush it.

    ``flush`` (not ``fsync``) is what makes the line visible to a watching
    process immediately; a hard crash can therefore lose the last few lines of a
    live run, which is why the run's durable snapshot and every child's
    transcript are rewritten/appended on each state change instead of once.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        handle.flush()


# --------------------------------------------------------------------------- #
# the provider edge
# --------------------------------------------------------------------------- #


class Provider(Protocol):
    """What a child needs from a model: one blocking completion call."""

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str: ...


class StubProvider:
    """Deterministic offline provider — **no model is called**.

    It exists so timing and failure paths are reproducible without a network,
    and it says so: any run using it carries ``provider.mode == "stub"`` and a
    warning. Its behaviour is driven by markers in the child's task:

    ==================  ========================================================
    marker              meaning
    ==================  ========================================================
    ``[[turns:N]]``     answer with the final marker on the Nth call of a prompt
    ``[[latency:S]]``   sleep ``S`` seconds before each call (default: the
                        provider's ``latency_s``)
    ``[[fail]]``        raise on the first call (a child that fails)
    ``[[infinite]]``    never finish (a child a cancel/timeout must stop)
    ==================  ========================================================

    Every call is recorded (prompt id, tier, wall clock, latency) under a lock;
    ``calls`` is the audit trail a test reads.
    """

    name = "stub"

    def __init__(self, *, latency_s: float = 0.05, default_turns: int = 2) -> None:
        self._latency_s = max(0.0, float(latency_s))
        self._default_turns = max(1, int(default_turns))
        self._lock = threading.Lock()
        self._seen: dict[str, int] = {}
        self.calls: list[dict] = []

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        markers = _markers(prompt)
        latency = float(markers.get("latency", self._latency_s) or 0.0)
        if latency > 0.0:
            time.sleep(latency)
        # The turn counter is per *child*, not per prompt id: every turn is a new
        # prompt id, so counting prompt ids would make [[turns:N]] unreachable.
        key = _session_of(prompt, prompt_id)
        with self._lock:
            call_index = self._seen.get(key, 0) + 1
            self._seen[key] = call_index
            self.calls.append(
                {
                    "prompt_id": prompt_id,
                    "session": key,
                    "tier": tier,
                    "call_index": call_index,
                    "prompt_chars": len(prompt),
                    "at": _iso(),
                    "thread": threading.current_thread().name,
                }
            )
        if "fail" in markers:
            raise RuntimeError("stub provider: the task asked for a failing child ([[fail]])")
        if "infinite" in markers:
            return f"stub: still working (turn {call_index}), no final answer yet"
        finish_at = int(markers.get("turns", self._default_turns) or self._default_turns)
        if call_index >= finish_at:
            return f"stub: done\n{FINAL_MARKER} child reached its final answer on turn {call_index}"
        return f"stub: working (turn {call_index}/{finish_at})"


_MARKER = re.compile(r"\[\[([a-z]+)(?::([0-9]+(?:\.[0-9]+)?))?\]\]")
_SESSION_LINE = re.compile(r"^Session id: (.+)$", re.MULTILINE)
#: ``[[spawn:do this]]`` in a child's task = "spawn this grandchild, then work".
_SPAWN_MARKER = re.compile(r"\[\[spawn:(.+?)\]\]", re.DOTALL)


def nesting_tasks(task: str) -> list[str]:
    """The grandchild tasks a brief declares — empty unless it asked for nesting.

    Nesting is *declared*, never inferred: a child that was not given
    ``[[spawn:…]]`` in its brief has no nesting work, which keeps the depth cap
    the only thing that can refuse it.
    """
    return [text.strip() for text in _SPAWN_MARKER.findall(str(task or "")) if text.strip()]


#: How a child asks for a **tool** — one directive per line of its own answer::
#:
#:     [[tool:read_file]] {"path": "src/x.py"}
#:
#: The wire protocol (native ``tool_calls``) belongs to the agent loop; a delegated child
#: speaks to *its own* dispatcher in text, because that is the interface this harness has
#: (``provider.complete()`` returns text). One name, one JSON object, nothing else: the name is
#: looked up in the child's role vocabulary and the arguments are handed to the role's
#: dispatcher, which refuses a tool the role does not hold with a named code. A directive whose
#: arguments are not a JSON object is **not dispatched at all** — the parse error travels back
#: to the child instead of an invented call.
CHILD_TOOL_MARKER = "[[tool:"
_CHILD_TOOL_LINE = re.compile(
    r"^[ \t]*\[\[tool:([A-Za-z][A-Za-z0-9_.\-]*)\]\][ \t]*(.*?)[ \t]*$", re.MULTILINE
)

#: How much of a tool result reaches the child's next prompt and the run's record. A handler
#: that returns a whole file listing must not turn one turn into an unbounded record.
TOOL_RESULT_CHARS = 1200
#: How much of a refusal/error message is kept (the code is always kept in full).
TOOL_DETAIL_CHARS = 400


def _bounded_tool_result(produced: Mapping[str, Any]) -> dict[str, Any]:
    """One tool result as it may travel: whole when small, head+size when not.

    The *fact* of the result is never lost — ``ok``, ``executed`` and the mode are on the
    outcome row regardless — only the payload is bounded, and a bounded payload says so.
    """
    try:
        text = json.dumps(produced, default=str, sort_keys=True)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        text = str(produced)
    if len(text) <= TOOL_RESULT_CHARS:
        return dict(produced)
    return {
        "tool": produced.get("tool"),
        "truncated": True,
        "chars": len(text),
        "head": text[:TOOL_RESULT_CHARS],
    }


def child_tool_calls(text: str) -> list[dict[str, Any]]:
    """Every tool directive in a child's answer, in the order it appears.

    Returns ``[{"tool": name, "arguments": {...}, "error": ""}]``; a malformed directive carries
    its ``error`` and an empty argument map, so the caller can hand the mistake back to the
    child instead of guessing what it meant. The harness never validates *rights* here —
    :meth:`SubagentRun.dispatch_tool` does that, and it is the only thing that can execute.
    """
    calls: list[dict[str, Any]] = []
    for match in _CHILD_TOOL_LINE.finditer(str(text or "")):
        name = match.group(1)
        raw = (match.group(2) or "").strip()
        if not raw:
            calls.append({"tool": name, "arguments": {}, "error": ""})
            continue
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            calls.append(
                {"tool": name, "arguments": {}, "error": f"arguments are not JSON: {exc}"}
            )
            continue
        if not isinstance(parsed, dict):
            calls.append(
                {
                    "tool": name,
                    "arguments": {},
                    "error": (
                        "arguments must be one JSON object "
                        f"(got {type(parsed).__name__}: {raw[:120]})"
                    ),
                }
            )
            continue
        calls.append({"tool": name, "arguments": parsed, "error": ""})
    return calls


def _without_tool_directives(text: str) -> str:
    """The child's text with its tool directives removed (the final marker is read from this)."""
    return _CHILD_TOOL_LINE.sub("", str(text or ""))


def _markers(text: str) -> dict[str, str]:
    return {name: (value or "") for name, value in _MARKER.findall(str(text or ""))}


def _session_of(prompt: str, prompt_id: str) -> str:
    """The child a call belongs to — the prompt carries its session id."""
    match = _SESSION_LINE.search(str(prompt or ""))
    return match.group(1).strip() if match else str(prompt_id)


class ProviderHandle:
    """The one client a run uses, its mode, and how to close it."""

    def __init__(
        self, provider: Any, *, mode: str, note: str = "", closer: Callable[[], None] | None = None
    ) -> None:
        self.provider = provider
        self.mode = str(mode)
        self.note = str(note)
        self._closer = closer
        self.closed = False

    def summary(self) -> dict:
        return {"mode": self.mode, "note": self.note, "closed": self.closed}

    def close(self) -> None:
        if self._closer is not None and not self.closed:
            self._closer()
        self.closed = True

    def __enter__(self) -> ProviderHandle:
        return self

    def __exit__(self, *exc: object) -> bool:
        self.close()
        return False


def open_provider(
    mode: str = "auto", *, stub_latency_s: float = 0.05, stub_turns: int = 2
) -> ProviderHandle:
    """Resolve **one** provider client for a whole run.

    ``stub`` → :class:`StubProvider` (offline, deterministic, marked as such).
    ``auto`` → the real OpenAI-compatible client from :mod:`skaldir.llm_provider`,
    built with **one shared ``httpx.Client``** for the whole run: the connection
    pool, the TLS handshake and the resolved configuration are paid once per
    run, not once per call (``OpenAICompatibleLLM`` opens a fresh client per call
    when none is injected). An unconfigured provider raises
    :class:`ProviderBlocked` naming the variables to export — the caller exits 3,
    it never invents a child.
    """
    if mode == "stub":
        return ProviderHandle(
            StubProvider(latency_s=stub_latency_s, default_turns=stub_turns),
            mode="stub",
            note=(
                "deterministic offline stub — no model was called; markers "
                "[[turns:N]] [[latency:S]] [[fail]] [[infinite]] drive it"
            ),
        )
    if mode != "auto":
        raise SubagentError(f"unknown provider mode {mode!r}: use 'auto' or 'stub'")
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - httpx is a declared dependency
        raise ProviderBlocked(
            "the real provider needs httpx (declared dependency 'httpx>=0.27,<1') — "
            "install it, or run with --provider stub for an offline determinstic run"
        ) from exc
    from .llm_provider import (
        OpenAICompatibleLLM,
        ProviderConfigError,
        not_configured_message,
        provider_summary,
        resolve_provider_config,
    )

    try:
        cfg = resolve_provider_config(None)
    except ProviderConfigError as exc:
        raise ProviderBlocked(str(exc)) from exc
    if cfg.problems:
        raise ProviderBlocked("; ".join(cfg.problems))
    if not cfg.ready():
        raise ProviderBlocked(not_configured_message(cfg))
    client = httpx.Client(follow_redirects=False)
    provider = OpenAICompatibleLLM(cfg, client=client)
    return ProviderHandle(
        provider,
        mode="openai-compatible",
        note=provider_summary(cfg) + " (one shared HTTP client for the whole run)",
        closer=client.close,
    )


# --------------------------------------------------------------------------- #
# the subagent provider override: the settings key the spawn surface reads     #
# --------------------------------------------------------------------------- #

#: The ``settings.yaml`` section (and the flat keys inside it) that names the
#: provider — and optionally the model — *children* call through. The root never
#: inherits it: a run whose own client is provider X spawns children on provider X
#: unless this section says otherwise (MAS-0048's honest rule, never a silent
#: inherit of the root's provider).
SUBAGENT_SECTION = "subagent"
SUBAGENT_PROVIDER_KEY = "provider"
SUBAGENT_MODEL_KEY = "model"
#: The per-role map: ``subagent.roles.<role>.{provider,model,api_key_env}``. Each named
#: role may point at its own provider and hold its own key variable.
SUBAGENT_ROLES_KEY = "roles"
SUBAGENT_ROLE_API_KEY_ENV = "api_key_env"


def read_subagent_override(runtime=None, *, env=None) -> dict:
    """The ``subagent:`` settings block, ``{}`` when there is none.

    Read from ``<runtime dir>/settings.yaml``. Never raises: an unreadable or
    malformed file is simply "no override" — a *reader* must not invent a
    provider, and a broken file must not silently change the spawn surface
    either, so a malformed *subagent* block specifically is refused by
    :func:`resolve_subagent_provider`; everything else here is ``{}``.
    """
    import sys

    yaml = sys.modules.get("yaml")
    if yaml is None:
        import yaml as _yaml

        yaml = _yaml
    registry_mod = sys.modules.get("skaldir.registry")
    if registry_mod is not None:
        default_runtime_dir = registry_mod.default_runtime_dir
    else:
        from .registry import default_runtime_dir

    base = Path(runtime).expanduser() if runtime else Path(default_runtime_dir())
    path = base / "settings.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    block = data.get(SUBAGENT_SECTION)
    if not isinstance(block, dict):
        return {}
    return {str(key): value for key, value in block.items()}


#: The two rules the spawn surface clamps with, when the settings file names them.
#: Empty means "no clamp on this axis" — a run tool may not invent its own number.
SUBAGENT_MODEL_CHARS = 512


def _rule_str(value: Any, *, max_chars: int = SUBAGENT_MODEL_CHARS) -> str:
    text = str(value or "").strip()
    return text[:max_chars]


def subagent_provider_rule(runtime=None) -> str:
    """The ``settings.yaml`` ``subagent.provider`` value, ``""`` when unset.

    Clamped ON READ (one place): the model may spell a provider id shorter than
    this, but no honest surface should carry a longer string around.
    """
    block = read_subagent_override(runtime)
    if not block:
        return ""
    if SUBAGENT_PROVIDER_KEY not in block:
        return ""
    return _rule_str(block.get(SUBAGENT_PROVIDER_KEY))


def subagent_model_rule(runtime=None) -> str:
    """The ``settings.yaml`` ``subagent.model`` value, ``""`` when unset."""
    block = read_subagent_override(runtime)
    if not block:
        return ""
    if SUBAGENT_MODEL_KEY not in block:
        return ""
    return _rule_str(block.get(SUBAGENT_MODEL_KEY))


# --------------------------------------------------------------------------- #
# the child provider resolver: where a child's client really comes from        #
# --------------------------------------------------------------------------- #


def read_subagent_role_overrides(runtime=None, *, env=None) -> dict[str, dict]:
    """The ``subagent.roles.<role>`` map: ``{role: {provider, model, api_key_env}}``.

    Read-only, never raises (same rule as :func:`read_subagent_override`): a missing or
    malformed file is simply "no role override", so a broken settings file cannot silently
    move a child to a provider nobody chose.
    """
    block = read_subagent_override(runtime, env=env)
    roles = block.get(SUBAGENT_ROLES_KEY) if isinstance(block, dict) else None
    if not isinstance(roles, dict):
        return {}
    out: dict[str, dict] = {}
    for role, spec in roles.items():
        if not isinstance(spec, dict):
            continue
        out[str(role)] = {
            SUBAGENT_PROVIDER_KEY: _rule_str(spec.get(SUBAGENT_PROVIDER_KEY)),
            SUBAGENT_MODEL_KEY: _rule_str(spec.get(SUBAGENT_MODEL_KEY)),
            SUBAGENT_ROLE_API_KEY_ENV: _rule_str(
                spec.get(SUBAGENT_ROLE_API_KEY_ENV), max_chars=128
            ),
        }
    return out


def resolve_subagent_provider(
    requested: str = "",
    *,
    model: str = "",
    role: str = "",
    runtime=None,
    env: "Mapping[str, str] | None" = None,
) -> tuple[Any, dict]:
    """Build the client **children** call through, and say where it came from.

    Resolution, in order — the first hit wins and is *named* in the returned info:

    1. an explicitly requested provider id (the tool's ``provider`` argument or
       ``skaldir agent spawn --provider``) — resolved through
       :func:`skaldir.setup_flow.provider_choice` + the catalog
       (:mod:`skaldir.provider_model_data.PROVIDERS`);
    2. ``settings.yaml`` ``subagent.provider`` (optionally ``subagent.model``) —
       the settings-level default; the root's own provider is **never** the
       fallback, so a run configured to delegate per-provider stays that way;
    3. nothing configured ⇒ ``(None, {"provider": ""})``: the caller keeps its
       today's behaviour (the run's own client, or a named refusal).
    """
    resolved = ""
    role_name = str(role or "").strip()
    role_spec = (
        read_subagent_role_overrides(runtime, env=env).get(role_name, {})
        if role_name
        else {}
    )
    # the role's own key variable, if it names one — beats the provider's default below
    api_key_env_override = _rule_str(
        role_spec.get(SUBAGENT_ROLE_API_KEY_ENV), max_chars=128
    )
    if str(requested or "").strip():
        requested = str(requested).strip()
        try:
            setup_flow = _setup_flow()
            setup_flow.provider_choice(requested)
        except Exception as exc:  # noqa: BLE001 — named refusal, not a guess
            raise SubagentError(_refusal_message(str(exc), "provider")) from exc
        resolved, model = requested, str(model or "").strip()
        source = f"explicit provider request ({requested})"
    else:
        block = read_subagent_override(runtime, env=env)
        role_provider = _rule_str(role_spec.get(SUBAGENT_PROVIDER_KEY))
        resolved = role_provider or _rule_str(block.get(SUBAGENT_PROVIDER_KEY))
        model = (
            str(model or "").strip()
            or _rule_str(role_spec.get(SUBAGENT_MODEL_KEY))
            or _rule_str(block.get(SUBAGENT_MODEL_KEY))
        )
        if not resolved:
            return None, {
                "provider": "",
                "endpoint": "",
                "model": "",
                "source": "",
                "api_key_env": api_key_env_override,
            }
        try:
            setup_flow = _setup_flow()
            setup_flow.provider_choice(resolved)
        except Exception as exc:  # noqa: BLE001 — a bad settings key is a named refusal
            raise SubagentError(_refusal_message(str(exc), "provider")) from exc
        source = (
            f"settings.yaml subagent.roles.{role_name}.provider"
            if role_provider
            else "settings.yaml subagent.provider"
        )

    from .provider_model_data import PROVIDERS

    entry = PROVIDERS.get(resolved)
    if entry is None:
        raise SubagentError(_refusal_message(
            f"unknown provider {resolved!r} (known: {sorted(PROVIDERS)})", "provider"
        ))
    models = tuple(entry.get("models") or ())
    if model and models and model not in models:
        raise SubagentError(_refusal_message(
            f"unknown model {model!r} for provider {resolved!r} "
            f"(known: {list(models)})", "model"
        ))
    if not model:
        model = models[0] if models else str(entry.get("default_model") or "")
    endpoint = str(entry.get("base_url") or "")
    if not model or not endpoint:
        raise SubagentError(_refusal_message(
            f"provider {resolved!r} does not declare a usable endpoint/model", "provider"
        ))

    env_map = {str(k): str(v) for k, v in (dict(os.environ) if env is None else env).items()}
    api_key_env = api_key_env_override or str(entry.get("api_key_env") or "").strip()
    credential = str(env_map.get(api_key_env, "")).strip() if api_key_env else ""
    if not credential:
        missing = f"export {api_key_env}" if api_key_env else "a credential"
        raise SubagentError(_blocked_message(
            f"provider {resolved!r} has no credential: {missing} first", resolved
        ))

    return _build_catalog_client(
        resolved, model, endpoint, credential, source, api_key_env
    )


def _setup_flow() -> Any:
    """The setup flow's provider registry, imported lazily (import cost stays flat)."""
    from . import setup_flow

    return setup_flow


def _refusal_message(text: str, kind: str) -> str:
    """One honest refusal sentence: what was asked, what the catalog actually has."""
    from .provider_model_data import PROVIDERS

    if kind == "provider":
        return f"{text} (the provider catalog carries: {', '.join(sorted(PROVIDERS))})"
    return text


def _blocked_message(text: str, provider_id: str) -> str:
    """The blocked-provider message: what to export, not a fake child."""
    return text


def _build_catalog_client(
    provider_id: str,
    model: str,
    endpoint: str,
    credential: str,
    source: str,
    api_key_env: str,
) -> tuple[Any, dict]:
    """One real HTTP client for the children, honest about every field's source."""
    from .llm_provider import OpenAICompatibleLLM, ProviderConfig

    try:
        import httpx  # noqa: F401 — the dependency the real client needs
    except ImportError as exc:  # pragma: no cover - httpx is a declared dependency
        raise ProviderBlocked(
            "the real provider needs httpx (declared dependency 'httpx>=0.27,<1') — "
            "install it, or run with --provider stub for an offline deterministic run"
        ) from exc

    cfg = ProviderConfig(
        provider="openai-compatible",
        base_url=endpoint,
        model=model,
        api_key_env=api_key_env,
        api_key=credential,
    )
    client = httpx.Client(follow_redirects=False)
    llm = OpenAICompatibleLLM(cfg, client=client)
    info = {
        "provider": provider_id,
        "endpoint": endpoint,
        "model": model,
        "source": source,
        "api_key_env": api_key_env,
    }
    return llm, info


# the child contract
# --------------------------------------------------------------------------- #


@dataclass
class ChildBrief:
    """One child's assignment: the task, its role, and its own envelope.

    ``role`` selects a **tool ceiling** (:mod:`skaldir.subagent_roles`). The ceiling is
    **no longer opt-in**: ``None`` (or a missing role) resolves through
    :func:`skaldir.subagent_roles.resolve_role` to the canonical role for this child's
    ``kind`` — ``worker`` unless the kind says research (``explore``) or a check
    (``verifier``). A ceiling-free child is still available, but it must be *asked for*
    with the explicit sentinel ``"unroled"``; nobody lands there by omitting a parameter.
    That is the difference between a control and a suggestion: the earlier default was
    ``""`` (unlimited), so every child nobody thought about was unbounded.

    A role-bearing brief is bounded twice over: the role narrows the brief's own
    ``max_turns``/``budget_tokens`` if they exceed the role's envelope, so a
    caller can never grant a child more than its role allows.
    """

    child_id: str
    task: str
    tier: str = "small"
    max_turns: int = DEFAULT_MAX_TURNS
    budget_tokens: int = DEFAULT_BUDGET_TOKENS
    timeout_s: float = DEFAULT_TIMEOUT_S
    #: Which nesting level this child occupies (root children are ``1``).
    depth: int = 1
    #: ``explore`` | ``worker`` | ``verifier`` | ``main``, the sentinel ``"unroled"``
    #: (the historical, ceiling-free child) — or ``None``/absent for the canonical
    #: default role of ``kind``.
    role: str | None = None
    #: What this child is for (``code``/``research``/``verify``/…) — the reason the
    #: default role is not always ``worker``.
    kind: str = ""

    def __post_init__(self) -> None:
        self.child_id = _safe_id(self.child_id, what="child id")
        if not str(self.task or "").strip():
            raise SubagentError(f"child {self.child_id}: empty task")
        if self.tier not in TIERS:
            raise SubagentError(
                f"child {self.child_id}: unknown model tier {self.tier!r} "
                f"(use {' or '.join(TIERS)})"
            )
        if int(self.max_turns) < 1:
            raise SubagentError(f"child {self.child_id}: --max-turns must be >= 1")
        if int(self.budget_tokens) < 1:
            raise SubagentError(f"child {self.child_id}: --budget must be >= 1 token")
        if float(self.timeout_s) <= 0.0:
            raise SubagentError(f"child {self.child_id}: --timeout must be > 0 seconds")
        if int(self.depth) < 1:
            raise SubagentError(
                f"child {self.child_id}: depth must be >= 1 (the root run is depth 0)"
            )
        try:
            self.role = resolve_role(self.role, kind=self.kind)
        except ValueError as exc:
            raise SubagentError(f"child {self.child_id}: {exc}") from exc
        policy = policy_for(self.role_name, allow_unroled=True)
        if policy is not None:
            # The role's envelope is a ceiling on the brief, enforced here so no
            # caller can widen a role by passing a bigger number.
            self.max_turns = min(int(self.max_turns), int(policy.budget.max_turns))
            self.budget_tokens = min(
                int(self.budget_tokens), int(policy.budget.max_tokens)
            )

    @property
    def role_name(self) -> str:
        """The resolved role name (``""`` for the explicitly unroled child)."""
        return str(self.role or "")

    @property
    def policy(self):
        """The role's policy, or ``None`` for the unroled child."""
        return policy_for(self.role_name, allow_unroled=True)

    @property
    def tools(self) -> tuple[str, ...]:
        """The tools this child is **offered** (the provisioning ceiling)."""
        return tools_for(self.role_name)

    @property
    def may_spawn(self) -> bool:
        """Whether this child's role allows delegation at all (depth is separate)."""
        policy = self.policy
        return True if policy is None else bool(policy.may_spawn)

    def role_budget(self) -> RoleBudget | None:
        policy = self.policy
        return None if policy is None else policy.budget


@dataclass
class ChildContext:
    """Everything a child owns privately — its identity, budget and workspace."""

    run_id: str
    child_id: str
    session_id: str
    workdir: str
    transcript: str
    task: str
    tier: str
    max_turns: int
    budget_tokens: int
    timeout_s: float
    depth: int = 1
    max_depth: int = DEFAULT_MAX_DEPTH
    parent_run: str = ""
    parent_child: str = ""
    nested_dir: str = ""
    #: The child's role (``""`` = unroled) and the tools that role is offered.
    role: str = ""
    tools: tuple[str, ...] = ()
    #: Of the tools the role holds, the ones this run can **really execute** for the child
    #: (the run's handler map ∩ the role's tool list). Everything else the role holds is
    #: *declared*: the right exists, this run has no executor for it — a fact the child's own
    #: prompt states, so it does not have to discover it from a call that reports ``declared``.
    implemented_tools: tuple[str, ...] = ()

    @property
    def may_spawn(self) -> bool:
        """Can this child spawn grandchildren?

        Two independent ceilings, both enforced *before* anything is created: the
        **depth cap** (the harness') and the **role's spawn right** (a ``worker``
        may not delegate, whatever the depth allows). The answer is true only when
        both agree.
        """
        if self.depth >= self.max_depth:
            return False
        policy = policy_for(self.role, allow_unroled=True)
        return True if policy is None else bool(policy.may_spawn)

    @property
    def depth_allows_spawn(self) -> bool:
        """Just the depth half — so a refusal can name *which* ceiling it hit."""
        return self.depth < self.max_depth

    @property
    def role_allows_spawn(self) -> bool:
        """Just the role half."""
        policy = policy_for(self.role, allow_unroled=True)
        return True if policy is None else bool(policy.may_spawn)

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "child_id": self.child_id,
            "session_id": self.session_id,
            "workdir": self.workdir,
            "transcript": self.transcript,
            "tier": self.tier,
            "max_turns": self.max_turns,
            "budget_tokens": self.budget_tokens,
            "timeout_s": self.timeout_s,
            "depth": self.depth,
            "max_depth": self.max_depth,
            "may_spawn": self.may_spawn,
            "role": self.role or None,
            "tools": list(self.tools),
            "implemented_tools": list(self.implemented_tools),
            "nested_dir": self.nested_dir,
            "parent": (
                {"run_id": self.parent_run, "child_id": self.parent_child}
                if self.parent_run
                else None
            ),
        }


@dataclass
class ChildResult:
    """The structured result one child hands back to the parent."""

    child_id: str
    task: str = ""
    tier: str = ""
    status: str = STATUS_QUEUED
    turns: int = 0
    answer: str = ""
    error: str = ""
    stop_reason: str = ""
    spent_tokens: int = 0
    budget_tokens: int = 0
    max_turns: int = 0
    context: dict = field(default_factory=dict)
    turn_log: list[dict] = field(default_factory=list)
    spawn_ms: float = 0.0
    queued_ms: float = 0.0
    provider_ms: float = 0.0
    overhead_ms: float = 0.0
    duration_ms: float = 0.0
    started_at: str = ""
    finished_at: str = ""
    thread_alive: bool = False
    finished_ms: float = 0.0
    evidence: list[dict] = field(default_factory=list)
    #: Why this child is not delivered — ``""`` when it is (see FAILURE_CLASSES).
    failure_class: str = ""
    #: True when this child had to wait for the parallel cap (it was in the queue).
    queued: bool = False
    #: The child's nested family (its own grandchildren), if it spawned any.
    nested: dict = field(default_factory=dict)
    nested_tokens: int = 0
    notes: list[str] = field(default_factory=list)
    #: The role this child held and the tools that role offered it (MVP/93).
    role: str = ""
    tools: list[str] = field(default_factory=list)
    #: Every tool call this child's role ceiling refused, with its named code.
    tool_refusals: list[dict] = field(default_factory=list)
    #: Every tool directive this child's own answers carried, with what executing it produced
    #: (or why it was refused/declared). The run's record of *what the child did*.
    tool_calls: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Delivered inside its own envelope — the only success this layer claims."""
        return self.status in DELIVERED_CHILD

    @property
    def cost(self) -> dict:
        """What this child cost, in the units this runtime can actually measure."""
        return {
            "tokens": self.spent_tokens,
            "budget_tokens": self.budget_tokens,
            "turns": self.turns,
            "provider_calls": len(self.turn_log),
            "provider_ms": self.provider_ms,
            "wall_ms": self.duration_ms,
            "nested_tokens": self.nested_tokens,
            "currency": None,
            "note": "tokens are chars/4 estimates; no price table is bundled",
        }

    def as_dict(self) -> dict:
        return {
            "child_id": self.child_id,
            "task": self.task,
            "tier": self.tier,
            "status": self.status,
            "ok": self.ok,
            "failure_class": self.failure_class or None,
            "queued": self.queued,
            "turns": self.turns,
            "answer": self.answer,
            "error": self.error or None,
            "stop_reason": self.stop_reason or None,
            "spent_tokens": self.spent_tokens,
            "budget_tokens": self.budget_tokens,
            "max_turns": self.max_turns,
            "depth": self.context.get("depth"),
            "role": self.role or self.context.get("role") or None,
            "tools": self.tools or list(self.context.get("tools") or []),
            "tool_calls": self.tool_calls,
            "tool_refusals": self.tool_refusals,
            "cost": self.cost,
            "nested": self.nested or None,
            "notes": self.notes,
            "context": self.context,
            "turn_log": self.turn_log,
            "timing": {
                "spawn_ms": self.spawn_ms,
                "queued_ms": self.queued_ms,
                "provider_ms": self.provider_ms,
                "overhead_ms": self.overhead_ms,
                "duration_ms": self.duration_ms,
                "finished_ms": self.finished_ms,
            },
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "thread_alive": self.thread_alive,
            "evidence": self.evidence,
        }


def failure_class_of(result: ChildResult, *, run_cancel_reason: str = "") -> str:
    """Classify a stopped child in one place — status alone is not enough to act on.

    ``cancelled`` means *this* child was stopped (or the run budget tripped);
    ``parent_cancelled`` means the whole run was cancelled by its parent.
    """
    if result.ok:
        return ""
    if result.status == STATUS_TIMED_OUT:
        return "timeout"
    if result.status == STATUS_BUDGET_EXHAUSTED:
        return "budget_exhausted"
    if result.status == STATUS_MAX_TURNS:
        return "max_turns"
    if result.status == STATUS_FAILED:
        return "provider_error" if result.stop_reason == "provider call raised" else "child_error"
    if result.status == STATUS_CANCELLED:
        reason = str(run_cancel_reason or "")
        if reason.startswith("run budget exhausted"):
            return "run_budget"
        if reason and reason == str(result.stop_reason or ""):
            return "parent_cancelled"
        return "cancelled"
    return ""


@dataclass
class RunRecord:
    """The run-level collector result: one document for the whole family."""

    run_id: str
    status: str = RUN_RUNNING
    provider: dict = field(default_factory=dict)
    parallel: int = 0
    run_budget_tokens: int = 0
    spent_tokens: int = 0
    started_at: str = ""
    finished_at: str = ""
    duration_ms: float = 0.0
    cancel_reason: str = ""
    pid: int = 0
    children: list[ChildResult] = field(default_factory=list)
    timing: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    #: Which nesting level this run occupies (the run a caller starts is ``0``).
    depth: int = 0
    max_depth: int = DEFAULT_MAX_DEPTH
    #: ``{run_id, child_id}`` when this run was spawned by a child, else ``None``.
    parent: dict | None = None
    #: The run this one re-runs the unfinished children of (`agent retry`).
    retried_from: str = ""
    #: The most children that were ever running at the same time (the queue's ceiling).
    queue_max: int = 0
    #: What nesting actually did in this run: spawned runs and refused attempts.
    nesting: dict = field(default_factory=dict)

    @property
    def delivered(self) -> int:
        return sum(1 for child in self.children if child.ok)

    @property
    def failed(self) -> list[str]:
        return [child.child_id for child in self.children if not child.ok]

    @property
    def partial(self) -> bool:
        return self.delivered < len(self.children)

    @property
    def failure_classes(self) -> dict:
        """``{failure_class: [child ids]}`` — the machine-readable why of each gap."""
        classes: dict[str, list[str]] = {}
        for child in self.children:
            if not child.ok:
                classes.setdefault(child.failure_class or "unknown", []).append(child.child_id)
        return classes

    @property
    def cost(self) -> dict:
        return cost_summary([child.cost for child in self.children])

    def as_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "status": self.status,
            "provider": self.provider,
            "parallel": self.parallel,
            "queue_max": self.queue_max,
            "run_budget_tokens": self.run_budget_tokens,
            "spent_tokens": self.spent_tokens,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "cancel_reason": self.cancel_reason or None,
            "pid": self.pid,
            "host": "in-process",
            "depth": self.depth,
            "max_depth": self.max_depth,
            "parent": self.parent,
            "retried_from": self.retried_from or None,
            "children_total": len(self.children),
            "children_delivered": self.delivered,
            "children_not_delivered": self.failed,
            "failure_classes": self.failure_classes,
            "partial": self.partial,
            "complete": self.status != RUN_RUNNING,
            "cost": self.cost,
            "nesting": self.nesting,
            "timing": self.timing,
            "children": [child.as_dict() for child in self.children],
            "notes": self.notes,
        }


def cost_summary(costs: Sequence[dict]) -> dict:
    """Add per-child costs into one run figure — never a money claim."""
    return {
        "tokens": sum(int(item.get("tokens") or 0) for item in costs),
        "turns": sum(int(item.get("turns") or 0) for item in costs),
        "provider_calls": sum(int(item.get("provider_calls") or 0) for item in costs),
        "provider_ms": round(sum(float(item.get("provider_ms") or 0.0) for item in costs), 3),
        "wall_ms": round(sum(float(item.get("wall_ms") or 0.0) for item in costs), 3),
        "nested_tokens": sum(int(item.get("nested_tokens") or 0) for item in costs),
        "children": len(costs),
        "currency": None,
        "note": "tokens are chars/4 estimates; no price table is bundled",
    }


def briefs_from_tasks(
    tasks: Sequence[str],
    *,
    tier: str = "small",
    max_turns: int = DEFAULT_MAX_TURNS,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    child_ids: Sequence[str] | None = None,
    depth: int = 1,
    role: str | None = None,
    kind: str = "",
) -> list[ChildBrief]:
    """One brief per task; ids default to ``c1..cN`` (stable, not random).

    ``role`` gives every child of this family the same ceiling
    (``explore``/``worker``/``verifier``/``main``). It defaults to ``None``, which
    resolves to the canonical role for ``kind`` — the child is **bounded by default**
    (``worker``, or ``explore``/``verifier`` when the kind says so). The historical
    ceiling-free child still exists but must be asked for explicitly with the sentinel
    ``"unroled"``; it is never what a forgotten parameter gets you.
    """
    if not tasks:
        raise SubagentError("no task given: one child needs at least one --task")
    ids = list(child_ids) if child_ids else [f"c{index + 1}" for index in range(len(tasks))]
    if len(ids) != len(tasks):
        raise SubagentError(f"{len(ids)} child id(s) for {len(tasks)} task(s)")
    return [
        ChildBrief(
            child_id=str(child_id),
            task=str(task),
            tier=tier,
            max_turns=int(max_turns),
            budget_tokens=int(budget_tokens),
            timeout_s=float(timeout_s),
            depth=int(depth),
            role=role,
            kind=kind,
        )
        for child_id, task in zip(ids, tasks, strict=True)
    ]


def retry_briefs(
    record: dict,
    *,
    child_ids: Sequence[str] = (),
    only_unfinished: bool = True,
    tier: str = "",
    max_turns: int = 0,
    budget_tokens: int = 0,
    timeout_s: float = 0.0,
    role: str | None = None,
) -> list[ChildBrief]:
    """The briefs for a retry of ``record``'s children.

    Default: **only the children that did not deliver**, with their original
    task and (unless overridden) their original tier/budgets/deadline — a retry
    of work that already succeeded would be a new run, not a retry. ``child_ids``
    selects explicitly (and then ``only_unfinished`` is not applied), so
    ``--child c2`` can re-run one child even after a partial success. ``role``
    overrides every retried child's role when given; left out, each child keeps
    the role it was recorded with.
    """
    children = [child for child in (record.get("children") or []) if isinstance(child, dict)]
    wanted = {str(item) for item in child_ids if str(item)}
    selected: list[dict] = []
    for child in children:
        if wanted:
            if str(child.get("child_id")) in wanted:
                selected.append(child)
            continue
        if not only_unfinished or not child.get("ok"):
            selected.append(child)
    if wanted:
        missing = sorted(wanted - {str(child.get("child_id")) for child in selected})
        if missing:
            raise SubagentError(
                "no such child in run "
                f"{record.get('run_id')!r}: {', '.join(missing)} "
                f"(it has {', '.join(str(c.get('child_id')) for c in children) or 'none'})"
            )
    if not selected:
        raise SubagentError(
            f"run {record.get('run_id')!r} has no child left to retry "
            "(pass --all to re-run the delivered ones too)"
        )
    depth = int(record.get("depth") or 0) + 1
    briefs: list[ChildBrief] = []
    for child in selected:
        previous = child.get("context") or {}
        # The role travels with the retry. A retry that dropped it would silently
        # *re-role* the child: an ``explore`` child (read-only, on purpose) would come
        # back as the default ``worker`` and gain write/exec rights it was deliberately
        # never given. An unroled original stays unroled, by name. An explicit override
        # (``agent retry --role …``) wins over the recording, and it is the *only* way
        # to re-role a retry.
        recorded_role = str(child.get("role") or "").strip().lower() or UNROLED
        override = str(role or "").strip().lower()
        briefs.append(
            ChildBrief(
                child_id=str(child.get("child_id")),
                task=str(child.get("task") or ""),
                tier=str(tier or child.get("tier") or "small"),
                max_turns=int(max_turns or child.get("max_turns") or DEFAULT_MAX_TURNS),
                budget_tokens=int(
                    budget_tokens or child.get("budget_tokens") or DEFAULT_BUDGET_TOKENS
                ),
                timeout_s=float(
                    timeout_s or previous.get("timeout_s") or DEFAULT_TIMEOUT_S
                ),
                depth=depth,
                role=override or recorded_role,
            )
        )
    return briefs


def collect_payload(record: dict, *, child_id: str = "") -> dict:
    """The collector view of a run record: one structured result per child."""
    children = list(record.get("children") or [])
    if child_id:
        children = [child for child in children if child.get("child_id") == child_id]
    results = [
        {
            "child_id": child.get("child_id"),
            "status": child.get("status"),
            "ok": bool(child.get("ok")),
            "failure_class": child.get("failure_class"),
            "turns": child.get("turns"),
            "answer": child.get("answer"),
            "error": child.get("error"),
            "stop_reason": child.get("stop_reason"),
            "spent_tokens": child.get("spent_tokens"),
            "cost": child.get("cost"),
            "depth": child.get("depth"),
            "nested": child.get("nested"),
            "session_id": (child.get("context") or {}).get("session_id"),
            "workdir": (child.get("context") or {}).get("workdir"),
            "transcript": (child.get("context") or {}).get("transcript"),
            "timing": child.get("timing"),
            "evidence": child.get("evidence"),
        }
        for child in children
    ]
    classes: dict[str, list[str]] = {}
    for item in results:
        if not item["ok"]:
            classes.setdefault(str(item["failure_class"] or "unknown"), []).append(
                str(item["child_id"])
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": record.get("run_id"),
        "status": record.get("status"),
        "complete": bool(record.get("complete")),
        "partial": bool(record.get("partial")),
        "delivered": sum(1 for item in results if item["ok"]),
        "failure_classes": classes,
        "depth": record.get("depth"),
        "max_depth": record.get("max_depth"),
        "parent": record.get("parent"),
        "retried_from": record.get("retried_from"),
        "queue_max": record.get("queue_max"),
        "cost": record.get("cost"),
        "results": results,
        "timing": record.get("timing") or {},
        "provider": record.get("provider") or {},
        "notes": record.get("notes") or [],
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "duration_ms": record.get("duration_ms"),
    }


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #


def _build_prompt(
    brief: ChildBrief,
    context: ChildContext,
    turns: list[dict],
    *,
    context_chars: int,
    nesting: dict | None = None,
) -> str:
    lines = [
        f"You are child agent '{brief.child_id}' in an isolated context "
        f"(run '{context.run_id}').",
        f"Session id: {context.session_id}",
        f"Model tier: {brief.tier}",
        f"Your working directory: {context.workdir}",
        f"Turn budget: {brief.max_turns}; token budget: {brief.budget_tokens} "
        "(estimated as characters / 4).",
        "You cannot see any sibling child's context — your transcript is your own.",
    ]
    if context.role:
        # The role is stated as what the child *has*, not as a request to behave:
        # the tool list below is the provisioning ceiling, and a call outside it is
        # refused at the dispatcher with a named code.
        policy = policy_for(context.role, allow_unroled=True)
        lines.append(
            f"Role: {context.role}"
            + (f" — {policy.when_to_use}" if policy is not None else "")
        )
        lines.append(
            "Tools your role holds: "
            + (", ".join(context.tools) if context.tools else "none")
            + ". Any other tool call is refused by the runtime, not by you."
        )
        if context.implemented_tools:
            # Stated as the interface it is: which of the role's tools this run can *execute*,
            # how to ask for one, and which ones it can only *declare*. A child that learns the
            # difference from a prompt spends its few turns working instead of probing.
            implemented = set(context.implemented_tools)
            declared = [name for name in context.tools if name not in implemented]
            lines.append(
                "Tools this run can really execute for you: "
                + ", ".join(context.implemented_tools)
                + ". Ask for one by putting a line like "
                + f'{CHILD_TOOL_MARKER}read_file]] {{"path": "src/x.py"}}'
                + " in your answer (one JSON object of arguments per line); the runtime runs "
                "it and shows you the result in your transcript on the next turn."
            )
            if declared:
                lines.append(
                    "Tools your role holds but this run cannot execute: "
                    + ", ".join(declared)
                    + " — a call to one is reported 'declared' and does nothing."
                )
    lines.append(
        f"Nesting: you are at depth {context.depth} of {context.max_depth} — "
        + (
            "you may spawn child agents below the cap."
            if context.may_spawn
            else (
                "the cap is reached, so you may not spawn child agents."
                if not context.depth_allows_spawn
                else f"your role ({context.role or 'unroled'}) may not spawn child agents."
            )
        )
    )
    nested_lines = _nested_lines(nesting)
    if nested_lines:
        lines += ["", "Children you spawned reported:"] + nested_lines
    lines += [
        "",
        "Task:",
        brief.task,
        "",
        "Work in turns. When you are finished, answer with your result and put a line "
        f"starting with {FINAL_MARKER!r} at the end; without that marker the runtime "
        "keeps you working until your turn budget or token budget runs out.",
    ]
    if turns:
        lines += ["", "Your transcript so far (truncated to the context budget):"]
        lines.append(_transcript(turns, limit=context_chars))
    return "\n".join(lines)


def _nested_lines(nesting: dict | None) -> list[str]:
    """What a child's own children reported — only *its* children, never a sibling's."""
    if not nesting:
        return []
    lines: list[str] = []
    for item in nesting.get("results") or []:
        status = item.get("status")
        answer = item.get("answer") or item.get("error") or item.get("stop_reason") or ""
        lines.append(f"- {item.get('child_id')} [{status}]: {str(answer).strip()[:400]}")
    return lines


def _transcript(turns: list[dict], *, limit: int) -> str:
    blocks = []
    for turn in turns:
        block = f"# turn {turn['turn']} ({turn['prompt_id']})\nanswer: {turn['text']}"
        calls = turn.get("tool_calls") or []
        if calls:
            # What the runtime did with this turn's directives — a refusal is as visible to the
            # child as a result, so a forbidden call is not retried blindly.
            block += "\ntools run by the runtime: " + json.dumps(calls, default=str)
        blocks.append(block)
    text = "\n\n".join(blocks)
    if limit > 0 and len(text) > limit:
        text = "… (earlier turns truncated) …\n" + text[-limit:]
    return text


class SubagentRun:
    """One family of children: creation, parallel execution, collection, cancel.

    Lifecycle: :meth:`start` spawns the worker threads, :meth:`run` also collects
    until every child is terminal (or its deadline hit) and returns the
    :class:`RunRecord`. A run is durable while it lives: ``run.json`` is rewritten
    atomically on every state change and ``events.jsonl`` is appended to and
    flushed as events happen, so another process can watch or cancel it.
    """

    def __init__(
        self,
        *,
        provider: Any,
        briefs: Sequence[ChildBrief],
        state_dir: str | os.PathLike[str],
        run_id: str = "",
        parallel: int = 0,
        run_budget_tokens: int = 0,
        poll_s: float = 0.02,
        cancel_poll_s: float = 0.05,
        on_event: Callable[[dict], None] | None = None,
        workdir_base: str = "",
        context_chars: int = DEFAULT_CONTEXT_CHARS,
        provider_summary: dict | None = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        depth: int = 0,
        parent_run: str = "",
        parent_child: str = "",
        retried_from: str = "",
        stop_grace_s: float = DEFAULT_STOP_GRACE_S,
        tool_handlers: Mapping[str, Callable[..., Any]] | None = None,
        workspace: str | os.PathLike[str] | None = None,
    ) -> None:
        if not briefs:
            raise SubagentError("a run needs at least one child brief")
        self.run_id = _safe_id(run_id) if str(run_id or "").strip() else uuid.uuid4().hex[:12]
        self.depth = max(0, int(depth))
        self.max_depth = int(max_depth)
        if self.max_depth < 0 or self.max_depth > MAX_DEPTH_LIMIT:
            raise SubagentError(
                f"--max-depth must be between 0 and {MAX_DEPTH_LIMIT} (got {self.max_depth}); "
                "0 = children may never spawn children, 1 = a child of the root may not "
                "(the default)"
            )
        self.parent_run = str(parent_run or "")
        self.parent_child = str(parent_child or "")
        self.retried_from = str(retried_from or "")
        self._stop_grace_s = max(0.0, float(stop_grace_s))
        self.run_dir = Path(state_dir).expanduser() / self.run_id
        if self.run_dir.exists() and (self.run_dir / RUN_FILE).exists():
            raise SubagentError(f"run {self.run_id} already exists under {self.run_dir.parent}")
        (self.run_dir / "children").mkdir(parents=True, exist_ok=True)
        self._provider = provider
        self._briefs = {brief.child_id: brief for brief in briefs}
        if len(self._briefs) != len(briefs):
            raise SubagentError("duplicate child id in this run")
        if tool_handlers:
            # This run's children dispatch their role's tools through *these* handlers. Without
            # them a granted tool is reported ``declared`` and never executed — which is the
            # honest default for a harness that owns rights, not execution, but useless to a run
            # that wants to delegate real work (``runtime_delegation``).
            self._tool_handlers = dict(tool_handlers)
        self._order = [brief.child_id for brief in briefs]
        self.parallel = max(0, int(parallel))
        self.run_budget_tokens = max(0, int(run_budget_tokens))
        self._poll_s = max(0.005, float(poll_s))
        self._cancel_poll_s = max(0.005, float(cancel_poll_s))
        self._on_event = on_event
        self._workdir_base = str(workdir_base or "")
        self._context_chars = max(0, int(context_chars))
        #: The workspace the tool handlers were built for (the scope a write may not leave).
        #: Recorded, not enforced here: the handler map is the enforcement point, and a caller
        #: that registers none gets no execution at all.
        self.workspace = str(Path(workspace).expanduser()) if workspace else ""
        self._lock = threading.RLock()
        self._t0 = time.monotonic()
        self._started_ts = time.time()
        self._events_path = self.run_dir / EVENTS_FILE
        self._cancel_path = self.run_dir / CANCEL_FILE
        self._stopping = False
        self._cancel_reason = ""
        self._cancel_requested_at = 0.0
        self._record = RunRecord(
            run_id=self.run_id,
            provider=dict(provider_summary or {}),
            parallel=self.parallel,
            run_budget_tokens=self.run_budget_tokens,
            started_at=_iso(self._started_ts),
            pid=os.getpid(),
            depth=self.depth,
            max_depth=self.max_depth,
            parent=(
                {"run_id": self.parent_run, "child_id": self.parent_child}
                if self.parent_run
                else None
            ),
            retried_from=self.retried_from,
        )
        self._results: dict[str, ChildResult] = {}
        self._live: dict[str, str] = {child_id: STATUS_QUEUED for child_id in self._order}
        self._child_started: dict[str, float] = {}
        self._spend: dict[str, int] = {}
        self._stop_flags = {child_id: threading.Event() for child_id in self._order}
        self._contexts = {
            brief.child_id: self._make_context(brief) for brief in briefs
        }
        self._threads: dict[str, threading.Thread] = {}
        self._sem = threading.Semaphore(self.parallel) if self.parallel > 0 else None
        self._last_cancel_check = 0.0
        self._snapshot_written = False
        self._io_errors: list[str] = []
        self._running_now = 0
        self._running_now_max = 0
        self._nested_done: dict[str, bool] = {}
        #: One tool dispatcher per role-bearing child (MVP/93). Built lazily, so
        #: an unroled run pays nothing for a ceiling it does not use.
        self._dispatchers: dict[str, RoleToolDispatcher] = {}
        #: Refusals the role ceilings produced, per child (MVP/93).
        self._tool_refusals: dict[str, list[dict]] = {}
        self._record.nesting = {"spawned": [], "refused": []}
        self._emit(
            "run_created",
            parallel=self.parallel,
            children=[brief.child_id for brief in briefs],
            roles={brief.child_id: (brief.role or None) for brief in briefs},
            provider=self._record.provider,
            depth=self.depth,
            max_depth=self.max_depth,
            parent=self._record.parent,
            retried_from=self.retried_from or None,
        )
        self._snapshot()

    # -- context / workspace -------------------------------------------------

    def _make_context(self, brief: ChildBrief) -> ChildContext:
        base = (
            Path(self._workdir_base).expanduser() if self._workdir_base else self.run_dir / "work"
        )
        workdir = base / brief.child_id
        workdir.mkdir(parents=True, exist_ok=True)
        transcript = self.run_dir / "children" / brief.child_id / "transcript.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        nested_dir = transcript.parent / "nested"
        context = ChildContext(
            run_id=self.run_id,
            child_id=brief.child_id,
            session_id=f"{self.run_id}:{brief.child_id}",
            workdir=str(workdir),
            transcript=str(transcript),
            task=brief.task,
            tier=brief.tier,
            max_turns=brief.max_turns,
            budget_tokens=brief.budget_tokens,
            timeout_s=brief.timeout_s,
            depth=brief.depth,
            max_depth=self.max_depth,
            parent_run=self.parent_run or self.run_id,
            parent_child=self.parent_child or brief.child_id,
            nested_dir=str(nested_dir),
            role=brief.role_name,
            tools=brief.tools,
            implemented_tools=tuple(
                sorted(set(brief.tools) & set(self._tool_handlers))
            ),
        )
        # A visible marker of the private workspace: which child owns this directory.
        _write_json_atomic(workdir / ".subagent.json", context.as_dict())
        return context

    def context_of(self, child_id: str) -> ChildContext:
        return self._contexts[child_id]

    # -- the tool ceiling (MVP/93) ------------------------------------------ #

    def dispatcher_for(self, child_id: str) -> RoleToolDispatcher:
        """The tool dispatcher of ``child_id`` — one per child, built once.

        A role-bearing child gets a dispatcher whose tool list is its role's; the
        dispatcher is the object that *refuses* a call the role does not hold
        (:class:`~skaldir.subagent_roles.ToolCeilingRefused`). An unroled child
        gets one too, with no tool ceiling — its only bound is the tool-call
        budget — so callers have a single entry point either way.
        """
        existing = self._dispatchers.get(child_id)
        if existing is not None:
            return existing
        brief = self._briefs.get(child_id)
        if brief is None:
            raise SubagentError(f"run {self.run_id} has no child {child_id!r}")
        dispatcher = RoleToolDispatcher(
            brief.role_name,
            handlers=self._tool_handlers,
            budget=brief.role_budget(),
        )
        self._dispatchers[child_id] = dispatcher
        return dispatcher

    #: Handlers a role's granted tools run through. Empty by default: the harness
    #: owns a child's *rights*, not its execution — a granted tool with no handler
    #: is reported as ``mode: "declared"`` rather than silently succeeding.
    _tool_handlers: dict[str, Callable[..., Any]] = {}

    def dispatch_tool(self, child_id: str, tool: str, **args: Any) -> dict:
        """Dispatch one tool call for a child, through its role ceiling.

        Raises :class:`~skaldir.subagent_roles.ToolCeilingRefused` (a **named**
        refusal carrying a stable ``code``) when the role does not hold the tool.
        The refusal is recorded on the dispatcher and on the child's result, so
        the run's own record shows what was attempted and what the ceiling did.
        """
        dispatcher = self.dispatcher_for(child_id)
        try:
            return dispatcher.dispatch(tool, **args)
        except ToolCeilingRefused as exc:
            self._note_tool_refusal(child_id, exc.as_dict())
            raise

    def _note_tool_refusal(self, child_id: str, refusal: dict) -> None:
        """Record a refusal on the run and in the run's events.

        Kept in the run's own table (not on a ``ChildResult`` that may not exist
        yet) and copied onto the child's result by :meth:`_store_result`, so a
        refusal is durable whether or not the child ever finishes cleanly.
        """
        with self._lock:
            self._tool_refusals.setdefault(child_id, []).append(refusal)
        if child_id in self._results:
            self._results[child_id].tool_refusals.append(refusal)
        self._emit("child_tool_refused", child_id=child_id, **refusal)

    def tool_refusals(self, child_id: str = "") -> dict[str, list[dict]]:
        """Every refusal the role ceilings produced, per child."""
        with self._lock:
            if child_id:
                return {child_id: list(self._tool_refusals.get(child_id, []))}
            return {key: list(value) for key, value in self._tool_refusals.items()}

    def role_report(self) -> dict:
        """Per child: its role, the tools it was offered, and what was refused."""
        out: dict[str, dict] = {}
        for child_id in self._order:
            context = self._contexts.get(child_id)
            dispatcher = self._dispatchers.get(child_id)
            out[child_id] = {
                "role": (context.role or None) if context is not None else None,
                "tools_offered": (
                    list(dispatcher.offered)
                    if dispatcher is not None
                    else list(getattr(context, "tools", ()) or ())
                ),
                "may_spawn": bool(context.may_spawn) if context is not None else None,
                "spawn_ceiling": {
                    "depth": (
                        "allows" if context is not None and context.depth_allows_spawn
                        else "blocks"
                    ),
                    "role": (
                        "allows" if context is not None and context.role_allows_spawn
                        else "blocks"
                    ),
                },
                "refusals": dispatcher.refusals() if dispatcher is not None else [],
            }
        return out

    def nested_spawner(self, child_id: str) -> Callable[..., RunRecord]:
        """The **only** way a child can spawn children — and it enforces the cap.

        Returns a callable that runs one nested family for ``child_id``. Calling
        it past the cap raises :class:`DepthCapReached` *before* any directory or
        thread exists, so a refused nesting leaves nothing behind. The nested run
        lives under ``children/<child_id>/nested/<run_id>``, inherits this run's
        ``max_depth`` (so a configured cap cannot be exceeded deeper down), is
        linked back with ``parent: {run_id, child_id}`` and its tokens are
        reported separately (they never silently spend the child's own budget).
        """
        context = self._contexts.get(child_id)
        if context is None:
            raise SubagentError(f"run {self.run_id} has no child {child_id!r}")

        def spawn_children(
            tasks: Sequence[str],
            *,
            run_id: str = "",
            parallel: int = 0,
            tier: str = "",
            max_turns: int = 0,
            budget_tokens: int = 0,
            timeout_s: float = 0.0,
        ) -> RunRecord:
            if not context.may_spawn:
                raise DepthCapReached(
                    child_id=context.child_id,
                    depth=context.depth,
                    max_depth=context.max_depth,
                )
            nested_id = str(run_id or f"{context.child_id}-n1")
            nested_briefs = briefs_from_tasks(
                list(tasks),
                tier=str(tier or context.tier),
                max_turns=int(max_turns or context.max_turns),
                budget_tokens=int(budget_tokens or context.budget_tokens),
                timeout_s=float(timeout_s or context.timeout_s),
                depth=context.depth + 1,
            )
            nested = SubagentRun(
                provider=self._provider,
                briefs=nested_briefs,
                state_dir=Path(context.nested_dir),
                run_id=nested_id,
                parallel=int(parallel),
                on_event=self._on_event,
                context_chars=self._context_chars,
                provider_summary=self._record.provider,
                max_depth=self.max_depth,
                depth=context.depth,
                parent_run=self.run_id,
                parent_child=context.child_id,
                stop_grace_s=self._stop_grace_s,
            )
            record = nested.run()
            return record

        return spawn_children

    # -- events / snapshot ---------------------------------------------------

    def _emit(self, kind: str, *, child_id: str = "", **fields: Any) -> dict:
        event = {
            "kind": kind,
            "run_id": self.run_id,
            "child_id": child_id,
            "at": _iso(),
            "t_ms": _round_ms(time.monotonic() - self._t0),
        }
        event.update(fields)
        try:
            _append_line(self._events_path, event)
        except OSError as exc:
            self._note_io_error(EVENTS_FILE, exc)
        with self._lock:
            stopping = self._stopping
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:  # noqa: BLE001 — a watcher must never break a run
                pass
        if not stopping:
            self._snapshot()
        return event

    def _record_dict(self) -> dict:
        record = self._record
        record.spent_tokens = sum(result.spent_tokens for result in self._results.values())
        children: list[ChildResult] = []
        for child_id in self._order:
            result = self._results.get(child_id)
            if result is not None:
                children.append(result)
                continue
            brief = self._briefs[child_id]
            context = self._contexts[child_id]
            status = self._live.get(child_id, STATUS_QUEUED)
            children.append(
                ChildResult(
                    child_id=child_id,
                    task=brief.task,
                    tier=brief.tier,
                    status=status,
                    budget_tokens=brief.budget_tokens,
                    max_turns=brief.max_turns,
                    context=context.as_dict(),
                    turn_log=_read_turns(Path(context.transcript)),
                )
            )
        record.children = children
        record.timing = self._timing_summary()
        return record.as_dict()

    def _timing_summary(self) -> dict:
        done = [result for result in self._results.values()]
        spawns = [result.spawn_ms for result in done]
        overheads = [result.overhead_ms for result in done]
        waits = [result.queued_ms for result in done if result.queued]
        finishes = [result.finished_ms for result in done if result.finished_ms > 0.0]
        return {
            "spawn_samples": len(spawns),
            "spawn_p50_ms": round(percentile(spawns, 0.5), 3),
            "spawn_p95_ms": round(percentile(spawns, 0.95), 3),
            "spawn_max_ms": round(max(spawns), 3) if spawns else 0.0,
            "overhead_p50_ms": round(percentile(overheads, 0.5), 3),
            "overhead_p95_ms": round(percentile(overheads, 0.95), 3),
            "provider_total_ms": round(sum(result.provider_ms for result in done), 3),
            "children_total_ms": round(sum(result.duration_ms for result in done), 3),
            "run_total_ms": _round_ms(time.monotonic() - self._t0),
            "first_result_ms": round(min(finishes), 3) if finishes else 0.0,
            "last_result_ms": round(max(finishes), 3) if finishes else 0.0,
            "parallel": self.parallel,
            "queue_max_concurrent": self._running_now_max,
            "queue_waited_children": [result.child_id for result in done if result.queued],
            "queue_wait_p50_ms": round(percentile(waits, 0.5), 3),
            "queue_wait_p95_ms": round(percentile(waits, 0.95), 3),
            "queue_wait_total_ms": round(sum(waits), 3),
            "queued_children": [
                child_id for child_id, status in self._live.items() if status == STATUS_QUEUED
            ],
        }

    def snapshot(self) -> dict:
        """The run record as it stands right now (also what ``run.json`` holds)."""
        with self._lock:
            return self._record_dict()

    def _snapshot(self) -> dict:
        with self._lock:
            record = self._record_dict()
        try:
            _write_json_atomic(self.run_dir / RUN_FILE, record)
        except OSError as exc:
            if not self._snapshot_written:
                # A run that cannot write its own record must not pretend to run.
                raise
            self._note_io_error(RUN_FILE, exc)
        self._snapshot_written = True
        return record

    def _note_io_error(self, where: str, exc: BaseException) -> None:
        """A failing status write is recorded, never allowed to fail a child."""
        message = f"{where}: {type(exc).__name__}: {exc}"
        with self._lock:
            if message not in self._io_errors:
                self._io_errors.append(message)

    # -- cancel --------------------------------------------------------------

    def _cancel_requested(self) -> object:
        payload = _read_json(self._cancel_path)
        return payload if isinstance(payload, dict) else None

    def cancel(self, reason: str = "parent cancel") -> bool:
        """Stop this run from **inside** the process (queued children never start).

        Returns ``False`` when the run was already cancelled — the first reason
        wins, so the record always names the actual cause.
        """
        with self._lock:
            if self._cancel_reason:
                return False
            self._cancel_reason = str(reason or "parent cancel")
            self._cancel_requested_at = time.monotonic()
        for child_id in self._order:
            self._stop_flags[child_id].set()
        self._emit("run_cancel_requested", reason=self._cancel_reason, source="in-process")
        return True

    def _check_cancel_file(self) -> None:
        now = time.monotonic()
        if now - self._last_cancel_check < self._cancel_poll_s:
            return
        self._last_cancel_check = now
        payload = self._cancel_requested()
        if not isinstance(payload, dict):
            return
        with self._lock:
            if self._cancel_reason:
                return
            self._cancel_reason = str(
                payload.get("reason") or "cancel requested by another process"
            )
            self._cancel_requested_at = time.monotonic()
        for child_id in self._order:
            self._stop_flags[child_id].set()
        self._emit("run_cancel_requested", reason=self._cancel_reason, source="cancel file",
                   requested_at=payload.get("at"))

    def _check_run_budget(self) -> None:
        if self.run_budget_tokens <= 0:
            return
        with self._lock:
            if self._cancel_reason:
                return
            live_spent = sum(self._spend.values())
            if live_spent < self.run_budget_tokens:
                return
            self._cancel_reason = (
                f"run budget exhausted: {live_spent} >= {self.run_budget_tokens} estimated tokens"
            )
            self._cancel_requested_at = time.monotonic()
        for child_id in self._order:
            self._stop_flags[child_id].set()
        self._emit("run_budget_exhausted", spent_tokens=live_spent,
                   run_budget_tokens=self.run_budget_tokens)

    def _note_spend(self, child_id: str, spent: int) -> None:
        """A child reports its own cumulative spend — the run-level cap reads this."""
        with self._lock:
            self._spend[child_id] = int(spent)

    def stop_child(self, child_id: str, reason: str) -> None:
        """Ask one child to stop at its next turn boundary."""
        if child_id in self._stop_flags:
            self._stop_flags[child_id].set()

    # -- execution -----------------------------------------------------------

    def start(self) -> None:
        """Create and start one worker thread per child (returns immediately)."""
        with self._lock:
            if self._threads:
                raise SubagentError(f"run {self.run_id} already started")
        for child_id in self._order:
            thread = threading.Thread(
                target=self._worker,
                args=(child_id,),
                name=f"subagent-{self.run_id}-{child_id}",
                daemon=True,
            )
            self._threads[child_id] = thread
        for thread in self._threads.values():
            thread.start()

    def run(self) -> RunRecord:
        """Start the family and collect until every child is terminal.

        While collecting, a parent cancel is honoured in three ways: an in-process
        :meth:`cancel`, the durable ``cancel.json`` request, and — when this runs
        on the main thread — ``SIGINT``/``SIGTERM``, which set the same flags so a
        Ctrl-C stops the children instead of leaving a ``running`` record behind.
        """
        self.start()
        previous = self._install_signal_guard()
        try:
            self._collect()
        finally:
            self._restore_signal_guard(previous)
        self._finish()
        return self._record

    def _collect(self) -> None:
        pending = dict(self._threads)
        while pending:
            for child_id, thread in list(pending.items()):
                thread.join(timeout=self._poll_s)
                if not thread.is_alive():
                    pending.pop(child_id, None)
                    continue
                if self._results.get(child_id) is not None:
                    pending.pop(child_id, None)  # a preemptive settle already landed
                    continue
                brief = self._briefs[child_id]
                if self._elapsed_for(child_id) > brief.timeout_s:
                    self.stop_child(child_id, "timeout")
                    self._store_result(self._timeout_result(child_id), preemptive=True)
                    pending.pop(child_id, None)
                    continue
                if self._cancel_reason and self._grace_expired(child_id):
                    self.stop_child(child_id, "cancel grace")
                    self._store_result(self._cancelled_result(child_id), preemptive=True)
                    pending.pop(child_id, None)
            if pending:
                self._check_cancel_file()
                self._check_run_budget()

    def _grace_expired(self, child_id: str) -> bool:
        """After a cancel the collector waits ``stop_grace_s``, then settles itself."""
        with self._lock:
            requested = self._cancel_requested_at
        if not requested:
            return False
        return (time.monotonic() - requested) > self._stop_grace_s

    # -- the parent's own cancellation ---------------------------------------

    def _install_signal_guard(self) -> list[tuple[int, Any]]:
        """Make a parent cancel (Ctrl-C / SIGTERM) stop the children.

        Only the main thread may install a handler; a run collected from a worker
        thread simply relies on :meth:`cancel` / ``cancel.json``. The previous
        handlers are restored in :meth:`_restore_signal_guard` — this harness must
        not change how the process behaves once the run is over.
        """
        if threading.current_thread() is not threading.main_thread():
            return []
        previous: list[tuple[int, Any]] = []
        for signal_name in ("SIGINT", "SIGTERM"):
            number = getattr(signal, signal_name, None)
            if number is None:
                continue
            try:
                previous.append((int(number), signal.getsignal(int(number))))
                signal.signal(int(number), self._on_parent_signal)
            except (ValueError, OSError, TypeError):
                previous.pop()
        return previous

    def _restore_signal_guard(self, previous: Sequence[tuple[int, Any]]) -> None:
        for number, handler in previous:
            try:
                signal.signal(int(number), handler)
            except (ValueError, OSError, TypeError):
                continue

    def _on_parent_signal(self, number: int, _frame: Any) -> None:
        try:
            name = signal.Signals(int(number)).name
        except ValueError:  # pragma: no cover - exotic signal number
            name = f"signal {number}"
        reason = f"parent cancelled the run ({name})"
        if self.cancel(reason):
            self._emit("run_parent_cancelled", signal=name, pid=os.getpid(), reason=reason)

    def _elapsed_for(self, child_id: str) -> float:
        with self._lock:
            started = self._child_started.get(child_id, 0.0)
        if not started:
            return 0.0
        return time.monotonic() - float(started)

    def _run_nested(
        self, brief: ChildBrief, context: ChildContext, result: ChildResult, declared: list[str]
    ) -> dict:
        """Execute a child's declared nesting through the depth-checked spawner.

        A refusal is recorded (event + run record + a note on this child) and the
        child **keeps working** — nesting is optional work, not the child's goal.
        Two ceilings are checked, and the *role* one is checked first because it is
        the stricter statement: a ``worker`` may not delegate whatever the depth
        allows, and the refusal it gets names that ceiling
        (``role_ceiling.delegation_forbidden``) rather than the depth cap.
        """
        self._nested_done[brief.child_id] = True
        self._emit(
            "child_nesting_declared",
            child_id=brief.child_id,
            depth=context.depth,
            max_depth=context.max_depth,
            may_spawn=context.may_spawn,
            role=context.role or None,
            tasks=len(declared),
        )
        try:
            self.dispatch_tool(brief.child_id, "spawn", tasks=list(declared))
        except ToolCeilingRefused as exc:
            refusal = {
                "child_id": brief.child_id,
                "role": context.role or None,
                "code": exc.code,
                "ceiling": "role",
                "depth": context.depth,
                "max_depth": context.max_depth,
                "tasks": len(declared),
                "reason": exc.reason,
            }
            with self._lock:
                self._record.nesting.setdefault("refused", []).append(refusal)
            result.notes.append(f"spawn refused by the role ceiling: {exc}")
            self._emit("child_nesting_refused", **refusal)
            return {}
        spawner = self.nested_spawner(brief.child_id)
        try:
            nested = spawner(declared)
        except DepthCapReached as exc:
            refusal = {
                "child_id": brief.child_id,
                "role": context.role or None,
                "code": "depth_cap_reached",
                "ceiling": "depth",
                "depth": context.depth,
                "max_depth": context.max_depth,
                "tasks": len(declared),
                "reason": str(exc),
            }
            with self._lock:
                self._record.nesting.setdefault("refused", []).append(refusal)
            result.notes.append(f"nesting refused: {exc}")
            self._emit("child_nesting_refused", **refusal)
            return {}
        payload = nested.as_dict()
        summary = {
            "run_id": payload.get("run_id"),
            "depth": payload.get("depth"),
            "status": payload.get("status"),
            "total": payload.get("children_total"),
            "delivered": payload.get("children_delivered"),
            "tokens": payload.get("spent_tokens"),
            "dir": str(Path(context.nested_dir) / str(payload.get("run_id"))),
            "results": [
                {
                    "child_id": child.get("child_id"),
                    "status": child.get("status"),
                    "answer": child.get("answer"),
                    "error": child.get("error"),
                    "stop_reason": child.get("stop_reason"),
                    "transcript": (child.get("context") or {}).get("transcript"),
                }
                for child in (payload.get("children") or [])
            ],
        }
        result.nested = summary
        result.nested_tokens = int(payload.get("spent_tokens") or 0)
        result.notes.append(
            f"spawned {summary['total']} child(ren) below the cap: run {summary['run_id']} "
            f"({summary['delivered']}/{summary['total']} delivered, {result.nested_tokens} "
            "estimated tokens, reported separately from this child's own budget)"
        )
        with self._lock:
            self._record.nesting.setdefault("spawned", []).append(
                {
                    "run_id": summary["run_id"],
                    "by": brief.child_id,
                    "depth": summary["depth"],
                    "total": summary["total"],
                    "delivered": summary["delivered"],
                    "dir": summary["dir"],
                }
            )
        self._emit(
            "child_nested_run",
            child_id=brief.child_id,
            nested_run_id=summary["run_id"],
            nested_depth=summary["depth"],
            nested_total=summary["total"],
            nested_delivered=summary["delivered"],
            nested_dir=summary["dir"],
        )
        return summary

    def _worker(self, child_id: str) -> None:
        t_started = time.monotonic()
        queued = False
        if self._sem is not None:
            queued = not self._sem.acquire(blocking=False)
            if queued:
                self._sem.acquire()
        t_running = time.monotonic()
        with self._lock:
            self._live[child_id] = STATUS_RUNNING
            self._child_started[child_id] = t_running
            self._running_now += 1
            self._running_now_max = max(self._running_now_max, self._running_now)
        brief = self._briefs[child_id]
        context = self._contexts[child_id]
        result = ChildResult(
            child_id=child_id,
            task=brief.task,
            tier=brief.tier,
            status=STATUS_RUNNING,
            budget_tokens=brief.budget_tokens,
            max_turns=brief.max_turns,
            context=context.as_dict(),
            spawn_ms=_round_ms(t_started - self._t0),
            queued_ms=_round_ms(t_running - t_started),
            queued=queued,
            started_at=_iso(),
        )
        try:
            if self._stop_flags[child_id].is_set():
                result.status = STATUS_CANCELLED
                result.stop_reason = self._cancel_reason or "cancelled before the first turn"
            else:
                self._child_loop(brief, context, result)
        except Exception as exc:  # noqa: BLE001 — one child must never take the run down
            result.status = STATUS_FAILED
            result.error = f"{type(exc).__name__}: {exc}"
        finally:
            if self._sem is not None:
                self._sem.release()
            with self._lock:
                self._running_now = max(0, self._running_now - 1)
                result.finished_at = _iso()
                result.duration_ms = _round_ms(time.monotonic() - t_running)
                result.overhead_ms = round(max(0.0, result.duration_ms - result.provider_ms), 3)
                result.turns = len(result.turn_log)
                result.thread_alive = False
                result.failure_class = failure_class_of(
                    result, run_cancel_reason=self._cancel_reason
                )
                result.finished_ms = _round_ms(time.monotonic() - self._t0)
                self._spend.setdefault(child_id, result.spent_tokens)
            self._store_result(result)

    def _child_loop(self, brief: ChildBrief, context: ChildContext, result: ChildResult) -> None:
        stop = self._stop_flags[brief.child_id]
        spent = 0
        nesting: dict = {}
        declared = nesting_tasks(brief.task)
        if declared:
            nesting = self._run_nested(brief, context, result, declared)
        while True:
            if stop.is_set():
                result.status = STATUS_CANCELLED
                result.stop_reason = self._cancel_reason or "stop requested"
                break
            if len(result.turn_log) >= brief.max_turns:
                result.status = STATUS_MAX_TURNS
                result.stop_reason = f"turn budget exhausted ({brief.max_turns} turns)"
                break
            turn_index = len(result.turn_log) + 1
            prompt = _build_prompt(
                brief,
                context,
                result.turn_log,
                context_chars=self._context_chars,
                nesting=nesting,
            )
            prompt_tokens = estimate_tokens(prompt)
            if spent + prompt_tokens > brief.budget_tokens:
                result.status = STATUS_BUDGET_EXHAUSTED
                result.stop_reason = (
                    f"token budget exhausted: {spent} spent + {prompt_tokens} for the next turn "
                    f"> {brief.budget_tokens} estimated tokens"
                )
                break
            prompt_id = f"subagent.{self.run_id}.{brief.child_id}.t{turn_index}"
            self._emit(
                "child_turn_started",
                child_id=brief.child_id,
                turn=turn_index,
                prompt_id=prompt_id,
                prompt_tokens=prompt_tokens,
            )
            started = time.monotonic()
            try:
                text = self._provider.complete(
                    prompt_id=prompt_id, prompt=prompt, tier=brief.tier
                )
            except Exception as exc:  # noqa: BLE001 — a provider failure is a child outcome
                call_ms = _round_ms(time.monotonic() - started)
                result.provider_ms = round(result.provider_ms + call_ms, 3)
                result.status = STATUS_FAILED
                result.error = f"{type(exc).__name__}: {exc}"
                result.stop_reason = "provider call raised"
                # The attempt is kept: it is what the child actually did, and its
                # prompt tokens are counted conservatively (the request may have
                # reached the provider even though the call raised).
                failure_turn = {
                    "turn": turn_index,
                    "prompt_id": prompt_id,
                    "prompt_tokens": prompt_tokens,
                    "response_tokens": 0,
                    "total_tokens": prompt_tokens,
                    "ms": call_ms,
                    "text": "",
                    "error": result.error,
                }
                result.turn_log.append(failure_turn)
                spent += prompt_tokens
                result.spent_tokens = spent
                self._note_spend(brief.child_id, spent)
                self._append_transcript(context, failure_turn)
                self._emit(
                    "child_failed",
                    child_id=brief.child_id,
                    turn=turn_index,
                    prompt_id=prompt_id,
                    error=result.error,
                    ms=call_ms,
                )
                break
            call_ms = _round_ms(time.monotonic() - started)
            result.provider_ms = round(result.provider_ms + call_ms, 3)
            response_tokens = estimate_tokens(text)
            spent += prompt_tokens + response_tokens
            result.spent_tokens = spent
            self._note_spend(brief.child_id, spent)
            turn = {
                "turn": turn_index,
                "prompt_id": prompt_id,
                "prompt_tokens": prompt_tokens,
                "response_tokens": response_tokens,
                "total_tokens": prompt_tokens + response_tokens,
                "ms": call_ms,
                "text": text,
            }
            # The child's own answer may carry tool directives. They are executed here — between
            # the model's turn and the record of it — through the child's role dispatcher, so
            # the run's record shows what really ran, and every refusal is handed back to the
            # child instead of being silently dropped.
            calls = child_tool_calls(text)
            if calls:
                turn["tool_calls"] = self._run_child_tool_calls(brief.child_id, calls)
                result.tool_calls.extend(turn["tool_calls"])
            result.turn_log.append(turn)
            self._append_transcript(context, turn)
            self._emit(
                "child_text",
                child_id=brief.child_id,
                turn=turn_index,
                prompt_id=prompt_id,
                ms=call_ms,
                tokens=turn["total_tokens"],
                text=text,
                tool_calls=[
                    {"tool": item.get("tool"), "executed": item.get("executed"),
                     "mode": item.get("mode")}
                    for item in (turn.get("tool_calls") or [])
                ],
            )
            # A turn that asked for tools is not a turn that finished: the directives are
            # stripped before the final marker is read, so "run this, then I am done" works in
            # one answer instead of costing the child an extra turn.
            answer = _final_answer(_without_tool_directives(text))
            if answer is not None:
                result.answer = answer
                result.status = STATUS_DELIVERED
                result.stop_reason = "final answer marker"
                break
            if spent >= brief.budget_tokens:
                result.status = STATUS_BUDGET_EXHAUSTED
                result.stop_reason = (
                    f"token budget exhausted: {spent} >= {brief.budget_tokens} estimated tokens"
                )
                break
        if result.status == STATUS_DELIVERED:
            result.evidence = [
                {
                    "kind": "turn",
                    "ref": result.turn_log[-1]["prompt_id"] if result.turn_log else "",
                    "note": (
                        f"final answer after {len(result.turn_log)} turn(s) "
                        f"on tier {brief.tier}"
                    ),
                },
                {
                    "kind": "file",
                    "ref": context.transcript,
                    "note": "this child's own transcript (JSONL, one record per turn)",
                },
                {
                    "kind": "file",
                    "ref": str(Path(context.workdir) / ".subagent.json"),
                    "note": "this child's private context (session id, budgets, workspace)",
                },
            ]
        else:
            result.evidence = [
                {
                    "kind": "run",
                    "ref": result.stop_reason or result.status,
                    "note": f"child ended as {result.status} after {len(result.turn_log)} turn(s)",
                },
                {
                    "kind": "file",
                    "ref": context.transcript,
                    "note": "the partial transcript this child actually produced",
                },
            ]
        self._emit(
            "child_finished",
            child_id=brief.child_id,
            status=result.status,
            turns=len(result.turn_log),
            spent_tokens=result.spent_tokens,
            answer=result.answer,
            error=result.error,
        )

    def _append_transcript(self, context: ChildContext, turn: dict) -> None:
        _append_line(
            Path(context.transcript),
            {
                "child_id": context.child_id,
                "session_id": context.session_id,
                "at": _iso(),
                **turn,
            },
        )

    def _run_child_tool_calls(self, child_id: str, calls: list[dict]) -> list[dict]:
        """Execute one turn's declared tool calls through the child's **role dispatcher**.

        The dispatcher — never the model, never this method — decides what a child may run:
        a tool the role does not hold raises :class:`ToolCeilingRefused`, which is recorded as
        the refusal it is (:meth:`_note_tool_refusal`) and handed back to the child as that
        turn's tool result. A granted tool with no handler comes back ``mode: declared`` /
        ``executed: false`` — reported, never invented. A directive whose arguments did not
        parse is not dispatched at all: the parse error goes back to the child.

        Returns one bounded outcome row per directive, in the order the child asked.
        """
        outcomes: list[dict] = []
        for call in calls:
            name = str(call.get("tool") or "")
            if call.get("error"):
                outcomes.append(
                    {
                        "tool": name,
                        "ok": False,
                        "executed": False,
                        "mode": "unparsed",
                        "error": str(call["error"])[:TOOL_DETAIL_CHARS],
                    }
                )
                continue
            try:
                produced = self.dispatch_tool(child_id, name, **dict(call.get("arguments") or {}))
            except ToolCeilingRefused as exc:
                outcomes.append(
                    {
                        "tool": name,
                        "ok": False,
                        "executed": False,
                        "mode": "refused",
                        "code": exc.code,
                        "error": exc.reason,
                    }
                )
                continue
            produced = produced if isinstance(produced, dict) else {"ok": bool(produced)}
            outcomes.append(
                {
                    "tool": name,
                    "ok": bool(produced.get("ok", True)),
                    "executed": bool(produced.get("executed", False)),
                    "mode": str(produced.get("mode") or ""),
                    "code": str(produced.get("code") or ""),
                    "error": str(produced.get("error") or "")[:TOOL_DETAIL_CHARS],
                    "result": _bounded_tool_result(produced),
                }
            )
        return outcomes

    def _store_result(self, result: ChildResult, *, preemptive: bool = False) -> None:
        with self._lock:
            if not preemptive and result.child_id in self._results:
                return  # a timeout/cancel already settled this child
            if not result.tool_refusals:
                result.tool_refusals = list(self._tool_refusals.get(result.child_id, []))
            if not result.role:
                brief = self._briefs.get(result.child_id)
                result.role = getattr(brief, "role", "") or ""
            if not result.tools:
                brief = self._briefs.get(result.child_id)
                result.tools = list(getattr(brief, "tools", ()) or ())
            self._results[result.child_id] = result
            self._live[result.child_id] = result.status
        self._snapshot()

    def _timeout_result(self, child_id: str) -> ChildResult:
        brief = self._briefs[child_id]
        context = self._contexts[child_id]
        turns = _read_turns(Path(context.transcript))
        result = ChildResult(
            child_id=child_id,
            task=brief.task,
            tier=brief.tier,
            status=STATUS_TIMED_OUT,
            turn_log=turns,
            turns=len(turns),
            budget_tokens=brief.budget_tokens,
            max_turns=brief.max_turns,
            context=context.as_dict(),
            spent_tokens=sum(int(turn.get("total_tokens", 0)) for turn in turns),
            stop_reason=(
                f"deadline {brief.timeout_s:g}s reached; the thread keeps its current provider "
                "call (a thread cannot be interrupted) and stops at its next turn boundary"
            ),
            thread_alive=True,
            started_at=_iso(),
            finished_at=_iso(),
            evidence=[
                {
                    "kind": "file",
                    "ref": context.transcript,
                    "note": "the partial transcript this child produced before its deadline",
                }
            ],
        )
        result.duration_ms = _round_ms(self._elapsed_for(child_id))
        result.failure_class = failure_class_of(result)
        return result

    def _cancelled_result(self, child_id: str) -> ChildResult:
        """Settle a child the collector could not wait for after a parent cancel.

        The grace deadline expired, so the child's own thread is still inside its
        provider call: the result is written from what really landed on disk and
        says ``thread_alive: true`` instead of pretending it stopped cleanly.
        """
        brief = self._briefs[child_id]
        context = self._contexts[child_id]
        turns = _read_turns(Path(context.transcript))
        with self._lock:
            reason = self._cancel_reason or "cancel requested"
        result = ChildResult(
            child_id=child_id,
            task=brief.task,
            tier=brief.tier,
            status=STATUS_CANCELLED,
            turn_log=turns,
            turns=len(turns),
            budget_tokens=brief.budget_tokens,
            max_turns=brief.max_turns,
            context=context.as_dict(),
            spent_tokens=sum(int(turn.get("total_tokens", 0)) for turn in turns),
            stop_reason=reason,
            thread_alive=True,
            started_at=_iso(),
            finished_at=_iso(),
            evidence=[
                {
                    "kind": "file",
                    "ref": context.transcript,
                    "note": "the partial transcript this child produced before the cancel",
                }
            ],
        )
        result.duration_ms = _round_ms(self._elapsed_for(child_id))
        result.failure_class = failure_class_of(result, run_cancel_reason=reason)
        return result

    def _finish(self) -> None:
        with self._lock:
            self._stopping = True
            self._record_dict()  # recompute children + timing from what really landed
            cancel_reason = self._cancel_reason
            self._record.queue_max = self._running_now_max
        timed_out = [child for child in self._results.values() if child.thread_alive]
        if cancel_reason:
            self._record.status = RUN_CANCELLED
            self._record.cancel_reason = cancel_reason
        else:
            self._record.status = RUN_COMPLETE
        self._record.finished_at = _iso()
        self._record.duration_ms = _round_ms(time.monotonic() - self._t0)
        if self._record.partial:
            self._record.notes.append(
                "not every child delivered ("
                + ", ".join(
                    f"{child.child_id}={child.failure_class or child.status}"
                    for child in self._record.children
                    if not child.ok
                )
                + ") — the other children's results are unaffected"
            )
        spawned = list(self._record.nesting.get("spawned") or [])
        refused = list(self._record.nesting.get("refused") or [])
        if spawned:
            self._record.notes.append(
                "nested runs below the depth cap: "
                + ", ".join(
                    f"{item['run_id']} by {item['by']} at depth {item['depth']}" for item in spawned
                )
                + f" (cap {self.max_depth})"
            )
        if refused:
            self._record.notes.append(
                f"nesting refused {len(refused)} time(s) at the cap (depth {self.max_depth}): "
                + "; ".join(str(item.get("reason")) for item in refused)
                + " — the child kept working, nothing was spawned"
            )
        if self._record.retried_from:
            self._record.notes.append(
                f"this run re-runs the unfinished children of {self._record.retried_from}"
            )
        if timed_out:
            self._record.notes.append(
                "a timed-out child's thread is still alive in this process; it stops at its next "
                "turn boundary and its result is already recorded"
            )
        if self._record.provider.get("mode") == "stub":
            self._record.notes.append(
                "provider mode 'stub': no model was called — the answers are deterministic "
                "stub text"
            )
        if self._io_errors:
            self._record.notes.append(
                "status writes failed during the run (the children were unaffected): "
                + "; ".join(self._io_errors)
            )
        self._emit("run_finished", status=self._record.status, delivered=self._record.delivered,
                   total=len(self._order), cancel_reason=cancel_reason or None)
        self._snapshot()


def _read_turns(path: Path) -> list[dict]:
    """A child's own transcript, read back from disk (also while it is still writing)."""
    turns: list[dict] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return turns
    for line in raw.splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            record = json.loads(text)
        except ValueError:
            continue
        if isinstance(record, dict):
            turns.append(record)
    return turns


def _final_answer(text: str) -> str | None:
    """The child's answer, or ``None`` while it is still working."""
    body = str(text or "")
    index = body.find(FINAL_MARKER)
    if index < 0:
        return None
    return body[index + len(FINAL_MARKER):].strip()


def spawn(
    tasks: Sequence[str],
    *,
    provider: Any,
    state_dir: str | os.PathLike[str],
    run_id: str = "",
    tier: str = "small",
    max_turns: int = DEFAULT_MAX_TURNS,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    parallel: int = 0,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    run_budget_tokens: int = 0,
    on_event: Callable[[dict], None] | None = None,
    workdir_base: str = "",
    context_chars: int = DEFAULT_CONTEXT_CHARS,
    provider_summary: dict | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    retried_from: str = "",
    stop_grace_s: float = DEFAULT_STOP_GRACE_S,
    role: str | None = None,
    kind: str = "",
) -> RunRecord:
    """Spawn N children in parallel on one shared provider and collect the family.

    ``role`` (``explore``/``worker``/``verifier``/``main``) gives every child the
    same tool ceiling; the default resolves to the canonical role for ``kind``
    (``worker`` unless the kind says otherwise), and ``"unroled"`` is the explicit
    request for the historical ceiling-free child.
    """
    briefs = briefs_from_tasks(
        tasks,
        tier=tier,
        max_turns=max_turns,
        budget_tokens=budget_tokens,
        timeout_s=timeout_s,
        role=role,
        kind=kind,
    )
    run = SubagentRun(
        provider=provider,
        briefs=briefs,
        state_dir=state_dir,
        run_id=run_id,
        parallel=parallel,
        run_budget_tokens=run_budget_tokens,
        on_event=on_event,
        workdir_base=workdir_base,
        context_chars=context_chars,
        provider_summary=provider_summary,
        max_depth=max_depth,
        retried_from=retried_from,
        stop_grace_s=stop_grace_s,
    )
    return run.run()


# --------------------------------------------------------------------------- #
# the durable side: what another process reads, watches and cancels
# --------------------------------------------------------------------------- #


@dataclass
class CancelOutcome:
    """What ``agent cancel`` actually did, including when there was nothing to do."""

    run_id: str
    action: str = "noop"  # signalled | noop | reconciled
    status: str = ""
    reason: str = ""
    pid: int = 0
    pid_alive: bool = False
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "action": self.action,
            "status": self.status,
            "reason": self.reason,
            "pid": self.pid or None,
            "pid_alive": self.pid_alive,
            "note": self.note,
        }


def _pid_alive(pid: int) -> bool:
    if int(pid) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RunStore:
    """Read/watch/cancel runs from any process — the CLI's view of the state dir."""

    def __init__(self, state_dir: str | os.PathLike[str]) -> None:
        self.root = Path(state_dir).expanduser()

    def run_dir(self, run_id: str) -> Path:
        return self.root / _safe_id(run_id)

    def load(self, run_id: str) -> dict | None:
        """The run's snapshot, or ``None`` when the id is unknown/unreadable."""
        return _read_json(self.run_dir(run_id) / RUN_FILE)

    def list(self, *, limit: int = 20, status: str = "") -> list[dict]:
        """Runs newest first (by ``started_at``), one summary record per run."""
        if not self.root.exists():
            return []
        runs: list[dict] = []
        for path in sorted(self.root.glob(f"*/{RUN_FILE}")):
            record = _read_json(path)
            if record is None:
                continue
            if status and str(record.get("status") or "") != status:
                continue
            children = list(record.get("children") or [])
            runs.append(
                {
                    "run_id": record.get("run_id") or path.parent.name,
                    "status": record.get("status"),
                    "started_at": record.get("started_at"),
                    "finished_at": record.get("finished_at"),
                    "duration_ms": record.get("duration_ms"),
                    "children_total": len(children),
                    "children_delivered": sum(1 for child in children if child.get("ok")),
                    "failure_classes": record.get("failure_classes") or {},
                    "parallel": record.get("parallel"),
                    "queue_max": record.get("queue_max"),
                    "depth": record.get("depth"),
                    "max_depth": record.get("max_depth"),
                    "retried_from": record.get("retried_from"),
                    "provider": (record.get("provider") or {}).get("mode"),
                    "cancel_reason": record.get("cancel_reason"),
                    "path": str(path.parent),
                }
            )
        runs.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
        return runs[: max(0, int(limit))]

    def transcript_path(self, run_id: str, child_id: str) -> Path:
        """Where a child's own transcript lives — the addressable evidence."""
        return (
            self.run_dir(run_id)
            / "children"
            / _safe_id(child_id, what="child id")
            / "transcript.jsonl"
        )

    def transcript(self, run_id: str, child_id: str, *, limit: int = 0) -> list[dict]:
        """One child's transcript, read from disk — also while it is still writing.

        This is the *live* reader: a running child appends and flushes each turn,
        so a second process can follow a child's output before the run finishes.
        """
        turns = _read_turns(self.transcript_path(run_id, child_id))
        if limit > 0:
            turns = turns[-int(limit):]
        return turns

    def children_of(self, run_id: str) -> list[str]:
        """The child ids that have a transcript on disk for this run."""
        root = self.run_dir(run_id) / "children"
        try:
            return sorted(path.name for path in root.iterdir() if path.is_dir())
        except (FileNotFoundError, OSError):
            return []

    def events(self, run_id: str, *, child_id: str = "", limit: int = 50) -> list[dict]:
        """The last ``limit`` streamed events (optionally one child's), oldest first."""
        path = self.run_dir(run_id) / EVENTS_FILE
        try:
            raw = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return []
        events: list[dict] = []
        for line in raw.splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            if child_id and record.get("child_id") != child_id:
                continue
            events.append(record)
        if limit > 0:
            events = events[-int(limit):]
        return events

    def wait(self, run_id: str, *, timeout_s: float = 60.0, poll_s: float = 0.05) -> dict | None:
        """Block until the run is terminal (or the wait deadline hits) — the collector's wait."""
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            record = self.load(run_id)
            if record is None:
                return None
            if str(record.get("status")) != RUN_RUNNING:
                return record
            if time.monotonic() >= deadline:
                return record
            time.sleep(max(0.005, float(poll_s)))

    def request_cancel(self, run_id: str, *, reason: str = "", by: str = "") -> CancelOutcome:
        """Cancel a live run, or say honestly why there was nothing to cancel.

        * **signalled** — the run record says ``running`` and its pid is alive: the
          request file is written and the live run stops its children at their next
          turn boundary.
        * **noop** — the run is already terminal. Its result is untouched.
        * **reconciled** — the record says ``running`` but the process is gone: an
          in-process child died with its process, so nothing can be cancelled. The
          record is rewritten as ``aborted`` (this is the only writer of a foreign
          run record, and only for a dead pid).
        """
        path = self.run_dir(run_id) / RUN_FILE
        record = _read_json(path)
        if record is None:
            return CancelOutcome(run_id=run_id, action="noop", status="unknown",
                                 note=f"no run record under {path.parent}")
        status = str(record.get("status") or "")
        pid = int(record.get("pid") or 0)
        alive = _pid_alive(pid)
        if status != RUN_RUNNING:
            return CancelOutcome(
                run_id=run_id, action="noop", status=status, pid=pid, pid_alive=alive,
                note="the run is already terminal — nothing was cancelled",
            )
        if not alive:
            record["status"] = RUN_ABORTED
            record["cancel_reason"] = (
                str(reason or "cancel requested") + f" (reconciled: pid {pid} is gone)"
            )
            record["finished_at"] = record.get("finished_at") or _iso()
            notes = list(record.get("notes") or [])
            notes.append(
                "reconciled by `agent cancel`: the run's process is gone, so its in-process "
                "child threads are gone too; queued children never started"
            )
            record["notes"] = notes
            _write_json_atomic(path, record)
            return CancelOutcome(
                run_id=run_id, action="reconciled", status=RUN_ABORTED, pid=pid, pid_alive=False,
                reason=str(reason or "cancel requested"),
                note=(
                    "the run's process is gone — recorded as aborted instead of pretending "
                    "a cancel"
                ),
            )
        _write_json_atomic(
            self.run_dir(run_id) / CANCEL_FILE,
            {"run_id": run_id, "reason": str(reason or "cancel requested"), "by": str(by),
             "at": _iso(), "pid": os.getpid()},
        )
        return CancelOutcome(
            run_id=run_id, action="signalled", status=status, pid=pid, pid_alive=True,
            reason=str(reason or "cancel requested"),
            note="cancel requested; outstanding children stop at their next turn boundary",
        )
