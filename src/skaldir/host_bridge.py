"""``skaldir host`` — the host bridge: the HOST's own native features, read-only.

WHAT THIS IS
------------
The Skaldir CLI keeps its own independent core (runtime dir, tools DB, ledger,
treasury). This module is the one place where that core reaches *out* to the
installed host — the Hermes install that ``hermes`` and the desktop app belong
to — and reads the host's own native features back:

    status · sessions · memory/session search · skills · cron · toolsets

Read-only first: every call in this module is a ``GET`` and the route whitelist
:data:`READ_ONLY_ROUTES` makes that mechanical, not a promise. Nothing here
writes to the host's config, its stores or its processes; the CLI can *drive*
the host only through the host's own commands (see the doc's write-scope note).

HOW IT REACHES THE HOST — two sanctioned transports, no internals
-----------------------------------------------------------------
1. **HTTP: the host's own backend** — ``hermes serve``, the loopback JSON gateway
   the desktop app also drives (``hermes serve --host 127.0.0.1 --port 0``). Every
   route used here is a documented ``/api/*`` route of that backend and answers
   JSON. On a loopback bind the backend hands the SPA a session token
   (``window.__HERMES_SESSION_TOKEN__`` in ``GET /``); sensitive routes take it in
   ``X-Hermes-Session-Token``. ``/api/health`` and ``/api/status`` are public —
   they are the host's own uptime probes — so ``host status`` needs no token.
2. **The host's own CLI** — ``hermes --version`` as a subprocess, for installation
   facts only. It is never used to scrape feature data: where the host exposes no
   machine-readable surface, the verb reports *not reachable* instead of parsing
   human text. The one deliberate exception is ``host sessions --via cli``, which
   is opt-in and marks its payload ``"fragile": true``.

There is deliberately **no import of any hermes package** here. The host's home is
read from ``$HERMES_HOME`` (with the standard default) instead of asking
``hermes_constants``, and the binary is resolved through this package's existing
resolver (:func:`skaldir.mcp_link.hermes_binary`, which owns the host-CLI lookup).
That keeps the bridge working against a host whose Python packages this process
cannot import at all — a different venv, a different version.

WHAT IS *NOT* REACHABLE FROM HERE (measured, 2026-09-18, host 0.21.3)
--------------------------------------------------------------------
* ``hermes serve --status`` cannot see a ``--port 0`` serve (it skips ``port <= 0``),
  so it is evidence *about* the host, never the way this bridge finds a backend.
  Discovery reads ``/proc`` instead (see :func:`discover_backends`).
* The bare ``GET /api/sessions`` (no ``profile``) reads the *backend process's own*
  store. The desktop app spawns its backend with its own ``hermes-home``
  (``~/.hermes/godsv/desktop-userdata/hermes-home/state.db`` — zero sessions), so
  the bare call returns an empty list while ``?profile=default`` returns the real
  machine-level store. Every verb here therefore names the profile it read.
* No write verbs. Sending a message, running a job, installing a skill: out of
  scope for this block by decision, not by inability.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .env import getenv, host_getenv

__all__ = [
    "Backend",
    "Discovery",
    "HostBlocked",
    "HostError",
    "HostNeedsSetup",
    "READ_ONLY_ROUTES",
    "SESSION_HEADER",
    "cron_jobs",
    "discover_backends",
    "doctor",
    "hermes_binary",
    "host_home",
    "host_version",
    "http_get",
    "memory_search",
    "resolve_backend",
    "sessions_via_cli",
    "skills",
    "status",
    "toolsets",
]

# --------------------------------------------------------------------------- #
# exit codes (the product contract's numbers, restated — the CLI maps them)     #
# --------------------------------------------------------------------------- #
#: The bridge is standalone: it must not import ``cli_contract`` (that module
#: belongs to the CLI's centre). These are the same numbers, named once here.
EXIT_OK = 0
EXIT_BLOCKED = 3
EXIT_NEEDS_SETUP = 4
EXIT_INTERNAL = 7

# --------------------------------------------------------------------------- #
# the host's surface: routes we are allowed to call, and only these             #
# --------------------------------------------------------------------------- #
#: ``path -> what it answers``. :func:`http_get` refuses anything else *and*
#: refuses any method but GET, so "read-only" cannot rot into a promise.
READ_ONLY_ROUTES: dict[str, str] = {
    "/api/health": "process liveness + version (public: no token)",
    "/api/status": "version, gateway state, components (public: no token)",
    "/api/sessions": "the host's session store, page by page",
    "/api/sessions/search": "FTS5 search over the host's session/message index",
    "/api/skills": "the host's installed skills",
    "/api/cron/jobs": "the host's scheduled jobs",
    "/api/tools/toolsets": "the host's toolsets and their tools",
    "/api/memory": "the host's memory provider state + built-in file sizes",
    "/api/profiles": "the host's profiles (names, homes, counts)",
    "/api/logs": "the host's own log tail",
}

#: The header the loopback backend takes its session token in.
SESSION_HEADER = "X-Hermes-Session-Token"
#: The SPA variable that carries the same token in ``GET /``.
SPA_TOKEN_RE = re.compile(r'__HERMES_SESSION_TOKEN__="([^"]+)"')
#: ``Hermes Agent v0.21.3 (2026.9.14) · upstream fb56a7e0``
VERSION_RE = re.compile(r"Hermes Agent v([0-9][^\s(]+)")

#: Point the bridge at an explicit backend when discovery is not wanted (tests,
#: remote/other setups, a serve on a fixed port).
ENV_URL = "SKALDIR_HOST_URL"
ENV_TOKEN = "SKALDIR_HOST_TOKEN"

DEFAULT_TIMEOUT = 8.0
PROBE_TIMEOUT = 3.0


class HostError(Exception):
    """A bridge verdict the CLI turns into an exit code and one error record."""

    exit_code = EXIT_INTERNAL
    retryable = False

    def __init__(self, reason: str, *, hint: str = "", details: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = str(reason)
        self.hint = str(hint)
        self.details = dict(details or {})


class HostNeedsSetup(HostError):
    """The host is not installed here — nothing to bridge to (exit 4)."""

    exit_code = EXIT_NEEDS_SETUP


class HostBlocked(HostError):
    """The host is installed but this feature cannot be read right now (exit 3)."""

    exit_code = EXIT_BLOCKED


# --------------------------------------------------------------------------- #
# the host itself: is it installed, which version, which home                    #
# --------------------------------------------------------------------------- #
def hermes_binary(explicit: str | None = None) -> str | None:
    """The host CLI, or ``None`` when Hermes is not installed on this machine.

    Reuses this package's existing resolver (``skaldir.mcp_link``) — a fourth copy
    of the ``~/.local/bin`` fallback list is exactly the drift the shared helpers
    exist to prevent.
    """
    if explicit:
        return str(explicit)
    try:
        from .mcp_link import hermes_binary as _resolve_host_cli

        found = _resolve_host_cli()
        if found:
            return str(found)
    except Exception:  # noqa: BLE001 — a resolver must never break the bridge
        pass
    return shutil.which("hermes")


def host_home() -> Path:
    """The host's home directory, read from the environment (no host import)."""
    explicit = str(host_getenv("HERMES_HOME", "") or "").strip()
    return Path(explicit).expanduser() if explicit else Path.home() / ".hermes"


