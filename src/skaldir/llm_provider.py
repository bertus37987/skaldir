"""OpenAI-compatible HTTP provider for standalone Skaldir runs (CLI-first).

``skaldir.llm`` deliberately has no provider of its own: callers receive an
``LLMClient``. This module supplies the **real** one, so the CLI can run Voice,
Jev, draft and judge work without the Hermes host. The plugin's ``ctx.llm``
path (``SKALDIR_LLM=1``, still read pre-rename as ``GODSV_LLM=1``) is untouched,
and the deterministic offline clients
(``ScriptedLLM`` / ``CallableLLM``) keep working — they stay the documented
scripted fallback of :func:`provider_for`.

Retry/timeout behaviour is **not** re-invented here: the single contract lives
in :mod:`skaldir.system_one_policy` (§43/JEV-09 ``plan_retry`` / ``backoff_delay``
/ ``MAX_ATTEMPTS``) and this client calls it. 401/403 are never retried, 422 is
a contract error, 429/529 and transport timeouts are retried with bounded
backoff only while the shared deadline still has room; malformed answers are
never retried.

Configuration contract (no secrets in the repo, none on disk, none in logs)
--------------------------------------------------------------------------

Every setting is resolved field by field, highest precedence first:

1. **explicit** — the ``config`` argument of :func:`provider_for` /
   :func:`resolve_provider_config` (a mapping or a :class:`ProviderConfig`);
2. **environment** — the variables in the table below;
3. **config file** — the ``godsv.llm`` block of a YAML file (PyYAML is already a
   runtime dependency). Path: ``$SKALDIR_LLM_CONFIG`` when set, else
   ``<runtime_dir>/config.yaml`` where ``<runtime_dir>`` follows the project's
   one default rule (``$SKALDIR_HOME`` → ``$HERMES_HOME/godsv`` →
   ``~/.hermes/godsv``). A top-level ``llm:`` block is accepted as well;
4. **defaults** — the ``DEFAULT_*`` constants of this module.

Environment variables (``config.yaml`` key → env var)
-----------------------------------------------------

=====================================  =========================================
config key (``godsv.llm``)             environment variable
=====================================  =========================================
``provider``                           ``SKALDIR_LLM_PROVIDER``
``base_url``                           ``SKALDIR_LLM_BASE_URL`` (fallbacks:
                                       ``SKALDIR_LLM_API_BASE``,
                                       ``OPENAI_BASE_URL``)
``api_key`` (env-only, never in file)  ``SKALDIR_LLM_API_KEY`` (fallbacks:
                                       ``OPENAI_API_KEY``, ``SKALDIR_LLM_KEY``)
``api_key_env`` (name only)            — (names the variable to read)
``model``                              ``SKALDIR_LLM_MODEL`` (fallback
                                       ``OPENAI_MODEL``)
``tiers.<tier>``                       ``SKALDIR_LLM_TIER_MODELS``
                                       (``"small=m1,main=m2"``)
``timeout_s``                          ``SKALDIR_LLM_TIMEOUT`` (default 30)
``total_timeout_s``                    ``SKALDIR_LLM_TOTAL_TIMEOUT``
                                       (default ``timeout_s * (max_retries+1)``)
``max_retries``                        ``SKALDIR_LLM_MAX_RETRIES`` (default 2)
``backoff_s``                          ``SKALDIR_LLM_BACKOFF_S`` (default 0.25)
``max_tokens``                         ``SKALDIR_LLM_MAX_TOKENS`` (default 900)
``stream``                             ``SKALDIR_LLM_STREAM`` (``0``/``1``)
— (file path)                          ``SKALDIR_LLM_CONFIG``
=====================================  =========================================

``max_tokens_field``, ``temperature``, ``headers``, ``extra_body`` and
``responses`` are config-file/argument-only (no environment form). ``provider``
accepts ``openai-compatible`` (default), ``scripted`` (needs ``responses``) and
``none`` (refused loudly — a disabled provider must not look like a working one).

Example ``<runtime_dir>/config.yaml`` (i.e. ``$SKALDIR_HOME/config.yaml``,
``$HERMES_HOME/godsv/config.yaml`` or ``~/.hermes/godsv/config.yaml`` — a host
``~/.hermes/config.yaml`` works too, point ``$SKALDIR_LLM_CONFIG`` at it)::

    godsv:
      llm:
        provider: openai-compatible
        base_url: http://127.0.0.1:8080/v1     # llama.cpp / vLLM / OpenRouter / OpenAI
        model: qwen2.5-7b-instruct
        api_key_env: SKALDIR_LLM_API_KEY         # the NAME of the variable, not the key
        tiers:
          small: qwen2.5-3b-instruct
          main: qwen2.5-72b-instruct
        timeout_s: 30
        max_retries: 2
        stream: false

Secrets: the key is read from the **environment only**. A literal ``api_key:`` /
``key:`` / ``token:`` / ``secret:`` value inside the config file is *refused*
(:class:`ProviderConfigError`) — a file in a repo or in ``$HOME`` is not a
secret store. Keep it in the service environment (systemd ``EnvironmentFile``,
container env, shell profile). The key is never logged, never written, and
never rendered: :class:`ProviderConfig` redacts it in ``repr`` and
:func:`provider_status` reports only ``"set"``/``"unset"`` plus the variable
name. Provider error text is included in messages, never request headers.

Quick start for the owner::

    export SKALDIR_LLM_BASE_URL=https://api.openai.com/v1
    export SKALDIR_LLM_MODEL=gpt-4o-mini
    export SKALDIR_LLM_API_KEY=sk-...            # never committed, never logged

``SKALDIR_LLM=1`` is *not* this provider — that switch gates the Hermes plugin's
host-owned ``ctx.llm``. This adapter needs no host and no plugin.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import struct
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx  # declared dependency (pip: httpx>=0.27); the CLI installs it

from . import prompt_cache as pc
from .env import expand, getenv
from .llm import TIERS, LLMClient, LLMError, LLMProviderError, ScriptedLLM
from .system_one_policy import (
    DEFAULT_BACKOFF_BASE as RETRY_BACKOFF_BASE,
)
from .system_one_policy import (
    MAX_ATTEMPTS,
    plan_retry,
    total_attempts,
)

# --------------------------------------------------------------------------- #
# defaults + environment contract                                             #
# --------------------------------------------------------------------------- #

DEFAULT_PROVIDER = "openai-compatible"
PROVIDERS = ("openai-compatible", "scripted", "none")

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_RETRIES = MAX_ATTEMPTS - 1  # §43: MAX_ATTEMPTS total attempts
DEFAULT_MAX_TOKENS = 900
DEFAULT_MAX_TOKENS_FIELD = "max_tokens"

#: The reasoning-budget guard's two bounds. One bounded retry only: the budget is doubled, and
#: never past the ceiling — an unbounded escalation would turn a provider quirk into a spend.
REASONING_BUDGET_FACTOR = 2
REASONING_BUDGET_CEILING = 32768
DEFAULT_BACKOFF_S = RETRY_BACKOFF_BASE

ENV_PROVIDER = "SKALDIR_LLM_PROVIDER"
ENV_BASE_URL = "SKALDIR_LLM_BASE_URL"
ENV_BASE_URL_FALLBACKS = ("SKALDIR_LLM_API_BASE", "OPENAI_BASE_URL")
ENV_API_KEY = "SKALDIR_LLM_API_KEY"
ENV_API_KEY_FALLBACKS = ("OPENAI_API_KEY", "SKALDIR_LLM_KEY")
ENV_MODEL = "SKALDIR_LLM_MODEL"
ENV_MODEL_FALLBACKS = ("OPENAI_MODEL",)
ENV_TIER_MODELS = "SKALDIR_LLM_TIER_MODELS"
ENV_TIMEOUT = "SKALDIR_LLM_TIMEOUT"
ENV_TOTAL_TIMEOUT = "SKALDIR_LLM_TOTAL_TIMEOUT"
ENV_MAX_RETRIES = "SKALDIR_LLM_MAX_RETRIES"
ENV_BACKOFF = "SKALDIR_LLM_BACKOFF_S"
ENV_MAX_TOKENS = "SKALDIR_LLM_MAX_TOKENS"
ENV_STREAM = "SKALDIR_LLM_STREAM"
ENV_CONFIG = "SKALDIR_LLM_CONFIG"
#: ``SKALDIR_PROMPT_CACHE_LEDGER`` — where measured requests are recorded for
#: ``skaldir prompt cache status``. Unset means "do not touch the filesystem";
#: ``1``/``true`` means the default path under the runtime directory.
ENV_CACHE_LEDGER = "SKALDIR_PROMPT_CACHE_LEDGER"

#: field → environment variables, for ``skaldir llm status`` and for the
#: "what do I export" message when nothing is configured.
ENV_CONTRACT: dict[str, tuple[str, ...]] = {
    "provider": (ENV_PROVIDER,),
    "base_url": (ENV_BASE_URL, *ENV_BASE_URL_FALLBACKS),
    "api_key": (ENV_API_KEY, *ENV_API_KEY_FALLBACKS),
    "model": (ENV_MODEL, *ENV_MODEL_FALLBACKS),
    "tiers": (ENV_TIER_MODELS,),
    "timeout_s": (ENV_TIMEOUT,),
    "total_timeout_s": (ENV_TOTAL_TIMEOUT,),
    "max_retries": (ENV_MAX_RETRIES,),
    "backoff_s": (ENV_BACKOFF,),
    "max_tokens": (ENV_MAX_TOKENS,),
    "stream": (ENV_STREAM,),
}

#: keys that must never carry a value in a config file (a file is not a vault).
SECRET_FILE_KEYS = (
    "api_key",
    "apikey",
    "api_key_value",
    "key",
    "secret",
    "token",
    "access_token",
    "bearer",
    "password",
)


# --------------------------------------------------------------------------- #
# errors                                                                      #
# --------------------------------------------------------------------------- #


class ProviderConfigError(LLMError):
    """The provider is missing or misconfigured — no request was attempted."""


class ProviderTimeoutError(LLMProviderError):
    """No usable answer in time (per-request timeout or transport failure)."""


class ProviderAuthError(LLMProviderError):
    """401/403 — the credential was refused. Never retried (§43)."""


class ProviderRateLimitError(LLMProviderError):
    """429 — retried inside the shared deadline, exhausted in the end (§43)."""


class ProviderResponseError(LLMError):
    """2xx with a body we cannot read (not JSON, stream garbage, empty)."""


class ReasoningBudgetError(ProviderResponseError):
    """**Named** refusal: the endpoint spent the whole budget on reasoning and answered nothing.

    The measured shape (twice on 2026-09-19, a routing block and a compaction asker): HTTP 200,
    ``finish_reason=length``, ``content`` empty, ``reasoning_content``/``reasoning_tokens``
    filled. It is a *budget artifact*, not a broken endpoint and not an empty answer — the
    model was still thinking when the ceiling hit. The call path retries it once with a larger
    budget (:data:`REASONING_BUDGET_FACTOR`, capped at :data:`REASONING_BUDGET_CEILING`) and
    records both attempts on the answer; when the second attempt is empty too, *this* is what
    the caller gets — a name, the counters, and the budget it tried, never an empty answer.
    """

    #: stable identifier for a caller that maps errors to verdicts
    code = "reasoning_budget_exhausted"


class _EmptyCompletion(ProviderResponseError):
    """Internal: a 2xx whose ``content`` came back empty. Carries the payload it was read from.

    It stays a :class:`ProviderResponseError` (so every existing caller keeps its behaviour and
    a malformed answer is still never retried by the §43 transport policy) but lets the *call
    path* look at ``choices[0].finish_reason`` and decide whether this is the reasoning-budget
    artifact the guard knows how to retry.
    """

    def __init__(self, message: str, payload: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.payload: Mapping[str, Any] = payload


# --------------------------------------------------------------------------- #
# configuration                                                               #
# --------------------------------------------------------------------------- #


@dataclass
class ProviderConfig:
    """Resolved provider settings. The API key never shows up in ``repr``."""

    provider: str = DEFAULT_PROVIDER
    base_url: str = ""
    api_key: str = ""
    api_key_env: str = ""
    model: str = ""
    tier_models: dict[str, str] = field(default_factory=dict)
    timeout_s: float = DEFAULT_TIMEOUT_S
    total_timeout_s: float | None = None
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_s: float = DEFAULT_BACKOFF_S
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_tokens_field: str = DEFAULT_MAX_TOKENS_FIELD
    temperature: float | None = None
    stream: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    responses: dict[str, str] = field(default_factory=dict)
    # Provider capability flags (CTX-02) — never inferred from the model name.
    supports_tool_discovery: bool = False
    supports_programmatic_tools: bool = False
    supports_explicit_cache_breakpoints: bool = False
    supports_context_compaction: bool = False
    config_file: str = ""
    sources: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    # -- safety: the key is never rendered ---------------------------------- #

    def redacted(self) -> dict[str, Any]:
        """JSON-safe view (CLI/panel/logs) — the key is reduced to set/unset."""
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "endpoint": endpoint_for(self.base_url) if self.base_url else "",
            "model": self.model,
            "tiers": dict(self.tier_models),
            "api_key": "set" if self.api_key else "unset",
            "api_key_env": self.api_key_env,
            "timeout_s": self.timeout_s,
            "total_timeout_s": self.effective_total_timeout(),
            "max_retries": self.max_retries,
            "backoff_s": self.backoff_s,
            "max_tokens": self.max_tokens,
            "max_tokens_field": self.max_tokens_field,
            "temperature": self.temperature,
            "stream": self.stream,
            "headers": sorted(self.headers),
            "extra_body": dict(self.extra_body),
            "scripted_ids": sorted(self.responses),
            "config_file": self.config_file,
            "sources": dict(self.sources),
            "problems": list(self.problems),
            "ready": self.ready(),
        }

    def __repr__(self) -> str:  # pragma: no cover - trivial, asserted in tests
        return (
            "ProviderConfig("
            f"provider={self.provider!r}, base_url={self.base_url!r}, "
            f"model={self.model!r}, api_key=<{'set' if self.api_key else 'unset'}>, "
            f"stream={self.stream}, timeout_s={self.timeout_s}, "
            f"max_retries={self.max_retries})"
        )

    def effective_total_timeout(self) -> float:
        if self.total_timeout_s is not None:
            return float(self.total_timeout_s)
        return float(self.timeout_s) * (int(self.max_retries) + 1)

    def ready(self) -> bool:
        """True when a real HTTP call can be attempted with these settings."""
        return (
            not self.problems
            and self.provider == "openai-compatible"
            and bool(self.base_url)
            and bool(self.model)
        )

    def model_for_tier(self, tier: str) -> str:
        return self.tier_models.get(tier) or self.model

    def cheap_tier(self) -> str:
        """``small``, else ``mid``, else ``main`` — the tier a *cheap* decision should ask for.

        The keep/shorten/drop questions of a compaction are a handful of tokens of judgement;
        asking the **main** model for them is spend with no answer behind it. Which tiers a
        block really declares is the config's own fact (``tiers.small``/``tiers.mid``, written
        by ``provider add --tier-small``/``--tier-mid``): with no declared tier the main model
        stands, so nothing is invented and a single-model block behaves exactly as before.
        """
        for tier in ("small", "mid"):
            if str(self.tier_models.get(tier) or "").strip():
                return tier
        return "main"


def cheap_decision_tier(client: Any, *, fallback: str = "main") -> str:
    """Which tier a **cheap** decision asks for, read off the client that would answer it.

    ``small`` when the block declares one, else ``mid``, else ``fallback``. Used by the
    compaction asker (the Jev keep/shorten/drop questions) so a few tokens of judgement do not
    go to the main model by default. A client that cannot say (a fake in a test, an older
    build) answers ``fallback`` — nothing is inferred from its absence.
    """
    method = getattr(client, "cheap_tier", None)
    if callable(method):
        try:
            tier = str(method() or "").strip()
        except Exception:  # noqa: BLE001 — a client that cannot answer is not a failure
            tier = ""
        if tier:
            return tier
    return str(fallback or "main")


def endpoint_for(base_url: str) -> str:
    """``.../v1`` → ``.../v1/chat/completions`` (an explicit path is kept)."""
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _first_env(env: Mapping[str, str], names: tuple[str, ...]) -> tuple[str, str] | None:
    """First non-empty value among ``names`` — each ``SKALDIR_*`` name before its legacy twin.

    `expand` adds the pre-rename spelling right after every primary name
    (``SKALDIR_LLM_BASE_URL`` → ``GODSV_LLM_BASE_URL``) and leaves third-party names
    (``OPENAI_API_KEY``) untouched, so the documented precedence is unchanged and
    an exported ``GODSV_LLM_*`` still works.
    """
    for name in expand(names):
        value = env.get(name)
        if value is not None and str(value).strip() != "":
            return name, str(value).strip()
    return None


def _compact(value: object, limit: int = 200) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _as_float(value: Any, *, key: str, source: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderConfigError(
            f"llm.{key} from {source} is not a number: {_compact(value)}"
        ) from exc


def _as_int(value: Any, *, key: str, source: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ProviderConfigError(
            f"llm.{key} from {source} is not an integer: {_compact(value)}"
        ) from exc


def _as_bool(value: Any, *, key: str, source: str) -> bool:
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off", ""):
        return False
    raise ProviderConfigError(f"llm.{key} from {source} is not a boolean: {_compact(value)}")


def _as_mapping(value: Any, *, key: str, source: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ProviderConfigError(f"llm.{key} from {source} must be a mapping")
    return {str(k): v for k, v in value.items()}


def _as_str_map(value: Any, *, key: str, source: str) -> dict[str, str]:
    return {k: str(v) for k, v in _as_mapping(value, key=key, source=source).items()}


def _as_tier_models(value: Any, *, key: str, source: str) -> dict[str, str]:
    """``{small: m1}`` or ``"small=m1,main=m2"`` → mapping (tier keys checked)."""
    if isinstance(value, str):
        parsed: dict[str, Any] = {}
        for chunk in value.split(","):
            piece = chunk.strip()
            if not piece:
                continue
            tier, sep, model = piece.partition("=")
            if not sep:
                raise ProviderConfigError(
                    f"llm.{key} from {source} expects 'tier=model' pairs, got {_compact(piece)!r}"
                )
            parsed[tier.strip()] = model.strip()
        mapping = parsed
    else:
        mapping = _as_str_map(value, key=key, source=source)
    unknown = [tier for tier in mapping if tier not in TIERS]
    if unknown:
        raise ProviderConfigError(
            f"llm.{key} from {source} has unknown tiers {unknown!r} (known: {list(TIERS)})"
        )
    return {tier: str(model) for tier, model in mapping.items() if str(model).strip()}


def default_config_file() -> Path:
    """``$SKALDIR_LLM_CONFIG`` → ``<runtime_dir>/config.yaml`` (one default rule)."""
    override = getenv(ENV_CONFIG)
    if override and override.strip():
        return Path(override.strip()).expanduser()
    from .registry import default_runtime_dir  # local import: keeps import cost flat

    return default_runtime_dir() / "config.yaml"


def _load_block(path: Path | None) -> tuple[dict[str, Any], str]:
    """Read the ``godsv.llm`` block (or a top-level ``llm:``) from the file.

    The read+parse is memoised on the file's identity (``perf_runtime.config_block``):
    a run resolves the provider config once per step, and the file does not change
    inside a run, so re-parsing it per call is pure cost. The cache key is
    ``(path, mtime_ns, size, inode)``, so a rewrite is always seen; set
    ``SKALDIR_PERF_NOCACHE=1`` to get the unconditional re-read back.
    """
    if path is None:
        return {}, ""

    def _read(target: Path) -> tuple[dict[str, Any], str]:
        return _parse_block(target)

    from .perf_runtime import config_block

    return config_block(path, _read)


def _parse_block(path: Path) -> tuple[dict[str, Any], str]:
    """The actual file read; separated so the cache cannot change its semantics."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, ""
    except OSError as exc:
        raise ProviderConfigError(f"cannot read llm config {path}: {type(exc).__name__}") from exc
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - PyYAML is a runtime dependency
        raise ProviderConfigError("PyYAML is required to read the llm config file") from exc
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ProviderConfigError(
            f"llm config {path} is not valid YAML: {type(exc).__name__}"
        ) from exc
    if not isinstance(data, Mapping):
        raise ProviderConfigError(f"llm config {path} must contain a mapping")
    godsv = data.get("godsv")
    block = godsv.get("llm") if isinstance(godsv, Mapping) else None
    if not isinstance(block, Mapping):
        top = data.get("llm")
        block = top if isinstance(top, Mapping) else None
    if not isinstance(block, Mapping):
        return {}, str(path)
    return {str(k): v for k, v in block.items()}, str(path)


