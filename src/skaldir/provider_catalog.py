"""A small, honest provider catalog and the fallback chain built from it.

Three questions this module answers, and one it refuses to answer:

0. **What do we know about provider X, and how do we know it?** —
   :data:`skaldir.provider_registry_data.REGISTRY` is the breadth catalog (16
   endpoints: the big commercial APIs, the open-model providers and the four
   local servers). Each of its claims is ``verified`` (a live probe we ran),
   ``documented`` (an official page — with a flag saying whether this round
   actually fetched it) or ``unverified``. It is re-exported here so one module
   still answers "what do we know". ``skaldir provider catalog`` prints it.

1. **What do we know about a *chain* provider?** — :data:`CATALOG` is a short,
   curated list of OpenAI-compatible endpoints (plus one honest "not compatible"
   entry). Every capability is a :class:`Claim`: a value (``yes``/``no``/
   ``likely``/``unknown``/``per_model``), a **source** (``documented`` +
   reference URL, or ``unknown``), a note — and, since the DeepSeek correction
   below, an optional ``measured`` field carrying what a **live probe actually
   returned**. A documented promise is still not a measurement: the source says
   where an expectation came from, ``measured`` says what we saw, and neither
   flips the other.

2. **Which candidate answers a run?** — :func:`build_chain` turns a list of

   candidates into an ordered :class:`ChainStep` list where every step is
   either ``ready`` or ``skipped`` **with the reason** (no credential, no
   base_url/model, the client cannot speak to this kind, a required feature the
   catalog says ``no``). :func:`run_chain` then walks the ready ones and records
   which candidate actually answered — and, for the ones that failed at runtime,
   the reason (rate limit, timeout, auth, a refused ``response_format`` or a
   validation failure). A run can therefore say truthfully which candidate
   answered and why the others did not.

   A candidate is never *invented*: :func:`auto_chain` only offers the
   configured provider (from the provider store / ``godsv.llm`` projection) and
   catalog entries, and each unconfigured catalog entry appears with its own
   skip reason instead of being quietly dropped — that list *is* the honest
   answer to "why did it not use OpenRouter?".
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from . import llm_provider as lp
from . import structured as st
from .env import expand
from .llm import LLMError
from .provider_registry_data import (
    CHAIN_OVERLAP,
    FETCHED_DOCS,
    PROBE_DATE,
    PROBE_DATE_PREVIOUS,
    PROBE_LOG,
    PROBE_NOT_RUN,
    PROBE_PARTIAL,
    PROBE_RUN,
    REGISTRY,
    REGISTRY_ORDER,
    UNVERIFIED,
    VERIFICATION_ORDER,
    VERIFIED,
    ProviderSpec,
    probe_report,
    registry_entry,
    registry_report,
)

# --------------------------------------------------------------------------- #
# vocabulary                                                                   #
# --------------------------------------------------------------------------- #

SOURCE_CONFIG = "config"
SOURCE_CATALOG = "catalog"
SOURCE_EXPLICIT = "explicit"
CANDIDATE_SOURCES = (SOURCE_CONFIG, SOURCE_CATALOG, SOURCE_EXPLICIT)

STATUS_READY = "ready"
STATUS_SKIPPED = "skipped"
STATUS_ANSWERED = "answered"
STATUS_FAILED = "failed"

#: skip reasons — the same vocabulary :mod:`skaldir.structured` reports, plus the
#: ones only a candidate list can produce. A reason is never a guess.
SKIP_MISSING_CREDENTIAL = "missing_credential"
SKIP_NO_BASE_URL = "no_base_url"
SKIP_NO_MODEL = "no_model"
SKIP_NOT_CONFIGURED = "not_configured"
SKIP_DISABLED = "disabled"
SKIP_CLIENT_UNSUPPORTED = "client_cannot_speak_to_kind"
SKIP_UNSUPPORTED = st.REASON_UNSUPPORTED
SKIP_INVALID = st.REASON_INVALID
SKIP_RATE_LIMIT = st.REASON_RATE_LIMIT
SKIP_TIMEOUT = st.REASON_TIMEOUT
SKIP_AUTH = st.REASON_AUTH
SKIP_PROVIDER_ERROR = st.REASON_PROVIDER_ERROR

SKIP_REASONS: tuple[str, ...] = (
    SKIP_MISSING_CREDENTIAL,
    SKIP_NO_BASE_URL,
    SKIP_NO_MODEL,
    SKIP_NOT_CONFIGURED,
    SKIP_DISABLED,
    SKIP_CLIENT_UNSUPPORTED,
    SKIP_UNSUPPORTED,
    SKIP_INVALID,
    SKIP_RATE_LIMIT,
    SKIP_TIMEOUT,
    SKIP_AUTH,
    SKIP_PROVIDER_ERROR,
)

#: features a chain can require of a candidate.
FEATURE_STRUCTURED = "structured"
FEATURE_TOOLS = "tools"
FEATURE_VISION = "vision"
FEATURES: tuple[str, ...] = (FEATURE_STRUCTURED, FEATURE_TOOLS, FEATURE_VISION)

#: claim values. ``per_model`` and ``unknown`` are honest answers, not gaps to
#: be filled in by guessing.
CLAIM_YES = "yes"
CLAIM_NO = "no"
CLAIM_LIKELY = "likely"
CLAIM_PER_MODEL = "per_model"
CLAIM_UNKNOWN = "unknown"
CLAIM_VALUES: tuple[str, ...] = (CLAIM_YES, CLAIM_NO, CLAIM_LIKELY, CLAIM_PER_MODEL, CLAIM_UNKNOWN)

#: claim sources.
SRC_DOCUMENTED = "documented"
SRC_UNKNOWN = "unknown"

KIND_OPENAI_COMPATIBLE = "openai-compatible"
KIND_NOT_COMPATIBLE = "not-openai-compatible"

#: env var naming the model for a catalog entry without a documented default.
ENV_MODEL_FALLBACKS = ("SKALDIR_LLM_MODEL", "OPENAI_MODEL")

#: How a model id reads in the probe log — the shape DeepSeek/OpenRouter ids have
#: (``deepseek-flash``, ``nex-agi/nex-n2.5-mini:free``).
_MODEL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")

CATALOG_ORDER: tuple[str, ...] = ("deepseek", "openrouter", "openai", "local", "anthropic")


# --------------------------------------------------------------------------- #
# the catalog                                                                  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Claim:
    """What we know about one feature of one provider — with where it came from.

    ``value``/``source`` are the *expectation*. ``measured`` is a separate,
    optional field for what a **live probe actually returned** — it exists
    because DeepSeek's documented ceiling (``json_object``) and its measured
    refusal of native ``json_schema`` are two different facts, and collapsing
    them into one field would hide one of them.
    """

    value: str
    source: str
    reference: str = ""
    note: str = ""
    measured: str = ""

    def __post_init__(self) -> None:
        if self.value not in CLAIM_VALUES:
            raise ValueError(f"unknown claim value {self.value!r} (expected {list(CLAIM_VALUES)})")

    @property
    def blocks(self) -> bool:
        """True when this claim is a documented *no* — the only value that skips."""
        return self.value == CLAIM_NO and self.source == SRC_DOCUMENTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "source": self.source,
            "reference": self.reference,
            "note": self.note,
            "measured": self.measured,
        }

    def describe(self) -> str:
        tail = f" ({self.reference})" if self.reference else ""
        extra = f" — {self.note}" if self.note else ""
        seen = f" [measured: {self.measured}]" if self.measured else ""
        return f"{self.value} [{self.source}]{tail}{extra}{seen}"


@dataclass(frozen=True)
class CatalogEntry:
    """One provider we know something about. Claims are expectations, never verdicts."""

    id: str
    label: str
    kind: str
    base_url: str
    api_key_env: tuple[str, ...] = ()
    default_model: str = ""
    models: tuple[str, ...] = ()
    features: Mapping[str, Claim] = field(default_factory=dict)
    docs: str = ""
    notes: str = ""

    def claim(self, feature: str) -> Claim:
        found = self.features.get(feature)
        if found is not None:
            return found
        return Claim(CLAIM_UNKNOWN, SRC_UNKNOWN, note="not catalogued for this provider")

    def model_for(self, env: Mapping[str, str] | None = None) -> str:
        """The model to offer: the documented default, else ``$SKALDIR_LLM_MODEL``.

        The lookup goes through :func:`skaldir.env.expand`, so the pre-rename
        spelling (``GODSV_LLM_MODEL``) is honoured exactly like the primary one —
        and a third-party name (``OPENAI_MODEL``) is left alone.
        """
        if self.default_model:
            return self.default_model
        lookup = env or {}
        for name in expand(ENV_MODEL_FALLBACKS):
            value = str(lookup.get(name) or "").strip()
            if value:
                return value
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "base_url": self.base_url,
            "api_key_env": list(self.api_key_env),
            "default_model": self.default_model,
            "models": list(self.models),
            "features": {name: self.claim(name).to_dict() for name in FEATURES},
            "docs": self.docs,
            "notes": self.notes,
        }


def _documented(value: str, reference: str, note: str = "") -> Claim:
    return Claim(value, SRC_DOCUMENTED, reference=reference, note=note)


def _unknown(note: str) -> Claim:
    return Claim(CLAIM_UNKNOWN, SRC_UNKNOWN, note=note)


#: The catalog. **Expectations with sources — not measurements.** Every
#: documented claim carries the page it comes from; anything not documented says
#: so, and ``skaldir provider test --structured`` is what measures.
CATALOG: dict[str, CatalogEntry] = {
    "deepseek": CatalogEntry(
        id="deepseek",
        label="DeepSeek",
        kind=KIND_OPENAI_COMPATIBLE,
        base_url="https://api.deepseek.com/v1",
        api_key_env=("DEEPSEEK_API_KEY",),
        default_model="deepseek-flash",
        models=("deepseek-flash", "deepseek-v4-pro"),
        features={
            FEATURE_STRUCTURED: Claim(
                CLAIM_LIKELY,
                SRC_DOCUMENTED,
                reference="https://api-docs.deepseek.com/guides/json_mode/",
                note="the docs document response_format={type: json_object}; a native "
                "json_schema form is NOT documented — json_object is the documented "
                "ceiling, so the value stays `likely` (structured output IS reachable "
                "here) while the measured refusal below records that the native form "
                "is not",
                measured=(
                    "2026-09-18 probe: response_format={type: json_schema, strict: true} "
                    'answered HTTP 400 {"error":{"message":"This response_format type is '
                    'unavailable now"}}; response_format={type: json_object} answered 200. '
                    "A catalog claim that promises the native form for DeepSeek is WRONG — "
                    "this field is the correction, and skaldir.provider_registry_data "
                    "carries it as verified/no so the tool reports it."
                ),
            ),
            FEATURE_TOOLS: _documented(
                CLAIM_YES,
                "https://api-docs.deepseek.com/guides/function_calling",
                "function calling is documented for both chat models",
            ),
            FEATURE_VISION: _unknown(
                "the name-based expectation lives in llm_provider.vision_catalog; "
                "deepseek-chat is on the text-only list there and still read a real "
                "image once — measure it with `skaldir vision capabilities`",
            ),
        },
        docs="https://api-docs.deepseek.com/",
        notes=(
            "OpenAI-compatible /chat/completions; the owner's measured default endpoint. The "
            "model ids are the ones the 2026-09-19 probe saw on the wire: GET /models listed "
            "exactly deepseek-flash and deepseek-v4-pro. The older ids (deepseek-chat, "
            "deepseek-reasoner) still ANSWER, but they ECHO back as `deepseek-flash` — they are "
            "aliases that resolve, not the ids the endpoint now serves, which is why they are no "
            "longer offered here (the drift guard fails an id the probe did not observe)"
        ),
    ),
    "openrouter": CatalogEntry(
        id="openrouter",
        label="OpenRouter",
        kind=KIND_OPENAI_COMPATIBLE,
        base_url="https://openrouter.ai/api/v1",
        api_key_env=("OPENROUTER_API_KEY",),
        default_model="",
        models=(),
        features={
            FEATURE_STRUCTURED: _documented(
                CLAIM_PER_MODEL,
                "https://openrouter.ai/docs/guides/features/structured-outputs",
                "response_format={type: json_schema, json_schema: {name, strict, "
                "schema}} is documented, but support is decided per provider "
                "endpoint, not per model — a refused request is expected on some "
                "routes and the fallback mode is the honest answer",
            ),
            FEATURE_TOOLS: _documented(
                CLAIM_PER_MODEL,
                "https://openrouter.ai/docs/api_reference/overview",
                "tools/tool_choice are passed through per provider",
            ),
            FEATURE_VISION: _unknown("depends on the routed model, not on OpenRouter"),
        },
        docs="https://openrouter.ai/docs",
        notes="set the model explicitly (provider/model); an empty model is skipped",
    ),
    "openai": CatalogEntry(
        id="openai",
        label="OpenAI",
        kind=KIND_OPENAI_COMPATIBLE,
        base_url="https://api.openai.com/v1",
        api_key_env=("OPENAI_API_KEY",),
        default_model="gpt-4o-mini",
        models=("gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"),
        features={
            FEATURE_STRUCTURED: _documented(
                CLAIM_YES,
                "https://platform.openai.com/docs/guides/structured-outputs",
                "response_format json_schema with strict:true is documented",
            ),
            FEATURE_TOOLS: _documented(
                CLAIM_YES, "https://platform.openai.com/docs/guides/function-calling"
            ),
            FEATURE_VISION: _documented(
                CLAIM_YES, "https://platform.openai.com/docs/guides/vision"
            ),
        },
        docs="https://platform.openai.com/docs",
        notes="the reference implementation of the wire shape we speak",
    ),
    "local": CatalogEntry(
        id="local",
        label="local OpenAI-compatible server",
        kind=KIND_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:8080/v1",
        api_key_env=(),
        default_model="",
        models=(),
        features={
            FEATURE_STRUCTURED: _unknown(
                "llama.cpp / vLLM / Ollama builds differ: some implement guided "
                "decoding behind response_format, others behind extra_body "
                "(guided_json) — measure it with `skaldir provider test --structured`",
            ),
            FEATURE_TOOLS: _unknown("depends on the server build and the template"),
            FEATURE_VISION: _unknown("depends on the served multimodal projection"),
        },
        docs="",
        notes=(
            "no credential is required; the served model name is server-specific, so "
            "this candidate is skipped until a model is configured"
        ),
    ),
    "anthropic": CatalogEntry(
        id="anthropic",
        label="Anthropic (native API)",
        kind=KIND_NOT_COMPATIBLE,
        base_url="https://api.anthropic.com/v1",
        api_key_env=("ANTHROPIC_API_KEY",),
        default_model="",
        features={
            FEATURE_STRUCTURED: _documented(
                CLAIM_NO,
                "https://docs.anthropic.com/en/api/messages",
                "the native Messages API has no OpenAI response_format field; a "
                "different wire shape would be needed, and this client cannot "
                "speak it — the catalog says so instead of failing at runtime",
            ),
        },
        docs="https://docs.anthropic.com/",
        notes="listed so the chain can say WHY it skips it, not to pretend support",
    ),
}


def _refresh_model_catalog() -> None:
    """Share exact IDs with the picker, without upgrading historical capability claims."""
    from .provider_model_data import PROVIDERS

    for key, data in PROVIDERS.items():
        models = tuple(data["models"])
        values = dict(label=data["label"], base_url=data["base_url"],
                      default_model=models[0] if models else "", models=models,
                      docs=data["sources"][0], notes=data["notes"])
        if key in CATALOG:
            CATALOG[key] = replace(CATALOG[key], **values)
        else:
            CATALOG[key] = CatalogEntry(
                id=key, kind=KIND_OPENAI_COMPATIBLE,
                api_key_env=(data["api_key_env"],) if data["api_key_env"] else (), **values,
            )


_refresh_model_catalog()
CATALOG_ORDER = tuple(CATALOG)


def catalog_entry(entry_id: str) -> CatalogEntry:
    """One entry by id, or ``KeyError`` naming the known ids."""
    key = str(entry_id or "").strip().lower()
    if key not in CATALOG:
        raise KeyError(f"unknown catalog entry {entry_id!r} (known: {list(CATALOG_ORDER)})")
    return CATALOG[key]


# --------------------------------------------------------------------------- #
# the breadth registry (provider_registry_data) — bridged, not duplicated       #
# --------------------------------------------------------------------------- #


def candidate_from_registry(entry_id: str) -> Candidate:
    """A :class:`Candidate` for one registry entry (its primary OpenAI-shaped URL).

    The bridge exists so the breadth table is *usable* by the chain instead of
    being a document: the registry owns the URLs, this turns one into the object
    :func:`build_chain` already understands. A registry entry whose primary
    endpoint is not OpenAI-compatible keeps that kind, so the chain skips it with
    ``client_cannot_speak_to_kind`` rather than building a request that cannot
    work.

    The model is the registry's documented default, never the environment: an
    entry with no documented default has no model, and the chain says ``no_model``
    out loud instead of borrowing another provider's model name.
    """
    spec: ProviderSpec = registry_entry(entry_id)
    endpoint = spec.primary_endpoint
    return Candidate(
        id=spec.id,
        label=spec.label,
        base_url=endpoint.base_url,
        model=spec.default_model,
        kind=KIND_OPENAI_COMPATIBLE if endpoint.openai_compatible else KIND_NOT_COMPATIBLE,
        api_key_env=endpoint.api_key_env,
        features={},
        source=SOURCE_CATALOG,
        notes=f"{spec.note} — {endpoint.label} (registry: {spec.verification})"
        if spec.note
        else endpoint.label,
    )


def registry_chain_entries() -> list[Candidate]:
    """Every registry entry id as a candidate, in :data:`REGISTRY_ORDER`.

    Candidates are not filtered here: an entry with no credential or no model is
    offered anyway and :func:`build_chain` reports the reason, which is the whole
    point of an honest chain.
    """
    return [candidate_from_registry(entry_id) for entry_id in REGISTRY_ORDER]


def _listed_model_ids(evidence: str) -> tuple[list[str], int]:
    """``(ids, claimed_count)`` from a ``GET /models`` step's own evidence text.

    The log states how many ids the endpoint listed (``2 model ids listed — a, b``);
    this reads that number and the names beside it, so the caller can tell
    "the probe listed none" from "the probe listed some and this reader could not
    read them" — the second is a gap in the reading, not a fact about the endpoint.
    """
    marker = "model ids listed"
    index = evidence.find(marker)
    if index < 0:
        return [], 0
    head = evidence[max(0, index - 12) : index]
    counts = re.findall(r"(\d+)", head)
    claimed = int(counts[-1]) if counts else 0
    tail = evidence[index + len(marker) :].split("(")[0]
    ids = [token for token in _MODEL_ID_RE.findall(tail) if any(c.isalpha() for c in token)]
    return ids, claimed


def _asked_model_id(step_name: str) -> str:
    found = re.search(r"model=([A-Za-z0-9._:/-]+)", str(step_name))
    return found.group(1) if found else ""


def _echoed_model_id(evidence: str) -> str:
    """The id a probe's evidence records the endpoint *echoing back*, if any."""
    found = re.search(r'model echoed\s*"?([A-Za-z0-9._:/-]+)"?', evidence)
    return found.group(1) if found else ""


