"""Decision context — fresh, bounded, source-traceable context per voice decision.

Every voice decision (hint, redirect, completion check) is built from a small
context assembled **fresh** from the persistent stores: pact/constraints first,
then targeted evidence. Each section has a hard character quota; anything cut
is marked explicitly (``…[truncated]``) so a summary never silently becomes the
whole truth. Every evidence line carries its source ref (step, signature,
note id) — facts stay traceable back to their origin.

The context is reconstructible after a restart because it is derived purely
from the event store and memory stores (no in-memory-only state).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .events import EventStore
from .pact import Pact

DEFAULT_TOTAL_BUDGET = 6000

DEFAULT_QUOTAS = {
    "pact": 700,
    "acceptance": 500,
    "recent": 2200,
    "errors": 700,
    "evidence": 900,
    "skills": 800,
    "state": 300,
}

# Role profiles (spec §3): the watcher runs small and cheap on every signal;
# a full investigation gets a fresh, larger window — never a growing thread.
ROLE_PROFILES: dict[str, dict] = {
    "watcher": {
        "total_budget": 2500,
        "recent_limit": 10,
        "quotas": {
            "pact": 500,
            "acceptance": 300,
            "recent": 900,
            "errors": 500,
            "evidence": 500,
            "skills": 300,
            "state": 200,
        },
    },
    "investigator": {
        "total_budget": 4000,
        "recent_limit": 24,
        "quotas": dict(DEFAULT_QUOTAS),
    },
}


def _clip(text: str, quota: int) -> tuple[str, bool]:
    if len(text) <= quota:
        return text, False
    return text[:quota] + f"\n…[truncated: {len(text) - quota} chars]", True


def _enforce_budget(context: DecisionContext) -> None:
    """Shrink non-priority sections until the total budget holds.

    Priority (high → low): pact, acceptance are protected; the largest of the
    remaining sections is shrunk first. Anything cut keeps an explicit marker.
    """
    overflow = context.total_chars - context.budget
    if overflow <= 0:
        return
    protected = {"pact", "acceptance"}
    candidates = [
        section
        for section in context.sections
        if section.name not in protected and section.text
    ]
    candidates.sort(key=lambda section: len(section.text), reverse=True)
    for section in candidates:
        if overflow <= 0:
            break
        keep = max(0, len(section.text) - overflow - 40)
        if keep < 40:
            keep = 0
        removed = len(section.text) - keep
        if removed <= 0:
            continue
        marker = "\n…[truncated: budget]"
        section.text = section.text[:keep] + marker
        section.truncated = True
        overflow -= removed


@dataclass
class Section:
    name: str
    text: str
    quota: int
    truncated: bool = False


@dataclass
class DecisionContext:
    sections: list[Section] = field(default_factory=list)
    budget: int = DEFAULT_TOTAL_BUDGET

    @property
    def total_chars(self) -> int:
        return sum(len(section.text) for section in self.sections)

    def truncated_sections(self) -> list[str]:
        return [section.name for section in self.sections if section.truncated]

    def stats(self) -> dict:
        """Context accounting for instrumentation (spec §2/§20).

        MEASURED: characters actually used, per-section sizes, truncations.
        ESTIMATED: token blow-up (chars/4) — clearly separated, never mixed.
        """
        measured = {
            "budget_chars": self.budget,
            "used_chars": self.total_chars,
            "sections": {section.name: len(section.text) for section in self.sections},
            "truncated_sections": self.truncated_sections(),
        }
        estimated = {"tokens": round(self.total_chars / 4), "method": "chars/4"}
        return {"measured": measured, "estimated": estimated}

    def render(self) -> str:
        parts: list[str] = []
        for section in self.sections:
            if not section.text.strip():
                continue
            parts.append(f"## {section.name}\n{section.text}")
        return "\n\n".join(parts)


def _event_line(event) -> str:
    """One compact, traceable line per event."""
    payload = event.payload or {}
    if event.kind == "tool_call":
        name = str(payload.get("name") or "")
        args = str(payload.get("args") or "")[:140]
        return f"step {event.step}: call {name} {args}"
    if event.kind == "tool_result":
        if payload.get("error"):
            return f"step {event.step}: result ERR {str(payload.get('error'))[:140]}"
        code = payload.get("exit_code")
        out = str(payload.get("stdout") or "")[:120]
        exit_note = f" (exit {code})" if code not in (None, 0) else ""
        return f"step {event.step}: result ok{exit_note} {out}".rstrip()
    if event.kind == "file_edit":
        return f"step {event.step}: edit {payload.get('path')}"
    if event.kind == "claim":
        return f"step {event.step}: CLAIM {str(payload.get('text'))[:140]}"
    if event.kind in ("pause_delivered", "pause_requested", "research_done"):
        return f"step {event.step}: {event.kind} {str(payload)[:120]}"
    if event.kind == "thought":
        return f"step {event.step}: thought {str(payload.get('text'))[:120]}"
    return ""


class ContextBuilder:
    """Assembles one bounded decision context from the persistent stores."""

    def __init__(
        self,
        *,
        total_budget: int = DEFAULT_TOTAL_BUDGET,
        quotas: dict[str, int] | None = None,
        recent_limit: int = 24,
    ) -> None:
        self.total_budget = total_budget
        self.quotas = dict(DEFAULT_QUOTAS if quotas is None else quotas)
        self.recent_limit = recent_limit

    def build(
        self,
        store: EventStore,
        session_id: str,
        *,
        step: int | None = None,
        query: str = "",
        pact: Pact | None = None,
        incidents=None,
        notes=None,
        skills_text: str = "",
        role: str | None = None,
    ) -> DecisionContext:
        profile = ROLE_PROFILES.get(role or "")
        budget = profile["total_budget"] if profile else self.total_budget
        quotas = dict(profile["quotas"]) if profile else self.quotas
        recent_limit = profile["recent_limit"] if profile else self.recent_limit

        sections: list[Section] = []

        def add(name: str, text: str) -> None:
            quota = quotas.get(name, 600)
            clipped, truncated = _clip(text, quota)
            sections.append(Section(name=name, text=clipped, quota=quota, truncated=truncated))

        if pact is not None:
            pact_lines = [f"Goal: {pact.goal}"]
            if pact.constraints:
                pact_lines.append("Constraints: " + "; ".join(pact.constraints))
            if pact.non_goals:
                pact_lines.append("Non-Goals: " + "; ".join(pact.non_goals))
            add("pact", "\n".join(pact_lines))
            if pact.acceptance:
                add(
                    "acceptance",
                    "\n".join(f"[{c.id}] {c.text}" for c in pact.acceptance),
                )

        events = store.iter_session(session_id)
        recent = [line for line in (_event_line(e) for e in events[-recent_limit:]) if line]
        add("recent", "\n".join(recent))

        errors: dict[str, dict] = {}
        for event in events:
            if event.kind != "tool_result":
                continue
            from .monitor import error_fingerprint, error_text

            text = error_text(event.payload)
            if text:
                entry = errors.setdefault(
                    error_fingerprint(text), {"count": 0, "text": text[:140]}
                )
                entry["count"] += 1
        if errors:
            top = sorted(errors.items(), key=lambda item: -item[1]["count"])[:3]
            add(
                "errors",
                "\n".join(
                    f"[{fp}] {item['count']}× {item['text']}" for fp, item in top
                ),
            )

        evidence_lines: list[str] = []
        if query:
            if incidents is not None:
                try:
                    for incident, _score in incidents.search_text(query, limit=4):
                        evidence_lines.append(
                            f"incident[{incident.signature}] {incident.to_evidence_text()[:220]}"
                        )
                except Exception:  # nosec B110 - best-effort lookup; section stays empty
                    pass
            if notes is not None:
                try:
                    for note, _score in notes.search_text(query, limit=4):
                        evidence_lines.append(
                            f"note#{note.id} (untrusted, trust={note.trust:.1f}) "
                            f"{note.to_evidence_text()[:220]}"
                        )
                except Exception:  # nosec B110 - best-effort lookup; section stays empty
                    pass
        add("evidence", "\n".join(evidence_lines))
        add("skills", skills_text)
        state_lines = [f"session={session_id} step={step if step is not None else len(events)}"]
        kinds: dict[str, int] = {}
        for event in events:
            kinds[event.kind] = kinds.get(event.kind, 0) + 1
        state_lines.append(
            "kinds: " + ", ".join(f"{k}×{v}" for k, v in sorted(kinds.items())[:12])
        )
        if errors:
            state_lines.append(f"error_fingerprints={len(errors)}")
        add("state", "\n".join(state_lines))

        context = DecisionContext(sections=sections, budget=budget)
        _enforce_budget(context)
        return context
