"""``decide()`` — the single entry point of the Jev façade (MVP/03 §4.3).

One decision at a time, one question pack, one typed answer:

    request  →  typed pack composition  →  SystemOneService.advise()
             →  validated narrowing     →  JevDecision (advisory)

The rules this module obeys:

* **The only provider edge.** No other module in ``skaldir.jev`` imports
  ``skaldir.system_one_service`` (pinned by a test): the façade calls the
  substrate's one facade, it does not build a second one. The *client* the
  substrate talks to is resolved here as well, through
  :func:`skaldir.llm_provider.provider_for` — the module that owns the real
  OpenAI-compatible adapter (``godsv.llm`` is a thin re-export and is on this
  package's forbidden-import list) — and only when the flags name the LLM adapter
  and the caller supplied neither a service nor an evaluator — a caller that
  wires its own provider owns that edge (see :func:`_provider_client`).
  :func:`service_for` is the public construction path those callers use, so
  "build me the runtime's Jev service" never means "import the service facade".
* **Always returns a decision, never raises for a provider problem.** A disabled
  provider, a blocked credential, a deadline or a stale result all become
  ``status="blocked"`` with the caller's own fallback named — never a fabricated
  answer. A *malformed* answer or a missing one is a
  :class:`~skaldir.system_one.SystemOneContractError`; a hard-rule hit is a
  :class:`~skaldir.jev.rules.JevRuleViolation`.
* **Never decides authority.** The decision is built here, but every hard rule
  is re-checked at construction (:func:`~skaldir.jev.rules.assert_decision_authority`
  with the request in hand): goal echo, monotone caution, no denied route, an
  owner-authored memory kept, targets that resolve to offered surfaces.

Deterministic short-circuits — cases where Jev answers *without* spending a
provider call because the request already contains the answer: a denied-only
candidate set (``no_route`` / ``permission_denied_only``). Those decisions are
labelled ``provider_mode="disabled"``, because no provider ran.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any

from ..system_one import (
    PROVIDER_LLM_ADAPTER,
    ChoiceAnswer,
    EvaluationContext,
    NoulAnswer,
    ScoreAnswer,
    SystemOneContractError,
    SystemOneEvaluation,
    SystemOneState,
    answer_confidence,
)
from ..system_one_policy import (
    RULE_COMPLETION_CLAIM_WITHOUT_EVIDENCE,
    RULE_DRIFT_GOAL_SUPPORT_MISSING,
    RULE_LOOP_WITHOUT_PROGRESS,
    RULE_RECOVERY_SYSTEM_TWO,
    RULE_UNTRUSTED_AUTHORITY_ATTEMPT,
    RULE_UNTRUSTED_SECRET_REQUEST,
)
from ..system_one_service import SystemOneService, build_service
from . import contracts as C
from .kinds import (
    DEFAULT_READ_THRESHOLDS,
    ReadThresholds,
    fallback_for,
    pack_ref_for,
)
from .packs import (
    CANDIDATES_PER_REQUEST,
    CHARS_PER_TOKEN_FLOOR,
    compaction_call_ids,
    compose_questions_for,
    pack_by_ref,
)
from .rules import (
    HARD_CAUTION_FLOOR,
    assert_decision_authority,
    caution_floor_for,
)

#: Emitted on every decision — one payload, no secrets, no state (§7.2).
DECISION_EVENT = "jev_decision"

#: Reason codes that are not a substrate policy rule name.
REASON_NO_CANDIDATES = "no_candidates"
REASON_NO_COURSES = "no_routes"
REASON_NO_SURFACES = "no_surfaces"
REASON_TOO_MANY_CANDIDATES = "too_many_candidates"
REASON_NO_STATE = "no_state"
REASON_NO_GOAL_BINDING = "no_goal_binding"
REASON_STATE_TOO_LARGE = "state_too_large"
REASON_NETWORK_FORBIDDEN = "budget_forbids_network"
REASON_NO_BUDGET = "no_budget"
REASON_PERMISSION_DENIED_ONLY = "permission_denied_only"
REASON_BELOW_THRESHOLD = "below_threshold_widen_retrieval"
REASON_RISK_NOT_ACCEPTABLE = "risk_not_acceptable"
REASON_KIND_NOT_ALLOWED = "kind_not_allowed"
REASON_CANDIDATE_UNHEALTHY = "candidate_unhealthy"
REASON_COMPUTER_USE_FALLBACK = "computer_use_fallback"
REASON_NO_ROUTE = "no_route"
REASON_DEFER_SENSIBLE_PATH = "defer_sensible_path"
REASON_PROVIDER_UNCONFIGURED = "provider_unconfigured"
REASON_NO_INTERVENTION = "no_intervention_needed"
REASON_NO_OVERRIDE = "no_override_needed"
REASON_NARROW_BUDGET = "batch_budget"
REASON_PLANNED = "planned"
REASON_NO_COMPLETION_CLAIM = "no_completion_claim"
REASON_NO_EVIDENCE_TO_CHECK = "no_evidence_to_check"
REASON_BEST_FIT = "best_fit"
REASON_NO_FIT = "no_fit_above_min_fit"

#: How a surface kind maps onto an operation and a deterministic oracle
#: (**declared**, so the plan contains no generated text and no selector).
SURFACE_OPERATION: dict[str, str] = {
    "ui_flow": "click",
    "api_endpoint": "http_request",
    "cli": "cli_run",
    "file_io": "file_write",
    "permission_boundary": "permission_probe",
}

SURFACE_ORACLE: dict[str, tuple[str, str]] = {
    "ui_flow": ("observed_state", "the surface's state changes and survives a reload"),
    "api_endpoint": ("http_status", "the endpoint answers with the status the case expects"),
    "cli": ("exit_code", "the command's exit code matches the case's expectation"),
    "file_io": ("file_diff", "the written file differs as the case expects and survives reload"),
    "permission_boundary": ("denied", "the boundary denies the probe without a side effect"),
}

#: The declared vocabulary of ``CapabilityRankingEntry.reasons`` — tokens, never prose.
CAPABILITY_REASON_TOKENS: tuple[str, ...] = (
    "covers_need",
    "scope_match",
    "health_ok",
    "authority_trusted",
    "recently_used",
)


# --------------------------------------------------------------------------- #
# the public API                                                                #
# --------------------------------------------------------------------------- #


async def decide_async(
    request: C.JevRequestBase | str,
    state: Mapping[str, Any] | None = None,
    *,
    service: SystemOneService | None = None,
    flags: Any | None = None,
    evaluator: Any | None = None,
    llm: Any | None = None,
    live: Any | None = None,
    thresholds: ReadThresholds | None = None,
    shadow: bool = False,
    emit: Any | None = None,
) -> C.JevDecision:
    """One typed decision. See the module docstring for the failure contract."""
    typed = _coerce_request(request, state)
    reads_thresholds = thresholds or DEFAULT_READ_THRESHOLDS

    refusal = _preflight(typed)
    if refusal is not None:
        reason, fallback = refusal
        return _finish(
            _blank_decision(typed, status="refused", reason=reason, fallback=fallback),
            typed,
            service=service,
            emit=emit,
        )

    if _denied_only(typed):
        answer = C.ToolRoutingAnswer(
            chosen_kind="no_route",
            route=None,
            requires_approval=False,
            reason_code=REASON_PERMISSION_DENIED_ONLY,
        )
        pack_id, pack_version = _pack_identity(str(typed.kind))
        decision = _build_decision(
            typed,
            answer=answer,
            status="ok",
            provider_mode="disabled",
            live_jev=False,
            refusal_reason="",
            fallback="",
            question_pack_id=pack_id,
            question_pack_version=pack_version,
            confidence=1.0,
            caution_level=caution_floor_for(typed),
            latency_ms=0,
            attempts=0,
            usage={"deterministic": True},
            evidence_refs=(),
        )
        return _finish(decision, typed, service=service, emit=emit)

    active_llm, provider_problem = _provider_client(
        flags, llm, service=service, evaluator=evaluator
    )
    active = _resolve_service(
        service=service, flags=flags, evaluator=evaluator, llm=active_llm
    )
    blocked_reason = _blocked_reason(
        active, flags=flags, provider_problem=provider_problem
    )
    if blocked_reason:
        return _finish(
            _blank_decision(
                typed,
                status="blocked",
                reason=blocked_reason,
                fallback=fallback_for(typed.kind),
            ),
            typed,
            service=active,
            emit=emit,
        )

    questions = compose_questions_for(typed)
    state_payload = _state_for(typed)
    context = EvaluationContext(
        run_id=typed.run_id,
        goal_id=typed.goal_id,
        goal_revision=int(typed.goal_revision),
        risk_class=str(typed.risk_class),
        deadline=time.time() + max(0, int(typed.deadline_ms)) / 1000.0,
        caller=f"jev.{typed.kind}",
        question_pack_id=questions.id,
        question_pack_version=questions.version,
        shadow=bool(shadow),
    )
    try:
        evaluation = await active.advise(
            state=SystemOneState(
                payload=state_payload,
                input_scope="task",
                source_event_refs=tuple(getattr(typed, "evidence_refs", ()) or ()),
            ),
            questions=questions,
            context=context,
            live=live,
        )
    except SystemOneContractError as exc:
        # The substrate refused to run or to label the run (measured case: the
        # live adapter's service wrapper reports its "can produce at best"
        # provider_mode before any authorized call happened, and refuses to label
        # a live access mode as anything else). That is "no advisory", not a
        # decision: map it to ``blocked`` and name the substrate's refusal
        # instead of turning it into a caller-facing contract error.
        return _finish(
            _blank_decision(
                typed,
                status="blocked",
                reason=f"provider_contract_refusal: {str(exc)[:160]}",
                fallback=fallback_for(typed.kind),
                attempted=attempted_outbound(active),
            ),
            typed,
            service=active,
            emit=emit,
        )
    if evaluation is None:
        return _finish(
            _blank_decision(
                typed,
                status="blocked",
                reason=_blocker_reason(active),
                fallback=fallback_for(typed.kind),
                # a call the gate recorded (measured: HTTP 402) is an attempt —
                # `provider_mode: disabled` must not be read as "never called"
                attempted=attempted_outbound(active),
            ),
            typed,
            service=active,
            emit=emit,
        )

    if not evaluation.answers:
        # A provider that answered nothing: honest when it is the disabled
        # evaluator, a contract error when a real provider was supposed to answer.
        if str(evaluation.provider_mode) == "disabled":
            return _finish(
                _blank_decision(
                    typed,
                    status="blocked",
                    reason="provider_disabled",
                    fallback=fallback_for(typed.kind),
                ),
                typed,
                service=active,
                emit=emit,
            )

    _require_answers(typed, evaluation, questions)

    answer = _narrow(typed, evaluation, questions, reads_thresholds)
    decision = _build_decision(
        typed,
        answer=answer,
        status="ok",
        provider_mode=str(evaluation.provider_mode),
        live_jev=bool(evaluation.live_jev),
        refusal_reason="",
        fallback="",
        question_pack_id=str(evaluation.question_pack_id or questions.id),
        question_pack_version=str(evaluation.question_pack_version or questions.version),
        confidence=_confidence(evaluation),
        caution_level=_caution_level(typed, answer),
        latency_ms=int(evaluation.latency_ms),
        attempts=int(evaluation.attempts),
        usage=dict(evaluation.usage or {}),
        evidence_refs=(
            *tuple(getattr(typed, "evidence_refs", ()) or ()),
            str(evaluation.evaluation_id),
        ),
    )
    return _finish(decision, typed, service=active, emit=emit)


def decide(
    request: C.JevRequestBase | str,
    state: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> C.JevDecision:
    """Synchronous :func:`decide_async` — for the CLI and for non-async callers.

    A running event loop is refused loudly instead of being worked around: an
    ``asyncio.run`` inside a loop would either deadlock or spawn a second loop,
    and neither is a decision path.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(decide_async(request, state, **kwargs))
    raise SystemOneContractError(
        "decide() cannot run inside a running event loop — await decide_async() instead"
    )


