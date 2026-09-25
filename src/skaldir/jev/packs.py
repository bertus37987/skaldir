"""The question packs the Jev façade needs — registered, never composed ad hoc.

MVP/03 §5.1–§5.6 names four **new** packs (memory_injection, routing,
capability_ranking, compaction, adversarial_test) and one **existing** pack it
reuses verbatim: ``voice_triage_v1`` from :mod:`skaldir.system_one_questions`.
Forking that pack is forbidden by the substrate's own rule §27 ("no second
contract"), so ``voice_triage`` here is literally
:func:`skaldir.system_one_questions.compose_voice_triage`.

Two deliberate implementation choices, both stated because they are load-bearing:

* **Template questions.** A per-candidate question (``mem.812.relevant_now``)
  cannot be a static registered id: the registry's rule is that an unregistered
  id can never be resolved. So the pack registers the *template* question
  (``mem.{id}.relevant_now``) with its text and its kind, and
  :func:`instantiate` substitutes the offered id at composition time. The text
  and the answer kind still come from the registry — never from the caller.
* **A Jev registry view.** :func:`jev_registry` is the substrate registry *plus*
  these packs. The substrate's own default registry is not modified, so no other
  consumer's manifest moves and no second registry is created.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from ..system_one_questions import (
    MAX_QUESTION_CHARS,
    Question,
    QuestionPack,
    QuestionRegistry,
    QuestionSet,
    assert_pack_metadata,
    default_question_registry,
)
from ..system_one_questions import (
    compose_voice_triage as substrate_compose_voice_triage,
)

# --------------------------------------------------------------------------- #
# ownership                                                                     #
# --------------------------------------------------------------------------- #

_OWNER_CONTEXT = "context"
_OWNER_ROUTING = "routing"
_OWNER_CAPABILITY = "capability"
_OWNER_VERIFICATION = "verification"

#: Documented constants the composers read (never invented per call).
CANDIDATES_PER_REQUEST = 8
"""MVP/03 §5.1: batching — at most 8 candidates per provider request."""

CHARS_PER_TOKEN_FLOOR = 4
"""A conservative floor: 4 chars/token means "even a perfect fit cannot work".

