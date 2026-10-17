"""Subscribe the Jev decision layer to the runtime's **own** event timeline (MVP/87).

What was wrong before this module existed (measured in ``MVP/78-jev-live.md`` §2):

* ``SystemOneService`` emits ``system_one.requested``/``completed``/``failed``/
  ``...escalated`` and ``decide()`` emits ``jev_decision`` on **every** decision — but the
  service's default handler is ``emit or (lambda kind, payload: None)`` and **no product
  caller passed an emit handler**. The decisions were produced, printed and (for the CLI
  verb) ledgered; they never reached the runtime's ``events.db``: 3 231 events, 27 kinds,
  **zero** ``system_one.*``/``jev_*`` kinds.
* So the layer was *always reachable* and *attached per call site*, i.e. opt-in at every
  caller. This module is the missing subscription: one emitter object that writes the
  decision layer's own events into the runtime's store, one arming helper that builds the
  configured service around it, and one turn layer the agent loop attaches to.

The honest ceiling, in one place — **what "always-armed" means after this module**:

* **Armed (subscribed):** every decision the façade produces through a caller that has the
  subscription attached — the agent loop's turn boundaries / tool routing
  (:class:`TurnDecisions`), voice triage (:mod:`skaldir.voice_service`), the adversarial
  planner (:mod:`skaldir.adversarial`), the ``skaldir jev`` CLI verb, the ROUTE probe.
  Each of those now lands one ``jev_decision`` row (decision id, kind, status,
  ``provider_mode``, ``live_jev``, confidence, fallback, latency) in the runtime store,
  next to the ``system_one.*`` lifecycle rows of the same request.
* **Not armed:** a call site that builds its own ``SystemOneService``/``decide()`` without
  asking :func:`armed_service` (:func:`bus_for`) keeps emitting to ``None`` exactly as
  before — the subscription is not injected by monkeypatching, it is passed.
* **``live_jev`` stays false.** ``live_jev: true`` is reserved for an *authorized TypeSafe
  Jev call* (``system_one.py``: ``typesafe_jev_live``). No TypeSafe credential exists on
  this machine: :func:`typesafe_status` therefore reports
  ``available: false`` with the named reason, and this module **never** writes
  ``live_jev: true``. What a TypeSafe credential would additionally buy is stated there —
  and nothing here fabricates it.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..events import EventStore
from ..registry import default_runtime_dir

#: The one event kind this module writes for a decision. It is registered in
#: :mod:`skaldir.events` (``KINDS``) so the store accepts it, and it is deliberately **not**
#: in ``CHAT_KINDS``: Jev stays inconspicuous in the chat feed (§10).
DECISION_EVENT = "jev_decision"

#: Bound for one string field of a decision payload (a model answer is not a state dump).
MAX_FIELD_CHARS = 4_000
#: Bound for the nested ``answer`` object.
MAX_ANSWER_CHARS = 20_000

#: Which fields of a decision document are persisted. The document is the same one the CLI
#: prints and the ledger stores, so stdout, ledger and event store can never disagree.
DECISION_KEYS: tuple[str, ...] = (
    "decision_id",
    "kind",
    "status",
    "authority",
    "certifies",
    "provider_mode",
    "live_jev",
    "confidence",
    "caution_level",
    "refusal_reason",
    "fallback",
    "latency_ms",
    "attempts",
    "usage",
    "question_pack_id",
    "question_pack_version",
    "run_id",
    "goal_id",
    "goal_revision",
    "evidence_refs",
    "created_at",
)

#: Which runtime paths this wiring attaches — printed by :func:`wiring_status`, so the
#: statement in the record is generated from the same table the code uses.
ARMED_PATHS: tuple[str, ...] = (
    "agent_loop.turn_boundary / tool_routing (skaldir.agent_loop.AgentLoop.decisions)",
    "voice_triage (skaldir.voice_service.triage/_jev_advisory)",
    "adversarial_test_planning (skaldir.adversarial.plan_suite --planner jev)",
    "context_compaction (skaldir.context_manager, --asker provider)",
    "the six typed decisions on demand (skaldir jev decide)",
    "ROUTE layer verdict (skaldir selftest --layer ROUTE)",
)

#: Which runtime paths stay **unarmed**, and why — the honest half of the table.
UNARMED_PATHS: tuple[tuple[str, str], ...] = (
    (
        "memory_injection / capability_ranking inside a run",
        "no live caller: only the on-demand verb asks for them",
    ),
    (
        "completion preflight / ACT / recovery / computer_use readings",
        "gated off by their own flags (system_one.goal_completion / computer_use / recovery)",
    ),
)


def runtime_dir(runtime: str | Path | None = None) -> Path:
    """The runtime whose ``events.db`` the subscription writes into."""
    if runtime is not None and str(runtime).strip():
        return Path(runtime).expanduser()
    return default_runtime_dir()


def pinned_provider_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment **without** the provider-config overrides, credential names kept.

    Why: a decision that is written into a runtime's event store must be answered by the
    provider that runtime's own ``config.yaml`` names. An inherited ``SKALDIR_LLM_BASE_URL`` /
    ``SKALDIR_LLM_MODEL`` (a test stub, another agent's proof endpoint) must not become the
    endpoint of a decision that lands in the operator's timeline — measured: without this pin,
    one test invocation of ``skaldir adversarial plan`` wrote four ``blocked`` decisions with
    ``refusal_reason='provider returned an empty completion'`` (a stub endpoint) into
    ``~/.hermes/godsv/events.db``.

    The credential variables are deliberately **kept**: that is how the product resolves a key
    (``api_key_env`` in the config file, or the documented ``SKALDIR_LLM_API_KEY`` fallbacks).
    """
    from ..llm_provider import ENV_CONTRACT

    overrides = {
        name
        for key, names in ENV_CONTRACT.items()
        if key != "api_key"
        for name in names
    }
    source = os.environ if env is None else dict(env)
    return {str(key): str(value) for key, value in source.items() if key not in overrides}


