"""System-One decision policy — errors, deadlines, staleness, risk, shadow.

This module holds the rules that keep System One *advisory*:

* §43/JEV-09 error + deadline policy: 401 is not retried, 422 is a contract
  error, 429/529 retry only inside the shared remaining deadline with bounded
  backoff, SDK-internal retries are part of the total attempt count, and a
  provider error never disables runtime policy (:class:`SystemOneGate` degrades
  to "no advisory" and keeps the caller's own path).
* §92/JEV-14 staleness binding: a late answer against a newer goal/policy/
  event/question-pack version is inert — :meth:`SystemOneGate.consume` refuses
  it and never calls the decision function with it.
* §44/§89 risk classes R0..R4 with R4 never authorizable by System One alone,
  and per-risk thresholds that are frozen before holdout (structure first; the
  numbers come from the project's own dev data, never from feel).
* §45 shadow harness: observe, record, compare — never mutate a run. Every
  record states ``mutations=0`` and ``route=None``, and the harness *asserts*
  those invariants (:func:`assert_shadow_advisory`) instead of commenting them.
* §32/§47/§90 route ladder: the §32 semantic suggestions are derived from the
  triage answers by named, testable rules (:func:`propose_route`) — the shadow
  record and the voice triage pack use exactly the same function, so there is
  no second reading of the same answers.

Deadlines are absolute ``time.time()`` stamps shared with the rest of the run
(§67); ``plan_retry`` only ever swallows part of what is still left.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from .system_one import (
    ChoiceAnswer,
    NoulAnswer,
    ScoreAnswer,
    SystemOneBlockedExternal,
    SystemOneContractError,
    SystemOneError,
    SystemOneEvaluation,
)

# --------------------------------------------------------------------------- #
# §43/JEV-09 — error classification and retry/deadline policy                   #
# --------------------------------------------------------------------------- #

STATUS_AUTH = 401
STATUS_CONTRACT = 422
STATUS_RATE_LIMIT = 429
STATUS_OVERLOADED = 529

REASON_AUTH = "auth_not_retryable"
REASON_CONTRACT = "contract_error"
REASON_RATE_LIMIT = "rate_limited"
REASON_OVERLOADED = "provider_overloaded"
REASON_DEADLINE = "deadline_exhausted"
REASON_MAX_ATTEMPTS = "max_attempts"
REASON_UNCLASSIFIED = "unclassified"

DEFAULT_BACKOFF_BASE = 0.25
DEFAULT_BACKOFF_CAP = 2.0
MAX_ATTEMPTS = 3


def classify_status(status: int | None) -> str:
    """Map an HTTP-ish status to the documented System-One case (§43)."""
    if status == STATUS_AUTH:
        return "auth"
    if status == STATUS_CONTRACT:
        return "contract"
    if status == STATUS_RATE_LIMIT:
        return "rate_limit"
    if status == STATUS_OVERLOADED:
        return "overloaded"
    return "unknown"


def backoff_delay(
    attempt: int, *, base: float = DEFAULT_BACKOFF_BASE, cap: float = DEFAULT_BACKOFF_CAP
) -> float:
    """Bounded exponential backoff — never larger than the cap."""
    exponent = max(0, int(attempt) - 1)
    return float(min(cap, base * (2**exponent)))


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    delay: float
    reason: str
    status: int | None = None
    attempts_total: int = 0


def plan_retry(
    *,
    status: int | None = None,
    attempt: int = 1,
    max_attempts: int = MAX_ATTEMPTS,
    remaining_deadline: float | None = None,
    timeout: bool = False,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    backoff_cap: float = DEFAULT_BACKOFF_CAP,
) -> RetryDecision:
    """The shared retry/deadline decision (§43).

    Rules, in order:

    * 401 → never retried (the credential is wrong; retrying burns the budget);
    * 422 → contract error, reported to code owners, never retried;
    * 429/529/timeout → retried only while attempts remain **and** the bounded
      backoff still fits into the shared remaining deadline.
    """
    case = classify_status(status)
    if case == "auth":
        return RetryDecision(False, 0.0, REASON_AUTH, status, attempt)
    if case == "contract":
        return RetryDecision(False, 0.0, REASON_CONTRACT, status, attempt)
    retryable = case in ("rate_limit", "overloaded") or (timeout and status is None)
    if not retryable:
        return RetryDecision(False, 0.0, REASON_UNCLASSIFIED, status, attempt)
    if attempt >= max_attempts:
        return RetryDecision(False, 0.0, REASON_MAX_ATTEMPTS, status, attempt)
    delay = backoff_delay(attempt, base=backoff_base, cap=backoff_cap)
    if remaining_deadline is not None and delay > remaining_deadline:
        # the shared deadline has the last word: no retry outside it (§67)
        return RetryDecision(False, 0.0, REASON_DEADLINE, status, attempt)
    reason = REASON_RATE_LIMIT if case == "rate_limit" else REASON_OVERLOADED
    return RetryDecision(True, delay, reason, status, attempt)


def total_attempts(*, client_attempts: int, sdk_retries: int) -> int:
    """SDK-internal retries are visible in the total attempt count (§43)."""
    return max(0, int(client_attempts)) + max(0, int(sdk_retries))


# --------------------------------------------------------------------------- #
# §92/JEV-14 — staleness / version binding                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VersionStamp:
    """The live versions an answer must still match to be usable."""

    goal_revision: int = 0
    policy_revision: int = 0
    event_cursor: int = 0
    question_pack_version: str = ""

    def __post_init__(self) -> None:
        if self.goal_revision < 0 or self.policy_revision < 0 or self.event_cursor < 0:
            raise SystemOneContractError("version stamps must not be negative")


def is_stale(evaluation: SystemOneEvaluation, *, live: VersionStamp) -> bool:
    """True when the answer was evaluated against a superseded version (§92).

    Any older revision, an older event cursor, or a different question-pack
    version makes the answer inert — it is refused instead of being reasoned
    around.
    """
    if int(evaluation.goal_revision) < int(live.goal_revision):
        return True
    if int(evaluation.policy_revision) < int(live.policy_revision):
        return True
    if int(evaluation.event_cursor) < int(live.event_cursor):
        return True
    if live.question_pack_version and (
        str(evaluation.question_pack_version) != str(live.question_pack_version)
    ):
        return True
    return False


def stale_reason(evaluation: SystemOneEvaluation, *, live: VersionStamp) -> str:
    parts: list[str] = []
    if int(evaluation.goal_revision) < int(live.goal_revision):
        parts.append(f"goal_revision {evaluation.goal_revision} < {live.goal_revision}")
    if int(evaluation.policy_revision) < int(live.policy_revision):
        parts.append(f"policy_revision {evaluation.policy_revision} < {live.policy_revision}")
    if int(evaluation.event_cursor) < int(live.event_cursor):
        parts.append(f"event_cursor {evaluation.event_cursor} < {live.event_cursor}")
    if live.question_pack_version and (
        str(evaluation.question_pack_version) != str(live.question_pack_version)
    ):
        parts.append(
            f"question_pack_version {evaluation.question_pack_version!r} "
            f"!= {live.question_pack_version!r}"
        )
    return "stale: " + ", ".join(parts) if parts else ""


@dataclass(frozen=True)
class AppliedDecision:
    """Outcome of consuming an evaluation: inert, or a chosen route."""

    inert: bool
    route: str | None
    reason: str
    risk_class: str = ""


# --------------------------------------------------------------------------- #
# §44/§89 — risk classes and calibrated thresholds                              #
# --------------------------------------------------------------------------- #

R0 = "R0"
R1 = "R1"
R2 = "R2"
R3 = "R3"
R4 = "R4"

REASON_R4 = "R4_never_system_one_alone"
REASON_RISK_CLASS = "risk_class_requires_system_two"
REASON_BELOW_THRESHOLD = "below_threshold"
#: §13: a reading inside the calibrated uncertainty band is escalated, never
#: guessed into an action.
REASON_UNCERTAIN_BAND = "uncertainty_band_escalate"


@dataclass(frozen=True)
class RiskPolicy:
    risk_class: str
    description: str
    may_automate: bool
    require_system_two: bool
    runtime_authorization: bool


RISK_POLICY: dict[str, RiskPolicy] = {
    R0: RiskPolicy(R0, "observation", True, False, False),
    R1: RiskPolicy(R1, "reversible internal routing", True, False, False),
    R2: RiskPolicy(R2, "plan/voice intervention", False, True, False),
    R3: RiskPolicy(R3, "external/real effect", False, True, True),
    R4: RiskPolicy(R4, "security/secrets/irreversible", False, True, True),
}


def risk_policy(risk_class: str) -> RiskPolicy:
    policy = RISK_POLICY.get(risk_class)
    if policy is None:
        raise SystemOneContractError(
            f"unknown risk class: {risk_class!r} (known: {sorted(RISK_POLICY)})"
        )
    return policy


@dataclass(frozen=True)
class ThresholdProfile:
    """Per-risk thresholds, frozen before holdout (§44/§89). Noul has its own
    threshold: it has no confidence, so it can never be handled like
    Choice/Score (which combine confidence and distribution).

    §13 completeness: the profile carries the identity of the calibration
    (``threshold_profile_id``, ``question_pack_version``, ``dataset_version``)
    and the **measured** rates. A rate nobody measured is ``None`` — this
    dataclass never invents a calibration number, and :meth:`measured` says
    honestly whether a full set exists. There is deliberately **no global
    threshold**: ``per_risk`` covers every action class R0..R4 (the action
    class *is* the risk class of §13), and a profile missing one is refused.
    """

    profile_id: str
    frozen: bool
    per_risk: dict[str, float]
    noul_threshold: float
    choice_threshold: float
    score_threshold: float
    provenance: dict = field(default_factory=dict)
    # -- §13 additions (all defaulted: an unmeasured profile stays honest) ---
    question_pack_version: str = ""
    dataset_version: str = ""
    measured_precision: float | None = None
    measured_recall: float | None = None
    false_positive_rate: float | None = None
    false_negative_rate: float | None = None
    #: Calibrated uncertainty band per action class: inside the band System One
    #: escalates instead of guessing. Empty = no band was measured, and then no
    #: number is invented to stand in for one.
    uncertainty_band: dict[str, tuple[float, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        missing = sorted(set(RISK_POLICY) - set(self.per_risk))
        if missing:
            raise SystemOneContractError(
                f"threshold profile {self.profile_id!r} has no threshold for action "
                f"class(es) {missing} — thresholds are per action class, never global (§13)"
            )
        object.__setattr__(
            self,
            "uncertainty_band",
            {
                str(risk): (float(bounds[0]), float(bounds[1]))
                for risk, bounds in dict(self.uncertainty_band or {}).items()
            },
        )
        for risk, bounds in self.uncertainty_band.items():
            if len(bounds) != 2 or bounds[0] > bounds[1]:
                raise SystemOneContractError(
                    f"uncertainty band for {risk} must be (low, high) with low <= high"
                )
        for name in (
            "measured_precision",
            "measured_recall",
            "false_positive_rate",
            "false_negative_rate",
        ):
            value = getattr(self, name)
            if value is not None and not 0.0 <= float(value) <= 1.0:
                raise SystemOneContractError(f"{name} must be within 0..1: got {value!r}")

    # -- identity ----------------------------------------------------------- #

    @property
    def threshold_profile_id(self) -> str:
        """§13 spelling of the profile identity (``profile_id`` stays the alias)."""
        return self.profile_id

    @property
    def measured(self) -> bool:
        """True only when all four rates were really measured."""
        return all(
            getattr(self, name) is not None
            for name in (
                "measured_precision",
                "measured_recall",
                "false_positive_rate",
                "false_negative_rate",
            )
        )

    # -- edits (all refused on a frozen profile: holdout integrity §89) ----- #

    def _refuse_if_frozen(self, what: str) -> None:
        if self.frozen:
            raise SystemOneContractError(
                f"threshold profile {self.profile_id!r} is frozen before holdout; "
                f"cannot change {what} — calibrate a new profile id instead"
            )

    def with_threshold(self, risk_class: str, value: float) -> ThresholdProfile:
        """A frozen profile refuses edits — holdout integrity (§89)."""
        self._refuse_if_frozen("a threshold")
        updated = dict(self.per_risk)
        updated[risk_class] = float(value)
        return replace(self, per_risk=updated)

    def with_uncertainty_band(
        self, risk_class: str, bounds: tuple[float, float]
    ) -> ThresholdProfile:
        """Set the calibrated escalation band of one action class (§13)."""
        self._refuse_if_frozen("an uncertainty band")
        updated = dict(self.uncertainty_band)
        updated[str(risk_class)] = (float(bounds[0]), float(bounds[1]))
        return replace(self, uncertainty_band=updated)

    def with_measurements(
        self,
        *,
        measured_precision: float,
        measured_recall: float,
        false_positive_rate: float,
        false_negative_rate: float,
        dataset_version: str = "",
        question_pack_version: str = "",
    ) -> ThresholdProfile:
        """Attach the rates that were really measured — then freeze."""
        self._refuse_if_frozen("the measurements")
        return replace(
            self,
            measured_precision=float(measured_precision),
            measured_recall=float(measured_recall),
            false_positive_rate=float(false_positive_rate),
            false_negative_rate=float(false_negative_rate),
            dataset_version=dataset_version or self.dataset_version,
            question_pack_version=question_pack_version or self.question_pack_version,
        )

    def frozen_profile(self) -> ThresholdProfile:
        """Freeze this profile for the holdout (§89)."""
        return replace(self, frozen=True)

    def threshold_for(self, risk_class: str) -> float:
        return float(self.per_risk.get(risk_class, 1.0))

    def threshold_for_action_class(self, action_class: str) -> float:
        """§13 spells the same thing "per action class"; one number, one place."""
        return self.threshold_for(action_class)

    def band_for(self, risk_class: str) -> tuple[float, float] | None:
        """The calibrated band of an action class — ``None`` when never measured."""
        return self.uncertainty_band.get(str(risk_class))



def calibrate_thresholds(
    samples: list[dict],
    *,
    profile_id: str,
    frozen: bool = False,
    margin: float = 0.0,
) -> ThresholdProfile:
    """Derive per-risk thresholds from the project's own dev samples (§89).

    ``samples`` are ``{"risk_class", "value", "correct"}`` records. The
    threshold is the smallest value that was still correct, shifted by
    ``margin`` — explicitly a *structure* here: the numbers only become
    meaningful with real dev data, and are frozen before holdout.
    """
    per_risk: dict[str, float] = {}
    for risk_class in RISK_POLICY:
        correct = [
            float(sample["value"])
            for sample in samples
            if sample.get("risk_class") == risk_class and sample.get("correct")
        ]
        per_risk[risk_class] = max(0.0, min(1.0, (min(correct) if correct else 1.0) + margin))
    noul = [
        float(sample["value"])
        for sample in samples
        if sample.get("answer_kind", "noul") == "noul" and sample.get("correct")
    ]
    return ThresholdProfile(
        profile_id=profile_id,
        frozen=frozen,
        per_risk=per_risk,
        noul_threshold=max(0.0, min(1.0, (min(noul) if noul else 1.0) + margin)),
        choice_threshold=0.0,
        score_threshold=0.0,
        provenance={"sample_count": len(samples), "method": "min_correct_value+margin"},
    )


@dataclass(frozen=True)
class AuthorizationDecision:
    auto: bool
    require_system_two: bool
    runtime_authorization: bool
    threshold: float | None
    reason: str
    #: §13: inside the calibrated uncertainty band the right move is escalation,
    #: not a guess in either direction.
    escalate: bool = False


def _reading_of(answer) -> float | None:
    """The scalar reading of an answer for the band check — never invented."""
    if isinstance(answer, NoulAnswer):
        return float(answer.value)
    if isinstance(answer, (ChoiceAnswer, ScoreAnswer)):
        probabilities = dict(getattr(answer, "probabilities", {}) or {})
        return max((float(value) for value in probabilities.values()), default=None)
    return None


def authorize(risk_class: str, answer, *, profile: ThresholdProfile) -> AuthorizationDecision:
    """May this answer drive an action on its own? R4: never (§44).

    §13: when the reading falls inside the calibrated uncertainty band of the
    action class, the decision escalates to System Two instead of guessing —
    before any threshold comparison, and regardless of the risk class.
    """
    policy = risk_policy(risk_class)
    band = profile.band_for(risk_class)
    if band is not None:
        reading = _reading_of(answer)
        if reading is not None and band[0] <= reading <= band[1]:
            return AuthorizationDecision(
                False,
                True,
                policy.runtime_authorization,
                profile.threshold_for(risk_class),
                REASON_UNCERTAIN_BAND,
                escalate=True,
            )
    if risk_class == R4:
        return AuthorizationDecision(False, True, True, None, REASON_R4)

    if not policy.may_automate:
        return AuthorizationDecision(
            False,
            policy.require_system_two,
            policy.runtime_authorization,
            profile.threshold_for(risk_class),
            REASON_RISK_CLASS,
        )
    threshold = profile.threshold_for(risk_class)
    if isinstance(answer, NoulAnswer):
        used = max(float(profile.noul_threshold), float(threshold))
        passed = float(answer.value) >= used
    elif isinstance(answer, ScoreAnswer):
        top = max(answer.probabilities.values()) if answer.probabilities else 0.0
        passed = float(answer.confidence) >= profile.score_threshold and top >= threshold
        used = profile.score_threshold
    elif isinstance(answer, ChoiceAnswer):
        top = max(answer.probabilities.values()) if answer.probabilities else 0.0
        passed = float(answer.confidence) >= profile.choice_threshold and top >= threshold
        used = profile.choice_threshold
    else:  # pragma: no cover — defensive: an unknown answer can never authorize
        return AuthorizationDecision(False, True, False, None, REASON_BELOW_THRESHOLD)
    return AuthorizationDecision(
        passed,
        not passed,
        policy.runtime_authorization,
        used,
        "threshold_met" if passed else REASON_BELOW_THRESHOLD,
    )


# --------------------------------------------------------------------------- #
# §43/§92 — the gate every consumer uses                                        #
# --------------------------------------------------------------------------- #


class SystemOneGate:
    """Single entry point for Voice/Completion/Computer Use (§27).

    Responsibilities:

    * degrade to "no advisory" on any provider failure — and **never** change
      the runtime flags or policy because of a provider error (§43);
    * refuse staleness: :meth:`consume` does not call the decision function for
      an inert evaluation (§92/JEV-14);
    * keep a structured record of errors, blockers and refusals.
    """

    def __init__(
        self, evaluator, *, flags=None, emit: Callable[[str, dict], None] | None = None
    ) -> None:
        self.evaluator = evaluator
        self.flags = flags
        self.emit = emit or (lambda kind, payload: None)
        self.errors: list[dict] = []
        self.stale: list[dict] = []

    @property
    def enabled(self) -> bool:
        """Runtime policy state — a provider error must never flip this (§43)."""
        return bool(getattr(self.flags, "enabled", True))

    async def advise(self, *, state, questions, context) -> SystemOneEvaluation | None:
        """Run the evaluator; any failure degrades to ``None`` (caller's path)."""
        try:
            return await self.evaluator.evaluate(state=state, questions=questions, context=context)
        except SystemOneBlockedExternal as exc:
            record = dict(exc.blocker)
            self.errors.append(record)
            self.emit("system_one_blocked", record)
            return None
        except SystemOneError as exc:
            record = {
                "state": "error",
                "provider": getattr(self.evaluator, "provider", ""),
                "error": type(exc).__name__,
                "status": getattr(exc, "status", None),
                "reason": str(exc)[:300],
            }
            self.errors.append(record)
            self.emit("system_one_error", record)
            return None
        except Exception as exc:  # noqa: BLE001 — a provider must never break the run
            record = {
                "state": "error",
                "provider": getattr(self.evaluator, "provider", ""),
                "error": type(exc).__name__,
                "status": getattr(exc, "status", None),
                "reason": str(exc)[:300],
            }
            self.errors.append(record)
            self.emit("system_one_error", record)
            return None

    def consume(
        self,
        evaluation: SystemOneEvaluation | None,
        *,
        live: VersionStamp,
        decide: Callable[[SystemOneEvaluation], AppliedDecision],
    ) -> AppliedDecision:
        """Consume an evaluation, or refuse it as inert when stale (§92)."""
        if evaluation is None:
            return AppliedDecision(True, None, "no_evaluation")
        if is_stale(evaluation, live=live):
            reason = stale_reason(evaluation, live=live)
            record = {
                "evaluation_id": evaluation.evaluation_id,
                "reason": reason,
                "goal_revision": evaluation.goal_revision,
                "event_cursor": evaluation.event_cursor,
            }
            self.stale.append(record)
            self.emit("system_one_stale", record)
            return AppliedDecision(True, None, reason)
        decision = decide(evaluation)
        self.emit(
            "system_one_decision",
            evaluation.evidence(route=decision.route or "", mode="active"),
        )
        return decision


# --------------------------------------------------------------------------- #
# §32/§47/§90 — the route ladder the triage answers may propose                 #
# --------------------------------------------------------------------------- #

ROUTE_OBSERVE = "observe"
ROUTE_HINT = "hint"
ROUTE_INVESTIGATE = "investigate"
ROUTE_PAUSE_AND_INVESTIGATE = "pause_and_investigate"
ROUTE_REQUEST_VERIFICATION = "request_verification"
ROUTE_REQUEST_USER_INPUT = "request_user_input"

#: §32's allowed semantic suggestions, in decision-ladder order.
ROUTE_LADDER: tuple[str, ...] = (
    ROUTE_OBSERVE,
    ROUTE_HINT,
    ROUTE_INVESTIGATE,
    ROUTE_PAUSE_AND_INVESTIGATE,
    ROUTE_REQUEST_VERIFICATION,
    ROUTE_REQUEST_USER_INPUT,
)

#: §48: System One may *ask* for System Two — it never escalates by itself.
#: Deliberately outside the ladder: the escalation is a request, not a rung.
ROUTE_ESCALATE_SYSTEM_TWO = "escalate_system_two"

#: The read threshold that turns a Noul probability into a reading. It is a
#: *read* threshold, never a calibrated number: the calibrated, per-risk
#: thresholds live in :class:`ThresholdProfile` and are frozen before holdout
#: (§89). Nothing here authorizes an action (§36).
READ_THRESHOLD = 0.5

RULE_RECOVERY_SYSTEM_TWO = "recovery_system_two_needed"
RULE_RECOVERY_SIDE_EFFECT_UNSAFE_REPLAY = "recovery_possible_side_effect_unsafe_replay"
RULE_RECOVERY_UNKNOWN_STATE = "recovery_state_unclear_replay_safe"
RULE_COMPLETION_CLAIM_WITHOUT_EVIDENCE = "completion_claim_without_current_evidence"
RULE_COMPLETION_INDEPENDENT_VERIFICATION = "completion_independent_verification_required"
RULE_LOOP_WITHOUT_PROGRESS = "loop_no_progress_without_new_information"
RULE_DRIFT_NON_GOAL_CONFLICT = "drift_declared_non_goal_conflict"
RULE_DRIFT_SCOPE_EXPANSION = "drift_scope_expansion"
RULE_DRIFT_GOAL_SUPPORT_MISSING = "drift_goal_support_missing"
RULE_UNTRUSTED_AUTHORITY_ATTEMPT = "untrusted_content_authority_attempt"
RULE_UNTRUSTED_SECRET_REQUEST = "untrusted_content_secret_request"
RULE_UNTRUSTED_GOAL_POLICY_CONFLICT = "untrusted_content_goal_policy_conflict"
RULE_RESEARCH_VERSION_SENSITIVE = "research_missing_version_sensitive_fact"
RULE_RESEARCH_MISSING_FACT = "research_missing_fact_hint"
RULE_LADDER_EXHAUSTED = "no_rule_matched"


@dataclass(frozen=True)
class ProposedRoute:
    """One advisory reading of a triage answer set: route + the rule that read it.

    Advisory by construction: ``propose_route`` returns a *proposal*. The
    selected route of a run stays with the caller's own policy (shadow: never).
    """

    route: str
    rule: str
    basis: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"route": self.route, "rule": self.rule, "basis": list(self.basis)}


