"""``terminal`` — run a command in a sandboxed working directory.

The contract (see :mod:`skaldir.tools_core.contract` for the shape):

* **Sandboxed working directory.** The command runs with its ``cwd`` pinned to
  the declared root (or a subdirectory of it — a ``cwd`` outside the root is
  refused before anything starts). The tool never addresses a path outside the
  root, and the child is told where it stands through
  ``SKALDIR_TOOL_WORKSPACE``/``GODSV_TOOL_WORKSPACE``.
* **Timeout.** Default 30 s, hard maximum 600 s. On timeout the whole process
  group is signalled (``SIGTERM``, then ``SIGKILL`` after a grace period) and the
  partial output is returned with ``timed_out: true`` — a measured result, not a
  hidden hang.
* **Output cap.** stdout and stderr are each capped by the contract's
  truncation rule (64 KiB, head+tail, marker, spill to
  ``<runtime>/tool_output/``).
* **Exit code.** The child's exit status is data; the *tool call* fails only
  when the command could not be started (``internal``), the timeout expired
  (``timeout``) or the request is refused (``blocked``/``usage``). A command
  that exits 1 is a successful observation of an exit code 1.
* **Environment.** Allowlist + credential scrub (see
  :func:`skaldir.tools_core.sandbox.child_environment`), reported per call.
* **Isolation.** Process group by default; optional ``bubblewrap`` escalation
  (``isolated: true``) that makes the filesystem read-only except the workspace,
  gives a private ``/tmp`` and namespaces the network off. When bubblewrap is not
  usable the result says so and falls back to the process sandbox — it never
  claims an isolation it did not get.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import replace
from pathlib import Path

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

NAME = "terminal"
MODULE = "skaldir.tools_core.terminal"

_KILL_GRACE_S = 1.0

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "minLength": 1,
            "maxLength": 20000,
            "description": "the shell command line, executed with /bin/sh -c inside the root",
        },
        "cwd": {
            "type": "string",
            "description": "working directory relative to the workspace root (default: the root)",
        },
        "timeout_s": {
            "type": "integer",
            "minimum": 1,
            "maximum": 600,
            "description": "wall-clock limit in seconds (default 30, hard maximum 600)",
        },
        "max_output_bytes": {
            "type": "integer",
            "minimum": 256,
            "maximum": 1048576,
            "description": "per-stream output cap (default 65536; the contract's cap is a ceiling)",
        },
        "stdin": {
            "type": "string",
            "maxLength": 65536,
            "description": "text piped to the command's stdin; empty means stdin is closed",
        },
        "env": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": (
                "extra environment variables, name -> value; both strings (a value is "
                "stringified; a name must be a valid variable name and must not look "
                "like a credential) — an open string map, not a free-form object"
            ),
        },
        "isolated": {
            "type": "boolean",
            "description": (
                "escalate to the bubblewrap sandbox when it is usable (else reported fallback)"
            ),
        },
        "unshare_net": {
            "type": "boolean",
            "description": "in isolated mode, drop the network namespace (default true)",
        },
    },
    "required": ["command"],
    "additionalProperties": False,
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "the command line as given"},
        "argv": {"type": "array", "description": "the argv that was actually executed"},
        "cwd": {"type": "string", "description": "absolute working directory"},
        "exit_code": {"type": "integer", "description": "child exit status (-1 when killed)"},
        "signal": {"type": "string", "description": "signal name when the child died from one"},
        "timed_out": {"type": "boolean", "description": "true when the timeout expired"},
        "duration_ms": {"type": "integer", "description": "wall-clock duration"},
        "stdout": {"type": "string", "description": "captured stdout (truncated per the rule)"},
        "stderr": {"type": "string", "description": "captured stderr (truncated per the rule)"},
        "stdout_bytes": {"type": "integer", "description": "stdout bytes before truncation"},
        "stderr_bytes": {"type": "integer", "description": "stderr bytes before truncation"},
        "stdout_truncated": {"type": "boolean", "description": "stdout was cut"},
        "stderr_truncated": {"type": "boolean", "description": "stderr was cut"},
        "truncation": {"type": "object", "description": "per-stream truncation accounting"},
        "spill": {"type": "array", "description": "paths of spilled full outputs"},
        "isolation": {"type": "object", "description": "the isolation that was really used"},
        "policy": {"type": "object", "description": "environment policy report (names, no values)"},
    },
    "required": ["command", "cwd", "exit_code", "timed_out", "duration_ms", "stdout", "stderr"],
    "additionalProperties": False,
}

CONTRACT = ToolContract(
    name=NAME,
    title="Run a shell command in the sandbox",
    description=(
        "Execute a command line in a sandboxed working directory with a wall-clock "
        "timeout, a per-stream output cap and the exit code reported as data."
    ),
    module=MODULE,
    input_schema=INPUT_SCHEMA,
    output_schema=OUTPUT_SCHEMA,
    permission=Permission(
        filesystem=FilesystemPermission(
            scope="workspace",
            mode="read-write",
            roots=("<workspace>",),
            deny_globs=(),
            symlink_policy="cwd must resolve inside the root (a symlinked cwd is refused)",
        ),
        network=NetworkPermission(
            mode="inherit",
            schemes=("https", "http", "tcp"),
            hosts=("*",),
            deny_hosts=(),
            requires_key=False,
            endpoints=(),
            private_hosts="inherited from the caller (isolated mode drops the network namespace)",
        ),
        subprocess=SubprocessPermission(
            allowed=True,
            shell=True,
            interpreter="/bin/sh -c",
            default_timeout_s=30,
            max_timeout_s=600,
            kill="process group: SIGTERM, then SIGKILL after a 1 s grace period",
            env_policy="allowlist + credential scrub + runtime-home pass-through",
            env_allow=sandbox.ENV_ALLOW,
            env_deny_pattern=sandbox.ENV_DENY_PATTERN,
            isolation=("process-group", "bubblewrap (opt-in)"),
            stdin="closed unless the call passes stdin",
        ),
    ),
    truncation=TruncationRule(max_bytes=65536, strategy="head+tail", spill=True),
    timeout_s=30,
    tags=("terminal", "shell", "sandbox"),
    mutates=True,
    smoke={
        "input": {"command": "printf 'core-smoke\\n'"},
        "expect": {"exit_code": 0, "stdout_contains": "core-smoke"},
    },
)


def _signal_name(returncode: int) -> str:
    if returncode >= 0:
        return ""
    number = -returncode
    try:
        return signal.Signals(number).name
    except ValueError:  # pragma: no cover — a signal this build does not know
        return f"SIG{number}"


def _run(argv: list[str], cwd: Path, env: dict[str, str], stdin: str, timeout_s: int) -> dict:
    """Start the child in its own process group; kill the group on timeout."""
    started = time.time()
    process = subprocess.Popen(  # noqa: S603 — the argv is built here, not by the caller
        argv,
        cwd=str(cwd),
        env=env,
        stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    timed_out = False
    stdout = b""
    stderr = b""
    try:
        stdout, stderr = process.communicate(
            input=stdin.encode() if stdin else None, timeout=timeout_s
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover — a child ignoring SIGKILL
            stdout, stderr = b"", b""
    return {
        "returncode": int(process.returncode if process.returncode is not None else -1),
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "duration_ms": int((time.time() - started) * 1000),
    }


def _kill_group(process: subprocess.Popen) -> None:
    """SIGTERM the whole group, wait briefly, then SIGKILL what is left."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except (ProcessLookupError, PermissionError):  # already gone
            return
        if sig is signal.SIGTERM:
            try:
                process.wait(timeout=_KILL_GRACE_S)
                return
            except subprocess.TimeoutExpired:
                continue


