"""``docker.ensure`` — the ninth built-in tool: the agent brings the runtime up itself.

The owner's requirement was *"set up docker start by the agent, if that is very
easy make it a fixed built-in tool"*. It is very easy on this host, and this is
that fixed tool — the same shape as the other core tools (typed input schema, typed
output schema, declared permissions, timeout, truncation, a declared smoke case),
because a second registration mechanism would be a second thing to keep honest.

It is a thin, honest shell over :mod:`skaldir.docker_ensure` (the state machine:
probe → socket activation → rootless podman → blocked). The tool adds exactly what
a *model* needs: a schema it can be trusted with, and a result that says what
happened instead of raising.

The registry name is dotted on purpose. ``agent_loop`` documents that convention
("Registry names are dotted (``tools.list``)"), normalises the name for the wire
(``docker.ensure`` → ``docker_ensure``) and keeps the mapping in ``source_name``;
the CLI's own leaf for this verb is ``docker.ensure`` too, so the capability has
the same name in every surface.

Blocked is not an error: an unreachable runtime is a **measured result** (exit 3),
so the model gets the provider, the reason and the exact command to hand the owner
— never a fake success.
"""

from __future__ import annotations

from typing import Any

from .. import docker_ensure as DE
from . import sandbox
from .contract import (
    FilesystemPermission,
    NetworkPermission,
    Permission,
    SubprocessPermission,
    ToolContext,
    ToolContract,
    ToolResult,
    TruncationRule,
    enforce_input,
    failure,
)

NAME = "docker.ensure"

