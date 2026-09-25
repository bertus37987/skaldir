"""The adversarial suite's contract: catalogue, planner, oracles, verdict and the canary target.

What this file pins (no browser needed — the browser cases are proved in
``tests/test_adversarial_browser.py``):

* the declared action space is internally consistent: every driver and oracle exists, the
  operation/oracle kind of a case matches the surface kind Jev maps it to, and every declared
  case is reachable through an offered surface;
* the planner asks the **Jev façade** (``adversarial_test_planning``) and says so — and when the
  façade cannot answer, the deterministic fallback plans the same catalogue and *claims*
  ``deterministic`` instead of pretending;
* a plan whose operation/oracle disagrees with the declared case, and a case whose oracle is
  not deterministic, is reported ``unknown`` — **never** a pass;
* the verdict rule: FAIL if any case failed, else UNKNOWN if any case is unknown, else PASS; an
  empty run is UNKNOWN (no cases, no certification);
* the release gate: 0 only for PASS, 2 for FAIL, 3 for an UNKNOWN suite unless the caller
  explicitly waived it in report mode — a release gate refuses the waiver;
* the canary target really has the broken and fixed behaviours the cases attack (server-side
  validation, escaping, idempotency, POST-redirect-GET, session expiry), so the browser proof
  is measuring a target and not a description of one.
"""

from __future__ import annotations

import importlib
import json
import time

import httpx
import pytest

from skaldir import adversarial as A

# --------------------------------------------------------------------------- #
# the declared action space                                                     #
# --------------------------------------------------------------------------- #


def test_every_declared_case_is_executable_and_consistent():
    assert A.ACTION_SPACE, "the suite has no cases"
    ids = [case.case_id for case in A.ACTION_SPACE]
    assert len(ids) == len(set(ids)), "two cases share an id"
    for case in A.ACTION_SPACE:
        assert case.driver in A.DRIVERS, f"{case.case_id}: unknown driver {case.driver}"
        assert case.oracle in A.ORACLES, f"{case.case_id}: unknown oracle {case.oracle}"
        assert case.driver in A.OBSERVED_BY, f"{case.case_id}: no observation source"
        assert case.intent_class in A.INTENT_CLASSES, f"{case.case_id}: unknown intent class"
        assert case.defect_class, f"{case.case_id}: a violation would be unnamed"
        assert case.expects and case.hostile, f"{case.case_id}: no declared intent"
        assert case.operation in ("click", "type_text", "http_request")
        assert case.oracle_kind in ("observed_state", "http_status", "exit_code")


def test_the_operation_and_oracle_kind_match_the_surface_kind_jev_maps():
    """Jev assigns operation/oracle from the surface kind; the catalogue must agree with it."""
    expected = {
        "ui_flow": ("click", "observed_state"),
        "api_endpoint": ("http_request", "http_status"),
    }
    surfaces = {
        case.surface_id: A.TargetSurface(
            surface_id=case.surface_id,
            kind="ui_flow" if case.surface_id == A.CANARY_SURFACE_ID else "api_endpoint",
            url="http://127.0.0.1:1",
            entry_hint="#form",
            seed_operations=(case.intent_class,),
        )
        for case in A.ACTION_SPACE
    }
    for case in A.ACTION_SPACE:
        want_operation, want_oracle = expected[surfaces[case.surface_id].kind]
        assert (case.operation, case.oracle_kind) == (want_operation, want_oracle), (
            f"{case.case_id}: the declared {case.operation}/{case.oracle_kind} is not what "
            f"Jev assigns for {surfaces[case.surface_id].kind}"
        )


def test_two_cases_may_share_a_pair_without_shadowing_each_other():
    """A pair is the *planning* granularity; the catalogue decides which case runs next."""
    boundary = A.CASE_BY_PAIR[(A.CANARY_SURFACE_ID, "boundary")]
    assert len(boundary) >= 2, "the boundary pair lost a case"
    first = A.next_case_for_pair((A.CANARY_SURFACE_ID, "boundary"))
    assert first is boundary[0]
    second = A.next_case_for_pair((A.CANARY_SURFACE_ID, "boundary"), {first.case_id: True})
    assert second is boundary[1] and second is not first
    covered = {case.case_id: True for case in boundary}
    assert A.next_case_for_pair((A.CANARY_SURFACE_ID, "boundary"), covered) is None


