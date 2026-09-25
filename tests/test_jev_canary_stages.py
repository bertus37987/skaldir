"""JEV-20/§21 — the six canary stages J0…J5, their flag mapping and the
invariant that hard policy stays outside Jev.

ROT-FIRST evidence, captured before the module existed:

    $ .venv/bin/python -c "import godsv.system_one_canary"
    ModuleNotFoundError: No module named 'godsv.system_one_canary'

    $ .venv/bin/python -m pytest tests/test_jev_canary_stages.py -p no:randomly
    ERROR tests/test_jev_canary_stages.py - ModuleNotFoundError:
        No module named 'godsv.system_one_canary'
    !!! Interrupted: 1 error during collection !!!

What this file pins (behaviour, never source text):

* the stage ladder is data: six stages with the goal's names, in order;
* a stage maps to concrete §22 flags and an explicit **effect** (what Jev is
  allowed to influence there) — ``J2`` shadow has no product effect, ``J3``
  advisory only advises, ``J4``/``J5`` are the first stages that may act, and
  only for low-risk classes;
* **no silent skip**: advancing more than one stage needs an explicit reason and
  the skipped stages are reported; rolling back is always allowed (§20);
* the calibration gate is real: ``J4``/``J5`` need a frozen threshold profile
  (and ``J5`` a holdout) — neither is invented when it is missing;
* **hard policy stays outside** at every stage: no stage lets Jev authorize an
  R4/high-stakes action, set completion, widen ``allowed_kinds`` or bypass
  approval. The LLM adapter caps at ``J3``: it is a readiness path, never a
  live-Jev activation.

Evidence class: contract (stage/flag machine). No live TypeSafe access and no
real desktop exist here, so ``J4``/``J5`` are reachable as *policy*, and remain
``blocked``/``not_run`` as a real activation (see
``docs/audit/JEV_PROVIDER_COMPATIBILITY.md``).
"""

from __future__ import annotations

import pytest

from skaldir.system_one_canary import (
    CANARY_STAGE_IDS,
    CANARY_STAGES,
    HARD_POLICY_OUTSIDE,
    STAGE_DISABLED,
    STAGE_LOW_RISK_ACTIVE,
    STAGE_SHADOW,
    UnknownCanaryStage,
    canary_stage,
    stage_for_flags,
    stage_invariants,
    stage_policy,
    stage_transition,
)
from skaldir.system_one_flags import SystemOneFlags

FROZEN_PROFILE = {"frozen_threshold_profile": "profile-2026-09-16"}
HOLDOUT = {"frozen_threshold_profile": "profile-2026-09-16", "holdout_evaluated": "holdout-1"}
INDEPENDENT = {**HOLDOUT, "independent_review": "review-1"}


def _flags(**overrides) -> SystemOneFlags:
    base = {
        "enabled": True,
        "provider": "llm_system_one_adapter",
        "shadow_mode": False,
        "voice_triage": False,
        "computer_use": True,
    }
    base.update(overrides)
    return SystemOneFlags(**base)


# --------------------------------------------------------------------------- #
# the ladder                                                                   #
# --------------------------------------------------------------------------- #


def test_the_ladder_is_the_goal_ladder():
    assert CANARY_STAGE_IDS == ("J0", "J1", "J2", "J3", "J4", "J5")
    names = tuple(stage.name for stage in CANARY_STAGES)
    assert names == (
        "disabled",
        "replay",
        "shadow",
        "advisory",
        "low_risk_active",
        "calibrated_active",
    )
    for stage in CANARY_STAGES:
        assert stage.product_effect is (stage.stage in ("J3", "J4", "J5"))
    assert canary_stage("J3").name == "advisory"
    with pytest.raises(UnknownCanaryStage):
        canary_stage("J9")


def test_the_stage_policy_is_explicit_about_what_a_stage_may_do():
    disabled = stage_policy(STAGE_DISABLED)
    assert disabled["product_effect"] is False
    assert disabled["live_jev_calls"] is False
    shadow = stage_policy(STAGE_SHADOW)
    assert shadow["product_effect"] is False
    assert shadow["records_traces"] is True
    advisory = stage_policy("J3")
    assert advisory["product_effect"] is True
    assert advisory["authorizes_actions"] is False
    low_risk = stage_policy(STAGE_LOW_RISK_ACTIVE)
    assert low_risk["product_effect"] is True
    assert low_risk["authorizes_actions"] is False, (
        "even J4 never authorizes: hard policy is outside"
    )
    assert low_risk["allowed_risk_classes"] == ("R0", "R1")
    calibrated = stage_policy("J5")
    assert calibrated["allowed_risk_classes"] == ("R0", "R1", "R2", "R3")


# --------------------------------------------------------------------------- #
# the §22 flag mapping                                                         #
# --------------------------------------------------------------------------- #


