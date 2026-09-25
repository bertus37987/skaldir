"""The seven decision kinds and their mapping onto the System-One question registry.

Jev (``skaldir.jev``) is a *typed decision façade* over the substrate that already
exists: ``skaldir.system_one*`` holds the provider edge (``SystemOneService``), the
versioned question registry (``voice_triage_v1``, ``completion_v1``, …) and its
own rule §27 "no second contract". This package therefore adds **no** provider,
**no** transport and **no** second question registry — it adds six *typed*
requirements (request model → question pack → typed answer) and the four hard
rules of MVP/03 §6.

Every module here obeys:

* one kind, one pack ref, one answer model;
* choices are ``Literal``s, never free text;
* Jev decides *choices*; the runtime gates keep every authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

# --------------------------------------------------------------------------- #
# the seven kinds of MVP/03 §5 + MVP/93                                             #
# --------------------------------------------------------------------------- #

#: The small decisions Jev is allowed to make (MVP/03 §5 + the seventh of MVP/93).
#: ``Literal`` types below and this tuple cannot drift apart silently —
#: ``tests/test_jev_facade.py`` asserts they agree.
DECISION_KINDS: tuple[str, ...] = (
    "memory_injection",
    "tool_routing",
    "capability_ranking",
    "context_compaction",
    "voice_triage",
    "adversarial_test_planning",
    "semantic_verification",
)

#: The contract type of ``JevDecision.kind`` / every request's ``kind``.
JevKind = Literal[
    "memory_injection",
    "tool_routing",
    "capability_ranking",
    "context_compaction",
    "voice_triage",
    "adversarial_test_planning",
    "semantic_verification",
]

assert get_args(JevKind) == DECISION_KINDS, "kind vocabulary drifted from its Literal"

# --------------------------------------------------------------------------- #
# decision kind → question pack                                                 #
# --------------------------------------------------------------------------- #

#: Which registered pack answers which kind (MVP/03 §4.3 ``PACK_FOR_KIND``).
#:
#: ``voice_triage`` is the substrate's **existing** ``voice_triage_v1`` pack —
#: reused, never forked (§27). The other five are registered by
#: :mod:`skaldir.jev.packs` in the Jev registry view, which is the substrate
#: registry *plus* these packs; the substrate default registry is untouched so
#: no other consumer's manifest moves.
PACK_FOR_KIND: dict[str, str] = {
    "memory_injection": "memory_injection_v1",
    "tool_routing": "routing_v1",
    "capability_ranking": "capability_ranking_v1",
    "context_compaction": "compaction_v1",
    "voice_triage": "voice_triage_v1",
    "adversarial_test_planning": "adversarial_test_v1",
    "semantic_verification": "semantic_verification_v1",
}

#: Which rollout flag (§7.4) gates which kind. ``decide()`` does **not** read
#: these — the caller owns its own gate — but the CLI self-audit reports them so
#: the flag surface is visible in one place.
FLAG_FOR_KIND: dict[str, str] = {
    "memory_injection": "memory_injection",
    "tool_routing": "tool_routing",
    "capability_ranking": "capability_ranking",
    "context_compaction": "context_compaction",
    "voice_triage": "voice_triage",
    "adversarial_test_planning": "adversarial_planning",
    "semantic_verification": "semantic_verification",
}

#: What the caller must do when Jev has no advisory (MVP/03 §5, per kind).
#: These are the *documented* fallbacks; :func:`fallback_for` also reads the
#: pack's own ``fallback_policy`` when a pack is available.
FALLBACK_FOR_KIND: dict[str, str] = {
    "memory_injection": "keep the deterministic retrieval order and the existing token budget",
    "tool_routing": "keep capability-mining order + Computer Use",
    "capability_ranking": "keep the retrieval score order",
    "context_compaction": "keep the full transcript (or the existing summary path)",
    "voice_triage": "the deterministic Voice hint alone (legacy path)",
    "adversarial_test_planning": "the deterministic episode catalogue in evalharness.py",
    "semantic_verification": (
        "the deterministic VERIFY verdict stands alone — the semantic gate is "
        "recorded as 'no advisory', never assumed to have agreed"
    ),
}


def fallback_for(kind: str) -> str:
    """The caller's own path when Jev returns no advisory for ``kind``."""
    return FALLBACK_FOR_KIND.get(kind, "the caller's own deterministic path")


def pack_ref_for(kind: str) -> str:
    try:
        return PACK_FOR_KIND[kind]
    except KeyError as exc:  # pragma: no cover — the Literal already refuses it
        raise KeyError(f"unknown Jev decision kind: {kind!r}") from exc


# --------------------------------------------------------------------------- #
# read thresholds — declared, never invented                                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReadThresholds:
    """The thresholds a *reading* is compared against (MVP/03 §5.2 "0.5 read threshold").

    Deliberately a plain, frozen, explicitly-declared object: every number used
    to turn a probability into a Jev choice is visible here instead of being
    buried in a branch. The calibrated substrate equivalent is
    :class:`skaldir.system_one_policy.ThresholdProfile` (per action class, frozen
    before holdout); ``decide(..., thresholds=ReadThresholds(...))`` accepts a
    replacement, and binding these to a measured profile is the calibration
    owner's step, not a number this package may invent.
    """

    read_at: float = 0.5
    """A reading at or above this counts as "yes"."""

    drop_at: float = 0.2
    """Below this a candidate is dropped rather than deferred."""

    influence_at: float = 0.5
    """Below this a candidate is too weak to change the ordering."""

    def yes(self, value: float) -> bool:
        return float(value) >= self.read_at

    def no(self, value: float) -> bool:
        return float(value) < self.read_at


DEFAULT_READ_THRESHOLDS = ReadThresholds()

__all__ = [
    "DECISION_KINDS",
    "DEFAULT_READ_THRESHOLDS",
    "FALLBACK_FOR_KIND",
    "FLAG_FOR_KIND",
    "JevKind",
    "PACK_FOR_KIND",
    "ReadThresholds",
    "fallback_for",
    "pack_ref_for",
]