def observed_model_ids(entry_id: str) -> dict[str, Any]:
    """The model ids a **live probe actually saw** for one provider, with its evidence.

    Two measured facts are read out of :data:`PROBE_LOG`, verbatim:

    * a ``GET /models`` step that lists the ids the endpoint served;
    * every step that asked for an id and whose evidence records what came back
      (``model echoed "deepseek-flash"``) — an id that resolves to another id is an
      alias, not the id the endpoint serves.

    ``ids`` is empty when the probe named none, and ``reason`` says which of the two
    gaps it is: nothing was measured, or the measurement is not readable as ids here
    (a counted-but-unnamed ``/models`` list). A caller must not read ``ids == []`` as
    "the provider serves nothing".
    """
    provider = str(entry_id or "").strip().lower()
    steps = [step for step in PROBE_LOG if str(step.provider) == provider]
    listed: list[str] = []
    claimed = 0
    echoes: dict[str, str] = {}
    evidence: list[dict[str, Any]] = []
    for step in steps:
        text = str(step.evidence or "")
        if "GET /models" in str(step.step):
            found, count = _listed_model_ids(text)
            if found:
                listed = found
                claimed = count
                evidence.append(
                    {
                        "step": str(step.step),
                        "date": str(step.date or PROBE_DATE_PREVIOUS),
                        "evidence": text,
                    }
                )
        asked = _asked_model_id(str(step.step))
        echoed = _echoed_model_id(text)
        if asked and echoed:
            echoes[asked] = echoed
    ids = sorted({*listed, *(echoes.values() if echoes else ())}) if (listed or echoes) else []
    reason = ""
    if not steps:
        reason = f"no probe step for {provider!r} in the log — nothing was measured"
    elif not ids:
        reason = (
            "the probe named no model id for this provider in a form this reader can quote "
            f"(a GET /models step counted {claimed} id(s) without naming them, and no chat step "
            "recorded an echoed id)"
            if claimed
            else "the probe's steps named no model id for this provider"
        )
    return {
        "id": provider,
        "ids": ids,
        "listed": listed,
        "echoes": echoes,
        "evidence": evidence,
        "probed": bool(steps),
        "reason": reason,
    }