Used only by the compaction pre-check, which refuses *before* spending a call.
The real fit decision belongs to the compaction port (``skaldir.jevcomp``).
"""


# --------------------------------------------------------------------------- #
# 1. memory_injection_v1                                                        #
# --------------------------------------------------------------------------- #

MEMORY_INJECTION_V1 = QuestionPack(
    id="memory_injection",
    version="v1",
    owner=_OWNER_CONTEXT,
    purpose=(
        "Decide which retrieved memories enter the next step's context window, and in "
        "which order, without ever dropping an owner-authored memory (MVP/03 §5.1)."
    ),
    applicable_states=("step_start", "context_build"),
    risk_class="R1",
    fallback_policy=(
        "No advisory: injection keeps the deterministic retrieval order and the "
        "existing token budget."
    ),
    calibration_dataset_version="memory-dev-2026-09-18",
    questions=(
        Question(
            id="mem.{id}.relevant_now",
            version="v1",
            owner=_OWNER_CONTEXT,
            kind="noul",
            text="Is memory {id} still relevant for the next step?",
            group="relevance",
        ),
        Question(
            id="mem.{id}.already_in_context",
            version="v1",
            owner=_OWNER_CONTEXT,
            kind="noul",
            text="Is memory {id} already present in the context window?",
            group="presence",
        ),
        Question(
            id="mem.{id}.stance",
            version="v1",
            owner=_OWNER_CONTEXT,
            kind="choice",
            text="Does memory {id} support, contradict or go stale against the goal?",
            group="stance",
            options=("supports", "contradicts", "stale", "neutral"),
        ),
        Question(
            id="mem.{id}.helpfulness",
            version="v1",
            owner=_OWNER_CONTEXT,
            kind="score",
            text="How helpful would memory {id} be for the next step?",
            group="helpfulness",
            legend=(0.0, 1.0, 2.0, 3.0),
        ),
    ),
)

# --------------------------------------------------------------------------- #
# 2. routing_v1                                                                 #
# --------------------------------------------------------------------------- #

ROUTING_V1 = QuestionPack(
    id="routing",
    version="v1",
    owner=_OWNER_ROUTING,
    purpose=(
        "Choose among an already permission-filtered candidate set: native tool, "
        "docker capability, GUI sub-tool, composite, or raw Computer Use "
        "(MVP/03 §5.2). It selects what will be executed, never authorizes it."
    ),
    applicable_states=("step_start", "after_failure", "tool_choice"),
    risk_class="R2",
    fallback_policy="No advisory: the caller keeps capability-mining order + Computer Use.",
    calibration_dataset_version="routing-dev-2026-09-18",
    questions=(
        Question(
            id="routing.choice",
            version="v1",
            owner=_OWNER_ROUTING,
            kind="choice",
            text="Which offered route should be taken for the next step?",
            group="choice",
            options=("no_route", "computer_use_fallback"),
        ),
        Question(
            id="routing.confident",
            version="v1",
            owner=_OWNER_ROUTING,
            kind="noul",
            text="Is the chosen route clearly the right one for this step?",
            group="confidence",
        ),
        Question(
            id="routing.risk_acceptable",
            version="v1",
            owner=_OWNER_ROUTING,
            kind="noul",
            text="Is the risk of the chosen route acceptable for this step?",
            group="risk",
        ),
        Question(
            id="routing.missing_inputs.{id}",
            version="v1",
            owner=_OWNER_ROUTING,
            kind="choice",
            text="Is required input {id} provided for the chosen route?",
            group="inputs",
            options=("provided", "missing", "unknown"),
        ),
    ),
)

# --------------------------------------------------------------------------- #
# 3. capability_ranking_v1                                                      #
# --------------------------------------------------------------------------- #

CAPABILITY_RANKING_V1 = QuestionPack(
    id="capability_ranking",
    version="v1",
    owner=_OWNER_CAPABILITY,
    purpose=(
        "Rank already-retrieved capabilities by fit for a stated need and say whether a "
        "real gap remains (MVP/03 §5.3). The capability pipeline decides whether to build."
    ),
    applicable_states=("capability_retrieval", "capability_gap"),
    risk_class="R1",
    fallback_policy="No advisory: retrieval score order stands.",
    calibration_dataset_version="capability-dev-2026-09-18",
    questions=(
        Question(
            id="cap.rank.{id}.fit",
            version="v1",
            owner=_OWNER_CAPABILITY,
            kind="score",
            text="How well does capability {id} fit the stated need?",
            group="fit",
            legend=(0.0, 1.0, 2.0, 3.0),
        ),
        Question(
            id="cap.rank.{id}.covers_need",
            version="v1",
            owner=_OWNER_CAPABILITY,
            kind="noul",
            text="Does capability {id} cover the stated need?",
            group="coverage",
        ),
        Question(
            id="cap.rank.best",
            version="v1",
            owner=_OWNER_CAPABILITY,
            kind="choice",
            text="Which offered capability fits the need best?",
            group="best",
            # Template options: the composer replaces them with the offered ids
            # (+ the reserved `no_fit`). A choice question needs >= 2 options, so
            # the placeholder states what happens instead of padding the list.
            options=("no_fit", "{candidate_id}"),
        ),
        Question(
            id="cap.rank.gap",
            version="v1",
            owner=_OWNER_CAPABILITY,
            kind="noul",
            text="Is there a real gap between the need and the offered capabilities?",
            group="gap",
        ),
    ),
)

# --------------------------------------------------------------------------- #
# 4. compaction_v1                                                              #
# --------------------------------------------------------------------------- #

COMPACTION_V1 = QuestionPack(
    id="compaction",
    version="v1",
    owner=_OWNER_CONTEXT,
    purpose=(
        "Two atomic readings per tool call — keep the call, keep the result verbatim — "
        "in the wording of fast-jev-compaction (MVP/03 §2.4/§5.4)."
    ),
    applicable_states=("compaction",),
    risk_class="R1",
    fallback_policy="No advisory: the existing compaction summary path stands.",
    calibration_dataset_version="compaction-dev-2026-09-18",
    questions=(
        Question(
            id="compaction.call.{id}.keep",
            version="v1",
            owner=_OWNER_CONTEXT,
            kind="noul",
            text="Should tool call {id} stay in the history for the assistant's next step?",
            group="call",
        ),
        Question(
            id="compaction.result.{id}.keep_verbatim",
            version="v1",
            owner=_OWNER_CONTEXT,
            kind="noul",
            text="Should the full output of tool call {id} stay verbatim instead of being re-run?",
            group="result",
        ),
    ),
)

# --------------------------------------------------------------------------- #
# 5. adversarial_test_v1                                                        #
# --------------------------------------------------------------------------- #

ADVERSARIAL_TEST_V1 = QuestionPack(
    id="adversarial_test",
    version="v1",
    owner=_OWNER_VERIFICATION,
    purpose=(
        "Plan the next adversarial release cases over indexed, observed surfaces with a "
        "deterministic oracle (MVP/03 §5.6). Jev never issues a release verdict."
    ),
    applicable_states=("release_test_planning", "defect_triage"),
    risk_class="R2",
    fallback_policy=(
        "No advisory: the deterministic episode catalogue in evalharness.py stands."
    ),
    calibration_dataset_version="adversarial-dev-2026-09-18",
    questions=(
        Question(
            id="adv.surface.pick",
            version="v1",
            owner=_OWNER_VERIFICATION,
            kind="choice",
            text="Which offered surface should the next adversarial case target?",
            group="surface",
            # Template options — replaced by the composer with the offered
            # surface ids (+ the reserved `no_surface`).
            options=("no_surface", "{surface_id}"),
        ),
        Question(
            id="adv.case.intent_class",
            version="v1",
            owner=_OWNER_VERIFICATION,
            kind="choice",
            text="Which intent class does the next adversarial case exercise?",
            group="intent",
            options=(
                "boundary",
                "stale_state",
                "permission",
                "concurrency",
                "injection",
                "persistence",
                "dependency_failure",
                "regression",
            ),
        ),
        Question(
            id="adv.case.priority",
            version="v1",
            owner=_OWNER_VERIFICATION,
            kind="score",
            text="How high is the priority of the next adversarial case?",
            group="priority",
            legend=(0.0, 1.0, 2.0, 3.0),
        ),
        Question(
            id="adv.case.covered_already",
            version="v1",
            owner=_OWNER_VERIFICATION,
            kind="noul",
            text="Is this adversarial case already covered by the recorded outcomes?",
            group="coverage",
        ),
        Question(
            id="adv.oracle.deterministic",
            version="v1",
            owner=_OWNER_VERIFICATION,
            kind="noul",
            text="Can the next adversarial case be judged by a deterministic oracle?",
            group="oracle",
        ),
    ),
)

# --------------------------------------------------------------------------- #
# 7. semantic_verification_v1                                                   #
# --------------------------------------------------------------------------- #

SEMANTIC_VERIFICATION_V1 = QuestionPack(
    id="semantic_verification",
    version="v1",
    owner=_OWNER_VERIFICATION,
    purpose=(
        "Read a bounded Evidence Brief and say whether the completion claim it "
        "carries is backed by it (MVP/93). The deterministic VERIFY verdict was "
        "computed before this call; Jev neither issues nor replaces it — an "
        "'unsupported' reading only keeps a deterministic PASS from being promoted "
        "to a completion."
    ),
    applicable_states=("verification", "completion_gate"),
    risk_class="R1",
    fallback_policy=(
        "No advisory: the deterministic VERIFY verdict stands alone and the record "
        "says the semantic gate returned no advisory."
    ),
    calibration_dataset_version="semantic-verify-dev-2026-09-19",
    questions=(
        Question(
            id="sem.claim.matches_objective",
            version="v1",
            owner=_OWNER_VERIFICATION,
            kind="noul",
            text=(
                "Does the completion claim address the objective it is attached to, "
                "rather than something adjacent to it?"
            ),
            group="objective",
        ),
        Question(
            id="sem.claim.backed",
            version="v1",
            owner=_OWNER_VERIFICATION,
            kind="noul",
            text=(
                "Is the completion claim backed by the recorded evidence in the brief "
                "(changed files, executed commands with their exit codes, evidence refs)?"
            ),
            group="backing",
        ),
        Question(
            id="sem.checks.sufficient",
            version="v1",
            owner=_OWNER_VERIFICATION,
            kind="noul",
            text=(
                "Are the executed checks sufficient to cover the objective, or do they "
                "cover only part of it?"
            ),
            group="coverage",
        ),
        Question(
            id="sem.gap.present",
            version="v1",
            owner=_OWNER_VERIFICATION,
            kind="noul",
            text=(
                "Does the brief leave an evidence gap: a part of the claim that nothing "
                "in the packet can support?"
            ),
            group="gap",
        ),
        Question(
            id="sem.support.verdict",
            version="v1",
            owner=_OWNER_VERIFICATION,
            kind="choice",
            text=(
                "Reading all of the above: is the claim supported by this brief, "
                "unsupported by it, or underspecified by it?"
            ),
            group="support",
            options=("supported", "unsupported", "underspecified"),
        ),
    ),
)

#: The six packs this package owns. ``voice_triage`` is **not** here: it is the
#: substrate's existing pack, reused through ``compose_voice_triage``.
JEV_PACKS: tuple[QuestionPack, ...] = (
    MEMORY_INJECTION_V1,
    ROUTING_V1,
    CAPABILITY_RANKING_V1,
    COMPACTION_V1,
    ADVERSARIAL_TEST_V1,
    SEMANTIC_VERIFICATION_V1,
)

for _pack in JEV_PACKS:  # §5 metadata is mandatory, so it is asserted at import
    assert_pack_metadata(_pack)


# --------------------------------------------------------------------------- #
# the registry view                                                             #
# --------------------------------------------------------------------------- #


def jev_registry() -> QuestionRegistry:
    """The substrate registry **plus** the five Jev packs — one registry, one view.

    Built by copying the substrate's registered packs (whatever version it
    serves) and adding the new ones. Registering a pack id the substrate already
    has would raise, which is exactly the "do not fork a pack" guard: if a
    substrate pack ever appears under one of our ids, this import fails loudly
    instead of silently shadowing it.
    """
    substrate = default_question_registry()
    registry = QuestionRegistry()
    for pack in substrate.packs():
        registry.register(pack)
    for pack in JEV_PACKS:
        registry.register(pack)
    return registry


@functools.lru_cache(maxsize=1)
def default_jev_registry() -> QuestionRegistry:
    """The one Jev registry instance (cached: a registry is not per-call state)."""
    return jev_registry()


def pack_by_ref(ref: str) -> QuestionPack:
    """Resolve a pack by ref (``compaction_v1``) or id (``compaction``)."""
    return default_jev_registry().pack(ref)


# --------------------------------------------------------------------------- #
# template instantiation                                                        #
# --------------------------------------------------------------------------- #


def instantiate(template: Question, substitutions: Mapping[str, object]) -> Question:
    """Substitute ``{name}`` placeholders in a registered question's id and text.

    The substituted id is *not* registered itself — it is the concrete instance
    of a registered template, and :func:`template_id_of` tells the two apart. A
    substitution that pushes the text past ``MAX_QUESTION_CHARS`` is refused
    loudly (never silently truncated), because the provider must see an atomic
    question.
    """
    question_id = template.id
    text = template.text
    for name, value in substitutions.items():
        token = "{" + str(name) + "}"
        question_id = question_id.replace(token, str(value))
        text = text.replace(token, str(value))
    if len(text) > MAX_QUESTION_CHARS:
        raise ValueError(
            f"instantiated question {question_id!r} is {len(text)} chars "
            f"(> {MAX_QUESTION_CHARS}) — the offered id is too long for the template "
            f"{template.id!r}"
        )
    return replace(template, id=question_id, text=text)


def template_id_of(question_id: str) -> str | None:
    """Which registered template an instantiated id came from — ``None`` if static."""
    for pack in JEV_PACKS:
        for question in pack.questions:
            if "{" not in question.id:
                if question.id == question_id:
                    return question.id
                continue
            prefix, _, suffix = question.id.partition("{")
            tail = suffix.split("}", 1)[1] if "}" in suffix else ""
            if question_id.startswith(prefix) and question_id.endswith(tail):
                return question.id
    return None


# --------------------------------------------------------------------------- #
# composers — one per kind                                                      #
# --------------------------------------------------------------------------- #


def _set(pack: QuestionPack, questions: Sequence[Question], groups: Sequence[str]) -> QuestionSet:
    return QuestionSet(
        id=pack.id,
        version=pack.version,
        owner=pack.owner,
        questions=tuple(questions),
        groups=tuple(groups),
    )


def compose_memory_injection(request) -> QuestionSet:
    """4 questions per offered memory (MVP/03 §5.1), in a stable order."""
    pack = MEMORY_INJECTION_V1
    templates = {question.group: question for question in pack.questions}
    questions: list[Question] = []
    for candidate in tuple(getattr(request, "candidates", ()) or ()):
        for group in ("relevance", "presence", "stance", "helpfulness"):
            questions.append(
                instantiate(templates[group], {"id": str(candidate.memory_id)})
            )
    return _set(pack, questions, ("relevance", "presence", "stance", "helpfulness"))


def compose_tool_routing(request) -> QuestionSet:
    """The candidate set + fallbacks as the offered choice (MVP/03 §5.2)."""
    pack = ROUTING_V1
    candidates = tuple(getattr(request, "candidates", ()) or ())
    options: list[str] = [str(candidate.candidate_id) for candidate in candidates]
    options.extend(("computer_use_fallback", "no_route"))
    choice = replace(pack.questions[0], options=tuple(options))
    questions: list[Question] = [choice]
    templates = {question.group: question for question in pack.questions}
    questions.append(templates["confidence"])
    questions.append(templates["risk"])
    slots: list[str] = []
    for candidate in candidates:
        for slot in tuple(getattr(candidate, "required_params", ()) or ()):
            if str(slot) not in slots:
                slots.append(str(slot))
    for slot in slots:
        questions.append(instantiate(templates["inputs"], {"id": slot}))
    return _set(pack, questions, ("choice", "confidence", "risk", "inputs"))


def compose_capability_ranking(request) -> QuestionSet:
    """Per-candidate fit + coverage, one best-fit choice, one gap reading (§5.3)."""
    pack = CAPABILITY_RANKING_V1
    candidates = tuple(getattr(request, "candidates", ()) or ())
    questions: list[Question] = []
    for candidate in candidates:
        for group in ("fit", "coverage"):
            question = [item for item in pack.questions if item.group == group][0]
            questions.append(
                instantiate(question, {"id": str(candidate.candidate_id)})
            )
    best = [item for item in pack.questions if item.group == "best"][0]
    options = [str(candidate.candidate_id) for candidate in candidates] + ["no_fit"]
    questions.append(replace(best, options=tuple(options)))
    questions.append([item for item in pack.questions if item.group == "gap"][0])
    return _set(pack, questions, ("fit", "coverage", "best", "gap"))


def compose_context_compaction(request) -> QuestionSet:
    """Two atomic readings per tool call of the history (MVP/03 §5.4)."""
    pack = COMPACTION_V1
    templates = {question.group: question for question in pack.questions}
    questions: list[Question] = []
    for call_id in compaction_call_ids(request):
        for group in ("call", "result"):
            questions.append(instantiate(templates[group], {"id": call_id}))
    return _set(pack, questions, ("call", "result"))


def compaction_call_ids(request) -> tuple[str, ...]:
    """The tool-call ids the compaction questions are asked about, in order.

    Only calls the transcript actually records are asked about, and a call
    without a paired result is not a candidate — the pairing rule of
    fast-jev-compaction §2.4 (1) expressed on the request's own history.
    """
    state = getattr(request, "state", None)
    ids: list[str] = []
    for entry in tuple(getattr(state, "history", ()) or ()):
        for call in tuple(getattr(entry, "tool_calls", ()) or ()):
            call_id = str(getattr(call, "id", ""))
            if not call_id:
                continue
            result_chars = int(getattr(call, "result_chars", 0) or 0)
            has_result = result_chars > 0 or bool(getattr(call, "is_error", False))
            if not has_result:
                continue
            if call_id not in ids:
                ids.append(call_id)
    return tuple(ids)


def compose_voice_triage(request) -> QuestionSet:
    """The substrate's own ``voice_triage_v1`` composition — reused, not forked (§27)."""
    trigger = str(getattr(request, "trigger_kind", "") or "*")
    return substrate_compose_voice_triage(trigger, registry=default_jev_registry())


