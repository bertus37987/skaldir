"""Night-shift consolidation — turn finished episodes into durable memory.

V0.5 scope: deterministic episode summary + incident proposals. Dry-run is
the default (``apply=False``): nothing is written unless the caller opts in,
and written incidents start as ``candidate`` (propose-first). Note extraction
follows with research events in V1.

§1.5 (wave 1): the offline consolidation is also the production entry of the
trace consumer — ``consolidate_traces`` drains completed executions from
``events.db`` into capability candidates (Scanned/New/Candidates, dry-run
aware). The live hook point is deliberately still open: the plugin's
``on_session_end`` (``integrations/hermes/godsv_plugin/__init__.py``) will call
this step in wave 2 — it belongs to the host-wiring block, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .events import EventStore
from .incidents import Incident, IncidentStore, make_provenance
from .llm import BudgetTracker, LLMClient
from .monitor import Monitor, classify_tool_error, error_fingerprint, error_text
from .notes import NoteStore, ResearchNote
from .promptrun import run_prompt
from .prompts import PromptRegistry


@dataclass
class EpisodeSummary:
    session_id: str
    events: int
    kinds: dict[str, int] = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)
    signals: list[dict] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    final_thought: str = ""

    @property
    def top_error(self) -> dict | None:
        return self.errors[0] if self.errors else None

    def to_text(self) -> str:
        lines = [f"Session: {self.session_id}", f"Events: {self.events}"]
        if self.kinds:
            lines.append("Kinds: " + ", ".join(f"{k}×{v}" for k, v in sorted(self.kinds.items())))
        if self.errors:
            lines.append("Errors (most frequent first):")
            for item in self.errors[:3]:
                lines.append(f"- {item['count']}× [{item['fingerprint']}] {item['error'][:160]}")
        if self.signals:
            lines.append("Signals: " + "; ".join(f"{s['kind']}" for s in self.signals[:6]))
        if self.files_touched:
            lines.append("Files: " + ", ".join(self.files_touched[:8]))
        if self.final_thought:
            lines.append(f"Final thought: {self.final_thought[:300]}")
        return "\n".join(lines)


@dataclass
class ConsolidationResult:
    session_id: str
    applied: bool
    proposals: list[Incident] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # §1.5: the trace-consumption step's report (Scanned/New/Candidates).
    # Empty when the caller had no runtime to derive (no incident store given).
    traces: dict = field(default_factory=dict)


def consolidate_traces(
    runtime: str | Path,
    *,
    apply: bool = False,
    limit: int = 2000,
    min_occurrences: int = 2,
) -> dict:
    """Trace-mining step of the offline consolidation (§1.5).

    Drains completed executions from ``<runtime>/events.db`` through the
    persistent cursor into capability candidates and answers with the
    Scanned/New/Candidates numbers. Dry-run default, like the rest of
    nightshift: without ``apply`` nothing is written (no cursor, no candidate)
    and the numbers describe what an applied run would do.
    """
    from .capability_mining import consume_completed_traces

    report = consume_completed_traces(
        runtime, limit=limit, min_occurrences=min_occurrences, apply=apply
    )
    return {
        "scanned": int(report["scanned"]),
        "new": int(report["new"]),
        "candidates": int(report["candidates"]),
        "redacted": int(report["redacted"]),
        "processed_seq": int(report["processed_seq"]),
        "cursor": str(report["cursor"]),
        "applied": bool(apply),
    }


def build_episode_summary(store: EventStore, session_id: str) -> EpisodeSummary:
    """Deterministic summary of a finished session (no LLM)."""
    events = store.iter_session(session_id)
    monitor = Monitor()  # no pact: scope checks are per-task, not per-episode
    kinds: dict[str, int] = {}
    errors: dict[str, dict] = {}
    signals: list[dict] = []
    files: list[str] = []
    final_thought = ""
    for event in events:
        kinds[event.kind] = kinds.get(event.kind, 0) + 1
        for signal in monitor.ingest(event):
            signals.append(
                {
                    "kind": signal.kind,
                    "severity": signal.severity,
                    "fingerprint": signal.fingerprint,
                    "message": signal.message,
                }
            )
        if event.kind == "tool_result":
            text = error_text(event.payload)
            if text:
                fingerprint = error_fingerprint(text)
                entry = errors.setdefault(
                    fingerprint,
                    {
                        "fingerprint": fingerprint,
                        "error": text[:200],
                        "count": 0,
                        # Block 7: first sighting step + classified failure class
                        # make an incident proposal traceable and comparable.
                        "step": event.step,
                        "failure_class": classify_tool_error(text) or "error",
                    },
                )
                entry["count"] += 1
        if event.kind == "file_edit":
            path = str(event.payload.get("path", ""))
            if path and path not in files:
                files.append(path)
        if event.kind == "thought":
            final_thought = str(event.payload.get("text", ""))[:500]
    ordered_errors = sorted(errors.values(), key=lambda item: -item["count"])
    return EpisodeSummary(
        session_id=session_id,
        events=len(events),
        kinds=kinds,
        errors=ordered_errors,
        signals=signals,
        files_touched=files,
        final_thought=final_thought,
    )


def consolidate(
    summary: EpisodeSummary,
    *,
    client: LLMClient | None = None,
    registry: PromptRegistry | None = None,
    incidents: IncidentStore | None = None,
    apply: bool = False,
    budget: BudgetTracker | None = None,
    runtime: str | Path | None = None,
) -> ConsolidationResult:
    """Propose (and optionally apply) incident records from an episode.

    §1.5: when a runtime is available (given directly or derived from the
    incident store's location — the CLI passes the same directory for both),
    the trace-consumption step runs as part of the consolidation and its
    Scanned/New/Candidates report rides along in ``result.traces``.
    """
    proposals: list[Incident] = []
    top = summary.top_error
    if top is not None:
        existing = incidents.get(top["fingerprint"]) if incidents is not None else None
        if existing is None:
            # Block 7: every proposal carries the learning axes + traceable
            # provenance (which session, when, from which evidence) — an
            # incident without provenance cannot be used as a basis later.
            proposal = Incident(
                signature=top["fingerprint"],
                title=f"recurring error: {top['error'][:70]}",
                kind="error",
                status="candidate",
                confidence=0.3,
                failure_class=str(top.get("failure_class") or "error"),
                evidence_refs=[f"{summary.session_id}: {top['count']}× {top['error'][:120]}"],
                provenance=make_provenance(
                    "nightshift_consolidate",
                    session_id=summary.session_id,
                    step=top.get("step"),
                    event_refs=[
                        f"{summary.session_id}:{top.get('step')}"
                        if top.get("step") is not None
                        else summary.session_id
                    ],
                    source="build_episode_summary",
                ),
            )
            if client is not None and registry is not None:
                result = run_prompt(
                    client,
                    registry,
                    "incident_extract",
                    {"episode_summary": summary.to_text()},
                    budget=budget,
                )
                data = result.parsed if isinstance(result.parsed, dict) else {}
                proposal.title = str(data.get("title") or proposal.title)
                proposal.root_cause = str(data.get("root_cause", ""))
                steps = data.get("resolution_steps")
                if isinstance(steps, list):
                    proposal.resolution_steps = [str(step) for step in steps]
                proposal.verification = str(data.get("verification", ""))
                confidence = data.get("confidence")
                if isinstance(confidence, (int, float)):
                    proposal.confidence = min(1.0, max(0.0, float(confidence)))
            proposals.append(proposal)
    applied = False
    if apply and incidents is not None and proposals:
        for proposal in proposals:
            incidents.upsert(proposal)
        applied = True
    # §1.5: drain the completed executions into capability candidates. The
    # runtime is the directory the ledgers live in — the incident store's
    # parent when the caller did not name one explicitly.
    runtime_dir: Path | None = None
    if runtime is not None:
        runtime_dir = Path(runtime)
    elif incidents is not None and getattr(incidents, "path", ""):
        runtime_dir = Path(str(incidents.path)).parent
    trace_report: dict = {}
    if runtime_dir is not None:
        try:
            trace_report = consolidate_traces(runtime_dir, apply=apply)
        except Exception:
            trace_report = {}  # an audit/reporting step never kills the consolidation
    return ConsolidationResult(
        session_id=summary.session_id,
        applied=applied,
        proposals=proposals,
        traces=trace_report,
    )


def consolidate_research_notes(
    store: EventStore,
    notes: NoteStore,
    session_id: str,
    *,
    apply: bool = False,
    trust: float = 0.6,
    limit: int = 5,
) -> list[ResearchNote]:
    """Turn ``research_done`` findings into atomic research notes (propose-first).

    Deterministic: one note per finding (topic = problem, claim = recommended
    direction), deduped by claim text; a same-topic note with a different
    claim is linked as a contradiction (heuristic, reviewable — the prior note
    is marked ``contradicted``).
    """
    existing = notes.all()
    existing_claims = {note.claim.strip() for note in existing}
    by_topic: dict[str, ResearchNote] = {}
    for note in existing:
        by_topic.setdefault(note.topic.strip().lower(), note)
    proposals: list[ResearchNote] = []
    for event in store.iter_session(session_id):
        if event.kind != "research_done" or len(proposals) >= limit:
            continue
        payload = event.payload or {}
        problem = str(payload.get("problem") or "").strip()
        direction = str(payload.get("direction") or "").strip()
        if not problem or not direction:
            continue
        claim = direction[:500]
        if claim in existing_claims:
            continue
        note = ResearchNote(
            topic=problem[:120],
            claim=claim,
            source_type="research",
            source_url=str(payload.get("archive") or ""),
            trust=trust,
            repo_scope=session_id,
        )
        prior = by_topic.get(problem[:120].strip().lower())
        if prior is not None and prior.claim.strip() != claim:
            note.contradicts = [f"note#{prior.id}"]
        proposals.append(note)
    if apply:
        for note in proposals:
            stored = notes.add(note)
            for ref in note.contradicts:
                if ref.startswith("note#"):
                    try:
                        notes.link_contradiction(int(ref.split("#", 1)[1]), f"note#{stored.id}")
                    except (ValueError, IndexError):
                        pass
    return proposals


def propose_solved_incidents(
    store: EventStore,
    session_id: str,
    incidents: IncidentStore,
    *,
    limit: int = 3,
) -> list[Incident]:
    """Propose incident records for error classes that got solved in-session.

    Deterministic: a fingerprint whose **last** sighting is followed by a
    success of the same tool (and which is not yet known) becomes a candidate
    — review flow decides promotion (propose-first).
    """
    events = list(store.iter_session(session_id))
    last_error: dict[str, int] = {}
    last_error_text: dict[str, str] = {}
    tool_of: dict[str, str] = {}
    successes_by_tool: dict[str, list[int]] = {}
    for event in events:
        if event.kind != "tool_result":
            continue
        tool = str(event.payload.get("tool") or "")
        text = error_text(event.payload)
        if text:
            fingerprint = error_fingerprint(text)
            last_error[fingerprint] = event.step
            last_error_text[fingerprint] = text[:160]
            tool_of[fingerprint] = tool
        else:
            successes_by_tool.setdefault(tool, []).append(event.step)
    proposals: list[Incident] = []
    for fingerprint, step in sorted(last_error.items(), key=lambda item: -item[1]):
        if len(proposals) >= limit:
            break
        if incidents.get(fingerprint) is not None:
            continue
        tool = tool_of.get(fingerprint, "")
        later = [seen for seen in successes_by_tool.get(tool, []) if seen > step]
        if not later:
            continue
        proposals.append(
            Incident(
                signature=fingerprint,
                title=f"Resolved: {last_error_text[fingerprint][:80]}",
                kind="error",
                status="candidate",
                confidence=0.4,
                evidence_refs=[f"{session_id}: error at step {step} → success at step {later[0]}"],
                verification=f"subsequent success of the same tool at step {later[0]}",
            )
        )
    return proposals
