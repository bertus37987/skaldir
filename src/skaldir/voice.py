"""The Voice (V0) — hint budget, dedup, and template synthesis.

System One (Jev) reaches Voice **only** through
:class:`skaldir.system_one_service.SystemOneService`, the one facade (§33):
``Voice -> SystemOneService -> SystemOneEvaluator``. This module never imports a
provider or an HTTP client (§3) — the advisory reading arrives as the internal
evaluation contract, and the deterministic hint is computed first and returned
unchanged.

See docs/CORE-PLAN.md §1.5/§1.6. V0 synthesizes hints from deterministic
signals with fixed templates; LLM-backed triage/synthesis (prompts/) lands in
V0.5 behind the same interface. Precision over recall: never repeat the same
fingerprint, respect a hard per-task budget and a minimum gap.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

from .monitor import Signal
from .system_one_policy import ROUTE_HINT, SystemOneContractError, propose_route

DEFAULT_BUDGET = 5
DEFAULT_MIN_GAP = 2

CONFIDENCE: dict[str, float] = {
    "loop": 0.70,
    "error": 0.75,
    "scope": 0.60,
    "slop": 0.85,
    "budget": 0.50,
}

_TEMPLATES: dict[str, str] = {
    "loop": (
        "Probable loop: {message}. Suggestion: clarify the cause instead of repeating the "
        "same call — read the last error message in full, check your assumptions "
        "or change the approach."
    ),
    "error_burst": (
        "Recurring error: {message}. Suggestion: check the known error class "
        "(incident memory), use the error message as a search term, build a minimal repro."
    ),
    "scope_drift": (
        "Scope note: {message}. Per the pact the file lies outside the agreed "
        "area. Suggestion: extend it deliberately (ask the user) or revert it."
    ),
    "slop_todo_placeholder": (
        "Placeholder instead of finished code: {message}. Implement it or honestly "
        "track it as an open item in the ledger."
    ),
    "slop_stub": (
        "Unimplemented stub: {message}. No silent `NotImplementedError` in the path — "
        "implement it or adjust the pact."
    ),
    "slop_bare_except": (
        "Error is swallowed: {message}. Suggestion: catch a concrete exception or "
        "propagate the error."
    ),
    "slop_except_pass": (  # nosec B105 — key name contains "pass", not a credential
        "Error is swallowed: {message}. Suggestion: catch a concrete exception or "
        "propagate the error."
    ),
    "slop_assert_true": (
        "Test theatre detected: {message}. The test asserts nothing — write a real assertion."
    ),
    "slop_tbd_comment": (
        "TBD in code: {message}. Decide and document it before completion."
    ),
    "hallucinated_api": (
        "Probably an invented API: {message} — verify it against the real source."
    ),
    "known_incident_repeat": (
        "Known error is repeating: {message} — use incident memory."
    ),
    "diff_churn": (
        "Diff grows without progress: {message}"
    ),
}


# --------------------------------------------------------------------------- #
# intervention cost model — spec: intervene only when                        #
# ``expected cost of continuing > expected cost of the intervention``         #
# --------------------------------------------------------------------------- #

# ESTIMATED values (explicitly derived, never presented as measurements):
EST_TOKENS_PER_STEP = 2200  # per-step context cost of a stuck agent
EST_PAUSE_COST = 3500  # pause + fresh investigator + condensed redirect


@dataclass(frozen=True)
class InterventionAssessment:
    """Deterministic cost sanity check before a pause is spent."""

    intervene: bool
    trigger_kind: str
    continuation_tokens: float
    intervention_tokens: float
    measured: dict
    estimated: dict


# Strictness multiplies the estimated continuation cost (P4 setting): strict
# intervenes earlier, relaxed stays its hand — thresholds shift alongside.
_STRICTNESS_FACTOR = {"relaxed": 0.6, "standard": 1.0, "strict": 1.7}


def assess_intervention(
    *,
    trigger_kind: str,
    severity: int,
    occurrences: int,
    steps_in_window: int = 0,
    strictness: str = "standard",
) -> InterventionAssessment:
    """Cheap gate: is a pause worth it for this trigger right now?

    MEASURED: occurrences, severity, observed window span.
    ESTIMATED: token costs (tokens-per-step × severity factor × occurrences).
    """
    severity_factor = {1: 0.6, 2: 1.0, 3: 2.2}.get(severity, 1.0)
    capped = min(max(occurrences, 1), 6)
    factor = _STRICTNESS_FACTOR.get(strictness, 1.0)
    continuation = EST_TOKENS_PER_STEP * severity_factor * capped * factor
    measured = {
        "occurrences": occurrences,
        "severity": severity,
        "steps_in_window": steps_in_window,
    }
    estimated = {
        "continuation_tokens": round(continuation),
        "intervention_tokens": EST_PAUSE_COST,
        "basis": {"tokens_per_step": EST_TOKENS_PER_STEP, "pause_cost": EST_PAUSE_COST},
        "method": (
            "tokens_per_step × severity_factor × occurrences (capped at 6)"
            f" × strictness_factor({strictness}={factor})"
        ),
    }
    return InterventionAssessment(
        intervene=continuation > EST_PAUSE_COST,
        trigger_kind=trigger_kind,
        continuation_tokens=round(continuation),
        intervention_tokens=EST_PAUSE_COST,
        measured=measured,
        estimated=estimated,
    )


@dataclass
class Hint:
    id: str
    sig: str
    kind: str
    severity: int
    confidence: float
    text: str
    evidence: list[str] = field(default_factory=list)
    pos: int = 0
    total: int = 0


# --------------------------------------------------------------------------- #
# NO_INTERVENTION_NEEDED — healthy debugging is a valid investigator result    #
# --------------------------------------------------------------------------- #

# Triggers where "the agent is actually debugging well" is a plausible reading.
# Security/contradiction/capability triggers are NEVER softened here: those
# findings must not be talked away (`pause.py`: no pause path grants anything).
SOFTENABLE_TRIGGERS = frozenset(
    {"loop", "error_burst", "strategy_repeat", "known_incident_repeat"}
)

_VERIFICATION_HINT = re.compile(
    r"(?i)\b(?:pytest|ruff|mypy|pyright|black|eslint|tsc|npm\s+run\s+(?:build|test|lint)|"
    r"cargo\s+(?:test|check|build)|go\s+(?:test|build|vet)|make\s+(?:test|check|build)|"
    r"tox|jest|vitest|phpunit|ctest)\b"
)


@dataclass(frozen=True)
class TrajectoryHealth:
    """Deterministic reading: healthy debugging, or a genuinely stuck run?

    Four markers, all read from the real event trajectory (never guessed):

    ``new_hypothesis``
        more than one *distinct* error signature in the window — the agent is
        exploring, not hammering one wall.
    ``new_evidence``
        information that was not already on the table (a green run, a new
        observation). Hard precondition: without evidence nothing may be
        declared healthy.
    ``localized_fix``
        an edit to a file that the window's errors actually name — a concrete,
        localised repair attempt instead of broad rewriting.
    ``verified_after_change``
        a verification run *after* the last edit — the change was checked.

    ``healthy`` requires the evidence precondition **and** at least three of
    the four markers. Deliberately strict: this verdict lets a pause close
    without a redirect, so it must rest on evidence, not on hope.
    """

    window_steps: int = 0
    distinct_errors: int = 0
    new_evidence: int = 0
    localized_fix: bool = False
    verified_after_change: bool = False
    edits: int = 0

    @property
    def new_hypothesis(self) -> bool:
        return self.distinct_errors >= 2

    @property
    def markers(self) -> int:
        return sum(
            (
                self.new_hypothesis,
                self.new_evidence >= 1,
                self.localized_fix,
                self.verified_after_change,
            )
        )

    @property
    def healthy(self) -> bool:
        return self.new_evidence >= 1 and self.markers >= 3

    def reason(self) -> str:
        if not self.healthy:
            return "Investigation: not enough evidence for a healthy trajectory."
        parts = []
        if self.new_hypothesis:
            parts.append(f"{self.distinct_errors} distinct error signatures (new hypothesis)")
        parts.append(f"{self.new_evidence} new observation(s)")
        if self.localized_fix:
            parts.append("localised fix in the named file")
        if self.verified_after_change:
            parts.append("verification after the change")
        return "normal recoverable debugging — " + "; ".join(parts)

    def as_refs(self) -> dict:
        return {
            "window_steps": self.window_steps,
            "distinct_errors": self.distinct_errors,
            "new_evidence": self.new_evidence,
            "localized_fix": self.localized_fix,
            "verified_after_change": self.verified_after_change,
            "markers": self.markers,
        }


def _event_error_text(payload: dict) -> str:
    from .monitor import error_text

    return error_text(payload)


def read_trajectory_health(
    events: Iterable,
    *,
    until_step: int,
    window: int = 12,
) -> TrajectoryHealth:
    """Read the last ``window`` steps up to ``until_step`` (deterministic)."""
    from .monitor import error_fingerprint, is_coarse_error_text

    recent = [
        event
        for event in events
        if event.kind in ("tool_call", "tool_result", "file_edit")
        and until_step - window < event.step <= until_step
    ]
    if not recent:
        return TrajectoryHealth(window_steps=0)

    error_fps: list[str] = []
    error_texts: list[str] = []
    evidence = 0
    last_edit_step = 0
    edit_paths: list[str] = []
    # A tool_result rarely carries the command/tool itself — the *call* does.
    # Attribute each result to its nearest preceding call, otherwise the
    # verification marker would be structurally unreachable.
    call_command: list[tuple[int, str]] = []

    for event in recent:
        if event.kind == "tool_call":
            command = str(event.payload.get("command") or "")
            tool = str(event.payload.get("name") or "")
            args = event.payload.get("args")
            if isinstance(args, dict) and not command:
                command = str(args.get("command") or args.get("cmd") or "")
            call_command.append((event.step, f"{command} {tool}".strip()))
            continue
        if event.kind == "file_edit":
            path = str(event.payload.get("path") or "")
            if path:
                edit_paths.append(path)
                last_edit_step = max(last_edit_step, event.step)
            continue
        if event.kind != "tool_result":
            continue
        text = _event_error_text(event.payload)
        if text:
            # A bare exit status carries no identity — never a "hypothesis".
            if not is_coarse_error_text(text):
                error_fps.append(error_fingerprint(text))
                error_texts.append(text)
            continue
        # a success carried information (a green run is evidence even when quiet)
        evidence += 1

    def _call_before(step: int) -> str:
        candidate = ""
        for call_step, label in call_command:
            if call_step < step:
                candidate = label
            else:
                break
        return candidate

    result_text = " ".join(error_texts).lower()
    localized = False
    for path in edit_paths:
        stem = Path(path).stem.lower()
        if len(stem) >= 4 and stem in result_text:
            localized = True
            break
        if path and path.lower() in result_text:
            localized = True
            break

    verified = False
    if last_edit_step:
        for event in recent:
            if event.kind != "tool_result" or event.step <= last_edit_step:
                continue
            if _event_error_text(event.payload):
                continue
            tool = str(event.payload.get("tool") or event.payload.get("name") or "")
            label = f"{tool} {_call_before(event.step)}"
            if _VERIFICATION_HINT.search(label):
                verified = True
                break

    return TrajectoryHealth(
        window_steps=len(recent),
        distinct_errors=len(set(error_fps)),
        new_evidence=evidence,
        localized_fix=localized,
        verified_after_change=verified,
        edits=len(edit_paths),
    )


class HealthyDebuggingRunner:
    """Investigator wrapper: healthy debugging may close without a redirect.

    ``NO_INTERVENTION_NEEDED`` is a valid and important investigator result
    (Block 5). The investigator already returns it via the LLM verdict; this
    wrapper adds the *deterministic* basis for it, so the self-restraint path
    is reachable without a model — and stays conservative:

    * only for soft trajectory triggers (never for security, contradictions
      or capability gaps — those findings are not talked away),
    * only when the LLM did not itself produce a redirect,
    * only on evidence (see :class:`TrajectoryHealth`).
    """

    def __init__(self, primary, *, events_from=None, enabled: bool = True) -> None:
        self.primary = primary
        self.events_from = events_from
        self.enabled = enabled

    def run(self, *, session_id, base_step, trigger_kind, trigger_fp, problem):
        result = self.primary.run(
            session_id=session_id,
            base_step=base_step,
            trigger_kind=trigger_kind,
            trigger_fp=trigger_fp,
            problem=problem,
        )
        try:
            if (
                self.enabled
                and result is not None
                and getattr(result, "status", "") == "ok"
                and getattr(result, "intervention_needed", True)
                and not getattr(result, "used_llm", False)
                and trigger_kind in SOFTENABLE_TRIGGERS
                and self.events_from is not None
            ):
                health = read_trajectory_health(
                    self.events_from(session_id), until_step=base_step
                )
                if health.healthy:
                    result.intervention_needed = False
                    result.no_intervention_reason = health.reason()
                    result.evidence = [
                        f"health: {key}={value}"
                        for key, value in health.as_refs().items()
                    ][:4]
        except Exception:
            # fail-safe: on any doubt the investigator's own verdict stands
            pass
        return result

    def __getattr__(self, item):
        return getattr(self.primary, item)


class Voice:
    """Decides what (if anything) is worth saying — precision over recall."""
    def __init__(self, budget: int = DEFAULT_BUDGET, min_gap: int = DEFAULT_MIN_GAP) -> None:
        self.budget = budget
        self.min_gap = min_gap
        self._delivered = 0
        self._seen: set[str] = set()
        self._last_step: int | None = None

    @property
    def delivered(self) -> int:
        return self._delivered

    def consider(
        self,
        signals: list[Signal],
        step: int,
        evidence: list[str] | None = None,
    ) -> Hint | None:
        """Pick at most one hint for this step, or ``None`` (silence)."""
        if not signals:
            return None
        if self._delivered >= self.budget:
            return None
        if self._last_step is not None and step - self._last_step < self.min_gap:
            return None
        candidates = [s for s in signals if s.fingerprint not in self._seen]
        if not candidates:
            return None
        # highest severity wins; first occurrence wins ties (stable order)
        best = candidates[0]
        for candidate in candidates[1:]:
            if candidate.severity > best.severity:
                best = candidate
        self._delivered += 1
        self._seen.add(best.fingerprint)
        self._last_step = step
        prefix = best.kind.split("_", 1)[0]
        confidence = CONFIDENCE.get(prefix, 0.60)
        template = _TEMPLATES.get(best.kind, "Signal: {message}.")
        return Hint(
            id=str(self._delivered),
            sig=best.fingerprint,
            kind=best.kind,
            severity=best.severity,
            confidence=confidence,
            text=template.format(message=best.message),
            evidence=list(evidence or []),
            pos=self._delivered,
            total=self.budget,
        )


# --------------------------------------------------------------------------- #
# System-One triage hook (ABNAHME Teil VIII §47/§48, JEV-20 §98)               #
# --------------------------------------------------------------------------- #
#
# Voice is the System-2 supervisor: it intervenes when the trajectory needs it.
# System One (Jev) may *read* the situation faster, but it holds no authority
# (§36): the deterministic hint below stays exactly as it is, and the System-One
# reading is attached as an advisory record only.
#
# Deactivation is the point of this hook: with the flags off (the default) — or
# with an unknown provider, no evaluation, no signals to triage — the existing
# deterministic path is taken and nothing changes (§98).

PATH_LEGACY = "legacy"
PATH_SHADOW = "shadow"
PATH_ADVISORY = "system_one_advisory"


@dataclass(frozen=True)
class TriageOutcome:
    """One triage step: the deterministic hint plus which path produced it.

    ``advisory`` is a *recording* (answers, provider, honesty labelling) — it
    is never an action and never replaces ``hint``.
    """

    hint: Hint | None
    path: str = PATH_LEGACY
    reason: str = ""
    advisory: dict | None = None


def advisory_from_evaluation(evaluation, *, shadow: bool = False) -> dict | None:
    """Flatten an evaluation into an advisory record (no authority implied).

    The record carries the reading plus the §32/§47 route the reading
    *proposes* (:func:`skaldir.system_one_policy.propose_route`) — a proposal
    that the caller may ignore, never an action (``route_proposal``).
    """
    if evaluation is None:
        return None
    reading: dict[str, dict] = {}
    for question_id, answer in dict(getattr(evaluation, "answers", {})).items():
        reading[question_id] = {
            "kind": getattr(answer, "kind", ""),
            "value": getattr(answer, "value", None),
            "choice": getattr(answer, "choice", None),
            # absent for Noul answers by contract — never invented here
            "confidence": getattr(answer, "confidence", None),
        }
    proposed = propose_route(evaluation)
    return {
        "mode": "shadow" if shadow else "advisory",
        "provider": getattr(evaluation, "provider", ""),
        "provider_model": getattr(evaluation, "provider_model", ""),
        "provider_mode": getattr(evaluation, "provider_mode", ""),
        "live_jev": bool(getattr(evaluation, "live_jev", False)),
        "evaluation_id": getattr(evaluation, "evaluation_id", ""),
        "route_proposal": proposed.route,
        "rule": proposed.rule,
        "basis": list(proposed.basis),
        "via": "system_one_evaluator",
        "reading": reading,
    }


def triage_with_system_one(
    *,
    voice: Voice,
    signals,
    step: int,
    evidence: list[str] | None = None,
    flags=None,
    evaluation=None,
    service=None,
) -> TriageOutcome:
    """The ONLY place Voice consults System One (§27: no second contract).

    Voice reaches System One strictly through
    :class:`skaldir.system_one_service.SystemOneService` — the one facade
    (``Voice -> SystemOneService -> SystemOneEvaluator``, §33). ``service`` is
    the facade instance the caller used; it is recorded on the advisory
    (``via``) so a run can be traced to the exact edge that produced the
    reading. This module never imports a provider (proven by
    ``tests/test_jev_service_facade.py``).

    The deterministic :meth:`Voice.consider` result is always computed first
    and always returned unchanged. Whether an advisory reading is attached
    depends solely on the flags:

    * flags absent/off, ``provider='disabled'``, flag ``voice_triage`` off,
      no evaluation, or nothing to triage → :data:`PATH_LEGACY`;
    * ``shadow_mode`` → :data:`PATH_SHADOW` (recorded, not consumed);
    * ``enabled`` + ``voice_triage`` → :data:`PATH_ADVISORY`.
    """
    hint = voice.consider(signals, step, evidence=evidence)
    if flags is None:
        return TriageOutcome(hint=hint, path=PATH_LEGACY, reason="no_flags")
    if not getattr(flags, "enabled", False):
        return TriageOutcome(hint=hint, path=PATH_LEGACY, reason="system_one_disabled")
    if getattr(flags, "provider", "disabled") == "disabled":
        return TriageOutcome(hint=hint, path=PATH_LEGACY, reason="provider_disabled")
    if evaluation is None:
        return TriageOutcome(hint=hint, path=PATH_LEGACY, reason="no_evaluation")
    if hint is None:
        # nothing to triage: an advisory reading would attach to no decision
        return TriageOutcome(hint=None, path=PATH_LEGACY, reason="no_hint")
    shadow = bool(getattr(flags, "shadow_mode", False))
    advisory = advisory_from_evaluation(evaluation, shadow=shadow)
    if advisory is not None and service is not None:
        advisory["via"] = "system_one_service"
    if shadow:
        return TriageOutcome(hint=hint, path=PATH_SHADOW, reason="shadow_mode", advisory=advisory)
    if not getattr(flags, "voice_triage", False):
        return TriageOutcome(hint=hint, path=PATH_LEGACY, reason="voice_triage_disabled")
    return TriageOutcome(hint=hint, path=PATH_ADVISORY, reason="advisory", advisory=advisory)


# --------------------------------------------------------------------------- #
# System-One shadow path (§45/JEV-10, E2E-13 §21)                               #
# --------------------------------------------------------------------------- #
#
# The shadow path runs an evaluator *beside* the deterministic triage: the same
# Voice snapshot goes through both, the deterministic hint is what the run
# keeps, and the comparison (disagreement, latency, usage) is stored on the
# shadow record. The §45 rules are not re-implemented here — ShadowHarness owns
# them and asserts them; this function only drives it.
#
# The flags stay the gate (§98): with the flags absent, off, or on a disabled
# provider, the result is byte-identical to today's ``triage_with_system_one``
# path — same hint, same path, same reason, and the same Voice budget effects.


def _hint_reference(hint: Hint) -> dict:
    """The deterministic reading a shadow record compares against (§49)."""
    return {
        "id": hint.id,
        "kind": hint.kind,
        "sig": hint.sig,
        "severity": int(hint.severity),
        "confidence": float(hint.confidence),
        "text": hint.text,
    }


async def triage_with_shadow(
    *,
    voice: Voice,
    signals,
    step: int,
    evidence: list[str] | None = None,
    flags=None,
    harness=None,
    state=None,
    questions=None,
    context=None,
    live=None,
) -> TriageOutcome:
    """Deterministic triage plus an optional shadow record (§45/JEV-10).

    Returns :data:`PATH_SHADOW` with the deterministic hint and the shadow
    record attached when the shadow really ran. In every other case the
    existing path is taken, with the existing reason:

    * flags absent/off or ``provider='disabled'`` → the legacy outcome, exactly
      as ``triage_with_system_one`` produces it today;
    * ``shadow_mode`` off → legacy, reason ``shadow_mode_disabled``;
    * nothing to triage (no hint) or nothing wired → legacy, reason ``no_hint``
      / ``no_shadow_harness``;
    * a provider failure inside the shadow → legacy, reason ``shadow_error:<Type>``
      — a shadow must never break the run (§43). A *contract* violation of the
      shadow rules (a mutating evaluator) is not degraded: it propagates (§45).
    """
    hint = voice.consider(signals, step, evidence=evidence)
    if flags is None:
        return TriageOutcome(hint=hint, path=PATH_LEGACY, reason="no_flags")
    if not getattr(flags, "enabled", False):
        return TriageOutcome(hint=hint, path=PATH_LEGACY, reason="system_one_disabled")
    if getattr(flags, "provider", "disabled") == "disabled":
        return TriageOutcome(hint=hint, path=PATH_LEGACY, reason="provider_disabled")
    if not getattr(flags, "shadow_mode", False):
        return TriageOutcome(hint=hint, path=PATH_LEGACY, reason="shadow_mode_disabled")
    if hint is None:
        # nothing to triage: there is no baseline a shadow could be compared to
        return TriageOutcome(hint=None, path=PATH_LEGACY, reason="no_hint")
    if harness is None or state is None or questions is None or context is None:
        return TriageOutcome(hint=hint, path=PATH_LEGACY, reason="no_shadow_harness")
    try:
        record = await harness.run(
            state=state,
            questions=questions,
            # a shadow-labelled context: the evaluation must not be readable as
            # an active one (§49) — the caller's context object stays untouched
            context=replace(context, shadow=True),
            baseline_route=ROUTE_HINT,
            baseline_hint=_hint_reference(hint),
            live=live,
        )
    except SystemOneContractError:
        raise  # a shadow that breaks the §45 rules must be loud, not degraded
    except Exception as exc:  # noqa: BLE001 — a provider must never break the run
        return TriageOutcome(
            hint=hint, path=PATH_LEGACY, reason=f"shadow_error:{type(exc).__name__}"
        )
    advisory = advisory_from_evaluation(harness.last_evaluation, shadow=True)
    if advisory is not None:
        advisory["shadow_record"] = record
    return TriageOutcome(hint=hint, path=PATH_SHADOW, reason="shadow_mode", advisory=advisory)