def host_version(binary: str | None = None, *, timeout: float = 10.0) -> dict:
    """``hermes --version`` — the install's own answer, parsed for the number.

    Returns ``{"installed", "binary", "version", "raw", "source"}``. The version
    string is the one thing read from CLI *text*: the host exposes no
    machine-readable version outside the backend (which answers
    ``/api/health.version`` when it runs), and a version banner is stable enough
    to parse while it is still reported as ``source: "cli-text"``.
    """
    resolved = hermes_binary(binary)
    if not resolved:
        return {
            "installed": False,
            "binary": None,
            "version": None,
            "raw": "",
            "source": "none",
            "error": "no `hermes` binary found (PATH, ~/.local/bin, the install venv)",
        }
    try:
        proc = subprocess.run(  # nosec B603 — fixed argv, read-only version banner
            [resolved, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "installed": True,
            "binary": resolved,
            "version": None,
            "raw": "",
            "source": "cli-text",
            "error": f"{type(exc).__name__}: {exc}",
        }
    raw = (proc.stdout or "") + (proc.stderr or "")
    match = VERSION_RE.search(raw)
    return {
        "installed": True,
        "binary": resolved,
        "version": match.group(1) if match else None,
        "raw": raw.strip().splitlines()[0] if raw.strip() else "",
        "source": "cli-text",
        "exit_code": int(proc.returncode),
    }


# --------------------------------------------------------------------------- #
# discovery: where is the host's loopback backend? (/proc, no psutil)            #
# --------------------------------------------------------------------------- #
def _proc_argv(pid: str | int) -> list[str]:
    """The argv of a process, or ``[]`` when it is gone or not ours to read."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [part for part in raw.decode("utf-8", "replace").split("\0") if part]


def _proc_environ(pid: str | int) -> dict[str, str]:
    """The environment of a process we own (``{}`` when unreadable)."""
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {}
    env: dict[str, str] = {}
    for entry in raw.decode("utf-8", "replace").split("\0"):
        if "=" in entry:
            key, _, value = entry.partition("=")
            env[key] = value
    return env


def _serve_subcommand(argv: Sequence[str]) -> str:
    """``serve`` / ``dashboard`` when argv really launches one, else ``""``.

    Handles both shapes on this machine: the console script
    (``/path/hermes serve …``) and the venv entry point
    (``/path/venv/bin/python /path/hermes serve …``) — so the check is "the first
    argv element whose basename is ``hermes`` / ``hermes.py``, then its successor".
    """
    for index, item in enumerate(argv):
        name = os.path.basename(str(item))
        if name in ("hermes", "hermes.py") or name.startswith("hermes."):
            following = argv[index + 1] if index + 1 < len(argv) else ""
            if following in ("serve", "dashboard", "gui", "desktop"):
                return following
            return ""
    return ""


def _argv_port(argv: Sequence[str]) -> int:
    """``--port N`` as written on the command line (``0`` = OS-assigned)."""
    for index, item in enumerate(argv):
        text = str(item)
        if text == "--port" and index + 1 < len(argv):
            try:
                return int(argv[index + 1])
            except (TypeError, ValueError):
                return 0
        if text.startswith("--port="):
            try:
                return int(text.split("=", 1)[1])
            except ValueError:
                return 0
    return 0


def _listening_ports(pid: str | int) -> list[int]:
    """Every TCP port this process is listening on (via ``/proc/net``).

    Reads the process's socket inodes, then matches them against the kernel's
    listening entries — no ``ss``, no psutil, no new dependency.
    """
    inodes: set[str] = set()
    fd_dir = Path(f"/proc/{pid}/fd")
    try:
        names = os.listdir(fd_dir)
    except OSError:
        return []
    for name in names:
        try:
            target = os.readlink(fd_dir / name)
        except OSError:
            continue
        if target.startswith("socket:["):
            inodes.add(target[len("socket:[") : -1])
    if not inodes:
        return []

    ports: list[int] = []
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":  # 0A = LISTEN
                continue
            if fields[9] not in inodes:
                continue
            address, _, port_hex = fields[1].rpartition(":")
            try:
                port = int(port_hex, 16)
            except ValueError:
                continue
            # Loopback binds only: the bridge never reaches a public surface.
            if address.upper().endswith("0100007F") or set(address) == {"0"}:
                ports.append(port)
    return sorted(set(ports))


def _probe_health(url: str, *, timeout: float = PROBE_TIMEOUT) -> dict | None:
    """``GET {url}/api/health`` — the host's own liveness probe, public route."""
    try:
        response = httpx.get(f"{url.rstrip('/')}/api/health", timeout=timeout)
        if response.status_code != 200:
            return None
        payload = response.json()
    except Exception:  # noqa: BLE001 — a dead candidate is data, not an error
        return None
    if not isinstance(payload, dict) or "version" not in payload:
        return None
    return payload


def _read_token(pid: str | int, url: str, *, timeout: float = PROBE_TIMEOUT) -> tuple[str, str]:
    """The backend's loopback session token and where it came from.

    Order: the process environment (the desktop app injects
    ``HERMES_DASHBOARD_SESSION_TOKEN``), then the SPA document at ``/`` — the
    channel the backend itself hands the token to loopback clients.
    """
    token = _proc_environ(pid).get("HERMES_DASHBOARD_SESSION_TOKEN", "").strip()
    if token:
        return token, "proc-environ"
    try:
        response = httpx.get(f"{url.rstrip('/')}/", timeout=timeout)
        match = SPA_TOKEN_RE.search(response.text or "")
    except Exception:  # noqa: BLE001 — no token is reported, never guessed
        return "", "none"
    return (match.group(1), "spa-html") if match else ("", "none")


@dataclass(frozen=True)
class Backend:
    """One reachable host backend: where it is, which process, which token."""

    url: str
    host: str = "127.0.0.1"
    port: int = 0
    pid: int | None = None
    mode: str = ""
    version: str = ""
    auth_required: bool | None = None
    token: str = ""
    token_source: str = "none"
    source: str = "proc-scan"
    desktop: bool = False
    announced_port: int = 0

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "host": self.host,
            "port": self.port,
            "pid": self.pid,
            "mode": self.mode,
            "version": self.version or None,
            "auth_required": self.auth_required,
            "token_available": bool(self.token),
            "token_source": self.token_source,
            "discovered_by": self.source,
            "desktop_owned": self.desktop,
            "announced_port": self.announced_port,
        }


