"""The typed Jev contract: seven requests, seven answers, one envelope (MVP/03 §4.2–§5 + MVP/93).

Every model here is ``frozen=True`` with ``extra="forbid"`` — a request or an
answer that carries an undeclared field is a contract error, never something to
absorb. Choices are ``Literal``s: there is no field in this package that accepts
free-form model output, and there is deliberately **no field** for a goal
verdict, a permission, a security exception or a goal rewrite (the import-time
scan in :mod:`skaldir.jev.rules` refuses one).

The last statement in this module is the layer-1 contract scan: importing this
module *is* the check.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .kinds import DECISION_KINDS, JevKind

# --------------------------------------------------------------------------- #
# the shared vocabulary                                                         #
# --------------------------------------------------------------------------- #

Status = Literal["ok", "refused", "blocked"]
"""``ok`` = advisory exists; ``refused`` = Jev declined on a precondition;
``blocked`` = no provider was available (never a fabricated answer)."""

ProviderMode = Literal[
    "typesafe_jev_live", "llm_system_one_adapter", "replay_fixture", "disabled"
]
"""The four honest provider labels of the substrate §4.2, re-stated."""

RiskClass = Literal["R0", "R1", "R2", "R3", "R4"]
RouteKind = Literal[
    "native_tool", "docker_capability", "gui_subtool", "composite", "computer_use"
]
"""The candidate kinds a routing request may offer."""

Health = Literal["ok", "degraded", "unhealthy", "unknown"]
PermissionState = Literal["allowed", "needs_approval", "denied"]
"""Copied from ``discovery.authorization_gate``; Jev may read it, never change it."""

Stance = Literal["supports", "contradicts", "stale", "neutral"]
Verdict = Literal["inject_now", "defer", "drop"]
GlobalInjection = Literal["inject", "defer", "none"]
RoutingOutcome = Literal["route", "computer_use_fallback", "no_route"]
VoiceOutcome = Literal["wake_voice", "stay_silent", "defer"]
RouteHint = Literal[
    "observe",
    "hint",
    "investigate",
    "pause_and_investigate",
    "request_verification",
    "request_user_input",
    "escalate_system_two",
]
CompactionAction = Literal["keep", "drop_result", "drop_call"]
CompactionReason = Literal["pinned", "kept", "result_dropped", "call_dropped"]
IntentClass = Literal[
    "boundary",
    "stale_state",
    "permission",
    "concurrency",
    "injection",
    "persistence",
    "dependency_failure",
    "regression",
]
Operation = Literal[
    "click",
    "type_text",
    "select",
    "scroll",
    "wait",
    "http_request",
    "cli_run",
    "file_write",
    "permission_probe",
]
OracleKind = Literal["observed_state", "exit_code", "http_status", "file_diff", "denied"]
TriggerKind = Literal[
    "loop",
    "error_burst",
    "strategy_repeat",
    "known_incident_repeat",
    "scope_drift",
    "contradiction",
    "capability_gap",
    "wrong_tool_call",
    "untrusted_content",
    "*",
]

_FROZEN = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- #
# the envelope                                                                  #
# --------------------------------------------------------------------------- #


class JevRequestBase(BaseModel):
    """Identity + the revisions a decision is bound to (MVP/03 §4.2).

    ``caution_baseline`` is the one field this facade adds to the design's
    envelope: without it on the *request*, the monotone-caution rule (§6 R3) has
    nothing to be monotone against. It is an input-only field (refused on any
    answer) and it can only be read by :func:`skaldir.jev.rules.caution_floor_for`.
    """

    model_config = _FROZEN

    run_id: str
    goal_id: str
    goal_revision: int = 0
    kind: JevKind
    state_fingerprint: str = ""
    risk_class: RiskClass = "R0"
    deadline_ms: int = 1500
    max_tokens: int = 0
    caution_baseline: int = Field(default=0, ge=0, le=3)


# --------------------------------------------------------------------------- #
# 1. memory_injection                                                           #
# --------------------------------------------------------------------------- #


class MemoryCandidate(BaseModel):
    """One retrieved memory offered to Jev — already secret-redacted (§40)."""

    model_config = _FROZEN

    memory_id: str
    source: Literal["incident", "note", "skill", "tool", "project_fact", "research"]
    title: str = ""
    digest: str = Field(default="", max_length=400)
    age_days: int = 0
    retrieval_score: float = 0.0
    already_in_context: bool = False
    protected: bool = False
    """Owner-authored: **must** be injected verbatim, never dropped by Jev (R4)."""
    contradicts: tuple[str, ...] = ()


class MemoryInjectionBudget(BaseModel):
    model_config = _FROZEN

    max_inject: int = 3
    max_tokens: int = 800


class JevMemoryInjectionRequest(JevRequestBase):
    kind: Literal["memory_injection"] = "memory_injection"

    goal_text: str = Field(default="", max_length=500)
    next_step_intent: str = ""
    context_digest: str = ""
    candidates: tuple[MemoryCandidate, ...] = ()
    budget: MemoryInjectionBudget = Field(default_factory=MemoryInjectionBudget)


class MemoryVerdict(BaseModel):
    """The per-candidate reading.

    The field is named ``read`` and not ``verdict`` on purpose: MVP/03 §6 puts
    ``verdict`` on the forbidden-name list (R1), and §5.1 also spells this field
    ``verdict`` — the rule wins, so the per-candidate reading is a ``read`` whose
    values are ``inject_now``/``defer``/``drop``. Nothing else changes.
    """

    model_config = _FROZEN

    memory_id: str
    read: Verdict
    relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    already_in_context: bool = False
    stance: Stance = "neutral"
    rank: int = 0


class MemoryInjectionAnswer(BaseModel):
    model_config = _FROZEN

    kind: Literal["memory_injection"] = "memory_injection"

    chosen: GlobalInjection
    injected: tuple[str, ...] = ()
    verdicts: tuple[MemoryVerdict, ...] = ()
    ordering: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> MemoryInjectionAnswer:
        chosen_now = {item.memory_id for item in self.verdicts if item.read == "inject_now"}
        if set(self.injected) != chosen_now:
            raise ValueError(
                "injected must be exactly the ids whose read is inject_now"
            )
        if set(self.ordering) != set(self.injected):
            raise ValueError("ordering must be a permutation of injected")
        if (self.chosen == "inject") != bool(self.injected):
            raise ValueError("chosen 'inject' holds exactly when something is injected")
        ranks = [item.rank for item in self.verdicts if item.rank]
        if ranks and sorted(ranks) != list(range(1, len(ranks) + 1)):
            raise ValueError("verdict ranks must be 1..n without gaps or ties")
        return self


# --------------------------------------------------------------------------- #
# 2. tool_routing                                                               #
# --------------------------------------------------------------------------- #


class RouteCandidate(BaseModel):
    """An offered route. ``permission_state`` is an input copy of the gate's answer."""

    model_config = _FROZEN

    candidate_id: str
    kind: RouteKind
    name: str = ""
    version: str = ""
    source: Literal["registry", "subtool_library", "composite_index", "docker_image"]
    input_types: tuple[str, ...] = ()
    required_params: tuple[str, ...] = ()
    missing_params: tuple[str, ...] = ()
    preconditions_satisfied: bool = True
    health: Health = "unknown"
    permission_state: PermissionState = "allowed"
    risk_class: RiskClass = "R0"
    anchor_available: bool = False
    subtool_of: str = ""
    last_error: str = ""