decide_sync = decide


# --------------------------------------------------------------------------- #
# request handling                                                              #
# --------------------------------------------------------------------------- #


def _coerce_request(
    request: C.JevRequestBase | str, state: Mapping[str, Any] | None
) -> C.JevRequestBase:
    if isinstance(request, str):
        return C.request_for(request, state)
    if state is not None:
        raise SystemOneContractError(
            "decide() takes either a typed request or (kind, state) — not both"
        )
    return request


def _state_for(request: C.JevRequestBase) -> dict:
    """The payload the provider may see: the request's own declared fields.

    Deliberately built from the model's declared fields and nothing else — no
    runtime snapshot, no ambient state (the caller's digests are already
    secret-redacted by the sanitizer, MVP/03 §5.1). ``model_dump`` on a frozen,
    ``extra="forbid"`` model is exactly the declared field set.
    """
    payload = request.model_dump(mode="json")
    for identity in ("run_id", "goal_id", "goal_revision", "state_fingerprint"):
        payload.pop(identity, None)
    return payload


# --------------------------------------------------------------------------- #
# preflight — the deterministic refusals (no provider call spent)               #
# --------------------------------------------------------------------------- #


def _preflight(request: C.JevRequestBase) -> tuple[str, str] | None:
    """Return ``(refusal_reason, fallback)`` when the request itself refuses."""
    kind = str(request.kind)
    fallback = fallback_for(kind)
    if kind == "memory_injection":
        if not str(request.goal_id or "").strip() or not str(request.goal_text or "").strip():
            return REASON_NO_GOAL_BINDING, fallback
        if not request.candidates:
            return REASON_NO_CANDIDATES, fallback
        if len(request.candidates) > CANDIDATES_PER_REQUEST:
            return REASON_TOO_MANY_CANDIDATES, fallback
    elif kind == "tool_routing":
        if not request.candidates:
            return REASON_NO_COURSES, fallback
        if len(request.candidates) > CANDIDATES_PER_REQUEST:
            return REASON_TOO_MANY_CANDIDATES, fallback
    elif kind == "capability_ranking":
        if not request.candidates:
            return REASON_NO_CANDIDATES, fallback
        if len(request.candidates) > CANDIDATES_PER_REQUEST:
            return REASON_TOO_MANY_CANDIDATES, fallback
    elif kind == "context_compaction":
        if not compaction_call_ids(request):
            return REASON_NO_STATE, fallback
        options = request.options
        limit = int(options.max_state_tokens) * CHARS_PER_TOKEN_FLOOR
        if len(str(request.state.context or "")) > limit:
            # A conservative pre-check: at 4 chars/token even a perfect fit
            # cannot reach the budget, so the call is not worth spending. The
            # authoritative fit verdict belongs to the compaction port.
            return REASON_STATE_TOO_LARGE, "the existing compaction summary path"
    elif kind == "voice_triage":
        if not request.signals and not request.trajectory_window:
            return REASON_NO_STATE, "the deterministic Voice hint alone"
    elif kind == "adversarial_test_planning":
        if not request.surfaces:
            return REASON_NO_SURFACES, fallback
        budget = request.budget
        if not budget.allow_network and any(
            "http_request" in tuple(surface.seed_operations or ())
            for surface in request.surfaces
        ):
            return REASON_NETWORK_FORBIDDEN, fallback
        if int(budget.max_cases) <= 0 or len(request.previous_results) >= int(
            budget.max_cases
        ):
            return REASON_NO_BUDGET, fallback
    elif kind == "semantic_verification":
        if not str(request.claim or "").strip():
            return REASON_NO_COMPLETION_CLAIM, fallback
        if not (request.files or request.commands or request.evidence_refs):
            # Nothing to read: a claim with no recorded evidence at all is not a
            # case for a provider call, it is a case for the deterministic layer
            # (which is exactly why this is a *refusal*, not a fabricated
            # "unsupported" reading).
            return REASON_NO_EVIDENCE_TO_CHECK, fallback
    return None


