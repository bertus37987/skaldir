"""Prompt caching — explicit breakpoints, automatic prefix caching, honest status.

A prompt cache is a *provider-side* KV cache keyed on the bytes of a request
prefix. Two families exist and they need different things from us:

``explicit`` (Anthropic Messages API, and gateways that pass the field through)
    the request has to *ask*: ``cache_control: {"type": "ephemeral"}`` on the
    last stable block — the system prompt, the tool catalogue, the project
    context. Up to :data:`MAX_BREAKPOINTS` markers per request; the marker
    caches everything up to and including its position.
``automatic`` (DeepSeek context caching, OpenAI prompt caching)
    nothing has to be sent. The provider hashes the request prefix itself and
    serves the longest cached prefix it has — so the only thing we control is
    that the prefix is **byte-stable**, which is exactly what
    :mod:`skaldir.prompt_builder` already guarantees (``fingerprint_of`` is the
    sha256 of the composed bytes, and the freeze file refuses a silent change).

This module owns the three things those two families share:

1. **Where the stable prefix is.** :func:`stable_prefix` canonicalises the
   leading system message(s) plus the tool catalogue into bytes and hashes
   them; :func:`prefix_reuse` compares a request against the previous one and
   says how many leading messages the provider *can* reuse. That answer is a
   **prediction from bytes** (``basis: "byte-compare"``) and is labelled as
   such — it is never reported as if the provider had confirmed it.
2. **Placing explicit markers.** :func:`explicit_breakpoints` returns the
   annotated request (messages + tools) and the list of breakpoints with the
   path each one landed on. When the caller hands in the
   :class:`skaldir.prompt_builder.PromptAssembly` the system prompt came from,
   the composer's own section offsets are used to split it into blocks — and the
   split is only accepted when it reconstructs the prompt **byte for byte**.
3. **Reading what the provider reported.** :func:`cache_report` reads the cache
   counters out of a ``usage`` block (``cache_read_input_tokens`` /
   ``cache_creation_input_tokens``, ``prompt_cache_hit_tokens`` /
   ``prompt_cache_miss_tokens``, ``prompt_tokens_details.cached_tokens``) and
   answers exactly one of :data:`CACHE_HIT`, :data:`CACHE_MISS`,
   :data:`CACHE_UNKNOWN`. **A field that is absent means ``unknown``** — the
   provider did not tell us, so we do not guess, and we never turn "we sent a
   stable prefix" into "the cache hit".

What this module deliberately does *not* do: it never invents a token count, it
never flips a documented claim into a measurement, and it never writes to the
request unless a caller asked for explicit breakpoints. The provider support
table (:data:`SUPPORT`) says which providers are *documented* and which were
actually *measured* — the measurement itself lives in ``MVP/54-prompt-caching.md``
and in ``tests/test_prompt_cache.py``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

__all__ = [
    "ANTHROPIC_FIELDS",
    "BREAKPOINT_TARGETS",
    "CACHE_CONTROL_TYPE",
    "CACHE_HIT",
    "CACHE_MISS",
    "CACHE_STATUSES",
    "CACHE_UNKNOWN",
    "CONF_DOCUMENTED",
    "CONF_PARTIAL",
    "CONF_UNKNOWN",
    "DEEPSEEK_FIELDS",
    "GENERIC_FIELDS",
    "LEDGER_FILE",
    "MAX_BREAKPOINTS",
    "MODE_AUTO",
    "MODE_EXPLICIT",
    "MODE_NONE",
    "OPENAI_FIELDS",
    "SUPPORT",
    "Breakpoint",
    "CacheEntry",
    "CacheLedger",
    "CachePlan",
    "CachePlanError",
    "CacheReport",
    "CacheStrategy",
    "PrefixReuse",
    "StablePrefix",
    "cache_counters",
    "cache_report",
    "canonical_bytes",
    "default_ledger_path",
    "explicit_breakpoints",
    "is_local_endpoint",
    "prefix_reuse",
    "replace_strategy_note",
    "section_blocks",
    "stable_prefix",
    "strategy_for",
    "summarize",
    "support_table",
]

# --------------------------------------------------------------------------- #
# vocabulary                                                                    #
# --------------------------------------------------------------------------- #

#: The three answers :func:`cache_report` may give. ``unknown`` is a real answer:
#: it means *the provider did not report a cache counter*, not "no cache".
CACHE_HIT = "hit"
CACHE_MISS = "miss"
CACHE_UNKNOWN = "unknown"
CACHE_STATUSES: tuple[str, ...] = (CACHE_HIT, CACHE_MISS, CACHE_UNKNOWN)

#: How a provider's cache is driven.
#: ``explicit`` = it needs ``cache_control`` markers; ``automatic`` = a stable
#: prefix is enough; ``none`` = nothing documented (status stays ``unknown``).
MODE_EXPLICIT = "explicit"
MODE_AUTO = "automatic"
MODE_NONE = "none"

#: The one ``cache_control`` type Anthropic documents.
CACHE_CONTROL_TYPE = "ephemeral"
#: TTL values the same API documents; anything else is refused, never guessed.
CACHE_TTLS: tuple[str, ...] = ("5m", "1h")
#: Anthropic's documented ceiling: at most four markers in one request.
MAX_BREAKPOINTS = 4
#: The blocks a breakpoint may be placed on.
BREAKPOINT_TARGETS: tuple[str, ...] = ("system", "tools", "project_context")

#: How far a claim can be trusted. ``documented`` = the provider's own docs say
#: it; ``partial`` = the docs describe it for some routes/models only;
#: ``unknown`` = nothing documented, only a measurement could say more.
CONF_DOCUMENTED = "documented"
CONF_PARTIAL = "partial"
CONF_UNKNOWN = "unknown"

#: usage fields, by provider family. A dotted name walks nested objects.
ANTHROPIC_FIELDS: tuple[str, ...] = (
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)
DEEPSEEK_FIELDS: tuple[str, ...] = (
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
)
OPENAI_FIELDS: tuple[str, ...] = ("prompt_tokens_details.cached_tokens",)
#: Seen on OpenAI-compatible gateways that flatten the detail object.
GENERIC_FIELDS: tuple[str, ...] = ("cached_tokens", "cache_read_tokens", "cache_miss_tokens")

#: The flattened view: canonical counter → the field names that carry it, in
#: the order they are tried. Only a *reported* integer is ever used.
READ_NAMES: tuple[str, ...] = (
    "cache_read_input_tokens",
    "cached_tokens",
    "prompt_tokens_details.cached_tokens",
    "cache_read_tokens",
)
#: ``cache_write_tokens`` is OpenRouter's name for the tokens a request *wrote*
#: into the cache (documented, and reported next to ``cached_tokens``).
WRITE_NAMES: tuple[str, ...] = (
    "cache_creation_input_tokens",
    "cache_write_tokens",
    "prompt_tokens_details.cache_write_tokens",
    "cache_creation_tokens",
)
HIT_NAMES: tuple[str, ...] = ("prompt_cache_hit_tokens",)
MISS_NAMES: tuple[str, ...] = ("prompt_cache_miss_tokens", "cache_miss_tokens")


# --------------------------------------------------------------------------- #
# the provider support table                                                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CacheStrategy:
    """What one provider's prompt cache needs from us — with where it came from.

    ``expected`` is ``True`` only for a provider whose caching is documented: it
    is what makes an ``unknown`` status informative ("this provider documents
    automatic caching, yet reported no counter" instead of a shrug).
    """

    provider: str
    mode: str
    confidence: str = CONF_UNKNOWN
    read_fields: tuple[str, ...] = ()
    write_fields: tuple[str, ...] = ()
    #: counters that report tokens which did *not* come from the cache — DeepSeek's
    #: ``prompt_cache_miss_tokens``. Kept apart from ``write_fields`` on purpose: a
    #: miss is not a cache write (DeepSeek reports no write counter at all).
    miss_fields: tuple[str, ...] = ()
    reference: str = ""
    matched_by: str = ""
    note: str = ""

    @property
    def explicit(self) -> bool:
        return self.mode == MODE_EXPLICIT

    @property
    def expected(self) -> bool:
        """True when caching is documented/observed for this provider at all."""
        return self.mode != MODE_NONE and self.confidence in (CONF_DOCUMENTED, CONF_PARTIAL)

    @property
    def fields(self) -> tuple[str, ...]:
        out: list[str] = []
        for name in (*self.read_fields, *self.write_fields, *self.miss_fields):
            if name not in out:
                out.append(name)
        return tuple(out)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "mode": self.mode,
            "explicit": self.explicit,
            "expected": self.expected,
            "confidence": self.confidence,
            "read_fields": list(self.read_fields),
            "write_fields": list(self.write_fields),
            "miss_fields": list(self.miss_fields),
            "reference": self.reference,
            "matched_by": self.matched_by,
            "note": self.note,
        }


#: **Expectations with sources — not measurements.** One row per provider family
#: whose cache behaviour we can describe; ``confidence`` says how far the claim
#: goes. The measured column of this table lives in ``MVP/54-prompt-caching.md``
#: (which providers were actually called and what their usage payloads said).
SUPPORT: tuple[dict[str, Any], ...] = (
    {
        "provider": "anthropic",
        "hosts": ("api.anthropic.com",),
        "mode": MODE_EXPLICIT,
        "read_fields": ANTHROPIC_FIELDS[:1],
        "write_fields": ANTHROPIC_FIELDS[1:],
        "miss_fields": (),
        "confidence": CONF_DOCUMENTED,
        "reference": "https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching",
        "note": (
            "the request must carry cache_control on the block that ends the stable "
            "prefix; up to four markers; the native Messages API is a different wire "
            "shape than this client speaks, so the markers are only sent when a "
            "gateway that passes them through is configured"
        ),
    },
    {
        "provider": "deepseek",
        "hosts": ("api.deepseek.com",),
        "mode": MODE_AUTO,
        "read_fields": DEEPSEEK_FIELDS[:1],
        "write_fields": (),
        "miss_fields": DEEPSEEK_FIELDS[1:],
        "confidence": CONF_DOCUMENTED,
        "reference": "https://api-docs.deepseek.com/guides/kv_cache",
        "note": (
            "context caching is automatic and needs a byte-stable prefix; the usage "
            "block reports prompt_cache_hit_tokens / prompt_cache_miss_tokens"
        ),
    },
    {
        "provider": "openai",
        "hosts": ("api.openai.com",),
        "mode": MODE_AUTO,
        "read_fields": OPENAI_FIELDS,
        "write_fields": ("prompt_tokens_details.cache_write_tokens",),
        "miss_fields": (),
        "confidence": CONF_DOCUMENTED,
        "reference": "https://platform.openai.com/docs/guides/prompt-caching",
        "note": (
            "automatic prompt caching; the usage block reports "
            "prompt_tokens_details.cached_tokens"
        ),
    },
    {
        "provider": "openrouter",
        "hosts": ("openrouter.ai",),
        "mode": MODE_AUTO,
        "read_fields": tuple(OPENAI_FIELDS) + ANTHROPIC_FIELDS[:1] + DEEPSEEK_FIELDS[:1],
        "write_fields": ("cache_write_tokens", *ANTHROPIC_FIELDS[1:]),
        "miss_fields": (DEEPSEEK_FIELDS[1],),
        "confidence": CONF_PARTIAL,
        "reference": "https://openrouter.ai/docs/features/prompt-caching",
        "note": (
            "caching is decided by the upstream provider that answers the route, so "
            "both the automatic counters and cache_control passthrough are expected — "
            "which of them answers is per model, and only the usage block settles it"
        ),
    },
)

#: Hosts whose cache behaviour is documented to be *different* from the table.
_LOCAL_HOSTS: tuple[str, ...] = ("localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal")


def is_local_endpoint(base_url: str) -> bool:
    """True for a loopback/LAN endpoint (a local server caches its own KV)."""
    try:
        host = (urlparse(str(base_url or "")).hostname or "").lower()
    except ValueError:  # a base_url that is not a URL at all
        return False
    if not host:
        return False
    return host in _LOCAL_HOSTS or host.startswith("127.")


def _strategy_row(provider: str) -> Mapping[str, Any] | None:
    key = str(provider or "").strip().lower()
    for row in SUPPORT:
        if row["provider"] == key:
            return row
    return None


def _host_row(base_url: str) -> Mapping[str, Any] | None:
    try:
        host = (urlparse(str(base_url or "")).hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    for row in SUPPORT:
        for candidate in row["hosts"]:
            if host == candidate or host.endswith("." + candidate):
                return row
    return None


def _strategy_from_row(row: Mapping[str, Any], matched_by: str) -> CacheStrategy:
    return CacheStrategy(
        provider=str(row["provider"]),
        mode=str(row["mode"]),
        confidence=str(row["confidence"]),
        read_fields=tuple(row["read_fields"]),
        write_fields=tuple(row["write_fields"]),
        miss_fields=tuple(row.get("miss_fields") or ()),
        reference=str(row.get("reference", "")),
        matched_by=matched_by,
        note=str(row.get("note", "")),
    )


def _model_row(model: str) -> Mapping[str, Any] | None:
    """A row inferred from the *model name* — the weakest match, and labelled so.

    A gateway can answer on its own base_url while the model still names the
    family (``deepseek/deepseek-chat`` on OpenRouter, a DeepSeek-compatible proxy
    on a LAN address). The match is recorded as ``matched_by: "model"`` so a
    reader knows the provider was inferred from a name, not from the endpoint.
    """
    name = str(model or "").strip().lower()
    if not name:
        return None
    for row in SUPPORT:
        prefix = str(row["provider"])
        if name == prefix or name.startswith(f"{prefix}/") or name.startswith(f"{prefix}-"):
            return row
        if f"/{prefix}" in name or f"{prefix}/" in name:
            return row
    return None


def strategy_for(
    provider: str = "", *, base_url: str = "", model: str = "", default: CacheStrategy | None = None
) -> CacheStrategy:
    """The cache strategy for one provider id, else matched by host, else by model.

    Precedence: the named provider id (a catalog id like ``deepseek``) → the host
    of ``base_url`` → the model name (weakest: recorded as ``matched_by: "model"``)
    → a local endpoint (``MODE_NONE``: a local server manages its own KV cache and
    reports nothing we can read) → an undocumented endpoint (``MODE_NONE`` +
    ``unknown``, with a note saying that any cache status will come from the usage
    payload or stay ``unknown``).
    """
    key = str(provider or "").strip().lower()
    row = _strategy_row(key)
    if row is not None:
        return _strategy_from_row(row, "provider")
    row = _host_row(base_url)
    if row is not None:
        return _strategy_from_row(row, "base_url")
    row = _model_row(model)
    if row is not None:
        strategy = _strategy_from_row(row, "model")
        return replace_strategy_note(
            strategy,
            f"{strategy.note} — matched by the model name {model!r} because the "
            f"endpoint ({base_url or 'unset'}) is not a documented host; the usage "
            f"payload is what decides, this only says what to expect",
        )
    if is_local_endpoint(base_url):
        return CacheStrategy(
            provider=key or "local",
            mode=MODE_NONE,
            confidence=CONF_UNKNOWN,
            matched_by="base_url",
            note=(
                "a local endpoint keeps its own KV cache: it may reuse a prefix and "
                "report nothing about it, so the cache status is read from the usage "
                "payload when the server sends one and stays 'unknown' otherwise"
            ),
        )
    return CacheStrategy(
        provider=key or "unknown",
        mode=MODE_NONE,
        confidence=CONF_UNKNOWN,
        matched_by="",
        note=(
            "no documented prompt-cache behaviour for this endpoint"
            + (f" ({model})" if model else "")
            + " — a cache counter in the usage payload is still read and reported, "
            "everything else stays 'unknown'"
        ),
    )


def replace_strategy_note(strategy: CacheStrategy, note: str) -> CacheStrategy:
    """The same strategy with a different note (the table itself is never edited)."""
    return CacheStrategy(
        provider=strategy.provider,
        mode=strategy.mode,
        confidence=strategy.confidence,
        read_fields=strategy.read_fields,
        write_fields=strategy.write_fields,
        miss_fields=strategy.miss_fields,
        reference=strategy.reference,
        matched_by=strategy.matched_by,
        note=note,
    )


def support_table() -> dict[str, Any]:
    """The support table as JSON — every row with its source and confidence."""
    return {
        "count": len(SUPPORT),
        "rows": [
            {
                "provider": row["provider"],
                "hosts": list(row["hosts"]),
                "mode": row["mode"],
                "confidence": row["confidence"],
                "read_fields": list(row["read_fields"]),
                "write_fields": list(row["write_fields"]),
                "miss_fields": list(row["miss_fields"]),
                "reference": row["reference"],
                "note": row["note"],
            }
            for row in SUPPORT
        ],
        "disclaimer": (
            "expectations with sources, not measurements: a documented claim is what "
            "the provider promises. Which providers were actually called, and what "
            "their usage payloads said, is in MVP/54-prompt-caching.md"
        ),
    }


# --------------------------------------------------------------------------- #
# usage → one honest status                                                     #
# --------------------------------------------------------------------------- #


def _walk(usage: Any, path: str) -> Any:
    """``"prompt_tokens_details.cached_tokens"`` → the value, or ``None``."""
    current: Any = usage
    for part in str(path).split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _lookup(source: Any, name: str) -> Any:
    """The value of ``name``, nested or **flattened** (a gateway's own spelling).

    Some OpenAI-compatible gateways emit the counter as a literal key
    (``"prompt_tokens_details.cached_tokens": 512``) instead of the nested object
    OpenAI sends. Both spellings mean the same thing, so both are read — and the
    flat spelling is what this module's own telemetry writes back onto ``usage``,
    which is why a second read of the same counters must find it again.
    """
    value = _walk(source, name)
    if value is None and isinstance(source, Mapping) and name in source:
        value = source[name]
    return value


def _as_int(value: Any) -> int | None:
    """An integer a provider actually reported. A bool is not a token count."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def unwrap_usage(payload: Any) -> Mapping[str, Any]:
    """Accept a ``usage`` block, a whole answer body, or a chunk — return usage.

    An empty mapping when nothing carries one; the caller then reports
    ``unknown`` instead of inventing three zeros.
    """
    if not isinstance(payload, Mapping):
        return {}
    for key in ("usage", "token_usage"):
        candidate = payload.get(key)
        if isinstance(candidate, Mapping):
            return candidate
    return payload


def cache_counters(usage: Any) -> dict[str, int]:
    """Every cache counter the payload **actually** carries, by its own name.

    Only names from :data:`READ_NAMES` / :data:`WRITE_NAMES` /
    :data:`HIT_NAMES` / :data:`MISS_NAMES` are read, and only when the value is a
    real integer — a nested ``prompt_tokens_details`` without ``cached_tokens``
    contributes nothing (as opposed to a zero it never sent).
    """
    source = unwrap_usage(usage)
    out: dict[str, int] = {}
    for name in (*READ_NAMES, *WRITE_NAMES, *HIT_NAMES, *MISS_NAMES):
        if name in out:
            continue
        value = _as_int(_lookup(source, name))
        if value is not None:
            out[name] = value
    return out


def _first(counters: Mapping[str, int], names: Sequence[str]) -> tuple[str, int] | None:
    for name in names:
        if name in counters:
            return name, int(counters[name])
    return None


@dataclass(frozen=True)
class CacheReport:
    """One request's cache status — as reported by the provider, or ``unknown``.

    ``status`` is derived from counters the provider sent; ``fields`` keeps those
    counters verbatim under their own names so a reader can check the conclusion.
    ``predicted`` (set by the caller that compared prefixes) is a *separate*
    field on purpose: a prediction must never be read as an observation.
    """

    status: str = CACHE_UNKNOWN
    basis: str = "no cache field in the usage payload"
    read_tokens: int | None = None
    write_tokens: int | None = None
    hit_tokens: int | None = None
    miss_tokens: int | None = None
    fields: Mapping[str, int] = field(default_factory=dict)
    provider: str = ""
    mode: str = MODE_NONE
    expected: bool = False
    confidence: str = CONF_UNKNOWN
    reference: str = ""
    predicted: str = CACHE_UNKNOWN
    note: str = ""

    @property
    def observed(self) -> bool:
        """True only when the provider's own payload decided the status."""
        return self.status in (CACHE_HIT, CACHE_MISS)

    @property
    def exempt_tokens(self) -> int | None:
        """Prompt tokens the provider served from its cache (``None`` if unread)."""
        if self.read_tokens is not None:
            return self.read_tokens
        return self.hit_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "observed": self.observed,
            "basis": self.basis,
            "read_tokens": self.read_tokens,
            "write_tokens": self.write_tokens,
            "hit_tokens": self.hit_tokens,
            "miss_tokens": self.miss_tokens,
            "exempt_tokens": self.exempt_tokens,
            "fields": dict(self.fields),
            "provider": self.provider,
            "mode": self.mode,
            "expected": self.expected,
            "confidence": self.confidence,
            "reference": self.reference,
            "predicted": self.predicted,
            "note": self.note,
        }


def cache_report(
    usage: Any,
    strategy: CacheStrategy | None = None,
    *,
    predicted: str = "",
) -> CacheReport:
    """One ``hit`` / ``miss`` / ``unknown`` per request, from the provider's counters.

    The rule, in one place:

    * a cache counter the provider reported with a value **above zero** → ``hit``
      (it says it served that many prompt tokens from the cache);
    * a cache counter present but **zero** → ``miss`` (the provider looked at its
      cache and read nothing; a cold write shows up here as
      ``cache_creation_input_tokens`` above zero with ``cache_read_input_tokens``
      at zero);
    * **no cache counter at all** → ``unknown``. That is the whole honesty rule:
      an absent field is not a miss, and it is never a hit.

    ``strategy`` adds context only (``mode``/``expected``/``reference``) — it
    never changes the status.
    """
    counters = cache_counters(usage)
    strategy = strategy or CacheStrategy(provider="", mode=MODE_NONE)
    common = {
        "provider": strategy.provider,
        "mode": strategy.mode,
        "expected": strategy.expected,
        "confidence": strategy.confidence,
        "reference": strategy.reference,
        "predicted": predicted or CACHE_UNKNOWN,
        "fields": dict(counters),
    }

    if not counters:
        note = strategy.note
        if strategy.expected:
            note = (
                f"{strategy.provider} documents caching, but this answer's usage block "
                f"carried no cache counter ({', '.join(strategy.fields) or 'none declared'}) "
                f"— the status stays unknown, it is not a miss"
            )
        return CacheReport(status=CACHE_UNKNOWN, note=note, **common)

    read = _first(counters, READ_NAMES)
    write = _first(counters, WRITE_NAMES)
    hit = _first(counters, HIT_NAMES)
    miss = _first(counters, MISS_NAMES)
    positives = [pair for pair in (read, hit) if pair is not None and pair[1] > 0]

    if positives:
        name, value = positives[0]
        basis = f"{name}={value} in the usage payload"
        status = CACHE_HIT
    else:
        status = CACHE_MISS
        zeros = [pair[0] for pair in (read, hit) if pair is not None]
        if zeros:
            basis = f"{zeros[0]}=0 in the usage payload (a cache counter the provider did send)"
        elif miss is not None:
            basis = f"{miss[0]}={miss[1]} — the provider counted a cache miss"
        elif write is not None:
            basis = f"{write[0]}={write[1]} — the entry was written, not read"
        else:  # pragma: no cover - every branch above is reachable
            basis = "a cache counter was present and none was above zero"

    return CacheReport(
        status=status,
        basis=basis,
        read_tokens=read[1] if read else None,
        write_tokens=write[1] if write else None,
        hit_tokens=hit[1] if hit else None,
        miss_tokens=miss[1] if miss else None,
        note=strategy.note,
        **common,
    )


def summarize(reports: Iterable[Any]) -> dict[str, Any]:
    """Fold per-request reports into a run's cache picture (counts, never guesses)."""
    counts = dict.fromkeys(CACHE_STATUSES, 0)
    read_tokens = 0
    write_tokens = 0
    hit_tokens = 0
    miss_tokens = 0
    observed = 0
    providers: dict[str, int] = {}
    for item in reports:
        data = item.to_dict() if hasattr(item, "to_dict") else dict(item or {})
        status = str(data.get("status") or CACHE_UNKNOWN)
        status = status if status in counts else CACHE_UNKNOWN
        counts[status] += 1
        if status in (CACHE_HIT, CACHE_MISS):
            observed += 1  # the provider's own payload decided it
        read_tokens += int(data.get("read_tokens") or 0)
        write_tokens += int(data.get("write_tokens") or 0)
        hit_tokens += int(data.get("hit_tokens") or 0)
        miss_tokens += int(data.get("miss_tokens") or 0)
        name = str(data.get("provider") or "")
        providers[name] = providers.get(name, 0) + 1
    total = sum(counts.values())
    return {
        "requests": total,
        "counts": counts,
        "observed": observed,
        "read_tokens": read_tokens,
        "write_tokens": write_tokens,
        "hit_tokens": hit_tokens,
        "miss_tokens": miss_tokens,
        "providers": providers,
        "note": (
            "a request whose usage payload carried no cache counter is counted as "
            "unknown — the provider did not report, which is neither a hit nor a miss"
        ),
    }


# --------------------------------------------------------------------------- #
# the byte-stable prefix                                                        #
# --------------------------------------------------------------------------- #


def canonical_bytes(value: Any) -> bytes:
    """The canonical JSON bytes of a request value (sorted keys, no padding).

    The same rule the composer's fingerprints use: one serialisation, so two
    identical structures can never hash differently by accident.
    """
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")


@dataclass(frozen=True)
class StablePrefix:
    """The part of a request a provider may serve from its cache, as bytes."""

    sha256: str = ""
    byte_count: int = 0
    message_count: int = 0
    tool_count: int = 0
    tools_sha256: str = ""
    _messages: tuple[bytes, ...] = ()
    _tools: bytes = b""

    @property
    def present(self) -> bool:
        return self.byte_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "bytes": self.byte_count,
            "messages": self.message_count,
            "tools": self.tool_count,
            "tools_sha256": self.tools_sha256,
            "present": self.present,
        }