def test_the_flag_mapping_derives_the_stage():
    assert stage_for_flags(SystemOneFlags.disabled()) == "J0"
    assert stage_for_flags(_flags(provider="disabled")) == "J0"
    assert stage_for_flags(_flags(enabled=False)) == "J0"
    assert stage_for_flags(_flags(shadow_mode=True)) == "J2"
    assert stage_for_flags(_flags()) == "J3"
    live = {"provider": "typesafe_jev"}
    assert stage_for_flags(_flags(**live), evidence=FROZEN_PROFILE) == "J4"
    assert stage_for_flags(_flags(**live), evidence=HOLDOUT) == "J4"
    assert stage_for_flags(_flags(**live), evidence=INDEPENDENT) == "J5"


def test_a_stage_needing_calibration_is_not_reached_without_it():
    """J4/J5 are gated on evidence, not on a flag being flipped."""
    live = {"provider": "typesafe_jev"}
    assert stage_for_flags(_flags(**live), evidence={}) == "J3"
    # shadow_mode on means shadow: promotion is a flag change, not an evidence trick
    assert stage_for_flags(_flags(**live, shadow_mode=True), evidence=INDEPENDENT) == "J2"
    assert stage_for_flags(_flags(**live, shadow_mode=False), evidence=FROZEN_PROFILE) == "J4"
    assert stage_for_flags(_flags(**live, shadow_mode=False), evidence={}) == "J3"


def test_the_llm_readiness_path_never_becomes_a_live_activation():
    """§14: the LLM adapter is no live Jev proof — it caps at J3 'advisory'."""
    stage = stage_for_flags(
        _flags(provider="llm_system_one_adapter"), evidence=INDEPENDENT
    )
    assert stage == "J3"
    capped = stage_transition("J3", "J4", reason="ship it", evidence=INDEPENDENT, flags=_flags())
    assert capped.allowed is False
    assert "readiness" in capped.reason or "llm" in capped.reason.lower()


# --------------------------------------------------------------------------- #
# no silent skip, rollback always allowed                                      #
# --------------------------------------------------------------------------- #


def test_the_next_stage_is_reached_without_ceremony():
    decision = stage_transition("J2", "J3", evidence=FROZEN_PROFILE)
    assert decision.allowed is True
    assert decision.skipped_stages == ()
    assert decision.reason_required is False


def test_a_skip_needs_an_explicit_reason_and_names_the_skipped_stages():
    refused = stage_transition("J0", "J4", reason="", evidence=INDEPENDENT)
    assert refused.allowed is False
    assert "reason" in refused.reason.lower()

    allowed = stage_transition(
        "J0",
        "J4",
        reason="owner-approved emergency switch after review #4711",
        evidence=INDEPENDENT,
    )
    assert allowed.allowed is True
    assert allowed.skipped_stages == ("J1", "J2", "J3")
    assert allowed.reason_required is True


def test_a_skip_without_the_calibration_evidence_is_refused():
    decision = stage_transition(
        "J0", "J5", reason="owner-approved emergency switch after review #4711", evidence={}
    )
    assert decision.allowed is False
    assert "frozen_threshold_profile" in decision.reason


def test_rollback_is_always_allowed():
    for current in CANARY_STAGE_IDS:
        for target in CANARY_STAGE_IDS:
            if CANARY_STAGE_IDS.index(target) < CANARY_STAGE_IDS.index(current):
                decision = stage_transition(current, target)
                assert decision.allowed is True
                assert decision.rollback is True
    assert stage_transition("J5", "J0").allowed is True


def test_an_unknown_transition_is_refused_not_guessed():
    with pytest.raises(UnknownCanaryStage):
        stage_transition("J2", "J9")


# --------------------------------------------------------------------------- #
# hard policy stays outside — at every stage                                    #
# --------------------------------------------------------------------------- #


def test_hard_policy_is_outside_at_every_stage():
    assert HARD_POLICY_OUTSIDE is True
    for stage_id in CANARY_STAGE_IDS:
        invariants = stage_invariants(stage_id)
        assert invariants["hard_policy_outside"] is True
        assert invariants["sets_completion"] is False
        assert invariants["widens_allowed_kinds"] is False
        assert invariants["bypasses_approval"] is False
        assert invariants["authorizes_high_risk"] is False
        assert "R4" not in stage_policy(stage_id)["allowed_risk_classes"]
        assert "R4" in invariants["risk_classes_never_authorized"]


def test_only_the_calibrated_stage_may_influence_external_effects():
    for stage_id in ("J0", "J1", "J2"):
        assert stage_policy(stage_id)["authorizes_actions"] is False
    assert stage_policy("J3")["allowed_action_classes"] == ()
    assert stage_policy(STAGE_LOW_RISK_ACTIVE)["allowed_action_classes"] == ("R0", "R1")