def _denied_only(request: C.JevRequestBase) -> bool:
    """True when every offered route is denied — answered without a provider (R2)."""
    if str(request.kind) != "tool_routing":
        return False
    candidates = tuple(request.candidates or ())
    return bool(candidates) and all(
        str(candidate.permission_state) == "denied" for candidate in candidates
    )


# --------------------------------------------------------------------------- #
# service resolution / blocking                                                 #
# --------------------------------------------------------------------------- #


def _provider_client(
    flags: Any | None,
    llm: Any | None,
    *,
    service: SystemOneService | None = None,
    evaluator: Any | None = None,
    env: Mapping[str, str] | None = None,
    config_path: Any = None,
) -> tuple[Any | None, str]:
    """The LLM client the flags ask for: the caller's, else the configured provider.

    :func:`skaldir.llm_provider.provider_for` is the one documented way to a real
    client (explicit config > environment > config file, and secrets only from
    the environment). It is asked for **only** when the flags name the LLM adapter
    and the caller brought neither a service nor an evaluator of its own — a
    caller that supplies one owns the provider edge.

    ``env``/``config_path`` are forwarded verbatim so a caller can pin the
    resolution to an isolated environment (tests do: an inherited
    ``SKALDIR_LLM_*`` must not turn an offline test into a live one).

    **The decision layer's own block wins when nobody else names an endpoint**
    (MVP/87): the runtime's documented order is ``godsv.jev`` →
    ``godsv.providers.entries.jev`` → ``godsv.llm``
    (:func:`skaldir.jev.runtime.provider_resolution`,
    the same rule ``skaldir jev status`` and the ROUTE probe *report*). Building the
    client from the general ``godsv.llm`` block instead let the client and the report
    disagree — measured: the record named ``godsv.jev`` (``deepseek-chat``, the JEV
    block's budget) while the request really went out as ``godsv.llm``'s
    ``deepseek-flash`` with the 900-token default, which spent its whole budget on
    ``reasoning_content`` and returned an empty completion → ROUTE blocked, exit 2.
    The TypeSafe-first resolution is therefore used whenever the caller pinned
    nothing *and* the environment names no provider override (an isolated run keeps
    the old behaviour — that decision belongs to the caller).

    Every failure (nothing configured, a secret written into a config file, a
    missing ``httpx``) becomes a *named* blocker string for
    :func:`_blocked_reason`: the caller gets ``provider_unconfigured`` plus the
    exact exports to set, never a fabricated answer and never a crash.
    """
    if llm is not None:
        return llm, ""
    if service is not None or evaluator is not None:
        return None, ""
    if str(getattr(flags, "provider", "")) != PROVIDER_LLM_ADAPTER:
        return None, ""
    try:
        if _ambient_provider_pin(env) is None and config_path is None and env is None:
            from .runtime import provider_resolution

            resolution = provider_resolution()
            if resolution["client"] is None:
                from ..llm_provider import ProviderConfigError

                raise ProviderConfigError(
                    f"{resolution['source']}: {resolution['problem']}"
                )
            return resolution["client"], ""
        from ..llm_provider import provider_for

        return provider_for(env=env, config_path=config_path), ""
    except Exception as exc:  # noqa: BLE001 — a wiring problem is a blocker, not a bug
        return None, " ".join(str(exc).split())[:240]


def _ambient_provider_pin(env: Mapping[str, str] | None = None) -> str | None:
    """The variable that pins this call to an endpoint, or ``None`` when nothing does.

    Only the *configuration* names count (``SKALDIR_LLM_BASE_URL``/``_MODEL``/…): the
    credential is not a pin — it is exactly what the config file's ``api_key_env`` names,
    and keeping it is what lets the JEV block answer at all.
    """
    import os

    from ..llm_provider import ENV_CONFIG, ENV_CONTRACT

    source = os.environ if env is None else dict(env)
    if str(source.get(ENV_CONFIG) or "").strip():
        return ENV_CONFIG
    for key, names in ENV_CONTRACT.items():
        if key == "api_key":
            continue
        for name in names:
            if str(source.get(name) or "").strip():
                return name
    return None


def provider_binding(
    flags: Any | None,
    llm: Any | None = None,
    *,
    env: Mapping[str, str] | None = None,
    config_path: Any = None,
) -> tuple[Any | None, str]:
    """``(client, problem)`` — the provider the flags name, or the named blocker.

    The public spelling of :func:`_provider_client` for callers that must
    *report* why no advisory can exist (the runtime's ROUTE status) instead of
    only acting on it.
    """
    return _provider_client(flags, llm, env=env, config_path=config_path)


def service_for(
    flags: Any | None = None,
    *,
    evaluator: Any | None = None,
    llm: Any | None = None,
    env: Mapping[str, str] | None = None,
    config_path: Any = None,
    emit: Any | None = None,
) -> SystemOneService | None:
    """Build the one :class:`SystemOneService` the flags ask for — provider wired.

    This is the construction path a caller outside this module uses (the runtime
    binding, the CLI's ROUTE probe): only ``decide.py`` may import the service
    facade, so "give me the wired service" is answered *here* rather than by a
    second edge into the substrate.

    * an explicit ``evaluator`` (the offline fixture, a live adapter) wins — the
      service is exactly that evaluator plus the flags;
    * otherwise the provider the flags name is resolved first
      (:func:`provider_binding`) and the client is handed to the substrate's own
      :func:`skaldir.system_one_service.build_service` — one construction path, no
      second one (§27);
    * a provider that cannot be built is **not** replaced by a scripted one: the
      service is built with ``llm=None`` (the substrate then degrades to its
      disabled evaluator) and the caller names the blocker it got back.

    ``emit`` is the subscription the caller attaches (MVP/87): without it the
    session's event store never learns that a decision happened, which is exactly
    the gap :mod:`skaldir.jev.wiring` closes. It is forwarded to the substrate's
    one construction path so the emitter is wired in one place, not by editing an
    attribute afterwards.
    """
    if evaluator is not None:
        return SystemOneService(evaluator=evaluator, flags=flags, emit=emit)
    if flags is None:
        return None
    client, _problem = provider_binding(flags, llm, env=env, config_path=config_path)
    return build_service(flags=flags, llm=client, emit=emit)


def _resolve_service(
    *,
    service: SystemOneService | None,
    flags: Any | None,
    evaluator: Any | None,
    llm: Any | None = None,
) -> SystemOneService | None:
    if service is not None:
        return service
    if evaluator is not None:
        return SystemOneService(evaluator=evaluator, flags=flags)
    if flags is not None:
        from ..system_one_flags import build_evaluator

        return SystemOneService(evaluator=build_evaluator(flags, llm=llm), flags=flags)
    return None


def _blocked_reason(
    service: SystemOneService | None, *, flags: Any | None, provider_problem: str = ""
) -> str:
    """Why no advisory can exist — named, never a fabricated answer."""
    if service is None:
        return "no_service"
    if provider_problem:
        # Wanted provider, but no client could be built: the honest reason plus
        # what to configure. Distinguishable from a deliberately disabled provider.
        return f"{REASON_PROVIDER_UNCONFIGURED}: {provider_problem}"
    if flags is not None and not bool(getattr(flags, "enabled", False)):
        return "system_one_disabled"
    if not service.enabled:
        return "system_one_disabled"
    if service.provider_name == "disabled":
        return "provider_disabled"
    if str(getattr(flags, "provider", "")) == "disabled":
        return "provider_disabled"
    return ""