def _reading(evaluation: SystemOneEvaluation | None, question_id: str) -> float | None:
    """The Noul reading of one question — ``None`` when it was not asked."""
    if evaluation is None:
        return None
    value = evaluation.noul(question_id)
    return None if value is None else float(value)


def _choice_of(evaluation: SystemOneEvaluation | None, question_id: str) -> str:
    if evaluation is None:
        return ""
    choice = getattr(evaluation.answer(question_id), "choice", None)
    return str(choice) if choice is not None else ""


def _high(value: float | None, threshold: float = READ_THRESHOLD) -> bool:
    return value is not None and float(value) >= threshold


def _low(value: float | None, threshold: float = READ_THRESHOLD) -> bool:
    return value is not None and float(value) < threshold


def _present(evaluation: SystemOneEvaluation, question_ids: tuple[str, ...]) -> tuple[str, ...]:
    """The subset of ``question_ids`` the evaluation really answered."""
    return tuple(q for q in question_ids if evaluation.answer(q) is not None)


def propose_route(evaluation: SystemOneEvaluation | None) -> ProposedRoute:
    """Read one §47 triage answer set on the §32 ladder (§47/§90/JEV-12).

    Rules, in order — the first match wins and its name travels with the
    record (§49: the rule that read the answer):

    1. an explicit System-Two request (§48) — ``voice.recovery.system_two_needed``
       or ``computer_use.recovery_needed``;
    2. a possibly executed side effect whose replay safety is unknown or unsafe
       — never a blind replay, even when the System-Two reading stayed low;
    3. a real loop: same strategy, no new information, no measurable progress;
    4. a completion claim without current/revision-matching evidence, or an
       outstanding independent verification (§35/JEV-13: it asks, it never
       certifies);
    5. drift: a declared non-goal conflict (the user owns the goal), a scope
       expansion, or work that no longer supports the goal;
    6. research: a missing external fact, escalated to an investigation when it
       is version-sensitive (§34);
    7. an unclear recovery state whose replay is safe;
    8. otherwise: observe.

    A question that was not asked is not a reading — it never triggers a rule.
    """
    if evaluation is None:
        return ProposedRoute(ROUTE_OBSERVE, RULE_LADDER_EXHAUSTED, ())

    # §8.2/§28: an untrusted-content reading is the *first* thing read. It can
    # never widen scope, release a secret, grant an approval or set completion —
    # the strongest it may do is ask for System Two / the user, and only the
    # real policy check decides what happens next.
    untrusted_ids = (
        "voice.untrusted.content_attempts_to_change_authority",
        "voice.untrusted.content_requests_secret_exposure",
        "voice.untrusted.content_conflicts_with_goal_or_policy",
    )
    authority_attempt = _reading(
        evaluation, "voice.untrusted.content_attempts_to_change_authority"
    )
    secret_request = _reading(evaluation, "voice.untrusted.content_requests_secret_exposure")
    policy_conflict = _reading(
        evaluation, "voice.untrusted.content_conflicts_with_goal_or_policy"
    )
    if _high(authority_attempt):
        return ProposedRoute(
            ROUTE_ESCALATE_SYSTEM_TWO,
            RULE_UNTRUSTED_AUTHORITY_ATTEMPT,
            _present(evaluation, untrusted_ids),
        )
    if _high(secret_request):
        return ProposedRoute(
            ROUTE_ESCALATE_SYSTEM_TWO,
            RULE_UNTRUSTED_SECRET_REQUEST,
            _present(evaluation, untrusted_ids),
        )
    if _high(policy_conflict):
        return ProposedRoute(
            ROUTE_REQUEST_USER_INPUT,
            RULE_UNTRUSTED_GOAL_POLICY_CONFLICT,
            _present(evaluation, untrusted_ids),
        )

    system_two_ids = ("voice.recovery.system_two_needed", "computer_use.recovery_needed")
    system_two = _reading(evaluation, "voice.recovery.system_two_needed")
    computer_use_recovery = _reading(evaluation, "computer_use.recovery_needed")
    if _high(system_two) or _high(computer_use_recovery):
        return ProposedRoute(
            ROUTE_ESCALATE_SYSTEM_TWO,
            RULE_RECOVERY_SYSTEM_TWO,
            _present(evaluation, system_two_ids),
        )

    side_effect = _reading(evaluation, "voice.recovery.side_effect_maybe_happened")
    replay_safe = _reading(evaluation, "voice.recovery.replay_safe")
    if _high(side_effect) and (replay_safe is None or _low(replay_safe)):
        return ProposedRoute(
            ROUTE_ESCALATE_SYSTEM_TWO,
            RULE_RECOVERY_SIDE_EFFECT_UNSAFE_REPLAY,
            _present(
                evaluation,
                ("voice.recovery.side_effect_maybe_happened", "voice.recovery.replay_safe"),
            ),
        )

    loop_ids = (
        "voice.loop.same_root_cause",
        "voice.loop.same_strategy",
        "voice.loop.new_information",
        "voice.loop.measurable_progress",
        "voice.loop.repeated_ineffective_redirect",
    )
    same_strategy = _reading(evaluation, "voice.loop.same_strategy")
    new_information = _reading(evaluation, "voice.loop.new_information")
    progress = _reading(evaluation, "voice.loop.measurable_progress")
    if _high(same_strategy) and _low(new_information) and (progress is None or _low(progress)):
        return ProposedRoute(
            ROUTE_PAUSE_AND_INVESTIGATE, RULE_LOOP_WITHOUT_PROGRESS, _present(evaluation, loop_ids)
        )

    completion_ids = (
        "voice.completion.claims_done",
        "voice.completion.evidence_current",
        "voice.completion.evidence_matches_revision",
        "voice.completion.independent_verification_required",
    )
    claims_done = _choice_of(evaluation, "voice.completion.claims_done")
    evidence_current = _reading(evaluation, "voice.completion.evidence_current")
    matches_revision = _reading(evaluation, "voice.completion.evidence_matches_revision")
    if claims_done == "yes" and (_low(evidence_current) or _low(matches_revision)):
        return ProposedRoute(
            ROUTE_REQUEST_VERIFICATION,
            RULE_COMPLETION_CLAIM_WITHOUT_EVIDENCE,
            _present(evaluation, completion_ids),
        )
    independent = _reading(evaluation, "voice.completion.independent_verification_required")
    if _high(independent):
        return ProposedRoute(
            ROUTE_REQUEST_VERIFICATION,
            RULE_COMPLETION_INDEPENDENT_VERIFICATION,
            _present(evaluation, completion_ids),
        )

    drift_ids = (
        "voice.drift.supports_goal",
        "voice.drift.scope_expansion",
        "voice.drift.non_goal_conflict",
    )
    non_goal_conflict = _reading(evaluation, "voice.drift.non_goal_conflict")
    if _high(non_goal_conflict):
        return ProposedRoute(
            ROUTE_REQUEST_USER_INPUT, RULE_DRIFT_NON_GOAL_CONFLICT, _present(evaluation, drift_ids)
        )
    scope_expansion = _reading(evaluation, "voice.drift.scope_expansion")
    if _high(scope_expansion):
        return ProposedRoute(
            ROUTE_INVESTIGATE, RULE_DRIFT_SCOPE_EXPANSION, _present(evaluation, drift_ids)
        )
    supports_goal = _reading(evaluation, "voice.drift.supports_goal")
    if _low(supports_goal):
        return ProposedRoute(
            ROUTE_INVESTIGATE, RULE_DRIFT_GOAL_SUPPORT_MISSING, _present(evaluation, drift_ids)
        )

    research_ids = (
        "voice.research.external_fact_missing",
        "voice.research.version_sensitive",
        "voice.research.existing_evidence_sufficient",
    )
    missing_fact = _reading(evaluation, "voice.research.external_fact_missing")
    version_sensitive = _choice_of(evaluation, "voice.research.version_sensitive")
    if _high(missing_fact) and version_sensitive == "yes":
        return ProposedRoute(
            ROUTE_INVESTIGATE, RULE_RESEARCH_VERSION_SENSITIVE, _present(evaluation, research_ids)
        )
    if _high(missing_fact):
        return ProposedRoute(
            ROUTE_HINT, RULE_RESEARCH_MISSING_FACT, _present(evaluation, research_ids)
        )

    state_unclear = _reading(evaluation, "voice.recovery.state_unclear")
    if _high(state_unclear):
        return ProposedRoute(
            ROUTE_INVESTIGATE,
            RULE_RECOVERY_UNKNOWN_STATE,
            _present(evaluation, ("voice.recovery.state_unclear", "voice.recovery.replay_safe")),
        )

    return ProposedRoute(ROUTE_OBSERVE, RULE_LADDER_EXHAUSTED, ())


