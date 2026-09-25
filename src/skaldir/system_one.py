"""System One (Jev) — provider-neutral evaluation abstraction (ABNAHME Teil VIII).

System One is advisory/classifying; authority stays with the runtime gates,
the decision policy, auth and evidence (§36). No Voice or Computer-Use module
may consume a provider-specific response object as its business contract (§37):
everything crosses the provider boundary as the internal
:class:`SystemOneEvaluation` contract below, built by
:class:`SystemOneEvaluator` implementations.

Design rules carried by this module:

* ``NoulAnswer`` is a probability 0..1 and has **no** confidence field — a
  Choice/Score having one does not invent one here (§38/JEV-02).
* Every probability distribution must sum to 1 within tolerance; provider
  payloads are validated strictly and unknown fields are contract errors
  (§42), never silently absorbed.
* ``live_jev`` defaults to ``False``: an LLM-based adapter result is never
  labelled as a live Jev call (§41/JEV-06).

Honest status: Jev is Early Access. Without authorized TypeSafe credentials the
live adapter reports ``blocked_external_access`` (see
:mod:`skaldir.system_one_adapters`) and never a fabricated answer (JEV-07 §85).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# --------------------------------------------------------------------------- #
# provider identity — the vocabulary the rest of the runtime speaks            #
# --------------------------------------------------------------------------- #

PROVIDER_DISABLED = "disabled"
PROVIDER_LLM_ADAPTER = "llm_system_one_adapter"
PROVIDER_TYPESAFE = "typesafe_jev"

ACCESS_DISABLED = "disabled"
ACCESS_LLM_ADAPTER = "llm_adapter"
ACCESS_LIVE = "live"
ACCESS_BLOCKED = "blocked_external_access"
ACCESS_FIXTURE = "fixture"

#: §4.2 ``provider_mode`` — the *honest* provider labelling of one evaluation.
#: Exactly four values, and the difference between them is the difference
#: between a real Jev call and everything else (§14/§36):
#:
#: * ``typesafe_jev_live``      — an authorized live TypeSafe/Jev call happened;
#: * ``llm_system_one_adapter`` — the official LLM-based System-One adapter ran;
#: * ``replay_fixture``         — a recorded/scripted evaluation (replay tests);
#: * ``disabled``               — no provider ran at all.
#:
#: An LLM-adapter result may NEVER read ``typesafe_jev_live``, and a live
#: result reads it only when ``live_jev`` is True — both are enforced below, so
#: the forbidden combination is not constructible (§4.2/§34).
PROVIDER_MODE_TYPESAFE_LIVE = "typesafe_jev_live"
PROVIDER_MODE_LLM_ADAPTER = "llm_system_one_adapter"
PROVIDER_MODE_REPLAY_FIXTURE = "replay_fixture"
PROVIDER_MODE_DISABLED = "disabled"

PROVIDER_MODES: tuple[str, ...] = (
    PROVIDER_MODE_TYPESAFE_LIVE,
    PROVIDER_MODE_LLM_ADAPTER,
    PROVIDER_MODE_REPLAY_FIXTURE,
    PROVIDER_MODE_DISABLED,
)

STATUS_OK = "ok"

#: Tolerance for "probabilities sum to 1" — provider floats are never exact.
PROB_TOLERANCE = 1e-3

ANSWER_KINDS = ("noul", "choice", "score")


# --------------------------------------------------------------------------- #
# errors                                                                       #
# --------------------------------------------------------------------------- #


class SystemOneError(RuntimeError):
    """Base class for every System-One failure (policy reasons live on it)."""


class SystemOneContractError(SystemOneError):
    """The internal or provider payload violates the answer contract (§38/§42)."""


class SystemOneNotRegistered(SystemOneContractError):
    """A question id/pack that nobody registered was asked for (§39/JEV-03)."""


#: The registry vocabulary name, used by the question module and its callers.
QuestionNotRegistered = SystemOneNotRegistered


class SystemOneAuthError(SystemOneError):
    """401 — missing/invalid credential. Never retried (§43)."""


class SystemOneRateLimited(SystemOneError):
    """429/529 — retryable only inside the shared remaining deadline (§43)."""

    def __init__(self, message: str, *, status: int = 429) -> None:
        super().__init__(message)
        self.status = int(status)


class SystemOneTimeout(SystemOneError):
    """The call exceeded the shared request deadline."""


class SystemOneBlockedExternal(SystemOneError):
    """No authorized external access — a structured blocker, never an answer.

    ``blocker`` is deliberately a plain mapping so it survives logging, events
    and the audit register unchanged. It never contains a credential and never
    an answer: a blocker is not an evaluation (§85/JEV-07).
    """

    def __init__(self, blocker: Mapping[str, Any]) -> None:
        super().__init__(str(blocker.get("reason") or ACCESS_BLOCKED))
        self.blocker: dict = dict(blocker)


# --------------------------------------------------------------------------- #
# answers (§38)                                                                #
# --------------------------------------------------------------------------- #


def validate_probabilities(
    probabilities: Mapping[str, float],
    *,
    tolerance: float = PROB_TOLERANCE,
    where: str = "probabilities",
) -> None:
    """Every distribution must sum to 1 within ``tolerance`` (§38)."""
    if not probabilities:
        raise SystemOneContractError(f"{where} must not be empty")
    try:
        total = sum(float(value) for value in probabilities.values())
    except (TypeError, ValueError) as exc:
        raise SystemOneContractError(f"{where} must be numeric: {exc}") from exc
    if abs(total - 1.0) > tolerance:
        raise SystemOneContractError(
            f"{where} must sum to 1 within {tolerance}: got {total!r}"
        )


def _validate_confidence(confidence: float, *, where: str) -> None:
    if not 0.0 <= float(confidence) <= 1.0:
        raise SystemOneContractError(f"{where} must be within 0..1: got {confidence!r}")


@dataclass(frozen=True)
class NoulAnswer:
    """A probability 0..1 — and, by contract, nothing else (§38).

    There is intentionally no ``confidence`` field: Choice/Score carrying a
    confidence does not justify inventing one for a Noul. §4.3's
    ``confidence = null`` is therefore honoured in the *serialized* contract
    (:meth:`as_dict` and :func:`answer_confidence`) instead of as an attribute —
    a property returning ``None`` would make ``hasattr(answer, "confidence")``
    true, and that pre-existing pin is the stronger statement.
    """

    question_id: str
    value: float
    question_type: str = ""
    provider_metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        value = float(self.value)
        if not 0.0 <= value <= 1.0:
            raise SystemOneContractError(
                f"Noul value must be within 0..1: got {self.value!r}"
            )
        if not self.question_type:
            object.__setattr__(self, "question_type", "noul")
        object.__setattr__(self, "provider_metadata", dict(self.provider_metadata or {}))

    @property
    def kind(self) -> str:
        return "noul"

    def as_dict(self) -> dict:
        return {
            "kind": "noul",
            "question_id": self.question_id,
            "question_type": self.question_type or "noul",
            "value": float(self.value),
            # §4.3: Noul has no confidence — stated explicitly, never invented
            "confidence": None,
            "provider_metadata": dict(self.provider_metadata),
        }


@dataclass(frozen=True)
class ChoiceAnswer:
    """A discrete choice with its distribution and confidence (§38)."""

    question_id: str
    choice: str
    probabilities: dict[str, float]
    confidence: float
    question_type: str = ""
    provider_metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_probabilities(self.probabilities, where=f"{self.question_id}.probabilities")
        _validate_confidence(self.confidence, where=f"{self.question_id}.confidence")
        if self.probabilities and self.choice not in self.probabilities:
            raise SystemOneContractError(
                f"{self.question_id}: choice {self.choice!r} not present in probabilities"
            )
        if not self.question_type:
            object.__setattr__(self, "question_type", "choice")
        object.__setattr__(self, "provider_metadata", dict(self.provider_metadata or {}))

    @property
    def kind(self) -> str:
        return "choice"

    def as_dict(self) -> dict:
        return {
            "kind": "choice",
            "question_id": self.question_id,
            "question_type": self.question_type or "choice",
            "choice": self.choice,
            "probabilities": dict(self.probabilities),
            "confidence": float(self.confidence),
            "provider_metadata": dict(self.provider_metadata),
        }


@dataclass(frozen=True)
class ScoreAnswer:
    """An ordinal score on a legend, with distribution and confidence (§38)."""

    question_id: str
    score: float
    legend: tuple[float, ...]
    probabilities: dict[str, float]
    confidence: float
    question_type: str = ""
    provider_metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.legend:
            raise SystemOneContractError(f"{self.question_id}: score answer needs a legend")
        validate_probabilities(self.probabilities, where=f"{self.question_id}.probabilities")
        _validate_confidence(self.confidence, where=f"{self.question_id}.confidence")
        object.__setattr__(self, "legend", tuple(float(value) for value in self.legend))
        if not self.question_type:
            object.__setattr__(self, "question_type", "score")
        object.__setattr__(self, "provider_metadata", dict(self.provider_metadata or {}))

    @property
    def kind(self) -> str:
        return "score"

    def as_dict(self) -> dict:
        return {
            "kind": "score",
            "question_id": self.question_id,
            "question_type": self.question_type or "score",
            "score": float(self.score),
            "legend": list(self.legend),
            "probabilities": dict(self.probabilities),
            "confidence": float(self.confidence),
            "provider_metadata": dict(self.provider_metadata),
        }


Answer = NoulAnswer | ChoiceAnswer | ScoreAnswer


def answer_confidence(answer: Answer | None) -> float | None:
    """The confidence of an answer — ``None`` where the kind has none (§4.3).

    The public reader for the Noul case: a Noul has no confidence field, and
    this function never invents one.
    """
    if isinstance(answer, (ChoiceAnswer, ScoreAnswer)):
        return float(answer.confidence)
    return None


#: Shape of a provider payload per kind. Unknown keys are contract errors (§42),
#: which is how an invented ``confidence`` on a Noul is rejected instead of
#: being absorbed (§38/JEV-02).
_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "noul": frozenset({"value"}),
    "choice": frozenset({"choice", "probabilities", "confidence"}),
    "score": frozenset({"score", "legend", "probabilities", "confidence"}),
}


def parse_answer(*, question_id: str, kind: str, payload: Mapping[str, Any]) -> Answer:
    """Translate a provider-shaped payload into the internal answer contract.

    Strict on purpose: an unexpected field (e.g. ``confidence`` on a Noul) is a
    contract error, not something to drop or invent around.
    """
    if kind not in ANSWER_KINDS:
        raise SystemOneContractError(f"unknown answer kind: {kind!r} (known: {ANSWER_KINDS})")
    if not isinstance(payload, Mapping):
        raise SystemOneContractError(f"{question_id}: payload must be a mapping")
    unexpected = set(payload) - _ALLOWED_FIELDS[kind]
    if unexpected:
        raise SystemOneContractError(
            f"{question_id}: unexpected field(s) for a {kind} answer: {sorted(unexpected)}"
        )
    if kind == "noul":
        return NoulAnswer(question_id=question_id, value=float(payload["value"]))
    if kind == "choice":
        return ChoiceAnswer(
            question_id=question_id,
            choice=str(payload["choice"]),
            probabilities={str(k): float(v) for k, v in dict(payload["probabilities"]).items()},
            confidence=float(payload["confidence"]),
        )
    return ScoreAnswer(
        question_id=question_id,
        score=float(payload["score"]),
        legend=tuple(float(value) for value in payload["legend"]),
        probabilities={str(k): float(v) for k, v in dict(payload["probabilities"]).items()},
        confidence=float(payload["confidence"]),
    )


def distribution(answer: Answer) -> dict:
    """The raw probability distribution of an answer, for evidence (§49)."""
    if isinstance(answer, NoulAnswer):
        return {"value": float(answer.value)}
    return dict(answer.probabilities)


# --------------------------------------------------------------------------- #
# state, questions, context                                                    #
# --------------------------------------------------------------------------- #


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def state_fingerprint(payload: Any) -> str:
    """Stable fingerprint of a (sanitized) state — the §38 ``state_fingerprint``."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SystemOneState:
    """What the provider is allowed to see — always the sanitizer's output (§40).

    ``input_scope`` records which scope the payload was filtered to; the
    fingerprint binds an evaluation to exactly this state (§38/§49).

    §7 stamp: every state carries ``source_event_refs``,
    ``workspace_fingerprint``, ``goal_revision``/``policy_revision`` (on the
    context that produced it) and ``created_at``. Those fields are populated by
    the **sanitizer/state builder** — a caller that constructs a state by hand
    leaves them at their neutral default instead of guessing a value. They are
    deliberately excluded from ``fingerprint``, so two builds of the same
    payload stay comparable (§23 dedup).
    """

    payload: dict
    input_scope: str = "task"
    fingerprint: str = ""
    source_event_refs: tuple[str, ...] = ()
    workspace_fingerprint: str = ""
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.payload, dict):
            raise SystemOneContractError("SystemOneState.payload must be a mapping")
        if not self.fingerprint:
            object.__setattr__(self, "fingerprint", state_fingerprint(self.payload))
        object.__setattr__(
            self, "source_event_refs", tuple(str(ref) for ref in (self.source_event_refs or ()))
        )

    def as_dict(self) -> dict:
        return {
            "payload": self.payload,
            "input_scope": self.input_scope,
            "fingerprint": self.fingerprint,
            "source_event_refs": list(self.source_event_refs),
            "workspace_fingerprint": self.workspace_fingerprint,
            "created_at": self.created_at,
        }