def _explicit_mapping(config: Any) -> dict[str, Any]:
    if config is None:
        return {}
    if isinstance(config, ProviderConfig):
        return {
            "provider": config.provider,
            "base_url": config.base_url,
            "api_key": config.api_key,
            "api_key_env": config.api_key_env,
            "model": config.model,
            "tiers": dict(config.tier_models),
            "timeout_s": config.timeout_s,
            "total_timeout_s": config.total_timeout_s,
            "max_retries": config.max_retries,
            "backoff_s": config.backoff_s,
            "max_tokens": config.max_tokens,
            "max_tokens_field": config.max_tokens_field,
            "temperature": config.temperature,
            "stream": config.stream,
            "headers": dict(config.headers),
            "extra_body": dict(config.extra_body),
            "responses": dict(config.responses),
        }
    if isinstance(config, Mapping):
        return {str(k): v for k, v in config.items()}
    raise ProviderConfigError(
        "provider config must be a mapping or ProviderConfig, got "
        f"{type(config).__name__}"
    )


def _default_max_tokens_field(base_url: str) -> str:
    """The token-budget field the endpoint expects when the config names none.

    Modern OpenAI chat models reject ``max_tokens`` (HTTP 400: use ``max_completion_tokens``);
    every other endpoint keeps the wire-shape default. A config that names the field always
    wins — this only fills the gap.
    """
    lowered = str(base_url or "").lower()
    if "api.openai.com" in lowered:
        return "max_completion_tokens"
    return DEFAULT_MAX_TOKENS_FIELD


def _opencode_session_id(base_url: str) -> str:
    """A stable routing id for OpenCode's required ``x-opencode-session`` header.

    Deterministic per endpoint (a UUIDv5 over the base URL), so repeated loads of the same
    config send the same id — the endpoint routes on it. OpenCode Go/Zen return HTTP 400
    without the header, before a single token of the answer.
    """
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, str(base_url or "").rstrip("/")))


def resolve_provider_config(
    config: Any = None,
    *,
    env: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
) -> ProviderConfig:
    """Resolve settings with the documented precedence (explicit > env > file).

    Never reads a secret from a file, never mutates anything, never raises for
    "unconfigured" — inspect :attr:`ProviderConfig.problems` / ``ready()``.
    """
    env_map: dict[str, str] = (
        {str(k): str(v) for k, v in os.environ.items()} if env is None else dict(env)
    )
    explicit = _explicit_mapping(config)
    path = Path(config_path).expanduser() if config_path else default_config_file()
    block, used_path = _load_block(path)
    sources: dict[str, str] = {}
    problems: list[str] = []

    def pick(
        key: str,
        *,
        env_names: tuple[str, ...] = (),
        default: Any = None,
        cast: Any = None,
    ) -> Any:
        value = explicit.get(key)
        if value is not None and str(value).strip() != "":
            source = "explicit"
        else:
            found = _first_env(env_map, env_names)
            if found is not None:
                value, source = found[1], f"env:{found[0]}"
            elif block.get(key) is not None and str(block.get(key)).strip() != "":
                value, source = block[key], f"config:{used_path}"
            else:
                value, source = default, "default"
        sources[key] = source
        if cast is not None and value is not None and str(value).strip() != "":
            value = cast(value, key=key, source=source)
        return value

    provider = str(pick("provider", env_names=ENV_CONTRACT["provider"], default=DEFAULT_PROVIDER))
    base_url = str(pick("base_url", env_names=ENV_CONTRACT["base_url"], default="") or "")
    model = str(pick("model", env_names=ENV_CONTRACT["model"], default="") or "")
    tier_models = pick(
        "tiers", env_names=ENV_CONTRACT["tiers"], default={}, cast=_as_tier_models
    )
    timeout_s = float(
        pick("timeout_s", env_names=ENV_CONTRACT["timeout_s"], default=DEFAULT_TIMEOUT_S,
             cast=_as_float)
    )
    total_raw = pick("total_timeout_s", env_names=ENV_CONTRACT["total_timeout_s"], default=None)
    total_timeout_s = (
        None
        if total_raw is None
        else _as_float(
            total_raw, key="total_timeout_s", source=sources["total_timeout_s"]
        )
    )
    max_retries = int(
        pick("max_retries", env_names=ENV_CONTRACT["max_retries"], default=DEFAULT_MAX_RETRIES,
             cast=_as_int)
    )
    backoff_s = float(
        pick("backoff_s", env_names=ENV_CONTRACT["backoff_s"], default=DEFAULT_BACKOFF_S,
             cast=_as_float)
    )
    max_tokens = int(
        pick("max_tokens", env_names=ENV_CONTRACT["max_tokens"], default=DEFAULT_MAX_TOKENS,
             cast=_as_int)
    )
    stream = bool(
        pick("stream", env_names=ENV_CONTRACT["stream"], default=False, cast=_as_bool)
    )
    max_tokens_field = str(pick("max_tokens_field", default=""))
    if not max_tokens_field:
        max_tokens_field = _default_max_tokens_field(base_url)
    temperature_raw = pick("temperature", default=None)
    temperature = (
        None if temperature_raw is None
        else _as_float(temperature_raw, key="temperature", source=sources["temperature"])
    )
    headers = _as_str_map(
        pick("headers", default={}), key="headers", source=sources["headers"] or "default"
    )
    # OpenCode Go/Zen require `x-opencode-session` on every request (HTTP 400 without it).
    # A config that already names the header keeps its own value.
    if "opencode.ai" in str(base_url or "").lower() and not any(
        name.lower() == "x-opencode-session" for name in headers
    ):
        headers["x-opencode-session"] = _opencode_session_id(base_url)
    extra_body = _as_mapping(
        pick("extra_body", default={}), key="extra_body", source=sources["extra_body"] or "default"
    )
    responses = _as_str_map(
        pick("responses", default={}), key="responses", source=sources["responses"] or "default"
    )

    # --- the key: environment only --------------------------------------- #
    api_key = str(explicit.get("api_key") or "").strip()
    api_key_env = ""
    if api_key:
        sources["api_key"] = "explicit"
    else:
        named = str(
            explicit.get("api_key_env") or block.get("api_key_env") or ""
        ).strip()
        found = _first_env(env_map, (named,)) if named else None
        if found is None:
            found = _first_env(env_map, (ENV_API_KEY, *ENV_API_KEY_FALLBACKS))
        if found is not None:
            api_key, api_key_env = found[1], found[0]
            sources["api_key"] = f"env:{found[0]}"
        else:
            sources["api_key"] = "unset"
            api_key_env = named

    # --- a file is not a vault ------------------------------------------- #
    for name in SECRET_FILE_KEYS:
        if name in block:
            problems.append(
                f"config file {used_path} carries a literal '{name}:' value — secrets are "
                f"never read from config files; export {ENV_API_KEY} instead and keep only "
                f"'api_key_env: {ENV_API_KEY}' in the file"
            )

    # --- validation ------------------------------------------------------- #
    provider = provider.strip().lower()
    if provider not in PROVIDERS:
        problems.append(f"unknown provider {provider!r} (expected one of {list(PROVIDERS)})")
    if provider == "none":
        problems.append(
            "provider is explicitly disabled; set SKALDIR_LLM_BASE_URL + SKALDIR_LLM_MODEL "
            "(env) or godsv.llm.base_url + godsv.llm.model (config.yaml) to enable it"
        )
    if base_url and not base_url.startswith(("http://", "https://")):
        problems.append(f"base_url must start with http:// or https://, got {_compact(base_url)!r}")
    if timeout_s <= 0:
        problems.append(f"timeout_s must be > 0, got {timeout_s}")
    if max_retries < 0:
        problems.append(f"max_retries must be >= 0, got {max_retries}")
    if backoff_s < 0:
        problems.append(f"backoff_s must be >= 0, got {backoff_s}")
    if max_tokens <= 0:
        problems.append(f"max_tokens must be > 0, got {max_tokens}")

    return ProviderConfig(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        api_key_env=api_key_env,
        model=model,
        tier_models=tier_models,
        timeout_s=timeout_s,
        total_timeout_s=total_timeout_s,
        max_retries=max_retries,
        backoff_s=backoff_s,
        max_tokens=max_tokens,
        max_tokens_field=max_tokens_field,
        temperature=temperature,
        stream=stream,
        headers=headers,
        extra_body=extra_body,
        responses=responses,
        config_file=used_path,
        sources=sources,
        problems=problems,
    )


def not_configured_message(cfg: ProviderConfig | None = None) -> str:
    """The exact exports the owner has to set to get a real model."""
    hint = (
        f"export {ENV_BASE_URL}=https://api.openai.com/v1\n"
        f"export {ENV_MODEL}=gpt-4o-mini\n"
        f"export {ENV_API_KEY}=<your-key>        # never commit this"
    )
    if cfg is not None and cfg.api_key and not cfg.base_url:
        return f"no base_url configured — add:\n{hint}"
    if cfg is not None and cfg.api_key_env and not cfg.api_key:
        return (
            f"{cfg.api_key_env} is not set in the environment (it is only named in the "
            f"config file) — export it:\n"
            f"export {cfg.api_key_env}=<your-key>"
        )
    return (
        "no LLM provider configured: set base_url + model (+ api_key for remote endpoints):\n"
        f"{hint}"
    )


