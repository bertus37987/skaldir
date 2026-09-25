"""The typed contract behind every core tool: schema, permissions, limits.

A tool in this package is *data plus a function*, and the data part is the part
that keeps an agent honest:

* a **typed input schema** and a **typed output schema** (JSON Schema, the same
  vocabulary the product CLI's ``contract dump`` speaks) — so a caller can see
  what a tool accepts before calling it, and a model cannot smuggle extra keys
  past it (``additionalProperties: false``),
* a **permission declaration** split into the three axes that actually decide
  what a tool can do to the machine: ``filesystem`` (scope/mode/roots/deny
  globs), ``network`` (mode/schemes/hosts/key requirement/endpoints) and
  ``subprocess`` (allowed, shell, interpreter, kill policy, environment
  policy),
* a **timeout** (default + hard maximum),
* an **output truncation rule** — an agent tool that can dump 10 MB into a
  context is a liability, so every tool declares how much of its own output it
  is allowed to return and where the rest goes instead of into the context.

Three further conventions hold for every tool here:

``ToolResult``
    Structured. ``ok`` is about *the tool call*, not about the world: a command
    that exits non-zero or a file that does not exist is a **successful
    observation** with a non-zero ``exit_code`` / a ``not_found`` error kind —
    the CLI maps the *error kind* onto the exit code (see :data:`ERROR_EXIT`),
    never the observed process status.
``SPECIAL_TAGS``
    The registry row (``tools.db``) carries the permission/timeout/truncation
    facts as tags, so ``tools list`` / ``tools show`` show them without a second
    store (see :func:`ToolContract.registry_tags`).
``module`` / ``entrypoint``
    Each contract names the module that implements it, so a registry consumer
    can resolve it without a table of its own.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The registry ``kind`` of a built-in tool. Generated/LLM-built tools register
#: as ``python`` (``tools.KIND_PYTHON``) or ``composition``; the built-in
#: toolset is its own kind, so ``tools list`` distinguishes reviewed runtime
#: tools from generated ones at a glance.
KIND_CORE = "core"

#: Version of the whole core toolset (one number: the contracts change together).
CORE_VERSION = "1.0.0"

#: The exit-code vocabulary of the product CLI (``cli_contract``), repeated here
#: so the tool layer is importable and testable on its own.
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_VERIFICATION_FAILED = 2
EXIT_BLOCKED = 3
EXIT_NEEDS_CREDENTIALS = 4
EXIT_NOT_FOUND = 5
EXIT_CONFLICT = 6
EXIT_INTERNAL = 7
EXIT_UNKNOWN = 8

#: error kind → exit code. The empty kind is the success case.
ERROR_EXIT: dict[str, int] = {
    "": EXIT_OK,
    "usage": EXIT_USAGE,
    "verification": EXIT_VERIFICATION_FAILED,
    "blocked": EXIT_BLOCKED,
    "needs_credentials": EXIT_NEEDS_CREDENTIALS,
    "not_found": EXIT_NOT_FOUND,
    "conflict": EXIT_CONFLICT,
    "internal": EXIT_INTERNAL,
    # a timeout and a network failure are *measured* results, not internal
    # errors: the CLI's "unknown/timeout" code is the honest one.
    "timeout": EXIT_UNKNOWN,
    "network": EXIT_UNKNOWN,
}

#: The error kinds a tool may report, in severity order (the CLI reports them).
ERROR_KINDS = tuple(ERROR_EXIT)


# --------------------------------------------------------------------- limits


@dataclass(frozen=True)
class FilesystemPermission:
    """What a tool may touch on disk, and what it must refuse."""

    scope: str = "none"
    mode: str = "none"
    roots: tuple[str, ...] = ()
    deny_globs: tuple[str, ...] = ()
    symlink_policy: str = "escapes the root => refused"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "mode": self.mode,
            "roots": list(self.roots),
            "deny_globs": list(self.deny_globs),
            "symlink_policy": self.symlink_policy,
        }


@dataclass(frozen=True)
class NetworkPermission:
    """What a tool may reach over the network — and whether it needs a key."""

    mode: str = "none"
    schemes: tuple[str, ...] = ()
    hosts: tuple[str, ...] = ()
    deny_hosts: tuple[str, ...] = ()
    requires_key: bool = False
    key_env: str = ""
    endpoints: tuple[str, ...] = ()
    max_bytes: int = 0
    timeout_s: int = 0
    private_hosts: str = "refused"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "schemes": list(self.schemes),
            "hosts": list(self.hosts),
            "deny_hosts": list(self.deny_hosts),
            "requires_key": self.requires_key,
            "key_env": self.key_env,
            "endpoints": list(self.endpoints),
            "max_bytes": self.max_bytes,
            "timeout_s": self.timeout_s,
            "private_hosts": self.private_hosts,
        }


@dataclass(frozen=True)
class SubprocessPermission:
    """Whether a tool may start processes, under which policy."""

    allowed: bool = False
    shell: bool = False
    interpreter: str = ""
    default_timeout_s: int = 0
    max_timeout_s: int = 0
    kill: str = ""
    env_policy: str = ""
    env_allow: tuple[str, ...] = ()
    env_deny_pattern: str = ""
    isolation: tuple[str, ...] = ()
    stdin: str = "closed unless the call passes one"

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "shell": self.shell,
            "interpreter": self.interpreter,
            "default_timeout_s": self.default_timeout_s,
            "max_timeout_s": self.max_timeout_s,
            "kill": self.kill,
            "env_policy": self.env_policy,
            "env_allow": list(self.env_allow),
            "env_deny_pattern": self.env_deny_pattern,
            "isolation": list(self.isolation),
            "stdin": self.stdin,
        }


@dataclass(frozen=True)
class Permission:
    """The three permission axes of one tool, declared (never implied)."""

    filesystem: FilesystemPermission = field(default_factory=FilesystemPermission)
    network: NetworkPermission = field(default_factory=NetworkPermission)
    subprocess: SubprocessPermission = field(default_factory=SubprocessPermission)

    def to_dict(self) -> dict[str, Any]:
        return {
            "filesystem": self.filesystem.to_dict(),
            "network": self.network.to_dict(),
            "subprocess": self.subprocess.to_dict(),
        }


@dataclass(frozen=True)
class TruncationRule:
    """How much of a tool's own output may reach a context, and where the rest goes."""

    max_bytes: int
    strategy: str = "head+tail"
    head_fraction: float = 0.75
    max_lines: int = 0
    spill: bool = True
    spill_max_bytes: int = 4 * 1024 * 1024
    spill_dir: str = "<runtime>/tool_output"

    @property
    def head_bytes(self) -> int:
        return max(1, int(self.max_bytes * self.head_fraction))

    @property
    def tail_bytes(self) -> int:
        return max(0, self.max_bytes - self.head_bytes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_bytes": self.max_bytes,
            "strategy": self.strategy,
            "head_bytes": self.head_bytes,
            "tail_bytes": self.tail_bytes,
            "max_lines": self.max_lines,
            "spill": self.spill,
            "spill_max_bytes": self.spill_max_bytes,
            "spill_dir": self.spill_dir,
        }


