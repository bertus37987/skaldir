"""Computer-Use System-One packs (§10.3) and the visual subtool contract (§11).

This is the "Computer Use + Provider" half of the Jev binding order. It extends
the existing Computer-Use stack — the question registry
(:mod:`skaldir.system_one_questions`), the GUIState contract
(:mod:`skaldir.system_one_guistate`) and the gate module
(:mod:`skaldir.computer_use`) — and adds **no** second orchestrator, no second
policy engine and no store of its own: the only thing this module owns is the
data contract of a reusable GUI macro and the atomic questions asked about it.

Three rules are enforced here in code, not by convention:

1. **A candidate ``Choice`` may only contain already-allowed options (§10.3).**
   :func:`allowed_candidate_options` never adds an option; the productive
   per-step composition (:func:`compose_computer_use_questions`) refuses to
   build a pack at all when the allowed set is not stated, and
   :func:`validate_candidate_choice` refuses a provider answer naming anything
   else — including click coordinates and tool names, which Jev may never
   invent (§53). The documented option set is fixed:
   ``candidate_a, candidate_b, keyboard_shortcut, reobserve, escalate, abort``.

2. **Blind macro replay stays forbidden (§11/§56).** A stored subtool is a
   state machine, not a click script: :func:`check_blind_replay` reports which
   start conditions and anchors are *not currently observed*, and
   :func:`assert_no_blind_replay` raises instead of replaying. A missing anchor
   is a FAIL case, never a reason to keep clicking.

3. **An inapplicable app version or locale is refused, never guessed (§12).**
   An unknown/empty version is *not* applicable; an unsupported locale is not
   "close enough".

The subtool contract carries all 16 fields: the 15 named by the goal's §11 list
(``capability_id`` … ``system_one_postchecks``) plus ABNAHME §55's third
optional, ``system_one_recovery_questions``. The goal text calls this "the
16-field contract"; the list it prints has 15 names, and §55 adds the 16th.

The six Jev checks of §11 live in :func:`evaluate_subtool_step` and produce one
decision: proceed / reobserve / escalate. A decision never carries a raw
coordinate or a shell command — only an action id that the subtool already
declared allowed.

Honest status: everything here is contract + test-double evidence. There is no
live GUI session on this host — ``skaldir cu check`` reports
``container GUI (godsv-cu-x11): nicht verfügbar (Image fehlt:
localhost/godsv-cu-x11:latest)`` — so no subtool here has been replayed against
a real application, and the anchors in the tests are declared data, not
observed pixels.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .computer_use import ESCALATION_TRIGGERS
from .system_one import SystemOneContractError
from .system_one_questions import Question, QuestionPack, QuestionRegistry, QuestionSet

# --------------------------------------------------------------------------- #
# §10.3 — the full Computer-Use question contract                              #
# --------------------------------------------------------------------------- #

COMPUTER_USE_PACK_ID = "computer_use"
#: v2 supersedes the 5-question v1 pack of §10.3's predecessor. A pack whose
#: question set changes incomparably must bump its version (§5), and it does.
COMPUTER_USE_PACK_VERSION = "v2"

_OWNER = "computer_use"

#: §10.3 pre-action.
PRE_ACTION_QUESTIONS: tuple[str, ...] = (
    "correct_application",
    "correct_window",
    "correct_document",
    "blocking_dialog_present",
    "state_sufficient_for_action",
    "stored_subtool_anchor_valid",
)

#: §10.3 post-action.
POST_ACTION_QUESTIONS: tuple[str, ...] = (
    "previous_action_succeeded",
    "expected_transition_occurred",
    "unexpected_transition_occurred",
    "wrong_target_changed",
    "reobserve_required",
)

#: §10.3 recovery.
RECOVERY_QUESTIONS: tuple[str, ...] = (
    "retry_safe",
    "alternate_candidate_needed",
    "state_recoverable",
    "system2_needed",
    "user_takeover_detected",
)

#: §10.3 candidate choice — the only options a provider may ever be offered.
CANDIDATE_CHOICE_OPTIONS: tuple[str, ...] = (
    "candidate_a",
    "candidate_b",
    "keyboard_shortcut",
    "reobserve",
    "escalate",
    "abort",
)

#: v1 ids that the rest of the runtime already reads. ``system_one_policy``
#: resolves ``computer_use.recovery_needed`` in its System-2 route rule, so
#: replacing the v1 pack must not strip it: it stays in the pack as an
#: explicitly marked legacy question instead of disappearing silently.
LEGACY_COMPUTER_USE_QUESTION_IDS: tuple[str, ...] = ("computer_use.recovery_needed",)

CANDIDATE_CHOICE_QUESTION_ID = "computer_use.choice.candidate_action"
LEGACY_RECOVERY_QUESTION_ID = LEGACY_COMPUTER_USE_QUESTION_IDS[0]

_LEGACY_GROUP = "recovery_legacy"


class CandidateChoiceViolation(SystemOneContractError):
    """A candidate ``Choice`` offered or answered an option that is not allowed.

    §10.3: ``Choice`` may contain *exclusively* already-allowed options. Jev
    never invents coordinates, shell commands or tool names (§53) — so this is a
    contract violation, not something to coerce into the nearest allowed value.
    """


class SubtoolContractError(SystemOneContractError):
    """A visual subtool that is missing a §11 field or breaks the state machine."""


def _text(name: str) -> str:
    """One atomic question text per contract name, always ending in one ``?``."""
    sentences = {
        "correct_application": "Is the focused application the one this step targets?",
        "correct_window": "Is the focused window the one this step targets?",
        "correct_document": "Is the open document the one this step targets?",
        "blocking_dialog_present": "Is a blocking dialog covering the window now?",
        "state_sufficient_for_action": "Is the observed state sufficient to act?",
        "stored_subtool_anchor_valid": "Is the stored subtool anchor still present?",
        "previous_action_succeeded": "Did the previous action succeed?",
        "expected_transition_occurred": "Did the expected transition occur?",
        "unexpected_transition_occurred": "Did an unexpected transition occur?",
        "wrong_target_changed": "Did an action change the wrong target?",
        "reobserve_required": "Is a fresh observation required before the next step?",
        "retry_safe": "Is retrying the last action safe here?",
        "alternate_candidate_needed": "Is an alternative candidate action needed?",
        "state_recoverable": "Is the current state recoverable?",
        "system2_needed": "Should System Two take over this step?",
        "user_takeover_detected": "Is a user takeover of this window detectable?",
    }[name]
    return sentences


def _question(name: str, group: str, *, version: str = COMPUTER_USE_PACK_VERSION) -> Question:
    return Question(
        id=f"computer_use.{group}.{name}",
        version=version,
        owner=_OWNER,
        kind="noul",
        text=_text(name),
        group=group,
    )


def computer_use_pack() -> QuestionPack:
    """The complete §10.3 Computer-Use pack (registry catalogue, 18 questions).

    The candidate ``Choice`` in the catalogue carries the documented six options
    because it is the *declaration*. A productive call never uses it as-is: it
    goes through :func:`compose_computer_use_questions`, which narrows the
    options to the already-allowed set.
    """
    questions: list[Question] = []
    questions += [_question(name, "pre") for name in PRE_ACTION_QUESTIONS]
    questions += [_question(name, "post") for name in POST_ACTION_QUESTIONS]
    questions += [_question(name, "recovery") for name in RECOVERY_QUESTIONS]
    questions.append(
        Question(
            id=CANDIDATE_CHOICE_QUESTION_ID,
            version=COMPUTER_USE_PACK_VERSION,
            owner=_OWNER,
            kind="choice",
            text="Which already-allowed candidate action should run next?",
            group="candidate_choice",
            options=CANDIDATE_CHOICE_OPTIONS,
        )
    )
    questions.append(
        Question(
            id=LEGACY_RECOVERY_QUESTION_ID,
            version="v1",
            owner=_OWNER,
            kind="noul",
            text="Is a recovery action needed?",
            group=_LEGACY_GROUP,
        )
    )
    return QuestionPack(
        id=COMPUTER_USE_PACK_ID,
        version=COMPUTER_USE_PACK_VERSION,
        owner=_OWNER,
        purpose=(
            "Structured GUI pre/postcondition reading for the complete §10.3 pack: right window, "
            "expected transition, reobserve or recover. Never a screenshot or vision replacement."
        ),
        applicable_states=("gui_state", "post_action"),
        risk_class="R3",  # it reads a real-effect step; the action set stays pre-approved
        fallback_policy=(
            "No advisory: the runtime action policy decides, and it never widens the allowed set."
        ),
        # declared dataset *name* (same convention as COMPUTER_USE_V1/VOICE_V1) - it is not a
        # measurement and no number is derived from it
        calibration_dataset_version="computer_use-dev-2026-09-16",
        questions=tuple(questions),
    )


def register_computer_use_pack(registry: QuestionRegistry) -> QuestionPack:
    """Register the §10.3 pack into the repo's existing question registry.

    The registry refuses a duplicate pack id, so the integrator's wiring is:
    register *this* pack for ``computer_use`` instead of the legacy 5-question
    v1 pack (the legacy id the runtime reads is still inside — see
    :data:`LEGACY_COMPUTER_USE_QUESTION_IDS`).
    """
    pack = computer_use_pack()
    try:
        registry.register(pack)
    except SystemOneContractError as exc:
        raise SubtoolContractError(
            f"cannot register {pack.ref}: {exc} — replace the legacy computer_use "
            "v1 pack with this one (its legacy ids are still included)"
        ) from exc
    return pack


def allowed_candidate_options(allowed_candidates: Iterable[str]) -> tuple[str, ...]:
    """The documented options ∩ the already-allowed candidates — never more.

    An option that is not part of the documented set (a coordinate, a tool name,
    a shell command) can never appear here, and nothing is ever added.
    """
    allowed = {str(item) for item in (allowed_candidates or ())}
    return tuple(option for option in CANDIDATE_CHOICE_OPTIONS if option in allowed)


def validate_candidate_choice(choice: str, *, allowed_candidates: Iterable[str]) -> str:
    """Accept a provider's candidate choice only if it was already allowed."""
    offered = allowed_candidate_options(allowed_candidates)
    if not offered:
        raise CandidateChoiceViolation(
            "no already-allowed candidate option was offered — a candidate choice "
            "without an allowed set can never be answered"
        )
    if str(choice) not in offered:
        raise CandidateChoiceViolation(
            f"candidate choice {choice!r} is not among the already-allowed options "
            f"{offered} — Jev never invents an action (§53/§10.3)"
        )
    return str(choice)


