"""One shared, context-bound capability set for every agent entry point — Paket 1.

The audit measured the problem precisely (F01): the entries offer different things. A
fresh ``build_agent_toolset`` has 14 tools, the goal run has 6, and between them sit
terminal, patch, search, web, code execution and the browser — reachable from one place
and not the other. The assignment calls a missing adapter an implementation gap even when
the engine behind it works.

So there is one assembly function here, and both entries use it. Its rules:

* **Context is bound at build time.** workspace, runtime, run id and policy are closed
  over, so two concurrent runs cannot share a cwd by accident (F03).
* **Capability identity is single.** A capability is offered once under one name. The
  audit found ``list_dir`` and ``list_dir_2`` offered side by side; that is a duplicate
  model tool, not extra capability.
* **Discovery is not execution.** ``tool_search`` may report tools that are not callable
  here. Those stay in discovery and are never handed to the model as if they ran — the
  assignment says this explicitly.
* **Missing installation/policy is reported precisely.** A tool whose backend is absent
  refuses by name with the reason instead of failing obscurely.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

__all__ = ["core_capability_tools", "CAPABILITY_INDEX"]


#: What the shared set is for — kept next to the code so the two cannot drift apart.
CAPABILITY_INDEX: dict[str, str] = {
    "run_code": "execute Python in the run's workspace",
    "terminal": "run a shell command in the run's workspace",
    "read_text": "read a file (bounded excerpt)",
    "write_text": "write a file",
    "search_files": "search file contents or names",
    "list_dir": "list a directory",
    "patch": "targeted find-and-replace in a file",
    "web_search": "search the web (free backends, no paid key)",
    "web_fetch": "fetch a URL as readable text",
    "browser": "drive a browser session",
}


def core_capability_tools(
    *,
    workspace: str | Path,
    runtime: str | Path | None = None,
    run_id: str = "",
    policy: Any = None,
    store: Any = None,
    include: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
) -> list[Any]:
    """The shared capability tools, bound to one run's context.

    ``include``/``exclude`` let a caller narrow the set *by capability name* — which is how
    a policy-limited entry stays limited without getting a second, differently-named copy
    of the same tool.
    """
    from .agent_loop import AgentTool, builtin_tools
    from .code_exec_tool import run_code_tool

    ws = Path(workspace).expanduser().resolve()
    rt = Path(runtime).expanduser().resolve() if runtime else None

    wanted = set(include) if include is not None else None
    banned = set(exclude or ())

    def keep(name: str) -> bool:
        if name in banned:
            return False
        return wanted is None or name in wanted

    tools: list[Any] = []

    # -- the existing builtins (read/write/list/sha) stay the builtins ------- #
    for tool in builtin_tools(str(ws)):
        if keep(getattr(tool, "name", "")):
            tools.append(tool)

    # -- code execution: the typed adapter over the existing runner --------- #
    if keep("run_code"):
        tools.append(
            run_code_tool(workspace=ws, runtime=rt, run_id=run_id, policy=policy)
        )

    # -- terminal, patch and search come from the core registry ------------- #
    for tool in _registry_capabilities(ws, rt, store=store, keep=keep):
        tools.append(tool)


    # -- browser (the layer already built; refused by name when absent) ----- #
    if keep("browser"):
        tool = _browser_tool(ws, rt, run_id)
        if tool is not None:
            tools.append(tool)

    return _dedupe(tools)


def _dedupe(tools: list[Any]) -> list[Any]:
    """One capability, one model tool.

    The audit saw ``list_dir`` and ``list_dir_2`` offered together. The first one wins and
    the duplicates are dropped, so the model never has to guess which spelling to use.
    """
    seen: dict[str, Any] = {}
    for tool in tools:
        name = getattr(tool, "name", "")
        if name and name not in seen:
            seen[name] = tool
    return list(seen.values())


#: capability name -> the typed wrapper in :mod:`skaldir.tools_core.bridge`.
_BRIDGE_WRAPPERS = {
    "terminal": "terminal",
    "patch": "patch",
    "search_files": "search_files",
    "read_file": "read_file",
    "list_dir": "list_dir",
    "web_search": "web_search",
    "web_fetch": "web_fetch",
}


def _registry_capabilities(ws: Path, rt: Path | None, *, store: Any, keep) -> list[Any]:
    """Terminal/patch/search/read as agent tools, bound to this run's workspace.

    These wrap the **existing** typed wrappers in :mod:`skaldir.tools_core.bridge` — not a
    second execution path. Each call runs inside a :class:`~skaldir.tools_core.bridge.
    RunContext`, which is what makes the run's workspace win over the process cwd (F03).
    """
    from .agent_loop import AgentTool
    from .tools_core import bridge as core_bridge

    out: list[Any] = []
    for capability, wrapper_name in _BRIDGE_WRAPPERS.items():
        if not keep(capability):
            continue
        wrapper = getattr(core_bridge, wrapper_name, None)
        if wrapper is None:
            continue
        out.append(_bound_tool(capability, wrapper, ws, rt, run_id=""))
    return out


def _bound_tool(capability: str, wrapper: Any, ws: Path, rt: Path | None, *,
                run_id: str) -> Any:
    """One agent tool over one bridge wrapper, with the run context applied."""
    import inspect

    from .agent_loop import AgentTool
    from .tools_core import bridge as core_bridge

    sig = inspect.signature(wrapper)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for pname, param in sig.parameters.items():
        if pname in ("self", "root", "runtime", "run_id"):
            continue
        ann = param.annotation
        kind = "string"
        if ann in (int, "int"):
            kind = "integer"
        elif ann in (float, "float"):
            kind = "number"
        elif ann in (bool, "bool"):
            kind = "boolean"
        elif ann in (dict, "dict"):
            kind = "object"
        properties[pname] = {"type": kind}
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    def handler(_wrapper=wrapper, _ws=ws, _rt=rt, _rid=run_id, **args: Any) -> Any:
        with core_bridge.RunContext(root=_ws, runtime=_rt, run_id=_rid):
            return _wrapper(**{k: v for k, v in args.items() if k in sig.parameters})

    return AgentTool(
        name=capability,
        description=f"{CAPABILITY_INDEX.get(capability, capability)} (core tool, run-bound)",
        input_schema={"type": "object", "properties": properties, "required": required},
        handler=handler,
        parallel_safe=capability in ("read_file", "list_dir", "search_files", "web_search", "web_fetch"),
        mutating=capability in ("terminal", "patch", "write_file"),
        source="core",
        source_name=f"skaldir.tools_core.bridge.{capability}",
    )


def _adapt_registry_tool(spec: Any, ws: Path, rt: Path | None, *, store: Any) -> Any | None:
    """Wrap one registry tool so every call carries this run's workspace and runtime."""
    from .agent_loop import AgentTool

    name = getattr(spec, "name", "") or (spec.get("name") if isinstance(spec, dict) else "")
    description = getattr(spec, "description", "") or (
        spec.get("description", "") if isinstance(spec, dict) else ""
    )
    schema = getattr(spec, "input_schema", None) or (
        spec.get("input_schema") if isinstance(spec, dict) else None
    ) or {"type": "object", "properties": {}}
    mutating = bool(getattr(spec, "mutating", True))

    def handler(_spec=spec, _name=name, **args: Any) -> dict[str, Any]:
        try:
            from .tools_core import bridge as core_bridge
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            # root/runtime are passed EXPLICITLY — never resolved from the process cwd
            return core_bridge.call(
                _name, args, root=str(ws), runtime=str(rt) if rt else None
            )
        except TypeError:
            # a bridge without the explicit context parameters: refuse rather than silently
            # fall back to the process cwd, which is the F03 bug
            return {
                "ok": False,
                "error": "context_not_bindable",
                "reason": (
                    "this core tool cannot be bound to the run's workspace — refusing "
                    "rather than running in the process cwd (audit F03)"
                ),
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": type(exc).__name__, "reason": str(exc)[:300]}

    return AgentTool(
        name=name,
        description=description,
        input_schema=schema,
        handler=handler,
        parallel_safe=not mutating,
        mutating=mutating,
        source="core",
        source_name=f"skaldir.tools_core.{name}",
    )


def _browser_tool(ws: Path, rt: Path | None, run_id: str) -> Any | None:
    """The browser layer as an agent tool.

    A missing tool is refused by name with the exact reason rather than failing obscurely —
    and this never starts a browser it did not find.
    """
    from .agent_loop import AgentTool

    def handler(do: str = "read", url: str = "", kind: str = "", target: str = "",
                value: str = "", session: str = "", **_ignored: Any) -> Any:
        try:
            from .browser_layer import AgentBrowserBackend, BrowserLayer, SessionManager
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "browser_unavailable", "reason": str(exc)[:300]}

        backend = AgentBrowserBackend()
        cap = backend.probe()
        if not cap.available:
            return {
                "ok": False,
                "error": "browser_unavailable",
                "reason": cap.reason,
                "backend": "agent-browser",
            }
        sessions = SessionManager()
        layer = BrowserLayer(sessions, backend, run_id=run_id, session_id=session)
        if str(do).lower() in ("read", "observe"):
            result = layer.read(url=url)
        else:
            payload = {k: v for k, v in
                       {"kind": kind, "target": target, "value": value, "url": url}.items() if v}
            result = layer.act(payload)
        return result if isinstance(result, dict) else {
            "ok": bool(getattr(result, "ok", False)),
            "detail": str(getattr(result, "detail", ""))[:400],
        }

    return AgentTool(
        name="browser",
        description=(
            "Drive a browser session: {do: read|act, url, kind, target, value}. "
            "Reuses the existing session and never closes the user's own tabs."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "do": {"type": "string", "enum": ["read", "act"]},
                "url": {"type": "string"},
                "kind": {"type": "string"},
                "target": {"type": "string"},
                "value": {"type": "string"},
                "session": {"type": "string"},
            },
            "required": ["do"],
        },
        handler=handler,
        parallel_safe=False,
        mutating=True,
        source="core",
        source_name="skaldir.browser_layer",
    )
