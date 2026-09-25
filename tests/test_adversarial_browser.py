"""The browser half of the adversarial suite, proved against the canary target for real.

What this file pins:

* the suite **catches** every defect class the canary's broken mode has — with the defect class
  named per case, not just a red verdict — and the positive control still passes;
* the suite **passes** every case on the fixed canary, so a red means a defect and not a habit;
* the cases really are parallel and really are isolated: each one runs in its own browser
  context, so no case sees another's session, entries or history;
* every judged case was observed deterministically (``dom+server_state`` / ``http_status``) and
  carries a recomputable observation hash — a case that could not be observed is ``unknown`` and
  never counted as verified;
* **a run leaves no browser and no driver process behind.** The suite is the only thing in this
  repository that starts Chromium, so the processes it starts have to be gone when it returns —
  measured 2026-09-20: one `run_suite(slots=5)` used to leave 5 ``playwright/driver/node``
  run-driver processes and 25 Chromium processes alive inside the *pytest* process, and the
  browser file runs the suite a dozen times, which is what accumulated while a full-suite run
  sat at 4% for sixteen minutes.

No network beyond loopback, no owner environment: the canary app is started by the suite itself.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from skaldir import adversarial as A

pytest.importorskip("playwright.sync_api", reason="the browser cases need Playwright")

#: how long a browser process may take to disappear after the run that owned it returned. The
#: driver stops synchronously, so this is slack for the kernel, not a wait for a shutdown.
PROCESS_SETTLE_S = 15.0

#: the two process shapes this suite spawns: the Playwright run-driver and the Chromium shell.
#: Nothing else in the repository starts either one, so the presence of one after a run is the
#: leak itself and not an inventory question.
DRIVER_MARKER = "/playwright/driver/node"
CHROMIUM_MARKER = "ms-playwright/chromium_headless_shell"


def _browser_processes() -> dict[str, str]:
    """``pid -> command`` for every Playwright driver / Chromium shell on this box.

    Two exclusions, both deliberate: the Hermes browser (``playwright-mcp``, driven over CDP by
    the agent harness) and anything without a readable ``/proc`` entry. This test may only
    report on processes the suite itself could have started.
    """
    found: dict[str, str] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().decode("utf-8", "replace").replace("\0", " ")
        except OSError:
            continue
        if "playwright-mcp" in cmd or "--cdp-endpoint" in cmd:
            continue
        if DRIVER_MARKER in cmd or CHROMIUM_MARKER in cmd:
            found[entry.name] = cmd.strip()
    return found


def _await_processes_settle(before: dict[str, str]) -> dict[str, str]:
    """The leftovers of this run, allowing a bounded settle for the kernel to reap them."""
    deadline = time.monotonic() + PROCESS_SETTLE_S
    left = {pid: cmd for pid, cmd in _browser_processes().items() if pid not in before}
    while left and time.monotonic() < deadline:
        time.sleep(0.25)
        left = {pid: cmd for pid, cmd in _browser_processes().items() if pid not in before}
    return left


@pytest.fixture(scope="module")
def broken():
    app = A.start_canary("broken")
    try:
        yield app
    finally:
        app.stop()


@pytest.fixture(scope="module")
def fixed():
    app = A.start_canary("fixed")
    try:
        yield app
    finally:
        app.stop()


def _run(app: A.CanaryApp, *, slots: int = 5) -> A.SuiteReport:
    # generous per-case timeouts on purpose: the cases include declared multi-second delays, and
    # a heavily loaded machine must not turn a detector into an UNKNOWN (which would weaken the
    # proof, not the target)
    plan = A.plan_suite(
        surfaces=A.offered_surfaces(ui_url=app.url),
        slots=slots,
        per_case_timeout_s=60,
        max_wall_s=300,
    )
    assert plan.planner in ("jev", "deterministic")
    return A.run_suite(plan, slots=slots)


def test_the_broken_canary_is_caught_by_every_declared_detector(broken):
    report = _run(broken)
    outcomes = {result.case_id: result.outcome for result in report.results}
    assert report.verdict == "FAIL", report.as_dict()["counts"]
    # every case the broken mode is supposed to violate is a measured failure, with its class
    detected = {
        result.case_id: result.defect_class
        for result in report.results
        if result.outcome == "fail"
    }
    assert detected == A.CANARY_EXPECTED, (
        "the detectors and the target disagree: "
        f"missed {sorted(set(A.CANARY_EXPECTED) - set(detected))}, "
        f"extra {sorted(set(detected) - set(A.CANARY_EXPECTED))}"
    )
    # the positive control must still pass: the suite is not simply red
    assert outcomes["ui-control-happy-path"] == "pass"
    # and nothing was excused as unknown
    assert report.counts["unknown"] == 0, [
        (r.case_id, r.reason) for r in report.results if r.outcome == "unknown"
    ]
    assert sorted({item["defect_class"] for item in report.defects}) == sorted(
        set(A.CANARY_EXPECTED.values())
    )


def test_the_fixed_canary_passes_every_case(fixed):
    report = _run(fixed)
    assert report.verdict == "PASS", [
        (r.case_id, r.outcome, r.reason) for r in report.results if r.outcome != "pass"
    ]
    assert report.counts == {"pass": len(report.results), "fail": 0, "unknown": 0}
    assert report.defects == ()
    assert report.blocks_release is False


def test_every_judged_case_was_observed_deterministically(broken):
    report = _run(broken)
    for result in report.results:
        assert result.observed_by in ("dom+server_state", "http_status", "http_status+digest")
        assert result.observed_by != "none"
        assert result.observed, f"{result.case_id}: a verdict without an observation"
        payload = result.as_dict()
        assert payload["observed_sha256"] == A._sha256_json(payload["observed"]), (  # noqa: SLF001
            f"{result.case_id}: the reported hash does not match the reported observation"
        )


def test_each_case_runs_in_its_own_context(fixed):
    """Isolation, measured: nine cases write in parallel and none sees another's entries."""
    report = _run(fixed, slots=5)
    counts = []
    for result in report.results:
        server = result.observed.get("server")
        counts.append(int(server["record_count"]))
    # every case's session holds exactly its own write(s) — never the other cases' writes
    assert max(counts) <= 1, counts
    control = next(r for r in report.results if r.case_id == "ui-control-happy-path")
    assert control.observed["server"]["record_count"] == 1
    assert control.observed["server"]["records"][0]["name"] == "control"