def stable_prefix(
    messages: Sequence[Mapping[str, Any]] | None, tools: Sequence[Mapping[str, Any]] | None = None
) -> StablePrefix:
    """Canonicalise the **leading system message(s) + the tool catalogue**.

    The stable prefix is the run of messages from index 0 whose role is
    ``system`` (the agent loop sends exactly one), plus the ``tools`` array when
    one is offered — both are the same bytes on every step of a conversation,
    which is the precondition both cache families have. Everything after it (the
    user turn, the tool results) is the dynamic tail and is deliberately not
    part of the hash.
    """
    blocks: list[bytes] = []
    for message in messages or ():
        if not isinstance(message, Mapping) or str(message.get("role") or "") != "system":
            break
        blocks.append(canonical_bytes(dict(message)))

    tool_list = [dict(tool) for tool in (tools or ()) if isinstance(tool, Mapping)]
    tools_bytes = canonical_bytes(tool_list) if tool_list else b""
    body = canonical_bytes(
        {"messages": [b.decode("utf-8") for b in blocks], "tools": tool_list}
    ) if (blocks or tool_list) else b""
    return StablePrefix(
        sha256=hashlib.sha256(body).hexdigest() if body else "",
        byte_count=len(body),
        message_count=len(blocks),
        tool_count=len(tool_list),
        tools_sha256=hashlib.sha256(tools_bytes).hexdigest() if tools_bytes else "",
        _messages=tuple(blocks),
        _tools=tools_bytes,
    )


