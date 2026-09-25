"""The Pact — the task contract (goals, non-goals, acceptance, budgets).

See docs/CORE-PLAN.md §1.2. A pact is created at task start, confirmed by the
owner, and is the yardstick for scope/slop checks (monitor.py) and the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _criterion_text(value: Any) -> str:
    """Criterion text as a plain string.

    A nested mapping is never a criterion text: the previous ``str(value)``
    leaked a dict repr into the contract (live-found 13.09.: criteria showed
    up as "{'id': 'c1', …}" in the chat). Take the inner ``text`` instead.
    """

    if isinstance(value, dict):
        return _criterion_text(value.get("text"))
    return str(value if value is not None else "").strip()


@dataclass
class AcceptanceCriterion:
    """One verifiable acceptance criterion of the task."""

    id: str
    text: str


@dataclass
class Pact:
    """Task contract. ``validate()`` must pass before a pact is used."""

    goal: str
    acceptance: list[AcceptanceCriterion] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    scope_paths: list[str] = field(default_factory=list)
    version: int = 1

    def validate(self) -> None:
        if not self.goal.strip():
            raise ValueError("pact.goal must not be empty")
        if not self.acceptance:
            raise ValueError("pact needs at least one acceptance criterion")
        seen: set[str] = set()
        for criterion in self.acceptance:
            if not criterion.id.strip():
                raise ValueError("acceptance criterion id must not be empty")
            if criterion.id in seen:
                raise ValueError(f"duplicate acceptance criterion id: {criterion.id}")
            seen.add(criterion.id)
            if not criterion.text.strip():
                raise ValueError(f"acceptance criterion {criterion.id} has no text")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "goal": self.goal,
            "acceptance": [{"id": c.id, "text": c.text} for c in self.acceptance],
            "non_goals": list(self.non_goals),
            "constraints": list(self.constraints),
            "budget": dict(self.budget),
            "scope_paths": list(self.scope_paths),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Pact:
        raw = data.get("acceptance") or []
        acceptance: list[AcceptanceCriterion] = []
        for index, item in enumerate(raw, 1):
            if isinstance(item, str):
                acceptance.append(AcceptanceCriterion(id=f"ac{index}", text=item))
            elif isinstance(item, dict):
                cid = str(item.get("id") or f"ac{index}")
                acceptance.append(
                    AcceptanceCriterion(id=cid, text=_criterion_text(item.get("text")))
                )
            else:
                raise ValueError(f"acceptance[{index}] must be a string or mapping")
        return cls(
            goal=str(data.get("goal", "")),
            acceptance=acceptance,
            non_goals=[str(x) for x in data.get("non_goals") or []],
            constraints=[str(x) for x in data.get("constraints") or []],
            budget=dict(data.get("budget") or {}),
            scope_paths=[str(x) for x in data.get("scope_paths") or []],
            version=int(data.get("version", 1)),
        )

    def to_yaml(self, path: str | Path) -> None:
        text = yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True)
        Path(path).write_text(text, encoding="utf-8")

    @classmethod
    def from_yaml(cls, path: str | Path) -> Pact:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("pact file must contain a YAML mapping")
        return cls.from_dict(data)

    @classmethod
    def example(cls) -> Pact:
        """Small example pact used by ``skaldir init`` and the demo."""
        return cls(
            goal="Add cross-validation to the training script",
            acceptance=[
                AcceptanceCriterion("ac1", "python train.py --cv 5 runs without errors"),
                AcceptanceCriterion("ac2", "suite/cv_test.py is green; metrics are in the log"),
            ],
            non_goals=["no refactor of the data loaders", "no new dependencies"],
            constraints=["ruff clean", "no cloud upload"],
            budget={"tokens": 150000, "time_min": 60, "money_eur": 0},
            scope_paths=["src/**", "suite/**"],
        )