def catalog_model_ids(entry: CatalogEntry) -> list[str]:
    """The model ids one catalog entry offers, in order and without repeats."""
    ids: list[str] = []
    for value in (entry.default_model, *entry.models):
        text = str(value or "").strip()
        if text and text not in ids:
            ids.append(text)
    return ids


def model_id_report(catalog: Mapping[str, CatalogEntry] | None = None) -> dict[str, Any]:
    """Do the model ids the chain offers match the ids a live probe saw? — the other guard.

    The endpoint used to be compared only on its URL, so a catalog entry could keep
    offering a model id the provider had stopped serving and nothing failed. This
    compares every id a shared catalog entry OFFERS with the ids the probe log
    records the endpoint SERVING (:func:`observed_model_ids`), and reports a
    ``model_ids`` conflict — with the probe step, its date and its evidence — when an
    offered id was not observed.

    Two honest answers are not failures: a provider whose credential was missing has
    no probe step at all, and a ``/models`` list that was counted but not named
    cannot be compared. Both are reported in ``unchecked`` with the reason, so a
    reader can tell "checked and equal" from "not checked".
    """
    table = catalog if catalog is not None else CATALOG
    conflicts: list[dict[str, Any]] = []
    compared: list[str] = []
    unchecked: list[dict[str, Any]] = []
    observed: dict[str, list[str]] = {}
    for entry_id in CHAIN_OVERLAP:
        entry = table.get(entry_id)
        if entry is None:
            continue
        offered = catalog_model_ids(entry)
        if not offered:
            continue
        probe = observed_model_ids(entry_id)
        observed[entry_id] = probe["ids"]
        if not probe["ids"]:
            unchecked.append({"id": entry_id, "offered": offered, "reason": probe["reason"]})
            continue
        compared.append(entry_id)
        unknown = [model for model in offered if model not in probe["ids"]]
        if not unknown:
            continue
        step = (probe["evidence"] or [{}])[0]
        conflicts.append(
            {
                "id": entry_id,
                "field": "model_ids",
                "source": "probe",
                "chain": ", ".join(offered),
                "registry": ", ".join(probe["ids"]),
                "offered_but_not_observed": unknown,
                "observed_ids": list(probe["ids"]),
                "probe_step": str(step.get("step") or ""),
                "probe_date": str(step.get("date") or ""),
                "probe_evidence": str(step.get("evidence") or ""),
            }
        )
    return {
        "compared": compared,
        "conflicts": conflicts,
        "unchecked": unchecked,
        "observed": observed,
        "agree": not conflicts,
        "note": (
            "a model id a catalog entry offers must be an id a live probe actually observed the "
            "endpoint serving; an id the provider has moved on from is a failure, not a warning, "
            "because a client that asks for it gets an answer from a different model"
        ),
    }


