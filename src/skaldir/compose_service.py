"""Learned composite tools (COMPOSE): a named, validated chain of steps.

A **composite tool** is one reusable capability built from steps that already
exist. Each step is one of four kinds — a normal (registry) tool, a Docker tool,
an API call or a Computer-Use sub-tool — and every step declares its own
inputs, outputs, precondition and end-state check. A composite is only callable
after it validates, and ``run_composite`` really executes it through the
runtime's existing runners. A kind that has no runner today is **refused**
(``status="blocked"``, the missing kind named) — never faked.

Where a composite lives (two records, one definition)
-----------------------------------------------------
* **The definition** is one JSON document: ``<runtime>/composites/<name>.json``.
  It is the composite itself (steps, declared I/O, checks, version).
* **The lifecycle** is the registry row in ``tools.db``
  (``ToolSpec.kind == "composition"``) — the same gate the tool layer already
  uses (``candidate`` → ``scanned`` → ``approved`` → ``revoked``). A composite
  is **runnable only while its registry row is approved**, and
  :func:`run_composite` refuses anything else instead of pretending.

The registry row mirrors the chain (``steps``) so ``compose candidates`` and the
registry view see it; :func:`document_digest` lets a caller detect drift between
the two records (reported as a warning, never silently repaired).

The check grammar (no new dependency, no eval)
----------------------------------------------
Every ``precondition``/``end_state`` is a text in this small language:

* ``always`` / ``never``                  — constant
* ``exit_ok``                             — the step's own runner reported success
  (in a *precondition* of the first step this is ``True``; for a later step it is
  the **previous** step's verdict)
* ``$input.<name> present`` / ``empty``
* ``$steps.<id>.<key> present`` / ``empty``
* ``<ref> == <value>`` and ``!=`` ``>`` ``<`` ``>=`` ``<=`` ``contains``

Clauses joined by `` and `` must all hold. ``<ref>`` is ``$input.<name>`` or
``$steps.<id>.<key>``; ``<value>`` is JSON when it parses (``5``, ``"x"``,
``true``), else the literal text. An unknown reference or an unparsable clause is
a **blocking validation problem**, never a silently-true check.

Backends this module uses (it implements no runner of its own)
--------------------------------------------------------------
==========================  ==================================================
kind                        runner
==========================  ==================================================
``tool``                    ``mcpserver._wrapper_for`` — approved python spec,
                            generated tools in the bwrap sandbox, usage ledger
``docker``                  ``docker_runtime.DockerRuntimeManager.run`` — one
                            shot ``--rm``, owner-labelled, ``--network none``
``api``                     *none wired* (reported, not faked)
``gui``                     *none wired* (reported, not faked)
==========================  ==================================================

How a step gets its values
--------------------------
* A **tool** step's ``inputs`` map the tool's keyword arguments; each value is a
  ``$input.<name>`` / ``$steps.<id>.<key>`` reference or a literal.
* A **docker** step binds through its ``argv``: every token may carry
  ``$input.<name>`` / ``$steps.<id>.<key>`` references (inline too, e.g.
  ``"echo $input.text"``) and they are substituted before the container starts.
  Its ``inputs`` must therefore stay empty — a second, silently-ignored binding
  path is exactly how a step ends up running with an unbound value. The step
  needs a **pinned** ``image_digest``: the docker policy refuses a mutable tag
  (``policy_problems()`` -> ``pin-required``), and that refusal is a blocking
  validation problem, not a run-time surprise.
* An **api** step declares ``url`` (+``method``) and **gui** step a sub-tool
  ``target``; both are validated and neither has a runner today.

Hand-over (files this layer must not edit)
------------------------------------------
Three things belong to the CLI owner and are reported, never patched in here:

1. ``src/skaldir/cli.py``: ``from .compose_cli import register_cli as
   _register_compose_cli`` + ``_register_compose_cli(sub)`` right after
   ``_register_jev_cli(sub)`` (~line 2000) and **before** the final
   ``cli_contract.annotate(parser)`` (~line 2588).
2. ``src/skaldir/cli_contract.py``: the five ``COMMANDS`` entries
   (``compose.candidates/create/validate/run/show``).
3. ``src/skaldir/cli_contract.py::_probe_compose`` (~lines 1400-1424): its
   ``compose_lifecycle`` probe still returns the hardcoded verdict "no composite
   can be created, validated or run from the CLI yet", and the function's last
   line returns ``False`` regardless of its probes.

The exact text of all three is in ``tests/test_compose_cli.py``
(``COMPOSE_COMMANDS``, ``probe_compose_replacement``), where tests prove the
entries close the dump invariant and the probe reports the layer ``reachable``
with the two kinds that have no runner still named.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .tools import (
    KIND_COMPOSITION,
    KIND_PYTHON,
    STATUS_APPROVED,
    STATUS_CANDIDATE,
    STATUS_REVOKED,
    ToolSpec,
    ToolStore,
    compose_candidates_scored,
)

try:  # the product CLI's exit-code vocabulary wins; this module also runs standalone
    from . import cli_contract as _CONTRACT
except Exception:  # noqa: BLE001 — an older build without the contract module
    _CONTRACT = None

EXIT_OK = int(getattr(_CONTRACT, "EXIT_OK", 0))
EXIT_USAGE = int(getattr(_CONTRACT, "EXIT_USAGE", 1))
EXIT_VERIFICATION_FAILED = int(getattr(_CONTRACT, "EXIT_VERIFICATION_FAILED", 2))
EXIT_BLOCKED = int(getattr(_CONTRACT, "EXIT_BLOCKED", 3))
EXIT_NOT_FOUND = int(getattr(_CONTRACT, "EXIT_NOT_FOUND", 5))
EXIT_CONFLICT = int(getattr(_CONTRACT, "EXIT_CONFLICT", 6))

#: Directory (inside the runtime dir) holding the composite definitions.
COMPOSITES_DIR = "composites"

#: Same name rule the tool layer uses for a drafted spec (tools.py:584).
NAME_RE = re.compile(r"[a-z][a-z0-9_]{2,39}")

#: A step id: no dots (``$steps.<id>.<key>`` must stay unambiguous).
STEP_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")

STEP_KINDS: tuple[str, ...] = ("tool", "docker", "api", "gui")

#: Dockers step result keys (the one-shot runner's own verdict fields).
DOCKER_OUTPUT_KEYS: tuple[str, ...] = ("ok", "exit_code", "stdout", "stderr")

_MAX_STEPS = 25


class CompositeError(ValueError):
    """A composite document that cannot be read at all (shape/type level)."""


class CheckError(ValueError):
    """A ``precondition``/``end_state`` text this grammar cannot parse."""


# --------------------------------------------------------------------------- #
# which step kinds really have a runner                                         #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class KindExecutor:
    """The runner behind one step kind — or the reason there is none.

    ``implemented`` is *structural*: a runner is wired for the kind, so the kind can be
    declared and ``run_composite`` will dispatch to it. ``available()`` is *this host's*
    answer from the runner's own probe (an importable dependency, a reachable bridge): a
    wired runner that cannot run here is not a validation problem — the step runs, is
    **refused by name** (``missing_kind`` + the precise missing capability) and the
    composite exits 3. Folding the two together would turn a per-host gap into a
    permanent "no runner", which is exactly the confusion the wiring removed.
    """

    kind: str
    runner: str
    reason: str
    probe: Callable[[], tuple[bool, str]] | None = None

    @property
    def implemented(self) -> bool:
        return bool(self.runner) and not self.reason

    def available(self) -> tuple[bool, str]:
        """``(can this host run the kind, the honest detail)`` — never raises."""
        if self.probe is None:
            return bool(self.implemented), ("" if self.implemented else self.reason)
        if not self.implemented:
            return False, self.reason
        try:
            ok, detail = self.probe()
        except Exception as exc:  # noqa: BLE001 — a broken probe is an unavailable kind
            return False, (
                f"the availability probe for {self.kind!r} failed: {type(exc).__name__}: {exc}"
            )
        return bool(ok), str(detail)

    def to_dict(self) -> dict:
        ok, detail = self.available()
        return {
            "kind": self.kind,
            "executable": self.implemented,
            "runner": self.runner,
            "reason": self.reason,
            "available": bool(ok),
            "available_detail": detail,
        }


def _step_runner_probe(kind: str) -> tuple[bool, str]:
    """Ask ``skaldir.step_runners`` whether this host can really run ``kind``.

    Imported lazily: the step-runners module pulls the ACT/HTTP layers, and the
    COMPOSE layer must stay importable without them (a missing layer is then an
    honest "unavailable", not an ImportError out of the executor table).
    """
    try:
        from . import step_runners
    except Exception as exc:  # noqa: BLE001 — an unimportable runner module is a real gap
        return False, (
            f"skaldir.step_runners could not be imported: {type(exc).__name__}: {exc}"
        )
    runner = None
    try:
        runner = step_runners.runner_for(kind)
    except Exception as exc:  # noqa: BLE001 — a broken registry must not become a pass
        return False, f"the step-runner registry failed for {kind!r}: {type(exc).__name__}: {exc}"
    if runner is None:
        return False, f"no runner is registered for {kind!r} in skaldir.step_runners"
    try:
        return runner.available()
    except Exception as exc:  # noqa: BLE001 — the probe itself failing is not availability
        return False, f"the {kind!r} runner probe failed: {type(exc).__name__}: {exc}"


_EXECUTORS: dict[str, KindExecutor] = {
    "tool": KindExecutor(
        "tool",
        "skaldir.mcpserver._wrapper_for (approved python spec; generated tools run in the "
        "bwrap sandbox, usage recorded in the tool-usage ledger)",
        "",
    ),
    "docker": KindExecutor(
        "docker",
        "skaldir.docker_runtime.DockerRuntimeManager.run (one-shot `--rm`, owner-labelled, "
        "`--network none` unless the step asks otherwise)",
        "",
    ),
    "api": KindExecutor(
        "api",
        "skaldir.step_runners.run_api_step (httpx>=0.27; the declared request goes out and "
        "the declared status/body assertions are checked against the real response)",
        "",
        probe=lambda: _step_runner_probe("api"),
    ),
    "gui": KindExecutor(
        "gui",
        "skaldir.step_runners.run_gui_step -> skaldir.act_service.run_action_list / "
        "run_subtool (a real isolated bridge: the container GUI display or the bwrap "
        "sandbox; the end state is read back from the display, never from the action's "
        "stdout)",
        "",
        probe=lambda: _step_runner_probe("gui"),
    ),
}


def executor_for(kind: str) -> KindExecutor | None:
    """The executor of one step kind, or ``None`` for an unknown kind."""
    return _EXECUTORS.get(str(kind or "").strip().lower())


def executor_report() -> dict[str, dict]:
    """Every step kind with its runner (or its honest reason) — machine readable."""
    return {kind: _EXECUTORS[kind].to_dict() for kind in STEP_KINDS}


def missing_kinds(kinds: Sequence[str]) -> list[str]:
    """The kinds without a runner, in first-seen order (each named once)."""
    out: list[str] = []
    for kind in kinds:
        item = executor_for(kind)
        if item is None or not item.implemented:
            name = item.kind if item is not None else str(kind)
            if name not in out:
                out.append(name)
    return out


def kind_reason(kind: str) -> str:
    """Why this kind cannot run here — the honest sentence ``compose run`` reports."""
    item = executor_for(kind)
    if item is None:
        return f"unknown step kind {kind!r} (known: {list(STEP_KINDS)})"
    if not item.implemented:
        return item.reason or f"no runner is wired for {kind!r}"
    ok, detail = item.available()
    if not ok:
        return detail or f"the {kind!r} runner is not available on this host"
    return "a runner exists"


# --------------------------------------------------------------------------- #
# the check grammar                                                             #
# --------------------------------------------------------------------------- #

_OPS: tuple[str, ...] = ("==", "!=", ">=", "<=", ">", "<", "contains")
_REF_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+")
_CONSTANTS = ("always", "never", "exit_ok")


@dataclass(frozen=True)
class Clause:
    ref: str  # "" for a constant clause
    op: str  # one of _OPS, "present", "empty", or the constant itself
    value: Any = None
    value_ref: str = ""  # the right-hand side is itself a $ref (chained comparison)

    def to_dict(self) -> dict:
        return {"ref": self.ref, "op": self.op, "value": self.value,
                "value_ref": self.value_ref}


@dataclass(frozen=True)
class Check:
    """One parsed precondition/end-state text."""

    text: str
    clauses: tuple[Clause, ...]

    @property
    def refs(self) -> list[str]:
        """Every reference the check makes — both sides of a comparison."""
        out: list[str] = []
        for clause in self.clauses:
            for ref in (clause.ref, clause.value_ref):
                if ref and ref not in out:
                    out.append(ref)
        return out

    def to_dict(self) -> dict:
        return {"text": self.text, "clauses": [c.to_dict() for c in self.clauses]}


def _literal(text: str) -> Any:
    raw = str(text).strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _parse_clause(text: str) -> Clause:
    raw = str(text or "").strip()
    if not raw:
        raise CheckError("empty clause")
    lowered = raw.lower()
    if lowered in _CONSTANTS:
        return Clause(ref="", op=lowered)
    match = _REF_RE.match(raw)
    if match is None:
        raise CheckError(f"clause {raw!r} starts with no $input./$steps. reference")
    ref = match.group(0)
    rest = raw[match.end() :].strip()
    if rest in ("present", "empty"):
        return Clause(ref=ref, op=rest)
    for op in _OPS:
        if rest.startswith(op):
            value = rest[len(op) :].strip()
            if not value:
                raise CheckError(f"clause {raw!r}: operator {op!r} without a value")
            if _REF_RE.fullmatch(value):
                # `$steps.a.key contains $steps.b.key` — the comparison value is resolved
                # from the same context, so a chain can assert against a *previous* step's
                # real output instead of a hard-coded literal.
                return Clause(ref=ref, op=op, value_ref=value)
            return Clause(ref=ref, op=op, value=_literal(value))
    raise CheckError(
        f"clause {raw!r}: expected one of present/empty/{'/'.join(_OPS)} after {ref}"
    )


def parse_check(text: str) -> Check:
    """Parse one check text. Raises :class:`CheckError` (never silently drops)."""
    raw = str(text or "").strip()
    if not raw:
        raise CheckError("empty check")
    clauses: list[Clause] = []
    for part in raw.split(" and "):
        clause = _parse_clause(part)
        if clause.ref == "" and clause.op in ("always", "never") and len(raw.split(" and ")) > 1:
            raise CheckError(f"check {raw!r}: {clause.op!r} cannot be combined with ` and `")
        clauses.append(clause)
    return Check(text=raw, clauses=tuple(clauses))


def _dig(context: Mapping[str, Any], ref: str) -> tuple[bool, Any]:
    """Resolve one ``$input.x`` / ``$steps.id.key`` reference."""
    parts = str(ref).lstrip("$").split(".")
    if len(parts) < 2 or parts[0] not in ("input", "steps"):
        return False, f"reference {ref!r} is not $input.<name> or $steps.<id>.<key>"
    node: Any = context.get(parts[0])
    for index, part in enumerate(parts[1:], start=1):
        if isinstance(node, Mapping) and part in node:
            node = node[part]
            continue
        where = "input" if parts[0] == "input" else f"step {parts[1]!r}"
        missing = ".".join(parts[1 : index + 1])
        return False, f"{where} has no {missing!r}"
    return True, node


def _compare(left: Any, op: str, right: Any) -> tuple[bool, str]:
    if op in ("present", "empty"):
        present = left is not None and left != "" and left != [] and left != {}
        ok = present if op == "present" else not present
        return ok, f"{op} -> {left!r} is {'present' if present else 'absent'}"
    if op == "contains":
        try:
            ok = right in left
        except TypeError:
            return False, f"{left!r} contains {right!r} is not decidable"
        return bool(ok), f"{left!r} contains {right!r} -> {bool(ok)}"
    if op == "==":
        return left == right, f"{left!r} == {right!r} -> {left == right}"
    if op == "!=":
        return left != right, f"{left!r} != {right!r} -> {left != right}"
    try:
        left_n, right_n = float(left), float(right)
    except (TypeError, ValueError):
        return False, f"{left!r} {op} {right!r} needs two numbers"
    outcome = {
        ">": left_n > right_n,
        "<": left_n < right_n,
        ">=": left_n >= right_n,
        "<=": left_n <= right_n,
    }[op]
    return outcome, f"{left_n} {op} {right_n} -> {outcome}"


def evaluate_check(check: Check, context: Mapping[str, Any]) -> tuple[bool, str]:
    """Evaluate a parsed check against ``{"input":…, "steps":…, "exit_ok": bool}``."""
    facts: list[str] = []
    for clause in check.clauses:
        if clause.ref == "":
            if clause.op == "always":
                facts.append("always")
                continue
            if clause.op == "never":
                return False, "never"
            ok = bool(context.get("exit_ok", False))
            facts.append(f"exit_ok -> {ok}")
            if not ok:
                return False, " and ".join(facts)
            continue
        found, value = _dig(context, clause.ref)
        if not found:
            return False, str(value)
        expected = clause.value
        if clause.value_ref:
            resolved, expected = _dig(context, clause.value_ref)
            if not resolved:
                return False, str(expected)
        ok, detail = _compare(value, clause.op, expected)
        extra = f" vs {clause.value_ref}" if clause.value_ref else ""
        facts.append(f"{clause.ref}{extra}: {detail}")
        if not ok:
            return False, " and ".join(facts)
    return True, " and ".join(facts) or "always"


# --------------------------------------------------------------------------- #
# the model                                                                     #
# --------------------------------------------------------------------------- #

_STEP_KEYS = {
    "id",
    "kind",
    "target",
    "tool",
    "image",
    "image_digest",
    "argv",
    "env",
    "url",
    "method",
    "inputs",
    "outputs",
    "precondition",
    "end_state",
    "timeout_s",
    "network",
    "note",
}


@dataclass
class StepSpec:
    """One step of a composite: a kind, a target, declared I/O and two checks."""

    id: str
    kind: str
    target: str = ""
    image: str = ""
    image_digest: str = ""
    argv: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    method: str = "GET"
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: list[str] = field(default_factory=list)
    precondition: str = ""
    end_state: str = ""
    timeout_s: float = 60.0
    network: str = "none"
    note: str = ""

    def to_dict(self) -> dict:
        payload: dict[str, Any] = {"id": self.id, "kind": self.kind}
        if self.target:
            payload["target"] = self.target
            payload["tool"] = self.target  # registry spelling (tools.validate_composition)
        if self.image:
            payload["image"] = self.image
        if self.image_digest:
            payload["image_digest"] = self.image_digest
        if self.argv:
            payload["argv"] = list(self.argv)
        if self.env:
            payload["env"] = dict(self.env)
        if self.kind == "api":
            payload["url"] = self.url
            payload["method"] = self.method
        if self.inputs:
            payload["inputs"] = dict(self.inputs)
        payload["outputs"] = list(self.outputs)
        payload["precondition"] = self.precondition
        payload["end_state"] = self.end_state
        if self.timeout_s != 60.0:
            payload["timeout_s"] = float(self.timeout_s)
        if self.network != "none":
            payload["network"] = self.network
        if self.note:
            payload["note"] = self.note
        return payload

    @staticmethod
    def from_dict(payload: Mapping[str, Any]) -> StepSpec:
        unknown = sorted(set(payload) - _STEP_KEYS)
        if unknown:
            raise CompositeError(
                f"step has unknown field(s) {unknown} — allowed: {sorted(_STEP_KEYS)}"
            )
        step_id = str(payload.get("id", "") or "").strip()
        kind = str(payload.get("kind", "") or "").strip().lower()
        argv = payload.get("argv") or []
        if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
            raise CompositeError(f"step {step_id or '?'}: argv must be a list of strings")
        outputs = payload.get("outputs") or []
        if not isinstance(outputs, list) or any(not isinstance(item, str) for item in outputs):
            raise CompositeError(f"step {step_id or '?'}: outputs must be a list of strings")
        inputs = payload.get("inputs") or {}
        if not isinstance(inputs, Mapping):
            raise CompositeError(f"step {step_id or '?'}: inputs must be an object")
        env = payload.get("env") or {}
        if not isinstance(env, Mapping):
            raise CompositeError(f"step {step_id or '?'}: env must be an object")
        try:
            timeout = float(payload.get("timeout_s", 60.0) or 60.0)
        except (TypeError, ValueError) as exc:
            raise CompositeError(
                f"step {step_id or '?'}: timeout_s is not a number: {exc}"
            ) from exc
        return StepSpec(
            id=step_id,
            kind=kind,
            target=str(payload.get("target", payload.get("tool", "")) or "").strip(),
            image=str(payload.get("image", "") or "").strip(),
            image_digest=str(payload.get("image_digest", "") or "").strip(),
            argv=[str(item) for item in argv],
            env={str(k): str(v) for k, v in dict(env).items()},
            url=str(payload.get("url", "") or "").strip(),
            method=str(payload.get("method", "GET") or "GET").strip().upper(),
            inputs=dict(inputs),
            outputs=[str(item) for item in outputs],
            precondition=str(payload.get("precondition", "") or ""),
            end_state=str(payload.get("end_state", "") or ""),
            timeout_s=timeout,
            network=str(payload.get("network", "none") or "none").strip(),
            note=str(payload.get("note", "") or ""),
        )


_COMPOSITE_KEYS = {
    "name",
    "title",
    "description",
    "tags",
    "inputs",
    "outputs",
    "precondition",
    "end_state",
    "steps",
    "version",
    "rationale",
    "provenance",
    "status",
}


@dataclass
class CompositeSpec:
    """The composite definition: declared inputs/outputs, checks and the chain."""

    name: str
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    inputs: dict[str, dict] = field(default_factory=dict)
    outputs: list[str] = field(default_factory=list)
    precondition: str = "always"
    end_state: str = "always"
    steps: list[StepSpec] = field(default_factory=list)
    version: str = "0.1.0"
    rationale: str = ""
    provenance: str = "manual"

    def step(self, step_id: str) -> StepSpec | None:
        return next((item for item in self.steps if item.id == step_id), None)

    @property
    def kinds(self) -> list[str]:
        return [item.kind for item in self.steps]

    @property
    def required_inputs(self) -> list[str]:
        return [
            name
            for name, spec in self.inputs.items()
            if bool((spec or {}).get("required", True))
        ]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title or self.name,
            "description": self.description,
            "tags": list(self.tags),
            "inputs": {name: dict(spec) for name, spec in self.inputs.items()},
            "outputs": list(self.outputs),
            "precondition": self.precondition,
            "end_state": self.end_state,
            "steps": [item.to_dict() for item in self.steps],
            "version": self.version,
            "rationale": self.rationale,
            "provenance": self.provenance,
        }

    def to_spec(self, *, status: str = STATUS_CANDIDATE, scan_summary: str = "") -> ToolSpec:
        """The registry row of this composite (kind=``composition``, steps mirrored).

        ``sample_input`` carries the declared input *values* (defaults), which is
        exactly what the tool layer means by sample input; the typed declarations
        live in the definition document.
        """
        return ToolSpec(
            name=self.name,
            title=self.title or self.name,
            description=self.description,
            tags=list(self.tags),
            kind=KIND_COMPOSITION,
            module="",
            entrypoint="",
            steps=[item.to_dict() for item in self.steps],
            sample_input={
                name: (spec or {}).get("default", (spec or {}).get("sample", ""))
                for name, spec in self.inputs.items()
            },
            rationale=self.rationale,
            provenance=self.provenance or "compose",
            status=status,
            version=self.version,
            scan_summary=scan_summary,
        )


def _parse_inputs(raw: Any) -> dict[str, dict]:
    """Declared inputs: a list of names or a mapping name → {type, required, default}."""
    if raw in (None, {}, []):
        return {}
    if isinstance(raw, Mapping):
        out: dict[str, dict] = {}
        for name, spec in raw.items():
            key = str(name).strip()
            if not key:
                raise CompositeError("an input is declared with an empty name")
            if isinstance(spec, Mapping):
                declared = dict(spec)
                unknown = sorted(set(declared) - {"type", "required", "default", "sample", "note"})
                if unknown:
                    raise CompositeError(
                        f"input {key!r} has unknown field(s) {unknown} "
                        "— allowed: type, required, default, sample, note"
                    )
                out[key] = declared
            else:
                out[key] = {"type": "string", "required": True, "sample": spec}
        return out
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return {str(name).strip(): {"type": "string", "required": True} for name in raw}
    raise CompositeError("inputs must be an object or a list of names")


def parse_composite(payload: Mapping[str, Any]) -> CompositeSpec:
    """Read a composite document — strict: an unknown field is an error, not a typo."""
    if not isinstance(payload, Mapping):
        raise CompositeError("a composite document must be one JSON object")
    unknown = sorted(set(payload) - _COMPOSITE_KEYS)
    if unknown:
        raise CompositeError(
            f"unknown field(s) {unknown} — allowed: {sorted(_COMPOSITE_KEYS)}"
        )
    name = str(payload.get("name", "") or "").strip()
    if not name:
        raise CompositeError("the composite needs a name")
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list):
        raise CompositeError("steps must be a list")
    steps: list[StepSpec] = []
    for entry in raw_steps:
        if not isinstance(entry, Mapping):
            raise CompositeError("every step must be a JSON object")
        steps.append(StepSpec.from_dict(entry))
    tags = payload.get("tags") or []
    if not isinstance(tags, list):
        raise CompositeError("tags must be a list")
    outputs = payload.get("outputs") or []
    if not isinstance(outputs, list) or any(not isinstance(item, str) for item in outputs):
        raise CompositeError("outputs must be a list of strings")
    return CompositeSpec(
        name=name,
        title=str(payload.get("title", "") or ""),
        description=str(payload.get("description", "") or ""),
        tags=[str(tag) for tag in tags],
        inputs=_parse_inputs(payload.get("inputs")),
        outputs=[str(item) for item in outputs],
        precondition=str(payload.get("precondition", "always") or "always"),
        end_state=str(payload.get("end_state", "always") or "always"),
        steps=steps,
        version=str(payload.get("version", "0.1.0") or "0.1.0"),
        rationale=str(payload.get("rationale", "") or ""),
        provenance=str(payload.get("provenance", "manual") or "manual"),
    )


def composite_from_registry(spec: ToolSpec) -> CompositeSpec:
    """Read a composite definition back out of its registry row (mirror view)."""
    return CompositeSpec(
        name=spec.name,
        title=spec.title,
        description=spec.description,
        tags=list(spec.tags),
        inputs={name: {"type": "string", "required": True, "sample": value}
                for name, value in (spec.sample_input or {}).items()},
        outputs=[],
        steps=[StepSpec.from_dict(step) for step in spec.steps if isinstance(step, Mapping)],
        version=spec.version,
        rationale=spec.rationale,
        provenance=spec.provenance,
    )


def document_digest(payload: Mapping[str, Any]) -> str:
    """Stable sha256 of a composite document (drift check between the two records)."""
    blob = json.dumps(dict(payload), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# the definition files                                                          #
# --------------------------------------------------------------------------- #


def composites_dir(runtime: str | Path) -> Path:
    return Path(runtime) / COMPOSITES_DIR


def composite_path(runtime: str | Path, name: str) -> Path:
    return composites_dir(runtime) / f"{name}.json"


def save_composite(runtime: str | Path, comp: CompositeSpec) -> Path:
    """Write the definition document (tmp + replace: never a half-written file)."""
    target = composite_path(runtime, comp.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(comp.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(target)
    return target


def load_composite(runtime: str | Path, name: str) -> CompositeSpec | None:
    """Read one definition document; ``None`` when it does not exist."""
    path = composite_path(runtime, name)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompositeError(f"{path} is not readable JSON: {exc}") from exc
    return parse_composite(payload)


def composite_files(runtime: str | Path) -> list[Path]:
    directory = composites_dir(runtime)
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.json"))


# --------------------------------------------------------------------------- #
# validation                                                                    #
# --------------------------------------------------------------------------- #


@dataclass
class StepVerdict:
    id: str
    kind: str
    target: str
    declarable: bool  # kind understood
    executable: bool  # a runner is wired for the kind
    reason: str
    outputs: list[str]
    precondition: str
    end_state: str
    available: bool = True  # ...and this host's own probe says it can run it
    available_detail: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "target": self.target,
            "declarable": self.declarable,
            "executable": self.executable,
            "reason": self.reason,
            "available": self.available,
            "available_detail": self.available_detail,
            "outputs": list(self.outputs),
            "precondition": self.precondition,
            "end_state": self.end_state,
        }


@dataclass
class ValidationReport:
    """What ``compose validate`` reports: blocking problems, warnings, verdicts."""

    name: str
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    steps: list[StepVerdict] = field(default_factory=list)
    missing_kinds: list[str] = field(default_factory=list)
    checks_parsed: int = 0

    @property
    def ok(self) -> bool:
        """No blocking problem at all."""
        return not self.problems

    @property
    def executable(self) -> bool:
        """Structurally clean **and** every step kind has a runner."""
        return self.ok and not self.missing_kinds

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "executable": self.executable,
            "problems": list(self.problems),
            "problem_count": len(self.problems),
            "warnings": list(self.warnings),
            "warning_count": len(self.warnings),
            "steps": [item.to_dict() for item in self.steps],
            "missing_kinds": list(self.missing_kinds),
            "checks_parsed": self.checks_parsed,
            "executors": executor_report(),
        }


def _docker_policy_problems(step: StepSpec) -> list[str]:
    """The docker runtime's own allowlist verdict for a step's container config."""
    from .docker_runtime import POLICY_REASONS, PolicyViolation, RunConfig

    try:
        config = RunConfig(
            image=step.image,
            image_digest=step.image_digest,
            entrypoint_argv=list(step.argv),
            network=step.network or "none",
            labels={},
        )
    except PolicyViolation as exc:  # privileged / socket mount — refused at construction
        return [f"{step.id}: docker config refused by policy: {exc}"]
    return [
        f"{step.id}: docker config refused: {problem}"
        + (f" ({POLICY_REASONS[problem]})" if problem in POLICY_REASONS else "")
        for problem in config.policy_problems()
    ]


def _runner_output_keys(kind: str) -> tuple[str, ...]:
    """The result keys the runner for ``kind`` always carries (its own authority)."""
    try:
        from . import step_runners
    except Exception:  # noqa: BLE001 — no runner module, no opinion to add
        return ()
    try:
        return tuple(step_runners.output_keys_for(kind))
    except Exception:  # noqa: BLE001
        return ()


def _runner_declaration_problems(step: StepSpec, kind: str) -> list[str]:
    """Catch *before* a run what the step runner would refuse *at* run time.

    The runner is the authority: it refuses an unknown field so that a typo like
    ``expct_status`` cannot silently drop an assertion. Exposing the same verdict here is
    what makes ``compose validate`` worth running — the refusal is named with the field and
    the allowed set, and the run is never started.
    """
    try:
        from . import step_runners
    except Exception:  # noqa: BLE001 — the runner is missing; availability says so
        return []
    allowed = tuple(
        step_runners.API_ARG_KEYS if kind == "api" else step_runners.GUI_ARG_KEYS
    )
    unknown = sorted(str(name) for name in (step.inputs or {}) if str(name) not in allowed)
    if not unknown:
        return []
    return [
        f"step {step.id or '?'}: {kind} field(s) {unknown} are not declared by the {kind} "
        f"runner — allowed: {list(allowed)}"
    ]


def is_inline_gui_target(target: str) -> bool:
    """Is this gui step's ``target`` the literal marker for a declared inline flow?"""
    try:
        from . import step_runners

        markers = tuple(step_runners.GUI_INLINE_TARGETS)
    except Exception:  # noqa: BLE001 — the runner module carries the vocabulary
        markers = ("inline", "actions", "declare")
    return str(target or "").strip().lower() in markers


