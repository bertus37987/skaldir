"""The four hard rules of MVP/03 §6 — encoded, not promised.

Jev is a fast decision layer *under* the agent. It may choose among options it
was handed; it may never decide a goal PASS, grant a permission, grant a
security exception or override a user goal. Those four sentences are enforced
three times over, so no single mistake can break them:

**Layer 1 — import time.** :func:`scan_contracts` walks every model in
``JEV_CONTRACTS`` and raises :class:`ImportError` (unshippable) if any contract
carries a field that could *hold* one of those decisions: a forbidden exact
field name, a forbidden substring in an **answer** model's field name, a
read-only input field leaking into an answer, or a forbidden Literal *value* in
an answer. The scan runs when :mod:`skaldir.jev.contracts` is imported.

**Layer 2 — construction.** Every pydantic model is ``frozen=True`` with
``extra="forbid"``; :class:`~skaldir.jev.contracts.JevDecision` re-checks itself in
an ``after`` validator through :func:`assert_decision_authority`, and
``decide()`` calls the same function again *with the request* so the echo
(``goal_revision``), the monotone-caution guard and the per-kind structural
rules (no denied route, protected memory kept, targets resolve to offered
surfaces) are enforced on every single decision.

**Layer 3 — test / audit.** :data:`RULES` names the tests that pin each rule;
:func:`skaldir.jev.selftest.selftest` refuses to claim a rule whose tests are not
present, and ``skaldir jev selftest`` exits ``1`` in that case.

The one deliberate exception in layer 1 is documented in
:data:`DECLARED_CONSTANT_FIELDS`: the envelope may carry a field *named* like a
grant, but only as a pinned constant (``grants: () = ()``, ``certifies: False``)
whose non-constant value is refused by a validator and by a test. The scan
itself refuses any other occurrence, so the allowlist cannot grow quietly.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..system_one import SystemOneContractError
from ..system_one_policy import (
    ROUTE_ESCALATE_SYSTEM_TWO,
    RULE_DRIFT_NON_GOAL_CONFLICT,
    RULE_DRIFT_SCOPE_EXPANSION,
    RULE_LOOP_WITHOUT_PROGRESS,
    RULE_UNTRUSTED_AUTHORITY_ATTEMPT,
    RULE_UNTRUSTED_SECRET_REQUEST,
)

# --------------------------------------------------------------------------- #
# the vocabulary the enforcement speaks                                         #
# --------------------------------------------------------------------------- #

AUTHORITY_ADVISORY = "advisory"

#: ``ok`` = an advisory exists; ``refused`` = Jev declined (deterministically
#: detected precondition); ``blocked`` = no provider was available.
ALLOWED_STATUS: tuple[str, ...] = ("ok", "refused", "blocked")

#: The four honest provider labels of the substrate (§4.2) — re-stated here so a
#: contract can be scanned without importing the substrate's answer module.
ALLOWED_PROVIDER_MODES: tuple[str, ...] = (
    "typesafe_jev_live",
    "llm_system_one_adapter",
    "replay_fixture",
    "disabled",
)

#: A caution floor this high means "do not stay silent".
HARD_CAUTION_FLOOR = 3

# --------------------------------------------------------------------------- #
# R1..R4 — rule ids and their statements                                        #
# --------------------------------------------------------------------------- #

RULE_R1 = "R1_no_goal_pass"
RULE_R2 = "R2_no_permission_grant"
RULE_R3 = "R3_no_security_exception"
RULE_R4 = "R4_no_goal_override"


class JevRuleViolation(SystemOneContractError):
    """A hard rule (R1–R4) was violated — never silently downgraded.

    Subclass of the substrate's :class:`SystemOneContractError`, so an existing
    consumer that catches contract errors keeps working; the ``rule`` attribute
    names which of the four invariants was hit.
    """

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"{rule}: {message}")
        self.rule = rule


# --------------------------------------------------------------------------- #
# layer 1 — the forbidden-name vocabulary                                       #
# --------------------------------------------------------------------------- #

#: Exact field names no Jev contract may carry (MVP/03 §6, the four rule rows).
FORBIDDEN_EXACT: frozenset[str] = frozenset(
    {
        # R1 — anything that could BE a goal verdict
        "goal_status",
        "verdict",
        "pass",
        "fail",
        "release_decision",
        "certify",
        "certifies",
        "certified",
        "signature",
        # R2 — anything that could BE a permission
        "grant",
        "grants",
        "permission",
        "permissions",
        "allow",
        "allowed_override",
        "authorization",
        "bypass",
        "approval_granted",
        # R3 — anything that could BE a security exception
        "exception",
        "security_override",
        "override",
        "risk_acceptance",
        "severity",
        "caution_floor_reset",
        # R4 — anything that could BE a goal rewrite
        "goal_text_out",
        "goal_patch",
        "new_goal",
        "goal_override",
        "revision_bump",
    }
)

#: Substrings that may not appear in the name of any **answer** field. Applied to
#: answer models only: request models legitimately carry the caller's own
#: read-only facts (``permission_state``), and those are covered by
#: :data:`INPUT_ONLY_FIELDS` instead.
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "pass",
    "certif",
    "grant",
    "permission",
    "exception",
    "security_override",
    "risk_accept",
    "goal_override",
    "goal_patch",
    "new_goal",
    "bypass",
    "authoriz",
)

#: Fields that exist **only** as a pinned constant on the envelope. The scan
#: requires the declared default to equal the constant exactly, so the field can
#: never hold anything else; the validator and the tests prove the refusal.
DECLARED_CONSTANT_FIELDS: dict[str, dict[str, Any]] = {
    "JevDecision": {"grants": (), "certifies": False},
}

#: Read-only inputs: allowed on a *request* model, refused on any answer model —
#: "a value Jev was told" must never look like "a value Jev produced".
INPUT_ONLY_FIELDS: frozenset[str] = frozenset(
    {
        "permission_state",
        "allow_network",
        "allow_approval_required",
        "risk_class_max",
        "caution_baseline",
    }
)

#: Literal *values* an answer may never choose. ``CaseOutcome.outcome`` (an input
#: recording what a runner measured) is exempt by model kind — the scan only walks
#: answer models.
FORBIDDEN_ANSWER_VALUES: frozenset[str] = frozenset(
    {
        "pass",
        "passed",
        "fail",
        "failed",
        "granted",
        "grant",
        "approved",
        "certified",
        "accept_risk",
        "accepted_risk",
        "override",
        "exception_granted",
    }
)

#: Snippets that would make model output executable if they ever reached an
#: answer (MVP/03 §5.6: "no field for a selector, a script or coordinates").
FORBIDDEN_PAYLOAD_SNIPPETS: tuple[str, ...] = (
    "{",
    "}",
    "$(",
    "document.",
    "javascript:",
    "<script",
    "xpath(",
    "css=",
)


def _schema_names(schema: Mapping[str, Any]) -> set[str]:
    """Every property name in a JSON schema, at any depth."""
    names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            properties = node.get("properties")
            if isinstance(properties, Mapping):
                names.update(str(key) for key in properties)
            for value in node.values():
                walk(value)
        elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
            for item in node:
                walk(item)

    walk(schema)
    return names


def _literal_values(annotation: Any) -> tuple[str, ...]:
    """The string Literal values of an annotation (``Literal``, union, container)."""
    from typing import get_args, get_origin

    origin = get_origin(annotation)
    if origin is None:
        return ()
    found: list[str] = []
    for arg in get_args(annotation):
        if str(origin).endswith("Literal"):
            if isinstance(arg, str):
                found.append(arg)
        else:
            found.extend(_literal_values(arg))
    return tuple(found)


@dataclass(frozen=True)
class ContractFinding:
    """One layer-1 finding — a contract field that could carry a decision."""

    rule: str
    model: str
    field: str
    reason: str

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "model": self.model,
            "field": self.field,
            "reason": self.reason,
        }


def scan_contracts(
    models: Iterable[Any] | None = None,
    *,
    answer_names: frozenset[str] | None = None,
) -> dict:
    """Layer 1: scan every Jev contract; raise :class:`ImportError` on a finding.

    Returns a report (models scanned, fields scanned, the vocabulary in force)
    so the CLI self-audit can print what was actually checked. ``models``
    defaults to ``JEV_CONTRACTS`` and is imported lazily — this module must stay
    importable *before* the models exist, or the scan could not run at their
    import time. ``answer_names`` is overridable so a test can feed the scan a
    synthetic model and still be held to the stricter answer-side rules.
    """
    if models is None:
        from .contracts import JEV_CONTRACTS

        models = JEV_CONTRACTS

    findings: list[ContractFinding] = []
    model_names: list[str] = []
    field_count = 0
    if answer_names is None:
        answer_names = {model.__name__ for model in _scanned_answer_models()}

    for model in models:
        name = model.__name__
        model_names.append(name)
        constants = DECLARED_CONSTANT_FIELDS.get(name, {})
        for field_name, info in model.model_fields.items():
            field_count += 1
            if field_name in FORBIDDEN_EXACT:
                if field_name in constants:
                    declared = info.default
                    if declared != constants[field_name]:
                        findings.append(
                            ContractFinding(
                                RULE_R1,
                                name,
                                field_name,
                                f"pinned constant drifted: default is {declared!r}, "
                                f"must stay {constants[field_name]!r}",
                            )
                        )
                else:
                    findings.append(
                        ContractFinding(
                            RULE_R1 if field_name in _R1_NAMES else _rule_for_name(field_name),
                            name,
                            field_name,
                            "forbidden exact field name (MVP/03 §6)",
                        )
                    )
                continue
            if field_name in INPUT_ONLY_FIELDS:
                if name in answer_names:
                    findings.append(
                        ContractFinding(
                            RULE_R2,
                            name,
                            field_name,
                            "a read-only input must never appear on an answer "
                            "(MVP/03 §6 R2)",
                        )
                    )
                continue
            if name in answer_names:
                lowered = field_name.lower()
                for needle in FORBIDDEN_SUBSTRINGS:
                    if needle in lowered:
                        findings.append(
                            ContractFinding(
                                _rule_for_name(needle),
                                name,
                                field_name,
                                f"answer field name contains {needle!r} — a value Jev "
                                "produced could then read as a decision it never had "
                                "(MVP/03 §6)",
                            )
                        )
                        break

        if name in answer_names:
            for field_name, info in model.model_fields.items():
                for value in _literal_values(info.annotation):
                    if value.strip().lower() in FORBIDDEN_ANSWER_VALUES:
                        findings.append(
                            ContractFinding(
                                RULE_R1,
                                name,
                                field_name,
                                f"answer Literal value {value!r} is a verdict word "
                                "(MVP/03 §6 R1)",
                            )
                        )

        if name in answers_with_schema():
            for leaked in sorted(_schema_names(model.model_json_schema()) & FORBIDDEN_EXACT):
                if leaked in constants:
                    continue
                findings.append(
                    ContractFinding(
                        _rule_for_name(leaked),
                        name,
                        leaked,
                        "forbidden name present in the model's JSON schema",
                    )
                )

    if findings:
        detail = "; ".join(
            f"{finding.rule} {finding.model}.{finding.field}: {finding.reason}"
            for finding in findings
        )
        raise ImportError(f"Jev contract scan failed ({len(findings)}): {detail}")

    return {
        "models": tuple(model_names),
        "model_count": len(model_names),
        "field_count": field_count,
        "forbidden_exact": tuple(sorted(FORBIDDEN_EXACT)),
        "forbidden_substrings": FORBIDDEN_SUBSTRINGS,
        "input_only_fields": tuple(sorted(INPUT_ONLY_FIELDS)),
        "constant_fields": {
            model: dict(fields) for model, fields in DECLARED_CONSTANT_FIELDS.items()
        },
        "answer_models": tuple(sorted(answer_names)),
    }


_R1_NAMES = frozenset(
    {
        "goal_status",
        "verdict",
        "pass",
        "fail",
        "release_decision",
        "certify",
        "certifies",
        "certified",
        "signature",
    }
)
_R4_NAMES = frozenset(
    {"goal_text_out", "goal_patch", "new_goal", "goal_override", "revision_bump"}
)


def _rule_for_name(needle: str) -> str:
    if needle in _R1_NAMES:
        return RULE_R1
    if needle in _R4_NAMES:
        return RULE_R4
    if needle in {"exception", "security_override", "override", "risk_acceptance",
                  "severity", "caution_floor_reset"}:
        return RULE_R3
    return RULE_R2


def _answer_models() -> tuple[Any, ...]:
    from .contracts import JEV_ANSWERS

    return tuple(JEV_ANSWERS.values())


def _scanned_answer_models() -> tuple[Any, ...]:
    """The models whose fields the substring/JSON-schema scan applies to.

    The six kind answers plus the envelope itself: the envelope carries
    ``grants``/``certifies`` as pinned constants (see
    :data:`DECLARED_CONSTANT_FIELDS`) and everything else about it must still be
    free of a verdict word.
    """
    from .contracts import JEV_ANSWERS, JevDecision

    return (*tuple(JEV_ANSWERS.values()), JevDecision)


def answers_with_schema() -> frozenset[str]:
    """The answer model names (their JSON schema is walked as well)."""
    return frozenset(model.__name__ for model in _scanned_answer_models())


# --------------------------------------------------------------------------- #
# layer 2 — construction-time enforcement                                       #
# --------------------------------------------------------------------------- #


def assert_decision_authority(decision: Any, *, request: Any | None = None) -> Any:
    """Re-check the four rules on a constructed decision; return it unchanged.

    Called twice on every decision: once by the model's own ``after`` validator
    (where only the decision is available) and once by ``decide()`` with the
    request in hand, which is what makes the ``goal_revision`` echo, the
    monotone-caution guard and the per-kind structural rules unconditional.
    """
    authority = getattr(decision, "authority", None)
    if authority != AUTHORITY_ADVISORY:
        raise JevRuleViolation(
            RULE_R1, f"authority must stay {AUTHORITY_ADVISORY!r}, got {authority!r}"
        )
    if getattr(decision, "certifies", None) is not False:
        raise JevRuleViolation(RULE_R1, "a Jev decision may never certify anything")
    grants = tuple(getattr(decision, "grants", ()) or ())
    if grants:
        raise JevRuleViolation(
            RULE_R2, f"grants must stay empty, got {list(grants)!r}"
        )

    status = getattr(decision, "status", None)
    if status not in ALLOWED_STATUS:
        raise JevRuleViolation(RULE_R1, f"unknown status {status!r}")
    refusal_reason = str(getattr(decision, "refusal_reason", "") or "")
    if (status == "ok") != (refusal_reason == ""):
        raise JevRuleViolation(
            RULE_R1,
            f"status {status!r} and refusal_reason {refusal_reason!r} contradict each other",
        )

    kind = str(getattr(decision, "kind", "") or "")
    answer = getattr(decision, "answer", None)
    expected = _answer_model_for(kind)
    if expected is None:
        raise JevRuleViolation(RULE_R1, f"unknown decision kind {kind!r}")
    if not isinstance(answer, expected):
        raise JevRuleViolation(
            RULE_R1,
            f"answer for kind {kind!r} must be a {expected.__name__}, "
            f"got {type(answer).__name__}",
        )

    caution_level = int(getattr(decision, "caution_level", 0))
    caution_baseline = int(getattr(decision, "caution_baseline", 0))
    if not 0 <= caution_level <= HARD_CAUTION_FLOOR:
        raise JevRuleViolation(
            RULE_R3, f"caution_level {caution_level} is outside 0..{HARD_CAUTION_FLOOR}"
        )
    if caution_level < caution_baseline:
        raise JevRuleViolation(
            RULE_R3,
            f"caution_level {caution_level} is below its own baseline "
            f"{caution_baseline} — caution is monotone",
        )

    if request is not None:
        assert_goal_revision_echo(request, decision)
        floor = caution_floor_for(request)
        if caution_level < floor:
            raise JevRuleViolation(
                RULE_R3,
                f"caution_level {caution_level} is below the floor {floor} this request "
                "carries — Jev may raise caution, never lower it",
            )
        if str(getattr(decision, "run_id", "")) != str(getattr(request, "run_id", "")):
            raise JevRuleViolation(RULE_R4, "run_id is an echo of the request")
        if str(getattr(decision, "goal_id", "")) != str(getattr(request, "goal_id", "")):
            raise JevRuleViolation(RULE_R4, "goal_id is an echo of the request")
        if str(getattr(decision, "kind", "")) != str(getattr(request, "kind", "")):
            raise JevRuleViolation(
                RULE_R4,
                f"decision kind {getattr(decision, 'kind', '')!r} must echo the request's "
                f"{getattr(request, 'kind', '')!r}",
            )
        _assert_kind_rules(request, decision)
    return decision


def _answer_model_for(kind: str) -> Any | None:
    from .contracts import JEV_ANSWERS

    return JEV_ANSWERS.get(kind)


def assert_goal_revision_echo(request: Any, decision: Any) -> None:
    """R4: the decision carries the request's ``goal_revision``, or it is refused."""
    wanted = int(getattr(request, "goal_revision", 0))
    got = int(getattr(decision, "goal_revision", -1))
    if got != wanted:
        raise JevRuleViolation(
            RULE_R4,
            f"goal_revision must echo the request ({wanted}), got {got} — Jev never "
            "writes a goal revision",
        )