MODULE = "skaldir.tools_core.docker"

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "start": {
            "type": "boolean",
            "description": (
                "attempt the declared password-free start paths (socket activation, then "
                "rootless podman) when the daemon is down; false = probe only"
            ),
        },
        "paths": {
            "type": "array",
            "description": (
                "the start paths to try, in order (default: socket-activation, rootless-podman); "
                "an empty list attempts nothing and is the docker-only check"
            ),
            "items": {"type": "string", "enum": list(DE.KNOWN_PATHS)},
        },
        "timeout_s": {
            "type": "number",
            "description": (
                "wall-clock bound for each probe (default 5.0, clamped to 0.5 … 30.0) — "
                "a dead socket is reported, never waited on"
            ),
            "minimum": DE.MIN_TIMEOUT_S,
            "maximum": int(DE.MAX_TIMEOUT_S),
        },
        "ready_s": {
            "type": "number",
            "description": (
                "how long a just-activated daemon may take to answer before the path "
                "is given up (default 5.0)"
            ),
            "minimum": DE.MIN_TIMEOUT_S,
            "maximum": int(DE.MAX_TIMEOUT_S),
        },
    },
    "required": [],
    "additionalProperties": False,
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "state": {
            "type": "string",
            "description": "`running` when a runtime answered, else `unavailable`",
        },
        "ok": {"type": "boolean", "description": "true exactly when a runtime answered"},
        "exit_code": {
            "type": "integer",
            "description": "0 when a runtime answered, 3 (blocked) when none did",
        },
        "provider": {"type": "string", "description": "`docker`, `podman`, or empty"},
        "answered_by": {
            "type": "string",
            "description": "the path that answered: probe | socket-activation | rootless-podman",
        },
        "server_version": {
            "type": "string",
            "description": "the runtime server's own reported version (empty when unavailable)",
        },
        "storage_driver": {
            "type": "string",
            "description": "the runtime's storage driver, as it reports it",
        },
        "rootless": {
            "type": ["boolean", "null"],
            "description": "rootless flag as podman reports it; null for docker and unknown",
        },
        "reason": {
            "type": "string",
            "description": "the measured reason nothing answered (empty when running)",
        },
        "detail": {"type": "string", "description": "one-line summary of the outcome"},
        "exact_command": {
            "type": "string",
            "description": (
                "the literal one-liner the owner must run when nothing answered; empty "
                "when running (never executed by this tool)"
            ),
        },
        "remediation": {
            "type": "array",
            "description": "ordered steps (step · command · why) for the owner",
            "items": {
                "type": "object",
                "properties": {
                    "step": {"type": "string", "description": "what the step achieves"},
                    "command": {"type": "string", "description": "the literal command"},
                    "why": {"type": "string", "description": "why it is the step"},
                },
                "required": ["step", "command", "why"],
                "additionalProperties": False,
            },
        },
        "attempts": {
            "type": "array",
            "description": "one entry per declared start path, in the order it was tried",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "the start path"},
                    "ok": {"type": "boolean", "description": "whether it succeeded"},
                    "detail": {"type": "string", "description": "what happened"},
                    "commands": {
                        "type": "array",
                        "description": "the literal commands this path ran",
                        "items": {"type": "string"},
                    },
                    "duration_ms": {"type": "integer", "description": "wall-clock cost"},
                    "readiness_ms": {
                        "type": "integer",
                        "description": "how long the daemon took to answer after activation",
                    },
                    "polls": {
                        "type": "integer",
                        "description": "how many probes the readiness wait cost",
                    },
                },
                "required": ["path", "ok", "detail", "commands", "duration_ms"],
                "additionalProperties": False,
            },
        },
        "commands": {
            "type": "array",
            "description": "every command that was really executed (probes and the connect only)",
            "items": {"type": "string"},
        },
        "container_ops_attempted": {
            "type": "integer",
            "description": (
                "how many non-probe container operations were executed — always 0, because "
                "the guard refuses them before they run"
            ),
        },
        "socket": {"type": "string", "description": "the unix socket this call looked at"},
        "socket_exists": {"type": "boolean", "description": "whether that socket file is there"},
        "docker_bin": {"type": "string", "description": "the docker binary that was probed"},
        "podman_bin": {"type": "string", "description": "the podman binary that was probed"},
        "paths": {
            "type": "array",
            "description": "the declared path order this call used",
            "items": {"type": "string"},
        },
        "timeout_s": {"type": "number", "description": "the bounded probe timeout that was used"},
        "duration_ms": {"type": "integer", "description": "wall-clock duration of the call"},
        "probed_at": {"type": "string", "description": "ISO-8601 UTC timestamp of the call"},
        "notes": {
            "type": "array",
            "description": "the honest notes the state machine accumulated",
            "items": {"type": "string"},
        },
    },
    "required": [
        "state",
        "ok",
        "exit_code",
        "provider",
        "answered_by",
        "server_version",
        "storage_driver",
        "reason",
        "exact_command",
        "attempts",
        "commands",
        "container_ops_attempted",
        "probed_at",
    ],
    "additionalProperties": False,
}