def test_every_case_is_reachable_through_an_offered_surface():
    surfaces = A.offered_surfaces(ui_url="http://127.0.0.1:1", api_url="http://127.0.0.1:2")
    reachable = {case.case_id for case in A.catalogue_for(surfaces)}
    assert reachable == {case.case_id for case in A.ACTION_SPACE}


def test_a_surface_without_a_url_is_not_offered():
    """An unconfigured target is not a test result: its cases are simply not planned."""
    assert A.offered_surfaces() == ()
    only_ui = A.offered_surfaces(ui_url="http://127.0.0.1:1")
    assert [surface.surface_id for surface in only_ui] == [A.CANARY_SURFACE_ID]
    only_api = A.offered_surfaces(api_url="http://127.0.0.1:2")
    assert [surface.surface_id for surface in only_api] == [A.SKALDIR_API_SURFACE_ID]


# --------------------------------------------------------------------------- #
# the planner                                                                   #
# --------------------------------------------------------------------------- #


def test_the_planner_asks_the_jev_facade_and_says_so():
    surfaces = A.offered_surfaces(ui_url="http://127.0.0.1:1")
    plan = A.plan_suite(surfaces=surfaces, slots=4, planner="auto")
    assert plan.planner == "jev", "the Jev façade is importable in this environment"
    assert plan.advisor == "declared_space"
    assert plan.decision_ids, "no Jev decision backs the plan"
    assert len(plan.decision_ids) == len(plan.cases), "one advisory decision per planned case"
    assert [planned.planned_by for planned in plan.cases] == ["jev"] * len(plan.cases)
    assert all(planned.consistent for planned in plan.cases)
    assert all(planned.oracle_source == "deterministic" for planned in plan.cases)


def test_the_plan_covers_the_whole_catalogue_once():
    """Every declared case is planned exactly once, in the order the advisor reads it.

    The advisor walks the *pairs* of a surface in the order the catalogue declares its intent
    classes, and takes the first case of that pair the ledger has not seen — so a pair with
    several declared cases contributes all of them, next to each other (measured order below;
    the ``regression`` pair of the canary carries four: the positive control and the three
    rendering classes).
    """
    surfaces = A.offered_surfaces(ui_url="http://127.0.0.1:1", api_url="http://127.0.0.1:2")
    plan = A.plan_suite(surfaces=surfaces, slots=4)
    planned = [item.case.case_id for item in plan.cases]
    assert sorted(planned) == sorted(case.case_id for case in A.ACTION_SPACE)
    assert planned == [
        "ui-empty-input",
        "ui-oversized-input",
        "ui-huge-input",
        "ui-double-submit",
        "ui-slow-network",
        "ui-refresh-mid-action",
        "ui-expired-session",
        "ui-history-replay",
        "ui-control-happy-path",
        "ui-broken-layout",
        "ui-missing-element",
        "ui-wrong-text",
        "api-health",
        "api-unknown-route",
        "api-missing-token",
        "api-malformed-body",
        "api-oversized-body",
        "api-concurrent-health",
        "api-openapi-stable",
    ]
    assert plan.stop_reason == "budget_exhausted"


def test_the_deterministic_fallback_plans_the_same_catalogue_and_claims_itself(monkeypatch):
    # ``skaldir.jev`` re-exports the *function* ``decide``, so the module is taken from the
    # import system rather than by a name that the package shadows (measured).
    jev_decide = importlib.import_module("skaldir.jev.decide")

    def refuse(*args, **kwargs):
        raise RuntimeError("no provider in this environment")

    monkeypatch.setattr(jev_decide, "decide", refuse)
    surfaces = A.offered_surfaces(ui_url="http://127.0.0.1:1")
    plan = A.plan_suite(surfaces=surfaces, planner="auto")
    assert plan.planner == "deterministic"
    assert plan.advisor == "none"
    assert [item.case.case_id for item in plan.cases] == [
        case.case_id for case in A.catalogue_for(surfaces)
    ]
    assert any("jev planner unavailable" in note for note in plan.notes), plan.notes
    assert all(item.planned_by == "deterministic" for item in plan.cases)


def test_planning_without_a_surface_refuses_instead_of_pretending():
    with pytest.raises(A.PlannerUnavailable):
        A.plan_suite(surfaces=())