def compose_computer_use_questions(
    *,
    allowed_candidates: Iterable[str],
    groups: Iterable[str] | None = None,
) -> QuestionSet:
    """The per-step question set, with the candidate ``Choice`` narrowed.

    ``allowed_candidates`` is required: a productive candidate choice has to
    state the already-allowed options, otherwise the provider would be able to
    pick something nobody allowed. An empty allowed set is refused.
    """
    offered = allowed_candidate_options(allowed_candidates)
    if not offered:
        raise CandidateChoiceViolation(
            "a candidate Choice must offer at least one already-allowed option — "
            f"got none from {tuple(allowed_candidates or ())!r}"
        )
    pack = computer_use_pack()
    selected = pack.questions if groups is None else pack.in_groups(groups)
    narrowed: list[Question] = []
    for question in selected:
        if question.id != CANDIDATE_CHOICE_QUESTION_ID:
            narrowed.append(question)
            continue
        narrowed.append(
            Question(
                id=question.id,
                version=question.version,
                owner=question.owner,
                kind=question.kind,
                text=question.text,
                group=question.group,
                options=offered,
            )
        )
    chosen = tuple(groups) if groups is not None else tuple(
        sorted({question.group for question in pack.questions})
    )
    return QuestionSet(
        id=pack.id,
        version=pack.version,
        owner=pack.owner,
        questions=tuple(narrowed),
        groups=chosen,
    )


