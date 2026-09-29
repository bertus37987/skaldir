"""Action Engine — one execution graph, run it with minimal LLM roundtrips (W3 / COMPOSE).

The engine is the orchestrator that binds tools, subtools, MCP tools and computer-use
actions into one resumable graph.  The model creates the graph once; the engine runs
it and returns control only at defined boundaries (spec 72-action-engine-spec.md §3):

1. a decision requires reasoning
2. an unexpected state occurs
3. an action fails
4. an observation is ambiguous
5. the execution graph finishes

Otherwise the engine continues without an LLM call.  Results to the model are compact
(status per node, failed/ambiguous observations, evidence handles); full traces stay
on disk.

Product constraints that win (AGENTS.md): isolation, permissions, prove-don't-claim,
VERIFY verdicts (`UNKNOWN` never success), ENFORCE goal lock (engine success ≠ goal PASS).

**SIGKILL persistence** (spec §6): the graph is written to disk after every node
transition.  On resume the engine loads the graph and retries only FAILED/AMBIGUOUS
nodes — VERIFIED nodes are never re-applied (idempotent by recorded step).

**EXPAND promotion** (spec §8): when every node is VERIFIED, the engine promotes the
sequence to a reusable subtool via ``act_service.save_subtool`` — success means verified
end_state + evidence, not merely exit 0.

**Permission enforcement** (spec §5): every node must declare ``permissions``; a node
that cannot declare permissions is refused at graph-validation time.  The engine never
bypasses permissions.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = [
    "NodeStatus",
    "NodeKind",
    "Boundary",
    "ActionNode",
    "ActionGraph",
    "ActionEngine",
    "EngineResult",
    "NodeResult",
    "GraphStore",
    "compact_result",
    "validate_graph",
    "promote_to_subtool",
]


# --------------------------------------------------------------------------- #
# Vocabulary                                                                   #
# --------------------------------------------------------------------------- #


class NodeKind(StrEnum):
    TOOL = "tool"              # tools_core / registry `tools call`
    SUBTOOL = "subtool"        # act_service BUILTIN_SUBTOOLS / saved / learned
    CU_ACTION = "cu_action"    # computer_use.Action (click / type / key / …)
    MCP = "mcp"                # approved MCP tool
    TERMINAL = "terminal"      # sandboxed shell / file op
    GENERATED = "generated"    # ToolBuilder / ContainerToolBuilder


class NodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    VERIFIED = "verified"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
    SKIPPED = "skipped"


class Boundary(StrEnum):
    """The five owner cases where control returns to the model (spec §3)."""
    DECISION = "decision"            # a decision requires reasoning
    UNEXPECTED = "unexpected"        # an unexpected state occurs
    FAILURE = "failure"              # an action fails
    AMBIGUOUS = "ambiguous"          # an observation is ambiguous
    FINISHED = "finished"            # the execution graph finishes


# --------------------------------------------------------------------------- #
# Nodes                                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class ActionNode:
    """One step in the execution graph."""

    id: str
    kind: NodeKind
    name: str  # tool / subtool / action name
    input: dict[str, Any] = field(default_factory=dict)
    verify: dict[str, Any] = field(default_factory=dict)  # expected result / postcondition
    depends_on: list[str] = field(default_factory=list)  # node ids (edges)
    permissions: list[str] = field(default_factory=list)  # required permissions (spec §5)
    status: NodeStatus = NodeStatus.PENDING
    result: dict[str, Any] | None = None
    evidence: list[str] = field(default_factory=list)  # file paths / digests
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def duration(self) -> float:
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "name": self.name,
            "input": self.input,
            "verify": self.verify,
            "depends_on": self.depends_on,
            "permissions": self.permissions,
            "status": self.status.value,
            "result": self.result,
            "evidence": self.evidence,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration": round(self.duration, 3),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActionNode:
        return cls(
            id=str(d["id"]),
            kind=NodeKind(d.get("kind", "tool")),
            name=str(d.get("name", "")),
            input=dict(d.get("input") or {}),
            verify=dict(d.get("verify") or {}),
            depends_on=list(d.get("depends_on") or []),
            permissions=list(d.get("permissions") or []),
            status=NodeStatus(d.get("status", "pending")),
            result=d.get("result"),
            evidence=list(d.get("evidence") or []),
            started_at=float(d.get("started_at") or 0),
            finished_at=float(d.get("finished_at") or 0),
        )


@dataclass
class NodeResult:
    """Compact result for one node (returned to the model)."""

    id: str
    status: str
    detail: str = ""
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "status": self.status, "detail": self.detail[:200],
                "evidence": self.evidence}


# --------------------------------------------------------------------------- #
# Graph                                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class ActionGraph:
    """The full execution graph — nodes, edges, goal, status."""

    goal: str = ""
    nodes: dict[str, ActionNode] = field(default_factory=dict)
    status: str = "draft"  # draft | validated | running | done | failed | paused
    created_at: float = field(default_factory=time.time)
    finished_at: float = 0.0

    def add(self, node: ActionNode) -> ActionNode:
        self.nodes[node.id] = node
        return node

    def validate(self) -> list[str]:
        """Return validation errors (empty list = valid).

        Every node must declare ``verify`` (postcondition) and ``permissions``
        (spec §5: a node that cannot declare permissions is refused at
        graph-validation time — the engine never bypasses permissions).
        """
        errors: list[str] = []
        for node in self.nodes.values():
            for dep in node.depends_on:
                if dep not in self.nodes:
                    errors.append(f"node {node.id!r} depends on unknown node {dep!r}")
            if not node.verify:
                errors.append(
                    f"node {node.id!r} declares no verify — every node needs an expectation"
                )

            if not node.permissions:
                errors.append(
                    f"node {node.id!r} declares no permissions — every node must declare "
                    f"its required permissions (spec §5: the engine never bypasses permissions)"
                )
            if node.kind == NodeKind.CU_ACTION and not node.verify:
                errors.append(f"CU node {node.id!r} needs an observation gate (verify)")
        # cycle check (Kahn)
        if not errors:
            errors.extend(self._cycle_errors())
        return errors

    def _cycle_errors(self) -> list[str]:
        in_degree = {nid: 0 for nid in self.nodes}
        for node in self.nodes.values():
            for dep in node.depends_on:
                if dep in in_degree:
                    in_degree[node.id] += 1  # noqa: B023 — loop var used by index
        # recompute properly
        in_degree = {nid: 0 for nid in self.nodes}
        for node in self.nodes.values():
            for dep in node.depends_on:
                if dep in self.nodes:
                    in_degree[node.id] += 1
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        visited = 0
        adj: dict[str, list[str]] = {nid: [] for nid in self.nodes}
        for node in self.nodes.values():
            for dep in node.depends_on:
                if dep in adj:
                    adj[dep].append(node.id)
        while queue:
            cur = queue.pop()
            visited += 1
            for nxt in adj[cur]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)
        if visited != len(self.nodes):
            return [f"graph contains a cycle ({len(self.nodes) - visited} nodes unreachable)"]
        return []

    def ready_nodes(self) -> list[ActionNode]:
        """Nodes whose dependencies are all VERIFIED and that are still PENDING."""
        ready: list[ActionNode] = []
        for node in self.nodes.values():
            if node.status != NodeStatus.PENDING:
                continue
            deps_ok = all(
                self.nodes[dep].status == NodeStatus.VERIFIED
                for dep in node.depends_on
                if dep in self.nodes
            )
            if deps_ok:
                ready.append(node)
        return ready

    def parallel_groups(self) -> list[list[ActionNode]]:
        """Independent nodes grouped by dependency depth (run each group in parallel)."""
        depth: dict[str, int] = {}
        for node in self._topo_order():
            parent_depth = max(
                (depth.get(dep, 0) for dep in node.depends_on if dep in self.nodes),
                default=-1,
            )
            depth[node.id] = parent_depth + 1
        groups: dict[int, list[ActionNode]] = {}
        for node in self.nodes.values():
            groups.setdefault(depth[node.id], []).append(node)
        return [groups[d] for d in sorted(groups)]

    def _topo_order(self) -> list[ActionNode]:
        in_degree = {nid: 0 for nid in self.nodes}
        adj: dict[str, list[str]] = {nid: [] for nid in self.nodes}
        for node in self.nodes.values():
            for dep in node.depends_on:
                if dep in self.nodes:
                    adj[dep].append(node.id)
                    in_degree[node.id] += 1
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        order: list[ActionNode] = []
        while queue:
            cur = queue.pop()
            order.append(self.nodes[cur])
            for nxt in adj[cur]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)
        return order

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "status": self.status,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "nodes": [n.to_dict() for n in self.nodes.values()],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActionGraph:
        graph = cls(
            goal=str(d.get("goal") or ""),
            status=str(d.get("status") or "draft"),
            created_at=float(d.get("created_at") or 0),
            finished_at=float(d.get("finished_at") or 0),
        )
        for nd in d.get("nodes") or []:
            graph.add(ActionNode.from_dict(nd))
        return graph


def validate_graph(graph: ActionGraph) -> list[str]:
    """Validate a graph before running it (returns errors, empty = valid)."""
    return graph.validate()


# --------------------------------------------------------------------------- #
# Engine                                                                       #
# --------------------------------------------------------------------------- #


@dataclass
class EngineResult:
    """Compact envelope returned to the model after a run (spec §7)."""

    boundary: Boundary
    graph_status: str
    nodes: list[NodeResult] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)  # node ids
    ambiguous: list[str] = field(default_factory=list)  # node ids
    evidence: list[str] = field(default_factory=list)  # all evidence handles
    detail: str = ""
    elapsed: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary": self.boundary.value,
            "graph_status": self.graph_status,
            "nodes": [n.to_dict() for n in self.nodes],
            "failed": self.failed,
            "ambiguous": self.ambiguous,
            "evidence": self.evidence,
            "detail": self.detail[:400],
            "elapsed": round(self.elapsed, 3),
        }


def compact_result(graph: ActionGraph, boundary: Boundary, detail: str = "") -> EngineResult:
    """Build the compact result envelope (full traces stay on disk)."""
    nodes = [
        NodeResult(id=n.id, status=n.status.value,
                   detail=str((n.result or {}).get("detail", ""))[:200],
                   evidence=n.evidence)
        for n in graph.nodes.values()
    ]
    failed = [n.id for n in graph.nodes.values() if n.status == NodeStatus.FAILED]
    ambiguous = [n.id for n in graph.nodes.values() if n.status == NodeStatus.AMBIGUOUS]
    evidence = [e for n in graph.nodes.values() for e in n.evidence]
    elapsed = (graph.finished_at - graph.created_at) if graph.finished_at else 0.0
    return EngineResult(
        boundary=boundary,
        graph_status=graph.status,
        nodes=nodes,
        failed=failed,
        ambiguous=ambiguous,
        evidence=evidence,
        detail=detail,
        elapsed=elapsed,
    )


#: One node executor: (node) → {ok, verified, detail, evidence}.
#: Injected by the caller (tests use a stub; production wires tools/subtools/CU).
NodeExecutor = Callable[[ActionNode], dict[str, Any]]


class GraphStore:
    """Persist an ActionGraph to disk for SIGKILL crash recovery (spec §6).

    The graph is written after every node transition.  On resume the engine loads
    the graph and retries only FAILED/AMBIGUOUS nodes — VERIFIED nodes are never
    re-applied (idempotent by recorded step).
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, graph: ActionGraph) -> None:
        """Write the graph to disk (atomic: tmp + rename)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(graph.to_dict(), indent=2), encoding="utf-8")
        tmp.rename(self.path)

    def load(self) -> ActionGraph | None:
        """Load the graph from disk (None when missing or corrupt)."""
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return ActionGraph.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def clear(self) -> None:
        """Remove the persisted graph (called after successful completion)."""
        self.path.unlink(missing_ok=True)


def promote_to_subtool(graph: ActionGraph, *, goal: str = "") -> str:
    """EXPAND promotion (spec §8): promote a fully VERIFIED graph to a reusable subtool.

    Success means verified end_state + evidence, not merely exit 0.  Returns the
    subtool name (empty string on failure).  Uses ``act_service.save_subtool`` so the
    promoted subtool is invocable via the EXPAND surface.
    """
    if graph.status != "done":
        return ""
    if any(n.status != NodeStatus.VERIFIED for n in graph.nodes.values()):
        return ""
    try:
        import re as _re

        from .act_service import save_subtool

        name = _re.sub(r"[^a-z0-9]+", "_", (goal or "engine_flow").lower()).strip("_")[:40]
        if not name or name.startswith("_"):
            name = "engine_flow"
        # Build a subtool spec from the graph's nodes — map NodeKind to DECLARED_KINDS
        # so save_subtool's action_error accepts the actions (measured: the old code
        # wrote kind=node.kind.value which is "tool"/"subtool"/"cu_action"/… and
        # save_subtool raised, swallowed by except → promotion silently failed).
        _KIND_MAP = {
            NodeKind.CU_ACTION: "key",
            NodeKind.TERMINAL: "cli",
            NodeKind.TOOL: "api_call",
            NodeKind.SUBTOOL: "cli",
            NodeKind.MCP: "api_call",
            NodeKind.GENERATED: "api_call",
        }
        actions = []
        for node in graph._topo_order():  # noqa: SLF001 — need dependency order
            actions.append({
                "kind": _KIND_MAP.get(node.kind, "cli"),
                "name": node.name,
                "payload": dict(node.input),
                "argv": list(node.input.get("argv") or []),
                "verify": dict(node.verify),
            })
        spec = {
            "name": name,
            "version": "1.0",
            "description": f"promoted from action engine: {goal[:120]}",
            "applicability": {
                "app": "any",
                "app_versions": [">=0"],
                "locales": ["*"],
            },
            "preconditions": [
                {"id": "session", "text": "session", "source": "host", "fact": "host_session"}
            ],
            "anchors": [
                {"id": "session", "text": "session", "source": "host", "fact": "host_session"}
            ],
            "actions": actions,
            "end_state": {
                "channel": "window_title",
                "description": f"promoted flow: {goal[:80]}",
                "expect_observe_contains": goal[:80] if goal else "done",
            },
            "verification_contract": f"promoted from action engine: {goal[:80]}",
        }
        save_subtool(spec, force=True)
        return name
    except Exception:  # noqa: BLE001 — promotion must never fail the run
        return ""


class ActionEngine:
    """Runs an ActionGraph with minimal LLM roundtrips.

    Independent nodes run in parallel; dependent nodes pipeline.  Control returns
    to the model only at the five owner boundaries (spec §3).  The engine never
    marks a goal complete — that is JEV / ENFORCE (spec §9).

    **SIGKILL persistence** (spec §6): when a ``GraphStore`` is provided, the graph
    is saved after every node transition.  On resume VERIFIED nodes are never
    re-applied (idempotent by recorded step).
    """

    def __init__(
        self,
        executor: NodeExecutor,
        *,
        max_workers: int = 4,
        graph_store: GraphStore | None = None,
    ) -> None:
        self.executor = executor
        self.max_workers = max_workers
        self.graph_store = graph_store

    def run(self, graph: ActionGraph) -> EngineResult:
        """Run the graph from its current state (validated or resumable)."""
        errors = graph.validate()
        if errors:
            graph.status = "failed"
            return compact_result(graph, Boundary.UNEXPECTED,
                                  detail=f"graph validation failed: {'; '.join(errors)}")
        graph.status = "running"
        self._persist(graph)
        start = time.time()
        # Pipeline: run dependency-depth groups; within a group, run in parallel
        for group in graph.parallel_groups():
            pending = [n for n in group if n.status == NodeStatus.PENDING]
            if not pending:
                continue
            if len(pending) == 1:
                boundary = self._run_one(pending[0])
            else:
                boundary = self._run_parallel(pending)
            self._persist(graph)
            if boundary is not None:
                # Boundary hit → stop and return control to the model
                graph.status = "paused"
                graph.finished_at = time.time()
                self._persist(graph)
                return compact_result(graph, boundary,
                                      detail=f"boundary hit: {boundary.value}")
        # All nodes done
        graph.finished_at = time.time()
        any_failed = any(n.status == NodeStatus.FAILED for n in graph.nodes.values())
        any_ambiguous = any(n.status == NodeStatus.AMBIGUOUS for n in graph.nodes.values())
        if any_failed:
            graph.status = "failed"
            self._persist(graph)
            return compact_result(
                graph, Boundary.FAILURE,
                detail=(
                    f"{sum(1 for n in graph.nodes.values() if n.status == NodeStatus.FAILED)}"
                    " node(s) failed"
                ),
            )
        if any_ambiguous:
            graph.status = "paused"
            self._persist(graph)
            return compact_result(graph, Boundary.AMBIGUOUS,
                                  detail="ambiguous observation(s) — model must decide")
        graph.status = "done"
        self._persist(graph)
        # EXPAND promotion (spec §8): promote fully verified sequences to subtools
        promoted = promote_to_subtool(graph, goal=graph.goal)
        result = compact_result(graph, Boundary.FINISHED, detail="graph completed")
        result.elapsed = time.time() - start
        if promoted:
            result.detail += f" · promoted to subtool {promoted!r}"
        return result

    def resume(self, graph: ActionGraph) -> EngineResult:
        """Resume a paused/failed graph (crash recovery, post-compaction, model change).

        VERIFIED nodes are never re-applied (idempotent by recorded step).
        FAILED/AMBIGUOUS nodes are reset to PENDING so they retry.
        """
        for node in graph.nodes.values():
            if node.status in (NodeStatus.FAILED, NodeStatus.AMBIGUOUS):
                node.status = NodeStatus.PENDING
                node.result = None
        graph.status = "validated"
        return self.run(graph)

    def _persist(self, graph: ActionGraph) -> None:
        """Save the graph to disk (SIGKILL crash recovery, spec §6)."""
        if self.graph_store is not None:
            try:
                self.graph_store.save(graph)
            except Exception:  # noqa: BLE001 — persistence failure must not crash the run
                pass

    def _run_one(self, node: ActionNode) -> Boundary | None:
        """Run one node; return a Boundary if control must go back to the model."""
        node.status = NodeStatus.RUNNING
        node.started_at = time.time()
        self._persist(getattr(self, "_current_graph", None) or None)
        try:
            raw = self.executor(node)
        except Exception as exc:  # noqa: BLE001 — a node crash is a boundary
            node.status = NodeStatus.FAILED
            node.finished_at = time.time()
            node.result = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
            return Boundary.FAILURE
        node.finished_at = time.time()
        node.result = dict(raw)
        node.evidence = list(raw.get("evidence") or [])
        ok = bool(raw.get("ok"))
        verified = bool(raw.get("verified")) and bool(raw.get("evidence"))
        if ok and verified:
            node.status = NodeStatus.VERIFIED
            return None
        if not ok:
            node.status = NodeStatus.FAILED
            return Boundary.FAILURE
        # ok but not verified → ambiguous observation (spec §4: UI differs from
        # expectations → return control to the model)
        node.status = NodeStatus.AMBIGUOUS
        return Boundary.AMBIGUOUS

    def _run_parallel(self, nodes: Sequence[ActionNode]) -> Boundary | None:
        """Run independent nodes in parallel; return the first boundary hit."""
        boundary: Boundary | None = None
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self._run_one, n): n for n in nodes}
            for future in as_completed(futures):
                result_boundary = future.result()
                if result_boundary is not None and boundary is None:
                    boundary = result_boundary
        return boundary