@dataclass(frozen=True)
class PrefixReuse:
    """How much of the previous request this one can reuse — a **prediction**."""

    predicted: str = CACHE_UNKNOWN
    basis: str = "byte-compare"
    common_messages: int = 0
    of_messages: int = 0
    tools_identical: bool = False
    reusable_bytes: int = 0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "predicted": self.predicted,
            "basis": self.basis,
            "common_messages": self.common_messages,
            "of_messages": self.of_messages,
            "tools_identical": self.tools_identical,
            "reusable_bytes": self.reusable_bytes,
            "note": self.note,
        }


def prefix_reuse(previous: StablePrefix | None, current: StablePrefix) -> PrefixReuse:
    """Compare two requests' prefixes and predict ``hit`` / ``miss`` / ``unknown``.

    ``basis: "byte-compare"`` is the point: this is what *should* be reusable,
    computed from the bytes we sent. Only the provider's usage payload may claim
    an observation, and :func:`cache_report` is what reads it.
    """
    if previous is None or not previous.present:
        return PrefixReuse(
            predicted=CACHE_UNKNOWN,
            note="no earlier request in this conversation — there is nothing to reuse yet",
        )
    if not current.present:
        return PrefixReuse(
            predicted=CACHE_UNKNOWN,
            note="this request has no stable prefix (no system message, no tools)",
        )
    if previous.sha256 == current.sha256:
        return PrefixReuse(
            predicted=CACHE_HIT,
            common_messages=current.message_count,
            of_messages=current.message_count,
            tools_identical=True,
            reusable_bytes=current.byte_count,
            note="byte-identical to the previous request's prefix",
        )
    common = 0
    for was, now in zip(previous._messages, current._messages, strict=False):
        if was != now:
            break
        common += 1
    tools_same = bool(previous.tools_sha256) and previous.tools_sha256 == current.tools_sha256
    reusable = sum(len(block) for block in current._messages[:common])
    if tools_same:
        reusable += len(current._tools)
    return PrefixReuse(
        predicted=CACHE_MISS,
        common_messages=common,
        of_messages=current.message_count,
        tools_identical=tools_same,
        reusable_bytes=reusable,
        note=(
            f"{common} of {current.message_count} stable message(s) are byte-identical; "
            + ("the tool catalogue is unchanged" if tools_same else "the tool catalogue moved")
        ),
    )