# --------------------------------------------------------------------------- #
# §11 — the visual subtool contract                                            #
# --------------------------------------------------------------------------- #

#: The 16 fields of §11 (the 15 goal-named ones) + §55's third optional.
VISUAL_SUBTOOL_FIELDS: tuple[str, ...] = (
    "capability_id",
    "version",
    "supported_app_versions",
    "supported_locales",
    "start_conditions",
    "anchors",
    "allowed_actions",
    "expected_transitions",
    "completion_conditions",
    "failure_conditions",
    "reobserve_policy",
    "system2_escalation_policy",
    "verification_contract",
    "system_one_prechecks",
    "system_one_postchecks",
    "system_one_recovery_questions",
)

#: Reobserve triggers a subtool may declare (§11 ``reobserve_policy``).
REOBSERVE_TRIGGERS: tuple[str, ...] = (
    "start_condition_mismatch",
    "anchor_mismatch",
    "transition_mismatch",
    "action_failed",
)

#: Machine-readable causes a step decision reports.
CAUSE_START_CONDITION_MISSING = "start_condition_missing"
CAUSE_ANCHOR_MISSING = "anchor_mismatch"
CAUSE_EXPECTED_TRANSITION_MISSING = "expected_transition_missing"
CAUSE_NO_ALLOWED_ACTION_LEFT = "no_allowed_action_left"
CAUSE_SYSTEM2_REQUESTED = "system2_requested"
CAUSE_COMPLETION_OBSERVED = "completion_conditions_observed"
CAUSE_LOCALE_OUTSIDE_APPLICABILITY = "locale_outside_subtool_applicability"
CAUSE_VERSION_OUTSIDE_APPLICABILITY = "app_version_outside_subtool_applicability"

