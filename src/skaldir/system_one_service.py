"""SystemOneService — the ONE facade the business logic is allowed to see (§33).

Business logic never talks to a provider. The only permitted edges are:

    Voice       -> SystemOneService -> SystemOneEvaluator
    Completion  -> SystemOneService -> SystemOneEvaluator
    ComputerUse -> SystemOneService -> SystemOneEvaluator

``skaldir.voice`` and ``skaldir.completion`` import this module and nowhere else
provider-shaped; the forbidden edges of §3 (``voice.py -> TypeSafeClient``,
``completion.py -> HTTP POST``) are proven absent by
``tests/test_jev_service_facade.py`` twice — an AST import scan and a subprocess
that makes every provider/HTTP module unimportable and then imports both
business modules.

The facade owns exactly the concerns that must not be re-implemented per
caller, and owns nothing else:

* **sanitization** — ``evaluate_snapshot`` runs the §40 pipeline and stamps the
  §7 state identity; the provider only ever sees the sanitized payload;
* **the provider edge** — one evaluator, behind the internal contract, with the
  §43/§92 gate (error classification, deadline, staleness refusal);
* **honesty labelling** — every result carries its §4.2 ``provider_mode``; an
  LLM-adapter result can never read ``typesafe_jev_live``;
* **dedup** (§23) and **metrics** (§26) — an evaluation already computed for
  this exact version of the world is not recomputed, and what was measured is
  reported as measured (a metric nobody measured stays ``not_run``);
* **runtime events** (§24) — ``system_one.requested``/``completed``/``failed``/
  ``discarded_stale``/``escalated``, through the payload guard, so no secret and
  no complete private state is persisted.

It is deliberately **not** a second runtime, policy engine, goal store or
orchestrator: the runtime gates, ``SystemOneGate``/``propose_route``
(:mod:`skaldir.system_one_policy`) and ``GateRunner`` (:mod:`skaldir.completion`)
keep every authority they had (§0/§34).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .events import SYSTEM_ONE_EVENT_KINDS, system_one_event_payload
from .system_one import (
    ACCESS_DISABLED,
    PROVIDER_DISABLED,
    EvaluationContext,
    SystemOneError,
    SystemOneEvaluation,
    SystemOneState,
    provider_mode_for,
)
from .system_one_cache import CacheKey, SystemOneCache, cache_key_for
from .system_one_metrics import (
    DECISION_POLICY_MS,
    PROVIDER_LATENCY_MS,
    SANITIZATION_MS,
    STATE_BUILD_MS,
    TOTAL_SYSTEM_ONE_MS,
    SystemOneMetrics,
)
from .system_one_policy import SystemOneGate, VersionStamp, is_stale, propose_route

Emit = Callable[[str, dict], None]

#: The five §24 lifecycle kinds this facade emits.
LIFECYCLE_REQUESTED = "system_one.requested"
LIFECYCLE_COMPLETED = "system_one.completed"
LIFECYCLE_FAILED = "system_one.failed"
LIFECYCLE_DISCARDED_STALE = "system_one.discarded_stale"
LIFECYCLE_ESCALATED = "system_one.escalated"

LIFECYCLE_KINDS: tuple[str, ...] = (
    LIFECYCLE_REQUESTED,
    LIFECYCLE_COMPLETED,
    LIFECYCLE_FAILED,
    LIFECYCLE_DISCARDED_STALE,
    LIFECYCLE_ESCALATED,
)

#: Routes that are a request for System Two / the user — never an action.
ESCALATION_ROUTES: tuple[str, ...] = (
    "escalate_system_two",
    "request_user_input",
    "request_verification",
    "pause_and_investigate",
)


class SystemOneService:
    """The provider edge + the measured, evented envelope around one evaluator."""

    def __init__(
        self,
        *,
        evaluator,
        flags=None,
        registry=None,
        sanitizer=None,
        cache: SystemOneCache | None = None,
        metrics: SystemOneMetrics | None = None,
        emit: Emit | None = None,
        threshold_profile_id: str = "",
    ) -> None:
        from .system_one_questions import default_question_registry
        from .system_one_sanitizer import StateSanitizer

        self.evaluator = evaluator
        self.flags = flags
        self.registry = registry if registry is not None else default_question_registry()
        self.sanitizer = sanitizer if sanitizer is not None else StateSanitizer()
        self.cache = cache
        self.metrics = metrics if metrics is not None else SystemOneMetrics()
        self._emit_handler = emit or (lambda kind, payload: None)
        self.threshold_profile_id = str(threshold_profile_id or "")
        self.gate = SystemOneGate(evaluator, flags=flags, emit=self.emit)
        self.evaluations = 0

    # -- flags / identity --------------------------------------------------- #

    @property
    def enabled(self) -> bool:
        """Master flag: absent flags mean "the caller decided already"."""
        if self.flags is None:
            return True
        return bool(getattr(self.flags, "enabled", False))

    @property
    def provider_name(self) -> str:
        return str(getattr(self.evaluator, "provider", "") or "")

    @property
    def provider_model(self) -> str:
        return str(getattr(self.evaluator, "provider_model", "") or "")

    @property
    def provider_mode(self) -> str:
        """The §4.2 labelling this evaluator can produce at best."""
        return provider_mode_for(
            access_mode=str(getattr(self.evaluator, "provider_access_mode", ACCESS_DISABLED)),
            live_jev=bool(getattr(self.evaluator, "live_jev", False)),
        )

    # -- events (§24) ------------------------------------------------------- #

    def emit(self, kind: str, payload: dict | None = None) -> None:
        """Emit a System-One event through the §24 payload guard."""
        data = dict(payload or {})
        if kind in SYSTEM_ONE_EVENT_KINDS:
            data = system_one_event_payload(kind, data)
        try:
            self._emit_handler(kind, data)
        except Exception:  # noqa: BLE001 — an emitter must never break a run
            pass

    # -- the provider edge -------------------------------------------------- #

    async def advise(
        self,
        *,
        state: SystemOneState,
        questions,
        context: EvaluationContext,
        live: VersionStamp | None = None,
        use_cache: bool = True,
    ) -> SystemOneEvaluation | None:
        """Run one evaluation, or refuse/return ``None`` — never guess.

        ``None`` means "no advisory; keep your own path": deactivated, provider
        failure, blocked access, or a stale result. Every one of those cases is
        reported as its own §24 event so it is visible without reading the record.
        """
        if not self.enabled:
            return None

        key = self._cache_key(state=state, context=context)
        if use_cache and self.cache is not None and key is not None:
            cached = self.cache.get(key)
            if cached is not None:
                self._emit_completed(cached, cached=True)
                return cached

        started = time.time()
        self.emit(
            LIFECYCLE_REQUESTED,
            {
                "run_id": context.run_id,
                "goal_id": context.goal_id,
                "goal_revision": int(context.goal_revision),
                "policy_revision": int(context.policy_revision),
                "caller": str(context.caller or ""),
                "question_pack_id": str(getattr(questions, "id", "")),
                "question_pack_version": str(getattr(questions, "version", "")),
                "state_fingerprint": state.fingerprint,
                "provider": self.provider_name,
                "provider_model": self.provider_model,
                "provider_mode": self.provider_mode,
                "live_jev": False,
            },
        )
        evaluation = await self.gate.advise(state=state, questions=questions, context=context)
        total_ms = (time.time() - started) * 1000.0
        self.metrics.record_timing(TOTAL_SYSTEM_ONE_MS, total_ms, source="service")
        if evaluation is None:
            self._emit_failed(context=context, questions=questions)
            return None

        self.evaluations += 1
        self.metrics.record_timing(
            PROVIDER_LATENCY_MS, float(evaluation.latency_ms), source="service"
        )
        self.metrics.record_usage(dict(evaluation.usage or {}), source="service")

        if live is not None and is_stale(evaluation, live=live):
            self.emit(
                LIFECYCLE_DISCARDED_STALE,
                self._evidence_payload(evaluation, extra={"reason": "stale_against_live_stamp"}),
            )
            return None

        if use_cache and self.cache is not None and key is not None:
            self.cache.put(key, evaluation)
        self._emit_completed(evaluation, cached=False)
        return evaluation

    async def evaluate_snapshot(
        self,
        *,
        snapshot: dict,
        questions,
        context: EvaluationContext,
        scope: str = "task",
        project_id: str | None = None,
        keys: tuple[str, ...] | None = None,
        source_event_refs: tuple[str, ...] = (),
        live: VersionStamp | None = None,
    ) -> SystemOneEvaluation | None:
        """Sanitize a runtime snapshot, then evaluate it (§40/§7/§33).

        The sanitizer timing is measured here because this is the only place
        the sanitizer runs for a System-One call.
        """
        started = time.time()
        state = self.sanitizer.sanitize(
            snapshot,
            scope=scope,
            project_id=project_id,
            keys=keys,
            workspace_fingerprint=context.workspace_fingerprint,
            source_event_refs=source_event_refs,
        )
        elapsed = (time.time() - started) * 1000.0
        self.metrics.record_timing(STATE_BUILD_MS, elapsed, source="service")
        self.metrics.record_timing(SANITIZATION_MS, elapsed, source="service")
        return await self.advise(state=state, questions=questions, context=context, live=live)

    # -- Voice ------------------------------------------------------------- #

    async def voice_triage(
        self,
        *,
        voice,
        signals,
        step: int,
        evidence: list[str] | None = None,
        flags=None,
        state: SystemOneState | None = None,
        questions=None,
        context: EvaluationContext | None = None,
        live: VersionStamp | None = None,
    ):
        """Voice triage through the facade: deterministic hint first, reading second.

        The deterministic :meth:`skaldir.voice.Voice.consider` result is always
        computed first and always returned unchanged — the facade only decides
        whether a reading is attached, and records the §32/§47 route the reading
        *proposes* (§49). A proposal is never an action: an escalation is
        reported as ``system_one.escalated`` with ``request_only`` semantics.
        """
        from .voice import triage_with_system_one

        active = flags if flags is not None else self.flags
        if active is None or not getattr(active, "enabled", False):
            return triage_with_system_one(
                voice=voice, signals=signals, step=step, evidence=evidence, flags=active
            )
        if str(getattr(active, "provider", PROVIDER_DISABLED)) == PROVIDER_DISABLED:
            return triage_with_system_one(
                voice=voice, signals=signals, step=step, evidence=evidence, flags=active
            )
        wants_reading = bool(
            getattr(active, "voice_triage", False) or getattr(active, "shadow_mode", False)
        )
        if not wants_reading:
            return triage_with_system_one(
                voice=voice, signals=signals, step=step, evidence=evidence, flags=active
            )
        if state is None or questions is None or context is None:
            return triage_with_system_one(
                voice=voice, signals=signals, step=step, evidence=evidence, flags=active
            )

        evaluation = await self.advise(
            state=state, questions=questions, context=context, live=live
        )

        if evaluation is None:
            # no reading: the legacy path applies, and the deterministic hint
            # is exactly what the caller would have produced anyway
            return triage_with_system_one(
                voice=voice, signals=signals, step=step, evidence=evidence, flags=active
            )

        outcome = triage_with_system_one(
            voice=voice,
            signals=signals,
            step=step,
            evidence=evidence,
            flags=active,
            evaluation=evaluation,
            service=self,
        )
        if outcome.advisory is None:
            return outcome

        policy_started = time.time()
        proposed = propose_route(evaluation)
        outcome.advisory["route_proposal"] = proposed.route
        outcome.advisory["rule"] = proposed.rule
        outcome.advisory["basis"] = list(proposed.basis)
        outcome.advisory["provider_mode"] = evaluation.provider_mode
        outcome.advisory["via"] = "system_one_service"
        self.metrics.record_timing(
            DECISION_POLICY_MS, (time.time() - policy_started) * 1000.0, source="service"
        )

        if proposed.route in ESCALATION_ROUTES:
            self.emit(
                LIFECYCLE_ESCALATED,
                self._evidence_payload(
                    evaluation,
                    extra={
                        "route": proposed.route,
                        "rule": proposed.rule,
                        # a request for System Two — never an executed action
                        "request_only": True,
                    },
                ),
            )
        else:
            # the reading agreed with the deterministic triage: a System-2 call
            # was avoided, and that is measured, not claimed (§20/§26)
            self.metrics.note_system2_call_saved(source="service")
        return outcome

    # -- internals ---------------------------------------------------------- #

    def _cache_key(
        self, *, state: SystemOneState, context: EvaluationContext
    ) -> CacheKey | None:
        if self.cache is None:
            return None
        return cache_key_for(
            state=state,
            context=context,
            provider_model=self.provider_model,
            threshold_profile=self.threshold_profile_id,
            epochs={
                "evidence": int(getattr(self.cache.live, "evidence_epoch", 0)),
                "gui_state": int(getattr(self.cache.live, "gui_state_epoch", 0)),
                "tool": int(getattr(self.cache.live, "tool_epoch", 0)),
            },
        )

    def _evidence_payload(
        self, evaluation: SystemOneEvaluation, *, extra: dict | None = None
    ) -> dict:
        payload = evaluation.evidence()
        if extra:
            payload.update(dict(extra))
        return payload

    def _emit_completed(self, evaluation: SystemOneEvaluation, *, cached: bool) -> None:
        payload = self._evidence_payload(evaluation)
        payload["cache"] = "hit" if cached else "miss"
        self.emit(LIFECYCLE_COMPLETED, payload)

    def _emit_failed(self, *, context: EvaluationContext, questions) -> None:
        record: Any = self.gate.errors[-1] if self.gate.errors else {}
        reason = "provider_unavailable"
        if isinstance(record, dict):
            reason = str(record.get("reason") or record.get("state") or reason)
        self.emit(
            LIFECYCLE_FAILED,
            {
                "run_id": context.run_id,
                "goal_id": context.goal_id,
                "caller": str(context.caller or ""),
                "question_pack_id": str(getattr(questions, "id", "")),
                "provider": self.provider_name,
                "provider_model": self.provider_model,
                "provider_mode": self.provider_mode,
                "reason": reason,
                "error": str(record.get("error", "")) if isinstance(record, dict) else "",
                "status": record.get("status") if isinstance(record, dict) else None,
            },
        )


def build_service(
    *,
    flags,
    llm=None,
    credential_resolver=None,
    transport=None,
    model: str = "",
    emit: Emit | None = None,
    metrics: SystemOneMetrics | None = None,
    cache: SystemOneCache | None = None,
    threshold_profile_id: str = "",
) -> SystemOneService:
    """Build the service the flags ask for — degrading to the disabled evaluator.

    One construction path for the whole runtime: the flags decide the evaluator
    (:func:`skaldir.system_one_flags.build_evaluator`), and the service wraps it.
    Deactivation always lands on the existing path (§98/JEV-20).
    """
    from .system_one_flags import build_evaluator

    evaluator = build_evaluator(
        flags,
        llm=llm,
        credential_resolver=credential_resolver,
        transport=transport,
        model=model,
        emit=emit,
    )
    return SystemOneService(
        evaluator=evaluator,
        flags=flags,
        emit=emit,
        metrics=metrics,
        cache=cache,
        threshold_profile_id=threshold_profile_id,
    )


__all__ = [
    "ESCALATION_ROUTES",
    "LIFECYCLE_COMPLETED",
    "LIFECYCLE_DISCARDED_STALE",
    "LIFECYCLE_ESCALATED",
    "LIFECYCLE_FAILED",
    "LIFECYCLE_KINDS",
    "LIFECYCLE_REQUESTED",
    "SystemOneError",
    "SystemOneService",
    "build_service",
]