@dataclass
class Discovery:
    """What the scan found, including what it could not look at."""

    method: str = "none"
    backends: list[Backend] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "backend_count": len(self.backends),
            "candidates": self.candidates,
            "notes": self.notes,
        }


def _proc_root() -> Path:
    """The process filesystem this scan reads — a seam, so tests can replace it.

    Reading ``/proc`` is the one platform-specific part of discovery; keeping it
    behind this function lets the suite exercise the real scanning code against a
    throwaway directory instead of the developer's running processes.
    """
    return Path("/proc")


def discover_backends(*, timeout: float = PROBE_TIMEOUT) -> Discovery:
    """Find the host's loopback backend(s) without touching the host.

    Linux/``/proc`` scan of running ``hermes serve`` / ``hermes dashboard``
    processes, their exec'd argv, and the listening ports their sockets own; each
    candidate is then confirmed with the host's own public ``/api/health``. On a
    platform without ``/proc`` this returns an empty discovery with a note — the
    caller then needs ``--url`` (that is the honest answer, not a guess).
    """
    found = Discovery(method="proc-scan")
    proc = _proc_root()
    if not proc.exists():
        found.method = "unsupported"
        found.notes.append(
            "no /proc on this platform: pass --url (or set SKALDIR_HOST_URL) — "
            "the bridge does not guess a port"
        )
        return found
    try:
        pids = [name for name in os.listdir(proc) if name.isdigit()]
    except OSError as exc:
        found.method = "failed"
        found.notes.append(f"cannot read /proc: {type(exc).__name__}: {exc}")
        return found

    for pid in pids:
        argv = _proc_argv(pid)
        if not argv:
            continue
        mode = _serve_subcommand(argv)
        if not mode:
            continue
        env = _proc_environ(pid)
        for port in _listening_ports(pid):
            url = f"http://127.0.0.1:{port}"
            health = _probe_health(url, timeout=timeout)
            candidate = {
                "pid": int(pid),
                "port": port,
                "url": url,
                "mode": mode,
                "announced_port": _argv_port(argv),
                "answers": bool(health),
            }
            found.candidates.append(candidate)
            if not health:
                continue
            token, token_source = _read_token(pid, url, timeout=timeout)
            found.backends.append(
                Backend(
                    url=url,
                    port=port,
                    pid=int(pid),
                    mode=mode,
                    version=str(health.get("version") or ""),
                    auth_required=bool(health.get("auth_required")) if health else None,
                    token=token,
                    token_source=token_source,
                    source="proc-scan",
                    desktop=bool(env.get("HERMES_DESKTOP_APP_NAME")),
                    announced_port=_argv_port(argv),
                )
            )
    # A desktop-owned backend is the one the user is actually looking at.
    found.backends.sort(key=lambda item: (not item.desktop, item.port))
    if not found.candidates:
        found.notes.append("no running `hermes serve`/`hermes dashboard` process found")
    return found