CAUSES: tuple[str, ...] = (
    CAUSE_START_CONDITION_MISSING,
    CAUSE_ANCHOR_MISSING,
    CAUSE_EXPECTED_TRANSITION_MISSING,
    CAUSE_NO_ALLOWED_ACTION_LEFT,
    CAUSE_SYSTEM2_REQUESTED,
    CAUSE_COMPLETION_OBSERVED,
    CAUSE_LOCALE_OUTSIDE_APPLICABILITY,
    CAUSE_VERSION_OUTSIDE_APPLICABILITY,
)


def _version_matches(spec: str, version: str) -> bool:
    """``4.1.*`` prefix, ``>=4.1,<5`` range, or exact match. Nothing else."""
    spec = spec.strip()
    if not spec or not version:
        return False
    if spec.endswith(".*"):
        prefix = spec[:-2]
        return version == prefix or version.startswith(f"{prefix}.")
    if "," in spec or spec.startswith((">=", "<=", ">", "<")):
        return _range_matches(spec, version)
    return version == spec


def _range_matches(spec: str, version: str) -> bool:
    """Every clause of ``>=x,<y``-style specs must hold; an unparsable one fails."""
    clauses = [part.strip() for part in spec.replace(" ", ",").split(",") if part.strip()]
    if not clauses:
        return False
    actual = _version_tuple(version)
    for clause in clauses:
        operator = next((op for op in (">=", "<=", ">", "<") if clause.startswith(op)), None)
        if operator is None:
            return False
        bound = _version_tuple(clause[len(operator) :].strip())
        if operator == ">=":
            ok = actual >= bound
        elif operator == ">":
            ok = actual > bound
        elif operator == "<=":
            ok = actual <= bound
        else:
            ok = actual < bound
        if not ok:
            return False
    return True


def _version_tuple(value: str) -> tuple[int, ...]:
    numbers: list[int] = []
    for part in str(value).split("."):
        digits = "".join(char for char in part if char.isdigit())
        numbers.append(int(digits) if digits else 0)
    return tuple(numbers)


