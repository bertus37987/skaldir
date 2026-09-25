"""Shared execution / permission boundary for local tool runs (master-prompt §1.1).

Every *local* tool execution path goes through this module: the panel's
``action=test`` wrapper run, the testing-tool runner at the completion gate,
the gate/builder checks (via :func:`skaldir.gates._run_checked`), the generated
CLI wrappers and — from Welle 2 on — the MCP wrappers.

Split of responsibility (one implementation, not two):

* :func:`skaldir.gates._run` stays the raw bubblewrap invocation (argv building,
  namespace setup, fail-closed runtime result). It is the *only* place that
  constructs a bwrap command line, and the only place the existing isolation
  tests monkeypatch.
* this module owns the **policy** (:class:`RunPolicy`: sandbox required, network,
  output cap), the **input** (:class:`ToolRun`: argv, workdir, timeout, binds,
  stdin, env) and the **result contract** (:class:`RunResult` with
  ``ok`` / ``failed`` / ``not-certifiable``), plus the container seam
  (:class:`ContainerExecutor`, :func:`get_container_executor`).

There is **no silent unprotected fallback**: with ``require_sandbox=True``
(the default) a missing or failing sandbox yields ``status="not-certifiable"``
and the process is never started — ``ok`` is never reported for a run that did
not happen. ``require_sandbox=False`` is an explicit, documented opt-out for
non-generated developer-owned commands; the result then always carries
``sandboxed=False`` and a detail marking it as unsandboxed. A sandbox that is
*available but fails at runtime* (namespace setup) never falls back — that
would be exactly the silent downgrade this boundary exists to prevent.
"""

from __future__ import annotations

import subprocess  # nosec B404 — bounded process execution is this module's purpose
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .gates import NO_SANDBOX_EXIT, TIMEOUT_EXIT

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_NOT_CERTIFIABLE = "not-certifiable"

SANDBOX_UNAVAILABLE_DETAIL = (
    "sandbox unavailable — execution blocked (fail-closed)"
)
UNSANDBOXED_DETAIL = "unsandboxed run — explicit policy require_sandbox=false"


@dataclass
class RunPolicy:
    """What a caller grants a run. Defaults are the strict ones."""

    require_sandbox: bool = True
    allow_network: bool = False
    max_output_bytes: int = 200_000


@dataclass
class ToolRun:
    """What a caller wants to run. argv only — a shell is never involved."""

    argv: list[str]
    workdir: str
    timeout: float = 60.0
    extra_binds: list[str] = field(default_factory=list)
    stdin: bytes | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class RunResult:
    """Structured outcome.

    ``status``: ``"ok"`` (ran, exit 0) · ``"failed"`` (ran, non-zero/timeout)
    · ``"not-certifiable"`` (never ran — no sandbox; NOT a pass and NOT a
    plain failure). ``exit_code`` is only set when a process actually ran.

    ``stdout`` carries the merged output of the bwrap run (the primitive
    returns one combined stream); ``stderr`` stays for callers that get
    separated streams later. ``detail`` is the human-readable reason,
    ``sandboxed`` records whether the run went through the sandbox.
    """

    ok: bool
    status: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    detail: str = ""
    sandboxed: bool = False


def sandbox_available() -> bool:
    """Probe the sandbox through the one existing probe (gates, positive-cache)."""
    from .gates import _sandbox_available

    return _sandbox_available()


def _truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated: {len(text) - limit} chars]"