def compose_adversarial_plan(request) -> QuestionSet:
    """Indexed surface choice + intent class + priority + coverage oracles (§5.6).

    The **offered** answer space is narrowed to the pairs the caller still has an
    uncovered case for (``request.remaining_pairs``, MVP/87): asking "which offered
    surface" while offering a surface whose every pair is spent is a question with an
    inadmissible answer, and offering all eight intent classes while the caller judges
    the pick against its own ledger is the same defect one layer down — measured: a live
    model answered ``boundary`` twice, the ledger had it, and the whole 12-case plan
    stopped at 2. ``no_surface`` stays in the options in every case, so a refusal remains
    expressible.
    """
    pack = ADVERSARIAL_TEST_V1
    surfaces = tuple(getattr(request, "surfaces", ()) or ())
    open_pairs = _remaining_pairs(getattr(request, "remaining_pairs", ()) or ())
    surface_question = [item for item in pack.questions if item.group == "surface"][0]
    options = [str(surface.surface_id) for surface in surfaces] + ["no_surface"]
    if open_pairs:
        # the offered surfaces, in the order the request lists them, restricted to those
        # that still own a pair; the intent classes likewise (declared order kept)
        live_surfaces = {surface_id for surface_id, _intent in open_pairs}
        narrowed = [item for item in options if item in live_surfaces]
        options = (narrowed or options) + ["no_surface"]
    questions: list[Question] = [replace(surface_question, options=tuple(dict.fromkeys(options)))]
    intent_question = [item for item in pack.questions if item.group == "intent"][0]
    if open_pairs:
        # declared order first (the pack's own vocabulary), then whatever the caller sent. A
        # choice question needs **two** options (the substrate refuses one), so a single open
        # intent class keeps the declared vocabulary: the last case of a plan then reaches the
        # composer with all eight classes and the planner's named repeated-pair refusal plus
        # its retry covers the residual risk instead of a one-option question crashing.
        open_intents = {intent for _surface, intent in open_pairs}
        narrowed_intents = [item for item in intent_question.options if item in open_intents]
        if len(narrowed_intents) >= 2:
            intent_question = replace(intent_question, options=tuple(narrowed_intents))
    questions.append(intent_question)
    questions.extend(
        item for item in pack.questions if item.group in ("priority",)
    )
    questions.extend(
        item for item in pack.questions if item.group in ("coverage", "oracle")
    )
    return _set(
        pack,
        questions,
        ("surface", "intent", "priority", "coverage", "oracle"),
    )


