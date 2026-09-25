"""TypeSafe/System-One treasury connection (§16, ABNAHME §86/JEV-08).

    ConnectionRef
    provider = typesafe
    target   = system_one
    scope    = evaluate
    secret_handle = ...
    status   = ...
    revision = ...

What this module is, and what it refuses to be:

* **the ConnectionRef contract** (:class:`ConnectionRef`) — six fields, the
  provider/target/scope pinned, a handle that must look like
  ``secret://service/name``, and a status vocabulary
  (``active``/``unconfigured``/``missing``/``expired``/``unauthorized``). An
  unknown status or a non-handle is a contract error, not a guess;
* **a resolver over the real store** — it uses :class:`skaldir.treasury.Vault` and
  :class:`skaldir.treasury.TreasuryState`, the repo's one credential store (§6/§76).
  No second vault, no key file, no env var. The Treasury has no revision column,
  so the revision is derived deterministically from the entry's own metadata
  (kind, scopes, creation time) and is explicitly **not** persisted;
* **the reference/secret split** — what crosses to the evaluator is a *handle
  reference* (`handle`, `provider`, `target`, `scope`, `revision`), never the
  material. The material is fetched per attempt by
  :meth:`SystemOneTreasuryAdapter.authorization_headers`, used for the transport
  header and dropped; it is never stored on the adapter, an evaluation, an event
  or a log.

Leak discipline (§16: "Kein Key in Prompt, Event, Model State, Memory, Logs,
Diagnostics oder Screenshots"):

* :func:`assert_no_secret` refuses any payload that contains the stored secret —
  raw or url-quoted — and also refuses anything the project's own
  :func:`skaldir.secretscan.scan` flags as a secret;
* :class:`GuardedEmitter` wraps an emitter so the refusal happens **before**
  anything is recorded (fail-closed): a leaking payload raises
  :class:`TreasuryLeakError` and the recorder never sees it;
* screenshots are deliberately out of reach here: no screenshot is ever built or
  sent by this path (§34), and no screenshot store exists on a headless host —
  that clause is ``not_run``, not "verified" (see
  ``docs/audit/JEV_PROVIDER_COMPATIBILITY.md``).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from .secretscan import scan
from .system_one import ACCESS_BLOCKED

CONNECTION_PROVIDER = "typesafe"
CONNECTION_TARGET = "system_one"
CONNECTION_SCOPE = "evaluate"

#: The six documented ConnectionRef fields, in order (§16).
CONNECTION_FIELDS = (
    "provider",
    "target",
    "scope",
    "secret_handle",
    "status",
    "revision",
)

#: Statuses this contract can report. Nothing else is allowed to appear.
CONNECTION_STATUSES = (
    "unconfigured",
    "missing",
    "expired",
    "unauthorized",
    "active",
)

#: The handle the runtime uses for Jev unless the owner names another one.
DEFAULT_HANDLE = "secret://typesafe/systemone"

#: The scope a handle needs before this connection may evaluate (§16).
REQUIRED_SCOPE = CONNECTION_SCOPE


class TreasuryLeakError(RuntimeError):
    """A payload was about to carry the stored secret outside the vault.

    Fail-closed by design: the payload is refused instead of being recorded,
    logged or sent.
    """


def _is_handle(handle: str) -> bool:
    text = str(handle or "")
    if not text.startswith("secret://"):
        return False
    remainder = text[len("secret://") :]
    service, _, name = remainder.partition("/")
    return bool(service and name and " " not in remainder)


@dataclass(frozen=True)
class ConnectionRef:
    """The §16 connection reference — a reference, never the material."""

    secret_handle: str
    status: str = "unconfigured"
    revision: str = ""
    provider: str = CONNECTION_PROVIDER
    target: str = CONNECTION_TARGET
    scope: str = CONNECTION_SCOPE

    def __post_init__(self) -> None:
        if self.provider != CONNECTION_PROVIDER:
            raise ValueError(f"a System-One connection is provider={CONNECTION_PROVIDER!r} (§16)")
        if self.target != CONNECTION_TARGET:
            raise ValueError(f"a System-One connection targets {CONNECTION_TARGET!r} (§16)")
        if self.scope != CONNECTION_SCOPE:
            raise ValueError(f"a System-One connection is scoped {CONNECTION_SCOPE!r} (§16)")
        if self.status not in CONNECTION_STATUSES:
            raise ValueError(
                f"unknown connection status: {self.status!r} (known: {CONNECTION_STATUSES})"
            )
        if not _is_handle(self.secret_handle):
            raise ValueError(
                f"a secret handle looks like secret://service/name, got {self.secret_handle!r} "
                "— a raw credential must never be named here (§16)"
            )
        if self.status == "active" and not self.revision:
            raise ValueError("an active connection needs a revision (§16)")

    @property
    def active(self) -> bool:
        return self.status == "active"

    def as_dict(self) -> dict:
        return {field: getattr(self, field) for field in CONNECTION_FIELDS}


def _secret_variants(secret: str) -> set[str]:
    """Every form the material could leak in (mirrors the Broker's scrubbing)."""
    if not secret:
        return set()
    variants = {secret}
    if len(secret) >= 8:
        stripped = secret.strip()
        if stripped:
            variants.add(stripped)
        variants.add(quote(secret, safe=""))
    return variants


def assert_no_secret(payload: Any, secret: str, *, where: str) -> None:
    """Fail closed if ``payload`` carries the stored secret (or any secret).

    Two independent checks: the exact material (raw, stripped, url-quoted) and
    the project redactor — the same
    :func:`skaldir.secretscan.scan` the rest of the runtime uses.
    """
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str, sort_keys=True)
    for variant in _secret_variants(str(secret or "")):
        if variant and variant in text:
            raise TreasuryLeakError(
                f"{where}: the stored secret would cross the vault boundary — refused "
                "(§16: no key in prompt, event, model state, memory, logs, diagnostics)"
            )
    findings = scan(text)
    if findings:
        kinds = ", ".join(sorted({finding.kind for finding in findings}))
        raise TreasuryLeakError(
            f"{where}: the payload contains a secret-like value ({kinds}) — refused (§16)"
        )


