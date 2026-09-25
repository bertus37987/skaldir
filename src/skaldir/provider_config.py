"""Provider configuration store — the write side of the ``godsv.llm`` contract.

:mod:`skaldir.llm_provider` already knows how to *read* a provider configuration
(``explicit config > environment > config file > defaults``) and it refuses to
read a secret from a file. Nothing in the product could *write* one, so the owner
had to hand-edit YAML and export variables. This module is the write side of that
same contract, plus the redacted views the CLI renders:

* **one file** — the file :func:`skaldir.llm_provider.default_config_file` resolves
  (``$SKALDIR_LLM_CONFIG`` → ``<runtime_dir>/config.yaml``, i.e. ``$SKALDIR_HOME`` →
  ``$HERMES_HOME/godsv`` → ``~/.hermes/godsv``). No second config location;
* **named providers + one active projection** — every configured provider lives
  in ``godsv.providers.entries.<name>``, and the *default* one is projected into
  the flat ``godsv.llm`` block that :mod:`skaldir.llm_provider` actually reads, so
  configuring a provider here is what the runtime then uses::

      godsv:
        llm:                                  # the ACTIVE provider (what the runtime reads)
          provider: openai-compatible
          base_url: https://api.deepseek.com/v1
          model: deepseek-chat
          api_key_env: DEEPSEEK_API_KEY        # the NAME of the variable, never the key
          tiers:
            small: deepseek-chat
            main: deepseek-reasoner
        providers:
          default: deepseek                    # which entry the projection mirrors
          entries:
            deepseek:
              kind: openai-compatible
              base_url: https://api.deepseek.com/v1
              model: deepseek-chat
              api_key_env: DEEPSEEK_API_KEY
              tiers: {small: …, main: …}

* **secret-free by construction** — three independent guards, none of which ever
  prints a value: (1) the store writes only known keys plus ``api_key_env`` (a
  variable *name*, matched against ``[A-Za-z_][A-Za-z0-9_]*``); (2) loading
  refuses a literal ``api_key:``/``key:``/``token:``/``secret:`` value inside a
  block it owns (:data:`skaldir.llm_provider.SECRET_FILE_KEYS`) — the invariant
  :mod:`skaldir.llm_provider` already enforces on the read side; (3) nothing is
  written before the serialized ``godsv`` subtree has passed
  :func:`skaldir.secretscan.scan` — a key smuggled into a ``--base-url`` or a
  ``--model`` is refused, never stored;
* **atomic + backed up** — temp file in the same directory + ``os.replace``, and
  the previous file is copied to ``<name>.pre-provider-<utc>.bak`` first (never
  overwriting an older backup), so a crash or a bad edit cannot lose the config;
* **no guessing** — paths and values are never invented: a missing provider is
  :class:`ProviderNotFound`, an unusable one is :class:`ProviderUnusable`, a
  remote endpoint without a credential is :class:`ProviderNeedsCredential` and
  *no request is attempted*. The CLI maps these onto the shared exit codes.

Other top-level keys of the target file are preserved (the document is
round-tripped); YAML comments are not, which is why the write report names the
file it rewrote — the same file is machine-written by ``skaldir provider …``.

:func:`probe_provider` is the one live call in this module: it builds its client
through :func:`skaldir.llm_provider.provider_for` (the documented factory) and runs
the real :class:`skaldir.llm_provider.OpenAICompatibleLLM`; the recording wrapper
only observes the answer so the ``model`` the endpoint reports can be echoed.
Retry/timeout behaviour stays in :mod:`skaldir.system_one_policy` — this module
calls nothing of its own.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx  # declared dependency (pip: httpx>=0.27)

from . import llm_provider as lp
from . import secretscan
from .llm import TIERS
from .llm_provider import _compact  # one home for the provider-side compaction helper

__all__ = [
    "DEFAULT_KIND",
    "PROBE_PROMPT",
    "PROBE_PROMPT_ID",
    "PROBE_TIER",
    "PROJECTION_KEYS",
    "PROVIDER_KINDS",
    "ProviderEntry",
    "ProviderExists",
    "ProviderInvalid",
    "ProviderNeedsCredential",
    "ProviderNotFound",
    "ProviderSecretRefused",
    "ProviderStore",
    "ProviderStoreError",
    "ProviderUnusable",
    "ProviderWriteError",
    "add_provider",
    "config_path",
    "endpoint_is_local",
    "load_store",
    "probe_provider",
    "remove_provider",
    "set_default",
    "set_tiers",
    "store_report",
    "validate_env_name",
    "validate_name",
]

# --------------------------------------------------------------------------- #
# contract constants                                                          #
# --------------------------------------------------------------------------- #

#: the only kind this store writes — the resolver's own vocabulary
#: (:data:`skaldir.llm_provider.PROVIDERS` also carries ``scripted``/``none``, which
#: are not configured *providers* but offline/disabled modes).
PROVIDER_KINDS = ("openai-compatible",)
DEFAULT_KIND = "openai-compatible"

#: ``godsv.providers`` — the named registry
REGISTRY_BLOCK = "providers"
REGISTRY_DEFAULT = "default"
REGISTRY_ENTRIES = "entries"

#: the keys the ACTIVE projection owns inside ``godsv.llm``. Everything else in
#: that block (``headers``, ``extra_body``, ``stream`` …) is left untouched.
PROJECTION_KEYS = (
    "provider",
    "base_url",
    "model",
    "api_key_env",
    "tiers",
    "timeout_s",
    "max_tokens",
)

#: The probe prompt: one fixed id, one fixed sentence, so a test can assert
#: exactly what left the machine.
PROBE_PROMPT_ID = "provider.test"
PROBE_PROMPT = "Reply with the single word: pong"
PROBE_TIER = "small"
PROBE_PREVIEW_CHARS = 200

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: the command a fresh install needs
EXAMPLE_ADD = (
    "skaldir provider add local --base-url http://127.0.0.1:8080/v1 "
    "--model qwen2.5-7b-instruct"
)


# --------------------------------------------------------------------------- #
# errors                                                                      #
# --------------------------------------------------------------------------- #


class ProviderStoreError(RuntimeError):
    """Anything this store refuses to do. The CLI maps the subclasses to codes."""


class ProviderNotFound(ProviderStoreError):
    """No configured provider with that name."""


class ProviderExists(ProviderStoreError):
    """A provider with that name is already configured."""


class ProviderInvalid(ProviderStoreError):
    """The caller's input is not usable (bad name, bad URL, malformed value)."""


