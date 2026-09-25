"""The provider breadth registry — one entry per endpoint we claim to speak to.

Why this file exists
--------------------

``skaldir.provider_catalog`` answers *"which candidate can answer this run?"* for
a handful of endpoints. This module answers the wider question an operator asks
before wiring anything up: **what is actually known about provider X, and how do
we know it?**

Three verification statuses, and the difference between them is the whole point:

``verified``
    We ran a probe against the real endpoint and the answer is in
    :data:`PROBE_LOG`. A verified claim names its probe step. **A probe beats a
    document**: if a fetched page promises native ``json_schema`` and the live
    endpoint answers HTTP 400, the registry says ``no`` and the refusal text is
    kept verbatim (that is exactly what happened to DeepSeek).
``documented``
    An official page says so. :attr:`Support.fetched` records whether *this*
    round actually fetched that page — ``fetched=True`` means the text was
    retrieved and read, ``fetched=False`` means the page is named as the source
    but was not retrieved, which is a weaker claim and says so.
``unverified``
    Nobody proved it. It is recorded as unknown with the place to look, never
    guessed into a ``yes``.

Nothing here is invented: every entry names the page it came from, and the
providers we could not reach (no credential in the environment) carry
``not_run`` with the reason instead of a silent gap.

Two probe rounds are in :data:`PROBE_LOG`, each step stamped with its own date:
``2026-09-18`` (the round that corrected DeepSeek's native ``json_schema``) and
``2026-09-19`` (the round that re-measured every provider a credential exists for
on this host and left the rest ``not_run`` with the reason).

Reading it from the CLI::

    skaldir provider catalog --text
    skaldir provider catalog --json --entry deepseek
    skaldir provider catalog --json --probes      # the live evidence, step by step
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

#: The decision layer's endpoint and model, imported from the adapter that speaks them:
#: the two strings live in **one** place, so the catalogue cannot drift away from
#: :func:`skaldir.system_one_adapters.build_typesafe_request` (before this entry the
#: catalogue named no TypeSafe row at all and those constants had no product reader).
from .system_one_adapters import TYPESAFE_ENDPOINT, TYPESAFE_MODEL

# --------------------------------------------------------------------------- #
# vocabulary                                                                   #
# --------------------------------------------------------------------------- #

#: how a claim was established. Ordered from strongest to weakest.
VERIFIED = "verified"
DOCUMENTED = "documented"
UNVERIFIED = "unverified"
VERIFICATION_ORDER: tuple[str, ...] = (VERIFIED, DOCUMENTED, UNVERIFIED)

#: what a feature is. ``per_model`` and ``unknown`` are honest answers.
SUPPORT_YES = "yes"
SUPPORT_NO = "no"
SUPPORT_PER_MODEL = "per_model"
SUPPORT_UNKNOWN = "unknown"
SUPPORT_VALUES: tuple[str, ...] = (SUPPORT_YES, SUPPORT_NO, SUPPORT_PER_MODEL, SUPPORT_UNKNOWN)

KIND_OPENAI_COMPATIBLE = "openai-compatible"
KIND_NATIVE = "native"
KIND_LOCAL = "local-openai-compatible"

#: Which *SKU* an endpoint serves. Plan/subscription bases are first-class: a
#: token-plan key on the pay-as-you-go host is a 401 that looks like a bad key
#: (measured on Xiaomi MiMo 2026-09-23). Empty = the provider's default/only SKU.
SKU_PAY_AS_YOU_GO = "pay-as-you-go"
SKU_TOKEN_PLAN = "token-plan"
SKU_CODING_PLAN = "coding-plan"
SKU_SUBSCRIPTION = "subscription"
SKU_CHATGPT_PLAN = "chatgpt-plan"
SKU_VALUES: tuple[str, ...] = (
    SKU_PAY_AS_YOU_GO,
    SKU_TOKEN_PLAN,
    SKU_CODING_PLAN,
    SKU_SUBSCRIPTION,
    SKU_CHATGPT_PLAN,
)

PROBE_RUN = "run"
PROBE_PARTIAL = "partial"
PROBE_NOT_RUN = "not_run"

#: the probe date this file's ``verified`` claims were measured on.
PROBE_DATE = "2026-09-19"

#: the earlier round, kept because its refusals are still the evidence for claims
#: no later round re-measured.
PROBE_DATE_PREVIOUS = "2026-09-18"


@dataclass(frozen=True)
class Support:
    """One feature of one provider, with the evidence that establishes it."""

    value: str
    status: str
    reference: str = ""
    note: str = ""
    fetched: bool = False

    def __post_init__(self) -> None:
        if self.value not in SUPPORT_VALUES:
            raise ValueError(
                f"unknown support value {self.value!r} (expected {list(SUPPORT_VALUES)})"
            )
        if self.status not in VERIFICATION_ORDER:
            raise ValueError(
                f"unknown verification status {self.status!r} (expected {list(VERIFICATION_ORDER)})"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "status": self.status,
            "reference": self.reference,
            "note": self.note,
            "fetched": self.fetched,
        }

    def describe(self) -> str:
        tail = f" ({self.reference})" if self.reference else ""
        extra = f" — {self.note}" if self.note else ""
        return f"{self.value} [{self.status}]{tail}{extra}"


@dataclass(frozen=True)
class Endpoint:
    """One base URL, the authentication it wants and the path we would POST to."""

    label: str
    base_url: str
    kind: str
    chat_path: str = "/chat/completions"
    auth_header: tuple[str, ...] = ()
    api_key_env: tuple[str, ...] = ()
    status: str = DOCUMENTED
    reference: str = ""
    fetched: bool = False
    note: str = ""
    #: Which plan/SKU this base serves (``SKU_*``). Empty = the provider default.
    sku: str = ""
    #: Key prefixes that belong to this SKU (``tp-`` → Xiaomi Token Plan). Used to
    #: auto-select an endpoint when the caller names no SKU.
    key_prefixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in VERIFICATION_ORDER:
            raise ValueError(f"unknown verification status {self.status!r}")

    @property
    def url(self) -> str:
        """The URL a chat completion would actually be POSTed to."""
        return self.base_url.rstrip("/") + self.chat_path

    @property
    def openai_compatible(self) -> bool:
        return self.kind in (KIND_OPENAI_COMPATIBLE, KIND_LOCAL)

    def matches_key(self, key: str) -> bool:
        """Does this credential look like it belongs to this SKU?"""
        text = str(key or "")
        return bool(text) and any(text.startswith(p) for p in self.key_prefixes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "base_url": self.base_url,
            "chat_path": self.chat_path,
            "url": self.url,
            "kind": self.kind,
            "openai_compatible": self.openai_compatible,
            "auth_header": list(self.auth_header),
            "api_key_env": list(self.api_key_env),
            "status": self.status,
            "reference": self.reference,
            "fetched": self.fetched,
            "note": self.note,
            "sku": self.sku,
            "key_prefixes": list(self.key_prefixes),
        }


@dataclass(frozen=True)
class ProviderSpec:
    """Everything the catalog claims about one provider — with its evidence."""

    id: str
    label: str
    docs: str
    endpoints: tuple[Endpoint, ...]
    model_patterns: tuple[str, ...] = ()
    default_model: str = ""
    streaming: Support = Support(SUPPORT_UNKNOWN, UNVERIFIED, note="not catalogued")
    native_json_schema: Support = Support(SUPPORT_UNKNOWN, UNVERIFIED, note="not catalogued")
    json_object: Support = Support(SUPPORT_UNKNOWN, UNVERIFIED, note="not catalogued")
    vision: Support = Support(SUPPORT_UNKNOWN, UNVERIFIED, note="not catalogued")
    tools: Support = Support(SUPPORT_UNKNOWN, UNVERIFIED, note="not catalogued")
    quirks: tuple[str, ...] = ()
    licence: str = ""
    licence_reference: str = ""
    licence_fetched: bool = False
    probe: str = PROBE_NOT_RUN
    probe_reason: str = ""
    note: str = ""

    @property
    def features(self) -> dict[str, Support]:
        return {
            "streaming": self.streaming,
            "native_json_schema": self.native_json_schema,
            "json_object": self.json_object,
            "vision": self.vision,
            "tools": self.tools,
        }

    @property
    def statuses(self) -> dict[str, str]:
        """The strongest status this entry can claim per axis (for a summary)."""
        out = {name: support.status for name, support in self.features.items()}
        out["endpoint"] = _strongest([endpoint.status for endpoint in self.endpoints])
        return out

    @property
    def verification(self) -> str:
        """The entry's overall status: the strongest thing proven about it.

        ``verified`` only when the endpoint really answered a probe; a provider
        whose page we read but never called stays ``documented``.
        """
        if self.probe == PROBE_RUN:
            return VERIFIED
        return _strongest([endpoint.status for endpoint in self.endpoints])

    @property
    def primary_endpoint(self) -> Endpoint:
        for endpoint in self.endpoints:
            if endpoint.kind in (KIND_OPENAI_COMPATIBLE, KIND_LOCAL):
                return endpoint
        return self.endpoints[0]

    def plan_endpoints(self) -> tuple[Endpoint, ...]:
        """Every pickable plan endpoint (one per SKU when the provider has several).

        Includes a ``native`` endpoint when it is a *plan SKU* (Codex's ChatGPT
        backend) — the plan is a pickable credential shape even though the
        OpenAI-shaped client cannot speak it as a chat base URL.
        """
        shaped = tuple(
            ep for ep in self.endpoints if ep.kind in (KIND_OPENAI_COMPATIBLE, KIND_LOCAL)
        )
        plans = tuple(ep for ep in self.endpoints if ep.sku and ep not in shaped)
        return shaped + plans if plans else (shaped or self.endpoints)

    def pick_endpoint(
        self, *, sku: str = "", key: str = "", base_url: str = ""
    ) -> Endpoint:
        """The one endpoint this call should use — SKU, key prefix, or URL; else primary.

        The rule that fixes the Xiaomi Token-Plan miss: a ``tp-`` key on the
        pay-as-you-go host is a 401 that looks like a bad key. Never guess past a
        contradiction: an explicit SKU wins, then a key-prefix match, then a URL
        match, then the primary endpoint.
        """
        wanted = str(sku or "").strip().lower()
        if wanted:
            for endpoint in self.plan_endpoints():
                if str(endpoint.sku or "").strip().lower() == wanted:
                    return endpoint
        text = str(key or "")
        if text:
            for endpoint in self.plan_endpoints():
                if endpoint.matches_key(text):
                    return endpoint
        url = str(base_url or "").strip().rstrip("/").lower()
        if url:
            for endpoint in self.plan_endpoints():
                if endpoint.base_url.strip().rstrip("/").lower() == url:
                    return endpoint
        return self.primary_endpoint

    def support(self, feature: str) -> Support:
        found = self.features.get(feature)
        if found is None:
            raise KeyError(f"unknown feature {feature!r} (known: {sorted(self.features)})")
        return found

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "docs": self.docs,
            "verification": self.verification,
            "probe": self.probe,
            "probe_reason": self.probe_reason,
            "default_model": self.default_model,
            "model_patterns": list(self.model_patterns),
            "endpoints": [endpoint.to_dict() for endpoint in self.endpoints],
            "features": {name: support.to_dict() for name, support in self.features.items()},
            "quirks": list(self.quirks),
            "licence": self.licence,
            "licence_reference": self.licence_reference,
            "licence_fetched": self.licence_fetched,
            "note": self.note,
        }


def _strongest(statuses: list[str]) -> str:
    for status in VERIFICATION_ORDER:
        if status in statuses:
            return status
    return UNVERIFIED


def _verified(value: str, probe_step: str, note: str) -> Support:
    return Support(value, VERIFIED, reference=f"probe:{probe_step}", note=note, fetched=True)


def _documented(value: str, reference: str, note: str = "", *, fetched: bool = False) -> Support:
    return Support(value, DOCUMENTED, reference=reference, note=note, fetched=fetched)


def _unverified(note: str, *, reference: str = "") -> Support:
    return Support(SUPPORT_UNKNOWN, UNVERIFIED, reference=reference, note=note)


# --------------------------------------------------------------------------- #
# the live probe log — what we actually measured, verbatim                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProbeStep:
    """One measured step of one live probe: what we sent, what came back."""

    provider: str
    step: str
    status: int | None
    ok: bool | None
    evidence: str
    #: which probe round this step belongs to — a claim verified in one round is not
    #: silently re-dated by a later one
    date: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "step": self.step,
            "status": self.status,
            "ok": self.ok,
            "evidence": self.evidence,
            "date": self.date or PROBE_DATE_PREVIOUS,
        }


def _dated(date: str, steps: tuple[ProbeStep, ...]) -> tuple[ProbeStep, ...]:
    """Stamp a round of probe steps with the date they were measured on."""
    return tuple(replace(step, date=date) for step in steps)


#: The first probe round (2026-09-18). Only providers whose credential was in the
#: environment could be probed; the rest are listed as ``not_run`` with the reason,
#: and **no claim anywhere in this file is upgraded because a probe was skipped**.
_PROBE_LOG_2026_09_18: tuple[ProbeStep, ...] = (
    ProbeStep("deepseek", "auth: GET /models", 200, True, "Bearer accepted"),
    ProbeStep(
        "deepseek",
        "auth control: bad key",
        401,
        True,
        '{"error":{"message":"Authentication Fails, Your api key: ****... is invalid",'
        '"type":"authentication_error","code":"invalid_request_error"}} — the 401 really is '
        "credential-driven, not a shape error",
    ),
    ProbeStep(
        "deepseek",
        "chat POST /chat/completions",
        200,
        True,
        'model echoed, answer "pong", 16 max_tokens',
    ),
    ProbeStep(
        "deepseek",
        "stream:true SSE",
        200,
        True,
        "text/event-stream, data: events delivered",
    ),
    ProbeStep(
        "deepseek",
        'response_format={"type":"json_schema","strict":true}',
        400,
        False,
        '{"error":{"message":"This response_format type is unavailable now",'
        '"type":"invalid_request_error","code":"invalid_request_error"}} — NATIVE json_schema '
        "REFUSED; this is the measurement that corrects the catalog",
    ),
    ProbeStep(
        "deepseek",
        'response_format={"type":"json_object"}',
        200,
        True,
        "the documented ceiling answers — the fallback mode is the honest one here",
    ),
    ProbeStep(
        "deepseek",
        "vision: image_url part, 64x64 truecolour PNG",
        200,
        True,
        'the image part was accepted and the answer described the picture ("Gradient") — a '
        "1x1 PNG was refused as an unsupported image first, so the smallest possible payload "
        "is not a fair probe",
    ),
    ProbeStep(
        "opencode-go",
        "auth: GET /models",
        200,
        True,
        "Bearer accepted, model list returned",
    ),
    ProbeStep(
        "opencode-go",
        "auth control: bad key",
        401,
        True,
        '{"type":"error","error":{"type":"AuthError","message":"Invalid API key."}}',
    ),
    ProbeStep(
        "opencode-go",
        "chat / stream / json_schema / json_object / vision",
        429,
        False,
        '{"type":"error","error":{"type":"GoUsageLimitError","message":"Weekly usage limit '
        'reached. Resets in 2 days."}} — the key and the base URL are proven, the SHAPE is not: '
        "the plan quota was exhausted, so every completion probe is not-run, not failed",
    ),
    ProbeStep(
        "openrouter", "auth: GET /models", 200, True, "Bearer accepted, 445 models listed"
    ),
    ProbeStep(
        "openrouter",
        "auth control: bad key",
        401,
        True,
        '{"error":{"message":"Missing Authentication header","code":401}}',
    ),
    ProbeStep(
        "openrouter",
        "chat POST /chat/completions (nex-agi/nex-n2.5-mini:free)",
        200,
        True,
        'answer "pong", model echoed',
    ),
    ProbeStep(
        "openrouter",
        "stream:true SSE (nex-agi/nex-n2.5-mini:free)",
        200,
        True,
        "text/event-stream, 7 data: chunks",
    ),
    ProbeStep(
        "openrouter",
        'response_format={"type":"json_schema","strict":true} (nex-agi/nex-n2.5-mini:free)',
        200,
        True,
        '{"answer":"pong","probe":1} — NATIVE json_schema measured working through the router on '
        "this route (OpenRouter's own docs make support per-model, which the 429s on another "
        "free route confirmed)",
    ),
    ProbeStep(
        "openrouter",
        'response_format={"type":"json_object"} (nex-agi/nex-n2.5-mini:free)',
        200,
        True,
        '{ "probe": 1, "answer": "pong" }',
    ),
    ProbeStep(
        "openrouter",
        "typed 429 shape on a rate-limited route (qwen/qwen3.8-27b:free)",
        429,
        False,
        '{"error":{"message":"Provider returned error","code":429,"metadata":{"raw":"… is '
        'temporarily rate-limited upstream…","is_byok":false}}} — the router reports the UPSTREAM '
        "provider as the limiter",
    ),
    ProbeStep(
        "local",
        "TCP connect 127.0.0.1:8080 / :8000 / :1234 / :11434",
        None,
        None,
        "all four closed on this host — no local server was running, so llm.cpp / vLLM / LM Studio "
        "/ Ollama stay documented and are NOT reported as measured",
    ),
    ProbeStep(
        "openai, anthropic, google-gemini, google-vertex, xai, moonshot, alibaba-qwen, zai, "
        "xiaomi-mimo",
        "probe not attempted",
        None,
        None,
        "no credential for these was present in the environment, so no probe was attempted — "
        "their claims are documented/unverified, never verified",
    ),
)

#: The second probe round (2026-09-19): every provider this host holds a credential
#: for was re-measured live, and the rest are ``not_run`` with the reason. The raw
#: payloads behind these steps are kept outside the repo, under the round's record.
_PROBE_LOG_2026_09_19: tuple[ProbeStep, ...] = (
    ProbeStep(
        "—",
        "credential inventory before the run",
        None,
        None,
        "the two dotenv files on this host hold exactly three provider credentials, read by "
        "NAME and LENGTH only: DEEPSEEK_API_KEY (len 35), OPENROUTER_API_KEY (len 73), "
        "OPENCODE_GO_API_KEY (len 67). SKALDIR_* and stub-shaped variables are dropped before "
        "anything reads a credential, and no value is printed or written anywhere",
    ),
    ProbeStep(
        "deepseek", "auth: GET /models", 200, True,
        "Bearer accepted; 2 model ids listed — deepseek-flash, deepseek-v4-pro (299 ms). The "
        "catalog's OWN default model id (deepseek-chat) is not in that list any more: it still "
        "answers, but the /models surface has moved on",
    ),
    ProbeStep(
        "deepseek",
        "auth control: bad key",
        401,
        True,
        '{"error":{"message":"Authentication Fails, Your api key: ****0000 is invalid",'
        '"type":"authentication_error","param":null,"code":"invalid_request_error"}} — the 401 '
        "is credential-driven, not a shape error",
    ),
    ProbeStep(
        "deepseek",
        "chat POST /chat/completions (model=deepseek-chat)",
        200,
        True,
        'model echoed "deepseek-flash", answer "pong" (content_chars=4), 724 ms, usage 12/2/14 '
        "— the alias answers, but the ECHO is a different id than the one asked for",
    ),
    ProbeStep(
        "deepseek",
        "chat POST /chat/completions (model=deepseek-reasoner, max_tokens=24)",
        200,
        True,
        "content_chars=0 and 101 chars of reasoning_content, finish_reason=length — THIS IS THE "
        "REASONING-ONLY ANSWER: a reasoning backend given too small a budget spends every token "
        "on reasoning_content and leaves content empty. A probe that reads only content records "
        "a false zero here",
    ),
    ProbeStep(
        "deepseek",
        "chat POST /chat/completions (model=deepseek-reasoner, max_tokens=400)",
        200,
        True,
        'content "pong" (content_chars=4) AND 396 chars of reasoning_content, finish_reason=stop '
        "— with room, the same model fills BOTH fields, which proves the empty content above was "
        "a token budget artifact and not a refusal",
    ),
    ProbeStep(
        "deepseek", "stream:true SSE (model=deepseek-chat)", 200, True,
        "text/event-stream; charset=utf-8 — 26 data: events, first after 296 ms, 74 chars of "
        "text delivered",
    ),
    ProbeStep(
        "deepseek", "stream:true SSE (model=deepseek-reasoner)", 200, True,
        "26 data: events with content_chars=0 and reasoning_chars=106 — on the stream the "
        "reasoning arrives in delta.reasoning_content, so a stream reader must watch both fields "
        "for the same reason the non-streaming reader must",
    ),
    ProbeStep(
        "deepseek",
        'response_format={"type":"json_schema","strict":true}',
        400,
        False,
        '{"error":{"message":"This response_format type is unavailable now",'
        '"type":"invalid_request_error","param":null,"code":"invalid_request_error"}} — NATIVE '
        "json_schema REFUSED again on 2026-09-19, on both model ids: the 2026-09-18 correction "
        "reproduces and is not a one-day outage",
    ),
    ProbeStep(
        "deepseek",
        'response_format={"type":"json_object"} (model=deepseek-chat)',
        200,
        True,
        '{"probe":1,"answer":"pong"} — parses as JSON; the documented ceiling answers',
    ),
    ProbeStep(
        "deepseek",
        'response_format={"type":"json_object"} (model=deepseek-reasoner)',
        200,
        False,
        "HTTP 200 but content_chars=0 with 392 chars of reasoning_content, so the answer is not "
        "parseable JSON — a 200 is not the same thing as a structured answer, and this is the "
        "reasoning-only trap landing on the structured-output path",
    ),
    ProbeStep(
        "deepseek",
        'response_format={"type":"json_object"} (model=deepseek-reasoner, max_tokens=400)',
        200,
        True,
        '{"probe":1,"answer":"pong"} — valid JSON beside 323 chars of reasoning_content, '
        "finish_reason=stop. THIS IS THE CONTROL that makes the row above honest: the reasoner "
        "CAN answer structured JSON, so the empty answer at max_tokens=96 was a token budget "
        "artifact and json_object stays `yes` for DeepSeek rather than being downgraded by the "
        "budget mistake",
    ),
    ProbeStep(
        "deepseek", "vision: 64x64 truecolour PNG (model=deepseek-chat)", 200, True,
        'the image part was accepted and answered "Gradient" in 1085 ms',
    ),
    ProbeStep(
        "deepseek", "vision: 64x64 truecolour PNG (model=deepseek-reasoner)", 200, True,
        'the same picture answered "Gradient" (content_chars=8) beside 256 chars of '
        "reasoning_content — image input is on the wire for both ids this round measured",
    ),
    ProbeStep(
        "openrouter", "auth: GET /models", 200, True,
        "Bearer accepted; 447 model ids listed (445 on 2026-09-18) — the list moves, so it is "
        "evidence of reachability, not a stable catalogue",
    ),
    ProbeStep(
        "openrouter",
        "auth control: bad key",
        401,
        True,
        '{"error":{"message":"Missing Authentication header","code":401}}',
    ),
    ProbeStep(
        "openrouter",
        "chat POST /chat/completions (nex-agi/nex-n2.5-mini:free)",
        200,
        True,
        'model echoed "nex-agi/nex-n2.5-mini:free", answer "pong" (content_chars=4, 2 chars of '
        "reasoning), 570 ms",
    ),
    ProbeStep(
        "openrouter",
        "chat POST /chat/completions (inclusionai/ling-3.0-flash-vl:free)",
        200,
        True,
        'model echoed back, answer "pong" PLUS 55 chars of reasoning_content — one completion '
        "can carry two answer fields, and a client that reads one of them is reading half",
    ),
    ProbeStep(
        "openrouter",
        "stream:true SSE (nex-agi/nex-n2.5-mini:free)",
        200,
        True,
        'text/event-stream; 7 data: events; content delivered — "one two three" (13 chars) — '
        "streaming really does stream here",
    ),
    ProbeStep(
        "openrouter",
        "stream:true SSE (inclusionai/ling-3.0-flash-vl:free)",
        200,
        True,
        "27 data: events and NOT one character of delta.content: every delta carried "
        '"reasoning" (with a parallel reasoning_details list) while content stayed "". The '
        "chunks are real and the model really was answering — OpenRouter names the field "
        "`reasoning` in a streaming delta, not `reasoning_content`, so a client that reads only "
        "delta.content reports a healthy stream as empty",
    ),
    ProbeStep(
        "openrouter",
        'response_format={"type":"json_schema","strict":true} (nex-agi/nex-n2.5-mini:free)',
        200,
        True,
        '{ "answer": "pong" , "probe": 1 } in 2163 ms — NATIVE json_schema measured working '
        "through the router on this route",
    ),
    ProbeStep(
        "openrouter",
        'response_format={"type":"json_schema","strict":true} (inclusionai/ling-3.0-flash-vl:free)',
        400,
        False,
        '{"error":{"message":"Provider returned error","code":400,"metadata":{"raw":"{\\"code\\":'
        '400, \\"reason\\":\\"INVALID_REQUEST_BODY\\", \\"message\\":\\"model features structured '
        'outputs not support\\", \\"metadata\\":{}}","provider_name":"Novita","is_byok":false}}} '
        "— the SAME router refuses it on another route and names the upstream provider, which is "
        "the docs' per-model claim reproduced as a measurement",
    ),
    ProbeStep(
        "openrouter",
        'response_format={"type":"json_object"} (nex-agi/nex-n2.5-mini:free)',
        200,
        True,
        '{ "probe": 1, "answer": "pong" } — the fallback mode answers in 488 ms',
    ),
    ProbeStep(
        "openrouter",
        'response_format={"type":"json_object"} (inclusionai/ling-3.0-flash-vl:free)',
        400,
        False,
        '{"error":{"message":"Provider returned error","code":400,"metadata":{"raw":"{\\"code\\":'
        '400, \\"reason\\":\\"INVALID_REQUEST_BODY\\", \\"message\\":\\"model: inclusionai/'
        'ling-3.0-flash-vl does not support feature: structured-outputs\\", \\"metadata\\":{}}",'
        '"provider_name":"Novita","is_byok":false}}} — even JSON MODE is per-model on a router, '
        "not a property of the router",
    ),
    ProbeStep(
        "openrouter",
        "vision: 64x64 truecolour PNG (inclusionai/ling-3.0-flash-vl:free)",
        200,
        True,
        "the image part was accepted in 1465 ms and the model answered with 280 chars of "
        "reasoning_content describing 'a color gradient ... from pink/magenta ... to blue', "
        "content empty at finish_reason=length — a vision route answering in the reasoning field",
    ),
    ProbeStep(
        "openrouter",
        "vision: 64x64 truecolour PNG (nex-agi/nex-n2.5-mini:free)",
        400,
        False,
        '{"error":{"message":"Provider returned error","code":400,"metadata":{"raw":"{\\"error\\":'
        '{\\"message\\":\\"The request is invalid.\\",\\"type\\":\\"invalid_request_error\\",'
        '\\"param\\":\\"\\",\\"code\\":\\"invalid_request\\"}}","provider_name":"Nex AGI",'
        '"provider_error_code":"invalid_request"},"is_byok":false}} — the other route refuses the '
        "image part, so vision here is per-model in both directions",
    ),
    ProbeStep(
        "openrouter",
        "retired free slug control (deepseek/deepseek-chat:free)",
        404,
        False,
        '{"error":{"message":"This model is unavailable for free. The paid version is available '
        'now - use this slug instead: deepseek/deepseek-chat","code":404}} — a `:free` slug can be '
        "retired out from under a client, so a model id is configuration and not a constant",
    ),
    ProbeStep(
        "opencode-go", "auth: GET /models", 200, True,
        "Bearer accepted; 37 model ids listed (minimax-m3, minimax-m2.7, minimax-m2.5, kimi-k3, "
        "kimi-k2.7-code, kimi-k2.6, longcat-2.0, kimi-k2.5, glm-5.2, glm-5.3-flash, glm-5.3, "
        "glm-5.1, …) in 407 ms",
    ),
    ProbeStep(
        "opencode-go",
        "auth control: bad key with a REAL model id (minimax-m2.7)",
        401,
        True,
        '{"type":"error","error":{"type":"AuthError","message":"Invalid API key."}} — this is the '
        "control that means something: a real model id and a wrong key",
    ),
    ProbeStep(
        "opencode-go",
        "auth control: bad key with a placeholder model id",
        401,
        True,
        '{"type":"error","error":{"type":"ModelError","message":"Model probe is not supported"}} '
        "— a 401 that is about the MODEL NAME, not the credential. Kept because it is exactly the "
        "trap: an auth control that sends a placeholder id measures the wrong thing",
    ),
    ProbeStep(
        "opencode-go",
        "every completion probe: chat / stream / json_schema / json_object / vision × "
        "(minimax-m2.7, minimax-m3)",
        429,
        False,
        '{"type":"error","error":{"type":"GoUsageLimitError","message":"Weekly usage limit '
        'reached. Resets in 1 day. To continue using this model now, enable usage from your '
        'available balance: https://opencode.ai/workspace/wrk_…/go"},"metadata":{"workspace":'
        '"wrk_…","limitName":"weekly"}} — the credential and the base URL are proven again and '
        "the SHAPE is not: the plan quota is exhausted, so every completion probe is not-run, "
        "not failed, and no capability claim here may be upgraded. The reset moved from 'in 2 "
        "days' (2026-09-18) to 'in 1 day', which is the same weekly window observed twice",
    ),
    ProbeStep(
        "local",
        "TCP connect 127.0.0.1:8080 / :8000 / :1234 / :11434",
        None,
        None,
        "all four closed again on 2026-09-19 — no local server was running, so llama.cpp / vLLM "
        "/ LM Studio / Ollama stay documented and are NOT reported as measured",
    ),
    ProbeStep(
        "openai, anthropic, google-gemini, google-vertex, xai, moonshot, alibaba-qwen, zai, "
        "xiaomi-mimo, ollama-cloud",
        "probe not attempted",
        None,
        None,
        "no credential for any of these exists on this host — the two dotenv files hold exactly "
        "three provider credentials and none of them belongs to these providers, so no probe was "
        "attempted and their claims stay documented/unverified. (A DASHSCOPE_BASE_URL is set "
        "without a DASHSCOPE_API_KEY: the region can be named, the credential cannot be sent.)",
    ),
    ProbeStep(
        "typesafe",
        "unauthenticated POST /v1/systemone (NO credential sent — deliberately, so no key can "
        "reach the wrong host)",
        403,
        False,
        '{"detail":{"error_type":"authentication_error","message":"Must supply an API key! '
        'Check your request and try again."}} — a live auth gate, not a dead name (two A records '
        "resolve: 44.227.31.201 / 100.20.85.248, answered in 561 ms). The fetched API page's "
        "error table names 401 for a missing key; the endpoint answered 403 with the same "
        "meaning, and the measured number is kept over the documented one. No authenticated call "
        "was made: there is no TYPESAFE_API_KEY in the environment and no "
        "secret://typesafe/systemone handle in the Treasury, so every capability claim on this "
        "entry is documented, never verified",
    ),
)

#: Every probe round, oldest first. A step carries its own date, so a claim verified
#: in one round is never silently re-dated by a later one.
PROBE_LOG: tuple[ProbeStep, ...] = (
    _dated(PROBE_DATE_PREVIOUS, _PROBE_LOG_2026_09_18)
    + _dated(PROBE_DATE, _PROBE_LOG_2026_09_19)
)

#: Pages this round actually retrieved and read (the ``fetched=True`` evidence).
FETCHED_DOCS: dict[str, str] = {
    "https://platform.openai.com/docs/api-reference/authentication": "OpenAI auth: HTTP Bearer",
    "https://platform.openai.com/docs/guides/structured-outputs": "OpenAI strict json_schema",
    "https://platform.openai.com/docs/api-reference/chat/create": "OpenAI POST /chat/completions",
    "https://docs.claude.com/en/api/overview": (
        "Anthropic base URL + Authorization/x-api-key/anthropic-version headers"
    ),
    "https://docs.claude.com/en/api/openai-sdk": (
        "Anthropic OpenAI-compat layer: response_format is IGNORED"
    ),
    "https://ai.google.dev/gemini-api/docs/openai": "Gemini OpenAI-compat base URL + Bearer",
    "https://ai.google.dev/gemini-api/docs/text-generation": "Gemini native x-goog-api-key",
    "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/migrate/openai/"
    "auth-and-credentials": "Vertex OpenAI-compat base URL + access-token auth",
    "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/migrate/openai/"
    "overview": "Vertex response_format json_object/json_schema, image_url support",
    "https://docs.x.ai/docs/developers/model-capabilities/text/structured-outputs": (
        "xAI response_format json_schema, strict implicit for tools"
    ),
    "https://docs.x.ai/docs/api-reference": "xAI base URL https://api.x.ai/v1",
    "https://api-docs.deepseek.com/guides/json_mode/": "DeepSeek documents json_object only",
    "https://platform.moonshot.ai/docs/api/chat": (
        "Moonshot base URL, json_object + json_schema, SSE + stream_options, image_url/video_url"
    ),
    "https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope": (
        "DashScope regional compatible-mode base URLs + region-bound API keys"
    ),
    "https://openrouter.ai/docs/guides/features/structured-outputs": (
        "OpenRouter json_schema is per-model, require_parameters, strict per provider"
    ),
    "https://docs.z.ai/api-reference/introduction": "Z.ai base URL + Bearer",
    "https://docs.z.ai/guides/capabilities/struct-output": "Z.ai documents json_object only",
    "https://mimo.mi.com/docs/quick-start/first-api-call": (
        "Xiaomi MiMo base URLs, MIMO_API_KEY, api-key header"
    ),
    "https://opencode.ai/docs/go/": "OpenCode Go model table + OpenAI-compatible endpoint",
    "https://docs.ollama.com/api/openai-compatibility": (
        "Ollama OpenAI-compat: response_format/stream/stream_options/vision, api key ignored"
    ),
    "https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md": (
        "llama.cpp server: port 8080, --api-key, /v1/chat/completions, schema-constrained JSON"
    ),
    "https://docs.vllm.ai/en/latest/serving/online_serving.html": (
        "vLLM /v1/chat/completions"
    ),
    "https://docs.vllm.ai/en/latest/features/structured_outputs.html": (
        "vLLM structured_outputs (guided_json removed in v0.12.0)"
    ),
    "https://lmstudio.ai/docs/developer/openai-compat": (
        "LM Studio http://localhost:1234/v1, chat completions with images"
    ),
    "https://raw.githubusercontent.com/ggml-org/llama.cpp/master/LICENSE": "llama.cpp MIT",
    "https://raw.githubusercontent.com/vllm-project/vllm/main/LICENSE": "vLLM Apache-2.0",
    "https://raw.githubusercontent.com/ollama/ollama/main/LICENSE": "Ollama MIT",
    "https://lmstudio.ai/terms": "LM Studio desktop app terms (proprietary)",
    "https://docs.typesafe.ai/api": (
        "TypeSafe System One: POST https://api.typesafe.ai/v1/systemone, Bearer auth, body "
        "{model, state, questions}, questions = map<string, Question> of noul/choice/score, "
        "answers keyed by the same ids, 401/422/429/529 error table"
    ),
    "https://docs.typesafe.ai/concepts/system-one": (
        "TypeSafe System One concepts: typed probabilities instead of prose, text input only "
        "(no images/audio/video), jev-latest as the documented model alias, confidence per answer"
    ),
    "https://typesafe.ai/blog/introducing-system-one-models-and-jev": (
        "TypeSafe early-access announcement: 70–500 ms end-to-end, calibrated decisions, output "
        "tokens free, Jev = the first System One model"
    ),
}

#: The shared, re-measured fact behind every ``not_run`` on this host: name the
#: variable that would have authorised the probe and the date it was checked.
#: "not verified" on its own is not a reason.
_NO_CREDENTIAL = (
    "no {var} on this host — re-checked 2026-09-19, when the two dotenv files held exactly "
    "three provider credentials (DeepSeek, OpenRouter, OpenCode Go) and no other provider's"
)

_NOT_RUN_OPENAI = _NO_CREDENTIAL.format(var="OPENAI_API_KEY")
_NOT_RUN_ANTHROPIC = _NO_CREDENTIAL.format(var="ANTHROPIC_API_KEY")
_NOT_RUN_GOOGLE = _NO_CREDENTIAL.format(var="GEMINI_API_KEY / GOOGLE_API_KEY")
_NOT_RUN_XAI = _NO_CREDENTIAL.format(var="XAI_API_KEY")
_NOT_RUN_MOONSHOT = _NO_CREDENTIAL.format(var="MOONSHOT_API_KEY")
_NOT_RUN_QWEN = (
    _NO_CREDENTIAL.format(var="DASHSCOPE_API_KEY")
    + ". A DASHSCOPE_BASE_URL IS set, which names the region and cannot authorise a call"
)
_NOT_RUN_ZAI = _NO_CREDENTIAL.format(var="ZAI_API_KEY / ZHIPUAI_API_KEY")
_NOT_RUN_XIAOMI = _NO_CREDENTIAL.format(var="MIMO_API_KEY")
_NOT_RUN_LOCAL = (
    "no local server was listening on the documented port at probe time — all four of "
    "127.0.0.1:8080 (llama.cpp), :8000 (vLLM), :1234 (LM Studio) and :11434 (Ollama) were "
    "closed on 2026-09-19, re-checked after the same result on 2026-09-18"
)


REGISTRY: dict[str, ProviderSpec] = {
    # ----------------------------------------------------------------- OpenAI --
    "openai": ProviderSpec(
        id="openai",
        label="OpenAI",
        docs="https://platform.openai.com/docs",
        endpoints=(
            Endpoint(
                label="the reference OpenAI-compatible endpoint",
                base_url="https://api.openai.com/v1",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=("Authorization: Bearer $OPENAI_API_KEY",),
                api_key_env=("OPENAI_API_KEY",),
                status=DOCUMENTED,
                reference="https://platform.openai.com/docs/api-reference/authentication",
                fetched=True,
                note="the fetched page says 'Provide API credentials with HTTP Bearer "
                "authentication' and curls https://api.openai.com/v1/models",
            ),
        ),
        model_patterns=("gpt-*", "o*-*", "*-mini", "*-nano", "gpt-*-chat-latest"),
        default_model="gpt-4o-mini",
        streaming=_documented(
            SUPPORT_YES,
            "https://platform.openai.com/docs/api-reference/chat/create",
            "`stream: true` on POST /chat/completions",
            fetched=True,
        ),
        native_json_schema=_documented(
            SUPPORT_YES,
            "https://platform.openai.com/docs/guides/structured-outputs",
            "response_format json_schema with strict:true is documented; a strict schema must "
            "set additionalProperties:false and list every property in `required`",
            fetched=True,
        ),
        json_object=_documented(
            SUPPORT_YES,
            "https://platform.openai.com/docs/guides/structured-outputs",
            "the same page covers the json_object form",
            fetched=True,
        ),
        vision=_documented(
            SUPPORT_YES,
            "https://platform.openai.com/docs/guides/vision",
            "the vision guide is named as the source; this round did not fetch it",
        ),
        tools=_documented(
            SUPPORT_YES, "https://platform.openai.com/docs/guides/function-calling",
            "named as the source; not fetched this round",
        ),
        quirks=(
            "the page fetched on 2026-09-18 uses `gpt-6-astra` as its example model — ids move "
            "faster than our catalog, so model ids are patterns here, not a list to trust",
            "a schema that is not strict-clean is refused, not silently relaxed",
        ),
        licence="hosted API governed by OpenAI's terms; not fetched this round",
        licence_reference="https://openai.com/policies/terms-of-use",
        probe=PROBE_NOT_RUN,
        probe_reason=_NOT_RUN_OPENAI,
        note="the wire shape every other 'compatible' provider imitates",
    ),
    # -------------------------------------------------------------- Anthropic --
    "anthropic": ProviderSpec(
        id="anthropic",
        label="Anthropic (Claude)",
        docs="https://docs.claude.com/",
        endpoints=(
            Endpoint(
                label="native Messages API",
                base_url="https://api.anthropic.com/v1",
                kind=KIND_NATIVE,
                chat_path="/messages",
                auth_header=(
                    "Authorization: Bearer $ANTHROPIC_API_KEY",
                    "x-api-key: $ANTHROPIC_API_KEY",
                    "anthropic-version: 2023-06-01",
                ),
                api_key_env=("ANTHROPIC_API_KEY",),
                status=DOCUMENTED,
                reference="https://docs.claude.com/en/api/overview",
                fetched=True,
                note="the fetched overview names `https://api.anthropic.com`, makes "
                "`anthropic-version` required, and marks x-api-key a legacy fallback for "
                "Authorization — a different wire shape from /chat/completions",
            ),
            Endpoint(
                label="OpenAI SDK compatibility layer",
                base_url="https://api.anthropic.com/v1",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=("Authorization: Bearer $ANTHROPIC_API_KEY",),
                api_key_env=("ANTHROPIC_API_KEY",),
                status=DOCUMENTED,
                reference="https://docs.claude.com/en/api/openai-sdk",
                fetched=True,
                note="the compatibility page lists `authorization` as fully supported and "
                "`response_format` as IGNORED — the endpoint exists, but a schema-constrained "
                "answer through it does not",
            ),
        ),
        model_patterns=("claude-*", "claude-*-latest"),
        default_model="",
        streaming=_documented(
            SUPPORT_YES,
            "https://docs.claude.com/en/api/openai-sdk",
            "the fetched compatibility table marks `stream` and `stream_options` fully supported",
            fetched=True,
        ),
        native_json_schema=_documented(
            SUPPORT_NO,
            "https://docs.claude.com/en/api/openai-sdk",
            "`response_format` is Ignored on the compatibility layer, and `strict` in a tool "
            "schema is Ignored too; JSON output is a native-API feature (Structured Outputs)",
            fetched=True,
        ),
        json_object=_documented(
            SUPPORT_NO,
            "https://docs.claude.com/en/api/openai-sdk",
            "the same row: response_format — Ignored",
            fetched=True,
        ),
        vision=_documented(
            SUPPORT_YES,
            "https://docs.claude.com/en/api/messages",
            "the Messages API takes image content blocks; the compatibility layer strips audio "
            "but not images (the messages page was not fetched this round)",
        ),
        tools=_documented(
            SUPPORT_YES,
            "https://docs.claude.com/en/api/openai-sdk",
            "tool name/description/parameters 'fully supported'; `strict` in a tool schema is "
            "Ignored",
            fetched=True,
        ),
        quirks=(
            "the native API needs the `anthropic-version` header — a request without it is a "
            "400, not a 401",
            "through the compatibility layer `max_tokens` is fully supported but `n` must be "
            "exactly 1 and temperature above 1 is capped at 1",
            "token accounting the compatibility layer cannot give: completion_tokens_details and "
            "prompt_tokens_details are Always empty",
            "rate-limit headers are real: x-ratelimit-limit/remaining/reset-tokens and "
            "retry-after are all fully supported, so a retry policy can read them",
        ),
        licence="hosted API governed by Anthropic's commercial terms; not fetched this round",
        licence_reference="https://www.anthropic.com/legal/commercial-terms",
        probe=PROBE_NOT_RUN,
        probe_reason=_NOT_RUN_ANTHROPIC,
        note="listed so a chain can say WHY the OpenAI-shaped client cannot use it natively",
    ),
    # ----------------------------------------------------------- Google Gemini --
    "google-gemini": ProviderSpec(
        id="google-gemini",
        label="Google Gemini (AI Studio API key)",
        docs="https://ai.google.dev/gemini-api/docs",
        endpoints=(
            Endpoint(
                label="OpenAI compatibility layer",
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=("Authorization: Bearer $GEMINI_API_KEY",),
                api_key_env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
                status=DOCUMENTED,
                reference="https://ai.google.dev/gemini-api/docs/openai",
                fetched=True,
                note="the fetched page sets base_url to .../v1beta/openai/ and posts to "
                ".../v1beta/openai/chat/completions with 'Authorization: Bearer'",
            ),
            Endpoint(
                label="native Gemini API (a different shape)",
                base_url="https://generativelanguage.googleapis.com/v1beta",
                kind=KIND_NATIVE,
                chat_path="/interactions",
                auth_header=("x-goog-api-key: $GEMINI_API_KEY",),
                api_key_env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
                status=DOCUMENTED,
                reference="https://ai.google.dev/gemini-api/docs/text-generation",
                fetched=True,
                note="the fetched page curls .../v1beta/interactions with an `x-goog-api-key` "
                "header and a `system_instruction` field — not the OpenAI shape",
            ),
        ),
        model_patterns=("gemini-*", "gemini-*-flash*", "gemini-*-pro*", "gemini-*-image*"),
        default_model="",
        streaming=_documented(
            SUPPORT_YES,
            "https://ai.google.dev/gemini-api/docs/openai",
            "'stream: true' is documented on the compatibility page",
            fetched=True,
        ),
        native_json_schema=_documented(
            SUPPORT_YES,
            "https://ai.google.dev/gemini-api/docs/openai",
            "the fetched page shows response_format used with a typed schema via the OpenAI "
            "SDK (zodResponseFormat / a Pydantic model)",
            fetched=True,
        ),
        json_object=_documented(
            SUPPORT_YES,
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/migrate/"
            "openai/overview",
            "on the Vertex-compatible surface json_object is 'Interpreted as passing "
            "application/json to the Gemini API'",
            fetched=True,
        ),
        vision=_documented(
            SUPPORT_YES,
            "https://ai.google.dev/gemini-api/docs/openai",
            "the fetched page shows an image_url part carrying a data:image/jpeg;base64 URI",
            fetched=True,
        ),
        tools=_documented(
            SUPPORT_YES,
            "https://ai.google.dev/gemini-api/docs/openai",
            "tool/function calling is documented on the compatibility page; not re-read in full",
        ),
        quirks=(
            "the model namespace differs between the two surfaces: on the compatibility layer "
            "you name `gemini-3.8-flash`, natively you speak `models/gemini-...`",
            "`stream_options: {\"include_usage\": true}` is how usage arrives on a stream",
            "the compatibility layer also serves /images/generations and /videos, which are NOT "
            "chat completions",
        ),
        licence="hosted API governed by Google's Gemini API terms; not fetched this round",
        licence_reference="https://ai.google.dev/gemini-api/terms",
        probe=PROBE_NOT_RUN,
        probe_reason=_NOT_RUN_GOOGLE,
        note="the free-tier key and the billed Vertex credential are different products — do "
        "not treat one as the other",
    ),
    # ----------------------------------------------------------- Google Vertex --
    "google-vertex": ProviderSpec(
        id="google-vertex",
        label="Google Vertex AI (project + region, OpenAI-compatible)",
        docs="https://docs.cloud.google.com/gemini-enterprise-agent-platform",
        endpoints=(
            Endpoint(
                label="Vertex OpenAI-compatible endpoint (regional)",
                base_url="https://{LOCATION}-aiplatform.googleapis.com/v1beta1/projects/"
                "{PROJECT_ID}/locations/{LOCATION}/endpoints/openapi",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=("Authorization: Bearer <gcloud access token>",),
                api_key_env=(),
                status=DOCUMENTED,
                reference="https://docs.cloud.google.com/gemini-enterprise-agent-platform/"
                "models/migrate/openai/auth-and-credentials",
                fetched=True,
                note="the fetched page exports OPENAI_BASE_URL to exactly this URL and sets "
                "OPENAI_API_KEY to the output of `gcloud auth application-default "
                "print-access-token` — an OAuth token, not an API key",
            ),
            Endpoint(
                label="Vertex endpoint id variant (a deployed endpoint)",
                base_url="https://{LOCATION}-aiplatform.googleapis.com/v1beta1/projects/"
                "{PROJECT_ID}/locations/{LOCATION}/endpoints/{ENDPOINT_ID}",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=("Authorization: Bearer <gcloud access token>",),
                api_key_env=(),
                status=DOCUMENTED,
                reference="https://docs.cloud.google.com/gemini-enterprise-agent-platform/"
                "models/migrate/openai/examples",
                fetched=True,
                note="a Model Garden / self-deployed endpoint is addressed by its own "
                "ENDPOINT_ID instead of the `openapi` suffix",
            ),
        ),
        model_patterns=("gemini-*", "meta/llama-*", "mistralai/*", "<endpoint-id>"),
        default_model="",
        streaming=_documented(
            SUPPORT_YES,
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/migrate/"
            "openai/overview",
            "the parameter table lists `stream` and streamed function-call arguments",
            fetched=True,
        ),
        native_json_schema=_documented(
            SUPPORT_YES,
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/migrate/"
            "openai/overview",
            "the table lists response_format json_schema and points at the OpenAI strict-schema "
            "rules for fully recursive schemas",
            fetched=True,
        ),
        json_object=_documented(
            SUPPORT_YES,
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/migrate/"
            "openai/overview",
            "json_object: 'Interpreted as passing application/json to the Gemini API'",
            fetched=True,
        ),
        vision=_documented(
            SUPPORT_YES,
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/migrate/"
            "openai/overview",
            "image_url accepts a Cloud Storage URI or base64 blob data",
            fetched=True,
        ),
        tools=_documented(
            SUPPORT_YES,
            "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/migrate/"
            "openai/overview",
            "tools take OpenAPI-specification parameters",
            fetched=True,
        ),
        quirks=(
            "the base URL is not a constant — it embeds the project, the region and (for a "
            "deployed endpoint) an endpoint id, so it cannot be a catalog default",
            "the credential is a short-lived access token: a 401 after an hour is an expired "
            "token, not a wrong key",
            "a `global` location variant exists (aiplatform.googleapis.com/.../locations/global"
            "/endpoints/{ENDPOINT})",
            "quota is per project AND per region — a regional 429 says nothing about another "
            "region",
        ),
        licence="governed by the Google Cloud / Vertex AI terms of service; not fetched",
        licence_reference="https://cloud.google.com/terms",
        probe=PROBE_NOT_RUN,
        probe_reason="needs a Google Cloud project AND a region AND a short-lived access token: "
        "no gcloud CLI and no application-default-credentials file exist on this host "
        "(re-checked 2026-09-19), so there is nothing to sign a request with. A bare API key "
        "would not authorise this endpoint",
        note="kept separate from google-gemini on purpose: different auth, different URL shape",
    ),
    # -------------------------------------------------------------------- xAI --
    "xai": ProviderSpec(
        id="xai",
        label="xAI (Grok)",
        docs="https://docs.x.ai/docs",
        endpoints=(
            Endpoint(
                label="OpenAI-compatible endpoint",
                base_url="https://api.x.ai/v1",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=("Authorization: Bearer $XAI_API_KEY",),
                api_key_env=("XAI_API_KEY",),
                status=DOCUMENTED,
                reference="https://docs.x.ai/docs/api-reference",
                fetched=True,
                note="the fetched reference curl's https://api.x.ai/v1/responses with "
                "'Authorization: Bearer'; the Python example sets base_url="
                '"https://api.x.ai/v1"',
            ),
        ),
        model_patterns=("grok-*", "grok-*-mini", "grok-*-vision*"),
        default_model="",
        streaming=_documented(
            SUPPORT_YES,
            "https://docs.x.ai/docs/api-reference",
            "the reference documents streaming chat plus responses/compact; not re-read in full",
        ),
        native_json_schema=_documented(
            SUPPORT_YES,
            "https://docs.x.ai/docs/developers/model-capabilities/text/structured-outputs",
            "the fetched page: setting response_format.type to json_schema with the schema under "
            "response_format.json_schema defines the output exactly; strict:true is shown",
            fetched=True,
        ),
        json_object=_unverified(
            "the structured-outputs page documents the json_schema form; a bare json_object "
            "form is not stated either way",
            reference="https://docs.x.ai/docs/developers/model-capabilities/text/"
            "structured-outputs",
        ),
        vision=_documented(
            SUPPORT_PER_MODEL,
            "https://docs.x.ai/docs/models",
            "vision is a per-model property (the grok vision models); the models page was not "
            "fetched this round",
        ),
        tools=_documented(
            SUPPORT_YES,
            "https://docs.x.ai/docs/developers/model-capabilities/text/structured-outputs",
            "the fetched page: tool-call arguments always conform strictly to the tool's input "
            "schema and `strict` is implicitly always true",
            fetched=True,
        ),
        quirks=(
            "tool arguments are strict by default — `strict` cannot be turned off there",
            "the reference puts /responses first and /chat/completions second; a client that "
            "only speaks /chat/completions still works, but the documented default has moved",
        ),
        licence="hosted API governed by xAI's terms; not fetched this round",
        licence_reference="https://x.ai/legal/terms-of-service",
        probe=PROBE_NOT_RUN,
        probe_reason=_NOT_RUN_XAI,
        note="",
    ),
    # --------------------------------------------------------------- DeepSeek --
    "deepseek": ProviderSpec(
        id="deepseek",
        label="DeepSeek",
        docs="https://api-docs.deepseek.com/",
        endpoints=(
            Endpoint(
                label="OpenAI-compatible endpoint",
                base_url="https://api.deepseek.com/v1",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=("Authorization: Bearer $DEEPSEEK_API_KEY",),
                api_key_env=("DEEPSEEK_API_KEY",),
                status=VERIFIED,
                reference="probe:deepseek",
                fetched=True,
                note="GET /models answered 200 with the real key and 401 with a wrong one — "
                "measured 2026-09-18 and re-measured 2026-09-19",
            ),
        ),
        model_patterns=(
            "deepseek-flash",
            "deepseek-v4-pro",
            "deepseek-chat",
            "deepseek-reasoner",
            "deepseek-*",
        ),
        default_model="deepseek-flash",
        streaming=_verified(
            SUPPORT_YES,
            "deepseek/stream/2026-09-19",
            "stream:true answered 200 with `text/event-stream; charset=utf-8`, 26 data: events "
            "and real text (first event after 296 ms) on both ids asked. A stream reader must "
            "read delta.reasoning_content as well as delta.content: the reasoner route carried "
            "106 chars of answer there with content empty",
        ),
        native_json_schema=_verified(
            SUPPORT_NO,
            "deepseek/json_schema_native/2026-09-19",
            'MEASURED REFUSAL, reproduced: HTTP 400 {"error":{"message":"This response_format '
            'type is unavailable now"}} on both model ids, on 2026-09-19 and again on '
            "2026-09-18. The fetched json_mode page documents json_object only. This is the "
            "claim the live probe corrected — never report native json_schema for DeepSeek",
        ),
        json_object=_verified(
            SUPPORT_YES,
            "deepseek/json_object/2026-09-19",
            'the documented ceiling answered 200 and parsed: {"probe":1,"answer":"pong"}. On the '
            "reasoner id the SAME request at max_tokens=96 answered 200 with content_chars=0 and "
            "392 chars of reasoning_content (a 200 is not a structured answer), and at "
            "max_tokens=400 it answered valid JSON beside 323 chars of reasoning — so the "
            "failure was a token budget, the capability is real, and the check that matters is "
            "'does the 200 body parse', not 'was the status 200'",
        ),
        vision=_verified(
            SUPPORT_PER_MODEL,
            "deepseek/vision/2026-09-19",
            "a genuine 64x64 truecolour PNG in an image_url part was accepted and answered "
            '"Gradient" on BOTH ids this round asked (deepseek-chat and deepseek-reasoner). A '
            "hand-typed 1x1 PNG is refused as an unsupported image first, so the smallest "
            "possible payload is not a fair probe — image support exists on the wire and is not "
            "a documented capability",
        ),
        tools=_documented(
            SUPPORT_YES,
            "https://api-docs.deepseek.com/guides/function_calling",
            "function calling is documented for both chat models; not probed this round either, "
            "so it stays documented and is NOT upgraded to verified",
        ),
        quirks=(
            "the base URL is the bare host too: https://api.deepseek.com also answers, /v1 is "
            "the OpenAI-shaped path",
            "native json_schema is refused with a 400 saying the response_format type is "
            "unavailable — the refusal text is the evidence, so a client must fall back to "
            "json_object + a prompted schema instead of retrying. It reproduced on 2026-09-19, "
            "so it is not an outage",
            "an image smaller than a real picture is refused as 'unsupported image', which reads "
            "like a format error but is a payload problem",
            "the alias you ask for is not the id you get back: deepseek-chat and deepseek-reasoner "
            "both ECHO as `deepseek-flash`, and GET /models now lists only deepseek-flash and "
            "deepseek-v4-pro — so model ids here are aliases that resolve, never strings a client "
            "may compare",
            "a REASONING-ONLY answer is reachable here: with a small max_tokens the reasoner id "
            "spends the whole budget on reasoning_content, returns HTTP 200 with content_chars=0, "
            "and finish_reason=length. Given room it fills both fields (measured: 4 chars of "
            "content beside 396 of reasoning). A client that reads only `content` reports an "
            "empty answer for a request that succeeded",
            "the same reasoning-only shape reaches the structured-output path, and it is a BUDGET "
            "trap rather than a capability limit: json_object on the reasoner id answered 200 "
            "with empty content at max_tokens=96 and valid JSON at 400 — so `200 OK` and `valid "
            "JSON` are two different checks, both needed, and a small max_tokens is enough to "
            "manufacture a false 'structured output is broken here'",
        ),
        licence="hosted API governed by DeepSeek's terms; not fetched this round",
        licence_reference="https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html",
        probe=PROBE_RUN,
        probe_reason="",
        note="the owner's measured default endpoint; re-probed end to end on 2026-09-19 — the "
        "native json_schema refusal reproduced, and the reasoning-content trap was measured "
        "deliberately rather than stumbled into",
    ),
    # --------------------------------------------------------------- Moonshot --
    "moonshot": ProviderSpec(
        id="moonshot",
        label="Moonshot AI (Kimi)",
        docs="https://platform.moonshot.ai/docs",
        endpoints=(
            Endpoint(
                label="OpenAI-compatible endpoint (international)",
                base_url="https://api.moonshot.ai/v1",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=("Authorization: Bearer $MOONSHOT_API_KEY",),
                api_key_env=("MOONSHOT_API_KEY",),
                status=DOCUMENTED,
                reference="https://platform.moonshot.ai/docs/api/chat",
                fetched=True,
                note="every example on the fetched page uses "
                "https://api.moonshot.ai/v1/chat/completions with 'Authorization: Bearer' and "
                "os.environ['MOONSHOT_API_KEY']",
            ),
            Endpoint(
                label="OpenAI-compatible endpoint (China mainland)",
                base_url="https://api.moonshot.cn/v1",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=("Authorization: Bearer $MOONSHOT_API_KEY",),
                api_key_env=("MOONSHOT_API_KEY",),
                status=DOCUMENTED,
                reference="https://platform.moonshot.cn/docs/api/chat",
                note="the .cn host is the mainland endpoint; it uses the SAME variable NAME but "
                "the two platforms issue separate keys (we do not invent a second variable "
                "name), and this page was NOT fetched this round",
            ),
        ),
        model_patterns=("kimi-*", "moonshot-v1-*", "kimi-*-k2*", "kimi-*-k3*"),
        default_model="",
        streaming=_documented(
            SUPPORT_YES,
            "https://platform.moonshot.ai/docs/api/chat",
            "the fetched page: stream:true returns Server-Sent Events; "
            "stream_options {include_usage: true} adds a final usage chunk",
            fetched=True,
        ),
        native_json_schema=_documented(
            SUPPORT_YES,
            "https://platform.moonshot.ai/docs/api/chat",
            "the fetched page documents {'type': 'json_schema', 'json_schema': {...}} as "
            "'outputs structured data according to the given JSON Schema' — documented, not "
            "measured by us",
            fetched=True,
        ),
        json_object=_documented(
            SUPPORT_YES,
            "https://platform.moonshot.ai/docs/api/chat",
            "the fetched page documents {'type': 'json_object'} and warns that the expected "
            "fields must be described in the prompt",
            fetched=True,
        ),
        vision=_documented(
            SUPPORT_PER_MODEL,
            "https://platform.moonshot.ai/docs/api/chat",
            "content parts `image_url` (object or bare string) and `video_url` are documented; "
            "the page links 'Use the Kimi Vision Model', so it is per-model",
            fetched=True,
        ),
        tools=_documented(
            SUPPORT_YES,
            "https://platform.moonshot.ai/docs/api/chat",
            "tools are part of the Chat Completions surface; not re-read in full",
        ),
        quirks=(
            "two platforms, two hosts and two key sets: api.moonshot.ai (international) vs "
            "api.moonshot.cn (mainland) — a key from one is not accepted by the other, "
            "and both are read from MOONSHOT_API_KEY (the platform is the switch, not "
            "the variable name)",
            "the same API also exposes a Responses and a Messages (Anthropic-shaped) surface",
            "json_object without a described field list 'may produce unexpected results' — the "
            "prompt carries the schema, not the response_format",
            "prompt caching is reported per token class: cached_tokens + cache_write_tokens + "
            "the uncached remainder sum to prompt_tokens",
        ),
        licence="hosted API governed by Moonshot's terms; not fetched this round",
        licence_reference="https://platform.moonshot.ai/docs/agreement",
        probe=PROBE_NOT_RUN,
        probe_reason=_NOT_RUN_MOONSHOT,
        note="native json_schema is documented here, which is the opposite of DeepSeek — worth "
        "a probe as soon as a key is available",
    ),
    # ---------------------------------------------------------- Alibaba Qwen ---
    "alibaba-qwen": ProviderSpec(
        id="alibaba-qwen",
        label="Alibaba Qwen (Model Studio / DashScope)",
        docs="https://www.alibabacloud.com/help/en/model-studio/",
        endpoints=(
            Endpoint(
                label="OpenAI-compatible mode (Beijing / China mainland)",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=("Authorization: Bearer $DASHSCOPE_API_KEY",),
                api_key_env=("DASHSCOPE_API_KEY",),
                status=DOCUMENTED,
                reference="https://www.alibabacloud.com/help/en/model-studio/"
                "compatibility-of-openai-with-dashscope",
                note="the /compatible-mode/v1 suffix is what makes DashScope speak the OpenAI "
                "shape; the page names the per-region hosts and warns that a key is bound to "
                "the region it was created in",
            ),
            Endpoint(
                label="OpenAI-compatible mode (Singapore, workspace-scoped)",
                base_url="https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=("Authorization: Bearer $DASHSCOPE_API_KEY",),
                api_key_env=("DASHSCOPE_API_KEY",),
                status=DOCUMENTED,
                reference="https://www.alibabacloud.com/help/en/model-studio/"
                "compatibility-of-openai-with-dashscope",
                fetched=True,
                note="the fetched page lists Singapore / Virginia / Hong Kong (China) / "
                "Japan (Tokyo) hosts of the same shape; Virginia is the non-workspace "
                "dashscope-us.aliyuncs.com",
            ),
        ),
        model_patterns=("qwen*", "qwen3*", "qwen-*-plus", "qwen-*-turbo", "deepseek-*"),
        default_model="",
        streaming=_documented(
            SUPPORT_YES,
            "https://www.alibabacloud.com/help/en/model-studio/"
            "compatibility-of-openai-with-dashscope",
            "the fetched page documents stream:true plus stream_options {include_usage}",
            fetched=True,
        ),
        native_json_schema=_unverified(
            "the fetched compatibility page did not state a json_schema form; treat native "
            "structured output as unproven until a probe says otherwise",
            reference="https://www.alibabacloud.com/help/en/model-studio/"
            "compatibility-of-openai-with-dashscope",
        ),
        json_object=_unverified(
            "not stated on the page this round fetched",
            reference="https://www.alibabacloud.com/help/en/model-studio/"
            "compatibility-of-openai-with-dashscope",
        ),
        vision=_documented(
            SUPPORT_PER_MODEL,
            "https://www.alibabacloud.com/help/en/model-studio/"
            "compatibility-of-openai-with-dashscope",
            "the fetched page lists vision-language Qwen models; the page also warns that "
            "Qwen-Audio does NOT support the OpenAI-compatible protocol",
            fetched=True,
        ),
        tools=_unverified(
            "not stated on the page this round fetched",
            reference="https://www.alibabacloud.com/help/en/model-studio/"
            "compatibility-of-openai-with-dashscope",
        ),
        quirks=(
            "an API key is bound to the region it was created in: a Beijing key against the "
            "Virginia endpoint answers HTTP 401 with error code `invalid_api_key` — that 401 is "
            "a region mismatch, not a bad key",
            "the regional hosts are being migrated: dashscope-intl.aliyuncs.com -> "
            "{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com, so the 'stable' URL is not stable",
            "non-chat models are reachable through the same base (embeddings, rerank); audio "
            "models are the documented exception and need the DashScope protocol",
        ),
        licence="hosted API governed by Alibaba Cloud Model Studio terms; not fetched this round",
        licence_reference="https://www.alibabacloud.com/help/en/legal/latest/alibaba-cloud-international-website-product-terms-of-service",
        licence_fetched=False,
        probe=PROBE_NOT_RUN,
        probe_reason=_NOT_RUN_QWEN,
        note="the OpenAI-compatible mode exists so OpenAI code runs unchanged; the native "
        "DashScope protocol is a different shape",
    ),
    # ------------------------------------------------------------- OpenRouter --
    "openrouter": ProviderSpec(
        id="openrouter",
        label="OpenRouter (router / aggregator)",
        docs="https://openrouter.ai/docs",
        endpoints=(
            Endpoint(
                label="OpenAI-compatible aggregation endpoint",
                base_url="https://openrouter.ai/api/v1",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=(
                    "Authorization: Bearer $OPENROUTER_API_KEY",
                    "HTTP-Referer: <app URL>",
                    "X-Title: <app name>",
                ),
                api_key_env=("OPENROUTER_API_KEY",),
                status=VERIFIED,
                reference="probe:openrouter",
                fetched=True,
                note="GET /models answered 200 listing 447 models with the real key (445 on "
                "2026-09-18); a wrong key answered 401 (measured 2026-09-18 and 2026-09-19). "
                "HTTP-Referer/X-Title are optional attribution headers, not credentials",
            ),
        ),
        model_patterns=("<vendor>/<model>", "*/<model>:free", "openrouter/auto"),
        default_model="",
        streaming=_verified(
            SUPPORT_YES,
            "openrouter/stream/2026-09-19",
            'text/event-stream with 7 to 27 data: events per route and real text delivered '
            '("one two three") on nex-agi/nex-n2.5-mini:free. On the reasoning route the deltas '
            'carried `reasoning` (plus reasoning_details) and NEVER `content`, so the stream was '
            "healthy while a content-only reader saw it as empty — the field name is the trap",
        ),
        native_json_schema=_verified(
            SUPPORT_PER_MODEL,
            "openrouter/json_schema_native/2026-09-19",
            'measured BOTH WAYS on the same router within one minute: 200 with {"answer":"pong",'
            '"probe":1} on nex-agi/nex-n2.5-mini:free, and 400 "model features structured outputs '
            'not support" (upstream named as Novita) on inclusionai/ling-3.0-flash-vl:free. '
            "Structured output here is per-model, which is what the docs claim and what the "
            "measurement now shows",
        ),
        json_object=_verified(
            SUPPORT_PER_MODEL,
            "openrouter/json_object/2026-09-19",
            '{ "probe": 1, "answer": "pong" } at 200 on one route, and 400 "model: '
            'inclusionai/ling-3.0-flash-vl does not support feature: structured-outputs" on '
            "another — even the fallback mode is a property of the routed model, not of the router",
        ),
        vision=_verified(
            SUPPORT_PER_MODEL,
            "openrouter/vision/2026-09-19",
            "a genuine 64x64 truecolour PNG in an image_url part was ACCEPTED on "
            "inclusionai/ling-3.0-flash-vl:free (200, and it described 'a color gradient' in 280 "
            "chars of reasoning_content) and REFUSED on nex-agi/nex-n2.5-mini:free (400 'The "
            "request is invalid.' from upstream Nex AGI) — vision is measured per-model in both "
            "directions, so no claim about it can be made about the router as such",
        ),
        tools=_documented(
            SUPPORT_PER_MODEL,
            "https://openrouter.ai/docs/api_reference/overview",
            "tools/tool_choice are passed through to the routed provider; not probed this round, "
            "so it stays documented rather than verified",
        ),
        quirks=(
            "a model id is `<vendor>/<model>`, and a `:free` suffix selects the free route — a "
            "route that is also the most rate-limited one AND one that can be retired: asking for "
            "deepseek/deepseek-chat:free on 2026-09-19 answered 404 'This model is unavailable for "
            "free ... use this slug instead: deepseek/deepseek-chat'. A model id is configuration, "
            "not a constant",
            "per-provider routing preferences matter: `require_parameters: true` plus "
            "`response_format` in the required parameters is how the docs tell you to guarantee "
            "a structured-output-capable route is chosen — the 400s measured on one route beside "
            "the 200s on another are that advice as a measurement",
            "enforcement of `strict: true` varies by upstream provider: some guarantee "
            "schema-conforming output, others only translate the schema into a prompt",
            "a rate limit arrives as HTTP 429 'Provider returned error' with the UPSTREAM "
            "provider named in metadata and `is_byok` telling you whether your own key was used",
            "REASONING FIELD ASYMMETRY: a non-streaming message can carry `reasoning_content`, "
            "while a streaming delta carries `reasoning` (with a parallel `reasoning_details` "
            "list) and leaves `content` an empty string. A stream client that reads only "
            "delta.content reports a working route as an empty answer",
            "there is a Response Healing plugin for non-streaming json_schema requests — a "
            "post-hoc repair, not a guarantee",
        ),
        licence="the router's own terms plus each routed model's licence; not fetched",
        licence_reference="https://openrouter.ai/terms",
        probe=PROBE_RUN,
        probe_reason="",
        note="an aggregator: a capability claim here is a claim about the routed model, and "
        "`skaldir provider catalog` reports it as per-model on purpose. The 2026-09-19 round "
        "measured both answers of every per-model pair on the same router within a minute",
    ),
    # ------------------------------------------------------------------ Z.ai ---
    "zai": ProviderSpec(
        id="zai",
        label="Z.ai (GLM)",
        docs="https://docs.z.ai/",
        endpoints=(
            Endpoint(
                label="OpenAI-compatible endpoint (paas v4)",
                base_url="https://api.z.ai/api/paas/v4",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=(
                    "Authorization: Bearer $ZAI_API_KEY",
                    "Accept-Language: en-US,en",
                ),
                api_key_env=("ZAI_API_KEY", "ZHIPUAI_API_KEY"),
                status=DOCUMENTED,
                reference="https://docs.z.ai/api-reference/introduction",
                fetched=True,
                note="the fetched page: 'The Z.AI API uses the standard HTTP Bearer for "
                "authentication', endpoint https://api.z.ai/api/paas/v4, curl POSTs "
                ".../chat/completions; the Python example reads ZAI_API_KEY",
                sku=SKU_PAY_AS_YOU_GO,
            ),
            Endpoint(
                label="coding-plan endpoint (a separate base path)",
                base_url="https://api.z.ai/api/coding/paas/v4",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=("Authorization: Bearer $ZAI_API_KEY",),
                api_key_env=("ZAI_API_KEY",),
                status=DOCUMENTED,
                reference="https://docs.z.ai/devpack/tool/others",
                note="the devpack page points tools at /api/coding/paas/v4 — a different base "
                "path from /api/paas/v4, and the page was not fetched this round",
                sku=SKU_CODING_PLAN,
            ),
        ),
        model_patterns=("glm-*", "glm-*-flash", "glm-*-air", "glm-*-flashx"),
        default_model="",
        streaming=_documented(
            SUPPORT_YES,
            "https://docs.z.ai/api-reference/introduction",
            "the fetched introduction's own curl sets \"stream\": true",
            fetched=True,
        ),
        native_json_schema=_documented(
            SUPPORT_NO,
            "https://docs.z.ai/guides/capabilities/struct-output",
            "the fetched structured-output page documents response_format "
            "{'type': 'json_object'} ONLY and enumerates the models that support it — no "
            "json_schema form is documented, so the documented ceiling here is json_object",
            fetched=True,
        ),
        json_object=_documented(
            SUPPORT_YES,
            "https://docs.z.ai/guides/capabilities/struct-output",
            "the fetched page: set response_format to {'type': 'json_object'} on a model that "
            "supports structured output (glm-5, glm-4.7, glm-4.6, glm-4.5, …)",
            fetched=True,
        ),
        vision=_documented(
            SUPPORT_PER_MODEL,
            "https://docs.z.ai/api-reference/llm/chat-completion",
            "the chat-completions reference has a Vision Model section; the page was not "
            "fetched in full this round",
        ),
        tools=_unverified(
            "the reference lists tools/tool_choice but this round did not read that section",
            reference="https://docs.z.ai/api-reference/llm/chat-completion",
        ),
        quirks=(
            "two base paths for one product: /api/paas/v4 (general) and /api/coding/paas/v4 "
            "(coding subscription) — pointing at the wrong one is a 404, not a 401",
            "the docs' own curl sends Accept-Language, so a client that sets nothing may get a "
            "different default language",
            "json_object is documented to require the expected shape in the prompt, like "
            "DeepSeek's ceiling",
        ),
        licence="hosted API governed by Z.ai's terms; not fetched this round",
        licence_reference="https://docs.z.ai/terms",
        probe=PROBE_NOT_RUN,
        probe_reason=_NOT_RUN_ZAI,
        note="same documented ceiling as DeepSeek (json_object, no native json_schema)",
    ),
    # ----------------------------------------------------------- Xiaomi MiMo ---
    "xiaomi-mimo": ProviderSpec(
        id="xiaomi-mimo",
        label="Xiaomi MiMo",
        docs="https://mimo.mi.com/docs",
        endpoints=(
            Endpoint(
                label="OpenAI-compatible endpoint (pay-as-you-go)",
                base_url="https://api.xiaomimimo.com/v1",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=(
                    "Authorization: Bearer $MIMO_API_KEY",
                    "api-key: $MIMO_API_KEY",
                ),
                api_key_env=("MIMO_API_KEY",),
                status=DOCUMENTED,
                reference="https://mimo.mi.com/docs/quick-start/first-api-call",
                fetched=True,
                note="the fetched page sets the OpenAI SDK base_url to "
                "https://api.xiaomimimo.com/v1 with MIMO_API_KEY, while its curl example "
                "sends an `api-key:` header — both are shown, so a client should accept both",
                sku=SKU_PAY_AS_YOU_GO,
            ),
            Endpoint(
                label="OpenAI-compatible endpoint (token plan, China)",
                base_url="https://token-plan-cn.xiaomimimo.com/v1",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=("api-key: $MIMO_API_KEY",),
                api_key_env=("MIMO_API_KEY",),
                status=DOCUMENTED,
                reference="https://mimo.mi.com/docs/quick-start/first-api-call",
                fetched=True,
                note="the fetched table lists https://token-plan-cn.xiaomimimo.com/v1 as the "
                "Token Plan base URL — a subscription SKU on a different host from the "
                "pay-as-you-go one. A `tp-` prefixed key belongs here (measured 2026-09-23: "
                "a tp- key on the pay-as-you-go host is a 401 Invalid API Key)",
                sku=SKU_TOKEN_PLAN,
                key_prefixes=("tp-",),
            ),
        ),
        model_patterns=("mimo-*", "mimo-*-pro", "mimo-*-flash", "mimo-v*"),
        default_model="",
        streaming=_documented(
            SUPPORT_YES,
            "https://mimo.mi.com/docs/quick-start/first-api-call",
            "the fetched example passes stream=False explicitly, implying stream=True is "
            "supported; not confirmed by a stream example on that page",
        ),
        native_json_schema=_unverified(
            "the quick-start page does not document response_format at all",
            reference="https://mimo.mi.com/docs/quick-start/first-api-call",
        ),
        json_object=_unverified(
            "the quick-start page does not document response_format at all",
            reference="https://mimo.mi.com/docs/quick-start/first-api-call",
        ),
        vision=_documented(
            SUPPORT_PER_MODEL,
            "https://openrouter.ai/xiaomi/mimo-v2.5",
            "MiMo V2.5 is offered as a multimodal model (image_url parts) by third parties; the "
            "vendor page fetched here does not state it, so this is per-model and weak",
        ),
        tools=_documented(
            SUPPORT_YES,
            "https://mimo.mi.com/docs/quick-start/first-api-call",
            "the fetched page shows a tool-calling request with tools + tool_choice and a "
            "reasoning_content message field",
            fetched=True,
        ),
        quirks=(
            "the docs show two auth spellings for one endpoint (Bearer via the OpenAI SDK, "
            "`api-key:` header via curl) — accept both rather than assume",
            "the token-plan host is different from the pay-as-you-go host, and the plan host is "
            "region-suffixed (token-plan-cn)",
            "an Anthropic-compatible surface exists at https://api.xiaomimimo.com/anthropic",
            "reasoning models return a `reasoning_content` field alongside content; a client "
            "that echoes assistant messages back must carry it or get a 400",
        ),
        licence="hosted API governed by Xiaomi's MiMo platform terms; not fetched this round",
        licence_reference="https://mimo.mi.com/docs",
        probe=PROBE_NOT_RUN,
        probe_reason=_NOT_RUN_XIAOMI,
        note="a plan SKU and a pay-as-you-go SKU with different hosts and no shared base URL",
    ),
    # ------------------------------------------------------- OpenAI Codex plan ---
    "openai-codex": ProviderSpec(
        id="openai-codex",
        label="OpenAI Codex (API / ChatGPT plan)",
        docs="https://developers.openai.com/codex/",
        endpoints=(
            Endpoint(
                label="OpenAI API (Codex models, pay-as-you-go)",
                base_url="https://api.openai.com/v1",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=("Authorization: Bearer $OPENAI_API_KEY",),
                api_key_env=("OPENAI_API_KEY",),
                status=DOCUMENTED,
                reference="https://developers.openai.com/codex/",
                fetched=False,
                note="gpt-*-codex* model ids on the ordinary OpenAI API with OPENAI_API_KEY. "
                "This is the pay-as-you-go SKU of Codex — the ChatGPT Plus/Pro *plan* is a "
                "different credential (OAuth in the Codex CLI), not an API key",
                sku=SKU_PAY_AS_YOU_GO,
            ),
            Endpoint(
                label="ChatGPT plan backend (Codex CLI OAuth — not an API key)",
                base_url="https://chatgpt.com/backend-api/codex",
                kind=KIND_NATIVE,
                chat_path="",
                auth_header=("Authorization: Bearer <ChatGPT OAuth access token>",),
                api_key_env=(),
                status=DOCUMENTED,
                reference="https://developers.openai.com/codex/",
                fetched=False,
                note="the Codex CLI's ChatGPT-subscription path: OAuth access token from the "
                "CLI login (~/.codex/auth.json), not a long-lived API key. skaldir does not "
                "yet perform the OAuth device flow — a ChatGPT-plan user pastes the access "
                "token into OPENCODE-style env only while it is valid, or uses the API SKU",
                sku=SKU_CHATGPT_PLAN,
            ),
        ),
        model_patterns=("gpt-*-codex*", "codex-*"),
        default_model="",
        streaming=_documented(
            SUPPORT_YES,
            "https://developers.openai.com/codex/",
            "Codex models on the OpenAI API speak stream:true like the rest of the chat API",
        ),
        tools=_documented(
            SUPPORT_YES,
            "https://developers.openai.com/codex/",
            "Codex is a tool-calling coding model; the API surface carries tools/tool_choice",
        ),
        quirks=(
            "the ChatGPT plan and the API key are two different credentials for two "
            "different bases — mixing them is a 401 that looks like a bad key",
            "Codex model ids are gpt-*-codex*; the ordinary gpt-* ids are a different SKU",
        ),
        licence="hosted API governed by OpenAI's terms; not fetched this round",
        licence_reference="https://openai.com/policies/terms-of-use/",
        probe=PROBE_NOT_RUN,
        probe_reason="No completion probe in the catalog refresh (OPENAI_API_KEY path is the "
        "same shape as the already-probed openai entry; the ChatGPT OAuth path is not wired).",
        note="plan-aware: pick the API SKU for OPENAI_API_KEY, the ChatGPT-plan SKU only when "
        "you hold a Codex CLI OAuth token",
    ),
    # ----------------------------------------------------------- OpenCode Go ---
    "opencode-go": ProviderSpec(
        id="opencode-go",
        label="OpenCode Go",
        docs="https://opencode.ai/docs/go/",
        endpoints=(
            Endpoint(
                label="OpenAI-compatible endpoint (subscription aggregator)",
                base_url="https://opencode.ai/zen/go/v1",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=("Authorization: Bearer $OPENCODE_GO_API_KEY",),
                api_key_env=("OPENCODE_GO_API_KEY", "OPENCODE_API_KEY"),
                status=VERIFIED,
                reference="probe:opencode-go",
                fetched=True,
                note="GET /models answered 200 — 37 model ids on 2026-09-19 — with the real key, "
                "and 401 with a wrong one sent against a REAL model id (AuthError 'Invalid API "
                "key.'). Measured 2026-09-18 and re-measured 2026-09-19; the docs table names "
                "https://opencode.ai/zen/go/v1/chat/completions for every model",
                sku=SKU_SUBSCRIPTION,
            ),
        ),
        model_patterns=(
            "minimax-*",
            "kimi-*",
            "glm-*",
            "longcat-*",
            "deepseek-*",
            "qwen*",
            "mimo-*",
            "hy*",
        ),
        default_model="",
        streaming=_documented(
            SUPPORT_PER_MODEL,
            "https://opencode.ai/docs/go/",
            "the endpoint is declared @ai-sdk/openai-compatible, which speaks stream:true, but "
            "our own stream probe was blocked by the plan quota (429), so this is not measured",
        ),
        native_json_schema=_documented(
            SUPPORT_PER_MODEL,
            "https://opencode.ai/docs/go/",
            "the upstream models differ (GLM / Kimi / DeepSeek / Qwen / MiniMax) and their "
            "capabilities differ with them; the aggregator documents no response_format support "
            "of its own",
        ),
        json_object=_documented(
            SUPPORT_PER_MODEL,
            "https://opencode.ai/docs/go/",
            "inherited from the upstream model; not documented by the aggregator, not measured",
        ),
        vision=_documented(
            SUPPORT_PER_MODEL,
            "https://opencode.ai/docs/go/",
            "the model table carries a dedicated "
            "'DeepSeek V4 Flash Vision Exp' entry, so vision is per-model here too",
        ),
        tools=_documented(
            SUPPORT_PER_MODEL,
            "https://opencode.ai/docs/go/",
            "@ai-sdk/openai-compatible passes tools through; per upstream model",
        ),
        quirks=(
            "model ids are bare (`deepseek-v4.1-flash`), NOT vendor-prefixed — this router is "
            "the opposite of OpenRouter's `<vendor>/<model>` convention",
            "the plan has a WEEKLY quota: the measured refusal was HTTP 429 "
            '{"type":"GoUsageLimitError","message":"Weekly usage limit reached. Resets in '
            '… days."} — a 429 here means the plan is exhausted, not that the key is wrong. '
            "Re-measured on 2026-09-19 with 'Resets in 1 day' after 'Resets in 2 days' on "
            "2026-09-18: the same weekly window, observed twice",
            "peak/off-peak pricing applies to the DeepSeek routes on a published UTC schedule",
            "an Anthropic-compatible surface also exists at https://opencode.ai/zen/go, and the "
            "Docker docs use OPENCODE_API_KEY for it — two spellings of one credential",
            "some Hermes-side configs name OPENCODE_GO_BASE_URL, so a base URL can be "
            "overridden rather than configured per call",
            "AUTH-CONTROL TRAP, measured: a wrong key sent with a model id the service does not "
            'know answers 401 {"type":"ModelError","message":"Model probe is not supported"} — a '
            "401 that is about the MODEL NAME, not the credential. The same wrong key against a "
            'real id answers 401 {"type":"AuthError","message":"Invalid API key."}. An auth '
            "control is only evidence when it sends a real model id",
            "reasoning models here return a `reasoning_content` field beside content, and the "
            "upstream models are the ones whose probe would have to watch both fields — the "
            "quota is what stopped that measurement, so it is recorded as unmeasured, not as "
            "supported",
        ),
        licence="subscription service under OpenCode's terms; the upstream models keep their "
        "own licences; neither was fetched this round",
        licence_reference="https://opencode.ai/docs/go/",
        probe=PROBE_PARTIAL,
        probe_reason="the credential and the base URL are verified (GET /models 200 with 37 ids, "
        "401 AuthError with a wrong key against a real model id); every completion probe was "
        "blocked again by the plan's weekly quota (HTTP 429 GoUsageLimitError, 'Resets in 1 "
        "day'), so no capability claim here is measured",
        note="a quota-blocked probe is reported as not-run, never as a failure of the shape. "
        "2026-09-19 was the second round to hit the same wall",
    ),
    # ------------------------------------------- the decision layer: TypeSafe/Jev ---
    "typesafe": ProviderSpec(
        id="typesafe",
        label="TypeSafe (System One / Jev)",
        docs="https://docs.typesafe.ai/api",
        endpoints=(
            Endpoint(
                label="System One evaluation endpoint — NOT an OpenAI chat endpoint",
                base_url=TYPESAFE_ENDPOINT,
                kind=KIND_NATIVE,
                # deliberately empty: this is the whole path, and there is no
                # /chat/completions beside it to append
                chat_path="",
                auth_header=("Authorization: Bearer $TYPESAFE_API_KEY",),
                api_key_env=("TYPESAFE_API_KEY",),
                status=DOCUMENTED,
                reference="https://docs.typesafe.ai/api",
                fetched=True,
                note="POST https://api.typesafe.ai/v1/systemone with 'Authorization: Bearer "
                "<API_KEY>'. The request body is exactly {model, state, questions}; 'state' is "
                "'string | object | array' and 'questions' is a map<string, Question> of "
                "noul/choice/score questions, answered under the same ids with calibrated "
                "probabilities. Measured 2026-09-19 (no credential sent): HTTP 403 "
                '{"detail":{"error_type":"authentication_error","message":"Must supply an API '
                'key! ..."}} — reachable, auth-gated, and NOT verified, because an unauthorised '
                "refusal proves the gate and nothing about the answers",
            ),
        ),
        model_patterns=("jev-latest", "jev-<version> (e.g. jev-1.13.0, seen in the examples)"),
        default_model=TYPESAFE_MODEL,
        streaming=_documented(
            SUPPORT_NO,
            "https://docs.typesafe.ai/api",
            "one POST returns one complete decision document ('get back structured answers, one "
            "per question'); the documented body carries no stream field and the answer is a "
            "sampled-in-one-query typed value — there is no token stream to follow",
            fetched=True,
        ),
        native_json_schema=_documented(
            SUPPORT_NO,
            "https://docs.typesafe.ai/api",
            "there is no response_format field in the documented body: the answer shape is fixed "
            "by the question types (noul/choice/score) and validated by the provider, which is "
            "the point of the endpoint — a client cannot ask for a schema of its own",
            fetched=True,
        ),
        json_object=_documented(
            SUPPORT_NO,
            "https://docs.typesafe.ai/api",
            "same reason as native_json_schema: answers ARE a JSON document, but the shape is "
            "chosen by the Question map, not requested through response_format (which the "
            "endpoint does not document). `build_typesafe_request` sends exactly "
            "DOCUMENTED_REQUEST_FIELDS = (model, state, questions) and nothing else",
            fetched=True,
        ),
        vision=_documented(
            SUPPORT_NO,
            "https://docs.typesafe.ai/concepts/system-one",
            "the concepts page says it plainly: 'Jev currently accepts text input only. It "
            "evaluates strings, JSON objects, and arrays of text. Images, audio, and video are "
            "not supported (yet).'",
            fetched=True,
        ),
        tools=_documented(
            SUPPORT_NO,
            "https://docs.typesafe.ai/api",
            "no tools/tool_choice field is documented and the body may carry nothing but model / "
            "state / questions: Jev returns typed decisions for a harness to act on, it does not "
            "call tools",
            fetched=True,
        ),
        quirks=(
            "the request body is {model, state, questions} — 'questions' is a MAP keyed by the "
            "question id you choose (map<string, Question>), NOT an array, and the answers come "
            "back under the same keys. Our adapter's own shape is "
            "skaldir.system_one_adapters.build_typesafe_request (see the Q-1 note in "
            "docs/audit/JEV_PROVIDER_COMPATIBILITY.md)",
            "state: string | object | array (the fetched reference names all three). The docs' "
            "own examples use a plain string; this repo's adapter sends the internal state "
            "envelope by default (state_shape='object')",
            "the response names the model that answered: asking for 'jev-latest' can echo "
            "'jev-1.13.0', so a captured model id is evidence about a revision, not about the "
            "alias",
            "answers carry a 'confidence' for choice/score alongside the probabilities, so a "
            "caller can decide when to escalate — but confidence is derived from the "
            "distribution and is not a correctness guarantee",
            "auth is the ONLY credential path here: the environment variable this catalogue "
            "names (TYPESAFE_API_KEY — the name the public LiteLLM pass-through documents) or "
            "the repo's own Treasury handle secret://typesafe/systemone. With neither, "
            "TypeSafeJevEvaluator stays blocked_external_access by name (system_one_adapters.py) "
            "— no configuration on this host substitutes a DeepSeek key for a TypeSafe one",
            "AUTH-CONTROL TRAP, measured: the docs' error table names 401 for a missing key, the "
            "live endpoint answered 403 authentication_error. Both are the auth gate; the "
            "measured number is the one recorded",
            "the OpenAI-shaped client CANNOT speak this endpoint: there is no /chat/completions "
            "beside /v1/systemone and a chat request would be a 404/422, which is why the "
            "catalogue marks it kind='native' so the chain reports client_cannot_speak_to_kind "
            "instead of attempting a request that cannot work",
        ),
        licence="commercial TypeSafe AI service (early access); terms at "
        "https://typesafe.ai/legal/terms — not fetched this round",
        licence_reference="https://typesafe.ai/legal/terms",
        probe=PROBE_NOT_RUN,
        probe_reason="no TypeSafe credential on this host: TYPESAFE_API_KEY is unset and the "
        "Treasury holds no secret://typesafe/systemone handle, so no authenticated call could be "
        "made. The only measurement taken was a credential-free POST, which answered HTTP 403 "
        "authentication_error (see the typesafe probe step) — reachability, not capability",
        note="the decision layer's own provider (Jev). It is NOT a general model: it answers "
        "typed questions with calibrated probabilities and never writes prose, so it belongs to "
        "the TypeSafe/Jev role and to nothing else. An ordinary LLM can stand in for it (the "
        "LLMSystemOneAdapter compatibility path) and the product must say so when one does",
    ),
    # ------------------------------------------------------ local: llama.cpp ---
    "llamacpp": ProviderSpec(
        id="llamacpp",
        label="llama.cpp (local server)",
        docs="https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md",
        endpoints=(
            Endpoint(
                label="local OpenAI-compatible server",
                base_url="http://127.0.0.1:8080/v1",
                kind=KIND_LOCAL,
                auth_header=("Authorization: Bearer <LLAMA_API_KEY>",),
                api_key_env=("LLAMA_API_KEY",),
                status=DOCUMENTED,
                reference="https://github.com/ggml-org/llama.cpp/blob/master/tools/server/"
                "README.md",
                fetched=True,
                note="the fetched README documents --port (default 8080), --api-key KEY (env "
                "LLAMA_API_KEY, 'default: none'), POST /v1/chat/completions and a "
                "'Schema-constrained JSON response format'",
            ),
        ),
        model_patterns=("*", "<path-or-alias-of-the-loaded-gguf>"),
        default_model="",
        streaming=_documented(
            SUPPORT_YES,
            "https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md",
            "the server speaks the OpenAI chat shape including SSE; the README's argument table "
            "does not restate it",
        ),
        native_json_schema=_documented(
            SUPPORT_YES,
            "https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md",
            "the fetched README lists 'Schema-constrained JSON response format' as a feature and "
            "documents -j/--json-schema and -jf/--json-schema-file plus --grammar (GBNF) — "
            "whether the JSON Schema arrives as response_format or as a server-side flag is "
            "build-dependent, so measure it",
            fetched=True,
        ),
        json_object=_documented(
            SUPPORT_YES,
            "https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md",
            "schema-constrained generation subsumes a plain JSON object constraint",
        ),
        vision=_documented(
            SUPPORT_PER_MODEL,
            "https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md",
            "the README documents multimodal flags (--vision-gemma-4b-default, "
            "--image-min/max-tokens) and a mmproj projection — vision depends on the served "
            "model, not on the server",
            fetched=True,
        ),
        tools=_documented(
            SUPPORT_PER_MODEL,
            "https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md",
            "tool calling depends on the chat template of the loaded model",
        ),
        quirks=(
            "the served model NAME is whatever the server was started with; a client that asks "
            "for a model the server did not load is refused, so an empty model id is a "
            "configuration error",
            "no credential by default ('--api-key ... default: none'): declaring a key variable "
            "for a local server is optional and adding one that is unset must NOT block the call",
            "schema constraints can be given at request time (response_format) or at server "
            "start (-j/--json-schema, --grammar) — only the first is portable",
            "the README also documents a router mode, where the \"model\" field selects the "
            "child instance and /v1/models forwards the loaded model list",
        ),
        licence="MIT (fetched: raw.githubusercontent.com/ggml-org/llama.cpp/master/LICENSE)",
        licence_reference="https://raw.githubusercontent.com/ggml-org/llama.cpp/master/LICENSE",
        licence_fetched=True,
        probe=PROBE_NOT_RUN,
        probe_reason=_NOT_RUN_LOCAL,
        note="no credential required; the local probe found port 8080 closed on this host",
    ),
    # ------------------------------------------------------------- local: vLLM -
    "vllm": ProviderSpec(
        id="vllm",
        label="vLLM (local server)",
        docs="https://docs.vllm.ai/",
        endpoints=(
            Endpoint(
                label="local OpenAI-compatible server",
                base_url="http://127.0.0.1:8000/v1",
                kind=KIND_LOCAL,
                auth_header=("Authorization: Bearer <VLLM_API_KEY>",),
                api_key_env=("VLLM_API_KEY",),
                status=DOCUMENTED,
                reference="https://docs.vllm.ai/en/latest/serving/online_serving.html",
                fetched=True,
                note="the fetched page documents the OpenAI-compatible server with "
                "/v1/chat/completions and /v1/completions; the 8000 default is vLLM's standard "
                "`vllm serve` port and is not restated on that page",
            ),
        ),
        model_patterns=("*", "<hf-repo-id>"),
        default_model="",
        streaming=_documented(
            SUPPORT_YES,
            "https://docs.vllm.ai/en/latest/serving/online_serving.html",
            "an OpenAI-compatible server streams by definition; the page does not restate it",
        ),
        native_json_schema=_documented(
            SUPPORT_PER_MODEL,
            "https://docs.vllm.ai/en/latest/features/structured_outputs.html",
            "the fetched page: structured outputs run through xgrammar or guidance and are "
            "requested via `structured_outputs` (json/regex/choice/grammar); the old "
            "guided_json / guided_decoding_backend fields were REMOVED in v0.12.0, and "
            "`response_format` support depends on the model + backend",
            fetched=True,
        ),
        json_object=_documented(
            SUPPORT_PER_MODEL,
            "https://docs.vllm.ai/en/latest/features/structured_outputs.html",
            "same page: the server can constrain generation to JSON, but whether "
            "response_format:{type:json_object} is honoured depends on the build",
        ),
        vision=_documented(
            SUPPORT_PER_MODEL,
            "https://docs.vllm.ai/en/latest/serving/online_serving.html",
            "multimodal models are served through the same OpenAI-compatible surface; the "
            "fetched page only says the Chat API applies to text-generation models",
        ),
        tools=_documented(
            SUPPORT_YES,
            "https://docs.vllm.ai/en/latest/serving/online_serving.html",
            "the fetched page documents tool calling and warns that parallel_tool_calls:true "
            "(the default) may return more than one call with no ordering guarantee",
            fetched=True,
        ),
        quirks=(
            "the structured-output surface CHANGED: guided_json etc. were removed in v0.12.0 in "
            "favour of structured_outputs — an old client's request is now a usage error, not a "
            "soft downgrade",
            "parallel_tool_calls defaults to true and the docs make no ordering guarantee about "
            "the calls it returns",
            "the served model name is the HF repo id the server was started with",
            "the server exposes /v1/responses as well as /v1/chat/completions, plus batch "
            "endpoints that are NOT the chat shape",
        ),
        licence="Apache-2.0 (fetched: raw.githubusercontent.com/vllm-project/vllm/main/LICENSE)",
        licence_reference="https://raw.githubusercontent.com/vllm-project/vllm/main/LICENSE",
        licence_fetched=True,
        probe=PROBE_NOT_RUN,
        probe_reason=_NOT_RUN_LOCAL,
        note="no credential required; the local probe found port 8000 closed on this host",
    ),
    # -------------------------------------------------------- local: LM Studio -
    "lmstudio": ProviderSpec(
        id="lmstudio",
        label="LM Studio (local server)",
        docs="https://lmstudio.ai/docs/app/api/endpoints/openai",
        endpoints=(
            Endpoint(
                label="local OpenAI-compatible server",
                base_url="http://localhost:1234/v1",
                kind=KIND_LOCAL,
                auth_header=(),
                api_key_env=(),
                status=DOCUMENTED,
                reference="https://lmstudio.ai/docs/developer/openai-compat",
                fetched=True,
                note="the fetched page: 'reuse existing OpenAI clients … by switching up the "
                "base URL', port 1234, base_url http://localhost:1234/v1, POST "
                "/v1/chat/completions",
            ),
        ),
        model_patterns=("<publisher>/<model>", "*"),
        default_model="",
        streaming=_documented(
            SUPPORT_YES,
            "https://lmstudio.ai/docs/developer/openai-compat",
            "an OpenAI-compatible server, so SSE streaming is part of the shape; the fetched "
            "page does not restate it",
        ),
        native_json_schema=_documented(
            SUPPORT_PER_MODEL,
            "https://lmstudio.ai/docs/developer/openai-compat",
            "LM Studio enforces structured output per loaded model; the fetched page names the "
            "endpoints but not the response_format levels, so this is per-model",
        ),
        json_object=_documented(
            SUPPORT_PER_MODEL,
            "https://lmstudio.ai/docs/developer/openai-compat",
            "same: depends on the loaded model",
        ),
        vision=_documented(
            SUPPORT_PER_MODEL,
            "https://lmstudio.ai/docs/developer/openai-compat",
            "the fetched page says Chat Completions serves 'text and images', so image input "
            "is part of the surface; which loaded model can read it is per-model",
            fetched=True,
        ),
        tools=_documented(
            SUPPORT_PER_MODEL,
            "https://lmstudio.ai/docs/developer/openai-compat",
            "tool support follows the loaded model's template; the fetched page does not "
            "enumerate it",
        ),
        quirks=(
            "the API key is decorative: LM Studio accepts any value (or none) — declaring a key "
            "variable for it would be misleading",
            "the server is off until someone starts it in the app; the local probe found port "
            "1234 closed on this host",
            "the model id is the folder name as the app imported it, so the same weights can "
            "have two ids on two machines",
        ),
        licence="proprietary desktop app: the fetched app terms govern the software, and each "
        "downloaded model keeps its own licence",
        licence_reference="https://lmstudio.ai/terms",
        licence_fetched=True,
        probe=PROBE_NOT_RUN,
        probe_reason=_NOT_RUN_LOCAL,
        note="the easiest local target to point a client at, and the one with no auth at all",
    ),
    # ----------------------------------------------------------- local: Ollama --
    "ollama": ProviderSpec(
        id="ollama",
        label="Ollama (local server, OpenAI compatibility)",
        docs="https://docs.ollama.com/api/openai-compatibility",
        endpoints=(
            Endpoint(
                label="local OpenAI-compatible server",
                base_url="http://localhost:11434/v1",
                kind=KIND_LOCAL,
                auth_header=("Authorization: Bearer <any value — IGNORED>",),
                api_key_env=(),
                status=DOCUMENTED,
                reference="https://docs.ollama.com/api/openai-compatibility",
                fetched=True,
                note="the fetched page: base_url http://localhost:11434/v1/ with "
                "api_key='ollama' commented 'required but ignored', and curl to "
                "/v1/chat/completions",
            ),
            Endpoint(
                label="Ollama cloud (hosted) endpoint",
                base_url="https://ollama.com/v1",
                kind=KIND_OPENAI_COMPATIBLE,
                auth_header=("Authorization: Bearer $OLLAMA_API_KEY",),
                api_key_env=("OLLAMA_API_KEY",),
                status=DOCUMENTED,
                reference="https://docs.ollama.com/api/openai-compatibility",
                fetched=True,
                note="the same page documents the hosted base URL https://ollama.com/v1 with a "
                "real OLLAMA_API_KEY — one client, two very different trust levels",
            ),
        ),
        model_patterns=("<model>:<tag>", "*:latest", "gpt-oss:20b"),
        default_model="",
        streaming=_documented(
            SUPPORT_YES,
            "https://docs.ollama.com/api/openai-compatibility",
            "the fetched compatibility checklist ticks Streaming, stream and stream_options",
            fetched=True,
        ),
        native_json_schema=_documented(
            SUPPORT_PER_MODEL,
            "https://docs.ollama.com/api/openai-compatibility",
            "the fetched checklist ticks `response_format` (JSON mode) but does not split "
            "json_object from json_schema — whether a schema is compiled depends on the model "
            "and the Ollama version, so measure it",
            fetched=True,
        ),
        json_object=_documented(
            SUPPORT_YES,
            "https://docs.ollama.com/api/openai-compatibility",
            "the fetched checklist ticks JSON mode / response_format",
            fetched=True,
        ),
        vision=_documented(
            SUPPORT_PER_MODEL,
            "https://docs.ollama.com/api/openai-compatibility",
            "the fetched page has a '/v1/chat/completions with vision example' section and "
            "ticks Vision — for a model that carries a vision projection",
            fetched=True,
        ),
        tools=_documented(
            SUPPORT_YES,
            "https://docs.ollama.com/api/openai-compatibility",
            "the fetched checklist covers tool calling on the compatibility endpoint",
        ),
        quirks=(
            "the API key is a placeholder that MUST be present for some SDKs and is ignored by "
            "the server — which is exactly why a 401 here would mean 'wrong host', not 'wrong "
            "key'",
            "the same page documents a hosted endpoint (https://ollama.com/v1) that DOES need "
            "OLLAMA_API_KEY; local and cloud share a wire shape but not a trust model",
            "the model id is `<name>:<tag>` and a missing tag means :latest, so a client that "
            "sends a bare name silently gets whatever :latest was re-pulled",
            "the compatibility endpoint is a translation layer over Ollama's own /api/chat — the "
            "native API has fields (keep_alive, think) the OpenAI shape cannot express",
        ),
        licence="MIT (fetched: raw.githubusercontent.com/ollama/ollama/main/LICENSE)",
        licence_reference="https://raw.githubusercontent.com/ollama/ollama/main/LICENSE",
        licence_fetched=True,
        probe=PROBE_NOT_RUN,
        probe_reason=_NOT_RUN_LOCAL
        + " — and the hosted endpoint (https://ollama.com/v1) needs an OLLAMA_API_KEY that this "
        "host does not have either, so neither surface of this entry could be measured",
        note="the widest local compatibility surface: response_format, streaming, vision and "
        "tools are all documented on the OpenAI shape",
    ),
}

def _refresh_model_registry() -> None:
    """Current source-backed models; historical probes remain historical, never upgraded."""
    from .provider_model_data import PROVIDERS

    for key, data in PROVIDERS.items():
        models = tuple(data["models"])
        default = models[0] if models else ""
        if key in REGISTRY:
            REGISTRY[key] = replace(REGISTRY[key], default_model=default, note=data["notes"])
            continue
        endpoint = Endpoint(
            label="OpenAI-compatible chat", base_url=data["base_url"],
            kind=KIND_LOCAL if data["local"] else KIND_OPENAI_COMPATIBLE,
            api_key_env=(data["api_key_env"],) if data["api_key_env"] else (),
            auth_header=("Authorization: Bearer <provider-specific key>",),
            status=DOCUMENTED, reference=data["sources"][0], fetched=True,
            note="Model catalog inspected; inference and per-model tools not measured.",
        )
        REGISTRY[key] = ProviderSpec(
            id=key, label=data["label"], docs=data["sources"][0], endpoints=(endpoint,),
            default_model=default, model_patterns=models, note=data["notes"],
            licence="hosted API; licence/ToS not fetched this round (see the provider's own terms)",
            licence_reference=data["sources"][0],
            licence_fetched=False,
            probe=PROBE_NOT_RUN, probe_reason="No completion probe in the catalog refresh.",
        )


_refresh_model_registry()
REGISTRY_ORDER: tuple[str, ...] = tuple(REGISTRY)

#: The ids ``provider_catalog.CATALOG`` also carries for the chain. The two tables
#: must agree about the endpoint they both name, or the chain would offer a URL the
#: breadth catalog contradicts.
CHAIN_OVERLAP: tuple[str, ...] = ("deepseek", "openrouter", "openai")


def registry_entry(entry_id: str) -> ProviderSpec:
    """One entry by id, or ``KeyError`` naming the known ids."""
    key = str(entry_id or "").strip().lower()
    if key not in REGISTRY:
        raise KeyError(f"unknown provider {entry_id!r} (known: {list(REGISTRY_ORDER)})")
    return REGISTRY[key]


def probe_report() -> dict[str, Any]:
    """The live-probe log as JSON — every step with its evidence, and no verdicts."""
    ran = [step for step in PROBE_LOG if step.status is not None]
    not_run = [step for step in PROBE_LOG if step.status is None]
    rounds: dict[str, dict[str, int]] = {}
    for step in PROBE_LOG:
        date = step.date or PROBE_DATE_PREVIOUS
        bucket = rounds.setdefault(date, {"measured": 0, "unmeasured": 0})
        bucket["measured" if step.status is not None else "unmeasured"] += 1
    return {
        "date": PROBE_DATE,
        "previous_date": PROBE_DATE_PREVIOUS,
        "rounds": {date: rounds[date] for date in sorted(rounds)},
        "measured_steps": len(ran),
        "unmeasured_steps": len(not_run),
        "steps": [step.to_dict() for step in PROBE_LOG],
        "fetched_docs": {url: what for url, what in sorted(FETCHED_DOCS.items())},
        "disclaimer": (
            "a probe proves the endpoint, the credential and the refusal it produced — it does "
            "not upgrade an unrelated claim, and a probe that could not run is not a failure of "
            "the provider"
        ),
    }


def registry_report(
    *, entry_id: str = "", verification: str = ""
) -> dict[str, Any]:
    """The whole registry (or one entry) as JSON, every claim with its evidence."""
    if verification and verification not in VERIFICATION_ORDER:
        raise ValueError(
            f"unknown verification status {verification!r} (expected {list(VERIFICATION_ORDER)})"
        )
    if entry_id:
        entries = [registry_entry(entry_id)]
        order: tuple[str, ...] = (entries[0].id,)
    else:
        entries = [REGISTRY[key] for key in REGISTRY_ORDER]
        order = REGISTRY_ORDER
    if verification:
        entries = [entry for entry in entries if entry.verification == verification]
        order = tuple(entry.id for entry in entries)
    counts: dict[str, int] = {status: 0 for status in VERIFICATION_ORDER}
    for entry in entries:
        counts[entry.verification] += 1
    probed = {PROBE_RUN: 0, PROBE_PARTIAL: 0, PROBE_NOT_RUN: 0}
    for entry in entries:
        probed[entry.probe] += 1
    return {
        "count": len(entries),
        "order": list(order),
        "verification_counts": counts,
        "probe_counts": probed,
        "probe_date": PROBE_DATE,
        "results": [entry.to_dict() for entry in entries],
        "features": ("streaming", "native_json_schema", "json_object", "vision", "tools"),
        "disclaimer": (
            "verified = a live probe we ran (see probe_report); documented = an official page "
            "(fetched says whether we retrieved it); unverified = nobody proved it. A "
            "documented promise is not a measurement."
        ),
    }


__all__ = [
    "CHAIN_OVERLAP",
    "DOCUMENTED",
    "FETCHED_DOCS",
    "KIND_LOCAL",
    "KIND_NATIVE",
    "KIND_OPENAI_COMPATIBLE",
    "SKU_CHATGPT_PLAN",
    "SKU_CODING_PLAN",
    "SKU_PAY_AS_YOU_GO",
    "SKU_SUBSCRIPTION",
    "SKU_TOKEN_PLAN",
    "SKU_VALUES",
    "PROBE_DATE",
    "PROBE_LOG",
    "PROBE_NOT_RUN",
    "PROBE_PARTIAL",
    "PROBE_RUN",
    "REGISTRY",
    "REGISTRY_ORDER",
    "SUPPORT_NO",
    "SUPPORT_PER_MODEL",
    "SUPPORT_UNKNOWN",
    "SUPPORT_VALUES",
    "SUPPORT_YES",
    "UNVERIFIED",
    "VERIFICATION_ORDER",
    "VERIFIED",
    "Endpoint",
    "ProbeStep",
    "ProviderSpec",
    "Support",
    "probe_report",
    "registry_entry",
    "registry_report",
]
