"""Autonomous tool builder — capability gap → build → gates → register.

Reuses the existing tool pipeline end to end:

* ``ToolSpec`` / ``ToolStore`` for the registry,
* ``gates.run_gates`` for secret scan / import / tests / determinism (sandboxed),
* ``toolscan`` findings inside those gates,
* ``mcpserver.build_server`` so ``type: mcp`` tools become MCP tools,
* the Treasury for credential *handles* (``secret://…``) — values never appear.

Every stage transition is appended to a build queue (JSONL) that the desktop
panel renders live — no fake progress: a stage is recorded when it actually
runs, and its detail carries the real gate output.

The builder does not invent research. A capability request carries the
research notes / source produced upstream (investigator or caller); the
builder's job is the deterministic pipeline, manifest, gates and registration.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import buildflow as bf
from . import container_adapter, container_media
from .docker_runtime import (
    DockerRuntimeManager,
    RunConfig,
    owned_labels,
)
from .docker_runtime import (
    validate_mounts as _runtime_validate_mounts,
)
from .events import EventStore
from .gates import run_gates
from .llm import BudgetTracker, LLMClient
from .prompts import PromptRegistry
from .tools import (
    KIND_COMPOSITION,
    KIND_PYTHON,
    STATUS_APPROVED,
    STATUS_CANDIDATE,
    STATUS_REVOKED,
    ToolSpec,
    ToolStore,
    compose_candidates_scored,
    draft_tool_spec_result,
    validate_composition,
)


def _run_adapter_probe(
    module_path: Path, module: str, entrypoint: str, payload: dict, *, workdir: Path
) -> dict:
    """Run the generated adapter once with one payload (sandboxed, fail-closed).

    This is the functional probe behind the `validate` phase and the evidence
    package's ``runtime_probe``: `python <module>.py` conventions do not apply
    to general tools, so the entrypoint is imported and called directly inside
    the sandbox. A missing sandbox yields no evidence (never a fake pass).
    """
    from .gates import _run_checked

    runner = (
        "import importlib.util, json, sys\n"
        "spec = importlib.util.spec_from_file_location('gv_probe', sys.argv[1])\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "out = getattr(mod, sys.argv[2])(**json.loads(sys.argv[3]))\n"
        "print(json.dumps(out, ensure_ascii=False, default=str))\n"
    )
    try:
        code, output, sandboxed = _run_checked(
            [sys.executable, "-c", runner, str(module_path), entrypoint or "main",
             json.dumps(payload, sort_keys=True)],
            workdir=workdir,
            timeout=30.0,
        )
    except Exception as exc:  # a crashing probe is not a pass
        return {
            "input": payload, "ok": False, "sandboxed": False,
            "detail": f"{type(exc).__name__}: {exc}"[:200],
        }
    text = (output or "").strip()
    parsed: object = None
    if code == 0 and text:
        try:
            parsed = json.loads(text.splitlines()[-1])
        except json.JSONDecodeError:
            parsed = None
    return {
        "input": payload,
        "ok": code == 0 and parsed is not None,
        "sandboxed": bool(sandboxed),
        "module": module,
        "detail": text[:200],
    }


def _run_testing_case(module: str, module_path: Path, target: str, *, workdir: Path) -> dict:
    """Run a generated testing tool against one target (validation case).

    Convention: ``python <module>.py <target>``; a pass means exit 0 with
    ``PASS`` on stdout — everything else is a fail. Used by the mandatory
    testing-tool gate (P1/B4).
    """
    from .gates import _run_checked, parse_testing_result

    try:
        code, output, _sandboxed = _run_checked(
            [sys.executable, str(module_path), str(target)], workdir=workdir, timeout=30.0
        )
    except Exception as exc:  # a crashing tool is invalid, not a pass
        return {"passed": False, "detail": f"{type(exc).__name__}: {exc}"[:160]}
    ok, reason = parse_testing_result(code, output)
    return {"passed": ok, "detail": (reason or output[:160])}


STAGES = (
    "requested",
    "researching",
    "generating",
    "manifest",
    "dependency_inspection",
    "gates",
    "register",
    "ready",
)

_MCP_HINTS = ("mcp", "server", "structured", "integration", "endpoints", "several", "multiple")
_CLI_HINTS = ("wrap", "cli", "shell", "status", "check", "one-shot", "deterministic", "small")

# §1.7 compose-before-create thresholds — deliberately the same conservatism as
# capability_mining._BUILD_REFUSE_OVERLAP (3 terms = duplicate ⇒ refuse);
# 2 terms stays an advisory hint so legitimate new work is never blocked.
_COMPOSE_REFUSE_OVERLAP = 3
_COMPOSE_HINT_OVERLAP = 2
#: A refusal additionally needs THIS many *subject* terms in common — shared
#: distinctive words (sha-256, money, currencies), not the wording every
#: request carries. 3 generic shared terms alone are name-similarity, not the
#: same capability: "compute the Adler-32 checksum of a UTF-8 string" vs an
#: existing sha-256 tool must reach the draft/no-client path, while a true
#: duplicate ("convert money amounts between currencies" vs money_convert)
#: still refuses. (audit 62 #7)
_COMPOSE_SUBJECT_OVERLAP = 2
#: The generic vocabulary a capability sentence always shares — articles,
#: conjunctions, filler verbs. Sharing these is never evidence of a duplicate
#: (widens capability_mining._BOILERPLATE to the words a *request* adds).
_COMPOSE_FILLER = frozenset(
    {
        "the", "and", "for", "with", "from", "that", "this", "into", "onto",
        "over", "under", "using", "use", "used", "via", "per", "about", "against",
        "compute", "calculate", "convert", "generate", "create", "make", "build",
        "run", "perform", "process", "handle", "provide", "support", "produce",
        "output", "given", "when", "while", "where", "within", "without",
        "each", "one", "two", "some", "any", "all", "new", "can", "may",
        "should", "would", "will", "its", "are", "was", "were", "been", "being",
        "have", "has", "had", "does", "did", "done", "what", "which", "whose",
        "here", "there", "also", "only", "just", "than", "then", "them", "their",
        "they", "these", "those", "such", "very", "much", "more", "most", "other",
        "between", "because", "instead", "either", "both", "every", "never",
        "always", "please", "must", "needs", "want", "wants", "needed",
    }
)

# §1.8 bounded draft budget for a build's single LLM draft (chars/4 estimate).
# The draft prompt declares model_tier=mid (prompts/tool_spec_draft.v1.md); the
# small limit mirrors the same guard for callers that override the tier.
DRAFT_BUDGET_LIMITS: dict[str, int] = {"small": 4000, "mid": 4000}

#: §4d: the outbox consumer this builder drains under (stable name — recovery
#: and the discovery cache both read the same table).
PUBLISH_CONSUMER = "godsv-buildflow"

#: Manifest v2 (B6): the docker-runtime extension of the Welle-2 tool manifest.
#: The deterministic pipeline generates these fields when ``runtime="docker"``;
#: version bump semantics stay those of the existing buildflow state machine.
MANIFEST_V2_RUNTIME = "docker"

_DIGEST_SHAPE = re.compile(r"^sha256:[0-9a-f]{64}$")


def manifest_v2_fields() -> dict:
    """The Manifest v2 docker-extension fields with their strict defaults."""
    return {
        "runtime": MANIFEST_V2_RUNTIME,
        "image_digest": "",
        "entrypoint_argv": [],
        "mounts": [],
        "resource_limits": {},
        "require_bind_ro": True,
        "healthcheck": {},
    }


def validate_manifest_v2(manifest: dict) -> None:
    """Validate the docker extension of a tool manifest (fail-closed).

    Raises ``ValueError`` when the runtime declaration is used without a
    pin-shaped digest, or a mount lacks either path.
    """
    runtime = str((manifest or {}).get("runtime", "") or "")
    if runtime != MANIFEST_V2_RUNTIME:
        return  # manifest v1 (local python tool) carries no docker fields
    digest = str(manifest.get("image_digest", "") or "")
    if not _DIGEST_SHAPE.match(digest):
        raise ValueError(
            f"runtime=docker requires a pin-shaped image_digest "
            f"(sha256:[64 hex]), got {digest!r} — an untraceable mutable tag "
            "is rejected"
        )
    for mount in manifest.get("mounts") or []:
        if not (mount.get("host_path") and mount.get("container_path")):
            raise ValueError(f"mount needs host_path AND container_path: {mount!r}")


def validate_build_mounts(mounts: list[dict], *, workdir: Path) -> list[str]:
    """BUILD-time mount allowlist: the tool's own paths only (B6 rule)."""
    return _runtime_validate_mounts(mounts, workdir=Path(workdir))


@dataclass
class ToolBuildRequest:
    """What the main agent (or a human) asks the builder to create."""

    capability: str
    reason: str = ""
    requested_by: str = "main-agent"
    kind: str = "auto"  # auto | mcp | cli
    name: str = ""
    # B6: container image the build uses (tagged source, pinned by digest at build)
    image: str = ""
    source_code: str = ""  # generated module code (or use source_path)
    source_path: str = ""
    tests_code: str = ""  # generated tests (or use tests_path)
    tests_path: str = ""
    module: str = ""
    entrypoint: str = ""
    sample_input: dict = field(default_factory=dict)
    research_notes: str = ""
    required_secrets: list[str] = field(default_factory=list)  # secret:// handles only
    allowed_domains: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    risk_level: str = "low"
    session_id: str = ""
    # §7: stable project id of the originating workspace ("" = unknown; the
    # tool then stays project-scoped but unassigned — never silently global)
    project_id: str = ""
    # (target, expect_pass) cases a testing tool MUST agree with (P1/B4);
    # empty + kind=testing_tool → not registrable ("needs_validation")
    validation_cases: list[tuple[str, bool]] = field(default_factory=list)
    # B6 holdout: the examples the capability was DERIVED from (mining) and the
    # inputs it must prove itself on. Synthesis != validation is enforced.
    synthesis_examples: list[str] = field(default_factory=list)
    holdout_inputs: list[dict] = field(default_factory=list)
    # --- §4a/§4b (B7): persistent build flow -------------------------------
    #: goal revision of the calling session (part of its subscription — a late
    #: build must never slip into a changed task)
    goal_revision: int = 0
    #: acceptance criteria stated by the caller (validated, never "exit 0")
    acceptance: list[str] = field(default_factory=list)
    #: holdout cases that were OPENED for diagnosis — they are burned and count
    #: as known regression tests, so the next acceptance needs fresh cases
    opened_for_diagnosis: list[str] = field(default_factory=list)
    #: time budget of the whole build in seconds (0 = DEFAULT_BUILD_BUDGET_SECONDS)
    budget_seconds: float = 0.0
    #: repair cap within that budget (configurable, default 3 — §4b)
    repair_limit: int = bf.DEFAULT_REPAIR_LIMIT
    # --- B6 (Manifest v2): docker runtime extension ------------------------
    #: requested container runtime ("" = local python tool, "docker" = pinned
    #: image). Docker tools carry their own evidence: pins, mounts, limits.
    runtime: str = ""
    #: the pinned digest the build MUST produce/verify (the pull/pin target)
    image_digest: str = ""
    #: explicit `--pull`: ask the registry even when the image is already local
    #: (the default is HUB-INDEPENDENT: a local digest is the pin, no network)
    pull: bool = False
    #: manifest v2 mounts ({host_path, container_path, readonly}) — build-time
    #: allowlist validates host_path stays inside the tool's own scratch dir
    mounts: list[dict] = field(default_factory=list)
    #: container caps {cpu, ram} turned into --cpus/--memory at build run
    resource_limits: dict = field(default_factory=dict)
    #: build-time health probe (argv of the container CLI) for a service tool
    healthcheck: dict = field(default_factory=dict)


def choose_kind(
    capability: str, *, api_functions: int = 1, wraps_existing_cli: bool = False
) -> str:
    """Decide MCP vs CLI deliberately (spec §7), not because MCP exists."""
    text = (capability or "").lower()
    if wraps_existing_cli:
        return "cli"
    if api_functions >= 2 or any(hint in text for hint in _MCP_HINTS):
        return "mcp"
    if any(hint in text for hint in _CLI_HINTS):
        return "cli"
    return "cli" if len(text) < 60 else "mcp"


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "tool").lower()).strip("_")
    return (slug or "tool")[:40]


def inspect_dependencies(source_text: str, declared: list[str]) -> dict:
    """AST import inspection: everything outside stdlib/deps must be declared."""
    try:
        tree = ast.parse(source_text)
    except SyntaxError as exc:
        return {"ok": False, "imports": [], "undeclared": [], "error": f"syntax: {exc.msg}"}
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imports.add(node.module.split(".")[0])
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    allowed = stdlib | set(declared) | {"skaldir", "godsv"}
    undeclared = sorted(
        name for name in imports
        if name not in allowed and not name.startswith(("skaldir", "godsv"))
    )
    return {"ok": not undeclared, "imports": sorted(imports), "undeclared": undeclared}


