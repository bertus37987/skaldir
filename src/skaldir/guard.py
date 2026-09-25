"""Completion & evidence guards (release focus).

Checks the main agent's *claims* against the pact's acceptance criteria and
the real evidence in the event store — deterministically first, with an
optional LLM judge for paraphrases (fail-open: the judge may only ADD
findings, never veto a deterministic one).

Delivery channels (Hermes hooks, see hooks.md):
- ``pre_verify`` (code-edited turns): ``{"action": "continue", "message": …}``
  keeps the agent going until the missing evidence exists (bounded built-in).
- ``transform_llm_output``: appends a short caveat to the final answer when a
  nudge is impossible or already spent — reaches the user-facing text of
  every turn, not just tool results.

Also detects: unsupported capability claims, unverified environment/version
assumptions, and claims contradicted by later errors (contradiction).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .events import EventStore
from .llm import BudgetTracker, LLMClient
from .pact import Pact
from .promptrun import run_prompt
from .prompts import PromptRegistry

CLAIM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "tests",
        re.compile(
            r"(?i)\b(tests?|test[- ]?suite|pytest|checks?|testlauf)\b[^.\n]{0,48}"
            r"\b(grün|green|bestanden|passed|passing|ok|laufen\s+durch|sind\s+ok)\b"
        ),
    ),
    (
        "tests",
        re.compile(r"(?i)\b(alle|all)\s+(tests?|checks?)\b[^.\n]{0,30}\b(grün|green|ok|bestanden)\b"),
    ),
    (
        "ui",
        re.compile(
            r"(?i)\b(ui|oberfläche|interface|fenster|layout|anzeige|seite|view)\b[^.\n]{0,48}"
            r"\b(funktioniert|läuft|sieht\s+(?:gut|korrekt)\s+aus|works|renders?|ok)\b"
        ),
    ),
    (
        "api",
        re.compile(
            r"(?i)\b(api|endpoint|schnittstelle)\b[^.\n]{0,48}"
            r"\b(liefert|funktioniert|unterstützt|returns?|works|antwortet)\b"
        ),
    ),
    (
        "done",
        re.compile(
            r"(?i)\b(fertig(?:gestellt)?|abgeschlossen|erledigt|komplett|implementiert|"
            r"umgesetzt|done|finished|completed|implemented)\b"
        ),
    ),
    (
        "works",
        re.compile(
            r"(?i)\b(funktioniert|läuft\s+(?:jetzt|wieder|rund)|works|working\s+(?:now|fine))\b"
        ),
    ),
]

NEGATIVE = re.compile(
    r"(?i)\b(nicht|noch\s+nicht|unfertig|offen|fehlt|not|no\b|unverified|unverified|"
    r"ungetestet|untested|unbelegt|skipped|skip(?:ped)?|failed|fehlgeschlagen|"
    r"kann\s+ich\s+nicht|konnte\s+nicht)\b"
)

ASSUMPTION = re.compile(
    r"(?i)\b(python|node|hermes|pytest|ruff|version|api|suite)\b[^.\n]{0,24}"
    r"\b(v?\d+\.\d+(?:\.\d+)?)\b"
)

TEST_HINT = re.compile(r"(?i)\b(pytest|unittest|tox|nox|test[s]?|check[s]?)\b")
VISUAL_HINT = re.compile(
    r"(?i)\b(screenshot|browser|playwright|selenium|render|xvfb|xdotool|screencapture)\b"
)
API_HINT = re.compile(r"(?i)\b(curl|wget|http|requests?|urllib)\b")

# Only execution-type tools count as run evidence — a successful write/patch
# is not verification of anything.
_EXEC_TOOLS = {
    "terminal", "shell", "bash", "sh", "exec", "command", "run", "subprocess",
    "python", "python_exec", "code", "code_execution",
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def extract_claims(text: str, *, limit: int = 20) -> list[tuple[str, str]]:
    """Return (category, sentence) for success/completion claims in ``text``.

    A sentence counts as a claim only when it matches a claim pattern and has
    no negation marker in the same sentence. Deterministic, no LLM.
    """
    if not text:
        return []
    claims: list[tuple[str, str]] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        candidate = sentence.strip()
        if not candidate or len(candidate) > 400:
            continue
        if NEGATIVE.search(candidate):
            continue
        for category, pattern in CLAIM_PATTERNS:
            if pattern.search(candidate):
                claims.append((category, candidate))
                break
        if len(claims) >= limit:
            break
    return claims


def extract_assumptions(text: str) -> list[str]:
    """Version/environment statements presented as fact (unverified suspects)."""
    if not text:
        return []
    found: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        candidate = sentence.strip()
        if not candidate or len(candidate) > 300 or NEGATIVE.search(candidate):
            continue
        if ASSUMPTION.search(candidate):
            found.append(candidate)
    return found


@dataclass
class EvidenceIndex:
    """What the event store can prove (steps are 1-based session steps)."""

    last_edit_step: int = 0
    tests_ok_steps: list[int] = field(default_factory=list)
    visual_ok_steps: list[int] = field(default_factory=list)
    api_ok_steps: list[int] = field(default_factory=list)
    any_ok_steps: list[int] = field(default_factory=list)
    version_check_steps: list[int] = field(default_factory=list)
    error_steps: list[tuple[int, str]] = field(default_factory=list)

    def last_ok_after(self, steps: list[int], after: int) -> int | None:
        candidates = [step for step in steps if step > after]
        return candidates[-1] if candidates else None


def build_evidence_index(store: EventStore, session_id: str) -> EvidenceIndex:
    """Deterministically index verification evidence from a session's events."""
    index = EvidenceIndex()
    call_category: dict[str, str] = {}
    call_is_version_check: dict[str, bool] = {}
    for event in store.iter_session(session_id):
        payload = event.payload or {}
        if event.kind == "tool_call":
            name = str(payload.get("name") or "")
            args_text = str(payload.get("args") or "")
            if TEST_HINT.search(args_text):
                call_category[name] = "tests"
            elif VISUAL_HINT.search(args_text):
                call_category[name] = "visual"
            elif API_HINT.search(args_text):
                call_category[name] = "api"
            else:
                call_category[name] = "other"
            call_is_version_check[name] = bool(re.search(r"(?i)--?version|python3? -V", args_text))
            continue
        if event.kind == "file_edit":
            # /dev/* pseudo-edits (redirect discards) must never move the
            # freshness checkpoint — this also covers events stored before
            # the plugin-side filter existed.
            if not str(payload.get("path") or "").startswith("/dev/"):
                index.last_edit_step = max(index.last_edit_step, event.step)
            continue
        if event.kind != "tool_result":
            continue
        error = payload.get("error") or (
            payload.get("stderr") if payload.get("exit_code") not in (None, 0) else ""
        )
        if error:
            index.error_steps.append((event.step, str(error)[:160]))
            continue
        if payload.get("exit_code") not in (None, 0):
            continue
        if payload.get("ok") is False:
            continue
        name = str(payload.get("tool") or payload.get("name") or "")
        category = call_category.get(name, "other")
        is_execution = name.lower() in _EXEC_TOOLS or "exit_code" in payload
        if is_execution:
            index.any_ok_steps.append(event.step)
        if category == "tests":
            index.tests_ok_steps.append(event.step)
        elif category == "visual":
            index.visual_ok_steps.append(event.step)
        elif category == "api":
            index.api_ok_steps.append(event.step)
        if call_is_version_check.get(name):
            index.version_check_steps.append(event.step)
    return index