# --------------------------------------------------------------------------- #
# payload shaping                                                            #
# --------------------------------------------------------------------------- #


def _bounded(value: Any, limit: int) -> Any:
    """JSON-safe, size-bounded copy — an event payload may never break the store."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"…(+{len(value) - limit})"
    if isinstance(value, Mapping):
        return {str(k): _bounded(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bounded(item, limit) for item in value]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return _bounded(str(value), limit)


def decision_payload(
    document: Mapping[str, Any],
    *,
    source: str = "",
    step: int = 0,
    extra: Mapping[str, Any] | None = None,
) -> dict:
    """The §24-safe payload for one ``jev_decision`` row.

    Reads the decision document (``JevDecision.model_dump(mode="json")``), keeps the
    contract fields of :data:`DECISION_KEYS`, flattens the answer to its typed core
    (``chosen``/``route``/``reason_code``/``plan_size``) and never copies a raw state, a
    prompt or a credential — the decision document carries none of those by construction.
    """
    data = dict(document or {})
    answer = data.pop("answer", None) or {}
    payload: dict[str, Any] = {}
    for key in DECISION_KEYS:
        if key in data and data[key] not in (None, "", (), [], {}):
            payload[key] = _bounded(data[key], MAX_FIELD_CHARS)
    payload["source"] = str(source or "")
    payload["step"] = int(step or 0)
    if isinstance(answer, Mapping):
        payload["answer_kind"] = str(answer.get("kind") or "")
        for key in (
            "chosen",
            "reason_code",
            "route_hint",
            "stop_reason",
            "requires_approval",
            "agrees",
        ):
            if answer.get(key) not in (None, "", (), [], {}):
                payload[key] = _bounded(answer[key], MAX_FIELD_CHARS)
        route = answer.get("route")
        if isinstance(route, Mapping):
            payload["route_candidate_id"] = str(route.get("candidate_id") or "")
        plan = answer.get("plan")
        if isinstance(plan, (list, tuple)):
            payload["plan_size"] = len(plan)
        payload["answer"] = _bounded(answer, MAX_ANSWER_CHARS)
    if extra:
        for key, value in dict(extra).items():
            payload[str(key)] = _bounded(value, MAX_FIELD_CHARS)
    return payload


# --------------------------------------------------------------------------- #
# the bus                                                                    #
# --------------------------------------------------------------------------- #


class DecisionBus:
    """The ``emit`` handler the decision layer was missing — never breaks a run.

    One instance is one open :class:`~skaldir.events.EventStore` plus the session/step a
    row is filed under. ``emit`` matches the signature ``SystemOneService`` and
    ``skaldir.jev.decide`` call (``emit(kind, payload)``), so it drops straight into
    ``build_service(..., emit=bus.emit)`` or ``decide(..., emit=bus.emit)``.

    Failure policy, deliberately the same as ``SystemOneService.emit``: a store that
    cannot be written is *counted* (``dropped``, ``last_error``) and the run continues.
    A payload that really carries a secret is written **redacted** (``append_redacted``),
    never silently — the leak marker ``_godsv_redacted`` stays in the row.
    """

    def __init__(
        self,
        store: EventStore,
        *,
        runtime: str | Path = "",
        session_id: str = "jev",
        step: int = 0,
        source: str = "",
    ) -> None:
        self.store = store
        self.path = str(getattr(store, "path", "") or "")
        self.runtime = str(runtime or "")
        self.session_id = str(session_id or "jev")
        self.step = int(step or 0)
        self.source = str(source or "")
        self.written = 0
        self.dropped = 0
        self.redacted = 0
        self.duplicates = 0
        self.kinds: dict[str, int] = {}
        self.last_error = ""
        self._seen: set[tuple[str, int, str, str]] = set()

    # -- subscription lifecycle ----------------------------------------------- #

    def bind(
        self,
        *,
        session_id: str | None = None,
        step: int | None = None,
        source: str | None = None,
    ) -> DecisionBus:
        """Re-file the following rows (the loop binds a new session/step per turn)."""
        if session_id is not None:
            self.session_id = str(session_id or "jev")
        if step is not None:
            self.step = int(step or 0)
        if source is not None:
            self.source = str(source or "")
        return self

    def close(self) -> None:
        try:
            self.store.close()
        except Exception as exc:  # noqa: BLE001 — a close failure is not a run failure
            self.last_error = f"close: {type(exc).__name__}: {exc}"

    def __enter__(self) -> DecisionBus:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- the emit surface ------------------------------------------------------ #

    def emit(self, kind: str, payload: dict | None = None) -> None:
        """Append one decision-layer event. Never raises (the façade's contract).

        Two shapes reach here and both are handled explicitly:

        * ``decide._finish`` sends ``{"decision_id": …, "kind": …, …, "decision": <dump>}``
          — the full decision document is what gets shaped and persisted;
        * the substrate sends the ``system_one.*`` lifecycle payloads, which the service has
          already put through the §24 guard — they are stored as they arrive.
        """
        text = str(kind or "")
        body = dict(payload or {})
        if text == DECISION_EVENT:
            document = body.get("decision")
            if not isinstance(document, Mapping):
                document = body
            body = decision_payload(
                document,
                source=str(body.get("source") or self.source),
                step=int(body.get("step") or self.step),
            )
        else:
            body = {
                str(key): _bounded(value, MAX_FIELD_CHARS) for key, value in body.items()
            }
        self.write(text, body)

    def write(self, kind: str, payload: dict) -> bool:
        """Append one already-shaped row; returns whether it landed.

        A row that names a decision (``decision_id``) or a record (``record``) is written
        **once per session/step**: a caller that asks the façade and then records the same
        document by hand must not produce two rows for one decision. The repeat is counted
        (``duplicates``), never silently swallowed.
        """
        text = str(kind or "")
        # what makes a row unique: its own record name first, then the decision it names. Both
        # are used, joined — an observation of a decision and the decision's own row share the
        # id but are different facts and must both survive (measured: keying on the id alone
        # swallowed every observation).
        record_name = str(payload.get("record") or "")
        decision_name = str(payload.get("decision_id") or "")
        named = f"{record_name}\x00{decision_name}" if (record_name or decision_name) else ""
        key = (self.session_id, self.step, text, named)
        if named and key in self._seen:
            self.duplicates += 1
            return False
        try:
            event = self.store.append_redacted(
                self.session_id, self.step, text, dict(payload)
            )
        except Exception as exc:  # noqa: BLE001 — an emitter must never break a run
            self.dropped += 1
            self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            return False
        if named:
            self._seen.add(key)
        self.written += 1
        self.kinds[text] = self.kinds.get(text, 0) + 1
        if int(getattr(event, "payload", {}).get("_godsv_redacted", 0) or 0) > 0:
            self.redacted += 1
        return True

    def decision(
        self,
        document: Mapping[str, Any],
        *,
        source: str = "",
        step: int | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict | None:
        """Record one decision document as a ``jev_decision`` row.

        This is the path for a call site that calls the façade itself
        (``voice_service``, ``adversarial``, the CLI verbs): the decision is already made,
        the subscription only persists it. Returns the payload that was written, or
        ``None`` when the store refused it (``dropped``/``last_error`` say why).
        """
        payload = decision_payload(
            document, source=source or self.source, step=self.step if step is None else step,
            extra=extra,
        )
        return payload if self.write(DECISION_EVENT, payload) else None

    def observation(self, name: str, **fields: Any) -> dict | None:
        """Record a **non-decision** fact of the same path (never dressed up as a decision).

        Used where the runtime observed something about a decision — what the model
        actually routed to, which plan the suite ran — so the timeline can be read without
        pretending those rows are advisories: they carry ``record: <name>`` and
        ``authority: "record"``.
        """
        payload = {
            "record": str(name),
            "authority": "record",
            "source": self.source,
            "step": int(self.step),
            **{str(key): _bounded(value, MAX_FIELD_CHARS) for key, value in fields.items()},
        }
        return payload if self.write(DECISION_EVENT, payload) else None

    # -- reporting -------------------------------------------------------------#

    def as_dict(self) -> dict:
        return {
            "subscribed": True,
            "events_db": self.path,
            "runtime": self.runtime,
            "session_id": self.session_id,
            "written": self.written,
            "dropped": self.dropped,
            "redacted": self.redacted,
            "duplicates": self.duplicates,
            "kinds": dict(sorted(self.kinds.items())),
            "last_error": self.last_error,
        }


class NullBus:
    """The explicit *unattached* subscription — a caller that wants no store writes.

    Not a silent fallback: ``subscribed`` is ``False`` and the wiring status prints it, so
    "no rows appeared" is always explained by the object that did not write them.
    """

    subscribed = False

    def __init__(self, reason: str = "") -> None:
        self.reason = str(reason or "no event store was attached")
        self.written = 0
        self.dropped = 0
        self.redacted = 0
        self.duplicates = 0
        self.kinds: dict[str, int] = {}

    def bind(self, **_: Any) -> NullBus:
        return self

    def emit(self, kind: str, payload: dict | None = None) -> None:  # pragma: no cover
        return None

    def write(self, kind: str, payload: dict) -> bool:
        return False

    def decision(self, document: Mapping[str, Any], **_: Any) -> None:
        return None

    def observation(self, name: str, **_: Any) -> None:
        return None

    def close(self) -> None:
        return None

    def __enter__(self) -> NullBus:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def as_dict(self) -> dict:
        return {"subscribed": False, "reason": self.reason, "written": 0, "dropped": 0}


def bus_for(
    runtime: str | Path | None = None,
    *,
    session_id: str = "jev",
    step: int = 0,
    source: str = "",
    create: bool = True,
) -> DecisionBus | NullBus:
    """The subscription for this runtime — a real bus, or an honest ``NullBus``.

    ``create=False`` refuses to create the store (a read-only caller must not make the
    runtime look alive); a store that cannot be opened yields ``NullBus`` **with the
    reason**, never an exception in the middle of a run.
    """
    root = runtime_dir(runtime)
    db = root / "events.db"
    if not create and not db.exists():
        return NullBus(f"no event store at {db} and create=False")
    try:
        store = EventStore(db)
    except Exception as exc:  # noqa: BLE001 — an unwritable store is reported, not raised
        return NullBus(f"{type(exc).__name__}: {str(exc)[:200]}")
    return DecisionBus(store, runtime=root, session_id=session_id, step=step, source=source)


# --------------------------------------------------------------------------- #
# arming                                                                     #
# --------------------------------------------------------------------------- #


def armed_flags(runtime: str | Path | None = None, *, provider: str = "", **overrides: Any):
    """The ``SystemOneFlags`` of this runtime, with an optional explicit provider.

    ``provider=""`` means "whatever ``settings.yaml`` says" — the fix the round asked for
    (the verbs used to default to ``""``/``disabled`` and then report ``provider_disabled``
    while the settings said the opposite). An unrecognised provider string is refused by
    the flags' own validation instead of arming something unknown.
    """
    from ..system_one_flags import load_system_one_flags

    flags = load_system_one_flags(runtime_dir(runtime))
    if str(provider or "").strip():
        flags = flags.with_flag("provider", str(provider))
    for name, value in overrides.items():
        flags = flags.with_flag(name, value)
    return flags


def armed_service(
    runtime: str | Path | None = None,
    *,
    session_id: str = "jev",
    step: int = 0,
    provider: str = "",
    llm: Any = None,
    source: str = "",
    **overrides: Any,
):
    """``(service, bus)`` — the decision layer **subscribed to the runtime's store**.

    One construction path: the flags of this runtime decide the evaluator, the provider
    edge is resolved by the façade's own :func:`skaldir.jev.decide.service_for` (never a
    second edge into the substrate), and the emitter is the bus. A provider that cannot be
    built is not replaced by a scripted one — the service degrades to its disabled
    evaluator and the caller names the blocker it got back.
    """
    from .decide import service_for

    bus = bus_for(runtime, session_id=session_id, step=step, source=source)
    flags = armed_flags(runtime, provider=provider, **overrides)
    service = service_for(flags, llm=llm, emit=bus.emit)
    return service, bus, flags


# --------------------------------------------------------------------------- #
# the agent loop's subscription: turn boundaries + tool routing               #
# --------------------------------------------------------------------------- #


class TurnDecisions:
    """The decision layer attached to a run's **turn boundaries** (tool routing).

    The loop publishes; Jev decides; the bus writes. Two things are recorded per turn:

    * ``jev_decision`` — the advisory the façade produced for this turn boundary
      (``kind=tool_routing``: the offered tool surface as candidates, the route the model
      of the decision layer chose, its confidence and the measured latency);
    * ``jev_decision`` with ``record="observed_routing"`` — what the run *actually* did
      with the tools the loop offered, tied to the advisory's ``decision_id``. A record is
      never an advisory and says so (``authority: "record"``).

    Nothing here changes the loop: the advisory is **never obeyed** (§9/§34). A disabled or
    unconfigured provider produces a ``blocked`` decision with the reason, and the turn
    continues exactly as it would have.
    """

    def __init__(
        self,
        bus: DecisionBus | NullBus,
        *,
        flags: Any = None,
        service: Any = None,
        llm: Any = None,
        provider: str = "",
        max_candidates: int = 24,
    ) -> None:
        self.bus = bus
        self.flags = flags
        self.service = service
        self.llm = llm
        self.provider = str(provider or "")
        self.max_candidates = max(1, int(max_candidates))
        self.run_id = ""
        self.goal = ""
        self.task = ""
        self.turn = 0
        self.decisions: list[dict] = []
        self.observed: list[dict] = []
        self.failures: list[str] = []

    # -- run identity ---------------------------------------------------------- #

    def start_run(self, *, run_id: str = "", goal: str = "", task: str = "") -> None:
        self.run_id = str(run_id or "")
        self.goal = str(goal or "")[:200]
        self.task = str(task or "")[:400]
        self.turn = 0
        self.bus.bind(session_id=self.run_id or "jev", step=0, source="agent_loop")

    def close(self) -> None:
        self.bus.close()

    # -- the two boundaries the loop publishes --------------------------------- #

    def before_turn(
        self,
        *,
        step: int,
        tool_names: Any = (),
        intent: str = "",
        recent_failures: Any = (),
    ) -> dict:
        """Ask Jev how this turn should be routed; record the advisory. Never raises."""
        from . import contracts as JC
        from .decide import decide

        self.turn = int(step or 0)
        names = [str(name) for name in (tool_names or ())][: self.max_candidates]
        self.bus.bind(step=self.turn, source="agent_loop.turn_boundary")
        request = JC.JevToolRoutingRequest(
            run_id=self.run_id or "agent-run",
            goal_id=self.goal or "unrecorded-goal",
            next_step_intent=str(intent or self.task or "")[:400],
            candidates=tuple(
                JC.RouteCandidate(
                    candidate_id=name,
                    kind="native_tool",
                    name=name,
                    source="registry",
                    preconditions_satisfied=True,
                    health="ok",
                    permission_state="allowed",
                )
                for name in names
            ),
            recent_failures=tuple(str(item)[:120] for item in (recent_failures or ())[:6]),
        )
        started = time.time()
        written_before = int(getattr(self.bus, "written", 0))
        try:
            if self.service is not None:
                decision = decide(request, service=self.service)
            else:
                decision = decide(request, flags=self.flags, llm=self.llm, emit=self.bus.emit)
        except Exception as exc:  # noqa: BLE001 — a Jev problem is reported, never invented
            self.failures.append(f"{type(exc).__name__}: {str(exc)[:200]}")
            self.bus.observation(
                "turn_boundary_failed",
                step=self.turn,
                error=f"{type(exc).__name__}: {str(exc)[:400]}",
                offered_tools=names,
            )
            return {"status": "error", "error": str(exc)[:200], "step": self.turn}
        document = decision.model_dump(mode="json")
        # The service is the bus's owner on this path, so the decision row is already
        # published by the time ``decide()`` returns; the explicit call below adds the
        # offered surface and is deduped when the row exists (one decision, one row).
        self.bus.decision(document, source="agent_loop.turn_boundary", step=self.turn)
        boundary = self.bus.observation(
            "turn_boundary",
            decision_id=str(document.get("decision_id", "")),
            offered_tools=names,
            offered_count=len(names),
            status=str(document.get("status", "")),
            provider_mode=str(document.get("provider_mode", "")),
            live_jev=bool(document.get("live_jev")),
        )
        summary = {
            "decision_id": str(document.get("decision_id", "")),
            "status": str(document.get("status", "")),
            "provider_mode": str(document.get("provider_mode", "")),
            "live_jev": bool(document.get("live_jev")),
            "confidence": float(document.get("confidence", 0.0) or 0.0),
            "latency_ms": int(getattr(decision, "latency_ms", 0) or 0),
            "call_wall_ms": round((time.time() - started) * 1000.0, 3),
            "chosen": str(getattr(getattr(decision, "answer", None), "chosen_kind", "") or ""),
            # "a row for this decision is in the store" — the service's own row counts
            "recorded": self.bus.written > written_before,
            "boundary_recorded": boundary is not None,
            "step": self.turn,
        }
        self.decisions.append(summary)
        return summary

    def after_turn(self, *, step: int, tool_names: Any = (), final: bool = False) -> dict | None:
        """Record what the run did with the tools — tied to this turn's advisory."""
        asked = next(
            (item for item in self.decisions if int(item.get("step", 0)) == int(step or 0)),
            None,
        )
        names = [str(name) for name in (tool_names or ())]
        self.bus.bind(step=int(step or 0), source="agent_loop.tool_routing")
        advisory_route = str((asked or {}).get("chosen", ""))
        payload = self.bus.observation(
            "observed_routing",
            decision_id=str((asked or {}).get("decision_id", "")),
            step=int(step or 0),
            observed_tools=names,
            observed_count=len(names),
            advisory_chosen=advisory_route,
            # "agrees" compares the advisory's outcome with what happened; an advisory that
            # said "route" while the run called no tool is a disagreement, and it is
            # recorded, not resolved.
            agrees=(advisory_route == "route") == bool(names) if asked else None,
            final=bool(final),
        )
        if payload is not None:
            self.observed.append(payload)
        return payload

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "turns": self.turn,
            "decisions": len(self.decisions),
            "observed": len(self.observed),
            "failures": list(self.failures),
            "bus": self.bus.as_dict(),
        }