@dataclass
class EvaluationContext:
    """Everything an evaluation is bound to (§38 staleness stamp + §67 deadline).

    ``deadline`` is an absolute ``time.time()`` stamp shared with the rest of
    the run (§67); ``budget`` is the shared :class:`skaldir.budget.BudgetAccount`
    when one exists. Both are optional — the adapter degrades, never lies.

    §4.1 adds the run-identity fields the contract asks for. All of them have
    neutral defaults so every existing call site keeps working unchanged:
    ``workspace_fingerprint``/``caller``/``question_pack_id``/
    ``question_pack_version`` (identity), ``budget_reservation`` (reserved
    units — ``0.0`` means "nothing was reserved", never a fabricated number) and
    ``privacy_scope`` ("task" by default: the smallest useful scope).
    """

    run_id: str
    goal_id: str
    goal_revision: int = 0
    policy_revision: int = 0
    event_cursor: int = 0
    project_id: str = ""
    shadow: bool = False
    risk_class: str = "R0"
    deadline: float | None = None
    budget: Any = None
    model: str = ""
    # -- §4.1 additions (all defaulted, so nothing existing breaks) ---------
    workspace_fingerprint: str = ""
    caller: str = ""
    question_pack_id: str = ""
    question_pack_version: str = ""
    budget_reservation: float = 0.0
    privacy_scope: str = "task"

    def remaining_deadline(self, *, now: float | None = None) -> float | None:
        if self.deadline is None:
            return None
        moment = time.time() if now is None else now
        return max(0.0, float(self.deadline) - moment)