def _gui_declaration_problems(step: StepSpec, *, subtools: Any) -> list[str]:
    """The ``gui`` kind: an inline flow, or a stored sub-tool that really resolves.

    A gui step declares its flow in ``inputs`` (``actions`` + ``end_state``) with
    ``target: 'inline'``, or names a stored sub-tool in ``target``. Both paths are checked
    here; the *bridge* (whether this host can drive a display at all) is not a document
    problem — it is answered at run time by the runner's own ``blocked`` outcome, with the
    missing capability named.
    """
    problems = _runner_declaration_problems(step, "gui")
    target = str(step.target or "").strip()
    if not target:
        problems.append(
            f"step {step.id or '?'}: a gui step needs a target — 'inline' for a declared "
            "flow (inputs.actions + inputs.end_state), or a stored sub-tool id"
        )
        return problems
    if is_inline_gui_target(target):
        bound = dict(step.inputs or {})
        actions = bound.get("actions")
        if not isinstance(actions, (list, tuple)) or not actions:
            problems.append(
                f"step {step.id or '?'}: the inline gui flow declares no inputs.actions "
                "list — a flow with no action can never be verified"
            )
        end_state = bound.get("end_state")
        if not isinstance(end_state, Mapping):
            problems.append(
                f"step {step.id or '?'}: the inline gui flow declares no inputs.end_state "
                "object — the end state is read from the real display, and a flow without "
                "one cannot be verified"
            )
        elif not (
            end_state.get("expect_observe_contains") or end_state.get("expect_state_contains")
        ):
            problems.append(
                f"step {step.id or '?'}: the inline gui flow's end_state declares no "
                "expectation (expect_observe_contains / expect_state_contains)"
            )
        return problems
    if subtools is not None:
        try:
            subtools.resolve(target)
        except Exception as exc:  # noqa: BLE001 — the registry's own verdict
            problems.append(
                f"step {step.id or '?'}: sub-tool {target!r} is not registered: {exc}"
            )
    return problems