CONTRACT = ToolContract(
    name=NAME,
    title="Ensure the container runtime is up (password-free) or report why not",
    description=(
        "Probe the docker daemon with a bounded timeout; when it does not answer, try the "
        "declared password-free start paths (systemd socket activation, then rootless podman) "
        "and report which one answered. When none does, report the measured reason and the "
        "exact command for the owner — blocked (exit 3), never a fake success. Never runs a "
        "privileged start and never runs a container."
    ),
    module=MODULE,
    input_schema=INPUT_SCHEMA,
    output_schema=OUTPUT_SCHEMA,
    permission=Permission(
        filesystem=FilesystemPermission(
            scope="host-runtime-socket",
            mode="connect-only",
            roots=(DE.DEFAULT_DOCKER_SOCKET, "<DOCKER_HOST>", "<runtime dir>/podman/podman.sock"),
            deny_globs=(),
            symlink_policy="no file is opened: the socket is connected to, and $DOCKER_HOST "
            "decides which one",
        ),
        network=NetworkPermission(
            mode="local-socket",
            schemes=("unix", "tcp"),
            hosts=("the socket $DOCKER_HOST names (default: the local unix socket)",),
            deny_hosts=(),
            requires_key=False,
            endpoints=(),
            max_bytes=0,
            timeout_s=5,
            private_hosts="a tcp:// DOCKER_HOST is honoured because the docker CLI honours it; "
            "nothing else is dialled",
        ),
        subprocess=SubprocessPermission(
            allowed=True,
            shell=False,
            interpreter="direct argv — the docker/podman binary and a probe verb",
            default_timeout_s=5,
            max_timeout_s=30,
            kill="the probe runs in its own process group: SIGTERM, then SIGKILL after "
            "a 1 s grace period, so a hung runtime CLI cannot outlive the probe",
            env_policy=(
                "the caller's environment minus the credential scrub — the allowlist is "
                "deliberately NOT applied, because $DOCKER_HOST must stay real"
            ),
            env_deny_pattern=sandbox.ENV_DENY_PATTERN,
            isolation=(),
            stdin="none",
        ),
    ),
    truncation=TruncationRule(max_bytes=16384, strategy="head", spill=False),
    timeout_s=20,
    tags=("container", "docker", "podman", "runtime", "capability"),
    mutates=False,
    smoke={
        # The smoke case needs an outside dependency (a container daemon), exactly like
        # the network tools do: `tools-core smoke` SKIPS it unless `--network` is passed,
        # because a host without a runtime must not report the toolset as failing.
        #
        # Its input is also the *sample* the agent loop reads to type this tool for the
        # model (`agent_loop.input_schema_from_sample`), so it spells the real call shape
        # out: the defaults with both optional numeric knobs. `paths` stays available
        # (the derived schema is open) and is declared in the contract.
        "network": True,
        "input": {"start": True, "timeout_s": 5.0, "ready_s": 5.0},
        "expect": {"state": "running", "container_ops_attempted": 0},
    },
)


def paths_problem(raw: Any) -> str:
    """The declared-path check, shared by the tool and the CLI verb (one rule)."""
    if raw is None:
        return ""
    if not isinstance(raw, list):
        return "paths must be an array of strings"
    unknown = [str(item) for item in raw if str(item) not in DE.KNOWN_PATHS]
    if unknown:
        return f"unknown start path(s): {', '.join(unknown)} (known: {', '.join(DE.KNOWN_PATHS)})"
    return ""


def run(args: dict, ctx: ToolContext) -> ToolResult:
    """Run one ``docker.ensure`` call: probe, try the password-free paths, report."""
    refusal = enforce_input(CONTRACT, args)
    if refusal is not None:
        return refusal
    problem = paths_problem(args.get("paths"))
    if problem:
        return failure(NAME, "usage", problem, data={"known_paths": list(DE.KNOWN_PATHS)})
    timeout_s = DE.clamp_timeout(args.get("timeout_s", DE.DEFAULT_TIMEOUT_S))
    ready_s = DE.clamp_timeout(args.get("ready_s", DE.ACTIVATION_READY_S))
    raw_paths = args.get("paths")
    report = DE.ensure(
        timeout_s=timeout_s,
        ready_s=ready_s,
        start=bool(args.get("start", True)),
        paths=None if raw_paths is None else tuple(str(item) for item in raw_paths),
    )
    data = report.to_dict()
    if report.ok:
        return ToolResult(tool=NAME, ok=True, data=data)
    return failure(
        NAME,
        "blocked",
        (
            f"no container runtime answered: {report.reason} — the owner's next step is "
            f"`{report.exact_command}`"
        ),
        data=data,
    )


RUNNERS = {NAME: run}

CONTRACTS = (CONTRACT,)

__all__ = [
    "CONTRACT",
    "CONTRACTS",
    "INPUT_SCHEMA",
    "MODULE",
    "NAME",
    "OUTPUT_SCHEMA",
    "RUNNERS",
    "paths_problem",
    "run",
]
