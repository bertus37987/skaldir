"""Gate runner for tool candidates: scan → import → test → determinism (CORE-PLAN §4).

Every stage runs offline in a subprocess with a minimal environment and a hard
timeout. When ``bwrap`` is available (probed once, cached) the process is
additionally sandboxed: network namespace unshared, root bound read-only, the
workspace writable. If ``bwrap`` **itself** fails to start the payload it says
so in its own ``bwrap:`` diagnostic and the run fails closed — untrusted
generated code is never executed unsandboxed. A payload that ran and exited with
a non-0/1 code (or with no output at all) is reported with *its own* code: the
wrapper propagates it, and blaming the sandbox for a silent failure destroyed
the diagnosis of the recovered long run (see
``evidence/12-runtime-final/silent-exit-misreported.txt``).

The runner only *reports*; approval is a separate, explicit step.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 — gated subprocess runs are this module's purpose
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .tools import ToolSpec
from .toolscan import has_blocking, scan_tool_file

_SANDBOX_CACHE: bool | None = None


@dataclass
class GateCheck:
    gate: str
    passed: bool
    detail: str


@dataclass
class GateReport:
    spec: str
    checks: list[GateCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def render_lines(self) -> list[str]:
        return [
            f"{'✓' if check.passed else '✗'} {check.gate} — {check.detail}" for check in self.checks
        ]


def _sandbox_available() -> bool:
    """Probe whether bwrap works on this system (userns etc.).

    Only a *positive* result is cached (P1/B2): a stale negative would block
    every gate forever, so a failed probe is retried next time.
    """
    global _SANDBOX_CACHE
    if _SANDBOX_CACHE:
        return True
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        return False
    try:
        probe = subprocess.run(  # nosec B603 — fixed argv (bwrap path), no shell
            [
                bwrap,
                "--unshare-net",
                "--die-with-parent",
                "--ro-bind",
                "/usr",
                "/usr",
                "--ro-bind",
                "/etc",
                "/etc",
                "--symlink",
                "usr/bin",
                "/bin",
                "--symlink",
                "usr/lib",
                "/lib",
                "--symlink",
                "usr/lib64",
                "/lib64",
                "--",
                "/usr/bin/python3",
                "-c",
                "pass",
            ],
            capture_output=True,
            timeout=10,
        )
        alive = probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        alive = False
    if alive:
        _SANDBOX_CACHE = True
    return alive


def _base_env(workdir: Path) -> dict[str, str]:
    """The minimal environment every local run gets (never the host env)."""
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONPATH": str(workdir),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(workdir),
        "LC_ALL": "C.UTF-8",
    }


def _bwrap_diagnostic(stderr: bytes | str | None) -> str:
    """``bwrap``'s **own** failure line, or ``""`` when the payload spoke instead.

    ``bwrap`` propagates the child's exit code, so a non-0/1 code is normally the child's
    own. Its failures are announced by the wrapper itself, on stderr, prefixed ``bwrap:``
    (measured: ``bwrap: Can't find source path /nonexistent-xyz: No such file or directory``).
    That prefix is the only reliable separator between "the sandbox refused to start" and
    "the sandbox ran the payload and the payload exited" — the pair the old
    ``returncode in (0, 1) or stdout or stderr`` test could not tell apart.
    """
    if isinstance(stderr, bytes):
        text = stderr.decode("utf-8", "replace")
    else:
        text = str(stderr or "")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("bwrap:"):
            return stripped
    return ""


def _run(
    cmd: list[str],
    *,
    workdir: Path,
    timeout: float,
    extra_env: dict[str, str] | None = None,
    extra_binds: list[str] | None = None,
    allow_network: bool = False,
    stdin: bytes | None = None,
    read_only_workdir: bool = False,
) -> tuple[int, str, bool]:
    """Run a command; returns (returncode, output, sandboxed).

    ``allow_network`` and ``stdin`` are additive (defaults preserve the old
    behaviour) and exist for the shared boundary in :mod:`skaldir.execution`:
    ``allow_network=True`` drops ``--unshare-net``, ``stdin`` feeds bytes to
    the child.
    """
    workdir = Path(workdir).resolve()  # bwrap needs absolute bind paths
    env = _base_env(workdir)
    if extra_env:
        env.update(extra_env)
    bwrap = shutil.which("bwrap")
    if bwrap and _sandbox_available():
        # Minimal read-only system binds (P1/B2): the whole rootfs used to be
        # visible; /home, /root and /tmp are now empty inside the sandbox.
        exe_binds: list[str] = []
        seen_paths: set[str] = set()
        bind_dirs: list[str] = []
        todo = [str(Path(sys.executable)), os.path.realpath(sys.executable)]
        while todo:
            candidate = todo.pop()
            if candidate in seen_paths or candidate.startswith("/usr/"):
                continue
            seen_paths.add(candidate)
            if os.path.islink(candidate):
                target = os.readlink(candidate)
                nxt = target if target.startswith("/") else os.path.join(
                    os.path.dirname(candidate), target
                )
                todo.append(os.path.normpath(nxt))
            # bind the containing dirs read-only (venv bin/ and its prefix);
            # the chain also covers intermediate symlink hops (e.g. uv's
            # cpython-3.11 → cpython-3.11.16)
            for folder in (str(Path(candidate).parent), str(Path(candidate).parent.parent)):
                if folder != "/" and not folder.startswith("/usr/") and folder not in bind_dirs:
                    if os.path.isdir(folder):
                        bind_dirs.append(folder)
        for folder in bind_dirs:
            exe_binds += ["--ro-bind", folder, folder]
        # Optional extra READ-ONLY binds (e.g. a check target outside the
        # workdir): a checker reads its target, it never writes it.
        extra_bind_args: list[str] = []
        for path in extra_binds or []:
            candidate = str(Path(path).resolve())
            if candidate != str(workdir) and os.path.isdir(candidate):
                extra_bind_args += ["--ro-bind", candidate, candidate]
        full = [
            bwrap,
            *(["--unshare-net"] if not allow_network else []),
            "--die-with-parent",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/etc",
            "/etc",
            "--ro-bind",
            "/opt",
            "/opt",
            "--symlink",
            "usr/bin",
            "/bin",
            "--symlink",
            "usr/sbin",
            "/sbin",
            "--symlink",
            "usr/lib",
            "/lib",
            "--symlink",
            "usr/lib64",
            "/lib64",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",  # nosec B108 — bwrap mount target inside the sandbox, not a temp path
            "--tmpfs",
            "/home",
            "--tmpfs",
            "/root",
            "--tmpfs",
            "/var",
            *exe_binds,
            *extra_bind_args,
            "--ro-bind" if read_only_workdir else "--bind",
            str(workdir),
            str(workdir),
            "--",
            *cmd,
        ]
        try:
            run_kwargs: dict = {
                "capture_output": True,
                "timeout": timeout,
                "env": env,
                "cwd": workdir,
            }
            if stdin is not None:
                run_kwargs["input"] = stdin
            else:
                run_kwargs["text"] = True
            proc = subprocess.run(  # nosec B603 — fixed argv, no shell, minimal env, timeout
                full, **run_kwargs
            )
        except subprocess.TimeoutExpired:
            raise
        if isinstance(proc.stdout, bytes) or isinstance(proc.stderr, bytes):
            output = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", "replace")
        else:
            output = (proc.stdout or "") + (proc.stderr or "")
        diagnostic = _bwrap_diagnostic(proc.stderr)
        if diagnostic:
            # The *wrapper* refused to start the payload (a bind it cannot resolve, a
            # namespace it cannot create). Fail closed (P1/B2) — untrusted generated code is
            # never run unsandboxed — and carry bwrap's own words so the failure is
            # diagnosable. Measured: bwrap exits 1 with a ``bwrap:`` line; this branch no
            # longer depends on the numeric code it happens to pick.
            return (
                -127,
                f"sandbox unavailable — execution blocked (fail-closed): {diagnostic}",
                False,
            )
        # The payload ran. Every code is the *child's own* — a silent exit 2/3/… used to be
        # reported as ``-127 sandbox unavailable``, which made ``expect_exit`` unobservable
        # and blamed the wrong subsystem
        # (evidence/12-runtime-final/silent-exit-misreported.txt).
        return proc.returncode, output.strip(), True
    # no sandbox → no execution (fail-closed)
    return -127, "sandbox unavailable — execution blocked (fail-closed)", False


def _run_checked(
    cmd: list[str],
    *,
    workdir: Path,
    timeout: float,
    extra_binds: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, bool]:
    """Tuple adapter over the shared execution boundary (§1.1).

    Delegates to :func:`skaldir.execution.run_local` (which owns the policy and
    the structured result) and maps its ``RunResult`` back to the historical
    ``(exit_code, output, sandboxed)`` tuple so every existing caller keeps its
    contract; ``not-certifiable`` maps to :data:`NO_SANDBOX_EXIT`.
    """
    from .execution import RunPolicy, ToolRun, run_local

    result = run_local(
        ToolRun(
            argv=list(cmd),
            workdir=str(workdir),
            timeout=timeout,
            extra_binds=list(extra_binds or []),
            env=dict(extra_env or {}),
        ),
        policy=RunPolicy(),
    )
    if result.status == "not-certifiable":
        return NO_SANDBOX_EXIT, result.detail, False
    if result.status == "failed" and result.exit_code == TIMEOUT_EXIT:
        return TIMEOUT_EXIT, result.detail, False
    code = result.exit_code if result.exit_code is not None else NO_SANDBOX_EXIT
    return code, (result.stdout + result.stderr).strip(), result.sandboxed


# Exit codes reserved for "the runner could not execute the check at all" —
# deliberately outside the range a command can produce in a shell (>= 0).
NO_SANDBOX_EXIT = -127
TIMEOUT_EXIT = -9


def run_declared_check(
    argv: list[str],
    *,
    workdir: Path,
    timeout: float,
    extra_env: dict[str, str] | None = None,
    read_only_workspace: bool = False,
) -> tuple[int, str, bool]:
    """Run a **workspace-declared** verification command, sandboxed (Block 3).

    Declared commands come from the workspace itself (``.godsv/checks.json`` or
    an env declaration), i.e. they are code the project supplies — never run
    them unsandboxed. Returns ``(exit_code, output, sandboxed)``; a missing
    sandbox yields :data:`NO_SANDBOX_EXIT` and a timeout
    :data:`TIMEOUT_EXIT`, so callers can tell "did not run" from "ran and
    failed" without guessing.
    """
    if not _sandbox_available():
        return (
            NO_SANDBOX_EXIT,
            "sandbox unavailable — declared check not executed (fail-closed)",
            False,
        )
    try:
        code, output, sandboxed = _run(
            list(argv), workdir=workdir, timeout=timeout, extra_env=extra_env,
            read_only_workdir=read_only_workspace,
        )
    except subprocess.TimeoutExpired:
        return TIMEOUT_EXIT, f"timeout after {timeout:.0f}s", False
    if code == NO_SANDBOX_EXIT and not sandboxed:
        return code, output, sandboxed
    return code, output, sandboxed


def _shorten(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + " […]"


def _find_source(spec: ToolSpec, workdir: Path) -> Path | None:
    if not spec.module:
        return None
    relative = spec.module.replace(".", "/") + ".py"
    candidates = [
        workdir / relative,
        Path(relative),
        workdir / (spec.module.split(".")[-1] + ".py"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def run_gates(
    spec: ToolSpec,
    *,
    workdir: str | Path,
    timeout: float = 30.0,
) -> GateReport:
    """Run all applicable gates for a python tool spec (offline, sandboxed)."""
    workdir = Path(workdir)
    report = GateReport(spec=spec.name)

    source = _find_source(spec, workdir)
    if source is None:
        report.checks.append(GateCheck("scan", False, "source code not found"))
    else:
        findings = scan_tool_file(source)
        counts = Counter(finding.severity for finding in findings)
        detail = ", ".join(f"{sev}: {count}" for sev, count in sorted(counts.items()))
        report.checks.append(
            GateCheck("scan", not has_blocking(findings), detail or "no findings")
        )

    if spec.module:
        code, output, sandboxed = _run_checked(
            [sys.executable, "-c", f"import {spec.module}"], workdir=workdir, timeout=timeout
        )
        label = "Import ok" if code == 0 else _shorten(output)
        if code == 0 and sandboxed:
            label = "Import ok (bwrap-Sandbox)"
        report.checks.append(GateCheck("import", code == 0, label))

    if spec.test_command:
        code, output, _sandboxed = _run_checked(
            list(spec.test_command), workdir=workdir, timeout=timeout
        )
        report.checks.append(
            GateCheck("test", code == 0, "Tests green" if code == 0 else _shorten(output))
        )

    if spec.entrypoint and spec.sample_input:
        script = (
            "import json\n"
            f"import {spec.module} as m\n"
            f"print(json.dumps(m.{spec.entrypoint}(**{json.dumps(spec.sample_input)}),"
            " sort_keys=True, default=str))\n"
        )
        outputs: list[str] = []
        ok = True
        detail = ""
        for _ in range(2):
            code, output, _sandboxed = _run_checked(
                [sys.executable, "-c", script], workdir=workdir, timeout=timeout
            )
            if code != 0:
                ok = False
                detail = "run failed: " + _shorten(output)
                break
            outputs.append(output)
        if ok:
            if outputs[0] != outputs[1]:
                ok = False
                detail = "outputs differ (non-deterministic)"
            else:
                detail = _shorten(outputs[0])
        report.checks.append(GateCheck("determinism", ok, detail))

    return report

def parse_testing_result(code: int, output: str) -> tuple[bool, str]:
    """Structured result contract for testing tools (audit P0, 13.09.2026).

    A pass requires BOTH: exit code 0 AND an explicit ``PASS`` verdict line.
    Explicit FAIL/ERROR verdict lines always win — ``FAIL: check did not PASS``
    at exit 0 is NOT a pass. Anything ambiguous (no verdict line) fails
    closed. Returns ``(ok, reason)`` with an empty reason on success.
    """
    import re as _re

    upper = (output or "").upper()
    verdict_fail = _re.search(r"^\s*(?:FAIL|FAILED|ERROR)\b", upper, _re.M) is not None
    verdict_pass = _re.search(r"^\s*PASS\b", upper, _re.M) is not None
    if code != 0:
        return False, f"exit {code}"
    if verdict_fail:
        return False, "explicit FAIL/ERROR verdict line"
    if verdict_pass:
        return True, ""
    return False, "no explicit PASS verdict line (fail-closed)"
