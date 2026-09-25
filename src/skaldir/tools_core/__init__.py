"""The built-in (core) toolset: the agent's hands, with declared limits.

Nine tools, each a real, registered capability of the Skaldir runtime:

==================  ======================================================
``terminal``        run a command in a sandboxed working directory (timeout,
                    output cap, exit code, optional bubblewrap isolation)
``read_file``       paginated, hashed read inside the workspace root
``write_file``      scoped, deny-listed, re-read-verified write
``patch``           find/replace with a four-step fuzzy ladder + unified diff
``search_files``    regex content search *and* file-name glob, bounded walk
``list_dir``        bounded directory listing
``web_fetch``       one http(s) URL, key-free, capped
``web_search``      key-free search backends only; refuses (exit 3) and names
                    what is missing when none answers
``docker.ensure``   brings the container runtime up password-free (socket
                    activation, then rootless podman) and reports honestly with
                    exit 3 when none answers — the tool that makes the runtime
                    the container tools need exist
==================  ======================================================

Every tool carries six things (see :mod:`skaldir.tools_core.contract`): a typed
**input schema**, a typed **output schema**, a **permission declaration**
(filesystem scope, network, subprocess), a **timeout**, an **output truncation
rule**, and a declared **smoke case** that :func:`skaldir.tools_core.registry.smoke`
runs for real and records in the capability-health ledger.

Typical use::

    from skaldir.tools_core import registry

    registry.register("~/.hermes/skaldir")          # or: skaldir tools-core register
    result = registry.call("terminal", {"command": "ls -la"})
    result.ok, result.exit_code, result.data["stdout"]

The CLI group lives in :mod:`skaldir.tools_core.cli` (``skaldir tools-core …``);
``python -m skaldir.tools_core …`` runs the same verbs without the product CLI.
"""

from __future__ import annotations

from typing import Any

#: The core tool names, in the order the toolset presents them.
CORE_TOOL_NAMES: tuple[str, ...] = (
    "terminal",
    "read_file",
    "write_file",
    "patch",
    "search_files",
    "list_dir",
    "web_fetch",
    "web_search",
    "docker.ensure",
)

__version__ = "1.0.0"


def contracts() -> dict[str, Any]:
    """Every core tool's typed contract, keyed by name."""
    import sys

    registry = sys.modules.get("skaldir.tools_core.registry")
    if registry is not None:
        return registry.contracts()
    from .registry import contracts as _contracts

    return _contracts()


def descriptor(name: str) -> dict[str, Any]:
    """One tool's full typed contract (schemas, permissions, timeout, truncation)."""
    from .registry import descriptor as _descriptor

    return _descriptor(name)


def call(name: str, arguments: dict | None = None, **kwargs: Any) -> Any:
    """Run one core tool through the single execution seam."""
    from .registry import call as _call

    return _call(name, arguments, **kwargs)


def register(runtime: str, **kwargs: Any) -> dict[str, Any]:
    """Publish the core toolset into a runtime's registry (idempotent)."""
    from .registry import register as _register

    return _register(runtime, **kwargs)


def smoke(runtime: str, **kwargs: Any) -> dict[str, Any]:
    """Run each tool's declared smoke case and record the health fact."""
    from .registry import smoke as _smoke

    return _smoke(runtime, **kwargs)


def health(runtime: str, **kwargs: Any) -> dict[str, Any]:
    """The capability-health view of the core tools."""
    from .registry import health as _health

    return _health(runtime, **kwargs)


__all__ = [
    "CORE_TOOL_NAMES",
    "__version__",
    "call",
    "contracts",
    "descriptor",
    "health",
    "register",
    "smoke",
]