def drift_report(catalog: Mapping[str, CatalogEntry] | None = None) -> dict[str, Any]:
    """Do the two tables agree — and do the ids we offer match what the probe measured?

    :data:`CATALOG` is the curated chain core and the registry is the breadth
    table; both name endpoints, so they must not disagree about one they share.
    Only the ids in :data:`CHAIN_OVERLAP` are compared — the chain's ``local``
    entry is deliberately a generic loopback default rather than any of the four
    named local servers.

    A shared id is also compared on its **model ids** against :data:`PROBE_LOG`
    (:func:`model_id_report`): a catalog that keeps offering ``deepseek-chat``
    while the endpoint's ``/models`` lists only ``deepseek-flash`` was a gap this
    guard did not read before, and it is the gap the 2026-09-19 probe measured.
    Those conflicts land in the same ``conflicts`` list (so ``agree`` is False and
    the CLI's drift warning fires) and carry the probe step they rest on.

    ``catalog`` is the table to check; the default is the live one, and a caller
    may pass a prepared table to test the guard itself.
    """
    table = catalog if catalog is not None else CATALOG
    conflicts: list[dict[str, str]] = []
    compared: list[str] = []
    for entry_id in CHAIN_OVERLAP:
        if entry_id not in table or entry_id not in REGISTRY:
            continue
        compared.append(entry_id)
        chain_entry = table[entry_id]
        spec = REGISTRY[entry_id]
        endpoint = spec.primary_endpoint
        if chain_entry.base_url.rstrip("/") != endpoint.base_url.rstrip("/"):
            conflicts.append(
                {
                    "id": entry_id,
                    "field": "base_url",
                    "chain": chain_entry.base_url,
                    "registry": endpoint.base_url,
                }
            )
        if tuple(chain_entry.api_key_env) != tuple(endpoint.api_key_env[:1]):
            conflicts.append(
                {
                    "id": entry_id,
                    "field": "api_key_env",
                    "chain": ", ".join(chain_entry.api_key_env),
                    "registry": ", ".join(endpoint.api_key_env),
                }
            )
    models = model_id_report(table)
    conflicts.extend(models["conflicts"])
    return {
        "compared": compared,
        "conflicts": conflicts,
        "agree": not conflicts,
        "model_ids": models,
        "note": (
            "the registry is the breadth table and the chain catalog is the curated core; a "
            "shared id must name the same endpoint in both, and — since the model-id drift the "
            "2026-09-19 probe measured — the ids a curated entry offers must be ids the probe "
            "observed the endpoint serving. This is the check on both."
        ),
    }