def _blocker_reason(service: SystemOneService) -> str:
    """The structured blocker the substrate recorded, if it recorded one."""
    for record in reversed(list(getattr(service.gate, "errors", ()) or ())):
        reason = str(record.get("reason") or record.get("state") or "").strip()
        if reason:
            return reason[:120]
    return "no_advisory"


def _require_answers(request, evaluation: SystemOneEvaluation, questions) -> None:
    """A missing answer is a contract error — never a default."""
    missing = [qid for qid in questions.ids if qid not in evaluation.answers]
    if not missing:
        return
    raise SystemOneContractError(
        f"{request.kind}: provider returned no answer for {len(missing)} of "
        f"{len(questions.ids)} questions (e.g. {missing[0]!r}) — a missing answer is a "
        "contract error, not a default"
    )


# --------------------------------------------------------------------------- #
# reading the evaluation                                                        #
# --------------------------------------------------------------------------- #


class _Reads:
    """Typed, validated access to one evaluation's answers.

    Validation mirrors the reference implementation's ``validate_choice``: the
    chosen option must be one of the question's **offered** options, and it must
    be the argmax of its own distribution. Nothing is repaired, defaulted or
    guessed.
    """

    def __init__(self, evaluation: SystemOneEvaluation, questions) -> None:
        self.evaluation = evaluation
        self.options = {
            question.id: tuple(str(option) for option in question.options)
            for question in questions.questions
        }
        self.legends = {
            question.id: tuple(float(value) for value in question.legend)
            for question in questions.questions
        }

    def has(self, question_id: str) -> bool:
        return question_id in self.evaluation.answers

    def find(self, suffix: str) -> str | None:
        for question_id in self.evaluation.answers:
            if question_id.endswith(suffix):
                return question_id
        return None

    def noul(self, question_id: str) -> float | None:
        answer = self.evaluation.answers.get(question_id)
        if answer is None:
            return None
        if not isinstance(answer, NoulAnswer):
            raise SystemOneContractError(
                f"{question_id}: expected a noul answer, got {type(answer).__name__}"
            )
        return float(answer.value)

    def noul_suffix(self, suffix: str) -> float | None:
        question_id = self.find(suffix)
        return self.noul(question_id) if question_id else None

    def choice(self, question_id: str) -> str | None:
        answer = self.evaluation.answers.get(question_id)
        if answer is None:
            return None
        if not isinstance(answer, ChoiceAnswer):
            raise SystemOneContractError(
                f"{question_id}: expected a choice answer, got {type(answer).__name__}"
            )
        offered = self.options.get(question_id)
        if offered is not None and answer.choice not in offered:
            raise SystemOneContractError(
                f"{question_id}: choice {answer.choice!r} is not one of the offered "
                f"options {list(offered)}"
            )
        top = max(answer.probabilities.values()) if answer.probabilities else 0.0
        if answer.probabilities and abs(
            float(answer.probabilities[answer.choice]) - float(top)
        ) > 1e-6:
            raise SystemOneContractError(
                f"{question_id}: choice {answer.choice!r} is not the argmax of its own "
                "distribution"
            )
        return str(answer.choice)

    def choice_suffix(self, suffix: str) -> str | None:
        question_id = self.find(suffix)
        return self.choice(question_id) if question_id else None

    def score(self, question_id: str) -> float | None:
        answer = self.evaluation.answers.get(question_id)
        if answer is None:
            return None
        if not isinstance(answer, ScoreAnswer):
            raise SystemOneContractError(
                f"{question_id}: expected a score answer, got {type(answer).__name__}"
            )
        legend = self.legends.get(question_id) or tuple(answer.legend)
        if legend and float(answer.score) not in legend:
            raise SystemOneContractError(
                f"{question_id}: score {answer.score} is not in the question's legend "
                f"{list(legend)}"
            )
        return float(answer.score)

    def legend(self, question_id: str) -> tuple[float, ...]:
        return self.legends.get(question_id) or ()


def _confidence(evaluation: SystemOneEvaluation) -> float:
    """The decision's confidence: the mean of the answers that *have* one."""
    values = [
        value
        for value in (
            answer_confidence(answer) for answer in evaluation.answers.values()
        )
        if value is not None
    ]
    if not values:
        return 0.0
    return max(0.0, min(1.0, sum(values) / len(values)))


# --------------------------------------------------------------------------- #
# narrowing — one function per kind                                             #
# --------------------------------------------------------------------------- #


def _narrow(request, evaluation, questions, thresholds: ReadThresholds):
    reads = _Reads(evaluation, questions)
    kind = str(request.kind)
    if kind == "memory_injection":
        return _memory_answer(request, reads, thresholds)
    if kind == "tool_routing":
        return _routing_answer(request, reads, thresholds)
    if kind == "capability_ranking":
        return _capability_answer(request, reads, thresholds)
    if kind == "context_compaction":
        return _compaction_answer(request, reads, thresholds)
    if kind == "voice_triage":
        return _voice_answer(request, reads, thresholds)
    if kind == "adversarial_test_planning":
        return _adversarial_answer(request, reads, thresholds)
    if kind == "semantic_verification":
        return _semantic_answer(request, reads, thresholds)
    raise SystemOneContractError(f"no narrowing for kind {kind!r}")  # pragma: no cover


def _require(value: Any, where: str) -> Any:
    if value is None:
        raise SystemOneContractError(f"{where}: the provider did not answer")
    return value


# -- 1. memory_injection ---------------------------------------------------- #


def _memory_answer(request, reads: _Reads, thresholds: ReadThresholds):
    verdicts: list[C.MemoryVerdict] = []
    ranked: list[tuple[float, str]] = []
    for candidate in request.candidates:
        memory_id = str(candidate.memory_id)
        relevance = _require(
            reads.noul(f"mem.{memory_id}.relevant_now"),
            f"mem.{memory_id}.relevant_now",
        )
        presence = _require(
            reads.noul(f"mem.{memory_id}.already_in_context"),
            f"mem.{memory_id}.already_in_context",
        )
        stance = _require(
            reads.choice(f"mem.{memory_id}.stance"), f"mem.{memory_id}.stance"
        )
        _require(
            reads.score(f"mem.{memory_id}.helpfulness"),
            f"mem.{memory_id}.helpfulness",
        )
        if candidate.protected:
            # Owner-authored: injected verbatim, never dropped, never deferred (R4).
            read = "inject_now"
        elif stance == "stale":
            read = "defer"
        elif relevance < thresholds.drop_at:
            read = "drop"
        elif presence >= thresholds.read_at:
            read = "defer"
        elif relevance >= thresholds.influence_at:
            read = "inject_now"
        else:
            read = "defer"
        if read == "inject_now":
            ranked.append((relevance, memory_id))
        verdicts.append(
            C.MemoryVerdict(
                memory_id=memory_id,
                read=read,
                relevance=float(relevance),
                already_in_context=bool(presence >= thresholds.read_at),
                stance=str(stance),
                rank=0,
            )
        )

    ranked.sort(key=lambda item: (-item[0], item[1]))
    protected = {
        str(candidate.memory_id)
        for candidate in request.candidates
        if bool(candidate.protected)
    }
    # Owner-authored memories are injected first and cannot be pushed out by the
    # budget: the budget then caps the *remaining* slots (R4).
    protected_ranked = [item for item in ranked if item[1] in protected]
    other_ranked = [item for item in ranked if item[1] not in protected]
    budget = int(request.budget.max_inject)
    limit = max(budget, len(protected_ranked))
    remaining = max(0, limit - len(protected_ranked))
    injected = tuple(memory_id for _, memory_id in protected_ranked) + tuple(
        memory_id for _, memory_id in other_ranked[:remaining]
    )
    ordering = tuple(injected)
    rank_of = {memory_id: index + 1 for index, memory_id in enumerate(injected)}
    final_verdicts = tuple(
        C.MemoryVerdict(
            memory_id=item.memory_id,
            read=(
                "inject_now"
                if item.memory_id in rank_of
                else ("defer" if item.read == "inject_now" else item.read)
            ),
            relevance=item.relevance,
            already_in_context=item.already_in_context,
            stance=item.stance,
            rank=rank_of.get(item.memory_id, 0),
        )
        for item in verdicts
    )
    if injected:
        chosen: str = "inject"
    elif any(item.read == "defer" for item in final_verdicts):
        chosen = "defer"
    else:
        chosen = "none"
    return C.MemoryInjectionAnswer(
        chosen=chosen,
        injected=injected,
        verdicts=final_verdicts,
        ordering=ordering,
    )