class SubToolRegistry:
    """The ACT sub-tool registry as the validator's ``resolve()`` port.

    A ``gui`` step may name a stored sub-tool; ``compose validate`` must then be able to say
    whether that name really exists (a saved sub-tool shadows a built-in). This adapter is the
    call site that makes the sub-tool registry reachable from COMPOSE — without it the
    validator could only warn about the name, and a typo would survive until run time.
    """

    def __init__(self, state: str | Path | None = None) -> None:
        self.state = state

    def resolve(self, name: str) -> dict:
        from .act_service import load_subtool

        spec, origin, path = load_subtool(str(name), state=self.state)
        return {"name": str(spec.get("name", name)), "origin": origin, "path": path}

    def names(self) -> list[str]:
        from .act_service import list_subtools

        out: list[str] = []
        for row in list_subtools(state=self.state):
            item = str(row.get("name", "") or "")
            if item:
                out.append(item)
        return sorted(out)


def validate_composite(
    store: ToolStore,
    comp: CompositeSpec,
    *,
    subtools: Any = None,
) -> ValidationReport:
    """Every blocking problem of a composite — an empty ``problems`` list means OK.

    Checked here (not at run time): the shape and the name rule, per-kind required
    fields, the registry side of every ``tool`` target (exists, python, approved,
    not revoked), the docker policy allowlist, one declared output per step, the
    declared check texts (parse + reference resolution), and the whole dataflow —
    a consumer may only read ``$input.*`` it declared or an **earlier** step's
    **declared** output.
    """
    report = ValidationReport(name=comp.name)
    if not NAME_RE.fullmatch(comp.name or ""):
        report.problems.append(
            f"name {comp.name!r} is not a valid tool name "
            "(lowercase letter, then [a-z0-9_], 3-40 chars)"
        )
    if not comp.steps:
        report.problems.append("the composite has no steps")
    if len(comp.steps) > _MAX_STEPS:
        report.problems.append(f"the composite has {len(comp.steps)} steps (max {_MAX_STEPS})")
    if not comp.version:
        report.problems.append("the composite declares no version")

    # ---- the chain ---------------------------------------------------------
    seen_ids: set[str] = set()
    for index, step in enumerate(comp.steps):
        where = f"step {index + 1} ({step.id or '?'})"
        if not step.id:
            report.problems.append(f"{where} has no id")
        elif not STEP_ID_RE.fullmatch(step.id):
            report.problems.append(f"{where}: id must match {STEP_ID_RE.pattern} (no dots)")
        elif step.id in seen_ids:
            report.problems.append(f"{where}: duplicate step id")
        seen_ids.add(step.id)
        if step.kind not in STEP_KINDS:
            report.problems.append(
                f"{where}: unknown kind {step.kind!r} — one of {list(STEP_KINDS)}"
            )
        if not step.outputs:
            report.problems.append(
                f"{where}: declares no outputs — an undeclared step cannot be verified"
            )
        elif len(set(step.outputs)) != len(step.outputs):
            report.problems.append(f"{where}: duplicate output name")
        for check_name, text in (
            ("precondition", step.precondition), ("end_state", step.end_state)
        ):
            if not str(text or "").strip():
                report.problems.append(
                    f"{where}: declares no {check_name} check "
                    "(`always` is the explicit no-op)"
                )

    # ---- per-kind required fields ------------------------------------------
    availability: dict[str, tuple[bool, str]] = {}

    def _availability(kind: str) -> tuple[bool, str]:
        """This host's own probe for one kind, asked once per validation.

        The GUI probe runs the ACT capability report, so it is not repeated per step.
        """
        if kind not in availability:
            item = executor_for(kind)
            availability[kind] = item.available() if item is not None else (False, "")
        return availability[kind]

    for step in comp.steps:
        where = f"step {step.id or '?'}"
        executor = executor_for(step.kind)
        if step.kind == "tool":
            if not step.target:
                report.problems.append(f"{where}: a tool step needs a target (the registry name)")
            else:
                target = store.get(step.target)
                if target is None:
                    report.problems.append(
                        f"{where}: unknown tool {step.target!r} in the registry"
                    )
                elif target.kind != KIND_PYTHON:
                    report.problems.append(
                        f"{where}: {step.target!r} is kind {target.kind!r}, a tool step needs "
                        f"kind {KIND_PYTHON!r}"
                    )
                elif target.status == STATUS_REVOKED:
                    report.problems.append(f"{where}: tool {step.target!r} is revoked")
                elif target.status != STATUS_APPROVED:
                    report.problems.append(
                        f"{where}: tool {step.target!r} is {target.status!r}, not approved — "
                        f"`skaldir tools approve {step.target}` first"
                    )
                elif not (target.module and target.entrypoint):
                    report.problems.append(
                        f"{where}: tool {step.target!r} carries no module/entrypoint to call"
                    )
        elif step.kind == "docker":
            if not (step.image or step.image_digest):
                report.problems.append(f"{where}: a docker step needs image or image_digest")
            else:
                report.problems.extend(_docker_policy_problems(step))
            if step.inputs:
                report.problems.append(
                    f"{where}: a docker step binds through its argv ($input./$steps. refs), "
                    "not through inputs — inputs must stay empty"
                )
        elif step.kind == "api":
            url = str(step.url or "").strip()
            if not url:
                report.problems.append(f"{where}: an api step needs a url")
            elif not url.startswith("$") and not url.startswith(("http://", "https://")):
                # a *parameterised* url ($input.url / $steps.<id>.<key>) is resolved by the
                # runner before the request goes out — that is what makes an api step a
                # reusable capability, so the literal http(s) rule applies only to literals.
                report.problems.append(f"{where}: url {url!r} is not http(s)")
            report.problems.extend(_runner_declaration_problems(step, "api"))
        elif step.kind == "gui":
            report.problems.extend(_gui_declaration_problems(step, subtools=subtools))
            target = str(step.target or "").strip()
            if subtools is None and target and not is_inline_gui_target(target):
                report.warnings.append(
                    f"{where}: sub-tool {target!r} cannot be resolved without a sub-tool "
                    "registry in this process"
                )
        report.steps.append(
            StepVerdict(
                id=step.id,
                kind=step.kind,
                target=step.target or step.image or step.url,
                declarable=step.kind in STEP_KINDS,
                executable=bool(executor is not None and executor.implemented),
                reason="" if (executor is not None and executor.implemented)
                else (executor.reason if executor else f"unknown kind {step.kind!r}"),
                available=_availability(step.kind)[0],
                available_detail=_availability(step.kind)[1],
                outputs=list(step.outputs),
                precondition=step.precondition,
                end_state=step.end_state,
            )
        )
    report.missing_kinds = missing_kinds(comp.kinds)

    # A kind that is wired but cannot run on this host right now is not a document problem:
    # the step is refused by name at run time (exit 3). Saying so here is what keeps
    # `compose validate` from implying a run will pass.
    for kind in dict.fromkeys(comp.kinds):
        item = executor_for(kind)
        if item is None or not item.implemented:
            continue
        ok, detail = _availability(kind)
        if not ok:
            report.warnings.append(
                f"step kind {kind!r} is wired but this host cannot run it right now: "
                f"{detail} — the step will be refused by name (exit 3), never faked"
            )

    # ---- declared checks ---------------------------------------------------
    step_index = {step.id: index for index, step in enumerate(comp.steps) if step.id}
    consumed: set[str] = set()

    def _check_refs(
        text: str,
        *,
        owner: str,
        allow_steps: bool,
        owner_index: int | None,
        allow_self: bool = False,
    ) -> None:
        """Parse one check and resolve every reference it makes.

        ``allow_self`` is for a step's **end_state**: by then the step's own
        outputs are in the context, so ``$steps.<its own id>.<key>`` is legitimate.
        A precondition or an input binding must not read the step's own outputs.
        """
        if not str(text or "").strip():
            return
        try:
            parsed = parse_check(text)
        except CheckError as exc:
            report.problems.append(f"{owner}: {exc}")
            return
        report.checks_parsed += 1
        for ref in parsed.refs:
            parts = ref.lstrip("$").split(".")
            if parts[0] == "input":
                if len(parts) != 2 or parts[1] not in comp.inputs:
                    report.problems.append(
                        f"{owner}: {ref} is not a declared composite input "
                        f"(declared: {sorted(comp.inputs)})"
                    )
                else:
                    consumed.add(parts[1])
                continue
            if not allow_steps:
                report.problems.append(
                    f"{owner}: {ref} reads a step — a composite precondition may only "
                    "read $input.*"
                )
                continue
            if len(parts) < 3:
                report.problems.append(
                    f"{owner}: {ref} must be $steps.<id>.<key> (or a deeper path into it)"
                )
                continue
            producer = comp.step(parts[1])
            if producer is None:
                report.problems.append(f"{owner}: {ref} names unknown step {parts[1]!r}")
                continue
            if owner_index is not None:
                producer_index = step_index.get(parts[1], 0)
                too_late = producer_index > owner_index or (
                    producer_index == owner_index and not allow_self
                )
                if too_late:
                    report.problems.append(
                        f"{owner}: {ref} reads step {parts[1]!r} "
                        + ("at" if producer_index == owner_index else "after")
                        + " this step — a composite is an ordered chain"
                    )
            if parts[2] not in producer.outputs:
                report.problems.append(
                    f"{owner}: {ref} is not a declared output of step {parts[1]!r} "
                    f"(declared: {producer.outputs})"
                )

    _check_refs(
        comp.precondition, owner="composite precondition", allow_steps=False, owner_index=None
    )
    # The composite's own end_state runs after every step, so it may read any step — and
    # each reference must resolve against a *declared* output, exactly like a step's own
    # end_state. Left unchecked, a typo there would only surface as a confusing run-time
    # "not found" after a whole chain had already executed.
    _check_refs(
        comp.end_state, owner="composite end_state", allow_steps=True, owner_index=None
    )
    for index, step in enumerate(comp.steps):
        _check_refs(
            step.precondition,
            owner=f"step {step.id or '?'} precondition",
            allow_steps=True,
            owner_index=index,
        )
        _check_refs(
            step.end_state,
            owner=f"step {step.id or '?'} end_state",
            allow_steps=True,
            owner_index=index,
            allow_self=True,
        )

    def _one_ref(ref: str, *, field: str, index: int, step: StepSpec) -> None:
        """Validate one reference against the declared dataflow.

        ``field`` is what the caller calls it (an input name, ``url``, an argv token), so the
        message names the thing that is wrong instead of the reference alone.
        """
        parts = ref.lstrip("$").split(".")
        owner = f"step {step.id or '?'}"
        if parts[0] == "input":
            if len(parts) != 2 or parts[1] not in comp.inputs:
                report.problems.append(
                    f"{owner}: {field} uses {ref}, which is not a declared composite input "
                    f"(declared: {sorted(comp.inputs)})"
                )
            else:
                consumed.add(parts[1])
            return
        if parts[0] != "steps" or len(parts) < 3:
            report.problems.append(
                f"{owner}: {field} uses {ref} — expected $input.<name> or $steps.<id>.<key> "
                "(a deeper path into the key is allowed)"
            )
            return
        producer = comp.step(parts[1])
        if producer is None:
            report.problems.append(f"{owner}: {field} reads unknown step {parts[1]!r}")
        elif step_index.get(parts[1], 0) >= index:
            report.problems.append(
                f"{owner}: {field} reads step {parts[1]!r} which does not run before it — "
                "a composite is an ordered chain"
            )
        elif parts[2] not in producer.outputs:
            report.problems.append(
                f"{owner}: {field} uses {ref}, which is not a declared output of step "
                f"{parts[1]!r} (declared: {producer.outputs})"
            )

    # ---- step inputs (the bindings) ---------------------------------------
    for index, step in enumerate(comp.steps):
        if not isinstance(step.inputs, Mapping):
            report.problems.append(f"step {step.id or '?'}: inputs must be an object")
            continue
        for name, value in step.inputs.items():
            if not isinstance(value, str) or not value.startswith("$"):
                continue  # a literal
            parts = value.lstrip("$").split(".")
            if parts[0] == "input":
                if len(parts) != 2 or parts[1] not in comp.inputs:
                    report.problems.append(
                        f"step {step.id or '?'}: input {name!r} binds {value} which is not a "
                        f"declared composite input (declared: {sorted(comp.inputs)})"
                    )
                else:
                    consumed.add(parts[1])
                continue
            if parts[0] != "steps" or len(parts) < 3:
                report.problems.append(
                    f"step {step.id or '?'}: input {name!r} binds {value!r} — expected "
                    "$input.<name> or $steps.<id>.<key> (a deeper path into the key is allowed)"
                )
                continue
            producer = comp.step(parts[1])
            if producer is None:
                report.problems.append(
                    f"step {step.id or '?'}: input {name!r} reads unknown step {parts[1]!r}"
                )
                continue
            if step_index.get(parts[1], len(comp.steps)) >= index:
                report.problems.append(
                    f"step {step.id or '?'}: input {name!r} reads step {parts[1]!r} which does "
                    "not run before it — a composite is an ordered chain"
                )
            elif parts[2] not in producer.outputs:
                report.problems.append(
                    f"step {step.id or '?'}: input {name!r} reads {value} which is not a "
                    f"declared output of step {parts[1]!r} (declared: {producer.outputs})"
                )
        for name in step.outputs:
            if step.kind == "docker" and name not in DOCKER_OUTPUT_KEYS:
                report.problems.append(
                    f"step {step.id or '?'}: docker output {name!r} is not one of "
                    f"{list(DOCKER_OUTPUT_KEYS)}"
                )
            if step.kind in ("api", "gui"):
                known = _runner_output_keys(step.kind)
                if known and name not in known:
                    report.problems.append(
                        f"step {step.id or '?'}: {step.kind} output {name!r} is not one of "
                        f"{list(known)} — that is what the {step.kind} runner really returns"
                    )
        # an api/gui step binds nested declarations (a JSON body, an action list): the refs
        # inside them are dataflow exactly like a top-level binding, so they are checked here
        # instead of surfacing at run time as an unbound value.
        if step.kind in ("api", "gui"):
            for name, value in step.inputs.items():
                if isinstance(value, (str, bytes)) or not isinstance(value, (Mapping, Sequence)):
                    continue
                for ref in _nested_refs(value):
                    _one_ref(ref, field=str(name), index=index, step=step)
            if step.kind == "api" and str(step.url or "").strip().startswith("$"):
                for ref in _nested_refs(step.url):
                    _one_ref(ref, field="url", index=index, step=step)
        # a docker step binds through argv — so its argv refs are dataflow too
        for token in step.argv if step.kind == "docker" else []:
            refs = _REF_RE.findall(str(token))
            if not refs and str(token).startswith("$"):
                report.problems.append(
                    f"step {step.id or '?'}: argv token {token!r} — expected $input.<name> or "
                    "$steps.<id>.<key> (inline refs are substituted), or a literal"
                )
                continue
            for ref in refs:
                parts = ref.lstrip("$").split(".")
                if parts[0] == "input":
                    if len(parts) != 2 or parts[1] not in comp.inputs:
                        report.problems.append(
                            f"step {step.id or '?'}: argv token {token!r} uses {ref}, which is "
                            f"not a declared composite input (declared: {sorted(comp.inputs)})"
                        )
                    else:
                        consumed.add(parts[1])
                    continue
                if parts[0] != "steps" or len(parts) < 3:
                    report.problems.append(
                        f"step {step.id or '?'}: argv token {token!r} uses {ref} — expected "
                        "$input.<name> or $steps.<id>.<key> (a deeper path is allowed)"
                    )
                    continue
                producer = comp.step(parts[1])
                if producer is None:
                    report.problems.append(
                        f"step {step.id or '?'}: argv token {token!r} reads "
                        f"unknown step {parts[1]!r}"
                    )
                elif step_index.get(parts[1], 0) >= index:
                    report.problems.append(
                        f"step {step.id or '?'}: argv token {token!r} reads step {parts[1]!r} "
                        "which does not run before it"
                    )
                elif parts[2] not in producer.outputs:
                    report.problems.append(
                        f"step {step.id or '?'}: argv token {token!r} uses {ref}, which is not a "
                        f"declared output of step {parts[1]!r} (declared: {producer.outputs})"
                    )

    # ---- composite outputs -------------------------------------------------
    produced: dict[str, str] = {}
    for step in comp.steps:
        for name in step.outputs:
            produced.setdefault(name, step.id)
    for name in comp.outputs:
        if name not in produced:
            report.problems.append(
                f"composite output {name!r} is produced by no step "
                f"(declared step outputs: {sorted(produced)})"
            )
    for name in comp.inputs:
        if name not in consumed:
            report.warnings.append(f"declared input {name!r} is read by nothing")
    # A step output nothing consumes is dead weight, not an error.
    consumers: set[str] = set()
    for step in comp.steps:
        blob = " ".join(
            [str(value) for value in (step.inputs or {}).values()]
            + [step.precondition, step.end_state]
        )
        for name, producer in produced.items():
            if f"$steps.{producer}.{name}" in blob:
                consumers.add(name)
    consumers.update(name for name in produced if name in comp.end_state)
    for name, producer in sorted(produced.items()):
        if name not in consumers and name not in comp.outputs:
            report.warnings.append(
                f"step {producer!r} output {name!r} is consumed by nothing"
            )
    return report