def assert_monotone_caution(request: Any, caution_level: int) -> None:
    """R3: the answer's caution may be raised, never lowered (MVP/03 §6 R3)."""
    baseline = int(getattr(request, "caution_baseline", 0))
    if int(caution_level) < baseline:
        raise JevRuleViolation(
            RULE_R3,
            f"caution_level {caution_level} < request baseline {baseline}",
        )
    floor = caution_floor_for(request)
    if int(caution_level) < floor:
        raise JevRuleViolation(
            RULE_R3, f"caution_level {caution_level} < hard floor {floor}"
        )


def caution_floor_for(request: Any) -> int:
    """The caution a request may not be answered below (MVP/03 §6 R3).

    Deterministic, in code, readable in one place:

    * the request's own ``caution_baseline`` always counts;
    * a ``voice_triage`` request about ``untrusted_content`` floors at 2; when
      the same request carries an authority or secret attempt signal it floors
      at :data:`HARD_CAUTION_FLOOR`, which is what forbids ``stay_silent``;
    * an ``adversarial_test_planning`` request touching a ``permission_boundary``
      surface floors at 2;
    * ``tool_routing`` over a candidate set that contains a denied candidate
      floors at 1 (the denial is visible, so silence is not an option).
    """
    floor = int(getattr(request, "caution_baseline", 0) or 0)
    kind = str(getattr(request, "kind", "") or "")
    if kind == "voice_triage":
        trigger = str(getattr(request, "trigger_kind", "") or "")
        if trigger == "untrusted_content":
            floor = max(floor, 2)
            signals = getattr(request, "signals", None) or {}
            if isinstance(signals, Mapping):
                for key, value in signals.items():
                    lowered = str(key).lower()
                    if not value or not isinstance(value, (bool, int, float)):
                        continue
                    if any(
                        marker in lowered
                        for marker in (
                            "authority",
                            "secret",
                            "credential",
                            "exfiltrat",
                            "injection",
                        )
                    ):
                        floor = HARD_CAUTION_FLOOR
                        break
    elif kind == "adversarial_test_planning":
        for surface in tuple(getattr(request, "surfaces", ()) or ()):
            if str(getattr(surface, "kind", "")) == "permission_boundary":
                floor = max(floor, 2)
    elif kind == "tool_routing":
        for candidate in tuple(getattr(request, "candidates", ()) or ()):
            if str(getattr(candidate, "permission_state", "")) == "denied":
                floor = max(floor, 1)
    return min(floor, HARD_CAUTION_FLOOR)


