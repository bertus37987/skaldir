"""In-process entrypoints for the registry/MCP/agent-loop execution path.

``skaldir.mcpserver._wrapper_for`` calls ``module.entrypoint(**arguments)`` for a
spec that resolves from ``sys.path``; ``skaldir.agent_loop`` builds its tool
definitions from those same specs. Both therefore need *functions with the same
argument names as the input schema* — that is all this module provides, one thin
function per tool, each of which delegates to the real implementation through
:func:`skaldir.tools_core.registry.call` (the single execution seam).

The functions return the tool's full result document
(``{"tool", "ok", "error_kind", "error", "exit_code", "warnings", "output"}``),
not a bare payload: a caller that only reads the payload would not learn that the
call was refused, and an agent that receives it can correct its next call.

The workspace root comes from ``SKALDIR_TOOL_WORKSPACE`` (legacy
``GODSV_TOOL_WORKSPACE``) when set, else the current directory — never from an
argument, so a model cannot widen its own scope. The runtime directory is the
product's default runtime unless an env override says otherwise.

Run context (audit F03): a **host-side** :class:`RunContext` binds workspace and runtime
for one run and outranks both the environment and the process cwd. It is a Python context
manager, not a tool argument, so the scope-widening rule above still holds — the model
cannot set it. Without a binding the behaviour is unchanged.

Precedence: ``RunContext`` > ``SKALDIR_TOOL_WORKSPACE`` > process cwd.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from . import registry


# --------------------------------------------------------------------------- #
# run context (audit F03)
# --------------------------------------------------------------------------- #
#
# The reproduced bug: `_call` resolved the runtime globally and let `resolve_root(None)`
# fall back to the PROCESS cwd, so `pwd` in a configured workspace printed the repository.
# Two concurrent runs therefore shared one cwd.
#
# The fix binds workspace and runtime *per run*, thread-locally. No `os.chdir`, no
# environment mutation, no per-call global switching — which is exactly what the
# assignment requires ("Kontext explizit und pro Run binden").

_RUN_CONTEXT = threading.local()


class RunContext:
    """Binds workspace/runtime for the current thread's core tool calls."""

    __slots__ = ("root", "runtime", "run_id", "_previous")

    def __init__(self, root: str | Path | None = None,
                 runtime: str | Path | None = None, run_id: str = "") -> None:
        self.root = str(root) if root is not None else None
        self.runtime = str(runtime) if runtime is not None else None
        self.run_id = str(run_id or "")
        self._previous: tuple[Any, Any, Any] | None = None

    def __enter__(self) -> "RunContext":
        self._previous = (
            getattr(_RUN_CONTEXT, "root", None),
            getattr(_RUN_CONTEXT, "runtime", None),
            getattr(_RUN_CONTEXT, "run_id", None),
        )
        _RUN_CONTEXT.root = self.root
        _RUN_CONTEXT.runtime = self.runtime
        _RUN_CONTEXT.run_id = self.run_id
        return self

    def __exit__(self, *exc: Any) -> bool:
        root, runtime, run_id = self._previous or (None, None, None)
        _RUN_CONTEXT.root = root
        _RUN_CONTEXT.runtime = runtime
        _RUN_CONTEXT.run_id = run_id
        return False


def bound_root() -> str | None:
    return getattr(_RUN_CONTEXT, "root", None)


def bound_runtime() -> str | None:
    return getattr(_RUN_CONTEXT, "runtime", None)


