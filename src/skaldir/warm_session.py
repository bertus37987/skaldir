"""Warm session: one long-lived ``skaldir`` process, many instant commands.

The measured problem (numbers in ``MVP/26-warm-session.md``): every single CLI
invocation pays the whole startup before the command itself runs —

    skaldir tools list --json     340 ms   (interpreter 12 · import graph ~50 · command work 7)
    skaldir --warm tools list     74 ms    (client interpreter + import 52 · round trip 2 · work 7)
    the same command, in-session    7 ms   (measured with the client startup excluded)

That is the difference between "a tool you avoid" and "Cline-like": the command
work is single-digit milliseconds, so anything that re-does the startup per
action is the cost. This module makes the startup *once per session*.

Shape
-----
``warm start`` forks a small daemon (``python -m <pkg>.warm_session --serve``)
that imports the product front door once, builds the ``argparse`` tree once, and
then answers commands over an ``AF_UNIX`` socket. Each request is served by
``fork()``-ing the daemon, so one command can neither see nor corrupt another:

* the child inherits the daemon's *imported modules* (that is the whole win) and
  gets a fresh copy of everything else;
* ``cwd``, ``argv``, ``environ``, ``sys.executable`` and the fd table are set to
  the client's values, then the product front door runs exactly as it does cold;
* stdout/stderr are captured at **fd level** (``dup2`` onto temp files), so print
  buffering, subprocess output and ``--json`` captures all land in the same bytes;
* ``sys.stdout.isatty()`` is answered from the *client's* tty state, so a command
  that colorizes keeps colorizing and one that does not stays plain.

Equivalence is the point, not a nice-to-have: the same ``argv``, the same
``cli_contract`` exit codes, the same bytes. The one deliberate difference is
documented and proven by ``tests/test_warm_session_equivalence.py``.

What is *not* warm
------------------
Only commands the contract declares read-only run warm, and never the
interactive/long-running ones (``mcp.serve``, ``desktop.start``, the REPL, the
session commands themselves). Anything else, an unknown command, a missing
daemon, a stale lock or an insecure socket falls back to the **cold** path with
a note on stderr — never a silently wrong answer.

Security
--------
The socket is the CLI, for the same user: whoever can connect can run commands as
that user, exactly as if they had a shell. Therefore:

* socket directory ``0700`` owned by the user, socket file ``0600`` — the
  connect check refuses a socket that is not ours or is group/other reachable;
* ``SO_PEERCRED`` on accept: the peer's uid must be ours, anything else is
  refused before a single byte is parsed (a stray ``chmod`` cannot widen this);
* no TCP, no token file, no network surface: a filesystem permission is the
  whole guard, plus the uid check;
* the daemon runs no privileged operation the cold path would not run, and only
  for commands the contract marks read-only;
* a stale lock (owner died) is detected by ``flock`` — the kernel releases it on
  process death — and taken over only by ``warm start``; an unreachable socket is
  reported, never silently replaced.
"""

from __future__ import annotations

import errno
import fcntl
import json
import locale
import os
import select
import signal
import socket
import stat
import struct
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "APP",
    "DEFAULT_IDLE_TIMEOUT_S",
    "DEFAULT_REQUEST_TIMEOUT_S",
    "EXIT_INTERNAL",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_USAGE",
    "PROTOCOL_VERSION",
    "ClientError",
    "ExecResult",
    "WarmRefused",
    "SessionStatus",
    "WarmDenied",
    "WarmServer",
    "check_socket_security",
    "client_env_fingerprint",
    "daemon_main",
    "env_names",
    "env_value",
    "default_socket_path",
    "lock_path_for",
    "log_path_for",
    "read_lock",
    "run_exec",
    "session_dir",
    "start_session",
    "status_of",
    "stop_session",
    "fingerprint_mismatch",
    "front_door",
    "product_prog",
    "stdin_for_warm",
    "warm_verdict",
]

PROTOCOL_VERSION = 1

#: The package name the session lives under (``skaldir`` / ``godsv``): taken from
#: this module's own package so a rename wave cannot leave a stale socket path
#: behind. Both the daemon and the client derive it the same way, from their own
#: module, i.e. from the same installation.
APP = (__package__ or "").split(".")[-1] or Path(__file__).resolve().parent.name

#: The console script name used as ``argv[0]`` inside the warm child comes from
#: :func:`product_prog` — measured from this machine, because argparse prints it in
#: usage/error text and the warm output has to match the cold output byte for byte.

DEFAULT_IDLE_TIMEOUT_S = 900.0
DEFAULT_REQUEST_TIMEOUT_S = 120.0
DEFAULT_START_TIMEOUT_S = 20.0
STOP_TIMEOUT_S = 10.0

#: Mirrors of ``cli_contract``'s exit codes. Asserted equal by
#: ``tests/test_warm_session.py::test_exit_codes_mirror_the_contract`` so the
#: mirror cannot drift into a wrong answer.
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NOT_FOUND = 5
EXIT_INTERNAL = 7

MAX_HEADER_BYTES = 1 << 20  # an env is a few KB; a 1 MiB header is a bug or an attack
MAX_STDIN_BYTES = 8 << 20
MAX_ARGV_ITEMS = 4096
MAX_ENV_ITEMS = 4096
LOG_ROTATE_BYTES = 256 << 10
LOG_TAIL_BYTES = 16 << 10
DEFAULT_MAX_RSS_MB = 768.0

#: Environment prefixes: the product's current name first, the pre-rename spelling as
#: the legacy alias — the same order ``registry.runtime_dir()`` uses for
#: ``$SKALDIR_HOME`` / ``$GODSV_HOME``. Composed from this tuple (instead of written
#: out twice) so both spellings keep working whatever a rename wave does to literals.
ENV_PREFIXES: tuple[str, ...] = ("SKALDIR", "GODSV")


def env_names(suffix: str) -> tuple[str, ...]:
    """Every environment name this module answers to, current spelling first."""
    return tuple(f"{prefix}_{suffix}" for prefix in ENV_PREFIXES)


def env_value(suffix: str) -> str | None:
    """The first set value of ``<prefix>_<suffix>`` (current spelling wins)."""
    for name in env_names(suffix):
        value = os.environ.get(name)
        if value:
            return value
    return None


_PROG_CACHE: str | None = None


def _resolve_prog() -> str:
    """The console script's name: measured from this machine, not assumed.

    It has to equal the cold path's ``argv[0]`` basename, because argparse prints the
    program name in usage and error text — a warm command that called itself something
    else would not be byte-identical. Sources in order: the running process' own
    ``argv[0]`` (the console script, once the front door is wired), a script sitting
    next to the interpreter, then the two names this product has shipped under.
    """
    override = env_value("WARM_PROG")
    if override:
        return override
    argv0 = os.path.basename(sys.argv[0] or "")
    if argv0 and not argv0.endswith(".py") and argv0 not in ("-c", "-m", ""):
        return argv0
    bindir = Path(sys.executable).parent
    fallback = ""
    for candidate in ("skaldir", "godsvoice"):
        script = bindir / candidate
        if not script.exists():
            continue
        if not fallback:
            fallback = candidate
        try:
            text = script.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover
            continue
        if f"{APP}." in text:  # the script that imports *this* installation
            return candidate
    return fallback or "skaldir"


def product_prog() -> str:
    """The console script name (cached) — see :func:`_resolve_prog`."""
    global _PROG_CACHE
    if _PROG_CACHE is None:
        _PROG_CACHE = _resolve_prog()
    return _PROG_CACHE