@dataclass
class GuardFinding:
    category: str
    claim: str
    missing: str
    criterion_id: str = ""


@dataclass
class GuardVerdict:
    status: str  # "ok" | "needs_verification" | "unsupported"
    findings: list[GuardFinding] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    claims_checked: int = 0
    source: str = "deterministic"

    @property
    def needs_continue(self) -> bool:
        return self.status in ("needs_verification", "unsupported")

    def message(self, *, compact: bool = False) -> str:
        """Concrete verification request for the main agent (German)."""
        if not self.findings:
            return ""
        if compact:
            first = self.findings[0]
            extra = f" (+{len(self.findings) - 1} more)" if len(self.findings) > 1 else ""
            return (
                f"Unbacked completion claim: {first.missing}{extra}. "
                "Provide the evidence or state the claim as open."
            )
        lines = ["Completion blocked: evidence missing (completion guard):"]
        for finding in self.findings[:4]:
            prefix = f"[{finding.criterion_id}] " if finding.criterion_id else ""
            lines.append(f"- {prefix}{finding.missing}")
        lines.append(
            "Run the missing checks now and evidence the result — "
            "or state the claim honestly as open/unverified."
        )
        return "\n".join(lines)


_CRITERION_CATEGORY: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\b(test|suite|pytest|läuft\s+fehlerfrei|fehlerfrei\s+durch)\b"), "tests"),
    (re.compile(r"(?i)\b(ui|oberfläche|anzeige|layout|fenster)\b"), "ui"),
    (re.compile(r"(?i)\b(api|endpoint|schnittstelle)\b"), "api"),
    (re.compile(r"(?i)\b(läuft|funktioniert|works)\b"), "works"),
]


