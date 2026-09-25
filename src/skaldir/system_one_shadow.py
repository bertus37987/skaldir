"""JEV-12 — the Voice triage pack evaluated against productive counter-examples.

ABNAHME §90 asks for the pack to be *bewertet* across Loop, Fortschritt
(progress), Drift, Completion, Research and Recovery **with productive
counter-examples** — the real trigger shapes that occur in production, not only
the happy path of the eight mapped triggers. §33 names the same field: a real
semantic loop, productive debugging, goal drift, *necessary supporting work
without drift*, a stub/fake completion, a research question, a crash/resume —
and a false Voice suspicion.

This module holds that evaluation. It deliberately does not re-implement
anything:

* the question sets come from :func:`skaldir.system_one_questions.compose_voice_triage`
  (real registry, real composition, §39/§47);
* the answers cross the real answer contract (``parse_answer`` — a Noul with an
  invented ``confidence`` is still rejected, §38/JEV-02);
* the deterministic baseline is the real :class:`skaldir.voice.Voice` hint;
* the comparison is driven through the **existing**
  :class:`~skaldir.system_one_policy.ShadowHarness`
  (§45/JEV-10), so every case is also a real shadow record: ``mutations=0``,
  ``route=None``, disagreement/latency/usage/outcome stored;
* the proposed route comes from :func:`skaldir.system_one_policy.propose_route`,
  the same function the shadow path uses — there is no second reading.

Evidence class of everything below: **mock** for the provider (``FixtureEvaluator``
is a stub), **integration** for the path itself (real registry, real harness,
real answer contract, real deterministic Voice, real flags) — and never *live*:
no authorized Jev/TypeSafe call is involved, and every evaluation carries
``live_jev=False`` (§41).

Honest limit that is part of the result: the conservative ``*`` trigger mapping
of :data:`skaldir.system_one_questions.TRIGGER_GROUPS` composes no research
question for real trigger kinds that need one. :func:`trigger_coverage` reports
that per trigger instead of hiding it; see docs/audit/SYSTEM_ONE_TRIAGE_PACK.md.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .monitor import Signal
from .system_one import (
    PROVIDER_LLM_ADAPTER,
    EvaluationContext,
    SystemOneContractError,
    SystemOneState,
    build_evaluation,
    parse_answer,
)
from .system_one_flags import SystemOneFlags
from .system_one_policy import (
    ROUTE_ESCALATE_SYSTEM_TWO,
    ROUTE_INVESTIGATE,
    ROUTE_LADDER,
    ROUTE_OBSERVE,
    ROUTE_PAUSE_AND_INVESTIGATE,
    ROUTE_REQUEST_USER_INPUT,
    ROUTE_REQUEST_VERIFICATION,
    ShadowHarness,
)
from .system_one_questions import compose_voice_triage, default_question_registry
from .system_one_sanitizer import SanitizerPolicy, StateSanitizer
from .voice import Voice, triage_with_shadow

# --------------------------------------------------------------------------- #
# outcome vocabulary — the ground truth a reading is measured against          #
# --------------------------------------------------------------------------- #

#: The run really needed no intervention: this is the productive counter-example.
OUTCOME_NO_INTERVENTION = "no_intervention_needed"
#: The run really needed an intervention (pause, investigation, verification,
#: escalation) — the pack must not stay silent.
OUTCOME_INTERVENTION = "intervention_needed"

#: Routes that keep working: a reading that proposes one of these spent no
#: intervention on the run.
NON_INTERVENING_ROUTES: frozenset[str] = frozenset({ROUTE_OBSERVE, "hint"})

#: Routes that do ask for something — from an investigation up to the §48
#: System-Two request (System One asks; it never acts, §36).
INTERVENING_ROUTES: frozenset[str] = frozenset(
    {
        ROUTE_INVESTIGATE,
        ROUTE_PAUSE_AND_INVESTIGATE,
        ROUTE_REQUEST_VERIFICATION,
        ROUTE_REQUEST_USER_INPUT,
        ROUTE_ESCALATE_SYSTEM_TWO,
    }
)

#: Routes a *productive counter-example* must never reach — an over-reaction.
HARD_INTERVENTION_ROUTES: frozenset[str] = frozenset(
    {ROUTE_PAUSE_AND_INVESTIGATE, ROUTE_ESCALATE_SYSTEM_TWO}
)


def outcome_aligned(*, route: str, outcome: str) -> bool:
    """Does a proposed route fit the outcome the run really had? (§90)

    ``observe``/``hint`` fit an outcome without intervention; every other rung
    fits an outcome that needed one. A route outside both sets is a contract
    error, not a silent ``False``.
    """
    if outcome == OUTCOME_NO_INTERVENTION:
        return route in NON_INTERVENING_ROUTES
    if outcome == OUTCOME_INTERVENTION:
        return route in INTERVENING_ROUTES
    raise SystemOneContractError(f"unknown productive outcome: {outcome!r}")


# --------------------------------------------------------------------------- #
# the real trigger kinds of this runtime (§47/§90)                             #
# --------------------------------------------------------------------------- #

#: Every trigger kind the monitor really emits and the pause policy really
#: knows: ``pause.TRIGGER_RULES`` (escalating kinds) plus the monitor's signal
#: kinds that carry a real production shape without a pause rule of their own.
PRODUCTION_TRIGGER_KINDS: tuple[str, ...] = (
    # pause.TRIGGER_RULES — the kinds that may open a pause
    "loop",
    "error_burst",
    "scope_drift",
    "known_incident_repeat",
    "strategy_repeat",
    "hallucinated_api",
    "capability_gap",
    "wrong_tool_call",
    "pattern_no_new_evidence",
    "pattern_retry_without_strategy_change",
    "pattern_dependency_churn",
    "pattern_oscillation",
    # monitor signal kinds with a real production shape (no pause rule)
    "stall",
    "revert",
    "error_recurrence_after_progress",
    "error_cluster",
    "diff_churn",
    "dependency_change",
    "outage_misdiagnosis",
    "slop_stub",
    "slop_todo_placeholder",
)

#: Which §47 dimensions a production shape *needs* — a judgement, written down
#: so it can be checked. Kinds whose need is not unambiguous are deliberately
#: absent rather than guessed.
REQUIRED_GROUPS_BY_TRIGGER: dict[str, tuple[str, ...]] = {
    "loop": ("loop",),
    "error_burst": ("loop", "research", "recovery"),
    "scope_drift": ("drift",),
    "known_incident_repeat": ("research", "recovery"),
    "strategy_repeat": ("loop", "recovery"),
    # an invented API is a version-sensitive external fact (§34)
    "hallucinated_api": ("research",),
    "capability_gap": ("research", "recovery"),
    "wrong_tool_call": ("loop", "drift"),
    "pattern_no_new_evidence": ("loop",),
    "pattern_retry_without_strategy_change": ("loop",),
    "pattern_oscillation": ("loop",),
    # dependency churn is a version question, not a loop question
    "pattern_dependency_churn": ("research",),
    "stall": ("loop", "recovery"),
    "revert": ("recovery",),
    "error_recurrence_after_progress": ("loop",),
    "error_cluster": ("loop",),
    "diff_churn": ("loop",),
    "dependency_change": ("research",),
    "outage_misdiagnosis": ("research",),
    "slop_stub": ("completion",),
    "slop_todo_placeholder": ("completion",),
}


@dataclass(frozen=True)
class CoverageGap:
    """A real trigger shape whose composed pack cannot ask a needed dimension."""

    trigger_kind: str
    required: tuple[str, ...]
    composed: tuple[str, ...]
    missing: tuple[str, ...]


def trigger_coverage(*, registry=None) -> tuple[CoverageGap, ...]:
    """Compare what each real trigger needs with what the pack composes (§90).

    Reported, never repaired here: the trigger→group mapping belongs to
    :mod:`skaldir.system_one_questions` (one source of truth, §39).
    """
    registry = registry or default_question_registry()
    gaps: list[CoverageGap] = []
    for trigger_kind, required in REQUIRED_GROUPS_BY_TRIGGER.items():
        composed = compose_voice_triage(trigger_kind, registry=registry).groups
        missing = tuple(group for group in required if group not in composed)
        if missing:
            gaps.append(
                CoverageGap(
                    trigger_kind=trigger_kind,
                    required=tuple(required),
                    composed=tuple(composed),
                    missing=missing,
                )
            )
    return tuple(gaps)


# --------------------------------------------------------------------------- #
# the corpus — one productive production shape per case                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TriageCase:
    """One production trigger shape with its reading and its ground truth.

    ``answers`` are provider-shaped payloads: they are translated by the same
    ``parse_answer`` contract the live adapters use, so the corpus cannot smuggle
    a field (an invented ``confidence`` on a Noul is rejected, §38).

    ``counter_example`` marks the *productive* counter-examples of §33: shapes in
    which an eager reading would intervene although the work is healthy.
    ``expected_outcome`` is the productive ground truth of the shape — did this
    run really need an intervention? It is a documented judgement, not a
    measurement from a live run (see the module docstring's honest limit).
    """

    case_id: str
    dimension: str
    trigger_kind: str
    shape: str
    answers: dict
    expected_route: str
    expected_rule: str
    expected_outcome: str
    counter_example: bool = False
    required_groups: tuple[str, ...] = ()
    severity: int = 2
    message: str = ""
    goal: str = "fix the failing test"
    non_goals: tuple[str, ...] = ()
    step: int = 10


def _noul(value: float) -> dict:
    return {"value": float(value)}


def _fill(primary: str, confidence: float, others: dict) -> tuple[dict[str, float], float]:
    """A distribution that sums to 1: the named option carries ``confidence``.

    The remaining probability is shared over the other options proportionally to
    the weights the case gives — the §38 rule (sum to 1 within tolerance) is the
    caller's contract, not something the fixture may silently violate.
    """
    confidence = float(confidence)
    if not others:
        return {primary: 1.0}, confidence
    rest = max(0.0, 1.0 - confidence)
    total = sum(float(value) for value in others.values())
    probabilities = {primary: confidence}
    for key, value in others.items():
        probabilities[key] = rest * (float(value) / total) if total else 0.0
    return probabilities, confidence


def _choice(choice: str, confidence: float, **others: float) -> dict:
    probabilities, confidence = _fill(choice, confidence, dict(others))
    return {"choice": choice, "probabilities": probabilities, "confidence": confidence}


def _score(score: float, legend: tuple[float, ...], confidence: float) -> dict:
    others = {str(value): 1.0 for value in legend if value != score}
    probabilities, confidence = _fill(str(score), confidence, others)
    return {
        "score": float(score),
        "legend": list(legend),
        "probabilities": probabilities,
        "confidence": confidence,
    }


PROGRESS_LEGEND = (0.0, 1.0, 2.0, 3.0)

PRODUCTION_CASES: tuple[TriageCase, ...] = (
    # -- Loop --------------------------------------------------------------- #
    TriageCase(
        case_id="loop_same_strategy_no_new_information",
        dimension="loop",
        trigger_kind="loop",
        shape=(
            "the same command fails a third time with the same root cause, the same strategy "
            "is retried and a previous redirect changed nothing"
        ),
        answers={
            "voice.loop.same_root_cause": _choice("same", 0.9, different=0.1),
            "voice.loop.same_strategy": _noul(0.9),
            "voice.loop.new_information": _noul(0.05),
            "voice.loop.measurable_progress": _score(0.0, PROGRESS_LEGEND, 0.9),
            "voice.loop.repeated_ineffective_redirect": _noul(0.7),
        },
        expected_route=ROUTE_PAUSE_AND_INVESTIGATE,
        expected_rule="loop_no_progress_without_new_information",
        expected_outcome=OUTCOME_INTERVENTION,
        required_groups=("loop",),
        message="same call repeated",
    ),
    TriageCase(
        case_id="loop_productive_debugging",
        dimension="loop",
        trigger_kind="loop",
        shape=(
            "the loop signal fires, but the agent is really debugging: distinct error "
            "signatures, new evidence, a localised fix and a verification run"
        ),
        answers={
            "voice.loop.same_root_cause": _choice("different", 0.8, same=0.1, unknown=0.1),
            "voice.loop.same_strategy": _noul(0.2),
            "voice.loop.new_information": _noul(0.9),
            "voice.loop.measurable_progress": _score(3.0, PROGRESS_LEGEND, 0.9),
            "voice.loop.repeated_ineffective_redirect": _noul(0.1),
        },
        expected_route=ROUTE_OBSERVE,
        expected_rule="no_rule_matched",
        expected_outcome=OUTCOME_NO_INTERVENTION,
        counter_example=True,
        required_groups=("loop",),
        severity=2,
        message="third tool result in a row",
    ),
    # -- Fortschritt / progress --------------------------------------------- #
    TriageCase(
        case_id="progress_real_but_slow",
        dimension="progress",
        trigger_kind="diff_churn",
        shape=(
            "the diff grows without an obvious milestone while the agent keeps making "
            "measurable progress and new information keeps arriving"
        ),
        answers={
            "voice.loop.same_strategy": _noul(0.7),
            "voice.loop.new_information": _noul(0.6),
            "voice.loop.measurable_progress": _score(2.0, PROGRESS_LEGEND, 0.8),
            "voice.loop.repeated_ineffective_redirect": _noul(0.1),
        },
        expected_route=ROUTE_OBSERVE,
        expected_rule="no_rule_matched",
        expected_outcome=OUTCOME_NO_INTERVENTION,
        counter_example=True,
        required_groups=("loop",),
        message="diff churn without a milestone",
    ),
    TriageCase(
        case_id="progress_stalled_without_new_information",
        dimension="progress",
        trigger_kind="pattern_no_new_evidence",
        shape=(
            "steps keep happening but no new evidence arrives and the measured progress "
            "stays at zero while the same strategy is retried"
        ),
        answers={
            "voice.loop.same_root_cause": _choice("same", 0.85, different=0.15),
            "voice.loop.same_strategy": _noul(0.85),
            "voice.loop.new_information": _noul(0.1),
            "voice.loop.measurable_progress": _score(0.0, PROGRESS_LEGEND, 0.85),
            "voice.loop.repeated_ineffective_redirect": _noul(0.8),
        },
        expected_route=ROUTE_PAUSE_AND_INVESTIGATE,
        expected_rule="loop_no_progress_without_new_information",
        expected_outcome=OUTCOME_INTERVENTION,
        required_groups=("loop",),
        message="no new evidence in the last steps",
    ),
    # -- Drift -------------------------------------------------------------- #
    TriageCase(
        case_id="drift_necessary_supporting_work",
        dimension="drift",
        trigger_kind="scope_drift",
        shape=(
            "a file outside the pact is touched because the goal cannot be reached "
            "otherwise — the work supports the goal and expands no scope"
        ),
        answers={
            "voice.drift.supports_goal": _noul(0.9),
            "voice.drift.scope_expansion": _noul(0.1),
            "voice.drift.non_goal_conflict": _noul(0.05),
        },
        expected_route=ROUTE_OBSERVE,
        expected_rule="no_rule_matched",
        expected_outcome=OUTCOME_NO_INTERVENTION,
        counter_example=True,
        required_groups=("drift",),
        severity=3,
        message="edit outside the agreed path",
    ),
    TriageCase(
        case_id="drift_non_goal_conflict",
        dimension="drift",
        trigger_kind="scope_drift",
        shape=(
            "the work expands the product scope and contradicts a declared non-goal the "
            "user owns"
        ),
        answers={
            "voice.drift.supports_goal": _noul(0.35),
            "voice.drift.scope_expansion": _noul(0.8),
            "voice.drift.non_goal_conflict": _noul(0.85),
        },
        expected_route=ROUTE_REQUEST_USER_INPUT,
        expected_rule="drift_declared_non_goal_conflict",
        expected_outcome=OUTCOME_INTERVENTION,
        required_groups=("drift",),
        severity=3,
        message="new configuration surface added",
    ),
    TriageCase(
        case_id="drift_scope_expansion_only",
        dimension="drift",
        trigger_kind="wrong_tool_call",
        shape=(
            "the planned tool call adds a second feature surface that nobody asked for, "
            "without touching a declared non-goal"
        ),
        answers={
            "voice.drift.supports_goal": _noul(0.6),
            "voice.drift.scope_expansion": _noul(0.85),
            "voice.drift.non_goal_conflict": _noul(0.1),
        },
        expected_route=ROUTE_INVESTIGATE,
        expected_rule="drift_scope_expansion",
        expected_outcome=OUTCOME_INTERVENTION,
        required_groups=("drift",),
        message="tool call planned outside the agreed scope",
    ),
    # -- Completion --------------------------------------------------------- #
    TriageCase(
        case_id="completion_stub_fake_done",
        dimension="completion",
        trigger_kind="slop_stub",
        shape=(
            "a NotImplementedError stub lands and the agent claims the task is done "
            "against evidence that does not belong to the current revision"
        ),
        answers={
            "voice.completion.claims_done": _choice("yes", 0.9, no=0.1),
            "voice.completion.evidence_current": _noul(0.2),
            "voice.completion.evidence_matches_revision": _noul(0.3),
            "voice.completion.independent_verification_required": _noul(0.6),
        },
        expected_route=ROUTE_REQUEST_VERIFICATION,
        expected_rule="completion_claim_without_current_evidence",
        expected_outcome=OUTCOME_INTERVENTION,
        required_groups=("completion",),
        severity=3,
        message="stub instead of implementation",
    ),
    TriageCase(
        case_id="completion_claim_with_current_evidence",
        dimension="completion",
        trigger_kind="slop_todo_placeholder",
        shape=(
            "a legitimately deferred TODO remains, but the claimed scope is finished with "
            "current evidence that matches the revision"
        ),
        answers={
            "voice.completion.claims_done": _choice("yes", 0.9, no=0.1),
            "voice.completion.evidence_current": _noul(0.95),
            "voice.completion.evidence_matches_revision": _noul(0.95),
            "voice.completion.independent_verification_required": _noul(0.1),
        },
        expected_route=ROUTE_OBSERVE,
        expected_rule="no_rule_matched",
        expected_outcome=OUTCOME_NO_INTERVENTION,
        counter_example=True,
        required_groups=("completion",),
        message="TODO for a later phase",
    ),
    # -- Research ----------------------------------------------------------- #
    TriageCase(
        case_id="research_version_sensitive_fact_missing",
        dimension="research",
        trigger_kind="error_burst",
        shape=(
            "the same provider error returns and the answer depends on a version-sensitive "
            "external fact (which API version behaves how) that is not in the repo"
        ),
        answers={
            "voice.research.external_fact_missing": _noul(0.85),
            "voice.research.version_sensitive": _choice("yes", 0.9, no=0.1),
            "voice.research.existing_evidence_sufficient": _noul(0.15),
        },
        expected_route=ROUTE_INVESTIGATE,
        expected_rule="research_missing_version_sensitive_fact",
        expected_outcome=OUTCOME_INTERVENTION,
        required_groups=("research", "loop", "recovery"),
        message="provider error repeated",
    ),
    TriageCase(
        case_id="research_missing_non_version_fact",
        dimension="research",
        trigger_kind="capability_gap",
        shape=(
            "an external fact is missing but it is not version-sensitive: a hint is enough, "
            "the run does not need an investigation"
        ),
        answers={
            "voice.research.external_fact_missing": _noul(0.75),
            "voice.research.version_sensitive": _choice("no", 0.8, yes=0.2),
            "voice.research.existing_evidence_sufficient": _noul(0.2),
        },
        expected_route="hint",
        expected_rule="research_missing_fact_hint",
        expected_outcome=OUTCOME_NO_INTERVENTION,
        counter_example=True,
        required_groups=("research", "recovery"),
        message="capability reported missing",
    ),
    TriageCase(
        case_id="research_sufficient_local_evidence",
        dimension="research",
        trigger_kind="known_incident_repeat",
        shape=(
            "a known incident repeats and the repository's own incident memory already "
            "answers the question — the pack must not send a research task"
        ),
        answers={
            "voice.research.external_fact_missing": _noul(0.15),
            "voice.research.version_sensitive": _choice("unknown", 0.6, no=0.3, yes=0.1),
            "voice.research.existing_evidence_sufficient": _noul(0.95),
        },
        expected_route=ROUTE_OBSERVE,
        expected_rule="no_rule_matched",
        expected_outcome=OUTCOME_NO_INTERVENTION,
        counter_example=True,
        required_groups=("research", "recovery"),
        message="known incident repeated",
    ),
    # -- Recovery ----------------------------------------------------------- #
    TriageCase(
        case_id="recovery_system_two_needed",
        dimension="recovery",
        trigger_kind="known_incident_repeat",
        shape=(
            "the recovered cause is genuinely unclear (new app version, contradictory "
            "signals) and the run asks for System Two"
        ),
        answers={
            "voice.recovery.system_two_needed": _noul(0.85),
            "voice.recovery.state_unclear": _noul(0.7),
            "voice.recovery.side_effect_maybe_happened": _noul(0.2),
            "voice.recovery.replay_safe": _noul(0.9),
        },
        expected_route=ROUTE_ESCALATE_SYSTEM_TWO,
        expected_rule="recovery_system_two_needed",
        expected_outcome=OUTCOME_INTERVENTION,
        required_groups=("recovery", "research"),
        message="incident repeated with a new app version",
    ),
    TriageCase(
        case_id="recovery_side_effect_unsafe_replay",
        dimension="recovery",
        trigger_kind="stall",
        shape=(
            "the run stalls in the crash window between side effect and receipt: the side "
            "effect may already have happened and a replay is not safe — while the pack's "
            "own System-Two reading stays low"
        ),
        answers={
            "voice.recovery.side_effect_maybe_happened": _noul(0.9),
            "voice.recovery.replay_safe": _noul(0.1),
            "voice.recovery.system_two_needed": _noul(0.2),
            "voice.recovery.state_unclear": _noul(0.9),
        },
        expected_route=ROUTE_ESCALATE_SYSTEM_TWO,
        expected_rule="recovery_possible_side_effect_unsafe_replay",
        expected_outcome=OUTCOME_INTERVENTION,
        required_groups=("recovery",),
        message="no step completed after the interrupted write",
    ),
    TriageCase(
        case_id="recovery_state_unclear_replay_safe",
        dimension="recovery",
        trigger_kind="stall",
        shape=(
            "the state is unclear after a resume, but no side effect happened and a replay "
            "is safe: clarify, do not escalate the run"
        ),
        answers={
            "voice.recovery.state_unclear": _noul(0.8),
            "voice.recovery.side_effect_maybe_happened": _noul(0.1),
            "voice.recovery.replay_safe": _noul(0.9),
            "voice.recovery.system_two_needed": _noul(0.1),
        },
        expected_route=ROUTE_INVESTIGATE,
        expected_rule="recovery_state_unclear_replay_safe",
        expected_outcome=OUTCOME_INTERVENTION,
        counter_example=True,
        required_groups=("recovery",),
        message="session resumed into an unclear state",
    ),
    # -- the documented coverage gap, measured (not silenced) --------------- #
    TriageCase(
        case_id="hallucinated_api_needs_research",
        dimension="research",
        trigger_kind="hallucinated_api",
        shape=(
            "an invented API is used; the needed reading is a version-sensitive external "
            "fact — but the trigger is unmapped, so the conservative default set asks no "
            "research question at all"
        ),
        answers={
            "voice.loop.same_strategy": _noul(0.5),
            "voice.loop.new_information": _noul(0.9),
            "voice.loop.measurable_progress": _score(2.0, PROGRESS_LEGEND, 0.8),
            "voice.completion.claims_done": _choice("yes", 0.8, no=0.2),
            "voice.completion.evidence_current": _noul(0.9),
            "voice.completion.evidence_matches_revision": _noul(0.9),
        },
        expected_route=ROUTE_OBSERVE,
        expected_rule="no_rule_matched",
        expected_outcome=OUTCOME_INTERVENTION,
        required_groups=("research",),
        message="call to a method that does not exist",
    ),
)


# --------------------------------------------------------------------------- #
# the fixture provider (evidence class: mock)                                   #
# --------------------------------------------------------------------------- #


class FixtureEvaluator:
    """Answers one case from a fixed payload table — an isolated stub.

    It replaces the transport only: the payloads are translated by the real
    ``parse_answer`` contract, so an answer to a question the pack does not ask
    is refused instead of quietly disappearing (``§39/JEV-03``).
    """

    provider = "triage_case_fixture"
    provider_model = "triage_case_fixture_v1"
    provider_access_mode = "fixture"

    def __init__(
        self, answers: Mapping[str, Any], *, usage: Mapping[str, Any] | None = None
    ) -> None:
        self.answers = dict(answers)
        self.usage = dict(usage or {"prompt_chars": 640, "response_chars": 180})
        self.calls = 0

    async def evaluate(self, *, state, questions, context):
        self.calls += 1
        kinds = {question.id: question.kind for question in questions.questions}
        translated = {}
        for question_id, payload in self.answers.items():
            if question_id not in kinds:
                raise SystemOneContractError(
                    f"the composed pack does not ask {question_id!r} for this trigger — "
                    "a case may only answer questions it really gets asked (§39)"
                )
            translated[question_id] = parse_answer(
                question_id=question_id, kind=kinds[question_id], payload=payload
            )
        return build_evaluation(
            context=context,
            questions=questions,
            state=state,
            provider=self.provider,
            provider_model=self.provider_model,
            access_mode=self.provider_access_mode,
            answers=translated,
            usage=dict(self.usage),
            started_at=2000.0,
            completed_at=2000.0 + 0.05,
            live_jev=False,  # a fixture is never a live Jev call (§41)
            shadow=bool(context.shadow),
        )


#: The §46 canary-1 wiring: enabled, adapter provider, shadow only.
SHADOW_TRIAGE_FLAGS = (
    SystemOneFlags.disabled()
    .with_flag("enabled", True)
    .with_flag("provider", PROVIDER_LLM_ADAPTER)
    .with_flag("shadow_mode", True)
)


def pack_evaluation_enabled(flags) -> bool:
    """Does this configuration ask for the pack's shadow evaluation? (§98)

    Exactly the same gates the shadow path itself uses: no flags, flags off, or
    a disabled provider → nothing runs, and the existing behaviour stays.
    """
    if flags is None or not getattr(flags, "enabled", False):
        return False
    if getattr(flags, "provider", "disabled") == "disabled":
        return False
    return bool(getattr(flags, "shadow_mode", False))


# --------------------------------------------------------------------------- #
# evaluation                                                                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CaseResult:
    """What one production shape produced — reading, rule, outcome, record."""

    case_id: str
    dimension: str
    trigger_kind: str
    counter_example: bool
    composed_groups: tuple[str, ...]
    missing_groups: tuple[str, ...]
    baseline_route: str
    baseline_hint_kind: str
    proposed_route: str
    proposed_rule: str
    expected_route: str
    expected_rule: str
    expected_outcome: str
    disagreement: bool
    record: dict
    evaluation: Any

    @property
    def route_matched(self) -> bool:
        return (
            self.proposed_route == self.expected_route
            and self.proposed_rule == self.expected_rule
        )

    @property
    def outcome_aligned(self) -> bool:
        return outcome_aligned(route=self.proposed_route, outcome=self.expected_outcome)

    @property
    def status(self) -> str:
        if self.missing_groups:
            return "coverage_gap"
        return "match" if self.route_matched and self.outcome_aligned else "mismatch"


@dataclass(frozen=True)
class PackReport:
    """The aggregated pack evaluation — counts, gaps and the records behind them."""

    results: tuple[CaseResult, ...]
    gaps: tuple[CoverageGap, ...] = ()

    @property
    def records(self) -> int:
        return len(self.results)

    @property
    def mismatches(self) -> tuple[CaseResult, ...]:
        return tuple(result for result in self.results if result.route_matched is False)

    @property
    def outcome_mismatches(self) -> tuple[CaseResult, ...]:
        """Route/outcome conflicts — coverage gaps are excluded, they are scored separately."""
        return tuple(
            result
            for result in self.results
            if not result.missing_groups and result.outcome_aligned is False
        )

    @property
    def coverage_gaps(self) -> tuple[CoverageGap, ...]:
        return self.gaps

    @property
    def disagreements(self) -> int:
        return sum(1 for result in self.results if result.disagreement)

    @property
    def counter_examples(self) -> int:
        return sum(1 for result in self.results if result.counter_example)

    @property
    def executed_routes(self) -> int:
        """Always 0: every case ran through the shadow harness (§45)."""
        return sum(1 for result in self.results if result.record.get("route") is not None)

    @property
    def dimensions(self) -> tuple[str, ...]:
        return tuple(sorted({result.dimension for result in self.results}))

    @property
    def ok(self) -> bool:
        """Every *asked* case read what was pinned for its production shape."""
        return not self.mismatches and not self.outcome_mismatches

    @property
    def fully_covered(self) -> bool:
        return not self.gaps


def _state_for(case: TriageCase) -> SystemOneState:
    """The sanitized state of one case — the sanitizer of §40 really runs here."""
    policy = SanitizerPolicy(project_id="gods-voice")
    snapshot = {
        "goal": case.goal,
        "non_goals": list(case.non_goals),
        "signals": [case.trigger_kind],
        "step": case.step,
    }
    return StateSanitizer(policy=policy).sanitize(
        snapshot, scope="task", project_id="gods-voice"
    )


def _context_for(case: TriageCase) -> EvaluationContext:
    return EvaluationContext(
        run_id=f"pack-{case.case_id}",
        goal_id="gods-voice",
        goal_revision=1,
        policy_revision=1,
        event_cursor=case.step,
        project_id="gods-voice",
        model="triage_case_fixture_v1",
    )


async def _evaluate_case_async(case: TriageCase, *, registry=None) -> CaseResult:
    registry = registry or default_question_registry()
    questions = compose_voice_triage(case.trigger_kind, registry=registry)
    composed_groups = tuple(sorted(questions.groups))
    missing_groups = tuple(group for group in case.required_groups if group not in composed_groups)

    evaluator = FixtureEvaluator(case.answers)
    harness = ShadowHarness(evaluator)
    signal = Signal(
        kind=case.trigger_kind,
        severity=case.severity,
        fingerprint=f"{case.case_id}:{case.trigger_kind}",
        message=case.message or case.shape,
    )
    outcome = await triage_with_shadow(
        voice=Voice(),
        signals=[signal],
        step=case.step,
        evidence=[case.trigger_kind],
        flags=SHADOW_TRIAGE_FLAGS,
        harness=harness,
        state=_state_for(case),
        questions=questions,
        context=_context_for(case),
    )
    if outcome.path != "shadow" or not harness.records:
        raise SystemOneContractError(
            f"case {case.case_id} did not run through the shadow path: "
            f"{outcome.path}/{outcome.reason}"
        )
    record = harness.records[-1]
    harness.observe_outcome(
        evaluation_id=record["evaluation"]["evaluation_id"],
        outcome=case.expected_outcome,
        details={"case_id": case.case_id, "source": "productive_shape"},
    )
    return CaseResult(
        case_id=case.case_id,
        dimension=case.dimension,
        trigger_kind=case.trigger_kind,
        counter_example=case.counter_example,
        composed_groups=composed_groups,
        missing_groups=missing_groups,
        baseline_route=record["baseline_route"],
        baseline_hint_kind=str(record["baseline_hint"].get("kind", "")),
        proposed_route=record["proposed_route"],
        proposed_rule=record["proposed_rule"],
        expected_route=case.expected_route,
        expected_rule=case.expected_rule,
        expected_outcome=case.expected_outcome,
        disagreement=bool(record["disagreement"]),
        record=record,
        evaluation=harness.last_evaluation,
    )


async def evaluate_cases_async(cases, *, registry=None) -> tuple[CaseResult, ...]:
    return tuple([await _evaluate_case_async(case, registry=registry) for case in cases])


def evaluate_case(case: TriageCase, *, registry=None) -> CaseResult:
    """Sync entry for CLI/tests — one case through the real shadow path."""
    return asyncio.run(_evaluate_case_async(case, registry=registry))


def evaluate_pack(cases=PRODUCTION_CASES, *, registry=None) -> PackReport:
    """Evaluate the whole corpus; the coverage gaps of §90 come with it."""
    results = asyncio.run(evaluate_cases_async(cases, registry=registry))
    return PackReport(results=results, gaps=trigger_coverage(registry=registry))


def evaluate_pack_if_enabled(*, flags, cases=PRODUCTION_CASES, registry=None) -> PackReport | None:
    """Run the evaluation only when the flags ask for it (§98/JEV-20).

    ``None`` means "nothing runs" — the caller keeps its existing behaviour.
    """
    if not pack_evaluation_enabled(flags):
        return None
    return evaluate_pack(cases, registry=registry)


def render_pack_report(report: PackReport) -> str:
    """A readable evidence block for the docs — every number from real records."""
    lines = [
        f"JEV-12 voice triage pack — mode=shadow, cases={report.records}, "
        f"counter_examples={report.counter_examples}, disagreements={report.disagreements}",
        "routes: " + ", ".join(ROUTE_LADDER) + f", {ROUTE_ESCALATE_SYSTEM_TWO} (§32/§48)",
    ]
    for result in report.results:
        lines.append(
            "  {case_id:<44} trigger={trigger:<36} route={route:<22} rule={rule:<44}"
            " outcome={outcome:<22} baseline={baseline:<8}"
            " disagree={disagree} status={status}".format(
                case_id=result.case_id,
                trigger=result.trigger_kind,
                route=result.proposed_route,
                rule=result.proposed_rule,
                outcome=result.expected_outcome,
                baseline=result.baseline_route,
                disagree="yes" if result.disagreement else "no",
                status=result.status,
            )
        )
    lines.append(
        f"  records={report.records} executed_routes={report.executed_routes} "
        "mutations=0 (shadow, §45)"
    )
    lines.append(
        f"  mismatches={len(report.mismatches)}"
        f" outcome_mismatches={len(report.outcome_mismatches)} "
        f"ok={report.ok} fully_covered={report.fully_covered}"
    )
    if report.coverage_gaps:
        lines.append(
            "  COVERAGE GAPS (real trigger kinds whose composed pack cannot ask a needed "
            "dimension — reported, not silently repaired, §90):"
        )
        for gap in report.coverage_gaps:
            lines.append(
                f"    {gap.trigger_kind}: needs {list(gap.required)} but composes "
                f"{list(gap.composed)} → missing {list(gap.missing)}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """``python -m skaldir.system_one_shadow`` — print the pack evaluation."""
    started = time.time()
    report = evaluate_pack()
    print(render_pack_report(report))
    print(f"  wall_time_s={time.time() - started:.2f}")
    return 0 if report.ok else 1


__all__ = [
    "HARD_INTERVENTION_ROUTES",
    "INTERVENING_ROUTES",
    "NON_INTERVENING_ROUTES",
    "OUTCOME_INTERVENTION",
    "OUTCOME_NO_INTERVENTION",
    "PRODUCTION_CASES",
    "PRODUCTION_TRIGGER_KINDS",
    "REQUIRED_GROUPS_BY_TRIGGER",
    "SHADOW_TRIAGE_FLAGS",
    "CaseResult",
    "CoverageGap",
    "FixtureEvaluator",
    "PackReport",
    "TriageCase",
    "evaluate_case",
    "evaluate_cases_async",
    "evaluate_pack",
    "evaluate_pack_if_enabled",
    "main",
    "outcome_aligned",
    "pack_evaluation_enabled",
    "render_pack_report",
    "trigger_coverage",
]


if __name__ == "__main__":  # pragma: no cover — CLI entry
    raise SystemExit(main())