#: Commands that must never run warm, by dotted command path prefix. Everything
#: else is decided by the contract (``CommandSpec.mutates``): only read-only
#: commands are warm — a mutating command keeps the cold path's process,
#: signals, tty and lifetime semantics.
WARM_DENY_PREFIXES: tuple[str, ...] = (
    # the session commands themselves (they manage the session, not the product)
    "warm",
    # long-running servers / app launches
    "serve",
    "daemon",
    "desktop.start",
    "desktop.build-app",
    "mcp.serve",
    # need a real display / a real tty
    "cu.check",
    "cu.demo",
    "repl",
    "shell",
    "watch",
)

#: Fallback key set for the parser cache: used only when the runtime probe
#: (:func:`_preload_recorded`) could not measure the real one — e.g. a build that
#: iterates the whole environment (``dict(os.environ)``), where no key list can be
#: trusted. A superset is the safe direction: a false miss costs one parser
#: rebuild, a false hit would answer with the wrong runtime directory.
_ENV_FINGERPRINT_FALLBACK: tuple[str, ...] = (
    "HERMES_HOME",
    "GODSV_HOME",
    "HOME",
    "GODSV_PROMPTS",
    "GODSV_VAULT_KEY",
    "GODSV_FORMAT",
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "XDG_RUNTIME_DIR",
    "XDG_CACHE_HOME",
    "PATH",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_MESSAGES",
    "TZ",
    "TERM",
    "COLUMNS",
    "LINES",
    "NO_COLOR",
    "USER",
    "LOGNAME",
    "SHELL",
)

#: Always in the key set, even if the recorder did not see them: they select the
#: runtime directory, which is where most build-time defaults come from.
_ENV_FINGERPRINT_FLOOR: tuple[str, ...] = ("HERMES_HOME", "HOME", *env_names("HOME"))


class _EnvRecorder:
    """Stand-in for ``os.environ`` that records which keys the import/build read.

    Measured instead of guessed: the daemon imports its CLI graph and builds its
    parser with this installed, so the cache is keyed on exactly the variables the
    build consumed. Iterating the whole environment (``dict(os.environ)`` …) sets
    :attr:`wildcard`, which makes the caller fall back to the conservative list.
    """

    def __init__(self, data: Any) -> None:
        self._data = data
        self.read: set[str] = set()
        self.wildcard = False

    def get(self, key: str, default: Any = None) -> Any:
        self.read.add(str(key))
        return self._data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        self.read.add(str(key))
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        self.read.add(str(key))
        return key in self._data

    def setdefault(self, key: str, default: Any = None) -> Any:
        self.read.add(str(key))
        return self._data.setdefault(key, default)

    def pop(self, key: str, default: Any = None) -> Any:
        self.read.add(str(key))
        return self._data.pop(key, default)

    def __iter__(self) -> Any:
        self.wildcard = True
        return iter(self._data)

    def keys(self) -> Any:
        self.wildcard = True
        return self._data.keys()

    def items(self) -> Any:
        self.wildcard = True
        return self._data.items()

    def values(self) -> Any:
        self.wildcard = True
        return self._data.values()

    def copy(self) -> dict:
        self.wildcard = True
        return dict(self._data)

    def __len__(self) -> int:
        self.wildcard = True
        return len(self._data)

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __delitem__(self, key: str) -> None:
        del self._data[key]

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._data.update(*args, **kwargs)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<_EnvRecorder read={sorted(self.read)} wildcard={self.wildcard}>"


#: Why a command is denied — carried to the client so the fallback note is honest.
_DENY_LONG_RUNNING = "long-running/interactive — the cold path owns its process"
_DENY_UNKNOWN = "not a command this session knows"
_DENY_MUTATES = "the contract marks this command as mutating — cold path only"


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #


def _owns_private_dir(path: Path) -> bool:
    """True when ``path`` exists, is ours and is not group/other reachable."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode):
        return False
    if st.st_uid != os.getuid():
        return False
    return not st.st_mode & 0o077


def session_dir() -> Path:
    """The directory holding socket, lock and log.

    ``<PREFIX>_WARM_DIR`` wins (tests and containers); otherwise
    ``$XDG_RUNTIME_DIR/<app>`` when the runtime dir is private, else
    ``$XDG_STATE_HOME|~/.local/state/<app>``. Nothing is created here — callers
    create it with ``0700``.
    """
    override = env_value("WARM_DIR")
    if override:
        return Path(override).expanduser()
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and _owns_private_dir(Path(runtime)):
        return Path(runtime) / APP
    state = os.environ.get("XDG_STATE_HOME")
    base = Path(state).expanduser() if state else Path.home() / ".local" / "state"
    return base / APP


def default_socket_path() -> Path:
    """``<PREFIX>_WARM_SOCKET`` when set, else ``<session_dir>/warm.sock``."""
    override = env_value("WARM_SOCKET")
    if override:
        return Path(override).expanduser()
    return session_dir() / "warm.sock"


def lock_path_for(socket_path: Path | str) -> Path:
    return Path(socket_path).with_name(Path(socket_path).name + ".lock")


def log_path_for(socket_path: Path | str) -> Path:
    return Path(socket_path).with_name(Path(socket_path).name + ".log")


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` as ``0700`` (and every missing parent), then verify it.

    Raises :class:`ClientError` when the directory exists with the wrong owner or
    permissions — a session never shares its socket with group/other.
    """
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        st = os.lstat(path)
    except OSError as exc:  # pragma: no cover — race with a cleaner
        raise ClientError(f"cannot inspect session dir {path}: {exc}") from exc
    if not stat.S_ISDIR(st.st_mode):
        raise ClientError(f"session dir {path} is not a directory")
    if st.st_uid != os.getuid():
        raise ClientError(f"session dir {path} is owned by uid {st.st_uid}, not {os.getuid()}")
    if st.st_mode & 0o077:
        os.chmod(path, 0o700)
    return path


def check_socket_security(socket_path: Path | str) -> tuple[bool, str]:
    """Is this socket ours and private? ``(ok, human explanation)``.

    Refusing on a socket that is not ``0600`` and not owned by us is the client
    half of the same-uid guard: connecting to a socket somebody else can write
    would mean trusting their commands.
    """
    path = Path(socket_path)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False, f"no socket at {path}"
    except OSError as exc:
        return False, f"cannot inspect {path}: {exc}"
    if stat.S_ISLNK(st.st_mode):
        return False, f"{path} is a symlink — refusing to follow it"
    if not stat.S_ISSOCK(st.st_mode):
        return False, f"{path} is not a socket"
    if st.st_uid != os.getuid():
        return False, f"{path} is owned by uid {st.st_uid}, not {os.getuid()}"
    if st.st_mode & 0o077:
        return False, f"{path} has mode {oct(st.st_mode & 0o777)} — group/other may connect"
    return True, f"{path} is 0600 and owned by uid {os.getuid()}"


# --------------------------------------------------------------------------- #
# lock / metadata
# --------------------------------------------------------------------------- #