def test_the_open_pairs_are_what_the_question_offers():
    """MVP/87: the model's answer space is the pairs the catalogue still has open.

    Before this, the intent question offered all eight classes while the planner judged the
    pick against its own ledger — measured live: a model answered ``boundary`` twice, the
    ledger had it, and the 12-case plan stopped at 2 with ``pair_already_covered``.
    """
    from skaldir.jev import contracts as JC
    from skaldir.jev import packs as P

    surfaces = A.offered_surfaces(ui_url="http://127.0.0.1:1")
    catalogue = A.catalogue_for(surfaces)
    request = JC.JevAdversarialPlanRequest(
        run_id="narrow",
        goal_id="narrow",
        surfaces=tuple(
            JC.TestSurface(
                surface_id=item.surface_id,
                name=item.surface_id,
                kind=item.kind,
                risk=item.risk_class,
                entry_hint=item.entry_hint,
                seed_operations=tuple(item.seed_operations),
            )
            for item in surfaces
        ),
        budget=JC.AdversarialBudget(max_cases=12),
    )
    # no ledger: every declared pair is offered, exactly as before the field existed
    assert A.open_pairs_for(catalogue, {}) == (
        "canary_ui/boundary",
        "canary_ui/injection",
        "canary_ui/concurrency",
        "canary_ui/dependency_failure",
        "canary_ui/stale_state",
        "canary_ui/permission",
        "canary_ui/persistence",
        "canary_ui/regression",
    )
    # two pairs open: the intent options are exactly those two
    two_open = request.model_copy(
        update={"remaining_pairs": ("canary_ui/boundary", "canary_ui/regression")}
    )
    options = {q.id: tuple(q.options) for q in P.compose_adversarial_plan(two_open).questions}
    assert options["adv.case.intent_class"] == ("boundary", "regression")
    assert "no_surface" in options["adv.surface.pick"]  # a refusal stays expressible
    # one pair open: a choice question needs two options, so the declared vocabulary stands
    last_open = request.model_copy(update={"remaining_pairs": ("canary_ui/boundary",)})
    options = {q.id: tuple(q.options) for q in P.compose_adversarial_plan(last_open).questions}
    assert len(options["adv.case.intent_class"]) >= 2
    assert "boundary" in options["adv.case.intent_class"]


def test_the_live_planner_hands_the_open_pairs_and_a_legible_ledger(monkeypatch):
    """The request carries the two facts the old one was missing (MVP/87)."""
    jev_decide = importlib.import_module("skaldir.jev.decide")
    real_decide = jev_decide.decide
    surfaces = A.offered_surfaces(ui_url="http://127.0.0.1:1")
    seen: list = []

    def watching(request, **kwargs):
        seen.append(request)
        # the declared reader over the ledger *this request* carries — which is the point:
        # the ledger names the declared case ids, so a reader can rebuild covered from it
        advisor = A.DeclaredSpaceAdvisor(
            surfaces=surfaces,
            covered={
                item.case_id: True
                for item in request.previous_results
                if str(item.outcome) != "skipped"
            },
        )
        return real_decide(request, evaluator=advisor)

    monkeypatch.setattr(jev_decide, "decide", watching)
    plan = A.plan_suite(surfaces=surfaces, slots=4, max_wall_s=120, planner="jev")
    assert len(plan.cases) == 12
    assert seen[0].remaining_pairs == A.open_pairs_for(A.catalogue_for(surfaces), {})
    # and the ledger the next ask receives names the *declared* case and its pair, not the
    # model's own counter (`adv-001` three times said nothing about what had been planned)
    first = seen[1].previous_results[0]
    assert first.case_id == plan.cases[0].case.case_id
    assert first.intent_class == plan.cases[0].case.intent_class
    assert first.intent_class, "the pair half of the ledger must be filled"