class ToolBuilder:
    """The deterministic build pipeline around one capability request."""

    def __init__(
        self,
        runtime: str | Path,
        *,
        store: ToolStore | None = None,
        events: EventStore | None = None,
        session_id: str = "",
        llm: LLMClient | None = None,
        budget: BudgetTracker | None = None,
        prompts: PromptRegistry | None = None,
        flow: bf.BuildFlowStore | None = None,
        owner: str = "",
        manager: DockerRuntimeManager | None = None,
    ) -> None:
        self.runtime = Path(runtime)
        self.session_id = str(session_id or "")
        self.tools_dir = self.runtime / "generated_tools"
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self.store = store if store is not None else ToolStore(self.runtime / "tools.db")
        self.events = events
        self.queue_path = self.runtime / "tool_builds.jsonl"
        # §1.8: the DRAFT uses the caller's already-configured model client —
        # the builder never constructs a provider client of its own (no second
        # provider configuration). No client = the caller's blocker to report.
        self.llm = llm
        self.budget = budget
        self.prompts = prompts if prompts is not None else PromptRegistry()
        # the tracker that bounded the most recent draft (injected or default)
        self.last_draft_budget: BudgetTracker | None = None
        # §4a: the persistent build flow (leases, phases, recovery). Injected by
        # tests; otherwise one SQLite file per runtime dir, created on demand.
        self.flow: bf.BuildFlowStore | None = flow
        self.owner = str(owner or "")
        # B6: the docker runtime of a container build. Injected by tests, created
        # on demand otherwise — the docker stages never construct their own docker.
        self._manager: DockerRuntimeManager | None = manager
        self.repairs: dict[str, bf.RepairLoop] = {}

    def manager(self) -> DockerRuntimeManager:
        """The docker runtime of this builder (lazy; injected in tests)."""
        self._manager = _manager_of(self._manager)
        return self._manager

    # -- persistent flow plumbing (§4a) ---------------------------------------
    def _flow_store(self) -> bf.BuildFlowStore:
        """The build-flow store of this runtime (lazy: no DB for pure reads)."""
        if self.flow is None:
            self.flow = bf.BuildFlowStore(self.runtime / "buildflow.db")
        return self.flow

    def _enter(self, build_id: str, phase: str, result: dict, **detail: object) -> bool:
        """Persist the transition INTO a phase BEFORE its external action.

        Fail-closed: a flow store that cannot record the transition stops the
        build (a build whose state cannot be reconstructed after a crash is
        exactly what §4a forbids).

        Repair re-entry: a resumed build (same build_id after a recorded repair)
        has already passed the earlier phases — those entries are no-ops instead
        of backwards transitions; the current phase is simply re-entered.
        """
        flow = self._flow_store()
        try:
            current = flow.get(build_id)
            if current is not None and current.terminal:
                # cancelled/closed from the outside: every later phase entry is
                # a cancellation point — the build stops here, nothing lands.
                result["error"] = (
                    f"build is closed ({current.phase}): {current.reason or 'no reason recorded'}"
                )
                result["flow_closed"] = current.phase
                if current.phase == "cancelled":
                    result["cancelled"] = True
                return False
            if (
                current is not None
                and bf.PHASES.index(phase) <= bf.PHASES.index(current.phase)
            ):
                return True
            flow.enter(build_id, phase, detail=dict(detail))
        except Exception as exc:
            result["error"] = (
                f"build flow cannot persist phase '{phase}': {type(exc).__name__}: {exc}"
            )
            result["blocked"] = "flow_store"
            return False
        return True

    def _finish(
        self,
        build_id: str,
        result: dict,
        outcome: str,
        *,
        reason: str,
        artifacts: list | None = None,
        next_step: str = "",
        error: str = "",
    ) -> dict:
        """Close the flow with a COMPLETE error state (never a bare failure)."""
        try:
            self._flow_store().finish(
                build_id,
                outcome,
                reason=reason,
                artifacts=[str(item) for item in (artifacts or [])],
                next_step=next_step,
                error=error or reason,
            )
        except Exception as exc:  # the outcome is reported in the result either way
            logging.getLogger("skaldir.toolbuilder").warning(
                "flow outcome write failed (%s: %s)", type(exc).__name__, exc
            )
        result["flow_outcome"] = outcome
        result.setdefault("next_step", next_step)
        return result

    def _halt(
        self,
        build_id: str,
        result: dict,
        *,
        started: float,
        budget_seconds: float,
        artifacts: list | None = None,
    ) -> dict | None:
        """Cancellation/budget gate between phases (§4a: cancel + budget)."""
        record = self._flow_store().get(build_id)
        if record is not None and record.phase == "cancelled":
            result["error"] = f"build cancelled: {record.reason or 'no reason recorded'}"
            result["cancelled"] = True
            return result
        spent = time.time() - started
        if budget_seconds and spent > budget_seconds:
            reason = f"time budget exhausted ({spent:.1f}s > {budget_seconds:.0f}s)"
            result["error"] = reason
            result["blocked"] = "budget"
            return self._finish(
                build_id, result, "failed", reason=reason, artifacts=artifacts,
                next_step="raise the budget or narrow the need", error=reason,
            )
        return None

    # -- §4d publication / recovery / bounded repair --------------------------
    def _emit_publish_event(self, spec: ToolSpec, receipt: dict) -> dict:
        """Best-effort ``tool_published`` event for a published capability.

        The outbox entry is the DURABLE fact (same transaction as the registry
        write); this event is the notification B9 and the chat feed consume. A
        failed dispatch stays unacked, so :meth:`recover` drains it later.
        """
        out = {"emitted": False, "acknowledged": False, "error": ""}
        if receipt.get("duplicate"):
            out["error"] = "duplicate publication — no second event"
            return out
        if self.events is None:
            out["error"] = "no event store configured (outbox entry stays for recovery)"
            return out
        try:
            self.events.append_redacted(
                self._session("panel-builds"),
                int(time.time() * 1000) % 10**9,
                "tool_published",
                {
                    "name": spec.name,
                    "version": spec.version,
                    "build_id": receipt.get("outbox_id"),
                    "registry_revision": receipt.get("revision"),
                    "status": spec.status,
                },
            )
            out["emitted"] = True
        except Exception as exc:
            out["error"] = f"{type(exc).__name__}: {exc}"
            return out
        try:
            self.store.ack_outbox(PUBLISH_CONSUMER, [int(receipt["outbox_id"])])
            out["acknowledged"] = True
        except Exception as exc:
            out["error"] = f"ack failed: {type(exc).__name__}: {exc}"
        return out

    def recover(self, *, registry: object = None) -> dict:
        """Crash recovery: reconcile open phases, then drain pending publications.

        A NEW builder instance (new process) reads the persisted state, closes
        what reality says is closed and reports the rest — never a silent green.
        """
        flow = self._flow_store()
        if callable(registry):
            check = registry
        else:
            def check(name: str) -> bool:
                return self.store.get(str(name)) is not None

        report = flow.recover(registry=check)
        drained: list[str] = []
        try:
            entries = self.store.drain_outbox(PUBLISH_CONSUMER, ack=False)
            for entry in entries:
                emitted = self._emit_publish_event(
                    ToolSpec(
                        name=entry["name"], title=entry["name"],
                        version=entry["version"], status=STATUS_APPROVED,
                    ),
                    {
                        "outbox_id": entry["id"],
                        "duplicate": False,
                        "revision": self.store.registry_revision(),
                    },
                )
                if emitted.get("emitted"):
                    self.store.ack_outbox(PUBLISH_CONSUMER, [entry["id"]])
                    drained.append(entry["name"])
                else:
                    report.setdefault("drain_failed", []).append(
                        {"name": entry["name"], "error": emitted.get("error", "")}
                    )
        except Exception as exc:
            report["drain_error"] = f"{type(exc).__name__}: {exc}"
        report["drained"] = drained
        report["pending_publications"] = self.store.pending_outbox(PUBLISH_CONSUMER)
        return report

    def repair(
        self,
        build_id: str,
        *,
        defect: str,
        change: str,
        checks: list[str],
        failure_kind: str = "adapter",
        kind: str = "adapter",
        evidence_new: bool = False,
        changes: list[str] | None = None,
    ) -> dict:
        """Record one BOUNDED repair attempt of a failed build (§4b).

        Refuses (raises) when the cap is reached, when the same defect comes
        back without new evidence, or when an infrastructure failure would be
        'repaired' by rewriting the adapter. ``changes`` names what was touched
        (rights/image/input_schema/dependencies/…) so the affected gates are
        re-triggered instead of a blanket re-run.
        """
        loop = self._repair_loop(build_id, limit=bf.DEFAULT_REPAIR_LIMIT)
        attempt = loop.record(
            defect=defect,
            change=change,
            checks=list(checks),
            failure_kind=failure_kind,
            kind=kind,
            evidence_new=evidence_new,
        )
        gates = bf.gates_to_rerun(list(changes or ["adapter_code"]))
        state = loop.state()
        try:
            flow = self._flow_store()
            flow.note(
                build_id,
                {
                    "repair": attempt.to_dict(),
                    "changes": list(changes or ["adapter_code"]),
                    "gates_to_rerun": gates,
                },
                kind="repair",
            )
            flow.transition(
                build_id, "adapter",
                repair_attempt=attempt.attempt,
                detail={"repair_attempt": attempt.attempt, "changes": changes or []},
            )
        except Exception as exc:  # a closed flow may legitimately refuse the edge
            logging.getLogger("skaldir.toolbuilder").warning(
                "repair transition not recorded (%s: %s)", type(exc).__name__, exc
            )
        return {
            "attempt": attempt.to_dict(),
            "state": state,
            "gates_to_rerun": gates,
            "next_step": (
                f"re-run the affected gates ({', '.join(gates)}) and the build's "
                "verification after the repair"
            ),
        }

    def observe(self, build_id: str, *, detail: dict | None = None) -> dict:
        """First real call/result/health of a published capability (§4a)."""
        record = self._flow_store().observe(build_id, detail=detail or {})
        return {
            "build_id": record.build_id,
            "phase": record.phase,
            "observed_at": record.observed_at,
            "status": record.status_text,
        }

    def flow_state(self, build_id: str) -> dict | None:
        record = self._flow_store().get(build_id)
        return record.to_dict() if record is not None else None

    def _repair_loop(self, build_id: str, *, limit: int) -> bf.RepairLoop:
        """The bounded repair loop of this build (§4b, cap configurable)."""
        loop = self.repairs.get(build_id)
        if loop is None:
            loop = bf.RepairLoop(limit=int(limit or bf.DEFAULT_REPAIR_LIMIT))
            self.repairs[build_id] = loop
        return loop

    def _stop_if_flow_says(self, build_id: str, result: dict, **halt: object) -> dict | None:
        """Between-phases gate: cancelled by an owner OR out of budget."""
        return self._halt(build_id, result, **halt)  # type: ignore[arg-type]

    # -- queue -----------------------------------------------------------------
    def _record(self, build_id: str, stage: str, **extra: object) -> dict:
        entry = {"build_id": build_id, "stage": stage, "ts": time.time(), **extra}
        with self.queue_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        if self.events is not None:
            try:
                self.events.append(
                    self._session("panel-builds"), int(time.time() * 1000) % 10**9,
                    f"tool_build_{stage}", {k: v for k, v in entry.items() if k != "ts"},
                )
            except Exception as exc:  # never fail a build over a mirror event,
                import logging  # but never swallow it silently either (P1/B1)

                logging.getLogger("skaldir.toolbuilder").warning(
                    "build event mirror failed (%s: %s)", type(exc).__name__, exc
                )
        return entry

    def _session(self, fallback: str) -> str:
        """The session that owns this build (the requesting chat, when known)."""
        return self.session_id or fallback

    def queue(self, limit: int = 50) -> list[dict]:
        """Latest state per build (newest first); stages kept in order."""
        if not self.queue_path.exists():
            return []
        by_id: dict[str, dict] = {}
        for line in self.queue_path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            build_id = str(entry.get("build_id", ""))
            record = by_id.setdefault(
                build_id,
                {"build_id": build_id, "stages": [], "stage": "", "capability": "", "updated": 0.0},
            )
            record["stages"].append({"stage": entry.get("stage", ""), "ts": entry.get("ts", 0)})
            record["stage"] = entry.get("stage", record["stage"])
            record["updated"] = max(record["updated"], float(entry.get("ts", 0) or 0))
            for key in (
                "capability", "kind", "name", "reason", "error", "ok", "session_id",
                "compose_check", "draft",
            ):
                if key in entry:
                    record[key] = entry[key]
        ordered = sorted(by_id.values(), key=lambda item: item["updated"], reverse=True)
        return ordered[:limit]

    # -- pipeline --------------------------------------------------------------
    def compose_check(self, *, name: str = "", capability: str = "") -> dict:
        """§1.7: is this capability already covered by an existing tool?

        Decision (deterministic term overlap over the registry, no LLM):

        * ``refuse``  — an approved tool shares >= 3 terms AND >=
          ``_COMPOSE_SUBJECT_OVERLAP`` of them are *subject* terms (shared
          distinctive words) — that is the same capability (duplicate),
        * ``hint``    — >= 2 terms, or a >= 3 term overlap whose shared words
          are all generic wording: advisory only, never blocking. This is
          what keeps "compute the Adler-32 checksum of a UTF-8 string" from
          being refused against an existing sha-256 tool (audit 62 #7) —
          name-similarity must not make the draft/no-client path unreachable,
        * ``clear``   — no overlap: a genuinely new capability.

        The scoring reuses capability_mining's boilerplate vocabulary, so
        generic wording ("inspect/verify/state") never adds up to a duplicate
        — the same conservatism as ``search_before_build``. The build's own
        name is skipped: rebuilding tool X is an update, and a true duplicate
        of X is already handled by search-before-build. Every candidate names
        the subject terms it actually shares, so a refusal can be checked
        against its own evidence.
        """
        from .capability_mining import _BOILERPLATE
        from .tools import _tokens

        def _subject(text: str) -> set[str]:
            return {
                term
                for term in _tokens(text)
                if term not in _BOILERPLATE and term not in _COMPOSE_FILLER
            }

        scored = compose_candidates_scored(
            self.store,
            description=capability,
            min_score=_COMPOSE_HINT_OVERLAP,
            ignore=set(_BOILERPLATE),
        )
        wanted = _subject(capability)
        candidates = [
            {
                "tool": spec.name,
                "overlap": score,
                "surface": "tools.db",
                "kind": spec.kind,
                "shared_terms": sorted(
                    wanted
                    & _subject(
                        " ".join(
                            [spec.name, spec.title, spec.description, " ".join(spec.tags)]
                        )
                    )
                ),
            }
            for score, spec in scored
            if spec.name != name
        ]
        if (
            candidates
            and candidates[0]["overlap"] >= _COMPOSE_REFUSE_OVERLAP
            and len(candidates[0]["shared_terms"]) >= _COMPOSE_SUBJECT_OVERLAP
        ):
            decision = "refuse"
        elif candidates:
            decision = "hint"
        else:
            decision = "clear"
        return {
            "decision": decision,
            "threshold": _COMPOSE_REFUSE_OVERLAP,
            "hint_threshold": _COMPOSE_HINT_OVERLAP,
            "subject_threshold": _COMPOSE_SUBJECT_OVERLAP,
            "candidates": candidates,
        }

    def build(self, request: ToolBuildRequest, *, build_id: str = "") -> dict:
        build_id = build_id or f"build-{uuid.uuid4().hex[:10]}"
        kind = (
            request.kind
            if request.kind in ("mcp", "cli", "testing_tool")
            else choose_kind(request.capability)
        )
        name = _slug(request.name or request.capability)
        started = time.time()
        budget_seconds = float(request.budget_seconds or bf.DEFAULT_BUILD_BUDGET_SECONDS)
        result: dict = {
            "build_id": build_id, "name": name, "kind": kind,
            "capability": request.capability, "ok": False, "error": "",
        }

        # -- §4b: acceptance criteria come from the user need, BEFORE the adapter.
        # "Websuche" is not "a researched report", HTTP 200 is not acceptance.
        need = request.capability or name
        acceptance = bf.AcceptanceSet(
            criteria=bf.derive_acceptance_criteria(need, declared=list(request.acceptance)),
            builder_cases=[{"target": json.dumps(request.sample_input, sort_keys=True),
                            "expect_pass": True}] if request.sample_input else [],
            holdout_cases=[
                {"target": str(target), "expect_pass": bool(expect)}
                for target, expect in request.validation_cases
            ],
        )
        for case_id in request.opened_for_diagnosis:
            acceptance.open_for_diagnosis(
                str(case_id), reason="opened for diagnosis — now a known regression test"
            )
        result["acceptance"] = acceptance.state()
        criteria_problems = bf.acceptance_problems(acceptance.criteria)

        # -- §4a: persistence, idempotency and the worker lease come first. Two
        # identical requests (normalized need + project + versions) admit to ONE
        # build; the second request subscribes instead of duplicating the work.
        flow = self._flow_store()
        owner = self.owner or f"builder-{os.getpid()}"
        source_kind = (
            "source" if (request.source_code or request.source_path)
            else ("draft" if self.llm is not None else "none")
        )
        existing = flow.get(build_id)
        if existing is not None and not existing.terminal:
            # repair re-entry: continue THIS build — its repair log, budget and
            # subscriptions stay in force (§4b: the cap spans the repair rounds).
            record = existing
            result["resumed_from"] = existing.phase
            flow.subscribe(
                build_id, request.session_id or self.session_id,
                goal_revision=request.goal_revision,
            )
        else:
            record, created = flow.admit(
                need=need,
                build_id=build_id,
                project=request.project_id,
                versions=list(request.dependencies),
                session_id=request.session_id or self.session_id,
                goal_revision=request.goal_revision,
                source=source_kind,
                budget={"seconds": budget_seconds, "repairs": int(request.repair_limit)},
                runtime=getattr(request, "runtime", "") or "",
                owner=owner,
                tool_name=name,
            )
            if not created:
                result["build_id"] = record.build_id
                result["subscribed"] = True
                result["flow"] = {
                    "phase": record.phase, "status": record.status_text,
                    "owner": record.owner, "subscribers": flow.subscribers(record.build_id),
                }
                result["error"] = (
                    f"identical build already running ({record.build_id}, phase "
                    f"{record.phase}) — this request subscribes to it instead of "
                    "building a duplicate"
                )
                self._record(
                    record.build_id, "requested", capability=request.capability,
                    requested_by=request.requested_by, kind=kind, name=name,
                    session_id=request.session_id, subscribed=True,
                )
                return result
        result["build_id"] = record.build_id

        # -- phase `need`: make the need concrete (effect, inputs, output,
        # criteria, limits) and persist it BEFORE anything is searched or built.
        self._record(
            result["build_id"], "requested",
            capability=request.capability, reason=request.reason,
            requested_by=request.requested_by, kind=kind, name=name,
            session_id=request.session_id, phase="need",
        )
        flow.step_done(
            result["build_id"], "need",
            detail={
                "effect": need,
                "inputs": sorted(str(key) for key in request.sample_input),
                "output": request.entrypoint or kind,
                "criteria": acceptance.criteria,
                "limits": {
                    "domains": list(request.allowed_domains),
                    "secrets": list(request.required_secrets),
                    "dependencies": list(request.dependencies),
                    "risk_level": request.risk_level,
                    "budget_seconds": budget_seconds,
                    "repair_limit": int(request.repair_limit),
                },
                "source": source_kind,
                "acceptance_problems": criteria_problems,
            },
        )
        if criteria_problems:
            result["error"] = (
                "acceptance is not about the need: " + "; ".join(criteria_problems)
            )
            result["blocked"] = "acceptance"
            self._record(result["build_id"], "failed", error=result["error"])
            return self._finish(
                result["build_id"], result, "blocked", reason=result["error"],
                next_step="state the observable effect the capability must have",
            )

        # -- phase `search`: what already exists (documented, with reasons).
        if not self._enter(result["build_id"], "search", result, need=need):
            return result
        self._record(
            result["build_id"], "researching", phase="search",
            note=(request.research_notes[:400] or "research notes supplied by caller"),
        )

        # B6/TOOLS-27+35: search before build. Everything that already exists —
        # generated tools, testing tools, skills, MCP servers, workflows, tool
        # hub — is searched first; a real duplicate is REFUSED (reuse instead),
        # it is not built a second time. The refusal demands a STRONG match
        # (3 terms): a weak 2-term report stays advisory, so legitimate new
        # work is never blocked by a keyword coincidence.
        from .capability_mining import _BUILD_REFUSE_OVERLAP, search_before_build

        verdict = search_before_build(
            self.runtime,
            request.capability,
            name=name,
            min_overlap=_BUILD_REFUSE_OVERLAP,
        )
        result["search"] = {
            "decision": verdict["decision"],
            "searched": verdict["searched"],
            "match": verdict["match"],
        }
        match_info = verdict.get("match") or {}
        search_reason = (
            f"'{match_info.get('name')}' on {match_info.get('surface')} already "
            "provides this capability"
            if match_info.get("name")
            else "no existing tool/skill/mcp/workflow covers this need"
        )
        flow.step_done(
            build_id, "search",
            detail={
                "decision": verdict["decision"],
                "searched": verdict["searched"],
                "match": verdict["match"],
                "reasons": search_reason,
            },
        )
        if verdict["decision"] == "reuse":
            match = verdict["match"] or {}
            result["error"] = (
                f"reuse instead of duplicate: '{match.get('name')}' "
                f"({match.get('surface')}) already provides this capability"
            )
            result["reuse_recommended"] = match
            result["redirect"] = ToolBuilder.redirect_refusal(
                request,
                reason=result["error"],
                candidates=[
                    {
                        "tool": str(match.get("name", "")),
                        "overlap": int(match.get("score", 0) or 0),
                        "surface": str(match.get("surface", "")),
                    }
                ],
            )
            self._record(
                build_id, "failed", error=result["error"], reuse_recommended=match
            )
            return self._finish(
                build_id, result, "superseded", reason=result["error"],
                next_step=(
                    f"use the existing '{match.get('name')}' ({match.get('surface')}) "
                    "— reuse instead of building a second tool"
                ),
            )

        # §1.7 compose-before-create: the registry (tools.db) is searched for
        # reusable/composable APPROVED tools before anything is written. A
        # 3-term overlap with an existing capability is a duplicate → refuse;
        # 2 terms is only a hint in the record. Same conservatism as
        # capability_mining._BUILD_REFUSE_OVERLAP.
        if not self._enter(build_id, "decide", result, candidates="registrierter Bestand"):
            return result
        compose = self.compose_check(name=name, capability=request.capability)
        self._record(build_id, "compose_check", compose_check=compose, phase="decide")
        expected_cost = {
            "kind": kind,
            "source": source_kind,
            "budget_seconds": budget_seconds,
            "repair_limit": int(request.repair_limit),
            "reuse_threshold": _COMPOSE_REFUSE_OVERLAP,
        }
        flow.step_done(
            build_id, "decide",
            detail={
                "decision": compose["decision"],
                "expected_cost": expected_cost,
                "candidates": compose["candidates"],
                "reasons": (
                    "a name difference is not a reason to build"
                    if compose["decision"] != "clear" else "genuinely new capability"
                ),
            },
        )
        if compose["decision"] == "refuse":
            top = compose["candidates"][0]
            shared = ", ".join(top.get("shared_terms") or []) or "generic wording"
            result["error"] = (
                f"compose before create: '{top['tool']}' already covers this capability "
                f"({top['overlap']} shared terms: {shared}) — reuse or compose it instead "
                "of building a duplicate"
            )
            result["compose_check"] = compose
            result["compose_recommended"] = compose["candidates"]
            result["redirect"] = ToolBuilder.redirect_refusal(
                request, reason=result["error"], candidates=compose["candidates"]
            )
            self._record(
                build_id, "failed", error=result["error"], compose_check=compose
            )
            return self._finish(
                build_id, result, "superseded", reason=result["error"],
                next_step=(
                    f"reuse or compose '{top['tool']}' instead of building a duplicate"
                ),
            )

        from . import capability_health as health

        health.record_building(self.runtime, name, reason=request.capability[:120])

        source_text = request.source_code
        if not source_text and request.source_path:
            try:
                source_text = Path(request.source_path).read_text(encoding="utf-8")
            except OSError as exc:
                result["error"] = f"source not readable: {exc}"
                self._record(build_id, "failed", error=result["error"])
                return self._finish(
                    build_id, result, "failed", reason=result["error"],
                    next_step="check the source path and retry",
                )
        if not source_text:
            # §1.8: no source — either the existing configured model client
            # drafts a spec, or this is a precise blocker. Never invented code.
            return self._draft_without_source(
                build_id, request, result, name=name, compose_check=compose
            )
        tool_dir = self.tools_dir / name
        module = request.module or f"godsv_tool_{name}"
        source_file = tool_dir / f"{module}.py"
        tests_text = request.tests_code
        if not tests_text and request.tests_path:
            tests_text = Path(request.tests_path).read_text(encoding="utf-8")
        test_command: list[str] = []
        if tests_text:
            test_command = [sys.executable, "-m", "pytest", "-q", "tests/"]
        manifest = {
            "name": name,
            "version": "0.1.0",
            # §7: new learned capabilities are project-scoped by default;
            # "" = no provable project context yet -> never silently global
            # (visible only in the explicit "show everything" view).
            "scope": "project",
            "project_id": request.project_id,
            "type": kind,
            "purpose": request.capability,
            "created_for": request.reason or request.capability,
            "created_by": "godsv-toolbuilder",
            "commands_or_tools": [request.entrypoint or module],
            "network": {"allowed_domains": list(request.allowed_domains)},
            "filesystem": {"access": "sandboxed workdir"},
            "required_secrets": list(request.required_secrets),
            "dependencies": list(request.dependencies),
            "tests": test_command,
            "risk_level": request.risk_level,
            "status": "built",
            "created_at": time.time(),
            "last_tested_at": None,
            # §4b: acceptance criteria were derived from the need BEFORE the
            # adapter existed — they are part of the manifest, not an afterthought.
            "acceptance_criteria": list(acceptance.criteria),
            "build_id": build_id,
        }
        # B6/Manifest v2: for a docker request the manifest carries the pinned
        # digest + build the container contract itself (validated fail-closed).
        request_runtime = getattr(request, "runtime", "") or ""
        if request_runtime == MANIFEST_V2_RUNTIME:
            manifest.update(manifest_v2_fields())
            manifest["image_digest"] = str(getattr(request, "image_digest", "") or "")
            manifest["mounts"] = list(getattr(request, "mounts", []) or [])
            manifest["resource_limits"] = dict(getattr(request, "resource_limits", {}) or {})
            manifest["entrypoint_argv"] = list(
                [request.entrypoint] if request.entrypoint else []
            ) or list(getattr(request, "entrypoint_argv", []) or [])
            manifest["healthcheck"] = dict(getattr(request, "healthcheck", {}) or {})
            try:
                validate_manifest_v2(manifest)
            except ValueError as exc:
                result["error"] = f"manifest v2 invalid: {exc}"
                result["blocked"] = "manifest_v2"
                self._record(result["build_id"], "failed", error=result["error"])
                return self._finish(
                    result["build_id"], result, "blocked", reason=result["error"],
                    next_step=(
                        "pin the image by digest (sha256:…) and declare every mount "
                        "with both paths"
                    ),
                )
        manifest_path = tool_dir / "manifest.yaml"

        def _write_failure_manifest() -> None:
            tool_dir.mkdir(parents=True, exist_ok=True)
            source_file.write_text(source_text, encoding="utf-8")
            manifest["status"] = "failed"
            manifest_path.write_text(
                yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
            )

        # -- phase `inspect`: source-bound inspection BEFORE the adapter exists.
        # Undeclared imports are a contract gap, not something to paper over.
        # B6: a docker tool additionally has its IMAGE committed in this phase —
        # the LOCAL digest is the pin whenever this daemon already carries the
        # image (no registry round trip), and the Hub is asked only when the
        # image is genuinely absent or the caller explicitly asks (request.pull).
        # Measured 2026-09-18: an unconditional `docker pull` failed every build
        # probe that only needed a local digest, because docker.io answered
        # `429 Too Many Requests` — the build must not depend on the Hub.
        # An infrastructure failure here is exactly that, and is recorded with
        # its taxonomy class instead of being smoothed into an adapter error.
        image_probe: dict = {}
        if request_runtime == MANIFEST_V2_RUNTIME:
            from .docker_runtime import verify_pinned_digest

            # the builder's OWN runtime (injected in tests), never a private one:
            # a second real manager here bypassed every injected fake.
            mgr = self.manager()
            available, detail = mgr.available()
            image_probe["daemon"] = {"ok": available, "detail": detail[:200]}
            if not available:
                result["error"] = f"docker daemon not reachable: {detail}"
                result["blocked"] = "docker_unavailable"
                manifest["status"] = "failed"
                self._record(result["build_id"], "failed", error=result["error"])
                return self._finish(
                    result["build_id"], result, "blocked", reason=result["error"],
                    artifacts=[str(manifest_path)],
                    next_step="start the docker daemon (or systemd --user docker), then retry",
                    error=result["error"],
                )
            image = str(request.image or "") or "alpine:3.20"
            digest_hint = str(manifest.get("image_digest", "") or "") or None
            want_pull = bool(getattr(request, "pull", False))
            local_digest = str(mgr.resolve_digest(image) or "").strip()
            image_probe["local_digest"] = local_digest
            if local_digest and not want_pull:
                # the image is already here: the local digest IS the pin. The
                # pinned hint is still verified against it (divergence stays fatal).
                if digest_hint:
                    verify_pinned_digest(
                        actual=local_digest, expected=digest_hint, image=image
                    )
                pinned = local_digest
                image_probe["pin_source"] = "local"
                image_probe["pulled"] = False
            else:
                pulled = mgr.pull(image)
                image_probe["pin_source"] = "pull"
                image_probe["pulled"] = True
                if not pulled.ok:
                    failure_class = pulled.failure_class or "docker_error"
                    result["error"] = (
                        f"docker pull/pin failed ({failure_class}): "
                        f"{pulled.detail or 'no detail'}"
                    )
                    result["blocked"] = "docker_pull"
                    result["failure_class"] = failure_class
                    manifest["status"] = "failed"
                    self._record(result["build_id"], "failed", error=result["error"])
                    return self._finish(
                        result["build_id"], result, "blocked", reason=result["error"],
                        artifacts=[str(manifest_path)],
                        next_step=(
                            "name an image AND ARCHITECTURE that exists (check the pull "
                            "taxonomy: image_missing / arch_mismatch / quota_limit); a "
                            "quota_limit is the registry rate-limiting this machine — "
                            "wait, or build from the digest already local"
                        ),
                        error=str(pulled.detail)[:400],
                    )
                pinned = str(pulled.stdout or "").strip()
                if digest_hint:
                    verify_pinned_digest(actual=pinned, expected=digest_hint, image=image)
            image_probe["pinned_digest"] = pinned
            manifest["image_digest"] = pinned
        if not self._enter(build_id, "inspect", result, source=str(source_file)):
            return result
        deps = inspect_dependencies(source_text, request.dependencies)
        self._record(
            build_id, "dependency_inspection", phase="inspect",
            imports=deps.get("imports", []), undeclared=deps.get("undeclared", []),
            ok=deps.get("ok", False),
        )
        result["dependencies"] = deps
        if request_runtime == MANIFEST_V2_RUNTIME:
            # B6: build-time mount allowlist (§2d rule — the tool's own paths).
            mount_problems = validate_build_mounts(
                list(getattr(request, "mounts", []) or []), workdir=tool_dir
            )
            image_probe["mount_problems"] = mount_problems
            if mount_problems:
                result["error"] = (
                    f"docker mounts refused by the build allowlist: {mount_problems}"
                )
                manifest["status"] = "failed"
                self._record(result["build_id"], "failed", error=result["error"])
                return self._finish(
                    result["build_id"], result, "blocked", reason=result["error"],
                    artifacts=[str(manifest_path)],
                    next_step="bind only the tool's own paths (inside generated_tools/<name>)",
                )
        flow.step_done(
            build_id, "inspect",
            detail={
                "imports": deps.get("imports", []),
                "undeclared": deps.get("undeclared", []),
                "ok": deps.get("ok", False),
                "image_probe": image_probe,
                "open_questions": (
                    [] if deps.get("ok", False)
                    else [f"undeclared import: {name}" for name in deps.get("undeclared", [])]
                ),
            },
        )
        if not deps.get("ok", False):
            result["error"] = f"undeclared imports: {deps.get('undeclared')}"
            _write_failure_manifest()
            self._record(build_id, "failed", error=result["error"])
            return self._finish(
                build_id, result, "failed", reason=result["error"],
                artifacts=[str(source_file), str(manifest_path)],
                next_step=(
                    "declare the dependency in the request (dependencies=[…]) or "
                    "rewrite the adapter without it"
                ),
            )

        # -- phase `contract`: the versioned manifest (schemas, limits, rights,
        # acceptance criteria) — NO executable wrapper without a contract.
        if not self._enter(build_id, "contract", result, manifest=str(manifest_path)):
            return result
        tool_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        self._record(build_id, "manifest", phase="contract", path=str(manifest_path))
        result["manifest"] = str(manifest_path)
        flow.step_done(
            build_id, "contract",
            detail={
                "path": str(manifest_path),
                "version": manifest["version"],
                "network": manifest["network"],
                "required_secrets": manifest["required_secrets"],
                "criteria": manifest["acceptance_criteria"],
            },
        )

        # -- phase `adapter`: the generated stand is written down verbatim,
        # against the contract that just went on record.
        if not self._enter(build_id, "adapter", result, module=module):
            return result
        tool_dir.mkdir(parents=True, exist_ok=True)
        source_file.write_text(source_text, encoding="utf-8")
        if tests_text:
            tests_dir = tool_dir / "tests"
            tests_dir.mkdir(exist_ok=True)
            (tests_dir / f"test_{name}.py").write_text(tests_text, encoding="utf-8")
        self._record(
            build_id, "generating", phase="adapter", module=module, source=str(source_file)
        )
        flow.step_done(
            build_id, "adapter",
            detail={"module": module, "source": str(source_file), "tests": bool(tests_text)},
        )
        stopped = self._halt(
            build_id, result, started=started, budget_seconds=budget_seconds,
            artifacts=[str(source_file)],
        )
        if stopped is not None:
            return stopped

        spec = ToolSpec(
            name=name,
            title=request.capability[:80] or name,
            description=request.capability,
            tags=[kind, "generated", "toolbuilder"],
            kind=KIND_PYTHON,
            module=module,
            entrypoint=request.entrypoint,
            test_command=test_command,
            sample_input=dict(request.sample_input),
            rationale=request.reason,
            provenance=f"toolbuilder:{build_id}",
            status=STATUS_CANDIDATE,
            created=time.time(),
            updated=time.time(),
        )
        # -- phase `verify`: syntax/schema/rights/limits/function probes. A
        # failing gate prevents publication — and (with new evidence) opens the
        # BOUNDED repair path instead of an endless retry loop.
        if not self._enter(build_id, "verify", result, gates="scan,import,test,determinism"):
            return result
        report = run_gates(spec, workdir=tool_dir)
        checks = [
            {"name": check.gate, "ok": bool(check.passed), "detail": check.detail}
            for check in report.checks
        ]
        # B6: for docker tools the gates are verified THROUGH the container —
        # the pinned image runs the adapter probe on the tool's own workdir
        # (mount allowlist holds; no host-home, no socket, network none).
        container_probe: dict = {}
        if request_runtime == MANIFEST_V2_RUNTIME:
            # B6 wiring: the docker runtime the probe below needs. Without it the
            # whole container half of the build was unreachable (`"mgr" in
            # locals()` could never be true) — the manager is created here, the
            # only place a container build gets one.
            mgr = self.manager()
            container_probe["docker"] = {
                "available": bool(mgr.available()[0]),
                "detail": mgr.available()[1][:200],
            }
            from .docker_runtime import (
                OWNER_LABEL_KEY as _OWNER_LABEL_KEY,
            )
            from .docker_runtime import (
                OWNER_LABEL_VALUE as _OWNER_LABEL_VALUE,
            )
            from .docker_runtime import (
                PolicyViolation as _PolicyViolation,
            )
            from .docker_runtime import RunConfig as _RunConfig
            from .docker_runtime import (
                policy_refusal as _policy_refusal,
            )

            real_digest = (
                mgr.resolve_digest(request.image or "alpine:3.20")
                or manifest.get("image_digest", "")
            )
            container_probe["image_digest"] = real_digest
            entry_argv = list(getattr(request, "healthcheck", {}).get("argv") or [])
            if not entry_argv:
                entry_argv = ["echo", "container probe ok"]
            container_conf = _RunConfig(
                image=request.image,
                image_digest=real_digest,
                name=f"godsv-build-{build_id}"[:60],
                entrypoint_argv=entry_argv,
                mounts=[],
                resource_limits=dict(getattr(request, "resource_limits", {}) or {}),
                # L7: every container we create is OURS by label — that is what
                # makes label-scoped teardown (and only that) possible later.
                labels={
                    _OWNER_LABEL_KEY: _OWNER_LABEL_VALUE,
                    "godsv.build_id": build_id,
                    "godsv.capability": (request.capability or name)[:80],
                },
            )
            # the config layer is asked for its problems as DATA (N12): a config
            # the policy forbids becomes a structured build blocker, not a raise
            probe_problems = container_conf.policy_problems(workdir=tool_dir)
            container_probe["policy_problems"] = probe_problems
            if probe_problems:
                result["error"] = (
                    f"container config refused: {', '.join(probe_problems)}"
                )
                result["failure_class"] = "policy_violation"
                result["probe_payload"] = {
                    "policy": "",
                    "problems": list(probe_problems),
                }
                manifest["status"] = "failed"
                self._record(result["build_id"], "failed", error=result["error"])
                return self._finish(
                    result["build_id"], result, "failed", reason=result["error"],
                    artifacts=[str(source_file), str(manifest_path)],
                    next_step="fix the container config (mounts/ports/limits) and re-run",
                )
            try:
                probe_result = mgr.run(container_conf, timeout=45.0)
            except _PolicyViolation as exc:
                # run() returns structured refusals; the config API keeps
                # raising for a direct call, and a build must never die on it
                probe_result = _policy_refusal([str(exc)])
            container_probe["probe"] = probe_result.to_dict()
            # L6/N1: sweep OUR leftovers for this build, whatever the probe did.
            # Scoped by owner + build label — never a name, never a prune. That
            # scope carries TWO labels, which is why it depends on
            # `list_by_labels` emitting one `--filter` per label: a comma-joined
            # `--filter label=a=1,b=2` matches nothing on the daemon (Server
            # 29.6.1, measured) and this sweep used to report a clean removal
            # while removing nothing.
            teardown = mgr.cleanup_by_labels(
                {
                    _OWNER_LABEL_KEY: _OWNER_LABEL_VALUE,
                    "godsv.build_id": build_id,
                },
                timeout=30.0,
            )
            container_probe["teardown"] = teardown.to_dict()
            checks.append(
                {
                    "name": "container_probe",
                    "ok": bool(probe_result.ok),
                    "detail": probe_result.detail[:200],
                }
            )
            checks.append(
                {
                    "name": "container_teardown",
                    "ok": bool(teardown.ok),
                    "detail": teardown.detail[:200],
                }
            )
            result["container_probe"] = container_probe
            if not probe_result.ok:
                failed = "container_probe"
                detail_text = probe_result.detail[:300]
                failure_kind = "infrastructure"
                result["error"] = f"container probe failed ({probe_result.failure_class})"
                result["failure_class"] = probe_result.failure_class
                result["probe_payload"] = dict(probe_result.payload)
                manifest["status"] = "failed"
                manifest_path.write_text(
                    yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
                )
                flow.note(
                    build_id,
                    {
                        "failure_class": probe_result.failure_class,
                        "payload": dict(probe_result.payload),
                    },
                    kind="container_failure",
                )
                self._record(result["build_id"], "failed", error=result["error"])
                return self._finish(
                    result["build_id"], result, "failed", reason=result["error"],
                    artifacts=[str(source_file), str(manifest_path)],
                    next_step=(
                        "infrastructure failure — repair the image/runtime as "
                        "infrastructure, never by rewriting the adapter"
                    ),
                    error=detail_text,
                )
            if not teardown.ok:
                # N1: a build that leaves a container behind is not green — the
                # partial teardown is reported (cleanup_partial), never hidden.
                result["error"] = (
                    f"container teardown failed ({teardown.failure_class}): "
                    f"{teardown.detail[:200]}"
                )
                result["failure_class"] = teardown.failure_class or "cleanup_partial"
                result["probe_payload"] = dict(teardown.payload)
                manifest["status"] = "failed"
                manifest_path.write_text(
                    yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
                )
                flow.note(
                    build_id,
                    {
                        "failure_class": result["failure_class"],
                        "payload": dict(teardown.payload),
                    },
                    kind="container_failure",
                )
                self._record(result["build_id"], "failed", error=result["error"])
                return self._finish(
                    result["build_id"], result, "failed", reason=result["error"],
                    artifacts=[str(source_file), str(manifest_path)],
                    next_step=(
                        "remove the labelled leftovers (label-scoped only) and "
                        "re-run — Skaldir never prunes the daemon"
                    ),
                    error=teardown.detail[:300],
                )
        self._record(build_id, "gates", phase="verify", checks=checks, passed=report.passed)
        result["gates"] = checks
        manifest["tests"] = test_command
        manifest["last_tested_at"] = time.time()
        flow.step_done(
            build_id, "verify",
            detail={
                "passed": report.passed,
                "checks": checks,
                "commands": [list(spec.test_command)],
            },
        )
        if not report.passed:
            failed = ", ".join(c["name"] for c in checks if not c["ok"])
            detail_text = "; ".join(
                f"{c['name']}: {c['detail']}" for c in checks if not c["ok"]
            )
            failure_kind = bf.classify_failure(detail_text)
            repair = self._repair_loop(build_id, limit=request.repair_limit)
            may, why = repair.may_repair(
                defect=f"gates failed: {failed}",
                failure_kind=failure_kind,
                layer="infrastructure" if failure_kind == "infrastructure" else "adapter",
            )
            result["error"] = f"gates failed: {failed}"
            result["repair"] = {
                "allowed": may,
                "reason": why,
                "failure_kind": failure_kind,
                "state": repair.state(),
            }
            result["next_step"] = why
            manifest["status"] = "failed"
            manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
            if may:
                # §4b: the loop is NOT finished — a repair round is available
                # (cap + new evidence). The flow stays open in `verify`; the
                # repair re-enters through `ToolBuilder.repair` + `build(...,
                # build_id=…)`, which keeps cap/budget in force.
                result["repairable"] = True
                result["repair"]["status"] = "repair_pending"
                flow.note(
                    build_id,
                    {
                        "repair_pending": True,
                        "reason": why,
                        "failure_kind": failure_kind,
                        "failed_checks": failed,
                    },
                    kind="repair",
                )
                self._record(
                    build_id, "failed", error=result["error"], repair=result["repair"],
                    repairable=True,
                )
                return result
            self._record(build_id, "failed", error=result["error"], repair=result["repair"])
            return self._finish(
                build_id, result, "failed", reason=result["error"],
                artifacts=[str(source_file), str(manifest_path)],
                next_step=why, error=detail_text[:400],
            )

        # Testing tools must prove they FAIL on broken targets (P1/B4) — the
        # capability-promotion path used to skip this validation entirely.
        if kind == "testing_tool" or request.validation_cases:
            if not self._enter(build_id, "validate", result, holdout="withheld cases"):
                return result
            cases = list(request.validation_cases)
            if not cases:
                result["error"] = (
                    "testing tool requires validation cases (broken + working targets) "
                    "before it may be registered"
                )
                manifest["status"] = "needs_validation"
                manifest_path.write_text(
                    yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
                )
                self._record(build_id, "failed", error=result["error"])
                return self._finish(
                    build_id, result, "blocked", reason=result["error"],
                    artifacts=[str(manifest_path)],
                    next_step="supply holdout cases (broken + working targets)",
                )
            from .capability_mining import (
                validate_testing_tool,
                validation_is_holdout,
            )

            # §4b fresh-case rule: a holdout that was opened for diagnosis is a
            # KNOWN regression test — it may not serve as the next independent
            # acceptance again. With no fresh case left, the build is blocked
            # (not published on burned evidence).
            burned = set(acceptance.burned)
            fresh_cases = [case for case in cases if str(case[0]) not in burned]
            flow.step_done(
                build_id, "validate",
                detail={
                    "holdout_cases": [str(case[0]) for case in cases],
                    "burned": sorted(burned),
                    "fresh_cases": [str(case[0]) for case in fresh_cases],
                    "fresh_cases_required": acceptance.fresh_cases_required,
                },
            )
            if burned and not fresh_cases:
                result["error"] = (
                    "the holdout was opened for diagnosis and now counts as a known "
                    "regression test — the next independent acceptance needs fresh cases"
                )
                result["blocked"] = "needs_fresh_holdout"
                manifest["status"] = "needs_fresh_holdout"
                manifest_path.write_text(
                    yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
                )
                self._record(build_id, "failed", error=result["error"])
                return self._finish(
                    build_id, result, "blocked", reason=result["error"],
                    artifacts=[str(source_file), str(manifest_path)],
                    next_step=(
                        "derive fresh holdout cases that were never opened for "
                        "diagnosis before claiming acceptance"
                    ),
                )

            holdout_ok, holdout_reason = validation_is_holdout(
                fresh_cases, request.synthesis_examples
            )
            if not holdout_ok:
                # after burning a case, a remainder that is no longer a full
                # holdout is exactly the fresh-case blocker — not a generic gap
                if burned:
                    result["error"] = (
                        "the holdout was opened for diagnosis and now counts as a known "
                        f"regression test — the next independent acceptance needs fresh "
                        f"cases ({holdout_reason})"
                    )
                    result["blocked"] = "needs_fresh_holdout"
                    manifest["status"] = "needs_fresh_holdout"
                else:
                    result["error"] = f"validation is not a holdout: {holdout_reason}"
                    manifest["status"] = "needs_holdout"
                manifest_path.write_text(
                    yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
                )
                self._record(build_id, "failed", error=result["error"])
                return self._finish(
                    build_id, result, "blocked", reason=result["error"],
                    artifacts=[str(source_file), str(manifest_path)],
                    next_step=(
                        "supply fresh, never-opened held-out cases (both outcomes, "
                        "no repeated target)" if burned else
                        "supply held-out cases (both outcomes, no repeated target)"
                    ),
                )

            validation = validate_testing_tool(
                lambda target: _run_testing_case(
                    module, source_file, target, workdir=tool_dir
                ),
                fresh_cases,
            )
            validation["holdout"] = {
                "status": "validated",
                "detail": holdout_reason,
                "fresh": True,
                "cases": [str(case[0]) for case in fresh_cases],
                "known_regression_tests": sorted(burned),
            }
            manifest["validation"] = validation
            checks.append(
                {
                    "name": "testing_validation",
                    "ok": validation["status"] == "validated",
                    "detail": "; ".join(
                        f"{c['case']}={'ok' if c['ok'] else 'mismatch'}"
                        for c in validation["checks"]
                    )[:200],
                }
            )
            self._record(
                build_id, "gates", phase="validate", checks=checks,
                validation=validation["status"],
            )
            result["gates"] = checks
            flow.step_done(
                build_id, "validate",
                detail={
                    "status": validation["status"],
                    "checks": validation["checks"],
                    "provenance": validation["holdout"],
                },
            )
            if validation["status"] != "validated":
                result["error"] = (
                    "testing tool validation failed — it reports PASS on a broken "
                    "target; promotion refused"
                )
                manifest["status"] = "failed"
                manifest_path.write_text(
                    yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
                )
                self._record(build_id, "failed", error=result["error"])
                return self._finish(
                    build_id, result, "failed", reason=result["error"],
                    artifacts=[str(source_file), str(manifest_path)],
                    next_step=(
                        "repair the checker so it FAILs on broken targets (bounded "
                        "repair: 3 attempts, only with new evidence)"
                    ),
                )

        # -- phase `validate` (general tools): held-out NEW inputs against an
        # externally checkable measure. Declared holdout inputs are run through
        # the real (sandboxed) adapter; without any, the limitation is RECORDED
        # instead of silently counting the gates as holdout evidence.
        probe_runs: list[dict] = []
        if kind != "testing_tool" and not request.validation_cases:
            if not self._enter(build_id, "validate", result, cases=len(request.holdout_inputs)):
                return result
            if request.holdout_inputs:
                probe_runs = [
                    _run_adapter_probe(
                        source_file, module, request.entrypoint or "main",
                        dict(payload), workdir=tool_dir,
                    )
                    for payload in request.holdout_inputs
                    if isinstance(payload, dict)
                ]
            failed_probes = [run for run in probe_runs if not run.get("ok")]
            # a probe that never ran (no sandbox) is NOT evidence either way —
            # fail-closed, but it must not read as "the adapter is broken".
            unsandboxed_only = bool(failed_probes) and all(
                not run.get("sandboxed") for run in failed_probes
            )
            if not probe_runs:
                probe_status = "not_declared"
            elif not failed_probes:
                probe_status = "validated"
            elif unsandboxed_only:
                probe_status = "not_certifiable"
            else:
                probe_status = "failed"
            validation_state = {
                "status": probe_status,
                "fresh": True,
                "cases": probe_runs,
                "detail": (
                    f"{len(probe_runs)} held-out input(s) probed"
                    if probe_runs else
                    "no held-out inputs declared — acceptance rests on the gates "
                    "and the declared sample input (documented limitation)"
                ),
                "known_regression_tests": sorted(acceptance.burned),
            }
            manifest["validation"] = validation_state
            result["validation"] = probe_status
            flow.step_done(build_id, "validate", detail=validation_state)
            self._record(
                build_id, "gates", phase="validate", validation=probe_status,
            )
            if probe_status == "failed":
                result["error"] = (
                    "held-out validation failed on the generated adapter: "
                    + "; ".join(str(run.get("detail", ""))[:120] for run in failed_probes)
                )
                manifest["status"] = "failed"
                manifest_path.write_text(
                    yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
                )
                self._record(build_id, "failed", error=result["error"])
                return self._finish(
                    build_id, result, "failed", reason=result["error"],
                    artifacts=[str(source_file), str(manifest_path)],
                    next_step=(
                        "repair the adapter against the held-out inputs (bounded "
                        "repair: 3 attempts, only with new evidence)"
                    ),
                )

        spec.scan_summary = "gates: " + ", ".join(
            f"{c['name']}={'ok' if c['ok'] else 'fail'}" for c in checks
        )
        spec.status = STATUS_APPROVED

        # -- phase `publish`: immutable version + evidence, ATOMICALLY activatable.
        # The registry commit and the outbox entry share ONE transaction (§4d),
        # so a crash between them cannot leave an invisible capability.
        stopped = self._halt(
            build_id, result, started=started, budget_seconds=budget_seconds,
            artifacts=[str(source_file), str(manifest_path)],
        )
        if stopped is not None:
            return stopped
        if not self._enter(build_id, "publish", result, tool=name, version=spec.version):
            return result
        subscriber = request.session_id or self.session_id
        sub_ok, sub_reason = flow.subscription_ok(
            build_id, subscriber, request.goal_revision
        )
        if not sub_ok:
            result["error"] = f"publication refused — {sub_reason}"
            result["superseded"] = True
            self._record(build_id, "failed", error=result["error"], superseded=True)
            return self._finish(
                build_id, result, "superseded", reason=sub_reason,
                artifacts=[str(source_file), str(manifest_path)],
                next_step=(
                    "the goal moved on — re-run the build under the current goal "
                    "revision instead of inserting this one"
                ),
            )

        validation_info = manifest.get("validation") or {}
        holdout_provenance = validation_info.get("holdout") or {
            "status": validation_info.get("status", "not_applicable"),
            "detail": validation_info.get(
                "detail", "not a testing tool — acceptance via gates + declared inputs"
            ),
            "cases": validation_info.get("cases", list(request.holdout_inputs)),
        }
        runtime_probe = (
            {
                "executed": any(bool(run.get("sandboxed")) for run in probe_runs),
                "runs": probe_runs,
                "sandboxed": all(bool(run.get("sandboxed")) for run in probe_runs),
                "detail": "held-out adapter probe (validate phase)",
            }
            if probe_runs else {
                "executed": False,
                "wrapper": str(tool_dir / "bin" / name),
                "note": (
                    "no held-out inputs declared — the first real call is the "
                    "`observe` phase"
                ),
            }
        )
        evidence = bf.evidence_package(
            need=need,
            search_decision=result.get("search", {}),
            sources=[
                {"surface": surface, "decision": "searched"}
                for surface in result.get("search", {}).get("searched", [])
            ] + [{"module": module, "kind": "generated adapter"}],
            manifest=str(manifest_path),
            code_hashes=bf.hashes_for([source_file]),
            image_hash=str(manifest.get("image_digest", "") or ""),
            test_commands=[list(spec.test_command), ["gates", "scan,import,test,determinism"]],
            results=checks,
            holdout_provenance=holdout_provenance,
            limits=[
                f"network: {', '.join(request.allowed_domains) or 'none'}",
                f"dependencies: {', '.join(request.dependencies) or 'stdlib only'}",
                f"secrets (handles only): {', '.join(request.required_secrets) or 'none'}",
                f"risk_level: {request.risk_level}",
                # B6: docker tools have their IMAGE as first-class evidence —
                # the pinned digest replaces the old "no container" line.
                (
                    f"container image pinned by digest: "
                    f"{manifest.get('image_digest', '')}"
                    if request_runtime == MANIFEST_V2_RUNTIME
                    else "no container image (local python tool) — image_hash stays empty"
                ),
                (
                    "container runtime probe: "
                    + json.dumps(container_probe, ensure_ascii=False, default=str)[:300]
                    if request_runtime == MANIFEST_V2_RUNTIME
                    else "runtime: local (no container)"
                ),
            ],
            runtime_probe=runtime_probe,
            secrets=list(request.required_secrets),
        )
        evidence["build_id"] = build_id
        evidence["acceptance"] = acceptance.state()
        evidence["known_regression_tests"] = sorted(acceptance.burned)
        evidence["repair_state"] = self._repair_loop(
            build_id, limit=request.repair_limit
        ).state()
        evidence_path = self.runtime / "builds" / build_id / "evidence.json"
        try:
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
            evidence["path"] = str(evidence_path)
        except OSError as exc:
            evidence["path_error"] = f"{type(exc).__name__}: {exc}"

        try:
            receipt = self.store.publish(
                spec,
                payload={
                    "build_id": build_id,
                    "name": spec.name,
                    "version": spec.version,
                    "kind": kind,
                    "project_id": request.project_id,
                    "source": source_kind,
                    "registry_revision_hint": True,
                },
            )
        except Exception as exc:
            # the transaction rolled back: neither a registry row nor an outbox
            # entry exists — nothing half-published (§4d, §4a)
            result["error"] = (
                f"publication failed (transaction rolled back): {type(exc).__name__}: {exc}"
            )
            manifest["status"] = "failed"
            manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
            self._record(build_id, "failed", error=result["error"])
            return self._finish(
                build_id, result, "failed", reason=result["error"],
                artifacts=[str(source_file), str(manifest_path)],
                next_step="fix the registry write and retry the publish step",
            )
        result["publish"] = receipt
        result["evidence"] = evidence
        result["published_event"] = self._emit_publish_event(spec, receipt)
        try:
            flow.attach(
                build_id, artifacts=[str(manifest_path), str(source_file)],
                evidence={
                    "path": evidence.get("path", ""),
                    "redactions": evidence.get("redactions", 0),
                    "fields": sorted(bf.EVIDENCE_FIELDS),
                },
            )
        except Exception as exc:
            logging.getLogger("skaldir.toolbuilder").warning(
                "evidence attach failed (%s: %s)", type(exc).__name__, exc
            )
        wrapper = tool_dir / "bin" / name
        wrapper.parent.mkdir(exist_ok=True)
        if kind in ("cli", "testing_tool"):
            wrapper.write_text(
                "#!/usr/bin/env python3\n"
                f'"""CLI wrapper for the generated tool {name!r}."""\n'
                "import json\n"
                "import sys\n"
                "import time\n"
                "_ROOT = __import__('pathlib').Path(__file__).resolve().parents[1]\n"
                "sys.path.insert(0, str(_ROOT))\n"
                f"import {module} as _tool\n"
                "\n"
                "def _scrub(text):\n"
                "    try:\n"
                "        from skaldir.secretscan import redact\n"
                "        return redact(str(text))[0]\n"
                "    except Exception:\n"
                "        return str(text)\n"
                "\n"
                "def _record(ok, error='', started=None):\n"
                "    try:\n"
                "        import json as _json\n"
                "        import pathlib as _pl\n"
                "        log = _ROOT.parent.parent / 'tool_usage.jsonl'\n"
                "        entry = {'ts': time.time(), 'name': " + repr(name) + ", 'ok': bool(ok)}\n"
                "        if started is not None:\n"
                "            entry['duration_ms'] = int((time.monotonic() - started) * 1000)\n"
                "        if error:\n"
                "            entry['error'] = _scrub(error)[:300]\n"
                "        with log.open('a', encoding='utf-8') as fh:\n"
                "            fh.write(_json.dumps(entry, ensure_ascii=False) + '\\n')\n"
                "    except Exception:\n"
                "        pass\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    payload = {}\n"
                "    if len(sys.argv) > 1:\n"
                "        payload = json.loads(sys.argv[1])\n"
                "    _started = time.monotonic()\n"
                "    try:\n"
                f"        out = _tool.{request.entrypoint or 'main'}(**payload)\n"
                "    except Exception as exc:\n"
                "        _record(False, f'{type(exc).__name__}: {exc}', _started)\n"
                "        raise\n"
                "    _record(True, '', _started)\n"
                "    print(json.dumps(out, ensure_ascii=False, default=str))\n",
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
        manifest["status"] = "ready"
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        self._record(build_id, "register", tool=spec.name, status=spec.status, wrapper=str(wrapper))
        flow.step_done(
            build_id, "publish",
            detail={
                "tool": spec.name,
                "version": spec.version,
                "status": spec.status,
                "outbox_id": result.get("publish", {}).get("outbox_id"),
                "registry_revision": result.get("publish", {}).get("revision"),
                "duplicate": result.get("publish", {}).get("duplicate"),
                "wrapper": str(wrapper),
                "evidence": evidence.get("path", ""),
            },
        )
        # B6/TOOLS-31: the build passing its gates IS the verification event.
        # Health is a separate fact from approval — a verified tool starts
        # HEALTHY and only then becomes visible to auto-routing.
        try:
            health.record_verification(
                self.runtime,
                spec.name,
                ok=True,
                detail="gates: " + ", ".join(
                    f"{c['name']}={'ok' if c['ok'] else 'fail'}" for c in checks
                )[:200],
            )
        except Exception:  # never fail a good build over a health ledger write
            pass
        self._record(build_id, "ready", ok=True, tool=spec.name, phase="bind")

        # -- phase `bind`: session binding is its OWN state. "registered,
        # binding pending" is not a finished state (§4a); the B5 live surface
        # (tool_published + apply_pending) does the actual insertion.
        bind_status = "pending"
        if self._enter(build_id, "bind", result, surface="B5 live surface"):
            try:
                flow.mark_bind(
                    build_id, "pending",
                    detail={"mechanism": "tool_published event + B5 apply_pending"},
                )
            except Exception as exc:
                logging.getLogger("skaldir.toolbuilder").warning(
                    "bind state not recorded (%s: %s)", type(exc).__name__, exc
                )
        result["ok"] = True
        result["tool"] = spec.name
        result["wrapper"] = str(wrapper)
        result["manifest"] = str(manifest_path)
        result["binding"] = bind_status
        result["flow"] = {"phase": "bind", "status": "published, binding pending"}
        return result

    def _draft_without_source(
        self, build_id: str, request: ToolBuildRequest, result: dict, *,
        name: str, compose_check: dict,
    ) -> dict:
        """§1.8: no source supplied — draft with the configured client, or block.

        No client means no draft: the caller's blocker is reported precisely
        (never invented code). A budget abort is a terminal error state — no
        tool dir, no manifest, no register entry, nothing half-built.
        """
        if self.llm is None:
            result["error"] = (
                "no source supplied (builder does not invent code) and no model "
                "client is configured for a draft — inject the existing configured "
                "client (ToolBuilder(llm=...)) or supply source/tests"
            )
            result["blocked"] = "no_source_no_client"
            self._record(
                build_id, "failed", error=result["error"], blocked="no_source_no_client"
            )
            return self._finish(
                build_id, result, "blocked", reason=result["error"],
                next_step="inject the configured model client or supply source/tests",
            )

        tracker = (
            self.budget
            if self.budget is not None
            else BudgetTracker(limits=dict(DRAFT_BUDGET_LIMITS))
        )
        self.last_draft_budget = tracker
        approved = [
            spec for spec in self.store.all() if spec.status == STATUS_APPROVED
        ][:20]
        existing = [f"{spec.name} — {spec.description or spec.title}" for spec in approved]
        draft = draft_tool_spec_result(
            self.llm, self.prompts, need=request.capability,
            existing_tools=existing, budget=tracker,
        )
        if draft.spec is None:
            result["error"] = f"model draft aborted ({draft.kind}): {draft.error}"
            result["blocked"] = f"draft_{draft.kind}"
            self._record(
                build_id, "failed", error=result["error"],
                draft_error=draft.error, budget=dict(tracker.usage),
            )
            return self._finish(
                build_id, result, "failed", reason=result["error"],
                next_step=(
                    "raise the build budget" if draft.kind == "budget"
                    else "fix the model client / prompt and retry the draft"
                ),
            )

        spec = draft.spec
        result["draft"] = {
            "name": spec.name,
            "title": spec.title,
            "description": spec.description,
            "kind": spec.kind,
            "tags": list(spec.tags),
            "steps": list(spec.steps),
            "rationale": spec.rationale,
            "provenance": spec.provenance,
        }
        problems = (
            validate_composition(self.store, spec)
            if spec.kind == KIND_COMPOSITION
            else []
        )
        if problems:
            result["draft_problems"] = problems
        note = (
            "no source supplied (builder does not invent code) — draft spec "
            f"'{spec.name}' ({spec.kind}) attached: it is a proposal, not a build; "
            "supply source+tests to build it or reuse/compose the tools it names"
        )
        if problems:
            note += f"; draft problems: {'; '.join(problems)}"
        result["error"] = note
        self._record(
            build_id, "drafted", draft=result["draft"], compose_check=compose_check,
            budget=dict(tracker.usage),
        )
        self._record(build_id, "failed", error=result["error"])
        return self._finish(
            build_id, result, "blocked", reason=result["error"],
            next_step=(
                "supply source+tests to build the proposal, or reuse/compose the "
                "tools it names"
            ),
        )

    # -- redirect back to the main agent --------------------------------------
    @staticmethod
    def redirect_refusal(
        request: ToolBuildRequest, *, reason: str, candidates: list[dict]
    ) -> dict:
        """The tiny refusal note for the main agent — reuse instead of building."""
        return {
            "tool_creation_refused": {
                "capability": request.capability,
                "reason": reason,
                "recommendation": (
                    "reuse or compose the existing tools instead of building a duplicate"
                ),
                "candidates": list(candidates),
            }
        }

    @staticmethod
    def redirect_payload(
        spec: ToolSpec, request: ToolBuildRequest, *, wrapper: str, kind: str | None = None
    ) -> dict:
        """The tiny note the main agent receives — not the build transcript."""
        effective = kind or (
            request.kind
            if request.kind in ("mcp", "cli")
            else (spec.tags[0] if spec.tags else "cli")
        )
        if effective == "cli":
            how = (
                f"CLI: run `{wrapper} '{{\"…\": \"…\"}}'` (JSON args) and read the JSON stdout."
            )
        elif effective == "testing_tool":
            how = (
                f"Testing tool `{spec.name}`: runs as a repeatable check at the "
                f"completion gate (register_testing_tools); invoke via `{wrapper}` "
                "with JSON args like the CLI form."
            )
        else:
            how = f"MCP: the tool `{spec.name}` is exposed by the godsv-tools MCP server."
        constraints = []
        if request.required_secrets:
            constraints.append(
                "credential handles required: " + ", ".join(request.required_secrets)
                + " (grant them to tool '" + spec.name + "' in the Treasury panel)"
            )
        constraints.append("network: " + (", ".join(request.allowed_domains) or "none"))
        return {
            "tool_created": {
                "capability": request.capability,
                "tool": spec.name,
                "kind": effective,
                "how_to_use": how,
                "important_constraints": constraints,
            }
        }


# --------------------------------------------------------------------------- #
# B6 — the container half of the self-expanding tool system
# --------------------------------------------------------------------------- #
#
# The owner's chain, for a container image:
#
#   search (never build what exists) → inspect the container's CLI/API/MCP
#   surface → generate a TYPED adapter → TEST the adapter against the real
#   container → REGISTER the validated tool live (version, health, permissions,
#   rollback).
#
# Three rules hold the whole path together:
#
# * **Nothing is generated from an uninspected surface.** The probes run in the
#   pinned image itself (one-shot, ``--rm``, network none, read-only rootfs); a
#   probe that fails refuses the build with a reason instead of guessing.
# * **An adapter is bound to the bytes that were tested.** The evidence records
#   a digest of ``adapter.json``; registration refuses when the file changed
#   after the test — and the generated module itself refuses to run when the
#   local image no longer carries the pinned digest.
# * **A capability is tested against an INDEPENDENT oracle.** The expected value
#   comes from a local stdlib implementation (hashlib/base64), never from the
#   container that is being tested — otherwise the test would only prove that
#   the container agrees with itself.

CONTAINER_KIND = "container"
ADAPTER_FILE = "adapter.json"
CONTAINER_EVIDENCE_FILE = "container_evidence.json"
CONTAINER_MODULE_FILE = "container_module.py"
CONTAINER_ROLLBACK_DIR = "container_rollbacks"
CONTAINER_SERVICE_DIR = "container_services"

#: Labels every container created for a capability carries (teardown scope).
CAPABILITY_LABEL = "godsv.capability"
PURPOSE_LABEL = "godsv.purpose"
PURPOSE_TEST = "test"
PURPOSE_SERVICE = "service"

#: What a container capability may look like: nothing but the pinned image,
#: no mounts, no network, no ports, no credentials.
CONTAINER_PERMISSIONS: dict = {
    "network": "none",
    "mounts": [],
    "read_only_rootfs": True,
    "no_new_privileges": True,
    "privileged": False,
    "resource_limits": {"cpu": "1", "ram": "128m"},
    "secrets": [],
    "allowed_domains": [],
    "publish_ports": {},
}


class SurfaceRefusal(RuntimeError):
    """The container's surface could not be inspected — nothing is generated."""


#: The refusal a typed-adapter failure raises: **the same type** the shared
#: adapter runtime uses (:class:`skaldir.container_adapter.AdapterRefusal`), so
#: the builder and the module it writes refuse through one exception type and one
#: code path. Existing callers keep catching it by this name.
AdapterRefusal = container_adapter.AdapterRefusal

# The general CLI families (tr/wc/sort/uniq/cut/basename/jq + the schema loader)
# extend this catalogue: same template shape, same generate/test/register path.
# See :mod:`skaldir.container_cli_templates` for the oracle honesty ladder.
from .container_cli_templates import (  # noqa: E402 — after AdapterRefusal exists
    TEXT_TEMPLATES as _CLI_TEXT_TEMPLATES,
)
from .container_cli_templates import (  # noqa: E402, F401 — after AdapterRefusal exists;
    #   re-exported facade: toolbuilder_cli imports ``load_schema_template`` THROUGH
    #   skaldir.toolbuilder (``from .toolbuilder import AdapterRefusal,
    #   load_schema_template``), so the name must keep resolving here even though this
    #   module body never calls it.
    load_schema_template,
)


def _sha256_file(path: Path) -> str:
    import hashlib

    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _oracle_sha256(payload: dict) -> str:
    """Independent local oracle for ``sha256sum`` (stdlib, not the container)."""
    import hashlib

    return hashlib.sha256(str(payload["text"]).encode("utf-8")).hexdigest()


def _oracle_md5(payload: dict) -> str:
    import hashlib

    return hashlib.md5(  # noqa: S324 — a digest, not a security decision
        str(payload["text"]).encode("utf-8"), usedforsecurity=False
    ).hexdigest()


def _oracle_base64(payload: dict) -> str:
    import base64

    return base64.b64encode(str(payload["text"]).encode("utf-8")).decode("ascii")


# --------------------------------------------------------------------------- #
# argument-mapping templates
# --------------------------------------------------------------------------- #
#
# The three templates above are *verb* templates: one string in, one line of
# hash-like text out, and the whole call is `printf %s "$0" | <verb> | cut`.
# That is why a media tool could never be mined: `ffmpeg -ss 2 -to 8 -i in.mp4
# -vn -c:a copy out.m4a` is not a verb on a string, it is a *command line built
# from typed arguments* over *files*, and the artefact it returns is not a line
# of text. The templates below declare that shape, and the runtime that executes
# it lives in :mod:`skaldir.container_adapter` (one copy for the builder and for
# the module the builder writes — see that module's docstring).

#: The largest source file an ``argv-base64`` template carries. One argv element
#: is capped by the kernel at ``MAX_ARG_STRLEN`` (131 072 bytes), so the base64
#: form of the source must stay below it; 64 KiB of source encodes to 87 kB,
#: which leaves room for the rest of the command line. A bigger source file is
#: REFUSED by name (never truncated) — the honest bound of this transfer mode.
_TRANSFER_MAX_SOURCE_BYTES = 65536

#: The scratch files the argument-mapping templates use. They live under
#: ``/dev/shm``: the container's rootfs is read-only by policy, and ``/dev/shm``
#: is the one writable tmpfs docker always provides — verified live with both
#: images, which is also why ImageMagick gets ``MAGICK_TEMPORARY_PATH`` pointed
#: at it (IM v7 writes an intermediate blob even when the output is a pipe).
_MAGICK_SCRATCH = {"_in": "/dev/shm/godsv_in.png", "_out": "/dev/shm/godsv_out"}
_FFMPEG_SCRATCH = {"_in": "/dev/shm/godsv_in.mp4", "_out": "/dev/shm/godsv_out"}

#: The literal ``sh -c`` prologue per scratch set: ``(prelude, marker, script)``.
#:
#: Literal on purpose. The shell re-parses the text after ``-c``, so a caller value
#: spliced into it would be re-parsed too; the scratch paths, the environment
#: prelude and the output marker are compile-time constants of these templates and
#: are written out here instead. ``$0`` is the base64 of the source file, ``$@`` the
#: whole mapped command, and the artefact is printed back as ``<marker>@b64=<b64>``.
_SCRATCH_PROLOGUES: dict[tuple[str, str], tuple[tuple[str, ...], str, str]] = {
    ("/dev/shm/godsv_in.png", "/dev/shm/godsv_out"): (
        ("export TMPDIR=/dev/shm MAGICK_TEMPORARY_PATH=/dev/shm",),
        "image",
        "set -e\n"
        "IN=/dev/shm/godsv_in.png\n"
        "OUT=/dev/shm/godsv_out\n"
        "printf %s \"$0\" | base64 -d > \"$IN\"\n"
        "export TMPDIR=/dev/shm MAGICK_TEMPORARY_PATH=/dev/shm\n"
        "\"$@\"\n"
        "printf 'image@b64='\n"
        "base64 \"$OUT\" | tr -d '\\n'\n"
        "printf '\\n'",
    ),
    ("/dev/shm/godsv_in.mp4", "/dev/shm/godsv_out"): (
        (),
        "audio",
        "set -e\n"
        "IN=/dev/shm/godsv_in.mp4\n"
        "OUT=/dev/shm/godsv_out\n"
        "printf %s \"$0\" | base64 -d > \"$IN\"\n"
        "\"$@\"\n"
        "printf 'audio@b64='\n"
        "base64 \"$OUT\" | tr -d '\\n'\n"
        "printf '\\n'",
    ),
}


def _argv_transfer() -> dict:
    """The declared transfer rule of an argument-mapping template."""
    from .container_adapter import ARGV_ELEMENT_LIMIT

    return {
        "mode": "argv-base64",
        "max_source_bytes": _TRANSFER_MAX_SOURCE_BYTES,
        "argv_element_limit": ARGV_ELEMENT_LIMIT,
        "note": (
            "the source file travels to the container as ONE base64 argv element "
            "(the same $0 convention the verb templates use for their text) and "
            "the artefact comes back the same way on stdout; the kernel caps one "
            f"argv element at {ARGV_ELEMENT_LIMIT} bytes, so a source above "
            f"{_TRANSFER_MAX_SOURCE_BYTES} bytes is refused, never truncated"
        ),
    }


def _scratch_runner(scratch: dict, prelude: list[str], marker: str) -> list[str]:
    """The literal ``sh -c`` prologue of an argument-mapping template.

    ``$0`` is the base64 of the source file and ``$@`` the *whole* mapped command
    (tool name included), so the mapped argv is passed as real argv elements — no
    shell quoting of caller values is ever needed, and the tool itself is data
    rather than a hardcoded word in this script. The artefact is written under
    ``/dev/shm`` and printed back as one keyed line per declared output:
    ``<name>@b64=<base64>``. ``set -e`` keeps a failing tool from printing a
    half-written output.

    The script text itself is a **literal**, looked up in
    :data:`_SCRATCH_PROLOGUES` (the shell re-parses the text after ``-c``, so a
    value spliced into it would be re-parsed too). A scratch set, prelude or marker
    with no literal prologue is refused by name instead of being built into one.
    """
    key = (str(scratch.get("_in") or ""), str(scratch.get("_out") or ""))
    entry = _SCRATCH_PROLOGUES.get(key)
    if entry is None:
        raise AdapterRefusal(
            f"the scratch set {key!r} has no literal shell prologue — add it to "
            "_SCRATCH_PROLOGUES; a scratch path is never spliced into shell text"
        )
    expected_prelude, expected_marker, script = entry
    if tuple(prelude) != expected_prelude or marker != expected_marker:
        raise AdapterRefusal(
            f"the runner for {key[0]} declares prelude {list(prelude)!r} and marker "
            f"{marker!r}, but its literal prologue is written for "
            f"{list(expected_prelude)!r} and {expected_marker!r}"
        )
    return ["sh", "-c", script]


def _oracle_image_resize(payload: dict) -> dict:
    """The expected geometry of one resize — from the SOURCE header and the box.

    Nothing here asks the container: the source's own PNG header carries the
    input geometry, the two parameters carry the requested box, and ImageMagick's
    ``-resize WxH`` rule (fit the box, keep the aspect ratio; a width-only
    ``1280x`` means "height follows") gives the one answer a correct call must
    produce. The comparison is exact — no tolerance is needed for an image.
    """
    source = Path(str(payload.get("input") or ""))
    if not source.is_file():
        raise AdapterRefusal(
            f"the local oracle needs the source image and {source} is not a file"
        )
    width, height = (
        int(part)
        for part in container_media.image_geometry(source.read_bytes()).split("x")
    )
    box_width = int(payload["width"])
    box_height = int(payload["height"]) if payload.get("height") else 0
    scale = (
        min(box_width / width, box_height / height)
        if box_height
        else box_width / width
    )
    geometry = f"{max(1, round(width * scale))}x{max(1, round(height * scale))}"
    return {
        "values": {"geometry": geometry},
        "tolerance": {},
        "note": (
            f"source {width}x{height} (read from its own header), requested box "
            f"{box_width}x{box_height or 'width-only'}, independent expectation "
            f"{geometry}"
        ),
    }


def _oracle_audio_range(payload: dict) -> dict:
    """The expected audio length of one ``-ss/-to`` window — arithmetic on the SOURCE.

    The source's audio track carries its own sample rate (the mp4 ``mdhd``
    timescale); the window's length in seconds times that rate is the number of
    samples a correct cut must contain. A PCM output is compared exactly; a
    copied AAC stream gets one coded frame of slack (1024 samples ≈ 23 ms),
    because the codec's frame granularity and the encoder's priming samples sit
    between the requested window and the produced file — and that slack is
    declared here, next to the expectation it applies to, rather than hidden in
    a comparison operator.
    """
    source = Path(str(payload.get("input") or ""))
    if not source.is_file():
        raise AdapterRefusal(
            f"the local oracle needs the source file and {source} is not a file"
        )
    rate = container_media.mp4_audio_rate(source.read_bytes())
    start = float(payload["start"])
    end = float(payload["end"])
    if end <= start:
        raise AdapterRefusal(
            f"the declared window {start}..{end} is empty or inverted — a range "
            "template cannot certify a window that contains nothing"
        )
    ticks = int(round((end - start) * rate))
    tolerance: dict = {}
    if str(payload.get("codec") or "copy").startswith("copy"):
        tolerance["audio_ticks"] = (
            1024,
            "one coded AAC frame (1024 samples at this rate): a copied stream "
            "keeps the codec's frame boundaries and its priming samples",
        )
    return {
        "values": {"audio_ticks": ticks},
        "tolerance": tolerance,
        "note": (
            f"source audio rate {rate} Hz (read from its own mdhd), window "
            f"{end - start:g} s, independent expectation {ticks} samples"
        ),
    }


#: The generator table: which container verb implements which capability, how
#: the call is rendered, what it accepts and returns — plus the LOCAL oracle
#: that verifies the container's answer. A capability that matches no entry is
#: refused; the builder never invents an adapter.
VERB_TEMPLATES: tuple[dict, ...] = (
    {
        "id": "sha256-string",
        "terms": ("sha-256", "sha256", "digest", "hash", "checksum"),
        "verb": "sha256sum",
        "argv_template": [
            "sh",
            "-c",
            "printf %s \"$0\" | sha256sum | cut -d' ' -f1",
            "{text}",
        ],
        "inputs": {"text": {"type": "string", "required": True}},
        "outputs": {"sha256": {"type": "string", "from": "stdout",
                               "pattern": "^[0-9a-f]{64}$"}},
        "oracle": _oracle_sha256,
        "sample_input": {"text": "skaldir"},
    },
    {
        "id": "md5-string",
        "terms": ("md5", "md-5"),
        "verb": "md5sum",
        "argv_template": [
            "sh",
            "-c",
            "printf %s \"$0\" | md5sum | cut -d' ' -f1",
            "{text}",
        ],
        "inputs": {"text": {"type": "string", "required": True}},
        "outputs": {"md5": {"type": "string", "from": "stdout",
                            "pattern": "^[0-9a-f]{32}$"}},
        "oracle": _oracle_md5,
        "sample_input": {"text": "skaldir"},
    },
    {
        "id": "base64-string",
        "terms": ("base64", "base-64", "encode", "encoded"),
        "verb": "base64",
        "argv_template": ["sh", "-c", "printf %s \"$0\" | base64", "{text}"],
        "inputs": {"text": {"type": "string", "required": True}},
        "outputs": {"base64": {"type": "string", "from": "stdout",
                               "pattern": "^[A-Za-z0-9+/]+=*$"}},
        "oracle": _oracle_base64,
        "sample_input": {"text": "skaldir"},
    },
    # -- argument mapping: one subcommand over files ------------------------- #
    {
        "id": "imagemagick-resize",
        "kind": "subcommand",
        "terms": ("resize", "resiz", "scal", "thumbnail", "thumb", "convert",
                  "webp", "jpeg", "image", "photo", "picture", "png"),
        "verb": "magick",
        # IM v7 spells the tool `magick` (with `convert` as a deprecated alias),
        # IM v6 only has `convert`: the surface decides which one is real.
        "verb_candidates": ("magick", "convert"),
        "runner": _scratch_runner(
            _MAGICK_SCRATCH,
            ["export TMPDIR=/dev/shm MAGICK_TEMPORARY_PATH=/dev/shm"],
            "image",
        ),
        "scratch": dict(_MAGICK_SCRATCH),
        "argv_template": [
            {"transfer": "base64", "param": "input"},
            {"verb": True},
            {"const": "{_in}"},
            {"flag": "-resize", "value": "{width}x{height}", "when_given": "height"},
            {"flag": "-resize", "value": "{width}x", "when_not_given": "height"},
            {"flag": "-quality", "value": "{quality}"},
            {"flag": "-strip", "when": "strip"},
            {"const": "{format}:{_out}"},
        ],
        "inputs": {
            "input": {
                "type": "path", "required": True, "host_only": True, "exists": True,
                "media": "png", "max_bytes": _TRANSFER_MAX_SOURCE_BYTES,
                "transfer": "argv-base64",
                "description": "the PNG whose bytes travel to the container",
            },
            "out": {
                "type": "path", "host_only": True, "exists": False,
                "description": "host path the returned image is written to",
            },
            "width": {"type": "integer", "required": True, "min": 1, "max": 20000,
                      "description": "the target width in pixels"},
            "height": {"type": "integer", "min": 1, "max": 20000,
                       "description": "the target height; omit for width-only "
                                      "`-resize 1280x`"},
            "quality": {"type": "integer", "min": 1, "max": 100, "default": 85},
            "format": {"type": "enum", "values": ("webp", "png", "jpeg"),
                       "default": "webp",
                       "description": "the image format the artefact is written in"},
            "strip": {"type": "boolean", "default": True,
                      "description": "drop metadata (`-strip`)"},
        },
        "outputs": {
            "image": {
                "type": "file", "media": ("webp", "png", "jpeg"),
                "host_path_param": "out",
                "description": "the resized image, byte for byte as the container "
                               "wrote it",
            },
            "geometry": {
                "type": "string",
                "derive": {"from": "image", "field": "geometry"},
                "pattern": "^[0-9]{1,5}x[0-9]{1,5}$",
                "description": "the artefact's own pixel geometry, read from its "
                               "header by the host",
            },
        },
        "oracle": _oracle_image_resize,
        "output_protocol": "keyed",
        "input_transfer": _argv_transfer(),
        "timeout_seconds": 90,
        "sample_input": {},
    },
    # -- argument mapping: one subcommand over a time RANGE ------------------ #
    {
        "id": "ffmpeg-audio-range",
        "kind": "subcommand-range",
        "terms": ("audio", "extract", "sound", "track", "range", "trim", "clip",
                  "segment", "slice", "cut", "m4a", "aac", "wav", "video"),
        "verb": "ffmpeg",
        "verb_candidates": ("ffmpeg",),
        "runner": _scratch_runner(_FFMPEG_SCRATCH, [], "audio"),
        "scratch": dict(_FFMPEG_SCRATCH),
        "argv_template": [
            {"transfer": "base64", "param": "input"},
            {"verb": True},
            {"const": "-hide_banner"},
            {"const": "-nostdin"},
            {"const": "-loglevel"},
            {"const": "error"},
            {"const": "-i"},
            {"const": "{_in}"},
            {"flag": "-ss", "value": "{start}", "when_given": "start"},
            {"flag": "-to", "value": "{end}", "when_given": "end"},
            {"flag": "-vn", "when": "audio_only"},
            {"flag": "-c:a", "value": "{codec}"},
            {"flag": "-f", "value": "{container}"},
            {"const": "{_out}"},
        ],
        "inputs": {
            "input": {
                "type": "path", "required": True, "host_only": True, "exists": True,
                "media": "mp4", "max_bytes": _TRANSFER_MAX_SOURCE_BYTES,
                "transfer": "argv-base64",
                "description": "the mp4 whose bytes travel to the container",
            },
            "out": {
                "type": "path", "host_only": True, "exists": False,
                "description": "host path the returned audio file is written to",
            },
            "start": {"type": "number", "required": True, "min": 0,
                      "description": "the window's start in seconds (`-ss`)"},
            "end": {"type": "number", "required": True, "min": 0,
                    "greater_than": "start",
                    "description": "the window's end in seconds (`-to`), after the start"},
            "audio_only": {"type": "boolean", "default": True,
                           "description": "drop the video stream (`-vn`)"},
            "codec": {"type": "enum", "values": ("copy", "aac", "pcm_s16le"),
                      "default": "copy",
                      "description": "the audio codec; `pcm_s16le` pairs with a "
                                     "wav container"},
            "container": {"type": "enum", "values": ("m4a", "wav"), "default": "m4a",
                          # the caller's word -> the muxer's own word
                          "token_map": {"m4a": "ipod", "wav": "wav"},
                          "description": "the output container; `m4a` renders as "
                                         "ffmpeg's `ipod` muxer"},
        },
        "outputs": {
            "audio": {
                "type": "file", "media": ("mp4", "wav"),
                "host_path_param": "out",
                "description": "the extracted audio, byte for byte as the container "
                               "wrote it",
            },
            "audio_ticks": {
                "type": "integer",
                "derive": {"from": "audio", "field": "audio_ticks"},
                "pattern": "^[0-9]{1,9}$",
                "description": "the number of audio samples in the artefact, read "
                               "from its own header/index by the host",
            },
        },
        "oracle": _oracle_audio_range,
        "output_protocol": "keyed",
        "input_transfer": _argv_transfer(),
        "timeout_seconds": 90,
        "sample_input": {},
    },
)

#: The full catalogue: the five media/hash families plus the general CLI text
#: families (each with its own independent host twin). ``match_verb_template``
#: and ``_template_of`` see one list — a new family is a new entry, not a new path.
VERB_TEMPLATES = VERB_TEMPLATES + _CLI_TEXT_TEMPLATES

#: Binaries whose presence means the image carries an API surface (reported,
#: never assumed to work).
_API_SERVER_HINTS = ("nginx", "httpd", "caddy", "traefik", "uvicorn", "gunicorn")


def _manager_of(manager: DockerRuntimeManager | None) -> DockerRuntimeManager:
    """A DockerRuntimeManager, injected or the real one."""
    if manager is not None:
        return manager
    from .docker_runtime import DockerRuntimeManager

    return DockerRuntimeManager()


def _run_config(
    *,
    image_digest: str,
    image: str,
    argv: list[str],
    name: str = "",
    labels: dict | None = None,
    readonly_rootfs: bool = True,
    resource_limits: dict | None = None,
    network: str = "none",
    port_bindings: dict | None = None,
    entrypoint: str | None = None,
) -> RunConfig:
    """One RunConfig for a build/test container — always ours by label.

    ``port_bindings`` is only meaningful together with a real network (the
    config layer itself refuses ports under ``--network none`` — docker drops
    the mapping silently there, measured on Server 29.6.1). A service that
    SERVES (the dashboard of a mined research tool) declares both in its
    adapter's service spec; everything else stays network-none.
    """
    return RunConfig(
        image=image,
        image_digest=image_digest,
        name=name,
        entrypoint_argv=list(argv),
        network=network,
        port_bindings={int(c): int(h) for c, h in dict(port_bindings or {}).items()},
        readonly_rootfs=readonly_rootfs,
        resource_limits=dict(resource_limits or CONTAINER_PERMISSIONS["resource_limits"]),
        labels=owned_labels(**(labels or {})),
        entrypoint=entrypoint,
    )


def _probe(
    manager: DockerRuntimeManager, *, image: str, image_digest: str, argv: list[str],
    label: str, timeout: float = 60.0, entrypoint: str | None = None,
) -> dict:
    """One surface probe, run in the pinned image — structured, never raising."""
    config = _run_config(
        image=image, image_digest=image_digest, argv=argv,
        labels={PURPOSE_LABEL: PURPOSE_TEST},
        entrypoint=entrypoint,
    )
    result = manager.run(config, timeout=timeout)
    ok = bool(getattr(result, "ok", False)) and getattr(result, "exit_code", 0) == 0
    return {
        "probe": label,
        "argv": list(argv),
        "ok": bool(ok),
        "exit_code": getattr(result, "exit_code", None),
        "stdout": str(getattr(result, "stdout", "") or "")[:4000],
        "stderr": str(getattr(result, "stderr", "") or "")[:600],
        "failure_class": str(getattr(result, "failure_class", "") or ""),
        "detail": str(getattr(result, "detail", "") or "")[:300],
        "entrypoint_bypass": entrypoint is not None,
    }


def _image_entrypoint(manager: DockerRuntimeManager, image: str) -> list[str]:
    """The ENTRYPOINT the image declares — ``[]`` when it declares none/unknown.

    This is what explains the most common un-mineable media image:
    ``dpokidov/imagemagick`` declares ``ENTRYPOINT ["convert"]`` and
    ``jrottenberg/ffmpeg`` ``["/bin/ffmpeg"]``, so ``docker run <image> sh -c …``
    runs ``convert sh -c …`` and the probe fails inside the *tool*, not because
    the image lacks a shell. Reported, never assumed: a CLI that cannot answer
    gives ``[]``.
    """
    try:
        cmd = manager._invoke(  # noqa: SLF001 — one read-only docker inspect
            ["docker", "image", "inspect", "--format", "{{json .Config.Entrypoint}}",
             str(image)],
            timeout=30.0,
        )
    except Exception:  # noqa: BLE001 — an unreadable config is simply unknown
        return []
    if getattr(cmd, "exit_code", 1) != 0:
        return []
    try:
        parsed = json.loads(str(getattr(cmd, "stdout", "") or "null"))
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(part) for part in parsed]


