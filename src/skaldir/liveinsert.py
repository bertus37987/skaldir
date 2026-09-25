"""Live insertion (Block 6/TOOLS-28): a verified capability becomes callable
in the SAME running process — no restart, no prompt-only hint.

The chain this module implements:

    VERIFIED (the build passed the gates and its validation)
      → registry update (``tools.db`` + ``generated_tools/<name>/manifest.yaml``,
        written by the existing builder — never a second registry)
      → the next safe model boundary (never mid-call: an in-flight side effect
        is not interrupted for an insertion)
      → the relevant HOT set (at most ``HOT_LIMIT`` new capabilities visible at
        once — a wide insertion flush is noise, not capability)
      → real tool discovery (the same server object a host is already talking
        to lists the new tool)
      → a real tool call (through the same MCP wrapper the host invokes)

Honest boundary: this proves the *runtime* insertion inside one process
(discovery + call through the real ``tools.db``/``generated_tools`` surface and
the real FastMCP server object). It does not by itself prove that a live Hermes
agent turn re-read its tool schema — that requires a real host roundtrip; the
plugin-side wiring and the remaining gap are stated in
``docs/CAPABILITY-LEARNING-E2E.md``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HOT_LIMIT = 3


def _approved_specs(runtime: str | Path, project_id: str = "") -> dict:
    """Approved python tools visible in this runtime context (the registry)."""
    from .mcpserver import approved_python_specs
    from .tools import ToolStore

    runtime = Path(runtime)
    store = ToolStore(runtime / "tools.db")
    try:
        return {
            spec.name: spec
            for spec in approved_python_specs(
                store, runtime=runtime, project_id=project_id, include_legacy=True
            )
        }
    finally:
        store.close()


def _ready(runtime: str | Path, name: str) -> bool:
    """Has the tool actually finished the gated build (its own verification)?"""
    import yaml

    manifest = Path(runtime) / "generated_tools" / str(name) / "manifest.yaml"
    if not manifest.is_file():
        return False
    try:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    return str(data.get("status") or "") in ("ready", "approved")


def _all_approved(runtime: str | Path, project_id: str = "") -> dict:
    """Approved python specs by name — health gate NOT applied.

    Deliberately split from :func:`_approved_specs`: the sync path must be able
    to NAME a health-excluded tool (reported as ``blocked``) instead of silently
    routing around it, and the hot order needs the build timestamps.
    """
    from .mcpserver import approved_python_specs
    from .tools import ToolStore

    runtime = Path(runtime)
    store = ToolStore(runtime / "tools.db")
    try:
        return {
            spec.name: spec
            for spec in approved_python_specs(
                store,
                runtime=runtime,
                project_id=project_id,
                include_legacy=True,
                apply_health=False,
            )
        }
    finally:
        store.close()


@dataclass
class RuntimeView:
    """One live consumer view (a running server / the agent's tool surface).

    Bound to the index it was created from: it sees insertions made later in
    the same process, which is exactly the "no restart" claim.
    """

    index: CapabilityIndex
    project_id: str = ""
    applied: list[str] = field(default_factory=list)

    def list_tools(self) -> list[str]:
        """Real discovery: the registry surface plus everything inserted live."""
        names = set(self.index.inserted())
        names.update(_approved_specs(self.index.runtime, self.project_id))
        return sorted(names)

    def has(self, name: str) -> bool:
        return str(name) in set(self.list_tools())


class CapabilityIndex:
    """The live capability surface of one running process."""

    def __init__(self, runtime: str | Path, *, project_id: str = "") -> None:
        self.runtime = Path(runtime)
        self.project_id = str(project_id or "")
        self._inserted: list[str] = []
        self._pending: list[str] = []
        self._hot: list[str] = []
        self._in_flight: list[str] = []
        self._views: list[RuntimeView] = []
        self._servers: list[Any] = []
        self._pushed: dict[int, set[str]] = {}

    # -- discovery ------------------------------------------------------------
    def inserted(self) -> list[str]:
        return list(self._inserted)

    def hot_set(self) -> list[str]:
        """The relevant HOT set: newest ``HOT_LIMIT`` capabilities, newest first."""
        return list(self._hot[:HOT_LIMIT])

    def hot(self, name: str) -> bool:
        return str(name) in set(self.hot_set())

    # -- safe boundaries ------------------------------------------------------
    def begin_call(self, name: str) -> None:
        """A call into ``name`` is in flight — do not re-shape the surface now."""
        if str(name) not in self._in_flight:
            self._in_flight.append(str(name))

    def end_call(self, name: str) -> None:
        self._in_flight = [item for item in self._in_flight if item != str(name)]

    def boundary_safe(self, name: str = "") -> bool:
        """A safe model boundary: no in-flight call touches the capability."""
        if not self._in_flight:
            return True
        if name and str(name) in self._in_flight:
            return False
        return True

    def apply_pending(self, view: RuntimeView | None = None) -> dict:
        """Push pending insertions at a safe boundary. Never mid-call."""
        if not self.boundary_safe():
            return {"applied": [], "deferred": list(self._pending)}
        applied: list[str] = []
        for name in list(self._pending):
            if name in self._in_flight:
                continue
            if view is not None and name not in view.applied:
                view.applied.append(name)
            applied.append(name)
            self._pending.remove(name)
        return {"applied": applied, "deferred": list(self._pending)}

    # -- insertion ------------------------------------------------------------
    def insert(self, name: str, *, capability: str = "") -> dict:
        """Register a freshly verified capability in THIS process.

        Only a tool that finished the gated build (``status: ready``) is
        eligible — insertion is not a bypass around the builder's verification.
        """
        name = str(name or "")
        if not name:
            return {"inserted": False, "reason": "no tool name given"}
        if not _ready(self.runtime, name):
            return {
                "inserted": False,
                "reason": f"{name} has no completed gated build (manifest status != ready)",
            }
        specs = _approved_specs(self.runtime, self.project_id)
        spec = specs.get(name)
        if spec is None:
            return {
                "inserted": False,
                "reason": f"{name} is not an approved tool in the registry",
            }
        if name not in self._inserted:
            self._inserted.append(name)
        if name not in self._pending:
            self._pending.append(name)
        self._hot = [item for item in self._hot if item != name]
        self._hot.insert(0, name)
        pushed = self._push_to_servers(name)
        return {
            "inserted": True,
            "tool": name,
            "capability": capability,
            "hot": name in set(self.hot_set()),
            "pending_boundary": name in self._pending,
            "discoverable": name in set(self.runtime_view().list_tools()),
            "servers_updated": pushed,
        }

    def _push_to_servers(self, name: str) -> list[str]:
        """Add the tool to the servers already running in this process.

        FastMCP has no sync "already registered?" probe (``_list_tools`` /
        ``get_tool`` are coroutines in 4.x), so the index tracks what it pushed
        per server object — and ``add_tool`` on an existing name is a warning,
        not an error, so a redundant push could never corrupt the surface.
        """
        from .mcpserver import _wrapper_for

        pushed: list[str] = []
        spec = _approved_specs(self.runtime, self.project_id).get(name)
        if spec is None:
            return pushed
        wrapper = _wrapper_for(spec, base_dir=self.runtime)
        for server in list(self._servers):
            seen = self._pushed.setdefault(id(server), set())
            if name in seen:
                continue
            try:
                server.add_tool(wrapper)
            except Exception:
                continue
            seen.add(name)
            pushed.append(name)
        return pushed

    def still_available(self, name: str) -> bool:
        """Is the capability still routable (health) and registered?"""
        from . import capability_health as health

        if not _ready(self.runtime, name):
            return False
        if str(name) not in set(_approved_specs(self.runtime, self.project_id)):
            return False
        try:
            return bool(health.file_state(self.runtime, name).get("auto_routable"))
        except Exception:
            # fail closed: without a health verdict the capability is not routed
            return False

    # -- host wiring (§1.3) ---------------------------------------------------
    def in_flight(self) -> list[str]:
        """Names with a call currently in flight (a boundary must not touch them)."""
        return list(self._in_flight)

    def sync_registry(self, *, limit: int = 0) -> dict:
        """Insert every verified-but-unknown capability of the registry.

        The production entry point for the host wiring: called at a real model
        boundary, it picks up tools that were built (and verified) by ANOTHER
        process — the registry is ``tools.db`` + ``generated_tools/``, so a
        separate builder process is visible immediately. Health-gated: a
        capability the ledger excluded never enters (and is named in
        ``blocked``, not silently routed around). Returns
        ``{"inserted": [...], "skipped": {name: reason}, "blocked": {name: why}}``.
        """
        from .mcpserver import health_verdict

        known = set(self._inserted)
        inserted: list[str] = []
        skipped: dict[str, str] = {}
        blocked: dict[str, str] = {}
        specs = _all_approved(self.runtime, self.project_id)
        # Newest first: the hot set means "most recently built", never
        # "alphabetically last" — otherwise a fresh capability loses its slot to
        # an older one that merely sorts later.
        for name in sorted(specs, key=lambda item: self._recency(item, specs[item])):
            if name in known:
                continue
            ok, why = health_verdict(self.runtime, name)
            if not ok:
                blocked[name] = why
                continue
            result = self.insert(name)
            if result.get("inserted"):
                inserted.append(name)
            else:
                skipped[name] = str(result.get("reason") or "")
            if limit and len(inserted) >= limit:
                break
        return {"inserted": inserted, "skipped": skipped, "blocked": blocked}

    def _recency(self, name: str, spec: Any) -> tuple[float, str]:
        """Build time of one capability (spec stamp, then manifest mtime)."""
        stamp = float(getattr(spec, "updated", 0) or getattr(spec, "created", 0) or 0)
        if not stamp:
            try:
                manifest = self.runtime / "generated_tools" / str(name) / "manifest.yaml"
                stamp = manifest.stat().st_mtime
            except OSError:
                stamp = 0.0
        return (stamp, str(name))

    def runtime_view(self, project_id: str = "") -> RuntimeView:
        view = RuntimeView(index=self, project_id=str(project_id or self.project_id))
        self._views.append(view)
        return view

    def mcp_server(self, *, project_id: str = ""):
        """Build the MCP server for this process and keep it live-updatable."""
        from .mcpserver import build_server
        from .tools import ToolStore

        effective = str(project_id or self.project_id)
        store = ToolStore(self.runtime / "tools.db")
        server = build_server(
            store, runtime=self.runtime, project_id=effective, include_legacy=True
        )
        store.close()
        self._servers.append(server)
        for name in self._inserted:
            self._push_to_servers(name)
        return server

    def snapshot(self) -> dict:
        return {
            "inserted": self.inserted(),
            "hot": self.hot_set(),
            "pending": list(self._pending),
            "in_flight": list(self._in_flight),
            "servers": len(self._servers),
            "updated": time.time(),
        }


def approved_spec(index: CapabilityIndex, name: str):
    """The approved spec behind a live capability (``None`` when it is gone).

    Public accessor for the host wiring: the native surface needs the spec that
    supplies the schema — and it must be the *same* approved spec the MCP
    wrapper resolves, never a second registry.
    """
    return _approved_specs(index.runtime, index.project_id).get(str(name))


def discover_tool_call(index: CapabilityIndex, name: str, arguments: dict) -> dict:
    """Call a capability through the real MCP wrapper (the host's call path).

    Returns ``{"ok": bool, "result": …}``; a missing or degraded capability is a
    structured refusal, never an exception and never a silent fake result.
    """
    from .mcpserver import _wrapper_for

    if not index.still_available(name):
        return {"ok": False, "reason": f"{name} is not available for routing", "result": None}
    spec = _approved_specs(index.runtime, index.project_id).get(str(name))
    if spec is None:
        return {"ok": False, "reason": f"unknown tool: {name}", "result": None}
    index.begin_call(name)
    try:
        payload = _wrapper_for(spec, base_dir=index.runtime)(dict(arguments or {}))
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "result": None}
    finally:
        index.end_call(name)
    if isinstance(payload, dict) and set(payload) == {"error"}:
        return {"ok": False, "reason": str(payload["error"]), "result": None}
    return {"ok": True, "reason": "", "result": payload}


# --------------------------------------------------------------------------- #
# Native host surface (§1.3): one verified capability as a real Hermes tool
# --------------------------------------------------------------------------- #


def _json_type(value: Any) -> str:
    """JSON type name for one sample value (schemas are derived, not guessed)."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def native_schema(spec: Any) -> dict:
    """Hermes tool schema for one approved spec (``{name, description, parameters}``).

    V1 derives the properties from ``spec.sample_input`` — the real input the
    build validated against. Richer per-tool JSON schemas arrive with Manifest
    v2; until then ``additionalProperties`` stays permissive because the wrapper
    forwards the object as ``**kwargs`` to the tool entrypoint.
    """
    properties = {
        str(key): {"type": _json_type(value)}
        for key, value in dict(getattr(spec, "sample_input", {}) or {}).items()
    }
    description = str(
        getattr(spec, "description", "") or getattr(spec, "title", "") or getattr(spec, "name", "")
    ).strip()
    return {
        "name": str(getattr(spec, "name", "")),
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "additionalProperties": True,
        },
    }


def native_handler(index: CapabilityIndex, name: str):
    """Handler for a natively registered capability — the SAME call path as MCP.

    ``discover_tool_call`` runs the real wrapper (generated code through
    ``gates._run``/bwrap, fail-closed) and returns a structured refusal instead
    of raising; an in-flight marker brackets the call so a live insertion at a
    boundary can never re-shape a running capability. The registry dispatch
    contract requires a STRING result (a bare dict is normalized into an error),
    so the payload is JSON-encoded.
    """

    def handler(args: dict | None = None, **_ignored: Any) -> str:
        call = discover_tool_call(index, str(name), dict(args or {}))
        payload: Any = call.get("result") if call.get("ok") else {
            "error": str(call.get("reason") or "call failed")
        }
        return json.dumps(payload, default=str, sort_keys=True)

    handler.__name__ = str(name)
    handler.__qualname__ = str(name)
    handler.__doc__ = f"God's Voice live capability '{name}' (verified build)."
    return handler


def surface_plan(index: CapabilityIndex, live: list[str], *, limit: int = HOT_LIMIT) -> dict:
    """The live native surface after one boundary: newest ``limit``, never in-flight.

    A wide flush is noise, not capability — at most ``HOT_LIMIT`` newly inserted
    capabilities are visible at once. A name with a call in flight stays visible
    even after it left the hot set (a running call keeps its capability) and
    nothing in flight is ever removed.
    """
    wanted = list(index.hot_set()[:limit])
    for name in index.in_flight():
        if name not in wanted:
            wanted.append(name)
    live_set = set(live)
    wanted_set = set(wanted)
    return {
        "add": [name for name in wanted if name not in live_set],
        "remove": [name for name in live if name not in wanted_set],
        "wanted": wanted,
    }