def compaction_sink(bus: DecisionBus | NullBus, *, source: str = "context_manager.compaction"):
    """``emit`` handler for :class:`skaldir.context_manager.ContextManager` (compaction path).

    The manager calls ``emit("context_compaction", payload)`` where a compaction really ran;
    this sink turns that into one ``jev_decision`` row and forwards any other kind untouched,
    so the manager needs no knowledge of the store or of the row shape. Rows written here are
    **records, not advisories** (``authority: "record"``) and carry no decision id: the
    decision is the jevcomp port's per-call plan, and the row says exactly that.
    """
    counter = {"n": 0}

    def sink(kind: str, payload: dict | None = None) -> None:
        text = str(kind or "")
        if text != "context_compaction":
            bus.emit(text, payload or {})
            return
        counter["n"] += 1
        row = {
            "record": str((payload or {}).get("record") or f"compaction_run#{counter['n']}"),
            "authority": "record",
            "source": source,
            "kind": "context_compaction",
            "step": int(getattr(bus, "step", 0) or 0),
        }
        for key, value in dict(payload or {}).items():
            row[str(key)] = _bounded(value, MAX_FIELD_CHARS)
        bus.write(DECISION_EVENT, row)

    return sink


def loop_decisions(
    runtime: str | Path | None = None,
    *,
    provider: str = "",
    llm: Any = None,
    **_flags: Any,
) -> TurnDecisions:
    """The subscription the agent loop attaches — ``(service, bus, flags)`` in one call."""
    service, bus, flags = armed_service(
        runtime, provider=provider, llm=llm, session_id="agent", source="agent_loop"
    )
    return TurnDecisions(bus, flags=flags, service=service, llm=llm, provider=provider)