def test_the_cases_really_run_in_parallel(fixed):
    report = _run(fixed, slots=5)
    case_ms = sum(result.duration_ms for result in report.results)
    assert case_ms > report.duration_ms * 1.4, (
        f"the cases did not overlap: {case_ms}ms of case work in {report.duration_ms}ms of wall "
        "time"
    )


def test_the_suite_bites_on_the_broken_target_and_clears_the_fixed_one():
    """The suite's own acceptance test — the same check ``adversarial selftest`` runs."""
    check = A.selftest(slots=5, timeout_s=60)
    assert check.ok, check.detail
    assert check.broken_verdict == "FAIL"
    assert check.fixed_verdict == "PASS"
    assert check.missed == ()
    assert check.false_positives == ()
    assert len(check.detected) == len(A.CANARY_EXPECTED)


def test_a_run_of_the_fixed_target_is_repeatable(fixed):
    """A verdict that moves between two identical runs cannot block a release."""
    first, second = _run(fixed, slots=4), _run(fixed, slots=4)
    assert first.verdict == second.verdict == "PASS"
    assert [r.outcome for r in first.results] == [r.outcome for r in second.results]
    assert first.counts == second.counts


# --------------------------------------------------------------------------- #
# the five browser defect classes the owner named, and who decided each one      #
# --------------------------------------------------------------------------- #

#: the classes the owner named → the declared case that really covers it
NAMED_BROWSER_CLASSES: dict[str, str] = {
    "broken layout": "ui-broken-layout",
    "missing element": "ui-missing-element",
    "wrong text": "ui-wrong-text",
    "slow response": "ui-slow-network",
    "refusal": "ui-expired-session",
}


def _by_case(report: A.SuiteReport) -> dict[str, A.CaseResult]:
    return {result.case_id: result for result in report.results}


def test_every_named_browser_class_is_really_covered_by_a_declared_case():
    ids = {case.case_id for case in A.ACTION_SPACE}
    missing = {label: case_id for label, case_id in NAMED_BROWSER_CLASSES.items()
               if case_id not in ids}
    assert missing == {}, f"the action space declares no case for {missing}"


def test_each_named_class_fires_on_the_broken_target_and_passes_on_the_fixed_one(broken, fixed):
    from_broken = _by_case(_run(broken))
    from_fixed = _by_case(_run(fixed))
    for label, case_id in NAMED_BROWSER_CLASSES.items():
        faulted = from_broken[case_id]
        assert faulted.outcome == "fail", f"{label}: the broken target was not caught"
        assert faulted.defects, f"{label}: a failure with no defect behind it"
        assert faulted.defect_class == A.CANARY_EXPECTED[case_id]
        assert from_fixed[case_id].outcome == "pass", (
            f"{label}: the case also fails on the fixed target, so it measures a habit and "
            "not a defect"
        )