# --------------------------------------------------------------------------- #
# explicit breakpoints                                                          #
# --------------------------------------------------------------------------- #


class CachePlanError(ValueError):
    """An explicit breakpoint plan that cannot be built as asked."""


@dataclass(frozen=True)
class Breakpoint:
    """One ``cache_control`` marker and the exact place it landed."""

    target: str
    block: str
    index: int
    path: str
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "block": self.block,
            "index": self.index,
            "path": self.path,
            "source": self.source,
        }


@dataclass
class CachePlan:
    """The annotated request plus the breakpoints that were placed on it."""

    mode: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    breakpoints: tuple[Breakpoint, ...] = ()
    skipped: tuple[dict[str, Any], ...] = ()
    prefix: StablePrefix | None = None
    strategy: CacheStrategy | None = None
    note: str = ""

    @property
    def placed(self) -> int:
        return len(self.breakpoints)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "placed": self.placed,
            "breakpoints": [item.to_dict() for item in self.breakpoints],
            "skipped": [dict(item) for item in self.skipped],
            "prefix": self.prefix.to_dict() if self.prefix else None,
            "strategy": self.strategy.to_dict() if self.strategy else None,
            "note": self.note,
        }


def _cache_control(ttl: str = "") -> dict[str, Any]:
    marker: dict[str, Any] = {"type": CACHE_CONTROL_TYPE}
    if ttl:
        if ttl not in CACHE_TTLS:
            raise CachePlanError(
                f"unknown cache ttl {ttl!r} — the documented values are {list(CACHE_TTLS)}"
            )
        marker["ttl"] = ttl
    return marker