def test_one_unreadable_answer_is_a_retry_not_the_end_of_the_plan(monkeypatch):
    """A live model's bad sample must not hand `--planner jev` to the declared reader.

    Measured before this bound existed: the façade raised
    ``SystemOneContractError: adv.case.intent_class: choice 'boundary' is not the argmax of
    its own distribution`` on the *first* case, which escaped the planner and produced a
    **deterministic** plan under a command that said ``--planner jev``.
    """
    from types import SimpleNamespace

    from skaldir.system_one import SystemOneContractError

    jev_decide = importlib.import_module("skaldir.jev.decide")
    real_decide = jev_decide.decide
    surfaces = A.offered_surfaces(ui_url="http://127.0.0.1:1")
    monkeypatch.setattr(
        A,
        "_planning_provider",
        lambda runtime: (
            SimpleNamespace(enabled=True, provider="llm_system_one_adapter"),
            object(),
            "",
            "llm_system_one_adapter",
        ),
    )
    calls = {"n": 0}

    def flaky(request, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise SystemOneContractError("adv.case.intent_class: not the argmax")
        advisor = A.DeclaredSpaceAdvisor(
            surfaces=surfaces,
            covered={
                item.case_id: True
                for item in request.previous_results
                if str(item.outcome) != "skipped"
            },
        )
        return real_decide(request, evaluator=advisor)

    monkeypatch.setattr(jev_decide, "decide", flaky)
    plan = A.plan_suite(surfaces=surfaces, slots=4, max_wall_s=120, live=True)
    assert plan.planner == "jev", "the planner was not handed to the deterministic reader"
    assert plan.answer_source == "llm_system_one_adapter"
    assert len(plan.cases) == 12
    assert plan.stop_reason == "budget_exhausted"
    assert calls["n"] == 13, "the unreadable sample cost exactly one extra ask"
    unreadable = [item for item in plan.decisions if item.get("status") == "error"]
    assert len(unreadable) == 1 and unreadable[0]["ask"] == 1


def test_a_model_that_keeps_repeating_a_pair_stops_with_a_named_reason(monkeypatch):
    """Bounded: three identical picks name the outcome instead of crashing the plan."""
    from types import SimpleNamespace

    jev_decide = importlib.import_module("skaldir.jev.decide")
    real_decide = jev_decide.decide
    surfaces = A.offered_surfaces(ui_url="http://127.0.0.1:1")
    advisor = A.DeclaredSpaceAdvisor(surfaces=surfaces)
    monkeypatch.setattr(
        A,
        "_planning_provider",
        lambda runtime: (
            SimpleNamespace(enabled=True, provider="llm_system_one_adapter"),
            object(),
            "",
            "llm_system_one_adapter",
        ),
    )

    def repeating(request, **kwargs):
        decision = real_decide(request, evaluator=advisor)
        answer = decision.answer
        if not answer.plan:
            return decision
        forced = answer.model_copy(
            update={
                "plan": tuple(
                    item.model_copy(
                        update={"surface_id": "canary_ui", "intent_class": "boundary"}
                    )
                    for item in answer.plan
                )
            }
        )
        return decision.model_copy(update={"answer": forced})

    monkeypatch.setattr(jev_decide, "decide", repeating)
    plan = A.plan_suite(surfaces=surfaces, slots=4, max_wall_s=120, live=True)
    assert plan.planner == "jev"
    # both declared `boundary` cases are planned; the third repeat is refused, three times
    assert [item.case.case_id for item in plan.cases] == [
        "ui-empty-input",
        "ui-oversized-input",
    ]
    assert plan.stop_reason == "pair_already_covered:canary_ui/boundary"
    repeats = [item for item in plan.decisions if item.get("named") == plan.stop_reason]
    assert [item["ask"] for item in repeats] == [1, 2, 3]


def test_a_recovered_refusal_does_not_leave_a_stale_stop_reason(monkeypatch):
    """A refusal that the retry recovers must not label a plan that then finished.

    The stop reason is written once per case — on the last ask — so a `case_already_covered`
    sample that the next ask turned into a planned case cannot survive into the final report.
    """
    from types import SimpleNamespace

    from skaldir.jev import contracts as JC

    jev_decide = importlib.import_module("skaldir.jev.decide")
    real_decide = jev_decide.decide
    surfaces = A.offered_surfaces(ui_url="http://127.0.0.1:1")
    monkeypatch.setattr(
        A,
        "_planning_provider",
        lambda runtime: (
            SimpleNamespace(enabled=True, provider="llm_system_one_adapter"),
            object(),
            "",
            "llm_system_one_adapter",
        ),
    )
    calls = {"n": 0}

    def refusing_once(request, **kwargs):
        calls["n"] += 1
        decision = real_decide(
            request,
            evaluator=A.DeclaredSpaceAdvisor(
                surfaces=surfaces,
                covered={
                    item.case_id: True
                    for item in request.previous_results
                    if str(item.outcome) != "skipped"
                },
            ),
        )
        if calls["n"] == 1:
            return decision.model_copy(
                update={
                    "answer": JC.AdversarialPlanAnswer(
                        chosen="refuse", stop_reason="case_already_covered"
                    )
                }
            )
        return decision

    monkeypatch.setattr(jev_decide, "decide", refusing_once)
    plan = A.plan_suite(surfaces=surfaces, slots=4, max_wall_s=120, live=True)
    assert len(plan.cases) == 12, "the recovered case was planned"
    assert plan.stop_reason == "budget_exhausted", "no stale refusal label"
    refused = [item for item in plan.decisions if item.get("case_id") == "case_already_covered"]
    assert len(refused) == 1 and refused[0]["ask"] == 1


def test_a_case_that_cannot_be_read_after_three_asks_names_the_cause(monkeypatch):
    """The stop label carries *what* could not be read, not only the exception class.

    Measured live (a bimodal model distribution answered `score: 1.5` with the legend
    `[0.0, 1.0, 2.0, 3.0]` three times in a row), the label used to be the bare
    `decision_error:SystemOneContractError` — which is also what the *pre-fix escape* looked
    like, so a reader could not tell a named stop from a crash.
    """
    from types import SimpleNamespace

    from skaldir.system_one import SystemOneContractError

    jev_decide = importlib.import_module("skaldir.jev.decide")
    real_decide = jev_decide.decide
    surfaces = A.offered_surfaces(ui_url="http://127.0.0.1:1")
    monkeypatch.setattr(
        A,
        "_planning_provider",
        lambda runtime: (
            SimpleNamespace(enabled=True, provider="llm_system_one_adapter"),
            object(),
            "",
            "llm_system_one_adapter",
        ),
    )
    cause = "adv.case.priority: score 1.5 is not in the question's legend [0.0, 1.0, 2.0, 3.0]"

    def unreadable_after_the_first(request, **kwargs):
        if request.previous_results:
            raise SystemOneContractError(cause)
        return real_decide(
            request,
            evaluator=A.DeclaredSpaceAdvisor(
                surfaces=surfaces,
                covered={
                    item.case_id: True
                    for item in request.previous_results
                    if str(item.outcome) != "skipped"
                },
            ),
        )

    monkeypatch.setattr(jev_decide, "decide", unreadable_after_the_first)
    plan = A.plan_suite(surfaces=surfaces, slots=4, max_wall_s=120, live=True)
    assert plan.planner == "jev"
    assert len(plan.cases) == 1, "the first case was planned"
    assert plan.stop_reason.startswith("decision_error:SystemOneContractError:")
    assert "not in the question's legend" in plan.stop_reason
    unreadable = [item for item in plan.decisions if item.get("status") == "error"]
    assert [item["ask"] for item in unreadable] == [1, 2, 3]
    assert all(cause in item["error"] for item in unreadable)


def test_the_planner_never_claims_a_case_the_catalogue_does_not_declare(monkeypatch):
    """A plan that would assign a foreign operation is not silently executed."""
    from skaldir.jev import contracts as JC

    jev_decide = importlib.import_module("skaldir.jev.decide")
    real_decide = jev_decide.decide

    def lying_decide(request, *, evaluator=None, **kwargs):
        decision = real_decide(request, evaluator=evaluator, **kwargs)
        answer = decision.answer
        broken = JC.AdversarialPlanAnswer(
            plan=tuple(
                JC.TestCasePlan(
                    case_id=item.case_id,
                    surface_id=item.surface_id,
                    intent=item.intent,
                    intent_class=item.intent_class,
                    operation="permission_probe",
                    target_ref=item.target_ref,
                    success_oracle=JC.Oracle(kind="denied", expect="denied"),
                    oracle_source="deterministic",
                )
                for item in answer.plan
            ),
            chosen=answer.chosen,
            stop_reason=answer.stop_reason,
            batch_size=answer.batch_size,
        )
        return decision.model_copy(update={"answer": broken})

    monkeypatch.setattr(jev_decide, "decide", lying_decide)
    surfaces = A.offered_surfaces(ui_url="http://127.0.0.1:1")
    plan = A.plan_suite(surfaces=surfaces, planner="jev")
    assert plan.planner == "jev"
    assert plan.cases, "the lying plan still produced cases"
    assert all(not item.consistent for item in plan.cases)
    assert "declared action space" in plan.cases[0].deviation

    # and the runner turns that deviation into UNKNOWN, not into a pass
    surface = plan.surfaces[0]
    result = A.run_case(
        plan.cases[0], surface=surface, timeout_s=5, slots=1, pool=None
    )
    assert result.outcome == "unknown"
    assert "plan deviation" in result.reason


# --------------------------------------------------------------------------- #
# the verdict and the release gate                                              #
# --------------------------------------------------------------------------- #


def _result(outcome: str, case_id: str = "case-1") -> A.CaseResult:
    return A.CaseResult(
        case_id=case_id,
        surface_id=A.CANARY_SURFACE_ID,
        intent_class="boundary",
        defect_class="nothing",
        operation="click",
        oracle="entry_rejected",
        oracle_kind="observed_state",
        target_ref="canary_ui##form",
        outcome=outcome,
        reason=f"measured {outcome}",
        observed_by="dom+server_state",
    )


def test_the_verdict_rule_is_fail_then_unknown_then_pass():
    assert A.verdict_for([]) == "UNKNOWN"
    assert A.verdict_for([_result("pass")]) == "PASS"
    assert A.verdict_for([_result("pass"), _result("unknown", "case-2")]) == "UNKNOWN"
    assert (
        A.verdict_for([_result("pass"), _result("fail", "case-2"), _result("unknown", "c")])
        == "FAIL"
    )


def _report(verdict: str) -> A.SuiteReport:
    outcomes = {
        "PASS": ["pass"],
        "FAIL": ["fail"],
        "UNKNOWN": ["unknown"],
    }[verdict]
    results = tuple(_result(outcome, f"case-{index}") for index, outcome in enumerate(outcomes))
    return A.SuiteReport(
        suite_version=A.SUITE_VERSION,
        target={"surfaces": {}},
        plan={"planner": "jev", "case_count": len(results)},
        verdict=verdict,
        results=results,
        started_at="2026-09-18T00:00:00Z",
        finished_at="2026-09-18T00:00:01Z",
        duration_ms=1000,
        run_id="run-1",
    )


def test_only_a_pass_exits_zero():
    assert A.exit_code_for(_report("PASS"), block_release=True) == 0
    assert A.exit_code_for(_report("PASS"), block_release=False) == 0
    assert A.exit_code_for(_report("FAIL"), block_release=True) == 2
    assert A.exit_code_for(_report("FAIL"), block_release=False) == 2
    assert A.exit_code_for(_report("UNKNOWN"), block_release=True) == 3
    assert A.exit_code_for(_report("UNKNOWN"), block_release=False) == 3


def test_an_unknown_suite_may_only_be_waived_outside_the_release_gate():
    waived = A.exit_code_for(_report("UNKNOWN"), block_release=False, allow_unknown=True)
    assert waived == 0
    # the gate ignores the waiver: a release is never certified on an unverifiable suite
    assert A.exit_code_for(_report("UNKNOWN"), block_release=True, allow_unknown=True) == 3
    gate = A.release_gate_payload(_report("UNKNOWN"), block_release=True, allow_unknown=True)
    assert gate["waived_unknown"] is False
    assert gate["blocks_release"] is True
    assert gate["exit_code"] == 3
    report_mode = A.release_gate_payload(
        _report("UNKNOWN"), block_release=False, allow_unknown=True
    )
    assert report_mode["waived_unknown"] is True
    assert report_mode["exit_code"] == 0


def test_the_gate_lists_the_defects_it_blocks_on():
    report = _report("FAIL")
    gate = A.release_gate_payload(report, block_release=True)
    assert gate["verdict"] == "FAIL"
    assert gate["blocks_release"] is True
    assert gate["failed_cases"] == ["case-0"]
    assert gate["defect_count"] == 0  # this synthetic case carries no defect record


# --------------------------------------------------------------------------- #
# unverifiable is never a pass                                                  #
# --------------------------------------------------------------------------- #


def test_a_case_whose_oracle_cannot_be_evaluated_is_unknown(monkeypatch):
    surfaces = A.offered_surfaces(ui_url="http://127.0.0.1:1")
    plan = A.plan_suite(surfaces=surfaces, planner="deterministic")
    planned = plan.cases[0]

    def cannot_judge(case, observed):
        raise A.Unverifiable("the state endpoint could not be read")

    monkeypatch.setitem(A.ORACLES, planned.case.oracle, cannot_judge)
    monkeypatch.setitem(A.DRIVERS, planned.case.driver, lambda ctx, case: {"driver": "stub"})
    result = A.run_case(planned, surface=plan.surfaces[0], timeout_s=5, slots=1, pool=None)
    assert result.outcome == "unknown"
    assert result.reason.startswith("unverifiable:")
    assert result.verified is False
    assert A.verdict_for([result]) == "UNKNOWN"


def test_a_case_without_a_deterministic_oracle_is_unknown_without_running():
    planned = A.PlannedCase(
        case=A.CASE_BY_ID["ui-empty-input"],
        operation="click",
        oracle_kind="observed_state",
        target_ref="canary_ui##form",
        oracle_source="computer_use",
    )
    surface = A.offered_surfaces(ui_url="http://127.0.0.1:1")[0]
    result = A.run_case(planned, surface=surface, timeout_s=5, slots=1, pool=None)
    assert result.outcome == "unknown"
    assert "deterministic oracle" in result.reason


def test_a_case_the_action_space_does_not_declare_is_unknown():
    planned = A.PlannedCase(
        case=A.CASE_BY_ID["ui-empty-input"],
        operation="click",
        oracle_kind="observed_state",
        target_ref="canary_ui##form",
        deviation="the plan assigned probe/denied",
        consistent=False,
    )
    surface = A.offered_surfaces(ui_url="http://127.0.0.1:1")[0]
    result = A.run_case(planned, surface=surface, timeout_s=5, slots=1, pool=None)
    assert result.outcome == "unknown"
    assert result.reason.startswith("plan deviation:")


def test_an_unreachable_target_yields_unknown_cases_and_an_unknown_verdict():
    """Nothing to reach is not 'no defects': every case stays unverified."""
    surfaces = A.offered_surfaces(ui_url="http://127.0.0.1:1")
    plan = A.plan_suite(surfaces=surfaces, planner="deterministic")
    report = A.run_suite(plan, slots=2)
    assert report.verdict == "UNKNOWN"
    assert report.counts["pass"] == 0
    assert all(result.outcome == "unknown" for result in report.results)
    assert report.blocks_release is True


# --------------------------------------------------------------------------- #
# the report: evidence, hashes, trimming                                        #
# --------------------------------------------------------------------------- #


def test_the_report_carries_a_recomputable_evidence_hash():
    report = _report("PASS")
    payload = report.as_dict()
    again = A._sha256_json(  # noqa: SLF001 — the same helper the report hashes with
        [
            {
                "case_id": item["case_id"],
                "outcome": item["outcome"],
                "defects": item["defects"],
                "observed": item["observed"],
            }
            for item in payload["cases"]
        ]
    )
    assert payload["evidence_sha256"] == again == report.evidence_sha256()
    for case in payload["cases"]:
        assert case["observed_sha256"] == A._sha256_json(case["observed"])  # noqa: SLF001


def test_a_long_stored_value_is_trimmed_in_the_report_and_the_hash_matches_what_is_shown():
    result = _result("fail")
    result.observed = {"server": {"records": [{"note": "x" * 5_000}]}}
    payload = result.as_dict()
    note = payload["observed"]["server"]["records"][0]["note"]
    assert note.startswith("xxxx") and "chars]" in note
    assert len(note) < 300
    assert payload["observed_sha256"] == A._sha256_json(payload["observed"])  # noqa: SLF001


def test_the_run_record_is_written_next_to_the_state_dir(tmp_path):
    report = _report("FAIL")
    path = A.record_run(report, tmp_path)
    assert path.parent == tmp_path
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["verdict"] == "FAIL"
    assert written["run_id"] == report.run_id


# --------------------------------------------------------------------------- #
# the canary target itself                                                      #
# --------------------------------------------------------------------------- #


@pytest.fixture
def canaries():
    apps = {"broken": A.start_canary("broken"), "fixed": A.start_canary("fixed")}
    try:
        yield apps
    finally:
        for app in apps.values():
            app.stop()


def test_the_canary_refuses_the_inputs_the_broken_mode_accepts(canaries):
    broken, fixed = canaries["broken"], canaries["fixed"]
    for case_id_expected, payload in (
        (A.DEFECT_EMPTY_INPUT, {"name": "", "note": "no name"}),
        (A.DEFECT_UNBOUNDED_INPUT, {"name": "big", "note": "x" * 100_000}),
    ):
        with httpx.Client(base_url=broken.url, timeout=20) as client:
            response = client.post("/submit", json=payload)
            assert response.status_code == 200, case_id_expected
            assert client.get("/state.json").json()["record_count"] == 1
        with httpx.Client(base_url=fixed.url, timeout=20) as client:
            response = client.post("/submit", json=payload)
            assert response.status_code == 422, case_id_expected
            assert client.get("/state.json").json()["record_count"] == 0


def test_the_canary_escapes_a_stored_value_only_in_the_fixed_mode(canaries):
    payload = '<img id="canary-xss" src="/nope.png">'
    assert 'id="canary-xss"' not in httpx.get(canaries["broken"].url + "/", timeout=10).text
    for mode, app in (("broken", canaries["broken"]), ("fixed", canaries["fixed"])):
        with httpx.Client(base_url=app.url, timeout=10) as client:
            client.post("/submit", json={"name": "n", "note": payload})
            page = client.get("/").text
        if mode == "broken":
            assert '<img id="canary-xss"' in page, "the broken mode renders raw markup"
        else:
            assert '<img id="canary-xss"' not in page
            assert "&lt;img" in page, "the fixed mode renders the value as text"


def test_the_canary_honours_an_idempotency_key_only_in_the_fixed_mode(canaries):
    for mode, app in (("broken", canaries["broken"]), ("fixed", canaries["fixed"])):
        key = f"key-{mode}"
        with httpx.Client(base_url=app.url, timeout=10) as client:
            for _ in range(2):
                client.post(
                    "/submit",
                    json={"name": "once", "note": "same intention"},
                    headers={"X-Idempotency-Key": key},
                )
            count = client.get("/state.json").json()["record_count"]
        assert count == (2 if mode == "broken" else 1), mode


def test_the_canary_redirects_after_a_native_post_only_in_the_fixed_mode(canaries):
    body = {"name": "form", "note": "native"}
    for mode, app in (("broken", canaries["broken"]), ("fixed", canaries["fixed"])):
        with httpx.Client(base_url=app.url, timeout=10, follow_redirects=False) as client:
            client.get("/")
            response = client.post("/submit", data=body)
        assert response.status_code == (200 if mode == "broken" else 303), mode
        if mode == "fixed":
            assert response.headers["location"] == "/?ok=1"


def test_the_canary_refuses_a_write_after_the_session_expired(canaries):
    for app in canaries.values():
        with httpx.Client(base_url=app.url, timeout=10) as client:
            client.get("/")  # establishes the session cookie
            assert client.get("/expire").json()["expired"] is True
            response = client.post("/submit", json={"name": "late", "note": "expired"})
            assert response.status_code == 401
            assert response.json()["error"] == "session expired"
            assert client.get("/state.json").json()["record_count"] == 0


def test_the_canary_slow_response_is_the_servers_own(canaries):
    """A declared slow answer comes from the target, not from a held request in the driver."""
    with httpx.Client(base_url=canaries["broken"].url, timeout=20) as client:
        client.get("/")
        started = time.time()
        response = client.post("/submit?slow=900", json={"name": "slow", "note": "x"})
        elapsed = time.time() - started
    assert response.status_code == 200
    assert elapsed >= 0.85, f"the declared delay did not happen ({elapsed:.2f}s)"


def test_an_unknown_canary_mode_is_refused():
    with pytest.raises(ValueError):
        A.CanaryApp("nearly-fixed")


def test_the_state_snapshot_is_per_session(canaries):
    app = canaries["broken"]
    with httpx.Client(base_url=app.url, timeout=10) as first, httpx.Client(
        base_url=app.url, timeout=10
    ) as second:
        first.get("/")
        second.get("/")
        first.post("/submit", json={"name": "mine", "note": "one"})
        assert first.get("/state.json").json()["record_count"] == 1
        assert second.get("/state.json").json()["record_count"] == 0, (
            "two sessions must not share entries: the cases rely on that isolation"
        )