# --------------------------------------------------------------------------- #
# per-kind structural rules (layer 2, request-aware)                            #
# --------------------------------------------------------------------------- #


def _assert_kind_rules(request: Any, decision: Any) -> None:
    kind = str(getattr(decision, "kind", ""))
    if getattr(decision, "status", "") != "ok":
        return
    if kind == "memory_injection":
        assert_protected_memories_kept(request, decision.answer)
        assert_injection_budget(request, decision.answer)
    elif kind == "tool_routing":
        assert_routes_are_offered(request, decision.answer)
    elif kind == "capability_ranking":
        assert_ranking_is_offered(request, decision.answer)
    elif kind == "context_compaction":
        assert_compaction_is_structural(request, decision.answer)
    elif kind == "voice_triage":
        assert_voice_cannot_be_silenced(request, decision.answer)
    elif kind == "adversarial_test_planning":
        assert_targets_resolve_to_offered_surfaces(request, decision.answer)


def assert_protected_memories_kept(request: Any, answer: Any) -> None:
    """A ``protected`` (owner-authored) memory may not be dropped by Jev."""
    protected = {
        str(candidate.memory_id)
        for candidate in tuple(getattr(request, "candidates", ()) or ())
        if bool(getattr(candidate, "protected", False))
    }
    if not protected:
        return
    injected = {str(mid) for mid in tuple(getattr(answer, "injected", ()) or ())}
    verdicts = {
        str(verdict.memory_id): str(verdict.read)
        for verdict in tuple(getattr(answer, "verdicts", ()) or ())
    }
    for memory_id in sorted(protected):
        if verdicts.get(memory_id) == "drop":
            raise JevRuleViolation(
                RULE_R4,
                f"memory {memory_id!r} is owner-authored (protected) — Jev may not drop it",
            )
        if memory_id not in injected:
            raise JevRuleViolation(
                RULE_R4,
                f"memory {memory_id!r} is owner-authored (protected) and must be "
                "injected verbatim",
            )


