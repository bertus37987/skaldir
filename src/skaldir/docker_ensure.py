"""DOCKER-ENSURE — the agent brings the container runtime up, password-free, or says why not.

The owner's requirement: *"set up docker start by the agent"*. On a normal Fedora
install an unprivileged user already reaches the daemon (they are in the ``docker``
group), so the interesting case is not "how do we get root" but "what do we do when
the daemon is down". This module answers it with one rule:

    **Only password-free paths are attempted. Every other case is reported.**

Three states, and no fourth:

``running``
    A runtime answered. ``provider`` names it (``docker`` or ``podman``) and
    ``answered_by`` names the *path* that answered — ``probe`` (it was already
    up), :data:`PATH_SOCKET_ACTIVATION` or :data:`PATH_ROOTLESS_PODMAN`.
``unavailable``
    Nothing answered. The report carries the measured ``reason``, the literal
    **one-liner the owner must run** (``exact_command``) and a ``remediation``
    list, and the caller exits :data:`EXIT_BLOCKED` (3).

Why socket activation is the password-free start path
-----------------------------------------------------

``docker.socket`` is a systemd socket unit. systemd owns the listening socket
(``/run/docker.sock`` → ``/var/run/docker.sock``), and *the first connection to it
starts the service* — no password, no ``sudo``, no polkit prompt: the privileged
step was already taken when the unit was enabled at install time. So
:func:`ensure` does exactly what any client does: it connects. That is the whole
trick, and it is why this module never needs a privileged helper.

When the socket itself is gone (unit not enabled, no runtime installed) there is
no password-free path left on this host — ``sudo -n`` is not configured — so the
capability **stops and reports**. It never calls ``sudo``, ``doas``, ``pkexec``
or ``su``; :func:`guard_argv` refuses such an argv outright (see below), and the
only place a privileged command appears is as *text* in ``exact_command`` for the
human to run.

What this module can never do
-----------------------------

Every process it starts is one of ``<runtime> version`` / ``<runtime> info``
(:data:`ALLOWED_VERBS`). :func:`guard_argv` raises on anything else, so
"no container operation was attempted" is a property of the code, not a promise:
``container_ops_attempted`` in the report is derived from the argv list that was
really executed. Probes are bounded by a wall-clock timeout (the agent loop must
never block on a dead socket): a hung ``docker`` CLI is killed and reported as a
timeout, and a dead unix socket fails at ``connect()`` in microseconds.

Typical use::

    from skaldir import docker_ensure

    report = docker_ensure.ensure()          # probe → socket activation → podman
    report.state, report.provider, report.exit_code
    report.exact_command                     # the one-liner, when nothing answered

    docker_ensure.runtime_status()           # read-only: probe, never start
    docker_ensure.selftest_probe()           # the `skaldir selftest` probe
    docker_ensure.install_selftest_probe()   # hook (see the docstring)
"""

from __future__ import annotations

import functools
import json
import os
import shlex
import shutil
import signal
import socket as socket_module
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import env as env_module

# --------------------------------------------------------------------- states

STATE_RUNNING = "running"
STATE_UNAVAILABLE = "unavailable"

PROVIDER_DOCKER = "docker"
PROVIDER_PODMAN = "podman"

#: The declared order of the password-free start paths. ``ensure`` tries them in
#: this order and stops at the first one that answers.
PATH_PROBE = "probe"
PATH_SOCKET_ACTIVATION = "socket-activation"
PATH_ROOTLESS_PODMAN = "rootless-podman"
DEFAULT_START_PATHS: tuple[str, ...] = (PATH_SOCKET_ACTIVATION, PATH_ROOTLESS_PODMAN)

#: Every path ``--path`` accepts (so a typo is a usage error, not a silent skip).
KNOWN_PATHS: tuple[str, ...] = (PATH_SOCKET_ACTIVATION, PATH_ROOTLESS_PODMAN)

# ------------------------------------------------------------------ exit codes

#: The product CLI's vocabulary (``cli_contract``): 0 ok, 3 blocked.
EXIT_OK = 0
EXIT_BLOCKED = 3

# --------------------------------------------------------------------- limits

DEFAULT_TIMEOUT_S = 5.0
MIN_TIMEOUT_S = 0.5
MAX_TIMEOUT_S = 30.0
#: The selftest probe is bounded tighter: `skaldir selftest` must stay fast.
SELFTEST_TIMEOUT_S = 4.0

#: After a successful socket activation the daemon is *starting*, not started: the
#: first re-probe can land before it binds and answers (measured on this host — a
#: oneshot unit's own first action can still be in flight when the connect returns).
#: So the re-probe polls, with a bounded readiness budget and a fixed interval.
#: Bounded on purpose: a dead socket must never turn into a hang in the agent loop.
ACTIVATION_READY_S = 5.0
ACTIVATION_POLL_S = 0.2

#: How long a killed probe group gets after SIGTERM before SIGKILL (same policy as
#: ``tools_core.terminal`` — a graceful stop first, then no survivors).
_KILL_GRACE_S = 1.0

DEFAULT_DOCKER_SOCKET = "/var/run/docker.sock"

#: The literal one-liner the owner must run when no password-free path answers.
#: It is *only* ever returned as text — this module never executes it.
PRIMARY_EXACT_COMMAND = "sudo systemctl start docker"