# --------------------------------------------------------------------------- #
# execution                                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class StepRun:
    id: str
    kind: str
    target: str
    status: str  # ok | precondition_failed | failed | end_state_failed | blocked | usage
    ok: bool
    detail: str = ""
    outputs: dict = field(default_factory=dict)
    raw: Any = None
    evidence: str = ""
    missing_kind: str = ""
    missing_capability: str = ""
    failure_class: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "target": self.target,
            "status": self.status,
            "ok": self.ok,
            "detail": self.detail,
            "outputs": dict(self.outputs),
            "evidence": self.evidence,
            "missing_kind": self.missing_kind,
            "missing_capability": self.missing_capability,
            "failure_class": self.failure_class,
            "duration_ms": self.duration_ms,
        }


@dataclass
class CompositeRun:
    name: str
    status: str  # verified | failed | blocked | usage
    exit_code: int
    reason: str = ""
    steps: list[StepRun] = field(default_factory=list)
    outputs: dict = field(default_factory=dict)
    missing_kinds: list[str] = field(default_factory=list)
    precondition: str = ""
    end_state: str = ""
    duration_ms: int = 0
    validation: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "verified"

    @property
    def verified(self) -> bool:
        return self.status == "verified"

    def to_dict(self) -> dict:
        return {
            "composite": self.name,
            "status": self.status,
            "ok": self.ok,
            "verified": self.verified,
            "exit_code": self.exit_code,
            "reason": self.reason,
            "steps": [item.to_dict() for item in self.steps],
            "step_count": len(self.steps),
            "outputs": dict(self.outputs),
            "missing_kinds": list(self.missing_kinds),
            "precondition": self.precondition,
            "end_state": self.end_state,
            "duration_ms": self.duration_ms,
            "validation": self.validation,
        }