# ------------------------------------------------------------------ contracts


@dataclass(frozen=True)
class ToolContract:
    """One tool's complete, machine-readable contract."""

    name: str
    title: str
    description: str
    module: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permission: Permission
    truncation: TruncationRule
    timeout_s: int
    tags: tuple[str, ...] = ()
    mutates: bool = False
    entrypoint: str = "run"
    smoke: dict[str, Any] = field(default_factory=dict)

    # -- derived facts a caller/consumer needs -------------------------------
    @property
    def max_timeout_s(self) -> int:
        declared = self.permission.subprocess.max_timeout_s
        return int(declared or self.timeout_s)

    def registry_tags(self) -> list[str]:
        """The contract facts as registry tags (the row repeats what the code says)."""
        fs = self.permission.filesystem
        net = self.permission.network
        sub = self.permission.subprocess
        proc = "shell" if sub.shell else ("none" if not sub.allowed else "exec")
        return sorted(
            {
                *self.tags,
                "core",
                f"fs:{fs.mode}",
                f"net:{net.mode}",
                f"proc:{proc}",
                f"timeout:{self.timeout_s}s",
                f"truncate:{self.truncation.max_bytes}b",
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "kind": KIND_CORE,
            "module": self.module,
            "entrypoint": self.entrypoint,
            "version": CORE_VERSION,
            "mutates": self.mutates,
            "timeout_s": self.timeout_s,
            "max_timeout_s": self.max_timeout_s,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "permissions": self.permission.to_dict(),
            "truncation": self.truncation.to_dict(),
            "smoke": dict(self.smoke),
            "tags": self.registry_tags(),
        }


# ------------------------------------------------------------------ execution


@dataclass
class ToolContext:
    """Everything a tool needs to run: the sandbox root, the runtime, limits.

    ``root`` is the workspace the tool may address. It is set by the *caller*
    (CLI ``--workspace``, ``SKALDIR_TOOL_WORKSPACE``, else the current
    directory) and never by a tool argument, so a model cannot widen its own
    filesystem scope by asking nicely.
    """

    root: Path = field(default_factory=Path.cwd)
    runtime: Path | None = None
    isolated: bool = False
    allow_private_hosts: bool = False
    offline: bool = False
    transport: Callable[..., dict] | None = None

    def spill_dir(self) -> Path | None:
        if self.runtime is None:
            return None
        return Path(self.runtime) / "tool_output"

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "runtime": str(self.runtime) if self.runtime else "",
            "isolated": bool(self.isolated),
            "offline": bool(self.offline),
        }


@dataclass
class ToolResult:
    """The structured outcome of one tool call.

    ``error_kind`` drives the exit code (:data:`ERROR_EXIT`); ``data`` is the
    typed output described by the contract's ``output_schema``.
    """

    tool: str
    ok: bool = True
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    error_kind: str = ""
    duration_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.error_kind:
            return ERROR_EXIT.get(self.error_kind, EXIT_INTERNAL)
        return EXIT_OK if self.ok else EXIT_INTERNAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "ok": bool(self.ok),
            "error_kind": self.error_kind,
            "error": self.error,
            "exit_code": self.exit_code,
            "duration_ms": int(self.duration_ms),
            "warnings": list(self.warnings),
            "evidence": list(self.evidence),
            "output": self.data,
        }