class RoutingConstraints(BaseModel):
    model_config = _FROZEN

    allowed_kinds: tuple[RouteKind, ...] = (
        "native_tool",
        "docker_capability",
        "gui_subtool",
        "composite",
        "computer_use",
    )
    max_risk_class: RiskClass = "R2"
    require_deterministic: bool = False
    allow_approval_required: bool = True


class RoutingEnvironment(BaseModel):
    model_config = _FROZEN

    url_kind: str = ""
    app_kind: str = ""
    has_display: bool = False
    has_docker: bool = False


class JevToolRoutingRequest(JevRequestBase):
    kind: Literal["tool_routing"] = "tool_routing"

    next_step_intent: str = ""
    candidates: tuple[RouteCandidate, ...] = ()
    constraints: RoutingConstraints = Field(default_factory=RoutingConstraints)
    recent_failures: tuple[str, ...] = ()
    environment: RoutingEnvironment = Field(default_factory=RoutingEnvironment)


class RouteChoice(BaseModel):
    model_config = _FROZEN

    candidate_id: str
    kind: RouteKind
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    subtool_of: str = ""
    anchor_available: bool = False


class RouteAlternative(BaseModel):
    model_config = _FROZEN

    candidate_id: str
    score: float = Field(default=0.0, ge=0.0, le=1.0)