def _call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run one core tool against the *bound* workspace/runtime.

    When a run context is bound it is used. When nothing is bound the behaviour is the old
    one, so callers that never bind keep working — but a run that binds is never silently
    redirected to the process cwd.
    """
    from ..registry import default_runtime_dir

    root = bound_root()
    runtime = bound_runtime() or default_runtime_dir()
    kwargs: dict[str, Any] = {"runtime": runtime}
    if root is not None:
        kwargs["root"] = root
    try:
        return registry.call(tool, arguments, **kwargs).to_dict()
    except TypeError:
        # a registry without a `root` parameter: pass what it accepts
        if "root" in kwargs:
            return registry.call(tool, arguments, runtime=runtime).to_dict()
        raise


def terminal(
    command: str,
    cwd: str | None = None,
    timeout_s: int | None = None,
    max_output_bytes: int | None = None,
    stdin: str = "",
    env: dict[str, str] | None = None,
    isolated: bool = False,
    unshare_net: bool = True,
) -> dict[str, Any]:
    """Run a shell command in the sandboxed workspace root (timeout, output cap)."""
    arguments: dict[str, Any] = {"command": command}
    for key, value in (
        ("cwd", cwd),
        ("timeout_s", timeout_s),
        ("max_output_bytes", max_output_bytes),
        ("stdin", stdin),
        ("env", env),
        ("isolated", isolated),
        ("unshare_net", unshare_net),
    ):
        if value is not None and not (key == "stdin" and value == ""):
            arguments[key] = value
    return _call("terminal", arguments)


def read_file(
    path: str,
    offset: int = 1,
    limit: int = 200,
    max_bytes: int = 200000,
) -> dict[str, Any]:
    """Read a text file inside the workspace (paginated, hashed)."""
    return _call(
        "read_file", {"path": path, "offset": offset, "limit": limit, "max_bytes": max_bytes}
    )


def write_file(
    path: str,
    content: str,
    mode: str = "overwrite",
    make_parents: bool = False,
) -> dict[str, Any]:
    """Write a text file inside the workspace (scoped, deny-listed, verified)."""
    return _call(
        "write_file", {"path": path, "content": content, "mode": mode, "make_parents": make_parents}
    )


def patch(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    fuzzy: bool = True,
    fuzzy_min_ratio: float = 0.8,
    dry_run: bool = False,
    context_lines: int = 3,
) -> dict[str, Any]:
    """Replace a target string in a file with fuzzy tolerance; returns a diff."""
    return _call(
        "patch",
        {
            "path": path,
            "old_string": old_string,
            "new_string": new_string,
            "replace_all": replace_all,
            "fuzzy": fuzzy,
            "fuzzy_min_ratio": fuzzy_min_ratio,
            "dry_run": dry_run,
            "context_lines": context_lines,
        },
    )


def search_files(
    pattern: str,
    target: str = "content",
    path: str = ".",
    file_glob: str = "",
    output_mode: str = "content",
    context: int = 0,
    limit: int = 50,
    offset: int = 0,
    case_sensitive: bool = True,
    max_files: int = 20000,
    max_file_bytes: int = 2000000,
    order: str = "discovery",
) -> dict[str, Any]:
    """Regex content search or file-name glob inside the workspace."""
    arguments: dict[str, Any] = {
        "pattern": pattern,
        "target": target,
        "path": path,
        "output_mode": output_mode,
        "context": context,
        "limit": limit,
        "offset": offset,
        "case_sensitive": case_sensitive,
        "max_files": max_files,
        "max_file_bytes": max_file_bytes,
        "order": order,
    }
    if file_glob:
        arguments["file_glob"] = file_glob
    return _call("search_files", arguments)


def list_dir(
    path: str = ".",
    depth: int = 1,
    glob: str = "",
    show_hidden: bool = False,
    limit: int = 200,
    order: str = "discovery",
) -> dict[str, Any]:
    """List a directory (bounded depth, entry limit, reported skips)."""
    arguments: dict[str, Any] = {
        "path": path,
        "depth": depth,
        "show_hidden": show_hidden,
        "limit": limit,
        "order": order,
    }
    if glob:
        arguments["glob"] = glob
    return _call("list_dir", arguments)


def web_fetch(
    url: str,
    extract: str = "text",
    timeout_s: int = 15,
    max_bytes: int = 100000,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Fetch one http(s) URL (capped, key-free, redirects reported)."""
    arguments: dict[str, Any] = {
        "url": url,
        "extract": extract,
        "timeout_s": timeout_s,
        "max_bytes": max_bytes,
    }
    if headers:
        arguments["headers"] = headers
    return _call("web_fetch", arguments)


def web_search(query: str, limit: int = 5, backend: str = "auto") -> dict[str, Any]:
    """Search the web through key-free backends only (refuses when none answers)."""
    return _call("web_search", {"query": query, "limit": limit, "backend": backend})


# --------------------------------------------------------------------------- #
# The dotted name: `docker.ensure`
# --------------------------------------------------------------------------- #
#
# `registry.bridge_entrypoint()` returns `contract.name`, and registry names are
# dotted by convention (`tools.list`, `docker.ensure`) because `agent_loop`
# normalises them for the wire (`docker.ensure` -> `docker_ensure`, keeping the
# mapping in `AgentTool.source_name`) and the CLI's own leaf is dotted too.
#
# A dotted name is not a Python identifier, so it cannot be spelled with `def`.
# It IS a valid module attribute name, and `getattr(module, spec.entrypoint)` is
# exactly how the runtime's one execution path resolves a tool — so the entrypoint
# is bound here, under its registry name, to the same thin wrapper shape as every
# function above. `tests/test_docker_ensure.py` pins both spellings; nothing else
# in the runtime needs to know.


def docker_ensure(start: bool = True, paths: list[str] | None = None,
                  timeout_s: float | None = None, ready_s: float | None = None) -> dict[str, Any]:
    """Ensure a container runtime is up (password-free) or report why it is not."""
    arguments: dict[str, Any] = {"start": bool(start)}
    if paths is not None:
        arguments["paths"] = list(paths)
    if timeout_s is not None:
        arguments["timeout_s"] = float(timeout_s)
    if ready_s is not None:
        arguments["ready_s"] = float(ready_s)
    return _call("docker.ensure", arguments)


globals()["docker.ensure"] = docker_ensure


__all__ = [
    "docker_ensure",
    "list_dir",
    "patch",
    "read_file",
    "search_files",
    "terminal",
    "web_fetch",
    "web_search",
    "write_file",
]