# -- 2. tool_routing -------------------------------------------------------- #


def _routing_answer(request, reads: _Reads, thresholds: ReadThresholds):
    candidates = {
        str(candidate.candidate_id): candidate for candidate in request.candidates
    }
    chosen_id = _require(reads.choice("routing.choice"), "routing.choice")
    confident = _require(reads.noul("routing.confident"), "routing.confident")
    risk_ok = _require(reads.noul("routing.risk_acceptable"), "routing.risk_acceptable")

    if chosen_id == "computer_use_fallback":
        return C.ToolRoutingAnswer(
            chosen_kind="computer_use_fallback",
            reason_code=REASON_COMPUTER_USE_FALLBACK,
        )
    if chosen_id == "no_route" or confident < thresholds.read_at:
        return C.ToolRoutingAnswer(
            chosen_kind="no_route",
            reason_code=REASON_BELOW_THRESHOLD if chosen_id != "no_route" else REASON_NO_ROUTE,
        )

    candidate = candidates[chosen_id]
    constraints = request.constraints
    if str(candidate.kind) not in tuple(constraints.allowed_kinds or ()):
        return C.ToolRoutingAnswer(
            chosen_kind="no_route", reason_code=REASON_KIND_NOT_ALLOWED
        )
    if _risk_index(candidate.risk_class) > _risk_index(constraints.max_risk_class):
        return C.ToolRoutingAnswer(
            chosen_kind="no_route", reason_code=REASON_RISK_NOT_ACCEPTABLE
        )
    if risk_ok < thresholds.read_at:
        return C.ToolRoutingAnswer(
            chosen_kind="no_route", reason_code=REASON_RISK_NOT_ACCEPTABLE
        )
    if str(candidate.health) == "unhealthy":
        return C.ToolRoutingAnswer(
            chosen_kind="no_route", reason_code=REASON_CANDIDATE_UNHEALTHY
        )

    missing = _missing_inputs(request, reads, candidate)
    approval = str(candidate.permission_state) == "needs_approval"
    if approval and not bool(constraints.allow_approval_required):
        return C.ToolRoutingAnswer(
            chosen_kind="no_route", reason_code="approval_not_allowed"
        )
    return C.ToolRoutingAnswer(
        chosen_kind="route",
        route=C.RouteChoice(
            candidate_id=chosen_id,
            kind=str(candidate.kind),
            score=float(confident),
            subtool_of=str(candidate.subtool_of),
            anchor_available=bool(candidate.anchor_available),
        ),
        requires_approval=approval,
        missing_inputs=missing,
        reason_code="route_selected",
    )


def _missing_inputs(request, reads: _Reads, candidate) -> tuple[str, ...]:
    """Which slots are missing: the caller's facts + the provider's per-slot choice."""
    missing: list[str] = [str(slot) for slot in tuple(candidate.missing_params or ())]
    for question_id in tuple(reads.evaluation.answers):
        prefix = "routing.missing_inputs."
        if not question_id.startswith(prefix):
            continue
        slot = question_id[len(prefix) :]
        if slot in tuple(candidate.required_params or ()):
            read = reads.choice(question_id)
            if read != "provided" and slot not in missing:
                missing.append(slot)
    return tuple(sorted(missing))


def _risk_index(risk_class: str) -> int:
    try:
        return int(str(risk_class).lstrip("R") or 0)
    except ValueError:  # pragma: no cover — the Literal already refuses it
        return 0


# -- 3. capability_ranking -------------------------------------------------- #


def _capability_answer(request, reads: _Reads, thresholds: ReadThresholds):
    entries: list[tuple[float, int, C.CapabilityRankingEntry]] = []
    for candidate in request.candidates:
        candidate_id = str(candidate.candidate_id)
        question_id = f"cap.rank.{candidate_id}.fit"
        score = _require(reads.score(question_id), question_id)
        legend = reads.legend(question_id)
        fit = _normalize(score, legend)
        covers = bool(
            (_require(reads.noul(f"cap.rank.{candidate_id}.covers_need"), "covers_need"))
            >= thresholds.read_at
        )
        reasons = _capability_reasons(candidate, covers)
        # Deterministic tie-break: covers_need first, then trusted provenance,
        # then health, then recency — never a generated ordering.
        tie = (
            int(covers)
            + int(str(candidate.authority) == "trusted")
            + int(str(candidate.health) in ("ok", "degraded"))
        )
        entries.append(
            (
                fit,
                tie,
                C.CapabilityRankingEntry(
                    candidate_id=candidate_id,
                    fit=fit,
                    rank=0,
                    covers_need=covers,
                    reasons=reasons,
                ),
            )
        )
    entries.sort(key=lambda item: (-item[0], -item[1], item[2].candidate_id))
    top_k = max(0, int(request.top_k))
    ranked = [
        entry
        for _, _, entry in entries[: top_k or len(entries)]
    ]
    ranked = [
        C.CapabilityRankingEntry(
            candidate_id=entry.candidate_id,
            fit=entry.fit,
            rank=index + 1,
            covers_need=entry.covers_need,
            reasons=entry.reasons,
        )
        for index, entry in enumerate(ranked)
    ]

    by_id = {
        str(candidate.candidate_id): candidate for candidate in request.candidates
    }
    best_fit: str | None = None
    if ranked:
        leader = ranked[0]
        candidate = by_id[leader.candidate_id]
        if (
            leader.fit >= float(request.min_fit)
            and leader.covers_need
            and bool(candidate.scope_match)
        ):
            best_fit = leader.candidate_id

    gap_read = _require(reads.noul("cap.rank.gap"), "cap.rank.gap")
    gap = bool(gap_read >= thresholds.read_at) or best_fit is None
    if best_fit is not None:
        gap_reason = ""
    elif not ranked:
        gap_reason = REASON_NO_FIT
    elif not ranked[0].covers_need:
        gap_reason = "leader_does_not_cover_need"
    elif not bool(by_id[ranked[0].candidate_id].scope_match):
        gap_reason = "leader_out_of_scope"
    else:
        gap_reason = REASON_NO_FIT
    return C.CapabilityRankingAnswer(
        ranking=tuple(ranked),
        best_fit=best_fit,
        gap=gap,
        gap_reason=gap_reason,
        widen_query=_echo_query(request) if best_fit is None else None,
    )


def _capability_reasons(candidate, covers: bool) -> tuple[str, ...]:
    tokens: list[str] = []
    if covers:
        tokens.append("covers_need")
    if bool(candidate.scope_match):
        tokens.append("scope_match")
    if str(candidate.health) == "ok":
        tokens.append("health_ok")
    if str(candidate.authority) == "trusted":
        tokens.append("authority_trusted")
    if int(candidate.last_used_days) <= 30:
        tokens.append("recently_used")
    return tuple(token for token in CAPABILITY_REASON_TOKENS if token in tokens)


def _normalize(score: float, legend: tuple[float, ...]) -> float:
    top = max((float(value) for value in legend), default=0.0)
    if top <= 0:
        return 0.0
    return max(0.0, min(1.0, float(score) / top))