class ProviderUnusable(ProviderStoreError):
    """The provider cannot make a call with what is configured."""


class ProviderNeedsCredential(ProviderStoreError):
    """A remote endpoint without a credential — no request is attempted."""


class ProviderSecretRefused(ProviderStoreError):
    """A literal secret is in the file, or one would have been written."""


class ProviderWriteError(ProviderStoreError):
    """The config file could not be read, written or backed up."""


# --------------------------------------------------------------------------- #
# small helpers                                                               #
# --------------------------------------------------------------------------- #


#: ``_compact`` lives in :mod:`skaldir.llm_provider` (imported above) — one home.


def _env_map(env: Mapping[str, str] | None = None) -> dict[str, str]:
    if env is None:
        return {str(k): str(v) for k, v in os.environ.items()}
    return {str(k): str(v) for k, v in env.items()}


def _echoable(value: object, limit: int = 24) -> bool:
    """May this caller value appear in a message?

    Only short, plain, non-secret-shaped strings. A field an owner may paste a
    credential into (``api_key_env``, ``base_url`` with a query token) never gets
    echoed verbatim — an error message is not a leak channel.
    """
    text = str(value or "")
    if not text or len(text) > limit:
        return False
    if secretscan.scan(text):
        return False
    return not re.search(r"[A-Za-z0-9]{16,}", text)


def _safe_value(value: object, limit: int = 80) -> str:
    """A caller value for a message: compacted, truncated, secret-shaped parts redacted."""
    text = _compact(value, limit)
    redacted, findings = secretscan.redact(text)
    return redacted if findings else text


def validate_name(name: str) -> str:
    """A provider name: a lowercase slug (never a path, never a secret)."""
    value = str(name or "").strip()
    if not _NAME_RE.match(value):
        shown = f", got {value!r}" if _echoable(value) else " (the value was not printed)"
        raise ProviderInvalid(
            "provider name must be a lowercase slug of [a-z0-9._-] starting with "
            f"[a-z0-9] (max 64 chars){shown}"
        )
    return value


def validate_env_name(name: str) -> str:
    """An environment variable NAME — a literal key (``sk-…``) never matches.

    The refused value is deliberately **not** echoed: this is the field a literal
    key gets pasted into, and a message must never become the leak.
    """
    value = str(name or "").strip()
    if not _ENV_NAME_RE.match(value):
        looks_like_a_key = bool(secretscan.scan(value))
        reason = (
            " — it looks like a literal key, so it was not printed"
            if looks_like_a_key
            else " — the value was not printed"
        )
        raise ProviderInvalid(
            "api_key_env must be the NAME of an environment variable "
            "([A-Za-z_][A-Za-z0-9_]*), e.g. DEEPSEEK_API_KEY; a literal key is never "
            f"stored or accepted{reason}"
        )
    return value


def endpoint_is_local(base_url: str) -> bool:
    """``True`` for loopback hosts, where a credential is not required.

    Mirrors :func:`skaldir.llm_provider._is_local` (a private helper there); a test
    pins the two against each other so they cannot drift apart.
    """
    host = str(base_url or "").split("://", 1)[-1].split("/", 1)[0].rsplit("@", 1)[-1]
    host = host.rsplit(":", 1)[0].lower()
    return host in (
        "localhost",
        "127.0.0.1",
        "::1",
        "[::1]",
        "0.0.0.0",
        "host.docker.internal",
    )


def config_path(path: str | Path | None = None) -> Path:
    """The one runtime config file (``$SKALDIR_LLM_CONFIG`` wins), never created here."""
    if path is not None and str(path).strip():
        return Path(str(path)).expanduser()
    return lp.default_config_file()


def _secret_keys(mapping: Mapping[str, Any]) -> list[str]:
    """Literal-secret key names in ``mapping`` (values are never read or shown)."""
    found = {str(key).strip().lower() for key in mapping}
    return sorted(found.intersection(lp.SECRET_FILE_KEYS))


def _refuse_secret(path: Path, where: str, keys: list[str]) -> ProviderSecretRefused:
    names = ", ".join(f"'{key}:'" for key in keys)
    return ProviderSecretRefused(
        f"config file {path} carries a literal {names} value in {where} — a config file "
        "is not a secret store. Remove the value, keep only 'api_key_env: <NAME>', and "
        f"export the credential in the environment (e.g. export {lp.ENV_API_KEY}=<your-key>)"
    )


# --------------------------------------------------------------------------- #
# the entry                                                                   #
# --------------------------------------------------------------------------- #