def provider_catalog_report(
    *, entry_id: str = "", verification: str = "", include_chain: bool = True
) -> dict[str, Any]:
    """The document ``skaldir provider catalog`` prints: the breadth registry.

    Carries the registry entries, the live-probe log, the drift check against the
    chain catalog and the chain itself — one JSON document, every claim with its
    evidence, and no claim upgraded because a neighbouring field was measured.
    """
    report = registry_report(entry_id=entry_id, verification=verification)
    report["drift"] = drift_report()
    if include_chain and not entry_id:
        try:
            steps = auto_chain(require=(FEATURE_STRUCTURED,))
            report["chain"] = chain_report(steps)
        except Exception as exc:  # noqa: BLE001 — the chain is context, not the catalog
            report["chain"] = {"error": f"{type(exc).__name__}: {exc}"}
    return report


def catalog_report() -> dict[str, Any]:
    """The catalog as JSON — every claim with its source, sorted by catalog order."""
    return {
        "count": len(CATALOG_ORDER),
        "order": list(CATALOG_ORDER),
        "entries": [CATALOG[entry_id].to_dict() for entry_id in CATALOG_ORDER],
        "disclaimer": (
            "expectations with sources, not measurements: a documented claim is what "
            "the provider promises; `skaldir provider test --structured` is what measures"
        ),
    }


# --------------------------------------------------------------------------- #
# candidates and the chain                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class Candidate:
    """One provider/model a run could use, and where the idea came from."""

    id: str
    base_url: str
    model: str = ""
    label: str = ""
    kind: str = KIND_OPENAI_COMPATIBLE
    api_key_env: tuple[str, ...] = ()
    tiers: Mapping[str, str] = field(default_factory=dict)
    features: Mapping[str, Claim] = field(default_factory=dict)
    source: str = SOURCE_CATALOG
    notes: str = ""
    disabled: bool = False

    def claim(self, feature: str) -> Claim:
        found = self.features.get(feature)
        if found is not None:
            return found
        return Claim(CLAIM_UNKNOWN, SRC_UNKNOWN, note="not catalogued for this candidate")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label or self.id,
            "base_url": self.base_url,
            "model": self.model,
            "kind": self.kind,
            "api_key_env": list(self.api_key_env),
            "tiers": dict(self.tiers),
            "source": self.source,
            "features": {name: self.claim(name).to_dict() for name in FEATURES},
            "notes": self.notes,
            "disabled": self.disabled,
        }