class GuardedEmitter:
    """An emitter that refuses to record a payload carrying the secret (§16)."""

    def __init__(self, *, recorder: Callable[[str, dict], Any], secret: str) -> None:
        self.recorder = recorder
        self.secret = secret
        self.refused = 0

    def __call__(self, kind: str, payload: Mapping[str, Any]) -> Any:
        try:
            assert_no_secret(payload, self.secret, where=f"event {kind}")
        except TreasuryLeakError:
            self.refused += 1
            raise
        return self.recorder(kind, dict(payload))

    def __repr__(self) -> str:  # pragma: no cover — never render the secret
        name = getattr(self.recorder, "__name__", "callable")
        return f"GuardedEmitter(recorder={name}, secret=<redacted>)"


class SystemOneTreasuryAdapter:
    """Resolves the TypeSafe/System-One connection against the real treasury.

    ``vault=None`` is the honest "no Treasury configured here" state: the
    ConnectionRef resolves to ``unconfigured`` and the evaluator stays blocked.
    The adapter never keeps the material: :meth:`authorization_headers` asks the
    vault for it at call time, and the value exists only for that header dict.
    """

    def __init__(
        self,
        vault,
        *,
        handle: str = DEFAULT_HANDLE,
        state=None,
        events=None,
        session_id: str = "treasury",
    ) -> None:
        if not _is_handle(handle):
            raise ValueError(
                f"a secret handle looks like secret://service/name, got {handle!r}"
            )
        self.vault = vault
        self.handle = handle
        self.state = state
        self.events = events
        self.session_id = session_id

    # -- the connection reference (§16) ------------------------------------

    def connection_ref(self, *, handle: str = "") -> ConnectionRef:
        """Resolve the six §16 fields against the real vault (never a key)."""
        wanted = str(handle or self.handle)
        if self.vault is None:
            return ConnectionRef(secret_handle=wanted, status="unconfigured")
        info = self.vault.describe(wanted)
        if info is None:
            return ConnectionRef(secret_handle=wanted, status="missing")
        if info.expires_at is not None and time.time() > info.expires_at:
            return ConnectionRef(secret_handle=wanted, status="expired")
        if REQUIRED_SCOPE not in tuple(info.scopes or ()):
            return ConnectionRef(secret_handle=wanted, status="unauthorized")
        return ConnectionRef(
            secret_handle=wanted,
            status="active",
            revision=self._revision(wanted, info),
        )

    @staticmethod
    def _revision(handle: str, info) -> str:
        """A deterministic revision from the entry's own metadata.

        The Treasury stores no revision column, so this is *derived*, not
        persisted — rotating the entry (new kind/scopes/created) yields a new
        revision, and the same entry always yields the same one.
        """
        payload = {
            "handle": handle,
            "kind": str(getattr(info, "kind", "")),
            "scopes": sorted(str(scope) for scope in (getattr(info, "scopes", ()) or ())),
            "created": float(getattr(info, "created", 0.0) or 0.0),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return f"rev-{digest[:12]}"

    # -- what crosses to the evaluator -------------------------------------

    def credential_reference(self, context=None) -> dict | None:
        """The handle **reference** for the evaluator's credential resolver.

        Returns ``None`` whenever the connection is not usable — the evaluator
        then reports ``blocked_external_access`` instead of calling anything.
        The returned mapping never contains material.
        """
        ref = self.connection_ref()
        if not ref.active:
            return None
        return {
            "handle": ref.secret_handle,
            "provider": ref.provider,
            "target": ref.target,
            "scope": ref.scope,
            "revision": ref.revision,
            "status": ref.status,
        }

    def authorization_headers(self) -> dict:
        """The ``Bearer`` header for one attempt, or empty when unusable.

        This is the one place the material is touched. It is never stored on the
        adapter, never emitted and never logged: the caller (the evaluator's
        transport) uses the dict for a single request. A missing/expired/
        unauthorized connection yields ``{}`` — and an unusable connection must
        not look like an authorized one.
        """
        ref = self.connection_ref()
        if not ref.active:
            self._record(
                "treasury_denied",
                {
                    "handle": ref.secret_handle,
                    "status": ref.status,
                    "purpose": "system_one:evaluate",
                },
            )
            return {}
        try:
            payload = self.vault._decrypt(ref.secret_handle)  # the broker side
        except Exception as exc:  # noqa: BLE001 — a vault failure is a denial
            self._record(
                "treasury_denied",
                {
                    "handle": ref.secret_handle,
                    "status": ref.status,
                    "reason": type(exc).__name__,
                    "purpose": "system_one:evaluate",
                },
            )
            return {}
        secret = str(payload.get("secret", ""))
        if not secret:
            return {}
        self._record(
            "treasury_use",
            {
                "handle": ref.secret_handle,
                "revision": ref.revision,
                "purpose": "system_one:evaluate",
                "ok": True,
            },
        )
        return {"Authorization": f"Bearer {secret}"}

    # -- events ------------------------------------------------------------

    def guarded_emitter(self, recorder: Callable[[str, dict], Any]) -> GuardedEmitter:
        """Wrap an emitter so a leaking payload is refused before it is recorded."""
        secret = self._current_secret()
        return GuardedEmitter(recorder=recorder, secret=secret)

    def _current_secret(self) -> str:
        """Read the material only to build the guard (never to record it)."""
        ref = self.connection_ref()
        if not ref.active:
            return ""
        try:
            payload = self.vault._decrypt(ref.secret_handle)
        except Exception:  # noqa: BLE001 — an unreadable handle yields no guard input
            return ""
        return str(payload.get("secret", ""))

    def _record(self, kind: str, payload: dict) -> None:
        """Audit a treasury decision — payloads here never carry material."""
        if self.events is None:
            return
        assert_no_secret(payload, self._current_secret(), where=f"event {kind}")
        try:
            self.events.append(self.session_id, 1, kind, payload)
        except TypeError:  # pragma: no cover — an EventStore needs a step cursor
            self.events.append(self.session_id, 1, kind, payload)

    # -- degraded state ----------------------------------------------------

    def blocked_reason(self) -> str:
        """Why the connection is not usable, in one line (§118 'blocked')."""
        ref = self.connection_ref()
        if ref.active:
            return ""
        return (
            f"typesafe/system_one connection is {ref.status} for handle "
            f"{ref.secret_handle} — no live Jev access ({ACCESS_BLOCKED})"
        )


__all__ = [
    "CONNECTION_FIELDS",
    "CONNECTION_PROVIDER",
    "CONNECTION_SCOPE",
    "CONNECTION_STATUSES",
    "CONNECTION_TARGET",
    "DEFAULT_HANDLE",
    "REQUIRED_SCOPE",
    "ConnectionRef",
    "GuardedEmitter",
    "SystemOneTreasuryAdapter",
    "TreasuryLeakError",
    "assert_no_secret",
]
