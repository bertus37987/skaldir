"""Canary rollout stages J0…J5 (§21, ABNAHME §46/JEV-20, flags §22).

    J0 disabled
    J1 replay
    J2 shadow
    J3 advisory
    J4 low-risk active
    J5 calibrated active

Hard policy stays outside at **every** stage. That is not a comment here, it is
:data:`HARD_POLICY_OUTSIDE` plus the invariants :func:`stage_invariants` returns
for each stage: no stage lets System One authorize an R4/high-stakes action, set
completion, widen ``allowed_kinds`` or bypass an approval. The stages only say
how much *influence* a probabilistic judgement may have, never what is allowed.

Two rules keep the ladder honest:

* **a stage is derived, not declared** — :func:`stage_for_flags` reads the §22
  flags and the calibration evidence; ``J4``/``J5`` are unreachable without a
  frozen threshold profile (``J5`` also without a holdout), and the LLM
  readiness adapter caps at ``J3`` because §14 forbids treating it as live Jev;
* **no silent skip** — :func:`stage_transition` allows the next stage freely,
  allows any rollback (§20), and refuses a multi-stage jump unless an explicit
  reason *and* the evidence for the stages being skipped are given; a jump that
  did happen reports the skipped stages.

Honest status: ``J0``–``J3`` are reachable today; ``J4``/``J5`` are gated on
calibration and holdout evidence that does not exist yet, so as a *real*
activation they stay ``blocked``/``not_run``. This module is the policy machine,
not a measurement.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .system_one import SystemOneContractError

#: The ladder, verbatim (§21).
CANARY_STAGE_IDS: tuple[str, ...] = ("J0", "J1", "J2", "J3", "J4", "J5")

STAGE_DISABLED = "J0"
STAGE_REPLAY = "J1"
STAGE_SHADOW = "J2"
STAGE_ADVISORY = "J3"
STAGE_LOW_RISK_ACTIVE = "J4"
STAGE_CALIBRATED_ACTIVE = "J5"

#: §21: "Hard Policy bleibt immer ausserhalb." Not configurable, by design.
HARD_POLICY_OUTSIDE = True

#: Risk classes System One may never authorize on its own (§13 R4 + R2/R3 limits).
RISK_CLASSES_NEVER_AUTHORIZED: tuple[str, ...] = ("R4",)

#: Evidence keys a stage can require. Nothing here is ever assumed present.
EVIDENCE_FROZEN_PROFILE = "frozen_threshold_profile"
EVIDENCE_HOLDOUT = "holdout_evaluated"
EVIDENCE_INDEPENDENT_REVIEW = "independent_review"

#: A skip needs a reason that says something; a placeholder does not count.
MIN_SKIP_REASON_CHARS = 12
_SKIP_PLACEHOLDERS = frozenset({"", "-", "n/a", "na", "skip", "todo", "tbd", "x"})


class UnknownCanaryStage(SystemOneContractError):
    """A stage id that is not part of the §21 ladder."""


class CanaryTransitionRefused(SystemOneContractError):
    """A stage transition that would skip the ladder without a stated reason."""


@dataclass(frozen=True)
class CanaryStage:
    """One stage: what it needs, and how far its influence may reach."""

    stage: str
    name: str
    product_effect: bool
    live_jev_calls: bool
    records_traces: bool
    advisory_only: bool
    allowed_risk_classes: tuple[str, ...]
    allowed_action_classes: tuple[str, ...]
    required_evidence: tuple[str, ...]
    flags: Mapping[str, object] = field(default_factory=dict)
    description: str = ""

    def policy(self) -> dict:
        return {
            "stage": self.stage,
            "name": self.name,
            "product_effect": self.product_effect,
            "live_jev_calls": self.live_jev_calls,
            "records_traces": self.records_traces,
            "advisory_only": self.advisory_only,
            # Jev never authorizes: the runtime's gates/approvals do (§13/§50). What a
            # stage may do is *influence* the execution of already-allowed low-risk steps.
            "authorizes_actions": False,
            "influences_action_execution": bool(self.allowed_action_classes),
            "allowed_risk_classes": self.allowed_risk_classes,
            "allowed_action_classes": self.allowed_action_classes,
            "required_evidence": self.required_evidence,
            "flag_mapping": dict(self.flags),
            "hard_policy_outside": HARD_POLICY_OUTSIDE,
            "sets_completion": False,
            "widens_allowed_kinds": False,
            "bypasses_approval": False,
            "authorizes_high_risk": False,
        }


CANARY_STAGES: tuple[CanaryStage, ...] = (
    CanaryStage(
        stage=STAGE_DISABLED,
        name="disabled",
        product_effect=False,
        live_jev_calls=False,
        records_traces=False,
        advisory_only=False,
        allowed_risk_classes=(),
        allowed_action_classes=(),
        required_evidence=(),
        flags={"system_one.enabled": False, "system_one.provider": "disabled"},
        description="System One is off; the existing deterministic paths run unchanged.",
    ),
    CanaryStage(
        stage=STAGE_REPLAY,
        name="replay",
        product_effect=False,
        live_jev_calls=False,
        records_traces=True,
        advisory_only=False,
        allowed_risk_classes=(),
        allowed_action_classes=(),
        required_evidence=(),
        flags={"system_one.provider": "replay_fixture"},
        description="Recorded answers are replayed offline: no call, no influence.",
    ),
    CanaryStage(
        stage=STAGE_SHADOW,
        name="shadow",
        product_effect=False,
        live_jev_calls=True,
        records_traces=True,
        advisory_only=False,
        allowed_risk_classes=(),
        allowed_action_classes=(),
        required_evidence=(),
        flags={"system_one.enabled": True, "system_one.shadow_mode": True},
        description=(
            "Real evaluation beside the real decision, recorded only — the product "
            "outcome is unchanged (§18)."
        ),
    ),
    CanaryStage(
        stage=STAGE_ADVISORY,
        name="advisory",
        product_effect=True,
        live_jev_calls=True,
        records_traces=True,
        advisory_only=True,
        allowed_risk_classes=("R0", "R1"),
        allowed_action_classes=(),
        required_evidence=(),
        flags={
            "system_one.enabled": True,
            "system_one.shadow_mode": False,
            "system_one.computer_use": True,
        },
        description=(
            "System One advises the deterministic decision policy. It still authorizes "
            "nothing: hard policy, approvals and completion stay outside."
        ),
    ),
    CanaryStage(
        stage=STAGE_LOW_RISK_ACTIVE,
        name="low_risk_active",
        product_effect=True,
        live_jev_calls=True,
        records_traces=True,
        advisory_only=True,
        allowed_risk_classes=("R0", "R1"),
        allowed_action_classes=("R0", "R1"),
        required_evidence=(EVIDENCE_FROZEN_PROFILE,),
        flags={
            "system_one.enabled": True,
            "system_one.shadow_mode": False,
            "system_one.computer_use": True,
            "system_one.provider": "typesafe_jev",
        },
        description=(
            "Low-risk steps (observation, reversible internal routing) may follow the "
            "System-One reading — inside the pre-approved action set, with a frozen "
            "threshold profile."
        ),
    ),
    CanaryStage(
        stage=STAGE_CALIBRATED_ACTIVE,
        name="calibrated_active",
        product_effect=True,
        live_jev_calls=True,
        records_traces=True,
        advisory_only=True,
        allowed_risk_classes=("R0", "R1", "R2", "R3"),
        allowed_action_classes=("R0", "R1", "R2", "R3"),
        required_evidence=(
            EVIDENCE_FROZEN_PROFILE,
            EVIDENCE_HOLDOUT,
            EVIDENCE_INDEPENDENT_REVIEW,
        ),
        flags={
            "system_one.enabled": True,
            "system_one.shadow_mode": False,
            "system_one.computer_use": True,
            "system_one.voice_triage": True,
            "system_one.provider": "typesafe_jev",
        },
        description=(
            "Full active rollout after calibration, holdout and independent review — "
            "R4 and every policy/auth/goal-revision decision still never runs through "
            "System One."
        ),
    ),
)


def canary_stage(stage_id: str) -> CanaryStage:
    """One stage by id; an unknown id is an error, never a fallback."""
    wanted = str(stage_id or "").strip().upper()
    for stage in CANARY_STAGES:
        if stage.stage == wanted:
            return stage
    raise UnknownCanaryStage(f"unknown canary stage: {stage_id!r} (known: {CANARY_STAGE_IDS})")


def stage_policy(stage_id: str) -> dict:
    """The explicit policy record of one stage."""
    return canary_stage(stage_id).policy()


def stage_invariants(stage_id: str) -> dict:
    """What must hold at this stage regardless of configuration (§50/§34)."""
    canary_stage(stage_id)
    return {
        "stage": str(stage_id).upper(),
        "hard_policy_outside": True,
        "sets_completion": False,
        "widens_allowed_kinds": False,
        "bypasses_approval": False,
        "authorizes_actions": False,
        "authorizes_high_risk": False,
        "candidates_only_from_pre_approved_set": True,
        "ground_truth_is_real_app_effect": True,
        "risk_classes_never_authorized": RISK_CLASSES_NEVER_AUTHORIZED,
    }


def _missing_evidence(evidence: Mapping[str, object] | None, stage: CanaryStage) -> tuple[str, ...]:
    """Which required evidence keys the caller did **not** provide (§117 'not_run')."""
    given = {key for key, value in dict(evidence or {}).items() if value}
    return tuple(key for key in stage.required_evidence if key not in given)


def _cap_reason(flags, target_stage: CanaryStage) -> str:
    """Why these flags may not carry this stage (empty = no objection)."""
    provider = str(getattr(flags, "provider", "") or "")
    if provider != "typesafe_jev" and target_stage.live_jev_calls:
        if target_stage.stage in (STAGE_LOW_RISK_ACTIVE, STAGE_CALIBRATED_ACTIVE):
            return (
                f"provider {provider or '(unset)'} is not the authorized live Jev provider "
                "— the LLM System-One adapter is a readiness path (§14), so it caps at "
                f"{STAGE_ADVISORY}"
            )
    return ""


def stage_for_flags(flags, *, evidence: Mapping[str, object] | None = None) -> str:
    """Derive the highest stage these §22 flags + evidence reach.

    Reading order: the master switch decides ``J0``; ``shadow_mode`` is ``J2``;
    otherwise the provider decides the ceiling (``llm_system_one_adapter`` is a
    readiness path and stops at ``J3``), and ``J4``/``J5`` additionally need
    their calibration evidence. Promotion is always a flag + evidence change —
    never an inference from an old result.
    """
    if flags is None or not getattr(flags, "enabled", False):
        return STAGE_DISABLED
    provider = str(getattr(flags, "provider", "disabled") or "disabled")
    if provider == "disabled":
        return STAGE_DISABLED
    if bool(getattr(flags, "shadow_mode", False)):
        return STAGE_SHADOW
    if provider != "typesafe_jev":
        return STAGE_ADVISORY
    if _missing_evidence(evidence, canary_stage(STAGE_LOW_RISK_ACTIVE)):
        return STAGE_ADVISORY
    if _missing_evidence(evidence, canary_stage(STAGE_CALIBRATED_ACTIVE)):
        return STAGE_LOW_RISK_ACTIVE
    return STAGE_CALIBRATED_ACTIVE


@dataclass(frozen=True)
class TransitionDecision:
    """One stage transition: allowed or refused, and on what grounds."""

    current: str
    target: str
    allowed: bool
    rollback: bool = False
    reason_required: bool = False
    skipped_stages: tuple[str, ...] = ()
    reason: str = ""
    missing_evidence: tuple[str, ...] = ()
    stage_policy: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "current": self.current,
            "target": self.target,
            "allowed": self.allowed,
            "rollback": self.rollback,
            "reason_required": self.reason_required,
            "skipped_stages": list(self.skipped_stages),
            "reason": self.reason,
            "missing_evidence": list(self.missing_evidence),
            "stage_policy": dict(self.stage_policy),
        }


def _usable_reason(reason: str) -> bool:
    text = str(reason or "").strip()
    return len(text) >= MIN_SKIP_REASON_CHARS and text.lower() not in _SKIP_PLACEHOLDERS


def stage_transition(
    current: str,
    target: str,
    *,
    reason: str = "",
    evidence: Mapping[str, object] | None = None,
    flags=None,
) -> TransitionDecision:
    """Move along the ladder: next stage free, rollback always, skip only with a reason.

    A skip (more than one stage forward) demands an explicit, non-placeholder
    reason **and** the target's required evidence; it then reports
    ``skipped_stages`` so the audit trail shows what was stepped over. ``flags``
    is optional and only adds the §14 readiness cap.
    """
    origin = canary_stage(current)
    destination = canary_stage(target)
    index_now = CANARY_STAGE_IDS.index(origin.stage)
    index_next = CANARY_STAGE_IDS.index(destination.stage)
    policy = destination.policy()

    if index_next == index_now:
        return TransitionDecision(
            current=origin.stage, target=destination.stage, allowed=True,
            reason="stage unchanged", stage_policy=policy,
        )
    if index_next < index_now:
        return TransitionDecision(
            current=origin.stage,
            target=destination.stage,
            allowed=True,
            rollback=True,
            reason=f"rollback from {origin.stage} to {destination.stage} is always allowed (§20)",
            stage_policy=policy,
        )

    skipped = CANARY_STAGE_IDS[index_now + 1 : index_next]
    missing = _missing_evidence(evidence, destination)
    cap = _cap_reason(flags, destination) if flags is not None else ""

    def refused(why: str) -> TransitionDecision:
        return TransitionDecision(
            current=origin.stage,
            target=destination.stage,
            allowed=False,
            reason_required=bool(skipped),
            skipped_stages=skipped,
            reason=why,
            missing_evidence=missing,
            stage_policy=policy,
        )

    if cap:
        return refused(cap)
    if missing:
        # Missing evidence is reported before the reason question: floor first.
        return refused(
            f"{destination.stage} requires evidence {list(missing)} — a stage that is not "
            "measured stays not_run instead of being assumed (§117)"
        )
    if skipped and not _usable_reason(reason):
        return refused(
            f"skipping {'/'.join(skipped)} needs an explicit reason of at least "
            f"{MIN_SKIP_REASON_CHARS} characters (got {str(reason or '').strip()!r})"
        )
    return TransitionDecision(
        current=origin.stage,
        target=destination.stage,
        allowed=True,
        reason_required=bool(skipped),
        skipped_stages=skipped,
        reason=str(reason).strip() or f"{origin.stage} → {destination.stage}",
        stage_policy=policy,
    )


__all__ = [
    "CANARY_STAGE_IDS",
    "CANARY_STAGES",
    "EVIDENCE_FROZEN_PROFILE",
    "EVIDENCE_HOLDOUT",
    "EVIDENCE_INDEPENDENT_REVIEW",
    "HARD_POLICY_OUTSIDE",
    "MIN_SKIP_REASON_CHARS",
    "RISK_CLASSES_NEVER_AUTHORIZED",
    "STAGE_ADVISORY",
    "STAGE_CALIBRATED_ACTIVE",
    "STAGE_DISABLED",
    "STAGE_LOW_RISK_ACTIVE",
    "STAGE_REPLAY",
    "STAGE_SHADOW",
    "CanaryStage",
    "CanaryTransitionRefused",
    "TransitionDecision",
    "UnknownCanaryStage",
    "canary_stage",
    "stage_for_flags",
    "stage_invariants",
    "stage_policy",
    "stage_transition",
]