def _lock_fd(path: Path) -> int | None:
    """Open (creating) the lock file, or ``None`` when that is not possible."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return None


def lock_is_held(path: Path | str) -> bool:
    """Does a live process hold this lock? ``flock`` is released on death.

    A successful non-blocking ``LOCK_EX`` proves nobody holds it, which is what
    makes staleness detectable without trusting a pid.
    """
    lock = Path(path)
    fd = _lock_fd(lock)
    if fd is None:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:  # pragma: no cover
            pass
        os.close(fd)
    return False


def read_lock(path: Path | str) -> dict:
    """The lock file's metadata (``{}`` when unreadable/garbage)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_lock(path: Path, payload: dict) -> None:
    text = json.dumps(payload, sort_keys=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")


def _log_line(log_path: Path | None, message: str) -> None:
    """Append one timestamped line, rotating once the log grows past the cap."""
    if log_path is None:
        return
    try:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
        if log_path.stat().st_size > LOG_ROTATE_BYTES:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            log_path.write_text("\n".join(lines[len(lines) // 2 :]) + "\n", encoding="utf-8")
    except OSError:  # pragma: no cover — a log that cannot be written must not kill the daemon
        pass


def log_tail(log_path: Path | str, limit: int = LOG_TAIL_BYTES) -> str:
    """The last ``limit`` bytes of the log — what ``warm start`` shows on failure."""
    try:
        data = Path(log_path).read_bytes()
    except OSError:
        return ""
    return data[-limit:].decode("utf-8", errors="replace").strip()


# --------------------------------------------------------------------------- #
# what may run warm
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WarmDenied:
    """Why a command cannot run in the warm session."""

    command: str
    reason: str


def _contract_module() -> Any:
    try:
        from . import cli_contract
    except Exception:  # noqa: BLE001 — a build without the contract module
        return None
    return cli_contract


def _command_path(argv: Sequence[str]) -> str:
    """The dotted command path of an argv (``["tools","list","--json"]`` → ``tools.list``)."""
    tokens = [str(item) for item in argv if not str(item).startswith("-")]
    return ".".join(tokens)


def warm_verdict(argv: Sequence[str]) -> str | WarmDenied:
    """May this argv run warm? ``"ok"`` or a :class:`WarmDenied` with the reason.

    The decision is read from the product's own contract (``CommandSpec.mutates``)
    plus the interactive/long-running deny list — i.e. it is the same declaration
    the CLI already ships, not a second hand-written list.
    """
    if not argv:
        return WarmDenied("", "no command given — the cold path prints the welcome")
    first = str(argv[0])
    if first in ("--help", "-h", "--version", "-V"):
        return WarmDenied(first, "answered by the front door without a command")
    path = _command_path(argv)
    if not path:
        return WarmDenied("", "no command given")
    for prefix in WARM_DENY_PREFIXES:
        if path == prefix or path.startswith(prefix + "."):
            return WarmDenied(path, _DENY_LONG_RUNNING)
    contract = _contract_module()
    if contract is None:
        return WarmDenied(
            path, "no cli_contract in this build — cannot prove the command is read-only"
        )
    spec = contract.COMMANDS.get(path)
    if spec is None:
        return WarmDenied(path, _DENY_UNKNOWN)
    if getattr(spec, "mutates", False):
        return WarmDenied(path, _DENY_MUTATES)
    return "ok"


def warm_eligible_commands() -> tuple[list[str], dict[str, str]]:
    """``(eligible, denied → reason)`` over the contract's command table."""
    contract = _contract_module()
    if contract is None:
        return [], {}
    eligible: list[str] = []
    denied: dict[str, str] = {}
    for command in sorted(contract.COMMANDS):
        verdict = warm_verdict(command.split("."))
        if verdict == "ok":
            eligible.append(command)
        else:
            assert isinstance(verdict, WarmDenied)
            denied[command] = verdict.reason
    return eligible, denied


# --------------------------------------------------------------------------- #
# framing
# --------------------------------------------------------------------------- #


class ClientError(RuntimeError):
    """The client could not talk to a session (or the session answered garbage)."""


class WarmRefused(ClientError):
    """The session answered, and the answer is *no* — a policy refusal, not a failure.

    Kept apart from the transport errors so the caller can say why a command did not run
    warm (mutating, long-running, unknown) instead of "the session is broken".
    """

    def __init__(self, reason: str, command: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.command = command


@dataclass
class _Buffer:
    """Bytes received but not yet consumed — survives ``fork``, hence shared."""

    data: bytes = b""


def _encode_line(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"


def _recv_header(sock: socket.socket, buf: _Buffer, deadline: float) -> dict:
    while True:
        index = buf.data.find(b"\n")
        if index >= 0:
            line, buf.data = buf.data[:index], buf.data[index + 1 :]
            try:
                payload = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ClientError(f"malformed request header: {exc}") from exc
            if not isinstance(payload, dict):
                raise ClientError("request header is not a JSON object")
            return payload
        if len(buf.data) > MAX_HEADER_BYTES:
            raise ClientError("request header exceeds the 1 MiB limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ClientError("timed out waiting for the request header")
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(1 << 16)
        except TimeoutError as exc:
            raise ClientError("timed out waiting for the request header") from exc
        if not chunk:
            raise ClientError("peer closed the connection before sending a request")
        buf.data += chunk


def _recv_payload(sock: socket.socket, buf: _Buffer, size: int, deadline: float) -> bytes:
    if size < 0 or size > MAX_STDIN_BYTES:
        raise ClientError(f"payload size {size} is out of range")
    if len(buf.data) >= size:
        data, buf.data = buf.data[:size], buf.data[size:]
        return data
    chunks = [buf.data]
    buf.data = b""
    got = len(chunks[0])
    while got < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ClientError("timed out waiting for the payload")
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(min(1 << 16, size - got))
        except TimeoutError as exc:
            raise ClientError("timed out waiting for the payload") from exc
        if not chunk:
            raise ClientError("peer closed before the payload was complete")
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def _peer_credentials(conn: socket.socket) -> dict:
    """``{"pid","uid","gid"}`` of the connected peer (Linux ``SO_PEERCRED``)."""
    try:
        raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, gid = struct.unpack("3i", raw)
    except (OSError, AttributeError):  # pragma: no cover — non-Linux without SO_PEERCRED
        return {}
    return {"pid": pid, "uid": uid, "gid": gid}


def _proc_cwd(pid: int) -> str | None:
    """``cwd`` of another process (same uid) — used by minimal clients."""
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def _proc_environ(pid: int) -> dict[str, str] | None:
    """The *exec-time* environment of another process, same uid only.

    Only a fallback for clients that send no environment (a shell client with
    ``nc``): ``/proc/<pid>/environ`` cannot see later ``os.environ`` changes, so
    the Python client always sends its own environment instead.
    """
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return None
    env: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if b"=" in item:
            key, _, value = item.partition(b"=")
            env[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return env or None


# --------------------------------------------------------------------------- #
# client
# --------------------------------------------------------------------------- #


@dataclass
class ExecResult:
    """One warm command's answer, byte for byte."""

    exit_code: int
    stdout: bytes
    stderr: bytes
    daemon_ms: float
    daemon_pid: int
    cache: str = "off"
    entry: str = ""
    wall_ms: float = 0.0


@dataclass
class SessionStatus:
    """What ``warm status`` reports — including the honest not-running states."""

    running: bool
    state: str  # running | not_running | stale | unreachable | insecure
    note: str = ""
    socket: str = ""
    pid: int = 0
    info: dict = field(default_factory=dict)


def _connect(socket_path: Path | str, timeout: float) -> socket.socket:
    ok, reason = check_socket_security(socket_path)
    if not ok:
        raise ClientError(reason)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(socket_path))
    except OSError as exc:
        sock.close()
        raise ClientError(f"cannot connect to {socket_path}: {exc.strerror or exc}") from exc
    return sock


def _exchange(socket_path: Path | str, request: dict, *, timeout: float,
              stdin_bytes: bytes = b"") -> tuple[dict, bytes, bytes]:
    """Send one request, read one response. Returns ``(header, stdout, stderr)``."""
    deadline = time.monotonic() + max(0.1, timeout)
    sock = _connect(socket_path, timeout)
    try:
        payload = json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
        sock.settimeout(max(0.1, timeout))
        try:
            sock.sendall(payload)
            if stdin_bytes:
                sock.sendall(stdin_bytes)
            buf = _Buffer()
            header = _recv_header(sock, buf, deadline)
            out_len = int(header.get("stdout_len") or 0)
            err_len = int(header.get("stderr_len") or 0)
            out = _recv_payload(sock, buf, out_len, deadline) if out_len else b""
            err = _recv_payload(sock, buf, err_len, deadline) if err_len else b""
        except OSError as exc:
            # A daemon that exits between our connect and our answer (idle timeout, a signal,
            # a crash) resets the connection. That is an expected state, not an exception:
            # the caller has to be able to report it and fall back to the cold path.
            raise ClientError(
                f"the session connection dropped: {getattr(exc, 'strerror', None) or exc}"
            ) from exc
        return header, out, err
    finally:
        sock.close()


def stdin_for_warm() -> tuple[bytes, str]:
    """``(bytes to forward, refusal reason)`` for the warm client's stdin.

    A cold command inherits this process' stdin; a warm command runs in the session, so the
    bytes have to be forwarded. That is safe exactly when the answer is already known:

    * a terminal → nothing forwarded (the session refuses tty-bound commands anyway);
    * ``/dev/null`` (the harness default) → nothing, and that *is* the answer (EOF);
    * a regular file or a pipe with data waiting → read it, forward it;
    * anything else — a pipe or socket with nothing ready yet (``sleep 1 | skaldir …``) →
      refuse: the cold path is the only way to run that command and see the same stdin.
    """
    import select as _select
    import stat as _stat

    stream = getattr(sys.stdin, "buffer", None)
    if stream is None or getattr(sys.stdin, "isatty", lambda: False)():
        return b"", ""
    try:
        mode = os.fstat(0).st_mode
    except OSError:  # pragma: no cover — no fd 0
        return b"", "stdin is not readable"
    if _stat.S_ISCHR(mode):
        return b"", ""
    try:
        ready, _, _ = _select.select([sys.stdin], [], [], 0)
    except (OSError, ValueError):  # pragma: no cover — closed fd
        return b"", "stdin cannot be polled"
    if not ready:
        return b"", "stdin is a pipe with nothing to read yet"
    data = stream.read(MAX_STDIN_BYTES + 1)
    if len(data) > MAX_STDIN_BYTES:
        return b"", f"stdin carries more than {MAX_STDIN_BYTES} bytes"
    return data, ""


def _request_payload(argv: Sequence[str], *, cwd: str | None = None, env: dict | None = None,
                     tty_out: bool | None = None, tty_err: bool | None = None,
                     stdin_bytes: bytes = b"", prog: str = "", executable: str = "",
                     request_id: int = 0) -> dict:
    return {
        "v": PROTOCOL_VERSION,
        "op": "exec",
        "argv": [str(item) for item in argv][:MAX_ARGV_ITEMS],
        "prog": prog or product_prog(),
        "cwd": cwd if cwd is not None else os.getcwd(),
        "env": env if env is not None else dict(os.environ),
        "tty_out": bool(sys.stdout.isatty()) if tty_out is None else bool(tty_out),
        "tty_err": bool(sys.stderr.isatty()) if tty_err is None else bool(tty_err),
        "stdin_len": len(stdin_bytes),
        "executable": executable or sys.executable,
        "request_id": request_id,
    }


def run_exec(argv: Sequence[str], *, socket_path: Path | str | None = None,
             timeout: float | None = None, cwd: str | None = None,
             env: dict | None = None, stdin_bytes: bytes = b"",
             tty_out: bool | None = None, tty_err: bool | None = None,
             prog: str = "") -> ExecResult:
    """Run one command in the warm session (raises :class:`ClientError` when it cannot)."""
    target = Path(socket_path) if socket_path is not None else default_socket_path()
    limit = DEFAULT_REQUEST_TIMEOUT_S if timeout is None else float(timeout)
    request = _request_payload(argv, cwd=cwd, env=env, tty_out=tty_out, tty_err=tty_err,
                               stdin_bytes=stdin_bytes, prog=prog)
    started = time.perf_counter()
    header, out, err = _exchange(target, request, timeout=limit, stdin_bytes=stdin_bytes)
    wall_ms = (time.perf_counter() - started) * 1000.0
    if header.get("status") == "refused":
        raise WarmRefused(str(header.get("reason") or "the session refused the command"),
                          str(header.get("command") or ""))
    if header.get("status") != "ok":
        raise ClientError(str(header.get("reason") or "the session could not answer"))
    return ExecResult(
        exit_code=int(header.get("exit_code") or 0),
        stdout=out,
        stderr=err,
        daemon_ms=float(header.get("daemon_ms") or 0.0),
        daemon_pid=int(header.get("daemon_pid") or 0),
        cache=str(header.get("cache") or "off"),
        entry=str(header.get("entry") or ""),
        wall_ms=wall_ms,
    )


def status_of(socket_path: Path | str | None = None, *, timeout: float = 2.0) -> SessionStatus:
    """Is a session running, and what is it? Never raises for the expected states."""
    target = Path(socket_path) if socket_path is not None else default_socket_path()
    lock = lock_path_for(target)
    held = lock_is_held(lock)
    meta = read_lock(lock)
    ok, reason = check_socket_security(target)
    if not ok:
        # A socket that exists but is not ours to use is refused *by name* — never
        # reported as "no session", which would invite a client to just try to connect.
        if target.exists() or target.is_symlink():
            return SessionStatus(False, "insecure", reason, str(target),
                                 int(meta.get("pid") or 0), {"lock": meta})
        if not held:
            note = reason
            if meta.get("pid"):
                # The lock *file* is deliberately never unlinked (removing it would let a
                # waiter hold the flock on an unlinked inode while a new daemon locks a fresh
                # one — two owners). So it outlives a clean exit, and "the lock names a pid
                # that does not hold it" is the honest reading for both a clean stop and a
                # crash.
                note += f" (the lock file names pid {meta['pid']}, which no longer holds it)"
            state = "stale" if meta.get("pid") else "not_running"
            return SessionStatus(False, state, note, str(target), int(meta.get("pid") or 0),
                                 {"lock": meta})
    try:
        header, _out, _err = _exchange(
            target, {"v": PROTOCOL_VERSION, "op": "status"}, timeout=timeout
        )
    except ClientError as exc:
        state = "unreachable" if held else "stale"
        note = f"{exc}"
        if held:
            note += f" (pid {meta.get('pid')} still holds the lock — the socket is not answering)"
        return SessionStatus(False, state, note, str(target),
                             int(meta.get("pid") or 0), {"lock": meta})
    info = header.get("info") or {}
    return SessionStatus(True, "running", "session answers", str(target),
                         int(info.get("pid") or 0), info)


def client_env_fingerprint(env: dict | None = None,
                           keys: Sequence[str] | None = None) -> dict[str, str]:
    """The environment values a cached parser was built from.

    ``keys`` is the session's measured key set (:func:`_preload_recorded`); without it
    the conservative fallback list is used, which is what a client has before it has
    asked a session anything.
    """
    source = os.environ if env is None else env
    wanted = list(keys) if keys else list(_ENV_FINGERPRINT_FALLBACK)
    return {key: str(source.get(key, "")) for key in wanted}


def fingerprint_mismatch(info: dict, env: dict | None = None) -> list[str]:
    """Which parser-cache keys differ between this client and the session (``[]`` = match).

    ``warm status`` uses it to say, honestly, whether the cached parser still describes
    this client's runtime: when keys differ the session rebuilds the tree per command,
    which is correct but not fast, and the operator can restart the session for speed.
    """
    keys = list(info.get("env_keys") or _ENV_FINGERPRINT_FALLBACK)
    theirs = info.get("env_fingerprint") or {}
    mine = client_env_fingerprint(env, keys)
    return sorted(key for key in keys if str(theirs.get(key, "")) != mine[key])


def start_session(socket_path: Path | str | None = None, *, idle_timeout: float | None = None,
                  wait_s: float = DEFAULT_START_TIMEOUT_S, parser_cache: bool = True,
                  env: dict | None = None) -> SessionStatus:
    """Start the daemon and wait until it answers. Idempotent: a live session is returned."""
    target = Path(socket_path) if socket_path is not None else default_socket_path()
    current = status_of(target)
    if current.running:
        return current
    if not hasattr(os, "fork"):  # pragma: no cover — Windows
        return SessionStatus(False, "unsupported",
                             "warm sessions need POSIX fork; use the cold path",
                             str(target))
    ensure_private_dir(target.parent)
    log = log_path_for(target)
    # A leftover socket from a dead daemon would make bind() fail; it is only
    # removed here — after the lock says nobody is alive — never by a client path.
    if not lock_is_held(lock_path_for(target)):
        try:
            if target.exists() and not check_socket_security(target)[0]:
                target.unlink()
            elif stat.S_ISSOCK(os.lstat(target).st_mode):
                target.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            return SessionStatus(False, "unreachable",
                                 f"cannot clear the stale socket {target}: {exc}",
                                 str(target))
    child_env = dict(os.environ if env is None else env)
    module = f"{__package__}.warm_session" if __package__ else "warm_session"
    pythonpath = [item for item in str(child_env.get("PYTHONPATH", "")).split(os.pathsep) if item]
    package_root = str(Path(__file__).resolve().parents[1])
    if package_root not in pythonpath:
        pythonpath.append(package_root)
    child_env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    argv = [
        sys.executable,
        "-m",
        module,
        "--serve",
        "--socket",
        str(target),
        "--idle-timeout",
        str(DEFAULT_IDLE_TIMEOUT_S if idle_timeout is None else float(idle_timeout)),
    ]
    if not parser_cache:
        argv.append("--no-parser-cache")
    with open(log, "a", encoding="utf-8") as handle:
        os.chmod(log, 0o600)
        try:
            import subprocess

            subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=handle,
                start_new_session=True,
                env=child_env,
                cwd=str(Path.cwd()),
            )
        except OSError as exc:
            return SessionStatus(False, "unreachable",
                                 f"cannot spawn the session daemon: {exc}", str(target))
    deadline = time.monotonic() + max(0.5, wait_s)
    last: SessionStatus | None = None
    while time.monotonic() < deadline:
        time.sleep(0.05)
        last = status_of(target, timeout=1.0)
        if last.running:
            return last
        if last.state == "insecure":
            return last
    tail = log_tail(log)
    note = f"the session daemon did not answer within {wait_s:.0f}s"
    if tail:
        note += f"; log tail: {tail}"
    return SessionStatus(False, "unreachable", note, str(target),
                         int((last or SessionStatus(False, "x")).pid or 0))


def stop_session(socket_path: Path | str | None = None, *, force: bool = False,
                 timeout: float = STOP_TIMEOUT_S) -> SessionStatus:
    """Ask the session to stop; with ``force`` also SIGTERM a stale lock holder.

    ``force`` validates the pid before signalling: it must come from *our* lock
    file *and* its command line must be this module — never an arbitrary pid.
    """
    target = Path(socket_path) if socket_path is not None else default_socket_path()
    lock = lock_path_for(target)
    current = status_of(target)
    if current.running:
        try:
            _exchange(target, {"v": PROTOCOL_VERSION, "op": "shutdown"}, timeout=2.0)
        except ClientError:
            pass  # it may have died while answering; verified below
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not lock_is_held(lock) and not target.exists():
                return SessionStatus(False, "not_running", "session stopped", str(target))
            time.sleep(0.05)
        if not force:
            return SessionStatus(False, "unreachable",
                                 f"the session did not release {lock} within "
                                 f"{timeout:.0f}s", str(target))
    meta = read_lock(lock)
    pid = int(meta.get("pid") or 0)
    if not force:
        if pid:
            return SessionStatus(False, "not_running",
                                 f"no session running (lock names pid {pid})",
                                 str(target))
        return SessionStatus(False, "not_running", "no session running", str(target))
    if not pid or pid == os.getpid():
        return SessionStatus(False, "not_running",
                             "no session running — nothing to force", str(target))
    cmdline = ""
    try:
        cmdline = (Path(f"/proc/{pid}/cmdline").read_bytes()
                   .replace(b"\0", b" ").decode("utf-8", "replace"))
    except OSError:
        return SessionStatus(False, "not_running", f"pid {pid} is gone", str(target))
    if module_name() not in cmdline or "--serve" not in cmdline:
        return SessionStatus(False, "stale",
                             f"refusing to signal pid {pid}: its command line is not "
                             f"this session daemon",
                             str(target))
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return SessionStatus(False, "stale", f"cannot signal pid {pid}: {exc}", str(target))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not lock_is_held(lock):
            _unlink_socket(target)
            return SessionStatus(False, "not_running", f"forced pid {pid} to stop", str(target))
        time.sleep(0.05)
    return SessionStatus(False, "stale",
                         f"pid {pid} still holds the lock after SIGTERM", str(target))


def module_name() -> str:
    """``skaldir.warm_session`` regardless of how the module was launched."""
    return f"{__package__}.warm_session" if __package__ else Path(__file__).resolve().stem


def _unlink_socket(socket_path: Path) -> None:
    try:
        if stat.S_ISSOCK(os.lstat(socket_path).st_mode):
            socket_path.unlink()
    except (FileNotFoundError, OSError):
        pass


# --------------------------------------------------------------------------- #
# the daemon
# --------------------------------------------------------------------------- #


class _TtyStream:
    """``sys.stdout``/``sys.stderr`` shim: the client's tty state, the command's fd."""

    def __init__(self, stream: Any, tty: bool) -> None:
        self._stream = stream
        self._tty = bool(tty)

    # -- what commands actually touch --------------------------------------- #
    def isatty(self) -> bool:
        return self._tty

    def write(self, data: str) -> int:
        return self._stream.write(data)

    def writelines(self, lines: Any) -> None:
        self._stream.writelines(lines)

    def flush(self) -> None:
        self._stream.flush()

    def fileno(self) -> int:
        return self._stream.fileno()

    def close(self) -> None:  # pragma: no cover — commands do not close stdout
        self._stream.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<_TtyStream tty={self._tty} of {self._stream!r}>"


def front_door() -> tuple[str, Any]:
    """Resolve the product's console-script entry (``<pkg>.perf_lazy:main`` first).

    The daemon must run *the same entry* the console script does, otherwise
    ``--version`` (fast-pathed in the front door before ``argparse`` exists) would
    answer differently warm than cold.
    """
    import importlib

    package = __package__ or Path(__file__).resolve().parent.name
    for suffix in ("perf_lazy", "cli"):
        name = f"{package}.{suffix}"
        try:
            module = importlib.import_module(name)
        except Exception:  # noqa: BLE001 — a build without that module
            continue
        entry = getattr(module, "main", None)
        if callable(entry):
            return f"{name}:main", entry
    raise ClientError("no CLI front door found (neither perf_lazy nor cli)")


def _parser_dump(parser: Any) -> str:
    """A stable fingerprint of every default in the tree (for the dependence probes)."""
    from . import cli_contract

    return json.dumps(cli_contract.dump(parser), sort_keys=True)


def _preload_recorded() -> tuple[list[str], bool, Any, str]:
    """Import the CLI graph and build the parser inside a recording ``os.environ``.

    Returns ``(keys_read, wildcard, parser, dump)``. This is the *measurement* behind
    the parser cache: the key set is what the import and the build actually read, not
    a hand-written list, and iterating the whole environment anywhere in that path
    (``dict(os.environ)``) sets ``wildcard`` so the caller stops trusting any key list.

    The swap is done with ``mock.patch.object`` rather than a bare ``os.environ = recorder``:
    the recorder *is* the real environment with reads recorded, so nothing is hidden from the
    code under probe — but a plain assignment reads like "the environment was replaced", which
    is what B003 warns about, and it needs a hand-written ``try``/``finally`` to be undone.
    ``patch.object`` says what is meant (temporarily, for this block) and restores on the way
    out, including on an exception.
    """
    import importlib
    from unittest import mock

    package = __package__ or Path(__file__).resolve().parent.name
    recorder = _EnvRecorder(os.environ)
    parser: Any = None
    dump = ""
    with mock.patch.object(os, "environ", recorder):
        cli = importlib.import_module(f"{package}.cli")
        parser = cli.build_parser()
    if parser is not None:
        try:
            dump = _parser_dump(parser)
        except Exception:  # noqa: BLE001 — no contract dump: the probe reports why
            dump = ""
    keys = sorted(recorder.read | set(_ENV_FINGERPRINT_FLOOR))
    return keys, recorder.wildcard, parser, dump


def _fingerprint_keys(keys: Sequence[str], wildcard: bool) -> list[str]:
    """The measured key set, or the conservative fallback list when it is unusable."""
    chosen = set(_ENV_FINGERPRINT_FALLBACK) if wildcard else set(keys)
    return sorted(chosen | set(_ENV_FINGERPRINT_FLOOR))


def _parser_dependence(keys: Sequence[str], parser: Any, base_dump: str) -> dict[str, Any]:
    """Do this tree's build-time defaults change with the environment / the cwd?

    Built twice more at startup (two more parser trees) and compared as data, because a
    cached parser freezes whatever the build read: ``env``/``cwd`` say which axes the
    cache has to be keyed on, and ``warm status`` reports the answer so the claim is
    checkable rather than assumed.
    """
    if not base_dump:
        return {"probed": False, "reason": "no contract dump for the built parser",
                "env": True, "cwd": True}
    env_dependent = True
    cwd_dependent = True
    saved_env = dict(os.environ)
    saved_cwd = os.getcwd()
    try:
        from . import cli
    except Exception as exc:  # noqa: BLE001 — no cli: nothing can be cached
        return {"probed": False, "reason": f"{type(exc).__name__}: {exc}", "env": True, "cwd": True}
    try:
        for key in keys:
            os.environ[key] = f"/tmp/warm-probe/{key}"
        env_dependent = _parser_dump(cli.build_parser()) != base_dump
    except Exception:  # noqa: BLE001 — a failed probe means "assume dependent"
        env_dependent = True
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
    try:
        with tempfile.TemporaryDirectory(prefix="warm-probe-") as tmp:
            os.chdir(tmp)
            cwd_dependent = _parser_dump(cli.build_parser()) != base_dump
    except Exception:  # noqa: BLE001
        cwd_dependent = True
    finally:
        os.chdir(saved_cwd)
    return {"probed": True, "env": env_dependent, "cwd": cwd_dependent}


class WarmServer:
    """The long-lived process behind the socket: import once, fork per request."""

    def __init__(self, socket_path: Path | str, *, idle_timeout: float = DEFAULT_IDLE_TIMEOUT_S,
                 parser_cache: bool = True, log_path: Path | None = None,
                 max_rss_mb: float = DEFAULT_MAX_RSS_MB, preload: bool = True) -> None:
        self.socket_path = Path(socket_path)
        self.lock_path = lock_path_for(self.socket_path)
        self.log_path = Path(log_path) if log_path is not None else log_path_for(self.socket_path)
        self.idle_timeout = float(idle_timeout)
        self.parser_cache = bool(parser_cache)
        self.max_rss_mb = float(max_rss_mb)
        self.preload = bool(preload)
        self.sock: socket.socket | None = None
        self.lock_fd: int | None = None
        self.started_at = time.time()
        self.started_monotonic = time.monotonic()
        self.requests = 0
        self.last_command = ""
        self.last_exit_code = 0
        self.last_daemon_ms = 0.0
        self.last_activity = time.monotonic()
        self.entry_name = ""
        self.dependence: dict[str, Any] = {}
        self._stop = False
        self._parser: Any = None
        self._env_keys: list[str] = []
        self.env_wildcard = False
        self._build_fingerprint: dict[str, str] = {}
        self._build_cwd = ""

    # -- lifecycle ---------------------------------------------------------- #

    def acquire(self) -> None:
        """Take the lock, bind the socket, preload the front door. Raises on conflict."""
        ensure_private_dir(self.socket_path.parent)
        self.lock_fd = _lock_fd(self.lock_path)
        if self.lock_fd is None:
            raise ClientError(f"cannot open the session lock {self.lock_path}")
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ClientError(
                f"another warm session already owns {self.lock_path} "
                f"(pid {read_lock(self.lock_path).get('pid')}): {exc}"
            ) from exc
        if self.socket_path.exists():
            # We hold the lock, so no live daemon owns this path: a leftover file.
            _unlink_socket(self.socket_path)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(self.socket_path))
        except OSError as exc:
            sock.close()
            raise ClientError(f"cannot bind {self.socket_path}: {exc}") from exc
        os.chmod(self.socket_path, 0o600)
        sock.listen(16)
        self.sock = sock
        entry_name, _entry = _front_door()
        self.entry_name = entry_name
        if self.preload:
            self._preload()
        else:
            self._env_keys = _fingerprint_keys([], False)
        _write_lock(self.lock_path, {
            "pid": os.getpid(),
            "socket": str(self.socket_path),
            "protocol": PROTOCOL_VERSION,
            "entry": self.entry_name,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "idle_timeout_s": self.idle_timeout,
            "parser_cache": bool(self.parser_cache),
        })
        _log_line(self.log_path, f"session start pid={os.getpid()} socket={self.socket_path} "
                                 f"entry={self.entry_name} parser_cache={self.parser_cache} "
                                 f"env_keys={len(self._env_keys)} wildcard={self.env_wildcard} "
                                 f"dependence={self.dependence}")

    def _preload(self) -> None:
        """Pay the import graph and the argparse tree once — and measure what they read.

        That measurement (``_preload_recorded``) is what makes the cached parser safe to
        reuse: the session knows exactly which environment variables the build consumed
        and how the built tree reacts when they change, instead of guessing a list.
        """
        keys, wildcard, parser, dump = _preload_recorded()
        self.env_wildcard = wildcard
        self._env_keys = _fingerprint_keys(keys, wildcard)
        self._build_fingerprint = client_env_fingerprint(keys=self._env_keys)
        self._build_cwd = os.getcwd()
        if not self.parser_cache:
            return
        if parser is None:
            self.dependence = {"probed": False, "reason": "the parser tree could not be built",
                               "env": True, "cwd": True}
            self.parser_cache = False
            return
        self.dependence = _parser_dependence(self._env_keys, parser, dump)
        if (self.dependence.get("probed") and not self.dependence.get("env")
                and not self.dependence.get("cwd")):
            self.dependence["note"] = (
                "the built tree does not react to the environment: the cache is "
                "unconditional"
            )
        self._parser = parser

    def release(self) -> None:
        """Close the socket, drop the lock, remove our own socket file."""
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:  # pragma: no cover
                pass
            self.sock = None
        _unlink_socket(self.socket_path)
        if self.lock_fd is not None:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            except OSError:  # pragma: no cover
                pass
            os.close(self.lock_fd)
            self.lock_fd = None

    def _rss_mb(self) -> float:
        try:
            for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
        except (OSError, IndexError, ValueError):
            pass
        return 0.0

    def info(self, *, extra: dict | None = None) -> dict:
        """The status payload (used by ``status`` and ``warm status``)."""
        eligible, denied = warm_eligible_commands()
        payload: dict[str, Any] = {
            "pid": os.getpid(),
            "socket": str(self.socket_path),
            "lock": str(self.lock_path),
            "log": str(self.log_path),
            "protocol": PROTOCOL_VERSION,
            "entry": self.entry_name,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self.started_at)),
            "uptime_s": round(time.monotonic() - self.started_monotonic, 3),
            "requests": self.requests,
            "last_command": self.last_command,
            "last_exit_code": self.last_exit_code,
            "last_daemon_ms": round(self.last_daemon_ms, 3),
            "idle_timeout_s": self.idle_timeout,
            "idle_remaining_s": round(
                max(0.0, self.idle_timeout - (time.monotonic() - self.last_activity)), 3
            ),
            "rss_mb": round(self._rss_mb(), 1),
            "max_rss_mb": self.max_rss_mb,
            "pid_backend": "fork",
            "parser_cache": self.parser_cache,
            "parser_cache_probe": self.dependence,
            "env_keys": list(self._env_keys),
            "env_wildcard": self.env_wildcard,
            "env_fingerprint": dict(self._build_fingerprint) if self.parser_cache else {},
            "parser_cache_cwd": self._build_cwd if self.parser_cache else "",
            "eligible_commands": len(eligible),
            "denied_commands": len(denied),
            "argv0": product_prog(),
        }
        if extra:
            payload.update(extra)
        return payload

    # -- request loop ------------------------------------------------------- #

    def _use_cached_parser(self, request: dict) -> str:
        """Patch ``cli.build_parser`` in *this forked child* when the cache is valid.

        Fork isolation is what makes this safe: the patch exists only in the child
        that is about to run one command, and the cache is only used when the
        environment/cwd the defaults were built from match the client's.
        """
        if not self.parser_cache or self._parser is None:
            return "off"
        dependence = self.dependence or {}
        client_env = request.get("env")
        if dependence.get("env") and client_env_fingerprint(
            client_env if isinstance(client_env, dict) else {}, self._env_keys
        ) != self._build_fingerprint:
            return "rebuild"
        if dependence.get("cwd") and str(request.get("cwd") or "") != self._build_cwd:
            return "rebuild"
        try:
            from . import cli

            parser = self._parser

            def build_parser() -> Any:
                return parser

            build_parser._godsv_warm_singleton = True  # type: ignore[attr-defined]
            cli.build_parser = build_parser
        except Exception:  # noqa: BLE001 — no patch, so the child builds its own tree
            return "rebuild"
        return "hit"

    def _exec_in_child(self, conn: socket.socket, request: dict, buf: _Buffer, peer: dict) -> None:
        """Runs in the forked child: build the environment, run the command, answer."""
        started = time.monotonic()
        stdin_bytes = b""
        try:
            size = int(request.get("stdin_len") or 0)
            if size:
                stdin_bytes = _recv_payload(conn, buf, size, time.monotonic() + 30.0)
        except ClientError as exc:
            _send_header(conn, {"v": PROTOCOL_VERSION, "op": "exec", "status": "error",
                                "reason": f"cannot read the request payload: {exc}"})
            return
        argv = [str(item) for item in (request.get("argv") or [])][:MAX_ARGV_ITEMS]
        verdict = warm_verdict(argv)
        if verdict != "ok":
            assert isinstance(verdict, WarmDenied)
            _send_header(conn, {
                "v": PROTOCOL_VERSION,
                "op": "exec",
                "status": "refused",
                "reason": verdict.reason,
                "command": verdict.command,
                "daemon_pid": os.getpid(),
            })
            return
        env = request.get("env")
        if not isinstance(env, dict) or not env:
            fallback_env = _proc_environ(int(peer.get("pid") or 0))
            env = fallback_env or dict(os.environ)
        cwd = str(request.get("cwd") or "") or (_proc_cwd(int(peer.get("pid") or 0)) or os.getcwd())
        prog = str(request.get("prog") or product_prog())
        executable = str(request.get("executable") or sys.executable)
        out_file = tempfile.TemporaryFile()
        err_file = tempfile.TemporaryFile()
        cache = "off"
        exit_code = EXIT_INTERNAL
        try:
            os.chdir(cwd)
            os.environ.clear()
            os.environ.update({str(k): str(v) for k, v in list(env.items())[:MAX_ENV_ITEMS]})
            # Never let the warm child start another warm client (recursion guard):
            # every spelling of the switch is off, so a nested front door runs cold.
            for name in env_names("WARM"):
                os.environ[name] = "0"
            sys.argv = [prog, *argv]
            sys.executable = executable
            cache = self._use_cached_parser(request)
            _redirect_fds(out_file, err_file, tty_out=bool(request.get("tty_out")),
                          tty_err=bool(request.get("tty_err")), stdin_bytes=stdin_bytes)
            entry_name, entry = _front_door()
            self.entry_name = entry_name
            exit_code = _invoke(entry, argv)
        except BaseException as exc:  # noqa: BLE001 — the protocol must survive any crash
            exit_code = _crash_exit_code(exc)
        finally:
            _flush_streams()
        try:
            out_file.seek(0)
            err_file.seek(0)
            out_bytes = out_file.read()
            err_bytes = err_file.read()
        finally:
            out_file.close()
            err_file.close()
        daemon_ms = (time.monotonic() - started) * 1000.0
        _send_header(conn, {
            "v": PROTOCOL_VERSION,
            "op": "exec",
            "status": "ok",
            "exit_code": int(exit_code),
            "stdout_len": len(out_bytes),
            "stderr_len": len(err_bytes),
            "daemon_pid": os.getpid(),
            "daemon_ms": round(daemon_ms, 3),
            "cache": cache,
            "entry": self.entry_name,
            "command": _command_path(argv),
        }, tail=(out_bytes, err_bytes))

    def serve_forever(self, *, max_requests: int | None = None) -> int:
        """Accept, fork, answer — until idle, ``stop`` or a signal."""
        if self.sock is None:  # pragma: no cover — misuse
            raise ClientError("acquire() first")
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._on_signal)
        exit_code = EXIT_OK
        try:
            while not self._stop:
                timeout = max(0.0, min(1.0, self.idle_timeout
                                        - (time.monotonic() - self.last_activity)))
                try:
                    readable, _, _ = select.select([self.sock], [], [], timeout)
                except InterruptedError:  # pragma: no cover
                    continue
                self._reap()
                if not readable:
                    if time.monotonic() - self.last_activity >= self.idle_timeout:
                        _log_line(self.log_path,
                                  f"idle timeout after {self.requests} requests — exiting")
                        break
                    continue
                try:
                    conn, _ = self.sock.accept()
                except OSError as exc:  # pragma: no cover — a closed listener
                    if exc.errno in (errno.EBADF, errno.EINVAL):
                        break
                    continue
                self._dispatch(conn)
                if max_requests is not None and self.requests >= max_requests:
                    _log_line(self.log_path, f"reached --max-requests {max_requests} — exiting")
                    break
        finally:
            self.release()
        _log_line(self.log_path, f"session stop pid={os.getpid()} requests={self.requests}")
        return exit_code

    def _on_signal(self, signum: int, _frame: Any) -> None:
        _log_line(self.log_path, f"signal {signum} — stopping")
        self._stop = True

    def _reap(self) -> None:
        """Clear finished forked children without ever blocking the loop."""
        while True:
            try:
                pid, _status = os.waitpid(-1, os.WNOHANG)
            except (ChildProcessError, OSError):
                return
            if pid == 0:
                return

    def _dispatch(self, conn: socket.socket) -> None:
        """Read one request on ``conn`` and answer it (forking for ``exec``)."""
        buf = _Buffer()
        deadline = time.monotonic() + 30.0
        try:
            request = _recv_header(conn, buf, deadline)
        except ClientError as exc:
            _safe_send(conn, {"v": PROTOCOL_VERSION, "status": "error", "reason": str(exc)})
            conn.close()
            return
        peer = _peer_credentials(conn)
        op = str(request.get("op") or "")
        if request.get("v") != PROTOCOL_VERSION:
            _safe_send(conn, {"v": PROTOCOL_VERSION, "op": op, "status": "error",
                              "reason": f"protocol mismatch: session speaks v{PROTOCOL_VERSION}, "
                                        f"client sent v{request.get('v')!r}"})
            conn.close()
            return
        if peer and peer.get("uid") != os.getuid():
            _log_line(self.log_path, f"refused uid {peer.get('uid')} (pid {peer.get('pid')})")
            _safe_send(conn, {"v": PROTOCOL_VERSION, "op": op, "status": "error",
                              "reason": f"uid {peer.get('uid')} is not the session owner"})
            conn.close()
            return
        if op == "ping":
            _safe_send(conn, {"v": PROTOCOL_VERSION, "op": "ping", "status": "ok",
                              "daemon_pid": os.getpid(), "protocol": PROTOCOL_VERSION})
            conn.close()
            return
        if op == "status":
            _safe_send(conn, {"v": PROTOCOL_VERSION, "op": "status", "status": "ok",
                              "daemon_pid": os.getpid(), "info": self.info()})
            conn.close()
            return
        if op == "shutdown":
            _safe_send(conn, {"v": PROTOCOL_VERSION, "op": "shutdown", "status": "ok",
                              "daemon_pid": os.getpid()})
            conn.close()
            self._stop = True
            return
        if op != "exec":
            _safe_send(conn, {"v": PROTOCOL_VERSION, "op": op, "status": "error",
                              "reason": f"unknown op {op!r}"})
            conn.close()
            return
        # exec: fork so the command gets a fresh process and the daemon keeps its imports.
        try:
            pid = os.fork()
        except OSError as exc:
            _safe_send(conn, {"v": PROTOCOL_VERSION, "op": "exec", "status": "error",
                              "reason": f"cannot fork a worker: {exc}"})
            conn.close()
            return
        if pid == 0:  # child
            try:
                if self.sock is not None:
                    try:
                        self.sock.close()
                    except OSError:  # pragma: no cover
                        pass
                self._exec_in_child(conn, request, buf, peer)
            except BaseException:  # noqa: BLE001 — a worker must never take the session down
                try:
                    _safe_send(conn, {"v": PROTOCOL_VERSION, "op": "exec", "status": "error",
                                      "reason": "the worker failed before answering"})
                except Exception:  # pragma: no cover
                    pass
                os._exit(EXIT_INTERNAL)
            finally:
                try:
                    conn.close()
                except OSError:  # pragma: no cover
                    pass
            os._exit(0)
        conn.close()
        self.requests += 1
        self.last_activity = time.monotonic()
        self.last_command = _command_path([str(item) for item in (request.get("argv") or [])])
        if self.requests % 25 == 0 and self.max_rss_mb > 0:
            rss = self._rss_mb()
            if rss > self.max_rss_mb:
                _log_line(self.log_path,
                          f"rss {rss:.0f} MB above the {self.max_rss_mb:.0f} MB guard "
                          f"— exiting")
                self._stop = True


def _redirect_fds(out_file: Any, err_file: Any, *, tty_out: bool, tty_err: bool,
                  stdin_bytes: bytes) -> None:
    """Point fd 1/2 at the capture files and give Python the same view cold has."""
    import io

    encoding = locale.getpreferredencoding(False) or "utf-8"
    os.dup2(out_file.fileno(), 1)
    os.dup2(err_file.fileno(), 2)
    sys.stdout = _TtyStream(
        io.TextIOWrapper(io.FileIO(1, "w", closefd=False), encoding=encoding,
                         line_buffering=bool(tty_out)),
        tty_out,
    )
    sys.stderr = _TtyStream(
        io.TextIOWrapper(io.FileIO(2, "w", closefd=False), encoding=encoding,
                         line_buffering=bool(tty_err)),
        tty_err,
    )
    sys.stdin = io.TextIOWrapper(io.BytesIO(stdin_bytes), encoding=encoding, newline=None)


def _flush_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001 — a closed/broken stream must not hide the answer
            pass


def _invoke(entry: Any, argv: Sequence[str]) -> int:
    """Call the front door with the argv we were given, mirroring Python's exit rules."""
    try:
        result = entry(list(argv))
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return EXIT_OK
        if isinstance(code, int):
            return int(code)
        sys.stderr.write(f"{code}\n")
        return EXIT_USAGE
    except KeyboardInterrupt:  # pragma: no cover — nothing sends SIGINT to a worker
        return 130
    return EXIT_OK if result is None else int(result)


def _crash_exit_code(exc: BaseException) -> int:
    """Reproduce what an uncaught exception does to the cold process' exit code."""
    import traceback

    if isinstance(exc, SystemExit):
        code = exc.code
        if code is None:
            return EXIT_OK
        if isinstance(code, int):
            return int(code)
        sys.stderr.write(f"{code}\n")
        return EXIT_USAGE
    if isinstance(exc, KeyboardInterrupt):  # pragma: no cover
        return 130
    traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
    return EXIT_USAGE


def _send_header(conn: socket.socket, header: dict, *, tail: tuple[bytes, ...] = ()) -> None:
    payload = _encode_line(header)
    try:
        conn.sendall(payload + b"".join(tail))
    except OSError:  # pragma: no cover — the client hung up while we answered
        pass


_front_door = front_door  # internal spelling kept for the module's own call sites
_safe_send = _send_header


# --------------------------------------------------------------------------- #
# daemon entry point (``python -m <pkg>.warm_session --serve``)
# --------------------------------------------------------------------------- #


def daemon_main(argv: Sequence[str] | None = None) -> int:
    """Entry point of the daemon process (the ``warm start`` child)."""
    import argparse

    parser = argparse.ArgumentParser(prog=f"{product_prog()} warm-serve",
                                     description="warm session daemon")
    parser.add_argument("--serve", action="store_true",
                        help="run the daemon (the shape `warm start` spawns; default anyway)")
    parser.add_argument("--socket", default=None)
    parser.add_argument("--idle-timeout", type=float, default=DEFAULT_IDLE_TIMEOUT_S)
    parser.add_argument("--log", default=None)
    parser.add_argument("--no-parser-cache", action="store_true")
    parser.add_argument("--no-preload", action="store_true")
    parser.add_argument("--max-requests", type=int, default=None)
    parser.add_argument("--max-rss-mb", type=float, default=DEFAULT_MAX_RSS_MB)
    args = parser.parse_args(list(argv) if argv is not None else None)
    socket_path = Path(args.socket) if args.socket else default_socket_path()
    server = WarmServer(
        socket_path,
        idle_timeout=args.idle_timeout,
        parser_cache=not args.no_parser_cache,
        log_path=Path(args.log) if args.log else None,
        max_rss_mb=args.max_rss_mb,
        preload=not args.no_preload,
    )
    try:
        server.acquire()
    except ClientError as exc:
        _log_line(server.log_path, f"refusing to start: {exc}")
        sys.stderr.write(f"{product_prog()} warm-serve: {exc}\n")
        return EXIT_INTERNAL
    return server.serve_forever(max_requests=args.max_requests)


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m <pkg>.warm_session``: the daemon entry, nothing else."""
    return daemon_main(argv)


if __name__ == "__main__":  # pragma: no cover — daemon entry
    raise SystemExit(main())