# --------------------------------------------------------------------------- #
# §45/JEV-10 — shadow harness                                                   #
# --------------------------------------------------------------------------- #

#: Fields a shadow record may never carry: they would make the record a handle
#: on the run (goal change, pause, tool call, completion verdict) — §45.
SHADOW_FORBIDDEN_KEYS: tuple[str, ...] = (
    "action",
    "execute",
    "goal_change",
    "pause",
    "block_completion",
    "release_completion",
)


def assert_shadow_advisory(record: dict) -> dict:
    """Assert the §45 invariants of a shadow record — and fail loudly.

    Raises :class:`SystemOneContractError` when a record claims a mutation, a
    selected route, or carries an executable action. Returns the record, so a
    caller can use the assertion as a guard instead of a comment.
    """
    mutations = record.get("mutations", -1)
    if int(mutations) != 0:
        raise SystemOneContractError(
            f"shadow record reports {mutations!r} mutations — shadow may not mutate (§45)"
        )
    if record.get("route") is not None:
        raise SystemOneContractError(
            "shadow record carries a route — shadow may not select one (§45)"
        )
    if record.get("selected_route") is not None:
        raise SystemOneContractError(
            "shadow record claims a selected route — none was selected (§45)"
        )
    executable = sorted(key for key in SHADOW_FORBIDDEN_KEYS if key in record)
    if executable:
        raise SystemOneContractError(
            f"shadow record carries executable field(s) {executable} — not a deliverable (§45)"
        )
    return record