def _bind(value: Any, context: Mapping[str, Any]) -> Any:
    """Resolve one binding value: a ``$ref`` string, else the literal itself."""
    if isinstance(value, str) and value.startswith("$"):
        found, resolved = _dig(context, value)
        if not found:
            raise CheckError(str(resolved))
        return resolved
    return value


def _substitute_token(token: str, context: Mapping[str, Any]) -> str:
    """Substitute every ``$ref`` inside one argv token (inline refs included).

    ``--tag $input.tag`` and ``echo $input.text`` both work; a ref that does not
    resolve raises :class:`CheckError`, so an unbound value can never reach the
    container as a literal ``$input.…`` string.
    """
    out = str(token)
    for ref in _REF_RE.findall(out):
        found, value = _dig(context, ref)
        if not found:
            raise CheckError(str(value))
        out = out.replace(ref, str(value))
    return out


def _nested_refs(value: Any) -> list[str]:
    """Every ``$ref`` inside a nested declaration (lists/objects), first-seen order.

    A gui step binds a whole flow (``inputs.actions``) and an api step binds a JSON body;
    the bindings live *inside* containers, so a top-level-only scan would leave them
    unchecked and an unbound/typo'd ref would only surface at run time.
    """
    out: list[str] = []
    stack: list[Any] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            for ref in _REF_RE.findall(item):
                if ref not in out:
                    out.append(ref)
        elif isinstance(item, Mapping):
            stack.extend(item.values())
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            stack.extend(item)
    return out