def new_evaluation_id(provider: str) -> str:
    return f"soe-{provider}-{uuid.uuid4().hex[:12]}"


def provider_mode_for(*, access_mode: str, live_jev: bool) -> str:
    """Derive the §4.2 ``provider_mode`` from the access mode and ``live_jev``.

    The derivation makes the §4.2 rules structural instead of a convention:

    * only ``access_mode == 'live'`` **and** ``live_jev is True`` may read
      ``typesafe_jev_live`` — a "live" access mode without a live call is an
      internal contradiction and is refused rather than mislabelled;
    * an LLM-adapter result can therefore never read ``typesafe_jev_live``;
    * a scripted/recorded evaluation reads ``replay_fixture``;
    * everything else (no provider, blocked access) reads ``disabled``.
    """
    mode = str(access_mode or "")
    if mode == ACCESS_LIVE:
        if not live_jev:
            raise SystemOneContractError(
                "access mode 'live' without a live Jev call — a result must never be "
                "labelled typesafe_jev_live unless the call really happened (§4.2)"
            )
        return PROVIDER_MODE_TYPESAFE_LIVE
    if mode == ACCESS_LLM_ADAPTER:
        if live_jev:
            raise SystemOneContractError(
                "an LLM-adapter evaluation may never claim live_jev (§4.2/§14)"
            )
        return PROVIDER_MODE_LLM_ADAPTER
    if mode == ACCESS_FIXTURE:
        return PROVIDER_MODE_REPLAY_FIXTURE
    return PROVIDER_MODE_DISABLED