def _echo_query(request) -> str | None:
    """The caller's own words as a widening hint — filtered to be inert."""
    need = request.need
    words = [str(word) for word in tuple(need.tags or ())] or [
        str(word) for word in tuple(need.required_input_types or ())
    ]
    safe = [
        word
        for word in words
        if word and not any(marker in word for marker in ("{", "}", "$(", ";", "<"))
    ]
    return " ".join(safe) or None


# -- 4. context_compaction -------------------------------------------------- #


def _compaction_answer(request, reads: _Reads, thresholds: ReadThresholds):
    options = request.options
    keep_at = float(options.keep_threshold)
    pinned_messages = set(int(index) for index in tuple(request.protected_message_ids or ()))
    history = tuple(request.state.history or ())
    newest = set(range(len(history) - int(options.preserve_recent_messages), len(history)))

    decisions: list[C.CompactionCallDecision] = []
    chars_before = 0
    chars_after = 0
    kept = results_dropped = calls_dropped = pinned = 0
    by_id = {str(call.id): call for entry in history for call in tuple(entry.tool_calls or ())}
    for call_id in compaction_call_ids(request):
        call = by_id[call_id]
        entry_index = _entry_index_of(history, call_id)
        keep_call = _require(
            reads.noul(f"compaction.call.{call_id}.keep"),
            f"compaction.call.{call_id}.keep",
        )
        keep_result = _require(
            reads.noul(f"compaction.result.{call_id}.keep_verbatim"),
            f"compaction.result.{call_id}.keep_verbatim",
        )
        result_chars = int(call.result_chars or 0)
        chars_before += result_chars
        is_pinned = (
            entry_index == 0
            or entry_index in pinned_messages
            or entry_index in newest
        )
        if is_pinned:
            action, reason, head = "keep", "pinned", result_chars
            pinned += 1
            kept += 1
        elif keep_result >= keep_at:
            action, reason, head = "keep", "kept", result_chars
            kept += 1
        elif keep_call >= keep_at:
            action, reason, head = (
                "drop_result",
                "result_dropped",
                min(int(options.truncate_head_chars), result_chars),
            )
            results_dropped += 1
        else:
            action, reason, head = "drop_call", "call_dropped", 0
            calls_dropped += 1
        chars_after += head
        decisions.append(
            C.CompactionCallDecision(
                id=call_id,
                tool=str(call.tool),
                keep_call=float(keep_call),
                keep_result=float(keep_result),
                action=action,
                reason=reason,
                head_chars_kept=head,
            )
        )

    ratio = 0.0 if chars_before == 0 else max(0.0, 1.0 - (chars_after / chars_before))
    stats = C.CompactionStats(
        calls=len(decisions),
        candidates=len(decisions),
        kept=kept,
        results_dropped=results_dropped,
        calls_dropped=calls_dropped,
        pinned=pinned,
        state_tokens=_estimate_tokens(str(request.state.context or "")),
        chars_before=chars_before,
        chars_after=chars_after,
        reduction_ratio=ratio,
    )
    return C.CompactionAnswer(
        calls=tuple(decisions),
        stats=stats,
        fitted_stage="request_state",
        requests=1,
    )


def _entry_index_of(history, call_id: str) -> int:
    for entry in history:
        for call in tuple(entry.tool_calls or ()):
            if str(call.id) == call_id:
                return int(entry.i)
    return -1