def _bind_step_inputs(step: StepSpec, context: Mapping[str, Any]) -> dict:
    return {str(name): _bind(value, context) for name, value in (step.inputs or {}).items()}


def _execute_tool_step(
    step: StepSpec, arguments: dict, *, store: ToolStore, runtime: Path | None
) -> tuple[bool, dict, Any, str]:
    """Call an approved python tool through the MCP wrapper (sandbox + usage ledger)."""
    from .mcpserver import _wrapper_for

    spec = store.get(step.target)
    if spec is None:
        return False, {}, None, f"tool {step.target!r} vanished from the registry"
    base_dir = runtime if runtime is not None else Path(store.path).parent
    call = _wrapper_for(spec, base_dir=base_dir)
    try:
        result = call(arguments)
    except Exception as exc:  # noqa: BLE001 — a failing step is reported, never hidden
        return False, {}, None, f"{type(exc).__name__}: {exc}"
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return False, {}, result, "the tool returned a non-JSON string"
    if not isinstance(result, Mapping):
        return False, {}, result, f"the tool returned {type(result).__name__}, not a result object"
    if "error" in result:
        return False, {}, dict(result), str(result.get("error"))
    outputs = {}
    missing = [name for name in step.outputs if name not in result]
    if missing:
        return (
            False,
            {},
            dict(result),
            f"declared output(s) {missing} are absent from the result "
            f"(present: {sorted(str(key) for key in result)})",
        )
    for name in step.outputs:
        outputs[name] = result[name]
    return True, outputs, dict(result), ""


def _execute_docker_step(
    step: StepSpec,
    context: Mapping[str, Any],
    *,
    runtime: Path | None,
    docker_bin: str | None,
) -> tuple[bool, dict, Any, str, str]:
    """One-shot container run through ``DockerRuntimeManager.run`` (never a fake).

    A docker step binds through **argv**: every token may be a ``$input.<name>`` /
    ``$steps.<id>.<key>`` reference (resolved against the composite context) or a
    literal. ``inputs`` must stay empty for a docker step — that is checked at
    validation time, so an unbound value can never be silently dropped.
    """
    from .docker_runtime import DockerRuntimeManager, RunConfig

    manager = DockerRuntimeManager(docker_bin=docker_bin or None)
    available, detail = manager.available()
    if not available:
        return False, {}, {"detail": detail}, f"docker daemon not reachable: {detail}", "docker"
    argv: list[str] = []
    for token in step.argv:
        try:
            argv.append(_substitute_token(token, context))
        except CheckError as exc:
            return False, {}, {"token": token}, f"argv token {token!r}: {exc}", ""
    workdir = Path(runtime) if runtime is not None else None
    config = RunConfig(
        image=step.image,
        image_digest=step.image_digest,
        entrypoint_argv=argv,
        network=step.network or "none",
        workdir=workdir,
    )
    result = manager.run(config, timeout=float(step.timeout_s or 60.0))
    payload = {
        "ok": bool(result.ok),
        "exit_code": int(result.exit_code or 0),
        "stdout": str(result.stdout or ""),
        "stderr": str(result.stderr or ""),
    }
    if not result.ok:
        return (
            False,
            {},
            payload,
            str(result.detail or result.failure_class or "docker run failed"),
            "",
        )
    outputs = {name: payload[name] for name in step.outputs}
    return True, outputs, payload, "", ""