def candidate_from_catalog(entry_id: str, *, env: Mapping[str, str] | None = None) -> Candidate:
    """A :class:`Candidate` for one catalog entry (model from env when unset)."""
    entry = catalog_entry(entry_id)
    lookup = env or {}
    model = entry.model_for(lookup)
    return Candidate(
        id=entry.id,
        label=entry.label,
        base_url=entry.base_url,
        model=model,
        kind=entry.kind,
        api_key_env=entry.api_key_env,
        features=dict(entry.features),
        source=SOURCE_CATALOG,
        notes=entry.notes,
    )


def credential_state(candidate: Candidate, env: Mapping[str, str] | None = None) -> str:
    """``set`` / ``unset`` / ``not_required`` / ``undeclared`` — never a value.

    ``not_required`` means a **local** endpoint (nothing to authenticate);
    ``undeclared`` means the candidate names no variable at all while pointing
    at a remote endpoint — a legitimate setup (a proxy that injects the key, an
    unauthenticated gateway) that will fail with 401 if the endpoint wants one,
    which is why :func:`build_chain` reports it instead of guessing.
    """
    lookup = env or {}
    if not candidate.api_key_env:
        return "not_required" if lp.is_local_base_url(candidate.base_url) else "undeclared"
    for name in candidate.api_key_env:
        if str(lookup.get(name) or "").strip():
            return "set"
    return "unset"


@dataclass
class ChainStep:
    """One candidate in the ordered chain, with its verdict and the reason."""

    index: int
    candidate: Candidate
    status: str = STATUS_READY
    reason: str = ""
    detail: str = ""
    credential: str = ""
    claim_notes: list[str] = field(default_factory=list)
    attempts: int = 0

    @property
    def id(self) -> str:
        return self.candidate.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "id": self.candidate.id,
            "label": self.candidate.label or self.candidate.id,
            "base_url": self.candidate.base_url,
            "model": self.candidate.model,
            "source": self.candidate.source,
            "status": self.status,
            "reason": self.reason,
            "detail": self.detail,
            "credential": self.credential,
            "claim_notes": list(self.claim_notes),
            "attempts": self.attempts,
        }


def _unverified_notes(candidate: Candidate, require: Sequence[str]) -> list[str]:
    """Claims that are *not* a documented yes — reported, never treated as a skip."""
    notes: list[str] = []
    for feature in require:
        claim = candidate.claim(feature)
        if claim.value == CLAIM_YES and claim.source == SRC_DOCUMENTED:
            continue
        notes.append(f"{feature}: {claim.describe()}")
    return notes


def build_chain(
    candidates: Iterable[Candidate],
    *,
    env: Mapping[str, str] | None = None,
    require: Sequence[str] = (),
) -> list[ChainStep]:
    """Order the candidates and say, per candidate, why it is usable or not.

    A candidate is **skipped** only for a fact: a kind this client cannot speak,
    a missing ``base_url``/``model``, a credential that is not in the
    environment, an explicit ``disabled`` flag, or a required feature the catalog
    documents as ``no``. A claim that is merely *unverified* (``likely`` /
    ``per_model`` / ``unknown``) keeps the candidate **ready** and is reported in
    ``claim_notes`` — an expectation must not remove a candidate that a real call
    could prove, and it must not silently pass as a capability either.
    """
    lookup = env if env is not None else _os_environ()
    unknown = [name for name in require if name not in FEATURES]
    if unknown:
        raise ValueError(f"unknown required feature(s) {unknown!r} (known: {list(FEATURES)})")
    steps: list[ChainStep] = []
    for position, candidate in enumerate(candidates, start=1):
        step = ChainStep(index=position, candidate=candidate)
        step.credential = credential_state(candidate, lookup)
        step.claim_notes = _unverified_notes(candidate, require)
        if candidate.disabled:
            step.status, step.reason = STATUS_SKIPPED, SKIP_DISABLED
            step.detail = "the candidate is explicitly disabled"
        elif candidate.kind != KIND_OPENAI_COMPATIBLE:
            step.status, step.reason = STATUS_SKIPPED, SKIP_CLIENT_UNSUPPORTED
            step.detail = (
                f"kind {candidate.kind!r} is not the OpenAI-compatible wire shape this "
                f"client speaks — no request would be attempted"
            )
        elif not str(candidate.base_url or "").strip():
            step.status, step.reason = STATUS_SKIPPED, SKIP_NO_BASE_URL
            step.detail = "no base_url configured"
        elif not str(candidate.model or "").strip():
            step.status, step.reason = STATUS_SKIPPED, SKIP_NO_MODEL
            step.detail = (
                "no model configured — set one on the candidate (env "
                f"{' / '.join(ENV_MODEL_FALLBACKS)} for a catalog entry)"
            )
        elif step.credential == "unset" and not lp.is_local_base_url(candidate.base_url):
            step.status, step.reason = STATUS_SKIPPED, SKIP_MISSING_CREDENTIAL
            step.detail = (
                "none of " + ", ".join(candidate.api_key_env) + " is set in the environment"
            )
        else:
            for feature in require:
                claim = candidate.claim(feature)
                if claim.blocks:
                    step.status, step.reason = STATUS_SKIPPED, SKIP_UNSUPPORTED
                    step.detail = (
                        f"required feature {feature!r} is documented as unavailable: "
                        f"{claim.describe()}"
                    )
                    break
            if step.status == STATUS_READY and step.credential != "set":
                step.detail = {
                    "unset": "no credential required for a local endpoint",
                    "not_required": "this candidate declares no credential (local endpoint)",
                    "undeclared": (
                        "no credential variable is declared — the call will fail with 401 "
                        "if this endpoint requires one"
                    ),
                }.get(step.credential, "")
        steps.append(step)
    return steps


def _os_environ() -> dict[str, str]:
    import os  # noqa: PLC0415 — only needed when no env mapping is passed

    return {str(k): str(v) for k, v in os.environ.items()}


