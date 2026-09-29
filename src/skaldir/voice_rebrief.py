"""The Voice rebrief contract — VOICE-03.

    "Rebrief nennt Befund, Evidence, betroffene Zielkriterien und konkreten nächsten
     Schritt. Kein vollständiges Neuschreiben des Goals, keine Rechteausweitung.
     Wiederaufnahme nach Voice-Pause nach dokumentierter Runtime-Regel; Nutzer-Stop bleibt
     ausschließlich nutzerfreigabepflichtig."

Four things must be named, two things are forbidden, and one pause cannot be cleared by
the mechanism that created it. This module is all three.

Why a rebrief has a shape at all: Voice pauses a run because something is wrong. The
message that ends the pause is the only chance to say *what* is wrong and *what to do*, so
it is worth refusing one that is missing a piece rather than letting the run resume into
the same problem.

The two prohibitions matter as much as the four parts. A rebrief is a course correction,
not a rewrite: it cannot quietly replace the goal the user gave, and it cannot hand itself
more rights than the run already had.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "Rebrief",
    "RebriefInvalid",
    "ResumeRefused",
    "UserStop",
    "validate_rebrief",
]


class RebriefInvalid(ValueError):
    """A rebrief is missing a required part, or contains something it may not."""


class ResumeRefused(RuntimeError):
    """A pause is being cleared by something that is not allowed to clear it."""


#: What a rebrief may *not* do (VOICE-03).
FORBIDDEN_IN_REBRIEF = (
    "full goal rewrite",
    "rights expansion",
)


@dataclass(frozen=True)
class Rebrief:
    """One course correction after a Voice pause."""

    finding: str
    """What is wrong — the observation, in one sentence."""

    evidence: str
    """How we know. A rebrief without evidence is a guess that ends a pause."""

    criteria: tuple[str, ...] = ()
    """Which of the run's goal criteria this affects."""

    next_step: str = ""
    """The concrete next action — not "continue", which re-enters the same loop."""

    goal: str = ""
    """The goal **as it already was**. A rebrief may restate it; it may not replace it."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding": self.finding,
            "evidence": self.evidence,
            "criteria": list(self.criteria),
            "next_step": self.next_step,
            "goal": self.goal,
        }


@dataclass
class UserStop:
    """A stop issued by the user. Only the user can clear it."""

    reason: str = ""
    at: float = 0.0
    released: bool = False
    released_by: str = ""

    def release(self, *, by: str) -> None:
        """Only the user may do this — checked by name, not by convention."""
        if str(by or "").strip().lower() not in ("user", "owner", "operator"):
            raise ResumeRefused(
                f"resume refused: {by or 'nobody'!r} may not clear a user stop — "
                "a user stop is exclusively user-release (VOICE-03)"
            )
        self.released = True
        self.released_by = str(by)


def validate_rebrief(rebrief: Rebrief) -> Rebrief:
    """Check a rebrief against VOICE-03. Raises :class:`RebriefInvalid` when it is not one.

    The rule for each part is deliberately "has to be a real sentence", not "has to be
    non-empty": a next step of "continue" re-enters the loop Voice just paused.
    """
    if not (rebrief.finding or "").strip():
        raise RebriefInvalid("a rebrief must name a finding (VOICE-03)")
    if not (rebrief.evidence or "").strip():
        raise RebriefInvalid(
            "a rebrief must carry evidence (VOICE-03) — a pause is not ended by a guess"
        )
    if not rebrief.criteria:
        raise RebriefInvalid(
            "a rebrief must name the goal criteria it affects (VOICE-03)"
        )
    step = (rebrief.next_step or "").strip()
    if not step:
        raise RebriefInvalid("a rebrief must name a concrete next step (VOICE-03)")
    if step.lower() in {"continue", "keep going", "proceed", "carrying on", "weiter"}:
        raise RebriefInvalid(
            f"next step {step!r} is not a step — it re-enters the loop Voice just paused"
        )

    # the two prohibitions
    if len(rebrief.goal) > 4000:
        raise RebriefInvalid(
            "a rebrief may restate the goal, not rewrite it (VOICE-03) — this one is a "
            "full goal document"
        )
    lowered = f"{rebrief.finding}\n{rebrief.next_step}\n{rebrief.goal}".lower()
    for marker in ("grant", "approve myself", "give me permission", "expand scope",
                   "widen rights", "skip approval"):
        if marker in lowered:
            raise RebriefInvalid(
                f"a rebrief may not widen rights (VOICE-03): found {marker!r}"
            )
    return rebrief


def resume_after_voice_pause(
    *,
    by: str,
    rule: str,
    documented_rules: Iterable[str],
    user_stop: UserStop | None = None,
) -> dict[str, Any]:
    """Resume a Voice pause — only through a documented runtime rule.

    VOICE-03: "Wiederaufnahme nach Voice-Pause nach dokumentierter Runtime-Regel;
    Nutzer-Stop bleibt ausschließlich nutzerfreigabepflichtig."

    So two independent checks: the rule must be one that is actually documented, and a
    user stop is *never* cleared here regardless of who asks.
    """
    rules = [str(r).strip() for r in documented_rules if str(r).strip()]
    if not str(rule).strip() or str(rule).strip() not in rules:
        raise ResumeRefused(
            f"resume refused: {rule!r} is not a documented runtime rule — a Voice pause "
            "is only ended by one (VOICE-03)"
        )
    if user_stop is not None and not user_stop.released:
        raise ResumeRefused(
            "resume refused: a user stop is still in force and is exclusively "
            "user-release (VOICE-03) — a runtime rule cannot clear it"
        )
    return {"resumed": True, "rule": str(rule).strip(), "by": str(by)}