# --------------------------------------------------------------------------- #
# the internal evaluation contract (§38)                                       #
# --------------------------------------------------------------------------- #


@dataclass
class SystemOneEvaluation:
    """The provider-neutral result of one evaluation (§38).

    ``live_jev`` is the honesty gate: only an authorized live TypeSafe call may
    set it to ``True`` (§41/§49). Everything else reports ``False`` and is
    labelled ``llm_system_one_adapter``/``disabled``.
    """

    evaluation_id: str
    run_id: str
    goal_id: str
    goal_revision: int
    policy_revision: int
    event_cursor: int
    provider: str
    provider_model: str
    provider_access_mode: str
    question_pack_id: str
    question_pack_version: str
    state_fingerprint: str
    input_scope: str
    started_at: float
    completed_at: float
    latency_ms: int
    answers: dict[str, Answer] = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    raw_ref: str = ""
    live_jev: bool = False
    status: str = STATUS_OK
    blocker: dict | None = None
    shadow: bool = False
    attempts: int = 1
    sdk_retries: int = 0
    #: §4.2 — exactly one of ``typesafe_jev_live``, ``llm_system_one_adapter``,
    #: ``replay_fixture``, ``disabled``. Derived by :func:`build_evaluation`;
    #: validated in :meth:`__post_init__`, so a lie is not constructible.
    provider_mode: str = ""

    def __post_init__(self) -> None:
        """Enforce the §4.2 labelling rules on every construction path."""
        mode = str(self.provider_mode or "")
        if not mode:
            object.__setattr__(
                self,
                "provider_mode",
                provider_mode_for(
                    access_mode=self.provider_access_mode, live_jev=bool(self.live_jev)
                ),
            )
            return
        if mode not in PROVIDER_MODES:
            raise SystemOneContractError(
                f"unknown provider_mode: {mode!r} (known: {list(PROVIDER_MODES)})"
            )
        if mode == PROVIDER_MODE_TYPESAFE_LIVE and not self.live_jev:
            raise SystemOneContractError(
                "provider_mode 'typesafe_jev_live' requires live_jev=True (§4.2)"
            )
        if self.live_jev and mode != PROVIDER_MODE_TYPESAFE_LIVE:
            raise SystemOneContractError(
                f"live_jev=True contradicts provider_mode {mode!r} (§4.2)"
            )
        if mode == PROVIDER_MODE_LLM_ADAPTER and str(self.provider_access_mode) == ACCESS_LIVE:
            raise SystemOneContractError(
                "an evaluation with live access can never be labelled "
                "llm_system_one_adapter (§4.2)"
            )


    # -- accessors ---------------------------------------------------------

    def answer(self, question_id: str) -> Answer | None:
        return self.answers.get(question_id)

    def noul(self, question_id: str) -> float | None:
        answer = self.answers.get(question_id)
        return float(answer.value) if isinstance(answer, NoulAnswer) else None

    def confidence(self, question_id: str) -> float | None:
        """Confidence where the answer kind defines one — never invented."""
        answer = self.answers.get(question_id)
        if isinstance(answer, (ChoiceAnswer, ScoreAnswer)):
            return float(answer.confidence)
        return None

    def evidence(self, *, mode: str = "", route: str = "", policy_rule: str = "") -> dict:
        """§49 evidence fields — always carries the honest provider labelling."""
        return {
            "evaluation_id": self.evaluation_id,
            "run_id": self.run_id,
            "goal_id": self.goal_id,
            "goal_revision": self.goal_revision,
            "policy_revision": self.policy_revision,
            "event_cursor": self.event_cursor,
            "question_pack_id": self.question_pack_id,
            "question_pack_version": self.question_pack_version,
            "state_fingerprint": self.state_fingerprint,
            "input_scope": self.input_scope,
            "provider": self.provider,
            "provider_model": self.provider_model,
            "provider_access_mode": self.provider_access_mode,
            # §4.2: the four-value labelling, never the raw access mode alone
            "provider_mode": self.provider_mode,
            "live_jev": bool(self.live_jev),
            "status": self.status,
            "mode": mode or ("shadow" if self.shadow else "advisory"),
            "route": route,
            "policy_rule": policy_rule,
            "latency_ms": int(self.latency_ms),
            "usage": dict(self.usage),
            "attempts": int(self.attempts),
            "sdk_retries": int(self.sdk_retries),
            "raw_ref": self.raw_ref,
            "probabilities": {
                question_id: distribution(answer) for question_id, answer in self.answers.items()
            },
            "confidence": {
                question_id: float(answer.confidence)
                for question_id, answer in self.answers.items()
                if isinstance(answer, (ChoiceAnswer, ScoreAnswer))
            },
        }


    def as_dict(self) -> dict:
        payload = self.evidence()
        payload["answers"] = {
            question_id: answer.as_dict() for question_id, answer in self.answers.items()
        }
        return payload