def section_blocks(content: str, assembly: Any) -> list[tuple[str, str]] | None:
    """Split a composed prompt into ``(section name, block text)`` pairs.

    Only accepted when the blocks reconstruct ``content`` **byte for byte** — the
    same ``"\\n\\n".join(blocks)`` the composer built it with. Anything else (a
    prompt that did not come from the assembly, a clipped section list) returns
    ``None`` and the caller falls back to a single block. Guessing where the
    sections are would put the marker in the wrong place, which is worse than no
    marker at all.
    """
    if assembly is None or not isinstance(content, str):
        return None
    text = getattr(assembly, "text", None)
    if not isinstance(text, str) or text != content:
        return None
    sections = getattr(assembly, "sections", None)
    if not isinstance(sections, (list, tuple)):
        return None
    blocks: list[tuple[str, str]] = []
    for section in sections:
        if not getattr(section, "present", False):
            continue
        title = str(getattr(section, "title", "") or "")
        body = str(getattr(section, "text", "") or "")
        blocks.append((str(getattr(section, "name", "") or ""), f"## {title}\n\n{body}"))
    if not blocks:
        return None
    if "\n\n".join(block for _, block in blocks) != content:
        return None
    return blocks


def _text_block(text: str, ttl: str = "") -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if ttl:
        block["cache_control"] = _cache_control(ttl)
    return block