def test_the_rendering_classes_read_the_target_instead_of_guessing(broken):
    report = _run(broken)
    for case_id in ("ui-broken-layout", "ui-missing-element", "ui-wrong-text"):
        result = _by_case(report)[case_id]
        assert result.observed_by == "dom+server_state"
        assert result.observed, f"{case_id}: a verdict without an observation"
        assert result.defect_class in result.observed.get("layout", {}) or result.observed


def test_every_planned_case_carries_the_jev_decision_that_chose_it(broken):
    """Who decided: one decision record per case, with the decision id, the reason and the time."""
    plan = A.plan_suite(
        surfaces=A.offered_surfaces(ui_url=broken.url), slots=5, per_case_timeout_s=60,
        max_wall_s=300,
    )
    assert plan.planner == "jev", "the façade did not plan; the fallback did"
    assert len(plan.decisions) == len(plan.cases)
    for planned in plan.cases:
        record = planned.decision
        assert planned.planned_by == "jev"
        assert record, f"{planned.case.case_id}: no decision record"
        assert record["decision_id"] == planned.decision_id
        assert record["decision_id"].startswith("jev-adversarial_test_planning-")
        assert record["kind"] == "adversarial_test_planning"
        assert record["case_id"] == planned.case.case_id
        assert record["reason"], "a decision without a reason is not a decision record"
        assert planned.case.case_id in record["reason"]
        assert record["status"] == "ok"
        assert isinstance(record["call_wall_us"], int)
        assert record["call_wall_us"] > 0, "the call was not timed"
        assert record["planned_at"].endswith("Z")
        # advisory by construction: Jev decides the plan, the oracle decides the verdict
        assert record["authority"] == "advisory" and record["certifies"] is False