#: The ordered remediation a blocked report carries (step · command · why).
REMEDIATION: tuple[dict[str, str], ...] = (
    {
        "step": "start the daemon",
        "command": PRIMARY_EXACT_COMMAND,
        "why": "starts docker.service directly; needs root because the daemon is a system service",
    },
    {
        "step": "enable socket activation (durable)",
        "command": "sudo systemctl enable --now docker.socket",
        "why": "after this the first connection to the socket starts the daemon — no password, "
        "which is the path this capability uses on its own",
    },
    {
        "step": "use the rootless fallback",
        "command": "podman info",
        "why": "rootless podman needs no daemon and no root; running it once starts the user "
        "runtime, and this capability then reports it as the answering provider",
    },
)

#: The verbs the two runtime CLIs may be invoked with. Everything else — ``run``,
#: ``exec``, ``create``, ``start``, ``compose``, … — is refused by
#: :func:`guard_argv`, so this capability cannot start a container by accident.
ALLOWED_VERBS: frozenset[str] = frozenset({"version", "info"})

#: Binaries this module refuses to run, whatever the argv says. The privileged
#: start is the owner's decision (``exact_command``), never the agent's.
PRIVILEGED_BINARIES: frozenset[str] = frozenset({"sudo", "doas", "pkexec", "su", "run0"})

#: Evidence reference of this capability (the selftest probe cites it).
EVIDENCE_REF = "src/skaldir/docker_ensure.py"

#: The name of the probe this capability installs into the EXPAND layer.
SELFTEST_PROBE_NAME = "container_runtime.ensure"

#: Marker on the wrapped layer prober, so installing twice is a no-op.
_PROBE_MARKER = "_skaldir_docker_ensure_installed"


class PrivilegedStartRefused(RuntimeError):
    """A privileged start was attempted — never by this module, on purpose."""


class ContainerOperationRefused(RuntimeError):
    """Something other than ``version``/``info`` was about to be executed."""


def guard_argv(argv: list[str]) -> None:
    """The one place a subprocess starts: refuse privilege and container ops.

    Raises :class:`PrivilegedStartRefused` for a privileged binary and
    :class:`ContainerOperationRefused` for a verb outside :data:`ALLOWED_VERBS`
    (a bare ``<binary>`` with no verb is refused too — it prints a help page, and
    a help page is not a probe).
    """
    if not argv:
        raise ContainerOperationRefused("refused: an empty argv is not a probe")
    binary = Path(str(argv[0])).name
    if binary in PRIVILEGED_BINARIES:
        raise PrivilegedStartRefused(
            f"refused: {binary!r} is a privileged start — that is the owner's decision, "
            "reported as `exact_command`, never executed by the agent"
        )
    verb = str(argv[1]) if len(argv) > 1 else ""
    if verb not in ALLOWED_VERBS:
        raise ContainerOperationRefused(
            f"refused: {binary} {verb or '(no verb)'} is not a probe "
            f"(allowed: {', '.join(sorted(ALLOWED_VERBS))})"
        )


# ------------------------------------------------------------------ datatypes


@dataclass(frozen=True)
class ExecResult:
    """One bounded subprocess call: exit code as data, never an exception."""

    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def command(self) -> str:
        """The executed line, quoted, as it can be pasted into a shell."""
        return shlex.join(self.argv)

    def first_line(self, limit: int = 300) -> str:
        """The first non-empty output line (stderr first: that is where a CLI shouts)."""
        for text in (self.stderr, self.stdout):
            for line in str(text or "").splitlines():
                stripped = line.strip()
                if stripped:
                    return stripped[:limit]
        if self.timed_out:
            return "no output (the call timed out)"
        return f"exit code {self.exit_code} without output"


@dataclass(frozen=True)
class RuntimeProbe:
    """What one runtime answered — or, with ``reachable=False``, why it did not."""

    provider: str
    reachable: bool
    detail: str
    binary: str = ""
    server_version: str = ""
    storage_driver: str = ""
    rootless: bool | None = None
    commands: tuple[str, ...] = ()
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.reachable


@dataclass(frozen=True)
class Attempt:
    """One declared start path, as it really went."""

    path: str
    ok: bool
    detail: str
    commands: tuple[str, ...] = ()
    duration_ms: int = 0
    #: How long the path had to wait for a runtime to answer (socket activation only).
    readiness_ms: int = 0
    polls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ok": bool(self.ok),
            "detail": self.detail,
            "commands": list(self.commands),
            "duration_ms": int(self.duration_ms),
            "readiness_ms": int(self.readiness_ms),
            "polls": int(self.polls),
        }