# --------------------------------------------------------------------------- #
# status: what is armed, what is not, and what a TypeSafe credential would buy #
# --------------------------------------------------------------------------- #


def typesafe_status(runtime: str | Path | None = None) -> dict:
    """Whether a *live* Jev (authorized TypeSafe) call is possible here, and why not.

    ``typesafe_jev_live`` is reserved for an authorized TypeSafe call; this function only
    checks whether the credential named by the runtime's config resolves. It never prints
    or returns a value — the variable *name* is the most it reports.
    """
    flags = armed_flags(runtime)
    why = ""
    if flags.provider not in ("typesafe", "typesafe_jev"):
        why = (
            "system_one.provider is "
            f"{flags.provider!r}: only provider='typesafe_jev' can answer live_jev"
        )
    else:
        from .decide import provider_binding

        client, problem = provider_binding(flags)
        if problem:
            why = problem
        elif client is None:
            why = "no client could be built for the typesafe provider"
    return {
        "available": not why,
        "reason": why,
        "provider": flags.provider,
        "would_buy": (
            "an authorized TypeSafe call: a real Jev answer with live_jev=true and "
            "provider_mode=typesafe_jev_live, replacing the LLM-adapter approximation "
            "(provider_mode=llm_system_one_adapter, live_jev=false)"
        ),
    }