def _extra_env(value: object) -> tuple[dict[str, str] | None, str]:
    if value is None:
        return {}, ""
    if not isinstance(value, dict):
        return None, "env must be an object of string values"
    extra: dict[str, str] = {}
    import re

    deny = re.compile(sandbox.ENV_DENY_PATTERN)
    for name, item in value.items():
        key = str(name)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            return None, f"env name {key!r} is not a valid variable name"
        if deny.search(key):
            return None, f"env name {key!r} looks like a credential — refused"
        extra[key] = str(item)
    return extra, ""


def run(args: dict, ctx: ToolContext) -> ToolResult:
    refusal = enforce_input(CONTRACT, args)
    if refusal is not None:
        return refusal
    started = time.time()
    command = str(args["command"])
    root = Path(ctx.root).resolve()

    cwd_value = str(args.get("cwd", "") or ".")
    cwd, problem = sandbox.candidate_path(root, cwd_value)
    if cwd is None:
        return failure(NAME, "blocked", f"cwd refused: {problem}", data={"cwd": cwd_value})
    if not cwd.is_dir():
        return failure(
            NAME, "not_found", f"cwd does not exist: {cwd_value}", data={"cwd": str(cwd)}
        )

    timeout_s = int(args.get("timeout_s", CONTRACT.timeout_s) or CONTRACT.timeout_s)
    warnings: list[str] = []
    if timeout_s > CONTRACT.max_timeout_s:
        warnings.append(
            f"timeout_s {timeout_s} exceeds the contract maximum {CONTRACT.max_timeout_s}; clamped"
        )
        timeout_s = CONTRACT.max_timeout_s
    rule = CONTRACT.truncation
    cap = int(args.get("max_output_bytes", 0) or 0)
    if cap:
        if cap > rule.max_bytes:
            warnings.append(
                f"max_output_bytes {cap} exceeds the contract cap {rule.max_bytes}; clamped"
            )
            cap = rule.max_bytes
        rule = replace(rule, max_bytes=cap)

    extra, env_problem = _extra_env(args.get("env"))
    if extra is None:
        return failure(NAME, "usage", env_problem)
    child_env, env_report = sandbox.child_environment(root, extra=extra)

    want_isolated = bool(args.get("isolated", False)) or bool(ctx.isolated)
    isolation = sandbox.isolation_report()
    unshare_net = bool(args.get("unshare_net", True))
    if ctx.isolated:
        want_isolated = True
    isolation_used: dict = {
        "mode": "process",
        "detail": "new session (process group), cwd pinned to the root, environment scrubbed",
        "requested": "bubblewrap" if want_isolated else "process",
        "fallback_reason": "",
        "unshare_net": False,
    }
    if want_isolated and not isolation.get("available"):
        isolation_used["fallback_reason"] = str(isolation.get("error") or "bubblewrap unavailable")
        warnings.append(
            "bubblewrap isolation was requested but is not usable here "
            f"({isolation_used['fallback_reason']}); ran in the process sandbox"
        )
    if want_isolated and isolation.get("available"):
        argv = sandbox.bubblewrap_argv(root, cwd, command, unshare_net=unshare_net)
        isolation_used.update(
            {
                "mode": "bubblewrap",
                "detail": isolation.get("filesystem", ""),
                "unshare_net": unshare_net,
            }
        )
    else:
        argv = ["/bin/sh", "-c", command]

    try:
        outcome = _run(argv, cwd, child_env, str(args.get("stdin", "") or ""), timeout_s)
    except OSError as exc:
        return failure(NAME, "internal", f"could not start the command: {exc}", data={"argv": argv})

    stdout = sandbox.truncate_bytes(
        outcome["stdout"], rule, label="stdout", runtime=ctx.runtime, spill_name=f"{NAME}-stdout"
    )
    stderr = sandbox.truncate_bytes(
        outcome["stderr"], rule, label="stderr", runtime=ctx.runtime, spill_name=f"{NAME}-stderr"
    )
    spill = [item for item in (stdout["spill_path"], stderr["spill_path"]) if item]
    returncode = int(outcome["returncode"])
    signal_name = _signal_name(returncode)
    data = {
        "command": command,
        "argv": argv,
        "cwd": str(cwd),
        "exit_code": -1 if outcome["timed_out"] and returncode < 0 else returncode,
        "signal": signal_name,
        "timed_out": bool(outcome["timed_out"]),
        "duration_ms": int(outcome["duration_ms"]),
        "stdout": stdout["text"],
        "stderr": stderr["text"],
        "stdout_bytes": stdout["total_bytes"],
        "stderr_bytes": stderr["total_bytes"],
        "stdout_truncated": bool(stdout["truncated"]),
        "stderr_truncated": bool(stderr["truncated"]),
        "truncation": {
            "rule": rule.to_dict(),
            "stdout": {key: value for key, value in stdout.items() if key != "text"},
            "stderr": {key: value for key, value in stderr.items() if key != "text"},
        },
        "spill": spill,
        "isolation": isolation_used,
        "policy": env_report,
    }
    if outcome["timed_out"]:
        result = failure(
            NAME, "timeout", f"command exceeded {timeout_s}s and was killed", data=data
        )
    elif signal_name:
        result = ToolResult(
            tool=NAME,
            ok=True,
            data=data,
            warnings=[*warnings, f"the command died from {signal_name}"],
        )
    else:
        result = ToolResult(tool=NAME, ok=True, data=data)
    result.warnings = [*warnings, *result.warnings]
    result.duration_ms = int((time.time() - started) * 1000)
    result.evidence = [{"ref": sandbox.shlex_join(argv), "kind": "run", "note": f"cwd={cwd}"}]
    return result


RUNNERS = {NAME: run}

__all__ = ["CONTRACT", "INPUT_SCHEMA", "MODULE", "NAME", "OUTPUT_SCHEMA", "RUNNERS", "run"]