def test_the_run_record_carries_the_decision_and_the_reason_per_case(broken, tmp_path):
    plan = A.plan_suite(
        surfaces=A.offered_surfaces(ui_url=broken.url), slots=5, per_case_timeout_s=60,
        max_wall_s=300,
    )
    report = A.run_suite(plan, slots=5)
    path = A.record_run(report, tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["plan"]["planner"] == "jev"
    assert written["plan"]["decision_count"] == len(written["plan"]["decisions"])
    assert written["plan"]["live_jev"] is False
    assert written["plan"]["provider_modes"] == ["replay_fixture"], (
        "the offline façade must label itself, so a reader can tell it from a live call"
    )
    for case in written["cases"]:
        decision = case["decision"]
        assert decision["decision_id"], case["case_id"]
        assert decision["reason"], case["case_id"]
        assert decision["call_wall_us"] > 0, case["case_id"]
        assert decision["live_jev"] is False
        assert decision["provider_mode"] == "replay_fixture"
        assert decision["latency_ms"] == 0, (
            "latency_ms is the façade's own number; 0 here means 'the façade did not time it', "
            "and the verifier's call_wall_us is the measurement that is always real"
        )
    ledger = A.decision_ledger(report.run_id, tmp_path)
    assert len(ledger) == len(written["plan"]["decisions"])
    assert [row["decision_id"] for row in ledger] == [
        row["decision_id"] for row in written["plan"]["decisions"]
    ]
    assert all(row["run_id"] == report.run_id for row in ledger)


def test_the_fallback_planner_is_distinguishable_from_the_jev_path(broken, fixed):
    """The two decision paths must not look alike in the record."""
    jev_plan = A.plan_suite(surfaces=A.offered_surfaces(ui_url=fixed.url), slots=4,
                            per_case_timeout_s=60, max_wall_s=300)
    plain_plan = A.plan_suite(surfaces=A.offered_surfaces(ui_url=fixed.url), slots=4,
                              per_case_timeout_s=60, max_wall_s=300, planner="deterministic")
    assert jev_plan.planner == "jev" and jev_plan.decisions
    assert jev_plan.live_jev is False
    assert plain_plan.planner == "deterministic"
    assert plain_plan.decisions == () and plain_plan.live_jev is False
    assert all(planned.planned_by == "jev" for planned in jev_plan.cases)
    assert all(planned.planned_by == "deterministic" for planned in plain_plan.cases)
    assert all(planned.decision == {} for planned in plain_plan.cases), (
        "the fallback planned no case through Jev, so it must carry no decision record"
    )
    # the same declared coverage, in whatever order each planner chose: the Jev planner picks
    # the pair per call, so its order is a decision, not a transcription of the catalogue
    assert sorted(item.case.case_id for item in jev_plan.cases) == sorted(
        item.case.case_id for item in plain_plan.cases
    )
    assert [item.case.case_id for item in jev_plan.cases] != [
        item.case.case_id for item in plain_plan.cases
    ], "the two planners agreed on the order as well — the plan is indistinguishable"


def test_a_deterministic_run_writes_an_empty_decision_ledger(fixed, tmp_path):
    plan = A.plan_suite(surfaces=A.offered_surfaces(ui_url=fixed.url), slots=4,
                        per_case_timeout_s=60, max_wall_s=300, planner="deterministic")
    report = A.run_suite(plan, slots=4)
    path = A.record_run(report, tmp_path)
    assert path.is_file()
    assert A.decision_ledger(report.run_id, tmp_path) == []
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["plan"]["decision_count"] == 0
    assert all(case["decision"] == {} for case in written["cases"])


# --------------------------------------------------------------------------- #
# the lifecycle: a run may not leave a browser or a driver process behind       #
# --------------------------------------------------------------------------- #


def test_the_pool_records_a_close_it_cannot_honour():
    """The guard, proved without launching a browser.

    Playwright refuses the cross-thread call, so a pool closed from a thread other than the one
    that used it *cannot* clean up — and the one thing it must never do is report success
    anyway. That silence is what let the leak live: ``close()`` swallowed
    ``Error: cannot switch to a different thread (which happens to have exited)`` and the run
    carried on. ``_owner`` is set directly here because the subject is the close path, not the
    launch path.
    """
    pool = A.BrowserPool()
    assert pool.owner_thread is None
    assert pool.close_errors == []
    pool._owner = threading.get_ident()  # noqa: SLF001 — "this pool was used on this thread"
    errors: list[str] = []

    def closer() -> None:
        pool.close()
        errors.extend(pool.close_errors)

    thread = threading.Thread(target=closer, name="wrong-thread-closer")
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive(), "the closing thread never finished"
    assert any("ran on thread" in item for item in errors), (
        f"a cross-thread close was swallowed instead of recorded: {errors}"
    )


def test_a_pool_closes_what_it_opened_on_its_own_thread():
    """The real lifecycle, on the thread that owns it: nothing is left running.

    This is the shape `_run_ui_case` uses — create the pool, drive, close — on one thread. It is
    also the counter-proof of the test above: the same pool, closed where it was used, records
    no error and leaves no driver and no Chromium behind.
    """
    before = _browser_processes()
    pool = A.BrowserPool()
    outcome: dict[str, object] = {}

    def work() -> None:
        context = pool.context(timeout_s=15)
        page = context.new_page()
        page.goto("about:blank", wait_until="load")
        context.close()
        pool.close()
        outcome["errors"] = list(pool.close_errors)
        outcome["owner"] = pool.owner_thread

    thread = threading.Thread(target=work, name="pool-owner")
    thread.start()
    thread.join(timeout=120)
    assert not thread.is_alive(), "the pool's own thread never finished"
    assert outcome.get("errors") == [], outcome
    assert outcome.get("owner") == thread.ident, (
        "the pool must name the thread that drove it, not the thread that only closed it"
    )
    leftovers = _await_processes_settle(before)
    assert leftovers == {}, (
        f"a pool closed on its own thread left {len(leftovers)} process(es) behind: {leftovers}"
    )


def test_a_run_suite_leaves_no_browser_or_driver_process_behind(fixed):
    """The regression: one finished run, nothing of it still running.

    Measured before the fix (the pool was built once and closed after the executor shut down,
    i.e. after every worker had exited): 5 ``playwright/driver/node`` processes and 25 Chromium
    processes stayed alive for the rest of the pytest process — per run, and this file runs the
    suite a dozen times. The stall this file was investigated for sat at 4% of a full-suite run
    for sixteen minutes with those processes accumulated behind it.
    """
    before = _browser_processes()
    report = _run(fixed)
    assert report.verdict == "PASS", [
        (result.case_id, result.outcome, result.reason)
        for result in report.results
        if result.outcome != "pass"
    ]
    leftovers = _await_processes_settle(before)
    assert leftovers == {}, (
        f"the run leaked {len(leftovers)} browser process(es): "
        + "; ".join(f"{pid}: {cmd}" for pid, cmd in sorted(leftovers.items()))
    )