def build_evaluation(
    *,
    context: EvaluationContext,
    questions: Any,
    state: SystemOneState,
    provider: str,
    provider_model: str,
    access_mode: str,
    answers: Mapping[str, Answer] | None = None,
    started_at: float | None = None,
    completed_at: float | None = None,
    usage: Mapping[str, Any] | None = None,
    raw_ref: str = "",
    live_jev: bool = False,
    shadow: bool = False,
    attempts: int = 1,
    sdk_retries: int = 0,
    status: str = STATUS_OK,
    blocker: Mapping[str, Any] | None = None,
    question_pack_id: str | None = None,
    question_pack_version: str | None = None,
    provider_mode: str = "",
) -> SystemOneEvaluation:
    """Assemble the internal evaluation record — the one construction path.

    Every adapter uses this so the §38 fields cannot drift between
    implementations, and so ``live_jev`` has to be set explicitly. §4.2's
    ``provider_mode`` is derived here from ``access_mode``/``live_jev`` unless a
    caller states it — and the record accepts the stated value only when it
    agrees with the derivation's rules.
    """
    start = time.time() if started_at is None else float(started_at)
    end = time.time() if completed_at is None else float(completed_at)
    return SystemOneEvaluation(
        evaluation_id=new_evaluation_id(provider),
        run_id=context.run_id,
        goal_id=context.goal_id,
        goal_revision=int(context.goal_revision),
        policy_revision=int(context.policy_revision),
        event_cursor=int(context.event_cursor),
        provider=provider,
        provider_model=provider_model,
        provider_access_mode=access_mode,
        question_pack_id=question_pack_id or str(getattr(questions, "id", "")),
        question_pack_version=question_pack_version or str(getattr(questions, "version", "")),
        state_fingerprint=state.fingerprint,
        input_scope=state.input_scope,
        started_at=start,
        completed_at=end,
        latency_ms=max(0, int((end - start) * 1000)),
        answers=dict(answers or {}),
        usage=dict(usage or {}),
        raw_ref=raw_ref,
        live_jev=bool(live_jev),
        status=status,
        blocker=dict(blocker) if blocker else None,
        shadow=bool(shadow),
        attempts=int(attempts),
        sdk_retries=int(sdk_retries),
        provider_mode=provider_mode
        or provider_mode_for(access_mode=access_mode, live_jev=bool(live_jev)),
    )