class ToolRoutingAnswer(BaseModel):
    model_config = _FROZEN

    kind: Literal["tool_routing"] = "tool_routing"

    chosen_kind: RoutingOutcome
    route: RouteChoice | None = None
    alternatives: tuple[RouteAlternative, ...] = ()
    requires_approval: bool = False
    """Reported, never granted: it echoes the chosen candidate's input permission state."""
    missing_inputs: tuple[str, ...] = ()
    reason_code: str = ""

    @model_validator(mode="after")
    def _route_matches_outcome(self) -> ToolRoutingAnswer:
        if (self.chosen_kind == "route") != (self.route is not None):
            raise ValueError("chosen_kind 'route' holds exactly when a route is named")
        return self


# --------------------------------------------------------------------------- #
# 3. capability_ranking                                                         #
# --------------------------------------------------------------------------- #


class CapabilityNeed(BaseModel):
    model_config = _FROZEN

    intent: str = ""
    required_input_types: tuple[str, ...] = ()
    expected_effect: str = ""
    tags: tuple[str, ...] = ()


class CapabilityCandidate(BaseModel):
    """An already-retrieved capability (scope/authorization filtering happened before).

    ``authority`` here is the *provenance* trust level of an offered candidate
    (trusted/community/unknown) — not the advisory ``JevDecision.authority``.
    """

    model_config = _FROZEN

    candidate_id: str
    name: str = ""
    summary: str = ""
    tags: tuple[str, ...] = ()
    input_types: tuple[str, ...] = ()
    output_types: tuple[str, ...] = ()
    health: Health = "unknown"
    version: str = ""
    authority: Literal["trusted", "community", "unknown"] = "unknown"
    last_used_days: int = 0
    scope_match: bool = True
    permission_state: PermissionState = "allowed"


class JevCapabilityRankingRequest(JevRequestBase):
    kind: Literal["capability_ranking"] = "capability_ranking"

    need: CapabilityNeed = Field(default_factory=CapabilityNeed)
    candidates: tuple[CapabilityCandidate, ...] = ()
    top_k: int = 5
    min_fit: float = Field(default=0.5, ge=0.0, le=1.0)


class CapabilityRankingEntry(BaseModel):
    model_config = _FROZEN

    candidate_id: str
    fit: float = Field(default=0.0, ge=0.0, le=1.0)
    rank: int = 0
    covers_need: bool = False
    reasons: tuple[str, ...] = ()


class CapabilityRankingAnswer(BaseModel):
    model_config = _FROZEN

    kind: Literal["capability_ranking"] = "capability_ranking"

    ranking: tuple[CapabilityRankingEntry, ...] = ()
    best_fit: str | None = None
    gap: bool = False
    gap_reason: str = ""
    widen_query: str | None = None
    """A deterministic echo of the need's own words — never generated text."""


# --------------------------------------------------------------------------- #
# 4. context_compaction                                                         #
# --------------------------------------------------------------------------- #


class HistoryToolCall(BaseModel):
    model_config = _FROZEN

    id: str
    tool: str = ""
    input: str = ""
    result_chars: int = 0
    is_error: bool = False


class HistoryEntry(BaseModel):
    model_config = _FROZEN

    i: int
    role: Literal["user", "assistant"]
    text: str = ""
    tool_calls: tuple[HistoryToolCall, ...] = ()


class CompactionState(BaseModel):
    model_config = _FROZEN

    context: str = ""
    goal: str = ""
    history: tuple[HistoryEntry, ...] = ()