@dataclass(frozen=True)
class VisualSubtool:
    """One reusable visual capability as a **state machine** (§11/§55).

    A subtool is data: which app versions and locales it is applicable to, which
    state it starts from, which anchors it needs, which actions it may take,
    which transitions and completion/failure conditions it expects, when it
    reobserves, when System Two takes over, and how it is verified. It carries
    no executable payload — a subtool never stores a click coordinate.
    """

    capability_id: str
    version: str
    supported_app_versions: tuple[str, ...]
    supported_locales: tuple[str, ...]
    start_conditions: tuple[str, ...]
    anchors: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    expected_transitions: tuple[str, ...]
    completion_conditions: tuple[str, ...]
    failure_conditions: tuple[str, ...]
    reobserve_policy: tuple[str, ...]
    system2_escalation_policy: tuple[str, ...]
    verification_contract: str
    system_one_prechecks: tuple[str, ...] = ()
    system_one_postchecks: tuple[str, ...] = ()
    system_one_recovery_questions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "supported_app_versions",
            "supported_locales",
            "start_conditions",
            "anchors",
            "allowed_actions",
            "expected_transitions",
            "completion_conditions",
            "failure_conditions",
            "reobserve_policy",
            "system2_escalation_policy",
            "system_one_prechecks",
            "system_one_postchecks",
            "system_one_recovery_questions",
        ):
            object.__setattr__(self, name, tuple(str(item) for item in getattr(self, name)))
        if not str(self.capability_id or "").strip():
            raise SubtoolContractError("a subtool needs a capability_id (§11)")
        if not str(self.version or "").strip():
            raise SubtoolContractError(f"{self.capability_id}: a subtool needs a version (§11)")
        if not self.supported_app_versions:
            raise SubtoolContractError(
                f"{self.capability_id}: supported_app_versions must not be empty — an "
                "unsupported app version can never be 'probably fine' (§11/§12)"
            )
        if not self.supported_locales:
            raise SubtoolContractError(
                f"{self.capability_id}: supported_locales must not be empty (§11)"
            )
        if not self.start_conditions or not self.anchors:
            raise SubtoolContractError(
                f"{self.capability_id}: a subtool without start_conditions and anchors "
                "cannot be checked before it acts (§11/§50)"
            )
        if not self.allowed_actions:
            raise SubtoolContractError(f"{self.capability_id}: allowed_actions must not be empty")
        if not self.expected_transitions:
            raise SubtoolContractError(
                f"{self.capability_id}: expected_transitions must not be empty — a step "
                "whose effect is never stated cannot be verified (§11/§54.2)"
            )
        if not str(self.verification_contract or "").strip():
            raise SubtoolContractError(
                f"{self.capability_id}: a subtool needs a verification_contract (§11)"
            )
        unknown_reobserve = [
            item for item in self.reobserve_policy if item not in REOBSERVE_TRIGGERS
        ]
        if unknown_reobserve:
            raise SubtoolContractError(
                f"{self.capability_id}: unknown reobserve trigger(s) {unknown_reobserve} "
                f"(known: {REOBSERVE_TRIGGERS})"
            )
        unknown_escalation = [
            item for item in self.system2_escalation_policy if item not in ESCALATION_TRIGGERS
        ]
        if unknown_escalation:
            raise SubtoolContractError(
                f"{self.capability_id}: unknown System-2 escalation trigger(s) "
                f"{unknown_escalation} (known: {ESCALATION_TRIGGERS})"
            )
        for field_name in ("system_one_prechecks", "system_one_postchecks"):
            for question in getattr(self, field_name):
                known_questions = (
                    PRE_ACTION_QUESTIONS + POST_ACTION_QUESTIONS + RECOVERY_QUESTIONS
                )
                if question not in known_questions:
                    raise SubtoolContractError(
                        f"{self.capability_id}: {field_name} names an unknown System-One "
                        f"question {question!r}"
                    )

    @property
    def ref(self) -> str:
        return f"{self.capability_id}@{self.version}"

    def as_dict(self) -> dict:
        payload: dict[str, Any] = {}
        for name in VISUAL_SUBTOOL_FIELDS:
            value: Any = getattr(self, name)
            payload[name] = list(value) if isinstance(value, tuple) else value
        payload["ref"] = self.ref
        return payload


class SubtoolNotRegistered(SystemOneContractError):
    """No subtool for that capability id (or version)."""


class SubtoolRevoked(SystemOneContractError):
    """The subtool version was revoked — it may not run any more (§56)."""