# --------------------------------------------------------------------------- #
# the provider-neutral protocol (§37)                                          #
# --------------------------------------------------------------------------- #


@runtime_checkable
class SystemOneEvaluator(Protocol):
    """Provider-neutral System-One evaluation contract (§37).

    Implementations: ``DisabledSystemOneEvaluator``, ``LLMSystemOneAdapter``
    (usable today) and ``TypeSafeJevEvaluator`` (live, behind authorized
    credentials). All three are interchangeable against the same contract test
    suite — the Pass condition of §79.
    """

    async def evaluate(
        self,
        *,
        state: SystemOneState,
        questions: Any,  # skaldir.system_one_questions.QuestionSet
        context: EvaluationContext,
    ) -> SystemOneEvaluation:  # pragma: no cover — protocol
        ...


class DisabledSystemOneEvaluator:
    """The default evaluator: no answers, access mode ``disabled`` (§36/§98).

    Returning an empty, valid evaluation (instead of raising) is what lets the
    caller keep its existing deterministic path untouched — the safe fallback
    JEV-20 requires.
    """

    provider = PROVIDER_DISABLED
    provider_model = "disabled"
    provider_access_mode = ACCESS_DISABLED

    async def evaluate(
        self,
        *,
        state: SystemOneState,
        questions: Any,
        context: EvaluationContext,
    ) -> SystemOneEvaluation:
        return build_evaluation(
            context=context,
            questions=questions,
            state=state,
            provider=self.provider,
            provider_model=self.provider_model,
            access_mode=self.provider_access_mode,
            answers={},
            usage={},
            shadow=bool(context.shadow),
        )