def provider_status(
    config: Any = None,
    *,
    env: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Redacted, JSON-safe provider state for the CLI/panel (never the key)."""
    cfg = resolve_provider_config(config, env=env, config_path=config_path)
    status = cfg.redacted()
    status["env_contract"] = {key: list(names) for key, names in ENV_CONTRACT.items()}
    status["env_lookup_order"] = {
        "base_url": [ENV_BASE_URL, *ENV_BASE_URL_FALLBACKS],
        "api_key": [ENV_API_KEY, *ENV_API_KEY_FALLBACKS],
        "model": [ENV_MODEL, *ENV_MODEL_FALLBACKS],
    }
    status["hint"] = "" if cfg.ready() else not_configured_message(cfg)
    if cfg.ready() and not cfg.api_key and not _is_local(cfg.base_url):
        named = str(cfg.api_key_env or "").strip() or ENV_API_KEY
        status["warning"] = (
            "api_key is unset for a non-local base_url — a remote endpoint will answer "
            f"401; export {named}"
        )
    return status


def _is_local(base_url: str) -> bool:
    host = base_url.split("://", 1)[-1].split("/", 1)[0].rsplit("@", 1)[-1]
    host = host.rsplit(":", 1)[0].lower()
    return host in ("localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0", "host.docker.internal")


def is_local_base_url(base_url: str) -> bool:
    """Public spelling of :func:`_is_local` — a loopback/LAN endpoint needs no key.

    The fallback chain (``skaldir.provider_catalog``) has to know whether a
    missing credential is a blocker or simply expected, and it must not reach
    into a private name to find out.
    """
    return _is_local(base_url)


# --------------------------------------------------------------------------- #
# the client                                                                  #
# --------------------------------------------------------------------------- #


def _resolve_cache_ledger(value: Any) -> pc.CacheLedger | None:
    """The prompt-cache ledger this client appends to, or ``None`` for no I/O.

    Precedence: an explicit :class:`~skaldir.prompt_cache.CacheLedger` (or a path,
    or ``True`` for the default) → ``SKALDIR_PROMPT_CACHE_LEDGER`` (a path, or
    ``1``/``true``/``yes``/``on`` for the default) → ``None``. The default is
    ``None`` on purpose: an ordinary API call never writes to the filesystem, so
    the ledger has to be asked for.
    """
    if value is False:
        return None
    if value is True:
        return pc.CacheLedger(pc.default_ledger_path())
    if isinstance(value, pc.CacheLedger):
        return value
    if value:
        return pc.CacheLedger(str(value))
    text = str(getenv(ENV_CACHE_LEDGER) or "").strip()
    if not text or text.lower() in {"0", "false", "no", "off"}:
        return None
    if text.lower() in {"1", "true", "yes", "on"}:
        return pc.CacheLedger(pc.default_ledger_path())
    return pc.CacheLedger(text)


def _empty_stream_stats() -> dict[str, Any]:
    """The counters one streamed model call fills (``OpenAICompatibleLLM.stream_stats``).

    ``deltas`` and ``chars`` are **measured** (one per SSE content delta / its length);
    ``first_content_ms`` is measured from the request being sent to the first *content*
    delta (a role-only chunk is not a token and must not count). ``tokens_estimate`` is
    ``chars//4`` and is labelled as such by whoever displays it — no tokenizer is bundled.
    ``sink_errors`` counts a sink that raised: a renderer must never break a run.
    """
    return {
        "streaming_requested": False,
        "deltas": 0,
        "chars": 0,
        "first_content_ms": None,
        "last_content_ms": None,
        "sink_errors": 0,
    }


class OpenAICompatibleLLM:
    """``LLMClient`` over any ``/v1/chat/completions`` endpoint.

    Works against OpenAI, OpenRouter, Together, Groq, a local llama.cpp
    (``llama-server``) or vLLM — anything that speaks the OpenAI chat format.
    Streaming and non-streaming are both supported; the tier selects the model
    when ``tiers`` maps one.
    """

    name = "openai-compatible"

    def __init__(
        self,
        config: ProviderConfig,
        *,
        client: Any = None,
        cache_ledger: Any = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> None:
        self._cfg = config
        if not config.base_url:
            raise ProviderConfigError(not_configured_message(config))
        if not config.model:
            raise ProviderConfigError(
                f"no model configured — export {ENV_MODEL} or set godsv.llm.model in "
                f"{config.config_file or 'config.yaml'}"
            )
        #: The sink every *content* delta is handed to as it arrives off the wire. This is
        #: the half that used to be missing: the SSE reader always parsed the deltas, but
        #: they were folded into the final answer and discarded, so nothing user-visible
        #: happened until the model had finished. ``None`` = accumulate only (exactly the
        #: old behaviour, and what a machine consumer wants). The loop sets it per call
        #: (``stream_render.StreamRenderer.delta`` is the product's implementation).
        self.on_delta: Callable[[str], None] | None = on_delta
        #: What the sink observed, filled by ``_note_delta``. Read by the loop into the run
        #: result so an answer can say whether it streamed and how long the first token took.
        self.stream_stats: dict[str, Any] = _empty_stream_stats()
        # ``client`` is used as-is and never closed by us; without it every call
        # takes the pooled client from ``perf_runtime`` (one connection per
        # process, not one per call — see ``_client_scope``).
        self._injected_client = client
        self._owned_client: Any = None
        #: monotonic stamp of the request that is in flight, and of its first
        #: *content* delta — the two halves of the first-byte latency
        #: (``ChatResult.first_byte_ms``). ``None`` until a streamed call runs.
        self._request_started: float | None = None
        self._first_byte_at: float | None = None
        # -- prompt cache (:mod:`skaldir.prompt_cache`) --------------------- #
        #: Where measured requests are appended when a ledger is asked for.
        #: ``SKALDIR_PROMPT_CACHE_LEDGER`` names one (``1`` = the default path
        #: under the runtime dir); without it nothing touches the filesystem.
        self._cache_ledger = _resolve_cache_ledger(cache_ledger)
        #: guards the previous request's prefix (subagents share one client)
        self._cache_lock = threading.Lock()
        self._last_prefix: pc.StablePrefix | None = None
        self._cache_strategy: pc.CacheStrategy | None = None
        #: how many ledger writes failed — reported, never raised
        self.cache_ledger_failures = 0
        #: the reasoning-budget guard's last verdict, on the paths that read a ``str``
        #: (:meth:`complete`): ``{"attempts": [{budget, finish_reason, content_chars,
        #: reasoning_tokens}, …], "retried": bool}``. Empty until a call had to widen the
        #: budget — the answer-shaped paths carry the same evidence on ``ChatResult.budget``.
        self.last_budget: dict[str, Any] = {}

    @property
    def config(self) -> ProviderConfig:
        return self._cfg

    def cheap_tier(self) -> str:
        """Which tier a cheap decision should ask for — the config's own answer.

        ``small`` when this block declares one, else ``mid``, else ``main`` (see
        :meth:`ProviderConfig.cheap_tier`). Used by the compaction asker, whose keep/shorten/
        drop questions are a handful of tokens of judgement and have no business on the main
        model when a cheaper one is configured.
        """
        return self._cfg.cheap_tier()

    def endpoint(self) -> str:
        return endpoint_for(self._cfg.base_url)

    # -- public interface -------------------------------------------------- #

    def complete(self, *, prompt_id: str, prompt: str, tier: str) -> str:
        """Call the endpoint; retries/timeouts follow :mod:`skaldir.system_one_policy`."""
        url = self.endpoint()
        body = self._body(prompt, tier)
        deadline = time.monotonic() + self._cfg.effective_total_timeout()
        attempt = 0
        last: LLMError = LLMError("provider call failed")
        with self._client_scope() as client:
            while True:
                attempt += 1
                try:
                    raw = self._send(client, url, body, timeout=self._attempt_timeout(deadline))
                    return self._extract(raw)
                except _EmptyCompletion as exc:
                    # the reasoning-budget guard, text path: one bounded retry with a bigger
                    # budget, then a named refusal (never an empty answer). ``None`` = not the
                    # budget artifact, so the empty completion is reported as it always was.
                    text = self._budget_retry_text(client, url, body, exc, deadline=deadline)
                    if text is not None:
                        return text
                    raise
                except ProviderResponseError:
                    raise  # a malformed answer is not in the retry policy (§43)
                except ProviderTimeoutError as exc:
                    last, status, timed_out = exc, None, True
                except LLMProviderError as exc:
                    last, status, timed_out = exc, exc.status, False
                decision = plan_retry(
                    status=status,
                    attempt=attempt,
                    max_attempts=self._cfg.max_retries + 1,
                    remaining_deadline=deadline - time.monotonic(),
                    timeout=timed_out,
                    backoff_base=self._cfg.backoff_s,
                )
                self._gate_retry(last, decision, attempt)
                time.sleep(decision.delay)

    def _gate_retry(self, last: LLMError, decision: Any, attempt: int) -> None:
        """The commit point has the last word over the §43 retry policy (§94).

        Raises the recorded error when no further attempt may be made: either the §43 policy
        itself refused (401/422/unclassified/max attempts), or a client-side gate
        (``skaldir.progress_guard.RetryGate``, installed by the agent loop) refused because
        something visible was already committed — re-sending the request would hand the user
        the same answer a second time. The gate can only *narrow* the policy, never widen it,
        and the refusal is recorded by the gate's own ``decide`` so a run counts it once.

        Both provider paths ask it: ``chat()`` always did, ``complete()`` did not (the
        integration seam named in the final-acceptance audit, §6d) — so a non-streaming prompt
        that prints to a user was unguarded while a streamed one was not.
        """
        if not decision.retry:
            raise self._with_attempts(last, attempt - 1) from last
        gate = getattr(self, "retry_gate", None)
        if gate is None:
            return
        gate_decision = gate.decide(attempt=attempt, error=str(last)[:200] or "provider retry")
        if not gate_decision.retry:
            raise self._with_attempts(last, attempt - 1) from last

    # -- request building -------------------------------------------------- #

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tier: str = "main",
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: Any = None,
        response_format: Mapping[str, Any] | None = None,
        stream: bool | None = None,
    ) -> ChatResult:
        """One tool-calling request; retries/timeouts follow the same §43 policy.

        This is the *only* difference to :meth:`complete`: the body carries
        ``tools``/``tool_choice`` when tools are offered, and the answer is read
        as text **plus** ``tool_calls`` (:class:`ChatResult`). An answer with no
        tool call is normal and is returned as such — the caller decides whether
        the loop is finished, this method never invents a tool call.

        ``response_format`` (see :func:`json_schema_response_format`) travels to
        the endpoint **unchanged**: this client does not validate the answer and
        never coerces a value — :mod:`skaldir.structured` owns that, and an
        endpoint that refuses the field is reported by its own error
        (:class:`StructuredOutputNotSupportedError`), never silently retried
        without it.

        **Prompt cache.** Every answer carries the provider's own verdict in
        :attr:`ChatResult.cache` (``hit``/``miss``/``unknown``, read from the
        usage counters — see :mod:`skaldir.prompt_cache`), plus the byte-compare
        *prediction* for this request against the previous one. When the
        configured provider needs explicit markers (the Anthropic family) the
        request is sent with ``cache_control`` on the last stable block; for the
        automatic family (DeepSeek, OpenAI) nothing is added — a byte-stable
        prefix is the whole requirement, and the counters are what we read back.

        **``stream``** overrides ``ProviderConfig.stream`` for this one call and is how a
        caller says "a human is watching": with it ``True`` the SSE deltas are read as they
        arrive and handed to :attr:`on_delta` (see
        :meth:`_read_chat_stream`), which is what puts text on a terminal before the model
        has finished. ``None`` (the default) keeps the configured behaviour exactly.
        """
        strategy = self.cache_strategy()
        plan = (
            pc.explicit_breakpoints(messages, tools=tools, strategy=strategy)
            if strategy.explicit
            else None
        )
        prefix = pc.stable_prefix(
            plan.messages if plan is not None else messages,
            plan.tools if plan is not None else tools,
        )
        with self._cache_lock:
            reuse = pc.prefix_reuse(self._last_prefix, prefix)
            self._last_prefix = prefix
        body = chat_request_body(
            self._cfg,
            messages,
            tier=tier,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            cache_plan=plan,
            stream=stream,
        )
        url = self.endpoint()
        deadline = time.monotonic() + self._cfg.effective_total_timeout()
        started = time.monotonic()
        attempt = 0
        last: LLMError = LLMError("provider call failed")
        with self._client_scope() as client:
            while True:
                attempt += 1
                try:
                    result = self._send_chat(
                        client, url, body, timeout=self._attempt_timeout(deadline),
                        stream=stream,
                    )
                    result = self._reasoning_budget_guard(
                        client, url, body, result, stream=stream, deadline=deadline
                    )
                    return self._finish_cache(
                        result,
                        strategy=strategy,
                        reuse=reuse,
                        plan=plan,
                        prefix=prefix,
                        started=started,
                    )
                except ProviderResponseError:
                    raise  # a malformed answer is not in the retry policy (§43)
                except ProviderTimeoutError as exc:
                    last, status, timed_out = exc, None, True
                except LLMProviderError as exc:
                    last, status, timed_out = exc, exc.status, False
                decision = plan_retry(
                    status=status,
                    attempt=attempt,
                    max_attempts=self._cfg.max_retries + 1,
                    remaining_deadline=deadline - time.monotonic(),
                    timeout=timed_out,
                    backoff_base=self._cfg.backoff_s,
                )
                # §94 PROGRESS-GUARD: the commit-point gate (``progress_guard.RetryGate``,
                # installed by the agent loop) narrows the §43 policy while nothing visible has
                # been committed and refuses the retry past the commit point — re-sending would
                # stream the same answer to the user a second time. One helper for both provider
                # paths, so `chat` and `complete` can never disagree about the commit point.
                self._gate_retry(last, decision, attempt)
                time.sleep(decision.delay)

    # -- prompt cache ------------------------------------------------------ #

    def cache_strategy(self) -> pc.CacheStrategy:
        """What this endpoint needs for a prompt cache (resolved once per client)."""
        if self._cache_strategy is None:
            self._cache_strategy = pc.strategy_for(
                self._cfg.provider,
                base_url=self._cfg.base_url,
                model=self._cfg.model,
            )
        return self._cache_strategy

    @property
    def cache_ledger(self) -> pc.CacheLedger | None:
        """The ledger this client appends to (``None``: no filesystem access)."""
        return self._cache_ledger

    def _finish_cache(
        self,
        result: ChatResult,
        *,
        strategy: pc.CacheStrategy,
        reuse: pc.PrefixReuse,
        plan: pc.CachePlan | None,
        prefix: pc.StablePrefix,
        started: float,
    ) -> ChatResult:
        """Stamp one answer with the provider's cache verdict and record it.

        The verdict is recomputed *with* the strategy so the payload knows
        whether the provider documents caching at all (that is the difference
        between "the provider reported nothing" and "nothing was expected"), and
        the byte-compare prediction is kept in its own field. The ledger write is
        best effort: a failed append is counted on the client and never raised.
        """
        report = pc.cache_report(result.usage, strategy, predicted=reuse.predicted)
        payload = report.to_dict()
        payload["reuse"] = reuse.to_dict()
        payload["prefix"] = prefix.to_dict()
        payload["strategy"] = strategy.to_dict()
        if plan is not None:
            payload["breakpoints"] = [item.to_dict() for item in plan.breakpoints]
            payload["breakpoints_skipped"] = [dict(item) for item in plan.skipped]
        result.cache = payload
        if self._cache_ledger is not None:
            entry = pc.entry_from_report(
                report,
                model=self._cfg.model_for_tier("main"),
                call="chat",
                prefix_sha256=prefix.sha256,
                latency_ms=round((time.monotonic() - started) * 1000.0, 3),
            )
            if not self._cache_ledger.append(entry):
                self.cache_ledger_failures += 1
        return result

    def cache_status(self, *, limit: int = 0) -> dict[str, Any]:
        """The cache surface of this client: strategy, last report, ledger summary.

        This is what ``skaldir prompt cache status`` prints and what a telemetry
        field carries. Nothing here is inferred: the strategy is the documented
        one, ``last`` is the verdict of the most recent request, and the ledger
        numbers are the lines that were actually written.
        """
        strategy = self.cache_strategy()
        ledger = self._cache_ledger
        rows = ledger.entries(limit=limit) if ledger is not None else ([], 0)
        return {
            "provider": strategy.provider,
            "mode": strategy.mode,
            "strategy": strategy.to_dict(),
            "ledger": str(ledger.path) if ledger is not None else "",
            "ledger_enabled": ledger is not None,
            "ledger_failures": int(self.cache_ledger_failures),
            "entries": len(rows[0]),
            "bad_lines": int(rows[1]),
            "summary": pc.summarize(rows[0]),
            "last": rows[0][-1].to_dict() if rows[0] else None,
            "note": (
                "the strategy says what this endpoint documents; `last`/`summary` "
                "say what its usage payloads reported. Without a ledger the cache "
                "status is per-call only (ChatResult.cache)"
            ),
        }

    def describe_images(
        self,
        *,
        prompt: str,
        images: Sequence[Mapping[str, Any]],
        tier: str = "main",
        prompt_id: str = "",
    ) -> ChatResult:
        """Send one image request through the very same chat path.

        ``images`` are the content parts built by :func:`image_part_from_bytes`
        (local file → base64 data URL) / :func:`image_part_from_url` (remote
        URL). The transport, retry policy and timeout handling are
        :meth:`chat`'s, unchanged; the answer comes back as a :class:`ChatResult`
        so the caller can report the provider's own ``usage`` counters. A refusal
        that names image support is raised as :class:`ImageNotSupportedError`.
        """
        return vision_chat(
            self, prompt=prompt, parts=images, tier=tier, prompt_id=prompt_id
        )

    def _body(self, prompt: str, tier: str) -> dict[str, Any]:
        cfg = self._cfg
        body: dict[str, Any] = dict(cfg.extra_body)
        body["model"] = cfg.model_for_tier(tier)
        body["messages"] = [{"role": "user", "content": prompt}]
        if cfg.max_tokens_field:
            body[cfg.max_tokens_field] = int(cfg.max_tokens)
        if cfg.temperature is not None:
            body["temperature"] = float(cfg.temperature)
        if cfg.stream:
            body["stream"] = True
        return body

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "text/event-stream" if self._cfg.stream else "application/json"}
        if self._cfg.api_key:  # the one place the key is used — never logged
            # a key read back from a `.env` written before the paste-sanitizer existed
            # can still carry bracketed-paste markers (ESC[200~ … ESC[201~); strip them
            # so the endpoint ever only sees the bare credential.
            key = re.sub(
                r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]",
                "",
                str(self._cfg.api_key),
            )
            key = "".join(ch for ch in key if " " <= ch <= "~").strip()
            headers["Authorization"] = f"Bearer {key}"
            # Xiaomi MiMo documents BOTH spellings for one endpoint (OpenAI SDK Bearer +
            # curl `api-key:`); send both so neither documented client shape is wrong.
            if "xiaomimimo" in str(self._cfg.base_url or ""):
                headers["api-key"] = key
        for name, value in self._cfg.headers.items():
            headers[str(name)] = str(value)
        return headers

    def _attempt_timeout(self, deadline: float) -> float:
        remaining = deadline - time.monotonic()
        return max(0.05, min(float(self._cfg.timeout_s), remaining))

    @contextmanager
    def _client_scope(self) -> Iterator[Any]:
        """The client this provider talks through — **pooled and reused**.

        An injected client is used as-is and never closed by us. Without one we
        take the *process-wide* pooled client from :mod:`skaldir.perf_runtime`
        instead of opening one per call: a multi-step ``agent run`` then keeps a
        single TCP connection (and a single TLS session) for the whole run
        instead of paying a connect per step. Measured on a loopback stub: 1 TCP
        connection for a 2-step run, versus 2 before (see
        ``MVP/47-performance-codex.md``). ``SKALDIR_PERF_NOCACHE=1`` restores the
        open-and-close-per-call behaviour.
        """
        if self._injected_client is not None:
            yield self._injected_client
            return
        from .perf_runtime import CACHE_ENABLED, http_client

        client = http_client(follow_redirects=False)
        self._owned_client = client
        try:
            yield client
        finally:
            if not CACHE_ENABLED:
                # ``SKALDIR_PERF_NOCACHE=1``: the exact pre-fix lifecycle — one
                # client per call, closed here — so the harness's "before" column
                # measures the old code, not a leak.
                client.close()

    def close(self) -> None:
        """Drop this provider's pooled client (the process pool outlives it).

        Safe to call twice; a no-op when an injected client was used.
        """
        self._owned_client = None

    # -- transport --------------------------------------------------------- #

    def _send(self, client: Any, url: str, body: dict[str, Any], *, timeout: float) -> str:
        headers = self._headers()
        self._request_started = time.monotonic()
        self._first_byte_at = None
        try:
            if self._cfg.stream:
                with client.stream(
                    "POST", url, json=body, headers=headers, timeout=timeout
                ) as response:
                    if int(response.status_code) >= 400:
                        response.read()
                        raise self._http_error(response)
                    return self._read_stream(response)
            response = client.post(url, json=body, headers=headers, timeout=timeout)
        except (httpx.InvalidURL, httpx.UnsupportedProtocol) as exc:
            raise ProviderConfigError(f"invalid provider base_url {url!r}") from exc
        except httpx.TransportError as exc:
            # connect/read/protocol failures behave like a timeout for retry purposes
            if isinstance(exc, httpx.TimeoutException):
                reason = f"timeout after {timeout:.2f}s"
            else:
                reason = f"{type(exc).__name__}"
            raise ProviderTimeoutError(f"{reason} calling {url}") from exc
        if int(response.status_code) >= 400:
            raise self._http_error(response)
        return self._read_json(response)

    def _send_chat(
        self, client: Any, url: str, body: dict[str, Any], *, timeout: float,
        stream: bool | None = None,
    ) -> ChatResult:
        """``chat``'s transport.

        Deliberately the same shape as :meth:`_send` (same error classes, same
        stream handling) rather than a refactor of it: ``complete()`` is a
        shipped path other layers call, and a shared helper would have to change
        it. Both read *the same* response — only the extraction differs.

        ``stream`` overrides the configured value for this one call: the CLI asks for a
        streamed answer when a human is watching (a terminal) and for a whole answer when a
        machine is consuming (``--json``, a pipe), without mutating a shared config.
        """
        headers = self._headers()
        self._request_started = time.monotonic()
        self._first_byte_at = None
        self.stream_stats = _empty_stream_stats()
        streaming = bool(self._cfg.stream) if stream is None else bool(stream)
        self.stream_stats["streaming_requested"] = streaming
        try:
            if streaming:
                with client.stream(
                    "POST", url, json=body, headers=headers, timeout=timeout
                ) as response:
                    if int(response.status_code) >= 400:
                        response.read()
                        raise self._http_error(response)
                    return self._stamp_first_byte(self._read_chat_stream(response))
            response = client.post(url, json=body, headers=headers, timeout=timeout)
        except (httpx.InvalidURL, httpx.UnsupportedProtocol) as exc:
            raise ProviderConfigError(f"invalid provider base_url {url!r}") from exc
        except httpx.TransportError as exc:
            if isinstance(exc, httpx.TimeoutException):
                reason = f"timeout after {timeout:.2f}s"
            else:
                reason = f"{type(exc).__name__}"
            raise ProviderTimeoutError(f"{reason} calling {url}") from exc
        if int(response.status_code) >= 400:
            raise self._http_error(response)
        return self._stamp_first_byte(self._read_chat_json(response))

    def _stamp_first_byte(self, result: ChatResult) -> ChatResult:
        """Attach the measured first-byte latency to the answer (never invents one)."""
        started, first = self._request_started, self._first_byte_at
        if started is not None and first is not None:
            result.first_byte_ms = round((first - started) * 1000.0, 3)
        return result

    def first_byte_ms(self) -> float | None:
        """Last measured time-to-first-content-token in ms (``None`` if not streamed)."""
        if self._request_started is None or self._first_byte_at is None:
            return None
        return round((self._first_byte_at - self._request_started) * 1000.0, 3)

    def _http_error(self, response: Any) -> LLMProviderError:
        status = int(response.status_code)
        detail = self._error_detail(response)
        if status in (401, 403):
            # Name the plan's own variable when it has one (MIMO_API_KEY, DEEPSEEK_API_KEY, …)
            # instead of always SKALDIR_LLM_API_KEY — the fallback is only the default.
            named = str(getattr(self._cfg, "api_key_env", "") or "").strip() or ENV_API_KEY
            return ProviderAuthError(
                f"provider refused the credential (HTTP {status}{detail}) — set "
                f"{named} in the environment (the key is never read from "
                f"{self._cfg.config_file or 'config.yaml'})",
                status=status,
            )
        if status == 429:
            # Plan/subscription quota is not a rate-limit blip: OpenCode Go answers
            # 429 GoUsageLimitError ("Weekly usage limit reached") when the plan is
            # exhausted. Say so — a 429 on a plan SKU means "wait for the reset".
            body = str(getattr(response, "text", "") or "").lower()
            plan_quota = any(
                needle in body
                for needle in (
                    "usagelimit",
                    "usage limit",
                    "quota",
                    "resets in",
                    "insufficient balance",
                    "exceeded your current quota",
                )
            )
            if plan_quota:
                return ProviderRateLimitError(
                    f"provider plan/quota exhausted (HTTP 429{detail}) — this is a "
                    "subscription or balance limit, not a wrong key; wait for the "
                    "reset or top up the plan",
                    status=status,
                )
            return ProviderRateLimitError(
                f"provider rate limited the call (HTTP 429{detail})", status=status
            )
        return LLMProviderError(f"provider call failed (HTTP {status}{detail})", status=status)

    @staticmethod
    def _error_detail(response: Any) -> str:
        """Short, sanitised error text (response body only — never our headers)."""
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 - a non-JSON error body is expected sometimes
            text = _compact(getattr(response, "text", "") or "")
            return f", body: {text}" if text else ""
        if isinstance(payload, Mapping):
            error = payload.get("error")
            if isinstance(error, Mapping):
                message = error.get("message") or error.get("code")
                if message:
                    return f", message: {_compact(message)}"
            if isinstance(error, str):
                return f", message: {_compact(error)}"
        return ""

    # -- response reading -------------------------------------------------- #

    def _read_json(self, response: Any) -> str:
        text = getattr(response, "text", "") or ""
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise ProviderResponseError(
                f"provider answered HTTP {response.status_code} with a body that is not "
                f"JSON: {_compact(text, 120)!r}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise ProviderResponseError("provider answer is not a JSON object")
        content = content_from_completion(payload)
        if not content.strip():
            # MVP/87: an empty `content` is almost never "the endpoint said nothing" — a
            # reasoning-capable backend puts its whole answer in `reasoning_content` and
            # returns an empty `content` when the budget runs out there. The refusal names
            # that, because "empty completion" alone sent one investigation hunting for a
            # missing Authorization header (the header was present; the response was 200).
            # ``_EmptyCompletion`` (not the bare class) so the call path can read the payload
            # and apply the reasoning-budget guard before it refuses.
            raise _EmptyCompletion(self._empty_completion_reason(payload, response), payload)
        return content

    @staticmethod
    def _empty_completion_reason(payload: Mapping[str, Any], response: Any) -> str:
        """The measured shape behind an empty ``content`` — never a guess."""
        choices = payload.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else {}
        first = first if isinstance(first, Mapping) else {}
        message = first.get("message")
        message = message if isinstance(message, Mapping) else {}
        usage = payload.get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        details = usage.get("completion_tokens_details")
        details = details if isinstance(details, Mapping) else {}
        parts = [
            "provider returned an empty completion "
            f"(HTTP {getattr(response, 'status_code', 0)}"
        ]
        finish = str(first.get("finish_reason") or "")
        if finish:
            parts.append(f"finish_reason={finish}")
        reasoning = str(message.get("reasoning_content") or "")
        if reasoning:
            parts.append(f"reasoning_chars={len(reasoning)}")
        if details.get("reasoning_tokens"):
            parts.append(f"reasoning_tokens={details['reasoning_tokens']}")
        if usage.get("completion_tokens"):
            parts.append(f"completion_tokens={usage['completion_tokens']}")
        head = _compact(reasoning, 80)
        return (
            ", ".join(parts)
            + ")"
            + (f" — the answer is in reasoning_content: {head}" if head else "")
            + " — raise max_tokens for this provider block when finish_reason=length"
        )

    # -- the reasoning-budget guard ---------------------------------------- #

    def _budget_field(self) -> str:
        """The body field that carries the token budget for this endpoint."""
        return str(self._cfg.max_tokens_field or DEFAULT_MAX_TOKENS_FIELD)

    def _budget_of(self, body: Mapping[str, Any]) -> int:
        """The budget this request is really asking for (the body is the truth, not the config)."""
        try:
            return int(body.get(self._budget_field()) or self._cfg.max_tokens)
        except (TypeError, ValueError):  # pragma: no cover — a body we built ourselves
            return int(self._cfg.max_tokens)

    def _bigger_budget(self, budget: int) -> int:
        """The one bounded escalation: doubled, never past the ceiling. ``0`` = do not retry."""
        ceiling = max(int(REASONING_BUDGET_CEILING), int(budget))
        bigger = min(int(budget) * int(REASONING_BUDGET_FACTOR), ceiling)
        return bigger if bigger > int(budget) else 0

    def _budget_refusal(self, attempts: list[dict[str, Any]], tried: int) -> str:
        """The **named** refusal: what happened, both attempts, and the one thing to change."""
        first = attempts[0]
        parts = [
            "reasoning budget exhausted: the endpoint answered HTTP 200 with "
            f"finish_reason=length and no content (budget {first['budget']}, "
            f"reasoning_tokens {first['reasoning_tokens']}, content_chars 0)"
        ]
        if tried:
            last = attempts[-1]
            parts.append(
                f"a retry with budget {tried} returned the same empty completion "
                f"(reasoning_tokens {last['reasoning_tokens']})"
            )
        else:
            parts.append(
                f"the budget is already at the declared ceiling ({REASONING_BUDGET_CEILING}) — "
                "no larger retry is attempted"
            )
        parts.append(
            f"raise `{self._budget_field()}` for this provider block, or ask the model for a "
            "shorter answer; `skaldir provider show` prints the current block"
        )
        return "; ".join(parts)

    def _reasoning_budget_guard(
        self,
        client: Any,
        url: str,
        body: Mapping[str, Any],
        result: ChatResult,
        *,
        stream: bool | None,
        deadline: float,
    ) -> ChatResult:
        """The measured reasoning-budget trap, in one place (see :class:`ReasoningBudgetError`).

        One answer whose ``finish_reason`` is ``length`` **and** whose ``content`` is empty is a
        *budget artifact* — the reasoner spent the whole allowance thinking. It is retried
        **once** with a bigger budget (:data:`REASONING_BUDGET_FACTOR`, capped at
        :data:`REASONING_BUDGET_CEILING`); both attempts are recorded on the answer
        (:attr:`ChatResult.budget`), and if the retry is empty too the call refuses **by name**
        instead of handing an empty content to a caller as an answer.

        Every other answer — including an empty one with ``finish_reason=stop`` — is returned
        untouched with the single attempt recorded, because widening the budget is the only
        thing this guard does and it only does it for the shape that was measured.
        """
        budget = self._budget_of(body)
        attempts = [
            budget_evidence(budget=budget, finish_reason=result.finish_reason,
                            content_chars=len(str(result.text or "")), usage=result.usage)
        ]
        if not reasoning_budget_artifact(result):
            result.budget = {"attempts": attempts, "retried": False}
            return result
        bigger = self._bigger_budget(budget)
        if not bigger:
            raise ReasoningBudgetError(self._budget_refusal(attempts, tried=0))
        retry_body = dict(body)
        retry_body[self._budget_field()] = bigger
        second = self._send_chat(
            client, url, retry_body, timeout=self._attempt_timeout(deadline), stream=stream
        )
        attempts.append(
            budget_evidence(budget=bigger, finish_reason=second.finish_reason,
                            content_chars=len(str(second.text or "")), usage=second.usage)
        )
        if reasoning_budget_artifact(second):
            # also recorded on the client, because the refusal itself carries no answer object
            self.last_budget = {"attempts": attempts, "retried": True, "budget": bigger}
            raise ReasoningBudgetError(self._budget_refusal(attempts, tried=bigger))
        second.budget = {"attempts": attempts, "retried": True}
        return second

    def _budget_retry_text(
        self, client: Any, url: str, body: Mapping[str, Any], exc: _EmptyCompletion, *,
        deadline: float,
    ) -> str | None:
        """The guard on the text path (:meth:`complete`): ``None`` = not the budget artifact.

        The text path reads a ``str``, so the evidence goes to :attr:`last_budget` — the same
        four facts per attempt, and both attempts, so "one call after a retry" is legible in
        the log line that reports it. A refusal is a :class:`ReasoningBudgetError`, never the
        empty answer.
        """
        if not reasoning_budget_payload_artifact(exc.payload):
            return None
        budget = self._budget_of(body)
        attempts = [
            budget_evidence(budget=budget, finish_reason="length", content_chars=0,
                            usage=exc.payload.get("usage")
                            if isinstance(exc.payload.get("usage"), Mapping) else {})
        ]
        bigger = self._bigger_budget(budget)
        if not bigger:
            self.last_budget = {"attempts": attempts, "retried": False, "budget": budget}
            raise ReasoningBudgetError(self._budget_refusal(attempts, tried=0)) from exc
        retry_body = dict(body)
        retry_body[self._budget_field()] = bigger
        raw = ""
        try:
            raw = self._send(client, url, retry_body, timeout=self._attempt_timeout(deadline))
        except _EmptyCompletion as second:
            attempts.append(
                budget_evidence(budget=bigger, finish_reason="length", content_chars=0,
                                usage=second.payload.get("usage")
                                if isinstance(second.payload.get("usage"), Mapping) else {})
            )
            self.last_budget = {"attempts": attempts, "retried": True, "budget": bigger}
            raise ReasoningBudgetError(self._budget_refusal(attempts, tried=bigger)) from second
        attempts.append(
            budget_evidence(budget=bigger, finish_reason="", content_chars=len(raw or ""))
        )
        self.last_budget = {"attempts": attempts, "retried": True, "budget": bigger}
        return raw

    def _read_stream(self, response: Any) -> str:
        parts: list[str] = []
        done = False
        for line in response.iter_lines():
            line = (line or "").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                # Do NOT break: the terminating chunk is still unread, and httpx
                # throws the connection away when a body is abandoned instead of
                # returned to the pool. Draining it is what makes connection
                # reuse possible at all (see MVP/47-performance-codex.md).
                done = True
                continue
            if done:
                continue
            try:
                chunk = json.loads(payload)
            except ValueError as exc:
                raise ProviderResponseError(
                    f"streamed chunk is not valid JSON: {_compact(payload, 120)!r}"
                ) from exc
            if isinstance(chunk, Mapping):
                if self._first_byte_at is None and delta_from_chunk(chunk):
                    self._first_byte_at = time.monotonic()
                parts.append(delta_from_chunk(chunk))
        return "".join(parts)

    def _extract(self, text: str) -> str:
        if not text or not text.strip():
            raise ProviderResponseError("provider returned an empty completion")
        return text

    def _read_chat_json(self, response: Any) -> ChatResult:
        """Non-streamed tool-calling answer (an empty completion with no call is refused)."""
        text = getattr(response, "text", "") or ""
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise ProviderResponseError(
                f"provider answered HTTP {response.status_code} with a body that is not "
                f"JSON: {_compact(text, 120)!r}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise ProviderResponseError("provider answer is not a JSON object")
        # An answer with no tool call and no text is *not* an error here: the loop
        # reports it as `incomplete/empty_answer` and the transcript shows the raw
        # answer. Raising would turn a model quirk into a provider failure.
        return chat_result_from_payload(payload)

    def _read_chat_stream(self, response: Any) -> ChatResult:
        """Streamed answer: content **and** fragmented tool-call deltas, folded per index.

        Each content delta is handed to :attr:`on_delta` **as it arrives** (before the
        answer is folded), which is the whole point of the streamed path: the caller can
        put the text on screen while the model is still generating. A sink that raises is
        counted in :attr:`stream_stats` and never breaks the call.

        An endpoint that *ignores* ``stream`` and answers with one whole JSON object (a
        gateway in front of a non-streaming model does this) is read as that answer, not as
        nothing: folding zero deltas would report "the model answered with neither text nor
        a tool call" about an answer that is right there in the response.
        """
        chunks: list[Mapping[str, Any]] = []
        body_lines: list[str] = []
        done = False
        for line in response.iter_lines():
            line = (line or "").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                body_lines.append(line)
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                # drain, never break: an abandoned body makes httpx close the
                # connection instead of returning it to the pool
                done = True
                continue
            if done:
                continue
            try:
                chunk = json.loads(payload)
            except ValueError as exc:
                raise ProviderResponseError(
                    f"streamed chunk is not valid JSON: {_compact(payload, 120)!r}"
                ) from exc
            if isinstance(chunk, Mapping):
                delta = delta_from_chunk(chunk)
                if delta:
                    # the first *content* delta — a role-only chunk is not a
                    # visible token and must not be counted as one
                    if self._first_byte_at is None:
                        self._first_byte_at = time.monotonic()
                    self._note_delta(delta)
                chunks.append(chunk)
        if not chunks and body_lines:
            whole = self._answer_body(body_lines)
            if whole is not None:
                self.stream_stats["stream_ignored"] = True
                return chat_result_from_payload(whole)
        return accumulate_stream_chunks(chunks)

    def _answer_body(self, body_lines: Sequence[str]) -> Mapping[str, Any] | None:
        """A non-SSE body that is a complete answer, or ``None`` when it is nothing of the sort.

        Deliberately narrow: only a JSON object carrying a ``choices`` array is accepted, so
        a gateway's HTML error page or a truncated body still raises rather than pretending
        an answer arrived.
        """
        try:
            payload = json.loads("".join(body_lines))
        except ValueError:
            return None
        if isinstance(payload, Mapping) and isinstance(payload.get("choices"), list):
            return payload
        return None

    def _note_delta(self, text: str) -> None:
        """Hand one content delta to the sink now, and count what really happened.

        Measured: ``deltas`` (one per content chunk), ``chars`` (their length) and
        ``first_content_ms``/``last_content_ms`` relative to the request being sent. The
        sink is called *first* — a slow counter must never delay the user's text.
        """
        stats = self.stream_stats
        sink = self.on_delta
        if sink is not None:
            try:
                sink(text)
            except Exception:  # noqa: BLE001 — a renderer must never break a run
                stats["sink_errors"] = int(stats.get("sink_errors") or 0) + 1
        stats["deltas"] = int(stats.get("deltas") or 0) + 1
        stats["chars"] = int(stats.get("chars") or 0) + len(text)
        if self._request_started is not None:
            stamp = round((time.monotonic() - self._request_started) * 1000.0, 3)
            if stats.get("first_content_ms") is None:
                stats["first_content_ms"] = stamp
            stats["last_content_ms"] = stamp

    @staticmethod
    def _with_attempts(exc: LLMError, retries: int) -> LLMError:
        """Same error type, with the retry count visible (§43 attempt accounting)."""
        if isinstance(exc, LLMProviderError):
            return type(exc)(str(exc), status=exc.status, sdk_retries=max(0, retries))
        return exc


def content_text(content: Any) -> str:
    """``"text"`` or ``[{"type": "text", "text": ...}]`` → plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, Mapping):
                value = part.get("text")
                if isinstance(value, str):
                    chunks.append(value)
        return "".join(chunks)
    return ""