_SURFACE_PROBES: tuple[tuple[str, list[str]], ...] = (
    ("identity", ["sh", "-c", "cat /etc/os-release 2>/dev/null | head -4"]),
    ("cli", [
        "sh", "-c",
        "(busybox --list 2>/dev/null || true); ls -1 /bin /usr/bin /usr/local/bin 2>/dev/null",
    ]),
    ("api_mcp", [
        "sh", "-c",
        "ls -1 /app /srv /opt 2>/dev/null | head -20; "
        "(command -v mcp mcp-server mcpserver 2>/dev/null || true); true",
    ]),
)


def inspect_container_surface(
    manager: DockerRuntimeManager, *, image: str = "", image_digest: str = "",
    timeout: float = 60.0, entrypoint_bypass: bool = True,
) -> dict:
    """Inspect a pinned container's CLI/API/MCP surface — or refuse with a reason.

    ``inspected`` is ``False`` (never a yes) when the digest is missing, the
    daemon does not answer, or the shell-based probes cannot run in the image:
    the build then stops instead of generating an adapter from guesswork.

    **The entrypoint bypass.** A media image usually declares its tool as the
    image's ``ENTRYPOINT`` (``convert``, ``/bin/ffmpeg``, ``/pandoc``). Docker
    appends the argv to that entrypoint, so ``sh -c …`` never runs a shell and
    *every* probe fails inside the tool — measured live, before any change, on
    ``dpokidov/imagemagick`` (``convert: unrecognized option '-c'``) and on
    ``jrottenberg/ffmpeg`` (``Unable to choose an output format for 'sh'``).
    When the first pass fails and the image has a shell, the probes are re-run
    with the entrypoint CLEARED (``--entrypoint ""``) and the surface records
    which pass worked — so the refusal of a genuinely shell-less image (pandoc's
    scratch rootfs) is still named for what it is.
    """
    if not image_digest:
        return {
            "inspected": False, "failure_class": "pin_required", "image": image,
            "image_digest": "", "reason": (
                "no pinned digest — an untraceable mutable tag cannot be inspected"
            ),
            "probes": [],
        }
    available, detail = manager.available()
    if not available:
        return {
            "inspected": False, "failure_class": "docker_unreachable", "image": image,
            "image_digest": image_digest,
            "reason": f"docker is not reachable: {detail}",
            "probes": [],
        }
    declared_entrypoint = _image_entrypoint(manager, image)
    passes: list[tuple[str, str | None]] = [("", None)]
    if entrypoint_bypass:
        passes.append(("entrypoint-cleared", ""))
    attempts: list[dict] = []
    for label_suffix, entrypoint in passes:
        probes = [
            _probe(manager, image=image, image_digest=image_digest, argv=argv,
                   label=(label if not label_suffix else f"{label}:{label_suffix}"),
                   timeout=timeout, entrypoint=entrypoint)
            for label, argv in _SURFACE_PROBES
        ]
        by_label = {
            probe["probe"].split(":")[0]: probe for probe in probes
        }
        attempts.append({
            "entrypoint": entrypoint,
            "label": label_suffix or "image entrypoint",
            "identity_ok": bool(by_label.get("identity", {}).get("ok")),
            "cli_ok": bool(by_label.get("cli", {}).get("ok")),
            "probes": probes,
        })
        if by_label.get("identity", {}).get("ok") and by_label.get("cli", {}).get("ok"):
            return _surface_of(
                attempts=attempts, probes=probes, image=image,
                image_digest=image_digest, declared_entrypoint=declared_entrypoint,
                bypassed=entrypoint is not None, cli_probe=by_label["cli"],
                identity=by_label["identity"], api_probe=by_label.get("api_mcp", {}),
            )
    # nothing worked: the refusal names what blocked it, both passes included
    last = attempts[-1]
    stderr = " ".join(
        str(probe.get("stderr") or "") + " " + str(probe.get("detail") or "")
        for probe in last["probes"]
    ).lower()
    if "executable file not found" in stderr or "not found in $path" in stderr:
        blocked_by = "no-shell-interpreter"
        blocked_detail = (
            "the probes are shell scripts and this image carries no shell "
            "interpreter at all (a scratch/distroless rootfs): the surface cannot "
            "be listed this way, and an adapter is never generated from a surface "
            "that was not listed"
        )
    elif declared_entrypoint:
        blocked_by = "entrypoint-and-probe-both-failed"
        blocked_detail = (
            f"the image declares ENTRYPOINT {declared_entrypoint} and neither pass "
            "ran the probes — the tool and the shell both failed"
        )
    else:
        blocked_by = "probe-failed"
        blocked_detail = "the probes did not run in the image"
    first = attempts[0]
    return {
        "inspected": False,
        "failure_class": str(
            next(
                (
                    probe.get("failure_class")
                    for probe in last["probes"]
                    if probe.get("failure_class")
                ),
                "surface_unavailable",
            )
        ),
        "image": image, "image_digest": image_digest,
        "entrypoint": declared_entrypoint,
        "entrypoint_bypass": bool(entrypoint_bypass and len(attempts) > 1),
        "entrypoint_blocked_by": blocked_by,
        "reason": (
            "the surface cannot be inspected: the probe(s) "
            + ", ".join(sorted(
                name for name, ok in (("identity", last["identity_ok"]),
                                      ("cli", last["cli_ok"])) if not ok
            ))
            + f" did not run in the image ({blocked_detail}); "
            + (
                f"the first pass (image entrypoint {declared_entrypoint}) failed too: "
                if first is not last and declared_entrypoint else ""
            )
            + "no adapter is generated from an uninspected surface"
        ),
        "blocked_by": blocked_by,
        "attempts": attempts,
        "probes": last["probes"],
    }