@dataclass
class ProviderEntry:
    """One configured provider. A credential is *named*, never held."""

    name: str
    kind: str = DEFAULT_KIND
    base_url: str = ""
    model: str = ""
    api_key_env: str = ""
    tiers: dict[str, str] = field(default_factory=dict)
    timeout_s: float | None = None
    max_tokens: int | None = None
    #: keys this store does not know yet — preserved verbatim across a write
    extra: dict[str, Any] = field(default_factory=dict)

    # -- serialization ------------------------------------------------------ #

    def config_block(self) -> dict[str, Any]:
        """The ``godsv.providers.entries.<name>`` mapping (comment order matters)."""
        block: dict[str, Any] = {
            "kind": self.kind,
            "base_url": self.base_url,
            "model": self.model,
        }
        if self.api_key_env:
            block["api_key_env"] = self.api_key_env
        tiers = {tier: self.tiers[tier] for tier in TIERS if str(self.tiers.get(tier) or "")}
        if tiers:
            block["tiers"] = tiers
        if self.timeout_s is not None:
            block["timeout_s"] = float(self.timeout_s)
        if self.max_tokens is not None:
            block["max_tokens"] = int(self.max_tokens)
        for key, value in self.extra.items():
            if key not in block:
                block[key] = value
        return block

    def projection(self) -> dict[str, Any]:
        """The ``godsv.llm`` block :func:`skaldir.llm_provider.resolve_provider_config` reads."""
        block: dict[str, Any] = {
            "provider": self.kind,
            "base_url": self.base_url,
            "model": self.model,
        }
        if self.api_key_env:
            block["api_key_env"] = self.api_key_env
        tiers = {tier: self.tiers[tier] for tier in TIERS if str(self.tiers.get(tier) or "")}
        if tiers:
            block["tiers"] = tiers
        if self.timeout_s is not None:
            block["timeout_s"] = float(self.timeout_s)
        if self.max_tokens is not None:
            block["max_tokens"] = int(self.max_tokens)
        return block

    # -- credentials (names and booleans only) ------------------------------ #

    def credential_env(self, env: Mapping[str, str] | None = None) -> str:
        """The variable the credential would come from (``""`` when none is set)."""
        env_map = _env_map(env)
        if self.api_key_env and str(env_map.get(self.api_key_env) or "").strip():
            return self.api_key_env
        for name in (lp.ENV_API_KEY, *lp.ENV_API_KEY_FALLBACKS):
            if str(env_map.get(name) or "").strip():
                return name
        return self.api_key_env

    def credential_set(self, env: Mapping[str, str] | None = None) -> bool:
        env_map = _env_map(env)
        if self.api_key_env and str(env_map.get(self.api_key_env) or "").strip():
            return True
        names = (lp.ENV_API_KEY, *lp.ENV_API_KEY_FALLBACKS)
        return any(str(env_map.get(name) or "").strip() for name in names)

    def is_local(self) -> bool:
        return endpoint_is_local(self.base_url)

    def needs_credential(self, env: Mapping[str, str] | None = None) -> bool:
        """A remote endpoint without a credential — the call would answer 401."""
        return not self.is_local() and not self.credential_set(env)

    def credential_state(self, env: Mapping[str, str] | None = None) -> str:
        env_map = _env_map(env)
        if self.api_key_env and str(env_map.get(self.api_key_env) or "").strip():
            return "set"
        if self.is_local() and not self.api_key_env:
            # a loopback endpoint needs no credential; a globally exported key is
            # irrelevant here and would only make the row look configured
            return "not-required"
        if self.credential_set(env):
            return "set"
        return "not-required" if self.is_local() else "unset"

    def missing(self, env: Mapping[str, str] | None = None) -> list[str]:
        """Everything that has to change before a call can succeed (offline view)."""
        out: list[str] = []
        if self.kind not in PROVIDER_KINDS:
            out.append(
                f"kind {self.kind!r} is not a configurable provider "
                f"(expected one of {list(PROVIDER_KINDS)})"
            )
        if not self.base_url:
            out.append("base_url is unset")
        elif not self.base_url.startswith(("http://", "https://")):
            out.append("base_url must start with http:// or https://")
        if not self.model:
            out.append("model is unset")
        if self.needs_credential(env):
            export = self.api_key_env or lp.ENV_API_KEY
            out.append(f"credential is unset — export {export} (the value is never stored)")
        if self.api_key_env and not _ENV_NAME_RE.match(self.api_key_env):
            out.append(f"api_key_env {self.api_key_env!r} is not a variable name")
        if self.timeout_s is not None and float(self.timeout_s) <= 0:
            out.append("timeout_s must be > 0")
        if self.max_tokens is not None and int(self.max_tokens) <= 0:
            out.append("max_tokens must be > 0")
        return out

    def ready(self, env: Mapping[str, str] | None = None) -> bool:
        return not self.missing(env)

    # -- reporting ---------------------------------------------------------- #

    def redacted(
        self, env: Mapping[str, str] | None = None, *, is_default: bool = False
    ) -> dict[str, Any]:
        """JSON-safe view: the credential is ``set``/``unset``/``not-required``."""
        return {
            "name": self.name,
            "kind": self.kind,
            "base_url": self.base_url,
            "endpoint": lp.endpoint_for(self.base_url) if self.base_url else "",
            "model": self.model,
            "tiers": {tier: self.tiers[tier] for tier in TIERS if str(self.tiers.get(tier) or "")},
            "api_key_env": self.api_key_env,
            "credential": self.credential_state(env),
            "credential_env": self.credential_env(env),
            "local": self.is_local(),
            "default": bool(is_default),
            "ready": self.ready(env),
            "missing": self.missing(env),
            "timeout_s": self.timeout_s,
            "max_tokens": self.max_tokens,
        }

    def __repr__(self) -> str:  # pragma: no cover - trivial, asserted in tests
        return (
            "ProviderEntry("
            f"name={self.name!r}, kind={self.kind!r}, base_url={self.base_url!r}, "
            f"model={self.model!r}, api_key_env={self.api_key_env!r}, "
            f"api_key=<{self.credential_state()}>, "
            f"tiers={sorted(self.tiers)}, ready={self.ready()})"
        )