def assert_injection_budget(request: Any, answer: Any) -> None:
    """The injected set is an offered subset, capped by the request's budget."""
    candidates = {
        str(candidate.memory_id)
        for candidate in tuple(getattr(request, "candidates", ()) or ())
    }
    injected = tuple(str(mid) for mid in tuple(getattr(answer, "injected", ()) or ()))
    unknown = sorted(set(injected) - candidates)
    if unknown:
        raise JevRuleViolation(
            RULE_R1, f"injected ids were never offered: {unknown}"
        )
    budget = getattr(request, "budget", None)
    max_inject = int(getattr(budget, "max_inject", 0) or 0)
    protected = len(
        [
            candidate
            for candidate in tuple(getattr(request, "candidates", ()) or ())
            if bool(getattr(candidate, "protected", False))
        ]
    )
    limit = max(max_inject, protected)
    if limit and len(injected) > limit:
        raise JevRuleViolation(
            RULE_R1,
            f"{len(injected)} injected ids exceed the request budget of {max_inject}"
            + (f" (raised to {limit} for owner-authored memories)" if protected else ""),
        )
    ordering = tuple(str(mid) for mid in tuple(getattr(answer, "ordering", ()) or ()))
    if set(ordering) - set(injected):
        raise JevRuleViolation(
            RULE_R1, "ordering may only order the injected ids, never add one"
        )