def _surface_of(
    *,
    attempts: list[dict],
    probes: list[dict],
    image: str,
    image_digest: str,
    declared_entrypoint: list[str],
    bypassed: bool,
    cli_probe: dict,
    identity: dict,
    api_probe: dict,
) -> dict:
    """The inspected surface of a successful pass (the shape the generator wants)."""
    os_release: dict[str, str] = {}
    for line in identity["stdout"].splitlines():
        key, _, value = line.partition("=")
        if key and value:
            os_release[key.strip()] = value.strip().strip('"')
    verbs = sorted(
        {
            token.strip()
            for token in cli_probe["stdout"].replace("\r", "\n").split()
            if token.strip() and not token.strip().startswith("/")
        }
    )
    api_tokens = [
        line.strip()
        for line in api_probe.get("stdout", "").splitlines()
        if line.strip() and not line.strip().endswith(":")
    ]
    server_hint = next((name for name in _API_SERVER_HINTS if name in verbs), "")
    mcp_executable = next(
        (token for token in api_tokens if "mcp" in token.lower() and "/" in token), ""
    )
    return {
        "inspected": True,
        "failure_class": "",
        "image": image,
        "image_digest": image_digest,
        "entrypoint": declared_entrypoint,
        "entrypoint_bypass": bool(bypassed),
        "os": os_release,
        "cli": {
            "verbs": verbs,
            "count": len(verbs),
            "probe": cli_probe["argv"],
        },
        "api": {
            "server_hint": server_hint,
            "paths": [token for token in api_tokens if token.startswith("/")],
            "note": (
                f"an API server binary is present: {server_hint}"
                if server_hint
                else "no API server binary in the inspected CLI surface"
            ),
        },
        "mcp": {
            "executable": mcp_executable,
            "note": (
                f"an MCP executable is present: {mcp_executable}"
                if mcp_executable
                else "no MCP executable in the inspected surface"
            ),
        },
        "probes": probes,
        "attempts": attempts,
        "inspected_at": time.time(),
    }