def chain_report(steps: Sequence[ChainStep]) -> dict[str, Any]:
    """The chain as JSON: the order, who is ready, who was skipped and why."""
    ready = [step for step in steps if step.status == STATUS_READY]
    skipped = [step for step in steps if step.status != STATUS_READY]
    return {
        "count": len(steps),
        "ready": [step.id for step in ready],
        "ready_count": len(ready),
        "skipped": [
            {"id": step.id, "reason": step.reason, "detail": step.detail} for step in skipped
        ],
        "order": [step.id for step in steps],
        "steps": [step.to_dict() for step in steps],
        "first": ready[0].id if ready else "",
    }


def auto_chain(
    *,
    env: Mapping[str, str] | None = None,
    require: Sequence[str] = (FEATURE_STRUCTURED,),
    config_path: str | Any | None = None,
    include_catalog: bool = True,
    include_unconfigured: bool = True,
    include_registry: bool = False,
    extra: Sequence[Candidate] = (),
) -> list[ChainStep]:
    """The chain a run would build: the configured provider, then the catalog.

    Order is documented and deliberate:

    1. **explicit** candidates the caller passed in (they win);
    2. **the configured provider** — the provider store's default entry, else the
       active ``godsv.llm`` projection (the same file ``skaldir provider list``
       writes and the runtime reads);
    3. **catalog** entries, in :data:`CATALOG_ORDER`;
    4. with ``include_registry=True``, **every breadth-registry entry** that the
       curated catalog does not already offer, in :data:`REGISTRY_ORDER` — off by
       default, because a default chain should be the endpoints we curate, and on
       when a caller wants the whole honest list (a report, a doctor view, a
       "why not Moonshot?" answer).

    ``include_unconfigured=True`` keeps the catalog entries whose credential is
    missing in the list, as **skipped steps with their reason** — that is how a
    run can answer "why not OpenRouter?" without a log hunt. With
    ``include_unconfigured=False`` only the usable ones are offered.
    """
    lookup = dict(env) if env is not None else _os_environ()
    candidates: list[Candidate] = [*extra]
    configured = configured_candidate(env=lookup, config_path=config_path)
    if configured is not None:
        configured.source = SOURCE_CONFIG
        candidates.append(configured)
    if include_catalog:
        for entry_id in CATALOG_ORDER:
            candidate = candidate_from_catalog(entry_id, env=lookup)
            if not include_unconfigured and credential_state(candidate, lookup) == "unset":
                if candidate.api_key_env:  # a local entry needs no credential
                    continue
            candidates.append(candidate)
    if include_registry:
        offered = {candidate.id for candidate in candidates}
        for candidate in registry_chain_entries():
            if candidate.id in offered:
                continue
            if not include_unconfigured and credential_state(candidate, lookup) == "unset":
                if candidate.api_key_env:
                    continue
            candidates.append(candidate)
    return build_chain(candidates, env=lookup, require=require)


def configured_candidate(
    *, env: Mapping[str, str] | None = None, config_path: str | Any | None = None
) -> Candidate | None:
    """The provider the runtime would use, or ``None`` when nothing is configured."""
    from . import provider_config as pc  # noqa: PLC0415 — keeps the import flat

    lookup = dict(env) if env is not None else _os_environ()
    store = pc.load_store(config_path, env=lookup)
    entry = store.entry(store.default or None) if store.providers else None
    if entry is not None:
        return Candidate(
            id=f"config:{entry.name}",
            label=f"configured provider {entry.name}",
            base_url=entry.base_url,
            model=entry.model,
            kind=entry.kind,
            api_key_env=(entry.api_key_env,) if entry.api_key_env else (),
            tiers=dict(entry.tiers),
            features={},
            source=SOURCE_CONFIG,
            notes=f"from the provider store ({store.path})",
        )
    projection = lp.resolve_provider_config(env=lookup, config_path=store.path)
    if not projection.base_url and not projection.model:
        return None
    return Candidate(
        id="config:<active>",
        label="active godsv.llm projection",
        base_url=projection.base_url,
        model=projection.model,
        kind=projection.provider,
        api_key_env=(projection.api_key_env,) if projection.api_key_env else (),
        tiers=dict(projection.tier_models),
        features={},
        source=SOURCE_CONFIG,
        notes=f"the godsv.llm block the runtime reads ({store.path})",
    )


# --------------------------------------------------------------------------- #
# running the chain                                                            #
# --------------------------------------------------------------------------- #


def reason_for_exception(exc: BaseException) -> str:
    """The skip reason a runtime failure produces — mapped, never paraphrased."""
    if isinstance(exc, lp.StructuredOutputNotSupportedError):
        return SKIP_UNSUPPORTED
    if isinstance(exc, lp.ProviderAuthError):
        return SKIP_AUTH
    if isinstance(exc, lp.ProviderRateLimitError):
        return SKIP_RATE_LIMIT
    if isinstance(exc, lp.ProviderTimeoutError):
        return SKIP_TIMEOUT
    if isinstance(exc, (lp.LLMProviderError, lp.ProviderResponseError, lp.ProviderConfigError)):
        return SKIP_PROVIDER_ERROR
    if isinstance(exc, LLMError):
        return SKIP_PROVIDER_ERROR
    return SKIP_PROVIDER_ERROR


@dataclass
class ChainOutcome:
    """Which candidate answered, and the truth about every one that did not."""

    ok: bool
    answered_by: str = ""
    value: Any = None
    raw: str = ""
    mode: str = ""
    error: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    ready_count: int = 0
    skipped_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "answered_by": self.answered_by,
            "value": self.value,
            "raw": self.raw,
            "mode": self.mode,
            "error": self.error,
            "attempts": [dict(item) for item in self.attempts],
            "steps": [dict(item) for item in self.steps],
            "ready_count": self.ready_count,
            "skipped_count": self.skipped_count,
        }