def explicit_breakpoints(
    messages: Sequence[Mapping[str, Any]],
    *,
    tools: Sequence[Mapping[str, Any]] | None = None,
    strategy: CacheStrategy | None = None,
    assembly: Any = None,
    system_index: int | None = None,
    context_index: int | None = None,
    ttl: str = "",
    limit: int = MAX_BREAKPOINTS,
) -> CachePlan:
    """Place ``cache_control`` on the last stable block: system, tools, project context.

    Anthropic's semantics are *prefix* semantics: a marker caches everything up
    to and including its position. So the three markers are placed at

    * the **end of the system prompt** (the whole stable head),
    * the **end of the ``project_context`` section** when the composer's
      :class:`~skaldir.prompt_builder.PromptAssembly` was handed in — that is the
      single largest stable section, and splitting the system text into section
      blocks is only done when the split reconstructs the prompt byte for byte,
    * the **last entry of the ``tools`` array**.

    A target that does not exist in this request is reported in ``skipped`` with
    its reason instead of being invented, more than ``limit`` markers raise
    :class:`CachePlanError` (never a silent drop), and the caller's ``messages``
    / ``tools`` are never mutated — the plan carries its own copies.
    """
    strategy = strategy or CacheStrategy(provider="anthropic", mode=MODE_EXPLICIT)
    planned = [dict(message) for message in messages]
    planned_tools = [dict(tool) for tool in (tools or ())]
    placed: list[Breakpoint] = []
    skipped: list[dict[str, Any]] = []

    index = system_index
    if index is None:
        for position in range(len(planned) - 1, -1, -1):
            if str(planned[position].get("role") or "") == "system":
                index = position
                break

    if index is None or not (0 <= int(index) < len(planned)):
        skipped.append(
            {"target": "system", "reason": "no system message in this request"}
        )
    else:
        index = int(index)
        raw_content = planned[index].get("content")
        path = f"messages[{index}].content"
        # the section split is computed once and only for a text system message:
        # a marker placed by a guess is worse than no marker at all
        split = section_blocks(raw_content, assembly) if isinstance(raw_content, str) else None
        blocks: list[dict[str, Any]] = []
        if isinstance(raw_content, str):
            if split is None:
                blocks = [_text_block(raw_content)]
                skipped.append(
                    {
                        "target": "project_context",
                        "reason": (
                            "the system prompt was not split into sections, so there is no "
                            "project-context block to mark — pass the composer's own section "
                            "list (assembly=…, or --assembly on the CLI) to place it; the "
                            "split is only accepted when it rebuilds the prompt byte for byte"
                        ),
                    }
                )
            else:
                blocks = [_text_block(block) for _, block in split]
                path = f"messages[{index}].content[{len(blocks) - 1}]"
        elif isinstance(raw_content, list):
            blocks = [
                copy.deepcopy(dict(item)) for item in raw_content if isinstance(item, Mapping)
            ]
            if not blocks:
                skipped.append(
                    {
                        "target": "system",
                        "reason": "the system message has an empty content block list",
                    }
                )
        else:
            skipped.append(
                {
                    "target": "system",
                    "reason": (
                        "the system message carries no text content "
                        f"({type(raw_content).__name__})"
                    ),
                }
            )
        if blocks:
            target_path = path
            if len(blocks) > 1:
                split_names = [name for name, _ in split or []]
                # the marker goes on the LAST block: everything before it is cached
                project_at = None
                if context_index is not None and 0 <= int(context_index) < len(blocks):
                    project_at = int(context_index)
                elif "project_context" in split_names:
                    project_at = split_names.index("project_context")
                if project_at is not None and project_at != len(blocks) - 1:
                    blocks[project_at]["cache_control"] = _cache_control(ttl)
                    placed.append(
                        Breakpoint(
                            target="project_context",
                            block="message",
                            index=index,
                            path=f"messages[{index}].content[{project_at}]",
                            source="assembly-section-offset",
                        )
                    )
                elif project_at is not None:
                    skipped.append(
                        {
                            "target": "project_context",
                            "reason": (
                                "the project-context section is the last block of the system "
                                "prompt — its marker would sit on the same position as the "
                                "system marker, so it is not duplicated"
                            ),
                        }
                    )
            blocks[-1]["cache_control"] = _cache_control(ttl)
            planned[index] = dict(planned[index])
            planned[index]["content"] = blocks
            placed.append(
                Breakpoint(
                    target="system",
                    block="message",
                    index=index,
                    path=target_path,
                    source="assembly-section-offset" if len(blocks) > 1 else "message",
                )
            )

    if planned_tools:
        planned_tools[-1] = dict(planned_tools[-1])
        planned_tools[-1]["cache_control"] = _cache_control(ttl)
        placed.append(
            Breakpoint(
                target="tools",
                block="tools",
                index=len(planned_tools) - 1,
                path=f"tools[{len(planned_tools) - 1}]",
                source="last-tool",
            )
        )
    else:
        skipped.append({"target": "tools", "reason": "this request offers no tools"})

    if len(placed) > int(limit):
        raise CachePlanError(
            f"{len(placed)} breakpoints requested but the documented limit is {limit} "
            f"({', '.join(item.target for item in placed)}) — drop a target instead of "
            f"silently losing a marker"
        )

    return CachePlan(
        mode=MODE_EXPLICIT,
        messages=planned,
        tools=planned_tools,
        breakpoints=tuple(placed),
        skipped=tuple(skipped),
        prefix=stable_prefix(planned, planned_tools),
        strategy=strategy,
        note=(
            "cache_control markers are placed; this is the explicit family. A "
            "provider that does not pass the field through will refuse the request, "
            "which is why the caller asks for a plan instead of getting one"
        ),
    )


