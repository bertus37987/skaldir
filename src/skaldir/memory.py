"""Memory learning (Block 7) — schema-aware retrieval with a hard applicability gate.

Three rules are enforced here, in this order — never in another order:

1. **Retrieve** — candidate entries (exact signature → FTS5 → optional vector
   similarity) come out of the stores.
2. **Check applicability** — every candidate goes through a deterministic,
   semantic applicability check (freshness, component/failure-class/scope
   compatibility, confidence, review status, contradictions). No LLM is
   required for this step; a verdict must never depend on a model being up.
3. **Use or reject** — only a ``strong`` verdict may drive a strong
   intervention (an interruption of the running agent). ``informational``
   entries may be *shown* as context; ``reject`` entries may not be used as a
   basis at all.

The invariant behind requirement §38/GS-04: **a weak incident match must never
trigger a strong intervention.** A candidate with the wrong component, a
stale ``valid_until``, an unreviewed ``candidate`` status or a confidence below
the threshold is never ``strong``, no matter how plausible its text looks.

Freshness and provenance:

* ``valid_until`` (epoch seconds, 0 = no expiry) — an expired entry is rejected,
  not silently trusted (audit MEM-12: the old ``stale`` flag was display only).
* ``provenance`` — every entry must be traceable: where it came from, when it
  was recorded, in which context (session/step/event refs). Write side:
  ``IncidentStore.upsert`` fills the unavoidable parts; ``provenance_of()``
  below is the read/verify side.

This module also carries the read-only checks for the audit's side finding
(5 duplicate event groups, no retention): ``duplicate_event_groups`` and
``retention_report`` inspect the event store **without writing to it** — the
enforcing change (unique key / pruning) belongs in ``events.py``, which is
owned by another work block (see docs/MEMORY-LEARNING.md, open points).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .embedding import DIM, HashingEmbeddings, cosine

# -- verdicts --------------------------------------------------------------- #

VERDICT_STRONG = "strong"
VERDICT_INFORMATIONAL = "informational"
VERDICT_REJECT = "reject"
VERDICTS = (VERDICT_STRONG, VERDICT_INFORMATIONAL, VERDICT_REJECT)

# Mandated entry schemas (Block 7). ``to_schema_dict()`` on the records emits
# exactly these keys — the mapping onto legacy columns is documented in
# docs/MEMORY-LEARNING.md (``evidence`` ← ``evidence_refs``, ``scope`` ←
# ``repo_scope``).
INCIDENT_SCHEMA_FIELDS = (
    "failure_class",
    "root_cause",
    "component",
    "evidence",
    "failed_strategies",
    "successful_recovery",
    "confidence",
    "scope",
    "first_seen",
    "last_seen",
    "provenance",
)

RESEARCH_SCHEMA_FIELDS = (
    "question",
    "finding",
    "evidence",
    "sources",
    "created_at",
    "valid_until",
    "confidence",
    "component",
    "goal_scope",
    "contradictions",
    "reused_from",
)

# Default thresholds. Deliberately conservative: strong use is the expensive
# decision (it interrupts a running agent), so it needs a real match.
DEFAULT_MIN_CONFIDENCE = 0.5
DEFAULT_STRONG_SIMILARITY = 0.6
DEFAULT_INFORMATIONAL_SIMILARITY = 0.2

_STOPWORDS = frozenset(
    {
        "cannot", "could", "from", "have", "into", "name", "should", "that", "their",
        "there", "these", "this", "those", "when", "where", "which", "while", "with",
        "would", "error", "failed", "failure", "traceback", "using", "unknown",
    }
)

_HASH = HashingEmbeddings()


# -- deterministic similarity ----------------------------------------------- #


def tokens(text: str) -> set[str]:
    """Meaningful tokens: alnum, length >= 4, stopwords and generics removed."""
    cleaned = "".join(char if char.isalnum() else " " for char in str(text).lower())
    return {token for token in cleaned.split() if len(token) >= 4 and token not in _STOPWORDS}


def lexical_overlap(left: str, right: str) -> float:
    """Overlap coefficient over meaningful tokens (0..1)."""
    left_tokens, right_tokens = tokens(left), tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))


def vector_score(left: str, right: str) -> float:
    """Cosine over the configured embedding provider (lexical overlap only).

    With ``HashingEmbeddings`` this is *character trigram* similarity — a
    lexical signal, **not** semantics (audit MEM-10). It is therefore weighted
    below the term overlap in :func:`similarity`.
    """
    if not left.strip() or not right.strip():
        return 0.0
    return max(0.0, cosine(_HASH.embed(left), _HASH.embed(right)))


def similarity(left: str, right: str) -> float:
    """Combined deterministic similarity in 0..1 (see module docstring)."""
    return max(lexical_overlap(left, right), 0.75 * vector_score(left, right))


def _is_path_scope(value: str) -> bool:
    """True for path-like scopes (``src/skaldir/**``) — prose goals are not paths."""
    text = value.strip()
    return "/" in text or "**" in text or "\\" in text


def _scope_intersects(left: str, right: str) -> bool:
    left_norm = left.strip().strip("/").lower()
    right_norm = right.strip().strip("/").lower()
    if not left_norm or not right_norm:
        return True
    if left_norm == right_norm:
        return True
    # path-like scopes: one side being a prefix of the other is an overlap
    if left_norm.endswith("/**"):
        left_norm = left_norm[:-3]
    if right_norm.endswith("/**"):
        right_norm = right_norm[:-3]
    return left_norm.startswith(right_norm) or right_norm.startswith(left_norm)


# -- context / verdict records ---------------------------------------------- #


@dataclass(frozen=True)
class RetrievalContext:
    """Everything the applicability check needs about the *current* situation."""

    problem: str = ""
    component: str = ""
    failure_class: str = ""
    goal_scope: str = ""
    signature: str = ""
    now: float = 0.0
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    strong_similarity: float = DEFAULT_STRONG_SIMILARITY
    informational_similarity: float = DEFAULT_INFORMATIONAL_SIMILARITY
    allow_candidate: bool = False  # propose-first: unreviewed entries stay advisory

    def at(self, now: float) -> RetrievalContext:
        return RetrievalContext(**{**self.__dict__, "now": now})

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass(frozen=True)
class Applicability:
    """The verdict for one candidate: retrieve → check → use/reject."""

    ref: str
    verdict: str
    kind: str = "incident"
    reasons: tuple[str, ...] = ()
    similarity: float = 0.0
    lexical_overlap: float = 0.0
    vector: float = 0.0
    confidence: float = 0.0
    fresh: bool = True
    matched_fields: tuple[str, ...] = ()
    conflict_fields: tuple[str, ...] = ()
    text: str = ""

    @property
    def applicable(self) -> bool:
        """True for ``strong`` and ``informational`` — ``reject`` is not usable."""
        return self.verdict != VERDICT_REJECT

    @property
    def strong(self) -> bool:
        return self.verdict == VERDICT_STRONG

    def can_trigger_strong_intervention(self) -> bool:
        """The gate: only a strong verdict may interrupt the running agent."""
        return self.verdict == VERDICT_STRONG

    def to_dict(self) -> dict:
        data = dict(self.__dict__)
        data["reasons"] = list(self.reasons)
        data["matched_fields"] = list(self.matched_fields)
        data["conflict_fields"] = list(self.conflict_fields)
        data["text"] = self.text[:200]
        return data


@dataclass
class RetrievalDecision:
    """Outcome of one retrieve→check→use/reject cycle."""

    context: RetrievalContext
    verdicts: list[Applicability] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    used_vector: bool = False

    @property
    def use(self) -> list[Applicability]:
        return [item for item in self.verdicts if item.verdict == VERDICT_STRONG]

    @property
    def informational(self) -> list[Applicability]:
        return [item for item in self.verdicts if item.verdict == VERDICT_INFORMATIONAL]

    @property
    def rejected(self) -> list[Applicability]:
        return [item for item in self.verdicts if item.verdict == VERDICT_REJECT]

    @property
    def strong_intervention_allowed(self) -> bool:
        """Invariant §38/GS-04 — no strong verdict, no strong intervention."""
        return bool(self.use)

    def evidence_lines(self, include_informational: bool = True) -> list[str]:
        """Renderable lines for a context/evidence section (rejected never shown)."""
        lines: list[str] = []
        for item in self.use:
            lines.append(f"{item.ref}: {item.text}")
        if include_informational:
            for item in self.informational:
                lines.append(
                    f"{item.ref} (weak match — verify before use): {item.text}"
                )
        return lines

    def to_dict(self) -> dict:
        return {
            "context": self.context.to_dict(),
            "use": [item.to_dict() for item in self.use],
            "informational": [item.to_dict() for item in self.informational],
            "rejected": [item.to_dict() for item in self.rejected],
            "candidates": list(self.candidates),
            "used_vector": self.used_vector,
            "strong_intervention_allowed": self.strong_intervention_allowed,
        }


@dataclass
class RetrievalLog:
    """Cold/warm bookkeeping: did memory actually contribute anything?"""

    retrievals: int = 0
    used: int = 0
    informational: int = 0
    rejected: int = 0

    def record(self, decision: RetrievalDecision) -> RetrievalDecision:
        self.retrievals += 1
        self.used += len(decision.use)
        self.informational += len(decision.informational)
        self.rejected += len(decision.rejected)
        return decision

    @property
    def warm(self) -> bool:
        return self.used > 0

    def to_dict(self) -> dict:
        return {
            "retrievals": self.retrievals,
            "used": self.used,
            "informational": self.informational,
            "rejected": self.rejected,
            "warm": self.warm,
        }


# -- the applicability check ------------------------------------------------ #


def classify_candidate(
    ref: str,
    text: str,
    *,
    context: RetrievalContext,
    kind: str = "incident",
    component: str = "",
    failure_class: str = "",
    scope: str = "",
    confidence: float = 0.0,
    valid_until: float | None = None,
    status: str = "active",
    contradictions: Iterable[str] = (),
    exact: bool = False,
) -> Applicability:
    """Deterministic, semantic applicability check for one candidate entry."""
    now = context.now or time.time()
    reasons: list[str] = []
    matched: list[str] = []
    conflicts: list[str] = []

    lex = lexical_overlap(context.problem, text)
    vec = vector_score(context.problem, text)
    sim = 1.0 if exact else max(lex, 0.75 * vec)

    fresh = True
    if valid_until and valid_until > 0 and now > valid_until:
        fresh = False
        reasons.append("expired")
    if status == "archived":
        reasons.append("archived")
    if kind == "research" and status == "contradicted":
        reasons.append("contradicted")
    if list(contradictions):
        reasons.append("contradicted")

    if context.component and component:
        (matched if component == context.component else conflicts).append("component")
    if context.failure_class and failure_class:
        (matched if failure_class == context.failure_class else conflicts).append(
            "failure_class"
        )
    # Scope is only compared when both sides describe the same *kind* of scope:
    # path-like scopes (``src/skaldir/**``) are prefix-compared; a prose goal is
    # never treated as a path, otherwise every text goal would "conflict".
    if context.goal_scope and scope and _is_path_scope(scope) and _is_path_scope(
        context.goal_scope
    ):
        if _scope_intersects(context.goal_scope, scope):
            matched.append("goal_scope")
        else:
            conflicts.append("goal_scope")
    reasons.extend(f"{name}_mismatch" for name in conflicts)

    if reasons:
        verdict = VERDICT_REJECT
    elif confidence < context.min_confidence:
        # An unreviewed/low-confidence entry can never be strong — this is the
        # §38/GS-04 rule: a weak incident match never drives a strong action.
        verdict = VERDICT_INFORMATIONAL
        reasons.append(
            "unreviewed_candidate"
            if status == "candidate" and not context.allow_candidate
            else "low_confidence"
        )
    elif status == "candidate" and not context.allow_candidate:
        verdict = VERDICT_INFORMATIONAL
        reasons.append("unreviewed_candidate")
    elif exact or (sim >= context.strong_similarity and matched):
        verdict = VERDICT_STRONG
        reasons.append("strong_match")
    elif sim >= context.informational_similarity or exact:
        verdict = VERDICT_INFORMATIONAL
        reasons.append("weak_match")
    else:
        verdict = VERDICT_REJECT
        reasons.append("insufficient_overlap")

    return Applicability(
        ref=ref,
        verdict=verdict,
        kind=kind,
        reasons=tuple(reasons),
        similarity=round(sim, 4),
        lexical_overlap=round(lex, 4),
        vector=round(vec, 4),
        confidence=float(confidence),
        fresh=fresh,
        matched_fields=tuple(matched),
        conflict_fields=tuple(conflicts),
        text=text,
    )


def applicability_report(
    ref: str, *, context: RetrievalContext, text: str, **axes: Any
) -> Applicability:
    """Build one applicability verdict directly (CLI/inspection entry point)."""
    return classify_candidate(ref, text, context=context, **axes)


def _get(source: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(source, dict):
            if name in source and source[name] is not None:
                return source[name]
        else:
            value = getattr(source, name, None)
            if value is not None:
                return value
    return default


def incident_text(incident: Any) -> str:
    """Text used for the similarity signal of an incident candidate."""
    parts = [
        str(_get(incident, "title", default="")),
        str(_get(incident, "failure_class", default="")),
        str(_get(incident, "component", default="")),
        str(_get(incident, "root_cause", default="")),
        " ".join(str(item) for item in (_get(incident, "resolution_steps", default=[]) or [])),
        str(_get(incident, "successful_recovery", default="")),
    ]
    return " — ".join(part for part in parts if part)


def classify_incident(
    incident: Any, context: RetrievalContext, *, exact: bool = False
) -> Applicability:
    """Applicability verdict for one recalled incident (see module docstring)."""
    ref = f"incident#{_get(incident, 'id', default='?')}"
    return classify_candidate(
        ref,
        incident_text(incident),
        context=context,
        kind="incident",
        component=str(_get(incident, "component", default="") or ""),
        failure_class=str(_get(incident, "failure_class", default="") or ""),
        scope=str(
            _get(incident, "scope", default="")
            or _get(incident, "repo_scope", default="")
            or ""
        ),
        confidence=float(_get(incident, "confidence", default=0.0) or 0.0),
        valid_until=float(_get(incident, "valid_until", default=0.0) or 0.0) or None,
        status=str(_get(incident, "status", default="active") or "active"),
        exact=exact,
    )


def classify_research(entry: Any, context: RetrievalContext) -> Applicability:
    """Applicability verdict for one archived research entry."""
    ref = f"research#{_get(entry, 'id', default='?')}"
    text = " — ".join(
        part
        for part in (
            str(_get(entry, "question", "problem", default="")),
            str(_get(entry, "finding", "conclusion", default="")),
            " ".join(str(item) for item in (_get(entry, "evidence", default=[]) or [])),
        )
        if part
    )
    return classify_candidate(
        ref,
        text,
        context=context,
        kind="research",
        component=str(_get(entry, "component", default="") or ""),
        failure_class="",
        scope=str(_get(entry, "goal_scope", default="") or ""),
        confidence=float(_get(entry, "confidence", default=0.0) or 0.0),
        valid_until=float(_get(entry, "valid_until", default=0.0) or 0.0) or None,
        status=str(_get(entry, "status", default="active") or "active"),
        contradictions=_get(entry, "contradictions", default=[]) or [],
    )


# -- retrieval -------------------------------------------------------------- #


def retrieve_incidents(
    incidents: Any,
    context: RetrievalContext,
    *,
    limit: int = 5,
    embedder: Any = None,
) -> tuple[list[tuple[Any, float, bool]], bool]:
    """Candidates from the incident store: exact → FTS → optional vector.

    Returns ``(candidates, used_vector)`` where each candidate is
    ``(incident, score, exact)``. This is step 1 only — applicability is
    decided in :func:`evaluate_retrieval`.
    """
    found: dict[str, tuple[Any, float, bool]] = {}
    if incidents is None:
        return [], False

    if context.signature:
        hit = incidents.get(context.signature)
        if hit is not None:
            found[f"incident#{hit.id}"] = (hit, 1.0, True)

    if context.problem:
        try:
            text_hits = incidents.search_text(context.problem, limit=limit)
        except Exception:  # best-effort lookup; the gate still applies downstream
            text_hits = []
        for incident, score in text_hits:
            key = f"incident#{incident.id}"
            current = found.get(key)
            if current is None or current[1] < float(score):
                found[key] = (incident, float(score), current[2] if current else False)

    used_vector = False
    if embedder is not None and context.problem:
        try:
            vector = embedder.embed(context.problem)
            for incident, score in incidents.search_similar(vector, limit=limit):
                used_vector = True
                key = f"incident#{incident.id}"
                if key not in found:
                    found[key] = (incident, float(score), False)
        except Exception:  # optional path — never a hard dependency
            used_vector = False

    ordered = sorted(found.values(), key=lambda item: item[1], reverse=True)[:limit]
    return ordered, used_vector


def evaluate_retrieval(
    incidents: Any,
    *,
    context: RetrievalContext,
    limit: int = 5,
    embedder: Any = None,
    log: RetrievalLog | None = None,
) -> RetrievalDecision:
    """retrieve → applicability check → use/reject (the enforced rule)."""
    candidates, used_vector = retrieve_incidents(
        incidents, context, limit=limit, embedder=embedder
    )
    verdicts = [
        classify_incident(incident, context, exact=exact)
        for incident, _score, exact in candidates
    ]
    order = {VERDICT_STRONG: 0, VERDICT_INFORMATIONAL: 1, VERDICT_REJECT: 2}
    verdicts.sort(key=lambda item: (order.get(item.verdict, 3), -item.similarity))
    decision = RetrievalDecision(
        context=context,
        verdicts=verdicts,
        candidates=[f"incident#{incident.id}" for incident, _s, _e in candidates],
        used_vector=used_vector,
    )
    if log is not None:
        log.record(decision)
    return decision


# -- enforcement at the evidence boundary ------------------------------------ #


def gate_evidence(
    evidence: Iterable[Any],
    *,
    context: RetrievalContext,
    incidents: Any = None,
    mark_attribute: str = "applicability",
) -> list[Any]:
    """Filter pre-collected evidence through the applicability gate.

    This is the one-call hook for the live intervention path: callers hand over
    their ``Evidence`` objects (``retrieval.py``) and get back **only** entries
    that may be used. Weak entries stay in the list but are marked
    non-applicable via ``mark_attribute``, so a caller cannot mistake a weak
    match for a strong one by forgetting to ask.

    Rejected entries are dropped entirely — a rejected basis must not even be
    rendered. ``incidents`` is optional; when given, the stored incident is
    re-checked with its full record (component, failure class, scope, expiry,
    review status) instead of the truncated evidence text alone.
    """
    kept: list[Any] = []
    for item in evidence:
        ref = str(getattr(item, "ref", "") or "")
        verdict = None
        if incidents is not None and ref.startswith("incident#"):
            try:
                incident_id = int(ref.split("#", 1)[1])
            except (IndexError, ValueError):
                incident_id = None
            if incident_id is not None:
                incident = incidents.get_by_id(incident_id)
                if incident is not None:
                    verdict = classify_incident(
                        incident, context, exact=bool(getattr(item, "exact", False))
                    )
        if verdict is None:
            # notes / research: external, untrusted by construction — they never
            # count as strong, only as informational context
            verdict = classify_candidate(
                ref,
                str(getattr(item, "text", "") or ""),
                context=context,
                kind="note" if ref.startswith("note#") else "incident",
                confidence=float(getattr(item, "trust", 0.0) or 0.0),
                status="active",
            )
            if getattr(item, "untrusted", False):
                verdict = Applicability(
                    ref=verdict.ref,
                    verdict=VERDICT_INFORMATIONAL,
                    kind="note",
                    reasons=tuple(verdict.reasons) + ("untrusted_source",),
                    similarity=verdict.similarity,
                    lexical_overlap=verdict.lexical_overlap,
                    vector=verdict.vector,
                    confidence=verdict.confidence,
                    fresh=verdict.fresh,
                    matched_fields=verdict.matched_fields,
                    conflict_fields=verdict.conflict_fields,
                    text=verdict.text,
                )
        if verdict.verdict == VERDICT_REJECT:
            continue
        try:
            setattr(item, mark_attribute, verdict)
        except AttributeError:  # frozen/foreign object: the verdict still governs
            pass
        kept.append(item)
    return kept


def strong_basis(evidence: Iterable[Any], mark_attribute: str = "applicability") -> list[Any]:
    """The subset of gated evidence that may drive a strong intervention."""
    result: list[Any] = []
    for item in evidence:
        verdict = getattr(item, mark_attribute, None)
        if verdict is not None and verdict.can_trigger_strong_intervention():
            result.append(item)
    return result


# -- provenance ------------------------------------------------------------- #


def provenance_of(entry: Any) -> dict:
    """Normalised provenance view: origin, recorded_at, session, step, refs, source."""
    raw = _get(entry, "provenance", default={}) or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {"source": raw}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "origin": str(raw.get("origin", "unknown")),
        "recorded_at": float(raw.get("recorded_at", 0.0) or 0.0),
        "session_id": str(raw.get("session_id", "")),
        "step": raw.get("step"),
        "event_refs": list(raw.get("event_refs", []) or []),
        "source": str(raw.get("source", "")),
        "complete": bool(raw.get("recorded_at"))
        and str(raw.get("origin", "")) not in ("", "unknown"),
    }


def merge_provenance(existing: Any, incoming: Any) -> dict:
    """Merge two provenance payloads (incoming non-empty keys win)."""
    base = dict(existing) if isinstance(existing, dict) else {}
    for key, value in (incoming or {}).items():
        if value in (None, "", [], {}):
            continue
        base[key] = value
    refs: list[str] = []
    for payload in (existing, incoming):
        if isinstance(payload, dict):
            for ref in payload.get("event_refs", []) or []:
                if ref not in refs:
                    refs.append(ref)
    if refs:
        base["event_refs"] = refs
    return base


# -- audit side finding: duplicate events + retention ------------------------ #

# Enforcement points that live in files owned by other work blocks. Recorded
# here (and in docs/MEMORY-LEARNING.md) so the gap is not silently forgotten:
# Anchored by symbol, not by line number: events.py is edited by other work
# blocks and its line numbers move.
ENFORCEMENT_GAPS = (
    "src/skaldir/events.py::EventStore._append — `INSERT INTO events` has no "
    "uniqueness key; append is not deduped",
    "src/skaldir/events.py::EventStore.iter_session / ::EventStore.stream — load "
    "unbounded; no retention/pruning exists anywhere",
    "src/skaldir/events.py::KINDS — every new event kind must be registered there "
    "before append, so a memory event kind cannot be added from this module",
    "tests/test_memory_learning.py::TestDedupeAndRetention — verification only; "
    "the enforcing change (unique key / pruning) needs events.py",
)


@dataclass
class RetentionReport:
    total: int = 0
    sessions: int = 0
    oldest_ts: float = 0.0
    newest_ts: float = 0.0
    bytes_on_disk: int = 0
    duplicate_groups: list[dict] = field(default_factory=list)
    duplicate_rows: int = 0
    over_age: int = 0
    scanned: int = 0
    truncated: bool = False
    policy: dict = field(default_factory=dict)
    enforcement_gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _page_events(store: Any, *, batch: int = 2000, max_events: int = 200_000):
    """Iterate an event store in bounded pages (never load everything at once)."""
    after = 0
    seen = 0
    while True:
        events = store.stream(after_seq=after)
        if not events:
            return
        for event in events:
            seen += 1
            if seen > max_events:
                return
            yield event
        after = int(events[-1].seq)
        if len(events) < batch and len(events) == 0:
            return


def duplicate_event_groups(
    store: Any, *, limit: int = 20, max_events: int = 200_000
) -> tuple[list[dict], int, int]:
    """Read-only duplicate detection over ``(session_id, step, kind)``.

    Returns ``(groups, duplicate_rows, scanned)``. This *checks* (audit: 5 live
    duplicate groups); it deliberately does not delete or re-key anything —
    ``events.py`` belongs to another work block.
    """
    counts: Counter[tuple[str, int, str]] = Counter()
    scanned = 0
    for event in _page_events(store, max_events=max_events):
        counts[(str(event.session_id), int(event.step), str(event.kind))] += 1
        scanned += 1
    groups = [
        {"session_id": key[0], "step": key[1], "kind": key[2], "count": count}
        for key, count in counts.items()
        if count > 1
    ]
    groups.sort(key=lambda item: -item["count"])
    duplicate_rows = sum(item["count"] - 1 for item in groups)
    return groups[:limit], duplicate_rows, scanned


def retention_report(
    store: Any,
    *,
    max_age_days: float = 30.0,
    max_bytes: int | None = None,
    now: float | None = None,
    max_events: int = 200_000,
) -> RetentionReport:
    """Read-only retention assessment (age/size/duplicates) over an event store.

    The rule is expressed, not enforced: pruning rows and adding a uniqueness
    key require ``events.py`` changes (see ``ENFORCEMENT_GAPS``).
    """
    timestamp = time.time() if now is None else now
    report = RetentionReport(
        policy={
            "max_age_days": max_age_days,
            "max_bytes": max_bytes,
            "mode": "report-only (read-only, no pruning)",
        },
        enforcement_gaps=list(ENFORCEMENT_GAPS),
    )
    sessions: set[str] = set()
    cutoff = timestamp - max_age_days * 86400
    timestamps: list[float] = []
    for event in _page_events(store, max_events=max_events):
        report.total += 1
        sessions.add(str(event.session_id))
        timestamps.append(float(event.ts))
        if float(event.ts) < cutoff:
            report.over_age += 1
    report.sessions = len(sessions)
    if timestamps:
        report.oldest_ts = min(timestamps)
        report.newest_ts = max(timestamps)
    if report.total >= max_events:
        report.truncated = True
    path = Path(str(getattr(store, "path", "") or ""))
    if path.is_file():
        report.bytes_on_disk = path.stat().st_size
    groups, duplicate_rows, scanned = duplicate_event_groups(store, max_events=max_events)
    report.duplicate_groups = groups
    report.duplicate_rows = duplicate_rows
    report.scanned = scanned
    return report


# -- observability ---------------------------------------------------------- #



def schema_axes() -> dict:
    """Schema facts for the doc/CLI: field counts and embedding dimension."""
    return {
        "incident_fields": list(INCIDENT_SCHEMA_FIELDS),
        "research_fields": list(RESEARCH_SCHEMA_FIELDS),
        "embedding_dim": DIM,
        "verdicts": list(VERDICTS),
    }