def run_chain(
    steps: Sequence[ChainStep],
    call: Callable[[ChainStep], Any],
    *,
    attempts_per_step: int = 1,
) -> ChainOutcome:
    """Walk the ready candidates until one answers; record why the others did not.

    ``call(step)`` does the work (a real caller builds a client with
    :func:`skaldir.llm_provider.provider_for` and runs
    :func:`skaldir.structured.structured_call`). It may return a
    :class:`skaldir.structured.StructuredOutcome` — ``ok=False`` then counts as
    :data:`SKIP_INVALID` with the validation error as the detail — or any object
    with ``ok``/``value``/``raw``/``mode``. An exception is mapped by
    :func:`reason_for_exception` and the chain moves on. The result names the
    candidate that answered; when none did, every failure is in ``attempts``.
    """
    steps = list(steps)
    ready = [step for step in steps if step.status == STATUS_READY]
    outcome = ChainOutcome(
        ok=False,
        steps=[step.to_dict() for step in steps],
        ready_count=len(ready),
        skipped_count=len(steps) - len(ready),
    )
    if not ready:
        reasons = [
            f"{step.id} skipped ({step.reason}: {step.detail})"
            for step in steps
            if step.status != STATUS_READY
        ]
        outcome.error = (
            "no candidate is ready — " + "; ".join(reasons)
            if reasons
            else "no candidate is ready (the chain is empty)"
        )
        return outcome
    for step in ready:
        for _ in range(max(1, int(attempts_per_step))):
            step.attempts += 1
            started = time.monotonic()
            record: dict[str, Any] = {
                "id": step.id,
                "model": step.candidate.model,
                "attempt": step.attempts,
            }
            try:
                result = call(step)
            except BaseException as exc:  # noqa: BLE001 — every failure is mapped, none hidden
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                record.update(
                    {
                        "status": STATUS_FAILED,
                        "reason": reason_for_exception(exc),
                        "detail": f"{type(exc).__name__}: {exc}",
                        "latency_ms": int((time.monotonic() - started) * 1000),
                    }
                )
                outcome.attempts.append(record)
                continue
            ok = bool(getattr(result, "ok", True))
            record["latency_ms"] = int((time.monotonic() - started) * 1000)
            record["mode"] = str(getattr(result, "mode", "") or "")
            if ok:
                record.update(
                    {"status": STATUS_ANSWERED, "reason": st.REASON_ANSWERED, "detail": ""}
                )
                outcome.attempts.append(record)
                outcome.ok = True
                outcome.answered_by = step.id
                outcome.value = getattr(result, "value", result)
                outcome.raw = str(getattr(result, "raw", "") or "")
                outcome.mode = record["mode"]
                return outcome
            detail = str(
                getattr(result, "validation_error", "") or getattr(result, "error", "") or ""
            )
            record.update({"status": STATUS_FAILED, "reason": SKIP_INVALID, "detail": detail})
            outcome.attempts.append(record)
            # a validation failure is about the model's answer, not the endpoint:
            # another attempt on the same candidate only repeats it.
            break
    outcome.error = "no candidate answered: " + "; ".join(
        f"{item['id']} {item['status']} ({item['reason']}: {item['detail'][:160]})"
        for item in outcome.attempts
    )
    return outcome


def chain_with_catalogs(
    *,
    env: Mapping[str, str] | None = None,
    require: Sequence[str] = (FEATURE_STRUCTURED,),
    config_path: str | Any | None = None,
) -> dict[str, Any]:
    """The chain plus the catalog it came from — one JSON document for a report."""
    steps = auto_chain(env=env, require=require, config_path=config_path)
    return {
        "chain": chain_report(steps),
        "require": list(require),
        "catalog": catalog_report(),
    }


__all__ = [
    "CATALOG",
    "CATALOG_ORDER",
    "CHAIN_OVERLAP",
    "CLAIM_LIKELY",
    "CLAIM_NO",
    "CLAIM_PER_MODEL",
    "CLAIM_UNKNOWN",
    "CLAIM_VALUES",
    "CLAIM_YES",
    "FEATURES",
    "FEATURE_STRUCTURED",
    "FEATURE_TOOLS",
    "FEATURE_VISION",
    "FETCHED_DOCS",
    "KIND_NOT_COMPATIBLE",
    "KIND_OPENAI_COMPATIBLE",
    "PROBE_DATE",
    "PROBE_DATE_PREVIOUS",
    "PROBE_LOG",
    "PROBE_NOT_RUN",
    "PROBE_PARTIAL",
    "PROBE_RUN",
    "REGISTRY",
    "REGISTRY_ORDER",
    "SKIP_AUTH",
    "SKIP_CLIENT_UNSUPPORTED",
    "SKIP_DISABLED",
    "SKIP_INVALID",
    "SKIP_MISSING_CREDENTIAL",
    "SKIP_NO_BASE_URL",
    "SKIP_NO_MODEL",
    "SKIP_NOT_CONFIGURED",
    "SKIP_PROVIDER_ERROR",
    "SKIP_RATE_LIMIT",
    "SKIP_REASONS",
    "SKIP_TIMEOUT",
    "SKIP_UNSUPPORTED",
    "SOURCE_CATALOG",
    "SOURCE_CONFIG",
    "SOURCE_EXPLICIT",
    "SRC_DOCUMENTED",
    "SRC_UNKNOWN",
    "STATUS_ANSWERED",
    "STATUS_FAILED",
    "STATUS_READY",
    "STATUS_SKIPPED",
    "UNVERIFIED",
    "VERIFICATION_ORDER",
    "VERIFIED",
    "Candidate",
    "CatalogEntry",
    "ChainOutcome",
    "ChainStep",
    "Claim",
    "ProviderSpec",
    "auto_chain",
    "build_chain",
    "candidate_from_catalog",
    "candidate_from_registry",
    "catalog_entry",
    "catalog_model_ids",
    "catalog_report",
    "chain_report",
    "chain_with_catalogs",
    "configured_candidate",
    "credential_state",
    "drift_report",
    "model_id_report",
    "observed_model_ids",
    "probe_report",
    "provider_catalog_report",
    "reason_for_exception",
    "registry_chain_entries",
    "registry_entry",
    "registry_report",
    "run_chain",
]