def _estimate_tokens(text: str) -> int:
    """A declared estimate (chars/4) for the stats payload only.

    The authoritative token accounting belongs to the compaction port; this
    number is labelled an estimate and is never used to decide anything.
    """
    return int((len(text) + 3) // CHARS_PER_TOKEN_FLOOR)


# -- 5. voice_triage -------------------------------------------------------- #


def _voice_answer(request, reads: _Reads, thresholds: ReadThresholds):
    floor = caution_floor_for(request)
    same_root = _choice_flag(reads, ".same_root_cause", "same")
    new_information = reads.noul_suffix(".new_information")
    claims_done = _choice_flag(reads, ".claims_done", "yes")
    evidence_current = reads.noul_suffix(".evidence_current")
    supports_goal = reads.noul_suffix(".supports_goal")
    system_two = reads.noul_suffix(".system_two_needed")
    evidence_refs = tuple(getattr(request, "evidence_refs", ()) or ())

    if floor >= HARD_CAUTION_FLOOR:
        secret = any("secret" in str(key).lower() or "credential" in str(key).lower()
                     for key in (request.signals or {}))
        return C.VoiceTriageAnswer(
            chosen="wake_voice",
            urgency=HARD_CAUTION_FLOOR,
            already_handled=False,
            on_sensible_path=bool(request.agent_on_sensible_path),
            reason_code=(
                RULE_UNTRUSTED_SECRET_REQUEST if secret else RULE_UNTRUSTED_AUTHORITY_ATTEMPT
            ),
            route_hint="pause_and_investigate",
            escalate_system_two=False,
        )
    if same_root and new_information is not None and new_information < thresholds.read_at:
        return C.VoiceTriageAnswer(
            chosen="wake_voice",
            urgency=3,
            already_handled=False,
            on_sensible_path=bool(request.agent_on_sensible_path),
            reason_code=RULE_LOOP_WITHOUT_PROGRESS,
            route_hint="pause_and_investigate",
            escalate_system_two=False,
        )
    if claims_done and evidence_current is not None and evidence_current < thresholds.read_at:
        return C.VoiceTriageAnswer(
            chosen="wake_voice",
            urgency=3,
            already_handled=False,
            on_sensible_path=bool(request.agent_on_sensible_path),
            reason_code=RULE_COMPLETION_CLAIM_WITHOUT_EVIDENCE,
            route_hint="request_verification",
            escalate_system_two=False,
        )
    if supports_goal is not None and supports_goal < thresholds.read_at:
        return C.VoiceTriageAnswer(
            chosen="wake_voice",
            urgency=2,
            already_handled=False,
            on_sensible_path=bool(request.agent_on_sensible_path),
            reason_code=RULE_DRIFT_GOAL_SUPPORT_MISSING,
            route_hint="hint",
            escalate_system_two=False,
        )
    if system_two is not None and system_two >= thresholds.read_at:
        return C.VoiceTriageAnswer(
            chosen="wake_voice",
            urgency=3,
            already_handled=False,
            on_sensible_path=bool(request.agent_on_sensible_path),
            reason_code=RULE_RECOVERY_SYSTEM_TWO,
            route_hint="escalate_system_two",
            escalate_system_two=True,
        )
    if bool(request.agent_on_sensible_path) or int(request.interruptions_so_far) >= 2:
        return C.VoiceTriageAnswer(
            chosen="defer",
            urgency=1,
            already_handled=False,
            on_sensible_path=True,
            reason_code=REASON_DEFER_SENSIBLE_PATH,
            route_hint="observe",
            escalate_system_two=False,
        )
    return C.VoiceTriageAnswer(
        chosen="stay_silent",
        urgency=0,
        already_handled=bool(evidence_refs),
        on_sensible_path=bool(request.agent_on_sensible_path),
        reason_code=REASON_NO_INTERVENTION,
        # 'observe' — nothing to do. The caller's own deterministic hint stays
        # the caller's: Jev's answer never echoes a pause hint next to a
        # 'stay_silent' reading, which would contradict itself.
        route_hint="observe",
        escalate_system_two=False,
    )


def _choice_flag(reads: _Reads, suffix: str, value: str) -> bool:
    read = reads.choice_suffix(suffix)
    return read == value


# -- 6. adversarial_test_planning ------------------------------------------- #


def _adversarial_answer(request, reads: _Reads, thresholds: ReadThresholds):
    budget = request.budget
    surfaces = {str(surface.surface_id): surface for surface in request.surfaces}
    surface_id = _require(reads.choice("adv.surface.pick"), "adv.surface.pick")
    intent_class = _require(
        reads.choice("adv.case.intent_class"), "adv.case.intent_class"
    )
    question_id = "adv.case.priority"
    score = _require(reads.score(question_id), question_id)
    priority = int(max(0.0, min(3.0, score)))
    covered = _require(
        reads.noul("adv.case.covered_already"), "adv.case.covered_already"
    )
    deterministic = _require(
        reads.noul("adv.oracle.deterministic"), "adv.oracle.deterministic"
    )

    if surface_id == "no_surface":
        return C.AdversarialPlanAnswer(
            chosen="refuse", stop_reason="no_surface_chosen"
        )

    remaining = int(budget.max_cases) - len(request.previous_results)
    if covered >= thresholds.read_at:
        return C.AdversarialPlanAnswer(
            chosen="refuse", stop_reason="case_already_covered"
        )
    surface = surfaces[surface_id]
    batch = max(1, min(remaining, int(budget.parallel_slots)))
    chosen = "narrow" if remaining <= 1 else "plan"
    if not deterministic >= thresholds.read_at:
        chosen = "narrow"
    if chosen == "narrow":
        batch = 1
    operation = SURFACE_OPERATION.get(str(surface.kind), "wait")
    oracle_kind, oracle_expect = SURFACE_ORACLE.get(
        str(surface.kind),
        ("observed_state", "the observed state matches the case's expectation"),
    )
    entry = str(surface.entry_hint or (surface.seed_operations or ("entry",))[0])
    target_ref = f"{surface_id}#{entry}"
    plan = tuple(
        C.TestCasePlan(
            case_id=f"adv-{index + 1:03d}",
            surface_id=surface_id,
            intent=f"exercise {surface_id} for {intent_class}",
            intent_class=intent_class,
            operation=operation,
            target_ref=target_ref,
            input_spec={"entry": "$goal"},
            success_oracle=C.Oracle(kind=oracle_kind, expect=oracle_expect),
            oracle_source=(
                "deterministic" if deterministic >= thresholds.read_at else "computer_use"
            ),
            abort_if=("stale_state", "permission_denied"),
            risk_class=str(surface.risk),
            priority=priority,
        )
        for index in range(batch)
    )
    return C.AdversarialPlanAnswer(
        plan=plan,
        chosen=chosen,
        stop_reason=REASON_NARROW_BUDGET if chosen == "narrow" else REASON_PLANNED,
        batch_size=len(plan),
        expected_defect_classes=(str(intent_class),),
    )


# -- 7. semantic_verification ------------------------------------------------- #


#: The three readings, least to most cautious. The final support is the *most
#: cautious* of the structural reading and the provider's own vote, so a
#: disagreement can only ever make Jev more careful, never less.
SUPPORT_SEVERITY: dict[str, int] = {
    "supported": 0,
    "underspecified": 1,
    "unsupported": 2,
}

#: Bound on how many gaps/reasons one reading may report (MVP/93 §2).
MAX_SEMANTIC_GAPS = 10


def _semantic_answer(request, reads: _Reads, thresholds: ReadThresholds):
    """Turn five readings of a brief into one advisory reading of the claim.

    The *structure* is deterministic and lives here, not in the provider: the
    provider contributes the five readings (and its own vote), and the mapping
    reading → reason → support is this function. That is what makes "the semantic
    gate refused, and here is why" a reproducible statement rather than a model's
    self-report — and it is why a disagreement can only tighten the reading.
    """
    matches = _require(
        reads.noul("sem.claim.matches_objective"), "sem.claim.matches_objective"
    )
    backed = _require(reads.noul("sem.claim.backed"), "sem.claim.backed")
    sufficient = _require(reads.noul("sem.checks.sufficient"), "sem.checks.sufficient")
    gap = _require(reads.noul("sem.gap.present"), "sem.gap.present")
    provider_choice = _require(
        reads.choice("sem.support.verdict"), "sem.support.verdict"
    )
    if provider_choice not in SUPPORT_SEVERITY:  # pragma: no cover — options are fixed
        raise SystemOneContractError(
            f"sem.support.verdict answered {provider_choice!r}, which is not one of "
            f"{list(SUPPORT_SEVERITY)}"
        )

    reasons: list[str] = []
    gaps: list[str] = []
    unverifiable: list[str] = []

    # -- what the packet itself can and cannot support ----------------------
    if not request.files:
        gaps.append("no changed file is recorded in the brief")
    if not any(command.exit_code == 0 for command in request.commands):
        gaps.append("no executed command with a zero exit code is recorded")
    if not request.evidence_refs:
        gaps.append("the run recorded no evidence refs for this claim")
    if request.brief_truncated:
        unverifiable.append(
            "the brief was shortened to fit its budget — the dropped part was not read"
        )
    for command in request.commands:
        if command.exit_code is None:
            unverifiable.append(
                f"the command {command.cmd[:120]!r} has no exit code in the brief, so "
                "its outcome is unknown"
            )
    for ref in request.evidence_refs:
        if str(ref).strip() == "":
            unverifiable.append("an evidence ref is blank")

    structural = "supported"
    if not thresholds.yes(float(matches)):
        structural = "unsupported"
        reasons.append(
            "the completion claim does not address the objective it is attached to"
        )
    elif not thresholds.yes(float(backed)):
        structural = "unsupported"
        reasons.append(
            "the evidence recorded in the brief does not support the completion claim"
        )
    elif thresholds.yes(float(gap)) or not thresholds.yes(float(sufficient)):
        structural = "underspecified"
        reasons.append(
            "the packet leaves part of the claim unsupported: "
            + (
                "an evidence gap was read"
                if thresholds.yes(float(gap))
                else "the executed checks do not cover the objective"
            )
        )

    final = max((structural, str(provider_choice)), key=lambda value: SUPPORT_SEVERITY[value])
    if final != structural and final != "supported":
        reasons.append(
            f"the verifier read the claim as {final!r} from the same packet"
        )
    if final == "unsupported" and not reasons:  # pragma: no cover — defensive
        reasons.append("the packet does not support the completion claim")
    if final != "supported":
        gaps = (gaps or ["the packet names no specific gap the reader could close"])[
            :MAX_SEMANTIC_GAPS
        ]
    if final == "supported":
        reasons = []
        gaps = []
        unverifiable = [u for u in unverifiable if "no exit code" not in u]

    checks_run = (
        f"objective_claim_alignment(matches={bool(thresholds.yes(float(matches)))})",
        f"evidence_backing(backed={bool(thresholds.yes(float(backed)))})",
        f"coverage_sufficiency(sufficient={bool(thresholds.yes(float(sufficient)))})",
        f"gap_scan(gap_present={bool(thresholds.yes(float(gap)))})",
        f"packet_read(files={len(request.files)}, commands={len(request.commands)}, "
        f"refs={len(request.evidence_refs)}, tail_lines={len(request.transcript_tail)})",
    )

    return C.SemanticVerificationAnswer(
        support=final,
        reasons=tuple(reasons[:MAX_SEMANTIC_GAPS]),
        evidence_gaps=tuple(gaps[:MAX_SEMANTIC_GAPS]),
        checks_run=tuple(checks_run),
        unverifiable=tuple(unverifiable[:MAX_SEMANTIC_GAPS]),
        provider_choice=str(provider_choice),
    )


# --------------------------------------------------------------------------- #
# assembling the decision                                                       #
# --------------------------------------------------------------------------- #


def _caution_level(request, answer) -> int:
    """``max(request floor, ...)`` — caution is raised by rules, never lowered."""
    level = caution_floor_for(request)
    if str(request.kind) == "voice_triage" and str(getattr(answer, "chosen", "")) == "wake_voice":
        level = max(level, int(getattr(answer, "urgency", 0)))
    if str(request.kind) == "tool_routing":
        route = getattr(answer, "route", None)
        if route is not None:
            candidate = next(
                (
                    item
                    for item in request.candidates
                    if str(item.candidate_id) == str(route.candidate_id)
                ),
                None,
            )
            if candidate is not None and str(candidate.permission_state) == "needs_approval":
                level = max(level, 1)
    if str(request.kind) == "semantic_verification":
        # A reading that the claim is not backed is a reason to be more careful,
        # never less: caution is raised here and can never be lowered.
        if str(getattr(answer, "support", "")) == "unsupported":
            level = max(level, 1)
    return int(min(level, HARD_CAUTION_FLOOR))


def _pack_identity(kind: str) -> tuple[str, str]:
    """The ``(id, version)`` of the pack this kind routes through.

    Used by the decisions that never reached a provider (refused/blocked, and the
    deterministic short-circuit) so their envelope names the same pack a provider
    decision would — with the registry as the source, and an honest fallback to
    the ref only if the pack cannot be resolved.
    """
    ref = pack_ref_for(kind)
    try:
        pack = pack_by_ref(ref)
    except Exception:  # noqa: BLE001 — an unresolvable pack must not break the caller
        return ref, ""
    return pack.id, pack.version


def _blank_answer(request) -> Any:
    """The neutral answer of a refused/blocked decision — nothing was chosen."""
    kind = str(request.kind)
    if kind == "memory_injection":
        return C.MemoryInjectionAnswer(chosen="none")
    if kind == "tool_routing":
        return C.ToolRoutingAnswer(chosen_kind="no_route", reason_code="no_advisory")
    if kind == "capability_ranking":
        return C.CapabilityRankingAnswer(best_fit=None, gap=False)
    if kind == "context_compaction":
        return C.CompactionAnswer()
    if kind == "voice_triage":
        # 'defer' — recorded, no interruption. Never 'stay_silent': a refused
        # decision must not read as Jev having silenced a trigger (R3).
        return C.VoiceTriageAnswer(chosen="defer", route_hint="observe")
    if kind == "adversarial_test_planning":
        return C.AdversarialPlanAnswer(chosen="refuse", stop_reason="no_advisory")
    if kind == "semantic_verification":
        # 'underspecified' — never a silent 'supported'. A refused or blocked gate
        # must not read as agreement, and it must not read as a goal refusal
        # either: it says only "nobody read the claim against the brief".
        return C.SemanticVerificationAnswer(
            support="underspecified",
            reasons=("no advisory was obtained for this brief",),
        )
    raise SystemOneContractError(f"no blank answer for kind {kind!r}")  # pragma: no cover


def _blank_decision(
    request, *, status: str, reason: str, fallback: str, attempted: bool = False
) -> C.JevDecision:
    """A decision that carries no advisory — and says whether a call went out.

    ``attempted`` is the honesty split audit 63/V2 asked for: ``provider_mode`` stays
    the §4.2 label (``disabled`` for every decision that produced no evaluation), while
    ``attempts`` records whether a real outbound provider call happened *before* the
    decision was blocked. A refused preflight, a disabled flag or an unconfigured
    provider spend no call (``attempts=0``); a provider that answered 402 (or failed
    any other way the gate recorded) really was called (``attempts=1``) and must never
    be reported as "never attempted".
    """
    pack_id, pack_version = _pack_identity(str(request.kind))
    return _build_decision(
        request,
        answer=_blank_answer(request),
        status=status,
        provider_mode="disabled",
        live_jev=False,
        refusal_reason=reason,
        fallback=fallback,
        question_pack_id=pack_id,
        question_pack_version=pack_version,
        confidence=0.0,
        caution_level=caution_floor_for(request),
        latency_ms=0,
        attempts=1 if attempted else 0,
        usage={},
        evidence_refs=tuple(getattr(request, "evidence_refs", ()) or ()),
    )


def attempted_outbound(service: SystemOneService | None) -> bool:
    """Did a real outbound provider call happen on this service? (audit 63/V2)

    Two records say yes, and both are the substrate's own: the gate wrote an error
    for the attempt (the measured case: HTTP 402 Insufficient Balance), or the
    evaluator completed an evaluation that was then discarded (stale labelling).
    Nothing else is read as an attempt — a disabled flag, an unconfigured provider
    or a preflight refusal leaves both counters untouched.
    """
    if service is None:
        return False
    if list(getattr(getattr(service, "gate", None), "errors", ()) or ()):
        return True
    return int(getattr(service, "evaluations", 0) or 0) > 0


def provider_attempted(document: Mapping[str, Any]) -> bool:
    """Whether a decision document's provider call really went out (audit 63/V2).

    The inverse of the label trap: ``provider_mode == "disabled"`` only says no
    evaluation was produced, not that nobody called. ``attempts`` is the count
    :func:`decide_async` recorded on the decision itself (1 once the gate saw a
    call, 0 when the request was refused/blocked before any call), with one
    defensive rule for an ``ok`` decision from a live provider mode.
    """
    try:
        if int(document.get("attempts") or 0) > 0:
            return True
    except (TypeError, ValueError):  # pragma: no cover — a malformed record
        return False
    return (
        str(document.get("status") or "") == "ok"
        and str(document.get("provider_mode") or "") not in ("", "disabled")
    )


def _build_decision(
    request,
    *,
    answer,
    status: str,
    provider_mode: str,
    live_jev: bool,
    refusal_reason: str,
    fallback: str,
    question_pack_id: str,
    question_pack_version: str,
    confidence: float,
    caution_level: int,
    latency_ms: int,
    attempts: int,
    usage: dict,
    evidence_refs: tuple[str, ...],
) -> C.JevDecision:
    decision = C.JevDecision(
        decision_id=C.new_decision_id(str(request.kind)),
        kind=request.kind,
        run_id=request.run_id,
        goal_id=request.goal_id,
        goal_revision=int(request.goal_revision),
        status=status,
        provider_mode=provider_mode,
        live_jev=bool(live_jev),
        question_pack_id=question_pack_id,
        question_pack_version=question_pack_version,
        confidence=float(confidence),
        answer=answer,
        refusal_reason=refusal_reason,
        fallback=fallback,
        caution_level=int(caution_level),
        latency_ms=int(latency_ms),
        attempts=int(attempts),
        usage=dict(usage or {}),
        evidence_refs=tuple(evidence_refs),
        created_at=time.time(),
    )
    # the request-aware pass: echo, monotone caution, per-kind structural rules
    return assert_decision_authority(decision, request=request)


def _finish(
    decision: C.JevDecision,
    request,
    *,
    service: SystemOneService | None,
    emit: Any | None,
) -> C.JevDecision:
    payload = {
        "decision_id": decision.decision_id,
        "kind": decision.kind,
        "status": decision.status,
        "provider_mode": decision.provider_mode,
        "live_jev": decision.live_jev,
        "confidence": decision.confidence,
        "refusal_reason": decision.refusal_reason,
        "fallback": decision.fallback,
        "caution_level": decision.caution_level,
        "latency_ms": decision.latency_ms,
        "run_id": decision.run_id,
        "goal_id": decision.goal_id,
        "goal_revision": decision.goal_revision,
        "decision": decision.model_dump(mode="json"),
    }
    if service is not None:
        service.emit(DECISION_EVENT, payload)
    elif emit is not None:
        emit(DECISION_EVENT, payload)
    return decision


__all__ = [
    "CAPABILITY_REASON_TOKENS",
    "DECISION_EVENT",
    "REASON_BELOW_THRESHOLD",
    "REASON_PERMISSION_DENIED_ONLY",
    "SURFACE_OPERATION",
    "SURFACE_ORACLE",
    "decide",
    "decide_async",
    "decide_sync",
]
