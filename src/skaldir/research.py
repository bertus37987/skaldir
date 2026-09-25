"""Isolated research runs for voice pauses.

A research run gets a **fresh, bounded context** (pact, recent events, memory
hits, targeted repo snippets), condenses it into a short redirect
(problem / evidence / direction / avoid / uncertainty) and archives the full
material on disk — only the condensed finding travels back into the loop
(no research text dumps). Runs are strictly bounded: wall-clock budget, hard
timeout at the caller (pause lease TTL), and estimation-only token numbers.

**Search before research** (Block 7): every run first queries the existing
research database, then checks each hit for freshness and applicability, and
only the missing or expired parts are researched again — see
``plan_research`` / ``find_prior_research``. The archive carries the mandated
research schema (``question``, ``finding``, ``evidence``, ``sources``,
``created_at``, ``valid_until``, ``confidence``/``confidence_score``,
``component``, ``goal_scope``, ``contradictions``, ``reused_from``) plus
``provenance`` (origin, session, step, trigger).

Never raises: a failed run returns ``status="failed"`` with a deterministic
template redirect so the pause path always has something to deliver.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context import ContextBuilder
from .events import EventStore
from .llm import BudgetTracker, LLMClient
from .pact import Pact
from .promptrun import run_prompt
from .prompts import PromptRegistry
from .secretscan import redact

TEMPLATES: dict[str, tuple[str, str]] = {
    "loop": (
        "Do not repeat the same call: read the error message in full, check your assumptions, "
        "then deliberately pursue a different approach (or ask the user).",
        "Repeating the identical call without new information.",
    ),
    "error_burst": (
        "Read the error message in full, check the known error class in incident memory, "
        "build a minimal repro and then fix it.",
        "Keep going and provoke the same error again.",
    ),
    "contradiction": (
        "Resolve the contradiction: withdraw the success claim and re-check it with real "
        "evidence before building further on it.",
        "Building on an unevidenced success assumption.",
    ),
    "drift": (
        "Return to the agreed scope/pact or explicitly ask the user for an extension.",
        "Silent scope expansion.",
    ),
    "other": (
        "Clarify the situation with a small, targeted check (error message, diff, docs) and "
        "then justify the next step.",
        "Carrying on blindly without new information.",
    ),
}

_SNIPPET_SUFFIXES = (".py", ".md", ".toml", ".yaml", ".yml")
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".godsv", "dist", "build"}


@dataclass
class ResearchResult:
    research_id: str
    session_id: str
    base_step: int
    trigger_kind: str
    trigger_fp: str
    problem: str
    evidence: list[str] = field(default_factory=list)
    direction: str = ""
    avoid: str = ""
    uncertainty: str = ""
    status: str = "ok"  # ok | failed
    duration_ms: int = 0
    tokens_est: int = 0
    archive_path: str = ""
    used_llm: bool = False
    # False = investigation says: normal recovery, do not interrupt (§10)
    intervention_needed: bool = True
    no_intervention_reason: str = ""
    # Block 7: what the research database contributed to this run
    reused_research: list[str] = field(default_factory=list)
    used_prior_finding: bool = False
    research_needed: bool = True

    def redirect_md(self) -> str:
        lines = [
            f"[voice·pause] briefly paused — reason: {self.trigger_kind} [{self.trigger_fp}].",
            "",
            f"Problem: {self.problem[:300]}",
        ]
        if self.evidence:
            lines.append("Evidence:")
            lines.extend(f"- {item[:220]}" for item in self.evidence[:3])
        lines.append(f"Empfohlene Richtung: {self.direction}")
        lines.append(f"Vermeiden: {self.avoid}")
        lines.append(f"Unsicherheit: {self.uncertainty}")
        lines.append("")
        lines.append(
            "Continue in this direction; afterwards verify equivalent claims with real evidence."
        )
        return "\n".join(lines)


_STOPWORDS = {
    "traceback", "error", "fehler", "import", "cannot", "from", "name", "module",
    "using", "failed", "fails", "failure", "unknown", "should", "would", "there",
}


def _terms(text: str, *, limit: int = 6) -> list[str]:
    tokens: list[str] = []
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,}", text):
        low = token.lower()
        if low not in tokens and low not in _STOPWORDS:
            tokens.append(low)
        if len(tokens) >= limit:
            break
    return tokens


_STALE_AFTER_SECONDS = 14 * 24 * 3600  # findings older than this are stale

# A prior finding is only reused as-is when it really matches the current
# problem (same rule as the incident gate: weak match never drives a strong use).
REUSE_SIMILARITY = 0.6

# Mandated research schema (Block 7) — fixed order for docs/CLI.
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


def _findings_of(payload: dict) -> dict:
    """The LLM finding block of an archive (never raises on legacy shapes)."""
    llm = payload.get("llm")
    return llm if isinstance(llm, dict) else {}


def _first_text(source: dict, *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if value:
            return str(value)
    return ""


def _finding_text(payload: dict) -> str:
    """Short finding for an archive: explicit ``finding`` first, LLM block second."""
    findings = _findings_of(payload)
    return _first_text(
        payload, "finding", "conclusion", "summary", "root_cause", "answer"
    ) or _first_text(
        findings, "finding", "conclusion", "summary", "root_cause", "answer", "direction"
    )


def _evidence_of(payload: dict) -> list[str]:
    findings = _findings_of(payload)
    raw = payload.get("evidence") or findings.get("evidence") or []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []


def _sources_of(payload: dict) -> list[str]:
    raw = payload.get("sources") or []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, dict):
        return [f"{key}:{value}" for key, value in raw.items()]
    return []


def _confidence_score(value: Any) -> float:
    """Normalise confidence to a comparable 0..1 number.

    Legacy/unverified archives carry the *string* ``"unverified"`` — that maps to
    0.0, never to a numeric default that would look verified. A missing value is
    also 0.0: absent evidence is not medium evidence.
    """
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    text = str(value).strip().lower()
    if text in ("", "unverified", "unknown", "none", "null"):
        return 0.0
    try:
        return max(0.0, min(1.0, float(text)))
    except ValueError:
        return 0.0


def archive_to_schema(payload: dict, *, path: str | Path | None = None) -> dict:
    """Render one archived research entry on the mandated schema (Block 7).

    Legacy archives (pre-Block 7 files) are mapped onto the same keys instead of
    being rewritten: an old file keeps working and simply carries empty
    ``sources``/``contradictions``.
    """
    created = float(payload.get("created_at") or 0.0)
    valid_until = float(payload.get("valid_until") or payload.get("stale_after") or 0.0)
    question = _first_text(payload, "question", "problem")
    confidence = payload.get("confidence")
    return {
        "id": payload.get("id") or (Path(path).stem if path else ""),
        "question": question,
        "finding": _finding_text(payload),
        "evidence": _evidence_of(payload),
        "sources": _sources_of(payload),
        "created_at": created,
        "valid_until": valid_until,
        "confidence": confidence if confidence is not None else "unverified",
        "confidence_score": _confidence_score(confidence),
        "component": str(payload.get("component") or ""),
        "goal_scope": str(payload.get("goal_scope") or ""),
        "contradictions": [str(item) for item in (payload.get("contradictions") or [])],
        "reused_from": [str(item) for item in (payload.get("reused_from") or [])],
        "provenance": payload.get("provenance") or {},
        "path": str(path) if path else "",
        "trigger": payload.get("trigger") or {},
    }


def find_prior_research(
    runtime_dir: str | Path,
    problem: str,
    *,
    limit: int = 3,
    now: float | None = None,
    component: str = "",
    goal_scope: str = "",
) -> list[dict]:
    """Search the research archive for findings relevant to ``problem``.

    Term-overlap scoring over the archived question/finding text; returns
    compact, provenance-tagged entries on the mandated schema (id, question,
    finding, evidence, sources, created_at, valid_until, confidence +
    confidence_score, component, goal_scope, contradictions, reused_from,
    age_hours, stale). Callers must re-check applicability — this is step 1
    (retrieve), never permission to use.
    """
    directory = Path(runtime_dir) / "research"
    if not directory.is_dir():
        return []
    terms = set(_terms(problem, limit=12))
    if not terms:
        return []
    timestamp = time.time() if now is None else now
    hits: list[tuple[int, dict]] = []
    for path in sorted(directory.glob("research-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        entry = archive_to_schema(payload, path=path)
        archived_text = f"{entry['question']} {entry['finding']}"
        overlap = len(terms & set(_terms(archived_text, limit=12)))
        if overlap < 2:  # two shared meaningful terms = same neighbourhood
            continue
        created = entry["created_at"] or path.stat().st_mtime
        age = max(0.0, timestamp - created)
        valid_until = entry["valid_until"]
        expired = bool(valid_until) and timestamp > valid_until
        scope_ok = True
        if goal_scope and entry["goal_scope"]:
            scope_ok = entry["goal_scope"].startswith(goal_scope) or goal_scope.startswith(
                entry["goal_scope"]
            )
        component_ok = not (component and entry["component"]) or entry["component"] == component
        entry.update(
            {
                "problem": entry["question"][:200],
                "conclusion": entry["finding"][:400],
                "created_at": created,
                "age_hours": round(age / 3600, 1),
                "stale": age > _STALE_AFTER_SECONDS or expired,
                "expired": expired,
                "scope_ok": scope_ok,
                "component_ok": component_ok,
                # applicability is decided by the caller via memory.classify_research
                "applicable": scope_ok and component_ok and not expired,
            }
        )
        hits.append((overlap, entry))
    hits.sort(key=lambda item: (-item[0], item[1]["created_at"]))
    return [entry for _score, entry in hits[: max(1, limit)]]


def plan_research(
    runtime_dir: str | Path,
    problem: str,
    *,
    now: float | None = None,
    component: str = "",
    goal_scope: str = "",
    limit: int = 3,
) -> dict:
    """Search → freshness/applicability → what still has to be researched.

    Returns ``{"reusable": [...], "inapplicable": [...], "questions": [...],
    "needed": bool, "checked": int}``. ``questions`` lists only the *open*
    sub-questions — a reused finding must never be re-researched, and an expired
    one must never be used; it becomes an open question instead.
    """
    hits = find_prior_research(
        runtime_dir,
        problem,
        now=now,
        limit=limit,
        component=component,
        goal_scope=goal_scope,
    )
    reusable = [hit for hit in hits if hit.get("applicable") and not hit.get("stale")]
    inapplicable = [hit for hit in hits if hit not in reusable]
    questions: list[str] = []
    for hit in inapplicable:
        reason = "expired" if hit.get("expired") else (
            "stale" if hit.get("stale") else "not applicable (component/scope)"
        )
        questions.append(f"re-verify prior finding {hit['id']} ({reason}): {hit['question'][:160]}")
    if not reusable and not questions:
        questions.append(problem[:300])
    return {
        "reusable": reusable,
        "inapplicable": inapplicable,
        "questions": questions,
        "needed": bool(questions),
        "checked": len(hits),
    }


def _reusable_direction(problem: str, reusable: list[dict]) -> tuple[dict, float] | None:
    """Pick the prior finding that really matches ``problem`` (or ``None``).

    Uses the same similarity rule as the incident applicability gate. A finding
    below the threshold is *not* silently reused — it stays context and the run
    researches again (weak match never becomes the answer).
    """
    from .memory import similarity  # local import: research must not need incidents

    best: tuple[dict, float] | None = None
    for entry in reusable:
        finding = str(entry.get("finding") or "")
        if not finding.strip():
            continue
        score = max(
            similarity(problem, str(entry.get("question") or "")),
            similarity(problem, finding),
        )
        if score >= REUSE_SIMILARITY and (best is None or score > best[1]):
            best = (entry, score)
    return best



class ResearchRunner:
    """Bounded, deterministic-first research for pause redirects."""

    def __init__(
        self,
        *,
        store: EventStore,
        state_dir: Path,
        incidents=None,
        notes=None,
        repo_root: Path | None = None,
        client: LLMClient | None = None,
        registry: PromptRegistry | None = None,
        budget: BudgetTracker | None = None,
        time_budget: float = 20.0,
        max_snippets: int = 10,
        on_heartbeat=None,
    ) -> None:
        self.store = store
        self.state_dir = Path(state_dir)
        self.incidents = incidents
        self.notes = notes
        self.repo_root = Path(repo_root) if repo_root else None
        self.client = client
        self.registry = registry
        self.budget = budget
        self.time_budget = time_budget
        self.max_snippets = max_snippets
        self.on_heartbeat = on_heartbeat
        self._builder = ContextBuilder(total_budget=4000)

    def run(
        self,
        *,
        session_id: str,
        base_step: int,
        trigger_kind: str,
        trigger_fp: str,
        problem: str,
        pact: Pact | None = None,
        skills_text: str = "",
    ) -> ResearchResult:
        started = time.monotonic()
        research_id = f"research-{uuid.uuid4().hex[:10]}"
        result = ResearchResult(
            research_id=research_id,
            session_id=session_id,
            base_step=base_step,
            trigger_kind=trigger_kind,
            trigger_fp=trigger_fp,
            problem=problem,
        )
        # P0-3 / Block 7: search the research DB BEFORE investigating — then check
        # freshness + applicability, and only the missing/expired parts are
        # researched again. What is already known is context, not new work.
        component = ""
        goal_scope = ""
        try:
            if pact is not None:
                goal_scope = str(getattr(pact, "goal", "") or "")[:120]
        except Exception:  # pact is optional metadata, never a blocker
            goal_scope = ""
        plan = plan_research(
            self.state_dir, problem, component=component, goal_scope=goal_scope
        )
        prior_findings = plan["reusable"] + plan["inapplicable"]
        created_at = time.time()
        archive: dict = {
            "id": research_id,
            "session_id": session_id,
            "base_step": base_step,
            "trigger": {"kind": trigger_kind, "fingerprint": trigger_fp},
            "problem": problem,
            # -- mandated research schema (Block 7) --------------------------
            "question": problem,
            "finding": "",  # filled once the redirect is known
            "evidence": [],
            "sources": [],  # filled with repo snippets / prior finding ids
            "created_at": created_at,
            "stale_after": created_at + _STALE_AFTER_SECONDS,
            "valid_until": created_at + _STALE_AFTER_SECONDS,
            "confidence": "unverified",  # provenance: Voice findings start unverified
            "confidence_score": 0.0,
            "component": component,
            "goal_scope": goal_scope,
            "contradictions": [],
            "reused_from": [entry["id"] for entry in plan["reusable"]],
            "provenance": {
                "origin": "voice_research",
                "recorded_at": created_at,
                "session_id": session_id,
                "step": base_step,
                "event_refs": [f"{session_id}:{base_step}"],
                "source": f"{trigger_kind}:{trigger_fp}",
            },
            "search_plan": {
                "checked": plan["checked"],
                "reused": len(plan["reusable"]),
                "open_questions": plan["questions"],
                "research_needed": plan["needed"],
            },
            "prior_findings": prior_findings,
            "context": "",
            "memory": [],
            "snippets": [],
            "llm": None,
        }
        try:
            direction, avoid = TEMPLATES.get(trigger_kind, TEMPLATES["other"])
            result.direction = direction
            result.avoid = avoid
            result.uncertainty = "Deterministic assessment (no LLM)."

            context = self._builder.build(
                self.store,
                session_id,
                step=base_step,
                query=problem,
                pact=pact,
                incidents=self.incidents,
                notes=self.notes,
                skills_text=skills_text,
                role="investigator",  # fresh, dedicated window — never the watcher's thread
            )
            archive["context"] = context.render()
            context_stats = context.stats()
            archive["context_stats"] = context_stats
            try:  # instrumentation must never break a research run
                self.store.append(
                    session_id,
                    base_step,
                    "voice_context_built",
                    {
                        "role": "investigator",
                        "budget_chars": context_stats["measured"]["budget_chars"],
                        "used_chars": context_stats["measured"]["used_chars"],
                        "truncated_sections": context_stats["measured"]["truncated_sections"],
                        "tokens_estimated": context_stats["estimated"]["tokens"],
                        "measure": "tokens ESTIMATED via chars/4",
                    },
                )
            except Exception:  # nosec B110 — accounting is best-effort by design
                pass
            if self.on_heartbeat:
                self.on_heartbeat()

            memory_lines: list[str] = []
            if self.incidents is not None:
                for incident, _score in self.incidents.search_text(problem, limit=4):
                    memory_lines.append(
                        f"incident[{incident.signature}]: {incident.to_evidence_text()}"
                    )
            if self.notes is not None:
                for note, _score in self.notes.search_text(problem, limit=4):
                    memory_lines.append(f"note#{note.id}: {note.to_evidence_text()}")
            for entry in prior_findings:
                memory_lines.append(
                    "research#{id}: {conclusion} (age {age}h, {conf}{stale})".format(
                        id=entry["id"],
                        conclusion=(entry["conclusion"] or entry["problem"])[:160],
                        age=entry["age_hours"],
                        conf=entry["confidence"],
                        stale=", STALE — re-verify" if entry["stale"] else "",
                    )
                )
            archive["memory"] = memory_lines
            if self.on_heartbeat:
                self.on_heartbeat()

            snippets = self._repo_snippets(problem)
            archive["snippets"] = snippets
            if self.on_heartbeat:
                self.on_heartbeat()

            result.evidence = (memory_lines[:3] + snippets[:2])[:4]
            archive["evidence"] = [item[:220] for item in result.evidence]
            archive["sources"] = list(snippets[:8]) + [
                f"research#{entry['id']}" for entry in plan["reusable"]
            ]

            # Block 7 / search-before-research: a fresh, applicable prior finding
            # for the *same* problem means there is nothing left to investigate —
            # no second full research run, no LLM call. The reuse gate is the same
            # strong-similarity rule as the incident gate (a weak match must never
            # silently become the answer).
            reuse = _reusable_direction(problem, plan["reusable"])
            result.reused_research = [entry["id"] for entry in plan["reusable"]]
            if reuse is not None:
                entry, score = reuse
                result.direction = str(entry["finding"])[:400]
                result.avoid = avoid
                result.uncertainty = (
                    "Reused from earlier research "
                    f"research#{entry['id']} (similarity {score:.2f}, age "
                    f"{entry['age_hours']}h) — check it against the current "
                    "state before applying."
                )
                result.used_prior_finding = True
                result.research_needed = False
                archive["reused_direction"] = True
                archive["reused_similarity"] = round(score, 4)
                archive["found"] = str(entry["finding"])[:2000]

            if (
                not result.used_prior_finding
                and self.client is not None
                and self.registry is not None
                and self._time_left(started) > 2
            ):
                findings = context.render()[:3000]
                if memory_lines:
                    findings += "\n\nMemory:\n" + "\n".join(memory_lines[:5])
                if snippets:
                    findings += "\n\nRepo-Funde:\n" + "\n".join(snippets[:8])
                try:
                    prompt_result = run_prompt(
                        self.client,
                        self.registry,
                        "voice_redirect",
                        {
                            "problem": f"{trigger_kind} [{trigger_fp}]: {problem[:400]}",
                            "goal": pact.goal if pact else "(no pact loaded)",
                            "findings": findings,
                        },
                        budget=self.budget,
                    )
                    data = prompt_result.parsed if isinstance(prompt_result.parsed, dict) else {}
                    archive["llm"] = data
                    no_action = (
                        data.get("no_intervention") is True
                        or data.get("intervention_needed") is False
                    )
                    if no_action:
                        result.intervention_needed = False
                        result.no_intervention_reason = str(
                            data.get("no_intervention_reason") or data.get("reason") or ""
                        )[:200]
                        result.used_llm = True
                        archive["intervention_needed"] = False
                    if data.get("direction"):
                        result.direction = str(data.get("direction"))[:400]
                        result.avoid = str(data.get("avoid") or avoid)[:300]
                        result.uncertainty = str(data.get("uncertainty") or "")[:300]
                        evidence = data.get("evidence")
                        if isinstance(evidence, list) and evidence:
                            result.evidence = [str(item)[:220] for item in evidence[:3]]
                        result.used_llm = True
                except Exception as exc:  # template redirect stays
                    archive["llm"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        except Exception as exc:
            result.status = "failed"
            archive["error"] = f"{type(exc).__name__}: {exc}"[:300]
        finally:
            result.duration_ms = int((time.monotonic() - started) * 1000)
            if result.used_llm:
                result.tokens_est = max(1, (len(archive.get("context", "")) + 1200) // 4)
            # mandated schema: the finding is the condensed redirect; an archive
            # that produced no direction is explicitly empty, never silently "ok".
            archive["finding"] = str(result.direction or "")[:2000]
            archive["confidence"] = (
                "unverified"  # Voice findings stay unverified until re-checked
            )
            archive["confidence_score"] = 0.0
            result.archive_path = self._archive(research_id, archive)
        return result

    def _time_left(self, started: float) -> float:
        return self.time_budget - (time.monotonic() - started)

    def _repo_snippets(self, problem: str) -> list[str]:
        if self.repo_root is None or not self.repo_root.exists():
            return []
        terms = _terms(problem)
        if not terms:
            return []
        snippets: list[str] = []
        files = sorted(
            path
            for path in self.repo_root.rglob("*")
            if path.is_file()
            and path.suffix in _SNIPPET_SUFFIXES
            and not any(part in _SKIP_DIRS for part in path.parts)
        )
        for path in files[:200]:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(text) > 200_000:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                low = line.lower()
                if any(term in low for term in terms):
                    rel = path.relative_to(self.repo_root)
                    snippets.append(f"{rel}:{lineno}: {line.strip()[:160]}")
                    break
            if len(snippets) >= self.max_snippets:
                break
        return snippets

    def _archive(self, research_id: str, archive: dict) -> str:
        try:
            directory = self.state_dir / "research"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{research_id}.json"
            archive.setdefault("created_at", time.time())
            archive.setdefault("stale_after", archive["created_at"] + _STALE_AFTER_SECONDS)
            payload = json.dumps(archive, ensure_ascii=False, indent=1)
            scrubbed, _findings = redact(payload)
            path.write_text(scrubbed, encoding="utf-8")
            return str(path)
        except Exception:
            return ""
