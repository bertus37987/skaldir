"""Adversarial release test suite — many isolated cases in parallel, deterministic verdicts.

This is the suite behind Jev's ``adversarial_test_planning`` decision kind (MVP/03 §5.6).
``browser-use/jev-ultrafast`` was studied as a *reference for the shape of the suite only*:
many isolated cases in parallel, a fast planner that picks operation + target, cases that
deliberately try to break real user flows, a deterministic oracle per case, and a verdict that
can block a release. No code, no dependency and no general computer-use system is adopted from
it; the cases here run on Playwright (already a dependency of this environment) and httpx.

Four parts, one contract:

1. **A declared action space.** :data:`ACTION_SPACE` is the whole catalogue: one
   :class:`AttackCase` per ``(surface, intent_class)`` pair, each naming its driver (the
   hostile interaction), the operation and oracle kind it must be driven with, the intended
   behaviour it expects, and the defect class a violation means. There are no generated
   selectors, no generated code and no model output on the execution path: a case the
   catalogue does not declare cannot run.
2. **A fast planner.** :func:`plan_suite` asks the **Jev façade** (``skaldir.jev.decide`` with
   kind ``adversarial_test_planning``) which offered surface to target next and which intent
   class to exercise. Jev composes the question set, validates the answers, applies its own
   budget arithmetic and returns an advisory :class:`~skaldir.jev.contracts.AdversarialPlanAnswer`
   whose ``operation``/``success_oracle``/``target_ref`` are *declared* mappings — never a
   selector or a script. The advisor behind those answers is this module's own
   :class:`DeclaredSpaceAdvisor` (a deterministic reading of the declared catalogue), and when
   the façade cannot be imported or cannot answer, :func:`_plan_deterministic` plans the same
   catalogue with the same ordering and the plan says so (``planner: "deterministic"``).
3. **Deterministic verification.** Every case carries an oracle over *observations*, not over
   impressions: DOM values read through the page, the target's own state endpoint read through
   the same session, HTTP status codes. A case whose required observation is missing, whose
   oracle was not assigned by the plan, or whose driver could not reach the target is reported
   ``unknown`` — **never** ``pass`` (:class:`Unverifiable`).
4. **A release-blocking verdict.** :func:`verdict_for` returns ``PASS``/``FAIL``/``UNKNOWN``;
   the report lists the defects with their defect class, the case, the oracle and the
   observation that violated it. ``adversarial run --block-release`` maps the verdict onto the
   process exit code (``0`` only for PASS).

Two targets ship with the suite so it can be proved without the owner's environment:

* :class:`CanaryApp` — a small local web app served by the stdlib, with a ``broken`` mode that
  contains **real** defect classes (no server-side validation, no idempotency, an optimistic
  success claim, no POST-redirect-GET, a client that ignores an expired session) and a
  ``fixed`` mode that behaves correctly. The suite must FAIL on ``broken`` *and* PASS on
  ``fixed``; that pair is what ``adversarial selftest`` asserts, because a suite that cannot
  distinguish the two is decoration.
* the **real Skaldir HTTP API** (``--with-api`` starts ``skaldir.api_server.serve`` in its own
  process, in a throwaway runtime dir): the API cases read the served OpenAPI document and
  check the boundaries that must hold in production (an unknown route is a 404 and never a
  500, a mutation without a token is refused, a malformed or oversized body is never a 200).

The suite never reaches the public network: every request goes to a loopback URL, and Jev's
budget is planned with ``allow_network=False``.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

import httpx

SUITE_VERSION = "adversarial-suite/1"

#: outcomes a *runner* may record. ``pass``/``fail`` are measured against the case's oracle;
#: ``unknown`` means the oracle could not be evaluated (never a pass).
OUTCOMES = ("pass", "fail", "unknown")
VERDICTS = ("PASS", "FAIL", "UNKNOWN")

#: the defect classes this catalogue can name. A violation is reported with one of these, so a
#: defect report can be grouped and tracked instead of read as free text.
DEFECT_EMPTY_INPUT = "empty_input_accepted"
DEFECT_UNBOUNDED_INPUT = "unbounded_input_accepted"
DEFECT_UNESCAPED_INPUT = "unescaped_input_rendered"
DEFECT_DOUBLE_SUBMIT = "double_submit_duplicate"
DEFECT_SLOW_NETWORK = "slow_network_duplicate"
DEFECT_FALSE_SUCCESS = "false_success_unconfirmed"
DEFECT_HISTORY_REPLAY = "history_submit_replayed"
DEFECT_EXPIRED_SESSION = "expired_session_false_success"
DEFECT_CONTROL_FLOW = "control_flow_broken"
DEFECT_HEALTH = "health_not_ok"
DEFECT_UNKNOWN_ROUTE = "unknown_route_mishandled"
DEFECT_MUTATION_AUTH = "mutation_without_token_allowed"
DEFECT_MALFORMED_BODY = "malformed_body_mishandled"
DEFECT_CONCURRENT = "concurrent_requests_degraded"
DEFECT_OVERSIZED_BODY = "oversized_body_mishandled"
DEFECT_OPENAPI = "openapi_unstable"
#: the three *rendering* classes: a user-flow verifier catches these, and so does this suite
DEFECT_LAYOUT = "layout_contract_broken"
DEFECT_ELEMENT_MISSING = "declared_element_missing"
DEFECT_TEXT_MISMATCH = "declared_text_mismatch"

#: the intent classes of the Jev pack, in the pack's own order (never re-spelled here).
INTENT_CLASSES = (
    "boundary",
    "stale_state",
    "permission",
    "concurrency",
    "injection",
    "persistence",
    "dependency_failure",
    "regression",
)

#: How many asks one adversarial case may cost on the **live** path (MVP/87). A model's
#: answer is a sample, and one unreadable, refused or repeated sample must cost a retry
#: rather than the plan — measured before this bound existed: three live `--planner jev`
#: runs planned 1, 2 and 2 of 12 cases, and one of them ended with the façade's
#: `SystemOneContractError` escaping the planner entirely, which handed the run to the
#: deterministic reader while the command still said `--planner jev`. The offline path
#: keeps exactly one ask: the declared-space reader is deterministic, so a second ask
#: could not change its answer.
ASKS_PER_CASE = 3

#: how a case's oracle is allowed to be sourced. ``deterministic`` is obligatory: a case that
#: could only be judged by Computer Use is planned (Jev may say so) and then reported UNKNOWN.
ORACLE_SOURCES = ("deterministic", "computer_use")


class Unverifiable(Exception):
    """The oracle could not be evaluated. Always reported as ``unknown``, never as a pass."""


class PlannerUnavailable(RuntimeError):
    """The suite could not produce a plan at all (no Jev façade *and* no catalogue match)."""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha256_json(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: an observation may carry whatever the target stored (a 200k-character value in the oversized
#: case). The report keeps a bounded, still-diagnostic view and hashes exactly that view, so the
#: hash a reader recomputes is the hash of what they can see.
OBSERVATION_CHAR_LIMIT = 256


def _trim_observation(value: Any, *, limit: int = OBSERVATION_CHAR_LIMIT) -> Any:
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return f"{value[:limit]}…[+{len(value) - limit} chars]"
    if isinstance(value, Mapping):
        return {str(key): _trim_observation(item, limit=limit) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_trim_observation(item, limit=limit) for item in value]
    return value


def _need(observed: Mapping[str, Any], key: str) -> Any:
    """Read a required observation, or refuse to judge (``Unverifiable``)."""
    if key not in observed or observed[key] is None:
        raise Unverifiable(f"observation {key!r} is missing — nothing to assert against")
    return observed[key]


# --------------------------------------------------------------------------- #
# the declared action space                                                     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AttackCase:
    """One declared adversarial case. Everything the runner may do is named here."""

    case_id: str
    surface_id: str
    intent_class: str
    #: what a violation of this case means (one of the ``DEFECT_*`` constants)
    defect_class: str
    #: the interaction the runner is allowed to drive (a key of :data:`DRIVERS`)
    driver: str
    #: the operation Jev must assign for this surface kind (``click``/``http_request``)
    operation: str
    #: the oracle kind Jev must assign (``observed_state``/``http_status``)
    oracle_kind: str
    #: the oracle that decides pass/fail over the observations (a key of :data:`ORACLES`)
    oracle: str
    #: the behaviour the target is supposed to have, in one line
    expects: str
    #: what the case deliberately breaks
    hostile: str
    priority: int = 2
    risk_class: str = "R1"
    #: how long the *target* is asked to take before answering this case's write, in ms. The
    #: delay is the server's, not the driver's: a route handler that sleeps would block the
    #: driver's own timeline in the sync API (measured), which is not a slow network.
    slow_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "surface_id": self.surface_id,
            "intent_class": self.intent_class,
            "defect_class": self.defect_class,
            "driver": self.driver,
            "operation": self.operation,
            "oracle_kind": self.oracle_kind,
            "oracle": self.oracle,
            "expects": self.expects,
            "hostile": self.hostile,
            "priority": self.priority,
            "risk_class": self.risk_class,
            "slow_ms": self.slow_ms,
        }


#: the catalogue. Two surfaces, one case per ``(surface, intent_class)`` pair.
ACTION_SPACE: tuple[AttackCase, ...] = (
    AttackCase(
        case_id="ui-empty-input",
        surface_id="canary_ui",
        intent_class="boundary",
        defect_class=DEFECT_EMPTY_INPUT,
        driver="empty_input",
        operation="click",
        oracle_kind="observed_state",
        oracle="entry_rejected",
        expects="an empty required field is refused client- and server-side and no entry is stored",
        hostile="submit the form with an empty name",
    ),
    AttackCase(
        case_id="ui-huge-input",
        surface_id="canary_ui",
        intent_class="injection",
        defect_class=DEFECT_UNESCAPED_INPUT,
        driver="hostile_input",
        operation="click",
        oracle_kind="observed_state",
        oracle="payload_not_rendered",
        expects="a stored value is escaped when it is rendered back, never executed as markup",
        hostile="store a value that contains markup with an event handler",
    ),
    AttackCase(
        case_id="ui-oversized-input",
        surface_id="canary_ui",
        intent_class="boundary",
        defect_class=DEFECT_UNBOUNDED_INPUT,
        driver="oversized_input",
        operation="click",
        oracle_kind="observed_state",
        oracle="entry_rejected",
        expects="a value far beyond the documented limit is refused and no entry is stored",
        hostile="submit a 200k-character value into a field with a documented 2000-char limit",
    ),
    AttackCase(
        case_id="ui-double-submit",
        surface_id="canary_ui",
        intent_class="concurrency",
        defect_class=DEFECT_DOUBLE_SUBMIT,
        driver="double_submit",
        operation="click",
        oracle_kind="observed_state",
        oracle="single_entry",
        expects="two submit actions for one intention create exactly one entry",
        hostile="click submit twice while the first request is still in flight",
        slow_ms=1200,
    ),
    AttackCase(
        case_id="ui-slow-network",
        surface_id="canary_ui",
        intent_class="dependency_failure",
        defect_class=DEFECT_SLOW_NETWORK,
        driver="slow_network",
        operation="click",
        oracle_kind="observed_state",
        oracle="single_entry",
        expects="a slow response does not turn repeated clicks into repeated entries",
        hostile="click submit three times while the response is delayed by 2.5s",
        slow_ms=2500,
    ),
    AttackCase(
        case_id="ui-refresh-mid-action",
        surface_id="canary_ui",
        intent_class="stale_state",
        defect_class=DEFECT_FALSE_SUCCESS,
        driver="refresh_mid_action",
        operation="click",
        oracle_kind="observed_state",
        oracle="no_unconfirmed_claim",
        expects="a success claim appears only after the server confirmed the write",
        hostile="reload the page while the submit request is still on the wire",
        slow_ms=1500,
    ),
    AttackCase(
        case_id="ui-expired-session",
        surface_id="canary_ui",
        intent_class="permission",
        defect_class=DEFECT_EXPIRED_SESSION,
        driver="expired_session",
        operation="click",
        oracle_kind="observed_state",
        oracle="no_unconfirmed_claim",
        expects="an expired session is surfaced as an error; a refused write is never 'Saved'",
        hostile="expire the session, then submit",
    ),
    AttackCase(
        case_id="ui-history-replay",
        surface_id="canary_ui",
        intent_class="persistence",
        defect_class=DEFECT_HISTORY_REPLAY,
        driver="history_replay",
        operation="click",
        oracle_kind="observed_state",
        oracle="single_entry",
        expects="a completed submit is not sent again by a reload or by back/forward",
        hostile="submit natively, reload the result, then walk the history back and forth",
    ),
    AttackCase(
        case_id="ui-control-happy-path",
        surface_id="canary_ui",
        intent_class="regression",
        defect_class=DEFECT_CONTROL_FLOW,
        driver="happy_path",
        operation="click",
        oracle_kind="observed_state",
        oracle="confirmed_single_entry",
        expects="the ordinary flow works: one confirmed entry, claimed only after confirmation",
        hostile="nothing — this is the positive control",
    ),
    AttackCase(
        case_id="ui-broken-layout",
        surface_id="canary_ui",
        intent_class="regression",
        defect_class=DEFECT_LAYOUT,
        driver="layout_read",
        operation="click",
        oracle_kind="observed_state",
        oracle="layout_contract",
        expects=(
            "the declared page region exists and owns the entry list, the status line and "
            "the form"
        ),
        hostile=(
            "open the page and read where the declared regions and anchors actually are"
        ),
    ),
    AttackCase(
        case_id="ui-missing-element",
        surface_id="canary_ui",
        intent_class="regression",
        defect_class=DEFECT_ELEMENT_MISSING,
        driver="element_read",
        operation="click",
        oracle_kind="observed_state",
        oracle="elements_present",
        expects="every declared UI anchor is present in the served document",
        hostile="open the page and ask for each declared anchor by its declared selector",
    ),
    AttackCase(
        case_id="ui-wrong-text",
        surface_id="canary_ui",
        intent_class="regression",
        defect_class=DEFECT_TEXT_MISMATCH,
        driver="text_read",
        operation="click",
        oracle_kind="observed_state",
        oracle="declared_text",
        expects="the declared controls carry exactly the contract's text",
        hostile="open the page and read back the declared control texts",
    ),
    AttackCase(
        case_id="api-health",
        surface_id="skaldir_api",
        intent_class="regression",
        defect_class=DEFECT_HEALTH,
        driver="api_health",
        operation="http_request",
        oracle_kind="http_status",
        oracle="health_ok",
        expects="GET /health answers 200 with a machine-readable body",
        hostile="nothing — the API's own liveness contract",
    ),
    AttackCase(
        case_id="api-unknown-route",
        surface_id="skaldir_api",
        intent_class="boundary",
        defect_class=DEFECT_UNKNOWN_ROUTE,
        driver="api_unknown_route",
        operation="http_request",
        oracle_kind="http_status",
        oracle="unknown_route_404",
        expects="an unknown path answers 404, never 200 and never 5xx",
        hostile="request a path no command declares",
    ),
    AttackCase(
        case_id="api-missing-token",
        surface_id="skaldir_api",
        intent_class="permission",
        defect_class=DEFECT_MUTATION_AUTH,
        driver="api_missing_token",
        operation="http_request",
        oracle_kind="http_status",
        oracle="mutation_needs_token",
        expects="every mutation route refuses a caller without a token (401/403, never 200)",
        hostile="POST to every mutation route in the served document without a token",
    ),
    AttackCase(
        case_id="api-malformed-body",
        surface_id="skaldir_api",
        intent_class="injection",
        defect_class=DEFECT_MALFORMED_BODY,
        driver="api_malformed_body",
        operation="http_request",
        oracle_kind="http_status",
        oracle="malformed_refused",
        expects="a malformed body is refused (4xx) and never reaches a handler as a 200",
        hostile="POST a truncated JSON body to a mutation route",
    ),
    AttackCase(
        case_id="api-oversized-body",
        surface_id="skaldir_api",
        intent_class="stale_state",
        defect_class=DEFECT_OVERSIZED_BODY,
        driver="api_oversized_body",
        operation="http_request",
        oracle_kind="http_status",
        oracle="oversized_refused",
        expects="a body beyond the documented limit is refused without a success envelope",
        hostile="POST 1.5 MiB to a mutation route",
    ),
    AttackCase(
        case_id="api-concurrent-health",
        surface_id="skaldir_api",
        intent_class="concurrency",
        defect_class=DEFECT_CONCURRENT,
        driver="api_concurrent",
        operation="http_request",
        oracle_kind="http_status",
        oracle="all_healthy",
        expects="eight concurrent status requests all answer 200",
        hostile="hit the status route from eight clients at once",
    ),
    AttackCase(
        case_id="api-openapi-stable",
        surface_id="skaldir_api",
        intent_class="dependency_failure",
        defect_class=DEFECT_OPENAPI,
        driver="api_openapi",
        operation="http_request",
        oracle_kind="http_status",
        oracle="openapi_stable",
        expects="the served surface document is the same document on every call",
        hostile="fetch the surface document twice and compare the bytes",
    ),
)

#: ``(surface_id, intent_class)`` → the declared cases of that pair, in catalogue order. Jev
#: decides the *pair* (surface + intent class); the catalogue decides which of its declared
#: cases runs next, so two cases may share a pair without one shadowing the other.
CASE_BY_PAIR: dict[tuple[str, str], tuple[AttackCase, ...]] = {}
for _case in ACTION_SPACE:
    CASE_BY_PAIR.setdefault((_case.surface_id, _case.intent_class), ())
    CASE_BY_PAIR[(_case.surface_id, _case.intent_class)] += (_case,)

CASE_BY_ID: dict[str, AttackCase] = {case.case_id: case for case in ACTION_SPACE}


def next_case_for_pair(
    pair: tuple[str, str], covered: Mapping[str, bool] | None = None
) -> AttackCase | None:
    """The first declared case of a pair that the coverage ledger has not seen yet."""
    seen = dict(covered or {})
    for case in CASE_BY_PAIR.get(pair, ()):
        if not seen.get(case.case_id):
            return case
    return None


# --------------------------------------------------------------------------- #
# targets and surfaces                                                          #
# --------------------------------------------------------------------------- #


#: the surface kinds Jev's contract declares (never invented here).
SurfaceKind = Literal["ui_flow", "api_endpoint", "cli", "file_io", "permission_boundary"]
RiskClass = Literal["R0", "R1", "R2", "R3", "R4"]


@dataclass(frozen=True)
class TargetSurface:
    """An observed surface offered to the planner (Jev's ``TestSurface``, plus its URL)."""

    surface_id: str
    kind: SurfaceKind
    url: str
    entry_hint: str
    risk_class: RiskClass = "R1"
    seed_operations: tuple[str, ...] = ()

    def declared_cases(self) -> tuple[AttackCase, ...]:
        return tuple(
            case
            for case in ACTION_SPACE
            if case.surface_id == self.surface_id and case.intent_class in self.seed_operations
        )


CANARY_SURFACE_ID = "canary_ui"
SKALDIR_API_SURFACE_ID = "skaldir_api"


def offered_surfaces(
    *,
    ui_url: str = "",
    api_url: str = "",
) -> tuple[TargetSurface, ...]:
    """The surfaces this run offers. A surface without a URL is *not offered* (never UNKNOWN).

    An unreachable surface is not a test result: if the owner did not start one, the cases for
    it are simply not planned, and the plan says which surfaces were offered.
    """
    offered: list[TargetSurface] = []
    if ui_url:
        offered.append(
            TargetSurface(
                surface_id=CANARY_SURFACE_ID,
                kind="ui_flow",
                url=ui_url,
                entry_hint="#form",
                risk_class="R1",
                seed_operations=tuple(
                    case.intent_class
                    for case in ACTION_SPACE
                    if case.surface_id == CANARY_SURFACE_ID
                ),
            )
        )
    if api_url:
        offered.append(
            TargetSurface(
                surface_id=SKALDIR_API_SURFACE_ID,
                kind="api_endpoint",
                url=api_url,
                entry_hint="/health",
                risk_class="R2",
                seed_operations=tuple(
                    case.intent_class
                    for case in ACTION_SPACE
                    if case.surface_id == SKALDIR_API_SURFACE_ID
                ),
            )
        )
    return tuple(offered)


def catalogue_for(surfaces: Sequence[TargetSurface]) -> tuple[AttackCase, ...]:
    """The declared cases reachable through the offered surfaces, in catalogue order."""
    ids = {surface.surface_id for surface in surfaces}
    return tuple(case for case in ACTION_SPACE if case.surface_id in ids)


# --------------------------------------------------------------------------- #
# the plan                                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PlannedCase:
    """One planned case: the declared case plus what the plan assigned to it.

    ``decision`` is the **decision record**: the Jev layer's own answer for this case —
    the decision id, why it answered what it answered, and how long the call took. It is
    carried onto the run's result, so the run record answers *who decided this case, with
    which decision, for which reason, and at what latency* instead of only naming a planner.
    """

    case: AttackCase
    operation: str
    oracle_kind: str
    target_ref: str
    oracle_source: str = "deterministic"
    planned_by: str = "deterministic"
    decision_id: str = ""
    consistent: bool = True
    deviation: str = ""
    decision: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        payload = self.case.as_dict()
        payload.update(
            {
                "assigned_operation": self.operation,
                "assigned_oracle_kind": self.oracle_kind,
                "target_ref": self.target_ref,
                "oracle_source": self.oracle_source,
                "planned_by": self.planned_by,
                "decision_id": self.decision_id,
                "consistent": self.consistent,
                "deviation": self.deviation,
                "decision": dict(self.decision),
            }
        )
        return payload


@dataclass(frozen=True)
class SuitePlan:
    """The plan: what will run, who decided it, and what was left out."""

    planner: str
    advisor: str
    surfaces: tuple[TargetSurface, ...]
    cases: tuple[PlannedCase, ...]
    stop_reason: str
    budget: dict
    decision_ids: tuple[str, ...] = ()
    decisions: tuple[dict, ...] = ()
    live_jev: bool = False
    provider_modes: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    #: MVP/87 — **who answered** the planning questions. ``declared_space`` means the suite's
    #: own deterministic reader of the declared catalogue answered (no model was asked);
    #: ``llm_system_one_adapter`` means the configured provider answered through the façade.
    #: Without this field ``provider_mode=replay_fixture`` alone could be read as "a recorded
    #: model answer", which it never was.
    answer_source: str = ""
    #: Why this plan is not a live-Jev plan (empty when a live provider answered).
    live_jev_reason: str = ""

    def as_dict(self) -> dict:
        return {
            "suite_version": SUITE_VERSION,
            "planner": self.planner,
            "advisor": self.advisor,
            "answer_source": self.answer_source,
            "live_jev": self.live_jev,
            "live_jev_reason": self.live_jev_reason,
            "provider_modes": list(self.provider_modes),
            "stop_reason": self.stop_reason,
            "budget": self.budget,
            "offered_surfaces": [surface.surface_id for surface in self.surfaces],
            "case_count": len(self.cases),
            "decision_count": len(self.decisions),
            "decision_ids": list(self.decision_ids),
            "decisions": [dict(item) for item in self.decisions],
            "notes": list(self.notes),
            "cases": [case.as_dict() for case in self.cases],
        }


def _decision_reason(decision: Any, answer: Any, *, case_id: str) -> str:
    """The reason for one assigned case, built **only** from the decision's own typed fields.

    Nothing here is invented: the fields are read off the ``JevDecision``/answer the façade
    returned (its ``chosen``/``stop_reason``/``expected_defect_classes``/``batch_size``, its
    refusal or fallback text). A reader can re-derive the line from the same JSON.
    """
    parts: list[str] = []
    for attribute in ("advisory_note", "note", "reason", "rationale"):
        value = getattr(answer, attribute, None)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
            break
    else:
        refusal = str(getattr(decision, "refusal_reason", "") or "")
        if refusal:
            parts.append(refusal)
    chosen = str(getattr(answer, "chosen", "") or "")
    if chosen:
        parts.append(f"chosen={chosen}")
    stop = str(getattr(answer, "stop_reason", "") or "")
    if stop:
        parts.append(f"stop_reason={stop}")
    raw_classes = getattr(answer, "expected_defect_classes", None)
    classes = tuple(str(item) for item in raw_classes) if raw_classes else ()
    if classes:
        parts.append("expected_defect_classes=" + ",".join(classes))
    batch = int(getattr(answer, "batch_size", 0) or 0)
    if batch:
        parts.append(f"batch_size={batch}")
    fallback = str(getattr(decision, "fallback", "") or "")
    if fallback:
        parts.append(f"fallback={fallback}")
    if case_id:
        parts.append(f"decided={case_id}")
    return "; ".join(parts)


def _cause(exc: BaseException, limit: int = 120) -> str:
    """The exception's own message, minus its class name.

    A stop reason of ``decision_error:SystemOneContractError`` names the *class* of the
    refusal but not what could not be read; the cause is what makes it actionable (MVP/87:
    ``adv.case.priority: score 1.5 is not in the question's legend [0.0, 1.0, 2.0, 3.0]``).
    ``_ask_error_record`` still carries the untruncated message.
    """
    text = " ".join(str(exc).split())
    prefix = f"{type(exc).__name__}:"
    if text.startswith(prefix):
        text = text[len(prefix) :].strip()
    return text[:limit]


def _ask_error_record(
    error: str, *, ask: int, calls: int, source: str, call_wall_us: int
) -> dict:
    """One *unreadable sample* row: no decision was produced, and the row says so.

    ``ask``/``asks_per_case`` (MVP/87) make a retried case legible: three of these on the
    same case mean the provider answered nothing readable three times, not that the
    planner crashed.
    """
    return {
        "decision_id": "",
        "kind": "adversarial_test_planning",
        "case_id": "decision_error",
        "status": "error",
        "answer_source": str(source or ""),
        "error": str(error)[:300],
        "ask": int(ask),
        "asks_per_case": int(calls),
        "call_wall_us": int(call_wall_us),
    }


def _decision_record(
    decision: Any,
    *,
    case_id: str,
    call_wall_us: int = 0,
    order: int = 0,
    answer_source: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict:
    """The decision record for one case: id, kind, status, reason, fallback and timing.

    ``reason`` is the decision's own typed fields (:func:`_decision_reason`), never a
    paraphrase, and ``live_jev``/``provider_mode`` are copied verbatim so a reader can tell a
    live Jev call from the offline façade without guessing.

    ``answer_source`` (MVP/87) names **who answered**: ``declared_space`` (the suite's own
    deterministic reader of the declared catalogue) or ``llm_system_one_adapter`` (the
    configured provider through the façade). ``provider_mode=replay_fixture`` alone used to be
    the only clue, and it read like "a recorded model answered" — which it never was.

    Two timings are reported, and they are not the same number:

    * ``latency_ms`` — the façade's **own** measurement of the call. It is ``0`` when the
      façade did not time it (the in-process fixture path does not), and a zero here means
      "not measured by the façade", never "instant".
    * ``call_wall_us`` / ``call_wall_ms`` — the verifier's own wall-clock measurement around
      the ``decide()`` call. Microseconds first, because an in-process decision is genuinely
      sub-millisecond and reporting it as "0 ms" would read as "not measured".
    """
    answer = getattr(decision, "answer", None)
    asked = getattr(answer, "question_pack_id", "") or getattr(decision, "question_pack_id", "")
    record = {
        "decision_id": str(getattr(decision, "decision_id", "")),
        "kind": str(getattr(decision, "kind", "")),
        "case_id": case_id,
        "plan_order": int(order),
        "status": str(getattr(decision, "status", "")),
        "authority": str(getattr(decision, "authority", "advisory")),
        "certifies": bool(getattr(decision, "certifies", False)),
        "provider_mode": str(getattr(decision, "provider_mode", "")),
        "live_jev": bool(getattr(decision, "live_jev", False)),
        # MVP/87: who answered, so a `replay_fixture` label can never be read as a model
        "answer_source": str(answer_source or ""),
        "question_pack_id": str(asked),
        "question_pack_version": str(getattr(decision, "question_pack_version", "")),
        "confidence": float(getattr(decision, "confidence", 0.0) or 0.0),
        "reason": _decision_reason(decision, answer, case_id=case_id),
        "refusal_reason": str(getattr(decision, "refusal_reason", "") or ""),
        "fallback": str(getattr(decision, "fallback", "") or ""),
        "caution_level": int(getattr(decision, "caution_level", 0) or 0),
        "latency_ms": int(getattr(decision, "latency_ms", 0) or 0),
        "call_wall_us": int(call_wall_us),
        "call_wall_ms": round(int(call_wall_us) / 1000.0, 3),
        "planned_at": _now_iso(),
        "attempts": int(getattr(decision, "attempts", 1) or 1),
        "created_at": float(getattr(decision, "created_at", 0.0) or 0.0),
    }
    for key, value in dict(extra or {}).items():
        record[str(key)] = value
    return record



def _budget_dict(*, max_cases: int, slots: int, per_case_timeout_s: int, max_wall_s: int) -> dict:
    return {
        "max_cases": int(max_cases),
        "max_wall_s": int(max_wall_s),
        "per_case_timeout_s": int(per_case_timeout_s),
        "parallel_slots": int(slots),
        "allow_network": False,
    }


class DeclaredSpaceAdvisor:
    """Answers the Jev planning questions from the suite's declared action space.

    The façade owns the question set, the validation, the narrowing and the advisory envelope;
    this advisor owns exactly one thing: the reading of the declared catalogue. It walks the
    offered surfaces in the order Jev put them in the question and returns the first case that
    is not already in the planner's coverage ledger, so the choice is reproducible.
    """

    provider = "declared_space"
    provider_model = "adversarial-declared-space-1"
    provider_access_mode = "fixture"
    live_jev = False

    def __init__(
        self,
        *,
        surfaces: Sequence[TargetSurface],
        covered: Mapping[str, bool] | None = None,
    ) -> None:
        self.surfaces = tuple(surfaces)
        #: the coverage ledger, keyed by declared case id
        self.covered = dict(covered or {})
        self.calls: list[dict] = []

    def next_case(self) -> AttackCase | None:
        """The declared case this advisor would plan next, or ``None`` if all are covered."""
        for surface in self.surfaces:
            for intent in surface.seed_operations:
                case = next_case_for_pair((surface.surface_id, intent), self.covered)
                if case is not None:
                    return case
        return None

    def next_pair(self) -> tuple[str, str] | None:
        """The ``(surface_id, intent_class)`` of :meth:`next_case`, or ``None``."""
        case = self.next_case()
        return (case.surface_id, case.intent_class) if case else None

    def _choice_payload(self, question, chosen: str) -> dict:
        options = [str(option) for option in question.options]
        if chosen not in options:
            chosen = options[0]
        others = [option for option in options if option != chosen]
        share = 0.02 / len(others) if others else 0.0
        weights = {option: share for option in others}
        weights[chosen] = 1.0 - sum(weights.values())
        return {"choice": chosen, "probabilities": weights, "confidence": 0.75}

    def _noul_payload(self, question, value: float) -> dict:
        return {"value": float(value)}

    def _score_payload(self, question, value: float) -> dict:
        legend = [float(item) for item in tuple(question.legend or (0.0,))] or [0.0]
        chosen_value = min(max(legend), max(min(legend), float(value)))
        options = [f"{item:g}" for item in legend]
        chosen = f"{chosen_value:g}"
        others = [option for option in options if option != chosen]
        share = 0.02 / len(others) if others else 0.0
        weights = {option: share for option in others}
        weights[chosen] = 1.0 - sum(weights.values())
        return {
            "score": chosen_value,
            "legend": legend,
            "probabilities": weights,
            "confidence": 0.75,
        }

    def _payload_for(self, question) -> dict:
        case = self.next_case()
        surface_id = case.surface_id if case else "no_surface"
        if question.id == "adv.surface.pick":
            return self._choice_payload(question, surface_id)
        if question.id == "adv.case.intent_class":
            return self._choice_payload(question, case.intent_class if case else "boundary")
        if question.id == "adv.case.priority":
            return self._score_payload(question, float(case.priority) if case else 0.0)
        if question.id == "adv.case.covered_already":
            return self._noul_payload(question, 0.0 if case else 1.0)
        if question.id == "adv.oracle.deterministic":
            # every declared case carries a deterministic oracle; a case without one is not
            # declared at all, so this is a fact about the catalogue, not an opinion.
            return self._noul_payload(question, 1.0)
        if question.kind == "noul":
            return self._noul_payload(question, 0.5)
        if question.kind == "score":
            return self._score_payload(question, 0.0)
        options = [str(option) for option in question.options] or [""]
        return self._choice_payload(question, options[0])

    def answers_for(self, questions) -> dict[str, Any]:
        """The answer map for one composed question set — parsed by the substrate's own parser."""
        from .system_one import parse_answer

        built: dict[str, Any] = {}
        for question in questions.questions:
            payload = self._payload_for(question)
            built[question.id] = parse_answer(
                question_id=question.id, kind=question.kind, payload=payload
            )
        return built

    async def evaluate(self, *, state, questions, context):
        """The substrate's ``SystemOneEvaluator`` protocol, answered offline."""
        from .system_one import build_evaluation

        self.calls.append(
            {
                "questions": tuple(questions.ids),
                "state_fingerprint": getattr(state, "fingerprint", ""),
                "run_id": getattr(context, "run_id", ""),
                "chosen": self.next_pair(),
            }
        )
        answers = self.answers_for(questions)
        return build_evaluation(
            context=context,
            questions=questions,
            state=state,
            provider=self.provider,
            provider_model=self.provider_model,
            access_mode=self.provider_access_mode,
            answers=answers,
            live_jev=False,
            usage={"input_tokens": 0, "output_tokens": 0},
        )


def plan_suite(
    *,
    surfaces: Sequence[TargetSurface],
    slots: int = 6,
    per_case_timeout_s: int = 45,
    max_wall_s: int = 300,
    planner: str = "auto",
    risk_class_max: str = "R2",
    run_id: str = "",
    goal_id: str = "",
    live: bool = False,
    runtime: str | Path | None = None,
) -> SuitePlan:
    """Plan the suite. ``auto`` prefers the Jev façade and falls back — saying which it used.

    ``live`` (MVP/87) is the answer-source switch, and it defaults to ``False``:

    * ``live=False`` — the declared-space reader answers the façade's questions. Deterministic,
      offline, reproducible; the plan and every decision record label themselves
      ``answer_source="declared_space"`` so nobody reads it as a model.
    * ``live=True`` — the **configured provider** answers, through the façade, one decision per
      case. The records then carry ``provider_mode=llm_system_one_adapter`` with the measured
      latency, or — when no provider can be built — ``answer_source="declared_space"`` with the
      named blocker in ``live_jev_reason``. A fixture is never dressed up as a model in either
      direction: the field says which one answered.

    The library default stays offline (a test suite must not buy 19 network round trips); the
    ``skaldir adversarial plan|run`` verbs pass ``live=True`` unless ``--offline-planner`` is given.
    """
    catalogue = catalogue_for(surfaces)
    if not catalogue:
        raise PlannerUnavailable("no surface was offered, so no declared case is reachable")
    budget = _budget_dict(
        max_cases=len(catalogue),
        slots=max(1, int(slots)),
        per_case_timeout_s=int(per_case_timeout_s),
        max_wall_s=int(max_wall_s),
    )
    if planner in ("auto", "jev"):
        try:
            return _plan_with_jev(
                surfaces=surfaces,
                catalogue=catalogue,
                budget=budget,
                risk_class_max=risk_class_max,
                run_id=run_id,
                goal_id=goal_id,
                live=live,
                runtime=runtime,
            )
        except Exception as exc:  # noqa: BLE001 — a planner that cannot answer is not a verdict
            plan = _plan_deterministic(surfaces=surfaces, catalogue=catalogue, budget=budget)
            note = f"jev planner unavailable ({type(exc).__name__}: {exc})"
            return SuitePlan(
                planner=plan.planner,
                advisor=plan.advisor,
                surfaces=plan.surfaces,
                cases=plan.cases,
                stop_reason=plan.stop_reason,
                budget=plan.budget,
                decision_ids=plan.decision_ids,
                notes=(note,) + plan.notes,
            )
    return _plan_deterministic(surfaces=surfaces, catalogue=catalogue, budget=budget)


def _planning_provider(runtime: str | Path | None) -> tuple[Any, Any, str, str]:
    """``(flags, client, problem, provider_name)`` — the provider the planner may ask.

    The flags come from the runtime's own ``settings.yaml`` (:mod:`skaldir.jev.wiring`) and the
    client is resolved against the runtime's own ``config.yaml`` with the provider-config
    *overrides* removed from the environment (:func:`skaldir.jev.wiring.pinned_provider_env`) —
    a decision that is written into this runtime's store must be answered by the provider this
    runtime names, not by an inherited stub endpoint. A missing credential is a *named* blocker
    (``provider_unconfigured: <what to export>``) and never a silently scripted answer.

    The client is returned so the caller passes the **same** one to ``decide()``: letting the
    façade resolve a second client would re-read the ambient environment and undo the pin.
    """
    from .jev import wiring as jev_wiring

    flags = jev_wiring.armed_flags(runtime)
    name = str(getattr(flags, "provider", ""))
    if name == "disabled" or not flags.enabled:
        return flags, None, "system_one is disabled in this runtime's settings", name
    root = jev_wiring.runtime_dir(runtime)
    # The runtime's **own** resolution order (godsv.jev → godsv.providers.entries.jev →
    # godsv.llm) — the same one `skaldir jev status` and the ROUTE probe report. Reading the
    # flat godsv.llm block here instead would answer the plan with a different model and
    # budget than the record names (measured: deepseek-flash at 4096 answered the planning
    # pack with an empty completion while the record claimed the JEV block).
    from .jev.runtime import provider_resolution

    resolution = provider_resolution(
        env=jev_wiring.pinned_provider_env(), config_path=root / "config.yaml"
    )
    return flags, resolution["client"], str(resolution["problem"]), name


def open_pairs_for(
    catalogue: Sequence[AttackCase], covered: Mapping[str, bool] | None = None
) -> tuple[str, ...]:
    """The ``"<surface_id>/<intent_class>"`` pairs the catalogue still has an open case for.

    Host-side knowledge handed to the façade in the request (``remaining_pairs``), which is
    what lets the question the model answers offer *admissible* pairs. Before that field
    existed the model chose among all eight intent classes while the planner judged the
    choice against a ledger the request never carried — measured on this machine: three
    live ``--planner jev`` runs planned 1, 2 and 2 of 12 cases, two of them stopping on
    ``pair_already_covered`` because the model repeated an intent class it had already
    spent. Catalogue order is kept, so the narrowed options are reproducible.
    """
    pairs: list[str] = []
    for case in catalogue:
        pair = f"{case.surface_id}/{case.intent_class}"
        if pair in pairs:
            continue
        if next_case_for_pair((case.surface_id, case.intent_class), covered) is not None:
            pairs.append(pair)
    return tuple(pairs)


def _plan_with_jev(
    *,
    surfaces: Sequence[TargetSurface],
    catalogue: Sequence[AttackCase],
    budget: dict,
    risk_class_max: str,
    run_id: str,
    goal_id: str,
    live: bool = False,
    runtime: str | Path | None = None,
) -> SuitePlan:
    """Ask the Jev façade for one case at a time until it refuses or the budget is spent.

    Who answers is decided here and stated in the plan (``answer_source``):

    * ``live=True`` and a provider that resolves → the model answers, through the façade's
      ``adversarial_test_planning`` pack, one decision per case;
    * otherwise → the declared-space reader answers (``DeclaredSpaceAdvisor``), and
      ``live_jev_reason`` names why the model was not asked.

    On the live path the **model's** choice drives the loop: the pair it names is looked up in
    the declared catalogue and planned if it is declared and not yet covered, so the order is
    the model's decision. An undeclarable pair stops the plan with that reason instead of
    silently reordering the suite.
    """
    from .jev import contracts as JC
    from .jev.decide import decide

    run = run_id or f"adv-{uuid.uuid4().hex[:12]}"
    goal = goal_id or "adversarial-release-suite"
    planned: list[PlannedCase] = []
    covered: dict[str, bool] = {}
    previous: list[Any] = []
    decision_ids: list[str] = []
    records: list[dict] = []
    refused: list[dict] = []
    stop_reason = "budget_exhausted"
    advisor = DeclaredSpaceAdvisor(surfaces=surfaces)
    flags, provider_client, provider_problem, provider_name = (
        _planning_provider(runtime) if live else (None, None, "", "")
    )
    ask_the_model = bool(live and flags is not None and not provider_problem)
    answer_source = (
        "llm_system_one_adapter" if ask_the_model else "declared_space"
    )
    why_not_live = (
        ""
        if ask_the_model
        else (
            provider_problem
            or ("live planner was not requested" if not live else "no provider was resolved")
        )
    )
    # The honest ceiling, stated by the plan itself rather than by a reader of this file:
    # `live_jev` is true only for an authorized TypeSafe call, which no configuration on this
    # machine can produce, so the reason is never empty.
    live_jev_reason = (
        f"the configured provider ({provider_name}) answered through the LLM adapter — "
        "live_jev=true needs an authorized TypeSafe call (system_one.provider='typesafe' "
        "plus a credential), which is not configured here"
        if ask_the_model
        else f"no model answered: {why_not_live or 'no provider configured'}"
    )
    jev_bus = None
    if ask_the_model:
        # the same subscription the other runtime paths use: every planning decision lands in
        # the runtime's event store, so the plan is auditable from the timeline too
        from .jev import wiring as jev_wiring

        jev_bus = jev_wiring.bus_for(
            runtime, session_id=run, step=0, source="adversarial.plan"
        )
    jaev_surfaces = tuple(
        JC.TestSurface(
            surface_id=surface.surface_id,
            name=surface.surface_id,
            kind=surface.kind,
            risk=surface.risk_class,
            entry_hint=surface.entry_hint,
            seed_operations=surface.seed_operations,
        )
        for surface in surfaces
    )
    #: A live model's answer is a *sample*: one unreadable, refused or repeated pick costs a
    #: retry, never the whole plan (MVP/87 — measured: one bad sample turned a 12-case plan
    #: into a 2-case plan, and another escaped the façade as ``SystemOneContractError`` and
    #: handed ``--planner jev`` to the deterministic reader). Bounded, and only where a
    #: model answers: the declared-space reader is deterministic, so a second ask cannot
    #: change its answer.
    asks_per_case = ASKS_PER_CASE if ask_the_model else 1
    for _ in range(int(budget["max_cases"])):
        advisor.covered = dict(covered)
        chosen_case = advisor.next_case()
        if chosen_case is None:
            stop_reason = "catalogue_exhausted"
            break
        # the admissible answers, recomputed per case: an already-planned pair is never
        # offered again, so `pair_already_covered` cannot be the model's fault any more
        open_pairs = open_pairs_for(catalogue, covered)
        case: AttackCase | None = None
        first = None
        for ask in range(1, asks_per_case + 1):
            request = JC.JevAdversarialPlanRequest(
                run_id=run,
                goal_id=goal,
                surfaces=jaev_surfaces,
                budget=JC.AdversarialBudget(
                    max_cases=int(budget["max_cases"]),
                    max_wall_s=int(budget["max_wall_s"]),
                    per_case_timeout_s=int(budget["per_case_timeout_s"]),
                    parallel_slots=int(budget["parallel_slots"]),
                    allow_network=False,
                ),
                previous_results=tuple(previous),
                risk_class_max=risk_class_max,
                remaining_pairs=open_pairs,
            )
            call_started = time.time()
            try:
                if ask_the_model:
                    decision = decide(
                        request,
                        flags=flags,
                        llm=provider_client,
                        emit=jev_bus.emit if jev_bus is not None else None,
                    )
                else:
                    decision = decide(request, evaluator=advisor)
            except Exception as exc:  # noqa: BLE001 — an unreadable sample is a retry, not a verdict
                error = f"{type(exc).__name__}: {str(exc)[:300]}"
                if ask < asks_per_case:
                    refused.append(
                        _ask_error_record(
                            error,
                            ask=ask,
                            calls=asks_per_case,
                            source=answer_source,
                            call_wall_us=int((time.time() - call_started) * 1_000_000),
                        )
                    )
                    continue
                if not planned and not records:
                    # nothing was assigned at all, and the last sample was unreadable too:
                    # this is not a plan, and `plan_suite` must be able to say "the Jev
                    # planner is unavailable" instead of reporting an empty plan that looks
                    # like a decision
                    raise
                stop_reason = f"decision_error:{type(exc).__name__}:{_cause(exc)}"
                refused.append(
                    _ask_error_record(
                        error,
                        ask=ask,
                        calls=asks_per_case,
                        source=answer_source,
                        call_wall_us=int((time.time() - call_started) * 1_000_000),
                    )
                )
                break
            call_wall_us = int((time.time() - call_started) * 1_000_000)
            decision_ids.append(str(decision.decision_id))
            if jev_bus is not None:
                jev_bus.decision(
                    decision.model_dump(mode="json"),
                    source="adversarial.plan",
                    extra={"case_id": str(chosen_case.case_id)},
                )
            answer = decision.answer
            plan_cases = tuple(getattr(answer, "plan", ()) or ())
            # "narrow" is still a plan (a reduced batch); only a refusal carries no case
            if str(getattr(answer, "chosen", "refuse")) not in ("plan", "narrow") or not plan_cases:
                named = str(getattr(answer, "stop_reason", "") or "refused")
                refusal = str(getattr(decision, "refusal_reason", "") or "")
                status = str(getattr(decision, "status", "") or "")
                named = named if named not in ("", "refused") else status or "refused"
                refusal_record = _decision_record(
                    decision,
                    case_id=str(getattr(answer, "stop_reason", "")) or "refused",
                    call_wall_us=call_wall_us,
                    order=len(records) + len(refused),
                    answer_source=answer_source,
                    extra={"ask": ask, "asks_per_case": asks_per_case},
                )
                if ask < asks_per_case:
                    # the model refused *this sample* — ask it again before treating the
                    # refusal as the plan's outcome. The stop reason is only written on the
                    # last ask: a retried sample that succeeds must not leave a stale
                    # "refused" reason on a plan that then finished its catalogue.
                    refused.append(refusal_record)
                    continue
                if not records:
                    # An empty plan must say *why* it is empty (MVP/87): the façade's own
                    # stopped reason first, then the refusal the decision carries.
                    # "no_advisory" alone reads like a planner quirk; the reader needs
                    # "…: provider_disabled" or "…: provider_unconfigured: <what to export>".
                    stop_reason = f"no_cases:{named}" + (f":{refusal}" if refusal else "")
                else:
                    stop_reason = named
                refused.append(refusal_record)
                break
            first = plan_cases[0]
            pair = (str(first.surface_id), str(first.intent_class))
            case = next_case_for_pair(pair, covered)
            if case is None:
                # Two different facts, two different reasons (MVP/87): the model named a pair
                # the catalogue does not declare, or one whose every case is already in the
                # coverage ledger (it repeated itself). Collapsing them into one string would
                # hide which of the two happened. Repeating is a sample-level miss: the same
                # case is asked again, bounded, before it becomes the plan's stop reason.
                named = (
                    f"pair_already_covered:{pair[0]}/{pair[1]}"
                    if CASE_BY_PAIR.get(pair)
                    else f"plan_pair_not_declarable:{pair[0]}/{pair[1]}"
                )
                refused.append(
                    _decision_record(
                        decision,
                        case_id=str(first.case_id),
                        call_wall_us=call_wall_us,
                        order=len(records) + len(refused),
                        answer_source=answer_source,
                        extra={"ask": ask, "asks_per_case": asks_per_case, "named": named},
                    )
                )
                if ask < asks_per_case:
                    continue
                stop_reason = named
                case = None
                break
            if not ask_the_model and case.case_id != chosen_case.case_id:
                # the offline reader answered something other than what it declares — that is
                # not a plan anyone may execute, and it is reported as such instead of being run
                stop_reason = (
                    f"plan_pair_not_declarable:{first.surface_id}/{first.intent_class}"
                )
                case = None
                break
            break
        if not isinstance(case, AttackCase):
            # every ask of this case produced no assignable plan: the named reason stands
            break
        consistent = (
            str(first.operation) == case.operation
            and str(first.success_oracle.kind) == case.oracle_kind
        )
        deviation = ""
        if not consistent:
            assigned = f"{first.operation}/{first.success_oracle.kind}"
            declared = f"{case.operation}/{case.oracle_kind}"
            deviation = (
                f"the plan assigned {assigned} to a case the declared action space "
                f"drives as {declared}"
            )
        record = _decision_record(
            decision,
            case_id=case.case_id,
            call_wall_us=call_wall_us,
            order=len(records),
            answer_source=answer_source,
            extra={
                "declared_order_case": str(chosen_case.case_id),
                "followed_the_model": case.case_id != chosen_case.case_id,
            }
            if ask_the_model
            else {"declared_order_case": str(chosen_case.case_id)},
        )
        records.append(record)
        planned.append(
            PlannedCase(
                case=case,
                operation=str(first.operation),
                oracle_kind=str(first.success_oracle.kind),
                target_ref=str(first.target_ref),
                oracle_source=str(first.oracle_source),
                planned_by="jev",
                decision_id=str(decision.decision_id),
                consistent=consistent,
                deviation=deviation,
                decision=record,
            )
        )
        covered[case.case_id] = True
        previous.append(
            JC.CaseOutcome(
                # the *declared* case, not the model's own invented id (MVP/87): the ledger
                # the next ask receives has to be legible, and three rows reading
                # `adv-001` (the model's counter) said nothing about what had been planned
                case_id=str(case.case_id),
                surface_id=str(case.surface_id),
                intent_class=str(case.intent_class),
                outcome="unknown",
                defect_class="",
            )
        )
    if jev_bus is not None:
        jev_bus.close()
    return SuitePlan(
        planner="jev",
        advisor="configured_provider" if ask_the_model else "declared_space",
        surfaces=tuple(surfaces),
        cases=tuple(planned),
        stop_reason=stop_reason,
        budget=budget,
        decision_ids=tuple(decision_ids),
        decisions=tuple(records + refused),
        live_jev=any(bool(item.get("live_jev")) for item in records + refused),
        provider_modes=tuple(
            sorted({str(item.get("provider_mode", "")) for item in records + refused})
        ),
        answer_source=answer_source,
        live_jev_reason=live_jev_reason,
        notes=(
            "the coverage ledger records a planned case as outcome 'unknown' — nothing has "
            "measured it yet, and Jev issues no verdict",
            (
                f"the answer source is {answer_source}: "
                + (
                    f"the configured provider ({provider_name}) answered the planning "
                    "questions through the Jev façade — a real model, one decision per case"
                    if ask_the_model
                    else "the suite's own declared-space reader answered; no model was asked "
                    f"({why_not_live or 'no provider configured'})"
                )
            ),
            *(
                ()
                if records
                else (
                    "the façade answered no case it could assign: the decision records below "
                    "are the refusals, and each one says so",
                )
            ),
        ),
    )


def _plan_deterministic(
    *,
    surfaces: Sequence[TargetSurface],
    catalogue: Sequence[AttackCase],
    budget: dict,
) -> SuitePlan:
    """The fallback planner: the same catalogue, the same order, no façade involved."""
    planned: list[PlannedCase] = []
    for case in catalogue:
        if len(planned) >= int(budget["max_cases"]):
            break
        surface = next(
            (item for item in surfaces if item.surface_id == case.surface_id), None
        )
        if surface is None:
            continue
        planned.append(
            PlannedCase(
                case=case,
                operation=case.operation,
                oracle_kind=case.oracle_kind,
                target_ref=f"{case.surface_id}#{surface.entry_hint}",
                oracle_source="deterministic",
                planned_by="deterministic",
            )
        )
    return SuitePlan(
        planner="deterministic",
        advisor="none",
        surfaces=tuple(surfaces),
        cases=tuple(planned),
        stop_reason="catalogue_exhausted",
        budget=budget,
        notes=("the Jev façade was not asked: the deterministic planner ordered the catalogue",),
    )


# --------------------------------------------------------------------------- #
# the case result and the verdict                                               #
# --------------------------------------------------------------------------- #


@dataclass
class CaseResult:
    """One case, measured. ``unknown`` is the honest outcome of an unverifiable case.

    ``decision`` is the decision record of the call that planned this case (empty when the
    deterministic fallback planned it): the decision id, the reason, and the latency, so the
    run record proves *who decided* instead of only asserting it.
    """

    case_id: str
    surface_id: str
    intent_class: str
    defect_class: str
    operation: str
    oracle: str
    oracle_kind: str
    target_ref: str
    outcome: str
    reason: str
    observed_by: str
    observed: dict = field(default_factory=dict)
    defects: tuple[dict, ...] = ()
    verified: bool = False
    duration_ms: int = 0
    planned_by: str = "deterministic"
    decision: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        reported = _trim_observation(self.observed)
        return {
            "case_id": self.case_id,
            "surface_id": self.surface_id,
            "intent_class": self.intent_class,
            "defect_class": self.defect_class,
            "operation": self.operation,
            "oracle": self.oracle,
            "oracle_kind": self.oracle_kind,
            "target_ref": self.target_ref,
            "outcome": self.outcome,
            "reason": self.reason,
            "observed_by": self.observed_by,
            "verified": self.verified,
            "planned_by": self.planned_by,
            "decision": dict(self.decision),
            "duration_ms": self.duration_ms,
            "defects": [_trim_observation(dict(item)) for item in self.defects],
            "observed": reported,
            "observed_sha256": _sha256_json(reported),
        }


@dataclass
class SuiteReport:
    """The suite's answer: a verdict, the defects behind it, and the evidence per case."""

    suite_version: str
    target: dict
    plan: dict
    verdict: str
    results: tuple[CaseResult, ...]
    started_at: str
    finished_at: str
    duration_ms: int
    run_id: str
    notes: tuple[str, ...] = ()
    #: set when the run never really started (a declared driver is missing): the structured
    #: refusal, published so a consumer reads *why* instead of inferring it from a count
    blocked: dict = field(default_factory=dict)

    @property
    def defects(self) -> tuple[dict, ...]:
        return tuple(defect for result in self.results for defect in result.defects)

    @property
    def counts(self) -> dict:
        return {
            outcome: sum(1 for result in self.results if result.outcome == outcome)
            for outcome in OUTCOMES
        }

    @property
    def blocks_release(self) -> bool:
        return self.verdict != "PASS"

    def as_dict(self) -> dict:
        return {
            "suite_version": self.suite_version,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "verdict": self.verdict,
            "blocks_release": self.blocks_release,
            "target": self.target,
            "plan": self.plan,
            "counts": self.counts,
            "case_count": len(self.results),
            "defects": [_trim_observation(dict(item)) for item in self.defects],
            "defect_classes": sorted({str(item["defect_class"]) for item in self.defects}),
            "cases": [result.as_dict() for result in self.results],
            "notes": list(self.notes),
            "blocked": dict(self.blocked),
            "evidence_sha256": self.evidence_sha256(),
        }

    def evidence_sha256(self) -> str:
        return _sha256_json(
            [
                {
                    "case_id": result.case_id,
                    "outcome": result.outcome,
                    "defects": [_trim_observation(dict(item)) for item in result.defects],
                    "observed": _trim_observation(result.observed),
                }
                for result in self.results
            ]
        )


def verdict_for(results: Sequence[CaseResult]) -> str:
    """``FAIL`` if any case failed; else ``UNKNOWN`` if any case is unknown; else ``PASS``.

    A run with no measurable case is ``UNKNOWN``: no cases, no certification.
    """
    if not results:
        return "UNKNOWN"
    outcomes = {result.outcome for result in results}
    if "fail" in outcomes:
        return "FAIL"
    if "unknown" in outcomes:
        return "UNKNOWN"
    return "PASS" if outcomes == {"pass"} else "UNKNOWN"


# --------------------------------------------------------------------------- #
# the canary app: a real local target with real defect classes                  #
# --------------------------------------------------------------------------- #

CANARY_MODES = ("broken", "fixed")
CANARY_NOTE_LIMIT = 2000
CANARY_NAME_LIMIT = 80
CANARY_SESSION_TTL_S = 900.0

#: The canary's **declared page contract** — the structure and texts a user flow depends on.
#: ``fixed`` ships exactly this; ``broken`` violates all three rendering classes, which is what
#: makes the rendering cases (broken layout, missing element, wrong text) measurable instead of
#: decorative.
CANARY_CONTENT_SELECTOR = "#content"
CANARY_DECLARED_ANCHORS: tuple[str, ...] = (
    "#form", "#name", "#note", "#quick", "#continue", "#status", "#list", "#hint",
)
CANARY_DECLARED_OWNED: tuple[str, ...] = ("#form", "#status", "#list")
CANARY_DECLARED_PRIMARY_LABEL = "Quick add"
CANARY_DECLARED_HINT = "Type a name, then choose Quick add."
CANARY_BROKEN_PRIMARY_LABEL = "Add"

_COMMON_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Adversarial canary</title></head>
<body>
<h1>Adversarial canary ({mode})</h1>
{content_open}
<form id="form" method="post" action="/submit">
  <input id="name" name="name" aria-label="name">
  <textarea id="note" name="note" aria-label="note"></textarea>
  <button id="quick" type="button">{primary_label}</button>
  <button id="continue" type="button">Save and continue</button>
</form>
{hint}
<p id="status" role="status"></p>
<p id="last-action"></p>
<ul id="list">{entries}</ul>
{content_close}
<p><a id="about" href="/about">About</a></p>
<script>{script}</script>
</body></html>
"""

_ABOUT_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Adversarial canary — about</title></head>
<body><h1>About</h1><p>Nothing to see here.</p>
<p><a id="back" href="/">Back</a></p></body></html>
"""

_BROKEN_SCRIPT = """
const statusEl = document.getElementById('status');
const nameEl = document.getElementById('name');
const noteEl = document.getElementById('note');
const form = document.getElementById('form');
const slow = new URLSearchParams(window.location.search).get('slow');
const suffix = slow ? ('?slow=' + slow) : '';
function claim(text) {
  statusEl.textContent = text;
  sessionStorage.setItem('canary.last_action', text);
}
document.getElementById('quick').addEventListener('click', () => {
  claim('Saved');
  fetch('/submit' + suffix, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: nameEl.value, note: noteEl.value})
  }).then(r => r.json()).then(d => { if (!d.ok) { statusEl.textContent = 'Failed: ' + d.error; } })
    .catch(() => {});
});
document.getElementById('continue').addEventListener('click', () => {
  claim('Saved');
  form.submit();
});
window.addEventListener('load', () => {
  const last = sessionStorage.getItem('canary.last_action');
  if (last) { document.getElementById('last-action').textContent = 'Last action: ' + last; }
});
"""

_FIXED_SCRIPT = """
const statusEl = document.getElementById('status');
const nameEl = document.getElementById('name');
const noteEl = document.getElementById('note');
const form = document.getElementById('form');
const quick = document.getElementById('quick');
const slow = new URLSearchParams(window.location.search).get('slow');
const suffix = slow ? ('?slow=' + slow) : '';
let inFlight = false;
function confirmSuccess(text) {
  statusEl.textContent = text;
  sessionStorage.setItem('canary.last_action', text);
}
quick.addEventListener('click', async () => {
  if (inFlight) { return; }
  inFlight = true;
  quick.disabled = true;
  const key = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Math.random());
  try {
    const response = await fetch('/submit' + suffix, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Idempotency-Key': key},
      body: JSON.stringify({name: nameEl.value, note: noteEl.value})
    });
    let payload = {};
    try { payload = await response.json(); } catch (err) { payload = {}; }
    if (response.ok && payload.ok) { confirmSuccess('Saved'); }
    else { statusEl.textContent = 'Failed: ' + (payload.error || response.status); }
  } catch (err) {
    statusEl.textContent = 'Failed: network';
  } finally {
    inFlight = false;
    quick.disabled = false;
  }
});
document.getElementById('continue').addEventListener('click', () => { form.submit(); });
window.addEventListener('load', () => {
  const last = sessionStorage.getItem('canary.last_action');
  if (last) { document.getElementById('last-action').textContent = 'Last action: ' + last; }
});
"""


def _escape_html(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


class CanaryApp:
    """A real local web app with a ``broken`` and a ``fixed`` mode.

    The two modes differ in exactly the behaviours the catalogue attacks: server-side
    validation and escaping, idempotency of a repeated submit, POST-redirect-GET on the native
    form, and whether the client may claim success before the server confirmed it. Session and
    session expiry are mode-independent, so an expired session is refused by both servers — the
    client's claim is what differs.
    """

    def __init__(self, mode: str = "broken") -> None:
        if mode not in CANARY_MODES:
            raise ValueError(f"unknown canary mode {mode!r}; use one of {CANARY_MODES}")
        self.mode = mode
        # reentrant: a snapshot nests session() and record() while it holds the lock,
        # and a plain Lock deadlocks the whole app on the first POST (measured)
        self._lock = threading.RLock()
        self._sessions: dict[str, dict] = {}
        self._submits = 0
        self._refusals = 0
        self._seen_keys: dict[str, dict] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle ----------------------------------------------------------- #

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("the canary app is not running")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> str:
        app = self
        handler = type("CanaryHandler", (_CanaryHandler,), {"app": app})
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> CanaryApp:
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # -- state --------------------------------------------------------------- #

    def session(self, token: str) -> dict:
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                session = {
                    "token": token,
                    "expires_at": time.time() + CANARY_SESSION_TTL_S,
                    "records": [],
                }
                self._sessions[token] = session
            return session

    def snapshot(self, token: str) -> dict:
        session = self.session(token)
        with self._lock:
            records = [dict(item) for item in session["records"]]
            return {
                "mode": self.mode,
                "expired": session["expires_at"] <= time.time(),
                "records": records,
                "record_count": len(records),
                "submits": self._submits,
                "refusals": self._refusals,
            }

    def expire(self, token: str) -> None:
        with self._lock:
            self._sessions.setdefault(
                token,
                {"token": token, "expires_at": time.time() + CANARY_SESSION_TTL_S, "records": []},
            )["expires_at"] = time.time() - 1.0

    def _record(self, token: str, name: str, note: str, key: str) -> dict:
        with self._lock:
            self._submits += 1
            if self.mode == "fixed" and key and key in self._seen_keys:
                return dict(self._seen_keys[key])
            record = {"name": str(name), "note": str(note), "at": time.time()}
            self.session(token)["records"].append(record)
            if self.mode == "fixed" and key:
                self._seen_keys[key] = dict(record)
            return dict(record)

    def _refuse(self) -> None:
        with self._lock:
            self._refusals += 1

    def page(self, token: str) -> str:
        session = self.session(token)
        with self._lock:
            records = [dict(item) for item in session["records"]]
        rows: list[str] = []
        for record in records:
            if self.mode == "fixed":
                rows.append(
                    "<li class=\"entry\">{}: {}</li>".format(
                        _escape_html(record["name"]), _escape_html(record["note"])
                    )
                )
            else:
                rows.append(
                    f'<li class="entry">{record["name"]}: {record["note"]}</li>'
                )
        script = _BROKEN_SCRIPT if self.mode == "broken" else _FIXED_SCRIPT
        fixed = self.mode == "fixed"
        return (
            _COMMON_HTML.replace("{mode}", self.mode)
            .replace("{content_open}", '<main id="content">' if fixed else "")
            .replace("{content_close}", "</main>" if fixed else "")
            .replace(
                "{hint}",
                f'<p id="hint">{CANARY_DECLARED_HINT}</p>' if fixed else "",
            )
            .replace(
                "{primary_label}",
                CANARY_DECLARED_PRIMARY_LABEL if fixed else CANARY_BROKEN_PRIMARY_LABEL,
            )
            .replace("{entries}", "".join(rows))
            .replace("{script}", script)
        )

    def validation_error(self, name: str, note: str) -> str:
        """The server-side validation this mode applies. ``""`` means accepted."""
        if self.mode != "fixed":
            return ""
        if not str(name).strip():
            return "name is required"
        if len(str(name)) > CANARY_NAME_LIMIT:
            return f"name is longer than {CANARY_NAME_LIMIT} characters"
        if len(str(note)) > CANARY_NOTE_LIMIT:
            return f"note is longer than {CANARY_NOTE_LIMIT} characters"
        return ""


class _CanaryHandler(BaseHTTPRequestHandler):
    """The canary's HTTP surface. ``app`` is bound on a per-instance subclass."""

    server_version = "skaladversarial-canary/1"
    protocol_version = "HTTP/1.1"
    app: CanaryApp

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — stdlib signature
        return

    # -- helpers ------------------------------------------------------------- #

    def _token(self, create: bool = True) -> str:
        cookies = self.headers.get("Cookie", "") or ""
        for chunk in cookies.split(";"):
            key, _, value = chunk.strip().partition("=")
            if key == "canary_session" and value:
                return urllib.parse.unquote(value)
        if not create:
            return ""
        token = uuid.uuid4().hex
        self._pending_cookie = token
        return token

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        token = getattr(self, "_pending_cookie", "")
        if token:
            self.send_header("Set-Cookie", f"canary_session={token}; Path=/; SameSite=Lax")
        for key, value in dict(extra or {}).items():
            self.send_header(str(key), str(value))
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # the client went away (a refresh mid-action aborts the fetch). The write
                # already happened server-side; an aborted response is not a target error.
                self.close_connection = True

    def _send_json(self, status: int, payload: dict, extra: dict | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send(status, body, "application/json", extra=extra)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        return self.rfile.read(length) if length > 0 else b""

    # -- routes -------------------------------------------------------------- #

    def do_GET(self) -> None:  # noqa: N802 — stdlib signature
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            token = self._token()
            body = self.app.page(token).encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
            return
        if path == "/about":
            self._send(200, _ABOUT_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/state.json":
            token = self._token()
            self._send_json(200, self.app.snapshot(token))
            return
        if path == "/expire":
            token = self._token()
            self.app.expire(token)
            self._send_json(200, {"ok": True, "expired": True})
            return
        if path == "/healthz":
            self._send_json(200, {"ok": True, "mode": self.app.mode})
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 — stdlib signature
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        slow = urllib.parse.parse_qs(parsed.query).get("slow") or [""]
        try:
            slow_ms = max(0, min(5000, int(slow[0])))
        except (TypeError, ValueError):
            slow_ms = 0
        raw = self._read_body()
        if slow_ms:
            # a *server-side* delay: the request is genuinely in flight while the driver looks
            time.sleep(slow_ms / 1000.0)
        if path != "/submit":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        token = self._token()
        session = self.app.session(token)
        with self.app._lock:  # noqa: SLF001 — the handler is part of the app
            expired = session["expires_at"] <= time.time()
        if expired:
            self.app._refuse()  # noqa: SLF001
            self._send_json(401, {"ok": False, "error": "session expired"})
            return
        content_type = str(self.headers.get("Content-Type", ""))
        name = ""
        note = ""
        if "json" in content_type:
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.app._refuse()  # noqa: SLF001
                self._send_json(422, {"ok": False, "error": "malformed body"})
                return
            if not isinstance(payload, dict):
                self.app._refuse()  # noqa: SLF001
                self._send_json(422, {"ok": False, "error": "malformed body"})
                return
            name = str(payload.get("name", ""))
            note = str(payload.get("note", ""))
        else:
            fields = urllib.parse.parse_qs(raw.decode("utf-8", errors="replace"))
            name = str((fields.get("name") or [""])[0])
            note = str((fields.get("note") or [""])[0])
        error = self.app.validation_error(name, note)
        if error:
            self.app._refuse()  # noqa: SLF001
            self._send_json(422, {"ok": False, "error": error})
            return
        record = self.app._record(  # noqa: SLF001
            token, name, note, str(self.headers.get("X-Idempotency-Key", ""))
        )
        if "json" in content_type:
            self._send_json(200, {"ok": True, "record": record})
            return
        # the native form path: fixed mode answers 303 (POST-redirect-GET), broken mode 200
        if self.app.mode == "fixed":
            self._send(303, b"", "text/html; charset=utf-8", extra={"Location": "/?ok=1"})
            return
        self._send(200, self.app.page(token).encode("utf-8"), "text/html; charset=utf-8")


def start_canary(mode: str = "broken") -> CanaryApp:
    """Start a canary app on a free loopback port and return it (``.url`` is live)."""
    app = CanaryApp(mode)
    app.start()
    return app


# --------------------------------------------------------------------------- #
# observations: what each driver is allowed to do                               #
# --------------------------------------------------------------------------- #


@dataclass
class PageContext:
    """Everything a driver may touch: the page, the session-bound request, the target URL."""

    page: Any
    request: Any
    base_url: str
    per_case_timeout_s: int = 45


def _ui_state(ctx: PageContext) -> dict:
    """Read the canary's own state through the *same* session (cookies are shared)."""
    response = ctx.request.get(f"{ctx.base_url}/state.json", timeout=10_000)
    if response.status != 200:
        raise Unverifiable(f"the state endpoint answered {response.status}")
    return response.json()


def _fill(page: Any, name: str, note: str) -> None:
    page.fill("#name", name)
    page.fill("#note", note)


def _status_text(page: Any) -> str:
    try:
        return str(page.eval_on_selector("#status", "el => el.textContent") or "")
    except Exception:  # noqa: BLE001 — a page mid-navigation is not a failed case
        return ""


def _last_action(page: Any) -> str:
    try:
        return str(page.eval_on_selector("#last-action", "el => el.textContent") or "")
    except Exception:  # noqa: BLE001 — see above
        return ""


def _settle(page: Any, ms: int = 400) -> None:
    page.wait_for_timeout(ms)


def _claim_marker(page: Any) -> str:
    """What the surface last told the user about the outcome (its own persisted claim).

    The canary keeps that line in ``sessionStorage``, so it survives a reload — which is exactly
    how a claim that was made for an unfinished write becomes visible afterwards. A surface
    without the key answers with an empty string (no claim), never with ``None``.
    """
    try:
        value = page.evaluate("() => sessionStorage.getItem('canary.last_action') || ''")
    except Exception:  # noqa: BLE001 — a page that cannot be read cannot be judged
        return ""
    return str(value or "")


def _is_enabled(page: Any, selector: str) -> bool:
    """Whether a control is actionable right now (a *user* cannot click a disabled button)."""
    try:
        return bool(page.is_enabled(selector, timeout=500))
    except Exception:  # noqa: BLE001
        return False


def _open(page: Any, base_url: str, slow_ms: int = 0) -> None:
    """Open the canary form; ``slow_ms`` asks the target to answer this case's write slowly."""
    suffix = f"?slow={int(slow_ms)}" if slow_ms else ""
    page.goto(f"{base_url}/{suffix}", wait_until="load")


def drive_empty_input(ctx: PageContext, case: AttackCase) -> dict:
    """Submit with an empty name; the empty field must be refused everywhere."""
    _open(ctx.page, ctx.base_url)
    _fill(ctx.page, "", "a note typed with no name")
    ctx.page.click("#quick")
    _settle(ctx.page, 800)
    return {
        "status_text": _status_text(ctx.page),
        "claim_marker": _claim_marker(ctx.page),
        "server": _ui_state(ctx),
        "driver": "empty_input",
    }


def drive_hostile_input(ctx: PageContext, case: AttackCase) -> dict:
    """Store markup that would run if the value were rendered unescaped."""
    payload = '<img id="canary-xss" src="/nope.png" onerror="window.__canaryXss=1">'
    _open(ctx.page, ctx.base_url)
    _fill(ctx.page, "hostile", payload)
    ctx.page.click("#quick")
    _settle(ctx.page, 700)
    # the value is only rendered back when the list is drawn again
    ctx.page.reload(wait_until="load")
    _settle(ctx.page, 300)
    return {
        "status_text": _status_text(ctx.page),
        "injected_elements": int(
            ctx.page.eval_on_selector_all("#list img", "els => els.length") or 0
        ),
        "handler_ran": bool(ctx.page.evaluate("() => window.__canaryXss === 1")),
        "list_text": str(ctx.page.eval_on_selector("#list", "el => el.textContent") or ""),
        "server": _ui_state(ctx),
        "driver": "hostile_input",
    }


def drive_oversized_input(ctx: PageContext, case: AttackCase) -> dict:
    """Submit far beyond the documented limit."""
    _open(ctx.page, ctx.base_url)
    _fill(ctx.page, "oversized", "x" * 200_000)
    ctx.page.click("#quick")
    _settle(ctx.page, 1200)
    return {
        "status_text": _status_text(ctx.page),
        "claim_marker": _claim_marker(ctx.page),
        "server": _ui_state(ctx),
        "driver": "oversized_input",
    }


def drive_double_submit(ctx: PageContext, case: AttackCase) -> dict:
    """Two clicks while the first request is still on the wire.

    The second click is only attempted when the control is *actionable*: a user cannot click a
    disabled button, and driving one anyway would manufacture a duplicate that no user could
    cause (measured: doing so made the fixed target look broken).
    """
    _open(ctx.page, ctx.base_url, case.slow_ms)
    _fill(ctx.page, "twice", "double")
    ctx.page.click("#quick")
    ctx.page.wait_for_timeout(250)
    dispatched = _is_enabled(ctx.page, "#quick")
    if dispatched:
        try:
            ctx.page.click("#quick", timeout=700)
        except Exception:  # noqa: BLE001 — the button may disable itself mid-click
            pass
    ctx.page.wait_for_timeout(int(case.slow_ms) + 900)
    return {
        "status_text": _status_text(ctx.page),
        "claim_marker": _claim_marker(ctx.page),
        "second_click_dispatched": dispatched,
        "server": _ui_state(ctx),
        "driver": "double_submit",
    }


def drive_slow_network(ctx: PageContext, case: AttackCase) -> dict:
    """Three clicks under a 2.5s response delay — only while the control is actionable."""
    _open(ctx.page, ctx.base_url, case.slow_ms)
    _fill(ctx.page, "slow", "slow network")
    ctx.page.click("#quick")
    ctx.page.wait_for_timeout(250)
    clicks = 1
    for _ in range(2):
        if not _is_enabled(ctx.page, "#quick"):
            break
        try:
            ctx.page.click("#quick", timeout=700)
            clicks += 1
        except Exception:  # noqa: BLE001 — see above
            break
        ctx.page.wait_for_timeout(300)
    ctx.page.wait_for_timeout(int(case.slow_ms) + 900)
    return {
        "status_text": _status_text(ctx.page),
        "claim_marker": _claim_marker(ctx.page),
        "clicks_dispatched": clicks,
        "server": _ui_state(ctx),
        "driver": "slow_network",
    }


def drive_refresh_mid_action(ctx: PageContext, case: AttackCase) -> dict:
    """Reload while the submit request is still held on the wire."""
    _open(ctx.page, ctx.base_url, case.slow_ms)
    _fill(ctx.page, "reload", "refresh mid action")
    ctx.page.click("#quick")
    ctx.page.wait_for_timeout(250)
    # both readings are taken while the write is still on the wire: the claim the surface made
    # and the entries the server had stored at that moment
    claim_while_in_flight = _claim_marker(ctx.page)
    server_before = _ui_state(ctx)
    ctx.page.reload(wait_until="load")
    _settle(ctx.page, 300)
    return {
        "status_text": _status_text(ctx.page),
        "last_action": _last_action(ctx.page),
        "claim_marker": _claim_marker(ctx.page) or claim_while_in_flight,
        "server_while_in_flight": server_before,
        "server": _ui_state(ctx),
        "driver": "refresh_mid_action",
    }


def drive_expired_session(ctx: PageContext, case: AttackCase) -> dict:
    """Expire the session, then submit."""
    _open(ctx.page, ctx.base_url)
    ctx.page.evaluate("() => fetch('/expire').then(r => r.status)")
    _settle(ctx.page, 300)
    _fill(ctx.page, "expired", "after the session expired")
    ctx.page.click("#quick")
    claim_after_click = _claim_marker(ctx.page)
    _settle(ctx.page, 900)
    return {
        "status_text": _status_text(ctx.page),
        "claim_marker": _claim_marker(ctx.page) or claim_after_click,
        "server": _ui_state(ctx),
        "driver": "expired_session",
    }


def drive_history_replay(ctx: PageContext, case: AttackCase) -> dict:
    """Submit natively, then go back and forward again (the POST must not be resent)."""
    _open(ctx.page, ctx.base_url)
    _fill(ctx.page, "history", "history replay")
    ctx.page.click("#continue")
    ctx.page.wait_for_load_state("load")
    _settle(ctx.page, 500)
    after_submit = _ui_state(ctx)
    # 1) reload the POST result: without POST-redirect-GET the browser resends the write
    #    (Chromium asks first; the context accepts dialogs, so a real user's "Continue" is
    #    what is measured)
    try:
        ctx.page.reload(wait_until="load")
    except Exception as exc:  # noqa: BLE001 — the browser's own refusal is data, not an error
        after_reload_note = f"{type(exc).__name__}"
    else:
        after_reload_note = ""
    _settle(ctx.page, 500)
    after_reload = _ui_state(ctx)
    # 2) then walk the history back and forth over the same result
    notes: list[str] = []
    for name, action in (("back", ctx.page.go_back), ("forward", ctx.page.go_forward)):
        try:
            action(wait_until="load")
        except Exception as exc:  # noqa: BLE001 — a browser without the cached result says so
            notes.append(f"{name}:{type(exc).__name__}")
        else:
            try:
                ctx.page.wait_for_load_state("load")
            except Exception:  # noqa: BLE001 — a page mid-navigation is not a failed case
                pass
            _settle(ctx.page, 300)
    return {
        "after_submit": after_submit,
        "after_reload": after_reload,
        "reload_note": after_reload_note,
        "history_notes": notes,
        "server": _ui_state(ctx),
        "status_text": _status_text(ctx.page),
        "driver": "history_replay",
    }


def drive_happy_path(ctx: PageContext, case: AttackCase) -> dict:
    """The ordinary flow: one valid entry, confirmed."""
    _open(ctx.page, ctx.base_url)
    _fill(ctx.page, "control", "the ordinary flow")
    ctx.page.click("#quick")
    _settle(ctx.page, 900)
    return {
        "status_text": _status_text(ctx.page),
        "claim_marker": _claim_marker(ctx.page),
        "server": _ui_state(ctx),
        "driver": "happy_path",
    }


# --------------------------------------------------------------------------- #
# the rendering drivers: read the served structure and the declared texts       #
# --------------------------------------------------------------------------- #


def drive_layout_read(ctx: PageContext, case: AttackCase) -> dict:
    """Where the declared page region and its anchors really are in the served document.

    Read-only: the page is opened and its structure is inspected. The declared contract is
    :data:`CANARY_CONTENT_SELECTOR` owning :data:`CANARY_DECLARED_OWNED`; a document without
    the region — or with the anchors outside it — is the broken-layout defect.
    """
    _open(ctx.page, ctx.base_url)
    layout = ctx.page.evaluate(
        """(spec) => {
             const region = document.querySelector(spec.region);
             const owned = {};
             for (const sel of spec.owned) {
               const el = document.querySelector(sel);
               owned[sel] = !!(region && el && region.contains(el));
             }
             return {
               region_present: !!region,
               region_fields: region ? region.children.length : 0,
               owned: owned,
             };
           }""",
        {"region": CANARY_CONTENT_SELECTOR, "owned": list(CANARY_DECLARED_OWNED)},
    )
    return {
        "layout": layout,
        "server": _ui_state(ctx),
        "driver": "layout_read",
    }


def drive_element_read(ctx: PageContext, case: AttackCase) -> dict:
    """Which of the declared anchors are really in the served document (read-only)."""
    _open(ctx.page, ctx.base_url)
    elements = ctx.page.evaluate(
        """(sels) => {
             const found = {};
             for (const sel of sels) { found[sel] = !!document.querySelector(sel); }
             return found;
           }""",
        list(CANARY_DECLARED_ANCHORS),
    )
    missing = sorted(sel for sel, present in (elements or {}).items() if not present)
    return {
        "elements": elements,
        "missing": missing,
        "declared": list(CANARY_DECLARED_ANCHORS),
        "server": _ui_state(ctx),
        "driver": "element_read",
    }


def drive_text_read(ctx: PageContext, case: AttackCase) -> dict:
    """What the declared controls really read (read-only; ``""`` when the anchor is absent)."""
    _open(ctx.page, ctx.base_url)
    texts = ctx.page.evaluate(
        """() => {
             const read = (sel) => {
               const el = document.querySelector(sel);
               return el ? (el.textContent || '').trim() : '';
             };
             return {primary_text: read('#quick'), hint_text: read('#hint')};
           }"""
    )
    return {
        "primary_text": str((texts or {}).get("primary_text", "")),
        "hint_text": str((texts or {}).get("hint_text", "")),
        "declared_primary_text": CANARY_DECLARED_PRIMARY_LABEL,
        "declared_hint_text": CANARY_DECLARED_HINT,
        "server": _ui_state(ctx),
        "driver": "text_read",
    }


# --------------------------------------------------------------------------- #
# the API drivers: no DOM, status codes and the served document only            #
# --------------------------------------------------------------------------- #


def _api_client(base_url: str, timeout_s: float = 20.0) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=timeout_s, follow_redirects=False)


def _api_document(client: httpx.Client) -> dict:
    response = client.get("/openapi.json")
    if response.status_code != 200:
        raise Unverifiable(f"the surface document answered {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise Unverifiable(f"the surface document is not JSON: {exc}") from exc


def _post_operations(document: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        raise Unverifiable("the surface document carries no 'paths' object")
    found: list[tuple[str, Mapping[str, Any]]] = []
    for path, operations in paths.items():
        if isinstance(operations, Mapping) and isinstance(operations.get("post"), Mapping):
            found.append((str(path), operations["post"]))
    if not found:
        raise Unverifiable("the surface document declares no POST route")
    return sorted(found, key=lambda item: item[0])


def _declares_mutation(operation: Mapping[str, Any]) -> bool:
    """Whether the *document* says this route mutates (so a token is required).

    The served document is the authority: ``x-godsv-mutates`` is this product's own marker and
    a non-empty ``security`` list is the OpenAPI-level equivalent. A route that declares itself
    read-only is allowed to answer without a token — demanding otherwise would invent a rule
    the surface never made (measured: 125 read-only POST routes "failed" such a rule).
    """
    marker = operation.get("x-godsv-mutates")
    if isinstance(marker, bool):
        return marker
    security = operation.get("security")
    return bool(security)


def _mutation_paths(document: Mapping[str, Any]) -> tuple[str, ...]:
    """The routes the document declares as mutating — the ones that must require a token."""
    found = [
        path for path, operation in _post_operations(document) if _declares_mutation(operation)
    ]
    if not found:
        raise Unverifiable(
            "the surface document declares no mutating route, so no boundary can be probed"
        )
    return tuple(found)


def _readonly_paths(document: Mapping[str, Any]) -> tuple[str, ...]:
    """The routes the document declares as read-only — reachable without a token."""
    found = [
        path for path, operation in _post_operations(document) if not _declares_mutation(operation)
    ]
    if not found:
        raise Unverifiable("the surface document declares no read-only route")
    return tuple(found)


def drive_api_health(ctx: PageContext, case: AttackCase) -> dict:
    with _api_client(ctx.base_url) as client:
        response = client.get("/health")
        body: Any
        try:
            body = response.json()
        except ValueError:
            body = None
        return {
            "responses": [{"probe": "health", "status": response.status_code, "body": body}],
            "driver": "api_health",
        }


def drive_api_unknown_route(ctx: PageContext, case: AttackCase) -> dict:
    with _api_client(ctx.base_url) as client:
        response = client.get("/definitely-not-a-route-4f2c")
        return {
            "responses": [
                {
                    "probe": "unknown_route",
                    "status": response.status_code,
                    "text": response.text[:200],
                }
            ],
            "driver": "api_unknown_route",
        }


def drive_api_missing_token(ctx: PageContext, case: AttackCase) -> dict:
    """Every route the document declares as mutating, called with no token at all."""
    with _api_client(ctx.base_url) as client:
        document = _api_document(client)
        probes = []
        for path in _mutation_paths(document):
            response = client.post(path, json={"probe": True})
            probes.append({"probe": path, "status": response.status_code})
        return {
            "responses": probes,
            "document_declares_mutations": len(probes),
            "driver": "api_missing_token",
        }


def drive_api_malformed_body(ctx: PageContext, case: AttackCase) -> dict:
    """A truncated body sent to a route the document declares read-only (so the body is parsed)."""
    with _api_client(ctx.base_url) as client:
        document = _api_document(client)
        path = _readonly_paths(document)[0]
        response = client.post(
            path, content=b'{"broken": ', headers={"Content-Type": "application/json"}
        )
        return {
            "responses": [
                {"probe": path, "status": response.status_code, "text": response.text[:200]}
            ],
            "driver": "api_malformed_body",
        }


def drive_api_oversized_body(ctx: PageContext, case: AttackCase) -> dict:
    """1.5 MiB to a route the document declares read-only, i.e. one that would parse it."""
    with _api_client(ctx.base_url) as client:
        document = _api_document(client)
        path = _readonly_paths(document)[0]
        response = client.post(
            path,
            content=b"x" * (1536 * 1024),
            headers={"Content-Type": "application/json"},
        )
        return {
            "responses": [
                {"probe": path, "status": response.status_code, "text": response.text[:200]}
            ],
            "driver": "api_oversized_body",
        }


def drive_api_concurrent(ctx: PageContext, case: AttackCase) -> dict:
    def one(index: int) -> dict:
        try:
            with _api_client(ctx.base_url) as client:
                response = client.get("/health")
                return {"probe": f"health-{index}", "status": response.status_code}
        except httpx.HTTPError as exc:
            return {"probe": f"health-{index}", "status": 0, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=8) as pool:
        probes = list(pool.map(one, range(8)))
    return {"responses": probes, "driver": "api_concurrent"}


def drive_api_openapi(ctx: PageContext, case: AttackCase) -> dict:
    with _api_client(ctx.base_url) as client:
        first = client.get("/openapi.json")
        second = client.get("/openapi.json")
        return {
            "responses": [
                {
                    "probe": "openapi",
                    "status": first.status_code,
                    "sha256": hashlib.sha256(first.content).hexdigest(),
                },
                {
                    "probe": "openapi-again",
                    "status": second.status_code,
                    "sha256": hashlib.sha256(second.content).hexdigest(),
                },
            ],
            "driver": "api_openapi",
        }


DRIVERS: dict[str, Callable[[PageContext, AttackCase], dict]] = {
    "empty_input": drive_empty_input,
    "hostile_input": drive_hostile_input,
    "oversized_input": drive_oversized_input,
    "double_submit": drive_double_submit,
    "slow_network": drive_slow_network,
    "refresh_mid_action": drive_refresh_mid_action,
    "expired_session": drive_expired_session,
    "history_replay": drive_history_replay,
    "happy_path": drive_happy_path,
    "layout_read": drive_layout_read,
    "element_read": drive_element_read,
    "text_read": drive_text_read,
    "api_health": drive_api_health,
    "api_unknown_route": drive_api_unknown_route,
    "api_missing_token": drive_api_missing_token,
    "api_malformed_body": drive_api_malformed_body,
    "api_oversized_body": drive_api_oversized_body,
    "api_concurrent": drive_api_concurrent,
    "api_openapi": drive_api_openapi,
}

#: which observation source each driver reads from (reported per case, so a verdict says how
#: it was verified instead of only what it concluded).
OBSERVED_BY = {
    "empty_input": "dom+server_state",
    "hostile_input": "dom+server_state",
    "oversized_input": "dom+server_state",
    "double_submit": "dom+server_state",
    "slow_network": "dom+server_state",
    "refresh_mid_action": "dom+server_state",
    "expired_session": "dom+server_state",
    "history_replay": "dom+server_state",
    "happy_path": "dom+server_state",
    "layout_read": "dom+server_state",
    "element_read": "dom+server_state",
    "text_read": "dom+server_state",
    "api_health": "http_status",
    "api_unknown_route": "http_status",
    "api_missing_token": "http_status",
    "api_malformed_body": "http_status",
    "api_oversized_body": "http_status",
    "api_concurrent": "http_status",
    "api_openapi": "http_status+digest",
}


# --------------------------------------------------------------------------- #
# the oracles: deterministic judgements over the observations                   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Judgement:
    outcome: str
    reason: str
    defects: tuple[dict, ...] = ()


def _defect(case: AttackCase, detail: str, observed: Any) -> dict:
    return {
        "defect_class": case.defect_class,
        "case_id": case.case_id,
        "surface_id": case.surface_id,
        "detail": detail,
        "observed": observed,
    }


def _claims_success(status_text: str, claim_marker: str = "") -> bool:
    """Whether the surface told the user the write succeeded (its status line or its claim)."""
    text = f"{status_text} {claim_marker}".strip().lower()
    return "saved" in text


def oracle_entry_rejected(case: AttackCase, observed: Mapping[str, Any]) -> Judgement:
    """Intended: the write is refused, no entry is stored, and the refusal is visible."""
    server = _need(observed, "server")
    status_text = str(_need(observed, "status_text"))
    count = int(server.get("record_count", -1))
    if count != 0:
        return Judgement(
            "fail",
            f"the target stored {count} entry/entries for a value the case says must be refused",
            (
                _defect(
                    case,
                    f"{count} entry/entries stored for a rejected input",
                    {"records": server.get("records"), "status_text": status_text},
                ),
            ),
        )
    if "failed" not in status_text.lower() and "error" not in status_text.lower():
        return Judgement(
            "fail",
            "the value was refused by the server but the surface never told the user",
            (
                _defect(
                    case,
                    "refusal not surfaced to the user",
                    {"status_text": status_text, "records": server.get("records")},
                ),
            ),
        )
    return Judgement("pass", "refused and surfaced; no entry stored")


def oracle_payload_not_rendered(case: AttackCase, observed: Mapping[str, Any]) -> Judgement:
    """Intended: a stored value is escaped, so no injected element exists and no handler runs."""
    server = _need(observed, "server")
    elements = int(_need(observed, "injected_elements"))
    handler_ran = bool(_need(observed, "handler_ran"))
    list_text = str(_need(observed, "list_text"))
    if elements or handler_ran:
        return Judgement(
            "fail",
            "the stored value was rendered as markup (an injected element exists)",
            (
                _defect(
                    case,
                    f"{elements} injected element(s) rendered; handler ran: {handler_ran}",
                    {"list_text": list_text[:200], "records": server.get("records")},
                ),
            ),
        )
    return Judgement("pass", "the stored value is rendered as text, not as markup")


def oracle_single_entry(case: AttackCase, observed: Mapping[str, Any]) -> Judgement:
    """Intended: repeated actions for one intention leave exactly one entry behind."""
    server = _need(observed, "server")
    count = int(server.get("record_count", -1))
    if count == 1:
        return Judgement("pass", "exactly one entry exists after the repeated action")
    if count <= 0:
        return Judgement(
            "fail",
            "no entry exists at all — the write did not survive the interaction",
            (_defect(case, "no entry stored where one was expected", server),),
        )
    return Judgement(
        "fail",
        f"{count} entries exist for one intention (duplicates)",
        (
            _defect(
                case,
                f"{count} entries created by one intention",
                {"records": server.get("records"), "submits": server.get("submits")},
            ),
        ),
    )


def oracle_no_unconfirmed_claim(case: AttackCase, observed: Mapping[str, Any]) -> Judgement:
    """Intended: a success claim requires a confirmed write.

    The claim is read from the surface's own record of what it last told the user, and the
    entries from the target's state. A claim made while nothing was stored — or while the write
    was still in flight and the server held nothing — is the false-completion defect this case
    exists for; a claim that matches a confirmed entry is fine.
    """
    server = _need(observed, "server")
    status_text = str(observed.get("status_text", ""))
    marker = str(_need(observed, "claim_marker"))
    claim = _claims_success(status_text, marker)
    count = int(server.get("record_count", -1))
    in_flight = observed.get("server_while_in_flight")
    if claim:
        if count == 0:
            return Judgement(
                "fail",
                "the surface claims the write succeeded while the server holds no entry",
                (
                    _defect(
                        case,
                        "success claimed without a confirmed write",
                        {"claim": (marker or status_text), "records": server.get("records")},
                    ),
                ),
            )
        if isinstance(in_flight, Mapping) and int(in_flight.get("record_count", -1)) == 0:
            return Judgement(
                "fail",
                "the surface claimed success while the write was still in flight and nothing "
                "was stored yet",
                (
                    _defect(
                        case,
                        "success claimed before the server confirmed the write",
                        {
                            "claim": (marker or status_text),
                            "records_during_action": in_flight.get("record_count"),
                            "records_after": count,
                        },
                    ),
                ),
            )
        return Judgement("pass", "the claim matches a confirmed write")
    if count > 0:
        return Judgement("pass", "the write landed and no unconfirmed claim was made")
    return Judgement("pass", "no claim and no entry: nothing was pretended")


def oracle_confirmed_single_entry(case: AttackCase, observed: Mapping[str, Any]) -> Judgement:
    """Intended (positive control): one entry, stored and confirmed to the user."""
    server = _need(observed, "server")
    status_text = str(_need(observed, "status_text"))
    count = int(server.get("record_count", -1))
    records = server.get("records") or []
    if count != 1:
        return Judgement(
            "fail",
            f"the ordinary flow stored {count} entries instead of 1",
            (_defect(case, f"ordinary flow stored {count} entries", server),),
        )
    if not _claims_success(status_text):
        return Judgement(
            "fail",
            "the ordinary flow stored an entry but never confirmed it to the user",
            (
                _defect(
                    case,
                    "no confirmation for a successful write",
                    {"status_text": status_text, "records": records},
                ),
            ),
        )
    name = str(records[0].get("name", ""))
    if name != "control":
        return Judgement(
            "fail",
            f"the stored entry carries {name!r} instead of the submitted value",
            (_defect(case, "stored value does not match the submission", records),),
        )
    return Judgement("pass", "one entry stored with the submitted value and confirmed")


def _single_response(observed: Mapping[str, Any], probe: str) -> dict:
    responses = _need(observed, "responses")
    for item in responses:
        if str(item.get("probe")) == probe:
            return dict(item)
    raise Unverifiable(f"the probe {probe!r} produced no response")


def oracle_layout_contract(case: AttackCase, observed: Mapping[str, Any]) -> Judgement:
    """Intended: the declared page region exists and owns every declared anchor.

    The contract is data (:data:`CANARY_CONTENT_SELECTOR`, :data:`CANARY_DECLARED_OWNED`), and
    the observation is the browser's own reading of the served document. A document without the
    region is the broken-layout defect, never an unverifiable case: absence *is* the finding.
    """
    layout = _need(observed, "layout")
    region = bool(layout.get("region_present"))
    owned = {str(sel): bool(value) for sel, value in (layout.get("owned") or {}).items()}
    misplaced = sorted(sel for sel in CANARY_DECLARED_OWNED if not owned.get(sel, False))
    if region and not misplaced:
        return Judgement(
            "pass",
            f"{CANARY_CONTENT_SELECTOR} exists and owns every declared anchor "
            f"({', '.join(CANARY_DECLARED_OWNED)})",
        )
    detail = (
        f"{CANARY_CONTENT_SELECTOR} is absent from the served document"
        if not region
        else f"declared anchor(s) outside {CANARY_CONTENT_SELECTOR}: {', '.join(misplaced)}"
    )
    return Judgement(
        "fail",
        f"the declared layout contract is broken: {detail}",
        (_defect(case, detail, dict(layout)),),
    )


def oracle_elements_present(case: AttackCase, observed: Mapping[str, Any]) -> Judgement:
    """Intended: every declared UI anchor is present in the served document."""
    missing = [str(item) for item in _need(observed, "missing")]
    if missing:
        return Judgement(
            "fail",
            f"{len(missing)} declared element(s) are missing from the served document: "
            f"{', '.join(missing)}",
            (
                _defect(
                    case,
                    f"declared element(s) missing: {', '.join(missing)}",
                    {"missing": missing, "elements": observed.get("elements")},
                ),
            ),
        )
    return Judgement(
        "pass",
        f"all {len(_need(observed, 'elements'))} declared element(s) are present",
    )


def oracle_declared_text(case: AttackCase, observed: Mapping[str, Any]) -> Judgement:
    """Intended: the declared controls carry exactly the contract's text."""
    primary = str(_need(observed, "primary_text"))
    hint = str(_need(observed, "hint_text"))
    if primary == CANARY_DECLARED_PRIMARY_LABEL and hint == CANARY_DECLARED_HINT:
        return Judgement("pass", "the declared control texts are exactly the contract's")
    return Judgement(
        "fail",
        f"the declared text does not match: #quick reads {primary!r} (declared "
        f"{CANARY_DECLARED_PRIMARY_LABEL!r}); #hint reads {hint!r} (declared "
        f"{CANARY_DECLARED_HINT!r})",
        (
            _defect(
                case,
                "declared control text mismatch",
                {"primary_text": primary, "hint_text": hint,
                 "declared_primary_text": CANARY_DECLARED_PRIMARY_LABEL,
                 "declared_hint_text": CANARY_DECLARED_HINT},
            ),
        ),
    )


def oracle_health_ok(case: AttackCase, observed: Mapping[str, Any]) -> Judgement:
    response = _single_response(observed, "health")
    status = int(response.get("status", 0))
    if status == 200 and isinstance(response.get("body"), Mapping):
        return Judgement("pass", "GET /health answered 200 with a JSON body")
    return Judgement(
        "fail",
        f"GET /health answered {status} without a JSON body",
        (_defect(case, f"health answered {status}", response),),
    )


def oracle_unknown_route_404(case: AttackCase, observed: Mapping[str, Any]) -> Judgement:
    response = _single_response(observed, "unknown_route")
    status = int(response.get("status", 0))
    if status == 404:
        return Judgement("pass", "an unknown path answered 404")
    return Judgement(
        "fail",
        f"an unknown path answered {status}",
        (_defect(case, f"unknown path answered {status}", response),),
    )


def oracle_mutation_needs_token(case: AttackCase, observed: Mapping[str, Any]) -> Judgement:
    responses = _need(observed, "responses")
    if not responses:
        raise Unverifiable("no mutation route was probed")
    wrong = [
        dict(item)
        for item in responses
        if int(item.get("status", 0)) not in (401, 403)
    ]
    if wrong:
        return Judgement(
            "fail",
            f"{len(wrong)} mutation route(s) did not refuse a token-less caller",
            (
                _defect(
                    case,
                    "a mutation answered a caller with no token: "
                    + ", ".join(f"{item.get('probe')}={item.get('status')}" for item in wrong),
                    wrong,
                ),
            ),
        )
    return Judgement("pass", f"{len(responses)} mutation route(s) refused a token-less caller")


def oracle_malformed_refused(case: AttackCase, observed: Mapping[str, Any]) -> Judgement:
    responses = _need(observed, "responses")
    item = dict(responses[0])
    status = int(item.get("status", 0))
    if status in (400, 401, 403, 413, 422):
        return Judgement("pass", f"a malformed body answered {status}")
    return Judgement(
        "fail",
        f"a malformed body answered {status}",
        (_defect(case, f"malformed body answered {status}", item),),
    )


def oracle_oversized_refused(case: AttackCase, observed: Mapping[str, Any]) -> Judgement:
    responses = _need(observed, "responses")
    item = dict(responses[0])
    status = int(item.get("status", 0))
    text = str(item.get("text", ""))
    if status in (200, 201) or '"ok": true' in text.replace(" ", ""):
        return Judgement(
            "fail",
            f"a 1.5 MiB body was accepted ({status})",
            (_defect(case, f"oversized body answered {status}", item),),
        )
    if status >= 500:
        return Judgement(
            "fail",
            f"an oversized body produced a server error ({status})",
            (_defect(case, f"oversized body answered {status}", item),),
        )
    return Judgement("pass", f"an oversized body was refused ({status})")


def oracle_all_healthy(case: AttackCase, observed: Mapping[str, Any]) -> Judgement:
    responses = _need(observed, "responses")
    bad = [dict(item) for item in responses if int(item.get("status", 0)) != 200]
    if bad:
        return Judgement(
            "fail",
            f"{len(bad)} of {len(responses)} concurrent status requests failed",
            (_defect(case, "concurrent status requests degraded", bad),),
        )
    return Judgement("pass", f"{len(responses)} concurrent status requests answered 200")


def oracle_openapi_stable(case: AttackCase, observed: Mapping[str, Any]) -> Judgement:
    first = _single_response(observed, "openapi")
    second = _single_response(observed, "openapi-again")
    if int(first.get("status", 0)) != 200 or int(second.get("status", 0)) != 200:
        raise Unverifiable(
            f"the surface document answered {first.get('status')}/{second.get('status')}"
        )
    if str(first.get("sha256")) != str(second.get("sha256")):
        return Judgement(
            "fail",
            "the served surface document changed between two consecutive calls",
            (_defect(case, "surface document is not stable", {"first": first, "second": second}),),
        )
    return Judgement("pass", "the served surface document is byte-stable across calls")


ORACLES: dict[str, Callable[[AttackCase, Mapping[str, Any]], Judgement]] = {
    "entry_rejected": oracle_entry_rejected,
    "payload_not_rendered": oracle_payload_not_rendered,
    "single_entry": oracle_single_entry,
    "no_unconfirmed_claim": oracle_no_unconfirmed_claim,
    "confirmed_single_entry": oracle_confirmed_single_entry,
    "layout_contract": oracle_layout_contract,
    "elements_present": oracle_elements_present,
    "declared_text": oracle_declared_text,
    "health_ok": oracle_health_ok,
    "unknown_route_404": oracle_unknown_route_404,
    "mutation_needs_token": oracle_mutation_needs_token,
    "malformed_refused": oracle_malformed_refused,
    "oversized_refused": oracle_oversized_refused,
    "all_healthy": oracle_all_healthy,
    "openapi_stable": oracle_openapi_stable,
}


# --------------------------------------------------------------------------- #
# the driver dependency: declared, probed, refused structurally                  #
# --------------------------------------------------------------------------- #

#: The extras ``pyproject.toml`` declares for the capabilities this suite imports lazily. The
#: names live here as data, not as prose, because the refusal message is built from them.
BROWSER_EXTRA = "browser"
API_EXTRA = "api"

#: Two failure modes, two fixes — so they are reported apart instead of as one blur: the *client
#: package* (``playwright``, a Python import) and the *browser binary* it drives (Chromium, a
#: separate download that the Python package does not bring).
BROWSER_DRIVER = "playwright"
BROWSER_INSTALL_COMMANDS: tuple[str, ...] = (
    "pip install 'skaldir-agent[browser]'",
    "playwright install chromium",
)
API_INSTALL_COMMANDS: tuple[str, ...] = ("pip install 'skaldir-agent[api]'",)

#: The drivers that need a real browser: every driver that observes through the DOM.
BROWSER_DRIVERS = frozenset(
    name for name, source in OBSERVED_BY.items() if str(source).startswith("dom")
)

#: Probe results, per failure question. Cached because the launch probe costs a browser start
#: and ``run_case`` asks once per case; ``refresh=True`` (and :func:`reset_driver_status_cache`)
#: exist so a caller that just changed the environment is not answered from a stale memory.
_DRIVER_STATUS: dict[str, dict] = {}


def _one_line(text: Any, *, limit: int = 240) -> str:
    """Collapse a multi-line driver error into the one line a payload can carry."""
    return " ".join(str(text).split())[:limit]


def _refusal_line(status: Mapping[str, Any]) -> str:
    """The one line a human can act on: what is missing, and the exact command that fixes it."""
    missing = status.get("missing") or status.get("driver") or "the driver"
    commands = " && ".join(status.get("install_commands") or [])
    feature = status.get("feature") or "this suite"
    return f"{feature} is blocked: {missing} is not available — install it with: {commands}"


def _browser_probe(probe_launch: bool) -> dict:
    """Measure it: is the driver importable, and can it actually open a browser here?"""
    base = {
        "feature": "the browser adversarial suite",
        "driver": BROWSER_DRIVER,
        "extra": BROWSER_EXTRA,
        "install_commands": list(BROWSER_INSTALL_COMMANDS),
    }
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415 — lazy on purpose
    except Exception as exc:  # noqa: BLE001 — every failure mode is a reason, not a crash
        return {
            **base,
            "available": False,
            "reason": "driver_missing",
            "missing": BROWSER_DRIVER,
            "detail": _one_line(f"{type(exc).__name__}: {exc}"),
        }
    if not probe_launch:
        return {
            **base,
            "available": True,
            "reason": "",
            "missing": "",
            "detail": f"{BROWSER_DRIVER} importable",
        }
    try:
        with sync_playwright() as driver:  # pragma: no cover — the browser is the machine's
            browser = driver.chromium.launch(headless=True)
            browser.close()
    except Exception as exc:  # noqa: BLE001
        return {
            **base,
            "available": False,
            "reason": "browser_missing",
            "missing": f"{BROWSER_DRIVER} chromium",
            "detail": _one_line(f"{type(exc).__name__}: {exc}"),
        }
    return {
        **base,
        "available": True,
        "reason": "",
        "missing": "",
        "detail": f"{BROWSER_DRIVER} chromium (headless)",
    }


def browser_driver_status(*, probe_launch: bool = True, refresh: bool = False) -> dict:
    """Is the browser driver usable here — and if not, what exactly installs it?

    ``probe_launch=False`` answers the cheap question only (importable?). ``probe_launch=True``
    settles it by really starting Chromium once, because "the package is installed" and "a page
    can be driven" are different facts and only the second one makes a verdict honest.
    """
    key = "launch" if probe_launch else "import"
    if refresh or key not in _DRIVER_STATUS:
        _DRIVER_STATUS[key] = _browser_probe(probe_launch)
    return dict(_DRIVER_STATUS[key])


def api_driver_status(*, refresh: bool = False) -> dict:
    """Can this interpreter serve the real Skaldir API? (``--with-api`` needs ``fastapi``.)"""
    if refresh or "api" not in _DRIVER_STATUS:
        base = {
            "feature": "the real Skaldir API as a target (--with-api)",
            "driver": "fastapi+uvicorn",
            "extra": API_EXTRA,
            "install_commands": list(API_INSTALL_COMMANDS),
        }
        try:
            import fastapi  # noqa: PLC0415,F401 — lazy on purpose: the CLI works without it
            import uvicorn  # noqa: PLC0415,F401
        except Exception as exc:  # noqa: BLE001
            _DRIVER_STATUS["api"] = {
                **base,
                "available": False,
                "reason": "driver_missing",
                "missing": "fastapi/uvicorn",
                "detail": _one_line(f"{type(exc).__name__}: {exc}"),
            }
        else:
            _DRIVER_STATUS["api"] = {
                **base,
                "available": True,
                "reason": "",
                "missing": "",
                "detail": "fastapi + uvicorn importable",
            }
    return dict(_DRIVER_STATUS["api"])


def reset_driver_status_cache() -> None:
    """Forget every probe (a caller that just changed the environment must not read a memory)."""
    _DRIVER_STATUS.clear()


class DriverUnavailable(Unverifiable):
    """A *declared* driver is not installed here: the suite is **blocked**, not failed.

    ``Unverifiable`` on purpose, so any case that meets it is reported ``unknown`` — never
    ``pass``, never ``fail``. The structured status travels with the exception, which is what
    lets the CLI answer exit ``3`` with the exact remediation instead of a traceback (or, worse,
    a verdict that blames the detectors for a missing package).
    """

    def __init__(self, status: Mapping[str, Any]) -> None:
        self.status = dict(status)
        super().__init__(_refusal_line(self.status))

    def as_dict(self) -> dict:
        """The machine-readable refusal: what is missing, and what installs it."""
        return {**self.status, "blocked": True, "exit_code": EXIT_BLOCKED}


def preflight(plan: SuitePlan) -> dict:
    """What of ``plan`` this host can actually run — and what has to be installed for the rest.

    ``{"ok": bool, "needs": [...], "blocked": {...}}``. ``blocked`` is empty when the run can
    proceed; otherwise it is the structured refusal (``exit_code`` 3, the missing driver, the
    extra, the commands, and the case ids it silences) exactly as the report publishes it.
    """
    blocked_cases = [
        planned for planned in plan.cases if planned.case.driver in BROWSER_DRIVERS
    ]
    needs = sorted({planned.case.driver for planned in blocked_cases})
    if not blocked_cases:
        return {"ok": True, "needs": [], "blocked": {}}
    status = browser_driver_status()
    if status["available"]:
        return {"ok": True, "needs": needs, "blocked": {}}
    return {
        "ok": False,
        "needs": needs,
        "blocked": {
            **status,
            "blocked": True,
            "exit_code": EXIT_BLOCKED,
            "cases": [planned.case.case_id for planned in blocked_cases],
        },
    }


def _blocked_result(planned: PlannedCase, status: Mapping[str, Any]) -> CaseResult:
    """The honest answer for a case this host cannot run: unknown, with the reason and the fix."""
    return CaseResult(
        case_id=planned.case.case_id,
        surface_id=planned.case.surface_id,
        intent_class=planned.case.intent_class,
        defect_class=planned.case.defect_class,
        operation=planned.operation,
        oracle=planned.case.oracle,
        oracle_kind=planned.oracle_kind,
        target_ref=planned.target_ref,
        outcome="unknown",
        reason=_refusal_line(status),
        observed_by=OBSERVED_BY.get(planned.case.driver, "none"),
        observed={"driver": {k: v for k, v in status.items() if k != "cases"}},
        planned_by=planned.planned_by,
        decision=dict(planned.decision),
    )


# --------------------------------------------------------------------------- #
# the runner                                                                    #
# --------------------------------------------------------------------------- #


class BrowserPool:
    """One Chromium per thread that drives cases, one fresh context per case.

    Isolation is the point: no case may inherit a cookie, a session or a history entry from
    another, so every case gets its own ``BrowserContext`` and the threads never share a page.

    **The pool is single-threaded, and that is a hard limit of the driver, not a preference.**
    Playwright's sync API is bound to the thread that started it: from any other thread, even
    after the original one has exited, every call raises
    ``Error: cannot switch to a different thread (which happens to have exited)`` — ``close()``
    included. So a pool can only close what it opened if it is closed *on the thread that used
    it* (measured 2026-09-20; that is why :func:`run_suite` no longer shares one pool across a
    ``ThreadPoolExecutor``). When — despite this — :meth:`close` meets a browser it cannot
    close, it records the reason in :attr:`close_errors` instead of swallowing it: a silent
    leak is what a release gate would never see.
    """

    def __init__(self) -> None:
        self._local = threading.local()
        #: every browser this pool opened, in open order (never keyed by thread id: the idents
        #: of dead threads are reused, and an overwritten entry is a browser nobody can close)
        self._browsers: list[Any] = []
        #: what ``close()`` could not close, and why. Empty on a healthy lifecycle.
        self.close_errors: list[str] = []
        #: the thread that first drove this pool (``None`` until one does)
        self._owner: int | None = None

    @property
    def owner_thread(self) -> int | None:
        """The thread that used this pool, or ``None`` if it never drove a case."""
        return self._owner

    def _browser(self) -> Any:
        if self._owner is None:
            self._owner = threading.get_ident()
        playwright = getattr(self._local, "playwright", None)
        if playwright is None:
            # the refused import: no bare ModuleNotFoundError may escape this class, because a
            # raw traceback is indistinguishable from a broken product
            status = browser_driver_status()
            if not status["available"]:
                raise DriverUnavailable(status)
            from playwright.sync_api import sync_playwright

            playwright = sync_playwright().start()
            self._local.playwright = playwright
        browser = getattr(self._local, "browser", None)
        if browser is None:
            browser = playwright.chromium.launch(headless=True)
            self._local.browser = browser
            self._browsers.append(browser)
        return browser

    def context(self, *, timeout_s: int) -> Any:
        browser = self._browser()
        context = browser.new_context(viewport={"width": 1024, "height": 768})
        context.set_default_timeout(max(2_000, int(timeout_s) * 1000))
        context.set_default_navigation_timeout(max(3_000, int(timeout_s) * 1000))
        # a browser asks before re-sending a POST (reload/back/forward); a real user answers
        # "continue", so the case measures the flow a user would actually get.
        context.on("dialog", lambda dialog: _accept(dialog))
        return context

    def close(self) -> None:
        """Close every browser this pool opened **and** the driver behind them.

        Both halves matter and they are not the same half: a browser that is closed while its
        ``playwright/driver/node`` run-driver process is left running is still a leaked process
        (that is what accumulated during the stalled run). A call that cannot be honoured is
        recorded in :attr:`close_errors` rather than passed over.
        """
        here = threading.get_ident()
        if self._owner is not None and self._owner != here:
            self.close_errors.append(
                f"close() ran on thread {here} but the pool was used on thread {self._owner}: "
                "Playwright cannot cross threads, so this browser and its driver are left to "
                "the OS — create and close the pool on one thread"
            )
        for browser in list(self._browsers):
            try:
                browser.close()
            except Exception as exc:  # noqa: BLE001 — a browser already gone is not a failure
                self.close_errors.append(
                    f"browser.close() failed: {type(exc).__name__}: {_one_line(exc)}"
                )
        self._browsers.clear()
        playwright = getattr(self._local, "playwright", None)
        if playwright is not None:
            try:
                playwright.stop()
            except Exception as exc:  # noqa: BLE001
                self.close_errors.append(
                    f"playwright.stop() failed: {type(exc).__name__}: {_one_line(exc)}"
                )
            self._local.playwright = None
            self._local.browser = None


def _accept(dialog: Any) -> None:
    """Accept a dialog (Chromium asks before re-sending a POST on back/forward)."""
    try:
        dialog.accept()
    except Exception:  # noqa: BLE001 — a dialog that is already gone is not a failure
        pass


def run_case(
    planned: PlannedCase,
    *,
    surface: TargetSurface,
    timeout_s: int,
    slots: int,
    pool: BrowserPool | None = None,
) -> CaseResult:
    """Run one case against one surface and judge it with its declared oracle."""
    case = planned.case
    started = time.time()
    driver = DRIVERS.get(case.driver)
    oracle = ORACLES.get(case.oracle)
    observed_by = OBSERVED_BY.get(case.driver, "none")
    base = {
        "case_id": case.case_id,
        "surface_id": case.surface_id,
        "intent_class": case.intent_class,
        "defect_class": case.defect_class,
        "operation": planned.operation,
        "oracle": case.oracle,
        "oracle_kind": planned.oracle_kind,
        "target_ref": planned.target_ref,
        "observed_by": observed_by,
        "planned_by": planned.planned_by,
        "decision": dict(planned.decision),
    }
    if driver is None or oracle is None:
        return CaseResult(
            **base,
            outcome="unknown",
            reason="the plan named a driver or oracle the action space does not declare",
            duration_ms=0,
        )
    if case.driver in BROWSER_DRIVERS:
        # the refusal, once per run: a missing driver is *blocked*, never a failed oracle — the
        # case cannot be observed, so it is unknown and the reason names the exact install
        status = browser_driver_status()
        if not status["available"]:
            return _blocked_result(planned, status)
    if not planned.consistent:
        return CaseResult(
            **base,
            outcome="unknown",
            reason=f"plan deviation: {planned.deviation}",
            duration_ms=0,
        )
    if planned.oracle_source != "deterministic":
        return CaseResult(
            **base,
            outcome="unknown",
            reason=(
                "no deterministic oracle was assigned"
                f" ({planned.oracle_source}); a Computer Use judgement is not a verdict here"
            ),
            duration_ms=0,
        )
    observed: dict = {}
    try:
        if surface.kind == "ui_flow":
            observed = _run_ui_case(case, surface, timeout_s, pool)
        else:
            observed = _run_api_case(case, surface, timeout_s)
        judgement = oracle(case, observed)
    except Unverifiable as exc:
        return CaseResult(
            **base,
            outcome="unknown",
            reason=f"unverifiable: {exc}",
            observed=observed,
            duration_ms=int((time.time() - started) * 1000),
        )
    except Exception as exc:  # noqa: BLE001 — an unreachable target is not a defect
        return CaseResult(
            **base,
            outcome="unknown",
            reason=f"could not observe the target: {type(exc).__name__}: {exc}"[:300],
            observed=observed,
            duration_ms=int((time.time() - started) * 1000),
        )
    return CaseResult(
        **base,
        outcome=judgement.outcome,
        reason=judgement.reason,
        observed=observed,
        defects=judgement.defects,
        verified=judgement.outcome == "pass",
        duration_ms=int((time.time() - started) * 1000),
    )


def _run_ui_case(
    case: AttackCase, surface: TargetSurface, timeout_s: int, pool: BrowserPool | None = None
) -> dict:
    """Drive one UI case. The pool is created **and closed on this thread** when none is given.

    A pool passed in is *borrowed*: whoever created it closes it, on the thread that created
    it. Cross-thread reuse is what leaks (see :func:`run_suite`), so the runner never passes
    one.
    """
    owned = pool is None
    active = pool if pool is not None else BrowserPool()
    context = None
    try:
        context = active.context(timeout_s=timeout_s)
        page = context.new_page()
        page.set_default_timeout(max(2_000, timeout_s * 1000))
        ctx = PageContext(
            page=page,
            request=context.request,
            base_url=surface.url.rstrip("/"),
            per_case_timeout_s=timeout_s,
        )
        return DRIVERS[case.driver](ctx, case)
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:  # noqa: BLE001 — a closed context is not a failure
                pass
        if owned:
            active.close()


def _run_api_case(case: AttackCase, surface: TargetSurface, timeout_s: int) -> dict:
    holder = PageContext(
        page=None,
        request=None,
        base_url=surface.url.rstrip("/"),
        per_case_timeout_s=timeout_s,
    )
    return DRIVERS[case.driver](holder, case)


def _blocked_report(
    plan: SuitePlan,
    status: Mapping[str, Any],
    *,
    started_at: str,
    started: float,
    run_id: str,
    extra_notes: Iterable[str],
) -> SuiteReport:
    """A run that never started: every case unknown, the refusal machine-readable, UNKNOWN.

    UNKNOWN and not FAIL: no oracle was violated and no detector was proved broken — the host
    simply cannot observe anything. The exit code for this report is ``3`` (blocked) and the
    ``--allow-unknown`` waiver is refused for it (see :func:`exit_code_for`), because waiving
    "the suite could not start" as a success is exactly the silent skip this refusal exists to
    prevent.
    """
    notes = [*list(extra_notes), _refusal_line(status)]
    notes.append(
        "no case was executed: a case that cannot be observed is not a pass, and a run that "
        "cannot start is not a verdict"
    )
    return SuiteReport(
        suite_version=SUITE_VERSION,
        target={
            "surfaces": {
                surface.surface_id: {"kind": surface.kind, "url": surface.url}
                for surface in plan.surfaces
            }
        },
        plan=plan.as_dict(),
        verdict="UNKNOWN",
        results=tuple(_blocked_result(planned, status) for planned in plan.cases),
        started_at=started_at,
        finished_at=_now_iso(),
        duration_ms=int((time.time() - started) * 1000),
        run_id=run_id or f"adv-{uuid.uuid4().hex[:12]}",
        notes=tuple(notes),
        blocked=dict(status),
    )


def run_suite(
    plan: SuitePlan,
    *,
    slots: int = 6,
    run_id: str = "",
    extra_notes: Iterable[str] = (),
) -> SuiteReport:
    """Run every planned case in parallel, each in its own context, and judge the verdict.

    Before a single case starts, the plan is checked against this host: a plan that needs a
    browser the interpreter cannot drive is **blocked**, not run empty. It is refused whole
    rather than case by case, because a partial suite must not leave a gate to read a count
    ("11 unknown") as if the cases had been tried — every case is reported with the reason and
    the exact install command, and ``report.blocked`` carries the same refusal as data.
    """
    started_at = _now_iso()
    started = time.time()
    gate = preflight(plan)
    if not gate["ok"]:
        return _blocked_report(
            plan,
            gate["blocked"],
            started_at=started_at,
            started=started,
            run_id=run_id,
            extra_notes=extra_notes,
        )
    slots = max(1, int(slots))
    per_case_timeout_s = int(plan.budget.get("per_case_timeout_s", 45))
    max_wall_s = int(plan.budget.get("max_wall_s", 300))
    by_surface = {surface.surface_id: surface for surface in plan.surfaces}
    results: dict[str, CaseResult] = {}
    notes = list(extra_notes)
    # NO pool is shared with the worker threads (measured, and the reason is not tidiness):
    # Playwright's sync API is bound to the thread that started it, so a browser or a driver
    # created inside a `ThreadPoolExecutor` worker can only be closed *by that thread*. This
    # function used to build one `BrowserPool` up front and call `pool.close()` after the
    # executor shut down — by then every worker had exited, the calls raised
    # `Error: cannot switch to a different thread (which happens to have exited)`, the pool
    # swallowed them (`except Exception: pass`), and every worker leaked its Chromium *and* its
    # `playwright/driver/node` run-driver process into the (long) life of the pytest process.
    # Each case now owns the pool it drives, created and closed on the thread that runs it
    # (`_run_ui_case`), so the lifecycle is same-thread by construction. The cost is one
    # Chromium per case instead of one per worker thread, which is what the leak was charging.
    with ThreadPoolExecutor(max_workers=slots) as executor:
        futures = {}
        for planned in plan.cases:
            surface = by_surface.get(planned.case.surface_id)
            if surface is None:
                results[planned.case.case_id] = CaseResult(
                    case_id=planned.case.case_id,
                    surface_id=planned.case.surface_id,
                    intent_class=planned.case.intent_class,
                    defect_class=planned.case.defect_class,
                    operation=planned.operation,
                    oracle=planned.case.oracle,
                    oracle_kind=planned.oracle_kind,
                    target_ref=planned.target_ref,
                    outcome="unknown",
                    reason="the plan targets a surface this run did not offer",
                    observed_by=OBSERVED_BY.get(planned.case.driver, "none"),
                    planned_by=planned.planned_by,
                    decision=dict(planned.decision),
                )
                continue
            futures[
                executor.submit(
                    run_case,
                    planned,
                    surface=surface,
                    timeout_s=per_case_timeout_s,
                    slots=slots,
                )
            ] = planned
        completed = 0
        try:
            for future in _iter_done(futures, max_wall_s):
                completed += 1
                result = future.result()
                results[result.case_id] = result
        except TimeoutError:
            notes.append(
                f"the wall budget of {max_wall_s}s expired after {completed} case(s); "
                "the rest are reported UNKNOWN"
            )

    ordered: list[CaseResult] = []
    for planned in plan.cases:
        result = results.get(planned.case.case_id)
        if result is None:
            result = CaseResult(
                case_id=planned.case.case_id,
                surface_id=planned.case.surface_id,
                intent_class=planned.case.intent_class,
                defect_class=planned.case.defect_class,
                operation=planned.operation,
                oracle=planned.case.oracle,
                oracle_kind=planned.oracle_kind,
                target_ref=planned.target_ref,
                outcome="unknown",
                reason="the case did not finish inside the wall budget",
                observed_by=OBSERVED_BY.get(planned.case.driver, "none"),
                planned_by=planned.planned_by,
                decision=dict(planned.decision),
            )
        ordered.append(result)
    verdict = verdict_for(ordered)
    if not ordered:
        notes.append("no case was planned, so nothing could be certified")
    return SuiteReport(
        suite_version=SUITE_VERSION,
        target={
            "surfaces": {
                surface.surface_id: {"kind": surface.kind, "url": surface.url}
                for surface in plan.surfaces
            }
        },
        plan=plan.as_dict(),
        verdict=verdict,
        results=tuple(ordered),
        started_at=started_at,
        finished_at=_now_iso(),
        duration_ms=int((time.time() - started) * 1000),
        run_id=run_id or f"adv-{uuid.uuid4().hex[:12]}",
        notes=tuple(notes),
    )


def _iter_done(futures: Mapping[Any, Any], max_wall_s: int) -> Iterable[Any]:
    from concurrent.futures import as_completed

    return as_completed(list(futures.keys()), timeout=max(1, int(max_wall_s)))


# --------------------------------------------------------------------------- #
# the release gate                                                              #
# --------------------------------------------------------------------------- #

#: exit codes this suite uses (the product CLI's own vocabulary)
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_VERIFICATION_FAILED = 2
EXIT_BLOCKED = 3


def exit_code_for(report: SuiteReport, *, block_release: bool, allow_unknown: bool = False) -> int:
    """Map the verdict onto an exit code.

    ``PASS`` is the only verdict that can exit ``0`` — unless the caller explicitly waived an
    ``UNKNOWN`` suite in report mode (``allow_unknown``), which a release gate refuses.

    A run that never started (``report.blocked``) is **never** waivable: ``--allow-unknown``
    exists to accept cases that ran and could not be judged, not to accept cases that were
    never attempted. Waiving "the suite could not start" as a success is exactly the silent
    skip this refusal exists to prevent.
    """
    if report.verdict == "PASS":
        return EXIT_OK
    if report.verdict == "FAIL":
        return EXIT_VERIFICATION_FAILED
    if report.blocked:
        return EXIT_BLOCKED
    if allow_unknown and not block_release:
        return EXIT_OK
    return EXIT_BLOCKED


def release_gate_payload(
    report: SuiteReport, *, block_release: bool, allow_unknown: bool = False
) -> dict:
    """The release decision as data: the verdict, the defects, and what the exit code did."""
    waived = bool(
        report.verdict == "UNKNOWN" and allow_unknown and not block_release and not report.blocked
    )
    return {
        "release_gate": bool(block_release),
        "verdict": report.verdict,
        "blocks_release": report.blocks_release,
        "waived_unknown": waived,
        "exit_code": exit_code_for(
            report, block_release=block_release, allow_unknown=allow_unknown
        ),
        "defect_count": len(report.defects),
        "defect_classes": sorted({str(item["defect_class"]) for item in report.defects}),
        "failed_cases": [
            result.case_id for result in report.results if result.outcome == "fail"
        ],
        "unknown_cases": [
            result.case_id for result in report.results if result.outcome == "unknown"
        ],
    }


# --------------------------------------------------------------------------- #
# durable record + the canary self-check                                        #
# --------------------------------------------------------------------------- #


#: the decision ledger's file name, next to the run report
DECISIONS_SUFFIX = ".decisions.jsonl"


def record_run(
    report: SuiteReport, state_dir: str | Path, *, extra: Mapping[str, Any] | None = None
) -> Path:
    """Write the run's report next to the evidence of every case (resumable, citable).

    ``extra`` carries what belongs to the *call* rather than to the run — the release gate, the
    waiver, the exit code — so the durable record answers "what did this run say, and what did
    the caller do with it" without re-reading the console.

    A second file is written beside the report: ``<run_id>.decisions.jsonl``, one line per Jev
    call, in the order the cases were planned. It is the *decision record*: the decision id,
    the reason, the status, the provider mode and ``live_jev``, the fallback and the latency.
    A reader can therefore answer "who decided this case, why, and how long did that take"
    from files alone — and tell a live Jev call from the offline façade without guessing.
    """
    directory = Path(state_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report.run_id}.json"
    payload = report.as_dict()
    if extra:
        payload.update(_trim_observation(dict(extra)))
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    ledger = directory / f"{report.run_id}{DECISIONS_SUFFIX}"
    rows = [
        {**dict(item), "run_id": report.run_id, "verdict": report.verdict}
        for item in (report.plan.get("decisions") or [])
    ]
    ledger.write_text(
        "".join(json.dumps(row, sort_keys=True, default=str) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def decision_ledger(run_id: str, state_dir: str | Path) -> list[dict]:
    """Read back the decision records of one run (empty when the run was never recorded)."""
    path = Path(state_dir).expanduser() / f"{run_id}{DECISIONS_SUFFIX}"
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


#: the defect classes the canary's *broken* mode must produce, per case id. This is the suite's
#: own specification: if one of these does not fire, the detector is broken, not the target.
CANARY_EXPECTED: dict[str, str] = {
    "ui-empty-input": DEFECT_EMPTY_INPUT,
    "ui-huge-input": DEFECT_UNESCAPED_INPUT,
    "ui-oversized-input": DEFECT_UNBOUNDED_INPUT,
    "ui-double-submit": DEFECT_DOUBLE_SUBMIT,
    "ui-slow-network": DEFECT_SLOW_NETWORK,
    "ui-refresh-mid-action": DEFECT_FALSE_SUCCESS,
    "ui-expired-session": DEFECT_EXPIRED_SESSION,
    "ui-history-replay": DEFECT_HISTORY_REPLAY,
    "ui-broken-layout": DEFECT_LAYOUT,
    "ui-missing-element": DEFECT_ELEMENT_MISSING,
    "ui-wrong-text": DEFECT_TEXT_MISMATCH,
}


@dataclass(frozen=True)
class SelfCheck:
    """The result of running the suite against both canary modes."""

    ok: bool
    broken_verdict: str
    fixed_verdict: str
    detected: tuple[str, ...]
    missed: tuple[str, ...]
    false_positives: tuple[str, ...]
    detail: str
    #: the structured refusal when the self-check could not start at all (a declared driver is
    #: missing). Empty for a self-check that really ran — so a consumer can tell "the detectors
    #: are broken" (a product defect) from "the browser is not installed here" (a setup step).
    blocked: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "broken_verdict": self.broken_verdict,
            "fixed_verdict": self.fixed_verdict,
            "detected": list(self.detected),
            "missed": list(self.missed),
            "false_positives": list(self.false_positives),
            "detail": self.detail,
            "blocked": dict(self.blocked),
        }

    @property
    def exit_code(self) -> int:
        """``3`` when the self-check never started, ``2`` when it ran and did not prove itself."""
        return EXIT_BLOCKED if self.blocked else EXIT_VERIFICATION_FAILED


def selftest(*, slots: int = 6, planner: str = "auto", timeout_s: int = 45) -> SelfCheck:
    """Prove the suite bites: FAIL on the broken canary, PASS on the fixed one.

    A detector that never fired is decoration — this is the suite's own acceptance test, and it
    is what ``adversarial selftest`` runs.

    It refuses **before** starting anything when the browser driver is not installed: on a clean
    install (no ``browser`` extra) the canary cannot be driven at all, and every detector would
    report "did not fire" — a *false* verdict that blames the detectors and never names the
    missing driver. The refusal is structured instead (exit ``3``, the driver, the extra, and
    the exact install commands), so "the product is broken" and "a setup step is missing" can
    never be confused again.
    """
    status = browser_driver_status()
    if not status["available"]:
        return SelfCheck(
            ok=False,
            broken_verdict="UNKNOWN",
            fixed_verdict="UNKNOWN",
            detected=(),
            missed=tuple(sorted(CANARY_EXPECTED)),
            false_positives=(),
            detail=(
                _refusal_line(status)
                + "; nothing was attempted, so no detector was measured"
            ),
            blocked={**status, "blocked": True, "exit_code": EXIT_BLOCKED},
        )
    broken = CanaryApp("broken")
    fixed = CanaryApp("fixed")
    try:
        broken_url = broken.start()
        fixed_url = fixed.start()
        broken_report = _run_target(
            ui_url=broken_url, slots=slots, planner=planner, timeout_s=timeout_s
        )
        fixed_report = _run_target(
            ui_url=fixed_url, slots=slots, planner=planner, timeout_s=timeout_s
        )
    finally:
        broken.stop()
        fixed.stop()
    detected = tuple(
        sorted(
            {
                str(item["case_id"])
                for item in broken_report.defects
                if str(item["defect_class"]) == CANARY_EXPECTED.get(str(item["case_id"]), "")
            }
        )
    )
    missed = tuple(
        sorted(case_id for case_id in CANARY_EXPECTED if case_id not in set(detected))
    )
    false_positives = tuple(
        result.case_id for result in fixed_report.results if result.outcome != "pass"
    )
    ok = not missed and not false_positives and broken_report.verdict == "FAIL"
    detail = (
        f"broken: {broken_report.verdict} with {len(broken_report.defects)} defect(s); "
        f"fixed: {fixed_report.verdict}; "
        f"detectors fired: {len(detected)}/{len(CANARY_EXPECTED)}"
    )
    if missed:
        detail += f"; detectors that did not fire: {', '.join(missed)}"
    if false_positives:
        detail += f"; cases that did not pass on the fixed target: {', '.join(false_positives)}"
    return SelfCheck(
        ok=ok,
        broken_verdict=broken_report.verdict,
        fixed_verdict=fixed_report.verdict,
        detected=detected,
        missed=missed,
        false_positives=false_positives,
        detail=detail,
    )


def _run_target(
    *,
    ui_url: str = "",
    api_url: str = "",
    slots: int = 6,
    planner: str = "auto",
    timeout_s: int = 45,
    max_wall_s: int = 300,
) -> SuiteReport:
    plan = plan_suite(
        surfaces=offered_surfaces(ui_url=ui_url, api_url=api_url),
        slots=slots,
        per_case_timeout_s=timeout_s,
        max_wall_s=max_wall_s,
        planner=planner,
    )
    return run_suite(plan, slots=slots)


# --------------------------------------------------------------------------- #
# the real Skaldir API as a target                                              #
# --------------------------------------------------------------------------- #


@dataclass
class ApiProcess:
    """A real ``skaldir.api_server`` child process used as a target."""

    url: str
    token: str
    process: subprocess.Popen
    runtime_dir: str

    def stop(self) -> None:
        try:
            self.process.terminate()
            self.process.wait(timeout=10)
        except Exception:  # noqa: BLE001 — an already-dead server is not a failure
            try:
                self.process.kill()
            except Exception:  # noqa: BLE001
                pass


#: the child: the real server, in its own process, in a throwaway runtime directory.
API_BOOTSTRAP = (
    "import sys\n"
    "from skaldir import api_server\n"
    "sys.exit(api_server.serve(host='127.0.0.1', port=int(sys.argv[1]), "
    "workdir=sys.argv[2], log_level='warning', timeout_s=30.0))\n"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_skaldir_api(
    *, timeout_s: float = 40.0, runtime_dir: str | Path | None = None
) -> ApiProcess:
    """Start the real API server (its own process) and wait until ``/health`` answers.

    The server needs ``fastapi``/``uvicorn``, which the core install does not carry. That is
    checked **before** a child process is spawned: the child would die during import and the
    caller would see ``RuntimeError: the API server exited during startup (code 1)`` — an
    internal error with a traceback, from a missing extra that was declared nowhere. The
    refusal is structured instead (exit ``3``, the extra, the install command).
    """
    status = api_driver_status()
    if not status["available"]:
        raise DriverUnavailable(status)
    import secrets
    import tempfile

    from . import __file__ as package_file

    source_root = Path(str(package_file)).resolve().parents[1]
    # a directory this call created itself: never the repository, never the owner's runtime, and
    # never a path a sibling block happens to use
    workdir = (
        Path(runtime_dir)
        if runtime_dir
        else Path(tempfile.mkdtemp(prefix="skaldir-adversarial-api-"))
    )
    workdir.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    token = secrets.token_urlsafe(32)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(source_root),
        "SKALDIR_API_TOKEN": token,
        "SKALDIR_HOME": str(workdir),
        "PYTHONUNBUFFERED": "1",
    }
    process = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-c", API_BOOTSTRAP, str(port), str(workdir)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(workdir),
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + max(5.0, float(timeout_s))
    last = ""
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"the API server exited during startup (code {process.returncode})"
            )
        try:
            response = httpx.get(f"{url}/health", timeout=2.0)
            if response.status_code == 200:
                return ApiProcess(
                    url=url, token=token, process=process, runtime_dir=str(workdir)
                )
            last = f"status {response.status_code}"
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        time.sleep(0.5)
    process.terminate()
    raise RuntimeError(f"the API server did not answer /health within {timeout_s}s ({last})")


def default_state_dir() -> Path:
    """Where run reports live: ``<runtime>/adversarial`` (never the repository)."""
    try:
        from .registry import default_runtime_dir

        base = Path(default_runtime_dir())
    except Exception:  # noqa: BLE001 — a runtime that cannot be resolved must not break a run
        from .env import getenv

        base = Path(getenv("HOME") or (Path.home() / ".hermes" / "skaldir"))
    return base / "adversarial"


__all__ = [
    "ACTION_SPACE",
    "API_BOOTSTRAP",
    "API_EXTRA",
    "API_INSTALL_COMMANDS",
    "ApiProcess",
    "AttackCase",
    "BROWSER_DRIVER",
    "BROWSER_DRIVERS",
    "BROWSER_EXTRA",
    "BROWSER_INSTALL_COMMANDS",
    "BrowserPool",
    "CANARY_DECLARED_ANCHORS",
    "CANARY_DECLARED_HINT",
    "CANARY_DECLARED_OWNED",
    "CANARY_DECLARED_PRIMARY_LABEL",
    "CANARY_EXPECTED",
    "CANARY_MODES",
    "CASE_BY_ID",
    "CASE_BY_PAIR",
    "CanaryApp",
    "CaseResult",
    "DECISIONS_SUFFIX",
    "DEFECT_ELEMENT_MISSING",
    "DEFECT_LAYOUT",
    "DEFECT_TEXT_MISMATCH",
    "DeclaredSpaceAdvisor",
    "DriverUnavailable",
    "EXIT_BLOCKED",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_VERIFICATION_FAILED",
    "OUTCOMES",
    "ORACLES",
    "PlannedCase",
    "PlannerUnavailable",
    "SUITE_VERSION",
    "SelfCheck",
    "SuitePlan",
    "SuiteReport",
    "TargetSurface",
    "Unverifiable",
    "VERDICTS",
    "api_driver_status",
    "browser_driver_status",
    "catalogue_for",
    "decision_ledger",
    "default_state_dir",
    "exit_code_for",
    "offered_surfaces",
    "plan_suite",
    "preflight",
    "record_run",
    "release_gate_payload",
    "reset_driver_status_cache",
    "run_suite",
    "selftest",
    "start_canary",
    "start_skaldir_api",
    "verdict_for",
]