def criterion_categories(text: str) -> list[str]:
    found: list[str] = []
    for pattern, category in _CRITERION_CATEGORY:
        if pattern.search(text) and category not in found:
            found.append(category)
    return found


def evaluate_completion(
    store: EventStore,
    session_id: str,
    final_text: str,
    *,
    pact: Pact | None = None,
    index: EvidenceIndex | None = None,
) -> GuardVerdict:
    """Deterministic completion check: claims vs. evidence vs. acceptance."""
    claims = extract_claims(final_text)
    assumptions = extract_assumptions(final_text)
    evidence = index if index is not None else build_evidence_index(store, session_id)
    findings: list[GuardFinding] = []
    edited = evidence.last_edit_step > 0
    checkpoint = evidence.last_edit_step

    def require(category: str, claim: str, steps: list[int], what: str, *, after: int) -> None:
        if evidence.last_ok_after(steps, after) is None:
            findings.append(GuardFinding(category=category, claim=claim[:200], missing=what))

    for category, sentence in claims:
        if category == "tests":
            require(
                "tests",
                sentence,
                evidence.tests_ok_steps,
                f"no test-run evidence for the claim “{sentence[:90]}” "
                f"(no successful test since step {checkpoint or '—'})",
                after=checkpoint,
            )
        elif category == "done":
            verification_pool = sorted(
                set(
                    evidence.tests_ok_steps
                    + evidence.visual_ok_steps
                    + evidence.api_ok_steps
                    + evidence.any_ok_steps
                )
            )
            if edited:
                if evidence.last_ok_after(verification_pool, checkpoint) is None:
                    findings.append(
                        GuardFinding(
                            category="done",
                            claim=sentence[:200],
                            missing=(
                                f"done/implemented claim without verification after the last "
                                f"change (step {checkpoint}): no test, no UI/API check "
                                f"evidenced — claim “{sentence[:80]}”"
                            ),
                        )
                    )
            elif not evidence.any_ok_steps:
                findings.append(
                    GuardFinding(
                        category="done",
                        claim=sentence[:200],
                        missing=(
                            f"completion claim with no executed check in this session — "
                            f"claim “{sentence[:80]}”"
                        ),
                    )
                )
        elif category == "works":
            if edited:
                require(
                    "works",
                    sentence,
                    sorted(
                        set(
                            evidence.tests_ok_steps
                            + evidence.visual_ok_steps
                            + evidence.api_ok_steps
                            + evidence.any_ok_steps
                        )
                    ),
                    f"success claim “{sentence[:90]}” has no run/check evidence "
                    f"after the last change (step {checkpoint})",
                    after=checkpoint,
                )
            elif not evidence.any_ok_steps:
                findings.append(
                    GuardFinding(
                        category="works",
                        claim=sentence[:200],
                        missing=(
                            f"success claim “{sentence[:80]}” with no executed check "
                            "in this session"
                        ),
                    )
                )
        elif category == "ui":
            require(
                "ui",
                sentence,
                evidence.visual_ok_steps,
                f"UI claim “{sentence[:90]}” without UI verification "
                f"(no screenshot/browser/observation)",
                after=checkpoint - 1 if checkpoint else 0,
            )
        elif category == "api":
            require(
                "api",
                sentence,
                evidence.api_ok_steps,
                f"API claim “{sentence[:90]}” without a successful API call as evidence",
                after=checkpoint,
            )

    # Acceptance criteria without any matching category claim but no evidence
    # at all (e.g. code exists but never verified) → only when a done-ish
    # claim was made; otherwise stay silent (precision over recall).
    if pact is not None and any(f.category == "done" for f in findings):
        for criterion in pact.acceptance:
            for category in criterion_categories(criterion.text):
                pool = {
                    "tests": evidence.tests_ok_steps,
                    "ui": evidence.visual_ok_steps,
                    "api": evidence.api_ok_steps,
                    "works": sorted(
                        evidence.tests_ok_steps + evidence.visual_ok_steps + evidence.api_ok_steps
                    ),
                }[category]
                if evidence.last_ok_after(pool, checkpoint) is None and not any(
                    f.criterion_id == criterion.id for f in findings
                ):
                    findings.append(
                        GuardFinding(
                            category=category,
                            claim=criterion.text[:120],
                            missing=(
                                f"acceptance criterion [{criterion.id}] “{criterion.text[:110]}” "
                                f"has no matching evidence"
                            ),
                            criterion_id=criterion.id,
                        )
                    )

    if findings:
        unsupported = any(not f.criterion_id and f.category in ("done", "tests") for f in findings)
        status = "unsupported" if unsupported else "needs_verification"
    else:
        status = "ok"
    return GuardVerdict(
        status=status,
        findings=findings,
        assumptions=assumptions,
        claims_checked=len(claims),
    )