def _reported_cost(usage) -> float | None:
    """The cost a provider really reported — ``None`` when it reported none."""
    try:
        value = dict(usage or {}).get("cost_usd")
    except Exception:  # noqa: BLE001 — an unreadable usage is no measurement
        return None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ShadowHarness:
    """Run System One beside the live path and record — never mutate (§45).

    The record states ``mutations=0`` and ``route``/``selected_route``/``policy_rule``
    as ``None``: the harness has no way to change a goal, pause Voice, start a
    tool call, or release/block completion. It compares against the baseline
    route and stores what the contract asks for (§88/JEV-10): disagreement,
    latency, usage, and the outcome the run really produced (attached later via
    :meth:`observe_outcome`).

    Two guards make the "no mutation" claim checkable instead of asserted in a
    comment: an optional ``state_probe`` is compared before and after the run,
    and :func:`assert_shadow_advisory` validates the record itself.
    """

    def __init__(
        self,
        evaluator,
        *,
        emit: Callable[[str, dict], None] | None = None,
        state_probe: Callable[[], object] | None = None,
    ) -> None:
        self.evaluator = evaluator
        self.emit = emit or (lambda kind, payload: None)
        self.state_probe = state_probe
        self.records: list[dict] = []
        self.violations: list[dict] = []
        self.outcomes: list[dict] = []
        self.last_evaluation: SystemOneEvaluation | None = None

    # -- the two facts that may never change -------------------------------- #

    @property
    def mutations(self) -> int:
        """Always ``0``: shadow mode holds no mutating handle (§45)."""
        return 0

    @property
    def executed_routes(self) -> tuple[str, ...]:
        """Always empty: a shadow record is never executed (§45)."""
        return ()

    async def run(
        self,
        *,
        state,
        questions,
        context,
        baseline_route: str = "",
        baseline_hint: dict | None = None,
        live: VersionStamp | None = None,
        trace_id: str = "",
    ) -> dict:
        """Evaluate in parallel, compare with the baseline, store the record.

        ``live`` is the optional :class:`VersionStamp` of the running goal: a
        late answer is recorded as ``stale``/``inert`` and still cannot change
        the run — the route stays ``None`` (§92/E2E-13).
        """
        before = None if self.state_probe is None else self.state_probe()
        evaluation = await self.evaluator.evaluate(
            state=state, questions=questions, context=context
        )
        after = None if self.state_probe is None else self.state_probe()

        proposed = propose_route(evaluation)
        stale = False
        stale_note = ""
        if live is not None and is_stale(evaluation, live=live):
            stale = True
            stale_note = stale_reason(evaluation, live=live)
        record = {
            "mode": "shadow",
            "mutations": 0,
            "route": None,
            "selected_route": None,
            "policy_rule": None,  # §49: no rule consumed the answer
            # §18: the trace identity plus the two sides of the comparison.
            # ``cost_if_known`` stays None unless the provider reported a cost —
            # a shadow record never invents a number (§5.1).
            "trace_id": str(trace_id or uuid.uuid4().hex),
            "baseline_route": baseline_route,
            "baseline_hint": dict(baseline_hint or {}),
            "current_system_result": {
                "route": baseline_route,
                "hint": dict(baseline_hint or {}),
            },
            "later_ground_truth": "",
            "cost_if_known": _reported_cost(evaluation.usage),
            "proposed_route": proposed.route,
            "proposed_rule": proposed.rule,
            "proposed_basis": list(proposed.basis),
            "disagreement": bool(baseline_route) and proposed.route != baseline_route,
            "stale": stale,
            "stale_checked": live is not None,
            "stale_reason": stale_note,
            # §45/E2E-13: a shadow record is inert by construction — it is
            # evidence, never a handle on the run.
            "inert": True,
            "context_shadow": bool(getattr(context, "shadow", False)),
            # §49: mode="shadow" labels the reading; the evidence's "route" field
            # carries the *proposal* it read. The selected route of the record
            # itself stays None — nothing was selected.
            "evaluation": evaluation.evidence(mode="shadow", route=proposed.route or ""),
            "latency_ms": evaluation.latency_ms,
            "usage": dict(evaluation.usage),
            "cost": dict(evaluation.usage).get("cost_usd"),
            "outcome": "",
            "outcome_at": None,
            "outcome_details": {},
            "recorded_at": time.time(),
        }


        if before is not None and after != before:
            violation = {
                "state": "mutation_detected",
                "mode": "shadow",
                "reason": "the shadow run changed the probed run state (§45)",
                "evaluation_id": evaluation.evaluation_id,
                "baseline_route": baseline_route,
                "proposed_route": proposed.route,
                "proposed_rule": proposed.rule,
            }
            self.violations.append(violation)
            self.emit("system_one_error", violation)
            raise SystemOneContractError(violation["reason"])

        assert_shadow_advisory(record)
        self.last_evaluation = evaluation
        self.records.append(record)
        self.emit("system_one_shadow", record)
        return record

    def observe_outcome(
        self,
        *,
        evaluation_id: str,
        outcome: str,
        details: dict | None = None,
        observed_at: float | None = None,
    ) -> dict:
        """Store the outcome the run really produced next to its shadow record.

        Storing a finished run's outcome is still observation: the record stays
        evidence and never becomes a handle to act on.
        """
        if not str(outcome or "").strip():
            raise SystemOneContractError("an observed outcome needs a value")
        entry = {
            "evaluation_id": str(evaluation_id),
            "outcome": str(outcome),
            "details": dict(details or {}),
            "observed_at": float(time.time() if observed_at is None else observed_at),
        }
        for record in self.records:
            if record["evaluation"]["evaluation_id"] == entry["evaluation_id"]:
                record["outcome"] = entry["outcome"]
                record["outcome_at"] = entry["observed_at"]
                record["outcome_details"] = entry["details"]
                # §18: what the run really did is the ground truth the shadow
                # reading is compared against — recorded, never acted on.
                record["later_ground_truth"] = entry["outcome"]
                self.outcomes.append(entry)
                return record
        raise SystemOneContractError(f"no shadow record for evaluation {evaluation_id!r}")

    def summary(self) -> dict:
        """The measured shadow numbers — counts and sums, nothing estimated."""
        latencies = [int(record["latency_ms"]) for record in self.records]
        disagreements = sum(1 for record in self.records if record["disagreement"])
        return {
            "mode": "shadow",
            "records": len(self.records),
            "disagreements": disagreements,
            "disagreement_rate": (
                round(disagreements / len(self.records), 4) if self.records else 0.0
            ),
            "outcomes_observed": sum(1 for record in self.records if record.get("outcome")),
            "latency_ms_total": sum(latencies),
            "latency_ms_max": max(latencies) if latencies else 0,
            "usage_available": sum(1 for record in self.records if record.get("usage")),
            "mutations": self.mutations,
            "executed_routes": len(self.executed_routes),
            "violations": len(self.violations),
        }