class CompactOptions(BaseModel):
    """The upstream defaults of fast-jev-compaction (MVP/03 §2.4)."""

    model_config = _FROZEN

    keep_threshold: float = 0.5
    preserve_recent_messages: int = 6
    max_state_tokens: int = 25_000
    max_request_tokens: int = 30_000
    truncate_head_chars: int = 300
    min_reduction_ratio: float = 0.25


class JevCompactionRequest(JevRequestBase):
    kind: Literal["context_compaction"] = "context_compaction"

    state: CompactionState = Field(default_factory=CompactionState)
    options: CompactOptions = Field(default_factory=CompactOptions)
    protected_message_ids: tuple[int, ...] = ()
    messages_digest: str = ""


class CompactionCallDecision(BaseModel):
    """No text field exists: compaction keeps, shortens or removes — it never rewrites."""

    model_config = _FROZEN

    id: str
    tool: str = ""
    keep_call: float = Field(default=0.0, ge=0.0, le=1.0)
    keep_result: float = Field(default=0.0, ge=0.0, le=1.0)
    action: CompactionAction
    reason: CompactionReason
    head_chars_kept: int = 0


class CompactionStats(BaseModel):
    model_config = _FROZEN

    calls: int = 0
    candidates: int = 0
    kept: int = 0
    results_dropped: int = 0
    calls_dropped: int = 0
    pinned: int = 0
    state_tokens: int = 0
    chars_before: int = 0
    chars_after: int = 0
    reduction_ratio: float = 0.0


class CompactionAnswer(BaseModel):
    model_config = _FROZEN

    kind: Literal["context_compaction"] = "context_compaction"

    calls: tuple[CompactionCallDecision, ...] = ()
    stats: CompactionStats = Field(default_factory=CompactionStats)
    fitted_stage: str = ""
    requests: int = 1


# --------------------------------------------------------------------------- #
# 5. voice_triage                                                               #
# --------------------------------------------------------------------------- #


class TrajectoryItem(BaseModel):
    model_config = _FROZEN

    action: str = ""
    kind: str = ""
    page_changed: bool | None = None
    error_class: str = ""
    hash: str = ""


class JevVoiceTriageRequest(JevRequestBase):
    kind: Literal["voice_triage"] = "voice_triage"

    trigger_kind: TriggerKind = "*"
    signals: dict[str, Any] = Field(default_factory=dict)
    step: int = 0
    trajectory_window: tuple[TrajectoryItem, ...] = ()
    deterministic_hint: str = ""
    agent_on_sensible_path: bool = False
    interruptions_so_far: int = 0
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _signals_are_scalar(self) -> JevVoiceTriageRequest:
        for key, value in self.signals.items():
            if not isinstance(key, str):
                raise ValueError("signal keys must be strings")
            if not isinstance(value, (str, bool, int, float)) and value is not None:
                raise ValueError(
                    f"signal {key!r} must be a scalar (str/bool/int/float/None), "
                    f"got {type(value).__name__}"
                )
        return self


class VoiceTriageAnswer(BaseModel):
    model_config = _FROZEN

    kind: Literal["voice_triage"] = "voice_triage"

    chosen: VoiceOutcome
    urgency: int = Field(default=0, ge=0, le=3)
    already_handled: bool = False
    on_sensible_path: bool = False
    reason_code: str = ""
    route_hint: RouteHint = "observe"
    escalate_system_two: bool = False


# --------------------------------------------------------------------------- #
# 6. adversarial_test_planning                                                  #
# --------------------------------------------------------------------------- #


class AdversarialTarget(BaseModel):
    model_config = _FROZEN

    name: str
    build_id: str = ""
    app_version: str = ""
    routes: tuple[str, ...] = ()
    env: Literal["local", "staging"] = "local"


class TestSurface(BaseModel):
    """An observed surface. ``seed_operations`` are the operations it supports."""

    model_config = _FROZEN

    surface_id: str
    name: str = ""
    kind: Literal["ui_flow", "api_endpoint", "cli", "file_io", "permission_boundary"]
    last_covered_at: float = 0.0
    risk: RiskClass = "R0"
    entry_hint: str = ""
    seed_operations: tuple[str, ...] = ()


