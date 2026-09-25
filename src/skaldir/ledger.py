"""The Ledger — continuity state that survives context boundaries.

See docs/CORE-PLAN.md §1.3. Compact (soft budget ~2k tokens), machine- and
human-readable. Invariant: no "done" without proof — a criterion can only
become ``evidenced`` with a non-empty evidence note.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .pact import Pact

STATUS_OPEN = "open"
STATUS_IN_PROGRESS = "in_progress"
STATUS_EVIDENCED = "evidenced"
STATUS_AT_RISK = "at_risk"
VALID_STATUS = {STATUS_OPEN, STATUS_IN_PROGRESS, STATUS_EVIDENCED, STATUS_AT_RISK}

TOKEN_GUARD = 2000  # soft budget for the rendered ledger (plan §1.3)

_STATUS_ICON = {
    STATUS_OPEN: "○",
    STATUS_IN_PROGRESS: "◐",
    STATUS_EVIDENCED: "☑",
    STATUS_AT_RISK: "⚠",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class CriterionState:
    status: str = STATUS_OPEN
    evidence: str = ""


@dataclass
class Ledger:
    goal: str
    criteria: dict[str, CriterionState] = field(default_factory=dict)
    criterion_texts: dict[str, str] = field(default_factory=dict)
    decisions: list[dict[str, str]] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    artifacts: list[dict[str, str]] = field(default_factory=list)
    test_status: str = "unknown"
    next_steps: list[str] = field(default_factory=list)
    updated_at: str = ""
    # CTX-01: original references survive context switches
    evidence_refs: list[str] = field(default_factory=list)
    knowledge_refs: list[str] = field(default_factory=list)

    @classmethod
    def from_pact(cls, pact: Pact) -> Ledger:
        return cls(
            goal=pact.goal,
            criteria={c.id: CriterionState() for c in pact.acceptance},
            criterion_texts={c.id: c.text for c in pact.acceptance},
            updated_at=_now(),
        )

    # -- mutations ---------------------------------------------------------
    def set_status(self, criterion_id: str, status: str, evidence: str | None = None) -> None:
        if criterion_id not in self.criteria:
            raise KeyError(f"unknown criterion: {criterion_id}")
        if status not in VALID_STATUS:
            raise ValueError(f"invalid status: {status}")
        state = self.criteria[criterion_id]
        if status == STATUS_EVIDENCED:
            note = (evidence or state.evidence).strip()
            if not note:
                raise ValueError("evidenced requires an evidence note (no 'done' without proof)")
            state.evidence = note
        state.status = status
        self.updated_at = _now()

    def add_decision(self, text: str, reason: str = "") -> None:
        self.decisions.append({"text": text, "reason": reason, "ts": _now()})
        self.updated_at = _now()

    def add_question(self, question: str) -> None:
        self.open_questions.append(question)
        self.updated_at = _now()

    def add_artifact(self, path: str, kind: str = "file", note: str = "") -> None:
        self.artifacts.append({"path": path, "kind": kind, "note": note})
        self.updated_at = _now()

    def set_test_status(self, status: str) -> None:
        self.test_status = status
        self.updated_at = _now()

    def set_next_steps(self, steps: list[str]) -> None:
        self.next_steps = list(steps)
        self.updated_at = _now()

    # -- queries -----------------------------------------------------------
    def completion(self) -> tuple[int, int]:
        total = len(self.criteria)
        done = sum(1 for s in self.criteria.values() if s.status == STATUS_EVIDENCED)
        return done, total

    def is_complete(self) -> bool:
        done, total = self.completion()
        return total > 0 and done == total

    def size_tokens_estimate(self) -> int:
        return len(self.render_markdown()) // 4

    # -- rendering / (de)serialization --------------------------------------
    def render_markdown(self) -> str:
        lines = ["# Ledger", "", f"**Goal:** {self.goal}", "", "## Criteria"]
        for criterion_id, state in self.criteria.items():
            icon = _STATUS_ICON.get(state.status, "?")
            text = self.criterion_texts.get(criterion_id, "")
            line = f"- {icon} [{criterion_id}] {text} — {state.status}"
            if state.evidence:
                line += f" · Evidence: {state.evidence}"
            lines.append(line)
        done, total = self.completion()
        lines += ["", f"**Progress:** {done}/{total} backed"]
        if self.decisions:
            lines += ["", "## Entscheidungen"]
            for item in self.decisions:
                entry = item.get("text", "")
                if item.get("reason"):
                    entry += f" — {item['reason']}"
                lines.append(f"- {entry}")
        if self.open_questions:
            lines += ["", "## Offene Fragen"]
            lines += [f"- {q}" for q in self.open_questions]
        if self.artifacts:
            lines += ["", "## Artefakte"]
            for art in self.artifacts:
                note = f" {art['note']}" if art.get("note") else ""
                lines.append(f"- {art.get('kind', 'file')}: {art.get('path', '')}{note}")
        lines += ["", f"**Tests:** {self.test_status}"]
        if self.next_steps:
            lines += ["", "## Next steps"]
            lines += [f"- {s}" for s in self.next_steps]
        lines += ["", f"_updated: {self.updated_at or '—'}_"]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "criteria": {
                cid: {"status": s.status, "evidence": s.evidence}
                for cid, s in self.criteria.items()
            },
            "criterion_texts": dict(self.criterion_texts),
            "decisions": list(self.decisions),
            "open_questions": list(self.open_questions),
            "artifacts": list(self.artifacts),
            "test_status": self.test_status,
            "next_steps": list(self.next_steps),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ledger:
        criteria = {
            str(cid): CriterionState(
                status=str(state.get("status", STATUS_OPEN)),
                evidence=str(state.get("evidence", "")),
            )
            for cid, state in (data.get("criteria") or {}).items()
        }
        return cls(
            goal=str(data.get("goal", "")),
            criteria=criteria,
            criterion_texts={
                str(key): str(value)
                for key, value in (data.get("criterion_texts") or {}).items()
            },
            decisions=list(data.get("decisions") or []),
            open_questions=[str(x) for x in data.get("open_questions") or []],
            artifacts=list(data.get("artifacts") or []),
            test_status=str(data.get("test_status", "unknown")),
            next_steps=[str(x) for x in data.get("next_steps") or []],
            updated_at=str(data.get("updated_at", "")),
        )

    def save(self, path: str | Path) -> None:
        text = yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True)
        Path(path).write_text(text, encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> Ledger:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("ledger file must contain a YAML mapping")
        return cls.from_dict(data)