DEFAULT_ENTRY_NAME = "<active>"


def _entry_from(name: str, raw: Mapping[str, Any], warnings: list[str]) -> ProviderEntry:
    """Read one registry entry; unknown values are reported, never guessed."""
    tiers: dict[str, str] = {}
    tiers_raw = raw.get("tiers")
    if isinstance(tiers_raw, Mapping):
        for tier, model in tiers_raw.items():
            key = str(tier).strip()
            value = str(model or "").strip()
            if key not in TIERS:
                warnings.append(
                    f"provider {name!r}: unknown tier {key!r} ignored (known: {list(TIERS)})"
                )
            elif value:
                tiers[key] = value
    elif tiers_raw is not None:
        warnings.append(f"provider {name!r}: tiers must be a mapping — ignored")

    timeout_s: float | None = None
    if raw.get("timeout_s") is not None:
        try:
            timeout_s = float(raw["timeout_s"])
        except (TypeError, ValueError):
            warnings.append(f"provider {name!r}: timeout_s is not a number — ignored")

    max_tokens: int | None = None
    if raw.get("max_tokens") is not None:
        try:
            max_tokens = int(str(raw["max_tokens"]).strip())
        except (TypeError, ValueError):
            warnings.append(f"provider {name!r}: max_tokens is not an integer — ignored")

    known = {
        "kind",
        "base_url",
        "model",
        "api_key_env",
        "tiers",
        "timeout_s",
        "max_tokens",
    }
    return ProviderEntry(
        name=name,
        kind=str(raw.get("kind") or DEFAULT_KIND).strip().lower(),
        base_url=str(raw.get("base_url") or "").strip(),
        model=str(raw.get("model") or "").strip(),
        api_key_env=str(raw.get("api_key_env") or "").strip(),
        tiers=tiers,
        timeout_s=timeout_s,
        max_tokens=max_tokens,
        extra={str(k): v for k, v in raw.items() if str(k) not in known},
    )


# --------------------------------------------------------------------------- #
# the store                                                                   #
# --------------------------------------------------------------------------- #


@dataclass
class ProviderStore:
    """The config file as this store sees it: registry + default + raw text."""

    path: Path
    document: dict[str, Any]
    providers: dict[str, ProviderEntry]
    default: str
    raw: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def exists(self) -> bool:
        return bool(self.raw)

    def names(self) -> list[str]:
        return sorted(self.providers)

    def entry(self, name: str | None = None) -> ProviderEntry:
        """The named entry, or the default one when ``name`` is empty."""
        wanted = str(name or "").strip() or self.default
        if not wanted:
            raise ProviderNotFound(
                "no provider configured "
                f"(config file {self.path}) — add one, e.g.: {EXAMPLE_ADD}"
            )
        entry = self.providers.get(wanted)
        if entry is None:
            known = ", ".join(self.names()) or "none"
            raise ProviderNotFound(f"no provider named {wanted!r} (configured: {known})")
        return entry

    def active(self) -> ProviderEntry | None:
        return self.providers.get(self.default) if self.default else None

    # -- reporting ---------------------------------------------------------- #

    def report(self, env: Mapping[str, str] | None = None) -> dict[str, Any]:
        """Redacted registry view for ``provider list`` / ``provider doctor``."""
        rows = [
            self.providers[name].redacted(env, is_default=name == self.default)
            for name in self.names()
        ]
        active_cfg = lp.resolve_provider_config(env=_env_map(env), config_path=self.path)
        projected = [
            key
            for key in PROJECTION_KEYS
            if str(_llm_block(self.document).get(key) or "").strip() not in ("", "{}")
        ]
        env_overrides = sorted(
            key for key, source in active_cfg.sources.items() if str(source).startswith("env:")
        )
        registry_entry = self.active()
        drift: list[str] = []
        if registry_entry is not None:
            for key, expected in (
                ("base_url", registry_entry.base_url),
                ("model", registry_entry.model),
                ("api_key_env", registry_entry.api_key_env),
            ):
                current = str(_llm_block(self.document).get(key) or "").strip()
                if expected and current != expected:
                    drift.append(
                        f"godsv.llm.{key} is {current or 'unset'!r}, the default "
                        f"provider says {expected!r}"
                    )
        return {
            "config_file": str(self.path),
            "config_exists": self.exists,
            "default": self.default,
            "count": len(self.providers),
            "providers": rows,
            "hand_written_block": bool(projected) and not self.providers,
            "projection_keys": projected,
            "drift": drift,
            "active": active_cfg.redacted(),
            "active_env_overrides": env_overrides,
            "warnings": list(self.warnings),
        }