def content_from_completion(payload: Mapping[str, Any]) -> str:
    """Read ``choices[0].message.content`` out of an OpenAI-style answer."""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderResponseError("provider answer has no 'choices' array")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise ProviderResponseError("provider answer has a malformed 'choices[0]'")
    message = first.get("message")
    if isinstance(message, Mapping):
        return content_text(message.get("content"))
    if isinstance(first.get("text"), str):  # legacy completion shape
        return str(first["text"])
    raise ProviderResponseError("provider answer has no 'choices[0].message.content'")


def delta_from_chunk(chunk: Mapping[str, Any]) -> str:
    """Read one streamed ``delta.content`` (missing deltas are simply empty)."""
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, Mapping):
        return ""
    delta = first.get("delta")
    if isinstance(delta, Mapping):
        return content_text(delta.get("content"))
    return content_text(first.get("text"))


# --------------------------------------------------------------------------- #
# tool calling — the wire shape of ``tools=`` / ``tool_calls`` (AGENT wave)   #
# --------------------------------------------------------------------------- #
#
# ``complete()`` answers with text only. A model can only *ask* for a tool when
# the request carries a ``tools`` array and the answer is read from
# ``choices[0].message.tool_calls`` — this section is that half of the protocol:
#
# * :func:`chat_request_body` — the pure request builder (testable without HTTP);
# * :func:`tool_calls_from_message` — the non-streamed answer;
# * :func:`accumulate_stream_chunks` — the streamed deltas (arguments arrive in
#   fragments and are concatenated per ``index``);
# * :meth:`OpenAICompatibleLLM.chat` — the call, with the same retry/timeout
#   policy as ``complete()`` (one policy, :mod:`skaldir.system_one_policy`).
#
# Nothing here re-implements retry logic and nothing here is a second HTTP layer.