def _from_env(token: str = "", url: str = "") -> Backend | None:
    """An explicit backend from the environment / flags (takes precedence)."""
    explicit = (url or getenv(ENV_URL, "") or "").strip()
    if not explicit:
        return None
    env_token = (token or getenv(ENV_TOKEN, "") or "").strip()
    health = _probe_health(explicit)
    if health is None:
        raise HostBlocked(
            f"no host backend answers at {explicit}",
            hint="check the port (`hermes serve --status` cannot see --port 0 serves) "
                 "or drop --url to let the bridge scan /proc",
            details={"url": explicit},
        )
    return Backend(
        url=explicit,
        port=int(explicit.rsplit(":", 1)[-1]) if ":" in explicit else 0,
        mode="(explicit)",
        version=str(health.get("version") or ""),
        auth_required=bool(health.get("auth_required")),
        token=env_token,
        token_source="flag" if token else ("env" if env_token else "none"),
        source="url-flag",
    )


def resolve_backend(
    *, url: str = "", token: str = "", timeout: float = PROBE_TIMEOUT
) -> tuple[Backend, Discovery]:
    """The backend to drive: an explicit one, else the best-discovered one.

    Raises :class:`HostBlocked` when the host is installed but nothing answers,
    with the raw scan as the evidence.
    """
    explicit = _from_env(token=token, url=url)
    if explicit is not None:
        return explicit, Discovery(method="url-flag", notes=["backend given explicitly"])
    discovery = discover_backends(timeout=timeout)
    if discovery.backends:
        return discovery.backends[0], discovery
    raise HostBlocked(
        "the host is installed but no loopback backend is answering",
        hint="start the host: the desktop app (Skaldir) or `hermes serve`",
        details=discovery.as_dict(),
    )


# --------------------------------------------------------------------------- #
# the one HTTP door: GET only, whitelisted routes, honest non-2xx               #
# --------------------------------------------------------------------------- #
def _assert_read_only(path: str, method: str = "GET") -> None:
    if method != "GET":
        raise ValueError(f"the host bridge is read-only: {method} {path} refused")
    if path not in READ_ONLY_ROUTES:
        raise ValueError(
            f"{path} is not a whitelisted read route — add it to READ_ONLY_ROUTES "
            "with its documented meaning, or it does not get called"
        )