def assert_routes_are_offered(request: Any, answer: Any) -> None:
    """R2: every named route is offered, and a **denied** candidate is unreachable."""
    offered = {
        str(candidate.candidate_id): str(candidate.permission_state)
        for candidate in tuple(getattr(request, "candidates", ()) or ())
    }
    chosen_kind = str(getattr(answer, "chosen_kind", ""))
    route = getattr(answer, "route", None)
    named: list[str] = []
    if route is not None:
        named.append(str(route.candidate_id))
    named.extend(
        str(alternative.candidate_id)
        for alternative in tuple(getattr(answer, "alternatives", ()) or ())
    )
    unknown = sorted({name for name in named if name not in offered})
    if unknown:
        raise JevRuleViolation(
            RULE_R1, f"routes were never offered: {unknown}"
        )
    denied = sorted(
        {name for name in named if offered.get(name) == "denied"}
    )
    if denied:
        raise JevRuleViolation(
            RULE_R2,
            f"a denied candidate may never be routed to: {denied} — the permission "
            "state is copied from the authorization gate, and Jev cannot change it",
        )
    if chosen_kind == "route" and route is None:
        raise JevRuleViolation(RULE_R1, "chosen_kind 'route' needs a route")
    if chosen_kind != "route" and route is not None:
        raise JevRuleViolation(
            RULE_R1, f"chosen_kind {chosen_kind!r} must not name a route"
        )