def _template_verbs(template: dict) -> tuple[str, ...]:
    """The verbs a template accepts — its candidates, or its single verb."""
    candidates = template.get("verb_candidates") or (template["verb"],)
    return tuple(str(item) for item in candidates)


def match_verb_template(capability: str, verbs: list[str] | set[str]) -> dict:
    """Resolve a capability to ONE verb template that the surface really offers.

    A template may name *candidates* instead of one verb (``magick`` or the
    deprecated ``convert`` on ImageMagick v6) — the surface decides which one is
    real, and the resolved verb is what the adapter records and renders.
    """
    offered = {str(verb) for verb in verbs}
    text = (capability or "").lower()
    scored: list[tuple[int, dict]] = []
    for template in VERB_TEMPLATES:
        resolved = next((name for name in _template_verbs(template) if name in offered), "")
        if not resolved:
            continue
        hits = sum(1 for term in template["terms"] if term in text)
        if hits:
            scored.append((hits, {**template, "verb": resolved}))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    if not scored:
        sample = ", ".join(sorted(offered)[:20]) or "none"
        raise AdapterRefusal(
            f"the inspected surface offers no container verb for "
            f"'{capability}' — refused instead of inventing an adapter "
            f"(surface verbs: {sample}). Pin a known verb with --verb, or pass "
            f"--schema FILE (a declared CLI fixture — see "
            f"skaldir.container_cli_templates.load_schema_template)"
        )
    top_hits, top = scored[0]
    if len(scored) > 1 and scored[1][0] == top_hits:
        raise AdapterRefusal(
            f"ambiguous capability '{capability}': {top['verb']} and "
            f"{scored[1][1]['verb']} both match — name one verb explicitly"
        )
    return top


