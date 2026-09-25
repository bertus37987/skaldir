"""Voice v2 core — LLM triage & synthesis on top of retrieval evidence.

Thin typed wrappers around the versioned prompts (``voice_triage``,
``voice_synthesis``) with **strict fallbacks**: on any parse or client error
these functions return ``None`` so the caller keeps the deterministic
template hint (precision over recall — a failing LLM must never break the
loop or silently drop a warning).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .llm import BudgetTracker, LLMClient
from .promptrun import run_prompt
from .prompts import PromptRegistry


@dataclass
class TriageResult:
    stuck: bool
    category: str
    confidence: float
    search_queries: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass
class SynthesisResult:
    hint_md: str
    confidence: float
    evidence_ids: list[str] = field(default_factory=list)
    verify: str = ""


def triage_step(
    client: LLMClient,
    registry: PromptRegistry,
    *,
    pact_excerpt: str,
    event_digest: str,
    budget: BudgetTracker | None = None,
) -> TriageResult | None:
    """LLM triage: is the agent stuck? Returns None on any failure."""
    try:
        result = run_prompt(
            client,
            registry,
            "voice_triage",
            {"pact_excerpt": pact_excerpt, "event_digest": event_digest},
            budget=budget,
        )
    except Exception:
        return None
    data = result.parsed if isinstance(result.parsed, dict) else {}
    stuck = data.get("stuck")
    if not isinstance(stuck, bool):
        return None  # no explicit verdict → no info (never guess "safe")
    confidence = data.get("confidence")
    confidence = float(confidence) if isinstance(confidence, (int, float)) else 0.0
    queries = [str(q) for q in (data.get("search_queries") or [])][:3]
    return TriageResult(
        stuck=stuck,
        category=str(data.get("category", "none")),
        confidence=min(1.0, max(0.0, confidence)),
        search_queries=queries,
        rationale=str(data.get("rationale", ""))[:300],
    )


def synthesize_hint(
    client: LLMClient,
    registry: PromptRegistry,
    *,
    trigger: str,
    evidence_items: list[str],
    budget: BudgetTracker | None = None,
) -> SynthesisResult | None:
    """LLM synthesis: one evidence-backed hint, or None (decline/failure)."""
    try:
        result = run_prompt(
            client,
            registry,
            "voice_synthesis",
            {
                "trigger": trigger,
                "evidence_items": "\n".join(evidence_items) or "(no evidence)",
            },
            budget=budget,
        )
    except Exception:
        return None
    data = result.parsed if isinstance(result.parsed, dict) else {}
    hint = data.get("hint_md")
    if not isinstance(hint, str) or not hint.strip():
        return None
    confidence = data.get("confidence")
    confidence = float(confidence) if isinstance(confidence, (int, float)) else 0.5
    evidence_ids = [str(item) for item in (data.get("evidence_ids") or [])]
    return SynthesisResult(
        hint_md=hint.strip(),
        confidence=min(1.0, max(0.0, confidence)),
        evidence_ids=evidence_ids,
        verify=str(data.get("verify", ""))[:200],
    )