def http_get(
    backend: Backend,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """One read call against the host backend. Never raises for a bad status.

    Returns ``{"path", "url", "status", "ok", "json", "text", "error"}`` so a 401
    is reported as a 401 (with the route's own detail message) instead of being
    flattened into "unreachable".
    """
    _assert_read_only(path, "GET")
    url = f"{backend.url.rstrip('/')}{path}"
    headers = {SESSION_HEADER: backend.token} if backend.token else {}
    result: dict[str, Any] = {
        "path": path,
        "url": url,
        "status": 0,
        "ok": False,
        "json": None,
        "text": "",
        "error": "",
        "token_source": backend.token_source,
    }
    try:
        response = httpx.get(url, params=params or None, headers=headers, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — connection failure is data here
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    result["status"] = int(response.status_code)
    result["text"] = (response.text or "")[:4000]
    if response.status_code < 400:
        try:
            result["json"] = response.json()
            result["ok"] = True
        except Exception:  # noqa: BLE001
            result["error"] = "the route answered non-JSON on a JSON surface"
    else:
        detail = ""
        try:
            detail = str(response.json().get("detail") or "")
        except Exception:  # noqa: BLE001
            detail = result["text"][:200]
        result["error"] = f"HTTP {response.status_code}: {detail}".strip()
    return result


def _need(
    backend: Backend, path: str, params: dict[str, Any] | None = None,
    *, timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """``http_get`` for a feature call: a non-2xx becomes the honest blocked reason."""
    result = http_get(backend, path, params, timeout=timeout)
    if result["ok"]:
        return result["json"]
    if result["status"] in (401, 403):
        raise HostBlocked(
            f"{path} refused the loopback token ({result['error']})",
            hint="the bridge reads the token from the backend's own process "
                 "environment or its SPA document at /; pass --token when neither is readable",
            details={
                "path": path, "status": result["status"],
                "token_source": backend.token_source,
            },
        )
    raise HostBlocked(
        f"{path} answered nothing usable ({result['error'] or 'no answer'})",
        hint=f"the host backend at {backend.url} did not answer this route",
        details={"path": path, "status": result["status"], "url": backend.url},
    )


# --------------------------------------------------------------------------- #
# the features                                                                  #
# --------------------------------------------------------------------------- #
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _target(backend: Backend, path: str, params: dict[str, Any] | None = None) -> dict:
    """The two audit fields every feature payload carries: which host, which request.

    A reader of the envelope must be able to tell *which* backend answered and
    *what* was asked — that is what makes the bridge auditable instead of magic.
    """
    query = "&".join(f"{key}={value}" for key, value in (params or {}).items())
    return {
        "backend": backend.as_dict(),
        "request": f"GET {path}" + (f"?{query}" if query else ""),
    }


def status(
    *, url: str = "", token: str = "", binary: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """Is a host installed, and is one running? — the honest answer either way.

    Never raises: the caller decides the exit code from ``installed`` / ``running``
    (4 needs-setup when nothing is installed, 3 blocked when it is installed but
    no backend answers).
    """
    install = host_version(binary)
    payload: dict[str, Any] = {
        "installed": bool(install.get("installed")),
        "binary": install.get("binary"),
        "version": install.get("version"),
        "version_source": install.get("source"),
        "version_raw": install.get("raw") or "",
        "hermes_home": str(host_home()),
        "running": False,
        "backend": None,
        "auth_required": None,
        "gateway": None,
        "components": None,
        "discovery": None,
        "checked_at": _now(),
    }
    if not payload["installed"]:
        payload["reason"] = install.get("error") or "no host CLI found"
        return payload

    try:
        backend, discovery = resolve_backend(url=url, token=token)
    except HostBlocked as exc:
        payload["reason"] = exc.reason
        payload["hint"] = exc.hint
        payload["discovery"] = exc.details
        return payload

    payload["running"] = True
    payload["backend"] = backend.as_dict()
    payload["discovery"] = discovery.as_dict()
    health = http_get(backend, "/api/health")
    if health["ok"] and isinstance(health["json"], dict):
        payload["version"] = payload["version"] or health["json"].get("version")
        if health["json"].get("version"):
            payload["version_source"] = "http"
        payload["auth_required"] = health["json"].get("auth_required")
    full = http_get(backend, "/api/status")
    if full["ok"] and isinstance(full["json"], dict):
        body = full["json"]
        payload["version"] = body.get("version") or payload["version"]
        payload["auth_required"] = body.get("auth_required", payload["auth_required"])
        payload["gateway"] = {
            "running": body.get("gateway_running"),
            "state": body.get("gateway_state"),
            "platforms": body.get("gateway_platforms"),
        }
        payload["components"] = {
            name: (value or {}).get("status")
            for name, value in (body.get("components") or {}).items()
            if isinstance(value, dict)
        }
    return payload


def sessions(
    *,
    url: str = "",
    token: str = "",
    profile: str = "default",
    limit: int = 20,
    offset: int = 0,
    order: str = "recent",
    backend: Backend | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """The host's session store, read-only, machine-readable (``GET /api/sessions``).

    ``profile`` names the store: ``default`` is the machine-level profile the
    ``hermes`` CLI prints. ``-`` omits the parameter, which asks for the backend
    process's *own* store — for the desktop-spawned backend that is the app's
    sandbox and is usually empty (see the module docstring).
    """
    backend = backend or resolve_backend(url=url, token=token)[0]
    params: dict[str, Any] = {
        "limit": max(0, min(int(limit), 100)),
        "offset": max(0, int(offset)),
        "order": order if order in ("created", "recent") else "recent",
    }
    if profile and profile != "-":
        params["profile"] = profile
    body = _need(backend, "/api/sessions", params, timeout=timeout)
    rows = body.get("sessions") if isinstance(body, dict) else None
    rows = rows if isinstance(rows, list) else []
    pinned = sum(1 for row in rows if isinstance(row, dict) and row.get("pinned"))
    note = ""
    if len(rows) > params["limit"]:
        note = (
            f"the host returned {len(rows)} rows for limit={params['limit']} "
            f"({pinned} of them pinned): it appends pinned sessions past the page"
        )
    return {
        **_target(backend, "/api/sessions", params),
        "profile": "(omitted)" if str(profile).strip() in ("", "-") else profile,
        "store": "the host backend's session store via GET /api/sessions",
        "total": body.get("total") if isinstance(body, dict) else None,
        "count": len(rows),
        "pinned": pinned,
        "limit": params["limit"],
        "offset": params["offset"],
        "order": params["order"],
        "note": note,
        "sessions": [
            {
                "id": row.get("id"),
                "title": row.get("title") or row.get("display_name") or "",
                "source": row.get("source"),
                "model": row.get("model"),
                "workspace": row.get("cwd"),
                "started_at": row.get("started_at"),
                "last_active": row.get("last_active"),
                "message_count": row.get("message_count"),
                "tool_call_count": row.get("tool_call_count"),
                "input_tokens": row.get("input_tokens"),
                "output_tokens": row.get("output_tokens"),
                "is_active": row.get("is_active"),
                "archived": row.get("archived"),
                "pinned": row.get("pinned"),
                "profile": row.get("profile"),
            }
            for row in rows
            if isinstance(row, dict)
        ],
        "transport": "http",
        "fragile": False,
    }


#: The host CLI's session table, for the opt-in fragile transport:
#: ``Title<2+spaces>Workspace<2+spaces>Last Active<2+spaces>ID``.
_CLI_SESSION_ROW_RE = re.compile(
    r"^(?P<title>.+?)\s{2,}(?P<workspace>\S+)\s{2,}(?P<when>.+?)\s{2,}"
    r"(?P<id>[0-9]{8}_[0-9]{6}_[0-9a-f]{4,})$"
)


def sessions_via_cli(limit: int = 20, *, binary: str | None = None, timeout: float = 30.0) -> dict:
    """``host sessions --via cli`` — the FRAGILE text fallback. Opt-in only.

    The host CLI prints this table for humans and may re-space it at any time;
    it is parsed here because an operator with no running backend asked for it,
    and the payload says so (``"fragile": true``) so no consumer mistakes it for
    the JSON surface. Use ``--via http`` (the default) whenever a backend runs.
    """
    resolved = hermes_binary(binary)
    if not resolved:
        raise HostNeedsSetup("no `hermes` binary found", hint="install the host CLI first")
    argv = [resolved, "sessions", "list", "--limit", str(max(1, int(limit)))]
    try:
        proc = subprocess.run(  # nosec B603 — fixed argv, read-only listing
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HostBlocked(
            f"`hermes sessions list` did not run: {type(exc).__name__}: {exc}"
        ) from exc
    rows = []
    for line in (proc.stdout or "").splitlines():
        match = _CLI_SESSION_ROW_RE.match(line.rstrip())
        if not match:
            continue
        rows.append(
            {
                "id": match.group("id"),
                "title": match.group("title").strip(),
                "workspace": match.group("workspace").strip(),
                "last_active": match.group("when").strip(),
            }
        )
    if not rows and proc.returncode != 0:
        raise HostBlocked(
            f"`hermes sessions list` exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}",
            hint="the host CLI is installed but refused the listing",
        )
    return {
        "backend": None,
        "request": " ".join(argv[len(argv) - 4:]) if len(argv) >= 4 else "hermes sessions list",
        "profile": "(host CLI default)",
        "store": "parsed from `hermes sessions list` (human table)",
        "count": len(rows),
        "limit": max(1, int(limit)),
        "sessions": rows,
        "transport": "cli-text",
        "fragile": True,
        "fragile_note": "the host prints this table for humans; columns may change "
                        "without notice — `--via http` is the machine-readable path",
        "exit_code": int(proc.returncode),
    }


def memory_search(
    query: str,
    *,
    url: str = "",
    token: str = "",
    profile: str = "default",
    limit: int = 20,
    backend: Backend | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """The host's own search index, read-only (``GET /api/sessions/search``).

    The route is FTS5 over the host's message index (with compression lineage
    dedup), and it answers snippets — this is the read-only search the host
    itself offers. It searches the *session store*, not ``MEMORY.md``; the file
    sizes of the built-in memory are reported by ``GET /api/memory`` and the
    bridge names the source here so nobody assumes otherwise.
    """
    text = str(query or "").strip()
    if not text:
        raise HostBlocked(
            "an empty query searches nothing",
            hint="skaldir host memory search <text>",
        )
    backend = backend or resolve_backend(url=url, token=token)[0]
    params: dict[str, Any] = {"q": text, "limit": max(1, min(int(limit), 100))}
    if profile and profile != "-":
        params["profile"] = profile
    body = _need(backend, "/api/sessions/search", params, timeout=timeout)
    rows = body.get("results") if isinstance(body, dict) else None
    rows = rows if isinstance(rows, list) else []
    return {
        **_target(backend, "/api/sessions/search", params),
        "query": text,
        "profile": "(omitted)" if str(profile).strip() in ("", "-") else profile,
        "source": "/api/sessions/search (FTS5 over the host's message index)",
        "searches": "the host's session/message store — not MEMORY.md",
        "count": len(rows),
        "results": [
            {
                "session_id": row.get("session_id"),
                "title": row.get("title") or "",
                "role": row.get("role"),
                "snippet": str(row.get("snippet") or "")[:600],
                "source": row.get("source"),
                "model": row.get("model"),
                "lineage_root": row.get("lineage_root"),
                "last_active": row.get("last_active"),
            }
            for row in rows
            if isinstance(row, dict)
        ],
        "transport": "http",
        "fragile": False,
    }


def skills(
    *, url: str = "", token: str = "", profile: str = "", backend: Backend | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """The host's installed skills (``GET /api/skills``), read-only."""
    backend = backend or resolve_backend(url=url, token=token)[0]
    params: dict[str, Any] = {"profile": profile} if profile else {}
    body = _need(backend, "/api/skills", params, timeout=timeout)
    rows = body if isinstance(body, list) else []
    rate: dict[str, int] = {}
    for row in rows:
        if isinstance(row, dict):
            provenance = str(row.get("provenance") or "unknown")
            rate[provenance] = rate.get(provenance, 0) + 1
    return {
        **_target(backend, "/api/skills", params),
        "profile": profile or "(host default)",
        "source": "/api/skills",
        "count": len(rows),
        "by_provenance": rate,
        "enabled": sum(1 for row in rows if isinstance(row, dict) and row.get("enabled")),
        "skills": [
            {
                "name": row.get("name"),
                "category": row.get("category"),
                "description": row.get("description"),
                "enabled": row.get("enabled"),
                "provenance": row.get("provenance"),
                "usage": row.get("usage"),
            }
            for row in rows
            if isinstance(row, dict)
        ],
        "transport": "http",
        "fragile": False,
    }


def cron_jobs(
    *, url: str = "", token: str = "", profile: str = "all", backend: Backend | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """The host's scheduled jobs (``GET /api/cron/jobs``), read-only."""
    backend = backend or resolve_backend(url=url, token=token)[0]
    params: dict[str, Any] = {"profile": profile} if profile else {}
    body = _need(backend, "/api/cron/jobs", params, timeout=timeout)
    rows = body if isinstance(body, list) else (
        body.get("jobs") if isinstance(body, dict) else None
    )
    rows = rows if isinstance(rows, list) else []
    return {
        **_target(backend, "/api/cron/jobs", params),
        "profile": profile or "(host default)",
        "source": "/api/cron/jobs",
        "count": len(rows),
        "jobs": [
            {
                "id": row.get("id") or row.get("job_id"),
                "name": row.get("name") or row.get("title") or "",
                "schedule": row.get("schedule") or row.get("cron"),
                "enabled": row.get("enabled"),
                "paused": row.get("paused"),
                "next_run": row.get("next_run") or row.get("next_run_at"),
                "last_run": row.get("last_run") or row.get("last_run_at"),
                "prompt": str(row.get("prompt") or "")[:200],
            }
            for row in rows
            if isinstance(row, dict)
        ],
        "transport": "http",
        "fragile": False,
    }


def toolsets(
    *, url: str = "", token: str = "", profile: str = "", backend: Backend | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """The host's toolsets and their tools (``GET /api/tools/toolsets``), read-only."""
    backend = backend or resolve_backend(url=url, token=token)[0]
    params: dict[str, Any] = {"profile": profile} if profile else {}
    body = _need(backend, "/api/tools/toolsets", params, timeout=timeout)
    rows = body if isinstance(body, list) else []
    return {
        **_target(backend, "/api/tools/toolsets", params),
        "profile": profile or "(host default)",
        "source": "/api/tools/toolsets",
        "count": len(rows),
        "tool_count": sum(len(row.get("tools") or []) for row in rows if isinstance(row, dict)),
        "toolsets": [
            {
                "name": row.get("name"),
                "label": row.get("label"),
                "platform": row.get("platform"),
                "enabled": row.get("enabled"),
                "available": row.get("available"),
                "configured": row.get("configured"),
                "tools": list(row.get("tools") or []),
            }
            for row in rows
            if isinstance(row, dict)
        ],
        "transport": "http",
        "fragile": False,
    }


#: The probes ``host doctor`` runs — one per feature this bridge can read, in the
#: order the CLI verbs use them. ``params`` are the ones the verbs themselves send.
DOCTOR_PROBES: tuple[tuple[str, str, dict], ...] = (
    ("health", "/api/health", {}),
    ("status", "/api/status", {}),
    ("sessions", "/api/sessions", {"limit": 1, "order": "recent", "profile": "default"}),
    ("memory_search", "/api/sessions/search", {"q": "skaldir", "limit": 1, "profile": "default"}),
    ("skills", "/api/skills", {}),
    ("cron", "/api/cron/jobs", {"profile": "all"}),
    ("tools", "/api/tools/toolsets", {}),
    ("memory", "/api/memory", {}),
    ("profiles", "/api/profiles", {}),
    ("logs", "/api/logs", {"limit": 1}),
)


def doctor(
    *,
    url: str = "",
    token: str = "",
    binary: str | None = None,
    probe_cli: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """What of the host is reachable from here, probed one route at a time.

    Every probe is the route a verb actually calls, with the params that verb
    sends, so a green doctor means the verbs work — not that some other route does.
    """
    report: dict[str, Any] = {
        "installed": False,
        "running": False,
        "backend": None,
        "discovery": None,
        "routes": [],
        "reachable": {},
        "blocked": {},
        "notes": [],
        "checked_at": _now(),
    }
    install = host_version(binary)
    report["installed"] = bool(install.get("installed"))
    report["binary"] = install.get("binary")
    report["version"] = install.get("version")
    report["version_source"] = install.get("source")
    report["hermes_home"] = str(host_home())
    if not report["installed"]:
        report["reason"] = install.get("error") or "no host CLI found"
        report["notes"].append("exit 4 (needs setup): install the host, then re-run")
        return report

    try:
        backend, discovery = resolve_backend(url=url, token=token)
    except HostBlocked as exc:
        report["reason"] = exc.reason
        report["hint"] = exc.hint
        report["discovery"] = exc.details
        report["notes"].append("exit 3 (blocked): the host is installed but no backend answered")
        if probe_cli:
            report["cli_serve_status"] = _cli_serve_status(backend=None, binary=binary)
        return report

    report["running"] = True
    report["backend"] = backend.as_dict()
    report["discovery"] = discovery.as_dict()
    for name, path, params in DOCTOR_PROBES:
        result = http_get(backend, path, params, timeout=timeout)
        entry = {
            "name": name,
            "path": path,
            "params": params,
            "status": result["status"],
            "ok": bool(result["ok"]),
            "detail": result["error"] or _probe_count(result["json"]),
        }
        report["routes"].append(entry)
        (report["reachable"] if result["ok"] else report["blocked"])[name] = entry["detail"]
    if probe_cli:
        report["cli_serve_status"] = _cli_serve_status(backend=backend, binary=binary)
    if report["blocked"]:
        report["notes"].append(
            "some routes are blocked: " + ", ".join(sorted(report["blocked"]))
        )
    return report


def _probe_count(payload: Any) -> str:
    if isinstance(payload, list):
        return f"{len(payload)} item(s)"
    if isinstance(payload, dict):
        if "sessions" in payload:
            return f"total={payload.get('total')} on this page={len(payload.get('sessions') or [])}"
        if "results" in payload:
            return f"{len(payload.get('results') or [])} result(s)"
        return f"keys: {', '.join(sorted(payload)[:6])}"
    return "answered"


def _cli_serve_status(*, backend: Backend | None, binary: str | None) -> dict:
    """``hermes serve --status`` as *evidence*, and why it can disagree with us.

    Measured on this machine: it reported "No hermes dashboard or serve processes
    running" while a desktop-spawned ``hermes serve --port 0`` was answering —
    it skips processes whose announced port is ``0``. Reported, never trusted.
    """
    resolved = hermes_binary(binary)
    if not resolved:
        return {"ran": False, "detail": "no host CLI"}
    try:
        proc = subprocess.run(  # nosec B603 — fixed argv, read-only process listing
            [resolved, "serve", "--status"], capture_output=True, text=True,
            timeout=30.0, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ran": False, "detail": f"{type(exc).__name__}: {exc}"}
    text = " ".join((proc.stdout or "").split())
    return {
        "ran": True,
        "exit_code": int(proc.returncode),
        "says": text[:300],
        "agrees_with_bridge": (
            bool("No hermes dashboard" not in text) if backend
            else "No hermes dashboard" in text
        ),
        "known_blind_spot": "a --port 0 serve is invisible to this command; the bridge "
                            "finds it through /proc instead",
    }