def generate_container_adapter(
    capability: str,
    *,
    name: str,
    image: str,
    image_digest: str,
    surface: dict,
    template: dict | None = None,
) -> dict:
    """The TYPED adapter: which container call, which inputs, which outputs.

    ``template`` pins the verb deliberately (a caller may name it); without one
    the template is resolved from the capability against the verbs the surface
    really listed — and nothing is generated when that fails.
    """
    if not surface.get("inspected"):
        raise SurfaceRefusal(
            "no inspected surface — an adapter cannot be derived from "
            f"{surface.get('reason') or 'an unknown surface'}"
        )
    verbs = list((surface.get("cli") or {}).get("verbs") or [])
    chosen = template or match_verb_template(capability, verbs)
    if chosen["verb"] not in set(verbs):
        raise AdapterRefusal(
            f"verb '{chosen['verb']}' does not exist in the inspected surface"
        )
    adapter = {
        "schema_version": 1,
        "name": name,
        "capability": capability,
        "runtime": MANIFEST_V2_RUNTIME,
        "image": image,
        "image_digest": image_digest,
        "verb": chosen["verb"],
        "template_id": chosen["id"],
        "argv_template": [dict(part) if isinstance(part, dict) else part
                          for part in chosen["argv_template"]],
        "operation": {
            "id": chosen["verb"],
            "inputs": dict(chosen["inputs"]),
            "outputs": dict(chosen["outputs"]),
        },
        "permissions": dict(CONTAINER_PERMISSIONS),
        "service": _service_spec_of(chosen),
        "surface": {
            "inspected_at": float(surface.get("inspected_at") or time.time()),
            "os": dict(surface.get("os") or {}),
            "verbs_found": int((surface.get("cli") or {}).get("count") or 0),
            "api": dict(surface.get("api") or {}),
            "mcp": dict(surface.get("mcp") or {}),
        },
        "timeout_seconds": int(chosen.get("timeout_seconds") or 60),
        "created_at": time.time(),
        "created_by": "godsv-toolbuilder",
    }
    # an argument-mapping template carries the shape of its call: the kind of
    # subcommand, the fixed shell prologue, the scratch names its argv refers to,
    # how the source file travels, and how the outputs come back. A verb template
    # declares none of these and its adapter stays exactly as it was.
    for key in ("kind", "runner", "scratch", "input_transfer", "output_protocol"):
        value = chosen.get(key)
        if value in (None, "", [], {}):
            continue
        adapter[key] = (
            [dict(part) if isinstance(part, dict) else part for part in value]
            if isinstance(value, list)
            else (dict(value) if isinstance(value, dict) else value)
        )
    if surface.get("entrypoint_bypass"):
        # the surface was only reachable with the image's ENTRYPOINT cleared, so
        # every call of this adapter must clear it too — `--entrypoint ""`
        adapter["entrypoint"] = ""
        adapter["entrypoint_declared"] = list(surface.get("entrypoint") or [])
    return adapter


def _service_spec_of(chosen: dict) -> dict:
    """The warm-service spec of an adapter — declared by the template or default.

    The default is the isolated keepalive (``--network none``, no ports). A
    template MAY declare its own service: the long-running ``argv`` plus, when
    the service must actually SERVE (a dashboard), a ``network`` and ``ports``
    mapping (``{"container_port": host_port}``) — that is how a mined tool gets
    its interactive localhost dashboard (VAL-TOOL-001, ports 3400-3499). A
    declaration without an argv is refused (nothing invented).
    """
    service = {
        "mode": "warm-exec",
        "purpose": (
            "reuse ONE started container for repeated calls (skip the "
            "container cold start); discovery.ensure_ready starts it, "
            "rollback removes it by label"
        ),
        "argv": ["sh", "-c", "while true; do sleep 30; done"],
    }
    declared = dict(chosen.get("service") or {})
    if not declared:
        return service
    if not [str(item) for item in (declared.get("argv") or [])]:
        raise AdapterRefusal(
            "the template declares a service without an argv — the warm "
            "container is started from its declared command, nothing is invented"
        )
    service.update(
        {
            str(key): value
            for key, value in declared.items()
            if value not in (None, "", [], {})
        }
    )
    return service


def render_container_argv(adapter: dict, payload: dict) -> list[str]:
    """Render the container command for one payload — inputs are validated.

    One rule, one place: the work is done by :func:`skaldir.container_adapter.render`,
    which is the same code the generated module runs (a copy of it is staged next
    to the tool). Verb templates render exactly as they always did — literal parts
    with ``{param}`` substitution; argument-mapping templates also get typed
    parameters, conditional flags, the resolved verb and the file transfer.
    """
    return container_adapter.render(adapter, payload)


def parse_container_stdout(
    adapter: dict, stdout: str, *, keep_artefacts: bool = False
) -> tuple[bool, dict, str]:
    """Parse the container's stdout into the declared outputs.

    Verb templates: one pattern per output, matched against the whole stdout — as
    before. Keyed templates: one line per declared output, plus the artefact facts
    derived locally from the bytes that arrived. See
    :func:`skaldir.container_adapter.parse_stdout`.
    """
    return container_adapter.parse_stdout(adapter, stdout, keep_artefacts=keep_artefacts)


def _template_of(adapter: dict) -> dict:
    """The generator entry an adapter was built from — by template id, or by verb."""
    template_id = str(adapter.get("template_id") or "")
    for item in VERB_TEMPLATES:
        if item["id"] == template_id:
            return item
    verb = str(adapter.get("verb") or "")
    for item in VERB_TEMPLATES:
        if verb in _template_verbs(item):
            return item
    raise AdapterRefusal(
        f"no local oracle for template '{template_id or adapter.get('verb')}' — a "
        "capability cannot be certified without an independent check "
        "(a host twin or a declared golden fixture, never the container alone)"
    )


def expected_outputs(adapter: dict, payload: dict, *, template: dict | None = None) -> dict:
    """The INDEPENDENT expectation of one call: ``{values, tolerance, note}``.

    ``values`` is keyed by the declared output it constrains; ``tolerance`` names
    the outputs whose comparison cannot be exact and holds ``(amount, reason)`` —
    declared next to the expectation instead of hidden in the comparison. The
    legacy verb oracles return one string and are normalised here, so the three
    hash-like templates behave exactly as they did.

    ``template`` is the generator entry (required for a schema/fixture template
    that is not in :data:`VERB_TEMPLATES`).
    """
    entry = template or _template_of(adapter)
    result = entry["oracle"](dict(payload))
    if isinstance(result, (str, int, float)):
        outputs = list((adapter.get("operation") or {}).get("outputs") or {})
        return {
            "values": {outputs[0] if outputs else "value": str(result)},
            "tolerance": {},
            "note": "",
        }
    return {
        "values": dict(result.get("values") or {}),
        "tolerance": dict(result.get("tolerance") or {}),
        "note": str(result.get("note") or ""),
    }


def _expected_for(adapter: dict, payload: dict) -> str:
    """The scalar expectation of an adapter (the verb templates' own shape)."""
    expectations = expected_outputs(adapter, payload)
    values = list(expectations["values"].values())
    return str(values[0]) if len(values) == 1 else json.dumps(
        expectations["values"], sort_keys=True
    )


def _compare(wanted: object, found: object, *, tolerance: object = None) -> tuple[bool, str]:
    """Does what the container returned agree with the independent expectation?

    Exact string equality is the rule (a digest, a geometry, a truncated file
    length). A declared tolerance is honoured only where the template declared one
    *and* its reason — the codec's frame granularity, never a comfortable margin
    around a number nobody can predict.
    """
    if found is None:
        return False, (
            f"the container returned nothing for this output (expected {wanted!r})"
        )
    if isinstance(found, dict) or isinstance(wanted, dict):
        left, right = json.dumps(found, sort_keys=True, default=str), json.dumps(
            wanted, sort_keys=True, default=str
        )
        if left == right:
            return True, f"{left} == {right} (exact)"
        return False, f"{left} != the expectation {right}"
    if str(found) == str(wanted):
        return True, f"{found} == {wanted} (exact)"
    if tolerance:
        try:
            delta = abs(float(str(found)) - float(str(wanted)))
        except (TypeError, ValueError):
            return False, f"{found!r} != the expectation {wanted!r}"
        if delta <= float(tolerance):
            return True, (
                f"{found} == {wanted} within the declared tolerance ±{tolerance}"
            )
        return False, (
            f"{found} differs from the expectation {wanted} by {delta:g}, beyond "
            f"the declared tolerance ±{tolerance}"
        )
    return False, f"{found!r} != the expectation {wanted!r}"


def container_tool_paths(runtime: str | Path, name: str) -> dict:
    """Every path of one container tool (one place, so nothing guesses)."""
    tool_dir = Path(runtime) / "generated_tools" / str(name)
    return {
        "tool_dir": tool_dir,
        "adapter": tool_dir / ADAPTER_FILE,
        "surface": tool_dir / "surface.json",
        "manifest": tool_dir / "manifest.yaml",
        "module": tool_dir / f"{name}.py",
        "wrapper": tool_dir / "bin" / str(name),
        "tests": tool_dir / "tests" / f"test_{name}.py",
        "evidence": tool_dir / CONTAINER_EVIDENCE_FILE,
        "rollbacks": Path(runtime) / CONTAINER_ROLLBACK_DIR / str(name),
        "cid_file": Path(runtime) / CONTAINER_SERVICE_DIR / f"{name}.cid",
    }


_CONTAINER_MODULE = '''"""Container adapter '__TOOL__' — generated by the godsv tool builder.

The runtime of this adapter is NOT in this file: it is
``container_adapter.py`` sitting next to it (a copy of
``skaldir.container_adapter``, staged when the tool was built) together with
``container_media.py``. The builder that certified this adapter runs the very
same code, so a rendered command here and a rendered command there cannot
diverge.

What this file does is the part that is about the CONTAINER: it reads
``adapter.json``, verifies that the local image still carries the PINNED digest,
runs the container one-shot (``docker run --rm``, network none,
no-new-privileges, read-only rootfs, the adapter's own ``--entrypoint`` when it
clears the image's), parses the declared outputs, writes the artefacts the
caller asked for to disk, and returns them. A digest change REFUSES the call
instead of running whatever happens to be local now.
"""
from __future__ import annotations

import json
import pathlib
import subprocess  # nosec B404 — one bounded docker call is this adapter's job
import sys

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:  # the runtime travels with the tool
    sys.path.insert(0, str(_HERE))

import container_adapter  # noqa: E402 — a sibling copy, not an import of the app

ADAPTER = json.loads((_HERE / "adapter.json").read_text(encoding="utf-8"))


def local_digest() -> str:
    """The digest of the local image ('' when it is gone)."""
    proc = subprocess.run(  # nosec B603 — fixed argv, no shell
        ["docker", "image", "inspect", "--format", "{{.Id}}", str(ADAPTER["image"])],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


def render(payload: dict) -> list[str]:
    """The container argv for one payload — the shared runtime renders it."""
    return container_adapter.render(ADAPTER, payload)


def docker_argv(rendered: list[str]) -> list[str]:
    """`docker run` for this adapter — the same isolation, one place."""
    perms = dict(ADAPTER.get("permissions") or {})
    argv = ["docker", "run", "--rm", "--network", str(perms.get("network") or "none")]
    argv += ["--security-opt", "no-new-privileges"]
    if perms.get("read_only_rootfs", True):
        argv += ["--read-only"]
    limits = dict(perms.get("resource_limits") or {})
    if limits.get("cpu"):
        argv += ["--cpus=" + str(limits["cpu"])]
    if limits.get("ram"):
        argv += ["--memory=" + str(limits["ram"])]
    argv += ["--label", "godsv.owner=skaldir"]
    argv += ["--label", "godsv.capability=" + str(ADAPTER.get("name") or "")]
    argv += ["--label", "godsv.purpose=call"]
    if ADAPTER.get("entrypoint") is not None:
        # '' clears the image ENTRYPOINT the surface was only reachable without
        argv += ["--entrypoint", str(ADAPTER["entrypoint"])]
    argv += [str(ADAPTER["image_digest"])]
    argv += list(rendered)
    return argv


def call(payload: dict) -> dict:
    """Run the pinned container once and return the declared outputs."""
    pinned = str(ADAPTER.get("image_digest") or "")
    actual = local_digest()
    if not pinned or actual != pinned:
        raise RuntimeError(
            "pinned digest mismatch: the adapter pins " + (pinned or "nothing")
            + ", the local image is " + (actual or "absent")
            + " — refusing to run a different image"
        )
    rendered = render(payload)
    proc = subprocess.run(  # nosec B603 — fixed argv, no shell
        docker_argv(rendered),
        capture_output=True,
        text=True,
        timeout=int(ADAPTER.get("timeout_seconds") or 60),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "container exited " + str(proc.returncode) + ": "
            + (proc.stderr or proc.stdout or "").strip()[:200]
        )
    ok, parsed, detail = container_adapter.parse_stdout(
        ADAPTER, proc.stdout or "", keep_artefacts=True
    )
    if not ok:
        raise RuntimeError(detail)
    container_adapter.write_artefacts(ADAPTER, payload, parsed)
    return parsed


def main(**payload) -> dict:
    return call(payload)


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(main(**json.loads(sys.argv[1] if len(sys.argv) > 1 else "{}"))))
'''


_CONTAINER_WRAPPER = '''#!/usr/bin/env python3
"""CLI wrapper for the generated container tool '__TOOL__'."""
import json
import sys
import time
_ROOT = __import__('pathlib').Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
import __MODULE__ as _tool


def _scrub(text):
    try:
        from skaldir.secretscan import redact
        return redact(str(text))[0]
    except Exception:
        # Do NOT silently return unredacted text — that leaks secrets into logs.
        # Return a placeholder that names the failure instead.
        return "[scrub failed]"


def _record(ok, error='', started=None):
    try:
        import json as _json
        import pathlib as _pl
        log = _ROOT.parent.parent / 'tool_usage.jsonl'
        entry = {'ts': time.time(), 'name': __NAME__, 'ok': bool(ok)}
        if started is not None:
            entry['duration_ms'] = int((time.monotonic() - started) * 1000)
        if error:
            entry['error'] = _scrub(error)[:300]
        with log.open('a', encoding='utf-8') as fh:
            fh.write(_json.dumps(entry, ensure_ascii=False) + '\\n')
    except Exception:
        pass


if __name__ == '__main__':
    payload = {}
    if len(sys.argv) > 1:
        payload = json.loads(sys.argv[1])
    _started = time.monotonic()
    try:
        out = _tool.main(**payload)
    except Exception as exc:
        _record(False, f'{type(exc).__name__}: {exc}', _started)
        raise
    _record(True, '', _started)
    print(json.dumps(out, ensure_ascii=False, default=str))
'''


_CONTAINER_TEST = '''"""Adapter test for '__TOOL__' — runs the REAL container (no docker: skipped).

The expectation comes from the local stdlib oracle the builder certified this
adapter with (read from the SOURCE the payload names, never from the container),
and every artefact field is read from the returned BYTES by ``container_media`` —
staged next to this tool — instead of being taken from the container's word.
"""
import json
import pathlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parents[1]
if str(_HERE) not in sys.path:  # the runtime travels with the tool
    sys.path.insert(0, str(_HERE))

import container_media  # noqa: E402 — a sibling copy

_WRAPPER = _HERE / "bin" / "__TOOL__"
_EXPECTED = __EXPECTED__
_TOLERANCE = __TOLERANCE__
_PAYLOAD = __PAYLOAD__
_HOST_PARAMS = __HOST_PARAMS__
_CHECKS = __CHECKS__


pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker is not installed"
)


def _payload_for(tmp_path: pathlib.Path) -> dict:
    """The recorded call, with its host output paths moved into the temp dir."""
    payload = dict(_PAYLOAD)
    for param in _HOST_PARAMS:
        suffix = pathlib.Path(str(payload.get(param, ""))).suffix
        payload[param] = str(tmp_path / (str(param) + suffix))
    return payload


def test_adapter_returns_the_independently_computed_value(tmp_path):
    proc = subprocess.run(
        [str(_WRAPPER), json.dumps(_payload_for(tmp_path))],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    parsed = json.loads(proc.stdout)
    for name, wanted in _EXPECTED.items():
        found = parsed[name]
        tolerance = _TOLERANCE.get(name)
        if tolerance:
            # the declared slack of a codec's frame granularity, not a habit:
            # it is recorded next to the expectation it applies to
            assert abs(float(found) - float(wanted)) <= float(tolerance), (
                str(name) + ": " + str(found) + " differs from " + str(wanted)
                + " by more than the declared tolerance " + str(tolerance)
            )
        else:
            assert str(found) == str(wanted), (
                str(name) + ": " + str(found) + " != " + str(wanted)
            )
    for check in _CHECKS:
        written = parsed[check["output"]].get("path", "")
        assert written, "the adapter wrote no artefact for " + str(check["output"])
        data = pathlib.Path(written).read_bytes()
        assert data, "the artefact is empty"
        found = container_media.field_of(data, check["field"])
        assert found == check["expected"], (
            check["field"] + ": " + str(found) + " != " + str(check["expected"])
        )


def test_adapter_refuses_a_changed_pinned_digest():
    from importlib import util as _util

    spec = _util.spec_from_file_location("_adapter_module", _HERE / "__MODULE__.py")
    module = _util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = module.ADAPTER["image_digest"]
    module.ADAPTER = dict(module.ADAPTER, image_digest="sha256:" + "0" * 64)
    try:
        with pytest.raises(RuntimeError):
            module.call(_PAYLOAD)
    finally:
        module.ADAPTER = dict(module.ADAPTER, image_digest=original)
    assert original
'''


def _write_text(path: Path, text: str, *, executable: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)
    return path