class SubtoolRegistry:
    """``capability_id → {version: subtool}`` — the only subtool registry.

    Same discipline as the question registry: a duplicate registration is
    refused, resolution by an unknown version is an error rather than a
    fallback to a nearby version, and revocation removes a version from use.
    """

    def __init__(self) -> None:
        self._by_capability: dict[str, dict[str, VisualSubtool]] = {}
        self._revoked: set[str] = set()

    def register(self, subtool: VisualSubtool) -> VisualSubtool:
        versions = self._by_capability.setdefault(subtool.capability_id, {})
        if subtool.version in versions:
            raise SubtoolContractError(f"subtool already registered: {subtool.ref}")
        versions[subtool.version] = subtool
        return subtool

    def resolve(self, capability_id: str, *, version: str = "") -> VisualSubtool:
        versions = self._by_capability.get(capability_id)
        if not versions:
            raise SubtoolNotRegistered(f"unknown visual subtool: {capability_id!r}")
        if not version:
            if len(versions) > 1:
                raise SubtoolNotRegistered(
                    f"{capability_id}: several versions registered "
                    f"({sorted(versions)}) — name the version explicitly"
                )
            version = next(iter(versions))
        if f"{capability_id}@{version}" in self._revoked:
            raise SubtoolRevoked(f"subtool version revoked: {capability_id}@{version}")
        found = versions.get(version)
        if found is None:
            raise SubtoolNotRegistered(
                f"unknown subtool version: {capability_id}@{version} (known: {sorted(versions)})"
            )
        return found

    def revoke(self, capability_id: str, *, version: str) -> bool:
        versions = self._by_capability.get(capability_id)
        if not versions or version not in versions:
            raise SubtoolNotRegistered(f"unknown subtool version: {capability_id}@{version}")
        self._revoked.add(f"{capability_id}@{version}")
        return True

    def capabilities(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_capability))

    def manifest(self) -> dict:
        return {
            f"{capability_id}@{version}": subtool.as_dict()
            for capability_id, versions in sorted(self._by_capability.items())
            for version, subtool in sorted(versions.items())
        }


# --------------------------------------------------------------------------- #
# §11/§12 — applicability, blind-replay refusal, the six checks                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SubtoolApplicability:
    """Whether this subtool may run at all here (§11/§12)."""

    applicable: bool
    reason: str
    app_version: str = ""
    locale: str = ""
    matched_version_spec: str = ""

    def as_dict(self) -> dict:
        return {
            "applicable": self.applicable,
            "reason": self.reason,
            "app_version": self.app_version,
            "locale": self.locale,
            "matched_version_spec": self.matched_version_spec,
        }


def check_applicability(
    subtool: VisualSubtool, *, app_version: str, locale: str
) -> SubtoolApplicability:
    """Refuse an inapplicable app version/locale instead of assuming it fits.

    An empty/unknown version is never applicable: "we could not read the app
    version" is not evidence that it is the supported one.
    """
    version = str(app_version or "").strip()
    wanted_locale = str(locale or "").strip()
    matched = next(
        (spec for spec in subtool.supported_app_versions if _version_matches(spec, version)), ""
    )
    if not matched:
        return SubtoolApplicability(
            applicable=False,
            reason=(
                f"app version {version or '(unknown)'} is outside the applicability of "
                f"{subtool.ref} (supported: {list(subtool.supported_app_versions)})"
            ),
            app_version=version,
            locale=wanted_locale,
        )
    if not wanted_locale:
        return SubtoolApplicability(
            applicable=False,
            reason=(
                f"locale unknown for {subtool.ref} — a locale is never assumed "
                f"(supported: {list(subtool.supported_locales)})"
            ),
            app_version=version,
            matched_version_spec=matched,
        )
    if "*" in subtool.supported_locales or "any" in subtool.supported_locales:
        return SubtoolApplicability(
            applicable=True, reason="locale independent", app_version=version,
            locale=wanted_locale, matched_version_spec=matched,
        )
    if wanted_locale not in subtool.supported_locales:
        return SubtoolApplicability(
            applicable=False,
            reason=(
                f"locale {wanted_locale} is outside the applicability of {subtool.ref} "
                f"(supported: {list(subtool.supported_locales)})"
            ),
            app_version=version,
            locale=wanted_locale,
            matched_version_spec=matched,
        )
    return SubtoolApplicability(
        applicable=True,
        reason="applicable",
        app_version=version,
        locale=wanted_locale,
        matched_version_spec=matched,
    )