def failure(
    tool: str,
    kind: str,
    message: str,
    *,
    data: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> ToolResult:
    """A refused/failed call with the right exit-code kind (one constructor)."""
    if kind not in ERROR_EXIT:
        raise ValueError(f"unknown error kind: {kind}")
    return ToolResult(
        tool=tool,
        ok=False,
        data=dict(data or {}),
        error=str(message),
        error_kind=kind,
        warnings=list(warnings or []),
    )


# --------------------------------------------------------------- input checks


_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
    "null": lambda value: value is None,
}


def _type_problem(value: Any, expected: Any) -> str:
    options = expected if isinstance(expected, list) else [expected]
    for option in options:
        check = _TYPE_CHECKS.get(str(option))
        if check is None or check(value):
            return ""
    return f"expected {expected}, got {type(value).__name__}"


def validate_schema(schema: Any, *, where: str = "schema") -> list[str]:
    """Structural check of a contract schema itself (used by the test suite)."""
    problems: list[str] = []
    if not isinstance(schema, dict):
        return [f"{where}: not an object"]
    if schema.get("type") != "object":
        problems.append(f"{where}: type must be 'object'")
    if not isinstance(schema.get("properties"), dict):
        problems.append(f"{where}: properties must be an object")
    if schema.get("additionalProperties") is not False:
        problems.append(f"{where}: additionalProperties must be false")
    if not isinstance(schema.get("required"), list):
        problems.append(f"{where}: required must be a list")
    for name, spec in (schema.get("properties") or {}).items():
        if not isinstance(spec, dict):
            problems.append(f"{where}.{name}: property schema is not an object")
            continue
        if "type" not in spec:
            problems.append(f"{where}.{name}: no type declared")
        if not str(spec.get("description", "")).strip():
            problems.append(f"{where}.{name}: no description")
    return problems


def validate_input(schema: dict[str, Any], args: Any) -> list[str]:
    """Check ``args`` against the (small) JSON Schema subset these tools use.

    Supports ``type``, ``required``, ``properties``, ``additionalProperties:
    false``, ``enum``, ``minimum``/``maximum``, ``minLength``/``maxLength``,
    ``pattern``, ``minItems``/``maxItems`` and ``items`` — enough for the core
    toolset, with no third-party dependency on the runtime path.
    """
    if not isinstance(args, dict):
        return [f"arguments must be an object, got {type(args).__name__}"]
    problems: list[str] = []
    properties: dict[str, Any] = schema.get("properties") or {}
    for name in schema.get("required") or []:
        if name not in args:
            problems.append(f"missing required argument: {name}")
    if schema.get("additionalProperties") is False:
        for name in args:
            if name not in properties:
                problems.append(f"unknown argument: {name}")
    for name, value in args.items():
        spec = properties.get(name)
        if spec is None:
            continue
        problem = _type_problem(value, spec.get("type"))
        members = spec.get("enum")
        if not problem and isinstance(members, list) and value not in members:
            problem = f"must be one of {members}"
        if not problem and isinstance(value, str):
            if "minLength" in spec and len(value) < int(spec["minLength"]):
                problem = f"must be at least {spec['minLength']} characters"
            if "maxLength" in spec and len(value) > int(spec["maxLength"]):
                problem = f"must be at most {spec['maxLength']} characters"
            if spec.get("pattern") and not re.search(str(spec["pattern"]), value):
                problem = f"does not match {spec['pattern']!r}"
        if not problem and isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in spec and value < spec["minimum"]:
                problem = f"must be >= {spec['minimum']}"
            if "maximum" in spec and value > spec["maximum"]:
                problem = f"must be <= {spec['maximum']}"
        if not problem and isinstance(value, list):
            if "minItems" in spec and len(value) < int(spec["minItems"]):
                problem = f"needs at least {spec['minItems']} items"
            if "maxItems" in spec and len(value) > int(spec["maxItems"]):
                problem = f"accepts at most {spec['maxItems']} items"
            item_type = (spec.get("items") or {}).get("type")
            if item_type:
                for item in value:
                    item_problem = _type_problem(item, item_type)
                    if item_problem:
                        problem = f"item {item!r}: {item_problem}"
                        break
        if not problem and isinstance(value, dict) and isinstance(spec.get("properties"), dict):
            nested = validate_input(spec, value)
            if nested:
                problem = "; ".join(nested)
        if problem:
            problems.append(f"{name}: {problem}")
    return problems


def enforce_input(contract: ToolContract, args: Any) -> ToolResult | None:
    """``None`` when the arguments are fine, else a ready ``usage`` failure."""
    problems = validate_input(contract.input_schema, args)
    if not problems:
        return None
    return failure(
        contract.name,
        "usage",
        "invalid input: " + "; ".join(problems),
        data={"problems": problems},
    )