def _remaining_pairs(raw: Sequence[object]) -> tuple[tuple[str, str], ...]:
    """Parse ``"<surface_id>/<intent_class>"`` entries, dropping anything unshaped."""
    pairs: list[tuple[str, str]] = []
    for entry in raw:
        surface_id, _, intent_class = str(entry).partition("/")
        if surface_id.strip() and intent_class.strip():
            pairs.append((surface_id.strip(), intent_class.strip()))
    return tuple(pairs)


def compose_semantic_verification(request) -> QuestionSet:
    """The five static readings of MVP/93 — no per-item instantiation.

    The brief itself is *not* fanned out into one question per file or per
    command: a hundred files would be a hundred questions for one decision the
    caller may veto anyway. The packet is the state; these five questions are the
    readings, and every question id here is registered in
    :data:`SEMANTIC_VERIFICATION_V1` (so the provider can never invent one).
    """
    pack = SEMANTIC_VERIFICATION_V1
    return _set(pack, pack.questions, ("objective", "backing", "coverage", "gap", "support"))


COMPOSERS: dict[str, Callable[[object], QuestionSet]] = {
    "memory_injection": compose_memory_injection,
    "tool_routing": compose_tool_routing,
    "capability_ranking": compose_capability_ranking,
    "context_compaction": compose_context_compaction,
    "voice_triage": compose_voice_triage,
    "adversarial_test_planning": compose_adversarial_plan,
    "semantic_verification": compose_semantic_verification,
}