class AdversarialBudget(BaseModel):
    model_config = _FROZEN

    max_cases: int = 8
    max_wall_s: int = 600
    per_case_timeout_s: int = 60
    allow_network: bool = False
    parallel_slots: int = 4


class CaseOutcome(BaseModel):
    """What a *runner* measured. It records outcomes; Jev never issues one.

    Named ``outcome`` and not ``verdict`` for the same reason as
    :class:`MemoryVerdict.read`: ``verdict`` is a forbidden field name (R1).

    ``intent_class`` is the pair half of the case this row is about (with
    ``surface_id``). It is empty when the caller's ledger was not written per pair.
    Without it a *previous* case is indistinguishable from its neighbours — measured
    (MVP/87): a live planner received three rows that all read
    ``case_id: "adv-001", surface_id: "canary_ui", outcome: "unknown"`` and repeated
    the intent class it had already spent, because nothing in the state said which
    ``(surface, intent_class)`` pair had been used.
    """

    model_config = _FROZEN

    case_id: str
    surface_id: str = ""
    intent_class: str = ""
    outcome: Literal["pass", "fail", "unknown", "skipped"] = "unknown"
    defect_class: str = ""


class JevAdversarialPlanRequest(JevRequestBase):
    kind: Literal["adversarial_test_planning"] = "adversarial_test_planning"

    target: AdversarialTarget | None = None
    surfaces: tuple[TestSurface, ...] = ()
    budget: AdversarialBudget = Field(default_factory=AdversarialBudget)
    previous_results: tuple[CaseOutcome, ...] = ()
    known_defects: tuple[str, ...] = ()
    risk_class_max: RiskClass = "R2"
    remaining_pairs: tuple[str, ...] = ()
    """The ``"<surface_id>/<intent_class>"`` pairs the caller's own catalogue still has an
    uncovered case for — the *admissible* answers, so the question the provider answers
    cannot offer a pair the caller would then have to refuse (MVP/87).

    Empty means "this caller keeps no ledger": the question composer then offers every
    declared pair, exactly as before. The caller that does keep one (the suite behind
    ``--planner jev``) fills it, and a plan then never stops on
    ``pair_already_covered`` — measured before this field existed: three live runs
    planned 1, 2 and 2 of 12 cases, two of them stopping on a pair the model had already
    spent, because the state it was asked about carried no per-pair coverage."""

    @model_validator(mode="after")
    def _pairs_are_shaped(self) -> JevAdversarialPlanRequest:
        for pair in self.remaining_pairs:
            if "/" not in str(pair) or not all(part.strip() for part in str(pair).split("/", 1)):
                raise ValueError(
                    f"remaining_pairs entries are '<surface_id>/<intent_class>': got {pair!r}"
                )
        return self


class Oracle(BaseModel):
    model_config = _FROZEN

    kind: OracleKind = "observed_state"
    expect: str = ""


class TestCasePlan(BaseModel):
    """One planned case. ``target_ref`` resolves to an **offered** surface + entry hint.

    There is no field for a selector, an XPath, screen coordinates, a shell script
    or JavaScript — "model output becomes an executable" is unconstructible
    (MVP/03 §5.6, mirroring the reference implementation's discipline).
    """

    model_config = _FROZEN

    case_id: str
    surface_id: str
    intent: str = ""
    intent_class: IntentClass
    operation: Operation
    target_ref: str
    input_spec: dict[str, str] = Field(default_factory=dict)
    success_oracle: Oracle = Field(default_factory=Oracle)
    oracle_source: Literal["deterministic", "computer_use"] = "deterministic"
    abort_if: tuple[str, ...] = ()
    risk_class: RiskClass = "R0"
    priority: int = Field(default=0, ge=0, le=3)


class AdversarialPlanAnswer(BaseModel):
    model_config = _FROZEN

    kind: Literal["adversarial_test_planning"] = "adversarial_test_planning"

    plan: tuple[TestCasePlan, ...] = ()
    chosen: Literal["plan", "narrow", "refuse"] = "plan"
    stop_reason: str = ""
    batch_size: int = 0
    expected_defect_classes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _plan_matches_choice(self) -> AdversarialPlanAnswer:
        if self.chosen == "plan" and not self.plan:
            raise ValueError("chosen 'plan' needs at least one case")
        if self.chosen == "refuse" and self.plan:
            raise ValueError("a refused plan carries no cases")
        return self


