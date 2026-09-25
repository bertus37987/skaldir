"""Evidence retrieval across incident + note memory (CORE-PLAN §2).

Returns ranked ``Evidence`` items for Voice hints. Evidence derived from
external sources (research notes) is marked ``untrusted`` and rendered
through ``spotlight()`` so retrieved content can never masquerade as
instructions (SECURITY T2). Archived memory is never surfaced.

``judge_same_problem`` adds the LLM verdict on top of exact/FTS hits
(``same_problem_judge`` prompt); without a client, callers rely on the
deterministic hits only.
"""

from __future__ import annotations

from dataclasses import dataclass

from .incidents import STATUS_ARCHIVED, IncidentStore
from .llm import BudgetTracker, LLMClient
from .memory import Applicability, RetrievalContext
from .notes import NoteStore
from .promptrun import run_prompt
from .prompts import PromptRegistry
from .spotlight import spotlight


@dataclass
class Evidence:
    ref: str
    text: str
    trust: float = 0.5
    untrusted: bool = False
    exact: bool = False
    # Block 7: set by ``memory.gate_evidence`` when a RetrievalContext was given.
    # ``None`` means "not checked" — never confuse that with "checked and strong".
    applicability: Applicability | None = None

    def render(self) -> str:
        if self.untrusted:
            return spotlight(self.text, source=self.ref).replace("\n", " ")
        return self.text


def find_evidence(
    incidents: IncidentStore | None = None,
    notes: NoteStore | None = None,
    *,
    signature: str | None = None,
    query: str | None = None,
    limit: int = 3,
    context: RetrievalContext | None = None,
) -> list[Evidence]:
    """Collect evidence: exact signature match first, then text search.

    Without ``context`` this is the original retrieval step (unchanged). With a
    ``memory.RetrievalContext`` the result is additionally filtered through the
    Block 7 applicability gate — rejected entries are dropped and every kept
    entry carries an ``applicability`` verdict, so a weak match can never be
    mistaken for a strong one at this boundary.
    """
    found: list[Evidence] = []
    if incidents is not None and signature:
        hit = incidents.get(signature)
        if hit is not None and hit.status != STATUS_ARCHIVED:
            found.append(
                Evidence(
                    ref=f"incident#{hit.id}",
                    text=hit.to_evidence_text(),
                    trust=hit.confidence,
                    exact=True,
                )
            )
    if incidents is not None and query:
        for incident, _score in incidents.search_text(query, limit=limit):
            found.append(
                Evidence(
                    ref=f"incident#{incident.id}",
                    text=incident.to_evidence_text(),
                    trust=incident.confidence,
                )
            )
    if notes is not None and query:
        for note, _score in notes.search_text(query, limit=limit):
            found.append(
                Evidence(
                    ref=f"note#{note.id}",
                    text=note.to_evidence_text(),
                    trust=note.trust,
                    untrusted=True,
                )
            )
    unique: list[Evidence] = []
    seen: set[str] = set()
    for item in found:
        if item.ref not in seen:
            seen.add(item.ref)
            unique.append(item)
    unique = unique[:limit]
    if context is not None:
        from .memory import gate_evidence  # local import: avoids an import cycle

        unique = gate_evidence(unique, context=context, incidents=incidents)
    return unique


def judge_same_problem(
    problem_text: str,
    candidates: list[Evidence],
    *,
    client: LLMClient | None = None,
    registry: PromptRegistry | None = None,
    budget: BudgetTracker | None = None,
) -> str | None:
    """Ask the LLM whether the current problem matches a stored incident.

    Returns the ``incident#id`` ref of the confirmed match, or ``None``.
    Requires a client/registry; without them, callers rely on exact/FTS hits.
    """
    if client is None or registry is None or not candidates:
        return None
    listing = "\n".join(f"- {item.ref}: {item.text}" for item in candidates)
    result = run_prompt(
        client,
        registry,
        "same_problem_judge",
        {"current_problem": problem_text, "candidates": listing},
        budget=budget,
    )
    data = result.parsed if isinstance(result.parsed, dict) else {}
    if data.get("same") is not True:
        return None
    ref = str(data.get("incident_id", ""))
    for item in candidates:
        if item.ref == ref:
            return ref
    return None