@dataclass(frozen=True)
class ToolCall:
    """One function call the model asked for (id + name + raw JSON arguments).

    ``parse_error`` is non-empty when the model's ``arguments`` string is not a
    JSON object — the caller must feed that back to the model instead of
    executing a guessed call.
    """

    id: str
    name: str
    arguments_raw: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    parse_error: str = ""
    index: int = 0

    def to_wire(self) -> dict[str, Any]:
        """The exact shape that goes back into the assistant message."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments_raw
                if self.arguments_raw
                else json.dumps(self.arguments, ensure_ascii=False),
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": dict(self.arguments),
            "arguments_raw": self.arguments_raw,
            "parse_error": self.parse_error,
            "index": self.index,
        }


@dataclass
class ChatResult:
    """One model answer: text, the tool calls it asked for, finish reason, usage."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    message: dict[str, Any] = field(default_factory=dict)
    #: Time from sending the request to the first *content* delta arriving, in
    #: milliseconds — only set for a streamed answer (``stream: true``), and
    #: ``None`` when nothing was measured. This is the number a streaming answer
    #: is judged on: the user sees text at this point, not at completion.
    first_byte_ms: float | None = None
    #: The provider's own prompt-cache verdict for this request — ``hit``,
    #: ``miss`` or ``unknown`` — plus the counters it was read from
    #: (:mod:`skaldir.prompt_cache`). Filled from the usage payload only: no
    #: counter means ``unknown``, and a stable prefix we sent is never
    #: reported as a hit.
    cache: dict[str, Any] = field(default_factory=dict)
    #: The reasoning-budget guard's evidence, when it had to act: ``{"attempts": [{budget,
    #: finish_reason, content_chars, reasoning_tokens}, …], "retried": bool}``. Empty for a
    #: normal answer, so a caller can tell "one call" from "one call after a budget retry"
    #: without counting its own requests. Both attempts are recorded, never just the last.
    budget: dict[str, Any] = field(default_factory=dict)

    @property
    def asked_for_tools(self) -> bool:
        return bool(self.tool_calls)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "text": self.text,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "finish_reason": self.finish_reason,
            "usage": dict(self.usage),
        }
        if self.cache:
            out["cache"] = dict(self.cache)
        if self.budget:
            out["budget"] = dict(self.budget)
        return out


def parse_tool_arguments(raw: Any) -> tuple[dict[str, Any], str]:
    """``"{\\"a\\": 1}"`` → ``({"a": 1}, "")``; a non-object is an error, never guessed.

    An empty string means "no arguments" (a valid, common case for a zero-arg
    tool); anything that is not a JSON object is reported as ``parse_error``.
    """
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}, ""
    text = "" if raw is None else str(raw)
    if not text.strip():
        return {}, ""
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        return {}, f"arguments are not valid JSON: {_compact(exc, 120)}"
    if not isinstance(parsed, Mapping):
        return {}, f"arguments are not a JSON object: {_compact(text, 120)}"
    return {str(key): value for key, value in parsed.items()}, ""