def _gui_evidence_suffix(entry: StepRun, outcome: Any) -> None:
    """Append the visual evidence and the teardown result to a GUI step's evidence line.

    Both are read from the runner's own report: the step either left a screenshot (with its
    hash, so the picture can be identified) or the outcome says which capability could not
    produce one; and the step either cleaned up its container or the line says it did not.
    A GUI step in a chain is not fully reported without those two facts.
    """
    outputs = dict(getattr(outcome, "outputs", {}) or {})
    raw_shot = outputs.get("screenshot")
    shot: dict = dict(raw_shot) if isinstance(raw_shot, Mapping) else {}
    if shot.get("path"):
        entry.evidence += (
            f"; screenshot {shot['path']}"
            + (f" (sha256 {str(shot.get('sha256'))[:12]})" if shot.get("sha256") else "")
            + (f" named after {shot.get('label')!r}" if shot.get("label") else "")
        )
    else:
        entry.evidence += "; no screenshot (the runner reported none)"
    cleanup = outputs.get("cleanup") if isinstance(outputs.get("cleanup"), Mapping) else {}
    if cleanup:
        entry.evidence += (
            "; cleanup: still present"
            if cleanup.get("still_present")
            else "; cleanup: the runner was removed"
        )


def _runner_result(outcome: Any) -> tuple[bool, dict, Any, str, str, str, int]:
    """``StepOutcome`` -> ``(ok, outputs, raw, detail, missing_kind, failure_class, exit)``.

    ``skaldir.step_runners.as_step_tuple`` is the runner layer's own adapter for exactly
    this dispatch; the two extra fields (``failure_class``, ``exit_code``) are what makes
    a refusal first-class — a declaration problem exits 1, a host without the capability
    exits 3 with the capability named, a real miss exits 2.
    """
    from .step_runners import as_step_tuple

    ok, outputs, raw, detail, missing = as_step_tuple(outcome)
    return (
        bool(ok),
        dict(outputs),
        raw,
        str(detail),
        str(missing),
        str(getattr(outcome, "failure_class", "") or ""),
        int(getattr(outcome, "exit_code", EXIT_VERIFICATION_FAILED) or 0),
    )


def _execute_api_step(
    step: StepSpec,
    arguments: dict,
    *,
    context: Mapping[str, Any],
    runtime: Path | None,
) -> Any:
    """One declared HTTP step through ``skaldir.step_runners.run_api_step`` (never a fake).

    The runner owns the whole HTTP contract — the declared request really goes out, the
    declared status/body assertions are checked against the **real** response, the declared
    retry policy is what decides how many attempts happen — and this call site is what makes
    the ``api`` kind a product path instead of a harness-only capability. Nothing about the
    request is re-implemented here. An exception out of the runner becomes a failed step
    (named), never a crash and never a pass.
    """
    from . import step_runners

    try:
        return step_runners.run_api_step(step, arguments, context=context, runtime=runtime)
    except Exception as exc:  # noqa: BLE001 — a crashing runner is a failed step
        return step_runners.StepOutcome.failure(
            step_runners.KIND_API,
            f"the api step runner raised {type(exc).__name__}: {exc}",
            failure_class=step_runners.FAILURE_RUNNER,
            raw={"exception": f"{type(exc).__name__}: {exc}"},
        )


def _execute_gui_step(
    step: StepSpec,
    arguments: dict,
    *,
    context: Mapping[str, Any],
    runtime: Path | None,
) -> Any:
    """One declared GUI step through ``skaldir.step_runners.run_gui_step`` (never a fake).

    The runner resolves a **real** isolated bridge (the container GUI display or the bwrap
    sandbox), refuses the in-process fixture for a composite step, and reads the end state
    back from the display. When this host has no bridge the runner answers
    ``StepOutcome.block`` with ``missing_kind`` and the precise missing capability — a named
    refusal with exit 3, which is the honest outcome and not an error.

    ``allow_fixture`` is deliberately not reachable from here: a composite document may
    never turn a scripted fixture into a "verified" GUI run.
    """
    from . import step_runners

    try:
        return step_runners.run_gui_step(step, arguments, context=context, runtime=runtime)
    except Exception as exc:  # noqa: BLE001 — a crashing bridge is a failed step
        return step_runners.StepOutcome.failure(
            step_runners.KIND_GUI,
            f"the gui step runner raised {type(exc).__name__}: {exc}",
            failure_class=step_runners.FAILURE_RUNNER,
            raw={"exception": f"{type(exc).__name__}: {exc}"},
        )


def run_composite(
    store: ToolStore,
    comp: CompositeSpec,
    *,
    inputs: Mapping[str, Any] | None = None,
    runtime: str | Path | None = None,
    docker_bin: str | None = None,
    subtools: Any = None,
) -> CompositeRun:
    """Execute the chain for real, step by step, and report the honest verdict.

    Order of the three refusals (measured, not hidden):

    1. ``usage``      — a required declared input is missing, or a check references
       something the context does not hold (the caller's invocation).
    2. ``blocked``    — a blocking validation problem, or a step kind with no
       runner (the missing kind is named in ``missing_kinds``).
    3. ``failed``     — the chain really ran and a step/end-state did not hold.

    Only ``verified`` (all steps ok **and** the composite end-state true) exits 0.
    """
    started = time.time()
    runtime_path = Path(runtime) if runtime is not None else None
    report = validate_composite(store, comp, subtools=subtools)
    out = CompositeRun(
        name=comp.name,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        precondition=comp.precondition,
        end_state=comp.end_state,
        validation=report.to_dict(),
    )

    supplied = dict(inputs or {})
    missing_inputs = [name for name in comp.required_inputs if name not in supplied]
    if missing_inputs:
        out.status = "usage"
        out.exit_code = EXIT_USAGE
        out.reason = (
            f"missing required composite input(s) {missing_inputs} "
            f"— declared: {sorted(comp.inputs)}"
        )
        return out

    context: dict[str, Any] = {
        "input": {
            name: supplied.get(name, (spec or {}).get("default"))
            for name, spec in comp.inputs.items()
        },
        "steps": {},
        "exit_ok": True,
    }

    if report.missing_kinds or report.problems:
        out.missing_kinds = list(report.missing_kinds)
        bits: list[str] = []
        if report.missing_kinds:
            # name the step, not only the kind — a bare "api is missing" is not actionable
            blamed = [
                f"step {step.id!r} (kind {step.kind!r}): {kind_reason(step.kind)}"
                for step in comp.steps
                if step.kind in report.missing_kinds
            ]
            bits.append("no runner for " + " | ".join(blamed))
        if report.problems:
            bits.append("validation: " + "; ".join(report.problems))
        out.reason = " | ".join(bits)
        return out

    try:
        ok, detail = evaluate_check(parse_check(comp.precondition), context)
    except CheckError as exc:
        out.status = "usage"
        out.exit_code = EXIT_USAGE
        out.reason = f"composite precondition: {exc}"
        return out
    if not ok:
        out.status = "failed"
        out.exit_code = EXIT_VERIFICATION_FAILED
        out.reason = f"composite precondition is false: {comp.precondition} ({detail})"
        return out

    for step in comp.steps:
        step_started = time.time()
        entry = StepRun(id=step.id, kind=step.kind, target=step.target or step.image or step.url,
                        status="ok", ok=False)
        try:
            pre_ok, pre_detail = evaluate_check(parse_check(step.precondition), context)
        except CheckError as exc:
            entry.status, entry.detail = "failed", f"precondition: {exc}"
            out.steps.append(entry)
            out.status, out.exit_code = "failed", EXIT_VERIFICATION_FAILED
            out.reason = f"step {step.id}: precondition is not parseable: {exc}"
            return out
        if not pre_ok:
            entry.status = "precondition_failed"
            entry.detail = f"{step.precondition} ({pre_detail})"
            out.steps.append(entry)
            out.status, out.exit_code = "failed", EXIT_VERIFICATION_FAILED
            out.reason = f"step {step.id}: precondition is false: {entry.detail}"
            return out

        if step.kind == "docker":
            arguments = {}  # a docker step binds through its argv, never through inputs
        else:
            try:
                arguments = _bind_step_inputs(step, context)
            except CheckError as exc:
                entry.status, entry.detail = "failed", str(exc)
                entry.duration_ms = int((time.time() - step_started) * 1000)
                out.steps.append(entry)
                out.status, out.exit_code = "failed", EXIT_VERIFICATION_FAILED
                out.reason = f"step {step.id}: cannot bind its inputs: {exc}"
                return out

        if step.kind == "tool":
            ok, outputs, raw, detail = _execute_tool_step(
                step, arguments, store=store, runtime=runtime_path
            )
            entry.evidence = f"registry tool {step.target!r} via mcpserver._wrapper_for"
        elif step.kind == "docker":
            ok, outputs, raw, detail, missing = _execute_docker_step(
                step, context, runtime=runtime_path, docker_bin=docker_bin
            )
            entry.evidence = (
                f"docker_runtime.DockerRuntimeManager.run image_digest={step.image_digest!r}"
            )
            if missing:
                entry.status, entry.detail, entry.missing_kind = "blocked", detail, missing
                entry.missing_capability = detail
                entry.duration_ms = int((time.time() - step_started) * 1000)
                out.steps.append(entry)
                out.missing_kinds = [missing]
                out.status, out.exit_code, out.reason = "blocked", EXIT_BLOCKED, (
                    f"step {step.id}: {detail}"
                )
                return out
        else:
            # api / gui: dispatched to the real runner in skaldir.step_runners, which owns
            # the whole contract and answers with its own StepOutcome.
            if step.kind == "api":
                outcome = _execute_api_step(
                    step, arguments, context=context, runtime=runtime_path
                )
                entry.evidence = (
                    f"step_runners.run_api_step ({step.method} {step.url})"
                )
            else:  # gui — executable is decided by the executor map, this is the last guard
                outcome = _execute_gui_step(
                    step, arguments, context=context, runtime=runtime_path
                )
                entry.evidence = (
                    "step_runners.run_gui_step -> act_service."
                    + ("run_subtool" if step.target not in ("inline", "") else "run_action_list")
                    + f" (target {step.target!r}, real isolated bridge only)"
                )
                _gui_evidence_suffix(entry, outcome)
            ok, outputs, raw, detail, missing, failure_class, step_exit = _runner_result(outcome)
            entry.failure_class = failure_class
            entry.outputs, entry.raw = dict(outputs), raw
            entry.duration_ms = int((time.time() - step_started) * 1000)
            if missing:
                # a named refusal: the kind is wired, this host cannot run it. The precise
                # missing capability travels with the step (never a bare "gui is missing").
                entry.status, entry.detail, entry.missing_kind = "blocked", detail, missing
                entry.missing_capability = str(
                    dict(outputs).get("missing_capability") or detail
                )
                out.steps.append(entry)
                out.missing_kinds = [missing]
                out.status, out.exit_code, out.reason = "blocked", EXIT_BLOCKED, (
                    f"step {step.id}: {detail}"
                )
                return out
            if not ok and step_exit == EXIT_USAGE:
                # the runner refused the *declaration* (an unknown field, a non-http url, an
                # unparsed retry policy): nothing was sent, the caller can fix it
                entry.status, entry.ok, entry.detail = "usage", False, detail
                out.steps.append(entry)
                out.status, out.exit_code = "usage", EXIT_USAGE
                out.reason = f"step {step.id}: {detail}"
                return out
            if not ok:
                entry.status, entry.ok, entry.detail = "failed", False, detail
                out.steps.append(entry)
                out.status, out.exit_code = "failed", EXIT_VERIFICATION_FAILED
                out.reason = f"step {step.id} failed: {detail}"
                return out

        entry.raw = raw
        entry.outputs = dict(outputs)
        entry.duration_ms = int((time.time() - step_started) * 1000)
        if not ok:
            entry.status, entry.ok, entry.detail = "failed", False, detail
            out.steps.append(entry)
            out.status, out.exit_code = "failed", EXIT_VERIFICATION_FAILED
            out.reason = f"step {step.id} failed: {detail}"
            return out
        context["steps"][step.id] = dict(outputs)
        context["exit_ok"] = True
        try:
            end_ok, end_detail = evaluate_check(parse_check(step.end_state), context)
        except CheckError as exc:
            entry.status, entry.ok, entry.detail = "end_state_failed", False, f"end_state: {exc}"
            out.steps.append(entry)
            out.status, out.exit_code = "failed", EXIT_VERIFICATION_FAILED
            out.reason = f"step {step.id}: end_state is not parseable: {exc}"
            return out
        if not end_ok:
            entry.status, entry.ok = "end_state_failed", False
            entry.detail = f"{step.end_state} ({end_detail})"
            out.steps.append(entry)
            out.status, out.exit_code = "failed", EXIT_VERIFICATION_FAILED
            out.reason = f"step {step.id}: end_state is false: {entry.detail}"
            return out
        entry.status, entry.ok, entry.detail = "ok", True, end_detail
        out.steps.append(entry)

    try:
        final_ok, final_detail = evaluate_check(parse_check(comp.end_state), context)
    except CheckError as exc:
        out.status, out.exit_code = "usage", EXIT_USAGE
        out.reason = f"composite end_state: {exc}"
        return out
    if not final_ok:
        out.status, out.exit_code = "failed", EXIT_VERIFICATION_FAILED
        out.reason = f"composite end_state is false: {comp.end_state} ({final_detail})"
        return out

    for name in comp.outputs:
        for step in comp.steps:
            if name in step.outputs:
                out.outputs[name] = context["steps"].get(step.id, {}).get(name)
                break
    out.status, out.exit_code = "verified", EXIT_OK
    out.reason = f"{len(comp.steps)} step(s) verified; end_state {comp.end_state!r} holds"
    out.duration_ms = int((time.time() - started) * 1000)
    return out