def assert_ranking_is_offered(request: Any, answer: Any) -> None:
    """A ranking only ranks offered candidates, with contiguous 1-based ranks."""
    offered = {
        str(candidate.candidate_id)
        for candidate in tuple(getattr(request, "candidates", ()) or ())
    }
    entries = tuple(getattr(answer, "ranking", ()) or ())
    unknown = sorted({str(entry.candidate_id) for entry in entries} - offered)
    if unknown:
        raise JevRuleViolation(RULE_R1, f"ranked ids were never offered: {unknown}")
    ranks = [int(entry.rank) for entry in entries]
    if ranks and sorted(ranks) != list(range(1, len(ranks) + 1)):
        raise JevRuleViolation(
            RULE_R1, f"ranks must be 1..n without gaps or ties, got {ranks}"
        )
    best_fit = getattr(answer, "best_fit", None)
    if best_fit is not None and str(best_fit) not in offered:
        raise JevRuleViolation(
            RULE_R1, f"best_fit {best_fit!r} was never offered"
        )


def assert_compaction_is_structural(request: Any, answer: Any) -> None:
    """Compaction has no text field: only keep/drop_result/drop_call, consistently."""
    options = getattr(request, "options", None)
    truncate_head = int(getattr(options, "truncate_head_chars", 0) or 0)
    for call in tuple(getattr(answer, "calls", ()) or ()):
        action = str(getattr(call, "action", ""))
        reason = str(getattr(call, "reason", ""))
        head = int(getattr(call, "head_chars_kept", 0) or 0)
        if action == "keep" and reason not in ("pinned", "kept"):
            raise JevRuleViolation(
                RULE_R1, f"call {call.id!r}: action 'keep' needs reason pinned|kept"
            )
        if action == "drop_result":
            if reason != "result_dropped":
                raise JevRuleViolation(
                    RULE_R1, f"call {call.id!r}: action 'drop_result' needs result_dropped"
                )
            if not 0 <= head <= truncate_head:
                raise JevRuleViolation(
                    RULE_R1,
                    f"call {call.id!r}: head_chars_kept {head} exceeds the request's "
                    f"truncate_head_chars {truncate_head}",
                )
        if action == "drop_call":
            if reason != "call_dropped" or head:
                raise JevRuleViolation(
                    RULE_R1,
                    f"call {call.id!r}: a dropped call keeps no head chars",
                )
    for call in tuple(getattr(answer, "calls", ()) or ()):
        if int(getattr(call, "head_chars_kept", 0) or 0) > int(
            getattr(answer.stats, "chars_before", 0) or 0
        ):
            raise JevRuleViolation(
                RULE_R1,
                f"call {call.id!r}: kept more chars than the history held",
            )


def assert_voice_cannot_be_silenced(request: Any, answer: Any) -> None:
    """R3: a hard caution floor forbids ``stay_silent``; route/flag must agree."""
    chosen = str(getattr(answer, "chosen", ""))
    route_hint = str(getattr(answer, "route_hint", ""))
    escalate = bool(getattr(answer, "escalate_system_two", False))
    if escalate != (route_hint == ROUTE_ESCALATE_SYSTEM_TWO):
        raise JevRuleViolation(
            RULE_R1,
            f"escalate_system_two ({escalate}) contradicts route_hint {route_hint!r}",
        )
    floor = caution_floor_for(request)
    if floor >= HARD_CAUTION_FLOOR and chosen == "stay_silent":
        trigger = str(getattr(request, "trigger_kind", ""))
        rule = (
            RULE_UNTRUSTED_SECRET_REQUEST
            if any(
                "secret" in str(key).lower() or "credential" in str(key).lower()
                for key in (getattr(request, "signals", None) or {})
            )
            else RULE_UNTRUSTED_AUTHORITY_ATTEMPT
        )
        raise JevRuleViolation(
            RULE_R3,
            f"trigger {trigger!r} carries a caution floor of {floor}: Jev may not answer "
            f"'stay_silent' (policy rule {rule})",
        )
    if floor >= HARD_CAUTION_FLOOR and route_hint in ("observe", ""):
        raise JevRuleViolation(
            RULE_R3,
            f"a caution floor of {floor} forbids the inert route {route_hint!r}",
        )


