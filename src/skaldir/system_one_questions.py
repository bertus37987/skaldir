"""Versioned System-One question registry + atomic composition (ABNAHME §39/§47).

Two contract rules live here:

* every productive question has a stable id, a version and an owner, and the
  registry is the only place they come from — a prompt string scattered through
  the code must never act as hidden policy (§39/JEV-03);
* questions are **atomic**: one dimension per question, and the combination is
  ordinary, testable Python (:func:`compose_questions` /
  :func:`compose_voice_triage`) — not one omnibus "analyse everything" prompt
  (§39/§82). The omnibus form is rejected at construction time.

The named packs mirror §39/§47: ``voice_triage_v1``, ``completion_v1``,
``recovery_v1``, ``computer_use_v1``, ``capability_v1``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .system_one import ANSWER_KINDS, QuestionNotRegistered, SystemOneContractError

#: §39's bad form, reproduced verbatim — the guard must reject it.
OMNIBUS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(analysiere alles|entscheide, was|analyse everything|decide what)\b"),
)
MAX_QUESTION_CHARS = 160


def _validate_question(
    *, kind: str, text: str, options: tuple[str, ...], legend: tuple[float, ...]
) -> None:
    if kind not in ANSWER_KINDS:
        raise SystemOneContractError(f"unknown question kind: {kind!r} (known: {ANSWER_KINDS})")
    if not text or not text.strip():
        raise SystemOneContractError("a question needs text")
    if len(text) > MAX_QUESTION_CHARS:
        raise SystemOneContractError(
            f"question text too long ({len(text)} > {MAX_QUESTION_CHARS}) — split it (§39)"
        )
    if not text.rstrip().endswith("?"):
        raise SystemOneContractError(
            f"an atomic question must ask one thing and end in '?': {text!r}"
        )
    if text.count("?") > 1:
        raise SystemOneContractError(
            f"a question asks exactly one dimension, not {text.count('?')}: {text!r}"
        )
    for pattern in OMNIBUS_PATTERNS:
        if pattern.search(text):
            raise SystemOneContractError(f"omnibus question form is forbidden (§39): {text!r}")
    if kind == "choice" and len(options) < 2:
        raise SystemOneContractError("a choice question needs at least two options")
    if kind == "score" and not legend:
        raise SystemOneContractError("a score question needs a legend")


@dataclass(frozen=True)
class Question:
    """One atomic question: stable id + version + owner (§39/JEV-03).

    ``canonical_id`` is the §6 metadata: the atomic concept this question
    answers (``same_root_cause``…). It is *additional* metadata — the existing
    ids are never renamed, and the registry resolves either form.
    """

    id: str
    version: str
    owner: str
    kind: str
    text: str
    group: str = ""
    options: tuple[str, ...] = ()
    legend: tuple[float, ...] = ()
    canonical_id: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.version or not self.owner:
            raise SystemOneContractError(
                "every question needs a stable id, a version and an owner (§39)"
            )
        object.__setattr__(self, "options", tuple(str(option) for option in self.options))
        object.__setattr__(self, "legend", tuple(float(value) for value in self.legend))
        _validate_question(
            kind=self.kind, text=self.text, options=self.options, legend=self.legend
        )

    def as_dict(self) -> dict:
        payload: dict[str, object] = {
            "id": self.id,
            "version": self.version,
            "owner": self.owner,
            "kind": self.kind,
            "group": self.group,
            "text": self.text,
        }
        if self.canonical_id:
            payload["canonical_id"] = self.canonical_id
        if self.options:
            payload["options"] = list(self.options)
        if self.legend:
            payload["legend"] = list(self.legend)
        return payload


@dataclass(frozen=True)
class QuestionPack:
    """A versioned pack of atomic questions owned by one subsystem (§39).

    §5 mandatory metadata: ``purpose``, ``applicable_states``, ``risk_class``,
    ``fallback_policy`` and ``calibration_dataset_version``. They are defaulted
    so that ad-hoc packs (unit fixtures, tool-local packs) stay constructible,
    and :func:`assert_pack_metadata` — enforced by the *productive* registry —
    refuses an incomplete pack. ``ref`` stays ``<id>_v<version>`` so the
    registry manifests do not move (§5).
    """

    id: str
    version: str
    owner: str
    questions: tuple[Question, ...]
    purpose: str = ""
    applicable_states: tuple[str, ...] = ()
    risk_class: str = "R0"
    fallback_policy: str = ""
    calibration_dataset_version: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.version or not self.owner:
            raise SystemOneContractError("every question pack needs id, version and owner (§39)")
        if not self.questions:
            raise SystemOneContractError(f"pack {self.id} has no questions")
        object.__setattr__(
            self,
            "applicable_states",
            tuple(str(state) for state in (self.applicable_states or ())),
        )
        seen: set[str] = set()
        for question in self.questions:
            if question.id in seen:
                raise SystemOneContractError(f"duplicate question id in {self.ref}: {question.id}")
            seen.add(question.id)

    @property
    def ref(self) -> str:
        """``voice_triage_v1`` — the registry key of §39."""
        return f"{self.id}_{self.version}"

    def ids(self) -> tuple[str, ...]:
        return tuple(question.id for question in self.questions)

    def in_groups(self, groups) -> tuple[Question, ...]:
        wanted = set(groups)
        return tuple(question for question in self.questions if question.group in wanted)

    def canonical_id(self, question_id: str) -> str:
        for question in self.questions:
            if question.id == question_id:
                return question.canonical_id
        return ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "owner": self.owner,
            "purpose": self.purpose,
            "applicable_states": list(self.applicable_states),
            "risk_class": self.risk_class,
            "fallback_policy": self.fallback_policy,
            "calibration_dataset_version": self.calibration_dataset_version,
            "questions": [question.as_dict() for question in self.questions],
        }


def missing_pack_metadata(pack: QuestionPack) -> tuple[str, ...]:
    """The §5 metadata a pack still lacks — an empty tuple means "complete"."""
    missing: list[str] = []
    if not str(pack.purpose or "").strip():
        missing.append("purpose")
    if not tuple(pack.applicable_states or ()):
        missing.append("applicable_states")
    if str(pack.risk_class or "") not in {"R0", "R1", "R2", "R3", "R4"}:
        missing.append("risk_class")
    if not str(pack.fallback_policy or "").strip():
        missing.append("fallback_policy")
    if not str(pack.calibration_dataset_version or "").strip():
        missing.append("calibration_dataset_version")
    return tuple(missing)


def assert_pack_metadata(pack: QuestionPack) -> None:
    """Refuse a pack without the §5 metadata — loudly, with the missing names."""
    missing = missing_pack_metadata(pack)
    if missing:
        raise SystemOneContractError(
            f"pack {pack.ref} is missing mandatory §5 metadata: {list(missing)}"
        )



@dataclass(frozen=True)
class QuestionSet:
    """The questions of one call: a pack narrowed to the composed groups."""

    id: str
    version: str
    owner: str
    questions: tuple[Question, ...]
    groups: tuple[str, ...] = ()

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(question.id for question in self.questions)


class QuestionRegistry:
    """Central, versioned question registry (§39/JEV-03).

    Registering the same pack ref twice is refused, and an unregistered id can
    never be resolved — that is the "no scattered prompt strings as hidden
    policy" guard.
    """

    def __init__(self, packs: tuple[QuestionPack, ...] = ()) -> None:
        self._packs: dict[str, QuestionPack] = {}
        self._by_id: dict[str, QuestionPack] = {}
        for pack in packs:
            self.register(pack)

    def register(self, pack: QuestionPack) -> QuestionPack:
        if pack.ref in self._packs:
            raise SystemOneContractError(f"question pack already registered: {pack.ref}")
        if pack.id in self._by_id:
            raise SystemOneContractError(
                f"question pack id already registered: {pack.id} "
                f"(versioned packs must be distinct; bump the version instead)"
            )
        self._packs[pack.ref] = pack
        self._by_id[pack.id] = pack
        return pack

    def pack(self, pack_id: str) -> QuestionPack:
        """Resolve by id (``voice_triage``) or by ref (``voice_triage_v1``)."""
        found = self._by_id.get(pack_id) or self._packs.get(pack_id)
        if found is None:
            raise QuestionNotRegistered(f"unknown question pack: {pack_id!r}")
        return found

    def packs(self) -> tuple[QuestionPack, ...]:
        return tuple(self._packs.values())

    def refs(self) -> tuple[str, ...]:
        return tuple(self._packs)

    def question_ids(self) -> tuple[str, ...]:
        return tuple(
            question.id for pack in self._packs.values() for question in pack.questions
        )

    def resolve(self, question_id: str) -> Question:
        for pack in self._packs.values():
            for question in pack.questions:
                if question.id == question_id:
                    return question
        raise QuestionNotRegistered(f"question not registered: {question_id!r}")

    # -- §6 canonical atomic ids -------------------------------------------- #

    def resolve_canonical(self, canonical_id: str) -> Question:
        """Resolve a §6 canonical id (or one of its aliases) to its question.

        The canonical names are an *alias layer over* the existing ids — no
        question was renamed. A canonical id that no registered question serves
        is a contract error, never a silent ``None``.
        """
        feature = canonical_feature(canonical_id)
        if feature is not None:
            try:
                return self.resolve(feature.question_id)
            except QuestionNotRegistered as exc:  # pragma: no cover — registry drift
                raise QuestionNotRegistered(
                    f"canonical id {canonical_id!r} points at an unregistered question "
                    f"{feature.question_id!r}"
                ) from exc
        for pack in self._packs.values():
            for question in pack.questions:
                if question.canonical_id == canonical_id:
                    return question
        raise QuestionNotRegistered(f"canonical id not registered: {canonical_id!r}")

    def canonical_id_for(self, question_id: str) -> str:
        """The canonical id a question carries — ``""`` when it has none."""
        return self.resolve(question_id).canonical_id

    def manifest(self) -> dict:
        """The versioned manifest artifact of §114 (SYSTEM_ONE_QUESTIONS.json)."""
        return {
            pack.ref: pack.as_dict()
            for pack in self._packs.values()
        }


# --------------------------------------------------------------------------- #
# §6 — the canonical atomic ids, as an explicit alias layer                     #
# --------------------------------------------------------------------------- #

#: §6 names the atomic decisions TypeSafe recommends. They are answered by
#: questions that already exist under their own ids; this table maps the
#: canonical name (and the alternates §6 lists with a slash) onto the *existing*
#: question, stating the polarity so an "is it stale?" reading is never confused
#: with the "is it current?" answer it inverts.
#:
#: ``polarity``: ``direct`` — the answer reads as the canonical statement;
#: ``inverted`` — the canonical statement is the complement of the answer
#: (``evidence_current`` low ⇒ ``evidence_stale``).
@dataclass(frozen=True)
class CanonicalFeature:
    canonical_id: str
    question_id: str
    answer_kind: str
    polarity: str = "direct"
    aliases: tuple[str, ...] = ()
    note: str = ""


CANONICAL_FEATURES: tuple[CanonicalFeature, ...] = (
    CanonicalFeature(
        "same_root_cause", "voice.loop.same_root_cause", "choice", "direct"
    ),
    CanonicalFeature(
        "same_strategy",
        "voice.loop.same_strategy",
        "noul",
        "direct",
        aliases=("same_failed_strategy",),
        note="§6 spells this 'same_strategy/same_failed_strategy' — one concept",
    ),
    CanonicalFeature(
        "new_information_obtained",
        "voice.loop.new_information",
        "noul",
        "direct",
        aliases=("new_evidence_obtained",),
    ),
    CanonicalFeature("meaningful_progress", "voice.loop.measurable_progress", "score", "direct"),
    CanonicalFeature(
        "current_evidence_supports_claim",
        "voice.completion.evidence_current",
        "noul",
        "direct",
    ),
    CanonicalFeature(
        "evidence_stale",
        "voice.completion.evidence_current",
        "noul",
        "inverted",
        note="the complement of evidence_current — same question, inverse reading",
    ),
    CanonicalFeature(
        "goal_drift_present",
        "voice.drift.supports_goal",
        "noul",
        "inverted",
        note="the drift pack has three atomic dimensions; this is the goal-support one",
    ),
    CanonicalFeature(
        "completion_claim_present", "voice.completion.claims_done", "choice", "direct"
    ),
    CanonicalFeature("system2_needed", "voice.recovery.system_two_needed", "noul", "direct"),
)


def canonical_feature(canonical_id: str) -> CanonicalFeature | None:
    """The §6 feature for a canonical id or one of its aliases."""
    wanted = str(canonical_id or "")
    for feature in CANONICAL_FEATURES:
        if feature.canonical_id == wanted or wanted in feature.aliases:
            return feature
    return None


def canonical_alias_names() -> dict[str, str]:
    """Every accepted spelling → its canonical id (explicit, reviewable)."""
    aliases: dict[str, str] = {}
    for feature in CANONICAL_FEATURES:
        aliases[feature.canonical_id] = feature.canonical_id
        for alias in feature.aliases:
            aliases[alias] = feature.canonical_id
    return aliases


#: §8.2 sections → the internal group names they are composed of. An alias
#: layer, not a rename: the questions keep their established group names.
SECTION_GROUPS: dict[str, tuple[str, ...]] = {
    "progress": ("loop",),
    "goal_scope": ("drift",),
    "completion": ("completion",),
    "investigation": ("research",),
    "untrusted_content": ("untrusted",),
}



# --------------------------------------------------------------------------- #
# the productive packs (§39/§47/§54)                                           #
# --------------------------------------------------------------------------- #

_OWNER_VOICE = "voice"
_OWNER_COMPLETION = "completion"
_OWNER_RECOVERY = "recovery"
_OWNER_COMPUTER_USE = "computer_use"
_OWNER_CAPABILITY = "capability"


def _q(
    question_id: str,
    group: str,
    kind: str,
    text: str,
    owner: str,
    *,
    canonical_id: str = "",
    **extra,
) -> Question:
    return Question(
        id=question_id,
        version="v1",
        owner=owner,
        kind=kind,
        text=text,
        group=group,
        canonical_id=canonical_id,
        **extra,
    )


VOICE_TRIAGE_V1 = QuestionPack(
    id="voice_triage",
    version="v1",
    owner=_OWNER_VOICE,
    purpose=(
        "Fast semantic triage of a Voice trigger before a System-2 model is spent: "
        "loop health, goal drift, completion claims, investigation need, untrusted content (§8)."
    ),
    applicable_states=("run", "voice_trigger", "trajectory_window"),
    risk_class="R1",  # reversible internal routing: it may ask for a redirect
    fallback_policy=(
        "No advisory: the deterministic Voice hint stands alone (legacy path, §98/JEV-20)."
    ),
    calibration_dataset_version="voice-dev-2026-09-16",
    questions=(
        # Loop (§47) — the same failure repeated, not the same code path reused.
        _q(
            "voice.loop.same_root_cause",
            "loop",
            "choice",
            "Same root cause as the last failure?",
            _OWNER_VOICE,
            canonical_id="same_root_cause",
            options=("same", "different", "unknown"),
        ),
        _q(
            "voice.loop.same_strategy",
            "loop",
            "noul",
            "Same strategy as the last attempt?",
            _OWNER_VOICE,
            canonical_id="same_strategy",
        ),
        _q(
            "voice.loop.new_information",
            "loop",
            "noul",
            "New relevant information since the last attempt?",
            _OWNER_VOICE,
            canonical_id="new_information_obtained",
        ),
        _q(
            "voice.loop.measurable_progress",
            "loop",
            "score",
            "How much measurable progress happened since the last attempt?",
            _OWNER_VOICE,
            canonical_id="meaningful_progress",
            legend=(0.0, 1.0, 2.0, 3.0),
        ),
        _q(
            "voice.loop.repeated_ineffective_redirect",
            "loop",
            "noul",
            "Was a previous redirect ineffective without a strategy change?",
            _OWNER_VOICE,
        ),
        # Completion (§47) — claim versus evidence.
        _q(
            "voice.completion.claims_done",
            "completion",
            "choice",
            "Does the agent claim completion?",
            _OWNER_VOICE,
            canonical_id="completion_claim_present",
            options=("yes", "no"),
        ),
        _q(
            "voice.completion.evidence_current",
            "completion",
            "noul",
            "Is the current evidence complete?",
            _OWNER_VOICE,
            canonical_id="current_evidence_supports_claim",
        ),
        _q(
            "voice.completion.evidence_matches_revision",
            "completion",
            "noul",
            "Does that evidence belong to the current revision?",
            _OWNER_VOICE,
        ),
        _q(
            "voice.completion.independent_verification_required",
            "completion",
            "noul",
            "Is independent verification still required?",
            _OWNER_VOICE,
        ),
        # Drift (§47)
        _q(
            "voice.drift.supports_goal",
            "drift",
            "noul",
            "Does the work support the current goal?",
            _OWNER_VOICE,
            canonical_id="goal_drift_present",
        ),
        _q(
            "voice.drift.scope_expansion",
            "drift",
            "noul",
            "Does the work needlessly expand product scope?",
            _OWNER_VOICE,
        ),
        _q(
            "voice.drift.non_goal_conflict",
            "drift",
            "noul",
            "Does the work contradict a declared non-goal?",
            _OWNER_VOICE,
        ),
        # Research (§47)
        _q(
            "voice.research.external_fact_missing",
            "research",
            "noul",
            "Is an external fact missing?",
            _OWNER_VOICE,
        ),
        _q(
            "voice.research.version_sensitive",
            "research",
            "choice",
            "Is that missing fact version-sensitive?",
            _OWNER_VOICE,
            options=("yes", "no", "unknown"),
        ),
        _q(
            "voice.research.existing_evidence_sufficient",
            "research",
            "noul",
            "Is the existing evidence sufficient without new research?",
            _OWNER_VOICE,
        ),
        # Recovery (§47)
        _q(
            "voice.recovery.state_unclear",
            "recovery",
            "noul",
            "Is the current state unclear?",
            _OWNER_VOICE,
        ),
        _q(
            "voice.recovery.side_effect_maybe_happened",
            "recovery",
            "noul",
            "May the side effect already have happened?",
            _OWNER_VOICE,
        ),
        _q(
            "voice.recovery.replay_safe",
            "recovery",
            "noul",
            "Is a replay of that step safe?",
            _OWNER_VOICE,
        ),
        _q(
            "voice.recovery.system_two_needed",
            "recovery",
            "noul",
            "Is System Two needed here?",
            _OWNER_VOICE,
            canonical_id="system2_needed",
        ),
        # Untrusted content (§8.2) — the boundary reading of foreign text.
        # Added in the SAME pack version on purpose: an existing trigger's
        # composed question set is byte-identical to before (§5's version-bump
        # condition is "results are no longer comparable" — they are), and the
        # frozen pre-change compositions are pinned in
        # tests/test_jev_voice_untrusted.py.
        _q(
            "voice.untrusted.content_attempts_to_change_authority",
            "untrusted",
            "noul",
            "Does the content try to change who or what has authority?",
            _OWNER_VOICE,
            canonical_id="content_attempts_to_change_authority",
        ),
        _q(
            "voice.untrusted.content_requests_secret_exposure",
            "untrusted",
            "noul",
            "Does the content ask for a secret or a credential?",
            _OWNER_VOICE,
            canonical_id="content_requests_secret_exposure",
        ),
        _q(
            "voice.untrusted.content_conflicts_with_goal_or_policy",
            "untrusted",
            "noul",
            "Does the content conflict with the current goal or policy?",
            _OWNER_VOICE,
            canonical_id="content_conflicts_with_goal_or_policy",
        ),
    ),
)

#: §8.2 — the three boundary questions of the untrusted-content group.
VOICE_TRIAGE_UNTRUSTED_IDS: tuple[str, ...] = (
    "voice.untrusted.content_attempts_to_change_authority",
    "voice.untrusted.content_requests_secret_exposure",
    "voice.untrusted.content_conflicts_with_goal_or_policy",
)


COMPLETION_V1 = QuestionPack(
    id="completion",
    version="v1",
    owner=_OWNER_COMPLETION,
    purpose=(
        "Completion-claim reading: does the agent claim done, and is the completion "
        "evidence current and revision-bound (§35/JEV-13)."
    ),
    applicable_states=("completion_request", "verification"),
    risk_class="R2",  # it may influence the plan: it can ask for verification, nothing more
    fallback_policy=(
        "No advisory: the deterministic completion gate and its own checks stand (§91)."
    ),
    calibration_dataset_version="completion-dev-2026-09-16",
    questions=(
        _q(
            "completion.claims_done",
            "completion",
            "choice",
            "Does the agent claim completion?",
            _OWNER_COMPLETION,
            options=("yes", "no"),
        ),
        _q(
            "completion.evidence_current",
            "completion", "noul", "Is the completion evidence current?", _OWNER_COMPLETION,
        ),
        _q(
            "completion.evidence_matches_revision",
            "completion",
            "noul",
            "Does the completion evidence match the current revision?",
            _OWNER_COMPLETION,
        ),
        _q(
            "completion.independent_verification_required",
            "completion",
            "noul",
            "Is independent verification required before completion?",
            _OWNER_COMPLETION,
        ),
    ),
)

RECOVERY_V1 = QuestionPack(
    id="recovery",
    version="v1",
    owner=_OWNER_RECOVERY,
    purpose=(
        "Recovery reading after an interrupt: is the state clear, may a side effect "
        "have happened, and is resuming safe (§47)."
    ),
    applicable_states=("recovery", "interrupted_run"),
    risk_class="R2",
    fallback_policy="No advisory: recovery keeps its deterministic crash-window policy.",
    calibration_dataset_version="recovery-dev-2026-09-16",
    questions=(
        _q(
            "recovery.state_unclear",
            "recovery", "noul", "Is the recovered state unclear?", _OWNER_RECOVERY,
        ),
        _q(
            "recovery.side_effect_maybe_happened",
            "recovery",
            "noul",
            "May the interrupted side effect already have happened?",
            _OWNER_RECOVERY,
        ),
        _q(
            "recovery.replay_safe",
            "recovery", "noul", "Is resuming or replaying safe?", _OWNER_RECOVERY,
        ),
        _q(
            "recovery.system_two_needed",
            "recovery", "noul", "Does recovery need System Two?", _OWNER_RECOVERY,
        ),
    ),
)

COMPUTER_USE_V1 = QuestionPack(
    id="computer_use",
    version="v1",
    owner=_OWNER_COMPUTER_USE,
    purpose=(
        "Structured GUI pre/postcondition reading: right window, expected transition, "
        "reobserve or recover (§10). Never a screenshot or vision replacement."
    ),
    applicable_states=("gui_state", "post_action"),
    risk_class="R3",  # it reads a real-effect step; the action set stays pre-approved
    fallback_policy="No advisory: the runtime action policy decides, and it never widens the set.",
    calibration_dataset_version="computer_use-dev-2026-09-16",
    questions=(
        _q(
            "computer_use.precondition_met",
            "precondition",
            "noul",
            "Are the preconditions for the planned action met?",
            _OWNER_COMPUTER_USE,
        ),
        _q(
            "computer_use.same_window",
            "precondition",
            "choice",
            "Is the target the same window as observed?",
            _OWNER_COMPUTER_USE,
            options=("same", "other", "unknown"),
        ),
        _q(
            "computer_use.action_had_effect",
            "postcondition",
            "noul",
            "Did the action produce the intended effect?",
            _OWNER_COMPUTER_USE,
        ),
        _q(
            "computer_use.reobserve_needed",
            "postcondition",
            "noul",
            "Is a fresh observation needed before the next step?",
            _OWNER_COMPUTER_USE,
        ),
        _q(
            "computer_use.recovery_needed",
            "recovery",
            "noul",
            "Is a recovery action needed?",
            _OWNER_COMPUTER_USE,
        ),
    ),
)

CAPABILITY_V1 = QuestionPack(
    id="capability",
    version="v1",
    owner=_OWNER_CAPABILITY,
    purpose=(
        "Capability-gap reading: is a capability missing, does a tool cover it, and is "
        "building one warranted."
    ),
    applicable_states=("capability_gap",),
    risk_class="R1",
    fallback_policy="No advisory: the capability pipeline keeps its own deterministic gates.",
    calibration_dataset_version="capability-dev-2026-09-16",
    questions=(
        _q(
            "capability.missing_capability",
            "capability",
            "noul",
            "Is a required capability missing?",
            _OWNER_CAPABILITY,
        ),
        _q(
            "capability.tool_exists",
            "capability",
            "noul",
            "Does an existing tool already cover it?",
            _OWNER_CAPABILITY,
        ),
        _q(
            "capability.build_needed",
            "capability",
            "noul",
            "Is building a new tool warranted?",
            _OWNER_CAPABILITY,
        ),
        _q(
            "capability.risk_of_build",
            "capability",
            "score",
            "How risky is building that tool?",
            _OWNER_CAPABILITY,
            legend=(0.0, 1.0, 2.0),
        ),
    ),
)

DEFAULT_QUESTION_PACKS: tuple[QuestionPack, ...] = (
    VOICE_TRIAGE_V1,
    COMPLETION_V1,
    RECOVERY_V1,
    COMPUTER_USE_V1,
    CAPABILITY_V1,
)


# --------------------------------------------------------------------------- #
# §9 — the goal-completion preflight and sanity packs                          #
# --------------------------------------------------------------------------- #

#: §9.4 — the semantic criterion preflight. One atomic question per dimension,
#: never "is the criterion done?".
COMPLETION_PREFLIGHT_V1 = QuestionPack(
    id="completion_preflight",
    version="v1",
    owner=_OWNER_COMPLETION,
    purpose=(
        "Semantic criterion preflight: does the evidence really carry the requirement "
        "(§9.4). Advisory only — the deterministic gate certifies."
    ),
    applicable_states=("completion_request", "ledger_criterion"),
    risk_class="R2",
    fallback_policy=(
        "No advisory: the hard structural checks already ran, and the criterion stays "
        "open/escalated without a model reading."
    ),
    calibration_dataset_version="completion-dev-2026-09-16",
    questions=(
        _q(
            "completion_preflight.does_evidence_directly_support_requirement",
            "preflight",
            "noul",
            "Does the evidence directly support the requirement?",
            _OWNER_COMPLETION,
        ),
        _q(
            "completion_preflight.is_evidence_only_indirect",
            "preflight",
            "noul",
            "Is the evidence only indirect?",
            _OWNER_COMPLETION,
        ),
        _q(
            "completion_preflight.is_live_requirement_supported_only_by_mock",
            "preflight",
            "noul",
            "Is a live requirement supported only by a mock?",
            _OWNER_COMPLETION,
        ),
        _q(
            "completion_preflight.does_evidence_cover_current_behavior",
            "preflight",
            "noul",
            "Does the evidence cover the current behaviour?",
            _OWNER_COMPLETION,
        ),
        _q(
            "completion_preflight.is_scope_of_proof_too_narrow",
            "preflight",
            "noul",
            "Is the scope of the proof too narrow?",
            _OWNER_COMPLETION,
        ),
        _q(
            "completion_preflight.does_contradicting_evidence_materially_challenge_pass",
            "preflight",
            "noul",
            "Does contradicting evidence materially challenge a pass?",
            _OWNER_COMPLETION,
        ),
        _q(
            "completion_preflight.is_independent_verification_still_needed",
            "preflight",
            "noul",
            "Is independent verification still needed?",
            _OWNER_COMPLETION,
        ),
        _q(
            "completion_preflight.is_criterion_semantically_unresolved",
            "preflight",
            "noul",
            "Is the criterion still semantically unresolved?",
            _OWNER_COMPLETION,
        ),
        _q(
            "completion_preflight.evidence_fit",
            "preflight",
            "choice",
            "How well does the evidence fit the requirement?",
            _OWNER_COMPLETION,
            options=("direct", "partial", "irrelevant", "contradictory", "uncertain"),
        ),
    ),
)

COMPLETION_PREFLIGHT_SEMANTIC_IDS: tuple[str, ...] = (
    "completion_preflight.does_evidence_directly_support_requirement",
    "completion_preflight.is_evidence_only_indirect",
    "completion_preflight.is_live_requirement_supported_only_by_mock",
    "completion_preflight.does_evidence_cover_current_behavior",
    "completion_preflight.is_scope_of_proof_too_narrow",
    "completion_preflight.does_contradicting_evidence_materially_challenge_pass",
    "completion_preflight.is_independent_verification_still_needed",
    "completion_preflight.is_criterion_semantically_unresolved",
)

COMPLETION_PREFLIGHT_FIT_ID = "completion_preflight.evidence_fit"

#: §9.7 — the second sanity pass over the 9-state summary.
COMPLETION_SANITY_V1 = QuestionPack(
    id="completion_sanity",
    version="v1",
    owner=_OWNER_COMPLETION,
    purpose=(
        "Second completion sanity pass over the aggregated 9-state summary (§9.7): "
        "gaps, stale verifications, mock-only live requirements, contradictions, "
        "changed artefacts, unknown side effects, circular self-certification."
    ),
    applicable_states=("completion_summary",),
    risk_class="R2",
    fallback_policy=(
        "No advisory: any unread summary stays open — an unread sanity pass never "
        "releases a completion."
    ),
    calibration_dataset_version="completion-dev-2026-09-16",
    questions=(
        _q(
            "completion_sanity.any_mandatory_gap",
            "sanity",
            "noul",
            "Does any mandatory criterion remain unmet?",
            _OWNER_COMPLETION,
        ),
        _q(
            "completion_sanity.any_stale_verification",
            "sanity",
            "noul",
            "Is any verification stale?",
            _OWNER_COMPLETION,
        ),
        _q(
            "completion_sanity.any_mock_only_live_requirement",
            "sanity",
            "noul",
            "Is any live requirement still mock-only?",
            _OWNER_COMPLETION,
        ),
        _q(
            "completion_sanity.any_unresolved_contradiction",
            "sanity",
            "noul",
            "Is any contradiction unresolved?",
            _OWNER_COMPLETION,
        ),
        _q(
            "completion_sanity.any_changed_artifact_after_verification",
            "sanity",
            "noul",
            "Did any artifact change after its verification?",
            _OWNER_COMPLETION,
        ),
        _q(
            "completion_sanity.any_unknown_external_side_effect",
            "sanity",
            "noul",
            "Is any external side effect unknown?",
            _OWNER_COMPLETION,
        ),
        _q(
            "completion_sanity.any_circular_self_certification",
            "sanity",
            "noul",
            "Is any proof circular or self-certifying?",
            _OWNER_COMPLETION,
        ),
    ),
)

COMPLETION_SANITY_IDS: tuple[str, ...] = (
    "completion_sanity.any_mandatory_gap",
    "completion_sanity.any_stale_verification",
    "completion_sanity.any_mock_only_live_requirement",
    "completion_sanity.any_unresolved_contradiction",
    "completion_sanity.any_changed_artifact_after_verification",
    "completion_sanity.any_unknown_external_side_effect",
    "completion_sanity.any_circular_self_certification",
)

#: §9.2/§9.7 — the nine aggregated states the sanity summary reports.
LEDGER_SUMMARY_STATES: tuple[str, ...] = (
    "verified",
    "failed",
    "unknown",
    "stale",
    "mock_only",
    "blocked",
    "unknown_side_effects",
    "unresolved_p0_p1",
    "changed_after_verification",
)

#: Every pack the productive registry serves: the five §39 packs of the
#: original contract plus the two §9 preflight packs. Kept as one tuple so a
#: caller cannot accidentally register only half of the registry.
DELTA_QUESTION_PACKS: tuple[QuestionPack, ...] = (
    COMPLETION_PREFLIGHT_V1,
    COMPLETION_SANITY_V1,
)

ALL_QUESTION_PACKS: tuple[QuestionPack, ...] = DEFAULT_QUESTION_PACKS + DELTA_QUESTION_PACKS


def default_question_registry() -> QuestionRegistry:
    """The productive registry — a fresh instance so callers cannot mutate it.

    Every pack here is required to carry the §5 metadata: this is the one place
    a productive question pack enters the system, so it is the one place the
    completeness rule is enforced (§5).
    """
    registry = QuestionRegistry(_productive_packs())
    for pack in registry.packs():
        assert_pack_metadata(pack)
    return registry


def _productive_packs() -> tuple:
    """``ALL_QUESTION_PACKS`` with the §10.3 computer-use pack in its complete v2 form.

    The registry refuses two packs with the same id, so the productive registry serves
    exactly one ``computer_use`` pack: v2. The legacy 5-question v1 pack stays in
    ``ALL_QUESTION_PACKS`` for the readers that pin it, and the legacy question ids the
    runtime reads (``computer_use.recovery_needed``) are contained in v2.
    """
    from skaldir.system_one_subtools import computer_use_pack  # local: avoids an import cycle

    v2 = computer_use_pack()
    return tuple(v2 if pack.id == v2.id else pack for pack in ALL_QUESTION_PACKS)



# --------------------------------------------------------------------------- #
# composition in code (§39/§47/§82)                                            #
# --------------------------------------------------------------------------- #

#: Which §47 groups a trigger needs. A trigger names its dimensions; the
#: combination lives here, in testable code, instead of inside a prompt.
TRIGGER_GROUPS: dict[str, tuple[str, ...]] = {
    "loop": ("loop", "completion"),
    "error_burst": ("loop", "research", "recovery"),
    "strategy_repeat": ("loop", "recovery"),
    "known_incident_repeat": ("research", "recovery"),
    "scope_drift": ("drift", "completion"),
    "contradiction": ("drift", "completion"),
    "capability_gap": ("research", "recovery"),
    "wrong_tool_call": ("loop", "drift"),
    # §8.1: a possible injection/untrusted-content case gets its own boundary
    # reading. Added in the same pack version: no pre-existing trigger's
    # question set changes (pinned in tests/test_jev_voice_untrusted.py).
    "untrusted_content": ("untrusted", "drift"),
    # Unknown triggers stay conservative: the loop/completion/recovery reading
    # is the one that can never silently bless a stuck run.
    "*": ("loop", "completion", "recovery"),
}


def compose_voice_sections(
    sections: tuple[str, ...],
    *,
    registry: QuestionRegistry | None = None,
) -> QuestionSet:
    """Compose the voice pack by the §8.2 **section** names (§8.2).

    ``progress``/``goal_scope``/``completion``/``investigation``/
    ``untrusted_content`` are an alias layer over the internal group names
    (:data:`SECTION_GROUPS`) — the questions keep their established group
    names, and an unknown section is refused like an unknown group.
    """
    registry = registry or default_question_registry()
    flat: list[str] = []
    for section in sections:
        groups = SECTION_GROUPS.get(str(section))
        if groups is None:
            raise QuestionNotRegistered(
                f"unknown §8.2 section: {section!r} (known: {sorted(SECTION_GROUPS)})"
            )
        for group in groups:
            if group not in flat:
                flat.append(group)
    return compose_questions("voice_triage", groups=tuple(flat), registry=registry)



def compose_questions(
    pack_id: str,
    *,
    groups: tuple[str, ...] | None = None,
    registry: QuestionRegistry | None = None,
) -> QuestionSet:
    """Narrow a registered pack to ``groups`` (all groups when omitted)."""
    registry = registry or default_question_registry()
    pack = registry.pack(pack_id)
    if groups is None:
        selected = pack.questions
        chosen = tuple(sorted({question.group for question in pack.questions}))
    else:
        known = {question.group for question in pack.questions}
        unknown = [group for group in groups if group not in known]
        if unknown:
            raise QuestionNotRegistered(
                f"unknown question group(s) for {pack.ref}: {unknown} (known: {sorted(known)})"
            )
        selected = pack.in_groups(groups)
        chosen = tuple(groups)
    return QuestionSet(
        id=pack.id,
        version=pack.version,
        owner=pack.owner,
        questions=tuple(selected),
        groups=chosen,
    )


def compose_voice_triage(
    trigger_kind: str,
    *,
    registry: QuestionRegistry | None = None,
) -> QuestionSet:
    """The voice triage question set for one trigger (§47/§82).

    Atomic questions only: the trigger selects groups, each group contributes
    its own single-dimension questions. No caller composes prompt text itself.
    """
    groups = TRIGGER_GROUPS.get(trigger_kind, TRIGGER_GROUPS["*"])
    return compose_questions("voice_triage", groups=groups, registry=registry)


def write_questions_manifest(path: str | Path) -> str:
    """Write the versioned ``SYSTEM_ONE_QUESTIONS.json`` artifact (§114)."""
    registry = default_question_registry()
    payload = {
        "schema_version": 1,
        "source": "skaldir.system_one_questions",
        "packs": registry.manifest(),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(target)


__all__ = [
    "ALL_QUESTION_PACKS",
    "CANONICAL_FEATURES",
    "CAPABILITY_V1",
    "COMPLETION_PREFLIGHT_FIT_ID",
    "COMPLETION_PREFLIGHT_SEMANTIC_IDS",
    "COMPLETION_PREFLIGHT_V1",
    "COMPLETION_SANITY_IDS",
    "COMPLETION_SANITY_V1",
    "COMPLETION_V1",
    "COMPUTER_USE_V1",
    "DEFAULT_QUESTION_PACKS",
    "DELTA_QUESTION_PACKS",
    "LEDGER_SUMMARY_STATES",
    "CanonicalFeature",
    "Question",
    "QuestionPack",
    "QuestionRegistry",
    "QuestionSet",
    "RECOVERY_V1",
    "SECTION_GROUPS",
    "TRIGGER_GROUPS",
    "VOICE_TRIAGE_UNTRUSTED_IDS",
    "VOICE_TRIAGE_V1",
    "assert_pack_metadata",
    "canonical_alias_names",
    "canonical_feature",
    "compose_questions",
    "compose_voice_sections",
    "compose_voice_triage",
    "default_question_registry",
    "missing_pack_metadata",
    "write_questions_manifest",
]