@dataclass
class EnsureReport:
    """The whole truth about one ``ensure`` call — JSON-ready, exit code included."""

    state: str = STATE_UNAVAILABLE
    provider: str = ""
    answered_by: str = ""
    server_version: str = ""
    storage_driver: str = ""
    rootless: bool | None = None
    reason: str = ""
    detail: str = ""
    exact_command: str = ""
    remediation: tuple[dict[str, str], ...] = ()
    attempts: tuple[Attempt, ...] = ()
    commands: tuple[str, ...] = ()
    container_ops_attempted: int = 0
    socket: str = ""
    socket_exists: bool = False
    docker_bin: str = ""
    podman_bin: str = ""
    paths: tuple[str, ...] = ()
    timeout_s: float = DEFAULT_TIMEOUT_S
    duration_ms: int = 0
    probed_at: str = ""
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.state == STATE_RUNNING

    @property
    def exit_code(self) -> int:
        """0 when a runtime answered, 3 (blocked) when none did."""
        return EXIT_OK if self.ok else EXIT_BLOCKED

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "ok": bool(self.ok),
            "exit_code": int(self.exit_code),
            "provider": self.provider,
            "answered_by": self.answered_by,
            "server_version": self.server_version,
            "storage_driver": self.storage_driver,
            "rootless": self.rootless,
            "reason": self.reason,
            "detail": self.detail,
            "exact_command": self.exact_command,
            "remediation": [dict(item) for item in self.remediation],
            "attempts": [item.to_dict() for item in self.attempts],
            "commands": list(self.commands),
            "container_ops_attempted": int(self.container_ops_attempted),
            "socket": self.socket,
            "socket_exists": bool(self.socket_exists),
            "docker_bin": self.docker_bin,
            "podman_bin": self.podman_bin,
            "paths": list(self.paths),
            "timeout_s": float(self.timeout_s),
            "duration_ms": int(self.duration_ms),
            "probed_at": self.probed_at,
            "notes": list(self.notes),
        }


# ------------------------------------------------------------------- plumbing


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def clamp_timeout(value: float | int | None) -> float:
    """Every probe gets a bounded wall clock — an unbounded one would hang the loop."""
    try:
        number = float(value) if value is not None else DEFAULT_TIMEOUT_S
    except (TypeError, ValueError):
        number = DEFAULT_TIMEOUT_S
    if number <= 0:
        number = DEFAULT_TIMEOUT_S
    return max(MIN_TIMEOUT_S, min(MAX_TIMEOUT_S, number))


def docker_binary(explicit: str | None = None) -> str:
    """``--docker-bin`` → ``SKALDIR_DOCKER_BIN`` → ``docker`` on ``PATH`` → ``docker``."""
    if explicit:
        return str(explicit)
    configured = env_module.first_env(("SKALDIR_DOCKER_BIN",), "")
    if configured:
        return str(configured)
    return shutil.which(PROVIDER_DOCKER) or PROVIDER_DOCKER


def podman_binary(explicit: str | None = None) -> str:
    """``--podman-bin`` → ``SKALDIR_PODMAN_BIN`` → ``podman`` on ``PATH`` → ``podman``."""
    if explicit:
        return str(explicit)
    configured = env_module.first_env(("SKALDIR_PODMAN_BIN",), "")
    if configured:
        return str(configured)
    return shutil.which(PROVIDER_PODMAN) or PROVIDER_PODMAN


def resolved_podman_bin(explicit: str | None = None) -> str:
    """The podman binary a report must NAME, probed or not — a real path or ``""``.

    ``podman_binary`` falls back to the bare name so a probe command line can
    always be built; the podman_bin *fact* in a report is different: it is what
    is installed on this host. Docker answering first used to short-circuit the
    report to ``podman_bin: ""`` even with rootless podman 5.8.1 installed
    (audit 62 #8), so the resolved path is filled in regardless of which
    provider won.
    """
    if explicit:
        return str(explicit)
    configured = env_module.first_env(("SKALDIR_PODMAN_BIN",), "")
    if configured:
        return str(configured)
    return shutil.which(PROVIDER_PODMAN) or ""


def docker_host() -> str:
    """``$DOCKER_HOST`` (the CLI's own variable — honoured, so simulations are real)."""
    return str(env_module.first_env(("DOCKER_HOST",), "") or "")


def scrubbed_environ(environ: dict[str, str] | None = None) -> dict[str, str]:
    """The environment a probe runs with: the caller's, minus credential-shaped names.

    A probe needs ``$DOCKER_HOST`` to be *real*, so this is deliberately not the
    tool sandbox's allowlist (:func:`skaldir.tools_core.sandbox.child_environment`
    would drop it). It is the other half of that same policy — the credential
    scrub — and its pattern has exactly one definition, in
    :mod:`skaldir.tools_core.sandbox`, imported lazily because the tool layer
    imports this module (a module-level import would be a cycle). Nothing a probe
    needs (``DOCKER_HOST``, ``DOCKER_CONTEXT``, ``DOCKER_TLS_VERIFY``,
    ``DOCKER_CERT_PATH``, ``DOCKER_CONFIG``, ``PATH``, ``XDG_RUNTIME_DIR``) matches
    the pattern, so the scrub cannot break a legitimate connection.
    """
    if environ is not None:
        return dict(environ)
    import re

    from .tools_core import sandbox

    deny = re.compile(sandbox.ENV_DENY_PATTERN)
    return {
        name: value
        for name, value in os.environ.items()
        if name not in sandbox.ENV_DROP and not deny.search(name)
    }


def docker_socket_path(host: str | None = None) -> str:
    """The unix socket to touch: ``DOCKER_HOST`` → ``SKALDIR_DOCKER_SOCKET`` → default."""
    configured = env_module.first_env(("SKALDIR_DOCKER_SOCKET",), "")
    if configured:
        return str(configured)
    value = docker_host() if host is None else str(host or "")
    if value.startswith("unix://"):
        remainder = value[len("unix://") :]
        return remainder or DEFAULT_DOCKER_SOCKET
    return DEFAULT_DOCKER_SOCKET