def assert_targets_resolve_to_offered_surfaces(request: Any, answer: Any) -> None:
    """Every planned case targets an offered surface — never a selector or a script."""
    surfaces = {
        str(surface.surface_id): tuple(str(op) for op in (surface.seed_operations or ()))
        for surface in tuple(getattr(request, "surfaces", ()) or ())
    }
    allow_network = bool(getattr(getattr(request, "budget", None), "allow_network", False))
    plan = tuple(getattr(answer, "plan", ()) or ())
    chosen = str(getattr(answer, "chosen", ""))
    if chosen == "plan" and not plan:
        raise JevRuleViolation(RULE_R1, "chosen 'plan' needs at least one case")
    for case in plan:
        surface_id = str(getattr(case, "surface_id", ""))
        target_ref = str(getattr(case, "target_ref", ""))
        if surface_id not in surfaces:
            raise JevRuleViolation(
                RULE_R1, f"case {case.case_id!r} targets an unoffered surface {surface_id!r}"
            )
        if not target_ref.startswith(surface_id):
            raise JevRuleViolation(
                RULE_R1,
                f"case {case.case_id!r} target_ref {target_ref!r} does not resolve to the "
                f"offered surface {surface_id!r}",
            )
        if not allow_network and "http_request" in surfaces.get(surface_id, ()):
            raise JevRuleViolation(
                RULE_R1,
                f"case {case.case_id!r} needs network while the request forbids it",
            )
    for _path, value in _strings(answer):
        lowered = str(value).lower()
        for snippet in FORBIDDEN_PAYLOAD_SNIPPETS:
            if snippet in lowered:
                raise JevRuleViolation(
                    RULE_R1,
                    f"answer carries {snippet!r} — model output must never be a "
                    "selector, a script or coordinates (MVP/03 §5.6)",
                )


def _strings(node: Any, path: str = "answer") -> list[tuple[str, str]]:
    """Every string value anywhere in a model/pydantic tree, with its path."""
    found: list[tuple[str, str]] = []
    if isinstance(node, str):
        return [(path, node)]
    if isinstance(node, Mapping):
        for key, value in node.items():
            found.extend(_strings(value, f"{path}.{key}"))
        return found
    if isinstance(node, (list, tuple, set, frozenset)):
        for index, item in enumerate(node):
            found.extend(_strings(item, f"{path}[{index}]"))
        return found
    fields = getattr(type(node), "model_fields", None)
    if fields:
        for name in fields:
            found.extend(_strings(getattr(node, name), f"{path}.{name}"))
    return found


# --------------------------------------------------------------------------- #
# the rule table (layer 3 reads this)                                           #
# --------------------------------------------------------------------------- #

RULE_TESTS_FILE = "tests/test_jev_facade_rules.py"


@dataclass(frozen=True)
class RuleSpec:
    """One hard rule: statement, both enforcement layers, and the tests that pin it."""

    rule: str
    statement: str
    layer1: str
    layer2: str
    tests: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "statement": self.statement,
            "layer1": self.layer1,
            "layer2": self.layer2,
            "tests": list(self.tests),
            "notes": list(self.notes),
        }


RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        RULE_R1,
        "Jev never returns a goal PASS and never certifies anything.",
        "scan_contracts(): forbidden exact names {goal_status, verdict, pass, fail, "
        "release_decision, certify, certifies, certified, signature}; forbidden "
        "verdict Literals in answer models; the JSON schema of every answer is walked "
        "as well.",
        "authority is Literal['advisory']; certifies is Literal[False]; grants is a "
        "pinned empty tuple; status/refusal_reason must agree; the answer model must be "
        "the one registered for the kind — re-checked by assert_decision_authority().",
        (
            f"{RULE_TESTS_FILE}::test_no_contract_can_certify",
            f"{RULE_TESTS_FILE}::test_authority_is_advisory_and_grants_stay_empty",
            f"{RULE_TESTS_FILE}::test_completion_gate_never_reads_jev",
        ),
    ),
    RuleSpec(
        RULE_R2,
        "Jev never grants a permission and never overrides the authorization gate.",
        "forbidden exact names {grant, grants, permission, permissions, allow, "
        "allowed_override, authorization, bypass, approval_granted}; permission_state "
        "is declared a read-only input (INPUT_ONLY_FIELDS) and refused on any answer.",
        "grants stays (); a routing answer naming a candidate whose input "
        "permission_state is 'denied' raises; a candidate set that is denied-only "
        "short-circuits to no_route/permission_denied_only without asking a provider.",
        (
            f"{RULE_TESTS_FILE}::test_jev_routing_cannot_upgrade_permission",
            f"{RULE_TESTS_FILE}::test_denied_only_candidates_short_circuit_to_no_route",
            f"{RULE_TESTS_FILE}::test_jev_does_not_bypass_authorization_gate",
        ),
    ),
    RuleSpec(
        RULE_R3,
        "Jev never grants a security exception and never lowers caution.",
        "forbidden exact names {exception, security_override, override, "
        "risk_acceptance, severity, caution_floor_reset}; no Jev module may be "
        "imported by guard.py / untrusted.py / secretscan.py.",
        "assert_monotone_caution / caution_floor_for: caution_level >= the request "
        "baseline and >= the request's floor; a floor of 3 forbids 'stay_silent' and "
        "the inert 'observe' route (untrusted_content with an authority/secret "
        "attempt).",
        (
            f"{RULE_TESTS_FILE}::test_jev_caution_is_monotone",
            f"{RULE_TESTS_FILE}::test_untrusted_trigger_cannot_be_silenced",
            f"{RULE_TESTS_FILE}::test_jev_not_imported_by_security_modules",
        ),
    ),
    RuleSpec(
        RULE_R4,
        "Jev never overrides a user goal: the goal is input-only and echoed, not written.",
        "forbidden exact names {goal_text_out, goal_patch, new_goal, goal_override, "
        "goal_status, revision_bump}; goal text exists only on request models.",
        "goal_revision and goal_id are echoes (refused on mismatch); request models "
        "are frozen and carry no mutable goal object; an owner-authored (protected) "
        "memory may not be dropped; the compaction pinning (message 0, the protected "
        "message ids and the newest ``preserve_recent_messages`` entries) keeps a "
        "protected call in the transcript, and the caller still owns the pact file "
        f"(the substrate's policy rules {RULE_LOOP_WITHOUT_PROGRESS}, "
        f"{RULE_DRIFT_NON_GOAL_CONFLICT}, {RULE_DRIFT_SCOPE_EXPANSION} stay the "
        "caller's).",
        (
            f"{RULE_TESTS_FILE}::test_jev_never_mutates_goal",
            f"{RULE_TESTS_FILE}::test_goal_revision_echo_enforced",
        ),
    ),
)

RULES_BY_ID: dict[str, RuleSpec] = {spec.rule: spec for spec in RULES}


def missing_rule_tests(root: Any | None = None) -> dict[str, tuple[str, ...]]:
    """For every rule, which of its pinned tests are **absent** from the file.

    Layer 3 as a function instead of a promise: ``skaldir jev selftest`` exits
    ``1`` when this is non-empty. The check reads the test file's source and
    looks for the test function name, so renaming or deleting a test is caught.
    """
    from pathlib import Path

    base = Path(root) if root is not None else Path(__file__).resolve().parents[3]
    path = base / RULE_TESTS_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    missing: dict[str, tuple[str, ...]] = {}
    for spec in RULES:
        absent = []
        for node_id in spec.tests:
            name = node_id.split("::")[-1]
            if f"def {name}(" not in text:
                absent.append(node_id)
        if absent:
            missing[spec.rule] = tuple(absent)
    return missing


def rule_report(root: Any | None = None) -> list[dict]:
    """The R1–R4 table as data, with the test presence actually measured."""
    missing = missing_rule_tests(root)
    report = []
    for spec in RULES:
        payload = spec.as_dict()
        payload["test_present"] = spec.rule not in missing
        payload["missing_tests"] = list(missing.get(spec.rule, ()))
        report.append(payload)
    return report


__all__ = [
    "ALLOWED_PROVIDER_MODES",
    "ALLOWED_STATUS",
    "AUTHORITY_ADVISORY",
    "DECLARED_CONSTANT_FIELDS",
    "FORBIDDEN_ANSWER_VALUES",
    "FORBIDDEN_EXACT",
    "FORBIDDEN_PAYLOAD_SNIPPETS",
    "FORBIDDEN_SUBSTRINGS",
    "HARD_CAUTION_FLOOR",
    "INPUT_ONLY_FIELDS",
    "JevRuleViolation",
    "RULES",
    "RULES_BY_ID",
    "RULE_R1",
    "RULE_R2",
    "RULE_R3",
    "RULE_R4",
    "RuleSpec",
    "assert_compaction_is_structural",
    "assert_decision_authority",
    "assert_goal_revision_echo",
    "assert_injection_budget",
    "assert_monotone_caution",
    "assert_protected_memories_kept",
    "assert_ranking_is_offered",
    "assert_routes_are_offered",
    "assert_targets_resolve_to_offered_surfaces",
    "assert_voice_cannot_be_silenced",
    "caution_floor_for",
    "missing_rule_tests",
    "rule_report",
    "scan_contracts",
]