def tool_calls_from_message(message: Mapping[str, Any]) -> list[ToolCall]:
    """Read ``tool_calls`` (and the legacy single ``function_call``) off a message."""
    calls: list[ToolCall] = []
    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list):
        for position, entry in enumerate(raw_calls):
            if not isinstance(entry, Mapping):
                continue
            function = entry.get("function")
            function = function if isinstance(function, Mapping) else {}
            name = str(function.get("name") or entry.get("name") or "").strip()
            raw_args = function.get("arguments", entry.get("arguments"))
            arguments, parse_error = parse_tool_arguments(raw_args)
            try:
                index = int(entry.get("index", position))
            except (TypeError, ValueError):
                index = position
            calls.append(
                ToolCall(
                    id=str(entry.get("id") or f"call_{position}"),
                    name=name,
                    arguments_raw="" if raw_args is None else str(raw_args),
                    arguments=arguments,
                    parse_error=parse_error,
                    index=index,
                )
            )
    legacy = message.get("function_call")
    if not calls and isinstance(legacy, Mapping):
        name = str(legacy.get("name") or "").strip()
        arguments, parse_error = parse_tool_arguments(legacy.get("arguments"))
        calls.append(
            ToolCall(
                id="call_0",
                name=name,
                arguments_raw=str(legacy.get("arguments") or ""),
                arguments=arguments,
                parse_error=parse_error,
                index=0,
            )
        )
    return [call for call in calls if call.name]