# --------------------------------------------------------------------------- #
# the ledger — the cache-status surface a run reads back                        #
# --------------------------------------------------------------------------- #

#: One JSON line per measured request, under the runtime directory.
LEDGER_FILE = "prompt-cache.jsonl"


def default_ledger_path(runtime: str | Path | None = None) -> Path:
    """``<runtime>/prompt-cache.jsonl`` — the default file the ledger appends to."""
    if runtime is not None:
        return Path(runtime).expanduser() / LEDGER_FILE
    try:
        from .registry import default_runtime_dir

        return Path(default_runtime_dir()) / LEDGER_FILE
    except Exception:  # noqa: BLE001 — a ledger path must resolve without the registry
        return Path("~/.hermes/skaldir").expanduser() / LEDGER_FILE


@dataclass(frozen=True)
class CacheEntry:
    """One request as it was measured: what we predicted, what the provider said."""

    at: float = 0.0
    provider: str = ""
    model: str = ""
    call: str = ""
    status: str = CACHE_UNKNOWN
    mode: str = MODE_NONE
    basis: str = ""
    predicted: str = CACHE_UNKNOWN
    read_tokens: int | None = None
    write_tokens: int | None = None
    #: The DeepSeek-shaped counters, kept beside the read/write pair: a ledger
    #: summary that could not sum them would report "4 hits, 0 hit tokens".
    hit_tokens: int | None = None
    miss_tokens: int | None = None
    fields: Mapping[str, int] = field(default_factory=dict)
    prefix_sha256: str = ""
    prompt_sha256: str = ""
    latency_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "provider": self.provider,
            "model": self.model,
            "call": self.call,
            "status": self.status,
            "mode": self.mode,
            "basis": self.basis,
            "predicted": self.predicted,
            "read_tokens": self.read_tokens,
            "write_tokens": self.write_tokens,
            "hit_tokens": self.hit_tokens,
            "miss_tokens": self.miss_tokens,
            "fields": dict(self.fields),
            "prefix_sha256": self.prefix_sha256,
            "prompt_sha256": self.prompt_sha256,
            "latency_ms": self.latency_ms,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CacheEntry:
        """Rebuild one entry; a malformed line is the caller's problem, not a guess."""
        return cls(
            at=float(data.get("at") or 0.0),
            provider=str(data.get("provider") or ""),
            model=str(data.get("model") or ""),
            call=str(data.get("call") or ""),
            status=str(data.get("status") or CACHE_UNKNOWN),
            mode=str(data.get("mode") or MODE_NONE),
            basis=str(data.get("basis") or ""),
            predicted=str(data.get("predicted") or CACHE_UNKNOWN),
            read_tokens=_as_int(data.get("read_tokens")),
            write_tokens=_as_int(data.get("write_tokens")),
            hit_tokens=_as_int(data.get("hit_tokens")),
            miss_tokens=_as_int(data.get("miss_tokens")),
            fields={
                str(key): int(value)
                for key, value in dict(data.get("fields") or {}).items()
                if _as_int(value) is not None
            },
            prefix_sha256=str(data.get("prefix_sha256") or ""),
            prompt_sha256=str(data.get("prompt_sha256") or ""),
            latency_ms=(None if data.get("latency_ms") is None else float(data["latency_ms"])),
        )