def composer_for(kind: str) -> Callable[[object], QuestionSet]:
    """The composer of ``kind`` — one function per kind, no inline prompt text."""
    try:
        return COMPOSERS[kind]
    except KeyError as exc:
        raise KeyError(f"no question composer for kind {kind!r}") from exc


def compose_questions_for(request) -> QuestionSet:
    """Compose the question set for a request object.

    The batching rule of §5.1 (at most ``CANDIDATES_PER_REQUEST`` candidates per
    provider request) is enforced by ``decide()`` as a deterministic refusal: one
    call = one batch, and a caller with a larger candidate set splits it itself
    instead of the façade silently dropping candidates it cannot ask about.
    """
    return composer_for(str(getattr(request, "kind", "")))(request)


__all__ = [
    "ADVERSARIAL_TEST_V1",
    "CANDIDATES_PER_REQUEST",
    "CAPABILITY_RANKING_V1",
    "CHARS_PER_TOKEN_FLOOR",
    "COMPACTION_V1",
    "COMPOSERS",
    "JEV_PACKS",
    "MEMORY_INJECTION_V1",
    "ROUTING_V1",
    "SEMANTIC_VERIFICATION_V1",
    "compaction_call_ids",
    "compose_adversarial_plan",
    "compose_capability_ranking",
    "compose_context_compaction",
    "compose_memory_injection",
    "compose_questions_for",
    "compose_semantic_verification",
    "compose_tool_routing",
    "compose_voice_triage",
    "composer_for",
    "default_jev_registry",
    "instantiate",
    "jev_registry",
    "pack_by_ref",
    "template_id_of",
]