def _reasoning_tokens(usage: Mapping[str, Any]) -> int | None:
    """The provider's reasoning-token counter, whichever of its spellings it uses.

    ``reasoning_tokens`` (DeepSeek/MiniMax), ``completion_tokens_details.reasoning_tokens``
    and ``output_tokens_details.reasoning_tokens`` (OpenAI-compatible) are all read;
    ``None`` means the provider reported none at all — never ``0``, which would read
    as \"this backend reasons for free\".
    """
    for key in ("reasoning_tokens", "reasoning_output_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    for container in ("completion_tokens_details", "output_tokens_details"):
        details = usage.get(container)
        if isinstance(details, Mapping):
            value = details.get("reasoning_tokens")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(value)
    return None


def usage_from_payload(payload: Mapping[str, Any]) -> dict[str, int]:
    """``usage`` reduced to integer counters (missing → empty).

    The three §43 counters, plus — when the provider reports them — the
    **reasoning** tokens. Reasoning tokens are billed like completion tokens but
    produce no content, so a run that spends its budget on them looks, in every
    content-shaped view, like a run that did nothing (§94: the 3 703-of-4 096 case).
    Reporting them separately is the only way that becomes visible; a provider that
    reports none adds no key, so absence stays distinct from zero.
    """
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return {}
    out: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = int(value)
    reasoning = _reasoning_tokens(usage)
    if reasoning is not None:
        out["reasoning_tokens"] = reasoning
    if "total_tokens" not in out and out:
        out["total_tokens"] = int(out.get("prompt_tokens", 0)) + int(
            out.get("completion_tokens", 0)
        )
    return out


def reasoning_budget_artifact(result: ChatResult) -> bool:
    """Is this answer the **budget artifact**: the reasoner hit the ceiling before it answered?

    The measured shape (2026-09-19, twice): ``finish_reason=length`` with an empty ``content``
    while the reasoning side is filled. Deliberately narrow — a ``length`` finish *with* text is
    a truncated answer the caller reports as such, and an answer that asked for a tool is
    complete enough to act on. Only this shape is retried with a bigger budget.
    """
    if str(result.finish_reason or "") != "length":
        return False
    if str(result.text or "").strip():
        return False
    return not result.tool_calls


def reasoning_budget_payload_artifact(payload: Mapping[str, Any]) -> bool:
    """The same shape, read from a raw response body (the text path reads no ChatResult)."""
    choices = payload.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else {}
    first = first if isinstance(first, Mapping) else {}
    if str(first.get("finish_reason") or "") != "length":
        return False
    message = first.get("message")
    message = message if isinstance(message, Mapping) else {}
    return not str(message.get("content") or "").strip()


def budget_evidence(*, budget: int, finish_reason: str, content_chars: int,
                    usage: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One attempt, in the four facts that make the trap legible.

    ``reasoning_tokens`` is reported **separately** (0 when the provider sent no counter — the
    absence is not invented into a measurement) exactly like :func:`usage_from_payload` does:
    a run that spends its whole budget thinking produces no content at all, and the only way
    that is visible afterwards is this number next to a zero ``content_chars``.
    """
    return {
        "budget": int(budget),
        "finish_reason": str(finish_reason or ""),
        "content_chars": int(content_chars),
        "reasoning_tokens": int(_reasoning_tokens(dict(usage or {})) or 0),
    }


def _attach_cache(result: ChatResult, payload: Mapping[str, Any]) -> ChatResult:
    """Fold the provider's own cache counters into an answer — never inventing one.

    The counters keep the provider's names (``prompt_cache_hit_tokens``,
    ``cache_read_input_tokens``, ``prompt_tokens_details.cached_tokens``) inside
    ``usage``, so the step telemetry a run already writes carries them without a
    second accounting path, and the one-word verdict lives in
    :attr:`ChatResult.cache`. A payload with no cache counter gets ``unknown``:
    a stable prefix we sent is never reported as a hit.
    """
    report = pc.cache_report(payload)
    result.cache = report.to_dict()
    if result.usage:  # only enrich real usage — never manufacture a usage block
        for name, value in report.fields.items():
            result.usage.setdefault(name, int(value))
    return result


def chat_result_from_payload(payload: Mapping[str, Any]) -> ChatResult:
    """One answer body → :class:`ChatResult` (no ``choices`` is a malformed answer)."""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderResponseError("provider answer has no 'choices' array")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise ProviderResponseError("provider answer has a malformed 'choices[0]'")
    message = first.get("message")
    message = dict(message) if isinstance(message, Mapping) else {}
    if not message and isinstance(first.get("text"), str):  # legacy completion shape
        message = {"role": "assistant", "content": first["text"]}
    return _attach_cache(
        ChatResult(
            text=content_text(message.get("content")),
            tool_calls=tool_calls_from_message(message),
            finish_reason=str(first.get("finish_reason") or ""),
            usage=usage_from_payload(payload),
            message=message,
        ),
        payload,
    )


def accumulate_stream_chunks(chunks: Iterable[Mapping[str, Any]]) -> ChatResult:
    """Fold streamed deltas into one :class:`ChatResult`.

    Tool-call deltas arrive **fragmented**: the first chunk carries
    ``delta.tool_calls[0].function.name``, later chunks append pieces of the
    ``arguments`` string. Fragments are concatenated per ``index`` (the
    position in the model's answer), which is the only identifier that stays
    stable across chunks — ``id`` may be empty in every delta.
    """
    parts: list[str] = []
    order: list[int] = []
    names: dict[int, str] = {}
    raw_args: dict[int, list[str]] = {}
    ids: dict[int, str] = {}
    finish = ""
    usage: dict[str, int] = {}
    #: the raw usage object of the last chunk that carried one — kept beside the
    #: reduced counter dict because the cache fields live in the raw object only
    raw_usage: Mapping[str, Any] = {}
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            continue
        if chunk.get("usage"):
            usage = usage_from_payload(chunk) or usage
            raw_usage = chunk
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        first = choices[0]
        if not isinstance(first, Mapping):
            continue
        if first.get("finish_reason"):
            finish = str(first["finish_reason"])
        delta = first.get("delta")
        if not isinstance(delta, Mapping):
            parts.append(content_text(first.get("text")))
            continue
        parts.append(content_text(delta.get("content")))
        raw_calls = delta.get("tool_calls")
        if not isinstance(raw_calls, list):
            continue
        for position, entry in enumerate(raw_calls):
            if not isinstance(entry, Mapping):
                continue
            try:
                index = int(entry.get("index", position))
            except (TypeError, ValueError):
                index = position
            if index not in names:
                order.append(index)
                names[index] = ""
                raw_args[index] = []
                ids[index] = ""
            if entry.get("id"):
                ids[index] = str(entry["id"])
            function = entry.get("function")
            if isinstance(function, Mapping):
                if function.get("name"):
                    names[index] = str(function["name"])
                if function.get("arguments") is not None:
                    raw_args[index].append(str(function["arguments"]))
    calls: list[ToolCall] = []
    for index in order:
        text = "".join(raw_args[index])
        arguments, parse_error = parse_tool_arguments(text)
        calls.append(
            ToolCall(
                id=ids[index] or f"call_{index}",
                name=names[index].strip(),
                arguments_raw=text,
                arguments=arguments,
                parse_error=parse_error,
                index=index,
            )
        )
    return _attach_cache(
        ChatResult(
            text="".join(parts),
            tool_calls=[call for call in calls if call.name],
            finish_reason=finish,
            usage=usage,
            message={"role": "assistant", "content": "".join(parts)},
        ),
        raw_usage,
    )


def chat_request_body(
    cfg: ProviderConfig,
    messages: Sequence[Mapping[str, Any]],
    *,
    tier: str,
    tools: Sequence[Mapping[str, Any]] | None = None,
    tool_choice: Any = None,
    response_format: Mapping[str, Any] | None = None,
    cache_plan: pc.CachePlan | None = None,
    stream: bool | None = None,
) -> dict[str, Any]:
    """The exact body for one tool-calling request (pure — no client, no I/O).

    ``tools``/``tool_choice`` are omitted when no tool is offered: an empty
    ``tools: []`` is rejected by several OpenAI-compatible servers, and
    ``tool_choice`` without ``tools`` is invalid.

    ``response_format`` is added verbatim when given (the caller built it with
    :func:`json_schema_response_format` / :func:`json_object_response_format`);
    nothing about the answer is guessed or repaired here.

    ``cache_plan`` (from :func:`skaldir.prompt_cache.explicit_breakpoints`) is the
    **only** way ``cache_control`` enters this body: when one is handed in, its
    annotated messages and tools are the ones sent. Without a plan the body is
    byte-identical to what this function always produced — the automatic cache
    family needs nothing here.

    ``stream`` overrides ``cfg.stream`` for this one request (``None`` = use the config):
    the same client serves a human on a terminal (streamed, token by token) and a machine
    on a pipe (one whole answer), and neither should have to mutate a shared config to say
    which one it is.
    """
    if cache_plan is not None:
        messages = cache_plan.messages
        tools = cache_plan.tools or None
    body: dict[str, Any] = dict(cfg.extra_body)
    body["model"] = cfg.model_for_tier(tier)
    body["messages"] = [dict(message) for message in messages]
    if cfg.max_tokens_field:
        body[cfg.max_tokens_field] = int(cfg.max_tokens)
    if cfg.temperature is not None:
        body["temperature"] = float(cfg.temperature)
    streaming = bool(cfg.stream) if stream is None else bool(stream)
    if streaming:
        body["stream"] = True
    if tools:
        body["tools"] = [dict(tool) for tool in tools]
        body["tool_choice"] = tool_choice if tool_choice is not None else "auto"
    if response_format:
        body["response_format"] = dict(response_format)
    return body


# --------------------------------------------------------------------------- #
# factory                                                                     #
# --------------------------------------------------------------------------- #


def provider_for(
    config: Any = None,
    *,
    scripted: LLMClient | None = None,
    env: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
    cache_ledger: Any = None,
) -> LLMClient:
    """Build the client for a run. Precedence: explicit > env > file > scripted.

    * an object that already implements ``complete()`` (``ScriptedLLM``,
      ``CallableLLM``, the plugin's ``ContextLLM``) is returned unchanged;
    * ``config["provider"] == "scripted"`` (or ``$SKALDIR_LLM_PROVIDER=scripted``)
      builds a :class:`ScriptedLLM` from the ``responses`` mapping;
    * a complete HTTP configuration (base_url + model) yields
      :class:`OpenAICompatibleLLM`;
    * otherwise the ``scripted`` argument is the fallback, and if that is absent
      a :class:`ProviderConfigError` states exactly what to export.

    ``cache_ledger`` is handed to the client unchanged (``None`` = the
    ``SKALDIR_PROMPT_CACHE_LEDGER`` rule, and no ledger at all when that is unset):
    a run that wants to report cache hits per request has to say where to write them.

    Raises :class:`ProviderConfigError` for ``provider: none``, for a secret
    stored in the config file, and for invalid settings — a half-configured
    provider must fail loudly instead of silently degrading.
    """
    if config is not None and not isinstance(config, (Mapping, ProviderConfig)):
        if callable(getattr(config, "complete", None)):
            return config  # already an LLMClient
        raise ProviderConfigError(
            f"provider config must be a mapping or ProviderConfig, got {type(config).__name__}"
        )
    cfg = resolve_provider_config(config, env=env, config_path=config_path)
    if cfg.problems:
        raise ProviderConfigError("; ".join(cfg.problems))
    if cfg.provider == "scripted":
        if not cfg.responses:
            raise ProviderConfigError(
                "provider 'scripted' needs a responses mapping "
                "(godsv.llm.responses in config.yaml or the 'responses' argument)"
            )
        return ScriptedLLM(responses=dict(cfg.responses))
    if cfg.ready():
        return OpenAICompatibleLLM(cfg, cache_ledger=cache_ledger)
    if scripted is not None:
        return scripted
    raise ProviderConfigError(not_configured_message(cfg))


def prompt_cache_surface(
    config: Any = None,
    *,
    env: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
    ledger: Any = None,
    limit: int = 0,
) -> dict[str, Any]:
    """The prompt-cache surface of the configured provider — **without a call**.

    This is what ``skaldir prompt cache status`` reports: the strategy this
    endpoint's cache needs (documented mode, the usage fields a verdict is read
    from, the reference it came from) plus the ledger of requests that were
    actually measured. It never calls the provider: a cache verdict that was not
    observed stays ``unknown`` instead of being produced here.
    """
    try:
        cfg = resolve_provider_config(config, env=env, config_path=config_path)
    except LLMError as exc:
        return {
            "configured": False,
            "problems": [str(exc)],
            "strategy": None,
            "ledger": str(ledger or ""),
            "entries": 0,
            "summary": pc.summarize(()),
            "note": "the provider configuration could not be resolved — no strategy is claimed",
        }
    strategy = pc.strategy_for(cfg.provider, base_url=cfg.base_url, model=cfg.model)
    resolved = _resolve_cache_ledger(ledger)
    rows, bad = resolved.entries(limit=limit) if resolved is not None else ([], 0)
    return {
        "configured": cfg.ready(),
        "problems": list(cfg.problems),
        "provider": strategy.provider,
        "mode": strategy.mode,
        "model": cfg.model,
        "endpoint": endpoint_for(cfg.base_url) if cfg.base_url else "",
        "api_key": "set" if cfg.api_key else "unset",
        "strategy": strategy.to_dict(),
        "ledger": str(resolved.path) if resolved is not None else "",
        "ledger_enabled": resolved is not None,
        "entries": len(rows),
        "bad_lines": int(bad),
        "summary": pc.summarize(rows),
        "last": rows[-1].to_dict() if rows else None,
        "recent": [row.to_dict() for row in rows[-20:]],
        "support": pc.support_table(),
        "note": (
            "the strategy is what the provider documents; the summary is what its "
            "usage payloads reported. `unknown` means the provider reported no cache "
            "counter — it is never counted as a hit"
        ),
    }


def provider_summary(cfg: ProviderConfig) -> str:
    """One-line human summary with the documented attempt accounting (§43)."""
    attempts = total_attempts(client_attempts=1, sdk_retries=0)
    return (
        f"{cfg.provider} model={cfg.model or '<unset>'} "
        f"endpoint={endpoint_for(cfg.base_url) or '<unset>'} "
        f"api_key={'set' if cfg.api_key else 'unset'} "
        f"timeout={cfg.timeout_s:g}s total={cfg.effective_total_timeout():g}s "
        f"max_retries={cfg.max_retries} (first call budget: {attempts} attempt)"
    )


def config_with(cfg: ProviderConfig, **changes: Any) -> ProviderConfig:
    """Copy-on-write helper (tests/CLI flags) — the key still never renders."""
    return replace(cfg, **changes)


# --------------------------------------------------------------------------- #
# vision / image input                                                         #
# --------------------------------------------------------------------------- #
#
# The OpenAI chat format carries an image as one *content part* of a user
# message. Two spellings reach the endpoint, and both are built here:
#
# ``{"type": "image_url", "image_url": {"url": "data:image/png;base64,<…>"}}``
#     a **local file** — its bytes are base64-encoded into the request. The
#     bytes leave the machine with the request: the caller is told which file
#     and which endpoint (see ``request_image_summary`` and the vision CLI).
# ``{"type": "image_url", "image_url": {"url": "https://…/x.png"}}``
#     a **remote image** — only the URL is sent; the provider fetches the bytes.
#
# Everything in this section is additive: ``complete()`` / ``_body()`` / ``_send``
# keep their shipped behaviour and semantics. Guards run *before* any request is
# built (``guard_image``), and nothing here ever writes the base64 payload, the
# api key or a local path into a log: ``scrub_image_data_urls`` collapses data
# URLs for any message that might travel, and ``request_image_summary`` is the
# only sanctioned view of a prepared body.

#: OpenAI's name for an image content part — unchanged across compatible servers.
IMAGE_PART_TYPE = "image_url"

#: Default cap for one image (10 MiB ≈ 13.2 MiB of base64 on the wire).
DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024

#: The mime types the guards accept (what every vision endpoint documents).
SUPPORTED_IMAGE_MIME: tuple[str, ...] = (
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/bmp",
)

#: Extension → mime, used to *declare* a type that ``sniff_image_mime`` verifies.
IMAGE_MIME_BY_EXT: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".jpe": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}

#: A separate model for images, when the main model is text-only. Resolution
#: order is documented at :func:`vision_model`.
ENV_VISION_MODEL = "SKALDIR_LLM_VISION_MODEL"

#: The tier ``tiers`` mapping / ``SKALDIR_LLM_TIER_MODELS`` may bind a vision model to.
VISION_TIER = "vision"

#: Matched against a ``data:`` URL to collapse it for any message that travels.
_DATA_URL_RE = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+")


class ImageInputError(LLMError):
    """The image handed in is not sendable (empty, wrong type, over the cap).

    Raised *before* a request is built, so a refused image never reaches the
    network and never appears in a body.
    """


class ImageNotSupportedError(LLMProviderError):
    """The endpoint/model refused the image part — a capability, not a transport, fault.

    Carries the provider's own status so a caller can tell "this model cannot
    see images" from "the call failed". ``reason`` is the matched provider text,
    already scrubbed of base64 payloads.
    """

    def __init__(self, message: str, *, status: int | None = None, reason: str = "") -> None:
        super().__init__(message, status=status)
        self.reason = str(reason)


def sniff_image_mime(data: bytes) -> str:
    """The mime type the *bytes* are, or ``""`` when they match no signature.

    Bytes win over any declared type or file extension: a ``.png`` that is really
    a PDF is refused by :func:`guard_image` instead of being sent.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) > 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return ""


def guard_image(
    data: bytes,
    *,
    mime: str = "",
    origin: str = "",
    max_bytes: int | None = None,
    allowed: Sequence[str] | None = None,
) -> str:
    """Check one image against the size/type guards; returns the accepted mime.

    ``origin`` is the human name used in the refusal (a path or a URL) — never a
    payload. Raises :class:`ImageInputError` for an empty image, an over-cap
    image, an unrecognised type, a type outside ``allowed``, or a declared type
    that the bytes contradict.
    """
    limit = DEFAULT_MAX_IMAGE_BYTES if max_bytes is None else int(max_bytes)
    if limit <= 0:
        limit = DEFAULT_MAX_IMAGE_BYTES
    name = str(origin or "the image")
    if not data:
        raise ImageInputError(f"{name} is empty — there is nothing to send")
    if len(data) > limit:
        raise ImageInputError(
            f"{name} is {len(data)} bytes, over the {limit} byte image cap "
            f"({limit / (1024 * 1024):.1f} MiB) — raise --max-image-mb if this is intended"
        )
    declared = str(mime or "").strip().lower()
    sniffed = sniff_image_mime(data)
    if sniffed and declared and declared != sniffed:
        raise ImageInputError(
            f"{name} is declared {declared} but its bytes are {sniffed} — refusing to "
            f"send a mislabelled image"
        )
    # The bytes decide: a declared extension is never enough. Every mime this
    # guard accepts has a signature, so "no signature" means "not an image" (a
    # text file named `.png` is refused here, not sent).
    if not sniffed:
        hint = f" (declared {declared})" if declared else ""
        raise ImageInputError(
            f"{name} is not a recognised image ({', '.join(SUPPORTED_IMAGE_MIME)}){hint} — "
            f"the bytes match no known image signature"
        )
    allow = tuple(allowed) if allowed else SUPPORTED_IMAGE_MIME
    if sniffed not in allow:
        raise ImageInputError(f"{name} is {sniffed} — this endpoint takes {', '.join(allow)}")
    return sniffed


def image_part_from_bytes(
    data: bytes,
    *,
    mime: str = "",
    origin: str = "",
    max_bytes: int | None = None,
    allowed: Sequence[str] | None = None,
) -> dict[str, Any]:
    """One image content part for a **local** image (base64 data URL)."""
    accepted = guard_image(
        data, mime=mime, origin=origin, max_bytes=max_bytes, allowed=allowed
    )
    encoded = base64.b64encode(data).decode("ascii")
    return {"type": IMAGE_PART_TYPE, "image_url": {"url": f"data:{accepted};base64,{encoded}"}}


def image_part_from_url(url: str) -> dict[str, Any]:
    """One image content part for a **remote** image (the URL travels, not bytes)."""
    text = str(url or "").strip()
    if not text.lower().startswith(("http://", "https://")):
        raise ImageInputError(
            f"remote image URLs must be http(s); got {text[:60]!r} — use a local path "
            f"to send a file's bytes"
        )
    return {"type": IMAGE_PART_TYPE, "image_url": {"url": text}}


def image_messages(prompt: str, parts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One user message: the text part first, then one part per image."""
    content: list[dict[str, Any]] = [{"type": "text", "text": str(prompt)}]
    content.extend(dict(part) for part in parts)
    return [{"role": "user", "content": content}]


def vision_request_body(
    cfg: ProviderConfig,
    prompt: str,
    parts: Sequence[Mapping[str, Any]],
    *,
    tier: str = "main",
    model: str = "",
) -> dict[str, Any]:
    """The exact body for one image request (pure — no client, no I/O).

    Same shape as :func:`chat_request_body`, so the image path inherits the
    configured ``max_tokens``/``temperature``/``extra_body``/``stream`` handling;
    ``model`` overrides the tier mapping for this call only.
    """
    body = chat_request_body(cfg, image_messages(prompt, parts), tier=tier)
    if str(model or "").strip():
        body["model"] = str(model).strip()
    return body


def scrub_image_data_urls(text: Any, *, limit: int = 400) -> str:
    """Collapse every ``data:image…;base64,…`` payload in a message.

    The one place an image could leak into a log is provider/exception text that
    echoes the request back. The base64 body is replaced by its mime type and
    length; the rest of the message is kept verbatim (still length-bounded).
    """

    def _replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        head, _, payload = raw.partition(";base64,")
        return f"{head};base64,<{len(payload)} base64 chars omitted>"

    return _compact(_DATA_URL_RE.sub(_replace, str(text)), limit)


def request_image_summary(body: Mapping[str, Any]) -> dict[str, Any]:
    """A safe, printable view of a prepared image request (never the payload).

    Reports what would leave the machine: one record per image part with its
    kind (``data-url``/``remote-url``), mime type, byte size and sha256 — the
    data URL itself is reduced to its mime. The api key is not in a body at all
    (it travels in the header) and is not reported here either.
    """
    images: list[dict[str, Any]] = []
    text_chars = 0
    messages = body.get("messages")
    for position, message in enumerate(messages if isinstance(messages, list) else []):
        content = message.get("content") if isinstance(message, Mapping) else None
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, Mapping):
                continue
            kind = str(part.get("type") or "")
            if kind == "text":
                text_chars += len(str(part.get("text") or ""))
                continue
            if kind != IMAGE_PART_TYPE:
                continue
            holder = part.get("image_url")
            holder = holder if isinstance(holder, Mapping) else {}
            url = str(holder.get("url") or "")
            record: dict[str, Any] = {
                "message": position,
                "detail": str(holder.get("detail") or ""),
            }
            if url.startswith("data:"):
                head, _, payload = url.partition(";base64,")
                raw = base64.b64decode(payload) if payload else b""
                record.update(
                    {
                        "kind": "data-url",
                        "mime": head[len("data:") :],
                        "bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
            else:
                record.update({"kind": "remote-url", "url": url})
            images.append(record)
    return {
        "model": str(body.get("model") or ""),
        "messages": len(messages) if isinstance(messages, list) else 0,
        "image_parts": images,
        "image_bytes": sum(int(item.get("bytes") or 0) for item in images),
        "text_chars": text_chars,
        "stream": bool(body.get("stream")),
    }


#: Substrings of model names that document image input. A hint, never a verdict —
#: the measured answer comes from a probe (:func:`vision_probe`), and this catalog
#: only says what is *expected* so the two can be compared.
VISION_MODEL_HINTS: tuple[str, ...] = (
    "gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-5", "o3", "o4-mini", "chatgpt-4o",
    "claude-3", "claude-4", "claude-sonnet", "claude-opus", "claude-haiku",
    "gemini", "gemma-3", "gemma3", "gemma-4",
    "qwen-vl", "qwen2-vl", "qwen2.5-vl", "qwen3-vl", "qwen2.5-omni", "qwen3-omni",
    "llava", "internvl", "pixtral", "mistral-small-3", "mistral-medium-3",
    "llama-4", "llama-3.2-vision", "llama3.2-vision", "mllama",
    "phi-3.5-vision", "phi-4-multimodal", "phi-4-mm",
    "minicpm-v", "moondream", "idefics", "florence", "paligemma", "smolvlm",
    "fuyu", "cogvlm", "molmo", "ovis", "internlm-xcomposer",
    "grok-2-vision", "grok-3", "grok-4", "deepseek-vl", "janus", "glm-4v", "glm-4.5v",
    "yi-vl", "step-1v", "kosmos", "nemotron-nano-vl", "midjourney", "dall-e",
)

#: Substrings of model names that are documented *text-only* — the honest "no".
#: Measured caveat (2026-09-18, api.deepseek.com): ``deepseek-chat`` is on this list
#: and still accepted *and read* a real image through that endpoint (the description
#: matched the picture). The list is an expectation, the probe is the answer — which
#: is why both are always reported side by side.
TEXT_ONLY_MODEL_HINTS: tuple[str, ...] = (
    "deepseek-chat", "deepseek-coder", "deepseek-reasoner", "deepseek-v3", "deepseek-r1",
    "text-davinci", "gpt-3.5-turbo-instruct", "qwen2.5-coder", "qwen3-coder",
    "codellama", "llama-3.1", "llama-3.3", "starcoder", "whisper", "embedding",
)


def vision_model(cfg: ProviderConfig, override: str = "") -> str:
    """Which model answers an image request.

    Order: the explicit ``override`` → ``$SKALDIR_LLM_VISION_MODEL`` →
    ``cfg.tier_models[VISION_TIER]`` (config ``tiers.vision`` or
    ``SKALDIR_LLM_TIER_MODELS="vision=…"``) → the configured ``model``. The
    service passes the result to :func:`vision_request_body`.
    """
    text = str(override or "").strip()
    if text:
        return text
    from .env import first_env

    from_env = str(first_env((ENV_VISION_MODEL,)) or "").strip()
    if from_env:
        return from_env
    return str(cfg.tier_models.get(VISION_TIER) or "").strip() or str(cfg.model or "")


def vision_tier_for(cfg: ProviderConfig, model: str) -> str:
    """The tier name to ask for, so ``model`` (when it is a mapped tier) is honoured."""
    if str(model or "").strip() and cfg.tier_models.get(VISION_TIER) == str(model).strip():
        return VISION_TIER
    return "main"


def vision_catalog(model: str) -> dict[str, Any]:
    """The catalog's *expectation* for a model name (``likely``/``unlikely``/``unknown``).

    Not a capability claim: the name is matched against the two documented lists
    above. :func:`vision_probe`/``vision capabilities`` is what measures, and the
    report states both, so a catalog guess never passes as a measurement.
    """
    name = str(model or "").strip().lower()
    if not name:
        return {"verdict": "unknown", "reason": "no model configured", "matched": ""}
    for hint in TEXT_ONLY_MODEL_HINTS:
        if hint in name:
            return {
                "verdict": "unlikely",
                "reason": f"the name matches the text-only list ({hint!r})",
                "matched": hint,
            }
    for hint in VISION_MODEL_HINTS:
        if hint in name:
            return {
                "verdict": "likely",
                "reason": f"the name matches the image-capable list ({hint!r})",
                "matched": hint,
            }
    return {
        "verdict": "unknown",
        "reason": "the name is in neither list — only a probe can answer",
        "matched": "",
    }


#: Provider texts that mean "this model cannot see". Matched case-insensitively
#: against the *scrubbed* error detail, and only for 4xx statuses.
IMAGE_REFUSAL_PATTERNS: tuple[str, ...] = (
    r"does not support (?:image|vision|multimodal)",
    r"(?:image|vision|multimodal|image_url)[^.]{0,40}\bnot supported\b",
    r"\bnot supported\b[^.]{0,40}\b(?:image|vision|multimodal)\b",
    r"not a multimodal model",
    r"multimodal[^.]{0,20}not (?:enabled|supported|available)",
    r"unsupported (?:image|content) (?:type|part)",
    r"invalid (?:image|content) (?:type|part|format)",
    r"cannot (?:read|process|handle|accept|see)[^.]{0,40}image",
    r"only (?:accepts?|supports?|handles?) text",
    r"text[- ]only",
    r"vision[^.]{0,20}not (?:enabled|supported|available)",
    r"content type[^.]{0,30}not supported",
    r"no vision",
)


def image_refusal_reason(text: Any, *, status: int | None = None) -> str:
    """The matched refusal text of a provider answer, or ``""`` (no match).

    Only 4xx answers count — a 500 is never read as "this model cannot see".
    """
    if status is not None and not 400 <= int(status) < 500:
        return ""
    scrubbed = scrub_image_data_urls(text, limit=800).lower()
    for pattern in IMAGE_REFUSAL_PATTERNS:
        match = re.search(pattern, scrubbed)
        if match:
            return match.group(0)
    return ""


def vision_chat(
    client: Any,
    *,
    prompt: str,
    parts: Sequence[Mapping[str, Any]],
    tier: str = "main",
    prompt_id: str = "",
) -> ChatResult:
    """Send one image request through the client's chat path.

    The client's own transport/retry/timeout policy is used unchanged (this is
    :meth:`OpenAICompatibleLLM.chat`). A provider refusal that names image
    support is re-raised as :class:`ImageNotSupportedError` with a plain English
    message naming the model — the one error a caller must not read as a
    transport fault. ``prompt_id`` is accepted for interface symmetry with
    :meth:`OpenAICompatibleLLM.complete` and is not sent on the wire.
    """
    del prompt_id  # nothing extra goes on the wire for a prompt id
    messages = image_messages(prompt, parts)
    try:
        return client.chat(messages, tier=tier)
    except ImageNotSupportedError:
        raise
    except LLMProviderError as exc:
        detail = scrub_image_data_urls(str(exc))
        reason = image_refusal_reason(str(exc), status=exc.status)
        if not reason:
            raise
        model = ""
        config = getattr(client, "config", None)
        if config is not None:
            try:
                model = str(config.model_for_tier(tier))
            except Exception:  # noqa: BLE001 — a model name is cosmetic here
                model = str(getattr(config, "model", "") or "")
        raise ImageNotSupportedError(
            f"the model {model or '<configured>'} refused the image part "
            f"(HTTP {exc.status}: {reason}) — it does not accept images; point "
            f"$SKALDIR_LLM_VISION_MODEL (or tiers.vision) at a vision-capable model "
            f"and run `skaldir vision capabilities` to have it measured. Detail: {detail}",
            status=exc.status,
            reason=reason,
        ) from exc


def image_size(data: bytes) -> tuple[int, int] | None:
    """``(width, height)`` read from the image header, or ``None``.

    PNG/GIF/JPEG/BMP only — enough for the token note, and no dependency is
    added for it (the header fields are documented and the parse is total).
    """
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
            return struct.unpack(">II", data[16:24])
        if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
            return struct.unpack("<HH", data[6:10])
        if data.startswith(b"BM") and len(data) >= 26:
            width, height = struct.unpack("<ii", data[18:26])
            return abs(int(width)), abs(int(height))
        if data.startswith(b"\xff\xd8"):
            index = 2
            while index + 9 < len(data):
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    index += 2
                    continue
                length = struct.unpack(">H", data[index + 2 : index + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    height, width = struct.unpack(">HH", data[index + 5 : index + 9])
                    return int(width), int(height)
                index += 2 + length
    except (struct.error, ValueError):  # a truncated header is not an image size
        return None
    return None


def estimate_image_tokens(size: tuple[int, int] | None) -> int | None:
    """The documented image-token rule (85 + pixels/750), or ``None`` without a size.

    An **estimate**, and labelled as one wherever it is reported: the provider's
    own ``usage`` counters are the measurement, this is the floor to compare them
    against — images are not free, even at 1x1 (:func:`vision_probe_image` costs
    the smallest request the guard allows).
    """
    if not size:
        return None
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0:
        return None
    return 85 + (width * height + 749) // 750


#: A valid 1x1 PNG — the smallest image a probe can send, inlined so the probe
#: needs no file, no dependency and no network for the bytes themselves.
PROBE_PNG: bytes = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

#: The question a probe asks. Any answer (including "I see a 1x1 pixel") proves
#: the part was accepted; only a refusal proves the model cannot see.
PROBE_PROMPT = "Reply with the single word: ok"


def vision_probe(
    client: Any,
    *,
    tier: str = "main",
    image: bytes | None = None,
) -> dict[str, Any]:
    """Measure whether the configured model accepts an image part at all.

    Sends one 1x1 PNG and reads the answer: ``accepts_images`` is ``True`` when
    the endpoint answered, ``False`` when it refused the image part
    (:class:`ImageNotSupportedError`), and ``None`` when the call failed for an
    unrelated reason (no key, no network) — an unknown is reported as unknown.
    """
    data = PROBE_PNG if image is None else bytes(image)
    part = image_part_from_bytes(data, mime="image/png", origin="probe image")
    result: dict[str, Any] = {
        "attempted": True,
        "image": {"mime": "image/png", "bytes": len(data), "width": 1, "height": 1},
        "prompt": PROBE_PROMPT,
        "accepts_images": None,
        "answer": "",
        "status": None,
        "detail": "",
    }
    try:
        answer = vision_chat(client, prompt=PROBE_PROMPT, parts=[part], tier=tier)
    except ImageNotSupportedError as exc:
        result["accepts_images"] = False
        result["status"] = exc.status
        result["detail"] = exc.reason or scrub_image_data_urls(str(exc))
        return result
    except LLMProviderError as exc:
        result["status"] = exc.status
        result["detail"] = f"inconclusive: {scrub_image_data_urls(str(exc))}"
        return result
    except LLMError as exc:
        result["detail"] = f"inconclusive: {scrub_image_data_urls(str(exc))}"
        return result
    result["accepts_images"] = True
    result["answer"] = scrub_image_data_urls(answer.text, limit=200)
    result["finish_reason"] = answer.finish_reason
    result["usage"] = dict(answer.usage)
    return result


# --------------------------------------------------------------------------- #
# structured output — the wire half of a schema-constrained call               #
# --------------------------------------------------------------------------- #
#
# ``complete()`` answers with free text and ``chat()`` answers with text plus
# tool calls. A *typed* decision (Jev routing, verification, a plan) needs the
# answer to satisfy a JSON Schema before anything is allowed to use it, so the
# request must carry ``response_format``:
#
# ``{"type": "json_schema", "json_schema": {"name", "strict", "schema"}}``
#     the native form (OpenAI, DeepSeek, OpenRouter, vLLM, llama.cpp builds
#     that implement guided decoding …). The endpoint constrains the answer.
# ``{"type": "json_object"}``
#     the older, weaker form: valid JSON, but no schema — the caller still has
#     to validate.
#
# What is here is *only* the wire shape: the request builder
# (:func:`json_schema_response_format` / :func:`json_object_response_format`),
# the refusal detector (:func:`structured_refusal_reason`), the error that
# names a refusal (:class:`StructuredOutputNotSupportedError`) and
# :func:`structured_chat`, which re-raises a refusal as that error.
#
# Validation, repair and the honest report (raw text + the validation error)
# live in :mod:`skaldir.structured`; this module never validates a value and
# never coerces one. ``chat(response_format=…)`` puts the field on the wire
# verbatim.

#: The strongest form: the endpoint itself is asked to constrain the answer.
RESPONSE_FORMAT_JSON_SCHEMA = "json_schema"

#: The weak form: valid JSON, no schema constraint.
RESPONSE_FORMAT_JSON_OBJECT = "json_object"

#: ``name`` in a ``json_schema`` response format must match this (OpenAI rule).
SCHEMA_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class StructuredOutputNotSupportedError(LLMProviderError):
    """The endpoint refused the ``response_format`` field — a capability fault.

    Carries the provider's own status so a caller can tell "this model cannot
    constrain its answer" from "the call failed". ``reason`` is the matched
    provider text (already scrubbed of image payloads), never a guess.
    """

    def __init__(self, message: str, *, status: int | None = None, reason: str = "") -> None:
        super().__init__(message, status=status)
        self.reason = str(reason)


#: Provider texts that mean "``response_format`` is not usable here". Matched
#: case-insensitively against the *scrubbed* error detail, and only for 4xx
#: statuses — a 500 is never read as "this model cannot do structured output".
STRUCTURED_REFUSAL_PATTERNS: tuple[str, ...] = (
    r"response_format[^.]{0,80}\b(?:not supported|unsupported|unknown|invalid|unavailable)\b",
    r"\b(?:not supported|unsupported|unknown|invalid|unavailable)\b[^.]{0,80}response_format",
    r"json_?schema[^.]{0,80}\b(?:not supported|unsupported|unknown|invalid|unavailable)\b",
    r"\b(?:not supported|unsupported|unavailable)\b[^.]{0,80}json_?schema",
    r"['\"]?json_schema['\"]? is not one of",
    r"response_format[^.]{0,80}\bmust be (?:one of|['\"]?(?:text|json_object))",
    r"json mode[^.]{0,40}not (?:enabled|supported|available)",
    r"structured outputs?[^.]{0,40}\b(?:not supported|unsupported|unavailable)\b",
    r"\b(?:not supported|unsupported|unavailable)\b[^.]{0,40}structured outputs?",
    r"guided (?:decoding|generation|json)[^.]{0,40}\b(?:not supported|unsupported|disabled)\b",
)


def structured_refusal_reason(text: Any, *, status: int | None = None) -> str:
    """The matched refusal text of a provider answer, or ``""`` (no match).

    Only 4xx answers count: a 5xx is a transport/server problem, and reading it
    as "the endpoint cannot do schemas" would turn a retryable failure into a
    capability downgrade.
    """
    if status is not None and not 400 <= int(status) < 500:
        return ""
    scrubbed = scrub_image_data_urls(text, limit=800).lower()
    for pattern in STRUCTURED_REFUSAL_PATTERNS:
        match = re.search(pattern, scrubbed)
        if match:
            return match.group(0)
    return ""


def schema_name_for(text: Any, *, default: str = "response") -> str:
    """A valid ``json_schema.name`` (``[A-Za-z0-9_-]{1,64}``) from any label."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", str(text or "").strip()).strip("_")
    return (cleaned or default)[:64]