def run_local(
    run: ToolRun,
    *,
    policy: RunPolicy = RunPolicy(),  # noqa: B008 — frozen interface default (strict policy)
) -> RunResult:
    """Run one local tool invocation under ``policy``.

    Fail-closed: a required but unavailable sandbox returns
    ``status="not-certifiable"`` without starting anything. ``failed`` means
    the process ran (exit code or timeout); ``ok`` means it ran and exited 0.
    """
    argv = [str(arg) for arg in run.argv]
    if not argv:
        return RunResult(ok=False, status=STATUS_FAILED, detail="empty argv — nothing to run")
    workdir = Path(run.workdir).resolve()
    if not workdir.is_dir():
        return RunResult(
            ok=False,
            status=STATUS_FAILED,
            detail=f"workdir does not exist: {run.workdir}",
        )
    if not sandbox_available():
        if policy.require_sandbox:
            return RunResult(
                ok=False,
                status=STATUS_NOT_CERTIFIABLE,
                sandboxed=False,
                detail=SANDBOX_UNAVAILABLE_DETAIL,
            )
        return _run_unsandboxed(run, argv=argv, workdir=workdir, policy=policy)

    # the one bubblewrap implementation (resolved at call time so the
    # isolation tests' monkeypatches of skaldir.gates keep working)
    from .gates import _run as gates_run

    try:
        code, output, sandboxed = gates_run(
            argv,
            workdir=workdir,
            timeout=run.timeout,
            extra_env=dict(run.env) or None,
            extra_binds=list(run.extra_binds) or None,
            allow_network=policy.allow_network,
            stdin=run.stdin,
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            ok=False,
            status=STATUS_FAILED,
            exit_code=TIMEOUT_EXIT,
            detail=f"timeout after {run.timeout:.0f}s",
        )
    output = _truncate(output or "", policy.max_output_bytes)
    if code == NO_SANDBOX_EXIT and not sandboxed:
        # bwrap failed at runtime (namespace setup): blocked, never downgraded
        return RunResult(
            ok=False,
            status=STATUS_NOT_CERTIFIABLE,
            sandboxed=False,
            detail=output or SANDBOX_UNAVAILABLE_DETAIL,
        )
    return RunResult(
        ok=code == 0,
        status=STATUS_OK if code == 0 else STATUS_FAILED,
        exit_code=code,
        stdout=output,
        detail="" if code == 0 else output,
        sandboxed=sandboxed,
    )


def _run_unsandboxed(
    run: ToolRun,
    *,
    argv: list[str],
    workdir: Path,
    policy: RunPolicy,
) -> RunResult:
    """Explicitly requested unprotected run (``require_sandbox=False``).

    Only for developer-owned, non-generated commands: the caller has declared
    that the sandbox is not required. The result always reports
    ``sandboxed=False`` and marks it in ``detail`` — nothing silent about it.
    """
    from .gates import _base_env  # same minimal env as the sandbox path

    env = _base_env(workdir)
    env.update(run.env)
    try:
        proc = subprocess.run(  # nosec B603 — fixed argv list, no shell; explicit policy path
            argv,
            capture_output=True,
            input=run.stdin,
            timeout=run.timeout,
            cwd=str(workdir),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            ok=False,
            status=STATUS_FAILED,
            exit_code=TIMEOUT_EXIT,
            detail=f"{UNSANDBOXED_DETAIL} | timeout after {run.timeout:.0f}s",
        )
    except OSError as exc:
        return RunResult(
            ok=False,
            status=STATUS_FAILED,
            detail=f"{UNSANDBOXED_DETAIL} | {type(exc).__name__}: {exc}",
        )
    stdout = _truncate((proc.stdout or b"").decode("utf-8", "replace"), policy.max_output_bytes)
    stderr = _truncate((proc.stderr or b"").decode("utf-8", "replace"), policy.max_output_bytes)
    ok = proc.returncode == 0
    detail = UNSANDBOXED_DETAIL if ok else f"{UNSANDBOXED_DETAIL} | exit {proc.returncode}"
    return RunResult(
        ok=ok,
        status=STATUS_OK if ok else STATUS_FAILED,
        exit_code=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        detail=detail,
        sandboxed=False,
    )


class ContainerExecutor(Protocol):
    """Execution boundary for containerised tools (DockerRuntimeManager, Welle 2)."""

    def available(self) -> tuple[bool, str]: ...

    def run_job(self, job: Any, policy: RunPolicy) -> RunResult: ...


class NullExecutor:
    """Placeholder until the container runtime exists (Docker is not installed yet).

    It answers ``available() == (False, …)`` and returns a structured
    ``not-certifiable`` error — never an unprotected local fallback for a job
    that was meant for a container.
    """

    reason = "docker runtime not installed yet"

    def available(self) -> tuple[bool, str]:
        return False, self.reason

    def run_job(self, job: Any, policy: RunPolicy) -> RunResult:
        return RunResult(
            ok=False,
            status=STATUS_NOT_CERTIFIABLE,
            sandboxed=False,
            detail=f"container execution not available: {self.reason}",
        )


_CONTAINER_EXECUTOR: ContainerExecutor | None = None


def register_container_executor(executor: ContainerExecutor) -> None:
    """Install the container executor (Welle 2/B6 replaces the NullExecutor)."""
    global _CONTAINER_EXECUTOR
    _CONTAINER_EXECUTOR = executor


def get_container_executor() -> ContainerExecutor:
    """The active container executor — the NullExecutor until B6 lands."""
    return _CONTAINER_EXECUTOR if _CONTAINER_EXECUTOR is not None else NullExecutor()
