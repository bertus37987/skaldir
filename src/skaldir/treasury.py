"""Treasury (CORE-PLAN §6) — the trust boundary: the owner grants, the agent
never sees secrets and never moves money unauthorized.

Zone A (credentials): a vault encrypted at rest (Fernet, key derived from a
master secret that is never stored) plus **broker handles**
(``secret://service/name``). Usage is *injection*: the broker runs a command
in the sandbox with the secret only in the child environment and scrubs the
secret from everything that comes back. Grants: an agent request becomes a
pending grant a human approves; entries marked ``grant:required`` need an
active grant for every use.

Zone B (logins): login material and TOTP seeds live in the vault; the broker
mints short-lived RFC 6238 codes — the agent gets a code, never the seed.
Browser profiles / device alerts are operational (see docs), not code here.

Zone C (money): staged spending. Stufe 0 = every spend needs a human
approval; Stufe 1 = whitelisted micro-purchases inside budget auto-approve;
Stufe 2 = mandate caps (e.g. 20 per tx, 50 per week). Executing requires an
approved request and a valid **verifiable intent** (amount/merchant/expiry
must match — the AP2 idea, modeled minimally). Caps are enforced in code and
every decision is an audit event.

Mechanism only: no real credentials, no real payment rails. Connecting real
material is an explicit owner step (``skaldir vault add`` prompts with
getpass — the secret never touches argv, chat or history).

Path arguments are data, never instructions. ``Path("")`` **is** ``Path(".")``, so a blank
``--db ""``/``--vault ""`` must not be read as "the current directory": it is refused with exit 1
(:func:`state_path`), and no code path below ever chmods a directory the caller did not name —
:func:`_restrict` only ever restricts a *file*, and the vault's own directory is tightened by
:func:`_restrict_dir` only when it is neither the working directory nor a shared one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import sqlite3
import stat
import struct
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from .cli_contract import EXIT_USAGE, CommandError
from .env import child_env, getenv
from .events import EventStore, SecretLeakError
from .llm import BudgetTracker, LLMClient
from .promptrun import run_prompt
from .prompts import PromptRegistry

try:  # POSIX-only (Fedora/Linux); degrade gracefully elsewhere
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

DEFAULT_HOURLY_LIMIT = 30
REDACTED = "[REDACTED:treasury]"

TIER_HUMAN = 0
TIER_WHITELIST = 1
TIER_MANDATE = 2


class VaultError(RuntimeError):
    """Wrong key, unknown/expired/revoked handle, or tampered data."""


#: Path-argument spellings that name no file at all: ``Path("")`` is ``Path(".")``, and a path
#: that resolves to the current working directory (or to any directory) is not a state file.
_EMPTY_PATH_ARGUMENTS = frozenset({"", ".", "./", "..", "../"})


class StatePathError(CommandError, ValueError):
    """A caller-supplied state path (``--db``/``--vault``/``--events``) does not name a file.

    Carries exit code 1 (``USAGE``) as the CLI's own verdict type, so a handler that builds
    :class:`TreasuryState` or :class:`Vault` straight from ``argparse`` answers ``ok: false`` /
    ``exit_code: 1`` with a reason instead of crashing — and, above all, the refusal happens
    *before* anything can chmod a path the caller never named.
    """


def state_path(raw: str | Path | None, *, argument: str) -> Path:
    """Resolve one state-file path argument; refuse anything that does not name a file.

    ``Path("") == Path(".")`` is the whole bug this guard exists for: ``--db ""`` reached
    ``_restrict(Path(""), 0o600)``, which chmod'ed the **current working directory** to ``0600``
    (no execute bit) while the envelope still answered ``ok: true`` — every later process started
    there died with ``EACCES``. An empty value is not "the default" either: the caller asked for a
    path, and quietly using another one hides the mistake (a GUI sending an unset field would look
    like it worked). An existing **directory** is refused for the same reason: a state file is a
    file, and no directory may be adopted as one.

    The message is written to stderr as well, because ``cli_contract.run_command`` does not print a
    handler's error record in text mode (``--json`` carries it in the envelope).
    """
    text = "" if raw is None else str(raw).strip()
    if text in _EMPTY_PATH_ARGUMENTS:
        message = (
            f"{argument}: the path is empty — Path('') is the current directory "
            f"({Path.cwd()}), which skaldir must never treat as a state file (and never chmod). "
            f"Give the file you mean, e.g. {argument} {Path.cwd() / 'treasury.db'}"
        )
        print(f"skaldir: {message}", file=sys.stderr)
        raise StatePathError(
            EXIT_USAGE,
            message,
            hint="a state path names a file, not the directory the command runs in",
            details={"argument": argument, "value": str(raw)},
        )
    path = Path(text).expanduser()
    try:
        resolves_to_cwd = path.resolve() == Path.cwd().resolve()
    except OSError:  # pragma: no cover — exotic filesystems
        resolves_to_cwd = False
    if path.is_dir() or resolves_to_cwd:
        message = (
            f"{argument}: {text!r} is a directory, not a file — refusing to use it as a state file "
            f"(and to touch its mode)"
        )
        print(f"skaldir: {message}", file=sys.stderr)
        raise StatePathError(
            EXIT_USAGE,
            message,
            hint=f"name the FILE you mean, e.g. {argument} {Path(text).resolve() / 'treasury.db'}",
            details={"argument": argument, "value": text},
        )
    return path


def _restrict(path: Path, mode: int) -> None:
    """Best-effort chmod of a **file**; never fatal, and never applied to a directory.

    Every caller restricts a file this module owns (vault, lock file, temp file, sqlite db and its
    ``-wal``/``-shm`` siblings). A directory that arrives anyway — the working directory, which a
    blank path collapses to, is the measured case — keeps its mode; directories go through
    :func:`_restrict_dir`, which has the guards.
    """
    try:
        if not path.is_file():
            return
        os.chmod(path, mode)
    except OSError:
        pass


def _restrict_dir(path: Path, mode: int) -> None:
    """Best-effort chmod of a directory we own; refuses the cwd and shared directories.

    Guards, in order: not a directory → skip; the process working directory (``Path('')`` is
    ``Path('.')``, and a bare relative ``--vault vault.json`` has ``.`` as its parent) → skip,
    it is the caller's to keep; a shared directory (sticky bit or group/other write, ``/tmp`` and
    friends) → skip, tightening it would break every other user of it. The caller additionally
    passes only an **absolute** path (see :meth:`Vault.__init__`): a directory reachable by a
    relative name sits inside the caller's working tree, not in godsv's runtime.
    """
    try:
        if not path.is_dir():
            return
        if path.resolve() == Path.cwd().resolve():
            return
        if path.stat().st_mode & (stat.S_ISVTX | stat.S_IWOTH | stat.S_IWGRP):
            return
        os.chmod(path, mode)
    except OSError:
        pass


def _fernet_key(master: str | bytes) -> bytes:
    raw = master.encode("utf-8") if isinstance(master, str) else master
    derived = hashlib.scrypt(raw, salt=b"godsv-vault-v1", n=2**14, r=8, p=1, dklen=32)
    return base64.urlsafe_b64encode(derived)


@dataclass
class HandleInfo:
    handle: str
    service: str
    name: str
    kind: str
    scopes: list[str] = field(default_factory=list)
    description: str = ""
    created: float = 0.0
    expires_at: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class Vault:
    """Encrypted-at-rest credential store; the API exposes metadata only."""

    def __init__(self, path: str | Path, master_key: str | bytes):
        self.path = state_path(path, argument="--vault")
        self._fernet = Fernet(_fernet_key(master_key))
        self._lock = threading.RLock()
        self._lock_path = self.path.with_suffix(".lock")
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.is_absolute():
                # Tighten the directory that holds the vault (SECURITY.md T-A4) — but only one the
                # caller named in full: a relative parent is the caller's own working tree, and
                # `_restrict_dir` refuses the cwd and shared directories on top of that.
                _restrict_dir(self.path.parent, 0o700)
            self._write({"version": 1, "entries": {}})

    @contextmanager
    def _file_lock(self):
        """Cross-process lock for read-modify-write cycles (best effort)."""
        if fcntl is None:
            yield
            return
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "w", encoding="ascii") as handle:
            _restrict(self._lock_path, 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    # -- owner side ---------------------------------------------------------
    def add(
        self,
        *,
        service: str,
        name: str,
        secret: str,
        scopes: list[str] | tuple[str, ...] = ("use",),
        kind: str = "api_key",
        description: str = "",
        expires_at: float | None = None,
        totp_seed: str | None = None,
    ) -> str:
        handle = f"secret://{service}/{name}"
        payload: dict = {"secret": secret}
        if totp_seed:
            payload["totp_seed"] = totp_seed
        entry = {
            "service": service,
            "name": name,
            "kind": kind,
            "scopes": list(scopes),
            "description": description,
            "created": time.time(),
            "expires_at": expires_at,
            "ciphertext": self._fernet.encrypt(
                json.dumps(payload, ensure_ascii=False).encode("utf-8")
            ).decode("ascii"),
        }
        with self._lock, self._file_lock():
            data = self._read()
            data["entries"][handle] = entry
            self._write(data)
        return handle

    def revoke(self, handle: str) -> bool:
        with self._lock, self._file_lock():
            data = self._read()
            if handle not in data["entries"]:
                return False
            del data["entries"][handle]
            self._write(data)
        return True

    # -- shared (agent-visible) side ---------------------------------------
    def list_handles(self) -> list[HandleInfo]:
        with self._lock:
            data = self._read()
        infos = [self._info(handle, entry) for handle, entry in data["entries"].items()]
        return sorted(infos, key=lambda info: info.handle)

    def describe(self, handle: str) -> HandleInfo | None:
        with self._lock:
            data = self._read()
        entry = data["entries"].get(handle)
        return self._info(handle, entry) if entry else None

    def _info(self, handle: str, entry: dict) -> HandleInfo:
        return HandleInfo(
            handle=handle,
            service=entry["service"],
            name=entry["name"],
            kind=entry["kind"],
            scopes=list(entry.get("scopes", [])),
            description=entry.get("description", ""),
            created=entry.get("created", 0.0),
            expires_at=entry.get("expires_at"),
        )

    # -- broker side (never exposed to the agent) --------------------------
    def _decrypt(self, handle: str) -> dict:
        with self._lock:
            data = self._read()
        entry = data["entries"].get(handle)
        if entry is None:
            raise VaultError(f"unknown handle: {handle}")
        if entry.get("expires_at") is not None and time.time() > entry["expires_at"]:
            raise VaultError(f"handle expired: {handle}")
        try:
            payload = json.loads(self._fernet.decrypt(entry["ciphertext"].encode("ascii")))
        except InvalidToken as exc:
            raise VaultError("decryption failed (wrong key?)") from exc
        return payload

    def _read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        """Write the vault atomically, with the private mode set at *creation*.

        ``write_text()`` + ``chmod()`` left the file at the process umask (usually
        ``0644``) between the two calls, and a crash in that window left it that way
        permanently — on a runtime dir that is ``0755`` that is a readable copy of
        every credential's ciphertext. ``os.open(..., 0o600)`` closes the window; the
        ``fchmod`` covers a temporary left over from an earlier run.
        """
        tmp = self.path.with_suffix(".tmp")
        payload = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
        handle = None
        try:
            handle = os.fdopen(
                os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb"
            )
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            if handle is not None:
                handle.close()
            tmp.unlink(missing_ok=True)
            raise
        handle.close()
        tmp.replace(self.path)
        _restrict(self.path, 0o600)


@dataclass
class BrokerResult:
    ok: bool
    denied: bool = False
    stdout: str = ""
    detail: str = ""


class _EventRecorder:
    """The shared append-only event trail of the broker and the spend ledger.

    ``events``/``session_id``/``_step`` belong to the concrete class; this mixin
    only knows how to append one step to them (one implementation, not two).
    """

    events: EventStore | None
    session_id: str
    _step: int

    def _record(self, kind: str, payload: dict) -> None:
        if self.events is None:
            return
        self._step += 1
        try:
            self.events.append(self.session_id, self._step, kind, payload)
        except SecretLeakError:
            # fail-safe: never crash the flow, never persist raw material
            self.events.append_redacted(self.session_id, self._step, kind, payload)


class Broker(_EventRecorder):
    """Injects vault material at the point of use; never returns it."""

    def __init__(
        self,
        vault: Vault,
        state: TreasuryState,
        *,
        events: EventStore | None = None,
        session_id: str = "treasury",
    ) -> None:
        self.vault = vault
        self.state = state
        self.events = events
        self.session_id = session_id
        self._step = 0

    def _deny(self, handle: str, reason: str, purpose: str) -> BrokerResult:
        self._record("treasury_denied", {"handle": handle, "reason": reason, "purpose": purpose})
        return BrokerResult(ok=False, denied=True, detail=reason)

    def _grant_ok(self, handle: str, tool_id: str | None) -> bool:
        if tool_id is None:
            return self.state.has_active_grant(handle)
        return self.state.has_active_grant_for_tool(handle, tool_id)

    def run_with_secret(
        self,
        handle: str,
        *,
        argv: list[str],
        env_var: str,
        purpose: str,
        tool_id: str | None = None,
        require_scopes: tuple[str, ...] = (),
        workdir: str | Path = ".",
        timeout: float = 30.0,
        hourly_limit: int = DEFAULT_HOURLY_LIMIT,
    ) -> BrokerResult:
        """Run ``argv`` in the sandbox with the secret in the child env only.

        With ``tool_id`` the call only proceeds under a grant that covers this
        tool (tool-scoped or unscoped). The returned output has every
        occurrence of the secret scrubbed.
        """
        env, denied = self._prepare_materials(
            [(handle, env_var)],
            purpose=purpose,
            tool_id=tool_id,
            hourly_limit=hourly_limit,
            scopes_for={handle: tuple(require_scopes)},
        )
        if denied is not None:
            return denied
        return self._run_with_env(env, argv=argv, purpose=purpose, workdir=workdir, timeout=timeout)

    def run_with_secrets(
        self,
        requests: list[tuple[str, str]] | tuple[tuple[str, str], ...],
        *,
        argv: list[str],
        purpose: str,
        tool_id: str | None = None,
        workdir: str | Path = ".",
        timeout: float = 30.0,
        hourly_limit: int = DEFAULT_HOURLY_LIMIT,
    ) -> BrokerResult:
        """Run ``argv`` once with EVERY declared handle injected (Wave 6).

        A tool that needs more than one connection cannot be run once per handle, so the gate is
        applied to all of them before the single run: every handle must exist, carry an active
        grant that covers this tool (when its scopes say ``grant:required``) and stay under its
        rate limit — otherwise nothing runs and the denial names the handle. The material lives in
        the child env only, the output is scrubbed, and the use is recorded like any other.
        """
        env, denied = self._prepare_materials(
            list(requests), purpose=purpose, tool_id=tool_id, hourly_limit=hourly_limit
        )
        if denied is not None:
            return denied
        return self._run_with_env(env, argv=argv, purpose=purpose, workdir=workdir, timeout=timeout)

    def _prepare_materials(
        self,
        requests: list[tuple[str, str]],
        *,
        purpose: str,
        tool_id: str | None,
        hourly_limit: int,
        scopes_for: dict[str, tuple[str, ...]] | None = None,
    ) -> tuple[dict[str, str], BrokerResult | None]:
        """Gate every (handle, env_var) and return the child-env mapping or the denial.

        Fail-closed and order-preserving: unknown handle → grant → scopes → rate limit →
        decrypt. Nothing is injected before every handle passed.
        """
        scopes_for = scopes_for or {}
        env: dict[str, str] = {}
        for handle, env_var in requests:
            info = self.vault.describe(handle)
            if info is None:
                return {}, self._deny(handle, "unknown handle", purpose)
            if "grant:required" in info.scopes and not self._grant_ok(handle, tool_id):
                return {}, self._deny(handle, "no active grant for this handle", purpose)
            missing = [scope for scope in scopes_for.get(handle, ()) if scope not in info.scopes]
            if missing:
                return {}, self._deny(handle, f"missing scopes: {', '.join(missing)}", purpose)
            if not self.state.bump_usage(handle, limit=hourly_limit):
                return {}, self._deny(handle, "rate limit reached", purpose)
            try:
                payload = self.vault._decrypt(handle)
            except VaultError as exc:
                return {}, self._deny(handle, str(exc), purpose)
            env[str(env_var)] = str(payload.get("secret", ""))
        return env, None

    def _run_with_env(
        self,
        env: dict[str, str],
        *,
        argv: list[str],
        purpose: str,
        workdir: str | Path,
        timeout: float,
    ) -> BrokerResult:
        """The single sandboxed run with the resolved material in the child env only."""
        from .gates import _run as _sandbox_run  # local import: avoid hard coupling

        code, output, sandboxed = _sandbox_run(
            list(argv), workdir=Path(workdir), timeout=timeout, extra_env=dict(env)
        )
        if not sandboxed:
            # fail-closed (P1/B2): a secret is never injected into an
            # unsandboxed child process
            handle = next(iter(env), "")
            return self._deny(
                handle, "sandbox unavailable — secret injection refused (fail-closed)", purpose
            )
        scrubbed = output
        replaced = 0
        for secret in env.values():
            if not secret:
                continue
            scrubbed, count = self._scrub(scrubbed, secret)
            replaced += count
        purpose_clean = purpose
        for secret in env.values():
            if secret:
                purpose_clean, _ = self._scrub(purpose_clean, secret)
        self._record(
            "treasury_use",
            {
                "handle": next(iter(env), ""),
                "handles": sorted(env.keys()),
                "purpose": purpose_clean,
                "ok": code == 0,
                "scrubbed": replaced,
            },
        )
        return BrokerResult(
            ok=code == 0, stdout=scrubbed, detail=scrubbed[:200] or f"exit {code}"
        )

    def selftest(self, handle: str, *, purpose: str = "credential-selftest") -> BrokerResult:
        """Owner-triggered credential self-test (spec §16).

        Proves the whole chain with **no external call**: the handle exists,
        the vault decrypts, and the secret reaches an **isolated subprocess
        environment** — while the output stays scrubbed. No grant is needed
        (the vault owner checks their own credential); every run is
        audit-logged like any other use.
        """
        info = self.vault.describe(handle)
        if info is None:
            return self._deny(handle, "unknown handle", purpose)
        try:
            payload = self.vault._decrypt(handle)
        except VaultError as exc:
            return self._deny(handle, str(exc), purpose)
        secret = str(payload.get("secret", ""))
        if not secret:
            return self._deny(handle, "empty secret", purpose)
        import shutil as _shutil

        from .gates import _run as _sandbox_run  # local import: avoid hard coupling

        shell = _shutil.which("sh") or "/bin/sh"
        code, output, sandboxed = _sandbox_run(
            [shell, "-c", 'test -n "$SKALDIR_TEST_VALUE" && echo injection-ok'],
            workdir=Path("."),
            timeout=15.0,
            extra_env=child_env({"TEST_VALUE": secret}),
        )
        scrubbed, replaced = self._scrub(output, secret)
        detail = scrubbed.strip() or f"exit {code}"
        self._record(
            "treasury_selftest",
            {"handle": handle, "ok": code == 0, "sandboxed": bool(sandboxed), "scrubbed": replaced},
        )
        return BrokerResult(ok=code == 0, stdout=scrubbed, detail=detail[:200])

    def totp_code(
        self, handle: str, *, purpose: str, at_time: float | None = None, digits: int = 6
    ) -> str | None:
        """Mint a short-lived TOTP code from the vault seed (seed never leaves)."""
        info = self.vault.describe(handle)
        if info is None:
            self._deny(handle, "unknown handle", purpose)
            return None
        if "grant:required" in info.scopes and not self.state.has_active_grant(handle):
            self._deny(handle, "no active grant for this handle", purpose)
            return None
        if not self.state.bump_usage(handle, limit=DEFAULT_HOURLY_LIMIT):
            self._deny(handle, "rate limit reached", purpose)
            return None
        try:
            payload = self.vault._decrypt(handle)
        except VaultError as exc:
            self._deny(handle, str(exc), purpose)
            return None
        seed = payload.get("totp_seed")
        if not seed:
            self._deny(handle, "no TOTP seed stored", purpose)
            return None
        code = totp_now(str(seed), digits=digits, at_time=at_time)
        self._record(
            "treasury_use", {"handle": handle, "purpose": purpose, "ok": True, "totp": True}
        )
        return code

    # -- grants -------------------------------------------------------------
    def request_grant(self, handle: str, *, purpose: str, ttl_seconds: int = 3600) -> str:
        grant_id = self.state.add_grant_request(handle, purpose, ttl_seconds)
        self._record(
            "treasury_grant_requested",
            {"handle": handle, "purpose": purpose, "grant": grant_id},
        )
        return grant_id

    def approve_grant(self, grant_id: str, *, by: str = "owner") -> bool:
        ok = self.state.approve_grant(grant_id, by=by)
        if ok:
            self._record("treasury_grant_approved", {"grant": grant_id, "by": by})
        return ok

    def deny_grant(self, grant_id: str, *, by: str = "owner", reason: str = "") -> bool:
        ok = self.state.deny_grant(grant_id, by=by)
        if ok:
            self._record("treasury_grant_denied", {"grant": grant_id, "by": by, "reason": reason})
        return ok

    @staticmethod
    def _scrub(text: str, secret: str) -> tuple[str, bool]:
        if not secret:
            return text, False
        variants = {secret}
        if len(secret) >= 8:  # short values would over-redact unrelated text
            stripped = secret.strip()
            if stripped:
                variants.add(stripped)
            variants.add(quote(secret, safe=""))
        replaced = False
        for variant in variants:
            if variant and variant in text:
                text = text.replace(variant, REDACTED)
                replaced = True
        return text, replaced


def totp_now(
    seed_base32: str, *, digits: int = 6, period: int = 30, at_time: float | None = None
) -> str:
    """RFC 6238 TOTP (HMAC-SHA1). Deterministic given ``at_time``."""
    normalized = seed_base32.strip().upper().replace(" ", "")
    padded = normalized + "=" * (-len(normalized) % 8)
    key = base64.b32decode(padded)
    counter = int((time.time() if at_time is None else at_time) // period)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


@dataclass
class SpendRules:
    tier: int = TIER_HUMAN
    per_tx_cap: float | None = None
    weekly_cap: float | None = None
    monthly_cap: float | None = None
    whitelist: list[str] = field(default_factory=list)


PRESETS: dict[str, SpendRules] = {
    "stufe0": SpendRules(tier=TIER_HUMAN),
    "stufe1": SpendRules(tier=TIER_WHITELIST, per_tx_cap=5.0, weekly_cap=20.0),
    "stufe2": SpendRules(tier=TIER_MANDATE, per_tx_cap=20.0, weekly_cap=50.0, whitelist=["infra"]),
}


@dataclass
class SpendIntent:
    """The verifiable intent (AP2-style): constraints checked at execution."""

    amount: float
    currency: str
    merchant: str
    purpose: str = ""
    expires_at: float | None = None
    intent_id: str = ""

    def __post_init__(self) -> None:
        if not self.intent_id:
            self.intent_id = f"intent-{uuid.uuid4().hex[:12]}"


@dataclass
class SpendRequest:
    request_id: str
    amount: float
    currency: str
    merchant: str
    purpose: str
    status: str  # pending_human | approved | denied | completed
    reason: str = ""
    approved_by: str = ""
    intent_id: str = ""
    intent_expires_at: float | None = None
    created: float = 0.0
    completed_at: float | None = None


def secret_env_var(handle: str) -> str:
    """The child-env variable a tool reads for ``handle`` — deterministic, no second config.

    ``secret://provider/account`` → ``SKALDIR_SECRET_PROVIDER_ACCOUNT``. A caller that knows the
    tool's own variable name passes it via ``secret_env`` (the manifest's ``required_secrets``
    carries handles only, as the design requires: handles, never material).
    """

    slug = "".join(ch if ch.isalnum() else "_" for ch in str(handle).removeprefix("secret://"))
    return f"SKALDIR_SECRET_{slug.upper()}"


def run_tool_with_secrets(
    *,
    argv: list[str],
    runtime: str | Path,
    handles: list[str] | tuple[str, ...],
    tool_id: str = "",
    purpose: str = "",
    workdir: str | Path = ".",
    timeout: float = 30.0,
    master_key: str | None = None,
    secret_env: dict[str, str] | None = None,
    events: EventStore | None = None,
    session_id: str = "treasury",
    hourly_limit: int = DEFAULT_HOURLY_LIMIT,
) -> dict:
    """Run one tool call under the Treasury gate (Wave 6: the treasury reaches real tool calls).

    Measured 17.09.: ``Broker.run_with_secret`` had **no production caller** — a generated tool
    could declare ``required_secrets`` and still run outside any grant. A tool with declared
    handles now runs only through the broker: every handle is gate-checked before the single run,
    the material lives in the child env only (never returned), the output is scrubbed, and the use
    is recorded as ``treasury_use`` / ``treasury_denied``. Tools without handles do not belong
    here — they run through :func:`skaldir.execution.run_local` as before.
    """

    runtime_dir = Path(runtime)
    key = master_key or getenv("VAULT_KEY")
    if not key:
        return {
            "ok": False,
            "denied": True,
            "reason": "SKALDIR_VAULT_KEY not set (master secret — never put it in argv/history)",
            "output": "",
            "env": {},
        }
    env = {handle: (secret_env or {}).get(handle) or secret_env_var(handle) for handle in handles}
    broker = Broker(
        Vault(runtime_dir / "vault.json", key),
        TreasuryState(runtime_dir / "treasury.db"),
        events=events,
        session_id=session_id,
    )
    try:
        result = broker.run_with_secrets(
            [(handle, env[handle]) for handle in handles],
            argv=list(argv),
            purpose=purpose or f"tool:{tool_id or 'unknown'}",
            tool_id=tool_id or None,
            workdir=workdir,
            timeout=timeout,
            hourly_limit=hourly_limit,
        )
    finally:
        broker.state.close()
    return {
        "ok": bool(result.ok),
        "denied": bool(result.denied),
        "reason": result.detail if result.denied else "",
        "output": result.stdout,
        "env": env,
    }


class TreasuryState:
    """SQLite state: usage windows, grants, spend requests (thread-safe)."""

    def __init__(self, path: str | Path):
        # Path("") is Path("."): a blank --db used to chmod the working directory to 0600 here.
        self.path = str(state_path(path, argument="--db"))
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS usage (handle TEXT PRIMARY KEY,"
                " window_start REAL, count INTEGER)"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS grants (id TEXT PRIMARY KEY, handle TEXT, purpose TEXT,"
                " ttl INTEGER, status TEXT, created REAL, decided REAL, by TEXT, tool_id TEXT)"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS spends (id TEXT PRIMARY KEY, amount REAL,"
                " currency TEXT, merchant TEXT, purpose TEXT, status TEXT, reason TEXT,"
                " approved_by TEXT,"
                " intent_id TEXT, intent_expires_at REAL, created REAL, completed_at REAL)"
            )
            # migrate pre-tool_id grant tables in place (idempotent)
            grant_cols = [row[1] for row in self._db.execute("PRAGMA table_info(grants)")]
            if "tool_id" not in grant_cols:
                self._db.execute("ALTER TABLE grants ADD COLUMN tool_id TEXT")
            self._db.commit()
        _restrict(Path(self.path), 0o600)
        for suffix in ("-wal", "-shm"):
            _restrict(Path(f"{self.path}{suffix}"), 0o600)

    # -- usage --------------------------------------------------------------
    def bump_usage(self, handle: str, *, limit: int, window: float = 3600.0) -> bool:
        now = time.time()
        with self._lock:
            row = self._db.execute(
                "SELECT window_start, count FROM usage WHERE handle = ?", (handle,)
            ).fetchone()
            if row is None or now - row[0] > window:
                self._db.execute(
                    "INSERT INTO usage(handle, window_start, count) VALUES (?,?,1)"
                    " ON CONFLICT(handle) DO UPDATE SET window_start = ?, count = 1",
                    (handle, now, now),
                )
                self._db.commit()
                return True
            if row[1] + 1 > limit:
                return False
            self._db.execute(
                "UPDATE usage SET count = count + 1 WHERE handle = ?", (handle,)
            )
            self._db.commit()
            return True

    # -- grants -------------------------------------------------------------
    def add_grant_request(
        self, handle: str, purpose: str, ttl: int, tool_id: str | None = None
    ) -> str:
        grant_id = f"grant-{uuid.uuid4().hex[:10]}"
        with self._lock:
            self._db.execute(
                "INSERT INTO grants(id, handle, purpose, ttl, status, created, tool_id)"
                " VALUES (?,?,?,?,?,?,?)",
                (grant_id, handle, purpose, ttl, "pending", time.time(), tool_id),
            )
            self._db.commit()
        return grant_id

    def decide_grant(self, grant_id: str, status: str, *, by: str) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "UPDATE grants SET status = ?, decided = ?, by = ?"
                " WHERE id = ? AND status = 'pending'",
                (status, time.time(), by, grant_id),
            )
            self._db.commit()
        return cursor.rowcount > 0

    def approve_grant(self, grant_id: str, *, by: str) -> bool:
        return self.decide_grant(grant_id, "approved", by=by)

    def deny_grant(self, grant_id: str, *, by: str) -> bool:
        return self.decide_grant(grant_id, "denied", by=by)

    def revoke_grant(self, grant_id: str, *, by: str = "owner") -> bool:
        """Take an approved grant away — the tool loses access immediately."""
        with self._lock:
            cursor = self._db.execute(
                "UPDATE grants SET status = 'revoked', decided = ?, by = ?"
                " WHERE id = ? AND status = 'approved'",
                (time.time(), by, grant_id),
            )
            self._db.commit()
        return cursor.rowcount > 0

    def list_grants(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, handle, purpose, ttl, status, created, decided, by, tool_id"
                " FROM grants ORDER BY created DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row[0], "handle": row[1], "purpose": row[2], "ttl": row[3],
                "status": row[4], "created": row[5], "decided": row[6], "by": row[7],
                "tool_id": row[8],
            }
            for row in rows
        ]

    def has_active_grant(self, handle: str) -> bool:
        return self._active_grant(handle, None)

    def has_active_grant_for_tool(self, handle: str, tool_id: str) -> bool:
        """An active grant that covers this tool (tool-scoped or unscoped)."""
        return self._active_grant(handle, tool_id)

    def _active_grant(self, handle: str, tool_id: str | None) -> bool:
        now = time.time()
        with self._lock:
            if tool_id is None:
                rows = self._db.execute(
                    "SELECT created, ttl FROM grants WHERE handle = ? AND status = 'approved'"
                    " ORDER BY created DESC LIMIT 20",
                    (handle,),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT created, ttl FROM grants WHERE handle = ? AND status = 'approved'"
                    " AND (tool_id IS NULL OR tool_id = ?) ORDER BY created DESC LIMIT 20",
                    (handle, tool_id),
                ).fetchall()
        return any(row[0] + row[1] > now for row in rows)

    # -- spends -------------------------------------------------------------
    def add_spend(self, request: SpendRequest, *, rules: SpendRules) -> SpendRequest:
        now = time.time()
        request.created = now
        with self._lock:
            if rules.per_tx_cap is not None and request.amount > rules.per_tx_cap:
                request.status = "denied"
                request.reason = f"over the per-tx cap ({rules.per_tx_cap})"
            elif (
                rules.weekly_cap is not None
                and self._spent_since(now - 7 * 86400) + request.amount > rules.weekly_cap
            ):
                request.status = "denied"
                request.reason = f"over the weekly budget ({rules.weekly_cap})"
            elif (
                rules.monthly_cap is not None
                and self._spent_since(now - 30 * 86400) + request.amount > rules.monthly_cap
            ):
                request.status = "denied"
                request.reason = f"over the monthly budget ({rules.monthly_cap})"
            elif rules.tier == TIER_HUMAN:
                request.status = "pending_human"
            elif request.merchant not in rules.whitelist:
                request.status = "pending_human"
                request.reason = "merchant not in whitelist"
            elif not request.intent_id:
                # mandates require a verifiable intent (AP2 idea): auto-approve
                # only for requests whose amount/merchant/expiry are pinned.
                request.status = "pending_human"
                request.reason = "mandate without a verifiable intent"
            else:
                request.status = "approved"
                request.approved_by = "policy"
            self._db.execute(
                "INSERT INTO spends(id, amount, currency, merchant, purpose, status, reason,"
                " approved_by, intent_id, intent_expires_at, created, completed_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    request.request_id,
                    request.amount,
                    request.currency,
                    request.merchant,
                    request.purpose,
                    request.status,
                    request.reason,
                    request.approved_by,
                    request.intent_id,
                    request.intent_expires_at,
                    request.created,
                    request.completed_at,
                ),
            )
            self._db.commit()
        return request

    def _spent_since(self, since: float) -> float:
        row = self._db.execute(
            "SELECT COALESCE(SUM(amount), 0.0) FROM spends WHERE status IN ('approved','completed')"
            " AND created >= ?",
            (since,),
        ).fetchone()
        return float(row[0])

    def decide_spend(self, request_id: str, status: str, *, by: str, reason: str = "") -> bool:
        with self._lock:
            cursor = self._db.execute(
                "UPDATE spends SET status = ?, approved_by = ?, reason = ?"
                " WHERE id = ? AND status = 'pending_human'",
                (status, by, reason, request_id),
            )
            self._db.commit()
        return cursor.rowcount > 0

    def complete_spend(self, request_id: str) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "UPDATE spends SET status = 'completed', completed_at = ?"
                " WHERE id = ? AND status = 'approved'",
                (time.time(), request_id),
            )
            self._db.commit()
        return cursor.rowcount > 0

    def expire_spend(self, request_id: str, reason: str) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "UPDATE spends SET status = 'denied', reason = ?"
                " WHERE id = ? AND status IN ('pending_human','approved')",
                (reason, request_id),
            )
            self._db.commit()
        return cursor.rowcount > 0

    def get_spend(self, request_id: str) -> SpendRequest | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM spends WHERE id = ?", (request_id,)).fetchone()
        if row is None:
            return None
        return SpendRequest(
            request_id=row[0],
            amount=row[1],
            currency=row[2],
            merchant=row[3],
            purpose=row[4],
            status=row[5],
            reason=row[6] or "",
            approved_by=row[7] or "",
            intent_id=row[8] or "",
            intent_expires_at=row[9],
            created=row[10],
            completed_at=row[11],
        )

    def open_spends(self) -> list[SpendRequest]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id FROM spends WHERE status IN ('pending_human','approved')"
                " ORDER BY created"
            ).fetchall()
        return [request for row in rows if (request := self.get_spend(row[0])) is not None]

    def close(self) -> None:
        with self._lock:
            self._db.close()


class SpendLedger(_EventRecorder):
    """Staged spending with human-approval default and verifiable intents."""

    def __init__(
        self,
        state: TreasuryState,
        rules: SpendRules | None = None,
        *,
        events: EventStore | None = None,
        session_id: str = "treasury",
    ) -> None:
        self.state = state
        self.rules = rules or PRESETS["stufe0"]
        self.events = events
        self.session_id = session_id
        self._step = 0

    def request(
        self,
        *,
        amount: float,
        merchant: str,
        purpose: str,
        currency: str = "EUR",
        intent: SpendIntent | None = None,
    ) -> SpendRequest:
        request = SpendRequest(
            request_id=f"spend-{uuid.uuid4().hex[:10]}",
            amount=float(amount),
            currency=currency,
            merchant=merchant,
            purpose=purpose,
            status="pending_human",
            intent_id=intent.intent_id if intent else "",
            intent_expires_at=intent.expires_at if intent else None,
        )
        if not math.isfinite(request.amount) or request.amount <= 0:
            request.status = "denied"
            request.reason = "invalid amount (not finite or <= 0)"
        elif intent is not None and (
            abs(intent.amount - request.amount) > 1e-9
            or intent.merchant != request.merchant
            or intent.currency != request.currency
        ):
            request.status = "denied"
            request.reason = "intent does not match the request"
        else:
            request = self.state.add_spend(request, rules=self.rules)
        kind = {
            "pending_human": "spend_requested",
            "approved": "spend_approved",
            "denied": "spend_denied",
        }[request.status]
        self._record(
            kind,
            {
                "request": request.request_id,
                "amount": request.amount,
                "currency": request.currency,
                "merchant": request.merchant,
                "reason": request.reason,
            },
        )
        return request

    def approve(self, request_id: str, *, by: str = "owner") -> bool:
        ok = self.state.decide_spend(request_id, "approved", by=by)
        if ok:
            self._record("spend_approved", {"request": request_id, "by": by})
        return ok

    def deny(self, request_id: str, *, by: str = "owner", reason: str = "") -> bool:
        ok = self.state.decide_spend(request_id, "denied", by=by, reason=reason)
        if ok:
            self._record("spend_denied", {"request": request_id, "by": by, "reason": reason})
        return ok

    def allowance(self, *, now: float | None = None) -> dict:
        """Fake-money allowance the agent may see (no payment rails exist).

        Budget bookkeeping only: tier, caps, whitelist and what is left in the
        current week/month for the configured **test money**. There is no
        real-funds path in this codebase.
        """
        moment = time.time() if now is None else now
        spent_week = self.state._spent_since(moment - 7 * 86400)
        spent_month = self.state._spent_since(moment - 30 * 86400)

        def remaining(cap: float | None, spent: float) -> float | None:
            return None if cap is None else round(max(0.0, cap - spent), 2)

        return {
            "mode": "testgeld",
            "tier": self.rules.tier,
            "per_tx_cap": self.rules.per_tx_cap,
            "weekly_cap": self.rules.weekly_cap,
            "monthly_cap": self.rules.monthly_cap,
            "whitelist": list(self.rules.whitelist),
            "spent_week": round(spent_week, 2),
            "spent_month": round(spent_month, 2),
            "remaining_week": remaining(self.rules.weekly_cap, spent_week),
            "remaining_month": remaining(self.rules.monthly_cap, spent_month),
        }

    def complete(self, request_id: str) -> bool:
        request = self.state.get_spend(request_id)
        if request is None or request.status != "approved":
            return False
        if request.intent_expires_at is not None and time.time() > request.intent_expires_at:
            self.state.expire_spend(request_id, "intent expired")
            self._record("spend_denied", {"request": request_id, "reason": "intent expired"})
            return False
        ok = self.state.complete_spend(request_id)
        if ok:
            self._record("spend_completed", {"request": request_id})
        return ok


def draft_spend_intent(
    client: LLMClient,
    registry: PromptRegistry,
    *,
    goal: str,
    whitelist: list[str],
    budget_hint: str = "",
    budget: BudgetTracker | None = None,
) -> SpendIntent | None:
    """LLM draft of a spend intent (None on any failure)."""
    try:
        result = run_prompt(
            client,
            registry,
            "spend_intent",
            {
                "goal": goal,
                "whitelist": ", ".join(whitelist) or "(none)",
                "budget_hint": budget_hint or "(no hint)",
            },
            budget=budget,
        )
    except Exception:
        return None
    data = result.parsed if isinstance(result.parsed, dict) else {}
    try:
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        return None
    merchant = str(data.get("merchant", "")).strip()
    if amount <= 0 or not merchant:
        return None
    return SpendIntent(
        amount=amount,
        currency=str(data.get("currency", "EUR"))[:3].upper(),
        merchant=merchant[:80],
        purpose=str(data.get("purpose", ""))[:200],
    )