def entry_from_report(
    report: CacheReport,
    *,
    at: float | None = None,
    model: str = "",
    call: str = "",
    prefix_sha256: str = "",
    prompt_sha256: str = "",
    latency_ms: float | None = None,
) -> CacheEntry:
    """One report → one ledger line (``predicted`` keeps the byte-compare verdict)."""
    return CacheEntry(
        at=time.time() if at is None else float(at),
        provider=report.provider,
        model=model,
        call=call,
        status=report.status,
        mode=report.mode,
        basis=report.basis,
        predicted=report.predicted,
        read_tokens=report.read_tokens,
        write_tokens=report.write_tokens,
        hit_tokens=report.hit_tokens,
        miss_tokens=report.miss_tokens,
        fields=dict(report.fields),
        prefix_sha256=prefix_sha256,
        prompt_sha256=prompt_sha256,
        latency_ms=latency_ms,
    )


class CacheLedger:
    """Append-only JSONL of measured requests — what ``prompt cache status`` reads.

    Writing is opt-in (the provider only appends when it was handed a ledger or
    ``SKALDIR_PROMPT_CACHE_LEDGER`` names one), so a plain API call never touches
    the filesystem. Reading never repairs: a line that is not a JSON object is
    reported as a ``bad_line`` count, not silently dropped.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def append(self, entry: CacheEntry | Mapping[str, Any]) -> bool:
        """One line per request; returns ``False`` when the write failed.

        A failed ledger write must never fail the call it describes: the caller
        gets the boolean, the provider logs nothing and keeps the answer.
        """
        data = entry.to_dict() if isinstance(entry, CacheEntry) else dict(entry or {})
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(data, sort_keys=True, default=str) + "\n")
        except OSError:
            return False
        return True

    def entries(self, limit: int = 0) -> tuple[list[CacheEntry], int]:
        """``(entries, bad_line_count)`` — newest last, capped at ``limit`` when > 0."""
        if not self.path.is_file():
            return [], 0
        rows: list[CacheEntry] = []
        bad = 0
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return [], 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if not isinstance(parsed, Mapping):
                bad += 1
                continue
            rows.append(CacheEntry.from_dict(parsed))
        if limit and limit > 0:
            rows = rows[-int(limit) :]
        return rows, bad

    def summary(self) -> dict[str, Any]:
        """The run's cache picture, straight off the ledger."""
        rows, bad = self.entries()
        report = summarize(rows)
        report["ledger"] = str(self.path)
        report["exists"] = self.path.is_file()
        report["bad_lines"] = bad
        predicted = dict.fromkeys(CACHE_STATUSES, 0)
        for row in rows:
            key = row.predicted if row.predicted in predicted else CACHE_UNKNOWN
            predicted[key] += 1
        report["predicted"] = predicted
        report["last"] = rows[-1].to_dict() if rows else None
        report["note"] = (
            "predicted counts come from comparing request prefixes byte by byte; "
            "counts/read_tokens come from the provider's own usage payload. They are "
            "different claims and are reported separately — a prediction is not a hit"
        )
        return report

    def clear(self) -> bool:
        """Remove the ledger (used by tests and by ``--reset``); idempotent."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True