@dataclass(frozen=True)
class ReplayGuardDecision:
    """Blind-macro-replay decision (§11/§56): may this stored sequence run?"""

    replay_allowed: bool
    reason: str
    missing_start_conditions: tuple[str, ...] = ()
    missing_anchors: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "replay_allowed": self.replay_allowed,
            "reason": self.reason,
            "missing_start_conditions": list(self.missing_start_conditions),
            "missing_anchors": list(self.missing_anchors),
        }


def check_blind_replay(
    subtool: VisualSubtool,
    *,
    observed_start_conditions: Iterable[str],
    observed_anchors: Iterable[str],
) -> ReplayGuardDecision:
    """May the stored macro be replayed against what is observed *now*?

    Replay is allowed only when every declared start condition **and** every
    declared anchor is currently observed. Nothing is inferred from the previous
    step, from the app name, or from the fact that it worked last time.
    """
    conditions = {str(item) for item in (observed_start_conditions or ())}
    anchors = {str(item) for item in (observed_anchors or ())}
    missing_conditions = tuple(c for c in subtool.start_conditions if c not in conditions)
    missing_anchors = tuple(a for a in subtool.anchors if a not in anchors)
    if missing_conditions or missing_anchors:
        return ReplayGuardDecision(
            replay_allowed=False,
            reason=(
                f"blind macro replay refused for {subtool.ref}: "
                f"start_conditions={list(missing_conditions)} anchors={list(missing_anchors)} "
                "are not currently observed — reobserve or escalate, never click on (§56)"
            ),
            missing_start_conditions=missing_conditions,
            missing_anchors=missing_anchors,
        )
    return ReplayGuardDecision(
        replay_allowed=True,
        reason=f"{subtool.ref}: start conditions and anchors are currently observed",
    )


class BlindReplayRefused(SubtoolContractError):
    """A stored macro was about to be replayed without current observation."""


def assert_no_blind_replay(decision: ReplayGuardDecision) -> ReplayGuardDecision:
    """Raise instead of replaying a macro whose preconditions are not observed."""
    if not decision.replay_allowed:
        raise BlindReplayRefused(decision.reason)
    return decision


@dataclass(frozen=True)
class SubtoolStepDecision:
    """The outcome of one subtool step: act, reobserve, escalate or finish."""

    action: str
    proceed: bool
    reobserve: bool
    escalate: bool
    complete: bool
    checks: Mapping[str, bool] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    applicability: SubtoolApplicability | None = None

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "proceed": self.proceed,
            "reobserve": self.reobserve,
            "escalate": self.escalate,
            "complete": self.complete,
            "checks": dict(self.checks),
            "reasons": list(self.reasons),
            "applicability": self.applicability.as_dict() if self.applicability else None,
            "advisory_only": True,
        }