def json_schema_response_format(
    name: str,
    schema: Mapping[str, Any],
    *,
    strict: bool = True,
    description: str = "",
) -> dict[str, Any]:
    """The native OpenAI ``response_format`` body for one JSON Schema.

    ``strict`` is on by default: with it, the endpoint is asked to guarantee the
    schema instead of merely promising JSON. The schema itself is passed
    through unchanged — a caller that wants a *validated* answer still validates
    (:mod:`skaldir.structured`), because a promise from the wire is not a
    measurement of the answer.
    """
    if not isinstance(schema, Mapping):
        raise ProviderConfigError(
            f"a json_schema response_format needs a schema mapping, got {type(schema).__name__}"
        )
    label = schema_name_for(name)
    if not SCHEMA_NAME_RE.match(label):
        raise ProviderConfigError(
            f"invalid json_schema name {label!r} — must match {SCHEMA_NAME_RE.pattern}"
        )
    inner: dict[str, Any] = {
        "name": label,
        "strict": bool(strict),
        "schema": dict(schema),
    }
    if str(description or "").strip():
        inner["description"] = str(description).strip()
    return {"type": RESPONSE_FORMAT_JSON_SCHEMA, "json_schema": inner}


def json_object_response_format() -> dict[str, Any]:
    """The weak ``response_format``: the answer must be JSON, nothing more."""
    return {"type": RESPONSE_FORMAT_JSON_OBJECT}


def response_format_for(
    mode: str,
    *,
    schema: Mapping[str, Any] | None = None,
    name: str = "",
    strict: bool = True,
) -> dict[str, Any] | None:
    """Build the ``response_format`` for a mode (``None`` = ask in the prompt).

    ``native`` needs a schema; ``json_object`` needs none; ``prompted`` puts
    nothing on the wire (the caller appends the instruction to the messages and
    validates locally). An unknown mode is a usage error, not a silent default.
    """
    from .structured import MODE_JSON_OBJECT, MODE_NATIVE, MODE_PROMPTED

    value = str(mode or "").strip().lower()
    if value == MODE_NATIVE:
        if not schema:
            raise ProviderConfigError("mode 'native' needs a JSON schema")
        return json_schema_response_format(name or "response", schema, strict=strict)
    if value == MODE_JSON_OBJECT:
        return json_object_response_format()
    if value == MODE_PROMPTED:
        return None
    raise ProviderConfigError(
        f"unknown structured-output mode {mode!r} "
        f"(expected {MODE_NATIVE!r}, {MODE_JSON_OBJECT!r} or {MODE_PROMPTED!r})"
    )


def structured_chat(
    client: Any,
    *,
    messages: Sequence[Mapping[str, Any]],
    tier: str = "main",
    response_format: Mapping[str, Any] | None = None,
) -> ChatResult:
    """One schema-constrained chat call through the client's own transport.

    The client's retry/timeout policy is used unchanged (this *is*
    :meth:`OpenAICompatibleLLM.chat`). Two failure modes get their own error:

    * the endpoint refuses the field → :class:`StructuredOutputNotSupportedError`
      (a capability, not a transport, fault) — the caller must fall back to a
      weaker mode or report it, never silently drop ``response_format``;
    * the client cannot carry a ``response_format`` at all (a test double or an
      older client) → the same error, named as such.
    """
    try:
        return client.chat(messages, tier=tier, response_format=response_format)
    except StructuredOutputNotSupportedError:
        raise
    except TypeError as exc:
        # A duck-typed client without the keyword: reported, never worked around.
        raise StructuredOutputNotSupportedError(
            f"the client {type(client).__name__} does not accept response_format "
            f"({_compact(exc, 120)}) — no request was attempted with a schema",
            status=None,
            reason="client-without-response_format",
        ) from exc
    except LLMProviderError as exc:
        detail = scrub_image_data_urls(str(exc))
        reason = structured_refusal_reason(str(exc), status=exc.status)
        if not reason:
            raise
        model = ""
        config = getattr(client, "config", None)
        if config is not None:
            try:
                model = str(config.model_for_tier(tier))
            except Exception:  # noqa: BLE001 — a model name is cosmetic here
                model = str(getattr(config, "model", "") or "")
        raise StructuredOutputNotSupportedError(
            f"the model {model or '<configured>'} refused response_format="
            f"{str(response_format.get('type') if isinstance(response_format, Mapping) else '')!r} "
            f"(HTTP {exc.status}: {reason}) — fall back to a weaker mode "
            f"(json_object, or a prompted JSON answer validated locally). Detail: {detail}",
            status=exc.status,
            reason=reason,
        ) from exc