# --------------------------------------------------------------------------- #
# compose candidates: which chains the real registry can build                  #
# --------------------------------------------------------------------------- #


def _usable_steps(store: ToolStore) -> list[ToolSpec]:
    """Approved, executable-by-kind registry tools (the steps a chain can use)."""
    return [
        spec
        for spec in store.all()
        if spec.status == STATUS_APPROVED
        and spec.kind == KIND_PYTHON
        and spec.module
        and spec.entrypoint
    ]


def _join_terms(left: ToolSpec, right: ToolSpec) -> list[str]:
    """The declared vocabulary two steps share (a heuristic, never a proof).

    The registry records a tool's *sample input* keys, so the honest join signal is
    a shared datum name (``text`` → ``text``) plus the overlap between the left
    tool's identity tokens and the right tool's declared inputs. A candidate is a
    **proposal**: ``compose create`` declares the real inputs/outputs, and
    :func:`validate_composite` then checks the dataflow for real.
    """
    left_keys = {str(key).lower() for key in (left.sample_input or {})}
    right_keys = {str(key).lower() for key in (right.sample_input or {})}
    shared = sorted(left_keys & right_keys) if left_keys and right_keys else []
    tokens = {
        word.lower()
        for word in re.findall(r"[A-Za-z_]{3,}", f"{left.name} {left.title} {left.description}")
    }
    contract = sorted(tokens & right_keys)
    return sorted(set(shared) | set(contract))


def buildable_chains(
    store: ToolStore,
    *,
    need: str = "",
    tags: list[str] | None = None,
    limit: int = 8,
    max_len: int = 4,
) -> dict:
    """Chains the real registry can build, ranked — plus the composites already there.

    A chain is a sequence of 2..``max_len`` distinct approved executable tools whose
    declared vocabulary joins (see :func:`_join_terms`). With ``need`` the seeds are
    the registry's own ranked compose candidates (``tools.py:469``); without a need
    every approved step seeds one chain.
    """
    steps = _usable_steps(store)
    seed_names: list[str] = []
    if need or tags:
        scored = compose_candidates_scored(
            store, description=need or " ", tags=tags, limit=max(limit, 3), min_score=1
        )
        seed_names = [spec.name for _score, spec in scored if spec.name in {s.name for s in steps}]
    by_name = {spec.name: spec for spec in steps}
    seeds = [by_name[name] for name in seed_names] if seed_names else steps

    chains: list[dict] = []
    used_chain_keys: set[tuple[str, ...]] = set()
    for seed in sorted(seeds, key=lambda item: item.name):
        chain = [seed]
        joins: list[dict] = []
        while len(chain) < max_len:
            best: tuple[int, ToolSpec | None, list[str]] = (0, None, [])
            for candidate in sorted(steps, key=lambda item: item.name):
                if candidate in chain:
                    continue
                terms = _join_terms(chain[-1], candidate)
                score = len(terms)
                if score > best[0]:
                    best = (score, candidate, terms)
            if best[1] is None:
                break
            joins.append(
                {"from": chain[-1].name, "to": best[1].name, "terms": list(best[2])}
            )
            chain.append(best[1])
        if len(chain) < 2:
            continue
        key = tuple(item.name for item in chain)
        if key in used_chain_keys:
            continue
        used_chain_keys.add(key)
        flat_terms = sorted({term for join in joins for term in join["terms"]})
        chains.append(
            {
                "chain": list(key),
                "kinds": [KIND_PYTHON] * len(key),
                "join_terms": flat_terms,
                "joins": joins,
                "join_score": sum(len(join["terms"]) for join in joins),
                "length": len(key),
                "proposal": "declare inputs/outputs and a precondition/end-state per step",
                "evidence": "registry step vocabulary (tools.sample_input keys + identity tokens)",
            }
        )
    chains.sort(key=lambda item: (-item["join_score"], item["chain"]))
    composites = [
        {
            "name": spec.name,
            "status": spec.status,
            "version": spec.version,
            "steps": [str(step.get("id", "")) for step in spec.steps if isinstance(step, Mapping)],
            "kinds": [
                str(step.get("kind", "")) for step in spec.steps if isinstance(step, Mapping)
            ],
            "path": str(composite_path(Path(store.path).parent, spec.name)),
        }
        for spec in store.all()
        if spec.kind == KIND_COMPOSITION
    ]
    return {
        "need": need,
        "tags": list(tags or []),
        "registry": str(store.path),
        "runtime": str(Path(store.path).parent),
        "steps_considered": len(steps),
        "seed_count": len(seeds),
        "chains": chains[:limit],
        "chain_count": len(chains),
        "composites": composites,
        "composite_count": len(composites),
        "rule": (
            "a chain candidate is a heuristic proposal (shared declared vocabulary); "
            "`compose create` declares the real inputs/outputs and `compose validate` "
            "checks the dataflow"
        ),
        "executors": executor_report(),
    }


__all__ = [
    "COMPOSITES_DIR",
    "DOCKER_OUTPUT_KEYS",
    "Check",
    "CheckError",
    "Clause",
    "CompositeError",
    "CompositeRun",
    "CompositeSpec",
    "KindExecutor",
    "STEP_KINDS",
    "StepRun",
    "StepSpec",
    "StepVerdict",
    "ValidationReport",
    "buildable_chains",
    "composite_files",
    "composite_from_registry",
    "composite_path",
    "composites_dir",
    "document_digest",
    "evaluate_check",
    "executor_for",
    "executor_report",
    "kind_reason",
    "load_composite",
    "missing_kinds",
    "parse_check",
    "parse_composite",
    "run_composite",
    "save_composite",
    "validate_composite",
]