# --------------------------------------------------------------------------- #
# 7. semantic_verification                                                      #
# --------------------------------------------------------------------------- #


#: The three readings of "is the completion claim backed by this brief?".
#:
#: Deliberately *not* a verdict vocabulary: ``supported`` / ``unsupported`` /
#: ``underspecified`` describe the evidence, not the goal. The deterministic
#: VERIFY layer computed the goal verdict before this call and keeps it; the only
#: weight this reading carries is that ``unsupported`` **keeps a PASS from being
#: promoted to a completion** (see ``skaldir.verify_service.semantic_gate``).
VerificationSupport = Literal["supported", "unsupported", "underspecified"]


class BriefFile(BaseModel):
    """One changed file as the brief reports it: path, diffstat, digest."""

    model_config = _FROZEN

    path: str
    diffstat: str = ""
    digest: str = ""


class BriefCommand(BaseModel):
    """One executed command with the exit code that makes it evidence."""

    model_config = _FROZEN

    cmd: str
    exit_code: int | None = None
    duration_ms: int = 0
    #: Where the fact came from (a check kind, a CLI verb…) — provenance, not a verdict.
    source: str = ""


class VerifierBudget(BaseModel):
    """The verifier's **own** envelope, declared on the request.

    Tokens *and* turns, because a semantic gate that can read forever is a second
    full agent. Read-only is enforced by the ``verifier`` role's tool ceiling
    (:mod:`skaldir.subagent_roles`), not by this model.
    """

    model_config = _FROZEN

    max_tokens: int = Field(default=16_000, ge=1)
    max_turns: int = Field(default=4, ge=1)
    max_reads: int = Field(default=12, ge=0)