def wiring_status(runtime: str | Path | None = None, *, session_id: str = "") -> dict:
    """Measure the subscription: what is armed, and what the store really holds.

    Read-only (``create=False``): asking what is armed must not make a runtime exist.
    """
    from ..voice_service import _read_only  # one read-only connection helper, not a copy

    root = runtime_dir(runtime)
    db = root / "events.db"
    counts: dict[str, int] = {}
    total = 0
    sessions = 0
    detail: dict[str, dict] = {}
    if db.exists():
        connection = _read_only(db)
        if connection is not None:
            try:
                total = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
                sessions = int(
                    connection.execute(
                        "SELECT COUNT(DISTINCT session_id) FROM events"
                    ).fetchone()[0]
                )
                rows = connection.execute(
                    "SELECT kind, COUNT(*) FROM events WHERE kind LIKE 'jev%' "
                    "OR kind LIKE 'system_one%' GROUP BY kind ORDER BY kind"
                ).fetchall()
                counts = {str(kind): int(count) for kind, count in rows}
                for kind in counts:
                    sample = connection.execute(
                        "SELECT session_id, step, payload FROM events WHERE kind = ? "
                        "ORDER BY seq DESC LIMIT 1",
                        (kind,),
                    ).fetchone()
                    if sample is None:
                        continue
                    try:
                        payload = json.loads(sample[2])
                    except Exception:  # noqa: BLE001
                        payload = {}
                    detail[kind] = {
                        "last_session": str(sample[0]),
                        "last_step": int(sample[1]),
                        "last_decision_id": str(payload.get("decision_id", "")),
                        "last_provider_mode": str(payload.get("provider_mode", "")),
                        "last_live_jev": bool(payload.get("live_jev", False)),
                        "last_latency_ms": int(payload.get("latency_ms", 0) or 0),
                    }
            finally:
                try:
                    connection.close()
                except Exception:  # noqa: BLE001
                    pass
    flags = armed_flags(root)
    return {
        "runtime": str(root),
        "events_db": str(db),
        "events_total": total,
        "sessions": sessions,
        "decision_kinds": counts,
        "decision_rows": int(sum(counts.values())),
        "last": detail,
        "flags": flags.as_dict(),
        "armed_paths": list(ARMED_PATHS),
        "unarmed_paths": [{"path": path, "why": why} for path, why in UNARMED_PATHS],
        "live_jev_possible": typesafe_status(root),
    }


__all__ = [
    "ARMED_PATHS",
    "DECISION_EVENT",
    "DECISION_KEYS",
    "DecisionBus",
    "NullBus",
    "TurnDecisions",
    "UNARMED_PATHS",
    "armed_flags",
    "armed_service",
    "bus_for",
    "compaction_sink",
    "decision_payload",
    "loop_decisions",
    "pinned_provider_env",
    "runtime_dir",
    "typesafe_status",
    "wiring_status",
]
