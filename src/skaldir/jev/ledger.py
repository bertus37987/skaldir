"""The Jev decision ledger — what Jev decided, and which fallback fired (§7.1).

One JSON object per line in ``<state>/jev_decisions.jsonl``, appended on every
``decide`` that is not a ``--dry-run``. It reuses the event store's JSONL pattern
and stores the *decision document* the CLI already printed, so the ledger and
stdout can never disagree about what happened.

Nothing secret is written: the document is the ``JevDecision`` payload, which by
construction carries the request's ids, revisions, status, provider labelling,
the typed answer and the fallback — never a state payload, never a credential.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TextIO

LEDGER_NAME = "jev_decisions.jsonl"


class DecisionLedger:
    """Append-only decision history in one JSONL file."""

    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / LEDGER_NAME
        self._handle: TextIO | None = None

    def append(self, document: dict) -> str:
        """Append one decision document; returns the ledger path."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self._handle is None:
            self._handle = self.path.open("a", encoding="utf-8")
        self._handle.write(json.dumps(document, ensure_ascii=False, sort_keys=True, default=str))
        self._handle.write("\n")
        self._handle.flush()
        return str(self.path)

    def read(self, *, run_id: str = "", limit: int = 0) -> list[dict]:
        """The recorded decisions, newest first, optionally filtered by run."""
        if not self.path.is_file():
            return []
        rows: list[dict] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    if run_id and str(row.get("run_id", "")) != run_id:
                        continue
                    rows.append(row)
        except OSError:
            return []
        rows.reverse()
        return rows[: int(limit)] if limit and int(limit) > 0 else rows

    def history(self, *, run_id: str = "", limit: int = 20) -> list[dict]:
        """A compact view of :meth:`read` — what a reader of ``jev history`` needs."""
        compact = []
        for row in self.read(run_id=run_id, limit=limit):
            answer = row.get("answer") or {}
            compact.append(
                {
                    "decision_id": row.get("decision_id", ""),
                    "kind": row.get("kind", ""),
                    "status": row.get("status", ""),
                    "provider_mode": row.get("provider_mode", ""),
                    "confidence": row.get("confidence", 0.0),
                    "fallback": row.get("fallback", ""),
                    "refusal_reason": row.get("refusal_reason", ""),
                    "caution_level": row.get("caution_level", 0),
                    "run_id": row.get("run_id", ""),
                    "goal_id": row.get("goal_id", ""),
                    "goal_revision": row.get("goal_revision", 0),
                    "created_at": row.get("created_at", 0.0),
                    "answer_kind": answer.get("kind", ""),
                }
            )
        return compact

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> DecisionLedger:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


__all__ = ["LEDGER_NAME", "DecisionLedger"]
