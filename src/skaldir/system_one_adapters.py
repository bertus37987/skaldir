"""System-One provider adapters — the readiness path and the live path (§41/§42).

Two implementations behind the one :class:`~skaldir.system_one.SystemOneEvaluator`
protocol:

``LLMSystemOneAdapter``
    Usable **today**, without TypeSafe access: the official adapter idea
    (``system-one-adapter-python``) implemented against the repo's own
    :class:`godsv.llm.LLMClient`. Its evaluation always carries
    ``provider='llm_system_one_adapter'`` and ``live_jev=False`` — an adapter
    result is never evidence of live Jev (§41/JEV-06).

``TypeSafeJevEvaluator``
    The live adapter: it only resolves a *secret handle* (never a raw secret),
    posts the sanitized state plus the question pack, validates the response
    strictly and translates it into the internal contract (§42). Without
    authorized credentials it raises :class:`SystemOneBlockedExternal` with a
    structured blocker — never a fabricated answer (§85/JEV-07). Jev is Early
    Access; ``blocked_external_access`` is the honest state.

No voice policy lives in adapter code: an adapter's only job is transport,
strict validation and translation (§42).

The §15/§17 half of the binding order lives here too:

* the documented TypeSafe request contract — ``POST
  https://api.typesafe.ai/v1/systemone``, ``Bearer`` auth, ``application/json``,
  ``model=jev-latest``, a ``state`` that may be a string, an object or an array,
  and a ``questions`` **map** (``map<string, Question>``, keyed by the id we
  choose; each entry ``{type, instructions, criteria}``) with Noul/Choice/Score
  questions mixed in one call — as :func:`build_typesafe_request` with **no
  invented field**;
* the seven first-class provider error codes of §17
  (:data:`PROVIDER_ERROR_CODES`) with :func:`classify_error`,
  :class:`ProviderErrorRecord`, retries that stay inside the shared deadline and
  SDK retries that stay counted;
* the §17 fallback chain — authorized LLM System-One fallback → System-2 Voice
  fallback → degraded observe/blocked — as :class:`SystemOneFallbackChain`,
  whose every rung stays distinguishable and whose LLM rung is never labelled
  live Jev;
* §2's four ``provider_mode`` contract values as :func:`provider_mode_for`,
  stamped onto an evaluation when the field exists (the field itself is added by
  the System-One contract module; this module only fills it in).

Behaviour note (§17 changed one mapping deliberately): an LLM failure with **no**
status is now ``provider_unavailable`` and no longer a synthetic
``SystemOneTimeout`` — a timeout is reported as a timeout, a transport failure
as a transport failure, and the two are no longer conflated.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .llm import TIER_MID
from .system_one import (
    ACCESS_BLOCKED,
    ACCESS_LIVE,
    ACCESS_LLM_ADAPTER,
    PROVIDER_DISABLED,
    PROVIDER_LLM_ADAPTER,
    PROVIDER_TYPESAFE,
    EvaluationContext,
    SystemOneAuthError,
    SystemOneBlockedExternal,
    SystemOneContractError,
    SystemOneError,
    SystemOneEvaluation,
    SystemOneRateLimited,
    SystemOneState,
    SystemOneTimeout,
    build_evaluation,
    canonical_json,
    parse_answer,
)
from .system_one_policy import plan_retry

CredentialResolver = Callable[[EvaluationContext], Any]
Transport = Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]

_JSON_FENCE = "```"


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith(_JSON_FENCE):
        return cleaned
    body = cleaned[len(_JSON_FENCE) :]
    if body.startswith("json"):
        body = body[4:]
    end = body.rfind(_JSON_FENCE)
    return (body[:end] if end != -1 else body).strip()


def _load_answers_json(text: str) -> dict:
    try:
        payload = json.loads(_strip_fences(text))
    except (TypeError, ValueError) as exc:
        raise SystemOneContractError(f"provider response is not valid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise SystemOneContractError("provider response must be a JSON object")
    answers = payload.get("answers", payload)
    if not isinstance(answers, Mapping):
        raise SystemOneContractError("provider response needs an 'answers' object")
    return dict(answers)


def build_prompt(state: SystemOneState, questions) -> str:
    """One prompt per question set — atomic questions, never an omnibus ask.

    The question text comes exclusively from the versioned registry; the
    adapter adds only serialization instructions.
    """
    lines = [
        "You are a System One evaluator. Answer each atomic question about the state below.",
        "Return JSON: {\"answers\": {\"<question id>\": <answer object>}}.",
        "Answer shapes: noul={value 0..1}; choice={choice, probabilities, confidence};",
        "score={score, legend, probabilities, confidence}.",
        "Answer only the listed question ids. Do not add ids or fields.",
        "",
        "QUESTIONS:",
    ]
    lines += [json.dumps(question.as_dict(), sort_keys=True) for question in questions.questions]
    lines += ["", "STATE:", json.dumps(state.payload, sort_keys=True, default=str)]
    return "\n".join(lines)


def _validate_answers(raw_answers: Mapping[str, Any], questions) -> dict:
    """Strict translation: only registered ids, only the documented shapes."""
    kinds = {question.id: question.kind for question in questions.questions}
    answers: dict[str, Any] = {}
    for question_id, payload in raw_answers.items():
        if question_id not in kinds:
            raise SystemOneContractError(
                f"provider answered an unregistered question id: {question_id!r}"
            )
        answers[question_id] = parse_answer(
            question_id=question_id, kind=kinds[question_id], payload=payload
        )
    return answers


def _classify_transport_error(exc: Exception) -> SystemOneError:
    """Translate a provider failure into the classified System-One error (§17).

    The internal exception keeps its class (so existing callers keep working)
    **and** now carries the §17 ``code`` as an attribute, so the seven error
    names are usable without string-matching a message.
    """
    code = classify_error(exc)
    status = getattr(exc, "status", None)
    if isinstance(exc, SystemOneError):
        error: SystemOneError = exc
    else:
        error = _error_for_code(code, str(exc), status=status)
    # the §17 code rides along on the internal error (the classes stay unchanged)
    setattr(error, "code", code)  # noqa: B010 — a plain attribute, not a signature change
    return error


# --------------------------------------------------------------------------- #
# §15 — the documented TypeSafe request contract                               #
# --------------------------------------------------------------------------- #
#
# Only what the goal documents goes into the request: the model, the state and
# the questions. Nothing else — no route hints, no retry counters, no envelope
# keys the provider never documented.

TYPESAFE_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
TYPESAFE_MODEL = "jev-latest"
TYPESAFE_AUTH_SCHEME = "Bearer"
TYPESAFE_CONTENT_TYPE = "application/json"

#: Only these keys may appear in the request body (§15).
DOCUMENTED_REQUEST_FIELDS = ("model", "state", "questions")

#: ``state`` may be ``string | object | array`` per the API documentation.
STATE_SHAPES = ("object", "string", "array")

#: The one thing each entry of the ``questions`` **map** is filed under: the question id.
QUESTIONS_SHAPE = "map<string, Question>"

#: The fields the API documents on a ``Question``: ``type`` and ``instructions`` are
#: required, ``criteria`` is required for ``choice``/``score`` and optional for ``noul``.
DOCUMENTED_QUESTION_FIELDS = ("type", "instructions", "criteria")


def documented_question(question: Any) -> dict:
    """One question as the API's own ``Question`` — the *documented* field names only.

    The registry's internal metadata (``id``/``version``/``owner``/``group``) stays home: the
    fetched reference documents ``{type, instructions, criteria}`` and says the id is the map
    key, not a field ("the key is not sent to the underlying model and is not used in
    inference").

    ``criteria`` is translated from what the registry really holds, never invented:

    * ``noul`` — omitted: the reference marks ``criteria`` optional ("what a yes and a no
      mean" is not something this registry models);
    * ``choice`` — ``{option: null}``: the reference requires the map and documents ``null``
      for "an option needs no extra detail";
    * ``score`` — the declared levels in legend order as strings: the reference requires an
      ordered array of at least two levels, and the registry holds numeric levels rather than
      prose. A wording per level is a **known gap**, stated instead of improvised.
    """
    payload: dict[str, Any] = {
        "type": str(question.kind),
        "instructions": str(question.text),
    }
    if question.kind == "choice":
        payload["criteria"] = {str(option): None for option in question.options}
    elif question.kind == "score":
        payload["criteria"] = [str(level) for level in question.legend]
    return payload


def documented_questions(questions: Any) -> dict[str, dict]:
    """``{question id: Question}`` — the documented **map**, keyed by the id we choose.

    Measured contract defect (report 14 §9 / Q-1, fixed 2026-09-19): this used to send a
    *list* of the registry's internal dicts while the fetched reference documents
    ``questions: map<string, Question>`` — a live call would have been refused (422) and the
    answers are documented to come back "under the same ids you used in questions", so the
    request and the answer map are the same shape here. A duplicate id is refused rather than
    silently collapsed (the registry refuses one too).
    """
    payload: dict[str, dict] = {}
    for question in questions.questions:
        key = str(question.id)
        if key in payload:
            raise SystemOneContractError(f"duplicate question id in the request: {key!r}")
        payload[key] = documented_question(question)
    return payload


def build_typesafe_request(
    state: SystemOneState,
    questions,
    *,
    model: str = TYPESAFE_MODEL,
    state_shape: str = "object",
) -> dict:
    """The documented request body: model, state, questions — nothing else.

    ``state_shape`` selects one of the three documented shapes. The *object*
    shape is the internal state envelope (payload + scope + fingerprint); the
    string shape serializes it canonically; the array shape sends the payload's
    values in the sanitized key order. None of the three adds a key the goal
    does not document.

    ``questions`` is the documented **map** ``map<string, Question>`` keyed by the question id
    (:func:`documented_questions`), and each entry carries the documented fields only
    (:func:`documented_question`).
    """
    if state_shape not in STATE_SHAPES:
        raise SystemOneContractError(
            f"unknown state shape: {state_shape!r} (documented: {STATE_SHAPES})"
        )
    if state_shape == "object":
        shaped: Any = state.as_dict()
    elif state_shape == "string":
        shaped = canonical_json(state.as_dict())
    else:
        payload = state.as_dict()
        shaped = [payload.get("input_scope", ""), payload.get("fingerprint", ""), state.payload]
    return {
        "model": model,
        "state": shaped,
        "questions": documented_questions(questions),
    }


# --------------------------------------------------------------------------- #
# §17 — the seven provider error codes                                         #
# --------------------------------------------------------------------------- #

#: The §17 taxonomy, verbatim. These names are the vocabulary callers may rely on.
PROVIDER_ERROR_CODES = (
    "connection_invalid",  # 401
    "request_contract_error",  # 422
    "provider_rate_limited",  # 429
    "provider_overloaded",  # 529
    "provider_timeout",
    "provider_unavailable",
    "adapter_error",
)

STATUS_ERROR_CODES: dict[int, str] = {
    401: "connection_invalid",
    422: "request_contract_error",
    429: "provider_rate_limited",
    529: "provider_overloaded",
}

#: Only these may be retried — and only while the shared deadline allows it (§43).
RETRYABLE_ERROR_CODES = (
    "provider_rate_limited",
    "provider_overloaded",
    "provider_timeout",
)

_ERROR_CLASSES: dict[str, type[SystemOneError]] = {
    "connection_invalid": SystemOneAuthError,
    "request_contract_error": SystemOneContractError,
    "provider_rate_limited": SystemOneRateLimited,
    "provider_overloaded": SystemOneRateLimited,
    "provider_timeout": SystemOneTimeout,
    "provider_unavailable": SystemOneError,
    "adapter_error": SystemOneContractError,
}


def error_code_for_status(status: Any) -> str | None:
    """The documented code for a known HTTP status, else ``None`` (no guessing)."""
    if status is None:
        return None
    try:
        return STATUS_ERROR_CODES.get(int(status))
    except (TypeError, ValueError):
        return None


def classify_error(exc: BaseException) -> str:
    """Map one failure to exactly one of the seven §17 codes."""
    code = error_code_for_status(getattr(exc, "status", None))
    if code is not None:
        return code
    if isinstance(
        exc, (SystemOneTimeout, TimeoutError, asyncio.TimeoutError)
    ) or getattr(exc, "timeout", False):
        return "provider_timeout"
    if isinstance(exc, (json.JSONDecodeError, SystemOneContractError, KeyError, AttributeError)):
        # a local mapping/validation failure — our bug, not the provider's answer
        return "adapter_error"
    return "provider_unavailable"


def _error_for_code(code: str, message: str, *, status: Any = None) -> SystemOneError:
    cls = _ERROR_CLASSES[code]
    if cls is SystemOneRateLimited:
        return SystemOneRateLimited(message, status=int(status or 429))
    return cls(message)


@dataclass(frozen=True)
class ProviderErrorRecord:
    """One classified provider failure, ready for an event or the audit trail.

    ``retryable`` is the taxonomy's answer, not the retry decision: whether a
    retry actually happens is :func:`skaldir.system_one_policy.plan_retry`'s call,
    bounded by the shared deadline.
    """

    code: str
    status: int | None = None
    message: str = ""
    attempts: int = 1
    sdk_retries: int = 0
    remaining_deadline: float | None = None

    def __post_init__(self) -> None:
        if self.code not in PROVIDER_ERROR_CODES:
            raise SystemOneContractError(
                f"unknown provider error code: {self.code!r} (known: {PROVIDER_ERROR_CODES})"
            )

    @classmethod
    def from_exception(
        cls,
        exc: BaseException,
        *,
        attempts: int = 1,
        sdk_retries: int = 0,
        remaining_deadline: float | None = None,
    ) -> ProviderErrorRecord:
        raw_status = getattr(exc, "status", None)
        status: int | None
        try:
            status = None if raw_status is None else int(raw_status)
        except (TypeError, ValueError):
            status = None
        return cls(
            code=classify_error(exc),
            status=status,
            message=str(exc)[:300],
            attempts=max(1, int(attempts)),
            sdk_retries=max(0, int(sdk_retries)),
            remaining_deadline=remaining_deadline,
        )

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_ERROR_CODES

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "status": self.status,
            "message": self.message,
            "retryable": self.retryable,
            "attempts": self.attempts,
            "sdk_retries": self.sdk_retries,
            "remaining_deadline": self.remaining_deadline,
        }


# --------------------------------------------------------------------------- #
# §2 — the four provider_mode contract values                                  #
# --------------------------------------------------------------------------- #

#: The four values of §4.2's ``provider_mode``, verbatim.
PROVIDER_MODES = (
    "typesafe_jev_live",
    "llm_system_one_adapter",
    "replay_fixture",
    "disabled",
)


def provider_mode_for(provider: str, *, live_jev: bool = False, replay: bool = False) -> str:
    """The contract value for a provider, refusing the dishonest combination.

    A ``typesafe`` provider that is not ``live_jev`` is a wiring bug, not a
    mode: it may not be labelled live, and calling it an LLM adapter would hide
    which provider answered. Replaying recorded answers is ``replay_fixture``.
    """
    if replay:
        return "replay_fixture"
    if provider == PROVIDER_TYPESAFE:
        if not live_jev:
            raise SystemOneContractError(
                "a typesafe evaluation that is not live_jev must not be shown as a "
                "live Jev call (and is not an LLM adapter either) — fix the wiring"
            )
        return "typesafe_jev_live"
    if provider == PROVIDER_LLM_ADAPTER:
        return "llm_system_one_adapter"
    if provider == PROVIDER_DISABLED:
        return "disabled"
    if provider == "replay_fixture":
        return "replay_fixture"
    raise SystemOneContractError(
        f"unknown provider {provider!r} (known: {PROVIDER_TYPESAFE}, "
        f"{PROVIDER_LLM_ADAPTER}, {PROVIDER_DISABLED}; replay: replay_fixture)"
    )


def _contract_provider_mode(evaluation: SystemOneEvaluation) -> str | None:
    """The contract module's own §4.2 derivation, when that module offers one.

    The label has one definition, in the contract module; this adapter only asks
    for it and treats an inconsistent record as unlabelable instead of inventing
    a mode.
    """
    try:
        from .system_one import provider_mode_for as contract_mode
    except ImportError:  # pragma: no cover — an older contract module
        return None
    try:
        return str(
            contract_mode(
                access_mode=str(getattr(evaluation, "provider_access_mode", "")),
                live_jev=bool(getattr(evaluation, "live_jev", False)),
            )
        )
    except Exception:  # noqa: BLE001 — a contradictory record is never relabelled
        return None


def stamp_provider_mode(evaluation: SystemOneEvaluation, *, replay: bool = False) -> str:
    """Label an evaluation with its contract ``provider_mode`` (§4.2).

    The field belongs to the System-One contract module (it derives and validates
    it there); this function is the adapter-side helper that (a) returns the
    already-correct label untouched, (b) forces ``replay_fixture`` for a recorded
    answer, and (c) falls back to the provider-based derivation when the contract
    module cannot label the record. It never turns a fallback into a live claim.
    """
    existing = str(getattr(evaluation, "provider_mode", "") or "")
    if existing and not replay:
        return existing
    mode = (
        "replay_fixture"
        if replay
        else (
            _contract_provider_mode(evaluation)
            or provider_mode_for(
                str(getattr(evaluation, "provider", "")),
                live_jev=bool(getattr(evaluation, "live_jev", False)),
            )
        )
    )
    try:
        evaluation.provider_mode = mode
    except AttributeError:  # pragma: no cover — a frozen implementation
        pass
    usage = getattr(evaluation, "usage", None)
    if isinstance(usage, dict):
        usage.setdefault("provider_mode", mode)
    return mode


class LLMSystemOneAdapter:
    """LLM-backed System One — the readiness adapter usable today (§41)."""

    provider = PROVIDER_LLM_ADAPTER
    provider_model = ""
    provider_access_mode = ACCESS_LLM_ADAPTER

    def __init__(
        self,
        llm,
        *,
        model: str = "",
        tier: str = TIER_MID,
        prompt_id: str = "system_one.evaluate",
        max_attempts: int = 2,
        backoff_base: float = 0.2,
        backoff_cap: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        emit: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.llm = llm
        self.provider_model = model or "gv-system-one-adapter"
        self.tier = tier
        self.prompt_id = prompt_id
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_base = float(backoff_base)
        self.backoff_cap = float(backoff_cap)
        self.sleep = sleep
        self.emit = emit or (lambda kind, payload: None)
        self.attempts = 0

    async def evaluate(
        self,
        *,
        state: SystemOneState,
        questions,
        context: EvaluationContext,
    ) -> SystemOneEvaluation:
        started = time.time()
        prompt = build_prompt(state, questions)
        response = ""
        attempts = 0
        sdk_retries = 0
        while True:
            attempts += 1
            try:
                response = self.llm.complete(
                    prompt_id=self.prompt_id, prompt=prompt, tier=self.tier
                )
                break
            except Exception as exc:  # noqa: BLE001 — classified below, never swallowed
                sdk_retries += int(getattr(exc, "sdk_retries", 0) or 0)
                decision = plan_retry(
                    status=getattr(exc, "status", None),
                    attempt=attempts,
                    max_attempts=self.max_attempts,
                    remaining_deadline=context.remaining_deadline(),
                    backoff_base=self.backoff_base,
                    backoff_cap=self.backoff_cap,
                )
                self.emit(
                    "system_one_error",
                    {
                        "provider": self.provider,
                        "attempt": attempts,
                        "status": getattr(exc, "status", None),
                        "reason": decision.reason,
                        "retry": decision.retry,
                    },
                )
                if not decision.retry:
                    self.attempts = attempts + sdk_retries
                    raise _classify_transport_error(exc) from exc
                self.sleep(decision.delay)
        self.attempts = attempts + sdk_retries

        answers = _validate_answers(_load_answers_json(response), questions)
        completed = time.time()
        evaluation = build_evaluation(
            context=context,
            questions=questions,
            state=state,
            provider=self.provider,
            provider_model=self.provider_model,
            access_mode=self.provider_access_mode,
            answers=answers,
            started_at=started,
            completed_at=completed,
            usage={
                "prompt_chars": len(prompt),
                "response_chars": len(response),
                "tier": self.tier,
                "client_attempts": attempts,
                "sdk_retries": sdk_retries,
            },
            live_jev=False,  # an adapter result is never a live Jev call (§41)
            shadow=bool(context.shadow),
            attempts=self.attempts,
            sdk_retries=sdk_retries,
        )
        evaluation.raw_ref = f"llm://{self.provider}/{evaluation.evaluation_id}"
        stamp_provider_mode(evaluation)
        self.emit("system_one_evaluation", evaluation.evidence())
        return evaluation


def _is_timeout(exc: BaseException) -> bool:
    """Is this failure a timeout? Only the shapes that really mean one (§17)."""
    return isinstance(exc, (SystemOneTimeout, TimeoutError, asyncio.TimeoutError)) or bool(
        getattr(exc, "timeout", False)
    )


def _accepts_headers(transport: Any) -> bool:
    """Does this transport take a ``headers`` keyword? A one-arg callable does not."""
    if transport is None:
        return False
    try:
        parameters = inspect.signature(transport).parameters
    except (TypeError, ValueError):  # pragma: no cover — exotic callables
        return False
    if any(parameter.kind == parameter.VAR_KEYWORD for parameter in parameters.values()):
        return True
    found = parameters.get("headers")
    return found is not None and found.kind in (
        found.POSITIONAL_OR_KEYWORD,
        found.KEYWORD_ONLY,
    )


def _blocked(reason: str, *, model: str, missing: str) -> SystemOneBlockedExternal:
    return SystemOneBlockedExternal(
        {
            "state": ACCESS_BLOCKED,
            "acceptance_state": ACCESS_BLOCKED,
            "provider": PROVIDER_TYPESAFE,
            "provider_model": model,
            "reason": reason,
            "missing": missing,
            "live_jev": False,
        }
    )


class TypeSafeJevEvaluator:
    """Live Jev adapter — blocked until authorized access exists (§42/JEV-07)."""

    provider = PROVIDER_TYPESAFE
    provider_model = "jev-latest"
    provider_access_mode = ACCESS_LIVE

    def __init__(
        self,
        *,
        credential_resolver: CredentialResolver | None = None,
        transport: Transport | None = None,
        model: str = "jev-latest",
        max_attempts: int = 3,
        state_shape: str = "object",
        auth_headers: Callable[[], Any] | None = None,
        backoff_base: float = 0.25,
        backoff_cap: float = 2.0,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        emit: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.credential_resolver = credential_resolver
        self.transport = transport
        self.provider_model = model
        self.max_attempts = max(1, int(max_attempts))
        self.state_shape = state_shape
        #: Called immediately before every attempt; it returns the request
        #: **headers** (never stored here, never in the request body).
        self.auth_headers = auth_headers
        self.backoff_base = float(backoff_base)
        self.backoff_cap = float(backoff_cap)
        self.sleep = sleep or asyncio.sleep
        self.emit = emit or (lambda kind, payload: None)
        self.attempts = 0
        self.last_error: dict | None = None

    async def _resolve_credential(self, context: EvaluationContext) -> Any:
        """Resolve a *secret handle* via the injected resolver / Treasury.

        Only a reference crosses this boundary; the adapter never sees a raw
        secret (§42/§76).
        """
        if self.credential_resolver is None:
            return None
        result = self.credential_resolver(context)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _headers(self) -> Mapping[str, str]:
        """Ask the Treasury adapter for the headers of this attempt (§16).

        The evaluator never keeps the material: it is fetched per attempt, used
        for the transport call and dropped. An unconfigured provider yields no
        header at all — the transport then owns auth (see the compatibility doc).
        """
        if self.auth_headers is None:
            return {}
        headers = self.auth_headers()
        if inspect.isawaitable(headers):
            headers = await headers
        return dict(headers or {})

    async def _call_transport(
        self, request: Mapping[str, Any], headers: Mapping[str, str]
    ) -> Any:
        """One transport call; a headers-aware transport is used when it exists."""
        if headers and _accepts_headers(self.transport):
            return await self.transport(request, headers=headers)  # type: ignore[call-arg]
        return await self.transport(request)

    async def evaluate(
        self,
        *,
        state: SystemOneState,
        questions,
        context: EvaluationContext,
    ) -> SystemOneEvaluation:
        handle = await self._resolve_credential(context)
        if not handle:
            raise _blocked(
                "no authorized TypeSafe credential available (Jev Early Access)",
                model=self.provider_model,
                missing="treasury secret handle for typesafe/systemone",
            )
        if self.transport is None:
            raise _blocked(
                "no authorized TypeSafe transport configured for this credential",
                model=self.provider_model,
                missing="authorized POST /v1/systemone transport",
            )

        request = build_typesafe_request(
            state, questions, model=self.provider_model, state_shape=self.state_shape
        )
        started = time.time()
        attempt = 0
        sdk_retries = 0
        response: Any = None
        while True:
            attempt += 1
            try:
                response = await self._call_transport(request, await self._headers())
                break
            except Exception as exc:  # noqa: BLE001 — classified, never swallowed (§17)
                sdk_retries += max(0, int(getattr(exc, "sdk_retries", 0) or 0))
                remaining = context.remaining_deadline()
                record = ProviderErrorRecord.from_exception(
                    exc,
                    attempts=attempt,
                    sdk_retries=sdk_retries,
                    remaining_deadline=remaining,
                )
                decision = plan_retry(
                    status=getattr(exc, "status", None),
                    attempt=attempt,
                    max_attempts=self.max_attempts,
                    remaining_deadline=remaining,
                    timeout=_is_timeout(exc),
                    backoff_base=self.backoff_base,
                    backoff_cap=self.backoff_cap,
                )
                self.last_error = {
                    **record.as_dict(),
                    "provider": self.provider,
                    "retry": bool(decision.retry),
                    "reason": decision.reason,
                }
                self.emit("system_one_error", dict(self.last_error))
                if not decision.retry:
                    self.attempts = attempt + sdk_retries
                    raise _classify_transport_error(exc) from exc
                await self.sleep(decision.delay)
        self.attempts = attempt + sdk_retries
        try:
            if not isinstance(response, Mapping):
                raise SystemOneContractError("TypeSafe response must be a mapping")
            raw_answers = response.get("answers") or {}
            if not isinstance(raw_answers, Mapping):
                raise SystemOneContractError("TypeSafe response needs an 'answers' object")
            answers = _validate_answers(raw_answers, questions)
        except SystemOneContractError as exc:
            # a local mapping/translation failure is our bug, not an answer (§17)
            exc.code = "adapter_error"
            raise
        response_sdk_retries = int(response.get("sdk_retries", 0) or 0)
        evaluation = build_evaluation(
            context=context,
            questions=questions,
            state=state,
            provider=self.provider,
            provider_model=self.provider_model,
            access_mode=self.provider_access_mode,
            answers=answers,
            started_at=started,
            usage=dict(response.get("usage") or {}),
            live_jev=True,
            attempts=attempt + sdk_retries + response_sdk_retries,
            sdk_retries=sdk_retries + response_sdk_retries,
        )
        self.attempts = int(evaluation.attempts)
        evaluation.raw_ref = f"typesafe://{self.provider_model}/{evaluation.evaluation_id}"
        stamp_provider_mode(evaluation)
        self.emit("system_one_evaluation", evaluation.evidence())
        return evaluation


async def evaluate_async(evaluator, *, state, questions, context) -> SystemOneEvaluation:
    """Small helper so sync callers do not re-implement the await contract."""
    return await evaluator.evaluate(state=state, questions=questions, context=context)


# --------------------------------------------------------------------------- #
# §17 — the fallback chain                                                      #
# --------------------------------------------------------------------------- #
#
#     Jev unavailable
#       ├─ authorized LLM System-One fallback
#       ├─ System-2 Voice fallback
#       └─ degraded observe / blocked
#
# The chain never blends two rungs into one record and never upgrades a fallback
# result to live Jev: ``rung``, ``error_codes`` and ``primary_failed`` say
# exactly where the answer (or the decision) came from.

FALLBACK_RUNG_PRIMARY = "primary"
FALLBACK_RUNG_LLM = "llm_system_one_fallback"
FALLBACK_RUNG_SYSTEM2_VOICE = "system2_voice_fallback"
FALLBACK_RUNG_DEGRADED = "degraded_observe_blocked"

FALLBACK_RUNG_ORDER = (
    FALLBACK_RUNG_PRIMARY,
    FALLBACK_RUNG_LLM,
    FALLBACK_RUNG_SYSTEM2_VOICE,
    FALLBACK_RUNG_DEGRADED,
)


@dataclass(frozen=True)
class FallbackOutcome:
    """Which rung anwered, what failed before it, and what the caller may do."""

    rung: str
    primary_failed: bool = False
    degraded: bool = False
    error_codes: tuple[str, ...] = ()
    evaluation: SystemOneEvaluation | None = None
    system2_decision: Any = None
    advisory_only: bool = True
    authorizes_action: bool = False

    def as_dict(self) -> dict:
        return {
            "rung": self.rung,
            "primary_failed": self.primary_failed,
            "degraded": self.degraded,
            "error_codes": list(self.error_codes),
            "evaluation": self.evaluation.as_dict() if self.evaluation is not None else None,
            "system2_decision": self.system2_decision,
            "advisory_only": self.advisory_only,
            "authorizes_action": self.authorizes_action,
        }


def _failure_code(exc: BaseException) -> str:
    """The honest label of one failed rung: a §17 code or a blocker state."""
    if isinstance(exc, SystemOneBlockedExternal):
        state = str(exc.blocker.get("state") or ACCESS_BLOCKED)
        code = str(exc.blocker.get("code") or "")
        return code or state
    return str(getattr(exc, "code", "") or classify_error(exc))


class SystemOneFallbackChain:
    """The §17 fallback chain as one evaluator-shaped object.

    ``llm_fallback_authorized`` is explicit on purpose: switching to an LLM
    System-One adapter is an authorization, and an unauthorized chain skips that
    rung entirely instead of quietly using it. ``voice_fallback`` is the System-2
    decision hook (Voice/planner); when it answers, the chain returns **no**
    System-One evaluation — System 2 decides, and this module never invents
    answers on its behalf.
    """

    def __init__(
        self,
        *,
        primary,
        llm_fallback=None,
        llm_fallback_authorized: bool = False,
        voice_fallback: Callable[..., Any] | None = None,
        degraded: bool = True,
        emit: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.primary = primary
        self.llm_fallback = llm_fallback
        self.llm_fallback_authorized = bool(llm_fallback_authorized)
        self.voice_fallback = voice_fallback
        self.degraded = bool(degraded)
        self.emit = emit or (lambda kind, payload: None)
        self.trail: list[str] = []

    async def _try(self, evaluator, *, state, questions, context, rung: str):
        """One rung: its result, or the honest failure code (never an answer)."""
        try:
            evaluation = await evaluator.evaluate(
                state=state, questions=questions, context=context
            )
        except Exception as exc:  # noqa: BLE001 — every rung failure is classified
            code = _failure_code(exc)
            self.trail.append(f"{rung}:{code}")
            return None, code
        self.trail.append(f"{rung}:ok")
        return evaluation, ""

    async def evaluate(
        self,
        *,
        state: SystemOneState,
        questions,
        context: EvaluationContext,
    ) -> FallbackOutcome:
        codes: list[str] = []
        evaluation, code = await self._try(
            self.primary, state=state, questions=questions, context=context, rung="primary"
        )
        if evaluation is not None:
            return FallbackOutcome(
                rung=FALLBACK_RUNG_PRIMARY, error_codes=(), evaluation=evaluation
            )
        codes.append(code)
        self.emit("system_one_fallback", {"from": FALLBACK_RUNG_PRIMARY, "code": code})

        if self.llm_fallback is not None and self.llm_fallback_authorized:
            evaluation, code = await self._try(
                self.llm_fallback,
                state=state,
                questions=questions,
                context=context,
                rung=FALLBACK_RUNG_LLM,
            )
            if evaluation is not None:
                # a fallback answer is never labelled live Jev, and it says where it came from
                stamp_provider_mode(evaluation)
                return FallbackOutcome(
                    rung=FALLBACK_RUNG_LLM,
                    primary_failed=True,
                    error_codes=tuple(codes),
                    evaluation=evaluation,
                )
            codes.append(code)
            self.emit("system_one_fallback", {"from": FALLBACK_RUNG_LLM, "code": code})

        if self.voice_fallback is not None:
            decision = self.voice_fallback(
                reason=f"system one unavailable: {', '.join(codes) or 'unknown'}"
            )
            if inspect.isawaitable(decision):
                decision = await decision
            self.trail.append(f"{FALLBACK_RUNG_SYSTEM2_VOICE}:ok")
            self.emit(
                "system_one_escalated",
                {"to": FALLBACK_RUNG_SYSTEM2_VOICE, "codes": list(codes)},
            )
            return FallbackOutcome(
                rung=FALLBACK_RUNG_SYSTEM2_VOICE,
                primary_failed=True,
                error_codes=tuple(codes),
                system2_decision=decision,
            )

        self.emit("system_one_fallback", {"from": "all_rungs", "codes": list(codes)})
        return FallbackOutcome(
            rung=FALLBACK_RUNG_DEGRADED,
            primary_failed=True,
            degraded=True,
            error_codes=tuple(codes),
        )


def run_sync(evaluator, *, state, questions, context) -> SystemOneEvaluation:
    """Run one evaluation from synchronous code (CLI, tests, desktop)."""
    return asyncio.run(evaluate_async(evaluator, state=state, questions=questions, context=context))


__all__ = [
    "DOCUMENTED_QUESTION_FIELDS",
    "DOCUMENTED_REQUEST_FIELDS",
    "FALLBACK_RUNG_DEGRADED",
    "FALLBACK_RUNG_LLM",
    "FALLBACK_RUNG_ORDER",
    "FALLBACK_RUNG_PRIMARY",
    "FALLBACK_RUNG_SYSTEM2_VOICE",
    "PROVIDER_ERROR_CODES",
    "PROVIDER_MODES",
    "QUESTIONS_SHAPE",
    "RETRYABLE_ERROR_CODES",
    "STATE_SHAPES",
    "STATUS_ERROR_CODES",
    "TYPESAFE_AUTH_SCHEME",
    "TYPESAFE_CONTENT_TYPE",
    "TYPESAFE_ENDPOINT",
    "TYPESAFE_MODEL",
    "FallbackOutcome",
    "LLMSystemOneAdapter",
    "ProviderErrorRecord",
    "SystemOneFallbackChain",
    "TypeSafeJevEvaluator",
    "build_prompt",
    "build_typesafe_request",
    "classify_error",
    "documented_question",
    "documented_questions",
    "error_code_for_status",
    "evaluate_async",
    "provider_mode_for",
    "run_sync",
    "stamp_provider_mode",
]