def judge_completion(
    client: LLMClient,
    registry: PromptRegistry,
    *,
    final_text: str,
    evidence_digest: str,
    budget: BudgetTracker | None = None,
) -> list[GuardFinding]:
    """Optional LLM second opinion; returns extra findings only (fail-open)."""
    try:
        result = run_prompt(
            client,
            registry,
            "completion_check",
            {"final_text": final_text[:6000], "evidence_digest": evidence_digest[:4000]},
            budget=budget,
        )
    except Exception:
        return []
    data = result.parsed if isinstance(result.parsed, dict) else {}
    raw = data.get("unverified")
    if not isinstance(raw, list):
        return []
    findings: list[GuardFinding] = []
    for item in raw[:5]:
        if not isinstance(item, dict):
            continue
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < 0.6:
            continue
        quote = str(item.get("quote", ""))[:140]
        missing = str(item.get("missing", ""))[:200]
        if quote and missing:
            findings.append(
                GuardFinding(
                    category="llm",
                    claim=quote,
                    missing=f"unbacked claim per judge: “{quote}” — {missing}",
                )
            )
    return findings


def evidence_digest(store: EventStore, session_id: str, *, limit: int = 40) -> str:
    """Short deterministic digest of tool activity for the LLM judge."""
    lines: list[str] = []
    for event in store.iter_session(session_id)[-limit:]:
        if event.kind == "tool_call":
            name = str(event.payload.get("name") or "")
            args = str(event.payload.get("args") or "")[:120]
            lines.append(f"step {event.step}: call {name} {args}")
        elif event.kind == "tool_result":
            status = "ERR" if event.payload.get("error") else "ok"
            out = str(event.payload.get("stdout") or event.payload.get("error") or "")[:100]
            lines.append(f"step {event.step}: result {status} {out}")
        elif event.kind == "file_edit":
            lines.append(f"step {event.step}: edit {event.payload.get('path')}")
    return "\n".join(lines)


def find_contradiction(
    store: EventStore, session_id: str, *, claim_lookback: int = 12
) -> dict | None:
    """Detect: success claim → the same known error recurs afterwards.

    Returns ``{"claim": …, "fingerprint": …, "claim_step": …, "error_step": …}``
    or ``None``. Deterministic; used as a pause trigger.
    """
    events = store.iter_session(session_id)
    last_claim: tuple[int, str] | None = None
    errors_before: set[str] = set()
    for event in events:
        if event.kind == "claim":
            text = str(event.payload.get("text") or "")
            categories = event.payload.get("categories") or []
            if any(cat in ("done", "tests", "works") for cat in categories):
                last_claim = (event.step, text[:160])
                continue
        if event.kind == "tool_result":
            error = event.payload.get("error") or (
                event.payload.get("stderr")
                if event.payload.get("exit_code") not in (None, 0)
                else ""
            )
            if not error:
                continue
            from .monitor import error_fingerprint

            fingerprint = error_fingerprint(str(error))
            if last_claim is not None and event.step - last_claim[0] <= claim_lookback:
                if fingerprint in errors_before:
                    return {
                        "claim": last_claim[1],
                        "fingerprint": fingerprint,
                        "claim_step": last_claim[0],
                        "error_step": event.step,
                    }
            errors_before.add(fingerprint)
    return None