class ContainerToolBuilder(ToolBuilder):
    """The container half: mine a capability from an image, prove it, register it.

    Inherits the deterministic pipeline plumbing (runtime dir, tools DB, build
    queue, health ledger) and adds the container stages on top: inspect the
    surface, generate the typed adapter, test it against the real container,
    register it with version/permissions/rollback and take it back again.
    """

    # -- (1) search --------------------------------------------------------- #
    def search_existing_adapter(self, capability: str, *, name: str = "") -> dict:
        """Search BEFORE building: an existing capability is reused, never rebuilt."""
        from .capability_mining import _BUILD_REFUSE_OVERLAP, search_before_build

        verdict = search_before_build(
            self.runtime, capability, name=name, min_overlap=_BUILD_REFUSE_OVERLAP
        )
        return {
            "decision": verdict["decision"],
            "searched": verdict["searched"],
            "match": verdict.get("match"),
            "candidates": verdict.get("candidates") or [],
        }

    # -- (2)+(3)+(4) mine: inspect → adapt → test ---------------------------- #
    def mine_container(
        self,
        capability: str,
        *,
        image: str = "",
        name: str = "",
        image_digest: str = "",
        pull: bool = False,
        verb: str = "",
        test_input: dict | None = None,
        expect: str = "",
        session_id: str = "",
        schema: dict | None = None,
    ) -> dict:
        """Inspect a container image, generate a typed adapter, TEST it for real.

        Nothing is written before the surface is inspected and the adapter has
        passed a test against the real container; the staged tool is NOT
        registered (`register_container_tool` does that, and only on evidence).

        ``schema`` is a declared CLI fixture template (see
        :mod:`skaldir.container_cli_templates`): used when no catalogue family
        matches, instead of inventing an adapter. Its oracle is a golden fixture
        or a caller-supplied function — never "the container agrees with itself".
        """
        manager = self.manager()
        started = time.time()
        name = _slug(name or capability)
        result: dict = {
            "ok": False, "name": name, "capability": capability,
            "image": image, "image_digest": "", "search": {}, "surface": {},
            "adapter": {}, "test": {}, "staged": False, "stages": [],
            "failure_class": "", "reason": "",
        }

        def stage(label: str, ok: bool, **extra: object) -> None:
            result["stages"].append(
                {"stage": label, "ok": bool(ok), "seconds": round(time.time() - started, 3),
                 **extra}
            )

        # (1) search — never build what already exists
        search = self.search_existing_adapter(capability, name=name)
        result["search"] = search
        repair = bool(
            name and str((search.get("match") or {}).get("name") or "") == name
        )
        if search["decision"] == "reuse" and repair:
            # the requested NAME itself is the deliberate repair path (§1.7):
            # rebuilding it is intended — and the health ledger downgrades the
            # old evidence (code changed) until the new adapter is registered.
            result["search"]["decision"] = "repair"
            search["decision"] = "repair"
        stage("search", search["decision"] != "reuse", decision=search["decision"])
        if search["decision"] == "reuse":
            match = search.get("match") or {}
            result["failure_class"] = "reuse"
            result["reason"] = (
                f"reuse instead of duplicate: '{match.get('name')}' "
                f"({match.get('surface')}) already provides this capability"
            )
            self._record("", "container_mine", capability=capability, name=name,
                         decision="reuse", match=match)
            return result

        # (2) resolve + pin the image (local inspect; pull only when asked)
        if not image:
            result["failure_class"] = "image_required"
            result["reason"] = "no image given — the capability needs a pinned container image"
            stage("resolve_image", False, reason=result["reason"])
            return result
        digest = image_digest or manager.resolve_digest(image)
        if pull and not digest:
            pulled = manager.pull(image)
            digest = pulled.stdout.strip() if pulled.ok else ""
            if not pulled.ok:
                result["failure_class"] = pulled.failure_class or "docker_error"
                result["reason"] = f"pull failed ({pulled.failure_class}): {pulled.detail}"
                stage("resolve_image", False, reason=result["reason"])
                return result
        if not digest:
            result["failure_class"] = "image_missing"
            result["reason"] = (
                f"image '{image}' is not local and no pinned digest is known — "
                "run with --pull (or pin a digest) instead of building from a tag"
            )
            stage("resolve_image", False, reason=result["reason"])
            return result
        result["image_digest"] = digest
        stage("resolve_image", True, image=image, image_digest=digest)

        # (2) inspect the container's CLI/API/MCP surface
        surface = inspect_container_surface(
            manager, image=image, image_digest=digest
        )
        result["surface"] = surface
        stage("inspect_surface", bool(surface["inspected"]),
              verbs=int((surface.get("cli") or {}).get("count") or 0))
        if not surface["inspected"]:
            result["failure_class"] = surface.get("failure_class") or "surface_unavailable"
            result["reason"] = surface.get("reason") or "the surface could not be inspected"
            return result

        # (3) generate the typed adapter from what the surface really offers
        template = (
            next(
                (
                    {**item, "verb": next(
                        (name for name in _template_verbs(item) if name in set(
                            (surface.get("cli") or {}).get("verbs") or []
                        )),
                        item["verb"],
                    )}
                    for item in VERB_TEMPLATES
                    if item["id"] == verb or verb in _template_verbs(item)
                ),
                None,
            )
            if verb
            else None
        )
        if verb and template is None and schema is not None:
            # a declared CLI fixture for this exact verb (W2 general path)
            template = {**schema, "verb": verb} if schema.get("verb") != verb else schema
        if verb and template is None and schema is None:
            result["failure_class"] = "unknown_verb"
            result["reason"] = (
                f"no generator knows the verb '{verb}' — known templates: "
                + ", ".join(
                    f"{item['id']} (" + "/".join(_template_verbs(item)) + ")"
                    for item in VERB_TEMPLATES
                )
                + ". Pass --schema FILE for a declared CLI fixture (container_cli_templates)"
            )
            stage("generate_adapter", False, reason=result["reason"])
            return result
        if template is None and schema is not None:
            template = schema
        try:
            adapter = generate_container_adapter(
                capability, name=name, image=image, image_digest=digest,
                surface=surface, template=template,
            )
        except (AdapterRefusal, SurfaceRefusal) as exc:
            result["failure_class"] = "adapter_refused"
            result["reason"] = str(exc)
            stage("generate_adapter", False, reason=str(exc))
            return result
        result["adapter"] = adapter
        stage("generate_adapter", True, verb=adapter["verb"],
              template=adapter["template_id"])

        # (4) TEST against the real container, checked against a local oracle
        template_entry = next(
            (
                item for item in VERB_TEMPLATES if item["id"] == adapter["template_id"]
            ),
            template or {},
        )
        payload = dict(test_input or template_entry.get("sample_input") or {})
        missing_test_input = [
            param for param, spec in (adapter["operation"]["inputs"]).items()
            if str((spec or {}).get("type")) == "path" and (spec or {}).get("required")
            and param not in payload
        ]
        if missing_test_input:
            result["failure_class"] = "test_input_required"
            result["reason"] = (
                "this template's call is over FILES, so the certification test "
                "needs real ones: pass --test-input naming "
                + ", ".join(missing_test_input)
                + " (a host path per declared path input) — no test is invented "
                "from a template default"
            )
            stage("test_adapter", False, reason=result["reason"])
            return result
        test = self._test_adapter(
            adapter,
            payload=payload,
            expect=expect,
            timeout=float(adapter.get("timeout_seconds") or 60),
            template=template_entry or template,
        )
        result["test"] = test
        stage("test_adapter", test["ok"], detail=test.get("detail", "")[:160])
        if not test["ok"]:
            result["failure_class"] = "test_failed"
            result["reason"] = test.get("detail") or "the adapter test failed"
            return result

        # stage the artifacts (still NOT registered — evidence does that)
        paths = container_tool_paths(self.runtime, name)
        result["staged"] = self._stage_container_tool(
            adapter, paths, test, payload, surface=surface
        )
        stage("stage", bool(result["staged"]), tool_dir=str(paths["tool_dir"]))
        result["ok"] = bool(result["staged"])
        result["adapter_path"] = str(paths["adapter"])
        result["evidence_path"] = str(paths["evidence"])
        result["tool_dir"] = str(paths["tool_dir"])
        result["wrapper"] = str(paths["wrapper"])
        self._record("", "container_mine", capability=capability, name=name,
                     image=image, image_digest=digest, verb=adapter["verb"],
                     tested=bool(test["ok"]))
        return result

    def _test_adapter(
        self,
        adapter: dict,
        *,
        payload: dict,
        expect: str = "",
        timeout: float = 60.0,
        template: dict | None = None,
    ) -> dict:
        """Run the generated adapter ONCE against the real container.

        The expectation comes from the independent local oracle
        (:func:`expected_outputs`): for a verb template one string, for an
        argument-mapping template one value per output it constrains, each with
        its own declared tolerance where an exact comparison is impossible. The
        observed values come from the container's answer — for a file output, from
        the *bytes that arrived*, read by the host's own parser
        (:mod:`skaldir.container_media`). ``template`` carries the oracle for a
        schema/fixture generator that is not in :data:`VERB_TEMPLATES`.
        """
        manager = self.manager()
        started = time.time()
        try:
            expectations = expected_outputs(adapter, payload, template=template)
        except AdapterRefusal as exc:
            return {"ok": False, "detail": str(exc), "input": payload}
        values = dict(expectations["values"])
        tolerances = dict(expectations["tolerance"])
        scalar = list(values.values())[0] if len(values) == 1 else ""
        reported = (
            str(scalar) if len(values) == 1
            else json.dumps(values, sort_keys=True)
        )
        if expect:
            declared = expect.strip().lower()
            if not any(declared == str(item).strip().lower() for item in values.values()):
                return {
                    "ok": False, "input": payload, "expected": reported,
                    "detail": (
                        f"the declared expectation {expect[:80]!r} disagrees with the "
                        f"independent local oracle ({reported})"
                    ),
                }
        try:
            rendered = render_container_argv(adapter, payload)
        except AdapterRefusal as exc:
            return {"ok": False, "input": payload, "detail": str(exc)}
        config = _run_config(
            image=adapter.get("image", ""), image_digest=adapter["image_digest"],
            argv=rendered, labels={PURPOSE_LABEL: PURPOSE_TEST,
                                   CAPABILITY_LABEL: str(adapter.get("name") or "")},
            resource_limits=dict((adapter.get("permissions") or {}).get("resource_limits") or {}),
            entrypoint=adapter.get("entrypoint"),
        )
        run = manager.run(config, timeout=timeout)
        # the RECORD masks a transferred source's bytes (the command that ran
        # carried them): evidence, manifest and envelopes are files on disk
        recorded_argv = container_adapter.redacted_argv(adapter, payload, rendered)
        parsed_ok, parsed, parse_detail = parse_container_stdout(
            adapter, getattr(run, "stdout", "") or "", keep_artefacts=True
        )
        written = container_adapter.write_artefacts(adapter, payload, parsed)
        verdicts: list[dict] = []
        for out_name, wanted in values.items():
            found = parsed.get(out_name)
            tolerance, reason = tolerances.get(out_name, (None, ""))
            agrees, detail = _compare(wanted, found, tolerance=tolerance)
            verdicts.append({
                "output": out_name, "expected": wanted,
                "observed": found if not isinstance(found, dict) else dict(found),
                "agrees": bool(agrees),
                "tolerance": tolerance, "tolerance_reason": reason, "detail": detail,
            })
        observed = (
            parsed.get(list(values)[0]) if len(values) == 1
            else {name: parsed.get(name) for name in values}
        )
        agreed = all(item["agrees"] for item in verdicts)
        ok = bool(getattr(run, "ok", False)) and parsed_ok and bool(verdicts) and agreed
        detail = parse_detail or "; ".join(
            f"{item['output']}: {item['detail']}" for item in verdicts if not item["agrees"]
        ) or (
            "" if ok else (
                f"container exit {getattr(run, 'exit_code', None)}: "
                f"{(getattr(run, 'stderr', '') or getattr(run, 'detail', ''))[:160]}"
            )
        )
        return {
            "ok": bool(ok),
            "input": payload,
            "expected": reported,
            "observed": observed if not isinstance(observed, dict) else json.dumps(
                observed, sort_keys=True, default=str
            ),
            "expectations": values,
            "verdicts": verdicts,
            "oracle": "local stdlib implementation (independent of the container)",
            "oracle_note": expectations["note"],
            "argv": recorded_argv,
            "outputs": {
                name: (dict(entry) if isinstance(entry, dict) else entry)
                for name, entry in parsed.items()
            },
            "artefacts": written,
            "exit_code": getattr(run, "exit_code", None),
            "stdout": str(getattr(run, "stdout", "") or "")[:400],
            "stderr": str(getattr(run, "stderr", "") or "")[:400],
            "failure_class": str(getattr(run, "failure_class", "") or ""),
            "detail": detail,
            "duration_ms": int((time.time() - started) * 1000),
            "isolation": {
                "mode": "one-shot docker run --rm",
                "network": "none",
                "read_only_rootfs": True,
                "no_new_privileges": True,
                "privileged": False,
                "image_digest": adapter["image_digest"],
                "entrypoint": adapter.get("entrypoint", None),
            },
        }

    def _stage_container_tool(
        self, adapter: dict, paths: dict, test: dict, payload: dict,
        surface: dict | None = None,
    ) -> bool:
        """Write adapter.json, the module, the wrapper, the manifest and the test.

        The tool directory is made **self-contained**: the shared runtime
        (``container_adapter.py``) and the stdlib artefact readers
        (``container_media.py``) are copied next to the generated module, so the
        module runs the same render/parse code the builder certified it with, and
        it can run without the application on the path.
        """
        name = str(adapter["name"])
        blob = json.dumps(adapter, indent=2, sort_keys=True)
        try:
            _write_text(paths["adapter"], blob + "\n")
            if surface:
                # the full inspection record stays on disk (the envelope carries
                # the summary): which probes ran, what they returned, what the
                # image offers — the evidence behind the generated adapter
                _write_text(
                    paths["surface"],
                    json.dumps(surface, indent=2, sort_keys=True, default=str),
                )
            module_name = f"container_{name}"[:60]
            _write_text(paths["tool_dir"] / f"{module_name}.py",
                        _CONTAINER_MODULE.replace("__TOOL__", name))
            for module in (container_adapter, container_media):
                source_file = str(getattr(module, "__file__", "") or "")
                if not source_file:
                    continue
                source = Path(source_file).read_text(encoding="utf-8")
                if module is container_adapter:
                    # the staged copy is a sibling of the generated module, not a
                    # member of this package: its one package-relative import
                    # becomes a plain one (and is checked by the staging test)
                    source = source.replace(
                        "from . import container_media", "import container_media"
                    )
                _write_text(paths["tool_dir"] / f"{module.__name__.split('.')[-1]}.py",
                            source)
            module_path = paths["tool_dir"] / f"{module_name}.py"
            paths["module"] = module_path
            paths["wrapper"] = _write_text(
                paths["tool_dir"] / "bin" / name,
                _CONTAINER_WRAPPER.replace("__TOOL__", name)
                .replace("__MODULE__", module_name)
                .replace("__NAME__", repr(name)),
                executable=True,
            )
            outputs = dict((adapter.get("operation") or {}).get("outputs") or {})
            host_params = sorted(
                {
                    str((spec or {}).get("host_path_param") or "")
                    for spec in outputs.values()
                    if (spec or {}).get("host_path_param")
                }
            )
            observed = dict(test.get("outputs") or {})
            checks = [
                {
                    "output": str((spec.get("derive") or {}).get("from") or ""),
                    "field": str((spec.get("derive") or {}).get("field") or ""),
                    "expected": observed.get(str(out_name)),
                }
                for out_name, spec in outputs.items()
                if (spec or {}).get("derive")
            ]
            _write_text(
                paths["tool_dir"] / "tests" / f"test_{name}.py",
                _CONTAINER_TEST.replace("__TOOL__", name)
                .replace("__MODULE__", module_name)
                .replace("__EXPECTED__", repr(dict(test.get("expectations") or {
                    next(iter(outputs), ""): test.get("expected"),
                })))
                .replace("__TOLERANCE__", repr({
                    str(item.get("output")): item.get("tolerance")
                    for item in (test.get("verdicts") or [])
                    if item.get("tolerance")
                }))
                .replace("__PAYLOAD__", repr(dict(payload)))
                .replace("__HOST_PARAMS__", repr(host_params))
                .replace("__CHECKS__", repr(checks)),
            )
            manifest = self._container_manifest(adapter, name=name, status="built",
                                               module_name=module_name,
                                               test=test, payload=payload)
            _write_text(paths["manifest"], yaml.safe_dump(manifest, sort_keys=False))
            evidence = dict(test)
            evidence.update(
                {
                    # the digest of the BYTES on disk — registration compares it
                    # against the file, so an adapter edited after the test is
                    # never registered on stale evidence
                    "adapter_digest": _sha256_file(paths["adapter"]),
                    "adapter_file": str(paths["adapter"]),
                    "tested_at": time.time(),
                    "registered": False,
                }
            )
            _write_text(paths["evidence"], json.dumps(evidence, indent=2, sort_keys=True))
        except OSError as exc:
            self._record("", "container_stage", name=name, error=str(exc))
            return False
        return True

    def _container_manifest(
        self, adapter: dict, *, name: str, status: str, module_name: str,
        version: str = "0.1.0", project_id: str = "", test: dict | None = None,
        payload: dict | None = None,
    ) -> dict:
        perms = dict(adapter.get("permissions") or CONTAINER_PERMISSIONS)
        entrypoint_argv = self._manifest_argv(adapter, test=test, payload=payload)
        return {
            "name": name,
            "version": version,
            "scope": "project",
            "project_id": project_id,
            "type": "cli",
            "purpose": adapter.get("capability", ""),
            "created_for": adapter.get("capability", ""),
            "created_by": "godsv-toolbuilder",
            "commands_or_tools": [adapter.get("verb", "")],
            "network": {"allowed_domains": list(perms.get("allowed_domains") or [])},
            "filesystem": {
                "access": "container rootfs, read-only; no host mounts",
                "mounts": list(perms.get("mounts") or []),
            },
            "required_secrets": list(perms.get("secrets") or []),
            "dependencies": [],
            "tests": [sys.executable, "-m", "pytest", "-q", "tests/"],
            "risk_level": "low",
            "status": status,
            "created_at": time.time(),
            "last_tested_at": None,
            "runtime": MANIFEST_V2_RUNTIME,
            "image": adapter.get("image", ""),
            "image_digest": adapter.get("image_digest", ""),
            "entrypoint_argv": entrypoint_argv,
            "argv_template": [
                dict(part) if isinstance(part, dict) else part
                for part in (adapter.get("argv_template") or [])
            ],
            "runner": list(adapter.get("runner") or []),
            "entrypoint": adapter.get("entrypoint", None),
            "mounts": list(perms.get("mounts") or []),
            "resource_limits": dict(perms.get("resource_limits") or {}),
            "require_bind_ro": True,
            "healthcheck": {"verb": adapter.get("verb", ""), "runtime": "docker"},
            "permissions": perms,
            "adapter": {"file": ADAPTER_FILE, "module": module_name,
                        "template": adapter.get("template_id", "")},
            "surface": dict(adapter.get("surface") or {}),
            "validation": {"status": "not_declared"},
        }

    @staticmethod
    def _manifest_argv(adapter: dict, *, test: dict | None, payload: dict | None) -> list[str]:
        """The manifest's ``entrypoint_argv``: real argv for this call, never a spec.

        A verb template's argv template *is* plain argv. An argument-mapping
        template's is a mapping plus a transfer, so the manifest carries the argv
        that was actually rendered for the certified call, with a transferred
        source's bytes masked out (a manifest is a file on disk; the payload that
        names the paths sits beside it, so the call re-renders from adapter.json).
        """
        rendered = list((test or {}).get("argv") or [])
        if rendered:
            return rendered
        try:
            return render_container_argv(adapter, dict(payload or {}))
        except AdapterRefusal:
            return [
                str(part) if not isinstance(part, dict) else str(
                    part.get("flag") or part.get("const") or part.get("transfer") or ""
                )
                for part in (adapter.get("argv_template") or [])
            ]

    # -- (5) register --------------------------------------------------------- #
    def _load_container_tool(self, name: str) -> dict:
        paths = container_tool_paths(self.runtime, name)
        if not paths["adapter"].is_file():
            return {"ok": False, "failure_class": "not_found",
                    "reason": f"no staged container adapter for '{name}'",
                    "paths": {key: str(value) for key, value in paths.items()}}
        try:
            adapter = json.loads(paths["adapter"].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "failure_class": "adapter_unreadable",
                    "reason": f"adapter.json is not readable: {exc}",
                    "paths": {}}
        evidence: dict = {}
        if paths["evidence"].is_file():
            try:
                evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                evidence = {}
        return {"ok": True, "adapter": adapter, "evidence": evidence,
                "paths": {key: str(value) for key, value in paths.items()}}

    def container_adapter(self, name: str) -> dict:
        """The typed adapter of a staged/registered container tool."""
        loaded = self._load_container_tool(name)
        if not loaded["ok"]:
            return loaded
        adapter = loaded["adapter"]
        manifest = {}
        path = Path(loaded["paths"]["manifest"])
        if path.is_file():
            try:
                manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001 — a broken manifest is reported as absent
                manifest = {}
        return {
            "ok": True,
            "name": name,
            "adapter": adapter,
            "manifest": manifest,
            "permissions": dict(adapter.get("permissions") or {}),
            "test": loaded["evidence"],
            "wrapper": loaded["paths"]["wrapper"],
            "tool_dir": loaded["paths"]["tool_dir"],
        }

    def register_container_tool(
        self, name: str, *, version: str = "", project_id: str = "",
        requested_by: str = "cli",
    ) -> dict:
        """Register a TESTED container adapter live — evidence, or nothing.

        Refuses when the recorded test did not pass, when the adapter changed
        after that test (digest binding), or when the tool is not staged at all.
        """
        from . import capability_health as health

        loaded = self._load_container_tool(name)
        if not loaded["ok"]:
            return loaded
        adapter = loaded["adapter"]
        evidence = loaded["evidence"]
        paths = container_tool_paths(self.runtime, name)
        digest_now = _sha256_file(Path(loaded["paths"]["adapter"]))
        if not evidence:
            return {
                "ok": False, "failure_class": "unverified",
                "reason": (
                    f"'{name}' has no test evidence — a capability is only "
                    "registered after its adapter ran against the real container"
                ),
            }
        if not evidence.get("ok"):
            return {
                "ok": False, "failure_class": "unverified",
                "reason": (
                    "the recorded adapter test did not pass: "
                    + str(evidence.get("detail") or "no detail")[:200]
                ),
            }
        if str(evidence.get("adapter_digest") or "") != digest_now:
            return {
                "ok": False, "failure_class": "adapter_changed",
                "reason": (
                    "adapter.json changed after the test — the evidence no longer "
                    "covers these bytes; re-run `tools mine` instead of registering"
                ),
            }

        previous = self.store.get(name)
        version = version or self._next_version(previous)
        module_name = str(
            (yaml.safe_load(paths["manifest"].read_text(encoding="utf-8")) or {}).get(
                "adapter", {}
            ).get("module", f"container_{name}"[:60])
        ) if paths["manifest"].is_file() else f"container_{name}"[:60]
        manifest = self._container_manifest(
            adapter, name=name, status="ready", module_name=module_name,
            version=version, project_id=project_id, test=evidence,
            payload=dict(evidence.get("input") or {}),
        )
        manifest["last_tested_at"] = float(evidence.get("tested_at") or time.time())
        comparison = "; ".join(
            f"{item.get('output')}: {item.get('detail')}"
            for item in (evidence.get("verdicts") or [])
        )
        manifest["validation"] = {
            "status": "validated",
            "at": evidence.get("tested_at"),
            "detail": (
                f"adapter ran against {adapter.get('image')}@{adapter.get('image_digest')}: "
                + (
                    comparison
                    or f"{evidence.get('observed')} == {evidence.get('expected')} (local oracle)"
                )
                + " (local oracle)"
            )[:300],
        }
        manifest["required_secrets"] = []
        perms = dict(adapter.get("permissions") or CONTAINER_PERMISSIONS)

        spec = ToolSpec(
            name=name,
            title=f"Container: {adapter.get('capability', name)}",
            description=(
                f"{adapter.get('capability')} — runs `{adapter.get('verb')}` in "
                f"{adapter.get('image')} pinned at {adapter.get('image_digest')}; "
                "network none, read-only rootfs, no mounts"
            ),
            tags=["container", str(adapter.get("verb") or ""), str(adapter.get("image") or "")],
            kind=CONTAINER_KIND,
            module=module_name,
            entrypoint="main",
            test_command=[sys.executable, "-m", "pytest", "-q", "tests/"],
            sample_input=dict(evidence.get("input") or {}),
            rationale=(
                f"mined from {adapter.get('image')}@{adapter.get('image_digest')}; "
                f"tested against the real container ({evidence.get('observed')})"
            ),
            provenance=f"container-toolbuilder:{requested_by}",
            status=STATUS_APPROVED,
            version=version,
            scan_summary=(
                "container adapter: pinned digest, network none, read-only rootfs, "
                "no-new-privileges, no mounts, no credentials"
            ),
        )
        publish = self.store.publish(
            spec,
            payload={
                "kind": CONTAINER_KIND,
                "runtime": MANIFEST_V2_RUNTIME,
                "image": adapter.get("image"),
                "image_digest": adapter.get("image_digest"),
                "verb": adapter.get("verb"),
                "version": version,
                "adapter_digest": digest_now,
                "tested_at": evidence.get("tested_at"),
            },
        )
        rollback_id = self._snapshot_for_rollback(name, previous)
        _write_text(paths["manifest"], yaml.safe_dump(manifest, sort_keys=False))
        evidence = dict(evidence)
        evidence.update({"registered": True, "registered_at": time.time(),
                         "version": version, "rollback_id": rollback_id})
        _write_text(paths["evidence"], json.dumps(evidence, indent=2, sort_keys=True))
        state = health.record_verification(
            self.runtime, name, ok=True,
            detail=(
                f"container adapter tested against {adapter.get('image')}@"
                f"{adapter.get('image_digest')}: {evidence.get('observed')}"
            )[:200],
        )
        health_report = health.file_state(self.runtime, name)
        self._record("", "container_register", name=name, version=version,
                     status=spec.status, runtime=MANIFEST_V2_RUNTIME,
                     rollback_id=rollback_id)
        return {
            "ok": True,
            "registered": True,
            "name": name,
            "version": version,
            "status": spec.status,
            "kind": CONTAINER_KIND,
            "image": adapter.get("image"),
            "image_digest": adapter.get("image_digest"),
            "permissions": perms,
            "health": health_report,
            "health_state": str(state),
            "rollback_id": rollback_id,
            "publish": publish,
            "wrapper": str(paths["wrapper"]),
            "spec": spec.to_dict(),
            "test": {
                "input": evidence.get("input"),
                "expected": evidence.get("expected"),
                "observed": evidence.get("observed"),
                "oracle": evidence.get("oracle"),
                "tested_at": evidence.get("tested_at"),
            },
        }

    @staticmethod
    def _next_version(previous: object) -> str:
        """A registered tool re-registered gets a NEW patch version."""
        if previous is None:
            return "0.1.0"
        parts = str(getattr(previous, "version", "0.1.0")).split(".")
        try:
            numbers = [int(part) for part in parts[:3]]
        except ValueError:
            return "0.1.0"
        while len(numbers) < 3:
            numbers.append(0)
        numbers[2] += 1
        return ".".join(str(item) for item in numbers)

    def _snapshot_for_rollback(self, name: str, previous: object) -> str:
        """Record what rollback must restore (spec + adapter bytes, or 'nothing')."""
        snapshot_dir = container_tool_paths(self.runtime, name)["rollbacks"]
        snapshot_id = f"{int(time.time() * 1000)}"
        payload = {
            "name": name,
            "at": time.time(),
            "previous_spec": previous.to_dict() if previous is not None else None,
            "had_adapter": Path(container_tool_paths(self.runtime, name)["adapter"]).is_file(),
        }
        try:
            _write_text(snapshot_dir / f"{snapshot_id}.json",
                        json.dumps(payload, indent=2, sort_keys=True))
        except OSError:
            return ""
        return snapshot_id

    # -- call / health / rollback --------------------------------------------- #
    def call_container_tool(
        self, name: str, payload: dict, *, warm: bool = False, timeout: float = 60.0
    ) -> dict:
        """Call a container capability once (one-shot, or through the warm container)."""
        loaded = self._load_container_tool(name)
        if not loaded["ok"]:
            return loaded
        adapter = loaded["adapter"]
        manager = self.manager()
        rendered = render_container_argv(adapter, dict(payload))
        started = time.time()
        if warm:
            started_service = ensure_container_started(self.runtime, name, manager=manager)
            if not started_service.get("started"):
                return {"ok": False, "failure_class": "service_unavailable",
                        "reason": started_service.get("reason", ""),
                        "service": started_service}
            run = manager.exec_in_container(
                Path(loaded["paths"]["cid_file"]), argv=rendered, timeout=timeout
            )
            mode = "warm-exec"
        else:
            config = _run_config(
                image=adapter.get("image", ""), image_digest=adapter["image_digest"],
                argv=rendered, labels={PURPOSE_LABEL: PURPOSE_TEST,
                                       CAPABILITY_LABEL: name},
                resource_limits=dict(
                    (adapter.get("permissions") or {}).get("resource_limits") or {}
                ),
                entrypoint=adapter.get("entrypoint"),
            )
            run = manager.run(config, timeout=timeout)
            mode = "one-shot"
        ok, parsed, detail = parse_container_stdout(
            adapter, getattr(run, "stdout", ""), keep_artefacts=True
        )
        written = container_adapter.write_artefacts(adapter, dict(payload), parsed)
        return {
            "ok": bool(getattr(run, "ok", False)) and ok,
            "name": name,
            "mode": mode,
            "input": dict(payload),
            "output": parsed,
            "artefacts": written,
            "argv": container_adapter.redacted_argv(adapter, dict(payload), rendered),
            "exit_code": getattr(run, "exit_code", None),
            "stdout": str(getattr(run, "stdout", "") or "")[:400],
            "stderr": str(getattr(run, "stderr", "") or "")[:400],
            "detail": detail or str(getattr(run, "detail", "") or "")[:200],
            "duration_ms": int((time.time() - started) * 1000),
        }

    def container_health(self, name: str, *, probe: bool = False) -> dict:
        """Health of a container capability: ledger state + live container facts."""
        from . import capability_health as health

        loaded = self._load_container_tool(name)
        if not loaded["ok"]:
            return loaded
        adapter = loaded["adapter"]
        state = health.file_state(self.runtime, name)
        selectable, why = health.selectable(self.runtime, name)
        manager = self.manager()
        available, detail = manager.available()
        local = manager.resolve_digest(adapter.get("image", "")) if available else ""
        pinned = str(adapter.get("image_digest") or "")
        report = {
            "ok": True,
            "name": name,
            "health": state,
            "auto_routable": bool(state.get("auto_routable")),
            "selectable": bool(selectable),
            "selectable_reason": why,
            "registered": bool(loaded["evidence"].get("registered")),
            "version": str(loaded["evidence"].get("version") or ""),
            "container": {
                "available": bool(available),
                "detail": detail,
                "image": adapter.get("image"),
                "image_digest": pinned,
                "local_digest": local,
                "digest_match": bool(pinned) and local == pinned,
            },
            "permissions": dict(adapter.get("permissions") or {}),
            "service": self.container_service_state(name),
        }
        if probe:
            report["probe"] = self.call_container_tool(
                name, dict(loaded["evidence"].get("input") or {"text": "skaldir"})
            )
        return report

    def container_service_state(self, name: str) -> dict:
        """Is the warm container of this capability running (by cid file + inspect)?"""
        cid_file = container_tool_paths(self.runtime, name)["cid_file"]
        if not cid_file.is_file():
            return {"started": False, "reason": "no warm container started yet"}
        cid = cid_file.read_text(encoding="utf-8").strip()
        if not cid:
            return {"started": False, "reason": "empty cid file"}
        inspect = self.manager()._invoke(
            ["docker", "inspect", "--format", "{{.State.Running}}", cid], timeout=20.0
        )
        running = inspect.exit_code == 0 and inspect.stdout.strip().lower() == "true"
        return {"started": running, "cid": cid, "cid_file": str(cid_file),
                "detail": inspect.stdout.strip()[:40] or inspect.stderr[:120]}

    def rollback_container_tool(self, name: str, *, reason: str = "") -> dict:
        """Take the registered capability back: registry, files, health, containers.

        The containers are removed by OUR labels (``godsv.capability=<name>``) —
        never by name, never blanket: the daemon is shared with foreign stacks.
        """
        from . import capability_health as health

        loaded = self._load_container_tool(name)
        if not loaded["ok"]:
            return loaded
        paths = container_tool_paths(self.runtime, name)
        snapshots = sorted(paths["rollbacks"].glob("*.json")) if paths["rollbacks"].is_dir() else []
        snapshot: dict = {}
        if snapshots:
            try:
                snapshot = json.loads(snapshots[-1].read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                snapshot = {}
        previous = snapshot.get("previous_spec")
        if previous is not None:
            restored = ToolSpec(**{
                key: value for key, value in previous.items()
                if key in ToolSpec.__dataclass_fields__
            })
            self.store.upsert(restored)
            restored_spec = restored
        else:
            current = self.store.get(name)
            if current is not None:
                current.status = STATUS_REVOKED
                self.store.upsert(current)
            restored_spec = None

        quarantine = ""
        if paths["rollbacks"].is_dir():
            quarantine_dir = paths["rollbacks"] / "rollback"
            try:
                if quarantine_dir.exists():
                    import shutil

                    shutil.rmtree(quarantine_dir)
                import shutil

                shutil.move(str(paths["tool_dir"]), str(quarantine_dir))
                quarantine = str(quarantine_dir)
            except (OSError, ValueError):
                quarantine = ""
        state = health.retire(
            self.runtime, name,
            reason=reason or "rolled back: containers and registration removed",
        )
        # One label is enough here (`godsv.capability` scopes to this capability's
        # containers). A wider owner+capability scope is correct as well since
        # `list_by_labels` emits ONE `--filter` per label — the comma-joined
        # `--filter label=a=1,b=2` form it used to emit matches NOTHING on
        # Server 29.6.1 (see docker_runtime.label_filter_args).
        # The scope is built ONCE, as data: the evidence renderer gets the label
        # pairs themselves (`teardown_labels`) instead of re-parsing the scope's
        # comma-joined TEXT form, so a label VALUE containing a comma survives.
        teardown_labels = {CAPABILITY_LABEL: str(name)}
        teardown = self.manager().cleanup_by_labels(teardown_labels)
        cid_file = paths["cid_file"]
        if cid_file.is_file():
            try:
                cid_file.unlink()
            except OSError:
                pass
        self._record("", "container_rollback", name=name, quarantine=quarantine,
                     containers=list(teardown.payload.get("removed") or []))
        return {
            "ok": True,
            "rolled_back": True,
            "name": name,
            "reason": reason,
            "restored_spec": restored_spec.to_dict() if restored_spec else None,
            "removed_registration": restored_spec is None,
            "quarantine": quarantine,
            "containers_removed": list(teardown.payload.get("removed") or []),
            "teardown_scope": teardown.payload.get("scope", ""),
            # the scope as DATA (a list of key=value pairs, in sweep order): the
            # text form above cannot round-trip a label value that contains a
            # comma, so the renderer reads these pairs instead of re-parsing it.
            "teardown_labels": [
                f"{key}={value}" for key, value in teardown_labels.items()
            ],
            "teardown_ok": bool(teardown.ok),
            "teardown_detail": teardown.detail,
            "health": state,
        }


# --------------------------------------------------------------------------- #
# the shared execution seam (execution.ContainerExecutor) — for real, not only
# in a test: the container call path installs itself on the boundary.
# --------------------------------------------------------------------------- #


@dataclass
class ContainerJob:
    """One containerised call as the shared execution boundary sees it."""

    image_digest: str = ""
    image: str = ""
    argv: list[str] = field(default_factory=list)
    mounts: list[dict] = field(default_factory=list)
    resource_limits: dict = field(default_factory=dict)
    labels: dict = field(default_factory=dict)
    name: str = ""
    readonly_rootfs: bool = True
    network: str = "none"
    workdir: Path | None = None
    timeout: float = 60.0
    #: ``""`` clears the image's ENTRYPOINT for this job (a mined media adapter
    #: needs it); ``None`` leaves the image's own entrypoint in charge.
    entrypoint: str | None = None


class DockerJobExecutor:
    """``execution.ContainerExecutor`` over DockerRuntimeManager.

    The former ``NullExecutor`` seam: ``available()`` answers with the real
    daemon and ``run_job()`` hands the job to the ONE job runner — the
    manager's own :meth:`DockerRuntimeManager.run_job`. This class holds no
    second mapping: it refuses anything that is not a ``ContainerJob`` (the
    builder's own job type) and delegates the rest, so the manager's more
    precise semantics apply — a docker-level failure (docker's own 125/126/127,
    a killed CLI / timeout) is ``not-certifiable`` because nothing ran, and only
    a workload that really ran can be ``failed``.
    """

    def __init__(self, manager: DockerRuntimeManager | None = None) -> None:
        self.manager = _manager_of(manager)

    def available(self) -> tuple[bool, str]:
        return self.manager.available()

    def run_job(self, job: object, policy: object = None) -> RunResult:  # noqa: F821
        from .execution import STATUS_NOT_CERTIFIABLE, RunResult

        if not isinstance(job, ContainerJob):
            return RunResult(
                ok=False, status=STATUS_NOT_CERTIFIABLE, sandboxed=False,
                detail=(
                    "the docker executor only runs ContainerJob objects — "
                    f"got {type(job).__name__}"
                ),
            )
        return self.manager.run_job(job, policy)


_REGISTERED_EXECUTOR: object | None = None


def ensure_container_executor(manager: DockerRuntimeManager | None = None) -> tuple[bool, str]:
    """Install the docker executor on the shared boundary — the real call site.

    Registered once per process, and only where a container is actually used
    (a guaranteed start or a container call), so no local-only path is ever
    silently routed into docker and so the ``NullExecutor`` stays the honest
    answer until docker is really needed.
    """
    global _REGISTERED_EXECUTOR

    from .execution import register_container_executor

    if _REGISTERED_EXECUTOR is not None and manager is None:
        return True, "docker executor already registered"
    executor = DockerJobExecutor(manager)
    register_container_executor(executor)
    _REGISTERED_EXECUTOR = executor
    return True, "docker executor registered on the shared execution boundary"


def capability_containers(manager: DockerRuntimeManager, name: str) -> list[str]:
    """Ids of the containers created FOR one capability — by LABEL, never by name.

    ``godsv.capability`` alone already scopes to this capability and to
    containers this project created; ``list_by_labels`` emits one ``--filter``
    per label, so adding ``godsv.owner`` would narrow it further without losing
    the match (a comma-joined ``--filter label=a=1,b=2`` matched nothing on
    Server 29.6.1 — see ``docker_runtime.label_filter_args``).
    """
    listing = manager.list_by_labels({CAPABILITY_LABEL: str(name)})
    if not listing.ok:
        return []
    return [str(cid) for cid in listing.payload.get("containers") or []]


def ensure_container_started(
    runtime: str | Path, name: str, *, manager: DockerRuntimeManager | None = None,
    purpose: str = PURPOSE_SERVICE,
    reuse: bool = True,
) -> dict:
    """Start the warm container of a container capability EXACTLY once.

    This is what ``discovery.ensure_ready`` calls: a cid file records the one
    container; a second call reports ``reused`` instead of starting a second
    one. Without a staged container adapter there is nothing to start — a
    structured refusal, never a fake success.
    """
    paths = container_tool_paths(runtime, name)
    if not paths["adapter"].is_file():
        return {
            "capability": str(name), "started": False, "startable": True,
            "reused": False,
            "reason": (
                "no staged container adapter for this capability — nothing to start "
                "(B6: a container capability is started from its adapter's service "
                "spec, not from a name)"
            ),
            "hook": "ensure_ready",
        }
    try:
        adapter = json.loads(paths["adapter"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "capability": str(name), "started": False, "startable": False,
            "reused": False,
            "reason": f"adapter.json is not readable: {exc}",
            "hook": "ensure_ready",
        }
    service = dict(adapter.get("service") or {})
    if not service.get("argv"):
        return {
            "capability": str(name), "started": False, "startable": False,
            "reused": False,
            "reason": "the adapter declares no service spec — this capability is one-shot only",
            "hook": "ensure_ready",
        }
    runtime_manager = _manager_of(manager)
    available, detail = runtime_manager.available()
    if not available:
        return {
            "capability": str(name), "started": False, "startable": True,
            "reused": False,
            "reason": f"docker is not reachable: {detail}",
            "hook": "ensure_ready",
        }
    if reuse:
        existing = capability_containers(runtime_manager, name)
        running = ""
        stale: list[str] = []
        for cid in existing:
            inspect = runtime_manager._invoke(
                ["docker", "inspect", "--format", "{{.State.Running}}", cid], timeout=20.0
            )
            if inspect.exit_code == 0 and inspect.stdout.strip().lower() == "true":
                running = running or cid
            else:
                stale.append(cid)
        if running:
            try:
                paths["cid_file"].parent.mkdir(parents=True, exist_ok=True)
                paths["cid_file"].write_text(running + "\n", encoding="utf-8")
            except OSError:
                pass
            # a container of ours is live: the container path IS the execution
            # boundary now, so the seam is installed here as well
            ensure_container_executor(runtime_manager)
            return {
                "capability": str(name), "started": True, "startable": True,
                "reused": True, "cid": running, "cid_file": str(paths["cid_file"]),
                "reason": (
                    "a warm container for this capability is already running "
                    "(adopted by label — exactly one is started)"
                ),
                "hook": "ensure_ready",
            }
        for cid in stale:
            # ids from the LABEL-filtered listing: a stale leftover of ours is
            # cleaned before a new one is started, so the name cannot collide
            runtime_manager.stop_and_remove(cid)
    # a serving service (the dashboard of a mined research tool) declares its
    # published ports in the adapter's service spec. RunConfig.validate is the
    # one gate for port publication: an EXPLICITLY declared network next to
    # ports is refused — the sanctioned route declares the ports and lets the
    # config layer promote the network (none → bridge, because docker drops
    # `-p` under `--network none`, measured live on Server 29.6.1). A foreign
    # network (host, container:…) with ports is refused here, before the run.
    ports = {str(c): int(h) for c, h in dict(service.get("ports") or {}).items()}
    declared_network = str(service.get("network") or "").strip()
    if ports:
        if declared_network not in ("", "none", "bridge"):
            return {
                "capability": str(name), "started": False, "startable": True,
                "reused": False,
                "reason": (
                    f"the service declares network {declared_network!r} together "
                    "with published ports — only the config layer's promotion "
                    "(none → bridge) may publish a port"
                ),
                "failure_class": "policy_violation", "hook": "ensure_ready",
            }
        network = "none"
    else:
        network = declared_network or "none"
    config = _run_config(
        image=adapter.get("image", ""), image_digest=str(adapter.get("image_digest") or ""),
        argv=list(service["argv"]),
        name=f"godsv_warm_{name}"[:60],
        labels={PURPOSE_LABEL: purpose, CAPABILITY_LABEL: str(name)},
        readonly_rootfs=bool((adapter.get("permissions") or {}).get("read_only_rootfs", True)),
        resource_limits=dict((adapter.get("permissions") or {}).get("resource_limits") or {}),
        network=network,
        port_bindings=ports,
        # a warm container of an adapter that had to clear the image ENTRYPOINT
        # must clear it too, or the service argv would be handed to the tool
        entrypoint=adapter.get("entrypoint"),
    )
    try:
        started = runtime_manager.start(
            config, cid_file=paths["cid_file"], timeout=60.0
        )
    except Exception as exc:  # noqa: BLE001 — a start that raises is still reported
        return {
            "capability": str(name), "started": False, "startable": True,
            "reused": False, "reason": f"{type(exc).__name__}: {exc}"[:200],
            "hook": "ensure_ready",
        }
    if not started.ok:
        return {
            "capability": str(name), "started": False, "startable": True,
            "reused": False,
            "reason": f"container start failed ({started.failure_class}): {started.detail}"[:300],
            "failure_class": started.failure_class, "hook": "ensure_ready",
        }
    # the container path is real now: install the executor on the shared boundary
    ensure_container_executor(runtime_manager)
    return {
        "capability": str(name), "started": True, "startable": True, "reused": False,
        "cid": started.cid, "cid_file": str(paths["cid_file"]),
        "published_ports": {str(c): int(h) for c, h in ports.items()},
        "reason": f"started one warm container for '{name}' (by label, teardown by label)",
        "hook": "ensure_ready",
    }