def _kill_group(process: subprocess.Popen) -> None:
    """SIGTERM the whole probe group, wait briefly, then SIGKILL what is left.

    A runtime CLI may start a child of its own (a wrapper script, a helper), so
    killing only the direct child would leave that helper running after a timeout —
    and a probe that leaves stray processes behind is not a bounded probe. The
    process-group kill is the same policy :mod:`skaldir.tools_core.terminal`
    declares, applied to the smallest possible case: one probe.
    """
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


def run_argv(
    argv: list[str],
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    environ: dict[str, str] | None = None,
) -> ExecResult:
    """Run one bounded probe. :func:`guard_argv` runs first, always.

    ``environ=None`` inherits the ambient environment on purpose: the ``docker``
    CLI must honour a real ``DOCKER_HOST`` (that is how a dead socket is
    simulated for real rather than mocked).

    The child gets its own process group and is killed *as a group* when the
    timeout expires, so a hung runtime CLI cannot outlive the probe.
    """
    guard_argv(list(argv))
    started = time.monotonic()
    try:
        process = subprocess.Popen(  # noqa: S603 — fixed argv, no shell, guard_argv above
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environ,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        return ExecResult(
            tuple(argv),
            127,
            stderr=f"{Path(str(argv[0])).name} not found: {exc}",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except OSError as exc:
        return ExecResult(
            tuple(argv),
            126,
            stderr=f"{type(exc).__name__}: {exc}",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=max(MIN_TIMEOUT_S, float(timeout_s)))
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(process)
        try:
            stdout, stderr = process.communicate(timeout=_KILL_GRACE_S)
        except subprocess.TimeoutExpired:  # pragma: no cover — a child ignoring SIGKILL
            stdout, stderr = "", ""
    duration_ms = int((time.monotonic() - started) * 1000)
    if timed_out:
        return ExecResult(
            tuple(argv),
            None,
            stdout=(stdout or "").strip(),
            stderr=f"timed out after {float(timeout_s):.1f}s (process group killed)",
            timed_out=True,
            duration_ms=duration_ms,
        )
    return ExecResult(
        tuple(argv),
        int(process.returncode if process.returncode is not None else -1),
        stdout=(stdout or "").strip(),
        stderr=(stderr or "").strip(),
        duration_ms=duration_ms,
    )


def _json_object(text: str) -> dict[str, Any] | None:
    """Parse a CLI's ``--format '{{json .}}'`` payload, or ``None`` when it is not one."""
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _dig_ci(payload: Any, *path: str) -> Any:
    """Follow a key path through nested JSON, matching each key case-insensitively.

    ``podman info --format '{{json .}}'`` spells its top-level keys lowercase
    (``version``/``store``/``host``) where podman 3/4 used ``Version``/``Store``/
    ``Host``, so a case-sensitive read would silently report "unknown" on one of
    the two. Matching case-insensitively is the honest read of both.
    """
    node: Any = payload
    for key in path:
        if not isinstance(node, dict):
            return None
        wanted = key.lower()
        match = next((name for name in node if str(name).lower() == wanted), None)
        if match is None:
            return None
        node = node[match]
    return node


# -------------------------------------------------------------------- probes


def probe_docker(
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    docker_bin: str | None = None,
    environ: dict[str, str] | None = None,
) -> RuntimeProbe:
    """Probe the docker daemon: ``docker version`` **and** ``docker info``.

    Both must answer — ``version`` alone prints the client's own version even with
    no daemon, so it is not evidence that a container could run. The reported
    ``server_version``/``storage_driver`` come from the daemon's ``info`` payload.
    """
    bounded = clamp_timeout(timeout_s)
    binary = docker_binary(docker_bin)
    commands: list[str] = []
    started = time.monotonic()
    child_env = scrubbed_environ(environ)

    version = run_argv([binary, "version", "--format", "{{json .}}"], timeout_s=bounded,
                       environ=child_env)
    commands.append(version.command)
    if not version.ok:
        return RuntimeProbe(
            provider=PROVIDER_DOCKER,
            reachable=False,
            detail=version.first_line(),
            binary=binary,
            commands=tuple(commands),
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    info = run_argv([binary, "info", "--format", "{{json .}}"], timeout_s=bounded,
                    environ=environ)
    commands.append(info.command)
    if not info.ok:
        return RuntimeProbe(
            provider=PROVIDER_DOCKER,
            reachable=False,
            detail=f"the daemon did not answer: {info.first_line()}",
            binary=binary,
            commands=tuple(commands),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    payload = _json_object(info.stdout)
    if payload is None:
        return RuntimeProbe(
            provider=PROVIDER_DOCKER,
            reachable=False,
            detail=f"info did not return JSON: {info.stdout[:200] or '(empty)'}",
            binary=binary,
            commands=tuple(commands),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    server_version = str(payload.get("ServerVersion") or "")
    driver = str(payload.get("Driver") or "")
    return RuntimeProbe(
        provider=PROVIDER_DOCKER,
        reachable=True,
        detail=f"docker server {server_version or '(unknown)'} answered "
        f"(storage driver {driver or 'unknown'})",
        binary=binary,
        server_version=server_version,
        storage_driver=driver,
        commands=tuple(commands),
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def probe_podman(
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    podman_bin: str | None = None,
    environ: dict[str, str] | None = None,
) -> RuntimeProbe:
    """Probe rootless podman — the fallback that needs no daemon and no root."""
    bounded = clamp_timeout(timeout_s)
    binary = podman_binary(podman_bin)
    started = time.monotonic()
    info = run_argv([binary, "info", "--format", "{{json .}}"], timeout_s=bounded,
                    environ=scrubbed_environ(environ))
    if not info.ok:
        return RuntimeProbe(
            provider=PROVIDER_PODMAN,
            reachable=False,
            detail=info.first_line(),
            binary=binary,
            commands=(info.command,),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    payload = _json_object(info.stdout)
    if payload is None:
        return RuntimeProbe(
            provider=PROVIDER_PODMAN,
            reachable=False,
            detail=f"podman info did not return JSON: {info.stdout[:200] or '(empty)'}",
            binary=binary,
            commands=(info.command,),
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    version = str(_dig_ci(payload, "version", "version") or "")
    rootless = _dig_ci(payload, "host", "security", "rootless")
    driver = str(_dig_ci(payload, "store", "graphdrivername") or "")
    rootless_flag = bool(rootless) if isinstance(rootless, bool) else None
    return RuntimeProbe(
        provider=PROVIDER_PODMAN,
        reachable=True,
        detail=f"podman {version or '(unknown)'} answered "
        f"(rootless={rootless_flag}, storage driver {driver or 'unknown'})",
        binary=binary,
        server_version=version,
        storage_driver=driver,
        rootless=rootless_flag,
        commands=(info.command,),
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def activate_socket(
    *,
    socket_path: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    connect: Callable[[str, float], str] | None = None,
) -> Attempt:
    """Trigger systemd socket activation by *connecting* to the docker socket.

    This is the password-free start: systemd already owns the listening socket, so
    the first connection starts ``docker.service``. Nothing here needs privilege —
    and nothing here is a container operation: one ``connect()``, then done.
    """
    bounded = clamp_timeout(timeout_s)
    path = socket_path or docker_socket_path()
    started = time.monotonic()
    connector = connect or _connect_unix
    try:
        detail = connector(path, bounded)
        ok = True
    except OSError as exc:
        ok = False
        detail = f"connecting to {path} failed: {type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 — a start path must report, never raise
        ok = False
        detail = f"connecting to {path} failed: {type(exc).__name__}: {exc}"
    return Attempt(
        path=PATH_SOCKET_ACTIVATION,
        ok=ok,
        detail=detail,
        commands=(f"connect({path})",),
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _connect_unix(path: str, timeout_s: float) -> str:
    """One connect to one unix socket. systemd does the rest."""
    if not Path(path).exists():
        raise FileNotFoundError(
            f"no socket at {path} — nothing listening to activate "
            "(the socket unit is not enabled, or no runtime is installed)"
        )
    sock = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    try:
        sock.settimeout(min(float(timeout_s), 5.0))
        sock.connect(path)
    finally:
        sock.close()
    return f"connected to {path} — systemd socket activation was triggered"


def _default_sleep(seconds: float) -> None:
    """The readiness poll's waiter (injectable, so the state machine is testable)."""
    time.sleep(seconds)


def await_ready(
    prober: Callable[..., RuntimeProbe],
    *,
    timeout_s: float,
    deadline_s: float,
    docker_bin: str | None = None,
    environ: dict[str, str] | None = None,
    interval_s: float = ACTIVATION_POLL_S,
    sleep: Callable[[float], None] | None = None,
) -> tuple[RuntimeProbe, int, int]:
    """Poll a just-activated runtime until it answers or the budget is spent.

    Returns ``(probe, readiness_ms, polls)``. The first probe is immediate — a
    daemon that is already up costs nothing — and the loop is bounded by
    ``deadline_s``, so a socket that activates nothing cannot hang a caller. The
    commands of *every* poll are not recorded: only the last probe's commands are
    (the report would otherwise repeat one line 25 times).
    """
    waiter = sleep or _default_sleep
    budget = max(0.0, float(deadline_s))
    probe_timeout = max(MIN_TIMEOUT_S, min(float(timeout_s), max(1.0, budget) or 1.0))
    started = time.monotonic()
    polls = 0
    while True:
        probe = prober(timeout_s=probe_timeout, docker_bin=docker_bin, environ=environ)
        polls += 1
        if probe.reachable:
            return probe, int((time.monotonic() - started) * 1000), polls
        if (time.monotonic() - started) >= budget:
            return probe, int((time.monotonic() - started) * 1000), polls
        waiter(float(interval_s))


# ------------------------------------------------------------------- reports


def _unavailable(
    *,
    docker_probe: RuntimeProbe,
    podman_probe: RuntimeProbe | None,
    attempts: tuple[Attempt, ...],
    socket: str,
    socket_exists: bool,
    paths: tuple[str, ...],
    timeout_s: float,
    notes: tuple[str, ...],
    started: float,
) -> EnsureReport:
    """The honest blocked report: what was measured, what to run, and nothing claimed."""
    pieces = [f"docker: {docker_probe.detail}"]
    if podman_probe is not None:
        pieces.append(f"podman: {podman_probe.detail}")
    commands: list[str] = list(docker_probe.commands)
    if podman_probe is not None:
        commands.extend(podman_probe.commands)
    for attempt in attempts:
        commands.extend(attempt.commands)
    return EnsureReport(
        state=STATE_UNAVAILABLE,
        provider="",
        answered_by="",
        reason="; ".join(pieces),
        detail="no container runtime answered, and no password-free start path brought one up",
        exact_command=PRIMARY_EXACT_COMMAND,
        remediation=REMEDIATION,
        attempts=attempts,
        commands=tuple(commands),
        container_ops_attempted=sum(
            1 for command in commands if not _is_probe_command(command)
        ),
        socket=socket,
        socket_exists=bool(socket_exists),
        docker_bin=docker_probe.binary,
        podman_bin=podman_probe.binary if podman_probe is not None else "",
        paths=paths,
        timeout_s=float(timeout_s),
        duration_ms=int((time.monotonic() - started) * 1000),
        probed_at=_now_iso(),
        notes=notes,
    )


def _is_probe_command(command: str) -> bool:
    """True when a *recorded* command line is a probe (or a socket connect)."""
    if not command:
        return True
    if command.startswith("connect("):
        return True
    try:
        argv = shlex.split(command)
    except ValueError:  # pragma: no cover — a recorded line is well-formed
        return False
    verb = argv[1] if len(argv) > 1 else ""
    return verb in ALLOWED_VERBS


def _running(
    probe: RuntimeProbe,
    *,
    answered_by: str,
    attempts: tuple[Attempt, ...],
    commands: tuple[str, ...],
    socket: str,
    socket_exists: bool,
    paths: tuple[str, ...],
    timeout_s: float,
    notes: tuple[str, ...],
    started: float,
    podman_bin: str = "",
) -> EnsureReport:
    return EnsureReport(
        state=STATE_RUNNING,
        provider=probe.provider,
        answered_by=answered_by,
        server_version=probe.server_version,
        storage_driver=probe.storage_driver,
        rootless=probe.rootless,
        reason="",
        detail=probe.detail,
        exact_command="",
        remediation=(),
        attempts=attempts,
        commands=commands,
        container_ops_attempted=sum(
            1 for command in commands if not _is_probe_command(command)
        ),
        socket=socket,
        socket_exists=bool(socket_exists),
        docker_bin=probe.binary if probe.provider == PROVIDER_DOCKER else "",
        # the winner's own binary, else the podman resolved on this host — the
        # fact is never erased because docker answered first (audit 62 #8)
        podman_bin=(
            probe.binary if probe.provider == PROVIDER_PODMAN else podman_bin
        ),
        paths=paths,
        timeout_s=float(timeout_s),
        duration_ms=int((time.monotonic() - started) * 1000),
        probed_at=_now_iso(),
        notes=notes,
    )


def _socket_facts(socket: str) -> tuple[bool, tuple[str, ...]]:
    """Whether the socket file is there, and the honest note when it is not."""
    exists = Path(socket).exists()
    if exists:
        return True, ()
    host = docker_host()
    note = f"no socket file at {socket}"
    if host.startswith("tcp://") or host.startswith("ssh://"):
        note = f"DOCKER_HOST={host} is not a unix socket — there is no local socket to activate"
    return False, (note,)


# ------------------------------------------------------------------ the state machine


def runtime_status(
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    docker_bin: str | None = None,
    podman_bin: str | None = None,
    environ: dict[str, str] | None = None,
    prober: Callable[..., RuntimeProbe] | None = None,
    podman_prober: Callable[..., RuntimeProbe] | None = None,
) -> EnsureReport:
    """**Read-only** status: which runtime answers right now. Never starts anything.

    Docker first (the primary), then rootless podman (the declared fallback).
    This is what ``--no-start`` and the ``skaldir selftest`` probe call, so both
    keep their read-only promise.
    """
    started = time.monotonic()
    bounded = clamp_timeout(timeout_s)
    docker_prober = prober or probe_docker
    podman_prober_ = podman_prober or probe_podman
    socket = docker_socket_path()
    socket_exists, notes = _socket_facts(socket)

    docker = docker_prober(timeout_s=bounded, docker_bin=docker_bin, environ=environ)
    if docker.reachable:
        return _running(
            docker,
            answered_by=PATH_PROBE,
            attempts=(),
            commands=docker.commands,
            socket=socket,
            socket_exists=socket_exists,
            paths=(),
            timeout_s=bounded,
            notes=notes,
            started=started,
            podman_bin=resolved_podman_bin(podman_bin),
        )
    podman = podman_prober_(timeout_s=bounded, podman_bin=podman_bin, environ=environ)
    if podman.reachable:
        podman_note = notes + (
            f"docker is not reachable ({docker.detail}); {PROVIDER_PODMAN} answered instead",
        )
        return _running(
            podman,
            answered_by=PATH_ROOTLESS_PODMAN,
            attempts=(),
            commands=tuple(docker.commands) + tuple(podman.commands),
            socket=socket,
            socket_exists=socket_exists,
            paths=(),
            timeout_s=bounded,
            notes=podman_note,
            started=started,
        )
    return _unavailable(
        docker_probe=docker,
        podman_probe=podman,
        attempts=(),
        socket=socket,
        socket_exists=socket_exists,
        paths=(),
        timeout_s=bounded,
        notes=notes,
        started=started,
    )


def ensure(
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    paths: tuple[str, ...] | list[str] | None = None,
    start: bool = True,
    docker_bin: str | None = None,
    podman_bin: str | None = None,
    environ: dict[str, str] | None = None,
    prober: Callable[..., RuntimeProbe] | None = None,
    podman_prober: Callable[..., RuntimeProbe] | None = None,
    activator: Callable[..., Attempt] | None = None,
    ready_s: float = ACTIVATION_READY_S,
    sleep: Callable[[float], None] | None = None,
) -> EnsureReport:
    """Bring the container runtime up through password-free paths, or report blocked.

    Order, and nothing else:

    1. **probe** the docker daemon (bounded) — if it answers, we are done;
    2. for each declared ``path`` (:data:`DEFAULT_START_PATHS` by default);
       :data:`PATH_SOCKET_ACTIVATION` connects to the socket, then polls the
       daemon for at most ``ready_s`` (a freshly activated daemon is *starting*,
       not started), and :data:`PATH_ROOTLESS_PODMAN` probes rootless podman;
    3. nothing answered ⇒ ``state="unavailable"`` with ``reason``,
       ``exact_command`` and :data:`EXIT_BLOCKED`.

    ``start=False`` is exactly :func:`runtime_status`: probe only, no start path.
    Every prober/activator/waiter is injectable, which is how the state machine is
    unit-tested without a daemon.
    """
    if not start:
        return runtime_status(
            timeout_s=timeout_s,
            docker_bin=docker_bin,
            podman_bin=podman_bin,
            environ=environ,
            prober=prober,
            podman_prober=podman_prober,
        )
    started = time.monotonic()
    bounded = clamp_timeout(timeout_s)
    docker_prober = prober or probe_docker
    podman_prober_ = podman_prober or probe_podman
    activator_ = activator or activate_socket
    declared = tuple(paths) if paths is not None else DEFAULT_START_PATHS
    socket = docker_socket_path()
    socket_exists, notes = _socket_facts(socket)
    note_list = list(notes)

    docker = docker_prober(timeout_s=bounded, docker_bin=docker_bin, environ=environ)
    if docker.reachable:
        return _running(
            docker,
            answered_by=PATH_PROBE,
            attempts=(),
            commands=docker.commands,
            socket=socket,
            socket_exists=socket_exists,
            paths=declared,
            timeout_s=bounded,
            notes=tuple(note_list),
            started=started,
            podman_bin=resolved_podman_bin(podman_bin),
        )

    attempts: list[Attempt] = []
    commands: list[str] = list(docker.commands)
    if declared:
        note_list.append(
            f"docker did not answer first ({docker.detail}) — trying the declared paths: "
            f"{', '.join(declared)}"
        )
    else:
        note_list.append(
            f"docker did not answer first ({docker.detail}) — no start path was declared, "
            "so nothing was attempted"
        )
    podman: RuntimeProbe | None = None

    for path in declared:
        if path == PATH_SOCKET_ACTIVATION:
            attempt = activator_(socket_path=socket, timeout_s=bounded)
            attempts.append(attempt)
            commands.extend(attempt.commands)
            if not attempt.ok:
                note_list.append(f"{PATH_SOCKET_ACTIVATION}: {attempt.detail}")
                continue
            probed, readiness_ms, polls = await_ready(
                docker_prober,
                timeout_s=bounded,
                deadline_s=min(bounded, ready_s),
                docker_bin=docker_bin,
                environ=environ,
                sleep=sleep,
            )
            commands.extend(probed.commands)
            attempts[-1] = replace(
                attempt,
                detail=(
                    f"{attempt.detail}; the daemon answered after {readiness_ms} ms "
                    f"({polls} probe(s))"
                ),
                commands=attempt.commands + probed.commands,
                duration_ms=attempt.duration_ms + readiness_ms,
                readiness_ms=readiness_ms,
                polls=polls,
            )
            if probed.reachable:
                note_list.append(
                    f"{PATH_SOCKET_ACTIVATION} started the daemon and it answered after "
                    f"{readiness_ms} ms"
                )
                return _running(
                    probed,
                    answered_by=PATH_SOCKET_ACTIVATION,
                    attempts=tuple(attempts),
                    commands=tuple(commands),
                    socket=socket,
                    socket_exists=socket_exists,
                    paths=declared,
                    timeout_s=bounded,
                    notes=tuple(note_list),
                    started=started,
                    podman_bin=resolved_podman_bin(podman_bin),
                )
            note_list.append(
                f"{PATH_SOCKET_ACTIVATION} did not bring a daemon up within "
                f"{min(bounded, ready_s):.1f}s ({probed.detail})"
            )
        elif path == PATH_ROOTLESS_PODMAN:
            podman = podman_prober_(timeout_s=bounded, podman_bin=podman_bin, environ=environ)
            commands.extend(podman.commands)
            attempt = Attempt(
                path=PATH_ROOTLESS_PODMAN,
                ok=podman.reachable,
                detail=podman.detail,
                commands=podman.commands,
                duration_ms=podman.duration_ms,
            )
            attempts.append(attempt)
            if podman.reachable:
                note_list.append(f"{PROVIDER_PODMAN} answered — docker stayed unreachable")
                return _running(
                    podman,
                    answered_by=PATH_ROOTLESS_PODMAN,
                    attempts=tuple(attempts),
                    commands=tuple(commands),
                    socket=socket,
                    socket_exists=socket_exists,
                    paths=declared,
                    timeout_s=bounded,
                    notes=tuple(note_list),
                    started=started,
                )
        else:  # pragma: no cover — the CLI validates the list before it gets here
            raise ValueError(f"unknown start path {path!r} (known: {KNOWN_PATHS})")

    return _unavailable(
        docker_probe=docker,
        podman_probe=podman,
        attempts=tuple(attempts),
        socket=socket,
        socket_exists=socket_exists,
        paths=declared,
        timeout_s=bounded,
        notes=tuple(note_list),
        started=started,
    )


# ------------------------------------------------------------------ selftest probe


def selftest_probe(*, timeout_s: float = SELFTEST_TIMEOUT_S) -> tuple[bool, str, str]:
    """The ``skaldir selftest`` probe: read-only, bounded, honest.

    Returns ``_probe``'s own three-tuple ``(ok, detail, evidence_ref)``. It never
    starts anything (``skaldir selftest`` promises a read-only report), so a host
    with a down daemon reports ``blocked`` plus the exact command — never a PASS
    and never a silent skip.
    """
    report = runtime_status(timeout_s=timeout_s)
    if report.ok:
        detail = (
            f"{report.provider} {report.server_version or '?'} reachable via "
            f"{report.answered_by} (storage driver {report.storage_driver or 'unknown'})"
        )
        return True, detail, EVIDENCE_REF
    detail = (
        f"no container runtime answered — {report.reason}; "
        f"the owner's next step: {report.exact_command}"
    )
    return False, detail, EVIDENCE_REF


def install_selftest_probe(contract: Any = None) -> dict[str, Any]:
    """Hook: append this capability's probe to the product CLI's EXPAND layer probes.

    Why a hook and not an edit: the probe table lives in ``cli_contract``, which
    has a single writer this round — so this capability *installs* its probe
    instead of touching that file. ``register_cli``'s wiring in ``cli.py`` is
    free to call this next to it::

        from .docker_ensure import install_selftest_probe   # +
        install_selftest_probe()                            # +

    It replaces ``cli_contract._PROBES["EXPAND"]`` with a wrapper that runs the
    layer's own probers first and then :func:`selftest_probe`, so
    ``skaldir selftest --json`` reports the container runtime. Idempotent (the
    wrapper carries a marker), and it returns what it did instead of assuming.
    Returns ``{"installed": bool, "already": bool, "probe": …, "layer": …}``.
    """
    module = contract if contract is not None else _cli_contract()
    if module is None:
        return {"installed": False, "already": False, "reason": "no cli_contract module"}
    probes = getattr(module, "_PROBES", None)
    if not isinstance(probes, dict) or "EXPAND" not in probes:
        return {
            "installed": False,
            "already": False,
            "reason": "the probe table has no EXPAND layer",
        }
    original = probes["EXPAND"]
    if getattr(original, _PROBE_MARKER, False):
        return {
            "installed": True,
            "already": True,
            "layer": "EXPAND",
            "probe": SELFTEST_PROBE_NAME,
        }

    @functools.wraps(original)
    def wrapped(runtime: Any, _original: Any = original) -> tuple[bool, str, list[Any]]:
        ok, blocked_reason, collected = _original(runtime)
        probe = module._probe(  # noqa: SLF001 — the layer's own probe factory
            SELFTEST_PROBE_NAME, selftest_probe, blocked=True, evidence_kind="code"
        )
        gathered = list(collected) + [probe]
        if ok and probe.ok:
            return True, "", gathered
        parts = [str(blocked_reason or "")] if not ok else []
        if not probe.ok:
            parts.append(f"{SELFTEST_PROBE_NAME}: {probe.detail}")
        return False, " ".join(piece for piece in parts if piece), gathered

    setattr(wrapped, _PROBE_MARKER, True)
    probes["EXPAND"] = wrapped
    return {
        "installed": True,
        "already": False,
        "layer": "EXPAND",
        "probe": SELFTEST_PROBE_NAME,
        "replaces": "cli_contract._PROBES['EXPAND']",
    }


def _cli_contract() -> Any:
    try:
        from . import cli_contract
    except Exception:  # noqa: BLE001 — a build without the product CLI
        return None
    return cli_contract


__all__ = [
    "ACTIVATION_POLL_S",
    "ACTIVATION_READY_S",
    "ALLOWED_VERBS",
    "Attempt",
    "DEFAULT_DOCKER_SOCKET",
    "DEFAULT_START_PATHS",
    "DEFAULT_TIMEOUT_S",
    "EVIDENCE_REF",
    "EXIT_BLOCKED",
    "EXIT_OK",
    "EnsureReport",
    "ExecResult",
    "KNOWN_PATHS",
    "MAX_TIMEOUT_S",
    "MIN_TIMEOUT_S",
    "PATH_PROBE",
    "PATH_ROOTLESS_PODMAN",
    "PATH_SOCKET_ACTIVATION",
    "PRIMARY_EXACT_COMMAND",
    "PRIVILEGED_BINARIES",
    "PROVIDER_DOCKER",
    "PROVIDER_PODMAN",
    "REMEDIATION",
    "RuntimeProbe",
    "SELFTEST_PROBE_NAME",
    "SELFTEST_TIMEOUT_S",
    "STATE_RUNNING",
    "STATE_UNAVAILABLE",
    "ContainerOperationRefused",
    "PrivilegedStartRefused",
    "activate_socket",
    "await_ready",
    "clamp_timeout",
    "docker_binary",
    "docker_host",
    "docker_socket_path",
    "ensure",
    "guard_argv",
    "install_selftest_probe",
    "podman_binary",
    "resolved_podman_bin",
    "probe_docker",
    "probe_podman",
    "run_argv",
    "runtime_status",
    "selftest_probe",
]