def store_report(
    name: str | None = None,
    *,
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Convenience: load the store and report it (the ``provider list`` payload)."""
    store = load_store(path, env=env)
    payload = store.report(env)
    if name:
        entry = store.entry(name)
        payload["provider"] = entry.redacted(env, is_default=entry.name == store.default)
    return payload


def _llm_block(document: Mapping[str, Any]) -> dict[str, Any]:
    godsv = document.get("godsv")
    if not isinstance(godsv, Mapping):
        return {}
    block = godsv.get("llm")
    return dict(block) if isinstance(block, Mapping) else {}


def _godsv_block(document: dict[str, Any], *, create: bool) -> dict[str, Any]:
    value = document.get("godsv")
    if value is None:
        if not create:
            return {}
        block: dict[str, Any] = {}
        document["godsv"] = block
        return block
    if not isinstance(value, Mapping):
        raise ProviderInvalid(f"'godsv' must be a mapping, found {type(value).__name__}")
    if not isinstance(value, dict):
        value = dict(value)
        document["godsv"] = value
    return value


def _dump(document: Mapping[str, Any], *, path: Path | None = None) -> str:
    import yaml

    try:
        return yaml.safe_dump(
            dict(document), sort_keys=False, default_flow_style=False, allow_unicode=True
        )
    except yaml.YAMLError as exc:  # pragma: no cover - a mapping always dumps
        raise ProviderWriteError(
            f"cannot serialize the config for {path}: {type(exc).__name__}"
        ) from exc


def _load_document(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, ""
    except OSError as exc:
        raise ProviderWriteError(
            f"cannot read the config file {path}: {type(exc).__name__}"
        ) from exc
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is a runtime dependency
        raise ProviderWriteError("PyYAML is required to read the provider config file") from exc
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ProviderInvalid(
            f"the config file {path} is not valid YAML ({type(exc).__name__}) — fix it, "
            "this store will not overwrite it"
        ) from exc
    if not isinstance(data, Mapping):
        raise ProviderInvalid(f"the config file {path} must contain a mapping")
    return {str(k): v for k, v in data.items()}, raw


def load_store(
    path: str | Path | None = None, *, env: Mapping[str, str] | None = None
) -> ProviderStore:
    """Read the registry. A literal secret in a block we own is refused, not shown.

    ``env`` is accepted for symmetry with the rest of the module; loading does not
    depend on it (readiness does, see :meth:`ProviderEntry.ready`).
    """
    target = config_path(path)
    document, raw = _load_document(target)
    warnings: list[str] = []

    llm_keys = _secret_keys(_llm_block(document))
    if llm_keys:
        raise _refuse_secret(target, "godsv.llm", llm_keys)

    providers: dict[str, ProviderEntry] = {}
    default = ""
    godsv = _godsv_block(document, create=False)
    registry = godsv.get(REGISTRY_BLOCK)
    if registry is not None:
        if not isinstance(registry, Mapping):
            warnings.append(f"'godsv.{REGISTRY_BLOCK}' must be a mapping — ignored")
        else:
            default = str(registry.get(REGISTRY_DEFAULT) or "").strip()
            raw_entries = registry.get(REGISTRY_ENTRIES)
            if raw_entries is not None and not isinstance(raw_entries, Mapping):
                warnings.append(
                    f"'godsv.{REGISTRY_BLOCK}.{REGISTRY_ENTRIES}' must be a mapping — ignored"
                )
                raw_entries = None
            for name, value in dict(raw_entries or {}).items():
                key = str(name)
                if not isinstance(value, Mapping):
                    warnings.append(f"provider {key!r} is not a mapping — ignored")
                    continue
                secret_keys = _secret_keys(value)
                if secret_keys:
                    raise _refuse_secret(target, f"godsv.providers.entries.{key}", secret_keys)
                providers[key] = _entry_from(key, value, warnings)
            if default and default not in providers:
                warnings.append(
                    f"the default provider {default!r} is not configured — "
                    "run `skaldir provider default <name>`"
                )

    return ProviderStore(
        path=target,
        document=document,
        providers=providers,
        default=default,
        raw=raw,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
# writing (atomic + backup)                                                   #
# --------------------------------------------------------------------------- #


def _backup(path: Path) -> Path:
    """Copy ``path`` to ``<path>.pre-provider-<utc>.bak`` (never overwrites)."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = Path(f"{path}.pre-provider-{stamp}.bak")
    counter = 2
    while target.exists():
        target = Path(f"{path}.pre-provider-{stamp}-{counter}.bak")
        counter += 1
    try:
        shutil.copy2(path, target)
    except OSError as exc:
        raise ProviderWriteError(
            f"cannot back up {path} to {target}: {type(exc).__name__}"
        ) from exc
    return target


def _atomic_write_text(path: Path, text: str) -> None:
    """Temp file in the same directory, fsync, then ``os.replace``."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.provider-tmp-{os.getpid()}")
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        raise ProviderWriteError(f"cannot write {path}: {type(exc).__name__}") from exc


def write_document(
    document: dict[str, Any],
    path: str | Path | None = None,
    *,
    backup: bool = True,
    warnings: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Write the document atomically, with a backup, unless it would store a secret.

    Only the ``godsv`` subtree is secret-scanned: a *host* config file
    (``$SKALDIR_LLM_CONFIG`` may point at ``~/.hermes/config.yaml``) legitimately
    carries other tools' keys, and this store must neither read nor block them.
    """
    target = config_path(path)
    text = _dump(document, path=target)
    findings = secretscan.scan(_dump(document.get("godsv", {}), path=target))
    if findings:
        kinds = ", ".join(sorted({finding.kind for finding in findings}))
        raise ProviderSecretRefused(
            f"refusing to write {target}: the godsv block would contain what looks like a "
            f"secret ({kinds}). Only the NAME of a variable is stored "
            "('api_key_env: <NAME>'); the credential belongs in the environment"
        )
    existed = target.exists()
    backup_path = _backup(target) if (backup and existed) else None
    _atomic_write_text(target, text)
    return {
        "path": str(target),
        "backup": str(backup_path) if backup_path is not None else None,
        "written": True,
        "created": not existed,
        "bytes": len(text.encode("utf-8")),
        "top_level_keys": sorted(document),
        "warnings": list(warnings),
    }


def _set_registry(
    document: dict[str, Any], providers: Mapping[str, ProviderEntry], default: str
) -> None:
    godsv = _godsv_block(document, create=True)
    if providers:
        godsv[REGISTRY_BLOCK] = {
            REGISTRY_DEFAULT: default,
            REGISTRY_ENTRIES: {name: providers[name].config_block() for name in sorted(providers)},
        }
    elif REGISTRY_BLOCK in godsv:
        del godsv[REGISTRY_BLOCK]


def _set_projection(document: dict[str, Any], entry: ProviderEntry | None) -> None:
    """Mirror the default entry into the ``godsv.llm`` block the runtime reads."""
    godsv = _godsv_block(document, create=True)
    llm = dict(_llm_block(document))
    for key in PROJECTION_KEYS:
        llm.pop(key, None)
    if entry is not None:
        llm.update(entry.projection())
    if llm:
        godsv["llm"] = llm
    elif "llm" in godsv:
        del godsv["llm"]


def _commit(
    store: ProviderStore,
    *,
    providers: Mapping[str, ProviderEntry],
    default: str,
    backup: bool = True,
    warnings: tuple[str, ...] = (),
) -> dict[str, Any]:
    document = store.document
    _set_projection(document, providers.get(default) if default else None)
    _set_registry(document, providers, default)
    report = write_document(document, store.path, backup=backup, warnings=warnings)
    report["config_file"] = str(store.path)
    report["previous_bytes"] = len(store.raw.encode("utf-8"))
    return report


# --------------------------------------------------------------------------- #
# the operations                                                              #
# --------------------------------------------------------------------------- #


def add_provider(
    name: str,
    *,
    base_url: str,
    model: str,
    api_key_env: str = "",
    kind: str = DEFAULT_KIND,
    tiers: Mapping[str, str] | None = None,
    timeout_s: float | None = None,
    max_tokens: int | None = None,
    make_default: bool | None = None,
    replace: bool = False,
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    backup: bool = True,
) -> dict[str, Any]:
    """Add (or replace) a provider and keep the active projection in sync.

    ``make_default=None`` means *become the default when there is none* — an
    existing default is never stolen silently; ``replace=False`` refuses an
    existing name (:class:`ProviderExists`).
    """
    key = validate_name(name)
    kind_value = str(kind or "").strip().lower() or DEFAULT_KIND
    if kind_value not in PROVIDER_KINDS:
        raise ProviderInvalid(
            f"unknown provider kind {_safe_value(kind_value)!r} (known: {list(PROVIDER_KINDS)})"
        )
    url = str(base_url or "").strip()
    if not url:
        raise ProviderInvalid("base_url is required (e.g. --base-url https://api.openai.com/v1)")
    if not url.startswith(("http://", "https://")):
        raise ProviderInvalid(
            f"base_url must start with http:// or https://, got {_safe_value(url)!r}"
        )
    model_value = str(model or "").strip()
    if not model_value:
        raise ProviderInvalid("model is required (e.g. --model gpt-4o-mini)")
    env_name = validate_env_name(api_key_env) if str(api_key_env or "").strip() else ""

    tier_map: dict[str, str] = {}
    for tier, value in dict(tiers or {}).items():
        key_tier = str(tier).strip()
        if key_tier not in TIERS:
            raise ProviderInvalid(f"unknown tier {key_tier!r} (known: {list(TIERS)})")
        text = str(value or "").strip()
        if text:
            tier_map[key_tier] = text
    if timeout_s is not None and float(timeout_s) <= 0:
        raise ProviderInvalid(f"timeout_s must be > 0, got {timeout_s}")
    if max_tokens is not None and int(max_tokens) <= 0:
        raise ProviderInvalid(f"max_tokens must be > 0, got {max_tokens}")

    store = load_store(path, env=env)
    existed = key in store.providers
    if existed and not replace:
        raise ProviderExists(
            f"provider {key!r} is already configured in {store.path} — "
            "remove it first or pass --force to replace it"
        )

    warnings: list[str] = []
    previous = store.providers.get(key)
    entry = ProviderEntry(
        name=key,
        kind=kind_value,
        base_url=url,
        model=model_value,
        api_key_env=env_name,
        tiers=tier_map,
        timeout_s=None if timeout_s is None else float(timeout_s),
        max_tokens=None if max_tokens is None else int(max_tokens),
        extra=dict(previous.extra) if previous is not None else {},
    )

    providers = dict(store.providers)
    providers[key] = entry
    default = store.default
    if make_default is True or (make_default is None and not store.default):
        default = key
    if not default:
        warnings.append(
            "no default provider yet — the runtime stays unconfigured until "
            "`skaldir provider default <name>` (or add the first provider)"
        )
    if previous is not None and previous.config_block() != entry.config_block():
        warnings.append(f"provider {key!r} replaced in {store.path}")

    report = _commit(
        store, providers=providers, default=default, backup=backup, warnings=tuple(warnings)
    )
    report.update(
        {
            "action": "replaced" if existed else "added",
            "name": key,
            "default": default,
            "projected": default == key,
            "provider": entry.redacted(env, is_default=default == key),
            "requires_credential": entry.needs_credential(env),
        }
    )
    if entry.needs_credential(env):
        export = env_name or lp.ENV_API_KEY
        report["next_export"] = f"export {export}=<your-key>"
    return report


def remove_provider(
    name: str, *, path: str | Path | None = None, env: Mapping[str, str] | None = None,
    backup: bool = True,
) -> dict[str, Any]:
    """Remove one provider. Removing the default clears the projection — loudly."""
    key = str(name or "").strip()
    store = load_store(path, env=env)
    entry = store.entry(key)  # ProviderNotFound when it is not configured
    providers = dict(store.providers)
    providers.pop(entry.name, None)
    default = store.default
    warnings: list[str] = []
    cleared = False
    if default == entry.name:
        default = ""
        cleared = True
        if providers:
            warnings.append(
                "the default provider was removed — the runtime has no provider until "
                "`skaldir provider default <name>` runs"
            )
    report = _commit(
        store, providers=providers, default=default, backup=backup, warnings=tuple(warnings)
    )
    report.update(
        {
            "action": "removed",
            "name": entry.name,
            "default": default,
            "projection_cleared": cleared,
            "remaining": sorted(providers),
            "removed": entry.redacted(env),
        }
    )
    return report


def set_default(
    name: str, *, path: str | Path | None = None, env: Mapping[str, str] | None = None,
    backup: bool = True,
) -> dict[str, Any]:
    """Make one configured provider the active projection (``godsv.llm``)."""
    store = load_store(path, env=env)
    entry = store.entry(str(name or "").strip())
    warnings: list[str] = []
    if not entry.ready(env):
        warnings.append(
            "the default provider is not usable yet — run `skaldir provider doctor`"
        )
    report = _commit(store, providers=store.providers, default=entry.name, backup=backup,
                     warnings=tuple(warnings))
    report.update(
        {
            "action": "default",
            "name": entry.name,
            "default": entry.name,
            "projection": entry.projection(),
            "provider": entry.redacted(env, is_default=True),
        }
    )
    return report


def set_tiers(
    name: str,
    *,
    tiers: Mapping[str, str] | None = None,
    replace: bool = False,
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    backup: bool = True,
) -> dict[str, Any]:
    """Set/clear the tier → model mapping (``small``/``mid``/``main``).

    Without ``replace`` the given tiers are merged into the existing mapping;
    an empty value removes that tier. ``tiers={}`` with ``replace=True`` clears
    the whole mapping (the provider model stays the fallback).
    """
    store = load_store(path, env=env)
    entry = store.entry(str(name or "").strip())
    merged: dict[str, str] = {} if replace else dict(entry.tiers)
    changes: dict[str, str] = {}
    for tier, value in dict(tiers or {}).items():
        key_tier = str(tier).strip()
        if key_tier not in TIERS:
            raise ProviderInvalid(f"unknown tier {key_tier!r} (known: {list(TIERS)})")
        text = str(value or "").strip()
        changes[key_tier] = text
        if text:
            merged[key_tier] = text
        else:
            merged.pop(key_tier, None)
    entry.tiers = merged
    providers = dict(store.providers)
    providers[entry.name] = entry
    report = _commit(store, providers=providers, default=store.default, backup=backup)
    report.update(
        {
            "action": "tiers",
            "name": entry.name,
            "requested": changes,
            "tiers": entry.config_block().get("tiers", {}),
            "projection_updated": store.default == entry.name,
        }
    )
    return report


# --------------------------------------------------------------------------- #
# doctor                                                                      #
# --------------------------------------------------------------------------- #


def doctor(
    *, path: str | Path | None = None, env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Offline health view: per configured provider, what is missing.

    Makes no request — a diagnosis that would need the network belongs to
    ``provider test``.
    """
    store = load_store(path, env=env)
    rows = [
        store.providers[name].redacted(env, is_default=name == store.default)
        for name in store.names()
    ]
    usable = [row for row in rows if row["ready"]]
    broken = [row for row in rows if not row["ready"]]
    active_cfg = lp.resolve_provider_config(env=_env_map(env), config_path=store.path)
    active = active_cfg.redacted()
    warnings = list(store.warnings)
    next_actions: list[dict[str, str]] = []

    if not rows:
        if active["ready"]:
            warnings.append(
                "the active provider comes from a hand-written godsv.llm block, not from "
                "`skaldir provider add` — `provider test`/`provider doctor` still read it"
            )
        else:
            next_actions.append(
                {"command": EXAMPLE_ADD, "reason": "no provider is configured"}
            )
    for row in broken:
        for item in row["missing"]:
            next_actions.append(
                {
                    "command": f"skaldir provider test {row['name']}",
                    "reason": f"{row['name']}: {item}",
                }
            )
    if store.default and store.default in store.providers and not active["ready"]:
        warnings.append(
            "the default provider projects into godsv.llm but the resolved config is not "
            f"ready: {'; '.join(active['problems']) or 'base_url/model missing in the projection'}"
        )
    return {
        "config_file": str(store.path),
        "config_exists": store.exists,
        "default": store.default,
        "count": len(rows),
        "providers": rows,
        "usable": [row["name"] for row in usable],
        "broken": [row["name"] for row in broken],
        "ok": bool(rows) and not broken,
        "active": active,
        "registry_present": bool(rows),
        "warnings": warnings,
        "next_actions": next_actions,
    }


# --------------------------------------------------------------------------- #
# the one live call                                                           #
# --------------------------------------------------------------------------- #


class _RecordingClient:
    """A real ``httpx.Client`` that remembers the last JSON answer.

    ``OpenAICompatibleLLM`` returns only the completion text; the probe wants the
    ``model`` the endpoint reports (and its usage) as well. Injected clients are
    used as-is and never closed by the LLM client, so :meth:`close` is ours.
    """

    def __init__(self, inner: httpx.Client) -> None:
        self._inner = inner
        self.payload: dict[str, Any] | None = None

    def post(self, url: str, **kwargs: Any) -> Any:
        response = self._inner.post(url, **kwargs)
        self._record(response)
        return response

    def stream(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.stream(*args, **kwargs)

    def _record(self, response: Any) -> None:
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 — a body we cannot parse is the client's problem
            return
        if isinstance(payload, Mapping):
            self.payload = {str(k): v for k, v in payload.items()}

    def close(self) -> None:
        self._inner.close()


def _usage_of(payload: Mapping[str, Any] | None) -> dict[str, int]:
    usage = (payload or {}).get("usage")
    if not isinstance(usage, Mapping):
        return {}
    out: dict[str, int] = {}
    for key, value in usage.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out[str(key)] = int(value)
    return out


def _model_of(payload: Mapping[str, Any] | None) -> str:
    return str((payload or {}).get("model") or "")


def _finish_reason_of(payload: Mapping[str, Any] | None) -> str:
    choices = (payload or {}).get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, Mapping):
        return ""
    return str(first.get("finish_reason") or "")


def _safe_preview(answer: str) -> tuple[str, list[str]]:
    """Compact the answer and redact anything secret-shaped an endpoint echoed back."""
    text = _compact(answer, PROBE_PREVIEW_CHARS)
    redacted, findings = secretscan.redact(text)
    return redacted, sorted({finding.kind for finding in findings})


def probe_provider(
    name: str | None = None,
    *,
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    tier: str = PROBE_TIER,
    prompt: str = PROBE_PROMPT,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """One real completion through the real client — latency, answer and model echo.

    Raises :class:`ProviderUnusable` when the provider cannot be called as
    configured and :class:`ProviderNeedsCredential` when a remote endpoint has no
    credential — **before** any request is made. A named entry that is not in the
    registry falls back to the active ``godsv.llm`` projection, so a hand-written
    config stays testable.
    """
    store = load_store(path, env=env)
    env_map = _env_map(env)
    if str(name or "").strip():
        entry = store.entry(name)
    elif store.providers:
        entry = store.entry(store.default or None)
    else:
        active_cfg = lp.resolve_provider_config(env=env_map, config_path=store.path)
        entry = ProviderEntry(
            name=DEFAULT_ENTRY_NAME,
            kind=active_cfg.provider,
            base_url=active_cfg.base_url,
            model=active_cfg.model,
            tiers=dict(active_cfg.tier_models),
            timeout_s=active_cfg.timeout_s,
        )
        if not active_cfg.ready() and not entry.base_url:
            raise ProviderUnusable(
                "no provider configured "
                f"(config file {store.path}) — add one, e.g.: {EXAMPLE_ADD}"
            )

    tier_value = str(tier or PROBE_TIER).strip() or PROBE_TIER
    if tier_value not in TIERS:
        raise ProviderInvalid(f"unknown tier {tier_value!r} (known: {list(TIERS)})")
    if not prompt or not prompt.strip():
        raise ProviderInvalid("the probe prompt must not be empty")

    unusable = [
        item for item in entry.missing(env_map) if not item.startswith("credential is unset")
    ]
    if unusable:
        raise ProviderUnusable(
            f"provider {entry.name!r} cannot be called: " + "; ".join(unusable)
        )
    if entry.needs_credential(env_map):
        export = entry.api_key_env or lp.ENV_API_KEY
        raise ProviderNeedsCredential(
            f"provider {entry.name!r} points at {_safe_value(entry.base_url)} and has no "
            f"credential: export {export}=<your-key> (the value is never stored in {store.path})"
        )

    explicit: dict[str, Any] = {
        "provider": entry.kind,
        "base_url": entry.base_url,
        "model": entry.model,
        "tiers": dict(entry.tiers),
    }
    if entry.api_key_env:
        explicit["api_key_env"] = entry.api_key_env
    effective_timeout = timeout_s if timeout_s is not None else entry.timeout_s
    if effective_timeout is not None:
        explicit["timeout_s"] = float(effective_timeout)

    cfg = lp.resolve_provider_config(explicit, env=env_map, config_path=store.path)
    if cfg.problems:
        raise ProviderUnusable("provider config refused: " + "; ".join(cfg.problems))
    if not cfg.ready():
        raise ProviderUnusable(lp.not_configured_message(cfg))

    # The documented factory decides what this configuration resolves to; the
    # probe then runs that same client class with an observing wrapper, because
    # ``complete()`` returns text only and the probe reports the model echo too.
    resolved = lp.provider_for(cfg)
    if not isinstance(resolved, lp.OpenAICompatibleLLM):  # pragma: no cover - defensive
        raise ProviderUnusable(
            f"the resolved provider is {type(resolved).__name__}, not an HTTP provider"
        )

    recorder = _RecordingClient(httpx.Client(follow_redirects=False))
    client = lp.OpenAICompatibleLLM(cfg, client=recorder)
    started = time.monotonic()
    try:
        answer = client.complete(prompt_id=PROBE_PROMPT_ID, prompt=prompt, tier=tier_value)
    finally:
        latency_ms = int((time.monotonic() - started) * 1000)
        recorder.close()

    preview, redacted_kinds = _safe_preview(answer)
    return {
        "ok": True,
        "name": entry.name,
        "kind": entry.kind,
        "base_url": entry.base_url,
        "endpoint": lp.endpoint_for(entry.base_url),
        "config_file": str(store.path),
        "tier": tier_value,
        "model": cfg.model_for_tier(tier_value),
        "model_echo": _model_of(recorder.payload),
        "finish_reason": _finish_reason_of(recorder.payload),
        "usage": _usage_of(recorder.payload),
        "answer_chars": len(answer),
        "answer_preview": preview,
        "redacted": bool(redacted_kinds),
        "redacted_kinds": redacted_kinds,
        "latency_ms": latency_ms,
        "client": type(client).__name__,
        "resolved_by": "skaldir.llm_provider.provider_for",
        "credential": entry.credential_state(env_map),
        "max_retries": cfg.max_retries,
        "timeout_s": cfg.timeout_s,
        "prompt_id": PROBE_PROMPT_ID,
    }