class JevSemanticVerificationRequest(JevRequestBase):
    """The Evidence Brief, typed — what a verifier is handed instead of a history.

    ``criteria_already_met`` records what the *deterministic* layer already
    verified, so the verifier is asked about the claim and not asked to re-run
    the suite. The brief itself is bounded by the caller
    (``skaldir.subagent_briefs.build_evidence_brief``); this model only refuses
    to carry an unbounded one.
    """

    kind: Literal["semantic_verification"] = "semantic_verification"

    objective: str
    objective_digest: str = ""
    claim: str = ""
    criteria_already_met: tuple[str, ...] = ()
    files: tuple[BriefFile, ...] = ()
    commands: tuple[BriefCommand, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    transcript_tail: tuple[str, ...] = ()
    brief_truncated: bool = False
    baseline_revision: str = ""
    budget: VerifierBudget = Field(default_factory=VerifierBudget)

    @model_validator(mode="after")
    def _brief_is_present_and_bounded(self) -> JevSemanticVerificationRequest:
        if not self.objective.strip():
            raise ValueError("a semantic_verification request needs an objective")
        if not self.claim.strip():
            raise ValueError(
                "a semantic_verification request needs the completion claim it checks "
                "(a brief never invents one and silence is not a claim)"
            )
        if len(self.files) > 100 or len(self.commands) > 100:
            raise ValueError("the brief may carry at most 100 files and 100 commands")
        if len(self.evidence_refs) > 50:
            raise ValueError("the brief may carry at most 50 evidence refs")
        if len(self.transcript_tail) > 50:
            raise ValueError("the transcript tail is bounded to 50 lines")
        if any(len(str(line)) > 2_000 for line in self.transcript_tail):
            raise ValueError("a transcript tail line is bounded to 2000 chars")
        return self


class SemanticVerificationAnswer(BaseModel):
    """What a verifier may say: a reading of the *claim*, plus its gaps.

    There is no field for a goal verdict, a release decision or a permission —
    the same layer-1 scan that guards the other six kinds guards this one, and
    ``assert_decision_authority`` proves the decision carries
    ``authority='advisory'`` and ``certifies=False``. ``unsupported`` must carry
    at least one reason: a refusal with no reason is not a finding.
    """

    model_config = _FROZEN

    kind: Literal["semantic_verification"] = "semantic_verification"

    support: VerificationSupport = "underspecified"
    reasons: tuple[str, ...] = ()
    evidence_gaps: tuple[str, ...] = ()
    checks_run: tuple[str, ...] = ()
    unverifiable: tuple[str, ...] = ()
    provider_choice: str = ""

    @model_validator(mode="after")
    def _a_refusal_carries_a_reason(self) -> SemanticVerificationAnswer:
        if self.support == "unsupported" and not self.reasons:
            raise ValueError(
                "an 'unsupported' reading must name at least one reason — a refusal "
                "without a reason is not a finding"
            )
        if len(self.reasons) > 10 or len(self.evidence_gaps) > 10:
            raise ValueError("at most 10 reasons and 10 evidence gaps")
        return self


# --------------------------------------------------------------------------- #
# the envelope decision                                                         #
# --------------------------------------------------------------------------- #

JevAnswer = Annotated[
    MemoryInjectionAnswer
    | ToolRoutingAnswer
    | CapabilityRankingAnswer
    | CompactionAnswer
    | VoiceTriageAnswer
    | AdversarialPlanAnswer
    | SemanticVerificationAnswer,
    Field(discriminator="kind"),
]

JEV_ANSWERS: dict[str, Any] = {
    "memory_injection": MemoryInjectionAnswer,
    "tool_routing": ToolRoutingAnswer,
    "capability_ranking": CapabilityRankingAnswer,
    "context_compaction": CompactionAnswer,
    "voice_triage": VoiceTriageAnswer,
    "adversarial_test_planning": AdversarialPlanAnswer,
    "semantic_verification": SemanticVerificationAnswer,
}

JEV_REQUESTS: dict[str, Any] = {
    "memory_injection": JevMemoryInjectionRequest,
    "tool_routing": JevToolRoutingRequest,
    "capability_ranking": JevCapabilityRankingRequest,
    "context_compaction": JevCompactionRequest,
    "voice_triage": JevVoiceTriageRequest,
    "adversarial_test_planning": JevAdversarialPlanRequest,
    "semantic_verification": JevSemanticVerificationRequest,
}


class JevDecision(BaseModel):
    """The one answer every Jev call returns — advisory by construction (§4.2)."""

    model_config = _FROZEN

    decision_id: str
    kind: JevKind
    run_id: str
    goal_id: str
    goal_revision: int = 0
    authority: Literal["advisory"] = "advisory"
    certifies: Literal[False] = False
    grants: tuple[str, ...] = Field(default=(), max_length=0)
    status: Status
    provider_mode: ProviderMode
    live_jev: bool = False
    question_pack_id: str = ""
    question_pack_version: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    answer: JevAnswer
    refusal_reason: str = ""
    fallback: str = ""
    caution_level: int = Field(default=0, ge=0, le=3)
    latency_ms: int = 0
    attempts: int = 1
    usage: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    created_at: float = 0.0

    @model_validator(mode="after")
    def _rules(self) -> JevDecision:
        # local import: rules.py must stay importable before the models exist,
        # because it is what scans them (no import cycle).
        from .rules import assert_decision_authority

        if self.live_jev != (self.provider_mode == "typesafe_jev_live"):
            raise ValueError(
                "live_jev must hold exactly for provider_mode 'typesafe_jev_live' "
                "(§4.2 honesty labelling)"
            )
        if self.answer.kind != self.kind:
            raise ValueError(
                f"answer kind {self.answer.kind!r} contradicts decision kind {self.kind!r}"
            )
        return assert_decision_authority(self)


# --------------------------------------------------------------------------- #
# the registries the rest of the package reads                                  #
# --------------------------------------------------------------------------- #

#: Every contract model, in one tuple: the layer-1 scan walks exactly this.
JEV_CONTRACTS: tuple[Any, ...] = (
    # requests + their inputs
    MemoryCandidate,
    MemoryInjectionBudget,
    JevMemoryInjectionRequest,
    RouteCandidate,
    RoutingConstraints,
    RoutingEnvironment,
    JevToolRoutingRequest,
    CapabilityNeed,
    CapabilityCandidate,
    JevCapabilityRankingRequest,
    HistoryToolCall,
    HistoryEntry,
    CompactionState,
    CompactOptions,
    JevCompactionRequest,
    TrajectoryItem,
    JevVoiceTriageRequest,
    AdversarialTarget,
    TestSurface,
    AdversarialBudget,
    CaseOutcome,
    JevAdversarialPlanRequest,
    BriefFile,
    BriefCommand,
    VerifierBudget,
    JevSemanticVerificationRequest,
    # answers + their parts
    MemoryVerdict,
    MemoryInjectionAnswer,
    RouteChoice,
    RouteAlternative,
    ToolRoutingAnswer,
    CapabilityRankingEntry,
    CapabilityRankingAnswer,
    CompactionCallDecision,
    CompactionStats,
    CompactionAnswer,
    VoiceTriageAnswer,
    Oracle,
    TestCasePlan,
    AdversarialPlanAnswer,
    SemanticVerificationAnswer,
    # the envelope
    JevDecision,
)


def request_for(kind: str, state: Mapping[str, Any] | None = None) -> JevRequestBase:
    """Build the request model of ``kind`` from a plain mapping.

    This is what makes ``decide(kind, state)`` possible: ``state`` is the
    kind-specific payload, and an undeclared key is a contract error (every
    model is ``extra="forbid"``) rather than a silently ignored field.
    """
    model = JEV_REQUESTS.get(str(kind))
    if model is None:
        raise ValueError(f"unknown Jev decision kind: {kind!r} (known: {list(DECISION_KINDS)})")
    payload = dict(state or {})
    return model(**payload)


def new_decision_id(kind: str) -> str:
    """A stable-shaped id: ``jev-<kind>-<hex>``."""
    import uuid

    return f"jev-{kind}-{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


# --------------------------------------------------------------------------- #
# layer 1 — the import-time contract scan                                       #
# --------------------------------------------------------------------------- #
#
# Deliberately at the tail of the module: every model above must exist before it
# can be scanned. A finding raises ImportError, so a contract that could carry a
# PASS, a permission, a security exception or a goal rewrite is unshippable —
# the failure happens at import, not at the first bad decision.
from .rules import scan_contracts as _scan_contracts  # noqa: E402

CONTRACT_SCAN: dict = _scan_contracts(JEV_CONTRACTS)

__all__ = [
    "CONTRACT_SCAN",
    "JEV_ANSWERS",
    "JEV_CONTRACTS",
    "JEV_REQUESTS",
    "AdversarialBudget",
    "AdversarialPlanAnswer",
    "AdversarialTarget",
    "BriefCommand",
    "BriefFile",
    "CapabilityCandidate",
    "CapabilityNeed",
    "CapabilityRankingAnswer",
    "CapabilityRankingEntry",
    "CaseOutcome",
    "CompactOptions",
    "CompactionAnswer",
    "CompactionCallDecision",
    "CompactionState",
    "CompactionStats",
    "HistoryEntry",
    "HistoryToolCall",
    "JevAdversarialPlanRequest",
    "JevAnswer",
    "JevCapabilityRankingRequest",
    "JevCompactionRequest",
    "JevDecision",
    "JevMemoryInjectionRequest",
    "JevRequestBase",
    "JevToolRoutingRequest",
    "JevVoiceTriageRequest",
    "JevSemanticVerificationRequest",
    "MemoryCandidate",
    "MemoryInjectionAnswer",
    "MemoryInjectionBudget",
    "MemoryVerdict",
    "Oracle",
    "ProviderMode",
    "RouteAlternative",
    "RouteCandidate",
    "RouteChoice",
    "RoutingConstraints",
    "RoutingEnvironment",
    "SemanticVerificationAnswer",
    "Status",
    "TestCasePlan",
    "TestSurface",
    "ToolRoutingAnswer",
    "TrajectoryItem",
    "VerificationSupport",
    "VerifierBudget",
    "VoiceTriageAnswer",
    "new_decision_id",
    "now",
    "request_for",
]