def evaluate_subtool_step(
    subtool: VisualSubtool,
    *,
    app_version: str,
    locale: str,
    observed_start_conditions: Iterable[str] = (),
    observed_anchors: Iterable[str] = (),
    observed_transitions: Iterable[str] = (),
    performed_actions: Iterable[str] = (),
    observed_completion_conditions: Iterable[str] = (),
    system2_requested: bool = False,
) -> SubtoolStepDecision:
    """The six §11 Jev checks for one step, combined in code (§82).

    ``start_condition_valid`` · ``anchor_reached`` ·
    ``expected_transition_happened`` · ``procedure_still_applicable`` ·
    ``new_observation_needed`` · ``system2_takeover``.

    The combination is ordinary, testable Python — and it can only ever return
    an action the subtool already declared allowed. When an anchor is lost or
    the expected transition is missing, the decision reobserves or escalates;
    it never proceeds.
    """
    applicability = check_applicability(subtool, app_version=app_version, locale=locale)
    guard = check_blind_replay(
        subtool,
        observed_start_conditions=observed_start_conditions,
        observed_anchors=observed_anchors,
    )
    transitions = {str(item) for item in (observed_transitions or ())}
    performed = tuple(str(item) for item in (performed_actions or ()))
    completed = {str(item) for item in (observed_completion_conditions or ())}

    completion_met = bool(subtool.completion_conditions) and all(
        condition in completed for condition in subtool.completion_conditions
    )
    got_transition = (
        True if not performed else bool(transitions & set(subtool.expected_transitions))
    )
    checks = {
        "start_condition_valid": applicability.applicable and not guard.missing_start_conditions,
        "anchor_reached": not guard.missing_anchors,
        "expected_transition_happened": got_transition,
        "procedure_still_applicable": applicability.applicable,
        "new_observation_needed": bool(guard.missing_anchors) or not got_transition,
        "system2_takeover": bool(system2_requested),
    }

    action = ""
    proceed = reobserve = escalate = complete = False
    reasons: list[str] = []

    if completion_met:
        complete = True
        reasons.append(CAUSE_COMPLETION_OBSERVED)
    elif not applicability.applicable:
        escalate = True
        checks["system2_takeover"] = True
        reasons.append(
            CAUSE_VERSION_OUTSIDE_APPLICABILITY
            if applicability.app_version
            else CAUSE_LOCALE_OUTSIDE_APPLICABILITY
        )
        if "app_version_outside_subtool_applicability" not in reasons:
            reasons.append("app_version_outside_subtool_applicability")
    elif guard.missing_start_conditions:
        escalate = True
        checks["system2_takeover"] = True
        reasons.append(CAUSE_START_CONDITION_MISSING)
    elif guard.missing_anchors:
        if "anchor_mismatch" in subtool.reobserve_policy:
            reobserve = True
            reasons.append(CAUSE_ANCHOR_MISSING)
        else:
            # §56: no reobserve allowed and the anchor is gone → escalate,
            # never "click where it used to be".
            escalate = True
            checks["system2_takeover"] = True
            reasons.append(CAUSE_ANCHOR_MISSING)
    elif not got_transition:
        if "transition_mismatch" in subtool.reobserve_policy or (
            "action_failed" in subtool.reobserve_policy
        ):
            reobserve = True
            reasons.append(CAUSE_EXPECTED_TRANSITION_MISSING)
        else:
            escalate = True
            checks["system2_takeover"] = True
            reasons.append(CAUSE_EXPECTED_TRANSITION_MISSING)
    else:
        remaining = [item for item in subtool.allowed_actions if item not in performed]
        if remaining:
            action = remaining[0]
            proceed = True
            reasons.append(f"next_allowed_action:{action}")
        else:
            escalate = True
            checks["system2_takeover"] = True
            reasons.append(CAUSE_NO_ALLOWED_ACTION_LEFT)

    if system2_requested:
        proceed = reobserve = complete = False
        escalate = True
        checks["system2_takeover"] = True
        action = ""
        reasons.append(CAUSE_SYSTEM2_REQUESTED)

    if escalate:
        # the subtool's own declared escalation policy is surfaced, not invented
        reasons.extend(subtool.system2_escalation_policy)

    return SubtoolStepDecision(
        action=action,
        proceed=proceed,
        reobserve=reobserve,
        escalate=escalate,
        complete=complete,
        checks=checks,
        reasons=tuple(reasons),
        applicability=applicability,
    )


__all__ = [
    "CANDIDATE_CHOICE_OPTIONS",
    "CANDIDATE_CHOICE_QUESTION_ID",
    "COMPUTER_USE_PACK_ID",
    "COMPUTER_USE_PACK_VERSION",
    "LEGACY_COMPUTER_USE_QUESTION_IDS",
    "POST_ACTION_QUESTIONS",
    "PRE_ACTION_QUESTIONS",
    "RECOVERY_QUESTIONS",
    "REOBSERVE_TRIGGERS",
    "VISUAL_SUBTOOL_FIELDS",
    "BlindReplayRefused",
    "CandidateChoiceViolation",
    "ReplayGuardDecision",
    "SubtoolApplicability",
    "SubtoolContractError",
    "SubtoolNotRegistered",
    "SubtoolRegistry",
    "SubtoolRevoked",
    "SubtoolStepDecision",
    "VisualSubtool",
    "allowed_candidate_options",
    "assert_no_blind_replay",
    "check_applicability",
    "check_blind_replay",
    "compose_computer_use_questions",
    "computer_use_pack",
    "evaluate_subtool_step",
    "register_computer_use_pack",
    "validate_candidate_choice",
]