# --------------------------------------------------------------------------- #
# §91/JEV-13 — completion precheck (advisory; never a certificate)              #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EvaluationPrecheckSignals:
    """Evaluation-derived signals about completion evidence — never a verdict.

    This is the *evaluation* reading (Noul/Choice answers of the completion
    pack). The §91 entry point a caller should use is
    :func:`skaldir.completion.completion_precheck`, which reads the runner's
    structural evidence and is binding-aware. Two public functions with the same
    name and different ``None`` semantics were a trap: one had to lose the name,
    and the name belongs to the entry point the completion gate consumes.
    """

    signals: tuple[str, ...]
    certifies: bool = False
    provider: str = ""
    live_jev: bool = False


def evaluation_precheck_signals(
    *,
    flags,
    evaluation: SystemOneEvaluation | None,
    live: VersionStamp,
    completion_state: dict | None = None,
) -> EvaluationPrecheckSignals | None:
    """Advisory precheck; ``None`` means "keep the existing completion path".

    With the flag off, with no evaluation, or with a stale evaluation this
    returns ``None`` so the caller's own gate stays authoritative (§91/JEV-20).
    """
    if flags is None or not getattr(flags, "enabled", False):
        return None
    if not getattr(flags, "completion_precheck", False):
        return None
    if evaluation is None or is_stale(evaluation, live=live):
        return None
    signals: list[str] = []
    claims = evaluation.answer("completion.claims_done")
    if claims is not None and getattr(claims, "choice", "") == "yes":
        signals.append("agent_claims_completion")
    current = evaluation.noul("completion.evidence_current")
    if current is not None and current < 0.5:
        signals.append("completion_evidence_may_be_missing")
    revision = evaluation.noul("completion.evidence_matches_revision")
    if revision is not None and revision < 0.5:
        signals.append("completion_evidence_may_be_stale")
    independent = evaluation.noul("completion.independent_verification_required")
    if independent is not None and independent >= 0.5:
        signals.append("independent_verification_required")
    if not signals:
        signals.append("no_advisory_signal")
    return EvaluationPrecheckSignals(
        signals=tuple(signals),
        certifies=False,  # System One never certifies completion (§91)
        provider=evaluation.provider,
        live_jev=bool(evaluation.live_jev),
    )


__all__ = [
    "AppliedDecision",
    "AuthorizationDecision",
    "EvaluationPrecheckSignals",
    "ProposedRoute",
    "R0",
    "R1",
    "R2",
    "R3",
    "R4",
    "READ_THRESHOLD",
    "RISK_POLICY",
    "ROUTE_ESCALATE_SYSTEM_TWO",
    "ROUTE_HINT",
    "ROUTE_INVESTIGATE",
    "ROUTE_LADDER",
    "ROUTE_OBSERVE",
    "ROUTE_PAUSE_AND_INVESTIGATE",
    "ROUTE_REQUEST_USER_INPUT",
    "ROUTE_REQUEST_VERIFICATION",
    "RetryDecision",
    "RiskPolicy",
    "SHADOW_FORBIDDEN_KEYS",
    "ShadowHarness",
    "SystemOneGate",
    "ThresholdProfile",
    "VersionStamp",
    "assert_shadow_advisory",
    "authorize",
    "backoff_delay",
    "calibrate_thresholds",
    "classify_status",
    "evaluation_precheck_signals",
    "is_stale",
    "plan_retry",
    "propose_route",
    "risk_policy",
    "stale_reason",
    "total_attempts",
]
