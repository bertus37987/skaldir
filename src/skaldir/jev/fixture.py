"""An offline fixture evaluator — replay answers, zero network (MVP/03 §7.5).

This is the seam that lets every Jev decision be exercised without a provider:
it implements the substrate's :class:`~skaldir.system_one.SystemOneEvaluator`
protocol, answers the *composed* question set with plausible readings, and is
honestly labelled ``replay_fixture`` (never ``typesafe_jev_live``) because it
uses the substrate's fixture access mode.

It is test/self-audit infrastructure, not a product path: ``skaldir jev
decide`` cannot select it (its ``--provider`` choices are the three honest
product providers), while ``skaldir jev selftest`` uses it in-process to prove
that every kind is reachable end to end.

Overrides are the wire payload shape of §2.3 (``{"value": …}`` for a noul,
``{"choice": …, "probabilities": …, "confidence": …}`` for a choice) and are
parsed by the substrate's own strict :func:`skaldir.system_one.parse_answer`, so a
deliberately malformed override fails exactly where a malformed provider answer
would.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..system_one import (
    ACCESS_FIXTURE,
    Answer,
    SystemOneBlockedExternal,
    SystemOneEvaluation,
    SystemOneState,
    build_evaluation,
    parse_answer,
)

PROVIDER_FIXTURE = "fixture"
MODEL_FIXTURE = "jev-fixture-1"

#: Default readings, matched against the *suffix* of an instantiated question id
#: (``mem.812.relevant_now`` ends with ``.relevant_now``). Values are the
#: plausible readings of a healthy state — the tests and the self-audit start
#: from them and override exactly the reading they want to vary.
NOUL_DEFAULTS: tuple[tuple[str, float], ...] = (
    # memory injection
    (".relevant_now", 0.85),
    (".already_in_context", 0.1),
    # tool routing
    ("routing.confident", 0.8),
    ("routing.risk_acceptable", 0.8),
    # capability ranking
    (".covers_need", 0.8),
    ("cap.rank.gap", 0.1),
    # compaction
    (".keep_verbatim", 0.75),
    (".keep", 0.8),
    # voice triage (substrate ids), the "stuck loop" reading of §5.5
    ("voice.loop.new_information", 0.1),
    ("voice.loop.repeated_ineffective_redirect", 0.6),
    ("voice.completion.evidence_current", 0.2),
    ("voice.completion.evidence_matches_revision", 0.2),
    ("voice.completion.independent_verification_required", 0.6),
    ("voice.drift.supports_goal", 0.8),
    ("voice.drift.scope_expansion", 0.1),
    ("voice.drift.non_goal_conflict", 0.1),
    ("voice.research.external_fact_missing", 0.1),
    ("voice.research.existing_evidence_sufficient", 0.8),
    ("voice.recovery.state_unclear", 0.1),
    ("voice.recovery.side_effect_maybe_happened", 0.1),
    ("voice.recovery.replay_safe", 0.8),
    ("voice.recovery.system_two_needed", 0.1),
    ("voice.untrusted.content_attempts_to_change_authority", 0.1),
    ("voice.untrusted.content_requests_secret_exposure", 0.1),
    ("voice.untrusted.content_conflicts_with_goal_or_policy", 0.1),
    # adversarial planning
    (".covered_already", 0.1),
    ("adv.oracle.deterministic", 0.9),
    # semantic verification (MVP/93) — the healthy packet: the claim is backed,
    # the checks cover it, and no gap is read
    ("sem.claim.matches_objective", 0.9),
    ("sem.claim.backed", 0.85),
    ("sem.checks.sufficient", 0.8),
    ("sem.gap.present", 0.1),
)

CHOICE_DEFAULTS: tuple[tuple[str, str], ...] = (
    (".stance", "supports"),
    ("voice.loop.same_root_cause", "same"),
    ("voice.completion.claims_done", "no"),
    ("voice.research.version_sensitive", "no"),
    ("adv.case.intent_class", "persistence"),
    ("sem.support.verdict", "supported"),
)

#: Score questions in these groups read the top of their legend by default.
SCORE_DEFAULT = 3.0


def _weights(options, chosen: str) -> dict[str, float]:
    """A distribution over ``options`` with ``chosen`` the argmax (sum == 1)."""
    keys = [str(key) for key in options]
    others = [key for key in keys if key != chosen]
    share = 0.02 / len(others) if others else 0.0
    payload = {key: share for key in others}
    payload[str(chosen)] = 1.0 - sum(payload.values())
    return payload


class FixtureJevEvaluator:
    """Deterministic, offline :class:`SystemOneEvaluator`.

    ``answers`` overrides single questions by their exact instantiated id;
    ``error`` makes the evaluator fail the way a provider would (e.g.
    :class:`~skaldir.system_one.SystemOneBlockedExternal`), which is how the
    ``blocked`` path is exercised without any network.
    """

    provider = PROVIDER_FIXTURE
    provider_model = MODEL_FIXTURE
    provider_access_mode = ACCESS_FIXTURE
    live_jev = False

    def __init__(
        self,
        answers: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.answers: dict[str, dict] = {
            str(key): dict(value) for key, value in dict(answers or {}).items()
        }
        self.error = error
        self.calls: list[dict] = []

    # -- payload selection -------------------------------------------------- #

    def _payload(self, question) -> dict:
        override = self.answers.get(question.id)
        if override is not None:
            return dict(override)
        if question.kind == "noul":
            for suffix, value in NOUL_DEFAULTS:
                if question.id.endswith(suffix):
                    return {"value": float(value)}
            return {"value": 0.5}
        if question.kind == "choice":
            chosen = ""
            for suffix, value in CHOICE_DEFAULTS:
                if question.id.endswith(suffix) and value in question.options:
                    chosen = value
                    break
            if not chosen:
                chosen = str(question.options[0])
            return {
                "choice": chosen,
                "probabilities": _weights(question.options, chosen),
                "confidence": 0.8,
            }
        legend = tuple(float(value) for value in question.legend) or (0.0,)
        score = min(SCORE_DEFAULT, max(legend))
        options = [f"{value:g}" for value in legend]
        return {
            "score": score,
            "legend": list(legend),
            "probabilities": _weights(options, f"{score:g}"),
            "confidence": 0.8,
        }

    def answers_for(self, questions) -> dict[str, Answer]:
        """The full answer map for one question set — validated, not assumed."""
        built: dict[str, Answer] = {}
        for question in questions.questions:
            payload = self._payload(question)
            built[question.id] = parse_answer(
                question_id=question.id, kind=question.kind, payload=payload
            )
        return built

    # -- the protocol ------------------------------------------------------- #

    async def evaluate(
        self,
        *,
        state: SystemOneState,
        questions,
        context,
    ) -> SystemOneEvaluation:
        self.calls.append(
            {
                "questions": tuple(questions.ids),
                "state_fingerprint": state.fingerprint,
                "run_id": context.run_id,
                "caller": context.caller,
            }
        )
        if self.error is not None:
            raise self.error
        answers = self.answers_for(questions)
        return build_evaluation(
            context=context,
            questions=questions,
            state=state,
            provider=self.provider,
            provider_model=self.provider_model,
            access_mode=self.provider_access_mode,
            answers=answers,
            live_jev=False,
            usage={"input_tokens": 0, "output_tokens": 0},
            attempts=1,
        )


class SilentFixtureEvaluator(FixtureJevEvaluator):
    """Answers nothing — the "provider returned no answer" contract-error path."""

    def answers_for(self, questions) -> dict[str, Answer]:  # noqa: ARG002
        return {}


def blocked_evaluator(reason: str = "no_authorized_credential") -> FixtureJevEvaluator:
    """An evaluator that fails like the live adapter does without a credential."""
    return FixtureJevEvaluator(
        error=SystemOneBlockedExternal(
            {
                "state": "blocked",
                "reason": reason,
                "provider": PROVIDER_FIXTURE,
                "access_mode": "blocked_external_access",
            }
        )
    )


__all__ = [
    "CHOICE_DEFAULTS",
    "MODEL_FIXTURE",
    "NOUL_DEFAULTS",
    "PROVIDER_FIXTURE",
    "FixtureJevEvaluator",
    "SilentFixtureEvaluator",
    "blocked_evaluator",
]
