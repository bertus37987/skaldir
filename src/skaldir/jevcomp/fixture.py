"""Fixture evaluators for the Jev compaction engine (port-only helper).

Nothing here talks to a network: these askers answer from a fixture, a replay map
or a probability function. They exist because the ported algorithm must be
exercisable without a provider — by the test suite, by the CLI's fixture mode and
by shadow runs — exactly as the upstream test suite injects a fake ``JevAsker``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .errors import JevAnswerError
from .types import JevResponse

#: One observed request, so a test (or a shadow run) can assert what was asked.
AskRecord = tuple[dict[str, Any], list[str]]


def noul_answers(probabilities: Mapping[str, float]) -> dict[str, dict[str, Any]]:
    """Build an answer set from ``{question_name: probability}``."""
    answers: dict[str, dict[str, Any]] = {}
    for name, probability in probabilities.items():
        if isinstance(probability, bool) or not isinstance(probability, (int, float)):
            raise JevAnswerError(f"Invalid Jev answer for {name}")
        answers[name] = {"type": "noul", "noul": float(probability)}
    return answers


@dataclass
class NoulAsker:
    """Answers every question with ``probability(name)``.

    ``seen`` collects one :data:`AskRecord` per request in call order, so tests
    can prove the batching, the pinned calls and the resend of the full state.
    """

    probability: Callable[[str], float]
    seen: list[AskRecord] = field(default_factory=list)
    model: str | None = None

    def ask(self, state: dict[str, Any], questions: dict[str, Any]) -> JevResponse:
        self.seen.append((state, list(questions.keys())))
        return JevResponse(
            answers=noul_answers(
                {name: self.probability(name) for name in questions},
            ),
            model=self.model,
        )


def noul_asker(
    probability: Callable[[str], float], seen: list[AskRecord] | None = None
) -> NoulAsker:
    """A :class:`~skaldir.jevcomp.types.JevAsker` answering from a function."""
    return NoulAsker(probability=probability, seen=[] if seen is None else seen)


@dataclass
class ReplayAsker:
    """Answers from a fixed ``{question_name: probability}`` map."""

    replay: Mapping[str, float]
    default: float | None = None
    seen: list[AskRecord] = field(default_factory=list)

    def ask(self, state: dict[str, Any], questions: dict[str, Any]) -> JevResponse:
        self.seen.append((state, list(questions.keys())))
        probabilities: dict[str, float] = {}
        for name in questions:
            if name in self.replay:
                probabilities[name] = float(self.replay[name])
            elif self.default is not None:
                probabilities[name] = float(self.default)
            else:
                raise JevAnswerError(f"Invalid Jev answer for {name}")
        return JevResponse(answers=noul_answers(probabilities))


def replay_asker(replay: Mapping[str, float], *, default: float | None = None) -> ReplayAsker:
    """A :class:`~skaldir.jevcomp.types.JevAsker` answering from a recorded map."""
    return ReplayAsker(replay=dict(replay), default=default)


def constant_asker(probability: float) -> NoulAsker:
    """Answers every question with the same probability."""
    return NoulAsker(probability=lambda _name: probability)


def keep_calls_drop_results() -> NoulAsker:
    """The upstream test stance: keep the calls (0.9), drop the results (0.1)."""
    return NoulAsker(
        probability=lambda name: 0.9 if name.startswith("call_") else 0.1,
    )
